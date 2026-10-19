; user_sgprs=2
; tgid_y=1
; velocity subsystem, capture pass: read a game render target (velocity or depth candidate, any tiled colour /
; depth format, through a T# built from its CB / DB registers) and reduce it to OUR 480 x 270 grid of 4x4
; display-pixel cells. One lane per cell, dispatch (60, 34) groups of 8x8 lanes (480 x 272, the last 2 rows idle).
; Runs either spliced into the game's command stream right before the target's memory is reused, or at the end of
; the frame in our DCB (see gv.c). Source pixel of display pixel p: floor((p + 0.5) * s), s = source size / display
; size (+0x40), so full, half and dynamic resolution all map onto the same grid. image_load returns the value
; converted by the T#'s number format (FLOAT / SNORM / UNORM...), untouched texels outside the T# read as 0.
; mode (+0x40 dword 0):
;   0 velocity: 4x4 samples, out = {centre (2,2), the sample farthest from it} (2 x float2 = 16 bytes per cell):
;     the centre is unbiased, the outlier keeps objects thinner than a cell (fence bars, a sword) for warp8g
;   1 depth: out = {min, max} of the 16 samples (.x channel) (8 bytes per cell); near = max with reversed Z
;   2 velocity, statistics only (no store): used to compare a spliced copy with the end-of-frame copy
; swz (+0x44): 0 = channels xy, 1 = channels zw (velocity in the second half of an RGBA16 target)
; statistics (+0x30 V#, 16 bytes, atomic add, zeroed by the CPU before the frame): {position-mixed hash of the cell
; values, cells with a non-zero centre, cells with a non-finite value, unused}. Hash = sum over cells of
; (bits(c.x) * 0x9E3779B1 + bits(c.y) * 0x85EBCA77 + (index + 1) * 0xC2B2AE3D) mod 2^32: order independent (atomic
; adds in any order give the same sum) but position and value sensitive.
; table: +0x00 T#src, +0x20 V#out, +0x30 V#stats, +0x40 {mode, swz, sx (float), sy (float)}
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v4f32(<4 x float>, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.s.barrier()
declare float @llvm.floor.f32(float)
declare float @llvm.fabs.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

@red = internal addrspace(3) global [192 x i32] undef, align 16

define internal void @bar() alwaysinline {
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ret void
}

; source coordinate of display pixel p: floor((p + 0.5) * s)
define internal i32 @src(i32 %p, float %s) alwaysinline {
  %f = uitofp i32 %p to float
  %h = fadd float %f, 0.5
  %m = fmul float %h, %s
  %fl = call float @llvm.floor.f32(float %m)
  %i = fptosi float %fl to i32
  ret i32 %i
}

; one sample: channels xy or zw
define internal <2 x float> @smp(<8 x i32> %t, i32 %x, i32 %y, i1 %zw) alwaysinline {
  %v = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %t, i32 0, i32 0)
  %a = extractelement <4 x float> %v, i32 0
  %b = extractelement <4 x float> %v, i32 1
  %c = extractelement <4 x float> %v, i32 2
  %d = extractelement <4 x float> %v, i32 3
  %p = select i1 %zw, float %c, float %a
  %q = select i1 %zw, float %d, float %b
  %r0 = insertelement <2 x float> undef, float %p, i32 0
  %r = insertelement <2 x float> %r0, float %q, i32 1
  ret <2 x float> %r
}

; squared distance of two samples (raw units; NaN-safe: NaN compares false and never becomes the outlier)
define internal float @d2(<2 x float> %a, <2 x float> %b) alwaysinline {
  %d = fsub <2 x float> %a, %b
  %x = extractelement <2 x float> %d, i32 0
  %y = extractelement <2 x float> %d, i32 1
  %xx = fmul float %x, %x
  %yy = fmul float %y, %y
  %s = fadd float %xx, %yy
  ret float %s
}

define internal i32 @isinf(float %x) alwaysinline {
  %a = call float @llvm.fabs.f32(float %x)
  %f = fcmp olt float %a, 0x47EFFFFFE0000000
  %r = select i1 %f, i32 0, i32 1
  ret i32 %r
}

; sum of the 64 lanes' values (single-wave group: barriers compile away), lane 0 adds it to stats[k]
define internal void @sum(i32 %tid, i32 %v, i32 %k, <4 x i32> %vs) alwaysinline {
entry:
  %k64 = shl i32 %k, 6
  %me = add i32 %k64, %tid
  %p = getelementptr [192 x i32], ptr addrspace(3) @red, i32 0, i32 %me
  store i32 %v, ptr addrspace(3) %p, align 4
  call void @bar()
  %is0 = icmp eq i32 %tid, 0
  br i1 %is0, label %loop, label %done
loop:
  %i = phi i32 [ 0, %entry ], [ %i1, %loop ]
  %s = phi i32 [ 0, %entry ], [ %s1, %loop ]
  %ix = add i32 %k64, %i
  %q = getelementptr [192 x i32], ptr addrspace(3) @red, i32 0, i32 %ix
  %x = load i32, ptr addrspace(3) %q, align 4
  %s1 = add i32 %s, %x
  %i1 = add i32 %i, 1
  %more = icmp ult i32 %i1, 64
  br i1 %more, label %loop, label %add
add:
  %off = shl i32 %k, 2
  %o = call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 %s1, <4 x i32> %vs, i32 %off, i32 0, i32 0)
  br label %done
done:
  call void @bar()
  ret void
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %ts = load <8 x i32>, ptr addrspace(4) %tbl, align 32, !invariant.load !0
  %po = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %vo = load <4 x i32>, ptr addrspace(4) %po, align 16, !invariant.load !0
  %pst = getelementptr i8, ptr addrspace(4) %tbl, i64 48
  %vs = load <4 x i32>, ptr addrspace(4) %pst, align 16, !invariant.load !0
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %cn = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %mode = extractelement <4 x i32> %cn, i32 0
  %swz = extractelement <4 x i32> %cn, i32 1
  %sxb = extractelement <4 x i32> %cn, i32 2
  %syb = extractelement <4 x i32> %cn, i32 3
  %sx = bitcast i32 %sxb to float
  %sy = bitcast i32 %syb to float
  %zw = icmp ne i32 %swz, 0
  %isdepth = icmp eq i32 %mode, 1
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %gx8 = shl i32 %gx, 3
  %gy8 = shl i32 %gy, 3
  %cx = add i32 %gx8, %lx
  %cy = add i32 %gy8, %ly
  %inx = icmp ult i32 %cx, 480
  %iny = icmp ult i32 %cy, 270
  %in = and i1 %inx, %iny
  %px = shl i32 %cx, 2
  %py = shl i32 %cy, 2

  %dx0 = add i32 %px, 0
  %sx0 = call i32 @src(i32 %dx0, float %sx)
  %dy0 = add i32 %py, 0
  %sy0 = call i32 @src(i32 %dy0, float %sy)
  %dx1 = add i32 %px, 1
  %sx1 = call i32 @src(i32 %dx1, float %sx)
  %dy1 = add i32 %py, 1
  %sy1 = call i32 @src(i32 %dy1, float %sy)
  %dx2 = add i32 %px, 2
  %sx2 = call i32 @src(i32 %dx2, float %sx)
  %dy2 = add i32 %py, 2
  %sy2 = call i32 @src(i32 %dy2, float %sy)
  %dx3 = add i32 %px, 3
  %sx3 = call i32 @src(i32 %dx3, float %sx)
  %dy3 = add i32 %py, 3
  %sy3 = call i32 @src(i32 %dy3, float %sy)
  %s00 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx0, i32 %sy0, i1 %zw)
  %s10 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx1, i32 %sy0, i1 %zw)
  %s20 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx2, i32 %sy0, i1 %zw)
  %s30 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx3, i32 %sy0, i1 %zw)
  %s01 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx0, i32 %sy1, i1 %zw)
  %s11 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx1, i32 %sy1, i1 %zw)
  %s21 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx2, i32 %sy1, i1 %zw)
  %s31 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx3, i32 %sy1, i1 %zw)
  %s02 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx0, i32 %sy2, i1 %zw)
  %s12 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx1, i32 %sy2, i1 %zw)
  %s22 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx2, i32 %sy2, i1 %zw)
  %s32 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx3, i32 %sy2, i1 %zw)
  %s03 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx0, i32 %sy3, i1 %zw)
  %s13 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx1, i32 %sy3, i1 %zw)
  %s23 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx2, i32 %sy3, i1 %zw)
  %s33 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx3, i32 %sy3, i1 %zw)
  ; centre = sample (2, 2); outlier = argmax of the squared distance to it; depth: min / max of .x
  %c = shufflevector <2 x float> %s22, <2 x float> undef, <2 x i32> <i32 0, i32 1>
  %e00 = call float @d2(<2 x float> %s00, <2 x float> %c)
  %g00 = fcmp ogt float %e00, 0.0
  %o00 = select i1 %g00, <2 x float> %s00, <2 x float> %c
  %m00 = select i1 %g00, float %e00, float 0.0
  %z00 = extractelement <2 x float> %s00, i32 0
  %mn00 = fadd float %z00, 0.0
  %mx00 = fadd float %z00, 0.0
  %e10 = call float @d2(<2 x float> %s10, <2 x float> %c)
  %g10 = fcmp ogt float %e10, %m00
  %o10 = select i1 %g10, <2 x float> %s10, <2 x float> %o00
  %m10 = select i1 %g10, float %e10, float %m00
  %z10 = extractelement <2 x float> %s10, i32 0
  %mn10 = call float @llvm.minnum.f32(float %mn00, float %z10)
  %mx10 = call float @llvm.maxnum.f32(float %mx00, float %z10)
  %e20 = call float @d2(<2 x float> %s20, <2 x float> %c)
  %g20 = fcmp ogt float %e20, %m10
  %o20 = select i1 %g20, <2 x float> %s20, <2 x float> %o10
  %m20 = select i1 %g20, float %e20, float %m10
  %z20 = extractelement <2 x float> %s20, i32 0
  %mn20 = call float @llvm.minnum.f32(float %mn10, float %z20)
  %mx20 = call float @llvm.maxnum.f32(float %mx10, float %z20)
  %e30 = call float @d2(<2 x float> %s30, <2 x float> %c)
  %g30 = fcmp ogt float %e30, %m20
  %o30 = select i1 %g30, <2 x float> %s30, <2 x float> %o20
  %m30 = select i1 %g30, float %e30, float %m20
  %z30 = extractelement <2 x float> %s30, i32 0
  %mn30 = call float @llvm.minnum.f32(float %mn20, float %z30)
  %mx30 = call float @llvm.maxnum.f32(float %mx20, float %z30)
  %e01 = call float @d2(<2 x float> %s01, <2 x float> %c)
  %g01 = fcmp ogt float %e01, %m30
  %o01 = select i1 %g01, <2 x float> %s01, <2 x float> %o30
  %m01 = select i1 %g01, float %e01, float %m30
  %z01 = extractelement <2 x float> %s01, i32 0
  %mn01 = call float @llvm.minnum.f32(float %mn30, float %z01)
  %mx01 = call float @llvm.maxnum.f32(float %mx30, float %z01)
  %e11 = call float @d2(<2 x float> %s11, <2 x float> %c)
  %g11 = fcmp ogt float %e11, %m01
  %o11 = select i1 %g11, <2 x float> %s11, <2 x float> %o01
  %m11 = select i1 %g11, float %e11, float %m01
  %z11 = extractelement <2 x float> %s11, i32 0
  %mn11 = call float @llvm.minnum.f32(float %mn01, float %z11)
  %mx11 = call float @llvm.maxnum.f32(float %mx01, float %z11)
  %e21 = call float @d2(<2 x float> %s21, <2 x float> %c)
  %g21 = fcmp ogt float %e21, %m11
  %o21 = select i1 %g21, <2 x float> %s21, <2 x float> %o11
  %m21 = select i1 %g21, float %e21, float %m11
  %z21 = extractelement <2 x float> %s21, i32 0
  %mn21 = call float @llvm.minnum.f32(float %mn11, float %z21)
  %mx21 = call float @llvm.maxnum.f32(float %mx11, float %z21)
  %e31 = call float @d2(<2 x float> %s31, <2 x float> %c)
  %g31 = fcmp ogt float %e31, %m21
  %o31 = select i1 %g31, <2 x float> %s31, <2 x float> %o21
  %m31 = select i1 %g31, float %e31, float %m21
  %z31 = extractelement <2 x float> %s31, i32 0
  %mn31 = call float @llvm.minnum.f32(float %mn21, float %z31)
  %mx31 = call float @llvm.maxnum.f32(float %mx21, float %z31)
  %e02 = call float @d2(<2 x float> %s02, <2 x float> %c)
  %g02 = fcmp ogt float %e02, %m31
  %o02 = select i1 %g02, <2 x float> %s02, <2 x float> %o31
  %m02 = select i1 %g02, float %e02, float %m31
  %z02 = extractelement <2 x float> %s02, i32 0
  %mn02 = call float @llvm.minnum.f32(float %mn31, float %z02)
  %mx02 = call float @llvm.maxnum.f32(float %mx31, float %z02)
  %e12 = call float @d2(<2 x float> %s12, <2 x float> %c)
  %g12 = fcmp ogt float %e12, %m02
  %o12 = select i1 %g12, <2 x float> %s12, <2 x float> %o02
  %m12 = select i1 %g12, float %e12, float %m02
  %z12 = extractelement <2 x float> %s12, i32 0
  %mn12 = call float @llvm.minnum.f32(float %mn02, float %z12)
  %mx12 = call float @llvm.maxnum.f32(float %mx02, float %z12)
  %e22 = call float @d2(<2 x float> %s22, <2 x float> %c)
  %g22 = fcmp ogt float %e22, %m12
  %o22 = select i1 %g22, <2 x float> %s22, <2 x float> %o12
  %m22 = select i1 %g22, float %e22, float %m12
  %z22 = extractelement <2 x float> %s22, i32 0
  %mn22 = call float @llvm.minnum.f32(float %mn12, float %z22)
  %mx22 = call float @llvm.maxnum.f32(float %mx12, float %z22)
  %e32 = call float @d2(<2 x float> %s32, <2 x float> %c)
  %g32 = fcmp ogt float %e32, %m22
  %o32 = select i1 %g32, <2 x float> %s32, <2 x float> %o22
  %m32 = select i1 %g32, float %e32, float %m22
  %z32 = extractelement <2 x float> %s32, i32 0
  %mn32 = call float @llvm.minnum.f32(float %mn22, float %z32)
  %mx32 = call float @llvm.maxnum.f32(float %mx22, float %z32)
  %e03 = call float @d2(<2 x float> %s03, <2 x float> %c)
  %g03 = fcmp ogt float %e03, %m32
  %o03 = select i1 %g03, <2 x float> %s03, <2 x float> %o32
  %m03 = select i1 %g03, float %e03, float %m32
  %z03 = extractelement <2 x float> %s03, i32 0
  %mn03 = call float @llvm.minnum.f32(float %mn32, float %z03)
  %mx03 = call float @llvm.maxnum.f32(float %mx32, float %z03)
  %e13 = call float @d2(<2 x float> %s13, <2 x float> %c)
  %g13 = fcmp ogt float %e13, %m03
  %o13 = select i1 %g13, <2 x float> %s13, <2 x float> %o03
  %m13 = select i1 %g13, float %e13, float %m03
  %z13 = extractelement <2 x float> %s13, i32 0
  %mn13 = call float @llvm.minnum.f32(float %mn03, float %z13)
  %mx13 = call float @llvm.maxnum.f32(float %mx03, float %z13)
  %e23 = call float @d2(<2 x float> %s23, <2 x float> %c)
  %g23 = fcmp ogt float %e23, %m13
  %o23 = select i1 %g23, <2 x float> %s23, <2 x float> %o13
  %m23 = select i1 %g23, float %e23, float %m13
  %z23 = extractelement <2 x float> %s23, i32 0
  %mn23 = call float @llvm.minnum.f32(float %mn13, float %z23)
  %mx23 = call float @llvm.maxnum.f32(float %mx13, float %z23)
  %e33 = call float @d2(<2 x float> %s33, <2 x float> %c)
  %g33 = fcmp ogt float %e33, %m23
  %o33 = select i1 %g33, <2 x float> %s33, <2 x float> %o23
  %m33 = select i1 %g33, float %e33, float %m23
  %z33 = extractelement <2 x float> %s33, i32 0
  %mn33 = call float @llvm.minnum.f32(float %mn23, float %z33)
  %mx33 = call float @llvm.maxnum.f32(float %mx23, float %z33)
  %ox = extractelement <2 x float> %o33, i32 0
  %oy = extractelement <2 x float> %o33, i32 1
  %ccx = extractelement <2 x float> %c, i32 0
  %ccy = extractelement <2 x float> %c, i32 1
  ; values that go into the statistics: centre (velocity) or min / max (depth)
  %hvx = select i1 %isdepth, float %mn33, float %ccx
  %hvy = select i1 %isdepth, float %mx33, float %ccy
  br i1 %in, label %store, label %stats
store:
  %row = mul i32 %cy, 480
  %idx = add i32 %row, %cx
  %isvel = icmp eq i32 %mode, 0
  br i1 %isvel, label %stv, label %std
stv:
  %o16 = shl i32 %idx, 4
  %w0 = insertelement <4 x float> undef, float %ccx, i32 0
  %w1 = insertelement <4 x float> %w0, float %ccy, i32 1
  %w2 = insertelement <4 x float> %w1, float %ox, i32 2
  %w3 = insertelement <4 x float> %w2, float %oy, i32 3
  call void @llvm.amdgcn.raw.buffer.store.v4f32(<4 x float> %w3, <4 x i32> %vo, i32 %o16, i32 0, i32 0)
  br label %stats
std:
  br i1 %isdepth, label %stz, label %stats
stz:
  %o8 = shl i32 %idx, 3
  %z0 = insertelement <2 x float> undef, float %mn33, i32 0
  %z1 = insertelement <2 x float> %z0, float %mx33, i32 1
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %z1, <4 x i32> %vo, i32 %o8, i32 0, i32 0)
  br label %stats
stats:
  %hbx = bitcast float %hvx to i32
  %hby = bitcast float %hvy to i32
  %h1 = mul i32 %hbx, -1640531535
  %h2 = mul i32 %hby, -2048144777
  %row2 = mul i32 %cy, 480
  %idx2 = add i32 %row2, %cx
  %idx3 = add i32 %idx2, 1
  %h3 = mul i32 %idx3, -1028477379
  %h12 = add i32 %h1, %h2
  %h = add i32 %h12, %h3
  %hz = select i1 %in, i32 %h, i32 0
  %nzx = fcmp one float %hvx, 0.0
  %nzy = fcmp one float %hvy, 0.0
  %nz0 = or i1 %nzx, %nzy
  %nz1 = and i1 %nz0, %in
  %nz = zext i1 %nz1 to i32
  %ifx = call i32 @isinf(float %hvx)
  %ify = call i32 @isinf(float %hvy)
  %if0 = or i32 %ifx, %ify
  %if = select i1 %in, i32 %if0, i32 0

  call void @sum(i32 %tid, i32 %hz, i32 0, <4 x i32> %vs)
  call void @sum(i32 %tid, i32 %nz, i32 1, <4 x i32> %vs)
  call void @sum(i32 %tid, i32 %if, i32 2, <4 x i32> %vs)
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
