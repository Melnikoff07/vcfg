"""Benchmark & verification suite for Gradient/Structure-Robust Optical Flow Pyramid
and Multi-Layer Object Segmentation (vcfg-r7-flow-pyr).

Tests vcfg_motion.py + vcfg_warp.py BEFORE (Baseline Mode 8.7) and AFTER on 1920x1080 frames:
  1. Articulated / mid-speed moving objects with camera pan (0.5 * |gm| <= |V_obj| <= |gm|).
  2. Swinging articulated limbs on an anchor body (|V_torso| <= 0.5 * |gm|, |V_limb - V_torso|_1 > 3 L1 px).
  3. Fast motion in all 4 directions (+X, -X, +Y, -Y) verifying symmetric temporal prediction (lanes 10..15).
  4. Illumination / specular variation and motion blur on moving textures.
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


def psnr(x, y, mask=None):
    d = (x - y) ** 2
    if mask is not None:
        if not mask.any():
            return float("nan")
        d = d[mask]
    mse = float(d.mean())
    return 99.0 if mse <= 1e-12 else 10.0 * np.log10(1.0 / mse)


# ---------------------------------------------------------------------------------------------------------------
# Exact Baseline Mode 8.7 Reference (Before Changes) for Head-to-Head Comparison
# ---------------------------------------------------------------------------------------------------------------
def baseline_me2(L2p, L2c, V3, prevF=None):
    h, w = L2c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = vm._grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    n3y, n3x = V3.shape[:2]
    ix0 = np.clip((bx * 4 - 2) >> 3, 0, n3x - 1)
    ix1 = np.minimum(ix0 + 1, n3x - 1)
    iy0 = np.clip((by * 4 - 2) >> 3, 0, n3y - 1)
    iy1 = np.minimum(iy0 + 1, n3y - 1)
    nb = [V3[iy0, ix0], V3[iy0, ix1], V3[iy1, ix0], V3[iy1, ix1]]
    medx = vm._med4(*[v[:, 0] for v in nb]) * 2
    medy = vm._med4(*[v[:, 1] for v in nb]) * 2
    tdx = np.zeros(N, np.int64)
    tdy = np.zeros(N, np.int64)
    if prevF is not None:
        tx, ty = np.minimum(bx * 2 + 1, 119), np.minimum(by * 2 + 1, 67)
        tdx = vm.rnd(prevF[ty, tx, 0] * np.float32(0.25))
        tdy = vm.rnd(prevF[ty, tx, 1] * np.float32(0.25))
    zero = np.zeros(N, np.int64)
    cands = [(v[:, 0] * 2, v[:, 1] * 2) for v in nb] + [(tdx, tdy), (zero, zero)]
    best = np.full(N, BIG, np.int64)
    cdx_b, cdy_b, zcost = zero.copy(), zero.copy(), None
    for sub, (cdx, cdy) in enumerate(cands):
        s = vm.sad_cur_grid(L2p, L2c, X, Y, cdx, cdy, dc_comp=False)
        cost = s + 256 + 4 * (np.abs(cdx - medx) + np.abs(cdy - medy)) - (8 if sub == 4 else 0)
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
        s = vm.sad_cur_grid(L2p, L2c, X, Y, cdx_b + ox, cdy_b + oy, dc_comp=False)
        key = ((s + 256 + 4 * (abs(ox) + abs(oy))) << 8) | k
        better = key < best2
        best2 = np.where(better, key, best2)
        bk = np.where(better, k, bk)
    vx, vy = cdx_b + bk % 5 - 2, cdy_b + bk // 5 - 2
    bsad = (best2 >> 8) - 256 - 4 * (np.abs(bk % 5 - 2) + np.abs(bk // 5 - 2))
    return (np.stack([vx, vy], -1).reshape(nby, nbx, 2), np.stack([bsad, zsad], -1).reshape(nby, nbx, 2))


def baseline_fgs8(L2p, L2c, V2, S2, gm, anchor_hist=None):
    nby2, nbx2 = V2.shape[:2]
    N = nbx2 * nby2
    i = np.arange(N)
    bxq, byq = i % nbx2, i // nbx2
    BX, BY = bxq * 8, byq * 8
    vx, vy = V2[..., 0].ravel(), V2[..., 1].ravel()
    sb = S2[..., 0].ravel()
    gmx, gmy = gm
    gs = [vm.sad_cur_grid(L2p, L2c, BX, BY, np.full(N, gmx + ox), np.full(N, gmy + oy), dc_comp=False)
          for ox, oy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))]
    gsad = np.minimum.reduce(gs)
    good = (sb * 5 <= gsad * 4) & (gsad - sb >= 96)
    q = (vx - gmx) ** 2 + (vy - gmy) ** 2
    vv = vx ** 2 + vy ** 2
    gmv = gmx * gmx + gmy * gmy
    dev = q > (gmv >> 2) + 4
    mov = vv > gmv + 4
    one, zero = np.ones(N, np.int64), np.zeros(N, np.int64)
    tex = vm.sad_cur_grid(L2c, L2c, BX, BY, one, zero) + vm.sad_cur_grid(L2c, L2c, BX, BY, zero, one)
    texp = vm.sad_cur_grid(L2p, L2p, BX + vx, BY + vy, one, zero) + vm.sad_cur_grid(L2p, L2p, BX + vx, BY + vy, zero, one)
    texm = np.minimum(tex, texp)
    istex = texm >= 64
    rm = 2 * sb <= texm
    f1 = good & dev & istex
    fg0 = f1 & mov & rm
    slw = 4 * vv <= gmv
    inb = (bxq >= 1) & (bxq <= 58) & (byq >= 1) & (byq <= 32)
    stl = gmv <= 4
    an0 = np.where(stl, istex & (4 * vv <= 4) & rm, f1 & slw & rm) & inb
    need = fg0 | an0

    def nbok(ox, oy):
        nx, ny = bxq + ox, byq + oy
        ok = (nx >= 0) & (nx < nbx2) & (ny >= 0) & (ny < nby2)
        nxc, nyc = np.clip(nx, 0, nbx2 - 1), np.clip(ny, 0, nby2 - 1)
        return ok & (np.abs(V2[nyc, nxc, 0] - vx) + np.abs(V2[nyc, nxc, 1] - vy) <= 4)

    nbk = np.zeros(N, bool)
    if need.any():
        nbk = nbok(-1, 0) | nbok(1, 0) | nbok(0, -1) | nbok(0, 1)
    isfg, isa0 = fg0 & nbk, an0 & nbk
    h = np.zeros(N, np.int64) if anchor_hist is None else anchor_hist.astype(np.int64)
    hn = np.where(isa0, np.minimum(h + 1, 255), 0)
    doit = isfg | (isa0 & (hn >= 2))
    prio = np.where(isfg, vm._isqrt(np.minimum(q >> 2, 16760836)) + 1, 0)
    GK = np.zeros((68, 120), np.int64)
    for n in np.nonzero(doit)[0]:
        pkey = (int(prio[n]) << 20) | int(n)
        cx = int(bxq[n]) * 32 + 16 + 2 * int(vx[n])
        cy = int(byq[n]) * 32 + 16 + 2 * int(vy[n])
        for gy in ((cy - 16) >> 4, cy >> 4, (cy + 15) >> 4):
            for gx in ((cx - 16) >> 4, cx >> 4, (cx + 15) >> 4):
                if 0 <= gx < 120 and 0 <= gy < 68:
                    e = (cx - (gx * 16 + 8)) ** 2 + (cy - (gy * 16 + 8)) ** 2
                    d4 = min(int(vm._isqrt(16 * e)), 255)
                    GK[gy, gx] = max(GK[gy, gx], pkey | ((255 - d4) << 12))
    return GK, hn.astype(np.uint8)


def baseline_me1(L1p, L1c, V2, GK, gm, lumasum_cur, lumasum_prev, prevF=None):
    h, w = L1c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = vm._grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    n2y, n2x = V2.shape[:2]
    gmx, gmy = gm
    gmx2, gmy2 = gmx * 2, gmy * 2
    still = (gmx | gmy) == 0
    ix0 = np.clip((bx * 4 - 2) >> 3, 0, n2x - 1)
    ix1 = np.minimum(ix0 + 1, n2x - 1)
    iy0 = np.clip((by * 4 - 2) >> 3, 0, n2y - 1)
    iy1 = np.minimum(iy0 + 1, n2y - 1)
    nbi = [(iy0, ix0), (iy0, ix1), (iy1, ix0), (iy1, ix1)]
    nbv = [V2[a, b].astype(np.int64) * 2 for a, b in nbi]
    zero = np.zeros(N, np.int64)

    def tdv(ex, ey):
        if prevF is None:
            return zero, zero
        tx, ty = np.minimum(bx + ex, 119), np.minimum(by + ey, 67)
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

    def nbdist(a, b, cdx, cdy):
        v = V2[a, b].astype(np.int64) * 2
        return np.abs(cdx - v[:, 0]) + np.abs(cdy - v[:, 1])

    zs, zsum = vm.sad_bilateral(L1p, L1c, X, Y, zero, zero, dc_comp=False)
    best = np.full(N, BIG, np.int64)
    cx1, cy1 = zero.copy(), zero.copy()
    for sub, (cdx, cdy) in enumerate(cand):
        sad1, sum1 = vm.sad_bilateral(L1p, L1c, X, Y, cdx, cdy, dc_comp=False)
        ist = 4 <= sub <= 6
        bias = np.zeros(N, np.int64)
        if ist:
            bias += 16
        if sub == 7:
            bias = np.where(still, 48, 0).astype(np.int64)
        if sub == 9:
            bias += 32
        if sub == 8:
            bias = np.where(isfgk, 96, 64).astype(np.int64)
        adx, ady = cdx - gdx, cdy - gdy
        afar = (np.abs(adx) + np.abs(ady)) > 3
        apen = np.where(isanc & afar & (sub != 8), 512, 0)
        p1f = (np.abs(cdx) + np.abs(cdy)) > 8
        p1p = np.where(p1f, np.maximum((np.abs(sum1 - zsum) >> 1) - zs, 0), 0)
        cost = sad1 + 256 + apen + p1p - bias
        if sub < 8:
            valid = np.ones(N, bool)
            if ist:
                sz = np.abs(cdx) + np.abs(cdy)
                smin = np.minimum.reduce([nbdist(a, b, cdx, cdy) for a, b in nbi] + [
                    sz, np.abs(cdx - gmx2) + np.abs(cdy - gmy2), np.where(hasfg, np.abs(cdx - gdx) + np.abs(cdy - gdy), sz)])
                tok = (smin <= 3) | (sz <= 64) | isfgk
                valid = tok
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
        sad2, sum2 = vm.sad_bilateral(L1p, L1c, X, Y, d2x, d2y, dc_comp=False)
        p2f = (np.abs(d2x) + np.abs(d2y)) > 8
        p2p = np.where(p2f, np.maximum((np.abs(sum2 - zsum) >> 1) - zs, 0), 0)
        key = ((sad2 + 256 + p2p + 4 * (abs(ox) + abs(oy))) << 8) | k
        better = key < best2
        best2 = np.where(better, key, best2)
        bk = np.where(better, k, bk)
    bvx, bvy = cx1 + bk % 3 - 1, cy1 + bk // 3 - 1
    r0, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx, bvy, dc_comp=False)
    r1, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx - 1, bvy, dc_comp=False)
    r2, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx + 1, bvy, dc_comp=False)
    r3, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx, bvy - 1, dc_comp=False)
    r4, _ = vm.sad_bilateral(L1p, L1c, X, Y, bvx, bvy + 1, dc_comp=False)
    r5, _ = vm.sad_bilateral(L1p, L1c, X, Y, zero, zero, dc_comp=False)
    spx = vm._equi(r1, r0, r2)
    spy = vm._equi(r3, r0, r4)
    vx = (bvx.astype(np.float32) + spx) * np.float32(2.0)
    vy = (bvy.astype(np.float32) + spy) * np.float32(2.0)
    zero0 = (r5 <= r0 + 48) & ~isfgk & still
    fsc, fsp = np.float32(lumasum_cur), np.float32(lumasum_prev)
    fad = np.abs(fsc - fsp)
    fade0 = fad > fsp * np.float32(0.02)
    fade = bool(fade0 and max(abs(gmx), abs(gmy)) <= 1)
    zero_all = zero0 | fade
    FT = np.stack([np.where(zero_all, 0, vx), np.where(zero_all, 0, vy)], -1).astype(np.float32).reshape(nby, nbx, 2)
    cthr = fad * np.float32(0.0019753087) + np.float32(256.0)
    cutn = int((np.minimum(r0, r5).astype(np.float32) > cthr).sum())
    return FT, cutn, fade


def baseline_mf8(L1p, L1c, FT, GK, V2, gm, fade):
    nby, nbx = FT.shape[:2]
    bx, by = vm._grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    gk = GK[by, bx]
    hasfg = gk != 0
    isfgk = gk >= (1 << 20)
    nfgk = ~isfgk
    gmx4, gmy4 = np.float32(gm[0] * 4), np.float32(gm[1] * 4)
    av = V2.reshape(-1, 2)[gk & 4095].astype(np.int64) * 2
    cap = np.where(nfgk, 32, 8)
    lsh = np.where(nfgk, 2, 1)

    def ldT(ox, oy):
        return FT[np.clip(by + oy, 0, nby - 1), np.clip(bx + ox, 0, nbx - 1)]

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
            cv = np.where(isfgk[:, None], np.array([gmx4, gmy4], np.float32)[None, :], cv)
        cx, cy = vm.rnd(cv[:, 0] * np.float32(0.5)), vm.rnd(cv[:, 1] * np.float32(0.5))
        scx = np.where(nfgk, cx >> 1, 0)
        scy = np.where(nfgk, cy >> 1, 0)
        sad, _ = vm.sad_bilateral(L1p, L1c, X + scx, Y + scy, cx, cy, dc_comp=False)
        terms = [np.minimum(np.abs(cx - nx) + np.abs(cy - ny), cap) for nx, ny in nbq]
        sm = sum(terms[j] for j in (0, 1, 2, 3, 5, 6, 7, 8)) + np.where(nfgk, terms[4] * 3, 0)
        pen = sm << lsh
        bias = np.where(hasfg, 96, 0) if sub == 0 else 0
        aoff = np.abs(cx - av[:, 0]) + np.abs(cy - av[:, 1])
        apen = np.where(hasfg & nfgk & (aoff > 3), 512, 0)
        cost = sad + 256 + pen + apen - bias
        key = (cost << 8) | sub
        valid = np.full(N, sub == 0) if fade else np.ones(N, bool)
        key = np.where(valid, key, BIG)
        better = key < best
        best = np.where(better, key, best)
        winv = np.where(better[:, None], cv, winv)
    return winv.reshape(nby, nbx, 2)


def run_pipeline(A, B, mode="after", prevF=None, anchor_hist=None):
    L1a, L2a, L3a = vref.pyr8(A)
    L1b, L2b, L3b = vref.pyr8(B)
    sum_a = int(L3a.sum())
    if mode == "before":
        V3, sum_b = vm.me3(L3a, L3b, prevF, dc_comp=False)
        V2, S2 = baseline_me2(L2a, L2b, V3, prevF)
        gm = vm.gm8(V2, S2)
        GK, hist = baseline_fgs8(L2a, L2b, V2, S2, gm, anchor_hist)
        FT, cutn, fade = baseline_me1(L1a, L1b, V2, GK, gm, sum_b, sum_a, prevF)
        F = baseline_mf8(L1a, L1b, FT, GK, V2, gm, fade)
    else:
        V3, sum_b = vm.me3(L3a, L3b, prevF, dc_comp=True)
        V2, S2 = vm.me2(L2a, L2b, V3, prevF)
        gm = vm.gm8(V2, S2)
        GK, hist = vm.fgs8(L2a, L2b, V2, S2, gm, anchor_hist)
        FT, cutn, fade = vm.me1(L1a, L1b, V2, GK, gm, sum_b, sum_a, prevF)
        F = vm.mf8(L1a, L1b, FT, GK, V2, gm, fade)
    mid = vw.warp8(A, B, F, GK, cutn, t=0.5)
    return dict(V3=V3, V2=V2, S2=S2, gm=gm, GK=GK, FT=FT, F=F, mid=mid, hist=hist)


# ---------------------------------------------------------------------------------------------------------------
# Synthetic 1080p Scene Generators
# ---------------------------------------------------------------------------------------------------------------
def _make_textured_canvas(h, w, seed, base_luma=0.35, contrast=0.22, tint=(1.0, 0.95, 0.9)):
    rng = np.random.default_rng(seed)
    # Multi-scale non-periodic spatial texture (smooth octaves + fine grain) so coarse-to-fine pyramid has no periodic aliasing
    g1 = rng.standard_normal((h // 16 + 4, w // 16 + 4)).astype(np.float32)
    g2 = rng.standard_normal((h // 4 + 4, w // 4 + 4)).astype(np.float32)
    g3 = rng.standard_normal((h, w)).astype(np.float32)
    y1 = np.repeat(np.repeat(g1, 16, axis=0), 16, axis=1)[:h, :w]
    y2 = np.repeat(np.repeat(g2, 4, axis=0), 4, axis=1)[:h, :w]
    pattern = np.clip(0.45 * y1 + 0.35 * y2 + 0.20 * g3, -1.5, 1.5)
    luma = np.clip(base_luma + contrast * pattern, 0.05, 0.95)
    img = np.stack([
        np.clip(luma * tint[0], 0.02, 0.98),
        np.clip(luma * tint[1], 0.02, 0.98),
        np.clip(luma * tint[2], 0.02, 0.98),
    ], axis=-1).astype(np.float32)
    return img


def _sample_patch(canvas, y0, x0, h, w):
    ys = np.clip(np.arange(y0, y0 + h), 0, canvas.shape[0] - 1)
    xs = np.clip(np.arange(x0, x0 + w), 0, canvas.shape[1] - 1)
    return canvas[ys[:, None], xs[None, :]].copy()


def _place_rect(frame, flow_gt, roi_mask, patch, cy, cx, vx, vy, illum_shift=0.0, blur_px=0):
    """Places a moving textured patch centered at (cx, cy) with velocity (vx, vy) full-res px/frame
    (convention: cur(x) ~ prev(x + d), so at prev it is centered at cx + vx/2 and at cur at cx - vx/2)."""
    ph, pw = patch.shape[:2]
    y0, x0 = int(round(cy - ph // 2)), int(round(cx - pw // 2))
    y1, x1 = max(0, y0), max(0, x0)
    y2, x2 = min(H, y0 + ph), min(W, x0 + pw)
    if y2 <= y1 or x2 <= x1:
        return
    sub = patch[y1 - y0:y2 - y0, x1 - x0:x2 - x0].copy()
    if blur_px > 0:
        acc = np.zeros_like(sub)
        steps = 2 * blur_px + 1
        for s in range(-blur_px, blur_px + 1):
            acc += np.roll(sub, s, axis=1)
        sub = acc / np.float32(steps)
    if illum_shift != 0.0:
        sub = np.clip(sub + np.float32(illum_shift), 0.02, 0.98)
    frame[y1:y2, x1:x2] = sub
    if roi_mask is not None:
        # Interior ROI (excluding 16px boundary transition where block straddles background)
        ry1, ry2 = min(y2, y1 + 16), max(y1, y2 - 16)
        rx1, rx2 = min(x2, x1 + 16), max(x1, x2 - 16)
        if ry2 > ry1 and rx2 > rx1:
            roi_mask[ry1:ry2, rx1:rx2] = True
        else:
            roi_mask[y1:y2, x1:x2] = True
    if flow_gt is not None:
        by1, by2 = max(0, y1 // 16), min(NBY, y2 // 16)
        bx1, bx2 = max(0, x1 // 16), min(NBX, x2 // 16)
        flow_gt[by1:by2, bx1:bx2, 0] = vx
        flow_gt[by1:by2, bx1:bx2, 1] = vy


def make_case1_mid_speed_pan():
    """Scenario 1A: Camera pans at gm = (8, 0) L2 px = (32, 0) full-res px/frame.
    Two articulated/mid-speed objects move in the dead zone 0.5 * |gm| <= |V_obj| <= |gm|:
      - Object 1: V = (20, 4) full-res px/frame (|V| = 0.64 * |gm|)
      - Object 2: V = (16, 0) full-res px/frame (|V| = 0.50 * |gm|)
    """
    bg_canvas = _make_textured_canvas(H + 256, W + 256, seed=101, base_luma=0.28, contrast=0.18, tint=(0.9, 0.95, 1.0))
    obj1_patch = _make_textured_canvas(160, 160, seed=202, base_luma=0.68, contrast=0.24, tint=(1.0, 0.85, 0.7))
    obj2_patch = _make_textured_canvas(160, 160, seed=203, base_luma=0.62, contrast=0.24, tint=(0.8, 1.0, 0.8))

    cam_vx, cam_vy = 32, 0
    ox0, oy0 = 128, 128
    # cur(x) = bg(ox0 + cam_vx/2 + x) = prev(x + cam_vx) where prev(x) = bg(ox0 - cam_vx/2 + x)
    A = _sample_patch(bg_canvas, oy0 - cam_vy // 2, ox0 - cam_vx // 2, H, W)
    GT = _sample_patch(bg_canvas, oy0, ox0, H, W)
    B = _sample_patch(bg_canvas, oy0 + cam_vy // 2, ox0 + cam_vx // 2, H, W)

    flow_gt = np.zeros((NBY, NBX, 2), np.float32)
    flow_gt[..., 0] = cam_vx
    flow_gt[..., 1] = cam_vy
    roi = np.zeros((H, W), bool)
    epe_roi = np.zeros((H, W), bool)

    objs = [
        (obj1_patch, 416, 640, 20, 4),
        (obj2_patch, 672, 1184, 16, 0),
    ]
    for patch, cy, cx, vx, vy in objs:
        _place_rect(A, None, None, patch, cy + vy * 0.5, cx + vx * 0.5, vx, vy)
        _place_rect(GT, flow_gt, roi, patch, cy, cx, vx, vy)
        _place_rect(B, None, None, patch, cy - vy * 0.5, cx - vx * 0.5, vx, vy)
        epe_roi[cy - 64:cy + 64, cx - 112:cx - 64] = True
        epe_roi[cy - 64:cy + 64, cx + 64:cx + 80] = True

    return A, GT, B, flow_gt, roi, epe_roi


def make_case2_anchor_articulated_limbs():
    """Scenario 1B: Third-person character torso is an anchor V_torso = (0, 0) while camera pans at (-32, 0) px/frame.
    Attached swinging arms/cloak (sharing L2 anchor blocks with the torso edge) move at:
      - Left arm: V_limb1 = (12, -8) full-res px/frame ((6, -4) L1 px, aad = 10 > 3)
      - Right arm/weapon: V_limb2 = (-12, 8) full-res px/frame ((-6, 4) L1 px, aad = 10 > 3)
    Warm anchor history (hn >= 1 from prior frame) is provided.
    """
    bg_canvas = _make_textured_canvas(H + 256, W + 256, seed=301, base_luma=0.30, contrast=0.18, tint=(0.85, 0.92, 1.0))
    torso_patch = _make_textured_canvas(128, 128, seed=302, base_luma=0.58, contrast=0.28, tint=(1.0, 0.9, 0.8))
    limb1_patch = _make_textured_canvas(96, 16, seed=303, base_luma=0.58, contrast=0.10, tint=(0.95, 0.85, 0.75))
    limb2_patch = _make_textured_canvas(96, 16, seed=304, base_luma=0.58, contrast=0.10, tint=(0.9, 0.95, 0.8))

    cam_vx, cam_vy = -32, 0
    ox0, oy0 = 128, 128
    A = _sample_patch(bg_canvas, oy0 - cam_vy // 2, ox0 - cam_vx // 2, H, W)
    GT = _sample_patch(bg_canvas, oy0, ox0, H, W)
    B = _sample_patch(bg_canvas, oy0 + cam_vy // 2, ox0 + cam_vx // 2, H, W)

    flow_gt = np.zeros((NBY, NBX, 2), np.float32)
    flow_gt[..., 0] = cam_vx
    flow_gt[..., 1] = cam_vy
    roi = np.zeros((H, W), bool)

    # Torso at screen center (960, 544), static V = (0, 0)
    _place_rect(A, None, None, torso_patch, 544, 960, 0, 0)
    _place_rect(GT, flow_gt, None, torso_patch, 544, 960, 0, 0)
    _place_rect(B, None, None, torso_patch, 544, 960, 0, 0)

    # Articulated limbs attached to torso (sharing L2 anchor blocks bxq=28 and bxq=31)
    limbs = [
        (limb1_patch, 544, 904, 12, -8),
        (limb2_patch, 544, 1016, -12, 8),
    ]
    for patch, cy, cx, vx, vy in limbs:
        _place_rect(A, None, None, patch, cy + vy * 0.5, cx + vx * 0.5, vx, vy)
        _place_rect(GT, flow_gt, roi, patch, cy, cx, vx, vy)
        _place_rect(B, None, None, patch, cy - vy * 0.5, cx - vx * 0.5, vx, vy)

    anchor_hist = np.ones(34 * 60, dtype=np.uint8)
    prevF = flow_gt.copy()
    return A, GT, B, flow_gt, roi, prevF, anchor_hist


def make_case3_fast_4dir_symmetric():
    """Scenario 2: Fast motion in all 4 directions (+X, -X, +Y, -Y) over a darker static textured background,
    verifying symmetric temporal prediction (lanes 10..11) + outer L2 ring (lanes 12..15) + gated p1p.
    """
    bg_canvas = _make_textured_canvas(H, W, seed=401, base_luma=0.18, contrast=0.12, tint=(0.9, 0.9, 1.0))
    A = bg_canvas.copy()
    GT = bg_canvas.copy()
    B = bg_canvas.copy()

    flow_gt = np.zeros((NBY, NBX, 2), np.float32)
    roi = np.zeros((H, W), bool)
    prevF = np.zeros((NBY, NBX, 2), np.float32)

    # 4 fast objects moving in +X, -X, +Y, -Y (32 px/frame = 16 L1 px/frame, |d|_1 = 16 > 8!)
    dirs = [
        (501, 96, 16, 320, 488, 32, 0),    # moving left (+dx): was at bx+1 in prevF
        (502, 96, 16, 320, 1448, -32, 0),  # moving right (-dx): was at bx-1 in prevF (needs lane 10!)
        (503, 16, 96, 776, 480, 0, 32),    # moving up (+dy): was at by+1 in prevF
        (504, 16, 96, 776, 1440, 0, -32),  # moving down (-dy): was at by-1 in prevF (needs lane 11!)
    ]
    for seed, ph, pw, cy, cx, vx, vy in dirs:
        patch = _make_textured_canvas(ph, pw, seed=seed, base_luma=0.82, contrast=0.18, tint=(1.0, 0.9, 0.75))
        _place_rect(A, None, None, patch, cy + vy * 0.5, cx + vx * 0.5, vx, vy)
        _place_rect(GT, flow_gt, roi, patch, cy, cx, vx, vy)
        _place_rect(B, None, None, patch, cy - vy * 0.5, cx - vx * 0.5, vx, vy)
        # In the previous frame (t - 1), the object was shifted upstream by 1 L1 block (16 px) in the direction of motion:
        sx = 1 if vx > 0 else (-1 if vx < 0 else 0)
        sy = 1 if vy > 0 else (-1 if vy < 0 else 0)
        by0, by1 = (cy - ph // 2) // 16 + sy, (cy + ph // 2) // 16 + sy
        bx0, bx1 = (cx - pw // 2) // 16 + sx, (cx + pw // 2) // 16 + sx
        prevF[max(0, by0):min(NBY, by1), max(0, bx0):min(NBX, bx1)] = [vx, vy]

    return A, GT, B, flow_gt, roi, prevF


def make_case4_illum_specular_blur():
    """Scenario 3: Illumination / specular variation & motion blur on moving textures.
    Object moves across a background whose mean brightness matches the specular-shifted object in frame B,
    causing pure luma SAD to prefer the wrong static/background match over the true illuminated texture match.
    """
    bg_canvas = _make_textured_canvas(H + 128, W + 128, seed=601, base_luma=0.50, contrast=0.14, tint=(0.95, 0.95, 0.95))
    A = bg_canvas[:H, :W].copy()
    GT = bg_canvas[:H, :W].copy()
    B = bg_canvas[:H, :W].copy()

    flow_gt = np.zeros((NBY, NBX, 2), np.float32)
    roi = np.zeros((H, W), bool)

    obj1 = _make_textured_canvas(128, 128, seed=602, base_luma=0.45, contrast=0.16, tint=(0.95, 0.95, 0.95))
    obj2 = _make_textured_canvas(128, 128, seed=603, base_luma=0.55, contrast=0.16, tint=(0.95, 0.95, 0.95))

    # Object 1 brightens by +0.10 (~25.5 luma levels, dc ~ 1632 <= 2048) from A to B; Object 2 darkens by -0.10 from A to B
    specs = [
        (obj1, 448, 640, 24, -8, +0.10),
        (obj2, 640, 1280, -24, 8, -0.10),
    ]
    for patch, cy, cx, vx, vy, d_lum in specs:
        _place_rect(A, None, None, patch, cy + vy * 0.5, cx + vx * 0.5, vx, vy, illum_shift=-d_lum * 0.5)
        _place_rect(GT, flow_gt, roi, patch, cy, cx, vx, vy, illum_shift=0.0)
        _place_rect(B, None, None, patch, cy - vy * 0.5, cx - vx * 0.5, vx, vy, illum_shift=+d_lum * 0.5, blur_px=1)

    prevF = flow_gt.copy()
    return A, GT, B, flow_gt, roi, prevF


def epe_metrics(F_est, F_gt, roi_mask):
    epe_map = np.sqrt(((F_est - F_gt) ** 2).sum(axis=-1))
    roi_blocks = np.zeros((NBY, NBX), bool)
    roi_blocks[:67, :] = roi_mask[:67 * 16, :].reshape(67, 16, NBX, 16).any(axis=(1, 3))
    return float(epe_map.mean()), float(epe_map[roi_blocks].mean())


class OpticalFlowPyramidTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {}

        # Case 1: Mid-speed moving object under camera pan (0.5 * |gm| <= |V| <= |gm|)
        A, GT, B, f_gt, roi, epe_roi = make_case1_mid_speed_pan()
        b1 = run_pipeline(A, B, mode="before")
        a1 = run_pipeline(A, B, mode="after")
        cls.results["1_mid_speed_pan"] = (
            epe_metrics(b1["F"], f_gt, epe_roi),
            epe_metrics(a1["F"], f_gt, epe_roi),
            psnr(b1["mid"], GT),
            psnr(a1["mid"], GT),
            psnr(b1["mid"], GT, roi),
            psnr(a1["mid"], GT, roi),
        )

        # Case 2: Articulated limbs on an anchor body during camera pan
        A, GT, B, f_gt, roi, prevF, ahist = make_case2_anchor_articulated_limbs()
        b2 = run_pipeline(A, B, mode="before", prevF=prevF, anchor_hist=ahist)
        a2 = run_pipeline(A, B, mode="after", prevF=prevF, anchor_hist=ahist)
        cls.results["2_anchor_limbs"] = (
            epe_metrics(b2["F"], f_gt, roi),
            epe_metrics(a2["F"], f_gt, roi),
            psnr(b2["mid"], GT),
            psnr(a2["mid"], GT),
            psnr(b2["mid"], GT, roi),
            psnr(a2["mid"], GT, roi),
        )

        # Case 3: Fast motion in all 4 directions (symmetric temporal + outer ring + gated p1p)
        A, GT, B, f_gt, roi, prevF = make_case3_fast_4dir_symmetric()
        b3 = run_pipeline(A, B, mode="before", prevF=prevF)
        a3 = run_pipeline(A, B, mode="after", prevF=prevF)
        cls.results["3_fast_4dir"] = (
            epe_metrics(b3["F"], f_gt, roi),
            epe_metrics(a3["F"], f_gt, roi),
            psnr(b3["mid"], GT),
            psnr(a3["mid"], GT),
            psnr(b3["mid"], GT, roi),
            psnr(a3["mid"], GT, roi),
        )

        # Case 4: Illumination / specular variation & motion blur on moving textures
        A, GT, B, f_gt, roi, prevF = make_case4_illum_specular_blur()
        b4 = run_pipeline(A, B, mode="before", prevF=prevF)
        a4 = run_pipeline(A, B, mode="after", prevF=prevF)
        cls.results["4_illum_specular"] = (
            epe_metrics(b4["F"], f_gt, roi),
            epe_metrics(a4["F"], f_gt, roi),
            psnr(b4["mid"], GT),
            psnr(a4["mid"], GT),
            psnr(b4["mid"], GT, roi),
            psnr(a4["mid"], GT, roi),
        )

        print("\n" + "=" * 102)
        print(f"{'Scenario':<22} | {'ROI EPE (px) Before -> After':<30} | {'ROI PSNR (dB) Before -> After':<30} | {'Full PSNR Delta':<15}")
        print("-" * 102)
        for name, (epe_b, epe_a, psnr_b, psnr_a, rpsnr_b, rpsnr_a) in cls.results.items():
            print(
                f"{name:<22} | {epe_b[1]:8.2f} -> {epe_a[1]:8.2f} ({epe_a[1]-epe_b[1]:+7.2f}) | "
                f"{rpsnr_b:7.2f} -> {rpsnr_a:7.2f} ({rpsnr_a-rpsnr_b:+6.2f} dB) | {psnr_a-psnr_b:+6.2f} dB"
            )
        print("=" * 102)

    def test_1_mid_speed_object_under_camera_pan(self):
        epe_b, epe_a, psnr_b, psnr_a, rpsnr_b, rpsnr_a = self.results["1_mid_speed_pan"]
        self.assertLess(epe_a[1], epe_b[1] * 0.5, "ROI EPE must drop by >50% in the 0.5*|gm|..|gm| dead zone")
        self.assertGreater(rpsnr_a, rpsnr_b + 3.0, "ROI PSNR must improve by >3 dB on mid-speed moving objects")

    def test_2_articulated_limbs_on_anchor_body(self):
        epe_b, epe_a, psnr_b, psnr_a, rpsnr_b, rpsnr_a = self.results["2_anchor_limbs"]
        self.assertLess(epe_a[1], epe_b[1] * 0.5, "ROI EPE on articulated limbs must drop by >50%")
        self.assertGreater(rpsnr_a, rpsnr_b + 2.0, "ROI PSNR on articulated limbs must improve by >2 dB")

    def test_3_fast_4dir_symmetric_prediction(self):
        epe_b, epe_a, psnr_b, psnr_a, rpsnr_b, rpsnr_a = self.results["3_fast_4dir"]
        self.assertLess(epe_a[1], epe_b[1] * 0.5, "ROI EPE on 4-direction fast motion must drop by >50%")
        self.assertGreater(rpsnr_a, rpsnr_b + 3.0, "ROI PSNR on 4-direction fast motion must improve by >3 dB")

    def test_4_illumination_and_specular_robustness(self):
        epe_b, epe_a, psnr_b, psnr_a, rpsnr_b, rpsnr_a = self.results["4_illum_specular"]
        self.assertLess(epe_a[1], epe_b[1] * 0.5, "ROI EPE under specular/illumination shift must drop by >50%")
        self.assertGreater(rpsnr_a, rpsnr_b + 3.0, "ROI PSNR under specular/illumination shift must improve by >3 dB")

    def test_5_shader_build_constraints(self):
        shaders_h = (ROOT / "classic" / "source" / "shaders.h").read_text(encoding="utf-8")
        for name in ("me3", "me2", "fgs8", "me1", "mf8"):
            m = re.search(rf"#define\s+{name.upper()}_RSRC1\s+0x([0-9A-Fa-f]+)", shaders_h)
            self.assertIsNotNone(m, f"Missing {name.upper()}_RSRC1 in classic/source/shaders.h")
            rsrc1 = int(m.group(1), 16)
            vgpr_blocks = rsrc1 & 0x3F
            max_vgprs = (vgpr_blocks + 1) * 4
            self.assertLessEqual(max_vgprs, 64, f"Shader {name} exceeds 64 VGPR limit (vgpr_blocks={vgpr_blocks})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
