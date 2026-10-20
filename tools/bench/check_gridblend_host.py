"""Check the exact blend IR against the clear-profile NumPy prototype."""
import ctypes
import json
import pathlib
import subprocess
import sys
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import metrics
import vcfg_ref as ref
from experiment_clear_profile import sharpen


def emulator(src):
    src=src.replace('target triple = "amdgcn--"','target triple = "x86_64-pc-windows-msvc"')
    src=src.replace('define amdgpu_cs void @main','define dllexport void @shader_main').replace(' inreg','')
    src=src.replace('@llvm.amdgcn.image.load.2d.v4f32.i32','@emu_load')
    src=src.replace('@llvm.amdgcn.image.store.2d.v4f32.i32','@emu_store')
    address=''' %lo = extractelement <8 x i32> %d,i32 0
 %hi = extractelement <8 x i32> %d,i32 1
 %w = extractelement <8 x i32> %d,i32 2
 %lo64 = zext i32 %lo to i64
 %hi64 = zext i32 %hi to i64
 %hib = shl i64 %hi64,32
 %addr = or i64 %lo64,%hib
 %row = mul i32 %y,%w
 %index = add i32 %row,%x
 %idx64 = zext i32 %index to i64
 %offset = shl i64 %idx64,4
 %sum = add i64 %addr,%offset
 %p = inttoptr i64 %sum to ptr
'''
    load='''define internal <4 x float> @emu_load(i32 %mask,i32 %x,i32 %y,<8 x i32> %d,i32 %a,i32 %b) alwaysinline {
'''+address+''' %value = load <4 x float>,ptr %p,align 1
 ret <4 x float> %value
}'''
    store='''define internal void @emu_store(<4 x float> %value,i32 %mask,i32 %x,i32 %y,<8 x i32> %d,i32 %a,i32 %b) alwaysinline {
'''+address+''' store <4 x float> %value,ptr %p,align 1
 ret void
}'''
    src=src.replace('declare <4 x float> @emu_load(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)',load)
    src=src.replace('declare void @emu_store(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)',store)
    return src+'\n@_fltused = global i32 0\n'


def main():
    root=pathlib.Path(__file__).resolve().parents[2];out=root/'build/gridmix'
    ll=out/'gridblend_host.ll';ll.write_text(emulator((out/'shaders/gridblend8.ll').read_text()))
    cfg=json.loads((root/'config.json').read_text());dll=out/'gridblend_host.dll'
    subprocess.run([cfg['llvm_bin']+'/clang.exe','-O2','-march=x86-64-v3','-shared','-nostdlib','-fuse-ld=lld',
                    '-Wl,/noentry','-Wl,/nodefaultlib',str(ll),'-o',str(dll)],check=True)
    lib=ctypes.CDLL(str(dll));fn=lib.shader_main
    fn.argtypes=[ctypes.c_void_p,ctypes.c_int32,ctypes.c_int32,ctypes.c_int32];fn.restype=None
    results=[]
    for name in ('0000','0008'):
        base=np.load(root/f'bench/clear_grid_mix/{name}_grid16.npy')
        fine=np.load(root/f'bench/clear_grid_mix/{name}_grid8.npy')
        a,b=[metrics.load(root/f'bench/clips/CUSA00557_full1/{name}_{f}.png').astype(np.float32) for f in ('a','b')]
        static=np.abs(a-b).max(-1)<1/255
        legacy=np.concatenate((base,np.ones((*base.shape[:2],1),np.float32)),axis=-1)
        detail=np.concatenate((fine,static[...,None].astype(np.float32)),axis=-1)
        output=np.zeros_like(legacy)
        backing=np.zeros(168,np.uint32)
        start=(-backing.ctypes.data)%32//4
        table=backing[start:start+160]
        for word,array in ((16,legacy),(144,detail),(152,output)):
            ptr=array.ctypes.data
            table[word:word+8]=[ptr&0xffffffff,ptr>>32,1920,1080,0,0,0,0]
        # Entire image, including all clamp-to-edge cases.
        for y in range(1080):
            for x in range(1920):fn(table.ctypes.data,x//8,y//8,(y%8)*8+x%8)
        got=ref.quant(output[...,:3],1023)
        expected=sharpen((base+fine)*np.float32(.5),.05);expected[static]=base[static]
        mismatch=np.any(got!=expected,axis=-1)
        row=dict(frame=name,mismatched_pixels=int(mismatch.sum()),max_error=float(np.abs(got-expected).max()),
                 alpha_preserved=bool(np.all(output[...,3]==1)),static_preserved=bool(np.array_equal(got[static],base[static])))
        print(json.dumps(row),flush=True);results.append(row)
    (out/'blend_host_conformance.json').write_text(json.dumps(results,indent=2))
    if any(r['mismatched_pixels'] or not r['alpha_preserved'] or not r['static_preserved'] for r in results):
        raise SystemExit('blend differs from reference')


if __name__=='__main__':main()
