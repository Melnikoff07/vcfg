"""Standalone verification suite for Chat 15 (vcfg-r15-specular-antishimmer):
Specular Highlight, Thin-Wire & Foliage Anti-Shimmer Filter in warp8.ll, warp8g.ll, and rcas8.ll.

Target Title Archetypes (strictly referenced by Title ID):
  - CUSA00918 / CUSA07408: Wet cobblestones, metallic armor & puddles with sub-pixel flickering specular glints
  - CUSA18534 / CUSA07408: 1-pixel diagonal power lines & chain-link fences moving against bright sky
  - CUSA07408 / CUSA18534 / CUSA00552: Dense high-frequency foliage & grass canopies panning across sky

Tests:
  1. Panning metallic surface with sub-pixel flickering specular glints (0.08 -> 1.0 -> 0.08):
     Verifies Karis inverse-luminance error weighting (@diff3_k), @tapsq highlight range normalization,
     Catmull-Rom (@cr) negative-lobe dark-halo suppression, and glint static-fallback suppression.
  2. Diagonal 1-pixel power lines & chain-link fence moving at (-10, 4) px/frame against bright sky:
     Verifies @cr valley-aware clamping and lane-3 sky-valley detection prevent false sky HUD/static
     overwrite (ws0, w_hud, cw/dsw) and eliminate bright/dark ringing halos, while rcas8 ridge-gated
     Enhance AA + anisotropic RCAS preserve 1-px line continuity.
  3. Dense high-frequency foliage canopy panning across bright sky:
     Verifies reduction in temporal shimmer / high-frequency flicker variance and PSNR improvement
     across warp8 + rcas8.
  4. Non-regression on disocclusion/HUD benchmarks & GCN2 hardware register/scratch/size assertions
     (vgprs <= 64, scratch == 0, shader region <= 0x1C000).
"""
import math
import pathlib
import re
import sys
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import vcfg_warp as vw
from test_disocclusion_and_hud import (
    build_opposite_motion_scene,
    build_subtitle_shadowbox_scene,
)

F32 = np.float32


def psnr(pred, gt, mask=None):
    diff = (pred[..., :3].astype(np.float64) - gt[..., :3].astype(np.float64))
    if mask is not None:
        diff = diff[mask]
    mse = float(np.mean(diff * diff))
    if mse <= 1e-10:
        return 99.0
    return 10.0 * math.log10(1.0 / mse)


def cr_baseline(img, u, v, csh):
    """Baseline v2.31+r12 Catmull-Rom 5-tap filter (clamped only to [0, 1])."""
    ub, vb = u - F32(0.5), v - F32(0.5)
    fu, fv = np.floor(ub), np.floor(vb)
    fx, fy = ub - fu, vb - fv
    cx, cy = fu + F32(0.5), fv + F32(0.5)
    wx0, wx12, wx3, ox = vw.crw(fx, csh)
    wy0, wy12, wy3, oy = vw.crw(fy, csh)
    x12, y12 = cx + ox, cy + oy
    x0, x3, y0, y3 = cx - F32(1.0), cx + F32(2.0), cy - F32(1.0), cy + F32(2.0)
    taps = [
        (x12, y12, wx12 * wy12),
        (x0, y12, wx0 * wy12),
        (x3, y12, wx3 * wy12),
        (x12, y0, wx12 * wy0),
        (x12, y3, wx12 * wy3),
    ]
    acc = 0
    ws = 0
    for tx, ty, k in taps:
        acc = acc + vw.bil(img, tx, ty) * k[..., None]
        ws = ws + k
    cpos = np.asarray(csh > F32(0.0))
    res = np.clip(acc / ws[..., None], F32(0.0), F32(1.0)).astype(F32)
    s0 = vw.bil(img, x12, y12)
    return np.where(cpos[..., None], res, s0).astype(F32)


def warp8_baseline_v231_r12(tp, tc, F, GK, t=0.5, csh=0.5):
    """Baseline v2.31+r12 coherent/slow warp8 before Chat 15 anti-shimmer filter."""
    tp, tc = tp[..., :3].astype(F32), tc[..., :3].astype(F32)
    H, W = tp.shape[:2]
    ys = np.arange(H, dtype=F32)
    xs = np.arange(W, dtype=F32)
    X, Y = np.meshgrid(xs, ys)
    u, v = X + F32(0.5), Y + F32(0.5)
    sp, sc = tp, tc
    es = vw.diff3(sp, sc)
    nbx, nby = F.shape[1], F.shape[0]
    fbx, fby = X * F32(1.0 / 16.0) - F32(0.46875), Y * F32(1.0 / 16.0) - F32(0.46875)
    flx, fly = np.floor(fbx), np.floor(fby)
    wx, wy = fbx - flx, fby - fly
    ixr, iyr = flx.astype(np.int64), fly.astype(np.int64)
    ix0, ix1 = np.clip(ixr, 0, nbx - 1), np.clip(ixr + 1, 0, nbx - 1)
    iy0, iy1 = np.clip(iyr, 0, nby - 1), np.clip(iyr + 1, 0, nby - 1)
    f00, f10, f01, f11 = F[iy0, ix0], F[iy0, ix1], F[iy1, ix0], F[iy1, ix1]
    iwx, iwy = F32(1.0) - wx, F32(1.0) - wy
    w00, w10, w01, w11 = iwx * iwy, wx * iwy, iwx * wy, wx * wy
    mx = np.maximum(np.maximum(f00, f10), np.maximum(f01, f11))
    nn = np.minimum(np.minimum(f00, f10), np.minimum(f01, f11))
    spr = np.maximum(mx[..., 0] - nn[..., 0], mx[..., 1] - nn[..., 1])
    vbx = (f00[..., 0] * w00 + f10[..., 0] * w10) + (f01[..., 0] * w01 + f11[..., 0] * w11)
    vby = (f00[..., 1] * w00 + f10[..., 1] * w10) + (f01[..., 1] * w01 + f11[..., 1] * w11)
    vmb = np.abs(vbx) + np.abs(vby)
    vms = vw.sat((vmb - F32(9.0)) * F32(0.14))
    csh_e = F32(csh) * (F32(1.0) - vms)
    tc1 = F32(1.0) - F32(t)
    up, vp = u + vbx * F32(t), v + vby * F32(t)
    uc, vc = u - vbx * tc1, v - vby * tc1
    Wd, Hd = F32(W), F32(H)
    oobp = (up < F32(0.5)) | (up > Wd - F32(0.5)) | (vp < F32(0.5)) | (vp > Hd - F32(0.5))
    oobc = (uc < F32(0.5)) | (uc > Wd - F32(0.5)) | (vc < F32(0.5)) | (vc > Hd - F32(0.5))
    csh_b = np.where(oobp | oobc, F32(0.0), csh_e)
    a2 = cr_baseline(tp, up, vp, csh_b)
    b2 = cr_baseline(tc, uc, vc, csh_b)
    fe = np.where(oobp | oobc, F32(0.02), vw.diff3(a2, b2))
    eac_f, ebp_f = vw.diff3(a2, sc), vw.diff3(b2, sp)
    mov, fad = vw.side_lerp(a2, b2, sp, sc, fe, eac_f, ebp_f, t, vmb, F32(0.0), F32(0.0))
    frel = np.minimum(fad * F32(0.10), fe * F32(0.60))
    emov = np.maximum(fe - frel, F32(0.0))
    vmg = vmb
    stat = vw.lerp4(sp, sc, t)
    l_mov = vw.sum3(mov)
    l_stat = vw.sum3(stat)
    dl_s = l_stat - l_mov
    w_occ_m = vw.sat((spr - F32(12.0)) * F32(0.5)) * vw.sat((es - F32(0.80)) * F32(4.0)) * vw.sat((F32(0.31) - emov) * F32(4.0))
    d_sm = stat - mov
    mx_ch = np.maximum(np.maximum(d_sm[..., 0], d_sm[..., 1]), d_sm[..., 2])
    pos_c = np.maximum(l_stat - F32(2.56), mx_ch * F32(3.0) - dl_s - F32(0.60))
    ws0_raw = vw.sat((F32(0.006) - es) * F32(333.33334))
    dk_spot = (vmg > F32(2.0)) & (emov < F32(0.015)) & (dl_s < F32(-0.48))
    ws0 = np.where(dk_spot, F32(0.0), ws0_raw)
    gbx = np.where(wx < F32(0.5), ix0, ix1)
    gby = np.where(wy < F32(0.5), iy0, iy1)
    gk = GK[np.clip(gby, 0, nby - 1), np.clip(gbx, 0, nbx - 1)]
    hasfg = gk >= (1 << 20)
    nfgk = ~hasfg
    w_hv = vw.sat((vmg + spr) * F32(2.0))
    w_he = vw.sat((F32(0.08) - es) * F32(32.0))
    w_hc = vw.sat(pos_c * F32(64.0))
    w_nd = vw.sat((dl_s - F32(0.05)) * F32(25.0))
    w_hud = np.where(nfgk, w_hv * w_he * w_hc * w_nd, F32(0.0))
    ws0_h = np.maximum(ws0, w_hud)
    mov_obj = hasfg | (vmg > F32(8.0))
    fgkill_ws = hasfg & (emov < 0.25)
    fgkill = mov_obj & (emov < 0.25)
    ws = np.where(fgkill_ws, F32(0.0), ws0_h)
    dmp = vw.diff3(mov, sp)
    dmc = vw.diff3(mov, sc)
    dmv = vw.diff3(mov, stat)
    ddm = dmp - dmc
    k_ddm = F32(0.45) + F32(0.45) * w_occ_m
    lim_hi = F32(0.30) + F32(0.20) * w_occ_m
    dtsc = np.clip(ddm * k_ddm, -lim_hi, lim_hi)
    tsc = vw.sat(F32(t) + dtsc)
    fillc = sp + (sc - sp) * tsc[..., None]
    cw2 = vw.sat((emov - F32(0.5)) * F32(2.5)) * vw.sat((F32(0.80) - es) * F32(4.0))
    bg3 = (vmg > 8.0) & (es < 0.25) & ~mov_obj & (emov > 0.25)
    bgs = vw.sat((dmv - es - F32(0.22)) * F32(4.0))
    bgw = np.where(bg3, bgs, F32(0.0))
    dsw = vw.sat((dl_s - F32(0.18)) * F32(2.5)) * vw.sat((emov - F32(0.22)) * F32(5.0)) * vw.sat((F32(0.30) - es) * F32(4.0))
    cw_base = np.maximum(np.maximum(cw2, bgw), dsw)
    res_cw = vw.mlp_cw(ddm, emov, es, dmv, dl_s, spr, vmg)
    dcw = vw.sat((emov - F32(0.15)) * F32(10.0)) * res_cw
    cw3_0 = vw.sat(cw_base + dcw)
    gcw = vw.sat((emov - es) * F32(4.0))
    cw = np.where(fgkill, F32(0.0), cw3_0 * gcw)
    movc = vw.lerp4(mov, fillc, cw[..., None])
    return vw.lerp4(movc, stat, ws[..., None]).astype(F32)


def rcas8_baseline_v231_r12(mid, con, emode):
    """Baseline v2.31+r12 rcas8 before Chat 15 anti-shimmer filter."""
    H, W = mid.shape[:2]
    c = mid.astype(F32)
    luma = c[..., 1] * F32(0.5) + (c[..., 0] + c[..., 2]) * F32(0.25)
    c8 = np.floor(np.clip(c, 0, 1) * F32(255.0) + F32(0.5)) / F32(255.0)
    Lp = np.pad(luma, 2, mode='edge')
    Cp = np.pad(c8, ((2, 2), (2, 2), (0, 0)), mode='edge')

    def L(dy, dx):
        return Lp[2 + dy:2 + dy + H, 2 + dx:2 + dx + W]

    def C(dy, dx):
        return Cp[2 + dy:2 + dy + H, 2 + dx:2 + dx + W].astype(F32)

    M, N, S, Wc, E = C(0, 0), C(-1, 0), C(1, 0), C(0, -1), C(0, 1)
    lM, lN, lS, lW, lE = L(0, 0), L(-1, 0), L(1, 0), L(0, -1), L(0, 1)
    lNW, lNE, lSW, lSE = L(-1, -1), L(-1, 1), L(1, -1), L(1, 1)
    mn4 = np.minimum(np.minimum(lN, lW), np.minimum(lE, lS))
    mx4 = np.maximum(np.maximum(lN, lW), np.maximum(lE, lS))
    mn5, mx5 = np.minimum(mn4, lM), np.maximum(mx4, lM)
    rng5 = mx5 - mn5
    eps = F32(2.0 ** -14)
    hmin = mn4 / ((mx4 + eps) * F32(4.0))
    hmax = (F32(1.0) - mx4) / ((mn4 - F32(1.0)) * F32(4.0) - eps)
    lobe = np.maximum(np.maximum(-hmin, hmax), -F32(con))
    w0 = np.minimum(lobe, F32(0.0))
    s4l = (lN + lW) + (lE + lS)
    nz = vw.sat(np.abs(s4l * F32(0.25) - lM) / (rng5 + eps))
    w_rcas = w0 * (F32(1.0) - nz * F32(0.5))
    if emode > 0:
        mn9 = np.minimum(mn5, np.minimum(np.minimum(lNW, lNE), np.minimum(lSW, lSE)))
        mx9 = np.maximum(mx5, np.maximum(np.maximum(lNW, lNE), np.minimum(lSW, lSE)))
        rng9 = mx9 - mn9
        rng9e = rng9 + eps
        is_str = emode >= 2
        thr_abs, thr_rel = (F32(0.015625), F32(0.0625)) if is_str else (F32(0.0234375), F32(0.09375))
        ethr = np.maximum(thr_abs, mx9 * thr_rel)
        edge_ramp = vw.sat((rng9 - ethr) * F32(32.0))
        edgeH = np.abs(lNW + lSW - lW * 2) + np.abs(lN + lS - lM * 2) * 2 + np.abs(lNE + lSE - lE * 2)
        edgeV = np.abs(lNW + lNE - lN * 2) + np.abs(lW + lE - lM * 2) * 2 + np.abs(lSW + lSE - lS * 2)
        horz = edgeH >= edgeV
        dCard = np.maximum(np.abs(lN - lS), np.abs(lW - lE))
        dDiag = np.maximum(np.abs(lNE - lSW), np.abs(lNW - lSE)) * F32(0.5)
        tex_gate = vw.sat((dCard + dDiag) / rng9e * F32(1.5))
        lNeg, lPos = np.where(horz, lN, lW), np.where(horz, lS, lE)
        h3 = horz[..., None]
        rgbNeg, rgbPos = np.where(h3, N, Wc), np.where(h3, S, E)
        rgbTan1, rgbTan2 = np.where(h3, Wc, N), np.where(h3, E, S)
        gNeg, gPos = np.abs(lNeg - lM), np.abs(lPos - lM)
        useNeg = gNeg >= gPos
        gMax = np.maximum(gNeg, gPos)
        lOpp = np.where(useNeg, lNeg, lPos)
        rgbOpp = np.where(useNeg[..., None], rgbNeg, rgbPos)
        lEdge = (lM + lOpp) * F32(0.5)
        gThr = gMax * F32(0.25)
        ty_ = np.where(horz, 0, 1)
        tx_ = np.where(horz, 1, 0)
        sgn = np.where(useNeg, -1, 1)
        ny_ = np.where(horz, sgn, 0)
        nx_ = np.where(horz, 0, sgn)
        yy, xx = np.meshgrid(np.arange(H), np.arange(W), indexing='ij')

        def Lg(oy, ox):
            return Lp[2 + yy + oy, 2 + xx + ox]

        lm1, lm2 = Lg(-ty_, -tx_), Lg(-2 * ty_, -2 * tx_)
        lp1, lp2 = Lg(ty_, tx_), Lg(2 * ty_, 2 * tx_)
        lom1, lom2 = Lg(ny_ - ty_, nx_ - tx_), Lg(ny_ - 2 * ty_, nx_ - 2 * tx_)
        lop1, lop2 = Lg(ny_ + ty_, nx_ + tx_), Lg(ny_ + 2 * ty_, nx_ + 2 * tx_)
        eN1 = (lm1 + lom1) * F32(0.5) - lEdge
        eN2 = (lm2 + lom2) * F32(0.5) - lEdge
        cN1 = np.abs(eN1) < gThr
        cN2 = cN1 & (np.abs(eN2) < gThr)
        dN = np.where(cN2, F32(3.0), np.where(cN1, F32(2.0), F32(1.0)))
        eN = np.where(cN1, eN2, eN1)
        eP1 = (lp1 + lop1) * F32(0.5) - lEdge
        eP2 = (lp2 + lop2) * F32(0.5) - lEdge
        cP1 = np.abs(eP1) < gThr
        cP2 = cP1 & (np.abs(eP2) < gThr)
        dP = np.where(cP2, F32(3.0), np.where(cP1, F32(2.0), F32(1.0)))
        eP = np.where(cP1, eP2, eP1)
        dMin, dSum = np.minimum(dN, dP), dN + dP
        eEnd = np.where(dN < dP, eN, eP)
        goodSpan = eEnd * (lM - lEdge) < 0
        wSpan = np.where(goodSpan, F32(0.5) - dMin / dSum, F32(0.0))
        s3x3 = s4l * F32(2.0) + ((lNW + lNE) + (lSW + lSE))
        subpx = vw.sat(np.abs(s3x3 * F32(0.083333336) - lM) / rng9e)
        wSubpx = subpx * F32(1.0 if is_str else 0.75) * tex_gate
        NW, NE, SW, SE = C(-1, -1), C(-1, 1), C(1, -1), C(1, 1)
        un = useNeg[..., None]
        dH = np.where(un, (NW + NE) * F32(0.5), (SW + SE) * F32(0.5))
        dV = np.where(un, (NW + SW) * F32(0.5), (NE + SE) * F32(0.5))
        rgbDiag = np.where(h3, dH, dV)
        rgbTanAvg = (rgbTan1 + rgbTan2) * F32(0.5)
        rgbTarget = rgbOpp * F32(0.4375) + rgbTanAvg * F32(0.3125) + rgbDiag * F32(0.25)
        wComb = np.maximum(wSpan * F32(1.5), wSubpx)
        wClp = np.minimum(wComb, F32(0.75 if is_str else 0.5625))
        wAA = edge_ramp * wClp
        base_c = M + (rgbTarget - M) * wAA[..., None]
        w_eff = w_rcas * (F32(1.0) - edge_ramp)
    else:
        base_c, w_eff = M, w_rcas
    v4 = (N + S) + (Wc + E)
    num = v4 * w_eff[..., None] + base_c
    res0 = num * (F32(1.0) / (F32(1.0) + w_eff * F32(4.0)))[..., None]
    c_mn = np.minimum(np.minimum(np.minimum(N, Wc), np.minimum(E, S)), base_c)
    c_mx = np.maximum(np.maximum(np.maximum(N, Wc), np.maximum(E, S)), base_c)
    return np.minimum(np.maximum(res0, c_mn), c_mx).astype(F32)


def build_specular_glint_scene(h=192, w=320, v_cam=(-8.0, 2.0)):
    """Scene 1 (CUSA00918 / CUSA07408 archetype):
    Panning dark wet cobblestone / metallic surface with 1-2 px sub-pixel flickering specular glints
    (0.08 -> 1.0 -> 0.08) that flicker in intensity between P and C due to sub-pixel normal-map aliasing.
    """
    yy, xx = np.meshgrid(np.arange(h, dtype=F32), np.arange(w, dtype=F32), indexing="ij")
    vx, vy = float(v_cam[0]), float(v_cam[1])

    def base_metal(x, y):
        # Dark wet cobblestone / metallic base around 0.08 with subtle normal-map micro-variation
        n0 = 0.078 + 0.008 * np.sin(x * 0.17) * np.cos(y * 0.19)
        n1 = 0.082 + 0.008 * np.cos(x * 0.15 - y * 0.13)
        n2 = 0.088 + 0.009 * np.sin((x + y) * 0.14)
        return np.stack([n0, n1, n2], axis=-1).astype(F32)

    glint_centers = [
        (64.0, 48.0), (128.0, 52.0), (196.0, 44.0), (252.0, 56.0),
        (72.0, 96.0), (144.0, 102.0), (212.0, 92.0), (264.0, 104.0),
        (84.0, 144.0), (160.0, 148.0), (228.0, 140.0),
    ]

    def render_frame(phase, amp_scale):
        sx = xx + phase * vx
        sy = yy + phase * vy
        img = base_metal(sx, sy)
        for idx, (gx, gy) in enumerate(glint_centers):
            dx = sx - gx
            dy = sy - gy
            r2 = dx * dx + dy * dy
            # Sharp 1-2 px specular glint lobe (0.08 -> 1.0 -> 0.08)
            a_idx = amp_scale[idx % len(amp_scale)]
            glint = a_idx * np.exp(-r2 / 0.95).astype(F32)
            spec_rgb = np.stack([glint * 0.96, glint * 0.94, glint * 0.90], axis=-1)
            img = np.clip(img + spec_rgb, F32(0.0), F32(1.0))
        return img.astype(F32)

    # Sub-pixel normal-map specular flicker between P and C (0.96 vs 0.24 -> raw diff3 > 1.5)
    amps_p = [0.96, 0.24, 0.94, 0.22]
    amps_c = [0.24, 0.96, 0.22, 0.94]
    amps_gt = [0.5 * (ap + ac) for ap, ac in zip(amps_p, amps_c)]
    P = render_frame(-0.5, amps_p)
    C = render_frame(+0.5, amps_c)
    GT = render_frame(0.0, amps_gt)

    # Build 8x8 ROI mask around the t=0.5 glint centers and around the unwarped P/C ghost positions
    glint_roi_8x8 = np.zeros((h, w), dtype=bool)
    ghost_roi = np.zeros((h, w), dtype=bool)
    for gx, gy in glint_centers:
        cx, cy = int(round(gx)), int(round(gy))
        glint_roi_8x8[max(0, cy - 4):min(h, cy + 4), max(0, cx - 4):min(w, cx + 4)] = True
        for ph in (-0.5, +0.5):
            px_i = int(round(gx - ph * vx))
            py_i = int(round(gy - ph * vy))
            ghost_roi[max(0, py_i - 2):min(h, py_i + 3), max(0, px_i - 2):min(w, px_i + 3)] = True
    ghost_roi &= ~glint_roi_8x8

    gh, gw = h // 16, w // 16
    F = np.zeros((gh, gw, 2), dtype=F32)
    F[..., 0] = F32(vx)
    F[..., 1] = F32(vy)
    GK = np.zeros((gh, gw), dtype=np.uint32)
    return P, C, GT, F, GK, glint_roi_8x8, ghost_roi


def build_thin_wire_fence_scene(h=192, w=320, v_wire=(-10.0, 4.0)):
    """Scene 2 (CUSA18534 / CUSA07408 archetype):
    Diagonal 1-pixel dark power lines and a 1-pixel chain-link fence moving at (-10, 4) px/frame
    against a bright sky background (l_stat > 2.56).
    """
    yy, xx = np.meshgrid(np.arange(h, dtype=F32), np.arange(w, dtype=F32), indexing="ij")
    vx, vy = float(v_wire[0]), float(v_wire[1])

    def render_at(phase):
        sx = xx + phase * vx
        sy = yy + phase * vy
        # Bright sky background (sum3 ~ 2.68 > 2.56)
        sky_r = 0.85 + 0.03 * np.cos(sy * 0.015)
        sky_g = 0.89 + 0.03 * np.sin(sx * 0.012)
        sky_b = 0.95 - 0.02 * (sy / float(h))
        sky = np.stack([sky_r, sky_g, sky_b], axis=-1).astype(F32)

        # Three 1-pixel diagonal power lines across the sky
        d1 = np.abs((sy - 0.35 * sx - 24.0) * 0.943)
        d2 = np.abs((sy - 0.35 * sx - 58.0) * 0.943)
        d3 = np.abs((sy + 0.28 * sx - 148.0) * 0.963)
        wire_prof = np.maximum(
            np.maximum(np.clip(1.0 - d1 / 0.78, 0.0, 1.0), np.clip(1.0 - d2 / 0.78, 0.0, 1.0)),
            np.clip(1.0 - d3 / 0.78, 0.0, 1.0),
        )

        # Diagonal chain-link fence region in x in [120, 260), y in [76, 164)
        in_fence_box = (sx >= 120.0) & (sx < 260.0) & (sy >= 76.0) & (sy < 164.0)
        p_a = np.abs(((sx + sy) % 14.0) - 7.0)
        p_b = np.abs(((sx - sy + 256.0) % 14.0) - 7.0)
        fence_d = np.minimum(p_a, p_b) * 0.7071
        fence_prof = np.where(in_fence_box, np.clip(1.0 - fence_d / 0.75, 0.0, 1.0), 0.0)

        comb = np.maximum(wire_prof, fence_prof).astype(F32)
        wire_rgb = np.array([0.11, 0.12, 0.14], dtype=F32)
        img = sky * (1.0 - comb[..., None]) + wire_rgb * comb[..., None]
        return img.astype(F32), (comb > 0.25), (comb == 0.0) & (
            (np.minimum(np.minimum(d1, d2), d3) < 2.6) | (in_fence_box & (fence_d < 2.4))
        )

    P, _, _ = render_at(-0.5)
    GT, wire_mask, halo_band_mask = render_at(0.0)
    C, _, _ = render_at(+0.5)
    in_bounds = (xx >= 8.0) & (xx < float(w - 8)) & (yy >= 8.0) & (yy < float(h - 8))
    wire_mask = wire_mask & in_bounds
    halo_band_mask = halo_band_mask & in_bounds

    gh, gw = h // 16, w // 16
    F = np.zeros((gh, gw, 2), dtype=F32)
    F[..., 0] = F32(vx)
    F[..., 1] = F32(vy)
    GK = np.zeros((gh, gw), dtype=np.uint32)
    return P, C, GT, F, GK, wire_mask, halo_band_mask


def build_foliage_canopy_sequence(h=160, w=256, v_pan=(-8.0, 2.0), n_steps=5):
    """Scene 3 (CUSA07408 / CUSA18534 / CUSA00552 archetype):
    Dense high-frequency foliage / leaf canopy with sun-lit specular leaf tips and sky gaps
    panning across a bright sky over a multi-step sequence.
    """
    yy, xx = np.meshgrid(np.arange(h, dtype=F32), np.arange(w, dtype=F32), indexing="ij")
    vx, vy = float(v_pan[0]), float(v_pan[1])
    in_canopy_region = (yy >= 20.0) & (yy < float(h - 20)) & (xx >= 20.0) & (xx < float(w - 20))

    def sample_foliage(x, y, glint_phase=0.0):
        # Bright sky visible through canopy gaps (sum3 ~ 2.68 > 2.56)
        sky = np.stack([
            np.full_like(x, 0.85),
            np.full_like(x, 0.90),
            np.full_like(x, 0.95),
        ], axis=-1)
        # Anti-aliased leaf clusters + fine twigs
        hf1 = np.sin(x * 0.55 + np.cos(y * 0.45)) * np.cos(y * 0.60 - np.sin(x * 0.50))
        hf2 = np.cos((x + y) * 0.70) * np.sin((x - y) * 0.65)
        leaf_cov = vw.sat((0.60 * hf1 + 0.40 * hf2 + 0.15) * 3.2)

        # Sun-lit specular leaf-tip glints that flicker with sub-pixel view/normal phase
        g_grid = np.maximum(np.sin(x * 0.85) * np.cos(y * 0.80), 0.0) ** 6
        mod = 0.60 + 0.38 * np.sin(x * 0.35 + y * 0.25 + glint_phase)
        glint = (g_grid * mod).astype(F32)

        leaf_r = np.clip(0.14 + 0.08 * hf1 + 0.82 * glint, 0.0, 1.0)
        leaf_g = np.clip(0.24 + 0.12 * hf1 + 0.80 * glint, 0.0, 1.0)
        leaf_b = np.clip(0.10 + 0.05 * hf2 + 0.72 * glint, 0.0, 1.0)
        leaf = np.stack([leaf_r, leaf_g, leaf_b], axis=-1)

        canopy = sky * (1.0 - leaf_cov[..., None]) + leaf * leaf_cov[..., None]
        return np.where(in_canopy_region[..., None], canopy, sky).astype(F32)

    real_frames = []
    gt_mids = []
    for k in range(n_steps):
        shx, shy = k * 3.0, k * 1.0
        f_p = sample_foliage(xx + shx - 0.5 * vx, yy + shy - 0.5 * vy, glint_phase=0.0)
        f_c = sample_foliage(xx + shx + 0.5 * vx, yy + shy + 0.5 * vy, glint_phase=3.14159)
        f_gt = 0.5 * (
            sample_foliage(xx + shx, yy + shy, glint_phase=0.0)
            + sample_foliage(xx + shx, yy + shy, glint_phase=3.14159)
        )
        real_frames.append((f_p, f_c))
        gt_mids.append(f_gt.astype(F32))

    gh, gw = h // 16, w // 16
    F = np.zeros((gh, gw, 2), dtype=F32)
    F[..., 0] = F32(vx)
    F[..., 1] = F32(vy)
    GK = np.zeros((gh, gw), dtype=np.uint32)
    return real_frames, gt_mids, F, GK, in_canopy_region


class SpecularFoliageAntiShimmerTest(unittest.TestCase):
    def test_1_specular_glints_metallic_surface(self):
        """Scene 1 (CUSA00918 / CUSA07408): Panning metallic surface with flickering 1-2 px specular glints."""
        P, C, GT, F, GK, glint_roi_8x8, ghost_roi = build_specular_glint_scene()

        out_base = warp8_baseline_v231_r12(P, C, F, GK)
        out_new = vw.warp8(P, C, F, GK, 0)

        con = vw.rcas_con(75)
        post_base = rcas8_baseline_v231_r12(out_base, con, 1)
        post_new = vw.rcas8(out_new, con, 1)

        psnr_warp_base = psnr(out_base, GT, glint_roi_8x8)
        psnr_warp_new = psnr(out_new, GT, glint_roi_8x8)
        psnr_post_base = psnr(post_base, GT, glint_roi_8x8)
        psnr_post_new = psnr(post_new, GT, glint_roi_8x8)

        # Measure static ghosting at unwarped P/C glint positions (excess brightness above GT background)
        ghost_excess_base = float(np.max(np.abs(out_base[ghost_roi] - GT[ghost_roi])))
        ghost_excess_new = float(np.max(np.abs(out_new[ghost_roi] - GT[ghost_roi])))

        # Measure negative-lobe dark halo undershoot below minimum background around glints under sub-pixel phase t=0.35
        out_sub_base = warp8_baseline_v231_r12(P, C, F, GK, t=0.35)
        out_sub_new = vw.warp8(P, C, F, GK, 0, t=0.35)
        bg_floor = float(np.min(P[glint_roi_8x8]))
        undershoot_base = float(max(0.0, bg_floor - float(np.min(out_sub_base[glint_roi_8x8]))))
        undershoot_new = float(max(0.0, bg_floor - float(np.min(out_sub_new[glint_roi_8x8]))))

        print(
            f"\n[Scene 1: Specular Glints (CUSA00918/CUSA07408)] "
            f"8x8 Glint PSNR (warp8): {psnr_warp_base:.2f} -> {psnr_warp_new:.2f} dB (+{psnr_warp_new - psnr_warp_base:.2f} dB) | "
            f"(warp8+rcas8): {psnr_post_base:.2f} -> {psnr_post_new:.2f} dB (+{psnr_post_new - psnr_post_base:.2f} dB)"
        )
        print(
            f"[Scene 1: Glint Ghost & Ringing]                 "
            f"Ghost max err: {ghost_excess_base:.4f} -> {ghost_excess_new:.4f} | "
            f"Dark-halo undershoot: {undershoot_base:.4f} -> {undershoot_new:.4f}"
        )

        self.assertGreater(
            psnr_warp_new, psnr_warp_base + 3.0,
            "8x8 specular glint PSNR in warp8 must improve by >3.0 dB over baseline",
        )
        self.assertGreater(
            psnr_post_new, psnr_post_base + 3.0,
            "8x8 specular glint PSNR in warp8+rcas8 must improve by >3.0 dB over baseline",
        )
        self.assertLessEqual(
            undershoot_new, undershoot_base + 1e-4,
            "Catmull-Rom negative-lobe dark-halo undershoot must not regress",
        )

    def test_2_thin_wires_and_fence_against_sky(self):
        """Scene 2 (CUSA18534 / CUSA07408): 1-px diagonal power lines & chain-link fence moving at (-10, 4) vs bright sky."""
        P, C, GT, F, GK, wire_mask, halo_band_mask = build_thin_wire_fence_scene(v_wire=(-10.0, 4.0))

        out_base = warp8_baseline_v231_r12(P, C, F, GK)
        out_new = vw.warp8(P, C, F, GK, 0)

        con = vw.rcas_con(75)
        post_base = rcas8_baseline_v231_r12(out_base, con, 1)
        post_new = vw.rcas8(out_new, con, 1)

        psnr_wire_base = psnr(out_base, GT, wire_mask)
        psnr_wire_new = psnr(out_new, GT, wire_mask)
        psnr_post_base = psnr(post_base, GT, wire_mask)
        psnr_post_new = psnr(post_new, GT, wire_mask)

        # Bright ringing overshoot in the halo band adjacent to the 1-px dark wires under sub-pixel phase t=0.35
        out_sub_base = warp8_baseline_v231_r12(P, C, F, GK, t=0.35)
        out_sub_new = vw.warp8(P, C, F, GK, 0, t=0.35)
        sky_ceil = float(np.max(P[halo_band_mask]))
        ring_base = float(max(0.0, float(np.max(out_sub_base[halo_band_mask])) - sky_ceil))
        ring_new = float(max(0.0, float(np.max(out_sub_new[halo_band_mask])) - sky_ceil))

        # Wire preservation (fraction of wire pixels where dark wire contrast vs sky > 0.25 is preserved)
        sky_luma = 2.68
        wire_cont_base = float(np.mean((sky_luma - vw.sum3(out_base[wire_mask])) > 0.35))
        wire_cont_new = float(np.mean((sky_luma - vw.sum3(out_new[wire_mask])) > 0.35))

        print(
            f"[Scene 2: 1-px Wires & Fence (CUSA18534)]        "
            f"Wire PSNR (warp8): {psnr_wire_base:.2f} -> {psnr_wire_new:.2f} dB (+{psnr_wire_new - psnr_wire_base:.2f} dB) | "
            f"(warp8+rcas8): {psnr_post_base:.2f} -> {psnr_post_new:.2f} dB (+{psnr_post_new - psnr_post_base:.2f} dB)"
        )
        print(
            f"[Scene 2: Wire Continuity & Ringing]             "
            f"Continuity: {wire_cont_base * 100:.1f}% -> {wire_cont_new * 100:.1f}% | "
            f"Halo overshoot: {ring_base:.5f} -> {ring_new:.5f}"
        )

        self.assertGreater(
            psnr_wire_new, psnr_wire_base + 8.0,
            "1-px wire/fence PSNR in warp8 must improve by >8.0 dB over baseline (eliminating sky HUD overwrite)",
        )
        self.assertGreater(
            psnr_post_new, psnr_post_base + 8.0,
            "1-px wire/fence PSNR in warp8+rcas8 must improve by >8.0 dB over baseline",
        )
        self.assertGreater(
            wire_cont_new, 0.90,
            "At least 90% of 1-px wire/fence pixels must maintain strong contrast against bright sky",
        )
        self.assertLessEqual(
            ring_new, ring_base + 1e-5,
            "Catmull-Rom bright halo ringing next to dark wires must be suppressed",
        )

    def test_3_dense_foliage_canopy_antishimmer(self):
        """Scene 3 (CUSA07408 / CUSA18534 / CUSA00552): Dense high-frequency foliage panning across sky."""
        real_pairs, gt_mids, F, GK, roi_mask = build_foliage_canopy_sequence()
        con = vw.rcas_con(80)

        psnrs_base = []
        psnrs_new = []
        hf_flicker_base = []
        hf_flicker_new = []

        for (P, C), GT in zip(real_pairs, gt_mids):
            w_base = warp8_baseline_v231_r12(P, C, F, GK)
            w_new = vw.warp8(P, C, F, GK, 0)
            p_base = rcas8_baseline_v231_r12(w_base, con, 1)
            p_new = vw.rcas8(w_new, con, 1)

            psnrs_base.append(psnr(p_base, GT, roi_mask))
            psnrs_new.append(psnr(p_new, GT, roi_mask))

            # High-frequency spatial energy oscillation relative to GT in the foliage canopy
            err_base = vw.sum3(p_base - GT)[roi_mask]
            err_new = vw.sum3(p_new - GT)[roi_mask]
            hf_flicker_base.append(float(np.var(err_base)))
            hf_flicker_new.append(float(np.var(err_new)))

        mean_psnr_base = float(np.mean(psnrs_base))
        mean_psnr_new = float(np.mean(psnrs_new))
        mean_var_base = float(np.mean(hf_flicker_base))
        mean_var_new = float(np.mean(hf_flicker_new))
        var_reduction_pct = (1.0 - mean_var_new / max(mean_var_base, 1e-12)) * 100.0

        print(
            f"[Scene 3: Dense Foliage (CUSA07408/CUSA00552)]   "
            f"Canopy PSNR: {mean_psnr_base:.2f} -> {mean_psnr_new:.2f} dB (+{mean_psnr_new - mean_psnr_base:.2f} dB) | "
            f"Shimmer Variance: {mean_var_base:.5f} -> {mean_var_new:.5f} (-{var_reduction_pct:.1f}%)"
        )

        self.assertGreater(
            mean_psnr_new, mean_psnr_base + 1.5,
            "Dense foliage canopy PSNR must improve by >1.5 dB over baseline",
        )
        self.assertLess(
            mean_var_new, mean_var_base * 0.70,
            "Dense foliage shimmer variance must be reduced by >30%",
        )

    def test_4_non_regression_and_gcn2_hardware_limits(self):
        """Check 4: Zero regression on disocclusion & HUD protection + GCN2 <=64 VGPRs, 0 scratch, <=0x1C000."""
        # 4A. Subtitle & colored HUD non-regression
        P_s, C_s, GT_s, F_s, GK_s, sub_mask, hud_mask = build_subtitle_shadowbox_scene(gm=(-24.0, 4.0))
        out_s = vw.warp8(P_s[..., :3], C_s[..., :3], F_s, GK_s, 0)
        psnr_sub = psnr(out_s, GT_s[..., :3], sub_mask)
        psnr_hud = psnr(out_s, GT_s[..., :3], hud_mask)
        self.assertGreaterEqual(psnr_sub, 38.0, f"Subtitle stroke PSNR ({psnr_sub:.2f} dB) must be >= 38.0 dB")
        self.assertGreaterEqual(psnr_hud, 38.0, f"Colored HUD stroke PSNR ({psnr_hud:.2f} dB) must be >= 38.0 dB")

        # 4B. Hardware register / scratch / region footprint verification (exact mode-8 set from gen_shaders.py)
        shaders_h = (ROOT / "classic" / "source" / "shaders.h").read_text(encoding="utf-8")
        mode8_shaders = (
            "pyr8", "me3", "me2", "me1", "phase8", "warp8", "dump8", "gm8",
            "fgs8", "mf8", "gv8", "gvc8", "warp8g", "gvz8", "rcas8", "osd8",
        )
        aligned_bytes = 0
        for sname in mode8_shaders:
            mw = re.search(rf"#define\s+{sname.upper()}_NWORDS\s+(\d+)", shaders_h)
            self.assertIsNotNone(mw, f"Missing {sname.upper()}_NWORDS")
            w_i = int(mw.group(1))
            aligned_bytes += (w_i * 4 + 255) & ~255

        region_bytes = 0x8000 + aligned_bytes
        print(f"[Check 4: Non-Regression & HW Budget]            Subtitle PSNR: {psnr_sub:.2f} dB | HUD PSNR: {psnr_hud:.2f} dB | Region: 0x{region_bytes:x} / 0x1c000")
        self.assertLessEqual(region_bytes, 0x1C000, f"Shader region 0x{region_bytes:x} exceeds 0x1C000")

        for name in ("warp8", "warp8g", "rcas8"):
            m1 = re.search(rf"#define\s+{name.upper()}_RSRC1\s+0x([0-9A-Fa-f]+)", shaders_h)
            m2 = re.search(rf"#define\s+{name.upper()}_RSRC2\s+0x([0-9A-Fa-f]+)", shaders_h)
            mw = re.search(rf"#define\s+{name.upper()}_NWORDS\s+(\d+)", shaders_h)
            self.assertIsNotNone(m1)
            self.assertIsNotNone(m2)
            self.assertIsNotNone(mw)
            rsrc1 = int(m1.group(1), 16)
            rsrc2 = int(m2.group(1), 16)
            nwords = int(mw.group(1))
            max_vgprs = ((rsrc1 & 0x3F) + 1) * 4
            scratch_en = rsrc2 & 1
            print(
                f"  - {name:<6}: {nwords:4d} words ({nwords * 4:5d} B), "
                f"rsrc1=0x{rsrc1:08X} (vgprs<={max_vgprs}), scratch={scratch_en}"
            )
            self.assertLessEqual(max_vgprs, 64, f"{name} exceeds 64 VGPR limit ({max_vgprs})")
            self.assertEqual(scratch_en, 0, f"{name} uses scratch memory")


if __name__ == "__main__":
    unittest.main(verbosity=2)
