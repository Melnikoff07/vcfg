; user_sgprs=2
; tgid_y=1
; Benchmark-only export. T# at 0, V# at 32, {width,height} at 48.
; Linear RGBA16 little-endian, rounded UNORM values from the engine's normal T#.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2i32(<2 x i32>, <4 x i32>, i32, i32, i32 immarg)
declare <4 x float> @llvm.maxnum.v4f32(<4 x float>, <4 x float>)
declare <4 x float> @llvm.minnum.v4f32(<4 x float>, <4 x float>)

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %tc = load <8 x i32>, ptr addrspace(4) %tbl, align 32, !invariant.load !0
  %pv = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %vo = load <4 x i32>, ptr addrspace(4) %pv, align 16, !invariant.load !0
  %pd = getelementptr i8, ptr addrspace(4) %tbl, i64 48
  %dims = load <2 x i32>, ptr addrspace(4) %pd, align 8, !invariant.load !0
  %w = extractelement <2 x i32> %dims, i32 0
  %h = extractelement <2 x i32> %dims, i32 1
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %x = add i32 %bx, %lx
  %y = add i32 %by, %ly
  %okx = icmp ult i32 %x, %w
  %oky = icmp ult i32 %y, %h
  %ok = and i1 %okx, %oky
  br i1 %ok, label %copy, label %exit
copy:
  %c = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %lo = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %c, <4 x float> zeroinitializer)
  %hi = call <4 x float> @llvm.minnum.v4f32(<4 x float> %lo, <4 x float> <float 1.0, float 1.0, float 1.0, float 1.0>)
  %sc = fmul <4 x float> %hi, <float 65535.0, float 65535.0, float 65535.0, float 65535.0>
  %rd = fadd <4 x float> %sc, <float 0.5, float 0.5, float 0.5, float 0.5>
  %qi = fptoui <4 x float> %rd to <4 x i32>
  %q = trunc <4 x i32> %qi to <4 x i16>
  %packed = bitcast <4 x i16> %q to <2 x i32>
  %row = mul i32 %y, %w
  %idx = add i32 %row, %x
  %off = shl i32 %idx, 3
  call void @llvm.amdgcn.raw.buffer.store.v2i32(<2 x i32> %packed, <4 x i32> %vo, i32 %off, i32 0, i32 0)
  br label %exit
exit:
  ret void
}
attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
