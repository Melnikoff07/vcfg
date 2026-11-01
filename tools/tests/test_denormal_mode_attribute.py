"""fp32 denormal mode of the shaders: the LLVM attribute must match the FLOAT_MODE that gen_shaders.py programs.

gen_shaders.py writes FLOAT_MODE = 0xC0 into COMPUTE_PGM_RSRC1 for every shader: round to nearest even for fp32 and
fp64, fp32 denormals flushed on input and output (FP_DENORM_MODE_SP = 0), fp64 denormals kept (FP_DENORM_MODE_DP = 3).
LLVM's default for amdgpu_cs is IEEE fp32 denormals, and under that assumption it never forms v_mad_f32 / v_mac_f32
(they do not support denormals). Declaring "denormal-fp-math-f32"="preserve-sign,preserve-sign" tells LLVM the truth
and lets it fuse mul + add into mad (ISD::FMAD: separately rounded, bit-identical to the two instructions).

The shaders below keep the IEEE assumption on purpose, because the attribute changes more than the mul + add fusion:
- llvm.sqrt.f32 without afn is lowered to v_rsq + refinement instead of v_sqrt + denormal scaling (other bits);
- a precise fdiv gets s_setreg denormal-mode toggles around the div_scale / div_fmas sequence (other bits for some
  inputs);
- warp8 and warp8g go over the 64 VGPR budget with the attribute, and no scheduler strategy fits them without
  scratch (warp8 on v2.35: 71 VGPRs with the default and iterative-maxocc strategies, 66 with iterative-ilp,
  78 with iterative-minreg; amdgpu-num-vgpr=64 or amdgpu-waves-per-eu=4 spill to scratch).
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHADERS = ROOT / "classic" / "shaders"
GEN = (ROOT / "classic" / "gen_shaders.py").read_text(encoding="utf-8")
ATTR = '"denormal-fp-math-f32"="preserve-sign,preserve-sign"'
KEEP_IEEE = {
    "fgs8": "sqrt", "gvz8": "sqrt", "pyr8": "sqrt",
    "me1": "fdiv", "phase8": "fdiv",
    "warp8": "vgpr", "warp8g": "vgpr",
}


def main_attrs(src):
    m = re.search(r"^define amdgpu_cs void @main\([^\n]*\)( #(\d+))? \{", src, re.M)
    if not m or not m.group(2):
        return ""
    g = re.search(r"^attributes #%s = \{(.*)\}$" % m.group(2), src, re.M)
    return g.group(1) if g else ""


class TestDenormalModeAttribute(unittest.TestCase):
    def test_float_mode_flushes_fp32_denormals(self):
        m = re.search(r"\(0x([0-9a-fA-F]+)<<12\)", GEN)
        self.assertIsNotNone(m, "FLOAT_MODE literal not found in gen_shaders.resources()")
        mode = int(m.group(1), 16)
        self.assertEqual(mode & 0x3, 0, "fp32 rounding must stay round-to-nearest-even")
        self.assertEqual((mode >> 4) & 0x3, 0,
                         "FP_DENORM_MODE_SP != 0: the preserve-sign attribute would no longer describe the hardware")

    def test_every_shader_declares_the_mode_or_is_a_listed_exception(self):
        names = sorted(p.stem for p in SHADERS.glob("*.ll"))
        self.assertGreaterEqual(len(names), 27)
        self.assertTrue(set(KEEP_IEEE) <= set(names), "stale exception list")
        for name in names:
            src = (SHADERS / f"{name}.ll").read_text(encoding="utf-8")
            attrs = main_attrs(src)
            self.assertNotRegex(src, r'"denormal-fp-math(-f32)?"="(ieee|positive-zero|dynamic)',
                                f"{name}: denormal mode that does not match FLOAT_MODE")
            if name in KEEP_IEEE:
                self.assertNotIn("denormal-fp-math", src, f"{name} is listed as IEEE but declares a denormal mode")
            else:
                self.assertIn(ATTR, attrs, f"{name}: main must declare {ATTR} (FLOAT_MODE flushes fp32 denormals)")

    def test_exceptions_still_have_their_reason(self):
        for name, why in KEEP_IEEE.items():
            src = (SHADERS / f"{name}.ll").read_text(encoding="utf-8")
            if why == "sqrt":
                self.assertRegex(src, r"call float @llvm\.sqrt\.f32", f"{name}: no sqrt left, retest with the attribute")
            elif why == "fdiv":
                self.assertRegex(src, r"= fdiv float ", f"{name}: no precise fdiv left, retest with the attribute")


if __name__ == "__main__":
    unittest.main()
