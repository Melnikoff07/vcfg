; user_sgprs=2
; mode 8, pass 5 (one wave, after the flip-done fence): interpolation phase t for this mid frame -> misc dword 1.
; Supports three phase sources:
;  1) CPU explicit fractional phase override in consts dword 1 upper 16 bits (tb[29] >> 16, Q0.16 in [1..65535]):
;     used by FG Quality (2x real-timestamp midpoint) and FG Fast (60 Hz fractional pulldown t1, t2).
;  2) Hardware vblank / flip monitor in mon[0..3] when adaptive phase (flags bit 0) is active.
;  3) Fixed t = 0.5 fallback.
; Also copies the frame statistics misc dwords {0 = unmatched blocks (scene-change test), 4, 5 = dominant motion,
; 6 = foreground L2 blocks, 7 = fade decision of me1} to the slot's stats area unless bit 15 of consts dword 1
; (0x8000 = second mid frame in FG Fast 3-step pulldown) is set.
target triple = "amdgcn--"
declare <4 x i32> @llvm.amdgcn.raw.buffer.load.v4i32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.f32(float, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v4i32(<4 x i32>, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) #0 {
entry:
  %is0 = icmp eq i32 %tid, 0
  br i1 %is0, label %go, label %exit
go:
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %pv = getelementptr i8, ptr addrspace(4) %tbl, i64 304
  %vv = load <4 x i32>, ptr addrspace(4) %pv, align 16, !invariant.load !0
  %mid_raw = extractelement <4 x i32> %cn, i32 1
  %mid = and i32 %mid_raw, 32767
  %skip_st0 = and i32 %mid_raw, 32768
  %skip_st = icmp ne i32 %skip_st0, 0
  %tover_q16 = lshr i32 %mid_raw, 16
  %has_over = icmp ugt i32 %tover_q16, 0
  %tover_f = uitofp i32 %tover_q16 to float
  %t_over = fmul float %tover_f, 0x3EF0000000000000 ; 1.0 / 65536.0
  %flags = extractelement <4 x i32> %cn, i32 2
  %mon = call <4 x i32> @llvm.amdgcn.raw.buffer.load.v4i32(<4 x i32> %vv, i32 0, i32 0, i32 3)
  %vbnow = extractelement <4 x i32> %mon, i32 0
  %cur = extractelement <4 x i32> %mon, i32 1
  %fvb = extractelement <4 x i32> %mon, i32 2
  %n0 = sub i32 %vbnow, %fvb
  %n1 = add i32 %n0, 1
  %onmid = icmp eq i32 %cur, %mid
  %n2 = select i1 %onmid, i32 1, i32 %n1
  %n3 = call i32 @llvm.smax.i32(i32 %n2, i32 1)
  %n = call i32 @llvm.smin.i32(i32 %n3, i32 3)
  %nf = sitofp i32 %n to float
  %n1f = fadd float %nf, 1.0
  %ta0 = fdiv float %nf, %n1f
  %tq16 = extractelement <4 x i32> %mon, i32 3
  %has_tq = icmp ugt i32 %tq16, 0
  %tqf = uitofp i32 %tq16 to float
  %tqn = fmul float %tqf, 0x3EF0000000000000 ; 1.0 / 65536.0
  %t_blend = fmul float %ta0, 0.5
  %t_blend2 = fmul float %tqn, 0.5
  %ta_ema = fadd float %t_blend, %t_blend2
  %ta = select i1 %has_tq, float %ta_ema, float %ta0
  %ad = and i32 %flags, 1
  %adb = icmp ne i32 %ad, 0
  %t_mon = select i1 %adb, float %ta, float 0.5
  %t0 = select i1 %has_over, float %t_over, float %t_mon
  ; flags bit 1: frame generation switched off (or >66 ms repeat guard): t = 0 -> exact copy of previous real frame
  %off = and i32 %flags, 2
  %offb = icmp ne i32 %off, 0
  %t = select i1 %offb, float 0.0, float %t0
  call void @llvm.amdgcn.raw.buffer.store.f32(float %t, <4 x i32> %vm, i32 4, i32 0, i32 0)
  br i1 %skip_st, label %exit, label %wr_stats
wr_stats:
  %m0 = call <4 x i32> @llvm.amdgcn.raw.buffer.load.v4i32(<4 x i32> %vm, i32 0, i32 0, i32 0)
  %m1 = call <4 x i32> @llvm.amdgcn.raw.buffer.load.v4i32(<4 x i32> %vm, i32 16, i32 0, i32 0)
  %ps = getelementptr i8, ptr addrspace(4) %tbl, i64 368
  %vs = load <4 x i32>, ptr addrspace(4) %ps, align 16, !invariant.load !0
  %cutn = extractelement <4 x i32> %m0, i32 0
  %st0 = shufflevector <4 x i32> %m1, <4 x i32> poison, <4 x i32> <i32 3, i32 0, i32 1, i32 2>
  %st = insertelement <4 x i32> %st0, i32 %cutn, i32 0
  call void @llvm.amdgcn.raw.buffer.store.v4i32(<4 x i32> %st, <4 x i32> %vs, i32 0, i32 0, i32 1)
  %fd = extractelement <4 x i32> %m1, i32 3
  %st2 = insertelement <4 x i32> zeroinitializer, i32 %fd, i32 0
  call void @llvm.amdgcn.raw.buffer.store.v4i32(<4 x i32> %st2, <4 x i32> %vs, i32 16, i32 0, i32 1)
  br label %exit
exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
