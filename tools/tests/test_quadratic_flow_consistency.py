"""Benchmark & verification suite for Quadratic Motion Trajectories & Temporal Flow Consistency
(vcfg-r14-quadratic-flow).

Tests vcfg_motion.py + vcfg_warp.py BEFORE (Baseline v2.31) and AFTER on 1920x1080 sequences:
  1. Accelerating multi-block foreground motion (16 -> 32 -> 48 px/frame in +X, -X, +Y, -Y):
     self-advected & 2-block temporal predictors plus constant-acceleration extrapolation in me3/me2/me1/mf8
     (>15% EPE reduction and >3 dB ROI PSNR gain).
  2. Orbital camera rotation + angular acceleration + accelerating pan:
     second-order quadratic midpoint trajectory in warp8 (at t=0.5 and t=0.25) and end-to-end pipeline PSNR.
  3. Temporal flow consistency confidence in warp8: damping false static fallback (cw) when motion trajectories
     are temporally consistent across frames.
  4. Hit-stop (abrupt stop from 32 px/frame to 0 px/frame) & constant-velocity non-regression guards:
     verifies zero overshoot (dq == 0) and zero regression on sudden stops and linear pans.
  5. Hardware & compiler limits: vgprs <= 64, scratch == 0, and shader region <= 0x1C000 in classic/source/shaders.h.
"""
import pathlib
import re
import sys
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
BENCH = ROOT / "tools" / "bench"
TESTS = ROOT / "tools" / "tests"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(TESTS))

import test_optical_flow_pyramid as tofp
import vcfg_motion as vm
import vcfg_ref as vref
import vcfg_warp as vw

H, W = 1080, 1920
NBY, NBX = 68, 120
BIG = np.iinfo(np.int64).max


def psnr(x, y, mask=None):
    d = (x - y) ** 2
    if mask is not None:
        if not mask.any():
            return float("nan")
        d = d[mask]
    mse = float(d.mean())
    return 99.0 if mse <= 1e-12 else 10.0 * np.log10(1.0 / mse)


def epe(F_est, F_gt, mask=None):
    err = np.sqrt((F_est[..., 0] - F_gt[..., 0]) ** 2 + (F_est[..., 1] - F_gt[..., 1]) ** 2)
    if mask is not None:
        err = err[mask]
    return float(err.mean())


# ---------------------------------------------------------------------------------------------------------------
# Baseline v2.31 Reference (Linear / Static-Index Temporal Predictor, No Quadratic Midpoint Offset)
# ---------------------------------------------------------------------------------------------------------------
def baseline_v231_me3(L3p, L3c, prevF=None, dc_comp=True):
    h, w = L3c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = vm._grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    tdx = np.zeros(N, np.int64)
    tdy = np.zeros(N, np.int64)
    if prevF is not None:
        tx, ty = np.minimum(bx * 4 + 2, 119), np.minimum(by * 4 + 2, 67)
        tdx = vm.rnd(prevF[ty, tx, 0] * np.float32(0.125))
        tdy = vm.rnd(prevF[ty, tx, 1] * np.float32(0.125))
    zero = np.zeros(N, np.int64)
    s0 = vm.sad_cur_grid(L3p, L3c, X, Y, zero, zero, dc_comp=dc_comp)
    s1 = vm.sad_cur_grid(L3p, L3c, X, Y, tdx, tdy, dc_comp=dc_comp)
    usetemp = (s1 + 256 - 4) < (s0 + 256)
    cx, cy = np.where(usetemp, tdx, 0), np.where(usetemp, tdy, 0)
    best = np.full(N, BIG, np.int64)
    bk = np.zeros(N, np.int64)
    for k in range(435):
        ox, oy = k % 29 - 14, k // 29 - 7
        s = vm.sad_cur_grid(L3p, L3c, X, Y, cx + ox, cy + oy, dc_comp=dc_comp)
        key = ((s + 256 + 4 * (abs(ox) + abs(oy))) << 9) | k
        better = key < best
        best = np.where(better, key, best)
        bk = np.where(better, k, bk)
    vx, vy = cx + (bk % 29 - 14), cy + (bk // 29 - 7)
    r = np.arange(8)[None, :]
    lumasum = int(vm._blocks(L3c, np.clip(X, 0, w - 8), np.clip(Y[:, None] + r, 0, h - 1)).sum())
    return np.stack([vx, vy], axis=-1).reshape(nby, nbx, 2), lumasum


def baseline_v231_me2(L2p, L2c, V3, prevF=None):
    h, w = L2c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = vm._grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    n3y, n3x = V3.shape[:2]
    ix0 = np.clip((bx * 4 - 2) >> 3, 0, max(n3x - 2, 0))
    ix1 = np.minimum(ix0 + 1, n3x - 1)
    iy0 = np.clip((by * 4 - 2) >> 3, 0, max(n3y - 2, 0))
    iy1 = np.minimum(iy0 + 1, n3y - 1)
    nb = [V3[iy0, ix0], V3[iy0, ix1], V3[iy1, ix0], V3[iy1, ix1]]
    medx = vm._med4(*[v[:, 0] for v in nb]) * 2
    medy = vm._med4(*[v[:, 1] for v in nb]) * 2
    zero = np.zeros(N, np.int64)
    tx0, ty0 = np.minimum(bx * 2 + 1, 119), np.minimum(by * 2 + 1, 67)

    def tdv2(ox, oy):
        if prevF is None:
            return zero, zero
        tx = np.clip(tx0 + ox, 0, 119)
        ty = np.clip(ty0 + oy, 0, 67)
        return vm.rnd(prevF[ty, tx, 0] * np.float32(0.25)), vm.rnd(prevF[ty, tx, 1] * np.float32(0.25))

    ixm1 = np.maximum(ix0 - 1, 0)
    ixp1 = np.minimum(ix1 + 1, n3x - 1)
    iym1 = np.maximum(iy0 - 1, 0)
    iyp1 = np.minimum(iy1 + 1, n3y - 1)
    outer = [V3[iy0, ixm1], V3[iy0, ixp1], V3[iym1, ix0], V3[iyp1, ix0]]
    cands = (
        [(v[:, 0] * 2, v[:, 1] * 2) for v in nb] +
        [tdv2(0, 0), (zero, zero)] +
        [tdv2(2, 0), tdv2(-2, 0), tdv2(0, 2), tdv2(0, -2)] +
        [(v[:, 0] * 2, v[:, 1] * 2) for v in outer]
    )
    best = np.full(N, BIG, np.int64)
    cdx_b, cdy_b, zcost = zero.copy(), zero.copy(), None
    for sub, (cdx, cdy) in enumerate(cands):
        s = vm.sad_cur_grid(L2p, L2c, X, Y, cdx, cdy, dc_comp=True)
        oob_m = (X + cdx < 0) | (X + cdx > w - 8) | (Y + cdy < 0) | (Y + cdy > h - 8)
        msh = np.where(oob_m, 3, 2)
        ist = (sub == 4) or (6 <= sub <= 9)
        isout = 10 <= sub <= 13
        cost = s + 256 + ((np.abs(cdx - medx) + np.abs(cdy - medy)) << msh) + (8 if isout else 0) - (8 if ist else 0)
        if sub == 5:
            zcost = cost
        key = (cost << 8) | sub
        better = key < best
        best = np.where(better, key, best)
        cdx_b, cdy_b = np.where(better, cdx, cdx_b), np.where(better, cdy, cdy_b)
    zsad = zcost - 256 - 4 * (np.abs(medx) + np.abs(medy))
    best2 = np.full(N, BIG, np.int64)
    bk = np.zeros(N, np.int64)
    for k in range(25):
        ox, oy = k % 5 - 2, k // 5 - 2
        s = vm.sad_cur_grid(L2p, L2c, X, Y, cdx_b + ox, cdy_b + oy, dc_comp=True)
        key = ((s + 256 + 4 * (abs(ox) + abs(oy))) << 8) | k
        better = key < best2
        best2 = np.where(better, key, best2)
        bk = np.where(better, k, bk)
    vx, vy = cdx_b + bk % 5 - 2, cdy_b + bk // 5 - 2
    bsad = (best2 >> 8) - 256 - 4 * (np.abs(bk % 5 - 2) + np.abs(bk // 5 - 2))
    return (np.stack([vx, vy], -1).reshape(nby, nbx, 2), np.stack([bsad, zsad], -1).reshape(nby, nbx, 2))


def baseline_v231_me1(L1p, L1c, V2, GK, gm, lumasum_cur, lumasum_prev, prevF=None):
    h, w = L1c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = vm._grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    n2y, n2x = V2.shape[:2]
    gmx, gmy = gm
    gmx2, gmy2 = gmx * 2, gmy * 2
    still = np.bool_((gmx | gmy) == 0)
    ix0 = np.clip((bx * 4 - 2) >> 3, 0, max(n2x - 2, 0))
    ix1 = np.minimum(ix0 + 1, n2x - 1)
    iy0 = np.clip((by * 4 - 2) >> 3, 0, max(n2y - 2, 0))
    iy1 = np.minimum(iy0 + 1, n2y - 1)
    nbi = [(iy0, ix0), (iy0, ix1), (iy1, ix0), (iy1, ix1)]
    nbv = [V2[a, b].astype(np.int64) * 2 for a, b in nbi]
    ixm1 = np.maximum(ix0 - 1, 0)
    ixp1 = np.minimum(ix1 + 1, n2x - 1)
    iym1 = np.maximum(iy0 - 1, 0)
    iyp1 = np.minimum(iy1 + 1, n2y - 1)
    out_nbi = [(iy0, ixm1), (iy0, ixp1), (iym1, ix0), (iyp1, ix0)]
    out_nbv = [V2[a, b].astype(np.int64) * 2 for a, b in out_nbi]
    zero = np.zeros(N, np.int64)

    def tdv(ex, ey):
        if prevF is None:
            return zero, zero
        tx, ty = np.clip(bx + ex, 0, 119), np.clip(by + ey, 0, 67)
        return (vm.rnd(prevF[ty, tx, 0] * np.float32(0.5)), vm.rnd(prevF[ty, tx, 1] * np.float32(0.5)))

    gk = GK[by, bx]
    hasfg = gk != 0
    isfgk = gk >= (1 << 20)
    isanc = hasfg & ~isfgk
    gv = V2.reshape(-1, 2)[gk & 4095].astype(np.int64) * 2
    gdx, gdy = gv[:, 0], gv[:, 1]
    cand = [(nbv[k][:, 0], nbv[k][:, 1]) for k in range(4)]
    cand += [tdv(0, 0), tdv(1, 0), tdv(0, 1)]
    cand += [(zero, zero), (gdx, gdy), (np.full(N, gmx2), np.full(N, gmy2))]
    cand += [tdv(-1, 0), tdv(0, -1)]
    cand += [(out_nbv[k][:, 0], out_nbv[k][:, 1]) for k in range(4)]

    def nbdist(a, b, cdx, cdy):
        v = V2[a, b].astype(np.int64) * 2
        return np.abs(cdx - v[:, 0]) + np.abs(cdy - v[:, 1])

    zs, zsum = vm.sad_bilateral(L1p, L1c, X, Y, zero, zero)
    best = np.full(N, BIG, np.int64)
    cx1, cy1 = zero.copy(), zero.copy()
    for sub, (cdx, cdy) in enumerate(cand):
        sad1, sum1 = vm.sad_bilateral(L1p, L1c, X, Y, cdx, cdy)
        ist = (4 <= sub <= 6) or (10 <= sub <= 11)
        isout = 12 <= sub <= 15
        bias = np.zeros(N, np.int64)
        if ist:
            bias += 16
        if sub == 7:
            bias = np.where(still, 48, 0).astype(np.int64)
        if sub == 9:
            bias += 32
        if sub == 8:
            bias = np.where(isfgk, 96, 64).astype(np.int64)
        if isout:
            bias -= 8
        aad = np.abs(cdx - gdx) + np.abs(cdy - gdy)
        sc = np.abs(cdx - gmx2) + np.abs(cdy - gmy2)
        afar = (aad > 3) & ((aad > 16) | ((~still) & (sc * 2 <= aad)))
        ap1 = isanc & afar & (sub != 8)
        apen = np.where(ap1, 512, 0)
        p1f = (np.abs(cdx) + np.abs(cdy)) > 8
        p1gate = (~isfgk) & (ap1 | ((~still) & (sc <= 6)))
        p1p = np.where(p1f & p1gate, np.maximum((np.abs(sum1 - zsum) >> 1) - zs, 0), 0)
        sm03 = np.minimum.reduce([nbdist(a, b, cdx, cdy) for a, b in nbi])
        sm03c = np.minimum(sm03, sc)
        xc1_0 = X - (cdx >> 1)
        xp1_0 = xc1_0 + cdx
        yc1_0 = Y - (cdy >> 1)
        yp1_0 = yc1_0 + cdy
        oob1 = ((xc1_0 < 0) | (xc1_0 > w - 8) | (xp1_0 < 0) | (xp1_0 > w - 8) |
                (yc1_0 < 0) | (yc1_0 > h - 8) | (yp1_0 < 0) | (yp1_0 > h - 8))
        oob_p = np.where(oob1 & (sm03c > 3) & ~isfgk, 128, 0)
        cost = sad1 + 256 + apen + p1p - bias + oob_p
        if ist:
            sz = np.abs(cdx) + np.abs(cdy)
            smin = np.minimum.reduce([sm03c, sz, np.where(hasfg, aad, sz)])
            valid = (smin <= 3) | (sz <= 64) | isfgk
        elif sub == 8:
            valid = hasfg
        else:
            valid = np.ones(N, bool)
        key = np.where(valid, (cost << 8) | sub, BIG)
        better = key < best
        best = np.where(better, key, best)
        cx1, cy1 = np.where(better, cdx, cx1), np.where(better, cdy, cy1)
    best2 = np.full(N, BIG, np.int64)
    bk = np.zeros(N, np.int64)
    for k in range(9):
        ox, oy = k % 3 - 1, k // 3 - 1
        d2x, d2y = cx1 + ox, cy1 + oy
        sad2, sum2 = vm.sad_bilateral(L1p, L1c, X, Y, d2x, d2y)
        p2f = (np.abs(d2x) + np.abs(d2y)) > 8
        aad2 = np.abs(d2x - gdx) + np.abs(d2y - gdy)
        sc2 = np.abs(d2x - gmx2) + np.abs(d2y - gmy2)
        ap2 = isanc & (aad2 > 3) & ((aad2 > 16) | ((~still) & (sc2 * 2 <= aad2)))
        p2gate = (~isfgk) & (ap2 | ((~still) & (sc2 <= 6)))
        p2p = np.where(p2f & p2gate, np.maximum((np.abs(sum2 - zsum) >> 1) - zs, 0), 0)
        key = ((sad2 + 256 + p2p + 4 * (abs(ox) + abs(oy))) << 8) | k
        better = key < best2
        best2 = np.where(better, key, best2)
        bk = np.where(better, k, bk)
    bvx, bvy = cx1 + bk % 3 - 1, cy1 + bk // 3 - 1
    r0, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx, bvy)
    r1, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx - 1, bvy)
    r2, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx + 1, bvy)
    r3, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx, bvy - 1)
    r4, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx, bvy + 1)
    r5, _ = vm.sad_bilateral(L1p, L1c, X, Y, zero, zero)
    spx = vm._equi(r1, r0, r2)
    spy = vm._equi(r3, r0, r4)
    vx = (bvx.astype(np.float32) + spx) * np.float32(2.0)
    vy = (bvy.astype(np.float32) + spy) * np.float32(2.0)
    zero0 = (r5 <= r0 + 48) & ~isfgk & still
    fsc, fsp = np.float32(lumasum_cur), np.float32(lumasum_prev)
    fad = np.abs(fsc - fsp)
    fade = bool((fad > fsp * np.float32(0.02)) and max(abs(gmx), abs(gmy)) <= 1)
    zero_all = zero0 | fade
    FT = np.stack([np.where(zero_all, 0, vx), np.where(zero_all, 0, vy)], -1).astype(np.float32).reshape(nby, nbx, 2)
    cthr = fad * np.float32(0.0019753087) + np.float32(256.0)
    cutn = int((np.minimum(r0, r5).astype(np.float32) > cthr).sum())
    return FT, cutn, fade


def run_baseline_v231_pipeline(A, B, prevF=None):
    L1a, L2a, L3a = vref.pyr8(A)
    L1b, L2b, L3b = vref.pyr8(B)
    V3, sum_b = baseline_v231_me3(L3a, L3b, prevF)
    V2, S2 = baseline_v231_me2(L2a, L2b, V3, prevF)
    gm = vm.gm8(V2, S2)
    GK, _ = vm.fgs8(L2a, L2b, V2, S2, gm)
    sum_a = int(L3a.sum())
    FT, cutn, fade = baseline_v231_me1(L1a, L1b, V2, GK, gm, sum_b, sum_a, prevF)
    F = vm.mf8(L1a, L1b, FT, GK, V2, gm, fade, prevF=None)
    mid = vw.warp8(A, B, F, GK, cutn, t=0.5, prevF=None)
    return dict(F=F, GK=GK, cutn=cutn, mid=mid)


def run_new_r14_pipeline(A, B, prevF=None):
    L1a, L2a, L3a = vref.pyr8(A)
    L1b, L2b, L3b = vref.pyr8(B)
    V3, sum_b = vm.me3(L3a, L3b, prevF)
    V2, S2 = vm.me2(L2a, L2b, V3, prevF)
    gm = vm.gm8(V2, S2)
    GK, _ = vm.fgs8(L2a, L2b, V2, S2, gm)
    sum_a = int(L3a.sum())
    FT, cutn, fade = vm.me1(L1a, L1b, V2, GK, gm, sum_b, sum_a, prevF)
    F = vm.mf8(L1a, L1b, FT, GK, V2, gm, fade, prevF=prevF)
    mid = vw.warp8(A, B, F, GK, cutn, t=0.5, prevF=prevF)
    return dict(F=F, GK=GK, cutn=cutn, mid=mid)


# ---------------------------------------------------------------------------------------------------------------
# Synthetic Test Scene Generators
# ---------------------------------------------------------------------------------------------------------------
def _bg_field(X, Y):
    """Rich multi-frequency procedural background on 1920x1080."""
    r = (0.45
         + 0.22 * np.sin(X * 0.031 + Y * 0.017)
         + 0.15 * np.cos(X * 0.073 - Y * 0.041)
         + 0.08 * np.sin(X * 0.13 + Y * 0.11))
    g = (0.44
         + 0.21 * np.cos(X * 0.027 - Y * 0.023)
         + 0.14 * np.sin(X * 0.061 + Y * 0.053)
         + 0.08 * np.cos(X * 0.11 - Y * 0.14))
    b = (0.42
         + 0.20 * np.sin(X * 0.019 - Y * 0.029)
         + 0.15 * np.cos(X * 0.047 + Y * 0.067)
         + 0.08 * np.sin(X * 0.15 + Y * 0.09))
    return np.clip(np.stack([r, g, b], axis=-1), 0.02, 0.98).astype(np.float32)


def make_case1_accelerating_fast_4dir():
    """4 fast thin objects (+X, -X, +Y, -Y) accelerating from 16 -> 32 -> 48 px/frame over a textured background."""
    bg_canvas = tofp._make_textured_canvas(H + 256, W + 256, seed=701, base_luma=0.18, contrast=0.12, tint=(0.9, 0.9, 1.0))
    A = bg_canvas[:H, :W].copy()
    GT = bg_canvas[:H, :W].copy()
    B = bg_canvas[:H, :W].copy()

    flow_gt = np.zeros((NBY, NBX, 2), np.float32)
    roi = np.zeros((H, W), bool)
    prevF = np.zeros((NBY, NBX, 2), np.float32)

    objs = [
        (711, 96, 16, 320, 488, 48, 0, 16, 0, 32, 0),
        (712, 96, 16, 320, 1432, -48, 0, -16, 0, -32, 0),
        (713, 16, 96, 776, 480, 0, 48, 0, 16, 0, 32),
        (714, 16, 96, 760, 1440, 0, -48, 0, -16, 0, -32),
    ]

    for seed, ph, pw, cy, cx, vx, vy, v0x, v0y, vadx, vady in objs:
        patch = tofp._make_textured_canvas(ph, pw, seed=seed, base_luma=0.82, contrast=0.18, tint=(1.0, 0.9, 0.75))
        tofp._place_rect(A, None, None, patch, cy + vy * 0.5, cx + vx * 0.5, vx, vy)
        tofp._place_rect(GT, flow_gt, roi, patch, cy, cx, vx, vy)
        tofp._place_rect(B, None, None, patch, cy - vy * 0.5, cx - vx * 0.5, vx, vy)

        upx = 1 if vx > 0 else (-1 if vx < 0 else 0)
        upy = 1 if vy > 0 else (-1 if vy < 0 else 0)
        by0, by1 = (cy - ph // 2) // 16, (cy + ph // 2) // 16
        bx0, bx1 = (cx - pw // 2) // 16, (cx + pw // 2) // 16
        for b_y in range(max(0, by0 - 2), min(NBY, by1 + 2)):
            for b_x in range(max(0, bx0 - 2), min(NBX, bx1 + 2)):
                prevF[b_y, b_x] = [v0x, v0y]
        if upx != 0:
            tx_cur = ((int(cx - vx * 0.5) // 32) * 2) + 1
            for b_y in range(max(0, by0 - 1), min(NBY, by1 + 1)):
                prevF[b_y, tx_cur + upx] = [vadx, vady]
                for b_x in range(bx0, bx1):
                    if b_x + upx != tx_cur:
                        prevF[b_y, b_x + upx] = [vadx, vady]
        if upy != 0:
            ty_cur = ((int(cy - vy * 0.5) // 32) * 2) + 1
            for b_x in range(max(0, bx0 - 1), min(NBX, bx1 + 1)):
                prevF[ty_cur + upy, b_x] = [vadx, vady]
                for b_y in range(by0, by1):
                    if b_y + upy != ty_cur:
                        prevF[b_y + upy, b_x] = [vadx, vady]

    return A, GT, B, flow_gt, roi, prevF


def make_case2_orbital_accelerating_camera():
    """Orbital camera rotation + angular acceleration + radial zoom on 1920x1080."""
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")
    cx, cy = W * 0.5, H * 0.5
    dx0, dy0 = xx - cx, yy - cy

    def pose(tau):
        theta = 0.014 * tau + 0.008 * (tau ** 2)
        tx = 12.0 * tau + 6.0 * (tau ** 2)
        ty = -6.0 * tau + 4.0 * (tau ** 2)
        cs, sn = np.cos(theta), np.sin(theta)
        wx = cx + cs * dx0 - sn * dy0 - tx
        wy = cy + sn * dx0 + cs * dy0 - ty
        return wx.astype(np.float32), wy.astype(np.float32)

    wx0, wy0 = pose(0.0)
    wx1, wy1 = pose(1.0)
    wx2, wy2 = pose(2.0)
    wxm, wym = pose(1.5)
    wx25, wy25 = pose(1.25)

    F0 = _bg_field(wx0, wy0)
    F1 = _bg_field(wx1, wy1)
    F2 = _bg_field(wx2, wy2)
    GT_50 = _bg_field(wxm, wym)
    GT_25 = _bg_field(wx25, wy25)

    by, bx = np.meshgrid(np.arange(NBY, dtype=np.float32), np.arange(NBX, dtype=np.float32), indexing="ij")
    bcx = bx * 16.0 + 8.0
    bcy = by * 16.0 + 8.0
    bdx, bdy = bcx - cx, bcy - cy

    def gt_flow(tau_p, tau_c):
        th_p = 0.014 * tau_p + 0.008 * (tau_p ** 2)
        tx_p = 12.0 * tau_p + 6.0 * (tau_p ** 2)
        ty_p = -6.0 * tau_p + 4.0 * (tau_p ** 2)
        th_c = 0.014 * tau_c + 0.008 * (tau_c ** 2)
        tx_c = 12.0 * tau_c + 6.0 * (tau_c ** 2)
        ty_c = -6.0 * tau_c + 4.0 * (tau_c ** 2)
        dth = th_c - th_p
        cs, sn = np.cos(dth), np.sin(dth)
        xp = cx + cs * bdx - sn * bdy - (tx_c - tx_p)
        yp = cy + sn * bdx + cs * dy0[:NBY, :NBX] * 0.0 + cs * bdy - (ty_c - ty_p)
        return np.stack([xp - bcx, yp - bcy], axis=-1).astype(np.float32)

    prevF_gt = gt_flow(0.0, 1.0)
    F_gt = gt_flow(1.0, 2.0)
    interior = np.zeros((H, W), dtype=bool)
    interior[80:-80, 120:-120] = True
    return F0, F1, F2, GT_50, GT_25, prevF_gt, F_gt, interior


def make_case3_hit_stop_and_linear():
    """Hit-stop (32 px/frame -> 0 px/frame abrupt stop) and constant-velocity pan (32 px/frame -> 32 px/frame)."""
    yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")
    F0 = _bg_field(xx + 32.0, yy)
    F1 = _bg_field(xx, yy)
    F2_stop = F1.copy()
    F2_lin = _bg_field(xx - 32.0, yy)
    GT_lin = _bg_field(xx - 16.0, yy)
    return F0, F1, F2_stop, F2_lin, GT_lin


# ---------------------------------------------------------------------------------------------------------------
# Unit Test Suite
# ---------------------------------------------------------------------------------------------------------------
class TestQuadraticFlowConsistency(unittest.TestCase):

    def test_1_accelerating_fast_object_flow_and_warp(self):
        A, GT, B, flow_gt, roi, prevF = make_case1_accelerating_fast_4dir()
        st_base = run_baseline_v231_pipeline(A, B, prevF=prevF)
        st_new = run_new_r14_pipeline(A, B, prevF=prevF)

        _, epe_base = tofp.epe_metrics(st_base["F"], flow_gt, roi)
        _, epe_new = tofp.epe_metrics(st_new["F"], flow_gt, roi)
        psnr_base = psnr(st_base["mid"], GT, roi)
        psnr_new = psnr(st_new["mid"], GT, roi)

        epe_red_pct = 100.0 * (epe_base - epe_new) / max(epe_base, 1e-6)
        print(f"\n[Case 1: Accelerating Fast 4-Dir Objects 16->32->48 px/frame] "
              f"ROI EPE: {epe_base:.2f} -> {epe_new:.2f} px (-{epe_red_pct:.1f}%), "
              f"ROI PSNR: {psnr_base:.2f} -> {psnr_new:.2f} dB (+{psnr_new - psnr_base:.2f} dB)")
        self.assertGreater(epe_red_pct, 15.0)
        self.assertGreater(psnr_new, psnr_base + 3.0)

    def test_2_orbital_accelerating_quadratic_warp_and_pyramid(self):
        F0, F1, F2, GT_50, GT_25, prevF_gt, F_gt, interior = make_case2_orbital_accelerating_camera()
        GK = np.zeros((NBY, NBX), dtype=np.int64)

        # Clear low 4 mantissa bits of synthetic ground-truth F_gt[..., 0] to match mf8.ll homogeneous block encoding (m = 0)
        F_gt = F_gt.copy()
        F_gt[..., 0] = (F_gt[..., 0].view(np.uint32) & np.uint32(0xFFFFFFF0)).view(np.float32)
        # Pure warp8 trajectory test (t=0.5 and t=0.25)
        mid50_base = vw.warp8(F1, F2, F_gt, GK, 0, t=0.5, prevF=None)
        mid50_new = vw.warp8(F1, F2, F_gt, GK, 0, t=0.5, prevF=prevF_gt)
        psnr50_base = psnr(mid50_base, GT_50, interior)
        psnr50_new = psnr(mid50_new, GT_50, interior)

        mid25_base = vw.warp8(F1, F2, F_gt, GK, 0, t=0.25, prevF=None)
        mid25_new = vw.warp8(F1, F2, F_gt, GK, 0, t=0.25, prevF=prevF_gt)
        psnr25_base = psnr(mid25_base, GT_25, interior)
        psnr25_new = psnr(mid25_new, GT_25, interior)

        # Full end-to-end pyramid + warp8 test on orbital accelerating camera
        e2e_base = run_baseline_v231_pipeline(F1, F2, prevF=prevF_gt)
        e2e_new = run_new_r14_pipeline(F1, F2, prevF=prevF_gt)
        psnr_e2e_base = psnr(e2e_base["mid"], GT_50, interior)
        psnr_e2e_new = psnr(e2e_new["mid"], GT_50, interior)

        print(f"[Case 2: Orbital Accelerating Camera] "
              f"Warp PSNR (t=0.5): {psnr50_base:.2f} -> {psnr50_new:.2f} dB (+{psnr50_new - psnr50_base:.2f} dB), "
              f"Warp PSNR (t=0.25): {psnr25_base:.2f} -> {psnr25_new:.2f} dB (+{psnr25_new - psnr25_base:.2f} dB), "
              f"E2E PSNR: {psnr_e2e_base:.2f} -> {psnr_e2e_new:.2f} dB (+{psnr_e2e_new - psnr_e2e_base:.2f} dB)")
        self.assertGreater(psnr50_new, psnr50_base + 6.0)
        self.assertGreater(psnr25_new, psnr25_base + 4.0)
        self.assertGreater(psnr_e2e_new, psnr_e2e_base + 5.0)

    def test_3_temporal_consistency_confidence_cw_damping(self):
        """Verify that consistent temporal trajectories damp false static fallback cw under moderate luma shift."""
        yy, xx = np.meshgrid(np.arange(H, dtype=np.float32), np.arange(W, dtype=np.float32), indexing="ij")
        F1 = _bg_field(xx, yy)
        F2 = np.clip(_bg_field(xx - 16.0, yy) + 0.10, 0.0, 1.0).astype(np.float32)
        GT = np.clip(_bg_field(xx - 8.0, yy) + 0.05, 0.0, 1.0).astype(np.float32)
        F_flow = np.zeros((NBY, NBX, 2), dtype=np.float32)
        F_flow[..., 0] = -16.0
        GK = np.zeros((NBY, NBX), dtype=np.int64)

        dbg_base, dbg_new = {}, {}
        mid_base = vw.warp8(F1, F2, F_flow, GK, 0, t=0.5, dbg=dbg_base, prevF=None)
        mid_new = vw.warp8(F1, F2, F_flow, GK, 0, t=0.5, dbg=dbg_new, prevF=F_flow)
        cw_base = float(np.concatenate(dbg_base["cw"], axis=0).mean())
        cw_new = float(np.concatenate(dbg_new["cw"], axis=0).mean())
        p_base = psnr(mid_base, GT)
        p_new = psnr(mid_new, GT)
        print(f"[Case 3: Temporal Consistency Confidence] "
              f"mean(cw): {cw_base:.4f} -> {cw_new:.4f}, PSNR: {p_base:.2f} -> {p_new:.2f} dB")
        self.assertLess(cw_new, cw_base)
        self.assertGreaterEqual(p_new, p_base)

    def test_4_hit_stop_and_constant_velocity_non_regression(self):
        """Verify zero overshoot on abrupt hit-stops (32 -> 0 px/frame) and zero regression on constant velocity."""
        F0, F1, F2_stop, F2_lin, GT_lin = make_case3_hit_stop_and_linear()
        prevF_pan = np.zeros((NBY, NBX, 2), dtype=np.float32)
        prevF_pan[..., 0] = -32.0

        st_stop_base = run_baseline_v231_pipeline(F1, F2_stop, prevF=prevF_pan)
        st_stop_new = run_new_r14_pipeline(F1, F2_stop, prevF=prevF_pan)
        psnr_stop_base = psnr(st_stop_base["mid"], F1)
        psnr_stop_new = psnr(st_stop_new["mid"], F1)
        max_stop_flow = float(np.abs(st_stop_new["F"]).max())

        interior = np.zeros((H, W), dtype=bool)
        interior[:, 64:-64] = True
        st_lin_base = run_baseline_v231_pipeline(F1, F2_lin, prevF=prevF_pan)
        st_lin_new = run_new_r14_pipeline(F1, F2_lin, prevF=prevF_pan)
        psnr_lin_base = psnr(st_lin_base["mid"], GT_lin, interior)
        psnr_lin_new = psnr(st_lin_new["mid"], GT_lin, interior)

        print(f"[Case 4: Hit-Stop & Linear Non-Regression] "
              f"Hit-Stop PSNR: {psnr_stop_base:.2f} -> {psnr_stop_new:.2f} dB (max |F|={max_stop_flow:.2f}), "
              f"Linear Pan PSNR: {psnr_lin_base:.2f} -> {psnr_lin_new:.2f} dB")
        self.assertEqual(max_stop_flow, 0.0)
        self.assertGreaterEqual(psnr_stop_new, 90.0)
        self.assertGreaterEqual(psnr_lin_new, psnr_lin_base - 0.01)

    def test_5_hardware_shader_constraints(self):
        """Verify classic/source/shaders.h meets vgprs <= 64, scratch == 0, and shader region <= 0x1C000."""
        sh_path = ROOT / "classic" / "source" / "shaders.h"
        txt = sh_path.read_text(encoding="utf-8")
        rsrc1_list = re.findall(r"#define\s+(\w+)_RSRC1\s+0x([0-9a-fA-F]+)", txt)
        rsrc2_map = dict(re.findall(r"#define\s+(\w+)_RSRC2\s+0x([0-9a-fA-F]+)", txt))
        nwords_map = dict(re.findall(r"#define\s+(\w+)_NWORDS\s+(\d+)", txt))
        self.assertGreaterEqual(len(rsrc1_list), 20)
        off = 0x8000
        max_vgprs_all = 0
        for name, r1_hex in rsrc1_list:
            r1 = int(r1_hex, 16)
            r2 = int(rsrc2_map[name], 16)
            nwords = int(nwords_map[name])
            vgpr_gran = r1 & 0x3F
            max_vgprs = (vgpr_gran + 1) * 4
            max_vgprs_all = max(max_vgprs_all, max_vgprs)
            scratch_en = r2 & 1
            self.assertLessEqual(max_vgprs, 64, f"{name} exceeded 64 VGPRs: {max_vgprs}")
            self.assertEqual(scratch_en, 0, f"{name} has scratch enabled: rsrc2=0x{r2:08X}")
            if name in {"PYR8","ME3","ME2","ME1","PHASE8","WARP8","DUMP8","GM8","FGS8","MF8","GV8","GVC8","WARP8G","GVZ8","RCAS8","OSD8","PCOPY8"}:
                off = ((off + nwords * 4 + 255) & ~255)
        print(f"[Case 5: Hardware Constraints] shaders={len(rsrc1_list)}, "
              f"max_vgprs={max_vgprs_all}, region_end=0x{off:X} (limit 0x1C000)")
        self.assertLessEqual(off, 0x1C000)


if __name__ == "__main__":
    unittest.main()
