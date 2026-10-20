"""Build an isolated CUSA00557-only experimental plugin in build/gridmix/.

Production C/shaders and generated headers are never overwritten. Requires
/data/bb_probe/gridmix_CUSA00557 at process load; other titles remain inert.
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from gen_gridmix_shaders import generate


def replace_once(text,old,new):
    if text.count(old)!=1:raise ValueError('source anchor changed: '+old[:85])
    return text.replace(old,new,1)


def stage_main(text):
    text=replace_once(text,'static int g_sh8_ok = 0;', '''static int g_sh8_ok = 0;
static uint32_t g_grid_off[3];
static uint8_t *g_grid_tmp;
static int g_grid_ready;
_Static_assert(0x18C000u + 240u * 135u * 8u <= WORK_SZ, "fine field exceeds spare work memory");''')
    text=text.replace('#define GPU_SZ 0x20000u','#define GPU_SZ 0x40000u').replace('#define GPU_SZ_BASE 0x20000u','#define GPU_SZ_BASE 0x40000u')
    text=text.replace('#define DCB8_OFF 0x1C000u','#define DCB8_OFF 0x38000u').replace('#define DCB8_SZ  0x1000u','#define DCB8_SZ  0x2000u')
    anchor='''#if VCFG_BENCH
        if (g_bench_enabled && g_sh8_ok) {'''
    text=replace_once(text,anchor,'''        if (g_sh8_ok) {
            const uint32_t *extra[3] = { gridrefine8_code, gridwarp8_code, gridblend8_code };
            const uint32_t words[3] = { GRIDREFINE8_NWORDS, GRIDWARP8_NWORDS, GRIDBLEND8_NWORDS };
            for (int gi = 0; gi < 3; gi++) {
                if (off + words[gi] * 4 > DCB8_OFF) { g_sh8_ok = 0; break; }
                memcpy((uint8_t *)addr + off, extra[gi], words[gi] * 4);
                g_grid_off[gi] = off;
                off = (off + words[gi] * 4 + 0xFFu) & ~0xFFu;
            }
        }
'''+anchor)
    text=replace_once(text,'    g_gpu = (uint8_t *)addr;\n    g_gpu_state = 1;', '''    g_gpu = (uint8_t *)addr;
    if (g_work && g_sh8_ok) {
        void *scratch = NULL;
        // Native tiled display surfaces require the same 2 MiB base alignment
        // as the established direct-memory mid buffers. Flexible mappings only
        // guarantee page alignment: retain headroom and align inside the mapping.
        int gr = sceKernelMapFlexibleMemory(&scratch, 0x1200000u, 0x33, 0);
        if (gr == 0 && scratch) {
            memset(scratch, 0, 0x1200000u);
            uintptr_t aligned = ((uintptr_t)scratch + 0x1FFFFFu) & ~(uintptr_t)0x1FFFFFu;
            g_grid_tmp = (uint8_t *)aligned;
            g_grid_ready = 1;
        }
        lg("GRIDMIX experimental: kernels=%d mapping=%p aligned=%p result=0x%08x; profile requires Quality/100/AA-off\\n", g_sh8_ok, scratch, (void *)g_grid_tmp, (unsigned)gr);
    }
    g_gpu_state = 1;''')
    text=replace_once(text,'    uint32_t mid1_packed = (mid1_idx & 0x7FFFu)', '''    int grid_on = g_grid_ready && !two_mids && !g_bench_offscreen &&
                  g_cfg.preset == 0 && g_cfg.sharpness == 100 && g_cfg.upscale == 0 &&
                  !gv_pass_active() && !gv_warp_active();
    static int grid_last = -1;
    if (grid_last != grid_on) {
        grid_last = grid_on;
        lg("GRIDMIX active=%d preset=%d sharpness=%d enhance=%d scratch=%p\\n", grid_on,
           g_cfg.preset, g_cfg.sharpness, g_cfg.upscale, (void *)g_grid_tmp);
    }
    uint32_t mid1_packed = (mid1_idx & 0x7FFFu)''')
    anchor='    gv_fill_table(tb, seq, slot);'
    text=replace_once(text,anchor,anchor+'''
    if (grid_on) {
        make_v(tb + 140, (uint64_t)(uintptr_t)(g_work + 0x18C000u), 240u * 135u * 8u);
        make_t(tb + 144, (uint64_t)(uintptr_t)g_grid_tmp, 1920, 1080, pitch, tidx);
        make_t(tb + 152, (uint64_t)(uintptr_t)(g_grid_tmp + 0x800000u), 1920, 1080, pitch, tidx);
    }''')
    anchor='''    SET_PASS(g_sh8_off[9], MF8_RSRC1, MF8_RSRC2);     DISPATCH(30, 68);
    PASS_BARRIER();'''
    text=replace_once(text,anchor,anchor+'''
    if (grid_on) {
        SET_PASS(g_grid_off[0], GRIDREFINE8_RSRC1, GRIDREFINE8_RSRC2); DISPATCH(507, 1);
        PASS_BARRIER();
    }''')
    anchor='''    DISPATCH(240, 135);
    PASS_BARRIER(); TS2(5);'''
    text=replace_once(text,anchor,'''    DISPATCH(240, 135);
    PASS_BARRIER();
    if (grid_on) {
        SET_PASS(g_grid_off[1], GRIDWARP8_RSRC1, GRIDWARP8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
    }
    TS2(5);''')
    anchor='''    if (gv_viz_active()) {
        PASS_BARRIER();'''
    extra='''    if (grid_on) {
        uint32_t *gtb = (uint32_t *)(slotb + SLOT_TB2_OFF);
        uint64_t gtbl = (uint64_t)(uintptr_t)gtb;
        memcpy(gtb, tb, 160 * sizeof(uint32_t));
        memcpy(gtb + 16, tb + 144, 8 * sizeof(uint32_t)); // fine T#out
        gtb[137] = (rcas_flags1 & ~3u) | 1u;            // fine mid only; cur already processed once
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)gtbl; *p++ = (uint32_t)(gtbl >> 32);
        SET_PASS(g_sh8_off[14], RCAS8_RSRC1, RCAS8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);
        SET_PASS(g_grid_off[2], GRIDBLEND8_RSRC1, GRIDBLEND8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        memcpy(gtb + 8, tb + 152, 8 * sizeof(uint32_t)); // finished T#cur -> T#out M
        memcpy(gtb + 16, tb + 16, 8 * sizeof(uint32_t));
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)gtbl; *p++ = (uint32_t)(gtbl >> 32);
        SET_PASS(g_sh8_off[16], PCOPY8_RSRC1, PCOPY8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);
    }
'''
    # Separate immutable tables are essential: commands execute after CPU construction.
    # The copy uses a third unused table in slotb + 0x680, fitting below the existing VS at 0x800.
    # Only 96 bytes are needed for its current/output descriptors.
    extra=extra.replace('        memcpy(gtb + 8, tb + 152, 8 * sizeof(uint32_t));',
                        '        uint32_t *ctb = (uint32_t *)(slotb + 0x680u);\n        uint64_t ctbl = (uint64_t)(uintptr_t)ctb;\n        memcpy(ctb + 8, tb + 152, 8 * sizeof(uint32_t));')
    extra=extra.replace('        memcpy(gtb + 16, tb + 16, 8 * sizeof(uint32_t));','        memcpy(ctb + 16, tb + 16, 8 * sizeof(uint32_t));')
    # Replace only the userdata write immediately before copy.
    extra=extra.replace('''*p++ = (uint32_t)gtbl; *p++ = (uint32_t)(gtbl >> 32);
        SET_PASS(g_sh8_off[16]''','''*p++ = (uint32_t)ctbl; *p++ = (uint32_t)(ctbl >> 32);
        SET_PASS(g_sh8_off[16]''')
    text=replace_once(text,anchor,extra+anchor)
    text=replace_once(text,'    if (!g_is_game_proc) {\n        klog(','''    if (strcmp(g_title_id, "CUSA00557") != 0) g_is_game_proc = 0;
    if (!g_is_game_proc) {
        klog(''')
    anchor='    BLOG("plugin_load enter title=%s", g_title_id);'
    text=replace_once(text,anchor,'''    int grid_marker = sceKernelOpen(LOG_DIR "/gridmix_CUSA00557", 0, 0);
    if (grid_marker < 0) {
        g_is_game_proc = 0;
        klog("[bb_probe] GRIDMIX inert: CUSA00557 marker absent\\n");
        return 0;
    }
    sceKernelClose(grid_marker);
    klog("[bb_probe] GRIDMIX experimental build: CUSA00557 only, no cut replacement\\n");
'''+anchor)
    text=replace_once(text,'if (m == 8 && !g_bench_enabled) gv_load(alloc_dm);',
                      'if (0) gv_load(alloc_dm); // experimental test uses optical motion only')
    return text


def main():
    root=pathlib.Path(__file__).resolve().parents[2]
    cfg=json.loads((root/'config.json').read_text())
    out=root/'build/gridmix';source=out/'source';shaders=out/'shaders'
    source.mkdir(parents=True,exist_ok=True);shaders.mkdir(parents=True,exist_ok=True)
    for p in (root/'classic/source').iterdir():
        if p.is_file():shutil.copy2(p,source/p.name)
    for p in (root/'classic/shaders').glob('*.ll'):shutil.copy2(p,shaders/p.name)
    generate(root,shaders)
    # Reuse the strict raw-code validator; generated files stay inside the staging tree.
    subprocess.run([cfg['python'],'-I',str(root/'classic/gen_shaders.py'),cfg['llc_gfx700'],str(shaders),str(source/'shaders.h')],check=True)
    original=(root/'classic/source/main.c').read_text()
    (source/'main.c').write_text(stage_main(original))
    (source/'git_ver.h').write_text('#define GIT_COMMIT "gridmix-exp"\n#define GIT_VER "gridmix-exp"\n#define GIT_NUM 0\n#define BUILD_DATE "2026-10-20"\n')
    tc,sdk,ll=cfg['openorbis_toolchain'],cfg['goldhen_sdk'],cfg['llvm_bin']
    flags=['--target=x86_64-pc-freebsd12-elf','-fPIC','-funwind-tables','-isysroot',tc,'-isystem',tc+'/include',
           '-I'+sdk+'/include','-I'+str(source),'-I'+str(root/'overlay'),'-Wno-c99-designator',
           '-D_BSD_SOURCE=1','-D__BSD_VISIBLE=1','-D__PS4__=1','-DOO=1','-D__OPENORBIS__=1',
           '-D__OOPS4__=1','-D__FINAL__=1','-D__USE_PRINTF__','-DVCFG_VER=2.16',
           '-DVCFG_2BUF_TEST=0','-DVCFG_BENCH=0']
    objects=[]
    for p in list(source.glob('*.c'))+[root/'overlay/vov.c']:
        obj=out/(p.stem+'.o')
        subprocess.run([ll+'/clang.exe',*flags,'-c',str(p),'-o',str(obj)],check=True)
        objects.append(str(obj))
    elf=out/'vcfg_gridmix.elf';prx=out/'vcfg_gridmix.prx'
    subprocess.run([ll+'/ld.lld.exe',sdk+'/build/crtprx.o',*objects,'-o',str(elf),'-m','elf_x86_64',
                    '-pie','-e','_init','--script',tc+'/link.x','--eh-frame-hdr','-L'+tc+'/lib','-L'+sdk,
                    '-lSceLibcInternal','-lSceGnmDriver','-lSceVideoOut','-lScePad','-lGoldHEN_Hook','-lkernel',
                    '-lSceSysmodule','-lSceSystemService','-lSceUserService'],check=True)
    subprocess.run([tc+'/bin/windows/create-fself.exe','-in='+str(elf),'-out='+str(out/'vcfg_gridmix.oelf'),
                    '-lib='+str(prx),'--paid','0x3800000000000011'],check=True,
                   env=dict(os.environ,OO_PS4_TOOLCHAIN=tc,DOTNET_ROLL_FORWARD='Major'))
    print(json.dumps(dict(plugin=str(prx),bytes=prx.stat().st_size,sha256=hashlib.sha256(prx.read_bytes()).hexdigest()),indent=2))


if __name__=='__main__':main()
