"""Warp and post stages of the NumPy port of VCFG classic mode 8: warp8 (mid frame) and rcas8 (Enhance AA + RCAS).

Float32 throughout. The texture sampler is an exact bilinear filter with clamp-to-edge (the GPU quantises the
fractional part to 1/256 texel: not modelled).
"""
import numpy as np

F32 = np.float32


def bil(img, u, v):
    """bilinear sample of img (H, W, C) at unnormalised coordinates (texel i covers [i, i+1)), clamp to edge"""
    H, W = img.shape[:2]
    x = u - F32(0.5)
    y = v - F32(0.5)
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    x0 = x0.astype(np.int64)
    y0 = y0.astype(np.int64)
    xa, xb = np.clip(x0, 0, W - 1), np.clip(x0 + 1, 0, W - 1)
    ya, yb = np.clip(y0, 0, H - 1), np.clip(y0 + 1, 0, H - 1)
    top = img[ya, xa] * (1 - fx) + img[ya, xb] * fx
    bot = img[yb, xa] * (1 - fx) + img[yb, xb] * fx
    return (top * (1 - fy) + bot * fy).astype(F32)


def diff3(a, b):
    return np.abs(a - b).sum(-1)


def lerp4(a, b, t):
    return a + (b - a) * F32(t)


def sat(x):
    return np.clip(x, F32(0.0), F32(1.0))


def crw(f, c):
    f2 = f * f
    f3 = f2 * f
    w0 = (f2 * F32(2.0) - f - f3) * F32(c)
    w1 = f2 * (F32(c) - F32(3.0)) + f3 * (F32(2.0) - F32(c)) + F32(1.0)
    w2 = f * F32(c) + f2 * (F32(3.0) - F32(2.0) * F32(c)) + f3 * (F32(c) - F32(2.0))
    w3 = (f3 - f2) * F32(c)
    w12 = w1 + w2
    return w0, w12, w3, w2 / w12


def cr(img, u, v, csh):
    """Catmull-Rom style 5-tap resample (tapsq): centre tap + 4 axis taps using bilinear taps at weighted offsets"""
    ub, vb = u - F32(0.5), v - F32(0.5)
    fu, fv = np.floor(ub), np.floor(vb)
    fx, fy = ub - fu, vb - fv
    cx, cy = fu + F32(0.5), fv + F32(0.5)
    wx0, wx12, wx3, ox = crw(fx, csh)
    wy0, wy12, wy3, oy = crw(fy, csh)
    x12, y12 = cx + ox, cy + oy
    x0, x3, y0, y3 = cx - F32(1.0), cx + F32(2.0), cy - F32(1.0), cy + F32(2.0)
    taps = [(x12, y12, wx12 * wy12), (x0, y12, wx0 * wy12), (x3, y12, wx3 * wy12), (x12, y0, wx12 * wy0), (x12, y3, wx12 * wy3)]
    acc = 0
    ws = 0
    for tx, ty, k in taps:
        acc = acc + bil(img, tx, ty) * k[..., None]
        ws = ws + k
    return np.clip(acc / ws[..., None], F32(0.0), F32(1.0)).astype(F32)


W_DIM, H_DIM = 1920.0, 1080.0


def _taps(tp, tc, u, v, vx, vy, t, sp, sc, es, str_en, quad, csh=0.5, use_cr=False):
    """one candidate vector (vx, vy): returns (a2, b2, e) as @taps / @tapsq (no overlay rectangle)"""
    tc1 = F32(1.0) - F32(t)
    up, vp = u + vx * F32(t), v + vy * F32(t)
    uc, vc = u - vx * tc1, v - vy * tc1
    if quad or use_cr:
        a, b = cr(tp, up, vp, csh), cr(tc, uc, vc, csh)
    else:
        a, b = bil(tp, up, vp), bil(tc, uc, vc)
    Wd, Hd = F32(tp.shape[1]), F32(tp.shape[0])
    oobp = (up < 0) | (up > Wd) | (vp < 0) | (vp > Hd)
    oobc = (uc < 0) | (uc > Wd) | (vc < 0) | (vc > Hd)
    onlyp, onlyc = oobp & ~oobc, oobc & ~oobp
    one = onlyp | onlyc
    a2 = np.where(onlyp[..., None], b, a)
    b2 = np.where(onlyc[..., None], a, b)
    e0 = diff3(a2, b2)
    if quad:
        return a2, b2, np.where(one, F32(0.1), e0)
    eb = np.where(one, F32(0.1), e0)
    both = ~oobp & ~oobc
    fastc = (np.abs(vx) + np.abs(vy)) > 16
    st_on = str_en & both & fastc
    eac, ebp = diff3(a2, sc), diff3(b2, sp)
    ed = np.maximum(np.minimum(eac, ebp) - es - F32(0.375), F32(0.0))
    return a2, b2, eb + np.where(st_on, ed, F32(0.0))


def warp8(tp, tc, F, GK, cutn, t=0.5, hud_packed=0, flags=0, dbg=None, flags_cut_thr=0, cut_emov=0.3, wp=None):
    wp = wp or {}
    """mid frame (H, W, 3) float32 for the interpolation phase t (no overlay rectangle, no marker)"""
    tp, tc = tp[..., :3].astype(F32), tc[..., :3].astype(F32)
    H, W = tp.shape[:2]
    out = np.empty((H, W, 3), F32)
    xs = np.arange(W, dtype=F32)
    nby, nbx = F.shape[:2]
    chunk = int(wp.get("chunk", 90))
    for y0 in range(0, H, chunk):
        y1 = min(H, y0 + chunk)
        ys = np.arange(y0, y1, dtype=F32)
        X, Y = np.meshgrid(xs, ys)
        u, v = X + F32(0.5), Y + F32(0.5)
        sp, sc = tp[y0:y1], tc[y0:y1]
        es = diff3(sp, sc)
        # fractional phase advection (zero at t == 0.5)
        cbx, cby = np.minimum(X.astype(np.int64) >> 4, nbx - 1), np.minimum(Y.astype(np.int64) >> 4, nby - 1)
        dt05 = F32(t) - F32(0.5)
        shx = np.clip(dt05 * F[cby, cbx, 0], -16, 16).astype(F32)
        shy = np.clip(dt05 * F[cby, cbx, 1], -16, 16).astype(F32)
        xfs, yfs = X + shx, Y + shy
        fbx, fby = xfs * F32(0.0625) - F32(0.46875), yfs * F32(0.0625) - F32(0.46875)
        flx, fly = np.floor(fbx), np.floor(fby)
        wx, wy = fbx - flx, fby - fly
        ixr, iyr = flx.astype(np.int64), fly.astype(np.int64)
        ix0, ix1 = np.clip(ixr, 0, nbx - 1), np.clip(ixr + 1, 0, nbx - 1)
        iy0, iy1 = np.clip(iyr, 0, nby - 1), np.clip(iyr + 1, 0, nby - 1)
        f00, f10, f01, f11 = F[iy0, ix0], F[iy0, ix1], F[iy1, ix0], F[iy1, ix1]
        lsum = [np.abs(f[..., 0]) + np.abs(f[..., 1]) for f in (f00, f10, f01, f11)]
        iwx, iwy = F32(1.0) - wx, F32(1.0) - wy
        w00, w10, w01, w11 = iwx * iwy, wx * iwy, iwx * wy, wx * wy
        mx = np.maximum(np.maximum(f00, f10), np.maximum(f01, f11))
        nn = np.minimum(np.minimum(f00, f10), np.minimum(f01, f11))
        spr = np.maximum(mx[..., 0] - nn[..., 0], mx[..., 1] - nn[..., 1])
        coh = spr <= F32(wp.get("coh_thr", 1.0))
        mov = np.zeros((y1 - y0, W, 3), F32)
        emov = np.zeros((y1 - y0, W), F32)
        vmg = np.zeros((y1 - y0, W), F32)
        # ---- coherent pixels
        vbx = (f00[..., 0] * w00 + f10[..., 0] * w10) + (f01[..., 0] * w01 + f11[..., 0] * w11)
        vby = (f00[..., 1] * w00 + f10[..., 1] * w10) + (f01[..., 1] * w01 + f11[..., 1] * w11)
        zv = (vbx == 0) & (vby == 0)
        zm = lerp4(sp, sc, t)
        a2, b2, fe = _taps(tp, tc, u, v, vbx, vby, t, sp, sc, es, None, True, csh=wp.get("csh", 0.5))
        if wp.get("fast_bil") is not None:   # cloud-report idea: bilinear instead of Catmull-Rom above this vector length
            ab, bb, feb = _taps(tp, tc, u, v, vbx, vby, t, sp, sc, es, np.zeros(es.shape, bool), False)
            usebil = ((np.abs(vbx) + np.abs(vby)) > F32(wp["fast_bil"]))[..., None]
            a2, b2 = np.where(usebil, ab, a2), np.where(usebil, bb, b2)
            fe = np.where(usebil[..., 0], feb, fe)
        fm = lerp4(a2, b2, t)
        fast_mov = np.where(zv[..., None], zm, fm)
        fast_e = np.where(zv, es, fe)
        vmb = np.abs(vbx) + np.abs(vby)
        mov = np.where(coh[..., None], fast_mov, mov)
        emov = np.where(coh, fast_e, emov)
        vmg = np.where(coh, vmb, vmg)
        # ---- non-coherent pixels: per-corner vectors, edge-weighted accumulation
        slow = ~coh
        if slow.any():
            gk_s = GK[np.clip(yfs.astype(np.int64) >> 4, 0, nby - 1), np.clip(xfs.astype(np.int64) >> 4, 0, nbx - 1)]
            nsl = sum((l < 12).astype(np.int64) for l in lsum)
            str_en = (gk_s == 0) & ((flags & 2048) == 0) & (nsl >= 2) & (es < 0.1875)
            win = int(wp.get("e_win", 1))
            acc = np.zeros_like(sp)
            sw = np.zeros(es.shape, F32)
            em = None
            for fv, w in ((f00, w00), (f10, w10), (f01, w01), (f11, w11)):
                a2, b2, e = _taps(tp, tc, u, v, fv[..., 0], fv[..., 1], t, sp, sc, es, str_en, False, csh=wp.get("csh", 0.5), use_cr=bool(wp.get("slow_cr", False)))
                if win > 1:
                    e = boxf(e, win)
                if em is None:
                    em = e.copy()
                em1 = np.minimum(em, e)
                scl = np.exp2((em1 - em) * F32(wp.get("k", 9.617967)))
                x1 = np.exp2((em1 - e) * F32(wp.get("k", 9.617967)))
                om = (w + F32(wp.get("w_eps", 0.05))) * x1
                acc = acc * scl[..., None] + lerp4(a2, b2, t) * om[..., None]
                sw = sw * scl + om
                em = em1
            sm_ = acc / sw[..., None]
            mov = np.where(slow[..., None], sm_, mov)
            emov = np.where(slow, em, emov)
            vmg = np.where(slow, F32(0.0), vmg)
        # ---- merge: static / foreground logic
        ws0 = sat((F32(0.006) - es) * F32(333.33334))
        gk = GK[np.clip(yfs.astype(np.int64) >> 4, 0, nby - 1), np.clip(xfs.astype(np.int64) >> 4, 0, nbx - 1)]
        hasfg = gk >= (1 << 20)
        fgkill = hasfg & (emov < 0.25) & ((flags & 16) == 0)
        ws = np.where(fgkill, F32(0.0), ws0)
        if flags & 4096:
            ws = ws * F32(0.0)
        stat = lerp4(sp, sc, t)
        cw2 = sat((emov - F32(wp.get("cw_bias", 0.5))) * F32(wp.get("cw_slope", 2.5)))
        dmv = diff3(mov, stat)
        bg3 = (vmg > 8.0) & (es < 0.25) & ~hasfg
        bgs = sat((dmv - es - F32(0.1225)) * F32(4.0))
        cw = np.maximum(cw2, np.where(bg3, bgs, F32(0.0)))
        cw = np.where(np.bool_((flags & 8) != 0), F32(0.0), cw)
        fillc = stat
        if wp.get("vis_fill"):   # cloud-report idea: fill from the visible side (the real frame that matches the warp) instead of a blend
            dmsc, dmsp = diff3(mov, sc), diff3(mov, sp)
            fillc = np.where((dmsc < dmsp)[..., None], sc, sp)
        movc = lerp4(mov, fillc, cw[..., None])
        out0 = lerp4(movc, stat, ws[..., None])
        cut_thr = flags_cut_thr if flags_cut_thr else 3264
        if flags & 8192:                       # experiment: scene-change replacement off
            pass
        elif flags & 16384:                    # experiment: only where the two warped samples really disagree
            if cutn > cut_thr:
                cutpx = (sc if t >= 0.5 else sp)
                m = (emov > F32(cut_emov))[..., None]
                out0 = np.where(m, cutpx, out0)
        elif cutn > cut_thr:
            out0 = (sc if t >= 0.5 else sp).copy()
        fbt = wp.get("fb_emov")
        if fbt is not None:   # experiment: low-confidence pixels (the two compensated samples disagree) fall back to a real frame
            fbp = sc if wp.get("fb", "cur") == "cur" else (stat if wp.get("fb") == "stat" else (sc * F32(0.5) + sp * F32(0.5)))
            w = sat((emov - F32(fbt)) * F32(wp.get("fb_slope", 8.0)))[..., None]
            out0 = out0 * (1 - w) + fbp * w
        out[y0:y1] = out0
        if dbg is not None:
            for k, val in (("coh", coh), ("es", es), ("ws", ws), ("cw", cw), ("bg3", bg3), ("mov", mov), ("stat", stat), ("emov", emov)):
                dbg.setdefault(k, []).append(val)
    return out


# ---------------------------------------------------------------------------------------------------------------
# rcas8: Enhance AA + Edge-Gated RCAS on the mid frame
# ---------------------------------------------------------------------------------------------------------------
def rcas_con(sharp_pct):
    if sharp_pct <= 0:
        return 0.0
    sharp_pct = min(sharp_pct, 100)
    stops = F32((100 - sharp_pct) * 0.02)
    con = F32(0.25) / (F32(1.0) + stops * (F32(0.693147) + stops * F32(0.240226)))
    if sharp_pct < 25:
        con = con * F32(sharp_pct * 0.04)
    return F32(con)


def rcas8(mid, con, emode):
    """mid (H, W, 3) float32 (already quantised like the T# store). Neighbour colours come from the 8-bit LDS copy,
    luma from the full precision value."""
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
    nz = sat(np.abs(s4l * F32(0.25) - lM) / (rng5 + eps))
    w_rcas = w0 * (F32(1.0) - nz * F32(0.5))
    if emode > 0:
        mn9 = np.minimum(mn5, np.minimum(np.minimum(lNW, lNE), np.minimum(lSW, lSE)))
        mx9 = np.maximum(mx5, np.maximum(np.maximum(lNW, lNE), np.maximum(lSW, lSE)))
        rng9 = mx9 - mn9
        rng9e = rng9 + eps
        is_str = emode >= 2
        thr_abs, thr_rel = (F32(0.015625), F32(0.0625)) if is_str else (F32(0.0234375), F32(0.09375))
        ethr = np.maximum(thr_abs, mx9 * thr_rel)
        edge_ramp = sat((rng9 - ethr) * F32(32.0))
        edgeH = np.abs(lNW + lSW - lW * 2) + np.abs(lN + lS - lM * 2) * 2 + np.abs(lNE + lSE - lE * 2)
        edgeV = np.abs(lNW + lNE - lN * 2) + np.abs(lW + lE - lM * 2) * 2 + np.abs(lSW + lSE - lS * 2)
        horz = edgeH >= edgeV
        dCard = np.maximum(np.abs(lN - lS), np.abs(lW - lE))
        dDiag = np.maximum(np.abs(lNE - lSW), np.abs(lNW - lSE)) * F32(0.5)
        tex_gate = sat((dCard + dDiag) / rng9e * F32(1.5))
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
        # span search: tangent step (tx, ty) along the edge, opposite row offset (nx, ny)
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
        subpx = sat(np.abs(s3x3 * F32(0.083333336) - lM) / rng9e)
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


def boxf(e, n):
    """n x n box mean of a 2-D map (edge replicated); only valid when the map covers the whole image"""
    p = n // 2
    ep = np.pad(e, p, mode='edge')
    c = np.cumsum(np.cumsum(ep, 0, dtype=np.float64), 1, dtype=np.float64)
    c = np.pad(c, ((1, 0), (1, 0)))
    H, W = e.shape
    s = c[n:n + H, n:n + W] - c[:H, n:n + W] - c[n:n + H, :W] + c[:H, :W]
    return (s / (n * n)).astype(F32)
