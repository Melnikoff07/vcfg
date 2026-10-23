#!/usr/bin/env python3
"""
Replay real PS4 hardware boot & arena DirectMemory traces (tools/tests/real_traces.json)
against the actual C implementation of classic/source/debug_hooks.h, classic/source/main.c,
and overlay/title_families.h compiled via clang.exe + lli.exe.

Guarantees zero regressions across all captured CUSAxxxxx titles before building a PKG.
"""

from __future__ import annotations
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")
HOOKS_H = (ROOT / "classic" / "source" / "debug_hooks.h").read_text(encoding="utf-8")
FAMILIES_H = (ROOT / "overlay" / "title_families.h").read_text(encoding="utf-8")
TRACES_PATH = ROOT / "tools" / "tests" / "real_traces.json"


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
FAMILIES_BODY = FAMILIES_H.replace("#pragma once", "").replace("#include <string.h>", "")

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

#define ASSERT_MSG(cond, tid, step, len_val) do { \
    if (!(cond)) { \
        printf("TRACE ASSERT FAILED [%s step %d len=0x%zx] at line %d: %s\n", (tid), (step), (size_t)(len_val), __LINE__, #cond); \
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
    int reg_mode;
    int pace_order;
} fg_config_t;

static char g_title_id[16] = "UNKNOWN";
static int g_known_game = 0;
static int g_ngame = 0;
static fg_config_t g_cfg = { 0, 0, 0 };
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

#define MAX_DM_BLOCKS 512
typedef struct {
    long start;
    size_t len;
    int type;
    int in_use;
} SimDmBlock;

#define MAX_FLEX_BLOCKS 512
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
static size_t g_sim_dm_total = 0x120000000ULL;
static size_t g_sim_flex_avail = 0x40000000ULL;
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
    g_sim_flex_avail = 0x40000000ULL;
    g_sim_force_misaligned_flex = 0;
    g_sim_map_dm_calls = 0;
    g_sim_map_flex_calls = 0;
    g_sim_last_flex_flags = 0;
    g_sim_fake_va_cursor = 0x550000000000ULL;

    g_dm_hooks = 1;
    g_own_alloc = 0;
    g_dbg_allocs = 0;
    g_dbg_maps = 0;
    g_dbg_alloc_fail = 0;
    g_flips = 0;
    g_gpu_state = 0;
    g_mbuf_state = 0;
    g_ngame = 0;
    g_low_sub2mb_end = 0;
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
    return 0;
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

REPLAY_HELPER_C = r"""
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
    g_syn_phys_cursor = 0;
    g_flex_vmap_n = 0;
    memset(g_flex_gpu_slots, 0, sizeof(g_flex_gpu_slots));
}

static void apply_title_defaults_sim(const char *tid)
{
    for (int i = 0; i < 15; i++) g_title_id[i] = tid[i];
    g_title_id[15] = 0;
    g_known_game = vcfg_is_verified_family(g_title_id);
    g_cfg.shave_mode = 0;
    g_cfg.reg_mode = 0;
    g_cfg.pace_order = 0;
    if (vcfg_in_family(g_title_id, "CUSA11456")) {
        g_cfg.reg_mode = 1;
    } else if (vcfg_in_family(g_title_id, "CUSA08519")) {
        g_cfg.shave_mode = 1;
    } else if (vcfg_in_family(g_title_id, "CUSA16734") || vcfg_in_family(g_title_id, "CUSA02299")) {
        g_cfg.reg_mode = 1;
    } else if (vcfg_in_family(g_title_id, "CUSA09311") ||
               vcfg_in_family(g_title_id, "CUSA18534") ||
               vcfg_in_family(g_title_id, "CUSA10123")) {
        g_cfg.reg_mode = 1;
    } else if (vcfg_in_family(g_title_id, "CUSA28561")) {
        g_cfg.shave_mode = 2;
    } else if (vcfg_in_family(g_title_id, "CUSA17722")) {
        g_cfg.reg_mode = 2;
    }
}

static void setup_plugin_load_early(size_t dm_sz, long pre_occ)
{
    sim_reset(dm_sz);
    reset_shave_state();
    if (pre_occ > 0) {
        long dummy = 0;
        g_own_alloc++;
        raw_sceKernelAllocateDirectMemory(0, pre_occ, (size_t)pre_occ, 0x4000u, 0, &dummy);
        g_own_alloc--;
        if ((pre_occ & 0x1FFFFFLL) != 0) {
            g_low_sub2mb_end = (pre_occ + 0x1FFFFFLL) & ~0x1FFFFFLL;
        }
    }
    if (g_cfg.shave_mode == 2) return;
    long top_e = (long)(dm_sz & ~(size_t)0x1FFFFFu);
    size_t want_pre = 0x2000000u;
    long top_s = top_e - (long)want_pre;
    g_own_alloc++;
    if (raw_sceKernelAllocateDirectMemory(top_s, top_e, want_pre, 0x200000u, 3, &g_pre_mem_phys) == 0) {
        raw_sceKernelMapDirectMemory(&g_pre_mem, want_pre, 0x33, 0, g_pre_mem_phys, 0x200000u);
        g_pre_size = want_pre;
    }
    g_own_alloc--;
    if (g_low_sub2mb_end > 0) {
        g_pre_gpu = alloc_dm_range(0, g_low_sub2mb_end, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    }
    if (!g_pre_gpu && g_pre_mem && g_pre_mem_phys >= 0x200000LL) {
        g_pre_gpu = alloc_dm_range(g_pre_mem_phys - 0x200000LL, g_pre_mem_phys, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    }
    if (!g_pre_gpu) g_pre_gpu = alloc_flex_gpu(g_gpu_sz, g_gpu_sz);
    g_pre_work = alloc_work_early(WORK_SZ_8, 3);
}

static void replay_register_buffers(int start_idx, int num_buf, uint32_t bw, uint32_t bh, uint32_t pitch)
{
    if (start_idx != 0 || num_buf < 2 || num_buf > 8 || g_cfg.reg_mode == 2) return;
    g_ngame = num_buf;
    size_t calc = (size_t)pitch * (size_t)((bh + 63u) & ~63u) * 4u;
    size_t one = (calc + 0x7FFFFu) & ~(size_t)0x7FFFFu;
    if (one < 0x100000u || one > 0x4000000u) return;
    if (bw < 1280u || bh < 720u || bw > 3840u || bh > 2160u) return;
    int want_pcopy = (g_ngame == 2);
    int hi_res = (bw > 1920u || bh > 1080u);
    int stream_3buf = (!want_pcopy &&
                       ((!g_known_game && g_dbg_allocs >= 32u) ||
                        vcfg_in_family(g_title_id, "CUSA09311") ||
                        vcfg_in_family(g_title_id, "CUSA18534") ||
                        vcfg_in_family(g_title_id, "CUSA10123")));
    int cap_extra = want_pcopy
        ? (vcfg_in_family(g_title_id, "CUSA11456") ? 1 : ((!g_known_game && g_dbg_allocs >= 32u) ? 2 : (hi_res ? 4 : 6)))
        : (stream_3buf ? 1 : ((hi_res || !g_known_game) ? 2 : 3));
    int max_extra = 16 - g_ngame;
    if (max_extra > cap_extra) max_extra = cap_extra;
    if (g_pre_mem) {
        int nm = (int)(g_pre_size / one);
        if (nm > max_extra) nm = max_extra;
        if (nm >= 1) {
            size_t used_pre = (one * (size_t)nm + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
            if (used_pre < 0x800000u) used_pre = 0x800000u;
            if (used_pre > 0 && used_pre < g_pre_size) {
                shrink_early_mem_to_ex(used_pre, 0);
            }
            g_mbuf_state = 1;
        }
    }
}
"""


def generate_trace_c(traces: list[dict]) -> str:
    lines: list[str] = ["int main(void) {", "    int passed = 0;"]
    for idx, tr in enumerate(traces):
        tkey = tr["trace_key"]
        tid = tr["title_id"]
        dm_sz = tr["dm_size"]
        pre_occ = tr["pre_occupied_low"]
        lines.append(f"    /* Trace {idx + 1}: {tkey} (v{tr['log_version']}) */")
        lines.append("    {")
        lines.append(f'        apply_title_defaults_sim("{tid}");')
        lines.append(f"        setup_plugin_load_early({dm_sz}ULL, {pre_occ}LL);")
        lines.append("        long id_phys[256]; size_t id_len[256];")
        lines.append("        memset(id_phys, 0, sizeof(id_phys)); memset(id_len, 0, sizeof(id_len));")
        for step_i, ev in enumerate(tr["events"]):
            flips = ev.get("flips", 0)
            lines.append(f"        g_flips = {flips}u;")
            op = ev["op"]
            if op == "release_id":
                rid = ev["id"]
                if 0 < rid < 256:
                    lines.append(
                        f"        if (id_len[{rid}] > 0) {{ sceKernelReleaseDirectMemory(id_phys[{rid}], id_len[{rid}]); id_len[{rid}] = 0; }}"
                    )
            elif op == "reg_buffers":
                lines.append(
                    f"        replay_register_buffers({ev['start']}, {ev['num']}, {ev['w']}u, {ev['h']}u, {ev['pitch']}u);"
                )
            elif op == "alloc":
                s = ev["s"]
                e = ev["e"]
                elen = ev["len"]
                align = ev["align"]
                mtype = ev["type"]
                aid = ev.get("id", 0)
                if ev["orig_ret"] == "0x0":
                    lines.append("        {")
                    lines.append("            long p = 0; void *va = NULL;")
                    lines.append(
                        f"            int32_t r = sceKernelAllocateDirectMemory({s}LL, {e}LL, {elen}ULL, {align}ULL, {mtype}, &p);"
                    )
                    lines.append(f'            ASSERT_MSG(r == 0, "{tkey}", {step_i}, {elen}ULL);')
                    lines.append(
                        f"            int32_t mr = sceKernelMapDirectMemory(&va, {elen}ULL, 0x33, 0, p, {align}ULL);"
                    )
                    lines.append(f'            ASSERT_MSG(mr == 0 && va != NULL, "{tkey}", {step_i}, {elen}ULL);')
                    if 0 < aid < 256:
                        lines.append(f"            id_phys[{aid}] = p; id_len[{aid}] = {elen}ULL;")
                    lines.append("        }")
                else:
                    lines.append("        {")
                    lines.append("            long p = 0;")
                    lines.append(
                        f"            int32_t r = sceKernelAllocateDirectMemory({s}LL, {e}LL, {elen}ULL, {align}ULL, {mtype}, &p);"
                    )
                    lines.append(f"            if (r == 0) sceKernelReleaseDirectMemory(p, {elen}ULL);")
                    lines.append("        }")
        if tid in ("CUSA07820", "CUSA10249", "CUSA00341", "CUSA07737"):
            lines.append(
                f'        for (int si = 0; si < g_shave_n; si++) ASSERT_MSG(g_shaves[si].real_len == 0, "{tkey}", 999, g_shaves[si].orig_len);'
            )
        lines.append(f'        ASSERT_MSG(on_sub() >= 0, "{tkey}", 1000, 0);')
        lines.append("        passed++;")
        lines.append("    }")
    lines.append('    printf("real hardware traces replay: %d/%d CUSAxxxxx boot traces passed 100%%\\n", passed, passed);')
    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines)


def main() -> None:
    traces = json.loads(TRACES_PATH.read_text(encoding="utf-8"))
    out = ROOT / "build" / "tests"
    out.mkdir(parents=True, exist_ok=True)
    source = out / "test_real_traces.c"
    bitcode = out / "test_real_traces.bc"
    full_c = (
        HARNESS_C
        + "\n"
        + FAMILIES_BODY
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
        + REPLAY_HELPER_C
        + "\n"
        + generate_trace_c(traces)
    )
    full_c = re.sub(r"\b(0x[0-9a-fA-F]+)L\b", r"\1LL", full_c)
    full_c = re.sub(r"\b(\d+)L\b", r"\1LL", full_c)
    full_c = full_c.replace("unsigned long long", "uint64_t").replace("long long", "int64_t").replace("unsigned long", "uint64_t")
    full_c = re.sub(r"\blong\b", "int64_t", full_c)
    source.write_text(full_c, encoding="utf-8")
    llvm = pathlib.Path(cfg["llvm_bin"])
    subprocess.run(
        [
            str(llvm / "clang.exe"),
            "--target=x86_64-pc-windows-msvc",
            "-O0",
            "-Wall",
            "-Wextra",
            "-Wno-unused-function",
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
