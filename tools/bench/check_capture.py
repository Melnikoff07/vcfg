"""Execute actual capture/PM4 builders as host bitcode with mocked console APIs.

Checks command capacity, original flip identity, unflipped scratch descriptors,
GPU completion gating and IO ownership. It does not execute GPU instructions.
"""
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
text = (ROOT / 'classic/source/main.c').read_text()
cfg = json.loads((ROOT / 'config.json').read_text())


def function(name):
    match = re.search(r'^static [^\n]+\b' + name + r'\(', text, re.M)
    start = match.start()
    pos = text.index('{', match.end())
    end, depth = pos + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def define(name):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r'#define ' + name + r'(?:\(|\s)', line):
            out = [line]
            while out[-1].endswith('\\'):
                i += 1
                out.append(lines[i])
            return '\n'.join(out)
    raise ValueError(name)


prefix = r'''
#include <stdint.h>
#include <stddef.h>
extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);
extern int strcmp(const char *, const char *);
extern int _snprintf(char *, size_t, const char *, ...);
extern int printf(const char *, ...);
#define snprintf _snprintf
typedef void FILE;
static unsigned writes;
static FILE *fopen(const char *p, const char *m) { return (FILE *)1; }
static int fscanf(FILE *f, const char *fmt, ...) { return 0; }
static size_t fwrite(const void *p, size_t a, size_t b, FILE *f) { writes++; return b; }
static int fflush(FILE *f) { return 0; }
static int fclose(FILE *f) { return 0; }
static int rename(const char *a, const char *b) { return 0; }
#define LOG_DIR "/unused"
#define VCFG_BENCH_SOURCE_HASH "0000000000000000000000000000000000000000000000000000000000000000"
#define BLOG(...) ((void)0)
typedef long off_t;
static void klog(const char *fmt, ...) {}
static int sceKernelOpen(const char *p, int f, int m) { return -1; }
static long sceKernelRead(int fd, void *b, size_t n) { return 0; }
static int sceKernelClose(int fd) { return 0; }
static size_t sceKernelGetDirectMemorySize(void) { return 0x100000000ull; }
static int sceKernelAllocateDirectMemory(long s, long e, size_t l, size_t a, int t, long *p) { *p = 0; return -1; }
static int sceKernelMapDirectMemory(void **a, size_t l, int p, int f, long ph, size_t al) { return -1; }
static int sceKernelMapFlexibleMemory(void **a, size_t l, int p, int f) { return -1; }
static int sceKernelAvailableDirectMemorySize(long s, long e, size_t a, long p, size_t z) { return 0; }
static int sceKernelAvailableFlexibleMemorySize(size_t z) { return 0; }
extern int sscanf(const char *, const char *, ...);
static uint64_t sceKernelGetProcessTimeCounter(void) { return 123; }
static int sceKernelMkdir(const char *p, int mode) { return 0; }
static void lg(const char *fmt, ...) {}
static void *alloc_dm(size_t n, size_t a, int t) { return NULL; }
static int file_flags(void) { return 0; }
static void cfg_reload_from_disk(int force) {}
static void io_start(void) {}
static void gpu_init(void) {}
static int dcb_mem_ok(uintptr_t p, size_t n) { return 1; }
static uint32_t *gv_emit_eof(uint32_t *p, uint32_t s, uint32_t k) { return p; }
static uint32_t *gv_emit_ts(uint32_t *p, uint32_t s, int i) { return p; }
static uint32_t *gv_emit_after_gv8(uint32_t *p, uint32_t s, uint32_t k) { return p; }
static void gv_fill_table(uint32_t *p, uint32_t s, uint32_t k) {}
static int gv_pass_active(void) { return 0; }
static int gv_warp_active(void) { return 0; }
static int gv_viz_active(void) { return 0; }
static uint32_t cut_blocks8(void) { return 0; }
static int post_mid_only(void) { return 1; }
static unsigned waits, overlays;
static int insert_wait_flip_done(uint32_t *p, uint32_t n, int32_t h, uint32_t i) { waits++; memset(p,0,28); return 0; }
static void osd_build_slot(uint8_t *p, uint32_t *x, uint32_t *w, uint32_t flags) { overlays++; *w=1; }
static uint8_t gpu_mem[0x20000+64], work_mem[0x220000], raw_mem[4u*1920u*1080u*8u+0x10000u];
static uint8_t surfaces[0x1000000];
static uint8_t *g_gpu=gpu_mem, *g_work=work_mem, *g_dump_mem=raw_mem;
static uint32_t mon[4], *g_mon=mon;
static uint32_t g_hud_packed, g_sh8_off[17], g_dump_frame, g_flips;
static uint64_t g_pyr_last_cur_base;
static uint32_t g_pyr_last_w, g_pyr_last_h, g_pyr_parity;
static int g_dump_req=1, g_fence_logged, g_fg_off=1, g_mem_tight=0;
static int g_opt_phase, g_opt_marker, g_opt_nocf, g_opt_nofgk, g_opt_noanc, g_opt_notp, g_opt_nomf;
static int g_opt_notex, g_opt_notpg, g_opt_sharp, g_opt_nostr;
static int g_bench_enabled=1, g_bench_offscreen, g_gpu_state=1, g_sh8_ok=1;
static uint32_t g_bench_pack_off=0x17000, g_bw=1920,g_bh=1080,g_pitch=1920,g_ema_frame_us=16683;
static int g_fmt_supported=1,g_handle=1,g_ngame=3,g_inject_on;
static uint32_t g_osd_real_fps_x10=600;
static uint64_t g_buf[16]={0x210000000ull,0x210800000ull,0x211000000ull};
static char g_title_id[16]="CUSA00557";
static unsigned submissions,max_bytes,submit_error,expected_index;
static int fail_submit;
static int test_submit(uint32_t count, void **d, uint32_t *z, void **c, uint32_t *cz,
                       uint32_t h,uint32_t i,uint32_t m,int64_t a) {
    submissions++;
    if(h!=1 || i!=expected_index || m!=1 || a!=77) submit_error=1;
    uint32_t *p=d[count-1];
    if ((uintptr_t)p>=(uintptr_t)raw_mem && (uintptr_t)p<(uintptr_t)raw_mem+sizeof(raw_mem)) {
        unsigned bytes=z[count-1], tails=0;
        if(bytes>max_bytes)max_bytes=bytes;
        if(bytes>8192)submit_error=2;
        for(unsigned k=0;k+1<bytes/4;k++)if(p[k]==0xC03E1000u && p[k+1]==0x68750777u)tails++;
        if(tails!=1)submit_error=3;
    }
    return fail_submit;
}
#define HOOK_CONTINUE(name, type, ...) test_submit(__VA_ARGS__)
'''
struct = re.search(r'typedef struct \{.*?\} fg_config_t;', text, re.S)[0]
attr = re.search(r'typedef struct \{\s*uint32_t format,.*?\} VoAttr;', text, re.S)[0]
pieces = [prefix, struct, attr, 'static fg_config_t g_cfg; static VoAttr g_attr;',
          '#include "shaders.h"', '#include "bench_shaders.h"', '#include "fg_res.h"', '#include "fg8_table.h"',
          'const uint8_t *vov_pal_ptr(void) {return NULL;} const uint8_t *vov_pix_ptr(void){return NULL;}']
pieces += [define(n) for n in ['SLOT0', 'SLOT_SZ', 'STATS_OFF', 'WORK_SZ', 'DUMP_DONE_OFF', 'DCB8_OFF', 'DCB8_SZ',
                              'PKT3', 'TS_EOP', 'TS2', 'PASS_BARRIER', 'SET_PASS']]
pieces += [function('calc_rcas_params'), function('build_dcb_flow8'), '#include "bench_capture.h"']
pieces += [r'''
static uint32_t original[128];
static void reset(unsigned width,unsigned sharp,unsigned enhance,unsigned bits) {
    memset(gpu_mem,0,sizeof(gpu_mem)); memset(&g_cfg,0,sizeof(g_cfg));
    g_cfg.sharpness=sharp;g_cfg.upscale=enhance;g_cfg.hud_protect=1;
    g_bench_profile=g_cfg;g_attr.format=bits?0x88000000u:0x80000000u;g_attr.tilingMode=0;
    g_t_dfmt=bits?9:10;
    g_bench_state=BENCH_IDLE;g_bench_started=1;g_bench_saved=0;g_bench_limit=1;g_bench_next=240;
    g_bench_attempt=0;g_bench_cookie=0;g_bench_width=width;g_bench_plane=(size_t)width*1080*8;
    g_bench_surfaces=surfaces;g_bench_tables=(uint32_t *)(g_dump_mem+4*g_bench_plane);
    submissions=submit_error=overlays=waits=writes=0;fail_submit=0;
    for(unsigned i=0;i<17;i++)g_sh8_off[i]=0x8000+i*256;
    memset(original,0,sizeof(original));original[64]=0xC03E1000u;original[65]=0x68750777u;
}
static int frame(unsigned n,uint64_t us) {
    void *d[1]={original};uint32_t z[1]={sizeof(original)};
    g_flips=n;expected_index=n%3;
    unsigned stage=g_bench_state==BENCH_IDLE?0:g_bench_state==BENCH_GT?1:2;
    uint32_t *guard=(uint32_t *)((uint8_t *)g_bench_tables+BENCH_DCB_OFF+(stage+1)*BENCH_DCB_BYTES);
    *guard=0x9abcdef0;
    int r=bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,us);
    if(*guard!=0x9abcdef0)return -99;
    return r;
}
int main(void) {
    for(unsigned width=960;width<=1920;width+=960)
    for(unsigned sharp=0;sharp<=100;sharp+=100)
    for(unsigned enhance=0;enhance<=2;enhance++)
    for(unsigned bits=0;bits<2;bits++) {
        reset(width,sharp,enhance,bits);
        if(frame(240,100000) || g_bench_state!=BENCH_GT)return 1;
        if(frame(241,116683) || g_bench_state!=BENCH_B)return 2;
        if(frame(242,133366) || g_bench_state!=BENCH_WAIT)return 3;
        if(waits || overlays || submit_error || submissions!=3)return 4;
        uint32_t *tb=(uint32_t *)(g_gpu+SLOT0+2*SLOT_SZ+0xB00);
        if(tb[137]&2u || (tb[30]&2u) || (tb[29]>>16)!=32768)return 5;
        if(tb[16]!=(uint32_t)(((uint64_t)(uintptr_t)surfaces+BENCH_SURFACE)>>8))return 6;
        bench_io_tick();if(writes || g_bench_state!=BENCH_WAIT)return 7;
        *(uint64_t *)(g_gpu+DUMP_DONE_OFF)=g_bench_cookie;
        bench_io_tick();if(writes!=5 || g_bench_state!=BENCH_DONE)return 8;
    }
    reset(960,40,1,1);
    if(frame(240,100000)||frame(241,126683)||frame(242,143366))return 9;
    *(uint64_t *)(g_gpu+DUMP_DONE_OFF)=g_bench_cookie;
    bench_io_tick();if(writes || g_bench_state!=BENCH_IDLE)return 10;
    reset(960,40,1,1);fail_submit=-1;
    if(frame(240,100000)!=-1 || g_bench_state!=BENCH_STOP)return 11;
    reset(960,40,1,1);g_bench_state=BENCH_WRITE;
    if(frame(240,100000) || writes || g_bench_state!=BENCH_WRITE)return 12;
    reset(960,40,1,1);frame(240,100000);frame(241,116683);frame(242,133366);
    if(frame(841,10000000) || g_bench_state!=BENCH_STOP)return 13;
    memcpy(g_title_id,"CUSA03041",10);g_ema_frame_us=33366;g_osd_real_fps_x10=300;
    reset(1920,40,1,0);
    if(frame(240,100000) || g_bench_state!=BENCH_GT)return 14;
    if(frame(241,133366) || g_bench_state!=BENCH_B)return 15;
    if(frame(242,166732) || g_bench_state!=BENCH_WAIT)return 16;
    *(uint64_t *)(g_gpu+DUMP_DONE_OFF)=g_bench_cookie;
    bench_io_tick();if(writes!=5 || g_bench_state!=BENCH_DONE)return 17;
    printf("capture host checks passed (60Hz + 30Hz); max appended DCB %u / 8192 bytes\n",max_bytes);
    return 0;
}
''']
out = ROOT / 'build/verification'
source = out / 'capture_host.c'
source.write_text('\n'.join(pieces), encoding='utf-8')
llvm = pathlib.Path(cfg['llvm_bin'])
bitcode = out / 'capture_host.bc'
subprocess.run([str(llvm / 'clang.exe'), '--target=x86_64-pc-windows-msvc', '-O0', '-fno-builtin',
                '-I' + str(ROOT / 'classic/source'), '-I' + str(ROOT / 'overlay'), '-I' + str(ROOT / 'build/classic-bench'),
                '-emit-llvm', '-c', str(source), '-o', str(bitcode)], check=True)
subprocess.run([str(llvm / 'lli.exe'), '--dlopen=msvcrt.dll', str(bitcode)], check=True)
