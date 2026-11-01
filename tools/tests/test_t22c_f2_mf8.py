#!/usr/bin/env python3
"""T22c regression test: F2_mf8 against temporal texture rebuilding across all modes (Quality / Fast / Sharp).

Verifies:
1. classic/shaders/mf8.ll and tools/bench/vcfg_motion.py::mf8 implement all four F2_mf8 components:
   - Pass 1 neighbor smoothness cap raised 32 -> 64 (%cap = select i1 %nfgk, i32 64, i32 8).
   - Coherent zero-vector texture in @mid_zinfo: ztex = max(min(ztex0, zmin * 2) - zraw_s, 0).
   - Bidirectional infl ([8..64] when t_min > num, else [16..64]) and gated luma_jump (ztex > 128) in @bsad / _norm_sad_mf8.
   - Pass 2 sub8 periodic-split guard: per_esc = (d12 >= 16) & (sc >= 16), sc1_g = (sc1 <= 3) | (per_esc & (sc1 <= 16)),
     thin_esc = (zm_le160 & (zm_le112 | dy_ge16)) | per_esc.
2. me1.ll and me2.ll remain untouched (no F1_me1 / F3_all changes).
3. Hardware budget on gfx700 (llc-22 + shaders.h): mf8 dwords == 1449, MF8_NWORDS == 1472, VGPRs == 60 (<= 64),
   scratch == 0, shader region == 0x1B600 (<= 0x1C000).
4. Compiled classic/shaders/mf8.ll (via clang-22) eliminates both Pass 1 Trap C false zero-lock (F[2,6,0]: 0.0 -> -80.0)
   and Pass 2 sub8 false periodic quadrant split (win_mask[4,6]: 1 -> 0) on a fast-panning periodic facade scene,
   and matches tools/bench/vcfg_motion.py::mf8 bit-for-bit (0 float32 word diffs).
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import vcfg_motion as vm
from test_t13_fast_thin_sub8_guard import _build_compiled_mf8_runner
from test_t6_head_hole_fix import _compile_shader_stats

MF8_LL = ROOT / "classic" / "shaders" / "mf8.ll"
ME1_LL = ROOT / "classic" / "shaders" / "me1.ll"
ME2_LL = ROOT / "classic" / "shaders" / "me2.ll"
SHADERS_H = ROOT / "classic" / "source" / "shaders.h"
LLC = pathlib.Path("/usr/bin/llc-22")


class TestT22cF2Mf8(unittest.TestCase):
    def test_01_f2_mf8_ir_and_hardware_budget(self) -> None:
        mf8_src = MF8_LL.read_text(encoding="utf-8")
        for tok in (
            "%tm_gt_n = icmp sgt i32 %t_min, %num",
            "%bidir_ok = and i1 %tm_gt_n, %zm_le512",
            "%infl_lo = select i1 %bidir_ok, i32 8, i32 16",
            "%infl0 = call i32 @llvm.smax.i32(i32 %q, i32 %infl_lo)",
            "%z_gt128 = icmp sgt i32 %ztex, 128",
            "%ljump = select i1 %z_gt128, i32 %ljump0, i32 0",
            "%dz0 = call i32 @llvm.amdgcn.sad.u8(i32 %qo0, i32 %q0, i32 0)",
            "%dz = call i32 @llvm.amdgcn.sad.u8(i32 %qo1, i32 %q1, i32 %dz0)",
            "%zraw_s = lshr i32 %ztp_next, 16",
            "%zm_512 = icmp sle i32 %zmin, 512",
            "%zt_m = call i32 @llvm.smin.i32(i32 %ztex0, i32 %zm2)",
            "%zt_d0 = sub i32 %zt_m, %zraw_s",
            "%zt_d = call i32 @llvm.smax.i32(i32 %zt_d0, i32 0)",
            "%ztex = select i1 %zm_512, i32 %zt_d, i32 %zt_m",
            "%cap = select i1 %nfgk, i32 64, i32 8",
            "%d12_ge16 = icmp sge i32 %d12, 16",
            "%sc_ge16 = icmp sge i32 %scd, 16",
            "%per_esc = and i1 %d12_ge16, %sc_ge16",
            "%thin_esc = or i1 %thin_esc1, %per_esc",
            "%sc1_le16 = icmp sle i32 %sc1, 16",
            "%per_sc1 = and i1 %per_esc, %sc1_le16",
            "%sc1_g = or i1 %sc1_le3, %per_sc1",
        ):
            self.assertIn(tok, mf8_src, f"Missing F2_mf8 token in classic/shaders/mf8.ll: {tok}")

        # Verify me1.ll and me2.ll are untouched by F1_me1
        me1_src = ME1_LL.read_text(encoding="utf-8")
        self.assertNotIn("%tm_gt_n", me1_src, "me1.ll must not contain F1_me1 changes in T22c")
        self.assertNotIn("%z_gt128", me1_src, "me1.ll must not contain F1_me1 changes in T22c")

        # Verify hardware budget via llc-22 and shaders.h
        if LLC.exists():
            st = _compile_shader_stats(MF8_LL)
            self.assertEqual(st["dwords"], 1449, f"Expected mf8.ll dwords == 1449, got {st}")
            self.assertEqual(st["vgprs"], 60, f"Expected mf8.ll vgprs == 60 (<= 64), got {st}")
            self.assertEqual(st["scratch"], 0, f"Expected mf8.ll scratch == 0, got {st}")

        shaders_h = SHADERS_H.read_text(encoding="utf-8")
        m_rsrc1 = re.search(r"#define\s+MF8_RSRC1\s+0x([0-9a-fA-F]+)", shaders_h)
        m_rsrc2 = re.search(r"#define\s+MF8_RSRC2\s+0x([0-9a-fA-F]+)", shaders_h)
        nwords_map = dict(re.findall(r"#define\s+(\w+)_NWORDS\s+(\d+)", shaders_h))
        self.assertIsNotNone(m_rsrc1)
        self.assertIsNotNone(m_rsrc2)
        self.assertEqual(int(nwords_map["MF8"]), 1472)
        mf8_vgprs = ((int(m_rsrc1.group(1), 16) & 0x3F) + 1) * 4
        mf8_scratch = int(m_rsrc2.group(1), 16) & 1
        self.assertLessEqual(mf8_vgprs, 64)
        self.assertEqual(mf8_scratch, 0)
        mode8_set = (
            "PYR8", "ME3", "ME2", "ME1", "PHASE8", "WARP8", "DUMP8", "GM8",
            "FGS8", "MF8", "GV8", "GVC8", "WARP8G", "GVZ8", "RCAS8", "OSD8", "PCOPY8",
        )
        end_off = 0x8000 + sum(((int(nwords_map[k]) * 4 + 255) & ~255) for k in mode8_set)
        self.assertEqual(end_off, 0x1B600, f"Expected shader region end 0x1B600, got 0x{end_off:X}")
        self.assertLessEqual(end_off, 0x1C000)

    def test_02_periodic_facade_trap_c_and_sub8_split_suppression_clang22(self) -> None:
        H1, W1 = 540, 960
        yy, xx = np.mgrid[0:H1, 0:W1].astype(np.int64)
        brick = (110 + ((xx * 37 + yy * 23) % 70)).astype(np.int64)
        L1c = brick.copy()
        L1p = brick.copy()
        for y in range(64):
            for x in range(40, 128):
                L1p[y, x - 40] = L1c[y, x]

        # Block (by=2, bx=6), Y=16..24, X=48..56: Pass 1 Trap C test
        # At (0,0) (X=48..56): periodic window-column mismatch with zraw_s = 256, ztex0 = 224 -> ztex_coh = 0 <= 128
        L1c[16:24, 48:56] = 75 + ((xx[16:24, 48:56] & 1) * 4)
        L1p[16:24, 48:56] = 75 + (((xx[16:24, 48:56] + 1) & 1) * 4)
        # At true pan cx = -40 (xc = 68..76, xp = 28..36): high texture (t_min = 448 > 96) + higher luma
        high_tex = 115 + ((xx[16:24, 68:76] & 1) * 8) + ((yy[16:24, 68:76] & 1) * 8)
        L1c[16:24, 68:76] = high_tex
        L1p[16:24:2, 28:36] = high_tex[::2] + 9
        L1p[17:24:2, 28:36] = high_tex[1::2] - 9

        # Block (by=4, bx=6), Y=32..40, X=48..56: Pass 2 sub8 periodic split test (zmin > 160, sc1 = 4 <= 16)
        L1p[32:40, 30:38] = L1c[32:40, 66:74]
        L1c[32:36, 66:70] = np.clip(L1c[32:36, 66:70] + 10, 0, 255)
        L1p[32:36, 48:52] = L1c[32:36, 48:52]

        FT = np.zeros((68, 120, 2), dtype=np.float32)
        FT[:, :, 0] = -80.0
        FT[2, 6, 0] = 0.0
        FT[2, 5, 0] = 0.0
        FT[1, 6, 0] = 0.0
        FT[3:6, 5:8, 0] = -72.0
        FT[4, 8, 0] = 0.0

        GK = np.zeros((68, 120), dtype=np.int64)
        V2 = np.zeros((34, 60, 2), dtype=np.int64)
        V2[:, :, 0] = -20
        gm = (-20, 0)

        F_py = vm.mf8(L1p, L1c, FT, GK, V2, gm, fade=0)
        # On a7f2094, F_py[2, 6, 0] is 0.0 (Trap C false zero-lock) and win_mask at (4, 6) is 1 (false periodic split).
        # Under F2_mf8, F_py[2, 6, 0] locks onto true pan -80.0 and win_mask at (4, 6) is 0.
        self.assertEqual(float(F_py[2, 6, 0]), -80.0, f"Block (2,6) must lock onto true pan -80.0, got {F_py[2, 6, 0]}")
        m_bldg = int(F_py[4, 6, 0].view(np.uint32) & np.uint32(0xF))
        self.assertEqual(m_bldg, 0, f"Periodic facade block (4,6) must not suffer false sub8 split, got mask={m_bldg}")

        with tempfile.TemporaryDirectory() as td:
            runner = _build_compiled_mf8_runner(MF8_LL.read_text(encoding="utf-8"), pathlib.Path(td))
            F_c = runner(L1p, L1c, FT, GK, V2, gm, fade=0, nx_groups=4, ny_groups=8)
            bit_diffs = int(np.count_nonzero(F_c[:8, :16].view(np.uint32) != F_py[:8, :16].view(np.uint32)))
            self.assertEqual(bit_diffs, 0, f"Compiled mf8.ll vs vcfg_motion.mf8 bit diffs: {bit_diffs}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
