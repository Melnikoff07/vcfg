"""Wave reductions of the motion passes (me1, me2, mf8: min over 16 lanes; me3: min and sum over 64) use a ds_swizzle
butterfly instead of a serial LDS loop, with the same bits.

@reduce still stores the key into @keys behind the same barrier (me2 reads @keys[base + 5] afterwards), then takes
umin through ds_swizzle_b32 in bit mode (offset = and_mask | or_mask << 5 | xor_mask << 10, offset bit 15 = 0: lane i
reads lane ((i & and) | or) ^ xor of its group of 32) with xor 1, 2, 4, 8, which leaves in every lane the min of its
aligned group of 16. Every call site passes base = tid & 48 with n = 16, so that group is [base, base + 16). me3 calls
with base 0 and n = 64: xor 16 and the min of lanes 0 and 32 (readlane) cover the wave. me3's lane-0 sum over @vecs is
the same butterfly with add (mod 2^32, any order). umin and integer add are exact, so only the lane pattern needs
proving: the emulation below checks it against the former serial loops. All calls run with the whole wave active
(workgroups of 64 threads, outside divergent branches).
"""
import pathlib
import re
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
SH = ROOT / "classic" / "shaders"
SWZ = {k: 0x1F | (k << 10) for k in (1, 2, 4, 8, 16)}


def swizzle(v, pattern):
    assert pattern < 0x8000   # bit mode
    a, o, x = pattern & 0x1F, (pattern >> 5) & 0x1F, (pattern >> 10) & 0x1F
    lane = np.arange(64)
    src = (lane & ~31) | (((lane & 31) & a) | o) ^ x
    return v[src]


def butterfly(v, op, n):
    r = v.copy()
    for k in (1, 2, 4, 8) + ((16,) if n == 64 else ()):
        r = op(r, swizzle(r, SWZ[k]))
    if n == 64:
        r = np.full(64, op(r[0:1], r[32:33])[0], dtype=r.dtype)
    return r


def reduce_body(src):
    m = re.search(r"define internal i32 @reduce\(i32 %tid, i32 %key, i32 %base, i32 %n\) alwaysinline \{\n(.*?)\n\}\n", src, re.S)
    assert m
    return m.group(1)


class TestMotionWaveReduce(unittest.TestCase):
    def test_butterfly_equals_serial_loop(self):
        rng = np.random.default_rng(7)
        cases = [rng.integers(0, 2**32, 64, dtype=np.uint64).astype(np.uint32) for _ in range(3000)]
        cases += [np.full(64, 0xFFFFFFFF, np.uint32), np.zeros(64, np.uint32), np.arange(64, dtype=np.uint32)[::-1].copy()]
        for _ in range(500):   # few distinct values, many ties, -1 sentinels
            v = rng.choice(np.array([0xFFFFFFFF, 5, 6, 0x100, 0x7FFFFFFF], np.uint32), 64)
            cases.append(v)
        tid = np.arange(64)
        for v in cases:
            b16 = butterfly(v, np.minimum, 16)
            ref16 = np.array([v[(t & 48):(t & 48) + 16].min() for t in tid], np.uint32)
            np.testing.assert_array_equal(b16, ref16)
            np.testing.assert_array_equal(butterfly(v, np.minimum, 64), np.full(64, v.min(), np.uint32))
            with np.errstate(over="ignore"):
                s = butterfly(v, lambda a, b: (a + b).astype(np.uint32), 64)
            self.assertEqual(int(s[0]), int(v.astype(np.uint64).sum() & 0xFFFFFFFF))

    def test_reduce_keeps_the_store_and_uses_the_butterfly(self):
        for name, n in (("me1", 16), ("me2", 16), ("mf8", 16), ("me3", 64)):
            body = reduce_body((SH / f"{name}.ll").read_text(encoding="utf-8"))
            self.assertIn("store i32 %key, ptr addrspace(3) %p, align 4", body, name)
            self.assertEqual(body.count("call void @llvm.amdgcn.s.barrier()"), 2, name)
            self.assertNotIn("load i32", body, name)
            self.assertNotIn("phi ", body, name)
            pats = [int(x) for x in re.findall(r"@llvm\.amdgcn\.ds\.swizzle\(i32 %\w+, i32 (\d+)\)", body)]
            want = [SWZ[k] for k in (1, 2, 4, 8)] + ([SWZ[16]] if n == 64 else [])
            self.assertEqual(pats, want, name)
            self.assertEqual(body.count("@llvm.amdgcn.readlane.i32("), 2 if n == 64 else 0, name)

    def test_call_sites_match_the_lane_groups(self):
        for name, n in (("me1", 16), ("me2", 16), ("mf8", 16), ("me3", 64)):
            src = (SH / f"{name}.ll").read_text(encoding="utf-8")
            calls = re.findall(r"call i32 @reduce\(i32 %tid, i32 %\w+, i32 (%?\w+), i32 (\d+)\)", src)
            self.assertGreaterEqual(len(calls), 2, name)
            for base, cn in calls:
                self.assertEqual(int(cn), n, name)
                if n == 16:
                    self.assertEqual(base, "%base", name)
                    self.assertEqual(re.findall(r"^\s*%base = (.*)$", src, re.M), ["and i32 %tid, 48"], name)
                else:
                    self.assertEqual(base, "0", name)

    def test_me3_sum_is_the_butterfly(self):
        src = (SH / "me3.ll").read_text(encoding="utf-8")
        self.assertNotIn("\nsum:\n", src)
        pats = [int(x) for x in re.findall(r"%ss\d+ = call i32 @llvm\.amdgcn\.ds\.swizzle\(i32 %\w+, i32 (\d+)\)", src)]
        self.assertEqual(pats, [SWZ[k] for k in (1, 2, 4, 8, 16)])
        self.assertIn("%sacc1 = add i32 %sh0, %sh1", src)
        self.assertIn("call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 %sacc1,", src)


if __name__ == "__main__":
    unittest.main()
