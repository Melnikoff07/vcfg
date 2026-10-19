// Separate, opt-in experiments. The normal build installs no additional hooks.
#pragma once
#include "two_buffer_policy.h"
#ifndef VCFG_2BUF_TEST
#define VCFG_2BUF_TEST 0
#endif
#if VCFG_2BUF_TEST < 0 || VCFG_2BUF_TEST > 3
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
}
#else
#define read_flip_status sceVideoOutGetFlipStatus
#define insert_wait_flip_done sceGnmInsertWaitFlipDone
#define tb_report() ((void)0)
#endif
