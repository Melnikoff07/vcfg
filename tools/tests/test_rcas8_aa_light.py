"""rcas8 in the Anti-aliasing Light profile (Enhance 1, Sharpness 0): conformance of the NumPy reference with
classic/shaders/rcas8.ll and the con == 0 fast path.

1. LDS unpack: rcas8.ll converts the packed byte with v_cvt_f32_ubyte and multiplies by the float constant 1/255
   (0x3F70101020000000); the reference must use the same product, not a division (126 of 256 codes differ by 1 ulp).
2. con == 0: the RCAS weight is +-0 for every pixel, so the RCAS apply returns base_c bit for bit. rcas8.ll skips the
   weight block and the apply under a uniform branch on the con bits; the reference mirrors it and must equal the
   forced full apply exactly.
"""
import pathlib
import re
import struct
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))

import vcfg_warp as vw  # noqa: E402

RCAS8 = (ROOT / "classic" / "shaders" / "rcas8.ll").read_text(encoding="utf-8")
F32 = np.float32


def hexf(h):
    return F32(struct.unpack("<d", struct.pack("<Q", int(h, 16)))[0])


def scene(seed, h=61, w=83):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (h, w, 3)).astype(F32)
    yy, xx = np.mgrid[0:h, 0:w]
    stair = (xx > 20 + yy // 3)[..., None]
    img[10:40] = np.where(stair[10:40], 235.0, 18.0)          # aliased staircase edge
    img[45:47, 5:70] = 250.0                                  # 2-px bright line
    img[:, 75:] = img[:, 75:76]                               # flat strip
    return img / F32(255.0)


class Rcas8AaLight(unittest.TestCase):
    def test_unpack_constant_matches_reference(self):
        m = re.search(r"@scale4\(<4 x float> %v3, float (0x[0-9A-F]+)\)", RCAS8)
        self.assertIsNotNone(m, "unpack_rgb scale constant not found")
        self.assertEqual(hexf(m.group(1)), vw.RCP255)
        self.assertEqual(vw.RCP255, F32(1.0) / F32(255.0))

    def test_con_zero_fast_path_is_bit_exact(self):
        for seed in range(4):
            img = scene(seed)
            for emode in (1, 2):
                fast = vw.rcas8(img, 0.0, emode)
                full = vw.rcas8(img, 0.0, emode, force_apply=True)
                self.assertTrue(np.array_equal(fast, full), f"seed {seed} emode {emode}")
            con = vw.rcas_con(35)
            for emode in (0, 1):
                self.assertTrue(np.array_equal(vw.rcas8(img, con, emode), vw.rcas8(img, con, emode, force_apply=True)))

    def test_ir_skips_weight_and_apply_when_con_is_zero(self):
        self.assertRegex(RCAS8, r"%conabs = and i32 %conb, 2147483647\n\s*%sharp = icmp ne i32 %conabs, 0")
        self.assertIn("br i1 %sharp, label %rcas_w, label %rcas_w_done", RCAS8)
        self.assertRegex(RCAS8, r"%w_rcas = phi float \[ %w_rcas0, %rcas_w \], \[ 0\.0, %ld_sync \]")
        self.assertIn("br i1 %sharp, label %rcas_full, label %aa_out", RCAS8)
        aa_out = RCAS8.split("aa_out:\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("insertelement <4 x float> %base_c, float %alpha, i32 3", aa_out)
        self.assertIn("ret <4 x float> %out_aa", aa_out)
        # the weight block holds every reciprocal of the RCAS lobe, none of them stays on the con == 0 path
        rcas_w = RCAS8.split("rcas_w:\n", 1)[1].split("rcas_w_done:\n", 1)[0]
        for v in ("%hmin", "%hmax", "%w_karis", "%nz1"):
            self.assertIn(v + " = fdiv", rcas_w)


if __name__ == "__main__":
    unittest.main(verbosity=2)
