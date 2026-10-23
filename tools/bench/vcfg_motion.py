"""NumPy reference implementation of the VCFG classic mode 8 motion shaders (pyr8 -> me3 -> me2 -> gm8 -> fgs8 -> me1 -> mf8).

Integer arithmetic matches the shaders line by line: same clamping, same tie-breaking keys (cost << 8 | lane), same
fixed-point shifts. All functions take / return NumPy arrays; pyr8 takes float32 RGB in [0, 1] (H, W, 3) and returns
uint8 luma levels L1 (540, 960), L2 (270, 480), L3 (135, 240).
"""
import numpy as np

BIG = np.iinfo(np.int64).max


# ---------------------------------------------------------------------------------------------------------------
# @bsad / @bsadu: 8x8 luma SAD with displacement-preserving border clamp and structure-weighted DC compensation
# ---------------------------------------------------------------------------------------------------------------
def _blocks(img, xs, ys):
    """gather (N, 8, 8) int64 blocks; xs is (N,) already clamped to [0, w-8], ys is (N, 8) already clamped to [0, h-1]"""
    cols = xs[:, None] + np.arange(8)[None, :]
    return img[ys[:, :, None], cols[:, None, :]]


def _dc_adjust(raw_sad, sum_p, sum_c):
    """DC illumination/specular compensation + cross-frame occlusion DC mismatch penalty:
    - When 256 < dc = |sum_P - sum_C| <= 2048 and ac = raw_sad - dc is small (coherent illumination shift on a matching
      texture), discounts up to 3/4 of (dc - 256): disc = max(((dc - 256) * 3 >> 2) - (ac << 1), 0).
    - When dc > 2048 (jumping onto a different surface across an occlusion boundary), adds (dc - 2048) >> 2 penalty.
    """
    dc = np.abs(sum_p - sum_c)
    ac = raw_sad - dc
    dc_eff = np.maximum(dc - 256, 0)
    disc = np.maximum(((dc_eff * 3) >> 2) - (ac << 1), 0)
    pen = np.maximum(dc - 2048, 0) >> 2
    return raw_sad - np.where(dc <= 2048, disc, -pen)


def sad_cur_grid(prev, cur, X, Y, dx, dy, dc_comp=False):
    """bsadu: cur block at (X, Y), prev block at (X + dx, Y + dy) with displacement-preserving border clamp."""
    h, w = cur.shape
    r = np.arange(8)[None, :]
    X, Y, dx, dy = (np.asarray(a, np.int64) for a in (X, Y, dx, dy))
    wm8, hm8 = w - 8, h - 8
    dxc = np.clip(dx, -wm8, wm8)
    dyc = np.clip(dy, -hm8, hm8)
    xc = np.clip(X, np.maximum(0, -dxc), np.minimum(wm8, wm8 - dxc))
    xp = xc + dxc
    ybc = np.clip(Y, np.maximum(0, -dyc), np.minimum(hm8, hm8 - dyc))
    ybp = ybc + dyc
    yc = ybc[:, None] + r
    yp = ybp[:, None] + r
    p = _blocks(prev, xp, yp)
    c = _blocks(cur, xc, yc)
    raw_sad = np.abs(p - c).sum(axis=(1, 2))
    if dc_comp:
        return _dc_adjust(raw_sad, p.sum(axis=(1, 2)), c.sum(axis=(1, 2)))
    return raw_sad


def sad_bilateral(prev, cur, X, Y, dx, dy, dc_comp=True):
    """me1 bsad: prev block at X + ceil(d/2), cur block at X - floor(d/2) with displacement-preserving border clamp.
    Returns (dc_compensated_sad, (sum_prev + sum_cur) >> 1)."""
    h, w = cur.shape
    r = np.arange(8)[None, :]
    X, Y, dx, dy = (np.asarray(a, np.int64) for a in (X, Y, dx, dy))
    wm8, hm8 = w - 8, h - 8
    dxc = np.clip(dx, -wm8, wm8)
    dyc = np.clip(dy, -hm8, hm8)
    xc0 = X - (dxc >> 1)
    xc = np.clip(xc0, np.maximum(0, -dxc), np.minimum(wm8, wm8 - dxc))
    xp = xc + dxc
    ybc0 = Y - (dyc >> 1)
    ybc = np.clip(ybc0, np.maximum(0, -dyc), np.minimum(hm8, hm8 - dyc))
    ybp = ybc + dyc
    yc = ybc[:, None] + r
    yp = ybp[:, None] + r
    p = _blocks(prev, xp, yp)
    c = _blocks(cur, xc, yc)
    raw_sad = np.abs(p - c).sum(axis=(1, 2))
    sum_p = p.sum(axis=(1, 2))
    sum_c = c.sum(axis=(1, 2))
    if dc_comp:
        sad = _dc_adjust(raw_sad, sum_p, sum_c)
    else:
        sad = raw_sad
    return np.minimum(sad, 65535), (sum_p + sum_c) >> 1


def rnd(f):
    """LLVM @rnd: fptosi(f + (f < 0 ? -0.5 : 0.5)) — round half away from zero, float32."""
    f = np.asarray(f, np.float32)
    return np.trunc(f + np.where(f < 0, np.float32(-0.5), np.float32(0.5))).astype(np.int64)


# ---------------------------------------------------------------------------------------------------------------
# pyr8: 1920x1080 float RGB [0,1] -> L1 (540, 960), L2 (270, 480), L3 (135, 240) int64 in 0..255
# ---------------------------------------------------------------------------------------------------------------
def pyr8(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    l0 = r * np.float32(0.2126) + g * np.float32(0.7152) + b * np.float32(0.0722)
    l1f = np.float32(0.25) * (l0[0::2, 0::2] + l0[0::2, 1::2] + l0[1::2, 0::2] + l0[1::2, 1::2])
    L1 = np.clip(l1f * np.float32(255.0) + np.float32(0.5), 0, 255).astype(np.int64)
    L2 = (L1[0::2, 0::2] + L1[0::2, 1::2] + L1[1::2, 0::2] + L1[1::2, 1::2] + 2) >> 2
    h2, w2 = L2.shape
    y0 = np.arange((h2 + 1) // 2) * 2
    y1 = np.minimum(y0 + 1, h2 - 1)
    x0 = np.arange((w2 + 1) // 2) * 2
    x1 = np.minimum(x0 + 1, w2 - 1)
    L3 = (L2[y0[:, None], x0[None, :]] + L2[y0[:, None], x1[None, :]] +
          L2[y1[:, None], x0[None, :]] + L2[y1[:, None], x1[None, :]] + 2) >> 2
    return L1, L2, L3


def _grid(nbx, nby):
    by, bx = np.meshgrid(np.arange(nby, dtype=np.int64), np.arange(nbx, dtype=np.int64), indexing='ij')
    return bx.ravel(), by.ravel()


def _med4(a, b, c, d):
    x0, x1 = np.minimum(a, b), np.maximum(a, b)
    x2, x3 = np.minimum(c, d), np.maximum(c, d)
    lo, hi = np.maximum(x0, x2), np.minimum(x1, x3)
    return (lo + hi) >> 1


# ---------------------------------------------------------------------------------------------------------------
# me3 / me2: coarse motion on the CUR grid
# ---------------------------------------------------------------------------------------------------------------
def me3(L3p, L3c, prevF=None, dc_comp=True):
    """30 x 17 blocks of 8x8 L3 px -> V3 (17, 30, 2) in L3 px; also the sum of the cur L3 frame (misc luma sum)"""
    h, w = L3c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = _grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    zero = np.zeros(N, np.int64)
    if prevF is not None:
        tx, ty = np.minimum(bx * 4 + 2, 119), np.minimum(by * 4 + 2, 67)
        tvx, tvy = prevF[ty, tx, 0], prevF[ty, tx, 1]
        tdx, tdy = rnd(tvx * np.float32(0.125)), rnd(tvy * np.float32(0.125))
        upx, upy = rnd(tvx * np.float32(0.0625)), rnd(tvy * np.float32(0.0625))
        txu, tyu = np.clip(tx + upx, 0, 119), np.clip(ty + upy, 0, 67)
        tvux, tvuy = prevF[tyu, txu, 0], prevF[tyu, txu, 1]
        tdxu, tdyu = rnd(tvux * np.float32(0.125)), rnd(tvuy * np.float32(0.125))
        tdxa = rnd((tvux * np.float32(2.0) - tvx) * np.float32(0.125))
        tdya = rnd((tvuy * np.float32(2.0) - tvy) * np.float32(0.125))
        tdxf = rnd((tvx * np.float32(2.0) - tvux) * np.float32(0.125))
        tdyf = rnd((tvy * np.float32(2.0) - tvuy) * np.float32(0.125))
    else:
        tdx = tdy = tdxu = tdyu = tdxa = tdya = tdxf = tdyf = zero
    cands_a = [(zero, zero), (tdx, tdy), (tdxu, tdyu), (tdxa, tdya), (tdxf, tdyf)]
    best_a = np.full(N, BIG, np.int64)
    cx, cy = zero.copy(), zero.copy()
    for tid_a, (ax, ay) in enumerate(cands_a):
        sa = sad_cur_grid(L3p, L3c, X, Y, ax, ay, dc_comp=dc_comp)
        ca = sa + 256 - (4 if tid_a >= 1 else 0)
        ka = (ca << 8) | tid_a
        better_a = ka < best_a
        best_a = np.where(better_a, ka, best_a)
        cx, cy = np.where(better_a, ax, cx), np.where(better_a, ay, cy)
    best = np.full(N, BIG, np.int64)
    bk = np.zeros(N, np.int64)
    for k in range(435):
        ox, oy = k % 29 - 14, k // 29 - 7
        s = sad_cur_grid(L3p, L3c, X, Y, cx + ox, cy + oy, dc_comp=dc_comp)
        key = ((s + 256 + 4 * (abs(ox) + abs(oy))) << 9) | k
        better = key < best
        best = np.where(better, key, best)
        bk = np.where(better, k, bk)
    vx, vy = cx + (bk % 29 - 14), cy + (bk // 29 - 7)
    # luma sum accumulated by lane 0 of each block (same clamping as sad_cur_grid at d=0)
    r = np.arange(8)[None, :]
    lumasum = int(_blocks(L3c, np.clip(X, 0, w - 8), np.clip(Y[:, None] + r, 0, h - 1)).sum())
    return np.stack([vx, vy], axis=-1).reshape(nby, nbx, 2), lumasum


def me2(L2p, L2c, V3, prevF=None):
    """60 x 34 blocks of 8x8 L2 px -> V2 (34, 60, 2) in L2 px, S2 (34, 60, 2) = (best_sad, zero_sad)"""
    h, w = L2c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = _grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    n3y, n3x = V3.shape[:2]
    ix0 = np.clip((bx * 4 - 2) >> 3, 0, max(n3x - 2, 0))
    ix1 = np.minimum(ix0 + 1, n3x - 1)
    iy0 = np.clip((by * 4 - 2) >> 3, 0, max(n3y - 2, 0))
    iy1 = np.minimum(iy0 + 1, n3y - 1)
    nb = [V3[iy0, ix0], V3[iy0, ix1], V3[iy1, ix0], V3[iy1, ix1]]    # lanes 0..3 (sub & 1 -> x1, sub & 2 -> y1)
    medx = _med4(*[v[:, 0] for v in nb]) * 2
    medy = _med4(*[v[:, 1] for v in nb]) * 2
    zero = np.zeros(N, np.int64)
    tx0, ty0 = np.minimum(bx * 2 + 1, 119), np.minimum(by * 2 + 1, 67)

    def tdv2(ox, oy):
        if prevF is None:
            return zero, zero
        tx = np.clip(tx0 + ox, 0, 119)
        ty = np.clip(ty0 + oy, 0, 67)
        return rnd(prevF[ty, tx, 0] * np.float32(0.25)), rnd(prevF[ty, tx, 1] * np.float32(0.25))

    if prevF is not None:
        tv0x, tv0y = prevF[ty0, tx0, 0], prevF[ty0, tx0, 1]
        upx, upy = rnd(tv0x * np.float32(0.0625)), rnd(tv0y * np.float32(0.0625))
        txu, tyu = np.clip(tx0 + upx, 0, 119), np.clip(ty0 + upy, 0, 67)
        tvux, tvuy = prevF[tyu, txu, 0], prevF[tyu, txu, 1]
        tdv_adv = (rnd(tvux * np.float32(0.25)), rnd(tvuy * np.float32(0.25)))
        tdv_acc = (rnd((tvux * np.float32(2.0) - tv0x) * np.float32(0.25)),
                   rnd((tvuy * np.float32(2.0) - tv0y) * np.float32(0.25)))
    else:
        tdv_adv = (zero, zero)
        tdv_acc = (zero, zero)

    ixm1 = np.maximum(ix0 - 1, 0)
    ixp1 = np.minimum(ix1 + 1, n3x - 1)
    iym1 = np.maximum(iy0 - 1, 0)
    iyp1 = np.minimum(iy1 + 1, n3y - 1)
    outer = [V3[iy0, ixm1], V3[iy0, ixp1], V3[iym1, ix0], V3[iyp1, ix0]]   # lanes 10..13
    cands = (
        [(v[:, 0] * 2, v[:, 1] * 2) for v in nb] +                         # lanes 0..3
        [tdv2(0, 0), (zero, zero)] +                                       # lanes 4, 5
        [tdv2(2, 0), tdv2(-2, 0), tdv2(0, 2), tdv2(0, -2)] +               # lanes 6..9 (symmetric temporal)
        [(v[:, 0] * 2, v[:, 1] * 2) for v in outer] +                      # lanes 10..13 (outer L3 ring)
        [tdv_adv, tdv_acc]                                                 # lanes 14..15 (self-advected & accel)
    )
    best = np.full(N, BIG, np.int64)
    cdx_b, cdy_b, zcost = zero.copy(), zero.copy(), None
    for sub, (cdx, cdy) in enumerate(cands):
        s = sad_cur_grid(L2p, L2c, X, Y, cdx, cdy, dc_comp=True)
        oob_m = (X + cdx < 0) | (X + cdx > w - 8) | (Y + cdy < 0) | (Y + cdy > h - 8)
        msh = np.where(oob_m, 3, 2)
        ist = (sub == 4) or (6 <= sub <= 9) or (14 <= sub <= 15)
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
        s = sad_cur_grid(L2p, L2c, X, Y, cdx_b + ox, cdy_b + oy, dc_comp=True)
        key = ((s + 256 + 4 * (abs(ox) + abs(oy))) << 8) | k
        better = key < best2
        best2 = np.where(better, key, best2)
        bk = np.where(better, k, bk)
    vx, vy = cdx_b + bk % 5 - 2, cdy_b + bk // 5 - 2
    bsad = (best2 >> 8) - 256 - 4 * (np.abs(bk % 5 - 2) + np.abs(bk // 5 - 2))
    return (np.stack([vx, vy], -1).reshape(nby, nbx, 2), np.stack([bsad, zsad], -1).reshape(nby, nbx, 2))


def gm8(V2, S2):
    """camera vector: per component lower median of the really matching L2 vectors, clamped to [-64, 63]"""
    sb, sz = S2[..., 0].ravel(), S2[..., 1].ravel()
    good = (sb * 5 <= sz * 4) | (sb <= 160)
    out = []
    for c in range(2):
        v = np.clip(V2[..., c].ravel()[good], -64, 63)
        out.append(int(np.sort(v)[v.size >> 1]) if v.size else 0)
    return out[0], out[1]


# ---------------------------------------------------------------------------------------------------------------
# fgs8: foreground splat
# ---------------------------------------------------------------------------------------------------------------
def _isqrt(q):
    return np.floor(np.sqrt(np.maximum(q, 0).astype(np.float32))).astype(np.int64)


def fgs8(L2p, L2c, V2, S2, gm, anchor_hist=None):
    """returns GK (68, 120) uint32 splat keys (prio << 20 | closeness << 12 | L2 block) and the new anchor history"""
    nby2, nbx2 = V2.shape[:2]
    N = nbx2 * nby2
    i = np.arange(N)
    bxq, byq = i % nbx2, i // nbx2
    BX, BY = bxq * 8, byq * 8
    vx, vy = V2[..., 0].ravel(), V2[..., 1].ravel()
    sb = S2[..., 0].ravel()
    gmx, gmy = gm
    gs = [sad_cur_grid(L2p, L2c, BX, BY, np.full(N, gmx + ox), np.full(N, gmy + oy), dc_comp=True)
          for ox, oy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))]
    gsad = np.minimum.reduce(gs)
    gdif = gsad - sb
    good = (sb * 5 <= gsad * 4) & (gdif >= 96)
    q = (vx - gmx) ** 2 + (vy - gmy) ** 2
    vv = vx ** 2 + vy ** 2
    gmv = gmx * gmx + gmy * gmy
    stl = np.bool_(gmv <= 4)
    # Close the 0.5 * |gm| .. 1.0 * |gm| dead zone: any well-matched, textured, spatially coherent block
    # deviating from gm (|V2 - gm|^2 > |gm|^2 / 16 + 3) and moving in screen space (vv > 2 when camera pans,
    # vv > 4 when camera is still) is splatted as a moving foreground layer; quasi-static blocks (vv <= 2) are anchors.
    dev = q > (gmv >> 4) + 3
    mov = np.where(stl, vv > 4, vv > 2)
    one, zero = np.ones(N, np.int64), np.zeros(N, np.int64)
    tex = sad_cur_grid(L2c, L2c, BX, BY, one, zero) + sad_cur_grid(L2c, L2c, BX, BY, zero, one)
    texp = sad_cur_grid(L2p, L2p, BX + vx, BY + vy, one, zero) + sad_cur_grid(L2p, L2p, BX + vx, BY + vy, zero, one)
    texm = np.minimum(tex, texp)
    istex = texm >= 64
    rm = 2 * sb <= texm
    f1 = good & dev & istex
    fg0 = f1 & mov & rm
    slw = (vv <= 2) & (4 * vv <= gmv)
    inb = (bxq >= 1) & (bxq <= 58) & (byq >= 1) & (byq <= 32)
    an0 = np.where(stl, istex & (4 * vv <= 4) & rm, f1 & slw & rm) & inb
    need = fg0 | an0

    def nbok(ox, oy):
        nx, ny = bxq + ox, byq + oy
        ok = (nx >= 0) & (nx < nbx2) & (ny >= 0) & (ny < nby2)
        nxc, nyc = np.clip(nx, 0, nbx2 - 1), np.clip(ny, 0, nby2 - 1)
        return ok & (np.abs(V2[nyc, nxc, 0] - vx) + np.abs(V2[nyc, nxc, 1] - vy) <= 4)

    nbk = np.zeros(N, bool)
    nb_cnt = np.zeros(N, np.int64)
    if need.any():
        nb0, nb1, nb2, nb3 = nbok(-1, 0), nbok(1, 0), nbok(0, -1), nbok(0, 1)
        nbk = nb0 | nb1 | nb2 | nb3
        nb_cnt = nb0.astype(np.int64) + nb1.astype(np.int64) + nb2.astype(np.int64) + nb3.astype(np.int64)
    isfg, isa0 = fg0 & nbk, an0 & nbk
    h = np.zeros(N, np.int64) if anchor_hist is None else anchor_hist.astype(np.int64)
    hn = np.where(isa0, np.minimum(h + 1, 255), 0)
    strong_an = (~stl) & (nb_cnt >= 2) & (gdif >= 160)
    isan = isa0 & ((hn >= 2) | strong_an)
    doit = isfg | isan
    prio = np.where(isfg, _isqrt(np.minimum(q >> 2, 16760836)) + 1, 0)
    GK = np.zeros((68, 120), np.int64)
    for n in np.nonzero(doit)[0]:
        pkey = (int(prio[n]) << 20) | int(n)
        cx = int(bxq[n]) * 32 + 16 + 2 * int(vx[n])
        cy = int(byq[n]) * 32 + 16 + 2 * int(vy[n])
        for gy in ((cy - 16) >> 4, cy >> 4, (cy + 15) >> 4):
            for gx in ((cx - 16) >> 4, cx >> 4, (cx + 15) >> 4):
                if 0 <= gx < 120 and 0 <= gy < 68:
                    e = (cx - (gx * 16 + 8)) ** 2 + (cy - (gy * 16 + 8)) ** 2
                    d4 = min(int(_isqrt(16 * e)), 255)
                    GK[gy, gx] = max(GK[gy, gx], pkey | ((255 - d4) << 12))
    return GK, hn.astype(np.uint8)


# ---------------------------------------------------------------------------------------------------------------
# me1: mid-grid motion field (bilateral SAD on L1)
# ---------------------------------------------------------------------------------------------------------------
def _equi(m, c, p):
    m, c, p = (np.asarray(a, np.float32) for a in (m, c, p))
    mx, mn = np.maximum(m, p), np.minimum(m, p)
    den = (mx - c) * np.float32(2.0)
    cg = np.minimum(c * np.float32(2.0) / np.maximum(mn, np.float32(1.0)), np.float32(1.0))
    with np.errstate(divide='ignore', invalid='ignore'):
        q = ((m - p) * cg) / den
    o = np.where(c < mn, q, np.float32(0.0))
    return np.clip(o, -0.5, 0.5).astype(np.float32)


def me1(L1p, L1c, V2, GK, gm, lumasum_cur, lumasum_prev, prevF=None, mp=None):
    mp = mp or {}
    """raw field FT (68, 120, 2) float32 (full-res px, mid grid), the unmatched-block count and the fade flag"""
    h, w = L1c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = _grid(nbx, nby)
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
    nbv = [V2[a, b].astype(np.int64) * 2 for a, b in nbi]                      # (N, 2) in L1 px
    ixm1 = np.maximum(ix0 - 1, 0)
    ixp1 = np.minimum(ix1 + 1, n2x - 1)
    iym1 = np.maximum(iy0 - 1, 0)
    iyp1 = np.minimum(iy1 + 1, n2y - 1)
    out_nbi = [(iy0, ixm1), (iy0, ixp1), (iym1, ix0), (iyp1, ix0)]
    out_nbv = [V2[a, b].astype(np.int64) * 2 for a, b in out_nbi]              # outer L2 ring for lanes 12..15
    zero = np.zeros(N, np.int64)

    if prevF is not None:
        tv0x, tv0y = prevF[by, bx, 0], prevF[by, bx, 1]
        upx, upy = rnd(tv0x * np.float32(0.0625)), rnd(tv0y * np.float32(0.0625))
        up_nz = (upx != 0) | (upy != 0)
        txu, tyu = np.clip(bx + upx, 0, 119), np.clip(by + upy, 0, 67)
        tvux, tvuy = prevF[tyu, txu, 0], prevF[tyu, txu, 1]

        def td_nb(tox, toy):
            tx1, ty1 = np.clip(bx + tox, 0, 119), np.clip(by + toy, 0, 67)
            v1x, v1y = prevF[ty1, tx1, 0], prevF[ty1, tx1, 1]
            low = (np.abs(v1x) + np.abs(v1y)) < np.float32(8.0)
            tx2, ty2 = np.clip(bx + 2 * tox, 0, 119), np.clip(by + 2 * toy, 0, 67)
            v2x, v2y = prevF[ty2, tx2, 0], prevF[ty2, tx2, 1]
            return np.where(low, v2x, v1x), np.where(low, v2y, v1y)

        nb5x, nb5y = td_nb(1, 0)
        nb6x, nb6y = td_nb(0, 1)
        nb10x, nb10y = td_nb(-1, 0)
        nb11x, nb11y = td_nb(0, -1)

        fwx, fwy = tv0x * np.float32(2.0) - tvux, tv0y * np.float32(2.0) - tvuy
        acx, acy = tvux * np.float32(2.0) - tv0x, tvuy * np.float32(2.0) - tv0y
        midx = tvux + nbv[2][:, 0].astype(np.float32)
        midy = tvuy + nbv[2][:, 1].astype(np.float32)

        t4 = (rnd(tv0x * np.float32(0.5)), rnd(tv0y * np.float32(0.5)))
        t5 = (rnd(np.where(up_nz, fwx, nb5x) * np.float32(0.5)),
              rnd(np.where(up_nz, fwy, nb5y) * np.float32(0.5)))
        sc6 = np.where(up_nz, np.float32(0.25), np.float32(0.5))
        t6 = (rnd(np.where(up_nz, midx, nb6x) * sc6),
              rnd(np.where(up_nz, midy, nb6y) * sc6))
        t10 = (rnd(np.where(up_nz, tvux, nb10x) * np.float32(0.5)),
               rnd(np.where(up_nz, tvuy, nb10y) * np.float32(0.5)))
        t11 = (rnd(np.where(up_nz, acx, nb11x) * np.float32(0.5)),
               rnd(np.where(up_nz, acy, nb11y) * np.float32(0.5)))
    else:
        t4 = t5 = t6 = t10 = t11 = (zero, zero)

    gk = GK[by, bx]
    hasfg = gk != 0
    isfgk = gk >= (1 << 20)
    isanc = hasfg & ~isfgk
    gv = V2.reshape(-1, 2)[gk & 4095].astype(np.int64) * 2
    gdx, gdy = gv[:, 0], gv[:, 1]
    cand = [(nbv[k][:, 0], nbv[k][:, 1]) for k in range(4)]                    # slots 0..3: inner 2x2 L2
    cand += [t4, t5, t6]                                                        # slots 4..6: temporal / forward / mid
    cand += [(zero, zero), (gdx, gdy), (np.full(N, gmx2), np.full(N, gmy2))]   # slots 7, 8, 9: zero, GK, camera
    cand += [t10, t11]                                                          # slots 10..11: advected / accel temporal
    cand += [(out_nbv[k][:, 0], out_nbv[k][:, 1]) for k in range(4)]           # slots 12..15: outer cardinal L2 ring

    def nbdist(a, b, cdx, cdy):
        v = V2[a, b].astype(np.int64) * 2
        return np.abs(cdx - v[:, 0]) + np.abs(cdy - v[:, 1])

    zs, zsum = sad_bilateral(L1p, L1c, X, Y, zero, zero)                        # lane 7 publishes (sad, mean sum)
    best = np.full(N, BIG, np.int64)
    cx1, cy1 = zero.copy(), zero.copy()
    for sub, (cdx, cdy) in enumerate(cand):
        sad1, sum1 = sad_bilateral(L1p, L1c, X, Y, cdx, cdy)
        ist = (4 <= sub <= 6) or (10 <= sub <= 11)
        isout = 12 <= sub <= 15
        bias = np.zeros(N, np.int64)
        if ist:
            bias += mp.get("tbias", 16)
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
        # Camera-escape guard: penalize jumping onto the camera pan gm2 (sc * 2 <= aad) or distant sky (aad > 16),
        # while allowing articulated limb motion (3 < aad <= 16 away from gm2) around an anchor torso.
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
        if ist:                                                                 # 8.6 temporal support gate
            sz = np.abs(cdx) + np.abs(cdy)
            smin = np.minimum.reduce([sm03c, sz, np.where(hasfg, aad, sz)])
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
    # phase 2: +-1 around the winner
    best2 = np.full(N, BIG, np.int64)
    bk = np.zeros(N, np.int64)
    for k in range(9):
        ox, oy = k % 3 - 1, k // 3 - 1
        d2x, d2y = cx1 + ox, cy1 + oy
        sad2, sum2 = sad_bilateral(L1p, L1c, X, Y, d2x, d2y)
        p2f = (np.abs(d2x) + np.abs(d2y)) > 8
        aad2 = np.abs(d2x - gdx) + np.abs(d2y - gdy)
        sc2 = np.abs(d2x - gmx2) + np.abs(d2y - gmy2)
        ap2 = isanc & (aad2 > 3) & ((aad2 > 16) | ((~still) & (sc2 * 2 <= aad2)))
        p2gate = (~isfgk) & (ap2 | ((~still) & (sc2 <= 6)))
        p2p = np.where(p2f & p2gate, np.maximum((np.abs(sum2 - zsum) >> 1) - zs, 0), 0)
        key = ((sad2 + 256 + p2p + mp.get("lam1", 4) * (abs(ox) + abs(oy))) << 8) | k
        better = key < best2
        best2 = np.where(better, key, best2)
        bk = np.where(better, k, bk)
    bvx, bvy = cx1 + bk % 3 - 1, cy1 + bk // 3 - 1
    r0, _ = sad_bilateral(L1p, L1c, X, Y, bvx, bvy)
    r1, _ = sad_bilateral(L1p, L1c, X, Y, bvx - 1, bvy)
    r2, _ = sad_bilateral(L1p, L1c, X, Y, bvx + 1, bvy)
    r3, _ = sad_bilateral(L1p, L1c, X, Y, bvx, bvy - 1)
    r4, _ = sad_bilateral(L1p, L1c, X, Y, bvx, bvy + 1)
    r5, _ = sad_bilateral(L1p, L1c, X, Y, zero, zero)
    spx = _equi(r1, r0, r2)
    spy = _equi(r3, r0, r4)
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


# ---------------------------------------------------------------------------------------------------------------
# mf8: robust smoothness filter of the field + 8x8 Sub-Block Contour Refinement & Dual-Layer FG/BG Motion Field
# ---------------------------------------------------------------------------------------------------------------
def unpack_f_8x8(F):
    """Unpack the 120x68 dual-layer float32 field F into the effective 240x136 8x8 sub-block motion field."""
    nby, nbx = F.shape[:2]
    bxi = F[..., 0].view(np.uint32)
    byi = F[..., 1].view(np.uint32)
    m = (bxi & np.uint32(0xF)).astype(np.int32)
    cx2 = ((bxi.astype(np.int32) << 20) >> 24) * 2
    cy2 = ((byi.astype(np.int32) << 24) >> 24) * 2
    v1x = (bxi & np.uint32(0xFFFFF000)).view(np.float32)
    v1y = (byi & np.uint32(0xFFFFFF00)).view(np.float32)
    v1x = np.where(m != 0, np.where(np.abs(v1x) <= np.float32(0.0625), np.float32(0.0), v1x), F[..., 0])
    v1y = np.where(m != 0, np.where(np.abs(v1y) <= np.float32(0.0625), np.float32(0.0), v1y), F[..., 1])
    F8 = np.zeros((nby * 2, nbx * 2, 2), np.float32)
    for qbit in range(4):
        use2 = ((m >> qbit) & 1) != 0
        qx, qy = qbit & 1, qbit >> 1
        F8[qy::2, qx::2, 0] = np.where(use2, cx2.astype(np.float32), v1x)
        F8[qy::2, qx::2, 1] = np.where(use2, cy2.astype(np.float32), v1y)
    return F8


def mf8(L1p, L1c, FT, GK, V2, gm, fade, mp=None, prevF=None):
    mp = mp or {}
    if prevF is None:
        prevF = mp.get("prevF")
    nby, nbx = FT.shape[:2]
    bx, by = _grid(nbx, nby)
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
    cap = np.where(nfgk, mp.get("cap", 32), 8)
    lsh = np.where(nfgk, mp.get("lsh", 2), 1)
    h1, w1 = L1c.shape
    wm8, hm8 = w1 - 8, h1 - 8
    r_idx = np.arange(8)[None, :]

    def ldT(ox, oy):
        x0 = bx + ox
        y0 = by + oy
        x = np.where(x0 < 0, min(1, nbx - 1), np.where(x0 >= nbx, max(nbx - 2, 0), x0))
        y = np.where(y0 < 0, min(1, nby - 1), np.where(y0 >= nby, max(nby - 2, 0), y0))
        return FT[y, x]

    nbrs = [ldT(ox, oy) for oy in (-1, 0, 1) for ox in (-1, 0, 1)]                # t0..t8 raster, t4 = own
    outer_offs = [(0, -2), (0, 2), (-2, 0), (2, 0), (-1, -2), (1, 2)]             # sub 10..15: 2-hop outer contour ring
    outer_nbrs = [ldT(ox, oy) for ox, oy in outer_offs]
    nbq = [(rnd(v[:, 0] * np.float32(0.5)), rnd(v[:, 1] * np.float32(0.5))) for v in nbrs]
    if prevF is not None:
        fp0x, fp0y = prevF[by, bx, 0], prevF[by, bx, 1]
        upx, upy = rnd(fp0x * np.float32(0.0625)), rnd(fp0y * np.float32(0.0625))
        up_nz = (upx != 0) | (upy != 0)
        ubx, uby = np.clip(bx + upx, 0, 119), np.clip(by + upy, 0, 67)
        fpux, fpuy = prevF[uby, ubx, 0], prevF[uby, ubx, 1]
        tpx_u, tpy_u = rnd(fpux * np.float32(0.5)), rnd(fpuy * np.float32(0.5))
        tpx_a = rnd((fpux * np.float32(2.0) - fp0x) * np.float32(0.5))
        tpy_a = rnd((fpuy * np.float32(2.0) - fp0y) * np.float32(0.5))
    else:
        up_nz = np.zeros(N, bool)
        tpx_u = tpy_u = tpx_a = tpy_a = np.zeros(N, np.int64)
    best = np.full(N, BIG, np.int64)
    winv = np.zeros((N, 2), np.float32)
    wincx = np.zeros(N, np.int64)
    wincy = np.zeros(N, np.int64)
    winq = np.zeros((N, 4), np.int64)
    cand_cx, cand_cy, cand_q, cand_ap, cand_val = [], [], [], [], []
    for sub in range(16):
        if sub == 0:
            cv = nbrs[4].copy()
        elif sub <= 8:
            k = sub - 1 if sub - 1 < 4 else sub                                    # skip the centre (k = 4)
            cv = nbrs[k].copy()
        elif sub == 9:
            cv = np.where(notanc[:, None], np.array([gmx4, gmy4], np.float32)[None, :], nbrs[4])
        else:
            cv = outer_nbrs[sub - 10].copy()
        cx, cy = rnd(cv[:, 0] * np.float32(0.5)), rnd(cv[:, 1] * np.float32(0.5))
        aoff = np.abs(cx - av[:, 0]) + np.abs(cy - av[:, 1])
        sc = np.abs(cx - gmx2) + np.abs(cy - gmy2)
        afar = (aoff > 3) & ((aoff > 16) | ((~still) & (sc * 2 <= aoff)))
        ap = isanc & afar
        terms = [np.minimum(np.abs(cx - nx) + np.abs(cy - ny), cap) for nx, ny in nbq]
        sm0 = sum(terms[j] for j in (0, 1, 2, 3, 5, 6, 7, 8))
        sm = sm0 + np.where(nfgk, terms[4] * 3, 0)
        pen = sm << lsh
        # Outward disocclusion/occlusion shift: when evaluating a camera-aligned background candidate
        # (sc <= 3) at a high-gradient motion boundary (sm0 >= 16), shift the bilateral SAD window
        # up to 5 L1 px away from the higher-disagreement (foreground) neighbor so the occluded tap
        # samples clean background instead of the foreground object's edge.
        bg_c = (sc <= 3) & notanc & (sm0 >= 16)
        shx_d = np.clip(((terms[3] - terms[5]) * 5) >> 3, -5, 5)
        shy_d = np.clip(((terms[1] - terms[7]) * 5) >> 3, -5, 5)
        # Only shift to the CUR grid when evaluating an anchor escape candidate (so static heads reject sky escape);
        # keep all valid moving objects, articulated limbs, and normal blocks on the MID grid matching me1.
        scx = np.where(ap, cx >> 1, np.where(bg_c, shx_d, 0))
        scy = np.where(ap, cy >> 1, np.where(bg_c, shy_d, 0))
        Xu, Yu = X + scx, Y + scy
        dxc = np.clip(cx, -wm8, wm8)
        dyc = np.clip(cy, -hm8, hm8)
        xc = np.clip(Xu - (dxc >> 1), np.maximum(0, -dxc), np.minimum(wm8, wm8 - dxc))
        xp = xc + dxc
        ybc = np.clip(Yu - (dyc >> 1), np.maximum(0, -dyc), np.minimum(hm8, hm8 - dyc))
        ybp = ybc + dyc
        p_blk = _blocks(L1p, xp, ybp[:, None] + r_idx)
        c_blk = _blocks(L1c, xc, ybc[:, None] + r_idx)
        adiff = np.abs(p_blk - c_blk)
        q00 = adiff[:, 0:4, 0:4].sum(axis=(1, 2))
        q10 = adiff[:, 0:4, 4:8].sum(axis=(1, 2))
        q01 = adiff[:, 4:8, 0:4].sum(axis=(1, 2))
        q11 = adiff[:, 4:8, 4:8].sum(axis=(1, 2))
        qs = np.stack([q00, q10, q01, q11], axis=-1)
        raw_sad = q00 + q10 + q01 + q11
        sad0 = _dc_adjust(raw_sad, p_blk.sum(axis=(1, 2)), c_blk.sum(axis=(1, 2)))
        # Partial-occlusion trim: when 1+ quadrant has a clean match (qmin <= 88), discount up to 3/4 of excess SAD
        qmin = qs.min(axis=-1)
        qexc = np.maximum(sad0 - (qmin * 4 + 256), 0)
        qtrim = qexc - (qexc >> 2)
        sad = np.minimum(sad0 - np.where(qmin <= 88, qtrim, 0), 65535)
        dtu = np.abs(cx - tpx_u) + np.abs(cy - tpy_u)
        dta = np.abs(cx - tpx_a) + np.abs(cy - tpy_a)
        tbias = np.where(up_nz & (np.minimum(dtu, dta) <= 2), 16, 0)
        bias = (np.where(hasfg & ((sc > 3) | (sm0 < 16)), 96, 0) if sub == 0 else 0) + tbias
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
        if fade:
            valid1 = np.full(N, sub == 0)
        else:
            valid1 = np.full(N, sub < 10)                                          # Pass 1 uses sub < 10
        key = np.where(valid1, key, BIG)
        better = key < best
        best = np.where(better, key, best)
        winv = np.where(better[:, None], cv, winv)
        wincx = np.where(better, cx, wincx)
        wincy = np.where(better, cy, wincy)
        winq = np.where(better[:, None], qs, winq)
        cand_cx.append(cx)
        cand_cy.append(cy)
        cand_q.append(qs)
        cand_ap.append(ap)
        cand_val.append(np.full(N, sub == 0) if fade else np.ones(N, bool))

    # Pass 2: Dual-layer 8x8 quadrant contour refinement across all 16 lanes (sub = 0..15)
    best2 = np.full(N, BIG, np.int64)
    win_mask = np.zeros(N, np.int64)
    win_cx2 = np.zeros(N, np.int64)
    win_cy2 = np.zeros(N, np.int64)
    sc1 = np.abs(wincx - gmx2) + np.abs(wincy - gmy2)
    winq_max = winq.max(axis=-1)
    for sub in range(16):
        cx, cy = cand_cx[sub], cand_cy[sub]
        qs = cand_q[sub]
        d12 = np.abs(cx - wincx) + np.abs(cy - wincy)
        sc = np.abs(cx - gmx2) + np.abs(cy - gmy2)
        qmin = qs.min(axis=-1)
        qmax = qs.max(axis=-1)
        qmin_ok = (qmin <= 32) | ((winq_max >= 32) & (qmin <= 96)) | ((winq_max >= 384) & (qmin <= 148))
        lock = (sc1 <= 3) & (sc > 4) & qmin_ok & (qmax >= 160) & ((qmax - qmin) >= 112)
        q_direct = (qs + 24 < winq) & (qs <= 160) & ((sc[:, None] > 3) | (winq >= 160))
        q_win = q_direct | (lock[:, None] & (qs <= 192) & (qs * 4 <= qmax[:, None] * 3))
        mask = (q_win[:, 0].astype(np.int64) |
                (q_win[:, 1].astype(np.int64) << 1) |
                (q_win[:, 2].astype(np.int64) << 2) |
                (q_win[:, 3].astype(np.int64) << 3))
        ok = cand_val[sub] & (~cand_ap[sub]) & (d12 >= 3) & (mask > 0) & (mask < 15) & qmin_ok
        c2 = qmin + 256
        k2 = np.where(ok, (c2 << 8) | sub, BIG)
        b2 = k2 < best2
        best2 = np.where(b2, k2, best2)
        win_mask = np.where(b2, mask, win_mask)
        win_cx2 = np.where(b2, np.clip(cx, -128, 127), win_cx2)
        win_cy2 = np.where(b2, np.clip(cy, -128, 127), win_cy2)

    # Canonicalize dual layers on split blocks so v1 is always the background layer (closer to gm)
    # and v2 (mask bit = 1) is always the foreground contour layer (farther from gm)
    sc2_win = np.abs(win_cx2 - gmx2) + np.abs(win_cy2 - gmy2)
    swap_l = (win_mask > 0) & (sc1 > sc2_win)
    win_mask = np.where(swap_l, win_mask ^ 0xF, win_mask)
    old_cx1 = np.clip(wincx, -128, 127)
    old_cy1 = np.clip(wincy, -128, 127)
    winv = np.where(swap_l[:, None], np.stack([win_cx2 * 2, win_cy2 * 2], axis=-1).astype(np.float32), winv)
    win_cx2 = np.where(swap_l, old_cx1, win_cx2)
    win_cy2 = np.where(swap_l, old_cy1, win_cy2)

    vx = winv[:, 0].copy()
    vy = winv[:, 1].copy()
    has_split = win_mask > 0
    vx = np.where(has_split & (np.abs(vx) < np.float32(0.0625)), np.float32(0.0625), vx)
    vy = np.where(has_split & (np.abs(vy) < np.float32(0.0625)), np.float32(0.0625), vy)
    bxi = vx.view(np.uint32)
    byi = vy.view(np.uint32)
    bxi_hom = bxi & np.uint32(0xFFFFFFF0)
    bxi_spl = ((bxi & np.uint32(0xFFFFF000)) |
               ((win_cx2.astype(np.uint32) & np.uint32(0xFF)) << np.uint32(4)) |
               (win_mask.astype(np.uint32) & np.uint32(0xF)))
    byi_spl = (byi & np.uint32(0xFFFFFF00)) | (win_cy2.astype(np.uint32) & np.uint32(0xFF))
    vx_out = np.where(has_split, bxi_spl, bxi_hom).astype(np.uint32).view(np.float32)
    vy_out = np.where(has_split, byi_spl, byi).astype(np.uint32).view(np.float32)
    return np.stack([vx_out, vy_out], axis=-1).reshape(nby, nbx, 2)


# ---------------------------------------------------------------------------------------------------------------
# R13: Temporal Pyramid Ping-Pong Cache & Warm Single-Frame Fast Path
# ---------------------------------------------------------------------------------------------------------------
FG8_FLAG_PYR_WARM = 0x1000


def pyr8_shader(rgb):
    """Exact 1:1 port of classic/shaders/pyr8.ll:
    2x2 hardware bilinear box mean -> sqrt((r + 2g + b) * 0.25) -> unquantized float LDS cascade -> u8 floor(x*255 + 0.5).
    """
    f = np.asarray(rgb[..., :3], np.float32)
    H, W = f.shape[:2]
    q = (f[0:H:2, 0:W:2] + f[0:H:2, 1:W:2] + f[1:H:2, 0:W:2] + f[1:H:2, 1:W:2]) * np.float32(0.25)
    l1 = np.sqrt(np.maximum((q[..., 0] + np.float32(2.0) * q[..., 1] + q[..., 2]) * np.float32(0.25), np.float32(0.0)))

    def down(x):
        h, w = x.shape
        xp = np.pad(x, ((0, h % 2), (0, w % 2)), mode='edge')
        return (xp[0::2, 0::2] + xp[0::2, 1::2] + xp[1::2, 0::2] + xp[1::2, 1::2]) * np.float32(0.25)

    def q8(x):
        return np.clip(np.floor(x * np.float32(255.0) + np.float32(0.5)), 0.0, 255.0).astype(np.int64)

    l2 = down(l1)
    l3 = down(l2)
    return q8(l1), q8(l2), q8(l3)


def me3_dual(L3p, L3c, prevF=None, dc_comp=True):
    """me3 with R13 packed (sv_p << 16) | sv_c luma sum reduction for both L3c (sumc) and L3p (sump)."""
    V3, sumc = me3(L3p, L3c, prevF=prevF, dc_comp=dc_comp)
    h, w = L3p.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = _grid(nbx, nby)
    X, Y = bx * 8, by * 8
    r = np.arange(8)[None, :]
    sump = int(_blocks(L3p, np.clip(X, 0, w - 8), np.clip(Y[:, None] + r, 0, h - 1)).sum())
    return V3, sumc, sump


def _pack_slot(L1, L2, L3):
    """Pack pyramid levels with both (L1, L2, L3) and (L0, L1, L2, E) views for verification tests."""
    return {
        "L1": L1,
        "L2": L2,
        "L3": L3,
        "L0": L1,
        "E": L3,
    }


class PyramidPingPongCache:
    """Host + GPU state machine for the R13 Temporal Pyramid Ping-Pong Cache (classic/source/main.c + pyr8.ll + me3.ll).

    - Tracks `last_cur_base`, `last_w`, `last_h`, and `parity` (0 = Bank A cur / Bank B prev, 1 = Bank B cur / Bank A prev).
    - On a Warm Hit (`prev_base != 0` and `prev_base == last_cur_base` and resolution unchanged):
      - Toggles `parity ^= 1` and sets `FG8_FLAG_PYR_WARM` (`0x1000`).
      - `pyr8.ll` builds ONLY `T#cur` into `slots[parity]` and zeroes only `W8_MISC[2 + parity]`, cutting pyramid work by 50%.
      - `slots[parity ^ 1]` (`L1prev..L3prev`) and `W8_MISC[2 + (parity ^ 1)]` (`sump`) are reused directly from the previous
        frame's `cur` slot — Zero extra DirectMemory and Zero contamination from in-place OSD/RCAS passes on frame N-1.
    - On a Cold Miss (first frame, skipped frame / discontinuity, scene-cut reset, or resolution change):
      - Resets `parity = 0` and clears `FG8_FLAG_PYR_WARM`.
      - `pyr8.ll` builds BOTH `T#cur` -> `slots[0]` and `T#prev` -> `slots[1]`, and zeroes both `W8_MISC[2..3]`.
      - `me3.ll` accumulates BOTH `sumc -> W8_MISC[2 + 0]` and `sump -> W8_MISC[2 + 1]`.
    """

    def __init__(self, use_shader_luma=True):
        self.use_shader_luma = use_shader_luma
        self.slots = [None, None]
        self.misc_luma_sums = [0, 0]
        self.last_cur_base = 0
        self.last_w = 0
        self.last_h = 0
        self.parity = 0
        self.warm_hits = 0
        self.cold_misses = 0

    def invalidate(self):
        self.last_cur_base = 0
        self.last_w = 0
        self.last_h = 0

    def _build_pyr(self, rgb):
        fn = pyr8_shader if self.use_shader_luma else pyr8
        L1, L2, L3 = fn(rgb)
        return _pack_slot(L1, L2, L3)

    def step(self, prev_rgb, cur_rgb, prev_base, cur_base, width=None, height=None,
             prevF=None, anchor_hist=None, mp=None, force_cold=False):
        h, w = cur_rgb.shape[:2]
        if width is not None:
            w = int(width)
        if height is not None:
            h = int(height)

        pyr_warm = (
            not force_cold
            and prev_base != 0
            and prev_base == self.last_cur_base
            and w == self.last_w
            and h == self.last_h
            and self.slots[self.parity] is not None
        )
        r = (self.parity ^ 1) if pyr_warm else 0
        self.parity = r
        self.last_cur_base = int(cur_base)
        self.last_w = w
        self.last_h = h
        flags = FG8_FLAG_PYR_WARM if pyr_warm else 0

        # pyr8.ll execution
        self.slots[r] = self._build_pyr(cur_rgb)
        self.misc_luma_sums[r] = 0
        if pyr_warm:
            self.warm_hits += 1
            pyr_evals = 1
        else:
            self.cold_misses += 1
            self.slots[r ^ 1] = self._build_pyr(prev_rgb)
            self.misc_luma_sums[r ^ 1] = 0
            pyr_evals = 2

        slot_prev = self.slots[r ^ 1]
        slot_cur = self.slots[r]
        L1p, L2p, L3p = slot_prev["L1"], slot_prev["L2"], slot_prev["L3"]
        L1c, L2c, L3c = slot_cur["L1"], slot_cur["L2"], slot_cur["L3"]

        # me3.ll execution
        V3, sum_c, sum_p = me3_dual(L3p, L3c, prevF=prevF)
        self.misc_luma_sums[r] += sum_c
        if not pyr_warm:
            self.misc_luma_sums[r ^ 1] += sum_p

        lumasum_cur = self.misc_luma_sums[r]
        lumasum_prev = self.misc_luma_sums[r ^ 1]

        # me2 -> gm8 -> fgs8 -> me1 -> mf8
        V2, S2 = me2(L2p, L2c, V3, prevF=prevF)
        gm = gm8(V2, S2)
        GK, new_hist = fgs8(L2p, L2c, V2, S2, gm, anchor_hist=anchor_hist)
        FT, cutn, fade = me1(L1p, L1c, V2, GK, gm, lumasum_cur, lumasum_prev, prevF=prevF, mp=mp)
        F = mf8(L1p, L1c, FT, GK, V2, gm, fade, mp=mp)

        return {
            "pyr_warm": pyr_warm,
            "flags": flags,
            "parity": r,
            "pyr_evals": pyr_evals,
            "slot_prev": slot_prev,
            "slot_cur": slot_cur,
            "L1p": L1p,
            "L2p": L2p,
            "L3p": L3p,
            "L1c": L1c,
            "L2c": L2c,
            "L3c": L3c,
            "lumasum_prev": lumasum_prev,
            "lumasum_cur": lumasum_cur,
            "V3": V3,
            "V2": V2,
            "S2": S2,
            "gm": gm,
            "GK": GK,
            "anchor_hist": new_hist,
            "FT": FT,
            "F": F,
            "cutn": cutn,
            "fade": fade,
        }
