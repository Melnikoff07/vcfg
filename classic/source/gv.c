// gv.c - game motion vectors for mode 8 (see gv.h, docs/VELOCITY_RU.md).
// Threads: the game's submit thread calls gv_rewrite / gv_emit_* / gv_fill_table / gv_frame_end (no file I/O, no
// waiting, bounded work); the plugin's IO thread calls gv_io_tick (calibration, profile, logs, dumps).
// The IO thread hands decisions to the submit thread through a double-buffered config (cfg_publish / cfg_read).
#include <Common.h>
#include <stdio.h>
#include <string.h>
#include "gv.h"
#include "pm4scan.h"
#include "gvcal.h"

void lg(const char *fmt, ...) __attribute__((format(printf, 1, 2)));   // main.c (queued, IO thread writes)
static int g_gv_neo = 0;

#define GV_DIR      "/data/bb_probe"
#define GV_PROF_DIR GV_DIR "/profiles"
#define PKT3(op, nbody, st) (0xC0000000u | (((uint32_t)(nbody) - 1u) << 16) | ((uint32_t)(op) << 8) | ((uint32_t)(st) << 1))

// ---- memory ----
// GVW (WC_GARLIC, GPU only): captured buffers on our 480 x 270 grid of 4x4 display-pixel cells
#define GVW_G     0x000000u     // 480*270 x {centre xy, outlier xy} float = 0x1FA400
#define GVW_Z     0x1FC000u     // 480*270 x {min, max} float = 0xFD200
#define GVW_SZ    0x300000u
// GVO (WB_ONION, CPU + GPU): command fragments, tables, labels, statistics, calibration snapshot
#define GVO_SNAP  0x000000u     // 8160 x 40 bytes = 0x4FB00
#define GVO_FRAG  0x050000u     // ring of GV_NRING command fragments, 0x400 bytes each
#define GVO_CTAB  0x058000u     // ring of capture tables, 0x80 bytes each
#define GVO_CSTAT 0x059000u     // ring of capture statistics, 16 bytes each
#define GVO_CLBL  0x059200u     // ring of labels {drain, done}, 16 bytes each
#define GVO_GSTAT 0x059400u     // gv8 statistics per flip slot (4 x 16 bytes)
#define GVO_SNAPL 0x059480u     // snapshot label (u64)
#define GVO_TS    0x059500u     // GPU timestamps per flip slot (4 x 4 x u64)
#define GVO_SZ    0x060000u
#define GV_NRING  32
#define CELLS_W 480
#define CELLS_H 270

// gflags (table +0x1C0 dword 0), shared with gv8.ll / warp8g.ll
#define GVF_VALID 1u
#define GVF_APPLY 2u
#define GVF_DEPTH 4u
#define GVF_ZREV  8u
#define GVF_WARP  16u
#define GVF_SNAP  32u
#define GVF_SENT  64u
#define GVF_JIT   128u
#define GVF_ANCHOR 256u     // merged build: warp8g anchor penalty (letter 'a')

static uint8_t *g_gvw = NULL, *g_gvo = NULL, *g_gpu_ = NULL;
static uint32_t g_off_gvc8 = 0;
uint32_t gv_off_gv8 = 0, gv_off_warp8g = 0;
static uint32_t g_off_gvz8 = 0;
uint32_t gv_rsrc_gv8[2], gv_rsrc_gvc8[2], gv_rsrc_warp8g[2];
static volatile int g_armed = 0, g_live = 0;   // g_live: started AND the shaders are in place (gpu_init runs after gv_start)
static volatile uint32_t g_letters = 0;
static uint32_t g_abkind[5], g_abdw = 0, g_abdw_all = 0;   /* walk abort reasons: 1 time 2 size 3 bad packet 4 unreadable */
static uint64_t g_disp[8]; static int g_ndisp = 0;
static uint32_t g_dw = 1920, g_dh = 1080;
static char g_title[16] = "UNKNOWN", g_appver[8] = "";

#define GVO(off) (g_gvo + (off))
#define GVA(ptr) ((uint64_t)(uintptr_t)(ptr))

// ---------------------------------------------------------------------------------------------------------------
// configuration published by the IO thread, read once per frame by the submit thread
typedef struct {
    uint32_t ver;
    int vel_on;                      // track + capture a velocity candidate
    pm4_desc_t vd; uint32_t vord, swz;
    pm4_desc_t vcap, zcap;           // merged build: descriptors for the capture T# (tile / pitch from the game's T#)
    int cap_fixed; uint64_t vfix; uint32_t vfw, vfh;   // end-of-frame copy at a fixed address (no tracking)
    uint64_t vscan_addr; uint32_t vscan_w, vscan_h;    // non-aliased scan base address fallback for huge-DCB games
    int depth_on; pm4_desc_t zd; uint64_t zaddr; uint32_t zw, zh; int zrev;
    int apply, warp, sent, jitter;
    float ax, ay, bx, by; uint32_t sx, sy;
    uint32_t cal_w, cal_h; int drs_scale;
    uint32_t biasg, biasz;
    float vax, vay, vbx, vby;        // merged build: decode of the 'z' view (calibrated a, b, or a robust normalisation)
} gv_cfg_t;
static gv_cfg_t g_cfgs[2];
static volatile uint32_t g_cfg_i = 0, g_cfg_ver = 0;
static void cfg_publish(gv_cfg_t *c)
{
    uint32_t n = g_cfg_i ^ 1u;
    c->ver = ++g_cfg_ver;
    g_cfgs[n] = *c;
    __atomic_store_n(&g_cfg_i, n, __ATOMIC_RELEASE);
}
static void cfg_read(gv_cfg_t *o)
{
    for (int k = 0; k < 3; k++) {
        uint32_t i = __atomic_load_n(&g_cfg_i, __ATOMIC_ACQUIRE);
        *o = g_cfgs[i];
        if (__atomic_load_n(&g_cfg_i, __ATOMIC_ACQUIRE) == i && o->ver == g_cfgs[i].ver) return;
    }
}

// ---------------------------------------------------------------------------------------------------------------
// CPU-readable / GPU-readable checks (sceKernelVirtualQuery + sceKernelQueryMemoryProtection, cached for one frame)
#define PROT_CPU_READ 0x01
#define PROT_GPU_READ 0x10
#define GV_MC_MAX     64u
typedef struct { uintptr_t s, e; int32_t prot; } mrange_t;
static mrange_t g_mc[GV_MC_MAX]; static uint32_t g_mcn = 0, g_mq = 0, g_mq_fail = 0;
static int mem_ok(uintptr_t a, size_t n, int32_t need)
{
    uintptr_t e = a + n;
    if (!a || !n || a < 0x100000u || e < a || e > (1ull << 47)) return 0;
    uintptr_t cur = a;
    for (int guard = 0; guard < 32 && cur < e; guard++) {
        int hit = 0;
        for (uint32_t i = 0; i < g_mcn && i < GV_MC_MAX; i++)
            if (cur >= g_mc[i].s && cur < g_mc[i].e) {
                if ((g_mc[i].prot & need) != need) return 0;
                cur = g_mc[i].e; hit = 1; break;
            }
        if (hit) continue;
        void *s = NULL, *en = NULL; int32_t prot = 0;
        g_mq++;
        OrbisKernelVirtualQueryInfo vq;
        memset(&vq, 0, sizeof(vq));
        if (sceKernelVirtualQuery((const void *)cur, 0, &vq, sizeof(vq)) == 0 &&
            (uintptr_t)vq.unk01 <= cur && (uintptr_t)vq.unk02 > cur) {
            s = vq.unk01;
            en = vq.unk02;
            prot = vq.isCommitted ? (int32_t)vq.unk04 : 0;
            if (vq.isCommitted && !prot) {
                void *ps = NULL, *pe = NULL; int32_t pprot = 0;
                if (sceKernelQueryMemoryProtection((void *)cur, &ps, &pe, &pprot) == 0) prot = pprot;
            }
        } else if (sceKernelQueryMemoryProtection((void *)cur, &s, &en, &prot) != 0 ||
                   (uintptr_t)s > cur || (uintptr_t)en <= cur) {
            // Cache negative dword at cur so repeated checks of the same unmapped pointer do not repeat syscalls
            g_mc[g_mcn % GV_MC_MAX] = (mrange_t){ cur, cur + 4u, 0 };
            g_mcn++;
            g_mq_fail++;
            return 0;
        }
        g_mc[g_mcn % GV_MC_MAX] = (mrange_t){ (uintptr_t)s, (uintptr_t)en, prot };
        g_mcn++;
        if ((prot & need) != need) return 0;
        cur = (uintptr_t)en;
    }
    return cur >= e;
}
static int pm4_readable(const void *p, size_t n, void *u) { (void)u; return mem_ok((uintptr_t)p, n, PROT_CPU_READ); }
static uint64_t pm4_clock(void *u) { (void)u; return sceKernelGetProcessTime(); }

// ---------------------------------------------------------------------------------------------------------------
// submit-thread state of the current frame
static struct {
    pm4_frame_t pf;
    gv_cfg_t cfg;
    int begun, mode, incomplete;
    int vcaptured, vcap_slot; uint64_t vaddr; uint32_t vw, vh;
    int val, nval, val_slot[4];      // validation frame: stats-only copies after the entries that wrote the target
    int eof_vslot, eof_zslot, eof_hslot;
    uint32_t gflags, nfrag;
    uint32_t snap_seq;
    // statistics (read by the IO thread for the log, approximate)
    uint32_t frames, walked, cap_splice, cap_split, cap_eof, cap_fixed, not_written, uncuttable, aborted, contended;
    uint32_t walk_us_sum, walk_us_max, walk_n, wd_hits;
    uint64_t last_addr; uint32_t addr_same;
    uint32_t gs_valid, gs_agree, gs_used, gs_n;
    uint64_t gpu_cap, gpu_gv, gpu_n;
    int wd_track_off, wd_gpu;
    uint32_t fast_entry, fast_sz, fast_end_idx, fast_dw0, fast_dw_end;
} R;
static volatile uint32_t g_lock = 0;

// IO <-> submit handshakes
static volatile int g_scan_state = 0;      // 0 idle, 1 requested, 2 result ready in g_scan
static pm4_frame_t g_scan;
static volatile int g_snap_state = 0;      // 0 idle, 1 requested, 2 in flight (g_snap_seq)
static volatile uint32_t g_snap_seq = 0;
static volatile int g_val_state = 0;       // 0 idle, 1 requested, 2 in flight
static volatile uint32_t g_val_fseq[5]; static volatile int g_val_n = 0;   // [0..n-1] after-write copies, [4] eof
static volatile int g_freeze = 0;          // dump in progress: no new captures

// fragment ring: a slot is reused only after the GPU wrote its "done" label
static uint32_t g_ring_seq[GV_NRING], g_ring_next = 0, g_fseq = 0;
static volatile uint64_t *clbl(int k) { return (volatile uint64_t *)GVO(GVO_CLBL + (uint32_t)k * 16u); }
static int ring_take(void)
{
    for (int t = 0; t < GV_NRING; t++) {
        int k = (int)(g_ring_next++ % GV_NRING);
        if (g_ring_seq[k] == 0 || (uint32_t)clbl(k)[1] == g_ring_seq[k]) return k;
    }
    return -1;   // every slot still in flight: skip (never overwrite command memory the GPU may still read)
}

// ---------------------------------------------------------------------------------------------------------------
// descriptors (same encodings as fg8_table.h make_t / make_v)
static void mk_v(uint32_t *v, uint64_t a, uint32_t nbytes)
{
    v[0] = (uint32_t)a; v[1] = (uint32_t)(a >> 32) & 0xFFFFu; v[2] = nbytes; v[3] = 0x00027FAC;
}
// T# of a game target: IMG_DATA_FORMAT = CB FORMAT numbering, IMG_NUM_FORMAT = CB NUMBER_TYPE except SRGB (6 -> 9)
static int mk_t_target(uint32_t *t, uint64_t a, const pm4_desc_t *d, uint32_t w, uint32_t h)
{
    uint32_t dfmt, nfmt;
    if (d->depth) {
        if (d->fmt == 3) { dfmt = 4; nfmt = 7; }          // Z32F -> IMG_DATA_FORMAT_32, FLOAT
        else if (d->fmt == 2) { dfmt = 20; nfmt = 0; }    // Z24 -> IMG_DATA_FORMAT_8_24, UNORM
        else if (d->fmt == 1) { dfmt = 2; nfmt = 0; }     // Z16 -> 16, UNORM
        else return 0;
    } else {
        dfmt = d->fmt; nfmt = d->num == 6 ? 9u : d->num;
        if (nfmt == 4 || nfmt == 5) return 0;             // UINT / SINT: image_load returns raw integers
    }
    if (!w || !h || w > 16384 || h > 16384 || d->pitch < w) return 0;
    t[0] = (uint32_t)(a >> 8);
    t[1] = (uint32_t)((a >> 40) & 0xFFu) | (dfmt << 20) | (nfmt << 26);
    t[2] = (w - 1u) | ((h - 1u) << 14);
    t[3] = 4u | (5u << 3) | (6u << 6) | (7u << 9) | ((d->tile & 0x1Fu) << 20) | (9u << 28);   // XYZW, tile index, 2D
    t[4] = (d->pitch - 1u) << 13;
    t[5] = 0;
    t[6] = (g_gv_neo > 0 && d->tile != 8u) ? (1u << 24) : 0u;   // PS4 Pro neo=1 16-pipe alt_tile_mode
    t[7] = 0;
    return 1;
}

static uint32_t *ts_eop(uint32_t *p, volatile uint64_t *slot)
{
    uint64_t a = GVA(slot);
    *slot = 0;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)a; *p++ = 0x60000000u | ((uint32_t)(a >> 32) & 0xFFFFu);
    *p++ = 0; *p++ = 0;
    return p;
}

// One capture: wait for the game's work so far, flush its CB / DB caches over the target, run gvc8 into our grid,
// wait for our dispatch (so the game cannot overwrite the target before we read it), write the done label,
// restore the compute registers the game had set (only when spliced into the game's stream: `rest`).
// mode: 0 velocity -> G, 1 depth -> Z, 2 velocity statistics only. Returns NULL (nothing emitted) on refusal.
static uint32_t *emit_capture(uint32_t *p, int k, uint64_t a, const pm4_desc_t *d, uint32_t w, uint32_t h,
                              uint32_t mode, uint32_t swz, const pm4_csregs_t *rest)
{
    uint32_t *tb = (uint32_t *)GVO(GVO_CTAB + (uint32_t)k * 0x80u);
    uint64_t size = (uint64_t)d->pitch * d->alloc_h * (d->depth ? (d->fmt == 1 ? 2u : 4u) : pm4_cb_bpp(d->fmt));
    if (!size || !mem_ok((uintptr_t)a, (size_t)size, PROT_GPU_READ)) return NULL;   // never point the GPU at a hole
    if (!mk_t_target(tb, a, d, w, h)) return NULL;
    if (mode == 1) mk_v(tb + 8, GVA(g_gvw + GVW_Z), CELLS_W * CELLS_H * 8);
    else if (mode == 0) mk_v(tb + 8, GVA(g_gvw + GVW_G), CELLS_W * CELLS_H * 16);
    else mk_v(tb + 8, GVA(g_gvw + GVW_G), 0);                                         // stats only: stores dropped
    volatile uint32_t *st = (volatile uint32_t *)GVO(GVO_CSTAT + (uint32_t)k * 16u);
    st[0] = st[1] = st[2] = st[3] = 0;
    mk_v(tb + 12, GVA(st), 16);
    // gvc8 steps px in 0..1919 (480*4) and py in 0..1079 (270*4), so scale relative to the 1920x1080 reference grid
    float sx = (float)w / 1920.0f, sy = (float)h / 1080.0f;
    tb[16] = mode; tb[17] = swz; memcpy(&tb[18], &sx, 4); memcpy(&tb[19], &sy, 4);
    uint32_t fs = ++g_fseq; if (!fs) fs = ++g_fseq;
    g_ring_seq[k] = fs;
    volatile uint64_t *lb = clbl(k);
    lb[0] = 0; lb[1] = 0;
    uint64_t l0 = GVA(&lb[0]), l1 = GVA(&lb[1]), tbl = GVA(tb), pgm = GVA(g_gpu_ + g_off_gvc8);
    if (rest || d->depth) {
        // flush the colour / depth metadata caches (at EOF build_dcb_flow8 already flushed CB_META + drained EOP)
        *p++ = PKT3(0x46, 1, 0); *p++ = d->depth ? 0x0000002Cu : 0x0000002Eu;   // FLUSH_AND_INV_DB_META / _CB_META
    }
    if (rest) {
        *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;                              // CS_PARTIAL_FLUSH
        // end-of-pipe label + wait: every draw before this point has retired (only needed when spliced mid-frame)
        *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)l0; *p++ = 0x40000000u | ((uint32_t)(l0 >> 32) & 0xFFFFu); *p++ = fs; *p++ = 0;
        *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)l0; *p++ = (uint32_t)(l0 >> 32); *p++ = fs; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    }
    // CB0..7 + DB dest base, CB + DB action over the target (CP_COHER_CNTL bits 6..14, 25, 26; bit 31 as the game's
    // own 0x82003FC0), then L1/scalar invalidate (at EOF build_dcb_flow8 already wrote back L2).
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x86007FC0u; *p++ = (uint32_t)((size + 255u) >> 8); *p++ = 0;
    *p++ = (uint32_t)(a >> 8); *p++ = (uint32_t)(a >> 40); *p++ = 0x0A;
    if (rest) {
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    }
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;                         // NUM_THREAD 64,1,1
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);   // USER_DATA_0..1
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x20C; *p++ = (uint32_t)(pgm >> 8); *p++ = (uint32_t)(pgm >> 40);   // PGM_LO/HI
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x212; *p++ = gv_rsrc_gvc8[0]; *p++ = gv_rsrc_gvc8[1];             // RSRC1/2
    *p++ = PKT3(0x15, 4, 1); *p++ = 60; *p++ = 34; *p++ = 1; *p++ = 1;                              // 480 x 272 cells
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;                              // our dispatch done before the game goes on
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)l1; *p++ = 0x40000000u | ((uint32_t)(l1 >> 32) & 0xFFFFu); *p++ = fs; *p++ = 0;
    if (rest) {
        // the game may set a compute shader once and dispatch it several times: give it back what it had.
        // Registers it never set this frame still hold OUR values from the previous frame's passes, as they
        // always did since mode 6, so they need nothing.
        for (int i = 0; i < PM4_NCS; i++)
            if (rest->known & (1u << i)) { *p++ = PKT3(0x76, 2, 1); *p++ = pm4_cs_reg_idx[i]; *p++ = rest->v[i]; }
    }
    return p;
}

// one spliced fragment = one DCB entry of its own (never inside game memory)
static int frag_capture(void **d, uint32_t *s, uint64_t a, const pm4_desc_t *dd, uint32_t w, uint32_t h,
                        uint32_t mode, uint32_t swz, const pm4_csregs_t *rest, int *slot_out)
{
    int k = ring_take();
    if (k < 0) return 0;
    uint32_t *p0 = (uint32_t *)GVO(GVO_FRAG + (uint32_t)k * 0x400u), *p = emit_capture(p0, k, a, dd, w, h, mode, swz, rest);
    if (!p) { g_ring_seq[k] = 0; return 0; }
    *d = p0; *s = (uint32_t)(p - p0) * 4u;
    if (slot_out) *slot_out = k;
    R.nfrag++;
    return 1;
}

// ---------------------------------------------------------------------------------------------------------------
static int gv_ini_check(const char *path, int *mode_io, int *gv_io)
{
    int fd = sceKernelOpen(path, 0, 0);
    if (fd < 0) return 0;
    char buf[1024];
    long n = (long)sceKernelRead(fd, buf, sizeof(buf) - 1);
    sceKernelClose(fd);
    if (n <= 0) return 0;
    buf[n] = 0;
    char *p = buf;
    while (*p) {
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (!*p) break;
        char *eol = p;
        while (*eol && *eol != '\r' && *eol != '\n') eol++;
        char saved = *eol;
        *eol = 0;
        if (*p != ';' && *p != '#' && *p != '[') {
            unsigned v = 0;
            if (sscanf(p, "mode = %u", &v) == 1 || sscanf(p, "mode=%u", &v) == 1) *mode_io = (int)v;
            else if (sscanf(p, "game_vectors = %u", &v) == 1 || sscanf(p, "game_vectors=%u", &v) == 1) *gv_io = (v != 0);
        }
        if (!saved) break;
        p = eol + 1;
    }
    return 1;
}

static void *(*g_gv_alloc)(size_t, size_t, int) = NULL;

static int gv_ensure_mem(void)
{
    if (g_gvw && g_gvo) return 1;
    if (!g_gv_alloc) return 0;
    if (!g_gvw) g_gvw = (uint8_t *)g_gv_alloc(GVW_SZ, 0x10000, 3 /*WC_GARLIC*/);
    if (!g_gvw) g_gvw = (uint8_t *)g_gv_alloc(GVW_SZ, 0x10000, 0);
    if (!g_gvo) g_gvo = (uint8_t *)g_gv_alloc(GVO_SZ, 0x10000, 0 /*WB_ONION*/);
    if (!g_gvw || !g_gvo) {
        lg("GV: memory unavailable (gvw=%p gvo=%p), velocity off\n", (void *)g_gvw, (void *)g_gvo);
        return 0;
    }
    lg("GV: memory ready (late), gvw=%p gvo=%p\n", (void *)g_gvw, (void *)g_gvo);
    return 1;
}

int gv_load(void *(*alloc)(size_t, size_t, int))
{
    {   // title id + app version for the profile and per-game config name (GoldHEN SDK: current process)
        struct proc_info pi;
        memset(&pi, 0, sizeof(pi));
        if (sys_sdk_proc_info(&pi) == 0 && pi.titleid[0]) {
            int ok = 1;
            for (int i = 0; i < 15 && pi.titleid[i]; i++) {
                char c = pi.titleid[i];
                if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) ok = 0;
            }
            if (ok) { memcpy(g_title, pi.titleid, 15); g_title[15] = 0; }
            memcpy(g_appver, pi.version, 6); g_appver[6] = 0;
            for (int i = 0; i < 6; i++) if (g_appver[i] && (g_appver[i] < 32 || g_appver[i] > 126)) g_appver[i] = '?';
        }
    }
    int mode = 8, want = 1;
    int fd = sceKernelOpen(GV_DIR "/inject", 0, 0);
    char b[16] = { 0 };
    long n = fd >= 0 ? (long)sceKernelRead(fd, b, 15) : 0;
    if (fd >= 0) sceKernelClose(fd);
    if (n >= 1 && b[0] >= '0' && b[0] <= '8') mode = b[0] - '0';
    for (long i = 0; i < n; i++) if (b[i] == 'v') want = 1;
    gv_ini_check(GV_DIR "/config.ini", &mode, &want);
    if (g_title[0] && strcmp(g_title, "UNKNOWN") != 0) {
        char cpath[96];
        snprintf(cpath, sizeof(cpath), GV_DIR "/config-%s.ini", g_title);
        gv_ini_check(cpath, &mode, &want);
    }
    (void)want;
    if (mode != 8) return 0;
    g_gv_alloc = alloc;
    memset(&R, 0, sizeof(R));
    R.pf.readable = pm4_readable; R.pf.clock = pm4_clock;
    R.pf.budget_us = 3000; R.pf.max_dwords = 8u << 20;
    g_gv_neo = sceKernelIsNeoMode() ? 1 : 0;
    g_armed = 1;
    klog("[bb_probe] gv: armed (deferred alloc), title %s ver %s\n", g_title, g_appver);
    return 1;
}
int gv_armed(void) { return g_armed; }

static volatile int g_started = 0;
void gv_set_shaders(uint8_t *gpu, uint32_t o_gv8, uint32_t o_gvc8, uint32_t o_warp8g, uint32_t o_gvz8)
{
    g_gpu_ = gpu; gv_off_gv8 = o_gv8; g_off_gvc8 = o_gvc8; gv_off_warp8g = o_warp8g; g_off_gvz8 = o_gvz8;
    if (g_started && g_gpu_ && g_off_gvc8 && gv_off_gv8 && gv_off_warp8g && gv_ensure_mem()) g_live = 1;
}
void gv_set_display(const uint64_t *bufs, int n, uint32_t w, uint32_t h)
{
    g_ndisp = n > 8 ? 8 : n;
    for (int i = 0; i < g_ndisp; i++) g_disp[i] = bufs[i];
    if (w && h) { g_dw = w; g_dh = h; }
}
void gv_set_letters(uint32_t l) { g_letters = l; }

// ---------------------------------------------------------------------------------------------------------------
// IO-thread state machine
enum { ST_OFF, ST_SCAN, ST_CAL, ST_TRIAL, ST_ACTIVE, ST_NONE };
static const char *st_name[] = { "off", "scan", "calibrating", "trial", "active", "none" };
typedef struct { pm4_desc_t d; uint32_t ord, swz; int score; int state; char why[80];
                 int tok; uint32_t ttile, tpitch;
                 uint64_t base; uint32_t w, h, flags; } cand_t;   // state: 0 new 1 ok -1 rej -2 soft; tok: game T# tile/pitch
static struct {
    int st;
    cand_t cand[12]; int ncand, cur;
    gvcal_t cal; gvcal_result_t res;
    gv_cfg_t cfg;
    int have_prof; gvcal_result_t prof; cand_t prof_cand; char prof_capture[8];
    int depth_ok; pm4_desc_t zd, zcap; uint64_t zaddr; uint32_t zw, zh; int zrev;
    uint32_t snaps, snaps_inf, snap_wait, fails, passes, trial_n;
    uint32_t scans, rescans, last_scan_frame, last_scan_draws, cand_frame0;
    uint64_t t_log, t_req;
    uint32_t val_ok, val_bad, val_n;
    uint32_t emerg_bad, emerg_good; int shadow;
    uint32_t letters_seen;
    uint64_t t_cand;                 // when the current candidate / trial started (no-capture timeout)
    cand_t rej[12]; int nrej;        // hard rejections of this session (not retried by a re-scan)
} S;
static gv_rec_t g_snapcopy[GV_NB];

void gv_start(int mode8)
{
    if (!g_armed || !mode8) return;
    memset(&S, 0, sizeof(S));
    S.cfg.biasg = 48; S.cfg.biasz = 96;
    S.st = ST_OFF;
    g_started = 1;
    if (g_gpu_ && g_off_gvc8 && gv_off_gv8 && gv_off_warp8g && gv_ensure_mem()) g_live = 1;   // else when gpu_init hands the shaders over
    lg("GV start: title %s version %s, profile %s/%s.ini\n", g_title, g_appver, GV_PROF_DIR, g_title);
}

// ---------------------------------------------------------------------------------------------------------------
// submit thread
static void frame_begin(void)
{
    cfg_read(&R.cfg);
    R.begun = 1; R.incomplete = 0; R.vcaptured = 0; R.vaddr = 0; R.vcap_slot = -1; R.nval = 0; R.nfrag = 0;
    R.eof_vslot = R.eof_zslot = R.eof_hslot = -1; R.gflags = 0;
    g_mcn = 0;                                   // memory-protection cache lives for one frame only
    int scan = g_scan_state == 1;
    int track = R.cfg.vel_on && !R.cfg.cap_fixed && !R.wd_track_off;
    R.mode = (scan ? PM4_FULL : 0) | (track ? PM4_TRACK : 0);
    R.val = track && g_val_state == 1;
    R.pf.mode = (uint32_t)R.mode;
    R.pf.trk_on = track;
    if (track) { R.pf.trk_desc = R.cfg.vd; R.pf.trk_ordinal = R.cfg.vord; }
    R.pf.ndisplay = (uint32_t)g_ndisp;
    for (int i = 0; i < g_ndisp; i++) R.pf.display[i] = g_disp[i];
    // Allow 10 ms budget on the first 8 tracking frames of a candidate so large-DCB games (e.g. CUSA08519: 118k dwords)
    // can lock in the O(1) fast-track (R.addr_same >= 8) instead of aborting at 1.5 ms on every frame.
    uint32_t trk_budget = (track && R.addr_same < 8u && R.walk_n < 12u) ? 10000u : 1500u;
    R.pf.budget_us = (g_letters & GVL_WIDE) ? 15000u : (scan ? 10000u : trk_budget);
    pm4_frame_begin(&R.pf);
    R.frames++;
}

static void cs_now(pm4_csregs_t *o)
{
    for (int i = 0; i < PM4_NCS; i++) o->v[i] = R.pf.sh[pm4_cs_reg_idx[i]];
    o->known = R.pf.cs_seen;
}

uint32_t gv_rewrite(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz,
                    void **od, uint32_t *os, void **oc, uint32_t *ocs, int is_flip)
{
    if (!g_live || !count || count > PM4_MAX_ENTRY || !dcb || !dcbSz) return 0;
    if (__atomic_exchange_n(&g_lock, 1u, __ATOMIC_ACQUIRE)) { R.contended++; R.incomplete = 1; return 0; }
    uint32_t nout = 0;
    if (!R.begun) frame_begin();
    if (!R.mode || R.pf.abort || g_freeze) goto out;
    pm4_call_begin(&R.pf);
    int nosplice = (g_letters & GVL_NOSPLICE) != 0;
    int wrote[PM4_MAX_ENTRY];
    // O(1) CPU fast-track: once a non-aliased target has been captured at EOF at the same address for 8 frames,
    // verify its cached entry header + last-write packet signature in O(1) (~2 us) instead of walking 100k dwords.
    if (R.mode == PM4_TRACK && !R.val && R.addr_same >= 8u && R.last_addr) {
        if (R.pf.trk.written) goto out;
        if (R.fast_entry < count && dcb[R.fast_entry] && dcbSz[R.fast_entry] == R.fast_sz &&
            R.fast_end_idx * 4u < R.fast_sz) {
            const uint32_t *pe = (const uint32_t *)dcb[R.fast_entry];
            uint64_t sz_need = (uint64_t)R.cfg.vcap.pitch * R.cfg.vcap.alloc_h * pm4_cb_bpp(R.cfg.vcap.fmt);
            if (!((uintptr_t)pe & 3u) && mem_ok((uintptr_t)pe, R.fast_sz, PROT_CPU_READ) &&
                pe[0] == R.fast_dw0 && pe[R.fast_end_idx] == R.fast_dw_end &&
                sz_need && mem_ok((uintptr_t)R.last_addr, (size_t)sz_need, PROT_GPU_READ)) {
                R.pf.trk.written = 1; R.pf.trk.aliased = 0; R.pf.trk.addr = R.last_addr;
                R.pf.trk.w = R.vw ? R.vw : g_dw; R.pf.trk.h = R.vh ? R.vh : g_dh;
                goto out;
            }
        }
        if (is_flip) R.addr_same = 0;
    }
    for (uint32_t i = 0; i < count; i++) {
        wrote[i] = 0;
        uint32_t sz = dcbSz[i];
        if (!dcb[i] || (sz & 3u) || sz > (16u << 20)) { R.pf.abort = PM4_ABORT_BADPKT; break; }
        if (pm4_walk_entry(&R.pf, (const uint32_t *)dcb[i], sz / 4u, (int32_t)i) != PM4_OK) break;
        if (R.pf.trk.written && R.pf.trk.lw_entry == (int32_t)i) {
            wrote[i] = 1;
            if (!R.pf.trk.aliased && R.pf.trk.lw_end > 0 && R.pf.trk.lw_end * 4u <= sz) {
                const uint32_t *pe = (const uint32_t *)dcb[i];
                R.fast_entry = i; R.fast_sz = sz; R.fast_end_idx = R.pf.trk.lw_end - 1u;
                R.fast_dw0 = pe[0]; R.fast_dw_end = pe[R.fast_end_idx];
            }
        }
    }
    if (R.pf.abort) { R.incomplete = 1; goto out; }
    if (!(R.mode & PM4_TRACK)) goto out;
    {
        pm4_track_t *k = &R.pf.trk;
        int before = -1, split = -1;
        if (k->aliased && k->al_entry >= 0 && !R.vcaptured) {
            uint32_t i = (uint32_t)k->al_entry;
            if (k->lw_entry < k->al_entry) before = (int)i;                  // last write in an earlier entry / call
            else if ((g_letters & GVL_SPLIT) && k->lw_entry == k->al_entry && k->lw_end != 0xFFFFFFFFu &&
                     k->lw_end <= k->al_off && !k->al_nested && !(ccbSz && ccbSz[i]) && (k->al_off & 3u) == 0 &&
                     k->al_off > 0 && k->al_off * 4u < dcbSz[i])
                split = (int)i;                                                // cut between two top-level packets
            else R.uncuttable++;
        }
        // validation copies: after the last entry of this call that wrote the target (not after the flip entry)
        int vafter = -1;
        if (R.val && R.nval < 4)
            for (int i = (int)count - 1; i >= 0; i--)
                if (wrote[i] && !(is_flip && i == (int)count - 1) && !(k->aliased && i >= k->al_entry)) { vafter = i; break; }
        if (nosplice) {                          // walk + track only (the end-of-frame copy decides by itself)
            if (before >= 0 || split >= 0) R.uncuttable++;
            goto out;
        }
        if (before < 0 && split < 0 && vafter < 0) goto out;
        // the flip submit gets one more entry from main.c afterwards (our DCB): leave room for it (merged build fix;
        // v1.13 could hand flip_inner 24 entries, which it then refused, so that frame had no generated frame)
        if (count + 3u > (is_flip ? GV_MAXE - 1u : GV_MAXE)) goto out;
        uint32_t nfrag0 = R.nfrag;
        for (uint32_t i = 0; i < count; i++) {
            if ((int)i == before || (int)i == split) {
                pm4_csregs_t rs = (int)i == before ? R.pf.entry_cs[i] : k->al_cs;
                void *fd; uint32_t fsz; int sl;
                // build the fragment first: an entry is only ever split when our copy really goes in between
                if (frag_capture(&fd, &fsz, k->addr, &R.cfg.vcap, k->w, k->h, 0, R.cfg.swz, &rs, &sl)) {
                    if ((int)i == split) {           // first part of the game's entry, then ours, then the rest
                        od[nout] = dcb[i]; os[nout] = k->al_off * 4u; oc[nout] = ccb ? ccb[i] : NULL; ocs[nout] = ccbSz ? ccbSz[i] : 0; nout++;
                    }
                    od[nout] = fd; os[nout] = fsz; oc[nout] = NULL; ocs[nout] = 0; nout++;
                    R.vcaptured = 1; R.vcap_slot = sl; R.vaddr = k->addr; R.vw = k->w; R.vh = k->h;
                    if ((int)i == split) {
                        R.cap_split++;
                        od[nout] = (uint8_t *)dcb[i] + k->al_off * 4u; os[nout] = dcbSz[i] - k->al_off * 4u;
                        oc[nout] = NULL; ocs[nout] = 0; nout++;   // never split an entry with a CCB (we required ccbSz 0)
                        continue;
                    }
                    R.cap_splice++;
                }
            }
            od[nout] = dcb[i]; os[nout] = dcbSz[i]; oc[nout] = ccb ? ccb[i] : NULL; ocs[nout] = ccbSz ? ccbSz[i] : 0; nout++;
            if ((int)i == vafter) {
                pm4_csregs_t rs;
                if (i + 1 < count) rs = R.pf.entry_cs[i + 1]; else cs_now(&rs);
                void *fd; uint32_t fsz; int sl;
                if (frag_capture(&fd, &fsz, k->addr, &R.cfg.vcap, k->w, k->h, 2, R.cfg.swz, &rs, &sl)) {
                    od[nout] = fd; os[nout] = fsz; oc[nout] = NULL; ocs[nout] = 0; nout++;
                    R.val_slot[R.nval++] = sl;
                }
            }
        }
        if (R.nfrag == nfrag0) nout = 0;                 // nothing of ours went in: hand the game's arrays on as they are
        // a zero-size entry makes the CP fetch garbage (GPU page fault, README): never hand one out
        for (uint32_t i = 0; i < nout; i++) if (!os[i] || !od[i]) { nout = 0; R.vcaptured = 0; break; }
    }
out:
    __atomic_store_n(&g_lock, 0u, __ATOMIC_RELEASE);
    return nout;
}

uint32_t *gv_emit_eof(uint32_t *p, uint32_t seq, uint32_t slot)
{
    (void)seq;
    if (!g_live || !R.begun || g_freeze) return p;
    if (__atomic_exchange_n(&g_lock, 1u, __ATOMIC_ACQUIRE)) return p;
    volatile uint64_t *ts = (volatile uint64_t *)GVO(GVO_TS + slot * 32u);
    p = ts_eop(p, &ts[0]);
    const gv_cfg_t *c = &R.cfg;
    if (c->vel_on) {
        uint64_t a = 0; uint32_t w = 0, h = 0; int mode = -1;
        if (R.vcaptured) {
            if (R.val) { a = R.vaddr; w = R.vw; h = R.vh; mode = 2; }          // compare spliced copy with eof
        } else if ((R.mode & PM4_TRACK) && R.pf.trk.written && !R.pf.trk.aliased &&
                   ((!R.incomplete && !R.pf.abort) || (R.pf.abort == PM4_ABORT_TIME && c->vscan_addr != 0))) {
            a = R.pf.trk.addr; w = R.pf.trk.w; h = R.pf.trk.h; mode = 0;
        } else if (c->cap_fixed) {
            a = c->vfix; w = c->vfw; h = c->vfh; mode = 0;
        } else if (c->vscan_addr && (R.wd_track_off || R.pf.abort == PM4_ABORT_TIME)) {
            a = c->vscan_addr; w = c->vscan_w; h = c->vscan_h; mode = 0;
        } else if (R.mode & PM4_TRACK) {
            if (!R.pf.trk.written) R.not_written++;
        }
        if (mode >= 0) {
            int k = ring_take();
            uint32_t *q = k >= 0 ? emit_capture(p, k, a, &c->vcap, w, h, (uint32_t)mode, c->swz, NULL) : NULL;
            if (q) {
                p = q;
                if (mode == 0) { R.vcaptured = 1; R.vcap_slot = k; R.vaddr = a; R.vw = w; R.vh = h;
                                 if (c->cap_fixed) R.cap_fixed++; else R.cap_eof++; }
                else R.eof_hslot = k;
            } else if (k >= 0) g_ring_seq[k] = 0;
        }
        if (R.vcaptured && R.vaddr && !R.pf.trk.aliased && R.vaddr == R.last_addr) R.addr_same++;
        else { R.addr_same = 0; R.last_addr = (R.vcaptured && !R.pf.trk.aliased) ? R.vaddr : 0; }
    }
    if (c->depth_on && c->apply && c->zaddr) {
        int k = ring_take();
        uint32_t *q = k >= 0 ? emit_capture(p, k, c->zaddr, &c->zcap, c->zw, c->zh, 1, 0, NULL) : NULL;
        if (q) { p = q; R.eof_zslot = k; } else if (k >= 0) g_ring_seq[k] = 0;
    }
    p = ts_eop(p, &ts[1]);
    // validation record for the IO thread
    if (R.val) {
        int n = R.nval;
        for (int i = 0; i < n; i++) g_val_fseq[i] = (uint32_t)R.val_slot[i] | (g_ring_seq[R.val_slot[i]] << 8);
        int ek = R.vcaptured && R.eof_hslot < 0 ? R.vcap_slot : R.eof_hslot;   // eof copy = the real capture if at eof
        g_val_fseq[4] = ek >= 0 ? ((uint32_t)ek | (g_ring_seq[ek] << 8)) : 0xFFFFFFFFu;
        g_val_n = n;
        __atomic_store_n(&g_val_state, 2, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&g_lock, 0u, __ATOMIC_RELEASE);
    return p;
}

void gv_fill_table(uint32_t *tb, uint32_t seq, uint32_t slot)
{
    for (int i = 100; i < 128; i++) tb[i] = 0;
    R.gflags = 0;
    if (!g_live) return;
    // merged build: the anchor experiment and the debug view also work without a captured game buffer
    uint32_t anchor = (g_letters & GVL_ANCHOR) ? GVF_ANCHOR : 0u;
    {
        const gv_cfg_t *c = &R.cfg;
        memcpy(tb + 124, &c->vax, 4); memcpy(tb + 125, &c->vay, 4); memcpy(tb + 126, &c->vbx, 4); memcpy(tb + 127, &c->vby, 4);
    }
    if (!R.begun || !R.vcaptured || g_freeze) { tb[112] = anchor; R.gflags = anchor; return; }
    const gv_cfg_t *c = &R.cfg;
    uint32_t f = GVF_VALID | anchor;
    int apply = c->apply && !(g_letters & GVL_NOAPPLY) && !S.shadow;
    if (apply) f |= GVF_APPLY;
    if (apply && c->warp && !R.wd_gpu) f |= GVF_WARP;
    if (c->depth_on && R.eof_zslot >= 0) f |= GVF_DEPTH | (c->zrev ? GVF_ZREV : 0u);
    if (c->sent) f |= GVF_SENT;
    if (c->jitter) f |= GVF_JIT;
    if (g_snap_state == 1) {
        f |= GVF_SNAP;
        R.snap_seq = seq;
        *(volatile uint64_t *)GVO(GVO_SNAPL) = 0;
        g_snap_seq = seq;
        __atomic_store_n(&g_snap_state, 2, __ATOMIC_RELEASE);
    }
    mk_v(tb + 100, GVA(g_gvw + GVW_G), CELLS_W * CELLS_H * 16);
    mk_v(tb + 104, GVA(g_gvw + GVW_Z), CELLS_W * CELLS_H * 8);
    float ax = c->ax, ay = c->ay, bx = c->bx, by = c->by;
    if (c->drs_scale && c->cal_w && c->cal_h && R.vw >= 320u && R.vh >= 180u &&
        (R.vw != c->cal_w || R.vh != c->cal_h)) {
        ax *= (float)c->cal_w / (float)R.vw;
        ay *= (float)c->cal_h / (float)R.vh;
    }
    memcpy(tb + 108, &ax, 4); memcpy(tb + 109, &ay, 4); memcpy(tb + 110, &bx, 4); memcpy(tb + 111, &by, 4);
    tb[112] = f; tb[113] = c->sx; tb[114] = c->sy; tb[115] = (c->biasg & 1023u) | ((c->biasz & 1023u) << 16);
    mk_v(tb + 116, GVA(GVO(GVO_SNAP)), GV_NB * 40);
    volatile uint32_t *gs = (volatile uint32_t *)GVO(GVO_GSTAT + slot * 16u);
    gs[0] = gs[1] = gs[2] = 0; gs[3] = seq;
    mk_v(tb + 120, GVA(gs), 16);
    R.gflags = f;
}

int gv_pass_active(void) { return g_live && (R.gflags & GVF_VALID) && gv_off_gv8; }
int gv_warp_active(void)
{
    return g_live && gv_off_warp8g && ((R.gflags & (GVF_VALID | GVF_APPLY | GVF_WARP)) == (GVF_VALID | GVF_APPLY | GVF_WARP) ||
                                       (R.gflags & GVF_ANCHOR));
}
int gv_viz_active(void) { return g_live && g_off_gvz8 && (g_letters & GVL_VIZ) && !g_freeze; }

uint32_t *gv_emit_ts(uint32_t *p, uint32_t slot, int i)
{
    if (!g_live || !g_gvo) return p;
    return ts_eop(p, (volatile uint64_t *)GVO(GVO_TS + slot * 32u + (uint32_t)i * 8u));
}

uint32_t *gv_emit_after_gv8(uint32_t *p, uint32_t seq, uint32_t slot)
{
    (void)slot;
    if (R.gflags & GVF_SNAP) {   // snapshot complete: label = seq (CPU reads it in the IO thread)
        uint64_t a = GVA(GVO(GVO_SNAPL));
        *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
        *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)a; *p++ = 0x40000000u | ((uint32_t)(a >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    }
    return p;
}

void gv_frame_end(void)
{
    if (!g_live) return;
    if (__atomic_exchange_n(&g_lock, 1u, __ATOMIC_ACQUIRE)) return;
    if (R.begun) {
        if (R.mode) {
            R.walked++;
            if (R.pf.abort) { R.aborted++; if (R.pf.abort < 5) g_abkind[R.pf.abort]++; g_abdw = R.pf.abort_at_dw; g_abdw_all = R.pf.dwords; }
            uint32_t wsum = R.walk_us_sum + R.pf.us, wn = R.walk_n + 1u;
            R.walk_us_sum = wsum; R.walk_n = wn;
            if (R.pf.us > R.walk_us_max) R.walk_us_max = R.pf.us;
            if (R.pf.us > 4000u) R.wd_hits++;
            // watchdog: per-frame tracking costs more than 0.5 ms on average or 4 ms three times -> stop tracking
            if (!R.wd_track_off && !(g_letters & GVL_WIDE) && (R.mode & PM4_TRACK) &&
                ((wn >= 120u && (wsum / (wn ? wn : 1u)) > 500u) || R.wd_hits >= 3)) {
                R.wd_track_off = 1;
                lg("GV watchdog: command buffer walk %u us avg (%u max, %u over 4 ms): per-frame tracking OFF\n",
                   wn ? wsum / wn : 0u, R.walk_us_max, R.wd_hits);
            }
            if ((R.mode & PM4_FULL) && g_scan_state == 1) {
                memcpy(&g_scan, &R.pf, sizeof(g_scan));
                if (R.incomplete && !g_scan.abort) g_scan.abort = 99;   // contended / partial
                __atomic_store_n(&g_scan_state, 2, __ATOMIC_RELEASE);
            }
        }
        if (R.val && g_val_state == 1) g_val_state = 0;   // requested but nothing emitted this frame: retry
    }
    R.begun = 0;
    __atomic_store_n(&g_lock, 0u, __ATOMIC_RELEASE);
}

void gv_slot_stats(uint32_t slot)
{
    if (!g_live || !g_gvo) return;
    volatile uint32_t *gs = (volatile uint32_t *)GVO(GVO_GSTAT + slot * 16u);
    if (gs[3]) {   // seq set = gv8 statistics of that frame exist
        R.gs_valid += gs[0]; R.gs_agree += gs[1]; R.gs_used += gs[2]; R.gs_n++;
        // emergency switch (shadow mode) on a buffer that suddenly disagrees everywhere (stale address, menu)
        if (gs[0] >= 1000u) {
            if (gs[1] * 100u < gs[0] * 15u) { __atomic_add_fetch(&S.emerg_bad, 1u, __ATOMIC_RELAXED); __atomic_store_n(&S.emerg_good, 0u, __ATOMIC_RELAXED); }
            else if (gs[1] * 100u >= gs[0] * 40u) { __atomic_add_fetch(&S.emerg_good, 1u, __ATOMIC_RELAXED); __atomic_store_n(&S.emerg_bad, 0u, __ATOMIC_RELAXED); }
        }
        gs[3] = 0;
    }
    volatile uint64_t *ts = (volatile uint64_t *)GVO(GVO_TS + slot * 32u);
    if (ts[1] > ts[0] && ts[3] > ts[2] && ts[0]) { R.gpu_cap += ts[1] - ts[0]; R.gpu_gv += ts[3] - ts[2]; R.gpu_n++; }
    ts[0] = ts[1] = ts[2] = ts[3] = 0;
}

// ---------------------------------------------------------------------------------------------------------------
// IO thread
static void cand_name(const cand_t *c, char *o, size_t n)
{
    char tt[32] = "";
    if (c->tok && (c->ttile != c->d.tile || c->tpitch != c->d.pitch)) snprintf(tt, sizeof(tt), " (game T#: t%u p%u)", c->ttile, c->tpitch);
    snprintf(o, n, "%s %s p%u h%u t%u ord%u%s%s", pm4_fmt_name(c->d.depth, c->d.fmt), pm4_num_name(c->d.num), c->d.pitch,
             c->d.alloc_h, c->d.tile, c->ord, c->swz ? " .zw" : "", tt);
}

static void prof_path(char *o, size_t n) { snprintf(o, n, GV_PROF_DIR "/%s.ini", g_title); }

static void prof_write(const char *state, const cand_t *c, const gvcal_result_t *r, const char *capture, const char *why)
{
    char path[96];
    sceKernelMkdir(GV_PROF_DIR, 0777);
    prof_path(path, sizeof(path));
    FILE *f = fopen(path, "w");
    if (!f) { lg("GV profile: cannot write %s\n", path); return; }
    fprintf(f, "; bb_probe velocity profile (written by the plugin; delete the file or use inject letter 'r' to recalibrate)\n");
    fprintf(f, "version=1\ntitle=%s\napp_ver=%s\nstate=%s\nreason=%s\n", g_title, g_appver, state, why ? why : "");
    if (c) {
        fprintf(f, "vel_depth=%u\nvel_fmt=%u\nvel_num=%u\nvel_pitch=%u\nvel_alloc_h=%u\nvel_tile=%u\nvel_ordinal=%u\nvel_swz=%u\n",
                c->d.depth, c->d.fmt, c->d.num, c->d.pitch, c->d.alloc_h, c->d.tile, c->ord, c->swz);
        fprintf(f, "capture=%s\n", capture ? capture : "track");
        if (c->tok) fprintf(f, "vel_ttile=%u\nvel_tpitch=%u\n", c->ttile, c->tpitch);
    }
    if (r) {
        fprintf(f, "ax=%.6g\nbx=%.6g\nay=%.6g\nby=%.6g\nr2x=%.4f\nr2y=%.4f\njit_x=%.3f\njit_y=%.3f\n",
                r->ax, r->bx, r->ay, r->by, r->r2x, r->r2y, r->jit_x, r->jit_y);
        fprintf(f, "sentinel=%d\nsent_x=0x%08x\nsent_y=0x%08x\nunits=%s\nframes=%u\nblocks=%u\n", r->sentinel, r->sent_x,
                r->sent_y, r->units, r->frames, r->blocks);
        if (r->cal_w && r->cal_h)
            fprintf(f, "cal_w=%u\ncal_h=%u\ndrs_scale=%d\n", r->cal_w, r->cal_h, r->drs_scale);
    }
    for (int i = 0; i < S.ncand; i++)
        if (S.cand[i].state == -1) {
            char nm[80]; cand_name(&S.cand[i], nm, sizeof(nm));
            fprintf(f, "rejected=%s | %s\n", nm, S.cand[i].why);
        }
    fclose(f);
    lg("GV profile written: %s (state %s)\n", path, state);
}

static int prof_read(void)
{
    char path[96], line[160];
    prof_path(path, sizeof(path));
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    memset(&S.prof, 0, sizeof(S.prof)); memset(&S.prof_cand, 0, sizeof(S.prof_cand));
    char st[16] = "", ver[8] = "";
    unsigned u; float x; int okv = 0, okc = 0, oka = 0;
    strcpy(S.prof_capture, "track");
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == ';') continue;
        if (sscanf(line, "version=%u", &u) == 1) okv = u == 1;
        else if (sscanf(line, "state=%15s", st) == 1) {}
        else if (sscanf(line, "app_ver=%7s", ver) == 1) {}
        else if (sscanf(line, "vel_depth=%u", &u) == 1) S.prof_cand.d.depth = u;
        else if (sscanf(line, "vel_fmt=%u", &u) == 1) { S.prof_cand.d.fmt = u; okc++; }
        else if (sscanf(line, "vel_num=%u", &u) == 1) S.prof_cand.d.num = u;
        else if (sscanf(line, "vel_pitch=%u", &u) == 1) { S.prof_cand.d.pitch = u; okc++; }
        else if (sscanf(line, "vel_alloc_h=%u", &u) == 1) S.prof_cand.d.alloc_h = u;
        else if (sscanf(line, "vel_tile=%u", &u) == 1) S.prof_cand.d.tile = u;
        else if (sscanf(line, "vel_ordinal=%u", &u) == 1) S.prof_cand.ord = u;
        else if (sscanf(line, "vel_swz=%u", &u) == 1) S.prof_cand.swz = u;
        else if (sscanf(line, "vel_ttile=%u", &u) == 1) { S.prof_cand.ttile = u; S.prof_cand.tok |= 1; }
        else if (sscanf(line, "vel_tpitch=%u", &u) == 1) { S.prof_cand.tpitch = u; S.prof_cand.tok |= 2; }
        else if (sscanf(line, "capture=%7s", S.prof_capture) == 1) {}
        else if (sscanf(line, "ax=%f", &x) == 1) { S.prof.ax = x; oka++; }
        else if (sscanf(line, "bx=%f", &x) == 1) S.prof.bx = x;
        else if (sscanf(line, "ay=%f", &x) == 1) { S.prof.ay = x; oka++; }
        else if (sscanf(line, "by=%f", &x) == 1) S.prof.by = x;
        else if (sscanf(line, "jit_x=%f", &x) == 1) S.prof.jit_x = x;
        else if (sscanf(line, "jit_y=%f", &x) == 1) S.prof.jit_y = x;
        else if (sscanf(line, "sentinel=%u", &u) == 1) S.prof.sentinel = (int)u;
        else if (sscanf(line, "sent_x=%x", &u) == 1) S.prof.sent_x = u;
        else if (sscanf(line, "sent_y=%x", &u) == 1) S.prof.sent_y = u;
        else if (sscanf(line, "cal_w=%u", &u) == 1) S.prof.cal_w = u;
        else if (sscanf(line, "cal_h=%u", &u) == 1) S.prof.cal_h = u;
        else if (sscanf(line, "drs_scale=%u", &u) == 1) S.prof.drs_scale = (int)u;
    }
    fclose(f);
    if (!okv) { lg("GV profile %s: unknown version, ignored\n", path); return 0; }
    if (strcmp(st, "accepted") != 0) { lg("GV profile %s: state '%s' (no velocity buffer last time), scanning again\n", path, st); return 0; }
    if (okc < 2 || oka < 2) { lg("GV profile %s: incomplete, ignored\n", path); return 0; }
    if (strcmp(ver, g_appver) != 0) lg("GV profile %s: made with app version %s, running %s: validating first\n", path, ver, g_appver);
    S.prof.state = 1;
    return 1;
}

static void cfg_for(const cand_t *c, const gvcal_result_t *r, int apply)
{
    gv_cfg_t *g = &S.cfg;
    if (r) { g->vax = r->ax; g->vay = r->ay; g->vbx = r->bx; g->vby = r->by; }   // 'z' view: calibrated decode
    else { g->vax = g->vay = 1.0f; g->vbx = g->vby = 0.0f; }
    g->vel_on = c != NULL;
    g->cap_fixed = 0;                        // a new candidate / profile is tracked again
    g->vscan_addr = 0; g->vscan_w = g->vscan_h = 0;
    R.wd_track_off = 0; R.wd_gpu = 0; R.addr_same = 0; R.last_addr = 0;
    R.walk_us_sum = R.walk_n = R.walk_us_max = R.wd_hits = 0;
    if (c) {
        g->vd = c->d; g->vord = c->ord; g->swz = c->swz;
        g->vcap = c->d;
        if (c->tok & 1) g->vcap.tile = c->ttile;
        if ((c->tok & 2) && c->tpitch && c->tpitch * 2u >= c->d.pitch && c->tpitch <= c->d.pitch * 2u) g->vcap.pitch = c->tpitch;
        if (!(c->flags & PM4RT_ALIASED) && c->base && c->w && c->h) {
            g->vscan_addr = c->base; g->vscan_w = c->w; g->vscan_h = c->h;
        }
    }
    g->apply = apply; g->warp = apply;
    if (r) {
        g->ax = r->ax; g->ay = r->ay; g->bx = r->bx; g->by = r->by;
        g->sent = r->sentinel; g->sx = r->sent_x; g->sy = r->sent_y;
        g->jitter = r->jit_x > 0.25f || r->jit_y > 0.25f;
        g->cal_w = r->cal_w; g->cal_h = r->cal_h; g->drs_scale = r->drs_scale;
    } else {
        g->ax = g->ay = 1.0f; g->bx = g->by = 0.0f; g->sent = 0; g->jitter = 0;
        g->cal_w = g->cal_h = 0; g->drs_scale = 0;
    }
    g->depth_on = S.depth_ok; g->zd = S.zd; g->zcap = S.zcap; g->zaddr = S.zaddr; g->zw = S.zw; g->zh = S.zh; g->zrev = S.zrev;
    cfg_publish(g);
}

static void cfg_depth_update(void)
{
    S.cfg.depth_on = S.depth_ok; S.cfg.zd = S.zd; S.cfg.zcap = S.zcap; S.cfg.zaddr = S.zaddr; S.cfg.zw = S.zw; S.cfg.zh = S.zh; S.cfg.zrev = S.zrev;
    cfg_publish(&S.cfg);
}

static void start_candidate(int i)
{
    S.cur = i;
    S.t_cand = sceKernelGetProcessTime();
    S.cand_frame0 = R.frames;
    gvcal_reset(&S.cal);
    S.snaps = S.snaps_inf = S.snap_wait = 0;
    char nm[80]; cand_name(&S.cand[i], nm, sizeof(nm));
    lg("GV calibrating candidate %d/%d: %s (score %d)\n", i + 1, S.ncand, nm, S.cand[i].score);
    S.st = ST_CAL;
    cfg_for(&S.cand[i], NULL, 0);
}

static void next_candidate(void)
{
    for (int i = 0; i < S.ncand; i++) if (S.cand[i].state == 0) { start_candidate(i); return; }
    S.st = ST_NONE;
    cfg_for(NULL, NULL, 0);
    lg("GV: no usable velocity buffer among %d candidates: optical flow only. Next scan in ~2 min\n", S.ncand);
    if (!S.have_prof)
        prof_write("none", NULL, NULL, NULL, "no candidate passed calibration");
    S.last_scan_frame = R.frames;
}

static void write_scan_file(const pm4_frame_t *f, uint32_t n)
{
    char path[96];
    snprintf(path, sizeof(path), GV_DIR "/scan_%u.txt", n % 8u);
    FILE *o = fopen(path, "w");
    if (!o) return;
    fprintf(o, "scan %u title %s: dwords %u packets %u IBs %u (unreadable %u, depth %u) draws %u dispatches %u passes %u "
               "dma %u type0 %u loads %u clear_state %u T# hits %u tables %u entries %u calls %u ce %u walk %u us abort %d\n",
            n, g_title, f->dwords, f->packets, f->ibs, f->ib_unreadable, f->depth_max, f->draws, f->dispatches, f->passes,
            f->dma, f->type0, f->loads, f->clear_state, f->tsharp_hits, f->tables_read, f->entries, f->calls, f->ce_entries,
            f->us, f->abort);
    fprintf(o, "#  address      fmt         num     w x h       pitch allocH tile bpp draws zdraws first last passes "
               "slots flags samp(ps/cs@draw) ord score marker\n");
    for (uint32_t i = 0; i < f->nrt; i++) {
        const pm4_rt_t *r = &f->rt[i];
        fprintf(o, "%2u %010llx %-11s %-6s %5ux%-5u %5u %5u %3u %3u %5u %5u %5u %5u %3u-%-3u %03x %04x %u/%u@%u %u %4d %s\n", i,
                (unsigned long long)r->base, pm4_fmt_name(r->d.depth, r->d.fmt), pm4_num_name(r->d.num), r->w, r->h,
                r->d.pitch, r->d.alloc_h, r->d.tile, r->bpp, r->draws, r->zdraws, r->first_draw, r->last_draw,
                r->first_pass, r->last_pass, r->slot_mask, r->flags, r->samp_ps, r->samp_cs, r->samp_draw, r->ordinal,
                r->score, r->mark && r->mark <= f->nmark ? f->marks[r->mark - 1] : "");
    }
    fprintf(o, "flags: 1 fastclear 2 compressed 4 eliminate/resolve 8 dma-clear 10 dma-write 20 aliased 40 z-clear 80 msaa "
               "100 sampled 200 display 400 mrt\n");
    for (uint32_t i = 0; i < f->nrt; i++) {   // merged build: what the game's own T# says (tile / pitch) vs the CB / DB view
        uint32_t tt, tp;
        if (pm4_rt_game_tsharp(&f->rt[i], &tt, &tp))
            fprintf(o, "game T# of %2u: tile %u pitch %u%s\n", i, tt, tp,
                    (tt != f->rt[i].d.tile || tp != f->rt[i].d.pitch) ? "  <- differs from the CB/DB registers, capture uses this" : "");
    }
    for (uint32_t i = 0; i < f->nmark; i++) fprintf(o, "marker %u: %s\n", i + 1, f->marks[i]);
    fclose(o);
}

static void process_scan(void)
{
    pm4_frame_t *f = &g_scan;
    S.scans++;
    S.last_scan_draws = f->draws;
    if (f->abort && f->abort != 99) {
        lg("GV scan %u incomplete (abort %d at %u dwords, %u us): retrying\n", S.scans, f->abort, f->abort_at_dw, f->us);
    }
    pm4_rank(f, g_dw, g_dh);
    write_scan_file(f, S.scans);
    lg("GV scan %u: %u dw, %u packets, %u IBs (%u unreadable), %u draws, %u dispatches, %u passes, %u targets, %u T# hits, "
       "%u us -> scan_%u.txt\n", S.scans, f->dwords, f->packets, f->ibs, f->ib_unreadable, f->draws, f->dispatches,
       f->passes, f->nrt, f->tsharp_hits, f->us, S.scans % 8u);
    // In multi-submit games (CUSA03041), if g_scan_state=1 was armed mid-frame right before the 256B flip DCB,
    // the scan only sees the flip submit (~1083 dw, <=2 draws, 1 target). Retry immediately on the next full frame!
    if (f->draws < 16u && S.scans <= 4u) {
        lg("GV scan %u saw only %u draws (mid-frame flip submit): scheduling immediate full-frame retry\n", S.scans, f->draws);
        g_scan_state = 1;
        return;
    }
    // Menu / loading screen with too few draws (< 64): do not waste calibration time on 2D UI targets; re-scan in ~6s
    if (f->draws < 64u) {
        S.last_scan_frame = R.frames;
        if (S.st == ST_SCAN) S.st = ST_NONE;
        lg("GV: menu/loading scene (%u draws < 64): waiting for 3D scene\n", f->draws);
        return;
    }
    // depth: best scoring DB target
    int bz = -1;
    for (uint32_t i = 0; i < f->nrt; i++)
        if (f->rt[i].d.depth && f->rt[i].score >= 50 && (bz < 0 || f->rt[i].score > f->rt[bz].score)) bz = (int)i;
    if (bz >= 0) {
        const pm4_rt_t *z = &f->rt[bz];
        S.depth_ok = !(z->flags & PM4RT_MSAA) && (z->d.fmt == 3 || z->d.fmt == 2 || z->d.fmt == 1);
        S.zd = z->d; S.zaddr = z->base; S.zw = z->w; S.zh = z->h;
        S.zcap = z->d;
        {   // the game's own T# of this depth buffer (tile index / pitch), if it was sampled
            uint32_t tt, tp;
            if (pm4_rt_game_tsharp(z, &tt, &tp)) {
                if (tt != z->d.tile || tp != z->d.pitch) lg("GV depth: game samples it with tile %u pitch %u (DB: tile %u pitch %u): using the game's\n", tt, tp, z->d.tile, z->d.pitch);
                S.zcap.tile = tt; S.zcap.pitch = tp;
            }
        }
        uint32_t rev = z->zfunc[4] + z->zfunc[6], fwd = z->zfunc[1] + z->zfunc[3];
        S.zrev = rev > fwd;
        lg("GV depth: %010llx %s %ux%u tile %u, %u draws, ZFUNC greater %u / less %u -> %s Z%s\n", (unsigned long long)z->base,
           pm4_fmt_name(1, z->d.fmt), z->w, z->h, z->d.tile, z->draws, rev, fwd, S.zrev ? "reversed" : "normal",
           (z->flags & PM4RT_COMPRESSED) ? " (HTILE: valid only if the game decompressed it before the end of the frame)" : "");
    }
    if (S.st != ST_SCAN) {
        if (S.cfg.vel_on) {
            for (uint32_t i = 0; i < f->nrt; i++) {
                const pm4_rt_t *r = &f->rt[i];
                if (pm4_desc_eq(&r->d, &S.cfg.vd) && r->ordinal == S.cfg.vord && !(r->flags & PM4RT_ALIASED) && r->base && r->w && r->h) {
                    S.cfg.vscan_addr = r->base; S.cfg.vscan_w = r->w; S.cfg.vscan_h = r->h;
                    S.prof_cand.base = r->base; S.prof_cand.w = r->w; S.prof_cand.h = r->h; S.prof_cand.flags = r->flags;
                    break;
                }
            }
            cfg_depth_update();
        }
        return;
    }
    // keep this session's hard rejections out of the new list
    for (int i = 0; i < S.ncand && S.nrej < 12; i++) if (S.cand[i].state == -1) S.rej[S.nrej++] = S.cand[i];
    // velocity candidates: colour targets, score >= 30, best first; RGBA16 twice (xy and zw)
    S.ncand = 0;
    for (int pass = 0; pass < 2; pass++)
        for (uint32_t i = 0; i < f->nrt && S.ncand < 12; i++) {
            const pm4_rt_t *r = &f->rt[i];
            if (r->d.depth || r->score < 30 || r->d.num == 4 || r->d.num == 5) continue;
            if (!(r->nch == 2 || (r->nch == 4 && r->bpp >= 8))) continue;
            if (pass == 1 && !(r->nch == 4 && r->bpp == 8)) continue;
            cand_t c = { r->d, r->ordinal, (uint32_t)pass, r->score - pass * 5, 0, "", 0, 0, 0, r->base, r->w, r->h, r->flags };
            if (pm4_rt_game_tsharp(r, &c.ttile, &c.tpitch)) c.tok = 3;
            int dup = 0;
            for (int j = 0; j < S.nrej; j++) if (pm4_desc_eq(&S.rej[j].d, &c.d) && S.rej[j].ord == c.ord && S.rej[j].swz == c.swz) dup = 1;
            for (int j = 0; j < S.ncand; j++) if (pm4_desc_eq(&S.cand[j].d, &c.d) && S.cand[j].ord == c.ord && S.cand[j].swz == c.swz) dup = 1;
            if (!dup) S.cand[S.ncand++] = c;
        }
    for (int a = 0; a < S.ncand; a++)          // sort by score (insertion)
        for (int b = a + 1; b < S.ncand; b++)
            if (S.cand[b].score > S.cand[a].score) { cand_t t = S.cand[a]; S.cand[a] = S.cand[b]; S.cand[b] = t; }
    for (int i = 0; i < S.ncand && i < 6; i++) {
        char nm[80]; cand_name(&S.cand[i], nm, sizeof(nm));
        lg("GV candidate %d: %s score %d\n", i + 1, nm, S.cand[i].score);
    }
    if (!S.ncand) { S.st = ST_NONE; S.last_scan_frame = R.frames; lg("GV: no 2-channel / RGBA16 target that could be velocity (see scan file)\n"); return; }
    if (S.have_prof) {   // a stored candidate that failed validation goes first
        for (int i = 0; i < S.ncand; i++)
            if (pm4_desc_eq(&S.cand[i].d, &S.prof_cand.d) && S.cand[i].ord == S.prof_cand.ord && S.cand[i].swz == S.prof_cand.swz && i) {
                cand_t t = S.cand[0]; S.cand[0] = S.cand[i]; S.cand[i] = t;
            }
    }
    start_candidate(0);
}

// 'z' view before calibration: robust centre / spread of the raw values of the last snapshot, so that 1 MAD of the raw
// values shows as 8 px (the structure of an unknown buffer becomes visible whatever its units)
static float g_vtmp[GV_NB];
static float kth_(float *a, int n, int k)
{
    int lo = 0, hi = n - 1;
    while (lo < hi) {
        float p = a[(lo + hi) >> 1]; int i = lo, j = hi;
        while (i <= j) { while (a[i] < p) i++; while (a[j] > p) j--; if (i <= j) { float t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; } }
        if (k <= j) hi = j; else if (k >= i) lo = i; else return a[k];
    }
    return a[k];
}
static void view_norm(const gv_rec_t *rec)
{
    float med[2], va[2];
    for (int ax = 0; ax < 2; ax++) {
        int n = 0;
        for (int i = 0; i < GV_NB; i++) {
            float v = ax ? rec[i].ry : rec[i].rx; uint32_t u; memcpy(&u, &v, 4);
            if ((rec[i].flags & GVREC_GVALID) && (u & 0x7F800000u) != 0x7F800000u) g_vtmp[n++] = v;
        }
        if (n < 64) return;
        med[ax] = kth_(g_vtmp, n, n / 2);
        for (int i = 0; i < n; i++) { float d = g_vtmp[i] - med[ax]; g_vtmp[i] = d < 0 ? -d : d; }
        float mad = kth_(g_vtmp, n, n / 2);
        va[ax] = mad > 1e-12f ? 8.0f / mad : 1.0f;
    }
    S.cfg.vax = va[0]; S.cfg.vay = va[1]; S.cfg.vbx = -va[0] * med[0]; S.cfg.vby = -va[1] * med[1];
    cfg_publish(&S.cfg);
}

static int snap_ready(void)
{
    if (g_snap_state != 2) return 0;
    if ((uint32_t)*(volatile uint64_t *)GVO(GVO_SNAPL) != g_snap_seq) {
        if (++S.snap_wait > 500) { g_snap_state = 0; S.snap_wait = 0; }   // 1 s: frame was dropped, ask again
        return 0;
    }
    S.snap_wait = 0;
    memcpy(g_snapcopy, GVO(GVO_SNAP), sizeof(g_snapcopy));   // onion memory: cached CPU reads
    g_snap_state = 0;
    return 1;
}

static void process_validation(void)
{
    if (g_val_state != 2) return;
    // wait until every copy of that frame is done
    int n = g_val_n;
    uint32_t e = g_val_fseq[4];
    for (int i = 0; i < n; i++) {
        uint32_t k = g_val_fseq[i] & 0xFFu, s = g_val_fseq[i] >> 8;
        if ((uint32_t)clbl((int)k)[1] != s) return;
    }
    if (e != 0xFFFFFFFFu && (uint32_t)clbl((int)(e & 0xFFu))[1] != (e >> 8)) return;
    if (n && e != 0xFFFFFFFFu) {
        volatile uint32_t *a = (volatile uint32_t *)GVO(GVO_CSTAT + (g_val_fseq[n - 1] & 0xFFu) * 16u);
        volatile uint32_t *b = (volatile uint32_t *)GVO(GVO_CSTAT + (e & 0xFFu) * 16u);
        int same = a[0] == b[0] && a[1] == b[1];
        S.val_n++;
        if (same) S.val_ok++; else S.val_bad++;
        lg("GV validation: copy after the last write %08x/%u vs end of frame %08x/%u -> %s (%u same, %u different)\n",
           a[0], a[1], b[0], b[1], same ? "same" : "DIFFERENT", S.val_ok, S.val_bad);
    }
    g_val_state = 0;
}

static double g_gpu_rate = 0;
void gv_set_gpu_rate(double r) { g_gpu_rate = r; }

static void log_status(void)
{
    uint32_t wn = R.walk_n ? R.walk_n : 1u;
    double gms = 0;
    lg("GV %s: frames %u walked %u (abort %u, busy %u) walk %u us avg %u max | captured splice %u split %u eof %u fixed %u, "
       "not written %u, uncuttable %u | gv8 blocks valid %u agree %u used %u (avg/frame) | mem queries %u (fail %u)%s\n",
       st_name[S.st], R.frames, R.walked, R.aborted, R.contended, R.walk_us_sum / wn, R.walk_us_max, R.cap_splice, R.cap_split,
       R.cap_eof, R.cap_fixed, R.not_written, R.uncuttable, R.gs_n ? R.gs_valid / R.gs_n : 0u,
       R.gs_n ? R.gs_agree / R.gs_n : 0u, R.gs_n ? R.gs_used / R.gs_n : 0u, g_mq, g_mq_fail, S.shadow ? " SHADOW" : "");
    lg("GV walk aborts: time %u size %u badpkt %u unreadable %u | last: at dw %u of %u walked\n", g_abkind[1], g_abkind[2], g_abkind[3], g_abkind[4], g_abdw, g_abdw_all);
    (void)gms;
    if (R.gpu_n && g_gpu_rate > 0) {
        double cap = (double)R.gpu_cap / R.gpu_n / g_gpu_rate * 1000.0, gv = (double)R.gpu_gv / R.gpu_n / g_gpu_rate * 1000.0;
        lg("GV GPU time per frame: copies %.3f ms + gv8 %.3f ms (warp8g extra is inside the warp figure of the pass line)\n", cap, gv);
        // GPU budget watchdog: only enforced once active, with realistic budget for 1080p velocity + depth copies
        if (S.st == ST_ACTIVE && cap + gv > 3.0 && !R.wd_gpu && !(g_letters & GVL_WIDE)) { R.wd_gpu = 1; lg("GV GPU watchdog: %.2f ms, warp8g per-pixel game vectors OFF\n", cap + gv); }
        else if (S.st == ST_ACTIVE && cap + gv > 4.0 && R.wd_gpu && !(g_letters & GVL_WIDE)) {
            lg("GV GPU watchdog: %.2f ms over budget: velocity subsystem OFF for this session\n", cap + gv);
            S.st = ST_NONE; S.rescans = 3; S.depth_ok = 0; cfg_for(NULL, NULL, 0);
        }
    }
    R.walk_us_sum = R.walk_n = R.walk_us_max = 0; R.wd_hits = 0;
    R.cap_splice = R.cap_split = R.cap_eof = R.cap_fixed = R.not_written = R.uncuttable = R.walked = R.aborted = 0;
    R.gs_valid = R.gs_agree = R.gs_used = R.gs_n = 0; R.gpu_cap = R.gpu_gv = R.gpu_n = 0;
}

static void do_dump(void)
{
    // freeze captures, let the GPU finish the last frames, write the grids + snapshot, unfreeze
    static int phase = 0; static uint32_t t0 = 0;
    if (phase == 0) { g_freeze = 1; t0 = R.frames; phase = 1; if (g_snap_state == 0) {} return; }
    if (R.frames < t0 + 6) return;
    const struct { const char *n; uint32_t off, sz; uint8_t *base; } fl[3] = {
        { GV_DIR "/gv_vel_480x270_f32x4.bin", GVW_G, CELLS_W * CELLS_H * 16, g_gvw },
        { GV_DIR "/gv_depth_480x270_f32x2.bin", GVW_Z, CELLS_W * CELLS_H * 8, g_gvw },
        { GV_DIR "/gv_snap_120x68_rec40.bin", GVO_SNAP, GV_NB * 40, g_gvo } };
    for (int i = 0; i < 3; i++) {
        char tmp[96]; snprintf(tmp, sizeof(tmp), "%s.tmp", fl[i].n);
        FILE *f = fopen(tmp, "wb");
        if (f) { fwrite(fl[i].base + fl[i].off, 1, fl[i].sz, f); fflush(f); fclose(f); rename(tmp, fl[i].n); }
    }
    lg("GV dump written: gv_vel_480x270_f32x4.bin, gv_depth_480x270_f32x2.bin, gv_snap_120x68_rec40.bin (tools/gv_view.py)\n");
    g_freeze = 0; phase = 0;
    S.letters_seen |= GVL_DUMP;
}

void gv_io_tick(void)
{
    if (!g_live) return;
    uint32_t L = g_letters;
    uint64_t now = sceKernelGetProcessTime();
    if (!(L & GVL_ON)) {   // 'v' / game_vectors=0 at runtime: everything off (memory stays reserved)
        if (S.cfg.vel_on || S.cfg.depth_on || S.st != ST_OFF) {
            S.depth_ok = 0; S.st = ST_OFF; cfg_for(NULL, NULL, 0);
            lg("GV off (game_vectors=0 / letter v removed)\n");
        }
        return;
    }
    if (S.st == ST_OFF) {
        S.st = ST_SCAN;
        S.t_log = 0;
    }
    if (!S.t_log) {
        S.t_log = now;
        {   // merged build: optional /data/bb_probe/gvbias "BIAS_G BIAS_Z" (gv8 bonuses, SAD units, 0..1023; default 48 96)
            int fd = sceKernelOpen(GV_DIR "/gvbias", 0, 0);
            if (fd >= 0) {
                char b[32] = { 0 };
                long n = (long)sceKernelRead(fd, b, 31);
                sceKernelClose(fd);
                unsigned bg = 0, bz = 0;
                if (n > 0 && sscanf(b, "%u %u", &bg, &bz) == 2 && bg <= 1023 && bz <= 1023) {
                    S.cfg.biasg = bg; S.cfg.biasz = bz; cfg_publish(&S.cfg);
                    lg("GV biases from gvbias: BIAS_G %u BIAS_Z %u\n", bg, bz);
                } else lg("GV gvbias file ignored (want two numbers 0..1023)\n");
            }
        }
        if (!(L & GVL_RECAL) && prof_read()) {
            S.have_prof = 1;
            S.st = ST_TRIAL;
            char nm[80]; cand_name(&S.prof_cand, nm, sizeof(nm));
            lg("GV profile loaded: %s, a %.5g %.5g b %.4g %.4g, sentinel %d: validating (hybrid off until 3 checks pass)\n", nm,
               S.prof.ax, S.prof.ay, S.prof.bx, S.prof.by, S.prof.sentinel);
            S.t_cand = now;
            S.cand_frame0 = R.frames;
            cfg_for(&S.prof_cand, &S.prof, 0);
            g_scan_state = 1;   // one scan anyway: depth target + the table for the log
        } else g_scan_state = 1;
    }
    if ((L & GVL_RECAL) && !(S.letters_seen & GVL_RECAL)) {
        S.st = ST_SCAN; S.nrej = 0; S.rescans = 0; S.have_prof = 0;
        cfg_for(NULL, NULL, 0); g_scan_state = 1;
        lg("GV recalibrate requested (r)\n");
    }
    if (L & GVL_RECAL) S.letters_seen |= GVL_RECAL; else S.letters_seen &= ~GVL_RECAL;
    if ((L & GVL_SCAN) && !(S.letters_seen & GVL_SCAN) && g_scan_state == 0) {
        if (S.st == ST_NONE) { S.st = ST_SCAN; S.nrej = 0; }
        g_scan_state = 1; lg("GV scan requested (s)\n");
    }
    if (L & GVL_SCAN) S.letters_seen |= GVL_SCAN; else S.letters_seen &= ~GVL_SCAN;
    if ((L & GVL_DUMP) && !(S.letters_seen & GVL_DUMP)) do_dump();
    if (!(L & GVL_DUMP)) S.letters_seen &= ~GVL_DUMP;
    if (g_scan_state == 2) { g_scan_state = 0; process_scan(); }   // clear first: process_scan() may re-arm the retry
    process_validation();
    // emergency shadow switch from the per-frame gv8 statistics (hysteresis: 60 bad frames off, 60 good frames on)
    if (!S.shadow && S.emerg_bad >= 60) { S.shadow = 1; lg("GV: game vectors disagree with the image in 60 frames: shadow mode (flow only)\n"); }
    if (S.shadow && S.emerg_good >= 60) { S.shadow = 0; lg("GV: agreement back: hybrid on again\n"); }
    if (now - S.t_log > 4000000) { S.t_log = now; log_status(); }
    // per-frame tracking stopped by the watchdog: if the target always sat at the same address (or scan found a non-aliased base), copy it there
    if (R.wd_track_off && S.cfg.vel_on && !S.cfg.cap_fixed &&
        ((R.addr_same >= 8 && R.last_addr) || S.cfg.vscan_addr != 0)) {
        S.cfg.cap_fixed = 1;
        S.cfg.vfix = (R.addr_same >= 8 && R.last_addr) ? R.last_addr : S.cfg.vscan_addr;
        S.cfg.vfw = R.vw ? R.vw : S.cfg.vscan_w;
        S.cfg.vfh = R.vh ? R.vh : S.cfg.vscan_h;
        cfg_publish(&S.cfg);
        lg("GV: tracking off, using fixed end-of-frame copy at address %010llx (%ux%u)\n",
           (unsigned long long)S.cfg.vfix, S.cfg.vfw, S.cfg.vfh);
    }
    // nothing captured for 10 s (target not written, aliased and not cuttable, unreadable memory): give up on it
    if ((S.st == ST_CAL || S.st == ST_TRIAL) && S.snaps == 0 && S.t_cand && now > S.t_cand) {
        if (R.frames < S.cand_frame0 + 15u || (S.st == ST_TRIAL && S.last_scan_draws < 64u)) {
            S.t_cand = now;   // loading screen / menu / no frames rendered yet: hold timeout clock
            if (S.st == ST_TRIAL && S.last_scan_draws < 64u && g_scan_state == 0 && R.frames > S.last_scan_frame + 180u)
                g_scan_state = 1;
        } else if (now - S.t_cand > 10000000u) {
            if (S.st == ST_CAL) {
                cand_t *c = &S.cand[S.cur];
                c->state = -2;
                snprintf(c->why, sizeof(c->why), "never captured in 10 s (not written %u / uncuttable %u in the last window)", R.not_written, R.uncuttable);
                lg("GV rejected candidate %d: %s\n", S.cur + 1, c->why);
                next_candidate();
            } else {
                lg("GV profile target never captured in 10 s: scanning\n");
                S.st = ST_SCAN; cfg_for(NULL, NULL, 0); g_scan_state = 1; S.t_cand = 0;
            }
        }
    }
    switch (S.st) {
    case ST_CAL:
    case ST_TRIAL:
    case ST_ACTIVE: {
        // snapshots: every ~4th frame while calibrating / validating, every ~2 s when active
        uint64_t every = S.st == ST_ACTIVE ? 2000000u : 130000u;
        if (g_snap_state == 0 && now - S.t_req > every) { S.t_req = now; g_snap_state = 1; }
        if (S.st != ST_ACTIVE && g_val_state == 0 && (S.snaps % 16u) == 8u) g_val_state = 1;
        if (!snap_ready()) {
            if (S.st == ST_CAL && R.frames > 0 && S.snaps == 0 && now - S.t_req < every) {}
            break;
        }
        S.snaps++;
        if (S.st == ST_CAL && (g_letters & GVL_VIZ) && (S.snaps % 8u) == 1u) view_norm(g_snapcopy);
        if (S.st == ST_CAL) {
            gvcal_frame_t fo;
            gvcal_set_res(&S.cal, R.vw ? R.vw : S.cand[S.cur].d.pitch, R.vh ? R.vh : S.cand[S.cur].d.alloc_h);
            int inf = gvcal_add(&S.cal, g_snapcopy, &fo);
            S.snaps_inf += (uint32_t)inf;
            int d = gvcal_decide(&S.cal, &S.res);
            if ((S.snaps % 8u) == 1u || d)
                lg("GV cal #%u: rel %u/%u textured, inf %d (total %u), frame a %.4g/%.4g R2 %.2f/%.2f prevR2 %.2f/%.2f "
                   "mode %.0f%% mean %.1f,%.1f std %.1f | %s\n", S.snaps, fo.n_rel, fo.n_textured, inf, S.snaps_inf,
                   fo.a[0], fo.a[1], fo.r2[0], fo.r2[1], fo.r2_prev[0], fo.r2_prev[1], fo.mode_share * 100.0f, fo.mean_fx,
                   fo.mean_fy, fo.std_f, S.res.reason);
            cand_t *c = &S.cand[S.cur];
            if (d == 1) {
                c->state = 1;
                const char *cap = S.val_bad ? "track-splice" : "track";
                lg("GV ACCEPTED: a %.5g %.5g b %.4g %.4g R2 %.3f %.3f jitter %.2f %.2f px, units %s, sentinel %d (0x%08x), "
                   "validation %u same / %u different\n", S.res.ax, S.res.ay, S.res.bx, S.res.by, S.res.r2x, S.res.r2y,
                   S.res.jit_x, S.res.jit_y, S.res.units, S.res.sentinel, S.res.sent_x, S.val_ok, S.val_bad);
                prof_write("accepted", c, &S.res, cap, "calibrated");
                S.prof = S.res; S.prof_cand = *c; S.have_prof = 1;
                S.st = ST_ACTIVE; S.fails = 0;
                cfg_for(c, &S.res, 1);
            } else if (d == -1 || S.snaps > 120) {
                c->state = d == -1 ? (S.res.soft ? -2 : -1) : -2;
                snprintf(c->why, sizeof(c->why), "%s", d == -1 ? S.res.reason : "timeout (not enough motion)");
                lg("GV rejected candidate %d: %s\n", S.cur + 1, c->why);
                next_candidate();
            }
        } else {
            float frac; uint32_t nrel;
            int ok = gvcal_check(&S.prof, g_snapcopy, &frac, &nrel);
            if (ok >= 0 || (S.snaps % 16u) == 0)
                lg("GV check (%s): %s, %.0f%% of %u reliable blocks predicted (pass %u fail %u)\n", st_name[S.st],
                   ok == 1 ? "pass" : ok == 0 ? "FAIL" : "not informative", frac * 100.0f, nrel, S.passes, S.fails);
            if (ok == 1) { S.passes++; S.fails = 0; }
            if (ok == 0) S.fails++;
            if (S.st == ST_TRIAL && S.passes >= 3) {
                lg("GV profile confirmed: hybrid ON\n");
                S.st = ST_ACTIVE; S.fails = 0;
                cfg_for(&S.prof_cand, &S.prof, 1);
            } else if (S.fails >= 3) {
                lg("GV profile does not fit any more (3 failed checks): hybrid OFF, calibrating again\n");
                S.have_prof = 1; S.st = ST_SCAN; S.passes = S.fails = 0;
                cfg_for(NULL, NULL, 0);
                g_scan_state = 1;
            }
        }
        break;
    }
    case ST_NONE:
        if (S.last_scan_draws < 64u && R.frames > S.last_scan_frame + 180u) {
            S.st = ST_SCAN; g_scan_state = 1;
            lg("GV re-scan after intro/loading screen (prev scan had %u draws)\n", S.last_scan_draws);
        } else if (R.frames > S.last_scan_frame + 3600u && S.rescans < 3) {   // the scene may change (menus, cutscenes)
            S.rescans++;
            for (int i = 0; i < S.ncand; i++) if (S.cand[i].state == -2) S.cand[i].state = 0;   // soft rejections again
            S.st = ST_SCAN; g_scan_state = 1;
            lg("GV re-scan %u/3\n", S.rescans);
        }
        break;
    default:
        break;
    }
}
