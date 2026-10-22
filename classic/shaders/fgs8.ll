; user_sgprs=2
; mode 8.2, pass 5: foreground splat (RCAS3 style forward splat, on blocks). One lane per L2 block (60 x 34 = 2040),
; dispatch 32 groups of 64.
; table: +0xA0 V#L2cur, +0xB0 V#L2prev, +0xE0 V#V3 (anchor history at +2048), +0xF0 V#V2, +0x120 V#misc,
;        +0x140 V#S2, +0x150 V#GK out
target triple = "amdgcn--"
declare i8 @llvm.amdgcn.raw.buffer.load.i8(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i8(i8, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.atomic.umax.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.smin.i32(i32, i32)
declare i32 @llvm.umin.i32(i32, i32)
declare float @llvm.sqrt.f32(float)
declare <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.alignbyte(i32, i32, i32)
declare i32 @llvm.amdgcn.sad.u8(i32, i32, i32)
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.abs.i32(i32, i1 immarg)

; Bilateral SAD with displacement-preserving clamp (r6) and optional DC-compensated SAD (r7).
define internal i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy, i1 %dc_en) alwaysinline {
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
  %rp0 = mul i32 %ybp, %wl
  %ap0 = add i32 %rp0, %axp
  %rc0 = mul i32 %ybc, %wl
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
  %sa0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 %qc0_0, i32 0)
  %sb0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 %qc1_0, i32 %sa0)
  %ma0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 0, i32 0)
  %mb0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 0, i32 %ma0)
  %mc0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_0, i32 0, i32 0)
  %md0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_0, i32 0, i32 %mc0)
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
  %ap4 = add i32 %ap3, %wl
  %ac4 = add i32 %ac3, %wl
  %dp4 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap4, i32 0, i32 0)
  %dc4 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac4, i32 0, i32 0)
  %p0_4 = extractelement <3 x i32> %dp4, i32 0
  %p1_4 = extractelement <3 x i32> %dp4, i32 1
  %p2_4 = extractelement <3 x i32> %dp4, i32 2
  %c0_4 = extractelement <3 x i32> %dc4, i32 0
  %c1_4 = extractelement <3 x i32> %dc4, i32 1
  %c2_4 = extractelement <3 x i32> %dc4, i32 2
  %qp0_4 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_4, i32 %p0_4, i32 %shp)
  %qp1_4 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_4, i32 %p1_4, i32 %shp)
  %qc0_4 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_4, i32 %c0_4, i32 %shc)
  %qc1_4 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_4, i32 %c1_4, i32 %shc)
  %sa4 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_4, i32 %qc0_4, i32 %sb3)
  %sb4 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_4, i32 %qc1_4, i32 %sa4)
  %ma4 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_4, i32 0, i32 %mb3)
  %mb4 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_4, i32 0, i32 %ma4)
  %mc4 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_4, i32 0, i32 %md3)
  %md4 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_4, i32 0, i32 %mc4)
  %ap5 = add i32 %ap4, %wl
  %ac5 = add i32 %ac4, %wl
  %dp5 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap5, i32 0, i32 0)
  %dc5 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac5, i32 0, i32 0)
  %p0_5 = extractelement <3 x i32> %dp5, i32 0
  %p1_5 = extractelement <3 x i32> %dp5, i32 1
  %p2_5 = extractelement <3 x i32> %dp5, i32 2
  %c0_5 = extractelement <3 x i32> %dc5, i32 0
  %c1_5 = extractelement <3 x i32> %dc5, i32 1
  %c2_5 = extractelement <3 x i32> %dc5, i32 2
  %qp0_5 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_5, i32 %p0_5, i32 %shp)
  %qp1_5 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_5, i32 %p1_5, i32 %shp)
  %qc0_5 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_5, i32 %c0_5, i32 %shc)
  %qc1_5 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_5, i32 %c1_5, i32 %shc)
  %sa5 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_5, i32 %qc0_5, i32 %sb4)
  %sb5 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_5, i32 %qc1_5, i32 %sa5)
  %ma5 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_5, i32 0, i32 %mb4)
  %mb5 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_5, i32 0, i32 %ma5)
  %mc5 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_5, i32 0, i32 %md4)
  %md5 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_5, i32 0, i32 %mc5)
  %ap6 = add i32 %ap5, %wl
  %ac6 = add i32 %ac5, %wl
  %dp6 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap6, i32 0, i32 0)
  %dc6 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac6, i32 0, i32 0)
  %p0_6 = extractelement <3 x i32> %dp6, i32 0
  %p1_6 = extractelement <3 x i32> %dp6, i32 1
  %p2_6 = extractelement <3 x i32> %dp6, i32 2
  %c0_6 = extractelement <3 x i32> %dc6, i32 0
  %c1_6 = extractelement <3 x i32> %dc6, i32 1
  %c2_6 = extractelement <3 x i32> %dc6, i32 2
  %qp0_6 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_6, i32 %p0_6, i32 %shp)
  %qp1_6 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_6, i32 %p1_6, i32 %shp)
  %qc0_6 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_6, i32 %c0_6, i32 %shc)
  %qc1_6 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_6, i32 %c1_6, i32 %shc)
  %sa6 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_6, i32 %qc0_6, i32 %sb5)
  %sb6 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_6, i32 %qc1_6, i32 %sa6)
  %ma6 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_6, i32 0, i32 %mb5)
  %mb6 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_6, i32 0, i32 %ma6)
  %mc6 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_6, i32 0, i32 %md5)
  %md6 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_6, i32 0, i32 %mc6)
  %ap7 = add i32 %ap6, %wl
  %ac7 = add i32 %ac6, %wl
  %dp7 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap7, i32 0, i32 0)
  %dc7 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac7, i32 0, i32 0)
  %p0_7 = extractelement <3 x i32> %dp7, i32 0
  %p1_7 = extractelement <3 x i32> %dp7, i32 1
  %p2_7 = extractelement <3 x i32> %dp7, i32 2
  %c0_7 = extractelement <3 x i32> %dc7, i32 0
  %c1_7 = extractelement <3 x i32> %dc7, i32 1
  %c2_7 = extractelement <3 x i32> %dc7, i32 2
  %qp0_7 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_7, i32 %p0_7, i32 %shp)
  %qp1_7 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_7, i32 %p1_7, i32 %shp)
  %qc0_7 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_7, i32 %c0_7, i32 %shc)
  %qc1_7 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_7, i32 %c1_7, i32 %shc)
  %sa7 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_7, i32 %qc0_7, i32 %sb6)
  %sb7 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_7, i32 %qc1_7, i32 %sa7)
  %ma7 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_7, i32 0, i32 %mb6)
  %mb7 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_7, i32 0, i32 %ma7)
  %mc7 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_7, i32 0, i32 %md6)
  %md7 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_7, i32 0, i32 %mc7)
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
  %sad1 = add i32 %sad0, %pen
  %sad = select i1 %dc_en, i32 %sad1, i32 %sb7
  ret i32 %sad
}

; Unidirectional SAD (cur grid): cur block at X, prev block at X + d.
define internal i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy, i1 %dc_en) alwaysinline {
  %hx = ashr i32 %dx, 1
  %hy = ashr i32 %dy, 1
  %X2 = add i32 %X, %hx
  %Y2 = add i32 %Y, %hy
  %s = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X2, i32 %Y2, i32 %dx, i32 %dy, i1 %dc_en)
  ret i32 %s
}

; floor(sqrt(m)) for m < 2^26: float estimate, then one correction step each way
define internal i32 @isqrt(i32 %m) alwaysinline {
  %f = uitofp i32 %m to float
  %r = call float @llvm.sqrt.f32(float %f)
  %s0 = fptoui float %r to i32
  %sq = mul i32 %s0, %s0
  %big = icmp ugt i32 %sq, %m
  %sm1 = sub i32 %s0, 1
  %s1 = select i1 %big, i32 %sm1, i32 %s0
  %n1 = add i32 %s1, 1
  %nq = mul i32 %n1, %n1
  %small = icmp ule i32 %nq, %m
  %s2 = select i1 %small, i32 %n1, i32 %s1
  ret i32 %s2
}

; one footprint cell (gx, gy): bounds check, closeness, atomic max
define internal void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gx, i32 %gy, i32 %pk) alwaysinline {
  %okx = icmp ult i32 %gx, 120
  %oky = icmp ult i32 %gy, 68
  %ok = and i1 %okx, %oky
  br i1 %ok, label %w, label %done
w:
  %gx16 = shl i32 %gx, 4
  %ccx = add i32 %gx16, 8
  %gy16 = shl i32 %gy, 4
  %ccy = add i32 %gy16, 8
  %ex = sub i32 %cx, %ccx
  %ey = sub i32 %cy, %ccy
  %ex2 = mul i32 %ex, %ex
  %ey2 = mul i32 %ey, %ey
  %e = add i32 %ex2, %ey2
  %e16 = shl i32 %e, 4
  %d4 = call i32 @isqrt(i32 %e16)
  %d4c = call i32 @llvm.umin.i32(i32 %d4, i32 255)
  %tie = sub i32 255, %d4c
  %tsh = shl i32 %tie, 12
  %key = or i32 %pk, %tsh
  %row = mul i32 %gy, 120
  %idx = add i32 %row, %gx
  %off = shl i32 %idx, 2
  %old = call i32 @llvm.amdgcn.raw.buffer.atomic.umax.i32(i32 %key, <4 x i32> %vk, i32 %off, i32 0, i32 0)
  br label %done
done:
  ret void
}

; neighbour (bx + ox, by + oy) of the 60 x 34 L2 grid exists and its vector is within 4 L2 px (manhattan) of (vx, vy)
define internal i1 @nbok(<4 x i32> %v2, i32 %bx, i32 %by, i32 %ox, i32 %oy, i32 %vx, i32 %vy) alwaysinline {
  %nx = add i32 %bx, %ox
  %ny = add i32 %by, %oy
  %inx = icmp ult i32 %nx, 60
  %iny = icmp ult i32 %ny, 34
  %in = and i1 %inx, %iny
  %nxc = call i32 @llvm.smin.i32(i32 %nx, i32 59)
  %nxd = call i32 @llvm.smax.i32(i32 %nxc, i32 0)
  %nyc = call i32 @llvm.smin.i32(i32 %ny, i32 33)
  %nyd = call i32 @llvm.smax.i32(i32 %nyc, i32 0)
  %r = mul i32 %nyd, 60
  %i = add i32 %r, %nxd
  %o = shl i32 %i, 2
  %v = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %o, i32 0, i32 0)
  %ws = shl i32 %v, 16
  %wx = ashr i32 %ws, 16
  %wy = ashr i32 %v, 16
  %dx = sub i32 %wx, %vx
  %dy = sub i32 %wy, %vy
  %ax0 = sub i32 0, %dx
  %ax = call i32 @llvm.smax.i32(i32 %dx, i32 %ax0)
  %ay0 = sub i32 0, %dy
  %ay = call i32 @llvm.smax.i32(i32 %dy, i32 %ay0)
  %a = add i32 %ax, %ay
  %close = icmp sle i32 %a, 4
  %ok = and i1 %in, %close
  ret i1 %ok
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) #0 {
entry:
  %g64 = shl i32 %gx, 6
  %i = add i32 %g64, %tid
  %in = icmp ult i32 %i, 2040
  br i1 %in, label %go, label %exit
go:
  %p2 = getelementptr i8, ptr addrspace(4) %tbl, i64 240
  %v2 = load <4 x i32>, ptr addrspace(4) %p2, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %ps = getelementptr i8, ptr addrspace(4) %tbl, i64 320
  %vs = load <4 x i32>, ptr addrspace(4) %ps, align 16, !invariant.load !0
  %pk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vk = load <4 x i32>, ptr addrspace(4) %pk, align 16, !invariant.load !0
  %off = shl i32 %i, 2
  %v = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %off, i32 0, i32 0)
  %s = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vs, i32 %off, i32 0, i32 0)
  %gmx = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 16, i32 0, i32 0)
  %gmy = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 20, i32 0, i32 0)
  ; ---- real match against the camera vector
  %pl2c = getelementptr i8, ptr addrspace(4) %tbl, i64 160
  %vl2c = load <4 x i32>, ptr addrspace(4) %pl2c, align 16, !invariant.load !0
  %pl2p = getelementptr i8, ptr addrspace(4) %tbl, i64 176
  %vl2p = load <4 x i32>, ptr addrspace(4) %pl2p, align 16, !invariant.load !0
  %bxq = urem i32 %i, 60
  %byq = udiv i32 %i, 60
  %BX = shl i32 %bxq, 3
  %BY = shl i32 %byq, 3
  %gmxm = sub i32 %gmx, 1
  %gmxp = add i32 %gmx, 1
  %gmym = sub i32 %gmy, 1
  %gmyp = add i32 %gmy, 1
  %cs0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmy, i1 true)
  %cs1 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmxm, i32 %gmy, i1 true)
  %cs2 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmxp, i32 %gmy, i1 true)
  %cs3 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmym, i1 true)
  %cs4 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmyp, i1 true)
  %cm1 = call i32 @llvm.umin.i32(i32 %cs0, i32 %cs1)
  %cm2 = call i32 @llvm.umin.i32(i32 %cs2, i32 %cs3)
  %cm3 = call i32 @llvm.umin.i32(i32 %cm1, i32 %cm2)
  %gsad = call i32 @llvm.umin.i32(i32 %cm3, i32 %cs4)
  %sb = and i32 %s, 65535
  %sb5 = mul i32 %sb, 5
  %gs4 = shl i32 %gsad, 2
  %g1 = icmp ule i32 %sb5, %gs4
  %gdif = sub i32 %gsad, %sb
  %g2 = icmp sge i32 %gdif, 96
  %good = and i1 %g1, %g2
  ; ---- deviation from the dominant motion > 8 full-res px (q > 3 + |gm|^2 / 16) and moving itself (|V2|^2 > 2 when camera moves)
  %vxs = shl i32 %v, 16
  %vx = ashr i32 %vxs, 16
  %vy = ashr i32 %v, 16
  %qx = sub i32 %vx, %gmx
  %qy = sub i32 %vy, %gmy
  %qx2 = mul i32 %qx, %qx
  %qy2 = mul i32 %qy, %qy
  %q = add i32 %qx2, %qy2
  %vx2 = mul i32 %vx, %vx
  %vy2 = mul i32 %vy, %vy
  %vv = add i32 %vx2, %vy2
  %gmx2 = mul i32 %gmx, %gmx
  %gmy2 = mul i32 %gmy, %gmy
  %gmv = add i32 %gmx2, %gmy2
  %gmv16 = lshr i32 %gmv, 4
  %devt = add i32 %gmv16, 3
  %dev = icmp ugt i32 %q, %devt
  %stl = icmp ule i32 %gmv, 4
  %mov_s = icmp ugt i32 %vv, 4
  %mov_m = icmp ugt i32 %vv, 2
  %mov = select i1 %stl, i1 %mov_s, i1 %mov_m
  ; 8.7 texture energy of both cur(BX, BY) and matched prev(BX+vx, BY+vy), plus real-match gate 2*sb <= texm on fg0
  %ax0 = call i32 @bsadu(<4 x i32> %vl2c, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 1, i32 0, i1 false)
  %ay0 = call i32 @bsadu(<4 x i32> %vl2c, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 0, i32 1, i1 false)
  %tex = add i32 %ax0, %ay0
  %BXp = add i32 %BX, %vx
  %BYp = add i32 %BY, %vy
  %px0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2p, i32 480, i32 270, i32 %BXp, i32 %BYp, i32 1, i32 0, i1 false)
  %py0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2p, i32 480, i32 270, i32 %BXp, i32 %BYp, i32 0, i32 1, i1 false)
  %texp = add i32 %px0, %py0
  %texm = call i32 @llvm.umin.i32(i32 %tex, i32 %texp)
  %istex0 = icmp uge i32 %texm, 64
  %pcng = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cng = load <4 x i32>, ptr addrspace(4) %pcng, align 16, !invariant.load !0
  %flgg = extractelement <4 x i32> %cng, i32 2
  %notx = and i32 %flgg, 256                 ; inject letter 'G': texture gate off (A/B)
  %notxb = icmp ne i32 %notx, 0
  %istex = or i1 %istex0, %notxb
  %sb2 = shl i32 %sb, 1
  %rm0 = icmp ule i32 %sb2, %texm
  %rm = or i1 %rm0, %notxb
  %f1a = and i1 %good, %dev
  %f1 = and i1 %f1a, %istex
  %fg0a = and i1 %f1, %mov
  %fg0 = and i1 %fg0a, %rm
  ; ---- 8.5 anchor: quasi-static screen-fixed object (|V2|^2 <= 2 and 4|V2|^2 <= |gm|^2), inner block, letter 'L' off
  %vv4 = shl i32 %vv, 2
  %slw0 = icmp ule i32 %vv, 2
  %slw1 = icmp ule i32 %vv4, %gmv
  %slw = and i1 %slw0, %slw1
  %bxm = add i32 %bxq, -1
  %inbx = icmp ult i32 %bxm, 58
  %bym = add i32 %byq, -1
  %inby = icmp ult i32 %bym, 32
  %inb = and i1 %inbx, %inby
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %flg = extractelement <4 x i32> %cn, i32 2
  %noab = and i32 %flg, 32
  %aon = icmp eq i32 %noab, 0
  ; 8.6: camera still (|gm|^2 <= 4): textured, own SAD <= texture / 2, |V2|^2 <= 1 (4 |V2|^2 <= 4)
  %vvs = icmp ule i32 %vv4, 4
  %as0 = and i1 %istex, %vvs
  %as1 = and i1 %as0, %rm
  %anm0 = and i1 %f1, %slw
  %anm = and i1 %anm0, %rm
  %an1 = select i1 %stl, i1 %as1, i1 %anm
  %an2 = and i1 %an1, %inb
  %an0 = and i1 %an2, %aon
  %need = or i1 %fg0, %an0
  br i1 %need, label %nbc, label %hist
nbc:
  ; 4 cardinal neighbours (in bounds) with |dV|_1 <= 4
  %nb0 = call i1 @nbok(<4 x i32> %v2, i32 %bxq, i32 %byq, i32 -1, i32 0, i32 %vx, i32 %vy)
  %nb1 = call i1 @nbok(<4 x i32> %v2, i32 %bxq, i32 %byq, i32 1, i32 0, i32 %vx, i32 %vy)
  %nb2 = call i1 @nbok(<4 x i32> %v2, i32 %bxq, i32 %byq, i32 0, i32 -1, i32 %vx, i32 %vy)
  %nb3 = call i1 @nbok(<4 x i32> %v2, i32 %bxq, i32 %byq, i32 0, i32 1, i32 %vx, i32 %vy)
  %nb0i = zext i1 %nb0 to i32
  %nb1i = zext i1 %nb1 to i32
  %nb2i = zext i1 %nb2 to i32
  %nb3i = zext i1 %nb3 to i32
  %nbc01 = add i32 %nb0i, %nb1i
  %nbc23 = add i32 %nb2i, %nb3i
  %nb_cnt = add i32 %nbc01, %nbc23
  %fg = icmp uge i32 %nb_cnt, 1
  %nb2ok = icmp uge i32 %nb_cnt, 2
  br label %hist
hist:
  %nbk = phi i1 [ false, %go ], [ %fg, %nbc ]
  %nb2k = phi i1 [ false, %go ], [ %nb2ok, %nbc ]
  %isfg = and i1 %fg0, %nbk
  %isa0 = and i1 %an0, %nbk
  ; anchor history: consecutive frames with the anchor condition (u8 per L2 block, V3 + 2048, zeroed at allocation)
  %pv3 = getelementptr i8, ptr addrspace(4) %tbl, i64 224
  %vv3 = load <4 x i32>, ptr addrspace(4) %pv3, align 16, !invariant.load !0
  %hoff = add i32 %i, 2048
  %h8 = call i8 @llvm.amdgcn.raw.buffer.load.i8(<4 x i32> %vv3, i32 %hoff, i32 0, i32 0)
  %h = zext i8 %h8 to i32
  %h1 = add i32 %h, 1
  %h1c = call i32 @llvm.umin.i32(i32 %h1, i32 255)
  %hn = select i1 %isa0, i32 %h1c, i32 0
  %hn8 = trunc i32 %hn to i8
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %hn8, <4 x i32> %vv3, i32 %hoff, i32 0, i32 0)
  %nstl = xor i1 %stl, true
  %g160 = icmp sge i32 %gdif, 160
  %sa0 = and i1 %nstl, %nb2k
  %strong_an = and i1 %sa0, %g160
  %hok0 = icmp uge i32 %hn, 2
  %hok = or i1 %hok0, %strong_an
  %isan = and i1 %isa0, %hok
  %doit = or i1 %isfg, %isan
  br i1 %doit, label %splat, label %exit
splat:
  %inc = zext i1 %isfg to i32
  %cnt = call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 %inc, <4 x i32> %vm, i32 24, i32 0, i32 0)
  %q4 = lshr i32 %q, 2
  %q4c = call i32 @llvm.umin.i32(i32 %q4, i32 16760836)     ; 4094^2
  %pr0 = call i32 @isqrt(i32 %q4c)
  %prio1 = add i32 %pr0, 1
  %prio = select i1 %isfg, i32 %prio1, i32 0               ; anchor: prio 0
  %psh = shl i32 %prio, 20
  %pkey = or i32 %psh, %i
  ; mid position of the block centre: 32 b + 16 + D/2 = 32 b + 16 + 2 V2 (full-res px)
  %bx = urem i32 %i, 60
  %by = udiv i32 %i, 60
  %bx32 = shl i32 %bx, 5
  %by32 = shl i32 %by, 5
  %cx0 = add i32 %bx32, 16
  %cy0 = add i32 %by32, 16
  %vx2x = shl i32 %vx, 1
  %vy2x = shl i32 %vy, 1
  %cx = add i32 %cx0, %vx2x
  %cy = add i32 %cy0, %vy2x
  ; cells floor((c - 16) / 16), floor(c / 16), floor((c + 15) / 16) in x and y
  %cxa = sub i32 %cx, 16
  %gxa = ashr i32 %cxa, 4
  %gxb = ashr i32 %cx, 4
  %cxc = add i32 %cx, 15
  %gxc = ashr i32 %cxc, 4
  %cya = sub i32 %cy, 16
  %gya = ashr i32 %cya, 4
  %gyb = ashr i32 %cy, 4
  %cyc = add i32 %cy, 15
  %gyc = ashr i32 %cyc, 4
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxa, i32 %gya, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxb, i32 %gya, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxc, i32 %gya, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxa, i32 %gyb, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxb, i32 %gyb, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxc, i32 %gyb, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxa, i32 %gyc, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxb, i32 %gyc, i32 %pkey)
  call void @put(<4 x i32> %vk, i32 %cx, i32 %cy, i32 %gxc, i32 %gyc, i32 %pkey)
  br label %exit
exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
