"""Model runtime color feedback separately from retained raw motion pyramids.

Legacy in-place post is approximated by deterministic, out-of-place NumPy post:
GPU workgroup races are NOT simulated by these quality numbers. Cached warm
motion is an approximation; this is not an exact console trace replay.
"""
import argparse
import hashlib
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


def post(x,sharp=100,aa=0):
    return ref.quant(warp.rcas8(ref.quant(x,1023),warp.rcas_con(sharp),aa),1023)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--lpips',action='store_true')
    ap.add_argument('--limit',type=int,default=12)
    ap.add_argument('--out',type=pathlib.Path,default=pathlib.Path('bench/post_chain_v2'))
    args=ap.parse_args()
    if not args.lpips:metrics.lpips_score=lambda x,y:float('nan')
    clip=pathlib.Path('bench/clips/CUSA00557_full1');dest=args.out;dest.mkdir(parents=True,exist_ok=True)
    files=list(clip.glob('*.png'))+[pathlib.Path('bench/state_full1_warm.pkl')]+[pathlib.Path(__file__),
        pathlib.Path('tools/bench/vcfg_ref.py'),pathlib.Path('tools/bench/vcfg_warp.py'),pathlib.Path('tools/bench/experiment_grid8.py')]
    manifest={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    mp=dest/'manifest.json'
    if mp.exists() and json.loads(mp.read_text())!=manifest:raise RuntimeError('cache provenance changed: use a fresh output directory')
    mp.write_text(json.dumps(manifest,indent=2))
    states=pickle.loads(pathlib.Path('bench/state_full1_warm.pkl').read_bytes());rows=[]
    for i,(name,pa,pg,pb) in enumerate(list(metrics.triplets(clip))[:args.limit]):
        a,gt,b=[metrics.load(p).astype(np.float32) for p in (pa,pg,pb)];st=states[i]
        old_a,display_gt=post(a),post(gt)
        params=dict(sharpness=100,enhance=0,warp_flags=8192)
        # Motion/pyramid history remains raw. Only the color history P is postprocessed.
        mids={}
        path=dest/f'{name}_legacy16.npy'
        if path.exists():legacy=np.load(path)
        else:legacy=ref.render(old_a,b,st,params);np.save(path,legacy)
        mids['legacy16']=legacy
        path=dest/f'{name}_legacy_gridmix.npy'
        if path.exists():mixed=np.load(path)
        else:
            fine=refine(a,b,st)  # deliberately raw A/B, matching retained L1 buffers
            rendered=ref.render(old_a,b,dict(st,F=fine),dict(params,wp={'block':8}))
            mixed=sharpen((legacy+rendered)*.5,.05)
            static=np.abs(old_a-b).max(-1)<1/255
            mixed[static]=legacy[static];np.save(path,mixed)
        mids['legacy_gridmix']=mixed
        path=dest/f'{name}_raw_mid_only.npy'
        if path.exists():raw=np.load(path)
        else:raw=ref.render(a,b,st,params);np.save(path,raw)
        mids['raw_mid_only']=raw
        for method,mid in mids.items():
            real_gt=gt if method=='raw_mid_only' else display_gt
            # Common raw GT makes profiles directly comparable. Display-target
            # scores have a different reference and must not be ranked as one task.
            row=dict(frame=name,method=method,raw_target=metrics.frame_metrics(a,gt,b,mid,None),
                     display_target=metrics.frame_metrics(a,real_gt,b,mid,None),
                     hf_raw=hf(mid)/hf(gt),hf_display=hf(mid)/hf(real_gt))
            rows.append(row);print(json.dumps(row),flush=True)
            (dest/'partial.json').write_text(json.dumps(rows,indent=2))
    summary={}
    for method in mids:
        selected=[r for r in rows if r['method']==method]
        summary[method]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]} for part in ('raw_target','display_target')}
        summary[method].update({k:float(np.mean([r[k] for r in selected])) for k in ('hf_raw','hf_display')})
    result=dict(caveat=__doc__,profile=dict(sharpness=100,enhance=0,warp_flags=8192),summary=summary,rows=rows)
    (dest/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(summary,indent=2),flush=True)
    # Image-only metrics above are unchanged; finalize temporal/static scores
    # against actual displayed endpoints for each chain.
    from finalize_post_chain import main as finalize
    finalize(dest)


if __name__=='__main__':main()
