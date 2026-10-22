; user_sgprs=2
; tgid_y=1
; mode 8.4, pass 7: motion-compensated median / bilateral vector filter on L1 (960x540), 120 x 68 blocks of 8x8 (= 16x16 full-res px).
; dispatch 30 x 68 groups; lane = block (tid>>4) * 16 + slot (tid&15): 4 blocks per wave, 10 of 16 slots active.
; table: +0x80 V#L1cur, +0x90 V#L1prev, +0xF0 V#V2, +0x100 V#F (out), +0x120 V#misc, +0x150 V#GK, +0x180 V#FT (raw in)
target triple = "amdgcn--"
declare <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>, <4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.alignbyte(i32, i32, i32)
declare i32 @llvm.amdgcn.sad.u8(i32, i32, i32)
declare void @llvm.amdgcn.s.barrier()
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)
declare i32 @llvm.umin.i32(i32, i32)
declare i32 @llvm.abs.i32(i32, i1 immarg)
declare float @llvm.floor.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)

@keys = internal addrspace(3) global [64 x i32] undef, align 16
@vecs = internal addrspace(3) global [64 x i32] undef, align 16

; Bilateral SAD of an 8x8 block using a 2-iteration 4-row loop,
; displacement-preserving clamp (r6), and structure-weighted DC-compensated SAD (r7).
define internal i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 %wl, i32 %hl, i32 %X, i32 %Y, i32 %dx, i32 %dy) alwaysinline {
entry:
  %wm8 = sub i32 %wl, 8
  %hm8 = sub i32 %hl, 8
  %nwm8 = sub i32 0, %wm8
  %nhm8 = sub i32 0, %hm8
  %dxc0 = call i32 @llvm.smax.i32(i32 %dx, i32 %nwm8)
  %dxc = call i32 @llvm.smin.i32(i32 %dxc0, i32 %wm8)
  %dyc0 = call i32 @llvm.smax.i32(i32 %dy, i32 %nhm8)
  %dyc = call i32 @llvm.smin.i32(i32 %dyc0, i32 %hm8)
  %ndx = sub i32 0, %dxc
  %ndy = sub i32 0, %dyc
  %fcx = ashr i32 %dxc, 1
  %fcy = ashr i32 %dyc, 1
  %xc0 = sub i32 %X, %fcx
  %xmin_c = call i32 @llvm.smax.i32(i32 0, i32 %ndx)
  %xmax_c0 = sub i32 %wm8, %dxc
  %xmax_c = call i32 @llvm.smin.i32(i32 %wm8, i32 %xmax_c0)
  %xc1 = call i32 @llvm.smax.i32(i32 %xc0, i32 %xmin_c)
  %xc = call i32 @llvm.smin.i32(i32 %xc1, i32 %xmax_c)
  %xp = add i32 %xc, %dxc
  %shp = and i32 %xp, 3
  %shc = and i32 %xc, 3
  %axp = and i32 %xp, -4
  %axc = and i32 %xc, -4
  %ybc0 = sub i32 %Y, %fcy
  %ymin_c = call i32 @llvm.smax.i32(i32 0, i32 %ndy)
  %ymax_c0 = sub i32 %hm8, %dyc
  %ymax_c = call i32 @llvm.smin.i32(i32 %hm8, i32 %ymax_c0)
  %ybc1 = call i32 @llvm.smax.i32(i32 %ybc0, i32 %ymin_c)
  %ybc = call i32 @llvm.smin.i32(i32 %ybc1, i32 %ymax_c)
  %ybp = add i32 %ybc, %dyc
  br label %rowloop
rowloop:
  %rbase = phi i32 [ 0, %entry ], [ 4, %rowloop ]
  %sbin = phi i32 [ 0, %entry ], [ %sb3, %rowloop ]
  %mbin = phi i32 [ 0, %entry ], [ %mb3, %rowloop ]
  %mdin = phi i32 [ 0, %entry ], [ %md3, %rowloop ]
  %ybp_r = add i32 %ybp, %rbase
  %ybc_r = add i32 %ybc, %rbase
  %rp0 = mul i32 %ybp_r, %wl
  %ap0 = add i32 %rp0, %axp
  %rc0 = mul i32 %ybc_r, %wl
  %ac0 = add i32 %rc0, %axc
  %dp0 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap0, i32 0, i32 0)
  %dc0 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac0, i32 0, i32 0)
  %p0_0 = extractelement <3 x i32> %dp0, i32 0
  %p1_0 = extractelement <3 x i32> %dp0, i32 1
  %p2_0 = extractelement <3 x i32> %dp0, i32 2
  %c0_0 = extractelement <3 x i32> %dc0, i32 0
  %c1_0 = extractelement <3 x i32> %dc0, i32 1
  %c2_0 = extractelement <3 x i32> %dc0, i32 2
  %qp0_0 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_0, i32 %p0_0, i32 %shp)
  %qp1_0 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_0, i32 %p1_0, i32 %shp)
  %qc0_0 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_0, i32 %c0_0, i32 %shc)
  %qc1_0 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_0, i32 %c1_0, i32 %shc)
  %sa0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 %qc0_0, i32 %sbin)
  %sb0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 %qc1_0, i32 %sa0)
  %ma0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_0, i32 0, i32 %mbin)
  %mb0 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_0, i32 0, i32 %ma0)
  %mc0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_0, i32 0, i32 %mdin)
  %md0 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_0, i32 0, i32 %mc0)
  %ap1 = add i32 %ap0, %wl
  %ac1 = add i32 %ac0, %wl
  %dp1 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap1, i32 0, i32 0)
  %dc1 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac1, i32 0, i32 0)
  %p0_1 = extractelement <3 x i32> %dp1, i32 0
  %p1_1 = extractelement <3 x i32> %dp1, i32 1
  %p2_1 = extractelement <3 x i32> %dp1, i32 2
  %c0_1 = extractelement <3 x i32> %dc1, i32 0
  %c1_1 = extractelement <3 x i32> %dc1, i32 1
  %c2_1 = extractelement <3 x i32> %dc1, i32 2
  %qp0_1 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_1, i32 %p0_1, i32 %shp)
  %qp1_1 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_1, i32 %p1_1, i32 %shp)
  %qc0_1 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_1, i32 %c0_1, i32 %shc)
  %qc1_1 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_1, i32 %c1_1, i32 %shc)
  %sa1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_1, i32 %qc0_1, i32 %sb0)
  %sb1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_1, i32 %qc1_1, i32 %sa1)
  %ma1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_1, i32 0, i32 %mb0)
  %mb1 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_1, i32 0, i32 %ma1)
  %mc1 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_1, i32 0, i32 %md0)
  %md1 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_1, i32 0, i32 %mc1)
  %ap2 = add i32 %ap1, %wl
  %ac2 = add i32 %ac1, %wl
  %dp2 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap2, i32 0, i32 0)
  %dc2 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac2, i32 0, i32 0)
  %p0_2 = extractelement <3 x i32> %dp2, i32 0
  %p1_2 = extractelement <3 x i32> %dp2, i32 1
  %p2_2 = extractelement <3 x i32> %dp2, i32 2
  %c0_2 = extractelement <3 x i32> %dc2, i32 0
  %c1_2 = extractelement <3 x i32> %dc2, i32 1
  %c2_2 = extractelement <3 x i32> %dc2, i32 2
  %qp0_2 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_2, i32 %p0_2, i32 %shp)
  %qp1_2 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_2, i32 %p1_2, i32 %shp)
  %qc0_2 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_2, i32 %c0_2, i32 %shc)
  %qc1_2 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_2, i32 %c1_2, i32 %shc)
  %sa2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_2, i32 %qc0_2, i32 %sb1)
  %sb2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_2, i32 %qc1_2, i32 %sa2)
  %ma2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_2, i32 0, i32 %mb1)
  %mb2 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_2, i32 0, i32 %ma2)
  %mc2 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_2, i32 0, i32 %md1)
  %md2 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_2, i32 0, i32 %mc2)
  %ap3 = add i32 %ap2, %wl
  %ac3 = add i32 %ac2, %wl
  %dp3 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vp, i32 %ap3, i32 0, i32 0)
  %dc3 = call <3 x i32> @llvm.amdgcn.raw.buffer.load.v3i32(<4 x i32> %vc, i32 %ac3, i32 0, i32 0)
  %p0_3 = extractelement <3 x i32> %dp3, i32 0
  %p1_3 = extractelement <3 x i32> %dp3, i32 1
  %p2_3 = extractelement <3 x i32> %dp3, i32 2
  %c0_3 = extractelement <3 x i32> %dc3, i32 0
  %c1_3 = extractelement <3 x i32> %dc3, i32 1
  %c2_3 = extractelement <3 x i32> %dc3, i32 2
  %qp0_3 = call i32 @llvm.amdgcn.alignbyte(i32 %p1_3, i32 %p0_3, i32 %shp)
  %qp1_3 = call i32 @llvm.amdgcn.alignbyte(i32 %p2_3, i32 %p1_3, i32 %shp)
  %qc0_3 = call i32 @llvm.amdgcn.alignbyte(i32 %c1_3, i32 %c0_3, i32 %shc)
  %qc1_3 = call i32 @llvm.amdgcn.alignbyte(i32 %c2_3, i32 %c1_3, i32 %shc)
  %sa3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_3, i32 %qc0_3, i32 %sb2)
  %sb3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_3, i32 %qc1_3, i32 %sa3)
  %ma3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp0_3, i32 0, i32 %mb2)
  %mb3 = call i32 @llvm.amdgcn.sad.u8(i32 %qp1_3, i32 0, i32 %ma3)
  %mc3 = call i32 @llvm.amdgcn.sad.u8(i32 %qc0_3, i32 0, i32 %md2)
  %md3 = call i32 @llvm.amdgcn.sad.u8(i32 %qc1_3, i32 0, i32 %mc3)
  %rmore = icmp eq i32 %rbase, 0
  br i1 %rmore, label %rowloop, label %rowdone
rowdone:
  %sb7 = add i32 %sb3, 0
  %mb7 = add i32 %mb3, 0
  %md7 = add i32 %md3, 0
  %dc_diff = sub i32 %mb7, %md7
  %dc_abs = call i32 @llvm.abs.i32(i32 %dc_diff, i1 false)
  %dc_ac = sub i32 %sb7, %dc_abs
  %dc_ac2 = shl i32 %dc_ac, 1
  %dc_e0 = sub i32 %dc_abs, 256
  %dc_eff = call i32 @llvm.smax.i32(i32 %dc_e0, i32 0)
  %dc_t3 = mul i32 %dc_eff, 3
  %dc_t3q = ashr i32 %dc_t3, 2
  %dc_d0 = sub i32 %dc_t3q, %dc_ac2
  %dc_d1 = call i32 @llvm.smax.i32(i32 %dc_d0, i32 0)
  %dc_le = icmp sle i32 %dc_abs, 2048
  %dc_disc = select i1 %dc_le, i32 %dc_d1, i32 0
  %dc_ex0 = sub i32 %dc_abs, 2048
  %dc_ex1 = call i32 @llvm.smax.i32(i32 %dc_ex0, i32 0)
  %pen = ashr i32 %dc_ex1, 2
  %sad0 = sub i32 %sb7, %dc_disc
  %sad = add i32 %sad0, %pen
  ret i32 %sad
}

; vectors are packed as (dx & 0xffff) | (dy << 16), level pixels
define internal i32 @pack(i32 %dx, i32 %dy) alwaysinline {
  %a = and i32 %dx, 65535
  %b = shl i32 %dy, 16
  %c = or i32 %a, %b
  ret i32 %c
}
define internal i32 @unx(i32 %p) alwaysinline {
  %a = shl i32 %p, 16
  %b = ashr i32 %a, 16
  ret i32 %b
}
define internal i32 @uny(i32 %p) alwaysinline {
  %b = ashr i32 %p, 16
  ret i32 %b
}
define internal i32 @rnd(float %f) alwaysinline {
  %a = fadd float %f, 0.5
  %b = call float @llvm.floor.f32(float %a)
  %c = call float @llvm.maxnum.f32(float %b, float -4096.0)
  %d = call float @llvm.minnum.f32(float %c, float 4096.0)
  %i = fptosi float %d to i32
  ret i32 %i
}

; min of the keys of lanes [base, base+n) (n = 16 or 64), every lane gets the result.
define internal i32 @reduce(i32 %tid, i32 %key, i32 %base, i32 %n) alwaysinline {
entry:
  %p = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %tid
  store i32 %key, ptr addrspace(3) %p, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  br label %loop
loop:
  %i = phi i32 [ 0, %entry ], [ %i1, %loop ]
  %m = phi i32 [ -1, %entry ], [ %m1, %loop ]
  %ix = add i32 %base, %i
  %q = getelementptr [64 x i32], ptr addrspace(3) @keys, i32 0, i32 %ix
  %k = load i32, ptr addrspace(3) %q, align 4
  %m1 = call i32 @llvm.umin.i32(i32 %m, i32 %k)
  %i1 = add i32 %i, 1
  %more = icmp ult i32 %i1, %n
  br i1 %more, label %loop, label %done
done:
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ret i32 %m1
}

; packed vector published by lane `src` (all lanes read it)
define internal i32 @bcast(i32 %tid, i32 %val, i32 %src) alwaysinline {
  %p = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %tid
  store i32 %val, ptr addrspace(3) %p, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  %q = getelementptr [64 x i32], ptr addrspace(3) @vecs, i32 0, i32 %src
  %v = load i32, ptr addrspace(3) %q, align 4
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ret i32 %v
}

; full-res float vector of block (bx + ox, by + oy) of FT, coordinates reflected at screen borders (r6)
define internal <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %ox, i32 %oy) alwaysinline {
  %x0 = add i32 %bx, %ox
  %xlt0 = icmp slt i32 %x0, 0
  %xgt = icmp sgt i32 %x0, 119
  %x1 = select i1 %xlt0, i32 1, i32 %x0
  %x = select i1 %xgt, i32 118, i32 %x1
  %y0 = add i32 %by, %oy
  %ylt0 = icmp slt i32 %y0, 0
  %ygt = icmp sgt i32 %y0, 67
  %y1 = select i1 %ylt0, i32 1, i32 %y0
  %y = select i1 %ygt, i32 66, i32 %y1
  %r = mul i32 %y, 120
  %i = add i32 %r, %x
  %o = shl i32 %i, 3
  %v = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vt, i32 %o, i32 0, i32 0)
  ret <2 x float> %v
}

; smoothness term of candidate (cx, cy) (L1 px) against neighbour (ox, oy): min(|c - rnd(v_n / 2)|_1, cap)
define internal i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 %ox, i32 %oy, i32 %cx, i32 %cy, i32 %cap) alwaysinline {
  %v = call <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %ox, i32 %oy)
  %vx = extractelement <2 x float> %v, i32 0
  %vy = extractelement <2 x float> %v, i32 1
  %hx = fmul float %vx, 0.5
  %hy = fmul float %vy, 0.5
  %nx = call i32 @rnd(float %hx)
  %ny = call i32 @rnd(float %hy)
  %dx = sub i32 %cx, %nx
  %dy = sub i32 %cy, %ny
  %ax = call i32 @llvm.abs.i32(i32 %dx, i1 false)
  %ay = call i32 @llvm.abs.i32(i32 %dy, i1 false)
  %a = add i32 %ax, %ay
  %m = call i32 @llvm.umin.i32(i32 %a, i32 %cap)
  ret i32 %m
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %pc = getelementptr i8, ptr addrspace(4) %tbl, i64 128
  %vc = load <4 x i32>, ptr addrspace(4) %pc, align 16, !invariant.load !0
  %pp = getelementptr i8, ptr addrspace(4) %tbl, i64 144
  %vp = load <4 x i32>, ptr addrspace(4) %pp, align 16, !invariant.load !0
  %pf = getelementptr i8, ptr addrspace(4) %tbl, i64 256
  %vf = load <4 x i32>, ptr addrspace(4) %pf, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %pt = getelementptr i8, ptr addrspace(4) %tbl, i64 384
  %vt = load <4 x i32>, ptr addrspace(4) %pt, align 16, !invariant.load !0
  %j = lshr i32 %tid, 4
  %sub = and i32 %tid, 15
  %base = and i32 %tid, 48
  %gx4 = shl i32 %gx, 2
  %bx = add i32 %gx4, %j
  %by = add i32 %gy, 0
  %X = shl i32 %bx, 3
  %Y = shl i32 %by, 3
  %grow = mul i32 %by, 120
  %gidx = add i32 %grow, %bx
  %goff = shl i32 %gidx, 2
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgk, i32 %goff, i32 0, i32 0)
  %hasfg = icmp ne i32 %gk, 0
  %isfgk = icmp uge i32 %gk, 1048576
  %nfgk = xor i1 %isfgk, true
  %isanc = and i1 %hasfg, %nfgk
  %notanc = xor i1 %isanc, true
  ; ---- candidate of this lane: slot 0 own, 1..8 neighbour k (raster 3x3 without the centre), 9 camera (when !isanc)
  %k0 = sub i32 %sub, 1
  %ge5 = icmp uge i32 %sub, 5
  %k1 = select i1 %ge5, i32 %sub, i32 %k0
  %isn = icmp ult i32 %k0, 8
  %k = select i1 %isn, i32 %k1, i32 4
  %kxu = urem i32 %k, 3
  %kyu = udiv i32 %k, 3
  %kx = sub i32 %kxu, 1
  %ky = sub i32 %kyu, 1
  %cv = call <2 x float> @ldT(<4 x i32> %vt, i32 %bx, i32 %by, i32 %kx, i32 %ky)
  %gmx = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 16, i32 0, i32 0)
  %gmy = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 20, i32 0, i32 0)
  %gmx2 = shl i32 %gmx, 1
  %gmy2 = shl i32 %gmy, 1
  %gmor = or i32 %gmx, %gmy
  %still = icmp eq i32 %gmor, 0
  %nstl = xor i1 %still, true
  %gmx4 = shl i32 %gmx, 2
  %gmy4 = shl i32 %gmy, 2
  %gfx = sitofp i32 %gmx4 to float
  %gfy = sitofp i32 %gmy4 to float
  %is9a = icmp eq i32 %sub, 9
  %is9 = and i1 %is9a, %notanc
  %cvx0 = extractelement <2 x float> %cv, i32 0
  %cvy0 = extractelement <2 x float> %cv, i32 1
  %cvx = select i1 %is9, float %gfx, float %cvx0
  %cvy = select i1 %is9, float %gfy, float %cvy0
  %hx = fmul float %cvx, 0.5
  %hy = fmul float %cvy, 0.5
  %cx = call i32 @rnd(float %hx)
  %cy = call i32 @rnd(float %hy)
  ; 8.5 anchor camera-escape check (r7)
  %p2 = getelementptr i8, ptr addrspace(4) %tbl, i64 240
  %v2 = load <4 x i32>, ptr addrspace(4) %p2, align 16, !invariant.load !0
  %asrc = and i32 %gk, 4095
  %aoff = shl i32 %asrc, 2
  %av = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %aoff, i32 0, i32 0)
  %avs = shl i32 %av, 16
  %avx = ashr i32 %avs, 16
  %avy = ashr i32 %av, 16
  %ax = shl i32 %avx, 1
  %ay = shl i32 %avy, 1
  %adx = sub i32 %cx, %ax
  %ady = sub i32 %cy, %ay
  %aadx = call i32 @llvm.abs.i32(i32 %adx, i1 false)
  %aady = call i32 @llvm.abs.i32(i32 %ady, i1 false)
  %aad = add i32 %aadx, %aady
  %scdx0 = sub i32 %cx, %gmx2
  %scdy0 = sub i32 %cy, %gmy2
  %scdx = call i32 @llvm.abs.i32(i32 %scdx0, i1 false)
  %scdy = call i32 @llvm.abs.i32(i32 %scdy0, i1 false)
  %scd = add i32 %scdx, %scdy
  %aadgt3 = icmp sgt i32 %aad, 3
  %aadgt16 = icmp sgt i32 %aad, 16
  %scd2 = shl i32 %scd, 1
  %scdle = icmp sle i32 %scd2, %aad
  %cam_esc = and i1 %nstl, %scdle
  %esc = or i1 %aadgt16, %cam_esc
  %afar = and i1 %aadgt3, %esc
  %ap = and i1 %isanc, %afar
  %hcx = ashr i32 %cx, 1
  %hcy = ashr i32 %cy, 1
  %scx = select i1 %ap, i32 %hcx, i32 0
  %scy = select i1 %ap, i32 %hcy, i32 0
  %Xu = add i32 %X, %scx
  %Yu = add i32 %Y, %scy
  %sad = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %Xu, i32 %Yu, i32 %cx, i32 %cy)
  %cap = select i1 %nfgk, i32 32, i32 8
  %t0 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 -1, i32 -1, i32 %cx, i32 %cy, i32 %cap)
  %t1 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 0, i32 -1, i32 %cx, i32 %cy, i32 %cap)
  %t2 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 1, i32 -1, i32 %cx, i32 %cy, i32 %cap)
  %t3 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 -1, i32 0, i32 %cx, i32 %cy, i32 %cap)
  %t4 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 0, i32 0, i32 %cx, i32 %cy, i32 %cap)
  %t5 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 1, i32 0, i32 %cx, i32 %cy, i32 %cap)
  %t6 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 -1, i32 1, i32 %cx, i32 %cy, i32 %cap)
  %t7 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 0, i32 1, i32 %cx, i32 %cy, i32 %cap)
  %t8 = call i32 @nterm(<4 x i32> %vt, i32 %bx, i32 %by, i32 1, i32 1, i32 %cx, i32 %cy, i32 %cap)
  %t4x3 = mul i32 %t4, 3
  %t4w = select i1 %nfgk, i32 %t4x3, i32 0
  %s01 = add i32 %t0, %t1
  %s23 = add i32 %t2, %t3
  %s56 = add i32 %t5, %t6
  %s78 = add i32 %t7, %t8
  %s03 = add i32 %s01, %s23
  %s58 = add i32 %s56, %s78
  %sm0 = add i32 %s03, %s58
  %sm = add i32 %sm0, %t4w
  %lsh = select i1 %nfgk, i32 2, i32 1
  %pen = shl i32 %sm, %lsh
  ; foreground bonus for the own vector
  %is0 = icmp eq i32 %sub, 0
  %fgo = and i1 %is0, %hasfg
  %bias = select i1 %fgo, i32 96, i32 0
  %apen = select i1 %ap, i32 512, i32 0
  ; OOB candidate penalty for non-supported out-of-screen taps (r6)
  %xc_0 = sub i32 %Xu, %hcx
  %xp_0 = add i32 %xc_0, %cx
  %yc_0 = sub i32 %Yu, %hcy
  %yp_0 = add i32 %yc_0, %cy
  %ox_0 = icmp ugt i32 %xc_0, 952
  %ox_1 = icmp ugt i32 %xp_0, 952
  %oy_0 = icmp ugt i32 %yc_0, 532
  %oy_1 = icmp ugt i32 %yp_0, 532
  %oobx = or i1 %ox_0, %ox_1
  %ooby = or i1 %oy_0, %oy_1
  %oob = or i1 %oobx, %ooby
  %notgm = icmp ugt i32 %scd, 3
  %notsm = icmp ugt i32 %sm0, 24
  %oob_u0 = and i1 %oob, %notgm
  %oob_u1 = and i1 %oob_u0, %notsm
  %oob_u = and i1 %oob_u1, %nfgk
  %oob_p = select i1 %oob_u, i32 96, i32 0
  %c0 = add i32 %sad, 256
  %c1 = add i32 %c0, %pen
  %c1b = add i32 %c1, %apen
  %c1c = add i32 %c1b, %oob_p
  %cost = sub i32 %c1c, %bias
  %kh = shl i32 %cost, 8
  %key0 = or i32 %kh, %sub
  ; fade frame (me1 zeroed the field on purpose): own vector only
  %fade = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 28, i32 0, i32 0)
  %nofade = icmp eq i32 %fade, 0
  %lt10 = icmp ult i32 %sub, 10
  ; 8.5 diagnostic (consts flags bit 7, inject letter 'M'): filter off, own vector only (F = FT)
  %pcnm = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnm = load <4 x i32>, ptr addrspace(4) %pcnm, align 16, !invariant.load !0
  %flm = extractelement <4 x i32> %cnm, i32 2
  %flm128 = and i32 %flm, 128
  %mon = icmp eq i32 %flm128, 0
  %nofm = and i1 %nofade, %mon
  %v1 = and i1 %lt10, %nofm
  %val = or i1 %v1, %is0
  %key = select i1 %val, i32 %key0, i32 -1
  %m = call i32 @reduce(i32 %tid, i32 %key, i32 %base, i32 16)
  %w = and i32 %m, 255
  %src = add i32 %base, %w
  %bxi = bitcast float %cvx to i32
  %byi = bitcast float %cvy to i32
  %ox = call i32 @bcast(i32 %tid, i32 %bxi, i32 %src)
  %oy = call i32 @bcast(i32 %tid, i32 %byi, i32 %src)
  br i1 %is0, label %st, label %exit
st:
  %fx = bitcast i32 %ox to float
  %fy = bitcast i32 %oy to float
  %o0 = insertelement <2 x float> undef, float %fx, i32 0
  %o1 = insertelement <2 x float> %o0, float %fy, i32 1
  %foff = shl i32 %gidx, 3
  call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %o1, <4 x i32> %vf, i32 %foff, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
