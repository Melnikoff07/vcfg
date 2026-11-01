; user_sgprs=2
; table: +0xD0 V#l4Cur, +0xE0 V#l4Prev, +0x90 V#candSad, +0xA0 V#candK, +0xB0 V#coarseMV (20x12).
; thread = block: min over the 8 partial results, zero-motion bias, store packed vector (units of the 160x90 level).
target triple = "amdgcn--"
declare float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)

define internal float @sad16(<4 x i32> %vc, <4 x i32> %vp, i32 %x0, i32 %y0, i32 %dx, i32 %dy) alwaysinline {
  ret float 0.0
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 208
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 224
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %ps = getelementptr i8, ptr addrspace(4) %tbl, i64 144
  %vs = load <4 x i32>, ptr addrspace(4) %ps, align 16, !invariant.load !0
  %pk = getelementptr i8, ptr addrspace(4) %tbl, i64 160
  %vk = load <4 x i32>, ptr addrspace(4) %pk, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 176
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %gb = shl i32 %gx, 6
  %b = add i32 %gb, %tid
  %ok = icmp ult i32 %b, 240
  br i1 %ok, label %start, label %exit

start:
  %x0 = urem i32 %b, 20
  %y0 = udiv i32 %b, 20
  %base = shl i32 %b, 3
  br label %red

red:
  %g = phi i32 [ 0, %start ], [ %g1, %red ]
  %best = phi float [ 1.073741824e+09, %start ], [ %best1, %red ]
  %bestk = phi i32 [ 144, %start ], [ %bestk1, %red ]
  %zero = phi float [ 1.073741824e+09, %start ], [ %zero1, %red ]
  %ix = add i32 %base, %g
  %o = shl i32 %ix, 2
  %sd = call float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32> %vs, i32 %o, i32 0, i32 0)
  %kk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vk, i32 %o, i32 0, i32 0)
  %better = fcmp olt float %sd, %best
  %best1 = select i1 %better, float %sd, float %best
  %bestk1 = select i1 %better, i32 %kk, i32 %bestk
  %g1 = add i32 %g, 1
  %gm = icmp ult i32 %g1, 8
  %zero1 = select i1 %gm, float %zero, float %zero
  br i1 %gm, label %red, label %done

done:
  %ky = udiv i32 %bestk1, 17
  %kx = urem i32 %bestk1, 17
  %mdy = sub i32 %ky, 8
  %mdx = sub i32 %kx, 8
  %dxm = and i32 %mdx, 65535
  %dys = shl i32 %mdy, 16
  %pack = or i32 %dxm, %dys
  %moff = shl i32 %b, 2
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pack, <4 x i32> %vm, i32 %moff, i32 0, i32 0)
  br label %exit

exit:
  ret void
}

!0 = !{}

attributes #0 = { "denormal-fp-math-f32"="preserve-sign,preserve-sign" }
