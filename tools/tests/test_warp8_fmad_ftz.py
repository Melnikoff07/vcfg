"""warp8 issues v_mad_f32 through llvm.amdgcn.fmad.ftz instead of the denormal attribute.

The attribute would let the backend fuse mul + add itself, but it costs warp8 65-71 VGPRs (see
test_denormal_mode_attribute.py), so warp8 keeps IEEE denormal semantics in the IR and every single-use fmul that feeds
an fadd/fsub of the same block is written as the intrinsic:
  fadd(fmul(a, b), c) = fmad(a, b, c); fsub(fmul(a, b), c) = fmad(a, b, -c); fsub(c, fmul(a, b)) = fmad(-a, b, c).
The identity holds because the hardware runs with FLOAT_MODE 0xc0 (fp32 denormals flushed in and out) and v_mad_f32
rounds the product and then the sum like v_mul_f32 followed by v_add_f32 (LLVM's ISD::FMAD contract), the assumption the
shaders with "denormal-fp-math-f32"="preserve-sign,preserve-sign" already make. The machine-IR dataflow of the rewritten
warp8 was compared with the original (mad normalised to add(mul), exact identities only) and found equivalent.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WARP8 = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
GEN = (ROOT / "classic" / "gen_shaders.py").read_text(encoding="utf-8")
MUL = re.compile(r"^\s*(%[\w.]+) = fmul float (\S+), (\S+)\s*$")
ADD = re.compile(r"^\s*%[\w.]+ = (?:fadd|fsub) float (\S+), (\S+)\s*$")


def functions(src):
    out, cur = [], None
    for line in src.split("\n"):
        if line.startswith("define "):
            cur = []
            out.append(cur)
        if cur is not None:
            cur.append(line)
        if line == "}":
            cur = None
    return out


class TestWarp8FmadFtz(unittest.TestCase):
    def test_hardware_flushes_fp32_denormals(self):
        # the identity needs FLOAT_MODE 0xc0 in RSRC1 (gen_shaders.resources)
        self.assertIn("(0xc0<<12)", GEN)

    def test_warp8_keeps_ieee_ir_and_uses_the_intrinsic(self):
        self.assertNotIn("denormal-fp-math", WARP8)
        self.assertIn("declare float @llvm.amdgcn.fmad.ftz.f32(float, float, float)", WARP8)
        calls = re.findall(r"call float @llvm\.amdgcn\.fmad\.ftz\.f32\(", WARP8)
        self.assertGreaterEqual(len(calls), 80)
        # the scalar intrinsic only: no vector or fused (fma / fmuladd) form, which would round once
        self.assertNotRegex(WARP8, r"llvm\.(fma|fmuladd)\.")
        self.assertNotRegex(WARP8, r"llvm\.amdgcn\.fmad\.ftz\.v")

    def test_no_single_use_mul_add_pair_left(self):
        # a new mul + add written by hand keeps the v_mul + v_add pair the rest of the shader no longer has
        left = []
        for fn in functions(WARP8):
            block, muls, uses = "entry", {}, {}
            for i, line in enumerate(fn):
                m = re.match(r"^([\w.]+):", line)
                if m:
                    block = m.group(1)
                for u in re.findall(r"(%[\w.]+)\b", line):
                    uses.setdefault(u, []).append(i)
                m = MUL.match(line)
                if m:
                    muls[m.group(1)] = (i, block)
            blk = {}
            block = "entry"
            for i, line in enumerate(fn):
                m = re.match(r"^([\w.]+):", line)
                if m:
                    block = m.group(1)
                blk[i] = block
            for name, (i, b) in muls.items():
                u = [j for j in uses.get(name, []) if j != i]
                if len(u) == 1 and blk[u[0]] == b:
                    m = ADD.match(fn[u[0]])
                    if m and (m.group(1).rstrip(",") == name) != (m.group(2) == name):
                        left.append(fn[i].strip())
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
