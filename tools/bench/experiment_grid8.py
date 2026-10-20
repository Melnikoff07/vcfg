"""PC-only 8x8 motion refinement; no shader or console changes.

Uses cached, locally generated motion states (pickle: trusted local files only).
GT is read only by the scoring stage, never by motion refinement.
Example: python tools/bench/experiment_grid8.py --clip bench/clips/CUSA00557_full1
  --states bench/state_full1_warm.pkl --out bench/grid8_warm --lpips
The warm cache is an approximation, not a warm-history hardware capture.
"""
import argparse
import hashlib
import json
import pathlib
import pickle
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref
import vcfg_warp as warp


def refine(a, b, state, penalty=4.0, temporal=None, temporal_penalty=0.0):
    """4x4 L1 SAD (8x8 full pixels), seeded by 3x3 old-grid vectors.

    Retain float seed vectors, then search +/-1 L1 pixel around the winner.
    The penalty discourages displacement from the inherited vector. This is
    a seeded refinement experiment, not a complete me1/mf8 shader port.
    Optional temporal arguments are PC experiments only; defaults reproduce
    the isolated gridmix GPU refinement exactly.
    """
    la, lb = ref.pyr8(a)[0], ref.pyr8(b)[0]
    h, w = la.shape
    by, bx = np.indices(((h + 3) // 4, (w + 3) // 4))
    bx, by = bx.ravel(), by.ravel()
    x, y = bx * 4, by * 4
    field = state['F']
    iy, ix = np.minimum(by // 2, field.shape[0] - 1), np.minimum(bx // 2, field.shape[1] - 1)
    seed = field[iy, ix].copy()
    prior = None if temporal is None else np.asarray(temporal, np.float32).reshape(-1, 2)
    if prior is not None and prior.shape != seed.shape:
        raise ValueError('fine temporal field geometry mismatch')
    # Luma pyramid samples are integers, matching the existing SAD convention.
    def sad(v):
        dx, dy = np.floor(v[:, 0] * .5 + .5).astype(int), np.floor(v[:, 1] * .5 + .5).astype(int)
        xp, xc = np.clip(x - ((-dx) >> 1), 0, w - 4), np.clip(x - (dx >> 1), 0, w - 4)
        r = np.arange(4)
        yp = np.clip((y - ((-dy) >> 1))[:, None] + r, 0, h - 1)
        yc = np.clip((y - (dy >> 1))[:, None] + r, 0, h - 1)
        av = la[yp[:, :, None], xp[:, None, None] + r]
        bv = lb[yc[:, :, None], xc[:, None, None] + r]
        return np.abs(av - bv).sum((1, 2))

    def cost(v):
        result = sad(v) + penalty * np.abs(v - seed).sum(-1) * .5
        if prior is not None:
            result = result + temporal_penalty * np.abs(v - prior).sum(-1) * .5
        return result

    best, winner = cost(seed), seed.copy()
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            v = field[np.clip(iy + oy, 0, field.shape[0] - 1), np.clip(ix + ox, 0, field.shape[1] - 1)]
            score = cost(v)
            take = score < best
            best, winner = np.minimum(best, score), np.where(take[:, None], v, winner)
    if prior is not None:
        score = cost(prior)
        take = score < best
        best, winner = np.minimum(best, score), np.where(take[:, None], prior, winner)
    centre = np.floor(winner * .5 + .5).astype(int)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            v = ((centre + (ox, oy)) * 2).astype(np.float32)
            score = cost(v)
            take = score < best
            best, winner = np.minimum(best, score), np.where(take[:, None], v, winner)
    return winner.reshape((h + 3) // 4, (w + 3) // 4, 2).astype(np.float32)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--clip', type=pathlib.Path, required=True)
    ap.add_argument('--states', type=pathlib.Path, required=True)
    ap.add_argument('--out', type=pathlib.Path, required=True)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--lpips', action='store_true')
    args = ap.parse_args()
    if not args.lpips:
        metrics.lpips_score = lambda x, y: float('nan')
    triples = list(metrics.triplets(args.clip))
    states = pickle.loads(args.states.read_bytes())
    if len(states) != len(triples):
        raise ValueError('cache/triplet count mismatch')
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    p = dict(prev_luma_sum=None, warp_flags=8192, sharpness=40, enhance=1)
    for i, (name, pa, pg, pb) in enumerate(triples[:args.limit]):
        a, gt, b = (metrics.load(path).astype(np.float32) for path in (pa, pg, pb))
        state = states[i]
        if state['F'].shape[:2] != ((a.shape[0] + 15) // 16, (a.shape[1] + 15) // 16):
            raise ValueError('cache geometry mismatch')
        f8 = refine(a, b, state)
        inherited = np.repeat(np.repeat(state['F'], 2, axis=0), 2, axis=1)[:f8.shape[0], :f8.shape[1]]
        for method, field in [('baseline16', state['F']), ('inherited8', inherited), ('refined8', f8)]:
            start = time.perf_counter()
            st = dict(state, F=field)
            params = dict(p, wp={'block': 16 if method == 'baseline16' else 8})
            mid = ref.render(a, b, st, params)
            np.save(args.out / f'{name}_{method}.npy', mid)
            hud_path = args.clip / 'hud_mask.png'
            hud = metrics.load(hud_path).mean(-1) > .5 if hud_path.exists() else None
            values = metrics.frame_metrics(a, gt, b, mid, hud)
            row = dict(frame=name, method=method, seconds=time.perf_counter() - start, **values)
            rows.append(row)
            print(json.dumps(row), flush=True)
        (args.out / 'partial.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    means = {}
    for method in ('baseline16', 'inherited8', 'refined8'):
        selected = [r for r in rows if r['method'] == method]
        means[method] = {key: float(np.mean([r[key] for r in selected]))
                         for key in selected[0] if key not in ('frame', 'method')}
    result = dict(clip=str(args.clip.resolve()), states=str(args.states.resolve()),
                  states_sha256=hashlib.sha256(args.states.read_bytes()).hexdigest(),
                  parameters=p, frames=len(rows) // 3, summary=means, per_frame=rows)
    (args.out / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(means, indent=2), flush=True)


if __name__ == '__main__':
    main()
