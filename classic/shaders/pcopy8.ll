; user_sgprs=2
; tgid_y=1
; pcopy8: present copy & YCoCg-R 4:2:0 16-bit history codec for double-buffered games.
; table:
;   +0x20 T#cur (source real frame)
;   +0x40 T#out (destination copy / history surface)
;   +0x60 V#ycocg (4.15 MB 16-bpp YCoCg-R 4:2:0 history buffer, 2 bytes/pixel)
;   +0x70 i32x4 params: [0] = mode (0 = T#cur->T#out, 1 = YCoCg-R 4:2:0 encode T#cur->V#ycocg, 2 = decode V#ycocg->T#out),
;                       [1] = pitch in pixels (default 1920), [2] = width, [3] = height
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.s.barrier()
declare float @llvm.floor.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

@lds_y  = internal addrspace(3) global [64 x float] undef, align 16
@lds_co = internal addrspace(3) global [64 x float] undef, align 16
@lds_cg = internal addrspace(3) global [64 x float] undef, align 16

define internal float @sat(float %v) alwaysinline {
  %a = call float @llvm.maxnum.f32(float %v, float 0.0)
  %b = call float @llvm.minnum.f32(float %a, float 1.0)
  ret float %b
}

define internal i32 @q8(float %v) alwaysinline {
  %s = call float @sat(float %v)
  %a = fmul float %s, 255.0
  %b = fadd float %a, 0.5
  %c = call float @llvm.floor.f32(float %b)
  %i = fptoui float %c to i32
  ret i32 %i
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %x = add i32 %bx, %lx
  %y = add i32 %by, %ly
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %prm = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %mode = extractelement <4 x i32> %prm, i32 0
  %is_copy = icmp eq i32 %mode, 0
  br i1 %is_copy, label %do_copy, label %check_mode

do_copy:
  %ptc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %ptc, align 32, !invariant.load !0
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  %c = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %c, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  ret void

check_mode:
  %pitch_raw = extractelement <4 x i32> %prm, i32 1
  %has_p = icmp ugt i32 %pitch_raw, 0
  %pitch = select i1 %has_p, i32 %pitch_raw, i32 1920
  %w_raw = extractelement <4 x i32> %prm, i32 2
  %has_w = icmp ugt i32 %w_raw, 0
  %w_lim = select i1 %has_w, i32 %w_raw, i32 %pitch
  %h_raw = extractelement <4 x i32> %prm, i32 3
  %has_h = icmp ugt i32 %h_raw, 0
  %h_lim = select i1 %has_h, i32 %h_raw, i32 1080
  %pvy = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %vyc = load <4 x i32>, ptr addrspace(4) %pvy, align 16, !invariant.load !0
  %is_enc = icmp eq i32 %mode, 1
  br i1 %is_enc, label %do_encode, label %do_decode

do_encode:
  %ptc_e = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc_e = load <8 x i32>, ptr addrspace(4) %ptc_e, align 32, !invariant.load !0
  %ce = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc_e, i32 0, i32 0)
  %r = extractelement <4 x float> %ce, i32 0
  %g = extractelement <4 x float> %ce, i32 1
  %b = extractelement <4 x float> %ce, i32 2
  %rb_sum = fadd float %r, %b
  %mrb = fmul float %rb_sum, 0.5
  %y_sum = fadd float %g, %mrb
  %yv = fmul float %y_sum, 0.5
  %rb_dif = fsub float %r, %b
  %co_h = fmul float %rb_dif, 0.5
  %co_u = fadd float %co_h, 0.5
  %g_dif = fsub float %g, %mrb
  %cg_h = fmul float %g_dif, 0.5
  %cg_u = fadd float %cg_h, 0.5
  %py_lds = getelementptr [64 x float], ptr addrspace(3) @lds_y, i32 0, i32 %tid
  %pco_lds = getelementptr [64 x float], ptr addrspace(3) @lds_co, i32 0, i32 %tid
  %pcg_lds = getelementptr [64 x float], ptr addrspace(3) @lds_cg, i32 0, i32 %tid
  store float %yv, ptr addrspace(3) %py_lds, align 4
  store float %co_u, ptr addrspace(3) %pco_lds, align 4
  store float %cg_u, ptr addrspace(3) %pcg_lds, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  %odd_x = and i32 %lx, 1
  %is_even_x = icmp eq i32 %odd_x, 0
  %x_ok = icmp ult i32 %x, %w_lim
  %y_ok = icmp ult i32 %y, %h_lim
  %xy_ok = and i1 %x_ok, %y_ok
  %can_store = and i1 %is_even_x, %xy_ok
  br i1 %can_store, label %enc_store, label %exit

enc_store:
  %tid1 = or i32 %tid, 1
  %py1 = getelementptr [64 x float], ptr addrspace(3) @lds_y, i32 0, i32 %tid1
  %yv1 = load float, ptr addrspace(3) %py1, align 4
  ; 2x2 chroma box filter across quad base q00 = tid & 54 (clears bit 0 and bit 3)
  %q00 = and i32 %tid, 54
  %q01 = or i32 %q00, 1
  %q10 = or i32 %q00, 8
  %q11 = or i32 %q00, 9
  %pco00 = getelementptr [64 x float], ptr addrspace(3) @lds_co, i32 0, i32 %q00
  %pco01 = getelementptr [64 x float], ptr addrspace(3) @lds_co, i32 0, i32 %q01
  %pco10 = getelementptr [64 x float], ptr addrspace(3) @lds_co, i32 0, i32 %q10
  %pco11 = getelementptr [64 x float], ptr addrspace(3) @lds_co, i32 0, i32 %q11
  %co00 = load float, ptr addrspace(3) %pco00, align 4
  %co01 = load float, ptr addrspace(3) %pco01, align 4
  %co10 = load float, ptr addrspace(3) %pco10, align 4
  %co11 = load float, ptr addrspace(3) %pco11, align 4
  %cos0 = fadd float %co00, %co01
  %cos1 = fadd float %co10, %co11
  %cos  = fadd float %cos0, %cos1
  %co_avg = fmul float %cos, 0.25
  %pcg00 = getelementptr [64 x float], ptr addrspace(3) @lds_cg, i32 0, i32 %q00
  %pcg01 = getelementptr [64 x float], ptr addrspace(3) @lds_cg, i32 0, i32 %q01
  %pcg10 = getelementptr [64 x float], ptr addrspace(3) @lds_cg, i32 0, i32 %q10
  %pcg11 = getelementptr [64 x float], ptr addrspace(3) @lds_cg, i32 0, i32 %q11
  %cg00 = load float, ptr addrspace(3) %pcg00, align 4
  %cg01 = load float, ptr addrspace(3) %pcg01, align 4
  %cg10 = load float, ptr addrspace(3) %pcg10, align 4
  %cg11 = load float, ptr addrspace(3) %pcg11, align 4
  %cgs0 = fadd float %cg00, %cg01
  %cgs1 = fadd float %cg10, %cg11
  %cgs  = fadd float %cgs0, %cgs1
  %cg_avg = fmul float %cgs, 0.25
  %qy0 = call i32 @q8(float %yv)
  %qy1 = call i32 @q8(float %yv1)
  %qco = call i32 @q8(float %co_avg)
  %qcg = call i32 @q8(float %cg_avg)
  %sy1 = shl i32 %qy1, 8
  %sco = shl i32 %qco, 16
  %scg = shl i32 %qcg, 24
  %pk0 = or i32 %qy0, %sy1
  %pk1 = or i32 %sco, %scg
  %pk  = or i32 %pk0, %pk1
  %row_px = mul i32 %y, %pitch
  %idx_px = add i32 %row_px, %x
  %boff   = shl i32 %idx_px, 1
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pk, <4 x i32> %vyc, i32 %boff, i32 0, i32 0)
  ret void

do_decode:
  %pto_d = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to_d = load <8 x i32>, ptr addrspace(4) %pto_d, align 32, !invariant.load !0
  %x_even = and i32 %x, -2
  %row_d  = mul i32 %y, %pitch
  %idx_d  = add i32 %row_d, %x_even
  %boff_d = shl i32 %idx_d, 1
  %pk_d   = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vyc, i32 %boff_d, i32 0, i32 0)
  %is_odd_d = and i32 %x, 1
  %y_shift  = shl i32 %is_odd_d, 3
  %y_shifted = lshr i32 %pk_d, %y_shift
  %yb  = and i32 %y_shifted, 255
  %cob0 = lshr i32 %pk_d, 16
  %cob = and i32 %cob0, 255
  %cgb = lshr i32 %pk_d, 24
  %yf0  = uitofp i32 %yb to float
  %cof0 = uitofp i32 %cob to float
  %cgf0 = uitofp i32 %cgb to float
  %yf  = fmul float %yf0, 0x3F70101020000000 ; 1.0 / 255.0
  %cof = fmul float %cof0, 0x3F70101020000000
  %cgf = fmul float %cgf0, 0x3F70101020000000
  %co_d = fsub float %cof, 0.5
  %cg_d = fsub float %cgf, 0.5
  %mrb_d = fsub float %yf, %cg_d
  %g_raw = fadd float %yf, %cg_d
  %r_raw = fadd float %mrb_d, %co_d
  %b_raw = fsub float %mrb_d, %co_d
  %r_out = call float @sat(float %r_raw)
  %g_out = call float @sat(float %g_raw)
  %b_out = call float @sat(float %b_raw)
  %v0 = insertelement <4 x float> undef, float %r_out, i32 0
  %v1 = insertelement <4 x float> %v0, float %g_out, i32 1
  %v2 = insertelement <4 x float> %v1, float %b_out, i32 2
  %v3 = insertelement <4 x float> %v2, float 1.0, i32 3
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %v3, i32 15, i32 %x, i32 %y, <8 x i32> %to_d, i32 0, i32 0)
  br label %exit

exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
