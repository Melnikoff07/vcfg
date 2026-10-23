"""Benchmark and verification suite for 8x8 Sub-Block Contour Refinement & Dual-Layer FG/BG Motion Field (vcfg-r8-fine-grid).

Tests vcfg_motion.py (mf8) + vcfg_warp.py (warp8) BEFORE (16x16 homogeneous block baseline) and AFTER (8x8 sub-block
quadrant split + dual-layer FG/BG contour field) on 1920x1080 synthetic triplets:
  1. Scene 1: Thin moving diagonal bar (10 px wide) crossing a panning background (gm = (-16, 0) full-res px/frame),
     evaluated under both Warm-Start temporal propagation and Ideal Temporal Seed (FT).
  2. Scene 2: Articulated character silhouette with thin (8-10 px) extremities and diagonal weapon/cape over panning terrain.
  3. Scene 3: Static HUD / crosshair / subtitle stroke preservation over moving background (0 regression guard).
  4. Shader hardware resource & IR verification (mf8 <= 64 VGPRs, warp8 <= 64 VGPRs, warp8g <= 64 VGPRs, scratch == 0).
"""
import pathlib
import re
import sys
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
BENCH = ROOT / "tools" / "bench"
sys.path.insert(0, str(BENCH))

import vcfg_motion as vm
import vcfg_ref as vref
import vcfg_warp as vw

H, W = 1080, 1920
NBY, NBX = 68, 120
BIG = np.iinfo(np.int64).max


def psnr(pred, gt, mask=None):
    d = (pred[..., :3] - gt[..., :3]) ** 2
    if mask is not None:
        d = d[mask]
    mse = float(d.mean())
    return 99.0 if mse <= 1e-12 else float(10.0 * np.log10(1.0 / mse))


def make_textured_canvas(h, w, seed, base_luma=0.32, contrast=0.18, tint=(0.9, 0.95, 1.0)):
    rng = np.random.default_rng(seed)
    g1 = rng.standard_normal((h // 16 + 4, w // 16 + 4)).astype(np.float32)
    g2 = rng.standard_normal((h // 4 + 4, w // 4 + 4)).astype(np.float32)
    g3 = rng.standard_normal((h, w)).astype(np.float32)
    y1 = np.repeat(np.repeat(g1, 16, axis=0), 16, axis=1)[:h, :w]
    y2 = np.repeat(np.repeat(g2, 4, axis=0), 4, axis=1)[:h, :w]
    pattern = np.clip(0.45 * y1 + 0.35 * y2 + 0.20 * g3, -1.5, 1.5)
    luma = np.clip(base_luma + contrast * pattern, 0.05, 0.95)
    return np.stack([
        np.clip(luma * tint[0], 0.02, 0.98),
        np.clip(luma * tint[1], 0.02, 0.98),
        np.clip(luma * tint[2], 0.02, 0.98),
    ], axis=-1).astype(np.float32)


def dilate_mask(mask, radius=8):
    out = np.zeros_like(mask)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Baseline 16x16 Homogeneous mf8 (Before Chat 8) for Head-to-Head Comparison
# ---------------------------------------------------------------------------------------------------------------
def baseline_mf8_16x16(L1p, L1c, FT, GK, V2, gm, fade):
    nby, nbx = FT.shape[:2]
    bx, by = vm._grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    gk = GK[by, bx]
    hasfg = gk != 0
    isfgk = gk >= (1 << 20)
    nfgk = ~isfgk
    isanc = hasfg & nfgk
    notanc = ~isanc
    gmx, gmy = gm
    gmx2, gmy2 = gmx * 2, gmy * 2
    still = np.bool_((gmx | gmy) == 0)
    gmx4, gmy4 = np.float32(gmx * 4), np.float32(gmy * 4)
    av = V2.reshape(-1, 2)[gk & 4095].astype(np.int64) * 2
    cap = np.where(nfgk, 32, 8)
    lsh = np.where(nfgk, 2, 1)
    h1, w1 = L1c.shape

    def ldT(ox, oy):
        x0 = bx + ox
        y0 = by + oy
        x = np.where(x0 < 0, min(1, nbx - 1), np.where(x0 >= nbx, max(nbx - 2, 0), x0))
        y = np.where(y0 < 0, min(1, nby - 1), np.where(y0 >= nby, max(nby - 2, 0), y0))
        return FT[y, x]

    nbrs = [ldT(ox, oy) for oy in (-1, 0, 1) for ox in (-1, 0, 1)]
    nbq = [(vm.rnd(v[:, 0] * np.float32(0.5)), vm.rnd(v[:, 1] * np.float32(0.5))) for v in nbrs]
    best = np.full(N, BIG, np.int64)
    winv = np.zeros((N, 2), np.float32)
    for sub in range(10):
        if sub == 0:
            k = 4
        elif sub <= 8:
            k = sub - 1 if sub - 1 < 4 else sub
        else:
            k = 4
        cv = nbrs[k].copy()
        if sub == 9:
            cv = np.where(notanc[:, None], np.array([gmx4, gmy4], np.float32)[None, :], cv)
        cx, cy = vm.rnd(cv[:, 0] * np.float32(0.5)), vm.rnd(cv[:, 1] * np.float32(0.5))
        aoff = np.abs(cx - av[:, 0]) + np.abs(cy - av[:, 1])
        sc = np.abs(cx - gmx2) + np.abs(cy - gmy2)
        afar = (aoff > 3) & ((aoff > 16) | ((~still) & (sc * 2 <= aoff)))
        ap = isanc & afar
        scx = np.where(ap, cx >> 1, 0)
        scy = np.where(ap, cy >> 1, 0)
        Xu, Yu = X + scx, Y + scy
        sad, _ = vm.sad_bilateral(L1p, L1c, Xu, Yu, cx, cy)
        terms = [np.minimum(np.abs(cx - nx) + np.abs(cy - ny), cap) for nx, ny in nbq]
        sm0 = sum(terms[j] for j in (0, 1, 2, 3, 5, 6, 7, 8))
        sm = sm0 + np.where(nfgk, terms[4] * 3, 0)
        pen = sm << lsh
        bias = np.where(hasfg, 96, 0) if sub == 0 else 0
        apen = np.where(ap, 512, 0)
        xc_0 = Xu - (cx >> 1)
        xp_0 = xc_0 + cx
        yc_0 = Yu - (cy >> 1)
        yp_0 = yc_0 + cy
        oob = ((xc_0 < 0) | (xc_0 > w1 - 8) | (xp_0 < 0) | (xp_0 > w1 - 8) |
               (yc_0 < 0) | (yc_0 > h1 - 8) | (yp_0 < 0) | (yp_0 > h1 - 8))
        oob_p = np.where(oob & (sc > 3) & (sm0 > 24) & nfgk, 96, 0)
        cost = sad + 256 + pen + apen + oob_p - bias
        key = (cost << 8) | sub
        valid = np.full(N, sub == 0) if fade else np.ones(N, bool)
        key = np.where(valid, key, BIG)
        better = key < best
        best = np.where(better, key, best)
        winv = np.where(better[:, None], cv, winv)
    return winv.reshape(nby, nbx, 2)


def epe_8x8(F8_est, F8_gt, mask_8x8):
    epe = np.sqrt(((F8_est - F8_gt) ** 2).sum(axis=-1))
    return float(epe[mask_8x8].mean())


# ---------------------------------------------------------------------------------------------------------------
# Scene Builders
# ---------------------------------------------------------------------------------------------------------------
def build_scene1_diagonal_bar():
    """Scene 1: 10 px wide diagonal bar moving at v_fg = (+16, -8) full-res px/frame across a panning background
    moving at v_bg = (-16, 0) full-res px/frame (gm = (-4, 0) L2 px)."""
    bg = make_textured_canvas(H + 128, W + 128, seed=10, base_luma=0.28, contrast=0.16, tint=(0.85, 0.92, 1.0))
    fg = make_textured_canvas(H + 128, W + 128, seed=20, base_luma=0.72, contrast=0.20, tint=(1.0, 0.85, 0.7))
    v_bg = (-16.0, 0.0)
    v_fg = (16.0, -8.0)
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")

    def render_at(phase):
        bx = np.clip(np.rint(xx + 64 + phase * v_bg[0]).astype(np.int32), 0, bg.shape[1] - 1)
        by = np.clip(np.rint(yy + 64 + phase * v_bg[1]).astype(np.int32), 0, bg.shape[0] - 1)
        fx = np.clip(np.rint(xx + 64 + phase * v_fg[0]).astype(np.int32), 0, fg.shape[1] - 1)
        fy = np.clip(np.rint(yy + 64 + phase * v_fg[1]).astype(np.int32), 0, fg.shape[0] - 1)
        img = bg[by, bx].copy()
        fg_img = fg[fy, fx]
        mx = xx + phase * v_fg[0]
        my = yy + phase * v_fg[1]
        in_bar = (my >= 300) & (my < 780) & (np.abs((mx - 960.0) - 0.5 * (my - 540.0)) <= 5.0)
        in_hub = (my >= 700) & (my < 780) & (mx >= 1000) & (mx < 1080)
        mask = in_bar | in_hub
        img[mask] = fg_img[mask]
        return img, in_bar, mask

    A, _, _ = render_at(-0.5)
    GT, bar_GT, mask_GT = render_at(0.0)
    B, _, _ = render_at(+0.5)

    bar_blocks = bar_GT[:67 * 16, :].reshape(67, 16, 120, 16).any(axis=(1, 3))
    prevF = np.zeros((NBY, NBX, 2), np.float32)
    prevF[..., 0] = v_bg[0]
    prevF[:67, :][bar_blocks] = v_fg

    q_cov = np.zeros((NBY * 2, NBX * 2), np.float32)
    q_cov[:67 * 2, :] = bar_GT[:67 * 16, :].reshape(67 * 2, 8, 120 * 2, 8).mean(axis=(1, 3))
    F8_gt = np.zeros((NBY * 2, NBX * 2, 2), np.float32)
    F8_gt[..., 0] = v_bg[0]
    F8_gt[q_cov >= 0.25] = v_fg
    q_mask = q_cov >= 0.5

    bnd_roi = dilate_mask(bar_GT, radius=8)
    return A, GT, B, prevF, bar_GT, bnd_roi, F8_gt, q_mask


def build_scene2_articulated_silhouette():
    """Scene 2: Articulated character silhouette with a core body hub and thin (8-10 px wide) diagonal weapon/spear
    and limbs moving at v_fg = (-16, 8) full-res px/frame over panning terrain v_bg = (16, 0) full-res px/frame."""
    bg = make_textured_canvas(H + 128, W + 128, seed=110, base_luma=0.30, contrast=0.17, tint=(0.90, 0.96, 0.88))
    fg = make_textured_canvas(H + 128, W + 128, seed=120, base_luma=0.74, contrast=0.20, tint=(1.0, 0.86, 0.72))
    v_bg = (16.0, 0.0)
    v_fg = (-16.0, 8.0)
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")

    def render_at(phase):
        bx = np.clip(np.rint(xx + 64 + phase * v_bg[0]).astype(np.int32), 0, bg.shape[1] - 1)
        by = np.clip(np.rint(yy + 64 + phase * v_bg[1]).astype(np.int32), 0, bg.shape[0] - 1)
        fx = np.clip(np.rint(xx + 64 + phase * v_fg[0]).astype(np.int32), 0, fg.shape[1] - 1)
        fy = np.clip(np.rint(yy + 64 + phase * v_fg[1]).astype(np.int32), 0, fg.shape[0] - 1)
        img = bg[by, bx].copy()
        fg_img = fg[fy, fx]
        mx = xx + phase * v_fg[0]
        my = yy + phase * v_fg[1]
        # Core torso hub (72x96) + two 10 px diagonal extremities (spear & extended arm/leg)
        torso = (my >= 520) & (my < 616) & (mx >= 924) & (mx < 996)
        spear = (my >= 280) & (my < 760) & (np.abs((mx - 960.0) + 0.5 * (my - 540.0)) <= 5.0)
        limb = (my >= 420) & (my < 680) & (np.abs((mx - 880.0) - 0.5 * (my - 550.0)) <= 4.5)
        thin_ext = (spear | limb) & (~torso)
        mask = torso | spear | limb
        img[mask] = fg_img[mask]
        return img, thin_ext, mask

    A, _, _ = render_at(-0.5)
    GT, ext_GT, mask_GT = render_at(0.0)
    B, _, _ = render_at(+0.5)

    fg_blocks = mask_GT[:67 * 16, :].reshape(67, 16, 120, 16).any(axis=(1, 3))
    prevF = np.zeros((NBY, NBX, 2), np.float32)
    prevF[..., 0] = v_bg[0]
    prevF[:67, :][fg_blocks] = v_fg

    q_cov = np.zeros((NBY * 2, NBX * 2), np.float32)
    q_cov[:67 * 2, :] = ext_GT[:67 * 16, :].reshape(67 * 2, 8, 120 * 2, 8).mean(axis=(1, 3))
    F8_gt = np.zeros((NBY * 2, NBX * 2, 2), np.float32)
    F8_gt[..., 0] = v_bg[0]
    F8_gt[q_cov >= 0.25] = v_fg
    q_mask = q_cov >= 0.5

    bnd_roi = dilate_mask(ext_GT, radius=8)
    return A, GT, B, prevF, ext_GT, bnd_roi, F8_gt, q_mask


def build_scene3_static_hud_guard():
    """Scene 3: Static 2 px white crosshair and colored HUD bar over a panning background."""
    bg = make_textured_canvas(H + 64, W + 64, seed=300, base_luma=0.26, contrast=0.14, tint=(0.88, 0.92, 1.0))
    v_bg = (16.0, 0.0)
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")

    def sample_bg(phase):
        bx = np.clip(np.rint(xx + 32 + phase * v_bg[0]).astype(np.int32), 0, bg.shape[1] - 1)
        by = np.clip(np.rint(yy + 32 + phase * v_bg[1]).astype(np.int32), 0, bg.shape[0] - 1)
        return bg[by, bx].copy()

    A, GT, B = sample_bg(-0.5), sample_bg(0.0), sample_bg(+0.5)
    ch_h = (np.abs(yy - 540) <= 1.0) & (np.abs(xx - 960) <= 24)
    ch_v = (np.abs(xx - 960) <= 1.0) & (np.abs(yy - 540) <= 24)
    hud_bar = (yy >= 64) & (yy < 68) & (xx >= 120) & (xx < 420)
    hud_mask = ch_h | ch_v | hud_bar
    for img in (A, GT, B):
        img[ch_h | ch_v] = np.array([0.95, 0.95, 0.95], dtype=np.float32)
        img[hud_bar] = np.array([0.90, 0.22, 0.15], dtype=np.float32)

    prevF = np.zeros((NBY, NBX, 2), np.float32)
    prevF[..., 0] = v_bg[0]
    return A, GT, B, prevF, hud_mask


def eval_scene(A, B, prevF):
    L1a, L2a, L3a = vref.pyr8(A)
    L1b, L2b, L3b = vref.pyr8(B)
    V3, sum_b = vm.me3(L3a, L3b, prevF)
    V2, S2 = vm.me2(L2a, L2b, V3, prevF)
    gm = vm.gm8(V2, S2)
    GK, _ = vm.fgs8(L2a, L2b, V2, S2, gm)
    FT, cutn, fade = vm.me1(L1a, L1b, V2, GK, gm, sum_b, int(L3a.sum()), prevF)

    # Before (16x16 homogeneous mf8 + warp8 without sub-block unpacking)
    F_base = baseline_mf8_16x16(L1a, L1b, FT, GK, V2, gm, fade)
    mid_base = vw.warp8(A, B, F_base, GK, cutn, t=0.5, wp={"sub8": False})
    F8_base = np.repeat(np.repeat(F_base, 2, axis=0), 2, axis=1)

    # After (8x8 sub-block contour split mf8 + dual-layer warp8)
    F_new = vm.mf8(L1a, L1b, FT, GK, V2, gm, fade)
    mid_new = vw.warp8(A, B, F_new, GK, cutn, t=0.5)
    F8_new = vm.unpack_f_8x8(F_new)

    # Ideal FT seed evaluation (isolating mf8 + warp8 contour capability)
    F_base_id = baseline_mf8_16x16(L1a, L1b, prevF, GK, V2, gm, False)
    mid_base_id = vw.warp8(A, B, F_base_id, GK, 0, t=0.5, wp={"sub8": False})
    F_new_id = vm.mf8(L1a, L1b, prevF, GK, V2, gm, False)
    mid_new_id = vw.warp8(A, B, F_new_id, GK, 0, t=0.5)

    return dict(
        mid_base=mid_base, mid_new=mid_new,
        F8_base=F8_base, F8_new=F8_new,
        mid_base_id=mid_base_id, mid_new_id=mid_new_id,
    )


class FineGrid8x8FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Scene 1: Thin diagonal bar crossing panning background
        A1, GT1, B1, prevF1, bar1, bnd1, F8_gt1, qm1 = build_scene1_diagonal_bar()
        r1 = eval_scene(A1, B1, prevF1)
        cls.s1 = dict(
            bar_psnr_b=psnr(r1["mid_base"], GT1, bar1),
            bar_psnr_a=psnr(r1["mid_new"], GT1, bar1),
            bnd_psnr_b=psnr(r1["mid_base"], GT1, bnd1),
            bnd_psnr_a=psnr(r1["mid_new"], GT1, bnd1),
            id_bnd_psnr_b=psnr(r1["mid_base_id"], GT1, bnd1),
            id_bnd_psnr_a=psnr(r1["mid_new_id"], GT1, bnd1),
            epe8_b=epe_8x8(r1["F8_base"], F8_gt1, qm1),
            epe8_a=epe_8x8(r1["F8_new"], F8_gt1, qm1),
        )

        # Scene 2: Articulated character silhouette with thin extremities
        A2, GT2, B2, prevF2, ext2, bnd2, F8_gt2, qm2 = build_scene2_articulated_silhouette()
        r2 = eval_scene(A2, B2, prevF2)
        cls.s2 = dict(
            ext_psnr_b=psnr(r2["mid_base"], GT2, ext2),
            ext_psnr_a=psnr(r2["mid_new"], GT2, ext2),
            bnd_psnr_b=psnr(r2["mid_base"], GT2, bnd2),
            bnd_psnr_a=psnr(r2["mid_new"], GT2, bnd2),
            id_bnd_psnr_b=psnr(r2["mid_base_id"], GT2, bnd2),
            id_bnd_psnr_a=psnr(r2["mid_new_id"], GT2, bnd2),
            epe8_b=epe_8x8(r2["F8_base"], F8_gt2, qm2),
            epe8_a=epe_8x8(r2["F8_new"], F8_gt2, qm2),
        )

        # Scene 3: Static HUD / crosshair preservation
        A3, GT3, B3, prevF3, hud3 = build_scene3_static_hud_guard()
        r3 = eval_scene(A3, B3, prevF3)
        cls.s3 = dict(
            hud_psnr_b=psnr(r3["mid_base"], GT3, hud3),
            hud_psnr_a=psnr(r3["mid_new"], GT3, hud3),
            full_psnr_b=psnr(r3["mid_base"], GT3),
            full_psnr_a=psnr(r3["mid_new"], GT3),
        )

        print("\n" + "=" * 106)
        print(f"{'Scene':<28} | {'8x8 Contour EPE (px)':<26} | {'Boundary ROI PSNR (dB)':<30} | {'Object Core PSNR (dB)':<22}")
        print("-" * 106)
        print(f"{'1_diag_bar_warm':<28} | {cls.s1['epe8_b']:7.2f} -> {cls.s1['epe8_a']:7.2f} px     | "
              f"{cls.s1['bnd_psnr_b']:6.2f} -> {cls.s1['bnd_psnr_a']:6.2f} ({cls.s1['bnd_psnr_a']-cls.s1['bnd_psnr_b']:+5.2f} dB) | "
              f"{cls.s1['bar_psnr_b']:6.2f} -> {cls.s1['bar_psnr_a']:6.2f} dB")
        print(f"{'1_diag_bar_ideal_ft':<28} | {'-':<26} | "
              f"{cls.s1['id_bnd_psnr_b']:6.2f} -> {cls.s1['id_bnd_psnr_a']:6.2f} ({cls.s1['id_bnd_psnr_a']-cls.s1['id_bnd_psnr_b']:+5.2f} dB) | -")
        print(f"{'2_articulated_extremities':<28} | {cls.s2['epe8_b']:7.2f} -> {cls.s2['epe8_a']:7.2f} px     | "
              f"{cls.s2['bnd_psnr_b']:6.2f} -> {cls.s2['bnd_psnr_a']:6.2f} ({cls.s2['bnd_psnr_a']-cls.s2['bnd_psnr_b']:+5.2f} dB) | "
              f"{cls.s2['ext_psnr_b']:6.2f} -> {cls.s2['ext_psnr_a']:6.2f} dB")
        print(f"{'3_static_hud_guard':<28} | {'0.00 -> 0.00 px':<26} | "
              f"{cls.s3['hud_psnr_b']:6.2f} -> {cls.s3['hud_psnr_a']:6.2f} ({cls.s3['hud_psnr_a']-cls.s3['hud_psnr_b']:+5.2f} dB) | "
              f"{cls.s3['full_psnr_b']:6.2f} -> {cls.s3['full_psnr_a']:6.2f} dB")
        print("=" * 106)

    def test_1_diagonal_bar_contour_refinement(self):
        self.assertLess(self.s1["epe8_a"], self.s1["epe8_b"] * 0.5,
                        "8x8 contour EPE on thin diagonal bar must drop by >50%")
        self.assertGreaterEqual(self.s1["bnd_psnr_a"], self.s1["bnd_psnr_b"] + 1.5,
                                "Boundary ROI PSNR on thin diagonal bar must improve by >= +1.5 dB")
        self.assertGreaterEqual(self.s1["bar_psnr_a"], self.s1["bar_psnr_b"] + 3.0,
                                "Bar interior/contour PSNR must improve by >= +3.0 dB")

    def test_2_articulated_silhouette_extremities(self):
        self.assertLess(self.s2["epe8_a"], self.s2["epe8_b"] * 0.5,
                        "8x8 contour EPE on articulated extremities must drop by >50%")
        self.assertGreaterEqual(self.s2["bnd_psnr_a"], self.s2["bnd_psnr_b"] + 1.5,
                                "Boundary ROI PSNR on articulated extremities must improve by >= +1.5 dB")
        self.assertGreaterEqual(self.s2["ext_psnr_a"], self.s2["ext_psnr_b"] + 3.0,
                                "Thin extremity PSNR must improve by >= +3.0 dB")

    def test_3_static_hud_crosshair_zero_regression(self):
        self.assertGreaterEqual(self.s3["hud_psnr_a"], self.s3["hud_psnr_b"] - 0.01,
                                "Static HUD / crosshair PSNR must have 0 regression")
        self.assertGreaterEqual(self.s3["full_psnr_a"], self.s3["full_psnr_b"] - 0.05,
                                "Full-frame PSNR on HUD scene must not regress")

    def test_4_shader_ir_and_hardware_limits(self):
        mf8_ll = (ROOT / "classic" / "shaders" / "mf8.ll").read_text(encoding="utf-8")
        warp8_ll = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
        warp8g_ll = (ROOT / "classic" / "shaders" / "warp8g.ll").read_text(encoding="utf-8")
        shaders_h = (ROOT / "classic" / "source" / "shaders.h").read_text(encoding="utf-8")

        for sym in ("@qwin", "%has_split", "%swap_l", "%bxi_spl"):
            self.assertIn(sym, mf8_ll, f"Missing {sym} in classic/shaders/mf8.ll")
        for sym in ("@sub_corner", "%any_split", "%is_fg_sub"):
            self.assertIn(sym, warp8_ll, f"Missing {sym} in classic/shaders/warp8.ll")
            self.assertIn(sym, warp8g_ll, f"Missing {sym} in classic/shaders/warp8g.ll")

        for name in ("MF8", "WARP8", "WARP8G"):
            m1 = re.search(rf"#define\s+{name}_RSRC1\s+0x([0-9A-Fa-f]+)", shaders_h)
            m2 = re.search(rf"#define\s+{name}_RSRC2\s+0x([0-9A-Fa-f]+)", shaders_h)
            self.assertIsNotNone(m1, f"Missing {name}_RSRC1 in shaders.h")
            self.assertIsNotNone(m2, f"Missing {name}_RSRC2 in shaders.h")
            rsrc1 = int(m1.group(1), 16)
            rsrc2 = int(m2.group(1), 16)
            max_vgprs = ((rsrc1 & 0x3F) + 1) * 4
            scratch_en = rsrc2 & 1
            self.assertLessEqual(max_vgprs, 64, f"{name} exceeds 64 VGPR limit: {max_vgprs}")
            self.assertEqual(scratch_en, 0, f"{name} uses scratch memory: {scratch_en}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
