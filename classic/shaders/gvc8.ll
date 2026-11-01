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

@red = internal addrspace(3) global [256 x i32] undef, align 16

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
  %p = getelementptr [256 x i32], ptr addrspace(3) @red, i32 0, i32 %me
  store i32 %v, ptr addrspace(3) %p, align 4
  call void @bar()
  %is0 = icmp eq i32 %tid, 0
  br i1 %is0, label %loop, label %done
loop:
  %i = phi i32 [ 0, %entry ], [ %i1, %loop ]
  %s = phi i32 [ 0, %entry ], [ %s1, %loop ]
  %ix = add i32 %k64, %i
  %q = getelementptr [256 x i32], ptr addrspace(3) @red, i32 0, i32 %ix
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
  %dx1 = add i32 %px, 1
  %sx1 = call i32 @src(i32 %dx1, float %sx)
  %dx2 = add i32 %px, 2
  %sx2 = call i32 @src(i32 %dx2, float %sx)
  %dx3 = add i32 %px, 3
  %sx3 = call i32 @src(i32 %dx3, float %sx)
  %dy2 = add i32 %py, 2
  %sy2 = call i32 @src(i32 %dy2, float %sy)
  ; centre = sample (2, 2); outlier = argmax of the squared distance to it; depth: min / max of .x
  %c = call <2 x float> @smp(<8 x i32> %ts, i32 %sx2, i32 %sy2, i1 %zw)
  br label %rloop

rloop:
  %ry = phi i32 [ 0, %entry ], [ %ry1, %rloop ]
  %o_in = phi <2 x float> [ %c, %entry ], [ %o3, %rloop ]
  %m_in = phi float [ 0.0, %entry ], [ %m3, %rloop ]
  %mn_in = phi float [ 0.0, %entry ], [ %mn3, %rloop ]
  %mx_in = phi float [ 0.0, %entry ], [ %mx3, %rloop ]
  %dy = add i32 %py, %ry
  %sy_r = call i32 @src(i32 %dy, float %sy)
  %s0 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx0, i32 %sy_r, i1 %zw)
  %s1 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx1, i32 %sy_r, i1 %zw)
  %s2 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx2, i32 %sy_r, i1 %zw)
  %s3 = call <2 x float> @smp(<8 x i32> %ts, i32 %sx3, i32 %sy_r, i1 %zw)
  %r0 = icmp eq i32 %ry, 0
  %e0 = call float @d2(<2 x float> %s0, <2 x float> %c)
  %g0 = fcmp ogt float %e0, %m_in
  %o0 = select i1 %g0, <2 x float> %s0, <2 x float> %o_in
  %m0 = select i1 %g0, float %e0, float %m_in
  %z0 = extractelement <2 x float> %s0, i32 0
  %mn0a = call float @llvm.minnum.f32(float %mn_in, float %z0)
  %mx0a = call float @llvm.maxnum.f32(float %mx_in, float %z0)
  %mn0 = select i1 %r0, float %z0, float %mn0a
  %mx0 = select i1 %r0, float %z0, float %mx0a
  %e1 = call float @d2(<2 x float> %s1, <2 x float> %c)
  %g1 = fcmp ogt float %e1, %m0
  %o1 = select i1 %g1, <2 x float> %s1, <2 x float> %o0
  %m1 = select i1 %g1, float %e1, float %m0
  %z1 = extractelement <2 x float> %s1, i32 0
  %mn1 = call float @llvm.minnum.f32(float %mn0, float %z1)
  %mx1 = call float @llvm.maxnum.f32(float %mx0, float %z1)
  %e2 = call float @d2(<2 x float> %s2, <2 x float> %c)
  %g2 = fcmp ogt float %e2, %m1
  %o2 = select i1 %g2, <2 x float> %s2, <2 x float> %o1
  %m2 = select i1 %g2, float %e2, float %m1
  %z2 = extractelement <2 x float> %s2, i32 0
  %mn2 = call float @llvm.minnum.f32(float %mn1, float %z2)
  %mx2 = call float @llvm.maxnum.f32(float %mx1, float %z2)
  %e3 = call float @d2(<2 x float> %s3, <2 x float> %c)
  %g3 = fcmp ogt float %e3, %m2
  %o3 = select i1 %g3, <2 x float> %s3, <2 x float> %o2
  %m3 = select i1 %g3, float %e3, float %m2
  %z3 = extractelement <2 x float> %s3, i32 0
  %mn3 = call float @llvm.minnum.f32(float %mn2, float %z3)
  %mx3 = call float @llvm.maxnum.f32(float %mx2, float %z3)
  %ry1 = add i32 %ry, 1
  %rmore = icmp ult i32 %ry1, 4
  br i1 %rmore, label %rloop, label %rdone

rdone:
  %ox = extractelement <2 x float> %o3, i32 0
  %oy = extractelement <2 x float> %o3, i32 1
  %ccx = extractelement <2 x float> %c, i32 0
  %ccy = extractelement <2 x float> %c, i32 1
  ; values that go into the statistics: centre (velocity) or min / max (depth)
  %hvx = select i1 %isdepth, float %mn3, float %ccx
  %hvy = select i1 %isdepth, float %mx3, float %ccy
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
  %zo0 = insertelement <2 x float> undef, float %mn3, i32 0
  %zo1 = insertelement <2 x float> %zo0, float %mx3, i32 1
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %zo1, <4 x i32> %vo, i32 %o8, i32 0, i32 0)
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
  ; stats[3]: active non-static cells (neither 0.0 nor UNORM 0.5 bias within +-1/255, and finite)
  %ahx = call float @llvm.fabs.f32(float %hvx)
  %ahy = call float @llvm.fabs.f32(float %hvy)
  %uhx0 = fsub float %hvx, 0.5
  %uhy0 = fsub float %hvy, 0.5
  %uhx = call float @llvm.fabs.f32(float %uhx0)
  %uhy = call float @llvm.fabs.f32(float %uhy0)
  %actx0 = fcmp ogt float %ahx, 0x3EE4F8B580000000 ; 1e-5
  %actx1 = fcmp ogt float %uhx, 0x3F70624DE0000000 ; 0.004
  %actx = and i1 %actx0, %actx1
  %acty0 = fcmp ogt float %ahy, 0x3EE4F8B580000000 ; 1e-5
  %acty1 = fcmp ogt float %uhy, 0x3F70624DE0000000 ; 0.004
  %acty = and i1 %acty0, %acty1
  %act0 = or i1 %actx, %acty
  %fin = icmp eq i32 %if0, 0
  %act1 = and i1 %act0, %fin
  %act2 = and i1 %act1, %in
  %act = zext i1 %act2 to i32

  call void @sum(i32 %tid, i32 %hz, i32 0, <4 x i32> %vs)
  call void @sum(i32 %tid, i32 %nz, i32 1, <4 x i32> %vs)
  call void @sum(i32 %tid, i32 %if, i32 2, <4 x i32> %vs)
  call void @sum(i32 %tid, i32 %act, i32 3, <4 x i32> %vs)
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" "denormal-fp-math-f32"="preserve-sign,preserve-sign" }
!0 = !{}
