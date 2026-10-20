// DirectMemory valve & boot-stage hooks:
// - sceKernelAllocateDirectMemory / MapDirectMemory / MapNamedDirectMemory are always installed on game processes
//   (when not in bench mode) so our early FG reservation is automatically yielded (Memory Valve) or tail-shaved
//   (Arena Shaving) if a tight game requests nearly 100% of DirectMemory during startup.
// - sceVideoOutOpen / sceVideoOutClose are installed when the app's "Debug log" switch is on (LOG_DIR/debug_log).
// Include after boot_stage.h.
#pragma once

int32_t sceVideoOutOpen(int32_t userId, int32_t busType, int32_t index, const void *param);
int32_t sceVideoOutClose(int32_t handle);
HOOK_INIT(sceVideoOutOpen);
HOOK_INIT(sceVideoOutClose);
HOOK_INIT(sceKernelAllocateDirectMemory);
HOOK_INIT(sceKernelMapDirectMemory);
HOOK_INIT(sceKernelMapNamedDirectMemory);
static int g_dbg_hooks;
static int g_dm_hooks;
static volatile int g_own_alloc;            // our own requests are logged by alloc_dm / alloc_mbufs themselves
static volatile uint32_t g_dbg_allocs;
static volatile uint32_t g_dbg_maps;
static volatile uint32_t g_dbg_alloc_fail;  // the game's own refused requests (after valve/shaving): one input of the safe-mode check
#define DBG_ALLOCS_MAX 48                   // the game's first requests only: the start, not every streaming buffer

static long g_shave_phys = 0;
static size_t g_shave_real_len = 0, g_shave_orig_len = 0;
static int g_shave_type = 0;

static int debug_log_on(void)               // raw kernel calls: stdio inside plugin_load faults
{
    int fd = sceKernelOpen(LOG_DIR "/debug_log", 0 /*O_RDONLY*/, 0);
    if (fd < 0) return 0;
    sceKernelClose(fd);
    return 1;
}

static size_t dm_avail_range(long s, long e, size_t align, off_t *start_out)
{
    off_t start = 0; size_t size = 0;
    int r = ((int32_t (*)(off_t, off_t, size_t, off_t *, size_t *))(void *)sceKernelAvailableDirectMemorySize)(
        (off_t)s, (off_t)e, align, &start, &size);
    if (start_out) *start_out = r == 0 ? start : 0;
    return r == 0 ? size : 0;
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
    if (!g_own_alloc && (uint32_t)r == 0x80020023u && g_flips == 0 && g_gpu_state == 0 && g_mbuf_state <= 0) {
        if (g_dump_mem) {
            release_early_dump();
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        // Arena shaving: if a game requests a >= 1 GB 2MB-aligned arena at boot and is short by <= 18 MB because of
        // our FG reservation at the top of the pool, allocate the available contiguous range and alias the tail on map.
        if ((uint32_t)r == 0x80020023u && !g_shave_orig_len && len >= 0x40000000u && align == 0x200000u &&
            (g_pre_mem || g_pre_work)) {
            off_t av_start = 0;
            size_t av = dm_avail_range(s, e, align, &av_start);
            if (av < len && len - av <= 0x1200000u && av >= 0x400000u) {
                int32_t sr = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                                           s, e, av, align, type, phys);
                if (sr == 0 && phys) {
                    g_shave_phys = *phys;
                    g_shave_real_len = av;
                    g_shave_orig_len = len;
                    g_shave_type = type;
                    r = 0;
                    STAGE("arena shave OK: game asked len=0x%zx, allocated 0x%zx at phys=0x%lx (shaved 0x%zx for FG)",
                          len, av, *phys, len - av);
                }
            }
        }
        // Memory valve: if the allocation still failed, yield our early FG buffers step by step and retry.
        if ((uint32_t)r == 0x80020023u && g_pre_mem) {
            release_early_mem();
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        if ((uint32_t)r == 0x80020023u && (g_pre_work || g_pre_gpu)) {
            release_early_work();
            release_early_gpu();
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
    }
    if (!g_own_alloc && r != 0) __atomic_add_fetch(&g_dbg_alloc_fail, 1u, __ATOMIC_RELAXED);
    if (!g_own_alloc && __atomic_add_fetch(&g_dbg_allocs, 1u, __ATOMIC_RELAXED) <= DBG_ALLOCS_MAX)
        STAGE("game AllocateDirectMemory(len=0x%zx align=0x%zx type=%d range=0x%lx..0x%lx) = 0x%08x phys=0x%lx caller=%p",
              len, align, type, s, e, (unsigned)r, (r == 0 && phys) ? *phys : 0L, __builtin_return_address(0));
    return r;
}

static int32_t call_orig_map(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize,
                             const char *name, int is_named)
{
    if (is_named)
        return HOOK_CONTINUE(sceKernelMapNamedDirectMemory,
                             int32_t (*)(void **, size_t, int, int, long, size_t, const char *),
                             addr, len, prot, flags, dmStart, maxPageSize, name);
    return HOOK_CONTINUE(sceKernelMapDirectMemory,
                         int32_t (*)(void **, size_t, int, int, long, size_t),
                         addr, len, prot, flags, dmStart, maxPageSize);
}

static int32_t handle_game_map(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize,
                               const char *name, int is_named)
{
    void *orig_addr_in = addr ? *addr : NULL;
    if (!g_own_alloc && g_shave_orig_len > 0) {
        long shave_cut = g_shave_phys + (long)g_shave_real_len;
        long shave_end = g_shave_phys + (long)g_shave_orig_len;
        long req_end = dmStart + (long)len;
        if (dmStart >= g_shave_phys && dmStart < shave_end && req_end > shave_cut && req_end <= shave_end) {
            size_t real_part = dmStart < shave_cut ? (size_t)(shave_cut - dmStart) : 0;
            size_t tail_part = len - real_part;
            int32_t r = 0;
            if (real_part > 0)
                r = call_orig_map(addr, real_part, prot, flags, dmStart, maxPageSize, name, is_named);
            if (real_part > 0 && r == 0 && addr && *addr) {
                void *want_va = (uint8_t *)(*addr) + real_part;
                void *got_va = want_va;
                int32_t rf = sceKernelMapFlexibleMemory(&got_va, tail_part, prot, flags | 0x10 /*MAP_FIXED*/);
                if (rf != 0 || got_va != want_va) {
                    if (rf == 0 && got_va) sceKernelMunmap(got_va, tail_part);
                    got_va = want_va;
                    rf = sceKernelMapFlexibleMemory(&got_va, tail_part, prot, flags);
                    if (rf == 0 && got_va != want_va) {
                        sceKernelMunmap(got_va, tail_part);
                        rf = -1;
                    }
                }
                STAGE("arena shave map OK: va=%p real=0x%zx shaved_tail=0x%zx flex_tail=%s (rf=0x%08x)",
                      *addr, real_part, tail_part, (rf == 0 && got_va == want_va) ? "mapped" : "unmapped-guard", (unsigned)rf);
                if (!g_own_alloc && __atomic_add_fetch(&g_dbg_maps, 1u, __ATOMIC_RELAXED) <= DBG_ALLOCS_MAX)
                    STAGE("game %s(phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx in=%p) = 0x00000000 va=%p (shaved)",
                          is_named ? "MapNamedDirectMemory" : "MapDirectMemory", dmStart, len, prot, flags, maxPageSize,
                          orig_addr_in, *addr);
                return 0;
            }
            if (real_part == 0 && addr) {
                int32_t rf = sceKernelMapFlexibleMemory(addr, tail_part, prot, flags);
                if (rf == 0 && *addr) {
                    STAGE("arena shave tail-only flex map OK: va=%p len=0x%zx", *addr, tail_part);
                    return 0;
                }
            }
            if (addr) *addr = orig_addr_in;
            if (dmStart == g_shave_phys && g_flips == 0 && g_gpu_state == 0 && g_mbuf_state <= 0) {
                int rr = sceKernelReleaseDirectMemory(g_shave_phys, g_shave_real_len);
                release_early_mem();
                release_early_work();
                long full_phys = 0;
                int ar = HOOK_CONTINUE(sceKernelAllocateDirectMemory,
                                       int32_t (*)(long, long, size_t, size_t, int, long *),
                                       g_shave_phys, shave_end, g_shave_orig_len, 0x200000u, g_shave_type, &full_phys);
                STAGE("arena shave fallback to valve: release_shave=0x%08x realloc_full=0x%08x phys=0x%lx",
                      (unsigned)rr, (unsigned)ar, full_phys);
                g_shave_orig_len = 0;
                g_shave_real_len = 0;
                if (ar == 0)
                    dmStart = full_phys;
            }
        }
    }
    int32_t r = call_orig_map(addr, len, prot, flags, dmStart, maxPageSize, name, is_named);
    if (!g_own_alloc && __atomic_add_fetch(&g_dbg_maps, 1u, __ATOMIC_RELAXED) <= DBG_ALLOCS_MAX)
        STAGE("game %s(phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx in=%p) = 0x%08x va=%p",
              is_named ? "MapNamedDirectMemory" : "MapDirectMemory", dmStart, len, prot, flags, maxPageSize,
              orig_addr_in, (unsigned)r, (r == 0 && addr) ? *addr : NULL);
    return r;
}

int32_t sceKernelMapDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize)
{
    return handle_game_map(addr, len, prot, flags, dmStart, maxPageSize, NULL, 0);
}
int32_t sceKernelMapNamedDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize, const char *name)
{
    return handle_game_map(addr, len, prot, flags, dmStart, maxPageSize, name, 1);
}

static void debug_hooks_install(void)
{
    HOOK32(sceKernelAllocateDirectMemory);
    HOOK32(sceKernelMapDirectMemory);
    HOOK32(sceKernelMapNamedDirectMemory);
    g_dm_hooks = 1;
    if (!debug_log_on()) return;
    HOOK32(sceVideoOutOpen);
    HOOK32(sceVideoOutClose);
    g_dbg_hooks = 1;
    STAGE("debug hooks installed (VideoOutOpen, VideoOutClose, Allocate/MapDirectMemory: first %d game requests)", DBG_ALLOCS_MAX);
}
static void debug_hooks_remove(void)
{
    if (g_dm_hooks) {
        UNHOOK(sceKernelAllocateDirectMemory);
        UNHOOK(sceKernelMapDirectMemory);
        UNHOOK(sceKernelMapNamedDirectMemory);
        g_dm_hooks = 0;
    }
    if (!g_dbg_hooks) return;
    UNHOOK(sceVideoOutOpen);
    UNHOOK(sceVideoOutClose);
    g_dbg_hooks = 0;
}
