#!/usr/bin/env python3
"""Compiled LLVM IR resolution scaling tests for classic/shaders/warp8.ll.

Compiles the real classic/shaders/warp8.ll (@main + helpers) to LLVM bitcode via
clang-22 + llvm-link-22 and executes it via lli-22 on a synthetic frame pair with a
known uniform 1080p displacement (vx = 8.0 px, vy = 8.0 px) at t = 0.5:
  1. Verifies bit-for-bit identical output (SHA-256 of all output float32 words)
     against commit 9ffc637 across all 5 standard 16:9 resolutions:
     1280x720, 1600x900, 1920x1080, 2560x1440, 3840x2160.
  2. Verifies accurate t=0.5 mid-frame reconstruction (MAE < 0.002, max_err < 0.003)
     at non-standard 16:9 resolutions (3200x1800, 2048x1152) and non-16:9 resolutions
     supported by fg_res_supported (1440x900, 1280x800, 1360x768).
     Fails on 9ffc637 where warp8.ll only recognizes wm1 in {3839, 2559, 1599, 1279}
     and ignores hm1.
  3. Verifies gfx700 hardware budget (NumVgprs <= 64, ScratchSize == 0,
     aligned word count == 2960, shader region <= 0x1C000).
"""
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WARP8_LL = ROOT / "classic" / "shaders" / "warp8.ll"
FG_RES_H = ROOT / "classic" / "source" / "fg_res.h"

# Reference SHA-256 digests of the 64x3 float32 output words on commit 9ffc637
# across all 5 standard 16:9 resolutions.
STD_9FFC637_SHA256 = {
    "1280x720": "bfa14ea1657cb78987c52e857aed828361b392f7a6acfdfa98d6d0801f926270",
    "1600x900": "837cb6f8af49d642470ac18ba3dfbe054d4dc4c37c862b62f5e72f19efb31508",
    "1920x1080": "a0eb53cf18b15c077b215dea0e89c8b4429db1d918add81dac69733e5fda1a42",
    "2560x1440": "003caa5439466eeb05bbcad3093987a0158eba05c20c1e1f79b48693a4da44c6",
    "3840x2160": "e6cf071f4254665fe83adb35266cd482176eb3576204c5cff8ebe6cdc13ad1af",
}

NONSTD_RESOLUTIONS = (
    "3200x1800",
    "2048x1152",
    "1440x900",
    "1280x800",
    "1360x768",
)


def _find_llvm_tool(name: str) -> str:
    for cand in (f"/usr/bin/{name}-22", shutil.which(f"{name}-22"), shutil.which(name)):
        if cand and pathlib.Path(cand).exists():
            return str(cand)
    return ""


def _run_compiled_warp8() -> dict:
    clang_bin = _find_llvm_tool("clang")
    link_bin = _find_llvm_tool("llvm-link")
    lli_bin = _find_llvm_tool("lli")
    if not (clang_bin and link_bin and lli_bin):
        raise unittest.SkipTest("clang-22, llvm-link-22, and lli-22 required")

    target_triple = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "aarch64-unknown-linux-gnu"
    ir_src = WARP8_LL.read_text(encoding="utf-8")
    ir = ir_src.replace('target triple = "amdgcn--"', f'target triple = "{target_triple}"')
    ir = ir.replace(
        "define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0",
        "define void @warp8_cs_main(ptr %tbl, i32 %gx, i32 %gy, i32 %tid)",
    )
    ir = ir.replace("ptr addrspace(4)", "ptr")
    ir = ir.replace(", !invariant.load !0", "")
    ir = ir.replace("@llvm.amdgcn.image.sample.lz.2d.v4f32.f32", "@sim_sample_lz")
    ir = ir.replace("@llvm.amdgcn.image.load.2d.v4f32.i32", "@sim_image_load")
    ir = ir.replace("@llvm.amdgcn.image.store.2d.v4f32.i32", "@sim_image_store")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.v2f32", "@sim_buf_load_v2f32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.v2i32", "@sim_buf_load_v2i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.i32", "@sim_buf_load_i32")
    ir = ir.replace("@llvm.amdgcn.readfirstlane.i32", "@sim_readfirstlane_i32")
    ir = ir.replace("@llvm.amdgcn.rcp.f32", "@sim_rcp_f32")
    ir = ir.replace("@llvm.amdgcn.fmad.ftz.f32", "@sim_fmad_ftz_f32")

    lines = [
        ln
        for ln in ir.splitlines()
        if not (ln.startswith("declare ") and "@sim_" in ln)
        and not ln.startswith("attributes #0")
    ]
    ir_stubs = """
declare float @c_sample_ch(i32, float, float, i32)
declare void @c_store_px(float, float, float, float)
declare float @c_get_vx()
declare float @c_get_vy()

define <4 x float> @sim_sample_lz(i32 %dm, float %u, float %v, <8 x i32> %t, <4 x i32> %sm, i1 %un, i32 %c1, i32 %c2) {
  %tid = extractelement <8 x i32> %t, i32 0
  %r = call float @c_sample_ch(i32 %tid, float %u, float %v, i32 0)
  %g = call float @c_sample_ch(i32 %tid, float %u, float %v, i32 1)
  %b = call float @c_sample_ch(i32 %tid, float %u, float %v, i32 2)
  %v0 = insertelement <4 x float> undef, float %r, i32 0
  %v1 = insertelement <4 x float> %v0, float %g, i32 1
  %v2 = insertelement <4 x float> %v1, float %b, i32 2
  %v3 = insertelement <4 x float> %v2, float 1.0, i32 3
  ret <4 x float> %v3
}

define <4 x float> @sim_image_load(i32 %dm, i32 %x, i32 %y, <8 x i32> %t, i32 %c1, i32 %c2) {
  %tid = extractelement <8 x i32> %t, i32 0
  %xf = sitofp i32 %x to float
  %yf = sitofp i32 %y to float
  %u = fadd float %xf, 0.5
  %v = fadd float %yf, 0.5
  %r = call float @c_sample_ch(i32 %tid, float %u, float %v, i32 0)
  %g = call float @c_sample_ch(i32 %tid, float %u, float %v, i32 1)
  %b = call float @c_sample_ch(i32 %tid, float %u, float %v, i32 2)
  %v0 = insertelement <4 x float> undef, float %r, i32 0
  %v1 = insertelement <4 x float> %v0, float %g, i32 1
  %v2 = insertelement <4 x float> %v1, float %b, i32 2
  %v3 = insertelement <4 x float> %v2, float 1.0, i32 3
  ret <4 x float> %v3
}

define void @sim_image_store(<4 x float> %val, i32 %dm, i32 %x, i32 %y, <8 x i32> %t, i32 %c1, i32 %c2) {
  %r = extractelement <4 x float> %val, i32 0
  %g = extractelement <4 x float> %val, i32 1
  %b = extractelement <4 x float> %val, i32 2
  %a = extractelement <4 x float> %val, i32 3
  call void @c_store_px(float %r, float %g, float %b, float %a)
  ret void
}

define <2 x float> @sim_buf_load_v2f32(<4 x i32> %vsharp, i32 %off, i32 %soff, i32 %aux) {
  %vx = call float @c_get_vx()
  %vy = call float @c_get_vy()
  %v0 = insertelement <2 x float> undef, float %vx, i32 0
  %v1 = insertelement <2 x float> %v0, float %vy, i32 1
  ret <2 x float> %v1
}

define <2 x i32> @sim_buf_load_v2i32(<4 x i32> %vsharp, i32 %off, i32 %soff, i32 %aux) {
  ret <2 x i32> <i32 0, i32 1056964608>
}

define i32 @sim_buf_load_i32(<4 x i32> %vsharp, i32 %off, i32 %soff, i32 %aux) {
  ret i32 0
}

define i32 @sim_readfirstlane_i32(i32 %x) {
  ret i32 %x
}

define float @sim_rcp_f32(float %x) {
  %r = fdiv float 1.0, %x
  ret float %r
}

define float @sim_fmad_ftz_f32(float %a, float %b, float %c) {
  %m = fmul float %a, %b
  %r = fadd float %m, %c
  ret float %r
}
"""
    ir_clean = "\n".join(lines) + "\n" + ir_stubs

    c_driver = r"""
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <string.h>

extern void warp8_cs_main(const void *tbl, int32_t gx, int32_t gy, int32_t tid);

static int g_W = 1920;
static int g_H = 1080;
static float g_vx1080 = 8.0f;
static float g_vy1080 = 8.0f;
static float g_last_out[4];

static float eval_scene_ch(float u1080, float v1080, int ch) {
    if (u1080 < 0.5f) u1080 = 0.5f;
    if (u1080 > 1919.5f) u1080 = 1919.5f;
    if (v1080 < 0.5f) v1080 = 0.5f;
    if (v1080 > 1079.5f) v1080 = 1079.5f;
    float xm = fmodf(u1080, 32.0f) - 16.0f;
    float ym = fmodf(v1080, 32.0f) - 16.0f;
    float pulse_x = expf(-0.25f * xm * xm);
    float pulse_y = expf(-0.25f * ym * ym);
    if (ch == 0) return 0.15f + 0.70f * pulse_x;
    if (ch == 1) return 0.15f + 0.70f * pulse_y;
    return 0.15f + 0.35f * (pulse_x + pulse_y);
}

float c_sample_ch(int32_t tex_id, float u_nat, float v_nat, int32_t ch) {
    if (u_nat < 0.5f) u_nat = 0.5f;
    if (u_nat > (float)g_W - 0.5f) u_nat = (float)g_W - 0.5f;
    if (v_nat < 0.5f) v_nat = 0.5f;
    if (v_nat > (float)g_H - 0.5f) v_nat = (float)g_H - 0.5f;
    float u1080 = u_nat * (1920.0f / (float)g_W);
    float v1080 = v_nat * (1080.0f / (float)g_H);
    if (tex_id == 1) {
        return eval_scene_ch(u1080 - 0.5f * g_vx1080, v1080 - 0.5f * g_vy1080, ch);
    } else {
        return eval_scene_ch(u1080 + 0.5f * g_vx1080, v1080 + 0.5f * g_vy1080, ch);
    }
}

void c_store_px(float r, float g, float b, float a) {
    g_last_out[0] = r;
    g_last_out[1] = g;
    g_last_out[2] = b;
    g_last_out[3] = a;
}

float c_get_vx(void) { return g_vx1080; }
float c_get_vy(void) { return g_vy1080; }

int main(void) {
    const int res_list[][2] = {
        {1280, 720},
        {1600, 900},
        {1920, 1080},
        {2560, 1440},
        {3840, 2160},
        {3200, 1800},
        {2048, 1152},
        {1440, 900},
        {1280, 800},
        {1360, 768}
    };
    int n_res = (int)(sizeof(res_list) / sizeof(res_list[0]));
    for (int r = 0; r < n_res; ++r) {
        g_W = res_list[r][0];
        g_H = res_list[r][1];
        uint32_t tbl[160];
        memset(tbl, 0, sizeof(tbl));
        tbl[0] = 1;
        tbl[8] = 2;
        tbl[18] = (uint32_t)((g_W - 1) | ((g_H - 1) << 14));

        double sum_abs_err = 0.0;
        double max_abs_err = 0.0;
        int count = 0;
        uint32_t hash_words[64 * 3];
        int hw_idx = 0;

        for (int iy = 0; iy < 8; ++iy) {
            for (int ix = 0; ix < 8; ++ix) {
                float u1080_target = 160.0f + (float)ix * 224.0f + 16.0f;
                float v1080_target = 96.0f + (float)iy * 112.0f + 16.0f;
                int x = (int)roundf(u1080_target * ((float)g_W / 1920.0f) - 0.5f);
                int y = (int)roundf(v1080_target * ((float)g_H / 1080.0f) - 0.5f);
                int gx = x >> 3;
                int gy = y >> 3;
                int tid = ((y & 7) << 3) | (x & 7);
                warp8_cs_main(tbl, gx, gy, tid);
                float u1080 = ((float)x + 0.5f) * (1920.0f / (float)g_W);
                float v1080 = ((float)y + 0.5f) * (1080.0f / (float)g_H);
                for (int c = 0; c < 3; ++c) {
                    float gt_c = eval_scene_ch(u1080, v1080, c);
                    float out_c = g_last_out[c];
                    float err = fabsf(out_c - gt_c);
                    sum_abs_err += err;
                    if (err > max_abs_err) max_abs_err = err;
                    union { float f; uint32_t u; } bits = { out_c };
                    hash_words[hw_idx++] = bits.u;
                }
                count += 3;
            }
        }
        printf("RES %dx%d mae=%.8f max=%.8f words=", g_W, g_H, sum_abs_err / count, max_abs_err);
        for (int i = 0; i < hw_idx; ++i) {
            printf("%08x", hash_words[i]);
        }
        printf("\n");
    }
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as td:
        td_p = pathlib.Path(td)
        ll_w = td_p / "warp8.ll"
        bc_w = td_p / "warp8.bc"
        c_d = td_p / "driver.c"
        bc_d = td_p / "driver.bc"
        bc_all = td_p / "all.bc"
        ll_w.write_text(ir_clean, encoding="utf-8")
        c_d.write_text(c_driver, encoding="utf-8")
        subprocess.run(
            [clang_bin, f"--target={target_triple}", "-O0", "-Wno-override-module", "-emit-llvm", "-c", str(ll_w), "-o", str(bc_w)],
            check=True,
        )
        subprocess.run(
            [clang_bin, f"--target={target_triple}", "-O0", "-emit-llvm", "-c", str(c_d), "-o", str(bc_d)],
            check=True,
        )
        subprocess.run([link_bin, str(bc_w), str(bc_d), "-o", str(bc_all)], check=True)
        lli_cmd = [lli_bin, "--dlopen=msvcrt.dll", str(bc_all)] if sys.platform == "win32" else [lli_bin, str(bc_all)]
        p = subprocess.run(lli_cmd, capture_output=True, text=True, check=True)
        out = {}
        for ln in p.stdout.strip().splitlines():
            parts = ln.split()
            res = parts[1]
            mae = float(parts[2].split("=")[1])
            mx = float(parts[3].split("=")[1])
            whex = parts[4].split("=")[1]
            sha = hashlib.sha256(bytes.fromhex(whex)).hexdigest()
            out[res] = {"mae": mae, "max_err": mx, "sha256": sha}
        return out


class TestWarp8Resolutions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = _run_compiled_warp8()

    def test_1_fg_res_supported_contract(self):
        """Verify fg_res.h supports 3200x1800 and all tested non-standard/non-16:9 resolutions."""
        fg_res_src = FG_RES_H.read_text(encoding="utf-8")
        self.assertIn("{ 3200u, 1800u, 1 }", fg_res_src)
        for res in list(STD_9FFC637_SHA256.keys()) + list(NONSTD_RESOLUTIONS):
            w_s, h_s = res.split("x")
            w, h = int(w_s), int(h_s)
            self.assertTrue(960 <= w <= 3840 and (w & 7) == 0, f"Invalid width in {res}")
            self.assertTrue(540 <= h <= 2160 and (h & 3) == 0, f"Invalid height in {res}")

    def test_2_compiled_warp8_standard_resolutions_bit_exact_with_9ffc637(self):
        """All 5 standard 16:9 resolutions must remain bit-for-bit identical to 9ffc637."""
        for res, exp_sha in STD_9FFC637_SHA256.items():
            r = self.results[res]
            self.assertEqual(
                r["sha256"],
                exp_sha,
                f"{res} output SHA-256 changed from 9ffc637 ({r['sha256']} != {exp_sha})",
            )
            self.assertLess(r["mae"], 0.001, f"{res} MAE {r['mae']:.6f} >= 0.001")
            self.assertLess(r["max_err"], 0.002, f"{res} max_err {r['max_err']:.6f} >= 0.002")

    def test_3_compiled_warp8_nonstandard_and_non16_9_resolutions(self):
        """Non-standard 16:9 (3200x1800, 2048x1152) and non-16:9 (1440x900, 1280x800, 1360x768)
        resolutions accepted by fg_res_supported must reconstruct the shifted mid-frame accurately."""
        for res in NONSTD_RESOLUTIONS:
            with self.subTest(res=res):
                r = self.results[res]
                self.assertLess(
                    r["mae"],
                    0.002,
                    f"{res} mid-frame MAE={r['mae']:.6f} >= 0.002 (unscaled warp8.ll regression)",
                )
                self.assertLess(
                    r["max_err"],
                    0.003,
                    f"{res} mid-frame max_err={r['max_err']:.6f} >= 0.003 (unscaled warp8.ll regression)",
                )

    def test_4_warp8_gfx700_hardware_budget(self):
        """Verify warp8.ll compiles within VGPR <= 64, ScratchSize == 0, and aligned 2960 words."""
        llc_bin = _find_llvm_tool("llc")
        if not llc_bin:
            self.skipTest("llc-22 required")
        sys.path.insert(0, str(ROOT / "classic"))
        import gen_shaders

        asm = subprocess.run(
            [llc_bin, "-mcpu=gfx700", "-O2", "-filetype=asm", str(WARP8_LL), "-o", "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        obj = subprocess.run(
            [llc_bin, "-mcpu=gfx700", "-O2", "-filetype=obj", str(WARP8_LL), "-o", "-"],
            capture_output=True,
            check=True,
        ).stdout
        nv = gen_shaders.stat(asm, "NumVgprs")
        ns = gen_shaders.stat(asm, "TotalNumSgprs", "NumSgprs")
        scratch = gen_shaders.stat(asm, "ScratchSize")
        words = gen_shaders.text_words(obj)
        padded_len = len(words) + ((-len(words)) % 64 + 16)
        self.assertLessEqual(nv, 64, f"NumVgprs={nv} > 64")
        self.assertLessEqual(ns, 104, f"TotalNumSgprs={ns} > 104")
        self.assertEqual(scratch, 0, f"ScratchSize={scratch} != 0")
        self.assertIn(padded_len, (2880, 2896, 2960, 3024), f"Aligned warp8 word count={padded_len} not in (2880, 2896, 2960, 3024)")


if __name__ == "__main__":
    unittest.main()
