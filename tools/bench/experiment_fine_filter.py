"""Filter isolated fine-grid vectors with a vector medoid and optional SAD guard.

This explores the outlier-removal stage described by AMD Optical Flow, adapted
to our seeded midpoint field. No game vectors/depth or GT enter this filter.
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
from experiment_grid8 import refine
from experiment_clear_profile import sharpen
from inspect_candidate import hf


def filter_field(a,b,field,guard):
    h,w=field.shape[:2]
    p=np.pad(field,((1,1),(1,1),(0,0)),mode='edge')
    candidates=np.stack([p[y:y+h,x:x+w] for y in range(3) for x in range(3)])
    costs=np.stack([np.abs(candidates-v).sum((0,3)) for v in candidates])
    order=np.argmin(costs,axis=0)
    med=np.take_along_axis(candidates,order[None,...,None],axis=0)[0]
    if guard:
        la,lb=ref.pyr8(a)[0],ref.pyr8(b)[0]
        yy,xx=np.indices((h,w));x=xx.ravel()*4;y=yy.ravel()*4;r=np.arange(4)
        def sad(v):
            v=v.reshape(-1,2);dx,dy=np.floor(v*.5+.5).astype(int).T
            xp=np.clip(x-((-dx)>>1),0,la.shape[1]-4)
            xc=np.clip(x-(dx>>1),0,la.shape[1]-4)
            yp=np.clip((y-((-dy)>>1))[:,None]+r,0,la.shape[0]-1)
            yc=np.clip((y-(dy>>1))[:,None]+r,0,la.shape[0]-1)
            return np.abs(la[yp[:,:,None],xp[:,None,None]+r]-lb[yc[:,:,None],xc[:,None,None]+r]).sum((1,2)).reshape(h,w)
        accept=sad(med)<=sad(field)+16
        med=np.where(accept[...,None],med,field)
    return med.astype(np.float32)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--variants',nargs='+',default=['medoid','guarded'])
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    dest=pathlib.Path('bench/fine_filter');dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes());rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))):
        split='train' if i%2==0 else 'validation'
        if args.split not in ('all',split):continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(f'bench/clear_grid_mix/{name}_gain0.05.npy')
        coarse=np.load(f'bench/clear_grid_mix/{name}_grid16.npy');fine=refine(a,b,states[i])
        bm=metrics.frame_metrics(a,gt,b,base,None);static=np.abs(a-b).max(-1)<1/255
        for variant in args.variants:
            filtered=filter_field(a,b,fine,variant=='guarded')
            new=ref.render(a,b,dict(states[i],F=filtered),dict(warp_flags=8192,sharpness=100,enhance=0,wp={'block':8}))
            mid=sharpen((coarse+new)*.5,.05);mid[static]=base[static]
            np.save(dest/f'{name}_{variant}.npy',mid)
            score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,split=split,variant=variant,metrics=score,baseline=bm,
                     delta={k:score[k]-bm[k] for k in score},changed_vectors=float((filtered!=fine).any(-1).mean()),
                     baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt))
            rows.append(row);print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for variant in args.variants:
        selected=[r for r in rows if r['variant']==variant]
        summary[variant]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]} for part in ('metrics','baseline','delta')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2));print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
