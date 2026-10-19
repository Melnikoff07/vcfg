; user_sgprs=2
; tgid_y=1
; mode 8.4, pass 6: raw motion field (filtered by mf8 afterwards) on L1 (960x540), 120 x 68 blocks of 8x8 (= 16x16 full-res px), on the MID grid.
; dispatch 30 x 68 groups; lane = block (tid>>4) * 16 + slot (tid&15): 4 blocks per wave, 16 candidate slots each.
;  phase 1: 4 nearest L2 vectors (x2) + 3 temporal predictors (previous field: same block, right, below) + zero;
;           temporal slots get -TBIAS, zero gets -ZBIAS (inertia / static preference, 3DRS style);
;           slot 8: the foreground vector fgs8 splatted onto this block (if any), x2, with -FGBIAS (96)
;           slot 9 (8.3): the camera vector gm (gm8, L2 px) x2 with -CBIAS (32)
;  8.3: the zero vector is only special while the camera stands still (gm = 0): zero bias and zero test apply
;       then only. In a fast turn (78 px/frame on the dump) a dark 16x16 block often matches dark-on-dark at the
;       zero vector better than at the true (blurred, changing) one; the zero vector won with its bias, and warp8
;       picked it per pixel = dark specks in the bright flowers.
;  phase 2: +-1 around the winner, penalty LAM1 per px
;  phase 3: SADs of b, b-+x, b-+y, 0 -> equiangular sub-pixel fit, zero test, store float2 full-res px vector.
; Blocks whose best and zero SAD are both above 256 (+ the share of a global brightness change) count towards the
; scene-change counter (misc dword 0).
; If the frame's luma sum (misc dword 2 + parity, from me3) differs from the previous frame's by > 2 %, the whole
; field is zero (fade / flash: plain blend). 8.3: only while the camera is (nearly) still, see the code.
; The zero test (zero SAD within ZBIAS of the best -> exactly 0) is skipped where a foreground vector was splatted:
; there the background behind the passing object matches the zero vector just as well, which is how mode 8 lost it.
; table: +0x80 V#L1cur, +0x90 V#L1prev, +0xF0 V#V2 (60x34 packed), +0x100 V#Fcur, +0x110 V#Fprev, +0x120 V#misc,
;        +0x150 V#GK (120x68 splat keys from fgs8: prio | closeness | L2 block, 0 = none)
; 8.3: the field goes to +0x180 V#FT (raw); mf8 filters it into +0x100 (F cur).
; 8.5: GK keys below 1 << 20 are ANCHORS (fgs8: a screen-fixed object while the camera turns). Slot 8 then carries
; the anchor vector with bias 64, and every other candidate farther than ARAD = 3 L1 px (manhattan) from it pays
; ESC = 512: on the mid grid a vector whose two endpoints jump over a static, frame-to-frame noisy object (RDR2 hair)
; onto uniform sky has bilateral SAD ~50 against 300..1200 for the true vector, and once it is in the field the
; temporal predictors keep it (the head split). Real foreground keys (>= 1 << 20) are unchanged.
; 8.6 (round 2): (1) a temporal candidate (slots 4..6: last frame's vector at this / the right / the lower block, not
; motion compensated) larger than TPBIG = 64 L1 px (manhattan) must be supported by the cur grid now: within 3 L1 px of
; one of the 4 L2 vectors, zero, the camera or the splat vector. Otherwise it is invalid: on dump 03 (camera still)
; 40 head blocks kept (192, -50) frame after frame only through these slots (bilateral SAD ~20 vs ~250 at zero).
; Cells with a real foreground key keep their temporal candidates (blade). (2) the zero test is skipped only under
; REAL foreground keys, not under anchors (anchors now exist with a still camera too).
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
  %ma0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 0, i32 0)
  %mb0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 0, i32 %ma0)
  %mc0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_0, i32 0, i32 %mb0)
  %md0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_0, i32 0, i32 %mc0)
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
  %ma1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_1, i32 0, i32 %md0)
  %mb1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_1, i32 0, i32 %ma1)
  %mc1 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_1, i32 0, i32 %mb1)
  %md1 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_1, i32 0, i32 %mc1)
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
  %ma2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_2, i32 0, i32 %md1)
  %mb2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_2, i32 0, i32 %ma2)
  %mc2 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_2, i32 0, i32 %mb2)
  %md2 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_2, i32 0, i32 %mc2)
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
  %ma3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_3, i32 0, i32 %md2)
  %mb3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_3, i32 0, i32 %ma3)
  %mc3 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_3, i32 0, i32 %mb3)
  %md3 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_3, i32 0, i32 %mc3)
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
  %ma4 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_4, i32 0, i32 %md3)
  %mb4 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_4, i32 0, i32 %ma4)
  %mc4 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_4, i32 0, i32 %mb4)
  %md4 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_4, i32 0, i32 %mc4)
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
  %ma5 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_5, i32 0, i32 %md4)
  %mb5 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_5, i32 0, i32 %ma5)
  %mc5 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_5, i32 0, i32 %mb5)
  %md5 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_5, i32 0, i32 %mc5)
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
  %ma6 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_6, i32 0, i32 %md5)
  %mb6 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_6, i32 0, i32 %ma6)
  %mc6 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_6, i32 0, i32 %mb6)
  %md6 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_6, i32 0, i32 %mc6)
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
  %ma7 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_7, i32 0, i32 %md6)
  %mb7 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_7, i32 0, i32 %ma7)
  %mc7 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_7, i32 0, i32 %mb7)
  %md7 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_7, i32 0, i32 %mc7)
  %msum = lshr i32 %md7, 1
  %msh = shl i32 %msum, 16
  %res = or i32 %sb7, %msh
  ret i32 %res
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
  ; ---- the 4 L2 blocks around this block centre
  %bx4 = shl i32 %bx, 2
  %fxm = sub i32 %bx4, 2
  %ix0r = ashr i32 %fxm, 3
  %ix0a = call i32 @llvm.smax.i32(i32 %ix0r, i32 0)
  %ix0 = call i32 @llvm.smin.i32(i32 %ix0a, i32 59)
  %ix1a = add i32 %ix0, 1
  %ix1 = call i32 @llvm.smin.i32(i32 %ix1a, i32 59)
  %by4 = shl i32 %gy, 2
  %fym = sub i32 %by4, 2
  %iy0r = ashr i32 %fym, 3
  %iy0a = call i32 @llvm.smax.i32(i32 %iy0r, i32 0)
  %iy0 = call i32 @llvm.smin.i32(i32 %iy0a, i32 33)
  %iy1a = add i32 %iy0, 1
  %iy1 = call i32 @llvm.smin.i32(i32 %iy1a, i32 33)
  %s1 = and i32 %sub, 1
  %s1b = icmp ne i32 %s1, 0
  %nx = select i1 %s1b, i32 %ix1, i32 %ix0
  %s2 = and i32 %sub, 2
  %s2b = icmp ne i32 %s2, 0
  %ny = select i1 %s2b, i32 %iy1, i32 %iy0
  %nrow = mul i32 %ny, 60
  %nidx = add i32 %nrow, %nx
  %noff = shl i32 %nidx, 2
  %nv = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %noff, i32 0, i32 0)
  %nvx = call i32 @unx(i32 %nv)
  %nvy = call i32 @uny(i32 %nv)
  %ndx = shl i32 %nvx, 1
  %ndy = shl i32 %nvy, 1
  ; ---- temporal predictors (previous frame's final field, full-res px -> L1 px)
  %is5 = icmp eq i32 %sub, 5
  %is6 = icmp eq i32 %sub, 6
  %e5 = zext i1 %is5 to i32
  %e6 = zext i1 %is6 to i32
  %tbxr = add i32 %bx, %e5
  %tbx = call i32 @llvm.smin.i32(i32 %tbxr, i32 119)
  %tbyr = add i32 %gy, %e6
  %tby = call i32 @llvm.smin.i32(i32 %tbyr, i32 67)
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
  ; ---- camera vector (gm8, L2 px) -> L1 px; the camera stands still when gm = 0
  %gmx = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 16, i32 0, i32 0)
  %gmy = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 20, i32 0, i32 0)
  %gmx2 = shl i32 %gmx, 1
  %gmy2 = shl i32 %gmy, 1
  %gmor = or i32 %gmx, %gmy
  %still = icmp eq i32 %gmor, 0
  ; ---- phase 1
  %islt4 = icmp ult i32 %sub, 4
  %s4 = sub i32 %sub, 4
  %ist = icmp ult i32 %s4, 3
  %is8 = icmp eq i32 %sub, 8
  %cx0 = select i1 %ist, i32 %tdx, i32 0
  %cy0 = select i1 %ist, i32 %tdy, i32 0
  %is9 = icmp eq i32 %sub, 9
  %cx9 = select i1 %is9, i32 %gmx2, i32 %cx0
  %cy9 = select i1 %is9, i32 %gmy2, i32 %cy0
  %cx8 = select i1 %is8, i32 %gdx, i32 %cx9
  %cy8 = select i1 %is8, i32 %gdy, i32 %cy9
  %cdx = select i1 %islt4, i32 %ndx, i32 %cx8
  %cdy = select i1 %islt4, i32 %ndy, i32 %cy8
  %zsrc = add i32 %base, 7
  %bs1 = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %cdx, i32 %cdy)
  %sad1 = and i32 %bs1, 65535
  %sum1 = lshr i32 %bs1, 16
  ; 8.7 (round 6 idea): triangle-inequality penalty. Lane 7 (zero vector) publishes this block's centre info
  ; (mean luma sum << 16 | zero SAD). A fast candidate (|d|_1 > 8) whose two taps have a mean brightness far from the
  ; block centre by more than the centre's own frame-to-frame change (zero SAD) jumped over a static object onto sky.
  %zinfo = call i32 @bcast(i32 %tid, i32 %bs1, i32 %zsrc)
  %zsad0 = and i32 %zinfo, 65535
  %sum0 = lshr i32 %zinfo, 16
  %isz = icmp eq i32 %sub, 7
  %bt = select i1 %ist, i32 16, i32 0
  %zb = select i1 %still, i32 48, i32 0
  %bz = select i1 %isz, i32 %zb, i32 %bt
  %b9 = select i1 %is9, i32 32, i32 %bz
  ; 8.5 anchor cells (key < 1 << 20): slot 8 bias 64, escape penalty for candidates far from the anchor vector
  %isfgk = icmp uge i32 %gk, 1048576
  %nfgk = xor i1 %isfgk, true
  %isanc = and i1 %hasfg, %nfgk
  %b8 = select i1 %isfgk, i32 96, i32 64
  %bias = select i1 %is8, i32 %b8, i32 %b9
  %adx = sub i32 %cdx, %gdx
  %ady = sub i32 %cdy, %gdy
  %aadx = call i32 @llvm.abs.i32(i32 %adx, i1 false)
  %aady = call i32 @llvm.abs.i32(i32 %ady, i1 false)
  %aad = add i32 %aadx, %aady
  %afar = icmp sgt i32 %aad, 3
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
  %p1a = and i1 %p1f, true
  %p1p = select i1 %p1a, i32 %p1e, i32 0
  %c1b = add i32 %c1ab, %p1p
  %cost1 = sub i32 %c1b, %bias
  %k1a = shl i32 %cost1, 8
  %k1b = or i32 %k1a, %sub
  ; 8.5 diagnostic (consts flags bit 6, inject letter 'T'): temporal slots 4..6 off
  %pcnt = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnt4 = load <4 x i32>, ptr addrspace(4) %pcnt, align 16, !invariant.load !0
  %flt = extractelement <4 x i32> %cnt4, i32 2
  %flt64 = and i32 %flt, 64
  %notp = icmp ne i32 %flt64, 0
  %tpoff = and i1 %ist, %notp
  ; 8.6 temporal support (see header): distance of this lane's candidate to the support set
  %sa0 = call i32 @nbdist(<4 x i32> %v2, i32 %ix0, i32 %iy0, i32 %cdx, i32 %cdy)
  %sa1 = call i32 @nbdist(<4 x i32> %v2, i32 %ix1, i32 %iy0, i32 %cdx, i32 %cdy)
  %sa2 = call i32 @nbdist(<4 x i32> %v2, i32 %ix0, i32 %iy1, i32 %cdx, i32 %cdy)
  %sa3 = call i32 @nbdist(<4 x i32> %v2, i32 %ix1, i32 %iy1, i32 %cdx, i32 %cdy)
  %szx = call i32 @llvm.abs.i32(i32 %cdx, i1 false)
  %szy = call i32 @llvm.abs.i32(i32 %cdy, i1 false)
  %sz = add i32 %szx, %szy
  %scx0 = sub i32 %cdx, %gmx2
  %scy0 = sub i32 %cdy, %gmy2
  %scx = call i32 @llvm.abs.i32(i32 %scx0, i1 false)
  %scy = call i32 @llvm.abs.i32(i32 %scy0, i1 false)
  %sc = add i32 %scx, %scy
  %sgx0 = sub i32 %cdx, %gdx
  %sgy0 = sub i32 %cdy, %gdy
  %sgx = call i32 @llvm.abs.i32(i32 %sgx0, i1 false)
  %sgy = call i32 @llvm.abs.i32(i32 %sgy0, i1 false)
  %sg0 = add i32 %sgx, %sgy
  %sg = select i1 %hasfg, i32 %sg0, i32 %sz
  %sm01 = call i32 @llvm.umin.i32(i32 %sa0, i32 %sa1)
  %sm23 = call i32 @llvm.umin.i32(i32 %sa2, i32 %sa3)
  %sm03 = call i32 @llvm.umin.i32(i32 %sm01, i32 %sm23)
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
  %val1a1 = icmp ult i32 %sub, 8
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
  %bs2 = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %d2x, i32 %d2y)
  %sad2 = and i32 %bs2, 65535
  %sum2 = lshr i32 %bs2, 16
  %p2x = call i32 @llvm.abs.i32(i32 %d2x, i1 false)
  %p2y = call i32 @llvm.abs.i32(i32 %d2y, i1 false)
  %p2m = add i32 %p2x, %p2y
  %p2f = icmp ugt i32 %p2m, 8
  %p2d0 = sub i32 %sum2, %sum0
  %p2d = call i32 @llvm.abs.i32(i32 %p2d0, i1 false)
  %p2h = lshr i32 %p2d, 1
  %p2e0 = sub i32 %p2h, %zsad0
  %p2e = call i32 @llvm.smax.i32(i32 %p2e0, i32 0)
  %p2a = and i1 %p2f, true
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
  %bs3 = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %X, i32 %Y, i32 %d3x, i32 %d3y)
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
  ; 8.3: only while the camera (gm8) moves at most 1 L2 px (4 full-res px) per frame. Turning towards the bright sky
  ; changes the frame's luma sum by several % too (9.5 % on the crop of dump 03), and a zero field then turned the
  ; whole mid frame into a 50/50 blend = double image during turns (log: 'fade frames' 13..23 of 90). A real fade
  ; with a moving camera is handled by the motion compensated blend anyway (prev and cur colours are mixed).
  %gax = call i32 @llvm.abs.i32(i32 %gmx, i1 false)
  %gay = call i32 @llvm.abs.i32(i32 %gmy, i1 false)
  %gaxy = call i32 @llvm.smax.i32(i32 %gax, i32 %gay)
  %slow1 = icmp sle i32 %gaxy, 1
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
  ; unmatched block: best and zero SAD above 256 + the SAD a uniform brightness change alone would cause
  ; (64 px * mean luma change per px), so fades do not count as scene changes
  %mn = call i32 @llvm.umin.i32(i32 %r0, i32 %r5)
  %mnf = uitofp i32 %mn to float
  %fdl = fmul float %fad, 0x3F602E85C0000000; 0.0019753087
  %cthr = fadd float %fdl, 256.0
  %cut = fcmp ogt float %mnf, %cthr
  br i1 %cut, label %cnt, label %exit
cnt:
  %old = call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 1, <4 x i32> %vm, i32 0, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

; equiangular (V shaped) sub-pixel fit of an L1 cost: o = (m - p) / (2 (max(m, p) - c)), clamped to +-0.5
; 8.4: only at a strict local minimum c < min(m, p) (found by the other model in round 5): phase 2 picks the best
; COST (SAD + 4 per px), so c can sit on a slope (m < c < p) and the fit then pushed the vector 0.5 L1 px (1 px)
; towards the worse side.
define internal float @equi(float %m, float %c, float %p) alwaysinline {
  %mx = call float @llvm.maxnum.f32(float %m, float %p)
  %mn = call float @llvm.minnum.f32(float %m, float %p)
  %d0 = fsub float %mx, %c
  %den = fmul float %d0, 2.0
  %num = fsub float %m, %p
  %q = fdiv float %num, %den
  %pos = fcmp olt float %c, %mn
  %o = select i1 %pos, float %q, float 0.0
  %o1 = call float @llvm.maxnum.f32(float %o, float -0.5)
  %o2 = call float @llvm.minnum.f32(float %o1, float 0.5)
  ret float %o2
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
