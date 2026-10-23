#!/usr/bin/env python3
"""Run the actual classic allocator, Memory Valve, Arena Shaving, and FlexibleMemory GPU fallback
as host LLVM bitcode via lli.exe (matching check_classic_config.py).

Tests:
1. alloc_flex_gpu(sz, align) fallback (PROT_CPU_RW | PROT_GPU_RW = 0x33) & alignment verification
   (256-byte shader code alignment, 32-byte descriptor table alignment, 8-byte EOP label alignment,
   48-bit V# GPU VA range, and clean unmap via free_dm_block).
2. Top-of-pool non-fragmenting early allocation layout + missing-retry bug fix when shrinking
   g_pre_mem 32 MB -> 8 MB.
3. Multi-arena shaving with post-arena reserve (CUSA04529 pattern).
4. Multi-block sequential fixed-chunk allocator (64 MB chunks and 8 MB chunks + while-loop probe
   protection + 1 MB slice map alias protection).
5. Range-constrained DirectMemory relocation (relocate_early_mem_outside).
6. sceKernelAvailableDirectMemorySize_hook & shave_mode=2 (LATE ALLOC) 8 MB preservation.
"""
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")
HOOKS_H = (ROOT / "classic" / "source" / "debug_hooks.h").read_text(encoding="utf-8")


def extract_between(src: str, start_marker: str, end_marker: str) -> str:
    s = src.find(start_marker)
    assert s >= 0, f"start marker not found: {start_marker}"
    e = src.find(end_marker, s)
    assert e > s, f"end marker not found: {end_marker}"
    return src[s:e]


MAIN_ALLOC_PART1 = extract_between(
    MAIN_C,
    "#define FLEX_GPU_MAX 8",
    '#include "debug_hooks.h"',
)
MAIN_ALLOC_PART2 = extract_between(
    MAIN_C,
    "static size_t dm_avail(size_t align)",
    "static void gpu_init(void)",
)
HOOKS_DECLS = extract_between(
    HOOKS_H,
    "#define SHAVE_MAX ",
    "// ---- Lock-free API flight ring buffer",
)
HOOKS_VMAP = extract_between(
    HOOKS_H,
    "// ---- Tracked FlexibleMemory-backed virtual ranges ----",
    "// ---- FreeBSD / OrbisOS amd64 crash signal handler ----",
)
HOOKS_CORE = extract_between(
    HOOKS_H,
    "static size_t dm_avail_range(",
    "static void debug_hooks_install(void)",
)

HARNESS_C = r"""
#include <stdint.h>
#include <stddef.h>

typedef int64_t off_t;
typedef struct { void *FunctionPtr; } Detour;
static int scePthreadGetthreadid(void) { return 1001; }
int32_t sceKernelMapDirectMemory2_hook(void **addr, size_t len, int type, int prot, int flags, long dmStart, size_t maxPageSize);

extern void *memset(void *, int, size_t);
extern void *calloc(size_t, size_t);
extern void free(void *);
extern int printf(const char *, ...);
extern void exit(int);

#define ASSERT(cond) do { \
    if (!(cond)) { \
        printf("ASSERT FAILED at line %d: %s\n", __LINE__, #cond); \
        exit(1); \
    } \
} while (0)

#define GPU_SZ_BASE 0x20000u
#define GPU_SZ      0x40000u
#define WORK_SZ     0x220000u
#define WORK_SZ_8   0x190000u
#define DUMP_SZ     0xC00000u
#define INJECT_AFTER_FLIPS 120

typedef struct {
    int shave_mode;
} fg_config_t;

static fg_config_t g_cfg = { 0 };
static int g_dm_hooks = 1;
static volatile int g_own_alloc = 0;
static uint32_t g_dbg_allocs = 0;
static uint32_t g_dbg_maps = 0;
static uint32_t g_dbg_alloc_fail = 0;
static uint32_t g_flips = 0;
static volatile uint32_t g_vo_submit_flips = 0;
static int g_gpu_state = 0;
static int g_mbuf_state = 0;
static void *g_gpu = NULL, *g_mon = NULL;
static int g_safe_never = 0, g_fg_off = 0;
static long g_low_sub2mb_end = 0;
static size_t g_gpu_sz = GPU_SZ_BASE;
static int g_work_garlic = 0;

static volatile uint32_t g_mem_hibernate_count = 0;
static inline void pyr_cache_invalidate(void) {}
static void *g_pre_gpu = NULL;
static void *g_pre_work = NULL;
static void *g_pre_mem = NULL;
static uint8_t *g_dump_mem = NULL;
static size_t g_pre_size = 0, g_pre_work_sz = 0;
static long g_pre_gpu_phys = 0, g_pre_work_phys = 0, g_pre_mem_phys = 0, g_dump_phys = 0;

static volatile uint32_t g_map_run_cnt = 0;
static long g_map_run_phys0 = 0, g_map_run_phys_last = 0;
static uintptr_t g_map_run_va0 = 0, g_map_run_va_last = 0;
static int g_map_run_prot = 0, g_map_run_flags = 0;

static void flush_map_run(void) { g_map_run_cnt = 0; }
static void api_ring_push(const char *fmt, ...) { (void)fmt; }
static void stage_nop(const char *fmt, ...) { (void)fmt; }
#define STAGE(...) stage_nop(__VA_ARGS__)
#define klog(...)  stage_nop(__VA_ARGS__)

// ---- Simulated OrbisOS DirectMemory & FlexibleMemory kernel ----
#define MAX_DM_BLOCKS 256
typedef struct {
    long start;
    size_t len;
    int type;
    int in_use;
} SimDmBlock;

#define MAX_FLEX_BLOCKS 256
typedef struct {
    uintptr_t va;
    size_t len;
    int prot;
    int flags;
    int in_use;
} SimFlexBlock;

static uint8_t g_backing_buf[0x400000];
static void *sim_memset(void *dst, int c, size_t n)
{
    uintptr_t d = (uintptr_t)dst;
    if (d >= 0x550000000000ULL && d < 0x560000000000ULL)
        return dst;
    return memset(dst, c, n);
}
#define memset sim_memset
static size_t g_sim_dm_total = 0x40000000u; // 1 GB default
static size_t g_sim_flex_avail = 0x10000000u;
static SimDmBlock g_sim_dm[MAX_DM_BLOCKS];
static SimFlexBlock g_sim_flex[MAX_FLEX_BLOCKS];
static int g_sim_force_misaligned_flex = 0;
static int g_sim_map_dm_calls = 0;
static int g_sim_map_flex_calls = 0;
static int g_sim_last_flex_flags = 0;
static uintptr_t g_sim_fake_va_cursor = 0x550000000000ULL;

static int32_t sceKernelAvailableFlexibleMemorySize(size_t *out)
{
    if (out) *out = g_sim_flex_avail;
    return 0;
}

typedef struct {
    void *unk01;
    void *unk02;
    off_t offset;
    int prot;
    int unk04;
    unsigned isFlexibleMemory : 1;
    unsigned isDirectMemory : 1;
    unsigned isStack : 1;
    unsigned isPooledMemory : 1;
    unsigned isCommitted : 1;
    char name[32];
} OrbisKernelVirtualQueryInfo;

static int32_t sceKernelVirtualQuery(const void *addr, int flags, OrbisKernelVirtualQueryInfo *info, size_t infoSize)
{
    (void)addr; (void)flags; (void)info; (void)infoSize;
    return -1;
}

static void sim_reset(size_t dm_total)
{
    memset(g_sim_dm, 0, sizeof(g_sim_dm));
    memset(g_sim_flex, 0, sizeof(g_sim_flex));
    g_sim_dm_total = dm_total;
    g_sim_flex_avail = 0x10000000u;
    g_sim_force_misaligned_flex = 0;
    g_sim_map_dm_calls = 0;
    g_sim_map_flex_calls = 0;
    g_sim_last_flex_flags = 0;
    g_sim_fake_va_cursor = 0x550000000000ULL;

    g_cfg.shave_mode = 0;
    g_dm_hooks = 1;
    g_own_alloc = 0;
    g_dbg_allocs = 0;
    g_dbg_maps = 0;
    g_dbg_alloc_fail = 0;
    g_flips = 0;
    g_gpu_state = 0;
    g_mbuf_state = 0;
    g_gpu_sz = GPU_SZ_BASE;
    g_work_garlic = 0;
    g_pre_gpu = NULL;
    g_pre_work = NULL;
    g_pre_mem = NULL;
    g_dump_mem = NULL;
    g_pre_size = 0;
    g_pre_work_sz = 0;
    g_pre_gpu_phys = 0;
    g_pre_work_phys = 0;
    g_pre_mem_phys = 0;
    g_dump_phys = 0;
}

static size_t sim_dm_used_bytes(void)
{
    size_t used = 0;
    for (int i = 0; i < MAX_DM_BLOCKS; i++)
        if (g_sim_dm[i].in_use) used += g_sim_dm[i].len;
    return used;
}

static int sim_dm_overlaps(long s, long e)
{
    for (int i = 0; i < MAX_DM_BLOCKS; i++) {
        if (!g_sim_dm[i].in_use) continue;
        long bs = g_sim_dm[i].start;
        long be = bs + (long)g_sim_dm[i].len;
        if (s < be && e > bs) return 1;
    }
    return 0;
}

static int sim_dm_is_allocated_range(long s, size_t len)
{
    long req_end = s + (long)len;
    long cur = s;
    while (cur < req_end) {
        int found = 0;
        for (int i = 0; i < MAX_DM_BLOCKS; i++) {
            if (!g_sim_dm[i].in_use) continue;
            long bs = g_sim_dm[i].start;
            long be = bs + (long)g_sim_dm[i].len;
            if (cur >= bs && cur < be) {
                cur = be;
                found = 1;
                break;
            }
        }
        if (!found) return 0;
    }
    return 1;
}

static size_t sceKernelGetDirectMemorySize(void)
{
    return g_sim_dm_total;
}

static int32_t raw_sceKernelAvailableDirectMemorySize(off_t s, off_t e, size_t align, off_t *startOut, size_t *sizeOut)
{
    if (s < 0) s = 0;
    if ((size_t)e > g_sim_dm_total) e = (off_t)g_sim_dm_total;
    if (align == 0) align = 0x4000u;
    size_t best_len = 0;
    off_t best_start = 0;
    off_t cur = (s + (off_t)align - 1) & ~((off_t)align - 1);
    while (cur < e) {
        off_t limit = e;
        int blocked_at_cur = 0;
        for (int i = 0; i < MAX_DM_BLOCKS; i++) {
            if (!g_sim_dm[i].in_use) continue;
            off_t bs = g_sim_dm[i].start;
            off_t be = bs + (off_t)g_sim_dm[i].len;
            if (cur >= bs && cur < be) {
                cur = (be + (off_t)align - 1) & ~((off_t)align - 1);
                blocked_at_cur = 1;
                break;
            }
            if (bs > cur && bs < limit) limit = bs;
        }
        if (blocked_at_cur) continue;
        size_t span = (size_t)(limit - cur) & ~(align - 1u);
        if (span > best_len) {
            best_len = span;
            best_start = cur;
        }
        cur = (limit + (off_t)align - 1) & ~((off_t)align - 1);
    }
    if (startOut) *startOut = best_start;
    if (sizeOut) *sizeOut = best_len;
    return 0;
}

static int32_t raw_sceKernelAllocateDirectMemory(long s, long e, size_t len, size_t align, int type, long *phys)
{
    if (s < 0) s = 0;
    if ((size_t)e > g_sim_dm_total) e = (long)g_sim_dm_total;
    if (align == 0) align = 0x4000u;
    if (len == 0 || e <= s || (size_t)(e - s) < len) return (int32_t)0x80020023u;

    long cur = (s + (long)align - 1L) & ~((long)align - 1L);
    while (cur + (long)len <= e) {
        if (!sim_dm_overlaps(cur, cur + (long)len)) {
            for (int i = 0; i < MAX_DM_BLOCKS; i++) {
                if (!g_sim_dm[i].in_use) {
                    g_sim_dm[i].in_use = 1;
                    g_sim_dm[i].start = cur;
                    g_sim_dm[i].len = len;
                    g_sim_dm[i].type = type;
                    if (phys) *phys = cur;
                    return 0;
                }
            }
        }
        cur += (long)align;
    }
    return (int32_t)0x80020023u;
}

static int32_t raw_sceKernelReleaseDirectMemory(long phys, size_t len)
{
    long rel_end = phys + (long)len;
    for (int i = 0; i < MAX_DM_BLOCKS; i++) {
        if (!g_sim_dm[i].in_use) continue;
        long bs = g_sim_dm[i].start;
        long be = bs + (long)g_sim_dm[i].len;
        if (phys == bs && rel_end == be) {
            g_sim_dm[i].in_use = 0;
            return 0;
        }
        if (phys == bs && rel_end < be) {
            g_sim_dm[i].start = rel_end;
            g_sim_dm[i].len = (size_t)(be - rel_end);
            return 0;
        }
        if (phys > bs && rel_end == be) {
            g_sim_dm[i].len = (size_t)(phys - bs);
            return 0;
        }
    }
    return -1;
}

static int32_t sceKernelUsleep(uint32_t usec)
{
    (void)usec;
    return 0;
}

static int32_t raw_sceKernelMapDirectMemory2(void **addr, size_t len, int type, int prot, int flags, long dmStart, size_t maxPageSize)
{
    (void)type;
    (void)maxPageSize;
    if (!sim_dm_is_allocated_range(dmStart, len))
        return (int32_t)0x80020016u;
    g_sim_map_dm_calls++;
    void *out_va = NULL;
    if (g_own_alloc && len <= sizeof(g_backing_buf)) {
        uintptr_t b = ((uintptr_t)g_backing_buf + 0x3FFFFu) & ~(uintptr_t)0x3FFFFu;
        out_va = (void *)b;
    } else if ((flags & 0x10) && addr && *addr != NULL) {
        out_va = *addr;
    } else {
        g_sim_fake_va_cursor = (g_sim_fake_va_cursor + 0x1FFFFFu) & ~(uintptr_t)0x1FFFFFu;
        out_va = (void *)g_sim_fake_va_cursor;
        g_sim_fake_va_cursor += len;
    }
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {
        if (!g_sim_flex[i].in_use) {
            g_sim_flex[i].in_use = 1;
            g_sim_flex[i].va = (uintptr_t)out_va;
            g_sim_flex[i].len = len;
            g_sim_flex[i].prot = prot;
            g_sim_flex[i].flags = flags;
            break;
        }
    }
    if (addr) *addr = out_va;
    return 0;
}

// In Orbis libkernel.sprx, sceKernelMapDirectMemory and sceKernelMapNamedDirectMemory call sceKernelMapDirectMemory2!
static int32_t raw_sceKernelMapDirectMemory(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize)
{
    return sceKernelMapDirectMemory2_hook(addr, len, -1, prot, flags, dmStart, maxPageSize);
}

static int32_t raw_sceKernelMapNamedDirectMemory(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize, const char *name)
{
    (void)name;
    return raw_sceKernelMapDirectMemory(addr, len, prot, flags, dmStart, maxPageSize);
}

static int32_t sceKernelMapFlexibleMemory(void **addr, size_t len, int prot, int flags)
{
    if (!addr || !len) return -1;
    g_sim_map_flex_calls++;
    g_sim_last_flex_flags = flags;
    if (len > g_sim_flex_avail) return (int32_t)0x8002000cu;
    uintptr_t base = 0;
    if ((flags & 0x10) && *addr != NULL) {
        base = (uintptr_t)(*addr);
    } else if (g_own_alloc && len <= sizeof(g_backing_buf) - 0x100000u) {
        base = ((uintptr_t)g_backing_buf + 0xFFFFFu) & ~(uintptr_t)0xFFFFFu;
        if (g_sim_force_misaligned_flex) {
            base += 0x4000u; // 16KB aligned, NOT 64KB/256KB aligned
        }
    } else {
        g_sim_fake_va_cursor = (g_sim_fake_va_cursor + 0x3FFFu) & ~(uintptr_t)0x3FFFu;
        base = g_sim_fake_va_cursor;
        g_sim_fake_va_cursor += len;
    }
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {
        if (!g_sim_flex[i].in_use) {
            g_sim_flex[i].in_use = 1;
            g_sim_flex[i].va = base;
            g_sim_flex[i].len = len;
            g_sim_flex[i].prot = prot;
            g_sim_flex[i].flags = flags;
            break;
        }
    }
    *addr = (void *)base;
    return 0;
}

static int32_t sceKernelMunmap(void *addr, size_t len)
{
    (void)len;
    if (!addr) return -1;
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {
        if (g_sim_flex[i].in_use && g_sim_flex[i].va == (uintptr_t)addr) {
            g_sim_flex[i].in_use = 0;
            return 0;
        }
    }
    return 0;
}

static int32_t raw_sceVideoOutOpen(int32_t u, int32_t bus, int32_t idx, const void *p) { (void)u; (void)bus; (void)idx; (void)p; return 1; }
static int32_t raw_sceVideoOutClose(int32_t h) { (void)h; return 0; }

#define HOOK_CONTINUE(sym, fn_t, ...) raw_##sym(__VA_ARGS__)

int32_t sceKernelAvailableDirectMemorySize_hook(off_t s, off_t e, size_t align, off_t *startOut, size_t *sizeOut);
int32_t sceKernelAllocateDirectMemory_hook(long s, long e, size_t len, size_t align, int type, long *phys);
int32_t sceKernelReleaseDirectMemory_hook(off_t start, size_t len);
int32_t sceKernelMapDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize);
int32_t sceKernelMapNamedDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize, const char *name);

#define sceKernelAvailableDirectMemorySize sceKernelAvailableDirectMemorySize_hook
#define sceKernelAllocateDirectMemory      sceKernelAllocateDirectMemory_hook
#define sceKernelReleaseDirectMemory       sceKernelReleaseDirectMemory_hook
#define sceKernelMapDirectMemory           sceKernelMapDirectMemory_hook
#define sceKernelMapNamedDirectMemory      sceKernelMapNamedDirectMemory_hook
#define sceVideoOutOpen                    raw_sceVideoOutOpen
#define sceVideoOutClose                   raw_sceVideoOutClose
"""

TESTS_MAIN_C = r"""
static void reset_shave_state(void)
{
    memset(g_shaves, 0, sizeof(g_shaves));
    g_shave_n = 0;
    g_shave_phys = 0;
    g_shave_real_len = 0;
    g_shave_orig_len = 0;
    g_shave_type = 0;
    g_shave_tail_mapped = 0;
    g_shave_end = 0;
    g_mem_tight = 0;
    g_mem_hibernating = 0;
    g_hibernated_at_flip = 0;
    g_hibernated_work_sz = 0;
    g_hibernated_releases_base = 0;
    g_done = NULL;
    g_last_seq = 0;
    g_work = NULL;
    g_shave_last_probe_s = -1;
    g_shave_last_probe_e = -1;
    g_shave_last_probe_len = 0;
    g_flex_vmap_n = 0;
    memset(g_flex_gpu_slots, 0, sizeof(g_flex_gpu_slots));
}

static void setup_early_buffers_top_first(void)
{
    size_t dm_sz = sceKernelGetDirectMemorySize();
    long phys = 0; void *addr = NULL;
    g_own_alloc++;
    int r = sceKernelAllocateDirectMemory((long)(dm_sz - 0x2000000u), (long)dm_sz, 0x2000000u, 0x200000u, 3, &phys);
    ASSERT(r == 0);
    r = sceKernelMapDirectMemory(&addr, 0x2000000u, 0x33, 0, phys, 0x200000u);
    ASSERT(r == 0 && addr != NULL);
    g_own_alloc--;
    g_pre_mem = addr;
    g_pre_size = 0x2000000u;
    g_pre_mem_phys = phys;

    g_gpu_sz = GPU_SZ_BASE;
    g_pre_gpu = alloc_dm_range(g_pre_mem_phys - 0x200000L, g_pre_mem_phys, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    ASSERT(g_pre_gpu != NULL);
    g_pre_work = alloc_work_early(WORK_SZ_8, 3);
    ASSERT(g_pre_work != NULL);
    g_work_garlic = (g_pre_work_phys >= 0);
}

static void test_flex_gpu_fallback_and_alignment(void)
{
    sim_reset(0x40000000u);
    reset_shave_state();

    long game_phys = 0;
    g_own_alloc = 1;
    int r = raw_sceKernelAllocateDirectMemory(0, 0x40000000L, 0x40000000u, 0x200000u, 3, &game_phys);
    g_own_alloc = 0;
    ASSERT(r == 0);
    ASSERT(dm_avail(0x4000u) == 0);

    g_sim_force_misaligned_flex = 1;

    g_gpu_sz = GPU_SZ;
    g_pre_gpu = alloc_dm_range(0, (long)sceKernelGetDirectMemorySize(), g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    ASSERT(g_pre_gpu != NULL);
    ASSERT(g_pre_gpu_phys == -1L);
    uintptr_t gpu_va = (uintptr_t)g_pre_gpu;
    ASSERT((gpu_va & (g_gpu_sz - 1u)) == 0);
    ASSERT((gpu_va & 0xFFu) == 0);
    ASSERT((gpu_va & 0x1Fu) == 0);
    ASSERT((gpu_va & 0x7u) == 0);
    ASSERT((gpu_va >> 48) == 0);

    g_pre_work = alloc_work_early(WORK_SZ_8, 3);
    ASSERT(g_pre_work != NULL);
    ASSERT(g_pre_work_phys == -1L);
    ASSERT(g_pre_work_sz == WORK_SZ_8);
    uintptr_t work_va = (uintptr_t)g_pre_work;
    ASSERT((work_va & 0xFFFFu) == 0);
    ASSERT((work_va >> 48) == 0);

    ASSERT(sim_dm_used_bytes() == 0x40000000u);

    release_early_gpu();
    release_early_work();
    ASSERT(g_pre_gpu == NULL && g_pre_work == NULL);

    void *gv_mem = alloc_gv_dm(0x10000u, 0x10000u, 0);
    ASSERT(gv_mem != NULL);
}

static void test_top_packing_and_shrink_retry(void)
{
    sim_reset(0x40000000u);
    reset_shave_state();
    setup_early_buffers_top_first();

    ASSERT(g_pre_mem_phys == 0x3E000000L);
    ASSERT(g_pre_gpu_phys == 0x3DE00000L);
    ASSERT(g_pre_work_phys == 0x3DE00000L + (long)GPU_SZ_BASE);
    off_t av_start = -1;
    size_t av_sz = dm_avail_range(0, 0x40000000L, 0x200000u, &av_start);
    ASSERT(av_start == 0);
    ASSERT(av_sz == 0x3DE00000u);

    long p1 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x20000000u, 0x200000u, 3, &p1) == 0);
    ASSERT(p1 == 0);

    long p2 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x1F400000u, 0x200000u, 3, &p2) == 0);
    ASSERT(g_pre_mem != NULL);
    ASSERT(g_pre_size == 0x800000u);
    ASSERT(g_shave_n == 0);
    ASSERT(g_pre_gpu == NULL && g_pre_work == NULL);
}

static void test_multi_arena_shave_and_post_reserve(void)
{
    sim_reset(0x40000000u);
    reset_shave_state();
    setup_early_buffers_top_first();

    long p1 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x10400000u, 0x200000u, 3, &p1) == 0);

    long p2 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x2F800000u, 0x200000u, 3, &p2) == 0);
    ASSERT(g_pre_mem != NULL && g_pre_size == 0x800000u);
    ASSERT(g_shave_n == 1);
    ASSERT(g_shaves[0].real_len == 0x2F000000u);
    ASSERT(g_shaves[0].orig_len == 0x2F800000u);

    long p3 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x400000u, 0x200000u, 3, &p3) == 0);
    ASSERT(p3 == g_shaves[0].phys + (long)g_shaves[0].real_len);

    void *va_arena4 = NULL;
    ASSERT(sceKernelMapDirectMemory(&va_arena4, 0x2F800000u, 0x33, 0, p2, 0x200000u) == 0);
    ASSERT(va_arena4 != NULL && g_shave_tail_mapped == 1);

    int dm_maps_before = g_sim_map_dm_calls;
    void *va_alloc5 = NULL;
    ASSERT(sceKernelMapDirectMemory(&va_alloc5, 0x400000u, 0x33, 0, p3, 0x200000u) == 0);
    ASSERT(va_alloc5 != NULL);
    ASSERT(g_sim_map_dm_calls == dm_maps_before + 1);

    // CUSA10249 exact 5-allocation pattern (4 arenas leaving only 2 MB unshaved + post-RegisterBuffers 6 MB Alloc 5):
    sim_reset(0x40000000u);
    reset_shave_state();
    setup_early_buffers_top_first();

    long a1 = 0, a2 = 0, a3 = 0, a4 = 0, a5 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x8000000u, 0x200000u, 3, &a1) == 0);
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x4000000u, 0x200000u, 0, &a2) == 0);
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x4600000u, 0x200000u, 3, &a3) == 0);
    // Remaining before g_pre_mem (after shrinking 32 MB -> 8 MB) is 0x2F200000.
    // Arena 4 (0x2F000000, type=3 GPU Garlic) fits 100% in real DirectMemory unshaved, leaving 0x200000 (2 MB) for Alloc 5:
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x2F000000u, 0x200000u, 3, &a4) == 0);
    ASSERT(g_pre_mem != NULL && g_pre_size == 0x800000u);
    ASSERT(g_shave_n == 0);

    // RegisterBuffers runs before Alloc 5 in CUSA10249 (setting g_mbuf_state = 1), and Alloc 5 (0x600000, type=0 CPU)
    // takes the remaining 2 MB of real DirectMemory + 4 MB FlexibleMemory tail:
    g_mbuf_state = 1;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x600000u, 0x200000u, 0, &a5) == 0);
    ASSERT(g_shave_n == 1);
    ASSERT(g_shaves[0].real_len == 0x200000u);
    ASSERT(g_shaves[0].orig_len == 0x600000u);
    void *va_a5 = NULL;
    ASSERT(sceKernelMapDirectMemory(&va_a5, 0x600000u, 0x2, 0, a5, 0x200000u) == 0);
    ASSERT(va_a5 != NULL);
}

static void test_sequential_chunks_and_slice_maps(void)
{
    sim_reset(0x40000000u);
    reset_shave_state();
    setup_early_buffers_top_first();

    long chunk_phys[16];
    for (int i = 0; i < 16; i++) {
        int r = sceKernelAllocateDirectMemory(0, 0x40000000L, 0x4000000u, 0x10000u, 3, &chunk_phys[i]);
        ASSERT(r == 0);
    }
    ASSERT(g_pre_mem != NULL && g_pre_size == 0x800000u);
    ASSERT(g_shave_n == 1);
    ASSERT(g_shaves[0].real_len == 0x3800000u);
    ASSERT(g_shaves[0].orig_len == 0x4000000u);

    int dm_maps_0 = g_sim_map_dm_calls;
    int flex_maps_0 = g_sim_map_flex_calls;
    for (size_t off = 0; off < 0x4000000u; off += 0x100000u) {
        void *slice_va = NULL;
        int mr = sceKernelMapDirectMemory(&slice_va, 0x100000u, 0x33, 0, chunk_phys[15] + (long)off, 0x100000u);
        ASSERT(mr == 0 && slice_va != NULL);
    }
    ASSERT(g_sim_map_dm_calls - dm_maps_0 == 56);
    ASSERT(g_sim_map_flex_calls - flex_maps_0 == 8);

    sim_reset(0x10000000u);
    reset_shave_state();
    setup_early_buffers_top_first();

    int ok_count = 0;
    long p8 = 0;
    while (sceKernelAllocateDirectMemory(0, 0x10000000L, 0x800000u, 0x200000u, 3, &p8) == 0) {
        void *va8 = NULL;
        ASSERT(sceKernelMapDirectMemory(&va8, 0x800000u, 0x33, 0, p8, 0x200000u) == 0);
        ok_count++;
        ASSERT(ok_count <= 32);
    }
    ASSERT(ok_count == 32);
    ASSERT(g_pre_mem != NULL && g_pre_size == 0x800000u);
}

static void test_range_constrained_relocation(void)
{
    // 1. Full overlap of g_pre_mem relocates 8 MB below avoid_s:
    sim_reset(0x40000000u);
    reset_shave_state();
    setup_early_buffers_top_first();

    long top_phys = 0;
    int r = sceKernelAllocateDirectMemory(0x3C000000L, 0x40000000L, 0x4000000u, 0x200000u, 3, &top_phys);
    ASSERT(r == 0);
    ASSERT(top_phys == 0x3C000000L);
    ASSERT(g_pre_mem != NULL && g_pre_size == 0x800000u);
    ASSERT(g_pre_mem_phys + (long)g_pre_size <= 0x3C000000L);
    ASSERT(g_shave_n == 0);

    // 2. Partial top-tail overlap (CUSA08809/CUSA08034 requesting top 0x220000 in 0x3FDE0000..0x40000000):
    //    trims only the overlapping 8 MB tail in-place (32 MB -> 24 MB) without moving g_pre_mem or releasing g_pre_work/gpu!
    sim_reset(0x40000000u);
    reset_shave_state();
    setup_early_buffers_top_first();
    void *orig_va = g_pre_mem;
    long orig_phys = g_pre_mem_phys;
    long tail_phys = 0;
    r = sceKernelAllocateDirectMemory(0x3FDE0000L, 0x40000000L, 0x220000u, 0x20000u, 3, &tail_phys);
    ASSERT(r == 0 && tail_phys == 0x3FDE0000L);
    ASSERT(g_pre_mem == orig_va && g_pre_mem_phys == orig_phys);
    ASSERT(g_pre_size == 0x1800000u);
    ASSERT(g_pre_work != NULL && g_pre_gpu != NULL);
}

static void test_avail_hook_and_late_alloc_mode2(void)
{
    sim_reset(0x40000000u);
    reset_shave_state();
    g_cfg.shave_mode = 2;

    off_t av_start = 0;
    size_t av_sz = 0;
    ASSERT(sceKernelAvailableDirectMemorySize(0, 0x40000000L, 0x200000u, &av_start, &av_sz) == 0);
    ASSERT(av_sz == 0x3F800000u);
    ASSERT(g_mem_tight == 1);

    long p = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, av_sz, 0x200000u, 3, &p) == 0);
    ASSERT(dm_avail(0x200000u) == 0x800000u);

    sim_reset(0x40000000u);
    reset_shave_state();
    g_cfg.shave_mode = 2;

    long c0 = 0, c1 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x20000000u, 0x200000u, 3, &c0) == 0);
    ASSERT(g_pre_mem == NULL);
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x20000000u, 0x200000u, 3, &c1) == 0);
    ASSERT(g_pre_mem != NULL && g_pre_size == 0x800000u);
    ASSERT(g_shave_n == 1 && g_shaves[0].real_len == 0x1F800000u);

    // Post-RegisterBuffers large allocation (e.g. CUSA17226 len=0xcc80000 when g_mbuf_state==1 and 32MB g_pre_mem is registered)
    sim_reset(0x20000000u);
    reset_shave_state();
    setup_early_buffers_top_first();
    g_mbuf_state = 1;
    long pre_arena = 0, post_reg = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x12B80000u, 0x80000u, 3, &pre_arena) == 0);
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0xCC80000u, 0x80000u, 3, &post_reg) == 0);
    ASSERT(g_shave_n == 1 && g_shaves[0].orig_len == 0xCC80000u && g_shaves[0].real_len == 0xB480000u);
    void *va_post = NULL;
    ASSERT(sceKernelMapDirectMemory(&va_post, 0xCC80000u, 0x33, 0, post_reg, 0x80000u) == 0 && va_post != NULL);
}

static void test_runtime_streaming_and_batchmap_guards(void)
{
    // 1. alloc_gv_dm consumes 0 bytes of DirectMemory even when DirectMemory is 100% free:
    sim_reset(0x40000000u);
    reset_shave_state();
    void *gv = alloc_gv_dm(0x100000u, 0x10000u, 0);
    ASSERT(gv != NULL);
    ASSERT(sim_dm_used_bytes() == 0);

    // 2. Runtime streaming (g_flips > 0) synthetic flex-backed chunk (CUSA32946 / CUSA10237 / CUSA10123):
    sim_reset(0x20000000u);
    reset_shave_state();
    setup_early_buffers_top_first();
    release_early_gpu();
    release_early_work();
    g_mbuf_state = 1;
    g_flips = 380;
    long main_arena = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x1E000000u, 0x200000u, 3, &main_arena) == 0);
    ASSERT(dm_avail(0x4000u) == 0);
    long stream_phys = 0;
    void *stream_va = NULL;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x1000000u, 0x10000u, 0, &stream_phys) == 0);
    ASSERT(sceKernelMapDirectMemory(&stream_va, 0x1000000u, 0x32, 0x10, stream_phys, 0x10000u) == 0);
    ASSERT(stream_va != NULL);

    // 3. Non-2MB-aligned giant BatchMap bundle in shave_mode=2 (CUSA28561 pattern):
    sim_reset(0x40000000u);
    reset_shave_state();
    g_cfg.shave_mode = 2;
    long b0 = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x10000000u, 0x200000u, 3, &b0) == 0);
    long bundle = 0;
    // Request 0x2F8F0000 (not 2MB-aligned, leaves < 8 MB): claims 8 MB g_pre_mem and refuses un-shavable bundle so
    // the game's binary splitter can allocate 2MB-aligned chunks cleanly around g_pre_mem.
    ASSERT(sceKernelAllocateDirectMemory(0, 0x40000000L, 0x2F8F0000u, 0x10000u, 3, &bundle) != 0);
    ASSERT(g_pre_mem != NULL && g_pre_size == 0x800000u);
    ASSERT(g_shave_n == 0);

    // 4. CUSA07820 pattern: when 0 bytes of DirectMemory remain and only 2 MB of FlexibleMemory remains,
    //    a 4 MB synthetic tail-only probe fails immediately with 0x80020023 (so caller falls back to 2 MB
    //    without looping 192 MapDirectMemory calls), and mapping a fixed-VA synthetic chunk uses 0x90
    //    (MAP_FIXED | MAP_NO_OVERWRITE) so libc.prx is never unmapped:
    sim_reset(0x20000000u);
    reset_shave_state();
    setup_early_buffers_top_first();
    release_early_gpu();
    release_early_work();
    g_mbuf_state = 1;
    long fill_arena = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x1E000000u, 0x200000u, 3, &fill_arena) == 0);
    ASSERT(dm_avail(0x4000u) == 0);
    g_sim_flex_avail = 0x200000u;
    long probe4m = 0, fallback2m = 0;
    ASSERT(sceKernelAllocateDirectMemory(0x1E000000L, 0x20000000L, 0x400000u, 0x200000u, 3, &probe4m) == (int32_t)0x80020023u);
    ASSERT(sceKernelAllocateDirectMemory(0x1E000000L, 0x20000000L, 0x200000u, 0x200000u, 3, &fallback2m) == 0);
    void *fixed_va = (void *)0x40000000ULL;
    ASSERT(sceKernelMapDirectMemory(&fixed_va, 0x200000u, 0x33, 0x90, fallback2m, 0x200000u) == 0);
    ASSERT(fixed_va == (void *)0x40000000ULL);
    ASSERT((g_sim_last_flex_flags & 0x90) == 0x90);
    ASSERT(on_sub() == 0);

    // 6. CUSA01967 scatter-gather power-of-two sub-1MB page allocator (0x80000 -> 0x40000 -> 0x20000 -> 0x10000 -> 0x8000 -> 0x4000):
    //    When a small 0x10000 hole remains in DirectMemory and no large arena shave exists, sub-1MB probes larger than 0x10000
    //    must step down naturally to claim the real 0x10000 DirectMemory hole without straddle-shaving, and once 0 bytes of
    //    DirectMemory remain, subsequent 0x4000 page allocations succeed via pure synthetic FlexibleMemory chunks:
    sim_reset(0x20000000u);
    reset_shave_state();
    setup_early_buffers_top_first();
    release_early_gpu();
    release_early_work();
    g_mbuf_state = 1;
    g_sim_flex_avail = 0x20000000u;
    long fill_sg = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x1DFF0000u, 0x10000u, 3, &fill_sg) == 0);
    ASSERT(dm_avail(0x4000u) == 0x10000u);
    long sg_p = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x80000u, 0x4000u, 3, &sg_p) == (int32_t)0x80020023u);
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x40000u, 0x4000u, 3, &sg_p) == (int32_t)0x80020023u);
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x20000u, 0x4000u, 3, &sg_p) == (int32_t)0x80020023u);
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x10000u, 0x4000u, 3, &sg_p) == 0);
    ASSERT(g_shave_n == 0);
    ASSERT(dm_avail(0x4000u) == 0);
    // Now DirectMemory is 0 bytes free; next 0x4000 page request is backed by synthetic FlexibleMemory:
    long sg_syn = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20000000L, 0x4000u, 0x4000u, 3, &sg_syn) == 0);
    ASSERT(g_shave_n == 1 && g_shaves[0].real_len == 0 && g_shaves[0].orig_len == 0x4000u);
    ASSERT(on_sub() == 0);
}

int main(void)
{
    test_flex_gpu_fallback_and_alignment();
    test_top_packing_and_shrink_retry();
    test_multi_arena_shave_and_post_reserve();
    test_sequential_chunks_and_slice_maps();
    test_range_constrained_relocation();
    test_avail_hook_and_late_alloc_mode2();
    test_runtime_streaming_and_batchmap_guards();
    printf("lowmem valve & alloc_flex_gpu unit tests: all 7 suites passed\n");
    return 0;
}
"""


def main() -> None:
    out = ROOT / "build" / "tests"
    out.mkdir(parents=True, exist_ok=True)
    source = out / "test_lowmem_valve.c"
    bitcode = out / "test_lowmem_valve.bc"
    source.write_text(
        HARNESS_C
        + "\n"
        + HOOKS_DECLS
        + "\n"
        + HOOKS_VMAP
        + "\n"
        + MAIN_ALLOC_PART1
        + "\n"
        + HOOKS_CORE
        + "\n"
        + MAIN_ALLOC_PART2
        + "\n"
        + TESTS_MAIN_C,
        encoding="utf-8",
    )
    llvm = pathlib.Path(cfg["llvm_bin"])
    subprocess.run(
        [
            str(llvm / "clang.exe"),
            "--target=x86_64-pc-windows-msvc",
            "-O0",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fno-builtin",
            "-emit-llvm",
            "-c",
            str(source),
            "-o",
            str(bitcode),
        ],
        check=True,
    )
    subprocess.run([str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bitcode)], check=True)


if __name__ == "__main__":
    main()
