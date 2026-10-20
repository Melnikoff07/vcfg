"""Check whether the two grid warps can share one nonlinear post pass.

Quality screening only. Fewer proposed GPU passes do not establish timing.
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
from experiment_clear_profile import sharpen
from inspect_candidate import hf


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips: metrics.lpips_score=lambda x,y:float('nan')
    dest=pathlib.Path('bench/post_fusion');dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes());rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))):
        split='train' if i%2==0 else 'validation'
        if args.split not in ('all',split):continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(f'bench/clear_grid_mix/{name}_gain0.05.npy')
        pres={}
        for block in (16,8):
            path=dest/f'{name}_pre{block}.npy'
            old=pathlib.Path(f'bench/motion_layers_warm/{name}_base_cache.npz')
            if path.exists():pre=np.load(path)
            elif block==16 and old.exists():pre=np.load(old)['pre']
            else:
                f=states[i]['F'] if block==16 else refine(a,b,states[i])
                pre=warp.warp8(a,b,f,states[i]['GK'],states[i]['cutn'],flags=8192,wp={'block':block})
            np.save(path,pre);pres[block]=ref.quant(pre,1023)
        mixed=ref.quant((pres[16]+pres[8])*.5,1023)
        mid=sharpen(ref.quant(warp.rcas8(mixed,warp.rcas_con(100),0),1023),.05)
        mid[np.abs(a-b).max(-1)<1/255]=base[np.abs(a-b).max(-1)<1/255]
        np.save(dest/f'{name}_fused.npy',mid)
        score=metrics.frame_metrics(a,gt,b,mid,None);bm=metrics.frame_metrics(a,gt,b,base,None)
        row=dict(frame=name,split=split,metrics=score,baseline=bm,delta={k:score[k]-bm[k] for k in score},
                 baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt))
        rows.append(row);print(json.dumps(row),flush=True)
        (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={part:{k:float(np.mean([r[part][k] for r in rows])) for k in rows[0][part]} for part in ('metrics','baseline','delta')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2));print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
