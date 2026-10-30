"""Option fade_hi (consts.flags bit 13): me1 also takes a frame luma-sum change above 8 % as a fade whatever the camera
vector (gm8) and the foreground count say. A dark fade whose gm8 is noise failed the 8.3 rule (2 % with gm8 <= 1 L2 px
and fg_blocks <= 32) and was warped with garbage vectors; with fade_hi it is blended.

Checks, without a GPU:
1. the two thresholds of me1.ll (one multiply per frame: 2 % while slow, 8 % otherwise) are the reference's FADE_LO /
   FADE_HI bit for bit, and the flag is consts.flags bit 13 = FG8_FLAG_FADE_HI, which main.c sets from g_fade_hi;
2. the IR decision equals the reference me1 on a grid of luma changes, camera vectors and foreground counts, and with
   bit 13 clear it is the 8.3 rule exactly (nothing changes without the option, nor at or below 8 % with it);
3. the option is on by default, comes back on at every load without the key, is parsed and saved;
4. the fade flag me1 reports (1 = 2 % fade, 2 = fade_hi only) is a fade for mf8 (any non-zero), for the FRAMES
   counter and for the post budget, and the FRAMES line keeps the fields its parsers read.
"""
import pathlib
import re
import struct
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))

import vcfg_motion as vm

ME1 = (ROOT / "classic" / "shaders" / "me1.ll").read_text(encoding="utf-8")
MF8 = (ROOT / "classic" / "shaders" / "mf8.ll").read_text(encoding="utf-8")
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")
TABLE_H = (ROOT / "classic" / "source" / "fg8_table.h").read_text(encoding="utf-8")
F32 = np.float32


def bits_f32(i):
    return np.frombuffer(struct.pack("<I", int(i)), np.float32)[0]


def ir_fade(fad, fsp, gm, fgcnt, fhon):
    """me1.ll: %fk = select %slow1, 0.02, 0.08; %fade = fad > fsp * fk and (slow1 or bit 13)"""
    slow1 = max(abs(gm[0]), abs(gm[1])) <= 1 and fgcnt <= 32
    fk = vm.FADE_LO if slow1 else vm.FADE_HI
    return bool(F32(fad) > F32(fsp) * fk) and (slow1 or bool(fhon))


class Me1FadeHiTest(unittest.TestCase):
    def test_thresholds_match_reference(self):
        m = re.search(r"%fki = select i1 %slow1, i32 (\d+), i32 (\d+)", ME1)
        self.assertIsNotNone(m)
        self.assertEqual(bits_f32(m.group(1)).tobytes(), vm.FADE_LO.tobytes())
        self.assertEqual(bits_f32(m.group(2)).tobytes(), vm.FADE_HI.tobytes())
        self.assertEqual(vm.FADE_LO, F32(0.02))
        self.assertEqual(vm.FADE_HI, F32(0.08))
        self.assertIn("%flim = fmul float %fsp, %fk", ME1)
        self.assertIn("%fade0 = fcmp ogt float %fad, %flim", ME1)
        self.assertIn("%fdok = or i1 %slow1, %fhon", ME1)
        self.assertIn("%fade = and i1 %fade0, %fdok", ME1)

    def test_flag_is_bit_13_of_consts_flags(self):
        # %cn = consts at +0x70 (tb[28..31]); element 2 = tb[30] = flags
        self.assertIn("%pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 112", ME1)
        self.assertIn("%fhflags = extractelement <4 x i32> %cn, i32 2", ME1)
        self.assertIn("%fhbit = and i32 %fhflags, 8192", ME1)
        self.assertIn("tb[28] = r; tb[29] = mid_idx; tb[30] = flags;", TABLE_H)
        self.assertIn("#define FG8_FLAG_FADE_HI 0x2000u", TABLE_H)
        self.assertIn("(g_fade_hi ? FG8_FLAG_FADE_HI : 0u)", MAIN_C)
        # bit 13 was free: no other flag of base_flags uses 0x2000 (8192), and cut_blocks8 starts at bit 16
        flags = MAIN_C[MAIN_C.index("uint32_t base_flags ="):MAIN_C.index("(cut_blocks8() << 16);")]
        self.assertNotIn("8192u", flags)
        self.assertNotIn("0x2000u", flags)

    def test_decision_matches_reference_and_is_83_without_the_flag(self):
        h, w = 64, 64
        rng = np.random.default_rng(13)
        L1 = rng.integers(0, 256, (h, w)).astype(np.int64)
        V2 = np.zeros((4, 4, 2), np.int64)
        GK = np.zeros((8, 8), np.int64)
        fsp = 1_000_000
        n = 0
        for ratio in (0.0, 0.019, 0.0201, 0.05, 0.0799, 0.08, 0.0801, 0.3, 0.95):
            for sign in (1, -1):
                fsc = int(round(fsp * (1 + sign * ratio)))
                fad = abs(F32(fsc) - F32(fsp))
                for gm in ((0, 0), (1, -1), (0, 2), (-30, 5)):
                    for fgcnt in (0, 32, 33, 400):
                        for fhon in (0, 1):
                            _, _, ref = vm.me1(L1, L1, V2, GK, gm, fsc, fsp, mp={"fade_hi": fhon}, fgcnt=fgcnt)
                            self.assertEqual(ref, ir_fade(fad, fsp, gm, fgcnt, fhon), (ratio, sign, gm, fgcnt, fhon))
                            rule83 = bool(fad > F32(fsp) * F32(0.02)) and max(abs(gm[0]), abs(gm[1])) <= 1 and fgcnt <= 32
                            if not fhon or fad <= F32(fsp) * F32(0.08):
                                self.assertEqual(ref, rule83, (ratio, sign, gm, fgcnt, fhon))
                            n += 1
        self.assertEqual(n, 9 * 2 * 4 * 4 * 2)

    def test_option_default_load_and_save(self):
        self.assertIn("static volatile int g_fade_hi = 1;", MAIN_C)
        self.assertIn("    g_fade_hi         = 1;   // option fade_hi", MAIN_C)
        self.assertIn('else if (strcmp(key, "fade_hi") == 0) g_fade_hi = (val != 0);', MAIN_C)
        self.assertIn('fprintf(f, "fade_hi = %d\\n", g_fade_hi ? 1 : 0);', MAIN_C)
        self.assertIn('lg("fade_hi: %s (option fade_hi;', MAIN_C)
        self.assertEqual(vm.FADE_HI_DEFAULT, 1)

    def test_fade_flag_values_are_fades_everywhere(self):
        self.assertIn("%fcl = and i1 %fade, %slow1", ME1)
        self.assertIn("%fh2 = select i1 %fade, i32 2, i32 0", ME1)
        self.assertIn("%fadei = select i1 %fcl, i32 1, i32 %fh2", ME1)
        self.assertIn("call void @llvm.amdgcn.raw.buffer.store.i32(i32 %fadei, <4 x i32> %vm, i32 28, i32 0, i32 0)", ME1)
        self.assertIn("%nofade = icmp eq i32 %fade, 0", MF8)
        self.assertIn("if (stv[4] == 1u || stv[4] == 2u) g_st_fades++;", MAIN_C)
        self.assertIn("if (stv[4] == 2u) g_st_fades_hi++;", MAIN_C)
        self.assertIn("if (cn > cb_thr || stv[4] == 1u || stv[4] == 2u) __atomic_fetch_add(&g_pb_st_dist", MAIN_C)
        self.assertNotIn("stv[4] == 1u)", MAIN_C.replace("stv[4] == 1u || stv[4] == 2u)", ""))

    def test_frames_line_keeps_its_parsed_fields(self):
        fmt = MAIN_C[MAIN_C.index('lgd("FRAMES %u: unmatched'):]
        fmt = fmt[:fmt.index("g_st_n,")]
        line = ("FRAMES 90: unmatched blocks avg 12 max 40, scene-cut frames 0, fade frames 3 | foreground: in 5 frames, "
                "avg 1.0 L2 blocks | camera 0,0 L2 px | log lines dropped 0 | fade_hi frames 2")
        self.assertIn("fade frames %u | foreground: ", fmt)
        self.assertIn("| log lines dropped %u | fade_hi frames %u\\n", fmt)
        g = re.search(r"unmatched blocks avg (\d+) max \d+, scene-cut frames (\d+), fade frames (\d+) \| foreground: in (\d+) frames", line)
        self.assertEqual(g.groups(), ("12", "0", "3", "5"))


if __name__ == "__main__":
    unittest.main()
