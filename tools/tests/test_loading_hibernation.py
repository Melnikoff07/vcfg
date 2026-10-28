#!/usr/bin/env python3
"""Unit test suite for Deferred Boot & Runtime Loading-Screen Memory Hibernation (vcfg-r12-zero-boot-mem).

Compiles the real allocator, Memory Valve, Arena Shaver, and Hibernation Recovery logic from
classic/source/main.c and classic/source/debug_hooks.h to LLVM bitcode and executes via lli.exe.

Covers:
- Scenario A (CUSA09311 Main-Menu -> Loading Screen):
  300 flips at main menu (g_flips = 300, g_shave_end = 0) -> 512 MB world arena shaved to 498 MB
  (leaving 2 MB headroom) -> 16 MB transient loading staging buffer triggers Step 2 Memory Valve
  GPU drain (*g_done == g_last_seq) + g_mem_hibernating = 1 + g_work_phys release -> 180 flips
  (g_flips = 480) + transient release -> automatic g_work re-allocation and FG resume.
- Scenario B (Concurrent Render + Loading Thread Valve Trigger in Step 2 & Step 3):
  In-flight FG command buffer (*g_done = 5, g_last_seq = 6) when loading thread triggers Step 2 or
  Step 3 of the Memory Valve -> g_mem_hibernating = 1 is set atomically BEFORE waiting on *g_done ==
  g_last_seq, and concurrent render thread calling on_sub() immediately sees g_mem_hibernating == 1
  and takes the native pass-through path without touching g_work or g_mid.
- Scenario C (shave_mode = 1/3 guard & FlexibleMemory Onion recovery fallback).
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
extern int strcmp(const char *, const char *);
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
    int audio_fix;
} fg_config_t;

static char g_title_id[16] = "CUSA00552";
static fg_config_t g_cfg = { 0 };
static int vcfg_in_family(const char *tid, const char *fam) { (void)tid; (void)fam; return 0; }
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

static uint8_t g_backing_buf[0x300000];
static void *sim_memset(void *dst, int c, size_t n)
{
    uintptr_t d = (uintptr_t)dst;
    if (d >= 0x550000000000ULL && d < 0x560000000000ULL)
        return dst;
    return memset(dst, c, n);
}
#define memset sim_memset
static size_t g_sim_dm_total = 0x40000000u;
static size_t g_sim_flex_avail = 0x10000000u;
static SimDmBlock g_sim_dm[MAX_DM_BLOCKS];
static SimFlexBlock g_sim_flex[MAX_FLEX_BLOCKS];
static int g_sim_force_misaligned_flex = 0;
static int g_sim_map_dm_calls = 0;
static int g_sim_map_flex_calls = 0;
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

static uint32_t g_sim_usleep_calls = 0;
static uint32_t g_sim_gpu_complete_on_sleep = 0;
static volatile uint32_t *g_sim_gpu_done_ptr = NULL;
static uint32_t g_sim_gpu_done_val = 0;
static int g_sim_check_hibernating_in_sleep = 0;
static int g_sim_render_saw_hibernating = 0;
static int g_sim_work_still_mapped_on_first_sleep = 0;
static uint32_t g_sim_done_val_at_work_release = 0;
static int on_sub(void);

static void sim_reset(size_t dm_total)
{
    memset(g_sim_dm, 0, sizeof(g_sim_dm));
    memset(g_sim_flex, 0, sizeof(g_sim_flex));
    g_sim_dm_total = dm_total;
    g_sim_force_misaligned_flex = 0;
    g_sim_map_dm_calls = 0;
    g_sim_map_flex_calls = 0;
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
    if (g_sim_gpu_done_ptr) {
        g_sim_done_val_at_work_release = *g_sim_gpu_done_ptr;
    }
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
    g_sim_usleep_calls++;
    if (g_sim_usleep_calls == 1 && g_pre_work != NULL) {
        g_sim_work_still_mapped_on_first_sleep = 1;
    }
    if (g_sim_check_hibernating_in_sleep) {
        if (on_sub() == 0) g_sim_render_saw_hibernating = 1;
    }
    if (g_sim_gpu_complete_on_sleep > 0 && g_sim_usleep_calls >= g_sim_gpu_complete_on_sleep && g_sim_gpu_done_ptr) {
        *g_sim_gpu_done_ptr = g_sim_gpu_done_val;
    }
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
    uintptr_t base = 0;
    if (g_own_alloc && len <= sizeof(g_backing_buf)) {
        base = ((uintptr_t)g_backing_buf + 0x3FFFFu) & ~(uintptr_t)0x3FFFFu;
        if (g_sim_force_misaligned_flex) {
            base += 0x4000u;
        }
    } else if ((flags & 0x10) && *addr != NULL) {
        base = (uintptr_t)(*addr);
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
int32_t sceKernelAllocateMainDirectMemory_hook(size_t len, size_t align, int type, long *phys);
int32_t sceKernelReleaseDirectMemory_hook(off_t start, size_t len);
int32_t sceKernelMapDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize);
int32_t sceKernelMapNamedDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize, const char *name);

#define sceKernelAvailableDirectMemorySize sceKernelAvailableDirectMemorySize_hook
#define sceKernelAllocateDirectMemory      sceKernelAllocateDirectMemory_hook
#define sceKernelAllocateMainDirectMemory  sceKernelAllocateMainDirectMemory_hook
#define sceKernelReleaseDirectMemory       sceKernelReleaseDirectMemory_hook
#define sceKernelMapDirectMemory           sceKernelMapDirectMemory_hook
#define sceKernelMapNamedDirectMemory      sceKernelMapNamedDirectMemory_hook
#define sceVideoOutOpen                    raw_sceVideoOutOpen
#define sceVideoOutClose                   raw_sceVideoOutClose
"""

TESTS_MAIN_C = r"""
static void reset_hibernation_test_state(void)
{
    memset(g_shaves, 0, sizeof(g_shaves));
    g_shave_n = 0;
    g_shave_phys = 0;
    g_shave_real_len = 0;
    g_shave_orig_len = 0;
    g_shave_type = 0;
    g_shave_end = 0;
    g_shave_tail_mapped = 0;
    g_mem_tight = 0;
    g_mem_hibernating = 0;
    g_hibernated_at_flip = 0;
    g_hibernated_work_sz = 0;
    g_hibernated_releases_base = 0;
    g_done = NULL;
    g_last_seq = 0;
    g_gpu_drain_waits = 0;
    g_dbg_releases = 0;
    g_dbg_batch_maps = 0;
    g_sim_usleep_calls = 0;
    g_sim_gpu_complete_on_sleep = 0;
    g_sim_gpu_done_ptr = NULL;
    g_sim_gpu_done_val = 0;
    g_sim_check_hibernating_in_sleep = 0;
    g_sim_render_saw_hibernating = 0;
    g_sim_work_still_mapped_on_first_sleep = 0;
    g_sim_done_val_at_work_release = 0;
    g_shave_last_probe_s = -1;
    g_shave_last_probe_e = -1;
    g_shave_last_probe_len = 0;
    g_flex_vmap_n = 0;
    memset(g_flex_gpu_slots, 0, sizeof(g_flex_gpu_slots));
}

// Scenario A (CUSA09311 Main-Menu -> Loading Screen):
static void test_scenario_a_cusa09311_post_menu_shave_and_hibernation(void)
{
    sim_reset(0x20200000u); // 514 MB pool
    reset_hibernation_test_state();

    // 14 MB held by VCFG at top of pool: 12 MB g_pre_mem + 2 MB g_pre_work -> 500 MB (0x1F400000) free.
    long p_mem = 0, p_work = 0;
    void *v_mem = NULL, *v_work = NULL;
    g_own_alloc = 1;
    ASSERT(raw_sceKernelAllocateDirectMemory(0x1F600000L, 0x20200000L, 0xC00000u, 0x200000u, 3, &p_mem) == 0);
    ASSERT(raw_sceKernelMapDirectMemory(&v_mem, 0xC00000u, 0x33, 0, p_mem, 0x200000u) == 0);
    ASSERT(raw_sceKernelAllocateDirectMemory(0x1F400000L, 0x1F600000L, 0x200000u, 0x10000u, 3, &p_work) == 0);
    ASSERT(raw_sceKernelMapDirectMemory(&v_work, 0x200000u, 0x33, 0, p_work, 0x10000u) == 0);
    g_own_alloc = 0;
    g_pre_mem = v_mem;
    g_pre_mem_phys = p_mem;
    g_pre_size = 0xC00000u;
    g_pre_work = v_work;
    g_pre_work_phys = p_work;
    g_pre_work_sz = 0x200000u;
    g_work = (uint8_t *)v_work;
    g_mbuf_state = 1;
    g_gpu_state = 1;

    // 1. Simulate 300 flips at main menu (g_flips = 300, g_shave_end = 0):
    g_flips = 300u;
    ASSERT(g_shave_end == 0);
    ASSERT(dm_avail(0x200000u) == 0x1F400000u); // 500 MB free

    // 2. Game requests a 512 MB world arena when only 500 MB is free:
    long world_phys = 0;
    ASSERT(sceKernelAllocateMainDirectMemory(0x20000000u, 0x200000u, 3, &world_phys) == 0);
    ASSERT(g_shave_n == 1);
    ASSERT(g_shaves[0].orig_len == 0x20000000u);
    ASSERT(g_shaves[0].real_len == 0x1F200000u); // Shaved to 498 MB, leaving 2 MB headroom!
    ASSERT(g_shave_end == world_phys + 0x20000000L);
    ASSERT(dm_avail(0x200000u) == 0x200000u);
    ASSERT(g_work != NULL && g_work_phys == p_work);

    // Map the 512 MB world arena (498 MB DirectMemory + 14 MB FlexibleMemory tail):
    void *world_va = NULL;
    ASSERT(sceKernelMapDirectMemory(&world_va, 0x20000000u, 0x33, 0, world_phys, 0x200000u) == 0);
    ASSERT(world_va != NULL && g_shave_tail_mapped == 1);

    // 3. Game requests a 16 MB transient loading staging buffer (< 256 MB):
    volatile uint32_t done_label = 299u;
    g_done = &done_label;
    g_last_seq = 300u;
    g_sim_gpu_done_ptr = &done_label;
    g_sim_gpu_done_val = 300u;
    g_sim_gpu_complete_on_sleep = 2u;

    long staging_phys = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20200000L, 0x1000000u, 0x200000u, 3, &staging_phys) == 0);
    ASSERT(*g_done == g_last_seq);
    ASSERT(g_sim_done_val_at_work_release == 300u); // GPU finished BEFORE ReleaseDirectMemory ran!
    ASSERT(g_mem_hibernating == 1);
    ASSERT(g_hibernated_at_flip == 300u);
    ASSERT(g_work == NULL && g_pre_work == NULL && g_work_phys == 0);

    // While loading screen is still in cooldown (< 180 flips), FG remains hibernated:
    g_flips = 400u;
    ASSERT(on_sub() == 0);
    ASSERT(g_mem_hibernating == 1);

    // Release transient staging buffer and reach 180 flips after hibernation (g_flips = 480):
    ASSERT(sceKernelReleaseDirectMemory(staging_phys, 0x1000000u) == 0);
    ASSERT(dm_avail(0x200000u) == 0x400000u); // 4 MB available (2 MB headroom + 2 MB freed work)
    g_flips = 479u;
    ASSERT(on_sub() == 1); // flip 480 triggers re-allocation of g_work and resumes FG!
    ASSERT(g_flips == 480u);
    ASSERT(g_mem_hibernating == 0);
    ASSERT(g_work != NULL && g_pre_work != NULL);
    ASSERT(g_work_phys >= 0);
}

// Scenario B (Concurrent Render + Loading Thread Valve Trigger):
static void test_scenario_b_concurrent_render_and_loading_valve(void)
{
    // 1. Step 2 (g_work_phys release) with in-flight FG command buffer (*g_done = 5, g_last_seq = 6):
    sim_reset(0x10000000u);
    reset_hibernation_test_state();
    setup_early_buffers_top_first_sim:
    {
        long pm = 0, pw = 0;
        void *vm = NULL, *vw = NULL;
        g_own_alloc = 1;
        ASSERT(raw_sceKernelAllocateDirectMemory(0x0F000000L, 0x10000000L, 0x1000000u, 0x200000u, 3, &pm) == 0);
        ASSERT(raw_sceKernelMapDirectMemory(&vm, 0x1000000u, 0x33, 0, pm, 0x200000u) == 0);
        ASSERT(raw_sceKernelAllocateDirectMemory(0x0EE00000L, 0x0F000000L, 0x200000u, 0x10000u, 3, &pw) == 0);
        ASSERT(raw_sceKernelMapDirectMemory(&vw, 0x200000u, 0x33, 0, pw, 0x10000u) == 0);
        g_own_alloc = 0;
        g_pre_mem = vm;
        g_pre_mem_phys = pm;
        g_pre_size = 0x1000000u;
        g_pre_work = vw;
        g_pre_work_phys = pw;
        g_pre_work_sz = 0x200000u;
        g_work = (uint8_t *)vw;
        g_mbuf_state = 1;
        g_gpu_state = 1;
    }
    (void)&&setup_early_buffers_top_first_sim;

    // Fill remaining 0x0EE00000 bytes so the next 2 MB request triggers Step 2 of the Memory Valve:
    long fill_phys = 0;
    g_own_alloc = 1;
    ASSERT(raw_sceKernelAllocateDirectMemory(0, 0x0EE00000L, 0x0EE00000u, 0x200000u, 3, &fill_phys) == 0);
    g_own_alloc = 0;
    ASSERT(dm_avail(0x200000u) == 0);

    g_flips = 250u;
    volatile uint32_t gpu_eop_label = 5u;
    g_done = &gpu_eop_label;
    g_last_seq = 6u;
    g_sim_gpu_done_ptr = &gpu_eop_label;
    g_sim_gpu_done_val = 6u;
    g_sim_gpu_complete_on_sleep = 4u; // GPU completes on 4th 500us sleep (2 ms)
    g_sim_check_hibernating_in_sleep = 1; // Render thread calls on_sub() during sleep #1!

    long req_phys = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x10000000L, 0x200000u, 0x200000u, 3, &req_phys) == 0);
    ASSERT(g_sim_work_still_mapped_on_first_sleep == 1);
    ASSERT(g_sim_render_saw_hibernating == 1); // Render thread immediately saw g_mem_hibernating == 1!
    ASSERT(g_gpu_drain_waits == 4u);
    ASSERT(*g_done == 6u);
    ASSERT(g_sim_done_val_at_work_release == 6u); // ReleaseDirectMemory ran only AFTER *g_done == 6!
    ASSERT(g_work == NULL && g_pre_work == NULL);

    // 2. Step 3 (g_extra_phys / g_pre_mem_phys release when shave_mode = 1 and g_flips > 0):
    gpu_eop_label = 5u;
    g_last_seq = 6u;
    g_gpu_drain_waits = 0;
    g_sim_usleep_calls = 0;
    g_sim_gpu_complete_on_sleep = 3u;
    g_sim_done_val_at_work_release = 0;
    g_cfg.shave_mode = 1; // Valve only (no Shave)

    long step3_phys = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x10000000L, 0x1000000u, 0x200000u, 3, &step3_phys) == 0);
    ASSERT(g_gpu_drain_waits == 3u);
    ASSERT(g_sim_done_val_at_work_release == 6u);
    ASSERT(g_pre_mem == NULL && g_extra_phys == 0);
}

// Scenario C (shave_mode = 3 guard & FlexibleMemory Onion recovery fallback):
static void test_scenario_c_shave_mode3_and_flex_recovery(void)
{
    (void)alloc_gv_dm;
    (void)release_early_dump;
    sim_reset(0x20200000u);
    reset_hibernation_test_state();
    g_cfg.shave_mode = 3;
    g_flips = 300u;

    long fill_phys = 0;
    g_own_alloc = 1;
    ASSERT(raw_sceKernelAllocateDirectMemory(0, 0x20200000L, 0x10000000u, 0x200000u, 3, &fill_phys) == 0);
    g_own_alloc = 0;

    // With shave_mode == 3, a 512 MB post-menu world arena must NOT be shaved:
    long world_phys = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x20200000L, 0x20000000u, 0x200000u, 3, &world_phys) != 0);
    ASSERT(g_shave_n == 0);

    // Verify FlexibleMemory (Onion) recovery when DirectMemory stays full after transient release:
    sim_reset(0x10000000u);
    reset_hibernation_test_state();
    {
        long pw = 0;
        void *vw = NULL;
        g_own_alloc = 1;
        ASSERT(raw_sceKernelAllocateDirectMemory(0x0FE00000L, 0x10000000L, 0x200000u, 0x10000u, 3, &pw) == 0);
        ASSERT(raw_sceKernelMapDirectMemory(&vw, 0x200000u, 0x33, 0, pw, 0x10000u) == 0);
        ASSERT(raw_sceKernelAllocateDirectMemory(0, 0x0FE00000L, 0x0FE00000u, 0x200000u, 3, &fill_phys) == 0);
        g_own_alloc = 0;
        g_pre_work = vw;
        g_pre_work_phys = pw;
        g_pre_work_sz = 0x200000u;
        g_work = (uint8_t *)vw;
        g_pre_mem = (void *)0x550080000000ULL;
        g_pre_size = 0x800000u;
        g_mbuf_state = 1;
        g_gpu_state = 1;
    }
    g_flips = 200u;
    long take_last_2m = 0;
    ASSERT(sceKernelAllocateDirectMemory(0, 0x10000000L, 0x200000u, 0x200000u, 3, &take_last_2m) == 0);
    ASSERT(g_mem_hibernating == 1 && g_work == NULL);
    ASSERT(dm_avail(0x4000u) == 0);

    // Simulate a game release of a small block that is immediately re-claimed, leaving 0 DirectMemory at flip 380:
    g_dbg_releases++;
    g_flips = 379u;
    ASSERT(on_sub() == 1); // Recovers via alloc_flex_gpu (Onion) at flip 380!
    ASSERT(g_mem_hibernating == 0);
    ASSERT(g_work != NULL && g_work_phys == -1L);
}

int main(void)
{
    test_scenario_a_cusa09311_post_menu_shave_and_hibernation();
    test_scenario_b_concurrent_render_and_loading_valve();
    test_scenario_c_shave_mode3_and_flex_recovery();
    printf("loading hibernation & post-menu world-arena shave unit tests: all 3 scenarios passed\n");
    return 0;
}
"""


def main() -> None:
    out = ROOT / "build" / "tests"
    out.mkdir(parents=True, exist_ok=True)
    source = out / "test_loading_hibernation.c"
    bitcode = out / "test_loading_hibernation.bc"
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


import unittest


class TestLoadingHibernation(unittest.TestCase):
    def test_all_loading_hibernation_scenarios(self) -> None:
        main()


if __name__ == "__main__":
    main()
