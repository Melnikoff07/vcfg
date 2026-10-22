; user_sgprs=2
; tgid_y=1
; mode 8, last pass: build the mid frame, one thread per output pixel (wx x wy groups of 8x8).
; table: +0x00 T#prev, +0x20 T#cur, +0x40 T#out, +0x60 S# (bilinear, clamp-to-edge, unnormalised coordinates),
;        +0x100 V#F (120x68 float2, 1080p full-res px, mid grid), +0x120 V#misc (0 = scene-change count, 1 = t),
;        +0x150 V#GK (120x68 foreground splat keys of fgs8, 8.4).
; Combines Track 1 (side5 @side_lerp + occlusion relief + 6-unit ALU Micro-MLP @mlp_cw) and Track 2 (Fixed-Pyramid
; Pro Multi-Resolution 720p/900p/1080p/1440p/2160p reading W-1 and H-1 from T#out dword 2).
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

; prev at (u1080 + t*v)*s, cur at (u1080 - (1 - t)*v)*s; returns {side-blended colour m, occlusion-relieved error e}
define internal { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v,
                           float %vx, float %vy, float %t, float %tc1, float %s,
                           <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en,
                           float %rx0, float %rx1, float %ry0, float %ry1,
                           float %w_occ, float %div_b) alwaysinline {
  %px = fmul float %vx, %t
  %py = fmul float %vy, %t
  %cx = fmul float %vx, %tc1
  %cy = fmul float %vy, %tc1
  %up = fadd float %u, %px
  %vp = fadd float %v, %py
  %uc = fsub float %u, %cx
  %vc = fsub float %v, %cy
  %ups = fmul float %up, %s
  %vps = fmul float %vp, %s
  %ucs = fmul float %uc, %s
  %vcs = fmul float %vc, %s
  %a = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %ups, float %vps, <8 x i32> %tp, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %b = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %ucs, float %vcs, <8 x i32> %tc, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %pl0 = fcmp olt float %up, 0.5
  %pl1 = fcmp ogt float %up, 1919.5
  %pl2 = fcmp olt float %vp, 0.5
  %pl3 = fcmp ogt float %vp, 1079.5
  %pla = or i1 %pl0, %pl1
  %plb = or i1 %pl2, %pl3
  %oobp0 = or i1 %pla, %plb
  %or0 = fcmp oge float %up, %rx0
  %or1 = fcmp olt float %up, %rx1
  %or2 = fcmp oge float %vp, %ry0
  %or3 = fcmp olt float %vp, %ry1
  %ora = and i1 %or0, %or1
  %orb = and i1 %or2, %or3
  %inovl = and i1 %ora, %orb
  %oobp = or i1 %oobp0, %inovl
  %cl0 = fcmp olt float %uc, 0.5
  %cl1 = fcmp ogt float %uc, 1919.5
  %cl2 = fcmp olt float %vc, 0.5
  %cl3 = fcmp ogt float %vc, 1079.5
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
  %eb = select i1 %one, float 0x3F947AE140000000, float %e0 ; 0.02
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
  %eo0 = fsub float %eb, 0x3FE19999A0000000 ; 0.55
  %eo1 = fmul float %eo0, 4.0
  %eo_g = call float @sat(float %eo1)
  %g_occ = fmul float %w_occ, %eo_g
  %dv0 = fmul float %vmag, 0.25
  %dv1 = fsub float 1.0, %dv0
  %dv_g = call float @sat(float %dv1)
  %div_eff = fmul float %div_b, %dv_g
  %sl = call { <4 x float>, float } @side_lerp(<4 x float> %a2, <4 x float> %b2, <4 x float> %sp, <4 x float> %sc, float %eb, float %eac, float %ebp, float %t, float %vmag, float %g_occ, float %div_eff)
  %m = extractvalue { <4 x float>, float } %sl, 0
  %adadb = extractvalue { <4 x float>, float } %sl, 1
  %rk0 = fmul float %g_occ, 0x3FDEB851E0000000 ; 0.48
  %rk = fadd float %rk0, 0x3FB99999A0000000 ; 0.10 + 0.48 * g_occ
  %rel0 = fmul float %adadb, %rk
  %rmax = fmul float %eb, 0x3FE3333340000000 ; 0.60
  %rel = call float @llvm.minnum.f32(float %rel0, float %rmax)
  %e_raw = fadd float %eb, %spen
  %e_sub = fsub float %e_raw, %rel
  %e = call float @llvm.maxnum.f32(float %e_sub, float 0.0)
  %r0 = insertvalue { <4 x float>, float } undef, <4 x float> %m, 0
  %r1 = insertvalue { <4 x float>, float } %r0, float %e, 1
  ret { <4 x float>, float } %r1
}

; same with Catmull-Rom resampling: sharper than bilinear on sub-pixel shifts (fast, coherent path only)
define internal { <4 x float>, <4 x float>, float } @tapsq(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v,
                           float %vx, float %vy, float %t, float %tc1, float %s,
                           float %csh, float %rx0, float %rx1, float %ry0, float %ry1) alwaysinline {
  %px = fmul float %vx, %t
  %py = fmul float %vy, %t
  %cx = fmul float %vx, %tc1
  %cy = fmul float %vy, %tc1
  %up = fadd float %u, %px
  %vp = fadd float %v, %py
  %uc = fsub float %u, %cx
  %vc = fsub float %v, %cy
  %ups = fmul float %up, %s
  %vps = fmul float %vp, %s
  %ucs = fmul float %uc, %s
  %vcs = fmul float %vc, %s
  %pl0 = fcmp olt float %up, 0.5
  %pl1 = fcmp ogt float %up, 1919.5
  %pl2 = fcmp olt float %vp, 0.5
  %pl3 = fcmp ogt float %vp, 1079.5
  %pla = or i1 %pl0, %pl1
  %plb = or i1 %pl2, %pl3
  %oobp0 = or i1 %pla, %plb
  %or0 = fcmp oge float %up, %rx0
  %or1 = fcmp olt float %up, %rx1
  %or2 = fcmp oge float %vp, %ry0
  %or3 = fcmp olt float %vp, %ry1
  %ora = and i1 %or0, %or1
  %orb = and i1 %or2, %or3
  %inovl = and i1 %ora, %orb
  %oobp = or i1 %oobp0, %inovl
  %cl0 = fcmp olt float %uc, 0.5
  %cl1 = fcmp ogt float %uc, 1919.5
  %cl2 = fcmp olt float %vc, 0.5
  %cl3 = fcmp ogt float %vc, 1079.5
  %cla = or i1 %cl0, %cl1
  %clb = or i1 %cl2, %cl3
  %oobc = or i1 %cla, %clb
  %any_oob = or i1 %oobp, %oobc
  %csh_b = select i1 %any_oob, float 0.0, float %csh
  %a = call <4 x float> @cr(<8 x i32> %tp, <4 x i32> %sm, float %ups, float %vps, float %csh_b)
  %b = call <4 x float> @cr(<8 x i32> %tc, <4 x i32> %sm, float %ucs, float %vcs, float %csh_b)
  %nc = xor i1 %oobc, true
  %np = xor i1 %oobp, true
  %onlyp = and i1 %oobp, %nc
  %onlyc = and i1 %oobc, %np
  %a2 = select i1 %onlyp, <4 x float> %b, <4 x float> %a
  %b2 = select i1 %onlyc, <4 x float> %a, <4 x float> %b
  %e = call float @diff3(<4 x float> %a2, <4 x float> %b2)
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
entry:
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
  %ox = extractelement <4 x float> %wx, i32 3
  %oy = extractelement <4 x float> %wy, i32 3
  %x12 = fadd float %cx, %ox
  %y12 = fadd float %cy, %oy
  %s0 = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %x12, float %y12, <8 x i32> %t, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %cpos = fcmp ogt float %csh, 0.0
  br i1 %cpos, label %cr5, label %crdone
cr5:
  %wx0 = extractelement <4 x float> %wx, i32 0
  %wx12 = extractelement <4 x float> %wx, i32 1
  %wx3 = extractelement <4 x float> %wx, i32 2
  %wy0 = extractelement <4 x float> %wy, i32 0
  %wy12 = extractelement <4 x float> %wy, i32 1
  %wy3 = extractelement <4 x float> %wy, i32 2
  %x0 = fsub float %cx, 1.0
  %x3 = fadd float %cx, 2.0
  %y0 = fsub float %cy, 1.0
  %y3 = fadd float %cy, 2.0
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
  br label %crdone
crdone:
  %out = phi <4 x float> [ %s0, %entry ], [ %hi, %cr5 ]
  ret <4 x float> %out
}

define internal { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st,
        { <4 x float>, float } %c, float %w) alwaysinline {
  %acc = extractvalue { <4 x float>, float, float } %st, 0
  %sw = extractvalue { <4 x float>, float, float } %st, 1
  %em = extractvalue { <4 x float>, float, float } %st, 2
  %m = extractvalue { <4 x float>, float } %c, 0
  %e = extractvalue { <4 x float>, float } %c, 1
  %em1 = call float @llvm.minnum.f32(float %em, float %e)
  %d0 = fsub float %em1, %em
  %k0 = fmul float %d0, 8.0
  %sc = call float @llvm.exp2.f32(float %k0)
  %d1 = fsub float %em1, %e
  %k1 = fmul float %d1, 8.0
  %x1 = call float @llvm.exp2.f32(float %k1)
  %w5 = fadd float %w, 0x3FB47AE140000000; 0.08
  %om = fmul float %w5, %x1
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

define internal float @sum3(<4 x float> %a) alwaysinline {
  %e0 = extractelement <4 x float> %a, i32 0
  %e1 = extractelement <4 x float> %a, i32 1
  %e2 = extractelement <4 x float> %a, i32 2
  %s = fadd float %e0, %e1
  %r = fadd float %s, %e2
  ret float %r
}

define internal { <4 x float>, float } @side_lerp(<4 x float> %a2, <4 x float> %b2, <4 x float> %sp, <4 x float> %sc,
                                                  float %e0, float %eac, float %ebp, float %t,
                                                  float %vmag, float %w_occ, float %div_b) alwaysinline {
  %ga0 = call float @diff3(<4 x float> %a2, <4 x float> %sp)
  %gb0 = call float @diff3(<4 x float> %b2, <4 x float> %sc)
  ; when candidate v_k is slow/near-zero, a2 ~ sp and b2 ~ sc spatially, so penalize trivial self-agreement
  ; under high 3x3 spread so cross-frame consistency (eac vs ebp) and 3x3 divergence break occlusion symmetry
  %wsf0 = fmul float %vmag, 0x3FC0000000000000 ; 0.125
  %wsf1 = call float @sat(float %wsf0)
  %io = fsub float 1.0, %w_occ
  %wsf = call float @llvm.maxnum.f32(float %wsf1, float %io)
  %ga = call float @llvm.maxnum.f32(float %ga0, float 0.0)
  %gb = call float @llvm.maxnum.f32(float %gb0, float 0.0)
  %da0 = call float @llvm.minnum.f32(float %eac, float %ga)
  %db0 = call float @llvm.minnum.f32(float %ebp, float %gb)
  %da = fadd float %da0, 0.0
  %db = fadd float %db0, 0.0
  %dba = fsub <4 x float> %b2, %a2
  %d_lu = call float @sum3(<4 x float> %dba)
  %d_min = fsub float %da, %db
  %d_cr = fsub float %eac, %ebp
  %d_sf0 = fsub float %ga, %gb
  %d_sf = fmul float %d_sf0, %wsf
  %ad_min = call float @llvm.fabs.f32(float %d_min)
  %ad_div0 = call float @llvm.fabs.f32(float %div_b)
  %ad_div = fmul float %ad_div0, %w_occ
  %adadb = call float @llvm.maxnum.f32(float %ad_min, float %ad_div)
  %em0 = fsub float %e0, 0x3F947AE140000000
  %em1 = fmul float %em0, 6.0
  %g_em = call float @sat(float %em1)
  %t0 = fmul float %d_min, 0x3FD51EB860000000
  %t1 = fmul float %d_cr, 0x3FB1EB8520000000
  %t2 = fmul float %d_sf, 0x3FC1EB8520000000
  %t3 = fmul float %d_lu, 0x3FA47AE140000000
  %s01 = fadd float %t0, %t1
  %s23 = fadd float %t2, %t3
  %raw0 = fadd float %s01, %s23
  %o12 = fmul float %s01, 1.625
  %o_sum = fadd float %o12, %div_b
  %o_term = fmul float %w_occ, %o_sum
  %raw = fadd float %raw0, %o_term
  %graw = fmul float %g_em, %raw
  %b_occ = fmul float %w_occ, 0x3FDB851EC0000000 ; 0.43
  %b_hi = fadd float %b_occ, 0x3FB1EB8520000000 ; 0.07 + 0.43 * w_occ
  %b_lo = fsub float 0.0, %b_hi
  %cl0 = call float @llvm.maxnum.f32(float %graw, float %b_lo)
  %dlt = call float @llvm.minnum.f32(float %cl0, float %b_hi)
  %tab0 = fadd float %t, %dlt
  %tab = call float @sat(float %tab0)
  %m = call <4 x float> @lerp4(<4 x float> %a2, <4 x float> %b2, float %tab)
  %r0 = insertvalue { <4 x float>, float } undef, <4 x float> %m, 0
  %r1 = insertvalue { <4 x float>, float } %r0, float %adadb, 1
  ret { <4 x float>, float } %r1
}

define internal float @mlp_cw(float %x0, float %x1, float %x2, float %x3, float %x4, float %x5, float %x6) alwaysinline {
  %acc0 = fadd float 0.0, 0x3FA0F414A0000000
  %u0_0 = fmul float %x0, 0x3FC3F44680000000
  %s0_0 = fadd float %u0_0, 0x3FAA7194A0000000
  %u0_1 = fmul float %x1, 0xBFB065BB20000000
  %s0_1 = fadd float %s0_0, %u0_1
  %u0_2 = fmul float %x2, 0x3FCC696560000000
  %s0_2 = fadd float %s0_1, %u0_2
  %u0_3 = fmul float %x3, 0xBFCD760280000000
  %s0_3 = fadd float %s0_2, %u0_3
  %u0_4 = fmul float %x4, 0x3FCA876A80000000
  %s0_4 = fadd float %s0_3, %u0_4
  %u0_5 = fmul float %x5, 0x3F91AA4D20000000
  %s0_5 = fadd float %s0_4, %u0_5
  %u0_6 = fmul float %x6, 0x3F9C0450A0000000
  %s0_6 = fadd float %s0_5, %u0_6
  %h0 = call float @sat(float %s0_6)
  %p0 = fmul float %h0, 0x3FC5607260000000
  %acc1 = fadd float %acc0, %p0
  %u1_0 = fmul float %x0, 0x3FB7DEC400000000
  %s1_0 = fadd float %u1_0, 0x3FB502A940000000
  %u1_1 = fmul float %x1, 0xBFCE7815E0000000
  %s1_1 = fadd float %s1_0, %u1_1
  %u1_2 = fmul float %x2, 0x3FB3174D20000000
  %s1_2 = fadd float %s1_1, %u1_2
  %u1_3 = fmul float %x3, 0x3FE3445960000000
  %s1_3 = fadd float %s1_2, %u1_3
  %u1_4 = fmul float %x4, 0x3F8D88B180000000
  %s1_4 = fadd float %s1_3, %u1_4
  %u1_5 = fmul float %x5, 0xBFA1481FA0000000
  %s1_5 = fadd float %s1_4, %u1_5
  %u1_6 = fmul float %x6, 0x3F9858A580000000
  %s1_6 = fadd float %s1_5, %u1_6
  %h1 = call float @sat(float %s1_6)
  %p1 = fmul float %h1, 0xBFBEEB9EA0000000
  %acc2 = fadd float %acc1, %p1
  %u2_0 = fmul float %x0, 0x3FA57A3F20000000
  %s2_0 = fadd float %u2_0, 0x3FB1FFCAE0000000
  %u2_1 = fmul float %x1, 0xBFBD902FC0000000
  %s2_1 = fadd float %s2_0, %u2_1
  %u2_2 = fmul float %x2, 0x3FBC89F040000000
  %s2_2 = fadd float %s2_1, %u2_2
  %u2_3 = fmul float %x3, 0x3FE3C65A20000000
  %s2_3 = fadd float %s2_2, %u2_3
  %u2_4 = fmul float %x4, 0xBFB5D25000000000
  %s2_4 = fadd float %s2_3, %u2_4
  %u2_5 = fmul float %x5, 0xBF8994F1E0000000
  %s2_5 = fadd float %s2_4, %u2_5
  %u2_6 = fmul float %x6, 0xBF949280A0000000
  %s2_6 = fadd float %s2_5, %u2_6
  %h2 = call float @sat(float %s2_6)
  %p2 = fmul float %h2, 0xBFC311BD60000000
  %acc3 = fadd float %acc2, %p2
  %u3_0 = fmul float %x0, 0xBFB118CFE0000000
  %s3_0 = fadd float %u3_0, 0x3FB5F7C1A0000000
  %u3_1 = fmul float %x1, 0xBFC6C64040000000
  %s3_1 = fadd float %s3_0, %u3_1
  %u3_2 = fmul float %x2, 0xBF75439B40000000
  %s3_2 = fadd float %s3_1, %u3_2
  %u3_3 = fmul float %x3, 0x3FE5C938E0000000
  %s3_3 = fadd float %s3_2, %u3_3
  %u3_4 = fmul float %x4, 0xBFBD267DC0000000
  %s3_4 = fadd float %s3_3, %u3_4
  %u3_5 = fmul float %x5, 0xBF8AD32C60000000
  %s3_5 = fadd float %s3_4, %u3_5
  %u3_6 = fmul float %x6, 0xBF957503E0000000
  %s3_6 = fadd float %s3_5, %u3_6
  %h3 = call float @sat(float %s3_6)
  %p3 = fmul float %h3, 0xBFC9E87220000000
  %acc4 = fadd float %acc3, %p3
  %u4_0 = fmul float %x0, 0x3FB8A754A0000000
  %s4_0 = fadd float %u4_0, 0x3FA75E5680000000
  %u4_1 = fmul float %x1, 0xBFD45CA740000000
  %s4_1 = fadd float %s4_0, %u4_1
  %u4_2 = fmul float %x2, 0xBFC288DB80000000
  %s4_2 = fadd float %s4_1, %u4_2
  %u4_3 = fmul float %x3, 0x3FE207A9E0000000
  %s4_3 = fadd float %s4_2, %u4_3
  %u4_4 = fmul float %x4, 0x3FBB9C1E00000000
  %s4_4 = fadd float %s4_3, %u4_4
  %u4_5 = fmul float %x5, 0xBF973F9A40000000
  %s4_5 = fadd float %s4_4, %u4_5
  %u4_6 = fmul float %x6, 0x3F725395C0000000
  %s4_6 = fadd float %s4_5, %u4_6
  %h4 = call float @sat(float %s4_6)
  %p4 = fmul float %h4, 0xBFB4C43480000000
  %acc5 = fadd float %acc4, %p4
  %u5_0 = fmul float %x0, 0x3FC2543F20000000
  %s5_0 = fadd float %u5_0, 0x3FB94845C0000000
  %u5_1 = fmul float %x1, 0xBFC28ECE00000000
  %s5_1 = fadd float %s5_0, %u5_1
  %u5_2 = fmul float %x2, 0x3FC7A203E0000000
  %s5_2 = fadd float %s5_1, %u5_2
  %u5_3 = fmul float %x3, 0x3F9AF58FC0000000
  %s5_3 = fadd float %s5_2, %u5_3
  %u5_4 = fmul float %x4, 0x3FB81CA580000000
  %s5_4 = fadd float %s5_3, %u5_4
  %u5_5 = fmul float %x5, 0x3F88B067A0000000
  %s5_5 = fadd float %s5_4, %u5_5
  %u5_6 = fmul float %x6, 0x3F8FB664A0000000
  %s5_6 = fadd float %s5_5, %u5_6
  %h5 = call float @sat(float %s5_6)
  %p5 = fmul float %h5, 0x3FC2CAC0E0000000
  %acc6 = fadd float %acc5, %p5
  ret float %acc6
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
  ; overlay rectangle (VCFG osd8, drawn last into the frame that becomes T#prev): prev taps inside it use cur
  %porc = getelementptr i8, ptr addrspace(4) %tbl, i64 544
  %orc = load <4 x i32>, ptr addrspace(4) %porc, align 16, !invariant.load !0
  %oxy = extractelement <4 x i32> %orc, i32 2
  %owh = extractelement <4 x i32> %orc, i32 3
  %ox0i = and i32 %oxy, 4095
  %oy0s = lshr i32 %oxy, 16
  %oy0i = and i32 %oy0s, 4095
  %ocl = and i32 %owh, 255
  %orw0 = lshr i32 %owh, 8
  %orw = and i32 %orw0, 255
  %ocw = shl i32 %ocl, 4
  %orh = shl i32 %orw, 4
  %ox1i = add i32 %ox0i, %ocw
  %oy1i = add i32 %oy0i, %orh
  %oxf0 = uitofp i32 %ox0i to float
  %oyf0 = uitofp i32 %oy0i to float
  %oxf1 = uitofp i32 %ox1i to float
  %oyf1 = uitofp i32 %oy1i to float
  %oon = icmp ne i32 %ocl, 0
  %pto = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %to = load <8 x i32>, ptr addrspace(4) %pto, align 32, !invariant.load !0
  %psm = getelementptr i8, ptr addrspace(4) %tbl, i64 96
  %sm = load <4 x i32>, ptr addrspace(4) %psm, align 16, !invariant.load !0
  %pf = getelementptr i8, ptr addrspace(4) %tbl, i64 256
  %vf = load <4 x i32>, ptr addrspace(4) %pf, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %to2 = extractelement <8 x i32> %to, i32 2
  %wm1 = and i32 %to2, 16383
  %is4k = icmp eq i32 %wm1, 3839
  %is1440 = icmp eq i32 %wm1, 2559
  %is900 = icmp eq i32 %wm1, 1599
  %is720 = icmp eq i32 %wm1, 1279
  %s0 = select i1 %is720, float 0x3FE5555560000000, float 1.0
  %s1 = select i1 %is900, float 0x3FEAAAAA00000000, float %s0
  %s2 = select i1 %is1440, float 0x3FF5555560000000, float %s1
  %s = select i1 %is4k, float 2.0, float %s2
  %is0 = select i1 %is720, float 1.5, float 1.0
  %is1 = select i1 %is900, float 0x3FF3333340000000, float %is0
  %is2 = select i1 %is1440, float 0.75, float %is1
  %inv_s = select i1 %is4k, float 0.5, float %is2
  %oxf0_s = fmul float %oxf0, %inv_s
  %oyf0_s = fmul float %oyf0, %inv_s
  %oxf1_s = fmul float %oxf1, %inv_s
  %oyf1_s = fmul float %oyf1, %inv_s
  %oxm2 = fsub float %oxf0_s, 3.0
  %oym2 = fsub float %oyf0_s, 3.0
  %oxp2 = fadd float %oxf1_s, 3.0
  %oyp2 = fadd float %oyf1_s, 3.0
  %rx0_s = select i1 %oon, float %oxm2, float -1000.0
  %rx1_s = select i1 %oon, float %oxp2, float -1000.0
  %ry0_s = select i1 %oon, float %oym2, float -1000.0
  %ry1_s = select i1 %oon, float %oyp2, float -1000.0
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %x = add i32 %bxp, %lx
  %y = add i32 %byp, %ly
  %xf = uitofp i32 %x to float
  %yf = uitofp i32 %y to float
  %u_nat = fadd float %xf, 0.5
  %v_nat = fadd float %yf, 0.5
  %u = fmul float %u_nat, %inv_s
  %v = fmul float %v_nat, %inv_s
  %xf1080 = fsub float %u, 0.5
  %yf1080 = fsub float %v, 0.5
  %ubx0 = fmul float %u, 0.0625
  %vby0 = fmul float %v, 0.0625
  %cbx0 = fptosi float %ubx0 to i32
  %cby0 = fptosi float %vby0 to i32
  %cbx1 = call i32 @llvm.smax.i32(i32 %cbx0, i32 0)
  %cbx = call i32 @llvm.smin.i32(i32 %cbx1, i32 119)
  %cby1 = call i32 @llvm.smax.i32(i32 %cby0, i32 0)
  %cby = call i32 @llvm.smin.i32(i32 %cby1, i32 67)
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
  %xfs = fadd float %xf1080, %shx
  %yfs = fadd float %yf1080, %shy
  ; ---- 4 nearest block centres (blocks of 16 px in 1080p space, centres at 16 i + 8)
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
  %xh_r = fcmp olt float %wx, 0.5
  %yh_b0 = fcmp olt float %wy, 0.5
  %gbx = select i1 %xh_r, i32 %ix0, i32 %ix1
  %gby = select i1 %yh_b0, i32 %iy0, i32 %iy1
  %gbr = mul i32 %gby, 120
  %gbi = add i32 %gbr, %gbx
  %gbo = shl i32 %gbi, 2
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
  %axm0 = call float @llvm.fabs.f32(float %mx)
  %axm1 = call float @llvm.fabs.f32(float %nx)
  %axm = call float @llvm.maxnum.f32(float %axm0, float %axm1)
  %aym0 = call float @llvm.fabs.f32(float %my)
  %aym1 = call float @llvm.fabs.f32(float %ny)
  %aym = call float @llvm.maxnum.f32(float %aym0, float %aym1)
  %vm4 = fadd float %axm, %aym
  %coh_t0 = fmul float %vm4, 0x3FC0000000000000 ; 0.125 * vm4
  %coh_thr = call float @llvm.maxnum.f32(float %coh_t0, float 2.0)
  %coh = fcmp ole float %spr, %coh_thr
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
  %vm0 = fsub float %vmb, 9.0
  %vm1 = fmul float %vm0, 0x3FC1EB8520000000
  %vms = call float @sat(float %vm1)
  %csc = fsub float 1.0, %vms
  %csh_e = fmul float %csh, %csc
  %cf = call { <4 x float>, <4 x float>, float } @tapsq(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %vbx, float %vby, float %t, float %tc1, float %s, float %csh_e, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s)
  %fa = extractvalue { <4 x float>, <4 x float>, float } %cf, 0
  %fb = extractvalue { <4 x float>, <4 x float>, float } %cf, 1
  %fe = extractvalue { <4 x float>, <4 x float>, float } %cf, 2
  %c_split = fcmp oge float %fe, -1.0
  br i1 %c_split, label %fastside, label %fastzero
fastside:
  %eac_f = call float @diff3(<4 x float> %fa, <4 x float> %sc)
  %ebp_f = call float @diff3(<4 x float> %fb, <4 x float> %sp)
  %slf = call { <4 x float>, float } @side_lerp(<4 x float> %fa, <4 x float> %fb, <4 x float> %sp, <4 x float> %sc, float %fe, float %eac_f, float %ebp_f, float %t, float %vmb, float 0.0, float 0.0)
  %fm = extractvalue { <4 x float>, float } %slf, 0
  %fad = extractvalue { <4 x float>, float } %slf, 1
  %frel0 = fmul float %fad, 0x3FB99999A0000000 ; 0.10
  %frmax = fmul float %fe, 0x3FE3333340000000 ; 0.60
  %frel = call float @llvm.minnum.f32(float %frel0, float %frmax)
  %fe_sub = fsub float %fe, %frel
  %fe_rel = call float @llvm.maxnum.f32(float %fe_sub, float 0.0)
  br label %merge

slow:
  %ix_d = select i1 %xh_r, i32 -1, i32 1
  %ix2_0 = add i32 %gbx, %ix_d
  %ix2_1 = call i32 @llvm.smax.i32(i32 %ix2_0, i32 0)
  %ix2 = call i32 @llvm.smin.i32(i32 %ix2_1, i32 119)
  %iy_d = select i1 %yh_b0, i32 -1, i32 1
  %iy2_0 = add i32 %gby, %iy_d
  %iy2_1 = call i32 @llvm.smax.i32(i32 %iy2_0, i32 0)
  %iy2 = call i32 @llvm.smin.i32(i32 %iy2_1, i32 67)
  %f20 = call <2 x float> @ldF(<4 x i32> %vf, i32 %ix2, i32 %gby)
  %f02 = call <2 x float> @ldF(<4 x i32> %vf, i32 %gbx, i32 %iy2)
  %x20 = extractelement <2 x float> %f20, i32 0
  %y20 = extractelement <2 x float> %f20, i32 1
  %x02 = extractelement <2 x float> %f02, i32 0
  %y02 = extractelement <2 x float> %f02, i32 1
  %am00 = call float @llvm.fabs.f32(float %x00)
  %bm00 = call float @llvm.fabs.f32(float %y00)
  %am10 = call float @llvm.fabs.f32(float %x10)
  %bm10 = call float @llvm.fabs.f32(float %y10)
  %am01 = call float @llvm.fabs.f32(float %x01)
  %bm01 = call float @llvm.fabs.f32(float %y01)
  %am11 = call float @llvm.fabs.f32(float %x11)
  %bm11 = call float @llvm.fabs.f32(float %y11)
  %am20 = call float @llvm.fabs.f32(float %x20)
  %bm20 = call float @llvm.fabs.f32(float %y20)
  %am02 = call float @llvm.fabs.f32(float %x02)
  %bm02 = call float @llvm.fabs.f32(float %y02)
  %l00 = fadd float %am00, %bm00
  %l10 = fadd float %am10, %bm10
  %l01 = fadd float %am01, %bm01
  %l11 = fadd float %am11, %bm11
  %l20 = fadd float %am20, %bm20
  %l02 = fadd float %am02, %bm02
  %lm0 = call float @llvm.maxnum.f32(float %l00, float %l10)
  %lm1 = call float @llvm.maxnum.f32(float %l01, float %l11)
  %lm2 = call float @llvm.maxnum.f32(float %l20, float %l02)
  %vmx4 = call float @llvm.maxnum.f32(float %lm0, float %lm1)
  %vmx = call float @llvm.maxnum.f32(float %vmx4, float %lm2)
  %ln0 = call float @llvm.minnum.f32(float %l00, float %l10)
  %ln1 = call float @llvm.minnum.f32(float %l01, float %l11)
  %ln2 = call float @llvm.minnum.f32(float %l20, float %l02)
  %vmn4 = call float @llvm.minnum.f32(float %ln0, float %ln1)
  %vmn = call float @llvm.minnum.f32(float %vmn4, float %ln2)
  %vsp = fsub float %vmx, %vmn
  %wo0 = fsub float %vsp, 12.0
  %wo1 = fmul float %wo0, 0.5
  %w_occ_s = call float @sat(float %wo1)
  %dxq0 = fsub float %x10, %x00
  %dxq1 = fsub float %x11, %x01
  %dxqs = fadd float %dxq0, %dxq1
  %dxq = fmul float %dxqs, 0.5
  %dx2_r = fsub float %x20, %x10
  %dx2_l = fsub float %x00, %x20
  %dx2 = select i1 %xh_r, float %dx2_l, float %dx2_r
  %div_x = fadd float %dxq, %dx2
  %dyq0 = fsub float %y01, %y00
  %dyq1 = fsub float %y11, %y10
  %dyqs = fadd float %dyq0, %dyq1
  %dyq = fmul float %dyqs, 0.5
  %dy2_b = fsub float %y02, %y01
  %dy2_t = fsub float %y00, %y02
  %dy2 = select i1 %yh_b0, float %dy2_t, float %dy2_b
  %div_y = fadd float %dyq, %dy2
  %div = fadd float %div_x, %div_y
  %div_s = fmul float %div, 0xBFA1EB8520000000 ; -0.035
  %div_c0 = call float @llvm.maxnum.f32(float %div_s, float 0xBFD6666660000000) ; -0.35
  %div_b_s = call float @llvm.minnum.f32(float %div_c0, float 0x3FD6666660000000) ; 0.35
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
  %pgks = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgks = load <4 x i32>, ptr addrspace(4) %pgks, align 16, !invariant.load !0
  %gks = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgks, i32 %gbo, i32 0, i32 0)
  %nofgs = icmp eq i32 %gks, 0
  %pcns = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cns2 = load <4 x i32>, ptr addrspace(4) %pcns, align 16, !invariant.load !0
  %flgs = extractelement <4 x i32> %cns2, i32 2
  %nsb = and i32 %flgs, 2048
  %nson = icmp eq i32 %nsb, 0
  %es_ok = fcmp olt float %es, 0.1875
  %se0 = and i1 %nofgs, %nson
  %se1 = and i1 %se0, %has_sl
  %str_en = and i1 %se1, %es_ok
  %c0 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x00, float %y00, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s)
  %e0 = extractvalue { <4 x float>, float } %c0, 1
  %st0 = insertvalue { <4 x float>, float, float } zeroinitializer, float %e0, 2
  %st1 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st0, { <4 x float>, float } %c0, float %w00)
  %c1 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x10, float %y10, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s)
  %st2 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st1, { <4 x float>, float } %c1, float %w10)
  %c2 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x01, float %y01, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s)
  %st3 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st2, { <4 x float>, float } %c2, float %w01)
  %c3 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u, float %v, float %x11, float %y11, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s)
  %st4 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st3, { <4 x float>, float } %c3, float %w11)
  %qs = extractvalue { <4 x float>, float, float } %st4, 0
  %os = extractvalue { <4 x float>, float, float } %st4, 1
  %emin = extractvalue { <4 x float>, float, float } %st4, 2
  %ios = fdiv arcp afn float 1.0, %os
  %sm_ = call <4 x float> @scale4(<4 x float> %qs, float %ios)
  br label %merge

merge:
  %mov = phi <4 x float> [ %zm, %fastzero ], [ %fm, %fastside ], [ %sm_, %slow ]
  %emov = phi float [ %es, %fastzero ], [ %fe_rel, %fastside ], [ %emin, %slow ]
  %vmg = phi float [ %vmb, %fastzero ], [ %vmb, %fastside ], [ 0.0, %slow ]
  %wom0 = fsub float %spr, 12.0
  %wom1 = fmul float %wom0, 0.5
  %w_om_s = call float @sat(float %wom1)
  %wom_es0 = fsub float %es, 0x3FE99999A0000000 ; 0.80
  %wom_es1 = fmul float %wom_es0, 4.0
  %w_om_es = call float @sat(float %wom_es1)
  %wom_em0 = fsub float 0x3FD3D70A40000000, %emov ; 0.31
  %wom_em1 = fmul float %wom_em0, 4.0
  %w_om_em = call float @sat(float %wom_em1)
  %w_om01 = fmul float %w_om_s, %w_om_es
  %w_occ_m = fmul float %w_om01, %w_om_em
  %stat = call <4 x float> @lerp4(<4 x float> %sp, <4 x float> %sc, float %t)
  %l_mov = call float @sum3(<4 x float> %mov)
  %l_stat = call float @sum3(<4 x float> %stat)
  %dl_s = fsub float %l_stat, %l_mov
  ; positive channel contrast for colored HUD/crosshair protection without dark-spot regression
  %d_sm = fsub <4 x float> %stat, %mov
  %d_sm0 = extractelement <4 x float> %d_sm, i32 0
  %d_sm1 = extractelement <4 x float> %d_sm, i32 1
  %d_sm2 = extractelement <4 x float> %d_sm, i32 2
  %mx_c0 = call float @llvm.maxnum.f32(float %d_sm0, float %d_sm1)
  %mx_ch = call float @llvm.maxnum.f32(float %mx_c0, float %d_sm2)
  %mx_ch3 = fmul float %mx_ch, 3.0
  %ch_ex0 = fsub float %mx_ch3, %dl_s
  %ch_ex = fsub float %ch_ex0, 0x3FE3333340000000 ; 0.60
  %wh_ex = fsub float %l_stat, 0x4004CCCCC0000000 ; 2.60
  %pos_c = call float @llvm.maxnum.f32(float %wh_ex, float %ch_ex)
  ; suppress baseline ws0 on moving bright texture where static is darker (prevents dark spots)
  %fl0 = fsub float 0x3F789374C0000000, %es; 0.006
  %fl1 = fmul float %fl0, 0x4074D55560000000; 333.33334
  %ws0_raw = call float @sat(float %fl1)
  %mv_act = fcmp ogt float %vmg, 2.0
  %em_lo = fcmp olt float %emov, 0x3FC6147AE0000000 ; 0.22
  %st_dk = fcmp olt float %dl_s, 0xBFA3D70A40000000 ; -0.04
  %dk_sp0 = and i1 %mv_act, %em_lo
  %dk_spot = and i1 %dk_sp0, %st_dk
  %ws0 = select i1 %dk_spot, float 0.0, float %ws0_raw
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgk, i32 %gbo, i32 0, i32 0)
  %hasfg = icmp uge i32 %gk, 1048576
  ; inline static HUD / crosshair / subtitle stroke protection
  %hv0 = fsub float %vmg, 6.0
  %hv1 = fmul float %hv0, 0.25
  %w_hv = call float @sat(float %hv1)
  %he0 = fsub float 0x3FA99999A0000000, %es ; 0.05
  %he1 = fmul float %he0, 50.0
  %w_he = call float @sat(float %he1)
  %hc1_f = fmul float %pos_c, 16.0
  %w_hc = call float @sat(float %hc1_f)
  %nd0 = fsub float %dl_s, 0x3FA99999A0000000 ; 0.05
  %nd1 = fmul float %nd0, 25.0
  %w_nd = call float @sat(float %nd1)
  %wh01 = fmul float %w_hv, %w_he
  %wh23 = fmul float %w_hc, %w_nd
  %w_hud0 = fmul float %wh01, %wh23
  %nfgk = xor i1 %hasfg, true
  %w_hud = select i1 %nfgk, float %w_hud0, float 0.0
  %ws0_h = call float @llvm.maxnum.f32(float %ws0, float %w_hud)
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %fgkill0 = and i1 %hasfg, %mfit
  %nofgn = xor i1 %nofgk, true
  %fgkill = and i1 %fgkill0, %nofgn
  %ws = select i1 %fgkill, float 0.0, float %ws0_h
  %pcn0 = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn0 = load <4 x i32>, ptr addrspace(4) %pcn0, align 16, !invariant.load !0
  %fl0b = extractelement <4 x i32> %cn0, i32 2
  %nocfb = and i32 %fl0b, 8
  %nocf = icmp ne i32 %nocfb, 0
  %dmp = call float @diff3(<4 x float> %mov, <4 x float> %sp)
  %dmc = call float @diff3(<4 x float> %mov, <4 x float> %sc)
  %dmv = call float @diff3(<4 x float> %mov, <4 x float> %stat)
  %ddm = fsub float %dmp, %dmc
  %k_ddm0 = fmul float %w_occ_m, 0x3FDCCCCCC0000000 ; 0.45 * w_occ_m
  %k_ddm = fadd float %k_ddm0, 0x3FDCCCCCC0000000 ; 0.45 + 0.45 * w_occ_m
  %dt_g = fmul float %ddm, %k_ddm
  %lim_o = fmul float %w_occ_m, 0x3FC99999A0000000 ; 0.20 * w_occ_m
  %lim_hi = fadd float %lim_o, 0x3FD3333340000000 ; 0.30 + 0.20 * w_occ_m
  %lim_lo = fsub float 0.0, %lim_hi
  %dt1 = call float @llvm.maxnum.f32(float %dt_g, float %lim_lo)
  %dtsc = call float @llvm.minnum.f32(float %dt1, float %lim_hi)
  %tsc0 = fadd float %t, %dtsc
  %tsc = call float @sat(float %tsc0)
  %fillc = call <4 x float> @lerp4(<4 x float> %sp, <4 x float> %sc, float %tsc)
  %cw0 = fsub float %emov, 0.5
  %cw1 = fmul float %cw0, 2.5
  %cw2a = call float @sat(float %cw1)
  %cwe0 = fsub float 0x3FE99999A0000000, %es
  %cwe1 = fmul float %cwe0, 4.0
  %cwe = call float @sat(float %cwe1)
  %cw2 = fmul float %cw2a, %cwe
  %bg0 = fcmp ogt float %vmg, 8.0
  %bg1 = fcmp olt float %es, 0.25
  %bg2 = and i1 %bg0, %bg1
  %bg2x = and i1 %bg2, %nfgk
  ; 2.24: static-colour fallback only where the two motion-compensated samples disagree (emov > 0.25)
  %bge = fcmp ogt float %emov, 0.25
  %bg3 = and i1 %bg2x, %bge
  %bgs0a = fsub float %dmv, %es
  %bgs0 = fsub float %bgs0a, 0x3FBF5C2900000000
  %bgs1 = fmul float %bgs0, 4.0
  %bgs = call float @sat(float %bgs1)
  %bgw = select i1 %bg3, float %bgs, float 0.0
  %ds0 = fsub float %dl_s, 0x3FD99999A0000000
  %ds1 = fmul float %ds0, 0x3FF99999A0000000
  %dsa = call float @sat(float %ds1)
  %dse0 = fsub float %emov, 0x3FBEB851E0000000
  %dse1 = fmul float %dse0, 5.0
  %dsb = call float @sat(float %dse1)
  %dss0 = fsub float 0x3FD3333340000000, %es
  %dss1 = fmul float %dss0, 4.0
  %dsc = call float @sat(float %dss1)
  %dsab = fmul float %dsa, %dsb
  %dsw = fmul float %dsab, %dsc
  %cw3a = call float @llvm.maxnum.f32(float %cw2, float %bgw)
  %cw_base = call float @llvm.maxnum.f32(float %cw3a, float %dsw)
  %res_cw = call float @mlp_cw(float %ddm, float %emov, float %es, float %dmv, float %dl_s, float %spr, float %vmg)
  %act0 = fsub float %emov, 0x3F999999A0000000
  %act1 = fmul float %act0, 10.0
  %act = call float @sat(float %act1)
  %dcw = fmul float %act, %res_cw
  %cw4 = fadd float %cw_base, %dcw
  %cw3_0 = call float @sat(float %cw4)
  %gcw0 = fsub float %emov, %es
  %gcw1 = fmul float %gcw0, 4.0
  %gcw = call float @sat(float %gcw1)
  %cw3_1 = fmul float %cw3_0, %gcw
  %cw3 = select i1 %fgkill, float 0.0, float %cw3_1
  %cw = select i1 %nocf, float 0.0, float %cw3
  %movc = call <4 x float> @lerp4(<4 x float> %mov, <4 x float> %fillc, float %cw)
  %out0 = call <4 x float> @lerp4(<4 x float> %movc, <4 x float> %stat, float %ws)
  ; scene change: > 68 % unmatched blocks -> per-pixel fallback only where both motion and static samples disagree
  ; per-title scene-change threshold: consts flags bits 16..31 = blocks / 8 (0 = default 5600 of 8160)
  %pcnc = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnc = load <4 x i32>, ptr addrspace(4) %pcnc, align 16, !invariant.load !0
  %flc = extractelement <4 x i32> %cnc, i32 2
  %cbk = lshr i32 %flc, 16
  %cbk8 = shl i32 %cbk, 3
  %cbz = icmp eq i32 %cbk, 0
  %cthr = select i1 %cbz, i32 5600, i32 %cbk8
  %cutc = icmp ugt i32 %cutn, %cthr
  %emov_hi = fcmp ogt float %emov, 0x3FD6666660000000
  %es_hi = fcmp ogt float %es, 0x3FD3333340000000
  %px_bad = and i1 %emov_hi, %es_hi
  %cut = and i1 %cutc, %px_bad
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
  %xh1080 = fptosi float %u to i32
  %yh1080 = fptosi float %v to i32
  %hc0 = icmp uge i32 %xh1080, %hx0
  %hc1 = icmp ult i32 %xh1080, %hx1
  %hc2 = icmp uge i32 %yh1080, %hy0
  %hc3 = icmp ult i32 %yh1080, %hy1
  %hc01 = and i1 %hc0, %hc1
  %hc23 = and i1 %hc2, %hc3
  %hcx = and i1 %hc01, %hc23
  %cdx = sub i32 %xh1080, %hx0
  %cdy = sub i32 %yh1080, %hy0
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

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" "amdgpu-waves-per-eu"="4,4" }
!0 = !{}
