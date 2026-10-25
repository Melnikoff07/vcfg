; user_sgprs=2
; tgid_y=1
; mode 8.4, pass 6: raw motion field (filtered by mf8 afterwards) on L1 (960x540), 120 x 68 blocks of 8x8 (= 16x16 full-res px), on the MID grid.
; dispatch 30 x 68 groups; lane = block (tid>>4) * 16 + slot (tid&15): 4 blocks per wave, 16 candidate slots each.
;  phase 1: 4 nearest L2 vectors (x2) + 5 symmetric temporal predictors (same, +x, +y, -x, -y) + zero
;           + slot 8 fg/anchor + slot 9 camera + slots 12..15 outer L2 ring;
;  phase 2: +-1 around the winner, penalty LAM1 per px
;  phase 3: SADs of b, b-+x, b-+y, 0 -> equiangular sub-pixel fit, zero test, store float2 full-res px vector.
; table: +0x80 V#L1cur, +0x90 V#L1prev, +0xF0 V#V2 (60x34 packed), +0x100 V#Fcur, +0x110 V#Fprev, +0x120 V#misc,
;        +0x150 V#GK (120x68 splat keys from fgs8: prio | closeness | L2 block, 0 = none), +0x180 V#FT (raw)
target triple = "amdgcn--"
declare <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32, <4 x i32>, i32, i32, i32 immarg)
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

; Bilateral SAD of an 8x8 block using a 2-iteration 4-row loop (to stay well within 64 VGPRs),
; displacement-preserving clamp (r6), and structure-weighted DC-compensated SAD (r7).
define internal { i32, i32, i32 } @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy) alwaysinline {
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
  %sbin = phi i32 [ 0, %entry ], [ %sb3, %rowloop ]
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
  %sa0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 %qc0_0, i32 %sbin)
  %sb0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 %qc1_0, i32 %sa0)
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
  %sa1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_1, i32 %qc0_1, i32 %sb0)
  %sb1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_1, i32 %qc1_1, i32 %sa1)
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
  %sa2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_2, i32 %qc0_2, i32 %sb1)
  %sb2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_2, i32 %qc1_2, i32 %sa2)
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
  %sa3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_3, i32 %qc0_3, i32 %sb2)
  %sb3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_3, i32 %qc1_3, i32 %sa3)
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
  %sb7 = add i32 %sb3, 0
  %mb7 = add i32 %mb3, 0
  %md7 = add i32 %md3, 0
  %msum0 = add i32 %mb7, %md7
  %msum = lshr i32 %msum0, 1
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
  %sad0 = sub i32 %sb7, %dc_disc
  %sad_u = add i32 %sad0, %pen
  %sad = call i32 @llvm.smin.i32(i32 %sad_u, i32 65535)
  %msh = shl i32 %msum, 16
  %res = or i32 %sad, %msh
  %t_sum = add i32 %tp_out, %tc_out
  %t_avg = ashr i32 %t_sum, 1
  %t_min = call i32 @llvm.smin.i32(i32 %tp_out, i32 %tc_out)
  %rv0 = insertvalue { i32, i32, i32 } undef, i32 %res, 0
  %rv1 = insertvalue { i32, i32, i32 } %rv0, i32 %t_avg, 1
  %rv2 = insertvalue { i32, i32, i32 } %rv1, i32 %t_min, 2
  ret { i32, i32, i32 } %rv2
}

define internal i32 @norm_sad(i32 %sad_in, i32 %avg_sum, i32 %t_avg, i32 %t_min, i32 %ztex, i32 %zsum) alwaysinline {
  %num = call i32 @llvm.smax.i32(i32 %ztex, i32 96)
  %den = call i32 @llvm.smax.i32(i32 %t_avg, i32 96)
  %num16 = shl i32 %num, 4
  %q = udiv i32 %num16, %den
  %infl0 = call i32 @llvm.smax.i32(i32 %q, i32 16)
  %infl = call i32 @llvm.smin.i32(i32 %infl0, i32 64)
  %sad_m = mul i32 %sad_in, %infl
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
  %sad_out = call i32 @llvm.smin.i32(i32 %sad_n, i32 65535)
  ret i32 %sad_out
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

; 8.6: manhattan distance (L1 px) of (cx, cy) to the L2 vector of block (ix, iy) x 2
define internal i32 @nbdist(<4 x i32> %v2, i32 %ix, i32 %iy, i32 %cx, i32 %cy) alwaysinline {
  %r = mul i32 %iy, 60
  %i = add i32 %r, %ix
  %o = shl i32 %i, 2
  %v = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %o, i32 0, i32 0)
  %ws = shl i32 %v, 16
  %wx = ashr i32 %ws, 16
  %wy = ashr i32 %v, 16
  %x2 = shl i32 %wx, 1
  %y2 = shl i32 %wy, 1
  %dx = sub i32 %cx, %x2
  %dy = sub i32 %cy, %y2
  %ax = call i32 @llvm.abs.i32(i32 %dx, i1 false)
  %ay = call i32 @llvm.abs.i32(i32 %dy, i1 false)
  %a = add i32 %ax, %ay
  ret i32 %a
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

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 128
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 144
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %p2 = getelementptr i8, ptr addrspace(4) %tbl, i64 240
  %v2 = load <4 x i32>, ptr addrspace(4) %p2, align 16, !invariant.load !0
  %pf = getelementptr i8, ptr addrspace(4) %tbl, i64 384
  %vf = load <4 x i32>, ptr addrspace(4) %pf, align 16, !invariant.load !0
  %pt = getelementptr i8, ptr addrspace(4) %tbl, i64 272
  %vt = load <4 x i32>, ptr addrspace(4) %pt, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %j = lshr i32 %tid, 4
  %sub = and i32 %tid, 15
  %base = and i32 %tid, 48
  %gx4 = shl i32 %gx, 2
  %bx = add i32 %gx4, %j
  %X = shl i32 %bx, 3
  %Y = shl i32 %gy, 3
  ; ---- the 4 L2 blocks around this block centre (symmetric border clamp [0,58]x[0,32]) + outer L2 ring
  %bx4 = shl i32 %bx, 2
  %fxm = sub i32 %bx4, 2
  %ix0r = ashr i32 %fxm, 3
  %ix0a = call i32 @llvm.smax.i32(i32 %ix0r, i32 0)
  %ix0 = call i32 @llvm.smin.i32(i32 %ix0a, i32 58)
  %ix1 = add i32 %ix0, 1
  %by4 = shl i32 %gy, 2
  %fym = sub i32 %by4, 2
  %iy0r = ashr i32 %fym, 3
  %iy0a = call i32 @llvm.smax.i32(i32 %iy0r, i32 0)
  %iy0 = call i32 @llvm.smin.i32(i32 %iy0a, i32 32)
  %iy1 = add i32 %iy0, 1
  %s1 = and i32 %sub, 1
  %s1b = icmp ne i32 %s1, 0
  %nx0 = select i1 %s1b, i32 %ix1, i32 %ix0
  %s2 = and i32 %sub, 2
  %s2b = icmp ne i32 %s2, 0
  %ny0 = select i1 %s2b, i32 %iy1, i32 %iy0
  %ixm1a = call i32 @llvm.smax.i32(i32 %ix0r, i32 1)
  %ixm1 = sub i32 %ixm1a, 1
  %ixp1a = add i32 %ix1, 1
  %ixp1 = call i32 @llvm.smin.i32(i32 %ixp1a, i32 59)
  %iym1a = call i32 @llvm.smax.i32(i32 %iy0r, i32 1)
  %iym1 = sub i32 %iym1a, 1
  %iyp1a = add i32 %iy1, 1
  %iyp1 = call i32 @llvm.smin.i32(i32 %iyp1a, i32 33)
  %is12 = icmp eq i32 %sub, 12
  %is13 = icmp eq i32 %sub, 13
  %is14 = icmp eq i32 %sub, 14
  %is15 = icmp eq i32 %sub, 15
  %is1213 = or i1 %is12, %is13
  %is1415 = or i1 %is14, %is15
  %isout = or i1 %is1213, %is1415
  %nx1 = select i1 %is12, i32 %ixm1, i32 %nx0
  %nx2 = select i1 %is13, i32 %ixp1, i32 %nx1
  %nx = select i1 %is1415, i32 %ix0, i32 %nx2
  %ny1 = select i1 %is1213, i32 %iy0, i32 %ny0
  %ny2 = select i1 %is14, i32 %iym1, i32 %ny1
  %ny = select i1 %is15, i32 %iyp1, i32 %ny2
  %nrow = mul i32 %ny, 60
  %nidx = add i32 %nrow, %nx
  %noff = shl i32 %nidx, 2
  %nv = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %noff, i32 0, i32 0)
  %nvx = call i32 @unx(i32 %nv)
  %nvy = call i32 @uny(i32 %nv)
  %ndx = shl i32 %nvx, 1
  %ndy = shl i32 %nvy, 1
  ; ---- symmetric temporal predictors (previous frame's final field: same, +x, +y, -x, -y)
  %is5 = icmp eq i32 %sub, 5
  %is6 = icmp eq i32 %sub, 6
  %is10 = icmp eq i32 %sub, 10
  %is11 = icmp eq i32 %sub, 11
  %is1011 = or i1 %is10, %is11
  %tox5 = select i1 %is5, i32 1, i32 0
  %tox = select i1 %is10, i32 -1, i32 %tox5
  %toy6 = select i1 %is6, i32 1, i32 0
  %toy = select i1 %is11, i32 -1, i32 %toy6
  %tbxa = add i32 %bx, %tox
  %tbxb = call i32 @llvm.smax.i32(i32 %tbxa, i32 0)
  %tbx = call i32 @llvm.smin.i32(i32 %tbxb, i32 119)
  %tbya = add i32 %gy, %toy
  %tbyb = call i32 @llvm.smax.i32(i32 %tbya, i32 0)
  %tby = call i32 @llvm.smin.i32(i32 %tbyb, i32 67)
  %trow = mul i32 %tby, 120
  %tidx = add i32 %trow, %tbx
  %toff = shl i32 %tidx, 3
  %tv = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vt, i32 %toff, i32 0, i32 0)
  %tvx = extractelement <2 x float> %tv, i32 0
  %tvy = extractelement <2 x float> %tv, i32 1
  %tvxh = fmul float %tvx, 0.5
  %tvyh = fmul float %tvy, 0.5
  %tdx = call i32 @rnd(float %tvxh)
  %tdy = call i32 @rnd(float %tvyh)
  ; ---- foreground candidate: splat key of this block -> L2 block -> its vector x2
  %grow = mul i32 %gy, 120
  %gidx = add i32 %grow, %bx
  %goff = shl i32 %gidx, 2
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgk, i32 %goff, i32 0, i32 0)
  %hasfg = icmp ne i32 %gk, 0
  %gsrc = and i32 %gk, 4095
  %gsoff = shl i32 %gsrc, 2
  %gv = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %gsoff, i32 0, i32 0)
  %gvx = call i32 @unx(i32 %gv)
  %gvy = call i32 @uny(i32 %gv)
  %gdx = shl i32 %gvx, 1
  %gdy = shl i32 %gvy, 1
  ; ---- camera vector (gm8, L2 px) -> L1 px; the camera stands still when gm = 0 and fg_blocks <= 32
  %gmx = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 16, i32 0, i32 0)
  %gmy = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 20, i32 0, i32 0)
  %fgcnt = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 24, i32 0, i32 0)
  %fg_still = icmp ule i32 %fgcnt, 32
  %gmx2 = shl i32 %gmx, 1
  %gmy2 = shl i32 %gmy, 1
  %gmor = or i32 %gmx, %gmy
  %still0 = icmp eq i32 %gmor, 0
  %still = and i1 %still0, %fg_still
  %nstl = xor i1 %still0, true
  ; ---- phase 1: slots 0..3 inner L2, 4..6 & 10..11 temporal, 7 zero, 8 fg/anchor, 9 camera, 12..15 outer L2
  %islt4 = icmp ult i32 %sub, 4
  %isl2 = or i1 %islt4, %isout
  %s4 = sub i32 %sub, 4
  %ist0 = icmp ult i32 %s4, 3
  %ist = or i1 %ist0, %is1011
  %is8 = icmp eq i32 %sub, 8
  %cx0 = select i1 %ist, i32 %tdx, i32 0
  %cy0 = select i1 %ist, i32 %tdy, i32 0
  %is9 = icmp eq i32 %sub, 9
  %cx9 = select i1 %is9, i32 %gmx2, i32 %cx0
  %cy9 = select i1 %is9, i32 %gmy2, i32 %cy0
  %cx8 = select i1 %is8, i32 %gdx, i32 %cx9
  %cy8 = select i1 %is8, i32 %gdy, i32 %cy9
  %cdx = select i1 %isl2, i32 %ndx, i32 %cx8
  %cdy = select i1 %isl2, i32 %ndy, i32 %cy8
  %zsrc = add i32 %base, 7
  %bs1_st = call { i32, i32, i32 } @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %cdx, i32 %cdy)
  %bs1 = extractvalue { i32, i32, i32 } %bs1_st, 0
  %tex1 = extractvalue { i32, i32, i32 } %bs1_st, 1
  %tmin1 = extractvalue { i32, i32, i32 } %bs1_st, 2
  %sad1_0 = and i32 %bs1, 65535
  %sum1 = lshr i32 %bs1, 16
  ; 8.7: triangle-inequality penalty. Lane 7 (zero vector) publishes this block's centre info (mean luma sum << 16 | zero SAD) and midpoint texture ztex.
  %zinfo = call i32 @bcast(i32 %tid, i32 %bs1, i32 %zsrc)
  %ztex = call i32 @bcast(i32 %tid, i32 %tex1, i32 %zsrc)
  %zsad0 = and i32 %zinfo, 65535
  %sum0 = lshr i32 %zinfo, 16
  %sad1 = call i32 @norm_sad(i32 %sad1_0, i32 %sum1, i32 %tex1, i32 %tmin1, i32 %ztex, i32 %sum0)
  %isz = icmp eq i32 %sub, 7
  %bt = select i1 %ist, i32 16, i32 0
  %zb = select i1 %still, i32 48, i32 0
  %bz = select i1 %isz, i32 %zb, i32 %bt
  %zgt160 = icmp sgt i32 %ztex, 160
  %t1x2 = shl i32 %tex1, 1
  %t1ltz = icmp slt i32 %t1x2, %ztex
  %no_cb = and i1 %zgt160, %t1ltz
  %cb9 = select i1 %no_cb, i32 0, i32 32
  %b9 = select i1 %is9, i32 %cb9, i32 %bz
  ; 8.5 anchor cells (key < 1 << 20): slot 8 bias 64, camera-escape penalty for candidates jumping onto camera/sky
  %isfgk = icmp uge i32 %gk, 1048576
  %nfgk = xor i1 %isfgk, true
  %isanc = and i1 %hasfg, %nfgk
  %b8 = select i1 %isfgk, i32 96, i32 64
  %bias0 = select i1 %is8, i32 %b8, i32 %b9
  %open = select i1 %isout, i32 8, i32 0
  %bias = sub i32 %bias0, %open
  %scx0 = sub i32 %cdx, %gmx2
  %scy0 = sub i32 %cdy, %gmy2
  %scx = call i32 @llvm.abs.i32(i32 %scx0, i1 false)
  %scy = call i32 @llvm.abs.i32(i32 %scy0, i1 false)
  %sc = add i32 %scx, %scy
  %adx = sub i32 %cdx, %gdx
  %ady = sub i32 %cdy, %gdy
  %aadx = call i32 @llvm.abs.i32(i32 %adx, i1 false)
  %aady = call i32 @llvm.abs.i32(i32 %ady, i1 false)
  %aad = add i32 %aadx, %aady
  %aadgt3 = icmp sgt i32 %aad, 3
  %aadgt16 = icmp sgt i32 %aad, 16
  %sc2 = shl i32 %sc, 1
  %scle = icmp sle i32 %sc2, %aad
  %cam_esc = and i1 %nstl, %scle
  %esc = or i1 %aadgt16, %cam_esc
  %afar = and i1 %aadgt3, %esc
  %not8 = xor i1 %is8, true
  %ap0 = and i1 %isanc, %afar
  %ap1 = and i1 %ap0, %not8
  %apen = select i1 %ap1, i32 512, i32 0
  %c1a = add i32 %sad1, 256
  %c1ab = add i32 %c1a, %apen
  %p1x = call i32 @llvm.abs.i32(i32 %cdx, i1 false)
  %p1y = call i32 @llvm.abs.i32(i32 %cdy, i1 false)
  %p1m = add i32 %p1x, %p1y
  %p1f = icmp ugt i32 %p1m, 8
  %p1d0 = sub i32 %sum1, %sum0
  %p1d = call i32 @llvm.abs.i32(i32 %p1d0, i1 false)
  %p1h = lshr i32 %p1d, 1
  %p1e0 = sub i32 %p1h, %zsad0
  %p1e = call i32 @llvm.smax.i32(i32 %p1e0, i32 0)
  %is89 = or i1 %is8, %is9
  %ist811 = or i1 %is89, %is1011
  %nist811 = xor i1 %ist811, true
  %nisl2 = xor i1 %isl2, true
  %scgt16 = icmp sgt i32 %sc, 16
  %p1g_nb = or i1 %nisl2, %scgt16
  %p1g_b = and i1 %nist811, %p1g_nb
  %zgt140 = icmp sgt i32 %ztex, 140
  %t1lt96 = icmp slt i32 %tex1, 96
  %sm_jmp = and i1 %zgt140, %t1lt96
  %p1g_or = or i1 %sm_jmp, %p1g_b
  %p1_gate = and i1 %nfgk, %p1g_or
  %p1a = and i1 %p1f, %p1_gate
  %p1p = select i1 %p1a, i32 %p1e, i32 0
  %c1b = add i32 %c1ab, %p1p
  %cost1_0 = sub i32 %c1b, %bias
  ; 8.5 diagnostic (consts flags bit 6, inject letter 'T'): temporal slots off
  %pcnt = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnt4 = load <4 x i32>, ptr addrspace(4) %pcnt, align 16, !invariant.load !0
  %flt = extractelement <4 x i32> %cnt4, i32 2
  %flt64 = and i32 %flt, 64
  %notp = icmp ne i32 %flt64, 0
  %tpoff = and i1 %ist, %notp
  ; 8.6 temporal support & r6 OOB tie-break penalty: distance of this lane's candidate to the support set
  %sa0 = call i32 @nbdist(<4 x i32> %v2, i32 %ix0, i32 %iy0, i32 %cdx, i32 %cdy)
  %sa1 = call i32 @nbdist(<4 x i32> %v2, i32 %ix1, i32 %iy0, i32 %cdx, i32 %cdy)
  %sa2 = call i32 @nbdist(<4 x i32> %v2, i32 %ix0, i32 %iy1, i32 %cdx, i32 %cdy)
  %sa3 = call i32 @nbdist(<4 x i32> %v2, i32 %ix1, i32 %iy1, i32 %cdx, i32 %cdy)
  %szx = call i32 @llvm.abs.i32(i32 %cdx, i1 false)
  %szy = call i32 @llvm.abs.i32(i32 %cdy, i1 false)
  %sz = add i32 %szx, %szy
  %sgx0 = sub i32 %cdx, %gdx
  %sgy0 = sub i32 %cdy, %gdy
  %sgx = call i32 @llvm.abs.i32(i32 %sgx0, i1 false)
  %sgy = call i32 @llvm.abs.i32(i32 %sgy0, i1 false)
  %sg0 = add i32 %sgx, %sgy
  %sg = select i1 %hasfg, i32 %sg0, i32 %sz
  %sm01 = call i32 @llvm.umin.i32(i32 %sa0, i32 %sa1)
  %sm23 = call i32 @llvm.umin.i32(i32 %sa2, i32 %sa3)
  %sm03 = call i32 @llvm.umin.i32(i32 %sm01, i32 %sm23)
  %sm03c = call i32 @llvm.umin.i32(i32 %sm03, i32 %sc)
  %sp_un = icmp ugt i32 %sm03c, 3
  %fcx1 = ashr i32 %cdx, 1
  %fcy1 = ashr i32 %cdy, 1
  %xc1_0 = sub i32 %X, %fcx1
  %xp1_0 = add i32 %xc1_0, %cdx
  %yc1_0 = sub i32 %Y, %fcy1
  %yp1_0 = add i32 %yc1_0, %cdy
  %ox1_0 = icmp ugt i32 %xc1_0, 952
  %ox1_1 = icmp ugt i32 %xp1_0, 952
  %oy1_0 = icmp ugt i32 %yc1_0, 532
  %oy1_1 = icmp ugt i32 %yp1_0, 532
  %oobx1 = or i1 %ox1_0, %ox1_1
  %ooby1 = or i1 %oy1_0, %oy1_1
  %oob1 = or i1 %oobx1, %ooby1
  %oob_u0 = and i1 %oob1, %sp_un
  %oob_u = and i1 %oob_u0, %nfgk
  %oob_p = select i1 %oob_u, i32 128, i32 0
  %cost1 = add i32 %cost1_0, %oob_p
  %k1a = shl i32 %cost1, 8
  %k1b = or i32 %k1a, %sub
  %smz = call i32 @llvm.umin.i32(i32 %sz, i32 %sc)
  %smg = call i32 @llvm.umin.i32(i32 %smz, i32 %sg)
  %smin = call i32 @llvm.umin.i32(i32 %sm03, i32 %smg)
  %supd = icmp ule i32 %smin, 3
  %small = icmp ule i32 %sz, 64
  %tok0 = or i1 %supd, %small
  %tok1 = or i1 %tok0, %isfgk
  %flp = and i32 %flt, 512                   ; inject letter 'P': temporal support gate off (A/B)
  %nogate = icmp ne i32 %flp, 0
  %tok = or i1 %tok1, %nogate
  %tbad0 = xor i1 %tok, true
  %tbad = and i1 %ist, %tbad0
  %ntbad = xor i1 %tbad, true
  %val1a1 = xor i1 %is89, true
  %val1a0 = and i1 %val1a1, %ntbad
  %ntpoff = xor i1 %tpoff, true
  %val1a = and i1 %val1a0, %ntpoff
  %val1b = and i1 %is8, %hasfg
  %val1c = or i1 %val1a, %val1b
  %val1 = or i1 %val1c, %is9
  %key1 = select i1 %val1, i32 %k1b, i32 -1
  %m1 = call i32 @reduce(i32 %tid, i32 %key1, i32 %base, i32 16)
  %w1 = and i32 %m1, 255
  %cpk = call i32 @pack(i32 %cdx, i32 %cdy)
  %src1 = add i32 %base, %w1
  %c1 = call i32 @bcast(i32 %tid, i32 %cpk, i32 %src1)
  %c1x = call i32 @unx(i32 %c1)
  %c1y = call i32 @uny(i32 %c1)
  ; ---- phase 2: +-1
  %sub9 = call i32 @llvm.umin.i32(i32 %sub, i32 8)
  %oxu = urem i32 %sub9, 3
  %oyu = udiv i32 %sub9, 3
  %ox = sub i32 %oxu, 1
  %oy = sub i32 %oyu, 1
  %d2x = add i32 %c1x, %ox
  %d2y = add i32 %c1y, %oy
  %bs2_st = call { i32, i32, i32 } @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %d2x, i32 %d2y)
  %bs2 = extractvalue { i32, i32, i32 } %bs2_st, 0
  %tex2 = extractvalue { i32, i32, i32 } %bs2_st, 1
  %tmin2 = extractvalue { i32, i32, i32 } %bs2_st, 2
  %sad2_0 = and i32 %bs2, 65535
  %sum2 = lshr i32 %bs2, 16
  %sad2 = call i32 @norm_sad(i32 %sad2_0, i32 %sum2, i32 %tex2, i32 %tmin2, i32 %ztex, i32 %sum0)
  %p2x = call i32 @llvm.abs.i32(i32 %d2x, i1 false)
  %p2y = call i32 @llvm.abs.i32(i32 %d2y, i1 false)
  %p2m = add i32 %p2x, %p2y
  %p2f = icmp ugt i32 %p2m, 8
  %p2d0 = sub i32 %sum2, %sum0
  %p2d = call i32 @llvm.abs.i32(i32 %p2d0, i1 false)
  %p2h = lshr i32 %p2d, 1
  %p2e0 = sub i32 %p2h, %zsad0
  %p2e = call i32 @llvm.smax.i32(i32 %p2e0, i32 0)
  %sc2x0 = sub i32 %d2x, %gmx2
  %sc2y0 = sub i32 %d2y, %gmy2
  %sc2x = call i32 @llvm.abs.i32(i32 %sc2x0, i1 false)
  %sc2y = call i32 @llvm.abs.i32(i32 %sc2y0, i1 false)
  %sc2d = add i32 %sc2x, %sc2y
  %sc2le6 = icmp sle i32 %sc2d, 6
  %cam2_near = and i1 %nstl, %sc2le6
  %p2g0 = or i1 %isanc, %cam2_near
  %p2_gate = and i1 %nfgk, %p2g0
  %p2a = and i1 %p2f, %p2_gate
  %p2p = select i1 %p2a, i32 %p2e, i32 0
  %aox = call i32 @llvm.abs.i32(i32 %ox, i1 false)
  %aoy = call i32 @llvm.abs.i32(i32 %oy, i1 false)
  %ao = add i32 %aox, %aoy
  %pen2 = shl i32 %ao, 2
  %c2a = add i32 %sad2, 256
  %c2b = add i32 %c2a, %p2p
  %cost2 = add i32 %c2b, %pen2
  %k2a = shl i32 %cost2, 8
  %k2b = or i32 %k2a, %sub
  %val2 = icmp ult i32 %sub, 9
  %key2 = select i1 %val2, i32 %k2b, i32 -1
  %m2 = call i32 @reduce(i32 %tid, i32 %key2, i32 %base, i32 16)
  %w2 = and i32 %m2, 255
  %wxu = urem i32 %w2, 3
  %wyu = udiv i32 %w2, 3
  %bvx0 = add i32 %c1x, %wxu
  %bvx = sub i32 %bvx0, 1
  %bvy0 = add i32 %c1y, %wyu
  %bvy = sub i32 %bvy0, 1
  ; ---- phase 3: slots 0 b, 1 b-x, 2 b+x, 3 b-y, 4 b+y, 5 zero
  %q1 = icmp eq i32 %sub, 1
  %q2 = icmp eq i32 %sub, 2
  %q3 = icmp eq i32 %sub, 3
  %q4 = icmp eq i32 %sub, 4
  %q5 = icmp eq i32 %sub, 5
  %ex1 = select i1 %q1, i32 -1, i32 0
  %ex = select i1 %q2, i32 1, i32 %ex1
  %ey1 = select i1 %q3, i32 -1, i32 0
  %ey = select i1 %q4, i32 1, i32 %ey1
  %d3x0 = add i32 %bvx, %ex
  %d3y0 = add i32 %bvy, %ey
  %d3x = select i1 %q5, i32 0, i32 %d3x0
  %d3y = select i1 %q5, i32 0, i32 %d3y0
  %bs3_st = call { i32, i32, i32 } @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %d3x, i32 %d3y)
  %bs3 = extractvalue { i32, i32, i32 } %bs3_st, 0
  %sad3 = and i32 %bs3, 65535
  %pk3 = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %tid
  store i32 %sad3, ptr addrspace(3) %pk3, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  %is0 = icmp eq i32 %sub, 0
  br i1 %is0, label %fin, label %exit
fin:
  %pb0 = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %base
  %r0 = load i32, ptr addrspace(3) %pb0, align 4
  %b1i = add i32 %base, 1
  %pb1 = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %b1i
  %r1 = load i32, ptr addrspace(3) %pb1, align 4
  %b2i = add i32 %base, 2
  %pb2 = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %b2i
  %r2 = load i32, ptr addrspace(3) %pb2, align 4
  %b3i = add i32 %base, 3
  %pb3 = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %b3i
  %r3 = load i32, ptr addrspace(3) %pb3, align 4
  %b4i = add i32 %base, 4
  %pb4 = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %b4i
  %r4 = load i32, ptr addrspace(3) %pb4, align 4
  %b5i = add i32 %base, 5
  %pb5 = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %b5i
  %r5 = load i32, ptr addrspace(3) %pb5, align 4
  %f0 = uitofp i32 %r0 to float
  %f1 = uitofp i32 %r1 to float
  %f2 = uitofp i32 %r2 to float
  %f3 = uitofp i32 %r3 to float
  %f4 = uitofp i32 %r4 to float
  %spx = call float @equi(float %f1, float %f0, float %f2)
  %spy = call float @equi(float %f3, float %f0, float %f4)
  %bfx = sitofp i32 %bvx to float
  %bfy = sitofp i32 %bvy to float
  %vx0 = fadd float %bfx, %spx
  %vy0 = fadd float %bfy, %spy
  %vx = fmul float %vx0, 2.0
  %vy = fmul float %vy0, 2.0
  %zlim = add i32 %r0, 48
  %zero0a = icmp ule i32 %r5, %zlim
  %nofg = xor i1 %isfgk, true   ; 8.6: zero test skipped under real foreground keys only
  %zero0b = and i1 %zero0a, %nofg
  %zero0 = and i1 %zero0b, %still
  ; global fade / flash: frame luma sum changed by more than 2 % -> no motion (plain blend is exact for a fade)
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %par = extractelement <4 x i32> %cn, i32 0
  %so0 = shl i32 %par, 2
  %soc = add i32 %so0, 8
  %npar = xor i32 %par, 1
  %so1 = shl i32 %npar, 2
  %sop = add i32 %so1, 8
  %sumc = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %soc, i32 0, i32 0)
  %sump = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %sop, i32 0, i32 0)
  %fsc = uitofp i32 %sumc to float
  %fsp = uitofp i32 %sump to float
  %fdif = fsub float %fsc, %fsp
  %fad = call float @llvm.fabs.f32(float %fdif)
  %flim = fmul float %fsp, 0x3F947AE140000000 ; 0.02
  %fade0 = fcmp ogt float %fad, %flim
  ; 8.3: only while the camera (gm8) moves at most 1 L2 px (4 full-res px) per frame and fg_blocks <= 32.
  %gax = call i32 @llvm.abs.i32(i32 %gmx, i1 false)
  %gay = call i32 @llvm.abs.i32(i32 %gmy, i1 false)
  %gaxy = call i32 @llvm.smax.i32(i32 %gax, i32 %gay)
  %slow1a = icmp sle i32 %gaxy, 1
  %slow1 = and i1 %slow1a, %fg_still
  %fade = and i1 %fade0, %slow1
  %zero = or i1 %zero0, %fade
  ; block (0, 0) reports the fade decision in misc dword 7 (frame statistics for the log)
  %b00a = or i32 %bx, %gy
  %b00 = icmp eq i32 %b00a, 0
  br i1 %b00, label %rfade, label %fin2
rfade:
  %fadei = zext i1 %fade to i32
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %fadei, <4 x i32> %vm, i32 28, i32 0, i32 0)
  br label %fin2
fin2:
  %ovx = select i1 %zero, float 0.0, float %vx
  %ovy = select i1 %zero, float 0.0, float %vy
  %o0 = insertelement <2 x float> undef, float %ovx, i32 0
  %o1 = insertelement <2 x float> %o0, float %ovy, i32 1
  %frow = mul i32 %gy, 120
  %fidx = add i32 %frow, %bx
  %foff = shl i32 %fidx, 3
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %o1, <4 x i32> %vf, i32 %foff, i32 0, i32 0)
  ; unmatched block: best and zero SAD above 384 + the SAD a uniform brightness change alone would cause
  ; (64 px * mean luma change per px), and motion SAD r0 did not reduce static SAD r5 by >= 2x (r0 * 2 > r5)
  %mn = call i32 @llvm.umin.i32(i32 %r0, i32 %r5)
  %mnf = uitofp i32 %mn to float
  %fdl = fmul float %fad, 0x3F602E85C0000000; 0.0019753087
  %cthr = fadd float %fdl, 384.0
  %cut0 = fcmp ogt float %mnf, %cthr
  %r0_2 = shl i32 %r0, 1
  %no_gain = icmp ugt i32 %r0_2, %r5
  %cut = and i1 %cut0, %no_gain
  br i1 %cut, label %cnt, label %exit
cnt:
  %old = call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 1, <4 x i32> %vm, i32 0, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

; equiangular (V shaped) sub-pixel fit of an L1 cost: o = (m - p) / (2 (max(m, p) - c)), clamped to +-0.5
define internal float @equi(float %m, float %c, float %p) alwaysinline {
  %mx = call float @llvm.maxnum.f32(float %m, float %p)
  %mn = call float @llvm.minnum.f32(float %m, float %p)
  %mn1 = call float @llvm.maxnum.f32(float %mn, float 1.0)
  %c2 = fmul float %c, 2.0
  %cg0 = fdiv float %c2, %mn1
  %cg = call float @llvm.minnum.f32(float %cg0, float 1.0)
  %d0 = fsub float %mx, %c
  %den = fmul float %d0, 2.0
  %num0 = fsub float %m, %p
  %num = fmul float %num0, %cg
  %q = fdiv float %num, %den
  %pos = fcmp olt float %c, %mn
  %o = select i1 %pos, float %q, float 0.0
  %o1 = call float @llvm.maxnum.f32(float %o, float -0.5)
  %o2 = call float @llvm.minnum.f32(float %o1, float 0.5)
  ret float %o2
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
