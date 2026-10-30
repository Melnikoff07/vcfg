#!/usr/bin/env python3
"""warp8 @crw: the cubic weights of the coherent path's 5-tap resample in factored form.

The B = 0 cubic with parameter c (Catmull-Rom at c = 0.5, Sharp at 0.75, faded towards the Hermite single tap at
large vectors) has, with g = 1 - f and cfg = c f g:
    w0 = -cfg g,  w3 = -cfg f,  w1 + w2 = 1 + cfg,  w2 = f^2 (3 - 2 f) + cfg g.
The IR evaluates exactly that (13 ops before the divide instead of 23) and the NumPy reference evaluates it in the
same float32 order. The kernel itself does not change: the weights match the expanded polynomial of v2.35 within
float32 rounding for every phase and every c the shader can see.
"""
import pathlib
import re
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
import vcfg_warp as vw  # noqa: E402

F32 = np.float32
IR = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")


def crw_expanded(f, c):
    """the v2.35 evaluation order of @crw (expanded cubic), float32"""
    f = np.asarray(f, F32); c = np.asarray(c, F32)
    f2 = f * f; f3 = f2 * f
    w0 = (f2 * F32(2.0) - f - f3) * c
    w1 = f2 * (c - F32(3.0)) + f3 * (F32(2.0) - c) + F32(1.0)
    w2 = f * c + f2 * (F32(3.0) - F32(2.0) * c) + f3 * (c - F32(2.0))
    w3 = (f3 - f2) * c
    w12 = w1 + w2
    return w0, w12, w3, w2 / w12


def body(name):
    m = re.search(r"define internal <4 x float> @" + name + r"\(float %f, float %c\) alwaysinline \{\n(.*?)\n\}", IR, re.S)
    assert m, name
    return m.group(1)


class TestCrwFactored(unittest.TestCase):
    def test_ir_is_factored(self) -> None:
        b = body("crw")
        for line in ("%g = fsub float 1.0, %f", "%fg = fmul float %f, %g", "%cfg = fmul float %c, %fg",
                     "%w0n = fmul float %cfg, %g", "%w3n = fmul float %cfg, %f", "%w12 = fadd float %cfg, 1.0",
                     "%o = fdiv arcp afn float %w2, %w12",
                     # the two single-use mul + add pairs as v_mad_f32 (test_warp8_fmad_ftz.py): the product rounds, then
                     # the sum, the order the NumPy reference evaluates
                     "%h2b = call float @llvm.amdgcn.fmad.ftz.f32(float %f, float -2.0, float 3.0)",
                     "%w2 = call float @llvm.amdgcn.fmad.ftz.f32(float %f2, float %h2b, float %w0n)"):
            self.assertIn(line, b)
        self.assertNotIn("%f3", b)
        ops = [ln for ln in b.splitlines() if re.match(r"\s+%\w+ = (fadd|fsub|fmul|fneg|fdiv|call float @llvm\.amdgcn\.fmad)", ln)]
        self.assertLessEqual(len(ops), 14)

    def test_reference_matches_the_ir_order(self) -> None:
        f = np.linspace(0.0, 1.0, 4097, dtype=F32)[:-1]
        for c in (F32(0.5), F32(0.75), F32(0.5) * F32(0.37), F32(0.0)):
            w0, w12, w3, o = vw.crw(f, c)
            g = F32(1.0) - f
            cfg = c * (f * g)
            self.assertTrue(np.array_equal(w0, -(cfg * g)))
            self.assertTrue(np.array_equal(w3, -(cfg * f)))
            self.assertTrue(np.array_equal(w12, cfg + F32(1.0)))
            w2 = (f * f) * (f * F32(-2.0) + F32(3.0)) + cfg * g
            self.assertTrue(np.array_equal(o, w2 / w12))

    def test_same_kernel_as_the_expanded_form(self) -> None:
        f = np.linspace(0.0, 1.0, 4097, dtype=F32)[:-1]
        for c in np.linspace(0.0, 0.75, 31, dtype=F32):
            new = vw.crw(f, c)
            old = crw_expanded(f, c)
            for a, b in zip(new, old):
                self.assertLess(float(np.abs(a.astype(np.float64) - b.astype(np.float64)).max()), 1e-6)
            self.assertLess(float(np.abs(new[0] + new[1] + new[2] - F32(1.0)).max()), 1e-6)


if __name__ == "__main__":
    unittest.main()
