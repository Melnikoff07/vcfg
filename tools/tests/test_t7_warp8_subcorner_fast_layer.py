#!/usr/bin/env python3
"""T7 regression test: warp8 @sub_corner must not discard fast second layers (|sx1| > 16).

Why `sok = (|sx1| <= 16)` in `warp8.ll` `@sub_corner` / `vcfg_warp.py` `_eval_sub_corner` broke 21/45 triplets:
- In `mf8` Pass 2, dual-layer split blocks (`0 < m < 15`) are canonicalized by `swap_l = (win_mask > 0) & (sc1 > sc2_win)`
  so that Layer 1 (`v1`, upper bits of `F.x, F.y`) is ALWAYS the background layer (closer to camera `gm2`),
  and Layer 2 (`v2 = (sx1 * 2, sy1 * 2)`, 8-bit signed `sx1, sy1` in L1 px, active on 8x8 quadrants with `(m >> qbit) & 1 == 1`)
  is ALWAYS the foreground contour layer (farther from camera `gm2`).
- When a foreground object or silhouette moves faster than 32 full-res px/frame (`|sx1| > 16` L1 px), `mf8` packs that
  legitimate foreground velocity into `v2` (`|sx1| > 16`) with `m != 0`.
- When `warp8` `@sub_corner` applied `sok = (|sx1| <= 16)` (`use2 = (((m >> qbit) & 1) != 0) & sok`), any split block with
  `|sx1| > 16` still had `any_split = True` (`m != 0`), switching interpolation to 8x8 sub-corner weights `(wx8, wy8)`,
  but forced `use2 = False` on all 4 quadrants — discarding the foreground contour velocity `v2` and substituting the
  background velocity `v1` on the foreground 8x8 sub-blocks, while also disabling `is_fg_sub = any_split & u2 & (e < 0.18)`.
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
sys.path.insert(0, str(ROOT / "tools/bench"))

import model_seq_flicker_fixes as msf
import vcfg_warp as vw

WARP8_LL = ROOT / "classic/shaders/warp8.ll"
MF8_LL = ROOT / "classic/shaders/mf8.ll"
LLC = pathlib.Path("/usr/bin/llc-22")

SEQ5_DIR = pathlib.Path(
    "/home/melnikoff/vcfg-pipeline/runtime/research/owner-seq5-clouds/bench_CUSA08519_00000003cf4253c9"
)
SEQ4_DIR = pathlib.Path(
    "/home/melnikoff/vcfg-pipeline/runtime/research/owner-seq4-24d7b38d/bench_CUSA08519_00000003d5a17ab2"
)


def _pack_dual_layer_f(
    v1x: np.ndarray,
    v1y: np.ndarray,
    sx1: np.ndarray,
    sy1: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """Pack background layer (v1x, v1y) and foreground 8x8 sub-block layer (sx1*2, sy1*2, mask) into F (H16, W16, 2)."""
    has_split = mask > 0
    vx = np.where(has_split & (np.abs(v1x) < np.float32(0.0625)), np.float32(0.0625), v1x).astype(np.float32)
    vy = np.where(has_split & (np.abs(v1y) < np.float32(0.0625)), np.float32(0.0625), v1y).astype(np.float32)
    bxi = vx.view(np.uint32)
    byi = vy.view(np.uint32)
    bxi_hom = bxi & np.uint32(0xFFFFFFF0)
    bxi_spl = (
        (bxi & np.uint32(0xFFFFF000))
        | ((sx1.astype(np.uint32) & np.uint32(0xFF)) << np.uint32(4))
        | (mask.astype(np.uint32) & np.uint32(0xF))
    )
    byi_spl = (byi & np.uint32(0xFFFFFF00)) | (sy1.astype(np.uint32) & np.uint32(0xFF))
    vx_out = np.where(has_split, bxi_spl, bxi_hom).astype(np.uint32).view(np.float32)
    vy_out = np.where(has_split, byi_spl, byi).astype(np.uint32).view(np.float32)
    return np.stack([vx_out, vy_out], axis=-1)


def _eval_sub_corner_without_sok(raw_x0y0, raw_x1y0, raw_x0y1, raw_x1y1, qx, qy):
    """Reference @sub_corner from 2d032b1 without sok = (|sx1| <= 16)."""
    F32 = np.float32
    use_x1 = (qx >= 2)[..., None]
    use_y1 = (qy >= 2)[..., None]
    r_top = np.where(use_x1, raw_x1y0, raw_x0y0)
    r_bot = np.where(use_x1, raw_x1y1, raw_x0y1)
    raw = np.where(use_y1, r_bot, r_top)
    qbit = (qx & 1) | ((qy & 1) << 1)
    bxi = raw[..., 0].view(np.uint32)
    byi = raw[..., 1].view(np.uint32)
    m = (bxi & np.uint32(0xF)).astype(np.int32)
    use2 = ((m >> qbit) & 1) != 0
    cx2 = ((bxi.astype(np.int32) << 20) >> 24) * 2
    cy2 = ((byi.astype(np.int32) << 24) >> 24) * 2
    v1x = (bxi & np.uint32(0xFFFFF000)).view(F32)
    v1y = (byi & np.uint32(0xFFFFFF00)).view(F32)
    v1x = np.where(m != 0, np.where(np.abs(v1x) <= F32(0.0625), F32(0.0), v1x), raw[..., 0])
    v1y = np.where(m != 0, np.where(np.abs(v1y) <= F32(0.0625), F32(0.0), v1y), raw[..., 1])
    vx = np.where(use2, cx2.astype(F32), v1x)
    vy = np.where(use2, cy2.astype(F32), v1y)
    return np.stack([vx, vy], axis=-1), m, use2


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse <= 1e-12 else -10.0 * float(np.log10(mse))


class TestT7Warp8SubcornerFastLayer(unittest.TestCase):
    def test_01_synthetic_fast_second_layer_subcorner_psnr(self) -> None:
        """Synthetic scene with a legitimate fast 2nd layer |sx1| = 22 > 16 (vx2 = +44 px/frame):
        warp8 output must match the un-clamped @sub_corner and must not ghost the 8x8 foreground contour.
        """
        H, W = 128, 256
        nby, nbx = H // 16, W // 16
        yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")

        # Background texture: static (v1 = (0, 0))
        bg = np.stack(
            [
                0.20 + 0.10 * np.sin(xx * 0.15) * np.cos(yy * 0.15),
                0.25 + 0.10 * np.cos(xx * 0.12),
                0.30 + 0.10 * np.sin(yy * 0.18),
            ],
            axis=-1,
        ).astype(np.float32)

        # Fast foreground object moving by v2 = (+44, 0) full-res px/frame (sx1 = +22 > 16 L1 px).
        # At t = 0.5 (GT), object occupies x in [104, 168), y in [32, 96):
        # Block column bx = 6 covers x in [96, 112): left 8px [96, 104) is background (qbit 0, 2 = 0),
        # right 8px [104, 112) is foreground (qbit 1, 3 = 1 -> mask = 0b1010 = 10).
        vx_fg = 44.0
        sx1_fg = 22  # 22 * 2 = 44.0, |sx1| = 22 > 16

        def fg_pattern(x_coord: np.ndarray, y_coord: np.ndarray) -> np.ndarray:
            return np.stack(
                [
                    0.75 + 0.18 * np.sin(x_coord * 0.35),
                    0.15 + 0.12 * np.cos(y_coord * 0.30),
                    0.80 - 0.15 * np.sin((x_coord + y_coord) * 0.25),
                ],
                axis=-1,
            ).astype(np.float32)

        def render_frame(t_shift: float) -> np.ndarray:
            # At t=0 (prev), object is at [104 + 22, 168 + 22) = [126, 190) (since up = u + 0.5*vx)
            # At t=0.5 (GT), object is at [104, 168)
            # At t=1 (cur), object is at [104 - 22, 168 - 22) = [82, 146) (since uc = u - 0.5*vx)
            x0_obj = 104.0 + t_shift * vx_fg
            x1_obj = 168.0 + t_shift * vx_fg
            in_fg = (yy >= 32) & (yy < 96) & (xx >= x0_obj) & (xx < x1_obj)
            fg_col = fg_pattern(xx - t_shift * vx_fg, yy)
            return np.where(in_fg[..., None], fg_col, bg)

        tp = render_frame(+0.5)
        gt = render_frame(0.0)
        tc = render_frame(-0.5)

        v1x = np.zeros((nby, nbx), dtype=np.float32)
        v1y = np.zeros((nby, nbx), dtype=np.float32)
        sx1 = np.zeros((nby, nbx), dtype=np.int32)
        sy1 = np.zeros((nby, nbx), dtype=np.int32)
        mask = np.zeros((nby, nbx), dtype=np.int32)

        # Interior foreground blocks bx in {7, 8, 9} (x in [112, 160)): homogeneous v1 = (+44, 0)
        v1x[2:6, 7:10] = vx_fg
        # Left split boundary block bx = 6 (x in [96, 112)): left half bg (0), right half fg (+44 -> sx1=22, mask=0b1010)
        sx1[2:6, 6] = sx1_fg
        mask[2:6, 6] = 0b1010
        # Right split boundary block bx = 10 (x in [160, 176)): left half fg (+44 -> sx1=22, mask=0b0101), right half bg (0)
        sx1[2:6, 10] = sx1_fg
        mask[2:6, 10] = 0b0101

        F = _pack_dual_layer_f(v1x, v1y, sx1, sy1, mask)
        GK = np.zeros((nby, nbx), dtype=np.uint32)

        # Evaluate current vcfg_warp.warp8
        out_cur = vw.warp8(tp, tc, F, GK, cutn=0, t=0.5)

        # Evaluate reference without sok
        orig_fn = vw._eval_sub_corner
        try:
            vw._eval_sub_corner = _eval_sub_corner_without_sok
            out_no_sok = vw.warp8(tp, tc, F, GK, cutn=0, t=0.5)
        finally:
            vw._eval_sub_corner = orig_fn

        psnr_cur_full = _psnr(out_cur, gt)
        psnr_nosok_full = _psnr(out_no_sok, gt)

        # Split contour ROI: y in [32, 96), x in [104, 112) (the right 8x8 quadrants of bx=6)
        roi_cur = out_cur[32:96, 104:112]
        roi_nosok = out_no_sok[32:96, 104:112]
        roi_gt = gt[32:96, 104:112]
        psnr_cur_roi = _psnr(roi_cur, roi_gt)
        psnr_nosok_roi = _psnr(roi_nosok, roi_gt)

        self.assertGreaterEqual(
            psnr_cur_full,
            psnr_nosok_full - 1e-4,
            f"warp8 full-frame PSNR with fast 2nd layer |sx1|=22 regressed vs no-sok: "
            f"{psnr_cur_full:.4f} dB vs {psnr_nosok_full:.4f} dB",
        )
        self.assertGreaterEqual(
            psnr_cur_roi,
            35.0,
            f"warp8 split-contour 8x8 ROI PSNR collapsed ({psnr_cur_roi:.2f} dB vs {psnr_nosok_roi:.2f} dB without sok)",
        )

    def test_02_warp8_ir_and_python_have_no_sok_clamp(self) -> None:
        warp8_src = WARP8_LL.read_text(encoding="utf-8")
        self.assertFalse("%sok = icmp sle i32 %sx1a, 16" in warp8_src, "sok clamp must be removed from warp8.ll")
        self.assertFalse("%u2 = and i1 %u2_0, %sok" in warp8_src, "u2 &= sok must be removed from warp8.ll")
        self.assertTrue("%u2 = icmp ne i32 %mb, 0" in warp8_src, "warp8.ll @sub_corner must use %u2 = icmp ne i32 %mb, 0")

        vw_src = (ROOT / "tools/bench/vcfg_warp.py").read_text(encoding="utf-8")
        self.assertFalse("sok = np.abs(sx1) <= 16" in vw_src, "sok clamp must be removed from vcfg_warp.py")
        self.assertTrue("use2 = ((m >> qbit) & 1) != 0" in vw_src, "vcfg_warp.py must use use2 = ((m >> qbit) & 1) != 0")

    def test_03_warp8_shader_budget_freed_margin(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            sp_path = td_path / "warp8.s"
            op_path = td_path / "warp8.o"
            subprocess.check_call(
                [str(LLC), "-mtriple=amdgcn--", "-mcpu=gfx700", "-O2", str(WARP8_LL), "-o", str(sp_path)]
            )
            subprocess.check_call(
                [
                    str(LLC),
                    "-mtriple=amdgcn--",
                    "-mcpu=gfx700",
                    "-O2",
                    "-filetype=obj",
                    str(WARP8_LL),
                    "-o",
                    str(op_path),
                ]
            )
            asm = sp_path.read_text(encoding="utf-8")
            obj = subprocess.check_output(["/usr/bin/llvm-objdump-22", "-h", str(op_path)], text=True)
            vgprs = int(re.search(r"NumVgprs:\s*(\d+)", asm).group(1))
            scratch = int(re.search(r"ScratchSize:\s*(\d+)", asm).group(1))
            text_bytes = 0
            for line in obj.splitlines():
                if ".text" in line:
                    text_bytes = int(line.split()[2], 16)
                    break
            dwords = text_bytes // 4
            self.assertLessEqual(dwords, 2996, f"Expected warp8 dwords <= 2996 after removing sok, got {dwords}")
            self.assertLessEqual(vgprs, 64, f"warp8 VGPR overflow: {vgprs}")
            self.assertEqual(scratch, 0, f"warp8 scratch non-zero: {scratch}")

    @unittest.skipUnless(SEQ5_DIR.exists() and SEQ4_DIR.exists(), "seq5/seq4 captures not present")
    def test_04_seq5_and_seq4_head_hole_metrics_preserved(self) -> None:
        res5 = msf.evaluate_t6_sequence_models(SEQ5_DIR)
        res4 = msf.evaluate_t6_sequence_models(SEQ4_DIR)
        b5 = res5["baseline_2d032b1"]
        n2_5 = res5["B_hold_N2"]
        b4 = res4["baseline_2d032b1"]
        n2_4 = res4["B_hold_N2"]
        self.assertLess(n2_5["unanc_cells"], b5["unanc_cells"])
        self.assertLess(n2_5["fast_unanc_hole_cells"], b5["fast_unanc_hole_cells"])
        self.assertLessEqual(n2_5["flips_tight"], b5["flips_tight"])
        self.assertLess(n2_4["unanc_cells"], b4["unanc_cells"])
        self.assertLess(n2_4["fast_unanc_hole_cells"], b4["fast_unanc_hole_cells"])
        self.assertLessEqual(n2_4["flips_tight"], b4["flips_tight"])


if __name__ == "__main__":
    unittest.main()
