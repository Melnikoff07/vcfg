; user_sgprs=2
; tgid_y=1
; mode 8.4, pass 7 (after me1): vector field filter on the L1 block grid (120 x 68 blocks of 16x16 full-res px).
; One ICM step of a robust smoothness prior, the "periodic structure / outlier" cleanup TV MEMC chips do:
;   candidates per block: slot 0 its own vector, slots 1..8 the vectors of its 8 neighbours, slot 9 the camera
;   vector (gm8, misc dwords 4, 5, L2 px x 4); cost = bilateral L1 SAD at the candidate (rounded to L1 px, as me1)
;   + LAM * sum over the 8 neighbours of min(|d - d_n|_1, CAP) (L1 px); slot 0 gets -FGBIAS where fgs8 splatted a
;   foreground vector (the swinging weapon keeps its vector). Ties -> own vector.
; Why: on a periodic structure (the iron fence, bars every ~7 px) a vector shifted by one or two periods matches as
;   well as the true one, and on L2/L3 the bars are below Nyquist (moire), so the coarse levels hand me1 alias vectors.
;   The SAD cannot tell them apart; the neighbours can. Same for isolated blocks that matched dark-on-dark at a wrong
;   vector during a fast turn (the dark specks in the flowers). A real object keeps its vector: there the other
;   candidates' SAD is far worse than the smoothness penalty (at most LAM * 8 * CAP = 128).
; A plain 3x3 vector median (round 2: -0.9 dB, kills small objects) has no cost check; this filter has.
; Reads the raw me1 field FT (+0x180), writes the final field F (+0x100): the warp and next frame's temporal
; predictors use the filtered field. Fade frames (misc dword 7 = 1, field is all zero on purpose): copy.
; 8.5: in anchor cells (GK key < 1 << 20, see fgs8 / me1) every candidate farther than 3 L1 px from the anchor vector
; (V2 of the anchor block x 2) pays 512: the filter's data term is the same bilateral SAD that cannot see the static
; object, and its smoothness term spread the sky's 'escape' vector into the head (model: 128 -> 1921 split pixels).
; table: +0x80 V#L1cur, +0x90 V#L1prev, +0xF0 V#V2, +0x100 V#F out, +0x120 V#misc, +0x150 V#GK, +0x180 V#FT in
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


; full-res float vector of block (bx + ox, by + oy) of FT, coordinates clamped (edge blocks see themselves)
define internal <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %ox, i32 %oy) alwaysinline {
  %x0 = add i32 %bx, %ox
  %x1 = call i32 @llvm.smax.i32(i32 %x0, i32 0)
  %x = call i32 @llvm.smin.i32(i32 %x1, i32 119)
  %y0 = add i32 %by, %oy
  %y1 = call i32 @llvm.smax.i32(i32 %y0, i32 0)
  %y = call i32 @llvm.smin.i32(i32 %y1, i32 67)
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
  ; ---- candidate of this lane: slot 0 own, 1..8 neighbour k (raster 3x3 without the centre), 9 camera
  %k0 = sub i32 %sub, 1
  %ge5 = icmp uge i32 %sub, 5
  %k1 = select i1 %ge5, i32 %sub, i32 %k0
  %isn = icmp ult i32 %k0, 8
  %k = select i1 %isn, i32 %k1, i32 4
  %kxu = urem i32 %k, 3
  %kyu = udiv i32 %k, 3
  %kx = sub i32 %kxu, 1
  %ky = sub i32 %kyu, 1
  %cv = call <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %kx, i32 %ky)
  %gmx = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 16, i32 0, i32 0)
  %gmy = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 20, i32 0, i32 0)
  %gmx4 = shl i32 %gmx, 2
  %gmy4 = shl i32 %gmy, 2
  %gfx = sitofp i32 %gmx4 to float
  %gfy = sitofp i32 %gmy4 to float
  %is9a = icmp eq i32 %sub, 9
  %is9 = and i1 %is9a, %isfgk
  %cvx0 = extractelement <2 x float> %cv, i32 0
  %cvy0 = extractelement <2 x float> %cv, i32 1
  %cvx = select i1 %is9, float %gfx, float %cvx0
  %cvy = select i1 %is9, float %gfy, float %cvy0
  %hx = fmul float %cvx, 0.5
  %hy = fmul float %cvy, 0.5
  %cx = call i32 @rnd(float %hx)
  %cy = call i32 @rnd(float %hy)
  %hcx = ashr i32 %cx, 1
  %hcy = ashr i32 %cy, 1
  %scx = select i1 %nfgk, i32 %hcx, i32 0
  %scy = select i1 %nfgk, i32 %hcy, i32 0
  %Xu = add i32 %X, %scx
  %Yu = add i32 %Y, %scy
  %sad = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %Xu, i32 %Yu, i32 %cx, i32 %cy)
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
  ; foreground bonus for the own vector
  %is0 = icmp eq i32 %sub, 0
  %fgo = and i1 %is0, %hasfg
  %bias = select i1 %fgo, i32 96, i32 0
  ; 8.5 anchor penalty
  %isanc = and i1 %hasfg, %nfgk
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
  %afar = icmp sgt i32 %aad, 3
  %ap = and i1 %isanc, %afar
  %apen = select i1 %ap, i32 512, i32 0
  %c0 = add i32 %sad, 256
  %c1 = add i32 %c0, %pen
  %c1b = add i32 %c1, %apen
  %cost = sub i32 %c1b, %bias
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
  br i1 %is0, label %st, label %exit
st:
  %fx = bitcast i32 %ox to float
  %fy = bitcast i32 %oy to float
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
