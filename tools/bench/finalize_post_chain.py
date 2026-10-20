"""Correct display-step endpoints and preserve measured image-only LPIPS scores.

Common-raw-reference metrics remain unchanged. Legacy display scores use
post(A), post(GT), post(B), rather than mixing a processed GT with raw endpoints.
"""
import hashlib
import json
import pathlib
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics
from experiment_post_chain import post


def main(dest=pathlib.Path('bench/post_chain_v1')):
    dest=pathlib.Path(dest);original=dest/'results.json'
    result=json.loads(original.read_text());manifest=json.loads((dest/'manifest.json').read_text())
    for p,expected in manifest.items():
        if p.endswith('.png') or p.endswith('.pkl'):
            if hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=expected:raise RuntimeError('input provenance changed: '+p)
    triples={name:(pa,pg,pb) for name,pa,pg,pb in metrics.triplets(pathlib.Path('bench/clips/CUSA00557_full1'))}
    cache={}
    for row in result['rows']:
        name=row['frame'];method=row['method']
        if name not in cache:
            cache.clear()
            a,gt,b=[metrics.load(p).astype(np.float32) for p in triples[name]]
            cache[name]=(a,gt,b,post(a),post(gt),post(b))
        a,gt,b,pa,pg,pb=cache[name]
        if method=='raw_mid_only':ea,eg,eb=a,gt,b
        else:ea,eg,eb=pa,pg,pb
        mid=np.load(dest/f'{name}_{method}.npy')
        # raw_mid_only file follows its method name; both legacy files do too.
        static=np.abs(ea-eb).max(-1)<1/255
        score=row['display_target']
        score['TCE']=float((np.abs(np.abs(mid-ea)-np.abs(eg-ea)).mean()+np.abs(np.abs(eb-mid)-np.abs(eb-eg)).mean())*100)
        score['PSNR_static']=metrics.psnr(mid,eg,static)
        score['HUD_flicker']=float(np.abs(mid-eg)[static].mean()*100) if static.any() else float('nan')
    summary={}
    for method in result['summary']:
        selected=[r for r in result['rows'] if r['method']==method]
        summary[method]={part:{k:float(np.mean([r[part][k] for r in selected])) for k in selected[0][part]} for part in ('raw_target','display_target')}
        summary[method].update({k:float(np.mean([r[k] for r in selected])) for k in ('hf_raw','hf_display')})
    result['summary']=summary
    result['display_endpoint_definition']='legacy: post(A), post(GT), post(B); raw_mid_only: raw A,GT,B. Image PSNR/SSIM/LPIPS unchanged from original scoring.'
    result['original_results_sha256']=hashlib.sha256(original.read_bytes()).hexdigest()
    result['finalizer_sha256']=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    (dest/'runtime_display_results.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
