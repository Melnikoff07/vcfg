"""Check the existing bit-16 static guard against a fixed cached baseline."""
import argparse
import json
import pathlib
import pickle
import sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split', choices=['train', 'validation', 'all'], default='train')
    ap.add_argument('--lpips', action='store_true')
    args = ap.parse_args()
    if not args.lpips:
        metrics.lpips_score = lambda x, y: float('nan')
    clip = pathlib.Path('bench/clips/CUSA00557_full1')
    dest = pathlib.Path('bench/static_guard_warm')
    dest.mkdir(parents=True, exist_ok=True)
    states = pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes())
    rows = []
    for i, (name, pa, pg, pb) in enumerate(metrics.triplets(clip)):
        split = 'train' if i % 2 == 0 else 'validation'
        if args.split not in (split, 'all'):
            continue
        a, gt, b = [metrics.load(p).astype(np.float32) for p in (pa, pg, pb)]
        baseline = np.load(pathlib.Path('bench/grid8_warm') / f'{name}_baseline16.npy')
        cached = dest / f'{name}_mid.npy'
        if cached.exists():
            mid = np.load(cached)
        else:
            mid = ref.render(a, b, states[i], dict(warp_flags=8192 | 16, sharpness=40, enhance=1))
            np.save(cached, mid)
        score = metrics.frame_metrics(a, gt, b, mid, None)
        base = metrics.frame_metrics(a, gt, b, baseline, None)
        row = dict(frame=name, split=split, metrics=score, baseline=base,
                   delta={k:score[k]-base[k] for k in score})
        rows.append(row)
        print(json.dumps(row), flush=True)
        (dest / f'{args.split}_partial.json').write_text(json.dumps(rows, indent=2))
    summary = {part:{k:float(np.mean([r[part][k] for r in rows])) for k in rows[0][part]}
               for part in ('metrics','baseline','delta')}
    (dest / f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
