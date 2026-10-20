"""Bounded dense Lucas-Kanade correction, admitted by source RGB match improvement.

Input-only PC experiment. Full-resolution solves are not a PS4 cost estimate.
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
import vcfg_warp as warp
from experiment_grid8 import refine
from experiment_edge_stability import gradient
from inspect_candidate import luma,hf


def dense(a,b,field):
    h,w=a.shape[:2];v,u=np.mgrid[:h,:w].astype(np.float32)+.5
    seed=warp.bil(field,u/8,v/8);f=seed.copy()
    ga=np.stack(gradient(luma(a)),-1);gb=np.stack(gradient(luma(b)),-1)
    def samples(f):
        return warp.bil(a,u+f[...,0]*.5,v+f[...,1]*.5),warp.bil(b,u-f[...,0]*.5,v-f[...,1]*.5)
    ap,bp=samples(f);initial=warp.boxf(np.abs(ap-bp).mean(-1),3)
    for _ in range(3):
        gp=(warp.bil(ga,u+f[...,0]*.5,v+f[...,1]*.5)+warp.bil(gb,u-f[...,0]*.5,v-f[...,1]*.5))*.5
        gx,gy=gp[...,0],gp[...,1];residual=luma(ap)-luma(bp)
        xx=warp.boxf(gx*gx,3)+.0001;yy=warp.boxf(gy*gy,3)+.0001;xy=warp.boxf(gx*gy,3)
        xr=warp.boxf(gx*residual,3);yr=warp.boxf(gy*residual,3);det=xx*yy-xy*xy
        dx=(xy*yr-yy*xr)/det;dy=(xy*xr-xx*yr)/det
        step=np.stack([dx,dy],-1);step=np.clip(step,-1,1)
        proposal=seed+np.clip(f+step-seed,-3,3)
        pa,pb=samples(proposal)
        new=warp.boxf(np.abs(pa-pb).mean(-1),3);old=warp.boxf(np.abs(ap-bp).mean(-1),3)
        take=new<old
        f=np.where(take[...,None],proposal,f)
        ap=np.where(take[...,None],pa,ap);bp=np.where(take[...,None],pb,bp)
    final=warp.boxf(np.abs(ap-bp).mean(-1),3)
    moving=np.clip((np.abs(seed).sum(-1)-1)/4,0,1)
    confidence=np.clip((initial-final-.003)*16,0,.5)*moving
    confidence[np.abs(a-b).max(-1)<1/255]=0
    pre=ref.quant((ap+bp)*.5,1023)
    post=ref.quant(warp.rcas8(pre,warp.rcas_con(100),0),1023)
    return post,confidence


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    dest=pathlib.Path('bench/dense_edges');dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes());rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))):
        split='train' if i%2==0 else 'validation'
        if args.split not in ('all',split):continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(f'bench/clear_grid_mix/{name}_gain0.05.npy')
        post,confidence=dense(a,b,refine(a,b,states[i]))
        mid=ref.quant(base+(post-base)*confidence[...,None],1023)
        np.save(dest/f'{name}_dense.npy',mid)
        score=metrics.frame_metrics(a,gt,b,mid,None);bm=metrics.frame_metrics(a,gt,b,base,None)
        row=dict(frame=name,split=split,metrics=score,baseline=bm,delta={k:score[k]-bm[k] for k in score},
                 confidence_mean=float(confidence.mean()),baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt))
        rows.append(row);print(json.dumps(row),flush=True)
        (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={part:{k:float(np.mean([r[part][k] for r in rows])) for k in rows[0][part]} for part in ('metrics','baseline','delta')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2));print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
