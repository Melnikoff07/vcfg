; user_sgprs=2
; tgid_y=1
; Gamepad menu & diagnostics overlay compute shader (ui8.ll) for mode 8.
; Renders a semi-transparent panel with border, highlight bar, slider bar, and 8x8 bitmap font (2x2 scaled = 16x16 px per char)
; onto BOTH the generated frame (T#out at +0x40) and the real frame (T#cur at +0x20) after warp8/rcas8.
; Reads the clean current frame pixel from T#cur once, blends the UI panel/glyph over it, and writes to T#out and T#cur
; (prevents double-blending when C becomes P on the next frame).
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare <4 x i32> @llvm.amdgcn.raw.buffer.load.v4i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

define internal <4 x float> @blend_over(<4 x float> %dst, <4 x float> %src, float %a) alwaysinline {
  %av0 = insertelement <4 x float> undef, float %a, i32 0
  %av = shufflevector <4 x float> %av0, <4 x float> undef, <4 x i32> zeroinitializer
  %d = fsub <4 x float> %src, %dst
  %m = fmul <4 x float> %d, %av
  %r = fadd <4 x float> %dst, %m
  %da = extractelement <4 x float> %dst, i32 3
  %out = insertelement <4 x float> %r, float %da, i32 3
  ret <4 x float> %out
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %prc = getelementptr i8, ptr addrspace(4) %tbl, i64 512
  %rc = load <4 x i32>, ptr addrspace(4) %prc, align 16, !invariant.load !0
  %upos = extractelement <4 x i32> %rc, i32 2
  %udim = extractelement <4 x i32> %rc, i32 3
  %uw = and i32 %udim, 65535
  %uh = lshr i32 %udim, 16
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %qx = add i32 %bxp, %lx
  %qy = add i32 %byp, %ly
  %inx = icmp ult i32 %qx, %uw
  %iny = icmp ult i32 %qy, %uh
  %in = and i1 %inx, %iny
  br i1 %in, label %body, label %done

body:
  %ux0 = and i32 %upos, 65535
  %uy0 = lshr i32 %upos, 16
  %x = add i32 %ux0, %qx
  %y = add i32 %uy0, %qy
  %pui = getelementptr i8, ptr addrspace(4) %tbl, i64 528
  %vui = load <4 x i32>, ptr addrspace(4) %pui, align 16, !invariant.load !0
  %h0 = call <4 x i32> @llvm.amdgcn.raw.buffer.load.v4i32(<4 x i32> %vui, i32 0, i32 0, i32 0)
  %h1 = call <4 x i32> @llvm.amdgcn.raw.buffer.load.v4i32(<4 x i32> %vui, i32 16, i32 0, i32 0)
  %opq = extractelement <4 x i32> %h0, i32 0
  %selr = extractelement <4 x i32> %h0, i32 1
  %barr = extractelement <4 x i32> %h0, i32 2
  %barw = extractelement <4 x i32> %h0, i32 3
  %mfl = extractelement <4 x i32> %h1, i32 0
  %opf0 = uitofp i32 %opq to float
  %opf1 = fmul float %opf0, 0.00390625
  %op_bg = call float @llvm.minnum.f32(float %opf1, float 1.0)
  %col = lshr i32 %qx, 4
  %row = lshr i32 %qy, 4
  %col_cl = and i32 %col, 31
  %row_cl = and i32 %row, 15
  %full_m0 = and i32 %mfl, 1
  %full_m = icmp ne i32 %full_m0, 0
  %is_hdr0 = icmp eq i32 %row_cl, 0
  %is_hdr = and i1 %is_hdr0, %full_m
  %is_sel = icmp eq i32 %row_cl, %selr
  %bg0 = select i1 %is_hdr, <4 x float> <float 0.125, float 0.25, float 0.375, float 1.0>, <4 x float> <float 0.0625, float 0.0625, float 0.125, float 1.0>
  %bg1 = select i1 %is_sel, <4 x float> <float 0.1875, float 0.3125, float 0.5, float 1.0>, <4 x float> %bg0
  %op_hi0 = fadd float %op_bg, 0.125
  %op_hi = call float @llvm.minnum.f32(float %op_hi0, float 1.0)
  %hi_row = or i1 %is_hdr, %is_sel
  %op0 = select i1 %hi_row, float %op_hi, float %op_bg
  %is_brow = icmp eq i32 %row_cl, %barr
  %ry_in = and i32 %qy, 15
  %by_ok0 = icmp uge i32 %ry_in, 13
  %by_ok1 = icmp ule i32 %ry_in, 14
  %by_ok = and i1 %by_ok0, %by_ok1
  %bx_rel = sub i32 %qx, 288
  %bx_tr0 = icmp uge i32 %qx, 288
  %bx_tr1 = icmp ult i32 %qx, 448
  %bx_tr = and i1 %bx_tr0, %bx_tr1
  %in_btr0 = and i1 %is_brow, %by_ok
  %in_btr = and i1 %in_btr0, %bx_tr
  %in_bfl0 = icmp ult i32 %bx_rel, %barw
  %in_bfl = and i1 %in_btr, %in_bfl0
  %bg2 = select i1 %in_btr, <4 x float> <float 0.1875, float 0.1875, float 0.25, float 1.0>, <4 x float> %bg1
  %bg3 = select i1 %in_bfl, <4 x float> <float 0.25, float 0.875, float 0.5, float 1.0>, <4 x float> %bg2
  %uw_m2 = sub i32 %uw, 2
  %uh_m2 = sub i32 %uh, 2
  %bd0 = icmp ult i32 %qx, 2
  %bd1 = icmp ult i32 %qy, 2
  %bd2 = icmp uge i32 %qx, %uw_m2
  %bd3 = icmp uge i32 %qy, %uh_m2
  %bd01 = or i1 %bd0, %bd1
  %bd23 = or i1 %bd2, %bd3
  %is_bd = or i1 %bd01, %bd23
  %bg4 = select i1 %is_bd, <4 x float> <float 0.25, float 0.625, float 0.875, float 1.0>, <4 x float> %bg3
  %op1 = select i1 %is_bd, float 1.0, float %op0
  %r32 = shl i32 %row_cl, 5
  %cidx = add i32 %r32, %col_cl
  %cdw_idx = and i32 %cidx, 508
  %coff = add i32 %cdw_idx, 32
  %cdw = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vui, i32 %coff, i32 0, i32 0)
  %csh0 = and i32 %cidx, 3
  %csh = shl i32 %csh0, 3
  %cshr = lshr i32 %cdw, %csh
  %cbyte = and i32 %cshr, 255
  %gid = and i32 %cbyte, 63
  %cmode = lshr i32 %cbyte, 6
  %gx_s0 = lshr i32 %qx, 1
  %gx_s = and i32 %gx_s0, 7
  %gy_s0 = lshr i32 %qy, 1
  %gy_s = and i32 %gy_s0, 7
  %goff0 = shl i32 %gid, 3
  %gy_hi0 = lshr i32 %gy_s, 2
  %gy_hi = shl i32 %gy_hi0, 2
  %foff0 = add i32 %goff0, %gy_hi
  %foff = add i32 %foff0, 544
  %fdw = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vui, i32 %foff, i32 0, i32 0)
  %gy_lo = and i32 %gy_s, 3
  %rbit = shl i32 %gy_lo, 3
  %cbit = sub i32 7, %gx_s
  %bidx = add i32 %rbit, %cbit
  %fshr = lshr i32 %fdw, %bidx
  %fbit = and i32 %fshr, 1
  %is_fg = icmp ne i32 %fbit, 0
  %has_sx = icmp ugt i32 %gx_s, 0
  %sbidx = add i32 %bidx, 1
  %sshr = lshr i32 %fdw, %sbidx
  %sbit = and i32 %sshr, 1
  %is_sh0 = icmp ne i32 %sbit, 0
  %is_sh1 = and i1 %has_sx, %is_sh0
  %not_fg = xor i1 %is_fg, true
  %is_sh = and i1 %is_sh1, %not_fg
  %cm1 = icmp eq i32 %cmode, 1
  %cm2 = icmp eq i32 %cmode, 2
  %cm3 = icmp eq i32 %cmode, 3
  %fg0 = select i1 %cm1, <4 x float> <float 1.0, float 0.875, float 0.25, float 1.0>, <4 x float> <float 1.0, float 1.0, float 1.0, float 1.0>
  %fg1 = select i1 %cm2, <4 x float> <float 0.625, float 0.625, float 0.6875, float 1.0>, <4 x float> %fg0
  %fg_col = select i1 %cm3, <4 x float> <float 0.25, float 1.0, float 0.5, float 1.0>, <4 x float> %fg1
  %col_s0 = select i1 %is_sh, <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0>, <4 x float> %bg4
  %ui_col = select i1 %is_fg, <4 x float> %fg_col, <4 x float> %col_s0
  %op_fg = or i1 %is_fg, %is_sh
  %ui_op = select i1 %op_fg, float 1.0, float %op1
  %ptc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %ptc, align 32, !invariant.load !0
  %dc = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %oc = call <4 x float> @blend_over(<4 x float> %dc, <4 x float> %ui_col, float %ui_op)
  %do_mid0 = and i32 %mfl, 2
  %do_mid = icmp ne i32 %do_mid0, 0
  br i1 %do_mid, label %draw_mid, label %after_mid

draw_mid:
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %oc, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  br label %after_mid

after_mid:
  %do_cur0 = and i32 %mfl, 4
  %do_cur = icmp ne i32 %do_cur0, 0
  br i1 %do_cur, label %draw_cur, label %done

draw_cur:
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %oc, i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  br label %done

done:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" "denormal-fp-math-f32"="preserve-sign,preserve-sign" }
!0 = !{}
