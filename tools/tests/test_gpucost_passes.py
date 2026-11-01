"""Cheaper mode-8 passes with the same or a better picture: conformance of the NumPy reference with the shaders and the
timestamps that split the console's pass line.

1. rcas8 still lanes (flag bit 4, mid pass): warp8 flags the pixels where P == C in RGB, and the real frames around the
   mid show them raw. With Sharpness > 0 the post-process runs on the mid only, so sharpening those pixels in the mid
   alternated at 30 Hz like the AA did. They now stay raw: rcas_only with w_eff = 0 returns M bit for bit, the lane
   skips its store, and a wave whose 64 lanes are all static returns right after the tile load (ballot, uniform
   branch). The reference keeps the old `skip` (no AA) and adds `still` (raw).
2. warp8 @accum: exp2(8 (em1 - em)) and exp2(8 (em1 - e)) are exp2(0) = 1 and exp2(-8 |em - e|), bit for bit
   (fl(a - b) == -fl(b - a), 8 a power of two), so one quarter-rate v_exp_f32 per slow-path candidate instead of two.
3. Console log: t2[8] after the 1st mid's rcas8, t2[9] after the wait for the 2nd mid's buffer and t2[10] after its
   warp8 split the "rcas8+osd" field, which held the whole 2nd generated frame below 30 fps.
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
WARP8 = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")
VW_SRC = (ROOT / "tools" / "bench" / "vcfg_warp.py").read_text(encoding="utf-8")
F32 = np.float32


def scene(seed, h=61, w=83):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (h, w, 3)).astype(F32)
    yy, xx = np.mgrid[0:h, 0:w]
    img[(xx + yy // 3) % 17 < 6] = 230.0                          # staircase edges for the AA
    return (img / F32(255.0)).astype(F32)


def block(src, label, end_label):
    return src.split("\n" + label + ":\n", 1)[1].split("\n" + end_label + ":\n", 1)[0]


class RcasStillLanes(unittest.TestCase):
    def test_reference_still_lanes_are_raw(self):
        rng = np.random.default_rng(3)
        for seed in range(3):
            img = scene(seed)
            c8 = np.floor(np.clip(img, 0, 1) * F32(255.0) + F32(0.5)) * vw.RCP255
            still = rng.random(img.shape[:2]) < 0.4
            still[8:24, 8:24] = True
            for con in (0.0, vw.rcas_con(40), vw.rcas_con(100)):
                for emode in (0, 1, 2):
                    old = vw.rcas8(img, con, emode, skip=still)
                    new = vw.rcas8(img, con, emode, skip=still, still=still)
                    self.assertTrue(np.array_equal(new[still], c8[still]), "a still lane is the 8-bit texel")
                    self.assertTrue(np.array_equal(new[~still], old[~still]), "other lanes are unchanged")
                    if con == 0.0 and emode > 0:   # Sharpness 0: the old skip already left the pixel raw
                        self.assertTrue(np.array_equal(new, old))

    def test_shader_early_out_and_raw_lane(self):
        self.assertIn("declare i64 @llvm.amdgcn.ballot.i64(i1)", RCAS8)
        pre = RCAS8.split("br i1 %any_nst, label %ld_sync, label %all_still", 1)[0]
        tail = pre.split("call void @bar()\n  ; center index", 1)[1]
        # the early-out reads only the centre texel (and N, paired into one ds_read2) after the tile barrier
        self.assertEqual(re.findall(r"@rd_[cl]\(i32 %(\w+)\)", tail), ["ci", "iN"])
        self.assertRegex(tail, r"%st_skip0 = and i1 %use_st, %st_c\n\s*%st_skip = or i1 %st_skip0, %st_ext\n\s*%nst = xor i1 %st_skip, true")
        self.assertIn("%nst_bal = call i64 @llvm.amdgcn.ballot.i64(i1 %nst)", tail)
        still = block(RCAS8, "all_still", "ld_sync")
        self.assertIn("insertvalue { <4 x float>, i1, i1 } undef, <4 x float> %M, 0", still)
        self.assertIn("i1 false, 1", still)                   # no store
        self.assertIn("%w_ro = select i1 %st_skip, float 0.0, float %w_rcas", RCAS8)
        self.assertIn("%w_eff  = phi float [ %w_eff_aa, %do_aa ], [ %w_ro, %rcas_only ]", RCAS8)
        self.assertIn("insertvalue { <4 x float>, i1, i1 } %rf0, i1 %nst, 1", RCAS8)
        # w_eff = 0 makes the apply return base_c = M exactly: norm 1 (rcp exact), clamp range contains M
        img = scene(5)
        c8 = np.floor(np.clip(img, 0, 1) * F32(255.0) + F32(0.5)) * vw.RCP255
        out = vw.rcas8(img, vw.rcas_con(40), 0, still=np.ones(img.shape[:2], bool))
        self.assertTrue(np.array_equal(out, c8))

    def test_main_sets_bit4_only_where_the_real_frame_is_raw_or_aa_without_sharpening(self):
        f = MAIN_C.split("static uint32_t rcas_static_flag(uint32_t rcas_flags)", 1)[1].split("\n}", 1)[0]
        self.assertIn("if ((rcas_flags & 3u) == 1u) return rcas_flags | 16u;", f)
        self.assertIn("if ((rcas_flags & 3u) == 3u && real_aa_on()) return rcas_flags | 48u;", f)
        self.assertIn("g_cfg.sharpness == 0", MAIN_C.split("static int real_aa_on(void)", 1)[1].split("\n}", 1)[0])


class Warp8AccumOneExp(unittest.TestCase):
    def test_one_exp2_is_bit_exact(self):
        rng = np.random.default_rng(11)
        em = rng.random(200000).astype(F32) * F32(1.2)
        e = rng.random(200000).astype(F32) * F32(1.2)
        e[:5000] = em[:5000]                                     # ties
        e[5000:6000] = F32(0.0); em[5000:5500] = F32(0.0)
        em[6000:7000] = F32(-0.0); e[6000:7000] = F32(0.0)
        k = F32(8.0)
        em1 = np.minimum(em, e)
        sc_old, x1_old = np.exp2((em1 - em) * k), np.exp2((em1 - e) * k)
        z = np.exp2(np.abs(em - e) * F32(-8.0))
        lo = e < em
        self.assertTrue(np.array_equal(np.where(lo, z, F32(1.0)), sc_old))
        self.assertTrue(np.array_equal(np.where(lo, F32(1.0), z), x1_old))

    def test_reference_and_shader_use_one_exp2(self):
        acc = WARP8.split("define internal { <4 x float>, float, float } @accum(", 1)[1].split("\n}", 1)[0]
        self.assertEqual(acc.count("@llvm.exp2.f32("), 1)
        self.assertRegex(acc, r"%d0 = fsub float %em, %e\n\s*%ad0 = call float @llvm.fabs.f32\(float %d0\)\n\s*%k0 = fmul float %ad0, -8.0")
        self.assertIn("%lo = fcmp olt float %e, %em", acc)
        self.assertIn("%sc = select i1 %lo, float %z, float 1.0", acc)
        self.assertIn("%x1 = select i1 %lo, float 1.0, float %z", acc)
        self.assertIn('z = np.exp2(np.abs(em - e_eff) * F32(-wp.get("k", 8.0)))', VW_SRC)
        self.assertEqual(VW_SRC.count("np.exp2((em1 - "), 0)


class PassTimestamps(unittest.TestCase):
    def test_split_timestamps(self):
        flow8 = MAIN_C.split("static uint32_t build_dcb_flow8(", 1)[1].split("\n}\n", 1)[0]
        order = [flow8.index(m) for m in ("TS2(4);", "TS2(5);", "TS2(8);", "TS2(9);", "TS2(10);", "TS2(7);", "TS2(6);")]
        self.assertEqual(order, sorted(order))
        rc = flow8.index("if (rcas_flags1) {")
        self.assertLess(rc, flow8.index("TS2(8);"))
        self.assertLess(flow8.index("TS2(8);"), flow8.index("if (osd_wh1) {"))
        g2 = flow8.split("if (two_mids) {", 1)[1]
        self.assertLess(g2.index("insert_wait_flip_done(p, 7, vo_handle, mid2_idx)"), g2.index("TS2(9);"))
        self.assertLess(g2.index("TS2(9);"), g2.index("PHASE8_RSRC1"))
        self.assertLess(g2.index("WARP8_RSRC1"), g2.index("TS2(10);"))
        self.assertLess(g2.index("TS2(10);"), g2.index("if (rcas_flags2) {"))
        # every DCB builder zeroes t2[0..10] (slot 0x980..0x9D7; 0x9C0..0x9FF was free)
        self.assertEqual(MAIN_C.count("for (int i = 0; i < 11; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;"), 3)
        self.assertNotIn("for (int i = 0; i < 8; i++) *(volatile uint64_t *)(slotb + 0x980 + i * 8) = 0;", MAIN_C)

    def test_log_line_keeps_the_old_prefix(self):
        self.assertIn('"PASSES split ms: warp8 %.2f rcas8+osd %.2f pcopy8 %.2f (frames %u) | rcas8 %.2f rest %.2f | "', MAIN_C)
        self.assertIn('"G2 frames %u: osd+wait %.2f phase8+warp8 %.2f rcas8+osd %.2f\\n"', MAIN_C)
        line = "PASSES split ms: warp8 1.13 rcas8+osd 0.78 pcopy8 0.00 (frames 89) | rcas8 0.78 rest 0.00 | G2 frames 0: osd+wait 0.00 phase8+warp8 0.00 rcas8+osd 0.00"
        old = re.compile(r"^PASSES split ms: warp8 ([-0-9.]+) rcas8\+osd ([-0-9.]+) pcopy8 ([-0-9.]+) \(frames (\d+)\)")
        self.assertIsNotNone(old.match(line))


if __name__ == "__main__":
    unittest.main()
