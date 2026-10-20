"""Confidence-weighted two-grid reconstruction, scored against deployed gridmix b.

Uses compensated source disagreement, never GT, to adapt the mixing weight.
The median/box support is spatial only: no claim of temporal shimmer reduction.
"""
import argparse
import json
import pathlib
import pickle
import sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_warp as warp
from experiment_grid8 import refine
from experiment_clear_profile import sharpen
from inspect_candidate import hf, luma
from experiment_edge_stability import gradient


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split', choices=['train','validation','all'], default='train')
    ap.add_argument('--variants', nargs='+', default=['soft4','soft12','coarse_guard'])
    ap.add_argument('--lpips', action='store_true')
    args = ap.parse_args()
    if not args.lpips: metrics.lpips_score = lambda x,y: float('nan')
    dest = pathlib.Path('bench/grid_confidence'); dest.mkdir(parents=True, exist_ok=True)
    states = pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes())
    rows = []
    for i, (name, pa, pg, pb) in enumerate(metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))):
        split = 'train' if i%2 == 0 else 'validation'
        if args.split not in ('all', split): continue
        a, gt, b = [metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base = np.load(f'bench/clear_grid_mix/{name}_gain0.05.npy')
        outputs = {block:np.load(f'bench/clear_grid_mix/{name}_grid{block}.npy') for block in (16,8)}
        errors = {}
        for block in (16,8):
            cache = dest/f'{name}_error{block}.npy'
            if cache.exists(): err = np.load(cache)
            else:
                dbg = {}
                f = states[i]['F'] if block==16 else refine(a,b,states[i])
                warp.warp8(a,b,f,states[i]['GK'],states[i]['cutn'],flags=8192,wp={'block':block},dbg=dbg)
                err = np.concatenate(dbg['emov']); np.save(cache, err)
            errors[block] = warp.boxf(err,3)
        static = np.abs(a-b).max(-1)<1/255
        bm = metrics.frame_metrics(a,gt,b,base,None)
        gg = gradient(luma(gt)); edge = np.hypot(*gg)>.04
        def edge_error(x):
            g = gradient(luma(x))
            return float((np.abs(g[0]-gg[0])+np.abs(g[1]-gg[1]))[edge].mean())
        for variant in args.variants:
            delta = errors[16]-errors[8]
            if variant == 'coarse_guard':
                weight = .5-np.clip((-delta-.02)*6,0,.5)
            else:
                scale = float(variant.removeprefix('soft'))
                weight = .5+.4*np.tanh(delta*scale)
            mixed = outputs[16]+(outputs[8]-outputs[16])*weight[...,None]
            mid = sharpen(mixed,.05); mid[static] = base[static]
            np.save(dest/f'{name}_{variant}.npy',mid)
            score = metrics.frame_metrics(a,gt,b,mid,None)
            row = dict(frame=name,split=split,variant=variant,metrics=score,baseline=bm,
                       delta={k:score[k]-bm[k] for k in score},fine_weight=float(weight.mean()),
                       baseline_edge_error=edge_error(base),candidate_edge_error=edge_error(mid),
                       baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt))
            rows.append(row); print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary = {}
    for variant in args.variants:
        selected = [r for r in rows if r['variant']==variant]
        summary[variant] = {part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]}
                            for part in ('metrics','baseline','delta')}
        summary[variant]['diagnostics'] = {k:float(np.mean([r[k] for r in selected])) for k in (
            'fine_weight','baseline_edge_error','candidate_edge_error','baseline_hf','candidate_hf')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__': main()
