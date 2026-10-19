/* Passive discovery prototype. No graphics commands, no allocations or VideoOut rewrites.
   v2 (Claude): module list + eboot fingerprint, own flip-event counter, flip marker inside plain submits,
   first caller address per hooked API, sceGnmSubmitDone count. All read-only. */
#include <Common.h>
#include <pthread.h>
#include "plugin_common.h"

typedef struct { uint32_t format,tile,aspect,width,height,pitch,option,reserved;uint64_t reserved1; } VoAttr;
typedef struct { uint64_t count,processTime,tsc;int64_t arg;uint64_t submit,reserved;int32_t gcq,pending,index;uint32_t pad; } VoStatus;
typedef struct { uint64_t begin,end;int32_t type;uint32_t pad; } DmInfo;
_Static_assert(sizeof(VoAttr)==40 && sizeof(VoStatus)==64 && sizeof(DmInfo)==24,"discovery ABI sizes");
int32_t sceGnmSubmitAndFlipCommandBuffers(uint32_t,void **,uint32_t *,void **,uint32_t *,uint32_t,uint32_t,uint32_t,int64_t);
int32_t sceGnmSubmitCommandBuffers(uint32_t,void **,uint32_t *,void **,uint32_t *);
int32_t sceGnmSubmitAndFlipCommandBuffersForWorkload(uint32_t,uint32_t,void **,uint32_t *,void **,uint32_t *,uint32_t,uint32_t,uint32_t,int64_t);
int32_t sceGnmSubmitCommandBuffersForWorkload(uint32_t,uint32_t,void **,uint32_t *,void **,uint32_t *);
int32_t sceVideoOutRegisterBuffers(int32_t,int32_t,void *const *,int32_t,const VoAttr *);
int32_t sceVideoOutGetFlipStatus(int32_t,VoStatus *);
int32_t sceVideoOutSetFlipRate(int32_t,int32_t);
int32_t sceVideoOutSubmitFlip(int32_t,int32_t,uint32_t,int64_t);
int32_t sceVideoOutUnregisterBuffers(int32_t,int32_t);
int32_t sceVideoOutClose(int32_t);
int32_t sceGnmRequestFlipAndSubmitDone(uint32_t *,uint32_t,uint32_t,uint32_t,uint32_t,int64_t);
int32_t sceGnmRequestFlipAndSubmitDoneForWorkload(uint64_t,uint32_t *,uint32_t,uint32_t,uint32_t,uint32_t,int64_t);
size_t sceKernelGetDirectMemorySize(void);
int32_t sceKernelDirectMemoryQuery(off_t,int,void *,size_t);
int32_t sceGnmSubmitDone(void);
int32_t sceVideoOutAddFlipEvent(OrbisKernelEqueue,int32_t,void *);
int32_t sceVideoOutDeleteFlipEvent(OrbisKernelEqueue,int32_t);
HOOK_INIT(sceGnmSubmitAndFlipCommandBuffers);
HOOK_INIT(sceGnmSubmitCommandBuffers);
HOOK_INIT(sceGnmSubmitAndFlipCommandBuffersForWorkload);
HOOK_INIT(sceGnmSubmitCommandBuffersForWorkload);
HOOK_INIT(sceVideoOutRegisterBuffers);
HOOK_INIT(sceVideoOutGetFlipStatus);
HOOK_INIT(sceVideoOutSetFlipRate);
HOOK_INIT(sceVideoOutSubmitFlip);
HOOK_INIT(sceVideoOutUnregisterBuffers);
HOOK_INIT(sceVideoOutClose);
HOOK_INIT(sceGnmRequestFlipAndSubmitDone);
HOOK_INIT(sceGnmRequestFlipAndSubmitDoneForWorkload);
HOOK_INIT(sceGnmSubmitDone);
attr_public const char *g_pluginName="bb_probe_nr_discovery";
attr_public const char *g_pluginDesc="Passive per-handle submission/VideoOut/DirectMemory discovery";
attr_public const char *g_pluginAuth="VCFG independent research";
attr_public uint32_t g_pluginVersion=0x00020000;

typedef struct {
    int32_t handle,rate;uint32_t used,generation,registered,index_mask,mode_mask;
    VoAttr attr,attributes[16];uint64_t addresses[16];int32_t groups[16];
    uint64_t calls[5],failures,last_submit,interval_sum,interval_count,interval_max;
    uint64_t physical_count,shown_us;int64_t arg;int32_t index,pending;
} Handle;
static Handle handles[8];static unsigned lock,dropped,stop_worker;
static uint64_t submits_without_flip,workload_without_flip;
static __thread unsigned inside_gnm;
// v2: first caller per API (0 gnm flip,1 vo flip,2 gnm flip workload,3 request,4 request workload,5 plain submit,
// 6 plain submit workload,7 get flip status,8 register buffers,9 submit done)
static uint64_t first_caller[10];
static void note_caller(unsigned k,void *ra){uint64_t z=0;__atomic_compare_exchange_n(&first_caller[k],&z,(uint64_t)(uintptr_t)ra,0,__ATOMIC_RELAXED,__ATOMIC_RELAXED);}
static uint64_t embed_marks,embed_tail_sum,submit_done_calls;static uint32_t embed_last_marker,embed_last_from_end;
static int32_t first_handle=-1;
// A flip placeholder is a type-3 NOP (0xC0xx1000) whose first body dword is 0x687507xx. Read-only scan of the last
// <=256 dwords of the last DCB of a plain submit: a hit means the game flips through a path we do not hook.
static void scan_embedded_flip(uint32_t n,void **d,const uint32_t *z){
    if(!n||!d||!z||!d[n-1]||z[n-1]<8||(z[n-1]&3))return;
    const uint32_t *w=(const uint32_t *)d[n-1];uint32_t nd=z[n-1]/4,from=nd>256?nd-256:0;
    for(uint32_t i=from;i+1<nd;i++) if((w[i]&0xC000FF00u)==0xC0001000u && (w[i+1]>>8)==0x687507u){
        __atomic_add_fetch(&embed_marks,1,__ATOMIC_RELAXED);embed_last_marker=w[i+1];embed_last_from_end=nd-i;
        embed_tail_sum+=nd-i;return;}
}
static OrbisPthread worker;
static char title[16],app_version[7],content_id[65];
static int enter(void) { if (__atomic_exchange_n(&lock,1,__ATOMIC_ACQUIRE)) {__atomic_add_fetch(&dropped,1,__ATOMIC_RELAXED);return 0;}return 1; }
static void leave(void) {__atomic_store_n(&lock,0,__ATOMIC_RELEASE);}
static Handle *slot(int32_t h) {
    for(unsigned i=0;i<8;i++) if(handles[i].used && handles[i].handle==h)return &handles[i];
    for(unsigned i=0;i<8;i++) if(!handles[i].used){memset(&handles[i],0,sizeof(handles[i]));handles[i].used=1;handles[i].handle=h;handles[i].rate=-1;return &handles[i];}
    __atomic_add_fetch(&dropped,1,__ATOMIC_RELAXED);return NULL;
}
static void record(int api,int32_t handle,uint32_t index,uint32_t mode,int rc) {
    if (!enter())return;
    Handle *s=slot(handle);
    if(s) {
        s->calls[api]++;if(rc<0)s->failures++;
        if(index<32)s->index_mask|=1u<<index;
        if(mode<32)s->mode_mask|=1u<<mode;
        uint64_t now=sceKernelGetProcessTime();
        if(!rc && s->last_submit && now>=s->last_submit){
            uint64_t d=now-s->last_submit;s->interval_sum+=d;s->interval_count++;
            if(d>s->interval_max)s->interval_max=d;
        }
        if(!rc)s->last_submit=now;
    }
    leave();
}
int32_t sceGnmSubmitAndFlipCommandBuffers_hook(uint32_t n,void **d,uint32_t *z,void **c,uint32_t *cz,uint32_t h,uint32_t i,uint32_t m,int64_t a) {
    note_caller(0,__builtin_return_address(0));int outer=inside_gnm++==0;
    int rc=HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,int32_t(*)(uint32_t,void**,uint32_t*,void**,uint32_t*,uint32_t,uint32_t,uint32_t,int64_t),n,d,z,c,cz,h,i,m,a);
    inside_gnm--;if(outer)record(0,(int32_t)h,i,m,rc);return rc;
}
int32_t sceGnmSubmitAndFlipCommandBuffersForWorkload_hook(uint32_t w,uint32_t n,void **d,uint32_t *z,void **c,uint32_t *cz,uint32_t h,uint32_t i,uint32_t m,int64_t a) {
    note_caller(2,__builtin_return_address(0));int outer=inside_gnm++==0;
    int rc=HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffersForWorkload,int32_t(*)(uint32_t,uint32_t,void**,uint32_t*,void**,uint32_t*,uint32_t,uint32_t,uint32_t,int64_t),w,n,d,z,c,cz,h,i,m,a);
    inside_gnm--;if(outer)record(2,(int32_t)h,i,m,rc);return rc;
}
int32_t sceGnmRequestFlipAndSubmitDone_hook(uint32_t *p,uint32_t bytes,uint32_t h,uint32_t i,uint32_t m,int64_t a) {
    note_caller(3,__builtin_return_address(0));int outer=inside_gnm++==0;
    int rc=HOOK_CONTINUE(sceGnmRequestFlipAndSubmitDone,int32_t(*)(uint32_t*,uint32_t,uint32_t,uint32_t,uint32_t,int64_t),p,bytes,h,i,m,a);
    inside_gnm--;if(outer)record(3,(int32_t)h,i,m,rc);return rc;
}
int32_t sceGnmRequestFlipAndSubmitDoneForWorkload_hook(uint64_t w,uint32_t *p,uint32_t bytes,uint32_t h,uint32_t i,uint32_t m,int64_t a) {
    note_caller(4,__builtin_return_address(0));int outer=inside_gnm++==0;
    int rc=HOOK_CONTINUE(sceGnmRequestFlipAndSubmitDoneForWorkload,int32_t(*)(uint64_t,uint32_t*,uint32_t,uint32_t,uint32_t,uint32_t,int64_t),w,p,bytes,h,i,m,a);
    inside_gnm--;if(outer)record(4,(int32_t)h,i,m,rc);return rc;
}
int32_t sceGnmSubmitCommandBuffers_hook(uint32_t n,void **d,uint32_t *z,void **c,uint32_t *cz) {
    __atomic_add_fetch(&submits_without_flip,1,__ATOMIC_RELAXED);
    note_caller(5,__builtin_return_address(0));if(!inside_gnm)scan_embedded_flip(n,d,z);
    return HOOK_CONTINUE(sceGnmSubmitCommandBuffers,int32_t(*)(uint32_t,void**,uint32_t*,void**,uint32_t*),n,d,z,c,cz);
}
int32_t sceGnmSubmitCommandBuffersForWorkload_hook(uint32_t w,uint32_t n,void **d,uint32_t *z,void **c,uint32_t *cz) {
    __atomic_add_fetch(&workload_without_flip,1,__ATOMIC_RELAXED);
    note_caller(6,__builtin_return_address(0));if(!inside_gnm)scan_embedded_flip(n,d,z);
    return HOOK_CONTINUE(sceGnmSubmitCommandBuffersForWorkload,int32_t(*)(uint32_t,uint32_t,void**,uint32_t*,void**,uint32_t*),w,n,d,z,c,cz);
}
int32_t sceGnmSubmitDone_hook(void) {
    __atomic_add_fetch(&submit_done_calls,1,__ATOMIC_RELAXED);note_caller(9,__builtin_return_address(0));
    return HOOK_CONTINUE(sceGnmSubmitDone,int32_t(*)(void));
}
int32_t sceVideoOutSubmitFlip_hook(int32_t h,int32_t i,uint32_t m,int64_t a) {
    note_caller(1,__builtin_return_address(0));
    int rc=HOOK_CONTINUE(sceVideoOutSubmitFlip,int32_t(*)(int32_t,int32_t,uint32_t,int64_t),h,i,m,a);
    if(!inside_gnm)record(1,h,(uint32_t)i,m,rc);return rc;
}
int32_t sceVideoOutRegisterBuffers_hook(int32_t h,int32_t start,void *const *addresses,int32_t n,const VoAttr *attr) {
    note_caller(8,__builtin_return_address(0));
    int rc=HOOK_CONTINUE(sceVideoOutRegisterBuffers,int32_t(*)(int32_t,int32_t,void*const*,int32_t,const VoAttr*),h,start,addresses,n,attr);
    if(rc>=0){int32_t none=-1;__atomic_compare_exchange_n(&first_handle,&none,h,0,__ATOMIC_RELEASE,__ATOMIC_RELAXED);}
    if(rc>=0 && start>=0 && start<16 && n>0 && n<=16-start && attr && addresses && enter()) {
        Handle *s=slot(h);
        if(s){s->generation++;s->attr=*attr;
            for(int j=0;j<n;j++){s->addresses[start+j]=(uint64_t)(uintptr_t)addresses[j];s->attributes[start+j]=*attr;s->groups[start+j]=rc;s->registered|=1u<<(start+j);}}
        leave();
    }
    return rc;
}
int32_t sceVideoOutUnregisterBuffers_hook(int32_t h,int32_t group) {
    int rc=HOOK_CONTINUE(sceVideoOutUnregisterBuffers,int32_t(*)(int32_t,int32_t),h,group);
    if(!rc && enter()) {
        for(unsigned j=0;j<8;j++)if(handles[j].used && handles[j].handle==h){
            for(unsigned k=0;k<16;k++)if(handles[j].groups[k]==group)handles[j].registered&=~(1u<<k);
            handles[j].generation++;
        }
        leave();
    }
    return rc;
}
int32_t sceVideoOutClose_hook(int32_t h) {
    int rc=HOOK_CONTINUE(sceVideoOutClose,int32_t(*)(int32_t),h);
    if(!rc && enter()){for(unsigned j=0;j<8;j++)if(handles[j].used && handles[j].handle==h)handles[j].used=0;leave();}
    return rc;
}
int32_t sceVideoOutSetFlipRate_hook(int32_t h,int32_t rate) {
    int rc=HOOK_CONTINUE(sceVideoOutSetFlipRate,int32_t(*)(int32_t,int32_t),h,rate);
    if(!rc && enter()){Handle *s=slot(h);if(s)s->rate=rate;leave();}return rc;
}
int32_t sceVideoOutGetFlipStatus_hook(int32_t h,VoStatus *out) {
    note_caller(7,__builtin_return_address(0));
    int rc=HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t(*)(int32_t,VoStatus*),h,out);
    if(!rc && out && enter()){Handle *s=slot(h);if(s){s->physical_count=out->count;s->shown_us=out->processTime;s->arg=out->arg;s->index=out->index;s->pending=out->pending;}leave();}
    return rc;
}
static void dump_modules(FILE *f) {
    OrbisKernelModule mods[96];size_t n=0;
    if(sceKernelGetModuleList(mods,96,&n)){fprintf(f,"MODULES rc_fail\n");return;}
    for(size_t i=0;i<n && i<96;i++){
        OrbisKernelModuleInfo mi;memset(&mi,0,sizeof(mi));mi.size=sizeof(mi);
        if(sceKernelGetModuleInfo(mods[i],&mi))continue;
        fprintf(f,"MODULE i=%u name=%s",(unsigned)i,mi.name);
        for(uint32_t k=0;k<mi.segmentCount && k<4;k++)fprintf(f," seg%u=0x%llx+0x%x/%d",k,(unsigned long long)(uintptr_t)mi.segmentInfo[k].address,mi.segmentInfo[k].size,mi.segmentInfo[k].prot);
        if(i==0){fprintf(f," fingerprint=");for(int b=0;b<20;b++)fprintf(f,"%02x",mi.fingerprint[b]);}
        fprintf(f,"\n");
    }
    static const char *api[10]={"gnm_flip","vo_flip","gnm_flip_wl","request","request_wl","plain","plain_wl","get_status","register","submit_done"};
    for(int k=0;k<10;k++)if(first_caller[k])fprintf(f,"CALLER api=%s ra=0x%llx\n",api[k],(unsigned long long)first_caller[k]);
}
// v2: own flip-event subscription (the same passive observer A20 runs in REF); counts real flips of the game handle.
static uint64_t fev_events,fev_waits;static int64_t fev_last_arg;static int32_t fev_last_index=-9;static unsigned fev_stop;
static void *flip_events(void *unused){
    (void)unused;int32_t h=-1;
    for(int t=0;t<600 && (h=__atomic_load_n(&first_handle,__ATOMIC_ACQUIRE))<0 && !__atomic_load_n(&fev_stop,__ATOMIC_ACQUIRE);t++)sceKernelUsleep(100000);
    if(h<0)return NULL;
    OrbisKernelEqueue q;if(sceKernelCreateEqueue(&q,"fg_disc_flip"))return NULL;
    if(sceVideoOutAddFlipEvent(q,h,NULL)){sceKernelDeleteEqueue(q);return NULL;}
    while(!__atomic_load_n(&fev_stop,__ATOMIC_ACQUIRE)){
        OrbisKernelEvent ev[4];int n=0;OrbisKernelUseconds to=100000;
        int rc=sceKernelWaitEqueue(q,ev,4,&n,&to);fev_waits++;
        if(rc==0 && n>0){fev_events+=(uint64_t)n;VoStatus fs={0};
            if(!HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t(*)(int32_t,VoStatus*),h,&fs)){fev_last_arg=fs.arg;fev_last_index=fs.index;}}
        else if(rc && (uint32_t)rc!=0x8002003cu && (uint32_t)rc!=0x80020004u)break;
    }
    sceVideoOutDeleteFlipEvent(q,h);sceKernelDeleteEqueue(q);return NULL;
}
static void dump_memory(FILE *f) {
    uint64_t pool=sceKernelGetDirectMemorySize(),pos=0;
    fprintf(f,"DM_SAMPLE t=%llu pool=0x%llx non_atomic=1\n",(unsigned long long)sceKernelGetProcessTime(),(unsigned long long)pool);
    for(unsigned i=0;i<128 && pos<pool;i++){
        DmInfo d={0};int rc=sceKernelDirectMemoryQuery((off_t)pos,1,&d,sizeof(d));
        if(rc || d.end<=pos || d.begin>=d.end || d.end>pool){fprintf(f,"DM_END rc=0x%08x pos=0x%llx\n",(unsigned)rc,(unsigned long long)pos);break;}
        fprintf(f,"DM_RANGE start=0x%llx end=0x%llx type=%d\n",(unsigned long long)d.begin,(unsigned long long)d.end,d.type);
        pos=d.end;
    }
    uint64_t begin=0,size=0;
    int rc=sceKernelAvailableDirectMemorySize(0,(off_t)pool,0x4000,(off_t)(uintptr_t)&begin,(size_t)(uintptr_t)&size);
    fprintf(f,"DM_LARGEST rc=0x%08x start=0x%llx bytes=0x%llx\n",(unsigned)rc,(unsigned long long)begin,(unsigned long long)size);
}
// C1: [default] deployment. A title that has its own [CUSAxxxxx] section in plugins.ini keeps only its own plugins
// (VCFG, bb_probe): discovery stays out so two plugins never hook the same functions in one process.
static int has_own_section(const char *t) {
    FILE *f=fopen("/data/GoldHEN/plugins.ini","r");if(!f)return 1;   // unknown -> stay out
    char want[24],line[160];snprintf(want,sizeof(want),"[%s]",t);int found=0;
    while(fgets(line,sizeof(line),f)){char *p=line;while(*p==' '||*p=='\t')p++;if(!strncmp(p,want,strlen(want))){found=1;break;}}
    fclose(f);return found;
}
#define DISCOVERY_LOG_LIMIT (2u*1024u*1024u)   // per title; later starts append only while below this size
static void *io(void *unused) {
    (void)unused;sceKernelMkdir("/data/bb_probe",0777);
    char path[128];snprintf(path,sizeof(path),"/data/bb_probe/discovery2-%s.log",title);
    FILE *f=fopen(path,"a");if(!f)return NULL;
    if(fseek(f,0,SEEK_END)==0 && ftell(f)>(long)DISCOVERY_LOG_LIMIT){fclose(f);return NULL;}
    fprintf(f,"DISCOVERY_BEGIN title=%s version=%s content=%s explicit_directmemory_allocations=0 no_status_rewrite=1 no_gpu_commands=1\n",title,app_version,content_id);
    dump_modules(f);fflush(f);
    unsigned samples=0;
    while(samples++<180u && !__atomic_load_n(&stop_worker,__ATOMIC_ACQUIRE)) {
        Handle snapshot[8];memset(snapshot,0,sizeof(snapshot));
        if(enter()){memcpy(snapshot,handles,sizeof(snapshot));leave();}
        for(unsigned i=0;i<8;i++)if(snapshot[i].used){
            Handle *s=&snapshot[i];VoStatus fs={0};
            int rc=HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t(*)(int32_t,VoStatus*),s->handle,&fs);
            fprintf(f,"DISCOVERY t=%llu h=%d gen=%u format=0x%x tile=%u dims=%ux%u pitch=%u registered=0x%x used=0x%x modes=0x%x rate=%d gnm=%llu cpu=%llu workload=%llu request=%llu request_workload=%llu fails=%llu avg_submit_us=%llu max_submit_us=%llu status_rc=0x%08x count=%llu shown=%llu arg=%lld index=%d pending=%d dropped=%u\n",
                (unsigned long long)sceKernelGetProcessTime(),s->handle,s->generation,s->attr.format,s->attr.tile,s->attr.width,s->attr.height,s->attr.pitch,
                s->registered,s->index_mask,s->mode_mask,s->rate,(unsigned long long)s->calls[0],(unsigned long long)s->calls[1],(unsigned long long)s->calls[2],
                (unsigned long long)s->calls[3],(unsigned long long)s->calls[4],(unsigned long long)s->failures,(unsigned long long)(s->interval_count?s->interval_sum/s->interval_count:0),(unsigned long long)s->interval_max,
                (unsigned)rc,(unsigned long long)fs.count,(unsigned long long)fs.processTime,(long long)fs.arg,fs.index,fs.pending,__atomic_load_n(&dropped,__ATOMIC_RELAXED));
            for(unsigned k=0;k<16;k++)if(s->registered&(1u<<k))fprintf(f,"DISPLAY h=%d i=%u group=%d va=0x%llx format=0x%x tile=%u dims=%ux%u pitch=%u\n",s->handle,k,s->groups[k],(unsigned long long)s->addresses[k],s->attributes[k].format,s->attributes[k].tile,s->attributes[k].width,s->attributes[k].height,s->attributes[k].pitch);
        }
        fprintf(f,"SUBMITS plain=%llu workload_plain=%llu submit_done=%llu embedded_flip_marks=%llu last_marker=0x%08x last_from_end_dw=%u\n",(unsigned long long)__atomic_load_n(&submits_without_flip,__ATOMIC_RELAXED),(unsigned long long)__atomic_load_n(&workload_without_flip,__ATOMIC_RELAXED),
            (unsigned long long)__atomic_load_n(&submit_done_calls,__ATOMIC_RELAXED),(unsigned long long)__atomic_load_n(&embed_marks,__ATOMIC_RELAXED),embed_last_marker,embed_last_from_end);
        fprintf(f,"FLIP_EVENTS handle=%d events=%llu waits=%llu last_arg=%lld last_index=%d\n",__atomic_load_n(&first_handle,__ATOMIC_RELAXED),(unsigned long long)fev_events,(unsigned long long)fev_waits,(long long)fev_last_arg,fev_last_index);
        if(samples==12)dump_modules(f);
        dump_memory(f);fflush(f);sceKernelUsleep(5000000);
    }
    __atomic_store_n(&fev_stop,1,__ATOMIC_RELEASE);
    fprintf(f,"DISCOVERY_COMPLETE samples=%u hooks_remain_passthrough=1\n",samples);
    fclose(f);return NULL;
}
#define INSTALL(n) do { Detour_Construct(&Detour_##n,DetourMode_x32);Detour_DetourFunction(&Detour_##n,(uint64_t)n,(void *)n##_hook);if(!Detour_##n.StubPtr){klog("Discovery hook install failed; restart game. Module retained.\n");return 0;}}while(0)
s32 attr_public plugin_load(s32 argc,const char *argv[]) {
    (void)argc;(void)argv;struct proc_info pi={0};
    if(sys_sdk_proc_info(&pi)!=0 || strncmp(pi.titleid,"CUSA",4) || strstr(pi.name,"SceShell") || strstr(pi.name,"RemotePlay"))return 0;
    for(unsigned i=0;i<9;i++)if(!((pi.titleid[i]>='A' && pi.titleid[i]<='Z') || (pi.titleid[i]>='0' && pi.titleid[i]<='9')))return 0;
    memcpy(title,pi.titleid,9);title[9]=0;
    memcpy(app_version,pi.version,6);app_version[6]=0;memcpy(content_id,pi.contentid,64);content_id[64]=0;
    if(has_own_section(title)){klog("Discovery: %s has its own plugins.ini section; no hooks installed.\n",title);return 0;}
    INSTALL(sceGnmSubmitAndFlipCommandBuffers);INSTALL(sceGnmSubmitCommandBuffers);
    INSTALL(sceGnmSubmitAndFlipCommandBuffersForWorkload);INSTALL(sceGnmSubmitCommandBuffersForWorkload);
    INSTALL(sceVideoOutRegisterBuffers);INSTALL(sceVideoOutGetFlipStatus);
    INSTALL(sceVideoOutSetFlipRate);INSTALL(sceVideoOutSubmitFlip);
    INSTALL(sceVideoOutUnregisterBuffers);INSTALL(sceVideoOutClose);
    INSTALL(sceGnmRequestFlipAndSubmitDone);INSTALL(sceGnmRequestFlipAndSubmitDoneForWorkload);
    INSTALL(sceGnmSubmitDone);
    int rc=scePthreadCreate(&worker,NULL,io,NULL,"fg_discovery");
    OrbisPthread fev;if(!rc && scePthreadCreate(&fev,NULL,flip_events,NULL,"fg_disc_flip"))klog("Discovery flip-event thread start failed.\n");
    if(rc)klog("Discovery worker start failed; module retained. Restart game.\n");
    return 0;
}
/* Only process exit is supported. A negative return is advisory; never force unload this PRX. */
s32 attr_public plugin_unload(s32 argc,const char *argv[]) {(void)argc;(void)argv;return -1;}
s32 attr_module_hidden module_start(s64 argc,const void *args) {(void)argc;(void)args;return 0;}
s32 attr_module_hidden module_stop(s64 argc,const void *args) {(void)argc;(void)args;return 0;}
