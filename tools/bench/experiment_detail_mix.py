"""Combine the complementary 16x16 and 8x8 PC candidates; GT only scores.

Uses saved outputs from the earlier experiments. This is an exploratory test
within the same capture, not an independent dataset or a runtime implementation.
"""
import argparse
import json
import pathlib
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref


def combine(a,b,baseline,primary,detail,alpha):
    if not 0 <= alpha <= 1:
        raise ValueError('alpha outside [0,1]')
    mixed=ref.quant(primary+(detail-primary)*np.float32(alpha),1023)
    static=np.abs(a-b).max(-1)<1/255
    mixed[static]=baseline[static]
    return mixed


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--alphas',type=float,nargs='+',default=[.125,.25,.5])
    ap.add_argument('--lpips',action='store_true')
    ap.add_argument('--primary',choices=['raw','baseline'],default='raw')
    args=ap.parse_args()
    if not args.lpips: metrics.lpips_score=lambda x,y:float('nan')
    clip=pathlib.Path('bench/clips/CUSA00557_full1')
    dest=pathlib.Path('bench/detail_mix_warm' if args.primary=='raw' else 'bench/detail_mix_baseline_warm');dest.mkdir(parents=True,exist_ok=True)
    rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(clip)):
        split='train' if i%2==0 else 'validation'
        if args.split not in (split,'all'):continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(pathlib.Path('bench/grid8_warm')/f'{name}_baseline16.npy')
        detail=np.load(pathlib.Path('bench/grid8_warm')/f'{name}_refined8.npy')
        primary=np.load(pathlib.Path('bench/motion_layers_warm')/f'{name}_raw_mid.npy') if args.primary=='raw' else base
        bm=metrics.frame_metrics(a,gt,b,base,None)
        for alpha in args.alphas:
            mid=combine(a,b,base,primary,detail,alpha)
            np.save(dest/f'{name}_mix{alpha:g}.npy',mid)
            score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,split=split,alpha=alpha,metrics=score,baseline=bm,
                     delta={k:score[k]-bm[k] for k in score})
            rows.append(row);print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for alpha in args.alphas:
        selected=[r for r in rows if r['alpha']==alpha]
        summary[str(alpha)]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]}
                             for part in ('metrics','baseline','delta')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':main()
