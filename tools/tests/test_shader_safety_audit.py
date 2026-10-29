#!/usr/bin/env python3
"""
R19 Static & Numerical Shader Safety Audit Suite (v2.33)
========================================================
Verifies four invariant classes across all 27 GCN (gfx700 / Bonaire) LLVM IR
compute shaders in `classic/shaders/*.ll` and their host descriptor tables in
`classic/source/fg8_table.h`, `classic/source/shaders.h`, and `classic/source/gv.c`:

  1. Division-by-Zero, NaN, and Inf Safety (Static IR Scan + IEEE-754 Float32 Fuzzing)
  2. Out-of-Bounds (OOB) Memory & LDS Guard Verification + R16 pcopy8 Table Audit
  3. Bitfield Packing, Sign-Extension & YCoCg-R 4:2:0 Color Space Range Verification
  4. GCN gfx700 Hardware Limits (VGPR/SGPR/LDS/Scratch/Code Budget) & Fg8Table Offsets
"""

import ctypes
import math
import re
import struct
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHADERS_DIR = REPO_ROOT / "classic" / "shaders"
SOURCE_DIR = REPO_ROOT / "classic" / "source"
GEN_SHADERS_PY = REPO_ROOT / "classic" / "gen_shaders.py"

sys.path.insert(0, str(REPO_ROOT / "tools"))
import elf_arrays  # noqa: E402


def f32(val: float) -> float:
    """Round a Python float to IEEE-754 binary32 (single precision)."""
    return struct.unpack("<f", struct.pack("<f", float(val)))[0]


def hex_double_to_f32(hex_str: str) -> float:
    """Convert LLVM IR 64-bit hex float literal (e.g. 0x3F10000000000000) to binary32."""
    bits64 = int(hex_str, 16)
    val64 = struct.unpack("<d", struct.pack("<Q", bits64))[0]
    return f32(val64)


def s8_from_u32(val: int) -> int:
    """Sign-extend low 8 bits of val to Python int in [-128, 127]."""
    b = val & 0xFF
    return b - 256 if (b & 0x80) else b


def s16_from_u32(val: int) -> int:
    """Sign-extend low 16 bits of val to Python int in [-32768, 32767]."""
    w = val & 0xFFFF
    return w - 65536 if (w & 0x8000) else w


def ashr32(val: int, sh: int) -> int:
    """32-bit arithmetic right shift matching LLVM IR `ashr i32`."""
    v32 = ctypes.c_int32(val & 0xFFFFFFFF).value
    return v32 >> sh


def clamp_i32(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


def sat_f32(x: float) -> float:
    return max(f32(0.0), min(f32(1.0), f32(x)))


class TestShaderSafetyAudit(unittest.TestCase):
    """Comprehensive static and numerical safety audit for v2.33 shaders."""

    @classmethod
    def setUpClass(cls):
        cls.ll_files = sorted(SHADERS_DIR.glob("*.ll"))
        cls.ll_texts = {p.name: p.read_text(encoding="utf-8") for p in cls.ll_files}
        cls.fg8_table_h = (SOURCE_DIR / "fg8_table.h").read_text(encoding="utf-8")
        cls.shaders_h = (SOURCE_DIR / "shaders.h").read_text(encoding="utf-8")
        cls.main_c = (SOURCE_DIR / "main.c").read_text(encoding="utf-8")
        cls.gv_c = (SOURCE_DIR / "gv.c").read_text(encoding="utf-8")
        cls.gen_shaders_text = GEN_SHADERS_PY.read_text(encoding="utf-8")

    # =========================================================================
    # INVARIANT 1: Division-by-Zero, NaN, and Inf Safety
    # =========================================================================

    def test_1a_static_scan_all_divisions_sqrt_exp2(self):
        """Scan all 27 .ll files for fdiv, udiv, sdiv, urem, srem, sqrt, exp2."""
        self.assertEqual(len(self.ll_files), 27, "Expected all 27 .ll shader files in classic/shaders/")

        fdiv_sites = []
        int_div_sites = []
        sqrt_sites = []
        exp2_sites = []

        for name, text in self.ll_texts.items():
            for lineno, line in enumerate(text.splitlines(), 1):
                # Strip trailing comments before scanning instructions
                code_part = line.split(";", 1)[0].strip()
                if not code_part:
                    continue
                if re.search(r"\bfdiv\b", code_part):
                    fdiv_sites.append((name, lineno, code_part))
                if re.search(r"\b(udiv|sdiv|urem|srem)\b", code_part):
                    int_div_sites.append((name, lineno, code_part))
                if "llvm.sqrt.f32(" in code_part and not code_part.startswith("declare"):
                    sqrt_sites.append((name, lineno, code_part))
                if "llvm.exp2.f32(" in code_part and not code_part.startswith("declare"):
                    exp2_sites.append((name, lineno, code_part))

        # Total counts across all 27 shaders
        self.assertEqual(len(fdiv_sites), 29, f"Unexpected fdiv count: {len(fdiv_sites)}")
        self.assertEqual(len(sqrt_sites), 4, f"Unexpected sqrt count: {len(sqrt_sites)}")
        self.assertEqual(len(exp2_sites), 3, f"Unexpected exp2 count: {len(exp2_sites)}")

        # Verify every integer division/remainder uses a non-zero constant divisor,
        # or (in legacy warp.ll) block size bs = 8 * sx >= 16.
        for name, lineno, stmt in int_div_sites:
            m = re.search(r"\b(?:udiv|sdiv|urem|srem)\s+i32\s+[^,]+,\s*(\S+)", stmt)
            self.assertIsNotNone(m, f"Unparsed int div at {name}:{lineno}: {stmt}")
            divisor = m.group(1)
            if divisor.lstrip("-").isdigit():
                self.assertNotEqual(int(divisor), 0, f"Zero int divisor at {name}:{lineno}")
            elif name == "warp.ll":
                self.assertEqual(divisor, "%bs")
                self.assertIn("%bs = extractelement <4 x i32> %cn, i32 3", self.ll_texts["warp.ll"])
            else:
                self.assertIn(name, ("me1.ll", "mf8.ll"), f"Dynamic int div in {name}:{lineno}: {stmt}")
                # Texture-normalized SAD divisor is smax(tex, 96) >= 96
                self.assertIn("call i32 @llvm.smax.i32(", self.ll_texts[name])
                self.assertIn(", i32 96)", self.ll_texts[name])

        # Verify all 4 sqrt sites have non-negative clamped/unsigned inputs:
        sqrt_files = [s[0] for s in sqrt_sites]
        self.assertEqual(
            sorted(sqrt_files),
            ["fgs8.ll", "gvz8.ll", "gvz8.ll", "pyr8.ll"],
        )
        self.assertIn("%y = fmul float %s2, 0.25", self.ll_texts["pyr8.ll"])
        self.assertIn("%l = call float @llvm.sqrt.f32(float %y)", self.ll_texts["pyr8.ll"])
        self.assertIn("%f = uitofp i32 %m to float", self.ll_texts["fgs8.ll"])
        self.assertIn("%r = call float @llvm.sqrt.f32(float %f)", self.ll_texts["fgs8.ll"])
        self.assertIn("%zn = call float @sat(float %zn0)", self.ll_texts["gvz8.ll"])
        self.assertIn("%zs1 = call float @llvm.sqrt.f32(float %zn)", self.ll_texts["gvz8.ll"])
        self.assertIn("%zs = call float @llvm.sqrt.f32(float %zs1)", self.ll_texts["gvz8.ll"])

        # Verify all 3 exp2 sites in warp8.ll and warp8g.ll have non-positive arguments.
        # warp8.ll (one exp2 per candidate): %k0 = |%em - %e| * -8.0 <= 0.
        warp8 = self.ll_texts["warp8.ll"]
        self.assertIn("%d0 = fsub float %em, %e", warp8)
        self.assertIn("%ad0 = call float @llvm.fabs.f32(float %d0)", warp8)
        self.assertIn("%k0 = fmul float %ad0, -8.0", warp8)
        self.assertIn("%z = call float @llvm.exp2.f32(float %k0)", warp8)
        # warp8g.ll: %em1 = min(%em, %e), so %d0 = %em1 - %em <= 0 and %d1 = %em1 - %e <= 0;
        # %k0 = %d0 * c <= 0 and %k1 = %d1 * c <= 0 (c = 9.617967).
        for wname, k_scale in (("warp8g.ll", "0x40233C6620000000"),):
            wtxt = self.ll_texts[wname]
            self.assertIn("%em1 = call float @llvm.minnum.f32(float %em, float %e)", wtxt)
            self.assertIn("%d0 = fsub float %em1, %em", wtxt)
            self.assertIn(f"%k0 = fmul float %d0, {k_scale}", wtxt)
            self.assertIn("%sc = call float @llvm.exp2.f32(float %k0)", wtxt)
            self.assertIn("%d1 = fsub float %em1, %e", wtxt)
            self.assertIn(f"%k1 = fmul float %d1, {k_scale}", wtxt)
            self.assertIn("%x1 = call float @llvm.exp2.f32(float %k1)", wtxt)

    def test_1b_numerical_fuzz_me1_equi_and_phase8(self):
        """Fuzz me1.ll @equi parabolic sub-pixel refinement and phase8.ll cadence divisor."""
        def me1_equi(l_val: float, c_val: float, r_val: float) -> float:
            l_f = f32(l_val)
            c_f = f32(c_val)
            r_f = f32(r_val)
            mn = min(l_f, r_f)
            mx = max(l_f, r_f)
            mn1 = max(mn, f32(1.0))
            cm = f32(c_f / mn1)
            good = cm < f32(1.25)
            num = f32(l_f - r_f)
            d0 = f32(mx - c_f)
            den = f32(d0 * f32(2.0))
            pos = c_f < mn
            # In LLVM IR, fdiv executes before select, then select discards if !pos
            q = f32(num / den) if den != 0.0 else float("nan")
            raw = q if pos else f32(0.0)
            cl1 = max(raw, f32(-0.5))
            cl2 = min(cl1, f32(0.5))
            hm = f32(cl2 * f32(4.0))
            res = hm if good else f32(0.0)
            return res

        test_sads = [0, 1, 2, 10, 100, 1000, 32768, 65535]
        for l_i in test_sads:
            for c_i in test_sads:
                for r_i in test_sads:
                    out = me1_equi(float(l_i), float(c_i), float(r_i))
                    self.assertFalse(math.isnan(out), f"NaN in @equi({l_i}, {c_i}, {r_i})")
                    self.assertFalse(math.isinf(out), f"Inf in @equi({l_i}, {c_i}, {r_i})")
                    self.assertGreaterEqual(out, -2.0)
                    self.assertLessEqual(out, 2.0)

        for n_raw in range(-50, 50):
            n_clamped = clamp_i32(n_raw, 1, 3)
            n1f = f32(float(n_clamped) + 1.0)
            self.assertGreaterEqual(n1f, 2.0)
            self.assertLessEqual(n1f, 4.0)
            for idx in range(0, n_clamped):
                phase = f32(f32(float(idx) + 1.0) / n1f)
                self.assertTrue(0.25 <= phase <= 0.75)

    def test_1c_numerical_fuzz_rcas8_all_10_fdivs(self):
        """Fuzz all 10 fdiv sites in rcas8.ll across boundary luma/chroma inputs."""
        eps = hex_double_to_f32("0x3F10000000000000")  # 2^-14 = 6.103515625e-05
        eps4 = hex_double_to_f32("0x3F30000000000000")  # 2^-12 = 2.44140625e-04
        minus_eps = hex_double_to_f32("0xBF10000000000000")  # -2^-14
        neg_quarter = hex_double_to_f32("0xBFCFFE0000000000")  # -0.24993896484375

        self.assertAlmostEqual(eps, 2.0**-14, places=10)
        self.assertAlmostEqual(eps4, 2.0**-12, places=10)
        self.assertAlmostEqual(minus_eps, -(2.0**-14), places=10)

        grid = [i / 255.0 for i in range(256)]
        for i in range(0, 256, 3):
            mn5 = f32(grid[i])
            for j in range(i, 256, 3):
                mx5 = f32(grid[j])
                c_den = f32(mx5 + eps)
                self.assertGreaterEqual(c_den, eps)
                c_rat = f32(f32(mx5 - mn5) / c_den)
                self.assertTrue(0.0 <= c_rat <= 1.0)

                den0 = f32(f32(4.0 * mx5) + eps4)
                self.assertGreaterEqual(den0, eps4)
                hmin = f32(mn5 / den0)

                den1 = f32(f32(4.0 * f32(mn5 - 1.0)) + minus_eps)
                self.assertLessEqual(den1, minus_eps)
                hmax = f32(f32(1.0 - mx5) / den1)

                neg_hmin = f32(-hmin)
                lobe0 = max(neg_hmin, hmax)
                lobe1 = max(lobe0, neg_quarter)
                lobe = min(f32(0.0), lobe1)

                k_den = f32(1.0 + mx5)
                self.assertGreaterEqual(k_den, 1.0)
                k_hl = f32(1.0 / k_den)

                for con in (0.0, 0.05, 0.125, 0.25, 0.5, 1.0):
                    w0 = f32(f32(lobe * f32(con)) * k_hl)
                    w_eff = w0
                    norm = f32(1.0 + f32(4.0 * w_eff))
                    self.assertGreater(
                        norm,
                        0.0,
                        f"Non-positive RCAS norm={norm} at mn5={mn5}, mx5={mx5}, con={con}",
                    )
                    inv_norm = f32(1.0 / norm)
                    self.assertFalse(math.isnan(inv_norm) or math.isinf(inv_norm))

        for mn5, mx5 in ((0.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
            den0 = f32(f32(4.0 * f32(mx5)) + eps4)
            den1 = f32(f32(4.0 * f32(f32(mn5) - 1.0)) + minus_eps)
            hmin = f32(f32(mn5) / den0)
            hmax = f32(f32(1.0 - f32(mx5)) / den1)
            lobe0 = max(f32(-hmin), hmax)
            lobe = min(f32(0.0), max(lobe0, neg_quarter))
            norm = f32(1.0 + f32(4.0 * lobe))
            self.assertGreaterEqual(norm, 2.0**-16)

    def test_1d_numerical_fuzz_warp8_and_warp8g_divisors(self):
        """Fuzz @tapsq, @crw, @cr, @diff3_k, and @accum across warp8.ll and warp8g.ll."""
        for c_sharp in (0.0, 0.375, 0.5):
            for ix in range(0, 64):
                fx = f32(ix / 64.0)
                t = f32(fx * f32(1.0 - fx))
                s = f32(t * f32(c_sharp))
                w0 = f32(-s * fx)
                w1 = f32(f32(1.0 - fx) + f32(s * f32(1.0 + fx)))
                w2 = f32(fx + f32(s * f32(2.0 - fx)))
                w3 = f32(-s * f32(1.0 - fx))
                w12 = f32(w1 + w2)
                self.assertGreaterEqual(w12, 1.0)

                for iy in range(0, 64):
                    fy = f32(iy / 64.0)
                    ty = f32(fy * f32(1.0 - fy))
                    sy = f32(ty * f32(c_sharp))
                    wy0 = f32(-sy * fy)
                    wy12 = f32(1.0 + sy)
                    wy3 = f32(-sy * f32(1.0 - fy))

                    g0 = f32(w12 * wy0)
                    g1 = f32(w0 * wy12)
                    g2 = f32(w12 * wy12)
                    g3 = f32(w3 * wy12)
                    g4 = f32(w12 * wy3)
                    ws = f32(f32(f32(g0 + g1) + f32(g2 + g3)) + g4)
                    self.assertGreaterEqual(ws, 0.93)

        # Simulate online running-min @accum from warp8.ll (lines 359-395) and warp8g.ll
        def sim_running_accum(errs, weights, is_warp8g=False):
            sw = f32(0.0)
            em = f32(1.0e30)
            floor_bias = hex_double_to_f32("0x3FA99999A0000000") if is_warp8g else hex_double_to_f32("0x3FB47AE140000000")
            for e0_in, w_in in zip(errs, weights):
                e0 = f32(e0_in)
                w = f32(w_in)
                is_fg_sub = w >= f32(1.5)
                w_raw = f32(w - 2.0) if is_fg_sub else w
                e_clean = e0 < hex_double_to_f32("0x3FC70A3D80000000")
                fg_bst = is_fg_sub and e_clean
                w_eff = f32(f32(w_raw * 4.0) + hex_double_to_f32("0x3FC47AE140000000")) if fg_bst else w_raw
                e_s1 = max(f32(e0 - hex_double_to_f32("0x3FB47AE140000000")), f32(0.0))
                e = e_s1 if fg_bst else e0
                em1 = min(em, e)
                d0 = f32(em1 - em)  # <= 0.0
                k0 = f32(d0 * 8.0)
                self.assertLessEqual(k0, 0.0)
                sc = f32(2.0**k0)
                d1 = f32(em1 - e)  # <= 0.0
                k1 = f32(d1 * 8.0)
                self.assertLessEqual(k1, 0.0)
                x1 = f32(2.0**k1)
                w5 = f32(w_eff + floor_bias)
                om = f32(w5 * x1)
                sw = f32(f32(sw * sc) + om)
                em = em1
            return sw

        for is_g in (False, True):
            for errs in (
                [0.0] * 7,
                [3.0] * 7,
                [2.5, 0.1, 3.0, 0.5, 0.8, 1.2, 1.5],
                [0.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
            ):
                sw = sim_running_accum(errs, [0.25, 0.25, 0.25, 0.25, 0.1, 0.1, 0.05], is_warp8g=is_g)
                min_bound = 0.05 if is_g else 0.08
                self.assertGreaterEqual(sw, f32(min_bound * 0.999))
                inv_sw = f32(1.0 / sw)
                self.assertFalse(math.isnan(inv_sw) or math.isinf(inv_sw))

    # =========================================================================
    # INVARIANT 2: Out-of-Bounds (OOB) Memory & GPU Page Fault Guards
    # =========================================================================

    def test_2a_bsad_bsadu_memory_bounds_across_resolutions_and_extreme_vectors(self):
        """
        Prove @bsad and @bsadu raw.buffer.load.v3i32 offsets never exceed V# nbytes
        across all resolutions (1920x1080, 1600x900, 1280x720), pyramid levels L1/L2/L3,
        and extreme motion vectors.
        """
        for sname in ("me3.ll", "me2.ll", "me1.ll", "fgs8.ll", "mf8.ll"):
            txt = self.ll_texts[sname]
            self.assertIn("@bsad(", txt)
            self.assertIn("%wm8 = sub i32 %wl, 8", txt)
            self.assertIn("%hm8 = sub i32 %hl, 8", txt)
            self.assertIn("%xmin_c = call i32 @llvm.smax.i32(i32 0, i32 %ndx)", txt)
            self.assertIn("%xmax_c = call i32 @llvm.smin.i32(i32 %wm8, i32 %xmax_c0)", txt)
            self.assertIn("%ymin_c = call i32 @llvm.smax.i32(i32 0, i32 %ndy)", txt)
            self.assertIn("%ymax_c = call i32 @llvm.smin.i32(i32 %hm8, i32 %ymax_c0)", txt)

        self.assertIn("define internal i32 @bsadu(", self.ll_texts["me3.ll"])
        # Confirm fg8_table.h pads V# pyramid buffers by +16 bytes (lines 233-238)
        self.assertIn("960 * 540 + 16", self.fg8_table_h)
        self.assertIn("480 * 270 + 16", self.fg8_table_h)
        self.assertIn("240 * 135 + 16", self.fg8_table_h)

        def sim_bsad_offsets(wl: int, hl: int, X: int, Y: int, dx: int, dy: int):
            wm8 = wl - 8
            hm8 = hl - 8
            dxc = clamp_i32(dx, -wm8, wm8)
            dyc = clamp_i32(dy, -hm8, hm8)
            ndx = -dxc
            ndy = -dyc
            fcx = dxc >> 1
            fcy = dyc >> 1
            xc0 = X - fcx
            xmin_c = max(0, ndx)
            xmax_c = min(wm8, wm8 - dxc)
            xc = clamp_i32(xc0, xmin_c, xmax_c)
            xp = xc + dxc
            axp = xp & -4
            axc = xc & -4

            ybc0 = Y - fcy
            ymin_c = max(0, ndy)
            ymax_c = min(hm8, hm8 - dyc)
            ybc = clamp_i32(ybc0, ymin_c, ymax_c)
            ybp = ybc + dyc
            return axp, axc, ybp, ybc

        resolutions = [(1920, 1080), (1600, 900), (1280, 720)]
        extreme_vectors = [-512, -240, -128, -127, -1, 0, 1, 127, 128, 240, 512]

        for w0, h0 in resolutions:
            for level in (1, 2, 3):
                wl = w0 >> level
                hl = h0 >> level
                v_nbytes = wl * hl + 16  # +16 slack bytes in fill_table8_wh

                test_coords = [
                    (0, 0),
                    (8, 8),
                    (wl - 8, hl - 8),
                    (wl - 1, hl - 1),
                    (wl + 16, hl + 16),
                ]
                for X, Y in test_coords:
                    for dx in extreme_vectors:
                        for dy in extreme_vectors:
                            for X_in, Y_in in ((X, Y), (X + (dx >> 1), Y + (dy >> 1))):
                                axp, axc, ybp, ybc = sim_bsad_offsets(wl, hl, X_in, Y_in, dx, dy)
                                for r in range(8):
                                    ap = (ybp + r) * wl + axp
                                    ac = (ybc + r) * wl + axc
                                    # v3i32 loads 12 bytes: [ap .. ap + 11]
                                    self.assertTrue(
                                        0 <= ap and ap + 12 <= wl * hl + 4 <= v_nbytes,
                                        f"OOB ap={ap} at {w0}x{h0} L{level} (v_nbytes={v_nbytes})",
                                    )
                                    self.assertTrue(
                                        0 <= ac and ac + 12 <= wl * hl + 4 <= v_nbytes,
                                        f"OOB ac={ac} at {w0}x{h0} L{level} (v_nbytes={v_nbytes})",
                                    )

    def test_2b_lds_shared_memory_bounds_all_shaders(self):
        """Verify LDS shared memory allocations and indexing bounds across all Mode 8 shaders."""
        for sname in ("me1.ll", "me2.ll", "me3.ll", "mf8.ll"):
            txt = self.ll_texts[sname]
            self.assertIn("@keys = internal addrspace(3) global [64 x i32] undef, align 16", txt)
            self.assertIn("@vecs = internal addrspace(3) global [64 x i32] undef, align 16", txt)

        gm8_txt = self.ll_texts["gm8.ll"]
        self.assertIn("@hist = internal addrspace(3) global [256 x i32] undef, align 16", gm8_txt)
        self.assertIn("@scan = internal addrspace(3) global [128 x i32] undef, align 16", gm8_txt)
        self.assertIn("@res = internal addrspace(3) global [2 x i32] undef, align 16", gm8_txt)
        self.assertIn("%ok = icmp ult i32 %i, 2040", gm8_txt)

        pcopy_txt = self.ll_texts["pcopy8.ll"]
        self.assertIn("@lds_y  = internal addrspace(3) global [64 x float] undef, align 16", pcopy_txt)
        self.assertIn("@lds_co = internal addrspace(3) global [64 x float] undef, align 16", pcopy_txt)
        self.assertIn("@lds_cg = internal addrspace(3) global [64 x float] undef, align 16", pcopy_txt)
        for tid in range(64):
            q00 = tid & 54
            for q in (tid, tid | 1, q00, q00 | 1, q00 | 8, q00 | 9):
                self.assertTrue(0 <= q < 64)

    def test_2c_audit_pcopy8_vs_fg8_table_descriptor_and_stride_contract(self):
        """
        Audit the R16 pcopy8.ll vs fg8_table.h descriptor contract and stride math.
        Confirms what pcopy8.ll expects vs what fill_pcopy8_table in fg8_table.h writes.
        """
        pcopy_ir = self.ll_texts["pcopy8.ll"]

        # 1. Verify what pcopy8.ll reads from %tbl:
        #    +32  (tb3+8):  T#cur  (8 dwords)
        #    +64  (tb3+16): T#out  (8 dwords)
        #    +96  (tb3+24): V#ycocg (4 dwords)
        #    +112 (tb3+28): params {mode, pitch, w, h} (4 dwords)
        self.assertIn("getelementptr i8, ptr addrspace(4) %tbl, i64 32", pcopy_ir)
        self.assertIn("getelementptr i8, ptr addrspace(4) %tbl, i64 64", pcopy_ir)
        self.assertIn("getelementptr i8, ptr addrspace(4) %tbl, i64 96", pcopy_ir)
        self.assertIn("getelementptr i8, ptr addrspace(4) %tbl, i64 112", pcopy_ir)
        self.assertIn("%mode = extractelement <4 x i32> %prm, i32 0", pcopy_ir)
        self.assertIn("%pitch_raw = extractelement <4 x i32> %prm, i32 1", pcopy_ir)
        self.assertIn("%xy_ok = and i1 %x_ok, %y_ok", pcopy_ir)

        # 2. Check fill_pcopy8_table in classic/source/fg8_table.h:
        #    Confirms R19-BUG-01/02 fix: fill_pcopy8_table writes make_v(tb3 + 24, copy_dst, nbytes) at +96 (tb3+24)
        #    and {ycocg_mode, eff_pitch, w, h} at tb3[28..31] (+112).
        self.assertIn("make_v(tb3 + 24, copy_dst, nbytes);", self.fg8_table_h)
        self.assertIn("tb3[28] = (uint32_t)ycocg_mode;", self.fg8_table_h)
        self.assertIn("tb3[29] = eff_pitch;", self.fg8_table_h)
        self.assertIn("tb3[30] = w;", self.fg8_table_h)
        self.assertIn("tb3[31] = h;", self.fg8_table_h)

        # 3. Confirms BUG-2: stride/bounds mismatch when pitch != w or h is not a multiple of 8 (1600x900)
        w, h, pitch = 1600, 900, 1920
        nbytes = (w * h * 2 + 255) & ~255  # 2,880,000 bytes
        max_boff_at_last_valid_pixel = ((h - 1) * pitch + (w - 1)) * 2  # 3,455,358 bytes
        self.assertGreater(
            max_boff_at_last_valid_pixel + 4,
            nbytes,
            "Confirms BUG-2: pcopy8.ll (y * pitch + x) * 2 overflows nbytes = w * h * 2 when pitch > w",
        )
        d_wy_900p = (900 + 7) // 8
        max_thread_y_900p = d_wy_900p * 8 - 1
        self.assertEqual(max_thread_y_900p, 903)
        self.assertGreaterEqual(max_thread_y_900p, h)

    # =========================================================================
    # INVARIANT 3: Bitfield Packing, Sign-Extension & YCoCg-R Color Space Range
    # =========================================================================

    def test_3a_ycocg_r_420_color_space_range_and_roundtrip(self):
        """
        Test YCoCg-R 4:2:0 encode/decode in both pcopy8.ll (GPU normalized float shader)
        and fg8_table.h (CPU integer lifting codec) across all 8 extreme RGB cube corners,
        all 64 pairs of corners, and a 3D RGB grid.
        """
        def q8_ir(v: float) -> int:
            s = sat_f32(v)
            return int(math.floor(f32(f32(s * 255.0) + 0.5)))

        def gpu_pcopy8_encode_quad(rgb00, rgb01, rgb10, rgb11):
            ys, cos, cgs = [], [], []
            for r_i, g_i, b_i in (rgb00, rgb01, rgb10, rgb11):
                r, g, b = f32(r_i / 255.0), f32(g_i / 255.0), f32(b_i / 255.0)
                mrb = f32(f32(r + b) * 0.5)
                yv = f32(f32(g + mrb) * 0.5)
                co_u = f32(f32(f32(r - b) * 0.5) + 0.5)
                cg_u = f32(f32(f32(g - mrb) * 0.5) + 0.5)
                self.assertTrue(-1e-6 <= yv <= 1.0 + 1e-6)
                self.assertTrue(-1e-6 <= co_u <= 1.0 + 1e-6)
                self.assertTrue(-1e-6 <= cg_u <= 1.0 + 1e-6)
                ys.append(yv)
                cos.append(co_u)
                cgs.append(cg_u)

            co_avg = f32(f32(f32(cos[0] + cos[1]) + f32(cos[2] + cos[3])) * 0.25)
            cg_avg = f32(f32(f32(cgs[0] + cgs[1]) + f32(cgs[2] + cgs[3])) * 0.25)
            qy0 = q8_ir(ys[0])
            qy1 = q8_ir(ys[1])
            qco = q8_ir(co_avg)
            qcg = q8_ir(cg_avg)
            self.assertTrue(0 <= qy0 <= 255 and 0 <= qy1 <= 255)
            self.assertTrue(0 <= qco <= 255 and 0 <= qcg <= 255)
            return qy0 | (qy1 << 8) | (qco << 16) | (qcg << 24)

        def gpu_pcopy8_decode_pair(pk: int):
            inv255 = hex_double_to_f32("0x3F70101020000000")
            cob = (pk >> 16) & 0xFF
            cgb = (pk >> 24) & 0xFF
            co_d = f32(f32(float(cob) * inv255) - 0.5)
            cg_d = f32(f32(float(cgb) * inv255) - 0.5)
            out = []
            for is_odd in (0, 1):
                yb = (pk >> (is_odd * 8)) & 0xFF
                yf = f32(float(yb) * inv255)
                mrb_d = f32(yf - cg_d)
                g_out = sat_f32(f32(yf + cg_d))
                r_out = sat_f32(f32(mrb_d + co_d))
                b_out = sat_f32(f32(mrb_d - co_d))
                out.append((round(r_out * 255.0), round(g_out * 255.0), round(b_out * 255.0)))
            return out[0], out[1]

        def cpu_ycocg_encode_quad(rgb00, rgb10, rgb01, rgb11):
            """Exact integer math from fg8_table.h ycocg420_compress_frame."""
            def to_ycocg(rgb):
                r, g, b = rgb
                co_h = (r - b) >> 1
                t = b + co_h
                cg_h = (g - t) >> 1
                y = clamp_i32(t + cg_h, 0, 255)
                co = clamp_i32(co_h + 128, 0, 255)
                cg = clamp_i32(cg_h + 128, 0, 255)
                return y, co, cg

            y00, co00, cg00 = to_ycocg(rgb00)
            y10, co10, cg10 = to_ycocg(rgb10)
            y01, co01, cg01 = to_ycocg(rgb01)
            y11, co11, cg11 = to_ycocg(rgb11)
            co_avg = clamp_i32((co00 + co10 + co01 + co11 + 2) >> 2, 0, 255)
            cg_avg = clamp_i32((cg00 + cg10 + cg01 + cg11 + 2) >> 2, 0, 255)
            chroma = (co_avg << 16) | (cg_avg << 24)
            return y00 | (y10 << 8) | chroma

        def cpu_ycocg_decode_pair(pk: int):
            """Exact integer math from fg8_table.h ycocg420_unpack_pixel."""
            co = ((pk >> 16) & 0xFF) - 128
            cg = ((pk >> 24) & 0xFF) - 128
            out = []
            for x_odd in (0, 1):
                y = ((pk >> 8) if x_odd else pk) & 0xFF
                t = y - cg
                g = clamp_i32(y + cg, 0, 255)
                b = clamp_i32(t - co, 0, 255)
                r = clamp_i32(t + co, 0, 255)
                out.append((r, g, b))
            return out[0], out[1]

        corners = [
            (0, 0, 0),
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 255),
            (255, 255, 255),
        ]

        for c0 in corners:
            for c1 in corners:
                pk_gpu = gpu_pcopy8_encode_quad(c0, c1, c0, c1)
                p0_g, p1_g = gpu_pcopy8_decode_pair(pk_gpu)
                pk_cpu = cpu_ycocg_encode_quad(c0, c1, c0, c1)
                p0_c, p1_c = cpu_ycocg_decode_pair(pk_cpu)

                if c0 == c1:
                    for ch in range(3):
                        self.assertLessEqual(abs(p0_g[ch] - c0[ch]), 2)
                        self.assertLessEqual(abs(p0_c[ch] - c0[ch]), 2)

        for r in range(0, 256, 17):
            for g in range(0, 256, 17):
                for b in range(0, 256, 17):
                    c = (r, g, b)
                    pk_gpu = gpu_pcopy8_encode_quad(c, c, c, c)
                    p0_g, p1_g = gpu_pcopy8_decode_pair(pk_gpu)
                    self.assertEqual(p0_g, p1_g)
                    self.assertLessEqual(abs(p0_g[0] - r), 2)
                    self.assertLessEqual(abs(p0_g[1] - g), 2)
                    self.assertLessEqual(abs(p0_g[2] - b), 2)

    def test_3b_motion_vector_bitfield_packing_and_mf8_dual_layer_sign_extension(self):
        """
        Verify @pack/@unx/@uny (16-bit s16 in me3/me2/fgs8), mf8.ll Pass 2 dual-layer
        float32 mantissa LSB bit-stealing (lines 714-735), and warp8.ll @sub_corner
        sign-extending unpack (lines 330-356).
        """
        # 1. @pack / @unx / @uny (16-bit signed dx, dy in me3.ll, me2.ll, me1.ll, fgs8.ll, mf8.ll)
        def pack16(dx: int, dy: int) -> int:
            return (dx & 0xFFFF) | ((dy & 0xFFFF) << 16)

        def unx16(p: int) -> int:
            return s16_from_u32(p)

        def uny16(p: int) -> int:
            return s16_from_u32(p >> 16)

        for dx in (-2048, -256, -128, -1, 0, 1, 127, 128, 256, 2047):
            for dy in (-2048, -256, -128, -1, 0, 1, 127, 128, 256, 2047):
                p = pack16(dx, dy)
                self.assertEqual(unx16(p), dx)
                self.assertEqual(uny16(p), dy)

        # 2. Verify exact IR instructions in mf8.ll and warp8.ll for dual-layer mantissa packing:
        #    In mf8.ll:
        #      %bxi_hi = and i32 %bxi_c, -4096   (clears low 12 bits of fx: bits [11:4] = win_cx2, bits [3:0] = win_mask)
        #      %byi_hi = and i32 %byi_c, -256    (clears low 8 bits of fy:  bits [7:0]  = win_cy2)
        self.assertIn("%bxi_hi = and i32 %bxi_c, -4096", self.ll_texts["mf8.ll"])
        self.assertIn("%byi_hi = and i32 %byi_c, -256", self.ll_texts["mf8.ll"])
        self.assertIn("%sx0 = shl i32 %bxi, 20", self.ll_texts["warp8.ll"])
        self.assertIn("%sx1 = ashr i32 %sx0, 24", self.ll_texts["warp8.ll"])
        self.assertIn("%sy0 = shl i32 %byi, 24", self.ll_texts["warp8.ll"])
        self.assertIn("%sy1 = ashr i32 %sy0, 24", self.ll_texts["warp8.ll"])

        def mf8_pack_dual_mantissa(vx1: float, vy1: float, win_cx2: int, win_cy2: int, win_mask: int):
            vx_c = f32(0.0625) if abs(f32(vx1)) < f32(0.0625) else f32(vx1)
            vy_c = f32(0.0625) if abs(f32(vy1)) < f32(0.0625) else f32(vy1)
            bxi_c = struct.unpack("<I", struct.pack("<f", vx_c))[0]
            byi_c = struct.unpack("<I", struct.pack("<f", vy_c))[0]
            bxi_hi = bxi_c & 0xFFFFF000  # -4096
            byi_hi = byi_c & 0xFFFFFF00  # -256
            cx2_m = clamp_i32(win_cx2, -128, 127) & 0xFF
            cy2_m = clamp_i32(win_cy2, -128, 127) & 0xFF
            bxi_spl = bxi_hi | (cx2_m << 4) | (win_mask & 0xF)
            byi_spl = byi_hi | cy2_m
            fx = struct.unpack("<f", struct.pack("<I", bxi_spl))[0]
            fy = struct.unpack("<f", struct.pack("<I", byi_spl))[0]
            return fx, fy

        def warp8_unpack_dual_mantissa(fx: float, fy: float):
            bxi = struct.unpack("<I", struct.pack("<f", fx))[0]
            byi = struct.unpack("<I", struct.pack("<f", fy))[0]
            win_mask = bxi & 0xF
            # Exact shift-and-ashr sign-extension from warp8.ll @sub_corner
            sx0 = (bxi << 20) & 0xFFFFFFFF
            sx1 = ashr32(sx0, 24)
            sy0 = (byi << 24) & 0xFFFFFFFF
            sy1 = ashr32(sy0, 24)

            b1x = bxi & 0xFFFFF000
            b1y = byi & 0xFFFFFF00
            v1x0 = struct.unpack("<f", struct.pack("<I", b1x))[0]
            v1y0 = struct.unpack("<f", struct.pack("<I", b1y))[0]
            v1x1 = f32(0.0) if abs(v1x0) <= f32(0.0625) else v1x0
            v1y1 = f32(0.0) if abs(v1y0) <= f32(0.0625) else v1y0
            return v1x1, v1y1, sx1, sy1, win_mask

        for vx1, vy1 in ((-192.0, -108.0), (-15.5, 32.25), (0.0, 0.0), (64.75, -96.5), (192.0, 108.0)):
            for cx2 in (-128, -64, -1, 0, 1, 64, 127):
                for cy2 in (-128, -64, -1, 0, 1, 64, 127):
                    for mask in range(1, 16):
                        fx_p, fy_p = mf8_pack_dual_mantissa(vx1, vy1, cx2, cy2, mask)
                        self.assertFalse(math.isnan(fx_p) or math.isinf(fx_p))
                        self.assertFalse(math.isnan(fy_p) or math.isinf(fy_p))
                        v1x, v1y, u_cx2, u_cy2, u_mask = warp8_unpack_dual_mantissa(fx_p, fy_p)
                        # Secondary layer (cx2, cy2) and 4-bit quadrant mask must unpack with zero sign bleed!
                        self.assertEqual(u_cx2, cx2)
                        self.assertEqual(u_cy2, cy2)
                        self.assertEqual(u_mask, mask)
                        # Primary vector error from 12-bit/8-bit mantissa truncation is bounded (< 0.1 px)
                        self.assertLessEqual(abs(v1x - vx1), 0.1)
                        self.assertLessEqual(abs(v1y - vy1), 0.01)

    # =========================================================================
    # INVARIANT 4: GCN gfx700 Hardware Limits & Fg8Table Descriptor Consistency
    # =========================================================================

    def test_4a_gcn_gfx700_registers_lds_scratch_and_code_region_budget(self):
        """
        Verify VGPR <= 64, SGPR <= 104, LDS <= 32768, Scratch == 0 across all 27 shaders
        in classic/source/shaders.h, and verify the 17-shader Mode 8 code region budget.
        """
        rsrc1_map = {
            m.group(1).lower(): int(m.group(2), 16)
            for m in re.finditer(r"#define\s+(\w+)_RSRC1\s+(0x[0-9a-fA-F]+)", self.shaders_h)
        }
        rsrc2_map = {
            m.group(1).lower(): int(m.group(2), 16)
            for m in re.finditer(r"#define\s+(\w+)_RSRC2\s+(0x[0-9a-fA-F]+)", self.shaders_h)
        }
        nwords_map = {
            m.group(1).lower(): int(m.group(2))
            for m in re.finditer(r"#define\s+(\w+)_NWORDS\s+(\d+)", self.shaders_h)
        }

        self.assertEqual(len(rsrc1_map), 27, f"Expected 27 shaders in shaders.h, got {len(rsrc1_map)}")

        mode8_shaders = {
            "pyr8", "me3", "me2", "me1", "mf8", "gm8", "fgs8", "phase8",
            "warp8", "warp8g", "rcas8", "gv8", "gvc8", "gvz8", "ui8", "osd8",
            "pcopy8", "dump8",
        }

        for name in sorted(rsrc1_map.keys()):
            r1 = rsrc1_map[name]
            r2 = rsrc2_map[name]
            nw = nwords_map[name]

            vgpr_gran = r1 & 0x3F
            sgpr_gran = (r1 >> 6) & 0x0F
            vgprs = (vgpr_gran + 1) * 4
            sgprs = (sgpr_gran + 1) * 8

            scratch_en = r2 & 1
            user_sgpr = (r2 >> 1) & 0x1F
            tgid_x_en = (r2 >> 7) & 1
            lds_gran = (r2 >> 15) & 0x1FF
            lds_bytes = lds_gran * 256  # Sea Islands (gfx700) CS: 64 dwords = 256 B per unit

            # 1. Zero scratch spilling (CRITICAL: plugin binds no scratch buffer)
            self.assertEqual(scratch_en, 0, f"Shader {name} has SCRATCH_EN=1 (stack spill!)")
            # 2. Register file limits on Bonaire / Liverpool (gfx700)
            self.assertLessEqual(vgprs, 64, f"Shader {name} VGPRs={vgprs} > 64")
            self.assertLessEqual(sgprs, 104, f"Shader {name} SGPRs={sgprs} > 104")
            # 3. LDS limit (32 KB per workgroup on gfx700)
            self.assertLessEqual(lds_bytes, 32768, f"Shader {name} LDS={lds_bytes} > 32768")
            # 4. User SGPRs: all 18 Mode 8 shaders use 2 user SGPRs (64-bit %tbl pointer);
            #    legacy Mode 1-7 shaders use <= 16 user SGPRs.
            if name in mode8_shaders:
                self.assertEqual(user_sgpr, 2, f"Mode 8 shader {name} USER_SGPR={user_sgpr} != 2")
            else:
                self.assertLessEqual(user_sgpr, 16, f"Legacy shader {name} USER_SGPR={user_sgpr} > 16")
            self.assertEqual(tgid_x_en, 1, f"Shader {name} TGID_X_EN != 1")
            self.assertGreater(nw, 0)

        # Verify the 17 Mode 8 shaders copied into [0x8000, DCB8_OFF = 0x1C000) in main.c (gpu_init's code[17]: dump8 is
        # always placed, ui8 never is)
        mode8_region_shaders = [
            "pyr8", "me3", "me2", "me1", "phase8", "warp8", "dump8", "gm8",
            "fgs8", "mf8", "gv8", "gvc8", "warp8g", "gvz8", "rcas8", "osd8",
            "pcopy8",
        ]
        m_code = re.search(r"const uint32_t \*code\[17\] = \{([^}]*)\}", self.main_c)
        self.assertIsNotNone(m_code)
        self.assertEqual([x.strip()[:-len("_code")] for x in m_code.group(1).split(",") if x.strip()], mode8_region_shaders)
        for s in mode8_region_shaders:
            # gen_shaders.py pads every shader to a multiple of 64 words with >= 16 s_endpgm words after the code
            self.assertEqual(nwords_map[s] % 64, 0, f"{s}: NWORDS not a multiple of 64")
        total_aligned = 0
        for s in mode8_region_shaders:
            code_bytes = nwords_map[s] * 4
            aligned_bytes = (code_bytes + 255) & ~255
            total_aligned += aligned_bytes

        end_off = 0x8000 + total_aligned
        dcb8_off = 0x1C000
        # layout snapshot of the current shaders.h (fp32 preserve-sign denormal attribute: mad/mac fusion shrank
        # warp8 by 512 B and rcas8 by 256 B; rcas8's guarded stores of unchanged lanes added 256 B back, its bit-5
        # real-frame static handling another 256 B; pyr8's still-pixel counter update 512 B; phase8's scene-cut history
        # reset 256 B; counting dump8 (placed) instead of ui8 (never placed) 256 B back; gen_shaders.py padding to a
        # multiple of 64 words instead of ceil64 + 16 words, 2560 B back over 10 of the 17 shaders; warp8's v_mad_f32
        # through llvm.amdgcn.fmad.ftz 256 B back; warp8's @crw in factored form 256 B back); the budget is the
        # assertLessEqual against DCB8_OFF below
        self.assertEqual(total_aligned, 0x13200)
        self.assertEqual(end_off, 0x1B200)
        self.assertLessEqual(
            end_off,
            dcb8_off,
            f"Mode 8 shaders overflow DCB8_OFF: end=0x{end_off:X} > 0x{dcb8_off:X}",
        )

        # Also check gen_shaders.py budget list: confirm BUG-4 fix (pcopy8 included in list in gen_shaders.py)
        m_list = re.search(r"for\s+n\s+in\s+\(([^)]+)\)", self.gen_shaders_text)
        self.assertIsNotNone(m_list)
        listed_in_gen = [x.strip().strip("'\"") for x in m_list.group(1).split(",")]
        self.assertIn(
            "pcopy8",
            listed_in_gen,
            "Expected gen_shaders.py to include pcopy8 (R19-BUG-04 fixed)",
        )
        self.assertEqual(listed_in_gen, mode8_region_shaders, "gen_shaders.py must check the set main.c places")

    def test_4b_fg8_table_descriptor_offsets_match_llvm_ir_geps(self):
        """
        Cross-verify every byte offset into `%tbl` across all 18 Mode 8 `.ll` shaders
        against `Fg8Table` in `classic/source/fg8_table.h` and `classic/source/gv.c`.
        """
        # Valid descriptor slot byte offsets defined in fill_table8_wh (fg8_table.h) + OSD/GV tables:
        valid_fg8_slots = {
            32,   # +0x20: T#cur (tb+8)
            48,   # +0x30: gvc8 / gv8 table slot
            64,   # +0x40: T#out (tb+16)
            80,   # +0x50: gvc8 / gv8 table slot
            96,   # +0x60: S# sampler (tb+24) / pcopy8 params
            112,  # +0x70: consts {r, mid_idx, flags, hud} (tb+28)
            128,  # +0x80: V#L1cur (tb+32) / pcopy8 V#ycocg
            144,  # +0x90: V#L1prev (tb+36)
            160,  # +0xA0: V#L2cur (tb+40)
            176,  # +0xB0: V#L2prev (tb+44)
            192,  # +0xC0: V#L3cur (tb+48)
            208,  # +0xD0: V#L3prev (tb+52)
            224,  # +0xE0: V#V3 (tb+56)
            240,  # +0xF0: V#V2 (tb+60)
            256,  # +0x100: V#Fcur (tb+64)
            272,  # +0x110: V#Fprev (tb+68)
            288,  # +0x120: V#misc (tb+72)
            304,  # +0x130: V#mon (tb+76)
            320,  # +0x140: V#S2 (tb+80)
            336,  # +0x150: V#GK (tb+84)
            352,  # +0x160: dump8 V#dump (tb+88)
            368,  # +0x170: V#stats (tb+92)
            384,  # +0x180: V#FT (tb+96)
            400,  # +0x190: gv_fill_table V#G (tb+100)
            416,  # +0x1A0: gv_fill_table V#GS (tb+104)
            432,  # +0x1B0: gv_fill_table V#GC (tb+108)
            448,  # +0x1C0: gv_fill_table V#Gcur (tb+112)
            464,  # +0x1D0: gv_fill_table V#Gprev (tb+116)
            480,  # +0x1E0: gv_fill_table V#GV (tb+120)
            496,  # +0x1F0: gv_fill_table V#GM (tb+124)
            512,  # +0x200: osd8 V#palette (tb+128)
            528,  # +0x210: osd8 V#pixels (tb+132)
            544,  # +0x220: osd8 / warp8 / rcas8 overlay rect & rcas config (tb+136)
            560,  # +0x230: osd8 V#styles (tb+140)
            576,  # +0x240: osd8 V#chars (tb+144)
        }

        mode8_ll_files = [
            "pyr8.ll", "me3.ll", "me2.ll", "me1.ll", "mf8.ll", "gm8.ll", "fgs8.ll", "phase8.ll",
            "warp8.ll", "warp8g.ll", "rcas8.ll", "ui8.ll", "osd8.ll", "dump8.ll",
            "gv8.ll", "gvc8.ll", "gvz8.ll", "pcopy8.ll",
        ]

        for sname in mode8_ll_files:
            txt = self.ll_texts[sname]
            found = {
                int(m.group(1))
                for m in re.finditer(
                    r"getelementptr\s+(?:inbounds\s+)?i8,\s*ptr\s+addrspace\(4\)\s+%tbl,\s*i64\s+(\d+)",
                    txt,
                )
            }
            self.assertTrue(len(found) > 0, f"No %tbl GEPs found in {sname}")
            for off in found:
                # Every descriptor offset must be 16-byte aligned (4 dwords) and in valid_fg8_slots
                self.assertEqual(off % 16, 0, f"Unaligned %tbl offset {off} in {sname}")
                self.assertIn(off, valid_fg8_slots, f"Unknown %tbl offset {off} (+0x{off:X}) in {sname}")

    def test_4c_gcn_tsharp_dfmt_channel_count_vs_shader_dmask(self):
        """
        Verify GCN (gfx700 / Sea Islands) T# IMG_DATA_FORMAT (dfmt) hardware channel count
        against shader image.store.2d dmask and image.load.2d / image.sample.lz.2d channel reads
        in pcopy8.ll, warp8.ll, warp8g.ll, and pyr8.ll (prevents v2.33 dfmt=3 green strobe).
        """
        # Hardware channel count per GCN Sea Islands IMG_DATA_FORMAT (dfmt, T#[1][25:20]):
        gcn_dfmt_channels = {
            1: 1,   # IMG_DATA_FORMAT_8
            2: 1,   # IMG_DATA_FORMAT_16
            3: 2,   # IMG_DATA_FORMAT_8_8 (2 channels X,Y ONLY - drops channel Z!)
            4: 1,   # IMG_DATA_FORMAT_32
            5: 2,   # IMG_DATA_FORMAT_16_16
            10: 4,  # IMG_DATA_FORMAT_8_8_8_8
            12: 4,  # IMG_DATA_FORMAT_16_16_16_16
            14: 4,  # IMG_DATA_FORMAT_32_32_32_32
            16: 3,  # IMG_DATA_FORMAT_5_6_5 (3 channels X,Y,Z in 16 bpp)
        }

        # Extract make_t_ex() and make_t_pbuf() from classic/source/fg8_table.h
        m_ex = re.search(
            r"static\s+(?:inline\s+)?void\s+make_t_ex\s*\([^)]*\)\s*\{(.*?)\n\}",
            self.fg8_table_h,
            re.S,
        )
        m_fn = re.search(
            r"static\s+(?:inline\s+)?void\s+make_t_pbuf\s*\([^)]*\)\s*\{(.*?)\n\}",
            self.fg8_table_h,
            re.S,
        )
        self.assertIsNotNone(m_ex, "make_t_ex not found in classic/source/fg8_table.h")
        self.assertIsNotNone(m_fn, "make_t_pbuf not found in classic/source/fg8_table.h")
        fn_body = m_fn.group(1)

        # Parse dfmt in fmt == 1 (16-bit half-footprint t[1] in make_t_pbuf) and fmt == 0 (g_t_dfmt in make_t_ex)
        m_fmt1 = re.search(r"t\[1\]\s*=[^;]*\(\s*(\d+)u\s*<<\s*20\s*\)", fn_body)
        m_fmt0 = re.search(r"g_t_dfmt\s*=\s*(\d+)u?", self.fg8_table_h)
        self.assertIsNotNone(m_fmt1, "Could not parse t[1] dfmt in make_t_pbuf(fmt=1)")
        self.assertIsNotNone(m_fmt0, "Could not parse g_t_dfmt for make_t_ex(fmt=0)")
        self.assertIn("(g_t_dfmt << 20)", m_ex.group(1))
        dfmt_fmt1 = int(m_fmt1.group(1))
        dfmt_fmt0 = int(m_fmt0.group(1))

        self.assertIn(dfmt_fmt1, gcn_dfmt_channels, f"Unknown GCN dfmt={dfmt_fmt1} in make_t_pbuf(fmt=1)")
        self.assertIn(dfmt_fmt0, gcn_dfmt_channels, f"Unknown GCN dfmt={dfmt_fmt0} in make_t_pbuf(fmt=0)")

        # In pcopy8.ll (do_copy, mode==0), image.store.2d writes <4 x float> %c (R, G, B, A) with dmask=15 to %to (T#out).
        # In warp8.ll / warp8g.ll, @taps / @cr samples <4 x float> (R, G, B, A) with dmask=15 from %tp (T#prev).
        pcopy_ir = self.ll_texts["pcopy8.ll"]
        self.assertIn(
            "call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %c, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)",
            pcopy_ir,
        )

        for wname in ("warp8.ll", "warp8g.ll"):
            wir = self.ll_texts[wname]
            self.assertIn("call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15,", wir)

        # CRITICAL INVARIANT: fmt==1 MUST have >= 3 hardware RGB channels (never dfmt=3 8_8 which has only 2 channels!)
        self.assertNotEqual(
            dfmt_fmt1,
            3,
            "REGRESSION: make_t_pbuf(fmt=1) uses dfmt=3 (8_8, 2 channels), dropping channel Z (Blue) -> 60 Hz color strobe!",
        )
        self.assertGreaterEqual(
            gcn_dfmt_channels[dfmt_fmt1],
            3,
            f"make_t_pbuf(fmt=1) dfmt={dfmt_fmt1} has only {gcn_dfmt_channels[dfmt_fmt1]} channels, but RGB T#prev requires >= 3!",
        )
        self.assertEqual(dfmt_fmt1, 16, "Expected dfmt=16 (IMG_DATA_FORMAT_5_6_5, 3 channels, 16 bpp) for fmt==1")
        self.assertGreaterEqual(gcn_dfmt_channels[dfmt_fmt0], 4)
        self.assertEqual(dfmt_fmt0, 10, "Expected dfmt=10 (IMG_DATA_FORMAT_8_8_8_8, 4 channels, 32 bpp) for fmt==0")

        # Simulate hardware IMG_DATA_FORMAT_5_6_5 (5-bit R, 6-bit G, 5-bit B UNORM) store & sample in pcopy8 -> warp8:
        def q_unorm(v: float, bits: int) -> float:
            mx = (1 << bits) - 1
            q = int(math.floor(sat_f32(v) * mx + 0.5))
            return f32(q / float(mx))

        def sim_pcopy8_warp8_hw_format(rgb, dfmt: int):
            nch = gcn_dfmt_channels[dfmt]
            r0, g0, b0 = [c / 255.0 for c in rgb]
            if dfmt == 16:  # 5_6_5 (3 channels: 5b R, 6b G, 5b B)
                r_s, g_s, b_s = q_unorm(r0, 5), q_unorm(g0, 6), q_unorm(b0, 5)
            elif dfmt == 3:  # 8_8 (2 channels: drops Z/Blue to 0.0!)
                r_s, g_s, b_s = q_unorm(r0, 8), q_unorm(g0, 8), (0.0 if nch < 3 else q_unorm(b0, 8))
            else:
                r_s, g_s, b_s = q_unorm(r0, 8), q_unorm(g0, 8), q_unorm(b0, 8)
            return (round(r_s * 255), round(g_s * 255), round(b_s * 255))

        for rgb in ((255, 255, 255), (180, 180, 180), (96, 96, 96), (220, 50, 50), (50, 80, 220)):
            r_out, g_out, b_out = sim_pcopy8_warp8_hw_format(rgb, dfmt_fmt1)
            self.assertLessEqual(abs(r_out - rgb[0]), 8, f"R channel corrupted for {rgb}: got {(r_out, g_out, b_out)}")
            self.assertLessEqual(abs(g_out - rgb[1]), 4, f"G channel corrupted for {rgb}: got {(r_out, g_out, b_out)}")
            self.assertLessEqual(abs(b_out - rgb[2]), 8, f"B channel corrupted for {rgb}: got {(r_out, g_out, b_out)}")

    def test_4d_compiled_me1_bit_exact_vs_python_port(self):
        """
        Compile classic/shaders/me1.ll directly with clang-22 and verify 100% bit-for-bit
        float32 identity (0 / 16,320 word diffs in FT) against tools/bench/vcfg_motion.py::me1.
        """
        import shutil
        import subprocess
        import tempfile
        import numpy as np

        clang_bin = shutil.which("clang-22") or "/usr/bin/clang-22"
        if not Path(clang_bin).exists() or sys.platform != "linux":
            self.skipTest("clang-22 Linux host required for native LLVM IR shader execution")

        sys.path.insert(0, str(REPO_ROOT / "tools" / "bench"))
        import vcfg_motion as vm

        c_wrapper = r"""
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef int32_t v2i32 __attribute__((vector_size(8)));
typedef int32_t v3i32 __attribute__((vector_size(12)));
typedef int32_t v4i32 __attribute__((vector_size(16)));
typedef float   v2f32 __attribute__((vector_size(8)));

#if defined(__aarch64__)
typedef struct { uint64_t regs[22]; } fiber_ctx_t;
__attribute__((noinline)) void fiber_switch(fiber_ctx_t *from, const fiber_ctx_t *to) {
    __asm__ volatile(
        "stp x19, x20, [%0, #0]\n\t"
        "stp x21, x22, [%0, #16]\n\t"
        "stp x23, x24, [%0, #32]\n\t"
        "stp x25, x26, [%0, #48]\n\t"
        "stp x27, x28, [%0, #64]\n\t"
        "stp x29, x30, [%0, #80]\n\t"
        "mov x9, sp\n\t"
        "str x9, [%0, #96]\n\t"
        "stp d8,  d9,  [%0, #104]\n\t"
        "stp d10, d11, [%0, #120]\n\t"
        "stp d12, d13, [%0, #136]\n\t"
        "stp d14, d15, [%0, #152]\n\t"
        "ldp x19, x20, [%1, #0]\n\t"
        "ldp x21, x22, [%1, #16]\n\t"
        "ldp x23, x24, [%1, #32]\n\t"
        "ldp x25, x26, [%1, #48]\n\t"
        "ldp x27, x28, [%1, #64]\n\t"
        "ldp x29, x30, [%1, #80]\n\t"
        "ldr x9, [%1, #96]\n\t"
        "mov sp, x9\n\t"
        "ldp d8,  d9,  [%1, #104]\n\t"
        "ldp d10, d11, [%1, #120]\n\t"
        "ldp d12, d13, [%1, #136]\n\t"
        "ldp d14, d15, [%1, #152]\n\t"
        : : "r"(from), "r"(to) : "x9", "memory"
    );
}
#endif

static fiber_ctx_t g_ctx[64];
static fiber_ctx_t g_main_ctx;
static uint8_t     g_stacks[64][16384] __attribute__((aligned(16)));
static int         g_cur_tid = 0;
static const uint8_t *g_cur_tbl = NULL;
static int32_t     g_cur_gx = 0;
static int32_t     g_cur_gy = 0;

extern void shader_main(const uint8_t *tbl, int32_t gx, int32_t gy, int32_t tid);

void sim_barrier(void) {
    int prev = g_cur_tid;
    int next = (prev + 1) & 63;
    g_cur_tid = next;
    fiber_switch(&g_ctx[prev], &g_ctx[next]);
}

static void lane_trampoline(void) {
    int tid = g_cur_tid;
    shader_main(g_cur_tbl, g_cur_gx, g_cur_gy, tid);
    if (tid < 63) {
        g_cur_tid = tid + 1;
        fiber_switch(&g_ctx[tid], &g_ctx[tid + 1]);
    } else {
        fiber_switch(&g_ctx[63], &g_main_ctx);
    }
}

static inline const uint8_t *vsharp_ptr(v4i32 v, int32_t off, int32_t soff, uint32_t *nbytes_out) {
    uint64_t lo = (uint32_t)v[0];
    uint64_t hi = (uint32_t)(v[1] & 0xFFFF);
    uint64_t base = lo | (hi << 32);
    *nbytes_out = (uint32_t)v[2];
    int64_t tot = (int64_t)off + (int64_t)soff;
    if (tot < 0 || (uint64_t)tot >= *nbytes_out) return NULL;
    return (const uint8_t *)(uintptr_t)(base + (uint64_t)tot);
}

int32_t sim_load_i32(v4i32 v, int32_t off, int32_t soff, int32_t glc) {
    uint32_t nb = 0;
    const uint8_t *p = vsharp_ptr(v, off, soff, &nb);
    if (!p) return 0;
    int32_t out = 0;
    memcpy(&out, p, 4);
    return out;
}

v2i32 sim_load_v2i32(v4i32 v, int32_t off, int32_t soff, int32_t glc) {
    uint32_t nb = 0;
    const uint8_t *p = vsharp_ptr(v, off, soff, &nb);
    v2i32 out = {0, 0};
    if (p) memcpy(&out, p, 8);
    return out;
}

v3i32 sim_load_v3i32(v4i32 v, int32_t off, int32_t soff, int32_t glc) {
    uint32_t nb = 0;
    const uint8_t *p = vsharp_ptr(v, off, soff, &nb);
    int32_t tmp[3] = {0, 0, 0};
    if (p) memcpy(tmp, p, 12);
    v3i32 out = {tmp[0], tmp[1], tmp[2]};
    return out;
}

v2f32 sim_load_v2f32(v4i32 v, int32_t off, int32_t soff, int32_t glc) {
    uint32_t nb = 0;
    const uint8_t *p = vsharp_ptr(v, off, soff, &nb);
    v2f32 out = {0.0f, 0.0f};
    if (p) memcpy(&out, p, 8);
    return out;
}

void sim_store_i32(int32_t val, v4i32 v, int32_t off, int32_t soff, int32_t glc) {
    uint32_t nb = 0;
    uint8_t *p = (uint8_t *)vsharp_ptr(v, off, soff, &nb);
    if (p) memcpy(p, &val, 4);
}

void sim_store_v2f32(v2f32 val, v4i32 v, int32_t off, int32_t soff, int32_t glc) {
    uint32_t nb = 0;
    uint8_t *p = (uint8_t *)vsharp_ptr(v, off, soff, &nb);
    if (p) memcpy(p, &val, 8);
}

int32_t sim_atomic_add_i32(int32_t val, v4i32 v, int32_t off, int32_t soff, int32_t glc) {
    uint32_t nb = 0;
    int32_t *p = (int32_t *)vsharp_ptr(v, off, soff, &nb);
    if (!p) return 0;
    int32_t old = *p;
    *p = old + val;
    return old;
}

int32_t sim_alignbyte(int32_t hi, int32_t lo, int32_t sel) {
    uint64_t comb = ((uint64_t)(uint32_t)hi << 32) | (uint64_t)(uint32_t)lo;
    uint32_t sh = (uint32_t)(sel & 3) * 8u;
    return (int32_t)(uint32_t)(comb >> sh);
}

int32_t sim_sad_u8(int32_t a, int32_t b, int32_t acc) {
    uint32_t ua = (uint32_t)a, ub = (uint32_t)b;
    int32_t s = acc;
    for (int i = 0; i < 4; i++) {
        int da = (int)((ua >> (i * 8)) & 0xFFu);
        int db = (int)((ub >> (i * 8)) & 0xFFu);
        s += (da >= db) ? (da - db) : (db - da);
    }
    return s;
}

void run_dispatch(const uint8_t *tbl, int32_t ngx, int32_t ngy) {
    g_cur_tbl = tbl;
    for (int32_t gy = 0; gy < ngy; gy++) {
        for (int32_t gx = 0; gx < ngx; gx++) {
            g_cur_gx = gx;
            g_cur_gy = gy;
            for (int t = 0; t < 64; t++) {
                memset(&g_ctx[t], 0, sizeof(g_ctx[t]));
                uint64_t sp = (uint64_t)(uintptr_t)(&g_stacks[t][16384]) & ~15ULL;
                g_ctx[t].regs[11] = (uint64_t)(uintptr_t)lane_trampoline;
                g_ctx[t].regs[12] = sp;
            }
            g_cur_tid = 0;
            fiber_switch(&g_main_ctx, &g_ctx[0]);
        }
    }
}
"""
        def ll_to_host(ll_src: str) -> str:
            s = re.sub(r'target triple = "amdgcn--"', "", ll_src)
            s = s.replace("addrspace(3)", "").replace("addrspace(4)", "")
            s = s.replace("define amdgpu_cs void @main(", "define void @shader_main(")
            s = s.replace(" inreg ", " ")
            s = s.replace('syncscope("workgroup") ', "")
            s = s.replace("@llvm.amdgcn.s.barrier", "@sim_barrier")
            s = s.replace("@llvm.amdgcn.raw.buffer.load.i32", "@sim_load_i32")
            s = s.replace("@llvm.amdgcn.raw.buffer.load.v2i32", "@sim_load_v2i32")
            s = s.replace("@llvm.amdgcn.raw.buffer.load.v3i32", "@sim_load_v3i32")
            s = s.replace("@llvm.amdgcn.raw.buffer.load.v2f32", "@sim_load_v2f32")
            s = s.replace("@llvm.amdgcn.raw.buffer.store.i32", "@sim_store_i32")
            s = s.replace("@llvm.amdgcn.raw.buffer.store.v2f32", "@sim_store_v2f32")
            s = s.replace("@llvm.amdgcn.raw.buffer.atomic.add.i32", "@sim_atomic_add_i32")
            s = s.replace("@llvm.amdgcn.alignbyte", "@sim_alignbyte")
            s = s.replace("@llvm.amdgcn.sad.u8", "@sim_sad_u8")
            s = s.replace(" #0 {", " {")
            s = re.sub(r"attributes #0 = \{[^}]*\}", "", s)
            return s.replace("i32 immarg", "i32")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "wrapper.c").write_text(c_wrapper, encoding="utf-8")
            me1_ll_str = (SHADERS_DIR / "me1.ll").read_text(encoding="utf-8")
            mf8_ll_str = (SHADERS_DIR / "mf8.ll").read_text(encoding="utf-8")
            (tmp / "me1_host.ll").write_text(ll_to_host(me1_ll_str), encoding="utf-8")
            (tmp / "mf8_host.ll").write_text(ll_to_host(mf8_ll_str), encoding="utf-8")
            so_path = tmp / "libme1_sim.so"
            so_mf8_path = tmp / "libmf8_sim.so"
            subprocess.run(
                [clang_bin, "-O2", "-fPIC", "-shared", "-Wno-override-module",
                 str(tmp / "wrapper.c"), str(tmp / "me1_host.ll"), "-o", str(so_path), "-lm"],
                check=True,
            )
            subprocess.run(
                [clang_bin, "-O2", "-fPIC", "-shared", "-Wno-override-module",
                 str(tmp / "wrapper.c"), str(tmp / "mf8_host.ll"), "-o", str(so_mf8_path), "-lm"],
                check=True,
            )
            lib = ctypes.CDLL(str(so_path))
            lib.run_dispatch.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32]
            lib.run_dispatch.restype = None
            lib_mf8 = ctypes.CDLL(str(so_mf8_path))
            lib_mf8.run_dispatch.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32]
            lib_mf8.run_dispatch.restype = None

            def set_v(tbl, dword_off, buf):
                addr = buf.ctypes.data
                tbl[dword_off + 0] = addr & 0xFFFFFFFF
                tbl[dword_off + 1] = (addr >> 32) & 0xFFFF
                tbl[dword_off + 2] = buf.nbytes
                tbl[dword_off + 3] = 0

            # Build test pairs: real console dump CUSA08519 #19 (if present) + synthetic textured scene
            pairs = []
            real_a = Path("/home/melnikoff/vcfg-pipeline/runtime/research/head-occlusion-20261023/bench_CUSA08519/bench_0019_a.rgba")
            real_b = Path("/home/melnikoff/vcfg-pipeline/runtime/research/head-occlusion-20261023/bench_CUSA08519/bench_0019_b.rgba")
            if real_a.exists() and real_b.exists():
                def load_rgb(p):
                    raw = np.fromfile(p, dtype="<u2").reshape(1080, 1920, 4)
                    return raw[:, :, :3].astype(np.float32) * np.float32(1.0 / 65535.0)
                pairs.append(("CUSA08519_19", load_rgb(real_a), load_rgb(real_b)))

            rng = np.random.default_rng(42)
            syn_p = rng.uniform(0.05, 0.95, (1080, 1920, 3)).astype(np.float32)
            syn_c = np.roll(syn_p, shift=(-6, 12), axis=(0, 1))
            pairs.append(("synthetic_textured", syn_p, syn_c))

            for label, A, B in pairs:
                L1p, L2p, L3p = vm.pyr8_shader(A)
                L1c, L2c, L3c = vm.pyr8_shader(B)
                V3, sum_c, sum_p = vm.me3_dual(L3p, L3c)
                V2, S2 = vm.me2(L2p, L2c, V3)
                gm = vm.gm8(V2, S2)
                GK, _ = vm.fgs8(L2p, L2c, V2, S2, gm)
                prevF_zero = np.zeros((68, 120, 2), dtype=np.float32)
                FT_py, cutn_py, _ = vm.me1(L1p, L1c, V2, GK, gm, sum_c, sum_p, prevF=prevF_zero)

                L1c_buf = np.zeros(960 * 540 + 16, dtype=np.uint8)
                L1p_buf = np.zeros(960 * 540 + 16, dtype=np.uint8)
                L1c_buf[:960 * 540] = L1c.astype(np.uint8).ravel()
                L1p_buf[:960 * 540] = L1p.astype(np.uint8).ravel()
                v2_x = (V2[:, :, 0].astype(np.int32) & 0xFFFF).astype(np.uint32)
                v2_y = (V2[:, :, 1].astype(np.int32) & 0xFFFF).astype(np.uint32)
                V2_buf = (v2_x | (v2_y << np.uint32(16))).ravel().copy()
                GK_buf = GK.astype(np.uint32).ravel().copy()
                Fprev_buf = np.zeros(120 * 68 * 2, dtype=np.float32)
                misc_buf = np.zeros(8, dtype=np.int32)
                misc_buf[2] = int(sum_c)
                misc_buf[3] = int(sum_p)
                misc_buf[4] = int(gm[0])
                misc_buf[5] = int(gm[1])
                misc_buf[6] = int(vm._LAST_FGCNT)
                FT_sim = np.zeros(120 * 68 * 2, dtype=np.float32)

                tbl = np.zeros(160, dtype=np.uint32)
                tbl[28] = 0
                tbl[30] = 0
                tbl[140] = 960
                tbl[141] = 540
                set_v(tbl, 32, L1c_buf)
                set_v(tbl, 36, L1p_buf)
                set_v(tbl, 60, V2_buf)
                set_v(tbl, 68, Fprev_buf)
                set_v(tbl, 72, misc_buf)
                set_v(tbl, 84, GK_buf)
                set_v(tbl, 96, FT_sim)

                lib.run_dispatch(tbl.ctypes.data, 30, 68)
                ft_diffs = int((FT_sim.view(np.uint32) != FT_py.ravel().view(np.uint32)).sum())
                self.assertEqual(
                    ft_diffs,
                    0,
                    f"Compiled me1.ll differs from vcfg_motion.py::me1 on {label}: {ft_diffs}/16320 float32 words differ",
                )
                self.assertEqual(int(misc_buf[0]), int(cutn_py), f"cutn mismatch on {label}")

                F_py = vm.mf8(L1p, L1c, FT_py, GK, V2, gm, bool(misc_buf[7]))
                F_sim = np.zeros(120 * 68 * 2, dtype=np.float32)
                set_v(tbl, 64, F_sim)
                lib_mf8.run_dispatch(tbl.ctypes.data, 30, 68)
                f_diffs = int((F_sim.view(np.uint32) != F_py.ravel().view(np.uint32)).sum())
                self.assertEqual(
                    f_diffs,
                    0,
                    f"Compiled mf8.ll differs from vcfg_motion.py::mf8 on {label}: {f_diffs}/16320 float32 words differ",
                )

    def test_4e_prx_reproducible_no_worktree_path(self):
        """
        Verify built vcfg_classic.prx and vcfg_classic.oelf do not embed absolute worktree paths
        from create-fself -in=..., and build scripts invoke create-fself with relative -in= paths.
        """
        for rel in ("build/classic/vcfg_classic.prx", "build/classic/vcfg_classic.oelf"):
            p = REPO_ROOT / rel
            if p.exists():
                raw = p.read_bytes()
                self.assertEqual(
                    raw.find(str(REPO_ROOT).encode("utf-8")),
                    -1,
                    f"{rel} embeds absolute worktree root path {REPO_ROOT}",
                )
        for script_rel in ("classic/build.ps1", "classic/build.sh", "build.ps1"):
            stext = (REPO_ROOT / script_rel).read_text(encoding="utf-8")
            self.assertNotIn("-in=$out", stext, f"{script_rel} passes absolute $out path to create-fself -in=")
            self.assertNotIn('-in="$B/', stext, f"{script_rel} passes absolute $B path to create-fself -in=")
            self.assertNotIn("-in=$elf", stext, f"{script_rel} passes absolute $elf path to create-fself -in=")


if __name__ == "__main__":
    unittest.main(verbosity=2)
