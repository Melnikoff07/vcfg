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
extern char *strstr(const char *, const char *);
#ifdef _WIN32
extern int _snprintf(char *, size_t, const char *, ...);
#define snprintf _snprintf
#else
extern int snprintf(char *, size_t, const char *, ...);
#endif
extern int printf(const char *, ...);
typedef void FILE;
static unsigned writes;
static const void *write_ptrs[256];
static size_t write_sizes[256];
static char write_bufs[256][2048];
static FILE *fopen(const char *p, const char *m) { return (FILE *)1; }
static int fscanf(FILE *f, const char *fmt, ...) { return 0; }
static size_t fwrite(const void *p, size_t a, size_t b, FILE *f) {
    size_t sz = a * b;
    if (writes < 256) {
        write_ptrs[writes] = p;
        write_sizes[writes] = sz;
        memset(write_bufs[writes], 0, sizeof(write_bufs[writes]));
        if (sz < sizeof(write_bufs[writes])) memcpy(write_bufs[writes], p, sz);
    }
    writes++;
    return b;
}
static int fflush(FILE *f) { return 0; }
static int fclose(FILE *f) { return 0; }
static int rename(const char *a, const char *b) { return 0; }
#define LOG_DIR "/unused"
#define VCFG_BENCH_SOURCE_HASH "0000000000000000000000000000000000000000000000000000000000000000"
#define BLOG(...) ((void)0)
typedef long off_t;
static void klog(const char *fmt, ...) {}
static const char *mock_capture_cfg;
static int mock_bench_go_present;
static int sceKernelOpen(const char *p, int f, int m) {
    if (mock_capture_cfg && !strcmp(p, LOG_DIR "/bench_capture")) return 10;
    if (mock_bench_go_present && !strcmp(p, LOG_DIR "/bench_go")) return 11;
    return -1;
}
static long sceKernelRead(int fd, void *b, size_t n) {
    if (fd == 10 && mock_capture_cfg) {
        size_t l = 0;
        while (mock_capture_cfg[l] && l < n) { ((char *)b)[l] = mock_capture_cfg[l]; l++; }
        return (long)l;
    }
    return 0;
}
static int sceKernelClose(int fd) { return 0; }
static size_t sceKernelGetDirectMemorySize(void) { return 0x100000000ull; }
static int mock_dm_fail, mock_dm_surfaces_ok_only;
static unsigned flex_calls, dm_alloc_calls;
static size_t last_dm_dump_bytes;
static uint8_t gpu_mem[0x20000+64], work_mem[0x220000], raw_mem[4u*1920u*1080u*8u+0x10000u];
static uint8_t surfaces[0x1000000];
static int sceKernelAllocateDirectMemory(long s, long e, size_t l, size_t a, int t, long *p) {
    dm_alloc_calls++;
    if (mock_dm_fail) return -1;
    if (mock_dm_surfaces_ok_only && a != 0x200000) return -1;
    if (a != 0x200000 && l > sizeof(raw_mem)) return -1;
    *p = (a == 0x200000) ? 0x1000000L : 0x2000000L;
    return 0;
}
static int sceKernelMapDirectMemory(void **a, size_t l, int p, int f, long ph, size_t al) {
    if (mock_dm_fail) return -1;
    if (al == 0x200000) { *a = surfaces; return 0; }
    if (mock_dm_surfaces_ok_only || l > sizeof(raw_mem)) return -1;
    last_dm_dump_bytes = l;
    *a = raw_mem;
    return 0;
}
static int sceKernelMapFlexibleMemory(void **a, size_t l, int p, int f) {
    flex_calls++;
    if (l > sizeof(raw_mem)) return -1;
    *a = (l == 0x1000000u) ? surfaces : raw_mem;
    return 0;
}
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
#ifndef BENCH_SEQ_STEP_BYTES
#define BENCH_SEQ_STEP_BYTES 0x10000u
#endif
#ifndef BENCH_SEQ_ANCHOR_PLANES
#define BENCH_SEQ_ANCHOR_PLANES 6u
#endif
#ifndef BENCH_SEQ_MAX_BYTES
#define BENCH_SEQ_MAX_BYTES 0x1000000u
#endif
static uint32_t original[128];
static void reset(unsigned width,unsigned sharp,unsigned enhance,unsigned bits) {
    memset(gpu_mem,0,sizeof(gpu_mem)); memset(&g_cfg,0,sizeof(g_cfg));
    g_cfg.mode=8;g_cfg.sharpness=sharp;g_cfg.upscale=enhance;g_cfg.hud_protect=1;
    g_bench_profile=g_cfg;g_attr.format=bits?0x88000000u:0x80000000u;g_attr.tilingMode=0;
    g_t_dfmt=bits?9:10;
    g_bench_state=BENCH_IDLE;g_bench_started=1;g_bench_saved=0;g_bench_limit=1;g_bench_next=240;
    g_bench_attempt=0;g_bench_cookie=0;g_bench_width=width;g_bench_plane=(size_t)width*1080*8;
    g_bench_seq=0;g_bench_wait_go=0;
    g_bench_surfaces=surfaces;g_bench_tables=(uint32_t *)(g_dump_mem+4*g_bench_plane);
    submissions=submit_error=overlays=waits=writes=0;fail_submit=0;
    dm_alloc_calls=flex_calls=0;mock_dm_fail=0;last_dm_dump_bytes=0;
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

    /* Sequence capture test (BENCH_SEQ2): K=1, N=32, DirectMemory ONLY (no bench_alloc_flex),
     * total dump allocation <= 16 MiB (0x1000000), small 64 KiB buffer per step + 2 anchor triplets (s=0, s=31),
     * file writes ONLY after capture finishes. */
    memcpy(g_title_id,"CUSA08519",10);g_ema_frame_us=33366;g_osd_real_fps_x10=300;
    reset(960,40,1,0);
    mock_capture_cfg="1 32 960 1";mock_bench_go_present=1;g_bench_enabled=0;
    if(!bench_bootstrap() || !g_bench_enabled || g_bench_limit!=32 || !g_bench_wait_go)return 20;

    /* Verify DirectMemory failure in sequence mode refuses cleanly (BENCH_STOP) without calling bench_alloc_flex */
    g_bench_started=0;mock_dm_fail=1;flex_calls=0;
    {
        void *d[1]={original};uint32_t z[1]={sizeof(original)};
        g_flips=240;expected_index=0;
        if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,100000) || g_bench_state!=BENCH_STOP || flex_calls!=0)return 201;
    }

    /* Reset and verify real bench_allocate() in sequence mode accepts width=240, uses DirectMemory ONLY and <= 16 MiB */
    reset(240,40,1,0);
    mock_capture_cfg="1 32 240 1";mock_bench_go_present=0;g_bench_enabled=0;
    if(!bench_bootstrap() || !g_bench_enabled || g_bench_limit!=32 || g_bench_width!=240 || !g_bench_wait_go)return 20;
    g_bench_started=0;mock_dm_fail=0;flex_calls=0;last_dm_dump_bytes=0;
    ((uint32_t *)work_mem)[0]=0x11111111u;
    {
        void *d[1]={original};uint32_t z[1]={sizeof(original)};
        g_flips=240;expected_index=0;
        if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,100000) || g_bench_state!=BENCH_IDLE || !g_bench_wait_go)return 21;
        if(flex_calls!=0 || last_dm_dump_bytes==0 || last_dm_dump_bytes>BENCH_SEQ_MAX_BYTES)return 202;
        if(((uint32_t *)work_mem)[0]!=0x11111111u)return 22;
        mock_bench_go_present=1;
        if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,100000) || g_bench_state!=BENCH_B || g_bench_wait_go)return 23;
        if(((uint32_t *)work_mem)[0]!=0u)return 24;
        ((uint32_t *)work_mem)[0]=0xCAFEBABEu;
        ((uint32_t *)work_mem)[(W8_V3+2048)/4]=0x04030201u;
        for(unsigned step=1;step<=32;step++) {
            g_flips=240+step;expected_index=g_flips%2;
            uint64_t us=100000ull+(uint64_t)step*33366ull;
            if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,us))return 25;
            if(((uint32_t *)work_mem)[0]!=0xCAFEBABEu || ((uint32_t *)work_mem)[(W8_V3+2048)/4]!=0x04030201u)return 26;
            uint32_t *tb=(uint32_t *)(g_gpu+SLOT0+(g_flips&3u)*SLOT_SZ+0xB00);
            if(!(tb[30]&FG8_FLAG_PYR_WARM) || tb[28]!=(step&1u))return 27;
            if(step<32 && g_bench_state!=BENCH_B)return 28;
            if(step==32 && g_bench_state!=BENCH_WAIT)return 29;
            if(writes!=0)return 30;
        }
        if(waits || overlays || submit_error || flex_calls!=0)return 30;
        bench_io_tick();if(writes || g_bench_state!=BENCH_WAIT)return 31;
        *(uint64_t *)(g_gpu+DUMP_DONE_OFF)=g_bench_cookie;
        bench_io_tick();
        /* 32 steps * 2 files (bench_seq_XXXX.bin + .json) + 2 anchor triplets * 5 files = 74 writes */
        if(writes!=74 || g_bench_saved!=32 || g_bench_state!=BENCH_DONE)return 32;
        size_t seq_base=BENCH_SEQ_ANCHOR_PLANES*g_bench_plane;
        /* Step 0 writes: seq_0000.bin, seq_0000.json, then anchor 0 (a, gt, b, mid, json) */
        if(write_ptrs[0]!=g_dump_mem+seq_base || write_sizes[0]!=BENCH_SEQ_STEP_BYTES)return 33;
        if(write_ptrs[2]!=g_dump_mem+0u*g_bench_plane || write_ptrs[3]!=g_dump_mem+1u*g_bench_plane)return 34;
        if(write_ptrs[4]!=g_dump_mem+1u*g_bench_plane || write_ptrs[5]!=g_dump_mem+2u*g_bench_plane)return 35;
        /* Intermediate steps s=1..30 write only seq_XXXX.bin + seq_XXXX.json (2 writes per step) */
        for(unsigned s=1;s<31;s++) {
            unsigned wi=7u+(s-1u)*2u;
            if(write_ptrs[wi]!=g_dump_mem+seq_base+(size_t)s*BENCH_SEQ_STEP_BYTES)return 36;
            if(write_sizes[wi]!=BENCH_SEQ_STEP_BYTES)return 36;
        }
        /* Final step s=31 writes seq_0031.bin, seq_0031.json, then anchor 1 (a, gt, b, mid, json) */
        if(write_ptrs[67]!=g_dump_mem+seq_base+31u*BENCH_SEQ_STEP_BYTES || write_sizes[67]!=BENCH_SEQ_STEP_BYTES)return 37;
        if(write_ptrs[69]!=g_dump_mem+3u*g_bench_plane || write_ptrs[70]!=g_dump_mem+4u*g_bench_plane)return 38;
        if(write_ptrs[71]!=g_dump_mem+4u*g_bench_plane || write_ptrs[72]!=g_dump_mem+5u*g_bench_plane)return 39;

        /* Sequence with one dt=39 ms step (s=5, stage=6) MUST be saved, with dt_us, timing_ok, nonuniform_steps=1 */
        g_bench_state=BENCH_IDLE;g_bench_saved=0;g_bench_next=240;writes=0;
        g_flips=240;expected_index=0;
        if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,100000) || g_bench_state!=BENCH_B)return 40;
        {
            uint64_t us=100000ull;
            for(unsigned step=1;step<=32;step++) {
                g_flips=240+step;expected_index=g_flips%2;
                us += (step==6) ? 39000ull : 33366ull;
                if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,us))return 41;
            }
        }
        *(uint64_t *)(g_gpu+DUMP_DONE_OFF)=g_bench_cookie;
        bench_io_tick();
        if(writes!=74 || g_bench_saved!=32 || g_bench_state!=BENCH_DONE)return 42;
        /* write_bufs[1] is bench_seq_0000.json; write_bufs[7 + (5-1)*2 + 1] = write_bufs[16] is bench_seq_0005.json */
        if(!strstr(write_bufs[1], "\"dt_us\":33366,\"timing_ok\":true,\"nonuniform_steps\":1"))return 43;
        if(!strstr(write_bufs[16], "\"dt_us\":39000,\"timing_ok\":false,\"nonuniform_steps\":1"))return 44;

        /* Sequence with dt=120 ms (>100 ms) MUST be discarded */
        g_bench_state=BENCH_IDLE;g_bench_saved=0;g_bench_next=240;writes=0;
        g_flips=240;expected_index=0;
        if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,100000) || g_bench_state!=BENCH_B)return 45;
        {
            uint64_t us=100000ull;
            for(unsigned step=1;step<=32;step++) {
                g_flips=240+step;expected_index=g_flips%2;
                us += (step==6) ? 120000ull : 33366ull;
                if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,us))return 46;
            }
        }
        *(uint64_t *)(g_gpu+DUMP_DONE_OFF)=g_bench_cookie;
        bench_io_tick();
        if(writes!=0 || g_bench_state!=BENCH_IDLE)return 47;

        /* Sequence with a skipped flip (g_flips != g_bench_first + stage) MUST be discarded */
        g_bench_state=BENCH_IDLE;g_bench_saved=0;g_bench_next=240;writes=0;
        g_flips=240;expected_index=0;
        if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,100000) || g_bench_state!=BENCH_B)return 48;
        for(unsigned step=1;step<=32;step++) {
            g_flips=240+step+(step>=6?1:0);expected_index=g_flips%2;
            uint64_t us=100000ull+(uint64_t)step*33366ull;
            if(bench_flip(1,d,z,NULL,NULL,1,expected_index,1,77,us))return 49;
        }
        *(uint64_t *)(g_gpu+DUMP_DONE_OFF)=g_bench_cookie;
        bench_io_tick();
        if(writes!=0 || g_bench_state!=BENCH_IDLE)return 50;
    }
    printf("capture host checks passed (60Hz + 30Hz + seq2 N=32 dm=%zu bytes); max appended DCB %u / 8192 bytes\n",
           last_dm_dump_bytes, max_bytes);
    return 0;
}
''']
out = ROOT / 'build/verification'
out.mkdir(parents=True, exist_ok=True)
bench_out = ROOT / 'build/classic-bench'
if not (bench_out / 'bench_shaders.h').exists():
    bench_out.mkdir(parents=True, exist_ok=True)
    subprocess.run([cfg['python'], '-I', str(ROOT / 'classic/gen_shaders.py'),
                    cfg['llc_gfx700'], str(ROOT / 'classic/bench/shaders'),
                    str(bench_out / 'bench_shaders.h')], check=True)
source = out / 'capture_host.c'
source.write_text('\n'.join(pieces), encoding='utf-8')
llvm = pathlib.Path(cfg['llvm_bin'])
if (llvm / 'clang.exe').exists():
    bitcode = out / 'capture_host.bc'
    subprocess.run([str(llvm / 'clang.exe'), '--target=x86_64-pc-windows-msvc', '-O0', '-fno-builtin',
                    '-I' + str(ROOT / 'classic/source'), '-I' + str(ROOT / 'overlay'), '-I' + str(ROOT / 'build/classic-bench'),
                    '-emit-llvm', '-c', str(source), '-o', str(bitcode)], check=True)
    subprocess.run([str(llvm / 'lli.exe'), '--dlopen=msvcrt.dll', str(bitcode)], check=True)
else:
    exe = out / 'capture_host'
    subprocess.run(['cc', '-O0', '-fno-builtin',
                    '-I' + str(ROOT / 'classic/source'), '-I' + str(ROOT / 'overlay'), '-I' + str(ROOT / 'build/classic-bench'),
                    str(source), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
