"""Two-grid interpolation under the existing clear profile (100 / AA off).

Exploratory PC prototype. Explicitly re-renders the baseline under the same
profile. It does not imply in-game timing, stability or HUD validation.
"""
import argparse
import json
import pathlib
import pickle
import hashlib
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref
from experiment_grid8 import refine
from inspect_candidate import hf,luma


def sharpen(x,amount):
    p=np.pad(x,((1,1),(1,1),(0,0)),mode='edge')
    n,s,w,e=p[:-2,1:-1],p[2:,1:-1],p[1:-1,:-2],p[1:-1,2:]
    lo=np.minimum.reduce((x,n,s,w,e));hi=np.maximum.reduce((x,n,s,w,e))
    return ref.quant(np.clip(x+np.float32(amount)*(x-(n+s+w+e)*np.float32(.25)),lo,hi),1023)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--gains',nargs='+',type=float,default=[0,.05,.1])
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    clip=pathlib.Path('bench/clips/CUSA00557_full1')
    dest=pathlib.Path('bench/clear_grid_mix');dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes())
    rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(clip)):
        split='train' if i%2==0 else 'validation'
        if args.split not in (split,'all'):continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        st=states[i]
        outputs={}
        for block in (16,8):
            cached=dest/f'{name}_grid{block}.npy'
            if cached.exists():mid=np.load(cached)
            else:
                f=st['F'] if block==16 else refine(a,b,st)
                mid=ref.render(a,b,dict(st,F=f),dict(warp_flags=8192,sharpness=100,enhance=0,wp={'block':block}))
                np.save(cached,mid)
            outputs[block]=mid
        base=outputs[16];mixed=(base+outputs[8])*np.float32(.5)
        bm=metrics.frame_metrics(a,gt,b,base,None)
        static=np.abs(a-b).max(-1)<1/255
        bright=luma(gt)>.6
        for gain in args.gains:
            mid=sharpen(mixed,gain)
            mid[static]=base[static]
            np.save(dest/f'{name}_gain{gain:g}.npy',mid)
            score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,split=split,gain=gain,metrics=score,baseline=bm,
                     baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt),
                     baseline_dark=float(((luma(gt)-luma(base)>.2)&bright).mean()),
                     candidate_dark=float(((luma(gt)-luma(mid)>.2)&bright).mean()),
                     delta={k:score[k]-bm[k] for k in score})
            rows.append(row);print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for gain in args.gains:
        selected=[r for r in rows if r['gain']==gain]
        summary[str(gain)]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]}
                           for part in ('metrics','baseline','delta')}
        summary[str(gain)]['diagnostics']={k:float(np.mean([r[k] for r in selected]))
                                          for k in ('baseline_hf','candidate_hf','baseline_dark','candidate_dark')}
    sources=['tools/bench/experiment_clear_profile.py','tools/bench/experiment_grid8.py',
             'tools/bench/vcfg_warp.py','tools/bench/vcfg_ref.py','tools/bench/vcfg_motion.py',
             'bench/state_full1_warm.pkl']
    hashes={p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sources}
    result=dict(profile=dict(sharpness=100,enhance=0,qmax=1023,warp_flags=8192),alpha=.5,
                split=args.split,hashes=hashes,rows=rows,summary=summary)
    (dest/f'{args.split}_results.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
