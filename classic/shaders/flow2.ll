; user_sgprs=2
; table: +0x60 V#lumaCur, +0x70 V#lumaPrev, +0x80 V#mv, +0x90 V#candSad, +0xA0 V#candK.
; thread = block: pick the best coarse candidate of the 8 groups, refine over +-1, compare with zero motion,
; store packed (dx & 0xffff) | dy << 16. Candidates are relative to the coarse-level predictor (7x7, step 2).
target triple = "amdgcn--"
declare float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare float @llvm.fabs.f32(float)
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)

define internal float @sad16(<4 x i32> %vc, <4 x i32> %vp, i32 %x0, i32 %y0, i32 %dx, i32 %dy) alwaysinline {
entry:
  br label %loop
loop:
  %j = phi i32 [ 0, %entry ], [ %j1, %loop ]
  %sad = phi float [ 0.0, %entry ], [ %sad1, %loop ]
  %jxx = and i32 %j, 3
  %jx = shl i32 %jxx, 1
  %jyy = lshr i32 %j, 2
  %jy = shl i32 %jyy, 1
  %cx = add i32 %x0, %jx
  %cy = add i32 %y0, %jy
  %pxr = add i32 %cx, %dx
  %pyr = add i32 %cy, %dy
  %pxa = call i32 @llvm.smax.i32(i32 %pxr, i32 0)
  %px = call i32 @llvm.smin.i32(i32 %pxa, i32 639)
  %pya = call i32 @llvm.smax.i32(i32 %pyr, i32 0)
  %py = call i32 @llvm.smin.i32(i32 %pya, i32 359)
  %crow = mul i32 %cy, 640
  %cidx = add i32 %crow, %cx
  %coff = shl i32 %cidx, 2
  %prow = mul i32 %py, 640
  %pidx = add i32 %prow, %px
  %poff = shl i32 %pidx, 2
  %c = call float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32> %vc, i32 %coff, i32 0, i32 0)
  %p = call float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32> %vp, i32 %poff, i32 0, i32 0)
  %d = fsub float %c, %p
  %ad = call float @llvm.fabs.f32(float %d)
  %sad1 = fadd float %sad, %ad
  %j1 = add i32 %j, 1
  %more = icmp ult i32 %j1, 16
  br i1 %more, label %loop, label %done
done:
  ret float %sad1
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 128
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %ps = getelementptr i8, ptr addrspace(4) %tbl, i64 144
  %vs = load <4 x i32>, ptr addrspace(4) %ps, align 16, !invariant.load !0
  %pk = getelementptr i8, ptr addrspace(4) %tbl, i64 160
  %vk = load <4 x i32>, ptr addrspace(4) %pk, align 16, !invariant.load !0
  %pco = getelementptr i8, ptr addrspace(4) %tbl, i64 176
  %vco = load <4 x i32>, ptr addrspace(4) %pco, align 16, !invariant.load !0
  %gb = shl i32 %gx, 6
  %b = add i32 %gb, %tid
  %ok = icmp ult i32 %b, 3600
  br i1 %ok, label %start, label %exit

start:
  %bx = urem i32 %b, 80
  %by = udiv i32 %b, 80
  %x0 = shl i32 %bx, 3
  %y0 = shl i32 %by, 3
  %base = shl i32 %b, 3
  %cbx = lshr i32 %bx, 2
  %cby = lshr i32 %by, 2
  %crow = mul i32 %cby, 20
  %cidx = add i32 %crow, %cbx
  %coff = shl i32 %cidx, 2
  %cpk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vco, i32 %coff, i32 0, i32 0)
  %pdxs = shl i32 %cpk, 16
  %pdx0 = ashr i32 %pdxs, 16
  %pdy0 = ashr i32 %cpk, 16
  %pdx = shl i32 %pdx0, 2
  %pdy = shl i32 %pdy0, 2
  br label %red

red:
  %g = phi i32 [ 0, %start ], [ %g1, %red ]
  %best = phi float [ 1.073741824e+09, %start ], [ %best1, %red ]
  %bestk = phi i32 [ 24, %start ], [ %bestk1, %red ]
  %ix = add i32 %base, %g
  %o = shl i32 %ix, 2
  %sd = call float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32> %vs, i32 %o, i32 0, i32 0)
  %kk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vk, i32 %o, i32 0, i32 0)
  %better = fcmp olt float %sd, %best
  %best1 = select i1 %better, float %sd, float %best
  %bestk1 = select i1 %better, i32 %kk, i32 %bestk
  %g1 = add i32 %g, 1
  %gm = icmp ult i32 %g1, 8
  br i1 %gm, label %red, label %refine0

refine0:
  %ky = udiv i32 %bestk1, 7
  %kx = urem i32 %bestk1, 7
  %cyc = sub i32 %ky, 3
  %cxc = sub i32 %kx, 3
  %cyo = shl i32 %cyc, 1
  %cxo = shl i32 %cxc, 1
  %cy0 = add i32 %cyo, %pdy
  %cx0 = add i32 %cxo, %pdx
  br label %ref

ref:
  %r = phi i32 [ 0, %refine0 ], [ %r1, %ref ]
  %rb = phi float [ 1.073741824e+09, %refine0 ], [ %rb1, %ref ]
  %rdx = phi i32 [ 0, %refine0 ], [ %rdx1, %ref ]
  %rdy = phi i32 [ 0, %refine0 ], [ %rdy1, %ref ]
  %ry = udiv i32 %r, 3
  %rx = urem i32 %r, 3
  %oy = sub i32 %ry, 1
  %ox = sub i32 %rx, 1
  %ddx = add i32 %cx0, %ox
  %ddy = add i32 %cy0, %oy
  %rs = call float @sad16(<4 x i32> %vc, <4 x i32> %vp, i32 %x0, i32 %y0, i32 %ddx, i32 %ddy)
  %rbt = fcmp olt float %rs, %rb
  %rb1 = select i1 %rbt, float %rs, float %rb
  %rdx1 = select i1 %rbt, i32 %ddx, i32 %rdx
  %rdy1 = select i1 %rbt, i32 %ddy, i32 %rdy
  %r1 = add i32 %r, 1
  %rm = icmp ult i32 %r1, 9
  br i1 %rm, label %ref, label %fin

fin:
  %zs = call float @sad16(<4 x i32> %vc, <4 x i32> %vp, i32 %x0, i32 %y0, i32 0, i32 0)
  %thr = fmul float %zs, 0.875
  %use = fcmp olt float %rb1, %thr
  %fdx = select i1 %use, i32 %rdx1, i32 0
  %fdy = select i1 %use, i32 %rdy1, i32 0
  %dxm = and i32 %fdx, 65535
  %dys = shl i32 %fdy, 16
  %pack = or i32 %dxm, %dys
  %moff = shl i32 %b, 2
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pack, <4 x i32> %vm, i32 %moff, i32 0, i32 0)
  br label %exit

exit:
  ret void
}

!0 = !{}
