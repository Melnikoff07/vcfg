"""Host-execute actual staged C command builders and inspect emitted dispatches.

Checks capacity, immutable descriptor destinations, raw real surfaces, original
flip tail, unsupported profiles, native bypass and all four command slots.
GPU execution, texture addressing and timing are not emulated here.
"""
import ast
import argparse
import json
import pathlib
import re
import subprocess


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--split',action='store_true');ap.add_argument('--build-dir');args=ap.parse_args()
    root=pathlib.Path(__file__).resolve().parents[2];out=root/(args.build_dir or ('build/postfix_split' if args.split else 'build/postfix'))
    text=(out/'source/main.c').read_text();cfg=json.loads((root/'config.json').read_text())
    old=ast.parse((root/'tools/bench/check_capture.py').read_text())
    prefix=next(ast.literal_eval(n.value) for n in old.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='prefix' for t in n.targets))
    prefix=prefix.replace('gpu_mem[0x20000+64]','gpu_mem[0x40000+64]')
    prefix=prefix.replace('*w=1;','*w=1u|(flags<<24);')
    prefix=prefix.replace('memset(p,0,28); return 0;', 'memset(p,0,28); p[0]=0xC0051000u; return 0;')
    def function(name):
        m=re.search(r'^static [^\n]+\b'+name+r'\(',text,re.M);pos=text.index('{',m.end());end=pos+1;depth=1
        while depth:
            depth+=(text[end]=='{')-(text[end]=='}');end+=1
        return text[m.start():end]
    def define(name):
        lines=text.splitlines()
        for i,line in enumerate(lines):
            if re.match(r'#define '+name+r'(?:\(|\s)',line):
                result=[line]
                while result[-1].endswith('\\'):i+=1;result.append(lines[i])
                return '\n'.join(result)
        raise ValueError(name)
    struct=re.search(r'typedef struct \{.*?\} fg_config_t;',text,re.S)[0]
    attr=re.search(r'typedef struct \{\s*uint32_t format,.*?\} VoAttr;',text,re.S)[0]
    pieces=[prefix,struct,attr,'static fg_config_t g_cfg; static VoAttr g_attr;',
            '#include "shaders.h"','#include "fg8_table.h"',
            'const uint8_t *vov_pal_ptr(void){return NULL;} const uint8_t *vov_pix_ptr(void){return NULL;}',
            'static uint32_t g_postfix_off=0x30000; static int g_postfix_ready=1; static uint8_t *g_postfix_tmp=(uint8_t *)0x500000000ull;',
            'static uint32_t g_postfix_raw_seq; static int g_postfix_raw_valid;',
            '#define POSTSPLIT '+str(int(args.split))]
    pieces += [define(n) for n in ('SLOT0','SLOT_SZ','STATS_OFF','WORK_SZ','DUMP_DONE_OFF','DCB8_OFF','DCB8_SZ',
                                   'PKT3','TS_EOP','TS2','PASS_BARRIER','SET_PASS')]
    pieces += [function(n) for n in ('calc_rcas_params','build_dcb_post8','build_dcb_flow8')]
    pieces += [r'''
static uint32_t command[DCB8_SZ/4+128],tail[64];
static uint64_t texaddr(const uint32_t *p){return ((uint64_t)p[0]<<8)|((uint64_t)(p[1]&255u)<<40);}
static uint64_t progaddr(uint32_t off){return ((uint64_t)(uintptr_t)(g_gpu+off))&~255ull;}
int main(void){
 unsigned tested=0,maxbytes=0;
 for(unsigned page=0;page<128;page++){
   uint64_t mapping=0x600000000ull+page*0x4000ull;
   uint64_t base=(mapping+0x1fffffull)&~0x1fffffull;
   if(base<mapping || base+(POSTSPLIT?0x1000000ull:0x800000ull)>mapping+(POSTSPLIT?0x1200000ull:0xa00000ull))return 1;
 }
 for(unsigned slot=0;slot<4;slot++)
 for(unsigned variant=0;variant<6;variant++)
 for(unsigned hist=0;hist<(POSTSPLIT?3u:1u);hist++)
 for(unsigned copy=0;copy<2;copy++){
   memset(gpu_mem,0,sizeof(gpu_mem));memset(&g_cfg,0,sizeof(g_cfg));
   g_cfg.sharpness=100;g_cfg.preset=0;g_cfg.upscale=POSTSPLIT?1:0;g_cfg.overlay=0;
   g_bench_offscreen=0;g_dump_req=0;g_fg_off=0;g_postfix_ready=1;
   if(variant==1)g_postfix_ready=0;
   if(variant==2)g_cfg.sharpness=50;
   if(variant==3)g_cfg.upscale=POSTSPLIT?0:1;
   if(variant==4)g_cfg.overlay=1;
   int two=(variant==5);
   g_postfix_raw_valid=hist!=0;g_postfix_raw_seq=hist==1?122:100;
   for(unsigned i=0;i<17;i++)g_sh8_off[i]=0x8000+i*256;
   for(unsigned i=0;i<64;i++)tail[i]=0x77770000u+i;
   for(unsigned i=0;i<DCB8_SZ/4+128;i++)command[i]=0xdeadbeefu;
   const uint64_t P=0x210000000ull,C=0x211000000ull,M=0x212000000ull,M2=0x213000000ull;
   uint32_t n=build_dcb_flow8(command,P,C,M,M2,1920,8,1920u*1088u*4u,slot,123,tail,1,3,4,32768,45000,two,0,
                            copy?0x214000000ull:0ull,5);
   if(n*4>DCB8_SZ || n<64)return 2;
   for(unsigned i=DCB8_SZ/4;i<DCB8_SZ/4+128;i++)if(command[i]!=0xdeadbeef)return 3;
   for(unsigned i=0;i<64;i++)if(command[n-64+i]!=tail[i])return 4;
   unsigned posts=0,scratchcopies=0,historycopies=0,realcopies=0,dispatch=0;uint64_t shader=0,tbl=0;
   for(unsigned k=0;k<n-64;){
     uint32_t hd=command[k];unsigned words=((hd>>16)&0x3fffu)+2u,op=(hd>>8)&255u;
     if((hd>>30)!=3 || k+words>n-64)return 5;
     if(op==0x76 && command[k+1]==0x240)tbl=(uint64_t)command[k+2]|((uint64_t)command[k+3]<<32);
     if(op==0x76 && command[k+1]==0x20c)shader=((uint64_t)command[k+2]<<8)|((uint64_t)command[k+3]<<40);
     if(op==0x15){
       dispatch++;
       if(tbl<(uint64_t)(uintptr_t)g_gpu || tbl+608>(uint64_t)(uintptr_t)(g_gpu+sizeof(gpu_mem)))return 6;
       uint32_t *tb=(uint32_t *)(uintptr_t)tbl;
       if(shader==progaddr(g_sh8_off[5])){
         uint64_t expectedP=(POSTSPLIT && variant==0 && hist==1)?(uint64_t)(uintptr_t)g_postfix_tmp+0x800000ull:P;
         if(texaddr(tb)!=expectedP)return 14;
       }
       if(shader==progaddr(g_sh8_off[14]))return 7; // never dispatch legacy in-place post
       if(shader==progaddr(g_postfix_off)){
         posts++;
         uint64_t expectedSource=(POSTSPLIT && posts==2)?C:M;
         if((tbl+576)%32 || texaddr(tb+16)!=expectedSource || texaddr(tb+144)!=(uint64_t)(uintptr_t)g_postfix_tmp)return 8;
         if((tb[137]&3u)!=1u)return 9;
         if(POSTSPLIT && posts==2 && (tb[136]!=0 || tb[137]!=5 || historycopies!=1))return 15;
       }
       if(shader==progaddr(g_sh8_off[16])){
         if(texaddr(tb+8)==(uint64_t)(uintptr_t)g_postfix_tmp){
           if(texaddr(tb+16)==M){scratchcopies++;if(posts!=1)return 10;}
           else if(POSTSPLIT && texaddr(tb+16)==C){realcopies++;if(posts!=2 || historycopies!=1)return 16;}
           else return 17;
         }else if(POSTSPLIT && texaddr(tb+16)==(uint64_t)(uintptr_t)g_postfix_tmp+0x800000ull){
           historycopies++;if(texaddr(tb+8)!=C || posts!=1 || scratchcopies!=1)return 18;
         }else if(!copy || texaddr(tb+8)!=C || texaddr(tb+16)!=0x214000000ull)return 11;
       }
     }
     k+=words;
   }
   unsigned active=variant==0;
   if(posts!=active*(POSTSPLIT?2u:1u) || scratchcopies!=active || historycopies!=active*POSTSPLIT || realcopies!=active*POSTSPLIT || dispatch<9)return 12;
   if(POSTSPLIT && ((unsigned)g_postfix_raw_valid!=active || (active && g_postfix_raw_seq!=123)))return 19;
   if(build_dcb_post8(command,C,1920,1080,1920,8,1920u*1088u*4u,slot,123,tail)!=0)return 13;
   if(n*4>maxbytes)maxbytes=n*4;tested++;
 }
 printf("POST command checks: split=%u, %u cases; max DCB %u / %u bytes; no legacy post; separate raw history verified; source/dest distinct; original tail; 128 allocation offsets fit\n",POSTSPLIT,tested,maxbytes,DCB8_SZ);
 return 0;
}
''']
    source=out/'commands_host.c';source.write_text('\n'.join(pieces));bc=out/'commands_host.bc'
    subprocess.run([cfg['llvm_bin']+'/clang.exe','--target=x86_64-pc-windows-msvc','-O0','-fno-builtin',
                    '-I'+str(out/'source'),'-I'+str(root/'overlay'),'-emit-llvm','-c',str(source),'-o',str(bc)],check=True)
    subprocess.run([cfg['llvm_bin']+'/lli.exe','--dlopen=msvcrt.dll',str(bc)],check=True)


if __name__=='__main__':main()
