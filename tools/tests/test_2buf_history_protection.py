#!/usr/bin/env python3
"""Unit and integration test suite for Universal 2-Buffer Protection & Compressed History Surface
(CUSA04607, CUSA05571, CUSA08519, CUSA17419, CUSA14209).

Compiles actual C functions extracted from classic/source/main.c, classic/source/fg8_table.h,
classic/source/two_buffer_test.h, and classic/source/debug_hooks.h to LLVM bitcode via clang.exe
and executes them via lli.exe.

Tests:
1. Automatic detection of g_ngame == 2 (RegisterBuffers count == 2 and runtime 2-buffer flip ring)
   and automatic activation of g_pbuf present-copy protection & virtual flip queue (vq_active()).
2. Low-DirectMemory fallback (only 8 MB DirectMemory available for g_mid): verifies g_pbuf falls
   back to FlexibleMemory (8 MB full-res when >= 24 MB free, 4.25 MB 50%-compressed YCoCg-R 16b
   when < 24 MB free, consuming 0 B of DirectMemory) and releases cleanly on hibernation
   (release_early_work at g_flips > 0) and shutdown (sceVideoOutClose_hook / release_early_mem).
3. T#prev overwrite protection & YCoCg-R 4:2:0 16-bit fidelity: verifies that overwriting
   buffers[prev_idx] with Frame N+1 does NOT corrupt T#prev read by warp8/pyr8, and verifies
   50%-footprint YCoCg-R 4:2:0 16-bit (4.15 MB) compression/decompression PSNR > 40 dB.
"""
import json
import pathlib
import re
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


def extract_function(src: str, name: str) -> str:
    match = re.search(r"^static [^\n]+\b" + name + r"\([^;]*\)\s*\{", src, re.M)
    assert match is not None, f"function not found: {name}"
    start = match.start()
    pos = src.index("{", start)
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[start:end]


def extract_define(src: str, name: str) -> str:
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"#define " + name + r"(?:\(|\s)", line):
            out = [line]
            while out[-1].endswith("\\"):
                i += 1
                out.append(lines[i])
            return "\n".join(out)
    raise ValueError(f"define not found: {name}")


MAIN_ALLOC_PART1 = extract_between(
    MAIN_C,
    "#define FLEX_GPU_MAX 8",
    '#include "debug_hooks.h"',
).replace("static uint8_t *g_work = NULL;", "").replace("static volatile int g_own_alloc;", "")
MAIN_ALLOC_PART2 = extract_between(
    MAIN_C,
    "static size_t dm_avail(size_t align)",
    "static void gpu_init(void)",
)
MAIN_2BUF_HELPERS = extract_between(
    MAIN_C,
    "static size_t safe_surface_stride(void);",
    "// ---- safe mode (titles not verified on hardware) ----",
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
HOOKS_DM_AVAIL_AND_CLOSE = extract_between(
    HOOKS_H,
    "static size_t dm_avail_range(",
    "static void shrink_early_mem_to(",
)

DEFINES = "\n".join(
    extract_define(MAIN_C, n)
    for n in [
        "SLOT0",
        "SLOT_SZ",
        "STATS_OFF",
        "WORK_SZ",
        "WORK_SZ_8",
        "DUMP_DONE_OFF",
        "DCB8_OFF",
        "DCB8_SZ",
        "PKT3",
        "TS_EOP",
        "TS2",
        "PASS_BARRIER",
        "SET_PASS",
    ]
)

FN_SAFE_STRIDE = extract_function(MAIN_C, "safe_surface_stride")
FN_CALC_RCAS = extract_function(MAIN_C, "calc_rcas_params")
FN_REAL_AA = extract_function(MAIN_C, "real_aa_on")   # called by rcas_static_flag (bit 5)
FN_RCAS_STATIC = extract_function(MAIN_C, "rcas_static_flag")
FN_BUILD_FLOW8 = extract_function(MAIN_C, "build_dcb_flow8")

HARNESS_C = f"""
#include <stdint.h>
#include <stddef.h>

typedef int64_t off_t;
typedef struct {{ void *FunctionPtr; }} Detour;
static int scePthreadGetthreadid(void) {{ return 1001; }}

extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);
extern void *calloc(size_t, size_t);
extern void free(void *);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, size_t);
extern int printf(const char *, ...);
extern void exit(int);

#define ASSERT(cond) do {{ \\
    if (!(cond)) {{ \\
        printf("ASSERT FAILED at line %d: %s\\n", __LINE__, #cond); \\
        exit(1); \\
    }} \\
}} while (0)

#define GPU_SZ_BASE 0x20000u
#define GPU_SZ      0x40000u
#define DUMP_SZ     0xC00000u
#define INJECT_AFTER_FLIPS 120
#define CUT_BLOCKS  5600u

typedef struct {{
    uint32_t format, tilingMode, aspectRatio, width, height, pitchInPixel, option;
    uint32_t _pad0;
    uint64_t _pad1;
}} VoAttr;

typedef struct {{
    uint64_t count, processTime, tsc;
    int64_t flipArg;
    uint64_t submitTsc;
    uint32_t _pad0;
    int32_t gcQueueNum, flipPendingNum, currentBuffer;
    uint32_t _pad1;
}} VoFlipStatus;

typedef struct {{
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
    int audio_fix;
}} fg_config_t;

static fg_config_t g_cfg;
static VoAttr g_attr;
static char g_title_id[16] = "CUSA08519";
static int32_t g_handle = 1;
static int g_ngame = 2;
static int g_mbase = 2;
static int g_nbuf = 3;
static int g_pcopy_avail = 0, g_pcopy_k = 1;
static volatile int g_pcopy_on = 0;
static int g_mbuf_state = 1;
static volatile int g_fg_off = 0;
static uint32_t g_bw = 1920, g_bh = 1080, g_pitch = 1920;
static uint32_t g_prev_idx = 0, g_prev2_idx = 0;
static uint64_t g_buf[16];

static int g_dm_hooks = 1;
static volatile int g_own_alloc = 0;
static uint64_t g_pyr_last_cur_base = 0;
static uint32_t g_pyr_last_w = 0, g_pyr_last_h = 0, g_pyr_parity = 0;
static inline void pyr_cache_invalidate(void) {{ g_pyr_last_cur_base = 0; g_pyr_last_w = 0; g_pyr_last_h = 0; }}
static int sceKernelUsleep(unsigned int us) {{ (void)us; return 0; }}
static uint32_t g_dbg_allocs = 0;
static uint32_t g_dbg_maps = 0;
static uint32_t g_dbg_alloc_fail = 0;
static uint32_t g_flips = 0;
static volatile uint32_t g_vo_submit_flips = 0;
static int g_gpu_state = 0;
static volatile uint32_t g_mem_hibernate_count = 0;
static int g_safe_never = 0;
static long g_low_sub2mb_end = 0;
static size_t g_gpu_sz = GPU_SZ_BASE;
static int g_work_garlic = 0;

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

static void flush_map_run(void) {{ g_map_run_cnt = 0; }}
static void api_ring_push(const char *fmt, ...) {{ (void)fmt; }}
static void stage_nop(const char *fmt, ...) {{ (void)fmt; }}
static void lg(const char *fmt, ...) {{ (void)fmt; }}
#define STAGE(...) stage_nop(__VA_ARGS__)
#define klog(...)  stage_nop(__VA_ARGS__)

// ---- Simulated OrbisOS DirectMemory & FlexibleMemory kernel ----
#define MAX_DM_BLOCKS 64
typedef struct {{
    long start;
    size_t len;
    int type;
    int in_use;
}} SimDmBlock;

#define MAX_FLEX_BLOCKS 64
typedef struct {{
    uintptr_t va;
    size_t len;
    int prot;
    int in_use;
    void *backing;
}} SimFlexBlock;

static size_t g_sim_dm_size = 0x128000000L;
static SimDmBlock g_sim_dm[MAX_DM_BLOCKS];
static SimFlexBlock g_sim_flex[MAX_FLEX_BLOCKS];
static size_t g_sim_flex_avail = 0x8000000ULL;
static size_t g_sim_flex_used = 0;
static size_t g_sim_dm_used = 0;

static void sim_reset(size_t dm_sz, size_t flex_avail)
{{
    g_sim_dm_size = dm_sz;
    g_sim_flex_avail = flex_avail;
    g_sim_flex_used = 0;
    g_sim_dm_used = 0;
    memset(g_sim_dm, 0, sizeof(g_sim_dm));
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {{
        if (g_sim_flex[i].in_use && g_sim_flex[i].backing) free(g_sim_flex[i].backing);
    }}
    memset(g_sim_flex, 0, sizeof(g_sim_flex));
}}

static size_t sceKernelGetDirectMemorySize(void) {{ return g_sim_dm_size; }}

static int32_t raw_sceKernelAvailableDirectMemorySize(off_t searchStart, off_t searchEnd, size_t alignment,
                                                      off_t *physAddrOut, size_t *sizeOut)
{{
    if (searchStart < 0) searchStart = 0;
    if ((size_t)searchEnd > g_sim_dm_size) searchEnd = (off_t)g_sim_dm_size;
    if (alignment == 0) alignment = 0x4000u;
    off_t best_start = 0;
    size_t best_len = 0;
    off_t cur = searchStart;
    while (cur < searchEnd) {{
        off_t aligned_cur = (cur + (off_t)alignment - 1) & ~((off_t)alignment - 1);
        if (aligned_cur >= searchEnd) break;
        off_t limit = searchEnd;
        int blocked = 0;
        for (int i = 0; i < MAX_DM_BLOCKS; i++) {{
            if (!g_sim_dm[i].in_use) continue;
            off_t bs = g_sim_dm[i].start;
            off_t be = bs + (off_t)g_sim_dm[i].len;
            if (bs <= aligned_cur && be > aligned_cur) {{
                cur = be;
                blocked = 1;
                break;
            }}
            if (bs > aligned_cur && bs < limit) limit = bs;
        }}
        if (blocked) continue;
        size_t len = (size_t)(limit - aligned_cur) & ~(size_t)0x3FFFu;
        if (len > best_len) {{
            best_len = len;
            best_start = aligned_cur;
        }}
        cur = limit;
    }}
    if (best_len == 0) return (int32_t)0x80020023u;
    if (physAddrOut) *physAddrOut = best_start;
    if (sizeOut) *sizeOut = best_len;
    return 0;
}}

static int32_t raw_sceKernelAllocateDirectMemory(long searchStart, long searchEnd, size_t len, size_t alignment,
                                                 int memoryType, long *physAddrOut)
{{
    if (alignment == 0) alignment = 0x4000u;
    off_t start = 0;
    size_t avail = 0;
    if (raw_sceKernelAvailableDirectMemorySize(searchStart, searchEnd, alignment, &start, &avail) != 0 || avail < len)
        return (int32_t)0x80020023u;
    for (int i = 0; i < MAX_DM_BLOCKS; i++) {{
        if (!g_sim_dm[i].in_use) {{
            g_sim_dm[i].in_use = 1;
            g_sim_dm[i].start = (long)start;
            g_sim_dm[i].len = len;
            g_sim_dm[i].type = memoryType;
            g_sim_dm_used += len;
            if (physAddrOut) *physAddrOut = (long)start;
            return 0;
        }}
    }}
    return (int32_t)0x80020023u;
}}

static int32_t sceKernelReleaseDirectMemory(long start, size_t len)
{{
    for (int i = 0; i < MAX_DM_BLOCKS; i++) {{
        if (g_sim_dm[i].in_use && g_sim_dm[i].start == start && g_sim_dm[i].len == len) {{
            g_sim_dm[i].in_use = 0;
            if (g_sim_dm_used >= len) g_sim_dm_used -= len;
            return 0;
        }}
    }}
    return 0;
}}

static int32_t raw_sceKernelMapDirectMemory(void **addr, size_t len, int prot, int flags, long dmStart, size_t align)
{{
    (void)prot; (void)flags;
    if (align < 0x4000u) align = 0x4000u;
    void *backing = calloc(1, len + align);
    if (!backing) return (int32_t)0x8002000Cu;
    uintptr_t raw_u = (uintptr_t)backing;
    uintptr_t al_u = (raw_u + align - 1u) & ~(align - 1u);
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {{
        if (!g_sim_flex[i].in_use) {{
            g_sim_flex[i].in_use = 1;
            g_sim_flex[i].va = al_u;
            g_sim_flex[i].len = len;
            g_sim_flex[i].prot = (int)dmStart;
            g_sim_flex[i].backing = backing;
            break;
        }}
    }}
    *addr = (void *)al_u;
    return 0;
}}

static int32_t sceKernelMapFlexibleMemory(void **addrInOut, size_t len, int prot, int flags)
{{
    (void)flags;
    if (g_sim_flex_used + len > g_sim_flex_avail) return (int32_t)0x8002000Cu;
    void *backing = calloc(1, len + 0x10000u);
    if (!backing) return (int32_t)0x8002000Cu;
    uintptr_t raw_u = (uintptr_t)backing;
    uintptr_t va = (raw_u + 0x3FFFu) & ~(uintptr_t)0x3FFFu;
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {{
        if (!g_sim_flex[i].in_use) {{
            g_sim_flex[i].in_use = 1;
            g_sim_flex[i].va = va;
            g_sim_flex[i].len = len;
            g_sim_flex[i].prot = prot;
            g_sim_flex[i].backing = backing;
            g_sim_flex_used += len;
            *addrInOut = (void *)va;
            return 0;
        }}
    }}
    free(backing);
    return (int32_t)0x8002000Cu;
}}

static int32_t sceKernelMunmap(void *addr, size_t len)
{{
    uintptr_t va = (uintptr_t)addr;
    for (int i = 0; i < MAX_FLEX_BLOCKS; i++) {{
        if (g_sim_flex[i].in_use && g_sim_flex[i].va == va) {{
            if (g_sim_flex_used >= g_sim_flex[i].len) g_sim_flex_used -= g_sim_flex[i].len;
            free(g_sim_flex[i].backing);
            g_sim_flex[i].in_use = 0;
            return 0;
        }}
    }}
    (void)len;
    return 0;
}}

static int32_t raw_sceKernelAvailableFlexibleMemorySize(size_t *sizeOut)
{{
    if (sizeOut) *sizeOut = (g_sim_flex_avail > g_sim_flex_used) ? (g_sim_flex_avail - g_sim_flex_used) : 0;
    return 0;
}}

static int32_t raw_sceVideoOutOpen(int32_t u, int32_t bus, int32_t idx, const void *p)
{{
    (void)u; (void)bus; (void)idx; (void)p;
    return 1;
}}

static int32_t raw_sceVideoOutClose(int32_t h)
{{
    (void)h;
    return 0;
}}

static VoFlipStatus g_mock_flip_status;
static int32_t raw_sceVideoOutGetFlipStatus(int32_t h, VoFlipStatus *s)
{{
    (void)h;
    if (s) *s = g_mock_flip_status;
    return 0;
}}

#define HOOK_INIT(name)
#define HOOK_CONTINUE(sym, fn_t, ...) raw_##sym(__VA_ARGS__)
#define sceKernelAvailableDirectMemorySize   raw_sceKernelAvailableDirectMemorySize
#define sceKernelAllocateDirectMemory        raw_sceKernelAllocateDirectMemory
#define sceKernelMapDirectMemory             raw_sceKernelMapDirectMemory
#define sceKernelAvailableFlexibleMemorySize raw_sceKernelAvailableFlexibleMemorySize
#define sceVideoOutGetFlipStatus             raw_sceVideoOutGetFlipStatus
#define sceVideoOutOpen                      raw_sceVideoOutOpen
#define sceVideoOutClose                     raw_sceVideoOutClose

static uint32_t g_wait_flip_calls = 0;
static uint32_t g_last_wait_flip_idx = 0xFFFFFFFFu;
static int32_t sceGnmInsertWaitFlipDone(uint32_t *p, uint32_t n, int32_t h, uint32_t i)
{{
    (void)n; (void)h;
    g_wait_flip_calls++;
    g_last_wait_flip_idx = i;
    memset(p, 0, 28);
    return 0;
}}

const uint8_t *vov_pal_ptr(void) {{ return NULL; }}
const uint8_t *vov_pix_ptr(void) {{ return NULL; }}

static uint32_t g_hud_packed = 0;
static uint32_t g_sh8_off[17];
static uint32_t g_dump_frame = 0;
static int g_dump_req = 0, g_fence_logged = 0;
static int g_opt_phase = 0, g_opt_marker = 0, g_opt_nocf = 0, g_opt_nofgk = 0, g_opt_noanc = 0;
static int g_opt_notp = 0, g_opt_nomf = 0, g_opt_notex = 0, g_opt_notpg = 0, g_opt_sharp = 0, g_opt_nostr = 0;
static int g_bench_offscreen = 0;
static uint8_t g_gpu_backing[0x40000 + 64];
static uint8_t g_work_backing[0x220000];
static uint8_t *g_gpu = g_gpu_backing;
static uint8_t *g_work = g_work_backing;
static uint32_t g_mon_backing[4];
static volatile uint32_t *g_mon = g_mon_backing;

static uint32_t cut_blocks8(void) {{ return 0; }}
static int post_mid_only(void) {{ return 1; }}
static void osd_build_slot(uint8_t *p, uint32_t *x, uint32_t *w, uint32_t flags)
{{
    (void)p; (void)flags;
    *x = 0; *w = 0;
}}
static uint32_t *gv_emit_eof(uint32_t *p, uint32_t s, uint32_t k) {{ (void)s; (void)k; return p; }}
static uint32_t *gv_emit_ts(uint32_t *p, uint32_t s, int i) {{ (void)s; (void)i; return p; }}
static uint32_t *gv_emit_after_gv8(uint32_t *p, uint32_t s, uint32_t k) {{ (void)s; (void)k; return p; }}
static void gv_fill_table(uint32_t *p, uint32_t s, uint32_t k) {{ (void)p; (void)s; (void)k; }}
static int gv_pass_active(void) {{ return 0; }}
static int gv_warp_active(void) {{ return 0; }}
static int gv_viz_active(void) {{ return 0; }}

#include "shaders.h"
#include "fg_res.h"
#include "fg8_table.h"
static int vcfg_in_family(const char *t, const char *f) {{ return strcmp(t, f) == 0; }}
#include "two_buffer_test.h"

{DEFINES}
{HOOKS_DECLS}
{HOOKS_VMAP}
{MAIN_ALLOC_PART1}
{HOOKS_DM_AVAIL_AND_CLOSE}
{MAIN_ALLOC_PART2}
{FN_SAFE_STRIDE}
{MAIN_2BUF_HELPERS}
{FN_CALC_RCAS}
{FN_REAL_AA}
{FN_RCAS_STATIC}
{FN_BUILD_FLOW8}

// Simulate GPU pcopy8 execution on the descriptor table built at slotb + OSD_SLOT_OFF
static void simulate_gpu_pcopy8(uint32_t slot)
{{
    uint8_t *slotb = g_gpu + SLOT0 + slot * SLOT_SZ;
    uint32_t *tb3 = (uint32_t *)(slotb + OSD_SLOT_OFF);
    uint64_t src_va = ((uint64_t)tb3[8] << 8) | ((uint64_t)(tb3[9] & 0xFFu) << 40);
    uint64_t dst_va = ((uint64_t)tb3[16] << 8) | ((uint64_t)(tb3[17] & 0xFFu) << 40);
    uint32_t dst_dfmt = (tb3[17] >> 20) & 0x3Fu;
    if (!src_va || !dst_va) return;
    if (dst_dfmt == 16u) {{
        // 50%-footprint 16-bpp (2B/px) history surface: compress via YCoCg-R 4:2:0 16-bit
        ycocg420_compress_frame((const uint32_t *)(uintptr_t)src_va,
                                (uint32_t *)(uintptr_t)dst_va, 64, 64, 64);
    }} else {{
        memcpy((void *)(uintptr_t)dst_va, (const void *)(uintptr_t)src_va, 64 * 64 * 4);
    }}
}}

static void test_suite1_auto_2buf_detection(void)
{{
    (void)vq_report;
    (void)tb_on_game_submit;
    (void)read_flip_status;
    (void)sceVideoOutOpen_hook;
    (void)release_early_dump;
    (void)release_early_gpu;
    (void)alloc_gv_dm;
    (void)alloc_work_early;

    sim_reset(0x128000000ULL, 0x8000000ULL);
    memset(&g_cfg, 0, sizeof(g_cfg));
    g_cfg.present_copy = 0; // AUTO
    g_cfg.two_buffer = 0;   // AUTO
    g_status_hooked = 1;
    g_vq_on = 0;
    g_runtime_2buf = 0;
    g_pcopy_avail = 0;
    g_pcopy_k = 1;
    g_mbuf_state = 1;
    g_mbase = 2;
    g_ngame = 2;
    g_prev_idx = 1;

    // 1A: RegisterBuffers with count == 2 automatically enables virtual flip queue & allocates g_pbuf
    on_register_buffers_2buf(2, 0x800000u);
    ASSERT(g_runtime_2buf == 1);
    ASSERT(g_vq_on == 1);
    ASSERT(vq_active() == 1);
    ASSERT(g_pbuf != NULL);
    ASSERT(g_pcopy_avail == 1);

    // Verify sceVideoOutGetFlipStatus_hook virtualizes pending flips and currentBuffer for 2-buffer game
    g_mock_flip_status.currentBuffer = 2; // on our mid buffer (>= g_mbase)
    g_mock_flip_status.flipPendingNum = 2;
    g_mock_flip_status.gcQueueNum = 2;
    VoFlipStatus st;
    memset(&st, 0, sizeof(st));
    ASSERT(sceVideoOutGetFlipStatus_hook(g_handle, &st) == 0);
    ASSERT(st.flipPendingNum == 1);
    ASSERT(st.gcQueueNum == 1);
    ASSERT(st.currentBuffer == (int32_t)g_prev_idx);

    // 1B: RegisterBuffers with count == 4 resets 2-buffer mode (including g_vq_on from a 2-buffer splash screen,
    //     e.g. CUSA10249 count=2 -> count=3 transition), then flipping a 2-buffer ring (0,1,0,1,0,1)
    //     dynamically detects the 2-buffer flip ring at runtime!
    g_ngame = 4;
    g_pcopy_avail = 0;
    on_register_buffers_2buf(4, 0x800000u);
    ASSERT(g_runtime_2buf == 0);
    ASSERT(g_vq_on == 0);
    ASSERT(vq_active() == 0);
    ASSERT(g_pbuf == NULL);

    g_buf[0] = 0x210000000ULL;
    g_buf[1] = 0x210800000ULL;
    for (int f = 0; f < 6; f++) {{
        on_flip_2buf_track((uint32_t)(f & 1));
    }}
    ASSERT(g_runtime_2buf == 1);
    ASSERT(g_vq_on == 1);
    ASSERT(vq_active() == 1);
    ASSERT(g_pbuf != NULL);
    ASSERT(g_pcopy_avail == 1);
    release_pbuf();
}}

static void test_suite2_lowmem_flex_and_ycocg_fallback(void)
{{
    // Simulate CUSA08519 / CUSA17419 / CUSA14209 tight DirectMemory:
    // Only 8 MB of DirectMemory exists, already claimed by g_pre_mem (1 mid buffer), 0 B DirectMemory free.
    sim_reset(0x800000ULL, 0x2000000ULL /* 32 MB FlexibleMemory available */);
    long pm_phys = 0;
    ASSERT(raw_sceKernelAllocateDirectMemory(0, 0x800000L, 0x800000u, 0x200000u, 3, &pm_phys) == 0);
    ASSERT(raw_sceKernelMapDirectMemory(&g_pre_mem, 0x800000u, 0x33, 0, pm_phys, 0x200000u) == 0);
    g_pre_mem_phys = pm_phys;
    g_pre_size = 0x800000u;
    g_mem_tight = 1;
    size_t dm_before = g_sim_dm_used;

    // 2A: FlexibleMemory >= 24 MB -> allocates full 8 MB g_pbuf in FlexibleMemory (0 DirectMemory bytes)
    on_register_buffers_2buf(2, 0x800000u);
    ASSERT(g_pbuf != NULL);
    ASSERT(g_pbuf_phys == -1L);
    ASSERT(g_pbuf_sz == 0x800000u);
    ASSERT(g_pbuf_ycocg == 0);
    ASSERT(g_sim_dm_used == dm_before); // 0 B extra DirectMemory!
    ASSERT(g_sim_flex_used >= 0x800000u);

    // Hibernation cleanup test: release_early_work() when g_flips > 0 frees g_pbuf
    g_flips = 200;
    g_pre_work = alloc_flex_gpu(WORK_SZ_8, 0x10000u);
    g_pre_work_phys = -1L;
    g_pre_work_sz = WORK_SZ_8;
    ASSERT(g_pre_work != NULL);
    release_early_work();
    ASSERT(g_pbuf == NULL);
    ASSERT(g_sim_flex_used == 0);

    // 2B: Tight FlexibleMemory (< 24 MB free, e.g. 12 MB available) -> falls back to 50%-footprint
    //     4.25 MB (0x440000) YCoCg-R 16-bit compressed history surface in FlexibleMemory!
    g_sim_flex_avail = 0xC00000ULL; // 12 MB
    on_register_buffers_2buf(2, 0x800000u);
    ASSERT(g_pbuf != NULL);
    ASSERT(g_pbuf_phys == -1L);
    ASSERT(g_pbuf_sz == 0x440000u); // 4.25 MB (50% footprint, <= 4.5 MB)
    ASSERT(g_pbuf_ycocg == 1);
    ASSERT(g_sim_dm_used == dm_before); // 0 B extra DirectMemory!

    // VideoOutClose cleanup test: sceVideoOutClose_hook releases g_pbuf cleanly
    ASSERT(sceVideoOutClose_hook(g_handle) == 0);
    ASSERT(g_pbuf == NULL);
    ASSERT(g_sim_flex_used == 0);
    release_early_mem();
}}

static void test_suite3_prev_overwrite_protection_and_ycocg_psnr(void)
{{
    sim_reset(0x800000ULL, 0xC00000ULL /* 12 MB flex -> forces 50% YCoCg-R 16b g_pbuf */);
    g_mem_tight = 1;
    g_pre_size = 0x800000u;
    for (int i = 0; i < 17; i++) g_sh8_off[i] = 0x8000u + (uint32_t)i * 256u;

    void *raw_b0 = calloc(64 * 64 + 64, sizeof(uint32_t));
    void *raw_b1 = calloc(64 * 64 + 64, sizeof(uint32_t));
    void *raw_bm = calloc(64 * 64 + 64, sizeof(uint32_t));
    ASSERT(raw_b0 && raw_b1 && raw_bm);
    uint32_t *game_buf0 = (uint32_t *)(((uintptr_t)raw_b0 + 255u) & ~(uintptr_t)255u);
    uint32_t *game_buf1 = (uint32_t *)(((uintptr_t)raw_b1 + 255u) & ~(uintptr_t)255u);
    uint32_t *mid_buf   = (uint32_t *)(((uintptr_t)raw_bm + 255u) & ~(uintptr_t)255u);

    g_buf[0] = (uint64_t)(uintptr_t)game_buf0;
    g_buf[1] = (uint64_t)(uintptr_t)game_buf1;
    g_buf[2] = (uint64_t)(uintptr_t)mid_buf;
    g_ngame = 2;
    g_mbase = 2;
    g_nbuf = 3;
    g_pcopy_k = 1;

    // Test 3A: Full-resolution g_pbuf (8 MB) T#prev protection against game overwriting buffers[prev_idx]
    g_sim_flex_avail = 0x2000000ULL; // 32 MB -> 8 MB full-res g_pbuf
    on_register_buffers_2buf(2, 0x800000u);
    ASSERT(g_pbuf != NULL && g_pbuf_ycocg == 0);

    // Frame 120: game renders Frame 120 (0xFF443322) into game_buf1 (index=1, prev_idx=0)
    for (int i = 0; i < 64 * 64; i++) {{
        game_buf0[i] = 0xFF001122u;
        game_buf1[i] = 0xFF443322u; // Frame 120 pixel signature
    }}
    uint32_t nop64[64];
    memset(nop64, 0, sizeof(nop64));
    uint32_t *mine0 = (uint32_t *)(g_gpu + DCB8_OFF);
    g_wait_flip_calls = 0;
    uint32_t dw0 = build_dcb_flow8(mine0, g_buf[0], g_buf[1], g_buf[2], g_buf[2], 1920,
                                   10u, 1920 * 1080 * 4u, 0u, 120u, nop64, g_handle, 2u, 2u,
                                   32768u, 32768u, 0, 0, 0ull, 0u);
    ASSERT(dw0 > 64u);
    // Only 1 WaitFlipDone call (for mid1_idx=2), NOT for offscreen g_pbuf (copy_idx=0xFFFF)!
    ASSERT(g_wait_flip_calls == 1u && g_last_wait_flip_idx == 2u);
    simulate_gpu_pcopy8(0u); // GPU executes pcopy8 at end of Frame 120, saving game_buf1 into g_pbuf

    // Now the game immediately overwrites game_buf1 (buffers[prev_idx]) with Frame 122 garbage BEFORE/DURING Frame 121!
    for (int i = 0; i < 64 * 64; i++) {{
        game_buf1[i] = 0xDEADDEADu; // Overwritten by game!
        game_buf0[i] = 0xFF556677u; // Frame 121 in game_buf0
    }}

    // Frame 121 (index=0, prev_idx=1): caller passes g_buf[1] (corrupted game_buf1) as P
    uint32_t *mine1 = (uint32_t *)(g_gpu + DCB8_OFF + DCB8_SZ);
    uint32_t dw1 = build_dcb_flow8(mine1, g_buf[1], g_buf[0], g_buf[2], g_buf[2], 1920,
                                   10u, 1920 * 1080 * 4u, 1u, 121u, nop64, g_handle, 2u, 2u,
                                   32768u, 32768u, 0, 0, 0ull, 0u);
    ASSERT(dw1 > 64u);
    uint32_t *tb_slot1 = (uint32_t *)(g_gpu + SLOT0 + 1u * SLOT_SZ + 0xB00);
    uint64_t t_prev_va = ((uint64_t)tb_slot1[0] << 8) | ((uint64_t)(tb_slot1[1] & 0xFFu) << 40);
    // T#prev MUST point to g_pbuf (NOT the overwritten game_buf1)!
    ASSERT(t_prev_va == (uint64_t)(uintptr_t)g_pbuf);
    ASSERT(t_prev_va != g_buf[1]);
    const uint32_t *protected_prev = (const uint32_t *)(uintptr_t)t_prev_va;
    ASSERT(protected_prev[0] == 0xFF443322u);
    ASSERT(protected_prev[1023] == 0xFF443322u);
    release_pbuf();

    // Test 3B: 50%-footprint YCoCg-R 4:2:0 16-bit g_pbuf (4.25 MB) descriptor & PSNR fidelity check
    g_sim_flex_avail = 0xC00000ULL; // 12 MB -> forces 50% compressed g_pbuf_ycocg == 1
    on_register_buffers_2buf(2, 0x800000u);
    ASSERT(g_pbuf != NULL && g_pbuf_ycocg == 1 && g_pbuf_sz == 0x440000u);

    // Frame 130: populate game_buf1 with a smooth gradient scene
    for (uint32_t y = 0; y < 64; y++) {{
        for (uint32_t x = 0; x < 64; x++) {{
            uint32_t r = x * 2u + y;
            uint32_t g = x + y * 2u + 16u;
            uint32_t b = 64u + x + y;
            game_buf1[y * 64 + x] = r | (g << 8) | (b << 16) | 0xFF000000u;
        }}
    }}
    build_dcb_flow8(mine0, g_buf[0], g_buf[1], g_buf[2], g_buf[2], 1920,
                    10u, 1920 * 1080 * 4u, 0u, 130u, nop64, g_handle, 2u, 2u,
                    32768u, 32768u, 0, 0, 0ull, 0u);
    simulate_gpu_pcopy8(0u); // Compresses game_buf1 into 16-bit YCoCg-R 4:2:0 in g_pbuf

    // Overwrite game_buf1 with garbage before Frame 131
    for (int i = 0; i < 64 * 64; i++) game_buf1[i] = 0xDEADDEADu;

    build_dcb_flow8(mine1, g_buf[1], g_buf[0], g_buf[2], g_buf[2], 1920,
                    10u, 1920 * 1080 * 4u, 1u, 131u, nop64, g_handle, 2u, 2u,
                    32768u, 32768u, 0, 0, 0ull, 0u);
    uint64_t t_prev_va2 = ((uint64_t)tb_slot1[0] << 8) | ((uint64_t)(tb_slot1[1] & 0xFFu) << 40);
    uint32_t t_prev_dfmt = (tb_slot1[1] >> 20) & 0x3Fu;
    uint32_t t_prev_tidx = (tb_slot1[3] >> 20) & 0x1Fu;
    ASSERT(t_prev_va2 == (uint64_t)(uintptr_t)g_pbuf);
    ASSERT(t_prev_dfmt == 16u); // 16-bpp 5_6_5 (2 bytes/pixel = 50% footprint, 3 RGB channels)
    ASSERT(t_prev_tidx == 8u); // Linear aligned
    uint32_t *tb3_slot1 = (uint32_t *)(g_gpu + SLOT0 + 1u * SLOT_SZ + OSD_SLOT_OFF);
    ASSERT(tb3_slot1[26] == 1920u * 1080u * 2u); // V#ycocg size = 4,147,200 bytes (4.15 MB)

    // Decompress from g_pbuf and verify MSE / PSNR against original Frame 130 gradient
    uint32_t *decoded = (uint32_t *)calloc(64 * 64, sizeof(uint32_t));
    ASSERT(decoded != NULL);
    ycocg420_decompress_frame((const uint32_t *)g_pbuf, decoded, 64, 64, 64);
    uint64_t sq_err_sum = 0;
    for (uint32_t y = 0; y < 64; y++) {{
        for (uint32_t x = 0; x < 64; x++) {{
            int32_t r0 = (int32_t)(x * 2u + y);
            int32_t g0 = (int32_t)(x + y * 2u + 16u);
            int32_t b0 = (int32_t)(64u + x + y);
            uint32_t px = decoded[y * 64 + x];
            int32_t dr = r0 - (int32_t)(px & 0xFFu);
            int32_t dg = g0 - (int32_t)((px >> 8) & 0xFFu);
            int32_t db = b0 - (int32_t)((px >> 16) & 0xFFu);
            sq_err_sum += (uint64_t)(dr * dr + dg * dg + db * db);
        }}
    }}
    // MSE <= 3.0 per channel corresponds to PSNR > 43 dB (65025 / 3.0 = 21675 -> 43.36 dB)
    ASSERT(sq_err_sum < (uint64_t)(64 * 64 * 3 * 3));

    free(decoded);
    free(raw_b0);
    free(raw_b1);
    free(raw_bm);
    release_pbuf();
}}

int main(void)
{{
    test_suite1_auto_2buf_detection();
    test_suite2_lowmem_flex_and_ycocg_fallback();
    test_suite3_prev_overwrite_protection_and_ycocg_psnr();
    printf("2-buffer history protection & YCoCg-R 16-bit unit tests: all 3 suites passed\\n");
    return 0;
}}
"""


def main() -> None:
    out_dir = ROOT / "build" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    c_path = out_dir / "test_2buf_history_protection.c"
    bc_path = out_dir / "test_2buf_history_protection.bc"
    c_path.write_text(HARNESS_C, encoding="utf-8")

    llvm_bin = pathlib.Path(cfg["llvm_bin"])
    subprocess.run(
        [
            str(llvm_bin / "clang.exe"),
            "--target=x86_64-pc-windows-msvc",
            "-O0",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-variable",
            "-Wno-unused-function",
            "-fno-builtin",
            "-I" + str(ROOT / "classic" / "source"),
            "-I" + str(ROOT / "overlay"),
            "-emit-llvm",
            "-c",
            str(c_path),
            "-o",
            str(bc_path),
        ],
        check=True,
    )
    subprocess.run(
        [str(llvm_bin / "lli.exe"), "--dlopen=msvcrt.dll", str(bc_path)],
        check=True,
    )


import unittest


class Test2BufHistoryProtection(unittest.TestCase):
    def test_all_2buf_history_protection_suites(self) -> None:
        main()


if __name__ == "__main__":
    main()
