; user_sgprs=2
; tgid_y=1
; mode 8.4, pass 7: motion-compensated median / bilateral vector filter on L1 (960x540), 120 x 68 blocks of 8x8 (= 16x16 full-res px)
; with 8x8 sub-block contour refinement & dual-layer FG/BG motion field packing (r8).
; dispatch 30 x 68 groups; lane = block (tid>>4) * 16 + slot (tid&15): 4 blocks per wave, all 16 slots active in Pass 2.
; table: +0x80 V#L1cur, +0x90 V#L1prev, +0xF0 V#V2, +0x100 V#F (out), +0x120 V#misc, +0x150 V#GK, +0x180 V#FT (raw in)
target triple = "amdgcn--"
declare <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32>, i32, i32, i32 immarg)
declare <2 x i32> @llvm.amdgcn.raw.buffer.load.v2i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.alignbyte(i32, i32, i32)
declare i32 @llvm.amdgcn.sad.u8(i32, i32, i32)
declare void @llvm.amdgcn.s.barrier()
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)
declare i32 @llvm.umin.i32(i32, i32)
declare i32 @llvm.abs.i32(i32, i1 immarg)
declare float @llvm.floor.f32(float)
declare float @llvm.fabs.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

@keys = internal addrspace(3) global [64 x i32] undef, align 16
@vecs = internal addrspace(3) global [64 x i32] undef, align 16

; Bilateral SAD of an 8x8 L1 block returning {sad, q00, q10, q01, q11}:
; - Accumulates left 4x4 and right 4x4 quadrant SADs per 4-row half-block at zero extra memory/ALU cost
; - Applies structure-weighted DC compensation + partial-occlusion quadrant trim when qmin <= 88 (r8).
define internal { i32, i32, i32, i32, i32 } @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy, i32 %ztex, i32 %zsum) alwaysinline {
entry:
  %wm8 = sub i32 %wl, 8
  %hm8 = sub i32 %hl, 8
  %nwm8 = sub i32 0, %wm8
  %nhm8 = sub i32 0, %hm8
  %dxc0 = call i32 @llvm.smax.i32(i32 %dx, i32 %nwm8)
  %dxc = call i32 @llvm.smin.i32(i32 %dxc0, i32 %wm8)
  %dyc0 = call i32 @llvm.smax.i32(i32 %dy, i32 %nhm8)
  %dyc = call i32 @llvm.smin.i32(i32 %dyc0, i32 %hm8)
  %ndx = sub i32 0, %dxc
  %ndy = sub i32 0, %dyc
  %fcx = ashr i32 %dxc, 1
  %fcy = ashr i32 %dyc, 1
  %xc0 = sub i32 %X, %fcx
  %xmin_c = call i32 @llvm.smax.i32(i32 0, i32 %ndx)
  %xmax_c0 = sub i32 %wm8, %dxc
  %xmax_c = call i32 @llvm.smin.i32(i32 %wm8, i32 %xmax_c0)
  %xc1 = call i32 @llvm.smax.i32(i32 %xc0, i32 %xmin_c)
  %xc = call i32 @llvm.smin.i32(i32 %xc1, i32 %xmax_c)
  %xp = add i32 %xc, %dxc
  %shp = and i32 %xp, 3
  %shc = and i32 %xc, 3
  %axp = and i32 %xp, -4
  %axc = and i32 %xc, -4
  %ybc0 = sub i32 %Y, %fcy
  %ymin_c = call i32 @llvm.smax.i32(i32 0, i32 %ndy)
  %ymax_c0 = sub i32 %hm8, %dyc
  %ymax_c = call i32 @llvm.smin.i32(i32 %hm8, i32 %ymax_c0)
  %ybc1 = call i32 @llvm.smax.i32(i32 %ybc0, i32 %ymin_c)
  %ybc = call i32 @llvm.smin.i32(i32 %ybc1, i32 %ymax_c)
  %ybp = add i32 %ybc, %dyc
  br label %rowloop
rowloop:
  %rbase = phi i32 [ 0, %entry ], [ 4, %rowloop ]
  %q00_in = phi i32 [ 0, %entry ], [ %ql3, %rowloop ]
  %q10_in = phi i32 [ 0, %entry ], [ %qr3, %rowloop ]
  %mbin = phi i32 [ 0, %entry ], [ %mb3, %rowloop ]
  %mdin = phi i32 [ 0, %entry ], [ %md3, %rowloop ]
  %tpin = phi i32 [ 0, %entry ], [ %tp_out, %rowloop ]
  %tcin = phi i32 [ 0, %entry ], [ %tc_out, %rowloop ]
  %p30_in = phi i32 [ 0, %entry ], [ %qp0_3, %rowloop ]
  %p31_in = phi i32 [ 0, %entry ], [ %qp1_3, %rowloop ]
  %c30_in = phi i32 [ 0, %entry ], [ %qc0_3, %rowloop ]
  %c31_in = phi i32 [ 0, %entry ], [ %qc1_3, %rowloop ]
  %ybp_r = add i32 %ybp, %rbase
  %ybc_r = add i32 %ybc, %rbase
  %rp0 = mul i32 %ybp_r, %wl
  %ap0 = add i32 %rp0, %axp
  %rc0 = mul i32 %ybc_r, %wl
  %ac0 = add i32 %rc0, %axc
  %dp0 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap0, i32 0, i32 0)
  %dc0 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac0, i32 0, i32 0)
  %p0_0 = extractelement <3 x i32> %dp0, i32 0
  %p1_0 = extractelement <3 x i32> %dp0, i32 1
  %p2_0 = extractelement <3 x i32> %dp0, i32 2
  %c0_0 = extractelement <3 x i32> %dc0, i32 0
  %c1_0 = extractelement <3 x i32> %dc0, i32 1
  %c2_0 = extractelement <3 x i32> %dc0, i32 2
  %qp0_0 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_0, i32 %p0_0, i32 %shp)
  %qp1_0 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_0, i32 %p1_0, i32 %shp)
  %qc0_0 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_0, i32 %c0_0, i32 %shc)
  %qc1_0 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_0, i32 %c1_0, i32 %shc)
  %ql0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 %qc0_0, i32 0)
  %qr0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 %qc1_0, i32 0)
  %ma0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 0, i32 %mbin)
  %mb0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 0, i32 %ma0)
  %mc0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_0, i32 0, i32 %mdin)
  %md0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_0, i32 0, i32 %mc0)
  %is_r0 = icmp eq i32 %rbase, 0
  %p_pr0 = select i1 %is_r0, i32 %qp0_0, i32 %p30_in
  %p_pr1 = select i1 %is_r0, i32 %qp1_0, i32 %p31_in
  %c_pr0 = select i1 %is_r0, i32 %qc0_0, i32 %c30_in
  %c_pr1 = select i1 %is_r0, i32 %qc1_0, i32 %c31_in
  %tp_v00 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 %p_pr0, i32 %tpin)
  %tp_v01 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 %p_pr1, i32 %tp_v00)
  %tp_h0l = call i32 @llvm.amdgcn.alignbyte(i32 %qp1_0, i32 %qp0_0, i32 1)
  %tp_h00 = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h0l, i32 %qp0_0, i32 %tp_v01)
  %tp_h0a = lshr i32 %qp1_0, 8
  %tp_h0b = and i32 %qp1_0, 16777215
  %tp_r0 = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h0a, i32 %tp_h0b, i32 %tp_h00)
  %tc_v00 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_0, i32 %c_pr0, i32 %tcin)
  %tc_v01 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_0, i32 %c_pr1, i32 %tc_v00)
  %tc_h0l = call i32 @llvm.amdgcn.alignbyte(i32 %qc1_0, i32 %qc0_0, i32 1)
  %tc_h00 = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h0l, i32 %qc0_0, i32 %tc_v01)
  %tc_h0a = lshr i32 %qc1_0, 8
  %tc_h0b = and i32 %qc1_0, 16777215
  %tc_r0 = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h0a, i32 %tc_h0b, i32 %tc_h00)
  %ap1 = add i32 %ap0, %wl
  %ac1 = add i32 %ac0, %wl
  %dp1 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap1, i32 0, i32 0)
  %dc1 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac1, i32 0, i32 0)
  %p0_1 = extractelement <3 x i32> %dp1, i32 0
  %p1_1 = extractelement <3 x i32> %dp1, i32 1
  %p2_1 = extractelement <3 x i32> %dp1, i32 2
  %c0_1 = extractelement <3 x i32> %dc1, i32 0
  %c1_1 = extractelement <3 x i32> %dc1, i32 1
  %c2_1 = extractelement <3 x i32> %dc1, i32 2
  %qp0_1 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_1, i32 %p0_1, i32 %shp)
  %qp1_1 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_1, i32 %p1_1, i32 %shp)
  %qc0_1 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_1, i32 %c0_1, i32 %shc)
  %qc1_1 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_1, i32 %c1_1, i32 %shc)
  %ql1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_1, i32 %qc0_1, i32 %ql0)
  %qr1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_1, i32 %qc1_1, i32 %qr0)
  %ma1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_1, i32 0, i32 %mb0)
  %mb1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_1, i32 0, i32 %ma1)
  %mc1 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_1, i32 0, i32 %md0)
  %md1 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_1, i32 0, i32 %mc1)
  %tp_v0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_1, i32 %qp0_0, i32 %tp_r0)
  %tp_v1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_1, i32 %qp1_0, i32 %tp_v0)
  %tp_h1l = call i32 @llvm.amdgcn.alignbyte(i32 %qp1_1, i32 %qp0_1, i32 1)
  %tp_h10 = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h1l, i32 %qp0_1, i32 %tp_v1)
  %tp_h1a = lshr i32 %qp1_1, 8
  %tp_h1b = and i32 %qp1_1, 16777215
  %tp_r1 = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h1a, i32 %tp_h1b, i32 %tp_h10)
  %tc_v0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_1, i32 %qc0_0, i32 %tc_r0)
  %tc_v1 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_1, i32 %qc1_0, i32 %tc_v0)
  %tc_h1l = call i32 @llvm.amdgcn.alignbyte(i32 %qc1_1, i32 %qc0_1, i32 1)
  %tc_h10 = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h1l, i32 %qc0_1, i32 %tc_v1)
  %tc_h1a = lshr i32 %qc1_1, 8
  %tc_h1b = and i32 %qc1_1, 16777215
  %tc_r1 = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h1a, i32 %tc_h1b, i32 %tc_h10)
  %ap2 = add i32 %ap1, %wl
  %ac2 = add i32 %ac1, %wl
  %dp2 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap2, i32 0, i32 0)
  %dc2 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac2, i32 0, i32 0)
  %p0_2 = extractelement <3 x i32> %dp2, i32 0
  %p1_2 = extractelement <3 x i32> %dp2, i32 1
  %p2_2 = extractelement <3 x i32> %dp2, i32 2
  %c0_2 = extractelement <3 x i32> %dc2, i32 0
  %c1_2 = extractelement <3 x i32> %dc2, i32 1
  %c2_2 = extractelement <3 x i32> %dc2, i32 2
  %qp0_2 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_2, i32 %p0_2, i32 %shp)
  %qp1_2 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_2, i32 %p1_2, i32 %shp)
  %qc0_2 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_2, i32 %c0_2, i32 %shc)
  %qc1_2 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_2, i32 %c1_2, i32 %shc)
  %ql2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_2, i32 %qc0_2, i32 %ql1)
  %qr2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_2, i32 %qc1_2, i32 %qr1)
  %ma2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_2, i32 0, i32 %mb1)
  %mb2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_2, i32 0, i32 %ma2)
  %mc2 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_2, i32 0, i32 %md1)
  %md2 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_2, i32 0, i32 %mc2)
  %tp_v2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_2, i32 %qp0_1, i32 %tp_r1)
  %tp_v3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_2, i32 %qp1_1, i32 %tp_v2)
  %tp_h2l = call i32 @llvm.amdgcn.alignbyte(i32 %qp1_2, i32 %qp0_2, i32 1)
  %tp_h20 = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h2l, i32 %qp0_2, i32 %tp_v3)
  %tp_h2a = lshr i32 %qp1_2, 8
  %tp_h2b = and i32 %qp1_2, 16777215
  %tp_r2 = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h2a, i32 %tp_h2b, i32 %tp_h20)
  %tc_v2 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_2, i32 %qc0_1, i32 %tc_r1)
  %tc_v3 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_2, i32 %qc1_1, i32 %tc_v2)
  %tc_h2l = call i32 @llvm.amdgcn.alignbyte(i32 %qc1_2, i32 %qc0_2, i32 1)
  %tc_h20 = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h2l, i32 %qc0_2, i32 %tc_v3)
  %tc_h2a = lshr i32 %qc1_2, 8
  %tc_h2b = and i32 %qc1_2, 16777215
  %tc_r2 = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h2a, i32 %tc_h2b, i32 %tc_h20)
  %ap3 = add i32 %ap2, %wl
  %ac3 = add i32 %ac2, %wl
  %dp3 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap3, i32 0, i32 0)
  %dc3 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac3, i32 0, i32 0)
  %p0_3 = extractelement <3 x i32> %dp3, i32 0
  %p1_3 = extractelement <3 x i32> %dp3, i32 1
  %p2_3 = extractelement <3 x i32> %dp3, i32 2
  %c0_3 = extractelement <3 x i32> %dc3, i32 0
  %c1_3 = extractelement <3 x i32> %dc3, i32 1
  %c2_3 = extractelement <3 x i32> %dc3, i32 2
  %qp0_3 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_3, i32 %p0_3, i32 %shp)
  %qp1_3 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_3, i32 %p1_3, i32 %shp)
  %qc0_3 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_3, i32 %c0_3, i32 %shc)
  %qc1_3 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_3, i32 %c1_3, i32 %shc)
  %ql3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_3, i32 %qc0_3, i32 %ql2)
  %qr3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_3, i32 %qc1_3, i32 %qr2)
  %ma3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_3, i32 0, i32 %mb2)
  %mb3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_3, i32 0, i32 %ma3)
  %mc3 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_3, i32 0, i32 %md2)
  %md3 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_3, i32 0, i32 %mc3)
  %tp_v4 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_3, i32 %qp0_2, i32 %tp_r2)
  %tp_v5 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_3, i32 %qp1_2, i32 %tp_v4)
  %tp_h3l = call i32 @llvm.amdgcn.alignbyte(i32 %qp1_3, i32 %qp0_3, i32 1)
  %tp_h30 = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h3l, i32 %qp0_3, i32 %tp_v5)
  %tp_h3a = lshr i32 %qp1_3, 8
  %tp_h3b = and i32 %qp1_3, 16777215
  %tp_out = call i32 @llvm.amdgcn.sad.u8(i32 %tp_h3a, i32 %tp_h3b, i32 %tp_h30)
  %tc_v4 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_3, i32 %qc0_2, i32 %tc_r2)
  %tc_v5 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_3, i32 %qc1_2, i32 %tc_v4)
  %tc_h3l = call i32 @llvm.amdgcn.alignbyte(i32 %qc1_3, i32 %qc0_3, i32 1)
  %tc_h30 = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h3l, i32 %qc0_3, i32 %tc_v5)
  %tc_h3a = lshr i32 %qc1_3, 8
  %tc_h3b = and i32 %qc1_3, 16777215
  %tc_out = call i32 @llvm.amdgcn.sad.u8(i32 %tc_h3a, i32 %tc_h3b, i32 %tc_h30)

  %rmore = icmp eq i32 %rbase, 0
  br i1 %rmore, label %rowloop, label %rowdone
rowdone:
  %q00 = add i32 %q00_in, 0
  %q10 = add i32 %q10_in, 0
  %q01 = add i32 %ql3, 0
  %q11 = add i32 %qr3, 0
  %qtop = add i32 %q00, %q10
  %qbot = add i32 %q01, %q11
  %sb7 = add i32 %qtop, %qbot
  %mb7 = add i32 %mb3, 0
  %md7 = add i32 %md3, 0
  %dc_diff = sub i32 %mb7, %md7
  %dc_abs = call i32 @llvm.abs.i32(i32 %dc_diff, i1 false)
  %dc_ac = sub i32 %sb7, %dc_abs
  %dc_ac2 = shl i32 %dc_ac, 1
  %dc_e0 = sub i32 %dc_abs, 256
  %dc_eff = call i32 @llvm.smax.i32(i32 %dc_e0, i32 0)
  %dc_t3 = mul i32 %dc_eff, 3
  %dc_t3q = ashr i32 %dc_t3, 2
  %dc_d0 = sub i32 %dc_t3q, %dc_ac2
  %dc_d1 = call i32 @llvm.smax.i32(i32 %dc_d0, i32 0)
  %dc_le = icmp sle i32 %dc_abs, 2048
  %dc_disc = select i1 %dc_le, i32 %dc_d1, i32 0
  %dc_ex0 = sub i32 %dc_abs, 2048
  %dc_ex1 = call i32 @llvm.smax.i32(i32 %dc_ex0, i32 0)
  %pen = ashr i32 %dc_ex1, 2
  %sad0a = sub i32 %sb7, %dc_disc
  %sad0 = add i32 %sad0a, %pen
  %qm0 = call i32 @llvm.smin.i32(i32 %q00, i32 %q10)
  %qm1 = call i32 @llvm.smin.i32(i32 %q01, i32 %q11)
  %qmin = call i32 @llvm.smin.i32(i32 %qm0, i32 %qm1)
  %qm4 = shl i32 %qmin, 2
  %qbase = add i32 %qm4, 256
  %qex0 = sub i32 %sad0, %qbase
  %qexc = call i32 @llvm.smax.i32(i32 %qex0, i32 0)
  %qexq = ashr i32 %qexc, 2
  %qtrim0 = sub i32 %qexc, %qexq
  %msum0 = add i32 %mb7, %md7
  %avg_sum = ashr i32 %msum0, 1
  %t_sum = add i32 %tp_out, %tc_out
  %t_avg = ashr i32 %t_sum, 1
  %t_min = call i32 @llvm.smin.i32(i32 %tp_out, i32 %tc_out)
  %q_clean = icmp sle i32 %qmin, 88
  %z_gt140 = icmp sgt i32 %ztex, 140
  %tm_lt96 = icmp slt i32 %t_min, 96
  %no_qt = and i1 %z_gt140, %tm_lt96
  %qt_ok = xor i1 %no_qt, true
  %q_allow = and i1 %q_clean, %qt_ok
  %qtrim = select i1 %q_allow, i32 %qtrim0, i32 0
  %sad_t0 = sub i32 %sad0, %qtrim
  %sad_t = call i32 @llvm.smin.i32(i32 %sad_t0, i32 65535)
  %num = call i32 @llvm.smax.i32(i32 %ztex, i32 96)
  %den = call i32 @llvm.smax.i32(i32 %t_avg, i32 96)
  %num16 = shl i32 %num, 4
  %q = udiv i32 %num16, %den
  %infl0 = call i32 @llvm.smax.i32(i32 %q, i32 16)
  %infl = call i32 @llvm.smin.i32(i32 %infl0, i32 64)
  %sad_m = mul i32 %sad_t, %infl
  %sad_i = ashr i32 %sad_m, 4
  %tl0 = sub i32 %ztex, %t_avg
  %tl1 = sub i32 %tl0, 48
  %tloss = call i32 @llvm.smax.i32(i32 %tl1, i32 0)
  %ld0 = sub i32 %avg_sum, %zsum
  %ld1 = call i32 @llvm.abs.i32(i32 %ld0, i1 false)
  %ld2 = sub i32 %ld1, 384
  %ld3 = call i32 @llvm.smax.i32(i32 %ld2, i32 0)
  %ljump = ashr i32 %ld3, 2
  %sk0 = sub i32 128, %t_min
  %sk1 = call i32 @llvm.smax.i32(i32 %sk0, i32 0)
  %sk = call i32 @llvm.smin.i32(i32 %sk1, i32 128)
  %ep0 = add i32 %tloss, %ljump
  %ep1 = mul i32 %ep0, %sk
  %esc = ashr i32 %ep1, 7
  %sad_n = add i32 %sad_i, %esc
  %sad = call i32 @llvm.smin.i32(i32 %sad_n, i32 65535)
  %r0 = insertvalue { i32, i32, i32, i32, i32 } undef, i32 %sad, 0
  %r1 = insertvalue { i32, i32, i32, i32, i32 } %r0, i32 %q00, 1
  %r2 = insertvalue { i32, i32, i32, i32, i32 } %r1, i32 %q10, 2
  %r3 = insertvalue { i32, i32, i32, i32, i32 } %r2, i32 %q01, 3
  %r4 = insertvalue { i32, i32, i32, i32, i32 } %r3, i32 %q11, 4
  ret { i32, i32, i32, i32, i32 } %r4
}

; vectors are packed as (dx & 0xffff) | (dy << 16), level pixels
define internal i32 @pack(i32 %dx, i32 %dy) alwaysinline {
  %a = and i32 %dx, 65535
  %b = shl i32 %dy, 16
  %c = or i32 %a, %b
  ret i32 %c
}
define internal i32 @unx(i32 %p) alwaysinline {
  %a = shl i32 %p, 16
  %b = ashr i32 %a, 16
  ret i32 %b
}
define internal i32 @uny(i32 %p) alwaysinline {
  %b = ashr i32 %p, 16
  ret i32 %b
}
define internal i32 @rnd(float %f) alwaysinline {
  %a = fadd float %f, 0.5
  %b = call float @llvm.floor.f32(float %a)
  %c = call float @llvm.maxnum.f32(float %b, float -4096.0)
  %d = call float @llvm.minnum.f32(float %c, float 4096.0)
  %i = fptosi float %d to i32
  ret i32 %i
}

; min of the keys of lanes [base, base+n) (n = 16 or 64), every lane gets the result.
define internal i32 @reduce(i32 %tid, i32 %key, i32 %base, i32 %n) alwaysinline {
entry:
  %p = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %tid
  store i32 %key, ptr addrspace(3) %p, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i1, %loop ]
  %m = phi i32 [ -1, %entry ], [ %m1, %loop ]
  %ix = add i32 %base, %i
  %q = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %ix
  %k = load i32, ptr addrspace(3) %q, align 4
  %m1 = call i32 @llvm.umin.i32(i32 %m, i32 %k)
  %i1 = add i32 %i, 1
  %more = icmp ult i32 %i1, %n
  br i1 %more, label %loop, label %done
done:
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ret i32 %m1
}

; packed vector published by lane `src` (all lanes read it)
define internal i32 @bcast(i32 %tid, i32 %val, i32 %src) alwaysinline {
  %p = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %tid
  store i32 %val, ptr addrspace(3) %p, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  %q = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %src
  %v = load i32, ptr addrspace(3) %q, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ret i32 %v
}

; full-res float vector of block (bx + ox, by + oy) of FT, coordinates reflected at screen borders (r6)
define internal <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %ox, i32 %oy) alwaysinline {
  %x0 = add i32 %bx, %ox
  %xlt0 = icmp slt i32 %x0, 0
  %xgt = icmp sgt i32 %x0, 119
  %x1 = select i1 %xlt0, i32 1, i32 %x0
  %x = select i1 %xgt, i32 118, i32 %x1
  %y0 = add i32 %by, %oy
  %ylt0 = icmp slt i32 %y0, 0
  %ygt = icmp sgt i32 %y0, 67
  %y1 = select i1 %ylt0, i32 1, i32 %y0
  %y = select i1 %ygt, i32 66, i32 %y1
  %r = mul i32 %y, 120
  %i = add i32 %r, %x
  %o = shl i32 %i, 3
  %v = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vt, i32 %o, i32 0, i32 0)
  ret <2 x float> %v
}

; smoothness term of candidate (cx, cy) (L1 px) against neighbour (ox, oy): min(|c - rnd(v_n / 2)|_1, cap)
define internal i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 %ox, i32 %oy, i32 %cx, i32 %cy, i32 %cap) alwaysinline {
  %v = call <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %ox, i32 %oy)
  %vx = extractelement <2 x float> %v, i32 0
  %vy = extractelement <2 x float> %v, i32 1
  %hx = fmul float %vx, 0.5
  %hy = fmul float %vy, 0.5
  %nx = call i32 @rnd(float %hx)
  %ny = call i32 @rnd(float %hy)
  %dx = sub i32 %cx, %nx
  %dy = sub i32 %cy, %ny
  %ax = call i32 @llvm.abs.i32(i32 %dx, i1 false)
  %ay = call i32 @llvm.abs.i32(i32 %dy, i1 false)
  %a = add i32 %ax, %ay
  %m = call i32 @llvm.umin.i32(i32 %a, i32 %cap)
  ret i32 %m
}

; evaluate whether quadrant (q, wq) is won by secondary candidate (sc, lock, qmax)
define internal i1 @qwin(i32 %q, i32 %wq, i32 %sc, i1 %lock, i32 %qmax) alwaysinline {
  %qp24 = add i32 %q, 24
  %lt_w = icmp slt i32 %qp24, %wq
  %le160 = icmp sle i32 %q, 160
  %sc_gt3 = icmp sgt i32 %sc, 3
  %wq_ge160 = icmp sge i32 %wq, 160
  %dir_g = or i1 %sc_gt3, %wq_ge160
  %dir0 = and i1 %lt_w, %le160
  %q_dir = and i1 %dir0, %dir_g
  %le192 = icmp sle i32 %q, 192
  %q4 = shl i32 %q, 2
  %qm3 = mul i32 %qmax, 3
  %le_qm = icmp sle i32 %q4, %qm3
  %lk0 = and i1 %lock, %le192
  %q_lk = and i1 %lk0, %le_qm
  %res = or i1 %q_dir, %q_lk
  ret i1 %res
}

define internal { i32, i32, i32, i32 } @mid_zinfo(i32 %tid, i32 %sub, i32 %base, <4 x i32> %vp, <4 x i32> %vc, i32 %X, i32 %Y) alwaysinline {
entry:
  %r = and i32 %sub, 7
  %is_c = icmp uge i32 %sub, 8
  %v_sel = select i1 %is_c, <4 x i32> %vc, <4 x i32> %vp
  %Yc = call i32 @llvm.smin.i32(i32 %Y, i32 532)
  %yr = add i32 %Yc, %r
  %ro = mul i32 %yr, 960
  %off = add i32 %ro, %X
  %d2 = call <2 x i32> @llvm.amdgcn.raw.buffer.load.v2i32(<4 x i32> %v_sel, i32 %off, i32 0, i32 0)
  %q0 = extractelement <2 x i32> %d2, i32 0
  %q1 = extractelement <2 x i32> %d2, i32 1
  %ms0 = call i32 @llvm.amdgcn.sad.u8(i32 %q0, i32 0, i32 0)
  %ms = call i32 @llvm.amdgcn.sad.u8(i32 %q1, i32 0, i32 %ms0)
  %hl = call i32 @llvm.amdgcn.alignbyte(i32 %q1, i32 %q0, i32 1)
  %dh0 = call i32 @llvm.amdgcn.sad.u8(i32 %hl, i32 %q0, i32 0)
  %ha = lshr i32 %q1, 8
  %hb = and i32 %q1, 16777215
  %dh = call i32 @llvm.amdgcn.sad.u8(i32 %ha, i32 %hb, i32 %dh0)
  %pk = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %tid
  %pv = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %tid
  store i32 %q0, ptr addrspace(3) %pk, align 4
  store i32 %q1, ptr addrspace(3) %pv, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  %r_lt7 = icmp ult i32 %r, 7
  %tn1 = add i32 %tid, 1
  %tsel = select i1 %r_lt7, i32 %tn1, i32 %tid
  %pkn = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %tsel
  %pvn = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %tsel
  %qn0 = load i32, ptr addrspace(3) %pkn, align 4
  %qn1 = load i32, ptr addrspace(3) %pvn, align 4
  %dv0 = call i32 @llvm.amdgcn.sad.u8(i32 %qn0, i32 %q0, i32 0)
  %dv = call i32 @llvm.amdgcn.sad.u8(i32 %qn1, i32 %q1, i32 %dv0)
  %rt = add i32 %dh, %dv
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  store i32 %ms, ptr addrspace(3) %pk, align 4
  store i32 %rt, ptr addrspace(3) %pv, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  br label %zloop
zloop:
  %zi = phi i32 [ 0, %entry ], [ %zi1, %zloop ]
  %zs_acc = phi i32 [ 0, %entry ], [ %zs_next, %zloop ]
  %ztp_acc = phi i32 [ 0, %entry ], [ %ztp_next, %zloop ]
  %ztc_acc = phi i32 [ 0, %entry ], [ %ztc_next, %zloop ]
  %zix_p = add i32 %base, %zi
  %zix_c = add i32 %zix_p, 8
  %zpk_p = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %zix_p
  %zpk_c = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %zix_c
  %zpv_p = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %zix_p
  %zpv_c = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %zix_c
  %zv_sp = load i32, ptr addrspace(3) %zpk_p, align 4
  %zv_sc = load i32, ptr addrspace(3) %zpk_c, align 4
  %zv_tp = load i32, ptr addrspace(3) %zpv_p, align 4
  %zv_tc = load i32, ptr addrspace(3) %zpv_c, align 4
  %zv_s = add i32 %zv_sp, %zv_sc
  %zs_next = add i32 %zs_acc, %zv_s
  %ztp_next = add i32 %ztp_acc, %zv_tp
  %ztc_next = add i32 %ztc_acc, %zv_tc
  %zi1 = add i32 %zi, 1
  %zmore = icmp ult i32 %zi1, 8
  br i1 %zmore, label %zloop, label %zdone
zdone:
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  %zsum = lshr i32 %zs_next, 1
  %zt_next = add i32 %ztp_next, %ztc_next
  %ztex = lshr i32 %zt_next, 1
  %zmin = call i32 @llvm.smin.i32(i32 %ztp_next, i32 %ztc_next)
  %zsub = sub i32 %ztp_next, %ztc_next
  %zdiff = call i32 @llvm.abs.i32(i32 %zsub, i1 false)
  %zr0 = insertvalue { i32, i32, i32, i32 } undef, i32 %ztex, 0
  %zr1 = insertvalue { i32, i32, i32, i32 } %zr0, i32 %zsum, 1
  %zr2 = insertvalue { i32, i32, i32, i32 } %zr1, i32 %zmin, 2
  %zr3 = insertvalue { i32, i32, i32, i32 } %zr2, i32 %zdiff, 3
  ret { i32, i32, i32, i32 } %zr3
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 128
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 144
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %pf = getelementptr i8, ptr addrspace(4) %tbl, i64 256
  %vf = load <4 x i32>, ptr addrspace(4) %pf, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %pt = getelementptr i8, ptr addrspace(4) %tbl, i64 384
  %vt = load <4 x i32>, ptr addrspace(4) %pt, align 16, !invariant.load !0
  %j = lshr i32 %tid, 4
  %sub = and i32 %tid, 15
  %base = and i32 %tid, 48
  %gx4 = shl i32 %gx, 2
  %bx = add i32 %gx4, %j
  %by = add i32 %gy, 0
  %X = shl i32 %bx, 3
  %Y = shl i32 %by, 3
  %grow = mul i32 %by, 120
  %gidx = add i32 %grow, %bx
  %goff = shl i32 %gidx, 2
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgk, i32 %goff, i32 0, i32 0)
  %hasfg = icmp ne i32 %gk, 0
  %isfgk = icmp uge i32 %gk, 1048576
  %nfgk = xor i1 %isfgk, true
  %isanc = and i1 %hasfg, %nfgk
  %notanc = xor i1 %isanc, true
  ; ---- candidate of this lane:
  ; slot 0 own, 1..8 neighbour k (raster 3x3 without centre), 9 camera (when !isanc),
  ; slots 10..15 outer 2-hop contour ring: (0,-2), (0,2), (-2,0), (2,0), (-1,-2), (1,2)
  %k0 = sub i32 %sub, 1
  %ge5 = icmp uge i32 %sub, 5
  %k1 = select i1 %ge5, i32 %sub, i32 %k0
  %isn = icmp ult i32 %k0, 8
  %k = select i1 %isn, i32 %k1, i32 4
  %kxu = urem i32 %k, 3
  %kyu = udiv i32 %k, 3
  %kx0 = sub i32 %kxu, 1
  %ky0 = sub i32 %kyu, 1
  %is10 = icmp eq i32 %sub, 10
  %is11 = icmp eq i32 %sub, 11
  %is12 = icmp eq i32 %sub, 12
  %is13 = icmp eq i32 %sub, 13
  %is14 = icmp eq i32 %sub, 14
  %is15 = icmp eq i32 %sub, 15
  %ox_a = select i1 %is12, i32 -2, i32 %kx0
  %ox_b = select i1 %is13, i32 2, i32 %ox_a
  %ox_c = select i1 %is14, i32 -1, i32 %ox_b
  %kx = select i1 %is15, i32 1, i32 %ox_c
  %oy_m2 = or i1 %is10, %is14
  %oy_p2 = or i1 %is11, %is15
  %oy_a = select i1 %oy_m2, i32 -2, i32 %ky0
  %ky = select i1 %oy_p2, i32 2, i32 %oy_a
  %cv = call <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %kx, i32 %ky)
  %gmx = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 16, i32 0, i32 0)
  %gmy = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 20, i32 0, i32 0)
  %gmx2 = shl i32 %gmx, 1
  %gmy2 = shl i32 %gmy, 1
  %gmor = or i32 %gmx, %gmy
  %still = icmp eq i32 %gmor, 0
  %nstl = xor i1 %still, true
  %gmx4 = shl i32 %gmx, 2
  %gmy4 = shl i32 %gmy, 2
  %gfx = sitofp i32 %gmx4 to float
  %gfy = sitofp i32 %gmy4 to float
  %is9a = icmp eq i32 %sub, 9
  %is9 = and i1 %is9a, %notanc
  %cvx0 = extractelement <2 x float> %cv, i32 0
  %cvy0 = extractelement <2 x float> %cv, i32 1
  %cvx = select i1 %is9, float %gfx, float %cvx0
  %cvy = select i1 %is9, float %gfy, float %cvy0
  %hx = fmul float %cvx, 0.5
  %hy = fmul float %cvy, 0.5
  %cx = call i32 @rnd(float %hx)
  %cy = call i32 @rnd(float %hy)
  ; 8.5 anchor camera-escape check (r7)
  %p2 = getelementptr i8, ptr addrspace(4) %tbl, i64 240
  %v2 = load <4 x i32>, ptr addrspace(4) %p2, align 16, !invariant.load !0
  %asrc = and i32 %gk, 4095
  %aoff = shl i32 %asrc, 2
  %av = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %aoff, i32 0, i32 0)
  %avs = shl i32 %av, 16
  %avx = ashr i32 %avs, 16
  %avy = ashr i32 %av, 16
  %ax = shl i32 %avx, 1
  %ay = shl i32 %avy, 1
  %adx = sub i32 %cx, %ax
  %ady = sub i32 %cy, %ay
  %aadx = call i32 @llvm.abs.i32(i32 %adx, i1 false)
  %aady = call i32 @llvm.abs.i32(i32 %ady, i1 false)
  %aad = add i32 %aadx, %aady
  %scdx0 = sub i32 %cx, %gmx2
  %scdy0 = sub i32 %cy, %gmy2
  %scdx = call i32 @llvm.abs.i32(i32 %scdx0, i1 false)
  %scdy = call i32 @llvm.abs.i32(i32 %scdy0, i1 false)
  %scd = add i32 %scdx, %scdy
  %aadgt3 = icmp sgt i32 %aad, 3
  %aadgt16 = icmp sgt i32 %aad, 16
  %scd2 = shl i32 %scd, 1
  %scdle = icmp sle i32 %scd2, %aad
  %cam_esc = and i1 %nstl, %scdle
  %esc = or i1 %aadgt16, %cam_esc
  %afar = and i1 %aadgt3, %esc
  %ap = and i1 %isanc, %afar
  %hcx = ashr i32 %cx, 1
  %hcy = ashr i32 %cy, 1
  %cap = select i1 %nfgk, i32 32, i32 8
  %t0 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 -1, i32 -1, i32 %cx, i32 %cy, i32 %cap)
  %t1 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 0, i32 -1, i32 %cx, i32 %cy, i32 %cap)
  %t2 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 1, i32 -1, i32 %cx, i32 %cy, i32 %cap)
  %t3 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 -1, i32 0, i32 %cx, i32 %cy, i32 %cap)
  %t4 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 0, i32 0, i32 %cx, i32 %cy, i32 %cap)
  %t5 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 1, i32 0, i32 %cx, i32 %cy, i32 %cap)
  %t6 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 -1, i32 1, i32 %cx, i32 %cy, i32 %cap)
  %t7 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 0, i32 1, i32 %cx, i32 %cy, i32 %cap)
  %t8 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 1, i32 1, i32 %cx, i32 %cy, i32 %cap)
  %t4x3 = mul i32 %t4, 3
  %t4w = select i1 %nfgk, i32 %t4x3, i32 0
  %s01 = add i32 %t0, %t1
  %s23 = add i32 %t2, %t3
  %s56 = add i32 %t5, %t6
  %s78 = add i32 %t7, %t8
  %s03 = add i32 %s01, %s23
  %s58 = add i32 %s56, %s78
  %sm0 = add i32 %s03, %s58
  %sm = add i32 %sm0, %t4w
  %lsh = select i1 %nfgk, i32 2, i32 1
  %pen = shl i32 %sm, %lsh
  ; outward disocclusion/occlusion shift for camera-aligned candidates at motion boundaries
  %isgm = icmp sle i32 %scd, 3
  %hi_sm = icmp uge i32 %sm0, 16
  %zpair = call { i32, i32, i32, i32 } @mid_zinfo(i32 %tid, i32 %sub, i32 %base, <4 x i32> %vp, <4 x i32> %vc, i32 %X, i32 %Y)
  %ztex = extractvalue { i32, i32, i32, i32 } %zpair, 0
  %zsum = extractvalue { i32, i32, i32, i32 } %zpair, 1
  %zmin = extractvalue { i32, i32, i32, i32 } %zpair, 2
  %zdiff = extractvalue { i32, i32, i32, i32 } %zpair, 3
  %bg_c0 = and i1 %isgm, %notanc
  %bg_c1 = and i1 %bg_c0, %hi_sm
  %z_le160 = icmp sle i32 %ztex, 160
  %t4_le3 = icmp sle i32 %t4, 3
  %zm_gt160 = icmp sgt i32 %zmin, 160
  %zd2 = shl i32 %zdiff, 1
  %zd_ok = icmp sge i32 %zd2, %ztex
  %ga0 = and i1 %t4_le3, %zm_gt160
  %ga1 = and i1 %ga0, %zd_ok
  %g_ok = or i1 %z_le160, %ga1
  %bg_c = and i1 %bg_c1, %g_ok
  %dx_t = sub i32 %t3, %t5
  %dx_t5 = mul i32 %dx_t, 5
  %dx_ts = ashr i32 %dx_t5, 3
  %shx_d0 = call i32 @llvm.smax.i32(i32 %dx_ts, i32 -5)
  %shx_d = call i32 @llvm.smin.i32(i32 %shx_d0, i32 5)
  %dy_t = sub i32 %t1, %t7
  %dy_t5 = mul i32 %dy_t, 5
  %dy_ts = ashr i32 %dy_t5, 3
  %shy_d0 = call i32 @llvm.smax.i32(i32 %dy_ts, i32 -5)
  %shy_d = call i32 @llvm.smin.i32(i32 %shy_d0, i32 5)
  %scx0 = select i1 %bg_c, i32 %shx_d, i32 0
  %scy0 = select i1 %bg_c, i32 %shy_d, i32 0
  %scx = select i1 %ap, i32 %hcx, i32 %scx0
  %scy = select i1 %ap, i32 %hcy, i32 %scy0
  %Xu = add i32 %X, %scx
  %Yu = add i32 %Y, %scy
  %bsr = call { i32, i32, i32, i32, i32 } @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %Xu, i32 %Yu, i32 %cx, i32 %cy, i32 %ztex, i32 %zsum)
  %sad = extractvalue { i32, i32, i32, i32, i32 } %bsr, 0
  %q00 = extractvalue { i32, i32, i32, i32, i32 } %bsr, 1
  %q10 = extractvalue { i32, i32, i32, i32, i32 } %bsr, 2
  %q01 = extractvalue { i32, i32, i32, i32, i32 } %bsr, 3
  %q11 = extractvalue { i32, i32, i32, i32, i32 } %bsr, 4
  ; foreground bonus for the own vector (suppressed when own vector is camera-aligned at a boundary)
  %is0 = icmp eq i32 %sub, 0
  %not_bg_b = and i1 %isgm, %hi_sm
  %keep_b = xor i1 %not_bg_b, true
  %fgo0 = and i1 %is0, %hasfg
  %fgo = and i1 %fgo0, %keep_b
  %bias = select i1 %fgo, i32 96, i32 0
  %apen = select i1 %ap, i32 512, i32 0
  ; OOB candidate penalty for non-supported out-of-screen taps (r6)
  %xc_0 = sub i32 %Xu, %hcx
  %xp_0 = add i32 %xc_0, %cx
  %yc_0 = sub i32 %Yu, %hcy
  %yp_0 = add i32 %yc_0, %cy
  %ox_0 = icmp ugt i32 %xc_0, 952
  %ox_1 = icmp ugt i32 %xp_0, 952
  %oy_0 = icmp ugt i32 %yc_0, 532
  %oy_1 = icmp ugt i32 %yp_0, 532
  %oobx = or i1 %ox_0, %ox_1
  %ooby = or i1 %oy_0, %oy_1
  %oob = or i1 %oobx, %ooby
  %notgm = icmp ugt i32 %scd, 3
  %notsm = icmp ugt i32 %sm0, 24
  %oob_u0 = and i1 %oob, %notgm
  %oob_u1 = and i1 %oob_u0, %notsm
  %oob_u = and i1 %oob_u1, %nfgk
  %oob_p = select i1 %oob_u, i32 96, i32 0
  %c0 = add i32 %sad, 256
  %c1 = add i32 %c0, %pen
  %c1b = add i32 %c1, %apen
  %c1c = add i32 %c1b, %oob_p
  %cost = sub i32 %c1c, %bias
  %kh = shl i32 %cost, 8
  %key0 = or i32 %kh, %sub
  ; fade frame (me1 zeroed the field on purpose): own vector only
  %fade = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 28, i32 0, i32 0)
  %nofade = icmp eq i32 %fade, 0
  %lt10 = icmp ult i32 %sub, 10
  ; 8.5 diagnostic (consts flags bit 7, inject letter 'M'): filter off, own vector only (F = FT)
  %pcnm = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnm = load <4 x i32>, ptr addrspace(4) %pcnm, align 16, !invariant.load !0
  %flm = extractelement <4 x i32> %cnm, i32 2
  %flm128 = and i32 %flm, 128
  %mon = icmp eq i32 %flm128, 0
  %nofm = and i1 %nofade, %mon
  %v1 = and i1 %lt10, %nofm
  %val = or i1 %v1, %is0
  %key = select i1 %val, i32 %key0, i32 -1
  %m = call i32 @reduce(i32 %tid, i32 %key, i32 %base, i32 16)
  %w = and i32 %m, 255
  %src = add i32 %base, %w
  %bxi = bitcast float %cvx to i32
  %byi = bitcast float %cvy to i32
  %ox = call i32 @bcast(i32 %tid, i32 %bxi, i32 %src)
  %oy = call i32 @bcast(i32 %tid, i32 %byi, i32 %src)
  ; ---- Pass 2: Dual-layer 8x8 quadrant contour refinement across all 16 lanes (r8)
  %pcxy = call i32 @pack(i32 %cx, i32 %cy)
  %wcxy = call i32 @bcast(i32 %tid, i32 %pcxy, i32 %src)
  %wincx = call i32 @unx(i32 %wcxy)
  %wincy = call i32 @uny(i32 %wcxy)
  %pqtop = call i32 @pack(i32 %q00, i32 %q10)
  %pqbot = call i32 @pack(i32 %q01, i32 %q11)
  %wqtop = call i32 @bcast(i32 %tid, i32 %pqtop, i32 %src)
  %wqbot = call i32 @bcast(i32 %tid, i32 %pqbot, i32 %src)
  %wq00 = call i32 @unx(i32 %wqtop)
  %wq10 = call i32 @uny(i32 %wqtop)
  %wq01 = call i32 @unx(i32 %wqbot)
  %wq11 = call i32 @uny(i32 %wqbot)
  %sc1x0 = sub i32 %wincx, %gmx2
  %sc1y0 = sub i32 %wincy, %gmy2
  %sc1x = call i32 @llvm.abs.i32(i32 %sc1x0, i1 false)
  %sc1y = call i32 @llvm.abs.i32(i32 %sc1y0, i1 false)
  %sc1 = add i32 %sc1x, %sc1y
  %wqm0 = call i32 @llvm.smax.i32(i32 %wq00, i32 %wq10)
  %wqm1 = call i32 @llvm.smax.i32(i32 %wq01, i32 %wq11)
  %winq_max = call i32 @llvm.smax.i32(i32 %wqm0, i32 %wqm1)
  %d12x0 = sub i32 %cx, %wincx
  %d12y0 = sub i32 %cy, %wincy
  %d12x = call i32 @llvm.abs.i32(i32 %d12x0, i1 false)
  %d12y = call i32 @llvm.abs.i32(i32 %d12y0, i1 false)
  %d12 = add i32 %d12x, %d12y
  %qmn0 = call i32 @llvm.smin.i32(i32 %q00, i32 %q10)
  %qmn1 = call i32 @llvm.smin.i32(i32 %q01, i32 %q11)
  %qmin = call i32 @llvm.smin.i32(i32 %qmn0, i32 %qmn1)
  %qmx0 = call i32 @llvm.smax.i32(i32 %q00, i32 %q10)
  %qmx1 = call i32 @llvm.smax.i32(i32 %q01, i32 %q11)
  %qmax = call i32 @llvm.smax.i32(i32 %qmx0, i32 %qmx1)
  %qm_le32 = icmp sle i32 %qmin, 32
  %wm_ge32 = icmp sge i32 %winq_max, 32
  %qm_le96 = icmp sle i32 %qmin, 96
  %qm_mid = and i1 %wm_ge32, %qm_le96
  %wm_ge384 = icmp sge i32 %winq_max, 384
  %qm_le148 = icmp sle i32 %qmin, 148
  %qm_hi = and i1 %wm_ge384, %qm_le148
  %qmin_ok0 = or i1 %qm_le32, %qm_mid
  %qmin_ok = or i1 %qmin_ok0, %qm_hi
  %sc1_le3 = icmp sle i32 %sc1, 3
  %sc_gt4 = icmp sgt i32 %scd, 4
  %qmx_ge160 = icmp sge i32 %qmax, 160
  %qspan = sub i32 %qmax, %qmin
  %qsp_ge112 = icmp sge i32 %qspan, 112
  %lk0 = and i1 %sc1_le3, %sc_gt4
  %lk1 = and i1 %lk0, %qmin_ok
  %lk2 = and i1 %lk1, %qmx_ge160
  %lock = and i1 %lk2, %qsp_ge112
  %qw0 = call i1 @qwin(i32 %q00, i32 %wq00, i32 %scd, i1 %lock, i32 %qmax)
  %qw1 = call i1 @qwin(i32 %q10, i32 %wq10, i32 %scd, i1 %lock, i32 %qmax)
  %qw2 = call i1 @qwin(i32 %q01, i32 %wq01, i32 %scd, i1 %lock, i32 %qmax)
  %qw3 = call i1 @qwin(i32 %q11, i32 %wq11, i32 %scd, i1 %lock, i32 %qmax)
  %mb0 = zext i1 %qw0 to i32
  %mb1_0 = zext i1 %qw1 to i32
  %mb1 = shl i32 %mb1_0, 1
  %mb2_0 = zext i1 %qw2 to i32
  %mb2 = shl i32 %mb2_0, 2
  %mb3_0 = zext i1 %qw3 to i32
  %mb3 = shl i32 %mb3_0, 3
  %m01 = or i32 %mb0, %mb1
  %m23 = or i32 %mb2, %mb3
  %qmask = or i32 %m01, %m23
  %val2 = or i1 %nofm, %is0
  %nap = xor i1 %ap, true
  %d12_ge3 = icmp sge i32 %d12, 3
  %m_gt0 = icmp sgt i32 %qmask, 0
  %m_lt15 = icmp slt i32 %qmask, 15
  %ok2a = and i1 %val2, %nap
  %ok2b = and i1 %ok2a, %d12_ge3
  %ok2c = and i1 %ok2b, %m_gt0
  %ok2d = and i1 %ok2c, %m_lt15
  %ok2 = and i1 %ok2d, %qmin_ok
  %c2 = add i32 %qmin, 256
  %kh2 = shl i32 %c2, 8
  %key2_0 = or i32 %kh2, %sub
  %key2 = select i1 %ok2, i32 %key2_0, i32 -1
  %m2 = call i32 @reduce(i32 %tid, i32 %key2, i32 %base, i32 16)
  %has_split = icmp ne i32 %m2, -1
  %w2 = and i32 %m2, 15
  %src2 = add i32 %base, %w2
  %cx_c0 = call i32 @llvm.smax.i32(i32 %cx, i32 -128)
  %cx_c1 = call i32 @llvm.smin.i32(i32 %cx_c0, i32 127)
  %cy_c0 = call i32 @llvm.smax.i32(i32 %cy, i32 -128)
  %cy_c1 = call i32 @llvm.smin.i32(i32 %cy_c0, i32 127)
  %cx_u8 = and i32 %cx_c1, 255
  %cy_u8 = and i32 %cy_c1, 255
  %cx_sh = shl i32 %cx_u8, 4
  %cy_sh = shl i32 %cy_u8, 12
  %p2_0 = or i32 %qmask, %cx_sh
  %p2_val = or i32 %p2_0, %cy_sh
  %p2_b = call i32 @bcast(i32 %tid, i32 %p2_val, i32 %src2)
  br i1 %is0, label %st, label %exit
st:
  %win_mask0 = and i32 %p2_b, 15
  %t_cx2 = shl i32 %p2_b, 20
  %win_cx2_0 = ashr i32 %t_cx2, 24
  %t_cy2 = shl i32 %p2_b, 12
  %win_cy2_0 = ashr i32 %t_cy2, 24
  %sc2x0 = sub i32 %win_cx2_0, %gmx2
  %sc2y0 = sub i32 %win_cy2_0, %gmy2
  %sc2x = call i32 @llvm.abs.i32(i32 %sc2x0, i1 false)
  %sc2y = call i32 @llvm.abs.i32(i32 %sc2y0, i1 false)
  %sc2_win = add i32 %sc2x, %sc2y
  %sc1_gt = icmp sgt i32 %sc1, %sc2_win
  %swap_l = and i1 %has_split, %sc1_gt
  %mask_sw = xor i32 %win_mask0, 15
  %win_mask = select i1 %swap_l, i32 %mask_sw, i32 %win_mask0
  %wcx_c0 = call i32 @llvm.smax.i32(i32 %wincx, i32 -128)
  %wcx_c1 = call i32 @llvm.smin.i32(i32 %wcx_c0, i32 127)
  %wcy_c0 = call i32 @llvm.smax.i32(i32 %wincy, i32 -128)
  %wcy_c1 = call i32 @llvm.smin.i32(i32 %wcy_c0, i32 127)
  %win_cx2 = select i1 %swap_l, i32 %wcx_c1, i32 %win_cx2_0
  %win_cy2 = select i1 %swap_l, i32 %wcy_c1, i32 %win_cy2_0
  %v2x_i = shl i32 %win_cx2_0, 1
  %v2y_i = shl i32 %win_cy2_0, 1
  %v2x_f = sitofp i32 %v2x_i to float
  %v2y_f = sitofp i32 %v2y_i to float
  %fx0 = bitcast i32 %ox to float
  %fy0 = bitcast i32 %oy to float
  %vx1 = select i1 %swap_l, float %v2x_f, float %fx0
  %vy1 = select i1 %swap_l, float %v2y_f, float %fy0
  %avx1 = call float @llvm.fabs.f32(float %vx1)
  %avy1 = call float @llvm.fabs.f32(float %vy1)
  %x_tiny = fcmp olt float %avx1, 0.0625
  %y_tiny = fcmp olt float %avy1, 0.0625
  %vx_c = select i1 %x_tiny, float 0.0625, float %vx1
  %vy_c = select i1 %y_tiny, float 0.0625, float %vy1
  %bxi_c = bitcast float %vx_c to i32
  %byi_c = bitcast float %vy_c to i32
  %bxi_hom = and i32 %ox, -16
  %bxi_hi = and i32 %bxi_c, -4096
  %byi_hi = and i32 %byi_c, -256
  %cx2_m = and i32 %win_cx2, 255
  %cy2_m = and i32 %win_cy2, 255
  %cx2_s = shl i32 %cx2_m, 4
  %bxi_s0 = or i32 %bxi_hi, %cx2_s
  %bxi_spl = or i32 %bxi_s0, %win_mask
  %byi_spl = or i32 %byi_hi, %cy2_m
  %ox_f = select i1 %has_split, i32 %bxi_spl, i32 %bxi_hom
  %oy_f = select i1 %has_split, i32 %byi_spl, i32 %oy
  %fx = bitcast i32 %ox_f to float
  %fy = bitcast i32 %oy_f to float
  %o0 = insertelement <2 x float> undef, float %fx, i32 0
  %o1 = insertelement <2 x float> %o0, float %fy, i32 1
  %foff = shl i32 %gidx, 3
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %o1, <4 x i32> %vf, i32 %foff, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
