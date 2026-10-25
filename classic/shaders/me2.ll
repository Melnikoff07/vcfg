; user_sgprs=2
; tgid_y=1
; mode 8.2, pass 3: medium motion on L2 (480x270), 60 x 34 blocks of 8x8 (= 32x32 full-res px), CUR grid
; (unidirectional SAD: cur block at X, prev block at X + d).
; Four blocks in a row per 64-lane group: subgroup j = tid >> 4 (0..3) handles bx = 4*gx + j, lane slot = tid & 15.
;  phase 1: 14 candidates (slots 0..3 the 4 surrounding L3 vectors x2, slot 4 temporal, slot 5 zero,
;           slots 6..9 symmetric temporal +-2, slots 10..13 outer L3 ring),
;           cost = SAD + LAM2 * |c - med(L3x2)| (x2 when OOB) - TBIAS (slots 4,6..9) + 8 (slots 10..13)
;  phase 2: 5x5 (+-2) around the winner, penalty LAM2 * |offset|
; Writes V2[by, bx] (packed (dx, dy)) and S2[by, bx] = bsad | (zsad << 16) (winner SAD and zero-vector SAD, for gm8 and fgs8).
; table: +0xA0 V#L2cur, +0xB0 V#L2prev, +0xE0 V#V3 in, +0xF0 V#V2 out, +0x110 V#Fprev, +0x140 V#S2 out
target triple = "amdgcn--"
declare <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32>, i32, i32, i32 immarg)
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

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 160
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 176
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %p3 = getelementptr i8, ptr addrspace(4) %tbl, i64 224
  %v3 = load <4 x i32>, ptr addrspace(4) %p3, align 16, !invariant.load !0
  %p2 = getelementptr i8, ptr addrspace(4) %tbl, i64 240
  %v2 = load <4 x i32>, ptr addrspace(4) %p2, align 16, !invariant.load !0
  %pt = getelementptr i8, ptr addrspace(4) %tbl, i64 272
  %vt = load <4 x i32>, ptr addrspace(4) %pt, align 16, !invariant.load !0
  %ps2 = getelementptr i8, ptr addrspace(4) %tbl, i64 320
  %vs2 = load <4 x i32>, ptr addrspace(4) %ps2, align 16, !invariant.load !0
  %j = lshr i32 %tid, 4
  %sub = and i32 %tid, 15
  %base = and i32 %tid, 48
  %gx4 = shl i32 %gx, 2
  %bx = add i32 %gx4, %j
  %X = shl i32 %bx, 3
  %Y = shl i32 %gy, 3
  ; ---- the 4 L3 blocks around this block centre (symmetric border clamp [0,28]x[0,15])
  %bx4 = shl i32 %bx, 2
  %fxm = sub i32 %bx4, 2
  %ix0r = ashr i32 %fxm, 3
  %ix0a = call i32 @llvm.smax.i32(i32 %ix0r, i32 0)
  %ix0 = call i32 @llvm.smin.i32(i32 %ix0a, i32 28)
  %ix1 = add i32 %ix0, 1
  %by4 = shl i32 %gy, 2
  %fym = sub i32 %by4, 2
  %iy0r = ashr i32 %fym, 3
  %iy0a = call i32 @llvm.smax.i32(i32 %iy0r, i32 0)
  %iy0 = call i32 @llvm.smin.i32(i32 %iy0a, i32 15)
  %iy1 = add i32 %iy0, 1
  %s1 = and i32 %sub, 1
  %s1b = icmp ne i32 %s1, 0
  %nx_in = select i1 %s1b, i32 %ix1, i32 %ix0
  %s2 = and i32 %sub, 2
  %s2b = icmp ne i32 %s2, 0
  %ny_in = select i1 %s2b, i32 %iy1, i32 %iy0
  %ix0m = call i32 @llvm.smax.i32(i32 %ix0r, i32 1)
  %ix0_out = sub i32 %ix0m, 1
  %ix1p = add i32 %ix1, 1
  %ix1_out = call i32 @llvm.smin.i32(i32 %ix1p, i32 29)
  %iy0m = call i32 @llvm.smax.i32(i32 %iy0r, i32 1)
  %iy0_out = sub i32 %iy0m, 1
  %iy1p = add i32 %iy1, 1
  %iy1_out = call i32 @llvm.smin.i32(i32 %iy1p, i32 16)
  %is10 = icmp eq i32 %sub, 10
  %is11 = icmp eq i32 %sub, 11
  %is12 = icmp eq i32 %sub, 12
  %is13 = icmp eq i32 %sub, 13
  %isout = icmp uge i32 %sub, 10
  %ox0 = select i1 %is10, i32 %ix0_out, i32 %ix0
  %ox1 = select i1 %is11, i32 %ix1_out, i32 %ox0
  %oy0 = select i1 %is12, i32 %iy0_out, i32 %iy0
  %oy1 = select i1 %is13, i32 %iy1_out, i32 %oy0
  %nx = select i1 %isout, i32 %ox1, i32 %nx_in
  %ny = select i1 %isout, i32 %oy1, i32 %ny_in
  %nrow = mul i32 %ny, 30
  %nidx = add i32 %nrow, %nx
  %noff = shl i32 %nidx, 2
  %nv = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v3, i32 %noff, i32 0, i32 0)
  %nvx = call i32 @unx(i32 %nv)
  %nvy = call i32 @uny(i32 %nv)
  %ndx = shl i32 %nvx, 1
  %ndy = shl i32 %nvy, 1
  ; ---- median of the 4 L3 candidates (each lane loads all 4: cached, no LDS round trip)
  %o00r = mul i32 %iy0, 30
  %o00i = add i32 %o00r, %ix0
  %o00 = shl i32 %o00i, 2
  %o10i = add i32 %o00r, %ix1
  %o10 = shl i32 %o10i, 2
  %o01r = mul i32 %iy1, 30
  %o01i = add i32 %o01r, %ix0
  %o01 = shl i32 %o01i, 2
  %o11i = add i32 %o01r, %ix1
  %o11 = shl i32 %o11i, 2
  %m00 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v3, i32 %o00, i32 0, i32 0)
  %m10 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v3, i32 %o10, i32 0, i32 0)
  %m01 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v3, i32 %o01, i32 0, i32 0)
  %m11 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v3, i32 %o11, i32 0, i32 0)
  %m00x = call i32 @unx(i32 %m00)
  %m10x = call i32 @unx(i32 %m10)
  %m01x = call i32 @unx(i32 %m01)
  %m11x = call i32 @unx(i32 %m11)
  %m00y = call i32 @uny(i32 %m00)
  %m10y = call i32 @uny(i32 %m10)
  %m01y = call i32 @uny(i32 %m01)
  %m11y = call i32 @uny(i32 %m11)
  %medx1 = call i32 @med4(i32 %m00x, i32 %m10x, i32 %m01x, i32 %m11x)
  %medy1 = call i32 @med4(i32 %m00y, i32 %m10y, i32 %m01y, i32 %m11y)
  %medx = shl i32 %medx1, 1
  %medy = shl i32 %medy1, 1
  ; ---- temporal predictor: previous field at L1 block (2bx+1, 2by+1) and +-2 offsets, full-res px -> L2 px
  %tbx0 = shl i32 %bx, 1
  %tbx1 = or i32 %tbx0, 1
  %is6 = icmp eq i32 %sub, 6
  %is7 = icmp eq i32 %sub, 7
  %is8 = icmp eq i32 %sub, 8
  %is9 = icmp eq i32 %sub, 9
  %txo0 = select i1 %is6, i32 2, i32 0
  %txo1 = select i1 %is7, i32 -2, i32 %txo0
  %tyo0 = select i1 %is8, i32 2, i32 0
  %tyo1 = select i1 %is9, i32 -2, i32 %tyo0
  %tbx2 = add i32 %tbx1, %txo1
  %tbx3 = call i32 @llvm.smax.i32(i32 %tbx2, i32 0)
  %tbx = call i32 @llvm.smin.i32(i32 %tbx3, i32 119)
  %tby0 = shl i32 %gy, 1
  %tby1 = or i32 %tby0, 1
  %tby2 = add i32 %tby1, %tyo1
  %tby3 = call i32 @llvm.smax.i32(i32 %tby2, i32 0)
  %tby = call i32 @llvm.smin.i32(i32 %tby3, i32 67)
  %trow = mul i32 %tby, 120
  %tidx = add i32 %trow, %tbx
  %toff = shl i32 %tidx, 3
  %tv = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vt, i32 %toff, i32 0, i32 0)
  %tvx = extractelement <2 x float> %tv, i32 0
  %tvy = extractelement <2 x float> %tv, i32 1
  %tvxq = fmul float %tvx, 0.25
  %tvyq = fmul float %tvx, 0.0
  %tvyq2 = fmul float %tvy, 0.25
  %tdx = call i32 @rnd(float %tvxq)
  %tdy = call i32 @rnd(float %tvyq2)
  ; ---- phase 1: slots 0..3 neighbours, 4 temporal, 5 zero, 6..9 temporal +-2, 10..13 outer L3
  %islt4 = icmp ult i32 %sub, 4
  %isnb = or i1 %islt4, %isout
  %ist0 = icmp eq i32 %sub, 4
  %ist69a = icmp uge i32 %sub, 6
  %ist69b = icmp ule i32 %sub, 9
  %ist69 = and i1 %ist69a, %ist69b
  %ist = or i1 %ist0, %ist69
  %cx0 = select i1 %ist, i32 %tdx, i32 0
  %cy0 = select i1 %ist, i32 %tdy, i32 0
  %cdx = select i1 %isnb, i32 %ndx, i32 %cx0
  %cdy = select i1 %isnb, i32 %ndy, i32 %cy0
  %sad1 = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 480, i32 270, i32 %X, i32 %Y, i32 %cdx, i32 %cdy)
  %bias0 = select i1 %ist, i32 8, i32 0
  %bias = select i1 %isout, i32 -8, i32 %bias0
  %mdx = sub i32 %cdx, %medx
  %mdy = sub i32 %cdy, %medy
  %amdx = call i32 @llvm.abs.i32(i32 %mdx, i1 false)
  %amdy = call i32 @llvm.abs.i32(i32 %mdy, i1 false)
  %amd = add i32 %amdx, %amdy
  %xp0_m = add i32 %X, %cdx
  %yp0_m = add i32 %Y, %cdy
  %oobx_m = icmp ugt i32 %xp0_m, 472
  %ooby_m = icmp ugt i32 %yp0_m, 264
  %oob_m = or i1 %oobx_m, %ooby_m
  %msh = select i1 %oob_m, i32 3, i32 2
  %mpen = shl i32 %amd, %msh
  %c1a0 = add i32 %sad1, 256
  %c1a = add i32 %c1a0, %mpen
  %cost1 = sub i32 %c1a, %bias
  %k1a = shl i32 %cost1, 8
  %k1b = or i32 %k1a, %sub
  %val1 = icmp ult i32 %sub, 14
  %key1 = select i1 %val1, i32 %k1b, i32 -1
  %m1 = call i32 @reduce(i32 %tid, i32 %key1, i32 %base, i32 16)
  %w1 = and i32 %m1, 255
  %zi = add i32 %base, 5
  %pz = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %zi
  %zk = load i32, ptr addrspace(3) %pz, align 4
  %zk8 = lshr i32 %zk, 8
  ; raw SAD of the zero vector for S2 (the key also holds the median penalty of (0, 0))
  %amzx = call i32 @llvm.abs.i32(i32 %medx, i1 false)
  %amzy = call i32 @llvm.abs.i32(i32 %medy, i1 false)
  %amz = add i32 %amzx, %amzy
  %zpen = shl i32 %amz, 2
  %zsad0 = sub i32 %zk8, 256
  %zsad = sub i32 %zsad0, %zpen
  %cpk = call i32 @pack(i32 %cdx, i32 %cdy)
  %src1 = add i32 %base, %w1
  %c1 = call i32 @bcast(i32 %tid, i32 %cpk, i32 %src1)
  %c1x = call i32 @unx(i32 %c1)
  %c1y = call i32 @uny(i32 %c1)
  ; ---- phase 2: +-2 (k = 0..24, raster order), lane slot handles k = slot and k = slot + 16
  %ka = call i32 @cost5(<4 x i32> %vp, <4 x i32> %vc, i32 %X, i32 %Y, i32 %c1x, i32 %c1y, i32 %sub)
  %kb_k = add i32 %sub, 16
  %kb0 = call i32 @cost5(<4 x i32> %vp, <4 x i32> %vc, i32 %X, i32 %Y, i32 %c1x, i32 %c1y, i32 %kb_k)
  %vb = icmp ult i32 %kb_k, 25
  %kb = select i1 %vb, i32 %kb0, i32 -1
  %kk = call i32 @llvm.umin.i32(i32 %ka, i32 %kb)
  %m2 = call i32 @reduce(i32 %tid, i32 %kk, i32 %base, i32 16)
  %w2 = and i32 %m2, 255
  %wxu = urem i32 %w2, 5
  %wyu = udiv i32 %w2, 5
  %vx0 = add i32 %c1x, %wxu
  %vx = sub i32 %vx0, 2
  %vy0 = add i32 %c1y, %wyu
  %vy = sub i32 %vy0, 2
  ; SAD of the winner = key cost - 256 - penalty
  %m2c = lshr i32 %m2, 8
  %wox = sub i32 %wxu, 2
  %woy = sub i32 %wyu, 2
  %awx = call i32 @llvm.abs.i32(i32 %wox, i1 false)
  %awy = call i32 @llvm.abs.i32(i32 %woy, i1 false)
  %awo = add i32 %awx, %awy
  %wpen = shl i32 %awo, 2
  %bs0 = sub i32 %m2c, 256
  %bsad = sub i32 %bs0, %wpen
  %is0 = icmp eq i32 %sub, 0
  br i1 %is0, label %st, label %exit
st:
  %pv = call i32 @pack(i32 %vx, i32 %vy)
  %orow = mul i32 %gy, 60
  %oidx = add i32 %orow, %bx
  %ooff = shl i32 %oidx, 2
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pv, <4 x i32> %v2, i32 %ooff, i32 0, i32 0)
  %zsh = shl i32 %zsad, 16
  %s2v = or i32 %bsad, %zsh
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %s2v, <4 x i32> %vs2, i32 %ooff, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

; key of candidate k of a 5x5 window around (cx, cy): ((SAD + 256 + LAM2 * |o|) << 8) | k
define internal i32 @cost5(<4 x i32> %vp, <4 x i32> %vc, i32 %X, i32 %Y, i32 %cx, i32 %cy, i32 %k) alwaysinline {
  %kc = call i32 @llvm.umin.i32(i32 %k, i32 24)
  %oxu = urem i32 %kc, 5
  %oyu = udiv i32 %kc, 5
  %ox = sub i32 %oxu, 2
  %oy = sub i32 %oyu, 2
  %dx = add i32 %cx, %ox
  %dy = add i32 %cy, %oy
  %s = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 480, i32 270, i32 %X, i32 %Y, i32 %dx, i32 %dy)
  %ax = call i32 @llvm.abs.i32(i32 %ox, i1 false)
  %ay = call i32 @llvm.abs.i32(i32 %oy, i1 false)
  %a = add i32 %ax, %ay
  %pen = shl i32 %a, 2
  %c0 = add i32 %s, 256
  %c = add i32 %c0, %pen
  %kh = shl i32 %c, 8
  %key = or i32 %kh, %kc
  ret i32 %key
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}

; lower-middle of 4 values: min(max(min(a,b), min(c,d)), min(max(a,b), max(c,d))) (a value between the 2nd and 3rd)
define internal i32 @med4(i32 %a, i32 %b, i32 %c, i32 %d) alwaysinline {
  %lo1 = call i32 @llvm.smin.i32(i32 %a, i32 %b)
  %hi1 = call i32 @llvm.smax.i32(i32 %a, i32 %b)
  %lo2 = call i32 @llvm.smin.i32(i32 %c, i32 %d)
  %hi2 = call i32 @llvm.smax.i32(i32 %c, i32 %d)
  %l = call i32 @llvm.smax.i32(i32 %lo1, i32 %lo2)
  %h = call i32 @llvm.smin.i32(i32 %hi1, i32 %hi2)
  %m = call i32 @llvm.smin.i32(i32 %l, i32 %h)
  ret i32 %m
}
