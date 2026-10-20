"""Local continuous motion refinement; no GT in vector estimation."""
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


def refine(a, b, field, iterations=3):
    """Regularized bilateral Lucas-Kanade update on the existing L1 blocks.

    Full-resolution displacement is bounded to +/-2 pixels per component.
    Only symmetric input matching is minimized; no ground truth is accepted.
    """
    la, lb = [ref.pyr8(x)[0].astype(np.float32)/255 for x in (a, b)]
    gy_a, gx_a = np.gradient(la)
    gy_b, gx_b = np.gradient(lb)
    nby, nbx = field.shape[:2]
    by, bx = np.indices((nby, nbx))
    x = bx.ravel()[:, None, None]*8 + np.arange(8)[None, None, :] + .5
    y = by.ravel()[:, None, None]*8 + np.arange(8)[None, :, None] + .5
    x, y = np.broadcast_arrays(x.astype(np.float32),y.astype(np.float32))
    seed = field.reshape(-1,2)
    current = seed.copy()
    def sample(img, u, v):
        return warp.bil(img[...,None],u,v)[...,0]
    def evaluate(v):
        up, vp = x+v[:,0,None,None]*.25,y+v[:,1,None,None]*.25
        uc, vc = x-v[:,0,None,None]*.25,y-v[:,1,None,None]*.25
        residual = sample(la,up,vp)-sample(lb,uc,vc)
        return up,vp,uc,vc,residual
    start_error = np.abs(evaluate(seed)[-1]).sum((1,2))
    for _ in range(iterations):
        up,vp,uc,vc,r = evaluate(current)
        gx = (sample(gx_a,up,vp)+sample(gx_b,uc,vc))*.25
        gy = (sample(gy_a,up,vp)+sample(gy_b,uc,vc))*.25
        weight = np.minimum(.06/np.maximum(np.abs(r),1e-6),1)
        sx = lambda v: (weight*v).sum((1,2))
        xx,yy,xy = sx(gx*gx),sx(gy*gy),sx(gx*gy)
        reg = np.maximum((xx+yy)*.05,1e-4)
        xx,yy = xx+reg,yy+reg
        rx,ry = sx(gx*r),sx(gy*r)
        det = xx*yy-xy*xy
        dx,dy = (-yy*rx+xy*ry)/det,(-xx*ry+xy*rx)/det
        update = np.clip(np.stack((dx,dy),-1),-1,1)
        current = seed+np.clip(current+update-seed,-2,2)
    finish_error = np.abs(evaluate(current)[-1]).sum((1,2))
    return np.where((finish_error<start_error)[:,None],current,seed).reshape(field.shape).astype(np.float32)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',choices=['train','validation','all'],default='train')
    ap.add_argument('--lpips',action='store_true')
    args=ap.parse_args()
    if not args.lpips:
        metrics.lpips_score=lambda x,y:float('nan')
    clip=pathlib.Path('bench/clips/CUSA00557_full1')
    dest=pathlib.Path('bench/subpixel_warm'); dest.mkdir(parents=True,exist_ok=True)
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes())
    rows=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(clip)):
        split='train' if i%2==0 else 'validation'
        if args.split not in (split,'all'): continue
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)]
        base=np.load(pathlib.Path('bench/grid8_warm')/f'{name}_baseline16.npy')
        bm=metrics.frame_metrics(a,gt,b,base,None)
        field=refine(a,b,states[i]['F'])
        np.save(dest/f'{name}_field.npy',field)
        for strength in (.5,1.):
            tag=f'lk{strength:g}'; output=dest/f'{name}_{tag}.npy'
            if output.exists(): mid=np.load(output)
            else:
                st=dict(states[i],F=states[i]['F']+(field-states[i]['F'])*strength)
                mid=ref.render(a,b,st,dict(warp_flags=8192,sharpness=40,enhance=1))
                np.save(output,mid)
            score=metrics.frame_metrics(a,gt,b,mid,None)
            row=dict(frame=name,split=split,variant=tag,metrics=score,baseline=bm,
                     delta={k:score[k]-bm[k] for k in score})
            rows.append(row); print(json.dumps(row),flush=True)
            (dest/f'{args.split}_partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for variant in ('lk0.5','lk1'):
        selected=[r for r in rows if r['variant']==variant]
        summary[variant]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]}
                          for part in ('metrics','baseline','delta')}
    (dest/f'{args.split}_results.json').write_text(json.dumps(dict(rows=rows,summary=summary),indent=2))
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__': main()
