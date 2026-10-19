; user_sgprs=2
; tgid_y=1
; mode 8, last pass: build the mid frame, one thread per output pixel (240 x 135 groups of 8x8).
; table: +0x00 T#prev, +0x20 T#cur, +0x40 T#out, +0x60 S# (bilinear, clamp-to-edge, unnormalised coordinates),
;        +0x100 V#F (120x68 float2, full-res px, mid grid), +0x120 V#misc (0 = scene-change count, 1 = t),
;        +0x150 V#GK (120x68 foreground splat keys of fgs8, 8.4).
; Supports arbitrary fractional phase t in (0..1) (FG Quality real-time midpoint & FG Fast 60 Hz pulldown t1, t2):
;  * me1/mf8/fgs8 estimate motion on the t = 0.5 mid-grid; when t != 0.5, warp8 advects the block-grid lookup
;    coordinate by (t - 0.5) * v_center (clamped to +-16 px) so moving silhouettes stay aligned with their flow/splat
;    cell at any fractional phase t (identically 0 shift when t == 0.5).
;  * scene change (more than 40 % of the blocks unmatched): prev for t < 0.5, cur for t >= 0.5.
target triple = "amdgcn--"
declare <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 immarg, float, float, <8 x i32>, <4 x i32>, i1 immarg, i32 immarg, i32 immarg)
declare <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float>, i32 immarg, i32, i32, <8 x i32>, i32 immarg, i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>, i32, i32, i32 immarg)
declare <2 x i32> @llvm.amdgcn.raw.buffer.load.v2i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)
declare float @llvm.floor.f32(float)
declare float @llvm.fabs.f32(float)
declare float @llvm.maxnum.f32(float, float)
declare float @llvm.minnum.f32(float, float)
declare float @llvm.exp2.f32(float)
declare <4 x float> @llvm.fabs.v4f32(<4 x float>)
declare <4 x float> @llvm.maxnum.v4f32(<4 x float>, <4 x float>)
declare <4 x float> @llvm.minnum.v4f32(<4 x float>, <4 x float>)

; prev at p + t v, cur at p - (1 - t) v; returns {prev colour, cur colour, |prev - cur| (r+g+b)}
define internal { <4 x float>, <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v,
                           float %vx, float %vy, float %t, float %tc1,
                           <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en) alwaysinline {
  %px = fmul float %vx, %t
  %py = fmul float %vy, %t
  %cx = fmul float %vx, %tc1
  %cy = fmul float %vy, %tc1
  %up = fadd float %u, %px
  %vp = fadd float %v, %py
  %uc = fsub float %u, %cx
  %vc = fsub float %v, %cy
  %a = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %up, float %vp, <8 x i32> %tp, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %b = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %uc, float %vc, <8 x i32> %tc, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %pl0 = fcmp olt float %up, 0.0
  %pl1 = fcmp ogt float %up, 1920.0
  %pl2 = fcmp olt float %vp, 0.0
  %pl3 = fcmp ogt float %vp, 1080.0
  %pla = or i1 %pl0, %pl1
  %plb = or i1 %pl2, %pl3
  %oobp = or i1 %pla, %plb
  %cl0 = fcmp olt float %uc, 0.0
  %cl1 = fcmp ogt float %uc, 1920.0
  %cl2 = fcmp olt float %vc, 0.0
  %cl3 = fcmp ogt float %vc, 1080.0
  %cla = or i1 %cl0, %cl1
  %clb = or i1 %cl2, %cl3
  %oobc = or i1 %cla, %clb
  %nc = xor i1 %oobc, true
  %np = xor i1 %oobp, true
  %onlyp = and i1 %oobp, %nc
  %onlyc = and i1 %oobc, %np
  %one = or i1 %onlyp, %onlyc
  %a2 = select i1 %onlyp, <4 x float> %b, <4 x float> %a
  %b2 = select i1 %onlyc, <4 x float> %a, <4 x float> %b
  %e0 = call float @diff3(<4 x float> %a2, <4 x float> %b2)
  %eb = select i1 %one, float 0x3FB99999A0000000, float %e0 ; 0.1
  %both = and i1 %np, %nc
  %avx = call float @llvm.fabs.f32(float %vx)
  %avy = call float @llvm.fabs.f32(float %vy)
  %vmag = fadd float %avx, %avy
  %fastc = fcmp ogt float %vmag, 16.0
  %st0 = and i1 %str_en, %both
  %st_on = and i1 %st0, %fastc
  %eac = call float @diff3(<4 x float> %a2, <4 x float> %sc)
  %ebp = call float @diff3(<4 x float> %b2, <4 x float> %sp)
  %emx = call float @llvm.minnum.f32(float %eac, float %ebp)
  %ed0 = fsub float %emx, %es
  %ed1 = fsub float %ed0, 0.375
  %ed2 = call float @llvm.maxnum.f32(float %ed1, float 0.0)
  %spen = select i1 %st_on, float %ed2, float 0.0
  %e = fadd float %eb, %spen
  %r0 = insertvalue { <4 x float>, <4 x float>, float } undef, <4 x float> %a2, 0
  %r1 = insertvalue { <4 x float>, <4 x float>, float } %r0, <4 x float> %b2, 1
  %r2 = insertvalue { <4 x float>, <4 x float>, float } %r1, float %e, 2
  ret { <4 x float>, <4 x float>, float } %r2
}

; same with Catmull-Rom resampling: sharper than bilinear on sub-pixel shifts (fast, coherent path only)
define internal { <4 x float>, <4 x float>, float } @tapsq(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v,
                           float %vx, float %vy, float %t, float %tc1, float %csh) alwaysinline {
  %px = fmul float %vx, %t
  %py = fmul float %vy, %t
  %cx = fmul float %vx, %tc1
  %cy = fmul float %vy, %tc1
  %up = fadd float %u, %px
  %vp = fadd float %v, %py
  %uc = fsub float %u, %cx
  %vc = fsub float %v, %cy
  %a = call <4 x float> @cr(<8 x i32> %tp, <4 x i32> %sm, float %up, float %vp, float %csh)
  %b = call <4 x float> @cr(<8 x i32> %tc, <4 x i32> %sm, float %uc, float %vc, float %csh)
  %pl0 = fcmp olt float %up, 0.0
  %pl1 = fcmp ogt float %up, 1920.0
  %pl2 = fcmp olt float %vp, 0.0
  %pl3 = fcmp ogt float %vp, 1080.0
  %pla = or i1 %pl0, %pl1
  %plb = or i1 %pl2, %pl3
  %oobp = or i1 %pla, %plb
  %cl0 = fcmp olt float %uc, 0.0
  %cl1 = fcmp ogt float %uc, 1920.0
  %cl2 = fcmp olt float %vc, 0.0
  %cl3 = fcmp ogt float %vc, 1080.0
  %cla = or i1 %cl0, %cl1
  %clb = or i1 %cl2, %cl3
  %oobc = or i1 %cla, %clb
  %nc = xor i1 %oobc, true
  %np = xor i1 %oobp, true
  %onlyp = and i1 %oobp, %nc
  %onlyc = and i1 %oobc, %np
  %one = or i1 %onlyp, %onlyc
  %a2 = select i1 %onlyp, <4 x float> %b, <4 x float> %a
  %b2 = select i1 %onlyc, <4 x float> %a, <4 x float> %b
  %e0 = call float @diff3(<4 x float> %a2, <4 x float> %b2)
  %e = select i1 %one, float 0x3FB99999A0000000, float %e0 ; 0.1
  %r0 = insertvalue { <4 x float>, <4 x float>, float } undef, <4 x float> %a2, 0
  %r1 = insertvalue { <4 x float>, <4 x float>, float } %r0, <4 x float> %b2, 1
  %r2 = insertvalue { <4 x float>, <4 x float>, float } %r1, float %e, 2
  ret { <4 x float>, <4 x float>, float } %r2
}

define internal <4 x float> @crw(float %f, float %c) alwaysinline {
  %f2 = fmul float %f, %f
  %f3 = fmul float %f2, %f
  %a0 = fmul float %f2, 2.0
  %a1 = fsub float %a0, %f
  %a2 = fsub float %a1, %f3
  %w0 = fmul float %a2, %c
  %c3 = fsub float %c, 3.0
  %c2 = fsub float 2.0, %c
  %b1 = fmul float %f2, %c3
  %b2 = fmul float %f3, %c2
  %b3 = fadd float %b1, %b2
  %w1 = fadd float %b3, 1.0
  %d1 = fmul float %f, %c
  %cc2 = fmul float %c, 2.0
  %d2c = fsub float 3.0, %cc2
  %d2 = fmul float %f2, %d2c
  %d3c = fsub float %c, 2.0
  %d3 = fmul float %f3, %d3c
  %d12 = fadd float %d1, %d2
  %w2 = fadd float %d12, %d3
  %e1 = fsub float %f3, %f2
  %w3a = fmul float %e1, %c
  %w12 = fadd float %w1, %w2
  %o = fdiv arcp afn float %w2, %w12
  %r0 = insertelement <4 x float> undef, float %w0, i32 0
  %r1 = insertelement <4 x float> %r0, float %w12, i32 1
  %r2 = insertelement <4 x float> %r1, float %w3a, i32 2
  %r3 = insertelement <4 x float> %r2, float %o, i32 3
  ret <4 x float> %r3
}

define internal <4 x float> @cr(<8 x i32> %t, <4 x i32> %sm, float %u, float %v, float %csh) alwaysinline {
  %ub = fsub float %u, 0.5
  %vb = fsub float %v, 0.5
  %fu = call float @llvm.floor.f32(float %ub)
  %fv = call float @llvm.floor.f32(float %vb)
  %fx = fsub float %ub, %fu
  %fy = fsub float %vb, %fv
  %cx = fadd float %fu, 0.5
  %cy = fadd float %fv, 0.5
  %wx = call <4 x float> @crw(float %fx, float %csh)
  %wy = call <4 x float> @crw(float %fy, float %csh)
  %wx0 = extractelement <4 x float> %wx, i32 0
  %wx12 = extractelement <4 x float> %wx, i32 1
  %wx3 = extractelement <4 x float> %wx, i32 2
  %ox = extractelement <4 x float> %wx, i32 3
  %wy0 = extractelement <4 x float> %wy, i32 0
  %wy12 = extractelement <4 x float> %wy, i32 1
  %wy3 = extractelement <4 x float> %wy, i32 2
  %oy = extractelement <4 x float> %wy, i32 3
  %x12 = fadd float %cx, %ox
  %y12 = fadd float %cy, %oy
  %x0 = fsub float %cx, 1.0
  %x3 = fadd float %cx, 2.0
  %y0 = fsub float %cy, 1.0
  %y3 = fadd float %cy, 2.0
  %s0 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %x12, float %y12, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s1 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %x0, float %y12, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s2 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %x3, float %y12, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s3 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %x12, float %y0, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %s4 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %x12, float %y3, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %k0 = fmul float %wx12, %wy12
  %k1 = fmul float %wx0, %wy12
  %k2 = fmul float %wx3, %wy12
  %k3 = fmul float %wx12, %wy0
  %k4 = fmul float %wx12, %wy3
  %z0 = call <4 x float> @scale4(<4 x float> %s0, float %k0)
  %z1 = call <4 x float> @scale4(<4 x float> %s1, float %k1)
  %z2 = call <4 x float> @scale4(<4 x float> %s2, float %k2)
  %z3 = call <4 x float> @scale4(<4 x float> %s3, float %k3)
  %z4 = call <4 x float> @scale4(<4 x float> %s4, float %k4)
  %y01 = fadd <4 x float> %z0, %z1
  %y23 = fadd <4 x float> %z2, %z3
  %y014 = fadd <4 x float> %y01, %z4
  %sum = fadd <4 x float> %y014, %y23
  %q01 = fadd float %k0, %k1
  %q23 = fadd float %k2, %k3
  %q014 = fadd float %q01, %k4
  %ws = fadd float %q014, %q23
  %iw = fdiv arcp afn float 1.0, %ws
  %res = call <4 x float> @scale4(<4 x float> %sum, float %iw)
  %lo = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %res, <4 x float> zeroinitializer)
  %hi = call <4 x float> @llvm.minnum.v4f32(<4 x float> %lo, <4 x float> <float 1.0, float 1.0, float 1.0, float 1.0>)
  ret <4 x float> %hi
}

define internal { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st,
        { <4 x float>, <4 x float>, float } %c, float %w, float %t) alwaysinline {
  %acc = extractvalue { <4 x float>, float, float } %st, 0
  %sw = extractvalue { <4 x float>, float, float } %st, 1
  %em = extractvalue { <4 x float>, float, float } %st, 2
  %a = extractvalue { <4 x float>, <4 x float>, float } %c, 0
  %b = extractvalue { <4 x float>, <4 x float>, float } %c, 1
  %e = extractvalue { <4 x float>, <4 x float>, float } %c, 2
  %em1 = call float @llvm.minnum.f32(float %em, float %e)
  %d0 = fsub float %em1, %em
  %k0 = fmul float %d0, 0x40233C6620000000; 9.617967 = log2(e) / 0.15
  %sc = call float @llvm.exp2.f32(float %k0)
  %d1 = fsub float %em1, %e
  %k1 = fmul float %d1, 0x40233C6620000000; 9.617967
  %x1 = call float @llvm.exp2.f32(float %k1)
  %w5 = fadd float %w, 0x3FA99999A0000000; 0.05
  %om = fmul float %w5, %x1
  %m = call <4 x float> @lerp4(<4 x float> %a, <4 x float> %b, float %t)
  %acs = call <4 x float> @scale4(<4 x float> %acc, float %sc)
  %mo = call <4 x float> @scale4(<4 x float> %m, float %om)
  %acc1 = fadd <4 x float> %acs, %mo
  %sws = fmul float %sw, %sc
  %sw1 = fadd float %sws, %om
  %r0 = insertvalue { <4 x float>, float, float } undef, <4 x float> %acc1, 0
  %r1 = insertvalue { <4 x float>, float, float } %r0, float %sw1, 1
  %r2 = insertvalue { <4 x float>, float, float } %r1, float %em1, 2
  ret { <4 x float>, float, float } %r2
}

define internal float @diff3(<4 x float> %a, <4 x float> %b) alwaysinline {
  %d = fsub <4 x float> %a, %b
  %ad = call <4 x float> @llvm.fabs.v4f32(<4 x float> %d)
  %e0 = extractelement <4 x float> %ad, i32 0
  %e1 = extractelement <4 x float> %ad, i32 1
  %e2 = extractelement <4 x float> %ad, i32 2
  %s = fadd float %e0, %e1
  %e = fadd float %s, %e2
  ret float %e
}

define internal <4 x float> @lerp4(<4 x float> %a, <4 x float> %b, float %t) alwaysinline {
  %t0 = insertelement <4 x float> undef, float %t, i32 0
  %tv = shufflevector <4 x float> %t0, <4 x float> undef, <4 x i32> zeroinitializer
  %d = fsub <4 x float> %b, %a
  %m = fmul <4 x float> %d, %tv
  %r = fadd <4 x float> %a, %m
  ret <4 x float> %r
}

define internal <4 x float> @scale4(<4 x float> %a, float %s) alwaysinline {
  %t0 = insertelement <4 x float> undef, float %s, i32 0
  %tv = shufflevector <4 x float> %t0, <4 x float> undef, <4 x i32> zeroinitializer
  %r = fmul <4 x float> %a, %tv
  ret <4 x float> %r
}

define internal float @sat(float %x) alwaysinline {
  %a = call float @llvm.maxnum.f32(float %x, float 0.0)
  %b = call float @llvm.minnum.f32(float %a, float 1.0)
  ret float %b
}

define internal <2 x float> @ldF(<4 x i32> %vf, i32 %ix, i32 %iy) alwaysinline {
  %r = mul i32 %iy, 120
  %i = add i32 %r, %ix
  %o = shl i32 %i, 3
  %v = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vf, i32 %o, i32 0, i32 0)
  ret <2 x float> %v
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %tp = load <8 x i32>, ptr addrspace(4) %tbl, align 32, !invariant.load !0
  %ptc = getelementptr i8, ptr addrspace(4) %tbl, i64 32
  %tc = load <8 x i32>, ptr addrspace(4) %ptc, align 32, !invariant.load !0
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  %psm = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %sm = load <4 x i32>, ptr addrspace(4) %psm, align 16, !invariant.load !0
  %pf = getelementptr i8, ptr addrspace(4) %tbl, i64 256
  %vf = load <4 x i32>, ptr addrspace(4) %pf, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %x = add i32 %bxp, %lx
  %y = add i32 %byp, %ly
  %xf = uitofp i32 %x to float
  %yf = uitofp i32 %y to float
  %u = fadd float %xf, 0.5
  %v = fadd float %yf, 0.5
  %misc = call <2 x i32> @llvm.amdgcn.raw.buffer.load.v2i32(<4 x i32> %vm, i32 0, i32 0, i32 0)
  %cutn = extractelement <2 x i32> %misc, i32 0
  %tbits = extractelement <2 x i32> %misc, i32 1
  %t = bitcast i32 %tbits to float
  %tc1 = fsub float 1.0, %t
  ; kernel sharpness (inject letter S -> flags bit 10): cubic C 0.5 (Catmull-Rom) or 0.75
  %pcs = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cns = load <4 x i32>, ptr addrspace(4) %pcs, align 16, !invariant.load !0
  %fls = extractelement <4 x i32> %cns, i32 2
  %shb = and i32 %fls, 1024
  %shn = icmp ne i32 %shb, 0
  %csh = select i1 %shn, float 0.75, float 0.5
  ; fractional phase advection on the t=0.5 mid-grid F: shift lookup by (t - 0.5) * v_center (0 when t == 0.5)
  %cbx0 = lshr i32 %x, 4
  %cby0 = lshr i32 %y, 4
  %cbx = call i32 @llvm.smin.i32(i32 %cbx0, i32 119)
  %cby = call i32 @llvm.smin.i32(i32 %cby0, i32 67)
  %fc0 = call <2 x float> @ldF(<4 x i32> %vf, i32 %cbx, i32 %cby)
  %fcx = extractelement <2 x float> %fc0, i32 0
  %fcy = extractelement <2 x float> %fc0, i32 1
  %dt05 = fsub float %t, 0.5
  %shx0 = fmul float %dt05, %fcx
  %shy0 = fmul float %dt05, %fcy
  %shx1 = call float @llvm.maxnum.f32(float %shx0, float -16.0)
  %shx = call float @llvm.minnum.f32(float %shx1, float 16.0)
  %shy1 = call float @llvm.maxnum.f32(float %shy0, float -16.0)
  %shy = call float @llvm.minnum.f32(float %shy1, float 16.0)
  %xfs = fadd float %xf, %shx
  %yfs = fadd float %yf, %shy
  ; ---- 4 nearest block centres (blocks of 16 px, centres at 16 i + 8)
  %fbx0 = fmul float %xfs, 0.0625
  %fbx = fsub float %fbx0, 0.46875
  %fby0 = fmul float %yfs, 0.0625
  %fby = fsub float %fby0, 0.46875
  %flx = call float @llvm.floor.f32(float %fbx)
  %fly = call float @llvm.floor.f32(float %fby)
  %wx = fsub float %fbx, %flx
  %wy = fsub float %fby, %fly
  %ixr = fptosi float %flx to i32
  %iyr = fptosi float %fly to i32
  %ix0a = call i32 @llvm.smax.i32(i32 %ixr, i32 0)
  %ix0 = call i32 @llvm.smin.i32(i32 %ix0a, i32 119)
  %ix1r = add i32 %ixr, 1
  %ix1a = call i32 @llvm.smax.i32(i32 %ix1r, i32 0)
  %ix1 = call i32 @llvm.smin.i32(i32 %ix1a, i32 119)
  %iy0a = call i32 @llvm.smax.i32(i32 %iyr, i32 0)
  %iy0 = call i32 @llvm.smin.i32(i32 %iy0a, i32 67)
  %iy1r = add i32 %iyr, 1
  %iy1a = call i32 @llvm.smax.i32(i32 %iy1r, i32 0)
  %iy1 = call i32 @llvm.smin.i32(i32 %iy1a, i32 67)
  %f00 = call <2 x float> @ldF(<4 x i32> %vf, i32 %ix0, i32 %iy0)
  %f10 = call <2 x float> @ldF(<4 x i32> %vf, i32 %ix1, i32 %iy0)
  %f01 = call <2 x float> @ldF(<4 x i32> %vf, i32 %ix0, i32 %iy1)
  %f11 = call <2 x float> @ldF(<4 x i32> %vf, i32 %ix1, i32 %iy1)
  %x00 = extractelement <2 x float> %f00, i32 0
  %y00 = extractelement <2 x float> %f00, i32 1
  %x10 = extractelement <2 x float> %f10, i32 0
  %y10 = extractelement <2 x float> %f10, i32 1
  %x01 = extractelement <2 x float> %f01, i32 0
  %y01 = extractelement <2 x float> %f01, i32 1
  %x11 = extractelement <2 x float> %f11, i32 0
  %y11 = extractelement <2 x float> %f11, i32 1
  %am00 = call float @llvm.fabs.f32(float %x00)
  %bm00 = call float @llvm.fabs.f32(float %y00)
  %am10 = call float @llvm.fabs.f32(float %x10)
  %bm10 = call float @llvm.fabs.f32(float %y10)
  %am01 = call float @llvm.fabs.f32(float %x01)
  %bm01 = call float @llvm.fabs.f32(float %y01)
  %am11 = call float @llvm.fabs.f32(float %x11)
  %bm11 = call float @llvm.fabs.f32(float %y11)
  %l00 = fadd float %am00, %bm00
  %l10 = fadd float %am10, %bm10
  %l01 = fadd float %am01, %bm01
  %l11 = fadd float %am11, %bm11
  %lm0 = call float @llvm.maxnum.f32(float %l00, float %l10)
  %lm1 = call float @llvm.maxnum.f32(float %l01, float %l11)
  %vmx = call float @llvm.maxnum.f32(float %lm0, float %lm1)
  %iwx = fsub float 1.0, %wx
  %iwy = fsub float 1.0, %wy
  %w00 = fmul float %iwx, %iwy
  %w10 = fmul float %wx, %iwy
  %w01 = fmul float %iwx, %wy
  %w11 = fmul float %wx, %wy
  ; coherence: per component spread of the 4 vectors
  %mxa = call float @llvm.maxnum.f32(float %x00, float %x10)
  %mxb = call float @llvm.maxnum.f32(float %x01, float %x11)
  %mx = call float @llvm.maxnum.f32(float %mxa, float %mxb)
  %nxa = call float @llvm.minnum.f32(float %x00, float %x10)
  %nxb = call float @llvm.minnum.f32(float %x01, float %x11)
  %nx = call float @llvm.minnum.f32(float %nxa, float %nxb)
  %mya = call float @llvm.maxnum.f32(float %y00, float %y10)
  %myb = call float @llvm.maxnum.f32(float %y01, float %y11)
  %my = call float @llvm.maxnum.f32(float %mya, float %myb)
  %nya = call float @llvm.minnum.f32(float %y00, float %y10)
  %nyb = call float @llvm.minnum.f32(float %y01, float %y11)
  %ny = call float @llvm.minnum.f32(float %nya, float %nyb)
  %sprx = fsub float %mx, %nx
  %spry = fsub float %my, %ny
  %spr = call float @llvm.maxnum.f32(float %sprx, float %spry)
  %coh = fcmp ole float %spr, 1.0
  ; static candidate: exact point loads
  %sp = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tp, i32 0, i32 0)
  %sc = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %es = call float @diff3(<4 x float> %sp, <4 x float> %sc)
  br i1 %coh, label %fast, label %slow

fast:
  %a0 = fmul float %x00, %w00
  %a1 = fmul float %x10, %w10
  %a2 = fmul float %x01, %w01
  %a3 = fmul float %x11, %w11
  %a01 = fadd float %a0, %a1
  %a23 = fadd float %a2, %a3
  %vbx = fadd float %a01, %a23
  %b0 = fmul float %y00, %w00
  %b1 = fmul float %y10, %w10
  %b2 = fmul float %y01, %w01
  %b3 = fmul float %y11, %w11
  %b01 = fadd float %b0, %b1
  %b23 = fadd float %b2, %b3
  %vby = fadd float %b01, %b23
  %vabx = call float @llvm.fabs.f32(float %vbx)
  %vaby = call float @llvm.fabs.f32(float %vby)
  %vmb = fadd float %vabx, %vaby
  %zx = fcmp oeq float %vbx, 0.0
  %zy = fcmp oeq float %vby, 0.0
  %zv = and i1 %zx, %zy
  br i1 %zv, label %fastzero, label %fastmove
fastzero:
  %zm = call <4 x float> @lerp4(<4 x float> %sp, <4 x float> %sc, float %t)
  br label %merge
fastmove:
  %cf = call { <4 x float>, <4 x float>, float } @tapsq(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %vbx, float %vby, float %t, float %tc1, float %csh)
  %fa = extractvalue { <4 x float>, <4 x float>, float } %cf, 0
  %fb = extractvalue { <4 x float>, <4 x float>, float } %cf, 1
  %fe = extractvalue { <4 x float>, <4 x float>, float } %cf, 2
  %fm = call <4 x float> @lerp4(<4 x float> %fa, <4 x float> %fb, float %t)
  br label %merge

slow:
  %pgks = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgks = load <4 x i32>, ptr addrspace(4) %pgks, align 16, !invariant.load !0
  %xs_i0 = fptosi float %xfs to i32
  %ys_i0 = fptosi float %yfs to i32
  %gbxs0 = ashr i32 %xs_i0, 4
  %gbys0 = ashr i32 %ys_i0, 4
  %gbxs1 = call i32 @llvm.smax.i32(i32 %gbxs0, i32 0)
  %gbxs = call i32 @llvm.smin.i32(i32 %gbxs1, i32 119)
  %gbys1 = call i32 @llvm.smax.i32(i32 %gbys0, i32 0)
  %gbys = call i32 @llvm.smin.i32(i32 %gbys1, i32 67)
  %gbrs = mul i32 %gbys, 120
  %gbis = add i32 %gbrs, %gbxs
  %gbos = shl i32 %gbis, 2
  %gks = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgks, i32 %gbos, i32 0, i32 0)
  %nofgs = icmp eq i32 %gks, 0
  %pcns = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cns2 = load <4 x i32>, ptr addrspace(4) %pcns, align 16, !invariant.load !0
  %flgs = extractelement <4 x i32> %cns2, i32 2
  %nsb = and i32 %flgs, 2048
  %nson = icmp eq i32 %nsb, 0
  %sl00 = fcmp olt float %l00, 12.0
  %sl10 = fcmp olt float %l10, 12.0
  %sl01 = fcmp olt float %l01, 12.0
  %sl11 = fcmp olt float %l11, 12.0
  %cnt00 = zext i1 %sl00 to i32
  %cnt10 = zext i1 %sl10 to i32
  %cnt01 = zext i1 %sl01 to i32
  %cnt11 = zext i1 %sl11 to i32
  %cs0 = add i32 %cnt00, %cnt10
  %cs1 = add i32 %cnt01, %cnt11
  %nslow = add i32 %cs0, %cs1
  %has_sl = icmp uge i32 %nslow, 2
  %es_ok = fcmp olt float %es, 0.1875
  %se0 = and i1 %nofgs, %nson
  %se1 = and i1 %se0, %has_sl
  %str_en = and i1 %se1, %es_ok
  %c0 = call { <4 x float>, <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x00, float %y00, float %t, float %tc1, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en)
  %e0 = extractvalue { <4 x float>, <4 x float>, float } %c0, 2
  %st0 = insertvalue { <4 x float>, float, float } zeroinitializer, float %e0, 2
  %st1 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st0, { <4 x float>, <4 x float>, float } %c0, float %w00, float %t)
  %c1 = call { <4 x float>, <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x10, float %y10, float %t, float %tc1, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en)
  %st2 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st1, { <4 x float>, <4 x float>, float } %c1, float %w10, float %t)
  %c2 = call { <4 x float>, <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x01, float %y01, float %t, float %tc1, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en)
  %st3 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st2, { <4 x float>, <4 x float>, float } %c2, float %w01, float %t)
  %c3 = call { <4 x float>, <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x11, float %y11, float %t, float %tc1, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en)
  %st4 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st3, { <4 x float>, <4 x float>, float } %c3, float %w11, float %t)
  %qs = extractvalue { <4 x float>, float, float } %st4, 0
  %os = extractvalue { <4 x float>, float, float } %st4, 1
  %emin = extractvalue { <4 x float>, float, float } %st4, 2
  %ios = fdiv arcp afn float 1.0, %os
  %sm_ = call <4 x float> @scale4(<4 x float> %qs, float %ios)
  br label %merge

merge:
  %mov = phi <4 x float> [ %zm, %fastzero ], [ %fm, %fastmove ], [ %sm_, %slow ]
  %emov = phi float [ %es, %fastzero ], [ %fe, %fastmove ], [ %emin, %slow ]
  %vmg = phi float [ %vmb, %fastzero ], [ %vmb, %fastmove ], [ 0.0, %slow ]
  %fl0 = fsub float 0x3F789374C0000000, %es; 0.006
  %fl1 = fmul float %fl0, 0x4074D55560000000; 333.33334
  %ws0 = call float @sat(float %fl1)
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %xsi = fptosi float %xfs to i32
  %ysi = fptosi float %yfs to i32
  %gbx0 = ashr i32 %xsi, 4
  %gby0 = ashr i32 %ysi, 4
  %gbx1 = call i32 @llvm.smax.i32(i32 %gbx0, i32 0)
  %gbx = call i32 @llvm.smin.i32(i32 %gbx1, i32 119)
  %gby1 = call i32 @llvm.smax.i32(i32 %gby0, i32 0)
  %gby = call i32 @llvm.smin.i32(i32 %gby1, i32 67)
  %gbr = mul i32 %gby, 120
  %gbi = add i32 %gbr, %gbx
  %gbo = shl i32 %gbi, 2
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgk, i32 %gbo, i32 0, i32 0)
  %hasfg = icmp uge i32 %gk, 1048576
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %fgkill0 = and i1 %hasfg, %mfit
  %nofgn = xor i1 %nofgk, true
  %fgkill = and i1 %fgkill0, %nofgn
  %ws = select i1 %fgkill, float 0.0, float %ws0
  %stat = call <4 x float> @lerp4(<4 x float> %sp, <4 x float> %sc, float %t)
  %pcn0 = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn0 = load <4 x i32>, ptr addrspace(4) %pcn0, align 16, !invariant.load !0
  %fl0b = extractelement <4 x i32> %cn0, i32 2
  %nocfb = and i32 %fl0b, 8
  %nocf = icmp ne i32 %nocfb, 0
  %cw0 = fsub float %emov, 0.5
  %cw1 = fmul float %cw0, 2.5
  %cw2 = call float @sat(float %cw1)
  %dmv = call float @diff3(<4 x float> %mov, <4 x float> %stat)
  %bg0 = fcmp ogt float %vmg, 8.0
  %bg1 = fcmp olt float %es, 0.25
  %nfgk = xor i1 %hasfg, true
  %bg2 = and i1 %bg0, %bg1
  %bg3 = and i1 %bg2, %nfgk
  %bgs0a = fsub float %dmv, %es
  %bgs0 = fsub float %bgs0a, 0x3FBF5C2900000000
  %bgs1 = fmul float %bgs0, 4.0
  %bgs = call float @sat(float %bgs1)
  %bgw = select i1 %bg3, float %bgs, float 0.0
  %cw3 = call float @llvm.maxnum.f32(float %cw2, float %bgw)
  %cw = select i1 %nocf, float 0.0, float %cw3
  %movc = call <4 x float> @lerp4(<4 x float> %mov, <4 x float> %stat, float %cw)
  %out0 = call <4 x float> @lerp4(<4 x float> %movc, <4 x float> %stat, float %ws)
  ; scene change: > 40 % unmatched blocks -> prev when t < 0.5 (mid1 in 3-step pulldown), cur when t >= 0.5
  %cut = icmp ugt i32 %cutn, 3264
  %t_ge_half = fcmp oge float %t, 0.5
  %cut_px = select i1 %t_ge_half, <4 x float> %sc, <4 x float> %sp
  %out1 = select i1 %cut, <4 x float> %cut_px, <4 x float> %out0
  %pcn = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn = load <4 x i32>, ptr addrspace(4) %pcn, align 16, !invariant.load !0
  %flags = extractelement <4 x i32> %cn, i32 2
  %fm4 = and i32 %flags, 4
  %mk0 = icmp ne i32 %fm4, 0
  %mxy = or i32 %x, %y
  %mk1 = icmp ult i32 %mxy, 32
  %mk = and i1 %mk0, %mk1
  %hudw = extractelement <4 x i32> %cn, i32 3
  %hb0 = and i32 %hudw, 255
  %hb1a = lshr i32 %hudw, 8
  %hb1 = and i32 %hb1a, 255
  %hb2a = lshr i32 %hudw, 16
  %hb2 = and i32 %hb2a, 255
  %hb3 = lshr i32 %hudw, 24
  %hx0 = shl i32 %hb0, 3
  %hy0 = shl i32 %hb1, 3
  %hx1 = shl i32 %hb2, 3
  %hy1 = shl i32 %hb3, 3
  %hc0 = icmp uge i32 %x, %hx0
  %hc1 = icmp ult i32 %x, %hx1
  %hc2 = icmp uge i32 %y, %hy0
  %hc3 = icmp ult i32 %y, %hy1
  %hc01 = and i1 %hc0, %hc1
  %hc23 = and i1 %hc2, %hc3
  %hcx = and i1 %hc01, %hc23
  %cdx = sub i32 %x, %hx0
  %cdy = sub i32 %y, %hy0
  %cdx2 = mul i32 %cdx, %cdx
  %cdy2 = mul i32 %cdy, %cdy
  %cd2 = add i32 %cdx2, %cdy2
  %crr = mul i32 %hx1, %hx1
  %cin = icmp ule i32 %cd2, %crr
  %iscirc = icmp eq i32 %hy1, 0
  %hcsel = select i1 %iscirc, i1 %cin, i1 %hcx
  %hnz = icmp ne i32 %hudw, 0
  %inhud = and i1 %hcsel, %hnz
  %out1h = select i1 %inhud, <4 x float> %sc, <4 x float> %out1
  %out2 = select i1 %mk, <4 x float> <float 1.0, float 0.0, float 1.0, float 1.0>, <4 x float> %out1h
  %alpha = extractelement <4 x float> %sc, i32 3
  %out = insertelement <4 x float> %out2, float %alpha, i32 3
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %out, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
