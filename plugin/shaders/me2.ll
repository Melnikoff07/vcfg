; user_sgprs=2
; tgid_y=1
; mode 8.4, pass 3: motion field on L2 (480x270), 60 x 34 blocks of 8x8 (= 32x32 full-res px), CUR grid
; (unidirectional SAD as me3). dispatch 15 x 34 groups; lane = block (tid>>4) * 16 + slot (tid&15).
;  phase 1: 4 nearest L3 vectors (x2) + temporal predictor (-TBIAS/2) + zero
;           8.4: every candidate pays MPEN (4) per L2 px of distance to the component median of the 4 L3 vectors (x2).
;           A wider me3 window finds more aliases on periodic texture (fence bars, repeated glyphs): one aliased L3
;           block must not drag the L2 blocks around it (idea of the other model in round 5, here as a median).
;  phase 2: +-2 around the winner (25 candidates in 2 rounds), penalty LAM2 per px
; Also stores per block S2 = SAD(best) | SAD(zero) << 16 (gm8 / fgs8 use them to tell a real match from noise).
; table: +0xA0 V#L2cur, +0xB0 V#L2prev, +0xE0 V#V3 (30x17 packed), +0xF0 V#V2 out, +0x110 V#Fprev, +0x140 V#S2 out
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
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

@keys = internal addrspace(3) global [64 x i32] undef, align 16
@vecs = internal addrspace(3) global [64 x i32] undef, align 16

; Bilateral SAD of an 8x8 block of two packed-u8 luma images (wl bytes per row, wl % 4 == 0, hl rows).
; Candidate d = (dx, dy): prev block at X + ceil(d/2), cur block at X - floor(d/2) (difference exactly d, so
; cur(x) ~ prev(x + d) as in the old code). The x start is clamped to [0, wl-8] (8 contiguous bytes per row),
; rows are clamped one by one. Each row: 3 dwords per image, 2 v_alignbyte, 2 v_sad_u8 (unmasked SAD of 4 bytes).
; The 8 rows are written out (llc does not unroll loops): all 16 loads are in flight at once.
define internal i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy) alwaysinline {
entry:
  %ndx = sub i32 0, %dx
  %ndy = sub i32 0, %dy
  %fpx = ashr i32 %ndx, 1
  %fpy = ashr i32 %ndy, 1
  %opx = sub i32 0, %fpx
  %opy = sub i32 0, %fpy
  %fcx = ashr i32 %dx, 1
  %fcy = ashr i32 %dy, 1
  %wm8 = sub i32 %wl, 8
  %hm1 = sub i32 %hl, 1
  %xp0 = add i32 %X, %opx
  %xp1 = call i32 @llvm.smax.i32(i32 %xp0, i32 0)
  %xp = call i32 @llvm.smin.i32(i32 %xp1, i32 %wm8)
  %xc0 = sub i32 %X, %fcx
  %xc1 = call i32 @llvm.smax.i32(i32 %xc0, i32 0)
  %xc = call i32 @llvm.smin.i32(i32 %xc1, i32 %wm8)
  %shp = and i32 %xp, 3
  %shc = and i32 %xc, 3
  %axp = and i32 %xp, -4
  %axc = and i32 %xc, -4
  %ybp = add i32 %Y, %opy
  %ybc = sub i32 %Y, %fcy
  %ypr0 = add i32 %ybp, 0
  %ypa0 = call i32 @llvm.smax.i32(i32 %ypr0, i32 0)
  %yp0 = call i32 @llvm.smin.i32(i32 %ypa0, i32 %hm1)
  %ycr0 = add i32 %ybc, 0
  %yca0 = call i32 @llvm.smax.i32(i32 %ycr0, i32 0)
  %yc0 = call i32 @llvm.smin.i32(i32 %yca0, i32 %hm1)
  %rp0 = mul i32 %yp0, %wl
  %ap0 = add i32 %rp0, %axp
  %rc0 = mul i32 %yc0, %wl
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
  %ypr1 = add i32 %ybp, 1
  %ypa1 = call i32 @llvm.smax.i32(i32 %ypr1, i32 0)
  %yp1 = call i32 @llvm.smin.i32(i32 %ypa1, i32 %hm1)
  %ycr1 = add i32 %ybc, 1
  %yca1 = call i32 @llvm.smax.i32(i32 %ycr1, i32 0)
  %yc1 = call i32 @llvm.smin.i32(i32 %yca1, i32 %hm1)
  %rp1 = mul i32 %yp1, %wl
  %ap1 = add i32 %rp1, %axp
  %rc1 = mul i32 %yc1, %wl
  %ac1 = add i32 %rc1, %axc
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
  %ypr2 = add i32 %ybp, 2
  %ypa2 = call i32 @llvm.smax.i32(i32 %ypr2, i32 0)
  %yp2 = call i32 @llvm.smin.i32(i32 %ypa2, i32 %hm1)
  %ycr2 = add i32 %ybc, 2
  %yca2 = call i32 @llvm.smax.i32(i32 %ycr2, i32 0)
  %yc2 = call i32 @llvm.smin.i32(i32 %yca2, i32 %hm1)
  %rp2 = mul i32 %yp2, %wl
  %ap2 = add i32 %rp2, %axp
  %rc2 = mul i32 %yc2, %wl
  %ac2 = add i32 %rc2, %axc
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
  %ypr3 = add i32 %ybp, 3
  %ypa3 = call i32 @llvm.smax.i32(i32 %ypr3, i32 0)
  %yp3 = call i32 @llvm.smin.i32(i32 %ypa3, i32 %hm1)
  %ycr3 = add i32 %ybc, 3
  %yca3 = call i32 @llvm.smax.i32(i32 %ycr3, i32 0)
  %yc3 = call i32 @llvm.smin.i32(i32 %yca3, i32 %hm1)
  %rp3 = mul i32 %yp3, %wl
  %ap3 = add i32 %rp3, %axp
  %rc3 = mul i32 %yc3, %wl
  %ac3 = add i32 %rc3, %axc
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
  %ypr4 = add i32 %ybp, 4
  %ypa4 = call i32 @llvm.smax.i32(i32 %ypr4, i32 0)
  %yp4 = call i32 @llvm.smin.i32(i32 %ypa4, i32 %hm1)
  %ycr4 = add i32 %ybc, 4
  %yca4 = call i32 @llvm.smax.i32(i32 %ycr4, i32 0)
  %yc4 = call i32 @llvm.smin.i32(i32 %yca4, i32 %hm1)
  %rp4 = mul i32 %yp4, %wl
  %ap4 = add i32 %rp4, %axp
  %rc4 = mul i32 %yc4, %wl
  %ac4 = add i32 %rc4, %axc
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
  %ypr5 = add i32 %ybp, 5
  %ypa5 = call i32 @llvm.smax.i32(i32 %ypr5, i32 0)
  %yp5 = call i32 @llvm.smin.i32(i32 %ypa5, i32 %hm1)
  %ycr5 = add i32 %ybc, 5
  %yca5 = call i32 @llvm.smax.i32(i32 %ycr5, i32 0)
  %yc5 = call i32 @llvm.smin.i32(i32 %yca5, i32 %hm1)
  %rp5 = mul i32 %yp5, %wl
  %ap5 = add i32 %rp5, %axp
  %rc5 = mul i32 %yc5, %wl
  %ac5 = add i32 %rc5, %axc
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
  %ypr6 = add i32 %ybp, 6
  %ypa6 = call i32 @llvm.smax.i32(i32 %ypr6, i32 0)
  %yp6 = call i32 @llvm.smin.i32(i32 %ypa6, i32 %hm1)
  %ycr6 = add i32 %ybc, 6
  %yca6 = call i32 @llvm.smax.i32(i32 %ycr6, i32 0)
  %yc6 = call i32 @llvm.smin.i32(i32 %yca6, i32 %hm1)
  %rp6 = mul i32 %yp6, %wl
  %ap6 = add i32 %rp6, %axp
  %rc6 = mul i32 %yc6, %wl
  %ac6 = add i32 %rc6, %axc
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
  %ypr7 = add i32 %ybp, 7
  %ypa7 = call i32 @llvm.smax.i32(i32 %ypr7, i32 0)
  %yp7 = call i32 @llvm.smin.i32(i32 %ypa7, i32 %hm1)
  %ycr7 = add i32 %ybc, 7
  %yca7 = call i32 @llvm.smax.i32(i32 %ycr7, i32 0)
  %yc7 = call i32 @llvm.smin.i32(i32 %yca7, i32 %hm1)
  %rp7 = mul i32 %yp7, %wl
  %ap7 = add i32 %rp7, %axp
  %rc7 = mul i32 %yc7, %wl
  %ac7 = add i32 %rc7, %axc
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
  ret i32 %sb7
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
  ; ---- the 4 L3 blocks around this block centre
  %bx4 = shl i32 %bx, 2
  %fxm = sub i32 %bx4, 2
  %ix0r = ashr i32 %fxm, 3
  %ix0a = call i32 @llvm.smax.i32(i32 %ix0r, i32 0)
  %ix0 = call i32 @llvm.smin.i32(i32 %ix0a, i32 29)
  %ix1a = add i32 %ix0, 1
  %ix1 = call i32 @llvm.smin.i32(i32 %ix1a, i32 29)
  %by4 = shl i32 %gy, 2
  %fym = sub i32 %by4, 2
  %iy0r = ashr i32 %fym, 3
  %iy0a = call i32 @llvm.smax.i32(i32 %iy0r, i32 0)
  %iy0 = call i32 @llvm.smin.i32(i32 %iy0a, i32 16)
  %iy1a = add i32 %iy0, 1
  %iy1 = call i32 @llvm.smin.i32(i32 %iy1a, i32 16)
  %s1 = and i32 %sub, 1
  %s1b = icmp ne i32 %s1, 0
  %nx = select i1 %s1b, i32 %ix1, i32 %ix0
  %s2 = and i32 %sub, 2
  %s2b = icmp ne i32 %s2, 0
  %ny = select i1 %s2b, i32 %iy1, i32 %iy0
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
  ; ---- temporal predictor: previous field at L1 block (2bx+1, 2by+1), full-res px -> L2 px
  %tbx0 = shl i32 %bx, 1
  %tbx1 = or i32 %tbx0, 1
  %tbx = call i32 @llvm.smin.i32(i32 %tbx1, i32 119)
  %tby0 = shl i32 %gy, 1
  %tby1 = or i32 %tby0, 1
  %tby = call i32 @llvm.smin.i32(i32 %tby1, i32 67)
  %trow = mul i32 %tby, 120
  %tidx = add i32 %trow, %tbx
  %toff = shl i32 %tidx, 3
  %tv = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vt, i32 %toff, i32 0, i32 0)
  %tvx = extractelement <2 x float> %tv, i32 0
  %tvy = extractelement <2 x float> %tv, i32 1
  %tvxq = fmul float %tvx, 0.25
  %tvyq = fmul float %tvy, 0.25
  %tdx = call i32 @rnd(float %tvxq)
  %tdy = call i32 @rnd(float %tvyq)
  ; ---- phase 1: slots 0..3 neighbours, 4 temporal, 5 zero
  %islt4 = icmp ult i32 %sub, 4
  %ist = icmp eq i32 %sub, 4
  %cx0 = select i1 %ist, i32 %tdx, i32 0
  %cy0 = select i1 %ist, i32 %tdy, i32 0
  %cdx = select i1 %islt4, i32 %ndx, i32 %cx0
  %cdy = select i1 %islt4, i32 %ndy, i32 %cy0
  %sad1 = call i32 @bsadu(<4 x i32> %vp, <4 x i32> %vc, i32 480, i32 270, i32 %X, i32 %Y, i32 %cdx, i32 %cdy)
  %bias = select i1 %ist, i32 8, i32 0
  %mdx = sub i32 %cdx, %medx
  %mdy = sub i32 %cdy, %medy
  %amdx = call i32 @llvm.abs.i32(i32 %mdx, i1 false)
  %amdy = call i32 @llvm.abs.i32(i32 %mdy, i1 false)
  %amd = add i32 %amdx, %amdy
  %mpen = shl i32 %amd, 2
  %c1a0 = add i32 %sad1, 256
  %c1a = add i32 %c1a0, %mpen
  %cost1 = sub i32 %c1a, %bias
  %k1a = shl i32 %cost1, 8
  %k1b = or i32 %k1a, %sub
  %val1 = icmp ult i32 %sub, 6
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
