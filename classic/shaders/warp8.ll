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
                           float %w_occ, float %div_b, float %vmn) alwaysinline {




  %up = call float @llvm.amdgcn.fmad.ftz.f32(float %vx, float %t, float %u)
  %vp = call float @llvm.amdgcn.fmad.ftz.f32(float %vy, float %t, float %v)
  %mz.n1 = fneg float %vx
  %uc = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.n1, float %tc1, float %u)
  %mz.n2 = fneg float %vy
  %vc = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.n2, float %tc1, float %v)
  %ups = fmul float %up, %s
  %vps = fmul float %vp, %s
  %ucs = fmul float %uc, %s
  %vcs = fmul float %vc, %s
  %a = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %ups, float %vps, <8 x i32> %tp, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %b = call <4 x float> @llvm.amdgcn.image.sample.lz.2d.v4f32.f32(i32 15, float %ucs, float %vcs, <8 x i32> %tc, <4 x i32> %sm, i1 false, i32 0, i32 0)
  %dpl = fsub float 0.5, %up
  %dpr = fsub float %up, 1919.5
  %dpt = fsub float 0.5, %vp
  %dpb = fsub float %vp, 1079.5
  %dpx = call float @llvm.maxnum.f32(float %dpl, float %dpr)
  %dpy = call float @llvm.maxnum.f32(float %dpt, float %dpb)
  %dp = call float @llvm.maxnum.f32(float %dpx, float %dpy)
  %oobp0 = fcmp ogt float %dp, 0.0
  %or0 = fcmp oge float %up, %rx0
  %or1 = fcmp olt float %up, %rx1
  %or2 = fcmp oge float %vp, %ry0
  %or3 = fcmp olt float %vp, %ry1
  %ora = and i1 %or0, %or1
  %orb = and i1 %or2, %or3
  %inovl = and i1 %ora, %orb
  %oobp = or i1 %oobp0, %inovl
  %dcl = fsub float 0.5, %uc
  %dcr = fsub float %uc, 1919.5
  %dct = fsub float 0.5, %vc
  %dcb = fsub float %vc, 1079.5
  %dcx = call float @llvm.maxnum.f32(float %dcl, float %dcr)
  %dcy = call float @llvm.maxnum.f32(float %dct, float %dcb)
  %dc = call float @llvm.maxnum.f32(float %dcx, float %dcy)
  %oobc = fcmp ogt float %dc, 0.0
  %nc = xor i1 %oobc, true
  %np = xor i1 %oobp, true
  %onlyp = and i1 %oobp, %nc
  %onlyc = and i1 %oobc, %np
  %one = or i1 %onlyp, %onlyc
  %dp_ov = select i1 %inovl, float 8.0, float %dp
  %d_one = select i1 %onlyp, float %dp_ov, float %dc
  %w_s = fmul float %d_one, 0.125
  %w_sat = call float @sat(float %w_s)
  %w_one = select i1 %one, float %w_sat, float 0.0
  %w_inv = fsub float 1.0, %w_sat
  %w_eff = select i1 %onlyp, float %w_sat, float %w_inv
  %m_one = call <4 x float> @lerp4(<4 x float> %a, <4 x float> %b, float %w_eff)
  %a2 = select i1 %onlyp, <4 x float> %m_one, <4 x float> %a
  %b2 = select i1 %onlyc, <4 x float> %m_one, <4 x float> %b
  %e0 = call float @diff3_k(<4 x float> %a2, <4 x float> %b2)
  %eb_pen = fmul float %w_one, 0x3F947AE140000000 ; 0.02
  %eb = fadd float %e0, %eb_pen
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
  %vm_d = fsub float %vmag, %vmn
  %v_ex = call float @llvm.maxnum.f32(float %vm_d, float 0.0)

  %mz.n3 = fneg float %v_ex
  %dv1 = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.n3, float 0.25, float 1.0)
  %dv_g = call float @sat(float %dv1)
  %eo0 = fsub float %eb, 0x3FE19999A0000000 ; 0.55
  %eo1 = fmul float %eo0, 4.0
  %eo_g = call float @sat(float %eo1)
  %g_occ0 = fmul float %w_occ, %dv_g
  %g_occ = fmul float %g_occ0, %eo_g
  %div_eff = fmul float %div_b, %dv_g
  %sl = call { <4 x float>, float } @side_lerp(<4 x float> %a2, <4 x float> %b2, <4 x float> %sp, <4 x float> %sc, float %eb, float %eac, float %ebp, float %t, float %v_ex, float %g_occ, float %div_eff)
  %m = extractvalue { <4 x float>, float } %sl, 0
  %adadb = extractvalue { <4 x float>, float } %sl, 1
  %rk0 = fmul float %g_occ, 0.75
  %rk = fadd float %rk0, 0x3FB99999A0000000 ; 0.10 + 0.75 * g_occ
  %rel0 = fmul float %adadb, %rk
  %rmax = fmul float %eb, 0x3FEA3D70A0000000 ; 0.82
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




  %up = call float @llvm.amdgcn.fmad.ftz.f32(float %vx, float %t, float %u)
  %vp = call float @llvm.amdgcn.fmad.ftz.f32(float %vy, float %t, float %v)
  %mz.n4 = fneg float %vx
  %uc = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.n4, float %tc1, float %u)
  %mz.n5 = fneg float %vy
  %vc = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.n5, float %tc1, float %v)
  %ups = fmul float %up, %s
  %vps = fmul float %vp, %s
  %ucs = fmul float %uc, %s
  %vcs = fmul float %vc, %s
  %dpl = fsub float 0.5, %up
  %dpr = fsub float %up, 1919.5
  %dpt = fsub float 0.5, %vp
  %dpb = fsub float %vp, 1079.5
  %dpx = call float @llvm.maxnum.f32(float %dpl, float %dpr)
  %dpy = call float @llvm.maxnum.f32(float %dpt, float %dpb)
  %dp = call float @llvm.maxnum.f32(float %dpx, float %dpy)
  %oobp0 = fcmp ogt float %dp, 0.0
  %or0 = fcmp oge float %up, %rx0
  %or1 = fcmp olt float %up, %rx1
  %or2 = fcmp oge float %vp, %ry0
  %or3 = fcmp olt float %vp, %ry1
  %ora = and i1 %or0, %or1
  %orb = and i1 %or2, %or3
  %inovl = and i1 %ora, %orb
  %oobp = or i1 %oobp0, %inovl
  %dcl = fsub float 0.5, %uc
  %dcr = fsub float %uc, 1919.5
  %dct = fsub float 0.5, %vc
  %dcb = fsub float %vc, 1079.5
  %dcx = call float @llvm.maxnum.f32(float %dcl, float %dcr)
  %dcy = call float @llvm.maxnum.f32(float %dct, float %dcb)
  %dc = call float @llvm.maxnum.f32(float %dcx, float %dcy)
  %oobc = fcmp ogt float %dc, 0.0
  %any_oob = or i1 %oobp, %oobc
  %csh_b = select i1 %any_oob, float 0.0, float %csh
  %a = call <4 x float> @cr(<8 x i32> %tp, <4 x i32> %sm, float %ups, float %vps, float %csh_b)
  %b = call <4 x float> @cr(<8 x i32> %tc, <4 x i32> %sm, float %ucs, float %vcs, float %csh_b)
  %nc = xor i1 %oobc, true
  %np = xor i1 %oobp, true
  %onlyp = and i1 %oobp, %nc
  %onlyc = and i1 %oobc, %np
  %one = or i1 %onlyp, %onlyc
  %dp_ov = select i1 %inovl, float 8.0, float %dp
  %d_one = select i1 %onlyp, float %dp_ov, float %dc
  %w_s = fmul float %d_one, 0.125
  %w_sat = call float @sat(float %w_s)
  %w_one = select i1 %one, float %w_sat, float 0.0
  %w_inv = fsub float 1.0, %w_sat
  %w_eff = select i1 %onlyp, float %w_sat, float %w_inv
  %m_one = call <4 x float> @lerp4(<4 x float> %a, <4 x float> %b, float %w_eff)
  %a2 = select i1 %onlyp, <4 x float> %m_one, <4 x float> %a
  %b2 = select i1 %onlyc, <4 x float> %m_one, <4 x float> %b
  %e0_r = call float @diff3_k(<4 x float> %a2, <4 x float> %b2)
  %hla0 = extractelement <4 x float> %a, i32 3
  %hlb0 = extractelement <4 x float> %b, i32 3
  %hla = select i1 %onlyp, float %hlb0, float %hla0
  %hlb = select i1 %onlyc, float %hla0, float %hlb0
  %hl_mn = call float @llvm.minnum.f32(float %hla, float %hlb)
  %both_w = fcmp oge float %hl_mn, 4.0
  %aw = fcmp oge float %hla, 4.0
  %bw = fcmp oge float %hlb, 4.0
  %hla_s = fsub float %hla, 4.0
  %hlb_s = fsub float %hlb, 4.0
  %hla_c = select i1 %aw, float %hla_s, float %hla
  %hlb_c = select i1 %bw, float %hlb_s, float %hlb
  %hl_mx = call float @llvm.maxnum.f32(float %hla_c, float %hlb_c)

  %hl_d = call float @llvm.amdgcn.fmad.ftz.f32(float %hl_mx, float 4.0, float 1.0)
  %e0 = fdiv arcp afn float %e0_r, %hl_d
  %e_pen = fmul float %w_one, 0x3F947AE140000000 ; 0.02
  %e = fadd float %e0, %e_pen
  %hl_hi = fadd float %hl_mx, 4.0
  %hl_tag = select i1 %both_w, float %hl_hi, float %hl_mx
  %a3 = insertelement <4 x float> %a2, float %hl_tag, i32 3
  %b3 = insertelement <4 x float> %b2, float %hl_tag, i32 3
  %r0 = insertvalue { <4 x float>, <4 x float>, float } undef, <4 x float> %a3, 0
  %r1 = insertvalue { <4 x float>, <4 x float>, float } %r0, <4 x float> %b3, 1
  %r2 = insertvalue { <4 x float>, <4 x float>, float } %r1, float %e, 2
  ret { <4 x float>, <4 x float>, float } %r2
}

define internal <4 x float> @crw(float %f, float %c) alwaysinline {
  %f2 = fmul float %f, %f
  %f3 = fmul float %f2, %f

  %mz.n6 = fneg float %f
  %a1 = call float @llvm.amdgcn.fmad.ftz.f32(float %f2, float 2.0, float %mz.n6)
  %a2 = fsub float %a1, %f3
  %w0 = fmul float %a2, %c
  %c3 = fsub float %c, 3.0
  %c2 = fsub float 2.0, %c

  %b2 = fmul float %f3, %c2
  %b3 = call float @llvm.amdgcn.fmad.ftz.f32(float %f2, float %c3, float %b2)
  %w1 = fadd float %b3, 1.0


  %mz.n7 = fneg float %c
  %d2c = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.n7, float 2.0, float 3.0)
  %d2 = fmul float %f2, %d2c
  %d3c = fsub float %c, 2.0

  %d12 = call float @llvm.amdgcn.fmad.ftz.f32(float %f, float %c, float %d2)
  %w2 = call float @llvm.amdgcn.fmad.ftz.f32(float %f3, float %d3c, float %d12)
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
  %s0z = insertelement <4 x float> %s0, float 0.0, i32 3
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
  %mn12 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %s1, <4 x float> %s2)
  %mn34 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %s3, <4 x float> %s4)
  %mn_out = call <4 x float> @llvm.minnum.v4f32(<4 x float> %mn12, <4 x float> %mn34)
  %mn_all = call <4 x float> @llvm.minnum.v4f32(<4 x float> %mn_out, <4 x float> %s0)
  %mx12 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %s1, <4 x float> %s2)
  %mx34 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %s3, <4 x float> %s4)
  %mx_out = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %mx12, <4 x float> %mx34)
  %mx5 = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %mx_out, <4 x float> %s0)
  %lo = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %res, <4 x float> %mn_all)
  %hi1 = call <4 x float> @llvm.minnum.v4f32(<4 x float> %lo, <4 x float> %mx5)
  %l_mx5 = call float @sum3(<4 x float> %mx5)
  %l_mn5 = call float @sum3(<4 x float> %mn_all)
  %l_s0 = call float @sum3(<4 x float> %s0)
  %rng_cr = fsub float %l_mx5, %l_mn5
  %br_ex0 = fsub float %l_mx5, 1.5
  %br_ex1 = fmul float %br_ex0, 2.0
  %br_g = call float @sat(float %br_ex1)
  %hl_rng0 = fmul float %rng_cr, %br_g
  %sky_ok = fcmp ogt float %l_mx5, 0x3FFCCCCCC0000000 ; 1.80
  %dip_v = fsub float %l_mx5, %l_s0
  %dip_ok = fcmp ogt float %dip_v, 0x3FC3333340000000 ; 0.15
  %is_wt = and i1 %sky_ok, %dip_ok
  %hl_rng_w = fadd float %hl_rng0, 4.0
  %hl_rng = select i1 %is_wt, float %hl_rng_w, float %hl_rng0
  %hi = insertelement <4 x float> %hi1, float %hl_rng, i32 3
  br label %crdone
crdone:
  %out = phi <4 x float> [ %s0z, %entry ], [ %hi, %cr5 ]
  ret <4 x float> %out
}

define internal { float, float, i1 } @sub_corner(<2 x float> %r00, <2 x float> %r10, <2 x float> %r01, <2 x float> %r11, i32 %qx, i32 %qy) alwaysinline {
  %ux1 = icmp sge i32 %qx, 2
  %uy1 = icmp sge i32 %qy, 2
  %rt = select i1 %ux1, <2 x float> %r10, <2 x float> %r00
  %rb = select i1 %ux1, <2 x float> %r11, <2 x float> %r01
  %raw = select i1 %uy1, <2 x float> %rb, <2 x float> %rt
  %rx = extractelement <2 x float> %raw, i32 0
  %ry = extractelement <2 x float> %raw, i32 1
  %bxi = bitcast float %rx to i32
  %byi = bitcast float %ry to i32
  %m = and i32 %bxi, 15
  %has_m = icmp ne i32 %m, 0
  %qx1 = and i32 %qx, 1
  %qy1 = and i32 %qy, 1
  %qy2 = shl i32 %qy1, 1
  %qbit = or i32 %qx1, %qy2
  %msh = lshr i32 %m, %qbit
  %mb = and i32 %msh, 1
  %u2 = icmp ne i32 %mb, 0
  %sx0 = shl i32 %bxi, 20
  %sx1 = ashr i32 %sx0, 24
  %sx2 = shl i32 %sx1, 1
  %sy0 = shl i32 %byi, 24
  %sy1 = ashr i32 %sy0, 24
  %sy2 = shl i32 %sy1, 1
  %v2x = sitofp i32 %sx2 to float
  %v2y = sitofp i32 %sy2 to float
  %b1x = and i32 %bxi, -4096
  %b1y = and i32 %byi, -256
  %v1x0 = bitcast i32 %b1x to float
  %v1y0 = bitcast i32 %b1y to float
  %ax1 = call float @llvm.fabs.f32(float %v1x0)
  %ay1 = call float @llvm.fabs.f32(float %v1y0)
  %zx1 = fcmp ole float %ax1, 0.0625
  %zy1 = fcmp ole float %ay1, 0.0625
  %v1x1 = select i1 %zx1, float 0.0, float %v1x0
  %v1y1 = select i1 %zy1, float 0.0, float %v1y0
  %v1x = select i1 %has_m, float %v1x1, float %rx
  %v1y = select i1 %has_m, float %v1y1, float %ry
  %vx = select i1 %u2, float %v2x, float %v1x
  %vy = select i1 %u2, float %v2y, float %v1y
  %o0 = insertvalue { float, float, i1 } undef, float %vx, 0
  %o1 = insertvalue { float, float, i1 } %o0, float %vy, 1
  %o2 = insertvalue { float, float, i1 } %o1, i1 %u2, 2
  ret { float, float, i1 } %o2
}

; slow-path mix of the 4 corner candidates: weight (w_eff + 0.08) * exp2(8 * (emin - e)). A clean foreground sub-block
; (e < 0.18) gets w * 4 + 0.16; its error e is no longer lowered by 0.08, which pulled the mix and emov towards the
; foreground at motion boundaries (round 2 of the CUSA18723 image work)
define internal { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st,
        { <4 x float>, float } %c, float %w) alwaysinline {
  %acc = extractvalue { <4 x float>, float, float } %st, 0
  %sw = extractvalue { <4 x float>, float, float } %st, 1
  %em = extractvalue { <4 x float>, float, float } %st, 2
  %m = extractvalue { <4 x float>, float } %c, 0
  %e0 = extractvalue { <4 x float>, float } %c, 1
  %is_fg_sub = fcmp oge float %w, 1.5
  %w_sub = fsub float %w, 2.0
  %w_raw = select i1 %is_fg_sub, float %w_sub, float %w
  %e_clean = fcmp olt float %e0, 0x3FC70A3D80000000 ; 0.18
  %fg_bst = and i1 %is_fg_sub, %e_clean
  %w_b0 = fmul float %w_raw, 4.0
  %w_b1 = fadd float %w_b0, 0x3FC47AE140000000 ; 0.16
  %w_eff = select i1 %fg_bst, float %w_b1, float %w_raw
  %e = fadd float %e0, -0.0 ; the candidate's own error, also for a boosted foreground sub-block
  %em1 = call float @llvm.minnum.f32(float %em, float %e)
  ; exp2(8 (em1 - em)) and exp2(8 (em1 - e)): one of them is exp2(0) = 1 and the other equals exp2(-8 |em - e|) bit
  ; for bit (fl(a - b) == -fl(b - a), 8 a power of two), so one quarter-rate v_exp_f32 per candidate instead of two
  %d0 = fsub float %em, %e
  %ad0 = call float @llvm.fabs.f32(float %d0)
  %k0 = fmul float %ad0, -8.0
  %z = call float @llvm.exp2.f32(float %k0)
  %lo = fcmp olt float %e, %em
  %sc = select i1 %lo, float %z, float 1.0
  %x1 = select i1 %lo, float 1.0, float %z
  %w5 = fadd float %w_eff, 0x3FB47AE140000000; 0.08
  %om = fmul float %w5, %x1
  %acs = call <4 x float> @scale4(<4 x float> %acc, float %sc)
  %mo = call <4 x float> @scale4(<4 x float> %m, float %om)
  %acc1 = fadd <4 x float> %acs, %mo

  %sw1 = call float @llvm.amdgcn.fmad.ftz.f32(float %sw, float %sc, float %om)
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

define internal float @diff3_k(<4 x float> %a, <4 x float> %b) alwaysinline {
  %mx = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %a, <4 x float> %b)
  %mn = call <4 x float> @llvm.minnum.v4f32(<4 x float> %a, <4 x float> %b)
  %l_hi = call float @sum3(<4 x float> %mx)
  %l_lo = call float @sum3(<4 x float> %mn)
  %e_raw = fsub float %l_hi, %l_lo
  %ex_hi0 = fsub float %l_hi, 0x3FFF333340000000 ; 1.95
  %ex_hi = call float @llvm.maxnum.f32(float %ex_hi0, float 0.0)
  %ex_lo0 = fsub float %l_lo, 0x3FECCCCCC0000000 ; 0.90
  %ex_lo = call float @llvm.maxnum.f32(float %ex_lo0, float 0.0)

  %k_lo = fmul float %ex_lo, 0.75
  %k_sum = call float @llvm.amdgcn.fmad.ftz.f32(float %ex_hi, float 1.25, float %k_lo)
  %den = fadd float 1.0, %k_sum
  %e = fdiv arcp afn float %e_raw, %den
  ret float %e
}

define internal <4 x float> @lerp4(<4 x float> %a, <4 x float> %b, float %t) alwaysinline {
  %t0 = insertelement <4 x float> undef, float %t, i32 0
  %tv = shufflevector <4 x float> %t0, <4 x float> undef, <4 x i32> zeroinitializer
  %d = fsub <4 x float> %b, %a

  %mz.v8a = extractelement <4 x float> %d, i32 0
  %mz.v8b = extractelement <4 x float> %tv, i32 0
  %mz.v8c = extractelement <4 x float> %a, i32 0
  %mz.v8r = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.v8a, float %mz.v8b, float %mz.v8c)
  %mz.v8i = insertelement <4 x float> poison, float %mz.v8r, i32 0
  %mz.v9a = extractelement <4 x float> %d, i32 1
  %mz.v9b = extractelement <4 x float> %tv, i32 1
  %mz.v9c = extractelement <4 x float> %a, i32 1
  %mz.v9r = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.v9a, float %mz.v9b, float %mz.v9c)
  %mz.v9i = insertelement <4 x float> %mz.v8i, float %mz.v9r, i32 1
  %mz.v10a = extractelement <4 x float> %d, i32 2
  %mz.v10b = extractelement <4 x float> %tv, i32 2
  %mz.v10c = extractelement <4 x float> %a, i32 2
  %mz.v10r = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.v10a, float %mz.v10b, float %mz.v10c)
  %mz.v10i = insertelement <4 x float> %mz.v9i, float %mz.v10r, i32 2
  %mz.v11a = extractelement <4 x float> %d, i32 3
  %mz.v11b = extractelement <4 x float> %tv, i32 3
  %mz.v11c = extractelement <4 x float> %a, i32 3
  %mz.v11r = call float @llvm.amdgcn.fmad.ftz.f32(float %mz.v11a, float %mz.v11b, float %mz.v11c)
  %r = insertelement <4 x float> %mz.v10i, float %mz.v11r, i32 3
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
  %ad_div1 = fmul float %ad_div0, 3.0
  %ad_div = fmul float %ad_div1, %w_occ
  %adadb = call float @llvm.maxnum.f32(float %ad_min, float %ad_div)
  %em0 = fsub float %e0, 0x3F947AE140000000
  %em1 = fmul float %em0, 6.0
  %g_em = call float @sat(float %em1)

  %t1 = fmul float %d_cr, 0x3FB1EB8520000000

  %t3 = fmul float %d_lu, 0x3FA47AE140000000
  %s01 = call float @llvm.amdgcn.fmad.ftz.f32(float %d_min, float 0x3FD51EB860000000, float %t1)
  %s23 = call float @llvm.amdgcn.fmad.ftz.f32(float %d_sf, float 0x3FC1EB8520000000, float %t3)
  %raw0 = fadd float %s01, %s23

  %o_sum = call float @llvm.amdgcn.fmad.ftz.f32(float %s01, float 1.625, float %div_b)

  %raw = call float @llvm.amdgcn.fmad.ftz.f32(float %w_occ, float %o_sum, float %raw0)
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

  %s0_0 = call float @llvm.amdgcn.fmad.ftz.f32(float %x0, float 0x3FC3F44680000000, float 0x3FAA7194A0000000)

  %s0_1 = call float @llvm.amdgcn.fmad.ftz.f32(float %x1, float 0xBFB065BB20000000, float %s0_0)

  %s0_2 = call float @llvm.amdgcn.fmad.ftz.f32(float %x2, float 0x3FCC696560000000, float %s0_1)

  %s0_3 = call float @llvm.amdgcn.fmad.ftz.f32(float %x3, float 0xBFCD760280000000, float %s0_2)

  %s0_4 = call float @llvm.amdgcn.fmad.ftz.f32(float %x4, float 0x3FCA876A80000000, float %s0_3)

  %s0_5 = call float @llvm.amdgcn.fmad.ftz.f32(float %x5, float 0x3F91AA4D20000000, float %s0_4)

  %s0_6 = call float @llvm.amdgcn.fmad.ftz.f32(float %x6, float 0x3F9C0450A0000000, float %s0_5)
  %h0 = call float @sat(float %s0_6)

  %acc1 = call float @llvm.amdgcn.fmad.ftz.f32(float %h0, float 0x3FC5607260000000, float %acc0)

  %s1_0 = call float @llvm.amdgcn.fmad.ftz.f32(float %x0, float 0x3FB7DEC400000000, float 0x3FB502A940000000)

  %s1_1 = call float @llvm.amdgcn.fmad.ftz.f32(float %x1, float 0xBFCE7815E0000000, float %s1_0)

  %s1_2 = call float @llvm.amdgcn.fmad.ftz.f32(float %x2, float 0x3FB3174D20000000, float %s1_1)

  %s1_3 = call float @llvm.amdgcn.fmad.ftz.f32(float %x3, float 0x3FE3445960000000, float %s1_2)

  %s1_4 = call float @llvm.amdgcn.fmad.ftz.f32(float %x4, float 0x3F8D88B180000000, float %s1_3)

  %s1_5 = call float @llvm.amdgcn.fmad.ftz.f32(float %x5, float 0xBFA1481FA0000000, float %s1_4)

  %s1_6 = call float @llvm.amdgcn.fmad.ftz.f32(float %x6, float 0x3F9858A580000000, float %s1_5)
  %h1 = call float @sat(float %s1_6)

  %acc2 = call float @llvm.amdgcn.fmad.ftz.f32(float %h1, float 0xBFBEEB9EA0000000, float %acc1)

  %s2_0 = call float @llvm.amdgcn.fmad.ftz.f32(float %x0, float 0x3FA57A3F20000000, float 0x3FB1FFCAE0000000)

  %s2_1 = call float @llvm.amdgcn.fmad.ftz.f32(float %x1, float 0xBFBD902FC0000000, float %s2_0)

  %s2_2 = call float @llvm.amdgcn.fmad.ftz.f32(float %x2, float 0x3FBC89F040000000, float %s2_1)

  %s2_3 = call float @llvm.amdgcn.fmad.ftz.f32(float %x3, float 0x3FE3C65A20000000, float %s2_2)

  %s2_4 = call float @llvm.amdgcn.fmad.ftz.f32(float %x4, float 0xBFB5D25000000000, float %s2_3)

  %s2_5 = call float @llvm.amdgcn.fmad.ftz.f32(float %x5, float 0xBF8994F1E0000000, float %s2_4)

  %s2_6 = call float @llvm.amdgcn.fmad.ftz.f32(float %x6, float 0xBF949280A0000000, float %s2_5)
  %h2 = call float @sat(float %s2_6)

  %acc3 = call float @llvm.amdgcn.fmad.ftz.f32(float %h2, float 0xBFC311BD60000000, float %acc2)

  %s3_0 = call float @llvm.amdgcn.fmad.ftz.f32(float %x0, float 0xBFB118CFE0000000, float 0x3FB5F7C1A0000000)

  %s3_1 = call float @llvm.amdgcn.fmad.ftz.f32(float %x1, float 0xBFC6C64040000000, float %s3_0)

  %s3_2 = call float @llvm.amdgcn.fmad.ftz.f32(float %x2, float 0xBF75439B40000000, float %s3_1)

  %s3_3 = call float @llvm.amdgcn.fmad.ftz.f32(float %x3, float 0x3FE5C938E0000000, float %s3_2)

  %s3_4 = call float @llvm.amdgcn.fmad.ftz.f32(float %x4, float 0xBFBD267DC0000000, float %s3_3)

  %s3_5 = call float @llvm.amdgcn.fmad.ftz.f32(float %x5, float 0xBF8AD32C60000000, float %s3_4)

  %s3_6 = call float @llvm.amdgcn.fmad.ftz.f32(float %x6, float 0xBF957503E0000000, float %s3_5)
  %h3 = call float @sat(float %s3_6)

  %acc4 = call float @llvm.amdgcn.fmad.ftz.f32(float %h3, float 0xBFC9E87220000000, float %acc3)

  %s4_0 = call float @llvm.amdgcn.fmad.ftz.f32(float %x0, float 0x3FB8A754A0000000, float 0x3FA75E5680000000)

  %s4_1 = call float @llvm.amdgcn.fmad.ftz.f32(float %x1, float 0xBFD45CA740000000, float %s4_0)

  %s4_2 = call float @llvm.amdgcn.fmad.ftz.f32(float %x2, float 0xBFC288DB80000000, float %s4_1)

  %s4_3 = call float @llvm.amdgcn.fmad.ftz.f32(float %x3, float 0x3FE207A9E0000000, float %s4_2)

  %s4_4 = call float @llvm.amdgcn.fmad.ftz.f32(float %x4, float 0x3FBB9C1E00000000, float %s4_3)

  %s4_5 = call float @llvm.amdgcn.fmad.ftz.f32(float %x5, float 0xBF973F9A40000000, float %s4_4)

  %s4_6 = call float @llvm.amdgcn.fmad.ftz.f32(float %x6, float 0x3F725395C0000000, float %s4_5)
  %h4 = call float @sat(float %s4_6)

  %acc5 = call float @llvm.amdgcn.fmad.ftz.f32(float %h4, float 0xBFB4C43480000000, float %acc4)

  %s5_0 = call float @llvm.amdgcn.fmad.ftz.f32(float %x0, float 0x3FC2543F20000000, float 0x3FB94845C0000000)

  %s5_1 = call float @llvm.amdgcn.fmad.ftz.f32(float %x1, float 0xBFC28ECE00000000, float %s5_0)

  %s5_2 = call float @llvm.amdgcn.fmad.ftz.f32(float %x2, float 0x3FC7A203E0000000, float %s5_1)

  %s5_3 = call float @llvm.amdgcn.fmad.ftz.f32(float %x3, float 0x3F9AF58FC0000000, float %s5_2)

  %s5_4 = call float @llvm.amdgcn.fmad.ftz.f32(float %x4, float 0x3FB81CA580000000, float %s5_3)

  %s5_5 = call float @llvm.amdgcn.fmad.ftz.f32(float %x5, float 0x3F88B067A0000000, float %s5_4)

  %s5_6 = call float @llvm.amdgcn.fmad.ftz.f32(float %x6, float 0x3F8FB664A0000000, float %s5_5)
  %h5 = call float @sat(float %s5_6)

  %acc6 = call float @llvm.amdgcn.fmad.ftz.f32(float %h5, float 0x3FC2CAC0E0000000, float %acc5)
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




  %mz.n12 = fneg float 3.0
  %oxm2 = call float @llvm.amdgcn.fmad.ftz.f32(float %oxf0, float %inv_s, float %mz.n12)
  %mz.n13 = fneg float 3.0
  %oym2 = call float @llvm.amdgcn.fmad.ftz.f32(float %oyf0, float %inv_s, float %mz.n13)
  %oxp2 = call float @llvm.amdgcn.fmad.ftz.f32(float %oxf1, float %inv_s, float 3.0)
  %oyp2 = call float @llvm.amdgcn.fmad.ftz.f32(float %oyf1, float %inv_s, float 3.0)
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
  ; fractional phase advection on the t=0.5 mid-grid F + second-order quadratic trajectory offset
  %fc0 = call <2 x float> @ldF(<4 x i32> %vf, i32 %cbx, i32 %cby)
  %fcx = extractelement <2 x float> %fc0, i32 0
  %fcy = extractelement <2 x float> %fc0, i32 1
  %pfp = getelementptr i8, ptr addrspace(4) %tbl, i64 272
  %vfp = load <4 x i32>, ptr addrspace(4) %pfp, align 16, !invariant.load !0
  %ux_p = fadd float %u, %fcx
  %uy_p = fadd float %v, %fcy
  %ubx_p0 = fmul float %ux_p, 0.0625
  %uby_p0 = fmul float %uy_p, 0.0625
  %upx0 = fptosi float %ubx_p0 to i32
  %upy0 = fptosi float %uby_p0 to i32
  %upx1 = call i32 @llvm.smax.i32(i32 %upx0, i32 0)
  %up_bx = call i32 @llvm.smin.i32(i32 %upx1, i32 119)
  %upy1 = call i32 @llvm.smax.i32(i32 %upy0, i32 0)
  %up_by = call i32 @llvm.smin.i32(i32 %upy1, i32 67)
  %fp0 = call <2 x float> @ldF(<4 x i32> %vfp, i32 %up_bx, i32 %up_by)
  %fpx = extractelement <2 x float> %fp0, i32 0
  %fpy = extractelement <2 x float> %fp0, i32 1
  %ax_q = fsub float %fcx, %fpx
  %ay_q = fsub float %fcy, %fpy

  %dot_y = fmul float %fcy, %fpy
  %dot_v = call float @llvm.amdgcn.fmad.ftz.f32(float %fcx, float %fpx, float %dot_y)
  %afcx = call float @llvm.fabs.f32(float %fcx)
  %afcy = call float @llvm.fabs.f32(float %fcy)
  %vm_c = fadd float %afcx, %afcy
  %afpx = call float @llvm.fabs.f32(float %fpx)
  %afpy = call float @llvm.fabs.f32(float %fpy)
  %vm_p = fadd float %afpx, %afpy
  %aax = call float @llvm.fabs.f32(float %ax_q)
  %aay = call float @llvm.fabs.f32(float %ay_q)
  %am_q = fadd float %aax, %aay
  %vmin_cp = call float @llvm.minnum.f32(float %vm_c, float %vm_p)
  %alim = fmul float %vmin_cp, 1.25
  %q_dot = fcmp ogt float %dot_v, 0.0
  %q_min = fcmp ogt float %vmin_cp, 2.0
  %q_acc = fcmp ole float %am_q, %alim
  %q_ok0 = and i1 %q_dot, %q_min
  %q_ok = and i1 %q_ok0, %q_acc
  %tt1 = fmul float %t, %tc1
  %q_s = fmul float %tt1, -0.5
  %dqx0 = fmul float %ax_q, %q_s
  %dqy0 = fmul float %ay_q, %q_s
  %dqx1 = call float @llvm.maxnum.f32(float %dqx0, float -4.0)
  %dqx2 = call float @llvm.minnum.f32(float %dqx1, float 4.0)
  %dqy1 = call float @llvm.maxnum.f32(float %dqy0, float -4.0)
  %dqy2 = call float @llvm.minnum.f32(float %dqy1, float 4.0)
  %dqx = select i1 %q_ok, float %dqx2, float 0.0
  %dqy = select i1 %q_ok, float %dqy2, float 0.0
  %dt05 = fsub float %t, 0.5
  %shx0 = fmul float %dt05, %fcx
  %shy0 = fmul float %dt05, %fcy
  %shx1 = call float @llvm.maxnum.f32(float %shx0, float -16.0)
  %shx = call float @llvm.minnum.f32(float %shx1, float 16.0)
  %shy1 = call float @llvm.maxnum.f32(float %shy0, float -16.0)
  %shy = call float @llvm.minnum.f32(float %shy1, float 16.0)
  %xfs0 = fadd float %xf1080, %shx
  %yfs0 = fadd float %yf1080, %shy
  %xfs = fadd float %xfs0, 0.0
  %yfs = fadd float %yfs0, 0.0
  %u_q = fadd float %u, 0.0
  %v_q = fadd float %v, 0.0
  ; ---- 4 nearest block centres (blocks of 16 px in 1080p space, centres at 16 i + 8)

  %mz.n14 = fneg float 0.46875
  %fbx = call float @llvm.amdgcn.fmad.ftz.f32(float %xfs, float 0.0625, float %mz.n14)

  %mz.n15 = fneg float 0.46875
  %fby = call float @llvm.amdgcn.fmad.ftz.f32(float %yfs, float 0.0625, float %mz.n15)
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
  %rx00 = extractelement <2 x float> %f00, i32 0
  %ry00 = extractelement <2 x float> %f00, i32 1
  %rx10 = extractelement <2 x float> %f10, i32 0
  %ry10 = extractelement <2 x float> %f10, i32 1
  %rx01 = extractelement <2 x float> %f01, i32 0
  %ry01 = extractelement <2 x float> %f01, i32 1
  %rx11 = extractelement <2 x float> %f11, i32 0
  %ry11 = extractelement <2 x float> %f11, i32 1
  %bi00 = bitcast float %rx00 to i32
  %bi10 = bitcast float %rx10 to i32
  %bi01 = bitcast float %rx01 to i32
  %bi11 = bitcast float %rx11 to i32
  %bo0 = or i32 %bi00, %bi10
  %bo1 = or i32 %bi01, %bi11
  %bo = or i32 %bo0, %bo1
  %bm = and i32 %bo, 15
  %any_split = icmp ne i32 %bm, 0

  %fx8 = call float @llvm.amdgcn.fmad.ftz.f32(float %wx, float 2.0, float 0.5)

  %fy8 = call float @llvm.amdgcn.fmad.ftz.f32(float %wy, float 2.0, float 0.5)
  %flx8 = call float @llvm.floor.f32(float %fx8)
  %fly8 = call float @llvm.floor.f32(float %fy8)
  %wx8 = fsub float %fx8, %flx8
  %wy8 = fsub float %fy8, %fly8
  %sqx0 = fptosi float %flx8 to i32
  %sqy0 = fptosi float %fly8 to i32
  %sqx1 = add i32 %sqx0, 1
  %sqy1 = add i32 %sqy0, 1
  %sc00 = call { float, float, i1 } @sub_corner(<2 x float> %f00, <2 x float> %f10, <2 x float> %f01, <2 x float> %f11, i32 %sqx0, i32 %sqy0)
  %sc10 = call { float, float, i1 } @sub_corner(<2 x float> %f00, <2 x float> %f10, <2 x float> %f01, <2 x float> %f11, i32 %sqx1, i32 %sqy0)
  %sc01 = call { float, float, i1 } @sub_corner(<2 x float> %f00, <2 x float> %f10, <2 x float> %f01, <2 x float> %f11, i32 %sqx0, i32 %sqy1)
  %sc11 = call { float, float, i1 } @sub_corner(<2 x float> %f00, <2 x float> %f10, <2 x float> %f01, <2 x float> %f11, i32 %sqx1, i32 %sqy1)
  %sx00 = extractvalue { float, float, i1 } %sc00, 0
  %sy00 = extractvalue { float, float, i1 } %sc00, 1
  %u00 = extractvalue { float, float, i1 } %sc00, 2
  %sx10 = extractvalue { float, float, i1 } %sc10, 0
  %sy10 = extractvalue { float, float, i1 } %sc10, 1
  %u10 = extractvalue { float, float, i1 } %sc10, 2
  %sx01 = extractvalue { float, float, i1 } %sc01, 0
  %sy01 = extractvalue { float, float, i1 } %sc01, 1
  %u01 = extractvalue { float, float, i1 } %sc01, 2
  %sx11 = extractvalue { float, float, i1 } %sc11, 0
  %sy11 = extractvalue { float, float, i1 } %sc11, 1
  %u11 = extractvalue { float, float, i1 } %sc11, 2
  %x00 = select i1 %any_split, float %sx00, float %rx00
  %y00 = select i1 %any_split, float %sy00, float %ry00
  %x10 = select i1 %any_split, float %sx10, float %rx10
  %y10 = select i1 %any_split, float %sy10, float %ry10
  %x01 = select i1 %any_split, float %sx01, float %rx01
  %y01 = select i1 %any_split, float %sy01, float %ry01
  %x11 = select i1 %any_split, float %sx11, float %rx11
  %y11 = select i1 %any_split, float %sy11, float %ry11
  %wx_e = select i1 %any_split, float %wx8, float %wx
  %wy_e = select i1 %any_split, float %wy8, float %wy
  %iwx = fsub float 1.0, %wx_e
  %iwy = fsub float 1.0, %wy_e
  %w00_0 = fmul float %iwx, %iwy
  %w10_0 = fmul float %wx_e, %iwy
  %w01_0 = fmul float %iwx, %wy_e
  %w11_0 = fmul float %wx_e, %wy_e
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
  %ncoh = xor i1 %coh, true
  %fg_en = and i1 %ncoh, %any_split
  %fg00 = and i1 %fg_en, %u00
  %fg10 = and i1 %fg_en, %u10
  %fg01 = and i1 %fg_en, %u01
  %fg11 = and i1 %fg_en, %u11
  %w00_p = fadd float %w00_0, 2.0
  %w10_p = fadd float %w10_0, 2.0
  %w01_p = fadd float %w01_0, 2.0
  %w11_p = fadd float %w11_0, 2.0
  %w00 = select i1 %fg00, float %w00_p, float %w00_0
  %w10 = select i1 %fg10, float %w10_p, float %w10_0
  %w01 = select i1 %fg01, float %w01_p, float %w01_0
  %w11 = select i1 %fg11, float %w11_p, float %w11_0
  ; static candidate: exact point loads
  %sp = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tp, i32 0, i32 0)
  %sc = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tc, i32 0, i32 0)
  %es = call float @diff3_k(<4 x float> %sp, <4 x float> %sc)
  br i1 %coh, label %fast, label %slow

fast:

  %a1 = fmul float %x10, %w10

  %a3 = fmul float %x11, %w11
  %a01 = call float @llvm.amdgcn.fmad.ftz.f32(float %x00, float %w00, float %a1)
  %a23 = call float @llvm.amdgcn.fmad.ftz.f32(float %x01, float %w01, float %a3)
  %vbx = fadd float %a01, %a23

  %b1 = fmul float %y10, %w10

  %b3 = fmul float %y11, %w11
  %b01 = call float @llvm.amdgcn.fmad.ftz.f32(float %y00, float %w00, float %b1)
  %b23 = call float @llvm.amdgcn.fmad.ftz.f32(float %y01, float %w01, float %b3)
  %vby = fadd float %b01, %b23
  %vabx = call float @llvm.fabs.f32(float %vbx)
  %vaby = call float @llvm.fabs.f32(float %vby)
  %vmb = fadd float %vabx, %vaby
  %zx = fcmp oeq float %vbx, 0.0
  %zy = fcmp oeq float %vby, 0.0
  %zv = and i1 %zx, %zy
  br i1 %zv, label %fastzero, label %fastmove
fastzero:
  %zm0 = call <4 x float> @lerp4(<4 x float> %sp, <4 x float> %sc, float %t)
  %zm = insertelement <4 x float> %zm0, float 0.0, i32 3
  br label %merge
fastmove:
  %vm0 = fsub float %vmb, 12.0
  %vm1 = fmul float %vm0, 0x3FC1EB8520000000
  %vms = call float @sat(float %vm1)
  %csc = fsub float 1.0, %vms
  %csh_e = fmul float %csh, %csc
  %cf = call { <4 x float>, <4 x float>, float } @tapsq(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u_q, float %v_q, float %vbx, float %vby, float %t, float %tc1, float %s, float %csh_e, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s)
  %fa = extractvalue { <4 x float>, <4 x float>, float } %cf, 0
  %fb = extractvalue { <4 x float>, <4 x float>, float } %cf, 1
  %fe = extractvalue { <4 x float>, <4 x float>, float } %cf, 2
  %c_split = fcmp oge float %fe, -1.0
  br i1 %c_split, label %fastside, label %fastzero
fastside:
  %eac_f = call float @diff3_k(<4 x float> %fa, <4 x float> %sc)
  %ebp_f = call float @diff3_k(<4 x float> %fb, <4 x float> %sp)
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
  %ln0 = call float @llvm.minnum.f32(float %l00, float %l10)
  %ln1 = call float @llvm.minnum.f32(float %l01, float %l11)
  %vmn = call float @llvm.minnum.f32(float %ln0, float %ln1)
  %vsp = fadd float %sprx, %spry
  %wo0 = fsub float %vsp, 12.0
  %wo1 = fmul float %wo0, 0.5
  %w_occ_s = call float @sat(float %wo1)
  %dxq0 = fsub float %x10, %x00
  %dxq1 = fsub float %x11, %x01
  %dxqs = fadd float %dxq0, %dxq1

  %dyq0 = fsub float %y01, %y00
  %dyq1 = fsub float %y11, %y10
  %dyqs = fadd float %dyq0, %dyq1
  %dyq = fmul float %dyqs, 0.5
  %div = call float @llvm.amdgcn.fmad.ftz.f32(float %dxqs, float 0.5, float %dyq)
  %div_s = fmul float %div, 0xBFB70A3D80000000 ; -0.09
  %div_c0 = call float @llvm.maxnum.f32(float %div_s, float 0xBFE19999A0000000) ; -0.55
  %div_b_s = call float @llvm.minnum.f32(float %div_c0, float 0x3FE19999A0000000) ; 0.55
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
  %c0 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u_q, float %v_q, float %x00, float %y00, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s, float %vmn)
  %e0 = extractvalue { <4 x float>, float } %c0, 1
  %st0 = insertvalue { <4 x float>, float, float } zeroinitializer, float %e0, 2
  %st1 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st0, { <4 x float>, float } %c0, float %w00)
  %c1 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u_q, float %v_q, float %x10, float %y10, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s, float %vmn)
  %st2 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st1, { <4 x float>, float } %c1, float %w10)
  %c2 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u_q, float %v_q, float %x01, float %y01, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s, float %vmn)
  %st3 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st2, { <4 x float>, float } %c2, float %w01)
  %c3 = call { <4 x float>, float } @taps(<8 x i32> %tp, <8 x i32> %tc, <4 x i32> %sm, float %u_q, float %v_q, float %x11, float %y11, float %t, float %tc1, float %s, <4 x float> %sp, <4 x float> %sc, float %es, i1 %str_en, float %rx0_s, float %rx1_s, float %ry0_s, float %ry1_s, float %w_occ_s, float %div_b_s, float %vmn)
  %st4 = call { <4 x float>, float, float } @accum({ <4 x float>, float, float } %st3, { <4 x float>, float } %c3, float %w11)
  %qs = extractvalue { <4 x float>, float, float } %st4, 0
  %os = extractvalue { <4 x float>, float, float } %st4, 1
  %emin = extractvalue { <4 x float>, float, float } %st4, 2
  %ios = fdiv arcp afn float 1.0, %os
  %sm_0 = call <4 x float> @scale4(<4 x float> %qs, float %ios)
  %sm_ = insertelement <4 x float> %sm_0, float 0.0, i32 3
  br label %merge

merge:
  %mov = phi <4 x float> [ %zm, %fastzero ], [ %fm, %fastside ], [ %sm_, %slow ]
  %emov = phi float [ %es, %fastzero ], [ %fe_rel, %fastside ], [ %emin, %slow ]
  %vmg = phi float [ %vmb, %fastzero ], [ %vmb, %fastside ], [ 0.0, %slow ]
  %hl_mov = extractelement <4 x float> %mov, i32 3
  %is_wire_fol = fcmp oge float %hl_mov, 4.0
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

  %mz.n16 = fneg float %dl_s
  %ch_ex0 = call float @llvm.amdgcn.fmad.ftz.f32(float %mx_ch, float 3.0, float %mz.n16)
  %ch_ex = fsub float %ch_ex0, 0x3FE3333340000000 ; 0.60
  %wh_ex = fsub float %l_stat, 0x40047AE140000000 ; 2.56
  %pos_c = call float @llvm.maxnum.f32(float %wh_ex, float %ch_ex)
  ; suppress baseline ws0 on moving bright texture where static is darker (prevents dark spots)
  %fl0 = fsub float 0x3F789374C0000000, %es; 0.006
  %fl1 = fmul float %fl0, 0x4074D55560000000; 333.33334
  %ws0_raw = call float @sat(float %fl1)
  %mv_act = fcmp ogt float %vmg, 2.0
  %em_lo = fcmp olt float %emov, 0x3F8EB851E0000000 ; 0.015
  %st_dk = fcmp olt float %dl_s, 0xBFDEB851E0000000 ; -0.48
  %dk_sp0 = and i1 %mv_act, %em_lo
  %dk_sp1 = and i1 %dk_sp0, %st_dk
  %gl_hi = fcmp ogt float %hl_mov, 0x3FD99999A0000000 ; 0.40
  %gl_br = fcmp olt float %dl_s, 0xBFA99999A0000000 ; -0.05
  %is_glint = and i1 %gl_hi, %gl_br
  %hl_any = or i1 %is_wire_fol, %is_glint
  %hl_act = and i1 %mv_act, %hl_any
  %dk_spot = or i1 %dk_sp1, %hl_act
  %ws0 = select i1 %dk_spot, float 0.0, float %ws0_raw
  %pgk = getelementptr i8, ptr addrspace(4) %tbl, i64 336
  %vgk = load <4 x i32>, ptr addrspace(4) %pgk, align 16, !invariant.load !0
  %gk = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vgk, i32 %gbo, i32 0, i32 0)
  %hasfg = icmp ne i32 %gk, 0
  ; inline static HUD / crosshair / subtitle stroke protection
  %hv0 = fadd float %vmg, %spr
  %hv1 = fmul float %hv0, 2.0
  %w_hv = call float @sat(float %hv1)
  %he0 = fsub float 0x3FB47AE140000000, %es ; 0.08
  %he1 = fmul float %he0, 32.0
  %w_he = call float @sat(float %he1)
  %hc1_f = fmul float %pos_c, 64.0
  %w_hc = call float @sat(float %hc1_f)
  %nd0 = fsub float %dl_s, 0x3FA99999A0000000 ; 0.05
  %nd1 = fmul float %nd0, 25.0
  %w_nd = call float @sat(float %nd1)
  %wh01 = fmul float %w_hv, %w_he
  %wh23 = fmul float %w_hc, %w_nd
  %w_hud0 = fmul float %wh01, %wh23
  %wf_act = and i1 %mv_act, %is_wire_fol
  %hud_kill = or i1 %hasfg, %wf_act
  %nfgk = xor i1 %hud_kill, true
  %w_hud = select i1 %nfgk, float %w_hud0, float 0.0
  %vmp_k = fmul float %vm_p, 0x3FEE147AE0000000
  %vmg_e = call float @llvm.maxnum.f32(float %vm_c, float %vmp_k)
  %wv0 = fsub float %vmg_e, 4.0
  %wv1 = fmul float %wv0, 0x3FC5555560000000
  %w_vel = call float @sat(float %wv1)
  %w_mov = select i1 %hasfg, float 1.0, float %w_vel
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %mfit_ok = and i1 %mfit, %nofgn
  %w_fgk = select i1 %mfit_ok, float %w_mov, float 0.0
  %ws_keep = fsub float 1.0, %w_fgk
  %ws0_s = fmul float %ws0, %ws_keep
  %ws = call float @llvm.maxnum.f32(float %ws0_s, float %w_hud)
  %pcn0 = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cn0 = load <4 x i32>, ptr addrspace(4) %pcn0, align 16, !invariant.load !0
  %fl0b = extractelement <4 x i32> %cn0, i32 2
  %nocfb = and i32 %fl0b, 8
  %nocf = icmp ne i32 %nocfb, 0
  ; fill weight: cw is exactly 0 when emov <= 0.025 (residual and every base term off), when emov <= es
  ; (gcw = 0), on hl_act, under the full foreground kill (ws_keep = 0) or with the no-fill flag; then movc = mov
  ; and the wave skips the fill block
  %cwa = fcmp ogt float %emov, 0x3F999999A0000000 ; 0.025
  %cwg = fcmp ogt float %emov, %es
  %cwl0 = and i1 %cwa, %cwg
  %nhlact = xor i1 %hl_act, true
  %wsk_pos = fcmp ogt float %ws_keep, 0.0
  %nfgkill = and i1 %nhlact, %wsk_pos
  %nnocf = xor i1 %nocf, true
  %cwl1 = and i1 %cwl0, %nfgkill
  %cw_live = and i1 %cwl1, %nnocf
  br i1 %cw_live, label %fill, label %fdone
fill:
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
  %dmp = call float @diff3_k(<4 x float> %mov, <4 x float> %sp)
  %dmc = call float @diff3_k(<4 x float> %mov, <4 x float> %sc)
  %dmv = call float @diff3_k(<4 x float> %mov, <4 x float> %stat)
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
  %bg2x = and i1 %bg2, %nmov_obj
  ; 2.24: static-colour fallback only where the two motion-compensated samples disagree (emov > 0.25)
  %bge = fcmp ogt float %emov, 0.25
  %bg3 = and i1 %bg2x, %bge
  %bgs0a = fsub float %dmv, %es
  %bgs0 = fsub float %bgs0a, 0x3FCC28F5C0000000 ; 0.22
  %bgs1 = fmul float %bgs0, 4.0
  %bgs = call float @sat(float %bgs1)
  %bgw = select i1 %bg3, float %bgs, float 0.0
  %ds0 = fsub float %dl_s, 0x3FC70A3D80000000 ; 0.18
  %ds1 = fmul float %ds0, 2.5
  %dsa = call float @sat(float %ds1)
  %dse0 = fsub float %emov, 0x3FCC28F5C0000000 ; 0.22
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

  %cw4 = call float @llvm.amdgcn.fmad.ftz.f32(float %act, float %res_cw, float %cw_base)
  %cw3_0 = call float @sat(float %cw4)
  %gcw0 = fsub float %emov, %es
  %gcw1 = fmul float %gcw0, 4.0
  %gcw = call float @sat(float %gcw1)
  %cw3_1 = fmul float %cw3_0, %gcw
  ; temporal flow consistency confidence: damp false static fallback when trajectory is consistent across frames
  %gbi_m = lshr i32 %gbo, 2
  %gbx_m = urem i32 %gbi_m, 120
  %gby_m = udiv i32 %gbi_m, 120
  %gfo_m = shl i32 %gbo, 1
  %fg_cur = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %vf, i32 %gfo_m, i32 0, i32 0)
  %fg_cx = extractelement <2 x float> %fg_cur, i32 0
  %fg_cy = extractelement <2 x float> %fg_cur, i32 1
  %fg_ux0 = fmul float %fg_cx, 0.0625
  %fg_uy0 = fmul float %fg_cy, 0.0625
  %fg_uxi = fptosi float %fg_ux0 to i32
  %fg_uyi = fptosi float %fg_uy0 to i32
  %fg_bx0 = add i32 %gbx_m, %fg_uxi
  %fg_bx1 = call i32 @llvm.smax.i32(i32 %fg_bx0, i32 0)
  %fg_bx = call i32 @llvm.smin.i32(i32 %fg_bx1, i32 119)
  %fg_by0 = add i32 %gby_m, %fg_uyi
  %fg_by1 = call i32 @llvm.smax.i32(i32 %fg_by0, i32 0)
  %fg_by = call i32 @llvm.smin.i32(i32 %fg_by1, i32 67)
  %pfp_m = getelementptr i8, ptr addrspace(4) %tbl, i64 272
  %vfp_m = load <4 x i32>, ptr addrspace(4) %pfp_m, align 16, !invariant.load !0
  %fg_prv = call <2 x float> @ldF(<4 x i32> %vfp_m, i32 %fg_bx, i32 %fg_by)
  %fg_px = extractelement <2 x float> %fg_prv, i32 0
  %fg_py = extractelement <2 x float> %fg_prv, i32 1
  %tc_dx0 = fsub float %fg_cx, %fg_px
  %tc_dy0 = fsub float %fg_cy, %fg_py
  %tc_dx = call float @llvm.fabs.f32(float %tc_dx0)
  %tc_dy = call float @llvm.fabs.f32(float %tc_dy0)
  %tc_err = fadd float %tc_dx, %tc_dy
  %tc_p0 = call float @llvm.fabs.f32(float %fg_px)
  %tc_p1 = call float @llvm.fabs.f32(float %fg_py)
  %tc_pm = fadd float %tc_p0, %tc_p1
  %tc_act0 = fsub float %tc_pm, 2.0
  %tc_act = call float @sat(float %tc_act0)
  %tc_c0 = fsub float 2.0, %tc_err
  %tc_c1 = fmul float %tc_c0, 0.5
  %tc_c = call float @sat(float %tc_c1)
  %tc_em0 = fsub float 0x3FD6666660000000, %emov ; 0.35 - emov
  %tc_em1 = fmul float %tc_em0, 4.0
  %tc_em = call float @sat(float %tc_em1)
  %tc_w0 = fmul float %tc_act, %tc_c
  %tc_w = fmul float %tc_w0, %tc_em
  %tc_dmp0 = fmul float %tc_w, 0x3FD3333340000000 ; 0.30
  %tc_dmp = fsub float 1.0, %tc_dmp0
  %cw3_s = fmul float %cw3_1, %ws_keep
  %cw3 = select i1 %hl_act, float 0.0, float %cw3_s
  %cw = select i1 %nocf, float 0.0, float %cw3
  %movf = call <4 x float> @lerp4(<4 x float> %mov, <4 x float> %fillc, float %cw)
  br label %fdone
fdone:
  %movc = phi <4 x float> [ %mov, %merge ], [ %movf, %fill ]
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
  ; HUD rectangle (hud file): uniform skip of the per-lane rectangle test when no rectangle is set (hudw == 0)
  %hnz = icmp ne i32 %hudw, 0
  br i1 %hnz, label %hud_rect, label %hud_done

hud_rect:
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
  %xh1080 = fptosi float %u_q to i32
  %yh1080 = fptosi float %v_q to i32
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
  br label %hud_done

hud_done:
  %inhud = phi i1 [ %hcsel, %hud_rect ], [ false, %fdone ]
  %out1h = select i1 %inhud, <4 x float> %sc, <4 x float> %out1
  %out2 = select i1 %mk, <4 x float> <float 1.0, float 0.0, float 1.0, float 1.0>, <4 x float> %out1h
  ; alpha = static-pixel flag for rcas8 (P == C in RGB at this pixel, es == 0): 1.0 static, 254/255 otherwise.
  ; Only the scanout and osd8 (pass-through) see the mid's alpha; rcas8 skips the AA where the flag is set.
  %st_px = fcmp oeq float %es, 0.0
  %alpha = select i1 %st_px, float 1.0, float 0x3FEFDFDFE0000000 ; 254/255
  %out = insertelement <4 x float> %out2, float %alpha, i32 3
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %out, i32 15, i32 %x, i32 %y, <8 x i32> %to, i32 0, i32 0)
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}

; v_mad_f32 without declaring the fp32 denormal mode (it costs warp8 65-71 VGPRs, tools/tests/test_denormal_mode_attribute.py):
; every single-use fmul feeding an fadd/fsub of the same block is llvm.amdgcn.fmad.ftz (fsub through an exact fneg).
; FLOAT_MODE 0xc0 flushes fp32 denormals, and v_mad_f32 rounds the product and then the sum like v_mul_f32 + v_add_f32
; (LLVM ISD::FMAD), the identity the 21 shaders that declare the preserve-sign fp32 denormal mode already rely on.
; tools/tests/test_warp8_fmad_ftz.py
declare float @llvm.amdgcn.fmad.ftz.f32(float, float, float) nounwind readnone speculatable
