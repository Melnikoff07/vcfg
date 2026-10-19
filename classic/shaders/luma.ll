; user_sgprs=2
; tgid_y=1
; table (32B aligned): +0x00 T#prev, +0x20 T#cur, +0x60 V#lumaOut, +0xC0 consts {sx, W, H, bs}.
; Writes 640x360 float luma of the CURRENT frame: each texel is the mean of 4 taps (corners of an sx*sx cell).
; FIX: this used to load the T# at +0x00, which build_dcb_flow fills with the PREVIOUS frame, so the
; motion field was always estimated between frames N-2 and N-1 and then applied to N-1 -> N.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.f32(float, <4 x i32>, i32, i32, i32 immarg)

define internal float @lum(<8 x i32> %t, i32 %x, i32 %y) alwaysinline {
  %p = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 7, i32 %x, i32 %y, <8 x i32> %t, i32 0, i32 0)
  %r = extractelement <4 x float> %p, i32 0
  %g = extractelement <4 x float> %p, i32 1
  %b = extractelement <4 x float> %p, i32 2
  %g2 = fmul float %g, 2.0
  %s = fadd float %r, %g2
  %s2 = fadd float %s, %b
  %l = fmul float %s2, 0.25
  ret float %l
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) {
  %ptc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %ptc, align 32, !invariant.load !0
  %pv = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %vo = load <4 x i32>, ptr addrspace(4) %pv, align 16, !invariant.load !0
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 192
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %sx = extractelement <4 x i32> %cn, i32 0
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %x2 = add i32 %bx, %lx
  %y2 = add i32 %by, %ly
  %x = mul i32 %x2, %sx
  %y = mul i32 %y2, %sx
  %sm = sub i32 %sx, 1
  %x1 = add i32 %x, %sm
  %y1 = add i32 %y, %sm
  %l00 = call float @lum(<8 x i32> %tc, i32 %x, i32 %y)
  %l10 = call float @lum(<8 x i32> %tc, i32 %x1, i32 %y)
  %l01 = call float @lum(<8 x i32> %tc, i32 %x, i32 %y1)
  %l11 = call float @lum(<8 x i32> %tc, i32 %x1, i32 %y1)
  %a = fadd float %l00, %l10
  %b = fadd float %l01, %l11
  %s = fadd float %a, %b
  %l = fmul float %s, 0.25
  %row = mul i32 %y2, 640
  %idx = add i32 %row, %x2
  %off = shl i32 %idx, 2
  call void @llvm.amdgcn.raw.buffer.store.f32(float %l, <4 x i32> %vo, i32 %off, i32 0, i32 0)
  ret void
}

!0 = !{}
