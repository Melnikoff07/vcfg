"""Compare independent camera/raw-motion layers, preserving the old static output.

This PC prototype runs extra warps. It makes no runtime cost claim.
"""
import argparse
import pathlib
import pickle
import json
import sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref
import vcfg_warp as warp


def post(pre):
    q = ref.quant(pre,1023)
    return ref.quant(warp.rcas8(q,warp.rcas_con(40),1),1023)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--variants',nargs='+',choices=['camera','raw','both'],default=['camera','raw','both'])
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips: metrics.lpips_score=lambda x,y:float('nan')
    clip=pathlib.Path('bench/clips/CUSA00557_full1')
    dest=pathlib.Path('bench/motion_layers_warm'); dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes())
    rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(clip)):
        split='train' if i%2==0 else 'validation'
        if args.split not in (split,'all'): continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(pathlib.Path('bench/grid8_warm')/f'{name}_baseline16.npy')
        bm=metrics.frame_metrics(a,gt,b,base,None)
        st=states[i]
        camera=np.empty_like(st['F']); camera[:]=np.array(st['gm'])*4
        fields={'base':st['F'],'camera':camera,'raw':st['FT']}
        pres,errors={},{}
        need=set(['base']+(['camera','raw'] if 'both' in args.variants else args.variants))
        for layer in sorted(need):
            cached=dest/f'{name}_{layer}_cache.npz'
            if cached.exists():
                data=np.load(cached); pre,err=data['pre'],data['error']
            else:
                dbg={}
                pre=warp.warp8(a,b,fields[layer],st['GK'],st['cutn'],flags=8192,dbg=dbg)
                err=np.concatenate(dbg['emov'])
                np.savez(cached,pre=pre,error=err)
            pres[layer],errors[layer]=pre,warp.boxf(err,3)
        static=np.abs(a-b).max(-1)<1/255
        for variant in args.variants:
            choices=['camera','raw'] if variant=='both' else [variant]
            alt,err=pres[choices[0]],errors[choices[0]]
            for choice in choices[1:]:
                take=errors[choice]<err
                alt=np.where(take[...,None],pres[choice],alt)
                err=np.minimum(err,errors[choice])
            # A fixed margin avoids changes for nearly tied input matches.
            strength=np.clip((errors['base']-err-.03)*8,0,.5)
            strength=np.where(static,0,strength)
            pre=pres['base']+(alt-pres['base'])*strength[...,None]
            mid=post(pre)
            # Deliberately retain legacy output at A==B pixels; GT is not used.
            mid[static]=base[static]
            np.save(dest/f'{name}_{variant}_mid.npy',mid)
            score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,split=split,variant=variant,changed_fraction=float((strength>0).mean()),
                     metrics=score,baseline=bm,delta={k:score[k]-bm[k] for k in score})
            rows.append(row); print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for variant in args.variants:
        selected=[r for r in rows if r['variant']==variant]
        summary[variant]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]}
                          for part in ('metrics','baseline','delta')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__': main()
