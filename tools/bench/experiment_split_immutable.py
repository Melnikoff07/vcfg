"""Split post with raw warp history: AA-only real, AA+sharpen mid, immutable sources.

Both common raw-GT and chain-specific displayed-GT scores are reported. They
are different tasks. Cached warm motion and sparse triples do not prove shimmer.
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
from experiment_post_chain import post
from inspect_candidate import hf


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--variants',nargs='+',default=['aa1_s100','aa1_s50','aa2_s50'])
    ap.add_argument('--lpips',action='store_true');args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    dest=pathlib.Path('bench/split_immutable');dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes());rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))):
        split='train' if i%2==0 else 'validation'
        if args.split not in ('all',split):continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        cache=dest/f'{name}_raw_pre.npy'
        if cache.exists():pre=np.load(cache)
        else:
            _,info=ref.render(a,b,states[i],dict(warp_flags=8192,sharpness=0,enhance=0),return_all=True)
            pre=info['pre'];np.save(cache,pre)
        baseline=np.load(f'bench/post_chain_v1/{name}_raw_mid_only.npy')
        bm=metrics.frame_metrics(a,gt,b,baseline,None)
        for variant in args.variants:
            aa,sharp=[int(x.removeprefix(p)) for x,p in zip(variant.split('_'),('aa','s'))]
            real_a,real_gt,real_b=[post(x,0,aa) for x in (a,gt,b)]
            mid=post(pre,sharp,aa);np.save(dest/f'{name}_{variant}.npy',mid)
            raw=metrics.frame_metrics(a,gt,b,mid,None)
            displayed=metrics.frame_metrics(real_a,real_gt,real_b,mid,None)
            row=dict(frame=name,split=split,variant=variant,raw_target=raw,display_target=displayed,
                     baseline_raw=bm,delta_raw={k:raw[k]-bm[k] for k in raw},hf_raw=hf(mid)/hf(gt),hf_display=hf(mid)/hf(real_gt))
            rows.append(row);print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for variant in args.variants:
        selected=[r for r in rows if r['variant']==variant]
        summary[variant]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]} for part in ('raw_target','display_target','baseline_raw','delta_raw')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2));print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
