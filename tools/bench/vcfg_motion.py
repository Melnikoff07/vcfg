"""Motion stages of the NumPy port of VCFG classic mode 8: me3, me2, gm8, fgs8, me1, mf8 (see vcfg_ref.py).

Everything here is integer logic ported 1:1 from classic/shaders/*.ll: candidate order, bias terms, penalties and
tie-breaks (key = cost << shift | candidate index, minimum wins) are the same as the shaders'.
"""
import numpy as np

BIG = np.iinfo(np.int64).max


# ---------------------------------------------------------------------------------------------------------------
# block SAD helpers
# ---------------------------------------------------------------------------------------------------------------
def _blocks(img, xs, ys):
    """img (h, w); xs (N,) block start x (clamped); ys (N, 8) clamped row indices -> (N, 8, 8)"""
    cols = xs[:, None] + np.arange(8)[None, :]
    return img[ys[:, :, None], cols[:, None, :]]


def sad_cur_grid(prev, cur, X, Y, dx, dy):
    """bsadu: cur block at (X, Y), prev block at (X + dx, Y + dy). x start clamped to [0, w-8], rows clamped one by one
    (bsad(X + floor(d/2)) puts the prev block at X + floor + ceil = X + d and the cur block at X)."""
    h, w = cur.shape
    r = np.arange(8)[None, :]
    X, Y, dx, dy = (np.asarray(a, np.int64) for a in (X, Y, dx, dy))
    xc = np.clip(X, 0, w - 8)
    xp = np.clip(X + dx, 0, w - 8)
    yc = np.clip(Y[:, None] + r, 0, h - 1)
    yp = np.clip((Y + dy)[:, None] + r, 0, h - 1)
    return np.abs(_blocks(prev, xp, yp) - _blocks(cur, xc, yc)).sum(axis=(1, 2))


def sad_bilateral(prev, cur, X, Y, dx, dy):
    """me1 bsad: prev block at X + ceil(d/2), cur block at X - floor(d/2). Returns (sad, (sum_prev + sum_cur) >> 1)."""
    h, w = cur.shape
    r = np.arange(8)[None, :]
    X, Y, dx, dy = (np.asarray(a, np.int64) for a in (X, Y, dx, dy))
    xp = np.clip(X - ((-dx) >> 1), 0, w - 8)
    xc = np.clip(X - (dx >> 1), 0, w - 8)
    yp = np.clip((Y - ((-dy) >> 1))[:, None] + r, 0, h - 1)
    yc = np.clip((Y - (dy >> 1))[:, None] + r, 0, h - 1)
    p = _blocks(prev, xp, yp)
    c = _blocks(cur, xc, yc)
    return np.abs(p - c).sum(axis=(1, 2)), (p.sum(axis=(1, 2)) + c.sum(axis=(1, 2))) >> 1


def rnd(f):
    """rnd(): floor(f + 0.5) clamped to +-4096"""
    return np.clip(np.floor(np.asarray(f, np.float32) + np.float32(0.5)), -4096, 4096).astype(np.int64)


def _grid(nbx, nby):
    bx, by = np.meshgrid(np.arange(nbx), np.arange(nby))
    return bx.ravel().astype(np.int64), by.ravel().astype(np.int64)


def _isqrt(m):
    m = np.asarray(m, np.int64)
    s = np.floor(np.sqrt(m.astype(np.float64))).astype(np.int64)
    s = np.where(s * s > m, s - 1, s)
    return np.where((s + 1) * (s + 1) <= m, s + 1, s)


# ---------------------------------------------------------------------------------------------------------------
# me3 / me2: coarse motion on the CUR grid
# ---------------------------------------------------------------------------------------------------------------
def me3(L3p, L3c, prevF=None):
    """30 x 17 blocks of 8x8 L3 px -> V3 (17, 30, 2) in L3 px; also the sum of the cur L3 frame (misc luma sum)"""
    h, w = L3c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = _grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    tdx = np.zeros(N, np.int64)
    tdy = np.zeros(N, np.int64)
    if prevF is not None:
        tx, ty = np.minimum(bx * 4 + 2, 119), np.minimum(by * 4 + 2, 67)
        tdx = rnd(prevF[ty, tx, 0] * np.float32(0.125))
        tdy = rnd(prevF[ty, tx, 1] * np.float32(0.125))
    zero = np.zeros(N, np.int64)
    s0 = sad_cur_grid(L3p, L3c, X, Y, zero, zero)
    s1 = sad_cur_grid(L3p, L3c, X, Y, tdx, tdy)
    usetemp = (s1 + 256 - 4) < (s0 + 256)   # lane 1 (temporal, bias 4) wins only with a strictly lower key
    cx, cy = np.where(usetemp, tdx, 0), np.where(usetemp, tdy, 0)
    best = np.full(N, BIG, np.int64)
    bk = np.zeros(N, np.int64)
    for k in range(435):
        ox, oy = k % 29 - 14, k // 29 - 7
        s = sad_cur_grid(L3p, L3c, X, Y, cx + ox, cy + oy)
        key = ((s + 256 + 4 * (abs(ox) + abs(oy))) << 9) | k
        better = key < best
        best = np.where(better, key, best)
        bk = np.where(better, k, bk)
    V3 = np.stack([cx + bk % 29 - 14, cy + bk // 29 - 7], -1).reshape(nby, nbx, 2)
    return V3, int(L3c.sum())


def _med4(a, b, c, d):
    lo1, hi1 = np.minimum(a, b), np.maximum(a, b)
    lo2, hi2 = np.minimum(c, d), np.maximum(c, d)
    return np.minimum(np.maximum(lo1, lo2), np.minimum(hi1, hi2))


def me2(L2p, L2c, V3, prevF=None):
    """60 x 34 blocks of 8x8 L2 px -> V2 (34, 60, 2) in L2 px and S2 (34, 60, 2) = (SAD of the best, SAD of zero)"""
    h, w = L2c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = _grid(nbx, nby)
    X, Y = bx * 8, by * 8
    N = bx.size
    n3y, n3x = V3.shape[:2]
    ix0 = np.clip((bx * 4 - 2) >> 3, 0, n3x - 1)
    ix1 = np.minimum(ix0 + 1, n3x - 1)
    iy0 = np.clip((by * 4 - 2) >> 3, 0, n3y - 1)
    iy1 = np.minimum(iy0 + 1, n3y - 1)
    nb = [V3[iy0, ix0], V3[iy0, ix1], V3[iy1, ix0], V3[iy1, ix1]]    # lanes 0..3 (sub & 1 -> x1, sub & 2 -> y1)
    medx = _med4(*[v[:, 0] for v in nb]) * 2
    medy = _med4(*[v[:, 1] for v in nb]) * 2
    tdx = np.zeros(N, np.int64)
    tdy = np.zeros(N, np.int64)
    if prevF is not None:
        tx, ty = np.minimum(bx * 2 + 1, 119), np.minimum(by * 2 + 1, 67)
        tdx = rnd(prevF[ty, tx, 0] * np.float32(0.25))
        tdy = rnd(prevF[ty, tx, 1] * np.float32(0.25))
    zero = np.zeros(N, np.int64)
    cands = [(v[:, 0] * 2, v[:, 1] * 2) for v in nb] + [(tdx, tdy), (zero, zero)]
    best = np.full(N, BIG, np.int64)
    cdx_b, cdy_b, zcost = zero.copy(), zero.copy(), None
    for sub, (cdx, cdy) in enumerate(cands):
        s = sad_cur_grid(L2p, L2c, X, Y, cdx, cdy)
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
        s = sad_cur_grid(L2p, L2c, X, Y, cdx_b + ox, cdy_b + oy)
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
    gs = [sad_cur_grid(L2p, L2c, BX, BY, np.full(N, gmx + ox), np.full(N, gmy + oy))
          for ox, oy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))]
    gsad = np.minimum.reduce(gs)
    good = (sb * 5 <= gsad * 4) & (gsad - sb >= 96)
    q = (vx - gmx) ** 2 + (vy - gmy) ** 2
    vv = vx ** 2 + vy ** 2
    gmv = gmx * gmx + gmy * gmy
    dev = q > (gmv >> 2) + 4
    mov = vv > gmv + 4
    one, zero = np.ones(N, np.int64), np.zeros(N, np.int64)
    tex = sad_cur_grid(L2c, L2c, BX, BY, one, zero) + sad_cur_grid(L2c, L2c, BX, BY, zero, one)
    texp = sad_cur_grid(L2p, L2p, BX + vx, BY + vy, one, zero) + sad_cur_grid(L2p, L2p, BX + vx, BY + vy, zero, one)
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
    with np.errstate(divide='ignore', invalid='ignore'):
        q = (m - p) / den
    o = np.where(c < mn, q, np.float32(0.0))
    return np.clip(o, -0.5, 0.5).astype(np.float32)


def me1(L1p, L1c, V2, GK, gm, lumasum_cur, lumasum_prev, prevF=None):
    """raw field FT (68, 120, 2) float32 (full-res px, mid grid), the unmatched-block count and the fade flag"""
    h, w = L1c.shape
    nbx, nby = (w + 7) // 8, (h + 7) // 8
    bx, by = _grid(nbx, nby)
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
    nbv = [V2[a, b].astype(np.int64) * 2 for a, b in nbi]                      # (N, 2) in L1 px
    zero = np.zeros(N, np.int64)

    def tdv(ex, ey):
        if prevF is None:
            return zero, zero
        tx, ty = np.minimum(bx + ex, 119), np.minimum(by + ey, 67)
        return (rnd(prevF[ty, tx, 0] * np.float32(0.5)), rnd(prevF[ty, tx, 1] * np.float32(0.5)))

    gk = GK[by, bx]
    hasfg = gk != 0
    isfgk = gk >= (1 << 20)
    isanc = hasfg & ~isfgk
    gv = V2.reshape(-1, 2)[gk & 4095].astype(np.int64) * 2
    gdx, gdy = gv[:, 0], gv[:, 1]
    cand = [(nbv[k][:, 0], nbv[k][:, 1]) for k in range(4)]
    cand += [tdv(0, 0), tdv(1, 0), tdv(0, 1)]                                   # slots 4, 5, 6 (temporal)
    cand += [(zero, zero), (gdx, gdy), (np.full(N, gmx2), np.full(N, gmy2))]    # slots 7, 8, 9

    def nbdist(a, b, cdx, cdy):
        v = V2[a, b].astype(np.int64) * 2
        return np.abs(cdx - v[:, 0]) + np.abs(cdy - v[:, 1])

    zs, zsum = sad_bilateral(L1p, L1c, X, Y, zero, zero)                        # lane 7 publishes (sad, mean sum)
    best = np.full(N, BIG, np.int64)
    cx1, cy1 = zero.copy(), zero.copy()
    for sub, (cdx, cdy) in enumerate(cand):
        sad1, sum1 = sad_bilateral(L1p, L1c, X, Y, cdx, cdy)
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
            if ist:                                                              # 8.6 temporal support gate
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
    # phase 2: +-1 around the winner
    best2 = np.full(N, BIG, np.int64)
    bk = np.zeros(N, np.int64)
    for k in range(9):
        ox, oy = k % 3 - 1, k // 3 - 1
        d2x, d2y = cx1 + ox, cy1 + oy
        sad2, sum2 = sad_bilateral(L1p, L1c, X, Y, d2x, d2y)
        p2f = (np.abs(d2x) + np.abs(d2y)) > 8
        p2p = np.where(p2f, np.maximum((np.abs(sum2 - zsum) >> 1) - zs, 0), 0)
        key = ((sad2 + 256 + p2p + 4 * (abs(ox) + abs(oy))) << 8) | k
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
# mf8: robust smoothness filter of the field
# ---------------------------------------------------------------------------------------------------------------
def mf8(L1p, L1c, FT, GK, V2, gm, fade):
    nby, nbx = FT.shape[:2]
    bx, by = _grid(nbx, nby)
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

    nbrs = [ldT(ox, oy) for oy in (-1, 0, 1) for ox in (-1, 0, 1)]                # t0..t8 raster, t4 = own
    nbq = [(rnd(v[:, 0] * np.float32(0.5)), rnd(v[:, 1] * np.float32(0.5))) for v in nbrs]
    best = np.full(N, BIG, np.int64)
    winv = np.zeros((N, 2), np.float32)
    for sub in range(10):
        if sub == 0:
            k = 4
        elif sub <= 8:
            k = sub - 1 if sub - 1 < 4 else sub                                    # skip the centre (k = 4)
        else:
            k = 4
        cv = nbrs[k].copy()
        if sub == 9:
            cv = np.where(isfgk[:, None], np.array([gmx4, gmy4], np.float32)[None, :], cv)
        cx, cy = rnd(cv[:, 0] * np.float32(0.5)), rnd(cv[:, 1] * np.float32(0.5))
        scx = np.where(nfgk, cx >> 1, 0)
        scy = np.where(nfgk, cy >> 1, 0)
        sad, _ = sad_bilateral(L1p, L1c, X + scx, Y + scy, cx, cy)
        terms = [np.minimum(np.abs(cx - nx) + np.abs(cy - ny), cap) for nx, ny in nbq]
        sm = sum(terms[j] for j in (0, 1, 2, 3, 5, 6, 7, 8)) + np.where(nfgk, terms[4] * 3, 0)
        pen = sm << lsh
        bias = np.where(hasfg, 96, 0) if sub == 0 else 0
        aoff = np.abs(cx - av[:, 0]) + np.abs(cy - av[:, 1])
        apen = np.where(hasfg & nfgk & (aoff > 3), 512, 0)
        cost = sad + 256 + pen + apen - bias
        key = (cost << 8) | sub
        if fade:
            valid = np.full(N, sub == 0)
        else:
            valid = np.ones(N, bool)                                               # sub < 10, filter on
        key = np.where(valid, key, BIG)
        better = key < best
        best = np.where(better, key, best)
        winv = np.where(better[:, None], cv, winv)
    return winv.reshape(nby, nbx, 2)
