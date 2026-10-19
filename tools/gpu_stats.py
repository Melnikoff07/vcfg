import csv,sys,statistics as st
rows=list(csv.DictReader(open(sys.argv[1])))
hz=99767753.29
def ms(a,b): 
    v=[(int(r[b])-int(r[a]))/hz*1000 for r in rows if int(r[b])>=int(r[a])>0]
    v.sort(); return f"n={len(v)} med={st.median(v):.3f} p95={v[int(len(v)*.95)]:.3f} max={v[-1]:.3f}"
print('wait',ms('gpu_before_wait','gpu_after_wait'))
print('pyr+sum',ms('gpu_after_wait','gpu_pyramids_done'))
print('motion',ms('gpu_pyramids_done','gpu_motion_done'))
print('phase',ms('gpu_motion_done','gpu_phase_done'))
print('warp720',ms('gpu_phase_done','gpu_warp_done'))
print('synth',ms('gpu_after_wait','gpu_warp_done'))
cuts=[int(r['cut_raw']) for r in rows]; print('cut>3264',sum(c>3264 for c in cuts),'of',len(cuts),'max',max(cuts))
args=[int(r['arg']) for r in rows]; d=[b-a for a,b in zip(args,args[1:])]
from collections import Counter; print('arg deltas',Counter(d).most_common(6))
