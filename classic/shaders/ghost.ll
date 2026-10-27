; user_sgprs=12
; per-byte average of two 32bpp surfaces: out = (a + b) / 2, written in place over b
target triple = "amdgcn--"
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)

define amdgpu_cs void @main(<4 x i32> inreg %a, <4 x i32> inreg %b, <4 x i32> inreg %o, i32 inreg %gid, i32 %tid) #0 {
  %g = shl i32 %gid, 6
  %i = add i32 %g, %tid
  %off = shl i32 %i, 2
  %x = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %a, i32 %off, i32 0, i32 0)
  %y = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %b, i32 %off, i32 0, i32 0)
  %xh = and i32 %x, -16843010
  %yh = and i32 %y, -16843010
  %xs = lshr i32 %xh, 1
  %ys = lshr i32 %yh, 1
  %c = and i32 %x, %y
  %c1 = and i32 %c, 16843009
  %s = add i32 %xs, %ys
  %r = add i32 %s, %c1
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %r, <4 x i32> %o, i32 %off, i32 0, i32 0)
  ret void
}

attributes #0 = { "denormal-fp-math-f32"="preserve-sign,preserve-sign" }
