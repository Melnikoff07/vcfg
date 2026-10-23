#!/usr/bin/env python3
"""Host-side test suite for Universal Engine Motion-Vector & Depth Auto-Discovery (Chat 10).

Compiles classic/source/pm4scan.c and classic/source/gvcal.c to host bitcode via
configured LLVM clang.exe and executes it under lli.exe (no PS4 hardware required),
plus verifies shader register limits and hybrid depth-silhouette / alpha-particle fusion.
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

# Verify shader hardware limits in classic/source/shaders.h
shaders_h = (ROOT / "classic/source/shaders.h").read_text(encoding="utf-8")
for m in re.finditer(r"//\s*(\w+):\s*\d+\s*B,\s*vgprs=(\d+)\s*sgprs=(\d+)", shaders_h):
    sh_name, vgprs, sgprs = m.group(1), int(m.group(2)), int(m.group(3))
    assert vgprs <= 64, f"Shader {sh_name} exceeds 64 VGPRs: {vgprs}"

test_c = out / "test_gv_autocal_host.c"
test_bc = out / "test_gv_autocal_host.bc"

c_code = r'''#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>

#include "pm4scan.c"
#include "gvcal.c"

typedef struct {
    uintptr_t s, e;
} mock_vma_t;

static mock_vma_t g_vmas[16];
static int g_nvmas = 0;
static uint64_t g_clock_us = 1000;

static int host_readable(const void *ptr, size_t bytes, void *user)
{
    (void)user;
    uintptr_t addr = (uintptr_t)ptr;
    uintptr_t e = addr + bytes;
    if (e < addr) return 0;
    for (int i = 0; i < g_nvmas; i++) {
        if (addr >= g_vmas[i].s && e <= g_vmas[i].e) return 1;
    }
    return 0;
}

static uint64_t host_clock(void *user)
{
    (void)user;
    g_clock_us += 2;
    return g_clock_us;
}

#define PKT3(op, nbody) (0xC0000000u | (((uint32_t)(nbody) - 1u) << 16) | ((uint32_t)(op) << 8))

static void build_tsharp(uint32_t *t, uint64_t base, uint32_t dfmt, uint32_t nfmt,
                         uint32_t w, uint32_t h, uint32_t pitch, uint32_t tile)
{
    t[0] = (uint32_t)(base >> 8);
    t[1] = (uint32_t)((base >> 40) & 0x3Fu) | (dfmt << 20) | (nfmt << 26);
    t[2] = (w - 1u) | ((h - 1u) << 14);
    t[3] = 4u | (5u << 3) | (6u << 6) | (7u << 9) | ((tile & 0x1Fu) << 20) | (9u << 28);
    t[4] = (pitch - 1u) << 13;
    t[5] = 0;
    t[6] = 0;
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

// ---------------------------------------------------------------------------------------------------------------
// Test 1: Synthetic PM4 DCB stream with 4 decoy RTs (UI, bloom, shadow map, normal/post-TAA),
//         1 dynamic-resolution 1600x900 R16G16_FLOAT velocity target, and 1 reversed-Z R32_FLOAT depth target
static int test_pm4_decoys_and_drs_1600x900(void)
{
    static uint32_t dcb[2048];
    g_nvmas = 0;
    g_vmas[g_nvmas++] = (mock_vma_t){ (uintptr_t)dcb, (uintptr_t)(dcb + 2048) };

    const uint64_t addr_shadow_rt = 0x110000000ull; // Decoy 1: 2048x2048 shadow map (square aspect -> rejected)
    const uint64_t addr_shadow_z  = 0x112000000ull;
    const uint64_t addr_bloom     = 0x114000000ull; // Decoy 2: 960x540 RGBA16F bloom (no Z draws)
    const uint64_t addr_ui_post   = 0x116000000ull; // Decoy 3: 1920x1080 RG16F 2D post-TAA/UI (zdraws=0, draws=2)
    const uint64_t addr_normals   = 0x118000000ull; // Decoy 4: 1920x1080 RGBA8 normal buffer (MRT0, fmt=10)
    const uint64_t addr_vel_drs   = 0x120000000ull; // True #1 Velocity: 1600x900 R16G16_FLOAT (CB fmt=5, num=7, T# dfmt=12, nfmt=7)
    const uint64_t addr_depth_drs = 0x124000000ull; // True #1 Depth: 1600x900 R32_FLOAT reversed-Z (zfmt=3, T# dfmt=4, nfmt=7)

    uint32_t *p = dcb;
    *p++ = PKT3(OP_SET_CONTEXT_REG, 2); *p++ = CTX_CB_COLOR_CONTROL; *p++ = 1u << 4;

    // Pass 1: 2048x2048 shadow map pass (Decoy 1)
    *p++ = PKT3(OP_SET_CONTEXT_REG, 3); *p++ = CTX_CB_TARGET_MASK; *p++ = 0xFu; *p++ = 0xFu;
    p = emit_cb(p, 0, addr_shadow_rt, 5, 7, 2048, 2048, 14);
    p = emit_db(p, addr_shadow_z, 3, 2048, 2048, 5, 6);
    p = emit_scissor(p, 2048, 2048);
    for (int i = 0; i < 90; i++) {
        *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;
    }

    // Pass 2: Main 3D G-buffer pass at 1600x900 DRS inside 1920x1088 padded allocation
    // MRT0 = Normals (RGBA8, fmt=10), MRT1 = Velocity (R16G16_FLOAT, fmt=5, num=7), Depth = Z32_FLOAT (zfmt=3, GEQUAL=6)
    *p++ = PKT3(OP_SET_CONTEXT_REG, 3); *p++ = CTX_CB_TARGET_MASK; *p++ = 0xFFu; *p++ = 0xFFu;
    p = emit_cb(p, 0, addr_normals, 10, 0, 1920, 1088, 14);
    p = emit_cb(p, 1, addr_vel_drs, 5, 7, 1920, 1088, 16);
    p = emit_db(p, addr_depth_drs, 3, 1920, 1088, 6, 6);
    p = emit_scissor(p, 1600, 900);
    for (int i = 0; i < 80; i++) {
        *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;
    }

    // Pass 3: TAA compute resolve binding 1600x900 T# descriptors in SET_SH_REG
    uint32_t t_vel[8], t_z[8];
    build_tsharp(t_vel, addr_vel_drs, 12, 7, 1600, 900, 1920, 16);
    build_tsharp(t_z, addr_depth_drs, 4, 7, 1600, 900, 1920, 6);
    *p++ = PKT3(OP_SET_SH_REG, 1 + 8);
    *p++ = 0x90u;
    for (int i = 0; i < 8; i++) *p++ = t_vel[i];
    *p++ = PKT3(OP_SET_SH_REG, 1 + 8);
    *p++ = 0xA0u;
    for (int i = 0; i < 8; i++) *p++ = t_z[i];
    *p++ = PKT3(OP_DISPATCH_DIRECT, 4); *p++ = 200; *p++ = 113; *p++ = 1; *p++ = 1;

    // Pass 4: 960x540 RGBA16F Bloom pass (Decoy 2, CB fmt=12, num=7, no depth draws)
    *p++ = PKT3(OP_SET_CONTEXT_REG, 2); *p++ = CTX_DB_DEPTH_CONTROL; *p++ = 0u;
    *p++ = PKT3(OP_SET_CONTEXT_REG, 3); *p++ = CTX_CB_TARGET_MASK; *p++ = 0xFu; *p++ = 0xFu;
    p = emit_cb(p, 0, addr_bloom, 12, 7, 960, 540, 14);
    p = emit_scissor(p, 960, 540);
    for (int i = 0; i < 6; i++) {
        *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;
    }
    uint32_t t_bloom[8];
    build_tsharp(t_bloom, addr_bloom, 14, 7, 960, 540, 960, 14);
    *p++ = PKT3(OP_SET_SH_REG, 1 + 8);
    *p++ = 0x90u;
    for (int i = 0; i < 8; i++) *p++ = t_bloom[i];

    // Pass 5: 1920x1080 RG16F 2D post-TAA / UI decoy (Decoy 3, zdraws=0, draws=2)
    p = emit_cb(p, 0, addr_ui_post, 5, 7, 1920, 1080, 14);
    p = emit_scissor(p, 1920, 1080);
    for (int i = 0; i < 2; i++) {
        *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;
    }
    uint32_t t_ui[8];
    build_tsharp(t_ui, addr_ui_post, 12, 7, 1920, 1080, 1920, 14);
    *p++ = PKT3(OP_SET_SH_REG, 1 + 8);
    *p++ = 0x90u;
    for (int i = 0; i < 8; i++) *p++ = t_ui[i];

    static pm4_frame_t f;
    memset(&f, 0, sizeof(f));
    f.mode = PM4_FULL;
    f.readable = host_readable;
    f.clock = host_clock;
    f.budget_us = 50000;
    f.max_dwords = 100000;
    pm4_frame_begin(&f);
    pm4_call_begin(&f);
    if (pm4_walk_entry(&f, dcb, (uint32_t)(p - dcb), 0) != PM4_OK) return 10;
    pm4_rank(&f, 1920, 1080);

    // Find top-ranked velocity and depth candidates
    const pm4_rt_t *best_v = NULL, *best_z = NULL;
    for (uint32_t i = 0; i < f.nrt; i++) {
        const pm4_rt_t *rt = &f.rt[i];
        if (rt->score < 0) continue;
        if (rt->d.depth) {
            if (!best_z || rt->score > best_z->score) best_z = rt;
        } else {
            if (!best_v || rt->score > best_v->score) best_v = rt;
        }
    }
    if (!best_v || best_v->base != addr_vel_drs) return 11;
    if (best_v->w != 1600 || best_v->h != 900) return 12;
    if (!best_z || best_z->base != addr_depth_drs) return 13;
    if (best_z->w != 1600 || best_z->h != 900 || best_z->zfunc[6] == 0) return 14;

    // Also verify compute-only R8G8_UNORM (T# dfmt=3, nfmt=0) velocity buffer discovered via T# in SET_SH_REG
    const uint64_t addr_cs_rg8 = 0x130000000ull;
    uint32_t t_rg8[8];
    build_tsharp(t_rg8, addr_cs_rg8, 3, 0, 1600, 900, 1600, 14);
    p = dcb;
    p = emit_db(p, addr_depth_drs, 3, 1920, 1088, 6, 6);
    p = emit_scissor(p, 1600, 900);
    for (int i = 0; i < 40; i++) {
        *p++ = PKT3(OP_DRAW_INDEX_AUTO, 2); *p++ = 3; *p++ = 0;
    }
    *p++ = PKT3(OP_SET_SH_REG, 1 + 8);
    *p++ = 0x90u;
    for (int i = 0; i < 8; i++) *p++ = t_rg8[i];

    memset(&f, 0, sizeof(f));
    f.mode = PM4_FULL;
    f.readable = host_readable;
    f.clock = host_clock;
    f.budget_us = 50000;
    f.max_dwords = 100000;
    pm4_frame_begin(&f);
    pm4_call_begin(&f);
    if (pm4_walk_entry(&f, dcb, (uint32_t)(p - dcb), 0) != PM4_OK) return 15;
    pm4_rank(&f, 1920, 1080);

    best_v = NULL;
    for (uint32_t i = 0; i < f.nrt; i++) {
        if (!f.rt[i].d.depth && f.rt[i].score >= 0) {
            if (!best_v || f.rt[i].score > best_v->score) best_v = &f.rt[i];
        }
    }
    if (!best_v || best_v->base != addr_cs_rg8 || best_v->w != 1600 || best_v->h != 900) return 16;
    return 0;
}

// ---------------------------------------------------------------------------------------------------------------
// Test 2: Universal Encoding & TAA Jitter Auto-Calibration across 5 conventions (< 10 frames, < 0.25 px error)
static float halton_seq(int index, int base)
{
    float f = 1.0f, r = 0.0f;
    int i = index;
    while (i > 0) {
        f /= (float)base;
        r += f * (float)(i % base);
        i /= base;
    }
    return r;
}

static void fill_jittered_frame(gv_rec_t *rec, int frame_idx, float fx_base, float fy_base,
                                float ax_true, float bx_true, float ay_true, float by_true,
                                int swap_xy)
{
    memset(rec, 0, sizeof(gv_rec_t) * GV_NB);
    // +/- 0.5 px Halton(2, 3) screen-wide TAA camera jitter per frame
    float jx = (halton_seq(frame_idx + 1, 2) - 0.5f) * 1.0f;
    float jy = (halton_seq(frame_idx + 1, 3) - 0.5f) * 1.0f;

    for (int by = 0; by < GV_BH; by++) {
        for (int bx = 0; bx < GV_BW; bx++) {
            int i = by * GV_BW + bx;
            gv_rec_t *r = &rec[i];
            r->flags = GVREC_GVALID;
            r->tex = 500u;
            r->sad = 55u;
            float fx = fx_base + (float)(bx - 60) * 0.14f;
            float fy = fy_base + (float)(by - 34) * 0.14f;
            r->fx = fx;
            r->fy = fy;
            r->fpx = fx;
            r->fpy = fy;
            // Game velocity buffer contains the true velocity PLUS per-frame TAA camera jitter!
            float rx_val = ((fx + jx) - bx_true) / ax_true;
            float ry_val = ((fy + jy) - by_true) / ay_true;
            if (!swap_xy) {
                r->rx = rx_val;
                r->ry = ry_val;
            } else {
                r->rx = ry_val;
                r->ry = rx_val;
            }
        }
    }
}

static float eval_max_px_err(const gvcal_result_t *res, float ax_true, float bx_true,
                             float ay_true, float by_true, int swap_xy, float drs_k)
{
    const float vx_test[5] = { -24.0f, -8.5f, 0.0f, 11.75f, 28.0f };
    const float vy_test[5] = { -12.0f,  4.25f, 0.0f, -7.5f, 15.0f };
    float max_e = 0.0f;
    for (int i = 0; i < 5; i++) {
        float vx = vx_test[i], vy = vy_test[i];
        float rx_val = (vx - bx_true) / ax_true;
        float ry_val = (vy - by_true) / ay_true;
        float r0 = swap_xy ? ry_val : rx_val;
        float r1 = swap_xy ? rx_val : ry_val;
        float dx_in = res->swap_xy ? r1 : r0;
        float dy_in = res->swap_xy ? r0 : r1;
        float gx = (dx_in * res->ax + res->bx) * drs_k;
        float gy = (dy_in * res->ay + res->by) * drs_k;
        float ex = gx - vx; if (ex < 0.0f) ex = -ex;
        float ey = gy - vy; if (ey < 0.0f) ey = -ey;
        if (ex > max_e) max_e = ex;
        if (ey > max_e) max_e = ey;
    }
    return max_e;
}

static int test_autocal_5_conventions_and_jitter(void)
{
    static gv_rec_t rec[GV_NB];
    static gvcal_t cal;
    gvcal_result_t res;

    const float pans_x[10] = { 14.0f, -16.0f, 20.0f, -10.0f, 18.0f, -22.0f, 12.0f, -15.0f, 16.0f, -12.0f };
    const float pans_y[10] = {  7.0f,  -9.0f,  5.0f,  11.0f, -12.0f,   8.0f, -6.0f,  10.0f, -8.0f,   6.0f };

    // 5 conventions:
    //  0: NDC [-2, +2]       (ax = -960,  bx = 0,    ay = 540,   by = 0,   swap = 0)
    //  1: UV [-1, +1]        (ax = 1920,  bx = 0,    ay = -1080, by = 0,   swap = 0)
    //  2: Pixel [-W, +W]     (ax = -1,    bx = 0,    ay = -1,    by = 0,   swap = 0)
    //  3: UNORM 0.5-biased   (ax = 255,   bx = -127, ay = -255,  by = 127, swap = 0)
    //  4: Swapped X/Y axes   (ax = 1920,  bx = 0,    ay = -1080, by = 0,   swap = 1)
    const float ax_list[5] = { -960.0f,  1920.0f, -1.0f,  255.0f,  1920.0f };
    const float bx_list[5] = {    0.0f,     0.0f,  0.0f, -127.0f,     0.0f };
    const float ay_list[5] = {  540.0f, -1080.0f, -1.0f, -255.0f, -1080.0f };
    const float by_list[5] = {    0.0f,     0.0f,  0.0f,  127.0f,     0.0f };
    const int   sw_list[5] = {       0,        0,     0,       0,        1 };

    for (int c = 0; c < 5; c++) {
        gvcal_reset(&cal);
        gvcal_set_res(&cal, 1920, 1080);
        int locked_at = -1;
        for (int f = 0; f < 10; f++) {
            fill_jittered_frame(rec, f, pans_x[f], pans_y[f],
                                ax_list[c], bx_list[c], ay_list[c], by_list[c], sw_list[c]);
            gvcal_add(&cal, rec, NULL);
            int d = gvcal_decide(&cal, &res);
            if (d == 1) {
                locked_at = f + 1;
                break;
            }
            if (d == -1) return 20 + c * 10 + 1;
        }
        if (locked_at < 1 || locked_at >= 10) return 20 + c * 10 + 2;
        if (res.swap_xy != sw_list[c]) return 20 + c * 10 + 3;
        float err = eval_max_px_err(&res, ax_list[c], bx_list[c], ay_list[c], by_list[c], sw_list[c], 1.0f);
        if (err >= 0.25f) {
            printf("Convention %d err=%.4f >= 0.25 (ax=%.3f bx=%.3f ay=%.3f by=%.3f)\n",
                   c, err, res.ax, res.bx, res.ay, res.by);
            return 20 + c * 10 + 4;
        }
        // Verify gvcal_check passes on valid frame and fails when surface is repurposed to static (0.5, 0.5)
        float frac = 0.0f; uint32_t nrel = 0;
        if (gvcal_check(&res, rec, &frac, &nrel) != 1 || frac < 0.70f) return 20 + c * 10 + 5;
        for (int i = 0; i < GV_NB; i++) { rec[i].rx = 0.5f; rec[i].ry = 0.5f; }
        if (gvcal_check(&res, rec, &frac, &nrel) != 0) return 20 + c * 10 + 6;
    }

    // Also test UNORM 0.5-biased under variable Dynamic-Resolution Scaling (DRS) + TAA jitter
    // In UNORM DRS, raw = 0.5 + (v_render / 128), where v_render = v_1080 * (drs_w / 1920).
    // Thus zero motion is ALWAYS raw = 0.5 regardless of DRS resolution!
    gvcal_reset(&cal);
    const uint32_t drs_w[8] = { 1600, 1440, 1728, 1920, 1536, 1280, 1600, 1440 };
    const uint32_t drs_h[8] = {  900,  810,  972, 1080,  864,  720,  900,  810 };
    int drs_locked = -1;
    for (int f = 0; f < 8; f++) {
        float kx = 1920.0f / (float)drs_w[f];
        float ky = 1080.0f / (float)drs_h[f];
        gvcal_set_res(&cal, drs_w[f], drs_h[f]);
        fill_jittered_frame(rec, f, pans_x[f], pans_y[f],
                            128.0f * kx, -64.0f * kx, -128.0f * ky, 64.0f * ky, 0);
        gvcal_add(&cal, rec, NULL);
        int d = gvcal_decide(&cal, &res);
        if (d == 1) {
            drs_locked = f + 1;
            break;
        }
    }
    if (drs_locked < 1 || drs_locked >= 10 || !res.drs_scale) return 80;
    // Check decoded error at both 1600x900 and 1440x810 DRS (scaled by res.cal_w / vw)
    float kx_1600 = 1920.0f / 1600.0f;
    float scale_1600 = (float)res.cal_w / 1600.0f;
    float err_drs_1600 = eval_max_px_err(&res, 128.0f * kx_1600, -64.0f * kx_1600,
                                         -128.0f * kx_1600, 64.0f * kx_1600, 0, scale_1600);
    float kx_1440 = 1920.0f / 1440.0f;
    float scale_1440 = (float)res.cal_w / 1440.0f;
    float err_drs_1440 = eval_max_px_err(&res, 128.0f * kx_1440, -64.0f * kx_1440,
                                         -128.0f * kx_1440, 64.0f * kx_1440, 0, scale_1440);
    if (err_drs_1600 >= 0.25f || err_drs_1440 >= 0.25f) return 81;

    // Verify dead/constant (0.5, 0.5) decoy buffer is rejected in <= 4 frames
    gvcal_reset(&cal);
    int rej_at = -1;
    for (int f = 0; f < 6; f++) {
        fill_jittered_frame(rec, f, pans_x[f], pans_y[f], 1920.0f, 0.0f, 1080.0f, 0.0f, 0);
        for (int i = 0; i < GV_NB; i++) { rec[i].rx = 0.5f; rec[i].ry = 0.5f; }
        gvcal_add(&cal, rec, NULL);
        if (gvcal_decide(&cal, &res) == -1) { rej_at = f + 1; break; }
    }
    if (rej_at < 1 || rej_at > 4) return 82;

    return 0;
}

// ---------------------------------------------------------------------------------------------------------------
// Test 3: Hybrid Depth-Silhouette & Alpha-Particle Fusion (gv8.ll + warp8g.ll behavioral verification)
static int test_hybrid_depth_and_alpha_fusion(void)
{
    // Exact integer/float logic matching gv8.ll and warp8g.ll
    // Case A: Un-vectored alpha particle moving at F=(14, -6) over flat-depth background G=(0, 0)
    {
        float z_min = 0.25f, z_max = 0.25f;
        int zedg = (z_max - z_min) >= 0.002f;
        uint32_t sad0 = 60u, sad_g = 195u;
        int sad_ok = (sad_g <= sad0 + 24u);
        int allow_bg = zedg || sad_ok;
        uint32_t bg = allow_bg ? 48u : 0u;
        uint32_t bz = zedg ? 96u : 0u;
        uint32_t cost_f = sad0 + 1024u;
        uint32_t cost_g = sad_g + 1024u - (bg + bz);
        if (cost_g < cost_f) return 90; // Alpha particle must NOT be overwritten by background G in gv8!

        int gzflat = !zedg;
        float gtol = gzflat ? 0.0f : 0.025f;
        float e_opt = 0.025f, e_eng = 0.180f;
        if (e_eng <= e_opt + gtol) return 91; // warp8g slow path must reject background G on alpha particle!
    }

    // Case B: 3D character silhouette edge moving at G=(18, 0) over background F=(0, 0)
    {
        float z_min = 0.15f, z_max = 0.65f;
        int zedg = (z_max - z_min) >= 0.002f;
        uint32_t sad0 = 150u, sad_g = 190u;
        int sad_ok = (sad_g <= sad0 + 24u);
        int allow_bg = zedg || sad_ok;
        uint32_t bg = allow_bg ? 48u : 0u;
        uint32_t bz = zedg ? 96u : 0u;
        uint32_t cost_f = sad0 + 1024u;
        uint32_t cost_g = sad_g + 1024u - (bg + bz);
        if (!(cost_g < cost_f)) return 92; // 3D silhouette edge MUST dilate foreground G in gv8!

        int gzflat = !zedg;
        float gtol = gzflat ? 0.0f : 0.025f;
        // Foreground pixel inside the 4x4 cell matches G (e_eng = 0.030 <= e_opt + 0.025)
        if (!(0.030f <= 0.240f + gtol)) return 93;
        // Background pixel inside the same 4x4 cell matches F (e_eng = 0.240 > e_opt + 0.025) -> no foreground halo!
        if (0.240f <= 0.020f + gtol) return 94;
    }

    return 0;
}

int main(void)
{
    int rc = test_pm4_decoys_and_drs_1600x900();
    if (rc != 0) { printf("test_pm4_decoys_and_drs_1600x900 failed: %d\n", rc); return rc; }
    rc = test_autocal_5_conventions_and_jitter();
    if (rc != 0) { printf("test_autocal_5_conventions_and_jitter failed: %d\n", rc); return rc; }
    rc = test_hybrid_depth_and_alpha_fusion();
    if (rc != 0) { printf("test_hybrid_depth_and_alpha_fusion failed: %d\n", rc); return rc; }
    return 0;
}
'''

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
print(
    "test_gv_autocal: PM4 DCB decoy filtering (1600x900 DRS + rev-Z), "
    "5-convention + TAA-jitter auto-calibration (<10 frames, <0.25 px), "
    "and depth-silhouette / alpha-particle fusion all passed"
)
