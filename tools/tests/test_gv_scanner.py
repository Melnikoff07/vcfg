"""Standalone host unit test for PM4 DCB velocity/depth scanner (pm4scan), readable-range
validation & capture setup (gv), and velocity encoding / DRS auto-calibration (gvcal).

Compiles the actual C implementation to host bitcode via configured LLVM clang.exe and
executes it under lli.exe (no PS4 hardware required).
"""
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
llvm = pathlib.Path(cfg["llvm_bin"])

out = ROOT / "build/tests"
out.mkdir(parents=True, exist_ok=True)

(out / "string.h").write_text(
    """#pragma once
#include <stddef.h>
extern void *memset(void *, int, size_t);
extern void *memcpy(void *, const void *, size_t);
extern int memcmp(const void *, const void *, size_t);
extern char *strstr(const char *, const char *);
extern int strcmp(const char *, const char *);
extern char *strcpy(char *, const char *);
extern size_t strlen(const char *);
""",
    encoding="utf-8",
)

(out / "stdio.h").write_text(
    """#pragma once
#include <stddef.h>
extern int _snprintf(char *, size_t, const char *, ...);
#define snprintf _snprintf
extern int printf(const char *, ...);
extern int sscanf(const char *, const char *, ...);
""",
    encoding="utf-8",
)

gv_text = (ROOT / "classic/source/gv.c").read_text(encoding="utf-8")


def extract_gv_fn(name):
    m = re.search(r"^(?:static\s+)?[^\n]+\b" + name + r"\([^\n]*\)\n\{", gv_text, re.M)
    assert m, f"Function {name} not found in gv.c"
    start = m.start()
    pos = gv_text.index("{", start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (gv_text[end] == "{") - (gv_text[end] == "}")
        end += 1
    return gv_text[start:end]


# Verify shader hardware limits in classic/source/shaders.h
shaders_h = (ROOT / "classic/source/shaders.h").read_text(encoding="utf-8")
for m in re.finditer(r"//\s*(\w+):\s*\d+\s*B,\s*vgprs=(\d+)\s*sgprs=(\d+)", shaders_h):
    sh_name, vgprs, sgprs = m.group(1), int(m.group(2)), int(m.group(3))
    assert vgprs <= 64, f"Shader {sh_name} exceeds 64 VGPRs: {vgprs}"

test_c = out / "test_gv_scanner_host.c"
test_bc = out / "test_gv_scanner_host.bc"

c_code = (
    r'''#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>

#include "pm4scan.c"
#include "gvcal.c"

// Mock Orbis kernel memory query structures & syscalls for gv.c's mem_ok / mk_t_target
typedef struct {
    void *unk01;
    void *unk02;
    uint64_t offset;
    int32_t unk04;
    int32_t isCommitted;
    char name[32];
} OrbisKernelVirtualQueryInfo;

typedef struct {
    uintptr_t s, e;
    int32_t prot, committed;
} mock_vma_t;

static mock_vma_t g_vmas[16];
static int g_nvmas = 0;
static uint32_t g_vq_calls = 0;

static int32_t sceKernelVirtualQuery(const void *addr, int flags, OrbisKernelVirtualQueryInfo *info, size_t infoSize)
{
    (void)flags; (void)infoSize;
    g_vq_calls++;
    uintptr_t a = (uintptr_t)addr;
    for (int i = 0; i < g_nvmas; i++) {
        if (a >= g_vmas[i].s && a < g_vmas[i].e) {
            memset(info, 0, sizeof(*info));
            info->unk01 = (void *)g_vmas[i].s;
            info->unk02 = (void *)g_vmas[i].e;
            info->unk04 = g_vmas[i].prot;
            info->isCommitted = g_vmas[i].committed;
            return 0;
        }
    }
    return -1;
}

static int32_t sceKernelQueryMemoryProtection(void *addr, void **start, void **end, int32_t *prot)
{
    uintptr_t a = (uintptr_t)addr;
    for (int i = 0; i < g_nvmas; i++) {
        if (a >= g_vmas[i].s && a < g_vmas[i].e && g_vmas[i].committed) {
            if (start) *start = (void *)g_vmas[i].s;
            if (end) *end = (void *)g_vmas[i].e;
            if (prot) *prot = g_vmas[i].prot;
            return 0;
        }
    }
    return -1;
}

static int g_gv_neo = 0;
#define PROT_CPU_READ 0x01
#define PROT_GPU_READ 0x10
#define GV_MC_MAX     64u
typedef struct { uintptr_t s, e; int32_t prot; } mrange_t;
static mrange_t g_mc[GV_MC_MAX];
static uint32_t g_mcn = 0, g_mq = 0, g_mq_fail = 0;
'''
    + extract_gv_fn("mem_ok")
    + "\n"
    + extract_gv_fn("mk_t_target")
    + r'''

#define PKT3(op, nbody) (0xC0000000u | (((uint32_t)(nbody) - 1u) << 16) | ((uint32_t)(op) << 8))

static uint32_t f2u(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static float u2f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void build_tsharp(uint32_t *t, uint64_t base, uint32_t dfmt, uint32_t nfmt,
                         uint32_t w, uint32_t h, uint32_t pitch, uint32_t tile, int neo)
{
    t[0] = (uint32_t)(base >> 8);
    t[1] = (uint32_t)((base >> 40) & 0x3Fu) | (dfmt << 20) | (nfmt << 26);
    t[2] = (w - 1u) | ((h - 1u) << 14);
    t[3] = 4u | (5u << 3) | (6u << 6) | (7u << 9) | ((tile & 0x1Fu) << 20) | (9u << 28);
    t[4] = (pitch - 1u) << 13;
    t[5] = 0;
    t[6] = (neo && tile != 8u) ? (1u << 24) : 0u;
    t[7] = 0;
}

static uint32_t *emit_cb(uint32_t *p, int slot, uint64_t base, uint32_t fmt, uint32_t num,
                         uint32_t pitch, uint32_t alloc_h, uint32_t tile)
{
    uint32_t reg = CTX_CB_COLOR0_BASE + CB_STRIDE * slot;
    *p++ = PKT3(OP_SET_CONTEXT_REG, 7);
    *p++ = reg;
    *p++ = (uint32_t)(base >> 8);
    *p++ = (pitch / 8u) - 1u;
    *p++ = (pitch * alloc_h / 64u) - 1u;
    *p++ = 0;
    *p++ = (fmt << 2) | (num << 8) | (1u << 13);
    *p++ = tile & 0x1Fu;
    return p;
}

static uint32_t *emit_db(uint32_t *p, uint64_t base, uint32_t zfmt, uint32_t pitch, uint32_t alloc_h,
                         uint32_t tile, uint32_t zfunc)
{
    *p++ = PKT3(OP_SET_CONTEXT_REG, 8);
    *p++ = CTX_DB_Z_INFO;
    *p++ = (zfmt & 3u) | ((tile & 7u) << 20);
    *p++ = 0;
    *p++ = (uint32_t)(base >> 8);
    *p++ = 0;
    *p++ = (uint32_t)(base >> 8);
    *p++ = 0;
    *p++ = ((pitch / 8u) - 1u) | ((((alloc_h / 8u) - 1u) & 0x7FFu) << 11);
    *p++ = PKT3(OP_SET_CONTEXT_REG, 2);
    *p++ = CTX_DB_DEPTH_CONTROL;
    *p++ = 2u | 4u | ((zfunc & 7u) << 4);
    return p;
}

static uint32_t *emit_scissor(uint32_t *p, uint32_t w, uint32_t h)
{
    *p++ = PKT3(OP_SET_CONTEXT_REG, 2);
    *p++ = CTX_PA_SC_SCREEN_SCISSOR_BR;
    *p++ = (w & 0x7FFFu) | ((h & 0x7FFFu) << 16);
    return p;
}

static int host_readable(const void *ptr, size_t n, void *user)
{
    (void)user;
    return mem_ok((uintptr_t)ptr, n, PROT_CPU_READ);
}

static uint64_t g_fake_clock = 1000;
static uint64_t host_clock(void *user)
{
    (void)user;
    g_fake_clock += 2;
    return g_fake_clock;
}

// ---------------------------------------------------------------------------------------------------------------
// Test 1: PM4 DCB traversal, EUD 2-dword aligned T# tables, nested EUD->ResourceTable, IBs, and Z24/RG16 formats
static int test_pm4_traversal_and_safety(void)
{
    static uint32_t dcb_top[512];
    static uint32_t ib_level1[256];
    static uint32_t ib_level2[256];
    static uint32_t ib_chained[256];
    static uint32_t eud_ps[64];
    static uint32_t eud_cs[32];
    static uint32_t nested_rt[32];
    static uint32_t loaded_ctx[0x400];

    // Register host memory buffers in the mock VMA table
    g_nvmas = 0;
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)dcb_top, (uintptr_t)(dcb_top + 512), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)ib_level1, (uintptr_t)(ib_level1 + 256), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)ib_level2, (uintptr_t)(ib_level2 + 256), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)ib_chained, (uintptr_t)(ib_chained + 256), PROT_CPU_READ | PROT_GPU_READ, 1 };
    // Map only 16 dwords of eud_cs to test adaptive partial-page EUD table probing
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)eud_ps, (uintptr_t)(eud_ps + 64), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)eud_cs, (uintptr_t)(eud_cs + 16), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)nested_rt, (uintptr_t)(nested_rt + 32), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)loaded_ctx, (uintptr_t)(loaded_ctx + 0x400), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_mcn = 0;

    uint64_t addr_vel_f16 = 0x120000000ull;    // RG16_FLOAT (fmt=5, num=7)
    uint64_t addr_vel_snorm = 0x121000000ull;  // RG16_SNORM (fmt=5, num=1)
    uint64_t addr_vel_unorm = 0x122000000ull;  // RG16_UNORM (fmt=5, num=0)
    uint64_t addr_vel_rgba16 = 0x123000000ull; // RGBA16_FLOAT (fmt=12, num=7)
    uint64_t addr_depth_z24 = 0x124000000ull;  // Z24 (fmt=2)
    uint64_t addr_cs_vel = 0x125000000ull;     // Compute-only RG16_FLOAT surface discovered via T#

    // Build ib_level2: MRT draw to RG16_FLOAT + RG16_SNORM + RG16_UNORM + RGBA16_FLOAT and Z24 depth
    uint32_t *p2 = ib_level2;
    *p2++ = PKT3(OP_SET_CONTEXT_REG, 2); *p2++ = CTX_CB_COLOR_CONTROL; *p2++ = 1u << 4;
    *p2++ = PKT3(OP_SET_CONTEXT_REG, 3); *p2++ = CTX_CB_TARGET_MASK; *p2++ = 0xFFFFu; *p2++ = 0xFFFFu;
    // Padded allocation height 1088 -> should unpad to 1080
    p2 = emit_cb(p2, 0, addr_vel_f16, 5, 7, 1920, 1088, 14);
    // Viewport-scaled DRS on slot 1: 1536x864 (80% of 1080p)
    p2 = emit_cb(p2, 1, addr_vel_snorm, 5, 1, 1920, 1080, 14);
    p2 = emit_cb(p2, 2, addr_vel_unorm, 5, 0, 1920, 1080, 14);
    p2 = emit_cb(p2, 3, addr_vel_rgba16, 12, 7, 1920, 1080, 14);
    p2 = emit_db(p2, addr_depth_z24, 2 /*Z24*/, 1920, 1088, 5, 6 /*GEQUAL reversed Z*/);
    for (int d = 0; d < 25; d++) {
        *p2++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p2++ = 3; *p2++ = 0;
    }

    // Build ib_level1: calls ib_level2, plus a cyclic self-IB and an unmapped IB (must not fault!)
    uint32_t *p1 = ib_level1;
    uintptr_t a2 = (uintptr_t)ib_level2;
    *p1++ = PKT3(OP_INDIRECT_BUFFER, 3);
    *p1++ = (uint32_t)(a2 & ~3u);
    *p1++ = (uint32_t)(a2 >> 32);
    *p1++ = (uint32_t)(p2 - ib_level2);
    // Cyclic IB pointing back to ib_level1
    uintptr_t a1 = (uintptr_t)ib_level1;
    *p1++ = PKT3(OP_INDIRECT_BUFFER, 3);
    *p1++ = (uint32_t)(a1 & ~3u);
    *p1++ = (uint32_t)(a1 >> 32);
    *p1++ = 4u;
    // Unmapped IB pointer
    uint64_t bad_ib = 0x7FF0DEAD000ull;
    *p1++ = PKT3(OP_INDIRECT_BUFFER, 3);
    *p1++ = (uint32_t)bad_ib;
    *p1++ = (uint32_t)(bad_ib >> 32);
    *p1++ = 16u;

    // Build eud_ps: place a 2-dword pointer at offset 0, then T# descriptors at 2-dword aligned offsets (2, 10, 18)!
    memset(eud_ps, 0, sizeof(eud_ps));
    eud_ps[0] = 0xDEAD0000u; eud_ps[1] = 0x0000007Full; // unmapped pointer inside EUD
    // T# at eud_ps + 2: samples addr_vel_f16 with game T# tile=16, pitch=1920, DRS active size 1600x900
    build_tsharp(eud_ps + 2, addr_vel_f16, 5, 7, 1600, 900, 1920, 16, 1);
    // T# at eud_ps + 10: samples Z24 depth buffer (IMG_DATA_FORMAT_8_24 = 20) with game T# tile=6
    build_tsharp(eud_ps + 10, addr_depth_z24, 20, 0, 1920, 1080, 1920, 6, 1);
    // T# at eud_ps + 18: samples RG16_UNORM velocity
    build_tsharp(eud_ps + 18, addr_vel_unorm, 5, 0, 1920, 1080, 1920, 14, 1);

    // Build eud_cs (only 16 dwords mapped): nested pointer at offset 0 -> nested_rt, plus inline T# at offset 2
    memset(eud_cs, 0, sizeof(eud_cs));
    uintptr_t nrt_a = (uintptr_t)nested_rt;
    eud_cs[0] = (uint32_t)nrt_a;
    eud_cs[1] = (uint32_t)(nrt_a >> 32);
    build_tsharp(eud_cs + 2, addr_vel_snorm, 5, 1, 1536, 864, 1920, 14, 1);

    memset(nested_rt, 0, sizeof(nested_rt));
    // T# in nested_rt + 2: standalone compute velocity surface addr_cs_vel (1280x720)
    build_tsharp(nested_rt + 2, addr_cs_vel, 5, 7, 1280, 720, 1280, 14, 1);

    // Build ib_chained: binds eud_ps in PS user data, draws, then binds eud_cs in CS user data and dispatches
    uint32_t *pc = ib_chained;
    uintptr_t eps_a = (uintptr_t)eud_ps;
    *pc++ = PKT3(OP_SET_SH_REG, 5);
    *pc++ = SH_USER_DATA_PS_0;
    *pc++ = (uint32_t)eps_a;
    *pc++ = (uint32_t)(eps_a >> 32);
    *pc++ = 0xDEADBEE0u; // unmapped pointer in user data slot 2..3 (must not fault)
    *pc++ = 0x0000007Fu;
    *pc++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *pc++ = 3; *pc++ = 0;

    uintptr_t ecs_a = (uintptr_t)eud_cs;
    *pc++ = PKT3(OP_SET_SH_REG, 3);
    *pc++ = SH_COMPUTE_USER_DATA_0;
    *pc++ = (uint32_t)ecs_a;
    *pc++ = (uint32_t)(ecs_a >> 32);
    *pc++ = PKT3(OP_DISPATCH_DIRECT, 4); *pc++ = 60; *pc++ = 34; *pc++ = 1; *pc++ = 1;

    // Build dcb_top: calls ib_level1, then chains into ib_chained
    uint32_t *pt = dcb_top;
    *pt++ = PKT3(OP_INDIRECT_BUFFER, 3);
    *pt++ = (uint32_t)(a1 & ~3u);
    *pt++ = (uint32_t)(a1 >> 32);
    *pt++ = (uint32_t)(p1 - ib_level1);

    uintptr_t ac = (uintptr_t)ib_chained;
    *pt++ = PKT3(OP_INDIRECT_BUFFER, 3);
    *pt++ = (uint32_t)(ac & ~3u);
    *pt++ = (uint32_t)(ac >> 32);
    *pt++ = (uint32_t)(pc - ib_chained) | (1u << 20); // CHAIN = 1

    static pm4_frame_t f;
    memset(&f, 0, sizeof(f));
    f.mode = PM4_FULL;
    f.readable = host_readable;
    f.clock = host_clock;
    f.budget_us = 50000;
    f.max_dwords = 100000;
    pm4_frame_begin(&f);
    pm4_call_begin(&f);
    int rc = pm4_walk_entry(&f, dcb_top, (uint32_t)(pt - dcb_top), 0);
    if (rc != PM4_OK) return 10;
    if (f.ib_unreadable != 2) return 11; // cyclic IB + unmapped IB caught safely
    if (f.depth_max != 2) return 12;
    if (f.nrt < 6) return 13; // 4 CB + 1 DB + 1 T#-discovered CS surface

    pm4_rank(&f, 1920, 1080);

    // Verify addr_vel_f16 was refined to 1600x900 by the sampling T# at eud_ps + 2 and game T# tile=16 was extracted
    const pm4_rt_t *rt_f16 = NULL, *rt_z24 = NULL, *rt_cs = NULL;
    for (uint32_t i = 0; i < f.nrt; i++) {
        if (f.rt[i].base == addr_vel_f16) rt_f16 = &f.rt[i];
        if (f.rt[i].base == addr_depth_z24) rt_z24 = &f.rt[i];
        if (f.rt[i].base == addr_cs_vel) rt_cs = &f.rt[i];
    }
    if (!rt_f16 || !rt_z24 || !rt_cs) return 14;
    if (!(rt_f16->flags & PM4RT_SAMPLED) || rt_f16->w != 1600 || rt_f16->h != 900) return 15;
    if (rt_f16->score < 80) return 16;

    uint32_t gtile = 0, gpitch = 0;
    if (!pm4_rt_game_tsharp(rt_f16, &gtile, &gpitch) || gtile != 16 || gpitch != 1920) return 17;
    if (!pm4_rt_game_tsharp(rt_z24, &gtile, &gpitch) || gtile != 6 || gpitch != 1920) return 18;
    if (rt_z24->w != 1920 || rt_z24->h != 1080 || rt_z24->score < 50) return 19;
    if (rt_cs->w != 1280 || rt_cs->h != 720 || rt_cs->score < 50) return 20;

    // Verify negative address caching in mem_ok: querying the same unmapped pointer again should not invoke sceKernelVirtualQuery
    uint32_t vq0 = g_vq_calls;
    if (mem_ok(0x7FF0DEAD000ull, 64, PROT_CPU_READ) != 0) return 21;
    if (g_vq_calls != vq0) return 22;

    // Verify mk_t_target for Z24 and PS4 Pro neo=1 alt_tile_mode
    uint32_t t_out[8];
    g_gv_neo = 1;
    if (!mk_t_target(t_out, addr_depth_z24, &rt_z24->d, 1920, 1080)) return 23;
    if (((t_out[1] >> 20) & 0x3Fu) != 20u || (t_out[6] & (1u << 24)) == 0) return 24;

    return 0;
}

// ---------------------------------------------------------------------------------------------------------------
// Test 2: Viewport-based DRS and PS4 Pro (neo=1) 1280x720..3840x2160 ranking
static int test_drs_and_neo_ranking(void)
{
    static uint32_t dcb[256];
    g_nvmas = 0;
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)dcb, (uintptr_t)(dcb + 256), PROT_CPU_READ | PROT_GPU_READ, 1 };
    g_mcn = 0;

    uint32_t *p = dcb;
    *p++ = PKT3(OP_SET_CONTEXT_REG, 2); *p++ = CTX_CB_COLOR_CONTROL; *p++ = 1u << 4;
    *p++ = PKT3(OP_SET_CONTEXT_REG, 3); *p++ = CTX_CB_TARGET_MASK; *p++ = 0xFu; *p++ = 0xFu;
    // 4K allocation (3840x2176 padded) with viewport-scale DRS set to 2560x1440
    p = emit_cb(p, 0, 0x200000000ull, 5, 7, 3840, 2176, 14);
    *p++ = PKT3(OP_SET_CONTEXT_REG, 5);
    *p++ = CTX_PA_CL_VPORT_XSCALE;
    *p++ = f2u(1280.0f);  // XSCALE = 2560 / 2
    *p++ = f2u(1280.0f);  // XOFFSET = 2560 / 2
    *p++ = f2u(-720.0f);  // YSCALE = -1440 / 2
    *p++ = f2u(720.0f);   // YOFFSET = 1440 / 2
    for (int i = 0; i < 30; i++) {
        *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;
    }
    // Follow with a small 64x64 scissor draw on the same target: must not clobber the 2560x1440 active area!
    p = emit_scissor(p, 64, 64);
    *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;

    static pm4_frame_t f;
    memset(&f, 0, sizeof(f));
    f.mode = PM4_FULL;
    f.readable = host_readable;
    f.clock = host_clock;
    f.budget_us = 50000;
    f.max_dwords = 100000;
    pm4_frame_begin(&f);
    pm4_call_begin(&f);
    if (pm4_walk_entry(&f, dcb, (uint32_t)(p - dcb), 0) != PM4_OK) return 30;
    if (f.nrt != 1 || f.rt[0].w != 2560 || f.rt[0].h != 1440) return 31;

    // Rank against both 3840x2160 (4K display) and 1920x1080 (Pro supersampled 1080p display)
    pm4_rank(&f, 3840, 2160);
    if (f.rt[0].score < 60) return 32;
    pm4_rank(&f, 1920, 1080);
    if (f.rt[0].score < 60) return 33;

    // Test LOAD_CONTEXT_REG + PM4_TRACK alias detection + PM4_TRACK unreadable IB abort
    static uint32_t shadow_ctx[0x400];
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)shadow_ctx, (uintptr_t)(shadow_ctx + 0x400), PROT_CPU_READ | PROT_GPU_READ, 1 };
    memset(shadow_ctx, 0, sizeof(shadow_ctx));
    uint32_t reg0 = CTX_CB_COLOR0_BASE;
    shadow_ctx[reg0 + CB_BASE] = (uint32_t)(0x210000000ull >> 8);
    shadow_ctx[reg0 + CB_PITCH] = (1920u / 8u) - 1u;
    shadow_ctx[reg0 + CB_SLICE] = (1920u * 1080u / 64u) - 1u;
    shadow_ctx[reg0 + CB_INFO] = (5u << 2) | (7u << 8);
    shadow_ctx[reg0 + CB_ATTRIB] = 14u;

    p = dcb;
    *p++ = PKT3(OP_CLEAR_STATE, 1); *p++ = 0;
    *p++ = PKT3(OP_SET_CONTEXT_REG, 2); *p++ = CTX_CB_COLOR_CONTROL; *p++ = 1u << 4;
    *p++ = PKT3(OP_SET_CONTEXT_REG, 3); *p++ = CTX_CB_TARGET_MASK; *p++ = 0xFu; *p++ = 0xFu;
    uintptr_t sh_a = (uintptr_t)shadow_ctx;
    *p++ = PKT3(OP_LOAD_CONTEXT_REG, 4);
    *p++ = (uint32_t)(sh_a & ~3u);
    *p++ = (uint32_t)(sh_a >> 32);
    *p++ = reg0;
    *p++ = 7u;
    *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;
    // Overwrite the same memory via DMA_DATA -> triggers track_alias
    *p++ = PKT3(OP_DMA_DATA, 6);
    *p++ = 2u << 29;
    *p++ = 0; *p++ = 0;
    *p++ = (uint32_t)0x210000000ull;
    *p++ = (uint32_t)(0x210000000ull >> 32);
    *p++ = 4096u;

    memset(&f, 0, sizeof(f));
    f.mode = PM4_FULL | PM4_TRACK;
    f.readable = host_readable;
    f.clock = host_clock;
    f.budget_us = 50000;
    f.max_dwords = 100000;
    f.trk_on = 1;
    f.trk_desc = (pm4_desc_t){ 0, 5, 7, 1920, 1080, 14 };
    f.trk_ordinal = 0;
    pm4_frame_begin(&f);
    pm4_call_begin(&f);
    if (pm4_walk_entry(&f, dcb, (uint32_t)(p - dcb), 0) != PM4_OK) return 34;
    if (!f.trk.written || !f.trk.aliased || f.trk.addr != 0x210000000ull) return 35;

    // Unreadable IB while tracking must abort with PM4_ABORT_UNREADABLE
    p = dcb;
    *p++ = PKT3(OP_INDIRECT_BUFFER, 3);
    *p++ = 0xDEAD0000u; *p++ = 0x0000007Fu; *p++ = 16u;
    pm4_frame_begin(&f);
    pm4_call_begin(&f);
    if (pm4_walk_entry(&f, dcb, (uint32_t)(p - dcb), 0) != PM4_ABORT_UNREADABLE) return 36;

    return 0;
}

// ---------------------------------------------------------------------------------------------------------------
// Test 3: Velocity encoding auto-calibration (UV with raw outlier spikes, UNORM bias, sentinel, and DRS pixel-space)
static void fill_synthetic_frame(gv_rec_t *rec, float fx_base, float fy_base, float ax_true, float bx_true,
                                 float ay_true, float by_true, int inject_outliers, int inject_sentinel, uint32_t sent_bits)
{
    memset(rec, 0, sizeof(gv_rec_t) * GV_NB);
    for (int by = 0; by < GV_BH; by++) {
        for (int bx = 0; bx < GV_BW; bx++) {
            int i = by * GV_BW + bx;
            gv_rec_t *r = &rec[i];
            r->flags = GVREC_GVALID;
            r->tex = 600u;
            r->sad = 50u;
            // Smooth spatial gradient across the screen so inside-frame variance is > 1.0 px^2 while neighbour diff <= 1.0 px
            float fx = fx_base + (float)(bx - 60) * 0.12f;
            float fy = fy_base + (float)(by - 34) * 0.12f;
            r->fx = fx;
            r->fy = fy;
            r->fpx = fx * 0.5f;
            r->fpy = fy * 0.5f;
            if (inject_sentinel && (i % 3 == 0)) {
                r->rx = u2f(sent_bits);
                r->ry = u2f(sent_bits);
            } else {
                r->rx = (fx - bx_true) / ax_true;
                r->ry = (fy - by_true) / ay_true;
            }
        }
    }
    if (inject_outliers) {
        // Inject 8 extreme corrupt raw spikes that would ruin an unfiltered OLS fit on UV/UNORM encodings
        for (int k = 0; k < 8; k++) {
            int idx = (k + 1) * 700;
            rec[idx].rx = 500.0f;
            rec[idx].ry = -500.0f;
        }
    }
}

static int approx(float a, float b, float tol)
{
    float d = a - b;
    if (d < 0.0f) d = -d;
    return d <= tol;
}

static int test_gvcal_encodings_and_drs(void)
{
    static gv_rec_t rec[GV_NB];
    static gvcal_t cal;
    gvcal_result_t res;

    // 3a: UV-normalized encoding (ax = -1920, ay = 1080, cur-prev, Y up) with corrupt raw outlier spikes + sentinel
    gvcal_reset(&cal);
    uint32_t sent = 0x3C003C00u;
    const float pans_x[10] = { 12.0f, -15.0f, 20.0f, -8.0f, 18.0f, -22.0f, 0.5f, 14.0f, -16.0f, 10.0f };
    const float pans_y[10] = { 6.0f, -8.0f, 4.0f, 10.0f, -12.0f, 7.0f, 0.2f, -9.0f, 11.0f, -5.0f };
    for (int f = 0; f < 10; f++) {
        fill_synthetic_frame(rec, pans_x[f], pans_y[f], -1920.0f, 0.0f, 1080.0f, 0.0f, 1, 1, sent);
        gvcal_add(&cal, rec, NULL);
    }
    if (gvcal_decide(&cal, &res) != 1) return 40;
    if (!approx(res.ax, -1920.0f, 5.0f) || !approx(res.ay, 1080.0f, 5.0f)) return 41;
    if (!res.sentinel || res.sent_x != sent || res.sent_y != sent) return 42;
    if (!strstr(res.units, "UV") || !strstr(res.units, "cur-prev") || !strstr(res.units, "Y up")) return 43;
    float frac = 0.0f; uint32_t nrel = 0;
    if (gvcal_check(&res, rec, &frac, &nrel) != 1 || frac < 0.95f) return 44;

    // 3b: RG16_UNORM biased encoding (raw in [0, 1] with 0.5 = zero motion: flow = 1920 * raw - 960, -1080 * raw + 540)
    gvcal_reset(&cal);
    for (int f = 0; f < 10; f++) {
        fill_synthetic_frame(rec, pans_x[f], pans_y[f], 1920.0f, -960.0f, -1080.0f, 540.0f, 1, 0, 0);
        gvcal_add(&cal, rec, NULL);
    }
    if (gvcal_decide(&cal, &res) != 1) return 45;
    if (!approx(res.ax, 1920.0f, 5.0f) || !approx(res.bx, -960.0f, 5.0f)) return 46;
    if (!approx(res.ay, -1080.0f, 5.0f) || !approx(res.by, 540.0f, 5.0f)) return 47;
    if (res.jit_x > 0.05f || res.jit_y > 0.05f) return 48; // no false jitter from float cancellation on large intercept
    if (!strstr(res.units, "UNORM")) return 49;

    // 3c: Dynamic-Resolution Scaling (DRS) pixel-space velocity buffer where resolution changes every frame
    // Frame resolutions step across 1920x1080 (100%), 1536x864 (80%), 1440x810 (75%), 1280x720 (66.7%)
    gvcal_reset(&cal);
    const uint32_t drs_w[10] = { 1920, 1536, 1440, 1280, 1600, 1920, 1440, 1280, 1536, 1728 };
    const uint32_t drs_h[10] = { 1080,  864,  810,  720,  900, 1080,  810,  720,  864,  972 };
    for (int f = 0; f < 10; f++) {
        float sx = 1920.0f / (float)drs_w[f];
        float sy = 1080.0f / (float)drs_h[f];
        gvcal_set_res(&cal, drs_w[f], drs_h[f]);
        fill_synthetic_frame(rec, pans_x[f], pans_y[f], sx, 0.0f, sy, 0.0f, 0, 0, 0);
        gvcal_add(&cal, rec, NULL);
    }
    if (gvcal_decide(&cal, &res) != 1) return 50;
    if (!res.drs_scale || res.cal_w != 1920 || res.cal_h != 1080) return 51;
    if (!approx(res.ax, 1.0f, 0.03f) || !approx(res.ay, 1.0f, 0.03f)) return 52;

    return 0;
}

int main(void)
{
    int rc = test_pm4_traversal_and_safety();
    if (rc != 0) { printf("test_pm4_traversal_and_safety failed: %d\n", rc); return rc; }
    rc = test_drs_and_neo_ranking();
    if (rc != 0) { printf("test_drs_and_neo_ranking failed: %d\n", rc); return rc; }
    rc = test_gvcal_encodings_and_drs();
    if (rc != 0) { printf("test_gvcal_encodings_and_drs failed: %d\n", rc); return rc; }
    return 0;
}
'''
)

test_c.write_text(c_code, encoding="utf-8")

subprocess.run(
    [
        str(llvm / "clang.exe"),
        "--target=x86_64-pc-windows-msvc",
        "-O0",
        "-fno-builtin",
        "-emit-llvm",
        "-I",
        str(out),
        "-I",
        str(ROOT / "classic/source"),
        "-c",
        str(test_c),
        "-o",
        str(test_bc),
    ],
    check=True,
)
subprocess.run([str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(test_bc)], check=True)
print("gv scanner & auto-calibration host tests: PM4 IB/EUD safety, DRS/Neo ranking, and UV/UNORM/DRS calibration passed")
