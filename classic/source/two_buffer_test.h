// Separate, opt-in experiments. The normal build installs no additional hooks.
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
    return VCFG_2BUF_TEST && (!strcmp(g_title_id, "CUSA05571") || !strcmp(g_title_id, "CUSA08809"));
}

#if VCFG_2BUF_TEST
int32_t sceVideoOutAddFlipEvent(OrbisKernelEqueue queue, int32_t handle, void *udata);
HOOK_INIT(sceVideoOutGetFlipStatus);
HOOK_INIT(sceVideoOutAddFlipEvent);
HOOK_INIT(sceGnmInsertWaitFlipDone);
static int g_tb_hooks;
static uint32_t g_tb_status_calls, g_tb_status_errors, g_tb_event_calls, g_tb_wait_calls;
static uint32_t g_tb_wait_mask;
static int64_t g_tb_seen_arg;
static int32_t g_tb_seen_buf = -1;
// What the game saw in its last status poll before it submitted a frame (= the value that let it out of its wait):
// raw queue numbers and, in test 4, the virtual ones it was given. Index 7 = 7 or more.
static int32_t g_tb_last_p = -1, g_tb_last_g = -1, g_tb_last_vp = -1, g_tb_last_vg = -1;
static uint32_t g_tb_exit_p[8], g_tb_exit_g[8], g_tb_exit_vp[8], g_tb_exit_vg[8], g_tb_virt_calls;
static void tb_hist(uint32_t *h, int32_t v) { if (v >= 0) __atomic_add_fetch(&h[v > 7 ? 7 : v], 1u, __ATOMIC_RELAXED); }
// Called by the game's thread when it submits a frame: the last poll before it is the one that released the wait.
static void tb_on_game_submit(void)
{
    if (!g_tb_hooks) return;
    tb_hist(g_tb_exit_p, __atomic_exchange_n(&g_tb_last_p, -1, __ATOMIC_RELAXED));
    tb_hist(g_tb_exit_g, __atomic_exchange_n(&g_tb_last_g, -1, __ATOMIC_RELAXED));
    tb_hist(g_tb_exit_vp, __atomic_exchange_n(&g_tb_last_vp, -1, __ATOMIC_RELAXED));
    tb_hist(g_tb_exit_vg, __atomic_exchange_n(&g_tb_last_vg, -1, __ATOMIC_RELAXED));
}

// Our monitor and GPU builders call the original entry, so their own observations
// do not get counted as the game's status polls or wait packets.
static int32_t read_flip_status(int32_t h, VoFlipStatus *s)
{
    if (g_tb_hooks) return HOOK_CONTINUE(sceVideoOutGetFlipStatus, int32_t (*)(int32_t, VoFlipStatus *), h, s);
    return sceVideoOutGetFlipStatus(h, s);
}
static int32_t insert_wait_flip_done(uint32_t *p, uint32_t n, int32_t h, uint32_t i)
{
    if (g_tb_hooks) return HOOK_CONTINUE(sceGnmInsertWaitFlipDone, int32_t (*)(uint32_t *, uint32_t, int32_t, uint32_t), p, n, h, i);
    return sceGnmInsertWaitFlipDone(p, n, h, i);
}
int32_t sceVideoOutGetFlipStatus_hook(int32_t h, VoFlipStatus *s)
{
    int32_t r = HOOK_CONTINUE(sceVideoOutGetFlipStatus, int32_t (*)(int32_t, VoFlipStatus *), h, s);
    if (h == g_handle) {
        uint32_t n = __atomic_add_fetch(&g_tb_status_calls, 1u, __ATOMIC_RELAXED);
        if (r) __atomic_add_fetch(&g_tb_status_errors, 1u, __ATOMIC_RELAXED);
        if (!r && s) {
            __atomic_store_n(&g_tb_seen_arg, s->flipArg, __ATOMIC_RELAXED);
            __atomic_store_n(&g_tb_seen_buf, s->currentBuffer, __ATOMIC_RELAXED);
            __atomic_store_n(&g_tb_last_p, s->flipPendingNum, __ATOMIC_RELAXED);
            __atomic_store_n(&g_tb_last_g, s->gcQueueNum, __ATOMIC_RELAXED);
            // Experiment 4: with present copy every real frame puts two flips in the queue (our mid, then the copy).
            // The game waits for its queue to drain, ours included; show it one entry per real frame instead.
            if (VCFG_2BUF_TEST == 4 && tb_title() && g_pcopy_on && !g_fg_off) {
                if (s->flipPendingNum > 0) s->flipPendingNum = (s->flipPendingNum + 1) / 2;
                if (s->gcQueueNum > 0) s->gcQueueNum = (s->gcQueueNum + 1) / 2;
                __atomic_add_fetch(&g_tb_virt_calls, 1u, __ATOMIC_RELAXED);
            }
            __atomic_store_n(&g_tb_last_vp, s->flipPendingNum, __ATOMIC_RELAXED);
            __atomic_store_n(&g_tb_last_vg, s->gcQueueNum, __ATOMIC_RELAXED);
        }
        if (n <= 3 && !r && s)
            lg("WAIT_API status caller=%p arg=%lld buf=%d pending=%d count=%llu\n", __builtin_return_address(0),
               (long long)s->flipArg, s->currentBuffer, s->flipPendingNum, (unsigned long long)s->count);
    }
    return r;
}
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
    lg("WAIT_API totals status=%u errors=%u event_subscriptions=%u gpu_wait=%u buffers=0x%x\n",
       __atomic_load_n(&g_tb_status_calls, __ATOMIC_RELAXED), __atomic_load_n(&g_tb_status_errors, __ATOMIC_RELAXED),
       __atomic_load_n(&g_tb_event_calls, __ATOMIC_RELAXED), __atomic_load_n(&g_tb_wait_calls, __ATOMIC_RELAXED),
       __atomic_load_n(&g_tb_wait_mask, __ATOMIC_RELAXED));
    lg("WAIT_API latest_status arg=%lld buf=%d\n", (long long)__atomic_load_n(&g_tb_seen_arg, __ATOMIC_RELAXED),
       __atomic_load_n(&g_tb_seen_buf, __ATOMIC_RELAXED));
    // queue numbers seen at the poll that released the game's wait, counts for 0..7+ (raw | given to the game)
    uint32_t *h[4] = { g_tb_exit_p, g_tb_exit_g, g_tb_exit_vp, g_tb_exit_vg };
    uint32_t v[4][8];
    for (int k = 0; k < 4; k++) for (int i = 0; i < 8; i++) v[k][i] = __atomic_exchange_n(&h[k][i], 0u, __ATOMIC_RELAXED);
    lg("WAIT_EXIT pending %u/%u/%u/%u/%u/%u/%u/%u gcq %u/%u/%u/%u/%u/%u/%u/%u | given pending %u/%u/%u/%u/%u/%u/%u/%u gcq %u/%u/%u/%u/%u/%u/%u/%u | virt %u\n",
       v[0][0], v[0][1], v[0][2], v[0][3], v[0][4], v[0][5], v[0][6], v[0][7],
       v[1][0], v[1][1], v[1][2], v[1][3], v[1][4], v[1][5], v[1][6], v[1][7],
       v[2][0], v[2][1], v[2][2], v[2][3], v[2][4], v[2][5], v[2][6], v[2][7],
       v[3][0], v[3][1], v[3][2], v[3][3], v[3][4], v[3][5], v[3][6], v[3][7],
       __atomic_exchange_n(&g_tb_virt_calls, 0u, __ATOMIC_RELAXED));
}
#else
#define read_flip_status sceVideoOutGetFlipStatus
#define insert_wait_flip_done sceGnmInsertWaitFlipDone
#define tb_report() ((void)0)
#define tb_on_game_submit() ((void)0)
#endif
