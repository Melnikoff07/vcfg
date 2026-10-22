"""Regression and verification suite for Round 6 Track 3:
Screen-border ring elimination (me3, me2, me1, mf8, warp8) and
moving-object MID-grid & warp consistency (mf8, warp8, warp8g).
"""
import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/bench"))

import vcfg_motion as vm
import vcfg_ref as vr
import vcfg_warp as vw


def _textured_bg(H, W, seed=42):
    """Smoothly varying multi-frequency procedural texture in [0.1, 0.9] (H, W, 3) float32."""
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")
    r = (0.5 + 0.22 * np.sin(xx * 0.07 + yy * 0.03) + 0.15 * np.cos(xx * 0.19 - yy * 0.11)).astype(np.float32)
    g = (0.5 + 0.22 * np.cos(xx * 0.05 - yy * 0.08) + 0.15 * np.sin(xx * 0.17 + yy * 0.13)).astype(np.float32)
    b = (0.5 + 0.20 * np.sin(xx * 0.09 + yy * 0.09) + 0.15 * np.cos(xx * 0.13 + yy * 0.17)).astype(np.float32)
    return np.clip(np.stack([r, g, b], axis=-1), 0.05, 0.95).astype(np.float32)


def _shift_img(img, dx, dy):
    """Shift image so that cur(x, y) = prev(x + dx, y + dy)."""
    H, W = img.shape[:2]
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")
    return vw.bil(img, xx + np.float32(0.5 + dx), yy + np.float32(0.5 + dy))


class EdgeAndMovingWarpTest(unittest.TestCase):
    def test_1_displacement_preserving_bsad_at_screen_border(self):
        """Verify that sad_cur_grid and sad_bilateral preserve exact displacement (xp - xc == dx)
        at all 4 screen borders instead of collapsing to (0, 0)."""
        H, W = 272, 480
        base = _textured_bg(H + 64, W + 64)
        dx_true, dy_true = 8, -4
        prev = base[32:32 + H, 32:32 + W]
        cur = base[32 + dy_true:32 + dy_true + H, 32 + dx_true:32 + dx_true + W]
        L_p = (prev[..., 1] * 255.0).astype(np.int64)
        L_c = (cur[..., 1] * 255.0).astype(np.int64)

        # Right-border block X = W - 8: previously X + dx_true clamped to W - 8 == X,
        # making sad_cur_grid(dx_true) == sad_cur_grid(0) > 0 while paying motion penalty!
        X = np.array([0, W - 8, W - 8, 0], dtype=np.int64)
        Y = np.array([0, 0, H - 8, H - 8], dtype=np.int64)
        s_true = vm.sad_cur_grid(L_p, L_c, X, Y, np.full(4, dx_true), np.full(4, dy_true))
        s_zero = vm.sad_cur_grid(L_p, L_c, X, Y, np.zeros(4, np.int64), np.zeros(4, np.int64))
        self.assertTrue(np.all(s_true == 0), f"Expected 0 SAD for true border pan, got {s_true}")
        self.assertTrue(np.all(s_zero > 200), f"Expected large zero-vector SAD, got {s_zero}")

        sb_true, _ = vm.sad_bilateral(L_p, L_c, X, Y, np.full(4, dx_true), np.full(4, dy_true))
        sb_zero, _ = vm.sad_bilateral(L_p, L_c, X, Y, np.zeros(4, np.int64), np.zeros(4, np.int64))
        self.assertTrue(np.all(sb_true == 0), f"Expected 0 bilateral SAD for true border pan, got {sb_true}")
        self.assertTrue(np.all(sb_zero > 200), f"Expected large bilateral zero SAD, got {sb_zero}")

    def test_2_screen_border_pan_ring_eliminated(self):
        """Full motion + warp pipeline on a panning frame pair: border blocks must track the pan
        and coherent border pixels must have cw == 0 and high PSNR."""
        H, W = 1080, 1920
        vx_true, vy_true = 16.0, -8.0
        big = _textured_bg(H + 64, W + 64)
        A = big[32:32 + H, 32:32 + W]
        GT = big[32 - 4:32 - 4 + H, 32 + 8:32 + 8 + W]
        B = big[32 - 8:32 - 8 + H, 32 + 16:32 + 16 + W]

        st = vr.motion(A, B, dict(prev_luma_sum=None))
        F = st["F"]
        # Check border blocks of F (top row 0, bottom row 67, left col 0, right col 119)
        border_mask = np.zeros(F.shape[:2], dtype=bool)
        border_mask[0, :] = border_mask[-1, :] = border_mask[:, 0] = border_mask[:, -1] = True
        err_border = np.abs(F[border_mask, 0] - vx_true) + np.abs(F[border_mask, 1] - vy_true)
        frac_good = np.mean(err_border <= 2.0)
        self.assertGreater(frac_good, 0.95, f"Border blocks failed to track camera pan: frac_good={frac_good:.3f}")

        dbg = {}
        mid = vw.warp8(A, B, F, st["GK"], st["cutn"], t=0.5, dbg=dbg)
        cw = np.concatenate(dbg["cw"], axis=0)
        # On a coherent pan, border disocclusion pixels must not trigger static fallback cw
        border_cw = np.concatenate([cw[:16, :].ravel(), cw[-16:, :].ravel(), cw[:, :16].ravel(), cw[:, -16:].ravel()])
        self.assertLess(float(np.mean(border_cw)), 0.01, f"Expected near-zero cw on pan border, got {np.mean(border_cw):.4f}")

        # Compare border PSNR (within 24 px of valid interior border) vs cross-fade
        ring = np.zeros((H, W), dtype=bool)
        ring[8:24, 16:-16] = ring[-24:-8, 16:-16] = ring[8:-8, 16:32] = ring[8:-8, -32:-16] = True
        mse_warp = float(np.mean((mid[ring] - GT[ring]) ** 2))
        mse_cf = float(np.mean(((0.5 * (A + B))[ring] - GT[ring]) ** 2))
        psnr_warp = -10.0 * np.log10(max(mse_warp, 1e-10))
        psnr_cf = -10.0 * np.log10(max(mse_cf, 1e-10))
        self.assertGreater(psnr_warp, psnr_cf + 12.0,
                           f"Expected border warp PSNR ({psnr_warp:.2f} dB) >> crossfade ({psnr_cf:.2f} dB)")

    def test_3_mf8_preserves_moving_object_on_mid_grid(self):
        """Bug 1 regression test: a moving object at t=0.5 without a foreground splat (gk == 0,
        e.g. moderate velocity 12 px) must be evaluated by mf8 on the MID grid (t=0.5), NOT shifted
        to the CUR grid (t=1.0) where the object has already moved away."""
        H1, W1 = 540, 960
        L1p = np.full((H1, W1), 60, dtype=np.int64)
        L1c = np.full((H1, W1), 60, dtype=np.int64)
        # Moving 32x32 textured patch at L1 velocity (dx, dy) = (12, 0) (full-res 24 px):
        # At t=0.5, the object sits at X_mid in [400, 432), Y_mid in [200, 232).
        # In prev (t=0), it is at X_mid + 6 = [406, 438).
        # In cur  (t=1), it is at X_mid - 6 = [394, 426).
        yy, xx = np.meshgrid(np.arange(32), np.arange(32), indexing="ij")
        patch = 140 + ((xx * 13 + yy * 7) % 60)
        L1p[200:232, 406:438] = patch
        L1c[200:232, 394:426] = patch

        FT = np.zeros((68, 120, 2), dtype=np.float32)
        # Object covers L1 blocks bx in 50..53 (X = 400..424), by in 25..28 (Y = 200..224)
        FT[25:29, 50:54, 0] = 24.0   # full-res px (= 12 L1 px)
        GK = np.zeros((68, 120), dtype=np.int64)  # gk == 0 (no foreground splat)
        V2 = np.zeros((34, 60, 2), dtype=np.int64)

        F = vm.mf8(L1p, L1c, FT, GK, V2, (0, 0), False)
        # Trailing block bx = 53 (X = 424..432):
        # On the MID grid, prev tap is [430, 438) (inside object!) and cur tap is [418, 426) (inside object!) -> SAD = 0!
        # Under the old CUR-grid bug (Xu = X + 6 = 430), prev tap was [436, 444) (outside object!) -> SAD huge -> vector wiped!
        self.assertEqual(float(F[26, 53, 0]), 24.0,
                         f"mf8 wiped trailing moving-object block on MID grid: got {F[26, 53, 0]}")
        self.assertEqual(float(F[26, 50, 0]), 24.0,
                         f"mf8 wiped leading moving-object block on MID grid: got {F[26, 50, 0]}")

    def test_4_moving_object_warp_no_static_pullback_and_static_anchor_preserved(self):
        """Bug 3 regression test: on a moving object where warped error emov < es (or hasfg is set),
        cw must be 0 so warp8 does not blend static ghosts (fillc) into the moving object, while
        static HUD (ws) and torn static structures (emov >> es) remain protected."""
        H, W = 128, 256
        A = _textured_bg(H, W, seed=1) * 0.4
        B = A.copy()
        # Moving bright object (vx = -20, vy = 0): in A at x=110..150, in B at x=90..130, at t=0.5 at x=100..140
        yy, xx = np.meshgrid(np.arange(40, dtype=np.float32), np.arange(40, dtype=np.float32), indexing="ij")
        obj_a = np.stack([
            0.75 + 0.15 * np.sin(xx * 0.4),
            0.65 + 0.15 * np.cos(yy * 0.4),
            0.25 + 0.10 * np.sin((xx + yy) * 0.3),
        ], axis=-1).astype(np.float32)
        # Slight lighting change between A and B so emov ~ 0.18 > 0.15 while es ~ 0.85
        obj_b = np.clip(obj_a + np.float32(0.06), 0.0, 1.0)
        A[44:84, 110:150] = obj_a
        B[44:84, 90:130] = obj_b

        F = np.zeros((H // 16, W // 16, 2), dtype=np.float32)
        F[2:6, 6:10, 0] = 20.0
        GK = np.zeros((H // 16, W // 16), dtype=np.int64)

        dbg = {}
        mid = vw.warp8(A, B, F, GK, 0, t=0.5, dbg=dbg)
        cw = np.concatenate(dbg["cw"], axis=0)
        # Inside the moving object at t=0.5 (y=50..78, x=104..136), es >> emov so cw is suppressed
        self.assertLess(float(np.mean(cw[50:78, 108:132])), 0.005)
        self.assertEqual(float(np.max(cw[50:78, 102:110])), 0.0)
        # With foreground splat (GK >= 1<<20), cw is strictly 0.0 across the entire object
        GK_fg = np.zeros((H // 16, W // 16), dtype=np.int64)
        GK_fg[2:6, 6:10] = 1 << 20
        dbg_fg = {}
        vw.warp8(A, B, F, GK_fg, 0, t=0.5, dbg=dbg_fg)
        cw_fg = np.concatenate(dbg_fg["cw"], axis=0)
        self.assertEqual(float(np.max(cw_fg[50:78, 104:136])), 0.0)

        # Now test a torn static structure where sp == sc (es = 0.02) but wrong motion vector gives emov ~ 0.60:
        A_s = _textured_bg(H, W, seed=7)
        B_s = A_s.copy()
        F_bad = np.full((H // 16, W // 16, 2), 20.0, dtype=np.float32)
        dbg_s = {}
        vw.warp8(A_s, B_s, F_bad, GK, 0, t=0.5, dbg=dbg_s)
        ws_s = np.concatenate(dbg_s["ws"], axis=0)
        self.assertGreater(float(np.mean(ws_s)), 0.99, "Static HUD/structure protection ws must remain 1.0 when es == 0")


if __name__ == "__main__":
    unittest.main()
