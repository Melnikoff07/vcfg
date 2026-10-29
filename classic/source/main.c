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
#define MENU_NROWS 11

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
    int post_mid;        // 0 = auto, 1 = generated mid frames only, 2 = generated + real frames,
                         //     3 = AA Light on the real frame too, with the static handling (Sharpness 0 only)
    int manual_mode;     // 0 = auto (algorithm), 1 = manual dev overrides active in config-<TITLE>.ini
    int vout_path;       // 0 = auto, 1 = decoupled sceVideoOutSubmitFlip path learned at runtime
    int gv_conv;         // -1 = auto/uncalibrated, 0..15 = learned motion-vector convention
    int audio_fix;       // CUSA10249 audio DirectMemory variant: 0 = c87af66, 1 = Variant C (default for CUSA10249), 2 = Variant D
    int below30;         // 0 = off (default), 1 = on: below 30 fps a pair predicted to take 3 refreshes shows 2 generated
                         //     frames; it only runs where the 2nd generated-frame buffer already exists (off otherwise)
    int shallow_queue;   // 0 = off (the game is handed back with 3-4 flips pending, as before), 1 = on (default)
} fg_config_t;

static char g_title_id[16] = "UNKNOWN";
static int g_known_game = 0;
static int g_is_game_proc = 1;
static volatile fg_config_t g_cfg = { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0, MENU_COMBO_DEFAULT, 0, 0, 0, 0, 0, 0, 0, 0, -1, 0, 0, 1 };
static fg_config_t g_menu_edit    = { 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0, MENU_COMBO_DEFAULT, 0, 0, 0, 0, 0, 0, 0, 0, -1, 0, 0, 1 };
static volatile uint32_t g_mem_hibernate_count = 0;
static volatile uint32_t g_vout_fg_inited = 0;
static volatile uint32_t g_injects = 0;
static uint32_t g_last_adapt_sig = 0;
volatile int g_gv_cal_winner = -1;
volatile int g_gv_cal_locked = 0;
static volatile int g_last_pace_pre = 1;
static volatile int g_menu_open = 0, g_menu_sel = 0, g_cfg_save_req = 0;
static volatile uint64_t g_osd_toast_until = 0;
static char g_osd_toast[32] = "READY";
static char g_inert_reason[32] = "";
static volatile int g_detected_60fps = 0;
static volatile int g_fmt_supported = 1;
static volatile uint32_t g_last_gpu_ms_x100 = 0, g_last_hz_x10 = 600;
static volatile uint32_t g_vb_win_q8 = 0;   // refresh period measured over the last ~2 s of vblanks, Q24.8 us (0 = none yet)
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
int32_t sceVideoOutUnregisterBuffers(int32_t handle, int32_t attributeIndex);
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

int scePthreadGetthreadid(void);
#define VCFG_TLS_SLOTS 32
static volatile int g_in_submit_tid[VCFG_TLS_SLOTS];
static volatile int g_in_submit_cnt[VCFG_TLS_SLOTS];

static inline int vcfg_in_submit_get(void)
{
    int tid = scePthreadGetthreadid();
    if (tid <= 0) tid = 1;
    for (int i = 0; i < VCFG_TLS_SLOTS; i++) {
        if (__atomic_load_n(&g_in_submit_tid[i], __ATOMIC_ACQUIRE) == tid)
            return __atomic_load_n(&g_in_submit_cnt[i], __ATOMIC_RELAXED);
    }
    return 0;
}

static inline void vcfg_in_submit_inc(void)
{
    int tid = scePthreadGetthreadid();
    if (tid <= 0) tid = 1;
    for (int i = 0; i < VCFG_TLS_SLOTS; i++) {
        if (__atomic_load_n(&g_in_submit_tid[i], __ATOMIC_ACQUIRE) == tid) {
            __atomic_add_fetch(&g_in_submit_cnt[i], 1, __ATOMIC_ACQ_REL);
            return;
        }
    }
    for (int i = 0; i < VCFG_TLS_SLOTS; i++) {
        int exp = 0;
        if (__atomic_compare_exchange_n(&g_in_submit_tid[i], &exp, tid, 0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
            __atomic_store_n(&g_in_submit_cnt[i], 1, __ATOMIC_RELEASE);
            return;
        }
    }
}

static inline void vcfg_in_submit_dec(void)
{
    int tid = scePthreadGetthreadid();
    if (tid <= 0) tid = 1;
    for (int i = 0; i < VCFG_TLS_SLOTS; i++) {
        if (__atomic_load_n(&g_in_submit_tid[i], __ATOMIC_ACQUIRE) == tid) {
            if (__atomic_sub_fetch(&g_in_submit_cnt[i], 1, __ATOMIC_ACQ_REL) <= 0) {
                __atomic_store_n(&g_in_submit_cnt[i], 0, __ATOMIC_RELEASE);
                __atomic_store_n(&g_in_submit_tid[i], 0, __ATOMIC_RELEASE);
            }
            return;
        }
    }
}

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
#include "cpu_cost.h"

// q-cpucost cuts (cpu_cost.h), one opt-in file each, read once by cc_read_cuts when the io thread starts.
static int g_cc_monev = 0, g_cc_iolazy = 0, g_cc_nospin = 0, g_cc_quiet = 0;
static volatile uint32_t g_cc_lines = 0, g_cc_klog_bytes = 0;   // lines through klog and their bytes (CPU plugin line)

static volatile uint32_t g_lgw_lock;   // lg_write may run on the game's thread (sync window) and the io thread at once
static void lg_write(const char *buf)
{
    while (__atomic_exchange_n(&g_lgw_lock, 1u, __ATOMIC_ACQUIRE)) sceKernelUsleep(50);
    if (buf[0] == CC_QUIET_MARK) {   // quietlog: a periodic diagnostic line, probe.log only
        buf++;
    } else {
        klog("[bb_probe] %s", buf);
        g_cc_lines++;
        g_cc_klog_bytes += (uint32_t)strlen(buf) + 11u;
        vcfg_trace(buf);
    }
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

// buf is a 384-byte line buffer (the size of a queue slot), so the whole slot is copied.
static void lg_put(char *buf)
{
    if (g_lg_async && !lg_sync_window()) {
        if (lgq_trylock()) {
            if (g_lgq_w - g_lgq_r < LGQ_N) { memcpy(g_lgq[g_lgq_w % LGQ_N], buf, sizeof(g_lgq[0])); g_lgq_w++; } else g_lgq_drop++;
            lgq_unlock();
        } else g_lgq_drop++;
        return;
    }
    lg_write(buf);
}
void lg(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void lg(const char *fmt, ...)
{
    char buf[384];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    lg_put(buf);
}
// Periodic diagnostic line (LATENCY, CADENCE, PASSES, FRAMES, ...): the same as lg() unless quietlog is on; then it
// goes to probe.log only, and without the debug log it is not even formatted.
static void lgd(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void lgd(const char *fmt, ...)
{
    if (g_cc_quiet && !g_log) return;
    char buf[384];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf + (g_cc_quiet ? 1 : 0), sizeof(buf) - 1u, fmt, ap);
    va_end(ap);
    buf[sizeof(buf) - 1u] = 0;
    if (g_cc_quiet) buf[0] = CC_QUIET_MARK;
    lg_put(buf);
}

// File only (no klog, no trace copy): bulk debug lines such as FTRACE.
static void lg_file_only(const char *buf)
{
    while (__atomic_exchange_n(&g_lgw_lock, 1u, __ATOMIC_ACQUIRE)) sceKernelUsleep(50);
    if (g_log) { fputs(buf, g_log); fflush(g_log); }
    __atomic_store_n(&g_lgw_lock, 0u, __ATOMIC_RELEASE);
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
static uint64_t g_slot_cpu[4], g_ts_slot_seen = 0, g_ts_first = 0, g_ts_cpu_first = 0, g_ts_last = 0, g_ts_cpu_last = 0, g_ts_sum = 0;
static uint32_t g_ts_n = 0;
static uint64_t g_praw[12];
static uint64_t g_ts_pass[4], g_pp[10], g_ts_span = 0, g_ts_span_max = 0;
static uint64_t g_pp_split[5];   // mode 8, per window: warp8 t2[4]..t2[5], rcas8+osd t2[5]..t2[7], pcopy8 t2[7]..t2[6],
                                 // rcas8 of the 1st mid t2[5]..t2[8], the rest (osd, 2nd mid) t2[8]..t2[7]
static uint32_t g_pp_split_n = 0;
static uint64_t g_pp_g2[3];      // 2nd generated frame (two_mids): osd + flip wait t2[8]..t2[9], phase8 + warp8 t2[9]..t2[10],
static uint32_t g_pp_g2_n = 0;   // rcas8 + osd t2[10]..t2[7]; frames that had it
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
#define CUT_BLOCKS 5600u
static uint32_t g_st_n = 0, g_st_cut_max = 0, g_st_cuts = 0, g_st_fgf = 0, g_st_fades = 0;
static uint64_t g_st_cut_sum = 0, g_st_fg_sum = 0;
static int32_t g_st_gmx = 0, g_st_gmy = 0;
static uint64_t g_pyr_last_cur_base = 0;
static uint32_t g_pyr_last_w = 0, g_pyr_last_h = 0, g_pyr_parity = 0;
static uint32_t g_pyr_warm_hits = 0, g_pyr_cold_misses = 0;
static inline void pyr_cache_invalidate(void)
{
    g_pyr_last_cur_base = 0;
    g_pyr_last_w = 0;
    g_pyr_last_h = 0;
}
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
static int g_mbase = 16;
#define IS_MID(b) (g_mbuf_state == 1 && (b) >= g_mbase && (b) < g_nbuf && !(g_pcopy_on && g_pcopy_k >= 2 && (b) >= g_mbase + g_pcopy_k))
static int g_ngame = 3;
// Highest display index the game itself registered + 1. Most games register one group at index 0; some register the
// same buffers again as further groups (0-1, 2-3, 4-5: seen on a 2-buffer title that then flips any of them).
static int g_game_idx_hi = 0;
static void *g_pre_mem = NULL;
static size_t g_pre_size = 0, g_pre_work_sz = 0;
static long g_pre_gpu_phys = 0, g_pre_work_phys = 0, g_pre_mem_phys = 0, g_dump_phys = 0;

#include "two_buffer_test.h"
#include "pace_policy.h"

// FTRACE (pace_policy.h): one 3 x 32-bit record per real frame, only while the debug log is on. The hook writes the
// hand-over / submit / own time, the monitor adds the time on screen and the vblanks held, the io thread prints them.
static uint32_t g_ft[FT_N][3];
static volatile uint32_t g_ft_w;          // records started by the hook
static volatile uint32_t g_ft_slot[16];   // per display buffer: record index + 1 of the real frame flipped to it
static uint32_t g_ft_r, g_ft_prev_h;      // io thread: next record to print, hand-over time of the last one printed
#include "bench_state.h"
#include "boot_stage.h"

static int g_safe = 0;            // 1: the title is not verified, observe first
static int g_safe_active = 0;     // 1: the late set-up succeeded, FG may run
static int g_safe_early = 0;      // 1: this start takes its memory early (state 1)
static int g_safe_never = 0;      // 1: state 2, never touch the game
static long g_low_sub2mb_end = 0x200000L; // end of initial partially-used sub-2MB region at boot

#define FLEX_GPU_MAX 8
#define g_work_phys  g_pre_work_phys
#define g_extra_phys g_pre_mem_phys
static uint8_t *g_work = NULL;
typedef struct {
    void *aligned_va;
    void *raw_va;
    size_t raw_sz;
} FlexGpuSlot;
static FlexGpuSlot g_flex_gpu_slots[FLEX_GPU_MAX];

static volatile int g_own_alloc;
#ifndef VCFG_PBUF_STATE_DEFINED
#define VCFG_PBUF_STATE_DEFINED
static void *g_pbuf = NULL;
static long g_pbuf_phys = 0;
static size_t g_pbuf_sz = 0;
static int g_pbuf_ycocg = 0;
static int g_pbuf_valid = 0;
static uint32_t g_pbuf_last_seq = 0;
static uint64_t g_pbuf_prev_va = 0;
static uint32_t g_flip_ring_hist[8] __attribute__((unused));
static uint32_t g_flip_ring_cnt __attribute__((unused)) = 0;
#endif
// shave14, on with below30 for the CUSA18723 family, opt-in by file for any other title (LOG_DIR/shave14_<TITLE>),
// decided once in plugin_load by s14_decide. A 3-buffer game whose
// boot valve shrinks the display reserve to 8 MiB (one generated-frame buffer) keeps S14_KEEP instead: two buffers, both
// DirectMemory, which VideoOut requires (it refuses FlexibleMemory, 0x80290013). The game allocation that is then short
// takes those 8 MiB as a longer FlexibleMemory tail (Arena Shaving, the mechanism that already backs 6 MiB today), and the
// history buffer g_pbuf (8 MiB of FlexibleMemory, idle in a 3-buffer ring) is not allocated, so the game ends its boot
// with the FlexibleMemory it has today. g_s14_state: 0 unused, 1 reserve kept at S14_KEEP, 2 two buffers registered,
// -1 back to today's single buffer (logged). g_s14_tail_extra > 0: the arena tail took that much more FlexibleMemory than
// it would with an 8 MiB reserve, so g_pbuf stays unallocated for the whole boot (alloc_pbuf_history).
#define S14_KEEP       0x1000000u   // 2 x (1920 x 1088 x 4 = 0x7F8000 rounded to 64 KiB) = 2 x 0x800000, on the valve's 2 MiB grid
#define S14_EXTRA      (S14_KEEP - 0x800000u)
#define S14_FLEX_FLOOR 0x1000000u   // shave_game_alloc: FlexibleMemory left after the tail, see the budget there
static int g_s14_on __attribute__((unused)) = 0;
static int g_s14_pbuf_logged __attribute__((unused)) = 0;
static volatile int g_s14_state __attribute__((unused)) = 0;
static size_t g_s14_tail_extra __attribute__((unused)) = 0, g_s14_flex_before __attribute__((unused)) = 0;
// Every path that shrinks or drops the display reserve reports here: below S14_KEEP shave14 is back to one buffer.
static void s14_reserve_now(size_t sz, const char *why)
{
    if (g_s14_state != 1 || sz >= S14_KEEP) return;
    g_s14_state = -1;
    STAGE("shave14: display reserve now 0x%zx (%s) -> one generated-frame buffer, as without shave14", sz, why);
}

static void free_dm_block(void **va, long *phys, size_t *sz, const char *tag)
{
    if (va != &g_pbuf && g_pbuf && (va == &g_pre_mem || va == &g_pre_work)) {
        free_dm_block(&g_pbuf, &g_pbuf_phys, &g_pbuf_sz, "pbuf");
        g_pbuf_ycocg = 0;
        g_pbuf_valid = 0;
        g_pbuf_last_seq = 0;
        g_pbuf_prev_va = 0;
    }
    if (!*va || !*sz) return;
    void *v = *va;
    long p = *phys;
    size_t s = *sz;
    *va = NULL;
    *phys = 0;
    *sz = 0;
    if (p < 0) {
        void *unmap_va = v;
        size_t unmap_sz = (s + 0x3FFFu) & ~(size_t)0x3FFFu;
        for (int i = 0; i < FLEX_GPU_MAX; i++) {
            if (g_flex_gpu_slots[i].aligned_va == v) {
                unmap_va = g_flex_gpu_slots[i].raw_va;
                unmap_sz = g_flex_gpu_slots[i].raw_sz;
                g_flex_gpu_slots[i].aligned_va = NULL;
                g_flex_gpu_slots[i].raw_va = NULL;
                g_flex_gpu_slots[i].raw_sz = 0;
                break;
            }
        }
        g_own_alloc++;
        int um = sceKernelMunmap(unmap_va, unmap_sz);
        g_own_alloc--;
        STAGE("memory valve: released flex %s va=%p (raw=%p len=0x%zx) unmap=0x%08x",
              tag, v, unmap_va, unmap_sz, (unsigned)um);
        return;
    }
    g_own_alloc++;
    int um = sceKernelMunmap(v, s);
    int rr = um == 0 ? sceKernelReleaseDirectMemory(p, s) : -1;
    g_own_alloc--;
    STAGE("memory valve: released %s va=%p phys=0x%lx len=0x%zx unmap=0x%08x release=0x%08x",
          tag, v, p, s, (unsigned)um, (unsigned)rr);
}
static void release_pbuf(void)
{
    free_dm_block(&g_pbuf, &g_pbuf_phys, &g_pbuf_sz, "pbuf");
    g_pbuf_ycocg = 0;
    g_pbuf_valid = 0;
    g_pbuf_last_seq = 0;
    g_pbuf_prev_va = 0;
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
    s14_reserve_now(0, "released");
    if (g_mbuf_state == 1) g_mbuf_state = -1;
    pyr_cache_invalidate();
}
static void release_early_work(void)
{
    free_dm_block(&g_pre_work, &g_pre_work_phys, &g_pre_work_sz, "work");
    g_work = NULL;
    g_work_garlic = 0;
    pyr_cache_invalidate();
}
static void release_early_gpu(void)
{
    size_t sz = g_pre_gpu ? (size_t)g_gpu_sz : 0;
    free_dm_block(&g_pre_gpu, &g_pre_gpu_phys, &sz, "gpu");
}

static volatile int g_adapt_shave2_failed __attribute__((unused)) = 0;
static volatile int g_adapt_early_crashes __attribute__((unused)) = 0;
static int g_pbuf_skip_logged __attribute__((unused)) = 0;

#include "debug_hooks.h"

// Largest free direct-memory range for an alignment, for the logs: a refused allocation is a refused request (length,
// alignment, type), not proof that no memory is left. OpenOrbis declares this call with by-value outputs, hence the cast.
static size_t dm_avail(size_t align)
{
    return dm_avail_range(0, (long)sceKernelGetDirectMemorySize(), align, NULL);
}

// Allocate GPU-accessible memory from FlexibleMemory (prot 0x33 = PROT_CPU_RW | PROT_GPU_RW) so g_gpu, g_work, and
// overlay bitmaps consume 0 bytes of DirectMemory when DirectMemory is exhausted or tight.
// Verifies 256-byte shader alignment (SH_PGM_LO/HI_CS), 32-byte descriptor table alignment, 8-byte EOP label alignment,
// and 48-bit GPU virtual address range for V# descriptors.
static void *alloc_flex_gpu(size_t sz, size_t align)
{
    if (!sz) return NULL;
    if (align < 0x4000u) align = 0x4000u;
    if (align & (align - 1u)) {
        size_t p2 = 0x4000u;
        while (p2 < align) p2 <<= 1;
        align = p2;
    }
    size_t sz_al = (sz + 0x3FFFu) & ~(size_t)0x3FFFu;
    void *raw = NULL;
    size_t map_sz = sz_al;
    g_own_alloc++;
    int32_t r = sceKernelMapFlexibleMemory(&raw, map_sz, 0x33 /*PROT_CPU_RW | PROT_GPU_RW*/, 0);
    if (r == 0 && raw && (((uintptr_t)raw & (align - 1u)) != 0)) {
        sceKernelMunmap(raw, map_sz);
        raw = NULL;
        map_sz = sz_al + align - 0x4000u;
        r = sceKernelMapFlexibleMemory(&raw, map_sz, 0x33 /*PROT_CPU_RW | PROT_GPU_RW*/, 0);
    }
    g_own_alloc--;
    if (r != 0 || !raw) {
        STAGE("alloc_flex_gpu FAILED: sz=0x%zx align=0x%zx r=0x%08x", sz, align, (unsigned)r);
        return NULL;
    }
    uintptr_t raw_u = (uintptr_t)raw;
    uintptr_t aligned_u = (raw_u + align - 1u) & ~(uintptr_t)(align - 1u);
    // Verify 256-byte shader code alignment, 8-byte EOP alignment, and 48-bit V# GPU VA range:
    if ((aligned_u & 0xFFu) != 0 || (aligned_u >> 48) != 0 || ((aligned_u + sz_al - 1u) >> 48) != 0) {
        sceKernelMunmap(raw, map_sz);
        STAGE("alloc_flex_gpu FAILED alignment/VA check: raw=%p aligned=0x%lx sz=0x%zx", raw, (unsigned long)aligned_u, sz);
        return NULL;
    }
    void *aligned = (void *)aligned_u;
    for (int i = 0; i < FLEX_GPU_MAX; i++) {
        if (!g_flex_gpu_slots[i].aligned_va) {
            g_flex_gpu_slots[i].aligned_va = aligned;
            g_flex_gpu_slots[i].raw_va = raw;
            g_flex_gpu_slots[i].raw_sz = map_sz;
            break;
        }
    }
    record_flex_vmap(aligned, sz_al, -1L, 0x33, 0);
    memset(aligned, 0, sz);
    STAGE("alloc_flex_gpu ok: sz=0x%zx align=0x%zx va=%p (raw=%p map_sz=0x%zx, 0 DirectMemory bytes)",
          sz, align, aligned, raw, map_sz);
    return aligned;
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
        size_t dm_sz = sceKernelGetDirectMemorySize();
        if (s == 0 && (size_t)e >= dm_sz) {
            void *flex = alloc_flex_gpu(len, align);
            if (flex) {
                if (phys_out) *phys_out = -1L;
                return flex;
            }
        }
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

static void *alloc_gv_dm(size_t len, size_t align, int type)
{
    size_t dm_sz = sceKernelGetDirectMemorySize();
    if (dm_sz > 0x100000000ULL && dm_avail(0x200000) >= len + 0x10000000u) {
        long top_e = (long)(dm_sz & ~(size_t)0x1FFFFFu);
        long hi_s = top_e > (long)(len + 0x2000000u) ? (top_e - (long)(len + 0x2000000u)) : 0L;
        void *top_dm = (hi_s > 0) ? alloc_dm_range(hi_s, top_e, len, align, type, NULL) : NULL;
        if (top_dm) return top_dm;
        void *dm = alloc_dm(len, align, type);
        if (dm) return dm;
    }
    size_t flex_av = 0;
    if (((int32_t (*)(size_t *))(void *)sceKernelAvailableFlexibleMemorySize)(&flex_av) == 0 && flex_av >= len + 0x1000000u) {
        void *flex = alloc_flex_gpu(len, align);
        if (flex) return flex;
    }
    return alloc_dm(len, align, type);
}

// Allocate an offscreen history buffer (g_pbuf) for 2-buffer titles when VideoOut present-copy buffers (m2)
// could not be allocated from DirectMemory (or to protect T#prev from being overwritten by Frame N+1).
// Priority:
//   1. If DirectMemory is not tight and has >= full_sz + 64 MB free, allocate full_sz from DirectMemory.
//   2. Otherwise, query FlexibleMemory: if >= 24 MB is free, allocate full_sz (8 MB at 1080p) via alloc_flex_gpu
//      (0 DirectMemory bytes).
//   3. If FlexibleMemory is tight (< 24 MB free), fall back to a 50%-footprint 16-bit surface (half_sz = 4.25 MB
//      at 1080p, g_pbuf_ycocg = 1) via alloc_flex_gpu (0 DirectMemory bytes).
static void *alloc_pbuf_history(size_t surf_sz) __attribute__((unused));
static void *alloc_pbuf_history(size_t surf_sz)
{
    if (cusa10249_audio_fix()) return NULL;
    if (g_pbuf && g_pbuf_sz > 0) return g_pbuf;
    if (!surf_sz) surf_sz = 0x800000u;
    size_t full_sz = (surf_sz + 0xFFFFu) & ~(size_t)0xFFFFu;
    if (g_s14_tail_extra) {   // shave14: its FlexibleMemory backs the longer arena tail (see S14_KEEP)
        if (!g_s14_pbuf_logged) {
            size_t fa = 0;
            ((int32_t (*)(size_t *))(void *)sceKernelAvailableFlexibleMemorySize)(&fa);
            g_s14_pbuf_logged = 1;
            STAGE("shave14: history buffer not allocated (0x%zx of FlexibleMemory stays with the game; the arena tail took "
                  "0x%zx more), flex_av=0x%zx", full_sz, g_s14_tail_extra, fa);
        }
        return NULL;
    }
    size_t half_sz = ((surf_sz >> 1) + 0x3FFFFu) & ~(size_t)0xFFFFu;
    if (half_sz < 0x440000u && full_sz >= 0x7E9000u) half_sz = 0x440000u;
    size_t flex_av = 0;
    int fq = ((int32_t (*)(size_t *))(void *)sceKernelAvailableFlexibleMemorySize)(&flex_av);
    size_t min_flex_reserve = (full_sz >= 0x880000u || g_cfg.shave_mode == 2) ? 0x800000u : 0x3C0000u;
    if (fq == 0 && flex_av < half_sz + min_flex_reserve) {
        if (!g_pbuf_skip_logged) {
            g_pbuf_skip_logged = 1;
            STAGE("alloc_pbuf_history skipped: flex_av=0x%zx < half_sz(0x%zx)+reserve(0x%zx) -> preserving FlexibleMemory for game",
                  flex_av, half_sz, min_flex_reserve);
        }
        return NULL;
    }
    g_pbuf_skip_logged = 0;
    int prefer_half = (fq == 0 && flex_av > 0 && (flex_av < 0x1800000u || flex_av < full_sz + 0x1000000u));
    if (!prefer_half) {
        void *fl = alloc_flex_gpu(full_sz, 0x10000u);
        if (fl) {
            g_pbuf = fl;
            g_pbuf_phys = -1L;
            g_pbuf_sz = full_sz;
            g_pbuf_ycocg = 0;
            g_pbuf_valid = 0;
            STAGE("alloc_pbuf_history: full FlexibleMemory buffer va=%p sz=0x%zx (flex_av=0x%zx, 0 DirectMemory bytes)",
                  fl, full_sz, flex_av);
            return fl;
        }
    }
    void *fl_half = alloc_flex_gpu(half_sz, 0x10000u);
    if (fl_half) {
        g_pbuf = fl_half;
        g_pbuf_phys = -1L;
        g_pbuf_sz = half_sz;
        g_pbuf_ycocg = 1;
        g_pbuf_valid = 0;
        STAGE("alloc_pbuf_history: 50%% compressed 5_6_5 16b FlexibleMemory buffer va=%p sz=0x%zx (flex_av=0x%zx, 0 DirectMemory bytes)",
              fl_half, half_sz, flex_av);
        return fl_half;
    }
    return NULL;
}

static void *alloc_work_early(size_t wsz, int type)
{
    size_t dm_sz = sceKernelGetDirectMemorySize();
    // 1. If g_pre_gpu sits in a partially used 2MB page whose remainder >= wsz (e.g. CUSA00918, CUSA11456), place work
    //    in the same 2MB page so it costs zero 2MB-aligned pages. Prefer the lower part [page_base, g_pre_gpu_phys)
    //    when g_pre_gpu sits at the top of the 2MB page so hibernating work merges directly with the free pool below.
    if (g_pre_gpu && g_pre_gpu_phys >= 0) {
        long page_base = g_pre_gpu_phys & ~0x1FFFFFL;
        if (g_pre_gpu_phys - page_base >= (long)wsz) {
            void *w = alloc_dm_range(page_base, g_pre_gpu_phys, wsz, 0x10000, type, &g_pre_work_phys);
            if (w) { g_pre_work_sz = wsz; g_work = (uint8_t *)w; return w; }
        }
        long end_gpu = g_pre_gpu_phys + (long)g_gpu_sz;
        long next_2mb = (end_gpu + 0x1FFFFFL) & ~0x1FFFFFL;
        if (next_2mb - end_gpu >= (long)wsz) {
            void *w = alloc_dm_range(end_gpu, next_2mb, wsz, 0x10000, type, &g_pre_work_phys);
            if (w) { g_pre_work_sz = wsz; g_work = (uint8_t *)w; return w; }
        }
    }
    // 2. Otherwise, if g_pre_mem was placed at the top of the pool (e.g. CUSA17419), place work right below g_pre_mem
    //    at the top so the game's contiguous arena from 0 upward is not fragmented at its start.
    if (g_pre_mem && g_pre_mem_phys >= 0x200000L) {
        void *w = alloc_dm_range(g_pre_mem_phys - 0x200000L, g_pre_mem_phys, wsz, 0x10000, type, &g_pre_work_phys);
        if (w) { g_pre_work_sz = wsz; g_work = (uint8_t *)w; return w; }
    }
    // 3. Fallback: anywhere in the pool (or FlexibleMemory via alloc_flex_gpu if DirectMemory is exhausted).
    void *w = alloc_dm_range(0, (long)dm_sz, wsz, 0x10000, type, &g_pre_work_phys);
    if (w) { g_pre_work_sz = wsz; g_work = (uint8_t *)w; return w; }
    w = alloc_flex_gpu(wsz, 0x10000);
    if (w) { g_pre_work_phys = -1L; g_pre_work_sz = wsz; g_work = (uint8_t *)w; }
    return w;
}

static int try_recover_hibernated_work_at(uint32_t cur_flips)
{
    if (!__atomic_load_n(&g_mem_hibernating, __ATOMIC_ACQUIRE)) return 0;
    uint32_t hib_flip = __atomic_load_n(&g_hibernated_at_flip, __ATOMIC_ACQUIRE);
    if (cur_flips < hib_flip + 180u) return 0;
    if (g_mbuf_state == 1 && g_pre_mem == NULL) {
        g_mbuf_state = -1;
    }
    if (g_mbuf_state < 0) return 0;
    if (g_pre_work) {
        g_work = (uint8_t *)g_pre_work;
        pyr_cache_invalidate();
        __atomic_store_n(&g_mem_hibernating, 0, __ATOMIC_RELEASE);
        return 1;
    }
    size_t dm2m = dm_avail(0x200000u);
    uint32_t rel_now = __atomic_load_n(&g_dbg_releases, __ATOMIC_ACQUIRE);
    if (dm2m < 0x400000u && rel_now <= g_hibernated_releases_base && cur_flips < hib_flip + 360u) {
        return 0;
    }
    size_t wsz = g_hibernated_work_sz ? g_hibernated_work_sz : WORK_SZ_8;
    void *w = NULL;
    if (dm2m >= 0x400000u || dm_avail(0x10000u) >= wsz) {
        w = alloc_work_early(wsz, 3 /*WC_GARLIC*/);
        if (!w) w = alloc_work_early(wsz, 0 /*WB_ONION*/);
    } else {
        w = alloc_flex_gpu(wsz, 0x10000u);
        if (w) {
            g_pre_work_phys = -1L;
            g_pre_work_sz = wsz;
        }
    }
    if (!w) return 0;
    g_pre_work = w;
    g_work = (uint8_t *)w;
    g_work_garlic = (g_pre_work_phys >= 0);
    pyr_cache_invalidate();
    __atomic_store_n(&g_mem_hibernating, 0, __ATOMIC_RELEASE);
    STAGE("hibernation recovery at flip %u (hibernated at %u): re-allocated g_work=%p phys=0x%lx sz=0x%zx (dm_free_2m=0x%zx)",
          cur_flips, hib_flip, w, g_pre_work_phys, wsz, dm2m);
    return 1;
}

static int try_recover_hibernated_work(void)
{
    uint32_t total_flips = g_flips + g_vo_submit_flips;
    return try_recover_hibernated_work_at(total_flips);
}

static int on_sub_hibernation_gate(uint32_t cur_flips)
{
    if (!__atomic_load_n(&g_mem_hibernating, __ATOMIC_ACQUIRE)) return 0;
    if (try_recover_hibernated_work_at(cur_flips)) return 0;
    pyr_cache_invalidate();
    return 1;
}

// Fast-path FG submit entry helper used by the rendering hook and unit-test harness:
// returns 0 (pass-through native flip) while g_mem_hibernating is active, or 1 when FG is active.
static int on_sub(void)
{
    uint32_t cf = ++g_flips;
    if (on_sub_hibernation_gate(cf)) return 0;
    return (g_work != NULL) ? 1 : 0;
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
    void *addr = g_pre_gpu;
    if (!addr) {
        size_t dm_sz = sceKernelGetDirectMemorySize();
        long top_e = (long)(dm_sz & ~(size_t)0x1FFFFFu);
        long hi_s = (g_pre_mem && g_pre_mem_phys >= 0x200000L) ? (g_pre_mem_phys - 0x200000L) : (top_e > 0x2000000L ? top_e - 0x2000000L : 0L);
        long hi_e = (g_pre_mem && g_pre_mem_phys >= 0x200000L) ? g_pre_mem_phys : top_e;
        if (hi_e > hi_s) addr = alloc_dm_range(hi_s, hi_e, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
        if (!addr && cusa10249_audio_fix() && dm_avail(0x200000u) == 0 && dm_avail(g_gpu_sz) >= g_gpu_sz) {
            addr = alloc_dm_range(0, (long)dm_sz, g_gpu_sz, g_gpu_sz, 0 /*WB_ONION*/, &g_pre_gpu_phys);
            if (addr) g_pre_gpu = addr;
        }
        if (!addr && dm_avail(0x200000u) == 0 && dm_avail(g_gpu_sz) >= g_gpu_sz)
            addr = alloc_dm(g_gpu_sz, g_gpu_sz, 0 /*WB_ONION*/);
        if (!addr) addr = alloc_flex_gpu(g_gpu_sz, g_gpu_sz);
        if (!addr) addr = alloc_dm(g_gpu_sz, g_gpu_sz, 0 /*WB_ONION*/);
        if (addr && cusa10249_audio_fix() && !g_pre_gpu) g_pre_gpu = addr;
    }
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
    if (g_pre_work) {
        g_work = (uint8_t *)g_pre_work;
    } else {
        size_t wsz = (g_inject_on == 8) ? WORK_SZ_8 : WORK_SZ;
        g_pre_work = alloc_work_early(wsz, 3);
        g_work_garlic = (g_pre_work != NULL && g_pre_work_phys >= 0);
        if (!g_pre_work) g_pre_work = alloc_work_early(wsz, 0);
        g_work = (uint8_t *)g_pre_work;
    }
    if (!g_work) lg("gpu_init: work memory unavailable, mode 6 disabled\n");
    g_mon = (volatile uint32_t *)((uint8_t *)addr + MON_OFF);
    g_gpu = (uint8_t *)addr;
    __atomic_store_n(&g_done, (volatile uint32_t *)((uint8_t *)addr + SLOT0 + 0x900), __ATOMIC_RELEASE);
    g_gpu_state = 1;
    pyr_cache_invalidate();
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
    for (int i = 0; i < 11; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;
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
    const size_t min_full_vsz = (VOV_BUF_SZ + 0xFFFFu) & ~(size_t)0xFFFFu; // 0x60000 (384 KB): full 720 px height (fits all 11 rows)
    const size_t dbl_full_vsz = (2u * VOV_BUF_SZ + 0xFFFFu) & ~(size_t)0xFFFFu;
    size_t vsz = ((g_mem_tight ? dbl_full_vsz : VOV_MEM_SZ) + 0xFFFFu) & ~(size_t)0xFFFFu;
    void *mem = alloc_flex_gpu(vsz, 0x10000);
    if (!mem && !g_mem_tight) { vsz = dbl_full_vsz; mem = alloc_flex_gpu(vsz, 0x10000); }
    if (!mem) { vsz = min_full_vsz; mem = alloc_flex_gpu(vsz, 0x10000); }
    if (!mem && !g_mem_tight) { vsz = (VOV_MEM_SZ + 0xFFFFu) & ~(size_t)0xFFFFu; mem = alloc_dm(vsz, 0x10000, 0 /*WB_ONION*/); }
    if (!mem && !g_mem_tight) { vsz = (4u * VOV_BUF_SZ + 0xFFFFu) & ~(size_t)0xFFFFu; mem = alloc_dm(vsz, 0x10000, 0); }
    if (!mem && !g_mem_tight) { vsz = dbl_full_vsz; mem = alloc_dm(vsz, 0x10000, 0); }
    if (!mem && (!g_mem_tight || dm_avail(0x10000) >= 0x100000u)) { vsz = min_full_vsz; mem = alloc_dm(vsz, 0x10000, 0); }
    if (!mem) { vsz = 0x30000u; mem = alloc_flex_gpu(vsz, 0x10000); }
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
        vov_row(&m, "Below 30 FPS", VOV_TOGGLE, c.below30, 0, "");
        vov_row(&m, "Low latency", VOV_TOGGLE, c.shallow_queue, 0, "");
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

// Option post_mid = 3 (first measured on CUSA18723): with Enhance AA on and Sharpness 0, the real frame gets the same
// AA as the generated one (rcas8 flags 3 in one dispatch, plus the static handling of bit 5 below), so edges are
// smoothed the same way in both frames. Not the default: the real frame is the next pair's P, so a moving edge is
// smoothed in P and again in the generated frame (corpus, Sharpness 0: moving-edge PSNR -0.32 dB training, -0.74 dB
// validation against v2.35). Sharpness > 0 keeps the generated-frame-only post-process (sharpening the real frame in
// place was measured worse, see post_mid_only). P must be the game's own 8-bit buffer (the 3-buffer ring without the
// history copy, or a full-size copy): with the 16-bpp history copy the write-back test of bit 5 cannot match, so it
// stays mid-only. Game vectors (warp8g) keep the game's alpha, so the static bits 4 and 5 below are never set and the
// real frame would get the AA on its static pixels too (offline: HUD -1.9 dB, still areas -3.2 dB): mid-only there.
static int real_aa_on(void)
{
    return g_cfg.post_mid == 3 && g_cfg.upscale > 0 && g_cfg.sharpness == 0 && !g_t_prev_half16 && !gv_warp_active();
}

// rcas8 flag bit 4: warp8 writes the mid's alpha as a static-pixel flag (1.0 where P == C in RGB, 254/255 elsewhere)
// and rcas8 leaves those pixels raw on the mid pass (no AA and, with Sharpness > 0, no sharpening either; no store,
// and a wave whose 64 pixels are all static skips the pass after its tile load): the real frames show those pixels
// raw, so a post-process only in the generated frame alternates at 30 Hz (HUD, still scenery). warp8g keeps the game's alpha, so the bit is set only for warp8, and
// only when the real frame is not post-processed in the same dispatch (bit 1, post_mid = 2): then both frames get the
// AA and a static pixel must get it in the mid too.
//
// Bit 5 (with bits 0, 1 and 4, real_aa_on() only): the real frame gets the AA too, in place, and it is the next
// pair's P. AA on the static pixels of P made warp8 stop seeing them as static (offline: HUD -1.9 dB, still areas
// -3.2 dB, HUD flicker +18 % against the mid-only AA), so the cur pass skips the AA where the mid pass found the static
// flag, and writes the pixel back raw where its AA result equals P's stored code (static, but P holds last frame's AA
// of it; without that, every edge that once moved stays AA'd in P and never compares static again).
static uint32_t rcas_static_flag(uint32_t rcas_flags)
{
    if (gv_warp_active()) return rcas_flags;
    if ((rcas_flags & 3u) == 1u) return rcas_flags | 16u;
    if ((rcas_flags & 3u) == 3u && real_aa_on()) return rcas_flags | 48u;
    return rcas_flags;
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
    pyr_cache_invalidate();

    calc_rcas_params(2u /*cur only*/, &rcas_con_bits, &rcas_flags);
    if (g_sh8_off[15]) osd_build_slot(slotb + OSD_SLOT_OFF, &osd_xy, &osd_wh, 2u /*cur only*/);
    if (!rcas_flags && !osd_wh) return 0; // nothing to draw -> zero-touch native pass-through!

    fill_table8_wh(tb, C, C, C, w, h, pitch, tidx, seq & 1u, 0, 0, g_work, g_mon, NULL);
    fill_table8_ext(tb, g_gpu, slotb, rcas_con_bits, rcas_flags, osd_xy, osd_wh);
    *(volatile uint64_t *)(slotb + 0x900) = 0;
    *(volatile uint64_t *)(slotb + 0x908) = 0;
    // GPU time with FG off: this slot's timestamps are written here too (the reader took stale FG-on values of all four
    // slots and divided them by the CPU time: "GPU time ... 400 ms"); pyramid / motion / fence are 0, warp/post = the
    // post pass. No frame statistics (no motion search): marked absent for the FRAMES line.
    for (int i = 0; i < 5; i++) *(volatile uint64_t *)(slotb + 0x910 + i * 8) = 0;
    for (int i = 0; i < 11; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;
    for (int i = 0; i < 8; i++) ((volatile uint32_t *)(slotb + STATS_OFF))[i] = 0xFFFFFFFFu;

    uint32_t *p = p0;
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x0000002E;
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
    *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)lbl2; *p++ = 0x40000000u | ((uint32_t)(lbl2 >> 32) & 0xFFFFu); *p++ = seq; *p++ = 0;
    *p++ = PKT3(0x3C, 6, 0); *p++ = 0x13; *p++ = (uint32_t)lbl2; *p++ = (uint32_t)(lbl2 >> 32); *p++ = seq; *p++ = 0xFFFFFFFFu; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x82003FC0; *p++ = (nbytes >> 8); *p++ = 0; *p++ = (uint32_t)(C >> 8); *p++ = (uint32_t)(C >> 40); *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x80C40000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;

    TS_EOP(0); TS_EOP(1); TS_EOP(2); TS_EOP(3);
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
    TS_EOP(4);

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
        lg("scene-change threshold: %u blocks (%s)\n", g_cut_blocks8 ? g_cut_blocks8 * 8u : CUT_BLOCKS, g_cut_blocks8 ? p : "default");
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
    if (real_aa_on()) return 0;
    if (g_mid_only < 0) {
        g_mid_only = 1;
        lg("post-process target: generated frames only (default, keeps real C/P unmodified)\n");
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
    int pyr_warm = (P != 0 && P != C && P == g_pyr_last_cur_base && g_bw == g_pyr_last_w && g_bh == g_pyr_last_h);
    uint32_t r = pyr_warm ? (g_pyr_parity ^ 1u) : 0u;
    g_pyr_parity = r;
    g_pyr_last_cur_base = C;
    g_pyr_last_w = g_bw;
    g_pyr_last_h = g_bh;
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
                        | (pyr_warm ? FG8_FLAG_PYR_WARM : 0u)
                        | (cut_blocks8() << 16);
    uint32_t mid1_packed = (mid1_idx & 0x7FFFu) | ((t1_q16 & 0xFFFFu) << 16);
    // History copy on a 3-buffer ring (every title; proven on CUSA18723): a game that flips its buffers in turn leaves
    // the previous real frame untouched in g_buf[g_prev_idx] until it renders into it again two flips later, which is
    // submitted after this DCB on the same graphics ring. The full-frame pcopy8 into g_pbuf (8.3 MB written to
    // FlexibleMemory plus two barriers per real frame) then copies exactly what P already holds: skip it and keep P in
    // the game's buffer. Pacing is untouched (g_pcopy_on / g_pcopy_avail and the reserved g_pbuf stay as they are), and
    // the copy comes back by itself when the ring turns 2-buffer at runtime or a real present copy runs (copy_dst != 0).
    // A 3-buffer game that rewrote its previous buffer before the next flip would show the wrong P here.
    if (!g_bench_offscreen) {
        static int s_pbuf_skip_logged = 0;
        int pbuf_skip = !copy_dst && g_ngame >= 3 && !g_runtime_2buf;
        if (pbuf_skip != s_pbuf_skip_logged) {
            s_pbuf_skip_logged = pbuf_skip;
            lg("history copy: %s\n", pbuf_skip ? "skipped (3-buffer ring, P read from the game's buffer)" : "on");
        }
        if (pbuf_skip) { g_t_prev_half16 = 0; g_pcopy_dst_half16 = 0; g_pbuf_valid = 0; }
        else fg8_resolve_2buf_history(&P, C, &copy_dst, &copy_idx, seq);
    }

    fill_table8_wh(tb, P, C, M1, g_bw, g_bh, pitch, tidx, r, mid1_packed, base_flags, g_work, g_mon, stats);

    uint32_t rcas_con_bits = 0, rcas_flags1 = 0, osd_xy = 0, osd_wh1 = 0;
    uint32_t mask1 = two_mids ? 1u /*mid1 only; mid2+cur done in pass 2*/ : 3u /*mid1 + cur*/;
    if (g_bench_offscreen) mask1 = 1u;  // never post-process the game's B/scanout surface during capture
    if (!g_mem_tight || g_cfg.upscale > 0)
        calc_rcas_params((post_mid_only() || g_bench_offscreen) ? 1u /*mid1 only*/ : mask1, &rcas_con_bits, &rcas_flags1);
    if (g_sh8_off[15] && !g_bench_offscreen) osd_build_slot(slotb + OSD_SLOT_OFF, &osd_xy, &osd_wh1, mask1);
    rcas_flags1 = rcas_static_flag(rcas_flags1);
    fill_table8_ext(tb, g_gpu, slotb, rcas_con_bits, rcas_flags1, osd_xy, osd_wh1);

    *(volatile uint64_t *)(slotb + 0x900) = 0;
    *(volatile uint64_t *)(slotb + 0x908) = 0;
    for (int i = 0; i < 5; i++) *(volatile uint64_t *)(slotb + 0x910 + i * 8) = 0;
    for (int i = 0; i < 11; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;
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
    TS2(8);   // t2[5]..t2[8] = rcas8 of the 1st mid alone (0 without post-process)
    // post_mid_only: the real frame gets the profile's Enhance AA WITHOUT sharpening (Enhance off = real untouched), so
    // the aliasing that crawls on edges is smoothed the same way in real and generated frames, and the sharpening only
    // compensates the generated frame's softness. Separate table in the tb2 region (only FG Fast's mid2 uses it; in that
    // mode this pass is skipped). Runs after the warp has read the raw C, before the overlay is drawn.
    if (g_cfg.post_mid == 2 && post_mid_only() && !g_mem_tight && !two_mids && !g_bench_offscreen && g_sh8_off[14]) {
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
        rcas_flags2 = rcas_static_flag(rcas_flags2);
        fill_table8_ext(tb2, g_gpu, slotb, rcas_con2, rcas_flags2, osd_xy, osd_wh1 ? osd_wh2 : 0u);

        int32_t fr2 = insert_wait_flip_done(p, 7, vo_handle, mid2_idx);
        if (fr2 != 0) { p[0] = PKT3(0x10, 6, 0); for (int i = 1; i < 7; i++) p[i] = 0; }
        p += 7;
        TS2(9);   // t2[8]..t2[9] = osd8 of the 1st mid + the wait for the 2nd mid's buffer

        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A; // inv scalar cache
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl2; *p++ = (uint32_t)(tbl2 >> 32);
        SET_PASS(g_sh8_off[4], PHASE8_RSRC1, PHASE8_RSRC2); DISPATCH(1, 1);
        PASS_BARRIER();
        if (gv_warp_active()) { SET_PASS(g_sh8_off[12], WARP8G_RSRC1, WARP8G_RSRC2); }
        else { SET_PASS(g_sh8_off[5], WARP8_RSRC1, WARP8_RSRC2); }
        DISPATCH(D.wx, D.wy);
        PASS_BARRIER();
        TS2(10);  // t2[9]..t2[10] = phase8 + warp8 of the 2nd mid, t2[10]..t2[7] = its rcas8 + osd8
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
    TS2(7);   // t2[5]..t2[7] = rcas8 (+ osd8 / FG Fast mid2), t2[7]..t2[6] = pcopy8 (0 when no copy runs)
    if (copy_dst && g_sh8_off[16]) {   // present copy: finished real frame -> own display or history buffer (see g_pcopy_avail / g_pbuf)
        uint32_t *tb3 = (uint32_t *)(slotb + OSD_SLOT_OFF);   // old text-grid OSD area, unused since the bitmap overlay
        uint64_t tbl3 = (uint64_t)(uintptr_t)tb3;
        fill_pcopy8_table(tb3, C, copy_dst, g_bw, g_bh, pitch, tidx, g_pcopy_dst_half16, g_pcopy_ycocg_mode);
        if (copy_idx != 0xFFFFu) {
            int32_t fr3 = insert_wait_flip_done(p, 7, vo_handle, copy_idx);
            if (fr3 != 0) { p[0] = PKT3(0x10, 6, 0); for (int i = 1; i < 7; i++) p[i] = 0; }
            p += 7;
        }
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

static void cfg_apply_title_defaults(fg_config_t *c)
{
    if (!g_title_id[0]) return;
    if (strcmp(g_title_id, "CUSA10249") == 0) {
        c->audio_fix = 1;
    }
    if (vcfg_in_family(g_title_id, "CUSA11456")) {
        // CUSA11456 / CUSA13323: verified profile M2-R2-P3-T0-F30-TLA (sessions 75649443a28fb1c2 / 3b554846995065e6).
        c->present_copy = 2;
        c->two_buffer = 0;
        c->shave_mode = 2;
        c->reg_mode = 2;
        c->pace_order = 3;
        c->fps_lock = 30;
    } else if (vcfg_in_family(g_title_id, "CUSA08519")) {
        c->shave_mode = 1;
    } else if (vcfg_in_family(g_title_id, "CUSA16734")) {
        c->reg_mode = 1;
        c->pace_order = 2;
    } else if (vcfg_in_family(g_title_id, "CUSA02299")) {
        c->reg_mode = 1;
    } else if (vcfg_in_family(g_title_id, "CUSA09311") ||
               vcfg_in_family(g_title_id, "CUSA18534") ||
               vcfg_in_family(g_title_id, "CUSA10123")) {
        // Dynamic streaming engines: 1 extra buffer in top group at index 15, GV off by default (matches v2.23).
        c->reg_mode = 1;
        c->game_vectors = 0;
    } else if (vcfg_in_family(g_title_id, "CUSA11260") ||
               vcfg_in_family(g_title_id, "CUSA06548") ||
               vcfg_in_family(g_title_id, "CUSA10237") ||
               vcfg_in_family(g_title_id, "CUSA32946")) {
        c->pace_order = 2;
    } else if (vcfg_in_family(g_title_id, "CUSA28561")) {
        c->shave_mode = 2;
    } else if (vcfg_in_family(g_title_id, "CUSA17722")) {
        // CUSA17722: late activation (reg_mode=2) keeps g_mbuf_state=0 during boot so g_pre_mem shrinks to 8 MB on the
        // game's post-RegisterBuffers 0x3a00000 allocation (shaving only 1 MB instead of 25 MB) and activates at flip 120.
        c->reg_mode = 2;
    }
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
    c->manual_mode    = 0;
    c->vout_path      = 0;
    c->gv_conv        = -1;
    c->audio_fix      = 0;
    c->below30        = 0;
    c->shallow_queue  = 1;
    cfg_apply_title_defaults(c);
}

static int parse_ini_file_ex(const char *path, fg_config_t *c, uint32_t *hash_io, int is_adapt)
{
    int fd = sceKernelOpen(path, 0 /*O_RDONLY*/, 0);
    if (fd < 0) return 0;
    char buf[1024];
    long n = (long)sceKernelRead(fd, buf, sizeof(buf) - 1);
    sceKernelClose(fd);
    if (n <= 0) return 0;
    buf[n] = 0;
    if (hash_io) *hash_io = fnv1a_bytes(*hash_io, buf, (size_t)n);
    int file_manual = is_adapt ? 1 : (strstr(buf, "; per-game profile") ? 0 : 1);
    int raw_pace = -1, raw_shave = -1, raw_reg = -1, raw_post = -1;
    int raw_adapt_ver = 0, raw_shave2_failed = 0, raw_early_crashes = 0;
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
                else if (strcmp(key, "manual_mode") == 0) { c->manual_mode = (val != 0); file_manual = (val != 0); }
                else if (strcmp(key, "pace_order") == 0) raw_pace = val < 0 ? 0 : (val > 3 ? 3 : val);
                else if (strcmp(key, "shave_mode") == 0) raw_shave = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "reg_mode") == 0) raw_reg = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "post_mid") == 0) raw_post = val < 0 ? 0 : (val > 3 ? 3 : val);
                else if (strcmp(key, "adapt_ver") == 0) raw_adapt_ver = val;
                else if (strcmp(key, "shave2_failed") == 0) raw_shave2_failed = (val != 0);
                else if (strcmp(key, "early_crashes") == 0) raw_early_crashes = val < 0 ? 0 : (val > 99 ? 99 : val);
                else if (strcmp(key, "vout_path") == 0) c->vout_path = (val != 0);
                else if (strcmp(key, "gv_conv") == 0) c->gv_conv = val < -1 ? -1 : (val > 15 ? 15 : val);
                else if (strcmp(key, "audio_fix") == 0) c->audio_fix = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "hud_protect") == 0) c->hud_protect = val < 0 ? 0 : (val > 2 ? 2 : val);
                else if (strcmp(key, "game_vectors") == 0) c->game_vectors = (val != 0);
                else if (strcmp(key, "tile_idx") == 0) g_tile_override = val < 0 ? -1 : (val > 31 ? 31 : val);
                else if (strcmp(key, "tile_sweep") == 0) g_tile_sweep = val ? 1 : 0;
                else if (strcmp(key, "t6_bit") == 0) g_t6_bit_override = val < 0 ? -1 : (val > 31 ? 31 : val);
                else if (strcmp(key, "fps_lock") == 0) c->fps_lock = (val == 20 || val == 30 || val == 60) ? val : 0;
                else if (strcmp(key, "below30") == 0) c->below30 = (val != 0);
                else if (strcmp(key, "shallow_queue") == 0) c->shallow_queue = (val != 0);
                else if (strcmp(key, "menu_hold_ms") == 0) c->menu_hold_ms = val < 500 ? 500 : (val > 10000 ? 10000 : val);
                else if (strcmp(key, "menu_opacity") == 0) c->menu_opacity = val < 20 ? 20 : (val > 100 ? 100 : val);
                else if (strcmp(key, "overlay") == 0) c->overlay = val < 0 ? 0 : (val > 8 ? 8 : val);
                else if (strcmp(key, "menu_combo") == 0) c->menu_combo = menu_combo_sanitize((uint32_t)val);   // table only, else L3+R3
            }
        }
        if (!saved) break;
        p = eol + 1;
    }
    if (is_adapt) {
        g_adapt_early_crashes = raw_early_crashes;
        if (raw_early_crashes >= 2) {
            c->framegen = 0;
            c->mode = 0;
        }
        if (raw_shave2_failed > 0) {
            raw_shave = -2;
            raw_reg = 0;
        } else if (raw_shave == 2 && raw_adapt_ver < 234) {
            // Auto-rescue users stuck in the legacy v2.33 R17 shave_mode=2 boot crash loop (e.g. CUSA08809)
            raw_shave = 0;
            raw_reg = 0;
        }
    }
    if (file_manual) {
        if (raw_pace >= 0)  c->pace_order = raw_pace;
        if (raw_shave == -2) c->shave_mode = -1;
        else if (raw_shave >= 0) c->shave_mode = raw_shave;
        if (raw_reg >= 0)   c->reg_mode   = raw_reg;
        if (raw_post >= 0)  c->post_mid   = raw_post;
    }
    return 1;
}

static int parse_ini_file(const char *path, fg_config_t *c, uint32_t *hash_io)
{
    return parse_ini_file_ex(path, c, hash_io, 0);
}

static int load_adapt_ini(fg_config_t *c, uint32_t *hash_io)
{
    if (!g_title_id[0] || strcmp(g_title_id, "UNKNOWN") == 0) return 0;
    fg_config_t ac;
    memset(&ac, 0, sizeof(ac));
    ac.framegen = 1;
    ac.mode = 8;
    ac.gv_conv = -1;
    char apath[96];
    snprintf(apath, sizeof(apath), LOG_DIR "/adapt-%s.ini", g_title_id);
    if (!parse_ini_file_ex(apath, &ac, hash_io, 1)) return 0;
    if (g_adapt_early_crashes >= 2 || ac.framegen == 0) {
        c->framegen = 0;
        c->mode = 0;
    }
    int s2_fail = (ac.shave_mode < 0) ? 1 : 0;
    if (ac.shave_mode < 0) {
        g_adapt_shave2_failed = 1;
        ac.shave_mode = 0;
    }
    if (ac.two_buffer && c->present_copy != 2) c->two_buffer = 1;
    if (c->present_copy == 0 && ac.present_copy > 0) c->present_copy = ac.present_copy;
    if (s2_fail && c->shave_mode == 2) {
        c->shave_mode = 0;
    } else if (!c->manual_mode && ac.shave_mode > 0) {
        c->shave_mode = ac.shave_mode;
    }
    if (!c->manual_mode && ac.reg_mode > c->reg_mode) c->reg_mode = ac.reg_mode;
    if (c->pace_order == 0 && ac.pace_order > 0) c->pace_order = ac.pace_order;
    if (c->post_mid   == 0 && ac.post_mid   > 0) c->post_mid   = ac.post_mid;
    if (ac.vout_path) c->vout_path = 1;
    if (ac.gv_conv >= 0) {
        c->gv_conv = ac.gv_conv;
        g_gv_cal_winner = ac.gv_conv;
    }
    return 1;
}

static void apply_adapt_ini(fg_config_t *c, uint32_t *hash_io)
{
    load_adapt_ini(c, hash_io);
}

static int save_adapt_ini(void)
{
    if (!g_title_id[0] || strncmp(g_title_id, "CUSA", 4) != 0) return 0;
    fg_config_t ac;
    memset(&ac, 0, sizeof(ac));
    ac.gv_conv = -1;
    load_adapt_ini(&ac, NULL);
    int prev_ec = g_adapt_early_crashes;
    if (g_flips >= INJECT_AFTER_FLIPS + 600 || g_vo_submit_flips >= INJECT_AFTER_FLIPS + 600) {
        g_adapt_early_crashes = 0;
    }
    if (g_mem_hibernate_count > 0 && !g_adapt_shave2_failed) {
        if (ac.shave_mode == 0) ac.shave_mode = (g_cfg.shave_mode > 0) ? g_cfg.shave_mode : 2;
        if (ac.reg_mode == 0)   ac.reg_mode   = (g_cfg.reg_mode > 0)   ? g_cfg.reg_mode   : 1;
    }
    if (g_ngame == 2 && g_cfg.present_copy != 2) {
        ac.two_buffer = 1;
        if (g_game_idx_hi > 2 && ac.reg_mode == 0) ac.reg_mode = 1;
    }
    if (g_vout_fg_inited > 0 && g_injects == 0) {
        ac.vout_path = 1;
    }
    if (g_gv_cal_locked > 0 && g_gv_cal_winner >= 0) {
        ac.gv_conv = g_gv_cal_winner;
    }
    int cur_ec = g_adapt_early_crashes;
    if (!ac.two_buffer && !ac.shave_mode && !ac.reg_mode && !ac.pace_order &&
        !ac.post_mid && !ac.vout_path && ac.gv_conv < 0 && !cur_ec && !prev_ec) {
        return 0;
    }
    uint32_t sig = 2166136261u;
    sig = fnv1a_bytes(sig, &ac.two_buffer, sizeof(ac.two_buffer));
    sig = fnv1a_bytes(sig, &ac.shave_mode, sizeof(ac.shave_mode));
    sig = fnv1a_bytes(sig, &ac.reg_mode, sizeof(ac.reg_mode));
    sig = fnv1a_bytes(sig, &ac.pace_order, sizeof(ac.pace_order));
    sig = fnv1a_bytes(sig, &ac.post_mid, sizeof(ac.post_mid));
    sig = fnv1a_bytes(sig, &ac.vout_path, sizeof(ac.vout_path));
    sig = fnv1a_bytes(sig, &ac.gv_conv, sizeof(ac.gv_conv));
    sig = fnv1a_bytes(sig, &cur_ec, sizeof(cur_ec));
    if (sig == g_last_adapt_sig) return 0;
    g_last_adapt_sig = sig;

    char apath[96], abuf[360];
    snprintf(apath, sizeof(apath), LOG_DIR "/adapt-%s.ini", g_title_id);
    int pos = snprintf(abuf, sizeof(abuf), "; runtime auto-healer profile for %s\nadapt_ver = 234\n", g_title_id);
    if (cur_ec > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "early_crashes = %d\n", cur_ec);
    if (cur_ec >= 2 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "framegen = 0\n");
    if (g_adapt_shave2_failed > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "shave2_failed = 1\n");
    if (ac.two_buffer > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "two_buffer = %d\n", ac.two_buffer);
    if (ac.shave_mode > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "shave_mode = %d\n", ac.shave_mode);
    if (ac.reg_mode > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "reg_mode = %d\n", ac.reg_mode);
    if (ac.pace_order > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "pace_order = %d\n", ac.pace_order);
    if (ac.post_mid > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "post_mid = %d\n", ac.post_mid);
    if (ac.vout_path > 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "vout_path = %d\n", ac.vout_path);
    if (ac.gv_conv >= 0 && pos < (int)sizeof(abuf))
        pos += snprintf(abuf + pos, sizeof(abuf) - (size_t)pos, "gv_conv = %d\n", ac.gv_conv);
    sceKernelMkdir(LOG_DIR, 0777);
    int fd = sceKernelOpen(apath, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
    if (fd < 0 || pos <= 0) return 0;
    sceKernelWrite(fd, abuf, (size_t)pos);
    sceKernelClose(fd);
    STAGE("self-adapt saved %s: two_buffer=%d shave_mode=%d reg_mode=%d pace_order=%d vout_path=%d gv_conv=%d",
          apath, ac.two_buffer, ac.shave_mode, ac.reg_mode, ac.pace_order, ac.vout_path, ac.gv_conv);
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
    if (c->game_vectors && c->framegen && !g_detected_60fps) gl |= GVL_ON;
    else if (!(g_gv_letters_base & GVL_ON)) gl &= ~GVL_ON;
    gv_set_letters(gl);
    if (reason) {
        lg("config (%s): framegen=%d mode=%d preset=%d sharpness=%d enhance=%d hud_protect=%d game_vectors=%d fps_lock=%d overlay=%d opacity=%d%% below30=%d shallow_queue=%d\n",
           reason, c->framegen, c->mode, c->preset, c->sharpness, c->upscale,
           c->hud_protect, c->game_vectors, c->fps_lock, c->overlay, c->menu_opacity, c->below30, c->shallow_queue);
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
    load_adapt_ini(&c, NULL);
    if (!c.framegen || g_adapt_early_crashes >= 2) return 0;
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
    load_adapt_ini(&c, NULL);
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
    load_adapt_ini(&c, NULL);
    if (c.present_copy == 2) return 0;   // STANDARD buffer mode forced: no virtual queue
    if (c.present_copy == 1 || c.two_buffer) return 1;
    if (vcfg_in_family(g_title_id, "CUSA17419") || vcfg_in_family(g_title_id, "CUSA05571") ||
        vcfg_in_family(g_title_id, "CUSA08809") || vcfg_in_family(g_title_id, "CUSA14209") ||
        vcfg_in_family(g_title_id, "CUSA36375") || vcfg_in_family(g_title_id, "CUSA08519") ||
        vcfg_in_family(g_title_id, "CUSA01015") ||
        vcfg_in_family(g_title_id, "CUSA15778") || vcfg_in_family(g_title_id, "CUSA00083") ||
        vcfg_in_family(g_title_id, "CUSA02299")) return 1;
    return 0;
}

// Clear stale adapt-<TITLE>.ini / safe-<TITLE>.state / safe-<TITLE>.boot when the plugin version changes so games
// that hit a boot issue on an older version automatically get a clean run under the new version.
static void check_title_version_upgrade(void)
{
    if (!g_title_id[0] || strncmp(g_title_id, "CUSA", 4) != 0) return;
    char vpath[96], buf[32] = {0};
    snprintf(vpath, sizeof(vpath), LOG_DIR "/vcfg_ver_%s", g_title_id);
    int fd = sceKernelOpen(vpath, 0 /*O_RDONLY*/, 0);
    if (fd >= 0) {
        long n = (long)sceKernelRead(fd, buf, sizeof(buf) - 1);
        sceKernelClose(fd);
        if (n > 0) {
            while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = 0;
            if (strcmp(buf, VCFG_TRACE_VERSION) == 0) return;
        }
    }
    char p[96];
    snprintf(p, sizeof(p), LOG_DIR "/adapt-%s.ini", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/safe-%s.state", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/safe-%s.boot", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/tile_%s", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/state-%s.ini", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/cut_blocks_%s", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/postfix_%s", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/postfix_split_%s", g_title_id);
    sceKernelUnlink(p);
    snprintf(p, sizeof(p), LOG_DIR "/gridmix_%s", g_title_id);
    sceKernelUnlink(p);
    // If config-<TITLE>.ini has manual_mode=1 saved from an older version's 05 DEV Advisor after a crash on that old
    // version, reset manual_mode to 0 so the new plugin version uses its clean verified defaults while preserving all
    // user visual preferences (framegen, preset, sharpness, enhance, overlay, menu_combo).
    snprintf(p, sizeof(p), LOG_DIR "/config-%s.ini", g_title_id);
    int cfd = sceKernelOpen(p, 0 /*O_RDONLY*/, 0);
    if (cfd >= 0) {
        char cbuf[1024];
        long cn = (long)sceKernelRead(cfd, cbuf, sizeof(cbuf) - 1);
        sceKernelClose(cfd);
        if (cn > 0) {
            cbuf[cn] = 0;
            char *m1 = strstr(cbuf, "manual_mode=1");
            if (!m1) m1 = strstr(cbuf, "manual_mode = 1");
            if (m1) {
                char *eq = strchr(m1, '1');
                if (eq) *eq = '0';
                int wcf = sceKernelOpen(p, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
                if (wcf >= 0) {
                    sceKernelWrite(wcf, cbuf, (size_t)cn);
                    sceKernelClose(wcf);
                }
            }
        }
    }
    sceKernelMkdir(LOG_DIR, 0777);
    int wfd = sceKernelOpen(vpath, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
    if (wfd >= 0) {
        sceKernelWrite(wfd, VCFG_TRACE_VERSION "\n", strlen(VCFG_TRACE_VERSION) + 1);
        sceKernelClose(wfd);
    }
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
    g_cfg.framegen     = c.framegen;
    g_cfg.mode         = c.mode;
    g_cfg.fps_lock     = c.fps_lock;
    g_cfg.present_copy = c.present_copy;
    g_cfg.manual_mode  = c.manual_mode;
    g_cfg.two_buffer   = c.two_buffer;
    g_cfg.pace_order   = c.pace_order;
    g_cfg.shave_mode   = c.shave_mode;
    g_cfg.reg_mode     = c.reg_mode;
    g_cfg.post_mid     = c.post_mid;
    g_cfg.vout_path    = c.vout_path;
    g_cfg.gv_conv      = c.gv_conv;
    g_cfg.audio_fix    = c.audio_fix;
    g_cfg.below30      = c.below30;
    g_cfg.shallow_queue = c.shallow_queue;
    g_fg_off           = !c.framegen;
}

static void *alloc_mbufs(size_t total)
{
    long phys = 0; void *addr = NULL;
    size_t range = sceKernelGetDirectMemorySize();
    long top_e = (long)(range & ~(size_t)0x1FFFFFu);
    long top_s = (top_e > (long)total) ? (top_e - (long)total) : 0L;
    g_own_alloc++;
    int r = (top_s > 0) ? sceKernelAllocateDirectMemory(top_s, top_e, total, 0x200000, 3 /*WC_GARLIC*/, &phys) : -1;
    if (r != 0)
        r = sceKernelAllocateDirectMemory(0, (long)range, total, 0x200000, 3 /*WC_GARLIC*/, &phys);
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
    if (!g_pre_mem) {
        g_pre_mem = addr;
        g_pre_size = total;
        g_pre_mem_phys = phys;
    }
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
    if (g_mbuf_state == 1 && !g_pre_mem) {
        g_mbuf_state = -1;
        g_pcopy_avail = (g_pbuf != NULL) ? 1 : 0;
        g_pcopy_on = 0;
        return 0;
    }
    return g_mbuf_state == 1;
}

static size_t safe_surface_stride(void);
static void on_register_buffers_2buf(int count, size_t surf_sz)
{
    g_flip_ring_cnt = 0;
    g_pbuf_valid = 0;
    g_pbuf_last_seq = 0;
    g_pbuf_prev_va = 0;
    g_t_prev_half16 = 0;
    g_pcopy_dst_half16 = 0;
    g_pcopy_ycocg_mode = 0;
    if (count == 2 || g_cfg.present_copy == 1 || g_cfg.two_buffer == 1) {
        if (count == 2) g_runtime_2buf = 1;
        if (g_cfg.present_copy != 2) {
            if (count == 2 || g_cfg.two_buffer == 1) g_vq_on = 1;
            if (!g_pcopy_avail || g_pcopy_k < 2) {
                if (alloc_pbuf_history(surf_sz)) {
                    g_pcopy_avail = 1;
                }
            }
        }
        STAGE("2-buffer setup at RegisterBuffers (count=%d): runtime_2buf=%d vq_on=%d pcopy_avail=%d pbuf=%p (sz=0x%zx ycocg=%d)",
              count, g_runtime_2buf, g_vq_on, g_pcopy_avail, g_pbuf, g_pbuf_sz, g_pbuf_ycocg);
    } else {
        g_runtime_2buf = 0;
        g_vq_on = 0;
        if (g_pbuf) release_pbuf();
    }
}

static void on_flip_2buf_track(uint32_t idx)
{
    g_flip_ring_hist[g_flip_ring_cnt & 7u] = idx;
    g_flip_ring_cnt++;
    if (g_flip_ring_cnt >= 6u) {
        uint32_t b0 = g_flip_ring_hist[(g_flip_ring_cnt - 1u) & 7u];
        uint32_t b1 = 0xFFFFFFFFu;
        int only_two = 1;
        for (uint32_t k = 1; k < 6u; k++) {
            uint32_t v = g_flip_ring_hist[(g_flip_ring_cnt - 1u - k) & 7u];
            if (v == b0) continue;
            if (b1 == 0xFFFFFFFFu) { b1 = v; continue; }
            if (v != b1) { only_two = 0; break; }
        }
        if (only_two && b1 != 0xFFFFFFFFu && b0 != b1) {
            if (!g_runtime_2buf) {
                g_runtime_2buf = 1;
                if (g_cfg.present_copy != 2) {
                    g_vq_on = 1;
                    if (!g_pbuf && (!g_pcopy_avail || g_pcopy_k < 2)) {
                        if (alloc_pbuf_history(safe_surface_stride())) {
                            g_pcopy_avail = 1;
                        }
                    }
                }
                pyr_cache_invalidate();
                STAGE("2-buffer flip ring detected at runtime (indices %u,%u): vq_on=%d pcopy_avail=%d pbuf=%p (sz=0x%zx ycocg=%d)",
                      b0, b1, g_vq_on, g_pcopy_avail, g_pbuf, g_pbuf_sz, g_pbuf_ycocg);
            }
        } else if (g_runtime_2buf && g_ngame > 2 && g_cfg.two_buffer == 0 && g_cfg.present_copy != 1) {
            g_runtime_2buf = 0;
            g_vq_on = 0;
            g_pbuf_valid = 0;
            g_pbuf_prev_va = 0;
            pyr_cache_invalidate();
        }
    }
    if ((g_ngame == 2 || g_runtime_2buf || g_vq_on) && g_cfg.present_copy != 2 &&
        !g_pbuf && (!g_pcopy_avail || g_pcopy_k < 2)) {
        if (alloc_pbuf_history(safe_surface_stride())) {
            g_pcopy_avail = 1;
        }
    }
}

// ---- safe mode (titles not verified on hardware) ----
// Such a title gets none of our memory, no changed RegisterBuffers and no forced flip rate while it starts: the plugin
// only watches. After INJECT_AFTER_FLIPS frames safe_try_activate() checks what it saw and, only if everything is
// fine, takes the memory and registers its extra display buffers as a separate group at the top of the 16 indices.
// A refusal leaves the game exactly as it runs without the plugin ("INERT: SAFE ..." in the report).
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
    if (g_safe && !strncmp(why, "NO ", 3) && strstr(why, "MEM")) {   // no free memory late: next start takes it early
        safe_state_write(1);
        STAGE("safe mode: state saved -> the next start takes its memory early (a start that does not reach frame %d is then given up)", INJECT_AFTER_FLIPS);
    }
}
// Surface size of one game display buffer: the VA gap between the game's buffers if it matches a single surface,
// clamped to pitch * aligned_height * 4 so interleaved depth/GBuffer allocations (e.g. CUSA14209 / CUSA36375 16 MB gap,
// CUSA32836 31.7 MB gap) never over-size our scanout buffers.
static size_t safe_surface_stride(void)
{
    size_t one = 0;
    for (int i = 0; i + 1 < g_ngame; i++) {
        uintptr_t a0 = (uintptr_t)g_buf[i], a1 = (uintptr_t)g_buf[i + 1];
        size_t diff = a1 > a0 ? (size_t)(a1 - a0) : (size_t)(a0 - a1);
        if (diff >= 0x100000 && diff <= 0x4000000 && !(diff & 0x7FFF) && (!one || diff < one)) one = diff;
    }
    if (g_pitch >= 640 && g_bh >= 360) {
        size_t raw_sz = (size_t)g_pitch * (size_t)((g_bh + 63u) & ~63u) * 4u;
        size_t surf_sz = (raw_sz + 0x7FFFFu) & ~(size_t)0x7FFFFu;
        int clamp_interleaved = (!one || one > surf_sz + 0x100000u || (g_pre_mem && g_pre_size >= surf_sz && g_pre_size < one));
        if (surf_sz >= 0x100000 && clamp_interleaved) one = surf_sz;
    }
    return (one < 0x100000 || one > 0x4000000 || (one & 0x7FFF)) ? 0 : one;
}

static void *g_ext_va[8];
static int g_ext_nm = 0;
static int g_ext_attr_slot = -1;
static VoAttr g_ext_attr;

// Called once on the game's flip thread at frame INJECT_AFTER_FLIPS, after the config was read.
static void safe_try_activate(uint32_t handle)
{
    g_safe_done = 1;
    STAGE("safe mode frame %u: check (title %s, %ux%u fmt=0x%x ngame=%d idx_hi=%d detected60=%d game_alloc_fail=%u)", g_flips,
          g_title_id, g_bw, g_bh, g_attr.format, g_ngame, g_game_idx_hi, g_detected_60fps, g_dbg_alloc_fail);
    STAGE("safe mode frame %u: free direct memory: largest block for align 0x10000 = 0x%zx, 0x200000 = 0x%zx (pool 0x%zx)", g_flips,
          dm_avail(0x10000), dm_avail(0x200000), (size_t)sceKernelGetDirectMemorySize());
    if (g_fg_off && !g_cfg.upscale && !g_cfg.overlay && !g_detected_60fps) {
        STAGE("safe mode: FG, Enhance and overlay all off -> nothing to set up");
        release_early_mem(); release_early_work(); release_early_gpu();
        return;
    }
    if (!g_fmt_supported) { char w[24]; snprintf(w, sizeof(w), "FMT 0x%x", g_attr.format); safe_refuse(w); return; }
    if (!fg_res_supported(g_bw, g_bh)) { char w[24]; snprintf(w, sizeof(w), "RES %ux%u", g_bw, g_bh); safe_refuse(w); return; }
    if (g_ngame < 2 || g_ngame > 8) { safe_refuse("BUFFERS"); return; }
    size_t one = safe_surface_stride();
    if (!one) { safe_refuse("SURFACE SIZE"); return; }
    int hi = g_game_idx_hi > g_ngame ? g_game_idx_hi : g_ngame;
    int want_pcopy = (g_cfg.present_copy == 1) || (g_cfg.present_copy == 0 && g_ngame == 2);
    int hi_res = (g_bw > 1920 || g_bh > 1080);
    int stream_3buf = (!want_pcopy &&
                       ((!g_known_game && g_dbg_allocs >= 32u) ||
                        vcfg_in_family(g_title_id, "CUSA09311") ||
                        vcfg_in_family(g_title_id, "CUSA18534") ||
                        vcfg_in_family(g_title_id, "CUSA10123")));
    // Find the highest contiguous range of unused buffer indices [free_base, free_end) in [g_ngame, 16).
    int free_base = hi < 16 ? hi : 16;
    int free_end = 16;
    if (free_base >= 16) {
        for (int e = 16; e > g_ngame; e--) {
            if (g_buf[e - 1] != 0) continue;
            int s = e - 1;
            while (s > g_ngame && g_buf[s - 1] == 0) s--;
            free_base = s;
            free_end = e;
            break;
        }
    }
    int avail_idx = free_end - free_base;
    int want = want_pcopy ? (hi_res ? 4 : 6) : (stream_3buf ? 1 : (hi_res ? 2 : 3));
    if (want > avail_idx) want = avail_idx;
    if (want < 1) { safe_refuse("NO INDICES"); return; }
    // Allocate display buffers (m) FIRST before g_pre_gpu / g_pre_work so our 2MB-aligned scanout allocation never
    // competes with GPU/work buffers (which can fall back to FlexibleMemory via alloc_flex_gpu).
    uint8_t *m = NULL; int nm = 0;
    if (g_pre_mem && g_pre_size >= one) {
        nm = (int)(g_pre_size / one);
        if (nm > want) nm = want;
        size_t used_pre = (one * (size_t)nm + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
        if (used_pre > 0 && used_pre < g_pre_size) {
            shrink_early_mem_to_ex(used_pre, 0);
        }
        m = (uint8_t *)g_pre_mem;
    }
    for (int n = want; !m && n >= 1; n = (n == 6 ? 4 : n - 1)) {   // 6, 4, 3, 2, 1
        size_t need = (one * (size_t)n + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
        if (n > 1 && dm_avail(0x200000) < need + (128ull << 20)) continue;
        if (n == 1 && dm_avail(0x200000) < need + 0x400000u) continue;
        m = (uint8_t *)alloc_mbufs(need);
        if (m) nm = n;
    }
    if (!m) { safe_refuse("NO DISPLAY MEM"); return; }
    // Use early pre-reserved buffers if they survived boot; otherwise use FlexibleMemory first for stream_3buf titles
    // so late activation consumes 0 extra bytes of DirectMemory.
    size_t safe_flex_av = 0;
    int safe_fq = ((int32_t (*)(size_t *))(void *)sceKernelAvailableFlexibleMemorySize)(&safe_flex_av);
    if (!g_pre_work && dm_avail(0x10000) < WORK_SZ_8 &&
        safe_fq == 0 && safe_flex_av < WORK_SZ_8 + 0x800000u) {
        safe_refuse("NO WORK MEM");
        return;
    }
    g_gpu_sz = gv_armed() ? GPU_SZ : GPU_SZ_BASE;
    if (!g_pre_gpu && stream_3buf) {
        g_pre_gpu = alloc_flex_gpu(g_gpu_sz, g_gpu_sz);
        g_pre_gpu_phys = -1;
    }
    if (!g_pre_gpu) g_pre_gpu = alloc_dm_range(0, (long)sceKernelGetDirectMemorySize(), g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    if (!g_pre_gpu) {
        g_pre_gpu = alloc_flex_gpu(g_gpu_sz, g_gpu_sz);
        g_pre_gpu_phys = -1;
    }
    if (!g_pre_gpu) { safe_refuse("NO GPU MEM"); return; }
    if (!g_pre_work && stream_3buf) {
        g_pre_work = alloc_flex_gpu(WORK_SZ_8, 0x10000);
        g_pre_work_phys = -1;
        g_pre_work_sz = g_pre_work ? WORK_SZ_8 : 0;
        g_work_garlic = 0;
    }
    if (!g_pre_work) {
        g_pre_work = alloc_work_early(WORK_SZ_8, 3);
        g_work_garlic = (g_pre_work != NULL && g_pre_work_phys >= 0);
    }
    if (!g_pre_work) g_pre_work = alloc_work_early(WORK_SZ_8, 0);
    if (!g_pre_work) { safe_refuse("NO WORK MEM"); return; }
    int base = free_end - nm;   // top of the available index range: the game's own indices stay free
    if (base < free_base) { safe_refuse("NO INDICES"); return; }
    void *ext[8];
    for (int i = 0; i < nm; i++) ext[i] = m + (size_t)i * one;
    int32_t r1 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                               (int32_t)handle, base, ext, nm, &g_attr);
    if (r1 == (int32_t)0x80290010) {
        for (int sl = 1; sl < 4; sl++) sceVideoOutUnregisterBuffers((int32_t)handle, sl);
        r1 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                           (int32_t)handle, base, ext, nm, &g_attr);
    }
    STAGE("safe mode RegisterBuffers (late group at %d, %d x 0x%zx) = 0x%08x", base, nm, one, (unsigned)r1);
    if (r1 < 0) { safe_refuse("REGISTER FAILED"); return; }
    g_ext_attr_slot = r1;
    g_ext_nm = nm <= 8 ? nm : 8;
    for (int i = 0; i < g_ext_nm; i++) g_ext_va[i] = ext[i];
    g_ext_attr = g_attr;
    for (int i = 0; i < nm; i++) g_buf[base + i] = (uint64_t)(uintptr_t)ext[i];
    g_mbase = base;
    g_nbuf = base + nm;
    g_pcopy_avail = (want_pcopy && nm >= 4);
    g_pcopy_k = nm >= 6 ? 3 : (nm >= 4 ? 2 : 1);
    g_nmid = g_pcopy_avail ? g_pcopy_k : (nm < 3 ? nm : 3);
    g_mbuf_state = 1;
    on_register_buffers_2buf(g_ngame, one);
    int32_t rr = HOOK_CONTINUE(sceVideoOutSetFlipRate, int32_t (*)(int32_t, int32_t), (int32_t)handle, 0);
    g_safe_active = 1;
    g_inert_reason[0] = 0;
    STAGE("safe mode activated at frame %u: nm=%d base=%d game flip rate was %d, SetFlipRate(0)=0x%08x", g_flips, nm, base,
          (int)g_game_flip_rate, (unsigned)rr);
    lg("safe mode: activated at frame %u (base=%d, extra=%d, SetFlipRate(0)=0x%08x)\n", g_flips, base, nm, (unsigned)rr);
}

static uint64_t g_pace_next = 0;
// Refresh period for the pacers: the measured one (exact), or the rounded rate until the first window is measured.
static uint32_t refresh_period_q8(void)
{
    uint32_t q = __atomic_load_n(&g_vb_win_q8, __ATOMIC_ACQUIRE);
    return q ? q : vb_period_q8_from_hz10(g_last_hz_x10);
}

// ---- q-cpucost (cpu_cost.h): opt-in cuts of the plugin's own CPU work and the CPU plugin line ----
static uint32_t g_cpu_ema_us;   // defined with the pacers' state below
int32_t sceVideoOutAddVblankEvent(OrbisKernelEqueue eq, int32_t handle, void *udata);
int32_t sceVideoOutAddFlipEvent(OrbisKernelEqueue eq, int32_t handle, void *udata);
static volatile uint32_t g_cc_mon_wakes, g_cc_mon_vb, g_cc_mon_flip, g_cc_mon_to, g_cc_mon_lat_n, g_cc_mon_lat_max;
static volatile uint64_t g_cc_mon_lat_sum;
static volatile uint32_t g_cc_io_wakes, g_cc_pad_wakes, g_cc_shq_polls, g_cc_pace_n, g_cc_pace_err_max;
static volatile uint64_t g_cc_spin_us, g_cc_pace_err_sum;
static volatile uint64_t g_cc_io_cpu_us, g_cc_pad_cpu_us;   // cumulative thread CPU of the io and pad threads (us)
static uint64_t g_cc_off_rel = 0;                           // framegen=0: when the game's SubmitAndFlip returned (us)
static uint32_t g_cc_off_n = 0, g_cc_off_sum_us = 0, g_cc_off_max_us = 0;   // hook thread only
static uint32_t g_cc_late_ema = 0;                          // pacer sleep lateness (hook thread only)
static int32_t g_cc_clk_rc = 0;                             // last refusal of a CPU clock (0: none)
#define CC_CLK_THREAD  14   // FreeBSD CLOCK_THREAD_CPUTIME_ID (the toolchain's time.h has the Linux numbers)
#define CC_CLK_PROCESS 15   // FreeBSD CLOCK_PROCESS_CPUTIME_ID (Sony's header names 15 PROCTIME: checked against CLOCK_PROF)
#define CC_CLK_PROF    2    // FreeBSD CLOCK_PROF: user + system time of the process

// CPU time of the calling thread or of the whole game process (us), through sceKernelClockGettime (declared without a
// prototype in the toolchain header). 0, with the error code kept in g_cc_clk_rc, when the clock is refused.
static uint64_t cc_cpu_us(int32_t clk)
{
    struct timespec ts;
    memset(&ts, 0, sizeof(ts));
    int32_t rc = ((int32_t (*)(int32_t, struct timespec *))sceKernelClockGettime)(clk, &ts);
    if (rc) { g_cc_clk_rc = rc; return 0u; }
    return cc_ts_us((int64_t)ts.tv_sec, (int64_t)ts.tv_nsec);
}
static int cc_file(const char *name)
{
    char p[64];
    snprintf(p, sizeof(p), LOG_DIR "/%s_%s", name, g_title_id);
    int fd = sceKernelOpen(p, 0, 0666);
    if (fd < 0) return 0;
    sceKernelClose(fd);
    return 1;
}
// Once, when the io thread starts (title known, outside plugin_load).
static void cc_read_cuts(void)
{
    g_cc_monev = cc_file("monev");
    g_cc_iolazy = cc_file("iolazy");
    g_cc_nospin = cc_file("nospin");
    g_tr_lite = cc_file("tracelite");
    g_cc_quiet = cc_file("quietlog");
    lg("cpu cuts: monev=%d iolazy=%d nospin=%d tracelite=%d quietlog=%d (files " LOG_DIR "/<cut>_%s)\n",
       g_cc_monev, g_cc_iolazy, g_cc_nospin, g_tr_lite, g_cc_quiet, g_title_id);
}

// Timer pacers: wait until the process-time counter reaches target. Today: sleep until 1 ms before it (nothing below
// 1.5 ms) and spin the rest. nospin: sleep the remaining time minus the measured lateness and return, never spin.
// Both measure the spin and how far from the target the game is released.
static void cc_wait_until(uint64_t target)
{
    uint64_t now = sceKernelGetProcessTimeCounter();
    if (now >= target || !g_freq) return;
    uint64_t rem_us = (target - now) * 1000000ull / g_freq;
    uint32_t s = g_cc_nospin ? cc_nospin_sleep_us(rem_us, g_cc_late_ema) : cc_spin_sleep_us(rem_us);
    if (s) {
        uint64_t a = sceKernelGetProcessTime();
        sceKernelUsleep(s);
        g_cc_late_ema = cc_late_ema(g_cc_late_ema, s, sceKernelGetProcessTime() - a);
    }
    if (!g_cc_nospin) {
        uint64_t s0 = sceKernelGetProcessTimeCounter();
        while (sceKernelGetProcessTimeCounter() < target) { }
        uint64_t s1 = sceKernelGetProcessTimeCounter();
        if (s1 > s0) g_cc_spin_us += (s1 - s0) * 1000000ull / g_freq;
    }
    uint64_t end = sceKernelGetProcessTimeCounter();
    uint64_t err = (end > target ? end - target : target - end) * 1000000ull / g_freq;
    g_cc_pace_n++;
    g_cc_pace_err_sum += err;
    if (err > g_cc_pace_err_max) g_cc_pace_err_max = err > 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)err;
}

// Pending flips for a decision in the hook. With monev the monitor sleeps between vblanks, so its sample misses the
// flips the hook submitted since: read the raw status instead (one VideoOut call per decision, a few per frame).
static uint32_t cc_pending_flips(void)
{
    if (g_cc_monev && g_handle >= 0) {
        VoFlipStatus fs;
        memset(&fs, 0, sizeof(fs));
        if (read_flip_status(g_handle, &fs) == 0) return (uint32_t)(fs.flipPendingNum > 0 ? fs.flipPendingNum : 0);
    }
    return __atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE);
}

// monev: our own event queue on the game's handle, vblank + flip events (udata tells them apart). Returns 1 while
// vblank events are on; on any refusal the monitor polls as before.
static OrbisKernelEqueue g_cc_eq;
static int g_cc_eq_made = 0, g_cc_eq_vb = 0;
static int32_t g_cc_eq_h = -1;
static int cc_mon_attach(int32_t h)
{
    if (g_cc_eq_h == h) return g_cc_eq_vb;
    if (!g_cc_eq_made) {
        int32_t r = sceKernelCreateEqueue(&g_cc_eq, "bb_mon");
        if (r) { lg("monitor events: equeue 0x%08x -> polling as before\n", (unsigned)r); g_cc_monev = 0; return 0; }
        g_cc_eq_made = 1;
    }
    int32_t rv = sceVideoOutAddVblankEvent(g_cc_eq, h, CC_EV_VBLANK);
#if VCFG_2BUF_TEST
    int32_t rf = -1;   // test builds hook sceVideoOutAddFlipEvent to find the game's own waits: vblank events only
#else
    int32_t rf = sceVideoOutAddFlipEvent(g_cc_eq, h, CC_EV_FLIP);
#endif
    g_cc_eq_h = h;
    g_cc_eq_vb = rv == 0;
    lg("monitor events on handle %d: vblank 0x%08x flip 0x%08x -> %s\n", (int)h, (unsigned)rv, (unsigned)rf,
       g_cc_eq_vb ? "event wait" : "polling as before");
    if (!g_cc_eq_vb) g_cc_monev = 0;
    return g_cc_eq_vb;
}
// Monitor wait: 1 when a vblank event woke it (the vblank status then belongs to the vblank that just happened).
static int cc_mon_wait(void)
{
    if (g_cc_monev && g_handle >= 0 && cc_mon_attach(g_handle)) {
        OrbisKernelEvent ev[4];
        int32_t n = 0, vb = 0;
        OrbisKernelUseconds to = CC_MON_EV_TIMEOUT_US;
        int32_t rc = sceKernelWaitEqueue(g_cc_eq, ev, 4, &n, &to);
        if (rc == 0 && n > 0) {
            for (int32_t i = 0; i < n && i < 4; i++) {
                if (ev[i].udata == CC_EV_VBLANK) { vb = 1; g_cc_mon_vb++; }
                else if (ev[i].udata == CC_EV_FLIP) g_cc_mon_flip++;
            }
        } else if (rc == 0 || (uint32_t)rc == 0x8002003cu || (uint32_t)rc == 0x80020004u) {
            g_cc_mon_to++;   // timeout or interrupted: sample anyway
        } else {
            lg("monitor events: wait 0x%08x -> polling as before\n", (unsigned)rc);
            g_cc_monev = 0;
        }
        return vb;
    }
    sceKernelUsleep(CC_MON_POLL_US);
    return 0;
}
static void cc_mon_vblank_latency(uint64_t vb_t)
{
    uint64_t now = sceKernelGetProcessTime();
    uint64_t l = now > vb_t ? now - vb_t : 0u;
    if (l > 0xFFFFFFFFull) l = 0xFFFFFFFFull;
    g_cc_mon_lat_n++;
    g_cc_mon_lat_sum += l;
    if ((uint32_t)l > g_cc_mon_lat_max) g_cc_mon_lat_max = (uint32_t)l;
}
// Monitor window (2 s), debug log on: wake-ups per second of each plugin thread, their CPU and the game process's CPU,
// the pacer's spin and release error, the shallow queue's polls, the flight recorder's and klog's writes, the cuts on
// and the game's own time per frame. The same line with and without a cut file compares it in the same scene.
static void cc_window_log(uint64_t dt_us)
{
    static uint64_t last_mon, last_io, last_pad, last_proc, last_prof, last_tr_bytes;
    static uint32_t last_tr_flushes;
    static int primed = 0;
    uint64_t mon = cc_cpu_us(CC_CLK_THREAD), proc = cc_cpu_us(CC_CLK_PROCESS), prof = cc_cpu_us(CC_CLK_PROF);
    uint64_t io = __atomic_load_n(&g_cc_io_cpu_us, __ATOMIC_ACQUIRE), pad = __atomic_load_n(&g_cc_pad_cpu_us, __ATOMIC_ACQUIRE);
    uint32_t wk = __atomic_exchange_n(&g_cc_mon_wakes, 0u, __ATOMIC_ACQ_REL), vb = __atomic_exchange_n(&g_cc_mon_vb, 0u, __ATOMIC_ACQ_REL);
    uint32_t fl = __atomic_exchange_n(&g_cc_mon_flip, 0u, __ATOMIC_ACQ_REL), to = __atomic_exchange_n(&g_cc_mon_to, 0u, __ATOMIC_ACQ_REL);
    uint32_t ln = __atomic_exchange_n(&g_cc_mon_lat_n, 0u, __ATOMIC_ACQ_REL), lmax = __atomic_exchange_n(&g_cc_mon_lat_max, 0u, __ATOMIC_ACQ_REL);
    uint64_t lsum = __atomic_exchange_n(&g_cc_mon_lat_sum, 0ull, __ATOMIC_ACQ_REL);
    uint32_t iow = __atomic_exchange_n(&g_cc_io_wakes, 0u, __ATOMIC_ACQ_REL), pw = __atomic_exchange_n(&g_cc_pad_wakes, 0u, __ATOMIC_ACQ_REL);
    uint32_t shq = __atomic_exchange_n(&g_cc_shq_polls, 0u, __ATOMIC_ACQ_REL), pn = __atomic_exchange_n(&g_cc_pace_n, 0u, __ATOMIC_ACQ_REL);
    uint32_t pmax = __atomic_exchange_n(&g_cc_pace_err_max, 0u, __ATOMIC_ACQ_REL);
    uint64_t psum = __atomic_exchange_n(&g_cc_pace_err_sum, 0ull, __ATOMIC_ACQ_REL), spin = __atomic_exchange_n(&g_cc_spin_us, 0ull, __ATOMIC_ACQ_REL);
    uint32_t lines = __atomic_exchange_n(&g_cc_lines, 0u, __ATOMIC_ACQ_REL), kb = __atomic_exchange_n(&g_cc_klog_bytes, 0u, __ATOMIC_ACQ_REL);
    uint32_t trf = g_tr_flushes;
    uint64_t trb = g_tr_bytes;
    if (primed && g_log && dt_us) {
        double k = 1000.0 / (double)dt_us;   // us of CPU over the window -> ms per second
        lgd("CPU plugin: wake/s mon %.0f (vb %u flip %u to %u, vb lat avg %.0f max %u us) io %.0f pad %.0f | cpu ms/s mon %.2f "
            "io %.2f pad %.2f game process %.0f (prof %.0f) | pacer spin ms/s %.2f err avg %.0f max %u us (%u) | shq polls/s %.0f | "
            "trace %u flushes %.0f B/s | klog %.1f lines/s %.0f B/s | cuts %d%d%d%d%d clk 0x%x | own %.1f ms\n",
            cc_per_s(wk, dt_us), vb, fl, to, ln ? (double)lsum / ln : 0.0, lmax, cc_per_s(iow, dt_us), cc_per_s(pw, dt_us),
            (double)(mon - last_mon) * k, (double)(io - last_io) * k,
            (double)(pad - last_pad) * k, (double)(proc - last_proc) * k, (double)(prof - last_prof) * k,
            (double)spin * k, pn ? (double)psum / pn : 0.0, pmax, pn, cc_per_s(shq, dt_us),
            trf - last_tr_flushes, cc_per_s(trb - last_tr_bytes, dt_us), cc_per_s(lines, dt_us), cc_per_s(kb, dt_us),
            g_cc_monev, g_cc_iolazy, g_cc_nospin, g_tr_lite, g_cc_quiet, (unsigned)g_cc_clk_rc, g_cpu_ema_us / 1000.0);
    }
    primed = 1;
    last_mon = mon; last_io = io; last_pad = pad; last_proc = proc; last_prof = prof; last_tr_flushes = trf; last_tr_bytes = trb;
}
// framegen=0 (no monitor thread): the game's own time per frame (its SubmitAndFlip returning -> the next one entering)
// and the process CPU, from the STAT gnm window, debug log on. The same scene with framegen=1 separates the CPU.
static void cc_fgoff_log(void)
{
    static uint64_t last_proc, last_prof, last_t;
    uint64_t proc = cc_cpu_us(CC_CLK_PROCESS), prof = cc_cpu_us(CC_CLK_PROF), t = sceKernelGetProcessTime();
    uint32_t n = g_cc_off_n, sum = g_cc_off_sum_us, mx = g_cc_off_max_us;
    g_cc_off_n = g_cc_off_sum_us = g_cc_off_max_us = 0;
    if (last_t && t > last_t && g_log)
        lgd("OWN fg-off: game release->submit avg %.1f max %.1f ms (%u frames) | game process cpu ms/s %.0f (prof %.0f) | klog %u lines clk 0x%x\n",
            n ? sum / 1000.0 / n : 0.0, mx / 1000.0, n, (double)(proc - last_proc) / (double)(t - last_t) * 1000.0,
            (double)(prof - last_prof) / (double)(t - last_t) * 1000.0,
            __atomic_exchange_n(&g_cc_lines, 0u, __ATOMIC_ACQ_REL), (unsigned)g_cc_clk_rc);
    last_proc = proc; last_prof = prof; last_t = t;
}

static void pace_hz(uint32_t hz)
{
    if (!hz || !g_freq) return;
    uint32_t vb_q8 = refresh_period_q8();
    uint64_t period = vb_ticks(g_freq, vb_q8, vb_per_frame(hz, vb_q8));   // whole refreshes: 60 -> 1, 30 -> 2, 20 -> 3
    uint64_t now = sceKernelGetProcessTimeCounter();
    if (g_pace_next == 0 || now > g_pace_next + period * 4) { g_pace_next = now + period; return; }
    if (now < g_pace_next) cc_wait_until(g_pace_next);
    g_pace_next += period;
}
// ---- shave14: 2nd generated-frame buffer from a 16 MiB display reserve (S14_KEEP) ----
// Decided once in plugin_load, before the game's first allocation. Candidates: the CUSA18723 family (where it was
// validated on the console) with below30 on, and any title with LOG_DIR/shave14_<TITLE>. It needs the configuration
// where today the plugin takes the 8 MiB history buffer that shave14 gives back to the game (two_buffer or present_copy
// forced), FG mode 8 with below30 on, and arena shaving available; otherwise the boot is exactly as without shave14.
// Any other title keeps today's boot: the below-30 regime then runs only where the mode-8 allocation already has a
// 2nd generated-frame buffer.
// Runs inside plugin_load: STAGE (raw kernel I/O) only. lg() opens probe.log with stdio, and stdio inside plugin_load
// faults in libSceLibcInternal (boot_stage.h); the probe.log line is written later, at RegisterBuffers (s14_log_decision).
static int g_s14_opt = 0;   // 0 not a candidate (no line), 1 LOG_DIR/shave14_<TITLE>, 2 CUSA18723 family default
static const char *g_s14_why = NULL;
static void s14_decide(int mode)
{
    char p[64];
    snprintf(p, sizeof(p), LOG_DIR "/shave14_%s", g_title_id);
    int fd = sceKernelOpen(p, 0, 0666);
    int opt = fd >= 0 ? 1 : (vcfg_in_family(g_title_id, "CUSA18723") && g_cfg.below30) ? 2 : 0;
    if (fd >= 0) sceKernelClose(fd);
    const char *why = !opt ? "no file"
        : g_bench_enabled ? "bench"
        : mode != 8 ? "FG mode is not 8"
        : g_safe_never ? "safe mode only observes this title"
        : g_cfg.shave_mode != 0 ? "shave_mode is not 0 (no boot valve or no arena shaving)"
        : !g_cfg.below30 ? "below30=0"
        : (g_cfg.present_copy == 2 || (g_cfg.present_copy != 1 && g_cfg.two_buffer != 1))
          ? "no history buffer to give back (two_buffer=0), the game would lose 8 MiB of FlexibleMemory"
        : cusa10249_audio_fix() ? "audio_fix"
        : NULL;
    g_s14_on = why == NULL;
    g_s14_opt = opt;
    g_s14_why = why;
    if (!opt) return;   // not a candidate: no line, the boot log stays as it is
    STAGE("shave14: %s (%s)%s%s", g_s14_on ? "on" : "off", opt == 1 ? p : "CUSA18723 family default", why ? ": " : "",
          why ? why : "");
}
// The decision in probe.log, once, from the game's RegisterBuffers (stdio is safe there; plugin_load is not).
static void s14_log_decision(void)
{
    static int done;
    if (done || !g_s14_opt) return;
    done = 1;
    if (g_s14_opt == 1)
        lg("shave14: %s (" LOG_DIR "/shave14_%s)%s%s\n", g_s14_on ? "on" : "off", g_title_id, g_s14_why ? ": " : "",
           g_s14_why ? g_s14_why : "");
    else
        lg("shave14: %s (CUSA18723 family default)%s%s\n", g_s14_on ? "on" : "off", g_s14_why ? ": " : "",
           g_s14_why ? g_s14_why : "");
}
// RegisterBuffers (reg_mode 0 and 1) after our nm display buffers of `one` bytes at m are registered: with the reserve
// kept at S14_KEEP (trimmed to what nm buffers need below 1080p) the 2nd one is the 2nd generated-frame buffer (g_nmid = 2,
// b30_available), otherwise back to one.
static void s14_after_register(int nm, size_t one, const uint8_t *m, int idx2)
{
    s14_log_decision();
    if (g_s14_state != 1) return;
    size_t fa = 0;
    ((int32_t (*)(size_t *))(void *)sceKernelAvailableFlexibleMemorySize)(&fa);
    if (nm >= 2 && m && g_pre_mem == (const void *)m) {
        g_s14_state = 2;
        STAGE("shave14: 2nd generated-frame buffer at %p (phys=0x%lx, DirectMemory, display index %d), reserve 0x%zx, "
              "flex_av=0x%zx (before the arena tail 0x%zx)", (const void *)(m + one), g_pre_mem_phys + (long)one, idx2,
              g_pre_size, fa, g_s14_flex_before);
        lg("shave14: 2 generated-frame buffers of 0x%zx in DirectMemory (2nd at %p, index %d), arena tail +0x%zx, "
           "flex_av 0x%zx -> 0x%zx\n", one, (const void *)(m + one), idx2, g_s14_tail_extra, g_s14_flex_before, fa);
    } else {
        g_s14_state = -1;
        STAGE("shave14: %d display buffer(s) of 0x%zx from reserve 0x%zx -> one generated-frame buffer, as without shave14",
              nm, one, g_pre_size);
        lg("shave14: back to one generated-frame buffer (%d of 0x%zx from 0x%zx)\n", nm, one, g_pre_size);
    }
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
static volatile uint64_t g_vblank_t_us = 0;
static volatile uint64_t g_vblank_cnt = 0;
static volatile uint64_t g_pll_phi_us = 0;
static volatile uint64_t g_pll_vb_cnt = 0;
static volatile uint32_t g_pll_period_q8 = (16683u << 8) | 85u; // 16683.33 us (59.94 Hz) in Q24.8
static volatile uint64_t g_pll_last_real_vb_us = 0;
static volatile uint32_t g_pll_t_interp_q16 = 32768u;
static volatile uint32_t g_pll_t2_q16 = 43690u;   // phase of the 2nd generated frame of a 3-slot pair (below-30 regime)
static volatile uint32_t g_pair_slots = 2u;       // refreshes of the pair being submitted: 2, or 3 in the below-30 regime
static uint32_t b30_win_frames, b30_win_on, b30_win_on_vb, b30_win_three, b30_win_cut;   // BELOW30 log line, per HOOK window
static Below30 g_b30;                             // below-30 regime state (pace_policy.h), game thread only
// Below-30 regime cost (pace_policy.h mid_lru / b30x), with below30 on: the 1st generated frame goes into the buffer
// flipped longest ago, and the regime is fed own - our extra GPU time.
static MidLru g_mid_lru;
static B30Extra g_b30x;
static uint32_t g_ts_ticks_per_ms = 0;            // GPU timestamp clock, from the PASSES window (0 = not measured yet)
static uint8_t g_slot_three[4];                   // the pair of each DCB slot had 3 slots (read back 3 frames later)
static uint32_t b30c_win_f1_after3_us, b30c_win_n_after3, b30c_win_f1_us, b30c_win_n;   // B30COST line, per HOOK window
static volatile uint32_t g_pll_t_permille = 500u;
static volatile uint32_t g_pll_lock = 0;
static uint64_t g_last_submit_enter_t = 0;
static uint64_t g_vo_last_submit_enter_t = 0;
static uint32_t g_pll_shortened = 0;

// Update VBlank Phase-Locked Loop (PLL) estimator from (vs.count, vs.processTime):
// Tracks hardware refresh period T_vb (EWMA alpha=1/16 in Q24.8 us) and phase offset phi_vb (EWMA alpha=1/4)
// to filter out OS thread wakeup/polling jitter and predict exact upcoming VBlank edges t_vb(k) = phi_vb + k * T_vb.
static void vblank_pll_update(uint64_t vb_cnt, uint64_t vb_time_us)
{
    if (!vb_time_us) return;
    if (__atomic_exchange_n(&g_pll_lock, 1u, __ATOMIC_ACQUIRE)) return;
    uint64_t prev_phi = g_pll_phi_us;
    uint64_t prev_cnt = g_pll_vb_cnt;
    uint32_t period_q8 = g_pll_period_q8;
    if (!prev_phi || vb_cnt <= prev_cnt || (vb_cnt - prev_cnt) > 120ull || vb_time_us <= prev_phi) {
        if (vb_cnt != prev_cnt) {
            g_pll_phi_us = vb_time_us;
            g_pll_vb_cnt = vb_cnt;
        }
    } else {
        uint64_t dc = vb_cnt - prev_cnt;
        uint64_t dt_us = vb_time_us - prev_phi;
        uint64_t meas_q8 = (dt_us << 8) / dc;
        if (meas_q8 >= (10000ull << 8) && meas_q8 <= (25000ull << 8)) {
            uint64_t pred_us = prev_phi + ((dc * (uint64_t)period_q8 + 128ull) >> 8);
            int64_t err_us = (int64_t)vb_time_us - (int64_t)pred_us;
            if (err_us > -4000ll && err_us < 4000ll) {
                // Alpha-beta PLL filter: alpha = 1/8 for phase, beta = 1/1024 in Q8 for sub-us crystal period tracking
                g_pll_phi_us = (uint64_t)((int64_t)pred_us + (err_us >> 3));
                int64_t p_adj = err_us / (int64_t)(dc << 2);
                int64_t new_p = (int64_t)period_q8 + p_adj;
                int64_t nom_p = (int64_t)((16683u << 8) | 85u);
                if (new_p >= nom_p - 32 && new_p <= nom_p + 32) {
                    g_pll_period_q8 = (uint32_t)new_p;
                }
            } else {
                period_q8 = (uint32_t)(((uint64_t)period_q8 * 15ull + meas_q8 + 8ull) >> 4);
                g_pll_period_q8 = period_q8;
                g_pll_phi_us = vb_time_us;
            }
            g_pll_vb_cnt = vb_cnt;
        } else {
            g_pll_phi_us = vb_time_us;
            g_pll_vb_cnt = vb_cnt;
        }
    }
    __atomic_store_n(&g_pll_lock, 0u, __ATOMIC_RELEASE);
}
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
    g_gv_letters_base = g;
    if (g_cfg.game_vectors && g_cfg.framegen && !g_detected_60fps) g |= GVL_ON;
    else if (!(g_gv_letters_base & GVL_ON)) g &= ~GVL_ON;
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
        if (g_gv_letters_base & GVL_ON) c.game_vectors = 1;
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
    fprintf(f, "manual_mode = %d\n", c.manual_mode);
    if (c.manual_mode) {
        if (c.pace_order) fprintf(f, "pace_order = %d\n", c.pace_order);
        if (c.shave_mode) fprintf(f, "shave_mode = %d\n", c.shave_mode);
        if (c.reg_mode)   fprintf(f, "reg_mode = %d\n", c.reg_mode);
        if (c.post_mid)   fprintf(f, "post_mid = %d\n", c.post_mid);
    }
    if (g_tile_override >= 0) fprintf(f, "tile_idx = %d\n", (int)g_tile_override);
    fprintf(f, "hud_protect = %d\n", c.hud_protect);
    fprintf(f, "game_vectors = %d\n", c.game_vectors);
    fprintf(f, "fps_lock = %d\n", c.fps_lock);
    fprintf(f, "below30 = %d\n", c.below30);
    fprintf(f, "shallow_queue = %d\n", c.shallow_queue);
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
    int no_flip_logged = 0, inert_checked = 0, busy = 1;
    uint64_t io_t0 = sceKernelGetProcessTime(), cfg_t = io_t0, cpu_t = io_t0;
    for (;;) {
        sceKernelUsleep(cc_io_sleep_us(g_cc_iolazy, busy || g_cfg.game_vectors));   // 2 ms; iolazy 10 ms while idle
        g_cc_io_wakes++;
        busy = 0;
        if (g_lgq_r != g_lgq_w) { lg_drain(); busy = 1; }
        if (g_log) {   // FTRACE: 15 records per line, each at least 6 real frames old (the monitor has completed it)
            uint32_t w = __atomic_load_n(&g_ft_w, __ATOMIC_ACQUIRE);
            if (w - g_ft_r > FT_N - 8u) { g_ft_r = w - (FT_N - 8u); g_ft_prev_h = 0; }   // fell behind: skip, restart dh
            if (w - g_ft_r >= 6u + 15u) {
                uint32_t rec[15][3];
                for (uint32_t i = 0; i < 15u; i++)
                    for (int j = 0; j < 3; j++) rec[i][j] = __atomic_load_n(&g_ft[(g_ft_r + i) % FT_N][j], __ATOMIC_RELAXED);
                char fb[512];
                uint32_t nd = ft_format(fb, sizeof(fb), (const uint32_t (*)[3])rec, 15u, g_ft_prev_h);
                if (nd) { g_ft_prev_h = rec[nd - 1][0]; g_ft_r += nd; lg_file_only(fb); busy = 1; }
                else g_ft_r += 15u;
            }
        }
        bench_io_tick();
        if (__atomic_exchange_n(&g_cfg_save_req, 0, __ATOMIC_ACQ_REL)) cfg_save_game_ini();
        if (g_inject_on == 8) gv_io_tick();
        poll_n++;
        uint64_t io_now = sceKernelGetProcessTime();
        // Periodic jobs: by wake-up count as before (5000 x 2 ms, 250 x 2 ms), by elapsed time with iolazy.
        int inert_due = g_cc_iolazy ? (!inert_checked && io_now - io_t0 >= CC_IO_INERT_US) : (poll_n == 5000);
        int cfg_due = g_cc_iolazy ? (io_now - cfg_t >= CC_IO_CFG_US) : ((poll_n % 250) == 0);
        if (inert_due) inert_checked = 1;
        if (cfg_due) cfg_t = io_now;
        if (io_now - cpu_t >= 1000000ull) { cpu_t = io_now; __atomic_store_n(&g_cc_io_cpu_us, cc_cpu_us(CC_CLK_THREAD), __ATOMIC_RELEASE); }
        if (!no_flip_logged && inert_due && g_flips == 0 && g_vo_submit_flips == 0) {
            no_flip_logged = 1;
            snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO GNM FLIP");
            size_t dm2m = dm_avail(0x200000), dm4k = dm_avail(0x4000);
            uint32_t rw = __atomic_load_n(&g_api_ring_w, __ATOMIC_ACQUIRE);
            lg("inert: no flip hook (0 flips after 10 s; game does not call sceGnmSubmitAndFlipCommandBuffers, voSubmitFlips=%u, "
               "handle=%d mbuf=%d dm_free_2m=0x%zx dm_free_4k=0x%zx allocs=%u(fail=%u) maps=%u rel=%u batch=%u last_api='%s')\n",
               g_vo_submit_flips, g_handle, g_mbuf_state, dm2m, dm4k,
               g_dbg_allocs, g_dbg_alloc_fail, g_dbg_maps, g_dbg_releases, g_dbg_batch_maps,
               rw ? g_api_ring[(rw - 1u) % API_RING_N] : "none");
            STAGE("inert after 10 s: flips=0 voFlips=%u handle=%d mbuf=%d dm_free_2m=0x%zx dm_free_4k=0x%zx allocs=%u(fail=%u) maps=%u rel=%u batch=%u last_api='%s'",
                  g_vo_submit_flips, g_handle, g_mbuf_state, dm2m, dm4k,
                  g_dbg_allocs, g_dbg_alloc_fail, g_dbg_maps, g_dbg_releases, g_dbg_batch_maps,
                  rw ? g_api_ring[(rw - 1u) % API_RING_N] : "none");
        }
        if (cfg_due && g_inject_on == 8 && !g_bench_enabled) {
            save_adapt_ini();
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
    uint32_t ft_mid_nv = 0, ft_real_rec = 0;   // FTRACE: hold of the last generated frame, record of the real on screen
    for (;;) {
        int woke_vb = cc_mon_wait();   // 500 us sleep as before, or with monev the next vblank / flip event
        g_cc_mon_wakes++;
        if (g_handle < 0) continue;
        memset(&fs, 0, sizeof(fs));
        memset(&vs, 0, sizeof(vs));
        if (read_flip_status(g_handle, &fs) != 0) continue;
        if (sceVideoOutGetVblankStatus(g_handle, &vs) == 0 && vs.processTime) {
            __atomic_store_n(&g_vblank_t_us, vs.processTime, __ATOMIC_RELEASE);
            __atomic_store_n(&g_vblank_cnt, vs.count, __ATOMIC_RELEASE);
            vblank_pll_update(vs.count, vs.processTime);
            if (woke_vb) cc_mon_vblank_latency(vs.processTime);
        }
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
                    if (IS_MID(last_buf)) ft_mid_nv += nv;   // both generated frames of a 3-slot pair
                    else if (ft_real_rec && g_log && __atomic_load_n(&g_ft_w, __ATOMIC_ACQUIRE) - (ft_real_rec - 1u) < FT_N) {
                        uint32_t *fr = &g_ft[(ft_real_rec - 1u) % FT_N][2];
                        __atomic_store_n(fr, ft_with_real_vb(__atomic_load_n(fr, __ATOMIC_RELAXED), nv), __ATOMIC_RELAXED);
                        ft_real_rec = 0;
                    }
                    if (!IS_MID(last_buf) && g_mon) {
                        static uint32_t ema_dt = 16683u;
                        uint32_t dtu = dt > 66732u ? 66732u : (uint32_t)dt;
                        __atomic_store_n(&g_last_real_hold_us, dtu, __ATOMIC_RELEASE);
                        ema_dt = (ema_dt * 3u + dtu) >> 2;
                        uint32_t pll_t = __atomic_load_n(&g_pll_t_interp_q16, __ATOMIC_ACQUIRE);
                        uint32_t t_q16 = pll_t ? pll_t : ((ema_dt << 16) / (ema_dt + 16683u));
                        if (t_q16 < 21845u) t_q16 = 21845u;
                        if (t_q16 > 43691u) t_q16 = 43691u;
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
                    if (IS_MID(fs.currentBuffer) && last_buf >= 0 && (!IS_MID(last_buf) || fs.currentBuffer != last_buf)) g_mids_shown++;
                }
            }
            if (fs.currentBuffer >= 0 && !IS_MID(fs.currentBuffer) && fs.currentBuffer < 16 && fs.currentBuffer != last_buf) {
                uint64_t t0 = __atomic_exchange_n(&g_sub_t[fs.currentBuffer], 0ull, __ATOMIC_ACQ_REL);
                if (t0 && fs.processTime > t0 && fs.processTime - t0 < 500000ull) {
                    uint64_t l = fs.processTime - t0; g_lat_sum += l; g_lat_n++; if (l > g_lat_max) g_lat_max = l;
                }
                uint32_t fs_slot = __atomic_exchange_n(&g_ft_slot[fs.currentBuffer], 0u, __ATOMIC_ACQ_REL);
                ft_real_rec = 0;
                if (fs_slot && g_log && __atomic_load_n(&g_ft_w, __ATOMIC_ACQUIRE) - (fs_slot - 1u) < FT_N) {
                    uint32_t *fr = &g_ft[(fs_slot - 1u) % FT_N][2];
                    uint32_t fv = __atomic_load_n(fr, __ATOMIC_RELAXED);
                    uint64_t h0 = __atomic_load_n(&g_ft[(fs_slot - 1u) % FT_N][0], __ATOMIC_RELAXED);
                    uint64_t dscr = (uint32_t)fs.processTime - (uint32_t)h0;   // both are process time in us (low 32 bits)
                    fv = ft_with_mid_vb(ft_with_screen(fv, dscr), ft_mid_nv);
                    __atomic_store_n(fr, fv, __ATOMIC_RELAXED);
                    ft_real_rec = fs_slot;
                }
                ft_mid_nv = 0;
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
            {
                uint32_t wq8 = vb_period_q8_from_window(vs.count - win_vb, vs.processTime - win_t);
                if (wq8) __atomic_store_n(&g_vb_win_q8, wq8, __ATOMIC_RELEASE);
            }
            uint32_t cn_ = __atomic_exchange_n(&g_cpu_n, 0u, __ATOMIC_ACQ_REL);
            uint32_t csum_ = __atomic_exchange_n(&g_cpu_sum_us, 0u, __ATOMIC_ACQ_REL);
            uint32_t cmax_ = __atomic_exchange_n(&g_cpu_max_us, 0u, __ATOMIC_ACQ_REL);
            lgd("LATENCY real frame hand-over -> on screen: avg %.1f max %.1f ms (%llu frames) | FG %s preset %d | pending max %d\n",
               g_lat_n ? (double)g_lat_sum / 1000.0 / (double)g_lat_n : 0.0, (double)g_lat_max / 1000.0,
               (unsigned long long)g_lat_n, (g_cfg.framegen && !g_fg_off && g_fmt_supported && g_mbuf_state == 1 && !g_inert_reason[0]) ? "on" : "off", g_cfg.preset, pend_max);
            g_lat_sum = g_lat_n = g_lat_max = 0;
            lgd("CADENCE (vblanks on screen) mid 1:%u 2:%u 3:%u 4+:%u | real 1:%u 2:%u 3:%u 4+:%u | refresh %.3f Hz | "
               "pending max %d gcq max %d | missed %u (gaps %u) | pace timeouts %u resync %u fix %u | game cpu release->submit avg %.1f max %.1f ms\n",
               g_hist[0][0], g_hist[0][1], g_hist[0][2], g_hist[0][3], g_hist[1][0], g_hist[1][1], g_hist[1][2], g_hist[1][3],
               hz, pend_max, gcq_max, g_mon_missed, g_mon_gaps, g_pace_timeouts, g_pace_resync, g_pace_fix,
               cn_ ? csum_ / 1000.0 / cn_ : 0.0, cmax_ / 1000.0);
            memset(g_hist, 0, sizeof(g_hist));
            // Keep the CPU measurement visible in the 200-byte flight-recorder rows.
            if (tb_title()) {
                lgd("CPU release->submit avg %.1f max %.1f ms | test=%d FG=%d pcopy=%d preset=%d\n",
                   cn_ ? csum_ / 1000.0 / cn_ : 0.0, cmax_ / 1000.0,
                   VCFG_2BUF_TEST, !g_fg_off, g_pcopy_on, g_cfg.preset);
                tb_report();
            }
            vq_report();
            cc_window_log(vs.processTime - win_t);
            {
                static uint32_t stall_last_flips = 0, stall_win_cnt = 0;
                uint32_t cur_flips = g_flips + g_vo_submit_flips;
                if (cur_flips > 0 && cur_flips == stall_last_flips) {
                    stall_win_cnt++;
                    if (stall_win_cnt == 2 || stall_win_cnt == 5) {
                        size_t dm2m = dm_avail(0x200000), dm4k = dm_avail(0x4000);
                        uint32_t rw = __atomic_load_n(&g_api_ring_w, __ATOMIC_ACQUIRE);
                        lg("STALL SNAPSHOT (%us no flip at flips=%u vo=%u): curBuf=%d pend=%d gcq=%d | dm_free_2m=0x%zx dm_free_4k=0x%zx | allocs=%u(fail=%u) maps=%u rel=%u batch=%u | last_api='%s'\n",
                           stall_win_cnt * 2u, g_flips, g_vo_submit_flips, fs.currentBuffer, fs.flipPendingNum, fs.gcQueueNum,
                           dm2m, dm4k, g_dbg_allocs, g_dbg_alloc_fail, g_dbg_maps, g_dbg_releases, g_dbg_batch_maps,
                           rw ? g_api_ring[(rw - 1u) % API_RING_N] : "none");
                        STAGE("STALL SNAPSHOT (%us no flip at flips=%u vo=%u): curBuf=%d pend=%d gcq=%d dm_free_2m=0x%zx dm_free_4k=0x%zx allocs=%u(fail=%u) maps=%u rel=%u batch=%u last_api='%s'",
                              stall_win_cnt * 2u, g_flips, g_vo_submit_flips, fs.currentBuffer, fs.flipPendingNum, fs.gcQueueNum,
                              dm2m, dm4k, g_dbg_allocs, g_dbg_alloc_fail, g_dbg_maps, g_dbg_releases, g_dbg_batch_maps,
                              rw ? g_api_ring[(rw - 1u) % API_RING_N] : "none");
                    }
                } else {
                    if (stall_win_cnt >= 2) {
                        lg("STALL RECOVERED after %us (flips=%u)\n", stall_win_cnt * 2u, cur_flips);
                        STAGE("STALL RECOVERED after %us (flips=%u)", stall_win_cnt * 2u, cur_flips);
                    }
                    stall_win_cnt = 0;
                    stall_last_flips = cur_flips;
                }
            }
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
    cc_read_cuts();
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

#define PLL_LEAD_US  2200u
#define PLL_LATCH_US 1550u

static uint32_t vblank_period_us(void)
{
    uint32_t p_q8 = __atomic_load_n(&g_pll_period_q8, __ATOMIC_ACQUIRE);
    if (p_q8 >= (10000u << 8) && p_q8 <= (25000u << 8)) {
        return (p_q8 + 128u) >> 8;
    }
    uint32_t vb_us = (refresh_period_q8() + 128u) >> 8;
    return (vb_us >= 10000u && vb_us <= 25000u) ? vb_us : 16683u;
}

// Compute microseconds remaining until the upcoming vblank edge using the VBlank PLL predictor
// t_vb(k) = phi_vb + k * T_vb (with direct sceVideoOutGetVblankStatus fallback if mon_thread has not sampled within 2 vblanks).
static uint32_t vblank_to_next_edge_us(int32_t handle, uint64_t now_us, uint32_t *out_vb_us)
{
    uint32_t vb_us = vblank_period_us();
    if (out_vb_us) *out_vb_us = vb_us;
    uint64_t vt = __atomic_load_n(&g_vblank_t_us, __ATOMIC_ACQUIRE);
    if ((!vt || now_us > vt + (uint64_t)vb_us * 2ull) && handle >= 0) {
        VoVblankStatus vs;
        memset(&vs, 0, sizeof(vs));
        if (sceVideoOutGetVblankStatus(handle, &vs) == 0 && vs.processTime) {
            vt = vs.processTime;
            __atomic_store_n(&g_vblank_t_us, vt, __ATOMIC_RELEASE);
            __atomic_store_n(&g_vblank_cnt, vs.count, __ATOMIC_RELEASE);
            vblank_pll_update(vs.count, vt);
        }
    }
    uint64_t phi = __atomic_load_n(&g_pll_phi_us, __ATOMIC_ACQUIRE);
    uint32_t p_q8 = __atomic_load_n(&g_pll_period_q8, __ATOMIC_ACQUIRE);
    if (!phi) phi = vt;
    if (p_q8 < (10000u << 8) || p_q8 > (25000u << 8)) p_q8 = vb_us << 8;
    if (!phi) return vb_us >> 1;
    if (now_us <= phi) {
        uint64_t diff = phi - now_us;
        return (diff > 0 && diff <= (uint64_t)vb_us) ? (uint32_t)diff : (vb_us >> 1);
    }
    uint64_t elapsed_q8 = (now_us - phi) << 8;
    uint64_t k = (elapsed_q8 / (uint64_t)p_q8) + 1ull;
    uint64_t next_edge_us = phi + ((k * (uint64_t)p_q8 + 128ull) >> 8);
    if (next_edge_us <= now_us) {
        k++;
        next_edge_us = phi + ((k * (uint64_t)p_q8 + 128ull) >> 8);
    }
    uint32_t rem_us = (uint32_t)(next_edge_us - now_us);
    return rem_us ? rem_us : vb_us;
}

// Predict the true display-time interpolation fraction t_interp = clamp((V_M - V_P) / (V_C - V_P), 0.333, 0.667)
// based on the PLL-predicted VBlank slots for previous real frame P, mid-frame M, and current real frame C.
static uint32_t pll_predict_t_interp_q16(int32_t handle, uint64_t now_us, uint64_t dt_enter_us, uint32_t hold_mid_us)
{
    uint32_t vb_us = 16683u;
    uint32_t to_next_vb_us = vblank_to_next_edge_us(handle, now_us, &vb_us);
    uint32_t to_mid_vb_us = (to_next_vb_us >= PLL_LATCH_US) ? to_next_vb_us : (to_next_vb_us + vb_us);
    uint64_t v_m_us = now_us + (uint64_t)to_mid_vb_us;
    uint64_t v_p_us = __atomic_load_n(&g_pll_last_real_vb_us, __ATOMIC_ACQUIRE);
    if (v_p_us > 0 && v_m_us < v_p_us + (uint64_t)vb_us) {
        v_m_us = v_p_us + (uint64_t)vb_us;
    }

    uint32_t extra_mid_vb = (hold_mid_us > (vb_us >> 1)) ? ((hold_mid_us + (vb_us >> 1)) / vb_us) : 0u;
    uint32_t vb_m_to_c = (__atomic_load_n(&g_pair_slots, __ATOMIC_ACQUIRE) == 3u ? 2u : 1u) + extra_mid_vb;

    uint32_t vb_p_to_m = 1u;
    if (v_p_us > 0 && v_m_us > v_p_us && (v_m_us - v_p_us) <= (uint64_t)vb_us * 6ull) {
        vb_p_to_m = (uint32_t)(((v_m_us - v_p_us) + (vb_us >> 1)) / vb_us);
        if (vb_p_to_m < 1u) vb_p_to_m = 1u;
    } else {
        uint64_t dt_ref = (dt_enter_us >= (uint64_t)vb_us && dt_enter_us <= (uint64_t)vb_us * 6ull)
                          ? dt_enter_us
                          : (uint64_t)(g_ema_frame_us > 20000u ? g_ema_frame_us : (vb_us * 2u));
        uint32_t total_vb = (uint32_t)((dt_ref + (vb_us >> 1)) / vb_us);
        if (total_vb < 2u) total_vb = 2u;
        if (total_vb > 5u) total_vb = 5u;
        vb_p_to_m = (total_vb > vb_m_to_c) ? (total_vb - vb_m_to_c) : 1u;
    }

    uint32_t vb_p_to_c = vb_p_to_m + vb_m_to_c;
    uint32_t t_q16 = 32768u, t2_q16 = 43690u;
    pll_slot_phases_q16(vb_p_to_m, vb_m_to_c, &t_q16, &t2_q16);
    __atomic_store_n(&g_pll_t2_q16, t2_q16, __ATOMIC_RELEASE);

    uint32_t t_permille = (vb_p_to_m * 1000u + (vb_p_to_c >> 1)) / vb_p_to_c;
    if (t_permille < 333u) t_permille = 333u;
    if (t_permille > 667u) t_permille = 667u;

    __atomic_store_n(&g_pll_t_interp_q16, t_q16, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pll_t_permille, t_permille, __ATOMIC_RELEASE);
    return t_q16;
}

static void pll_commit_frame_vblank(int32_t handle, uint64_t submit_us, uint32_t hold_mid_us)
{
    uint32_t vb_us = 16683u;
    uint32_t to_next_vb_us = vblank_to_next_edge_us(handle, submit_us, &vb_us);
    uint32_t to_mid_vb_us = (to_next_vb_us >= PLL_LATCH_US) ? to_next_vb_us : (to_next_vb_us + vb_us);
    uint64_t v_m_us = submit_us + (uint64_t)to_mid_vb_us;
    uint64_t prev_vp = __atomic_load_n(&g_pll_last_real_vb_us, __ATOMIC_ACQUIRE);
    if (prev_vp > 0 && v_m_us < prev_vp + (uint64_t)vb_us) {
        v_m_us = prev_vp + (uint64_t)vb_us;
    }
    uint32_t extra_mid_vb = (hold_mid_us > (vb_us >> 1)) ? ((hold_mid_us + (vb_us >> 1)) / vb_us) : 0u;
    uint32_t vb_m_to_c = (__atomic_load_n(&g_pair_slots, __ATOMIC_ACQUIRE) == 3u ? 2u : 1u) + extra_mid_vb;
    uint64_t v_c_us = v_m_us + (uint64_t)vb_m_to_c * (uint64_t)vb_us;
    __atomic_store_n(&g_pll_last_real_vb_us, v_c_us, __ATOMIC_RELEASE);
}

// Vblank-Phase-Aware Adaptive Hold (PRE-SUBMIT PLL):
// Uses the vblank PLL estimator (t_vb(k) = phi_vb + k * T_vb) and elapsed CPU frame interval
// (now - last_submit_enter_t) so that when a game frame arrives late in the 2-vblank window
// (e.g. 23..28 fps, 36..43 ms frame time), the pre-submit sleep automatically shortens to target
// the upcoming vblank edge with PLL_LEAD_US safety margin (guaranteeing mid is latched on the
// target vblank without slipping across the edge).
static uint32_t pll_adaptive_hold_us(int32_t handle, uint64_t now_us, uint64_t dt_enter_us, uint32_t req_hold_us)
{
    uint32_t vb_us = 16683u;
    uint32_t to_next_vb_us = vblank_to_next_edge_us(handle, now_us, &vb_us);
    uint32_t win_2vb_us = vb_us * 2u;
    uint32_t target_edge_us = (dt_enter_us > 0 && (dt_enter_us < (uint64_t)vb_us || to_next_vb_us < PLL_LEAD_US))
                              ? (to_next_vb_us + vb_us)
                              : to_next_vb_us;
    uint32_t vb_budget_us = (target_edge_us > PLL_LEAD_US) ? (target_edge_us - PLL_LEAD_US) : 0u;

    uint32_t rem_2vb_us = 0u;
    if (dt_enter_us > 0 && dt_enter_us < (uint64_t)win_2vb_us) {
        rem_2vb_us = (uint32_t)((uint64_t)win_2vb_us - dt_enter_us);
    }

    uint32_t hold_us = req_hold_us;
    if (hold_us > rem_2vb_us) hold_us = rem_2vb_us;
    if (hold_us > vb_budget_us) hold_us = vb_budget_us;
    if (to_next_vb_us > hold_us) {
        uint32_t rem_after = to_next_vb_us - hold_us;
        if (rem_after >= (PLL_LATCH_US - 200u) && rem_after <= (PLL_LATCH_US + 200u)) {
            uint32_t nudge_us = rem_after - (PLL_LATCH_US - 250u);
            if (hold_us + nudge_us <= req_hold_us) hold_us += nudge_us;
        }
    }
    if (hold_us < req_hold_us) g_pll_shortened++;
    return hold_us;
}

static uint64_t g_dbl_frames = 0;
static int64_t g_pace_off = 0;
// Present-copy state as the pacers see it. With two_buffer or present_copy forced, the history buffer makes
// g_pcopy_avail 1 (on_register_buffers_2buf), so g_pcopy_on = g_pcopy_avail && present_copy != 2 picks timer pacing,
// post-submit release and the deeper drain threshold for that title. shave14 withholds that history buffer
// (g_s14_tail_extra, alloc_pbuf_history) and takes g_pcopy_on to 0 with it; the pacers keep the decision the title has
// without shave14. Rendering still follows g_pcopy_on (no history buffer to copy into).
static int pace_pcopy(void)
{
    return g_pcopy_on || (g_s14_tail_extra && g_cfg.present_copy != 2);
}
static void pace_vblank_ex(int prev2_real, int prev_real, int cur_real, int32_t handle, uint64_t dt_enter_us, int pace_pre)
{
    if (!g_mon_on) { pace_30(); return; }
    g_dbl_frames++;
    volatile uint64_t *cnt = g_opt_early ? &g_mids_shown : &g_reals_shown;
    // Present copy (2-buffer games): release the game when the frame before the previous one is on screen, so its CPU
    // work overlaps the GPU of the previous frame (waiting for the previous one serialised them: 26 + 26 ms, 24 fps).
    int64_t step = pace_pcopy() ? 2 : 1;
    int64_t target = (int64_t)g_dbl_frames - step + g_pace_off;
    int64_t have = (int64_t)*cnt;
    if (have > target) {
        g_pace_off = have - ((int64_t)g_dbl_frames - step);
        g_pace_resync++;
        g_rel_t = sceKernelGetProcessTime();
        return;
    }
    uint64_t t0 = sceKernelGetProcessTime();
    uint64_t max_wait_us = 60000ull;
    if (pace_pre && dt_enter_us > 0 && cc_pending_flips() <= 1u) {
        max_wait_us = pll_adaptive_hold_us(handle, t0, dt_enter_us, 60000u);
    }
    while ((int64_t)*cnt < target) {
        uint64_t el = sceKernelGetProcessTime() - t0;
        if (el >= max_wait_us && cc_pending_flips() <= 1u) {
            g_pace_off = (int64_t)*cnt - ((int64_t)g_dbl_frames - step);
            break;
        }
        if (el > 60000ull) {
            g_pace_off = (int64_t)*cnt - ((int64_t)g_dbl_frames - step);
            g_pace_timeouts++;
            break;
        }
        sceKernelUsleep(200);
    }
    int distinct_ok = (g_ngame == 2) ? (prev_real >= 0 && prev_real != cur_real)
                                     : (prev2_real != cur_real && prev_real != cur_real && prev2_real != prev_real);
    if (distinct_ok && pace_pcopy()) {
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
static void pace_vblank(int prev2_real, int prev_real, int cur_real)
{
    pace_vblank_ex(prev2_real, prev_real, cur_real, g_handle, 0ull, 0);
}

// VCFG timer pacing for FG pairs, per title (LOG_DIR/pace_timer_<TITLE> exists): releases the game once per pair
// period (2 refreshes at the measured period, exact to ~0.01 us: the rounded 59.9 Hz drifted 22 us per pair and made
// the latency a 1.3 ms / 2 s sawtooth) from the previous release, instead of waiting until its previous real
// frame is on screen (pace_vblank). CUSA05571 needs 25 ms per frame but waited ~13 ms more in pace_vblank behind a
// 4-deep flip queue and ran at 24-25 fps. +2 ms on the next release while 3+ flips are pending (drift).
static int g_pace_timer = -1;
static uint64_t g_pair_next;
static void pace_pair_ex(int32_t handle, uint64_t dt_enter_us, int pace_pre, uint32_t nvb)
{
    if (!g_freq) return;
    if (nvb < 2u || nvb > 3u) nvb = 2u;
    uint64_t period = vb_ticks(g_freq, refresh_period_q8(), nvb);   // nvb refreshes (2, or 3 for a 3-slot pair) at the measured period
    uint64_t now = sceKernelGetProcessTimeCounter();
    if (!g_pair_next || now > g_pair_next + period * 3) { g_pair_next = now + period; g_rel_t = sceKernelGetProcessTime(); return; }
    if (now < g_pair_next) {
        uint64_t us = (g_pair_next - now) * 1000000ull / g_freq;
        if (pace_pre && dt_enter_us > 0) {
            uint32_t pll_us = pll_adaptive_hold_us(handle, sceKernelGetProcessTime(), dt_enter_us, (uint32_t)us);
            if (pll_us < us) {
                us = pll_us;
                g_pair_next = now + (uint64_t)us * g_freq / 1000000ull;
            }
        }
        cc_wait_until(g_pair_next);
        now = sceKernelGetProcessTimeCounter();
    }
    g_pair_next += period;
    if (g_pair_next <= now) {
        if (pace_pre) {
            uint32_t vb_us = 16683u;
            uint32_t to_next_vb_us = vblank_to_next_edge_us(handle, sceKernelGetProcessTime(), &vb_us);
            uint32_t align_us = (to_next_vb_us > PLL_LEAD_US) ? (to_next_vb_us - PLL_LEAD_US + vb_us) : vb_us;
            g_pair_next = now + (uint64_t)align_us * g_freq / 1000000ull;
        } else {
            g_pair_next = now + period;
        }
    }
    uint32_t drain_thr = pace_pcopy() ? 5u : (pace_pre ? 3u : 4u);
    if (cc_pending_flips() >= drain_thr) g_pair_next += g_freq / 500u;   // drain: +2 ms
    g_rel_t = sceKernelGetProcessTime();
}
static void pace_pair(void) { pace_pair_ex(g_handle, 0ull, 0, 2u); }
static int pace_timer_on(void)
{
    if (g_cfg.pace_order == 3) return 1;
    if (g_cfg.pace_order == 0 &&
        (pace_pcopy() ||
         vcfg_in_family(g_title_id, "CUSA05571") ||
         vcfg_in_family(g_title_id, "CUSA09311") ||
         vcfg_in_family(g_title_id, "CUSA18534") ||
         vcfg_in_family(g_title_id, "CUSA10123"))) {
        if (g_pace_timer < 0) {
            g_pace_timer = 1;
            lg("pacing: timer (auto for pcopy/%s)%s\n", g_title_id,
               (!g_pcopy_on && g_s14_tail_extra) ? " (shave14: history buffer withheld, present-copy pacing kept)" : "");
        }
        return 1;
    }
    if (g_pace_timer < 0) {
        char p[96]; snprintf(p, sizeof(p), LOG_DIR "/pace_timer_%s", g_title_id);
        FILE *f = fopen(p, "r"); g_pace_timer = f != NULL; if (f) fclose(f);
        lg("pacing: %s (%s)\n", g_pace_timer ? "timer" : "vblank", p);
    }
    return g_pace_timer;
}

// The below-30 regime's buffer order and feed state, set up once on the game thread (never in plugin_load).
static void b30cost_init(void)
{
    static int done;
    if (done) return;
    done = 1;
    mid_lru_reset(&g_mid_lru, (uint32_t)g_nmid);
    b30x_reset(&g_b30x);
}
// Own time the below-30 regime and its accumulator read: without the GPU time the regime itself adds.
static uint32_t b30_feed(uint64_t own_us)
{
    uint32_t o = own_us < 1000000ull ? (uint32_t)own_us : 0u;
    return b30_feed_us(o, g_b30x.ema_us);
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
        // 1.4: Auto-detect 60 fps games during warmup (flips 60..120) before FG pacing begins.
        // Ignore uncapped loading spin-loops (> 75 fps, e.g. CUSA15090 at 754 fps) and games that explicitly set flipRate >= 1.
        if (n >= 60 && n <= INJECT_AFTER_FLIPS && sm_rfps >= 480u && sm_rfps <= 750u && !g_detected_60fps && g_game_flip_rate <= 0) {
            g_detected_60fps = 1;
            snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
            lg("auto-detect: game runs at %.1f fps (48..75 fps) -> FrameGen auto-disabled (60 Hz output limit)\n", rfps);
        }
        {   // Track frame rate class transitions (e.g. 60 fps menu/splash -> 30 fps gameplay) and auto-re-enable FG.
            static int rate_class;
            static int low_fps_wins;
            int cls = (sm_rfps >= 480u && sm_rfps <= 750u) ? 60 : (sm_rfps >= 120u && sm_rfps < 400u ? 30 : 0);
            if (cls == 30) low_fps_wins++;
            else if (cls == 60) low_fps_wins = 0;
            if (n >= 30 && cls && cls != rate_class) {
                if (rate_class) lg("rate change: %d -> %d fps class at flip %u (%.1f fps, detected_60=%d)\n", rate_class, cls, n, rfps, g_detected_60fps);
                rate_class = cls;
            }
            static int lock60_logged;
            if (g_detected_60fps && n > INJECT_AFTER_FLIPS && low_fps_wins >= 2) {
                if (fg_auto_reenable_allowed(g_cfg.fps_lock)) {
                    g_detected_60fps = 0;
                    g_inert_reason[0] = 0;
                    if (g_known_game && !g_menu_open) { g_cfg.framegen = 1; g_fg_off = 0; }
                    g_last_disk_hash = 0;
                    gv_start(g_inject_on == 8 && fg_res_supported(g_bw, g_bh));
                    lg("auto-detect: game dropped to %.1f fps (30 fps class) -> FrameGen auto-enabled\n", rfps);
                } else if (!lock60_logged) {   // fps_lock = 60: the player wants the game free, without frame generation
                    lock60_logged = 1;
                    lg("auto-detect: game dropped to %.1f fps (30 fps class), fps_lock=60 -> FrameGen stays off, game free up to 60 fps\n", rfps);
                }
            }
        }
        g_fps_win_t = now;
        g_fps_win_n = 0;
    }

    if (n <= 30)
        lg("gnm #%u t=%.3fs dt=%.2fms idx=%u mode=%u\n", n, (now - g_t0) / (double)g_freq, dt, index, mode);
    if (now - g_win_t >= g_freq * 2) {
        double secs = (now - g_win_t) / (double)g_freq;
        lgd("STAT gnm: %.1f flips/s, max dt %.1f ms (window %.1fs)\n", g_win_n / secs, g_win_max, secs);
        if (!g_mon_on) cc_fgoff_log();
        g_win_t = now; g_win_n = 0; g_win_max = 0.0;
    }
}

static int dcb_mem_ok(uintptr_t a, size_t n)
{
    if (!a || !n || a + n < a) return 0;
    uintptr_t end = a + n, cur = a;
    while (cur < end) {
        OrbisKernelVirtualQueryInfo vq;
        memset(&vq, 0, sizeof(vq));
        if (sceKernelVirtualQuery((const void *)cur, 0, &vq, sizeof(vq)) == 0 &&
            (uintptr_t)vq.unk01 <= cur && (uintptr_t)vq.unk02 > cur) {
            if (!vq.isCommitted) return 0;
            if ((vq.unk04 & 0x01 /*PROT_CPU_READ*/) == 0) return 0;
            cur = (uintptr_t)vq.unk02;
            continue;
        }
        void *s = NULL, *en = NULL;
        int32_t prot = 0;
        if (sceKernelQueryMemoryProtection((void *)cur, &s, &en, &prot) != 0) return 0;
        if ((uintptr_t)s > cur || (uintptr_t)en <= cur) return 0;
        if ((prot & 0x01 /*PROT_CPU_READ*/) == 0) return 0;
        cur = (uintptr_t)en;
    }
    return 1;
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
        char cpath[64];
        snprintf(cpath, sizeof(cpath), LOG_DIR "/crash-%s.txt", g_title_id);
        sceKernelUnlink(cpath);
        STAGE("safe mode: the early start reached frame %d (%s) -> marker & stale crash removed", INJECT_AFTER_FLIPS,
              from_vout ? "VideoOutSubmitFlip" : "SubmitAndFlip");
    }
    if (g_ngame < 2 && g_game_idx_hi >= 2 && g_game_idx_hi <= 8) g_ngame = g_game_idx_hi;
    if (g_safe && g_safe_never) { g_safe_done = 1; snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: SAFE GIVEN UP"); }
    if (g_safe && !g_safe_done) {
        if (g_inject_on == 8) safe_try_activate(handle);
        else safe_refuse("MODE");
    } else if (!g_safe && !g_safe_done && g_mbuf_state != 1 && g_inject_on == 8 && g_ngame >= 2) {
        safe_try_activate(handle);
    }
    memcpy(safe_reason, g_inert_reason, sizeof(safe_reason));
    if (g_safe && !g_safe_active) g_inject_on = 0;   // unverified title that was refused: observe only
    if (!g_fmt_supported) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: HDR/FMT 0x%x", g_attr.format);
    else if (!fg_res_supported(g_bw, g_bh)) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: RES %ux%u", g_bw, g_bh);
    else if (!mbufs_ready(handle)) snprintf(g_inert_reason, sizeof(g_inert_reason), "INERT: NO MBUF (%d)", g_nbuf);
    else if (g_detected_60fps && g_fg_off) snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
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
    gv_start(g_inject_on == 8 && fg_res_supported(g_bw, g_bh) && !g_detected_60fps);
    if (from_vout) g_vout_fg_inited = 1;
    save_adapt_ini();
}

static int32_t gnm_submit_and_flip_raw(int has_wl, uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz,
                                       void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    if (has_wl) {
        return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffersForWorkload,
                             int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                             workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    }
    return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                         int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                         count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
}

static int32_t flip_inner_ex(int has_wl, uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    uint64_t t_hook_in = sceKernelGetProcessTime();
    uint64_t dt_enter_us = g_last_submit_enter_t ? (t_hook_in - g_last_submit_enter_t) : 33366ull;
    if (index < 16u) __atomic_store_n(&g_sub_t[index], t_hook_in, __ATOMIC_RELEASE);
    uint64_t prev_flip_t = g_last;
    g_flips++;
    if (g_flips == 1) {
        lg("first SubmitAndFlip%s: count=%u flipMode=%u title=%s\n", has_wl ? "ForWorkload" : "", count, flipMode, g_title_id);
        lg("CLASSIC_BUILD version=%s test=%d target=%d two_buffer=%d status_hook=%d\n", VCFG_TRACE_VERSION, VCFG_2BUF_TEST, tb_title(),
           g_vq_on, g_status_hooked);
    }
    uint64_t ft_own_us = 0;
    if (g_cc_off_rel) {   // framegen=0, debug log: kept apart from g_cpu_* (the pacers' own-time average)
        uint64_t us = sceKernelGetProcessTime() - g_cc_off_rel;
        g_cc_off_rel = 0;
        if (us < 1000000) { g_cc_off_n++; g_cc_off_sum_us += (uint32_t)us; if (us > g_cc_off_max_us) g_cc_off_max_us = (uint32_t)us; }
    }
    if (g_rel_t) {
        uint64_t us = sceKernelGetProcessTime() - g_rel_t;
        ft_own_us = us;
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

    if (on_sub_hibernation_gate(g_flips)) {
        g_prev2_idx = g_prev_idx;
        g_prev_idx = index;
        g_last_submit_enter_t = sceKernelGetProcessTime();
        return gnm_submit_and_flip_raw(has_wl, workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    }

    if (g_inject_on && g_flips >= INJECT_AFTER_FLIPS && count >= 1 && count <= (GV_MAXE - 1u) && dcb && dcbSz && index < (uint32_t)g_nbuf) {
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
        if (strcmp(g_title_id, "CUSA10249") == 0 && (g_flips == INJECT_AFTER_FLIPS || g_flips == 600u))
            a10249_log_budget(g_flips == INJECT_AFTER_FLIPS ? "f:120" : "f:600");
        if (dcb_valid && g_gpu_state == 1) {
            uint32_t last = count - 1;
            uint32_t nd = dcbSz[last] / 4;
            uint32_t tail_dw = 0;
            const uint32_t *tail = NULL;
            if (nd >= 64) {
                const uint32_t *t64 = (const uint32_t *)dcb[last] + (nd - 64);
                // CUSA00411: the same 64-dword flip packet but its second dword is 0x68750778 instead of 0x68750777
                if (t64[0] == 0xC03E1000u && (t64[1] >> 8) == 0x687507u) {
                    tail = t64;
                    tail_dw = 64u;
                }
            }
            if (!tail && nd >= 16) {
                const uint32_t *dw = (const uint32_t *)dcb[last];
                uint32_t scan_s = (nd > 128u) ? (nd - 128u) : 0u;
                for (uint32_t ti = scan_s; ti + 2u <= nd; ti++) {
                    uint32_t h = dw[ti];
                    uint32_t pk_len = ((h >> 16) & 0x3FFFu) + 2u;
                    if ((h & 0xC000FF00u) == 0xC0001000u && (dw[ti + 1u] >> 8) == 0x687507u &&
                        ti + pk_len == nd && pk_len >= 16u && pk_len <= 128u) {
                        tail = dw + ti;
                        tail_dw = pk_len;
                        break;
                    }
                }
            }
            if (tail) {
                static const uint32_t k_nop64_pkt[64] = { 0 };
                const uint32_t *tail64 = (tail_dw >= 64u) ? tail : k_nop64_pkt;
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
                                if (t2[7] && t2[5] >= t2[4] && t2[7] >= t2[5] && t2[6] >= t2[7]) {
                                    g_pp_split[0] += t2[5] - t2[4];
                                    g_pp_split[1] += t2[7] - t2[5];
                                    g_pp_split[2] += t2[6] - t2[7];
                                    g_pp_split_n++;
                                    if (g_cfg.below30 && g_ts_ticks_per_ms)
                                        b30x_add(&g_b30x, b30x_ticks_us(ts[3] > ts[2] ? ts[3] - ts[2] : 0u, g_ts_ticks_per_ms),
                                                 b30x_ticks_us(t2[7] - t2[5], g_ts_ticks_per_ms), g_slot_three[ps]);
                                    if (g_cfg.below30) {   // B30COST: fence before the 1st generated frame, by the pair before
                                        uint32_t f1 = b30x_ticks_us(ts[3] > ts[2] ? ts[3] - ts[2] : 0u, g_ts_ticks_per_ms);
                                        b30c_win_f1_us += f1; b30c_win_n++;
                                        if (g_slot_three[(ps + 3u) & 3u]) { b30c_win_f1_after3_us += f1; b30c_win_n_after3++; }
                                    }
                                    if (t2[8] >= t2[5] && t2[7] >= t2[8]) {
                                        g_pp_split[3] += t2[8] - t2[5];
                                        g_pp_split[4] += t2[7] - t2[8];
                                        if (t2[9] >= t2[8] && t2[10] >= t2[9] && t2[7] >= t2[10]) {
                                            g_pp_g2[0] += t2[9] - t2[8];
                                            g_pp_g2[1] += t2[10] - t2[9];
                                            g_pp_g2[2] += t2[7] - t2[10];
                                            g_pp_g2_n++;
                                        }
                                    }
                                }
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
                            if (unit > 1e6 && unit < 1e10) g_ts_ticks_per_ms = (uint32_t)(unit / 1000.0 + 0.5);
                            double ms[4], tot = 0;
                            for (int i = 0; i < 4; i++) { ms[i] = (double)g_ts_pass[i] / (double)g_ts_n / unit * 1000.0; tot += ms[i]; }
                            double span = g_ts_span_n ? (double)g_ts_span / g_ts_span_n / unit * 1000.0 : 0.0;
                            double spanmax = (double)g_ts_span_max / unit * 1000.0;
                            g_ts_span = g_ts_span_max = 0; g_ts_span_n = 0;
                            if (g_inject_on == 8) {
                                double gpu_act = ms[0] + ms[1] + ms[3];
                                g_last_gpu_ms_x100 = (uint32_t)(gpu_act * 100.0 + 0.5);
                                lgd("GPU time of our passes: pyramid %.2f + motion %.2f + warp/post %.2f = %.2f ms (+ fence wait %.2f) | "
                                   "game span avg %.1f max %.1f ms\n", ms[0], ms[1], ms[3], gpu_act, ms[2], span, spanmax);
                                lgd("PASSES ms: pyr8 %.2f me3 %.2f me2 %.2f gm8+fgs8 %.2f me1 %.2f mf8 %.2f fence %.2f phase8 %.2f warp8+rcas+osd %.2f\n",
                                   (double)g_pp[0] / g_ts_n / unit * 1000.0, (double)g_pp[1] / g_ts_n / unit * 1000.0, (double)g_pp[2] / g_ts_n / unit * 1000.0,
                                   (double)g_pp[3] / g_ts_n / unit * 1000.0, (double)g_pp[4] / g_ts_n / unit * 1000.0, (double)g_pp[5] / g_ts_n / unit * 1000.0,
                                   (double)g_pp[6] / g_ts_n / unit * 1000.0, (double)g_pp[7] / g_ts_n / unit * 1000.0, (double)g_pp[8] / g_ts_n / unit * 1000.0);
                                lgd("PASS RAW last frame: ts %llu %llu %llu %llu %llu | t2 %llu %llu %llu %llu %llu %llu %llu\n", (unsigned long long)g_praw[0], (unsigned long long)g_praw[1], (unsigned long long)g_praw[2], (unsigned long long)g_praw[3], (unsigned long long)g_praw[4], (unsigned long long)g_praw[5], (unsigned long long)g_praw[6], (unsigned long long)g_praw[7], (unsigned long long)g_praw[8], (unsigned long long)g_praw[9], (unsigned long long)g_praw[10], (unsigned long long)g_praw[11]);
                                if (g_pp_split_n) {   // separate line: parsers of "PASSES ms:" / "PASS RAW" stay valid
                                    // the fields after "(frames N)" split rcas8+osd: rcas8 of the 1st mid alone, then the rest; with two
                                    // generated frames per pair (below30, FG Fast) the rest holds the 2nd mid, split again per G2 frame
                                    double g2n = g_pp_g2_n ? (double)g_pp_g2_n : 1.0;
                                    lgd("PASSES split ms: warp8 %.2f rcas8+osd %.2f pcopy8 %.2f (frames %u) | rcas8 %.2f rest %.2f | "
                                       "G2 frames %u: osd+wait %.2f phase8+warp8 %.2f rcas8+osd %.2f\n",
                                       (double)g_pp_split[0] / g_pp_split_n / unit * 1000.0, (double)g_pp_split[1] / g_pp_split_n / unit * 1000.0,
                                       (double)g_pp_split[2] / g_pp_split_n / unit * 1000.0, g_pp_split_n,
                                       (double)g_pp_split[3] / g_pp_split_n / unit * 1000.0, (double)g_pp_split[4] / g_pp_split_n / unit * 1000.0,
                                       g_pp_g2_n, (double)g_pp_g2[0] / g2n / unit * 1000.0, (double)g_pp_g2[1] / g2n / unit * 1000.0,
                                       (double)g_pp_g2[2] / g2n / unit * 1000.0);
                                }
                                for (int i = 0; i < 10; i++) g_pp[i] = 0;
                                for (int i = 0; i < 5; i++) g_pp_split[i] = 0;
                                for (int i = 0; i < 3; i++) g_pp_g2[i] = 0;
                                g_pp_split_n = 0; g_pp_g2_n = 0;
                                if (g_st_n)
                                    lgd("FRAMES %u: unmatched blocks avg %.0f max %u, scene-cut frames %u, fade frames %u | foreground: "
                                       "in %u frames, avg %.1f L2 blocks | camera %d,%d L2 px | log lines dropped %u\n", g_st_n,
                                       (double)g_st_cut_sum / g_st_n, g_st_cut_max, g_st_cuts, g_st_fades, g_st_fgf,
                                       (double)g_st_fg_sum / g_st_n, g_st_gmx, g_st_gmy, g_lgq_drop);
                                g_st_n = g_st_cut_max = g_st_cuts = g_st_fgf = g_st_fades = 0; g_st_cut_sum = g_st_fg_sum = 0;
                            }
                            else
                                lgd("GPU time of our passes: total %.2f ms = luma %.2f + search %.2f + refine %.2f + warp %.2f\n", tot, ms[0], ms[1], ms[2], ms[3]);
                            g_ts_n = 0; g_ts_first = 0; for (int i = 0; i < 4; i++) g_ts_pass[i] = 0;
                        }
                        ts[0] = 0;   // consumed: a slot no DCB rewrites (nothing injected) is not read again as new
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

                if (!(can_fg8 && want_fg)) {
                    pyr_cache_invalidate();
                }

                if (can_fg8 && want_fg) {
                    g_inert_reason[0] = 0;
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t tidx = tile_idx_now();
                    int pcopy = g_pcopy_avail && g_cfg.present_copy != 2;   // auto and on: available when 4..6 buffers allocated
                    g_pcopy_on = pcopy;
                    uint32_t mi1 = (pcopy && g_pcopy_k >= 2) ? (uint32_t)g_mbase + (g_flips % (uint32_t)g_pcopy_k) : (uint32_t)g_mbase + (g_flips % (uint32_t)g_nmid);
                    uint32_t mi2 = (uint32_t)g_mbase + ((g_flips + 1u) % (uint32_t)g_nmid);
                    b30cost_init();
                    // below30 and FG Fast (the two that emit 3-slot pairs): the 1st generated frame goes into the buffer
                    // flipped longest ago, the 2nd into the next one (pace_policy.h mid_lru); with 2-slot pairs only this is
                    // the round robin above. Fast with the round robin waited ~5-11 ms per 3-slot pair in WaitFlipDone with
                    // our DCB on the game's ring ("fence wait" 0.5-1.7 ms per pair, CUSA18723 29/10). Quality / Sharp
                    // without below30: as v2.35.
                    int mid_lru_on = (g_cfg.below30 || g_cfg.preset == 1) && !(pcopy && g_pcopy_k >= 2) && g_nmid >= 2 && (uint32_t)g_nmid <= MID_LRU_MAX;
                    if (mid_lru_on) {
                        if (g_mid_lru.n != (uint32_t)g_nmid) mid_lru_reset(&g_mid_lru, (uint32_t)g_nmid);
                        mi1 = (uint32_t)g_mbase + mid_lru_pick(&g_mid_lru, 0u);
                        mi2 = (uint32_t)g_mbase + mid_lru_pick(&g_mid_lru, 1u);
                    }
                    int tb_fast = VCFG_2BUF_TEST == 3 && tb_title() && pcopy && !g_fg_off;
                    static uint32_t tb_mid_cursor;
                    if (tb_fast) {
                        mi1 = tb_mid_index((uint32_t)g_mbase, (uint32_t)g_pcopy_k, tb_mid_cursor, 0);
                        mi2 = tb_mid_index((uint32_t)g_mbase, (uint32_t)g_pcopy_k, tb_mid_cursor, 1);
                    }
                    uint32_t t1_q16 = 32768u, t2_q16 = 43690u;
                    int two_mids = 0;
                    int force_repeat = (raw_dt_us > 66667ull) ? 1 : 0; // >66.7 ms (<15 fps hitch) -> repeat frame
                    __atomic_store_n(&g_pair_slots, 2u, __ATOMIC_RELEASE);

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
                            if (cc_pending_flips() >= 3u) m_out = 2;
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
                        // 1.1 FG Quality (strict 2x): 1 mid frame per real frame, VBlank-PLL display-slot phase.
                        // Below-30 regime (pace_policy.h b30_*): a pair predicted to take 3 refreshes shows 2 generated
                        // frames (t ~ 1/3, 2/3) in the 2nd buffer the mode-8 allocation already gave, then the real.
                        g_q2x_hold_acc = 0;
                        uint32_t pq8 = refresh_period_q8();
                        int b30_ok = b30_available(g_cfg.below30, g_nmid, g_mem_tight && g_s14_state != 2, g_cfg.preset, g_fg_off, pace_timer_on(), g_cfg.fps_lock);
                        int b30_on = b30_regime_step(&g_b30, (b30_ok && !force_repeat) ? b30_feed(ft_own_us) : 0u, pq8);
                        b30_win_frames++;
                        if (b30_on) b30_win_on++;
                        if (force_repeat) {
                            t1_q16 = 0u;
                        } else {
                            if (b30_on) {
                                uint32_t vbu = 16683u;
                                uint32_t to_vb = vblank_to_next_edge_us((int32_t)handle, sceKernelGetProcessTime(), &vbu);
                                int cut = 0;
                                uint32_t m_out = b30_slots(&g_b30, b30_feed(g_cpu_ema_us), pq8,
                                                           b30_queue_ahead_q8(cc_pending_flips(), to_vb, pq8), &cut);
                                if (cut) b30_win_cut++;
                                if (m_out == 3u && g_nmid >= 2 && mi1 != mi2) {
                                    two_mids = 1;
                                    mid2_flip_idx = mi2;
                                    third_flip = 1;
                                    __atomic_store_n(&g_pair_slots, 3u, __ATOMIC_RELEASE);
                                }
                                b30_win_on_vb += third_flip ? 3u : 2u;
                            }
                            t1_q16 = pll_predict_t_interp_q16((int32_t)handle, sceKernelGetProcessTime(), dt_enter_us, hold_mid_us);
                            t2_q16 = __atomic_load_n(&g_pll_t2_q16, __ATOMIC_ACQUIRE);
                        }
                    }

                    // 3-slot pair of the below-30 regime (FG Quality): the present-copy block below drops the 2nd generated
                    // frame whenever a copy is possible; it comes back when the copy did not run (pcopy cleared: one copy
                    // buffer or none, e.g. a 3-buffer game with g_pcopy_k = 1), because then no buffer takes the real's place.
                    int b30_two = two_mids && g_cfg.preset != 1 && !tb_fast;
                    uint32_t b30_mid2 = mid2_flip_idx;
                    on_flip_2buf_track(index);
                    int two_buf_now = (g_cfg.two_buffer || g_runtime_2buf || g_ngame == 2 || g_cfg.present_copy == 1);
                    if (!pcopy && two_buf_now && g_pbuf && g_cfg.present_copy != 2) pcopy = 1;
                    uint32_t ci = 0;
                    if (pcopy) {
                        static uint32_t pc_flip;
                        pc_flip = (pc_flip + 1u) % (uint32_t)g_pcopy_k;
                        ci = (uint32_t)g_mbase + (uint32_t)g_pcopy_k + pc_flip;
                        if (!tb_fast) { two_mids = 0; third_flip = 0; }   // FAST 3-step needs a second mid buffer: only the test-3 pulldown keeps it
                        if (g_pcopy_k >= 2 && ci < (uint32_t)g_nbuf && ci < 16u && g_buf[ci]) {
                            show_idx = ci;
                            if (index < 16u) __atomic_store_n(&g_sub_t[ci], __atomic_load_n(&g_sub_t[index], __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
                        } else {
                            pcopy = 0;
                        }
                    }
                    if (tb_fast) {
                        tb_mid_cursor = (tb_mid_cursor + (two_mids ? 2u : 1u)) % (uint32_t)g_pcopy_k;
                        if ((g_flips % 60u) == 0)
                            lg("PULLDOWN dt_us=%u mids=%d mi1=%u mi2=%u copy=%u pending=%u\n", g_ema_frame_us,
                               two_mids ? 2 : 1, mi1, mi2, ci, __atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE));
                    }
                    if (b30_two && !two_mids) {
                        if (!pcopy) { two_mids = 1; third_flip = 1; mid2_flip_idx = b30_mid2; }
                        else { t2_q16 = 43690u; __atomic_store_n(&g_pair_slots, 2u, __ATOMIC_RELEASE); }   // the copy runs
                    }
                    if (third_flip && g_cfg.preset != 1 && !tb_fast) b30_win_three++;
                    if (mid_lru_on)   // a 2nd generated frame outside the ring (late DirectMemory buffer) leaves the order alone
                        mid_lru_flipped(&g_mid_lru, mi1 - (uint32_t)g_mbase, third_flip && mid2_flip_idx == mi2 && mi2 >= (uint32_t)g_mbase &&
                                        mi2 < (uint32_t)g_mbase + (uint32_t)g_nmid, mi2 - (uint32_t)g_mbase);
                    g_slot_three[slot] = (uint8_t)(third_flip ? 1 : 0);
                    n = build_dcb_flow8(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi1], g_buf[mi2], g_pitch,
                                        tidx, px * 4, slot, g_flips, tail64, (int32_t)handle, mi1, mi2,
                                        t1_q16, t2_q16, two_mids, force_repeat, pcopy ? g_buf[ci] : 0ull, ci);
                    flip_idx = mi1;
                    second_flip = 1;
                } else if (g_inject_on == 8 && g_sh8_ok && g_fmt_supported && g_bw >= 640 && g_bh >= 360 && g_bw <= 3840 && g_bh <= 2160) {
                    // 1.4: Native / Post-only path (FG off in menu, or 60fps game): draw OSD menu/overlay + RCAS/Enhance on cur.
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t tidx = tile_idx_now();
                    n = build_dcb_post8(mine, g_buf[index], g_bw, g_bh, g_pitch, tidx, px * 4, slot, g_flips, tail64);
                } else if ((g_inject_on == 4 || g_inject_on == 6 || g_inject_on == 7) && g_prev_idx < game_idx_end() && mbufs_ready(handle)) {
                    uint32_t px = g_pitch * ((g_bh + 15u) & ~15u);
                    uint32_t mi = (uint32_t)g_mbase + (g_flips % (uint32_t)g_nmid);
                    if (g_inject_on >= 6 && g_work && g_bw >= 1280 && (g_bw % 640) == 0 && g_bh == g_bw * 9 / 16 && g_bw / 640 <= 4)
                        n = build_dcb_flow(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi], g_bw, g_bh, g_pitch,
                                           tile_idx_now(), px * 4, slot, g_flips, tail64,
                                           (int32_t)handle, mi, g_inject_on >= 7);
                    else
                        n = build_dcb_ghost(mine, g_buf[g_prev_idx], g_buf[index], g_buf[mi], (px + 63) / 64, px * 4, slot, g_flips, tail64);
                    flip_idx = mi;
                    second_flip = 1;
                } else if (g_inject_on == 2 || g_inject_on == 3) {
                    uint32_t px = g_pitch * ((g_bh + 63u) & ~63u);
                    uint64_t prev = (g_inject_on == 3) ? g_buf[index] : g_buf[g_prev_idx < (uint32_t)g_nbuf ? g_prev_idx : index];
                    n = build_dcb_ghost(mine, prev, g_buf[index], g_buf[index], (px + 63) / 64, px * 4, slot, g_flips, tail64);
                } else if (g_inject_on == 5) {
                    uint32_t px = g_pitch * ((g_bh + 63u) & ~63u);
                    uint32_t tidx = tile_idx_now();
                    n = build_dcb_rect(mine, g_buf[index], g_bw, g_bh, g_pitch, tidx, 32, 16, px * 4, slot, g_flips, tail64);
                } else if (g_inject_on == 1) {
                    if ((g_flips & 7) == 1 && !g_pend_seq) {
                        for (int i = 0; i < 64; i++) scr[i] = 0xDEADBEEFu;
                        n = build_dcb(mine, (uint64_t)(uintptr_t)scr, 1, 256, slot, g_flips, tail64);
                        g_pend_seq = g_flips; g_pend_slot = slot;
                    } else {
                        n = build_dcb(mine, g_buf[index], 1024, 0x40000, slot, g_flips, tail64);
                    }
                }

                if (n >= 64u && tail_dw != 64u) {
                    uint32_t base_n = n - 64u;
                    for (uint32_t ti = 0; ti < tail_dw; ti++) mine[base_n + ti] = tail[ti];
                    n = base_n + tail_dw;
                }

                int pace_p2 = (int)g_prev2_idx, pace_p1 = (int)g_prev_idx;
                g_prev2_idx = g_prev_idx;
                g_prev_idx = index;

                if (n == 0) {
                    // Nothing injected (e.g. Native mode with OSD/RCAS off, or first frame before g_prev_idx is valid)
                    g_last_submit_enter_t = sceKernelGetProcessTime();
                    int32_t rc0 = gnm_submit_and_flip_raw(has_wl, workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 30 && !g_detected_60fps && want_fg) pace_hz(30);
                    g_rel_t = sceKernelGetProcessTime();
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
                if (third_flip)  { for (uint32_t i = 0; i < tail_dw; i++) fb2[i] = tail[i]; }
                if (second_flip) { for (uint32_t i = 0; i < tail_dw; i++) fb[i]  = tail[i]; }
                static int last_show = -1;                  // with present copy the shown real buffers are the copies
                int pace_pre = (g_cfg.pace_order == 1) ? 1 : (g_cfg.pace_order == 2) ? 0 : (!pace_pcopy());
                g_last_pace_pre = pace_pre;
                if (hold_mid_us > 0) {
                    if (pace_pre) {
                        if (g_inject_on >= 7 && second_flip) hold_mid_us = 0;
                        else hold_mid_us = pll_adaptive_hold_us((int32_t)handle, sceKernelGetProcessTime(), dt_enter_us, hold_mid_us);
                    } else {
                        uint32_t vb_us = 16683u;
                        uint32_t to_vb = vblank_to_next_edge_us((int32_t)handle, sceKernelGetProcessTime(), &vb_us);
                        if (to_vb + 1500u < hold_mid_us) { hold_mid_us = to_vb + 1500u; g_pll_shortened++; }
                    }
                }
                if (third_flip) hold_mid_us = 0;   // 3-slot pair: mid1, mid2, real flipped back to back; a late real repeats the real
                // Shallow flip queue (pace_policy.h): FG pairs on the timer pacer, the game's own time well below 2 refreshes
                // for ~4 s -> the frame enters the queue only when at most one flip is still pending (raw status, not the
                // virtual one the game sees). Before the pre-submit pacer, or before the submit with post-submit pacing.
                static ShallowQueue s_shq;
                static uint32_t s_shq_frames, s_shq_on_frames, s_shq_timeouts;
                static uint64_t s_shq_wait_sum, s_shq_wait_max;
                int shq_ok = g_inject_on == 8 && second_flip && !third_flip && g_cfg.fps_lock != 20 && pace_timer_on();
                if (!g_cfg.shallow_queue || g_b30.on) shq_ok = 0;   // player setting; never in the below-30 regime (resets the dwell)
                int shq_on = shq_step(&s_shq, shq_ok ? g_cpu_ema_us : 0u);
                uint64_t t_shq0 = sceKernelGetProcessTime();
                if (shq_on) {
                    for (;;) {
                        VoFlipStatus qs;
                        memset(&qs, 0, sizeof(qs));
                        if (read_flip_status((int32_t)handle, &qs) != 0 || qs.flipPendingNum <= SHQ_MAX_PENDING) break;
                        if (sceKernelGetProcessTime() - t_shq0 > 40000ull) { s_shq_timeouts++; break; }
                        g_cc_shq_polls++;
                        sceKernelUsleep(200);
                    }
                }
                uint64_t shq_wait = sceKernelGetProcessTime() - t_shq0;
                if (shq_ok) {
                    s_shq_frames++;
                    if (shq_on) { s_shq_on_frames++; s_shq_wait_sum += shq_wait; if (shq_wait > s_shq_wait_max) s_shq_wait_max = shq_wait; }
                }
                uint64_t t_p0 = sceKernelGetProcessTime(), t_p1 = t_p0;
                if (pace_pre) {
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 60 && !second_flip) pace_hz(60);
                    else if (g_inject_on == 8 && second_flip && pace_timer_on()) pace_pair_ex((int32_t)handle, dt_enter_us, 1, b30_pace_nvb(g_pair_slots));
                    else if (g_inject_on >= 7 && second_flip) {
                        int shown = (int)show_idx, pp1 = show_idx != index ? last_show : pace_p1;
                        pace_vblank_ex(show_idx != index ? -1 : pace_p2, pp1, shown, (int32_t)handle, dt_enter_us, 1);
                        last_show = shown;
                    }
                    else if (second_flip) pace_30();
                    if (hold_mid_us > 0) {
                        uint32_t pre_hold = pll_adaptive_hold_us((int32_t)handle, sceKernelGetProcessTime(), dt_enter_us, hold_mid_us);
                        if (pre_hold > 200u) sceKernelUsleep(pre_hold);
                        hold_mid_us = 0;
                    }
                    t_p1 = sceKernelGetProcessTime();
                }
                uint64_t t_s1 = t_p1;
                if (can_fg8 && want_fg && !g_fg_off && raw_dt_us <= 66667ull) {
                    if (g_cfg.preset != 1 && !(VCFG_2BUF_TEST == 3 && tb_title() && g_pcopy_on)) {
                        uint32_t t_pll_q16 = pll_predict_t_interp_q16((int32_t)handle, t_s1, dt_enter_us, hold_mid_us);
                        uint32_t *tb_slot = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0xB00);
                        tb_slot[29] = (tb_slot[29] & 0xFFFFu) | ((t_pll_q16 & 0xFFFFu) << 16);
                        if (third_flip) {   // 2nd generated frame of a 3-slot pair (its table in the tb2 region of the slot)
                            uint32_t *tb2_slot = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + SLOT_TB2_OFF);
                            uint32_t t2n = __atomic_load_n(&g_pll_t2_q16, __ATOMIC_ACQUIRE);
                            tb2_slot[29] = (tb2_slot[29] & 0xFFFFu) | ((t2n & 0xFFFFu) << 16);
                        }
                        if (g_mon) g_mon[3] = t_pll_q16;
                    }
                    pll_commit_frame_vblank((int32_t)handle, t_s1, hold_mid_us);
                }
                g_last_submit_enter_t = t_s1;
                if (g_log && show_idx < 16u) {   // FTRACE record of this real frame (debug log only)
                    uint32_t fw = __atomic_load_n(&g_ft_w, __ATOMIC_RELAXED), fi = fw % FT_N;
                    __atomic_store_n(&g_ft[fi][0], (uint32_t)t_hook_in, __ATOMIC_RELAXED);
                    __atomic_store_n(&g_ft[fi][1], ft_pack_sub(t_s1 - t_hook_in, ft_own_us), __ATOMIC_RELAXED);
                    __atomic_store_n(&g_ft[fi][2], ft_with_ngen(0u, (second_flip && can_fg8 && want_fg && !g_fg_off && raw_dt_us <= 66667ull) ? (third_flip ? 2u : 1u) : 0u), __ATOMIC_RELAXED);
                    __atomic_store_n(&g_ft_slot[show_idx], fw + 1u, __ATOMIC_RELEASE);
                    __atomic_store_n(&g_ft_w, fw + 1u, __ATOMIC_RELEASE);
                }
                __atomic_store_n(&g_done, (volatile uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900), __ATOMIC_RELEASE);
                __atomic_store_n(&g_last_seq, g_flips, __ATOMIC_RELEASE);
                g_injects++;
                int32_t rc = gnm_submit_and_flip_raw(has_wl, workload, ncount, d2, s2, ccb ? c2 : ccb, ccbSz ? cs2 : ccbSz, handle, flip_idx, flipMode, flipArg);
                uint64_t t_s1e = sceKernelGetProcessTime(), t_hold = t_s1e, t_s2e = t_s1e;
                if (third_flip) {
                    void *dm2[1] = { fb2 };
                    uint32_t sm2[1] = { tail_dw * 4u };
                    gnm_submit_and_flip_raw(has_wl, workload, 1, dm2, sm2, NULL, NULL, handle, mid2_flip_idx, flipMode, flipArg);
                }
                if (second_flip) {
                    if (hold_mid_us > 0) sceKernelUsleep(hold_mid_us);
                    t_hold = sceKernelGetProcessTime();
                    void *d3[1] = { fb };
                    uint32_t s3[1] = { tail_dw * 4u };
                    // Experiment 2: the first mid keeps the game's opaque argument;
                    // tag only the real copy. This does not virtualize status or labels.
                    int64_t real_arg = flipArg;
                    if (VCFG_2BUF_TEST == 2 && tb_title() && g_pcopy_on && show_idx != index) {
                        real_arg = tb_tag_real_arg(flipArg);
                        if (g_flips < INJECT_AFTER_FLIPS + 5)
                            lg("FLIP_ARGS mid=%lld real=%lld mid_buf=%u real_buf=%u\n", (long long)flipArg,
                               (long long)real_arg, flip_idx, show_idx);
                    }
                    int32_t r2 = gnm_submit_and_flip_raw(has_wl, workload, 1, d3, s3, NULL, NULL, handle, show_idx, flipMode, real_arg);
                    t_s2e = sceKernelGetProcessTime();
                    if (g_flips < INJECT_AFTER_FLIPS + 12) lg("mode4 #%u: gnm flip(mid idx %u)=0x%08x, gnm flip(cur idx %u)=0x%08x\n", g_flips, flip_idx, (unsigned)rc, index, (unsigned)r2);
                }
                if (!pace_pre) {
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 60 && !second_flip) pace_hz(60);
                    else if (g_inject_on == 8 && second_flip && pace_timer_on()) pace_pair_ex((int32_t)handle, dt_enter_us, 0, b30_pace_nvb(g_pair_slots));
                    else if (g_inject_on >= 7 && second_flip) {
                        int shown = (int)show_idx, pp1 = show_idx != index ? last_show : pace_p1;
                        pace_vblank_ex(show_idx != index ? -1 : pace_p2, pp1, shown, (int32_t)handle, dt_enter_us, 0);
                        last_show = shown;
                    }
                    else if (second_flip) pace_30();
                } else if (second_flip) {
                    g_rel_t = t_s2e;
                }
                if (!second_flip) g_rel_t = sceKernelGetProcessTime();   // FG off: release -> submit is measured too
                if (second_flip) {   // where the game's thread spends its time inside this hook (FG frames)
                    static uint64_t a_build, a_sub, a_hold, a_flip2, a_pace, a_n;
                    uint64_t t_end = sceKernelGetProcessTime();
                    a_build += t_p0 - t_hook_in - shq_wait; a_sub += t_s1e - t_s1; a_hold += t_hold - t_s1e; a_flip2 += t_s2e - t_hold;
                    a_pace += pace_pre ? (t_p1 - t_p0) : (t_end - t_s2e); a_n++;
                    if (a_n >= 60) {
                        double k = 1.0 / 1000.0 / (double)a_n;
                        lgd("HOOK ms per FG frame: build %.1f submit %.1f hold %.1f flip2 %.1f pace %.1f total %.1f | game own %.1f | pending %u pll %u\n",
                           a_build * k, a_sub * k, a_hold * k, a_flip2 * k, a_pace * k, (a_build + a_sub + a_hold + a_flip2 + a_pace) * k,
                           g_cpu_ema_us / 1000.0, __atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE), g_pll_shortened);
                        a_build = a_sub = a_hold = a_flip2 = a_pace = a_n = 0;
                        if (s_shq_frames)   // separate line: parsers of "HOOK ms per FG frame" stay valid
                            lgd("QUEUE shallow %s: on in %u of %u frames, wait avg %.2f max %.2f ms, timeouts %u (on below %.1f ms own for %u frames, off above %.1f)\n",
                               shq_on ? "on" : "off", s_shq_on_frames, s_shq_frames,
                               s_shq_on_frames ? (double)s_shq_wait_sum / 1000.0 / s_shq_on_frames : 0.0, (double)s_shq_wait_max / 1000.0,
                               s_shq_timeouts, SHQ_ENTER_US / 1000.0, SHQ_DWELL, SHQ_EXIT_US / 1000.0);
                        s_shq_frames = s_shq_on_frames = s_shq_timeouts = 0; s_shq_wait_sum = s_shq_wait_max = 0;
                        if (g_cfg.below30 && b30_win_frames) {   // separate line, as QUEUE
                            uint32_t pq = refresh_period_q8();
                            lgd("BELOW30 %s: regime %u of %u frames (%.1f s), 3-slot pairs %u, 2nd generated dropped by the queue %u, "
                               "median own %.2f refreshes (on above %.2f, off below %.2f)%s\n",
                               g_b30.on ? "on" : "off", b30_win_on, b30_win_frames, (double)b30_win_on_vb * pq / 256.0 / 1e6,
                               b30_win_three, b30_win_cut, b30_median_q8(&g_b30) / 256.0, B30_ENTER_Q8 / 256.0, B30_EXIT_Q8 / 256.0,
                               g_s14_state == 2 ? " | 2nd buffer: shave14 DirectMemory reserve"
                               : (g_nmid < 2 || g_mem_tight) ? " | unavailable: one generated-frame buffer (memory tight)" : "");
                        }
                        if (g_cfg.below30 && b30_win_frames) {   // separate line, as BELOW30
                            lg("B30COST: fence before the 1st generated frame avg %.2f ms (%u), after a 3-slot pair %.2f ms (%u) | "
                               "extra GPU fed back avg %.2f ms (fence %.2f + 2nd generated %.2f over %u frames, %u 3-slot) | own %.1f fed %.1f ms\n",
                               b30c_win_n ? b30c_win_f1_us / 1000.0 / b30c_win_n : 0.0, b30c_win_n,
                               b30c_win_n_after3 ? b30c_win_f1_after3_us / 1000.0 / b30c_win_n_after3 : 0.0, b30c_win_n_after3,
                               g_b30x.ema_us / 1000.0, g_b30x.nw ? g_b30x.fence_sum_us / 1000.0 / g_b30x.nw : 0.0,
                               g_b30x.nw ? g_b30x.g2_sum_us / 1000.0 / g_b30x.nw : 0.0, g_b30x.nw, g_b30x.n3,
                               g_cpu_ema_us / 1000.0, b30_feed(g_cpu_ema_us) / 1000.0);
                            b30c_win_f1_us = b30c_win_n = b30c_win_f1_after3_us = b30c_win_n_after3 = 0;
                            g_b30x.fence_sum_us = g_b30x.g2_sum_us = g_b30x.nw = g_b30x.n3 = 0;
                        }
                        b30_win_frames = b30_win_on = b30_win_on_vb = b30_win_three = b30_win_cut = 0;
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
    g_last_submit_enter_t = sceKernelGetProcessTime();
    int32_t prc = gnm_submit_and_flip_raw(has_wl, workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    if (!g_mon_on && g_log) g_cc_off_rel = sceKernelGetProcessTime();   // framegen=0: own time per frame (cc_fgoff_log)
    return prc;
}

#include "vout_trace.h"

int32_t sceGnmSubmitAndFlipCommandBuffers_hook(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    if (vcfg_in_submit_get()) {
        return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
                             int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                             count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    }
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    tb_on_game_submit();            // test builds: the status poll that let the game out of its wait
    static uint32_t s_boot_flips;   // the first submits are boot stages too (B01: starts that end before the first one)
    uint32_t bf = s_boot_flips < 3 ? ++s_boot_flips : 0;
    if (bf) STAGE("SubmitAndFlip #%u enter: count=%u handle=%u index=%u flipMode=%u", bf, count, handle, index, flipMode);
    uint32_t gn = gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 1);
    if (gn) { count = gn; dcb = gd; dcbSz = gs; if (ccb) ccb = gc; if (ccbSz) ccbSz = gcs; }
    vcfg_in_submit_inc();
    int32_t rc = flip_inner_ex(0, 0, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    vcfg_in_submit_dec();
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
    if (vcfg_in_submit_get()) {
        return HOOK_CONTINUE(sceGnmSubmitCommandBuffers, int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                             count, dcb, dcbSz, ccb, ccbSz);
    }
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    uint32_t gn = gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 0);
    if (count >= 1 && dcb && dcbSz) {
        uint32_t wi = __atomic_fetch_add(&g_vo_sub_w, 1u, __ATOMIC_ACQ_REL) % VO_SUB_RING_N;
        g_vo_sub_ring[wi].ts_us = sceKernelGetProcessTime();
        g_vo_sub_ring[wi].count = count;
        g_vo_sub_ring[wi].last_sz = dcbSz[count - 1];
        g_vo_sub_ring[wi].last_dcb = (uintptr_t)dcb[count - 1];
    }
    if (g_vt_on && count >= 1 && count <= 16 && dcb && dcbSz)
        vt_rec(VT_SUBMIT, count, dcbSz[count - 1], ccbSz ? ccbSz[count - 1] : 0, (uint32_t)(uintptr_t)dcb[count - 1], (const uint32_t *)dcb[count - 1], dcbSz[count - 1]);
    int32_t rc;
    vcfg_in_submit_inc();
    if (gn)
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers, int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                           gn, gd, gs, ccb ? gc : NULL, ccbSz ? gcs : NULL);
    else
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers, int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                           count, dcb, dcbSz, ccb, ccbSz);
    vcfg_in_submit_dec();
    return rc;
}

static void wl_count(int k, const char *name)
{
    if (vcfg_in_submit_get()) { g_wl_nested[k]++; return; }
    uint32_t n = ++g_wl_direct[k];
    if (n == 1 || (n % 600u) == 0)
        lg("DIAG: the game calls %s directly (%u calls, %u nested in ours): routed into FG/scanner pipeline\n",
           name, n, g_wl_nested[k]);
}
int32_t sceGnmSubmitCommandBuffersForWorkload_hook(uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz)
{
    wl_count(0, "sceGnmSubmitCommandBuffersForWorkload");
    if (vcfg_in_submit_get()) {
        return HOOK_CONTINUE(sceGnmSubmitCommandBuffersForWorkload,
                             int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *),
                             workload, count, dcb, dcbSz, ccb, ccbSz);
    }
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    uint32_t gn = gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 0);
    if (count >= 1 && dcb && dcbSz) {
        uint32_t wi = __atomic_fetch_add(&g_vo_sub_w, 1u, __ATOMIC_ACQ_REL) % VO_SUB_RING_N;
        g_vo_sub_ring[wi].ts_us = sceKernelGetProcessTime();
        g_vo_sub_ring[wi].count = count;
        g_vo_sub_ring[wi].last_sz = dcbSz[count - 1];
        g_vo_sub_ring[wi].last_dcb = (uintptr_t)dcb[count - 1];
    }
    if (g_vt_on && count >= 1 && count <= 16 && dcb && dcbSz)
        vt_rec(VT_SUBMIT, count, dcbSz[count - 1], ccbSz ? ccbSz[count - 1] : 0, (uint32_t)(uintptr_t)dcb[count - 1], (const uint32_t *)dcb[count - 1], dcbSz[count - 1]);
    int32_t rc;
    vcfg_in_submit_inc();
    if (gn)
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffersForWorkload,
                           int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *),
                           workload, gn, gd, gs, ccb ? gc : NULL, ccbSz ? gcs : NULL);
    else
        rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffersForWorkload,
                           int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *),
                           workload, count, dcb, dcbSz, ccb, ccbSz);
    vcfg_in_submit_dec();
    return rc;
}
int32_t sceGnmSubmitAndFlipCommandBuffersForWorkload_hook(uint32_t workload, uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[],
                                                          uint32_t *ccbSz, uint32_t handle, uint32_t index, uint32_t flipMode, int64_t flipArg)
{
    wl_count(1, "sceGnmSubmitAndFlipCommandBuffersForWorkload");
    if (vcfg_in_submit_get()) {
        return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffersForWorkload,
                             int32_t (*)(uint32_t, uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
                             workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    }
    void *gd[GV_MAXE]; uint32_t gs[GV_MAXE]; void *gc[GV_MAXE]; uint32_t gcs[GV_MAXE];
    tb_on_game_submit();
    static uint32_t s_wl_boot_flips;
    uint32_t bf = s_wl_boot_flips < 3 ? ++s_wl_boot_flips : 0;
    if (bf) STAGE("SubmitAndFlipForWorkload #%u enter: wl=%u count=%u handle=%u index=%u flipMode=%u", bf, workload, count, handle, index, flipMode);
    uint32_t gn = gv_rewrite(count, dcb, dcbSz, ccb, ccbSz, gd, gs, gc, gcs, 1);
    if (gn) { count = gn; dcb = gd; dcbSz = gs; if (ccb) ccb = gc; if (ccbSz) ccbSz = gcs; }
    vcfg_in_submit_inc();
    int32_t rc = flip_inner_ex(1, workload, count, dcb, dcbSz, ccb, ccbSz, handle, index, flipMode, flipArg);
    vcfg_in_submit_dec();
    if (bf) STAGE("SubmitAndFlipForWorkload #%u done = 0x%08x", bf, (unsigned)rc);
    gv_frame_end();
    return rc;
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
    vcfg_in_submit_inc();
    int32_t rc = HOOK_CONTINUE(sceGnmSubmitCommandBuffers,
                               int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *),
                               1u, d2, s2, NULL, NULL);
    vcfg_in_submit_dec();
    return rc;
}

// Full FrameGen + OSD/RCAS pipeline for games that separate sceGnmSubmitCommandBuffers and sceVideoOutSubmitFlip.
int32_t sceVideoOutSubmitFlip_hook(int32_t handle, int32_t index, uint32_t mode, int64_t arg)
{
    if (g_vt_hooked && !vcfg_in_submit_get()) {
        if (g_vo_submit_flips + 1u >= VT_START_FLIP && !g_vt_dumped) g_vt_on = 1;
        vt_rec(VT_FLIP, (uint32_t)index, mode, (uint32_t)arg, (uint32_t)handle, NULL, 0);
    }
    uint64_t t_hook_in = sceKernelGetProcessTime();
    int gnm_flip_active = (g_flips > 0 && g_last_submit_enter_t && t_hook_in >= g_last_submit_enter_t &&
                           (t_hook_in - g_last_submit_enter_t) < 250000ull);
    // If called from inside sceGnmSubmitAndFlipCommandBuffers (or while SubmitAndFlip is actively used), pass through.
    if (vcfg_in_submit_get() || gnm_flip_active || index < 0) {
        int32_t vr = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t), handle, index, mode, arg);
        if (g_vt_hooked && g_vt_n >= VT_N) vt_dump();
        return vr;
    }

    uint64_t dt_enter_us = g_vo_last_submit_enter_t ? (t_hook_in - g_vo_last_submit_enter_t) : 33366ull;
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
    if (vf == INJECT_AFTER_FLIPS || (vf > INJECT_AFTER_FLIPS && !g_vout_fg_inited) ||
        (g_cfg.vout_path && !g_vout_fg_inited && (vf + g_flips) >= INJECT_AFTER_FLIPS)) {
        vout_fg_ctx_init((uint32_t)handle, 1);
    }

    if (on_sub_hibernation_gate(vf + g_flips)) {
        g_prev2_idx = g_prev_idx;
        g_prev_idx = u_idx;
        g_vo_last_submit_enter_t = sceKernelGetProcessTime();
        int32_t vr_hib = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t), handle, index, mode, arg);
        if (g_vt_hooked && g_vt_n >= VT_N) vt_dump();
        return vr_hib;
    }

    if (g_inject_on && (vf >= INJECT_AFTER_FLIPS || g_flips >= INJECT_AFTER_FLIPS || g_vout_fg_inited) && u_idx < (uint32_t)g_nbuf) {
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
                __atomic_store_n(&g_pair_slots, 2u, __ATOMIC_RELEASE);   // the below-30 regime runs on the gnm path only
                int pcopy = g_pcopy_avail && g_cfg.present_copy != 2;
                g_pcopy_on = pcopy;
                uint32_t mi1 = (pcopy && g_pcopy_k >= 2) ? (uint32_t)g_mbase + (vf % (uint32_t)g_pcopy_k)
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
                    if (cc_pending_flips() >= 3u) m_out = 2;
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
                    if (force_repeat) {
                        t1_q16 = 0u;
                    } else {
                        t1_q16 = pll_predict_t_interp_q16(handle, sceKernelGetProcessTime(), dt_enter_us, hold_mid_us);
                    }
                }

                on_flip_2buf_track(u_idx);
                int vo_two_buf_now = (g_cfg.two_buffer || g_runtime_2buf || g_ngame == 2 || g_cfg.present_copy == 1);
                if (!pcopy && vo_two_buf_now && g_pbuf && g_cfg.present_copy != 2) pcopy = 1;
                uint32_t ci = 0;
                if (pcopy) {
                    static uint32_t vo_pc_flip;
                    vo_pc_flip = (vo_pc_flip + 1u) % (uint32_t)g_pcopy_k;
                    ci = (uint32_t)g_mbase + (uint32_t)g_pcopy_k + vo_pc_flip;
                    if (g_pcopy_k >= 2 && ci < (uint32_t)g_nbuf && ci < 16u && g_buf[ci]) {
                        show_idx = ci;
                        if (u_idx < 16u)
                            __atomic_store_n(&g_sub_t[ci], __atomic_load_n(&g_sub_t[u_idx], __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
                    } else {
                        pcopy = 0;
                    }
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
            } else {
                pyr_cache_invalidate();
            }

            int pace_p2 = (int)g_prev2_idx, pace_p1 = (int)g_prev_idx;
            g_prev2_idx = g_prev_idx;
            g_prev_idx = u_idx;

            if (n > 0) {
                static int vo_last_show = -1;
                int pace_pre = (g_cfg.pace_order == 1) ? 1 : (g_cfg.pace_order == 2) ? 0 : (!pace_pcopy());
                g_last_pace_pre = pace_pre;
                if (hold_mid_us > 0) {
                    if (pace_pre) {
                        hold_mid_us = pll_adaptive_hold_us(handle, sceKernelGetProcessTime(), dt_enter_us, hold_mid_us);
                    } else {
                        uint32_t vb_us = 16683u;
                        uint32_t to_vb = vblank_to_next_edge_us(handle, sceKernelGetProcessTime(), &vb_us);
                        if (to_vb + 1500u < hold_mid_us) { hold_mid_us = to_vb + 1500u; g_pll_shortened++; }
                    }
                }
                if (pace_pre) {
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 60 && !second_flip) pace_hz(60);
                    else if (g_inject_on == 8 && second_flip && pace_timer_on()) pace_pair_ex(handle, dt_enter_us, 1, 2u);
                    else if (g_inject_on >= 7 && second_flip) {
                        int shown = (int)show_idx, pp1 = show_idx != u_idx ? vo_last_show : pace_p1;
                        pace_vblank_ex(show_idx != u_idx ? -1 : pace_p2, pp1, shown, handle, dt_enter_us, 1);
                        vo_last_show = shown;
                    } else if (second_flip) {
                        pace_30();
                    }
                    if (hold_mid_us > 0) {
                        uint32_t pre_hold = pll_adaptive_hold_us(handle, sceKernelGetProcessTime(), dt_enter_us, hold_mid_us);
                        if (pre_hold > 200u) sceKernelUsleep(pre_hold);
                        hold_mid_us = 0;
                    }
                }
                uint64_t vo_t_s1 = sceKernelGetProcessTime();
                if (can_fg8 && want_fg && !g_fg_off && raw_dt_us <= 66667ull) {
                    if (g_cfg.preset != 1) {
                        uint32_t t_pll_q16 = pll_predict_t_interp_q16(handle, vo_t_s1, dt_enter_us, hold_mid_us);
                        uint32_t *tb_slot = (uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0xB00);
                        tb_slot[29] = (tb_slot[29] & 0xFFFFu) | ((t_pll_q16 & 0xFFFFu) << 16);
                        if (g_mon) g_mon[3] = t_pll_q16;
                    }
                    pll_commit_frame_vblank(handle, vo_t_s1, hold_mid_us);
                }
                g_vo_last_submit_enter_t = vo_t_s1;
                __atomic_store_n(&g_done, (volatile uint32_t *)(g_gpu + SLOT0 + slot * SLOT_SZ + 0x900), __ATOMIC_RELEASE);
                __atomic_store_n(&g_last_seq, vf, __ATOMIC_RELEASE);
                int32_t s_rc = vout_submit_dcb(mine, n);
                if (vf == INJECT_AFTER_FLIPS || (vf % 600u) == 0)
                    lg("vout inject #%u: dcb=%u dw submit=0x%08x flip_idx=%u show_idx=%u pcopy=%d pace_pre=%d\n",
                       vf, n, (unsigned)s_rc, flip_idx, show_idx, g_pcopy_on, pace_pre);
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
                if (!pace_pre) {
                    if (g_cfg.fps_lock == 20) pace_hz(20);
                    else if (g_cfg.fps_lock == 60 && !second_flip) pace_hz(60);
                    else if (g_inject_on == 8 && second_flip && pace_timer_on()) pace_pair_ex(handle, dt_enter_us, 0, 2u);
                    else if (g_inject_on >= 7 && second_flip) {
                        int shown = (int)show_idx, pp1 = show_idx != u_idx ? vo_last_show : pace_p1;
                        pace_vblank_ex(show_idx != u_idx ? -1 : pace_p2, pp1, shown, handle, dt_enter_us, 0);
                        vo_last_show = shown;
                    } else if (second_flip) {
                        pace_30();
                    }
                } else if (second_flip) {
                    g_rel_t = sceKernelGetProcessTime();
                }
                gv_frame_end();
                if (g_vt_hooked && g_vt_n >= VT_N) vt_dump();
                return vr1;
            }
        }
    }
#endif
    g_vo_last_submit_enter_t = sceKernelGetProcessTime();
    int32_t vr = HOOK_CONTINUE(sceVideoOutSubmitFlip, int32_t (*)(int32_t, int32_t, uint32_t, int64_t), handle, index, mode, arg);
    if (g_vt_hooked && g_vt_n >= VT_N) vt_dump();
    return vr;
}

int32_t sceVideoOutUnregisterBuffers(int32_t handle, int32_t attributeIndex);

int32_t sceVideoOutRegisterBuffers_hook(int32_t handle, int32_t startIndex, void *const *addresses, int32_t bufferNum, const VoAttr *attr)
{
    cusa10249_apply_deferred_tail_if_needed();
    flush_map_run();
    if (g_dbg_hooks) crash_handlers_install();
    STAGE("RegisterBuffers enter: handle=%d start=%d num=%d", handle, startIndex, bufferNum);
    VoAttr fixed_attr;
    if (attr && attr->tilingMode == 0 && (attr->pitchInPixel & 127u) != 0) {
        fixed_attr = *attr;
        fixed_attr.pitchInPixel = (attr->pitchInPixel + 127u) & ~127u;
        STAGE("RegisterBuffers: aligned macro-tiled pitch %u -> %u for %ux%u",
              attr->pitchInPixel, fixed_attr.pitchInPixel, attr->width, attr->height);
        attr = &fixed_attr;
    }
    if (attr)
        STAGE("  attr: fmt=0x%x tiling=%u aspect=%u %ux%u pitch=%u option=%u", attr->format, attr->tilingMode,
              attr->aspectRatio, attr->width, attr->height, attr->pitchInPixel, attr->option);
    for (int i = 0; addresses && i < bufferNum && i < 8; i++) STAGE("  buf[%d]=%p", startIndex + i, addresses[i]);
    lg("RegisterBuffers handle=%d start=%d num=%d\n", handle, startIndex, bufferNum);
    int is_aux_plane = (startIndex > 0 && g_bw > 0 && attr &&
                        (attr->width != g_bw || attr->height != g_bh || attr->tilingMode != g_attr.tilingMode));
    int need_rereg_ext_fmt = 0;
    if (attr && startIndex == 0) { g_handle = handle; g_attr = *attr; }
    if (attr && !is_aux_plane) {
        g_bw = attr->width; g_bh = attr->height; g_pitch = attr->pitchInPixel;
        // Scanout formats: 8-bit 0x8000xxxx and 10-bit 0x88xxxxxx (each needs its own T# data format; treating 10-bit as
        // 8888 turned the picture into noise). When startIndex == 0, the game is registering a primary scanout set
        // (e.g. switching from an 8-bit linear splash screen to a 10-bit tiled 3D engine in CUSA04529), so reset seen_dfmt.
        uint32_t fam = attr->format >> 24, dfmt = fam == 0x80u ? 10u : fam == 0x88u ? 9u : 0u;   // 0x88740000 = HDR10 PQ
        static uint32_t seen_dfmt;
        if (startIndex == 0) {
            seen_dfmt = 0; g_fmt_supported = 1; g_game_idx_hi = 0; g_nbuf = 0;
            if (!g_safe_active) {
                g_mbuf_state = 0; g_mbase = 16; g_nmid = 0; g_pcopy_avail = 0; g_pcopy_on = 0;
                for (int i = 0; i < 16; i++) g_buf[i] = 0;
            }
            g_prev_idx = 0xFFFFFFFFu; g_prev2_idx = 0xFFFFFFFFu;
            pyr_cache_invalidate();
        }
        if (!dfmt) {
            g_fmt_supported = 0;
        } else if (seen_dfmt && seen_dfmt != dfmt) {
            seen_dfmt = dfmt;
            g_t_dfmt = dfmt;
            g_fmt_supported = 1;
            g_attr = *attr;
            g_ext_attr = *attr;
            pyr_cache_invalidate();
            if (g_mbuf_state == 1 && g_ext_nm >= 1 && g_mbase >= startIndex + bufferNum) {
                need_rereg_ext_fmt = 1;
            }
        } else {
            seen_dfmt = dfmt;
            g_t_dfmt = dfmt;
        }
        vov_set_hdr(attr->format == 0x88740000u);   // HDR10 (BT.2020 PQ): overlay palette re-encoded, passes unchanged
        lg("  attr: fmt=0x%x (supported=%d) tiling=%u aspect=%u %ux%u pitch=%u option=%u\n",
           attr->format, g_fmt_supported, attr->tilingMode, attr->aspectRatio, attr->width, attr->height, attr->pitchInPixel, attr->option);
    }
    int overlapped_our_mbase = 0;
    if (addresses && bufferNum > 0 && !is_aux_plane) {
        if (startIndex > 0 && g_mbuf_state == 1 && startIndex < g_nbuf && startIndex + bufferNum > g_mbase) {
            overlapped_our_mbase = 1;
            STAGE("RegisterBuffers: the game registers %d..%d over our buffers from %d -> will unregister slot 0 & relocate ours to top",
                  startIndex, startIndex + bufferNum - 1, g_mbase);
        }
        if (startIndex + bufferNum > g_game_idx_hi) g_game_idx_hi = startIndex + bufferNum;
    }
    for (int i = 0; addresses && i < bufferNum && startIndex + i < 16; i++) {
        if (!is_aux_plane) {
            g_buf[startIndex + i] = (uint64_t)(uintptr_t)addresses[i];
            if (g_mbuf_state != 1 && startIndex + i + 1 > g_nbuf) g_nbuf = startIndex + i + 1;
        }
        lg("  buf[%d]=%p\n", startIndex + i, addresses[i]);
    }
    if (g_safe_active && startIndex == 0 && g_mbuf_state == 1 && g_mbase >= bufferNum && g_ext_nm >= 1) {
        for (int i = 0; i < g_ext_nm && g_mbase + i < 16; i++) {
            g_buf[g_mbase + i] = (uint64_t)(uintptr_t)g_ext_va[i];
        }
        g_nbuf = g_mbase + g_ext_nm;
    }
    if (overlapped_our_mbase && g_ext_nm >= 1) {
        // Free attribute slot 0 (which held 0..g_ngame+g_ext_nm-1) and re-register the game's original 0..startIndex-1
        // buffers alone before registering the game's new startIndex..startIndex+bufferNum-1 buffers (CUSA13323, CUSA11456).
        if (startIndex <= 8) {
            void *orig0[8];
            for (int i = 0; i < startIndex; i++) orig0[i] = (void *)(uintptr_t)g_buf[i];
            int32_t ur = sceVideoOutUnregisterBuffers(handle, 0);
            int32_t rr0 = (ur == 0) ? HOOK_CONTINUE(sceVideoOutRegisterBuffers,
                                                    int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                                    handle, 0, orig0, startIndex, &g_attr) : -1;
            STAGE("RegisterBuffers slot-0 split: unregister(0)=0x%08x re-reg(0..%d)=0x%08x",
                  (unsigned)ur, startIndex - 1, (unsigned)rr0);
        }
        int32_t r0 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                   handle, startIndex, addresses, bufferNum, attr);
        g_ngame = g_game_idx_hi;
        if (g_ngame > 2) {
            g_pcopy_avail = 0;
            if (g_ext_nm > 3) g_ext_nm = 3;
            g_nmid = g_ext_nm;
        }
        int base = 16 - g_ext_nm;
        if (r0 >= 0 && base >= g_game_idx_hi) {
            int32_t r1 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                       handle, base, g_ext_va, g_ext_nm, &g_ext_attr);
            STAGE("RegisterBuffers auto-relocated our %d buffer(s) to index %d = 0x%08x", g_ext_nm, base, (unsigned)r1);
            lg("mbufs: auto-relocated %d buffer(s) to index %d after game registered %d..%d (r=0x%08x)\n",
               g_ext_nm, base, startIndex, startIndex + bufferNum - 1, (unsigned)r1);
            if (r1 >= 0) {
                g_ext_attr_slot = r1;
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
    if (attr && startIndex == 0 && bufferNum >= 2 && bufferNum <= 8 && addresses && (g_mbuf_state >= 0 || g_pre_mem != NULL) && !g_safe) {
        g_ngame = bufferNum;
        if (g_ngame == 2 && g_status_hooked && !g_bench_enabled && read_present_copy_raw() != 2) g_vq_on = 1;
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
            // B01: the VA gap between the game's buffers is not proof of the surface size when the game interleaves
            // depth/GBuffer surfaces between scanout buffers (0x1000000 = 16 MB in CUSA14209/CUSA36375,
            // 0x10f8000 = 17 MB in CUSA10454/CUSA11100, 0x1fb0000 = 31.7 MB in CUSA32836), while small tails
            // <= 1 MB (e.g. 0x870000 in CUSA08519 for DCC/CMASK metadata) are part of the scanout surface.
            if (g_pitch >= 640 && g_bh >= 360) {
                size_t calc = (size_t)g_pitch * (size_t)((g_bh + 63u) & ~63u) * 4u;
                size_t surf_sz = (calc + 0x7FFFFu) & ~(size_t)0x7FFFFu;
                int clamp_interleaved = (!one || one > surf_sz + 0x100000u ||
                                         (g_pre_mem && g_pre_size >= surf_sz && g_pre_size < one));
                STAGE("mbufs size: VA-gap stride 0x%zx, pitch*h*4 0x%zx (surf_sz 0x%zx)%s", one, calc, surf_sz,
                      (one && clamp_interleaved) ? " -> clamped to surf_sz" : "");
                if (surf_sz >= 0x100000 && clamp_interleaved) {
                    one = surf_sz;
                    lg("mbufs: using computed surface size 0x%zx from %ux%u (pitch %u)\n", one, g_bw, g_bh, g_pitch);
                }
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
            // use g_pre_mem if present, or at most 1 mid buffer from the preserved 8 MB reserve when g_pre_mem is NULL.
            int mem_tight = (g_shave_orig_len > 0) || g_mem_tight || (g_pre_mem && g_pre_size < 0x2000000u);
            uint8_t *m = NULL;
            int nm = 0;
            int max_extra = 16 - g_ngame;
            int stream_3buf = (!want_pcopy &&
                               ((!g_known_game && g_dbg_allocs >= 32u) ||
                                vcfg_in_family(g_title_id, "CUSA09311") ||
                                vcfg_in_family(g_title_id, "CUSA18534") ||
                                vcfg_in_family(g_title_id, "CUSA10123")));
            int cap_extra = want_pcopy
                ? (vcfg_in_family(g_title_id, "CUSA11456") ? 1 : ((!g_known_game && g_dbg_allocs >= 32u) ? 2 : (hi_res ? 4 : 6)))
                : (stream_3buf ? 1 : ((hi_res || !g_known_game) ? 2 : 3));
            if (max_extra > cap_extra) max_extra = cap_extra;
            if (one && g_pre_mem) {
                nm = (int)(g_pre_size / one);
                if (nm > max_extra) nm = max_extra;
                if (nm >= 1) {
                    // Shrink g_pre_mem from the LOW end via shrink_early_mem_to(used_pre) BEFORE taking m = g_pre_mem,
                    // so the retained display buffers stay at the very TOP of DirectMemory and the free DirectMemory
                    // pool below g_pre_mem remains 100% contiguous for post-RegisterBuffers arenas (e.g. CUSA13323,
                    // CUSA17722, CUSA01154, CUSA32946).
                    size_t used_pre = (one * (size_t)nm + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
                    if (used_pre < 0x800000u) used_pre = 0x800000u;
                    if (used_pre > 0 && used_pre < g_pre_size) {
                        long old_pm = g_pre_mem_phys;
                        shrink_early_mem_to_ex(used_pre, 0);
                        // If g_pre_gpu / g_pre_work were packed in the 2 MB page right below old_pm, move them right below
                        // the new g_pre_mem_phys so the reclaimed range [old_pm, g_pre_mem_phys) is not pinned in the middle.
                        if (g_pre_mem && g_pre_mem_phys > old_pm && g_pre_mem_phys >= 0x200000L && !g_gpu && !g_work) {
                            long new_pack_s = g_pre_mem_phys - 0x200000L, new_pack_e = g_pre_mem_phys;
                            if (g_pre_gpu && g_pre_gpu_phys >= old_pm - 0x200000L && g_pre_gpu_phys < old_pm) {
                                long np = 0;
                                void *nv = alloc_dm_range(new_pack_e - (long)g_gpu_sz, new_pack_e, g_gpu_sz, g_gpu_sz, 0, &np);
                                if (!nv) nv = alloc_dm_range(new_pack_s, new_pack_e, g_gpu_sz, g_gpu_sz, 0, &np);
                                if (nv) {
                                    size_t osz = g_gpu_sz;
                                    free_dm_block(&g_pre_gpu, &g_pre_gpu_phys, &osz, "gpu_repack");
                                    g_pre_gpu = nv; g_pre_gpu_phys = np;
                                }
                            }
                            if (g_pre_work && g_pre_work_phys >= old_pm - 0x200000L && g_pre_work_phys < old_pm) {
                                long np = 0;
                                size_t wsz = g_pre_work_sz;
                                void *nv = alloc_dm_range(new_pack_s, new_pack_e, wsz, 0x10000, g_work_garlic ? 3 : 0, &np);
                                if (nv) {
                                    free_dm_block(&g_pre_work, &g_pre_work_phys, &g_pre_work_sz, "work_repack");
                                    g_pre_work = nv; g_pre_work_phys = np; g_pre_work_sz = wsz;
                                }
                            }
                        }
                    }
                    m = (uint8_t *)g_pre_mem;
                }
            }
            for (int want = (mem_tight ? 1 : max_extra); one && !m && want >= 1; want--) {
                m = (uint8_t *)alloc_mbufs((one * (size_t)want + 0x1FFFFFu) & ~(size_t)0x1FFFFFu);
                if (m) nm = want;
            }
            // Double-buffered game (or forced 2-Buffer mode): mid buffers + present-copy buffers.
            // Never allocate a second DirectMemory block m2 when g_pre_mem already provides nm >= 2 buffers
            // (nm = 4 for 1080p = 2 mids + 2 present-copy buffers), or when surface size >= 16 MB (4K/1800p where m2
            // would consume an extra 16..32 MB of un-valved DirectMemory), so we don't starve games that allocate
            // arenas after RegisterBuffers (CUSA13323, CUSA11456, CUSA32946).
            static uint8_t *s_pcopy_m2 = NULL;
            static size_t s_pcopy_m2_sz = 0;
            uint8_t *m2 = NULL; int nm2 = 0;
            STAGE("pcopy gate: ngame=%d pcopy_cfg=%d want_pcopy=%d tight=%d m=%p nm=%d one=0x%zx pre=%p pre_size=0x%zx max_extra=%d",
                  g_ngame, pcopy_cfg, want_pcopy, mem_tight, (void *)m, nm, one, g_pre_mem, g_pre_size, max_extra);
            if (want_pcopy && m && nm < 2 && max_extra >= 2 && one < 0x1000000u) {
                size_t need2 = (one * (size_t)(2 - nm) + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
                if (s_pcopy_m2 && s_pcopy_m2_sz >= need2) {
                    m2 = s_pcopy_m2;
                    nm2 = 2 - nm;
                } else if (!mem_tight && dm_avail(0x200000) >= need2 + 0x4000000u) {
                    m2 = (uint8_t *)alloc_mbufs(need2);
                    if (m2) { s_pcopy_m2 = m2; s_pcopy_m2_sz = need2; nm2 = 2 - nm; }
                }
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
                g_pcopy_k = nm >= 6 ? 3 : (nm >= 4 ? 2 : 1);
                if (g_pcopy_avail) g_nmid = g_pcopy_k;
                if (g_cfg.reg_mode == 1 && (g_cfg.manual_mode || g_game_idx_hi > g_ngame) && 16 - nm >= g_ngame) {
                    int base = 16 - nm;
                    int32_t r0 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                               handle, startIndex, addresses, bufferNum, attr);
                    if (r0 >= 0 && g_ext_attr_slot > 0) {
                        sceVideoOutUnregisterBuffers(handle, g_ext_attr_slot);
                        g_ext_attr_slot = -1;
                    }
                    int32_t r1 = r0 < 0 ? r0 : HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                                             handle, base, ext + g_ngame, nm, attr);
                    if (r0 >= 0 && r1 == (int32_t)0x80290010) {
                        for (int sl = 1; sl < 4; sl++) sceVideoOutUnregisterBuffers(handle, sl);
                        r1 = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                           handle, base, ext + g_ngame, nm, attr);
                    }
                    lg("mbufs: %d extra buffer(s) of 0x%zx in separate top group at index %d (reg_mode=1): game=0x%08x, ours=0x%08x\n",
                       nm, one, base, (unsigned)r0, (unsigned)r1);
                    STAGE("RegisterBuffers done (reg_mode=1 separate group at %d, %d x 0x%zx): game=0x%08x, ours=0x%08x",
                          base, nm, one, (unsigned)r0, (unsigned)r1);
                    if (r0 >= 0 && (r1 >= 0 || r1 == (int32_t)0x80290010)) {
                        if (r1 >= 0) g_ext_attr_slot = r1;
                        g_mbase = base;
                        for (int i = 0; i < nm; i++) g_buf[base + i] = (uint64_t)(uintptr_t)ext[g_ngame + i];
                        g_nbuf = base + nm;
                        g_mbuf_state = 1;
                        s14_after_register(nm, one, m, base + 1);
                        if (g_flips >= INJECT_AFTER_FLIPS) {
                            gv_set_display(g_buf, g_nbuf, g_bw, g_bh);
                            if (g_fmt_supported && fg_res_supported(g_bw, g_bh) &&
                                (strncmp(g_inert_reason, "INERT: NO MBUF", 14) == 0 ||
                                 strncmp(g_inert_reason, "INERT: HDR/FMT", 14) == 0)) {
                                g_inert_reason[0] = 0;
                            }
                        }
                        on_register_buffers_2buf(g_ngame, one);
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
                    s14_after_register(nm, one, m, g_ngame + 1);
                    if (g_flips >= INJECT_AFTER_FLIPS) {
                        gv_set_display(g_buf, g_nbuf, g_bw, g_bh);
                        if (g_fmt_supported && fg_res_supported(g_bw, g_bh) &&
                            (strncmp(g_inert_reason, "INERT: NO MBUF", 14) == 0 ||
                             strncmp(g_inert_reason, "INERT: HDR/FMT", 14) == 0)) {
                            g_inert_reason[0] = 0;
                        }
                    }
                    on_register_buffers_2buf(g_ngame, one);
                    return r;
                }
            }
            if (g_mbuf_state != 1) {   // no own buffers: the game's registration goes through unchanged, frames stay native
                g_mbase = 16; g_nmid = 0; g_pcopy_avail = 0; g_pcopy_on = 0;
                on_register_buffers_2buf(0, 0);
                lg("mbufs: no extra display buffers (stride 0x%zx) -> native, the game's registration unchanged\n", one);
            }
        }
    }
    int32_t rn = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                               handle, startIndex, addresses, bufferNum, attr);
    if (rn >= 0 && need_rereg_ext_fmt && attr && g_mbuf_state == 1 && g_ext_nm >= 1) {
        if (g_ext_attr_slot > 0) {
            sceVideoOutUnregisterBuffers(handle, g_ext_attr_slot);
            g_ext_attr_slot = -1;
        }
        int32_t r_ext = HOOK_CONTINUE(sceVideoOutRegisterBuffers, int32_t (*)(int32_t, int32_t, void *const *, int32_t, const VoAttr *),
                                      handle, g_mbase, g_ext_va, g_ext_nm, attr);
        if (r_ext >= 0) g_ext_attr_slot = r_ext;
        if (g_fmt_supported && fg_res_supported(g_bw, g_bh) &&
            strncmp(g_inert_reason, "INERT: HDR/FMT", 14) == 0) {
            g_inert_reason[0] = 0;
        }
        STAGE("RegisterBuffers re-registered our %d buffer(s) at index %d for format switch fmt=0x%x (dfmt=%u) = 0x%08x",
              g_ext_nm, g_mbase, attr->format, g_t_dfmt, (unsigned)r_ext);
        lg("mbufs: re-registered %d buffer(s) at index %d with new fmt=0x%x (dfmt=%u) after start=%d..%d format switch (r=0x%08x)\n",
           g_ext_nm, g_mbase, attr->format, g_t_dfmt, startIndex, startIndex + bufferNum - 1, (unsigned)r_ext);
    }
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
static volatile int g_in_pad_tid[HOOK_GUARD_SLOTS];
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
    case 8: c->below30 = !c->below30; break;
    case 9: c->shallow_queue = !c->shallow_queue; break;
    case 10: c->menu_opacity += dir * 5; if (c->menu_opacity < 20) c->menu_opacity = 20; if (c->menu_opacity > 100) c->menu_opacity = 100; break;
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
    int slot = tid_guard_enter(g_in_pad_tid, scePthreadGetthreadid());
    if (slot == -1) return HOOK_CONTINUE(scePadReadState, int32_t (*)(int32_t, void *), handle, data);
    int32_t r = HOOK_CONTINUE(scePadReadState, int32_t (*)(int32_t, void *), handle, data);
    if (r == 0 && data) pad_menu_step((PadDataRaw *)data, 1);
    tid_guard_leave(g_in_pad_tid, slot);
    return r;
}

int32_t scePadRead_hook(int32_t handle, void *data, int32_t num)
{
    int slot = tid_guard_enter(g_in_pad_tid, scePthreadGetthreadid());
    if (slot == -1) return HOOK_CONTINUE(scePadRead, int32_t (*)(int32_t, void *, int32_t), handle, data, num);
    int32_t r = HOOK_CONTINUE(scePadRead, int32_t (*)(int32_t, void *, int32_t), handle, data, num);
    if (r > 0 && data) pad_menu_step((PadDataRaw *)data, r > 64 ? 64 : r);
    tid_guard_leave(g_in_pad_tid, slot);
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
    uint32_t cpu_n = 0;
    for (;;) {
        sceKernelUsleep(16000);
        g_cc_pad_wakes++;
        if ((++cpu_n & 63u) == 0) __atomic_store_n(&g_cc_pad_cpu_us, cc_cpu_us(CC_CLK_THREAD), __ATOMIC_RELEASE);
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
    if (g_dbg_hooks) crash_handlers_install();
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
    check_title_version_upgrade();
    load_early_adapt_cfg();
    STAGE("plugin_load enter: title=%s (family=%s) version=" VCFG_TRACE_VERSION " engine=classic neo=%d dm=0x%zx shave_mode=%d reg_mode=%d pace_order=%d post_mid=%d",
          g_title_id, vcfg_title_family(g_title_id), (int)g_is_neo, (size_t)sceKernelGetDirectMemorySize(),
          g_cfg.shave_mode, g_cfg.reg_mode, g_cfg.pace_order, g_cfg.post_mid);
    if (strcmp(g_title_id, "CUSA10249") == 0) {
        STAGE("audio_fix=%d", g_cfg.audio_fix);
    }
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
        if (!g_bench_enabled) {
            // Progressive self-adapting boot pipeline for all titles:
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
            } else if (st == 1 || g_cfg.reg_mode == 2) {
                g_safe = 1;
                g_safe_early = 1;
                safe_marker(1);
                STAGE("safe mode: title %s in state=%d (reg_mode=%d) -> deferred RegisterBuffers/SetFlipRate to frame %d",
                      g_title_id, st, g_cfg.reg_mode, INJECT_AFTER_FLIPS);
            } else {
                g_safe = 0;
                g_safe_early = 1;
                safe_marker(1);
                STAGE("safe mode: title %s in state=0 -> universal early pipeline active (boot marker armed until frame %d)",
                      g_title_id, INJECT_AFTER_FLIPS);
            }
        }
        int late_alloc = (!g_bench_enabled &&
                          (g_cfg.shave_mode == 2 || (g_cfg.shave_mode == 1 && g_cfg.reg_mode == 2)));
        s14_decide(m);
        if (late_alloc) {
            STAGE("dev override: late_alloc active (shave_mode=%d reg_mode=%d) -> skipping all early DirectMemory allocations in plugin_load",
                  g_cfg.shave_mode, g_cfg.reg_mode);
        }
        if (m == 8 && !g_bench_enabled) gv_load(alloc_gv_dm);
#if VCFG_BENCH
        // capture memory (~50 MB) is taken later, from bench_flip once the game runs: taking it here crashed the game (run d, 2026-10-20)
#endif
        int stream_3buf_early = (vcfg_in_family(g_title_id, "CUSA09311") ||
                                 vcfg_in_family(g_title_id, "CUSA18534") ||
                                 vcfg_in_family(g_title_id, "CUSA10123"));
        if ((m == 4 || m >= 6) && !g_bench_enabled && !late_alloc) {
            // Allocate g_pre_mem FIRST at the top of the DirectMemory pool before g_pre_gpu and g_pre_work so
            // physical address 0x0 remains 100% unfragmented and g_pre_gpu + g_pre_work can pack together into the
            // single 2 MB page immediately below g_pre_mem_phys.
            size_t dm_sz = sceKernelGetDirectMemorySize();
            long top_e = (long)(dm_sz & ~(size_t)0x1FFFFFu);
            const size_t try_sz[3] = { 0x2000000u, 0x1000000u, 0x800000u };
            int start_ti = (stream_3buf_early || cusa10249_audio_fix()) ? 2 : 0;
            int r = -1;
            for (int ti = start_ti; ti < 3 && !g_pre_mem; ti++) {
                size_t total = try_sz[ti];
                long hi_s = top_e > (long)total ? (top_e - (long)total) : 0;
                for (int hi = (hi_s > 0 ? 1 : 0); hi >= 0 && !g_pre_mem; hi--) {
                    long phys = 0; void *addr = NULL;
                    long s = hi ? hi_s : 0;
                    long e = hi ? top_e : (long)dm_sz;
                    g_own_alloc++;
                    r = sceKernelAllocateDirectMemory(s, e, total, 0x200000, 3 /*WC_GARLIC*/, &phys);
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
                    STAGE("early display memory try %d (%s): len=0x%zx align=0x200000 type=3 range=0x%lx..0x%lx r=0x%08x phys=0x%lx%s",
                          ti + 1, hi ? "top" : "full", total, s, e, (unsigned)r, g_pre_mem ? phys : 0L,
                          g_pre_mem ? " ok" : "");
                }
            }
            klog("[bb_probe] early display-buffer memory: %s (size=0x%zx, r=0x%08x)\n", g_pre_mem ? "ok" : "FAILED", g_pre_size, (unsigned)r);
        }
        if (m >= 1 && m <= 8 && !late_alloc) {
            g_gpu_sz = gv_armed() ? GPU_SZ : GPU_SZ_BASE;
            size_t dm_sz = sceKernelGetDirectMemorySize();
            off_t first_2mb_start = 0;
            size_t tot_2mb = dm_avail_range(0, (long)dm_sz, 0x200000u, &first_2mb_start);
            g_low_sub2mb_end = (tot_2mb > 0 && first_2mb_start >= 0x200000L) ? (long)first_2mb_start : 0x200000L;
            if (cusa10249_audio_fix()) {
                STAGE("A10249 audio_fix=%d: skipping early g_pre_gpu/g_pre_work in plugin_load (lazy init at f:120)", g_cfg.audio_fix);
                a10249_log_budget("plugin_load");
            } else {
                STAGE("alloc gpu sz=0x%zx (mode %d)", (size_t)g_gpu_sz, m);
                if (!g_pre_gpu && vcfg_in_family(g_title_id, "CUSA04529") && tot_2mb > 0 && first_2mb_start >= 0x200000L) {
                    size_t need_sub = g_gpu_sz + ((m >= 6) ? ((m == 8) ? WORK_SZ_8 : WORK_SZ) : 0u);
                    long sub_top_s = (long)first_2mb_start - 0x200000L;
                    size_t sub_av = dm_avail_range(sub_top_s, (long)first_2mb_start, 0x10000u, NULL);
                    if (sub_av >= need_sub) {
                        g_pre_gpu = alloc_dm_range(sub_top_s, (long)first_2mb_start, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
                    }
                }
                if (!g_pre_gpu && g_pre_mem && g_pre_mem_phys >= 0x200000L) {
                    g_pre_gpu = alloc_dm_range(g_pre_mem_phys - (long)g_gpu_sz, g_pre_mem_phys, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
                    if (!g_pre_gpu) {
                        g_pre_gpu = alloc_dm_range(g_pre_mem_phys - 0x200000L, g_pre_mem_phys, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
                    }
                }
                if (!g_pre_gpu) {
                    long top_e = (long)(dm_sz & ~(size_t)0x1FFFFFu);
                    if (top_e >= 0x200000L) {
                        g_pre_gpu = alloc_dm_range(top_e - (long)g_gpu_sz, top_e, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
                        if (!g_pre_gpu) {
                            g_pre_gpu = alloc_dm_range(top_e - 0x200000L, top_e, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
                        }
                    }
                }
                if (!g_pre_gpu) {
                    g_pre_gpu = alloc_dm_range(0, (long)dm_sz, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
                }
                STAGE("alloc gpu -> %p (phys=0x%lx)", g_pre_gpu, g_pre_gpu_phys);
                {
                    int ff = file_flags();
                    if (!g_bench_enabled && ff > 0 && (ff & 16)) {
                        g_dump_mem = (uint8_t *)alloc_dm_range(0, (long)sceKernelGetDirectMemorySize(), DUMP_SZ, 0x10000, 0, &g_dump_phys);
                        klog("[bb_probe] dump memory: %p\n", (void *)g_dump_mem);
                    }
                }
                if (strcmp(g_title_id, "CUSA10249") == 0) {
                    a10249_log_budget("plugin_load");
                }
            }
        }
        if (m >= 6 && !late_alloc && !cusa10249_audio_fix()) {
            size_t wsz = (m == 8) ? WORK_SZ_8 : WORK_SZ;
            if (!g_pre_work && m == 8) {
                STAGE("alloc work sz=0x%zx garlic", wsz);
                g_pre_work = alloc_work_early(wsz, 3);
                g_work_garlic = (g_pre_work != NULL && g_pre_work_phys >= 0);
            }
            if (!g_pre_work) g_pre_work = alloc_work_early(wsz, 0);
            STAGE("alloc work -> %p (phys=0x%lx) garlic=%d", g_pre_work, g_pre_work_phys, (int)g_work_garlic);
            klog("[bb_probe] early gpu/work memory: %p / %p\n", g_pre_gpu, g_pre_work);
        }
    }
    STAGE("hooking");
    HOOK32(sceGnmSubmitAndFlipCommandBuffers);
    HOOK32(sceGnmSubmitCommandBuffers);
    HOOK32(sceGnmSubmitCommandBuffersForWorkload);
    HOOK32(sceGnmSubmitAndFlipCommandBuffersForWorkload);
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
    release_pbuf();
    debug_hooks_remove();
#if VCFG_2BUF_TEST
    if (g_tb_hooks) {
        UNHOOK(sceVideoOutAddFlipEvent);
        UNHOOK(sceGnmInsertWaitFlipDone);
    }
#endif
    UNHOOK(sceGnmSubmitAndFlipCommandBuffers);
    UNHOOK(sceGnmSubmitCommandBuffers);
    UNHOOK(sceGnmSubmitCommandBuffersForWorkload);
    UNHOOK(sceGnmSubmitAndFlipCommandBuffersForWorkload);
    UNHOOK(sceVideoOutRegisterBuffers);
    UNHOOK(sceVideoOutSetFlipRate);
    UNHOOK(sceVideoOutSubmitFlip);
    if (g_vt_hooked) UNHOOK(sceGnmSubmitDone);
    if (g_log) { fclose(g_log); g_log = NULL; }
    return 0;
}

s32 attr_module_hidden module_start(s64 argc, const void *args) { (void)argc; (void)args; return 0; }
s32 attr_module_hidden module_stop(s64 argc, const void *args) { (void)argc; (void)args; return 0; }
