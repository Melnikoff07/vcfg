# Usage: python -I tools/stage_check.py <A21_best dir>. Each shader array must occur exactly once in each stage ELF.
import re,struct,pathlib,hashlib,sys
root=pathlib.Path(sys.argv[1]);src=root/'source';arr={}
for h in ['shaders.h','warp720.h','post_cb.h','warp540.h']:
    for m in re.finditer(r'static const uint32_t (\w+)_code\[(\d+)\] = \{(.*?)\};',(src/h).read_text(),re.S):
        w=[int(x,16) for x in re.findall(r'0x([0-9A-Fa-f]{8})',m[3])];arr[m[1]]=struct.pack('<%dI'%len(w),*w)
need=['pyr8','me3','me2','gm8','fgs8','me1','mf8','phase8','sum_previous','osd8'];out=[]
for s in ['s1','s2','s3','s3b','s4','s5','s6','s7','s8','s9']:
    d=root/f'build/bb_probe_diag_A21b_{s}';elf=(d/f'bb_probe_diag_A21b_{s}.elf').read_bytes();prx=(d/f'bb_probe_diag_A21b_{s}.prx').read_bytes()
    req=need+(['warp540'] if s=='s9' else ['warp720'])+(['post_cb'] if s[1]>='5' else ['rcas8'])
    found={k:elf.count(arr[k]) for k in req};ok=all(v==1 for v in found.values())
    out.append(f"{s} arrays_exact={sum(v==1 for v in found.values())}/{len(req)} {'OK' if ok else 'BAD '+str(found)} prx_sha256={hashlib.sha256(prx).hexdigest().upper()} bytes={len(prx)}")
for h in ['shaders.h','warp720.h','post_cb.h','warp540.h','fg_core.c']:
    out.append(f"{h} sha256={hashlib.sha256((src/h).read_bytes()).hexdigest()}")
(root/'BUILD_HASHES.txt').write_text('\n'.join(out)+'\n');print('\n'.join(out))
