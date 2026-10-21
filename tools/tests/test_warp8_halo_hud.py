"""Offline CPU reference and PSNR/edge-sharpness/dark-spot verification for warp8.ll.

Tests the two warp8 improvements against baseline warp8 math on synthetic frame triplets:
  1. Asymmetric Disocclusion / Silhouette Halo Reduction (stationary & panning background)
  2. Inline Static HUD / Crosshair / Subtitle Protection (1-3px white & colored strokes)
  3. Moving-Texture Dark-Spot Regression Guard (periodic moving texture with |P(x)-C(x)| ~ 0)
"""
import math
import pathlib
import sys
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]


def sat(x):
    return np.clip(x, 0.0, 1.0)


def diff3(a, b):
    return np.sum(np.abs(a[..., :3] - b[..., :3]), axis=-1)


def sum3(a):
    return np.sum(a[..., :3], axis=-1)


def lerp4(a, b, t):
    return a + (b - a) * t[..., None]


def sample_bilinear(img, u, v):
    """Unnormalized bilinear sampler with clamp-to-edge (matching GCN S#)."""
    h, w, c = img.shape
    x = np.clip(u - 0.5, 0.0, w - 1.0)
    y = np.clip(v - 0.5, 0.0, h - 1.0)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    c00 = img[y0, x0]
    c10 = img[y0, x1]
    c01 = img[y1, x0]
    c11 = img[y1, x1]
    return (c00 * (1.0 - fx) * (1.0 - fy) +
            c10 * fx * (1.0 - fy) +
            c01 * (1.0 - fx) * fy +
            c11 * fx * fy)


def mlp_cw(x0, x1, x2, x3, x4, x5, x6):
    """Exact 6-unit ALU Micro-MLP @mlp_cw from warp8.ll."""
    weights = [
        (float.fromhex('0x3FAA7194A0000000'), [
            float.fromhex('0x3FC3F44680000000'), float.fromhex('0xBFB065BB20000000'),
            float.fromhex('0x3FCC696560000000'), float.fromhex('0xBFCD760280000000'),
            float.fromhex('0x3FCA876A80000000'), float.fromhex('0x3F91AA4D20000000'),
            float.fromhex('0x3F9C0450A0000000'),
        ], float.fromhex('0x3FC5607260000000')),
        (float.fromhex('0x3FB502A940000000'), [
            float.fromhex('0x3FB7DEC400000000'), float.fromhex('0xBFCE7815E0000000'),
            float.fromhex('0x3FB3174D20000000'), float.fromhex('0x3FE3445960000000'),
            float.fromhex('0x3F8D88B180000000'), float.fromhex('0xBFA1481FA0000000'),
            float.fromhex('0x3F9858A580000000'),
        ], float.fromhex('0xBFBEEB9EA0000000')),
        (float.fromhex('0x3FB1FFCAE0000000'), [
            float.fromhex('0x3FA57A3F20000000'), float.fromhex('0xBFBD902FC0000000'),
            float.fromhex('0x3FBC89F040000000'), float.fromhex('0x3FE3C65A20000000'),
            float.fromhex('0xBFB5D25000000000'), float.fromhex('0xBF8994F1E0000000'),
            float.fromhex('0xBF949280A0000000'),
        ], float.fromhex('0xBFC311BD60000000')),
        (float.fromhex('0x3FB5F7C1A0000000'), [
            float.fromhex('0xBFB118CFE0000000'), float.fromhex('0xBFC6C64040000000'),
            float.fromhex('0xBF75439B40000000'), float.fromhex('0x3FE5C938E0000000'),
            float.fromhex('0xBFBD267DC0000000'), float.fromhex('0xBF8AD32C60000000'),
            float.fromhex('0xBF957503E0000000'),
        ], float.fromhex('0xBFC9E87220000000')),
        (float.fromhex('0x3FA75E5680000000'), [
            float.fromhex('0x3FB8A754A0000000'), float.fromhex('0xBFD45CA740000000'),
            float.fromhex('0xBFC288DB80000000'), float.fromhex('0x3FE207A9E0000000'),
            float.fromhex('0x3FBB9C1E00000000'), float.fromhex('0xBF973F9A40000000'),
            float.fromhex('0x3F725395C0000000'),
        ], float.fromhex('0xBFB4C43480000000')),
        (float.fromhex('0x3FB94845C0000000'), [
            float.fromhex('0x3FC2543F20000000'), float.fromhex('0xBFC28ECE00000000'),
            float.fromhex('0x3FC7A203E0000000'), float.fromhex('0x3F9AF58FC0000000'),
            float.fromhex('0x3FB81CA580000000'), float.fromhex('0x3F88B067A0000000'),
            float.fromhex('0x3F8FB664A0000000'),
        ], float.fromhex('0x3FC2CAC0E0000000')),
    ]
    xs = [x0, x1, x2, x3, x4, x5, x6]
    acc = np.full_like(x0, float.fromhex('0x3FA0F414A0000000'))
    for b, w, out_w in weights:
        s = b + sum(wi * xi for wi, xi in zip(w, xs))
        acc = acc + sat(s) * out_w
    return acc


def side_lerp_ref(a2, b2, sp, sc, e0, eac, ebp, t, vmag, w_occ, div_b, mode):
    ga = np.maximum(diff3(a2, sp), 0.0)
    gb = np.maximum(diff3(b2, sc), 0.0)
    da = np.minimum(eac, ga)
    db = np.minimum(ebp, gb)
    d_lu = sum3(b2 - a2)
    d_min = da - db
    d_cr = eac - ebp
    d_sf0 = ga - gb
    if mode == 'new':
        wsf = np.maximum(sat(vmag * 0.125), 1.0 - w_occ)
        d_sf = d_sf0 * wsf
        adadb = np.maximum(np.abs(d_min), np.abs(div_b) * w_occ)
    else:
        d_sf = d_sf0
        adadb = np.abs(d_min)

    g_em = sat((e0 - 0.08) * 6.0)
    s01 = d_min * 0.33 + d_cr * 0.07
    s23 = d_sf * 0.14 + d_lu * 0.04
    raw0 = s01 + s23
    if mode == 'new':
        o_sum = s01 * 1.625 + div_b
        raw = raw0 + w_occ * o_sum
        b_hi = 0.07 + 0.43 * w_occ
    else:
        raw = raw0
        b_hi = 0.07

    dlt = np.clip(g_em * raw, -b_hi, b_hi)
    tab = sat(t + dlt)
    m = lerp4(a2, b2, tab)
    return m, adadb


def taps_ref(P, C, u, v, vx, vy, t, sp, sc, es, str_en, w_occ, div_b, mode):
    tc1 = 1.0 - t
    up = u + vx * t
    vp = v + vy * t
    uc = u - vx * tc1
    vc = v - vy * tc1
    a2 = sample_bilinear(P, up, vp)
    b2 = sample_bilinear(C, uc, vc)
    eb = diff3(a2, b2)
    vmag = np.abs(vx) + np.abs(vy)
    st_on = str_en & (vmag > 16.0)
    eac = diff3(a2, sc)
    ebp = diff3(b2, sp)
    emx = np.minimum(eac, ebp)
    spen = np.where(st_on, np.maximum(emx - es - 0.375, 0.0), 0.0)
    if mode == 'new':
        g_occ = w_occ * sat((eb - 0.55) * 4.0)
        div_eff = div_b * sat(1.0 - vmag * 0.25)
    else:
        g_occ = w_occ
        div_eff = div_b
    m, adadb = side_lerp_ref(a2, b2, sp, sc, eb, eac, ebp, t, vmag, g_occ, div_eff, mode)
    if mode == 'new':
        rk = 0.10 + 0.48 * g_occ
    else:
        rk = 0.10
    rel = np.minimum(adadb * rk, eb * 0.60)
    e = np.maximum(eb + spen - rel, 0.0)
    return m, e


def run_warp8_ref(P, C, F, GK=None, t=0.5, mode='new'):
    """Vectorized reference implementation of warp8.ll ('base' vs 'new')."""
    h, w, _ = P.shape
    gh, gw, _ = F.shape
    if GK is None:
        GK = np.zeros((gh, gw), dtype=np.uint32)

    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing='ij')
    u = xx + 0.5
    v = yy + 0.5
    xf1080 = u - 0.5
    yf1080 = v - 0.5

    cbx = np.clip((u * 0.0625).astype(np.int32), 0, gw - 1)
    cby = np.clip((v * 0.0625).astype(np.int32), 0, gh - 1)
    fc = F[cby, cbx]
    shx = np.clip((t - 0.5) * fc[..., 0], -16.0, 16.0)
    shy = np.clip((t - 0.5) * fc[..., 1], -16.0, 16.0)
    xfs = xf1080 + shx
    yfs = yf1080 + shy

    fbx = xfs * 0.0625 - 0.46875
    fby = yfs * 0.0625 - 0.46875
    flx = np.floor(fbx)
    fly = np.floor(fby)
    wx = fbx - flx
    wy = fby - fly
    ixr = flx.astype(np.int32)
    iyr = fly.astype(np.int32)
    ix0 = np.clip(ixr, 0, gw - 1)
    ix1 = np.clip(ixr + 1, 0, gw - 1)
    iy0 = np.clip(iyr, 0, gh - 1)
    iy1 = np.clip(iyr + 1, 0, gh - 1)

    xh_r = wx < 0.5
    yh_b0 = wy < 0.5
    gbx = np.where(xh_r, ix0, ix1)
    gby = np.where(yh_b0, iy0, iy1)
    ix2 = np.clip(gbx + np.where(xh_r, -1, 1), 0, gw - 1)
    iy2 = np.clip(gby + np.where(yh_b0, -1, 1), 0, gh - 1)

    f00 = F[iy0, ix0]
    f10 = F[iy0, ix1]
    f01 = F[iy1, ix0]
    f11 = F[iy1, ix1]
    f20 = F[gby, ix2]
    f02 = F[iy2, gbx]

    x00, y00 = f00[..., 0], f00[..., 1]
    x10, y10 = f10[..., 0], f10[..., 1]
    x01, y01 = f01[..., 0], f01[..., 1]
    x11, y11 = f11[..., 0], f11[..., 1]
    x20, y20 = f20[..., 0], f20[..., 1]
    x02, y02 = f02[..., 0], f02[..., 1]

    l00 = np.abs(x00) + np.abs(y00)
    l10 = np.abs(x10) + np.abs(y10)
    l01 = np.abs(x01) + np.abs(y01)
    l11 = np.abs(x11) + np.abs(y11)
    l20 = np.abs(x20) + np.abs(y20)
    l02 = np.abs(x02) + np.abs(y02)

    vmx4 = np.maximum(np.maximum(l00, l10), np.maximum(l01, l11))
    vmn4 = np.minimum(np.minimum(l00, l10), np.minimum(l01, l11))
    if mode == 'new':
        vmx = np.maximum(vmx4, np.maximum(l20, l02))
        vmn = np.minimum(vmn4, np.minimum(l20, l02))
        vsp = vmx - vmn
        w_occ_s = sat((vsp - 12.0) * 0.5)
        dxq = 0.5 * ((x10 - x00) + (x11 - x01))
        dx2 = np.where(xh_r, x00 - x20, x20 - x10)
        div_x = dxq + dx2
        dyq = 0.5 * ((y01 - y00) + (y11 - y10))
        dy2 = np.where(yh_b0, y00 - y02, y02 - y01)
        div_y = dyq + dy2
        div = div_x + div_y
        div_b_s = np.clip(div * -0.035, -0.35, 0.35)
    else:
        vmx = vmx4
        w_occ_s = np.zeros_like(vmx)
        div_b_s = np.zeros_like(vmx)

    iwx = 1.0 - wx
    iwy = 1.0 - wy
    w00 = iwx * iwy
    w10 = wx * iwy
    w01 = iwx * wy
    w11 = wx * wy

    mx = np.maximum(np.maximum(x00, x10), np.maximum(x01, x11))
    nx = np.minimum(np.minimum(x00, x10), np.minimum(x01, x11))
    my = np.maximum(np.maximum(y00, y10), np.maximum(y01, y11))
    ny = np.minimum(np.minimum(y00, y10), np.minimum(y01, y11))
    spr = np.maximum(mx - nx, my - ny)
    if mode == 'new':
        vm4 = np.maximum(np.abs(mx), np.abs(nx)) + np.maximum(np.abs(my), np.abs(ny))
        coh_thr = np.maximum(vm4 * 0.125, 2.0)
        coh = spr <= coh_thr
    else:
        coh = spr <= 1.0

    sp = P.copy()
    sc = C.copy()
    es = diff3(sp, sc)

    # Fast path
    vbx = x00 * w00 + x10 * w10 + x01 * w01 + x11 * w11
    vby = y00 * w00 + y10 * w10 + y01 * w01 + y11 * w11
    vmb = np.abs(vbx) + np.abs(vby)
    fa = sample_bilinear(P, u + vbx * t, v + vby * t)
    fb = sample_bilinear(C, u - vbx * (1.0 - t), v - vby * (1.0 - t))
    fe = diff3(fa, fb)
    eac_f = diff3(fa, sc)
    ebp_f = diff3(fb, sp)
    fm, fad = side_lerp_ref(fa, fb, sp, sc, fe, eac_f, ebp_f, t, vmb,
                            np.zeros_like(vmb), np.zeros_like(vmb), mode)
    frel = np.minimum(fad * 0.10, fe * 0.60)
    fe_rel = np.maximum(fe - frel, 0.0)
    zv = (vbx == 0.0) & (vby == 0.0)
    zm = lerp4(sp, sc, np.full_like(es, t))
    fast_mov = np.where(zv[..., None], zm, fm)
    fast_emov = np.where(zv, es, fe_rel)

    # Slow path
    gk = GK[gby, gbx]
    nofgs = (gk == 0)
    nslow = (l00 < 12.0).astype(np.int32) + (l10 < 12.0).astype(np.int32) + (l01 < 12.0).astype(np.int32) + (l11 < 12.0).astype(np.int32)
    str_en = nofgs & (nslow >= 2) & (es < 0.1875)

    c0_m, c0_e = taps_ref(P, C, u, v, x00, y00, t, sp, sc, es, str_en, w_occ_s, div_b_s, mode)
    c1_m, c1_e = taps_ref(P, C, u, v, x10, y10, t, sp, sc, es, str_en, w_occ_s, div_b_s, mode)
    c2_m, c2_e = taps_ref(P, C, u, v, x01, y01, t, sp, sc, es, str_en, w_occ_s, div_b_s, mode)
    c3_m, c3_e = taps_ref(P, C, u, v, x11, y11, t, sp, sc, es, str_en, w_occ_s, div_b_s, mode)

    acc = np.zeros_like(P)
    sw = np.zeros_like(es)
    em = c0_e.copy()
    for (cm, ce), cw_i in [((c0_m, c0_e), w00), ((c1_m, c1_e), w10), ((c2_m, c2_e), w01), ((c3_m, c3_e), w11)]:
        em1 = np.minimum(em, ce)
        sc_w = np.exp2((em1 - em) * 5.95)
        x1 = np.exp2((em1 - ce) * 5.95)
        om = (cw_i + 0.26) * x1
        acc = acc * sc_w[..., None] + cm * om[..., None]
        sw = sw * sc_w + om
        em = em1
    sm_ = acc / sw[..., None]

    mov = np.where(coh[..., None], fast_mov, sm_)
    emov = np.where(coh, fast_emov, em)
    vmg = np.where(coh, vmb, np.zeros_like(vmb))

    stat = lerp4(sp, sc, np.full_like(es, t))
    l_mov = sum3(mov)
    l_stat = sum3(stat)
    dl_s = l_stat - l_mov

    fl1 = (0.006 - es) * 333.33334
    ws0_raw = sat(fl1)
    hasfg = gk >= 1048576
    nfgk = ~hasfg

    if mode == 'new':
        w_occ_m = sat((spr - 12.0) * 0.5) * sat((es - 0.80) * 4.0) * sat((0.31 - emov) * 4.0)
        d_sm = stat - mov
        mx_ch = np.maximum(np.maximum(d_sm[..., 0], d_sm[..., 1]), d_sm[..., 2])
        pos_c = np.maximum(l_stat - 2.60, mx_ch * 3.0 - dl_s - 0.60)
        dk_spot = (vmg > 2.0) & (emov < 0.22) & (dl_s < -0.04)
        ws0 = np.where(dk_spot, 0.0, ws0_raw)
        w_hv = sat((vmg - 6.0) * 0.25)
        w_he = sat((0.05 - es) * 50.0)
        w_hc = sat(pos_c * 16.0)
        w_nd = sat((dl_s - 0.05) * 25.0)
        w_hud = np.where(nfgk, w_hv * w_he * w_hc * w_nd, 0.0)
        ws0_h = np.maximum(ws0, w_hud)
    else:
        w_occ_m = np.zeros_like(spr)
        pos_c = dl_s
        ws0_h = ws0_raw

    fgkill = hasfg & (emov < 0.25)
    ws = np.where(fgkill, 0.0, ws0_h)

    dmp = diff3(mov, sp)
    dmc = diff3(mov, sc)
    dmv = diff3(mov, stat)
    ddm = dmp - dmc

    if mode == 'new':
        k_ddm = 0.45 + 0.45 * w_occ_m
        lim_hi = 0.30 + 0.20 * w_occ_m
        dtsc = np.clip(ddm * k_ddm, -lim_hi, lim_hi)
    else:
        dtsc = np.clip(ddm * 0.45, -0.30, 0.30)

    tsc = sat(t + dtsc)
    fillc = lerp4(sp, sc, tsc)

    cw2 = sat((emov - 0.5) * 2.5) * sat((0.8 - es) * 4.0)
    bg2x = (vmg > 8.0) & (es < 0.25) & nfgk
    bg3 = bg2x & (emov > 0.25)
    bgs = sat((dmv - es - 0.22) * 4.0)
    bgw = np.where(bg3, bgs, 0.0)
    dsa = sat((dl_s - 0.18) * 2.5)
    dsb = sat((emov - 0.22) * 5.0)
    dsc = sat((0.30 - es) * 4.0)
    dsw = dsa * dsb * dsc

    cw_base = np.maximum(np.maximum(cw2, bgw), dsw)
    res_cw = mlp_cw(ddm, emov, es, dmv, dl_s, spr, vmg)
    act = sat((emov - 0.15) * 10.0)
    cw = sat(cw_base + act * res_cw)

    movc = lerp4(mov, fillc, cw)
    out = lerp4(movc, stat, ws)
    return out


def psnr(pred, gt, mask=None):
    diff = (pred[..., :3] - gt[..., :3]) ** 2
    if mask is not None:
        mse = np.mean(diff[mask])
    else:
        mse = np.mean(diff)
    if mse <= 1e-12:
        return 99.0
    return float(10.0 * np.log10(1.0 / mse))


def edge_sharpness(img, mask=None):
    luma = sum3(img) / 3.0
    gx = np.abs(np.diff(luma, axis=1, prepend=luma[:, :1]))
    gy = np.abs(np.diff(luma, axis=0, prepend=luma[:1, :]))
    grad = np.hypot(gx, gy)
    return float(np.mean(grad[mask]) if mask is not None else np.mean(grad))


def build_disocclusion_triplet(h=128, w=192, v_bg_x=0.0, v_fg_x=-16.0):
    """Foreground silhouette moving over a structured background.

    Note: In warp8 convention, sample_P = u + t*vx, sample_C = u - (1-t)*vx,
    so an object moving right by +D px from P to C has vx = -D.
    """
    gh, gw = h // 16, w // 16
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing='ij')

    def bg_at(x, y):
        c0 = 0.22 + 0.14 * np.sin(x * 0.11) * np.cos(y * 0.09)
        c1 = 0.28 + 0.15 * np.cos(x * 0.07 + 0.5)
        c2 = 0.35 + 0.12 * np.sin(y * 0.13)
        return np.stack([c0, c1, c2, np.ones_like(c0)], axis=-1)

    def fg_at(x, y):
        r0 = 0.85 + 0.08 * np.cos(x * 0.2)
        r1 = 0.72 + 0.08 * np.sin(y * 0.2)
        r2 = 0.18 + 0.05 * np.cos((x + y) * 0.15)
        return np.stack([r0, r1, r2, np.ones_like(r0)], axis=-1)

    # Foreground x-interval at t=0.5 is [72, 120]
    fg_x0_mid, fg_x1_mid = 72.0, 120.0
    fg_y0, fg_y1 = 24.0, 104.0

    def render_frame(phase):
        # phase = -0.5 for P, 0.0 for GT (t=0.5), +0.5 for C
        # Since vx = x_P - x_C, position at phase is x_mid - phase * vx
        bg_x = xx + phase * v_bg_x
        fg_x = xx + phase * v_fg_x
        bg = bg_at(bg_x, yy)
        fg = fg_at(fg_x, yy)
        in_fg = (fg_x >= fg_x0_mid) & (fg_x < fg_x1_mid) & (yy >= fg_y0) & (yy < fg_y1)
        return np.where(in_fg[..., None], fg, bg), in_fg

    P, in_fg_P = render_frame(-0.5)
    GT, in_fg_GT = render_frame(0.0)
    C, in_fg_C = render_frame(+0.5)

    # Mid-grid motion vectors F (16x16 blocks)
    F = np.zeros((gh, gw, 2), dtype=np.float32)
    F[..., 0] = v_bg_x
    for by in range(gh):
        for bx in range(gw):
            cx = bx * 16.0 + 8.0
            cy = by * 16.0 + 8.0
            if fg_x0_mid <= cx <= fg_x1_mid and fg_y0 <= cy < fg_y1:
                F[by, bx, 0] = v_fg_x

    # Disocclusion (covered in P, uncovered in C & GT) and occlusion (visible in P & GT, covered in C)
    disocc_mask = in_fg_P & (~in_fg_C) & (~in_fg_GT)
    occ_mask = (~in_fg_P) & in_fg_C & (~in_fg_GT)
    boundary_mask = disocc_mask | occ_mask
    return P, C, GT, F, disocc_mask, occ_mask, boundary_mask


def build_inline_hud_triplet(h=128, w=192):
    """Inline 1-3px white crosshair, colored HUD bar, and subtitle strokes over fast moving background."""
    gh, gw = h // 16, w // 16
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing='ij')
    vx_bg, vy_bg = 12.0, 8.0  # Fast diagonal background motion

    def bg_at(x, y):
        # Smooth low-frequency moving background where a2 ~ b2 so emov ~ 0
        r = 0.20 + 0.08 * np.sin(x * 0.025) * np.cos(y * 0.03)
        g = 0.24 + 0.07 * np.cos(x * 0.02)
        b = 0.28 + 0.08 * np.sin(y * 0.025)
        return np.stack([r, g, b, np.ones_like(r)], axis=-1)

    P = bg_at(xx - 0.5 * vx_bg, yy - 0.5 * vy_bg)
    GT = bg_at(xx, yy)
    C = bg_at(xx + 0.5 * vx_bg, yy + 0.5 * vy_bg)

    hud_mask = np.zeros((h, w), dtype=bool)

    # 1. 2px White crosshair at center (64, 96)
    ch_h = (np.abs(yy - 64) <= 0.5) & (np.abs(xx - 96) <= 14)
    ch_v = (np.abs(xx - 96) <= 0.5) & (np.abs(yy - 64) <= 14)
    crosshair = ch_h | ch_v
    hud_white = np.array([0.92, 0.92, 0.92, 1.0], dtype=np.float32)

    # 2. 2px Colored (red/amber) HUD health/reticle indicator (low sum3 contrast, high single-channel contrast)
    col_stroke = (yy >= 22) & (yy < 24) & (xx >= 40) & (xx < 152)
    hud_color = np.array([0.82, 0.18, 0.12, 1.0], dtype=np.float32)

    # 3. 1-2px Subtitle glyph strokes at bottom (y in [98, 110])
    sub_stroke = ((yy >= 100) & (yy < 102) & (xx >= 52) & (xx < 140) & (((xx.astype(np.int32) // 3) % 2) == 0))

    for mask, color in [(crosshair, hud_white), (col_stroke, hud_color), (sub_stroke, hud_white)]:
        hud_mask |= mask
        # Slight temporal jitter/compression noise (es ~ 0.03) so baseline ws0 (es < 0.006) fails
        P[mask] = np.clip(color - 0.005, 0.0, 1.0)
        C[mask] = np.clip(color + 0.005, 0.0, 1.0)
        GT[mask] = color

    F = np.zeros((gh, gw, 2), dtype=np.float32)
    F[..., 0] = vx_bg
    F[..., 1] = vy_bg
    return P, C, GT, F, hud_mask


def build_dark_spot_regression_triplet(h=128, w=192):
    """Moving periodic bright texture where P(x) and C(x) coincide at a dark trough (es ~ 0, dl_s < 0).

    Verifies that the HUD gate never triggers on dark static coincidences and that false dark spots
    from baseline ws0 are eliminated.
    """
    gh, gw = h // 16, w // 16
    yy, xx = np.meshgrid(np.arange(h, dtype=np.float32), np.arange(w, dtype=np.float32), indexing='ij')
    # Period = 16 px, velocity vx = 16 px:
    # At t=0 (P), phase shift is -8 px (half period).
    # At t=1 (C), phase shift is +8 px (half period).
    # So cos(2*pi*(x - 8)/16) == cos(2*pi*(x + 8)/16) = -cos(2*pi*x/16)!
    # Thus P(x) == C(x) EVERYWHERE (es == 0!), while at peaks of GT(x) = cos(2*pi*x/16),
    # GT(x) is BRIGHT (+1) and P(x) == C(x) is DARK (-1)!
    vx = 16.0
    omega = 2.0 * math.pi / 16.0

    def tex_at(x, y):
        wave = 0.55 + 0.30 * np.cos(omega * x) * np.cos(y * 0.05)
        return np.stack([wave, wave, wave, np.ones_like(wave)], axis=-1)

    P = tex_at(xx - 0.5 * vx, yy)
    GT = tex_at(xx, yy)
    C = tex_at(xx + 0.5 * vx, yy)

    F = np.zeros((gh, gw, 2), dtype=np.float32)
    F[..., 0] = vx
    # Dark-spot zone: interior where GT is bright (> 0.65) and static P(x)==C(x) is darker (< 0.45)
    interior = (xx >= 16) & (xx < w - 16)
    dark_coincidence_mask = interior & (GT[..., 0] > 0.65) & (P[..., 0] < 0.45)
    return P, C, GT, F, dark_coincidence_mask


def verify_shader_build_and_ir():
    """Verify warp8.ll and compiled shaders.h respect GCN2 hardware limits."""
    warp8_ll = (ROOT / 'classic/shaders/warp8.ll').read_text(encoding='utf-8')
    shaders_h = (ROOT / 'classic/source/shaders.h').read_text(encoding='utf-8')

    # Check key asymmetric disocclusion & inline HUD symbols in warp8.ll
    for sym in ['%f20', '%f02', '%vsp', '%w_occ_s', '%div_b_s', '%pos_c', '%dk_spot', '%w_hud', '%w_occ_m']:
        assert sym in warp8_ll, f"Missing expected symbol {sym} in classic/shaders/warp8.ll"

    # Extract COMPUTE_PGM_RSRC1 and COMPUTE_PGM_RSRC2 for warp8 from shaders.h
    import re
    m1 = re.search(r'#define\s+WARP8_RSRC1\s+0x([0-9a-fA-F]+)', shaders_h)
    m2 = re.search(r'#define\s+WARP8_RSRC2\s+0x([0-9a-fA-F]+)', shaders_h)
    mw = re.search(r'#define\s+WARP8_NWORDS\s+(\d+)', shaders_h)
    assert m1 and m2 and mw, "Could not find WARP8_RSRC1/WARP8_RSRC2/WARP8_NWORDS in classic/source/shaders.h"
    rsrc1 = int(m1.group(1), 16)
    rsrc2 = int(m2.group(1), 16)
    nwords = int(mw.group(1))
    vgpr_blocks = rsrc1 & 0x3F
    sgpr_blocks = (rsrc1 >> 6) & 0x0F
    num_vgprs = (vgpr_blocks + 1) * 4
    num_sgprs = (sgpr_blocks + 1) * 8
    scratch_en = (rsrc2 & 1)
    assert num_vgprs <= 64, f"warp8 VGPR limit exceeded: {num_vgprs} > 64"
    assert scratch_en == 0, f"warp8 uses scratch memory (SCRATCH_EN={scratch_en})"
    print(f"[Hardware Limits Check]                warp8: {nwords} words, rsrc1=0x{rsrc1:08X} (vgprs<={num_vgprs}, "
          f"sgprs<={num_sgprs}), rsrc2=0x{rsrc2:08X} (scratch={scratch_en})")


def main():
    verify_shader_build_and_ir()

    # 1. Stationary-background disocclusion & occlusion test
    P, C, GT, F, disocc_m, occ_m, bnd_m = build_disocclusion_triplet(v_bg_x=0.0, v_fg_x=-16.0)
    out_base = run_warp8_ref(P, C, F, mode='base')
    out_new = run_warp8_ref(P, C, F, mode='new')

    psnr_disocc_base = psnr(out_base, GT, disocc_m)
    psnr_disocc_new = psnr(out_new, GT, disocc_m)
    psnr_occ_base = psnr(out_base, GT, occ_m)
    psnr_occ_new = psnr(out_new, GT, occ_m)
    psnr_bnd_base = psnr(out_base, GT, bnd_m)
    psnr_bnd_new = psnr(out_new, GT, bnd_m)
    sharp_bnd_base = edge_sharpness(out_base, bnd_m)
    sharp_bnd_new = edge_sharpness(out_new, bnd_m)

    # 2. Panning-camera disocclusion & occlusion test
    P2, C2, GT2, F2, disocc_m2, occ_m2, bnd_m2 = build_disocclusion_triplet(v_bg_x=6.0, v_fg_x=22.0)
    out_base2 = run_warp8_ref(P2, C2, F2, mode='base')
    out_new2 = run_warp8_ref(P2, C2, F2, mode='new')
    psnr_pan_base = psnr(out_base2, GT2, bnd_m2)
    psnr_pan_new = psnr(out_new2, GT2, bnd_m2)

    # 3. Inline static HUD / crosshair / subtitle test
    P3, C3, GT3, F3, hud_m = build_inline_hud_triplet()
    out_base3 = run_warp8_ref(P3, C3, F3, mode='base')
    out_new3 = run_warp8_ref(P3, C3, F3, mode='new')
    psnr_hud_base = psnr(out_base3, GT3, hud_m)
    psnr_hud_new = psnr(out_new3, GT3, hud_m)
    sharp_hud_base = edge_sharpness(out_base3, hud_m)
    sharp_hud_new = edge_sharpness(out_new3, hud_m)

    # 4. Moving-texture dark-spot regression test
    P4, C4, GT4, F4, dk_m = build_dark_spot_regression_triplet()
    out_base4 = run_warp8_ref(P4, C4, F4, mode='base')
    out_new4 = run_warp8_ref(P4, C4, F4, mode='new')
    psnr_dk_base = psnr(out_base4, GT4, dk_m)
    psnr_dk_new = psnr(out_new4, GT4, dk_m)
    max_dark_err_new = float(np.max(np.abs(out_new4[..., :3] - GT4[..., :3])[dk_m]))

    # 5. Moving dark feature over slightly textured bright background (es=0.03) non-regression check
    P5 = np.clip(P4 - 0.005, 0.0, 1.0)
    C5 = np.clip(C4 + 0.005, 0.0, 1.0)
    out_base5 = run_warp8_ref(P5, C5, F4, mode='base')
    out_new5 = run_warp8_ref(P5, C5, F4, mode='new')
    dark_trough_m = (GT4[..., 0] < 0.45) & (P4[..., 0] > 0.65)
    psnr_trough_base = psnr(out_base5, GT4, dark_trough_m)
    psnr_trough_new = psnr(out_new5, GT4, dark_trough_m)

    print(f"[Disocclusion/Occlusion Stationary BG] Boundary PSNR: {psnr_bnd_base:.2f} dB -> {psnr_bnd_new:.2f} dB "
          f"(+{psnr_bnd_new - psnr_bnd_base:.2f} dB) | Disocc: {psnr_disocc_base:.2f} -> {psnr_disocc_new:.2f} dB | "
          f"Occ: {psnr_occ_base:.2f} -> {psnr_occ_new:.2f} dB")
    print(f"[Disocclusion/Occlusion Panning BG]    Boundary PSNR: {psnr_pan_base:.2f} dB -> {psnr_pan_new:.2f} dB "
          f"(+{psnr_pan_new - psnr_pan_base:.2f} dB)")
    print(f"[Inline Static HUD/Crosshair/Subtitle] Stroke PSNR:   {psnr_hud_base:.2f} dB -> {psnr_hud_new:.2f} dB "
          f"(+{psnr_hud_new - psnr_hud_base:.2f} dB) | Edge Sharpness: {sharp_hud_base:.4f} -> {sharp_hud_new:.4f}")
    print(f"[Moving Texture Dark-Spot Guard]       Coincidence PSNR: {psnr_dk_base:.2f} dB -> {psnr_dk_new:.2f} dB "
          f"(+{psnr_dk_new - psnr_dk_base:.2f} dB) | Max err (new): {max_dark_err_new:.5f}")
    print(f"[Moving Dark Feature Non-Regression]   Dark Trough PSNR: {psnr_trough_base:.2f} dB -> {psnr_trough_new:.2f} dB "
          f"({psnr_trough_new - psnr_trough_base:+.2f} dB)")

    assert psnr_disocc_new > psnr_disocc_base + 1.5, "Expected significant disocclusion PSNR gain"
    assert psnr_occ_new > psnr_occ_base + 1.5, "Expected significant occlusion PSNR gain"
    assert psnr_pan_new > psnr_pan_base + 1.0, "Expected panning boundary PSNR gain"
    assert psnr_hud_new > psnr_hud_base + 8.0, "Expected major inline HUD/crosshair PSNR gain"
    assert sharp_hud_new > sharp_hud_base * 1.5, "Expected sharper HUD/crosshair stroke edges"
    assert psnr_dk_new > psnr_dk_base + 10.0 and max_dark_err_new < 0.02, "Dark-spot regression detected"
    assert psnr_trough_new >= psnr_trough_base - 0.01, "Moving dark feature regression detected"
    print("All warp8 halo reduction & inline HUD protection checks passed.")


if __name__ == '__main__':
    main()
