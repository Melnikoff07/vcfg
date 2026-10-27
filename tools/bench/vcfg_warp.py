"""Warp and post stages of the NumPy port of VCFG classic mode 8: warp8 (mid frame) and rcas8 (Enhance AA + RCAS).

Float32 throughout. The texture sampler is an exact bilinear filter with clamp-to-edge (the GPU quantises the
fractional part to 1/256 texel: not modelled).
"""
import struct

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
    return np.abs(a[..., :3] - b[..., :3]).sum(-1)


def diff3_k(a, b):
    a3, b3 = a[..., :3], b[..., :3]
    mx = np.maximum(a3, b3)
    mn = np.minimum(a3, b3)
    l_hi = mx[..., 0] + mx[..., 1] + mx[..., 2]
    l_lo = mn[..., 0] + mn[..., 1] + mn[..., 2]
    e_raw = l_hi - l_lo
    den = (F32(1.0) + np.maximum(l_hi - F32(1.95), F32(0.0)) * F32(1.25) +
           np.maximum(l_lo - F32(0.90), F32(0.0)) * F32(0.75))
    return (e_raw / den).astype(F32)


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


def cr(img, u, v, csh, return_hl=False):
    """Catmull-Rom style 5-tap resample (tapsq): centre tap + 4 axis taps using bilinear taps at weighted offsets"""
    ub, vb = u - F32(0.5), v - F32(0.5)
    fu, fv = np.floor(ub), np.floor(vb)
    fx, fy = ub - fu, vb - fv
    cx, cy = fu + F32(0.5), fv + F32(0.5)
    wx0, wx12, wx3, ox = crw(fx, csh)
    wy0, wy12, wy3, oy = crw(fy, csh)
    x12, y12 = cx + ox, cy + oy
    x0, x3, y0, y3 = cx - F32(1.0), cx + F32(2.0), cy - F32(1.0), cy + F32(2.0)
    s0 = bil(img, x12, y12)
    s1 = bil(img, x0, y12)
    s2 = bil(img, x3, y12)
    s3 = bil(img, x12, y0)
    s4 = bil(img, x12, y3)
    k0, k1, k2, k3, k4 = wx12 * wy12, wx0 * wy12, wx3 * wy12, wx12 * wy0, wx12 * wy3
    acc = (s0 * k0[..., None] + s1 * k1[..., None]) + (s2 * k2[..., None] + s3 * k3[..., None]) + s4 * k4[..., None]
    ws = (k0 + k1) + (k2 + k3) + k4
    res = acc / ws[..., None]
    mn_out = np.minimum(np.minimum(s1, s2), np.minimum(s3, s4))
    mn_all = np.minimum(mn_out, s0)
    mx_out = np.maximum(np.maximum(s1, s2), np.maximum(s3, s4))
    mx5 = np.maximum(mx_out, s0)
    lo = np.maximum(res, mn_all)
    hi = np.minimum(lo, mx5).astype(F32)
    cpos = np.asarray(csh > F32(0.0))
    out = np.where(cpos[..., None], hi, s0).astype(F32)
    if not return_hl:
        return out
    l_mx5 = sum3(mx5)
    l_mn5 = sum3(mn_all)
    l_s0 = sum3(s0)
    hl_rng0 = (l_mx5 - l_mn5) * sat((l_mx5 - F32(1.5)) * F32(2.0))
    is_wt = (l_mx5 > F32(1.80)) & ((l_mx5 - l_s0) > F32(0.15))
    hl_rng = np.where(is_wt, hl_rng0 + F32(4.0), hl_rng0)
    hl_out = np.where(cpos, hl_rng, F32(0.0)).astype(F32)
    return out, hl_out


W_DIM, H_DIM = 1920.0, 1080.0


def sum3(a):
    return a[..., 0] + a[..., 1] + a[..., 2]


def side_lerp(a2, b2, sp, sc, e0, eac, ebp, t, v_ex=0.0, w_occ=0.0, div_b=0.0, gem0=0.02, mob=None):
    ga = np.maximum(diff3(a2, sp), F32(0.0))
    gb = np.maximum(diff3(b2, sc), F32(0.0))
    da = np.minimum(eac, ga)
    db = np.minimum(ebp, gb)
    d_lu = sum3(b2 - a2)
    d_min = da - db
    d_cr = eac - ebp
    d_sf0 = ga - gb
    wsf = np.maximum(sat(F32(v_ex) * F32(0.125)), F32(1.0) - F32(w_occ))
    d_sf = d_sf0 * wsf
    adadb = np.maximum(np.abs(d_min), np.abs(F32(div_b)) * F32(3.0) * F32(w_occ))
    g_em = sat((e0 - F32(gem0)) * F32(6.0))
    s01 = d_min * F32(0.33) + d_cr * F32(0.07)
    s23 = d_sf * F32(0.14) + d_lu * F32(0.04)
    raw0 = s01 + s23
    o_sum = s01 * F32(1.625) + F32(div_b)
    raw = raw0 + F32(w_occ) * o_sum
    b_hi = F32(0.07) + F32(0.43) * F32(w_occ)
    dlt = np.clip(g_em * raw, -b_hi, b_hi)
    tab = sat(F32(t) + dlt)
    if mob is not None:   # experiment (Mob-FGSR blend rule): one side where the two warps agree, ramped
        agree = sat((F32(mob[0]) - e0) * F32(mob[1]))
        side = np.where(dlt >= 0, F32(1.0), F32(0.0))
        tab = tab + (side - tab) * agree
    return a2 + (b2 - a2) * tab[..., None], adadb


def _irf(bits):
    """float constant written in warp8.ll as the bit pattern of the equivalent double (LLVM IR hex float)"""
    return F32(struct.unpack('>d', bytes.fromhex(bits))[0])


# @mlp_cw of warp8.ll: per hidden unit (bias, weights x0..x6, output weight), in the IR's evaluation order
MLP_OUT_BIAS = _irf('3FA0F414A0000000')
MLP_UNITS = [
    ('3FAA7194A0000000', ('3FC3F44680000000', 'BFB065BB20000000', '3FCC696560000000', 'BFCD760280000000',
                          '3FCA876A80000000', '3F91AA4D20000000', '3F9C0450A0000000'), '3FC5607260000000'),
    ('3FB502A940000000', ('3FB7DEC400000000', 'BFCE7815E0000000', '3FB3174D20000000', '3FE3445960000000',
                          '3F8D88B180000000', 'BFA1481FA0000000', '3F9858A580000000'), 'BFBEEB9EA0000000'),
    ('3FB1FFCAE0000000', ('3FA57A3F20000000', 'BFBD902FC0000000', '3FBC89F040000000', '3FE3C65A20000000',
                          'BFB5D25000000000', 'BF8994F1E0000000', 'BF949280A0000000'), 'BFC311BD60000000'),
    ('3FB5F7C1A0000000', ('BFB118CFE0000000', 'BFC6C64040000000', 'BF75439B40000000', '3FE5C938E0000000',
                          'BFBD267DC0000000', 'BF8AD32C60000000', 'BF957503E0000000'), 'BFC9E87220000000'),
    ('3FA75E5680000000', ('3FB8A754A0000000', 'BFD45CA740000000', 'BFC288DB80000000', '3FE207A9E0000000',
                          '3FBB9C1E00000000', 'BF973F9A40000000', '3F725395C0000000'), 'BFB4C43480000000'),
    ('3FB94845C0000000', ('3FC2543F20000000', 'BFC28ECE00000000', '3FC7A203E0000000', '3F9AF58FC0000000',
                          '3FB81CA580000000', '3F88B067A0000000', '3F8FB664A0000000'), '3FC2CAC0E0000000'),
]
MLP_UNITS = [(_irf(b), tuple(_irf(w) for w in ws), _irf(o)) for b, ws, o in MLP_UNITS]
CW_ACT_THR = _irf('3F999999A0000000')   # 0.025: the residual is active above this motion error


def mlp_cw(x0, x1, x2, x3, x4, x5, x6):
    xs = (x0, x1, x2, x3, x4, x5, x6)
    acc = F32(0.0) + MLP_OUT_BIAS
    for b, ws, o in MLP_UNITS:
        s_ = (xs[0] * ws[0]).astype(F32) + b
        for xi, wi in zip(xs[1:], ws[1:]):
            s_ = s_ + xi * wi
        acc = acc + sat(s_) * o
    return acc


def _taps(tp, tc, u, v, vx, vy, t, sp, sc, es, str_en, quad, csh=0.5, use_cr=False, return_hl=False, return_spen=False):
    """one candidate vector (vx, vy): returns (a2, b2, e) as @taps / @tapsq (no overlay rectangle)"""
    tc1 = F32(1.0) - F32(t)
    up, vp = u + vx * F32(t), v + vy * F32(t)
    uc, vc = u - vx * tc1, v - vy * tc1
    Wd, Hd = F32(tp.shape[1]), F32(tp.shape[0])
    dp = np.maximum(np.maximum(F32(0.5) - up, up - (Wd - F32(0.5))),
                    np.maximum(F32(0.5) - vp, vp - (Hd - F32(0.5))))
    dc = np.maximum(np.maximum(F32(0.5) - uc, uc - (Wd - F32(0.5))),
                    np.maximum(F32(0.5) - vc, vc - (Hd - F32(0.5))))
    oobp = dp > F32(0.0)
    oobc = dc > F32(0.0)
    if quad or use_cr:
        csh_b = np.where(oobp | oobc, F32(0.0), F32(csh))
        a, hla = cr(tp, up, vp, csh_b, return_hl=True)
        b, hlb = cr(tc, uc, vc, csh_b, return_hl=True)
    else:
        a, b = bil(tp, up, vp), bil(tc, uc, vc)
        hla = hlb = np.zeros(u.shape, F32)
    onlyp, onlyc = oobp & ~oobc, oobc & ~oobp
    one = onlyp | onlyc
    d_one = np.where(onlyp, dp, dc)
    w_sat = sat(d_one * F32(0.125))
    w_one = np.where(one, w_sat, F32(0.0))
    wp = np.where(onlyp, w_sat, F32(0.0))
    wc = np.where(onlyc, w_sat, F32(0.0))
    a2 = a + (b - a) * wp[..., None]
    b2 = b + (a - b) * wc[..., None]
    hla2 = np.where(onlyp, hlb, hla)
    hlb2 = np.where(onlyc, hla, hlb)
    e0_r = diff3_k(a2, b2)
    if quad:
        hl_mn = np.minimum(hla2, hlb2)
        both_w = hl_mn >= F32(4.0)
        hla_c = np.where(hla2 >= F32(4.0), hla2 - F32(4.0), hla2)
        hlb_c = np.where(hlb2 >= F32(4.0), hlb2 - F32(4.0), hlb2)
        hl_mx = np.maximum(hla_c, hlb_c)
        e0 = e0_r / (F32(1.0) + hl_mx * F32(4.0))
        eb = e0 + F32(0.02) * w_one
        hl_tag = np.where(both_w, hl_mx + F32(4.0), hl_mx)
        if return_hl:
            return a2, b2, eb, hl_tag
        return a2, b2, eb
    eb = e0_r + F32(0.02) * w_one
    both = ~oobp & ~oobc
    fastc = (np.abs(vx) + np.abs(vy)) > 16
    st_on = str_en & both & fastc
    eac, ebp = diff3(a2, sc), diff3(b2, sp)
    ed = np.maximum(np.minimum(eac, ebp) - es - F32(0.375), F32(0.0))
    spen = np.where(st_on, ed, F32(0.0))
    if return_spen:
        return a2, b2, eb, spen
    return a2, b2, eb + spen


def _eval_sub_corner(raw_x0y0, raw_x1y0, raw_x0y1, raw_x1y1, qx, qy):
    """Unpack 8x8 sub-block corner (qx, qy) in {0, 1, 2, 3}^2 from the 2x2 loaded 16x16 blocks."""
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


def warp8(tp, tc, F, GK, cutn, t=0.5, hud_packed=0, flags=0, dbg=None, flags_cut_thr=0, cut_emov=0.3, wp=None, prevF=None):
    wp = wp or {}
    if prevF is None:
        prevF = wp.get("prevF")
    """mid frame (H, W, 3) float32 for the interpolation phase t (no overlay rectangle, no marker)"""
    tp, tc = tp[..., :3].astype(F32), tc[..., :3].astype(F32)
    H, W = tp.shape[:2]
    out = np.empty((H, W, 3), F32)
    xs = np.arange(W, dtype=F32)
    nby, nbx = F.shape[:2]
    # PC experiment only: the production shader uses a 16-pixel grid with 8x8 sub-block unpacking.
    block = int(wp.get("block", 16))
    if block not in (8, 16):
        raise ValueError("motion grid block must be 8 or 16")
    gny, gnx = GK.shape[:2]
    chunk = int(wp.get("chunk", 90))
    halo = int(wp.get("e_win", 1)) // 2 if wp.get("slow_select") else 0
    for core0 in range(0, H, chunk):
        core1 = min(H, core0 + chunk)
        y0, y1 = max(0, core0 - halo), min(H, core1 + halo)
        ys = np.arange(y0, y1, dtype=F32)
        X, Y = np.meshgrid(xs, ys)
        u, v = X + F32(0.5), Y + F32(0.5)
        sp, sc = tp[y0:y1], tc[y0:y1]
        es = diff3_k(sp, sc)
        # fractional phase advection (zero at t == 0.5) + second-order quadratic trajectory offset
        cbx, cby = np.minimum(X.astype(np.int64) // block, nbx - 1), np.minimum(Y.astype(np.int64) // block, nby - 1)
        fcx, fcy = F[cby, cbx, 0], F[cby, cbx, 1]
        vm_c = np.abs(fcx) + np.abs(fcy)
        if prevF is not None:
            up_bx = np.clip(((u + fcx) * F32(1.0 / block)).astype(np.int64), 0, nbx - 1)
            up_by = np.clip(((v + fcy) * F32(1.0 / block)).astype(np.int64), 0, nby - 1)
            fpx, fpy = prevF[up_by, up_bx, 0], prevF[up_by, up_bx, 1]
            ax_q, ay_q = fcx - fpx, fcy - fpy
            dot_v = fcx * fpx + fcy * fpy
            vm_p = np.abs(fpx) + np.abs(fpy)
            am_q = np.abs(ax_q) + np.abs(ay_q)
            vmin_cp = np.minimum(vm_c, vm_p)
            q_ok = (dot_v > F32(0.0)) & (vmin_cp > F32(2.0)) & (am_q <= F32(1.25) * vmin_cp)
            q_s = F32(-0.5) * F32(t) * (F32(1.0) - F32(t))
            dqx = np.where(q_ok, np.clip(ax_q * q_s, F32(-4.0), F32(4.0)), F32(0.0)).astype(F32)
            dqy = np.where(q_ok, np.clip(ay_q * q_s, F32(-4.0), F32(4.0)), F32(0.0)).astype(F32)
        else:
            vm_p = np.zeros_like(vm_c)
            dqx = np.zeros_like(u)
            dqy = np.zeros_like(v)
        dt05 = F32(t) - F32(0.5)
        shx = np.clip(dt05 * fcx, -16, 16).astype(F32)
        shy = np.clip(dt05 * fcy, -16, 16).astype(F32)
        xfs, yfs = X + shx + dqx, Y + shy + dqy
        u, v = u + dqx, v + dqy
        scale, centre = F32(1.0 / block), F32(0.5 - 0.5 / block)
        fbx, fby = xfs * scale - centre, yfs * scale - centre
        flx, fly = np.floor(fbx), np.floor(fby)
        wx, wy = fbx - flx, fby - fly
        ixr, iyr = flx.astype(np.int64), fly.astype(np.int64)
        # nearest 16-px block (GK and fill lookups): warp8.ll takes it from the block weights before the 8x8 unpack
        xh_r, yh_b0 = wx < F32(0.5), wy < F32(0.5)
        ix0, ix1 = np.clip(ixr, 0, nbx - 1), np.clip(ixr + 1, 0, nbx - 1)
        iy0, iy1 = np.clip(iyr, 0, nby - 1), np.clip(iyr + 1, 0, nby - 1)
        raw00, raw10, raw01, raw11 = F[iy0, ix0], F[iy0, ix1], F[iy1, ix0], F[iy1, ix1]
        if block == 16 and wp.get("sub8", True):
            any_split = ((raw00[..., 0].view(np.uint32) | raw10[..., 0].view(np.uint32) |
                          raw01[..., 0].view(np.uint32) | raw11[..., 0].view(np.uint32)) & np.uint32(0xF)) != 0
            fx8 = wx * F32(2.0) + F32(0.5)
            fy8 = wy * F32(2.0) + F32(0.5)
            sx = np.floor(fx8).astype(np.int32)
            sy = np.floor(fy8).astype(np.int32)
            wx8 = fx8 - np.floor(fx8)
            wy8 = fy8 - np.floor(fy8)
            s00, _, u00 = _eval_sub_corner(raw00, raw10, raw01, raw11, sx, sy)
            s10, _, u10 = _eval_sub_corner(raw00, raw10, raw01, raw11, sx + 1, sy)
            s01, _, u01 = _eval_sub_corner(raw00, raw10, raw01, raw11, sx, sy + 1)
            s11, _, u11 = _eval_sub_corner(raw00, raw10, raw01, raw11, sx + 1, sy + 1)
            f00 = np.where(any_split[..., None], s00, raw00)
            f10 = np.where(any_split[..., None], s10, raw10)
            f01 = np.where(any_split[..., None], s01, raw01)
            f11 = np.where(any_split[..., None], s11, raw11)
            wx = np.where(any_split, wx8, wx)
            wy = np.where(any_split, wy8, wy)
        else:
            f00, f10, f01, f11 = raw00, raw10, raw01, raw11
            any_split = np.zeros(es.shape, bool)
            u00 = u10 = u01 = u11 = np.zeros(es.shape, bool)
        lsum = [np.abs(f[..., 0]) + np.abs(f[..., 1]) for f in (f00, f10, f01, f11)]
        iwx, iwy = F32(1.0) - wx, F32(1.0) - wy
        w00, w10, w01, w11 = iwx * iwy, wx * iwy, iwx * wy, wx * wy
        mx = np.maximum(np.maximum(f00, f10), np.maximum(f01, f11))
        nn = np.minimum(np.minimum(f00, f10), np.minimum(f01, f11))
        spr = np.maximum(mx[..., 0] - nn[..., 0], mx[..., 1] - nn[..., 1])
        vm4 = np.maximum(np.abs(mx[..., 0]), np.abs(nn[..., 0])) + np.maximum(np.abs(mx[..., 1]), np.abs(nn[..., 1]))
        coh_thr = ( np.maximum(vm4 * F32(0.125), F32(2.0))
                    if "coh_thr" not in wp else F32(wp["coh_thr"]) )
        coh = spr <= coh_thr
        gbx = np.where(xh_r, ix0, ix1)
        gby = np.where(yh_b0, iy0, iy1)
        vmn = np.minimum(np.minimum(lsum[0], lsum[1]), np.minimum(lsum[2], lsum[3]))
        vsp = (mx[..., 0] - nn[..., 0]) + (mx[..., 1] - nn[..., 1])
        mov = np.zeros((y1 - y0, W, 3), F32)
        emov = np.zeros((y1 - y0, W), F32)
        vmg = np.zeros((y1 - y0, W), F32)
        hl_mov = np.zeros((y1 - y0, W), F32)
        # ---- coherent pixels
        vbx = (f00[..., 0] * w00 + f10[..., 0] * w10) + (f01[..., 0] * w01 + f11[..., 0] * w11)
        vby = (f00[..., 1] * w00 + f10[..., 1] * w10) + (f01[..., 1] * w01 + f11[..., 1] * w11)
        zv = (vbx == 0) & (vby == 0)
        zm = lerp4(sp, sc, t)
        vmb = np.abs(vbx) + np.abs(vby)
        vms = sat((vmb - F32(12.0)) * F32(0.14))
        csh_e = F32(wp.get("csh", 0.5)) * (F32(1.0) - vms)
        a2, b2, fe, hl_tag = _taps(tp, tc, u, v, vbx, vby, t, sp, sc, es, None, True, csh=csh_e, return_hl=True)
        if wp.get("fast_bil") is not None:   # cloud-report idea: bilinear instead of Catmull-Rom above this vector length
            ab, bb, feb = _taps(tp, tc, u, v, vbx, vby, t, sp, sc, es, np.zeros(es.shape, bool), False)
            usebil = ((np.abs(vbx) + np.abs(vby)) > F32(wp["fast_bil"]))[..., None]
            a2, b2 = np.where(usebil, ab, a2), np.where(usebil, bb, b2)
            fe = np.where(usebil[..., 0], feb, fe)
            hl_tag = np.where(usebil[..., 0], F32(0.0), hl_tag)
        eac_f, ebp_f = diff3_k(a2, sc), diff3_k(b2, sp)
        fm, fad = side_lerp(a2, b2, sp, sc, fe, eac_f, ebp_f, t, vmb, F32(0.0), F32(0.0), gem0=wp.get('gem0', 0.02), mob=wp.get('mob'))
        frel = np.minimum(fad * F32(0.10), fe * F32(0.60))
        fe_rel = np.maximum(fe - frel, F32(0.0))
        fast_mov = np.where(zv[..., None], zm, fm)
        fast_e = np.where(zv, es, fe_rel)
        fast_hl = np.where(zv, F32(0.0), hl_tag)
        mov = np.where(coh[..., None], fast_mov, mov)
        emov = np.where(coh, fast_e, emov)
        vmg = np.where(coh, vmb, vmg)
        hl_mov = np.where(coh, fast_hl, hl_mov)
        # ---- non-coherent pixels: per-corner vectors, edge-weighted accumulation
        slow = ~coh
        if slow.any():
            gk_s = GK[np.clip(gby, 0, gny - 1), np.clip(gbx, 0, gnx - 1)]
            nsl = sum((l < 12).astype(np.int64) for l in lsum)
            str_en = (gk_s == 0) & ((flags & 2048) == 0) & (nsl >= 2) & (es < 0.1875)
            w_occ_s = sat((vsp - F32(12.0)) * F32(0.5))
            dxq = F32(0.5) * ((f10[..., 0] - f00[..., 0]) + (f11[..., 0] - f01[..., 0]))
            dyq = F32(0.5) * ((f01[..., 1] - f00[..., 1]) + (f11[..., 1] - f10[..., 1]))
            div = dxq + dyq
            div_b_s = np.clip(div * F32(-0.09), F32(-0.55), F32(0.55))
            win = int(wp.get("e_win", 1))
            acc = np.zeros_like(sp)
            sw = np.zeros(es.shape, F32)
            em = None
            ranked = []
            for fv, w, u2 in ((f00, w00, u00), (f10, w10, u10), (f01, w01, u01), (f11, w11, u11)):
                a2, b2, eb, spen = _taps(tp, tc, u, v, fv[..., 0], fv[..., 1], t, sp, sc, es, str_en, False, csh=wp.get("csh", 0.5), use_cr=bool(wp.get("slow_cr", False)), return_spen=True)
                vmag = np.abs(fv[..., 0]) + np.abs(fv[..., 1])
                v_ex = np.maximum(vmag - vmn, F32(0.0))
                dv_g = sat(F32(1.0) - v_ex * F32(0.25))
                g_occ = w_occ_s * dv_g * sat((eb - F32(0.55)) * F32(4.0))
                div_eff = div_b_s * dv_g
                eac_s, ebp_s = diff3(a2, sc), diff3(b2, sp)
                m_s, adadb = side_lerp(a2, b2, sp, sc, eb, eac_s, ebp_s, t, v_ex, g_occ, div_eff, gem0=wp.get('gem0', 0.02), mob=wp.get('mob'))
                rk = F32(0.10) + F32(0.75) * g_occ
                rel = np.minimum(adadb * rk, eb * F32(0.82))
                e = np.maximum(eb + spen - rel, F32(0.0))
                if wp.get('slow_cr_max') is not None:
                    ab, bb, eb_b = _taps(tp, tc, u, v, fv[..., 0], fv[..., 1], t, sp, sc, es, str_en, False)
                    sharp = (np.abs(fv[..., 0]) + np.abs(fv[..., 1])) <= F32(wp['slow_cr_max'])
                    a2, b2, e = np.where(sharp[..., None], a2, ab), np.where(sharp[..., None], b2, bb), np.where(sharp, e, eb_b)
                if win > 1:
                    e = boxf(e, win)
                # warp8.ll @accum: weight (w_eff + 0.08) * exp2(8 * (emin - e)); a clean foreground sub-block (e < 0.18)
                # gets w * 4 + 0.16. Its error was also lowered by 0.08 before round 2, which pulled the mix and emov
                # towards the foreground at motion boundaries (wp={"fg_boost": True}; False = no boost at all).
                w_eff, e_eff = w, e
                fgb = wp.get("fg_boost", "w")
                if fgb:
                    is_fg_sub = any_split & u2 & (e < F32(0.18))
                    if fgb in (True, "w"):
                        w_eff = np.where(is_fg_sub, w * F32(4.0) + F32(0.16), w)
                    if fgb in (True, "e"):
                        e_eff = np.where(is_fg_sub, np.maximum(e - F32(0.08), F32(0.0)), e)
                if wp.get("acc") == "err":   # experiment: error-only weight, no distance weight
                    w_eff = None
                if dbg is not None and wp.get("dump4"):   # analysis only: per-corner colour, error, weight
                    dbg.setdefault("_c4", []).append((core0, slow, m_s, e_eff, w_eff, eb, w, e))
                if wp.get("slow_select"):
                    ranked.append((e_eff, m_s, w_eff))
                if em is None:
                    em = e_eff.copy()
                em1 = np.minimum(em, e_eff)
                # one exp2 per candidate (warp8.ll @accum): one of the two factors is exp2(0) = 1, and the other is
                # exp2(-k * |em - e|) bit for bit, since fl(a - b) == -fl(b - a) and k is a power of two
                z = np.exp2(np.abs(em - e_eff) * F32(-wp.get("k", 8.0)))
                lo = e_eff < em
                scl = np.where(lo, z, F32(1.0)).astype(F32)
                x1 = np.where(lo, F32(1.0), z).astype(F32)
                om = x1 if w_eff is None else (w_eff + F32(wp.get("w_eps", 0.08))) * x1
                acc = acc * scl[..., None] + m_s * om[..., None]
                sw = sw * scl + om
                em = em1
            sm_ = acc / sw[..., None]
            if ranked:
                # Alternative layers: choose the best two local matches rather
                # than mixing all four vectors across a motion boundary.
                costs = np.stack([r[0] + F32(wp.get("layer_prior", 0.01)) * (1 - r[2]) for r in ranked])
                order = np.argsort(costs, axis=0, kind='stable')
                colors = np.stack([r[1] for r in ranked])
                best_color = np.take_along_axis(colors, order[:1, ..., None], axis=0)[0]
                if wp['slow_select'] == 'best':
                    sm_ = best_color
                else:
                    second = np.take_along_axis(colors, order[1:2, ..., None], axis=0)[0]
                    scores = np.take_along_axis(costs, order[:2], axis=0)
                    weight = F32(1.0) / (F32(1.0) + np.exp2(np.minimum((scores[1] - scores[0]) * F32(wp.get('layer_k', 64.0)), F32(80.0))))
                    sm_ = best_color + (second - best_color) * weight[..., None]
            mov = np.where(slow[..., None], sm_, mov)
            emov = np.where(slow, em, emov)
            vmg = np.where(slow, F32(0.0), vmg)
        # ---- merge: static / foreground logic
        stat = lerp4(sp, sc, t)
        l_mov = sum3(mov)
        l_stat = sum3(stat)
        dl_s = l_stat - l_mov
        w_occ_m = sat((spr - F32(12.0)) * F32(0.5)) * sat((es - F32(0.80)) * F32(4.0)) * sat((F32(0.31) - emov) * F32(4.0))
        d_sm = stat - mov
        mx_ch = np.maximum(np.maximum(d_sm[..., 0], d_sm[..., 1]), d_sm[..., 2])
        pos_c = np.maximum(l_stat - F32(2.56), mx_ch * F32(3.0) - dl_s - F32(0.60))
        ws0_raw = sat((F32(0.006) - es) * F32(333.33334))
        mv_act = vmg > F32(2.0)
        is_wire_fol = hl_mov >= F32(4.0)
        is_glint = (hl_mov > F32(0.40)) & (dl_s < F32(-0.05))
        hl_act = mv_act & (is_wire_fol | is_glint)
        dk_spot = (mv_act & (emov < F32(0.015)) & (dl_s < F32(-0.48))) | hl_act
        ws0 = np.where(dk_spot, F32(0.0), ws0_raw)
        gk = GK[np.clip(gby, 0, gny - 1), np.clip(gbx, 0, gnx - 1)]
        hasfg = gk != 0
        hud_kill = hasfg | (mv_act & is_wire_fol)
        nfgk = ~hud_kill
        w_hv = sat((vmg + spr) * F32(2.0))
        w_he = sat((F32(0.08) - es) * F32(32.0))
        w_hc = sat(pos_c * F32(64.0))
        w_nd = sat((dl_s - F32(0.05)) * F32(25.0))
        w_hud = np.where(nfgk, w_hv * w_he * w_hc * w_nd, F32(0.0))
        vmg_e = np.maximum(vm_c, F32(0.94000000) * vm_p)
        w_vel = sat((vmg_e - F32(4.0)) * F32(0.16666667))
        w_mov = np.where(hasfg, F32(1.0), w_vel)
        mov_obj = hasfg | (vmg > F32(8.0))
        mfit_ok = (emov < 0.25) & ((flags & 16) == 0)
        w_fgk = np.where(mfit_ok, w_mov, F32(0.0))
        ws_keep = F32(1.0) - w_fgk
        ws0_s = ws0 * ws_keep
        ws = np.maximum(ws0_s, w_hud)
        if flags & 4096:
            ws = ws * F32(0.0)
        dmp = diff3_k(mov, sp)
        dmc = diff3_k(mov, sc)
        dmv = diff3_k(mov, stat)
        ddm = dmp - dmc
        k_ddm = F32(wp.get("k_ddm0", 0.45)) + F32(wp.get("k_ddm1", 0.45)) * w_occ_m
        lim_hi = F32(wp.get("lim0", 0.30)) + F32(wp.get("lim1", 0.20)) * w_occ_m
        dtsc = np.clip(ddm * k_ddm, -lim_hi, lim_hi)
        tsc = sat(F32(t) + dtsc)
        fillc = sp + (sc - sp) * tsc[..., None]
        cw2 = sat((emov - F32(wp.get("cw_bias", 0.5))) * F32(wp.get("cw_slope", 2.5))) * sat((F32(0.80) - es) * F32(4.0))
        bg3 = (vmg > 8.0) & (es < 0.25) & ~mov_obj & (emov > 0.25)
        bgs = sat((dmv - es - F32(0.22)) * F32(4.0))
        bgw = np.where(bg3, bgs, F32(0.0))
        dsw = sat((dl_s - F32(0.18)) * F32(2.5)) * sat((emov - F32(0.22)) * F32(5.0)) * sat((F32(0.30) - es) * F32(4.0))
        cw_base = np.maximum(np.maximum(cw2, bgw), dsw)
        res_cw = mlp_cw(ddm, emov, es, dmv, dl_s, spr, vmg)
        dcw = sat((emov - CW_ACT_THR) * F32(10.0)) * res_cw
        cw3_0 = sat(cw_base + dcw)
        gcw = sat((emov - es) * F32(4.0))
        cw3_1 = cw3_0 * gcw
        # NOTE: warp8.ll computes this temporal damping (%tc_dmp) but does not apply it; wp={"tc_dmp": False} gives
        # the shader behaviour (round 2: |dPSNR| <= 0.00004 dB per frame on the corpus either way)
        if prevF is not None and wp.get("tc_dmp", True):
            gbx_m, gby_m = gbx, gby
            fg_cx, fg_cy = F[gby_m, gbx_m, 0], F[gby_m, gbx_m, 1]
            fg_bx = np.clip(gbx_m + (fg_cx * F32(1.0 / block)).astype(np.int64), 0, nbx - 1)
            fg_by = np.clip(gby_m + (fg_cy * F32(1.0 / block)).astype(np.int64), 0, nby - 1)
            fg_px, fg_py = prevF[fg_by, fg_bx, 0], prevF[fg_by, fg_bx, 1]
            tc_err = np.abs(fg_cx - fg_px) + np.abs(fg_cy - fg_py)
            tc_pm = np.abs(fg_px) + np.abs(fg_py)
            if wp.get("tc_src") == "q":   # experiment: reuse the trajectory sample of the quadratic offset
                tc_err, tc_pm = am_q, vm_p
            tc_w = sat(tc_pm - F32(2.0)) * sat((F32(2.0) - tc_err) * F32(0.5)) * sat((F32(0.35) - emov) * F32(4.0))
            cw3_1 = cw3_1 * (F32(1.0) - F32(wp.get("tc_k", 0.30)) * tc_w)
        cw3_s = cw3_1 * ws_keep
        cw3 = np.where(hl_act, F32(0.0), cw3_s)
        cw = np.where(np.bool_((flags & 8) != 0), F32(0.0), cw3)
        # warp8.ll runs the fill block only for waves with a lane where cw can be nonzero: cw is exactly 0 when
        # emov <= 0.025 (residual and base terms off), emov <= es (gcw = 0), on hl_act, under the full foreground
        # kill (ws_keep = 0) or the no-fill flag
        cw_live = (emov > CW_ACT_THR) & (emov > es) & ~hl_act & (ws_keep > F32(0.0)) & np.bool_((flags & 8) == 0)
        cw_pre = cw
        cw = np.where(cw_live, cw, F32(0.0))
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
        elif wp.get("cut_rule") is not None:   # experiment: compare the scene-change rules on one grid scale
            rule = wp["cut_rule"]
            frac = float(nby * nbx) / F32(8160.0)
            thr = (wp.get("cut_thr_full", 8160 if rule == "none" else (5600 if rule == "shader" else 3264))) * frac
            if rule != "none" and cutn > thr:
                cutpx = (sc if t >= 0.5 else sp)
                if rule == "shader":
                    m = ((emov > F32(0.35)) & (es > F32(0.30)))[..., None]
                    out0 = np.where(m, cutpx, out0)
                else:
                    out0 = cutpx.copy()
        elif wp.get("cut_full"):              # experiment: the pre-2.x rule, whole frame replaced above 3264 blocks
            if cutn > (flags_cut_thr if flags_cut_thr else 3264):
                out0 = (sc if t >= 0.5 else sp).copy()
        elif cutn > (flags_cut_thr if flags_cut_thr else 5600):
            # warp8.ll: above the threshold (default 5600 of 8160 blocks), only pixels where both the motion and the
            # static samples disagree take the real frame
            m = ((emov > F32(0.35)) & (es > F32(0.30)))[..., None]
            out0 = np.where(m, sc if t >= 0.5 else sp, out0)
        fbt = wp.get("fb_emov")
        if fbt is not None:   # experiment: low-confidence pixels (the two compensated samples disagree) fall back to a real frame
            fbp = sc if wp.get("fb", "cur") == "cur" else (stat if wp.get("fb") == "stat" else (sc * F32(0.5) + sp * F32(0.5)))
            w = sat((emov - F32(fbt)) * F32(wp.get("fb_slope", 8.0)))[..., None]
            out0 = out0 * (1 - w) + fbp * w
        crop = slice(core0 - y0, core1 - y0)
        out[core0:core1] = out0[crop]
        if dbg is not None:
            for k, val in (("coh", coh), ("es", es), ("ws", ws), ("cw", cw), ("cw_pre", cw_pre), ("cw_live", cw_live), ("bg3", bg3), ("mov", mov), ("stat", stat), ("emov", emov)):
                dbg.setdefault(k, []).append(val[crop])
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


RCP255 = F32(1.0) / F32(255.0)   # 0x3F70101020000000 in rcas8.ll unpack_rgb


def rcas_weight(lM, lN, lS, lW, lE, mn5, mx5, rng5, tex_gate, s4l, con):
    """RCAS adaptive lobe weight of rcas8 (block rcas_w of rcas8.ll; only run when con != 0)"""
    eps = F32(2.0 ** -14)
    hmin = mn5 / ((mx5 + eps) * F32(4.0))
    hmax = (F32(1.0) - mx5) / ((mn5 - F32(1.0)) * F32(4.0) - eps)
    lobe = np.maximum(np.maximum(-hmin, hmax), -F32(con))
    w_karis = F32(1.0) / (F32(1.0) + mx5)
    w0 = np.minimum(lobe, F32(0.0)) * w_karis
    nz = sat(np.abs(s4l * F32(0.25) - lM) / (rng5 + eps))
    dn = sat(F32(1.0) - nz * F32(0.85)) * tex_gate
    w_sharp = w0 * dn
    w_shim = (F32(1.0) - tex_gate) * sat((nz - F32(0.45)) * F32(2.0)) * F32(0.10) * w_karis * sat(F32(con) * F32(8.0))
    return w_sharp + w_shim


def static_px(tp, tc):
    """warp8's static-pixel flag, written to the mid's alpha (1.0 / 254/255): P == C in RGB at the pixel (es == 0)"""
    return diff3_k(tp.astype(F32), tc.astype(F32)) == F32(0.0)


def rcas8_real(cur, prev, con, emode, dbg=None):
    """rcas8 cur pass with rcas flag bit 5 (real frame post-processed in place, the next pair's P): no AA where warp8
    flagged the pixel static (static_px(prev, cur), carried over from the mid pass of the same workgroup), and the
    lane is written back raw (store skipped) where its AA result already equals prev's stored code: the pixel is
    static but P holds last frame's AA of it, so it would never compare static again. Returns the stored frame."""
    cur = cur.astype(F32)
    st = static_px(prev, cur)
    out = rcas8(cur, con, emode, skip=st, dbg=dbg)
    q = lambda x: np.rint(np.clip(x, 0, 1) * F32(255.0))
    same = (q(out) == q(prev.astype(F32))).all(-1)
    return np.where(same[..., None], cur, out).astype(F32)


def rcas8(mid, con, emode, force_apply=False, skip=None, dbg=None, still=None):
    """mid (H, W, 3) float32 (already quantised like the T# store). Neighbour colours come from the 8-bit LDS copy,
    luma from the full precision value. con == 0 (Sharpness 0) skips the RCAS weight and the RCAS apply, as rcas8.ll
    does: the weight is +-0 there, so the apply returns base_c bit for bit. force_apply runs the full apply anyway
    (tests prove the identity with it). skip (H, W) bool: warp8's static flag (rcas flag bit 4, mid pass); those
    pixels take the rcas_only path (no AA, RCAS as with Enhance off). Lanes with edge_ramp == 0 take that path in
    rcas8.ll too, which is the identity here (wAA == 0, w_eff == w_rcas). dbg: dict, receives edge_ramp.
    still (H, W) bool: warp8's static flag on the mid pass of rcas8.ll (flag bit 4): the real frames around the
    mid show those pixels raw, so the mid keeps them raw too (no AA and no sharpening: out = the 8-bit texel);
    sharpening only the mid there alternated at 30 Hz like the AA did. A wave whose 64 lanes are all still skips
    the whole pass after the tile load."""
    H, W = mid.shape[:2]
    c = mid.astype(F32)
    luma = c[..., 1] * F32(0.5) + (c[..., 0] + c[..., 2]) * F32(0.25)
    # LDS unpack of rcas8.ll: v_cvt_f32_ubyte * 1/255 (float constant), not a division (126 of 256 codes differ by 1 ulp)
    c8 = np.floor(np.clip(c, 0, 1) * F32(255.0) + F32(0.5)) * RCP255
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
    mn9 = np.minimum(mn5, np.minimum(np.minimum(lNW, lNE), np.minimum(lSW, lSE)))
    mx9 = np.maximum(mx5, np.maximum(np.maximum(lNW, lNE), np.maximum(lSW, lSE)))
    rng9 = mx9 - mn9
    rng9e = rng9 + eps
    dNS, dWE = np.abs(lN - lS), np.abs(lW - lE)
    dCard = np.maximum(dNS, dWE)
    dDiag = np.maximum(np.abs(lNE - lSW), np.abs(lNW - lSE)) * F32(0.5)
    tex_gate = sat((dCard + dDiag) / rng9e * F32(1.5))
    sharp = F32(con) != F32(0.0)
    s4l = (lN + lW) + (lE + lS)
    w_rcas = rcas_weight(lM, lN, lS, lW, lE, mn5, mx5, rng5, tex_gate, s4l, con) if sharp else np.zeros_like(lM)
    if emode > 0:
        is_str = emode >= 2
        thr_abs, thr_rel = (F32(0.015625), F32(0.0625)) if is_str else (F32(0.0234375), F32(0.09375))
        ethr = np.maximum(thr_abs, mx9 * thr_rel)
        edge_ramp = sat((rng9 - ethr) * F32(32.0))
        if dbg is not None:
            dbg['edge_ramp'] = edge_ramp
        edgeH = np.abs(lNW + lSW - lW * 2) + np.abs(lN + lS - lM * 2) * 2 + np.abs(lNE + lSE - lE * 2)
        edgeV = np.abs(lNW + lNE - lN * 2) + np.abs(lW + lE - lM * 2) * 2 + np.abs(lSW + lSE - lS * 2)
        horz = edgeH >= edgeV
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
        gMin = np.minimum(gNeg, gPos)
        r_gate = F32(1.0) - F32(0.85) * (gMin / (gMax + eps))
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
        wAA = edge_ramp * wClp * r_gate
        base_c = M + (rgbTarget - M) * wAA[..., None]
        w_eff = w_rcas * (F32(1.0) - edge_ramp)
        if skip is not None:
            base_c = np.where(skip[..., None], M, base_c)
            w_eff = np.where(skip, w_rcas, w_eff)
    else:
        base_c, w_eff = M, w_rcas
    if still is not None:
        base_c = np.where(still[..., None], M, base_c)
    if not sharp and not force_apply:
        return base_c.astype(F32)
    a_ns0 = (dNS * F32(2.0)) / (dNS + dWE + eps)
    a_we0 = F32(2.0) - a_ns0
    is_sh = w_eff < F32(0.0)
    a_ns = np.where(is_sh, a_ns0, F32(1.0))
    a_we = np.where(is_sh, a_we0, F32(1.0))
    v4 = (N + S) * a_ns[..., None] + (Wc + E) * a_we[..., None]
    num = v4 * w_eff[..., None] + base_c
    res0 = num * (F32(1.0) / (F32(1.0) + w_eff * F32(4.0)))[..., None]
    c_mn = np.minimum(np.minimum(np.minimum(N, Wc), np.minimum(E, S)), base_c)
    c_mx = np.maximum(np.maximum(np.maximum(N, Wc), np.maximum(E, S)), base_c)
    out = np.minimum(np.maximum(res0, c_mn), c_mx).astype(F32)
    return out if still is None else np.where(still[..., None], M, out).astype(F32)


def boxf(e, n):
    """n x n box mean of a 2-D map (edge replicated); only valid when the map covers the whole image"""
    p = n // 2
    ep = np.pad(e, p, mode='edge')
    c = np.cumsum(np.cumsum(ep, 0, dtype=np.float64), 1, dtype=np.float64)
    c = np.pad(c, ((1, 0), (1, 0)))
    H, W = e.shape
    s = c[n:n + H, n:n + W] - c[:H, n:n + W] - c[n:n + H, :W] + c[:H, :W]
    return (s / (n * n)).astype(F32)
