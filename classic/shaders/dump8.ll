; user_sgprs=2
; tgid_y=1
; debug: copy the whole LEFT HALF 960x1080 (origin 0,0) of prev / cur / mid (tiled 8888, via T#) to a linear RGBA8 buffer.
; table: +0x00 T#prev, +0x20 T#cur, +0x40 T#mid, +0x160 V#out (3 x 640*360 dwords). dispatch (120,135).
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>, <4 x i32>, i32, i32, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)

define internal i32 @pack(<4 x float> %p) alwaysinline {
  %s = fmul <4 x float> %p, <float 255.0, float 255.0, float 255.0, float 255.0>
  %a = fadd <4 x float> %s, <float 0.5, float 0.5, float 0.5, float 0.5>
  %i = fptoui <4 x float> %a to <4 x i32>
  %r = extractelement <4 x i32> %i, i32 0
  %g = extractelement <4 x i32> %i, i32 1
  %b = extractelement <4 x i32> %i, i32 2
  %al = extractelement <4 x i32> %i, i32 3
  %g8 = shl i32 %g, 8
  %b16 = shl i32 %b, 16
  %a24 = shl i32 %al, 24
  %o1 = or i32 %r, %g8
  %o2 = or i32 %o1, %b16
  %o3 = or i32 %o2, %a24
  ret i32 %o3
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %tp = load <8 x i32>, ptr addrspace(4) %tbl, align 32, !invariant.load !0
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %pc, align 32, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %tm = load <8 x i32>, ptr addrspace(4) %pm, align 32, !invariant.load !0
  %po = getelementptr i8, ptr addrspace(4) %tbl, i64 352
  %vo = load <4 x i32>, ptr addrspace(4) %po, align 16, !invariant.load !0
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bx = shl i32 %gx, 3
  %by = shl i32 %gy, 3
  %cx = add i32 %bx, %lx
  %cy = add i32 %by, %ly
  %x = add i32 %cx, 0
  %y = add i32 %cy, 0
  %a = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tp, i32 0, i32 0)
  %b = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %c = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tm, i32 0, i32 0)
  %pa = call i32 @pack(<4 x float> %a)
  %pb = call i32 @pack(<4 x float> %b)
  %pcc = call i32 @pack(<4 x float> %c)
  %row = mul i32 %cy, 960
  %idx = add i32 %row, %cx
  %off0 = shl i32 %idx, 2
  %off1 = add i32 %off0, 4147200
  %off2 = add i32 %off0, 8294400
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pa, <4 x i32> %vo, i32 %off0, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pb, <4 x i32> %vo, i32 %off1, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pcc, <4 x i32> %vo, i32 %off2, i32 0, i32 0)
  ; field copy (CUSA03041 round): one thread per block (120 x 68 = 8160): F final (+0x100), GK splat keys (+0x150), FT raw (+0x180)
  ; written behind the three planes: F at 12441600, GK at +65280, FT at +97920
  %gxl = mul i32 %gy, 120
  %gxs = add i32 %gxl, %gx
  %lin0 = shl i32 %gxs, 6
  %lin = add i32 %lin0, %tid
  %okb = icmp ult i32 %lin, 8160
  br i1 %okb, label %fcopy, label %fend
fcopy:
  %pF = getelementptr i8, ptr addrspace(4) %tbl, i64 256
  %vF = load <4 x i32>, ptr addrspace(4) %pF, align 16, !invariant.load !0
  %pG = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vG = load <4 x i32>, ptr addrspace(4) %pG, align 16, !invariant.load !0
  %pT = getelementptr i8, ptr addrspace(4) %tbl, i64 384
  %vT = load <4 x i32>, ptr addrspace(4) %pT, align 16, !invariant.load !0
  %o8 = shl i32 %lin, 3
  %o4 = shl i32 %lin, 2
  %fv = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vF, i32 %o8, i32 0, i32 0)
  %gv = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vG, i32 %o4, i32 0, i32 0)
  %tv = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vT, i32 %o8, i32 0, i32 0)
  %dF = add i32 %o8, 12441600
  %dG = add i32 %o4, 12506880
  %dT = add i32 %o8, 12539520
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %fv, <4 x i32> %vo, i32 %dF, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %gv, <4 x i32> %vo, i32 %dG, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %tv, <4 x i32> %vo, i32 %dT, i32 0, i32 0)
  br label %fend
fend:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
