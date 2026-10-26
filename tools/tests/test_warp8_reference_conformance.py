"""The NumPy reference of warp8 (tools/bench/vcfg_warp.py) uses the same fill-weight constants as warp8.ll.

Checks, without a GPU: every @mlp_cw constant of the IR, in order, equals the reference's MLP table bit for bit,
and the residual activation threshold of the merge stage is the reference's CW_ACT_THR.
"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
