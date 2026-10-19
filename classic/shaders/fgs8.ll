; user_sgprs=2
; mode 8.2, pass 5: foreground splat (RCAS3 style forward splat, on blocks). One lane per L2 block (60 x 34 = 2040),
; dispatch 32 groups of 64.
; An L2 block (32x32 full-res px, cur grid) whose vector really matches (same test as gm8) and differs from the
; dominant motion gm (misc dwords 4, 5) by more than 8 full-res px is a moving object. Half way along its path it sits
; at c = block centre + D/2 (D = 4 V2 full-res px; with the convention cur(x) ~ prev(x + D) the mid position of a
; cur pixel x is x + D/2). Its key goes to the L1 blocks (16x16 px, mid grid) its 32x32 footprint covers there
; (3 x 3 cells around c), atomic max: the most deviating vector wins (closer to the camera / faster = in front),
; ties -> the block whose centre is closer to the cell centre, then the larger block index.
; key = prio << 20 | closeness << 12 | block,  prio = 1 + min(floor(|D - 4 gm| / 8), 4094) (>= 1: key 0 = empty),
; closeness = 255 - min(floor(4 * distance), 255). me1 takes V2[block] x2 as an extra candidate with a bonus.
; All integer: |D - 4 gm| / 8 = sqrt(q) / 2 with q = |V2 - gm|^2, floor(sqrt(q) / 2) = isqrt(q >> 2);
; floor(4 dist) = isqrt(16 e). GK (120 x 68 keys) is zeroed by pyr8. Foreground blocks are counted in misc dword 6
; (statistics for the log only; zeroed by pyr8).
; 8.3: "really matches" is now tested against the CAMERA vector, not the zero vector: SAD(best) * 5 <= SAD(cam) * 4
; and SAD(cam) - SAD(best) >= 96, where SAD(cam) = min over gm and its 4 neighbours (gm is an integer L2 vector, the
; real camera motion is sub-pixel). The old test (vs zero, or SAD <= 160) let any block of a fine or periodic texture
; through while the camera moves (zero is wrong there anyway): on the console dump the fence got 'foreground' vectors
; of +-31 px (bars every ~7 px: about 4 periods) splatted with bonus 96 and the bars washed out.
; 8.4 (ideas of the two other models in round 5): also (a) the deviation from the camera must exceed a quarter of the
; camera motion squared: |V2 - gm|^2 > 4 + |gm|^2 / 4 (depth parallax in a fast turn is not an object), and the block
; must move faster than the camera: |V2|^2 > |gm|^2 + 4; (b) one of the 4 cardinal L2 neighbours must have a vector
; within 4 L2 px (manhattan): a real object is bigger than one 32x32 block, an isolated mismatch is not. On the
; console dump 03 (fast turn) this leaves 6 foreground blocks instead of 144.
; 8.5 (head split, RDR2): ANCHORS. A block that really matches (same camera test), deviates from the camera like a
; foreground block but moves at most half as fast as the camera on screen (|V2|^2 * 4 <= |gm|^2: a third-person
; character, a held object, HUD over a turning scene) is splatted too, with prio 0 (key < 1 << 20, so any real
; foreground key wins the atomic max). me1 / mf8 then penalise every candidate far from the anchor vector: the
; bilateral SAD of the mid grid cannot see a static object at all (a vector whose two endpoints jump over the head
; onto uniform sky has SAD ~0, the true small vector pays the hair's frame to frame noise), the unidirectional cur
; grid vector of me2 can. Not on border blocks, not while consts flags bit 5 (inject letter 'L') is set, and only
; after the condition held in 2 consecutive frames (u8 counter per L2 block at V3 + 2048; a fast object's trail or
; disocclusion matches the anchor test for one frame). Anchors are not counted in misc dword 6.
; 8.6 (round 2, console field dump): (1) TEXTURE GATE. A flat block (sky) carries no evidence of its own motion: on dump 11
; the sky disoccluded next to a static head could not match at the camera vector (prev shows the head there), matched
; 'around' the head at (cam.x, -12 L2) instead, passed every foreground test and was splatted at block + D/2 = onto
; the head (fg keys beat anchors, me1 then skips the zero test). Foreground and anchors now need the texture energy
; A = SAD(cur, cur shifted +1 x) + SAD(cur, cur shifted +1 y) of the 8x8 L2 block >= 64 (dumps: sky 12..53, head,
; body, blade >= 116). (2) ANCHORS ALSO WITH A STILL CAMERA (|gm|^2 <= 4): textured, own SAD at most half the texture
; energy (the cur grid vector really explains the block), |V2| <= 1 L2 px. Dump 03 (camera still, 40 head blocks at
; (192, -50) from earlier frames, no key at all) is exactly this case. With a moving camera the 8.5 test is kept.
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
  ; ---- real match against the camera vector (see header)
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
  %cs0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmy)
  %cs1 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmxm, i32 %gmy)
  %cs2 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmxp, i32 %gmy)
  %cs3 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmym)
  %cs4 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 %gmx, i32 %gmyp)
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
  ; ---- deviation from the dominant motion > 8 full-res px (q > 4) and moving itself (|V2|^2 > 4)
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
  %gmv4 = lshr i32 %gmv, 2
  %devt = add i32 %gmv4, 4
  %dev = icmp ugt i32 %q, %devt
  %movt = add i32 %gmv, 4
  %mov = icmp ugt i32 %vv, %movt
  ; 8.7 texture energy of both cur(BX, BY) and matched prev(BX+vx, BY+vy), plus real-match gate 2*sb <= texm on fg0
  %ax0 = call i32 @bsadu(<4 x i32> %vl2c, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 1, i32 0)
  %ay0 = call i32 @bsadu(<4 x i32> %vl2c, <4 x i32> %vl2c, i32 480, i32 270, i32 %BX, i32 %BY, i32 0, i32 1)
  %tex = add i32 %ax0, %ay0
  %BXp = add i32 %BX, %vx
  %BYp = add i32 %BY, %vy
  %px0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2p, i32 480, i32 270, i32 %BXp, i32 %BYp, i32 1, i32 0)
  %py0 = call i32 @bsadu(<4 x i32> %vl2p, <4 x i32> %vl2p, i32 480, i32 270, i32 %BXp, i32 %BYp, i32 0, i32 1)
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
  ; ---- 8.5 anchor: at most half the camera speed, inner block, letter 'L' off (see header)
  %vv4 = shl i32 %vv, 2
  %slw = icmp ule i32 %vv4, %gmv
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
  %stl = icmp ule i32 %gmv, 4
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
  %nb01 = or i1 %nb0, %nb1
  %nb23 = or i1 %nb2, %nb3
  %fg = or i1 %nb01, %nb23
  br label %hist
hist:
  %nbk = phi i1 [ false, %go ], [ %fg, %nbc ]
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
  %hok = icmp uge i32 %hn, 2
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
