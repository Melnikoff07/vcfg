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
TWO_BUF_H = (ROOT / "classic" / "source" / "two_buffer_test.h").read_text(encoding="utf-8")
FG8_H = (ROOT / "classic" / "source" / "fg8_table.h").read_text(encoding="utf-8")
FAMILIES_H = (ROOT / "overlay" / "title_families.h").read_text(encoding="utf-8")
TRACES_PATH = ROOT / "tools" / "tests" / "real_traces.json"


def extract_between(src: str, start_marker: str, end_marker: str) -> str:
    s = src.find(start_marker)
    assert s >= 0, f"start marker not found: {start_marker}"
    e = src.find(end_marker, s)
    assert e > s, f"end marker not found: {end_marker}"
    return src[s:e]


MAIN_MBASE_DECLS = extract_between(
    MAIN_C,
    "static int g_nmid = 3;",
    "static void *g_pre_mem = NULL;",
)
ON_OUR_BUF_LINE = extract_between(
    TWO_BUF_H,
    "int on_our_buf =",
    "\n",
)
FG8_RESOLVE_FN = extract_between(
    FG8_H,
    "static inline void fg8_resolve_2buf_history(",
    "static inline void make_t_ex(",
)
FLIP_PCOPY_BLOCK = extract_between(
    MAIN_C,
    "int two_buf_now =",
    "if (tb_fast) {",
)
REG_HOOK_SRC = extract_between(
    MAIN_C,
    "int32_t sceVideoOutRegisterBuffers_hook(",
    "// ---- in-game L3+R3 (3s) menu",
)
REG_START0_RESET = extract_between(
    REG_HOOK_SRC,
    "if (startIndex == 0) {",
    "if (!dfmt",
)
REG_FMT_BLOCK = extract_between(
    REG_HOOK_SRC,
    "uint32_t fam = attr->format >> 24",
    "vov_set_hdr(",
)
LATENCY_FG_EXPR_M = re.search(
    r"\(unsigned long long\)g_lat_n,\s*(.+?),\s*g_cfg\.preset,\s*pend_max\);",
    MAIN_C,
)
assert LATENCY_FG_EXPR_M is not None, "Could not find LATENCY FG expression in classic/source/main.c"
LATENCY_FG_EXPR = LATENCY_FG_EXPR_M.group(1)
REG_BUF_LOOP = extract_between(
    REG_HOOK_SRC,
    "for (int i = 0; addresses && i < bufferNum && startIndex + i < 16; i++) {",
    "if (g_safe_active && startIndex == 0",
)
PCOPY_AVAIL_BLOCK = extract_between(
    REG_HOOK_SRC,
    "g_pcopy_avail = (want_pcopy",
    "if (g_cfg.reg_mode == 1",
)
REG_FALLBACK_BLOCK = (
    extract_between(
        REG_HOOK_SRC,
        "if (g_mbuf_state != 1)",
        'lg("mbufs: no extra display buffers',
    )
    + 'lg("mbufs: no extra display buffers (stride 0x%zx)\\n", one);\n'
    + ("}" if "if (g_mbuf_state != 1) {" in REG_HOOK_SRC else "")
)
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
PCOPY_GATE_HAS_MAX_EXTRA = 1 if ("nm < 2 && max_extra >= 2" in MAIN_C or "nm < 2 && nm < max_extra" in MAIN_C) else 0
HAS_CUSA10249_AUDIO_FIX = 1 if "cusa10249_audio_fix" in HOOKS_H else 0
HAS_FAMILY_AUDIO_FIX = 1 if "cusa04529_audio_fix" in HOOKS_H else 0
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
    int framegen;
    int shave_mode;
    int reg_mode;
    int pace_order;
    int present_copy;
    int two_buffer;
    int audio_fix;
} fg_config_t;

typedef struct {
    uint32_t format;
    uint32_t tilingMode;
    uint32_t aspectRatio;
    uint32_t width;
    uint32_t height;
    uint32_t pitchInPixel;
    uint64_t option;
} VoAttr;

static char g_title_id[16] = "UNKNOWN";
static int g_known_game = 0;
static int g_sim_neo = 0;
static fg_config_t g_cfg = { 1, 0, 0, 0, 0, 0, 0 };
static int g_dm_hooks = 1;
static volatile int g_own_alloc = 0;
static uint32_t g_dbg_allocs = 0;
static uint32_t g_dbg_maps = 0;
static uint32_t g_dbg_alloc_fail = 0;
static uint32_t g_flips = 0;
static volatile uint32_t g_vo_submit_flips = 0;
static int g_gpu_state = 0;
static int g_mbuf_state = 0;
static int g_nbuf = 0;
static uint64_t g_buf[16];
static uint32_t g_bw = 0, g_bh = 0, g_pitch = 0;
static int g_fmt_supported = 1, g_safe_active = 0;
static uint32_t g_t_dfmt = 10u;
static VoAttr g_attr = { 0x80000000u, 0, 0, 1920, 1080, 1920, 0 };
static VoAttr g_ext_attr = { 0x80000000u, 0, 0, 1920, 1080, 1920, 0 };
static int g_ext_nm = 0;
static uint32_t g_sim_rereg_ext_fmt = 0;
static int g_sim_rereg_ext_cnt = 0;
static char g_inert_reason[64] = "";
static uint32_t g_prev_idx = 0xFFFFFFFFu, g_prev2_idx = 0xFFFFFFFFu;
static int g_vq_on = 0, g_runtime_2buf = 0;
static int g_t_prev_half16 = 0, g_pcopy_dst_half16 = 0;
static uint64_t g_sub_t[16];
static void *g_gpu = NULL, *g_mon = NULL;
static int g_safe_never = 0, g_fg_off = 0;
static long g_low_sub2mb_end = 0;
static size_t g_gpu_sz = GPU_SZ_BASE;
static int g_work_garlic = 0;
__MAIN_MBASE_DECLS__

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
#define lg(...)    stage_nop(__VA_ARGS__)

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
    int is_flex;
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
    g_nbuf = 0;
    memset(g_buf, 0, sizeof(g_buf));
    g_bw = 0;
    g_bh = 0;
    g_pitch = 0;
    g_fmt_supported = 1;
    g_safe_active = 0;
    g_prev_idx = 0xFFFFFFFFu;
    g_prev2_idx = 0xFFFFFFFFu;
    g_vq_on = 0;
    g_runtime_2buf = 0;
    g_nmid = 3;
    g_pcopy_avail = 0;
    g_pcopy_k = 2;
    g_pcopy_on = 0;
    g_mbase = __INIT_G_MBASE__;
    g_ngame = 0;
    g_game_idx_hi = 0;
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
        uintptr_t mask = g_sim_force_misaligned_flex ? 0x3FFFu : 0xFFFFu;
        g_sim_fake_va_cursor = (g_sim_fake_va_cursor + mask) & ~mask;
        base = g_sim_fake_va_cursor;
        g_sim_fake_va_cursor += len;
    }
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {
        if (!g_sim_flex[i].in_use) {
            g_sim_flex[i].in_use = 1;
            g_sim_flex[i].is_flex = 1;
            g_sim_flex[i].va = base;
            g_sim_flex[i].len = len;
            g_sim_flex[i].prot = prot;
            g_sim_flex[i].flags = flags;
            break;
        }
    }
    g_sim_flex_avail -= len;
    *addr = (void *)base;
    return 0;
}

static int32_t sceKernelMunmap(void *addr, size_t len)
{
    (void)len;
    if (!addr) return -1;
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {
        if (g_sim_flex[i].in_use && g_sim_flex[i].va == (uintptr_t)addr) {
            if (g_sim_flex[i].is_flex) g_sim_flex_avail += g_sim_flex[i].len;
            g_sim_flex[i].in_use = 0;
            g_sim_flex[i].is_flex = 0;
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
#if !__HAS_CUSA10249_AUDIO_FIX__
static int g_c10249_tail_pending = 0;
static int g_c10249_first_reg_done = 0;
static inline int cusa10249_audio_fix(void) { return 0; }
static inline void cusa10249_apply_deferred_tail_if_needed(void) {}
#endif
#if !__HAS_FAMILY_AUDIO_FIX__
#define AUDIO_FIX_TAIL 0x600000u
static size_t g_af_main_t0_len = 0;
static int g_af_t3_arenas = 0;
static size_t g_af_preshave_len = 0;
static inline int cusa04529_audio_fix(void) { return cusa10249_audio_fix(); }
#endif

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
    g_pbuf = NULL;
    g_pbuf_phys = 0;
    g_pbuf_sz = 0;
    g_pbuf_ycocg = 0;
    g_pbuf_valid = 0;
    g_c10249_tail_pending = 0;
    g_c10249_first_reg_done = 0;
    g_af_main_t0_len = 0;
    g_af_t3_arenas = 0;
    g_af_preshave_len = 0;
}

static void apply_title_defaults_sim(const char *tid)
{
    for (int i = 0; i < 15; i++) g_title_id[i] = tid[i];
    g_title_id[15] = 0;
    g_known_game = vcfg_is_verified_family(g_title_id);
    g_cfg.present_copy = 0;
    g_cfg.two_buffer = 0;
    g_cfg.shave_mode = 0;
    g_cfg.reg_mode = 0;
    g_cfg.pace_order = 0;
    /* cfg_apply_title_defaults: CUSA10249 on any console, the rest of the CUSA04529 family on base PS4 only. */
    g_cfg.audio_fix = (strcmp(g_title_id, "CUSA10249") == 0) ? 1 : 0;
    if (__HAS_FAMILY_AUDIO_FIX__ && !g_cfg.audio_fix && vcfg_in_family(g_title_id, "CUSA04529") && !g_sim_neo) g_cfg.audio_fix = 1;
    if (vcfg_in_family(g_title_id, "CUSA11456")) {
        g_cfg.present_copy = 2;
        g_cfg.two_buffer = 0;
        g_cfg.shave_mode = 2;
        g_cfg.reg_mode = 2;
        g_cfg.pace_order = 3;
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
    int skip_gpu_work = (__HAS_CUSA10249_AUDIO_FIX__ && strcmp(g_title_id, "CUSA10249") == 0 && (g_cfg.audio_fix == 1 || g_cfg.audio_fix == 2));
    long top_e = (long)(dm_sz & ~(size_t)0x1FFFFFu);
    size_t want_pre = skip_gpu_work ? 0x800000u : 0x2000000u;
    long top_s = top_e - (long)want_pre;
    g_own_alloc++;
    if (raw_sceKernelAllocateDirectMemory(top_s, top_e, want_pre, 0x200000u, 3, &g_pre_mem_phys) == 0) {
        raw_sceKernelMapDirectMemory(&g_pre_mem, want_pre, 0x33, 0, g_pre_mem_phys, 0x200000u);
        g_pre_size = want_pre;
    }
    g_own_alloc--;
    if (skip_gpu_work) return;
    if (g_low_sub2mb_end > 0) {
        g_pre_gpu = alloc_dm_range(0, g_low_sub2mb_end, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    }
    if (!g_pre_gpu && g_pre_mem && g_pre_mem_phys >= 0x200000LL) {
        g_pre_gpu = alloc_dm_range(g_pre_mem_phys - 0x200000LL, g_pre_mem_phys, g_gpu_sz, g_gpu_sz, 0, &g_pre_gpu_phys);
    }
    if (!g_pre_gpu) g_pre_gpu = alloc_flex_gpu(g_gpu_sz, g_gpu_sz);
    g_pre_work = alloc_work_early(WORK_SZ_8, 3);
}

typedef struct { int32_t currentBuffer; } SimFlipStatus;
static int sim_on_our_buf(int32_t cur_buf)
{
    SimFlipStatus st = { cur_buf };
    SimFlipStatus *s = &st;
    __ON_OUR_BUF_LINE__
    return on_our_buf;
}

__FG8_RESOLVE_FN__

static void sim_step_2buf_frame(uint32_t seq, uint32_t index, uint32_t prev_idx,
                                uint32_t *out_show_idx, uint32_t *out_copy_idx,
                                uint64_t *out_P, uint64_t *out_copy_dst)
{
    int pcopy = g_pcopy_avail && g_cfg.present_copy != 2;
    g_pcopy_on = pcopy;
    int tb_fast = 0, two_mids = 0, third_flip = 0;
    (void)two_mids; (void)third_flip;
    uint32_t show_idx = index;
    __FLIP_PCOPY_BLOCK__
    uint64_t P = g_buf[prev_idx];
    uint64_t C = g_buf[index];
    uint64_t copy_dst = pcopy ? g_buf[ci] : 0ull;
    uint32_t copy_idx = ci;
    fg8_resolve_2buf_history(&P, C, &copy_dst, &copy_idx, seq);
    if (out_show_idx) *out_show_idx = show_idx;
    if (out_copy_idx) *out_copy_idx = copy_idx;
    if (out_P) *out_P = P;
    if (out_copy_dst) *out_copy_dst = copy_dst;
}

static void on_register_buffers_2buf(int count, size_t surf_sz)
{
    g_pbuf_valid = 0;
    if (count == 2 || g_cfg.present_copy == 1 || g_cfg.two_buffer == 1) {
        if (count == 2) g_runtime_2buf = 1;
        if (g_cfg.present_copy != 2) {
            if (count == 2 || g_cfg.two_buffer == 1) g_vq_on = 1;
            if (!g_pcopy_avail || g_pcopy_k < 2) {
                if (alloc_pbuf_history(surf_sz)) {
                    g_pcopy_avail = 1;
                }
            }
        }
    } else {
        g_runtime_2buf = 0;
        g_vq_on = 0;
        if (g_pbuf) release_pbuf();
    }
}

static const char *sim_latency_fg_str(void)
{
    return __LATENCY_FG_EXPR__;
}

static void replay_register_buffers_fmt(int start_idx, int num_buf, uint32_t bw, uint32_t bh, uint32_t pitch, uint32_t fmt)
{
    int32_t startIndex = start_idx, bufferNum = num_buf;
    (void)bufferNum;
    VoAttr attr_val = { fmt, 0, 0, bw, bh, pitch, 0 };
    const VoAttr *attr = &attr_val;
    int need_rereg_ext_fmt = 0;
    (void)need_rereg_ext_fmt;
    if (attr && startIndex == 0) g_attr = *attr;
    int is_aux_plane = (startIndex > 0 && g_bw > 0 && (bw != g_bw || bh != g_bh));
    if (attr && !is_aux_plane) {
        g_bw = attr->width; g_bh = attr->height; g_pitch = attr->pitchInPixel;
        __REG_FMT_BLOCK__
    }
    if (need_rereg_ext_fmt) {
        g_sim_rereg_ext_fmt = attr->format;
        g_sim_rereg_ext_cnt++;
    }
}

/* What backs a game AllocateDirectMemory(len=AUDIO_FIX_TAIL) result, read right after the call:
 * 0 real DirectMemory, 1 tail-only synthetic chunk (FlexibleMemory on map), 2 partial shave (FlexibleMemory tail),
 * 3 not allocated in the simulated pool, 4 overlaps the display buffer. */
static int sim_audio_kind(long p)
{
    for (int si = 0; si < g_shave_n; si++) {
        if (!g_shaves[si].orig_len) continue;
        if (g_shaves[si].real_len == 0 && p >= g_shaves[si].phys && p < g_shaves[si].phys + (long)g_shaves[si].orig_len) return 1;
        if (g_shaves[si].phys == p && g_shaves[si].orig_len == AUDIO_FIX_TAIL && g_shaves[si].real_len < AUDIO_FIX_TAIL) return 2;
    }
    if (!sim_dm_is_allocated_range(p, AUDIO_FIX_TAIL)) return 3;
    if (g_pre_mem && p < g_pre_mem_phys + (long)g_pre_size && p + (long)AUDIO_FIX_TAIL > g_pre_mem_phys) return 4;
    return 0;
}

static void replay_register_buffers(int start_idx, int num_buf, uint32_t bw, uint32_t bh, uint32_t pitch)
{
    cusa10249_apply_deferred_tail_if_needed();
    int32_t startIndex = start_idx, bufferNum = num_buf;
    void *dummy_addrs[16];
    for (int i = 0; i < 16; i++)
        dummy_addrs[i] = (void *)(uintptr_t)(0x800000000ULL + (uint64_t)((startIndex + i) & 15) * 0x800000ULL);
    void *const *addresses = dummy_addrs;
    int is_aux_plane = (startIndex > 0 && g_bw > 0 && (bw != g_bw || bh != g_bh));
    if (!is_aux_plane) {
        g_bw = bw; g_bh = bh; g_pitch = pitch;
        uint32_t seen_dfmt = 0;
        (void)seen_dfmt;
        __REG_START0_RESET__
    }
    if (addresses && bufferNum > 0 && !is_aux_plane) {
        if (startIndex + bufferNum > g_game_idx_hi) g_game_idx_hi = startIndex + bufferNum;
    }
    __REG_BUF_LOOP__
    if (start_idx != 0 || num_buf < 2 || num_buf > 8 || g_cfg.reg_mode == 2) return;
    g_ngame = num_buf;
    if (g_ngame == 2) g_vq_on = 1;
    g_mbuf_state = -1;
    size_t calc = (size_t)pitch * (size_t)((bh + 63u) & ~63u) * 4u;
    size_t one = (calc + 0x7FFFFu) & ~(size_t)0x7FFFFu;
    if (one >= 0x100000u && one <= 0x4000000u && bw >= 1280u && bh >= 720u && bw <= 3840u && bh <= 2160u) {
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
            g_nmid = nm > 0 ? nm : 1;
            if (nm >= 1) {
                size_t used_pre = (one * (size_t)nm + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
                if (used_pre < 0x800000u) used_pre = 0x800000u;
                if (used_pre > 0 && used_pre < g_pre_size) {
                    shrink_early_mem_to_ex(used_pre, 0);
                }
                void *m2_va = NULL;
                int nm2 = 0;
                if (want_pcopy && nm < 2 && (__PCOPY_HAS_MAX_EXTRA__ ? (nm < max_extra) : 1) && one < 0x1000000u) {
                    size_t need2 = (one * (size_t)(2 - nm) + 0x1FFFFFu) & ~(size_t)0x1FFFFFu;
                    if (!g_mem_tight && dm_avail(0x200000u) >= need2 + 0x4000000u) {
                        long m2_phys = 0;
                        size_t range = sceKernelGetDirectMemorySize();
                        long top_e = (long)(range & ~(size_t)0x1FFFFFu);
                        long top_s = (top_e > (long)need2) ? (top_e - (long)need2) : 0L;
                        g_own_alloc++;
                        int r2 = (top_s > 0) ? sceKernelAllocateDirectMemory(top_s, top_e, need2, 0x200000u, 3, &m2_phys) : -1;
                        if (r2 != 0) r2 = sceKernelAllocateDirectMemory(0, (long)range, need2, 0x200000u, 3, &m2_phys);
                        if (r2 == 0) {
                            sceKernelMapDirectMemory(&m2_va, need2, 0x33, 0, m2_phys, 0x200000u);
                            if (m2_va) nm2 = 2 - nm;
                        }
                        g_own_alloc--;
                    }
                }
                nm += nm2;
                __PCOPY_AVAIL_BLOCK__
                int base = (g_cfg.reg_mode == 1 && g_game_idx_hi > g_ngame && 16 - nm >= g_ngame) ? (16 - nm) : g_ngame;
                g_mbase = base;
                for (int i = 0; i < nm && base + i < 16; i++)
                    g_buf[base + i] = 0x881200000ULL + (uint64_t)i * one;
                g_nbuf = base + nm;
                g_mbuf_state = 1;
                on_register_buffers_2buf(g_ngame, calc);
                return;
            }
        }
        __REG_FALLBACK_BLOCK__
    }
}
""".replace("__PCOPY_HAS_MAX_EXTRA__", str(PCOPY_GATE_HAS_MAX_EXTRA)).replace(
    "__HAS_CUSA10249_AUDIO_FIX__", str(HAS_CUSA10249_AUDIO_FIX)
).replace("__HAS_FAMILY_AUDIO_FIX__", str(HAS_FAMILY_AUDIO_FIX))


FAMILY_04529 = set(re.findall(r'\{\s*"(CUSA\d{5})",\s*"CUSA04529"\s*\}', FAMILIES_H))
AUDIO_LEN = 0x600000


def expected_preshave(tr: dict, af: int) -> bool:
    """Test oracle for audio_fix_preshave_arena: does a boot arena of this trace match the Variant C / D role?"""
    main0, t3 = 0, 0
    for ev in tr["events"]:
        if ev["op"] != "alloc" or ev.get("flips", 0) != 0 or ev["orig_ret"] != "0x0":
            continue
        ln, ty = int(ev["len"], 16), ev["type"]
        if af == 1 and ty == 0 and 0x4000000 <= ln <= 0x20000000 and main0 > ln:
            return True
        if af == 2 and ty == 3 and 0x10000000 <= ln <= 0x3FE00000 and t3 > 0:
            return True
        if ty == 0 and ln >= 0x40000000:
            main0 = max(main0, ln)
        if ty == 3 and ln >= 0x40000000:
            t3 += 1
    return False


def generate_family_audio_c(traces: list[dict]) -> list[str]:
    """CUSA04529-family audio_fix replays: every family trace, followed by the 0x600000 type 0 audio allocation (appended
    at the end when the trace stops before it), under the per-title default on base PS4, audio_fix=2, the audio_fix=0
    override and the PS4 Pro default; the console boots also with the audio allocation before the first RegisterBuffers
    (boot-CUSA00341.prev.txt order). With the fix the audio allocation must be real DirectMemory (never the tail-only
    synthetic chunk or a FlexibleMemory tail), mapped without FlexibleMemory, and the 8 MB display buffer must survive."""
    lines: list[str] = ["    int family_audio_passed = 0;"]
    for tr in traces:
        tid = tr["title_id"]
        if tid not in FAMILY_04529:
            continue
        tkey, dm_sz, pre_occ = tr["trace_key"], tr["dm_size"], tr["pre_occupied_low"]
        events = list(tr["events"])
        has_audio = any(ev["op"] == "alloc" and int(ev["len"], 16) == AUDIO_LEN and ev["type"] == 0 for ev in events)
        if not has_audio:
            last_flips = events[-1].get("flips", 0) if events else 0
            events.append({"op": "alloc", "id": 255, "flips": last_flips, "s": "0x0", "e": dm_sz, "len": hex(AUDIO_LEN),
                           "align": "0x200000", "type": 0, "orig_ret": "0x0", "orig_phys": "0x0"})
        has_reg = any(ev["op"] == "reg_buffers" for ev in events)
        console_boot = tkey.endswith("_v2.34")
        modes = [("default", 0, None, events), ("af2", 0, 2, events), ("af0", 0, 0, events), ("neo", 1, None, events)]
        if console_boot:
            audio_i = next(i for i, ev in enumerate(events) if ev["op"] == "alloc" and int(ev["len"], 16) == AUDIO_LEN)
            reg_i = next(i for i, ev in enumerate(events) if ev["op"] == "reg_buffers")
            first = events[:reg_i] + [events[audio_i]] + [ev for i, ev in enumerate(events[reg_i:], reg_i) if i != audio_i]
            modes.append(("audio_first", 0, None, first))
        for mode, neo, af, evs in modes:
            tag = f"{tkey}_audio_{mode}"
            lines.append(f"    /* Family audio_fix: {tkey} [{mode}] */")
            lines.append("    {")
            lines.append(f"        g_sim_neo = {neo};")
            lines.append(f'        apply_title_defaults_sim("{tid}");')
            if af is not None:
                lines.append(f"        g_cfg.audio_fix = {af};")
            lines.append(f"        setup_plugin_load_early({dm_sz}ULL, {pre_occ}LL);")
            if tkey == "CUSA10249_0x137200000":
                lines.append("        g_sim_flex_avail = 0xE00000u;")
            lines.append("        int flex_calls_boot = g_sim_map_flex_calls, flex_calls_pre_reg = -1;")
            lines.append("        int n_audio = 0, n_audio_real = 0, n_audio_syn = 0, n_audio_flex_map = 0, n_audio_failed = 0;")
            lines.append("        long audio_p_last = -1;")
            lines.append("        long id_phys[256]; size_t id_len[256];")
            lines.append("        memset(id_phys, 0, sizeof(id_phys)); memset(id_len, 0, sizeof(id_len));")
            for step_i, ev in enumerate(evs):
                lines.append(f"        g_flips = {ev.get('flips', 0)}u;")
                op = ev["op"]
                if op == "release_id":
                    rid = ev["id"]
                    if 0 < rid < 256:
                        lines.append(
                            f"        if (id_len[{rid}] > 0) {{ sceKernelReleaseDirectMemory(id_phys[{rid}], id_len[{rid}]); id_len[{rid}] = 0; }}"
                        )
                elif op == "reg_buffers":
                    lines.append("        if (flex_calls_pre_reg < 0) flex_calls_pre_reg = g_sim_map_flex_calls;")
                    lines.append(
                        f"        replay_register_buffers({ev['start']}, {ev['num']}, {ev['w']}u, {ev['h']}u, {ev['pitch']}u);"
                    )
                elif op == "alloc":
                    elen, aid = ev["len"], ev.get("id", 0)
                    is_audio = int(elen, 16) == AUDIO_LEN and ev["type"] == 0
                    lines.append("        {")
                    if ev["orig_ret"] == "0x0" and is_audio:
                        # Audio allocation: classified, never asserted to succeed here (see the per-mode checks below).
                        lines.append("            long p = 0; void *va = NULL;")
                        lines.append(
                            f"            int32_t r = sceKernelAllocateDirectMemory({ev['s']}LL, {ev['e']}LL, {elen}ULL, {ev['align']}ULL, {ev['type']}, &p);"
                        )
                        lines.append("            n_audio++;")
                        lines.append("            if (r != 0) {")
                        lines.append("                n_audio_failed++;")
                        lines.append("            } else {")
                        lines.append("                int kind = sim_audio_kind(p), fc = g_sim_map_flex_calls;")
                        lines.append(
                            f"                int32_t mr = sceKernelMapDirectMemory(&va, {elen}ULL, 0x33, 0, p, {ev['align']}ULL);"
                        )
                        lines.append(f'                ASSERT_MSG(mr == 0 && va != NULL, "{tag}", {step_i}, {elen}ULL);')
                        lines.append("                audio_p_last = p;")
                        lines.append("                if (kind == 0) n_audio_real++;")
                        lines.append("                if (kind == 1) n_audio_syn++;")
                        lines.append("                if (g_sim_map_flex_calls != fc) n_audio_flex_map++;")
                        if 0 < aid < 256:
                            lines.append(f"                id_phys[{aid}] = p; id_len[{aid}] = {elen}ULL;")
                        lines.append("            }")
                    elif ev["orig_ret"] == "0x0":
                        lines.append("            long p = 0; void *va = NULL;")
                        lines.append(
                            f"            int32_t r = sceKernelAllocateDirectMemory({ev['s']}LL, {ev['e']}LL, {elen}ULL, {ev['align']}ULL, {ev['type']}, &p);"
                        )
                        lines.append(f'            ASSERT_MSG(r == 0, "{tag}", {step_i}, {elen}ULL);')
                        lines.append(
                            f"            int32_t mr = sceKernelMapDirectMemory(&va, {elen}ULL, 0x33, 0, p, {ev['align']}ULL);"
                        )
                        lines.append(f'            ASSERT_MSG(mr == 0 && va != NULL, "{tag}", {step_i}, {elen}ULL);')
                        if 0 < aid < 256:
                            lines.append(f"            id_phys[{aid}] = p; id_len[{aid}] = {elen}ULL;")
                    else:
                        lines.append("            long p = 0;")
                        lines.append(
                            f"            int32_t r = sceKernelAllocateDirectMemory({ev['s']}LL, {ev['e']}LL, {elen}ULL, {ev['align']}ULL, {ev['type']}, &p);"
                        )
                        lines.append(f"            if (r == 0) sceKernelReleaseDirectMemory(p, {elen}ULL);")
                    lines.append("        }")
            fix_on = mode in ("default", "af2", "audio_first") or (mode == "neo" and tid == "CUSA10249")
            if fix_on:
                variant = 2 if mode == "af2" else 1
                engaged = expected_preshave(tr, variant)
                lines.append(f'        ASSERT_MSG(cusa04529_audio_fix() == {variant}, "{tag}", 900, g_cfg.audio_fix);')
                # With the fix active the audio allocation is never the synthetic chunk nor FlexibleMemory on map.
                lines.append(
                    f'        ASSERT_MSG(n_audio >= 1 && n_audio_syn == 0 && n_audio_flex_map == 0 && n_audio_real + n_audio_failed == n_audio, "{tag}", 901, n_audio_syn);'
                )
                if engaged or variant == 1:
                    # ... and, with the role-matched arena pre-shaved, it is real DirectMemory.
                    lines.append(f'        ASSERT_MSG(n_audio_failed == 0 && n_audio_real == n_audio, "{tag}", 908, n_audio_failed);')
                if engaged or has_reg:
                    # The display buffer (at least the 8 MB mid buffer) survives in real DirectMemory. (Without a pre-shave
                    # and before any RegisterBuffers, the memory valve may still yield it to the audio allocation.)
                    lines.append(
                        f'        ASSERT_MSG(g_pre_mem != NULL && g_pre_size >= 0x800000ULL && sim_dm_is_allocated_range(g_pre_mem_phys, g_pre_size), "{tag}", 902, g_pre_size);'
                    )
                if has_reg:
                    lines.append(f'        ASSERT_MSG(g_mbuf_state == 1 && IS_MID(g_mbase), "{tag}", 903, g_mbuf_state);')
                    # No FlexibleMemory is mapped before the first RegisterBuffers (the Variant C tail is deferred to it).
                    lines.append(f'        ASSERT_MSG(flex_calls_pre_reg == flex_calls_boot, "{tag}", 904, flex_calls_pre_reg);')
                    lines.append(f'        ASSERT_MSG(g_c10249_first_reg_done == 1 && g_c10249_tail_pending == 0, "{tag}", 905, g_c10249_tail_pending);')
                if engaged:
                    lines.append(
                        f'        ASSERT_MSG(g_af_preshave_len != 0 && g_shave_n >= 1 && g_shaves[0].orig_len == g_af_preshave_len && g_shaves[0].orig_len - g_shaves[0].real_len == AUDIO_FIX_TAIL, "{tag}", 906, g_af_preshave_len);'
                    )
                    if variant == 2:
                        lines.append(f'        ASSERT_MSG(g_sim_map_flex_calls == flex_calls_boot, "{tag}", 907, g_sim_map_flex_calls);')
                if console_boot and mode in ("default", "audio_first"):
                    # Console boot with the fix: the audio allocation sits right below the 8 MB display buffer at the top.
                    top = int(dm_sz, 16) - 0x800000 - AUDIO_LEN
                    lines.append(f'        ASSERT_MSG(audio_p_last == {hex(top)}LL && g_pre_mem_phys == {hex(top + AUDIO_LEN)}LL, "{tag}", 909, audio_p_last);')
            else:
                lines.append(f'        ASSERT_MSG(cusa04529_audio_fix() == 0 && g_af_preshave_len == 0, "{tag}", 910, g_cfg.audio_fix);')
                if mode == "af0" and console_boot:
                    # audio_fix=0 reproduces the console boot: the audio allocation lands on the synthetic chunk.
                    audio_ev = next(ev for ev in events if ev["op"] == "alloc" and int(ev["len"], 16) == AUDIO_LEN and ev["type"] == 0)
                    lines.append(f'        ASSERT_MSG(n_audio == 1 && n_audio_syn == 1 && audio_p_last == {audio_ev["orig_phys"]}LL, "{tag}", 911, audio_p_last);')
            lines.append(f'        ASSERT_MSG(on_sub() >= 0, "{tag}", 1000, 0);')
            lines.append("        (void)flex_calls_boot; (void)flex_calls_pre_reg; (void)n_audio; (void)n_audio_real; (void)n_audio_syn; (void)n_audio_flex_map; (void)n_audio_failed; (void)audio_p_last;")
            lines.append("        family_audio_passed++;")
            lines.append("    }")
    lines.append(
        '    printf("CUSA04529 family audio_fix replay: %d/%d cases passed (audio allocation in real DirectMemory, display buffer kept)\\n", family_audio_passed, family_audio_passed);'
    )
    return lines


def generate_trace_c(traces: list[dict]) -> str:
    lines: list[str] = [
        "int main(void) {",
        "    int passed = 0;",
        "    int fixA_failures = 0;",
        """
    /* Real trace: CUSA10249 (615a072258, 1440p re-registration after 1080p 2-buffer boot, audio_fix=0 pbuf release check) */
    {
        apply_title_defaults_sim("CUSA10249");
        g_cfg.audio_fix = 0;
        setup_plugin_load_early(0x15f200000ULL, 0x15c200000LL);
        g_sim_flex_avail = 0xF00000u;
        shrink_early_mem_to_ex(0x800000u, 1);
        long p_fill = 0;
        g_own_alloc++;
        raw_sceKernelAllocateDirectMemory(0, 0x15ea00000LL, 0x2800000u, 0x200000u, 3, &p_fill);
        g_own_alloc--;
        replay_register_buffers(0, 2, 1920u, 1080u, 1920u);
        ASSERT_MSG(g_mbuf_state == 1 && g_mbase == 2 && g_nbuf == 3 && IS_MID(2) && sim_on_our_buf(2), "CUSA10249_615a072258", 1, g_mbase);
        ASSERT_MSG(g_runtime_2buf == 1 && g_vq_on == 1 && g_pbuf != NULL && g_pbuf_ycocg == 1, "CUSA10249_615a072258", 2, g_pbuf_sz);
        {
            long p = 0; void *va = NULL;
            int32_t r = sceKernelAllocateDirectMemory(0LL, 0x15f200000LL, 0x600000ULL, 0x200000ULL, 0, &p);
            ASSERT_MSG(r == 0, "CUSA10249_615a072258", 3, 0x600000ULL);
            int32_t mr = sceKernelMapDirectMemory(&va, 0x600000ULL, 0x33, 0, p, 0x200000ULL);
            ASSERT_MSG(mr == 0 && va != NULL, "CUSA10249_615a072258", 4, 0x600000ULL);
        }
        replay_register_buffers(0, 3, 2560u, 1440u, 2560u);
        if (!(g_mbuf_state == -1 && g_mbase == 16 && g_nbuf == 3 && !IS_MID(2) && !sim_on_our_buf(2) && g_vq_on == 0 && g_runtime_2buf == 0 && g_pbuf == NULL)) {
            printf("TRACE ASSERT FAILED [CUSA10249_615a072258 step 5]: mbuf_state=%d mbase=%d nbuf=%d IS_MID(2)=%d on_our_buf(2)=%d vq_on=%d runtime_2buf=%d pbuf=%p\\n",
                   g_mbuf_state, g_mbase, g_nbuf, (int)IS_MID(2), sim_on_our_buf(2), g_vq_on, g_runtime_2buf, g_pbuf);
            fixA_failures++;
        }
    }
    /* Real trace: CUSA07410 (3b55484699, second group start=14 num=2 after start=0 num=3) */
    {
        apply_title_defaults_sim("CUSA07410");
        setup_plugin_load_early(0x135210000ULL, 0xc2400000LL);
        shrink_early_mem_to_ex(0x800000u, 1);
        replay_register_buffers(0, 3, 1920u, 1080u, 1920u);
        ASSERT_MSG(g_mbuf_state == 1 && g_mbase == 3 && g_nbuf == 4 && IS_MID(3) && sim_on_our_buf(3), "CUSA07410_3b55484699", 1, g_nbuf);
        replay_register_buffers(14, 2, 1920u, 1080u, 1920u);
        if (!(g_mbuf_state == 1 && g_mbase == 3 && g_nbuf == 4 && g_game_idx_hi == 16 && !IS_MID(14) && !IS_MID(15) && !sim_on_our_buf(14) && !sim_on_our_buf(15))) {
            printf("TRACE ASSERT FAILED [CUSA07410_3b55484699 step 2]: mbuf_state=%d mbase=%d nbuf=%d idx_hi=%d IS_MID(14)=%d IS_MID(15)=%d on_our_buf(14)=%d on_our_buf(15)=%d\\n",
                   g_mbuf_state, g_mbase, g_nbuf, g_game_idx_hi, (int)IS_MID(14), (int)IS_MID(15), sim_on_our_buf(14), sim_on_our_buf(15));
            fixA_failures++;
        }
    }
    if (fixA_failures > 0) exit(1);
    int fixB_failures = 0;
    /* Real trace: CUSA03468 (701e4d8e5f, 2-buffer 1080p pitch=1920 one=0x810000 pre_size=0x1a00000 -> nm=3) */
    {
        apply_title_defaults_sim("CUSA03468");
        setup_plugin_load_early(0x128000000ULL, 0x810000LL);
        shrink_early_mem_to_ex(0x1a00000u, 0);
        replay_register_buffers(0, 2, 1920u, 1080u, 1920u);
        uint32_t show_121 = 0, copy_121 = 0, show_122 = 0, copy_122 = 0;
        uint64_t P_121 = 0, dst_121 = 0, P_122 = 0, dst_122 = 0;
        sim_step_2buf_frame(121u, 0u, 1u, &show_121, &copy_121, &P_121, &dst_121);
        sim_step_2buf_frame(122u, 1u, 0u, &show_122, &copy_122, &P_122, &dst_122);
        if (!(dst_122 == 0ull || copy_122 == 0xFFFFu || copy_122 != show_121)) {
            printf("TRACE ASSERT FAILED [CUSA03468_701e4d8e5f f:122 deadlock]: pcopy_k=%d nmid=%d show_121=%u copy_122=%u dst_122=0x%zx pbuf=%p\\n",
                   g_pcopy_k, g_nmid, show_121, copy_122, (size_t)dst_122, g_pbuf);
            fixB_failures++;
        } else {
            ASSERT_MSG(g_pbuf != NULL && P_122 == (uint64_t)(uintptr_t)g_pbuf && copy_122 == 0xFFFFu, "CUSA03468_701e4d8e5f", 2, copy_122);
        }
    }
    /* Real trace: CUSA08809 (21c982659f, 2-buffer 1080p pitch=1920 one=0x800000 pre_size=0x1800000 -> nm=3, flex tight -> ycocg=1) */
    {
        apply_title_defaults_sim("CUSA08809");
        setup_plugin_load_early(0x128000000ULL, 0x0LL);
        g_sim_flex_avail = 0x1000000u;
        shrink_early_mem_to_ex(0x1800000u, 1);
        replay_register_buffers(0, 2, 1920u, 1080u, 1920u);
        uint32_t show_121 = 0, copy_121 = 0, show_122 = 0, copy_122 = 0;
        uint64_t P_121 = 0, dst_121 = 0, P_122 = 0, dst_122 = 0;
        sim_step_2buf_frame(121u, 0u, 1u, &show_121, &copy_121, &P_121, &dst_121);
        sim_step_2buf_frame(122u, 1u, 0u, &show_122, &copy_122, &P_122, &dst_122);
        if (!(dst_122 == 0ull || copy_122 == 0xFFFFu || copy_122 != show_121)) {
            printf("TRACE ASSERT FAILED [CUSA08809_21c982659f f:122 deadlock]: pcopy_k=%d nmid=%d show_121=%u copy_122=%u dst_122=0x%zx pbuf=%p\\n",
                   g_pcopy_k, g_nmid, show_121, copy_122, (size_t)dst_122, g_pbuf);
            fixB_failures++;
        } else {
            ASSERT_MSG(g_pbuf != NULL && g_pbuf_ycocg == 1 && P_122 == (uint64_t)(uintptr_t)g_pbuf && copy_122 == 0xFFFFu, "CUSA08809_21c982659f", 2, copy_122);
        }
    }
    if (fixB_failures > 0) exit(1);
    /* NEGATIVE HARDWARE EXAMPLE (owner-test-234b/boot-CUSA10249.prev.txt, saved in tools/tests/negative_cusa10249_arena2_preshave_sigbus_boot.txt):
     * Pre-shaving CUSA10249 Arena 2 (len=0x15800000, type=0, phys=0x8e400000) by 0x600000 with an immediate FlexibleMemory tail (7afdb16)
     * consumes 6 MB of FlexibleMemory at t=2.437s BEFORE the game's FlexibleMemory check at t=2.479s (eboot.bin+0x6a224b, right after Arena 4 map),
     * causing the game's 86 MB FlexibleMemory mapping (va=0x1400000000) to fail with -15 (uc[19]=-15, [rsp+0x200]=0x600000) and abort via int $0x41 (SIGBUS sig=10 code=3).
     * At audio_fix=0 (c87af66 compatibility), Arena 2 (0x15800000) is NOT pre-shaved (g_shave_n == 0 after Arenas 1..4). */
    {
        apply_title_defaults_sim("CUSA10249");
        g_cfg.audio_fix = 0;
        setup_plugin_load_early(0x137200000ULL, 0x200000LL);
        g_sim_flex_avail = 0xE00000u;
        long p1 = 0, p2 = 0, p3 = 0, p4 = 0;
        void *va1 = (void *)(uintptr_t)0x1000000000ULL;
        void *va2 = (void *)(uintptr_t)0x1100000000ULL;
        void *va3 = (void *)(uintptr_t)0x1200000000ULL;
        void *va4 = (void *)(uintptr_t)0x1300000000ULL;
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x8e200000ULL, 0x200000ULL, 0, &p1) == 0, "CUSA10249_neg_arena2_sigbus", 1, p1);
        ASSERT_MSG(sceKernelMapDirectMemory(&va1, 0x8e200000ULL, 0x33, 0x90, p1, 0x200000ULL) == 0, "CUSA10249_neg_arena2_sigbus", 1, (uintptr_t)va1);
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x15800000ULL, 0x200000ULL, 0, &p2) == 0, "CUSA10249_neg_arena2_sigbus", 2, p2);
        ASSERT_MSG(sceKernelMapDirectMemory(&va2, 0x15800000ULL, 0x33, 0x90, p2, 0x200000ULL) == 0, "CUSA10249_neg_arena2_sigbus", 2, (uintptr_t)va2);
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x68e00000ULL, 0x200000ULL, 3, &p3) == 0, "CUSA10249_neg_arena2_sigbus", 3, p3);
        ASSERT_MSG(sceKernelMapDirectMemory(&va3, 0x68e00000ULL, 0x33, 0x90, p3, 0x200000ULL) == 0, "CUSA10249_neg_arena2_sigbus", 3, (uintptr_t)va3);
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x2a000000ULL, 0x200000ULL, 3, &p4) == 0, "CUSA10249_neg_arena2_sigbus", 4, p4);
        ASSERT_MSG(sceKernelMapDirectMemory(&va4, 0x2a000000ULL, 0x33, 0x90, p4, 0x200000ULL) == 0, "CUSA10249_neg_arena2_sigbus", 4, (uintptr_t)va4);
        if (!(g_shave_n == 0 && p2 == 0x8e400000LL && p3 == 0xa3c00000LL && p4 == 0x10ca00000LL)) {
            printf("TRACE ASSERT FAILED [CUSA10249_neg_arena2_sigbus]: Arena 2 must NOT be pre-shaved at audio_fix=0 (shave_n=%d p2=0x%zx p3=0x%zx p4=0x%zx)\\n",
                   g_shave_n, (size_t)p2, (size_t)p3, (size_t)p4);
            exit(1);
        }
    }
    /* Real trace: owner-test-c87af66/boot-CUSA10249.txt under audio_fix=1 (Variant C, default for CUSA10249) and audio_fix=2 (Variant D):
     * 1) Default for CUSA10249 is audio_fix=1.
     * 2) 0 bytes of FlexibleMemory mapped BEFORE the first sceVideoOutRegisterBuffers (after Arenas 1..4 map, where eboot+0x6a224b checks Flex).
     * 3) At audio_fix=1 (Variant C), the deferred 6 MB Flex tail of Arena 2 is mapped at the beginning of the FIRST sceVideoOutRegisterBuffers;
     *    at audio_fix=2 (Variant D), Arena 4 is pre-shaved by 6 MB and its tail is unmapped (0 Flex calls).
     * 4) In both audio_fix=1 and audio_fix=2, Alloc 5 (len=0x600000, align=0x200000, type=0 at 9.256s) receives 100% real DirectMemory (phys=0x136400000). */
    for (int af_mode = 1; af_mode <= 2; af_mode++) {
        apply_title_defaults_sim("CUSA10249");
        if (af_mode == 1 && g_cfg.audio_fix != 1) {
            printf("TRACE ASSERT FAILED [CUSA10249 default audio_fix]: expected 1, got %d\\n", g_cfg.audio_fix);
            exit(1);
        }
        g_cfg.audio_fix = af_mode;
        setup_plugin_load_early(0x137200000ULL, 0x200000LL);
        g_sim_flex_avail = 0xE00000u;
        long p1 = 0, p2 = 0, p3 = 0, p4 = 0, p_audio = 0;
        void *va1 = (void *)(uintptr_t)0x1000000000ULL;
        void *va2 = (void *)(uintptr_t)0x1100000000ULL;
        void *va3 = (void *)(uintptr_t)0x1200000000ULL;
        void *va4 = (void *)(uintptr_t)0x1300000000ULL;
        void *va_audio = NULL;
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x8e200000ULL, 0x200000ULL, 0, &p1) == 0, "CUSA10249_audio_fix", 1, p1);
        ASSERT_MSG(sceKernelMapDirectMemory(&va1, 0x8e200000ULL, 0x33, 0x90, p1, 0x200000ULL) == 0, "CUSA10249_audio_fix", 1, (uintptr_t)va1);
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x15800000ULL, 0x200000ULL, 0, &p2) == 0, "CUSA10249_audio_fix", 2, p2);
        ASSERT_MSG(sceKernelMapDirectMemory(&va2, 0x15800000ULL, 0x33, 0x90, p2, 0x200000ULL) == 0, "CUSA10249_audio_fix", 2, (uintptr_t)va2);
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x68e00000ULL, 0x200000ULL, 3, &p3) == 0, "CUSA10249_audio_fix", 3, p3);
        ASSERT_MSG(sceKernelMapDirectMemory(&va3, 0x68e00000ULL, 0x33, 0x90, p3, 0x200000ULL) == 0, "CUSA10249_audio_fix", 3, (uintptr_t)va3);
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x2a000000ULL, 0x200000ULL, 3, &p4) == 0, "CUSA10249_audio_fix", 4, p4);
        ASSERT_MSG(sceKernelMapDirectMemory(&va4, 0x2a000000ULL, 0x33, 0x90, p4, 0x200000ULL) == 0, "CUSA10249_audio_fix", 4, (uintptr_t)va4);
        if (!(g_sim_map_flex_calls == 0 && g_sim_flex_avail == 0xE00000u)) {
            printf("TRACE ASSERT FAILED [CUSA10249 audio_fix=%d pre-RegisterBuffers flex check]: map_flex_calls=%d flex_avail=0x%zx\\n",
                   af_mode, g_sim_map_flex_calls, g_sim_flex_avail);
            exit(1);
        }
        if (af_mode == 1) {
            if (!(g_shave_n == 1 && g_shaves[0].orig_len == 0x15800000ULL && g_shaves[0].real_len == 0x15200000ULL &&
                  p2 == 0x8e400000LL && p3 == 0xa3600000LL && p4 == 0x10c400000LL)) {
                printf("TRACE ASSERT FAILED [CUSA10249 audio_fix=1 (C) arena2 shave]: shave_n=%d orig=0x%zx real=0x%zx p2=0x%zx p3=0x%zx p4=0x%zx\\n",
                       g_shave_n, g_shave_n > 0 ? g_shaves[0].orig_len : 0, g_shave_n > 0 ? g_shaves[0].real_len : 0,
                       (size_t)p2, (size_t)p3, (size_t)p4);
                exit(1);
            }
        } else {
            if (!(g_shave_n == 1 && g_shaves[0].orig_len == 0x2a000000ULL && g_shaves[0].real_len == 0x29a00000ULL &&
                  p2 == 0x8e400000LL && p3 == 0xa3c00000LL && p4 == 0x10ca00000LL)) {
                printf("TRACE ASSERT FAILED [CUSA10249 audio_fix=2 (D) arena4 shave]: shave_n=%d orig=0x%zx real=0x%zx p2=0x%zx p3=0x%zx p4=0x%zx\\n",
                       g_shave_n, g_shave_n > 0 ? g_shaves[0].orig_len : 0, g_shave_n > 0 ? g_shaves[0].real_len : 0,
                       (size_t)p2, (size_t)p3, (size_t)p4);
                exit(1);
            }
        }
        replay_register_buffers(0, 2, 1920u, 1080u, 1920u);
        if (af_mode == 1) {
            if (!(g_pbuf == NULL && g_sim_map_flex_calls == 1 && g_sim_flex_avail == 0x800000u)) {
                printf("TRACE ASSERT FAILED [CUSA10249 audio_fix=1 (C) RegBuffers#1 deferred tail]: pbuf=%p map_flex_calls=%d flex_avail=0x%zx\\n",
                       g_pbuf, g_sim_map_flex_calls, g_sim_flex_avail);
                exit(1);
            }
        } else {
            if (!(g_pbuf == NULL && g_sim_map_flex_calls == 0 && g_sim_flex_avail == 0xE00000u)) {
                printf("TRACE ASSERT FAILED [CUSA10249 audio_fix=2 (D) RegBuffers#1 no flex]: pbuf=%p map_flex_calls=%d flex_avail=0x%zx\\n",
                       g_pbuf, g_sim_map_flex_calls, g_sim_flex_avail);
                exit(1);
            }
        }
        ASSERT_MSG(sceKernelAllocateDirectMemory(0LL, 0x137200000LL, 0x600000ULL, 0x200000ULL, 0, &p_audio) == 0, "CUSA10249_audio_fix", 5, p_audio);
        ASSERT_MSG(sceKernelMapDirectMemory(&va_audio, 0x600000ULL, 0x33, 0, p_audio, 0x200000ULL) == 0 && va_audio != NULL, "CUSA10249_audio_fix", 5, (uintptr_t)va_audio);
        if (!(p_audio == 0x136400000LL && sim_dm_is_allocated_range((uint64_t)p_audio, 0x600000ULL) &&
              g_pre_mem != NULL && g_pre_size == 0x800000ULL && g_pre_mem_phys == 0x136a00000LL &&
              g_shave_n == 1 && g_mbuf_state == 1 && g_mbase == 2 && g_nbuf == 3)) {
            printf("TRACE ASSERT FAILED [CUSA10249 audio_fix=%d Alloc5 real DM]: p_audio=0x%zx in_dm=%d pre_mem_phys=0x%zx shave_n=%d mbuf_state=%d mbase=%d nbuf=%d\\n",
                   af_mode, (size_t)p_audio, sim_dm_is_allocated_range((uint64_t)p_audio, 0x600000ULL), (size_t)g_pre_mem_phys,
                   g_shave_n, g_mbuf_state, g_mbase, g_nbuf);
            exit(1);
        }
    }
    /* Real trace: owner-test-234/trace-CUSA13323.txt:192-195 (HDR10 format switch at start=2 num=2 fmt=0x88740000 + LATENCY FG off check) */
    {
        apply_title_defaults_sim("CUSA13323");
        setup_plugin_load_early(0x13ca00000ULL, 0x18200000LL);
        g_cfg.framegen = 1;
        g_fg_off = 0;
        g_inert_reason[0] = 0;
        g_sim_rereg_ext_fmt = 0;
        g_sim_rereg_ext_cnt = 0;
        replay_register_buffers_fmt(0, 2, 1920u, 1080u, 1920u, 0x80000000u);
        replay_register_buffers_fmt(2, 2, 1920u, 1080u, 1920u, 0x80000000u);
        replay_register_buffers_fmt(4, 2, 1920u, 1080u, 1920u, 0x80000000u);
        /* Late activation at f:120 registers our extra buffer at index 15 */
        g_safe_active = 1;
        g_mbuf_state = 1;
        g_mbase = 15;
        g_nbuf = 16;
        g_ext_nm = 1;
        /* At f:673, game switches scanout group start=2 num=2 to HDR10 (0x88740000, dfmt=9) */
        replay_register_buffers_fmt(2, 2, 1920u, 1080u, 1920u, 0x88740000u);
        if (!(g_fmt_supported == 1 && g_t_dfmt == 9u && g_attr.format == 0x88740000u && g_ext_attr.format == 0x88740000u && g_sim_rereg_ext_fmt == 0x88740000u && g_sim_rereg_ext_cnt == 1)) {
            printf("TRACE ASSERT FAILED [CUSA13323_hdr10_format_switch]: fmt_supported=%d t_dfmt=%u attr_fmt=0x%x ext_attr_fmt=0x%x rereg_fmt=0x%x rereg_cnt=%d\\n",
                   g_fmt_supported, g_t_dfmt, g_attr.format, g_ext_attr.format, g_sim_rereg_ext_fmt, g_sim_rereg_ext_cnt);
            exit(1);
        }
        /* LATENCY FG status must report 'off' whenever g_fmt_supported == 0 (even if g_inert_reason[0] == 0) */
        if (strcmp(sim_latency_fg_str(), "on") != 0) {
            printf("TRACE ASSERT FAILED [LATENCY FG status when supported]: got %s\\n", sim_latency_fg_str());
            exit(1);
        }
        g_fmt_supported = 0;
        if (strcmp(sim_latency_fg_str(), "off") != 0) {
            printf("TRACE ASSERT FAILED [LATENCY FG status when g_fmt_supported=0]: got %s\\n", sim_latency_fg_str());
            exit(1);
        }
        g_fmt_supported = 1;
        g_safe_active = 0;
    }""",
    ]
    for idx, tr in enumerate(traces):
        tkey = tr["trace_key"]
        tid = tr["title_id"]
        dm_sz = tr["dm_size"]
        pre_occ = tr["pre_occupied_low"]
        lines.append(f"    /* Trace {idx + 1}: {tkey} (v{tr['log_version']}) */")
        lines.append("    {")
        lines.append(f"        g_sim_neo = {int(tr.get('neo', 0))};")
        lines.append(f'        apply_title_defaults_sim("{tid}");')
        if "shave_mode" in tr:
            lines.append(f'        g_cfg.shave_mode = {int(tr["shave_mode"])};')
        lines.append(f"        setup_plugin_load_early({dm_sz}ULL, {pre_occ}LL);")
        if tkey in ("CUSA13323_0x13ca00000", "CUSA11456_0x13ca00000"):
            lines.append("        g_sim_flex_avail = 0x2C00000u;")
        elif tkey == "CUSA10249_0x137200000":
            lines.append("        g_sim_flex_avail = 0xE00000u;")
        elif tkey == "CUSA13323_0x164a00000":
            lines.append("        g_sim_flex_avail = 0x2000000u;")
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
                if tkey in ("CUSA13323_0x13ca00000", "CUSA11456_0x13ca00000") and ev["start"] == 0:
                    lines.append("        g_sim_flex_avail = (g_pbuf != NULL && g_pbuf_phys < 0) ? 0x500000u : 0x1520000u;")
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
        if tid in ("CUSA07820", "CUSA00341", "CUSA07737"):
            # Only tail-only (synthetic) shaves, except the audio_fix pre-shave (exactly AUDIO_FIX_TAIL, its tail deferred).
            lines.append(
                f'        for (int si = 0; si < g_shave_n; si++) ASSERT_MSG(g_shaves[si].real_len == 0 || (cusa04529_audio_fix() != 0 && g_shaves[si].orig_len == g_af_preshave_len && g_shaves[si].orig_len - g_shaves[si].real_len == AUDIO_FIX_TAIL), "{tkey}", 999, g_shaves[si].orig_len);'
            )
        if tid in ("CUSA13323", "CUSA11456"):
            lines.append(
                f'        for (int si = 0; si < g_shave_n; si++) ASSERT_MSG(g_shaves[si].orig_len - g_shaves[si].real_len <= g_pre_size, "{tkey}", 998, g_shaves[si].orig_len - g_shaves[si].real_len);'
            )
        if tkey == "CUSA13323_0x13ca00000":
            lines.append(f'        ASSERT_MSG(g_shave_n == 1 && g_pre_mem != NULL && g_pbuf == NULL, "{tkey}", 997, g_shave_n);')
        if tkey == "CUSA11456_0x13ca00000":
            lines.append(f'        ASSERT_MSG(g_pre_mem != NULL && g_pbuf == NULL && g_dbg_alloc_fail == 0, "{tkey}", 997, g_dbg_alloc_fail);')
        if tkey == "CUSA10249_0x137200000":
            lines.append(
                f'        ASSERT_MSG(g_pbuf == NULL && g_pre_mem != NULL && g_pre_size == 0x800000ULL && g_shave_n == 1 && g_shaves[0].orig_len == 0x15800000ULL && g_shaves[0].real_len == 0x15200000ULL && id_phys[5] == 0x136400000LL && sim_dm_is_allocated_range((uint64_t)id_phys[5], 0x600000ULL), "{tkey}", 997, id_phys[5]);'
            )
        if tkey == "CUSA13323_0x164a00000":
            lines.append(f'        ASSERT_MSG((g_pre_mem != NULL || dm_avail(0x200000u) >= 0x800000u) && g_pbuf == NULL && g_vq_on == 0 && g_dbg_alloc_fail == 0, "{tkey}", 997, g_dbg_alloc_fail);')
        lines.append("        for (int bi = 0; bi < 16; bi++) {")
        lines.append("            if (g_mbuf_state != 1) {")
        lines.append(f'                ASSERT_MSG(!IS_MID(bi) && !sim_on_our_buf(bi), "{tkey}", 996, bi);')
        lines.append("            } else if (bi < g_mbase || bi >= g_nbuf) {")
        lines.append(f'                ASSERT_MSG(!IS_MID(bi) && !sim_on_our_buf(bi), "{tkey}", 996, bi);')
        lines.append("            }")
        lines.append("        }")
        lines.append("        if (g_ngame == 2 && g_mbuf_state == 1) {")
        lines.append("            uint32_t sh121 = 0, cp121 = 0, sh122 = 0, cp122 = 0;")
        lines.append("            uint64_t p121 = 0, d121 = 0, p122 = 0, d122 = 0;")
        lines.append("            sim_step_2buf_frame(121u, 0u, 1u, &sh121, &cp121, &p121, &d121);")
        lines.append("            sim_step_2buf_frame(122u, 1u, 0u, &sh122, &cp122, &p122, &d122);")
        lines.append(f'            ASSERT_MSG(d122 == 0ull || cp122 == 0xFFFFu || cp122 != sh121, "{tkey}", 995, cp122);')
        lines.append("        }")
        lines.append(f'        ASSERT_MSG(on_sub() >= 0, "{tkey}", 1000, 0);')
        lines.append("        passed++;")
        lines.append("    }")
        if tkey == "CUSA10249_0x137200000":
            for extra_af in (2, 0):
                lines.append("    {")
                lines.append(f'        apply_title_defaults_sim("{tid}");')
                lines.append(f"        g_cfg.audio_fix = {extra_af};")
                lines.append(f"        setup_plugin_load_early({dm_sz}ULL, {pre_occ}LL);")
                lines.append("        g_sim_flex_avail = 0xE00000u;")
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
                            lines.append(f'            ASSERT_MSG(r == 0, "{tkey}_af{extra_af}", {step_i}, {elen}ULL);')
                            lines.append(
                                f"            int32_t mr = sceKernelMapDirectMemory(&va, {elen}ULL, 0x33, 0, p, {align}ULL);"
                            )
                            lines.append(f'            ASSERT_MSG(mr == 0 && va != NULL, "{tkey}_af{extra_af}", {step_i}, {elen}ULL);')
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
                if extra_af == 2:
                    lines.append(
                        f'        ASSERT_MSG(g_pbuf == NULL && g_pre_mem != NULL && g_pre_size == 0x800000ULL && g_shave_n == 1 && g_shaves[0].orig_len == 0x2a000000ULL && g_shaves[0].real_len == 0x29a00000ULL && id_phys[5] == 0x136400000LL && sim_dm_is_allocated_range((uint64_t)id_phys[5], 0x600000ULL) && g_sim_map_flex_calls == 0, "{tkey}_af2", 997, id_phys[5]);'
                    )
                else:
                    lines.append(
                        f'        for (int si = 0; si < g_shave_n; si++) ASSERT_MSG(g_shaves[si].real_len == 0, "{tkey}_af0", 999, g_shaves[si].orig_len);'
                    )
                    lines.append(f'        ASSERT_MSG(g_pbuf != NULL && g_pbuf_ycocg == 1 && g_shave_n == 1, "{tkey}_af0", 997, g_pbuf_sz);')
                lines.append(f'        ASSERT_MSG(on_sub() >= 0, "{tkey}_af{extra_af}", 1000, 0);')
                lines.append("    }")
    lines.extend(generate_family_audio_c(traces))
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
    init_mbase_m = re.search(r"static int g_mbase\s*=\s*(\d+);", MAIN_MBASE_DECLS)
    init_mbase = init_mbase_m.group(1) if init_mbase_m else "3"
    harness_c = HARNESS_C.replace("__MAIN_MBASE_DECLS__", MAIN_MBASE_DECLS).replace("__INIT_G_MBASE__", init_mbase)
    replay_c = (
        REPLAY_HELPER_C.replace("__ON_OUR_BUF_LINE__", ON_OUR_BUF_LINE)
        .replace("__FG8_RESOLVE_FN__", FG8_RESOLVE_FN)
        .replace("__FLIP_PCOPY_BLOCK__", FLIP_PCOPY_BLOCK)
        .replace("__REG_START0_RESET__", REG_START0_RESET)
        .replace("__REG_FMT_BLOCK__", REG_FMT_BLOCK)
        .replace("__LATENCY_FG_EXPR__", LATENCY_FG_EXPR)
        .replace("__REG_BUF_LOOP__", REG_BUF_LOOP)
        .replace("__PCOPY_AVAIL_BLOCK__", PCOPY_AVAIL_BLOCK)
        .replace("__REG_FALLBACK_BLOCK__", REG_FALLBACK_BLOCK)
    )
    full_c = (
        harness_c
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
        + replay_c
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
