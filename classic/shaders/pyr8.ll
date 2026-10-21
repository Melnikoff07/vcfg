; user_sgprs=2
; tgid_y=1
; mode 8, pass 1: luma pyramid of the CURRENT frame in one dispatch (30 x 68 groups of 64 threads).
; table: +0x20 T#cur, +0x60 S# (bilinear, clamp, unnormalised), +0x80 V#L1 (960x540 u8), +0xA0 V#L2 (480x270 u8),
;        +0xC0 V#L3 (240x135 u8), +0x70 consts {parity}, +0x120 V#misc (dword 0 = scene-change counter and
;        dword 2 + parity = luma sum of this frame, dword 6 = foreground block count, all zeroed here;
;        me1 / me3 / fgs8 accumulate them),
;        +0x150 V#GK (120x68 foreground splat keys, zeroed here by the first 8160 threads; fgs8 fills, me1 reads).
; thread (tx = tid&7, ty = tid>>3) of group (gx, gy): L1 pixels x = gx*32 + tx*4 + k (k = 0..3), y = gy*8 + ty.
; Supports arbitrary input resolution (720p/900p/1080p/1440p/2160p) by reading W-1 and H-1 from T#cur dword 2
; and scaling the 960x540 L1 sample centers by (W / 1920.0, H / 1080.0) into native texel space.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 immarg, float, float, <8 x i32>, <4 x i32>, i1 immarg, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i8(i8, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.s.barrier()
declare float @llvm.sqrt.f32(float)
declare float @llvm.floor.f32(float)
declare float @llvm.minnum.f32(float, float)

@hrow = internal addrspace(3) global [128 x float] undef, align 16   ; [ty][16] horizontal pair sums of L1
@m2 = internal addrspace(3) global [64 x float] undef, align 16      ; [ty/2][16] L2 values

define internal float @luma(<8 x i32> %t, <4 x i32> %s, float %u, float %v) alwaysinline {
  %c = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 7, float %u, float %v, <8 x i32> %t, <4 x i32> %s, i1 false, i32 0, i32 0)
  %r = extractelement <4 x float> %c, i32 0
  %g = extractelement <4 x float> %c, i32 1
  %b = extractelement <4 x float> %c, i32 2
  %g2 = fmul float %g, 2.0
  %s1 = fadd float %r, %g2
  %s2 = fadd float %s1, %b
  %y = fmul float %s2, 0.25
  %l = call float @llvm.sqrt.f32(float %y)
  ret float %l
}

define internal i32 @q8(float %l) alwaysinline {
  %a = fmul float %l, 255.0
  %b = fadd float %a, 0.5
  %c = call float @llvm.floor.f32(float %b)
  %d = call float @llvm.minnum.f32(float %c, float 255.0)
  %i = fptoui float %d to i32
  ret i32 %i
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %pt = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %pt, align 32, !invariant.load !0
  %ps = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %sm = load <4 x i32>, ptr addrspace(4) %ps, align 16, !invariant.load !0
  %p1 = getelementptr i8, ptr addrspace(4) %tbl, i64 128
  %v1 = load <4 x i32>, ptr addrspace(4) %p1, align 16, !invariant.load !0
  %p2 = getelementptr i8, ptr addrspace(4) %tbl, i64 160
  %v2 = load <4 x i32>, ptr addrspace(4) %p2, align 16, !invariant.load !0
  %p3 = getelementptr i8, ptr addrspace(4) %tbl, i64 192
  %v3 = load <4 x i32>, ptr addrspace(4) %p3, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %tc2 = extractelement <8 x i32> %tc, i32 2
  %wm1 = and i32 %tc2, 16383
  %hm1_0 = lshr i32 %tc2, 14
  %hm1 = and i32 %hm1_0, 16383
  %w_i = add i32 %wm1, 1
  %h_i = add i32 %hm1, 1
  %wf = uitofp i32 %w_i to float
  %hf = uitofp i32 %h_i to float
  %sx = fmul float %wf, 0x3F41111120000000 ; 1.0 / 1920.0
  %sy = fmul float %hf, 0x3F4E573AC0000000 ; 1.0 / 1080.0
  %sx2 = fmul float %sx, 2.0
  %tx = and i32 %tid, 7
  %ty = lshr i32 %tid, 3
  %gxb = shl i32 %gx, 5
  %txb = shl i32 %tx, 2
  %x1 = add i32 %gxb, %txb
  %gyb = shl i32 %gy, 3
  %y1 = add i32 %gyb, %ty
  ; full-res sample positions (unnormalised texel space: texel i spans [i, i+1), scaled from 960x540 L1 to WxH)
  %x2 = shl i32 %x1, 1
  %y2 = shl i32 %y1, 1
  %y2o = or i32 %y2, 1
  %vf0 = uitofp i32 %y2o to float
  %vf = fmul float %vf0, %sy
  %u0i = or i32 %x2, 1
  %u0_0 = uitofp i32 %u0i to float
  %u0 = fmul float %u0_0, %sx
  %u1 = fadd float %u0, %sx2
  %u2 = fadd float %u1, %sx2
  %u3 = fadd float %u2, %sx2
  %l0 = call float @luma(<8 x i32> %tc, <4 x i32> %sm, float %u0, float %vf)
  %l1 = call float @luma(<8 x i32> %tc, <4 x i32> %sm, float %u1, float %vf)
  %l2 = call float @luma(<8 x i32> %tc, <4 x i32> %sm, float %u2, float %vf)
  %l3 = call float @luma(<8 x i32> %tc, <4 x i32> %sm, float %u3, float %vf)
  ; ---- L1 store (one packed dword = 4 pixels)
  %iny1 = icmp ult i32 %y1, 540
  br i1 %iny1, label %st1, label %lds1
st1:
  %q0 = call i32 @q8(float %l0)
  %q1 = call i32 @q8(float %l1)
  %q2 = call i32 @q8(float %l2)
  %q3 = call i32 @q8(float %l3)
  %s1 = shl i32 %q1, 8
  %s2 = shl i32 %q2, 16
  %s3 = shl i32 %q3, 24
  %o01 = or i32 %q0, %s1
  %o23 = or i32 %s2, %s3
  %pk = or i32 %o01, %o23
  %row1 = mul i32 %y1, 960
  %off1 = add i32 %row1, %x1
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %pk, <4 x i32> %v1, i32 %off1, i32 0, i32 0)
  br label %lds1
lds1:
  %h0 = fadd float %l0, %l1
  %h1 = fadd float %l2, %l3
  %hi = shl i32 %tid, 1
  %ph0 = getelementptr [128 x float], ptr addrspace(3) @hrow, i32 0, i32 %hi
  store float %h0, ptr addrspace(3) %ph0, align 4
  %hi1 = or i32 %hi, 1
  %ph1 = getelementptr [128 x float], ptr addrspace(3) @hrow, i32 0, i32 %hi1
  store float %h1, ptr addrspace(3) %ph1, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ; ---- L2: rows ty even combine with ty+1
  %tyodd = and i32 %ty, 1
  %iseven = icmp eq i32 %tyodd, 0
  br i1 %iseven, label %l2a, label %l2done
l2a:
  %hj = add i32 %hi, 16                     ; same column, next row
  %pa = getelementptr [128 x float], ptr addrspace(3) @hrow, i32 0, i32 %hj
  %a0 = load float, ptr addrspace(3) %pa, align 4
  %hj1 = add i32 %hi, 17
  %pb = getelementptr [128 x float], ptr addrspace(3) @hrow, i32 0, i32 %hj1
  %a1 = load float, ptr addrspace(3) %pb, align 4
  %e0 = fadd float %h0, %a0
  %e1 = fadd float %h1, %a1
  %m0 = fmul float %e0, 0.25
  %m1 = fmul float %e1, 0.25
  %r2 = lshr i32 %ty, 1                     ; 0..3
  %mi = shl i32 %r2, 4
  %mtx = shl i32 %tx, 1
  %mi0 = add i32 %mi, %mtx
  %pm0 = getelementptr [64 x float], ptr addrspace(3) @m2, i32 0, i32 %mi0
  store float %m0, ptr addrspace(3) %pm0, align 4
  %mi1 = add i32 %mi0, 1
  %pm1 = getelementptr [64 x float], ptr addrspace(3) @m2, i32 0, i32 %mi1
  store float %m1, ptr addrspace(3) %pm1, align 4
  %gy4 = shl i32 %gy, 2
  %y2l = add i32 %gy4, %r2
  %iny2 = icmp ult i32 %y2l, 270
  br i1 %iny2, label %st2, label %l2done
st2:
  %gx16 = shl i32 %gx, 4
  %x2l = add i32 %gx16, %mtx
  %row2 = mul i32 %y2l, 480
  %off2 = add i32 %row2, %x2l
  %w0 = call i32 @q8(float %m0)
  %w1 = call i32 @q8(float %m1)
  %b0 = trunc i32 %w0 to i8
  %b1 = trunc i32 %w1 to i8
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %b0, <4 x i32> %v2, i32 %off2, i32 0, i32 0)
  %off2b = add i32 %off2, 1
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %b1, <4 x i32> %v2, i32 %off2b, i32 0, i32 0)
  br label %l2done
l2done:
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ; ---- L3: threads with ty = 0 / 4 average a 2x2 of L2
  %ty3 = and i32 %ty, 3
  %is3 = icmp eq i32 %ty3, 0
  br i1 %is3, label %l3a, label %l3done
l3a:
  %r3 = lshr i32 %ty, 1                     ; L2 row in LDS: 0 or 2
  %ri = shl i32 %r3, 4
  %c2 = shl i32 %tx, 1
  %i00 = add i32 %ri, %c2
  %i01 = add i32 %i00, 1
  %i10 = add i32 %i00, 16
  %i11 = add i32 %i00, 17
  %pq00 = getelementptr [64 x float], ptr addrspace(3) @m2, i32 0, i32 %i00
  %pq01 = getelementptr [64 x float], ptr addrspace(3) @m2, i32 0, i32 %i01
  %pq10 = getelementptr [64 x float], ptr addrspace(3) @m2, i32 0, i32 %i10
  %pq11 = getelementptr [64 x float], ptr addrspace(3) @m2, i32 0, i32 %i11
  %z00 = load float, ptr addrspace(3) %pq00, align 4
  %z01 = load float, ptr addrspace(3) %pq01, align 4
  %z10 = load float, ptr addrspace(3) %pq10, align 4
  %z11 = load float, ptr addrspace(3) %pq11, align 4
  %za = fadd float %z00, %z01
  %zb = fadd float %z10, %z11
  %zs = fadd float %za, %zb
  %zm = fmul float %zs, 0.25
  %gy2 = shl i32 %gy, 1
  %r3h = lshr i32 %ty, 2
  %y3 = add i32 %gy2, %r3h
  %iny3 = icmp ult i32 %y3, 135
  br i1 %iny3, label %st3, label %l3done
st3:
  %gx8 = shl i32 %gx, 3
  %x3 = add i32 %gx8, %tx
  %row3 = mul i32 %y3, 240
  %off3 = add i32 %row3, %x3
  %w3 = call i32 @q8(float %zm)
  %b3 = trunc i32 %w3 to i8
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %b3, <4 x i32> %v3, i32 %off3, i32 0, i32 0)
  br label %l3done
l3done:
  ; ---- clear the foreground splat keys (thread index (gy * 30 + gx) * 64 + tid < 120 * 68)
  %lg0 = mul i32 %gy, 30
  %lg1 = add i32 %lg0, %gx
  %lg2 = shl i32 %lg1, 6
  %lin = add i32 %lg2, %tid
  %ink = icmp ult i32 %lin, 8160
  br i1 %ink, label %zk, label %zkd
zk:
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %koff = shl i32 %lin, 2
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 0, <4 x i32> %vgk, i32 %koff, i32 0, i32 0)
  br label %zkd
zkd:
  ; group (0,0) thread 0 resets the scene-change counter used by me1/warp of this frame
  %g0 = or i32 %gx, %gy
  %g0t = or i32 %g0, %tid
  %first = icmp eq i32 %g0t, 0
  br i1 %first, label %zc, label %exit
zc:
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 0, <4 x i32> %vm, i32 0, i32 0, i32 0)
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %par = extractelement <4 x i32> %cn, i32 0
  %so0 = shl i32 %par, 2
  %so = add i32 %so0, 8
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 0, <4 x i32> %vm, i32 %so, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 0, <4 x i32> %vm, i32 24, i32 0, i32 0)    ; foreground count (fgs8)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 0, <4 x i32> %vm, i32 28, i32 0, i32 0)    ; fade flag (me1)
  br label %exit
exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
