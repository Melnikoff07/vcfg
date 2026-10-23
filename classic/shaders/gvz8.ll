; user_sgprs=2
; tgid_y=1
; velocity subsystem, debug view gvz8 (inject letter 'z', needs 'v' at load): four 240 x 135 panels in the top-right
; corner of the GENERATED frame (the mid buffer), drawn after warp8/warp8g, so the person at the TV sees live what
; the game buffer holds and whether it agrees with our flow (idea of the bb_probe_85 variant: picture-in-picture).
; Real frames are not touched, so the panels blink at 30 Hz; that is intended (nothing of the game image is lost).
; Layout (x0 = 1416, y0 = 16, 8 px black gaps):
;   A top-left     game buffer, centre value of each 4x4 cell, decoded with the VIEW decode (+0x1F0: {va x, va y,
;                  vb x, vb y}): after calibration = the calibrated a, b; before it the IO thread fills a robust
;                  normalisation of the raw values (median / MAD of the last snapshot), so even an uncalibrated
;                  candidate shows its structure. Dark grey = sentinel ("not written"), black = nothing captured.
;   B top-right    our flow field F (120 x 68 blocks, after gv8 when it ran)
;   C bottom-left  agreement of the calibrated game vector (+0x1B0 decode) with F: green < 2 px, yellow < 6 px,
;                  red otherwise, black = no game sample. Meaningful only after calibration.
;   D bottom-right depth (nearest of the cell), brighter = nearer, 4th root for contrast; black = no depth
; Vector colour: channel 0 = 0.5 + vx/64, channel 2 = 0.5 - vx/64, channel 1 = 0.5 + vy/64 (grey = no motion,
; full colour at 32 px). Channel 0 / 2 may be red / blue or blue / red depending on the display format; only the
; contrast matters.
; Dispatch (61, 35) groups of 8 x 8 (488 x 280 px), offset (1416, 16).
; table: +0x40 T#out, +0x100 V#F cur, +0x190 V#G, +0x1A0 V#Z, +0x1B0 {ax, ay, bx, by}, +0x1C0 {gflags, sentinel x,
;        sentinel y, -}, +0x1F0 {va x, va y, vb x, vb y} (view decode). gflags: 1 G valid, 4 depth valid, 8 reversed Z,
;        64 sentinel on.
target triple = "amdgcn--"
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare <4 x float> @llvm.amdgcn.raw.buffer.load.v4f32(<4 x i32>, i32, i32, i32 immarg)
declare float @llvm.fabs.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)
declare float @llvm.sqrt.f32(float)

define internal float @sat(float %x) alwaysinline {
  %a = call float @llvm.maxnum.f32(float %x, float 0.0)
  %b = call float @llvm.minnum.f32(float %a, float 1.0)
  ret float %b
}

; vector (px) -> colour, see header
define internal <4 x float> @vcol(float %vx, float %vy) alwaysinline {
  %sx = fmul float %vx, 0.015625
  %sy = fmul float %vy, 0.015625
  %c0a = fadd float %sx, 0.5
  %c2a = fsub float 0.5, %sx
  %c1a = fadd float %sy, 0.5
  %c0 = call float @sat(float %c0a)
  %c1 = call float @sat(float %c1a)
  %c2 = call float @sat(float %c2a)
  %r0 = insertelement <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0>, float %c0, i32 0
  %r1 = insertelement <4 x float> %r0, float %c1, i32 1
  %r2 = insertelement <4 x float> %r1, float %c2, i32 2
  ret <4 x float> %r2
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %qx = add i32 %bxp, %lx
  %qy = add i32 %byp, %ly
  ; inside the 488 x 278 area?
  %inx = icmp ult i32 %qx, 488
  %iny = icmp ult i32 %qy, 278
  %in = and i1 %inx, %iny
  br i1 %in, label %body, label %done
body:
  %x = add i32 %qx, 1416
  %y = add i32 %qy, 16
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  %pf = getelementptr i8, ptr addrspace(4) %tbl, i64 256
  %vf = load <4 x i32>, ptr addrspace(4) %pf, align 16, !invariant.load !0
  %pgf = getelementptr i8, ptr addrspace(4) %tbl, i64 448
  %gfv = load <4 x i32>, ptr addrspace(4) %pgf, align 16, !invariant.load !0
  %gfl = extractelement <4 x i32> %gfv, i32 0
  ; panel and position inside it (gap columns 240..247, gap rows 135..142)
  %right = icmp uge i32 %qx, 248
  %bottom = icmp uge i32 %qy, 143
  %px0 = sub i32 %qx, 248
  %px = select i1 %right, i32 %px0, i32 %qx
  %py0 = sub i32 %qy, 143
  %py = select i1 %bottom, i32 %py0, i32 %qy
  %gapx = icmp uge i32 %px, 240
  %gapy = icmp uge i32 %py, 135
  %gap = or i1 %gapx, %gapy
  br i1 %gap, label %store_black, label %panel
panel:
  ; game cell (2 px, 2 py) of the 480 x 270 grid, flow block (px / 2, py / 2) of 120 x 68
  %cx = shl i32 %px, 1
  %cy = shl i32 %py, 1
  %crow = mul i32 %cy, 480
  %ci = add i32 %crow, %cx
  %fbx = lshr i32 %px, 1
  %fby = lshr i32 %py, 1
  %frow = mul i32 %fby, 120
  %fi = add i32 %frow, %fbx
  %fo = shl i32 %fi, 3
  %fv = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vf, i32 %fo, i32 0, i32 0)
  %ffx = extractelement <2 x float> %fv, i32 0
  %ffy = extractelement <2 x float> %fv, i32 1
  br i1 %bottom, label %lower, label %upper
upper:
  br i1 %right, label %pB, label %pA
pB:
  %colB = call <4 x float> @vcol(float %ffx, float %ffy)
  br label %store
pA:
  %gvalid = and i32 %gfl, 1
  %hasg = icmp ne i32 %gvalid, 0
  br i1 %hasg, label %pA1, label %store_black
pA1:
  %pgg = getelementptr i8, ptr addrspace(4) %tbl, i64 400
  %vgg = load <4 x i32>, ptr addrspace(4) %pgg, align 16, !invariant.load !0
  %go = shl i32 %ci, 4
  %graw = call <4 x float> @llvm.amdgcn.raw.buffer.load.v4f32(<4 x i32> %vgg, i32 %go, i32 0, i32 0)
  %rx0 = extractelement <4 x float> %graw, i32 0
  %ry0 = extractelement <4 x float> %graw, i32 1
  %sxb = extractelement <4 x i32> %gfv, i32 1
  %syb = extractelement <4 x i32> %gfv, i32 2
  %rxb = bitcast float %rx0 to i32
  %ryb = bitcast float %ry0 to i32
  %ex = icmp eq i32 %rxb, %sxb
  %ey = icmp eq i32 %ryb, %syb
  %exy = and i1 %ex, %ey
  %son0 = and i32 %gfl, 64
  %son = icmp ne i32 %son0, 0
  %issent = and i1 %exy, %son
  %fsw = and i32 %gfl, 512
  %sw = icmp ne i32 %fsw, 0
  %rx = select i1 %sw, float %ry0, float %rx0
  %ry = select i1 %sw, float %rx0, float %ry0
  %pva = getelementptr i8, ptr addrspace(4) %tbl, i64 496
  %va = load <4 x float>, ptr addrspace(4) %pva, align 16, !invariant.load !0
  %vax = extractelement <4 x float> %va, i32 0
  %vay = extractelement <4 x float> %va, i32 1
  %vbx = extractelement <4 x float> %va, i32 2
  %vby = extractelement <4 x float> %va, i32 3
  %dx0 = fmul float %rx, %vax
  %dx = fadd float %dx0, %vbx
  %dy0 = fmul float %ry, %vay
  %dy = fadd float %dy0, %vby
  %colA0 = call <4 x float> @vcol(float %dx, float %dy)
  %colA = select i1 %issent, <4 x float> <float 0x3FC99999A0000000, float 0x3FC99999A0000000, float 0x3FC99999A0000000, float 1.0>, <4 x float> %colA0
  br label %store
lower:
  br i1 %right, label %pD, label %pC
pC:
  %gvalid2 = and i32 %gfl, 1
  %hasg2 = icmp ne i32 %gvalid2, 0
  br i1 %hasg2, label %pC1, label %store_black
pC1:
  %pgg2 = getelementptr i8, ptr addrspace(4) %tbl, i64 400
  %vgg2 = load <4 x i32>, ptr addrspace(4) %pgg2, align 16, !invariant.load !0
  %go2 = shl i32 %ci, 4
  %graw2 = call <4 x float> @llvm.amdgcn.raw.buffer.load.v4f32(<4 x i32> %vgg2, i32 %go2, i32 0, i32 0)
  %rx2_0 = extractelement <4 x float> %graw2, i32 0
  %ry2_0 = extractelement <4 x float> %graw2, i32 1
  %sxb2 = extractelement <4 x i32> %gfv, i32 1
  %syb2 = extractelement <4 x i32> %gfv, i32 2
  %rxb2 = bitcast float %rx2_0 to i32
  %ryb2 = bitcast float %ry2_0 to i32
  %ex2 = icmp eq i32 %rxb2, %sxb2
  %ey2 = icmp eq i32 %ryb2, %syb2
  %exy2 = and i1 %ex2, %ey2
  %son2a = and i32 %gfl, 64
  %son2 = icmp ne i32 %son2a, 0
  %issent2 = and i1 %exy2, %son2
  %fsw2 = and i32 %gfl, 512
  %sw2 = icmp ne i32 %fsw2, 0
  %rx2 = select i1 %sw2, float %ry2_0, float %rx2_0
  %ry2 = select i1 %sw2, float %rx2_0, float %ry2_0
  %pab = getelementptr i8, ptr addrspace(4) %tbl, i64 432
  %ab = load <4 x float>, ptr addrspace(4) %pab, align 16, !invariant.load !0
  %ax = extractelement <4 x float> %ab, i32 0
  %ay = extractelement <4 x float> %ab, i32 1
  %bx = extractelement <4 x float> %ab, i32 2
  %by = extractelement <4 x float> %ab, i32 3
  %gx0 = fmul float %rx2, %ax
  %gxd = fadd float %gx0, %bx
  %gy0 = fmul float %ry2, %ay
  %gyd = fadd float %gy0, %by
  %ddx = fsub float %gxd, %ffx
  %ddy = fsub float %gyd, %ffy
  %adx = call float @llvm.fabs.f32(float %ddx)
  %ady = call float @llvm.fabs.f32(float %ddy)
  %dm = call float @llvm.maxnum.f32(float %adx, float %ady)
  %ok2 = fcmp olt float %dm, 2.0
  %ok6 = fcmp olt float %dm, 6.0
  %c6 = select i1 %ok6, <4 x float> <float 0x3FECCCCCC0000000, float 0x3FECCCCCC0000000, float 0.0, float 1.0>, <4 x float> <float 0x3FECCCCCC0000000, float 0.0, float 0x3FECCCCCC0000000, float 1.0>
  %c2 = select i1 %ok2, <4 x float> <float 0.0, float 0x3FE99999A0000000, float 0.0, float 1.0>, <4 x float> %c6
  ; non-finite decoded value (NaN compares false above) shows as "far off" (magenta-ish): fine for a debug view
  %colC = select i1 %issent2, <4 x float> <float 0x3FC99999A0000000, float 0x3FC99999A0000000, float 0x3FC99999A0000000, float 1.0>, <4 x float> %c2
  br label %store
pD:
  %zvalid = and i32 %gfl, 4
  %hasz = icmp ne i32 %zvalid, 0
  br i1 %hasz, label %pD1, label %store_black
pD1:
  %pz = getelementptr i8, ptr addrspace(4) %tbl, i64 416
  %vz = load <4 x i32>, ptr addrspace(4) %pz, align 16, !invariant.load !0
  %zo = shl i32 %ci, 3
  %zmm = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vz, i32 %zo, i32 0, i32 0)
  %zmin = extractelement <2 x float> %zmm, i32 0
  %zmax = extractelement <2 x float> %zmm, i32 1
  %zr0 = and i32 %gfl, 8
  %zrev = icmp ne i32 %zr0, 0
  %znf = fsub float 1.0, %zmin
  %zn0 = select i1 %zrev, float %zmax, float %znf
  %zn = call float @sat(float %zn0)
  %zs1 = call float @llvm.sqrt.f32(float %zn)
  %zs = call float @llvm.sqrt.f32(float %zs1)
  %zc0 = insertelement <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0>, float %zs, i32 0
  %zc1 = insertelement <4 x float> %zc0, float %zs, i32 1
  %colD = insertelement <4 x float> %zc1, float %zs, i32 2
  br label %store
store_black:
  br label %store
store:
  %col = phi <4 x float> [ %colB, %pB ], [ %colA, %pA1 ], [ %colC, %pC1 ], [ %colD, %pD1 ], [ <float 0.0, float 0.0, float 0.0, float 1.0>, %store_black ]
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %col, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  br label %done
done:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
