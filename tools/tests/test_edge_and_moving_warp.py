"""Regression and verification suite for Round 6 Track 3:
Screen-border ring elimination (me3, me2, me1, mf8, warp8) and
moving-object MID-grid & warp consistency (mf8, warp8, warp8g).
"""
import math
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

        # Now test a torn static structure where sp == sc (es = 0.02) but wrong motion vector gives emov ~ 0.60.
        # Intentional V5b (86efff7) trade-off (see runtime/research/v235-release/TEST173.md,
        # runtime/research/v235-release/V5B_STATIC_RISK.md, runtime/research/v235-release/V5B_00557.md):
        # - On all pixels of real warped mismatch (emov >= 0.25, 23983/32768 = 73.19% of frame) and high-contrast
        #   HUD (w_hud == 1.0, total 25787/32768 = 78.70%), ws remains strictly 1.0 and output matches A_s (PSNR > 170 dB).
        # - On smooth low-contrast pixels where shifted taps happen to agree (emov < 0.25, w_hud == 0), V5b suppresses
        #   ws0 via w_vel = sat((vmg_e - 4.0) / 6.0) to eliminate ~70% of contour flicker on CUSA08519/CUSA05571/
        #   CUSA10249/CUSA13323 and improve panning periodic-texture PSNR by +1.20 dB on 27 CUSA00557 triplets
        #   (with 0/45 + 0/27 degraded spots >= 8x8 on real captures).
        # - On this synthetic F_bad=(20,20) stress case (TEST173.md section 2.1-2.4), mean(ws_s) = 0.7902,
        #   full-frame PSNR = 20.278 dB (MSE = 0.009380), MAE = 0.03552, interior max_abs = 0.5440, full max_abs = 0.6119.
        A_s = _textured_bg(H, W, seed=7)
        B_s = A_s.copy()
        F_bad = np.full((H // 16, W // 16, 2), 20.0, dtype=np.float32)
        dbg_s = {}
        mid_s = vw.warp8(A_s, B_s, F_bad, GK, 0, t=0.5, dbg=dbg_s)
        ws_s = np.concatenate(dbg_s["ws"], axis=0)
        emov_s = np.concatenate(dbg_s["emov"], axis=0)
        mismatch_mask = emov_s >= 0.25
        self.assertGreater(float(np.mean(mismatch_mask)), 0.73)
        self.assertEqual(
            float(np.min(ws_s[mismatch_mask])),
            1.0,
            "Static structure protection ws must remain strictly 1.0 on all pixels of real warped mismatch (emov >= 0.25)",
        )
        self.assertLess(
            float(np.max(np.abs(mid_s[mismatch_mask] - A_s[mismatch_mask]))),
            1e-6,
            "Output on emov >= 0.25 pixels must exactly preserve static reference A_s",
        )
        self.assertGreater(float(np.mean(ws_s)), 0.785, "Mean ws across synthetic F_bad frame must stay >= 0.785 (V5b: 0.7902)")
        mse_s = float(np.mean((mid_s - A_s) ** 2))
        psnr_s = float(-10.0 * np.log10(max(mse_s, 1e-12)))
        mae_s = float(np.mean(np.abs(mid_s - A_s)))
        max_abs_interior = float(np.max(np.abs(mid_s[12:-12, 12:-12] - A_s[12:-12, 12:-12])))
        max_abs_full = float(np.max(np.abs(mid_s - A_s)))
        self.assertGreater(psnr_s, 20.20, f"Synthetic F_bad full-frame PSNR regressed below TEST173.md bound (20.278 dB): {psnr_s:.3f} dB")
        self.assertLess(mae_s, 0.036, f"Synthetic F_bad MAE regressed above TEST173.md bound (0.03552): {mae_s:.5f}")
        self.assertLess(max_abs_interior, 0.545, f"Synthetic F_bad interior max_abs regressed above TEST173.md bound (0.5440): {max_abs_interior:.4f}")
        self.assertLess(max_abs_full, 0.615, f"Synthetic F_bad full max_abs regressed above TEST173.md bound (0.6119): {max_abs_full:.4f}")

    def test_5_smooth_oob_ramp_eliminates_edge_step_discontinuity(self):
        """v2.35 E2_ramp8 regression test: out-of-bounds single-side taps (onlyp / onlyc) must blend
        smoothly over an 8-pixel ramp past the screen border instead of forcing a hard step eb=0.02 -> fgkill=1
        at dc=0 (which caused a visible ~7% edge warp boundary during running/forward motion)."""
        H, W = 64, 256
        tp = np.full((H, W, 3), 0.62, dtype=np.float32)
        tc = np.full((H, W, 3), 0.30, dtype=np.float32)
        # Query _taps along row y=32 across the oobc boundary uc = 0.5 (for vx=32, t=0.5 -> uc = u - 16.0):
        # u = [16.6, 16.4, 14.5, 12.5, 8.5] -> uc = [0.6, 0.4, -1.5, -3.5, -7.5] -> dc = [-0.1, +0.1, 2.0, 4.0, 8.0]
        u = np.array([[16.6, 16.4, 14.5, 12.5, 8.5]], dtype=np.float32)
        v = np.full_like(u, 32.5)
        vx = np.full_like(u, 32.0)
        vy = np.zeros_like(u)
        sp = tp[32:33, :5]
        sc = tc[32:33, :5]
        es = vw.diff3_k(sp, sc)
        _, _, eb_q = vw._taps(tp, tc, u, v, vx, vy, 0.5, sp, sc, es, None, True, csh=0.0)
        eb_row = eb_q[0]
        # Across dc = -0.1 px (in-bounds) to dc = +0.1 px (just OOB), eb must be continuous (no jump to 0.02)
        self.assertGreater(float(eb_row[0]), 0.50)
        self.assertLess(abs(float(eb_row[1] - eb_row[0])), 0.03,
                        f"Hard OOB step discontinuity at screen border: eb(dc=-0.1)={eb_row[0]:.4f}, eb(dc=+0.1)={eb_row[1]:.4f}")
        # Smoothly monotonic decrease over the 8 px ramp down to 0.02 at dc >= 8.0 px
        self.assertGreater(float(eb_row[1]), float(eb_row[2]))
        self.assertGreater(float(eb_row[2]), float(eb_row[3]))
        self.assertGreater(float(eb_row[3]), float(eb_row[4]))
        self.assertAlmostEqual(float(eb_row[4]), 0.02, places=5)

        # Full warp8 check across the oobc seam at x = 15 (uc = -0.5, dc = 1.0 px) vs x = 16 (uc = 0.5, dc = 0.0 px)
        F = np.zeros((H // 16, W // 16, 2), dtype=np.float32)
        F[:, :, 0] = 32.0
        GK = np.zeros((H // 16, W // 16), dtype=np.int64)
        dbg = {}
        vw.warp8(tp, tc, F, GK, 0, t=0.5, dbg=dbg)
        emov = np.concatenate(dbg["emov"], axis=0)
        cw = np.concatenate(dbg["cw"], axis=0)
        step_emov = abs(float(np.mean(emov[:, 16]) - np.mean(emov[:, 15])))
        step_cw = abs(float(np.mean(cw[:, 16]) - np.mean(cw[:, 15])))
        self.assertGreater(float(np.mean(emov[:, 15])), 0.70, "Just-OOB column x=15 (dc=1 px) must not collapse to eb=0.02 / fgkill")
        self.assertGreater(float(np.mean(emov[:, 15])), float(np.mean(emov[:, 12])))
        self.assertGreater(float(np.mean(emov[:, 12])), float(np.mean(emov[:, 8])))
        self.assertLess(step_emov, 0.22, f"Expected 1/8-ramp emov step across oobc seam (was 0.94), got step={step_emov:.4f}")
        self.assertLess(step_cw, 0.02, f"Expected no fgkill cw cliff across oobc seam, got step={step_cw:.4f}")

        # Verify warp8.ll shader IR also implements the 8 px OOB ramp in both @taps and @tapsq
        warp8_ir = (ROOT / "classic/shaders/warp8.ll").read_text(encoding="utf-8")
        self.assertEqual(warp8_ir.count("fmul float %d_one, 0.125"), 2,
                         "Expected 8 px OOB ramp (fmul float %d_one, 0.125) in both @taps and @tapsq of warp8.ll")

    def test_6_real_captured_cusa05571_00_edge_fixture_regression(self):
        """Real hardware capture regression test on CUSA05571_00 left-border 32x64 crop (y=544..576, x=0..64):
        verifies provenance against bench_CUSA05571/bench_0000_{a,b,gt}.rgba and asserts real GT PSNR
        improvement across the OOB ramp (base 01bf65c FAILS at 20.1222 dB / 18.4080 dB edge; E2_ramp8 PASSES
        at 21.3343 dB / 20.3544 dB edge)."""
        import hashlib

        fix_path = ROOT / "tools" / "tests" / "fixtures" / "cusa05571_00_edge_fixture.npz"
        self.assertTrue(fix_path.exists(), f"Missing real fixture: {fix_path}")
        self.assertEqual(
            hashlib.sha256(fix_path.read_bytes()).hexdigest(),
            "915546cb3f6f0262b38b22284d20a2ff62b9b863b38d21f9855827ef25587f93",
        )
        with np.load(str(fix_path)) as fix:
            A_u16 = fix["A_u16"].copy()
            B_u16 = fix["B_u16"].copy()
            GT_u16 = fix["GT_u16"].copy()
            Fp = fix["F"].copy()
            GKp = fix["GK"].copy()
            y0, y1 = int(fix["y0"]), int(fix["y1"])
            x0, x1 = int(fix["x0"]), int(fix["x1"])
        self.assertEqual((y0, y1, x0, x1), (544, 576, 0, 64))

        # If the full raw dump bench_CUSA05571/bench_0000_*.rgba is present on disk, verify exact byte identity
        for cand_dir in (
            pathlib.Path(r"C:\vcfg-work\head_eval_windows\bench_CUSA05571"),
            pathlib.Path("/home/melnikoff/vcfg-pipeline/runtime/research/head-occlusion-20261023/bench_CUSA05571"),
        ):
            a_raw_p = cand_dir / "bench_0000_a.rgba"
            if a_raw_p.exists():
                b_raw_p = cand_dir / "bench_0000_b.rgba"
                gt_raw_p = cand_dir / "bench_0000_gt.rgba"
                self.assertEqual(
                    hashlib.sha256(a_raw_p.read_bytes()).hexdigest(),
                    "f59eed6999605cf8c93b8da32c873af7ac41012e316282e938cf1b088f37f542",
                )
                self.assertEqual(
                    hashlib.sha256(b_raw_p.read_bytes()).hexdigest(),
                    "3d0a1e13625f716c9f2800e8cffeb219d4a41c2e5a7c31afc40e5f8aa196b3b3",
                )
                self.assertEqual(
                    hashlib.sha256(gt_raw_p.read_bytes()).hexdigest(),
                    "96e8b71ffd5fa47722415a10ffb404d80503c28edbba88c68e2cd4e63cc95813",
                )
                a_full = np.frombuffer(a_raw_p.read_bytes(), dtype="<u2").reshape(1080, 1920, 4)[y0:y1, x0:x1, :3]
                b_full = np.frombuffer(b_raw_p.read_bytes(), dtype="<u2").reshape(1080, 1920, 4)[y0:y1, x0:x1, :3]
                gt_full = np.frombuffer(gt_raw_p.read_bytes(), dtype="<u2").reshape(1080, 1920, 4)[y0:y1, x0:x1, :3]
                self.assertTrue(np.array_equal(A_u16, a_full))
                self.assertTrue(np.array_equal(B_u16, b_full))
                self.assertTrue(np.array_equal(GT_u16, gt_full))
                break

        Ap = (A_u16.astype(np.float32) / np.float32(65535.0)).astype(np.float32)
        Bp = (B_u16.astype(np.float32) / np.float32(65535.0)).astype(np.float32)
        GTp = (GT_u16.astype(np.float32) / np.float32(65535.0)).astype(np.float32)

        dbg = {}
        out_p = vw.warp8(Ap, Bp, Fp, GKp, 0, t=0.5, dbg=dbg)
        psnr_all = -10.0 * math.log10(float(np.mean((out_p - GTp) ** 2)))
        psnr_edge = -10.0 * math.log10(float(np.mean((out_p[:, :24] - GTp[:, :24]) ** 2)))
        emov = np.concatenate(dbg["emov"], axis=0)
        mean_emov_oob = float(emov[:, :16].mean())

        # Base 01bf65c FAILS these 3 real-capture assertions:
        #   base psnr_all      = 20.1222 dB (< 21.0 dB)
        #   base psnr_edge     = 18.4080 dB (< 20.0 dB)
        #   base mean_emov_oob = 0.0853     (< 0.105)
        self.assertGreater(psnr_all, 21.0, f"Real CUSA05571_00 border crop PSNR={psnr_all:.4f} dB must exceed 21.0 dB (base=20.1222 dB)")
        self.assertGreater(psnr_edge, 20.0, f"Real CUSA05571_00 OOB-ramp x=0..24 PSNR={psnr_edge:.4f} dB must exceed 20.0 dB (base=18.4080 dB)")
        self.assertGreater(mean_emov_oob, 0.105, f"Real CUSA05571_00 x=0..16 mean emov={mean_emov_oob:.4f} must not collapse to 0.0853")

    def test_7_compiled_warp8_llvm_ir_parity_on_real_cusa05571_00_fixture(self):
        """Compile classic/shaders/warp8.ll (@taps, @tapsq, @cr, @crw, @side_lerp, @diff3_k, @diff3, @lerp4, @sat)
        into native LLVM bitcode via clang + lli and execute on 16 real CUSA05571_00 border pixels (x=0..15, y=16).
        Verifies numerical parity (max_abs_diff < 5e-7) with tools/bench/vcfg_warp.py::_taps and confirms
        compiled warp8.ll produces continuous OOB ramp values (whereas compiled 01bf65c warp8.ll collapses to 0.02)."""
        import json
        import shutil
        import struct
        import subprocess
        import tempfile

        clang_bin = None
        lli_bin = None
        cfg_p = ROOT / "config.json"
        if cfg_p.exists():
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
            llvm_dir = pathlib.Path(cfg.get("llvm_bin", ""))
            if (llvm_dir / "clang.exe").exists() and (llvm_dir / "lli.exe").exists():
                clang_bin = str(llvm_dir / "clang.exe")
                lli_bin = str(llvm_dir / "lli.exe")
        if not clang_bin:
            c22 = shutil.which("clang-22") or "/usr/bin/clang-22"
            l22 = shutil.which("lli-22") or "/usr/bin/lli-22"
            if pathlib.Path(c22).exists() and pathlib.Path(l22).exists():
                clang_bin, lli_bin = c22, l22
        if not clang_bin or not lli_bin:
            self.skipTest("LLVM clang + lli required for compiled warp8.ll execution")

        def f32_hex_llvm(val: float) -> str:
            f32_val = struct.unpack("<f", struct.pack("<f", float(val)))[0]
            f64_bits = struct.unpack("<Q", struct.pack("<d", float(f32_val)))[0]
            return f"0x{f64_bits:016X}"

        with np.load(str(ROOT / "tools" / "tests" / "fixtures" / "cusa05571_00_edge_fixture.npz")) as fix:
            Ap = (fix["A_u16"].astype(np.float32) / np.float32(65535.0)).astype(np.float32)
            Bp = (fix["B_u16"].astype(np.float32) / np.float32(65535.0)).astype(np.float32)
            Fp = fix["F"].copy()

        def img_to_llvm_global(name: str, img: np.ndarray) -> str:
            flat = img.reshape(-1, 3)
            elems = [
                f"<4 x float> <float {f32_hex_llvm(r)}, float {f32_hex_llvm(g)}, float {f32_hex_llvm(b)}, float 1.0>"
                for r, g, b in flat
            ]
            return f"@{name} = internal constant [{len(elems)} x <4 x float>] [" + ", ".join(elems) + "], align 16"

        warp8_ll_text = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
        idx_main = warp8_ll_text.index("define amdgpu_cs void @main")
        ir_body = warp8_ll_text[:idx_main]
        target_triple = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "aarch64-unknown-linux-gnu"
        ir_body = ir_body.replace('target triple = "amdgcn--"', f'target triple = "{target_triple}"')
        ir_body = ir_body.replace(
            "declare <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 immarg, float, float, <8 x i32>, <4 x i32>, i1 immarg, i32 immarg, i32 immarg)",
            "",
        )
        ir_body = ir_body.replace("@llvm.amdgcn.image.sample.lz.2d.v4f32.f32", "@sim_sample_lz")
        ir_clean = "\n".join(
            ln for ln in ir_body.splitlines()
            if not ln.startswith("declare ") or "@llvm.amdgcn." not in ln
        )

        vx_val = float(Fp[1, 0, 0])
        vy_val = float(Fp[1, 0, 1])
        queries = []
        for x in range(16):
            y = 16
            u_v, v_v = float(x) + 0.5, float(y) + 0.5
            sp_rgb, sc_rgb = Ap[y, x], Bp[y, x]
            es_v = float(vw.diff3_k(sp_rgb[None, None, :], sc_rgb[None, None, :])[0, 0])
            queries.append((sp_rgb, sc_rgb, u_v, v_v, vx_val, vy_val, 0.5, es_v, 24.0, 0.5))

        driver_lines = [
            ir_clean,
            "",
            img_to_llvm_global("g_ap", Ap),
            img_to_llvm_global("g_bp", Bp),
            '@fmt = private unnamed_addr constant [26 x i8] c"%.9g %.9g %.9g %.9g %.9g\\0A\\00"',
            "declare i32 @printf(ptr, ...)",
            "",
            "define <4 x float> @sim_sample_lz(i32 %dm, float %u, float %v, <8 x i32> %t, <4 x i32> %sm, i1 %un, i32 %c1, i32 %c2) {",
            "  %t0 = extractelement <8 x i32> %t, i32 0",
            "  %isp = icmp eq i32 %t0, 1",
            "  %base = select i1 %isp, ptr @g_ap, ptr @g_bp",
            "  %u0 = fsub float %u, 0.5",
            "  %v0 = fsub float %v, 0.5",
            "  %x_lo = call float @llvm.maxnum.f32(float %u0, float 0.0)",
            "  %x = call float @llvm.minnum.f32(float %x_lo, float 63.0)",
            "  %y_lo = call float @llvm.maxnum.f32(float %v0, float 0.0)",
            "  %y = call float @llvm.minnum.f32(float %y_lo, float 31.0)",
            "  %xf = call float @llvm.floor.f32(float %x)",
            "  %yf = call float @llvm.floor.f32(float %y)",
            "  %fx = fsub float %x, %xf",
            "  %fy = fsub float %y, %yf",
            "  %x0 = fptosi float %xf to i32",
            "  %y0 = fptosi float %yf to i32",
            "  %x1a = add i32 %x0, 1",
            "  %y1a = add i32 %y0, 1",
            "  %x1 = call i32 @llvm.smin.i32(i32 %x1a, i32 63)",
            "  %y1 = call i32 @llvm.smin.i32(i32 %y1a, i32 31)",
            "  %r0 = shl i32 %y0, 6",
            "  %r1 = shl i32 %y1, 6",
            "  %i00 = add i32 %r0, %x0",
            "  %i10 = add i32 %r0, %x1",
            "  %i01 = add i32 %r1, %x0",
            "  %i11 = add i32 %r1, %x1",
            "  %p00 = getelementptr <4 x float>, ptr %base, i32 %i00",
            "  %p10 = getelementptr <4 x float>, ptr %base, i32 %i10",
            "  %p01 = getelementptr <4 x float>, ptr %base, i32 %i01",
            "  %p11 = getelementptr <4 x float>, ptr %base, i32 %i11",
            "  %c00 = load <4 x float>, ptr %p00, align 16",
            "  %c10 = load <4 x float>, ptr %p10, align 16",
            "  %c01 = load <4 x float>, ptr %p01, align 16",
            "  %c11 = load <4 x float>, ptr %p11, align 16",
            "  %top = call <4 x float> @lerp4(<4 x float> %c00, <4 x float> %c10, float %fx)",
            "  %bot = call <4 x float> @lerp4(<4 x float> %c01, <4 x float> %c11, float %fx)",
            "  %res = call <4 x float> @lerp4(<4 x float> %top, <4 x float> %bot, float %fy)",
            "  ret <4 x float> %res",
            "}",
            "",
            "define i32 @main() {",
        ]
        for idx, q in enumerate(queries):
            sp_rgb, sc_rgb, u_v, v_v, vx_v, vy_v, t_v, es_v, vmn_v, csh_v = q
            tc1_v = np.float32(1.0) - np.float32(t_v)
            sp_vec = f"<float {f32_hex_llvm(sp_rgb[0])}, float {f32_hex_llvm(sp_rgb[1])}, float {f32_hex_llvm(sp_rgb[2])}, float 1.0>"
            sc_vec = f"<float {f32_hex_llvm(sc_rgb[0])}, float {f32_hex_llvm(sc_rgb[1])}, float {f32_hex_llvm(sc_rgb[2])}, float 1.0>"
            driver_lines += [
                f"  %tp_{idx} = insertelement <8 x i32> zeroinitializer, i32 1, i32 0",
                f"  %tc_{idx} = insertelement <8 x i32> zeroinitializer, i32 2, i32 0",
                f"  %rt_{idx} = call {{ <4 x float>, float }} @taps(<8 x i32> %tp_{idx}, <8 x i32> %tc_{idx}, <4 x i32> zeroinitializer, "
                f"float {f32_hex_llvm(u_v)}, float {f32_hex_llvm(v_v)}, float {f32_hex_llvm(vx_v)}, float {f32_hex_llvm(vy_v)}, "
                f"float {f32_hex_llvm(t_v)}, float {f32_hex_llvm(tc1_v)}, float 1.0, "
                f"<4 x float> {sp_vec}, <4 x float> {sc_vec}, float {f32_hex_llvm(es_v)}, i1 true, "
                f"float -1000.0, float -1000.0, float -1000.0, float -1000.0, float 0.0, float 0.0, float {f32_hex_llvm(vmn_v)})",
                f"  %et_{idx} = extractvalue {{ <4 x float>, float }} %rt_{idx}, 1",
                f"  %rq_{idx} = call {{ <4 x float>, <4 x float>, float }} @tapsq(<8 x i32> %tp_{idx}, <8 x i32> %tc_{idx}, <4 x i32> zeroinitializer, "
                f"float {f32_hex_llvm(u_v)}, float {f32_hex_llvm(v_v)}, float {f32_hex_llvm(vx_v)}, float {f32_hex_llvm(vy_v)}, "
                f"float {f32_hex_llvm(t_v)}, float {f32_hex_llvm(tc1_v)}, float 1.0, float {f32_hex_llvm(csh_v)}, "
                f"float -1000.0, float -1000.0, float -1000.0, float -1000.0)",
                f"  %b2q_{idx} = extractvalue {{ <4 x float>, <4 x float>, float }} %rq_{idx}, 1",
                f"  %eq_{idx} = extractvalue {{ <4 x float>, <4 x float>, float }} %rq_{idx}, 2",
                f"  %b2r_{idx} = extractelement <4 x float> %b2q_{idx}, i32 0",
                f"  %b2g_{idx} = extractelement <4 x float> %b2q_{idx}, i32 1",
                f"  %b2b_{idx} = extractelement <4 x float> %b2q_{idx}, i32 2",
                f"  %b2rd_{idx} = fpext float %b2r_{idx} to double",
                f"  %b2gd_{idx} = fpext float %b2g_{idx} to double",
                f"  %b2bd_{idx} = fpext float %b2b_{idx} to double",
                f"  %eqd_{idx} = fpext float %eq_{idx} to double",
                f"  %etd_{idx} = fpext float %et_{idx} to double",
                f"  call i32 (ptr, ...) @printf(ptr @fmt, double %b2rd_{idx}, double %b2gd_{idx}, double %b2bd_{idx}, double %eqd_{idx}, double %etd_{idx})",
            ]
        driver_lines += ["  ret i32 0", "}"]

        with tempfile.TemporaryDirectory() as td:
            ll_f = pathlib.Path(td) / "warp8_taps_host.ll"
            bc_f = pathlib.Path(td) / "warp8_taps_host.bc"
            ll_f.write_text("\n".join(driver_lines), encoding="utf-8")
            subprocess.run(
                [clang_bin, f"--target={target_triple}", "-O0", "-Wno-override-module", "-emit-llvm", "-c", str(ll_f), "-o", str(bc_f)],
                check=True,
            )
            lli_cmd = [lli_bin, "--dlopen=msvcrt.dll", str(bc_f)] if sys.platform == "win32" else [lli_bin, str(bc_f)]
            res = subprocess.run(lli_cmd, capture_output=True, text=True, check=True)

        rows = [[float(v) for v in ln.strip().split()] for ln in res.stdout.strip().splitlines()]
        self.assertEqual(len(rows), 16)
        max_abs_diff = 0.0
        for idx, q in enumerate(queries):
            sp_rgb, sc_rgb, u_v, v_v, vx_v, vy_v, t_v, es_v, vmn_v, csh_v = q
            u_arr, v_arr = np.float32([[u_v]]), np.float32([[v_v]])
            vx_arr, vy_arr = np.float32([[vx_v]]), np.float32([[vy_v]])
            sp_arr, sc_arr = sp_rgb[None, None, :], sc_rgb[None, None, :]
            es_arr = np.float32([[es_v]])
            _, b2_q, eb_q = vw._taps(Ap, Bp, u_arr, v_arr, vx_arr, vy_arr, 0.5, sp_arr, sc_arr, es_arr, True, True, csh=csh_v)
            py_vals = [float(b2_q[0, 0, 0]), float(b2_q[0, 0, 1]), float(b2_q[0, 0, 2]), float(eb_q[0, 0])]
            for k in range(4):
                max_abs_diff = max(max_abs_diff, abs(rows[idx][k] - py_vals[k]))

        # Compiled warp8.ll @tapsq must match Python vcfg_warp._taps within float32 rounding (< 5e-7; actual 1.12e-7)
        self.assertLess(max_abs_diff, 5e-7, f"Compiled warp8.ll vs vcfg_warp._taps max_abs_diff={max_abs_diff:.3e} >= 5e-7")
        # Across the OOB ramp x=4..10 (dc=6.93..0.93 px), compiled warp8.ll eb must be > 0.07 (base 01bf65c is 0.020000)
        for idx in range(4, 11):
            self.assertGreater(rows[idx][3], 0.07, f"Compiled warp8.ll eb at x={idx} collapsed to {rows[idx][3]:.6f}")


if __name__ == "__main__":
    unittest.main()
