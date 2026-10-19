; user_sgprs=2
; tgid_y=1
; table: +0x60 V#lumaCur (640x360 f32), +0xD0 V#l4Out (160x90 f32). 4x4 box downsample.
target triple = "amdgcn--"
declare float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.f32(float, <4 x i32>, i32, i32, i32 immarg)

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %po = getelementptr i8, ptr addrspace(4) %tbl, i64 208
  %vo = load <4 x i32>, ptr addrspace(4) %po, align 16, !invariant.load !0
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %x4 = add i32 %bx, %lx
  %y4 = add i32 %by, %ly
  %ok = icmp ult i32 %y4, 90
  br i1 %ok, label %start, label %exit

start:
  %x0 = shl i32 %x4, 2
  %y0 = shl i32 %y4, 2
  br label %loop

loop:
  %j = phi i32 [ 0, %start ], [ %j1, %loop ]
  %acc = phi float [ 0.0, %start ], [ %acc1, %loop ]
  %jx = and i32 %j, 3
  %jy = lshr i32 %j, 2
  %x = add i32 %x0, %jx
  %y = add i32 %y0, %jy
  %row = mul i32 %y, 640
  %idx = add i32 %row, %x
  %off = shl i32 %idx, 2
  %v = call float @llvm.amdgcn.raw.buffer.load.f32(<4 x i32> %vc, i32 %off, i32 0, i32 0)
  %acc1 = fadd float %acc, %v
  %j1 = add i32 %j, 1
  %more = icmp ult i32 %j1, 16
  br i1 %more, label %loop, label %done

done:
  %avg = fmul float %acc1, 0.0625
  %orow = mul i32 %y4, 160
  %oidx = add i32 %orow, %x4
  %ooff = shl i32 %oidx, 2
  call void @llvm.amdgcn.raw.buffer.store.f32(float %avg, <4 x i32> %vo, i32 %ooff, i32 0, i32 0)
  br label %exit

exit:
  ret void
}

!0 = !{}
