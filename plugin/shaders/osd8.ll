; user_sgprs=2
; tgid_y=1
; mode 8 overlay pass (osd8): semi-transparent gamepad menu & diagnostics HUD on both mid and real frames.
; Renders a 2x-scaled 8x8 bitmap font (16x16 px per cell) and styled panel rows from GPU memory.
; table: +0x20 T#cur, +0x40 T#out, +0x200 V#font (96 ASCII glyphs x 8 B = 768 B),
;        +0x210 V#osd (64 B row styles + 512 B 16x32 char grid),
;        +0x220 {rcas_con, rcas_flags, osd_xy (x0 | y0<<16), osd_wh (cols | rows<<8 | alpha_u8<<16 | flags<<24)}.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)

define internal <4 x float> @lerp4(<4 x float> %a, <4 x float> %b, float %t) alwaysinline {
  %t0 = insertelement <4 x float> undef, float %t, i32 0
  %tv = shufflevector <4 x float> %t0, <4 x float> undef, <4 x i32> zeroinitializer
  %d = fsub <4 x float> %b, %a
  %m = fmul <4 x float> %d, %tv
  %r = fadd <4 x float> %a, %m
  ret <4 x float> %r
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %qx = add i32 %bxp, %lx
  %qy = add i32 %byp, %ly
  %prcfg = getelementptr i8, ptr addrspace(4) %tbl, i64 544
  %rcfg = load <4 x i32>, ptr addrspace(4) %prcfg, align 16, !invariant.load !0
  %xy = extractelement <4 x i32> %rcfg, i32 2
  %wh = extractelement <4 x i32> %rcfg, i32 3
  %cols = and i32 %wh, 255
  %rows0 = lshr i32 %wh, 8
  %rows = and i32 %rows0, 255
  %pw = shl i32 %cols, 4
  %ph = shl i32 %rows, 4
  %inx = icmp ult i32 %qx, %pw
  %iny = icmp ult i32 %qy, %ph
  %in = and i1 %inx, %iny
  br i1 %in, label %body, label %done

body:
  %x0 = and i32 %xy, 65535
  %y0 = lshr i32 %xy, 16
  %x = add i32 %x0, %qx
  %y = add i32 %y0, %qy
  %au0 = lshr i32 %wh, 16
  %au = and i32 %au0, 255
  %af0 = uitofp i32 %au to float
  %bga = fmul float %af0, 0.00390625 ; 1/256
  %ofl = lshr i32 %wh, 24
  ; cell & intra-glyph coordinates (16x16 px per cell = 2x of 8x8 font)
  %col = lshr i32 %qx, 4
  %row = lshr i32 %qy, 4
  %fx0 = lshr i32 %qx, 1
  %fx = and i32 %fx0, 7
  %fy0 = lshr i32 %qy, 1
  %fy = and i32 %fy0, 7
  %pvf = getelementptr i8, ptr addrspace(4) %tbl, i64 512
  %vfont = load <4 x i32>, ptr addrspace(4) %pvf, align 16, !invariant.load !0
  %pvo = getelementptr i8, ptr addrspace(4) %tbl, i64 528
  %vosd = load <4 x i32>, ptr addrspace(4) %pvo, align 16, !invariant.load !0
  ; load row style dword at row * 4
  %rsoff = shl i32 %row, 2
  %rst = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vosd, i32 %rsoff, i32 0, i32 0)
  %rtype = and i32 %rst, 255
  %vcol0 = lshr i32 %rst, 8
  %vcol = and i32 %vcol0, 255
  %vclr0 = lshr i32 %rst, 16
  %vclr = and i32 %vclr0, 255
  %bend = lshr i32 %rst, 24
  ; load ASCII character at 64 + row * 32 + (col & ~3)
  %ro32 = shl i32 %row, 5
  %ca4 = and i32 %col, 252
  %co0 = add i32 %ro32, %ca4
  %co = add i32 %co0, 64
  %cdw = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vosd, i32 %co, i32 0, i32 0)
  %cbs = and i32 %col, 3
  %csh = shl i32 %cbs, 3
  %ch0 = lshr i32 %cdw, %csh
  %ch = and i32 %ch0, 255
  %chi0 = sub i32 %ch, 32
  %chi1 = call i32 @llvm.smax.i32(i32 %chi0, i32 0)
  %chi = call i32 @llvm.smin.i32(i32 %chi1, i32 95)
  ; load 8x8 glyph dword from V#font: (chi * 8) + (fy & 4)
  %go0 = shl i32 %chi, 3
  %fy4 = and i32 %fy, 4
  %go = add i32 %go0, %fy4
  %gdw = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vfont, i32 %go, i32 0, i32 0)
  %fym3 = and i32 %fy, 3
  %gsh = shl i32 %fym3, 3
  %grow0 = lshr i32 %gdw, %gsh
  %grow = and i32 %grow0, 255
  %bpos = sub i32 7, %fx
  %gbit0 = lshr i32 %grow, %bpos
  %gbit = and i32 %gbit0, 1
  %ison = icmp ne i32 %gbit, 0
  ; 1-texel horizontal drop shadow (bit to the left: 8 - fx when fx > 0)
  %spos = sub i32 8, %fx
  %sbit0 = lshr i32 %grow, %spos
  %sbit1 = and i32 %sbit0, 1
  %fxgt0 = icmp ugt i32 %fx, 0
  %sh0 = icmp ne i32 %sbit1, 0
  %issh = and i1 %fxgt0, %sh0
  ; panel border check (outer 2 px)
  %pwm2 = sub i32 %pw, 2
  %phm2 = sub i32 %ph, 2
  %bl0 = icmp ult i32 %qx, 2
  %br0 = icmp uge i32 %qx, %pwm2
  %bt0 = icmp ult i32 %qy, 2
  %bb0 = icmp uge i32 %qy, %phm2
  %bx0 = or i1 %bl0, %br0
  %by0 = or i1 %bt0, %bb0
  %isbdr = or i1 %bx0, %by0
  ; background colour by row_type (0=normal, 1=header, 2=selected, 3=footer) and progress bar
  %ishdr = icmp eq i32 %rtype, 1
  %issel = icmp eq i32 %rtype, 2
  %inval = icmp uge i32 %col, %vcol
  %inbar0 = icmp ult i32 %col, %bend
  %inbar = and i1 %inval, %inbar0
  %bg_norm = select i1 %inbar, <4 x float> <float 0.125, float 0.25, float 0.375, float 1.0>, <4 x float> <float 0.0625, float 0.0625, float 0.09375, float 1.0>
  %bg_sel0 = select i1 %inbar, <4 x float> <float 0.25, float 0.5, float 0.625, float 1.0>, <4 x float> <float 0.1875, float 0.3125, float 0.4375, float 1.0>
  %bg0 = select i1 %issel, <4 x float> %bg_sel0, <4 x float> %bg_norm
  %bg1 = select i1 %ishdr, <4 x float> <float 0.125, float 0.25, float 0.375, float 1.0>, <4 x float> %bg0
  %bg2 = select i1 %issh, <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0>, <4 x float> %bg1
  ; foreground glyph colour
  %fg_lbl = select i1 %issel, <4 x float> <float 1.0, float 0.875, float 0.25, float 1.0>, <4 x float> <float 0.9375, float 0.9375, float 0.9375, float 1.0>
  %fg_lblh = select i1 %ishdr, <4 x float> <float 0.375, float 0.875, float 1.0, float 1.0>, <4 x float> %fg_lbl
  %vc1 = icmp eq i32 %vclr, 1
  %vc2 = icmp eq i32 %vclr, 2
  %vc3 = icmp eq i32 %vclr, 3
  %fv0 = select i1 %vc3, <4 x float> <float 0.625, float 0.625, float 0.625, float 1.0>, <4 x float> <float 1.0, float 1.0, float 1.0, float 1.0>
  %fv1 = select i1 %vc2, <4 x float> <float 0.375, float 0.875, float 1.0, float 1.0>, <4 x float> %fv0
  %fv2 = select i1 %vc1, <4 x float> <float 0.25, float 1.0, float 0.375, float 1.0>, <4 x float> %fv1
  %fg = select i1 %inval, <4 x float> %fv2, <4 x float> %fg_lblh
  %px0 = select i1 %ison, <4 x float> %fg, <4 x float> %bg2
  %px = select i1 %isbdr, <4 x float> <float 0.25, float 0.625, float 0.875, float 1.0>, <4 x float> %px0
  %fg_or_bdr = or i1 %ison, %isbdr
  %alpha = select i1 %fg_or_bdr, float 1.0, float %bga
  ; sample clean T#cur (unmodified by OSD yet) so T#prev's old menu state never ghosts into T#out
  %ptc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %ptc, align 32, !invariant.load !0
  %cc0 = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %cc1 = call <4 x float> @lerp4(<4 x float> %cc0, <4 x float> %px, float %alpha)
  %ca = extractelement <4 x float> %cc0, i32 3
  %cc = insertelement <4 x float> %cc1, float %ca, i32 3
  %do_mid0 = and i32 %ofl, 1
  %do_mid = icmp ne i32 %do_mid0, 0
  br i1 %do_mid, label %w_mid, label %chk_cur

w_mid:
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %cc, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  br label %chk_cur

chk_cur:
  %do_cur0 = and i32 %ofl, 2
  %do_cur = icmp ne i32 %do_cur0, 0
  br i1 %do_cur, label %w_cur, label %done

w_cur:
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %cc, i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  br label %done

done:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
