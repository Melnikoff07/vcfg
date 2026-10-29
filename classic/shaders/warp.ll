; user_sgprs=2
; tgid_y=1
; table: +0x00 T#prev, +0x20 T#cur, +0x40 T#out, +0x80 V#mv (80x45 packed int16 dx | dy<<16, luma units),
;        +0xC0 consts {sx, W, H, bs}: sx = full-res px per luma px, bs = 8*sx = block size in full-res px.
; Per pixel: candidates = vectors of the 4 nearest block centres + zero; pick the one with the smallest
; |prev(x+d) - cur(x-d)|; mid = average of the two samples, softly falling back to cur(x) where the match is poor.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)
declare <4 x float> @llvm.fabs.v4f32(<4 x float>)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

; box of up to 4 taps at (bx,by),(bx+fx,by),(bx,by+fy),(bx+fx,by+fy): with fx/fy = 0/1 this is an exact
; half-pixel bilinear sample (the mid-frame offset is d_full/2, i.e. a whole or a half pixel)
define internal <4 x float> @tap4(<8 x i32> %t, i32 %bx, i32 %by, i32 %fx, i32 %fy, i32 %wm, i32 %hm) alwaysinline {
  %x1r = add i32 %bx, %fx
  %y1r = add i32 %by, %fy
  %x0a = call i32 @llvm.smax.i32(i32 %bx, i32 0)
  %x0 = call i32 @llvm.smin.i32(i32 %x0a, i32 %wm)
  %x1a = call i32 @llvm.smax.i32(i32 %x1r, i32 0)
  %x1 = call i32 @llvm.smin.i32(i32 %x1a, i32 %wm)
  %y0a = call i32 @llvm.smax.i32(i32 %by, i32 0)
  %y0 = call i32 @llvm.smin.i32(i32 %y0a, i32 %hm)
  %y1a = call i32 @llvm.smax.i32(i32 %y1r, i32 0)
  %y1 = call i32 @llvm.smin.i32(i32 %y1a, i32 %hm)
  %a = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x0, i32 %y0, <8 x i32> %t, i32 0, i32 0)
  %b = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x1, i32 %y0, <8 x i32> %t, i32 0, i32 0)
  %c = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x0, i32 %y1, <8 x i32> %t, i32 0, i32 0)
  %d = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x1, i32 %y1, <8 x i32> %t, i32 0, i32 0)
  %ab = fadd <4 x float> %a, %b
  %cd = fadd <4 x float> %c, %d
  %s = fadd <4 x float> %ab, %cd
  %r = fmul <4 x float> %s, <float 0.25, float 0.25, float 0.25, float 0.25>
  ret <4 x float> %r
}

; returns average colour in xyz and the match error (sum of |a-b| over rgb) in w
define internal <4 x float> @cand(<8 x i32> %tp, <8 x i32> %tc, i32 %x, i32 %y, i32 %pk, i32 %sx, i32 %wm, i32 %hm) alwaysinline {
  %dxs = shl i32 %pk, 16
  %dxl = ashr i32 %dxs, 16
  %dyl = ashr i32 %pk, 16
  %dx = mul i32 %dxl, %sx
  %dy = mul i32 %dyl, %sx
  ; prev sampled at x + d/2, cur at x - d/2 (d = full displacement in px)
  %pdx = ashr i32 %dx, 1
  %pdy = ashr i32 %dy, 1
  %fxp = and i32 %dx, 1
  %fyp = and i32 %dy, 1
  %pbx = add i32 %x, %pdx
  %pby = add i32 %y, %pdy
  %dx1 = add i32 %dx, 1
  %dy1 = add i32 %dy, 1
  %cdx = ashr i32 %dx1, 1
  %cdy = ashr i32 %dy1, 1
  %cbx = sub i32 %x, %cdx
  %cby = sub i32 %y, %cdy
  %a = call <4 x float> @tap4(<8 x i32> %tp, i32 %pbx, i32 %pby, i32 %fxp, i32 %fyp, i32 %wm, i32 %hm)
  %b = call <4 x float> @tap4(<8 x i32> %tc, i32 %cbx, i32 %cby, i32 %fxp, i32 %fyp, i32 %wm, i32 %hm)
  %df = fsub <4 x float> %a, %b
  %af = call <4 x float> @llvm.fabs.v4f32(<4 x float> %df)
  %e0 = extractelement <4 x float> %af, i32 0
  %e1 = extractelement <4 x float> %af, i32 1
  %e2 = extractelement <4 x float> %af, i32 2
  %e01 = fadd float %e0, %e1
  %err = fadd float %e01, %e2
  %s = fadd <4 x float> %a, %b
  %avg = fmul <4 x float> %s, <float 0.5, float 0.5, float 0.5, float 0.5>
  %r = insertelement <4 x float> %avg, float %err, i32 3
  ret <4 x float> %r
}

; cur(x - d/2) only: sharp, no averaging
define internal <4 x float> @curwarp(<8 x i32> %tc, i32 %x, i32 %y, i32 %pk, i32 %sx, i32 %wm, i32 %hm) alwaysinline {
  %dxs = shl i32 %pk, 16
  %dxl = ashr i32 %dxs, 16
  %dyl = ashr i32 %pk, 16
  %dx = mul i32 %dxl, %sx
  %dy = mul i32 %dyl, %sx
  %fxp = and i32 %dx, 1
  %fyp = and i32 %dy, 1
  %dx1 = add i32 %dx, 1
  %dy1 = add i32 %dy, 1
  %cdx = ashr i32 %dx1, 1
  %cdy = ashr i32 %dy1, 1
  %cbx = sub i32 %x, %cdx
  %cby = sub i32 %y, %cdy
  %b = call <4 x float> @tap4(<8 x i32> %tc, i32 %cbx, i32 %cby, i32 %fxp, i32 %fyp, i32 %wm, i32 %hm)
  ret <4 x float> %b
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
  %tp = load <8 x i32>, ptr addrspace(4) %tbl, align 32, !invariant.load !0
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %pc, align 32, !invariant.load !0
  %po = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %po, align 32, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 128
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 192
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %sx = extractelement <4 x i32> %cn, i32 0
  %w = extractelement <4 x i32> %cn, i32 1
  %h = extractelement <4 x i32> %cn, i32 2
  %bs = extractelement <4 x i32> %cn, i32 3
  %wm = sub i32 %w, 1
  %hm = sub i32 %h, 1
  %hbs = lshr i32 %bs, 1
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %x = add i32 %bx, %lx
  %y = add i32 %by, %ly

  ; the four nearest block centres (centres sit at bs*b + bs/2)
  %fx0 = sub i32 %x, %hbs
  %fy0 = sub i32 %y, %hbs
  %fx = call i32 @llvm.smax.i32(i32 %fx0, i32 0)
  %fy = call i32 @llvm.smax.i32(i32 %fy0, i32 0)
  %bx0r = udiv i32 %fx, %bs
  %by0r = udiv i32 %fy, %bs
  %bx1r = add i32 %bx0r, 1
  %by1r = add i32 %by0r, 1
  %bx0 = call i32 @llvm.smin.i32(i32 %bx0r, i32 79)
  %bx1 = call i32 @llvm.smin.i32(i32 %bx1r, i32 79)
  %by0 = call i32 @llvm.smin.i32(i32 %by0r, i32 44)
  %by1 = call i32 @llvm.smin.i32(i32 %by1r, i32 44)
  %r0 = mul i32 %by0, 80
  %r1 = mul i32 %by1, 80
  %i00 = add i32 %r0, %bx0
  %i10 = add i32 %r0, %bx1
  %i01 = add i32 %r1, %bx0
  %i11 = add i32 %r1, %bx1
  %o00 = shl i32 %i00, 2
  %o10 = shl i32 %i10, 2
  %o01 = shl i32 %i01, 2
  %o11 = shl i32 %i11, 2
  %m00 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %o00, i32 0, i32 0)
  %m10 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %o10, i32 0, i32 0)
  %m01 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %o01, i32 0, i32 0)
  %m11 = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %o11, i32 0, i32 0)

  ; one fixed-point step: the vector of the block that CONTAINS this pixel says where the content comes from on the
  ; cur grid (x - d/2); the block found there carries the vector that really belongs to the moving surface
  %kbx0 = udiv i32 %x, %bs
  %kby0 = udiv i32 %y, %bs
  %kbx = call i32 @llvm.smin.i32(i32 %kbx0, i32 79)
  %kby = call i32 @llvm.smin.i32(i32 %kby0, i32 44)
  %krow = mul i32 %kby, 80
  %kidx = add i32 %krow, %kbx
  %koff = shl i32 %kidx, 2
  %mA = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %koff, i32 0, i32 0)
  %aXs = shl i32 %mA, 16
  %aXl = ashr i32 %aXs, 16
  %aYl = ashr i32 %mA, 16
  %aX = mul i32 %aXl, %sx
  %aY = mul i32 %aYl, %sx
  %aX2 = sdiv i32 %aX, 2
  %aY2 = sdiv i32 %aY, 2
  %p1xr = sub i32 %x, %aX2
  %p1yr = sub i32 %y, %aY2
  %p1xa = call i32 @llvm.smax.i32(i32 %p1xr, i32 0)
  %p1x = call i32 @llvm.smin.i32(i32 %p1xa, i32 %wm)
  %p1ya = call i32 @llvm.smax.i32(i32 %p1yr, i32 0)
  %p1y = call i32 @llvm.smin.i32(i32 %p1ya, i32 %hm)
  %qbx0 = udiv i32 %p1x, %bs
  %qby0 = udiv i32 %p1y, %bs
  %qbx = call i32 @llvm.smin.i32(i32 %qbx0, i32 79)
  %qby = call i32 @llvm.smin.i32(i32 %qby0, i32 44)
  %qrow = mul i32 %qby, 80
  %qidx = add i32 %qrow, %qbx
  %qoff = shl i32 %qidx, 2
  %mB = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %qoff, i32 0, i32 0)
  %cB = call <4 x float> @cand(<8 x i32> %tp, <8 x i32> %tc, i32 %x, i32 %y, i32 %mB, i32 %sx, i32 %wm, i32 %hm)
  %wB = extractelement <4 x float> %cB, i32 3

  %c00 = call <4 x float> @cand(<8 x i32> %tp, <8 x i32> %tc, i32 %x, i32 %y, i32 %m00, i32 %sx, i32 %wm, i32 %hm)
  %c10 = call <4 x float> @cand(<8 x i32> %tp, <8 x i32> %tc, i32 %x, i32 %y, i32 %m10, i32 %sx, i32 %wm, i32 %hm)
  %c01 = call <4 x float> @cand(<8 x i32> %tp, <8 x i32> %tc, i32 %x, i32 %y, i32 %m01, i32 %sx, i32 %wm, i32 %hm)
  %c11 = call <4 x float> @cand(<8 x i32> %tp, <8 x i32> %tc, i32 %x, i32 %y, i32 %m11, i32 %sx, i32 %wm, i32 %hm)
  %cz = call <4 x float> @cand(<8 x i32> %tp, <8 x i32> %tc, i32 %x, i32 %y, i32 0, i32 %sx, i32 %wm, i32 %hm)

  %w00 = extractelement <4 x float> %c00, i32 3
  %w10 = extractelement <4 x float> %c10, i32 3
  %w01 = extractelement <4 x float> %c01, i32 3
  %w11 = extractelement <4 x float> %c11, i32 3
  %wz = extractelement <4 x float> %cz, i32 3
  ; best non-zero candidate (the four neighbours + the projected one)
  %l1 = fcmp olt float %w10, %w00
  %b1 = select i1 %l1, <4 x float> %c10, <4 x float> %c00
  %e1 = select i1 %l1, float %w10, float %w00
  %k1 = select i1 %l1, i32 %m10, i32 %m00
  %l2 = fcmp olt float %w01, %e1
  %b2 = select i1 %l2, <4 x float> %c01, <4 x float> %b1
  %e2 = select i1 %l2, float %w01, float %e1
  %k2 = select i1 %l2, i32 %m01, i32 %k1
  %l3 = fcmp olt float %w11, %e2
  %b3 = select i1 %l3, <4 x float> %c11, <4 x float> %b2
  %e3 = select i1 %l3, float %w11, float %e2
  %k3 = select i1 %l3, i32 %m11, i32 %k2
  %l5 = fcmp olt float %wB, %e3
  %bnz = select i1 %l5, <4 x float> %cB, <4 x float> %b3
  %enz = select i1 %l5, float %wB, float %e3
  %knz = select i1 %l5, i32 %mB, i32 %k3
  ; zero motion wins unless a vector is clearly better: static content (UI, text, fades, loading screens)
  ; must never pick up a random vector just because its brightness changes from frame to frame
  %zthr0 = fmul float %wz, 0.6875
  %zthr = fsub float %zthr0, 0.03125
  %usenz = fcmp olt float %enz, %zthr
  %b5 = select i1 %usenz, <4 x float> %bnz, <4 x float> %cz
  %e5 = select i1 %usenz, float %enz, float %wz
  %k5 = select i1 %usenz, i32 %knz, i32 0
  ; static pixel with a plausible brightness change (fade): the average is exact, no fallback needed
  %iszero = xor i1 %usenz, true
  %fade_ok = fcmp olt float %wz, 0.5
  %fade = and i1 %iszero, %fade_ok
  %e6 = select i1 %fade, float 0.0, float %e5

  ; agreement of prev/cur along the chosen vector decides how much to trust the average:
  ;   err <= 0.0625: plain average (denoise);  0.0625..0.1875: fade to cur(x-d/2) only (sharp, no ghost);
  ;   0.3125..0.5625: fade to cur(x) unwarped.
  %cw = call <4 x float> @curwarp(<8 x i32> %tc, i32 %x, i32 %y, i32 %k5, i32 %sx, i32 %wm, i32 %hm)
  %c0 = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %u0 = fsub float %e6, 0.0625
  %u1 = fmul float %u0, 8.0
  %u2 = call float @llvm.maxnum.f32(float %u1, float 0.0)
  %wa = call float @llvm.minnum.f32(float %u2, float 1.0)
  %v0 = fsub float %e6, 0.3125
  %v1 = fmul float %v0, 4.0
  %v2 = call float @llvm.maxnum.f32(float %v1, float 0.0)
  %wb = call float @llvm.minnum.f32(float %v2, float 1.0)
  %wa0 = insertelement <4 x float> undef, float %wa, i32 0
  %wav = shufflevector <4 x float> %wa0, <4 x float> undef, <4 x i32> zeroinitializer
  %wb0 = insertelement <4 x float> undef, float %wb, i32 0
  %wbv = shufflevector <4 x float> %wb0, <4 x float> undef, <4 x i32> zeroinitializer
  %ia = fsub <4 x float> <float 1.0, float 1.0, float 1.0, float 1.0>, %wav
  %ib = fsub <4 x float> <float 1.0, float 1.0, float 1.0, float 1.0>, %wbv
  %p0 = fmul <4 x float> %b5, %ia
  %p1 = fmul <4 x float> %cw, %wav
  %mix1 = fadd <4 x float> %p0, %p1
  %q0 = fmul <4 x float> %mix1, %ib
  %q1 = fmul <4 x float> %c0, %wbv
  %m = fadd <4 x float> %q0, %q1
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %m, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  ret void
}

!0 = !{}

attributes #0 = { "denormal-fp-math-f32"="preserve-sign,preserve-sign" }
