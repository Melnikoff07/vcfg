; user_sgprs=2
; Sum freshly rebuilt previous L3 for the original fade detector. One wave.
target triple = "amdgcn--"
declare i8 @llvm.amdgcn.raw.buffer.load.i8(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32, <4 x i32>, i32, i32, i32 immarg)
define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) #0 {
entry:
 %pl = getelementptr i8, ptr addrspace(4) %tbl, i64 192
 %l = load <4 x i32>, ptr addrspace(4) %pl, align 16
 %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
 %m = load <4 x i32>, ptr addrspace(4) %pm, align 16
 br label %loop
loop:
 %i = phi i32 [ %tid, %entry ], [ %next, %read ]
 %sum = phi i32 [ 0, %entry ], [ %s, %read ]
 %ok = icmp ult i32 %i, 32400
 br i1 %ok, label %read, label %done
read:
 %b = call i8 @llvm.amdgcn.raw.buffer.load.i8(<4 x i32> %l, i32 %i, i32 0, i32 0)
 %v = zext i8 %b to i32
 %s = add i32 %sum, %v
 %next = add i32 %i, 64
 br label %loop
done:
 %old = call i32 @llvm.amdgcn.raw.buffer.atomic.add.i32(i32 %sum, <4 x i32> %m, i32 8, i32 0, i32 0)
 ret void
}
attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
