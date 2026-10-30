; user_sgprs=2
; tgid_y=1
; mode 8.2, pass 2: coarse motion on L3 (240x135), 30 x 17 blocks of 8x8 (= 64x64 full-res px), CUR grid
; (unidirectional SAD: cur block at X, prev block at X + d). One group (64 lanes) per block.
; Why the cur grid: a block on a fast object can only match the object itself. With the bilateral (mid grid) search of
; mode 8 a block where a fast thin object passes the middle of its path finds a background-background pair that matches
; equally well (the object is elsewhere in both frames), so the object vector never survived to the final field.
;  phase A: centre = zero or the temporal predictor (previous field, -TBIAS/4), whichever matches better
;  phase B: full search +-14 x +-7 L3 px around the centre (29 x 15 = 435 candidates, +-112 x +-56 full-res px),
;           penalty LAM3 per px. 8.4 (window of the other model, round 5): the console dump 03 is a turn of ~80..92 px
;           per frame, beyond the old +-10 (+-80 px) when the temporal centre is lost; vertical turns are slower.
;           (a step-2 grid + refine was tried in the reference model: it loses small fast objects)
; A camera pan faster than the window is still tracked from frame to frame through the temporal centre.
; Also adds the sum of its cur block to misc dword 2 + parity (zeroed by pyr8) for the global fade test in me1.
; table: +0x70 consts {parity, ...}, +0xC0 V#L3cur, +0xD0 V#L3prev, +0xE0 V#V3 out (30x17 packed), +0x110 V#Fprev,
;        +0x120 V#misc
target triple = "amdgcn--"
declare i32 @llvm.amdgcn.readlane.i32(i32, i32)
declare i32 @llvm.amdgcn.ds.swizzle(i32, i32 immarg)
declare <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare i8 @llvm.amdgcn.raw.buffer.load.i8(<4 x i32>, i32, i32, i32 immarg)
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
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

@keys = internal addrspace(3) global [64 x i32] undef, align 16
@vecs = internal addrspace(3) global [64 x i32] undef, align 16

; Bilateral SAD of an 8x8 block of two packed-u8 luma images (wl bytes per row, wl % 4 == 0, hl rows)
; with displacement-preserving clamp (r6) and structure-weighted DC-compensated SAD (r7).
define internal i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy) alwaysinline {
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
  %sad = add i32 %sad0, %pen
  ret i32 %sad
}

; Unidirectional SAD (cur grid): cur block at X, prev block at X + d. Same loads as @bsad shifted by floor(d/2):
; bsad(X + floor(d/2)) puts prev at X + floor(d/2) + ceil(d/2) = X + d and cur at X (clamping identical).
define internal i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy) alwaysinline {
  %hx = ashr i32 %dx, 1
  %hy = ashr i32 %dy, 1
  %X2 = add i32 %X, %hx
  %Y2 = add i32 %Y, %hy
  %s = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X2, i32 %Y2, i32 %dx, i32 %dy)
  ret i32 %s
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

; min of the keys of lanes [base, base+n) (n = 16 or 64), every lane gets the result. Single-wave workgroup:
; the barrier compiles away, the fences become s_waitcnt.
define internal i32 @reduce(i32 %tid, i32 %key, i32 %base, i32 %n) alwaysinline {
entry:
  %p = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %tid
  store i32 %key, ptr addrspace(3) %p, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ; butterfly over the lane's aligned group of 16 (ds_swizzle bit mode: and 0x1f, xor 1 / 2 / 4 / 8, 16, then the two wave halves)
  %s1 = call i32 @llvm.amdgcn.ds.swizzle(i32 %key, i32 1055)
  %r1 = call i32 @llvm.umin.i32(i32 %key, i32 %s1)
  %s2 = call i32 @llvm.amdgcn.ds.swizzle(i32 %r1, i32 2079)
  %r2 = call i32 @llvm.umin.i32(i32 %r1, i32 %s2)
  %s4 = call i32 @llvm.amdgcn.ds.swizzle(i32 %r2, i32 4127)
  %r4 = call i32 @llvm.umin.i32(i32 %r2, i32 %s4)
  %s8 = call i32 @llvm.amdgcn.ds.swizzle(i32 %r4, i32 8223)
  %r8 = call i32 @llvm.umin.i32(i32 %r4, i32 %s8)
  %s16 = call i32 @llvm.amdgcn.ds.swizzle(i32 %r8, i32 16415)
  %r16 = call i32 @llvm.umin.i32(i32 %r8, i32 %s16)
  %h0 = call i32 @llvm.amdgcn.readlane.i32(i32 %r16, i32 0)
  %h1 = call i32 @llvm.amdgcn.readlane.i32(i32 %r16, i32 32)
  %m1 = call i32 @llvm.umin.i32(i32 %h0, i32 %h1)
  br label %done
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

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 192
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 208
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %p3 = getelementptr i8, ptr addrspace(4) %tbl, i64 224
  %v3 = load <4 x i32>, ptr addrspace(4) %p3, align 16, !invariant.load !0
  %pt = getelementptr i8, ptr addrspace(4) %tbl, i64 272
  %vt = load <4 x i32>, ptr addrspace(4) %pt, align 16, !invariant.load !0
  %X = shl i32 %gx, 3
  %Y = shl i32 %gy, 3
  ; ---- temporal predictor: previous field at L1 block (4bx+2, 4by+2), full-res px -> L3 px
  %tbx0 = shl i32 %gx, 2
  %tbx1 = add i32 %tbx0, 2
  %tbx = call i32 @llvm.smin.i32(i32 %tbx1, i32 119)
  %tby0 = shl i32 %gy, 2
  %tby1 = add i32 %tby0, 2
  %tby = call i32 @llvm.smin.i32(i32 %tby1, i32 67)
  %trow = mul i32 %tby, 120
  %tidx = add i32 %trow, %tbx
  %toff = shl i32 %tidx, 3
  %tv = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vt, i32 %toff, i32 0, i32 0)
  %tvx = extractelement <2 x float> %tv, i32 0
  %tvy = extractelement <2 x float> %tv, i32 1
  %tvxq = fmul float %tvx, 0.125
  %tvyq = fmul float %tvy, 0.125
  %tdx = call i32 @rnd(float %tvxq)
  %tdy = call i32 @rnd(float %tvyq)
  ; ---- phase A: lane 0 zero, lane 1 temporal
  %is1 = icmp eq i32 %tid, 1
  %ax = select i1 %is1, i32 %tdx, i32 0
  %ay = select i1 %is1, i32 %tdy, i32 0
  %sa = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 240, i32 135, i32 %X, i32 %Y, i32 %ax, i32 %ay)
  %ba = select i1 %is1, i32 4, i32 0
  %ca0 = add i32 %sa, 256
  %ca = sub i32 %ca0, %ba
  %kah = shl i32 %ca, 8
  %ka = or i32 %kah, %tid
  %vala = icmp ult i32 %tid, 2
  %keya = select i1 %vala, i32 %ka, i32 -1
  %ma = call i32 @reduce(i32 %tid, i32 %keya, i32 0, i32 64)
  %wa = and i32 %ma, 255
  %usetemp = icmp eq i32 %wa, 1
  %cx = select i1 %usetemp, i32 %tdx, i32 0
  %cy = select i1 %usetemp, i32 %tdy, i32 0
  br label %pre
pre:
  ; ---- phase B: full search +-14x+-7 around the centre (29x15 = 435 candidates, raster order), lane handles k = tid + 64 i
  br label %grid
grid:
  %gi = phi i32 [ 0, %pre ], [ %gi1, %grid ]
  %best = phi i32 [ -1, %pre ], [ %best1, %grid ]
  %gk0 = shl i32 %gi, 6
  %gk = add i32 %gk0, %tid
  %kr = call i32 @costf(<4 x i32> %vp, <4 x i32> %vc, i32 %X, i32 %Y, i32 %cx, i32 %cy, i32 %gk)
  %kv = icmp ult i32 %gk, 435
  %kk = select i1 %kv, i32 %kr, i32 -1
  %best1 = call i32 @llvm.umin.i32(i32 %best, i32 %kk)
  %gi1 = add i32 %gi, 1
  %gmore = icmp ult i32 %gi1, 7
  br i1 %gmore, label %grid, label %gdone
gdone:
  %mb = call i32 @reduce(i32 %tid, i32 %best1, i32 0, i32 64)
  %wb = and i32 %mb, 511
  %wxu = urem i32 %wb, 29
  %wyu = udiv i32 %wb, 29
  %vx0 = add i32 %cx, %wxu
  %vx = sub i32 %vx0, 14
  %vy0 = add i32 %cy, %wyu
  %vy = sub i32 %vy0, 7
  ; ---- sum of this L3 block of the current frame -> misc dword 2 + parity (global fade detector, read by me1)
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %par = extractelement <4 x i32> %cn, i32 0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %sr = lshr i32 %tid, 3
  %scl = and i32 %tid, 7
  %sy = add i32 %Y, %sr
  %sx = add i32 %X, %scl
  %syok = icmp ult i32 %sy, 135
  %srow = mul i32 %sy, 240
  %soff = add i32 %srow, %sx
  %sb = call i8 @llvm.amdgcn.raw.buffer.load.i8(<4 x i32> %vc, i32 %soff, i32 0, i32 0)
  %sv0 = zext i8 %sb to i32
  %sv = select i1 %syok, i32 %sv0, i32 0
  %psv = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %tid
  store i32 %sv, ptr addrspace(3) %psv, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ; sum of the 64 lanes' %sv: ds_swizzle butterfly (xor 1 / 2 / 4 / 8 / 16) and the two wave halves; integer adds
  ; in any order give the bits of lane 0's former serial loop over @vecs
  %ss1 = call i32 @llvm.amdgcn.ds.swizzle(i32 %sv, i32 1055)
  %sa1 = add i32 %sv, %ss1
  %ss2 = call i32 @llvm.amdgcn.ds.swizzle(i32 %sa1, i32 2079)
  %sa2 = add i32 %sa1, %ss2
  %ss4 = call i32 @llvm.amdgcn.ds.swizzle(i32 %sa2, i32 4127)
  %sa4 = add i32 %sa2, %ss4
  %ss8 = call i32 @llvm.amdgcn.ds.swizzle(i32 %sa4, i32 8223)
  %sa8 = add i32 %sa4, %ss8
  %ss16 = call i32 @llvm.amdgcn.ds.swizzle(i32 %sa8, i32 16415)
  %sa16 = add i32 %sa8, %ss16
  %sh0 = call i32 @llvm.amdgcn.readlane.i32(i32 %sa16, i32 0)
  %sh1 = call i32 @llvm.amdgcn.readlane.i32(i32 %sa16, i32 32)
  %sacc1 = add i32 %sh0, %sh1
  %is0 = icmp eq i32 %tid, 0
  br i1 %is0, label %st, label %exit
st:
  %moff0 = shl i32 %par, 2
  %moff = add i32 %moff0, 8
  %old = call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 %sacc1, <4 x i32> %vm, i32 %moff, i32 0, i32 0)
  %pv = call i32 @pack(i32 %vx, i32 %vy)
  %orow = mul i32 %gy, 30
  %oidx = add i32 %orow, %gx
  %ooff = shl i32 %oidx, 2
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pv, <4 x i32> %v3, i32 %ooff, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

; key of candidate k of the 29x15 window around (cx, cy): ((SAD + 256 + LAM3 * |o|) << 9) | k
define internal i32 @costf(<4 x i32> %vp, <4 x i32> %vc, i32 %X, i32 %Y, i32 %cx, i32 %cy, i32 %k) alwaysinline {
  %kc = call i32 @llvm.umin.i32(i32 %k, i32 434)
  %oxu = urem i32 %kc, 29
  %oyu = udiv i32 %kc, 29
  %ox = sub i32 %oxu, 14
  %oy = sub i32 %oyu, 7
  %dx = add i32 %cx, %ox
  %dy = add i32 %cy, %oy
  %s = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 240, i32 135, i32 %X, i32 %Y, i32 %dx, i32 %dy)
  %ax = call i32 @llvm.abs.i32(i32 %ox, i1 false)
  %ay = call i32 @llvm.abs.i32(i32 %oy, i1 false)
  %a = add i32 %ax, %ay
  %pen = shl i32 %a, 2
  %c0 = add i32 %s, 256
  %c = add i32 %c0, %pen
  %kh = shl i32 %c, 9
  %key = or i32 %kh, %kc
  ret i32 %key
}

; 5 waves per SIMD (<= 48 VGPRs) instead of 4: register allocation and scheduling only, same machine IR before scheduling (tools/tests/test_motion_wave_reduce.py)
attributes #0 = { "amdgpu-flat-work-group-size"="64,64" "denormal-fp-math-f32"="preserve-sign,preserve-sign" "amdgpu-waves-per-eu"="5,10" "amdgpu-sched-strategy"="iterative-maxocc" }
!0 = !{}
