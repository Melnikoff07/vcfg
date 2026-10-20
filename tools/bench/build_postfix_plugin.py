"""Isolated CUSA00557 raw-real / immutable-source mid-post experiment.

Uses one aligned 8 MiB scratch surface. Never post-processes real game buffers.
No 8x8 refinement or temporal color history is included in this diagnostic build.
"""
import hashlib
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from build_gridmix_plugin import replace_once


def post_shader(src):
    src=src.replace('; Fixes the in-place neighbour race condition of the previous rcas8 by pre-loading a 12x12 tile (144 texels:',
                    '; Out-of-place variant: immutable source plus separate destination. LDS tile uses 144 texels:')
    head=src[:src.index('define amdgpu_cs void @main')]
    return head+'''define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0 {
entry:
  %lx = and i32 %tid, 7
  %ly = lshr i32 %tid, 3
  %bxp = shl i32 %gx, 3
  %byp = shl i32 %gy, 3
  %x = add i32 %bxp, %lx
  %y = add i32 %byp, %ly
  %pcfg = getelementptr i8, ptr addrspace(4) %tbl, i64 544
  %cfg = load <4 x i32>, ptr addrspace(4) %pcfg, align 16
  %conb = extractelement <4 x i32> %cfg, i32 0
  %con = bitcast i32 %conb to float
  %flags = extractelement <4 x i32> %cfg, i32 1
  %em0 = lshr i32 %flags, 2
  %emode = and i32 %em0, 3
  %psrc = getelementptr i8, ptr addrspace(4) %tbl, i64 64
  %src = load <8 x i32>, ptr addrspace(4) %psrc, align 32
  %pdst = getelementptr i8, ptr addrspace(4) %tbl, i64 576
  %dst = load <8 x i32>, ptr addrspace(4) %pdst, align 32
  %color = call <4 x float> @eval_tile(<8 x i32> %src, i32 %tid, i32 %bxp, i32 %byp, i32 %lx, i32 %ly, float %con, i32 %emode)
  call void @llvm.amdgcn.image.store.2d.v4f32.i32(<4 x float> %color, i32 15, i32 %x, i32 %y, <8 x i32> %dst, i32 0, i32 0)
  ret void
}
attributes #0 = { "amdgpu-flat-work-group-size"="64,64" }
!0 = !{}
'''


def stage_main(text):
    text=replace_once(text,'static int g_sh8_ok = 0;', '''static int g_sh8_ok = 0;
static uint32_t g_postfix_off;
static uint8_t *g_postfix_tmp;
static int g_postfix_ready;''')
    for old,new in [('#define GPU_SZ 0x20000u','#define GPU_SZ 0x40000u'),
                    ('#define GPU_SZ_BASE 0x20000u','#define GPU_SZ_BASE 0x40000u'),
                    ('#define DCB8_OFF 0x1C000u','#define DCB8_OFF 0x38000u'),
                    ('#define DCB8_SZ  0x1000u','#define DCB8_SZ  0x2000u')]:
        text=replace_once(text,old,new)
    anchor='''#if VCFG_BENCH
        if (g_bench_enabled && g_sh8_ok) {'''
    text=replace_once(text,anchor,'''        if (g_sh8_ok) {
            if (off + sizeof(postout8_code) > DCB8_OFF) g_sh8_ok = 0;
            else {
                memcpy((uint8_t *)addr + off, postout8_code, sizeof(postout8_code));
                g_postfix_off = off;
                off = (off + sizeof(postout8_code) + 0xFFu) & ~0xFFu;
            }
        }
'''+anchor)
    text=replace_once(text,'    g_gpu = (uint8_t *)addr;\n    g_gpu_state = 1;', '''    g_gpu = (uint8_t *)addr;
    if (g_work && g_sh8_ok && g_postfix_off) {
        void *mapping = NULL;
        int r = sceKernelMapFlexibleMemory(&mapping, 0xA00000u, 0x33, 0);
        if (r == 0 && mapping) {
            memset(mapping, 0, 0xA00000u);
            uintptr_t aligned = ((uintptr_t)mapping + 0x1FFFFFu) & ~(uintptr_t)0x1FFFFFu;
            g_postfix_tmp = (uint8_t *)aligned;
            g_postfix_ready = 1;
        }
        lg("POSTFIX allocation mapping=%p aligned=%p result=0x%08x ready=%d\\n", mapping, (void *)g_postfix_tmp, (unsigned)r, g_postfix_ready);
    }
    g_gpu_state = 1;''')
    # Native mode must not seed the next interpolation with a processed real.
    text=replace_once(text,'    calc_rcas_params(2u /*cur only*/, &rcas_con_bits, &rcas_flags);',
                      '    return 0; // POSTFIX: no real-buffer post or overlay writes, including native startup\n    rcas_con_bits = rcas_flags = 0;')
    text=replace_once(text,'    uint32_t mask1 = two_mids ? 1u /*mid1 only; mid2+cur done in pass 2*/ : 3u /*mid1 + cur*/;',
                      '    uint32_t mask1 = 1u; // POSTFIX: overlays only on generated surfaces')
    text=replace_once(text,'(osd_wh1 & 0x00FFFFFFu) | (3u << 24)',
                      '(osd_wh1 & 0x00FFFFFFu) | (1u << 24)')
    text=replace_once(text,'    uint32_t mid1_packed = (mid1_idx & 0x7FFFu)', '''    int postfix_on = g_postfix_ready && !two_mids && !g_bench_offscreen &&
                     g_cfg.preset == 0 && g_cfg.sharpness == 100 && g_cfg.upscale == 0 &&
                     g_cfg.overlay == 0 && !gv_pass_active() && !gv_warp_active();
    static int postfix_last = -1;
    if (postfix_last != postfix_on) {
        postfix_last = postfix_on;
        lg("POSTFIX active=%d preset=%d sharpness=%d enhance=%d overlay=%d raw-real=1\\n", postfix_on,
           g_cfg.preset, g_cfg.sharpness, g_cfg.upscale, g_cfg.overlay);
    }
    uint32_t mid1_packed = (mid1_idx & 0x7FFFu)''')
    # There is no legacy in-place real post even if the experimental gate is off.
    # Unsupported profile/allocation failure disables post, and logs active=0.
    text=replace_once(text,'    calc_rcas_params(mask1, &rcas_con_bits, &rcas_flags1);',
                      '    if (postfix_on) calc_rcas_params(1u /*mid only*/, &rcas_con_bits, &rcas_flags1);')
    text=replace_once(text,'    calc_rcas_params(3u /*mid2 + cur*/, &rcas_con2, &rcas_flags2);',
                      '    rcas_con2 = rcas_flags2 = 0; // unsupported two-mid mode: no in-place post')
    anchor='    gv_fill_table(tb, seq, slot);'
    text=replace_once(text,anchor,anchor+'''
    if (postfix_on) make_t(tb + 144, (uint64_t)(uintptr_t)g_postfix_tmp, 1920, 1080, pitch, tidx);''')
    old='''    if (rcas_flags1) {
        SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
    }'''
    text=replace_once(text,old,'''    if (postfix_on && rcas_flags1) {
        // Read complete immutable M1, write only scratch. Dispatch barrier then copy back.
        SET_PASS(g_postfix_off, POSTOUT8_RSRC1, POSTOUT8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        uint32_t *ctb = (uint32_t *)(slotb + SLOT_TB2_OFF);
        uint64_t ctbl = (uint64_t)(uintptr_t)ctb;
        memset(ctb, 0, 24u * sizeof(uint32_t));
        memcpy(ctb + 8, tb + 144, 8u * sizeof(uint32_t));
        memcpy(ctb + 16, tb + 16, 8u * sizeof(uint32_t));
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)ctbl; *p++ = (uint32_t)(ctbl >> 32);
        SET_PASS(g_sh8_off[16], PCOPY8_RSRC1, PCOPY8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);
    }''')
    text=replace_once(text,'    if (!g_is_game_proc) {\n        klog(','''    if (strcmp(g_title_id, "CUSA00557") != 0) g_is_game_proc = 0;
    if (!g_is_game_proc) {
        klog(''')
    anchor='    BLOG("plugin_load enter title=%s", g_title_id);'
    text=replace_once(text,anchor,'''    int marker = sceKernelOpen(LOG_DIR "/postfix_CUSA00557", 0, 0);
    if (marker < 0) { g_is_game_proc = 0; klog("[bb_probe] POSTFIX inert: marker absent\\n"); return 0; }
    sceKernelClose(marker);
    klog("[bb_probe] POSTFIX experimental: CUSA00557 only, raw real + out-of-place mid, no cut replacement\\n");
'''+anchor)
    text=replace_once(text,'if (m == 8 && !g_bench_enabled) gv_load(alloc_dm);','if (0) gv_load(alloc_dm); // optical-only isolation')
    return text


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--split',action='store_true',help='AA1 real and mid, with separate raw color history')
    ap.add_argument('--title',choices=['CUSA00557','CUSA08519'],default='CUSA00557')
    args=ap.parse_args()
    root=pathlib.Path(__file__).resolve().parents[2];cfg=json.loads((root/'config.json').read_text())
    variant='postfix_split' if args.split else 'postfix'
    out=root/'build'/(variant if args.title=='CUSA00557' else variant+'_'+args.title);source=out/'source';shaders=out/'shaders'
    source.mkdir(parents=True,exist_ok=True);shaders.mkdir(parents=True,exist_ok=True)
    for p in (root/'classic/source').iterdir():
        if p.is_file():shutil.copy2(p,source/p.name)
    for p in (root/'classic/shaders').glob('*.ll'):shutil.copy2(p,shaders/p.name)
    (shaders/'postout8.ll').write_text(post_shader((root/'classic/shaders/rcas8.ll').read_text()))
    warp=(shaders/'warp8.ll').read_text()
    warp=replace_once(warp,'  %cut = icmp ugt i32 %cutn, 3264','  %cut = icmp ugt i32 0, 3264')
    (shaders/'warp8.ll').write_text(warp)
    subprocess.run([cfg['python'],'-I',str(root/'classic/gen_shaders.py'),cfg['llc_gfx700'],str(shaders),str(source/'shaders.h')],check=True)
    staged=stage_main((root/'classic/source/main.c').read_text())
    if args.split:
        from build_postfix_split import stage_split
        staged=stage_split(staged)
    if args.title != 'CUSA00557':
        staged=replace_once(staged,'if (strcmp(g_title_id, "CUSA00557") != 0) g_is_game_proc = 0;',
                            'if (strcmp(g_title_id, "'+args.title+'") != 0) g_is_game_proc = 0;')
        marker='postfix_split' if args.split else 'postfix'
        staged=replace_once(staged,'LOG_DIR "/'+marker+'_CUSA00557"','LOG_DIR "/'+marker+'_'+args.title+'"')
        staged=replace_once(staged,'experimental: CUSA00557 only','experimental: '+args.title+' only')
    (source/'main.c').write_text(staged)
    (source/'git_ver.h').write_text('#define GIT_COMMIT "postfix-exp"\n#define GIT_VER "postfix-exp"\n#define GIT_NUM 0\n#define BUILD_DATE "2026-10-20"\n')
    tc,sdk,ll=cfg['openorbis_toolchain'],cfg['goldhen_sdk'],cfg['llvm_bin']
    flags=['--target=x86_64-pc-freebsd12-elf','-fPIC','-funwind-tables','-isysroot',tc,'-isystem',tc+'/include',
           '-I'+sdk+'/include','-I'+str(source),'-I'+str(root/'overlay'),'-Wno-c99-designator',
           '-D_BSD_SOURCE=1','-D__BSD_VISIBLE=1','-D__PS4__=1','-DOO=1','-D__OPENORBIS__=1','-D__OOPS4__=1',
           '-D__FINAL__=1','-D__USE_PRINTF__','-DVCFG_VER=2.16','-DVCFG_2BUF_TEST=0','-DVCFG_BENCH=0']
    objects=[]
    for p in list(source.glob('*.c'))+[root/'overlay/vov.c']:
        obj=out/(p.stem+'.o');subprocess.run([ll+'/clang.exe',*flags,'-c',str(p),'-o',str(obj)],check=True);objects.append(str(obj))
    elf=out/('vcfg_'+variant+'.elf');prx=out/('vcfg_'+variant+'.prx')
    subprocess.run([ll+'/ld.lld.exe',sdk+'/build/crtprx.o',*objects,'-o',str(elf),'-m','elf_x86_64','-pie','-e','_init',
                    '--script',tc+'/link.x','--eh-frame-hdr','-L'+tc+'/lib','-L'+sdk,'-lSceLibcInternal','-lSceGnmDriver',
                    '-lSceVideoOut','-lScePad','-lGoldHEN_Hook','-lkernel','-lSceSysmodule','-lSceSystemService','-lSceUserService'],check=True)
    subprocess.run([tc+'/bin/windows/create-fself.exe','-in='+str(elf),'-out='+str(out/('vcfg_'+variant+'.oelf')),'-lib='+str(prx),
                    '--paid','0x3800000000000011'],check=True,env=dict(os.environ,OO_PS4_TOOLCHAIN=tc,DOTNET_ROLL_FORWARD='Major'))
    print(json.dumps(dict(plugin=str(prx),bytes=prx.stat().st_size,sha256=hashlib.sha256(prx.read_bytes()).hexdigest()),indent=2))


if __name__=='__main__':main()
