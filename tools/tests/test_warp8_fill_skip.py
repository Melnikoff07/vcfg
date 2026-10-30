"""warp8 skips the fill block of the merge stage for waves where the fill weight cw is provably 0.

1. Reference: on occlusion, HUD/subtitle, pan and scene-cut triplets, cw before the skip mask is 0 wherever the
   predicate says the fill is not live (the skip changes no pixel), and the predicate is live somewhere.
2. IR: warp8.ll branches on the same predicate (same 0.025 constant as the reference), the skipped block feeds the
   rest of the shader only through the fill colour, and the skip path keeps the motion colour.
"""
import pathlib
import re
import struct
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import vcfg_ref as vref
import vcfg_motion as vm
import vcfg_warp as vw
from test_disocclusion_and_hud import build_opposite_motion_scene, build_subtitle_shadowbox_scene

IR = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
F32 = np.float32


def pan_scene(h=192, w=320, v=(10.0, -6.0), seed=7):
    rng = np.random.default_rng(seed)
    yy, xx = np.meshgrid(np.arange(h, dtype=F32), np.arange(w, dtype=F32), indexing="ij")
    fr = rng.uniform(0.03, 0.4, (3, 4))
    ph = rng.uniform(0, 6.28, (3, 4))

    def img(dx, dy):
        x, y = xx + dx, yy + dy
        ch = [0.45 + sum(0.1 * np.sin(fr[c, i] * (x if i % 2 else y) + ph[c, i]) for i in range(4)) for c in range(3)]
        return np.clip(np.stack(ch, -1), 0, 1).astype(F32)
    return img(-0.5 * v[0], -0.5 * v[1]), img(0, 0), img(0.5 * v[0], 0.5 * v[1])


def cw_maps(P, C, flags=0):
    st = vref.motion(P[..., :3], C[..., :3], dict(vref.PARAMS, prev_luma_sum=None))
    dbg = {}
    vw.warp8(P[..., :3], C[..., :3], st["F"], st["GK"], st["cutn"], flags=flags, dbg=dbg)
    return np.concatenate(dbg["cw_pre"], 0), np.concatenate(dbg["cw_live"], 0)


class Warp8FillSkipTest(unittest.TestCase):
    def test_reference_skip_is_exact(self):
        scenes = []
        P, C, GT, F, GK, _, _ = build_opposite_motion_scene()
        scenes.append(("occlusion", P, C))
        P, C, GT, F, GK, _, _ = build_subtitle_shadowbox_scene()
        scenes.append(("subtitle", P, C))
        Pp, _, Cp = pan_scene()
        scenes.append(("pan", Pp, Cp))
        Pc, _, _ = pan_scene(seed=3)
        _, _, Cc = pan_scene(seed=11, v=(-4.0, 2.0))
        scenes.append(("cut", Pc, Cc))
        live_any = 0
        for name, P, C in scenes:
            for flags in (0, 8):
                cw, live = cw_maps(P, C, flags)
                self.assertEqual(float(np.abs(cw[~live]).max(initial=0.0)), 0.0,
                                 f"{name} flags={flags}: cw != 0 outside the live predicate")
                if flags == 8:
                    self.assertFalse(live.any(), f"{name}: the no-fill flag must disable the fill block")
                else:
                    live_any += int(live.sum())
            print(f"[fill skip] {name:10s}: live {cw_maps(P, C)[1].mean() * 100:5.1f} % of pixels")
        self.assertGreater(live_any, 0, "the predicate is never live: the test would be vacuous")

    def test_ir_branch_matches_reference(self):
        m = re.search(r"%cwa = fcmp ogt float %emov, 0x([0-9A-F]{16})", IR)
        self.assertIsNotNone(m)
        thr = F32(struct.unpack(">d", bytes.fromhex(m.group(1)))[0])
        self.assertEqual(thr.tobytes(), F32(vw.CW_ACT_THR).tobytes())
        act = re.search(r"%act0 = fsub float %emov, 0x([0-9A-F]{16})", IR)
        self.assertEqual(act.group(1), m.group(1), "skip threshold must equal the residual activation threshold")
        for line in ("%cwg = fcmp ogt float %emov, %es", "br i1 %cw_live, label %fill, label %fdone",
                     "%movc = phi <4 x float> [ %mov, %merge ], [ %movf, %fill ]"):
            self.assertIn(line, IR)
        live = re.search(r"%cwl0 = and i1 %cwa, %cwg\n.*%nhlact = xor i1 %hl_act, true\n"
                         r".*%wsk_pos = fcmp ogt float %ws_keep, 0.0\n.*%nfgkill = and i1 %nhlact, %wsk_pos\n"
                         r".*%nnocf = xor i1 %nocf, true\n"
                         r".*%cwl1 = and i1 %cwl0, %nfgkill\n.*%cw_live = and i1 %cwl1, %nnocf\n", IR, re.S)
        self.assertIsNotNone(live)
        # values defined in the fill block are used after it only through %movf
        main = IR[IR.index("define amdgpu_cs void @main"):]
        fill = main[main.index("\nfill:\n"):main.index("\nfdone:\n")]
        after = main[main.index("\nfdone:\n"):]
        defs = set(re.findall(r"^\s*(%[\w.]+) =", fill, re.M))
        used_after = {d for d in defs if re.search(re.escape(d) + r"\b", after)}
        self.assertEqual(used_after, {"%movf"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
