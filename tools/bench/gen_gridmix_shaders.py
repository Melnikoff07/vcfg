"""Emit isolated experimental kernels; never replaces production shader sources."""
import pathlib
import re
import struct
import sys


def f32(x):
    return '0x'+struct.pack('>d',struct.unpack('f',struct.pack('f',x))[0]).hex().upper()


DECL = '''; user_sgprs=2
target triple = "amdgcn--"
declare <2 x i32> @llvm.amdgcn.raw.buffer.load.v2i32(<4 x i32>,i32,i32,i32 immarg)
declare <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32>,i32,i32,i32 immarg)
declare i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32>,i32,i32,i32 immarg)
declare void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float>,<4 x i32>,i32,i32,i32 immarg)
declare i32 @llvm.amdgcn.alignbyte(i32,i32,i32)
declare i32 @llvm.amdgcn.sad.u8(i32,i32,i32)
declare i32 @llvm.smin.i32(i32,i32)
declare i32 @llvm.smax.i32(i32,i32)
declare float @llvm.floor.f32(float)
declare float @llvm.fabs.f32(float)
define internal i32 @clamp(i32 %v,i32 %hi) alwaysinline {
 %lo = call i32 @llvm.smax.i32(i32 %v,i32 0)
 %out = call i32 @llvm.smin.i32(i32 %lo,i32 %hi)
 ret i32 %out
}
define internal <2 x float> @field(<4 x i32> %v,i32 %x,i32 %y) alwaysinline {
 %cx = call i32 @clamp(i32 %x,i32 119)
 %cy = call i32 @clamp(i32 %y,i32 67)
 %row = mul i32 %cy,120
 %idx = add i32 %row,%cx
 %off = shl i32 %idx,3
 %value = call <2 x float> @llvm.amdgcn.raw.buffer.load.v2f32(<4 x i32> %v,i32 %off,i32 0,i32 0)
 ret <2 x float> %value
}
define internal i32 @rnd(float %v) alwaysinline {
 %half = fmul float %v,0.5
 %a = fadd float %half,0.5
 %b = call float @llvm.floor.f32(float %a)
 %i = fptosi float %b to i32
 ret i32 %i
}
'''


def refinement():
    out=[DECL,'''define internal float @cost(<4 x i32> %p,<4 x i32> %c,i32 %X,i32 %Y,
 float %vx,float %vy,float %sx,float %sy) alwaysinline {
 %dx = call i32 @rnd(float %vx)
 %dy = call i32 @rnd(float %vy)
 %nx = sub i32 0,%dx
 %ny = sub i32 0,%dy
 %cx = ashr i32 %dx,1
 %cy = ashr i32 %dy,1
 %px = ashr i32 %nx,1
 %py = ashr i32 %ny,1
 %xp0 = sub i32 %X,%px
 %xc0 = sub i32 %X,%cx
 %xp = call i32 @clamp(i32 %xp0,i32 956)
 %xc = call i32 @clamp(i32 %xc0,i32 956)
 %yp = sub i32 %Y,%py
 %yc = sub i32 %Y,%cy
 %ap = and i32 %xp,-4
 %ac = and i32 %xc,-4
 %sp = and i32 %xp,3
 %sc = and i32 %xc,3
''']
    for row in range(4):
        for side in ('p','c'):
            out.append(f''' %yr{side}{row} = add i32 %y{side},{row}
 %y{side}{row} = call i32 @clamp(i32 %yr{side}{row},i32 539)
 %r{side}{row} = mul i32 %y{side}{row},960
 %o{side}{row} = add i32 %r{side}{row},%a{side}
 %d{side}{row} = call <2 x i32> @llvm.amdgcn.raw.buffer.load.v2i32(<4 x i32> %{side},i32 %o{side}{row},i32 0,i32 0)
 %l{side}{row} = extractelement <2 x i32> %d{side}{row},i32 0
 %h{side}{row} = extractelement <2 x i32> %d{side}{row},i32 1
 %q{side}{row} = call i32 @llvm.amdgcn.alignbyte(i32 %h{side}{row},i32 %l{side}{row},i32 %s{side})
''')
        previous='0' if row==0 else f'%sad{row-1}'
        out.append(f' %sad{row} = call i32 @llvm.amdgcn.sad.u8(i32 %qp{row},i32 %qc{row},i32 {previous})\n')
    out.append(''' %s = uitofp i32 %sad3 to float
 %ex = fsub float %vx,%sx
 %ey = fsub float %vy,%sy
 %ax = call float @llvm.fabs.f32(float %ex)
 %ay = call float @llvm.fabs.f32(float %ey)
 %sum = fadd float %ax,%ay
 %pen = fmul float %sum,2.0
 %result = fadd float %s,%pen
 ret float %result
}
define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl,i32 inreg %gx,i32 %tid) #0 {
entry:
 %wg = shl i32 %gx,6
 %cell = add i32 %wg,%tid
 %valid = icmp ult i32 %cell,32400
 br i1 %valid,label %work,label %end
work:
 %bx = urem i32 %cell,240
 %by = udiv i32 %cell,240
 %px = lshr i32 %bx,1
 %py = lshr i32 %by,1
 %X = shl i32 %bx,2
 %Y = shl i32 %by,2
 %pf = getelementptr i8,ptr addrspace(4) %tbl,i64 256
 %vf = load <4 x i32>,ptr addrspace(4) %pf,align 16
 %pp = getelementptr i8,ptr addrspace(4) %tbl,i64 144
 %vp = load <4 x i32>,ptr addrspace(4) %pp,align 16
 %pc = getelementptr i8,ptr addrspace(4) %tbl,i64 128
 %vc = load <4 x i32>,ptr addrspace(4) %pc,align 16
 %po = getelementptr i8,ptr addrspace(4) %tbl,i64 560
 %vo = load <4 x i32>,ptr addrspace(4) %po,align 16
 %seed = call <2 x float> @field(<4 x i32> %vf,i32 %px,i32 %py)
 %sx = extractelement <2 x float> %seed,i32 0
 %sy = extractelement <2 x float> %seed,i32 1
 %c0 = call float @cost(<4 x i32> %vp,<4 x i32> %vc,i32 %X,i32 %Y,float %sx,float %sy,float %sx,float %sy)
''')
    lastcost,lastx,lasty='%c0','%sx','%sy'
    for k in range(9):
        ox,oy=k%3-1,k//3-1
        tag=f'n{k}'
        out.append(f''' %{tag}x = add i32 %px,{ox}
 %{tag}y = add i32 %py,{oy}
 %{tag}v = call <2 x float> @field(<4 x i32> %vf,i32 %{tag}x,i32 %{tag}y)
 %{tag}vx = extractelement <2 x float> %{tag}v,i32 0
 %{tag}vy = extractelement <2 x float> %{tag}v,i32 1
 %{tag}cost = call float @cost(<4 x i32> %vp,<4 x i32> %vc,i32 %X,i32 %Y,float %{tag}vx,float %{tag}vy,float %sx,float %sy)
 %{tag}take = fcmp olt float %{tag}cost,{lastcost}
 %{tag}best = select i1 %{tag}take,float %{tag}cost,float {lastcost}
 %{tag}outx = select i1 %{tag}take,float %{tag}vx,float {lastx}
 %{tag}outy = select i1 %{tag}take,float %{tag}vy,float {lasty}
''')
        lastcost,lastx,lasty=f'%{tag}best',f'%{tag}outx',f'%{tag}outy'
    out.append(f''' %centre_x = call i32 @rnd(float {lastx})
 %centre_y = call i32 @rnd(float {lasty})
''')
    for k in range(9):
        ox,oy=k%3-1,k//3-1;tag=f'l{k}'
        out.append(f''' %{tag}x = add i32 %centre_x,{ox}
 %{tag}y = add i32 %centre_y,{oy}
 %{tag}x2 = shl i32 %{tag}x,1
 %{tag}y2 = shl i32 %{tag}y,1
 %{tag}vx = sitofp i32 %{tag}x2 to float
 %{tag}vy = sitofp i32 %{tag}y2 to float
 %{tag}cost = call float @cost(<4 x i32> %vp,<4 x i32> %vc,i32 %X,i32 %Y,float %{tag}vx,float %{tag}vy,float %sx,float %sy)
 %{tag}take = fcmp olt float %{tag}cost,{lastcost}
 %{tag}best = select i1 %{tag}take,float %{tag}cost,float {lastcost}
 %{tag}outx = select i1 %{tag}take,float %{tag}vx,float {lastx}
 %{tag}outy = select i1 %{tag}take,float %{tag}vy,float {lasty}
''')
        lastcost,lastx,lasty=f'%{tag}best',f'%{tag}outx',f'%{tag}outy'
    out.append(f''' %pm = getelementptr i8,ptr addrspace(4) %tbl,i64 288
 %vm = load <4 x i32>,ptr addrspace(4) %pm,align 16
 %fade = call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm,i32 28,i32 0,i32 0)
 %isfade = icmp ne i32 %fade,0
 %outx = select i1 %isfade,float 0.0,float {lastx}
 %outy = select i1 %isfade,float 0.0,float {lasty}
 %v0 = insertelement <2 x float> poison,float %outx,i32 0
 %v1 = insertelement <2 x float> %v0,float %outy,i32 1
 %off = shl i32 %cell,3
 call void @llvm.amdgcn.raw.buffer.store.v2f32(<2 x float> %v1,<4 x i32> %vo,i32 %off,i32 0,i32 0)
 br label %end
end:
 ret void
}}
attributes #0 = {{ "amdgpu-flat-work-group-size"="64,64" }}
''')
    text=''.join(out).replace('inreg %tbl,i32','inreg %tbl, i32').replace('inreg %gx,i32','inreg %gx, i32')
    # NumPy adds the integer SAD to the float32 penalty in float64. Preserve
    # that tie-break instead of changing several near-equal winners per frame.
    text=text.replace('define internal float @cost','define internal double @cost').replace('call float @cost','call double @cost')
    text=text.replace(' %result = fadd float %s,%pen\n ret float %result',
                      ' %s64 = fpext float %s to double\n %pen64 = fpext float %pen to double\n %result = fadd double %s64,%pen64\n ret double %result')
    lines=[]
    for line in text.splitlines():
        if 'fcmp olt float' in line or 'best = select' in line:
            line=line.replace('float','double')
        lines.append(line)
    return '\n'.join(lines)+'\n'


def fine_warp(source):
    src=source.replace('i64 256\n','i64 560\n',1).replace('i64 64\n','i64 576\n',1)
    src=src.replace('%r = mul i32 %iy, 120','%r = mul i32 %iy, 240')
    begin=src.index('  %cbx0 =');end=src.index('  %x00 =',begin)
    segment=src[begin:end].replace('lshr i32 %x, 4','lshr i32 %x, 3').replace('lshr i32 %y, 4','lshr i32 %y, 3')
    segment=segment.replace('i32 119','i32 239').replace('i32 67','i32 134').replace('0.0625','0.125').replace('0.46875','0.4375')
    src=src[:begin]+segment+src[end:]
    # Fine scratch alpha stores the pre-post static decision. It is never flipped.
    static=f'''  %sd = fsub <4 x float> %sp, %sc
  %sa = call <4 x float> @llvm.fabs.v4f32(<4 x float> %sd)
  %sr = extractelement <4 x float> %sa, i32 0
  %sg = extractelement <4 x float> %sa, i32 1
  %sb = extractelement <4 x float> %sa, i32 2
  %srg = call float @llvm.maxnum.f32(float %sr, float %sg)
  %smax = call float @llvm.maxnum.f32(float %srg, float %sb)
  %static = fcmp olt float %smax, {f32(1/255)}
  %alpha = select i1 %static, float 1.0, float 0.0'''
    return src.replace('  %alpha = extractelement <4 x float> %sc, i32 3',static)


def blend(source):
    declarations=source[:source.index('define amdgpu_cs')]
    declarations=declarations.replace('target triple = "amdgcn--"', 'target triple = "amdgcn--"\ndeclare <4 x float> @llvm.maxnum.v4f32(<4 x float>,<4 x float>)\ndeclare <4 x float> @llvm.minnum.v4f32(<4 x float>,<4 x float>)\ndeclare <4 x float> @llvm.fabs.v4f32(<4 x float>)\ndeclare i32 @llvm.smin.i32(i32,i32)\ndeclare i32 @llvm.smax.i32(i32,i32)')
    head='''define internal <4 x float> @mix(<8 x i32> %base,<8 x i32> %fine,i32 %x0,i32 %y0) alwaysinline {
 %xa = call i32 @llvm.smax.i32(i32 %x0,i32 0)
 %x = call i32 @llvm.smin.i32(i32 %xa,i32 1919)
 %ya = call i32 @llvm.smax.i32(i32 %y0,i32 0)
 %y = call i32 @llvm.smin.i32(i32 %ya,i32 1079)
 %a = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15,i32 %x,i32 %y,<8 x i32> %base,i32 0,i32 0)
 %b = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15,i32 %x,i32 %y,<8 x i32> %fine,i32 0,i32 0)
 %sum = fadd <4 x float> %a,%b
 %out = fmul <4 x float> %sum,<float 0.5,float 0.5,float 0.5,float 0.5>
 ret <4 x float> %out
}
'''
    main=source[source.index('define amdgpu_cs'):source.index('  %ptc =')]
    main+='''  %pb = getelementptr i8,ptr addrspace(4) %tbl,i64 64
  %base = load <8 x i32>,ptr addrspace(4) %pb,align 32
  %pf = getelementptr i8,ptr addrspace(4) %tbl,i64 576
  %fine = load <8 x i32>,ptr addrspace(4) %pf,align 32
  %po = getelementptr i8,ptr addrspace(4) %tbl,i64 608
  %outdesc = load <8 x i32>,ptr addrspace(4) %po,align 32
  %xn = sub i32 %x,1
  %xp = add i32 %x,1
  %yn = sub i32 %y,1
  %yp = add i32 %y,1
'''
    for name,x,y in [('m','%x','%y'),('n','%x','%yn'),('s','%x','%yp'),('w','%xn','%y'),('e','%xp','%y')]:
        main+=f'  %{name} = call <4 x float> @mix(<8 x i32> %base,<8 x i32> %fine,i32 {x},i32 {y})\n'
    main+='''  %ns = fadd <4 x float> %n,%s
  %nsw = fadd <4 x float> %ns,%w
  %nswe = fadd <4 x float> %nsw,%e
  %avg = fmul <4 x float> %nswe,<float 0.25,float 0.25,float 0.25,float 0.25>
  %delta = fsub <4 x float> %m,%avg
'''
    c=f32(.05);main+=f'  %scaled = fmul <4 x float> %delta,<float {c},float {c},float {c},float {c}>\n'
    main+='  %sharp = fadd <4 x float> %m,%scaled\n'
    lastlo,lasthi='%m','%m'
    for neighbor in ('n','s','w','e'):
        main+=f'  %lo{neighbor} = call <4 x float> @llvm.minnum.v4f32(<4 x float> {lastlo},<4 x float> %{neighbor})\n'
        main+=f'  %hi{neighbor} = call <4 x float> @llvm.maxnum.v4f32(<4 x float> {lasthi},<4 x float> %{neighbor})\n'
        lastlo,lasthi=f'%lo{neighbor}',f'%hi{neighbor}'
    main+=f'''  %limitedlo = call <4 x float> @llvm.maxnum.v4f32(<4 x float> %sharp,<4 x float> {lastlo})
  %limited = call <4 x float> @llvm.minnum.v4f32(<4 x float> %limitedlo,<4 x float> {lasthi})
  %b = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15,i32 %x,i32 %y,<8 x i32> %base,i32 0,i32 0)
  %f = call <4 x float> @llvm.amdgcn.image.load.2d.v4f32.i32(i32 15,i32 %x,i32 %y,<8 x i32> %fine,i32 0,i32 0)
  %mask = extractelement <4 x float> %f,i32 3
  %static = fcmp oge float %mask,0.5
  %selected = select i1 %static,<4 x float> %b,<4 x float> %limited
  %alpha = extractelement <4 x float> %b,i32 3
  %result = insertelement <4 x float> %selected,float %alpha,i32 3
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %result,i32 15,i32 %x,i32 %y,<8 x i32> %outdesc,i32 0,i32 0)
  ret void
}}
attributes #0 = {{ "amdgpu-flat-work-group-size"="64,64" }}
!0 = !{{}}
'''
    return declarations+head+main


def generate(root,out):
    out.mkdir(parents=True,exist_ok=True)
    warp=(root/'classic/shaders/warp8.ll').read_text()
    # Only the isolated test build disables replacement on cuts, matching the PC experiment.
    warp=warp.replace('%cut = icmp ugt i32 %cutn, 3264','%cut = icmp ugt i32 0, 3264')
    (out/'warp8.ll').write_text(warp)
    (out/'gridrefine8.ll').write_text(refinement())
    (out/'gridwarp8.ll').write_text(fine_warp(warp))
    (out/'gridblend8.ll').write_text(blend((root/'classic/shaders/pcopy8.ll').read_text()))


if __name__=='__main__':generate(pathlib.Path(__file__).resolve().parents[2],pathlib.Path(sys.argv[1]))
