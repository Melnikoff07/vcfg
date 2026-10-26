"""rcas8 in the Anti-aliasing Light profile (Enhance 1, Sharpness 0): conformance of the NumPy reference with
classic/shaders/rcas8.ll and the con == 0 fast path.

1. LDS unpack: rcas8.ll converts the packed byte with v_cvt_f32_ubyte and multiplies by the float constant 1/255
   (0x3F70101020000000); the reference must use the same product, not a division (126 of 256 codes differ by 1 ulp).
2. con == 0: the RCAS weight is +-0 for every pixel, so the RCAS apply returns base_c bit for bit. rcas8.ll skips the
   weight block and the apply under a uniform branch on the con bits; the reference mirrors it and must equal the
   forced full apply exactly.
3. Static-pixel skip: the real frames are shown raw (post-process target = generated frames only), so AA only in the
   generated frame alternates at 30 Hz wherever the picture is static (HUD, still scenery). warp8 writes the mid's
   alpha as a flag (1.0 where P == C in RGB, es == 0; 254/255 elsewhere), main.c sets rcas flag bit 4 only when warp8
   (not warp8g) wrote the mid, and rcas8 sends flagged pixels down the rcas_only path on the mid pass only.
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
WARP8 = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")
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
        self.assertIn("insertelement <4 x float> %base_c, float %aM, i32 3", aa_out)
        self.assertIn("insertvalue { <4 x float>, i1 } undef, <4 x float> %out_aa, 0", aa_out)   # returned with the changed flag
        # the weight block holds every reciprocal of the RCAS lobe, none of them stays on the con == 0 path
        rcas_w = RCAS8.split("rcas_w:\n", 1)[1].split("rcas_w_done:\n", 1)[0]
        for v in ("%hmin", "%hmax", "%w_karis", "%nz1"):
            self.assertIn(v + " = fdiv", rcas_w)

    def test_static_skip_reference(self):
        rng = np.random.default_rng(7)
        for seed in range(3):
            img = scene(seed)
            skip = rng.random(img.shape[:2]) < 0.4
            skip[10:40, :30] = True                                   # part of the staircase is static
            c8 = np.floor(np.clip(img, 0, 1) * F32(255.0) + F32(0.5)) * vw.RCP255
            for con in (0.0, vw.rcas_con(35)):
                out = vw.rcas8(img, con, 1, skip=skip)
                ref_aa = vw.rcas8(img, con, 1)
                ref_off = vw.rcas8(img, con, 0) if con else c8
                self.assertTrue(np.array_equal(out[~skip], ref_aa[~skip]), "non-static pixels must be unchanged")
                self.assertTrue(np.array_equal(out[skip], ref_off[skip]), "static pixels take the rcas_only path")

    def test_static_px_is_warp8_es_zero(self):
        a = scene(1)
        b = a.copy()
        b[5:9, 5:9, 1] += F32(1.0 / 255.0)
        st = vw.static_px(a, b)
        self.assertFalse(st[5:9, 5:9].any())
        self.assertTrue(st[20:, 20:].all())
        self.assertTrue(np.array_equal(st, vw.diff3_k(a, b) == 0))

    def test_ir_static_flag_path(self):
        self.assertRegex(WARP8, r"%st_px = fcmp oeq float %es, 0\.0\n\s*%alpha = select i1 %st_px, float 1\.0, float (0x[0-9A-F]+)")
        lo = hexf(re.search(r"%alpha = select i1 %st_px, float 1\.0, float (0x[0-9A-F]+)", WARP8).group(1))
        self.assertEqual(int(np.floor(lo * F32(255.0) + F32(0.5))), 254)
        thr = hexf(re.search(r"%st_c = fcmp ogt float %aM, (0x[0-9A-F]+)", RCAS8).group(1))
        self.assertTrue(F32(254.0) * vw.RCP255 < thr < F32(255.0) * vw.RCP255)
        self.assertRegex(RCAS8, r"%stb = and i32 %rfl, 16\n\s*%use_st = icmp ne i32 %stb, 0")
        self.assertRegex(RCAS8, r"%omr = call \{ <4 x float>, i1 \} @eval_tile\([^)]*i1 %sharp, i1 %use_st\)")
        self.assertRegex(RCAS8, r"%ocr = call \{ <4 x float>, i1 \} @eval_tile\([^)]*i1 %sharp, i1 false\)")
        self.assertIn("%doaa0 = and i1 %has_enh, %nst", RCAS8)
        self.assertIn("br i1 %doaa, label %do_aa, label %rcas_only", RCAS8)

    def test_main_sets_static_bit_only_for_warp8_mids(self):
        self.assertRegex(MAIN_C, r"return \(rcas_flags & 3u\) == 1u && !gv_warp_active\(\) \? \(rcas_flags \| 16u\) : rcas_flags;")
        self.assertIn("rcas_flags1 = rcas_static_flag(rcas_flags1);", MAIN_C)
        self.assertIn("rcas_flags2 = rcas_static_flag(rcas_flags2);", MAIN_C)
        self.assertEqual(MAIN_C.count("rcas_static_flag("), 3)       # definition + the two mid tables
        post8 = MAIN_C.split("static uint32_t build_dcb_post8(", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn("rcas_static_flag", post8)                   # real-frame-only pass never honours it

    def test_lds_put_integer_shortcuts(self):
        q8 = RCAS8.split("define internal i32 @q8(float %v) alwaysinline {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("@llvm.floor", q8)                           # fptoui truncation == floor for r >= 0.5
        self.assertLess(q8.index("call float @sat("), q8.index("fptoui float"))
        put = RCAS8.split("define internal void @lds_put(", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("= udiv ", put)
        self.assertNotIn("= urem ", put)
        self.assertRegex(put, r"%ty171 = mul nuw nsw i32 %idx, 171\n\s*%ty = lshr i32 %ty171, 11")
        for idx in range(144):                                        # 12x12 tile, tid + 0 / 64 / 128
            self.assertEqual((idx * 171) >> 11, idx // 12)
        for k in range(256):                                          # unorm8 load -> packed byte
            v = F32(k) / F32(255.0)
            self.assertEqual(int(v * F32(255.0) + F32(0.5)), k)

    def test_edge_ramp_zero_early_out_is_identity(self):
        for seed in range(4):
            img = scene(seed)
            for con in (0.0, vw.rcas_con(35)):
                for emode in (1, 2):
                    dbg = {}
                    full = vw.rcas8(img, con, emode, dbg=dbg)
                    flat = dbg['edge_ramp'] == 0
                    self.assertTrue(flat.any() and (~flat).any())
                    self.assertTrue(np.array_equal(vw.rcas8(img, con, emode, skip=flat), full))
        self.assertRegex(RCAS8, r"%has_edge = fcmp ogt float %edge_ramp, 0\.0\n\s*%doaa0 = and i1 %has_enh, %nst\n\s*%doaa = and i1 %doaa0, %has_edge")
        pre = RCAS8.split("br i1 %doaa, label %do_aa", 1)[0]
        self.assertIn("%edge_ramp = call float @sat(float %er1)", pre)


if __name__ == "__main__":
    unittest.main(verbosity=2)
