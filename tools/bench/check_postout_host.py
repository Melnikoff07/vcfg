"""Execute the actual out-of-place post IR with immutable host image emulation.

Preload the full LDS tile before invoking lanes serially. This preserves the
shader's arithmetic, not GPU scheduling/tiling/performance. Source hashes and
guard bands detect accidental writes to the input or outside the destination.
"""
import ctypes
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from check_gridblend_host import emulator
import vcfg_ref as ref
import vcfg_warp as warp


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--split',action='store_true');args=ap.parse_args()
    root=pathlib.Path(__file__).resolve().parents[2];out=root/('build/postfix_split' if args.split else 'build/postfix')
    src=(out/'shaders/postout8.ll').read_text()
    src=emulator(src)
    src=src.replace(' %w = extractelement <8 x i32> %d,i32 2',
                    ' %dim = extractelement <8 x i32> %d,i32 2\n %wm = and i32 %dim,16383\n %w = add i32 %wm,1')
    src=re.sub(r'define internal void @bar\(\) alwaysinline \{.*?\n\}',
               'define internal void @bar() alwaysinline { ret void }',src,flags=re.S)
    src+='''
define dllexport void @host_group(ptr %table, i32 %gx, i32 %gy) {
entry:
 %tbl = addrspacecast ptr %table to ptr addrspace(4)
 %ps = getelementptr i8,ptr addrspace(4) %tbl,i64 64
 %desc = load <8 x i32>,ptr addrspace(4) %ps,align 32
 %dim = extractelement <8 x i32> %desc,i32 2
 %wm = and i32 %dim,16383
 %hm0 = lshr i32 %dim,14
 %hm = and i32 %hm0,16383
 %bx = shl i32 %gx,3
 %by = shl i32 %gy,3
 br label %preload
preload:
 %i = phi i32 [0,%entry],[%next,%preload]
 call void @lds_put(<8 x i32> %desc,i32 %bx,i32 %by,i32 %wm,i32 %hm,i32 %i)
 %next = add i32 %i,1
 %more = icmp ult i32 %next,144
 br i1 %more,label %preload,label %lanes
lanes:
 %lane = phi i32 [0,%preload],[%lnext,%lanes]
 call void @shader_main(ptr addrspace(4) %tbl,i32 %gx,i32 %gy,i32 %lane)
 %lnext = add i32 %lane,1
 %lmore = icmp ult i32 %lnext,64
 br i1 %lmore,label %lanes,label %exit
exit:
 ret void
}
'''
    ll=out/'postout_host.ll';ll.write_text(src);dll=out/'postout_host.dll'
    cfg=json.loads((root/'config.json').read_text())
    subprocess.run([cfg['llvm_bin']+'/clang.exe','-O2','-march=x86-64-v3','-shared','-nostdlib','-fuse-ld=lld',
                    '-Wl,/noentry','-Wl,/nodefaultlib',str(ll),'-o',str(dll)],check=True)
    lib=ctypes.CDLL(str(dll));fn=lib.host_group
    fn.argtypes=[ctypes.c_void_p,ctypes.c_int32,ctypes.c_int32];fn.restype=None
    rows=[]
    for name in ('0000','0008'):
        pre=np.load(root/f'bench/post_fusion/{name}_pre16.npy');pre=ref.quant(pre,1023)
        source=np.concatenate((pre,np.ones((*pre.shape[:2],1),np.float32)),axis=-1)
        before=hashlib.sha256(source.tobytes()).hexdigest()
        for sharp,aa in (((100,1),(0,1)) if args.split else ((100,0),(50,1),(0,2))):
            backing=np.full((source.size+128,),-123,np.float32);dest=backing[64:-64].reshape(source.shape)
            tbback=np.zeros(168,np.uint32);shift=(-tbback.ctypes.data)%32//4;tb=tbback[shift:shift+152]
            for offset,array in ((16,source),(144,dest)):
                ptr=array.ctypes.data
                tb[offset:offset+8]=[ptr&0xffffffff,ptr>>32,1919|(1079<<14),0,0,0,0,0]
            tb[136]=np.float32(warp.rcas_con(sharp)).view(np.uint32);tb[137]=1|(aa<<2)
            for gy in range(135):
                for gx in range(240):fn(tb.ctypes.data,gx,gy)
            expected=ref.quant(warp.rcas8(pre,warp.rcas_con(sharp),aa),1023)
            actual=ref.quant(dest[...,:3],1023)
            codes=np.abs(np.rint((actual-expected)*1023)).astype(int)
            assert hashlib.sha256(source.tobytes()).hexdigest()==before,'post shader wrote its source'
            assert (backing[:64]==-123).all() and (backing[-64:]==-123).all(),'destination overflow'
            assert np.isfinite(dest).all() and np.max(np.abs(dest[...,3]-1))==0
            row=dict(frame=name,sharpness=sharp,aa=aa,max_code_error=int(codes.max()),
                     changed_components=int((codes!=0).sum()),components=int(codes.size),source_unchanged=True,guard_bands=True)
            rows.append(row);print(json.dumps(row),flush=True)
            # Host reciprocal/fast-math may differ at a quantization boundary.
            assert row['max_code_error']<=1,row
    (out/'postout_host_results.json').write_text(json.dumps(rows,indent=2))


if __name__=='__main__':main()
