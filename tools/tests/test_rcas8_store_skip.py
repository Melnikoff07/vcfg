"""rcas8 skips the image store of lanes it leaves unchanged, on 8_8_8_8 surfaces only (bit-exact).

With con == 0 (Sharpness 0) a lane that does not take the AA block (no edge, or the static-pixel flag on the mid pass)
returns M as read through the 8-bit LDS copy: unpack(pack(texel)). On an 8_8_8_8 surface that round trip gives back
the stored code bit for bit, so storing it rewrites the same value; neighbour tiles that read the texel during the
in-place race read the same value either way. The shader therefore stores only the lanes that took the AA block (or
every lane when sharpening, con != 0), and keeps storing every lane on other data formats (2_10_10_10 and friends lose
precision in the 8-bit LDS copy, so skipping there would change pixels).

1. Exhaustive: for every code k, unorm8 load (k / 255), LDS pack (q8: floor(sat(v) * 255 + 0.5)), unpack (k * RCP255)
   and the unorm8 store conversion return k.
2. Reference: outside the AA lanes (edge_ramp == 0, or the static skip) the stored 8-bit output equals the input codes
   for both Enhance modes, with and without the static flag; inside, some lanes change (the test discriminates).
3. IR: eval_tile reports `changed` = took the AA block (true on the sharpening path); main guards both stores with
   `changed | keep_all`, keep_all = data format of T#cur (table +0x20, dword 1 bits 20..25) != 10; the mid pass
   barrier stays outside the guarded block.
"""
import pathlib
import re
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))

import vcfg_warp as vw  # noqa: E402

RCAS8 = (ROOT / "classic" / "shaders" / "rcas8.ll").read_text(encoding="utf-8")
F32 = np.float32


def codes(img):
    return np.rint(np.clip(img, 0, 1) * F32(255.0)).astype(np.int64)


def scene(seed, h=61, w=83):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (h, w, 3)).astype(F32)
    yy, xx = np.mgrid[0:h, 0:w]
    img[10:40] = np.where((xx > 20 + yy // 3)[..., None][10:40], 235.0, 18.0)
    img[45:47, 5:70] = 250.0
    img[:, 60:] = img[:, 60:61]
    return img / F32(255.0)


class Rcas8StoreSkip(unittest.TestCase):
    def test_unorm8_lds_round_trip_is_identity(self):
        k = np.arange(256, dtype=np.int64)
        load = k.astype(F32) / F32(255.0)                                   # unorm8 -> float
        packed = np.floor(np.clip(load, 0, 1) * F32(255.0) + F32(0.5))      # q8 (lds pack)
        unpacked = packed.astype(F32) * vw.RCP255                           # unpack_rgb
        stored = np.floor(unpacked * F32(255.0) + F32(0.5)).astype(np.int64)  # float -> unorm8
        self.assertTrue(np.array_equal(packed.astype(np.int64), k))
        self.assertTrue(np.array_equal(stored, k))

    def test_unchanged_lanes_store_the_input_codes(self):
        for seed in range(4):
            img = scene(seed)
            st = np.zeros(img.shape[:2], bool)
            st[:, 30:50] = True                                             # a static band
            for emode in (1, 2):
                for skip in (None, st):
                    dbg = {}
                    out = vw.rcas8(img, 0.0, emode, skip=skip, dbg=dbg)
                    aa = dbg['edge_ramp'] > 0
                    if skip is not None:
                        aa &= ~skip
                    same = (codes(out) == codes(img)).all(-1)
                    self.assertTrue(same[~aa].all(), f"seed {seed} emode {emode}")
                    self.assertFalse(same[aa].all(), "no AA lane changed: the scene does not discriminate")

    def test_ir_reports_changed_lanes(self):
        self.assertIn("define internal { <4 x float>, i1, i1 } @eval_tile(", RCAS8)
        self.assertIn("%chg = phi i1 [ true, %do_aa ], [ false, %rcas_only ]", RCAS8)
        self.assertRegex(RCAS8, r"%ra1 = insertvalue \{ <4 x float>, i1, i1 \} %ra0, i1 %chg, 1")
        self.assertRegex(RCAS8, r"%rf1 = insertvalue \{ <4 x float>, i1, i1 \} %rf0, i1 true, 1")   # sharpening path

    def test_ir_guards_both_stores(self):
        self.assertRegex(RCAS8, r"%pdf = getelementptr i8, ptr addrspace\(4\) %tbl, i64 32\n"
                                r"\s*%tcd = load <4 x i32>, ptr addrspace\(4\) %pdf.*\n"
                                r"\s*%t1w = extractelement <4 x i32> %tcd, i32 1\n"
                                r"\s*%dfs = lshr i32 %t1w, 20\n\s*%dfmt = and i32 %dfs, 63\n"
                                r"\s*%is8888 = icmp eq i32 %dfmt, 10\n\s*%keep_all = xor i1 %is8888, true")
        main = RCAS8.split("define amdgpu_cs void @main", 1)[1]
        self.assertEqual(main.count("@llvm.amdgcn.image.store"), 2)
        for tag, v, lbl in (("mid", "om", "st_mid"), ("cur", "oc", "wb_sel")):   # cur: bit 5 write-back test first
            self.assertRegex(main, rf"%{v}_st = or i1 %{v}c, %keep_all\n\s*br i1 %{v}_st, label %{lbl}, label %")
            blk = main.split(f"st_{tag}:", 1)[1].split("br label", 1)[0]
            self.assertIn(f"image.store.2d.v4f32.i32(<4 x float> %{v},", blk)
        join = main.split("mid_join:", 1)[1].split("br label %check_cur", 1)[0]
        self.assertIn("call void @bar()", join)
        self.assertNotIn("@bar()", main.split("st_mid:", 1)[1].split("mid_join:", 1)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
