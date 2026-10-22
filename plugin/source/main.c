// bb_probe: 30 -> 60 Hz frame generation for console games (GoldHEN plugin).
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
#include <pthread.h>
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
#include "gv.h"

#define PLUGIN_NAME "bb_probe"
#define LOG_DIR "/data/bb_probe"
#define LOG_PATH LOG_DIR "/probe.log"
#define BB_DIAG_ALLOC_A 1 /* baseline telemetry: never reserve DirectMemory / never enable FG */
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
} fg_config_t;

static char g_title_id[16] = "UNKNOWN";
#include "a21_config.h"
// A22: titles that run the A20/A21 runtime. Everywhere the old code special-cased REF (passive start, no early
// allocations, allocator valve hook, runtime worker) these titles get the same treatment.
static int a22_runtime_title(void)
{
    if (strcmp(g_title_id, "CUSA07439") == 0) return 1;          // CUSA07439
#if A22_UNIVERSAL
    if (strcmp(g_title_id, "CUSA09171") == 0) return 1;          // CUSA09171 (EU): 3 x 1080p, 10-bit PQ
    if (strcmp(g_title_id, "CUSA13801") == 0) return 1;          // CUSA13801 (EU): 3 buffers, 10-bit PQ; 1080p without the 720p patch
#endif
    return 0;
}
static int g_known_game = 0;
static int g_is_game_proc = 0; // safe default: 0 until verified as CUSAxxxxx
static volatile fg_config_t g_cfg = { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0 };
static fg_config_t g_cfg_buf[2]   = {
    { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0 },
    { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0 }
};
static volatile uint32_t g_cfg_idx = 0, g_cfg_ver = 0;
static fg_config_t g_menu_edit    = { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0 };
static volatile int g_menu_open = 0, g_menu_sel = 0, g_cfg_save_req = 0;

typedef struct {
    const char *title_id;
    int fg_default;      // 1 = enable FG by default, 0 = start passive (native pass-through until user enables)
    int max_mid_bufs;    // 1 = 8 MB (tight memory games), 2 = 16 MB (enables 3-step Fast 60 Hz pulldown)
    int high_mem_alloc;  // 1 = allocate GARLIC from upper half of DirectMemory pool
    int late_mbuf_reg;   // 1 = do not extend game's initial RegisterBuffers; register mid buffers lazily
    int target_fps;      // 30 or 60
} game_profile_t;

static const game_profile_t g_builtin_profiles[] = {
    { "CUSA08519", 1, 2, 1, 0, 30 }, // CUSA08519: 2 game bufs, 2 mid bufs (Fast 60Hz ready)
    { "CUSA00411", 1, 2, 1, 0, 30 }, // CUSA00411: 3 game bufs, 2 mid bufs, 0x68750778 flip magic
    { "CUSA00207", 0, 1, 1, 1, 30 }, // CUSA00207: tight GARLIC pool -> passive start, 1 mid buf from high mem, late reg
    { "CUSA07439", 0, 1, 1, 1, 30 }, // CUSA07439: engine -> passive start, keep flipRate=1 until FG enabled
#if A22_UNIVERSAL
    { "CUSA09171", 0, 1, 1, 1, 30 }, // CUSA09171: same passive REF-style profile (A22 runtime owns FG)
    { "CUSA13801", 0, 1, 1, 1, 30 }, // CUSA13801: same passive REF-style profile
#endif
};
static game_profile_t g_prof = { "DEFAULT", 0, 2, 1, 0, 30 };
static volatile int g_passive_mode = 0, g_crash_fail_count = 0, g_boot_pending_dirty = 0;
static volatile int g_game_flip_rate = 1, g_forced_flip_rate_zero = 0;
static volatile int g_start_mode = 8, g_activation_done = 0, g_mbuf_reg_req = 0;
// Menu asks the I/O thread to leave passive mode (X with FG on, or Triangle); done there so mon/pad threads start once.
static volatile int g_leave_passive_req = 0;
// FG crash guard: armed when 60 Hz output is switched on, cleared after FG_GUARD_FLIPS stable flips.
static volatile int g_fg_guard_arm_req = 0;
static volatile uint32_t g_fg_guard_flip0 = 0;
#define FG_GUARD_FLIPS 1800u
// Lazy MBUF attempts that found no DirectMemory; REF stops retrying after MBUF_MAX_FAILS.
static int g_mbuf_fail_n = 0;
#define MBUF_MAX_FAILS 3
static void dcb_mem_process_queue(void);
static int ensure_mbufs_registered(int32_t handle);
static void mon_start(void);
static volatile uint32_t g_unfilled_vblanks = 0, g_total_sched_vblanks = 0;
static volatile uint64_t g_osd_toast_until = 0;
static char g_osd_toast[32] = "READY";
static char g_inert_reason[32] = "";
static volatile int g_detected_60fps = 0;
static volatile int g_fmt_supported = 1;
static volatile uint32_t g_last_gpu_ms_x100 = 0, g_last_hz_x10 = 600;
static volatile uint32_t g_osd_real_fps_x10 = 300, g_osd_out_fps_x10 = 600, g_osd_frame_ms_x10 = 333;
static volatile uint32_t g_cur_pending_flips = 0, g_cur_gcq = 0;
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
int32_t sceVideoOutAddFlipEvent(OrbisKernelEqueue queue, int32_t handle, void *udata);
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
// One-run REF allocator telemetry. These hooks only forward the original call
// once and log its inputs/result; this diagnostic build never reserves MBUF.
HOOK_INIT(sceKernelAllocateDirectMemory);
HOOK_INIT(sceKernelMapDirectMemory);
HOOK_INIT(sceKernelMapNamedDirectMemory);
HOOK_INIT(sceVideoOutAddFlipEvent);
HOOK_INIT(sceVideoOutGetFlipStatus);
HOOK_INIT(sceKernelGetEventData);
HOOK_INIT(sceKernelWaitEqueue);
static int32_t sceKernelAllocateDirectMemory_hook(long searchStart, long searchEnd, size_t length, size_t alignment, int type, long *physAddrOut);
static int32_t sceKernelMapDirectMemory_hook(void **addr, size_t length, int prot, int flags, long directMemoryStart, size_t maxPageSize);
static int32_t sceKernelMapNamedDirectMemory_hook(void **addr, size_t length, int prot, int flags, long directMemoryStart, size_t maxPageSize, const char *name);

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

#define VCFG_ENGINE "new"
#include "vcfg_trace.h"

static void lg_write(const char *buf)
{
    klog("[bb_probe] %s", buf);
    vcfg_trace(buf);
    log_open();
    if (g_log) { fputs(buf, g_log); fflush(g_log); }
}

#define LGQ_N 256
static char g_lgq[LGQ_N][384];
static volatile uint32_t g_lgq_w = 0, g_lgq_r = 0, g_lgq_lock = 0, g_lgq_drop = 0;
static volatile int g_lg_async = 0;
// Only bb_io may write the file; its own messages must not fill its drain queue.
int scePthreadGetthreadid(void);
static volatile int g_lg_io_owner_tid = 0;
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
    int io_tid = __atomic_load_n(&g_lg_io_owner_tid, __ATOMIC_ACQUIRE);
    if (io_tid != 0 && scePthreadGetthreadid() == io_tid) { lg_write(buf); return; }
    if (g_lg_async) {
        if (lgq_trylock()) {
            if (g_lgq_w - g_lgq_r < LGQ_N) { memcpy(g_lgq[g_lgq_w % LGQ_N], buf, sizeof(buf)); g_lgq_w++; } else g_lgq_drop++;
            lgq_unlock();
        } else g_lgq_drop++;
        return;
    }
    klog("[bb_probe] %s", buf);
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

static unsigned long long diag_thread_id(void)
{
    return (unsigned long long)(uintptr_t)pthread_self();
}

static unsigned g_a6_sequence, g_a6_counts[4];
static int32_t a20_alloc_pressure(long start,long end,size_t length,size_t alignment,int type,long *out,int32_t original_rc);
static void a20_worker_tick(void);
static int a20_try_submit(uint32_t count,void **dcb,uint32_t *sizes,void **ccb,uint32_t *csizes,
                        uint32_t handle,uint32_t index,uint32_t mode,int64_t arg,int32_t *result);
static int a6_sample(unsigned api)
{
    unsigned n = __atomic_add_fetch(&g_a6_counts[api],1u,__ATOMIC_RELAXED);
    return n <= 256u || (n % 120u) == 0;
}
static unsigned a6_seq(void) { return __atomic_add_fetch(&g_a6_sequence,1u,__ATOMIC_RELAXED); }
int32_t sceVideoOutAddFlipEvent_hook(OrbisKernelEqueue queue, int32_t handle, void *udata)
{
    int rc = HOOK_CONTINUE(sceVideoOutAddFlipEvent,int32_t (*)(OrbisKernelEqueue,int32_t,void *),queue,handle,udata);
    lg("A6_ADD_EVENT seq=%u t=%llu tid=%llx caller=%p queue=0x%llx handle=%d udata=%p rc=0x%08x\n",
       a6_seq(),(unsigned long long)sceKernelGetProcessTime(),diag_thread_id(),__builtin_return_address(0),
       (unsigned long long)queue,handle,udata,(unsigned)rc);
    return rc;
}
int32_t sceVideoOutGetFlipStatus_hook(int32_t handle, VoFlipStatus *st)
{
    int rc = HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),handle,st);
    if (a6_sample(0)) {
        if (rc == 0 && st)
            lg("A6_STATUS seq=%u t=%llu tid=%llx caller=%p h=%d rc=0 count=%llu shown_t=%llu arg=%lld idx=%d pending=%d gcq=%d\n",
               a6_seq(),(unsigned long long)sceKernelGetProcessTime(),diag_thread_id(),__builtin_return_address(0),handle,
               (unsigned long long)st->count,(unsigned long long)st->processTime,(long long)st->flipArg,st->currentBuffer,st->flipPendingNum,st->gcQueueNum);
        else lg("A6_STATUS_FAIL h=%d rc=0x%08x\n",handle,(unsigned)rc);
    }
    return rc;
}
intptr_t sceKernelGetEventData_hook(const OrbisKernelEvent *event)
{
    intptr_t result = HOOK_CONTINUE(sceKernelGetEventData,intptr_t (*)(const OrbisKernelEvent *),event);
    if (event && a6_sample(1))
        lg("A6_EVENT_DATA seq=%u t=%llu tid=%llx caller=%p ident=0x%llx filter=%d flags=0x%x data=%lld udata=%p result=%lld\n",
           a6_seq(),(unsigned long long)sceKernelGetProcessTime(),diag_thread_id(),__builtin_return_address(0),
           (unsigned long long)event->ident,(int)event->filter,(unsigned)event->flags,(long long)event->data,event->udata,(long long)result);
    return result;
}
int32_t sceKernelWaitEqueue_hook(OrbisKernelEqueue queue, OrbisKernelEvent *events, int32_t maxevents,
                                int32_t *count, OrbisKernelUseconds *timeout)
{
    uint64_t begin = sceKernelGetProcessTime();
    int rc = HOOK_CONTINUE(sceKernelWaitEqueue,
        int32_t (*)(OrbisKernelEqueue,OrbisKernelEvent *,int32_t,int32_t *,OrbisKernelUseconds *),queue,events,maxevents,count,timeout);
    if (a6_sample(2)) {
        int n = rc == 0 && count ? *count : 0;
        lg("A6_WAIT seq=%u t=%llu wait_us=%llu tid=%llx caller=%p queue=0x%llx rc=0x%08x count=%d max=%d\n",
           a6_seq(),(unsigned long long)sceKernelGetProcessTime(),(unsigned long long)(sceKernelGetProcessTime()-begin),
           diag_thread_id(),__builtin_return_address(0),(unsigned long long)queue,(unsigned)rc,n,maxevents);
        if (events && n > 0 && n <= maxevents) {
            for (int i=0; i<n && i<4; i++)
                lg("A6_WAIT_EVENT queue=0x%llx slot=%d ident=0x%llx filter=%d flags=0x%x data=%lld udata=%p\n",
                   (unsigned long long)queue,i,(unsigned long long)events[i].ident,(int)events[i].filter,
                   (unsigned)events[i].flags,(long long)events[i].data,events[i].udata);
        }
    }
    return rc;
}
static void a6_submit_trace(const char *api,uint32_t handle,uint32_t index,uint32_t mode,int64_t arg,int32_t rc,uint64_t begin)
{
    // Full submission trace for up to ~5 minutes at 30 fps; bound later logging.
    unsigned sample = __atomic_add_fetch(&g_a6_counts[3],1u,__ATOMIC_RELAXED);
    if (sample > 10000u && (sample % 120u) != 0) return;
    lg("A6_SUBMIT seq=%u api=%s begin=%llu end=%llu tid=%llx h=%u idx=%u mode=%u arg=%lld rc=0x%08x\n",
       a6_seq(),api,(unsigned long long)begin,(unsigned long long)sceKernelGetProcessTime(),diag_thread_id(),
       handle,index,mode,(long long)arg,(unsigned)rc);
}

int32_t sceKernelAllocateDirectMemory_hook(long searchStart, long searchEnd, size_t length, size_t alignment, int type, long *physAddrOut)
{
    long phys = -1;
    int32_t r = HOOK_CONTINUE(sceKernelAllocateDirectMemory,
        int32_t (*)(long, long, size_t, size_t, int, long *),
        searchStart, searchEnd, length, alignment, type, physAddrOut);
    if (physAddrOut && r == 0) phys = *physAddrOut;
    lg("DM_ALLOC tid=%llx start=0x%lx end=0x%lx len=0x%zx align=0x%zx type=%d out=%p rc=0x%08x phys=0x%lx\n",
       diag_thread_id(), searchStart, searchEnd, length, alignment, type, (void *)physAddrOut, (unsigned)r, phys);
    return a20_alloc_pressure(searchStart,searchEnd,length,alignment,type,physAddrOut,r);
}

int32_t sceKernelMapDirectMemory_hook(void **addr, size_t length, int prot, int flags, long directMemoryStart, size_t maxPageSize)
{
    int32_t r = HOOK_CONTINUE(sceKernelMapDirectMemory,
        int32_t (*)(void **, size_t, int, int, long, size_t),
        addr, length, prot, flags, directMemoryStart, maxPageSize);
    void *mapped = (r == 0 && addr) ? *addr : NULL;
    lg("DM_MAP tid=%llx phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx out=%p rc=0x%08x va=%p\n",
       diag_thread_id(), directMemoryStart, length, prot, flags, maxPageSize, (void *)addr, (unsigned)r, mapped);
    return r;
}

int32_t sceKernelMapNamedDirectMemory_hook(void **addr, size_t length, int prot, int flags, long directMemoryStart, size_t maxPageSize, const char *name)
{
    int32_t r = HOOK_CONTINUE(sceKernelMapNamedDirectMemory,
        int32_t (*)(void **, size_t, int, int, long, size_t, const char *),
        addr, length, prot, flags, directMemoryStart, maxPageSize, name);
    void *mapped = (r == 0 && addr) ? *addr : NULL;
    lg("DM_MAP_NAMED tid=%llx phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx name_ptr=%p out=%p rc=0x%08x va=%p\n",
       diag_thread_id(), directMemoryStart, length, prot, flags, maxPageSize, (const void *)name, (void *)addr, (unsigned)r, mapped);
    return r;
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
static volatile uint32_t g_gpu_init_req = 0;
static int g_deferred_ref_gpu = 0;

static void *g_pre_gpu = NULL, *g_pre_work = NULL;
#define WORK_SZ 0x220000u
_Static_assert(W8_END <= WORK_SZ, "mode 8 work memory layout does not fit WORK_SZ");
_Static_assert(DCB8_OFF + 4 * DCB8_SZ <= GPU_SZ, "mode 8 DCB areas do not fit GPU_SZ");

#define MON_OFF 0xE00u
static volatile uint32_t *g_mon = NULL;
static uint32_t g_sh8_off[16];         // code offsets in g_gpu: 0..9 pyr8..mf8, 10..13 gv8..gvz8, 14 rcas8, 15 osd8
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
static int g_mbuf_state = 0; // 0 retryable, 1 ok, -1 registration failed
static uint64_t g_mbuf_retry_after_us = 0; // I/O-thread retry backoff
static void *g_mbuf_mem = NULL;
static int g_nmid = 3;
static int g_ngame = 3;
static void *g_pre_mem = NULL;
static size_t g_pre_size = 0;
static size_t g_mbuf_stride = 0;
static volatile int g_ref_dmem_scan_done = 0;
// REF may need the extra display surface reserved before the renderer fragments Garlic.
// Perform this on bb_io after plugin_load returns; never allocate it on GoldHEN's loader thread.
static volatile uint32_t g_ref_mbuf_prereserve_req = 0;
static uint64_t g_ref_mbuf_prereserve_after_us = 0;
static void *pre_mem_load(void)
{
    return __atomic_load_n(&g_pre_mem, __ATOMIC_ACQUIRE);
}

#define GARLIC_SAFETY_RESERVE (32ull << 20) // Always leave >= 32 MB of DirectMemory for the game
int32_t sceKernelReleaseDirectMemory(long start, size_t len);

static void fg_cfg_publish(const fg_config_t *c)
{
    uint32_t next = g_cfg_idx ^ 1u;
    g_cfg_buf[next] = *c;
    __atomic_add_fetch(&g_cfg_ver, 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_cfg_idx, next, __ATOMIC_RELEASE);
    g_cfg = *c;
}

static void fg_cfg_read(fg_config_t *out)
{
    for (int k = 0; k < 3; k++) {
        uint32_t v0 = __atomic_load_n(&g_cfg_ver, __ATOMIC_ACQUIRE);
        uint32_t idx = __atomic_load_n(&g_cfg_idx, __ATOMIC_ACQUIRE);
        *out = g_cfg_buf[idx];
        uint32_t v1 = __atomic_load_n(&g_cfg_ver, __ATOMIC_ACQUIRE);
        if (v0 == v1) return;
    }
}

// console target ABI: these manual declarations require LP64 widths.
_Static_assert(sizeof(long) == 8 && sizeof(size_t) == 8 && sizeof(void *) == 8,
               "bb_probe DirectMemory declarations require the console LP64 ABI");

static void *alloc_dm_ex(size_t len, size_t align, int type, int high_mem, size_t min_free_after)
{
#if BB_DIAG_ALLOC_A
    if (a22_runtime_title()) return NULL;
#endif
    size_t dm_sz = sceKernelGetDirectMemorySize();
    long start_hi = (high_mem && dm_sz > (256ull << 20)) ? (long)(dm_sz >> 1) : 0;
    long phys = 0; void *addr = NULL;
    if (len > SIZE_MAX - min_free_after) {
        lg("dm size overflow len=0x%zx reserve=0x%zx\n", len, min_free_after);
        return NULL;
    }
    if (min_free_after > 0) {
        long probe_phys = 0;
        size_t probe_len = len + min_free_after;
        int pr = sceKernelAllocateDirectMemory(start_hi, (long)dm_sz, probe_len, align, type, &probe_phys);
        if (pr != 0) {
            // A failed contiguous probe is not a measurement of total free memory.
            lg("dm probe FAIL range=[0x%lx,0x%zx) len=0x%zx align=0x%zx type=%d reserve=0x%zx r=0x%08x\n",
               (unsigned long)start_hi, dm_sz, probe_len, align, type, min_free_after, (unsigned)pr);
            return NULL;
        }
        int rr = sceKernelReleaseDirectMemory(probe_phys, probe_len);
        if (rr != 0) {
            lg("dm probe release FAIL phys=0x%lx len=0x%zx r=0x%08x; stopping this request\n",
               (unsigned long)probe_phys, probe_len, (unsigned)rr);
            return NULL;
        }
    }
    int r = sceKernelAllocateDirectMemory(start_hi, (long)dm_sz, len, align, type, &phys);
    if (r != 0) {
        lg("dm alloc FAIL range=[0x%lx,0x%zx) len=0x%zx align=0x%zx type=%d reserve=0x%zx r=0x%08x\n",
           (unsigned long)start_hi, dm_sz, len, align, type, min_free_after, (unsigned)r);
        return NULL;
    }
    r = sceKernelMapDirectMemory(&addr, len, 0x33 /*CPU rw + GPU rw*/, 0, phys, align);
    if (r != 0 || !addr) {
        int rr = sceKernelReleaseDirectMemory(phys, len);
        lg("dm map FAIL phys=0x%lx len=0x%zx align=0x%zx r=0x%08x addr=%p release=0x%08x\n",
           (unsigned long)phys, len, align, (unsigned)r, addr, (unsigned)rr);
        return NULL;
    }
    lg("dm OK range=[0x%lx,0x%zx) phys=0x%lx len=0x%zx align=0x%zx type=%d reserve=0x%zx va=%p\n",
       (unsigned long)start_hi, dm_sz, (unsigned long)phys, len, align, type, min_free_after, addr);
    memset(addr, 0, len);
    return addr;
}
void *alloc_dm(size_t len, size_t align, int type);
void *alloc_dm(size_t len, size_t align, int type)
{
    return alloc_dm_ex(len, align, type, g_prof.high_mem_alloc, type == 3 ? (16ull << 20) : 0);
}

// Query-only view of free DirectMemory (no Allocate/Release, nothing taken from the game).
// Reports the largest free run per alignment and its physical address.
// OpenOrbis orbis/libkernel.h declares it as (off_t, off_t, size_t, off_t, size_t), i.e. without the two out
// pointers of the real API (off_t *physAddrOut, size_t *sizeOut). Pass the addresses as integers: SysV x86-64
// puts args 4/5 in rcx/r8 either way, so the callee receives the same pointers.
static void dmem_report_free(const char *why)
{
    static int reports = 0;
    if (reports >= 6) return; // bounded samples per session
    reports++;
    size_t dm_sz = sceKernelGetDirectMemorySize();
    off_t hi_start = (dm_sz > (256ull << 20)) ? (off_t)(dm_sz >> 1) : 0;
    const size_t aligns[4] = { 0x200000, 0x10000, 0x8000, 0x4000 };
    for (int i = 0; i < 4; i++) {
        long phys_all = 0, phys_hi = 0;
        size_t sz_all = 0, sz_hi = 0;
        int r_all = sceKernelAvailableDirectMemorySize(0, (off_t)dm_sz, aligns[i], (off_t)(uintptr_t)&phys_all, (size_t)(uintptr_t)&sz_all);
        int r_hi = hi_start ? sceKernelAvailableDirectMemorySize(hi_start, (off_t)dm_sz, aligns[i], (off_t)(uintptr_t)&phys_hi, (size_t)(uintptr_t)&sz_hi) : r_all;
        lg("dmem free (%s): dm_sz=0x%zx align=0x%zx -> full[r=0x%08x max=0x%zx (%zu KiB) @0x%lx] hi[r=0x%08x max=0x%zx (%zu KiB) @0x%lx]\n",
           why, dm_sz, aligns[i], (unsigned)r_all, sz_all, sz_all >> 10, (unsigned long)phys_all,
           (unsigned)r_hi, sz_hi, sz_hi >> 10, (unsigned long)phys_hi);
    }
}

// Query metadata only. No physical memory is mapped, read, reserved or released.
// Layout independently checked against the OpenOrbis declaration and shadPS4 memory.h.
typedef struct { uint64_t start, end; int32_t type; uint32_t pad; } DiagDmemInfo;
_Static_assert(sizeof(DiagDmemInfo) == 24, "DirectMemoryQuery ABI");
_Static_assert(sizeof(OrbisKernelVirtualQueryInfo) == 72, "VirtualQuery ABI");
typedef struct {
    DiagDmemInfo entries[128];
    uint64_t time_begin, time_end, pool_size;
    uint64_t low_free_start, low_free_size;
    uint32_t count;
    int32_t terminal_rc, low_free_rc;
} DiagDmemSnapshot;
static DiagDmemSnapshot g_diag_early;
static volatile uint32_t g_diag_map_ready = 0;

static void diag_capture_dmem(DiagDmemSnapshot *s)
{
    memset(s, 0, sizeof(*s));
    s->time_begin = sceKernelGetProcessTime();
    s->pool_size = sceKernelGetDirectMemorySize();
    uint64_t pos = 0;
    for (uint32_t i = 0; i < 128 && pos < s->pool_size; i++) {
        DiagDmemInfo info = {0};
        int32_t r = sceKernelDirectMemoryQuery((off_t)pos, 1, &info, sizeof(info));
        s->terminal_rc = r;
        if (r != 0) break;
        if (info.end <= pos || info.start >= info.end || info.end > s->pool_size) {
            s->terminal_rc = -1; // invalid metadata: stop, never use it as an address
            break;
        }
        s->entries[s->count++] = info;
        pos = info.end;
    }
    s->low_free_rc = sceKernelAvailableDirectMemorySize(0, 0x2a00000, 0x4000,
        (off_t)(uintptr_t)&s->low_free_start, (size_t)(uintptr_t)&s->low_free_size);
    s->time_end = sceKernelGetProcessTime();
}

static void diag_dump_dmem(const char *phase, const DiagDmemSnapshot *s)
{
    lg("DMQ_BEGIN phase=%s t0=%llu t1=%llu pool=0x%llx count=%u terminal_rc=0x%08x lgq_drop=%u\n",
        phase, (unsigned long long)s->time_begin, (unsigned long long)s->time_end,
        (unsigned long long)s->pool_size, s->count, (unsigned)s->terminal_rc, g_lgq_drop);
    for (uint32_t i = 0; i < s->count; i++) {
        const DiagDmemInfo *q = &s->entries[i];
        lg("DMQ phase=%s i=%u start=0x%llx end=0x%llx type=%d\n", phase, i,
            (unsigned long long)q->start, (unsigned long long)q->end, q->type);
    }
    lg("DMQ_LOW_FREE phase=%s range=[0,0x2a00000) align=0x4000 rc=0x%08x start=0x%llx size=0x%llx\n",
        phase, (unsigned)s->low_free_rc, (unsigned long long)s->low_free_start,
        (unsigned long long)s->low_free_size);
}

static void diag_virtual_low(const char *phase)
{
    uint64_t pos = 0;
    uint32_t count = 0, hits = 0;
    int32_t r = 0;
    for (; count < 4096 && pos < 0x10000000000ull; count++) {
        OrbisKernelVirtualQueryInfo q;
        memset(&q, 0, sizeof(q));
        r = sceKernelVirtualQuery((const void *)(uintptr_t)pos, 1, &q, sizeof(q));
        if (r != 0) break;
        uint64_t start = (uint64_t)(uintptr_t)q.unk01;
        uint64_t end = (uint64_t)(uintptr_t)q.unk02;
        if (end <= pos || end <= start) { r = -1; break; }
        if (q.isDirectMemory && q.offset >= 0 && (uint64_t)q.offset < 0x2a00000ull) {
            char name[33]; memcpy(name, q.name, 32); name[32] = 0;
            lg("VMQ_LOW phase=%s va=[0x%llx,0x%llx) phys_offset=0x%llx prot=0x%x type=%d committed=%u name=%s\n",
                phase, (unsigned long long)start, (unsigned long long)end,
                (unsigned long long)q.offset, (unsigned)q.unk04, q.unk05, q.isCommitted, name);
            hits++;
        }
        pos = end;
    }
    lg("VMQ_END phase=%s queries=%u low_hits=%u next=0x%llx terminal_rc=0x%08x\n",
        phase, count, hits, (unsigned long long)pos, (unsigned)r);
}

static void diag_modules(const char *phase)
{
    OrbisKernelModule mods[256]; size_t count = 0;
    int32_t r = sceKernelGetModuleList(mods, 256, &count);
    lg("DMQ_MODULES phase=%s rc=0x%08x count=%zu\n", phase, (unsigned)r, count);
    if (r != 0 || count > 256) return;
    for (size_t i = 0; i < count; i++) {
        OrbisKernelModuleInfo q; memset(&q, 0, sizeof(q)); q.size = sizeof(q);
        if (sceKernelGetModuleInfo(mods[i], &q) != 0) continue;
        q.name[255] = 0;
        for (uint32_t j = 0; j < q.segmentCount && j < 4; j++)
            lg("DMQ_MODULE phase=%s id=%u name=%s seg=%u va=%p size=0x%x prot=0x%x\n",
                phase, mods[i], q.name, j, q.segmentInfo[j].address,
                q.segmentInfo[j].size, q.segmentInfo[j].prot);
    }
}

static void gpu_init(void)
{
    __atomic_store_n(&g_gpu_state, -1, __ATOMIC_RELEASE);
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
    if (!addr) return;
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
        const uint32_t *code[16] = { pyr8_code, me3_code, me2_code, me1_code, phase8_code, warp8_code, dump8_code, gm8_code,
                                     fgs8_code, mf8_code, gv8_code, gvc8_code, warp8g_code, gvz8_code, rcas8_code, osd8_code };
        const uint32_t nw[16] = { PYR8_NWORDS, ME3_NWORDS, ME2_NWORDS, ME1_NWORDS, PHASE8_NWORDS, WARP8_NWORDS, DUMP8_NWORDS,
                                  GM8_NWORDS, FGS8_NWORDS, MF8_NWORDS, GV8_NWORDS, GVC8_NWORDS, WARP8G_NWORDS, GVZ8_NWORDS,
                                  RCAS8_NWORDS, OSD8_NWORDS };
        uint32_t off = 0x8000;
        g_sh8_ok = 1;
        for (int i = 0; i < 10; i++) {
            if (off + nw[i] * 4 > DCB8_OFF) { g_sh8_ok = 0; break; }
            memcpy((uint8_t *)addr + off, code[i], nw[i] * 4);
            g_sh8_off[i] = off;
            off = (off + nw[i] * 4 + 0xFFu) & ~0xFFu;
        }
        for (int i = 14; g_sh8_ok && i < 16; i++) {
            if (off + nw[i] * 4 > DCB8_OFF) { g_sh8_ok = 0; break; }
            memcpy((uint8_t *)addr + off, code[i], nw[i] * 4);
            g_sh8_off[i] = off;
            off = (off + nw[i] * 4 + 0xFFu) & ~0xFFu;
        }
        lg("gpu_init: mode 8 + rcas8/osd8 shaders %s, end 0x%x\n", g_sh8_ok ? "ok" : "DO NOT FIT", off);
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
    g_work = (uint8_t *)(g_pre_work ? g_pre_work : alloc_dm(WORK_SZ, 0x10000, 0));
    if (!g_work) lg("gpu_init: work memory unavailable, mode 6 disabled\n");
    g_mon = (volatile uint32_t *)((uint8_t *)addr + MON_OFF);
    g_gpu = (uint8_t *)addr;
    __atomic_store_n(&g_gpu_state, 1, __ATOMIC_RELEASE);
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
    t[0] = (uint32_t)(surf >> 8);
    t[1] = (uint32_t)((surf >> 40) & 0xFFu) | (10u << 20);
    t[2] = (w - 1u) | ((h - 1u) << 14);
    t[3] = 4u | (5u << 3) | (6u << 6) | (7u << 9) | (tile_idx << 20) | (9u << 28);
    t[4] = 0u | ((pitch - 1u) << 13);
    t[5] = t[6] = t[7] = 0;
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
        int32_t fr = sceGnmInsertWaitFlipDone(p, 7, vo_handle, mid_idx);
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

static void osd_set_row(uint32_t *styles, char (*grid)[32], int row, uint32_t rtype, uint32_t vcol, uint32_t vclr, uint32_t bend, const char *txt)
{
    styles[row] = (rtype & 0xFFu) | ((vcol & 0xFFu) << 8) | ((vclr & 0xFFu) << 16) | ((bend & 0xFFu) << 24);
    int i = 0;
    for (; i < 32 && txt[i]; i++) grid[row][i] = txt[i];
    for (; i < 32; i++) grid[row][i] = ' ';
}

static const char *overlay_label(int ov)
{
    static const char *names[9] = {
        "OFF",
        "SMALL [TL]", "DETAIL [TL]",
        "SMALL [TR]", "DETAIL [TR]",
        "SMALL [BR]", "DETAIL [BR]",
        "SMALL [BL]", "DETAIL [BL]"
    };
    if (ov < 0 || ov > 8) return "OFF";
    return names[ov];
}

// Builds OSD character/style grid in slotb + OSD_SLOT_OFF.
// out_flags: 3u = write both mid & cur (when FG is active), 2u = write cur only (native/post-only mode).
static void osd_build_slot(uint8_t *osd_mem, uint32_t *xy_out, uint32_t *wh_out, uint32_t out_flags)
{
    // The legacy grid menu does not match the current osd8 (overlay/vov.c bitmaps): A20/A22 paths draw the overlay.
    (void)osd_mem; (void)out_flags; *xy_out = 0; *wh_out = 0;
    if (1) return;
    uint64_t now = sceKernelGetProcessTime();
    int ov = g_cfg.overlay;
    if (!g_menu_open && now >= g_osd_toast_until && ov <= 0) {
        *xy_out = 0;
        *wh_out = 0;
        return;
    }
    uint32_t *st = (uint32_t *)osd_mem;
    char (*gr)[32] = (char (*)[32])(osd_mem + 64);
    int op = g_menu_open ? g_menu_edit.menu_opacity : g_cfg.menu_opacity;
    if (op < 20) op = 20;
    if (op > 100) op = 100;
    uint32_t alpha_u8 = (uint32_t)(op * 255 / 100);
    char line[64];

    if (!g_menu_open && now < g_osd_toast_until) {
        snprintf(line, sizeof(line), " VCFG  %s", g_title_id);
        osd_set_row(st, gr, 0, 1, 32, 0, 0, line);
        snprintf(line, sizeof(line), " %s", g_osd_toast);
        osd_set_row(st, gr, 1, 0, 1, 1, 0, line);
        *xy_out = 48u | (48u << 16);
        *wh_out = 28u | (2u << 8) | (alpha_u8 << 16) | ((out_flags & 3u) << 24);
        return;
    }

    // 1.3: Corner FPS Overlay when menu is closed and overlay > 0
    if (!g_menu_open) {
        int is_det = (ov & 1) == 0;          // 1,3,5,7 = SMALL; 2,4,6,8 = DETAILED
        int corner = (ov - 1) / 2;           // 0=TL, 1=TR, 2=BR, 3=BL
        uint32_t rfps = __atomic_load_n(&g_osd_real_fps_x10, __ATOMIC_ACQUIRE);
        uint32_t ofps = __atomic_load_n(&g_osd_out_fps_x10, __ATOMIC_ACQUIRE);
        uint32_t ftms = __atomic_load_n(&g_osd_frame_ms_x10, __ATOMIC_ACQUIRE);
        uint32_t gpums = __atomic_load_n(&g_last_gpu_ms_x100, __ATOMIC_ACQUIRE);
        if (!g_cfg.framegen || g_inert_reason[0]) ofps = rfps;

        const char *mtag = (!g_cfg.framegen || g_inert_reason[0]) ? "NAT" : (g_cfg.preset == 1 ? "F60" : "2X");
        uint32_t cols = is_det ? 28u : 22u;
        uint32_t rows = is_det ? 2u : 1u;

        if (!is_det) {
            snprintf(line, sizeof(line), " FPS %2u.%u->%2u.%u [%s]",
                     rfps / 10u, rfps % 10u, ofps / 10u, ofps % 10u, mtag);
            osd_set_row(st, gr, 0, 0, 5, g_cfg.framegen && !g_inert_reason[0] ? 1 : 2, 0, line);
        } else {
            snprintf(line, sizeof(line), " FPS:%2u.%u->%2u.%u FT:%2u.%ums",
                     rfps / 10u, rfps % 10u, ofps / 10u, ofps % 10u, ftms / 10u, ftms % 10u);
            osd_set_row(st, gr, 0, 1, 5, 1, 0, line);
            const char *aat = g_cfg.upscale == 2 ? "AA:HI" : (g_cfg.upscale == 1 ? "AA:LO" : "AA:--");
            snprintf(line, sizeof(line), " GPU:%u.%02ums %s %s",
                     gpums / 100u, gpums % 100u, mtag, aat);
            osd_set_row(st, gr, 1, 0, 5, 2, 0, line);
        }

        uint32_t sw = g_bw >= 640 ? g_bw : 1920u;
        uint32_t sh = g_bh >= 360 ? g_bh : 1080u;
        uint32_t pw = cols * 16u, ph = rows * 16u;
        uint32_t x0 = (corner == 1 || corner == 2) ? (sw > pw + 24u ? sw - pw - 24u : 0u) : 24u;
        uint32_t y0 = (corner == 2 || corner == 3) ? (sh > ph + 24u ? sh - ph - 24u : 0u) : 24u;
        *xy_out = (x0 & 0xFFFFu) | ((y0 & 0xFFFFu) << 16);
        *wh_out = cols | (rows << 8) | (alpha_u8 << 16) | ((out_flags & 3u) << 24);
        return;
    }

    fg_config_t c = g_menu_edit;
    int sel = g_menu_sel;
    uint32_t rfps = __atomic_load_n(&g_osd_real_fps_x10, __ATOMIC_ACQUIRE);
    uint32_t ofps = __atomic_load_n(&g_osd_out_fps_x10, __ATOMIC_ACQUIRE);
    if (!c.framegen || g_inert_reason[0]) ofps = rfps;

    snprintf(line, sizeof(line), " VCFG %s  %2u.%u>%2u.%u FPS", g_title_id, rfps / 10u, rfps % 10u, ofps / 10u, ofps % 10u);
    osd_set_row(st, gr, 0, 1, 32, 0, 0, line);

    // Always show the edited ON/OFF value here; the inert reason is shown on the status row below.
    const char *fgs;
    if (g_passive_mode) fgs = c.framegen ? "ON (PRESS X)" : "OFF (SAFE)"; // row is cut at 32 chars
    else if (!c.framegen) fgs = "OFF  NATIVE";
    else if (g_inert_reason[0]) fgs = "ON (INERT)";
    else fgs = c.preset == 1 ? "ON  FAST 60" : "ON  QUALITY 2X";
    snprintf(line, sizeof(line), " FRAME GEN       %s", fgs);
    osd_set_row(st, gr, 1, sel == 0 ? 2 : 0, 17, (c.framegen && !g_inert_reason[0]) ? 1 : 3, 0, line);

    const char *pn = c.preset == 1 ? "FAST" : (c.preset == 2 ? "SHARP" : "QUALITY");
    snprintf(line, sizeof(line), " PRESET          %s", pn);
    osd_set_row(st, gr, 2, sel == 1 ? 2 : 0, 17, 2, 0, line);

    if (c.sharpness > 0) snprintf(line, sizeof(line), " SHARPNESS       %d%%", c.sharpness);
    else snprintf(line, sizeof(line), " SHARPNESS       OFF");
    uint32_t b3 = c.sharpness > 0 ? (17u + (uint32_t)(c.sharpness * 15) / 100u) : 0u;
    osd_set_row(st, gr, 3, sel == 2 ? 2 : 0, 17, c.sharpness > 0 ? 1 : 3, b3, line);

    const char *un = c.upscale <= 0 ? "OFF" : (c.upscale == 1 ? "LIGHT" : "STRONG");
    snprintf(line, sizeof(line), " ENHANCE         %s", un);
    osd_set_row(st, gr, 4, sel == 3 ? 2 : 0, 17, c.upscale > 0 ? 1 : 3, 0, line);

    snprintf(line, sizeof(line), " FPS COUNTER     %s", overlay_label(c.overlay));
    osd_set_row(st, gr, 5, sel == 4 ? 2 : 0, 17, c.overlay > 0 ? 1 : 3, 0, line);

    const char *vn = !c.game_vectors ? "OFF" : (gv_armed() ? "ON  HYBRID" : "ON  AFTER REBOOT");
    snprintf(line, sizeof(line), " GAME VECTORS    %s", vn);
    osd_set_row(st, gr, 6, sel == 5 ? 2 : 0, 17, c.game_vectors ? (gv_armed() ? 1 : 2) : 3, 0, line);

    const char *hn = c.hud_protect <= 0 ? "OFF" : (c.hud_protect == 1 ? "CONSERVATIVE" : "AGGRESSIVE");
    snprintf(line, sizeof(line), " HUD PROTECT     %s", hn);
    osd_set_row(st, gr, 7, sel == 6 ? 2 : 0, 17, c.hud_protect > 0 ? 1 : 3, 0, line);

    if (c.fps_lock <= 0) snprintf(line, sizeof(line), " FPS LOCK        AUTO");
    else snprintf(line, sizeof(line), " FPS LOCK        %d", c.fps_lock);
    osd_set_row(st, gr, 8, sel == 7 ? 2 : 0, 17, 2, 0, line);

    snprintf(line, sizeof(line), " OPACITY         %d%%", c.menu_opacity);
    uint32_t b9 = 17u + (uint32_t)(c.menu_opacity * 15) / 100u;
    osd_set_row(st, gr, 9, sel == 8 ? 2 : 0, 17, 2, b9, line);

    snprintf(line, sizeof(line), " GPU:%u.%02ums | %s", g_last_gpu_ms_x100 / 100u, g_last_gpu_ms_x100 % 100u,
             g_inert_reason[0] ? g_inert_reason : g_osd_toast);
    osd_set_row(st, gr, 10, 3, 1, 2, 0, line);
    osd_set_row(st, gr, 11, 3, 32, 0, 0, " UP/DN SELECT  LEFT/RIGHT CHANGE");
    osd_set_row(st, gr, 12, 3, 32, 0, 0, " X APPLY [] RESET /\\ SAVE O EXIT");

    *xy_out = 64u | (64u << 16);
    *wh_out = 32u | (13u << 8) | (alpha_u8 << 16) | ((out_flags & 3u) << 24);
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

// 1.4: Native post-processing pass on T#cur only (when VCFG is OFF or inert due to resolution / 60fps / no mbuf).
// Keeps L3+R3 menu, corner FPS overlay, and Enhance AA + RCAS working in ANY game without locking to 30 fps!
static uint32_t build_dcb_post8(uint32_t *p0, uint64_t C, uint32_t w, uint32_t h, uint32_t pitch, uint32_t tidx,
                                uint32_t nbytes, uint32_t slot, uint32_t seq, const uint32_t *tail, uint32_t tail_dw)
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
    for (uint32_t i = 0; i < tail_dw; i++) *p++ = tail[i];
    return (uint32_t)(p - p0);
}

static uint32_t build_dcb_flow8(uint32_t *p0, uint64_t P, uint64_t C, uint64_t M1, uint64_t M2,
                                uint32_t pitch, uint32_t tidx, uint32_t nbytes, uint32_t slot, uint32_t seq,
                                const uint32_t *tail, uint32_t tail_dw, int32_t vo_handle, uint32_t mid1_idx, uint32_t mid2_idx,
                                uint32_t t1_q16, uint32_t t2_q16, int two_mids, int force_repeat)
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

    uint32_t base_flags = (g_opt_phase ? 1u : 0u) | ((g_fg_off || force_repeat) ? 2u : 0u) | (g_opt_marker ? 4u : 0u)
                        | (g_opt_nocf ? 8u : 0u) | (g_opt_nofgk ? 16u : 0u) | (g_opt_noanc ? 32u : 0u)
                        | (g_opt_notp ? 64u : 0u) | (g_opt_nomf ? 128u : 0u) | (g_opt_notex ? 256u : 0u)
                        | (g_opt_notpg ? 512u : 0u) | (g_opt_sharp ? 1024u : 0u) | (g_opt_nostr ? 2048u : 0u);
    uint32_t mid1_packed = (mid1_idx & 0x7FFFu) | ((t1_q16 & 0xFFFFu) << 16);

    fill_table8(tb, P, C, M1, pitch, tidx, r, mid1_packed, base_flags, g_work, g_mon, stats);

    uint32_t rcas_con_bits = 0, rcas_flags1 = 0, osd_xy = 0, osd_wh1 = 0;
    uint32_t mask1 = two_mids ? 1u /*mid1 only; mid2+cur done in pass 2*/ : 3u /*mid1 + cur*/;
    calc_rcas_params(mask1, &rcas_con_bits, &rcas_flags1);
    if (g_sh8_off[15]) osd_build_slot(slotb + OSD_SLOT_OFF, &osd_xy, &osd_wh1, mask1);
    fill_table8_ext(tb, g_gpu, slotb, rcas_con_bits, rcas_flags1, osd_xy, osd_wh1);

    *(volatile uint64_t *)(slotb + 0x900) = 0;
    *(volatile uint64_t *)(slotb + 0x908) = 0;
    for (int i = 0; i < 5; i++) *(volatile uint64_t *)(slotb + 0x910 + i * 8) = 0;
    for (int i = 0; i < 7; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;
#define DISPATCH(x, y) do { *p++ = PKT3(0x15, 4, 1); *p++ = (x); *p++ = (y); *p++ = 1; *p++ = 1; } while (0)
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
    SET_PASS(g_sh8_off[0], PYR8_RSRC1, PYR8_RSRC2);   DISPATCH(30, 68);
    PASS_BARRIER();
    TS_EOP(1);
    SET_PASS(g_sh8_off[1], ME3_RSRC1, ME3_RSRC2);     DISPATCH(30, 17);
    PASS_BARRIER(); TS2(0);
    SET_PASS(g_sh8_off[2], ME2_RSRC1, ME2_RSRC2);     DISPATCH(15, 34);
    PASS_BARRIER(); TS2(1);
    SET_PASS(g_sh8_off[7], GM8_RSRC1, GM8_RSRC2);     DISPATCH(1, 1);
    PASS_BARRIER();
    SET_PASS(g_sh8_off[8], FGS8_RSRC1, FGS8_RSRC2);   DISPATCH(32, 1);
    PASS_BARRIER(); TS2(2);
    SET_PASS(g_sh8_off[3], ME1_RSRC1, ME1_RSRC2);     DISPATCH(30, 68);
    PASS_BARRIER(); TS2(3);
    SET_PASS(g_sh8_off[9], MF8_RSRC1, MF8_RSRC2);     DISPATCH(30, 68);
    PASS_BARRIER();
    if (gv_pass_active()) {
        p = gv_emit_ts(p, slot, 2);
        SET_PASS(g_sh8_off[10], GV8_RSRC1, GV8_RSRC2);  DISPATCH(30, 68);
        PASS_BARRIER();
        p = gv_emit_after_gv8(p, seq, slot);
        p = gv_emit_ts(p, slot, 3);
    }
    TS_EOP(2);
    {
        int32_t fr = sceGnmInsertWaitFlipDone(p, 7, vo_handle, mid1_idx);
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
    DISPATCH(240, 135);
    PASS_BARRIER(); TS2(5);
    if (rcas_flags1) {
        SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
    }
    if (gv_viz_active()) {
        PASS_BARRIER();
        SET_PASS(g_sh8_off[13], GVZ8_RSRC1, GVZ8_RSRC2); DISPATCH(61, 35);
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
        make_t(tb2 + 16, M2, g_bw ? g_bw : 1920u, g_bh ? g_bh : 1080u, pitch, tidx); // T#out = M2
        tb2[29] = (mid2_idx & 0x7FFFu) | 0x8000u /*skip_st*/ | ((t2_q16 & 0xFFFFu) << 16);
        uint32_t rcas_con2 = 0, rcas_flags2 = 0, osd_wh2 = (osd_wh1 & 0x00FFFFFFu) | (3u << 24);
        calc_rcas_params(3u /*mid2 + cur*/, &rcas_con2, &rcas_flags2);
        fill_table8_ext(tb2, g_gpu, slotb, rcas_con2, rcas_flags2, osd_xy, osd_wh1 ? osd_wh2 : 0u);

        int32_t fr2 = sceGnmInsertWaitFlipDone(p, 7, vo_handle, mid2_idx);
        if (fr2 != 0) { p[0] = PKT3(0x10, 6, 0); for (int i = 1; i < 7; i++) p[i] = 0; }
        p += 7;

        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A; // inv scalar cache
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl2; *p++ = (uint32_t)(tbl2 >> 32);
        SET_PASS(g_sh8_off[4], PHASE8_RSRC1, PHASE8_RSRC2); DISPATCH(1, 1);
        PASS_BARRIER();
        if (gv_warp_active()) { SET_PASS(g_sh8_off[12], WARP8G_RSRC1, WARP8G_RSRC2); }
        else { SET_PASS(g_sh8_off[5], WARP8_RSRC1, WARP8_RSRC2); }
        DISPATCH(240, 135);
        PASS_BARRIER();
        if (rcas_flags2) {
            SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2); DISPATCH(240, 135);
            PASS_BARRIER();
        }
        if (osd_wh1) {
            PASS_BARRIER();
            SET_PASS(g_sh8_off[15], OSD8_RSRC1, OSD8_RSRC2);
            DISPATCH((osd_wh2 & 0xFFu) * 2u, ((osd_wh2 >> 8) & 0xFFu) * 2u);
        }
    }

    if (g_dump_req && g_dump_mem) {
        PASS_BARRIER();
        make_v(tb + 88, (uint64_t)(uintptr_t)g_dump_mem, 3 * 960 * 1080 * 4 + 163200);
        SET_PASS(g_sh8_off[6], DUMP8_RSRC1, DUMP8_RSRC2); DISPATCH(120, 135);
        {
            uint64_t dl = (uint64_t)(uintptr_t)(g_gpu + DUMP_DONE_OFF);
            *(volatile uint64_t *)(g_gpu + DUMP_DONE_OFF) = 0;
            *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
            *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)dl; *p++ = 0x40000000u | ((uint32_t)(dl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
        }
        g_dump_req = 0;
        g_dump_frame = seq;
    }
    TS2(6);
    TS_EOP(4);
#undef DISPATCH
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl; *p++ = 0x40000000u | ((uint32_t)(lbl >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl; *p++ = (uint32_t)(lbl >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    for (uint32_t i = 0; i < tail_dw; i++) *p++ = tail[i];
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
    // 1.4: Only attach to actual console game titles (CUSAxxxxx); never hook SceShellUI / NPXS* / Remote Play host
    g_is_game_proc = (strncmp(g_title_id, "CUSA", 4) == 0 &&
                      strstr(pi.name, "SceShell") == NULL &&
                      strstr(pi.name, "RemotePlay") == NULL);
    for (size_t i = 0; i < sizeof(g_builtin_profiles) / sizeof(g_builtin_profiles[0]); i++) {
        if (strcmp(g_title_id, g_builtin_profiles[i].title_id) == 0) {
            g_prof = g_builtin_profiles[i];
            break;
        }
    }
    if (g_is_game_proc) {
        char ppath[96];
        snprintf(ppath, sizeof(ppath), LOG_DIR "/profiles/%s.ini", g_title_id);
        int fd = sceKernelOpen(ppath, 0, 0);
        if (fd >= 0) {
            char buf[512] = {0};
            long n = (long)sceKernelRead(fd, buf, sizeof(buf) - 1);
            sceKernelClose(fd);
            if (n > 0) {
                char *p = buf;
                while (*p) {
                    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
                    char *eol = p;
                    while (*eol && *eol != '\r' && *eol != '\n') eol++;
                    char sv = *eol; *eol = 0;
                    int v = 0;
                    if (sscanf(p, "fg_default=%d", &v) == 1) g_prof.fg_default = (v != 0);
                    else if (sscanf(p, "max_mid_bufs=%d", &v) == 1 && v >= 1 && v <= 3) g_prof.max_mid_bufs = v;
                    else if (sscanf(p, "high_mem_alloc=%d", &v) == 1) g_prof.high_mem_alloc = (v != 0);
                    else if (sscanf(p, "late_mbuf_reg=%d", &v) == 1) g_prof.late_mbuf_reg = (v != 0);
                    else if (sscanf(p, "target_fps=%d", &v) == 1) g_prof.target_fps = v;
                    if (!sv) break;
                    p = eol + 1;
                }
            }
        }
    }
    g_known_game = g_prof.fg_default;
    if (g_prof.target_fps == 60) g_detected_60fps = 1;
}

static void crash_guard_check_on_load(void)
{
    char spath[96];
    snprintf(spath, sizeof(spath), LOG_DIR "/state-%s.ini", g_title_id);
    int fd = sceKernelOpen(spath, 0, 0);
    int pending = 0, fails = 0;
    if (fd >= 0) {
        char buf[128] = {0};
        long n = (long)sceKernelRead(fd, buf, sizeof(buf) - 1);
        sceKernelClose(fd);
        if (n > 0) {
            sscanf(buf, "boot_pending=%d\nfail_count=%d", &pending, &fails);
        }
    }
    if (pending) {
        fails++;
        g_crash_fail_count = fails;
        g_passive_mode = 1;
        g_known_game = 0;
        snprintf(g_inert_reason, sizeof(g_inert_reason), "SAFE MODE (CRASH #%d)", fails);
        // lg (not klog): bb_io writes it to both klog and probe.log, so safe mode is visible in probe.log too.
        lg("crash_guard: %s had unfinished boot (fail_count=%d) -> starting in SAFE PASSIVE MODE\n", g_title_id, fails);
    }
    g_boot_pending_dirty = 1;
}

static void crash_guard_io_write(int pending, int fails)
{
    char spath[96], tmp[104], buf[96];
    snprintf(spath, sizeof(spath), LOG_DIR "/state-%s.ini", g_title_id);
    snprintf(tmp, sizeof(tmp), "%s.tmp", spath);
    sceKernelMkdir(LOG_DIR, 0777);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    int len = snprintf(buf, sizeof(buf), "boot_pending=%d\nfail_count=%d\n", pending, fails);
    if (len > 0) fwrite(buf, 1, (size_t)len, f);
    fflush(f);
    fclose(f);
    rename(tmp, spath);
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
                else if (strcmp(key, "hud_protect") == 0) c->hud_protect = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "game_vectors") == 0) c->game_vectors = (val != 0);
                else if (strcmp(key, "fps_lock") == 0) c->fps_lock = (val == 20 || val == 30 || val == 60) ? val : 0;
                else if (strcmp(key, "menu_hold_ms") == 0) c->menu_hold_ms = val < 500 ? 500 : (val > 10000 ? 10000 : val);
                else if (strcmp(key, "menu_opacity") == 0) c->menu_opacity = val < 20 ? 20 : (val > 100 ? 100 : val);
                else if (strcmp(key, "overlay") == 0) c->overlay = val < 0 ? 0 : (val > 8 ? 8 : val);
            }
        }
        if (!saved) break;
        p = eol + 1;
    }
    return 1;
}

static void cfg_apply_live(const fg_config_t *c, const char *reason)
{
    fg_cfg_publish(c);
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

static void *alloc_ref_mbuf_relaxed_align(size_t total, const char *phase);
static void *alloc_mbufs(size_t total)
{
    // Try to preserve the normal reserve, then progressively relax it for REF.
    // REF's late-registration profile reaches this after native buffers exist.
    const size_t reserves[] = { GARLIC_SAFETY_RESERVE, 24ull << 20, 16ull << 20, 8ull << 20, 0 };
    int is_ref = a22_runtime_title();
    int late_ref = is_ref && g_prof.late_mbuf_reg;
    int n = late_ref ? 5 : (is_ref ? 4 : 1);
    for (int i = 0; i < n; i++) {
        void *p = alloc_dm_ex(total, 0x200000, 3 /*WC_GARLIC*/, g_prof.high_mem_alloc, reserves[i]);
        if (p) {
            if (i && reserves[i])
                lg("alloc_mbufs: REF used reduced reserve %u MiB (high-memory pool)\n", (unsigned)(reserves[i] >> 20));
            else if (is_ref && !reserves[i])
                lg("alloc_mbufs: REF allocated with no free-memory reserve in the profiled range\n");
            return p;
        }
    }
    // Last REF-only option for its late-registration profile: Garlic anywhere
    // in DirectMemory. Native game buffers already exist; this never runs in plugin_load.
    if (late_ref && g_prof.high_mem_alloc) {
        void *p = alloc_dm_ex(total, 0x200000, 3 /*WC_GARLIC*/, 0, 0);
        if (p) lg("alloc_mbufs: REF allocated from full Garlic range with no reserve\n");
        if (!p) p = alloc_ref_mbuf_relaxed_align(total, "lazy");
        return p;
    }
    if (late_ref) return alloc_ref_mbuf_relaxed_align(total, "lazy");
    return NULL;
}

static void *alloc_ref_mbuf_relaxed_align(size_t total, const char *phase)
{
    // Keep the 8 MiB footprint and Garlic type; change only physical alignment.
    const size_t aligns[] = { 0x10000, 0x8000 };
    int high = g_prof.high_mem_alloc;
    for (size_t i = 0; i < sizeof(aligns) / sizeof(aligns[0]); i++) {
        size_t align = aligns[i];
        lg("REF MBUF fallback: phase=%s trying align=0x%zx len=0x%zx type=3\n", phase, align, total);
        void *p = alloc_dm_ex(total, align, 3 /*WC_GARLIC*/, high, 0);
        if (p) {
            lg("REF MBUF fallback OK: phase=%s align=0x%zx va=%p\n", phase, align, p);
            return p;
        }
        if (high) {
            p = alloc_dm_ex(total, align, 3 /*WC_GARLIC*/, 0, 0);
            if (p) {
                lg("REF MBUF fallback OK: phase=%s align=0x%zx full-range va=%p\n", phase, align, p);
                return p;
            }
        }
    }
    return NULL;
}

// One-shot capability scan through the same kernel Allocate/Release API used by the plugin.
// Every successful probe is released immediately; this does not map or retain memory.
static int ref_dmem_scan_probe(size_t len, size_t align, int type, int high, size_t dm_sz)
{
    long start = (high && dm_sz > (256ull << 20)) ? (long)(dm_sz >> 1) : 0;
    long phys = 0;
    int r = sceKernelAllocateDirectMemory(start, (long)dm_sz, len, align, type, &phys);
    if (r != 0) {
        lg("REF DM SWEEP FAIL range=[0x%lx,0x%zx) len=0x%zx align=0x%zx type=%d rc=0x%08x\n",
           (unsigned long)start, dm_sz, len, align, type, (unsigned)r);
        return 0;
    }
    int rr = sceKernelReleaseDirectMemory(phys, len);
    lg("REF DM SWEEP %s range=[0x%lx,0x%zx) len=0x%zx align=0x%zx type=%d phys=0x%lx release=0x%08x\n",
       rr == 0 ? "PASS" : "RELEASE_FAIL", (unsigned long)start, dm_sz, len, align, type,
       (unsigned long)phys, (unsigned)rr);
    return rr == 0 ? 1 : -1;
}

static __attribute__((unused)) void ref_dmem_scan_once(void)
{
    if (__atomic_exchange_n(&g_ref_dmem_scan_done, 1, __ATOMIC_ACQ_REL)) return;
    size_t dm_sz = sceKernelGetDirectMemorySize();
    int high_count = g_prof.high_mem_alloc ? 2 : 1;
    // Test WB_GARLIC too: some titles/firmware combinations cannot provide a
    // large WC_GARLIC run after the game allocates its own surfaces, while a
    // cacheable Garlic allocation may still have a suitable contiguous span.
    // This is allocate/release only; do not register it unless a later build
    // explicitly validates CPU/GPU coherency for that memory type.
    const int types[] = { 3, 0xA, 0 }; // WC_GARLIC, WB_GARLIC, WB_ONION control
    const size_t garlic_sizes[] = { 0x800000, 0x7F8000, 0x600000, 0x400000, 0x200000, 0x100000 };
    lg("REF DM SWEEP BEGIN dm_sz=0x%zx; no Map; allocate/release only; one shot\n", dm_sz);
    for (int ti = 0; ti < 2; ti++) {
        int type = types[ti];
        int size_count = (type == 3 || type == 0xA) ? (int)(sizeof(garlic_sizes) / sizeof(garlic_sizes[0])) : 1;
        for (int hi = 0; hi < high_count; hi++) {
            int high = (hi == 0 && g_prof.high_mem_alloc) ? 1 : 0;
            for (int si = 0; si < size_count; si++) {
                size_t len = (type == 3 || type == 0xA) ? garlic_sizes[si] : 0x800000;
                size_t align = 0x8000;
                int r = ref_dmem_scan_probe(len, align, type, high, dm_sz);
                if (r < 0) {
                    lg("REF DM SWEEP ABORT: ReleaseDirectMemory failed; no more probes\n");
                    return;
                }
            }
        }
    }
    lg("REF DM SWEEP END\n");
}

// Unused since the RegisterBuffers-time REF reserve was removed; kept for reference/A-B builds.
static __attribute__((unused)) void *alloc_ref_mbuf_reserve(size_t total, const char *phase)
{
    // Reserve one display surface while retaining as much free Garlic memory
    // as possible when the game first requests its mid-frame buffer. GPU/work
    // allocations remain deferred separately for REF.
    const size_t reserves[] = { GARLIC_SAFETY_RESERVE, 24ull << 20, 16ull << 20, 8ull << 20 };
    for (int i = 0; i < 4; i++) {
        void *p = alloc_dm_ex(total, 0x200000, 3 /*WC_GARLIC*/, g_prof.high_mem_alloc, reserves[i]);
        if (p) {
            lg("REF MBUF reserved: phase=%s reserve=%u MiB va=%p\n", phase,
               (unsigned)(reserves[i] >> 20), p);
            return p;
        }
    }
    // Last resort: take only the required surface, then try the full range and
    // relaxed physical alignment. Caller controls when this is safe to try.
    void *p = alloc_dm_ex(total, 0x200000, 3 /*WC_GARLIC*/, g_prof.high_mem_alloc, 0);
    if (p) {
        lg("REF MBUF reserved: phase=%s reserve=0 va=%p\n", phase, p);
        return p;
    }
    if (g_prof.high_mem_alloc) {
        p = alloc_dm_ex(total, 0x200000, 3 /*WC_GARLIC*/, 0, 0);
        if (p) {
            lg("REF MBUF reserved: phase=%s full-range va=%p\n", phase, p);
            return p;
        }
    }
    p = alloc_ref_mbuf_relaxed_align(total, phase);
    if (p) return p;
    lg("REF MBUF reserve failed: phase=%s; runtime retry remains enabled\n", phase);
    return NULL;
}

static int mbufs_ready(uint32_t handle)
{
    (void)handle;
    return g_mbuf_state == 1;
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
        switch (b[i]) {
        case 'x': f |= 1; break;
        case 'e': f |= 2; break;
        case 'm': f |= 4; break;
        case 'p': f |= 8; break;
        case 'd': f |= 16; break;
        case 'D': f |= 32; break;
        case 'k': f |= 64; break;
        case 'f': f |= 128; break;
        case 'g': f |= 256; break;
        case 'L': f |= 512; break;
        case 'T': f |= 1024; break;
        case 'M': f |= 2048; break;
        case 'G': f |= 4096; break;
        case 'P': f |= 8192; break;
        case 'S': f |= 16384; break;
        case 'b': f |= 32768; break;
        case 'v': g |= GVL_ON; break;
        case 'n': g |= GVL_NOAPPLY; break;
        case 's': g |= GVL_SCAN; break;
        case 'V': g |= GVL_DUMP; break;
        case 'r': g |= GVL_RECAL; break;
        case 'w': g |= GVL_SPLIT; break;
        case 'q': g |= GVL_NOSPLICE; break;
        case 'a': g |= GVL_ANCHOR; break;
        case 'z': g |= GVL_VIZ; break;
        case 'W': g |= GVL_WIDE; break;
        default: break;
        }
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
    fprintf(f, "hud_protect = %d\n", c.hud_protect);
    fprintf(f, "game_vectors = %d\n", c.game_vectors);
    fprintf(f, "fps_lock = %d\n", c.fps_lock);
    fprintf(f, "menu_hold_ms = %d\n", c.menu_hold_ms);
    fprintf(f, "menu_opacity = %d\n", c.menu_opacity);
    fprintf(f, "overlay = %d\n", c.overlay);
    fflush(f);
    fclose(f);
    rename(tmp, path);
    g_last_disk_hash = 0;
    cfg_reload_from_disk(0);
    snprintf(g_osd_toast, sizeof(g_osd_toast), "SAVED CONFIG-%s.INI", g_title_id);
    g_osd_toast_until = sceKernelGetProcessTime() + 2500000ull;
    lg("config saved to %s\n", path);
}

static void pad_poll_start(void);
static void *io_thread(void *arg)
{
    (void)arg;
    __atomic_store_n(&g_lg_io_owner_tid, scePthreadGetthreadid(), __ATOMIC_RELEASE);
    unsigned poll_n = 0;
    int no_flip_logged = 0;
    int crash_cleared = 0;
    uint64_t diag_window_next = 0;
    for (;;) {
        sceKernelUsleep(2000);
        if (g_lgq_r != g_lgq_w) lg_drain();
        if (__atomic_exchange_n(&g_diag_map_ready, 0u, __ATOMIC_ACQ_REL)) {
            diag_dump_dmem("before_io_and_hooks", &g_diag_early);
            diag_virtual_low("worker_start");
            diag_modules("worker_start");
        }
        // Query-only periodic measurements, on the I/O worker, never in a game hook.
        // Non-atomic samples cannot prove that an interval was free between samples.
        if (a22_runtime_title() && g_flips >= 300) {
            uint64_t now = sceKernelGetProcessTime();
            if (now >= diag_window_next) {
                diag_window_next = now + 5000000ull;
                lg("A6_COUNTERS t=%llu status=%u getdata=%u waits=%u submits=%u dropped=%u\n",
                   (unsigned long long)now,__atomic_load_n(&g_a6_counts[0],__ATOMIC_RELAXED),
                   __atomic_load_n(&g_a6_counts[1],__ATOMIC_RELAXED),__atomic_load_n(&g_a6_counts[2],__ATOMIC_RELAXED),
                   __atomic_load_n(&g_a6_counts[3],__ATOMIC_RELAXED),g_lgq_drop);
                const long starts[2] = {0x130000L, 0x11FB00000L};
                const long ends[2] = {0x200000L, 0x120000000L};
                for (unsigned wi = 0; wi < 2; wi++) {
                    long free_at = 0; size_t free_size = 0;
                    int rc = sceKernelAvailableDirectMemorySize(starts[wi], ends[wi], 0x4000,
                        (off_t)(uintptr_t)&free_at, (size_t)(uintptr_t)&free_size);
                    int intact = rc == 0 && free_at == starts[wi] && free_size == (size_t)(ends[wi]-starts[wi]);
                    lg("A3_WINDOW t_us=%llu flips=%u window=%u search=[0x%lx,0x%lx) rc=0x%08x free_at=0x%lx max=0x%zx intact=%d\n",
                       (unsigned long long)now, (unsigned)g_flips, wi, (unsigned long)starts[wi], (unsigned long)ends[wi],
                       (unsigned)rc, (unsigned long)free_at, free_size, intact);
                }
            }
        }
        dcb_mem_process_queue();
        if (a22_runtime_title()) a20_worker_tick();
#if !BB_DIAG_ALLOC_A
        if (__atomic_load_n(&g_ref_mbuf_prereserve_req, __ATOMIC_ACQUIRE) &&
            sceKernelGetProcessTime() >= g_ref_mbuf_prereserve_after_us &&
            __atomic_exchange_n(&g_ref_mbuf_prereserve_req, 0u, __ATOMIC_ACQ_REL)) {
            if (a22_runtime_title() && !g_passive_mode && !pre_mem_load() && g_start_mode == 8) {
                const size_t reserve_sz = 0x800000u; // one 1080p mid surface; actual stride is read at RegisterBuffers
                lg("REF async MBUF reserve: worker context after plugin_load, len=0x%zx\n", reserve_sz);
                void *p = alloc_mbufs(reserve_sz);
                if (p) {
                    g_pre_size = reserve_sz;
                    __atomic_store_n(&g_pre_mem, p, __ATOMIC_RELEASE);
                    lg("REF async MBUF reserve: OK va=%p len=0x%zx; registration remains lazy\n", p, reserve_sz);
                } else {
                    lg("REF async MBUF reserve: FAILED; lazy runtime allocation remains available\n");
                }
            } else {
                lg("REF async MBUF reserve: skipped (title=%s mode=%d passive=%d pre_mem=%p)\n",
                   g_title_id, g_start_mode, g_passive_mode, pre_mem_load());
            }
        }
#endif
        if (__atomic_exchange_n(&g_gpu_init_req, 0u, __ATOMIC_ACQ_REL) &&
            __atomic_load_n(&g_gpu_state, __ATOMIC_ACQUIRE) == 0) {
            lg("gpu_init deferred request: starting from I/O thread\n");
            gpu_init();
        }
        if (__atomic_exchange_n(&g_mbuf_reg_req, 0, __ATOMIC_ACQ_REL) && g_mbuf_state == 0 && g_handle >= 0) {
            uint64_t mbuf_now = sceKernelGetProcessTime();
            if (mbuf_now >= g_mbuf_retry_after_us) {
                int registered = ensure_mbufs_registered(g_handle);
                if (registered) {
                    g_mbuf_retry_after_us = 0;
                } else if (g_mbuf_state == 0) {
                    g_mbuf_retry_after_us = mbuf_now + 5000000ull;
                    lg("mbufs lazy: allocation not available; retrying in 5 seconds\n");
                }
            }
        }
        if (!g_activation_done && g_flips >= INJECT_AFTER_FLIPS) {
            g_inject_on = read_mode_raw();
            g_start_mode = g_inject_on;
            file_flags();
            cfg_reload_from_disk(1);
            // Safe mode keeps mode 8 only as a menu renderer. Frame generation,
            // cadence changes and buffer registration remain gated by passive mode.
            if (g_passive_mode) g_inject_on = (g_start_mode == 8) ? 8 : 0;
            if (g_passive_mode) { /* keep "SAFE MODE (CRASH #n)" from crash_guard_check_on_load visible */ }
            else if (!g_fmt_supported) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: HDR/FMT 0x%x", g_attr.format);
            else if (g_bw != 1920 || g_bh != 1080) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: RES %ux%u", g_bw, g_bh);
            else if (!mbufs_ready(g_handle)) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO MBUF (%d)", g_nbuf);
            else if (g_detected_60fps && g_cfg.fps_lock != 30) snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
            else g_inert_reason[0] = 0;
            lg("injection mode %d (title %s, known=%d, fg_off=%d, preset=%d, sharpness=%d%%, enhance=%d, overlay=%d, inert='%s'), work memory %s\n",
               g_inject_on, g_title_id, g_known_game, g_fg_off, g_cfg.preset, g_cfg.sharpness, g_cfg.upscale, g_cfg.overlay,
               g_inert_reason, g_work_garlic ? "garlic" : "onion");
            if (a22_runtime_title())
                dmem_report_free("REF activation pre-gpu_init");
            if (g_passive_mode) {
                // The monitor starts the pad poller in normal mode. Start only
                // the input poller in safe mode so L3+R3 can open the OSD.
                pad_poll_start();
            } else if (g_inject_on >= 6) {
                mon_start();
            }
            gv_set_display(g_buf, g_nbuf, g_bw, g_bh);
            if (!g_passive_mode)
                gv_start(g_inject_on == 8 && g_bw == 1920 && g_bh == 1080);
            g_activation_done = 1;
        }
        if (__atomic_exchange_n(&g_boot_pending_dirty, 0, __ATOMIC_ACQ_REL)) {
            crash_guard_io_write(1, g_crash_fail_count);
        }
        if (!crash_cleared && g_flips >= 300) {
            crash_cleared = 1;
            crash_guard_io_write(0, 0);
            lg("crash_guard: reached 300 flips cleanly -> state-%s.ini marked OK\n", g_title_id);
            if (a22_runtime_title()) {
                DiagDmemSnapshot snapshot;
                diag_capture_dmem(&snapshot);
                diag_dump_dmem("300_flips", &snapshot);
                diag_virtual_low("300_flips");
                diag_modules("300_flips");
                dmem_report_free("DIAG_A2 after 300 flips");
            }
        }
        if (__atomic_exchange_n(&g_cfg_save_req, 0, __ATOMIC_ACQ_REL)) {
            cfg_save_game_ini();
            if (g_passive_mode) __atomic_store_n(&g_leave_passive_req, 1, __ATOMIC_RELEASE);
        }
        if (__atomic_exchange_n(&g_leave_passive_req, 0, __ATOMIC_ACQ_REL) && g_passive_mode && g_activation_done) {
            // Explicit user request from the menu. Start everything normal mode starts at activation;
            // previously only g_passive_mode was cleared, so bb_mon (g_mon timing for the shaders) never ran.
            g_passive_mode = 0;
            g_crash_fail_count = 0;
            if (g_inert_reason[0] && strncmp(g_inert_reason, "SAFE MODE", 9) == 0) g_inert_reason[0] = 0;
            if (g_start_mode == 8 && g_inject_on != 8) g_inject_on = 8;
            if (g_inject_on >= 6) mon_start();
            gv_start(g_inject_on == 8 && g_bw == 1920 && g_bh == 1080);
            crash_guard_io_write(0, 0);
            lg("passive mode left on user request (inject=%d, mbuf_state=%d, fg_off=%d)\n", g_inject_on, g_mbuf_state, g_fg_off);
        }
        if (__atomic_exchange_n(&g_fg_guard_arm_req, 0, __ATOMIC_ACQ_REL)) {
            // A crash while FG is active must put the next boot into safe mode; the 300-flip boot guard is long cleared.
            g_fg_guard_flip0 = g_flips ? g_flips : 1u;
            crash_guard_io_write(1, 0);
            lg("crash_guard: FG activated at flip %u -> state marked pending until %u stable flips\n", g_fg_guard_flip0, FG_GUARD_FLIPS);
        }
        if (g_fg_guard_flip0 && g_flips - g_fg_guard_flip0 >= FG_GUARD_FLIPS) {
            g_fg_guard_flip0 = 0;
            crash_guard_io_write(0, 0);
            lg("crash_guard: FG stable for %u flips -> state marked OK\n", FG_GUARD_FLIPS);
        }
        if (g_inject_on == 8) gv_io_tick();
        poll_n++;
        if (!no_flip_logged && poll_n == 5000 && g_flips == 0) {
            no_flip_logged = 1;
            snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO GNM FLIP");
            lg("inert: no flip hook (0 flips after 10 s; game does not call sceGnmSubmitAndFlipCommandBuffers, voSubmitFlips=%u)\n",
               g_vo_submit_flips);
        }
        if ((poll_n % 250) == 0 && g_inject_on == 8) {
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
        if (sceVideoOutGetFlipStatus(g_handle, &fs) != 0) continue;
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
                    g_hist[last_buf >= g_ngame ? 0 : 1][nv - 1]++;
                    if (last_buf < g_ngame && g_mon) {
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
                int cur_mid = fs.currentBuffer >= g_ngame, last_mid = last_buf >= g_ngame;
                if (nf > 1 && fs.currentBuffer >= 0 && last_buf >= 0 && ((nf & 1) ? cur_mid != last_mid : cur_mid == last_mid)) {
                    uint64_t a = (nf + 1) / 2, b = nf / 2;
                    if (cur_mid) { g_mids_shown += a; g_reals_shown += b; } else { g_reals_shown += a; g_mids_shown += b; }
                    g_mon_gaps++;
                } else {
                    if (fs.currentBuffer >= 0 && fs.currentBuffer < g_ngame && last_buf >= g_ngame) g_reals_shown++;
                    if (fs.currentBuffer >= g_ngame && last_buf >= 0 && fs.currentBuffer != last_buf) g_mids_shown++;
                }
            }
            if (fs.currentBuffer >= 0 && fs.currentBuffer < g_ngame) g_last_real_buf = fs.currentBuffer;
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
            uint32_t uvb_ = __atomic_exchange_n(&g_unfilled_vblanks, 0u, __ATOMIC_ACQ_REL);
            uint32_t tvb_ = __atomic_exchange_n(&g_total_sched_vblanks, 0u, __ATOMIC_ACQ_REL);
            double unfill_pct = tvb_ ? (double)uvb_ * 100.0 / (double)tvb_ : 0.0;
            lg("CADENCE (vblanks on screen) mid 1:%u 2:%u 3:%u 4+:%u | real 1:%u 2:%u 3:%u 4+:%u | refresh %.3f Hz | "
               "pending max %d gcq max %d | missed %u (gaps %u) | unfilled %.1f%% (%u/%u) | pace timeouts %u resync %u fix %u | game cpu release->submit avg %.1f max %.1f ms\n",
               g_hist[0][0], g_hist[0][1], g_hist[0][2], g_hist[0][3], g_hist[1][0], g_hist[1][1], g_hist[1][2], g_hist[1][3],
               hz, pend_max, gcq_max, g_mon_missed, g_mon_gaps, unfill_pct, uvb_, tvb_,
               g_pace_timeouts, g_pace_resync, g_pace_fix,
               cn_ ? csum_ / 1000.0 / cn_ : 0.0, cmax_ / 1000.0);
            memset(g_hist, 0, sizeof(g_hist));
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
    int64_t target = (int64_t)g_dbl_frames - 1 + g_pace_off;
    int64_t have = (int64_t)*cnt;
    if (have > target) {
        g_pace_off = have - ((int64_t)g_dbl_frames - 1);
        g_pace_resync++;
        g_rel_t = sceKernelGetProcessTime();
        return;
    }
    uint64_t t0 = sceKernelGetProcessTime();
    while ((int64_t)*cnt < target) {
        if (sceKernelGetProcessTime() - t0 > 60000) {
            g_pace_off = (int64_t)*cnt - ((int64_t)g_dbl_frames - 1);
            g_pace_timeouts++;
            break;
        }
        sceKernelUsleep(200);
    }
    int distinct_ok = (g_ngame == 2) ? (prev_real >= 0 && prev_real != cur_real)
                                     : (prev2_real != cur_real && prev_real != cur_real && prev2_real != prev_real);
    if (distinct_ok) {
        VoFlipStatus fs;
        memset(&fs, 0, sizeof(fs));
        if (sceVideoOutGetFlipStatus(g_handle, &fs) == 0) {
            int late = fs.currentBuffer == cur_real ||
                       (g_opt_early && fs.currentBuffer >= g_ngame && g_last_real_buf == prev_real);
            if (late) { g_pace_off--; g_pace_fix++; }
        }
    }
    g_rel_t = sceKernelGetProcessTime();
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
        // 1.4: Auto-detect 60 fps games during warmup (flips 30..120) before FG pacing begins
        if (n >= 60 && n <= INJECT_AFTER_FLIPS && sm_rfps >= 480u && !g_detected_60fps) {
            g_detected_60fps = 1;
            snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
            lg("auto-detect: game runs at %.1f fps (>= 48 fps) -> VCFG auto-disabled (60 Hz output limit)\n", rfps);
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

typedef struct { uintptr_t s, e; int32_t prot; } dcb_mrange_t;
static dcb_mrange_t g_dcb_mc[64];
static uint32_t g_dcb_mcn = 0;
static int dcb_mem_ok(uintptr_t a, size_t n)
{
    if (!a || !n || a + n < a) return 0;
    uintptr_t end_a = a + n;
    for (uint32_t i = 0; i < g_dcb_mcn && i < 64u; i++) {
        if (a >= g_dcb_mc[i].s && end_a <= g_dcb_mc[i].e)
            return (g_dcb_mc[i].prot & 0x01 /*PROT_CPU_READ*/) != 0;
    }
    void *s = NULL, *en = NULL;
    int32_t prot = 0;
    if (sceKernelQueryMemoryProtection((void *)a, &s, &en, &prot) != 0) return 0;
    if ((uintptr_t)s > a || (uintptr_t)en < end_a) return 0;
    uint32_t mc_n = __atomic_load_n(&g_dcb_mcn, __ATOMIC_RELAXED);
    if (mc_n < 64u) {
        g_dcb_mc[mc_n] = (dcb_mrange_t){ (uintptr_t)s, (uintptr_t)en, prot };
        __atomic_store_n(&g_dcb_mcn, mc_n + 1u, __ATOMIC_RELEASE);
    }
    return (prot & 0x01 /*PROT_CPU_READ*/) != 0;
}

typedef struct { uintptr_t a; size_t n; } dcb_mem_req_t;
static dcb_mem_req_t g_dcb_mem_q[64];
static volatile uint32_t g_dcb_mem_w = 0, g_dcb_mem_r = 0;
static int dcb_mem_cached(uintptr_t a, size_t n)
{
    if (!a || !n || a + n < a) return 0;
    uintptr_t e = a + n;
    uint32_t count = __atomic_load_n(&g_dcb_mcn, __ATOMIC_ACQUIRE);
    for (uint32_t i = 0; i < count && i < 64u; i++)
        if (a >= g_dcb_mc[i].s && e <= g_dcb_mc[i].e) return (g_dcb_mc[i].prot & 0x01) != 0;
    uint32_t w = __atomic_load_n(&g_dcb_mem_w, __ATOMIC_RELAXED);
    uint32_t r = __atomic_load_n(&g_dcb_mem_r, __ATOMIC_ACQUIRE);
    if (w - r < 64u) {
        g_dcb_mem_q[w % 64u] = (dcb_mem_req_t){ a, n };
        __atomic_store_n(&g_dcb_mem_w, w + 1u, __ATOMIC_RELEASE);
    }
    return 0;
}
static void dcb_mem_process_queue(void)
{
    for (;;) {
        uint32_t r = __atomic_load_n(&g_dcb_mem_r, __ATOMIC_RELAXED);
        uint32_t w = __atomic_load_n(&g_dcb_mem_w, __ATOMIC_ACQUIRE);
        if (r == w) break;
        dcb_mem_req_t q = g_dcb_mem_q[r % 64u];
        dcb_mem_ok(q.a, q.n);
        __atomic_store_n(&g_dcb_mem_r, r + 1u, __ATOMIC_RELEASE);
    }
}

static int find_flip_tail_pm4(const uint32_t *dw, uint32_t nd, uint32_t *tail_off_out, uint32_t *tail_dw_out)
{
    if (!dw || nd < 32u || nd > 65536u) return 0; // bound flip-hook scan time; oversized DCB stays untouched
    uint32_t pos = 0, last_off = 0, last_len = 0, last_op = 0, last_b0 = 0;
    while (pos < nd) {
        uint32_t h = dw[pos];
        uint32_t type = h >> 30;
        if (type == 2u) { pos++; continue; }
        if (type == 1u) return 0;
        uint32_t cnt = (h >> 16) & 0x3FFFu;
        uint32_t op = (type == 3u) ? ((h >> 8) & 0xFFu) : 0u;
        uint32_t len = (type == 3u && op == 0x10u && cnt == 0x3FFFu) ? 1u : (cnt + 2u);
        if (pos + len > nd) return 0;
        last_off = pos;
        last_len = len;
        last_op = op;
        last_b0 = (len >= 2u) ? dw[pos + 1u] : 0u;
        pos += len;
    }
    if (pos == nd && last_op == 0x10u && last_len >= 32u && last_len <= 128u &&
        (last_b0 >> 8) == 0x687507u && last_off + last_len == nd) {
        *tail_off_out = last_off;
        *tail_dw_out = last_len;
        return 1;
    }
    return 0;
}

#include "vov_glue.h"
#include "a20_copy_impl.h"

static int ensure_mbufs_registered(int32_t handle)
{
    if (g_mbuf_state == 1) return 1;
    if (g_mbuf_state < 0 || g_ngame < 2 || g_ngame > 8 || !g_mbuf_stride) return 0;
    g_mbuf_state = -1;
    int max_extra = 16 - g_ngame;
    if (max_extra > g_prof.max_mid_bufs) max_extra = g_prof.max_mid_bufs;
    if (max_extra < 1) max_extra = 1;
    uint8_t *m = (uint8_t *)pre_mem_load();
    int nm = m ? (int)(g_pre_size / g_mbuf_stride) : 0;
    if (nm > max_extra) nm = max_extra;
    for (int want = max_extra; (!m || nm < 1) && want >= 1; want--) {
        m = (uint8_t *)alloc_mbufs((g_mbuf_stride * (size_t)want + 0x1FFFFFu) & ~(size_t)0x1FFFFFu);
        if (m) nm = want;
    }
    if (!m || nm < 1) {
        g_mbuf_state = 0; // allocation failure is recoverable; do not latch the game inert
        // Query-only measurement replaces the Allocate/Release sweep (ref_dmem_scan_once), which raced the game
        // for its last free DirectMemory with ~20 extra allocations.
        dmem_report_free("mbuf alloc failed");
        snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: MBUF ALLOC");
        lg("mbufs lazy: allocation failed (game=%d max_extra=%d stride=0x%zx); see dm stage/range diagnostics; retryable\n",
           g_ngame, max_extra, g_mbuf_stride);
        // REF measurements: after its init only ~5 MiB of DirectMemory is free (2 MiB allocates, 4 MiB does not),
        // so an 8 MiB surface can never appear later. Stop the 5-second retry loop instead of probing forever.
        if (a22_runtime_title() && ++g_mbuf_fail_n >= MBUF_MAX_FAILS) {
            g_mbuf_state = -2;
            snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO DMEM 8MB");
            lg("mbufs lazy: REF gave up after %d attempts; VCFG stays native this session (see ANALYSIS: DirectMemory budget)\n",
               g_mbuf_fail_n);
        }
        return 0;
    }
    // Keep ownership even when VideoOut registration fails. Cleanup requires a
    // separate unregister/quiescence protocol, so do not retry with this block.
    g_mbuf_mem = m;
    void *ext_only[4];
    for (int i = 0; i < nm; i++) ext_only[i] = m + (size_t)i * g_mbuf_stride;
    int32_t r = HOOK_CONTINUE(sceVideoOutRegisterBuffers,
                              int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                              handle, g_ngame, ext_only, nm, &g_attr);
    // Successful calls can return a non-negative buffer-set index (the prior
    // working REF run returned 1 for startIndex=3). Never re-register the
    // game's occupied indices 0..g_ngame-1 as an error fallback.
    lg("mbufs (lazy): %d extra buffer(s) of 0x%zx at %p, RegisterBuffers(start=%d,num=%d) -> r=0x%08x\n",
       nm, g_mbuf_stride, (void *)m, g_ngame, nm, (unsigned)r);
    if (r >= 0) {
        for (int i = 0; i < nm; i++) g_buf[g_ngame + i] = (uint64_t)(uintptr_t)ext_only[i];
        g_nmid = nm;
        g_nbuf = g_ngame + nm;
        g_mbuf_state = 1;
        g_inert_reason[0] = 0;
        g_mbuf_retry_after_us = 0;
        return 1;
    }
    g_mbuf_state = -1; // registration failure is latched to avoid leaking the allocated buffer on retries
    snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: MBUF REG");
    lg("mbufs lazy: RegisterBuffers failed (0x%08x); not retrying to avoid leaked memory\n", (unsigned)r);
    return 0;
}

static int32_t flip_inner(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    uint64_t prev_flip_t = g_last;
    g_flips++;
    if (g_flips == 1) lg("first SubmitAndFlip: count=%u flipMode=%u title=%s\n", count, flipMode, g_title_id);
    if (g_rel_t) {
        uint64_t us = sceKernelGetProcessTime() - g_rel_t;
        g_rel_t = 0;
        if (us < 1000000) { g_cpu_n++; g_cpu_sum_us += (uint32_t)us; if (us > g_cpu_max_us) g_cpu_max_us = (uint32_t)us; }
    }
    tick(g_flips, handle, index, flipMode);
    int32_t a20_rc;
    if (a20_try_submit(count,dcb,dcbSz,ccb,ccbSz,handle,index,flipMode,flipArg,&a20_rc)) return a20_rc;
    if (a22_runtime_title())
        return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
            int32_t (*)(uint32_t,void **,uint32_t *,void **,uint32_t *,uint32_t,uint32_t,uint32_t,int64_t),
            count,dcb,dcbSz,ccb,ccbSz,handle,index,flipMode,flipArg);
    uint64_t raw_dt_us = (prev_flip_t && g_freq) ? ((g_last - prev_flip_t) * 1000000ull / g_freq) : 33366ull;
    int render_needed = 1;
    if (g_inject_on == 8) {
        fg_config_t live_cfg;
        fg_cfg_read(&live_cfg);
        render_needed = g_passive_mode
                            ? (g_menu_open != 0)
                            : (!g_fg_off || g_ab_x_flag || g_menu_open || live_cfg.overlay > 0 || live_cfg.upscale > 0);
        if (render_needed && __atomic_load_n(&g_gpu_state, __ATOMIC_ACQUIRE) == 0)
            __atomic_store_n(&g_gpu_init_req, 1u, __ATOMIC_RELEASE);
    }

#if INJECT
    if (g_inject_on && render_needed && g_flips >= INJECT_AFTER_FLIPS && count >= 1 && count <= (gv_armed() ? GV_MAXE - 1u : 6u) && dcb && dcbSz && index < (uint32_t)g_nbuf) {
        int dcb_valid = 1;
        for (uint32_t i = 0; i < count; i++) {
            if (!dcb[i] || !dcbSz[i] || (dcbSz[i] & 3u) || dcbSz[i] > (16u << 20) || !dcb_mem_cached((uintptr_t)dcb[i], dcbSz[i])) {
                dcb_valid = 0;
                static int dcb_err_logged = 0;
                if (!dcb_err_logged) {
                    dcb_err_logged = 1;
                    lg("fallback: DCB[%u] ptr=%p sz=%u not validated yet (first sight of this range is queued to bb_io) -> original SubmitAndFlip\n",
                       i, dcb[i], dcbSz[i]);
                }
                break;
            }
        }
        if (dcb_valid && __atomic_load_n(&g_gpu_state, __ATOMIC_ACQUIRE) == 0)
            __atomic_store_n(&g_gpu_init_req, 1u, __ATOMIC_RELEASE);
        if (dcb_valid && __atomic_load_n(&g_gpu_state, __ATOMIC_ACQUIRE) == 1) {
            uint32_t last = count - 1;
            uint32_t nd = dcbSz[last] / 4;
            uint32_t tail_off = 0, tail_dw = 0;
            int tail_ok = find_flip_tail_pm4((const uint32_t *)dcb[last], nd, &tail_off, &tail_dw);
            const uint32_t *tail = tail_ok ? ((const uint32_t *)dcb[last] + tail_off) : NULL;
            if (tail_ok && tail) {
                uint32_t slot = g_flips & 3;
                fg_config_t cfg_cur;
                fg_cfg_read(&cfg_cur);
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
                                if (cn > CUT_BLOCKS) g_st_cuts++;
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
                uint32_t flip_idx = index;
                uint32_t mid2_flip_idx = 0;
                int second_flip = 0;
                int third_flip = 0;

                int want_fg = (!g_fg_off || g_ab_x_flag) && (!g_detected_60fps || cfg_cur.fps_lock == 30) && !g_passive_mode;
                int can_fg8 = (g_inject_on == 8 && g_work && g_sh8_ok && g_fmt_supported &&
                               g_bw == 1920 && g_bh == 1080 && g_prev_idx < (uint32_t)g_ngame && mbufs_ready(handle));
                if (want_fg && g_mbuf_state == 0)
                    __atomic_store_n(&g_mbuf_reg_req, 1u, __ATOMIC_RELEASE);
                // Do not force 60 Hz until the mid-buffer and all mode-8 resources are ready.
                if (want_fg && can_fg8 && !g_forced_flip_rate_zero && g_handle >= 0) {
                    g_forced_flip_rate_zero = 1;
                    HOOK_CONTINUE(sceVideoOutSetFlipRate, int32_t (*)(int32_t, int32_t), g_handle, 0);
                    lg("SetFlipRate: switched handle=%d to rate=0 (60 Hz output for active FG)\n", g_handle);
                    __atomic_store_n(&g_fg_guard_arm_req, 1, __ATOMIC_RELEASE);
                } else if ((!want_fg || !can_fg8) && g_forced_flip_rate_zero && g_handle >= 0 && g_game_flip_rate > 0) {
                    g_forced_flip_rate_zero = 0;
                    HOOK_CONTINUE(sceVideoOutSetFlipRate, int32_t (*)(int32_t, int32_t), g_handle, g_game_flip_rate);
                    lg("SetFlipRate: restored handle=%d to game rate=%d (FG off/resources pending)\n", g_handle, g_game_flip_rate);
                }

                if (can_fg8 && want_fg) {
                    g_inert_reason[0] = 0;
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t tidx = (g_attr.tilingMode == 0) ? 10u : 8u;
                    uint32_t mi1 = (uint32_t)g_ngame + (g_flips % (uint32_t)g_nmid);
                    uint32_t mi2 = (uint32_t)g_ngame + ((g_flips + 1u) % (uint32_t)g_nmid);
                    uint32_t t1_q16 = 32768u, t2_q16 = 43690u;
                    int two_mids = 0;
                    int force_repeat = (raw_dt_us > 66667ull) ? 1 : 0; // >66.7 ms (<15 fps hitch) -> repeat frame

                    if (cfg_cur.preset == 1 && !g_fg_off) {
                        // 1.1 FG Fast (direct sigma-delta vblank integrator for 20..40 fps + 3x lock at 18..20 fps):
                        int m_out = 2;
                        int32_t acc_prev = g_fast_acc_q16;
                        if (force_repeat) {
                            g_fast_acc_q16 = 0;
                            t1_q16 = 0;
                        } else if (raw_dt_us >= 49000ull && g_ema_frame_us >= 47500u) {
                            m_out = 3;
                            g_fast_acc_q16 = 0;
                            acc_prev = 0;
                        } else {
                            uint32_t dt_clamped = (uint32_t)(raw_dt_us > 100000ull ? 100000u : raw_dt_us);
                            if (dt_clamped < 25000u) dt_clamped = 25000u; // 40 fps
                            if (dt_clamped > 50050u) dt_clamped = 50050u; // 20 fps (3 * 16683 us)
                            int32_t r_q16 = (int32_t)((((uint64_t)dt_clamped << 16) + 8341ull) / 16683ull);
                            int32_t acc_new = acc_prev + r_q16;
                            m_out = (acc_new >= ((3 << 16) - 29491)) ? 3 : 2;
                            if (__atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE) >= 4u) m_out = 2;
                            g_fast_acc_q16 = acc_new - (m_out << 16);
                            if (g_fast_acc_q16 < -32768) g_fast_acc_q16 = -32768;
                            if (g_fast_acc_q16 >  32768) g_fast_acc_q16 =  32768;
                        }
                        if (!force_repeat) {
                            int32_t phase_shift = (acc_prev * 3) >> 5;
                            if (m_out == 3) {
                                int32_t p1 = 21845 + phase_shift; // ~1/3 (0.333)
                                int32_t p2 = 43690 + phase_shift; // ~2/3 (0.667)
                                if (p1 < 16384) p1 = 16384;
                                if (p1 > 27525) p1 = 27525;
                                if (p2 < 38010) p2 = 38010;
                                if (p2 > 49152) p2 = 49152;
                                t1_q16 = (uint32_t)p1;
                                t2_q16 = (uint32_t)p2;
                                if (g_nmid >= 2 && mi1 != mi2) {
                                    two_mids = 1;
                                    mid2_flip_idx = mi2;
                                    third_flip = 1;
                                } else {
                                    t1_q16 = 32768u;
                                }
                            } else {
                                int32_t p1 = 32768 + phase_shift;
                                if (p1 < 22937) p1 = 22937;
                                if (p1 > 42598) p1 = 42598;
                                t1_q16 = (uint32_t)p1;
                            }
                        }
                        uint32_t vb_need = (uint32_t)((raw_dt_us + 8341ull) / 16683ull);
                        if (vb_need < 2u) vb_need = 2u;
                        if (vb_need > 3u) vb_need = 3u;
                        uint32_t vb_got = (two_mids ? 3u : 2u);
                        __atomic_add_fetch(&g_total_sched_vblanks, vb_need, __ATOMIC_RELAXED);
                        if (vb_got < vb_need) __atomic_add_fetch(&g_unfilled_vblanks, vb_need - vb_got, __ATOMIC_RELAXED);
                    } else if (!g_fg_off) {
                        uint32_t prev_hold = __atomic_load_n(&g_last_real_hold_us, __ATOMIC_ACQUIRE);
                        uint32_t tot_us = g_ema_frame_us > 20000u ? g_ema_frame_us : 33366u;
                        uint32_t t_real = (prev_hold << 16) / tot_us;
                        t1_q16 = (t_real + 32768u) >> 1;
                        if (t1_q16 < 22937u) t1_q16 = 22937u;
                        if (t1_q16 > 42598u) t1_q16 = 42598u;
                        g_q2x_hold_acc = 0;
                    }

                    n = build_dcb_flow8(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi1], g_buf[mi2], g_pitch,
                                        tidx, px * 4, slot, g_flips, tail, tail_dw, (int32_t)handle, mi1, mi2,
                                        t1_q16, t2_q16, two_mids, force_repeat);
                    flip_idx = mi1;
                    second_flip = 1;
                } else if (g_inject_on == 8 && g_sh8_ok && g_fmt_supported && g_bw >= 640 && g_bh >= 360 &&
                           (g_menu_open || cfg_cur.overlay > 0 || cfg_cur.upscale > 0)) {
                    // Native pass-through when FG, menu, overlay and enhancement are all off.
                    // Only append a post DCB when it has visible work (menu/overlay/enhance).
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t tidx = (g_attr.tilingMode == 0) ? 10u : 8u;
                    n = build_dcb_post8(mine, g_buf[index], g_bw, g_bh, g_pitch, tidx, px * 4, slot, g_flips, tail, tail_dw);
                } else if ((g_inject_on == 4 || g_inject_on == 6 || g_inject_on == 7) && g_prev_idx < (uint32_t)g_ngame && mbufs_ready(handle)) {
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t mi = g_ngame + (g_flips % (uint32_t)g_nmid);
                    if (g_inject_on >= 6 && g_work && g_bw >= 1280 && (g_bw % 640) == 0 && g_bh == g_bw * 9 / 16 && g_bw / 640 <= 4)
                        n = build_dcb_flow(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi], g_bw, g_bh, g_pitch,
                                           (g_attr.tilingMode == 0) ? 10u : 8u, px * 4, slot, g_flips, tail,
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
                    uint32_t tidx = (g_attr.tilingMode == 0) ? 10u : 8u;
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

                g_prev2_idx = g_prev_idx;
                g_prev_idx = index;

                if (n == 0) {
                    // Nothing injected (e.g. Native mode with OSD/RCAS off, or first frame before g_prev_idx is valid)
                    int32_t rc0 = HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                         int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                         count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);

                    return rc0;
                }

                void *d2[GV_MAXE]; uint32_t s2[GV_MAXE]; void *c2[GV_MAXE]; uint32_t cs2[GV_MAXE];
                if (n * 4u > (dcb8 ? DCB8_SZ : 0x800u)) {
                    static int warned = 0;
                    if (!warned) { warned = 1; lg("ERROR: our DCB is %u dwords, memory layout overlap\n", n); }
                }
                for (uint32_t i = 0; i < count; i++) { d2[i] = dcb[i]; s2[i] = dcbSz[i]; c2[i] = ccb ? ccb[i] : 0; cs2[i] = ccbSz ? ccbSz[i] : 0; }
                uint32_t ncount = count + 1;
                if (s2[last] == tail_dw * 4u) {
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
                    s2[last] -= tail_dw * 4u;
                    d2[count] = mine; s2[count] = n * 4; c2[count] = 0; cs2[count] = 0;
                }
                if (g_flips >= INJECT_AFTER_FLIPS && g_flips < INJECT_AFTER_FLIPS + 3) {
                    lg("submit args #%u: count=%u ccb=%p ccbSz=%p handle=%u index=%u flipMode=%u flipArg=%lld\n", g_flips, count,
                       (void *)ccb, (void *)ccbSz, handle, index, flipMode, (long long)flipArg);
                    for (uint32_t i = 0; i < count; i++)
                        lg("  [%u] dcb=%p sz=%u  ccb=%p sz=%u\n", i, dcb[i], dcbSz[i], ccb ? ccb[i] : NULL, ccbSz ? ccbSz[i] : 0u);
                }
                if (g_flips == INJECT_AFTER_FLIPS || (g_flips % 600) == 0)
                    lg("inject #%u: count %u->%u, our DCB %u dw, out buf[%u]=%llx\n", g_flips, count, ncount, n, index, (unsigned long long)g_buf[index]);
                int32_t rc = HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                     int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                     ncount, d2, s2, ccb ? c2 : ccb, ccbSz ? cs2 : ccbSz, handle, flip_idx, flipMode, flipArg);
                if (third_flip) {
                    uint32_t *fb2 = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + SLOT_FB2_OFF);
                    for (uint32_t i = 0; i < tail_dw; i++) fb2[i] = tail[i];
                    void *dm2[1] = { fb2 };
                    uint32_t sm2[1] = { tail_dw * 4u };
                    HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                  int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                  1, dm2, sm2, NULL, NULL, handle, mid2_flip_idx, flipMode, flipArg);
                }
                if (second_flip) {
                    uint32_t *fb = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0xE00);
                    for (uint32_t i = 0; i < tail_dw; i++) fb[i] = tail[i];
                    void *d3[1] = { fb };
                    uint32_t s3[1] = { tail_dw * 4u };
                    int32_t r2 = HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                                     int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                                     1, d3, s3, NULL, NULL, handle, index, flipMode, flipArg);
                    if (g_flips < INJECT_AFTER_FLIPS + 12) lg("mode4 #%u: gnm flip(mid idx %u)=0x%08x, gnm flip(cur idx %u)=0x%08x\n", g_flips, flip_idx, (unsigned)rc, index, (unsigned)r2);
                }

                return rc;
            } else if (g_flips == INJECT_AFTER_FLIPS) {
                snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO FLIP TAIL");
                lg("inject skipped: tail marker not found (nd=%u)\n", nd);
                {   // diagnostic for games whose flip tail differs: dump the last DCB (up to 160 dwords) and the marker search result
                    static int dumped = 0;
                    if (!dumped && dcb[last] && dcb_mem_cached((uintptr_t)dcb[last], (size_t)nd * 4u)) {
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

int32_t sceGnmSubmitAndFlipCommandBuffers_hook(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    uint64_t a6_begin = sceKernelGetProcessTime();
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    uint32_t gn = gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 1);
    if (gn) { count = gn; dcb = gd; dcbSz = gs; if (ccb) ccb = gc; if (ccbSz) ccbSz = gcs; }
    g_in_submit++;
    int32_t rc = flip_inner(count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    g_in_submit--;
    gv_frame_end();
    a6_submit_trace("gnm",handle,index,flipMode,flipArg,rc,a6_begin);
    return rc;
}

int32_t sceGnmSubmitCommandBuffers_hook(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz)
{
    // A21: pass-through counter of game submits between flips (our own fence submit is excluded).
    int own_sub = a21_is_own_submit();
    if (!own_sub) __atomic_add_fetch(&a21_nonflip_submits, 1u, __ATOMIC_RELEASE);
#if A21_SIDE_WALK
    // S3b: read-only walk of the game's buffers (compute-register writes) before they are submitted.
    int a21_walked = !own_sub && __atomic_load_n(&a20_state, __ATOMIC_ACQUIRE) == 2;
    if (a21_walked) a21_side_submit(count, dcb, dcbSz, ccb, ccbSz);
#endif
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    uint32_t gn = gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 0);
    int32_t rc;
    g_in_submit++;
    if (gn)
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers, int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                           gn, gd, gs, ccb ? gc : NULL, ccbSz ? gcs : NULL);
    else
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers, int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                           count, dcb, dcbSz, ccb, ccbSz);
    g_in_submit--;
#if A21_SIDE_WALK
    if (a21_walked) a21_side_submit_done();
#endif
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
    int own_sub = a21_is_own_submit();
    if (!own_sub) __atomic_add_fetch(&a21_nonflip_submits, 1u, __ATOMIC_RELEASE);
#if A21_SIDE_WALK
    if (!own_sub) a21_side_mark_dirty(1);   // not walked: the carried shadow is no longer proven
#endif
    return HOOK_CONTINUE(sceGnmSubmitCommandBuffersForWorkload,
                         int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *),
                         workload, count, dcb, dcbSz, ccb, ccbSz);
}
int32_t sceGnmSubmitAndFlipCommandBuffersForWorkload_hook(uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[],
                                                          uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    wl_count(1, "sceGnmSubmitAndFlipCommandBuffersForWorkload");
    int own_sub = a21_is_own_submit();
    if (!own_sub) __atomic_add_fetch(&a21_nonflip_submits, 1u, __ATOMIC_RELEASE);
#if A21_SIDE_WALK
    if (!own_sub) a21_side_mark_dirty(1);
#endif
    return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffersForWorkload,
                         int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                         workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
}

// 1.4: Observe direct sceVideoOutSubmitFlip calls (games that separate SubmitCommandBuffers and VideoOutSubmitFlip)
int32_t sceVideoOutSubmitFlip_hook(int32_t handle, int32_t index, uint32_t mode, int64_t arg)
{
    uint64_t a6_begin = sceKernelGetProcessTime();
    if (!g_in_submit) {
        uint32_t vf = ++g_vo_submit_flips;
        if (vf == INJECT_AFTER_FLIPS && g_flips == 0) {
            snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: VOUT FLIP API");
            mon_start();
            lg("inert: game flips via sceVideoOutSubmitFlip (%u flips, 0 gnm flips)\n", vf);
        }
    }
    int32_t a6_rc = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t), handle, index, mode, arg);
    a6_submit_trace("videoout",(uint32_t)handle,(uint32_t)index,mode,arg,a6_rc,a6_begin);
    return a6_rc;
}

static int32_t diag_register_buffers_call(int32_t handle, int32_t startIndex, void *const *addresses, int32_t bufferNum, const VoAttr *attr)
{
    int32_t r = HOOK_CONTINUE(sceVideoOutRegisterBuffers,
        int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
        handle, startIndex, addresses, bufferNum, attr);
    lg("VO_REGISTER_RESULT tid=%llx handle=%d start=%d num=%d rc=0x%08x\n",
       diag_thread_id(), handle, startIndex, bufferNum, (unsigned)r);
    return r;
}

int32_t sceVideoOutRegisterBuffers_hook(int32_t handle, int32_t startIndex, void *const *addresses, int32_t bufferNum, const VoAttr *attr)
{
    lg("RegisterBuffers tid=%llx handle=%d start=%d num=%d addresses=%p attr=%p\n",
       diag_thread_id(), handle, startIndex, bufferNum, (const void *)addresses, (const void *)attr);
    if (attr && startIndex == 0) { g_handle = handle; g_attr = *attr; }
    if (attr) {
        g_bw = attr->width; g_bh = attr->height; g_pitch = attr->pitchInPixel;
        // 1.4: Strict check for 8-bit 8_8_8_8 SDR formats (0x80000000 ARGB / 0x80002200 ABGR); rejects 10-bit HDR 0x88xxxxxx
        g_fmt_supported = ((attr->format & 0xFFFF00FFu) == 0x80000000u);
        lg("  attr: fmt=0x%x (supported=%d) tiling=%u aspect=%u %ux%u pitch=%u option=%u\n",
           attr->format, g_fmt_supported, attr->tilingMode, attr->aspectRatio, attr->width, attr->height, attr->pitchInPixel, attr->option);
    }
    for (int i = 0; addresses && i < bufferNum && startIndex + i < 16; i++) {
        g_buf[startIndex + i] = (uint64_t)(uintptr_t)addresses[i];
        if (startIndex + i + 1 > g_nbuf) g_nbuf = startIndex + i + 1;
        lg("  buf[%d]=%p\n", startIndex + i, addresses[i]);
    }
    // 1.4: Support 2..8 game display buffers (some some engines/some engines games register 4 buffers)
    if (attr && startIndex == 0 && bufferNum >= 2 && bufferNum <= 8 && addresses && g_mbuf_state >= 0) {
        g_ngame = bufferNum;
        int m_raw = g_start_mode;
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
            g_mbuf_stride = one;
            if (g_passive_mode || g_prof.late_mbuf_reg || !g_fmt_supported || g_bw != 1920 || g_bh != 1080) {
                g_mbuf_state = 0;
                lg("mbufs: deferred registration (passive=%d late_reg=%d res=%ux%u fmt_ok=%d, stride=0x%zx)\n",
                   g_passive_mode, g_prof.late_mbuf_reg, g_bw, g_bh, g_fmt_supported, one);
                if (a22_runtime_title() && !g_passive_mode) dmem_report_free("REF first RegisterBuffers");
                return diag_register_buffers_call(handle, startIndex, addresses, bufferNum, attr);
            }
            uint8_t *m = NULL;
            int nm = 0;
            int max_extra = 16 - g_ngame;
            if (max_extra > g_prof.max_mid_bufs) max_extra = g_prof.max_mid_bufs;
            void *pre_mem = pre_mem_load();
            if (one && pre_mem) {
                nm = (int)(g_pre_size / one);
                if (nm > max_extra) nm = max_extra;
                if (nm >= 1) m = (uint8_t *)pre_mem;
            }
            for (int want = max_extra; one && !m && want >= 1; want--) {
                m = (uint8_t *)alloc_mbufs((one * (size_t)want + 0x1FFFFFu) & ~(size_t)0x1FFFFFu);
                if (m) nm = want;
            }
            g_nmid = nm > 0 ? nm : 1;
            if (m && nm >= 1) {
                void *ext[16];
                for (int i = 0; i < g_ngame; i++) ext[i] = addresses[i];
                for (int i = 0; i < nm; i++) ext[g_ngame + i] = m + (size_t)i * one;
                int32_t r = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                          handle, startIndex, ext, g_ngame + nm, attr);
                lg("mbufs: %d extra buffer(s) of 0x%zx at %p%s, game RegisterBuffers extended to %d = 0x%08x\n", nm, one, (void *)m,
                   pre_mem_load() ? " (early)" : "", g_ngame + nm, (unsigned)r);
                if (r == 0) {
                    for (int i = 0; i < nm; i++) g_buf[g_ngame + i] = (uint64_t)(uintptr_t)ext[g_ngame + i];
                    g_nbuf = g_ngame + nm;
                    g_mbuf_state = 1;
                    return r;
                }
            }
        }
    }
    return diag_register_buffers_call(handle, startIndex, addresses, bufferNum, attr);
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
static int32_t g_combo_handle = -1;
static volatile int g_in_pad_tid = 0;
static volatile uint32_t g_pad_calls[4];
static volatile uint32_t g_pad_wrap_activity;
static volatile uint32_t g_pad_poll_reads = 0;

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
        // Deterministic: RIGHT = ON, LEFT = OFF. A toggle made any even number of presses (or d-pad auto-repeat) land on OFF.
        c->framegen = dir > 0 ? 1 : 0;
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

static void pad_menu_step_h(int32_t handle, PadDataRaw *pkts, int count)
{
    if (!pkts || count < 1) return;
    PadDataRaw *latest = &pkts[count - 1];
    uint32_t raw_btn = latest->buttons;
    uint64_t now = sceKernelGetProcessTime();
    if (a22_runtime_title()) {
        if (a20_pad_step(raw_btn,latest->timestamp,now)) pad_swallow(pkts,count);
        return;
    }

    if (latest->timestamp && latest->timestamp == g_pad_last_ts && raw_btn == g_pad_last_raw) {
        if (g_menu_open || now < g_pad_swallow_until) pad_swallow(pkts, count);
        return;
    }
    g_pad_last_ts = latest->timestamp;
    g_pad_last_raw = raw_btn;

    if (!g_menu_open) {
        if (now < g_pad_swallow_until) {
            if ((raw_btn & 0xFFFFu) == 0) g_pad_swallow_until = 0;
            else { pad_swallow(pkts, count); return; }
        }
        if ((raw_btn & (PAD_L3 | PAD_R3)) == (PAD_L3 | PAD_R3)) {
            uint64_t hold_us = (uint64_t)(g_cfg.menu_hold_ms >= 500 ? g_cfg.menu_hold_ms : 3000) * 1000ull;
            if (!g_combo_t) { g_combo_t = now; g_combo_handle = handle; }
            else if (!g_combo_fired && now > g_combo_t && now - g_combo_t >= hold_us) {
                g_combo_fired = 1;
                g_menu_edit = g_cfg;
                g_menu_sel = 0;
                snprintf(g_osd_toast, sizeof(g_osd_toast), "MENU OPEN");
                g_menu_open = 1;
                g_pad_prev_nav = raw_btn;
                g_pad_rep_t = now;
                pad_swallow(pkts, count);
                lg("menu opened (L3+R3 held %d ms)\n", g_cfg.menu_hold_ms);
            }
        } else if (handle == g_combo_handle || g_combo_handle < 0) {
            g_combo_t = 0;
            g_combo_fired = 0;
        }
        return;
    }

    pad_swallow(pkts, count);

    if ((raw_btn & (PAD_L3 | PAD_R3)) != (PAD_L3 | PAD_R3)) g_combo_fired = 0;

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

    if ((edge & (PAD_CIRCLE | PAD_OPTIONS)) || (!g_combo_fired && (raw_btn & (PAD_L3 | PAD_R3)) == (PAD_L3 | PAD_R3))) {
        g_menu_open = 0;
        g_combo_fired = 1;
        g_combo_t = now;
        g_pad_swallow_until = now + 300000ull;
        snprintf(g_osd_toast, sizeof(g_osd_toast), "FG:%s AA:%d RCAS:%d%%",
                 g_cfg.framegen ? (g_cfg.preset == 1 ? "F60" : "2X") : "OFF", g_cfg.upscale, g_cfg.sharpness);
        g_osd_toast_until = now + 1500000ull;
        lg("menu closed (pad wrapper calls: state %u read %u stateExt %u readExt %u | poll_reads %u)\n",
           g_pad_calls[0], g_pad_calls[1], g_pad_calls[2], g_pad_calls[3], g_pad_poll_reads);
        return;
    }
    if (edge & PAD_UP)   g_menu_sel = (g_menu_sel + MENU_NROWS - 1) % MENU_NROWS;
    if (edge & PAD_DOWN) g_menu_sel = (g_menu_sel + 1) % MENU_NROWS;
    if (edge & (PAD_LEFT | PAD_RIGHT)) {
        int dir = (edge & PAD_RIGHT) ? 1 : -1;
        menu_adjust_row(&g_menu_edit, g_menu_sel, dir);
        if (g_menu_sel == 0) {
            lg("menu-edit: row=0 framegen=%d (passive=%d, press X to apply)\n", g_menu_edit.framegen, g_passive_mode);
            snprintf(g_osd_toast, sizeof(g_osd_toast), "PRESS [X] TO APPLY FG");
        } else {
            cfg_apply_live(&g_menu_edit, NULL);
            snprintf(g_osd_toast, sizeof(g_osd_toast), "LIVE ([/\\] SAVE)");
        }
    }
    if (edge & PAD_CROSS) {
        cfg_apply_live(&g_menu_edit, "menu-apply");
        snprintf(g_osd_toast, sizeof(g_osd_toast), "APPLIED");
        // In safe mode want_fg is gated by !g_passive_mode, so Apply alone could never start FG.
        // Enabling FG explicitly is the user's override of the crash guard; the FG guard re-arms on activation.
        if (g_passive_mode && g_menu_edit.framegen) {
            __atomic_store_n(&g_leave_passive_req, 1, __ATOMIC_RELEASE);
            snprintf(g_osd_toast, sizeof(g_osd_toast), "APPLIED: LEAVING SAFE MODE");
        }
    }
    if (edge & PAD_SQUARE) {
        cfg_defaults(&g_menu_edit);
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
    int tid = scePthreadGetthreadid();
    int cur = __atomic_load_n(&g_in_pad_tid, __ATOMIC_ACQUIRE);
    if (cur != 0 && cur == tid) return HOOK_CONTINUE(scePadReadState, int32_t (*)(int32_t, void *), handle, data);
    __atomic_store_n(&g_in_pad_tid, tid, __ATOMIC_RELEASE);
    int32_t r = HOOK_CONTINUE(scePadReadState, int32_t (*)(int32_t, void *), handle, data);
    if (r == 0 && data) pad_menu_step_h(handle, (PadDataRaw *)data, 1);
    __atomic_store_n(&g_in_pad_tid, 0, __ATOMIC_RELEASE);
    return r;
}

int32_t scePadRead_hook(int32_t handle, void *data, int32_t num)
{
    int tid = scePthreadGetthreadid();
    int cur = __atomic_load_n(&g_in_pad_tid, __ATOMIC_ACQUIRE);
    if (cur != 0 && cur == tid) return HOOK_CONTINUE(scePadRead, int32_t (*)(int32_t, void *, int32_t), handle, data, num);
    __atomic_store_n(&g_in_pad_tid, tid, __ATOMIC_RELEASE);
    int32_t r = HOOK_CONTINUE(scePadRead, int32_t (*)(int32_t, void *, int32_t), handle, data, num);
    if (r > 0 && data) pad_menu_step_h(handle, (PadDataRaw *)data, r > 64 ? 64 : r);
    __atomic_store_n(&g_in_pad_tid, 0, __ATOMIC_RELEASE);
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
    __atomic_add_fetch(&g_pad_wrap_activity, 1u, __ATOMIC_RELAXED);
    int32_t r = g_orig_pad_state(handle, data);
    if (r == 0 && data) pad_menu_step_h(handle, (PadDataRaw *)data, 1);
    return r;
}
static int32_t got_pad_read_wrap(int32_t handle, void *data, int32_t num)
{
    g_pad_calls[1]++;
    __atomic_add_fetch(&g_pad_wrap_activity, 1u, __ATOMIC_RELAXED);
    int32_t r = g_orig_pad_read(handle, data, num);
    if (r > 0 && data) pad_menu_step_h(handle, (PadDataRaw *)data, r > 64 ? 64 : r);
    return r;
}
static int32_t got_pad_state_ext_wrap(int32_t handle, void *data)
{
    g_pad_calls[2]++;
    __atomic_add_fetch(&g_pad_wrap_activity, 1u, __ATOMIC_RELAXED);
    int32_t r = g_orig_pad_state_ext(handle, data);
    if (r == 0 && data) pad_menu_step_h(handle, (PadDataRaw *)data, 1);
    return r;
}
static int32_t got_pad_read_ext_wrap(int32_t handle, void *data, int32_t num)
{
    g_pad_calls[3]++;
    __atomic_add_fetch(&g_pad_wrap_activity, 1u, __ATOMIC_RELAXED);
    int32_t r = g_orig_pad_read_ext(handle, data, num);
    if (r > 0 && data) pad_menu_step_h(handle, (PadDataRaw *)data, r > 64 ? 64 : r);
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
            // 1.4: Scan any RW segment (including RWX segments used by some engines/some engines JIT/GOT tables), up to 64 MB
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
    uint32_t last_wrap_activity = 0;
    for (;;) {
        sceKernelUsleep(16000);
        if (g_flips < INJECT_AFTER_FLIPS + 60 && g_vo_submit_flips < INJECT_AFTER_FLIPS + 60) continue;
        uint64_t now = sceKernelGetProcessTime();
        // 1.4: Retry GOT patching up to 12 times (every 5 s) in case the game resolves lazy PLT slots after splash screens
        if (!g_pad_got_patched && got_tries < 12 && now >= next_got_try) {
            got_tries++;
            next_got_try = now + 5000000ull;
            int np = pad_got_patch();
            if (got_tries == 1 || np > 0) lg("pad got (try %d): %d slot(s) patched\n", got_tries, np);
            if (np > 0) g_pad_got_patched = 1;
        }
        // Some games never call the patched pad imports. Keep polling as a
        // fallback, and avoid processing the same controller twice when a GOT
        // wrapper was active during this poll interval.
        uint32_t wrap_activity = __atomic_load_n(&g_pad_wrap_activity, __ATOMIC_RELAXED);
        int wrapper_used = g_pad_got_patched && wrap_activity != last_wrap_activity;
        last_wrap_activity = wrap_activity;
        if (wrapper_used) continue;
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
        if (r == 0) { errs = 0; g_pad_poll_reads++; pad_menu_step_h(handle, &st, 1); }
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
    // A6 only observes; do not activate the legacy force-rate path.
    g_game_flip_rate = rate;
    int rc = HOOK_CONTINUE(sceVideoOutSetFlipRate,int32_t (*)(int32_t,int32_t),handle,rate);
    lg("A6_RATE seq=%u t=%llu tid=%llx h=%d requested=%d forwarded=%d rc=0x%08x\n",
       a6_seq(),(unsigned long long)sceKernelGetProcessTime(),diag_thread_id(),handle,rate,rate,(unsigned)rc);
    return rc;
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
    if (a22_runtime_title())
        diag_capture_dmem(&g_diag_early);
    // Start the asynchronous logger before startup checks/configuration can call lg().
    // plugin_load runs on GoldHEN's loader thread; opening probe.log there can SIGBUS.
    io_start();
    crash_guard_check_on_load();
    cfg_reload_from_disk(1);
#if BB_DIAG_ALLOC_A
    if (a22_runtime_title()) {
        fg_config_t diag_cfg;
        fg_cfg_read(&diag_cfg);
        diag_cfg.framegen = 0;
        fg_cfg_publish(&diag_cfg);
        g_passive_mode = 1;
        g_known_game = 0;
        klog("[bb_probe] DIAG_A: REF passive baseline; DirectMemory reserve and FG activation disabled\n");
    }
#endif
    {
        int m = read_mode_raw();
        g_start_mode = m;
        // REF: no DirectMemory in plugin_load in ANY inject mode. Previously only mode 8 deferred, so
        // inject '1'..'7' still allocated GPU/work here - the exact pattern of the Dantelion2 panic crashes.
        g_deferred_ref_gpu = (a22_runtime_title());
        if (g_deferred_ref_gpu) {
            klog("[bb_probe] REF safe start: deferring GPU/work DirectMemory allocations (mode %d)\n", m);
        }
        if (m == 8 && !g_passive_mode && !g_deferred_ref_gpu) gv_load(alloc_dm);
        if (m >= 1 && m <= 8 && !g_passive_mode && !g_deferred_ref_gpu) {
            g_gpu_sz = gv_armed() ? GPU_SZ : GPU_SZ_BASE;
            g_pre_gpu = alloc_dm(g_gpu_sz, g_gpu_sz, 0);
            { int ff = file_flags(); if (ff > 0 && (ff & 16)) { g_dump_mem = (uint8_t *)alloc_dm(DUMP_SZ, 0x10000, 0); klog("[bb_probe] dump memory: %p\n", (void *)g_dump_mem); } }
            if (m == 8) {
                g_pre_work = alloc_dm(WORK_SZ, 0x10000, 3);
                g_work_garlic = g_pre_work != NULL;
            }
            if (!g_pre_work && m >= 6) g_pre_work = alloc_dm(WORK_SZ, 0x10000, 0);
            klog("[bb_probe] early gpu/work memory: %p / %p\n", g_pre_gpu, g_pre_work);
        }
        if ((m == 4 || m >= 6) && !g_passive_mode && !g_deferred_ref_gpu && !g_prof.late_mbuf_reg) {
            const size_t try_sz[2] = { g_prof.max_mid_bufs >= 2 ? 0x1000000u : 0x800000u, 0x800000u };
            for (int ti = 0; ti < 2 && !pre_mem_load(); ti++) {
                void *addr = alloc_dm_ex(try_sz[ti], 0x200000, 3 /*WC_GARLIC*/, g_prof.high_mem_alloc, GARLIC_SAFETY_RESERVE);
                if (addr) { g_pre_size = try_sz[ti]; __atomic_store_n(&g_pre_mem, addr, __ATOMIC_RELEASE); }
            }
            klog("[bb_probe] early display-buffer memory: %s (size=0x%zx)\n", pre_mem_load() ? "ok" : "DEFERRED", g_pre_size);
        }
#if !BB_DIAG_ALLOC_A
        if (m == 8 && a22_runtime_title() && !g_passive_mode &&
            g_prof.late_mbuf_reg && !pre_mem_load()) {
            // Reserve the scarce display surface from bb_io only, with a short grace period
            // for plugin_load to return. Keep GPU/work allocation and VideoOut registration deferred.
            g_ref_mbuf_prereserve_after_us = sceKernelGetProcessTime() + 100000ull;
            __atomic_store_n(&g_ref_mbuf_prereserve_req, 1u, __ATOMIC_RELEASE);
            klog("[bb_probe] REF: queued async MBUF reservation (worker, 100 ms grace; registration still lazy)\n");
        }
#endif
    }
    HOOK32(sceGnmSubmitAndFlipCommandBuffers);
    HOOK32(sceVideoOutRegisterBuffers);
    HOOK32(sceVideoOutSetFlipRate);
    HOOK32(sceVideoOutSubmitFlip);
    {
        int ff = file_flags();
        if (ff > 0 && (ff & 128)) g_diag_hooks = 1;
    }
    if (gv_armed() || g_diag_hooks || (A21_COUNT_SUBMITS && a22_runtime_title())) HOOK32(sceGnmSubmitCommandBuffers);
    if (g_diag_hooks || (A21_COUNT_SUBMITS && a22_runtime_title())) {
        HOOK32(sceGnmSubmitCommandBuffersForWorkload);
        HOOK32(sceGnmSubmitAndFlipCommandBuffersForWorkload);
        klog("[bb_probe] diagnostic hooks on the ForWorkload submit variants installed\n");
    }
#if BB_DIAG_ALLOC_A
    if (a22_runtime_title()) {
        // A15 stalls before runtime activation. Isolate new entry-point pad detours.
        // Use the established polling/GOT controller path for this build.
        klog("[bb_probe] A20_PAD_ENTRY_HOOKS_DISABLED polling_controller=1\n");
        HOOK32(sceKernelAllocateDirectMemory);
        HOOK32(sceKernelMapDirectMemory);
        HOOK32(sceKernelMapNamedDirectMemory);
        klog("[bb_probe] DIAG_A allocator hooks installed (allocate/map/map-named)\n");
        HOOK32(sceVideoOutAddFlipEvent);
        HOOK32(sceVideoOutGetFlipStatus);
        HOOK32(sceKernelGetEventData);
        HOOK32(sceKernelWaitEqueue);
        klog("[bb_probe] DIAG_A18 continuous REF FG; L3+R3 menu; CROSS toggle; native-only menu; active memory valve; no synthetic allocation\n");
        __atomic_store_n(&g_diag_map_ready, 1u, __ATOMIC_RELEASE);
    }
#endif
    return 0;
}

s32 attr_public plugin_unload(s32 argc, const char *argv[])
{
    (void)argc; (void)argv;
    if (!g_is_game_proc) return 0;
    UNHOOK(sceGnmSubmitAndFlipCommandBuffers);
    UNHOOK(sceVideoOutRegisterBuffers);
    UNHOOK(sceVideoOutSetFlipRate);
    UNHOOK(sceVideoOutSubmitFlip);
    if (gv_armed() || g_diag_hooks || (A21_COUNT_SUBMITS && a22_runtime_title())) UNHOOK(sceGnmSubmitCommandBuffers);
    if (g_diag_hooks || (A21_COUNT_SUBMITS && a22_runtime_title())) { UNHOOK(sceGnmSubmitCommandBuffersForWorkload); UNHOOK(sceGnmSubmitAndFlipCommandBuffersForWorkload); }
#if BB_DIAG_ALLOC_A
    if (a22_runtime_title()) {
        UNHOOK(sceKernelAllocateDirectMemory);
        UNHOOK(sceKernelMapDirectMemory);
        UNHOOK(sceKernelMapNamedDirectMemory);
        UNHOOK(sceVideoOutAddFlipEvent);
        UNHOOK(sceVideoOutGetFlipStatus);
        UNHOOK(sceKernelGetEventData);
        UNHOOK(sceKernelWaitEqueue);
    }
#endif
    if (g_log) { fclose(g_log); g_log = NULL; }
    return 0;
}

s32 attr_module_hidden module_start(s64 argc, const void *args) { (void)argc; (void)args; return 0; }
s32 attr_module_hidden module_stop(s64 argc, const void *args) { (void)argc; (void)args; return 0; }
