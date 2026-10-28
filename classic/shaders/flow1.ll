; user_sgprs=2
; table: +0x60 V#lumaCur, +0x70 V#lumaPrev, +0x90 V#candSad, +0xA0 V#candK.
; thread = block*8 + g : search around the predictor from the coarse (160x90) level: 7x7 candidates, step 2 luma px,
; candidates k = g, g+8, ...
; best (sad,k) per (block,g) is written for the reduction pass. Block = 8x8 of the 640x360 luma, 16 sample SAD.
target triple = "amdgcn--"
declare float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.f32(float, <4 x i32>, i32, i32, i32 immarg)
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

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %ps = getelementptr i8, ptr addrspace(4) %tbl, i64 144
  %vs = load <4 x i32>, ptr addrspace(4) %ps, align 16, !invariant.load !0
  %pk = getelementptr i8, ptr addrspace(4) %tbl, i64 160
  %vk = load <4 x i32>, ptr addrspace(4) %pk, align 16, !invariant.load !0
  %pco = getelementptr i8, ptr addrspace(4) %tbl, i64 176
  %vco = load <4 x i32>, ptr addrspace(4) %pco, align 16, !invariant.load !0
  %gb = shl i32 %gx, 6
  %t = add i32 %gb, %tid
  %b = lshr i32 %t, 3
  %g = and i32 %t, 7
  %ok = icmp ult i32 %b, 3600
  br i1 %ok, label %start, label %exit

start:
  %bx = urem i32 %b, 80
  %by = udiv i32 %b, 80
  %x0 = shl i32 %bx, 3
  %y0 = shl i32 %by, 3
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
  br label %cand

cand:
  %k = phi i32 [ %g, %start ], [ %k1, %cand ]
  %best = phi float [ 1.073741824e+09, %start ], [ %best1, %cand ]
  %bestk = phi i32 [ %g, %start ], [ %bestk1, %cand ]
  %kdy = udiv i32 %k, 7
  %kdx = urem i32 %k, 7
  %dyc = sub i32 %kdy, 3
  %dxc = sub i32 %kdx, 3
  %dyo = shl i32 %dyc, 1
  %dxo = shl i32 %dxc, 1
  %dy = add i32 %dyo, %pdy
  %dx = add i32 %dxo, %pdx
  %s = call float @sad16(<4 x i32> %vc, <4 x i32> %vp, i32 %x0, i32 %y0, i32 %dx, i32 %dy)
  %better = fcmp olt float %s, %best
  %best1 = select i1 %better, float %s, float %best
  %bestk1 = select i1 %better, i32 %k, i32 %bestk
  %k1 = add i32 %k, 8
  %more = icmp ult i32 %k1, 49
  br i1 %more, label %cand, label %done

done:
  %o = shl i32 %t, 2
  call void @llvm.amdgcn.raw.buffer.store.f32(float %best1, <4 x i32> %vs, i32 %o, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %bestk1, <4 x i32> %vk, i32 %o, i32 0, i32 0)
  br label %exit

exit:
  ret void
}

!0 = !{}

attributes #0 = { "denormal-fp-math-f32"="preserve-sign,preserve-sign" }
