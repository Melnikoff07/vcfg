"""Consecutive external-video check with warm motion history and residual deltas.

Full-size 50 Hz source triples represent 25->50, not console 30->60. This is
an independent content check, not proof of in-game edge stability. The input
motion field is reused for the approximate temporal scoring alignment only.
"""
import argparse
import hashlib
import json
import pathlib
import pickle
import sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref
import vcfg_warp as warp
from experiment_grid8 import refine
from experiment_clear_profile import sharpen
from experiment_edge_stability import setup
from inspect_candidate import luma, hf


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--clip', required=True, type=pathlib.Path)
    ap.add_argument('--out', required=True, type=pathlib.Path)
    ap.add_argument('--count', type=int, default=6)
    ap.add_argument('--lpips', action='store_true')
    args = ap.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    if not args.lpips: metrics.lpips_score = lambda x,y: float('nan')
    prev_field = prev_b = prev_gt = None
    previous = {}; rows = []; transitions = []; hashes = {}
    for i,(name,pa,pg,pb) in enumerate(list(metrics.triplets(args.clip))[:args.count]):
        a,gt,b = [metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        for p in (pa,pg,pb): hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        if prev_b is not None and not np.array_equal(a,prev_b):
            raise ValueError('sequence is not consecutive: current A differs from preceding B')
        cache = args.out/f'{name}_state.pkl'
        if cache.exists(): st = pickle.loads(cache.read_bytes())
        else:
            st = ref.motion(a,b,dict(prev_luma_sum=None,prevF=prev_field))
            cache.write_bytes(pickle.dumps(st))
        outputs = {}; errors = {}
        for block in (16,8):
            path = args.out/f'{name}_grid{block}.npz'
            if path.exists():
                data = np.load(path); outputs[block],errors[block] = data['post'],data['error']
            else:
                f = st['F'] if block==16 else refine(a,b,st)
                dbg = {}; pre = warp.warp8(a,b,f,st['GK'],st['cutn'],flags=8192,wp={'block':block},dbg=dbg)
                post = ref.quant(warp.rcas8(ref.quant(pre,1023),warp.rcas_con(100),0),1023)
                err = warp.boxf(np.concatenate(dbg['emov']),3)
                outputs[block],errors[block] = post,err
                np.savez(path,post=post,error=err)
        static = np.abs(a-b).max(-1)<1/255
        base = sharpen((outputs[16]+outputs[8])*.5,.05); base[static] = outputs[16][static]
        weight = .5+.4*np.tanh((errors[16]-errors[8])*12)
        confidence = sharpen(outputs[16]+(outputs[8]-outputs[16])*weight[...,None],.05)
        confidence[static] = base[static]
        mask,tangent,_ = setup(a,b,base,st['F'])
        edge = ref.quant(base+(tangent-base)*(mask*.5)[...,None],1023)
        mids = dict(grid16=outputs[16],gridmix_b=base,confidence12=confidence,tangent50=edge)
        for method,mid in mids.items():
            np.save(args.out/f'{name}_{method}.npy',mid)
            row = dict(frame=name,method=method,scored=i>0,metrics=metrics.frame_metrics(a,gt,b,mid,None),hf=hf(mid)/hf(gt))
            rows.append(row); print(json.dumps(row),flush=True)
        if prev_gt is not None and i>1:
            h,w = a.shape[:2]; v,u = np.mgrid[:h,:w].astype(np.float32)+.5
            f = warp.bil(st['F'],u/16,v/16)
            ru,rv = u+f[...,0],v+f[...,1]
            gt_reprojected = warp.bil(prev_gt,ru,rv)
            valid = (ru>=.5)&(ru<w-.5)&(rv>=.5)&(rv<h-.5)
            valid &= np.abs(gt_reprojected-gt).mean(-1)<.08
            for method,mid in mids.items():
                error = mid-gt
                prior = previous[method]-prev_gt
                reprojected = warp.bil(prior,ru,rv)
                transitions.append(dict(frame=name,method=method,coverage=float(valid.mean()),
                    residual_delta=float(np.abs(error-prior).mean()),
                    aligned_residual_delta=float(np.abs(error-reprojected)[valid].mean())))
        previous = mids; prev_gt = gt; prev_b = b; prev_field = st['F']
        (args.out/'partial.json').write_text(json.dumps(dict(rows=rows,transitions=transitions),indent=2))
    summary = {}
    for method in mids:
        selected = [r for r in rows if r['method']==method and r['scored']]
        ts = [r for r in transitions if r['method']==method]
        summary[method] = dict(metrics={k:float(np.mean([r['metrics'][k] for r in selected])) for k in selected[0]['metrics']},
                              hf=float(np.mean([r['hf'] for r in selected])),
                              temporal={k:float(np.mean([r[k] for r in ts])) for k in ('coverage','residual_delta','aligned_residual_delta')})
    result = dict(clip=str(args.clip.resolve()),source_hashes=hashes,first_pair_excluded_for_warmup=True,
                  rows=rows,transitions=transitions,summary=summary)
    (args.out/'results.json').write_text(json.dumps(result,indent=2)); print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__': main()
