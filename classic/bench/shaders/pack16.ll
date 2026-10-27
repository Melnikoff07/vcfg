; user_sgprs=2
; tgid_y=1
; Benchmark-only export.
; Mode A (dims.y != 68): T# at 0, V# at 32, {width,height} at 48 -> Linear RGBA16LE export.
; Mode B (dims.y == 68): Lightweight sequence step snapshot (64 KiB at V#out):
;   +0x0000: V3 (2040 B) + anchor_hist (2040 B at +0x0800)
;   +0x1000: V2 (8160 B)
;   +0x2FE0: W8_MISC (32 B)
;   +0x3000: GK (32640 B, 120x68 u32)
;   +0xAF80: fgmask (8160 B, 120x68 u8: bit0 hasfg, bit1 isanc, bit2 vmg_hi, bit3 mov_obj, bit4 mfit, bit5 fgkill_ws, bit6 fgkill, bit7 sub8)
;   +0xCF60: F_head_crop (8064 B, 28x36 float2 for gy=13..40, gx=42..77)
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 immarg, float, float, <8 x i32>, <4 x i32>, i1 immarg, i32 immarg, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i8(i8, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2i32(<2 x i32>, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>, <4 x i32>, i32, i32, i32 immarg)
declare float @llvm.fabs.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)
declare i32 @llvm.umin.i32(i32, i32)
declare <4 x float> @llvm.maxnum.v4f32(<4 x float>, <4 x float>)
declare <4 x float> @llvm.minnum.v4f32(<4 x float>, <4 x float>)

define internal float @sat(float %x) alwaysinline {
  %a = call float @llvm.maxnum.f32(float %x, float 0.0)
  %b = call float @llvm.minnum.f32(float %a, float 1.0)
  ret float %b
}

define internal float @sum3(<4 x float> %a) alwaysinline {
  %e0 = extractelement <4 x float> %a, i32 0
  %e1 = extractelement <4 x float> %a, i32 1
  %e2 = extractelement <4 x float> %a, i32 2
  %s = fadd float %e0, %e1
  %r = fadd float %s, %e2
  ret float %r
}

define internal float @diff3_k(<4 x float> %a, <4 x float> %b) alwaysinline {
  %mx = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %a, <4 x float> %b)
  %mn = call <4 x float> @llvm.minnum.v4f32(<4 x float> %a, <4 x float> %b)
  %l_hi = call float @sum3(<4 x float> %mx)
  %l_lo = call float @sum3(<4 x float> %mn)
  %e_raw = fsub float %l_hi, %l_lo
  %ex_hi0 = fsub float %l_hi, 0x3FFF333340000000 ; 1.95
  %ex_hi = call float @llvm.maxnum.f32(float %ex_hi0, float 0.0)
  %ex_lo0 = fsub float %l_lo, 0x3FECCCCCC0000000 ; 0.90
  %ex_lo = call float @llvm.maxnum.f32(float %ex_lo0, float 0.0)
  %k_hi = fmul float %ex_hi, 1.25
  %k_lo = fmul float %ex_lo, 0.75
  %k_sum = fadd float %k_hi, %k_lo
  %den = fadd float 1.0, %k_sum
  %e = fdiv arcp afn float %e_raw, %den
  ret float %e
}

define internal { float, float, i1 } @unpack_f(<2 x float> %f_val) alwaysinline {
  %rx = extractelement <2 x float> %f_val, i32 0
  %ry = extractelement <2 x float> %f_val, i32 1
  %bxi = bitcast float %rx to i32
  %byi = bitcast float %ry to i32
  %sub_m = and i32 %bxi, 15
  %sub8 = icmp ne i32 %sub_m, 0
  %b1x = and i32 %bxi, -4096
  %b1y = and i32 %byi, -256
  %v1x0 = bitcast i32 %b1x to float
  %v1y0 = bitcast i32 %b1y to float
  %ax1 = call float @llvm.fabs.f32(float %v1x0)
  %ay1 = call float @llvm.fabs.f32(float %v1y0)
  %zx1 = fcmp ole float %ax1, 0.0625
  %zy1 = fcmp ole float %ay1, 0.0625
  %v1x1 = select i1 %zx1, float 0.0, float %v1x0
  %v1y1 = select i1 %zy1, float 0.0, float %v1y0
  %fx = select i1 %sub8, float %v1x1, float %rx
  %fy = select i1 %sub8, float %v1y1, float %ry
  %r0 = insertvalue { float, float, i1 } undef, float %fx, 0
  %r1 = insertvalue { float, float, i1 } %r0, float %fy, 1
  %r2 = insertvalue { float, float, i1 } %r1, i1 %sub8, 2
  ret { float, float, i1 } %r2
}

define internal <4 x float> @sample_hl(<8 x i32> %t, <4 x i32> %sm, float %px, float %py) alwaysinline {
  %px_l = fsub float %px, 1.0
  %px_r = fadd float %px, 1.0
  %py_u = fsub float %py, 1.0
  %py_d = fadd float %py, 1.0
  %s0 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %px, float %py, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s1 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %px_l, float %py, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s2 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %px_r, float %py, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s3 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %px, float %py_u, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s4 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %px, float %py_d, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %mn12 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %s1, <4 x float> %s2)
  %mn34 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %s3, <4 x float> %s4)
  %mn_out = call <4 x float> @llvm.minnum.v4f32(<4 x float> %mn12, <4 x float> %mn34)
  %mn_all = call <4 x float> @llvm.minnum.v4f32(<4 x float> %mn_out, <4 x float> %s0)
  %mx12 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %s1, <4 x float> %s2)
  %mx34 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %s3, <4 x float> %s4)
  %mx_out = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %mx12, <4 x float> %mx34)
  %mx5 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %mx_out, <4 x float> %s0)
  %l_mx5 = call float @sum3(<4 x float> %mx5)
  %l_mn5 = call float @sum3(<4 x float> %mn_all)
  %l_s0 = call float @sum3(<4 x float> %s0)
  %rng_cr = fsub float %l_mx5, %l_mn5
  %br_ex0 = fsub float %l_mx5, 1.5
  %br_ex1 = fmul float %br_ex0, 2.0
  %br_g = call float @sat(float %br_ex1)
  %hl_rng0 = fmul float %rng_cr, %br_g
  %sky_ok = fcmp ogt float %l_mx5, 0x3FFCCCCCC0000000 ; 1.80
  %dip_v = fsub float %l_mx5, %l_s0
  %dip_ok = fcmp ogt float %dip_v, 0x3FC3333340000000 ; 0.15
  %is_wt = and i1 %sky_ok, %dip_ok
  %hl_rng_w = fadd float %hl_rng0, 4.0
  %hl_rng = select i1 %is_wt, float %hl_rng_w, float %hl_rng0
  %hi = insertelement <4 x float> %s0, float %hl_rng, i32 3
  ret <4 x float> %hi
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %tc = load <8 x i32>, ptr addrspace(4) %tbl, align 32, !invariant.load !0
  %pv = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %vo = load <4 x i32>, ptr addrspace(4) %pv, align 16, !invariant.load !0
  %pd = getelementptr i8, ptr addrspace(4) %tbl, i64 48
  %dims = load <4 x i32>, ptr addrspace(4) %pd, align 16, !invariant.load !0
  %w = extractelement <4 x i32> %dims, i32 0
  %h = extractelement <4 x i32> %dims, i32 1
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %x = add i32 %bx, %lx
  %y = add i32 %by, %ly
  %okx = icmp ult i32 %x, %w
  %oky = icmp ult i32 %y, %h
  %ok = and i1 %okx, %oky
  br i1 %ok, label %dispatch, label %exit

dispatch:
  %is_seq = icmp eq i32 %h, 68
  br i1 %is_seq, label %seq_entry, label %copy

copy:
  %c = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %lo = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %c, <4 x float> zeroinitializer)
  %hi = call <4 x float> @llvm.minnum.v4f32(<4 x float> %lo, <4 x float> <float 1.0, float 1.0, float 1.0, float 1.0>)
  %sc = fmul <4 x float> %hi, <float 65535.0, float 65535.0, float 65535.0, float 65535.0>
  %rd = fadd <4 x float> %sc, <float 0.5, float 0.5, float 0.5, float 0.5>
  %qi = fptoui <4 x float> %rd to <4 x i32>
  %q = trunc <4 x i32> %qi to <4 x i16>
  %packed = bitcast <4 x i16> %q to <2 x i32>
  %row = mul i32 %y, %w
  %idx = add i32 %row, %x
  %off = shl i32 %idx, 3
  call void @llvm.amdgcn.raw.buffer.store.v2i32(<2 x i32> %packed, <4 x i32> %vo, i32 %off, i32 0, i32 0)
  br label %exit

seq_entry:
  %flw = extractelement <4 x i32> %dims, i32 2
  %foff_base = extractelement <4 x i32> %dims, i32 3
  %ptcur = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %tcur = load <8 x i32>, ptr addrspace(4) %ptcur, align 32, !invariant.load !0
  %pvw = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %vw = load <4 x i32>, ptr addrspace(4) %pvw, align 16, !invariant.load !0
  %psm = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %sm = load <4 x i32>, ptr addrspace(4) %psm, align 16, !invariant.load !0

  %srow = mul i32 %y, 120
  %sidx = add i32 %srow, %x
  %soff4 = shl i32 %sidx, 2

  %in_v3 = icmp ult i32 %sidx, 1024
  br i1 %in_v3, label %do_v3, label %after_v3

do_v3:
  %v3_w = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vw, i32 %soff4, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %v3_w, <4 x i32> %vo, i32 %soff4, i32 0, i32 0)
  br label %after_v3

after_v3:
  %in_v2 = icmp ult i32 %sidx, 2040
  br i1 %in_v2, label %do_v2, label %after_v2

do_v2:
  %v2_off = add i32 %soff4, 4096
  %v2_w = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vw, i32 %v2_off, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %v2_w, <4 x i32> %vo, i32 %v2_off, i32 0, i32 0)
  br label %after_v2

after_v2:
  %in_misc = icmp ult i32 %sidx, 8
  br i1 %in_misc, label %do_misc, label %after_misc

do_misc:
  %misc_src = add i32 %soff4, 143360
  %misc_dst = add i32 %soff4, 12256
  %misc_w = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vw, i32 %misc_src, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %misc_w, <4 x i32> %vo, i32 %misc_dst, i32 0, i32 0)
  br label %after_misc

after_misc:
  %gk_src = add i32 %soff4, 155648
  %gk_dst = add i32 %soff4, 12288
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vw, i32 %gk_src, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %gk, <4 x i32> %vo, i32 %gk_dst, i32 0, i32 0)

  %soff8 = shl i32 %sidx, 3
  %f_src = add i32 %foff_base, %soff8
  %f_val = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vw, i32 %f_src, i32 0, i32 0)

  %gy_ge13 = icmp uge i32 %y, 13
  %gy_lt41 = icmp ult i32 %y, 41
  %gx_ge42 = icmp uge i32 %x, 42
  %gx_lt78 = icmp ult i32 %x, 78
  %in_cy = and i1 %gy_ge13, %gy_lt41
  %in_cx = and i1 %gx_ge42, %gx_lt78
  %in_crop = and i1 %in_cy, %in_cx
  br i1 %in_crop, label %do_crop, label %after_crop

do_crop:
  %cy_rel = sub i32 %y, 13
  %cx_rel = sub i32 %x, 42
  %crow = mul i32 %cy_rel, 36
  %cidx = add i32 %crow, %cx_rel
  %coff8 = shl i32 %cidx, 3
  %crop_dst = add i32 %coff8, 53088
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %f_val, <4 x i32> %vo, i32 %crop_dst, i32 0, i32 0)
  br label %after_crop

after_crop:
  %hasfg = icmp uge i32 %gk, 1048576
  %gk_nz = icmp ne i32 %gk, 0
  %gk_lt = icmp ult i32 %gk, 1048576
  %isanc_gk = and i1 %gk_nz, %gk_lt

  %x2 = lshr i32 %x, 1
  %y2 = lshr i32 %y, 1
  %arow = mul i32 %y2, 60
  %aidx = add i32 %arow, %x2
  %a_aligned = and i32 %aidx, -4
  %a_src = add i32 %a_aligned, 2048
  %a_word = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vw, i32 %a_src, i32 0, i32 0)
  %a_sh0 = and i32 %aidx, 3
  %a_sh = shl i32 %a_sh0, 3
  %a_byte0 = lshr i32 %a_word, %a_sh
  %a_byte = and i32 %a_byte0, 255
  %isanc_h = icmp uge i32 %a_byte, 2
  %isanc = or i1 %isanc_gk, %isanc_h

  %u00 = call { float, float, i1 } @unpack_f(<2 x float> %f_val)
  %fx = extractvalue { float, float, i1 } %u00, 0
  %fy = extractvalue { float, float, i1 } %u00, 1
  %sub8 = extractvalue { float, float, i1 } %u00, 2

  %x1a = add i32 %x, 1
  %x1 = call i32 @llvm.umin.i32(i32 %x1a, i32 119)
  %y1a = add i32 %y, 1
  %y1 = call i32 @llvm.umin.i32(i32 %y1a, i32 67)
  %sidx10 = add i32 %srow, %x1
  %soff10 = shl i32 %sidx10, 3
  %fsrc10 = add i32 %foff_base, %soff10
  %f10 = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vw, i32 %fsrc10, i32 0, i32 0)
  %srow01 = mul i32 %y1, 120
  %sidx01 = add i32 %srow01, %x
  %soff01 = shl i32 %sidx01, 3
  %fsrc01 = add i32 %foff_base, %soff01
  %f01 = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vw, i32 %fsrc01, i32 0, i32 0)
  %sidx11 = add i32 %srow01, %x1
  %soff11 = shl i32 %sidx11, 3
  %fsrc11 = add i32 %foff_base, %soff11
  %f11 = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vw, i32 %fsrc11, i32 0, i32 0)

  %u10 = call { float, float, i1 } @unpack_f(<2 x float> %f10)
  %x10 = extractvalue { float, float, i1 } %u10, 0
  %y10 = extractvalue { float, float, i1 } %u10, 1
  %u01 = call { float, float, i1 } @unpack_f(<2 x float> %f01)
  %x01 = extractvalue { float, float, i1 } %u01, 0
  %y01 = extractvalue { float, float, i1 } %u01, 1
  %u11 = call { float, float, i1 } @unpack_f(<2 x float> %f11)
  %x11 = extractvalue { float, float, i1 } %u11, 0
  %y11 = extractvalue { float, float, i1 } %u11, 1

  %mxa = call float @llvm.maxnum.f32(float %fx, float %x10)
  %mxb = call float @llvm.maxnum.f32(float %x01, float %x11)
  %mx = call float @llvm.maxnum.f32(float %mxa, float %mxb)
  %nxa = call float @llvm.minnum.f32(float %fx, float %x10)
  %nxb = call float @llvm.minnum.f32(float %x01, float %x11)
  %nx = call float @llvm.minnum.f32(float %nxa, float %nxb)
  %mya = call float @llvm.maxnum.f32(float %fy, float %y10)
  %myb = call float @llvm.maxnum.f32(float %y01, float %y11)
  %my = call float @llvm.maxnum.f32(float %mya, float %myb)
  %nya = call float @llvm.minnum.f32(float %fy, float %y10)
  %nyb = call float @llvm.minnum.f32(float %y01, float %y11)
  %ny = call float @llvm.minnum.f32(float %nya, float %nyb)
  %sprx = fsub float %mx, %nx
  %spry = fsub float %my, %ny
  %spr = call float @llvm.maxnum.f32(float %sprx, float %spry)
  %axm0 = call float @llvm.fabs.f32(float %mx)
  %axm1 = call float @llvm.fabs.f32(float %nx)
  %axm = call float @llvm.maxnum.f32(float %axm0, float %axm1)
  %aym0 = call float @llvm.fabs.f32(float %my)
  %aym1 = call float @llvm.fabs.f32(float %ny)
  %aym = call float @llvm.maxnum.f32(float %aym0, float %aym1)
  %vm4 = fadd float %axm, %aym
  %coh_t0 = fmul float %vm4, 0x3FC0000000000000 ; 0.125 * vm4
  %coh_thr = call float @llvm.maxnum.f32(float %coh_t0, float 2.0)
  %coh = fcmp ole float %spr, %coh_thr

  %afx = call float @llvm.fabs.f32(float %fx)
  %afy = call float @llvm.fabs.f32(float %fy)
  %vmb = fadd float %afx, %afy
  %vmg = select i1 %coh, float %vmb, float 0.0
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi

  %x16 = shl i32 %x, 4
  %y16 = shl i32 %y, 4
  %ux0 = add i32 %x16, 8
  %uy0 = add i32 %y16, 8
  %u = uitofp i32 %ux0 to float
  %v = uitofp i32 %uy0 to float
  %hfx = fmul float %fx, 0.5
  %hfy = fmul float %fy, 0.5
  %up = fadd float %u, %hfx
  %vp = fadd float %v, %hfy
  %uc = fsub float %u, %hfx
  %vc = fsub float %v, %hfy
  %sp = call <4 x float> @sample_hl(<8 x i32> %tc, <4 x i32> %sm, float %up, float %vp)
  %sc_m = call <4 x float> @sample_hl(<8 x i32> %tcur, <4 x i32> %sm, float %uc, float %vc)
  %e0_r = call float @diff3_k(<4 x float> %sp, <4 x float> %sc_m)
  %hla = extractelement <4 x float> %sp, i32 3
  %hlb = extractelement <4 x float> %sc_m, i32 3
  %hl_mn = call float @llvm.minnum.f32(float %hla, float %hlb)
  %both_w = fcmp oge float %hl_mn, 4.0
  %aw = fcmp oge float %hla, 4.0
  %bw = fcmp oge float %hlb, 4.0
  %hla_s = fsub float %hla, 4.0
  %hlb_s = fsub float %hlb, 4.0
  %hla_c = select i1 %aw, float %hla_s, float %hla
  %hlb_c = select i1 %bw, float %hlb_s, float %hlb
  %hl_mx = call float @llvm.maxnum.f32(float %hla_c, float %hlb_c)
  %hl_s = fmul float %hl_mx, 4.0
  %hl_d = fadd float %hl_s, 1.0
  %e0_f = fdiv arcp afn float %e0_r, %hl_d
  %emov = select i1 %coh, float %e0_f, float %e0_r
  %mfit = fcmp olt float %emov, 0.25

  %hl_hi = fadd float %hl_mx, 4.0
  %hl_tag = select i1 %both_w, float %hl_hi, float %hl_mx
  %hl_mov = select i1 %coh, float %hl_tag, float 0.0
  %sp_st = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %u, float %v, <8 x i32> %tc, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %sc_st = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %u, float %v, <8 x i32> %tcur, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %l_st0 = call float @sum3(<4 x float> %sp_st)
  %l_st1 = call float @sum3(<4 x float> %sc_st)
  %l_st_s = fadd float %l_st0, %l_st1
  %l_stat = fmul float %l_st_s, 0.5
  %l_mv0 = call float @sum3(<4 x float> %sp)
  %l_mv1 = call float @sum3(<4 x float> %sc_m)
  %l_mv_s = fadd float %l_mv0, %l_mv1
  %l_mov = fmul float %l_mv_s, 0.5
  %dl_s = fsub float %l_stat, %l_mov
  %mv_act = fcmp ogt float %vmg, 2.0
  %is_wire_fol = fcmp oge float %hl_mov, 4.0
  %gl_hi = fcmp ogt float %hl_mov, 0x3FD99999A0000000 ; 0.40
  %gl_br = fcmp olt float %dl_s, 0xBFA99999A0000000 ; -0.05
  %is_glint = and i1 %gl_hi, %gl_br
  %hl_any = or i1 %is_wire_fol, %is_glint
  %hl_act = and i1 %mv_act, %hl_any

  %nofgb = and i32 %flw, 16
  %nofgn = icmp eq i32 %nofgb, 0
  %fgk_ws0 = and i1 %hasfg, %mfit
  %fgkill_ws = and i1 %fgk_ws0, %nofgn
  %fgk0 = and i1 %mov_obj, %mfit
  %fgk1 = and i1 %fgk0, %nofgn
  %fgkill = or i1 %fgk1, %hl_act

  %b0 = zext i1 %hasfg to i32
  %b1a = zext i1 %isanc to i32
  %b1 = shl i32 %b1a, 1
  %b2a = zext i1 %vmg_hi to i32
  %b2 = shl i32 %b2a, 2
  %b3a = zext i1 %mov_obj to i32
  %b3 = shl i32 %b3a, 3
  %b4a = zext i1 %mfit to i32
  %b4 = shl i32 %b4a, 4
  %b5a = zext i1 %fgkill_ws to i32
  %b5 = shl i32 %b5a, 5
  %b6a = zext i1 %fgkill to i32
  %b6 = shl i32 %b6a, 6
  %b7a = zext i1 %sub8 to i32
  %b7 = shl i32 %b7a, 7

  %m01 = or i32 %b0, %b1
  %m23 = or i32 %b2, %b3
  %m45 = or i32 %b4, %b5
  %m67 = or i32 %b6, %b7
  %m0123 = or i32 %m01, %m23
  %m4567 = or i32 %m45, %m67
  %mask_u32 = or i32 %m0123, %m4567
  %mask_u8 = trunc i32 %mask_u32 to i8
  %mask_dst = add i32 %sidx, 44928
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %mask_u8, <4 x i32> %vo, i32 %mask_dst, i32 0, i32 0)
  br label %exit

exit:
  ret void
}
attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
