"""VCFG benchmark metrics (see docs/BENCHMARK.md).

Usage: python -I tools/bench/metrics.py <clip_dir> <method_dir> [<method_dir> ...] [--csv out.csv] [--crossfade]
  clip_dir:   NNNN_a.png, NNNN_gt.png, NNNN_b.png (+ optional hud_mask.png)
  method_dir: NNNN_mid.png for every triplet (the method's generated frame)
LPIPS is computed only when torch and lpips are installed.
"""
import argparse
import csv
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pngio import load_rgb

import numpy as np


def load(p):
    return load_rgb(p)


def psnr(x, y, mask=None):
    d = (x - y) ** 2
    if mask is not None:
        if not mask.any():
            return float('nan')
        d = d[mask]
    mse = float(d.mean())
    return 99.0 if mse <= 1e-12 else 10.0 * np.log10(1.0 / mse)


def _gauss(sigma=1.5, n=11):
    x = np.arange(n) - n // 2
    g = np.exp(-(x ** 2) / (2 * sigma ** 2))
    return g / g.sum()


def _blur(img, g):
    pad = len(g) // 2
    p = np.pad(img, pad, mode='reflect')
    t = np.apply_along_axis(lambda r: np.convolve(r, g, mode='valid'), 1, p)
    return np.apply_along_axis(lambda c: np.convolve(c, g, mode='valid'), 0, t)


def ssim(x, y):
    lx = x @ np.array([0.299, 0.587, 0.114])
    ly = y @ np.array([0.299, 0.587, 0.114])
    g = _gauss()
    mx, my = _blur(lx, g), _blur(ly, g)
    sxx = _blur(lx * lx, g) - mx * mx
    syy = _blur(ly * ly, g) - my * my
    sxy = _blur(lx * ly, g) - mx * my
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    s = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
    return float(s.mean())


_lpips = None


def lpips_score(x, y):
    global _lpips
    try:
        import torch
        import lpips
    except ImportError:
        return float('nan')
    if _lpips is None:
        _lpips = lpips.LPIPS(net='alex', verbose=False)
    tx = torch.from_numpy(x.transpose(2, 0, 1)[None].astype(np.float32) * 2 - 1)
    ty = torch.from_numpy(y.transpose(2, 0, 1)[None].astype(np.float32) * 2 - 1)
    with torch.no_grad():
        return float(_lpips(tx, ty).item())


def frame_metrics(a, gt, b, m, hud):
    static = np.abs(a - b).max(-1) < (1.0 / 255.0)
    flick_mask = hud if hud is not None else static
    r = {
        'PSNR': psnr(m, gt),
        'SSIM': ssim(m, gt),
        'LPIPS': lpips_score(m, gt),
        'PSNR_HUD': psnr(m, gt, hud) if hud is not None else float('nan'),
        'PSNR_static': psnr(m, gt, static),
        'HUD_flicker': float(np.abs(m - gt)[flick_mask].mean() * 100.0) if flick_mask.any() else float('nan'),
        # temporal consistency error: how far the steps A -> M -> B differ from the true steps A -> GT -> B
        'TCE': float((np.abs(np.abs(m - a) - np.abs(gt - a)).mean() + np.abs(np.abs(b - m) - np.abs(b - gt)).mean()) * 100.0),
    }
    return r


def triplets(clip):
    for gt in sorted(clip.glob('*_gt.png')):
        n = gt.name[:-len('_gt.png')]
        yield n, clip / f'{n}_a.png', gt, clip / f'{n}_b.png'


def evaluate(clip, method_dir, crossfade=False):
    hud_p = clip / 'hud_mask.png'
    hud = None
    rows = []
    for n, pa, pg, pb in triplets(clip):
        a, gt, b = load(pa), load(pg), load(pb)
        if hud is None and hud_p.exists():
            hud = load(hud_p).mean(-1) > 0.5
        if crossfade:
            m = (a + b) / 2.0
        else:
            pm = method_dir / f'{n}_mid.png'
            if not pm.exists():
                raise FileNotFoundError(f'missing {pm}; partial-method scores are refused')
            m = load(pm)
        if a.shape != gt.shape or b.shape != gt.shape or m.shape != gt.shape:
            raise ValueError(f'geometry mismatch in triplet {n}')
        if hud is not None and hud.shape != gt.shape[:2]:
            raise ValueError('HUD mask geometry mismatch')
        rows.append(frame_metrics(a, gt, b, m, hud))
    if not rows:
        return None
    return {k: float(np.nanmean([r[k] for r in rows])) if not all(np.isnan(r[k]) for r in rows) else float('nan')
            for k in rows[0]} | {'frames': len(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('clip')
    ap.add_argument('methods', nargs='*')
    ap.add_argument('--csv')
    ap.add_argument('--crossfade', action='store_true', help='add the (A+B)/2 baseline')
    args = ap.parse_args()
    clip = pathlib.Path(args.clip)
    results = []
    if args.crossfade:
        results.append(('cross-fade', evaluate(clip, None, crossfade=True)))
    for m in args.methods:
        results.append((pathlib.Path(m).name, evaluate(clip, pathlib.Path(m))))
    keys = ['PSNR', 'SSIM', 'LPIPS', 'PSNR_HUD', 'PSNR_static', 'HUD_flicker', 'TCE', 'frames']
    print('method'.ljust(22) + ''.join(k.rjust(13) for k in keys))
    for name, r in results:
        if r is None:
            print(name.ljust(22) + '  (no frames)')
            continue
        print(name.ljust(22) + ''.join((f'{r[k]:13.3f}' if k != 'frames' else f'{r[k]:13d}') for k in keys))
    if args.csv:
        with open(args.csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['method'] + keys)
            for name, r in results:
                if r:
                    w.writerow([name] + [r[k] for k in keys])


if __name__ == '__main__':
    main()
