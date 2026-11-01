#!/usr/bin/env python3
"""Unit & synthetic-trace regression tests for CUSA06782 SIGABRT fixes (v2.36-all Task T1).

Compiles the real allocator, Memory Valve, Arena Shaver, Hibernation Recovery, vov_ready,
and adapt.ini crash/load logic from classic/source/main.c and classic/source/debug_hooks.h
to LLVM bitcode via clang.exe and executes via lli.exe.

Covers:
- D1: Step 6 (sceKernelAllocateDirectMemory_hook) must not trigger false hibernation when
      g_pre_mem does not overlap the requested [s, e) window (or when a non-fatal first
      ranged probe 0x19ca9d2 fails while the full pool still has >= len free, so probe 2
      0x19caa19 succeeds on its own).
- D2: Full plugin DM release (g_pre_mem, g_pre_gpu, g_pre_work, vov) on genuine pool deficit
      + clean FG recovery if the game later frees the zone.
- D3: Single shave (g_shave_n < 1, including shave_mode=1) spent only when cur_av < len
      across the entire pool, never on a non-fatal ranged probe.
- D4: vov_ready on CUSA06782 never falls back to alloc_dm(0x60000, 0x10000); uses g_pbuf
      after release_pbuf or disables the overlay.
- D5: adapt.ini on CUSA06782 never rolls back shave_mode > 0 to 0 or ping-pongs 1<->2<->0.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import unittest

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
GPU_INIT_ALLOC = extract_between(
    MAIN_C,
    "if (!g_pre_gpu) g_gpu_sz =",
    'if (!addr) { STAGE("gpu_init FAILED',
)
REG_HOOK_SRC = extract_between(
    MAIN_C,
    "int32_t sceVideoOutRegisterBuffers_hook(",
    "// ---- in-game L3+R3 (3s) menu",
)
REG_CLAMP_BLOCK = extract_between(
    REG_HOOK_SRC,
    "if (g_pitch >= 640 && g_bh >= 360) {",
    "if (one < 0x100000",
)
MAIN_VOV_READY = extract_between(
    MAIN_C,
    "static const uint8_t *g_vov_pal",
    "static const char *overlay_label",
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
extern void *memcpy(void *, const void *, size_t);
extern void *calloc(size_t, size_t);
extern void free(void *);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, size_t);
extern char *strstr(const char *, const char *);
extern size_t strlen(const char *);
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
#define VOV_W       512u
#define VOV_MAX_H   720u
#define VOV_PIX_SZ  (VOV_W * VOV_MAX_H)
#define VOV_PAL_SZ  1024u
#define VOV_BUF_SZ  (VOV_PAL_SZ + VOV_PIX_SZ)
#define VOV_NBUF    10u
#define VOV_MEM_SZ  (VOV_NBUF * VOV_BUF_SZ)

static void vov_init_size(uint8_t *mem, size_t size) { (void)mem; (void)size; }
static int gv_armed(void) { return 0; }
static void vov_read_title(char *out, size_t cap) { if (cap) out[0] = 0; }
static void vov_cpy(char *d, const char *s, size_t cap)
{
    if (!cap) return;
    size_t i = 0;
    while (s && s[i] && i + 1 < cap) { d[i] = s[i]; i++; }
    d[i] = 0;
}

typedef struct {
    int framegen;
    int mode;
    int preset;
    int sharpness;
    int upscale;
    int upscale_factor;
    int present_copy;
    int two_buffer;
    int manual_mode;
    int pace_order;
    int shave_mode;
    int reg_mode;
    int post_mid;
    int hud_protect;
    int game_vectors;
    int fps_lock;
    int menu_hold_ms;
    int menu_opacity;
    int overlay;
    uint32_t menu_combo;
    int vout_path;
    int gv_conv;
    int audio_fix;
} fg_config_t;

static char g_title_id[16] = "CUSA06782";
static fg_config_t g_cfg = { 0 };
static int vcfg_in_family(const char *tid, const char *fam) { return strcmp(tid, fam) == 0; }
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

#ifndef VCFG_VOV_STATE_DEFINED
#define VCFG_VOV_STATE_DEFINED
static void *g_vov_mem __attribute__((unused)) = NULL;
static long g_vov_phys __attribute__((unused)) = 0;
static size_t g_vov_sz __attribute__((unused)) = 0;
static int g_vov_state __attribute__((unused)) = 0;
static int g_pbuf_yielded_to_vov __attribute__((unused)) = 0;
#endif

static volatile uint32_t g_map_run_cnt = 0;
static long g_map_run_phys0 = 0, g_map_run_phys_last = 0;
static uintptr_t g_map_run_va0 = 0, g_map_run_va_last = 0;
static int g_map_run_prot = 0, g_map_run_flags = 0;

static void flush_map_run(void) { g_map_run_cnt = 0; }
static void api_ring_push(const char *fmt, ...) { (void)fmt; }
static void stage_nop(const char *fmt, ...) { (void)fmt; }
static void lg(const char *fmt, ...) __attribute__((unused));
static void lg(const char *fmt, ...) { (void)fmt; }
#define STAGE(...) stage_nop(__VA_ARGS__)
#define klog(...)  stage_nop(__VA_ARGS__)

#define MAX_DM_BLOCKS 64
typedef struct {
    long start;
    size_t len;
    int type;
    int in_use;
} SimDmBlock;

#define MAX_FLEX_BLOCKS 64
typedef struct {
    uintptr_t va;
    size_t len;
    int prot;
    int flags;
    int in_use;
} SimFlexBlock;

static SimDmBlock g_sim_dm[MAX_DM_BLOCKS];
static SimFlexBlock g_sim_flex[MAX_FLEX_BLOCKS];
static size_t g_sim_dm_total = 0x133000000ULL;
static size_t g_sim_flex_avail = 0x8000000u;
static uintptr_t g_sim_fake_va_cursor = 0x550000000000ULL;
static uint8_t g_backing_buf[0x2400000 + 0x40000];

static void *sim_memset(void *dst, int c, size_t n)
{
    uintptr_t d = (uintptr_t)dst;
    if (d >= 0x550000000000ULL && d < 0x560000000000ULL)
        return dst;
    return memset(dst, c, n);
}
#define memset sim_memset

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
    g_sim_flex_avail = 0x8000000u;
    g_sim_fake_va_cursor = 0x550000000000ULL;
    g_own_alloc = 0;
    g_dbg_allocs = 0;
    g_dbg_maps = 0;
    g_dbg_alloc_fail = 0;
    g_flips = 0;
    g_vo_submit_flips = 0;
    g_gpu_state = 0;
    g_mbuf_state = 0;
    g_gpu = NULL;
    g_mon = NULL;
    g_safe_never = 0;
    g_fg_off = 0;
    g_low_sub2mb_end = 0;
    g_gpu_sz = GPU_SZ_BASE;
    g_work_garlic = 0;
    g_mem_hibernate_count = 0;
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
    g_vov_mem = NULL;
    g_vov_phys = 0;
    g_vov_sz = 0;
    g_vov_state = 0;
    g_pbuf_yielded_to_vov = 0;
    memset(&g_cfg, 0, sizeof(g_cfg));
}

static int sceKernelUsleep(uint32_t us) { (void)us; return 0; }

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

static size_t sceKernelGetDirectMemorySize(void) { return g_sim_dm_total; }

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

static int32_t sceKernelAvailableFlexibleMemorySize(size_t *sizeOut)
{
    if (!sizeOut) return -1;
    *sizeOut = g_sim_flex_avail;
    return 0;
}

static int32_t raw_sceKernelMapDirectMemory2(void **addr, size_t len, int type, int prot, int flags, long dmStart, size_t maxPageSize)
{
    (void)type; (void)maxPageSize;
    if (!sim_dm_is_allocated_range(dmStart, len))
        return (int32_t)0x80020016u;
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
    if (g_sim_flex_avail < len) return (int32_t)0x8002000cu;
    g_sim_flex_avail -= len;
    uintptr_t base = 0;
    if (g_own_alloc && len <= sizeof(g_backing_buf)) {
        base = ((uintptr_t)g_backing_buf + 0x3FFFFu) & ~(uintptr_t)0x3FFFFu;
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
            g_sim_flex_avail += g_sim_flex[i].len;
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
static void reset_hook_state(void)
{
    (void)alloc_gv_dm;
    (void)release_early_dump;
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
    g_shave_last_probe_s = -1;
    g_shave_last_probe_e = -1;
    g_shave_last_probe_len = 0;
    g_flex_vmap_n = 0;
    memset(g_flex_gpu_slots, 0, sizeof(g_flex_gpu_slots));
}

// D1: Synthetic trace from logs 39a8c6990d (f:5938) & bce36005df (f:3930).
// g_pre_mem is at 0x8e00000 (len=0xa00000, outside requested window 0xfb300000..0x133000000),
// while [0xfb300000, 0x133000000) has 0x3600000 (54 MB) free and [0xf7300000, 0xfb300000)
// has 0x4000000 (64 MB) free. Probe 1 (caller 0x19ca9d2, range 0xfb300000..0x133000000)
// must NOT trigger false hibernation or destroy g_pre_mem, and Probe 2 (caller 0x19caa19,
// range 0xf7300000..0x133000000) succeeds on its own with FG still active.
static void test_d1_no_false_hibernation_on_ranged_probe(void)
{
    sim_reset(0x133000000ULL);
    reset_hook_state();
    memcpy(g_title_id, "CUSA06782", 10);
    g_cfg.shave_mode = 2;
    g_sim_flex_avail = 0; // Flex tight so shave_game_alloc cannot shave Probe 1

    // Occupy lower pool leaving g_pre_mem at 0x8e00000..0x9800000 (10 MB):
    long dummy = 0, p_mem = 0;
    void *v_mem = NULL;
    g_own_alloc = 1;
    ASSERT(raw_sceKernelAllocateDirectMemory(0, 0x8e00000L, 0x8e00000u, 0x200000u, 3, &dummy) == 0);
    ASSERT(raw_sceKernelAllocateDirectMemory(0x8e00000L, 0x9800000L, 0xa00000u, 0x200000u, 3, &p_mem) == 0);
    ASSERT(raw_sceKernelMapDirectMemory(&v_mem, 0xa00000u, 0x33, 0, p_mem, 0x200000u) == 0);
    // Occupy 0x9800000..0xf7300000:
    ASSERT(raw_sceKernelAllocateDirectMemory(0x9800000L, 0xf7300000L, 0xedB00000u, 0x100000u, 3, &dummy) == 0);
    // Leave [0xf7400000, 0xfb400000) = 64 MB (2MB-aligned) free for Probe 2 (0x19caa19),
    // occupy [0xfb400000, 0x12fa00000), leaving [0x12fa00000, 0x133000000) = 54 MB free inside Probe 1 window:
    ASSERT(raw_sceKernelAllocateDirectMemory(0xfb400000L, 0x12fa00000L, 0x34600000u, 0x200000u, 3, &dummy) == 0);
    g_own_alloc = 0;

    g_pre_mem = v_mem;
    g_pre_mem_phys = p_mem;
    g_pre_size = 0xa00000u;
    g_pre_work = (void *)0x550090000000ULL;
    g_pre_work_phys = -1L;
    g_pre_work_sz = WORK_SZ_8;
    g_work = (uint8_t *)g_pre_work;
    g_mbuf_state = 1;
    g_gpu_state = 1;
    g_flips = 5938u;

    // Probe 1 (caller 0x19ca9d2): requests 64 MB in 0xfb300000..0x133000000 (where cur_av = 54 MB):
    long probe1_phys = 0;
    int32_t r1 = sceKernelAllocateDirectMemory(0xfb300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &probe1_phys);
    ASSERT((uint32_t)r1 == 0x80020023u);
    // Must NOT have triggered false hibernation or killed g_pre_mem!
    ASSERT(g_pre_mem != NULL);
    ASSERT(g_mbuf_state == 1);
    ASSERT(g_mem_hibernating == 0);
    ASSERT(g_mem_hibernate_count == 0);

    // Probe 2 (caller 0x19caa19): steps down by 64 MB to 0xf7300000..0x133000000 and succeeds on its own:
    long probe2_phys = 0;
    int32_t r2 = sceKernelAllocateDirectMemory(0xf7300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &probe2_phys);
    ASSERT(r2 == 0);
    ASSERT(probe2_phys >= 0xf7300000L);
    ASSERT(g_pre_mem != NULL && g_mbuf_state == 1);
    ASSERT(on_sub() == 1);
}

// D2: Synthetic trace from logs 061dd1094d, 39a8c6990d, f24be2ff0c, bce36005df & REVIEW_T1.md §3.1-3.2.
// 1. At RegisterBuffers (39a8c6990d / bce36005df f:0, where buf[1] - buf[0] = 0x810000), CUSA06782 must
//    clamp one to surf_sz = 0x800000 so g_pre_mem is 8 MB (0x800000) instead of 10 MB (0xa00000).
// 2. At gpu_init (061dd1094d / f24be2ff0c f:120, where g_pre_mem is at 0x102000000 and 0x101e00000..0x102000000
//    is free), CUSA06782 must allocate g_gpu (0x20000) in FlexibleMemory first and NEVER pin the 2 MB
//    DirectMemory slot [g_pre_mem_phys - 0x200000, g_pre_mem_phys).
// 3. At runtime zone allocation (len = 0x4000000), Step 6 must NEVER destructively unmap live scanout
//    memory g_pre_mem or g_gpu (zero UAF on active VideoOut scanout buffer or mon_thread).
static void *sim_gpu_init_alloc(void)
{
    __GPU_INIT_ALLOC__
    g_gpu = (uint8_t *)addr;
    g_gpu_state = 1;
    return addr;
}

static size_t sim_clamp_reg_stride(size_t one, uint32_t bw, uint32_t bh, uint32_t pitch)
{
    uint32_t g_bw = bw, g_bh = bh, g_pitch = pitch;
    (void)g_bw;
    __REG_CLAMP_BLOCK__
    return one;
}

static void test_d2_proactive_minimal_footprint_no_destructive_hibernation(void)
{
    sim_reset(0x133000000ULL);
    reset_hook_state();
    memcpy(g_title_id, "CUSA06782", 10);
    g_cfg.shave_mode = 2;
    g_sim_flex_avail = 0x800000u;

    // 1. In 39a8c6990d / bce36005df (f:0), buf[1] - buf[0] = 0x810000 at 1920x1080 pitch=1920:
    //    must clamp one to 0x800000 (8 MB) even when g_pre_mem == NULL (shave_mode=2):
    size_t one = sim_clamp_reg_stride(0x810000u, 1920u, 1080u, 1920u);
    ASSERT(one == 0x800000u);

    // 2. In 061dd1094d / f24be2ff0c (f:120), g_pre_mem (8 MB) is at 0x102000000..0x102800000,
    //    and [0x101e00000, 0x102000000) is a free 2 MB DirectMemory page right below g_pre_mem:
    long dummy = 0, p_mem = 0;
    void *v_mem = NULL;
    g_own_alloc = 1;
    ASSERT(raw_sceKernelAllocateDirectMemory(0, 0xfe600000L, 0xfe600000u, 0x200000u, 3, &dummy) == 0);
    ASSERT(raw_sceKernelAllocateDirectMemory(0x102000000L, 0x102800000L, 0x800000u, 0x200000u, 3, &p_mem) == 0);
    ASSERT(raw_sceKernelMapDirectMemory(&v_mem, 0x800000u, 0x33, 0, p_mem, 0x200000u) == 0);
    ASSERT(raw_sceKernelAllocateDirectMemory(0x102800000L, 0x133000000L, 0x30800000u, 0x200000u, 3, &dummy) == 0);
    g_own_alloc = 0;

    g_pre_mem = v_mem;
    g_pre_mem_phys = p_mem;
    g_pre_size = 0x800000u;
    g_mbuf_state = 1;

    // gpu_init must allocate g_gpu (0x20000) in FlexibleMemory, leaving [0x101e00000, 0x102000000) 100% free in DM!
    void *v_gpu = sim_gpu_init_alloc();
    ASSERT(v_gpu != NULL);
    ASSERT(dm_avail_range(0x101e00000L, 0x102000000L, 0x200000u, NULL) == 0x200000u);
    // Total contiguous 2MB-aligned DirectMemory below g_pre_mem is now [0xfe600000, 0x102000000) = 0x3a00000 (58 MB, +4 MB saved vs v2.35):
    ASSERT(dm_avail(0x200000u) == 0x3a00000u);

    // 3. When FlexibleMemory is 0 at runtime (f:85726 / f:12902) and the game requests 64 MB (0x4000000),
    //    Step 6 must NEVER unmap live g_pre_mem or g_gpu:
    g_sim_flex_avail = 0;
    g_flips = 85726u;
    long zone_phys = 0;
    int32_t r = sceKernelAllocateDirectMemory(0, 0x133000000L, 0x4000000u, 0x200000u, 3, &zone_phys);
    ASSERT((uint32_t)r == 0x80020023u);
    ASSERT(g_pre_mem == v_mem);
    ASSERT(g_gpu == (uint8_t *)v_gpu);
    ASSERT(g_mbuf_state == 1 && g_gpu_state == 1);
    ASSERT(g_mem_hibernating == 0);
}

// D3: Synthetic trace from log 39a8c6990d (f:73024) & AUTHOR_T1.md D3.
// Single shave (g_shave_n < 1, including shave_mode=1) must NOT be spent on a non-fatal first
// ranged probe (0x19ca9d2) when the full pool still has >= len free; it must only be spent when
// cur_av < len across the entire pool.
static void test_d3_single_shave_only_when_full_pool_deficit(void)
{
    sim_reset(0x133000000ULL);
    reset_hook_state();
    memcpy(g_title_id, "CUSA06782", 10);
    g_cfg.shave_mode = 2;
    g_sim_flex_avail = 0x2000000u; // 32 MB Flex available

    long dummy = 0, p_mem = 0;
    void *v_mem = NULL;
    g_own_alloc = 1;
    // Occupy 0..0x8e00000, place g_pre_mem (8 MB) at 0x8e00000..0x9600000, occupy 0x9600000..0xb1400000:
    ASSERT(raw_sceKernelAllocateDirectMemory(0, 0x8e00000L, 0x8e00000u, 0x200000u, 3, &dummy) == 0);
    ASSERT(raw_sceKernelAllocateDirectMemory(0x8e00000L, 0x9600000L, 0x800000u, 0x200000u, 3, &p_mem) == 0);
    ASSERT(raw_sceKernelMapDirectMemory(&v_mem, 0x800000u, 0x33, 0, p_mem, 0x200000u) == 0);
    ASSERT(raw_sceKernelAllocateDirectMemory(0x9600000L, 0xb1400000L, 0xa7e00000u, 0x200000u, 3, &dummy) == 0);
    // Leave [0xb1400000, 0xb5400000) = 64 MB (2MB-aligned) free for Probe 2 (0x19caa19).
    // In [0xb5400000, 0x133000000), leave [0xb5400000, 0xb9000000) = 0x3c00000 (60 MB) free and occupy [0xb9000000, 0x133000000):
    ASSERT(raw_sceKernelAllocateDirectMemory(0xb9000000L, 0x133000000L, 0x7a000000u, 0x200000u, 3, &dummy) == 0);
    g_own_alloc = 0;

    g_pre_mem = v_mem;
    g_pre_mem_phys = p_mem;
    g_pre_size = 0x800000u;
    g_mbuf_state = 1;
    g_gpu_state = 1;
    g_flips = 73024u;

    // 1. Probe 1 (caller 0x19ca9d2) in 0xb5300000..0x133000000 sees shave_av = 60 MB < 64 MB,
    //    but full pool has 64 MB free at 0xb1400000..0xb5400000 -> must NOT spend shave!
    long z1_phys = 0;
    int32_t r1 = sceKernelAllocateDirectMemory(0xb5300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &z1_phys);
    ASSERT((uint32_t)r1 == 0x80020023u);
    ASSERT(g_shave_n == 0);

    // 2. Probe 2 (caller 0x19caa19) in 0xb1300000..0x133000000 succeeds unshaved in real DirectMemory:
    int32_t r2 = sceKernelAllocateDirectMemory(0xb1300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &z1_phys);
    ASSERT(r2 == 0);
    ASSERT(g_shave_n == 0);

    // 3. Now full pool only has 60 MB (0x3c00000 < 0x4000000) free at 0xb5400000..0xb9000000.
    //    Even with shave_mode = 1, the single shave (g_shave_n < 1) must be allowed when cur_av < len in the full pool,
    //    and must not fire on an upper probe 0xfb300000..0x133000000 where shave_av == 0 < pool_av:
    g_cfg.shave_mode = 1;
    long z2_phys = 0;
    int32_t r3 = sceKernelAllocateDirectMemory(0xfb300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &z2_phys);
    ASSERT((uint32_t)r3 == 0x80020023u);
    ASSERT(g_shave_n == 0);

    int32_t r4 = sceKernelAllocateDirectMemory(0xb5300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &z2_phys);
    ASSERT(r4 == 0);
    ASSERT(g_shave_n == 1);
    ASSERT(g_shaves[0].real_len == 0x3c00000u && g_shaves[0].orig_len == 0x4000000u);
    ASSERT(g_pre_mem != NULL && g_mbuf_state == 1);

    // 4. Subsequent shave (#2, as in 39a8c6990d f:73024): when a second 64 MB zone has a 6 MB deficit
    //    (0x3a00000 = 58 MB free in full pool) and FlexibleMemory has room (g_sim_flex_avail >= 6 MB),
    //    shave #2 must be allowed (not blocked by a hard g_shave_n >= 1 cap):
    ASSERT(sceKernelReleaseDirectMemory(0xb1400000L, 0x3a00000u) == 0);
    long z3_phys = 0;
    // Upper probe with shave_av == 0 < pool_av (58 MB) is still rejected:
    int32_t r5 = sceKernelAllocateDirectMemory(0xfb300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &z3_phys);
    ASSERT((uint32_t)r5 == 0x80020023u);
    ASSERT(g_shave_n == 1);
    // Probe covering the 58 MB free block succeeds via shave #2 (6 MB tail in FlexibleMemory):
    int32_t r6 = sceKernelAllocateDirectMemory(0xb1300000L, 0x133000000L, 0x4000000u, 0x200000u, 3, &z3_phys);
    ASSERT(r6 == 0);
    ASSERT(g_shave_n == 2);
    ASSERT(g_shaves[1].real_len == 0x3a00000u && g_shaves[1].orig_len == 0x4000000u);
}

// D4: Synthetic trace from logs 39a8c6990d, f24be2ff0c, bce36005df (f:120) & REVIEW_T1.md §3.3.
// In build_dcb_flow8, fg8_resolve_2buf_history puts g_pbuf VA into P and copy_dst BEFORE osd_build_slot
// calls vov_ready(). Therefore vov_ready() on CUSA06782 must NEVER allocate in DirectMemory (0 DM bytes)
// and must NEVER call release_pbuf() (which would cause a UAF when pcopy8 writes to copy_dst):
// it must either fit in small FlexibleMemory (0x30000) or disable the overlay (g_vov_state = -1).
static void test_d4_vov_ready_no_dm_and_no_pbuf_uaf(void)
{
    (void)g_vov_pal;
    (void)g_vov_pix;
    sim_reset(0x133000000ULL);
    reset_hook_state();
    memcpy(g_title_id, "CUSA06782", 10);
    g_mem_tight = 1;

    // 1. Allocate g_pbuf in FlexibleMemory, then leave only 0x30000 (192 KB) of FlexibleMemory
    //    (so min_full_vsz = 0x60000 fails, while 0x30000 fits):
    g_sim_flex_avail = 0x1800000u;
    ASSERT(alloc_pbuf_history(0x800000u) != NULL);
    ASSERT(g_pbuf != NULL && g_pbuf_phys == -1L);
    void *saved_pbuf = g_pbuf;
    uint64_t copy_dst = (uint64_t)(uintptr_t)g_pbuf;
    g_sim_flex_avail = 0x30000u;

    size_t dm_before = dm_avail(0x200000u);
    size_t dm_16k_before = dm_avail(0x4000u);
    ASSERT(vov_ready() == 1);
    ASSERT(g_vov_state == 1);
    // Must NOT have freed or unmapped g_pbuf, and must have used 0 bytes of DirectMemory:
    ASSERT(g_pbuf == saved_pbuf);
    ASSERT((uint64_t)(uintptr_t)g_pbuf == copy_dst);
    ASSERT(dm_avail(0x200000u) == dm_before && dm_avail(0x4000u) == dm_16k_before);

    // 2. When remaining FlexibleMemory is 0 (< 0x30000) while g_pbuf is active in build_dcb_flow8,
    //    vov_ready() must disable the overlay (g_vov_state = -1, return 0) without touching DirectMemory
    //    and WITHOUT freeing g_pbuf (no UAF on copy_dst):
    sim_reset(0x133000000ULL);
    reset_hook_state();
    memcpy(g_title_id, "CUSA06782", 10);
    g_mem_tight = 1;
    g_sim_flex_avail = 0x1800000u;
    ASSERT(alloc_pbuf_history(0x800000u) != NULL);
    saved_pbuf = g_pbuf;
    copy_dst = (uint64_t)(uintptr_t)g_pbuf;
    g_sim_flex_avail = 0;
    dm_before = dm_avail(0x200000u);
    dm_16k_before = dm_avail(0x4000u);

    ASSERT(vov_ready() == 0);
    ASSERT(g_vov_state == -1);
    ASSERT(g_pbuf == saved_pbuf);
    ASSERT((uint64_t)(uintptr_t)g_pbuf == copy_dst);
    ASSERT(dm_avail(0x200000u) == dm_before && dm_avail(0x4000u) == dm_16k_before);

    // 3. Blocker #1 (T3c / 7a63890): free_dm_block must only cascade-free g_pbuf when freeing g_pre_mem or g_pre_work
    //    (va == &g_pre_mem || va == &g_pre_work), matching 41753df 1:1. Calling release_early_gpu() or release_early_dump()
    //    on non-CUSA06782 or CUSA06782 must NEVER free a live g_pbuf.
    const char *tids[2] = { "CUSA00552", "CUSA06782" };
    for (int ti = 0; ti < 2; ti++) {
        sim_reset(0x133000000ULL);
        reset_hook_state();
        memcpy(g_title_id, tids[ti], 10);
        g_sim_flex_avail = 0x2000000u;
        g_flips = 200u;
        ASSERT(alloc_pbuf_history(0x800000u) != NULL && g_pbuf != NULL);
        saved_pbuf = g_pbuf;
        g_pbuf_ycocg = 1;
        g_pbuf_valid = 1;
        g_pbuf_last_seq = 55u;
        g_pbuf_prev_va = 0xAABBCCDDEEULL;

        g_pre_gpu = alloc_dm(g_gpu_sz, g_gpu_sz, 0);
        ASSERT(g_pre_gpu != NULL);
        release_early_gpu();
        ASSERT(g_pre_gpu == NULL);
        ASSERT(g_pbuf == saved_pbuf && g_pbuf_valid == 1 && g_pbuf_ycocg == 1 &&
               g_pbuf_last_seq == 55u && g_pbuf_prev_va == 0xAABBCCDDEEULL);

        g_dump_mem = (uint8_t *)alloc_dm(DUMP_SZ, 0x200000u, 0);
        ASSERT(g_dump_mem != NULL);
        release_early_dump();
        ASSERT(g_dump_mem == NULL);
        ASSERT(g_pbuf == saved_pbuf && g_pbuf_valid == 1 && g_pbuf_ycocg == 1 &&
               g_pbuf_last_seq == 55u && g_pbuf_prev_va == 0xAABBCCDDEEULL);

        g_pre_work = alloc_dm(WORK_SZ_8, 0x10000u, 3);
        g_pre_work_sz = WORK_SZ_8;
        ASSERT(g_pre_work != NULL);
        release_early_work();
        ASSERT(g_pre_work == NULL);
        ASSERT(g_pbuf == NULL && g_pbuf_valid == 0 && g_pbuf_ycocg == 0 &&
               g_pbuf_last_seq == 0 && g_pbuf_prev_va == 0);
    }
}

int main(void)
{
    test_d1_no_false_hibernation_on_ranged_probe();
    test_d2_proactive_minimal_footprint_no_destructive_hibernation();
    test_d3_single_shave_only_when_full_pool_deficit();
    test_d4_vov_ready_no_dm_and_no_pbuf_uaf();
    return 0;
}
"""


def _extract_c_function(text: str, name: str) -> str:
    m = re.search(r"^static [^\n]+\b" + re.escape(name) + r"\([^\n]*\)\n\{", text, re.M)
    if not m:
        raise RuntimeError(f"Function {name} not found")
    start = m.start()
    pos = text.index("{", start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def run_d5_adapt_tests() -> None:
    """D5: Verify CUSA06782 adapt.ini crash/load logic does not roll back shave_mode > 0 to 0 or ping-pong 1<->2<->0."""
    struct_def = re.search(r"typedef struct \{.*?\} fg_config_t;", MAIN_C, re.S)[0]
    families_h = (
        (ROOT / "overlay" / "title_families.h")
        .read_text(encoding="utf-8")
        .replace("#include <string.h>", "")
        .replace("#pragma once", "")
    )
    fn_names = [
        "fnv1a_bytes",
        "cfg_apply_title_defaults",
        "cfg_defaults",
        "parse_ini_file_ex",
        "parse_ini_file",
        "load_adapt_ini",
        "apply_adapt_ini",
        "save_adapt_ini",
        "read_mode_raw",
        "read_present_copy_raw",
        "read_two_buffer_raw",
        "load_early_adapt_cfg",
    ]
    crash_mod_def = re.search(
        r"typedef struct \{\n    char name\[64\];.*?\} CrashModEntry;\nstatic CrashModEntry g_crash_mods\[256\];\nstatic int g_crash_nmods = 0;",
        HOOKS_H,
        re.S,
    )[0]
    self_adapt_m = re.search(
        r"(char tp_mod\[64\] = \"\";.*?)\n    crash_write_both\(fd_c, fd_b, \"=== END CRASH REPORT ===\\n\"\);",
        HOOKS_H,
        re.S,
    )
    assert self_adapt_m is not None
    crash_self_adapt_body = self_adapt_m.group(1)
    extracted_fns = (
        crash_mod_def
        + "\n\n"
        + _extract_c_function(HOOKS_H, "is_third_party_prx_name")
        + "\n\n"
        + _extract_c_function(HOOKS_H, "crash_resolve_third_party_mod")
        + "\n\n"
        + "\n\n".join(_extract_c_function(MAIN_C, n) for n in fn_names)
        + f"\n\nstatic void sim_crash_self_adapt(void)\n{{\n    int fd_c = -1, fd_b = -1;\n    const void *uctx = NULL;\n    uintptr_t si_addr = 0;\n    {crash_self_adapt_body}\n}}\n"
    )
    out = ROOT / "build" / "tests"
    out.mkdir(parents=True, exist_ok=True)
    c_path = out / "test_cusa06782_d5.c"
    bc_path = out / "test_cusa06782_d5.bc"
    c_src = f"""#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include "menu_combo.h"

extern int sscanf(const char *, const char *, ...);
extern int vsprintf(char *, const char *, va_list);
extern int printf(const char *, ...);
extern void exit(int);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, size_t);
extern char *strstr(const char *, const char *);
extern size_t strlen(const char *);
extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);

#define ASSERT(cond) do {{ \\
    if (!(cond)) {{ \\
        printf("ASSERT FAILED at line %d: %s\\n", __LINE__, #cond); \\
        exit(1); \\
    }} \\
}} while (0)

static int snprintf(char *buf, size_t sz, const char *fmt, ...)
{{
    (void)sz;
    va_list ap;
    va_start(ap, fmt);
    int r = vsprintf(buf, fmt, ap);
    va_end(ap);
    return r;
}}

#define LOG_DIR "/data/bb_probe"
#define INJECT_AFTER_FLIPS 120
#define STAGE(...) do {{}} while (0)

{families_h}
{struct_def}

static char g_title_id[16] = "CUSA06782";
static int g_known_game = 1, g_detected_60fps = 0, g_is_neo = 0, g_boot_preset = 0;
static int g_tile_override = -1, g_tile_sweep = 0, g_t6_bit_override = -1;
static volatile fg_config_t g_cfg;
static volatile int g_fg_off = 0;
static volatile uint32_t g_mem_hibernate_count = 0;
static volatile int g_adapt_shave2_failed = 0;
static volatile int g_adapt_early_crashes = 0;
static volatile uint32_t g_vout_fg_inited = 0;
static volatile uint32_t g_injects = 0;
static uint32_t g_last_adapt_sig = 0;
static uint32_t g_flips = 0;
static volatile uint32_t g_vo_submit_flips = 0;
static volatile uint32_t g_dbg_alloc_fail = 0;
static volatile int g_last_pace_pre = 0;
static int g_shave_n = 0;
static int g_ngame = 3;
static int g_game_idx_hi = 3;
volatile int g_gv_cal_winner = -1;
volatile int g_gv_cal_locked = 0;

static void init_title_id(void) {{}}

static char vfs_adapt[1024];
static char vfs_config[1024];
static char vfs_global[1024];
static char vfs_safe_state[16];

static int sceKernelMkdir(const char *p, int mode) {{ (void)p; (void)mode; return 0; }}
static int sceKernelOpen(const char *p, int flags, int mode) {{
    (void)mode;
    if (strstr(p, "adapt-")) {{
        if (flags != 0) vfs_adapt[0] = 0;
        else if (!vfs_adapt[0]) return -1;
        return 10;
    }}
    if (strstr(p, "config-")) {{
        if (!vfs_config[0]) return -1;
        return 11;
    }}
    if (strstr(p, "config.ini")) {{
        if (!vfs_global[0]) return -1;
        return 12;
    }}
    if (strstr(p, "safe-") && strstr(p, ".state")) {{
        if (flags != 0) vfs_safe_state[0] = 0;
        else if (!vfs_safe_state[0]) return -1;
        return 13;
    }}
    return -1;
}}
static long sceKernelRead(int fd, void *buf, size_t n) {{
    const char *src = (fd == 10) ? vfs_adapt : (fd == 11 ? vfs_config : (fd == 12 ? vfs_global : (fd == 13 ? vfs_safe_state : "")));
    size_t len = strlen(src);
    if (len > n) len = n;
    memcpy(buf, src, len);
    return (long)len;
}}
static long sceKernelWrite(int fd, const void *buf, size_t n) {{
    if (fd == 13) {{
        if (n >= sizeof(vfs_safe_state)) n = sizeof(vfs_safe_state) - 1;
        memcpy(vfs_safe_state, buf, n);
        vfs_safe_state[n] = 0;
        return (long)n;
    }}
    if (fd != 10) return -1;
    if (n >= sizeof(vfs_adapt)) n = sizeof(vfs_adapt) - 1;
    memcpy(vfs_adapt, buf, n);
    vfs_adapt[n] = 0;
    return (long)n;
}}
static int sceKernelClose(int fd) {{ (void)fd; return 0; }}
static int sceKernelUnlink(const char *p) {{ (void)p; return 0; }}
static void crash_write_both(int fd_c, int fd_b, const char *fmt, ...) {{ (void)fd_c; (void)fd_b; (void)fmt; }}

{extracted_fns}

static void reset_d5_state(void) {{
    memcpy(g_title_id, "CUSA06782", 10);
    vfs_adapt[0] = 0;
    vfs_config[0] = 0;
    vfs_global[0] = 0;
    vfs_safe_state[0] = 0;
    g_last_adapt_sig = 0;
    g_mem_hibernate_count = 0;
    g_adapt_shave2_failed = 0;
    g_adapt_early_crashes = 0;
    g_fg_off = 0;
    g_vout_fg_inited = 0;
    g_injects = 0;
    g_flips = 0;
    g_vo_submit_flips = 0;
    g_dbg_alloc_fail = 0;
    g_last_pace_pre = 0;
    g_shave_n = 0;
    g_ngame = 3;
    g_game_idx_hi = 3;
    g_gv_cal_winner = -1;
    g_gv_cal_locked = 0;
    g_crash_nmods = 0;
    cfg_defaults((fg_config_t *)&g_cfg);
}}

int main(void) {{
    // 1. Logs 061dd1094d (f:183535), 39a8c6990d (f:73024), bce36005df (f:12902):
    //    CUSA06782 crashes with shave_mode=2, alloc_fail=1 -> must NOT roll back to shave_mode=0 or set shave2_failed=1.
    reset_d5_state();
    snprintf(vfs_adapt, sizeof(vfs_adapt), "; runtime auto-healer profile\\nadapt_ver = 234\\nshave_mode = 2\\nreg_mode = 1\\n");
    load_early_adapt_cfg();
    ASSERT(g_cfg.shave_mode == 2 && g_cfg.reg_mode == 1);
    g_flips = 73024u;
    g_dbg_alloc_fail = 1u;
    sim_crash_self_adapt();
    ASSERT(g_adapt_shave2_failed == 0);
    load_early_adapt_cfg();
    ASSERT(g_cfg.shave_mode == 2);
    ASSERT(g_adapt_shave2_failed == 0);

    // 2. Starting from shave_mode=1, reg_mode=1: consecutive crashes must NOT ping-pong 1 -> 2 -> 0 and disable shave:
    reset_d5_state();
    snprintf(vfs_adapt, sizeof(vfs_adapt), "; runtime auto-healer profile\\nadapt_ver = 234\\nshave_mode = 1\\nreg_mode = 1\\n");
    load_early_adapt_cfg();
    ASSERT(g_cfg.shave_mode == 1 && g_cfg.reg_mode == 1);
    g_flips = 12902u;
    g_dbg_alloc_fail = 1u;
    sim_crash_self_adapt();
    load_early_adapt_cfg();
    ASSERT(g_cfg.shave_mode > 0 && g_adapt_shave2_failed == 0);
    // Second crash in a row also keeps shave_mode > 0:
    g_flips = 183535u;
    g_dbg_alloc_fail = 1u;
    sim_crash_self_adapt();
    load_early_adapt_cfg();
    ASSERT(g_cfg.shave_mode > 0 && g_adapt_shave2_failed == 0);

    // 3. Existing v2.35 adapt-CUSA06782.ini that was rolled back (shave2_failed = 1, shave_mode = 0)
    //    is rescued on startup to shave_mode = 2, reg_mode >= 1 with g_adapt_shave2_failed == 0:
    reset_d5_state();
    snprintf(vfs_adapt, sizeof(vfs_adapt),
             "; auto-generated on boot/alloc crash\\nadapt_ver = 234\\nshave2_failed = 1\\nshave_mode = 0\\nreg_mode = 0\\n");
    load_early_adapt_cfg();
    ASSERT(g_cfg.shave_mode == 2 && g_cfg.reg_mode >= 1 && g_adapt_shave2_failed == 0);

    printf("CUSA06782 SIGABRT unit tests: all scenarios passed\\n");
    return 0;
}}
"""
    c_path.write_text(c_src, encoding="utf-8")
    llvm = pathlib.Path(cfg["llvm_bin"])
    subprocess.run(
        [
            str(llvm / "clang.exe"),
            "--target=x86_64-pc-windows-msvc",
            "-O0",
            "-fno-builtin",
            "-emit-llvm",
            "-I",
            str(ROOT / "overlay"),
            "-c",
            str(c_path),
            "-o",
            str(bc_path),
        ],
        check=True,
    )
    subprocess.run([str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bc_path)], check=True)


def run_c_tests() -> None:
    out = ROOT / "build" / "tests"
    out.mkdir(parents=True, exist_ok=True)
    source = out / "test_cusa06782_sigabrt.c"
    bitcode = out / "test_cusa06782_sigabrt.bc"
    full_c = (
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
        + MAIN_VOV_READY
        + "\n"
        + (
            TESTS_MAIN_C.replace("__GPU_INIT_ALLOC__", GPU_INIT_ALLOC)
            .replace("__REG_CLAMP_BLOCK__", REG_CLAMP_BLOCK)
        )
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
    run_d5_adapt_tests()


class TestCusa06782Sigabrt(unittest.TestCase):
    def test_cusa06782_sigabrt_suite(self) -> None:
        run_c_tests()


if __name__ == "__main__":
    run_c_tests()
