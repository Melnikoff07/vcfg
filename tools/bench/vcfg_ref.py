"""NumPy port of VCFG classic mode 8 (pyr8 -> me3 -> me2 -> gm8 -> fgs8 -> me1 -> mf8 -> phase8 -> warp8 -> rcas8).

Ported from classic/shaders/*.ll for the benchmark (docs/BENCHMARK.md section 4). Target: the same frame as the
console's `mid` for a captured triplet (cold history: temporal field zero, anchor history zero). Integer stages are
exact ports, float stages use float32; the GPU's 1/256 texel quantisation in the bilinear sampler is not modelled.

    mid = run(A, B)          A, B: float32 (H, W, 3) in 0..1 (codes / max code). Returns the quantised mid (H, W, 3).

Capture-build caveat: the capture build never runs me3 for the A frame, so the luma sum of the previous frame used
by the fade detector is 0 (params['prev_luma_sum'] = 0): a nearly still camera then forces a zero field (plain
blend + post pass). Pass prev_luma_sum=None to use the real sum of A (what the game does in normal play).
"""
import numpy as np

import vcfg_motion as vm
import vcfg_warp as vw

PARAMS = dict(
    qmax=1023.0,            # scanout format code range (1023 for 2_10_10_10, 255 for 8888)
    sharpness=40,           # profile sharpness, percent
    enhance=1,              # profile Enhance AA mode 0 / 1 / 2
    prev_luma_sum=0,        # None = sum of the previous frame's L3 (normal play); 0 = capture build
    t=0.5,
)


def _q8(l):
    return np.minimum(np.floor(l * np.float32(255.0) + np.float32(0.5)), np.float32(255.0))


def pyr8(frame):
    """luma pyramid: L1 (H/2 x W/2), L2, L3 as int64 u8 codes. luma = sqrt((r + 2 g + b) / 4) of the 2x2 box mean;
    L2 / L3 are 2x2 means of the UNQUANTISED level below (LDS), each stored with floor(x * 255 + 0.5)."""
    f = frame[..., :3].astype(np.float32)
    H, W = f.shape[:2]
    q = (f[0:H:2, 0:W:2] + f[0:H:2, 1:W:2] + f[1:H:2, 0:W:2] + f[1:H:2, 1:W:2]) * np.float32(0.25)
    l1 = np.sqrt((q[..., 0] + np.float32(2.0) * q[..., 1] + q[..., 2]) * np.float32(0.25))

    def down(x):
        h, w = x.shape
        xp = np.pad(x, ((0, h % 2), (0, w % 2)), mode='edge')
        return (xp[0::2, 0::2] + xp[0::2, 1::2] + xp[1::2, 0::2] + xp[1::2, 1::2]) * np.float32(0.25)

    l2 = down(l1)
    l3 = down(l2)
    return tuple(_q8(x).astype(np.int64) for x in (l1, l2, l3))


def quant(x, qmax):
    return (np.rint(np.clip(x, 0.0, 1.0) * np.float32(qmax)) / np.float32(qmax)).astype(np.float32)


def motion(A, B, params=None):
    """all motion stages (pyr8 .. mf8): returns the state dict that render() needs (cacheable per triplet)"""
    p = dict(PARAMS)
    if params:
        p.update(params)
    L1a, L2a, L3a = pyr8(A)
    L1b, L2b, L3b = pyr8(B)
    V3, sum_b = vm.me3(L3a, L3b)
    V2, S2 = vm.me2(L2a, L2b, V3)
    gm = vm.gm8(V2, S2)
    GK, hist = vm.fgs8(L2a, L2b, V2, S2, gm)
    sum_a = int(L3a.sum()) if p['prev_luma_sum'] is None else p['prev_luma_sum']
    FT, cutn, fade = vm.me1(L1a, L1b, V2, GK, gm, sum_b, sum_a)
    F = vm.mf8(L1a, L1b, FT, GK, V2, gm, fade)
    return dict(V3=V3, V2=V2, S2=S2, gm=gm, GK=GK, FT=FT, F=F, cutn=cutn, fade=fade)


def render(A, B, st, params=None, return_all=False):
    """warp8 + rcas8 + quantisation from a motion() state"""
    p = dict(PARAMS)
    if params:
        p.update(params)
    mid = vw.warp8(A, B, st['F'], st['GK'], st['cutn'], t=p['t'], flags=p.get('warp_flags', 0), flags_cut_thr=p.get('cut_thr', 0), cut_emov=p.get('cut_emov', 0.3), wp=p.get('wp'))
    mid_q = quant(mid, p['qmax'])
    out = quant(vw.rcas8(mid_q, vw.rcas_con(p['sharpness']), p['enhance']) if (p['sharpness'] > 0 or p['enhance'] > 0) else mid_q,
                p['qmax'])
    if return_all:
        return out, dict(st, pre=mid_q)
    return out


def run(A, B, params=None, return_all=False):
    st = motion(A, B, params)
    return render(A, B, st, params, return_all)
