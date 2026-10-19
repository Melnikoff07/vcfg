; user_sgprs=8
; tgid_y=1
; fills a 2D area of a (tiled) 8888 surface with magenta using image_store through a T# descriptor
target triple = "amdgcn--"
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)

define amdgpu_cs void @main(<8 x i32> inreg %t, i32 inreg %gx, i32 inreg %gy, i32 %tid) {
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %x = add i32 %bx, %lx
  %y = add i32 %by, %ly
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> <float 1.0, float 0.0, float 1.0, float 1.0>, i32 15, i32 %x, i32 %y, <8 x i32> %t, i32 0, i32 0)
  ret void
}
