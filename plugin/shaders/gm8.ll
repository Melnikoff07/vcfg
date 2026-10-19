; user_sgprs=2
; mode 8.2, pass 4: dominant (camera) motion of this frame = per component lower median of the L2 vectors that
; really match (SAD(best) * 5 <= SAD(zero) * 4, or SAD(best) <= 160). One group of 64 lanes, dispatch (1, 1).
;  1. histogram of 2 x 128 bins over [-64, 63] L2 px (= +-256 full-res px, outside values clamped) in LDS
;  2. inclusive prefix sum over 64 lanes (2 bins per lane), the lane whose bin crosses n/2 publishes it
; -> misc dwords 4, 5 (i32, L2 px), read by fgs8. A plain mean would be pulled by the very object we look for.
; table: +0xF0 V#V2 (60x34 packed), +0x120 V#misc, +0x140 V#S2 (60x34: SAD best | SAD zero << 16)
target triple = "amdgcn--"
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.i32(i32, <4 x i32>, i32, i32, i32 immarg)
declare void @llvm.amdgcn.s.barrier()
declare i32 @llvm.smax.i32(i32, i32)
declare i32 @llvm.smin.i32(i32, i32)

@hist = internal addrspace(3) global [256 x i32] undef, align 16    ; [c * 128 + bin]
@scan = internal addrspace(3) global [128 x i32] undef, align 16    ; [c * 64 + lane]
@res = internal addrspace(3) global [2 x i32] undef, align 16

define internal void @bar() alwaysinline {
  fence syncscope("workgroup") release
  call void @llvm.amdgcn.s.barrier()
  fence syncscope("workgroup") acquire
  ret void
}

; 128 bins over [-64, 63]
define internal i32 @bin(i32 %v) alwaysinline {
  %a = call i32 @llvm.smax.i32(i32 %v, i32 -64)
  %b = call i32 @llvm.smin.i32(i32 %a, i32 63)
  %c = add i32 %b, 64
  ret i32 %c
}

; add block i (if it exists and matches) to the histogram
define internal void @acc(<4 x i32> %v2, <4 x i32> %vs, i32 %i) alwaysinline {
  %ok = icmp ult i32 %i, 2040
  %ic = select i1 %ok, i32 %i, i32 0
  %off = shl i32 %ic, 2
  %v = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %v2, i32 %off, i32 0, i32 0)
  %s = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vs, i32 %off, i32 0, i32 0)
  %sb = and i32 %s, 65535
  %sz = lshr i32 %s, 16
  %sb5 = mul i32 %sb, 5
  %sz4 = shl i32 %sz, 2
  %g1 = icmp ule i32 %sb5, %sz4
  %g2 = icmp ule i32 %sb, 160
  %g12 = or i1 %g1, %g2
  %g = and i1 %g12, %ok
  %gi = zext i1 %g to i32
  %vxs = shl i32 %v, 16
  %vx = ashr i32 %vxs, 16
  %vy = ashr i32 %v, 16
  %bx = call i32 @bin(i32 %vx)
  %by0 = call i32 @bin(i32 %vy)
  %by = add i32 %by0, 128
  %px = getelementptr [256 x i32], ptr addrspace(3) @hist, i32 0, i32 %bx
  %py = getelementptr [256 x i32], ptr addrspace(3) @hist, i32 0, i32 %by
  %o1 = atomicrmw add ptr addrspace(3) %px, i32 %gi syncscope("workgroup") monotonic
  %o2 = atomicrmw add ptr addrspace(3) %py, i32 %gi syncscope("workgroup") monotonic
  ret void
}

; one Hillis-Steele step of the inclusive scan of component c: x += scan[c*64 + tid - o] (tid >= o)
define internal i32 @step(i32 %tid, i32 %c, i32 %x, i32 %o) alwaysinline {
  %c64 = shl i32 %c, 6
  %me = add i32 %c64, %tid
  %pm = getelementptr [128 x i32], ptr addrspace(3) @scan, i32 0, i32 %me
  store i32 %x, ptr addrspace(3) %pm, align 4
  call void @bar()
  %has = icmp uge i32 %tid, %o
  %j0 = sub i32 %me, %o
  %j = select i1 %has, i32 %j0, i32 %me
  %pj = getelementptr [128 x i32], ptr addrspace(3) @scan, i32 0, i32 %j
  %y = load i32, ptr addrspace(3) %pj, align 4
  %y1 = select i1 %has, i32 %y, i32 0
  call void @bar()
  %r = add i32 %x, %y1
  ret i32 %r
}

; lower median bin of component c (smallest bin with cumulative count > n/2) -> res[c] (bin - 64)
define internal void @med(i32 %tid, i32 %c) alwaysinline {
  %c128 = shl i32 %c, 7
  %t2 = shl i32 %tid, 1
  %i0 = add i32 %c128, %t2
  %i1 = add i32 %i0, 1
  %p0 = getelementptr [256 x i32], ptr addrspace(3) @hist, i32 0, i32 %i0
  %p1 = getelementptr [256 x i32], ptr addrspace(3) @hist, i32 0, i32 %i1
  %h0 = load i32, ptr addrspace(3) %p0, align 4
  %h1 = load i32, ptr addrspace(3) %p1, align 4
  %a = add i32 %h0, %h1
  %s1 = call i32 @step(i32 %tid, i32 %c, i32 %a, i32 1)
  %s2 = call i32 @step(i32 %tid, i32 %c, i32 %s1, i32 2)
  %s3 = call i32 @step(i32 %tid, i32 %c, i32 %s2, i32 4)
  %s4 = call i32 @step(i32 %tid, i32 %c, i32 %s3, i32 8)
  %s5 = call i32 @step(i32 %tid, i32 %c, i32 %s4, i32 16)
  %inc = call i32 @step(i32 %tid, i32 %c, i32 %s5, i32 32)
  ; total = inclusive sum of lane 63
  %c64 = shl i32 %c, 6
  %me = add i32 %c64, %tid
  %pm = getelementptr [128 x i32], ptr addrspace(3) @scan, i32 0, i32 %me
  store i32 %inc, ptr addrspace(3) %pm, align 4
  call void @bar()
  %li = add i32 %c64, 63
  %pl = getelementptr [128 x i32], ptr addrspace(3) @scan, i32 0, i32 %li
  %n = load i32, ptr addrspace(3) %pl, align 4
  %half = lshr i32 %n, 1
  %exc = sub i32 %inc, %a
  %cum0 = add i32 %exc, %h0
  ; bin 2 tid: cum0 > half >= exc; bin 2 tid + 1: inc > half >= cum0
  %ga = icmp ugt i32 %cum0, %half
  %gb = icmp ule i32 %exc, %half
  %isa = and i1 %ga, %gb
  %gc = icmp ugt i32 %inc, %half
  %gd = icmp ule i32 %cum0, %half
  %isb = and i1 %gc, %gd
  %hit = or i1 %isa, %isb
  %bb = zext i1 %isb to i32
  %bin = add i32 %t2, %bb
  %val = sub i32 %bin, 64
  br i1 %hit, label %w, label %done
w:
  %pr = getelementptr [2 x i32], ptr addrspace(3) @res, i32 0, i32 %c
  store i32 %val, ptr addrspace(3) %pr, align 4
  br label %done
done:
  call void @bar()
  ret void
}

define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) #0 {
entry:
  %p2 = getelementptr i8, ptr addrspace(4) %tbl, i64 240
  %v2 = load <4 x i32>, ptr addrspace(4) %p2, align 16, !invariant.load !0
  %pm = getelementptr i8, ptr addrspace(4) %tbl, i64 288
  %vm = load <4 x i32>, ptr addrspace(4) %pm, align 16, !invariant.load !0
  %ps = getelementptr i8, ptr addrspace(4) %tbl, i64 320
  %vs = load <4 x i32>, ptr addrspace(4) %ps, align 16, !invariant.load !0
  ; ---- clear the histogram (4 bins per lane) and the result (no good vector at all -> 0)
  %t4 = shl i32 %tid, 2
  %q0 = getelementptr [256 x i32], ptr addrspace(3) @hist, i32 0, i32 %t4
  store <4 x i32> zeroinitializer, ptr addrspace(3) %q0, align 16
  %lt2 = icmp ult i32 %tid, 2
  br i1 %lt2, label %zr, label %clr
zr:
  %pr = getelementptr [2 x i32], ptr addrspace(3) @res, i32 0, i32 %tid
  store i32 0, ptr addrspace(3) %pr, align 4
  br label %clr
clr:
  call void @bar()
  ; ---- histogram: lane handles blocks tid + 64 k, 4 per iteration (8 loads in flight), k = 0..31
  br label %loop
loop:
  %k = phi i32 [ 0, %clr ], [ %k1, %loop ]
  %kb = shl i32 %k, 8
  %i0 = add i32 %kb, %tid
  %i1 = add i32 %i0, 64
  %i2 = add i32 %i0, 128
  %i3 = add i32 %i0, 192
  call void @acc(<4 x i32> %v2, <4 x i32> %vs, i32 %i0)
  call void @acc(<4 x i32> %v2, <4 x i32> %vs, i32 %i1)
  call void @acc(<4 x i32> %v2, <4 x i32> %vs, i32 %i2)
  call void @acc(<4 x i32> %v2, <4 x i32> %vs, i32 %i3)
  %k1 = add i32 %k, 1
  %more = icmp ult i32 %k1, 8
  br i1 %more, label %loop, label %hdone
hdone:
  call void @bar()
  call void @med(i32 %tid, i32 0)
  call void @med(i32 %tid, i32 1)
  %is0 = icmp eq i32 %tid, 0
  br i1 %is0, label %st, label %exit
st:
  %rx = load i32, ptr addrspace(3) @res, align 4
  %pry = getelementptr [2 x i32], ptr addrspace(3) @res, i32 0, i32 1
  %ry = load i32, ptr addrspace(3) %pry, align 4
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %rx, <4 x i32> %vm, i32 16, i32 0, i32 0)
  call void @llvm.amdgcn.raw.buffer.store.i32(i32 %ry, <4 x i32> %vm, i32 20, i32 0, i32 0)
  br label %exit
exit:
  ret void
}

attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
