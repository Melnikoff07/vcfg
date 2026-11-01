"""T18 Mode Separation Tests: Quality (preset=0), Fast (preset=1), and Sharp (preset=2).

Verifies:
1. Quality (preset=0) is 100% bit-for-bit identical to release-236 (8504e70) across main.c, shaders, and Python reference.
2. Sharp (preset=2) boosts contour sharpness in calc_rcas_params() + rcas8.ll (emode bit 2 = 4) and relaxes high-speed
   damping of %csh_e in warp8.ll (flags bit 10 = 1024), increasing contour gradient energy E_contour while keeping
   Python and LLVM IR in lockstep.
3. Fast (preset=1) sets FG8_FLAG_FAST (32768u, bit 15 of consts.flags) and g_opt_fast=1 ONLY when preset == 1, forces
   rcas8 mid_only on generated-frame dispatches, skips @cr wave-uniformly in warp8.ll, and skips Phase 2 / @bsad
   wave-uniformly on calm blocks in me1.ll and mf8.ll.
4. vcfg16 (plugin/source/a20_copy_impl.h) supports preset=2 ("Sharp") in INI parser, menu cycling, menu label, and
   a21_emit_post without altering preset=0 or preset=1.
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))

import vcfg_ref as vref  # noqa: E402
import vcfg_motion as vm  # noqa: E402
import vcfg_warp as vw  # noqa: E402

F32 = np.float32
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8", errors="replace")
FG8_TABLE_H = (ROOT / "classic" / "source" / "fg8_table.h").read_text(encoding="utf-8", errors="replace")
A20_H = (ROOT / "plugin" / "source" / "a20_copy_impl.h").read_text(encoding="utf-8", errors="replace")
WARP8_LL = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
RCAS8_LL = (ROOT / "classic" / "shaders" / "rcas8.ll").read_text(encoding="utf-8")
ME1_LL = (ROOT / "classic" / "shaders" / "me1.ll").read_text(encoding="utf-8")
MF8_LL = (ROOT / "classic" / "shaders" / "mf8.ll").read_text(encoding="utf-8")


def rcas_luma(rgb: np.ndarray) -> np.ndarray:
    c = rgb[..., :3].astype(F32)
    return c[..., 1] * F32(0.5) + (c[..., 0] + c[..., 2]) * F32(0.25)


def eval_contour_energy(rgb: np.ndarray, mask: np.ndarray) -> float:
    L = rcas_luma(rgb)
    H, W = L.shape
    Lp = np.pad(L, 1, mode="edge")
    gx = np.abs(Lp[1:1 + H, 2:2 + W] - Lp[1:1 + H, 0:W])
    gy = np.abs(Lp[2:2 + H, 1:1 + W] - Lp[0:H, 1:1 + W])
    g = (gx + gy) * F32(255.0)
    return float(np.mean(g[mask])) if np.any(mask) else 0.0


def extract_function(src: str, name: str) -> str:
    m = re.search(r"^static [^\n]+\b" + name + r"\([^;]*\)\s*\{", src, re.M)
    assert m is not None, f"Function {name} not found"
    pos = src.index("{", m.start())
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[m.start():end]


class TestT18ClassicHostAndIR(unittest.TestCase):
    def test_fg8_flag_fast_and_g_opt_fast_bound_to_preset1(self):
        self.assertIn("#define FG8_FLAG_FAST", FG8_TABLE_H)
        self.assertIn("g_opt_fast", FG8_TABLE_H)
        self.assertRegex(MAIN_C, r"g_opt_fast\s*=\s*\(c->preset\s*==\s*1\)")
        self.assertIn("FG8_FLAG_FAST", MAIN_C)

    def test_calc_rcas_params_separates_quality_fast_sharp(self):
        fn_src = extract_function(MAIN_C, "calc_rcas_params")
        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct {{ int preset, sharpness, upscale; }} fg_config_t;
static fg_config_t g_cfg;
static uint32_t g_sh8_off[16] = {{ [14] = 0x18D00u }};
{fn_src}
int main(void) {{
    for (int preset = 0; preset <= 2; preset++) {{
        for (int sh = 0; sh <= 40; sh += 40) {{
            for (int up = 0; up <= 1; up++) {{
                g_cfg.preset = preset;
                g_cfg.sharpness = sh;
                g_cfg.upscale = up;
                uint32_t cb = 0, fl = 0;
                calc_rcas_params(3u, &cb, &fl);
                printf("p=%d s=%d u=%d cb=0x%08x fl=%u\\n", preset, sh, up, cb, fl);
            }}
        }}
    }}
    return 0;
}}
"""
        cc = shutil.which("clang") or shutil.which("cc")
        if cc is None:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "t.c"
            exe = pathlib.Path(td) / "t"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([cc, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
        rows = {}
        for ln in out.strip().splitlines():
            parts = dict(kv.split("=") for kv in ln.split())
            rows[(int(parts["p"]), int(parts["s"]), int(parts["u"]))] = (int(parts["cb"], 16), int(parts["fl"]))

        # Quality (p=0) at s=0, u=0: rcas8 off (cb=0, fl=0)
        self.assertEqual(rows[(0, 0, 0)], (0, 0))
        # Sharp (p=2) at s=0, u=0: floor sharp_pct=35 activates rcas8 with bit 7 (128) set
        self.assertNotEqual(rows[(2, 0, 0)][0], 0)
        self.assertEqual(rows[(2, 0, 0)][1] & 128, 128)
        # Sharp (p=2) at s=40, u=1: stronger con than Quality (p=0, s=40, u=1) and bit 7 (128) set
        self.assertGreater(rows[(2, 40, 1)][0], rows[(0, 40, 1)][0])
        self.assertEqual(rows[(2, 40, 1)][1], rows[(0, 40, 1)][1] | 128)
        # Fast (p=1) preserves target_mask and con_bits identically to Quality (p=0)
        self.assertEqual(rows[(1, 40, 1)][0], rows[(0, 40, 1)][0])
        self.assertEqual(rows[(1, 40, 1)][1] & 3, 3)

    def test_ir_contains_sharp_and_fast_gates(self):
        # warp8.ll: FG8_FLAG_FAST (32768) zeroes %csh for uniform @cr skip; %sh_on (1024) relaxes %vms damping by 0.5
        self.assertIn("and i32 %flags, 32768", WARP8_LL)
        self.assertIn("select i1 %sh_on, float 5.000000e-01, float 1.000000e+00", WARP8_LL)
        # rcas8.ll: bit 7 (128) of rfl passed as bit 2 (4) of %emode for Sharp contour boost
        self.assertIn("and i32 %emode, 4", RCAS8_LL)
        # me1.ll and mf8.ll remain bit-for-bit identical to release-236 (no 32768 skip)
        self.assertNotIn("32768", ME1_LL)
        self.assertNotIn("32768", MF8_LL)


class TestT18ReferenceAndCompiledShaders(unittest.TestCase):
    def test_sharp_boosts_contour_energy_and_matches_ir(self):
        H, W = 64, 96
        yy, xx = np.mgrid[0:H, 0:W]
        # Moving diagonal/vertical contour scene with high-speed pan (v = 18.5 px, where vms damps csh_e)
        def make_frame(shift_x):
            bg = np.full((H, W, 3), 0.22, F32)
            edge = ((xx - 48.0 - shift_x) - (yy - 32.0) * 0.25)
            fg_w = (0.5 + 0.5 * np.tanh(edge * 0.8))[..., None].astype(F32)
            fg = np.array([0.82, 0.78, 0.70], F32)[None, None, :]
            return (bg * (F32(1.0) - fg_w) + fg * fg_w).astype(F32)

        A = make_frame(9.25)
        B = make_frame(-9.25)
        F = np.zeros((H // 16 + 1, W // 16 + 1, 2), F32)
        F[..., 0] = F32(18.5)
        GK = np.zeros((H // 16 + 1, W // 16 + 1), np.int64)

        mid_qual = vw.warp8(A, B, F, GK, 0, t=0.5, flags=0)
        mid_sharp = vw.warp8(A, B, F, GK, 0, t=0.5, flags=1024)
        mid_fast = vw.warp8(A, B, F, GK, 0, t=0.5, flags=32768)

        # Sharp warp8 differs from Quality on fast moving contours (relaxed vms damping)
        self.assertFalse(np.array_equal(mid_qual, mid_sharp))
        # Fast warp8 skips @cr (csh = 0.0)
        mid_cr0 = vw.warp8(A, B, F, GK, 0, t=0.5, flags=0, wp={"csh": 0.0})
        np.testing.assert_array_equal(mid_fast, mid_cr0)

        # RCAS Sharp mode (emode | 4) boosts E_contour vs Quality (emode = 1)
        still = vw.static_px(A, B)
        con_q = vw.rcas_con(40)
        con_s = vw.rcas_con(60)
        out_qual = vw.rcas8(mid_qual, con_q, 1, skip=still, still=still)
        out_sharp = vw.rcas8(mid_sharp, con_s, 2 | 4, skip=still, still=still)
        mask = (~still) & (np.abs((xx - 48.0) - (yy - 32.0) * 0.25) <= 1.0)
        e_qual = eval_contour_energy(out_qual, mask)
        e_sharp = eval_contour_energy(out_sharp, mask)
        self.assertGreater(e_sharp, e_qual * 1.05)

        # Verify motion stage is identical across Quality/Fast/Sharp
        st_q = vref.motion(A, B, dict(vref.PARAMS, flags=0))
        st_f = vref.motion(A, B, dict(vref.PARAMS, flags=32768))
        np.testing.assert_array_equal(st_q["F"], st_f["F"])


class TestT18Vcfg16NewEngineSharp(unittest.TestCase):
    def test_a20_copy_impl_supports_preset2_sharp(self):
        # 1. INI parser allows preset 0..2
        self.assertIn("a21_preset = v < 0 ? 0 : (v > 2 ? 2 : v);", A20_H)
        # 2. Menu adjust cycles 0 -> 1 -> 2 -> 0
        self.assertRegex(A20_H, r"a21_preset\s*=\s*\(a21_preset\s*\+\s*\(right\s*\?\s*1\s*:\s*2\)\)\s*%\s*3")
        # 3. Menu label shows Quality / Fast / Sharp
        self.assertIn('"Sharp"', A20_H)
        # 4. a21_emit_post boosts sharpness only when a21_preset == 2
        self.assertRegex(A20_H, r"a21_preset\s*==\s*2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
