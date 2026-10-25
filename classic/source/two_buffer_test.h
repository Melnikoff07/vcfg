// Two-buffer games: the virtual flip queue (normal builds, per title: two_buffer=1 in config-<TITLE>.ini, set by the
// app's "2-BUFFER" engine choice) and the separate, opt-in test experiments (VCFG_2BUF_TEST builds only).
//
// Why: with present copy every real frame puts two flips into the queue (our generated frame, then the copy of the
// real one). A double-buffered game polls sceVideoOutGetFlipStatus until its queue is short enough before it renders
// the next frame (CUSA05571: it goes on at 2 entries; we had 4), so it waited for our frames too and ran at 24 fps
// instead of 30. The game is shown one entry per real frame instead (test 4 on hardware: 30 fps, real frames held one
// refresh 97-100%). Only while present copy is active and FG is on; every other game and state reads the real status.
// A build that installs no status hook (no two_buffer file, not a test title) behaves exactly as before.
#pragma once
#include "two_buffer_policy.h"
#ifndef VCFG_2BUF_TEST
#define VCFG_2BUF_TEST 0
#endif
#if VCFG_2BUF_TEST < 0 || VCFG_2BUF_TEST > 4
#error invalid VCFG_2BUF_TEST
#endif

static int tb_title(void)
{
    return VCFG_2BUF_TEST && (vcfg_in_family(g_title_id, "CUSA05571") || vcfg_in_family(g_title_id, "CUSA08809"));
}

HOOK_INIT(sceVideoOutGetFlipStatus);
static int g_status_hooked;   // sceVideoOutGetFlipStatus is hooked (virtual queue or a test build on a test title)
static int g_vq_on;           // two_buffer=1 for this title (read in plugin_load or auto-armed on 2-buffer detection)
static int g_runtime_2buf;    // 1: 2-buffer display ring detected via RegisterBuffers(count==2) or flip index ring
static int vq_active(void) { return (g_vq_on || g_ngame == 2 || g_runtime_2buf || (VCFG_2BUF_TEST == 4 && tb_title())) && (g_pcopy_on || g_mbuf_state == 1) && !g_fg_off; }

// What the game saw in its last status poll before it submitted a frame (= the value that let it out of its wait):
// raw queue numbers and the ones it was given. Index 7 = 7 or more.
static int32_t g_tb_last_p = -1, g_tb_last_g = -1, g_tb_last_vp = -1, g_tb_last_vg = -1;
static uint32_t g_tb_exit_p[8], g_tb_exit_g[8], g_tb_exit_vp[8], g_tb_exit_vg[8], g_tb_virt_calls, g_tb_status_calls, g_tb_status_logged;
static void tb_hist(uint32_t *h, int32_t v) { if (v >= 0) __atomic_add_fetch(&h[v > 7 ? 7 : v], 1u, __ATOMIC_RELAXED); }
// Called by the game's thread when it submits a frame: the last poll before it is the one that released the wait.
static void tb_on_game_submit(void)
{
    if (!g_status_hooked) return;
    tb_hist(g_tb_exit_p, __atomic_exchange_n(&g_tb_last_p, -1, __ATOMIC_RELAXED));
    tb_hist(g_tb_exit_g, __atomic_exchange_n(&g_tb_last_g, -1, __ATOMIC_RELAXED));
    tb_hist(g_tb_exit_vp, __atomic_exchange_n(&g_tb_last_vp, -1, __ATOMIC_RELAXED));
    tb_hist(g_tb_exit_vg, __atomic_exchange_n(&g_tb_last_vg, -1, __ATOMIC_RELAXED));
}
// Our monitor calls the original entry, so its own observations are neither counted nor virtualized.
static int32_t read_flip_status(int32_t h, VoFlipStatus *s)
{
    if (g_status_hooked) return HOOK_CONTINUE(sceVideoOutGetFlipStatus, int32_t (*)(int32_t, VoFlipStatus *), h, s);
    return sceVideoOutGetFlipStatus(h, s);
}

#if VCFG_2BUF_TEST
int32_t sceVideoOutAddFlipEvent(OrbisKernelEqueue queue, int32_t handle, void *udata);
HOOK_INIT(sceVideoOutAddFlipEvent);
HOOK_INIT(sceGnmInsertWaitFlipDone);
static int g_tb_hooks;
static uint32_t g_tb_status_errors, g_tb_event_calls, g_tb_wait_calls;
static uint32_t g_tb_wait_mask;
static int64_t g_tb_seen_arg;
static int32_t g_tb_seen_buf = -1;
static int32_t insert_wait_flip_done(uint32_t *p, uint32_t n, int32_t h, uint32_t i)
{
    if (g_tb_hooks) return HOOK_CONTINUE(sceGnmInsertWaitFlipDone, int32_t (*)(uint32_t *, uint32_t, int32_t, uint32_t), p, n, h, i);
    return sceGnmInsertWaitFlipDone(p, n, h, i);
}
#else
#define insert_wait_flip_done sceGnmInsertWaitFlipDone
#endif

int32_t sceVideoOutGetFlipStatus_hook(int32_t h, VoFlipStatus *s)
{
    int32_t r = HOOK_CONTINUE(sceVideoOutGetFlipStatus, int32_t (*)(int32_t, VoFlipStatus *), h, s);
    if (h == g_handle) {
        uint32_t n = __atomic_add_fetch(&g_tb_status_calls, 1u, __ATOMIC_RELAXED);
#if VCFG_2BUF_TEST
        if (r) __atomic_add_fetch(&g_tb_status_errors, 1u, __ATOMIC_RELAXED);
#endif
        if (!r && s) {
#if VCFG_2BUF_TEST
            __atomic_store_n(&g_tb_seen_arg, s->flipArg, __ATOMIC_RELAXED);
            __atomic_store_n(&g_tb_seen_buf, s->currentBuffer, __ATOMIC_RELAXED);
#endif
            __atomic_store_n(&g_tb_last_p, s->flipPendingNum, __ATOMIC_RELAXED);
            __atomic_store_n(&g_tb_last_g, s->gcQueueNum, __ATOMIC_RELAXED);
            if (vq_active()) {   // one queue entry per real frame: our generated frame is not the game's
                int on_our_buf = (g_mbuf_state == 1 && g_mbase > 0 && s->currentBuffer >= g_mbase && s->currentBuffer < g_nbuf);
                if (on_our_buf && s->flipPendingNum == 1 && s->gcQueueNum == 0) {
                    s->flipPendingNum = 0;
                } else if (s->flipPendingNum > 0) {
                    s->flipPendingNum = (s->flipPendingNum + 1) / 2;
                }
                if (s->gcQueueNum > 0) s->gcQueueNum = (s->gcQueueNum + 1) / 2;
                if (on_our_buf && (g_ngame == 2 || g_runtime_2buf || g_vq_on)) {
                    s->currentBuffer = (g_prev_idx < (uint32_t)g_ngame) ? (int32_t)g_prev_idx : 0;
                }
                __atomic_add_fetch(&g_tb_virt_calls, 1u, __ATOMIC_RELAXED);
            }
            __atomic_store_n(&g_tb_last_vp, s->flipPendingNum, __ATOMIC_RELAXED);
            __atomic_store_n(&g_tb_last_vg, s->gcQueueNum, __ATOMIC_RELAXED);
        }
        if (n <= 3 && !r && s && __atomic_add_fetch(&g_tb_status_logged, 1u, __ATOMIC_RELAXED) <= 3u)
            lg("WAIT_API status caller=%p arg=%lld buf=%d pending=%d count=%llu\n", __builtin_return_address(0),
               (long long)s->flipArg, s->currentBuffer, s->flipPendingNum, (unsigned long long)s->count);
    }
    return r;
}

// Every 2 s from the monitor: the queue numbers at the poll that released the game's wait (raw | given to the game).
static void vq_report(void)
{
    if (!g_status_hooked) return;
    uint32_t *h[4] = { g_tb_exit_p, g_tb_exit_g, g_tb_exit_vp, g_tb_exit_vg };
    uint32_t v[4][8];
    for (int k = 0; k < 4; k++) for (int i = 0; i < 8; i++) v[k][i] = __atomic_exchange_n(&h[k][i], 0u, __ATOMIC_RELAXED);
    lg("WAIT_EXIT pending %u/%u/%u/%u/%u/%u/%u/%u gcq %u/%u/%u/%u/%u/%u/%u/%u | given pending %u/%u/%u/%u/%u/%u/%u/%u gcq %u/%u/%u/%u/%u/%u/%u/%u | virt %u polls %u\n",
       v[0][0], v[0][1], v[0][2], v[0][3], v[0][4], v[0][5], v[0][6], v[0][7],
       v[1][0], v[1][1], v[1][2], v[1][3], v[1][4], v[1][5], v[1][6], v[1][7],
       v[2][0], v[2][1], v[2][2], v[2][3], v[2][4], v[2][5], v[2][6], v[2][7],
       v[3][0], v[3][1], v[3][2], v[3][3], v[3][4], v[3][5], v[3][6], v[3][7],
       __atomic_exchange_n(&g_tb_virt_calls, 0u, __ATOMIC_RELAXED), __atomic_exchange_n(&g_tb_status_calls, 0u, __ATOMIC_RELAXED));
}

#if VCFG_2BUF_TEST
int32_t sceVideoOutAddFlipEvent_hook(OrbisKernelEqueue q, int32_t h, void *u)
{
    int32_t r = HOOK_CONTINUE(sceVideoOutAddFlipEvent, int32_t (*)(OrbisKernelEqueue, int32_t, void *), q, h, u);
    // A subscription may precede the first buffer registration.
    uint32_t n = __atomic_add_fetch(&g_tb_event_calls, 1u, __ATOMIC_RELAXED);
    if (n <= 3) lg("WAIT_API event caller=%p handle=%d rc=0x%08x\n", __builtin_return_address(0), h, (unsigned)r);
    return r;
}
int32_t sceGnmInsertWaitFlipDone_hook(uint32_t *p, uint32_t n, int32_t h, uint32_t i)
{
    int32_t r = HOOK_CONTINUE(sceGnmInsertWaitFlipDone, int32_t (*)(uint32_t *, uint32_t, int32_t, uint32_t), p, n, h, i);
    if (h == g_handle) {
        uint32_t k = __atomic_add_fetch(&g_tb_wait_calls, 1u, __ATOMIC_RELAXED);
        if (i < 32) __atomic_fetch_or(&g_tb_wait_mask, 1u << i, __ATOMIC_RELAXED);
        if (k <= 3) lg("WAIT_API gpu_wait caller=%p buf=%u rc=0x%08x\n", __builtin_return_address(0), i, (unsigned)r);
    }
    return r;
}
static void tb_report(void)
{
    if (!g_tb_hooks) return;
    lg("WAIT_API totals errors=%u event_subscriptions=%u gpu_wait=%u buffers=0x%x\n",
       __atomic_load_n(&g_tb_status_errors, __ATOMIC_RELAXED),
       __atomic_load_n(&g_tb_event_calls, __ATOMIC_RELAXED), __atomic_load_n(&g_tb_wait_calls, __ATOMIC_RELAXED),
       __atomic_load_n(&g_tb_wait_mask, __ATOMIC_RELAXED));
    lg("WAIT_API latest_status arg=%lld buf=%d\n", (long long)__atomic_load_n(&g_tb_seen_arg, __ATOMIC_RELAXED),
       __atomic_load_n(&g_tb_seen_buf, __ATOMIC_RELAXED));
}
#else
#define tb_report() ((void)0)
#endif
