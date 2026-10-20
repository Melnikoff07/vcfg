"""Fixed train/validation split for PC warp candidates; GT only enters scoring."""
import argparse
import json
import pathlib
import pickle
import sys
import time
import hashlib
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref

VARIANTS = {
    'cr': {'slow_cr': True},
    'cr_fast12': {'slow_cr': True, 'fast_bil': 12},
    'layer_best3': {'slow_cr': True, 'slow_select': 'best', 'e_win': 3},
    'layer_top2_3': {'slow_cr': True, 'slow_select': 'top2', 'e_win': 3},
    'cr_limit12': {'slow_cr': True, 'slow_cr_max': 12, 'fast_bil': 12},
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--clip', type=pathlib.Path, default=pathlib.Path('bench/clips/CUSA00557_full1'))
    ap.add_argument('--states', type=pathlib.Path, default=pathlib.Path('bench/state_full1_warm.pkl'))
    ap.add_argument('--out', type=pathlib.Path, default=pathlib.Path('bench/layers_warm'))
    ap.add_argument('--split', choices=['train', 'validation', 'all'], default='train')
    ap.add_argument('--variants', nargs='+', choices=list(VARIANTS), default=list(VARIANTS))
    ap.add_argument('--lpips', action='store_true')
    args = ap.parse_args()
    if not args.lpips:
        metrics.lpips_score = lambda x, y: float('nan')
    states = pickle.loads(args.states.read_bytes())
    triples = list(metrics.triplets(args.clip))
    if len(states) != len(triples):
        raise ValueError('cache count mismatch')
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (name, pa, pg, pb) in enumerate(triples):
        split = 'train' if i % 2 == 0 else 'validation'
        if args.split not in (split, 'all'):
            continue
        a, gt, b = [metrics.load(p).astype(np.float32) for p in (pa, pg, pb)]
        hudfile = args.clip / 'hud_mask.png'
        hud = metrics.load(hudfile).mean(-1) > .5 if hudfile.exists() else None
        # Same cached baseline as the first experiment; never use it as GT.
        baseline = np.load(pathlib.Path('bench/grid8_warm') / f'{name}_baseline16.npy')
        bm = metrics.frame_metrics(a, gt, b, baseline, hud)
        for variant in args.variants:
            start = time.perf_counter()
            dest = args.out / f'{name}_{variant}.npy'
            if dest.exists():
                mid = np.load(dest)
            else:
                mid = ref.render(a, b, states[i], dict(warp_flags=8192, sharpness=40, enhance=1, wp=VARIANTS[variant]))
                np.save(dest, mid)
            score = metrics.frame_metrics(a, gt, b, mid, hud)
            row = dict(frame=name, split=split, variant=variant, seconds=time.perf_counter()-start,
                       metrics=score, baseline=bm, delta={k: score[k]-bm[k] for k in score})
            rows.append(row)
            print(json.dumps(row), flush=True)
            (args.out / f'{args.split}_partial.json').write_text(json.dumps(rows, indent=2))
    summary = {}
    for variant in args.variants:
        rs = [r for r in rows if r['variant'] == variant]
        summary[variant] = {part: {k:float(np.mean([r[part][k] for r in rs])) for k in rs[0][part]}
                            for part in ('metrics', 'baseline', 'delta')}
    provenance = {p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in
                  ('tools/bench/vcfg_warp.py', 'tools/bench/vcfg_ref.py', 'tools/bench/experiment_layers.py', str(args.states))}
    result = dict(split=args.split, variants={v:VARIANTS[v] for v in args.variants},
                  provenance=provenance, rows=rows, summary=summary)
    (args.out / f'{args.split}_results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
