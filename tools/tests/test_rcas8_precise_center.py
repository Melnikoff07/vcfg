"""rcas8 flag bit 6: the stored pixel keeps the centre texel's low bits on a non 8_8_8_8 surface (CUSA02299 only).

rcas8 reads its tile through an 8-bit LDS copy (q8 pack, 1/255 unpack) and builds every output lane from that copy:
an unchanged lane returns unpack(pack(texel)). On 8_8_8_8 the round trip is exact. rcas8 stores every lane, so on
2_10_10_10 (VideoOut 0x88xxxxxx) without bit 6 the mid and, with post_mid 2, the real frame lose their 2 low bits, and
a 1-code difference between the mid and the real frame becomes a 4-code step wherever it crosses an 8-bit boundary. Bit 6 adds texel - unpack(pack(texel)) back before each store.

1. Numeric model of the shader arithmetic (float32, unorm10 conversions): without the residual, unchanged lanes are
   off by up to 2 codes and most codes change; with it, every code survives; on unorm8 it is at most 1 ulp and
   changes no stored code.
2. Amplification: share of 1-code mid/real pairs that become 4-code steps without the residual, none with it.
3. IR: main decodes bit 64, both stores (mid pass and cur pass) add @resid8 of their own T# under it, and @resid8
   loads the centre and subtracts the same pack/unpack the LDS tile uses.
4. main.c: bit 64 is set only for CUSA02299 on a non 8_8_8_8 display format, on both rcas tables of the flow.
"""
import pathlib
import re
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
RCAS8 = (ROOT / "classic" / "shaders" / "rcas8.ll").read_text(encoding="utf-8")
MAIN = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")
F32 = np.float32
RCP255 = F32(1.0) / F32(255.0)


def q8_roundtrip(v):
    return (np.floor(np.clip(v, 0, 1) * F32(255.0) + F32(0.5)) * RCP255).astype(F32)


def unorm_load(codes, bits):
    return (codes.astype(F32) / F32((1 << bits) - 1)).astype(F32)


def unorm_store(v, bits):
    m = F32((1 << bits) - 1)
    return np.floor(np.clip(v, 0, 1) * m + F32(0.5)).astype(np.int64)


def stored(codes, bits, precise):
    """Unchanged lane of rcas8: out = M from LDS (+ residual when bit 6), then the store conversion."""
    f = unorm_load(codes, bits)
    m8 = q8_roundtrip(f)
    out = (m8 + (f - m8)).astype(F32) if precise else m8
    return unorm_store(out, bits)


class Rcas8PreciseCenter(unittest.TestCase):
    def test_unchanged_lanes_keep_10_bits_only_with_the_residual(self):
        k = np.arange(1024, dtype=np.int64)
        lossy = stored(k, 10, False)
        err = np.abs(lossy - k)
        self.assertEqual(int(err.max()), 2)
        self.assertGreater(int((err > 0).sum()), 700)   # 768 of 1024 codes change without bit 6
        self.assertTrue(np.array_equal(stored(k, 10, True), k))

    def test_residual_is_zero_on_unorm8(self):
        k = np.arange(256, dtype=np.int64)
        f = unorm_load(k, 8)
        # the unpack multiplies by 1/255 (1 ulp off k / 255 for some codes), so the residual is at most 1 ulp there
        self.assertLessEqual(float(np.abs(f - q8_roundtrip(f)).max()), float(np.spacing(F32(1.0))))
        self.assertTrue(np.array_equal(stored(k, 8, False), k))
        self.assertTrue(np.array_equal(stored(k, 8, True), k))

    def test_two_bit_alpha_survives_the_8_bit_copy(self):
        k = np.arange(4, dtype=np.int64)
        self.assertTrue(np.array_equal(stored(k, 2, False), k))   # so the shader leaves the alpha residual at 0

    def test_one_code_mid_real_difference_is_not_amplified(self):
        c = np.arange(1023, dtype=np.int64)
        d_lossy = np.abs(stored(c + 1, 10, False) - stored(c, 10, False))
        d_prec = np.abs(stored(c + 1, 10, True) - stored(c, 10, True))
        self.assertIn(int(d_lossy.max()), (4, 5))   # 8-bit steps re-expand to 4 or 5 ten-bit codes
        self.assertGreater(int((d_lossy >= 3).sum()), 200)   # one pair in four crosses an 8-bit boundary
        self.assertTrue(np.all(d_prec == 1))

    def test_shader_adds_the_residual_before_both_stores(self):
        self.assertRegex(RCAS8, r"%prb = and i32 %rfl, 64\n\s+%prec = icmp ne i32 %prb, 0")
        h = re.search(r"define internal <4 x float> @resid8\(<8 x i32> %t, i32 %x, i32 %y\).*?\n}\n", RCAS8, re.S)
        self.assertIsNotNone(h)
        body = h.group(0)
        self.assertIn("@llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %t", body)
        self.assertIn("fmul <4 x float> %f, <float 255.0,", body)
        self.assertIn("fadd <4 x float> %s, <float 0.5,", body)
        self.assertIn("@llvm.floor.v4f32(<4 x float> %r)", body)
        self.assertIn("fmul <4 x float> %i, <float 0x3F70101020000000,", body)   # unpack_rgb's 1/255
        self.assertIn("fsub <4 x float> %f, %q", body)
        self.assertIn("insertelement <4 x float> %d, float 0.0, i32 3", body)
        main = RCAS8.split("define amdgpu_cs void @main", 1)[1]
        for tag, v, t, blk in (("mid", "om", "to", "pass_mid"), ("cur", "oc", "tc", "pass_cur")):
            self.assertRegex(main, rf"%{v}0 = call <4 x float> @eval_tile\(<8 x i32> %{t}, ")
            self.assertRegex(main, rf"br i1 %prec, label %{tag}_prec, label %{tag}_sel\n{tag}_prec:\n\s+%\w+ = call "
                                   rf"<4 x float> @resid8\(<8 x i32> %{t}, i32 %x, i32 %y\)\n\s+%{v}p = fadd <4 x float> "
                                   rf"%{v}0, %\w+\n\s+br label %{tag}_sel\n{tag}_sel:\n\s+%{v} = phi <4 x float> "
                                   rf"\[ %{v}p, %{tag}_prec \], \[ %{v}0, %{blk} \]\n\s+call void "
                                   rf"@llvm.amdgcn.image.store.2d.v4f32.i32\(<4 x float> %{v}, i32 15, i32 %x, i32 %y, <8 x i32> %{t},")
        self.assertEqual(main.count("@llvm.amdgcn.image.store"), 2)

    def test_main_sets_bit_6_only_for_cusa02299_on_10_bit(self):
        f = re.search(r"static uint32_t rcas_precise_flag\(uint32_t rcas_flags\)\n\{\n(.*?)\n\}", MAIN, re.S)
        self.assertIsNotNone(f)
        self.assertIn('rcas_flags && g_t_dfmt != 10u && vcfg_in_family(g_title_id, "CUSA02299")', f.group(1))
        self.assertIn("rcas_flags | 64u", f.group(1))
        self.assertEqual(MAIN.count("rcas_flags1 = rcas_precise_flag(rcas_flags1);"), 1)
        self.assertEqual(MAIN.count("rcas_flags2 = rcas_precise_flag(rcas_flags2);"), 1)
        self.assertEqual(len(re.findall(r"\| 64u", MAIN)), 1)


if __name__ == "__main__":
    unittest.main()
