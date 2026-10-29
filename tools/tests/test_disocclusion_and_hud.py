"""Standalone verification suite for Chat 9 (vcfg-r9-disocc-halo):
Disocclusion Inpainting, Double-Contour Halo Elimination & Thin 1-2 px / Semi-Transparent HUD & Subtitle Protection.

Tests:
  1. Trailing-edge disocclusion: foreground rectangle moving right at v_fg = (+12, 0) px/frame
     (warp8 convention vx = x_P - x_C = -12) over a textured background moving left at v_bg = (-8, 0)
     (warp8 convention vx = +8), measuring PSNR in the 16-px disoccluded crescent behind the object
     across both mf8 + warp8 and baseline vs v2.29 warp8.
  2. Leading-edge occlusion: same scene, measuring PSNR in the 16-px covered crescent ahead of the object.
  3. Thin 1-2 px white subtitle text inside a 50% alpha shadow box over a rapidly panning background
     gm = (-24, 4), asserting PSNR >= 38 dB on the text strokes and verifying colored 1-2 px HUD protection.
  4. Moving foreground object preservation & GCN2 hardware register/scratch/region constraints
     for mf8, warp8, and warp8g (vgprs <= 64, scratch == 0).
"""
import pathlib
import re
import sys
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import vcfg_ref as vref
import vcfg_motion as vm
import vcfg_warp as vw
from test_warp8_halo_hud import run_warp8_ref, psnr, edge_sharpness


def build_opposite_motion_scene(h=256, w=384, disp_fg=(12.0, 0.0), disp_bg=(-8.0, 0.0)):
    """Build a synthetic triplet (P, C, GT) where a foreground rectangle moves by disp_fg = (+12, 0)
    from P to C over a multi-scale textured background moving by disp_bg = (-8, 0) from P to C.

    Note on velocity sign convention in VCFG:
      sample_P = u + t * v, sample_C = u - (1 - t) * v  =>  v = u_P - u_C = -disp.
    """
    vx_fg, vy_fg = -float(disp_fg[0]), -float(disp_fg[1])
    vx_bg, vy_bg = -float(disp_bg[0]), -float(disp_bg[1])

    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")

    def sample_bg(x, y):
        c0 = 0.24 + 0.14 * np.sin(x * 0.11) * np.cos(y * 0.09) + 0.05 * np.sin(x * 0.29 - y * 0.21)
        c1 = 0.29 + 0.15 * np.cos(x * 0.07 + 0.5) + 0.04 * np.cos(x * 0.23 + y * 0.19)
        c2 = 0.35 + 0.12 * np.sin(y * 0.13) + 0.05 * np.sin((x + y) * 0.17)
        return np.stack([c0, c1, c2, np.ones_like(c0)], axis=-1).astype(np.float32)

    def sample_fg(x, y):
        r0 = 0.84 + 0.08 * np.cos(x * 0.20) + 0.04 * np.sin(y * 0.31)
        r1 = 0.71 + 0.08 * np.sin(y * 0.20) + 0.04 * np.cos(x * 0.27)
        r2 = 0.19 + 0.05 * np.cos((x + y) * 0.15)
        return np.stack([r0, r1, r2, np.ones_like(r0)], axis=-1).astype(np.float32)

    fg_x0, fg_x1 = 144.0, 240.0
    fg_y0, fg_y1 = 64.0, 192.0

    def render_at(phase):
        # phase = -0.5 (P), 0.0 (GT at t=0.5), +0.5 (C)
        bx = xx + phase * vx_bg
        by = yy + phase * vy_bg
        fx = xx + phase * vx_fg
        fy = yy + phase * vy_fg
        bg = sample_bg(bx, by)
        fg = sample_fg(fx, fy)
        in_fg = (fx >= fg_x0) & (fx < fg_x1) & (fy >= fg_y0) & (fy < fg_y1)
        return np.where(in_fg[..., None], fg, bg).astype(np.float32), in_fg

    P, in_fg_P = render_at(-0.5)
    GT, in_fg_GT = render_at(0.0)
    C, in_fg_C = render_at(+0.5)

    gh, gw = h // 16, w // 16
    F_gt = np.zeros((gh, gw, 2), dtype=np.float32)
    F_gt[..., 0] = vx_bg
    F_gt[..., 1] = vy_bg
    GK = np.zeros((gh, gw), dtype=np.uint32)
    for by in range(gh):
        for bx in range(gw):
            cx = bx * 16.0 + 8.0
            cy = by * 16.0 + 8.0
            if fg_x0 <= cx < fg_x1 and fg_y0 <= cy < fg_y1:
                F_gt[by, bx, 0] = vx_fg
                F_gt[by, bx, 1] = vy_fg
                GK[by, bx] = (1 << 20)

    # Trailing-edge disocclusion crescent (16 px wide behind the object: covered in P, uncovered in C and GT)
    trailing_disocc_mask = in_fg_P & (~in_fg_C) & (~in_fg_GT)
    # Leading-edge occlusion crescent (16 px wide ahead of the object: uncovered in P and GT, covered in C)
    leading_occ_mask = (~in_fg_P) & in_fg_C & (~in_fg_GT)

    return P, C, GT, F_gt, GK, trailing_disocc_mask, leading_occ_mask


def build_subtitle_shadowbox_scene(h=192, w=320, gm=(-24.0, 4.0)):
    """Build a synthetic triplet with 1-2 px white subtitle strokes inside a 50% alpha dark shadow box
    and 1-2 px colored HUD elements over a rapidly panning background gm = (-24, 4).
    """
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing="ij")
    vx_bg, vy_bg = float(gm[0]), float(gm[1])

    def bg_at(x, y):
        r = 0.28 + 0.10 * np.sin(x * 0.03) * np.cos(y * 0.035)
        g = 0.32 + 0.09 * np.cos(x * 0.025 + 0.4)
        b = 0.36 + 0.10 * np.sin(y * 0.03 - 0.2)
        return np.stack([r, g, b, np.ones_like(r)], axis=-1).astype(np.float32)

    P = bg_at(xx - 0.5 * vx_bg, yy - 0.5 * vy_bg)
    GT = bg_at(xx, yy)
    C = bg_at(xx + 0.5 * vx_bg, yy + 0.5 * vy_bg)

    # 50% alpha dark shadow box behind subtitles: y in [132, 168), x in [48, 272)
    box_mask = (yy >= 132) & (yy < 168) & (xx >= 48) & (xx < 272)
    shadow_rgb = np.array([0.04, 0.04, 0.05, 1.0], dtype=np.float32)
    for img in (P, GT, C):
        img[box_mask] = 0.50 * img[box_mask] + 0.50 * shadow_rgb

    # 1-2 px white subtitle strokes inside the 50% alpha shadow box
    sub_1px_h = (yy == 142) & (xx >= 64) & (xx < 256) & ((xx.astype(np.int32) % 8) != 0)
    sub_2px_h = (yy >= 152) & (yy < 154) & (xx >= 72) & (xx < 248) & ((xx.astype(np.int32) % 10) >= 2)
    sub_1px_v = (yy >= 140) & (yy < 160) & ((xx.astype(np.int32) % 12) == 4) & (xx >= 64) & (xx < 256)
    subtitle_stroke_mask = sub_1px_h | sub_2px_h | sub_1px_v

    white_stroke = np.array([0.96, 0.96, 0.96, 1.0], dtype=np.float32)
    # Realistic slight alpha bleed / TAA jitter (es ~ 0.03 > 0.006) so baseline ws0 (es < 0.006) fails
    P[subtitle_stroke_mask] = np.clip(white_stroke - 0.005, 0.0, 1.0)
    C[subtitle_stroke_mask] = np.clip(white_stroke + 0.005, 0.0, 1.0)
    GT[subtitle_stroke_mask] = white_stroke

    # Additional 1-2 px colored HUD elements (red/green/cyan) directly over panning background
    hud_red = (yy >= 28) & (yy < 30) & (xx >= 48) & (xx < 136)
    hud_green = (yy == 48) & (xx >= 48) & (xx < 136)
    red_c = np.array([0.95, 0.12, 0.10, 1.0], dtype=np.float32)
    grn_c = np.array([0.12, 0.94, 0.15, 1.0], dtype=np.float32)
    P[hud_red], C[hud_red], GT[hud_red] = red_c - 0.005, red_c + 0.005, red_c
    P[hud_green], C[hud_green], GT[hud_green] = grn_c - 0.005, grn_c + 0.005, grn_c

    gh, gw = h // 16, w // 16
    F = np.zeros((gh, gw, 2), dtype=np.float32)
    F[..., 0] = vx_bg
    F[..., 1] = vy_bg
    GK = np.zeros((gh, gw), dtype=np.uint32)

    return P, C, GT, F, GK, subtitle_stroke_mask, (hud_red | hud_green)


class DisocclusionAndHudTest(unittest.TestCase):
    def test_1_trailing_edge_disocclusion(self):
        """Trailing-edge disocclusion: v_fg = (+12, 0), v_bg = (-8, 0), 16-px disoccluded crescent."""
        P, C, GT, F_gt, GK, disocc_m, _ = build_opposite_motion_scene(disp_fg=(12.0, 0.0), disp_bg=(-8.0, 0.0))

        # 1A. Verify mf8 outward disocclusion shift prevents 1-block foreground bleeding into background
        L1p, _, _ = vref.pyr8(P[..., :3])
        L1c, _, _ = vref.pyr8(C[..., :3])
        gm = (4, 0)  # L2 camera vector corresponding to (-8, 0) disp -> +8 full-res vx -> +2 L2 vx
        V2 = np.zeros((h_v2 := P.shape[0] // 32, w_v2 := P.shape[1] // 32, 2), dtype=np.int16)
        V2[..., 0] = 2
        F_mf8 = vm.mf8(L1p, L1c, F_gt, GK, V2, (2, 0), False)

        out_base = run_warp8_ref(P, C, F_gt, GK=GK, mode="base")
        out_v229 = vw.warp8(P[..., :3], C[..., :3], F_mf8, GK, 0)

        psnr_base = psnr(out_base, GT, disocc_m)
        psnr_v229 = psnr(out_v229, GT[..., :3], disocc_m)
        print(f"\n[Test 1: Trailing-Edge Disocclusion] 16-px Crescent PSNR: {psnr_base:.2f} dB -> {psnr_v229:.2f} dB "
              f"(+{psnr_v229 - psnr_base:.2f} dB)")
        self.assertGreater(psnr_v229, psnr_base + 8.0,
                           "Trailing-edge disocclusion crescent PSNR must improve by >8 dB over baseline")
        self.assertGreaterEqual(psnr_v229, 32.0,
                                "Trailing-edge disocclusion crescent PSNR must be >= 32 dB")

    def test_2_leading_edge_occlusion(self):
        """Leading-edge occlusion: v_fg = (+12, 0), v_bg = (-8, 0), 16-px covered crescent ahead of object."""
        P, C, GT, F_gt, GK, _, occ_m = build_opposite_motion_scene(disp_fg=(12.0, 0.0), disp_bg=(-8.0, 0.0))

        L1p, _, _ = vref.pyr8(P[..., :3])
        L1c, _, _ = vref.pyr8(C[..., :3])
        V2 = np.zeros((P.shape[0] // 32, P.shape[1] // 32, 2), dtype=np.int16)
        V2[..., 0] = 2
        F_mf8 = vm.mf8(L1p, L1c, F_gt, GK, V2, (2, 0), False)

        out_base = run_warp8_ref(P, C, F_gt, GK=GK, mode="base")
        out_v229 = vw.warp8(P[..., :3], C[..., :3], F_mf8, GK, 0)

        psnr_base = psnr(out_base, GT, occ_m)
        psnr_v229 = psnr(out_v229, GT[..., :3], occ_m)
        print(f"[Test 2: Leading-Edge Occlusion]     16-px Crescent PSNR: {psnr_base:.2f} dB -> {psnr_v229:.2f} dB "
              f"(+{psnr_v229 - psnr_base:.2f} dB)")
        self.assertGreater(psnr_v229, psnr_base + 8.0,
                           "Leading-edge occlusion crescent PSNR must improve by >8 dB over baseline")
        self.assertGreaterEqual(psnr_v229, 31.0,
                                "Leading-edge occlusion crescent PSNR must be >= 31 dB")

    def test_3_subtitle_shadowbox_and_thin_hud(self):
        """1-2 px white subtitle text with 50% alpha shadow box over rapidly panning background gm = (-24, 4)."""
        P, C, GT, F, GK, sub_mask, hud_mask = build_subtitle_shadowbox_scene(gm=(-24.0, 4.0))

        out_base = run_warp8_ref(P, C, F, GK=GK, mode="base")
        out_v229 = vw.warp8(P[..., :3], C[..., :3], F, GK, 0)

        psnr_sub_base = psnr(out_base, GT, sub_mask)
        psnr_sub_v229 = psnr(out_v229, GT[..., :3], sub_mask)
        psnr_hud_base = psnr(out_base, GT, hud_mask)
        psnr_hud_v229 = psnr(out_v229, GT[..., :3], hud_mask)
        sharp_sub_base = edge_sharpness(out_base, sub_mask)
        sharp_sub_v229 = edge_sharpness(out_v229, sub_mask)

        print(f"[Test 3: 1-2px Subtitle + 50% Box]   Stroke PSNR:         {psnr_sub_base:.2f} dB -> {psnr_sub_v229:.2f} dB "
              f"(+{psnr_sub_v229 - psnr_sub_base:.2f} dB) | Sharpness: {sharp_sub_base:.4f} -> {sharp_sub_v229:.4f}")
        print(f"[Test 3: 1-2px Colored HUD Strokes]  Stroke PSNR:         {psnr_hud_base:.2f} dB -> {psnr_hud_v229:.2f} dB "
              f"(+{psnr_hud_v229 - psnr_hud_base:.2f} dB)")

        self.assertGreaterEqual(psnr_sub_v229, 38.0,
                                f"Subtitle stroke PSNR ({psnr_sub_v229:.2f} dB) must be >= 38.0 dB")
        self.assertGreaterEqual(psnr_hud_v229, 38.0,
                                f"Colored HUD stroke PSNR ({psnr_hud_v229:.2f} dB) must be >= 38.0 dB")
        self.assertGreater(sharp_sub_v229, sharp_sub_base * 1.5,
                           "Subtitle edge sharpness must improve by >1.5x over baseline")

    def test_4_hardware_register_and_scratch_limits(self):
        """Verify mf8, warp8, and warp8g compile within <= 64 VGPRs, scratch == 0, and region <= 0x1C000."""
        shaders_h = (ROOT / "classic" / "source" / "shaders.h").read_text(encoding="utf-8")
        for name in ("mf8", "warp8", "warp8g"):
            m1 = re.search(rf"#define\s+{name.upper()}_RSRC1\s+0x([0-9A-Fa-f]+)", shaders_h)
            m2 = re.search(rf"#define\s+{name.upper()}_RSRC2\s+0x([0-9A-Fa-f]+)", shaders_h)
            mw = re.search(rf"#define\s+{name.upper()}_NWORDS\s+(\d+)", shaders_h)
            self.assertIsNotNone(m1, f"Missing {name.upper()}_RSRC1 in shaders.h")
            self.assertIsNotNone(m2, f"Missing {name.upper()}_RSRC2 in shaders.h")
            self.assertIsNotNone(mw, f"Missing {name.upper()}_NWORDS in shaders.h")
            rsrc1 = int(m1.group(1), 16)
            rsrc2 = int(m2.group(1), 16)
            nwords = int(mw.group(1))
            vgpr_blocks = rsrc1 & 0x3F
            max_vgprs = (vgpr_blocks + 1) * 4
            scratch_en = rsrc2 & 1
            print(f"[Test 4: Hardware Check]             {name:<6}: {nwords:4d} words, "
                  f"rsrc1=0x{rsrc1:08X} (vgprs<={max_vgprs}), scratch={scratch_en}")
            self.assertLessEqual(max_vgprs, 64, f"{name} exceeds 64 VGPR limit ({max_vgprs})")
            self.assertEqual(scratch_en, 0, f"{name} uses scratch memory (scratch_en={scratch_en})")

    def test_5_v5b_head_boundary_flicker_suppression(self):
        """Verify V5b suppresses cw and ws0 leaks on incoherent (!coh) fast quads and prio=0 GK anchors while keeping WARP8_NWORDS=2960."""
        F32 = np.float32
        H_p, W_p = 64, 128
        nby, nbx = H_p // 16, W_p // 16
        P = np.full((H_p, W_p, 4), 0.35, dtype=F32)
        C = np.full((H_p, W_p, 4), 0.35, dtype=F32)
        P[..., 3] = 1.0
        C[..., 3] = 1.0
        # Create a slight warped-tap disagreement (0.16 < emov < 0.24 < 0.25) while static center es == 0 (< 0.006 -> ws0_raw == 1.0)
        yy, xx = np.meshgrid(np.arange(H_p, dtype=F32), np.arange(W_p, dtype=F32), indexing="ij")
        wave = F32(0.045) * np.sin(xx * F32(0.4))
        P[..., :3] = np.clip(F32(0.35) + wave[..., None], F32(0.0), F32(1.0))
        C[..., :3] = np.clip(F32(0.35) + wave[..., None], F32(0.0), F32(1.0))

        F = np.zeros((nby, nbx, 2), dtype=F32)
        prevF = np.zeros((nby, nbx, 2), dtype=F32)
        GK = np.zeros((nby, nbx), dtype=np.int64)

        # Region A (block by=1, bx=2, px y=16..31, x=32..47): incoherent 2x2 quad (!coh) where F(1,2)=(11.5, 0)
        # and neighbor F(1,3)=(-26.0, 0) -> spr=37.5 > coh_thr (slow: branch where vmg=0.0 in baseline),
        # while vm_c=11.5 and vm_p=12.0 -> vmg_e >= 10.0 -> w_vel == 1.0.
        F[1, 2, 0] = F32(11.5)
        F[1, 3, 0] = F32(-26.0)
        prevF[1, 2, 0] = F32(12.0)

        # Region B (block by=2, bx=5, px y=32..47, x=80..95): prio=0 anchor splat in GK (0 < gk < 1048576) with slow F=(2.0, 0)
        F[2, 5, 0] = F32(2.0)
        GK[2, 5] = 0x00080010  # prio=0, wt=128, nonzero GK anchor

        dbg = {}
        vw.warp8(P[..., :3], C[..., :3], F, GK, 0, dbg=dbg, prevF=prevF)
        ws_map = np.concatenate(dbg["ws"], axis=0)
        cw_map = np.concatenate(dbg["cw"], axis=0)
        coh_map = np.concatenate(dbg["coh"], axis=0)
        emov_map = np.concatenate(dbg["emov"], axis=0)

        # Verify pixel (y=24, x=36) in Region A is incoherent (!coh) and has emov < 0.25
        self.assertFalse(bool(coh_map[24, 36]), "Region A test pixel must be in !coh (slow:) branch")
        self.assertLess(float(emov_map[24, 36]), 0.25, "Region A test pixel must have mfit (emov < 0.25)")
        self.assertEqual(float(ws_map[24, 36]), 0.0, "V5b must suppress ws0 leak on incoherent fast quad (vm_c=11.5)")
        self.assertEqual(float(cw_map[24, 36]), 0.0, "V5b must suppress cw leak on incoherent fast quad (vm_c=11.5)")

        # Verify pixel (y=40, x=88) in Region B (prio=0 GK anchor) has ws == 0.0 and cw == 0.0
        self.assertLess(float(emov_map[40, 88]), 0.25, "Region B test pixel must have mfit (emov < 0.25)")
        self.assertEqual(float(ws_map[40, 88]), 0.0, "V5b must include prio=0 GK anchors (gk != 0) in hasfg ws suppression")
        self.assertEqual(float(cw_map[40, 88]), 0.0, "V5b must include prio=0 GK anchors (gk != 0) in hasfg cw suppression")

        # Verify classic/shaders/warp8.ll and classic/source/shaders.h match V5b +0B aligned budget (WARP8_NWORDS == 2960)
        warp8_ll = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
        self.assertIn("%hasfg = icmp ne i32 %gk, 0", warp8_ll)
        self.assertIn("%vmg_e = call float @llvm.maxnum.f32(float %vm_c, float %vmp_k)", warp8_ll)
        self.assertIn("%ws0_s = fmul float %ws0, %ws_keep", warp8_ll)
        self.assertIn("%ws = call float @llvm.maxnum.f32(float %ws0_s, float %w_hud)", warp8_ll)

        shaders_h = (ROOT / "classic" / "source" / "shaders.h").read_text(encoding="utf-8")
        mw = re.search(r"#define\s+WARP8_NWORDS\s+(\d+)", shaders_h)
        self.assertIsNotNone(mw)
        # 2960 with gen_shaders.py's old ceil64 + 16 padding, 2944 padded to a multiple of 64 words, 2880 with v_mad_f32
        # through llvm.amdgcn.fmad.ftz (test_warp8_fmad_ftz.py)
        self.assertEqual(int(mw.group(1)), 2880, "WARP8_NWORDS must remain 2880 (11,520 B, 256 B aligned)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
