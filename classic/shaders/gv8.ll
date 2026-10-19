; user_sgprs=2
; tgid_y=1
; velocity subsystem, hybrid pass gv8 (after mf8, before the fence / phase8 / warp): per L1 block (120 x 68 blocks of
; 16x16 full-res px, MID grid) choose between our flow vector F and the game's vectors, verified by the same bilateral
; L1 SAD the flow search uses. Dispatch 30 x 68 groups; lane = block (tid >> 4) * 16 + slot (tid & 15), as mf8.
; The game buffer G is on the CUR grid (one value per cur pixel: where that pixel came from), our field on the MID
; grid. The cur pixel that lands on mid point m along vector d is m - d/2, so G is read at m - f/2 (f = our vector,
; this is also the raw value the calibrator sees) and once more at m - g/2 (fixed point with the decoded game vector).
; Decoding: g = a * raw + b per axis (table +0x1B0, from the calibrator), raw == sentinel = "not written" (objects-
; only buffers: there the flow vector stays, it already contains the camera motion and parallax).
; Candidates (slot: vector):  0 f  |  1 g (centre value at m - g/2)  |  2 outlier value of that cell (thin objects)
;   3..11 centre values of the 3x3 cells around it (4 px apart: dilation; with depth the nearest one gets BIAS_Z,
;   the TAA "closest depth" rule: at an edge between a near and a far layer the block takes the near layer's motion)
;   12, 13: texture of the block (self SAD at a 2 L1 px shift in x / y, for the calibration snapshot only)
; cost = SAD + 1024 - bias; bias: game candidates BIAS_G, nearest-depth BIAS_Z, game centre that agrees with f
; within 1 px +32 (sub-pixel exact, not with jitter); f: 0. Lowest cost wins, ties -> lower slot (f).
; Why a SAD check at all: the game buffer is wrong where the image is not geometry (particles, transparency,
; reflections, UI) and for a stale or misidentified buffer; there the flow vector matches clearly better and stays.
; Why the bias: the cases flow gets wrong are exactly those where the true vector has the worse block SAD (a dark head
; over the bright sky: the sky vector matches the bright 60 % of the block, the head vector only the head part).
; gflags (+0x1C0 dword 0): 1 G valid this frame, 2 apply (else shadow: statistics only, F unchanged), 4 depth valid,
;   8 reversed Z (near = larger), 32 write the calibration snapshot, 64 sentinel on, 128 jitter (no sub-pixel bonus)
; Statistics (+0x1E0 V#, CPU zeroes them per frame, atomic add): {blocks with a game sample, blocks where the game
;   vector agrees with f within 2 px, blocks where a game candidate won, -}
; Snapshot (+0x1D0 V#, 40 bytes per block, gvcal.h gv_rec_t): f, F prev, raw at m - f/2, SAD(f), texture, flags, z.
; table: +0x80 V#L1cur, +0x90 V#L1prev, +0x100 V#F (in/out), +0x110 V#F prev, +0x150 V#GK, +0x190 V#G, +0x1A0 V#Z,
;        +0x1B0 {ax, ay, bx, by}, +0x1C0 {gflags, sentinel x, sentinel y, BIAS_G | BIAS_Z << 16}, +0x1D0 V#snapshot,
;        +0x1E0 V#statistics
target triple = "amdgcn--"
declare <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare <4 x float> @llvm.amdgcn.raw.buffer.load.v4f32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v4i32(<4 x i32>, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2i32(<2 x i32>, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.alignbyte(i32, i32, i32)
declare i32 @llvm.amdgcn.sad.u8(i32, i32, i32)
declare i64 @llvm.amdgcn.ballot.i64(i1)
declare i64 @llvm.ctpop.i64(i64)
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



; cell index (480 x 270 grid of 4x4 px) of full-res point (qx, qy), clamped (NaN -> 0)
define internal i32 @cell(float %qx, float %qy) alwaysinline {
  %x0 = fmul float %qx, 0.25
  %y0 = fmul float %qy, 0.25
  %x1 = call float @llvm.maxnum.f32(float %x0, float 0.0)
  %y1 = call float @llvm.maxnum.f32(float %y0, float 0.0)
  %x2 = call float @llvm.minnum.f32(float %x1, float 479.0)
  %y2 = call float @llvm.minnum.f32(float %y1, float 269.0)
  %xi = fptosi float %x2 to i32
  %yi = fptosi float %y2 to i32
  %r = mul i32 %yi, 480
  %i = add i32 %r, %xi
  ret i32 %i
}
define internal <4 x float> @ldG(<4 x i32> %vg, i32 %c) alwaysinline {
  %o = shl i32 %c, 4
  %v = call <4 x float> @llvm.amdgcn.raw.buffer.load.v4f32(<4 x i32> %vg, i32 %o, i32 0, i32 0)
  ret <4 x float> %v
}
; nearest depth of a cell as an unsigned key (smaller = nearer): depth >= 0, so float bits order like the values;
; reversed Z (near = 1): invert the bits of max
define internal i32 @zkey(<4 x i32> %vz, i32 %c, i1 %rev) alwaysinline {
  %o = shl i32 %c, 3
  %z = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vz, i32 %o, i32 0, i32 0)
  %mn = extractelement <2 x float> %z, i32 0
  %mx = extractelement <2 x float> %z, i32 1
  %mnp = call float @llvm.maxnum.f32(float %mn, float 0.0)
  %mxp = call float @llvm.maxnum.f32(float %mx, float 0.0)
  %bn = bitcast float %mnp to i32
  %bx = bitcast float %mxp to i32
  %bxi = xor i32 %bx, -1
  %k = select i1 %rev, i32 %bxi, i32 %bn
  ret i32 %k
}
define internal float @dec(float %r, float %a, float %b) alwaysinline {
  %m = fmul float %r, %a
  %d = fadd float %m, %b
  ret float %d
}
; usable game sample: finite decoded vector within 256 px, not the sentinel
define internal i1 @gok(float %rx, float %ry, float %gx, float %gy, i1 %son, i32 %sx, i32 %sy) alwaysinline {
  %ax = call float @llvm.fabs.f32(float %gx)
  %ay = call float @llvm.fabs.f32(float %gy)
  %m = call float @llvm.maxnum.f32(float %ax, float %ay)
  %fin = fcmp olt float %m, 256.0
  %bx = bitcast float %rx to i32
  %by = bitcast float %ry to i32
  %ex = icmp eq i32 %bx, %sx
  %ey = icmp eq i32 %by, %sy
  %e = and i1 %ex, %ey
  %isn = and i1 %e, %son
  %nsn = xor i1 %isn, true
  %ok = and i1 %fin, %nsn
  ret i1 %ok
}
define internal float @dinf(float %ax, float %ay, float %bx, float %by) alwaysinline {
  %dx = fsub float %ax, %bx
  %dy = fsub float %ay, %by
  %x = call float @llvm.fabs.f32(float %dx)
  %y = call float @llvm.fabs.f32(float %dy)
  %m = call float @llvm.maxnum.f32(float %x, float %y)
  ret float %m
}
; number of lanes of this wave with %c set (one atomic per wave instead of one per block)
define internal void @count(i1 %c, i1 %lane0, <4 x i32> %vs, i32 %off) alwaysinline {
entry:
  %b = call i64 @llvm.amdgcn.ballot.i64(i1 %c)
  %n64 = call i64 @llvm.ctpop.i64(i64 %b)
  %n = trunc i64 %n64 to i32
  br i1 %lane0, label %add, label %done
add:
  %o = call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 %n, <4 x i32> %vs, i32 %off, i32 0, i32 0)
  br label %done
done:
  ret void
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 128
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 144
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %pf = getelementptr i8, ptr addrspace(4) %tbl, i64 256
  %vf = load <4 x i32>, ptr addrspace(4) %pf, align 16, !invariant.load !0
  %pfp = getelementptr i8, ptr addrspace(4) %tbl, i64 272
  %vfp = load <4 x i32>, ptr addrspace(4) %pfp, align 16, !invariant.load !0
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %pg = getelementptr i8, ptr addrspace(4) %tbl, i64 400
  %vg = load <4 x i32>, ptr addrspace(4) %pg, align 16, !invariant.load !0
  %pz = getelementptr i8, ptr addrspace(4) %tbl, i64 416
  %vz = load <4 x i32>, ptr addrspace(4) %pz, align 16, !invariant.load !0
  %pab = getelementptr i8, ptr addrspace(4) %tbl, i64 432
  %ab = load <4 x float>, ptr addrspace(4) %pab, align 16, !invariant.load !0
  %pfl = getelementptr i8, ptr addrspace(4) %tbl, i64 448
  %fl = load <4 x i32>, ptr addrspace(4) %pfl, align 16, !invariant.load !0
  %psn = getelementptr i8, ptr addrspace(4) %tbl, i64 464
  %vsn = load <4 x i32>, ptr addrspace(4) %psn, align 16, !invariant.load !0
  %pst = getelementptr i8, ptr addrspace(4) %tbl, i64 480
  %vst = load <4 x i32>, ptr addrspace(4) %pst, align 16, !invariant.load !0
  %gfl = extractelement <4 x i32> %fl, i32 0
  %sentx = extractelement <4 x i32> %fl, i32 1
  %senty = extractelement <4 x i32> %fl, i32 2
  %biasw = extractelement <4 x i32> %fl, i32 3
  %biasg = and i32 %biasw, 1023
  %biasz0 = lshr i32 %biasw, 16
  %biasz = and i32 %biasz0, 1023
  %fapply = and i32 %gfl, 2
  %apply = icmp ne i32 %fapply, 0
  %fdep = and i32 %gfl, 4
  %dep = icmp ne i32 %fdep, 0
  %frev = and i32 %gfl, 8
  %rev = icmp ne i32 %frev, 0
  %fsnap = and i32 %gfl, 32
  %snap = icmp ne i32 %fsnap, 0
  %fsen = and i32 %gfl, 64
  %son = icmp ne i32 %fsen, 0
  %fjit = and i32 %gfl, 128
  %jit = icmp ne i32 %fjit, 0
  %ax = extractelement <4 x float> %ab, i32 0
  %ay = extractelement <4 x float> %ab, i32 1
  %bx_ = extractelement <4 x float> %ab, i32 2
  %by_ = extractelement <4 x float> %ab, i32 3
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
  %foff = shl i32 %gidx, 3
  %f = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vf, i32 %foff, i32 0, i32 0)
  %fx = extractelement <2 x float> %f, i32 0
  %fy = extractelement <2 x float> %f, i32 1
  %mxi = shl i32 %bx, 4
  %myi = shl i32 %by, 4
  %mxu = or i32 %mxi, 8
  %myu = or i32 %myi, 8
  %mx = uitofp i32 %mxu to float
  %my = uitofp i32 %myu to float
  ; ---- sample 1 at m - f/2 (raw for the calibrator)
  %hfx = fmul float %fx, 0.5
  %hfy = fmul float %fy, 0.5
  %q1x = fsub float %mx, %hfx
  %q1y = fsub float %my, %hfy
  %c1 = call i32 @cell(float %q1x, float %q1y)
  %G1 = call <4 x float> @ldG(<4 x i32> %vg, i32 %c1)
  %r1x = extractelement <4 x float> %G1, i32 0
  %r1y = extractelement <4 x float> %G1, i32 1
  %g1x = call float @dec(float %r1x, float %ax, float %bx_)
  %g1y = call float @dec(float %r1y, float %ay, float %by_)
  %ok1 = call i1 @gok(float %r1x, float %r1y, float %g1x, float %g1y, i1 %son, i32 %sentx, i32 %senty)
  ; ---- fixed point: sample 2 at m - g1/2
  %hgx = fmul float %g1x, 0.5
  %hgy = fmul float %g1y, 0.5
  %q2xa = fsub float %mx, %hgx
  %q2ya = fsub float %my, %hgy
  %q2x = select i1 %ok1, float %q2xa, float %q1x
  %q2y = select i1 %ok1, float %q2ya, float %q1y
  %c2 = call i32 @cell(float %q2x, float %q2y)
  %G2 = call <4 x float> @ldG(<4 x i32> %vg, i32 %c2)
  %r2x = extractelement <4 x float> %G2, i32 0
  %r2y = extractelement <4 x float> %G2, i32 1
  %r2ox = extractelement <4 x float> %G2, i32 2
  %r2oy = extractelement <4 x float> %G2, i32 3
  %g2x = call float @dec(float %r2x, float %ax, float %bx_)
  %g2y = call float @dec(float %r2y, float %ay, float %by_)
  %gox = call float @dec(float %r2ox, float %ax, float %bx_)
  %goy = call float @dec(float %r2oy, float %ay, float %by_)
  %ok2 = call i1 @gok(float %r2x, float %r2y, float %g2x, float %g2y, i1 %son, i32 %sentx, i32 %senty)
  %oko = call i1 @gok(float %r2ox, float %r2oy, float %gox, float %goy, i1 %son, i32 %sentx, i32 %senty)
  ; ---- slots 3..11: centre value of the cell (ox, oy) in the 3x3 around q2 (4 px apart)
  %k0 = sub i32 %sub, 3
  %isz0 = icmp ult i32 %k0, 9
  %k = select i1 %isz0, i32 %k0, i32 4
  %kxu = urem i32 %k, 3
  %kyu = udiv i32 %k, 3
  %kx = sub i32 %kxu, 1
  %ky = sub i32 %kyu, 1
  %kxf = sitofp i32 %kx to float
  %kyf = sitofp i32 %ky to float
  %dqx = fmul float %kxf, 4.0
  %dqy = fmul float %kyf, 4.0
  %q3x = fadd float %q2x, %dqx
  %q3y = fadd float %q2y, %dqy
  %c3 = call i32 @cell(float %q3x, float %q3y)
  %G3 = call <4 x float> @ldG(<4 x i32> %vg, i32 %c3)
  %r3x = extractelement <4 x float> %G3, i32 0
  %r3y = extractelement <4 x float> %G3, i32 1
  %g3x = call float @dec(float %r3x, float %ax, float %bx_)
  %g3y = call float @dec(float %r3y, float %ay, float %by_)
  %ok3 = call i1 @gok(float %r3x, float %r3y, float %g3x, float %g3y, i1 %son, i32 %sentx, i32 %senty)
  ; ---- candidate of this lane
  %is0 = icmp eq i32 %sub, 0
  %is1 = icmp eq i32 %sub, 1
  %is2 = icmp eq i32 %sub, 2
  %istx = icmp eq i32 %sub, 12
  %isty = icmp eq i32 %sub, 13
  %istex = or i1 %istx, %isty
  %cxa = select i1 %isz0, float %g3x, float %fx
  %cya = select i1 %isz0, float %g3y, float %fy
  %cxb = select i1 %is2, float %gox, float %cxa
  %cyb = select i1 %is2, float %goy, float %cya
  %cvx = select i1 %is1, float %g2x, float %cxb
  %cvy = select i1 %is1, float %g2y, float %cyb
  %v1 = and i1 %is1, %ok2
  %v2 = and i1 %is2, %oko
  %v3 = and i1 %isz0, %ok3
  %vg12 = or i1 %v1, %v2
  %isg = or i1 %vg12, %v3
  %cval = or i1 %isg, %is0
  ; ---- nearest depth among the valid 3x3 cells (slots 3..11)
  %zk0 = call i32 @zkey(<4 x i32> %vz, i32 %c3, i1 %rev)
  %zk1 = and i32 %zk0, -16
  %zk2 = or i32 %zk1, %sub
  %v3d = and i1 %v3, %dep
  %zk = select i1 %v3d, i32 %zk2, i32 -1
  %zm = call i32 @reduce(i32 %tid, i32 %zk, i32 %base, i32 16)
  %zslot = and i32 %zm, 15
  %zany = icmp ne i32 %zm, -1
  %isn0 = icmp eq i32 %sub, %zslot
  %isnear = and i1 %isn0, %zany
  ; ---- cost: bilateral L1 SAD at the rounded L1 candidate; texture lanes: self SAD of cur shifted by 2 L1 px
  %hx = fmul float %cvx, 0.5
  %hy = fmul float %cvy, 0.5
  %cx0 = call i32 @rnd(float %hx)
  %cy0 = call i32 @rnd(float %hy)
  %cx1 = select i1 %istx, i32 2, i32 %cx0
  %cy1 = select i1 %istx, i32 0, i32 %cy0
  %cx = select i1 %isty, i32 0, i32 %cx1
  %cy = select i1 %isty, i32 2, i32 %cy1
  %vimg = select i1 %istex, <4 x i32> %vc, <4 x i32> %vp
  %sad = call i32 @bsad(<4 x i32> %vimg, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %cx, i32 %cy)
  %bg = select i1 %isg, i32 %biasg, i32 0
  %bz = select i1 %isnear, i32 %biasz, i32 0
  %agd = call float @dinf(float %g2x, float %g2y, float %fx, float %fy)
  %ag1 = fcmp ole float %agd, 1.0
  %njit = xor i1 %jit, true
  %ag2 = and i1 %ag1, %njit
  %ag3 = and i1 %ag2, %v1
  %ba = select i1 %ag3, i32 32, i32 0
  %b01 = add i32 %bg, %bz
  %bias0 = add i32 %b01, %ba
  %bias = call i32 @llvm.umin.i32(i32 %bias0, i32 1023)
  %c0 = add i32 %sad, 1024
  %cost = sub i32 %c0, %bias
  %kh = shl i32 %cost, 8
  %key0 = or i32 %kh, %sub
  %lt12 = icmp ult i32 %sub, 12
  %kv = and i1 %cval, %lt12
  %key = select i1 %kv, i32 %key0, i32 -1
  %m = call i32 @reduce(i32 %tid, i32 %key, i32 %base, i32 16)
  %w = and i32 %m, 255
  %src = add i32 %base, %w
  %bxi = bitcast float %cvx to i32
  %byi = bitcast float %cvy to i32
  %ox = call i32 @bcast(i32 %tid, i32 %bxi, i32 %src)
  %oy = call i32 @bcast(i32 %tid, i32 %byi, i32 %src)
  %tx12 = add i32 %base, 12
  %tx13 = add i32 %base, 13
  %tsx = call i32 @bcast(i32 %tid, i32 %sad, i32 %tx12)
  %tsy = call i32 @bcast(i32 %tid, i32 %sad, i32 %tx13)
  %zsrc0 = add i32 %base, %zslot
  %zsrc = select i1 %zany, i32 %zsrc0, i32 %base
  %zb = call i32 @bcast(i32 %tid, i32 %zk0, i32 %zsrc)
  ; ---- statistics (one atomic per wave and counter)
  %lane0 = icmp eq i32 %tid, 0
  %s_valid = and i1 %is0, %ok1
  %ag2b = fcmp ole float %agd, 2.0
  %s_ag0 = and i1 %ag2b, %ok2
  %s_agree = and i1 %is0, %s_ag0
  %won = icmp ne i32 %w, 0
  %s_used = and i1 %is0, %won
  call void @count(i1 %s_valid, i1 %lane0, <4 x i32> %vst, i32 0)
  call void @count(i1 %s_agree, i1 %lane0, <4 x i32> %vst, i32 4)
  call void @count(i1 %s_used, i1 %lane0, <4 x i32> %vst, i32 8)
  br i1 %is0, label %st, label %exit
st:
  %goff = shl i32 %gidx, 2
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgk, i32 %goff, i32 0, i32 0)
  %gk_nz = icmp ne i32 %gk, 0
  %gk_lt = icmp ult i32 %gk, 1048576
  %has_anc = and i1 %gk_nz, %gk_lt
  %ofx = bitcast i32 %ox to float
  %ofy = bitcast i32 %oy to float
  %dfg = call float @dinf(float %ofx, float %ofy, float %fx, float %fy)
  %far_anc = fcmp ogt float %dfg, 6.0
  %fl_good = icmp ule i32 %sad, 192
  %anc_keep0 = and i1 %has_anc, %far_anc
  %anc_keep = and i1 %anc_keep0, %fl_good
  %anc_allow = xor i1 %anc_keep, true
  %doap0 = and i1 %apply, %won
  %doap = and i1 %doap0, %anc_allow
  br i1 %doap, label %wf, label %snp
wf:
  %o0 = insertelement <2 x float> undef, float %ofx, i32 0
  %o1 = insertelement <2 x float> %o0, float %ofy, i32 1
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %o1, <4 x i32> %vf, i32 %foff, i32 0, i32 0)
  br label %snp
snp:
  br i1 %snap, label %ws, label %exit
ws:
  %fp = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vfp, i32 %foff, i32 0, i32 0)
  %hasgk = icmp uge i32 %gk, 1048576 ; 8.5/8.6: only real fg splats (>= 1<<20) set GVREC_GK; keep anchored blocks in gvcal!
  %fl0 = zext i1 %hasgk to i32
  %okb = zext i1 %ok1 to i32
  %okb2 = shl i32 %okb, 1
  %flags = or i32 %fl0, %okb2
  ; z: nearest depth back from the key (undo the inversion of reversed Z), 0 without depth
  %zbi = xor i32 %zb, -1
  %zbv = select i1 %rev, i32 %zbi, i32 %zb
  %zbd = select i1 %zany, i32 %zbv, i32 0
  %fxb = bitcast float %fx to i32
  %fyb = bitcast float %fy to i32
  %fpx = extractelement <2 x float> %fp, i32 0
  %fpy = extractelement <2 x float> %fp, i32 1
  %fpxb = bitcast float %fpx to i32
  %fpyb = bitcast float %fpy to i32
  %r1xb = bitcast float %r1x to i32
  %r1yb = bitcast float %r1y to i32
  %tex = add i32 %tsx, %tsy
  %a0 = insertelement <4 x i32> undef, i32 %fxb, i32 0
  %a1 = insertelement <4 x i32> %a0, i32 %fyb, i32 1
  %a2 = insertelement <4 x i32> %a1, i32 %fpxb, i32 2
  %a3 = insertelement <4 x i32> %a2, i32 %fpyb, i32 3
  %b0 = insertelement <4 x i32> undef, i32 %r1xb, i32 0
  %b1 = insertelement <4 x i32> %b0, i32 %r1yb, i32 1
  %b2 = insertelement <4 x i32> %b1, i32 %sad, i32 2
  %b3 = insertelement <4 x i32> %b2, i32 %tex, i32 3
  %d0 = insertelement <2 x i32> undef, i32 %flags, i32 0
  %d1 = insertelement <2 x i32> %d0, i32 %zbd, i32 1
  %ro = mul i32 %gidx, 40
  %ro16 = add i32 %ro, 16
  %ro32 = add i32 %ro, 32
  call void @llvm.amdgcn.raw.buffer.store.v4i32(<4 x i32> %a3, <4 x i32> %vsn, i32 %ro, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.v4i32(<4 x i32> %b3, <4 x i32> %vsn, i32 %ro16, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.v2i32(<2 x i32> %d1, <4 x i32> %vsn, i32 %ro32, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
