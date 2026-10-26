; user_sgprs=2
; tgid_y=1
; mode 8 post-pass (rcas8): LDS-tiled (12x12 = 8x8 workgroup + 2-px border on all sides) directional anti-aliasing
; (edge AA L/Z-span + directional AA orientation & sub-pixel blend) + Edge-Gated RCAS sharpening.
; Fixes the in-place neighbour race condition of the previous rcas8 by pre-loading a 12x12 tile (144 texels:
; packed RGBA8 + float luma = 1152 B of LDS) and barrier-synchronising the wave before any computation or store.
; Supports dynamic screen resolutions (reads width-1 and height-1 directly from T# dword 2).
; table: +0x20 T#cur (real frame), +0x40 T#out (generated mid frame),
;        +0x220 {rcas_con (float bits), rcas_flags (bit 0 = mid, bit 1 = cur, bits 2..3 = enhance mode 0/1/2,
;        bit 4 = the mid's alpha carries warp8's static-pixel flag, honoured on the mid pass only)}.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.s.barrier()
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)
declare float @llvm.fabs.f32(float)
declare float @llvm.floor.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)
declare <4 x float> @llvm.maxnum.v4f32(<4 x float>, <4 x float>)
declare <4 x float> @llvm.minnum.v4f32(<4 x float>, <4 x float>)

@lds_rgb  = internal addrspace(3) global [144 x i32] undef, align 16
@lds_luma = internal addrspace(3) global [144 x float] undef, align 16

define internal void @bar() alwaysinline {
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ret void
}

define internal float @sat(float %x) alwaysinline {
  %a = call float @llvm.maxnum.f32(float %x, float 0.0)
  %b = call float @llvm.minnum.f32(float %a, float 1.0)
  ret float %b
}

define internal float @luma(<4 x float> %c) alwaysinline {
  %r = extractelement <4 x float> %c, i32 0
  %g = extractelement <4 x float> %c, i32 1
  %b = extractelement <4 x float> %c, i32 2
  %rb = fadd float %r, %b
  %rb4 = fmul float %rb, 0.25
  %g2 = fmul float %g, 0.5
  %l = fadd float %g2, %rb4
  ret float %l
}

define internal <4 x float> @scale4(<4 x float> %a, float %s) alwaysinline {
  %t0 = insertelement <4 x float> undef, float %s, i32 0
  %tv = shufflevector <4 x float> %t0, <4 x float> undef, <4 x i32> zeroinitializer
  %r = fmul <4 x float> %a, %tv
  ret <4 x float> %r
}

define internal i32 @q8(float %v) alwaysinline {
  ; r >= 0.5 after sat, so fptoui (truncation) already equals floor: no v_floor
  %c = call float @sat(float %v)
  %s = fmul float %c, 255.0
  %r = fadd float %s, 0.5
  %i = fptoui float %r to i32
  ret i32 %i
}

define internal i32 @pack_rgba(<4 x float> %c) alwaysinline {
  %r0 = extractelement <4 x float> %c, i32 0
  %g0 = extractelement <4 x float> %c, i32 1
  %b0 = extractelement <4 x float> %c, i32 2
  %a0 = extractelement <4 x float> %c, i32 3
  %ri = call i32 @q8(float %r0)
  %gi = call i32 @q8(float %g0)
  %bi = call i32 @q8(float %b0)
  %ai = call i32 @q8(float %a0)
  %gs = shl i32 %gi, 8
  %bs = shl i32 %bi, 16
  %as = shl i32 %ai, 24
  %rg = or i32 %ri, %gs
  %ba = or i32 %bs, %as
  %pk = or i32 %rg, %ba
  ret i32 %pk
}

define internal <4 x float> @unpack_rgb(i32 %pk) alwaysinline {
  %ri = and i32 %pk, 255
  %g0 = lshr i32 %pk, 8
  %gi = and i32 %g0, 255
  %b0 = lshr i32 %pk, 16
  %bi = and i32 %b0, 255
  %a0 = lshr i32 %pk, 24
  %rf0 = uitofp i32 %ri to float
  %gf0 = uitofp i32 %gi to float
  %bf0 = uitofp i32 %bi to float
  %af0 = uitofp i32 %a0 to float
  %v0 = insertelement <4 x float> undef, float %rf0, i32 0
  %v1 = insertelement <4 x float> %v0, float %gf0, i32 1
  %v2 = insertelement <4 x float> %v1, float %bf0, i32 2
  %v3 = insertelement <4 x float> %v2, float %af0, i32 3
  %res = call <4 x float> @scale4(<4 x float> %v3, float 0x3F70101020000000) ; 1.0 / 255.0
  ret <4 x float> %res
}

define internal void @lds_put(<8 x i32> %t, i32 %bx0, i32 %by0, i32 %wm1, i32 %hm1, i32 %idx) alwaysinline {
  ; idx < 144: idx / 12 == (idx * 171) >> 11 exactly (idx < 192), on full-rate 24-bit multiplies instead of the
  ; quarter-rate v_mul_hi_u32 / v_mul_lo_u32 of udiv / urem
  %ty171 = mul nuw nsw i32 %idx, 171
  %ty = lshr i32 %ty171, 11
  %ty12 = mul nuw nsw i32 %ty, 12
  %tx = sub nuw nsw i32 %idx, %ty12
  %x0 = add i32 %bx0, %tx
  %x1 = sub i32 %x0, 2
  %xc0 = call i32 @llvm.smax.i32(i32 %x1, i32 0)
  %sx = call i32 @llvm.smin.i32(i32 %xc0, i32 %wm1)
  %y0 = add i32 %by0, %ty
  %y1 = sub i32 %y0, 2
  %yc0 = call i32 @llvm.smax.i32(i32 %y1, i32 0)
  %sy = call i32 @llvm.smin.i32(i32 %yc0, i32 %hm1)
  %c = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %sx, i32 %sy, <8 x i32> %t, i32 0, i32 0)
  %l = call float @luma(<4 x float> %c)
  %pk = call i32 @pack_rgba(<4 x float> %c)
  %pl = getelementptr [144 x float], ptr addrspace(3) @lds_luma, i32 0, i32 %idx
  store float %l, ptr addrspace(3) %pl, align 4
  %pr = getelementptr [144 x i32], ptr addrspace(3) @lds_rgb, i32 0, i32 %idx
  store i32 %pk, ptr addrspace(3) %pr, align 4
  ret void
}

define internal float @rd_l(i32 %idx) alwaysinline {
  %p = getelementptr [144 x float], ptr addrspace(3) @lds_luma, i32 0, i32 %idx
  %v = load float, ptr addrspace(3) %p, align 4
  ret float %v
}

define internal <4 x float> @rd_c(i32 %idx) alwaysinline {
  %p = getelementptr [144 x i32], ptr addrspace(3) @lds_rgb, i32 0, i32 %idx
  %pk = load i32, ptr addrspace(3) %p, align 4
  %c = call <4 x float> @unpack_rgb(i32 %pk)
  ret <4 x float> %c
}

define internal <4 x float> @eval_tile(<8 x i32> %t, i32 %tid, i32 %bxp, i32 %byp, i32 %lx, i32 %ly, float %con, i32 %emode, i1 %sharp, i1 %use_st) alwaysinline {
entry:
  %t2 = extractelement <8 x i32> %t, i32 2
  %wm1 = and i32 %t2, 16383
  %hm1_0 = lshr i32 %t2, 14
  %hm1 = and i32 %hm1_0, 16383
  call void @lds_put(<8 x i32> %t, i32 %bxp, i32 %byp, i32 %wm1, i32 %hm1, i32 %tid)
  %id1 = add i32 %tid, 64
  call void @lds_put(<8 x i32> %t, i32 %bxp, i32 %byp, i32 %wm1, i32 %hm1, i32 %id1)
  %lt16 = icmp ult i32 %tid, 16
  br i1 %lt16, label %ld_tail, label %ld_sync
ld_tail:
  %id2 = add i32 %tid, 128
  call void @lds_put(<8 x i32> %t, i32 %bxp, i32 %byp, i32 %wm1, i32 %hm1, i32 %id2)
  br label %ld_sync
ld_sync:
  call void @bar()
  ; center index in 12x12 LDS tile: (ly + 2) * 12 + (lx + 2)
  %ly2 = add i32 %ly, 2
  %lx2 = add i32 %lx, 2
  %rowc = mul i32 %ly2, 12
  %ci = add i32 %rowc, %lx2
  %iN  = sub i32 %ci, 12
  %iS  = add i32 %ci, 12
  %iW  = sub i32 %ci, 1
  %iE  = add i32 %ci, 1
  %iNW = sub i32 %ci, 13
  %iNE = sub i32 %ci, 11
  %iSW = add i32 %ci, 11
  %iSE = add i32 %ci, 13

  %M  = call <4 x float> @rd_c(i32 %ci)
  %N  = call <4 x float> @rd_c(i32 %iN)
  %S  = call <4 x float> @rd_c(i32 %iS)
  %W  = call <4 x float> @rd_c(i32 %iW)
  %E  = call <4 x float> @rd_c(i32 %iE)

  %lM  = call float @rd_l(i32 %ci)
  %lN  = call float @rd_l(i32 %iN)
  %lS  = call float @rd_l(i32 %iS)
  %lW  = call float @rd_l(i32 %iW)
  %lE  = call float @rd_l(i32 %iE)
  %lNW = call float @rd_l(i32 %iNW)
  %lNE = call float @rd_l(i32 %iNE)
  %lSW = call float @rd_l(i32 %iSW)
  %lSE = call float @rd_l(i32 %iSE)

  %mn_nw = call float @llvm.minnum.f32(float %lN, float %lW)
  %mn_es = call float @llvm.minnum.f32(float %lE, float %lS)
  %mn4 = call float @llvm.minnum.f32(float %mn_nw, float %mn_es)
  %mx_nw = call float @llvm.maxnum.f32(float %lN, float %lW)
  %mx_es = call float @llvm.maxnum.f32(float %lE, float %lS)
  %mx4 = call float @llvm.maxnum.f32(float %mx_nw, float %mx_es)
  %mn5 = call float @llvm.minnum.f32(float %mn4, float %lM)
  %mx5 = call float @llvm.maxnum.f32(float %mx4, float %lM)
  %rng5 = fsub float %mx5, %mn5

  ; 5-tap & 9-tap local extrema + directional structure gate (shared by RCAS & Enhance AA)
  %mn_d0 = call float @llvm.minnum.f32(float %lNW, float %lNE)
  %mn_d1 = call float @llvm.minnum.f32(float %lSW, float %lSE)
  %mn_d  = call float @llvm.minnum.f32(float %mn_d0, float %mn_d1)
  %mn9   = call float @llvm.minnum.f32(float %mn5, float %mn_d)
  %mx_d0 = call float @llvm.maxnum.f32(float %lNW, float %lNE)
  %mx_d1 = call float @llvm.maxnum.f32(float %lSW, float %lSE)
  %mx_d  = call float @llvm.maxnum.f32(float %mx_d0, float %mx_d1)
  %mx9   = call float @llvm.maxnum.f32(float %mx5, float %mx_d)
  %rng9  = fsub float %mx9, %mn9
  %rng9e = fadd float %rng9, 0x3F10000000000000 ; 2^-14

  ; Directional gradient gate (distinguishes coherent edges/wires from Nyquist foliage/glint speckles)
  %dNS0 = fsub float %lN, %lS
  %dNS  = call float @llvm.fabs.f32(float %dNS0)
  %dWE0 = fsub float %lW, %lE
  %dWE  = call float @llvm.fabs.f32(float %dWE0)
  %dCard = call float @llvm.maxnum.f32(float %dNS, float %dWE)
  %dD10 = fsub float %lNE, %lSW
  %dD1  = call float @llvm.fabs.f32(float %dD10)
  %dD20 = fsub float %lNW, %lSE
  %dD2  = call float @llvm.fabs.f32(float %dD20)
  %dDiag0 = call float @llvm.maxnum.f32(float %dD1, float %dD2)
  %dDiag = fmul float %dDiag0, 0.5
  %gDir = fadd float %dCard, %dDiag
  %tg0 = fdiv arcp afn float %gDir, %rng9e
  %tg1 = fmul float %tg0, 1.5
  %tex_gate = call float @sat(float %tg1)
  %s_nw = fadd float %lN, %lW
  %s_es = fadd float %lE, %lS
  %s4l = fadd float %s_nw, %s_es
  br i1 %sharp, label %rcas_w, label %rcas_w_done

rcas_w:
  ; RCAS adaptive lobe weight (5-tap extrema + Karis highlight compression)
  %mx5e = fadd float %mx5, 0x3F10000000000000 ; 2^-14
  %den0 = fmul float %mx5e, 4.0
  %hmin = fdiv arcp afn float %mn5, %den0
  %nhmin = fsub float 0.0, %hmin
  %num1 = fsub float 1.0, %mx5
  %mn5m1 = fsub float %mn5, 1.0
  %den1a = fmul float %mn5m1, 4.0
  %den1 = fsub float %den1a, 0x3F10000000000000
  %hmax = fdiv arcp afn float %num1, %den1
  %lobe0 = call float @llvm.maxnum.f32(float %nhmin, float %hmax)
  %ncon = fsub float 0.0, %con
  %lobe1 = call float @llvm.maxnum.f32(float %ncon, float %lobe0)
  %w0_raw = call float @llvm.minnum.f32(float %lobe1, float 0.0)
  %k_den = fadd float 1.0, %mx5
  %w_karis = fdiv arcp afn float 1.0, %k_den
  %w0 = fmul float %w0_raw, %w_karis

  %avg4 = fmul float %s4l, 0.25
  %nz0 = fsub float %avg4, %lM
  %anz = call float @llvm.fabs.f32(float %nz0)
  %rng5e = fadd float %rng5, 0x3F10000000000000
  %nz1 = fdiv arcp afn float %anz, %rng5e
  %nz = call float @sat(float %nz1)
  %dn0 = fmul float %nz, 0x3FEB333340000000 ; 0.85
  %dn1 = fsub float 1.0, %dn0
  %dn_s = call float @sat(float %dn1)
  %dn = fmul float %dn_s, %tex_gate
  %w_sharp = fmul float %w0, %dn
  %om_tg = fsub float 1.0, %tex_gate
  %nz_ex0 = fsub float %nz, 0x3FDCCCCCC0000000 ; 0.45
  %nz_ex1 = fmul float %nz_ex0, 2.0
  %nz_g = call float @sat(float %nz_ex1)
  %con_g0 = fmul float %con, 8.0
  %con_g = call float @sat(float %con_g0)
  %w_sh0 = fmul float %om_tg, %nz_g
  %w_sh1 = fmul float %w_sh0, 0x3FB99999A0000000 ; 0.10
  %w_sh2 = fmul float %w_sh1, %w_karis
  %w_shim = fmul float %w_sh2, %con_g
  %w_rcas0 = fadd float %w_sharp, %w_shim
  br label %rcas_w_done

rcas_w_done:
  %w_rcas = phi float [ %w_rcas0, %rcas_w ], [ 0.0, %ld_sync ]
  %has_enh = icmp ugt i32 %emode, 0
  ; static pixel (warp8 alpha flag 1.0 = P == C here): the real frames show it raw, so AA only in the mid would
  ; alternate at 30 Hz; take the rcas_only path for it (waves with every lane static skip the AA block)
  %aM = extractelement <4 x float> %M, i32 3
  %st_c = fcmp ogt float %aM, 0x3FEFF7CEE0000000 ; 0.999
  %st_skip = and i1 %use_st, %st_c
  %nst = xor i1 %st_skip, true
  %is_str = icmp uge i32 %emode, 2
  %thr_abs = select i1 %is_str, float 0.015625, float 0.0234375 ; 4/256 vs 6/256
  %thr_rel = select i1 %is_str, float 0.0625, float 0.09375     ; 4/64 vs 6/64
  %t_rel0 = fmul float %mx9, %thr_rel
  %ethr = call float @llvm.maxnum.f32(float %thr_abs, float %t_rel0)
  %er0 = fsub float %rng9, %ethr
  %er1 = fmul float %er0, 32.0
  %edge_ramp = call float @sat(float %er1)
  ; edge_ramp == 0 makes wAA == 0 and w_eff == w_rcas: the AA block returns M bit for bit, so those lanes take the
  ; rcas_only path too (waves with no edge lane skip the AA block)
  %has_edge = fcmp ogt float %edge_ramp, 0.0
  %doaa0 = and i1 %has_enh, %nst
  %doaa = and i1 %doaa0, %has_edge
  br i1 %doaa, label %do_aa, label %rcas_only

do_aa:
  %NW = call <4 x float> @rd_c(i32 %iNW)
  %NE = call <4 x float> @rd_c(i32 %iNE)
  %SW = call <4 x float> @rd_c(i32 %iSW)
  %SE = call <4 x float> @rd_c(i32 %iSE)


  ; directional AA Sobel orientation: horizontal vs vertical
  %lW2 = fmul float %lW, 2.0
  %lM2 = fmul float %lM, 2.0
  %lE2 = fmul float %lE, 2.0
  %lN2 = fmul float %lN, 2.0
  %lS2 = fmul float %lS, 2.0
  %eh0a = fadd float %lNW, %lSW
  %eh0b = fsub float %eh0a, %lW2
  %eh0  = call float @llvm.fabs.f32(float %eh0b)
  %eh1a = fadd float %lN, %lS
  %eh1b = fsub float %eh1a, %lM2
  %eh1c = call float @llvm.fabs.f32(float %eh1b)
  %eh1  = fmul float %eh1c, 2.0
  %eh2a = fadd float %lNE, %lSE
  %eh2b = fsub float %eh2a, %lE2
  %eh2  = call float @llvm.fabs.f32(float %eh2b)
  %eh01 = fadd float %eh0, %eh1
  %edgeH = fadd float %eh01, %eh2

  %ev0a = fadd float %lNW, %lNE
  %ev0b = fsub float %ev0a, %lN2
  %ev0  = call float @llvm.fabs.f32(float %ev0b)
  %ev1a = fadd float %lW, %lE
  %ev1b = fsub float %ev1a, %lM2
  %ev1c = call float @llvm.fabs.f32(float %ev1b)
  %ev1  = fmul float %ev1c, 2.0
  %ev2a = fadd float %lSW, %lSE
  %ev2b = fsub float %ev2a, %lS2
  %ev2  = call float @llvm.fabs.f32(float %ev2b)
  %ev01 = fadd float %ev0, %ev1
  %edgeV = fadd float %ev01, %ev2
  %horz = fcmp oge float %edgeH, %edgeV

  %lNeg = select i1 %horz, float %lN, float %lW
  %lPos = select i1 %horz, float %lS, float %lE
  %rgbNeg = select i1 %horz, <4 x float> %N, <4 x float> %W
  %rgbPos = select i1 %horz, <4 x float> %S, <4 x float> %E
  %rgbTan1 = select i1 %horz, <4 x float> %W, <4 x float> %N
  %rgbTan2 = select i1 %horz, <4 x float> %E, <4 x float> %S

  %gn0 = fsub float %lNeg, %lM
  %gNeg = call float @llvm.fabs.f32(float %gn0)
  %gp0 = fsub float %lPos, %lM
  %gPos = call float @llvm.fabs.f32(float %gp0)
  %useNeg = fcmp oge float %gNeg, %gPos
  %gMax = call float @llvm.maxnum.f32(float %gNeg, float %gPos)
  %lOpp = select i1 %useNeg, float %lNeg, float %lPos
  %rgbOpp = select i1 %useNeg, <4 x float> %rgbNeg, <4 x float> %rgbPos
  %lSum = fadd float %lM, %lOpp
  %lEdge = fmul float %lSum, 0.5
  %gThr = fmul float %gMax, 0.25

  ; 5-step directional span search in LDS (-2, -1, 0, +1, +2 along tangent)
  %tan_step = select i1 %horz, i32 1, i32 12
  %norm_off = select i1 %horz, i32 12, i32 1
  %norm_s   = select i1 %useNeg, i32 0, i32 %norm_off
  %norm_n   = select i1 %useNeg, i32 %norm_off, i32 0
  %ci_opp   = sub i32 %ci, %norm_n
  %ci_opp2  = add i32 %ci_opp, %norm_s

  %im1 = sub i32 %ci, %tan_step
  %im2 = sub i32 %im1, %tan_step
  %ip1 = add i32 %ci, %tan_step
  %ip2 = add i32 %ip1, %tan_step

  %om1 = sub i32 %ci_opp2, %tan_step
  %om2 = sub i32 %om1, %tan_step
  %op1 = add i32 %ci_opp2, %tan_step
  %op2 = add i32 %op1, %tan_step

  %lm1 = call float @rd_l(i32 %im1)
  %lm2 = call float @rd_l(i32 %im2)
  %lp1 = call float @rd_l(i32 %ip1)
  %lp2 = call float @rd_l(i32 %ip2)
  %lom1 = call float @rd_l(i32 %om1)
  %lom2 = call float @rd_l(i32 %om2)
  %lop1 = call float @rd_l(i32 %op1)
  %lop2 = call float @rd_l(i32 %op2)

  %em1a = fadd float %lm1, %lom1
  %em1b = fmul float %em1a, 0.5
  %eN1  = fsub float %em1b, %lEdge
  %em2a = fadd float %lm2, %lom2
  %em2b = fmul float %em2a, 0.5
  %eN2  = fsub float %em2b, %lEdge
  %aeN1 = call float @llvm.fabs.f32(float %eN1)
  %aeN2 = call float @llvm.fabs.f32(float %eN2)
  %cN1  = fcmp olt float %aeN1, %gThr
  %cN2a = fcmp olt float %aeN2, %gThr
  %cN2  = and i1 %cN1, %cN2a
  %dN0  = select i1 %cN1, float 2.0, float 1.0
  %dN   = select i1 %cN2, float 3.0, float %dN0
  %eN   = select i1 %cN1, float %eN2, float %eN1

  %ep1a = fadd float %lp1, %lop1
  %ep1b = fmul float %ep1a, 0.5
  %eP1  = fsub float %ep1b, %lEdge
  %ep2a = fadd float %lp2, %lop2
  %ep2b = fmul float %ep2a, 0.5
  %eP2  = fsub float %ep2b, %lEdge
  %aeP1 = call float @llvm.fabs.f32(float %eP1)
  %aeP2 = call float @llvm.fabs.f32(float %eP2)
  %cP1  = fcmp olt float %aeP1, %gThr
  %cP2a = fcmp olt float %aeP2, %gThr
  %cP2  = and i1 %cP1, %cP2a
  %dP0  = select i1 %cP1, float 2.0, float 1.0
  %dP   = select i1 %cP2, float 3.0, float %dP0
  %eP   = select i1 %cP1, float %eP2, float %eP1

  %dMin = call float @llvm.minnum.f32(float %dN, float %dP)
  %dSum = fadd float %dN, %dP
  %n_lt_p = fcmp olt float %dN, %dP
  %eEnd = select i1 %n_lt_p, float %eN, float %eP
  %mDev = fsub float %lM, %lEdge
  %prod = fmul float %eEnd, %mDev
  %goodSpan = fcmp olt float %prod, 0.0
  %rat = fdiv arcp afn float %dMin, %dSum
  %ws0 = fsub float 0.5, %rat
  %wSpan = select i1 %goodSpan, float %ws0, float 0.0

  ; 3x3 sub-pixel & L-corner contrast weight
  %sDiag0 = fadd float %lNW, %lNE
  %sDiag1 = fadd float %lSW, %lSE
  %sDiag  = fadd float %sDiag0, %sDiag1
  %sCard2 = fmul float %s4l, 2.0
  %s3x3   = fadd float %sCard2, %sDiag
  %lAvg3x3 = fmul float %s3x3, 0x3FB5555560000000 ; 1.0 / 12.0
  %sp0 = fsub float %lAvg3x3, %lM
  %asp0 = call float @llvm.fabs.f32(float %sp0)
  %sp1 = fdiv arcp afn float %asp0, %rng9e
  %subpx = call float @sat(float %sp1)
  %sp_sc = select i1 %is_str, float 1.0, float 0.75
  %gMin = call float @llvm.minnum.f32(float %gNeg, float %gPos)
  %gMaxe = fadd float %gMax, 0x3F10000000000000 ; 2^-14
  %r_rat = fdiv arcp afn float %gMin, %gMaxe
  %r_at0 = fmul float %r_rat, 0x3FEB333340000000 ; 0.85
  %r_gate = fsub float 1.0, %r_at0
  %wSub0 = fmul float %subpx, %sp_sc
  %wSubpx = fmul float %wSub0, %tex_gate

  ; Diagonal & tangent target colour for smooth staircase fill
  %dH_neg0 = fadd <4 x float> %NW, %NE
  %dH_neg  = call <4 x float> @scale4(<4 x float> %dH_neg0, float 0.5)
  %dH_pos0 = fadd <4 x float> %SW, %SE
  %dH_pos  = call <4 x float> @scale4(<4 x float> %dH_pos0, float 0.5)
  %dH_sel  = select i1 %useNeg, <4 x float> %dH_neg, <4 x float> %dH_pos

  %dV_neg0 = fadd <4 x float> %NW, %SW
  %dV_neg  = call <4 x float> @scale4(<4 x float> %dV_neg0, float 0.5)
  %dV_pos0 = fadd <4 x float> %NE, %SE
  %dV_pos  = call <4 x float> @scale4(<4 x float> %dV_pos0, float 0.5)
  %dV_sel  = select i1 %useNeg, <4 x float> %dV_neg, <4 x float> %dV_pos

  %rgbDiagOpp = select i1 %horz, <4 x float> %dH_sel, <4 x float> %dV_sel
  %tanSum = fadd <4 x float> %rgbTan1, %rgbTan2
  %rgbTanAvg = call <4 x float> @scale4(<4 x float> %tanSum, float 0.5)

  %t_opp = call <4 x float> @scale4(<4 x float> %rgbOpp, float 0.4375)
  %t_tan = call <4 x float> @scale4(<4 x float> %rgbTanAvg, float 0.3125)
  %t_dia = call <4 x float> @scale4(<4 x float> %rgbDiagOpp, float 0.25)
  %t_01  = fadd <4 x float> %t_opp, %t_tan
  %rgbTarget = fadd <4 x float> %t_01, %t_dia

  %wSpan15 = fmul float %wSpan, 1.5
  %wComb = call float @llvm.maxnum.f32(float %wSpan15, float %wSubpx)
  %max_w = select i1 %is_str, float 0.75, float 0.5625
  %wClp = call float @llvm.minnum.f32(float %wComb, float %max_w)
  %wAA0 = fmul float %edge_ramp, %wClp
  %wAA = fmul float %wAA0, %r_gate

  %difAA = fsub <4 x float> %rgbTarget, %M
  %stepAA = call <4 x float> @scale4(<4 x float> %difAA, float %wAA)
  %aa_rgb = fadd <4 x float> %M, %stepAA

  %om_er = fsub float 1.0, %edge_ramp
  %w_eff_aa = fmul float %w_rcas, %om_er
  br label %rcas_apply

rcas_only:
  br label %rcas_apply

rcas_apply:
  %base_c = phi <4 x float> [ %aa_rgb, %do_aa ], [ %M, %rcas_only ]
  %w_eff  = phi float [ %w_eff_aa, %do_aa ], [ %w_rcas, %rcas_only ]
  br i1 %sharp, label %rcas_full, label %aa_out

aa_out:
  %out_aa = insertelement <4 x float> %base_c, float %aM, i32 3
  ret <4 x float> %out_aa

rcas_full:

  %dSum_c0 = fadd float %dNS, %dWE
  %dSum_c = fadd float %dSum_c0, 0x3F10000000000000 ; 2^-14
  %dNS2 = fmul float %dNS, 2.0
  %a_ns0 = fdiv arcp afn float %dNS2, %dSum_c
  %a_we0 = fsub float 2.0, %a_ns0
  %is_sh = fcmp olt float %w_eff, 0.0
  %a_ns = select i1 %is_sh, float %a_ns0, float 1.0
  %a_we = select i1 %is_sh, float %a_we0, float 1.0
  %v_ns0 = fadd <4 x float> %N, %S
  %v_we0 = fadd <4 x float> %W, %E
  %v_ns = call <4 x float> @scale4(<4 x float> %v_ns0, float %a_ns)
  %v_we = call <4 x float> @scale4(<4 x float> %v_we0, float %a_we)
  %v4   = fadd <4 x float> %v_ns, %v_we
  %wv4  = call <4 x float> @scale4(<4 x float> %v4, float %w_eff)
  %num  = fadd <4 x float> %wv4, %base_c
  %w4   = fmul float %w_eff, 4.0
  %norm = fadd float 1.0, %w4
  %rcp  = fdiv arcp afn float 1.0, %norm
  %res0 = call <4 x float> @scale4(<4 x float> %num, float %rcp)

  %c_nw0 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %N, <4 x float> %W)
  %c_es0 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %E, <4 x float> %S)
  %c_mn4 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %c_nw0, <4 x float> %c_es0)
  %c_mn5 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %c_mn4, <4 x float> %base_c)
  %c_nw1 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %N, <4 x float> %W)
  %c_es1 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %E, <4 x float> %S)
  %c_mx4 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %c_nw1, <4 x float> %c_es1)
  %c_mx5 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %c_mx4, <4 x float> %base_c)
  %cl0 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %res0, <4 x float> %c_mn5)
  %cl1 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %cl0, <4 x float> %c_mx5)
  %out = insertelement <4 x float> %cl1, float %aM, i32 3
  ret <4 x float> %out
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %x = add i32 %bxp, %lx
  %y = add i32 %byp, %ly
  %prcfg = getelementptr i8, ptr addrspace(4) %tbl, i64 544
  %rcfg = load <4 x i32>, ptr addrspace(4) %prcfg, align 16, !invariant.load !0
  %conb = extractelement <4 x i32> %rcfg, i32 0
  %con = bitcast i32 %conb to float
  %rfl = extractelement <4 x i32> %rcfg, i32 1
  %do_mid0 = and i32 %rfl, 1
  %do_mid = icmp ne i32 %do_mid0, 0
  %em0 = lshr i32 %rfl, 2
  %emode = and i32 %em0, 3
  ; bit 4: the mid's alpha carries warp8's static-pixel flag (set by main.c only when warp8 wrote the mid)
  %stb = and i32 %rfl, 16
  %use_st = icmp ne i32 %stb, 0
  ; con == 0 (Sharpness 0): the RCAS weight is +-0 everywhere, so rcas_apply returns base_c bit for bit; skip it
  %conabs = and i32 %conb, 2147483647
  %sharp = icmp ne i32 %conabs, 0
  br i1 %do_mid, label %pass_mid, label %check_cur
pass_mid:
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  %om = call <4 x float> @eval_tile(<8 x i32> %to, i32 %tid, i32 %bxp, i32 %byp, i32 %lx, i32 %ly, float %con, i32 %emode, i1 %sharp, i1 %use_st)
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %om, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  call void @bar()
  br label %check_cur
check_cur:
  %do_cur0 = and i32 %rfl, 2
  %do_cur = icmp ne i32 %do_cur0, 0
  br i1 %do_cur, label %pass_cur, label %done
pass_cur:
  %ptc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %ptc, align 32, !invariant.load !0
  %oc = call <4 x float> @eval_tile(<8 x i32> %tc, i32 %tid, i32 %bxp, i32 %byp, i32 %lx, i32 %ly, float %con, i32 %emode, i1 %sharp, i1 false)
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %oc, i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  br label %done
done:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" "denormal-fp-math-f32"="preserve-sign,preserve-sign" }
!0 = !{}
