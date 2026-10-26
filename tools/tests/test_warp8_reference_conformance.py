"""The NumPy reference of warp8 (tools/bench/vcfg_warp.py) uses the same fill-weight constants as warp8.ll.

Checks, without a GPU: every @mlp_cw constant of the IR, in order, equals the reference's MLP table bit for bit,
and the residual activation threshold of the merge stage is the reference's CW_ACT_THR. The side_lerp gate, the
scene-change rule and the slow-path mix weight (foreground sub-block error not lowered) also follow warp8.ll.
"""
import inspect
import pathlib
import re
import struct
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))

import vcfg_warp as vw

IR = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")


def ir_float(bits):
    return np.float32(struct.unpack(">d", bytes.fromhex(bits))[0])


class Warp8ReferenceConformanceTest(unittest.TestCase):
    def test_mlp_constants_match_ir(self):
        body = IR[IR.index("define internal float @mlp_cw"):IR.index("define internal <2 x float> @ldF")]
        consts = [ir_float(h) for h in re.findall(r"0x([0-9A-F]{16})", body)]
        # IR order: output bias, then per hidden unit: weight x0, bias, weights x1..x6, output weight
        ref = [vw.MLP_OUT_BIAS]
        for b, ws, o in vw.MLP_UNITS:
            ref += [ws[0], b] + list(ws[1:]) + [o]
        self.assertEqual(len(consts), 55)
        self.assertEqual([c.tobytes() for c in consts], [np.float32(r).tobytes() for r in ref])

    def test_residual_activation_threshold_matches_ir(self):
        m = re.search(r"%act0 = fsub float %emov, 0x([0-9A-F]{16})", IR)
        self.assertIsNotNone(m)
        self.assertEqual(ir_float(m.group(1)).tobytes(), np.float32(vw.CW_ACT_THR).tobytes())

    def test_side_lerp_gate_matches_ir(self):
        body = IR[IR.index("define internal { <4 x float>, float } @side_lerp"):IR.index("define internal float @mlp_cw")]
        m = re.search(r"%em0 = fsub float %e0, 0x([0-9A-F]{16})", body)
        self.assertIsNotNone(m)
        ref = inspect.signature(vw.side_lerp).parameters["gem0"].default
        self.assertEqual(ir_float(m.group(1)).tobytes(), np.float32(ref).tobytes())

    def test_scene_change_rule_matches_ir(self):
        self.assertIn("%cthr = select i1 %cbz, i32 5600, i32 %cbk8", IR)
        em = re.search(r"%emov_hi = fcmp ogt float %emov, 0x([0-9A-F]{16})", IR)
        es = re.search(r"%es_hi = fcmp ogt float %es, 0x([0-9A-F]{16})", IR)
        self.assertEqual((float(ir_float(em.group(1))), float(ir_float(es.group(1)))), (float(np.float32(0.35)), float(np.float32(0.30))))
        rng = np.random.default_rng(3)
        a = rng.uniform(0.0, 1.0, (64, 64, 3)).astype(np.float32)
        b = rng.uniform(0.0, 1.0, (64, 64, 3)).astype(np.float32)
        F = np.zeros((4, 4, 2), np.float32)
        GK = np.zeros((4, 4), np.int64)
        dbg = {}
        keep = vw.warp8(a, b, F, GK, 5600, t=0.5, dbg=dbg)
        cut = vw.warp8(a, b, F, GK, 5601, t=0.5)
        emov, es = np.concatenate(dbg["emov"], 0), np.concatenate(dbg["es"], 0)
        bad = (emov > np.float32(0.35)) & (es > np.float32(0.30))
        self.assertTrue(bad.any() and (~bad).any())
        np.testing.assert_array_equal(cut[bad], b[bad])
        np.testing.assert_array_equal(cut[~bad], keep[~bad])

    def test_slow_mix_weight_matches_ir(self):
        body = IR[IR.index("define internal { <4 x float>, float, float } @accum"):IR.index("define internal float @diff3(")]
        self.assertIn("%w_b1 = fadd float %w_b0, 0x3FC47AE140000000", body)   # boosted weight w * 4 + 0.16 stays
        self.assertIn("%e = fadd float %e0, -0.0", body)                       # the error is not lowered any more
        self.assertNotIn("%e_s0", body)
        sys.path.insert(0, str(ROOT / "tools" / "tests"))
        import vcfg_ref as vref
        from test_disocclusion_and_hud import build_opposite_motion_scene
        P, C, GT, F, GK, _, _ = build_opposite_motion_scene()
        st = vref.motion(P[..., :3], C[..., :3], dict(vref.PARAMS, prev_luma_sum=None))
        args = (P[..., :3], C[..., :3], st["F"], st["GK"], st["cutn"])
        cur = vw.warp8(*args, t=0.5)
        np.testing.assert_array_equal(cur, vw.warp8(*args, t=0.5, wp={"fg_boost": "w"}))
        old = vw.warp8(*args, t=0.5, wp={"fg_boost": True})
        self.assertGreater(float(np.abs(cur - old).max()), 0.0)   # the scene exercises the removed error offset


if __name__ == "__main__":
    unittest.main(verbosity=2)
