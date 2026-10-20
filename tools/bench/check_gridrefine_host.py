"""Execute the refinement IR on the host by emulating its buffer intrinsics."""
import ctypes
import pathlib
import pickle
import subprocess
import sys
import json
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref
from experiment_grid8 import refine


def emulate(src):
    src=src.replace('target triple = "amdgcn--"','target triple = "x86_64-pc-windows-msvc"')
    src=src.replace('define amdgpu_cs void @main','define dllexport void @shader_main').replace(' inreg','')
    for typ,stem in [('<2 x i32>','v2i32'),('<2 x float>','v2f32'),('i32','i32')]:
        name='llvm.amdgcn.raw.buffer.load.'+stem
        emu='emu_load_'+stem
        src=src.replace('@'+name,'@'+emu)
        declaration=f'declare {typ} @{emu}(<4 x i32>,i32,i32,i32 immarg)'
        body=f'''define internal {typ} @{emu}(<4 x i32> %d,i32 %off,i32 %so,i32 %fl) alwaysinline {{
 %lo = extractelement <4 x i32> %d,i32 0
 %hi = extractelement <4 x i32> %d,i32 1
 %lo64 = zext i32 %lo to i64
 %hi64 = zext i32 %hi to i64
 %hib = shl i64 %hi64,32
 %addr = or i64 %lo64,%hib
 %of64 = sext i32 %off to i64
 %sum = add i64 %addr,%of64
 %p = inttoptr i64 %sum to ptr
 %v = load {typ},ptr %p,align 1
 ret {typ} %v
}}'''
        if declaration not in src:raise ValueError('load declaration mismatch')
        src=src.replace(declaration,body)
    src=src.replace('@llvm.amdgcn.raw.buffer.store.v2f32','@emu_store')
    src=src.replace('declare void @emu_store(<2 x float>,<4 x i32>,i32,i32,i32 immarg)', '''define internal void @emu_store(<2 x float> %v,<4 x i32> %d,i32 %off,i32 %so,i32 %fl) alwaysinline {
 %lo = extractelement <4 x i32> %d,i32 0
 %hi = extractelement <4 x i32> %d,i32 1
 %lo64 = zext i32 %lo to i64
 %hi64 = zext i32 %hi to i64
 %hib = shl i64 %hi64,32
 %addr = or i64 %lo64,%hib
 %of64 = sext i32 %off to i64
 %sum = add i64 %addr,%of64
 %p = inttoptr i64 %sum to ptr
 store <2 x float> %v,ptr %p,align 1
 ret void
}''')
    src=src.replace('@llvm.amdgcn.alignbyte','@emu_align')
    src=src.replace('declare i32 @emu_align(i32,i32,i32)', '''define internal i32 @emu_align(i32 %hi,i32 %lo,i32 %shift) alwaysinline {
 %h = zext i32 %hi to i64
 %l = zext i32 %lo to i64
 %hb = shl i64 %h,32
 %both = or i64 %hb,%l
 %s = zext i32 %shift to i64
 %bits = shl i64 %s,3
 %shifted = lshr i64 %both,%bits
 %out = trunc i64 %shifted to i32
 ret i32 %out
}''')
    src=src.replace('@llvm.amdgcn.sad.u8','@emu_sad')
    sad=['define internal i32 @emu_sad(i32 %a,i32 %b,i32 %acc) alwaysinline {']
    last='%acc'
    for k in range(4):
        sad.append(f''' %a{k}s = lshr i32 %a,{k*8}
 %b{k}s = lshr i32 %b,{k*8}
 %a{k} = and i32 %a{k}s,255
 %b{k} = and i32 %b{k}s,255
 %d{k} = sub i32 %a{k},%b{k}
 %neg{k} = sub i32 0,%d{k}
 %abs{k} = call i32 @llvm.smax.i32(i32 %d{k},i32 %neg{k})
 %sum{k} = add i32 {last},%abs{k}''')
        last=f'%sum{k}'
    sad.append(f' ret i32 {last}\n}}')
    src=src.replace('declare i32 @emu_sad(i32,i32,i32)','\n'.join(sad))
    return src+'\n@_fltused = global i32 0\n'


def main():
    root=pathlib.Path(__file__).resolve().parents[2]
    out=root/'build/gridmix'
    source=out/'gridrefine_host.ll'
    source.write_text(emulate((out/'shaders/gridrefine8.ll').read_text()))
    cfg=json.loads((root/'config.json').read_text())
    dll=out/'gridrefine_host.dll'
    command=[str(pathlib.Path(cfg['llvm_bin'])/'clang.exe'),'-O2','-march=x86-64-v3','-shared','-nostdlib',
             '-fuse-ld=lld','-Wl,/noentry','-Wl,/nodefaultlib',str(source),'-o',str(dll)]
    subprocess.run(command,check=True)
    lib=ctypes.CDLL(str(dll));fn=lib.shader_main
    fn.argtypes=[ctypes.c_void_p,ctypes.c_int32,ctypes.c_int32];fn.restype=None
    states=pickle.loads((root/'bench/state_full1_warm.pkl').read_bytes())
    results=[]
    for i,(name,pa,pg,pb) in enumerate(metrics.triplets(root/'bench/clips/CUSA00557_full1')):
        a,b=[metrics.load(p).astype(np.float32) for p in (pa,pb)]
        lp,lc=[np.pad(ref.pyr8(x)[0].astype(np.uint8).ravel(),(0,16)) for x in (a,b)]
        field=states[i]['F'].copy();target=np.zeros((135,240,2),np.float32);misc=np.zeros(8,np.int32)
        table=np.zeros(160,np.uint32)
        def descriptor(word,array):
            ptr=array.ctypes.data
            table[word:word+4]=[ptr&0xffffffff,ptr>>32,array.nbytes,0]
        descriptor(32,lc);descriptor(36,lp);descriptor(64,field);descriptor(72,misc);descriptor(140,target)
        for cell in range(32400):fn(table.ctypes.data,cell//64,cell%64)
        expected=refine(a,b,states[i])
        mismatch=np.any(target!=expected,axis=-1)
        result=dict(frame=name,mismatched_blocks=int(mismatch.sum()),max_component_error=float(np.max(np.abs(target-expected))))
        results.append(result);print(json.dumps(result),flush=True)
    (out/'host_conformance.json').write_text(json.dumps(results,indent=2))
    if any(r['mismatched_blocks'] for r in results):raise SystemExit('IR/reference mismatch: investigate before deployment')
    print('All 388800 block vectors match exactly.')


if __name__=='__main__':main()
