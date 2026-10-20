"""Score saved float32 candidates without rerunning the motion/warp stages."""
import argparse
import json
import pathlib
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--clip',type=pathlib.Path,default=pathlib.Path('bench/clips/CUSA00557_full1'))
    ap.add_argument('--method',required=True,help='path template with {frame}')
    ap.add_argument('--baseline',default='bench/grid8_warm/{frame}_baseline16.npy')
    ap.add_argument('--out',type=pathlib.Path,required=True)
    ap.add_argument('--split',choices=['train','validation','all'],default='all')
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips: metrics.lpips_score=lambda x,y:float('nan')
    rows=[]
    hudfile=args.clip/'hud_mask.png'
    hud=metrics.load(hudfile).mean(-1)>.5 if hudfile.exists() else None
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(args.clip)):
        split='train' if i%2==0 else 'validation'
        if args.split not in (split,'all'): continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        m=np.load(args.method.format(frame=name)); baseline=np.load(args.baseline.format(frame=name))
        if any(x.shape!=gt.shape for x in (a,b,m,baseline)):
            raise ValueError('input/output geometry mismatch')
        score=metrics.frame_metrics(a,gt,b,m,hud); bm=metrics.frame_metrics(a,gt,b,baseline,hud)
        row=dict(frame=name,split=split,metrics=score,baseline=bm,delta={k:score[k]-bm[k] for k in score})
        rows.append(row); print(json.dumps(row),flush=True)
    summary={part:{k:float(np.mean([r[part][k] for r in rows])) for k in rows[0][part]}
             for part in ('metrics','baseline','delta')}
    args.out.write_text(json.dumps(dict(split=args.split,method=args.method,rows=rows,summary=summary),indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__': main()
