"""Reproject and validate generated-frame history only on moving edges.

This is a temporal reconstruction filter without engine jitter/depth/vectors,
not a complete TAA implementation. Independent short sequences are required.
"""
import argparse
import json
import pathlib
import pickle
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics
from vcfg_warp import bil
from vcfg_ref import quant
from experiment_edge_stability import gradient
from inspect_candidate import luma,hf


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sequence',type=pathlib.Path,required=True)
    ap.add_argument('--strengths',nargs='+',type=float,default=[.25,.5,.75])
    ap.add_argument('--lpips',action='store_true')
    ap.add_argument('--result-name',default='results.json')
    args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    meta=json.loads((args.sequence/'results.json').read_text());clip=pathlib.Path(meta['clip'])
    dest=args.sequence/'history_edges';dest.mkdir(exist_ok=True)
    prev={};previous_base=prev_gt=None;rows=[];transitions=[]
    for i,(name,pa,pg,pb) in enumerate(list(metrics.triplets(clip))[:len(meta['rows'])//4]):
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(args.sequence/f'{name}_gridmix_b.npy')
        st=pickle.loads((args.sequence/f'{name}_state.pkl').read_bytes())
        h,w=base.shape[:2];v,u=np.mgrid[:h,:w].astype(np.float32)+.5
        f=bil(st['F'],u/16,v/16);ru,rv=u+f[...,0],v+f[...,1]
        moving=np.clip((np.abs(f).sum(-1)-1)/4,0,1)
        gx,gy=gradient(luma(base));edge=np.clip((np.hypot(gx,gy)-.01)/.06,0,1)
        static=np.abs(a-b).max(-1)<1/255
        valid=(ru>=.5)&(ru<w-.5)&(rv>=.5)&(rv<h-.5)
        p=np.pad(base,((1,1),(1,1),(0,0)),mode='edge')
        neighbors=[p[y:y+h,x:x+w] for y in range(3) for x in range(3)]
        lo=np.minimum.reduce(neighbors);hi=np.maximum.reduce(neighbors)
        bm=metrics.frame_metrics(a,gt,b,base,None)
        if prev_gt is not None:
            scoremask=valid&(np.abs(bil(prev_gt,ru,rv)-gt).mean(-1)<.08)
        for strength in args.strengths:
            key=str(strength)
            if key not in prev:mid=base.copy();weight=np.zeros((h,w),np.float32)
            else:
                history=bil(prev[key],ru,rv)
                reliability=np.clip(1-np.abs(history-base).max(-1)/.15,0,1)
                weight=strength*moving*edge*reliability*valid
                weight[static]=0
                history=np.clip(history,lo,hi)
                mid=quant(base+(history-base)*weight[...,None],1023)
            score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,strength=strength,scored=i>0,metrics=score,baseline=bm,
                     delta={k:score[k]-bm[k] for k in score},weight_mean=float(weight.mean()),
                     baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt))
            rows.append(row);print(json.dumps(row),flush=True)
            if i>1:
                error=mid-gt;prior=prev[key]-prev_gt
                baseline_error=base-gt;baseline_prior=previous_base-prev_gt
                transitions.append(dict(frame=name,strength=strength,coverage=float(scoremask.mean()),
                    residual_delta=float(np.abs(error-prior).mean()),
                    aligned_residual_delta=float(np.abs(error-bil(prior,ru,rv))[scoremask].mean()),
                    baseline_residual_delta=float(np.abs(baseline_error-baseline_prior).mean()),
                    baseline_aligned_residual_delta=float(np.abs(baseline_error-bil(baseline_prior,ru,rv))[scoremask].mean())))
            prev[key]=mid;np.save(dest/f'{name}_history{strength:g}.npy',mid)
        prev_gt=gt;previous_base=base
    summary={}
    for strength in args.strengths:
        selected=[r for r in rows if r['strength']==strength and r['scored']]
        ts=[r for r in transitions if r['strength']==strength]
        summary[str(strength)]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]} for part in ('metrics','baseline','delta')}
        summary[str(strength)]['diagnostics']={k:float(np.mean([r[k] for r in selected])) for k in ('weight_mean','baseline_hf','candidate_hf')}
        summary[str(strength)]['temporal']={k:float(np.mean([r[k] for r in ts])) for k in ts[0] if k not in ('frame','strength')}
    (dest/args.result_name).write_text(json.dumps(dict(rows=rows,transitions=transitions,summary=summary),indent=2));print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
