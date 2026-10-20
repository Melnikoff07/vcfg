"""Regularize fine-grid motion against advected history, without filtering colors.

Global camera change is removed from the history prior. History is a candidate,
not a forced result; the original input SAD and coarse-field penalty still apply.
"""
import argparse
import json
import pathlib
import pickle
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref
from vcfg_warp import bil
from experiment_grid8 import refine
from experiment_clear_profile import sharpen
from inspect_candidate import hf


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sequence',required=True,type=pathlib.Path)
    ap.add_argument('--penalties',nargs='+',type=float,default=[1,4])
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    meta=json.loads((args.sequence/'results.json').read_text());clip=pathlib.Path(meta['clip'])
    dest=args.sequence/'fine_history';dest.mkdir(exist_ok=True)
    prior_fields={};previous={};previous_base=previous_gt=previous_gm=None;rows=[];transitions=[]
    for i,(name,pa,pg,pb) in enumerate(list(metrics.triplets(clip))[:len(meta['rows'])//4]):
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        st=pickle.loads((args.sequence/f'{name}_state.pkl').read_bytes())
        base=np.load(args.sequence/f'{name}_gridmix_b.npy');coarse=np.load(args.sequence/f'{name}_grid16.npz')['post']
        h,w=base.shape[:2];v,u=np.mgrid[:h,:w].astype(np.float32)+.5
        f=bil(st['F'],u/16,v/16);ru,rv=u+f[...,0],v+f[...,1]
        static=np.abs(a-b).max(-1)<1/255;bm=metrics.frame_metrics(a,gt,b,base,None)
        gm=np.array(st['gm'],np.float32)*4
        if previous_gt is not None:
            valid=(ru>=.5)&(ru<w-.5)&(rv>=.5)&(rv<h-.5)
            valid &= np.abs(bil(previous_gt,ru,rv)-gt).mean(-1)<.08
        for penalty in args.penalties:
            key=str(penalty)
            if key not in prior_fields:
                field=refine(a,b,st);mid=base.copy()
            else:
                ph,pw=prior_fields[key].shape[:2]
                yy,xx=np.mgrid[:ph,:pw].astype(np.float32)*8+4
                coarse_motion=bil(st['F'],xx/16,yy/16)
                prior=bil(prior_fields[key],(xx+coarse_motion[...,0])/8,(yy+coarse_motion[...,1])/8)
                prior=prior+(gm-previous_gm)
                field=refine(a,b,st,temporal=prior,temporal_penalty=penalty)
                fine=ref.render(a,b,dict(st,F=field),dict(warp_flags=8192,sharpness=100,enhance=0,wp={'block':8}))
                mid=sharpen((coarse+fine)*.5,.05);mid[static]=base[static]
            score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,penalty=penalty,scored=i>0,metrics=score,baseline=bm,
                     delta={k:score[k]-bm[k] for k in score},baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt))
            rows.append(row);print(json.dumps(row),flush=True)
            if i>1:
                err=mid-gt;priorerr=previous[key]-previous_gt;be=base-gt;bp=previous_base-previous_gt
                transitions.append(dict(frame=name,penalty=penalty,coverage=float(valid.mean()),
                    aligned_residual_delta=float(np.abs(err-bil(priorerr,ru,rv))[valid].mean()),
                    baseline_aligned_residual_delta=float(np.abs(be-bil(bp,ru,rv))[valid].mean())))
            prior_fields[key]=field;previous[key]=mid
            np.save(dest/f'{name}_penalty{penalty:g}.npy',mid)
        previous_gt=gt;previous_base=base;previous_gm=gm
    summary={}
    for penalty in args.penalties:
        selected=[r for r in rows if r['penalty']==penalty and r['scored']];ts=[r for r in transitions if r['penalty']==penalty]
        summary[str(penalty)]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]} for part in ('metrics','baseline','delta')}
        summary[str(penalty)]['diagnostics']={k:float(np.mean([r[k] for r in selected])) for k in ('baseline_hf','candidate_hf')}
        summary[str(penalty)]['temporal']={k:float(np.mean([r[k] for r in ts])) for k in ts[0] if k not in ('frame','penalty')}
    (dest/'results.json').write_text(json.dumps(dict(rows=rows,transitions=transitions,summary=summary),indent=2));print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
