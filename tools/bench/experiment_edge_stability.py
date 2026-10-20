"""Input-only moving-edge filter screening; sparse triples cannot prove shimmer reduction.

GT is used only for scoring. Baseline is the deployed gridmix b algorithm.
The even/odd split reuses a previously explored capture, not unseen scenes.
"""
import argparse
import json
import pathlib
import pickle
import sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import metrics
from inspect_candidate import luma, hf
from vcfg_warp import bil
from vcfg_ref import quant


def cross(x):
    p = np.pad(x, ((1, 1), (1, 1)), mode='edge')
    return (4*x+p[:-2, 1:-1]+p[2:, 1:-1]+p[1:-1, :-2]+p[1:-1, 2:])*np.float32(.125)


def gradient(x):
    p = np.pad(x, ((1, 1), (1, 1)), mode='edge')
    return (p[1:-1, 2:]-p[1:-1, :-2])*.5, (p[2:, 1:-1]-p[:-2, 1:-1])*.5


def setup(a, b, base, field):
    h, w = a.shape[:2]
    v, u = np.mgrid[:h, :w].astype(np.float32)+.5
    f = bil(field, u/16, v/16)
    ap = bil(a, u+f[..., 0]*.5, v+f[..., 1]*.5)
    bp = bil(b, u-f[..., 0]*.5, v-f[..., 1]*.5)
    # Fine detail disagreement after compensation detects an unstable source
    # signal; coarse exposure/color mismatch should not alone trigger AA.
    la, lb = luma(ap), luma(bp)
    disagreement = np.abs((la-cross(la))-(lb-cross(lb)))
    gx, gy = gradient(cross(luma(base)))
    norm = np.sqrt(gx*gx+gy*gy)+1e-6
    tx, ty = -gy/norm, gx/norm
    edge = np.clip((norm-.012)/.06, 0, 1)
    moving = np.clip((np.abs(f).sum(-1)-1)/4, 0, 1)
    unstable = np.clip((disagreement-.004)/.035, 0, 1)
    mask = (edge*moving*unstable).astype(np.float32)
    mask[np.abs(a-b).max(-1)<1/255] = 0
    tangent = (bil(base, u+tx*.75, v+ty*.75)+bil(base, u-tx*.75, v-ty*.75))*.5
    normal = (bil(base, u+gx/norm*.75, v+gy/norm*.75)+bil(base, u-gx/norm*.75, v-gy/norm*.75))*.5
    return mask, tangent, normal


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split', choices=['train', 'validation', 'all'], default='train')
    ap.add_argument('--variants', nargs='+', default=['tangent25', 'tangent50', 'tangent100', 'normal50'])
    ap.add_argument('--lpips', action='store_true')
    args = ap.parse_args()
    if not args.lpips: metrics.lpips_score = lambda x, y: float('nan')
    dest = pathlib.Path('bench/edge_stability'); dest.mkdir(parents=True, exist_ok=True)
    states = pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes())
    rows = []
    for i, (name, pa, pg, pb) in enumerate(metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))):
        split = 'train' if i%2 == 0 else 'validation'
        if args.split not in ('all', split): continue
        a, gt, b = [metrics.load(p).astype(np.float32) for p in (pa, pg, pb)]
        base = np.load(f'bench/clear_grid_mix/{name}_gain0.05.npy')
        mask, tangent, normal = setup(a, b, base, states[i]['F'])
        bm = metrics.frame_metrics(a, gt, b, base, None)
        # Evaluation mask is fixed by GT gradient and input motion, never used
        # to construct the output. Gradient error measures edge accuracy only.
        gg = gradient(luma(gt)); edge = np.hypot(*gg)>.04
        def edge_error(x):
            g = gradient(luma(x))
            return float((np.abs(g[0]-gg[0])+np.abs(g[1]-gg[1]))[edge].mean())
        for variant in args.variants:
            direction = tangent if variant.startswith('tangent') else normal
            strength = int(''.join(c for c in variant if c.isdigit()))/100
            mid = quant(base+(direction-base)*(mask*strength)[..., None], 1023)
            np.save(dest/f'{name}_{variant}.npy', mid)
            score = metrics.frame_metrics(a, gt, b, mid, None)
            row = dict(frame=name, split=split, variant=variant, metrics=score, baseline=bm,
                       delta={k:score[k]-bm[k] for k in score}, mask_mean=float(mask.mean()),
                       baseline_edge_error=edge_error(base), candidate_edge_error=edge_error(mid),
                       baseline_hf=hf(base)/hf(gt), candidate_hf=hf(mid)/hf(gt),
                       max_static_change=float(np.abs(mid-base)[np.abs(a-b).max(-1)<1/255].max(initial=0)))
            rows.append(row); print(json.dumps(row), flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows, indent=2))
    summary = {}
    for variant in args.variants:
        selected = [r for r in rows if r['variant']==variant]
        summary[variant] = {part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]}
                            for part in ('metrics', 'baseline', 'delta')}
        summary[variant]['diagnostics'] = {k:float(np.mean([r[k] for r in selected])) for k in (
            'mask_mean','baseline_edge_error','candidate_edge_error','baseline_hf','candidate_hf','max_static_change')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows, summary=summary), indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__': main()
