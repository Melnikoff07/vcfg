// bb_probe: 30 -> 60 Hz frame generation for PS4 games (GoldHEN plugin).
// Mode from the first byte of /data/bb_probe/inject (0 off .. 7 = old flow+warp, 8 = new pipeline, see build_dcb_flow8;
// v1.16:
//  - 1.1 FG Presets: Quality (strict 2x with real-timestamp midpoint phase & even hold pacing) vs Fast (aggressive 60 Hz
//        target for 20..40 fps games via fractional vblank pulldown scheduler, 1-2 mid frames at fractional t1/t2,
//        3-sample median jitter filter, queue backpressure guard, scene-cut & >66 ms repeat guard) vs Sharp (2x Catmull-Rom).
//  - 1.2 True Enhance AA: 12x12 LDS-tiled edge AA L/Z-span + directional AA directional anti-aliasing + texture gate +
//        edge-gated RCAS (Off / Light / Strong) in rcas8.ll, zero in-place neighbour race, zero 30 Hz flicker.
//  - 1.3 Corner FPS Overlay: Off / Small / Detailed with selectable corner (TL / TR / BR / BL), lock-free atomic
//        real game FPS (0.5 s window), output FPS (actual flips/s), frame time (ms) and GPU time (ms).
//  - 1.4 Universal Game Support: CUSA process filter (protects SceShellUI / Remote Play host), 2..8 display buffers,
//        auto-detection of 60 fps games (auto-disables FG), post-only native pass (build_dcb_post8) so L3+R3 menu,
//        FPS overlay and Enhance AA work even when FG is off or inert (720p/900p/60fps), periodic GOT pad patching.
#include <Common.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include "plugin_common.h"
#include "shaders.h"
static uint32_t g_hud_packed = 0;      // active HUD rectangle to copy from the real frame (8 px units)
static uint32_t g_hud_file_packed = 0; // HUD rectangle loaded from /data/bb_probe/hud_<TITLEID>
static volatile int g_opt_nofgk = 0;  // inject letter g: keep the static weight under foreground splats (A/B)
static volatile int g_opt_noanc = 0; // 8.5 inject letter L: fgs8 anchors off (head split fix A/B)
static volatile int g_opt_notp = 0;  // 8.5 inject letter T: me1 temporal candidates off (diagnostic)
static volatile int g_opt_nomf = 0;  // 8.5 inject letter M: mf8 field filter bypassed (diagnostic)
static volatile int g_opt_notex = 0; // 8.6 inject letter G: fgs8 texture gate off (A/B)
static volatile int g_opt_notpg = 0; // 8.6 inject letter P: me1 temporal support gate off (A/B)
static volatile int g_opt_sharp = 0; // inject letter S: sharper cubic (C 0.75) in the warp
static volatile int g_opt_nostr = 0; // inject letter b: straddle guard in warp8 off (A/B)
static volatile int g_opt_nocf = 0;  // inject letter k: confidence fallback off
#include "fg8_table.h"
#include "fg_res.h"
#include "gv.h"
#include "vov_glue.h"
#include "menu_combo.h"
#include "title_families.h"

#define PLUGIN_NAME "bb_probe"
#define LOG_DIR "/data/bb_probe"
#define LOG_PATH LOG_DIR "/probe.log"
#define INJECT 1
#define FG8_PAD 0   /* inline hooks on libScePad stay OFF (crash at ~frame 27-30); GOT patch + poll thread used */
#define INJECT_AFTER_FLIPS 120
#define MENU_NROWS 9

typedef struct {
    int framegen;        // 0 = off, 1 = on
    int mode;            // 8 = 8.6 pipeline
    int preset;          // 0 = Quality (2x), 1 = Fast (60 Hz pulldown), 2 = Sharp (2x cubic C=0.75)
    int sharpness;       // 0..100 (RCAS, 0 = off)
    int upscale;         // Enhance AA: 0 = off, 1 = Light AA, 2 = Strong AA
    int upscale_factor;  // 50..100
    int hud_protect;     // 0 = off, 1 = conservative, 2 = aggressive + hud file
    int game_vectors;    // 0 = off (safe default), 1 = on
    int fps_lock;        // 0 = auto, 20, 30, 60
    int menu_hold_ms;    // hold L3+R3 in ms to open menu (default 3000)
    int menu_opacity;    // 0..100 (default 80)
    int overlay;         // 0 = off, 1..2 = TL (Small/Detail), 3..4 = TR, 5..6 = BR, 7..8 = BL
    uint32_t menu_combo; // pad buttons held together to open the menu (menu_combo.h table, default L3+R3)
    int present_copy;    // 0 = auto, 1 = on, 2 = off; profile setting, not an overlay menu row
    int two_buffer;      // 1 = virtual flip queue for a double-buffered game (app engine choice "2-BUFFER"), read at start
    int pace_order;      // 0 = auto (!pcopy -> pre-submit), 1 = force pre-submit, 2 = force post-submit, 3 = timer pacing
    int shave_mode;      // 0 = auto (Valve+Shave), 1 = Valve only (no Shave), 2 = Late alloc (no early alloc at boot)
    int reg_mode;        // 0 = auto (inline extend + auto-reloc), 1 = separate group at top (16-N), 2 = deferred (frame 120)
    int post_mid;        // 0 = auto, 1 = generated mid frames only, 2 = generated + real frames
} fg_config_t;

static char g_title_id[16] = "UNKNOWN";
static int g_known_game = 0;
static int g_is_game_proc = 1;
static volatile fg_config_t g_cfg = { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0, MENU_COMBO_DEFAULT, 0, 0, 0, 0, 0, 0 };
static fg_config_t g_menu_edit    = { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0, MENU_COMBO_DEFAULT, 0, 0, 0, 0, 0, 0 };
static volatile int g_last_pace_pre = 1;
static volatile int g_menu_open = 0, g_menu_sel = 0, g_cfg_save_req = 0;
static volatile uint64_t g_osd_toast_until = 0;
static char g_osd_toast[32] = "READY";
static char g_inert_reason[32] = "";
static volatile int g_detected_60fps = 0;
static volatile int g_fmt_supported = 1;
static volatile uint32_t g_last_gpu_ms_x100 = 0, g_last_hz_x10 = 600;
static volatile uint32_t g_osd_real_fps_x10 = 300, g_osd_out_fps_x10 = 600, g_osd_frame_ms_x10 = 333;
static volatile uint32_t g_cur_pending_flips = 0, g_cur_gcq = 0;
// Latency of real frames: time the game handed frame <buffer> to the flip hook -> time it was on screen.
static volatile uint64_t g_sub_t[16];
static uint64_t g_lat_sum, g_lat_n, g_lat_max;
static volatile uint32_t g_gv_letters_base = 0;
static volatile int g_ab_x_flag = 0;   // inject letter 'x' (developer A/B comparison mode: keep 30 fps pacing when FG off)

attr_public const char *g_pluginName = PLUGIN_NAME;
attr_public const char *g_pluginDesc = "Flip observer + mode 8.6 FG (2x/Fast60) + edge AA/directional AA Enhance + OSD";
attr_public const char *g_pluginAuth = "proyavka";
attr_public u32 g_pluginVersion = 0x00000110;

typedef struct {
    uint32_t format, tilingMode, aspectRatio, width, height, pitchInPixel, option, reserved0;
    uint64_t reserved1;
} VoAttr;

int32_t sceGnmSubmitAndFlipCommandBuffers(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg);
int32_t sceGnmSubmitCommandBuffers(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz);
int32_t sceGnmSubmitCommandBuffersForWorkload(uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz);
int32_t sceGnmSubmitAndFlipCommandBuffersForWorkload(uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[],
                                                     uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg);
int32_t sceVideoOutRegisterBuffers(int32_t handle, int32_t startIndex, void *const *addresses, int32_t bufferNum, const VoAttr *attr);
int32_t sceVideoOutSetFlipRate(int32_t handle, int32_t rate);
int32_t sceKernelAllocateDirectMemory(long searchStart, long searchEnd, size_t length, size_t alignment, int type, long *physAddrOut);
int32_t sceKernelReleaseDirectMemory(long start, size_t len);
int32_t sceKernelMapDirectMemory(void **addr, size_t length, int prot, int flags, long directMemoryStart, size_t maxPageSize);
int32_t sceKernelMapNamedDirectMemory(void **addr, size_t length, int prot, int flags, long directMemoryStart, size_t maxPageSize, const char *name);
size_t sceKernelGetDirectMemorySize(void);
int32_t sceVideoOutSubmitFlip(int32_t handle, int32_t index, uint32_t mode, int64_t arg);
int32_t sceKernelUsleep(uint32_t usec);

typedef struct {
    uint64_t count, processTime, tsc;
    int64_t flipArg;
    uint64_t submitTsc, reserved0;
    int32_t gcQueueNum, flipPendingNum, currentBuffer;
    uint32_t reserved1;
} VoFlipStatus;
typedef struct {
    uint64_t count, processTime, tsc, reserved;
    uint8_t flags, pad[7];
} VoVblankStatus;
int32_t sceVideoOutGetFlipStatus(int32_t handle, VoFlipStatus *st);
int32_t sceVideoOutGetVblankStatus(int32_t handle, VoVblankStatus *st);
int32_t sceGnmInsertWaitFlipDone(uint32_t *cmd, uint32_t numDwords, int32_t videoOutHandle, uint32_t displayBufferIndex);

int32_t scePadReadState(int32_t handle, void *data);
int32_t scePadGetHandle(int32_t userID, uint32_t controller_type, uint32_t controller_index);
int32_t sceUserServiceGetInitialUser(int32_t *userId);
int32_t scePadReadExt(int32_t handle, void *data, int32_t count);
int32_t scePadReadStateExt(int32_t handle, void *data);
int32_t scePadRead(int32_t handle, void *data, int32_t num);

HOOK_INIT(sceGnmSubmitAndFlipCommandBuffers);
HOOK_INIT(sceGnmSubmitCommandBuffers);
HOOK_INIT(sceGnmSubmitCommandBuffersForWorkload);
HOOK_INIT(sceGnmSubmitAndFlipCommandBuffersForWorkload);
HOOK_INIT(sceVideoOutRegisterBuffers);
HOOK_INIT(sceVideoOutSetFlipRate);
HOOK_INIT(sceVideoOutSubmitFlip);
HOOK_INIT(scePadReadState);
HOOK_INIT(scePadRead);

static volatile int g_in_submit = 0;
static volatile uint32_t g_wl_direct[2], g_wl_nested[2], g_vo_submit_flips = 0;
static int g_diag_hooks = 0, g_pad_state_hooked = 0, g_pad_read_hooked = 0;

static FILE *g_log = NULL;
static uint64_t g_freq = 0, g_t0 = 0, g_last = 0, g_win_t = 0, g_fps_win_t = 0;
static uint32_t g_flips = 0, g_win_n = 0, g_fps_win_n = 0;
static double g_win_max = 0.0;

// ---- 1.1: Frame interval & fractional pulldown scheduler state ----
static uint32_t g_dt_ring[3] = { 33366u, 33366u, 33366u };
static uint32_t g_ema_frame_us = 33366u;       // smoothed real frame interval in us
static int32_t  g_fast_acc_q16 = 0;            // Q16.16 fractional vblank accumulator for FG Fast (60 Hz target)
static int32_t  g_q2x_hold_acc = 0;            // Q16.16 sub-vblank hold accumulator for FG Quality (even 2x split)
static volatile uint32_t g_last_real_hold_us = 16683u; // duration previous real frame stayed on screen before mid

static void log_open(void)
{
    static int tried = 0;
    if (tried) return;
    tried = 1;
    sceKernelMkdir(LOG_DIR, 0777);
    // VCFG: the file log is written only while the app's "Debug log" switch is on (LOG_DIR/debug_log exists).
    // One file per game start; the previous one is kept as probe.prev.log for crash reports.
    FILE *on = fopen(LOG_DIR "/debug_log", "r");
    if (!on) return;
    fclose(on);
    remove(LOG_DIR "/probe.prev.log");
    rename(LOG_PATH, LOG_DIR "/probe.prev.log");
    g_log = fopen(LOG_PATH, "w");
}

#define VCFG_ENGINE "classic"
#include "vcfg_trace.h"

static volatile uint32_t g_lgw_lock;   // lg_write may run on the game's thread (sync window) and the io thread at once
static void lg_write(const char *buf)
{
    while (__atomic_exchange_n(&g_lgw_lock, 1u, __ATOMIC_ACQUIRE)) sceKernelUsleep(50);
    klog("[bb_probe] %s", buf);
    vcfg_trace(buf);
    log_open();
    if (g_log) { fputs(buf, g_log); fflush(g_log); }
    __atomic_store_n(&g_lgw_lock, 0u, __ATOMIC_RELEASE);
}

#define LGQ_N 32
static char g_lgq[LGQ_N][384];
static volatile uint32_t g_lgq_w = 0, g_lgq_r = 0, g_lgq_lock = 0, g_lgq_drop = 0;
static volatile int g_lg_async = 0;
// With the debug log on, the first seconds of a game start are written straight to disk on the calling thread (not
// through the queue), so a crash during loading keeps every line. Later lines go through the queue as before.
#define LG_SYNC_US 10000000ull
static uint64_t g_boot_t0;
static int lg_sync_window(void) { return g_log && g_boot_t0 && sceKernelGetProcessTime() - g_boot_t0 < LG_SYNC_US; }
static int lgq_trylock(void)
{
    for (int i = 0; i < 2000; i++)
        if (!__atomic_exchange_n(&g_lgq_lock, 1u, __ATOMIC_ACQUIRE)) return 1;
    return 0;
}
static void lgq_unlock(void) { __atomic_store_n(&g_lgq_lock, 0u, __ATOMIC_RELEASE); }

void lg(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void lg(const char *fmt, ...)
{
    char buf[384];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (g_lg_async && !lg_sync_window()) {
        if (lgq_trylock()) {
            if (g_lgq_w - g_lgq_r < LGQ_N) { memcpy(g_lgq[g_lgq_w % LGQ_N], buf, sizeof(buf)); g_lgq_w++; } else g_lgq_drop++;
            lgq_unlock();
        } else g_lgq_drop++;
        return;
    }
    lg_write(buf);
}

static void lg_drain(void)
{
    char buf[384];
    for (;;) {
        int have = 0;
        if (!lgq_trylock()) return;
        if (g_lgq_r != g_lgq_w) { memcpy(buf, g_lgq[g_lgq_r % LGQ_N], sizeof(buf)); g_lgq_r++; have = 1; }
        lgq_unlock();
        if (!have) return;
        lg_write(buf);
    }
}

// ---- display buffers (from RegisterBuffers) ----
static uint64_t g_buf[16];
static int g_nbuf = 0;
static uint32_t g_bw = 0, g_bh = 0, g_pitch = 0, g_prev_idx = 0xFFFFu, g_prev2_idx = 0xFFFFu;

static const uint32_t cs_code[] = {
    0x8F058804, 0x34000082, 0x7E0202FF, 0xFFFF00FF, 0xE0701000, 0x05000100, 0xBF810000,
    0xBF810000, 0xBF810000, 0xBF810000, 0xBF810000, 0xBF810000, 0xBF810000, 0xBF810000,
};

#define GPU_SZ 0x20000u
#define GPU_SZ_BASE 0x20000u
static uint32_t g_gpu_sz = GPU_SZ_BASE;
#define SLOT_SZ 0x1000u
#define SLOT0 0x1000u
#define DCB8_OFF 0x1C000u
#define DCB8_SZ  0x1000u
static uint8_t *g_gpu = NULL;
static uint8_t *g_work = NULL;
static uint64_t g_slot_cpu[4], g_ts_slot_seen = 0, g_ts_first = 0, g_ts_cpu_first = 0, g_ts_last = 0, g_ts_cpu_last = 0, g_ts_sum = 0;
static uint32_t g_ts_n = 0;
static uint64_t g_praw[12];
static uint64_t g_ts_pass[4], g_pp[10], g_ts_span = 0, g_ts_span_max = 0;
static uint32_t g_ts_span_n = 0;
static uint32_t g_pend_seq = 0, g_pend_slot = 0;
static int g_inject_on = 0;
static int g_gpu_state = 0; // 0 untried, 1 ok, -1 failed

static void *g_pre_gpu = NULL, *g_pre_work = NULL;
#define WORK_SZ   0x220000u
#define WORK_SZ_8 0x190000u
_Static_assert(W8_END <= WORK_SZ_8, "mode 8 work memory layout does not fit WORK_SZ_8");
_Static_assert(WORK_SZ_8 <= WORK_SZ, "WORK_SZ_8 must not exceed WORK_SZ");
_Static_assert(DCB8_OFF + 4 * DCB8_SZ <= GPU_SZ, "mode 8 DCB areas do not fit GPU_SZ");

#define MON_OFF 0xE00u
static volatile uint32_t *g_mon = NULL;
static uint32_t g_sh8_off[17];         // code offsets in g_gpu: 0..9 pyr8..mf8, 10..13 gv8..gvz8, 14 rcas8, 15 osd8, 16 pcopy8
#define STATS_OFF 0x960u
#define CUT_BLOCKS 3264u
static uint32_t g_st_n = 0, g_st_cut_max = 0, g_st_cuts = 0, g_st_fgf = 0, g_st_fades = 0;
static uint64_t g_st_cut_sum = 0, g_st_fg_sum = 0;
static int32_t g_st_gmx = 0, g_st_gmy = 0;
static int g_sh8_ok = 0;
static int g_opt_phase = 0, g_opt_early = 0, g_opt_marker = 0;
static volatile int g_fg_off = 0;
static uint8_t *g_dump_mem = NULL;
static volatile int g_dump_req = 0;
static volatile uint32_t g_dump_frame = 0;
#define DUMP_DONE_OFF 0xE20u
#define DUMP_SZ 0xC10000u
static int g_work_garlic = 0;

static int32_t g_handle = -1;
static VoAttr g_attr;
// Tile mode index of the T# descriptors we build for the game's output surfaces. tilingMode 0 (TILE) has always been index 10 (display 2D thin) and
// works on the base PS4. On a PS4 Pro (Neo mode) our writes (generated frames, overlay) come out in 128 px wide column garbage, so the layout there
// does not match. TEST build: on Neo the index sweeps through the candidates (15 s each, logged) unless tile_sweep=0; tile_idx=N in config-<TITLE>.ini
// pins one index (any console), tile_sweep=1 forces the sweep. Remove once the right Neo mode is known.
static volatile int g_tile_override = -1, g_tile_sweep = -1;   // sweep: -1 auto (Neo only), 0 off, 1 on
static volatile int g_is_neo = -1;
static uint32_t tile_idx_now(void)
{
    uint32_t base = (g_attr.tilingMode == 0) ? 10u : 8u;
    if (g_is_neo < 0) { g_is_neo = sceKernelIsNeoMode() ? 1 : 0; g_t_is_neo = (uint32_t)g_is_neo; }
    if (g_tile_override >= 0) return (uint32_t)g_tile_override;
    int sw = g_tile_sweep < 0 ? 0 : g_tile_sweep;   // default off; L3 + D-pad Left/Right (tile_hotkey) steps the index instead
    if (!sw || g_attr.tilingMode != 0) return base;
    static const uint32_t cand[] = { 10u, 14u, 13u, 9u, 12u, 11u };
    uint64_t sec = sceKernelGetProcessTime() / 1000000ull;
    uint32_t idx = cand[(sec / 15u) % (sizeof(cand) / sizeof(cand[0]))];
    static uint32_t last = 0xFFFFu;
    if (idx != last) { last = idx; lg("tile sweep: idx=%u at process time %llu s (neo=%d)\n", idx, (unsigned long long)sec, (int)g_is_neo); }
    return idx;
}
#define TILE_N 6
static const uint32_t k_tile_cand[TILE_N] = { 10u, 14u, 13u, 9u, 12u, 11u };
static void tile_file(char *p, size_t n) { snprintf(p, n, LOG_DIR "/tile_%s", g_title_id); }
static void tile_load(void)   // the index a tester picked with the hotkey, kept per game
{
    char p[96]; tile_file(p, sizeof(p)); FILE *f = fopen(p, "r"); if (!f) return;
    int v = -1; if (fscanf(f, "%d", &v) == 1 && v >= 0 && v < 32) g_tile_override = v;
    fclose(f); lg("tile index %d loaded from %s\n", v, p);
}
static void tile_hotkey(int dir)   // L3 + D-pad Right / Left while no menu is open: next / previous candidate, saved at once
{
    uint32_t cur = tile_idx_now(); int pos = 0;
    for (int i = 0; i < TILE_N; i++) if (k_tile_cand[i] == cur) pos = i;
    pos = (pos + dir + TILE_N) % TILE_N;
    g_tile_sweep = 0; g_tile_override = (int)k_tile_cand[pos];
    char p[96]; tile_file(p, sizeof(p)); FILE *f = fopen(p, "w"); if (f) { fprintf(f, "%d\n", g_tile_override); fclose(f); }
    snprintf(g_osd_toast, sizeof(g_osd_toast), "TILE %d", g_tile_override);
    g_osd_toast_until = sceKernelGetProcessTime() + 2500000ull;
    lg("tile hotkey: idx=%d (saved to %s)\n", g_tile_override, p);
}
static int g_mbuf_state = 0; // 0 untried, 1 ok, -1 failed
static void *g_mbuf_mem = NULL;
static int g_nmid = 3;
// Present copy (double-buffered games): the real frame is copied into one of two own buffers (g_ngame + 1, + 2) and
// that copy is flipped, so the game's buffer is free right after the copy instead of one refresh later. Mid frames
// use g_ngame. present_copy: 0 auto (games with 2 buffers), 1 on, 2 off.
static int g_pcopy_avail = 0, g_pcopy_k = 2;   // k mids + k copies
static volatile int g_pcopy_on = 0;
// mids: g_ngame .. g_ngame + k - 1, copies: g_ngame + k .. + 2k - 1 (rings, so the buffer the GPU writes has long
// left the screen and WaitFlipDone does not stall the GPU behind a full flip queue)
// g_mbase: index of our first extra display buffer. Verified titles: right after the game's buffers (g_ngame).
// Other titles: a separate VideoOut group at the top of the 16 indices, so a game that registers more buffers
// later (CUSA04841 crashed right after we took index 3) still finds its indices free.
static int g_mbase = 3;
#define IS_MID(b) ((b) >= g_mbase && !(g_pcopy_on && (b) >= g_mbase + g_pcopy_k))
static int g_ngame = 3;
// Highest display index the game itself registered + 1. Most games register one group at index 0; some register the
// same buffers again as further groups (0-1, 2-3, 4-5: seen on a 2-buffer title that then flips any of them).
static int g_game_idx_hi = 0;
static void *g_pre_mem = NULL;
static size_t g_pre_size = 0, g_pre_work_sz = 0;
static long g_pre_gpu_phys = 0, g_pre_work_phys = 0, g_pre_mem_phys = 0, g_dump_phys = 0;

#include "two_buffer_test.h"
#include "bench_state.h"
#include "boot_stage.h"

static void free_dm_block(void **va, long *phys, size_t *sz, const char *tag)
{
    if (!*va || !*sz) return;
    int um = sceKernelMunmap(*va, *sz);
    int rr = um == 0 ? sceKernelReleaseDirectMemory(*phys, *sz) : -1;
    STAGE("memory valve: released %s va=%p phys=0x%lx len=0x%zx unmap=0x%08x release=0x%08x",
          tag, *va, *phys, *sz, (unsigned)um, (unsigned)rr);
    *va = NULL;
    *phys = 0;
    *sz = 0;
}
static void release_early_dump(void)
{
    size_t sz = g_dump_mem ? (size_t)DUMP_SZ : 0;
    void *va = g_dump_mem;
    free_dm_block(&va, &g_dump_phys, &sz, "dump");
    g_dump_mem = NULL;
}
static void release_early_mem(void)
{
    free_dm_block(&g_pre_mem, &g_pre_mem_phys, &g_pre_size, "display");
}
static void release_early_work(void)
{
    free_dm_block(&g_pre_work, &g_pre_work_phys, &g_pre_work_sz, "work");
    g_work_garlic = 0;
}
static void release_early_gpu(void)
{
    size_t sz = g_pre_gpu ? (size_t)g_gpu_sz : 0;
    free_dm_block(&g_pre_gpu, &g_pre_gpu_phys, &sz, "gpu");
}

#include "debug_hooks.h"

// Largest free direct-memory range for an alignment, for the logs: a refused allocation is a refused request (length,
// alignment, type), not proof that no memory is left. OpenOrbis declares this call with by-value outputs, hence the cast.
static size_t dm_avail(size_t align)
{
    return dm_avail_range(0, (long)sceKernelGetDirectMemorySize(), align, NULL);
}

static void *alloc_dm_range(long s, long e, size_t len, size_t align, int type, long *phys_out)
{
    long phys = 0; void *addr = NULL;
    g_own_alloc++;
    int r = sceKernelAllocateDirectMemory(s, e, len, align, type, &phys);
    int stage = r == 0 ? 2 : 1;
    if (r == 0) r = sceKernelMapDirectMemory(&addr, len, 0x33 /*CPU rw + GPU rw*/, 0, phys, align);
    g_own_alloc--;
    if (r != 0 || !addr) {
        if (stage == 2) sceKernelReleaseDirectMemory(phys, len);
        size_t av = dm_avail(align);
        klog("[bb_probe] alloc_dm(0x%zx) failed 0x%08x\n", len, (unsigned)r);
        STAGE("alloc_dm FAILED at %s: len=0x%zx align=0x%zx type=%d range=0x%lx..0x%lx r=0x%08x largest free=0x%zx",
              stage == 1 ? "allocate" : "map", len, align, type, s, e, (unsigned)r, av);
        return NULL;
    }
    if (phys_out) *phys_out = phys;
    STAGE("alloc_dm ok: len=0x%zx align=0x%zx type=%d range=0x%lx..0x%lx phys=0x%lx va=%p", len, align, type, s, e, phys, addr);
    memset(addr, 0, len);
    return addr;
}

void *alloc_dm(size_t len, size_t align, int type);
void *alloc_dm(size_t len, size_t align, int type)
{
    return alloc_dm_range(0, (long)sceKernelGetDirectMemorySize(), len, align, type, NULL);
}

static void *alloc_work_early(size_t wsz, int type)
{
    size_t dm_sz = sceKernelGetDirectMemorySize();
    // 1. If g_pre_gpu sits in a partially used 2MB page whose tail >= wsz (e.g. CUSA00918, CUSA11456), place work
    //    right after g_pre_gpu so it costs zero 2MB-aligned pages.
    if (g_pre_gpu && g_pre_gpu_phys >= 0) {
        long end_gpu = g_pre_gpu_phys + (long)g_gpu_sz;
        long next_2mb = (end_gpu + 0x1FFFFFL) & ~0x1FFFFFL;
        if (next_2mb - end_gpu >= (long)wsz) {
            void *w = alloc_dm_range(end_gpu, next_2mb, wsz, 0x10000, type, &g_pre_work_phys);
            if (w) { g_pre_work_sz = wsz; return w; }
        }
    }
    // 2. Otherwise, if g_pre_mem was placed at the top of the pool (e.g. CUSA17419), place work right below g_pre_mem
    //    at the top so the game's contiguous arena from 0 upward is not fragmented at its start.
    if (g_pre_mem && g_pre_mem_phys >= 0x200000L) {
        void *w = alloc_dm_range(g_pre_mem_phys - 0x200000L, g_pre_mem_phys, wsz, 0x10000, type, &g_pre_work_phys);
        if (w) { g_pre_work_sz = wsz; return w; }
    }
    // 3. Fallback: anywhere in the pool.
    void *w = alloc_dm_range(0, (long)dm_sz, wsz, 0x10000, type, &g_pre_work_phys);
    if (w) g_pre_work_sz = wsz;
    return w;
}

static void gpu_init(void)
{
    STAGE("gpu_init enter pre_gpu=%p pre_work=%p", g_pre_gpu, g_pre_work);
    g_gpu_state = -1;
    {
        char hpath[96] = LOG_DIR "/hud";
        if (g_title_id[0] && strcmp(g_title_id, "UNKNOWN") != 0)
            snprintf(hpath, sizeof(hpath), LOG_DIR "/hud_%s", g_title_id);
        int hfd = sceKernelOpen(hpath, 0 /*O_RDONLY*/, 0);
        lg("hud file: %s (%s)\n", hpath, hfd >= 0 ? "found" : "none");
        g_hud_file_packed = 0;
        if (hfd >= 0) {
            char hb[64] = {0};
            long hn = (long)sceKernelRead(hfd, hb, 63);
            sceKernelClose(hfd);
            unsigned a = 0, b = 0, c = 0, d = 0;
            int hcn = (hn > 0) ? sscanf(hb, "%u %u %u %u", &a, &b, &c, &d) : 0;
            if (hcn == 3 && a <= 2040 && b <= 2040 && c > 0 && c <= 2040) g_hud_file_packed = (a / 8) | ((b / 8) << 8) | ((c / 8) << 16);
            else if (hcn == 4 && c > a && d > b && c <= 2040 && d <= 2040)
                g_hud_file_packed = (a / 8) | ((b / 8) << 8) | (((c + 7) / 8) << 16) | (((d + 7) / 8) << 24);
            lg("hud rectangle: %u %u %u %u -> packed 0x%08x\n", a, b, c, d, g_hud_file_packed);
        }
        g_hud_packed = (g_cfg.hud_protect >= 1) ? g_hud_file_packed : 0u;
    }
    if (!g_pre_gpu) g_gpu_sz = gv_armed() ? GPU_SZ : GPU_SZ_BASE;
    void *addr = g_pre_gpu ? g_pre_gpu : alloc_dm(g_gpu_sz, g_gpu_sz, 0 /*WB_ONION*/);
    if (!addr) { STAGE("gpu_init FAILED: no GPU memory (0x%zx) -> native", (size_t)g_gpu_sz); return; }
    memset(addr, 0, g_gpu_sz);
    memcpy(addr, cs_code, sizeof(cs_code));
    memcpy((uint8_t *)addr + 0x100, ghost_code, sizeof(ghost_code));
    memcpy((uint8_t *)addr + 0x400, rect_code, sizeof(rect_code));
    memcpy((uint8_t *)addr + FONT8_OFF, g_font8x8, sizeof(g_font8x8));
    memcpy((uint8_t *)addr + 0x5000, luma_code, sizeof(luma_code));
    memcpy((uint8_t *)addr + 0x5400, luma4_code, sizeof(luma4_code));
    memcpy((uint8_t *)addr + 0x5800, coarse1_code, sizeof(coarse1_code));
    memcpy((uint8_t *)addr + 0x7800, coarse2_code, sizeof(coarse2_code));
    memcpy((uint8_t *)addr + 0x5C00, flow1_code, sizeof(flow1_code));
    memcpy((uint8_t *)addr + 0x6000, flow2_code, sizeof(flow2_code));
    memcpy((uint8_t *)addr + 0x6800, warp_code, sizeof(warp_code));
    {
        const uint32_t *code[17] = { pyr8_code, me3_code, me2_code, me1_code, phase8_code, warp8_code, dump8_code, gm8_code,
                                     fgs8_code, mf8_code, gv8_code, gvc8_code, warp8g_code, gvz8_code, rcas8_code, osd8_code,
                                     pcopy8_code };
        const uint32_t nw[17] = { PYR8_NWORDS, ME3_NWORDS, ME2_NWORDS, ME1_NWORDS, PHASE8_NWORDS, WARP8_NWORDS, DUMP8_NWORDS,
                                  GM8_NWORDS, FGS8_NWORDS, MF8_NWORDS, GV8_NWORDS, GVC8_NWORDS, WARP8G_NWORDS, GVZ8_NWORDS,
                                  RCAS8_NWORDS, OSD8_NWORDS, PCOPY8_NWORDS };
        uint32_t off = 0x8000;
        g_sh8_ok = 1;
        for (int i = 0; i < 10; i++) {
            if (off + nw[i] * 4 > DCB8_OFF) { g_sh8_ok = 0; break; }
            memcpy((uint8_t *)addr + off, code[i], nw[i] * 4);
            g_sh8_off[i] = off;
            off = (off + nw[i] * 4 + 0xFFu) & ~0xFFu;
        }
        for (int i = 14; g_sh8_ok && i < 17; i++) {
            if (off + nw[i] * 4 > DCB8_OFF) { g_sh8_ok = 0; break; }
            memcpy((uint8_t *)addr + off, code[i], nw[i] * 4);
            g_sh8_off[i] = off;
            off = (off + nw[i] * 4 + 0xFFu) & ~0xFFu;
        }
        lg("gpu_init: mode 8 + rcas8/osd8 shaders %s, end 0x%x\n", g_sh8_ok ? "ok" : "DO NOT FIT", off);
#if VCFG_BENCH
        if (g_bench_enabled && g_sh8_ok) {
            if (off + sizeof(pack16_code) > DCB8_OFF) g_sh8_ok = 0;
            else { memcpy((uint8_t *)addr + off, pack16_code, sizeof(pack16_code)); g_bench_pack_off = off; }
        }
#endif
        if (g_sh8_ok && gv_armed() && g_gpu_sz == GPU_SZ) {
            int vok = 1;
            for (int i = 10; i < 14; i++) {
                if (off + nw[i] * 4 > DCB8_OFF) { vok = 0; break; }
                memcpy((uint8_t *)addr + off, code[i], nw[i] * 4);
                g_sh8_off[i] = off;
                off = (off + nw[i] * 4 + 0xFFu) & ~0xFFu;
            }
            lg("gpu_init: velocity shaders %s, end 0x%x (limit 0x%x)\n", vok ? "ok" : "DO NOT FIT: velocity off", off, DCB8_OFF);
            if (vok) {
                gv_rsrc_gv8[0] = GV8_RSRC1; gv_rsrc_gv8[1] = GV8_RSRC2;
                gv_rsrc_gvc8[0] = GVC8_RSRC1; gv_rsrc_gvc8[1] = GVC8_RSRC2;
                gv_rsrc_warp8g[0] = WARP8G_RSRC1; gv_rsrc_warp8g[1] = WARP8G_RSRC2;
                gv_set_shaders((uint8_t *)addr, g_sh8_off[10], g_sh8_off[11], g_sh8_off[12], g_sh8_off[13]);
            }
        }
    }
    g_work = (uint8_t *)(g_pre_work ? g_pre_work : alloc_dm(g_inject_on == 8 ? WORK_SZ_8 : WORK_SZ, 0x10000, 0));
    if (!g_work) lg("gpu_init: work memory unavailable, mode 6 disabled\n");
    g_mon = (volatile uint32_t *)((uint8_t *)addr + MON_OFF);
    g_gpu = (uint8_t *)addr;
    g_gpu_state = 1;
    STAGE("gpu_init ok at %p work %p", addr, (void *)g_work);
    lg("gpu_init: ok at %p (%s), work %p\n", addr, g_pre_gpu ? "early" : "late", (void *)g_work);
}

#define PKT3(op, nbody, st) (0xC0000000u | (((uint32_t)(nbody) - 1u) << 16) | ((uint32_t)(op) << 8) | ((uint32_t)(st) << 1))

static uint32_t build_dcb(uint32_t *p0, uint64_t out_addr, uint32_t groups, uint32_t nrec, uint32_t slot, uint32_t seq, const uint32_t *nop64)
{
    uint32_t *p = p0;
    uint64_t pgm = (uint64_t)(uintptr_t)g_gpu;
    uint64_t lbl = (uint64_t)(uintptr_t)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900);
    uint32_t *vs = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x800);
    vs[0] = (uint32_t)out_addr;
    vs[1] = (uint32_t)(out_addr >> 32) & 0xFFFFu;
    vs[2] = nrec;
    vs[3] = 0x00027FAC;
    *(volatile uint64_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900) = 0;

    *p++ = PKT3(0x76, 3, 1); *p++ = 0x20C; *p++ = (uint32_t)(pgm >> 8); *p++ = (uint32_t)(pgm >> 40);
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x212; *p++ = 0x002C0080; *p++ = 0x00000088;
    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x76, 5, 1); *p++ = 0x240; *p++ = vs[0]; *p++ = vs[1]; *p++ = vs[2]; *p++ = vs[3];
    *p++ = PKT3(0x15, 4, 1); *p++ = groups; *p++ = 1; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl; *p++ = 0x40000000u | ((uint32_t)(lbl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl; *p++ = (uint32_t)(lbl >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    for (int i = 0; i < 64; i++) *p++ = nop64[i];
    return (uint32_t)(p - p0);
}

static uint32_t build_dcb_ghost(uint32_t *p0, uint64_t prev, uint64_t cur, uint64_t out, uint32_t groups, uint32_t nbytes, uint32_t slot, uint32_t seq, const uint32_t *nop64)
{
    uint32_t *p = p0;
    uint64_t pgm = (uint64_t)(uintptr_t)g_gpu + 0x100;
    uint64_t lbl = (uint64_t)(uintptr_t)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900);
    uint32_t vs[12];
    uint64_t base[3] = { prev, cur, out };
    for (int i = 0; i < 3; i++) {
        vs[i * 4 + 0] = (uint32_t)base[i];
        vs[i * 4 + 1] = (uint32_t)(base[i] >> 32) & 0xFFFFu;
        vs[i * 4 + 2] = nbytes;
        vs[i * 4 + 3] = 0x00027FAC;
    }
    *(volatile uint64_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900) = 0;
    {
        uint64_t lbl2 = lbl + 8;
        *(volatile uint64_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x908) = 0;
        *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
        *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl2; *p++ = 0x40000000u | ((uint32_t)(lbl2 >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
        *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl2; *p++ = (uint32_t)(lbl2 >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x82003FC0; *p++ = (nbytes >> 8); *p++ = 0; *p++ = (uint32_t)(cur >> 8); *p++ = (uint32_t)(cur >> 40); *p++ = 0x0A;
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    }
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x20C; *p++ = (uint32_t)(pgm >> 8); *p++ = (uint32_t)(pgm >> 40);
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x212; *p++ = GHOST_RSRC1; *p++ = GHOST_RSRC2;
    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x76, 13, 1); *p++ = 0x240; for (int i = 0; i < 12; i++) *p++ = vs[i];
    *p++ = PKT3(0x15, 4, 1); *p++ = groups; *p++ = 1; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl; *p++ = 0x40000000u | ((uint32_t)(lbl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl; *p++ = (uint32_t)(lbl >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    for (int i = 0; i < 64; i++) *p++ = nop64[i];
    return (uint32_t)(p - p0);
}

static uint32_t build_dcb_rect(uint32_t *p0, uint64_t surf, uint32_t w, uint32_t h, uint32_t pitch, uint32_t tile_idx,
                               uint32_t gx, uint32_t gy, uint32_t nbytes, uint32_t slot, uint32_t seq, const uint32_t *nop64)
{
    uint32_t *p = p0;
    uint64_t pgm = (uint64_t)(uintptr_t)g_gpu + 0x400;
    uint64_t lbl = (uint64_t)(uintptr_t)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900);
    uint64_t lbl2 = lbl + 8;
    uint32_t t[8];
    make_t_ex(t, surf, w, h, pitch, tile_idx, g_is_neo > 0 ? 1 : 0);
    *(volatile uint64_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900) = 0;
    *(volatile uint64_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x908) = 0;
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl2; *p++ = 0x40000000u | ((uint32_t)(lbl2 >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl2; *p++ = (uint32_t)(lbl2 >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x82003FC0; *p++ = (nbytes >> 8); *p++ = 0; *p++ = (uint32_t)(surf >> 8); *p++ = (uint32_t)(surf >> 40); *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x20C; *p++ = (uint32_t)(pgm >> 8); *p++ = (uint32_t)(pgm >> 40);
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x212; *p++ = RECT_RSRC1; *p++ = RECT_RSRC2;
    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x76, 9, 1); *p++ = 0x240; for (int i = 0; i < 8; i++) *p++ = t[i];
    *p++ = PKT3(0x15, 4, 1); *p++ = gx; *p++ = gy; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl; *p++ = 0x40000000u | ((uint32_t)(lbl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl; *p++ = (uint32_t)(lbl >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    for (int i = 0; i < 64; i++) *p++ = nop64[i];
    return (uint32_t)(p - p0);
}

static int g_fence_logged = 0;
static uint32_t build_dcb_flow(uint32_t *p0, uint64_t P, uint64_t C, uint64_t M, uint32_t w, uint32_t h, uint32_t pitch, uint32_t tidx,
                               uint32_t nbytes, uint32_t slot, uint32_t seq, const uint32_t *nop64,
                               int32_t vo_handle, uint32_t mid_idx, int fence)
{
    uint32_t *p = p0;
    uint32_t r = seq & 1;
    uint64_t lumaCur = (uint64_t)(uintptr_t)(g_work + (r ? 0xE1000 : 0));
    uint64_t lumaPrev = (uint64_t)(uintptr_t)(g_work + (r ? 0 : 0xE1000));
    uint64_t candS = (uint64_t)(uintptr_t)(g_work + 0x1C2000);
    uint64_t candK = (uint64_t)(uintptr_t)(g_work + 0x1DE200);
    uint64_t mv = (uint64_t)(uintptr_t)(g_work + 0x1FA400);
    uint64_t l4Cur = (uint64_t)(uintptr_t)(g_work + (r ? 0x20E100 : 0x200000));
    uint64_t l4Prev = (uint64_t)(uintptr_t)(g_work + (r ? 0x200000 : 0x20E100));
    uint64_t coarse = (uint64_t)(uintptr_t)(g_work + 0x21C200);
    uint8_t *slotb = g_gpu + SLOT0 + slot * SLOT_SZ;
    uint32_t *tb = (uint32_t *)(slotb + 0xB00);
    uint64_t tbl = (uint64_t)(uintptr_t)tb;
    uint64_t lbl = (uint64_t)(uintptr_t)(slotb + 0x900), lbl2 = lbl + 8;
    uint64_t gb = (uint64_t)(uintptr_t)g_gpu;
    make_t(tb + 0, P, w, h, pitch, tidx);
    make_t(tb + 8, C, w, h, pitch, tidx);
    make_t(tb + 16, M, w, h, pitch, tidx);
    make_v(tb + 24, lumaCur, 640 * 360 * 4);
    make_v(tb + 28, lumaPrev, 640 * 360 * 4);
    make_v(tb + 32, mv, 3600 * 4);
    make_v(tb + 36, candS, 3600 * 8 * 4);
    make_v(tb + 40, candK, 3600 * 8 * 4);
    make_v(tb + 44, coarse, 240 * 4);
    make_v(tb + 52, l4Cur, 160 * 90 * 4);
    make_v(tb + 56, l4Prev, 160 * 90 * 4);
    { uint32_t sx_ = w / 640; tb[48] = sx_; tb[49] = w; tb[50] = h; tb[51] = sx_ * 8; }
    *(volatile uint64_t *)(slotb + 0x900) = 0;
    *(volatile uint64_t *)(slotb + 0x908) = 0;
    for (int i = 0; i < 5; i++) *(volatile uint64_t *)(slotb + 0x910 + i * 8) = 0;
    for (int i = 0; i < 8; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;
#define TS_EOP(i) do { uint64_t a_ = (uint64_t)(uintptr_t)(slotb + 0x910 + (i) * 8); \
        *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)a_; *p++ = 0x60000000u | ((uint32_t)(a_ >> 32) & 0xFFFFu); *p++ = 0; *p++ = 0; } while (0)
#define TS2(j) do { uint64_t a2_ = (uint64_t)(uintptr_t)(slotb + 0x980 + (j) * 8); \
        *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)a2_; *p++ = 0x60000000u | ((uint32_t)(a2_ >> 32) & 0xFFFFu); *p++ = 0; *p++ = 0; } while (0)
#define PASS_BARRIER() do { *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407; \
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x80400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A; } while (0)
#define SET_PASS(off, r1, r2) do { uint64_t pg_ = gb + (off); \
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x20C; *p++ = (uint32_t)(pg_ >> 8); *p++ = (uint32_t)(pg_ >> 40); \
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x212; *p++ = (r1); *p++ = (r2); } while (0)
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl2; *p++ = 0x40000000u | ((uint32_t)(lbl2 >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl2; *p++ = (uint32_t)(lbl2 >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x82003FC0; *p++ = (nbytes >> 8); *p++ = 0; *p++ = (uint32_t)(C >> 8); *p++ = (uint32_t)(C >> 40); *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    TS_EOP(0);
    SET_PASS(0x5000, LUMA_RSRC1, LUMA_RSRC2);
    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);
    *p++ = PKT3(0x15, 4, 1); *p++ = 80; *p++ = 45; *p++ = 1; *p++ = 1;
    PASS_BARRIER();
    SET_PASS(0x5400, LUMA4_RSRC1, LUMA4_RSRC2);
    *p++ = PKT3(0x15, 4, 1); *p++ = 20; *p++ = 12; *p++ = 1; *p++ = 1;
    PASS_BARRIER();
    SET_PASS(0x5800, COARSE1_RSRC1, COARSE1_RSRC2);
    *p++ = PKT3(0x15, 4, 1); *p++ = 30; *p++ = 1; *p++ = 1; *p++ = 1;
    PASS_BARRIER();
    SET_PASS(0x7800, COARSE2_RSRC1, COARSE2_RSRC2);
    *p++ = PKT3(0x15, 4, 1); *p++ = 4; *p++ = 1; *p++ = 1; *p++ = 1;
    PASS_BARRIER();
    TS_EOP(1);
    SET_PASS(0x5C00, FLOW1_RSRC1, FLOW1_RSRC2);
    *p++ = PKT3(0x15, 4, 1); *p++ = 450; *p++ = 1; *p++ = 1; *p++ = 1;
    PASS_BARRIER();
    TS_EOP(2);
    SET_PASS(0x6000, FLOW2_RSRC1, FLOW2_RSRC2);
    *p++ = PKT3(0x15, 4, 1); *p++ = 57; *p++ = 1; *p++ = 1; *p++ = 1;
    PASS_BARRIER();
    TS_EOP(3);
    if (fence) {
        int32_t fr = insert_wait_flip_done(p, 7, vo_handle, mid_idx);
        if (fr != 0) { p[0] = PKT3(0x10, 6, 0); for (int i = 1; i < 7; i++) p[i] = 0; }
        if (!g_fence_logged) {
            g_fence_logged = 1;
            lg("fence: sceGnmInsertWaitFlipDone(handle, buf %u) = 0x%08x, packet %08x %08x %08x %08x %08x %08x %08x\n",
               mid_idx, (unsigned)fr, p[0], p[1], p[2], p[3], p[4], p[5], p[6]);
        }
        p += 7;
    }
    SET_PASS(0x6800, WARP_RSRC1, WARP_RSRC2);
    *p++ = PKT3(0x15, 4, 1); *p++ = (w + 7) / 8; *p++ = (h + 7) / 8; *p++ = 1; *p++ = 1;
    TS_EOP(4);
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl; *p++ = 0x40000000u | ((uint32_t)(lbl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl; *p++ = (uint32_t)(lbl >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    for (int i = 0; i < 64; i++) *p++ = nop64[i];
    return (uint32_t)(p - p0);
}

// VCFG overlay (overlay/vov.c): menu panel and corner FPS counter in the launcher design.
// osd8 composites the bitmap; fill_table8_ext points V#palette / V#pixels at g_vov_pal / g_vov_pix.
static const uint8_t *g_vov_pal, *g_vov_pix;
static int g_vov_state; // 0 not tried, 1 ready, -1 no memory
static char g_vov_title[48];

static int vov_ready(void)
{
    if (g_vov_state) return g_vov_state > 0;
    size_t vsz = ((g_mem_tight ? 0x30000u : VOV_MEM_SZ) + 0xFFFFu) & ~(size_t)0xFFFFu;
    void *mem = alloc_dm(vsz, 0x10000, 0 /*WB_ONION*/);
    if (!mem && !g_mem_tight) { vsz = (4u * VOV_BUF_SZ + 0xFFFFu) & ~(size_t)0xFFFFu; mem = alloc_dm(vsz, 0x10000, 0); }
    if (!mem && !g_mem_tight) { vsz = (2u * VOV_BUF_SZ + 0xFFFFu) & ~(size_t)0xFFFFu; mem = alloc_dm(vsz, 0x10000, 0); }   // fewer bitmaps, fits tight-memory games
    if (!mem && !g_mem_tight) { vsz = 0x30000u; mem = alloc_dm(vsz, 0x10000, 0); }
    if (!mem) { g_vov_state = -1; lg("overlay: no memory, menu disabled\n"); return 0; }
    vov_init_size((uint8_t *)mem, vsz);
    vov_read_title(g_vov_title, sizeof(g_vov_title));
    if (!g_vov_title[0]) vov_cpy(g_vov_title, g_title_id, sizeof(g_vov_title));
    lg("overlay: ready at %p, %u KB (%u bitmaps), title '%s'\n", mem, (unsigned)(vsz >> 10), (unsigned)(vsz >= 2u * VOV_BUF_SZ ? (vsz / VOV_BUF_SZ < VOV_NBUF ? vsz / VOV_BUF_SZ : VOV_NBUF) : 1u), g_vov_title);
    g_vov_state = 1;
    return 1;
}

static const char *overlay_label(int ov)
{
    static const char *names[9] = { "OFF", "SMALL TL", "DETAIL TL", "SMALL TR", "DETAIL TR", "SMALL BR", "DETAIL BR", "SMALL BL", "DETAIL BL" };
    return ov < 0 || ov > 8 ? "OFF" : names[ov];
}

static void vov_row(VovMenu *m, const char *label, int kind, int on, int pct, const char *value)
{
    VovRow *r = &m->rows[m->nrows++];
    vov_cpy(r->label, label, sizeof(r->label)); vov_cpy(r->value, value, sizeof(r->value));
    r->kind = kind; r->on = on; r->pct = pct;
}

// out_flags: 3u = write both mid & cur (when FG is active), 2u = write cur only (native/post-only mode).
static void osd_build_slot(uint8_t *osd_mem, uint32_t *xy_out, uint32_t *wh_out, uint32_t out_flags)
{
    (void)osd_mem;
    *xy_out = 0; *wh_out = 0;
    uint64_t now = sceKernelGetProcessTime();
    int ov = g_cfg.overlay, toast = !g_menu_open && now < g_osd_toast_until;
    if (!g_menu_open && !toast && ov <= 0) return;
    if (!vov_ready()) return;
    int op = g_menu_open ? g_menu_edit.menu_opacity : g_cfg.menu_opacity;
    if (op < 20) op = 20;
    if (op > 100) op = 100;
    uint32_t alpha = (uint32_t)(op * 255 / 100);
    uint32_t rfps = __atomic_load_n(&g_osd_real_fps_x10, __ATOMIC_ACQUIRE);
    uint32_t ofps = __atomic_load_n(&g_osd_out_fps_x10, __ATOMIC_ACQUIRE);
    unsigned w = 0, h = 0;

    if (!g_menu_open && !toast) {   // corner counter
        if (!g_cfg.framegen || g_inert_reason[0]) ofps = rfps;
        VovCounter k = { ofps, (__atomic_load_n(&g_last_gpu_ms_x100, __ATOMIC_ACQUIRE) + 5u) / 10u, (ov & 1) == 0 };
        if (!vov_counter(&k, &g_vov_pal, &g_vov_pix, &w, &h)) return;
        int corner = (ov - 1) / 2;   // 0=TL, 1=TR, 2=BR, 3=BL
        uint32_t sw = g_bw >= 640 ? g_bw : 1920u, sh = g_bh >= 360 ? g_bh : 1080u;
        uint32_t x0 = (corner == 1 || corner == 2) ? (sw > w + 32u ? sw - w - 32u : 0u) : 32u;
        uint32_t y0 = (corner == 2 || corner == 3) ? (sh > h + 32u ? sh - h - 32u : 0u) : 32u;
        *xy_out = (x0 & 0xFFFFu) | ((y0 & 0xFFFFu) << 16);
        *wh_out = vov_wh(w, h, alpha, out_flags);
        return;
    }

    static VovMenu m;
    memset(&m, 0, sizeof(m));
    vov_cpy(m.title, g_vov_title, sizeof(m.title));
    fg_config_t c = toast ? g_cfg : g_menu_edit;
    m.game_fps_x10 = rfps;
    m.out_fps_x10 = (!c.framegen || g_inert_reason[0]) ? rfps : ofps;
    if (toast) {
        vov_cpy(m.status, g_osd_toast, sizeof(m.status));
    } else {
        m.sel = g_menu_sel;
        if (g_inert_reason[0]) vov_cpy(m.status, g_inert_reason, sizeof(m.status));
        vov_row(&m, "Frame generation", VOV_TOGGLE, c.framegen, 0, "");
        vov_row(&m, "Mode", VOV_TEXT, 0, 0, c.preset == 1 ? "Fast" : (c.preset == 2 ? "Sharp" : "Quality"));
        vov_row(&m, "Sharpness", VOV_SEGMENTS, 0, c.sharpness, "");
        vov_row(&m, "Anti-aliasing", VOV_TEXT, 0, 0, c.upscale <= 0 ? "Off" : (c.upscale == 1 ? "Light" : "Strong"));
        vov_row(&m, "FPS counter", VOV_TEXT, 0, 0, overlay_label(c.overlay));
        vov_row(&m, "Game vectors", VOV_TOGGLE, c.game_vectors, 0, "");
        vov_row(&m, "HUD protection", VOV_TEXT, 0, 0, c.hud_protect <= 0 ? "Off" : (c.hud_protect == 1 ? "Conservative" : "Aggressive"));
        char lock[16]; if (c.fps_lock <= 0) vov_cpy(lock, "Auto", sizeof(lock)); else snprintf(lock, sizeof(lock), "%d", c.fps_lock);
        vov_row(&m, "FPS lock", VOV_TEXT, 0, 0, lock);
        vov_row(&m, "Opacity", VOV_SEGMENTS, 0, c.menu_opacity, "");
        vov_cpy(m.hints, "\xe2\x86\x95 \xe2\x86\x90 \xe2\x86\x92  \xc3\x97 APPLY  \xe2\x96\xa1 RESET  \xe2\x96\xb3 SAVE  \xe2\x97\x8b", sizeof(m.hints));
    }
    if (!vov_menu(&m, &g_vov_pal, &g_vov_pix, &w, &h)) return;
    *xy_out = 64u | (64u << 16);
    *wh_out = vov_wh(w, h, alpha, out_flags);
}

static void calc_rcas_params(uint32_t target_mask, uint32_t *con_bits_out, uint32_t *flags_out)
{
    uint32_t rcas_con_bits = 0, rcas_flags = 0;
    int sharp_pct = g_cfg.sharpness;
    int enh_mode = g_cfg.upscale;
    if (enh_mode < 0) enh_mode = 0;
    if (enh_mode > 2) enh_mode = 2;
    if ((sharp_pct > 0 || enh_mode > 0) && g_sh8_off[14]) {
        if (sharp_pct > 100) sharp_pct = 100;
        if (sharp_pct > 0) {
            float stops = (float)(100 - sharp_pct) * 0.02f;
            float con = 0.25f / (1.0f + stops * (0.693147f + stops * 0.240226f));
            if (sharp_pct < 25) con *= (float)sharp_pct * 0.04f;
            memcpy(&rcas_con_bits, &con, 4);
        }
        rcas_flags = (target_mask & 3u) | ((uint32_t)enh_mode << 2);
    }
    *con_bits_out = rcas_con_bits;
    *flags_out = rcas_flags;
}

// 1.4: Native post-processing pass on T#cur only (when FrameGen is OFF or inert due to resolution / 60fps / no mbuf).
// Keeps L3+R3 menu, corner FPS overlay, and Enhance AA + RCAS working in ANY game without locking to 30 fps!
static uint32_t build_dcb_post8(uint32_t *p0, uint64_t C, uint32_t w, uint32_t h, uint32_t pitch, uint32_t tidx,
                                uint32_t nbytes, uint32_t slot, uint32_t seq, const uint32_t *nop64)
{
    uint8_t *slotb = g_gpu + SLOT0 + slot * SLOT_SZ;
    uint32_t *tb = (uint32_t *)(slotb + 0xB00);
    uint64_t tbl = (uint64_t)(uintptr_t)tb;
    uint64_t lbl = (uint64_t)(uintptr_t)(slotb + 0x900), lbl2 = lbl + 8;
    uint64_t gb = (uint64_t)(uintptr_t)g_gpu;
    uint32_t rcas_con_bits = 0, rcas_flags = 0, osd_xy = 0, osd_wh = 0;

    calc_rcas_params(2u /*cur only*/, &rcas_con_bits, &rcas_flags);
    if (g_sh8_off[15]) osd_build_slot(slotb + OSD_SLOT_OFF, &osd_xy, &osd_wh, 2u /*cur only*/);
    if (!rcas_flags && !osd_wh) return 0; // nothing to draw -> zero-touch native pass-through!

    fill_table8_wh(tb, C, C, C, w, h, pitch, tidx, seq & 1u, 0, 0, g_work, g_mon, NULL);
    fill_table8_ext(tb, g_gpu, slotb, rcas_con_bits, rcas_flags, osd_xy, osd_wh);
    *(volatile uint64_t *)(slotb + 0x900) = 0;
    *(volatile uint64_t *)(slotb + 0x908) = 0;

    uint32_t *p = p0;
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x0000002E;
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl2; *p++ = 0x40000000u | ((uint32_t)(lbl2 >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl2; *p++ = (uint32_t)(lbl2 >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x82003FC0; *p++ = (nbytes >> 8); *p++ = 0; *p++ = (uint32_t)(C >> 8); *p++ = (uint32_t)(C >> 40); *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;

    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);

    if (rcas_flags) {
        SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2);
        *p++ = PKT3(0x15, 4, 1); *p++ = (w + 7u) / 8u; *p++ = (h + 7u) / 8u; *p++ = 1; *p++ = 1;
        PASS_BARRIER();
    }
    if (osd_wh) {
        SET_PASS(g_sh8_off[15], OSD8_RSRC1, OSD8_RSRC2);
        *p++ = PKT3(0x15, 4, 1); *p++ = (osd_wh & 0xFFu) * 2u; *p++ = ((osd_wh >> 8) & 0xFFu) * 2u; *p++ = 1; *p++ = 1;
        PASS_BARRIER();
    }

    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl; *p++ = 0x40000000u | ((uint32_t)(lbl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl; *p++ = (uint32_t)(lbl >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    for (int i = 0; i < 64; i++) *p++ = nop64[i];
    return (uint32_t)(p - p0);
}

// Per-title scene-change threshold (blocks of 8160 that must be unmatched for warp8 to copy the real frame instead of
// the generated one): LOG_DIR/cut_blocks_<TITLE> holds the number. No file = the original 3264 (40 %), unchanged.
// Fast camera motion with blur can push the unmatched count past 3264 although the scene did not change.
static uint32_t g_cut_blocks8 = 0;
static int g_cut_blocks_loaded = 0;
static uint32_t cut_blocks8(void)
{
    if (!g_cut_blocks_loaded) {
        g_cut_blocks_loaded = 1;
        char p[96]; snprintf(p, sizeof(p), LOG_DIR "/cut_blocks_%s", g_title_id);
        int fd = sceKernelOpen(p, 0 /*O_RDONLY*/, 0);
        if (fd >= 0) {
            char b[16] = {0};
            long n = (long)sceKernelRead(fd, b, 15);
            sceKernelClose(fd);
            unsigned v = 0;
            if (n > 0 && sscanf(b, "%u", &v) == 1 && v >= 1000u && v <= 8160u) g_cut_blocks8 = v / 8u;
        }
        if (!g_cut_blocks8 && vcfg_in_family(g_title_id, "CUSA17419")) g_cut_blocks8 = 4800u / 8u;
        lg("scene-change threshold: %u blocks (%s)\n", g_cut_blocks8 ? g_cut_blocks8 * 8u : 3264u, g_cut_blocks8 ? p : "default");
    }
    return g_cut_blocks8;
}

// Per-title "post-process the generated frame only": LOG_DIR/post_mid_only_<TITLE> exists -> while frames are generated,
// Sharpness / Enhance AA run on the mid frame(s) only and never on the game's real frame. The real frame is processed
// IN PLACE and that buffer is the next pair's P, so the warp blended a sharpened A with a raw B and then sharpened the mid
// again (A's half twice); the displayed real frames were sharpened too, so the mid stayed softer than its neighbours
// (offline, 12 CUSA00557 triplets at Sharpness 100: mid/real detail 0.79 in-place vs 1.03 mid-only, PSNR +0.73 dB,
// TCE -8 %). In-place also lets a tile read neighbour pixels already sharpened by another workgroup (frame-to-frame
// edge flicker on the 8-px tile grid of the real frame). With Enhance AA on, the real frame still gets Enhance AA, without
// sharpening (pass after rcas8 below). The overlay/menu still go to every frame. No file = unchanged.
static int g_mid_only = -1;
static int post_mid_only(void)
{
    if (g_cfg.post_mid == 1) return 1;
    if (g_cfg.post_mid == 2) return 0;
    if (g_mid_only < 0) {
        char p[96]; snprintf(p, sizeof(p), LOG_DIR "/post_mid_only_%s", g_title_id);
        int fd = sceKernelOpen(p, 0 /*O_RDONLY*/, 0);
        g_mid_only = fd >= 0 ? 1 : 0;
        if (fd >= 0) sceKernelClose(fd);
        if (!g_mid_only && vcfg_in_family(g_title_id, "CUSA04529"))
            g_mid_only = 1;
        lg("post-process target: %s\n", g_mid_only ? "generated frames only (post_mid_only)" : "generated + real (default)");
    }
    return g_mid_only;
}

static uint32_t build_dcb_flow8(uint32_t *p0, uint64_t P, uint64_t C, uint64_t M1, uint64_t M2,
                                uint32_t pitch, uint32_t tidx, uint32_t nbytes, uint32_t slot, uint32_t seq,
                                const uint32_t *nop64, int32_t vo_handle, uint32_t mid1_idx, uint32_t mid2_idx,
                                uint32_t t1_q16, uint32_t t2_q16, int two_mids, int force_repeat,
                                uint64_t copy_dst, uint32_t copy_idx)
{
    uint32_t *p = p0;
    uint32_t r = seq & 1;
    uint8_t *slotb = g_gpu + SLOT0 + slot * SLOT_SZ;
    uint32_t *tb = (uint32_t *)(slotb + 0xB00);
    uint64_t tbl = (uint64_t)(uintptr_t)tb;
    uint64_t lbl = (uint64_t)(uintptr_t)(slotb + 0x900), lbl2 = lbl + 8;
    uint64_t gb = (uint64_t)(uintptr_t)g_gpu;
    volatile uint32_t *stats = (volatile uint32_t *)(slotb + STATS_OFF);
    for (int i = 0; i < 8; i++) stats[i] = 0xFFFFFFFFu;

    uint32_t base_flags = (g_opt_phase ? 1u : 0u) | (((g_fg_off && !g_bench_offscreen) || force_repeat) ? 2u : 0u) | (g_opt_marker ? 4u : 0u)
                        | (g_opt_nocf ? 8u : 0u) | (g_opt_nofgk ? 16u : 0u) | (g_opt_noanc ? 32u : 0u)
                        | (g_opt_notp ? 64u : 0u) | (g_opt_nomf ? 128u : 0u) | (g_opt_notex ? 256u : 0u)
                        | (g_opt_notpg ? 512u : 0u) | (g_opt_sharp ? 1024u : 0u) | (g_opt_nostr ? 2048u : 0u)
                        | (cut_blocks8() << 16);
    uint32_t mid1_packed = (mid1_idx & 0x7FFFu) | ((t1_q16 & 0xFFFFu) << 16);

    fill_table8_wh(tb, P, C, M1, g_bw, g_bh, pitch, tidx, r, mid1_packed, base_flags, g_work, g_mon, stats);

    uint32_t rcas_con_bits = 0, rcas_flags1 = 0, osd_xy = 0, osd_wh1 = 0;
    uint32_t mask1 = two_mids ? 1u /*mid1 only; mid2+cur done in pass 2*/ : 3u /*mid1 + cur*/;
    if (g_bench_offscreen) mask1 = 1u;  // never post-process the game's B/scanout surface during capture
    if (!g_mem_tight || g_cfg.upscale > 0)
        calc_rcas_params((post_mid_only() || g_bench_offscreen) ? 1u /*mid1 only*/ : mask1, &rcas_con_bits, &rcas_flags1);
    if (g_sh8_off[15] && !g_bench_offscreen) osd_build_slot(slotb + OSD_SLOT_OFF, &osd_xy, &osd_wh1, mask1);
    fill_table8_ext(tb, g_gpu, slotb, rcas_con_bits, rcas_flags1, osd_xy, osd_wh1);

    *(volatile uint64_t *)(slotb + 0x900) = 0;
    *(volatile uint64_t *)(slotb + 0x908) = 0;
    for (int i = 0; i < 5; i++) *(volatile uint64_t *)(slotb + 0x910 + i * 8) = 0;
    for (int i = 0; i < 7; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;
#define DISPATCH(x, y) do { *p++ = PKT3(0x15, 4, 1); *p++ = (x); *p++ = (y); *p++ = 1; *p++ = 1; } while (0)
    const FgDim D = fg_dim(g_bw, g_bh);   // 1080p: the literals that stood here (30 x 68, 240 x 135, ...), see fg_res.h
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x0000002E;
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl2; *p++ = 0x40000000u | ((uint32_t)(lbl2 >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl2; *p++ = (uint32_t)(lbl2 >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x82003FC0; *p++ = (nbytes >> 8); *p++ = 0; *p++ = (uint32_t)(C >> 8); *p++ = (uint32_t)(C >> 40); *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    TS_EOP(0);
    p = gv_emit_eof(p, seq, slot);
    gv_fill_table(tb, seq, slot);
    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);
    SET_PASS(g_sh8_off[0], PYR8_RSRC1, PYR8_RSRC2);   DISPATCH(D.px, D.py);
    PASS_BARRIER();
    TS_EOP(1);
    SET_PASS(g_sh8_off[1], ME3_RSRC1, ME3_RSRC2);     DISPATCH(D.m3x, D.m3y);
    PASS_BARRIER(); TS2(0);
    SET_PASS(g_sh8_off[2], ME2_RSRC1, ME2_RSRC2);     DISPATCH(D.m2x, D.m2y);
    PASS_BARRIER(); TS2(1);
    SET_PASS(g_sh8_off[7], GM8_RSRC1, GM8_RSRC2);     DISPATCH(1, 1);
    PASS_BARRIER();
    SET_PASS(g_sh8_off[8], FGS8_RSRC1, FGS8_RSRC2);   DISPATCH(32, 1);
    PASS_BARRIER(); TS2(2);
    SET_PASS(g_sh8_off[3], ME1_RSRC1, ME1_RSRC2);     DISPATCH(D.px, D.py);
    PASS_BARRIER(); TS2(3);
    SET_PASS(g_sh8_off[9], MF8_RSRC1, MF8_RSRC2);     DISPATCH(D.px, D.py);
    PASS_BARRIER();
    if (gv_pass_active()) {
        p = gv_emit_ts(p, slot, 2);
        SET_PASS(g_sh8_off[10], GV8_RSRC1, GV8_RSRC2);  DISPATCH(D.px, D.py);
        PASS_BARRIER();
        p = gv_emit_after_gv8(p, seq, slot);
        p = gv_emit_ts(p, slot, 3);
    }
    TS_EOP(2);
    if (!g_bench_offscreen) {
        int32_t fr = insert_wait_flip_done(p, 7, vo_handle, mid1_idx);
        if (fr != 0) { p[0] = PKT3(0x10, 6, 0); for (int i = 1; i < 7; i++) p[i] = 0; }
        if (!g_fence_logged) { g_fence_logged = 1; lg("fence (mode 8): 0x%08x\n", (unsigned)fr); }
        p += 7;
    }
    TS_EOP(3);
    if (g_opt_phase || t1_q16) {
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    }
    SET_PASS(g_sh8_off[4], PHASE8_RSRC1, PHASE8_RSRC2); DISPATCH(1, 1);
    PASS_BARRIER(); TS2(4);
    if (gv_warp_active()) { SET_PASS(g_sh8_off[12], WARP8G_RSRC1, WARP8G_RSRC2); }
    else { SET_PASS(g_sh8_off[5], WARP8_RSRC1, WARP8_RSRC2); }
    DISPATCH(D.wx, D.wy);
    PASS_BARRIER(); TS2(5);
    if (rcas_flags1) {
        SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2); DISPATCH(D.wx, D.wy);
        PASS_BARRIER();
    }
    // post_mid_only: the real frame gets the profile's Enhance AA WITHOUT sharpening (Enhance off = real untouched), so
    // the aliasing that crawls on edges is smoothed the same way in real and generated frames, and the sharpening only
    // compensates the generated frame's softness. Separate table in the tb2 region (only FG Fast's mid2 uses it; in that
    // mode this pass is skipped). Runs after the warp has read the raw C, before the overlay is drawn.
    if (post_mid_only() && !g_mem_tight && !two_mids && !g_bench_offscreen && g_sh8_off[14]) {
        int enh = g_cfg.upscale < 0 ? 0 : (g_cfg.upscale > 2 ? 2 : g_cfg.upscale);
        if (enh > 0) {
            uint32_t *tbr = (uint32_t *)(slotb + SLOT_TB2_OFF);
            uint64_t tblr = (uint64_t)(uintptr_t)tbr;
            memcpy(tbr, tb, 140 * sizeof(uint32_t));
            fill_table8_ext(tbr, g_gpu, slotb, 0u /*con 0 = no sharpening*/, 2u /*cur*/ | ((uint32_t)enh << 2), 0u, 0u);
            *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A; // inv scalar cache
            *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tblr; *p++ = (uint32_t)(tblr >> 32);
            SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2); DISPATCH(D.wx, D.wy);
            PASS_BARRIER();
            *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
            *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);
        }
    }
    if (gv_viz_active()) {
        PASS_BARRIER();
        SET_PASS(g_sh8_off[13], GVZ8_RSRC1, GVZ8_RSRC2); DISPATCH(D.zx, D.zy);
    }
    if (osd_wh1) {
        PASS_BARRIER();
        SET_PASS(g_sh8_off[15], OSD8_RSRC1, OSD8_RSRC2);
        DISPATCH((osd_wh1 & 0xFFu) * 2u, ((osd_wh1 >> 8) & 0xFFu) * 2u);
    }

    // 1.1 FG Fast 3-step pulldown (two_mids == 1): generate mid2 at fractional phase t2 into M2 reusing F and GK!
    if (two_mids) {
        uint32_t *tb2 = (uint32_t *)(slotb + SLOT_TB2_OFF);
        uint64_t tbl2 = (uint64_t)(uintptr_t)tb2;
        memcpy(tb2, tb, 140 * sizeof(uint32_t));
        make_t(tb2 + 16, M2, g_bw, g_bh, pitch, tidx); // T#out = M2
        tb2[29] = (mid2_idx & 0x7FFFu) | 0x8000u /*skip_st*/ | ((t2_q16 & 0xFFFFu) << 16);
        uint32_t rcas_con2 = 0, rcas_flags2 = 0, osd_wh2 = (osd_wh1 & 0x00FFFFFFu) | (3u << 24);
        calc_rcas_params(post_mid_only() ? 1u /*mid2 only*/ : 3u /*mid2 + cur*/, &rcas_con2, &rcas_flags2);
        fill_table8_ext(tb2, g_gpu, slotb, rcas_con2, rcas_flags2, osd_xy, osd_wh1 ? osd_wh2 : 0u);

        int32_t fr2 = insert_wait_flip_done(p, 7, vo_handle, mid2_idx);
        if (fr2 != 0) { p[0] = PKT3(0x10, 6, 0); for (int i = 1; i < 7; i++) p[i] = 0; }
        p += 7;

        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A; // inv scalar cache
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl2; *p++ = (uint32_t)(tbl2 >> 32);
        SET_PASS(g_sh8_off[4], PHASE8_RSRC1, PHASE8_RSRC2); DISPATCH(1, 1);
        PASS_BARRIER();
        if (gv_warp_active()) { SET_PASS(g_sh8_off[12], WARP8G_RSRC1, WARP8G_RSRC2); }
        else { SET_PASS(g_sh8_off[5], WARP8_RSRC1, WARP8_RSRC2); }
        DISPATCH(D.wx, D.wy);
        PASS_BARRIER();
        if (rcas_flags2) {
            SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2); DISPATCH(D.wx, D.wy);
            PASS_BARRIER();
        }
        if (osd_wh1) {
            PASS_BARRIER();
            SET_PASS(g_sh8_off[15], OSD8_RSRC1, OSD8_RSRC2);
            DISPATCH((osd_wh2 & 0xFFu) * 2u, ((osd_wh2 >> 8) & 0xFFu) * 2u);
        }
    }

    if (g_dump_req && g_dump_mem && !g_bench_offscreen) {
        PASS_BARRIER();
        make_v(tb + 88, (uint64_t)(uintptr_t)g_dump_mem, 3 * 960 * 1080 * 4 + 163200);
        SET_PASS(g_sh8_off[6], DUMP8_RSRC1, DUMP8_RSRC2); DISPATCH(D.dx, D.dy);
        {
            uint64_t dl = (uint64_t)(uintptr_t)(g_gpu + DUMP_DONE_OFF);
            *(volatile uint64_t *)(g_gpu + DUMP_DONE_OFF) = 0;
            *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
            *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)dl; *p++ = 0x40000000u | ((uint32_t)(dl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
        }
        g_dump_req = 0;
        g_dump_frame = seq;
    }
    if (copy_dst && g_sh8_off[16]) {   // present copy: finished real frame -> own display buffer (see g_pcopy_avail)
        uint32_t *tb3 = (uint32_t *)(slotb + OSD_SLOT_OFF);   // old text-grid OSD area, unused since the bitmap overlay
        uint64_t tbl3 = (uint64_t)(uintptr_t)tb3;
        memset(tb3, 0, 0x60);
        make_t(tb3 + 8, C, g_bw, g_bh, pitch, tidx);
        make_t(tb3 + 16, copy_dst, g_bw, g_bh, pitch, tidx);
        int32_t fr3 = insert_wait_flip_done(p, 7, vo_handle, copy_idx);
        if (fr3 != 0) { p[0] = PKT3(0x10, 6, 0); for (int i = 1; i < 7; i++) p[i] = 0; }
        p += 7;
        PASS_BARRIER();
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A; // inv scalar cache
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl3; *p++ = (uint32_t)(tbl3 >> 32);
        SET_PASS(g_sh8_off[16], PCOPY8_RSRC1, PCOPY8_RSRC2); DISPATCH(D.wx, D.wy);
        PASS_BARRIER();
    }
    TS2(6);
    TS_EOP(4);
#undef DISPATCH
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl; *p++ = 0x40000000u | ((uint32_t)(lbl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl; *p++ = (uint32_t)(lbl >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    for (int i = 0; i < 64; i++) *p++ = nop64[i];
    return (uint32_t)(p - p0);
}

// ---- INI config + inject reader / writer ----
static void init_title_id(void)
{
    static int done = 0;
    if (done) return;
    done = 1;
    struct proc_info pi;
    memset(&pi, 0, sizeof(pi));
    if (sys_sdk_proc_info(&pi) == 0 && pi.titleid[0]) {
        int ok = 1;
        for (int i = 0; i < 15 && pi.titleid[i]; i++) {
            char c = pi.titleid[i];
            if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) ok = 0;
        }
        if (ok) { memcpy(g_title_id, pi.titleid, 15); g_title_id[15] = 0; }
    }
    g_known_game = vcfg_is_verified_family(g_title_id);
    // 1.4: Only attach to actual PS4 game titles (CUSAxxxxx); never hook SceShellUI / NPXS* / Remote Play host
    g_is_game_proc = (strncmp(g_title_id, "CUSA", 4) == 0);
}

static uint32_t fnv1a_bytes(uint32_t h, const void *data, size_t n)
{
    const uint8_t *p = (const uint8_t *)data;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 16777619u; }
    return h;
}

static void cfg_defaults(fg_config_t *c)
{
    c->framegen       = (g_known_game && !g_detected_60fps) ? 1 : 0;
    c->mode           = 8;
    c->preset         = 0;
    c->sharpness      = 40;
    c->upscale        = 0;
    c->upscale_factor = 100;
    c->hud_protect    = 1;
    c->game_vectors   = 0;
    c->fps_lock       = 30;
    c->menu_hold_ms   = 3000;
    c->menu_opacity   = 80;
    c->overlay        = 0;
    c->menu_combo     = MENU_COMBO_DEFAULT;
    c->present_copy   = 0;
    c->two_buffer     = 0;
    c->pace_order     = 0;
    c->shave_mode     = 0;
    c->reg_mode       = 0;
    c->post_mid       = 0;
}

static int parse_ini_file(const char *path, fg_config_t *c, uint32_t *hash_io)
{
    int fd = sceKernelOpen(path, 0 /*O_RDONLY*/, 0);
    if (fd < 0) return 0;
    char buf[1024];
    long n = (long)sceKernelRead(fd, buf, sizeof(buf) - 1);
    sceKernelClose(fd);
    if (n <= 0) return 0;
    buf[n] = 0;
    if (hash_io) *hash_io = fnv1a_bytes(*hash_io, buf, (size_t)n);
    char *p = buf;
    while (*p) {
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        if (!*p) break;
        char *eol = p;
        while (*eol && *eol != '\r' && *eol != '\n') eol++;
        char saved = *eol;
        *eol = 0;
        if (*p != ';' && *p != '#' && *p != '[') {
            char key[32] = {0};
            int val = 0;
            if (sscanf(p, "%31[^= \t] = %d", key, &val) == 2 || sscanf(p, "%31[^= \t]=%d", key, &val) == 2) {
                if (strcmp(key, "framegen") == 0) c->framegen = (val != 0);
                else if (strcmp(key, "mode") == 0 && val >= 0 && val <= 8) c->mode = val;
                else if (strcmp(key, "preset") == 0 && val >= 0 && val <= 2) c->preset = val;
                else if (strcmp(key, "sharpness") == 0) c->sharpness = val < 0 ? 0 : (val > 100 ? 100 : val);
                else if (strcmp(key, "upscale") == 0 || strcmp(key, "enhance") == 0) c->upscale = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "upscale_factor") == 0) c->upscale_factor = val < 50 ? 50 : (val > 100 ? 100 : val);
                else if (strcmp(key, "present_copy") == 0) c->present_copy = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "two_buffer") == 0) c->two_buffer = (val != 0);
                else if (strcmp(key, "pace_order") == 0) c->pace_order = val < 0 ? 0 : (val > 3 ? 3 : val);
                else if (strcmp(key, "shave_mode") == 0) c->shave_mode = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "reg_mode") == 0) c->reg_mode = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "post_mid") == 0) c->post_mid = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "hud_protect") == 0) c->hud_protect = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "game_vectors") == 0) c->game_vectors = (val != 0);
                else if (strcmp(key, "tile_idx") == 0) g_tile_override = val < 0 ? -1 : (val > 31 ? 31 : val);
                else if (strcmp(key, "tile_sweep") == 0) g_tile_sweep = val ? 1 : 0;
                else if (strcmp(key, "t6_bit") == 0) g_t6_bit_override = val < 0 ? -1 : (val > 31 ? 31 : val);
                else if (strcmp(key, "fps_lock") == 0) c->fps_lock = (val == 20 || val == 30 || val == 60) ? val : 0;
                else if (strcmp(key, "menu_hold_ms") == 0) c->menu_hold_ms = val < 500 ? 500 : (val > 10000 ? 10000 : val);
                else if (strcmp(key, "menu_opacity") == 0) c->menu_opacity = val < 20 ? 20 : (val > 100 ? 100 : val);
                else if (strcmp(key, "overlay") == 0) c->overlay = val < 0 ? 0 : (val > 8 ? 8 : val);
                else if (strcmp(key, "menu_combo") == 0) c->menu_combo = menu_combo_sanitize((uint32_t)val);   // table only, else L3+R3
            }
        }
        if (!saved) break;
        p = eol + 1;
    }
    return 1;
}

static void cfg_apply_live(const fg_config_t *c, const char *reason)
{
    g_cfg = *c;
    g_fg_off = !c->framegen;
    if (c->preset == 1) { g_opt_nostr = 0; g_opt_sharp = 0; }      // Fast 60 Hz pulldown keeps straddle guard active
    else if (c->preset == 2) { g_opt_nostr = 0; g_opt_sharp = 1; } // Sharp 2x cubic C=0.75
    else { g_opt_nostr = 0; g_opt_sharp = 0; }                     // Quality 2x
    g_opt_nocf = (c->hud_protect <= 0) ? 1 : 0;
    g_hud_packed = (c->hud_protect >= 1) ? g_hud_file_packed : 0u;
    uint32_t gl = g_gv_letters_base;
    if (c->game_vectors) gl |= GVL_ON; else gl &= ~GVL_ON;
    gv_set_letters(gl);
    if (reason) {
        lg("config (%s): framegen=%d mode=%d preset=%d sharpness=%d enhance=%d hud_protect=%d game_vectors=%d fps_lock=%d overlay=%d opacity=%d%%\n",
           reason, c->framegen, c->mode, c->preset, c->sharpness, c->upscale,
           c->hud_protect, c->game_vectors, c->fps_lock, c->overlay, c->menu_opacity);
    }
}

static int read_mode_raw(void)
{
    init_title_id();
    fg_config_t c;
    cfg_defaults(&c);
    int have = 0;
    int fd = sceKernelOpen(LOG_DIR "/inject", 0 /*O_RDONLY*/, 0);
    if (fd >= 0) {
        char ch = 0;
        long n = (long)sceKernelRead(fd, &ch, 1);
        sceKernelClose(fd);
        if (n == 1 && ch >= '0' && ch <= '8') { c.mode = ch - '0'; have = 1; }
    }
    if (parse_ini_file(LOG_DIR "/config.ini", &c, NULL)) have = 1;
    if (g_title_id[0] && strcmp(g_title_id, "UNKNOWN") != 0) {
        char gpath[96];
        snprintf(gpath, sizeof(gpath), LOG_DIR "/config-%s.ini", g_title_id);
        if (parse_ini_file(gpath, &c, NULL)) have = 1;
    }
    return have ? c.mode : 8;
}
// two_buffer / present_copy in config.ini / config-<TITLE>.ini: read once at start,
// decides whether the virtual flip queue and present-copy buffer extension are enabled.
static int read_present_copy_raw(void)
{
    fg_config_t c;
    cfg_defaults(&c);
    parse_ini_file(LOG_DIR "/config.ini", &c, NULL);
    if (g_title_id[0] && strcmp(g_title_id, "UNKNOWN") != 0) {
        char gpath[96];
        snprintf(gpath, sizeof(gpath), LOG_DIR "/config-%s.ini", g_title_id);
        parse_ini_file(gpath, &c, NULL);
    }
    return c.present_copy; // 0 = Auto, 1 = 2-Buffer (Force On), 2 = Standard (Force Off)
}
static int read_two_buffer_raw(void)
{
    fg_config_t c;
    cfg_defaults(&c);
    parse_ini_file(LOG_DIR "/config.ini", &c, NULL);
    if (g_title_id[0] && strcmp(g_title_id, "UNKNOWN") != 0) {
        char gpath[96];
        snprintf(gpath, sizeof(gpath), LOG_DIR "/config-%s.ini", g_title_id);
        parse_ini_file(gpath, &c, NULL);
    }
    if (c.present_copy == 2) return 0;   // STANDARD buffer mode forced: no virtual queue
    if (c.present_copy == 1 || c.two_buffer) return 1;
    if (vcfg_in_family(g_title_id, "CUSA17419") || vcfg_in_family(g_title_id, "CUSA05571") || vcfg_in_family(g_title_id, "CUSA08809")) return 1;
    return 0;
}

static void apply_adapt_ini(fg_config_t *c, uint32_t *hash_io)
{
    if (!g_title_id[0] || strcmp(g_title_id, "UNKNOWN") == 0) return;
    fg_config_t ac;
    cfg_defaults(&ac);
    char apath[96];
    snprintf(apath, sizeof(apath), LOG_DIR "/adapt-%s.ini", g_title_id);
    if (!parse_ini_file(apath, &ac, hash_io)) return;
    if (c->shave_mode == 0 && ac.shave_mode > 0) c->shave_mode = ac.shave_mode;
    if (c->reg_mode   == 0 && ac.reg_mode   > 0) c->reg_mode   = ac.reg_mode;
    if (c->pace_order == 0 && ac.pace_order > 0) c->pace_order = ac.pace_order;
    if (c->post_mid   == 0 && ac.post_mid   > 0) c->post_mid   = ac.post_mid;
}

static void load_early_adapt_cfg(void)
{
    fg_config_t c;
    cfg_defaults(&c);
    parse_ini_file(LOG_DIR "/config.ini", &c, NULL);
    if (g_title_id[0] && strcmp(g_title_id, "UNKNOWN") != 0) {
        char gpath[96];
        snprintf(gpath, sizeof(gpath), LOG_DIR "/config-%s.ini", g_title_id);
        parse_ini_file(gpath, &c, NULL);
    }
    apply_adapt_ini(&c, NULL);
    g_cfg.pace_order = c.pace_order;
    g_cfg.shave_mode = c.shave_mode;
    g_cfg.reg_mode   = c.reg_mode;
    g_cfg.post_mid   = c.post_mid;
}

static void *alloc_mbufs(size_t total)
{
    long phys = 0; void *addr = NULL;
    size_t range = sceKernelGetDirectMemorySize();
    g_own_alloc++;
    int r = sceKernelAllocateDirectMemory(0, (long)range, total, 0x200000, 3 /*WC_GARLIC*/, &phys);
    if (r != 0) {
        g_own_alloc--;
        size_t av = dm_avail(0x200000);
        lg("mbufs: AllocateDirectMemory = 0x%08x (len 0x%zx align 0x200000 type 3 range 0..0x%zx, largest free 0x%zx)\n",
           (unsigned)r, total, range, av);
        STAGE("mbufs allocate FAILED: len=0x%zx align=0x200000 type=3 range=0..0x%zx r=0x%08x largest free=0x%zx",
              total, range, (unsigned)r, av);
        return NULL;
    }
    r = sceKernelMapDirectMemory(&addr, total, 0x33, 0, phys, 0x200000);
    if (r != 0 || !addr) {
        sceKernelReleaseDirectMemory(phys, total);
        g_own_alloc--;
        lg("mbufs: MapDirectMemory = 0x%08x (len 0x%zx phys 0x%lx)\n", (unsigned)r, total, phys);
        STAGE("mbufs map FAILED: len=0x%zx phys=0x%lx r=0x%08x", total, phys, (unsigned)r);
        return NULL;
    }
    g_own_alloc--;
    STAGE("mbufs ok: len=0x%zx phys=0x%lx va=%p", total, phys, addr);
    memset(addr, 0, total);
    return addr;
}

// End of the game's own display indices (its groups), never reaching into ours.
static uint32_t game_idx_end(void)
{
    int e = g_game_idx_hi > g_ngame ? g_game_idx_hi : g_ngame;
    if (g_mbuf_state == 1 && g_mbase >= g_ngame && e > g_mbase) e = g_mbase;
    return (uint32_t)e;
}

static int mbufs_ready(uint32_t handle)
{
    (void)handle;
    return g_mbuf_state == 1;
}

// ---- safe mode (titles not verified on hardware) ----
// Such a title gets none of our memory, no changed RegisterBuffers and no forced flip rate while it starts: the plugin
// only watches. After INJECT_AFTER_FLIPS frames safe_try_activate() checks what it saw and, only if everything is
// fine, takes the memory and registers its extra display buffers as a separate group at the top of the 16 indices.
// A refusal leaves the game exactly as it runs without the plugin ("INERT: SAFE ..." in the report).
static int g_safe = 0;            // 1: the title is not verified, observe first
static int g_safe_active = 0;     // 1: the late set-up succeeded, FG may run
static int g_safe_done = 0;       // the one-time check ran (either result)
static int32_t g_game_flip_rate = -1;

static int is_verified_title(void)
{
    return vcfg_is_verified_family(g_title_id);
}

// What the plugin learned about a title (LOG_DIR/safe-<TITLE>.state, one digit; raw kernel calls, stdio faults in plugin_load):
// 0/none = observe, then try to take memory late; 1 = the late set-up found no free memory (a game that claims the whole
// pool at its start), so the next start takes the memory early like a verified title; 2 = such an early start did not
// reach frame INJECT_AFTER_FLIPS (LOG_DIR/safe-<TITLE>.boot marker left behind), so this title only gets observed.
static int g_safe_early = 0;      // 1: this start takes its memory early (state 1)
static int g_safe_never = 0;      // 1: state 2, never touch the game
static void safe_path(char *buf, size_t n, const char *ext) { snprintf(buf, n, LOG_DIR "/safe-%s.%s", g_title_id, ext); }
static int safe_state_read(void)
{
    char p[64], c = '0';
    safe_path(p, sizeof(p), "state");
    int fd = sceKernelOpen(p, 0, 0);
    if (fd < 0) return 0;
    if ((long)sceKernelRead(fd, &c, 1) != 1) c = '0';
    sceKernelClose(fd);
    return c == '1' ? 1 : c == '2' ? 2 : 0;
}
static void safe_state_write(int st)
{
    char p[64], c = (char)('0' + st);
    safe_path(p, sizeof(p), "state");
    int fd = sceKernelOpen(p, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
    if (fd < 0) return;
    sceKernelWrite(fd, &c, 1);
    sceKernelClose(fd);
}
static int safe_marker(int op)   // 0 = exists?, 1 = create, 2 = remove
{
    char p[64];
    safe_path(p, sizeof(p), "boot");
    if (op == 2) { sceKernelUnlink(p); return 0; }
    int fd = sceKernelOpen(p, op == 1 ? 0x601 : 0, 0666);
    if (fd < 0) return 0;
    if (op == 1) sceKernelWrite(fd, "1", 1);
    sceKernelClose(fd);
    return 1;
}

static void safe_refuse(const char *why)
{
    snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: SAFE %s", why);
    STAGE("safe mode frame %u: refused (%s) -> the game stays native", g_flips, why);
    lg("safe mode: refused at frame %u (%s)\n", g_flips, why);
    release_early_mem();
    release_early_work();
    release_early_gpu();
    if (!strncmp(why, "NO ", 3) && strstr(why, "MEM")) {   // no free memory late: next start takes it early
        safe_state_write(1);
        STAGE("safe mode: state saved -> the next start takes its memory early (a start that does not reach frame %d is then given up)", INJECT_AFTER_FLIPS);
    }
}
// Surface size of one game display buffer: the VA gap between the game's buffers if it looks like a surface, else
// pitch * height * 4. 0 = unknown (the same rules as the early path in RegisterBuffers).
static size_t safe_surface_stride(void)
{
    size_t one = 0;
    for (int i = 0; i + 1 < g_ngame; i++) {
        uintptr_t a0 = (uintptr_t)g_buf[i], a1 = (uintptr_t)g_buf[i + 1];
        size_t diff = a1 > a0 ? (size_t)(a1 - a0) : (size_t)(a0 - a1);
        if (diff >= 0x100000 && diff <= 0x4000000 && !(diff & 0x7FFF) && (!one || diff < one)) one = diff;
    }
    if (!one && g_pitch >= 640 && g_bh >= 360) {
        size_t raw_sz = (size_t)g_pitch * (size_t)((g_bh + 63u) & ~63u) * 4u;
        one = (raw_sz + 0x7FFFFu) & ~(size_t)0x7FFFFu;
    }
    return (one < 0x100000 || one > 0x4000000 || (one & 0x7FFF)) ? 0 : one;
}

// Called once on the game's flip thread at frame INJECT_AFTER_FLIPS, after the config was read.
static void safe_try_activate(uint32_t handle)
{
    g_safe_done = 1;
    STAGE("safe mode frame %u: check (title %s, %ux%u fmt=0x%x ngame=%d idx_hi=%d detected60=%d game_alloc_fail=%u)", g_flips,
          g_title_id, g_bw, g_bh, g_attr.format, g_ngame, g_game_idx_hi, g_detected_60fps, g_dbg_alloc_fail);
    STAGE("safe mode frame %u: free direct memory: largest block for align 0x10000 = 0x%zx, 0x200000 = 0x%zx (pool 0x%zx)", g_flips,
          dm_avail(0x10000), dm_avail(0x200000), (size_t)sceKernelGetDirectMemorySize());
    if (g_fg_off && !g_cfg.upscale && !g_cfg.overlay) {
        STAGE("safe mode: FG, Enhance and overlay all off -> nothing to set up");
        release_early_mem(); release_early_work(); release_early_gpu();
        return;
    }
    if (!g_fmt_supported) { char w[24]; snprintf(w, sizeof(w), "FMT 0x%x", g_attr.format); safe_refuse(w); return; }
    if (!fg_res_supported(g_bw, g_bh)) { char w[24]; snprintf(w, sizeof(w), "RES %ux%u", g_bw, g_bh); safe_refuse(w); return; }
    if (g_ngame < 2 || g_ngame > 8) { safe_refuse("BUFFERS"); return; }
    if (g_detected_60fps && g_cfg.fps_lock != 30) {
        snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
        release_early_mem(); release_early_work(); release_early_gpu();
        return;
    }
    size_t one = safe_surface_stride();
    if (!one) { safe_refuse("SURFACE SIZE"); return; }
    int hi = g_game_idx_hi > g_ngame ? g_game_idx_hi : g_ngame;
    int want_pcopy = (g_cfg.present_copy == 1) || (g_cfg.present_copy == 0 && g_ngame == 2);
    int hi_res = (g_bw > 1920 || g_bh > 1080);
    int want = want_pcopy ? (hi_res ? 4 : 6) : (hi_res ? 2 : 3);
    if (want > 16 - hi) want = 16 - hi;
    if (want < 1) { safe_refuse("NO INDICES"); return; }
    // Use early pre-reserved buffers if they survived boot; otherwise try late allocation.
    g_gpu_sz = gv_armed() ? GPU_SZ : GPU_SZ_BASE;
    if (!g_pre_gpu) g_pre_gpu = alloc_dm_range(0, (long)sceKernelGetDirectMemorySize(), g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    if (!g_pre_gpu) { safe_refuse("NO GPU MEM"); return; }
    if (!g_pre_work) {
        g_pre_work = alloc_work_early(WORK_SZ_8, 3);
        g_work_garlic = g_pre_work != NULL;
    }
    if (!g_pre_work) g_pre_work = alloc_work_early(WORK_SZ_8, 0);
    if (!g_pre_work) { safe_refuse("NO WORK MEM"); return; }
    uint8_t *m = NULL; int nm = 0;
    if (g_pre_mem && g_pre_size >= one) {
        m = (uint8_t *)g_pre_mem;
        nm = (int)(g_pre_size / one);
        if (nm > want) nm = want;
    }
    for (int n = want; !m && n >= 1; n = (n == 6 ? 4 : n - 1)) {   // 6, 4, 3, 2, 1
        size_t need = (one * (size_t)n + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
        if (n > 1 && dm_avail(0x200000) < need + (128ull << 20)) continue;
        m = (uint8_t *)alloc_mbufs(need);
        if (m) nm = n;
    }
    if (!m) { safe_refuse("NO DISPLAY MEM"); return; }
    int base = 16 - nm;   // top of the index range: the game's own indices stay free
    if (base < hi) { safe_refuse("NO INDICES"); return; }
    void *ext[8];
    for (int i = 0; i < nm; i++) ext[i] = m + (size_t)i * one;
    int32_t r1 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                               (int32_t)handle, base, ext, nm, &g_attr);
    STAGE("safe mode RegisterBuffers (late group at %d, %d x 0x%zx) = 0x%08x", base, nm, one, (unsigned)r1);
    if (r1 < 0) { safe_refuse("REGISTER FAILED"); return; }
    for (int i = 0; i < nm; i++) g_buf[base + i] = (uint64_t)(uintptr_t)ext[i];
    g_mbase = base;
    g_nbuf = base + nm;
    g_nmid = nm < 3 ? nm : 3;
    g_pcopy_avail = (want_pcopy && nm >= 4);
    g_pcopy_k = nm >= 6 ? 3 : 2;
    g_mbuf_state = 1;
    int32_t rr = HOOK_CONTINUE(sceVideoOutSetFlipRate, int32_t (*)(int32_t, int32_t), (int32_t)handle, 0);
    g_safe_active = 1;
    g_inert_reason[0] = 0;
    STAGE("safe mode activated at frame %u: nm=%d base=%d game flip rate was %d, SetFlipRate(0)=0x%08x", g_flips, nm, base,
          (int)g_game_flip_rate, (unsigned)rr);
    lg("safe mode: activated at frame %u (base=%d, extra=%d, SetFlipRate(0)=0x%08x)\n", g_flips, base, nm, (unsigned)rr);
}

static uint64_t g_pace_next = 0;
static void pace_hz(uint32_t hz)
{
    if (!hz || !g_freq) return;
    uint64_t period = g_freq / hz;
    uint64_t now = sceKernelGetProcessTimeCounter();
    if (g_pace_next == 0 || now > g_pace_next + period * 4) { g_pace_next = now + period; return; }
    if (now < g_pace_next) {
        uint64_t us = (g_pace_next - now) * 1000000ull / g_freq;
        if (us > 1500) sceKernelUsleep((uint32_t)(us - 1000));
        while (sceKernelGetProcessTimeCounter() < g_pace_next) { }
    }
    g_pace_next += period;
}
static void pace_30(void) { pace_hz(30); }

// ---- flip cadence monitor ----
static volatile int g_mon_on = 0;
static volatile uint64_t g_reals_shown = 0;
static volatile uint64_t g_mids_shown = 0;
static volatile int g_last_real_buf = -1;
static uint32_t g_hist[2][4];
static uint32_t g_mon_missed = 0, g_pace_timeouts = 0, g_pace_resync = 0, g_pace_fix = 0, g_mon_gaps = 0;
static volatile uint64_t g_rel_t = 0;
static volatile uint32_t g_cpu_n = 0, g_cpu_sum_us = 0, g_cpu_max_us = 0;
static uint32_t g_cpu_ema_us = 0;   // game time release -> next submit (its own work, without our waits)

static int file_flags_ex(uint32_t *hash_io)
{
    int fd = sceKernelOpen(LOG_DIR "/inject", 0, 0);
    if (fd < 0) return -1;
    char b[16] = {0};
    long n = (long)sceKernelRead(fd, b, 15);
    sceKernelClose(fd);
    if (n > 0 && hash_io) *hash_io = fnv1a_bytes(*hash_io, b, (size_t)n);
    int f = 0;
    uint32_t g = 0;
    for (long i = 0; i < n; i++) {
        if (b[i] == 'x') f |= 1; if (b[i] == 'e') f |= 2; if (b[i] == 'm') f |= 4; if (b[i] == 'p') f |= 8; if (b[i] == 'd') f |= 16; if (b[i] == 'D') f |= 32; if (b[i] == 'k') f |= 64; if (b[i] == 'f') f |= 128; if (b[i] == 'g') f |= 256;
        if (b[i] == 'L') f |= 512; if (b[i] == 'T') f |= 1024; if (b[i] == 'M') f |= 2048;
        if (b[i] == 'G') f |= 4096; if (b[i] == 'P') f |= 8192; if (b[i] == 'S') f |= 16384; if (b[i] == 'b') f |= 32768;
        if (b[i] == 'v') g |= GVL_ON; if (b[i] == 'n') g |= GVL_NOAPPLY; if (b[i] == 's') g |= GVL_SCAN; if (b[i] == 'V') g |= GVL_DUMP;
        if (b[i] == 'r') g |= GVL_RECAL; if (b[i] == 'w') g |= GVL_SPLIT; if (b[i] == 'q') g |= GVL_NOSPLICE;
        if (b[i] == 'a') g |= GVL_ANCHOR; if (b[i] == 'z') g |= GVL_VIZ; if (b[i] == 'W') g |= GVL_WIDE;
    }
    g_ab_x_flag = (f & 1) ? 1 : 0;
    g_gv_letters_base = g & ~GVL_ON;
    if (g_cfg.game_vectors) g |= GVL_ON;
    gv_set_letters(g);
    return f;
}
static int file_flags(void) { return file_flags_ex(NULL); }

static uint32_t g_last_disk_hash = 0;
static void cfg_reload_from_disk(int force)
{
    uint32_t h = 2166136261u;
    fg_config_t c;
    cfg_defaults(&c);
    int f = file_flags_ex(&h);
    if (f >= 0) {
        c.framegen = (f & 1) ? 0 : ((g_known_game && !g_detected_60fps) ? 1 : 0);
        if (f & (1 << 15)) c.preset = 1;
        else if (f & (1 << 14)) c.preset = 2;
        if (f & (1 << 6)) c.hud_protect = 0;
        if (gv_armed()) c.game_vectors = 1;
    }
    int has_global = parse_ini_file(LOG_DIR "/config.ini", &c, &h);
    int has_game = 0;
    if (g_title_id[0] && strcmp(g_title_id, "UNKNOWN") != 0) {
        char gpath[96];
        snprintf(gpath, sizeof(gpath), LOG_DIR "/config-%s.ini", g_title_id);
        has_game = parse_ini_file(gpath, &c, &h);
    }
    apply_adapt_ini(&c, &h);
    if (!g_known_game && !has_game && !has_global) c.framegen = 0;
    if (g_detected_60fps && !has_game) c.framegen = 0;
    if (!force && h == g_last_disk_hash) return;
    g_last_disk_hash = h;
    if (f >= 0) {
        int e = (f >> 1) & 1, m = (f >> 2) & 1, ph = (f >> 3) & 1;
        if (e != g_opt_early) { g_opt_early = e; lg("option early release %s (switch file)\n", e ? "ON" : "OFF"); }
        if (m != g_opt_marker) { g_opt_marker = m; lg("option marker %s (switch file)\n", m ? "ON" : "OFF"); }
        if ((f & 32) && g_dump_mem && !g_dump_req && !g_dump_frame) { g_dump_req = 1; lg("dump requested\n"); }
        if (!(f & 32) && g_dump_frame == 0xFFFFFFFFu) g_dump_frame = 0;
        g_opt_notex = (f >> 12) & 1; g_opt_notpg = (f >> 13) & 1;
        g_opt_noanc = (f >> 9) & 1;  g_opt_notp  = (f >> 10) & 1; g_opt_nomf = (f >> 11) & 1;
        g_opt_nofgk = (f >> 8) & 1;  g_opt_phase = ph;
    }
    cfg_apply_live(&c, force ? "init" : "hot-reload");
    if (!g_menu_open) g_menu_edit = c;
}

static void cfg_save_game_ini(void)
{
    char path[96], tmp[104];
    snprintf(path, sizeof(path), LOG_DIR "/config-%s.ini", g_title_id);
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    sceKernelMkdir(LOG_DIR, 0777);
    FILE *f = fopen(tmp, "w");
    if (!f) {
        snprintf(g_osd_toast, sizeof(g_osd_toast), "ERR: SAVE FAILED");
        lg("config: cannot write %s\n", tmp);
        return;
    }
    fg_config_t c = g_cfg;
    fprintf(f, "[bb_probe]\n");
    fprintf(f, "; per-game profile for %s (saved from L3+R3 menu)\n", g_title_id);
    fprintf(f, "framegen = %d\n", c.framegen);
    fprintf(f, "mode = %d\n", c.mode);
    fprintf(f, "preset = %d\n", c.preset);
    fprintf(f, "sharpness = %d\n", c.sharpness);
    fprintf(f, "upscale = %d\n", c.upscale);
    fprintf(f, "upscale_factor = %d\n", c.upscale_factor);
    fprintf(f, "present_copy = %d\n", c.present_copy);
    fprintf(f, "two_buffer = %d\n", c.two_buffer);
    if (c.pace_order) fprintf(f, "pace_order = %d\n", c.pace_order);
    if (c.shave_mode) fprintf(f, "shave_mode = %d\n", c.shave_mode);
    if (c.reg_mode) fprintf(f, "reg_mode = %d\n", c.reg_mode);
    if (c.post_mid) fprintf(f, "post_mid = %d\n", c.post_mid);
    if (g_tile_override >= 0) fprintf(f, "tile_idx = %d\n", (int)g_tile_override);
    fprintf(f, "hud_protect = %d\n", c.hud_protect);
    fprintf(f, "game_vectors = %d\n", c.game_vectors);
    fprintf(f, "fps_lock = %d\n", c.fps_lock);
    fprintf(f, "menu_hold_ms = %d\n", c.menu_hold_ms);
    fprintf(f, "menu_opacity = %d\n", c.menu_opacity);
    fprintf(f, "overlay = %d\n", c.overlay);
    fprintf(f, "menu_combo = %u\n", (unsigned)menu_combo_sanitize(c.menu_combo));
    fflush(f);
    fclose(f);
    rename(tmp, path);
    g_last_disk_hash = 0;
    cfg_reload_from_disk(0);
    snprintf(g_osd_toast, sizeof(g_osd_toast), "SAVED CONFIG-%s.INI", g_title_id);
    g_osd_toast_until = sceKernelGetProcessTime() + 2500000ull;
    lg("config saved to %s\n", path);
}

static void *io_thread(void *arg)
{
    (void)arg;
    unsigned poll_n = 0;
    int no_flip_logged = 0;
    for (;;) {
        sceKernelUsleep(2000);
        if (g_lgq_r != g_lgq_w) lg_drain();
        bench_io_tick();
        if (__atomic_exchange_n(&g_cfg_save_req, 0, __ATOMIC_ACQ_REL)) cfg_save_game_ini();
        if (g_inject_on == 8) gv_io_tick();
        poll_n++;
        if (!no_flip_logged && poll_n == 5000 && g_flips == 0) {
            no_flip_logged = 1;
            snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO GNM FLIP");
            lg("inert: no flip hook (0 flips after 10 s; game does not call sceGnmSubmitAndFlipCommandBuffers, voSubmitFlips=%u)\n",
               g_vo_submit_flips);
        }
        if ((poll_n % 250) == 0 && g_inject_on == 8 && !g_bench_enabled) {
            cfg_reload_from_disk(0);
        }
        if (g_dump_frame && g_dump_frame != 0xFFFFFFFFu && g_dump_mem && g_flips > g_dump_frame + 2 &&
            (uint32_t)*(volatile uint64_t *)(g_gpu + DUMP_DONE_OFF) != g_dump_frame && g_flips <= g_dump_frame + 120) {
        } else if (g_dump_frame && g_dump_frame != 0xFFFFFFFFu && g_dump_mem && g_flips > g_dump_frame + 120) {
            lg("dump: completion label of frame %u never seen, dump skipped\n", (unsigned)g_dump_frame);
            g_dump_frame = 0xFFFFFFFFu;
        } else if (g_dump_frame && g_dump_frame != 0xFFFFFFFFu && g_flips > g_dump_frame + 2 && g_dump_mem) {
            static const char *names[3] = { "prev", "cur", "mid" };
            for (int i = 0; i < 3; i++) {
                char path[96], tmp[104];
                snprintf(path, sizeof(path), LOG_DIR "/dump_%s.rgba", names[i]);
                snprintf(tmp, sizeof(tmp), "%s.tmp", path);
                FILE *df = fopen(tmp, "wb");
                if (df) { fwrite(g_dump_mem + (size_t)i * 960 * 1080 * 4, 1, 960 * 1080 * 4, df); fflush(df); fclose(df); rename(tmp, path); }
            }
            {
                FILE *ff = fopen(LOG_DIR "/dump_field.bin.tmp", "wb");
                if (ff) { fwrite(g_dump_mem + (size_t)3 * 960 * 1080 * 4, 1, 163200, ff); fflush(ff); fclose(ff); rename(LOG_DIR "/dump_field.bin.tmp", LOG_DIR "/dump_field.bin"); }
            }
            lg("dump written: dump_prev/cur/mid.rgba (960x1080 RGBA8, left half of the screen) + dump_field.bin at frame %u\n", (unsigned)g_dump_frame);
            g_dump_frame = 0xFFFFFFFFu;
        }
    }
    return NULL;
}

static void *mon_thread(void *arg)
{
    (void)arg;
    VoFlipStatus fs;
    VoVblankStatus vs;
    uint64_t last_count = 0, last_t = 0, win_t = 0, win_vb = 0, osd_t = 0, osd_fcnt = 0;
    int last_buf = -1, pend_max = 0, gcq_max = 0;
    for (;;) {
        sceKernelUsleep(500);
        if (g_handle < 0) continue;
        memset(&fs, 0, sizeof(fs));
        memset(&vs, 0, sizeof(vs));
        if (read_flip_status(g_handle, &fs) != 0) continue;
        sceVideoOutGetVblankStatus(g_handle, &vs);
        __atomic_store_n(&g_cur_pending_flips, (uint32_t)(fs.flipPendingNum > 0 ? fs.flipPendingNum : 0), __ATOMIC_RELEASE);
        __atomic_store_n(&g_cur_gcq, (uint32_t)(fs.gcQueueNum > 0 ? fs.gcQueueNum : 0), __ATOMIC_RELEASE);
        if (fs.flipPendingNum > pend_max) pend_max = fs.flipPendingNum;
        if (fs.gcQueueNum > gcq_max) gcq_max = fs.gcQueueNum;
        if (fs.count != last_count) {
            if (g_mon) { g_mon[1] = (uint32_t)fs.currentBuffer; g_mon[2] = (uint32_t)vs.count; }
            if (last_count) {
                if (fs.count - last_count > 1) g_mon_missed += (uint32_t)(fs.count - last_count - 1);
                if (last_buf >= 0 && fs.processTime > last_t) {
                    uint64_t dt = fs.processTime - last_t;
                    uint32_t nv = (uint32_t)((dt + 8341) / 16683);
                    if (nv < 1) nv = 1;
                    if (nv > 4) nv = 4;
                    g_hist[IS_MID(last_buf) ? 0 : 1][nv - 1]++;
                    if (!IS_MID(last_buf) && g_mon) {
                        static uint32_t ema_dt = 16683u;
                        uint32_t dtu = dt > 66732u ? 66732u : (uint32_t)dt;
                        __atomic_store_n(&g_last_real_hold_us, dtu, __ATOMIC_RELEASE);
                        ema_dt = (ema_dt * 3u + dtu) >> 2;
                        uint32_t t_q16 = (ema_dt << 16) / (ema_dt + 16683u);
                        if (t_q16 < 32768u) t_q16 = 32768u;
                        if (t_q16 > 49152u) t_q16 = 49152u;
                        g_mon[3] = t_q16;
                    }
                }
                uint64_t nf = fs.count - last_count;
                int cur_mid = IS_MID(fs.currentBuffer), last_mid = IS_MID(last_buf);
                if (nf > 1 && fs.currentBuffer >= 0 && last_buf >= 0 && ((nf & 1) ? cur_mid != last_mid : cur_mid == last_mid)) {
                    uint64_t a = (nf + 1) / 2, b = nf / 2;
                    if (cur_mid) { g_mids_shown += a; g_reals_shown += b; } else { g_reals_shown += a; g_mids_shown += b; }
                    g_mon_gaps++;
                } else {
                    if (fs.currentBuffer >= 0 && !IS_MID(fs.currentBuffer) && last_buf >= 0 && IS_MID(last_buf)) g_reals_shown++;
                    if (IS_MID(fs.currentBuffer) && last_buf >= 0 && !IS_MID(last_buf)) g_mids_shown++;
                }
            }
            if (fs.currentBuffer >= 0 && !IS_MID(fs.currentBuffer) && fs.currentBuffer < 16 && fs.currentBuffer != last_buf) {
                uint64_t t0 = __atomic_exchange_n(&g_sub_t[fs.currentBuffer], 0ull, __ATOMIC_ACQ_REL);
                if (t0 && fs.processTime > t0 && fs.processTime - t0 < 500000ull) {
                    uint64_t l = fs.processTime - t0; g_lat_sum += l; g_lat_n++; if (l > g_lat_max) g_lat_max = l;
                }
            }
            if (fs.currentBuffer >= 0 && !IS_MID(fs.currentBuffer)) g_last_real_buf = fs.currentBuffer;
            last_count = fs.count;
            last_t = fs.processTime;
            last_buf = fs.currentBuffer;
        }
        if (g_mon) g_mon[0] = (uint32_t)vs.count;
        if (!osd_t) { osd_t = vs.processTime; osd_fcnt = fs.count; }
        if (vs.processTime > osd_t + 500000ull) {
            double out_fps = (double)(fs.count - osd_fcnt) * 1e6 / (double)(vs.processTime - osd_t);
            if (out_fps > 60.0) out_fps = 60.0;
            uint32_t prev_out = __atomic_load_n(&g_osd_out_fps_x10, __ATOMIC_ACQUIRE);
            uint32_t cur_out = (uint32_t)(out_fps * 10.0 + 0.5);
            uint32_t sm_out = prev_out ? (prev_out * 2u + cur_out * 2u) / 4u : cur_out;
            __atomic_store_n(&g_osd_out_fps_x10, sm_out, __ATOMIC_RELEASE);
            osd_t = vs.processTime;
            osd_fcnt = fs.count;
        }
        if (!win_t) { win_t = vs.processTime; win_vb = vs.count; }
        if (vs.processTime > win_t + 2000000) {
            double hz = (double)(vs.count - win_vb) * 1e6 / (double)(vs.processTime - win_t);
            g_last_hz_x10 = (uint32_t)(hz * 10.0 + 0.5);
            uint32_t cn_ = __atomic_exchange_n(&g_cpu_n, 0u, __ATOMIC_ACQ_REL);
            uint32_t csum_ = __atomic_exchange_n(&g_cpu_sum_us, 0u, __ATOMIC_ACQ_REL);
            uint32_t cmax_ = __atomic_exchange_n(&g_cpu_max_us, 0u, __ATOMIC_ACQ_REL);
            lg("LATENCY real frame hand-over -> on screen: avg %.1f max %.1f ms (%llu frames) | FG %s preset %d | pending max %d\n",
               g_lat_n ? (double)g_lat_sum / 1000.0 / (double)g_lat_n : 0.0, (double)g_lat_max / 1000.0,
               (unsigned long long)g_lat_n, (g_cfg.framegen && !g_inert_reason[0]) ? "on" : "off", g_cfg.preset, pend_max);
            g_lat_sum = g_lat_n = g_lat_max = 0;
            lg("CADENCE (vblanks on screen) mid 1:%u 2:%u 3:%u 4+:%u | real 1:%u 2:%u 3:%u 4+:%u | refresh %.3f Hz | "
               "pending max %d gcq max %d | missed %u (gaps %u) | pace timeouts %u resync %u fix %u | game cpu release->submit avg %.1f max %.1f ms\n",
               g_hist[0][0], g_hist[0][1], g_hist[0][2], g_hist[0][3], g_hist[1][0], g_hist[1][1], g_hist[1][2], g_hist[1][3],
               hz, pend_max, gcq_max, g_mon_missed, g_mon_gaps, g_pace_timeouts, g_pace_resync, g_pace_fix,
               cn_ ? csum_ / 1000.0 / cn_ : 0.0, cmax_ / 1000.0);
            memset(g_hist, 0, sizeof(g_hist));
            // Keep the CPU measurement visible in the 200-byte flight-recorder rows.
            if (tb_title()) {
                lg("CPU release->submit avg %.1f max %.1f ms | test=%d FG=%d pcopy=%d preset=%d\n",
                   cn_ ? csum_ / 1000.0 / cn_ : 0.0, cmax_ / 1000.0,
                   VCFG_2BUF_TEST, !g_fg_off, g_pcopy_on, g_cfg.preset);
                tb_report();
            }
            vq_report();
            pend_max = gcq_max = 0;
            g_mon_missed = g_pace_timeouts = g_pace_resync = g_pace_fix = g_mon_gaps = 0;
            win_t = vs.processTime;
            win_vb = vs.count;
        }
    }
    return NULL;
}

static void io_start(void)
{
    static int io_started = 0;
    if (io_started) return;
    io_started = 1;
    OrbisPthread th2;
    int r2 = scePthreadCreate(&th2, NULL, io_thread, NULL, "bb_io");
    g_lg_async = (r2 == 0);
    lg("io thread started: 0x%08x\n", (unsigned)r2);
}

static void pad_poll_start(void);
static void mon_start(void)
{
    static int started = 0;
    io_start();
    if (started) return;
    started = 1;
    pad_poll_start();
    OrbisPthread th;
    int r = scePthreadCreate(&th, NULL, mon_thread, NULL, "bb_mon");
    g_mon_on = (r == 0);
    lg("cadence monitor thread: 0x%08x\n", (unsigned)r);
}

static uint64_t g_dbl_frames = 0;
static int64_t g_pace_off = 0;
static void pace_vblank(int prev2_real, int prev_real, int cur_real)
{
    if (!g_mon_on) { pace_30(); return; }
    g_dbl_frames++;
    volatile uint64_t *cnt = g_opt_early ? &g_mids_shown : &g_reals_shown;
    // Present copy (2-buffer games): release the game when the frame before the previous one is on screen, so its CPU
    // work overlaps the GPU of the previous frame (waiting for the previous one serialised them: 26 + 26 ms, 24 fps).
    int64_t step = g_pcopy_on ? 2 : 1;
    int64_t target = (int64_t)g_dbl_frames - step + g_pace_off;
    int64_t have = (int64_t)*cnt;
    if (have > target) {
        g_pace_off = have - ((int64_t)g_dbl_frames - step);
        g_pace_resync++;
        g_rel_t = sceKernelGetProcessTime();
        return;
    }
    uint64_t t0 = sceKernelGetProcessTime();
    while ((int64_t)*cnt < target) {
        if (sceKernelGetProcessTime() - t0 > 60000) {
            g_pace_off = (int64_t)*cnt - ((int64_t)g_dbl_frames - step);
            g_pace_timeouts++;
            break;
        }
        sceKernelUsleep(200);
    }
    int distinct_ok = (g_ngame == 2) ? (prev_real >= 0 && prev_real != cur_real)
                                     : (prev2_real != cur_real && prev_real != cur_real && prev2_real != prev_real);
    if (distinct_ok && g_pcopy_on) {
        VoFlipStatus fs;
        memset(&fs, 0, sizeof(fs));
        if (read_flip_status(g_handle, &fs) == 0) {
            int late = fs.currentBuffer == cur_real ||
                       (g_opt_early && fs.currentBuffer >= g_mbase && g_last_real_buf == prev_real);
            if (late) { g_pace_off--; g_pace_fix++; }
        }
    }
    g_rel_t = sceKernelGetProcessTime();
}

// VCFG timer pacing for FG pairs, per title (LOG_DIR/pace_timer_<TITLE> exists): releases the game once per pair
// period (2 refreshes at the measured rate) from the previous release, instead of waiting until its previous real
// frame is on screen (pace_vblank). CUSA05571 needs 25 ms per frame but waited ~13 ms more in pace_vblank behind a
// 4-deep flip queue and ran at 24-25 fps. +2 ms on the next release while 3+ flips are pending (drift).
static int g_pace_timer = -1;
static uint64_t g_pair_next;
static void pace_pair(void)
{
    if (!g_freq) return;
    uint32_t hz10 = g_last_hz_x10 > 400u && g_last_hz_x10 < 800u ? g_last_hz_x10 : 599u;
    uint64_t period = g_freq * 20ull / hz10;                  // 2 refreshes
    uint64_t now = sceKernelGetProcessTimeCounter();
    if (!g_pair_next || now > g_pair_next + period * 3) { g_pair_next = now + period; g_rel_t = sceKernelGetProcessTime(); return; }
    if (now < g_pair_next) {
        uint64_t us = (g_pair_next - now) * 1000000ull / g_freq;
        if (us > 1500) sceKernelUsleep((uint32_t)(us - 1000));
        while (sceKernelGetProcessTimeCounter() < g_pair_next) { }
        now = sceKernelGetProcessTimeCounter();
    }
    g_pair_next += period;
    if (g_pair_next <= now) g_pair_next = now + period;
    if (__atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE) >= 3u) g_pair_next += g_freq / 500u;   // drain: +2 ms
    g_rel_t = sceKernelGetProcessTime();
}
static int pace_timer_on(void)
{
    if (g_cfg.pace_order == 3) return 1;
    if (g_pace_timer < 0) {
        char p[96]; snprintf(p, sizeof(p), LOG_DIR "/pace_timer_%s", g_title_id);
        FILE *f = fopen(p, "r"); g_pace_timer = f != NULL; if (f) fclose(f);
        lg("pacing: %s (%s)\n", g_pace_timer ? "timer" : "vblank", p);
    }
    return g_pace_timer;
}

static uint32_t med3_u32(uint32_t a, uint32_t b, uint32_t c)
{
    if (a > b) { uint32_t t = a; a = b; b = t; }
    if (b > c) { b = c; }
    return a > b ? a : b;
}

static void tick(uint32_t n, uint32_t handle, uint32_t index, uint32_t mode)
{
    (void)handle;
    if (!g_freq) g_freq = sceKernelGetProcessTimeCounterFrequency();
    uint64_t now = sceKernelGetProcessTimeCounter();
    if (!g_t0) { g_t0 = g_last = g_win_t = g_fps_win_t = now; }
    double dt = (now - g_last) * 1000.0 / (double)g_freq;
    g_last = now;
    g_win_n++;
    g_fps_win_n++;
    if (dt > g_win_max) g_win_max = dt;

    // 1.1: Update 3-tap median filter + EMA of real game frame interval (in us)
    if (n > 2 && dt > 5.0 && dt < 200.0) {
        uint32_t dtu = (uint32_t)(dt * 1000.0 + 0.5);
        g_dt_ring[0] = g_dt_ring[1];
        g_dt_ring[1] = g_dt_ring[2];
        g_dt_ring[2] = dtu;
        uint32_t m3 = med3_u32(g_dt_ring[0], g_dt_ring[1], g_dt_ring[2]);
        g_ema_frame_us = (g_ema_frame_us * 3u + m3) >> 2;
        if (g_ema_frame_us < 16683u) g_ema_frame_us = 16683u;
        if (g_ema_frame_us > 100000u) g_ema_frame_us = 100000u;
        __atomic_store_n(&g_osd_frame_ms_x10, (g_ema_frame_us + 50u) / 100u, __ATOMIC_RELEASE);
    }

    // 1.3: Smooth ~0.5 s real game FPS counter for the corner overlay
    if (now - g_fps_win_t >= g_freq / 2) {
        double secs = (now - g_fps_win_t) / (double)g_freq;
        double rfps = g_fps_win_n / secs;
        uint32_t cur_rfps = (uint32_t)(rfps * 10.0 + 0.5);
        uint32_t prev_rfps = __atomic_load_n(&g_osd_real_fps_x10, __ATOMIC_ACQUIRE);
        uint32_t sm_rfps = prev_rfps ? (prev_rfps + cur_rfps) / 2u : cur_rfps;
        __atomic_store_n(&g_osd_real_fps_x10, sm_rfps, __ATOMIC_RELEASE);
        // 1.4: Auto-detect 60 fps games during warmup (flips 30..120) before FG pacing begins.
        // Skip if the game explicitly requested a 30/20 Hz lock via sceVideoOutSetFlipRate(handle, >=1).
        if (n >= 60 && n <= INJECT_AFTER_FLIPS && sm_rfps >= 480u && !g_detected_60fps && g_game_flip_rate <= 0) {
            g_detected_60fps = 1;
            snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
            lg("auto-detect: game runs at %.1f fps (>= 48 fps) -> FrameGen auto-disabled (60 Hz output limit)\n", rfps);
        }
        {   // Diagnostics only: log a change of the game's frame rate class (30 -> 60 on a menu, etc.)
            static int rate_class;
            int cls = sm_rfps >= 480u ? 60 : (sm_rfps < 400u ? 30 : 0);
            if (n >= 30 && cls && cls != rate_class) {
                if (rate_class) lg("rate change: %d -> %d fps class at flip %u (%.1f fps, detected_60=%d)\n", rate_class, cls, n, rfps, g_detected_60fps);
                rate_class = cls;
            }
        }
        g_fps_win_t = now;
        g_fps_win_n = 0;
    }

    if (n <= 30)
        lg("gnm #%u t=%.3fs dt=%.2fms idx=%u mode=%u\n", n, (now - g_t0) / (double)g_freq, dt, index, mode);
    if (now - g_win_t >= g_freq * 2) {
        double secs = (now - g_win_t) / (double)g_freq;
        lg("STAT gnm: %.1f flips/s, max dt %.1f ms (window %.1fs)\n", g_win_n / secs, g_win_max, secs);
        g_win_t = now; g_win_n = 0; g_win_max = 0.0;
    }
}

static int dcb_mem_ok(uintptr_t a, size_t n)
{
    if (!a || !n || a + n < a) return 0;
    void *s = NULL, *en = NULL;
    int32_t prot = 0;
    if (sceKernelQueryMemoryProtection((void *)a, &s, &en, &prot) != 0) return 0;
    if ((uintptr_t)s > a || (uintptr_t)en < a + n) return 0;
    return (prot & 0x01 /*PROT_CPU_READ*/) != 0;
}

#if VCFG_BENCH
#include "bench_capture.h"
#endif

// Shared FG context initializer at frame INJECT_AFTER_FLIPS for both sceGnmSubmitAndFlipCommandBuffers and
// decoupled sceVideoOutSubmitFlip games.
static void vout_fg_ctx_init(uint32_t handle, int from_vout)
{
    g_inject_on = read_mode_raw();
    FILE *tf = fopen(LOG_DIR "/inject", "r");
    if (tf) {
        int ch = fgetc(tf);
        if (ch >= '1' && ch <= '8') g_inject_on = ch - '0';
        for (int k = 0; k < 8 && (ch = fgetc(tf)) != EOF; k++) {
            if (ch == 'p') g_opt_phase = 1;
            if (ch == 'e') g_opt_early = 1;
            if (ch == 'm') g_opt_marker = 1;
        }
        fclose(tf);
    }
    flush_map_run();
    if (g_dbg_hooks) { crash_load_modules(); crash_handlers_install(); }
    cfg_reload_from_disk(1);
    char safe_reason[sizeof(g_inert_reason)];
    if (g_safe_early) {
        safe_marker(2);
        STAGE("safe mode: the early start reached frame %d (%s) -> marker removed", INJECT_AFTER_FLIPS,
              from_vout ? "VideoOutSubmitFlip" : "SubmitAndFlip");
    }
    if (g_safe && g_safe_never) { g_safe_done = 1; snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: SAFE GIVEN UP"); }
    if (g_safe && !g_safe_done) {
        if (g_inject_on == 8) safe_try_activate(handle);
        else safe_refuse("MODE");
    }
    memcpy(safe_reason, g_inert_reason, sizeof(safe_reason));
    if (g_safe && !g_safe_active) g_inject_on = 0;   // unverified title that was refused: observe only
    if (!g_fmt_supported) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: HDR/FMT 0x%x", g_attr.format);
    else if (!fg_res_supported(g_bw, g_bh)) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: RES %ux%u", g_bw, g_bh);
    else if (!mbufs_ready(handle)) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO MBUF (%d)", g_nbuf);
    else if (g_detected_60fps && g_cfg.fps_lock != 30) snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
    else g_inert_reason[0] = 0;

    if (g_safe && !g_safe_active && safe_reason[0]) memcpy(g_inert_reason, safe_reason, sizeof(g_inert_reason));
    lg("injection mode %d (%s, title %s, known=%d, fg_off=%d, preset=%d, sharpness=%d%%, enhance=%d, overlay=%d, inert='%s'), work memory %s\n",
       g_inject_on, from_vout ? "vout_flip" : "gnm_flip", g_title_id, g_known_game, g_fg_off, g_cfg.preset, g_cfg.sharpness,
       g_cfg.upscale, g_cfg.overlay, g_inert_reason, g_work_garlic ? "garlic" : "onion");
    if (g_is_neo < 0) { g_is_neo = sceKernelIsNeoMode() ? 1 : 0; g_t_is_neo = (uint32_t)g_is_neo; }
    lg("console: neo=%d (T# alt_tile_mode t[6]=0x%08x), output %ux%u pitch %u tiling %u, tile index %u, overlay bitmaps %u\n",
       (int)g_is_neo, (unsigned)(g_is_neo && g_attr.tilingMode == 0 ? GNM_T6_ALT_TILE_MODE_NEO : 0u), g_bw, g_bh, g_pitch,
       (unsigned)g_attr.tilingMode, (unsigned)(g_attr.tilingMode == 0 ? 10u : 8u), (unsigned)VOV_NBUF);
    tile_load();
    lg("tile index: override %d, sweep %d (sweep default off; L3 + D-pad Left/Right changes the index in game)\n", (int)g_tile_override, (int)g_tile_sweep);
    if (g_inject_on >= 6) mon_start();
    gv_set_display(g_buf, g_nbuf, g_bw, g_bh);
    gv_start(g_inject_on == 8 && fg_res_supported(g_bw, g_bh));
}

static int32_t flip_inner(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    uint64_t t_hook_in = sceKernelGetProcessTime();
    if (index < 16u) __atomic_store_n(&g_sub_t[index], t_hook_in, __ATOMIC_RELEASE);
    uint64_t prev_flip_t = g_last;
    g_flips++;
    if (g_flips == 1) {
        lg("first SubmitAndFlip: count=%u flipMode=%u title=%s\n", count, flipMode, g_title_id);
        lg("CLASSIC_BUILD version=%s test=%d target=%d two_buffer=%d status_hook=%d\n", VCFG_TRACE_VERSION, VCFG_2BUF_TEST, tb_title(),
           g_vq_on, g_status_hooked);
    }
    if (g_rel_t) {
        uint64_t us = sceKernelGetProcessTime() - g_rel_t;
        g_rel_t = 0;
        if (us < 1000000) { g_cpu_n++; g_cpu_sum_us += (uint32_t)us; if (us > g_cpu_max_us) g_cpu_max_us = (uint32_t)us;
                            g_cpu_ema_us = g_cpu_ema_us ? (g_cpu_ema_us * 7u + (uint32_t)us) / 8u : (uint32_t)us; }
    }
    tick(g_flips, handle, index, flipMode);
    uint64_t raw_dt_us = (prev_flip_t && g_freq) ? ((g_last - prev_flip_t) * 1000000ull / g_freq) : 33366ull;
#if VCFG_BENCH
    if (g_bench_enabled) return bench_flip(count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg, t_hook_in);
#endif

#if INJECT
    if (g_flips == INJECT_AFTER_FLIPS) {
        vout_fg_ctx_init(handle, 0);
    }

    if (g_inject_on && g_flips >= INJECT_AFTER_FLIPS && count >= 1 && count <= (gv_armed() ? GV_MAXE - 1u : 6u) && dcb && dcbSz && index < (uint32_t)g_nbuf) {
        int dcb_valid = 1;
        for (uint32_t i = 0; i < count; i++) {
            if (!dcb[i] || !dcbSz[i] || (dcbSz[i] & 3u) || dcbSz[i] > (16u << 20) || !dcb_mem_ok((uintptr_t)dcb[i], dcbSz[i])) {
                dcb_valid = 0;
                static int dcb_err_logged = 0;
                if (!dcb_err_logged) {
                    dcb_err_logged = 1;
                    lg("fallback: invalid DCB[%u] ptr=%p sz=%u -> passing original SubmitAndFlip unchanged\n", i, dcb[i], dcbSz[i]);
                }
                break;
            }
        }
        if (dcb_valid && g_gpu_state == 0) gpu_init();
        if (dcb_valid && g_gpu_state == 1) {
            uint32_t last = count - 1;
            uint32_t nd = dcbSz[last] / 4;
            const uint32_t *tail = nd >= 64 ? (const uint32_t *)dcb[last] + (nd - 64) : NULL;
            // GTA V (CUSA00411): the same 64-dword flip packet but its second dword is 0x68750778 instead of 0x68750777
            if (tail && tail[0] == 0xC03E1000u && (tail[1] >> 8) == 0x687507u) {
                uint32_t slot = g_flips & 3;
                int dcb8 = g_inject_on == 8 && g_gpu_sz == GPU_SZ;
                uint32_t *mine = (uint32_t *)(g_gpu + (dcb8 ? DCB8_OFF + slot * DCB8_SZ : SLOT0 + slot * SLOT_SZ));
                volatile uint32_t *scr = (volatile uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0xA00);
                if (g_pend_seq && g_flips - g_pend_seq >= 4) {
                    volatile uint32_t *ps = (volatile uint32_t *)(g_gpu + SLOT0 + g_pend_slot * SLOT_SZ + 0xA00);
                    volatile uint64_t *pl = (volatile uint64_t *)(g_gpu + SLOT0 + g_pend_slot * SLOT_SZ + 0x900);
                    lg("selftest seq=%u: label=%llu scratch[0]=%08x [1]=%08x [63]=%08x [255]=%08x\n", g_pend_seq,
                       (unsigned long long)*pl, ps[0], ps[1], ps[63], ps[255]);
                    g_pend_seq = 0;
                }
                if (g_inject_on >= 6) {
                    uint32_t ps = (g_flips + 1) & 3;
                    if (g_inject_on == 8) gv_slot_stats(ps);
                    volatile uint64_t *ts = (volatile uint64_t *)(g_gpu + SLOT0 + ps * SLOT_SZ + 0x910);
                    uint64_t cpu = sceKernelGetProcessTimeCounter();
                    if (ts[0] && ts[4] > ts[0] && g_ts_slot_seen != ts[0]) {
                        g_ts_slot_seen = ts[0];
                        if (!g_ts_first) { g_ts_first = ts[0]; g_ts_cpu_first = g_slot_cpu[ps]; }
                        g_ts_last = ts[0]; g_ts_cpu_last = g_slot_cpu[ps];
                        for (int i = 0; i < 4; i++) g_ts_pass[i] += (ts[i + 1] > ts[i]) ? ts[i + 1] - ts[i] : 0;
                        {
                            volatile uint64_t *t2 = (volatile uint64_t *)(g_gpu + SLOT0 + ps * SLOT_SZ + 0x980);
                            for (int i = 0; i < 5; i++) g_praw[i] = ts[i];
                            for (int i = 0; i < 7; i++) g_praw[5 + i] = t2[i];
                            if (t2[4]) {
                                uint64_t mk[9] = { ts[0], ts[1], t2[0], t2[1], t2[2], t2[3], ts[2], ts[3], t2[4] };
                                for (int i = 0; i < 8; i++) g_pp[i] += (mk[i + 1] > mk[i]) ? mk[i + 1] - mk[i] : 0;
                                g_pp[8] += (ts[4] > t2[4]) ? ts[4] - t2[4] : 0;
                            }
                        }
                        g_ts_n++;
                        if (g_inject_on == 8) {
                            volatile uint32_t *stv = (volatile uint32_t *)(g_gpu + SLOT0 + ps * SLOT_SZ + STATS_OFF);
                            uint32_t cn = stv[0], fgn = stv[3];
                            if (cn != 0xFFFFFFFFu) {
                                g_st_n++; g_st_cut_sum += cn; g_st_fg_sum += fgn;
                                if (cn > g_st_cut_max) g_st_cut_max = cn;
                                uint32_t cb_thr = cut_blocks8() ? cut_blocks8() * 8u : CUT_BLOCKS;
                                if (cn > cb_thr) g_st_cuts++;
                                if (fgn) g_st_fgf++;
                                if (stv[4] == 1u) g_st_fades++;
                                g_st_gmx = (int32_t)stv[1]; g_st_gmy = (int32_t)stv[2];
                            }
                        }
                        {
                            volatile uint64_t *tq = (volatile uint64_t *)(g_gpu + SLOT0 + ((ps + 3) & 3) * SLOT_SZ + 0x910);
                            if (tq[4] && ts[0] > tq[4] && ts[0] - tq[4] < (ts[4] - ts[0]) * 1000) {
                                uint64_t sp = ts[0] - tq[4];
                                g_ts_span += sp; g_ts_span_n++;
                                if (sp > g_ts_span_max) g_ts_span_max = sp;
                            }
                        }
                        if (g_ts_n >= 90 && g_ts_cpu_last > g_ts_cpu_first) {
                            double secs = (double)(g_ts_cpu_last - g_ts_cpu_first) / (double)g_freq;
                            double unit = (double)(g_ts_last - g_ts_first) / secs;
                            gv_set_gpu_rate(unit);
                            double ms[4], tot = 0;
                            for (int i = 0; i < 4; i++) { ms[i] = (double)g_ts_pass[i] / (double)g_ts_n / unit * 1000.0; tot += ms[i]; }
                            double span = g_ts_span_n ? (double)g_ts_span / g_ts_span_n / unit * 1000.0 : 0.0;
                            double spanmax = (double)g_ts_span_max / unit * 1000.0;
                            g_ts_span = g_ts_span_max = 0; g_ts_span_n = 0;
                            if (g_inject_on == 8) {
                                double gpu_act = ms[0] + ms[1] + ms[3];
                                g_last_gpu_ms_x100 = (uint32_t)(gpu_act * 100.0 + 0.5);
                                lg("GPU time of our passes: pyramid %.2f + motion %.2f + warp/post %.2f = %.2f ms (+ fence wait %.2f) | "
                                   "game span avg %.1f max %.1f ms\n", ms[0], ms[1], ms[3], gpu_act, ms[2], span, spanmax);
                                lg("PASSES ms: pyr8 %.2f me3 %.2f me2 %.2f gm8+fgs8 %.2f me1 %.2f mf8 %.2f fence %.2f phase8 %.2f warp8+rcas+osd %.2f\n",
                                   (double)g_pp[0] / g_ts_n / unit * 1000.0, (double)g_pp[1] / g_ts_n / unit * 1000.0, (double)g_pp[2] / g_ts_n / unit * 1000.0,
                                   (double)g_pp[3] / g_ts_n / unit * 1000.0, (double)g_pp[4] / g_ts_n / unit * 1000.0, (double)g_pp[5] / g_ts_n / unit * 1000.0,
                                   (double)g_pp[6] / g_ts_n / unit * 1000.0, (double)g_pp[7] / g_ts_n / unit * 1000.0, (double)g_pp[8] / g_ts_n / unit * 1000.0);
                                lg("PASS RAW last frame: ts %llu %llu %llu %llu %llu | t2 %llu %llu %llu %llu %llu %llu %llu\n", (unsigned long long)g_praw[0], (unsigned long long)g_praw[1], (unsigned long long)g_praw[2], (unsigned long long)g_praw[3], (unsigned long long)g_praw[4], (unsigned long long)g_praw[5], (unsigned long long)g_praw[6], (unsigned long long)g_praw[7], (unsigned long long)g_praw[8], (unsigned long long)g_praw[9], (unsigned long long)g_praw[10], (unsigned long long)g_praw[11]);
                                for (int i = 0; i < 10; i++) g_pp[i] = 0;
                                if (g_st_n)
                                    lg("FRAMES %u: unmatched blocks avg %.0f max %u, scene-cut frames %u, fade frames %u | foreground: "
                                       "in %u frames, avg %.1f L2 blocks | camera %d,%d L2 px | log lines dropped %u\n", g_st_n,
                                       (double)g_st_cut_sum / g_st_n, g_st_cut_max, g_st_cuts, g_st_fades, g_st_fgf,
                                       (double)g_st_fg_sum / g_st_n, g_st_gmx, g_st_gmy, g_lgq_drop);
                                g_st_n = g_st_cut_max = g_st_cuts = g_st_fgf = g_st_fades = 0; g_st_cut_sum = g_st_fg_sum = 0;
                            }
                            else
                                lg("GPU time of our passes: total %.2f ms = luma %.2f + search %.2f + refine %.2f + warp %.2f\n", tot, ms[0], ms[1], ms[2], ms[3]);
                            g_ts_n = 0; g_ts_first = 0; for (int i = 0; i < 4; i++) g_ts_pass[i] = 0;
                        }
                    }
                    g_slot_cpu[slot] = cpu;
                }

                uint32_t n = 0;
                uint32_t flip_idx = index, show_idx = index;   // show_idx: buffer flipped for the real frame
                uint32_t mid2_flip_idx = 0;
                int second_flip = 0;
                int third_flip = 0;
                uint32_t hold_mid_us = 0;

                int can_fg8 = (g_inject_on == 8 && g_work && g_sh8_ok && g_fmt_supported &&
                               fg_res_supported(g_bw, g_bh) && g_prev_idx < game_idx_end() && mbufs_ready(handle));
                int want_fg = (!g_fg_off || g_ab_x_flag) && (!g_detected_60fps || g_cfg.fps_lock == 30);

                if (can_fg8 && want_fg) {
                    g_inert_reason[0] = 0;
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t tidx = tile_idx_now();
                    int pcopy = g_pcopy_avail && g_cfg.present_copy != 2;   // auto and on: available when 4..6 buffers allocated
                    g_pcopy_on = pcopy;
                    uint32_t mi1 = pcopy ? (uint32_t)g_mbase + (g_flips % (uint32_t)g_pcopy_k) : (uint32_t)g_mbase + (g_flips % (uint32_t)g_nmid);
                    uint32_t mi2 = (uint32_t)g_mbase + ((g_flips + 1u) % (uint32_t)g_nmid);
                    int tb_fast = VCFG_2BUF_TEST == 3 && tb_title() && pcopy && !g_fg_off;
                    static uint32_t tb_mid_cursor;
                    if (tb_fast) {
                        mi1 = tb_mid_index((uint32_t)g_mbase, (uint32_t)g_pcopy_k, tb_mid_cursor, 0);
                        mi2 = tb_mid_index((uint32_t)g_mbase, (uint32_t)g_pcopy_k, tb_mid_cursor, 1);
                    }
                    uint32_t t1_q16 = 32768u, t2_q16 = 43690u;
                    int two_mids = 0;
                    int force_repeat = (raw_dt_us > 66667ull) ? 1 : 0; // >66.7 ms (<15 fps hitch) -> repeat frame

                    if ((g_cfg.preset == 1 || tb_fast) && !g_fg_off) {
                        // 1.1 FG Fast (aggressive 60 Hz output target for 20..40 fps):
                        if (force_repeat) {
                            g_fast_acc_q16 = 0;
                            t1_q16 = 0;
                        } else {
                            uint32_t dt_clamped = g_ema_frame_us;
                            if (dt_clamped < 25000u) dt_clamped = 25000u; // 40 fps
                            if (dt_clamped > 50000u) dt_clamped = 50000u; // 20 fps
                            int32_t r_q16 = (int32_t)(((uint64_t)dt_clamped << 16) / 16683ull);
                            int32_t acc_prev = g_fast_acc_q16;
                            int32_t acc_new = acc_prev + r_q16;
                            int m_out = (int)((acc_new + 32768) >> 16);
                            if (m_out < 2) m_out = 2;
                            if (m_out > 3) m_out = 3;
                            // Queue backpressure guard: drop to M=2 if flip queue has >=3 pending flips
                            if (__atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE) >= 3u) m_out = 2;
                            g_fast_acc_q16 = acc_new - (m_out << 16);
                            if (g_fast_acc_q16 < -49152) g_fast_acc_q16 = -49152;
                            if (g_fast_acc_q16 >  49152) g_fast_acc_q16 =  49152;

                            int32_t phase_shift = (acc_prev * 3) >> 5; // smooth sub-vblank phase offset (~0.1 * acc_prev)
                            if (m_out == 3) {
                                int32_t p1 = 21845 + phase_shift; // ~1/3 (0.333)
                                int32_t p2 = 43690 + phase_shift; // ~2/3 (0.667)
                                if (p1 < 16384) p1 = 16384; if (p1 > 27525) p1 = 27525;
                                if (p2 < 38010) p2 = 38010; if (p2 > 49152) p2 = 49152;
                                t1_q16 = (uint32_t)p1;
                                t2_q16 = (uint32_t)p2;
                                if ((tb_fast ? g_pcopy_k : g_nmid) >= 2 && mi1 != mi2) {
                                    two_mids = 1;
                                    mid2_flip_idx = mi2;
                                    third_flip = 1;
                                } else if (g_ngame != 2 && !g_mem_tight && g_cpu_ema_us > 30000u) {
                                    // Single mid-buffer fallback (only when CPU itself > 30 ms): hold mid1 for 2 vblanks and cur for 1 vblank
                                    t1_q16 = 29491u; // t = 0.45
                                    hold_mid_us = 15500u;
                                } else {
                                    t1_q16 = 32768u;
                                }
                            } else {
                                int32_t p1 = 32768 + phase_shift;
                                if (p1 < 22937) p1 = 22937; if (p1 > 42598) p1 = 42598;
                                t1_q16 = (uint32_t)p1;
                            }
                        }
                    } else if (!g_fg_off) {
                        // 1.1 FG Quality (strict 2x): 1 mid frame per real frame, real-timestamp midpoint phase & even hold
                        uint32_t prev_hold = __atomic_load_n(&g_last_real_hold_us, __ATOMIC_ACQUIRE);
                        uint32_t tot_us = g_ema_frame_us > 20000u ? g_ema_frame_us : 33366u;
                        uint32_t t_real = (prev_hold << 16) / tot_us;
                        // Blend 50/50 with 0.5 (32768) for jitter-free midpoint placement
                        t1_q16 = (t_real + 32768u) >> 1;
                        if (t1_q16 < 22937u) t1_q16 = 22937u; // 0.35
                        if (t1_q16 > 42598u) t1_q16 = 42598u; // 0.65
                        // Only when the game itself is slow: the 15 ms hold sleeps in the game's thread, so applying it to a
                        // game that needs ~26 ms per frame kept it below 26 fps for good (CUSA05571: 24-25 fps).
                        // Never sleep 15 ms on 2-buffer games (g_ngame == 2), where CPU time includes flip-queue waits.
                        if (g_ngame != 2 && !g_mem_tight && tot_us > 38000u && g_cpu_ema_us > 30000u) {
                            // Distribute extra vblanks evenly between mid and cur when game drops below 30 fps (e.g. 25 fps -> 50)
                            uint32_t half_us = tot_us >> 1;
                            int32_t extra_q16 = (int32_t)(((uint64_t)(half_us > 16683u ? half_us - 16683u : 0u) << 16) / 16683ull);
                            g_q2x_hold_acc += extra_q16;
                            if (g_q2x_hold_acc >= 32768 && __atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE) <= 2u) {
                                g_q2x_hold_acc -= 65536;
                                hold_mid_us = 15000u;
                                t1_q16 = 32768u;
                            }
                        } else {
                            g_q2x_hold_acc = 0;
                        }
                    }

                    uint32_t ci = 0;
                    if (pcopy) {
                        static uint32_t pc_flip;
                        pc_flip = (pc_flip + 1u) % (uint32_t)g_pcopy_k;
                        ci = (uint32_t)g_mbase + (uint32_t)g_pcopy_k + pc_flip;
                        if (!tb_fast) { two_mids = 0; third_flip = 0; }   // FAST 3-step needs a second mid buffer: only the test-3 pulldown keeps it
                        show_idx = ci;
                        if (index < 16u && ci < 16u) __atomic_store_n(&g_sub_t[ci], __atomic_load_n(&g_sub_t[index], __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
                    }
                    if (tb_fast) {
                        tb_mid_cursor = (tb_mid_cursor + (two_mids ? 2u : 1u)) % (uint32_t)g_pcopy_k;
                        if ((g_flips % 60u) == 0)
                            lg("PULLDOWN dt_us=%u mids=%d mi1=%u mi2=%u copy=%u pending=%u\n", g_ema_frame_us,
                               two_mids ? 2 : 1, mi1, mi2, ci, __atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE));
                    }
                    n = build_dcb_flow8(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi1], g_buf[mi2], g_pitch,
                                        tidx, px * 4, slot, g_flips, tail, (int32_t)handle, mi1, mi2,
                                        t1_q16, t2_q16, two_mids, force_repeat, pcopy ? g_buf[ci] : 0ull, ci);
                    flip_idx = mi1;
                    second_flip = 1;
                } else if (g_inject_on == 8 && g_sh8_ok && g_fmt_supported && g_bw >= 640 && g_bh >= 360 && g_bw <= 3840 && g_bh <= 2160) {
                    // 1.4: Native / Post-only path (FG off in menu, or 60fps game): draw OSD menu/overlay + RCAS/Enhance on cur.
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t tidx = tile_idx_now();
                    n = build_dcb_post8(mine, g_buf[index], g_bw, g_bh, g_pitch, tidx, px * 4, slot, g_flips, tail);
                } else if ((g_inject_on == 4 || g_inject_on == 6 || g_inject_on == 7) && g_prev_idx < game_idx_end() && mbufs_ready(handle)) {
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t mi = (uint32_t)g_mbase + (g_flips % (uint32_t)g_nmid);
                    if (g_inject_on >= 6 && g_work && g_bw >= 1280 && (g_bw % 640) == 0 && g_bh == g_bw * 9 / 16 && g_bw / 640 <= 4)
                        n = build_dcb_flow(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi], g_bw, g_bh, g_pitch,
                                           tile_idx_now(), px * 4, slot, g_flips, tail,
                                           (int32_t)handle, mi, g_inject_on >= 7);
                    else
                        n = build_dcb_ghost(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi], (px + 63) / 64, px * 4, slot, g_flips, tail);
                    flip_idx = mi;
                    second_flip = 1;
                } else if (g_inject_on == 2 || g_inject_on == 3) {
                    uint32_t px = g_pitch * ((g_bh + 63u) & ~63u);
                    uint64_t prev = (g_inject_on == 3) ? g_buf[index] : g_buf[g_prev_idx < (uint32_t)g_nbuf ? g_prev_idx : index];
                    n = build_dcb_ghost(mine, prev, g_buf[index], g_buf[index], (px + 63) / 64, px * 4, slot, g_flips, tail);
                } else if (g_inject_on == 5) {
                    uint32_t px = g_pitch * ((g_bh + 63u) & ~63u);
                    uint32_t tidx = tile_idx_now();
                    n = build_dcb_rect(mine, g_buf[index], g_bw, g_bh, g_pitch, tidx, 32, 16, px * 4, slot, g_flips, tail);
                } else if (g_inject_on == 1) {
                    if ((g_flips & 7) == 1 && !g_pend_seq) {
                        for (int i = 0; i < 64; i++) scr[i] = 0xDEADBEEFu;
                        n = build_dcb(mine, (uint64_t)(uintptr_t)scr, 1, 256, slot, g_flips, tail);
                        g_pend_seq = g_flips; g_pend_slot = slot;
                    } else {
                        n = build_dcb(mine, g_buf[index], 1024, 0x40000, slot, g_flips, tail);
                    }
                }

                int pace_p2 = (int)g_prev2_idx, pace_p1 = (int)g_prev_idx;
                g_prev2_idx = g_prev_idx;
                g_prev_idx = index;

                if (n == 0) {
                    // Nothing injected (e.g. Native mode with OSD/RCAS off, or first frame before g_prev_idx is valid)
                    int32_t rc0 = HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                         int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                         count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 30 && !g_detected_60fps && want_fg) pace_hz(30);
                    return rc0;
                }

                void *d2[GV_MAXE]; uint32_t s2[GV_MAXE]; void *c2[GV_MAXE]; uint32_t cs2[GV_MAXE];
                if (n * 4u > (dcb8 ? DCB8_SZ : 0x800u)) {
                    static int warned = 0;
                    if (!warned) { warned = 1; lg("ERROR: our DCB is %u dwords, memory layout overlap\n", n); }
                }
                for (uint32_t i = 0; i < count; i++) { d2[i] = dcb[i]; s2[i] = dcbSz[i]; c2[i] = ccb ? ccb[i] : 0; cs2[i] = ccbSz ? ccbSz[i] : 0; }
                uint32_t ncount = count + 1;
                if (s2[last] == 64 * 4) {
                    d2[last] = mine; s2[last] = n * 4;
                    {
                        static int ccb_logged = 0;
                        if (!ccb_logged && ccb && ccbSz && ccb[last] && ccbSz[last]) {
                            ccb_logged = 1;
                            lg("WARNING: the replaced flip entry had a CCB of %u bytes (dropped, as always): report this log\n", ccbSz[last]);
                        }
                    }
                    c2[last] = 0; cs2[last] = 0; ncount = count;
                } else {
                    s2[last] -= 64 * 4;
                    d2[count] = mine; s2[count] = n * 4; c2[count] = 0; cs2[count] = 0;
                }
                if (g_flips >= INJECT_AFTER_FLIPS && g_flips < INJECT_AFTER_FLIPS + 3) {
                    lg("submit args #%u: count=%u ccb=%p ccbSz=%p handle=%u index=%u flipMode=%u flipArg=%lld\n", g_flips, count,
                       (void *)ccb, (void *)ccbSz, handle, index, flipMode, (long long)flipArg);
                    for (uint32_t i = 0; i < count; i++)
                        lg("  [%u] dcb=%p sz=%u  ccb=%p sz=%u\n", i, dcb[i], dcbSz[i], ccb ? ccb[i] : NULL, ccbSz ? ccbSz[i] : 0u);
                }
                if (g_flips == INJECT_AFTER_FLIPS && dcb[last] && dcb_mem_ok((uintptr_t)dcb[last], (size_t)nd * 4u)) {
                    const uint32_t *dw = (const uint32_t *)dcb[last];
                    uint32_t nn = nd > 160u ? 160u : nd;
                    lg("flip DCB dump #%u: count=%u last=%u nd=%u (first %u dwords)\n", g_flips, count, last, nd, nn);
                    for (uint32_t i = 0; i < nn; i += 8) {
                        lg("  %03u: %08x %08x %08x %08x %08x %08x %08x %08x\n", i, dw[i], i+1<nn?dw[i+1]:0, i+2<nn?dw[i+2]:0, i+3<nn?dw[i+3]:0,
                           i+4<nn?dw[i+4]:0, i+5<nn?dw[i+5]:0, i+6<nn?dw[i+6]:0, i+7<nn?dw[i+7]:0);
                    }
                }
                if (g_flips == INJECT_AFTER_FLIPS || (g_flips % 600) == 0)
                    lg("inject #%u: count %u->%u, our DCB %u dw, out buf[%u]=%llx\n", g_flips, count, ncount, n, index, (unsigned long long)g_buf[index]);
                uint32_t *fb2 = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + SLOT_FB2_OFF);
                uint32_t *fb  = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0xE00);
                if (third_flip)  { for (int i = 0; i < 64; i++) fb2[i] = tail[i]; }
                if (second_flip) { for (int i = 0; i < 64; i++) fb[i]  = tail[i]; }
                static int last_show = -1;                  // with present copy the shown real buffers are the copies
                int pace_pre = (g_cfg.pace_order == 1) ? 1 : (g_cfg.pace_order == 2) ? 0 : (!g_pcopy_on);
                g_last_pace_pre = pace_pre;
                uint64_t t_p0 = sceKernelGetProcessTime(), t_p1 = t_p0;
                if (pace_pre) {
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 60 && !second_flip) pace_hz(60);
                    else if (g_inject_on == 8 && second_flip && pace_timer_on()) pace_pair();
                    else if (g_inject_on >= 7 && second_flip) {
                        int shown = (int)show_idx, pp1 = show_idx != index ? last_show : pace_p1;
                        pace_vblank(show_idx != index ? -1 : pace_p2, pp1, shown);
                        last_show = shown;
                    }
                    else if (second_flip) pace_30();
                    t_p1 = sceKernelGetProcessTime();
                }
                uint64_t t_s1 = t_p1;
                int32_t rc = HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                     int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                     ncount, d2, s2, ccb ? c2 : ccb, ccbSz ? cs2 : ccbSz, handle, flip_idx, flipMode, flipArg);
                uint64_t t_s1e = sceKernelGetProcessTime(), t_hold = t_s1e, t_s2e = t_s1e;
                if (third_flip) {
                    void *dm2[1] = { fb2 };
                    uint32_t sm2[1] = { 64 * 4 };
                    HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                  int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                  1, dm2, sm2, NULL, NULL, handle, mid2_flip_idx, flipMode, flipArg);
                }
                if (second_flip) {
                    if (hold_mid_us > 0) sceKernelUsleep(hold_mid_us);
                    t_hold = sceKernelGetProcessTime();
                    void *d3[1] = { fb };
                    uint32_t s3[1] = { 64 * 4 };
                    // Experiment 2: the first mid keeps the game's opaque argument;
                    // tag only the real copy. This does not virtualize status or labels.
                    int64_t real_arg = flipArg;
                    if (VCFG_2BUF_TEST == 2 && tb_title() && g_pcopy_on && show_idx != index) {
                        real_arg = tb_tag_real_arg(flipArg);
                        if (g_flips < INJECT_AFTER_FLIPS + 5)
                            lg("FLIP_ARGS mid=%lld real=%lld mid_buf=%u real_buf=%u\n", (long long)flipArg,
                               (long long)real_arg, flip_idx, show_idx);
                    }
                    int32_t r2 = HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                     int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                     1, d3, s3, NULL, NULL, handle, show_idx, flipMode, real_arg);
                    t_s2e = sceKernelGetProcessTime();
                    if (g_flips < INJECT_AFTER_FLIPS + 12) lg("mode4 #%u: gnm flip(mid idx %u)=0x%08x, gnm flip(cur idx %u)=0x%08x\n", g_flips, flip_idx, (unsigned)rc, index, (unsigned)r2);
                }
                if (!pace_pre) {
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 60 && !second_flip) pace_hz(60);
                    else if (g_inject_on == 8 && second_flip && pace_timer_on()) pace_pair();
                    else if (g_inject_on >= 7 && second_flip) {
                        int shown = (int)show_idx, pp1 = show_idx != index ? last_show : pace_p1;
                        pace_vblank(show_idx != index ? -1 : pace_p2, pp1, shown);
                        last_show = shown;
                    }
                    else if (second_flip) pace_30();
                } else if (second_flip) {
                    g_rel_t = t_s2e;
                }
                if (second_flip) {   // where the game's thread spends its time inside this hook (FG frames)
                    static uint64_t a_build, a_sub, a_hold, a_flip2, a_pace, a_n;
                    uint64_t t_end = sceKernelGetProcessTime();
                    a_build += t_p0 - t_hook_in; a_sub += t_s1e - t_s1; a_hold += t_hold - t_s1e; a_flip2 += t_s2e - t_hold;
                    a_pace += pace_pre ? (t_p1 - t_p0) : (t_end - t_s2e); a_n++;
                    if (a_n >= 60) {
                        double k = 1.0 / 1000.0 / (double)a_n;
                        lg("HOOK ms per FG frame: build %.1f submit %.1f hold %.1f flip2 %.1f pace %.1f total %.1f | game own %.1f | pending %u\n",
                           a_build * k, a_sub * k, a_hold * k, a_flip2 * k, a_pace * k, (a_build + a_sub + a_hold + a_flip2 + a_pace) * k,
                           g_cpu_ema_us / 1000.0, __atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE));
                        a_build = a_sub = a_hold = a_flip2 = a_pace = a_n = 0;
                    }
                }
                return rc;
            } else if (g_flips == INJECT_AFTER_FLIPS) {
                snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO FLIP TAIL");
                lg("inject skipped: tail marker not found (nd=%u)\n", nd);
                {   // diagnostic for games whose flip tail differs: dump the last DCB (up to 160 dwords) and the marker search result
                    static int dumped = 0;
                    if (!dumped && dcb[last] && dcb_mem_ok((uintptr_t)dcb[last], (size_t)nd * 4u)) {
                        dumped = 1;
                        const uint32_t *dw = (const uint32_t *)dcb[last];
                        uint32_t nn = nd > 160u ? 160u : nd;
                        lg("flip DCB dump: count=%u last=%u nd=%u (first %u dwords)\n", count, last, nd, nn);
                        for (uint32_t i = 0; i < nn; i += 8) {
                            lg("  %03u: %08x %08x %08x %08x %08x %08x %08x %08x\n", i, dw[i], i+1<nn?dw[i+1]:0, i+2<nn?dw[i+2]:0, i+3<nn?dw[i+3]:0,
                               i+4<nn?dw[i+4]:0, i+5<nn?dw[i+5]:0, i+6<nn?dw[i+6]:0, i+7<nn?dw[i+7]:0);
                        }
                        for (uint32_t i = 0; i + 1 < nd; i++)
                            if (dw[i] == 0xC03E1000u && dw[i+1] == 0x68750777u) lg("  marker at dword %u (tail would be %u dwords)\n", i, nd - i);
                    }
                }
            }
        }
    }
#endif
    return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                         int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                         count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
}

#include "vout_trace.h"

int32_t sceGnmSubmitAndFlipCommandBuffers_hook(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    tb_on_game_submit();            // test builds: the status poll that let the game out of its wait
    static uint32_t s_boot_flips;   // the first submits are boot stages too (B01: starts that end before the first one)
    uint32_t bf = s_boot_flips < 3 ? ++s_boot_flips : 0;
    if (bf) STAGE("SubmitAndFlip #%u enter: count=%u handle=%u index=%u flipMode=%u", bf, count, handle, index, flipMode);
    uint32_t gn = gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 1);
    if (gn) { count = gn; dcb = gd; dcbSz = gs; if (ccb) ccb = gc; if (ccbSz) ccbSz = gcs; }
    g_in_submit++;
    int32_t rc = flip_inner(count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    g_in_submit--;
    if (bf) STAGE("SubmitAndFlip #%u done = 0x%08x", bf, (unsigned)rc);
    gv_frame_end();
    return rc;
}

// Ring buffer of recent sceGnmSubmitCommandBuffers calls for games that decouple GPU submit and sceVideoOutSubmitFlip.
#define VO_SUB_RING_N 8
typedef struct {
    uint64_t ts_us;
    uint32_t count;
    uint32_t last_sz;
    uintptr_t last_dcb;
} VoSubEntry;
static VoSubEntry g_vo_sub_ring[VO_SUB_RING_N];
static volatile uint32_t g_vo_sub_w = 0;

int32_t sceGnmSubmitCommandBuffers_hook(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz)
{
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    uint32_t gn = g_in_submit ? 0u : gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 0);
    if (!g_in_submit && count >= 1 && dcb && dcbSz) {
        uint32_t wi = __atomic_fetch_add(&g_vo_sub_w, 1u, __ATOMIC_ACQ_REL) % VO_SUB_RING_N;
        g_vo_sub_ring[wi].ts_us = sceKernelGetProcessTime();
        g_vo_sub_ring[wi].count = count;
        g_vo_sub_ring[wi].last_sz = dcbSz[count - 1];
        g_vo_sub_ring[wi].last_dcb = (uintptr_t)dcb[count - 1];
    }
    if (g_vt_on && count >= 1 && count <= 16 && dcb && dcbSz)
        vt_rec(VT_SUBMIT, count, dcbSz[count - 1], ccbSz ? ccbSz[count - 1] : 0, (uint32_t)(uintptr_t)dcb[count - 1], (const uint32_t *)dcb[count - 1], dcbSz[count - 1]);
    int32_t rc;
    g_in_submit++;
    if (gn)
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers, int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                           gn, gd, gs, ccb ? gc : NULL, ccbSz ? gcs : NULL);
    else
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers, int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                           count, dcb, dcbSz, ccb, ccbSz);
    g_in_submit--;
    return rc;
}

static void wl_count(int k, const char *name)
{
    if (g_in_submit) { g_wl_nested[k]++; return; }
    uint32_t n = ++g_wl_direct[k];
    if (n == 1 || (n % 600u) == 0)
        lg("DIAG: the game calls %s directly (%u calls, %u nested in ours): those submits bypass frame generation and the "
           "scanner\n", name, n, g_wl_nested[k]);
}
int32_t sceGnmSubmitCommandBuffersForWorkload_hook(uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz)
{
    wl_count(0, "sceGnmSubmitCommandBuffersForWorkload");
    return HOOK_CONTINUE(sceGnmSubmitCommandBuffersForWorkload,
                         int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *),
                         workload, count, dcb, dcbSz, ccb, ccbSz);
}
int32_t sceGnmSubmitAndFlipCommandBuffersForWorkload_hook(uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[],
                                                          uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    wl_count(1, "sceGnmSubmitAndFlipCommandBuffersForWorkload");
    return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffersForWorkload,
                         int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                         workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
}

// Submit our standalone compute DCB via sceGnmSubmitCommandBuffers for decoupled VideoOutSubmitFlip games.
// Why we do not need the 64-dword prepareFlip tail here:
//   - In sceGnmSubmitAndFlipCommandBuffers games, our DCB replaces the last 64 dwords of the game's DCB, so we copy
//     those 64 dwords (the prepareFlip EOP label write that the kernel's Gnm flip packet waits on) to the end of mine.
//   - In decoupled sceVideoOutSubmitFlip games, the game has ALREADY submitted its rendering DCB via
//     sceGnmSubmitCommandBuffers on the same graphics queue (Ring 0). Submitting our compute DCB afterwards on the
//     same in-order ring executes strictly after the game's frame rendering finishes (enforced by our CS/CB flush and
//     WaitRegMem at the start of build_dcb_flow8 / build_dcb_post8), and its trailing RELEASE_MEM + WaitRegMem on
//     slotb+0x900 completes before the subsequent sceVideoOutSubmitFlip scanout flip is consumed by DCE.
static const uint32_t k_nop64_pkt[64] = { PKT3(0x10 /*NOP*/, 63, 0) };
static int32_t vout_submit_dcb(uint32_t *mine, uint32_t n_dwords)
{
    // Strip the trailing 64-dword NOP placeholder if present so we don't send unnecessary padding.
    uint32_t send_dw = (n_dwords > 64u && mine[n_dwords - 64u] == k_nop64_pkt[0]) ? (n_dwords - 64u) : n_dwords;
    void *d2[1] = { mine };
    uint32_t s2[1] = { send_dw * 4u };
    g_in_submit++;
    int32_t rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers,
                               int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                               1u, d2, s2, NULL, NULL);
    g_in_submit--;
    return rc;
}

// Full FrameGen + OSD/RCAS pipeline for games that separate sceGnmSubmitCommandBuffers and sceVideoOutSubmitFlip.
int32_t sceVideoOutSubmitFlip_hook(int32_t handle, int32_t index, uint32_t mode, int64_t arg)
{
    if (g_vt_hooked && !g_in_submit) {
        if (g_vo_submit_flips + 1u >= VT_START_FLIP && !g_vt_dumped) g_vt_on = 1;
        vt_rec(VT_FLIP, (uint32_t)index, mode, (uint32_t)arg, (uint32_t)handle, NULL, 0);
    }
    // If called from inside sceGnmSubmitAndFlipCommandBuffers (or while SubmitAndFlip is active), pass through.
    if (g_in_submit || g_flips > 0 || index < 0) {
        int32_t vr = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t), handle, index, mode, arg);
        if (g_vt_hooked && g_vt_n >= VT_N) vt_dump();
        return vr;
    }

    uint64_t t_hook_in = sceKernelGetProcessTime();
    uint32_t u_idx = (uint32_t)index;
    if (u_idx < 16u) __atomic_store_n(&g_sub_t[u_idx], t_hook_in, __ATOMIC_RELEASE);
    uint64_t prev_flip_t = g_last;
    uint32_t vf = ++g_vo_submit_flips;
    tb_on_game_submit();

    if (vf == 1) {
        uint32_t sw = __atomic_load_n(&g_vo_sub_w, __ATOMIC_ACQUIRE);
        lg("first VideoOutSubmitFlip: handle=%d index=%d mode=%u arg=%lld gnmSubmits=%u title=%s\n",
           handle, index, mode, (long long)arg, sw, g_title_id);
        STAGE("VideoOutSubmitFlip #1: handle=%d index=%d mode=%u gnmSubmits=%u", handle, index, mode, sw);
    }
    if (g_rel_t) {
        uint64_t us = t_hook_in - g_rel_t;
        g_rel_t = 0;
        if (us < 1000000) {
            g_cpu_n++; g_cpu_sum_us += (uint32_t)us; if (us > g_cpu_max_us) g_cpu_max_us = (uint32_t)us;
            g_cpu_ema_us = g_cpu_ema_us ? (g_cpu_ema_us * 7u + (uint32_t)us) / 8u : (uint32_t)us;
        }
    }
    tick(vf, (uint32_t)handle, u_idx, mode);
    uint64_t raw_dt_us = (prev_flip_t && g_freq) ? ((g_last - prev_flip_t) * 1000000ull / g_freq) : 33366ull;

#if INJECT
    if (vf == INJECT_AFTER_FLIPS) {
        vout_fg_ctx_init((uint32_t)handle, 1);
    }

    if (g_inject_on && vf >= INJECT_AFTER_FLIPS && u_idx < (uint32_t)g_nbuf) {
        if (g_gpu_state == 0) gpu_init();
        if (g_gpu_state == 1) {
            uint32_t slot = vf & 3u;
            int dcb8 = g_inject_on == 8 && g_gpu_sz == GPU_SZ;
            uint32_t *mine = (uint32_t *)(g_gpu + (dcb8 ? DCB8_OFF + slot * DCB8_SZ : SLOT0 + slot * SLOT_SZ));
            uint32_t n = 0;
            uint32_t flip_idx = u_idx, show_idx = u_idx;
            uint32_t mid2_flip_idx = 0;
            int second_flip = 0, third_flip = 0;
            uint32_t hold_mid_us = 0;

            int can_fg8 = (g_inject_on == 8 && g_work && g_sh8_ok && g_fmt_supported &&
                           fg_res_supported(g_bw, g_bh) && g_prev_idx < game_idx_end() && mbufs_ready((uint32_t)handle));
            int want_fg = (!g_fg_off || g_ab_x_flag) && (!g_detected_60fps || g_cfg.fps_lock == 30);

            if (can_fg8 && want_fg) {
                g_inert_reason[0] = 0;
                uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                uint32_t tidx = tile_idx_now();
                int pcopy = g_pcopy_avail && g_cfg.present_copy != 2;
                g_pcopy_on = pcopy;
                uint32_t mi1 = pcopy ? (uint32_t)g_mbase + (vf % (uint32_t)g_pcopy_k)
                                     : (uint32_t)g_mbase + (vf % (uint32_t)g_nmid);
                uint32_t mi2 = (uint32_t)g_mbase + ((vf + 1u) % (uint32_t)g_nmid);
                uint32_t t1_q16 = 32768u, t2_q16 = 43690u;
                int two_mids = 0;
                int force_repeat = (raw_dt_us > 66667ull) ? 1 : 0;

                if (g_cfg.preset == 1 && !g_fg_off && !force_repeat) {
                    uint32_t dt_clamped = g_ema_frame_us < 25000u ? 25000u : (g_ema_frame_us > 50000u ? 50000u : g_ema_frame_us);
                    int32_t r_q16 = (int32_t)(((uint64_t)dt_clamped << 16) / 16683ull);
                    int32_t acc_prev = g_fast_acc_q16;
                    int32_t acc_new = acc_prev + r_q16;
                    int m_out = (int)((acc_new + 32768) >> 16);
                    if (m_out < 2) m_out = 2;
                    if (m_out > 3) m_out = 3;
                    if (__atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE) >= 3u) m_out = 2;
                    g_fast_acc_q16 = acc_new - (m_out << 16);
                    if (g_fast_acc_q16 < -49152) g_fast_acc_q16 = -49152;
                    if (g_fast_acc_q16 >  49152) g_fast_acc_q16 =  49152;
                    int32_t phase_shift = (acc_prev * 3) >> 5;
                    if (m_out == 3 && !pcopy && g_nmid >= 2 && mi1 != mi2) {
                        int32_t p1 = 21845 + phase_shift, p2 = 43690 + phase_shift;
                        if (p1 < 16384) p1 = 16384; if (p1 > 27525) p1 = 27525;
                        if (p2 < 38010) p2 = 38010; if (p2 > 49152) p2 = 49152;
                        t1_q16 = (uint32_t)p1; t2_q16 = (uint32_t)p2;
                        two_mids = 1; mid2_flip_idx = mi2; third_flip = 1;
                    } else {
                        int32_t p1 = 32768 + phase_shift;
                        if (p1 < 22937) p1 = 22937; if (p1 > 42598) p1 = 42598;
                        t1_q16 = (uint32_t)p1;
                    }
                } else if (!g_fg_off) {
                    uint32_t prev_hold = __atomic_load_n(&g_last_real_hold_us, __ATOMIC_ACQUIRE);
                    uint32_t tot_us = g_ema_frame_us > 20000u ? g_ema_frame_us : 33366u;
                    uint32_t t_real = (prev_hold << 16) / tot_us;
                    t1_q16 = (t_real + 32768u) >> 1;
                    if (t1_q16 < 22937u) t1_q16 = 22937u;
                    if (t1_q16 > 42598u) t1_q16 = 42598u;
                }

                uint32_t ci = 0;
                if (pcopy) {
                    static uint32_t vo_pc_flip;
                    vo_pc_flip = (vo_pc_flip + 1u) % (uint32_t)g_pcopy_k;
                    ci = (uint32_t)g_mbase + (uint32_t)g_pcopy_k + vo_pc_flip;
                    show_idx = ci;
                    if (u_idx < 16u && ci < 16u)
                        __atomic_store_n(&g_sub_t[ci], __atomic_load_n(&g_sub_t[u_idx], __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
                }
                n = build_dcb_flow8(mine, g_buf[g_prev_idx], g_buf[u_idx], g_buf[mi1], g_buf[mi2], g_pitch,
                                    tidx, px * 4u, slot, vf, k_nop64_pkt, handle, mi1, mi2,
                                    t1_q16, t2_q16, two_mids, force_repeat, pcopy ? g_buf[ci] : 0ull, ci);
                flip_idx = mi1;
                second_flip = 1;
            } else if (g_inject_on == 8 && g_sh8_ok && g_fmt_supported && g_bw >= 640 && g_bh >= 360 && g_bw <= 3840 && g_bh <= 2160) {
                uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                uint32_t tidx = tile_idx_now();
                n = build_dcb_post8(mine, g_buf[u_idx], g_bw, g_bh, g_pitch, tidx, px * 4u, slot, vf, k_nop64_pkt);
            }

            int pace_p2 = (int)g_prev2_idx, pace_p1 = (int)g_prev_idx;
            g_prev2_idx = g_prev_idx;
            g_prev_idx = u_idx;

            if (n > 0) {
                int32_t s_rc = vout_submit_dcb(mine, n);
                if (vf == INJECT_AFTER_FLIPS || (vf % 600u) == 0)
                    lg("vout inject #%u: dcb=%u dw submit=0x%08x flip_idx=%u show_idx=%u pcopy=%d\n",
                       vf, n, (unsigned)s_rc, flip_idx, show_idx, g_pcopy_on);
                int32_t vr1 = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t),
                                            handle, (int32_t)flip_idx, mode, arg);
                if (third_flip) {
                    HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t),
                                  handle, (int32_t)mid2_flip_idx, mode, arg);
                }
                if (second_flip) {
                    if (hold_mid_us > 0) sceKernelUsleep(hold_mid_us);
                    int32_t vr2 = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t),
                                                handle, (int32_t)show_idx, mode, arg);
                    if (vf < INJECT_AFTER_FLIPS + 12)
                        lg("vout fg #%u: flip(mid %u)=0x%08x flip(real %u)=0x%08x\n",
                           vf, flip_idx, (unsigned)vr1, show_idx, (unsigned)vr2);
                }
                if (g_cfg.fps_lock == 20) pace_hz(20);
                else if (g_cfg.fps_lock == 60 && !second_flip) pace_hz(60);
                else if (g_inject_on == 8 && second_flip && pace_timer_on()) pace_pair();
                else if (g_inject_on >= 7 && second_flip) {
                    static int vo_last_show = -1;
                    int shown = (int)show_idx, pp1 = show_idx != u_idx ? vo_last_show : pace_p1;
                    pace_vblank(show_idx != u_idx ? -1 : pace_p2, pp1, shown);
                    vo_last_show = shown;
                } else if (second_flip) {
                    pace_30();
                }
                gv_frame_end();
                if (g_vt_hooked && g_vt_n >= VT_N) vt_dump();
                return vr1;
            }
        }
    }
#endif
    int32_t vr = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t), handle, index, mode, arg);
    if (g_vt_hooked && g_vt_n >= VT_N) vt_dump();
    return vr;
}

static void *g_ext_va[8];
static int g_ext_nm = 0;
static VoAttr g_ext_attr;

int32_t sceVideoOutRegisterBuffers_hook(int32_t handle, int32_t startIndex, void *const *addresses, int32_t bufferNum, const VoAttr *attr)
{
    flush_map_run();
    if (g_dbg_hooks) crash_handlers_install();
    STAGE("RegisterBuffers enter: handle=%d start=%d num=%d", handle, startIndex, bufferNum);
    if (attr)
        STAGE("  attr: fmt=0x%x tiling=%u aspect=%u %ux%u pitch=%u option=%u", attr->format, attr->tilingMode,
              attr->aspectRatio, attr->width, attr->height, attr->pitchInPixel, attr->option);
    for (int i = 0; addresses && i < bufferNum && i < 8; i++) STAGE("  buf[%d]=%p", startIndex + i, addresses[i]);
    lg("RegisterBuffers handle=%d start=%d num=%d\n", handle, startIndex, bufferNum);
    if (attr && startIndex == 0) { g_handle = handle; g_attr = *attr; }
    if (attr) {
        g_bw = attr->width; g_bh = attr->height; g_pitch = attr->pitchInPixel;
        // Scanout formats: 8-bit 0x8000xxxx and 10-bit 0x88xxxxxx (each needs its own T# data format; treating 10-bit as
        // 8888 turned the picture into noise). When startIndex == 0, the game is registering a primary scanout set
        // (e.g. switching from an 8-bit linear splash screen to a 10-bit tiled 3D engine in CUSA04529), so reset seen_dfmt.
        uint32_t fam = attr->format >> 24, dfmt = fam == 0x80u ? 10u : fam == 0x88u ? 9u : 0u;   // 0x88740000 = HDR10 PQ
        static uint32_t seen_dfmt;
        if (startIndex == 0) { seen_dfmt = 0; g_fmt_supported = 1; g_game_idx_hi = 0; g_nbuf = 0; }
        if (!dfmt || (seen_dfmt && seen_dfmt != dfmt)) g_fmt_supported = 0;
        else { seen_dfmt = dfmt; g_t_dfmt = dfmt; }
        vov_set_hdr(attr->format == 0x88740000u);   // HDR10 (BT.2020 PQ): overlay palette re-encoded, passes unchanged
        lg("  attr: fmt=0x%x (supported=%d) tiling=%u aspect=%u %ux%u pitch=%u option=%u\n",
           attr->format, g_fmt_supported, attr->tilingMode, attr->aspectRatio, attr->width, attr->height, attr->pitchInPixel, attr->option);
    }
    int overlapped_our_mbase = 0;
    if (addresses && bufferNum > 0) {
        if (startIndex > 0 && g_mbuf_state == 1 && startIndex < g_nbuf && startIndex + bufferNum > g_mbase) {
            overlapped_our_mbase = 1;
            STAGE("RegisterBuffers: the game registers %d..%d over our buffers from %d -> will relocate ours to top",
                  startIndex, startIndex + bufferNum - 1, g_mbase);
        }
        if (startIndex + bufferNum > g_game_idx_hi) g_game_idx_hi = startIndex + bufferNum;
    }
    for (int i = 0; addresses && i < bufferNum && startIndex + i < 16; i++) {
        g_buf[startIndex + i] = (uint64_t)(uintptr_t)addresses[i];
        if (startIndex + i + 1 > g_nbuf) g_nbuf = startIndex + i + 1;
        lg("  buf[%d]=%p\n", startIndex + i, addresses[i]);
    }
    if (overlapped_our_mbase && g_ext_nm >= 1) {
        int32_t r0 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                   handle, startIndex, addresses, bufferNum, attr);
        int base = 16 - g_ext_nm;
        if (r0 >= 0 && base >= g_game_idx_hi) {
            int32_t r1 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                       handle, base, g_ext_va, g_ext_nm, &g_ext_attr);
            STAGE("RegisterBuffers auto-relocated our %d buffer(s) to index %d = 0x%08x", g_ext_nm, base, (unsigned)r1);
            lg("mbufs: auto-relocated %d buffer(s) to index %d after game registered %d..%d (r=0x%08x)\n",
               g_ext_nm, base, startIndex, startIndex + bufferNum - 1, (unsigned)r1);
            if (r1 >= 0) {
                g_mbase = base;
                for (int i = 0; i < g_ext_nm; i++) g_buf[base + i] = (uint64_t)(uintptr_t)g_ext_va[i];
                g_nbuf = base + g_ext_nm;
                g_mbuf_state = 1;
                return r0;
            }
        }
        g_mbuf_state = -1;
        return r0;
    }
    if (g_bench_enabled) {
        if (startIndex == 0) g_ngame = bufferNum;
        int32_t rb = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                   handle, startIndex, addresses, bufferNum, attr);
        STAGE("RegisterBuffers done (bench, unchanged) = 0x%08x", (unsigned)rb);
        return rb;
    }
    // 1.4: Support 2..8 game display buffers (some engines register 4 buffers)
    if (g_safe && attr && startIndex == 0 && bufferNum >= 2 && bufferNum <= 8) g_ngame = bufferNum;   // observed only: see safe_try_activate()
    if (attr && startIndex == 0 && bufferNum >= 2 && bufferNum <= 8 && addresses && g_mbuf_state >= 0 && !g_safe) {
        g_ngame = bufferNum;
        int m_raw = read_mode_raw();
        int want4 = (m_raw == 4 || m_raw == 6 || m_raw == 7 || m_raw == 8);
        if (want4) {
            g_mbuf_state = -1;
            size_t one = 0;
            // Find minimum positive stride between any pair of game buffers (handles reverse-order allocations)
            for (int i = 0; i + 1 < bufferNum; i++) {
                uintptr_t a0 = (uintptr_t)addresses[i], a1 = (uintptr_t)addresses[i + 1];
                size_t diff = a1 > a0 ? (size_t)(a1 - a0) : (size_t)(a0 - a1);
                if (diff >= 0x100000 && diff <= 0x4000000 && !(diff & 0x7FFF)) {
                    if (!one || diff < one) one = diff;
                }
            }
            // B01: the VA gap between the game's buffers is not proof of the surface size (a gap may hold other
            // objects: 0x1fb0000 = 31.7 MB per 1080p buffer was seen). Logged next to pitch * aligned height * 4.
            {
                size_t calc = (g_pitch >= 640 && g_bh >= 360) ? (size_t)g_pitch * (size_t)((g_bh + 63u) & ~63u) * 4u : 0;
                STAGE("mbufs size: VA-gap stride 0x%zx, pitch*h*4 0x%zx%s", one, calc,
                      one && calc && one > 2 * calc ? " (VA gap > 2x the surface: suspicious, extra memory is over-sized)" : "");
            }
            // Fallback stride calculation from pitch * aligned_height * 4 if game allocated buffers in separate pools
            if (!one && g_pitch >= 640 && g_bh >= 360) {
                size_t raw_sz = (size_t)g_pitch * (size_t)((g_bh + 63u) & ~63u) * 4u;
                one = (raw_sz + 0x7FFFFu) & ~(size_t)0x7FFFFu;
                lg("mbufs: non-contiguous game buffers, computed surface size 0x%zx from %ux%u (pitch %u)\n", one, g_bw, g_bh, g_pitch);
            }
            if (one < 0x100000 || one > 0x4000000 || (one & 0x7FFF)) {
                lg("mbufs: unexpected buffer stride 0x%zx, not extending\n", one);
                one = 0;
            }
            if (!fg_res_supported(g_bw, g_bh)) {
                lg("mbufs: output %ux%u not supported by FG -> not allocating extra display buffers\n", g_bw, g_bh);
                STAGE("mbufs skipped: unsupported resolution %ux%u", g_bw, g_bh);
                one = 0;
            }
            int pcopy_cfg = read_present_copy_raw();   // 0 = Auto, 1 = 2-Buffer (present copy forced), 2 = Standard (off)
            int want_pcopy = (pcopy_cfg == 1) || (pcopy_cfg == 0 && g_ngame == 2);
            int hi_res = (g_bw > 1920 || g_bh > 1080);
            // If Memory Valve or Arena Shaving already fired at boot, the game is tight on DirectMemory:
            // use only g_pre_mem and never allocate extra mbufs from the kernel pool (preserves the shaved reserve).
            int mem_tight = (g_shave_orig_len > 0) || (g_pre_mem && g_pre_size < 0x2000000u);
            uint8_t *m = NULL;
            int nm = 0;
            int max_extra = 16 - g_ngame;
            int cap_extra = want_pcopy ? (hi_res ? 4 : 6) : (hi_res ? 2 : 3);
            if (max_extra > cap_extra) max_extra = cap_extra;
            if (one && g_pre_mem) {
                nm = (int)(g_pre_size / one);
                if (nm > max_extra) nm = max_extra;
                if (nm >= 1) m = (uint8_t *)g_pre_mem;
            }
            for (int want = max_extra; one && !m && !mem_tight && want >= 1; want--) {
                m = (uint8_t *)alloc_mbufs((one * (size_t)want + 0x1FFFFFu) & ~(size_t)0x1FFFFFu);
                if (m) nm = want;
            }
            // Double-buffered game (or forced 2-Buffer mode): mid buffers + present-copy buffers.
            uint8_t *m2 = NULL; int nm2 = 0;
            STAGE("pcopy gate: ngame=%d pcopy_cfg=%d want_pcopy=%d tight=%d m=%p nm=%d one=0x%zx pre=%p pre_size=0x%zx max_extra=%d",
                  g_ngame, pcopy_cfg, want_pcopy, mem_tight, (void *)m, nm, one, g_pre_mem, g_pre_size, max_extra);
            for (int want = (hi_res ? 4 : 6); want_pcopy && !mem_tight && m && !m2 && want >= 4; want -= 2) {
                if (nm >= want) break;
                size_t need2 = (one * (size_t)(want - nm) + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
                m2 = (uint8_t *)alloc_mbufs(need2);
                if (m2) nm2 = want - nm;
            }
            STAGE("pcopy gate done: nm=%d nm2=%d m2=%p", nm, nm2, (void *)m2);
            g_nmid = nm > 0 ? nm : 1;
            if (m && nm >= 1) {
                void *ext[16];
                for (int i = 0; i < g_ngame; i++) ext[i] = addresses[i];
                for (int i = 0; i < nm; i++) ext[g_ngame + i] = m + (size_t)i * one;
                for (int i = 0; i < nm2; i++) ext[g_ngame + nm + i] = m2 + (size_t)i * one;
                nm += nm2;
                g_ext_nm = nm <= 8 ? nm : 8;
                for (int i = 0; i < g_ext_nm; i++) g_ext_va[i] = ext[g_ngame + i];
                g_ext_attr = *attr;
                g_pcopy_avail = (want_pcopy && nm >= 4);
                g_pcopy_k = nm >= 6 ? 3 : 2;
                if (g_cfg.reg_mode == 1 && 16 - nm >= g_ngame) {
                    int base = 16 - nm;
                    int32_t r0 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                               handle, startIndex, addresses, bufferNum, attr);
                    int32_t r1 = r0 < 0 ? r0 : HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                                             handle, base, ext + g_ngame, nm, attr);
                    lg("mbufs: %d extra buffer(s) of 0x%zx in separate top group at index %d (reg_mode=1): game=0x%08x, ours=0x%08x\n",
                       nm, one, base, (unsigned)r0, (unsigned)r1);
                    STAGE("RegisterBuffers done (reg_mode=1 separate group at %d, %d x 0x%zx): game=0x%08x, ours=0x%08x",
                          base, nm, one, (unsigned)r0, (unsigned)r1);
                    if (r0 >= 0 && r1 >= 0) {
                        g_mbase = base;
                        for (int i = 0; i < nm; i++) g_buf[base + i] = (uint64_t)(uintptr_t)ext[g_ngame + i];
                        g_nbuf = base + nm;
                        g_mbuf_state = 1;
                        return r0;
                    }
                    g_pcopy_avail = 0;
                    return r0;
                }
                g_mbase = g_ngame;
                if (g_pcopy_avail) lg("double-buffered game: present copy available (%d mids + %d copies from index %d)\n", g_pcopy_k, g_pcopy_k, g_ngame);
                int32_t r = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                          handle, startIndex, ext, g_ngame + nm, attr);
                lg("mbufs: %d extra buffer(s) of 0x%zx at %p%s, game RegisterBuffers extended to %d = 0x%08x\n", nm, one, (void *)m,
                   g_pre_mem ? " (early)" : "", g_ngame + nm, (unsigned)r);
                STAGE("RegisterBuffers done (extended to %d, %d x 0x%zx%s) = 0x%08x", g_ngame + nm, nm, one, g_pre_mem ? ", early" : "",
                      (unsigned)r);
                if (r == 0) {
                    for (int i = 0; i < nm; i++) g_buf[g_ngame + i] = (uint64_t)(uintptr_t)ext[g_ngame + i];
                    g_nbuf = g_ngame + nm;
                    g_mbuf_state = 1;
                    return r;
                }
            }
            if (g_mbuf_state != 1)   // no own buffers: the game's registration goes through unchanged, frames stay native
                lg("mbufs: no extra display buffers (stride 0x%zx) -> native, the game's registration unchanged\n", one);
        }
    }
    int32_t rn = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                               handle, startIndex, addresses, bufferNum, attr);
    STAGE("RegisterBuffers done (unchanged%s) = 0x%08x", g_mbuf_state == -1 ? ", no extra buffers -> native" : "", (unsigned)rn);
    return rn;
}

// ---- in-game L3+R3 (3s) menu & input swallowing on ScePadData ----
typedef struct {
    uint32_t buttons;
    uint8_t  lx, ly;
    uint8_t  rx, ry;
    uint8_t  l2, r2;
    uint16_t pad0;
    uint8_t  motion_touch_rest[0x44];
    uint64_t timestamp;
    uint8_t  tail[0x20];
} __attribute__((packed)) PadDataRaw;
_Static_assert(sizeof(PadDataRaw) == 0x78, "PadDataRaw size must equal ScePadData (0x78)");

#define PAD_L3       0x0002u
#define PAD_R3       0x0004u
#define PAD_OPTIONS  0x0008u
#define PAD_UP       0x0010u
#define PAD_RIGHT    0x0020u
#define PAD_DOWN     0x0040u
#define PAD_LEFT     0x0080u
#define PAD_TRIANGLE 0x1000u
#define PAD_CIRCLE   0x2000u
#define PAD_CROSS    0x4000u
#define PAD_SQUARE   0x8000u

static uint64_t g_combo_t = 0, g_pad_last_ts = 0, g_pad_rep_t = 0, g_pad_swallow_until = 0;
static uint32_t g_pad_prev_nav = 0, g_pad_last_raw = 0;
static int g_combo_fired = 0;
static __thread int t_in_pad = 0;
static volatile uint32_t g_pad_calls[4];

static void pad_swallow(PadDataRaw *pkts, int count)
{
    for (int i = 0; i < count; i++) {
        pkts[i].buttons = 0;
        pkts[i].lx = 0x80; pkts[i].ly = 0x80;
        pkts[i].rx = 0x80; pkts[i].ry = 0x80;
        pkts[i].l2 = 0;    pkts[i].r2 = 0;
    }
}

static void menu_adjust_row(fg_config_t *c, int row, int dir)
{
    switch (row) {
    case 0:
        c->framegen = !c->framegen;
        if (c->framegen && g_detected_60fps && c->fps_lock != 30) c->fps_lock = 30;
        break;
    case 1: c->preset = (c->preset + (dir > 0 ? 1 : 2)) % 3; break;
    case 2: c->sharpness += dir * 5; if (c->sharpness < 0) c->sharpness = 0; if (c->sharpness > 100) c->sharpness = 100; break;
    case 3: c->upscale = (c->upscale + (dir > 0 ? 1 : 2)) % 3; break;
    case 4: c->overlay = (c->overlay + (dir > 0 ? 1 : 8)) % 9; break;
    case 5: c->game_vectors = !c->game_vectors; break;
    case 6: c->hud_protect = (c->hud_protect + (dir > 0 ? 1 : 2)) % 3; break;
    case 7: {
        static const int locks[4] = { 0, 20, 30, 60 };
        int idx = 0;
        for (int i = 0; i < 4; i++) if (locks[i] == c->fps_lock) idx = i;
        idx = (idx + (dir > 0 ? 1 : 3)) & 3;
        c->fps_lock = locks[idx];
        break;
    }
    case 8: c->menu_opacity += dir * 5; if (c->menu_opacity < 20) c->menu_opacity = 20; if (c->menu_opacity > 100) c->menu_opacity = 100; break;
    default: break;
    }
}

static void pad_menu_step(PadDataRaw *pkts, int count)
{
    if (!pkts || count < 1) return;
    PadDataRaw *latest = &pkts[count - 1];
    uint32_t raw_btn = latest->buttons;
    const uint32_t combo = menu_combo_sanitize(g_cfg.menu_combo);   // buttons held together (table only, default L3+R3)
    uint64_t now = sceKernelGetProcessTime();

    if (latest->timestamp && latest->timestamp == g_pad_last_ts && raw_btn == g_pad_last_raw) {
        if (g_menu_open || now < g_pad_swallow_until) pad_swallow(pkts, count);
        return;
    }
    g_pad_last_ts = latest->timestamp;
    g_pad_last_raw = raw_btn;
    { static uint32_t tile_prev; uint32_t te = raw_btn & ~tile_prev; tile_prev = raw_btn;
      if (!g_menu_open && (raw_btn & PAD_L3) && (te & (PAD_RIGHT | PAD_LEFT))) tile_hotkey((te & PAD_RIGHT) ? 1 : -1); }

    if (!g_menu_open) {
        if (now < g_pad_swallow_until) {
            if ((raw_btn & 0xFFFFu) == 0) g_pad_swallow_until = 0;
            else { pad_swallow(pkts, count); return; }
        }
        if ((raw_btn & combo) == combo) {
            uint64_t hold_us = (uint64_t)(g_cfg.menu_hold_ms >= 500 ? g_cfg.menu_hold_ms : 3000) * 1000ull;
            if (!g_combo_t) g_combo_t = now;
            else if (!g_combo_fired && now > g_combo_t && now - g_combo_t >= hold_us) {
                g_combo_fired = 1;
                g_menu_edit = g_cfg;
                g_menu_sel = 0;
                snprintf(g_osd_toast, sizeof(g_osd_toast), "MENU OPEN");
                g_menu_open = 1;
                g_pad_prev_nav = raw_btn;
                g_pad_rep_t = now;
                pad_swallow(pkts, count);
                char cname[32]; menu_combo_name(combo, cname, sizeof(cname));
                lg("menu opened (%s held %d ms)\n", cname, g_cfg.menu_hold_ms);
            }
        } else {
            g_combo_t = 0;
            g_combo_fired = 0;
        }
        return;
    }

    pad_swallow(pkts, count);

    if ((raw_btn & combo) != combo) g_combo_fired = 0;

    uint32_t nav_btn = raw_btn;
    if (latest->ly < 0x38) nav_btn |= PAD_UP;
    if (latest->ly > 0xC8) nav_btn |= PAD_DOWN;
    if (latest->lx < 0x38) nav_btn |= PAD_LEFT;
    if (latest->lx > 0xC8) nav_btn |= PAD_RIGHT;

    uint32_t edge = nav_btn & ~g_pad_prev_nav;
    uint32_t dpad_held = nav_btn & (PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT);
    if (edge & (PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT)) {
        g_pad_rep_t = now + 400000ull;
    } else if (dpad_held && now >= g_pad_rep_t) {
        edge |= dpad_held;
        g_pad_rep_t = now + 120000ull;
    }
    g_pad_prev_nav = nav_btn;

    if ((edge & (PAD_CIRCLE | PAD_OPTIONS)) || (!g_combo_fired && (raw_btn & combo) == combo)) {
        g_menu_open = 0;
        g_combo_fired = 1;
        g_combo_t = now;
        g_pad_swallow_until = now + 300000ull;
        snprintf(g_osd_toast, sizeof(g_osd_toast), "FG:%s AA:%d RCAS:%d%%",
                 g_cfg.framegen ? (g_cfg.preset == 1 ? "F60" : "2X") : "OFF", g_cfg.upscale, g_cfg.sharpness);
        g_osd_toast_until = now + 1500000ull;
        lg("menu closed (pad wrapper calls: state %u read %u stateExt %u readExt %u)\n", g_pad_calls[0], g_pad_calls[1], g_pad_calls[2], g_pad_calls[3]);
        return;
    }
    if (edge & PAD_UP)   g_menu_sel = (g_menu_sel + MENU_NROWS - 1) % MENU_NROWS;
    if (edge & PAD_DOWN) g_menu_sel = (g_menu_sel + 1) % MENU_NROWS;
    if (edge & (PAD_LEFT | PAD_RIGHT)) {
        int dir = (edge & PAD_RIGHT) ? 1 : -1;
        menu_adjust_row(&g_menu_edit, g_menu_sel, dir);
        cfg_apply_live(&g_menu_edit, NULL);
        snprintf(g_osd_toast, sizeof(g_osd_toast), "LIVE ([/\\] SAVE)");
    }
    if (edge & PAD_CROSS) {
        cfg_apply_live(&g_menu_edit, "menu-apply");
        snprintf(g_osd_toast, sizeof(g_osd_toast), "APPLIED");
    }
    if (edge & PAD_SQUARE) {
        int present_copy = g_cfg.present_copy;
        cfg_defaults(&g_menu_edit);
        g_menu_edit.menu_combo = g_cfg.menu_combo;   // the hotkey is not a visible setting: defaults must not change it
        g_menu_edit.present_copy = present_copy;  // profile-only option is not reset by the overlay
        cfg_apply_live(&g_menu_edit, "menu-defaults");
        snprintf(g_osd_toast, sizeof(g_osd_toast), "DEFAULTS LOADED");
    }
    if (edge & PAD_TRIANGLE) {
        cfg_apply_live(&g_menu_edit, "menu-save");
        __atomic_store_n(&g_cfg_save_req, 1, __ATOMIC_RELEASE);
        snprintf(g_osd_toast, sizeof(g_osd_toast), "SAVING INI...");
    }
}

int32_t scePadReadState_hook(int32_t handle, void *data)
{
    if (t_in_pad) return HOOK_CONTINUE(scePadReadState, int32_t (*)(int32_t, void *), handle, data);
    t_in_pad = 1;
    int32_t r = HOOK_CONTINUE(scePadReadState, int32_t (*)(int32_t, void *), handle, data);
    if (r == 0 && data) pad_menu_step((PadDataRaw *)data, 1);
    t_in_pad = 0;
    return r;
}

int32_t scePadRead_hook(int32_t handle, void *data, int32_t num)
{
    if (t_in_pad) return HOOK_CONTINUE(scePadRead, int32_t (*)(int32_t, void *, int32_t), handle, data, num);
    t_in_pad = 1;
    int32_t r = HOOK_CONTINUE(scePadRead, int32_t (*)(int32_t, void *, int32_t), handle, data, num);
    if (r > 0 && data) pad_menu_step((PadDataRaw *)data, r > 64 ? 64 : r);
    t_in_pad = 0;
    return r;
}

typedef int32_t (*pad_state_fn)(int32_t, void *);
typedef int32_t (*pad_read_fn)(int32_t, void *, int32_t);
static pad_state_fn g_orig_pad_state = 0;
static pad_read_fn g_orig_pad_read = 0;
static volatile int g_pad_got_patched = 0;
static pad_state_fn g_orig_pad_state_ext = 0;
static pad_read_fn g_orig_pad_read_ext = 0;
static int32_t got_pad_state_wrap(int32_t handle, void *data)
{
    g_pad_calls[0]++;
    int32_t r = g_orig_pad_state(handle, data);
    if (r == 0 && data) pad_menu_step((PadDataRaw *)data, 1);
    return r;
}
static int32_t got_pad_read_wrap(int32_t handle, void *data, int32_t num)
{
    g_pad_calls[1]++;
    int32_t r = g_orig_pad_read(handle, data, num);
    if (r > 0 && data) pad_menu_step((PadDataRaw *)data, r > 64 ? 64 : r);
    return r;
}
static int32_t got_pad_state_ext_wrap(int32_t handle, void *data)
{
    g_pad_calls[2]++;
    int32_t r = g_orig_pad_state_ext(handle, data);
    if (r == 0 && data) pad_menu_step((PadDataRaw *)data, 1);
    return r;
}
static int32_t got_pad_read_ext_wrap(int32_t handle, void *data, int32_t num)
{
    g_pad_calls[3]++;
    int32_t r = g_orig_pad_read_ext(handle, data, num);
    if (r > 0 && data) pad_menu_step((PadDataRaw *)data, r > 64 ? 64 : r);
    return r;
}
static int pad_got_patch(void)
{
    uintptr_t a1 = (uintptr_t)scePadReadState, a2 = (uintptr_t)scePadRead;
    if (!a1 || !a2) return 0;
    g_orig_pad_state = (pad_state_fn)a1;
    g_orig_pad_read = (pad_read_fn)a2;
    uintptr_t a3 = (uintptr_t)scePadReadStateExt, a4 = (uintptr_t)scePadReadExt;
    g_orig_pad_state_ext = (pad_state_fn)a3;
    g_orig_pad_read_ext = (pad_read_fn)a4;
    uint32_t mods[256];
    size_t nmods = 0;
    if (sceKernelGetModuleList(mods, 256, &nmods) != 0 || nmods > 256) { lg("pad got: module list failed\n"); return 0; }
    int patched = 0;
    for (size_t mi = 0; mi < nmods; mi++) {
        OrbisKernelModuleInfo info;
        memset(&info, 0, sizeof(info));
        info.size = sizeof(info);
        if (sceKernelGetModuleInfo(mods[mi], &info) != 0) continue;
        if (strstr(info.name, "bb_probe") || strstr(info.name, "libScePad") || strstr(info.name, "GoldHEN")) continue;
        for (uint32_t si = 0; si < info.segmentCount && si < 4; si++) {
            // 1.4: Scan any RW segment (including RWX segments used by engine JIT/GOT tables), up to 64 MB
            if ((info.segmentInfo[si].prot & 3) != 3) continue;
            uintptr_t base = (uintptr_t)info.segmentInfo[si].address;
            size_t len = info.segmentInfo[si].size;
            if (len > 0x4000000u) len = 0x4000000u;
            len &= ~(size_t)7;
            for (size_t off = 0; off < len; off += 0x10000u) {
                size_t chunk = len - off < 0x10000u ? len - off : 0x10000u;
                if (!dcb_mem_ok(base + off, chunk)) continue;
                volatile uintptr_t *w = (volatile uintptr_t *)(base + off);
                for (size_t k = 0; k < chunk / 8; k++) {
                    uintptr_t v = w[k];
                    if (v == a1) { w[k] = (uintptr_t)got_pad_state_wrap; patched++; lg("pad got: %s +0x%llx scePadReadState\n", info.name, (unsigned long long)(off + k * 8)); }
                    else if (a3 && v == a3) { w[k] = (uintptr_t)got_pad_state_ext_wrap; patched++; lg("pad got: %s scePadReadStateExt\n", info.name); }
                    else if (a4 && v == a4) { w[k] = (uintptr_t)got_pad_read_ext_wrap; patched++; lg("pad got: %s scePadReadExt\n", info.name); }
                    else if (v == a2) { w[k] = (uintptr_t)got_pad_read_wrap; patched++; lg("pad got: %s +0x%llx scePadRead\n", info.name, (unsigned long long)(off + k * 8)); }
                }
            }
        }
    }
    return patched;
}

static void *pad_poll_thread(void *arg)
{
    (void)arg;
    int32_t uid = -1, handle = -1, errs = 0, got_tries = 0;
    uint64_t next_got_try = 0;
    for (;;) {
        sceKernelUsleep(16000);
        if (g_flips < INJECT_AFTER_FLIPS + 60 && g_vo_submit_flips < INJECT_AFTER_FLIPS + 60) continue;
        uint64_t now = sceKernelGetProcessTime();
        // 1.4: Retry GOT patching up to 12 times (every 5 s) in case the game resolves lazy PLT slots after splash screens
        // VCFG: GOT pad patching is off for every title: it crashed CUSA00207 and is the main suspect for a reported
        // CUSA00411 crash at load, and it patched nothing on CUSA08519 (the menu works by polling there).
        int got_ok = 0;
        if (!g_pad_got_patched && got_ok && got_tries < 12 && now >= next_got_try) {
            got_tries++;
            next_got_try = now + 5000000ull;
            int np = pad_got_patch();
            if (got_tries == 1 || np > 0) lg("pad got (try %d): %d slot(s) patched\n", got_tries, np);
            if (np > 0) { g_pad_got_patched = 1; return NULL; }
        }
        if (handle < 0) {
            if (uid < 0 && sceUserServiceGetInitialUser(&uid) != 0) { uid = -1; sceKernelUsleep(1000000); continue; }
            handle = scePadGetHandle(uid, 0, 0);
            if (handle < 0) { sceKernelUsleep(1000000); continue; }
            lg("pad poll: user %d handle %d\n", (int)uid, (int)handle);
            errs = 0;
        }
        PadDataRaw st;
        memset(&st, 0, sizeof(st));
        int32_t r = scePadReadState(handle, (void *)&st);
        if (r == 0) { errs = 0; pad_menu_step(&st, 1); }
        else if (++errs > 60) { lg("pad poll: read error 0x%08x, re-acquiring handle\n", (unsigned)r); handle = -1; sceKernelUsleep(1000000); }
    }
    return NULL;
}
static void pad_poll_start(void)
{
    static int started = 0;
    if (started) return;
    started = 1;
    OrbisPthread th;
    int r = scePthreadCreate(&th, NULL, pad_poll_thread, NULL, "bb_pad");
    lg("pad poll thread started: 0x%08x\n", (unsigned)r);
}

int32_t sceVideoOutSetFlipRate_hook(int32_t handle, int32_t rate)
{
    if (g_bench_enabled) return HOOK_CONTINUE(sceVideoOutSetFlipRate, int32_t (*)(int32_t, int32_t), handle, rate);
    int m_raw = read_mode_raw();
    int want4 = (m_raw == 4 || m_raw == 6 || m_raw == 7 || m_raw == 8);
    static uint32_t s_sfr_n;   // some games call SetFlipRate every frame: only the first calls (or rate changes) are logged
    int sfr_log = (s_sfr_n < 4 || rate != g_game_flip_rate) ? (int)++s_sfr_n : 0;
    if (sfr_log) {
        lg("SetFlipRate handle=%d rate=%d%s\n", handle, rate, (want4 && (!g_safe || g_safe_active)) ? " -> forced 0 (mode 4/8: 60 Hz output, we pace the game ourselves)" : "");
        STAGE("SetFlipRate enter: handle=%d rate=%d%s", handle, rate, (want4 && (!g_safe || g_safe_active)) ? " -> 0" : "");
    }
    g_game_flip_rate = rate;   // safe mode: the game's own rate stays until the late set-up succeeded
    if (want4 && (!g_safe || g_safe_active)) rate = 0;
    int32_t r = HOOK_CONTINUE(sceVideoOutSetFlipRate, int32_t (*)(int32_t, int32_t), handle, rate);
    if (sfr_log) STAGE("SetFlipRate done = 0x%08x", (unsigned)r);
    return r;
}

s32 attr_public plugin_load(s32 argc, const char *argv[])
{
    (void)argc; (void)argv;
    final_printf("[GoldHEN] <%s\\Ver.0x%08x> %s\n", g_pluginName, g_pluginVersion, __func__);
    boot_ver();
    init_title_id();
    if (!g_is_game_proc) {
        klog("[bb_probe] inert: process title_id='%s' is not a CUSA game -> skipping hooks and allocations\n", g_title_id);
        return 0;
    }
    g_is_neo = sceKernelIsNeoMode() ? 1 : 0;
    g_t_is_neo = (uint32_t)g_is_neo;
    load_early_adapt_cfg();
    STAGE("plugin_load enter: title=%s (family=%s) version=" VCFG_TRACE_VERSION " engine=classic neo=%d dm=0x%zx shave_mode=%d reg_mode=%d pace_order=%d post_mid=%d",
          g_title_id, vcfg_title_family(g_title_id), (int)g_is_neo, (size_t)sceKernelGetDirectMemorySize(),
          g_cfg.shave_mode, g_cfg.reg_mode, g_cfg.pace_order, g_cfg.post_mid);
#if VCFG_BENCH
    if (!bench_bootstrap()) { BLOG("bench_bootstrap: inert"); return 0; }
    BLOG("bench_bootstrap ok");
#endif
    g_boot_t0 = sceKernelGetProcessTime();
    if (!g_bench_enabled) debug_hooks_install();
    g_vq_on = !g_bench_enabled && read_two_buffer_raw();
    // Always install sceVideoOutGetFlipStatus hook unless present_copy=2 (Standard forced) so any 2-buffer game
    // discovered at RegisterBuffers time automatically gets full virtual flip queue + currentBuffer/flipArg translation.
    if (!g_bench_enabled && (g_vq_on || tb_title() || read_present_copy_raw() != 2)) {
        HOOK32(sceVideoOutGetFlipStatus);
        g_status_hooked = 1;
        STAGE("status hook installed (two_buffer=%d, test=%d)", g_vq_on, VCFG_2BUF_TEST);
    }
#if VCFG_2BUF_TEST
    if (tb_title()) {
        HOOK32(sceVideoOutAddFlipEvent);
        HOOK32(sceGnmInsertWaitFlipDone);
        g_tb_hooks = 1;
    }
#endif
    {
        int m = g_bench_enabled ? 8 : read_mode_raw();
        if (!g_bench_enabled && g_cfg.reg_mode == 2) {
            g_safe = 1;
            STAGE("dev override: reg_mode=2 -> deferring RegisterBuffers/SetFlipRate to frame %d", INJECT_AFTER_FLIPS);
        } else if (!g_bench_enabled && !is_verified_title()) {
            // Progressive self-adapting boot pipeline for unverified titles:
            //   state 0 (default): full universal early pipeline (g_safe=0: inline RegisterBuffers + auto-relocate + SetFlipRate(0))
            //   state 1: deferred late separate-group RegisterBuffers/SetFlipRate at frame INJECT_AFTER_FLIPS (g_safe=1)
            //   state 2: passive native observation only (g_safe=1, g_safe_never=1, m=0)
            int st = safe_state_read();
            if (safe_marker(0)) {
                safe_marker(2);
                st = (st < 2) ? (st + 1) : 2;
                safe_state_write(st);
                STAGE("safe mode: previous start did not reach frame %d -> advancing state to %d", INJECT_AFTER_FLIPS, st);
            }
            if (st == 2) {
                g_safe = 1;
                g_safe_never = 1;
                m = 0;
                STAGE("safe mode: title %s is in passive state=2 -> no early memory, hooks only observe", g_title_id);
            } else if (st == 1) {
                g_safe = 1;
                g_safe_early = 1;
                safe_marker(1);
                STAGE("safe mode: title %s in state=1 -> deferred RegisterBuffers/SetFlipRate to frame %d", g_title_id, INJECT_AFTER_FLIPS);
            } else {
                g_safe = 0;
                g_safe_early = 1;
                safe_marker(1);
                STAGE("safe mode: title %s in state=0 -> universal early pipeline active (boot marker armed until frame %d)",
                      g_title_id, INJECT_AFTER_FLIPS);
            }
        }
        int late_alloc = (!g_bench_enabled && g_cfg.shave_mode == 2);
        if (late_alloc) {
            STAGE("dev override: shave_mode=2 (LATE ALLOC) -> skipping all early DirectMemory allocations in plugin_load");
        }
        if (m == 8 && !g_bench_enabled && !late_alloc) gv_load(alloc_dm);
        if (m >= 1 && m <= 8 && !late_alloc) {
            g_gpu_sz = gv_armed() ? GPU_SZ : GPU_SZ_BASE;
            STAGE("alloc gpu sz=0x%zx (mode %d)", (size_t)g_gpu_sz, m);
            g_pre_gpu = alloc_dm_range(0, (long)sceKernelGetDirectMemorySize(), g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
            STAGE("alloc gpu -> %p", g_pre_gpu);
            {
                int ff = file_flags();
                if (!g_bench_enabled && ff > 0 && (ff & 16)) {
                    g_dump_mem = (uint8_t *)alloc_dm_range(0, (long)sceKernelGetDirectMemorySize(), DUMP_SZ, 0x10000, 0, &g_dump_phys);
                    klog("[bb_probe] dump memory: %p\n", (void *)g_dump_mem);
                }
            }
        }
#if VCFG_BENCH
        // capture memory (~50 MB) is taken later, from bench_flip once the game runs: taking it here crashed the game (run d, 2026-10-20)
#endif
        if ((m == 4 || m >= 6) && !g_bench_enabled && !late_alloc) {
            // Try 32 MB first (4 x 8 MB for 2-buffer present copy), then 16 MB, then 8 MB (1 mid buffer).
            // Place at the top of the DirectMemory pool so the game's lowest-first arena remains contiguous from 0 upward.
            // If a non-arena game needs part of this back at boot, the Memory Valve shrinks 32 MB -> 8 MB automatically.
            size_t dm_sz = sceKernelGetDirectMemorySize();
            const size_t try_sz[3] = { 0x2000000u, 0x1000000u, 0x800000u };
            int r = -1;
            for (int ti = 0; ti < 3 && !g_pre_mem; ti++) {
                size_t total = try_sz[ti];
                long hi_s = dm_sz > total ? (long)(dm_sz - total) : 0;
                for (int hi = (hi_s > 0 ? 1 : 0); hi >= 0 && !g_pre_mem; hi--) {
                    long phys = 0; void *addr = NULL;
                    long s = hi ? hi_s : 0;
                    g_own_alloc++;
                    r = sceKernelAllocateDirectMemory(s, (long)dm_sz, total, 0x200000, 3 /*WC_GARLIC*/, &phys);
                    if (r == 0) {
                        r = sceKernelMapDirectMemory(&addr, total, 0x33, 0, phys, 0x200000);
                        if (r != 0 || !addr) sceKernelReleaseDirectMemory(phys, total);
                    }
                    g_own_alloc--;
                    if (r == 0 && addr) {
                        memset(addr, 0, total);
                        g_pre_mem = addr;
                        g_pre_size = total;
                        g_pre_mem_phys = phys;
                    }
                    STAGE("early display memory try %d (%s): len=0x%zx align=0x200000 type=3 range=0x%lx..0x%zx r=0x%08x phys=0x%lx%s",
                          ti + 1, hi ? "top" : "full", total, s, dm_sz, (unsigned)r, g_pre_mem ? phys : 0L,
                          g_pre_mem ? " ok" : "");
                }
            }
            klog("[bb_probe] early display-buffer memory: %s (size=0x%zx, r=0x%08x)\n", g_pre_mem ? "ok" : "FAILED", g_pre_size, (unsigned)r);
        }
        if (m >= 6 && !late_alloc) {
            size_t wsz = (m == 8) ? WORK_SZ_8 : WORK_SZ;
            if (m == 8) {
                STAGE("alloc work sz=0x%zx garlic", wsz);
                g_pre_work = alloc_work_early(wsz, 3);
                g_work_garlic = g_pre_work != NULL;
            }
            if (!g_pre_work) g_pre_work = alloc_work_early(wsz, 0);
            STAGE("alloc work -> %p garlic=%d", g_pre_work, (int)g_work_garlic);
            klog("[bb_probe] early gpu/work memory: %p / %p\n", g_pre_gpu, g_pre_work);
        }
    }
    STAGE("hooking");
    HOOK32(sceGnmSubmitAndFlipCommandBuffers);
    HOOK32(sceGnmSubmitCommandBuffers);
    HOOK32(sceVideoOutRegisterBuffers);
    HOOK32(sceVideoOutSetFlipRate);
    HOOK32(sceVideoOutSubmitFlip);
    STAGE("hooks installed");
    {
        int ff = file_flags();
        if (ff > 0 && (ff & 128)) g_diag_hooks = 1;
    }
    if (g_dbg_hooks && g_safe && !g_bench_enabled) {   // debug log on + unverified title: research trace of VideoOut-flip games
        g_vt_hooked = 1;
        HOOK32(sceGnmSubmitDone);
        STAGE("voutrace armed: records the game's submits and flips from VideoOut flip %d (%d events)", VT_START_FLIP, VT_N);
    }
    if (g_diag_hooks) {
        HOOK32(sceGnmSubmitCommandBuffersForWorkload);
        HOOK32(sceGnmSubmitAndFlipCommandBuffersForWorkload);
        klog("[bb_probe] diagnostic hooks on the ForWorkload submit variants installed\n");
    }
    return 0;
}

s32 attr_public plugin_unload(s32 argc, const char *argv[])
{
    (void)argc; (void)argv;
    if (!g_is_game_proc) return 0;
#if VCFG_BENCH
    if (!g_bench_enabled) return 0;  // bootstrap may have returned without installing hooks
#endif
    if (g_status_hooked) UNHOOK(sceVideoOutGetFlipStatus);
    debug_hooks_remove();
#if VCFG_2BUF_TEST
    if (g_tb_hooks) {
        UNHOOK(sceVideoOutAddFlipEvent);
        UNHOOK(sceGnmInsertWaitFlipDone);
    }
#endif
    UNHOOK(sceGnmSubmitAndFlipCommandBuffers);
    UNHOOK(sceGnmSubmitCommandBuffers);
    UNHOOK(sceVideoOutRegisterBuffers);
    UNHOOK(sceVideoOutSetFlipRate);
    UNHOOK(sceVideoOutSubmitFlip);
    if (g_vt_hooked) UNHOOK(sceGnmSubmitDone);
    if (g_diag_hooks) { UNHOOK(sceGnmSubmitCommandBuffersForWorkload); UNHOOK(sceGnmSubmitAndFlipCommandBuffersForWorkload); }
    if (g_log) { fclose(g_log); g_log = NULL; }
    return 0;
}

s32 attr_module_hidden module_start(s64 argc, const void *args) { (void)argc; (void)args; return 0; }
s32 attr_module_hidden module_stop(s64 argc, const void *args) { (void)argc; (void)args; return 0; }
