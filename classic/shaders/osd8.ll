; user_sgprs=2
; tgid_y=1
; osd8: composites the VCFG overlay panel (8-bit palette bitmap drawn on the CPU by overlay/vov.c) onto the frame.
; table: +0x20 T#cur, +0x40 T#out, +0x200 V#palette (256 x BGRA8), +0x210 V#pixels (w * h bytes, row pitch w),
;        +0x220 {rcas_con, rcas_flags, osd_xy (x0 | y0<<16), osd_wh (w/16 | h/16<<8 | alpha_u8<<16 | flags<<24)}.
; Dispatch: (w/16)*2 x (h/16)*2 groups of 8x8 px, relative to the panel origin. flags: 1 = write T#out, 2 = write T#cur.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %qx = add i32 %bxp, %lx
  %qy = add i32 %byp, %ly
  %prcfg = getelementptr i8, ptr addrspace(4) %tbl, i64 544
  %rcfg = load <4 x i32>, ptr addrspace(4) %prcfg, align 16, !invariant.load !0
  %xy = extractelement <4 x i32> %rcfg, i32 2
  %wh = extractelement <4 x i32> %rcfg, i32 3
  %cols = and i32 %wh, 255
  %rows0 = lshr i32 %wh, 8
  %rows = and i32 %rows0, 255
  %pw = shl i32 %cols, 4
  %ph = shl i32 %rows, 4
  %inx = icmp ult i32 %qx, %pw
  %iny = icmp ult i32 %qy, %ph
  %in = and i1 %inx, %iny
  br i1 %in, label %body, label %done

body:
  %pvp = getelementptr i8, ptr addrspace(4) %tbl, i64 512
  %vpal = load <4 x i32>, ptr addrspace(4) %pvp, align 16, !invariant.load !0
  %pvx = getelementptr i8, ptr addrspace(4) %tbl, i64 528
  %vpix = load <4 x i32>, ptr addrspace(4) %pvx, align 16, !invariant.load !0
  ; palette index: byte (qy * pw + qx) of V#pixels
  %rowo = mul i32 %qy, %pw
  %idx = add i32 %rowo, %qx
  %dwo = and i32 %idx, -4
  %dw = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vpix, i32 %dwo, i32 0, i32 0)
  %bsel = and i32 %idx, 3
  %bsh = shl i32 %bsel, 3
  %pi0 = lshr i32 %dw, %bsh
  %pi = and i32 %pi0, 255
  %isz = icmp eq i32 %pi, 0
  br i1 %isz, label %done, label %draw

draw:
  %po = shl i32 %pi, 2
  %pc = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vpal, i32 %po, i32 0, i32 0)
  %c0i = and i32 %pc, 255
  %c1s = lshr i32 %pc, 8
  %c1i = and i32 %c1s, 255
  %c2s = lshr i32 %pc, 16
  %c2i = and i32 %c2s, 255
  %c3i = lshr i32 %pc, 24
  %c0 = uitofp i32 %c0i to float
  %c1 = uitofp i32 %c1i to float
  %c2 = uitofp i32 %c2i to float
  %c3 = uitofp i32 %c3i to float
  %r0 = fmul float %c0, 0x3F70101020000000 ; 1/255
  %r1 = fmul float %c1, 0x3F70101020000000
  %r2 = fmul float %c2, 0x3F70101020000000
  %pa = fmul float %c3, 0x3F70101020000000
  %au0 = lshr i32 %wh, 16
  %au = and i32 %au0, 255
  %af = uitofp i32 %au to float
  %ga = fmul float %af, 0x3F70101020000000
  %t = fmul float %pa, %ga
  %x0 = and i32 %xy, 65535
  %y0 = lshr i32 %xy, 16
  %x = add i32 %x0, %qx
  %y = add i32 %y0, %qy
  %ptc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %ptc, align 32, !invariant.load !0
  %cc0 = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %s0 = extractelement <4 x float> %cc0, i32 0
  %s1 = extractelement <4 x float> %cc0, i32 1
  %s2 = extractelement <4 x float> %cc0, i32 2
  %sa = extractelement <4 x float> %cc0, i32 3
  %d0 = fsub float %r0, %s0
  %d1 = fsub float %r1, %s1
  %d2 = fsub float %r2, %s2
  %m0 = fmul float %d0, %t
  %m1 = fmul float %d1, %t
  %m2 = fmul float %d2, %t
  %o0 = fadd float %s0, %m0
  %o1 = fadd float %s1, %m1
  %o2 = fadd float %s2, %m2
  %v0 = insertelement <4 x float> undef, float %o0, i32 0
  %v1 = insertelement <4 x float> %v0, float %o1, i32 1
  %v2 = insertelement <4 x float> %v1, float %o2, i32 2
  %cc = insertelement <4 x float> %v2, float %sa, i32 3
  %ofl = lshr i32 %wh, 24
  %do_mid0 = and i32 %ofl, 1
  %do_mid = icmp ne i32 %do_mid0, 0
  br i1 %do_mid, label %w_mid, label %chk_cur

w_mid:
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %cc, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  br label %chk_cur

chk_cur:
  %do_cur0 = and i32 %ofl, 2
  %do_cur = icmp ne i32 %do_cur0, 0
  br i1 %do_cur, label %w_cur, label %done

w_cur:
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %cc, i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  br label %done

done:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
