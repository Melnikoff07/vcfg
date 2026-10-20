"""Phase-consistent midpoint SAD and subpixel refinement for the fine field.

The integer search places odd L1 displacements at a half-pixel shifted patch
center. This prototype compares both inputs at a common midpoint using bilinear
L1 samples. It tests a hypothesis; it does not establish the user's artifact cause.
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
from experiment_clear_profile import sharpen
from inspect_candidate import hf


def refine_symmetric(a,b,state):
    la,lb=(ref.pyr8(x)[0].astype(np.float32)[...,None] for x in (a,b))
    h,w=la.shape[:2];yy,xx=np.indices(((h+3)//4,(w+3)//4));shape=yy.shape
    yy,xx=yy.ravel(),xx.ravel();iy=np.minimum(yy//2,state['F'].shape[0]-1);ix=np.minimum(xx//2,state['F'].shape[1]-1)
    seed=state['F'][iy,ix];r=np.arange(4,dtype=np.float32)
    oy,ox=np.meshgrid(r,r,indexing='ij')
    u=xx[:,None]*4+ox.ravel()[None,:]+.5;v=yy[:,None]*4+oy.ravel()[None,:]+.5
    def cost(f):
        dx=f[:,0,None]*.25;dy=f[:,1,None]*.25
        av=bil(la,u+dx,v+dy);bv=bil(lb,u-dx,v-dy)
        return np.abs(av-bv).sum((1,2))+np.abs(f-seed).sum(-1)*2
    best=cost(seed);winner=seed.copy()
    for dy in (-1,0,1):
        for dx in (-1,0,1):
            f=state['F'][np.clip(iy+dy,0,state['F'].shape[0]-1),np.clip(ix+dx,0,state['F'].shape[1]-1)]
            c=cost(f);take=c<best;best=np.minimum(best,c);winner=np.where(take[:,None],f,winner)
    centre=np.floor(winner*.5+.5)*2
    for dy in (-2,0,2):
        for dx in (-2,0,2):
            f=centre+np.array([dx,dy],np.float32);c=cost(f);take=c<best
            best=np.minimum(best,c);winner=np.where(take[:,None],f,winner)
    updates=[]
    for axis in (0,1):
        d=np.zeros(2,np.float32);d[axis]=2
        left,right=cost(winner-d),cost(winner+d)
        denom=left+right-2*best
        fraction=np.clip((left-right)/(2*np.maximum(denom,1e-6)),-.5,.5)
        updates.append(np.where(denom>1e-3,fraction*2,0))
    shifted=winner+np.stack(updates,-1)
    # A real cost check prevents the quadratic approximation worsening the fit.
    shifted=np.where((cost(shifted)<best)[:,None],shifted,winner)
    return {k:f.reshape(*shape,2).astype(np.float32) for k,f in [('symmetric',winner),('subpixel',shifted)]}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--variants',nargs='+',default=['symmetric','subpixel'])
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    dest=pathlib.Path('bench/symmetric_refine');dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes());rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))):
        split='train' if i%2==0 else 'validation'
        if args.split not in ('all',split):continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(f'bench/clear_grid_mix/{name}_gain0.05.npy');coarse=np.load(f'bench/clear_grid_mix/{name}_grid16.npy')
        fields=refine_symmetric(a,b,states[i]);bm=metrics.frame_metrics(a,gt,b,base,None);static=np.abs(a-b).max(-1)<1/255
        for variant in args.variants:
            np.save(dest/f'{name}_{variant}_field.npy',fields[variant])
            fine=ref.render(a,b,dict(states[i],F=fields[variant]),dict(warp_flags=8192,sharpness=100,enhance=0,wp={'block':8}))
            mid=sharpen((coarse+fine)*.5,.05);mid[static]=base[static]
            np.save(dest/f'{name}_{variant}.npy',mid);score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,split=split,variant=variant,metrics=score,baseline=bm,
                     delta={k:score[k]-bm[k] for k in score},baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt))
            rows.append(row);print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for variant in args.variants:
        selected=[r for r in rows if r['variant']==variant]
        summary[variant]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]} for part in ('metrics','baseline','delta')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2));print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
