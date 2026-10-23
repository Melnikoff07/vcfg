// gv: game motion vectors for the mode 8 frame generator (inject letter 'v'). Scanner (pm4scan) -> capture
// (gvc8.ll, spliced into the game's command stream or at the end of the frame) -> calibrator (gvcal, IO thread)
// -> hybrid (gv8.ll on the block field, warp8g.ll per pixel). Everything is off unless 'v' was in
// /data/bb_probe/inject at plugin load; then the optical-flow path is unchanged until a candidate is calibrated, and
// any failure (no candidate, unreadable memory, time budget, bad statistics) falls back to it silently.
// Design and test plan: docs/VELOCITY_RU.md.
#pragma once
#include <stdint.h>

#define GV_MAXE 24          // DCB entries we hand to the driver per submit call (game's + ours)

// letters of /data/bb_probe/inject that gv reads (bit mask for gv_set_letters)
#define GVL_ON      0x01u   // 'v' velocity subsystem on (needed at load for the memory)
#define GVL_NOAPPLY 0x02u   // 'n' shadow mode: everything runs, the field / frame are not changed (A/B)
#define GVL_SCAN    0x04u   // 's' scan a frame now (one shot per appearance)
#define GVL_DUMP    0x08u   // 'V' dump the captured buffers + snapshot to /data/bb_probe/gv_*.bin (one shot)
#define GVL_RECAL   0x10u   // 'r' ignore the stored profile (calibrate again)
#define GVL_SPLIT   0x20u   // 'w' allow cutting INSIDE a DCB entry (packet boundary); default: entry boundaries only
#define GVL_NOSPLICE 0x40u  // 'q' never insert anything into the game's submits: end-of-frame copies in our DCB only
// merged build (v1.14)
#define GVL_ANCHOR  0x80u   // 'a' experiment: anchor (silhouette) penalty in warp8g, also without game vectors (off by default)
#define GVL_WIDE    0x200u  // 'W' calibration mode: walk budget 15 ms per frame and no CPU watchdog (CUSA03041: a frame is ~100k dwords = 6 ms)
#define GVL_VIZ     0x100u  // 'z' debug view: 4 panels (game buffer, our flow, agreement, depth) on generated frames

// plugin_load: allocate the memory (only if 'v'), read title id. Returns 1 if the subsystem is armed.
int  gv_load(void *(*alloc)(size_t len, size_t align, int type));
// gpu_init: where the three shaders were copied (offsets from gpu base) and their RSRC words
void gv_set_shaders(uint8_t *gpu, uint32_t off_gv8, uint32_t off_gvc8, uint32_t off_warp8g, uint32_t off_gvz8);
void gv_set_display(const uint64_t *bufs, int n, uint32_t w, uint32_t h);
// mode 8 became active (flip 120): starts working. 0 = never.
void gv_start(int mode8);
void gv_set_letters(uint32_t l);
int  gv_armed(void);
// both submit hooks: walk the game's entries and possibly insert our fragments. Returns the new entry count
// (arrays od/os/oc/ocs filled, max GV_MAXE) or 0 = pass the game's arrays unchanged.
uint32_t gv_rewrite(uint32_t count, void *dcb[], uint32_t *dcbSz, void *ccb[], uint32_t *ccbSz,
                    void **od, uint32_t *os, void **oc, uint32_t *ocs, int is_flip);
// build_dcb_flow8: end-of-frame captures (call after barrier 1, before our passes set their user data)
uint32_t *gv_emit_eof(uint32_t *p, uint32_t seq, uint32_t slot);
// build_dcb_flow8: table words 100..127 (zeros when off) and the gflags of this frame
void gv_fill_table(uint32_t *tb, uint32_t seq, uint32_t slot);
int  gv_pass_active(void);      // dispatch gv8 after mf8
int  gv_warp_active(void);      // warp8g instead of warp8 (game vectors applied, or the 'a' experiment)
int  gv_viz_active(void);       // gvz8 debug view after the warp ('z')
uint32_t *gv_emit_after_gv8(uint32_t *p, uint32_t seq, uint32_t slot);   // snapshot label, timestamps
uint32_t *gv_emit_ts(uint32_t *p, uint32_t slot, int i);                  // GPU timestamp i (0..3) of this slot
// flip hook end (every flip, injected or not): closes the walker's frame
void gv_frame_end(void);
// flip hook, 3 frames later, same slot as main.c's GPU time read-back
void gv_slot_stats(uint32_t slot);
// IO thread, every 2 ms
void gv_io_tick(void);
// main.c's measured GPU timestamp rate (ticks per second), for the GPU budget watchdog
void gv_set_gpu_rate(double ticks_per_s);
// shader code / RSRC of the new passes (from shaders.h, set by main.c)
extern uint32_t gv_rsrc_gv8[2], gv_rsrc_gvc8[2], gv_rsrc_warp8g[2];
extern uint32_t gv_off_gv8, gv_off_warp8g;
extern volatile int g_gv_cal_winner;
extern volatile int g_gv_cal_locked;
