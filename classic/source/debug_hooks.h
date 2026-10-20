// Debug-log-only hooks: installed only while the app's "Debug log" switch is on (LOG_DIR/debug_log), so a normal
// start installs none of them. They record what the game itself asks VideoOut and the direct-memory allocator for
// during its start, into the crash-safe boot-<TITLE>.txt (B01/B05: starts that end before the first frame, refused
// memory). Include after boot_stage.h.
#pragma once

int32_t sceVideoOutOpen(int32_t userId, int32_t busType, int32_t index, const void *param);
int32_t sceVideoOutClose(int32_t handle);
HOOK_INIT(sceVideoOutOpen);
HOOK_INIT(sceVideoOutClose);
HOOK_INIT(sceKernelAllocateDirectMemory);
static int g_dbg_hooks;
static volatile int g_own_alloc;            // our own requests are logged by alloc_dm / alloc_mbufs themselves
static volatile uint32_t g_dbg_allocs;
static volatile uint32_t g_dbg_alloc_fail;  // the game's own refused requests (debug hooks on only): one input of the safe-mode check
#define DBG_ALLOCS_MAX 48                   // the game's first requests only: the start, not every streaming buffer

static int debug_log_on(void)               // raw kernel calls: stdio inside plugin_load faults
{
    int fd = sceKernelOpen(LOG_DIR "/debug_log", 0 /*O_RDONLY*/, 0);
    if (fd < 0) return 0;
    sceKernelClose(fd);
    return 1;
}

int32_t sceVideoOutOpen_hook(int32_t u, int32_t bus, int32_t idx, const void *p)
{
    int32_t r = HOOK_CONTINUE(sceVideoOutOpen, int32_t (*)(int32_t, int32_t, int32_t, const void *), u, bus, idx, p);
    STAGE("game VideoOutOpen(user=%d bus=%d index=%d param=%p) = %d", u, bus, idx, p, r);
    return r;
}
int32_t sceVideoOutClose_hook(int32_t h)
{
    STAGE("game VideoOutClose(handle=%d)", h);
    int32_t r = HOOK_CONTINUE(sceVideoOutClose, int32_t (*)(int32_t), h);
    STAGE("game VideoOutClose done = 0x%08x", (unsigned)r);
    return r;
}
int32_t sceKernelAllocateDirectMemory_hook(long s, long e, size_t len, size_t align, int type, long *phys)
{
    int32_t r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
    if (!g_own_alloc && r != 0) __atomic_add_fetch(&g_dbg_alloc_fail, 1u, __ATOMIC_RELAXED);
    if (!g_own_alloc && __atomic_add_fetch(&g_dbg_allocs, 1u, __ATOMIC_RELAXED) <= DBG_ALLOCS_MAX)
        STAGE("game AllocateDirectMemory(len=0x%zx align=0x%zx type=%d range=0x%lx..0x%lx) = 0x%08x phys=0x%lx caller=%p",
              len, align, type, s, e, (unsigned)r, (r == 0 && phys) ? *phys : 0L, __builtin_return_address(0));
    return r;
}

static void debug_hooks_install(void)
{
    if (!debug_log_on()) return;
    HOOK32(sceVideoOutOpen);
    HOOK32(sceVideoOutClose);
    HOOK32(sceKernelAllocateDirectMemory);
    g_dbg_hooks = 1;
    STAGE("debug hooks installed (VideoOutOpen, VideoOutClose, AllocateDirectMemory: first %d game requests)", DBG_ALLOCS_MAX);
}
static void debug_hooks_remove(void)
{
    if (!g_dbg_hooks) return;
    UNHOOK(sceVideoOutOpen);
    UNHOOK(sceVideoOutClose);
    UNHOOK(sceKernelAllocateDirectMemory);
}
