"""Diagnostic texture/dark-error proxies and a fixed-location comparison sheet."""
import argparse
import pathlib
import json
import sys
import numpy as np
from PIL import Image,ImageDraw
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics


def luma(x):return x@np.array([.299,.587,.114],np.float32)


def hf(x):
    x=luma(x)
    return float(np.abs(4*x[1:-1,1:-1]-x[:-2,1:-1]-x[2:,1:-1]-x[1:-1,:-2]-x[1:-1,2:]).mean())


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--method',required=True)
    ap.add_argument('--baseline',default='bench/grid8_warm/{frame}_baseline16.npy')
    ap.add_argument('--out',type=pathlib.Path,required=True)
    args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    clip=pathlib.Path('bench/clips/CUSA00557_full1')
    rows=[]
    for name,pa,pg,pb in metrics.triplets(clip):
        gt=metrics.load(pg).astype(np.float32)
        base=np.load(args.baseline.format(frame=name))
        mid=np.load(args.method.format(frame=name))
        bright=luma(gt)>.6
        row=dict(frame=name,baseline_hf=hf(base)/hf(gt),candidate_hf=hf(mid)/hf(gt),
                 baseline_dark_fraction=float(((luma(gt)-luma(base)>.2)&bright).mean()),
                 candidate_dark_fraction=float(((luma(gt)-luma(mid)>.2)&bright).mean()))
        rows.append(row)
        if name=='0008':
            # Fixed crop of the foreground fence, selected before candidate inspection.
            x0,y0,size=1080,100,384
            sheet=Image.new('RGB',(size*3,size+32),'#151515');draw=ImageDraw.Draw(sheet)
            for j,(label,data) in enumerate((('GT',gt),('baseline',base),('candidate',mid))):
                crop=data[y0:y0+size,x0:x0+size]
                im=Image.fromarray(np.rint(np.clip(crop,0,1)*255).astype(np.uint8))
                sheet.paste(im,(j*size,32));draw.text((j*size+8,9),label,fill='white')
            sheet.save(args.out/'fence_comparison.png')
    summary={k:float(np.mean([r[k] for r in rows])) for k in rows[0] if k!='frame'}
    (args.out/'diagnostics.json').write_text(json.dumps(dict(definitions=dict(
        hf='mean absolute 4-neighbor luma Laplacian, divided by GT',
        dark='fraction of all pixels with GT luma > .6 and candidate at least .2 darker; not a causal artifact detector'),
        rows=rows,summary=summary),indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
