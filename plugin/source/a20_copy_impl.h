#pragma once
#include "a21_config.h"
#include "a20_copy_shader.h"
#include "pm4scan.h"
#include "warp720.h"
#if A21_SLAB2
#include "post_cb.h"
#endif
#if A21_SCHED
#include "fg_core.h"
#endif
#if A21_FAST540
#include "warp540.h"
#endif

#if A21_FAST540
// S9 memory (REF windows): upper [0x11FB00000,0x120000000) = slab 0x24000 + 2 x 960x540 (0x200000 each, pitch 960)
// + high work 0xD0000 = 0x4F4000 (0xC000 left); low [0x130000,0x200000) = 0xC0000 (0x10000 left).
#define A20_GPU_BYTES 0x24000u
#define A21_MID_BYTES 0x200000u
#define A21_NMIDS 2u
#define A21_MID_W 960u
#define A21_MID_H 540u
#define A20_UPPER_WORK 0xd0000u
#define A20_LOWER_WORK 0xc0000u
#else
#define A20_GPU_BYTES 0x20000u
#define A21_MID_BYTES 0x384000u
#define A21_NMIDS 1u
#define A21_MID_W 1280u
#define A21_MID_H 720u
#define A20_UPPER_WORK 0x110000u
#define A20_LOWER_WORK 0x80000u
#endif
#define A20_IMAGE_BYTES (A21_MID_BYTES*A21_NMIDS)
// A22: our first mid index = number of game buffers (2 in REF, 3 in RE2). Fixed before a20_init registers it.
static int a22_mid=2;
#define A21_IS_MID(i) ((i)>=a22_mid && (i)<a22_mid+(int)A21_NMIDS)
#define A20_OVL_BYTES 0x48000u  // in-game overlay bitmap (overlay/vov.c, one buffer): this profile allows no later allocation
#define A20_TOTAL_BYTES (A20_GPU_BYTES+A20_IMAGE_BYTES+A20_UPPER_WORK+A20_OVL_BYTES)
#define A20_SLOTS 8u
#if A21_FAST540
// [0x100,0xA800) code (warp540 replaces warp720), font 0xA800, markers 0xAC00, 8 slots x 0x3000 from 0xC000.
#define A20_SLOT_BASE 0xc000u
#define A20_SLOT_BYTES 0x3000u
#define A21_CODE_LIMIT 0xa800u
#define A21_FONT_OFF 0xa800u
#define A21_MARKER_OFF 0xac00u
#define A21_NSHADERS 12u
#elif A21_SLAB2
// [0x100,0xA800) code: 11 A20 shaders + post_cb (index 11), font 0xA800, markers 0xAC00, slots as in A20.
#define A20_SLOT_BASE 0xc000u
#define A20_SLOT_BYTES 0x2800u
#define A21_CODE_LIMIT 0xa800u
#define A21_FONT_OFF 0xa800u
#define A21_MARKER_OFF 0xac00u
#define A21_NSHADERS 12u
#elif A21_SLAB_MAP
// A21 slab: [0x100,0xC000) code, font 0xC000 (768 B), markers 0xC400 (0x2000..0x2108 window), CQ ring/ctrl
// 0xC800..0xCD00 (reserved, unused until the compute-queue stage), slots 8 x 0x2400 at 0xE000..0x20000.
#define A20_SLOT_BASE 0xe000u
#define A20_SLOT_BYTES 0x2400u
#define A21_CODE_LIMIT 0xc000u
#define A21_FONT_OFF 0xc000u
#define A21_MARKER_OFF 0xc400u
#define A21_NSHADERS 12u
#else
#define A20_SLOT_BASE 0xc000u
#define A20_SLOT_BYTES 0x2800u
#define A21_CODE_LIMIT 0xa000u
#define A21_FONT_OFF 0xa000u
#define A21_MARKER_OFF 0xb000u
#define A21_NSHADERS 11u
#endif
#define A20_RING 2048u
#define A20_PAIR_LIMIT 600u
#if A21_FAST540
// Repacked work (whole buffers per window; no shader buffer crosses the physical hole):
// low: L1A 0x7F000 | L2A 0x20000 | L2B 0x20000 = 0xBF000 (<=0xC0000)
// high: L1B 0x7F000 | L3A 0x8000 | L3B 0x8000 | V3 0x1000 | V2 0x2000 | FA 0x10000 | FB 0x10000 | MISC 0x1000 |
//       S2 0x2000 | GK 0x8000 | FT 0x10000 = 0xCD000 (<=0xD0000)
_Static_assert(W8_L1B-W8_L1A+(W8_L3A-W8_L2A)<=A20_LOWER_WORK,"S9 low work overflow");
_Static_assert((W8_L2A-W8_L1B)+(W8_END-W8_L3A)<=A20_UPPER_WORK,"S9 high work overflow");
#else
_Static_assert(W8_L1B<=A20_LOWER_WORK,"A18 low work overflow");
_Static_assert(W8_END-W8_L1B<=A20_UPPER_WORK,"A18 high work overflow");
#endif
_Static_assert(A20_TOTAL_BYTES<=0x500000u,"A18 upper window overflow");
_Static_assert(A20_SLOT_BASE+A20_SLOTS*A20_SLOT_BYTES<=A20_GPU_BYTES,"A18 slots overflow");
_Static_assert(0x100u+sizeof(probe_code)<A20_SLOT_BASE,"A18 shader overflow");

void sceVideoOutSetBufferAttribute(void *,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t);
int32_t sceVideoOutUnregisterBuffers(int32_t,int32_t);
int32_t sceVideoOutDeleteFlipEvent(OrbisKernelEqueue,int32_t);
static int a20_state; // 0=new 1=init 2=copy 3=quiesce 4=done/retained
static uint8_t *a20_gpu,*a20_image;
static long a20_phys,a20_low_phys;
static uint8_t *a20_low,*a20_high;
static int a20_low_owned;
static unsigned a20_shader_off[A21_NSHADERS];
_Static_assert(A21_FONT_OFF+sizeof(g_font8x8)<=A21_MARKER_OFF,"font overlaps markers");
_Static_assert(A21_MARKER_OFF+0x110u<=A20_SLOT_BASE,"markers overlap slots");
_Static_assert(0x1d00u+576u<=A20_SLOT_BYTES,"A18 OSD exceeds slot");
_Static_assert(0x1600u+128u*4u<=0x1800u,"native tail overlaps queue fence");
_Static_assert(A20_SLOT_BASE+A20_SLOTS*A20_SLOT_BYTES==A20_GPU_BYTES,"slots must end at slab end");
#if A21_SLAB2
#define A21_R256(x) ((sizeof(x)+255u)&~255u)
_Static_assert(0x100u+A21_R256(pyr8_code)+A21_R256(me3_code)+A21_R256(me2_code)+A21_R256(gm8_code)+A21_R256(fgs8_code)+
    A21_R256(me1_code)+A21_R256(mf8_code)+A21_R256(phase8_code)+A21_R256(warp720_code)+A21_R256(sum_previous_code)+
    A21_R256(osd8_code)+A21_R256(post_cb_code)<=A21_CODE_LIMIT,"slab2 code region overflow");
#if A21_FAST540
_Static_assert(sizeof(warp540_code)==sizeof(warp720_code),"warp540 must fit the warp720 slot");
#endif
// Slot layout (offsets from the slot base). A20: 0x0 table (0x190 + menu fields to 0x230), 0x1c0 stats, 0x1e0 mon,
// 0x280 labels, 0x300 timestamps, 0x400 cmd (<=0x1000), 0x1600 native tail, 0x1800 queue fence, 0x1a00 seed table,
// 0x1d00 OSD text (576 B). A21-best adds:
#define A21_POST_TB 0x1f40u   // 4 post tables, 0x20 apart (T# copies at +0x20..+0xC0, configs at +0x220..+0x290)
#define A21_OVL_N_TB 0x21e0u  // overlay table, target = native C (0x230)
#define A21_OVL_I_TB 0x1d00u  // overlay table, target = generated mid I (0x230, replaces the old OSD text)
#define A21_POSTC_TB 0x2420u  // post tables of the native frame C (0x290)
_Static_assert(A21_POST_TB>=0x1d00u+576u && A21_POST_TB+0x290u<=A21_OVL_N_TB,"post tables");
_Static_assert(A21_OVL_I_TB+0x230u<=A21_POST_TB && A21_OVL_N_TB+0x230u<=A21_POSTC_TB && A21_POSTC_TB+0x290u<=0x2800u,
    "overlay / post tables exceed the A20 slot size");
#if A21_FAST540
#define A21_SECOND_TB 0x2800u // second mid table (0x190)
#define A21_MID_TAIL 0x2a00u  // replayed flip tail for the second mid (64 dwords; driver patches each flip's copy)
#define A21_POST2_TB 0x2b00u  // post tables of the second mid (0x290)
_Static_assert(A21_MID_TAIL>=A21_SECOND_TB+0x190u && A21_MID_TAIL+0x100u<=A21_POST2_TB &&
               A21_POST2_TB+0x290u<=A20_SLOT_BYTES,"S9 slot layout");
#endif
#endif
#if A21_SLAB_MAP
// Sum of 256-byte rounded sizes of the 12 loaded shaders (0xAD00 with A13 arrays + rcas8); re-checked at init.
_Static_assert(0x100u+((sizeof(pyr8_code)+255u)&~255u)+((sizeof(me3_code)+255u)&~255u)+((sizeof(me2_code)+255u)&~255u)+
    ((sizeof(gm8_code)+255u)&~255u)+((sizeof(fgs8_code)+255u)&~255u)+((sizeof(me1_code)+255u)&~255u)+
    ((sizeof(mf8_code)+255u)&~255u)+((sizeof(phase8_code)+255u)&~255u)+((sizeof(warp720_code)+255u)&~255u)+
    ((sizeof(sum_previous_code)+255u)&~255u)+((sizeof(osd8_code)+255u)&~255u)+((sizeof(rcas8_code)+255u)&~255u)
    <=A21_CODE_LIMIT,"A21 code region overflow");
#endif
// ---- A21 discovery telemetry (passive) ----
static unsigned a21_tail_old_ok,a21_tail_struct_ok,a21_tail_disagree,a21_tail_trailing,a21_tail_m77,a21_tail_m78;
static unsigned a21_cs_first_full,a21_cs_first_partial,a21_cs_nodispatch,a21_skip_nodispatch,a21_skip_dispatch;
static unsigned a21_carry_used,a21_carry_refused;
static uint64_t a21_disc_report_at;
static uint64_t a21_n_hold_repeat,a21_i_hold_repeat;
static volatile uint32_t a21_nonflip_submits; // game sceGnmSubmitCommandBuffers calls (A21_COUNT_SUBMITS hook)
static __thread int a21_own_submit;
static uint32_t a21_prev_full_known,a21_submits_at_prev;
static const char *volatile a21_stop_reason="none";
static volatile uint32_t *a21_label3;   // S9: WaitFlipDone label of index 3
static uint64_t a21_total_mids,a21_post_jobs,a21_overlay_jobs;
#if A21_MENU
// Overlay metrics, x10 per second, refreshed by the worker once per second from observer counters.
static uint32_t a21_disp_x10,a21_uniq_x10,a21_rep_x10,a21_skip_x10,a21_gpu_us;
#endif
#if A21_SCHED
static FgScheduler a21_sched;
#endif
static int a21_sched_reset; // set when the preset changes: FAST restarts without carried credit
static int32_t a20_handle=-1,a20_group=-1;
static uint64_t a20_ready_at,a20_stop_at;
static unsigned a20_busy,a20_copies,a20_skips,a20_valid_slots;
static uint32_t a20_slot_seq[A20_SLOTS];
static int64_t a20_slot_arg[A20_SLOTS];
static int64_t a20_last_arg=-1;
static OrbisKernelEqueue a20_queue;
static int a20_queue_created,a20_event_added,a20_thread_started;
static OrbisPthread a20_thread;
static int a20_observer_stop,a20_observer_done;
typedef struct {
    uint64_t cpu,count,shown,vblank;
    int64_t arg;
    int32_t index,pending,wait_rc,status_rc,vb_rc,events;
    uint32_t kind,label_readable,label2,probe_enter,probe_done;
    uint64_t recheck_count;
    int32_t recheck_index;
} A18Observation;
static A18Observation a20_ring[A20_RING];
static unsigned a20_ring_count,a20_ring_dropped;
// Only the observer writes the live counters/ring. Snapshot publication uses release/acquire.
static A18Observation a20_snapshot[A20_RING];
static unsigned a20_snapshot_count;
static int a20_snapshot_request,a20_capture_gate;
static uint64_t a20_capture_started,a20_capture_poll;
static int a20_auto_captured;
static uint64_t a20_seen_count,a20_seen_time;
static int a20_seen_index=-1;
static uint64_t a20_shown_i,a20_shown_n,a20_unobserved,a20_i_hold_us,a20_i_holds,a20_status_errors;
static uint64_t a20_report_i,a20_report_n,a20_report_at;
static pm4_frame_t a20_shadow;
static unsigned a20_scene=2,a20_eligible;
static int a20_enabled,a20_menu_open,a20_pad_lock,a20_save_config,a20_cooldown;
static int a20_cadence_reset;
static unsigned a20_fg_pairs,a20_ui_frames,a20_ring_total;
static uint64_t a20_pad_hold,a20_pad_stamp,a20_runtime_report_at;
static uint32_t a20_pad_buttons;
static int a20_pad_fired;
static int a20_valve_requested,a20_valve_success,a20_logs_pending,a20_urgent_io;
static uint64_t a20_io_tid,a20_valve_start;
static int a20_retained;
static volatile uint32_t *a20_driver_label;
static int a20_label_readable;
static unsigned a20_probe_seq;
static int64_t a20_probe_arg;
static int a20_probe_submitted,a20_probe_blocked_seen,a20_probe_unsafe;
static uint64_t a20_probe_first_done_cpu,a20_stop_count;
static uint64_t a20_native_count;
static int64_t a20_native_arg=-1;
static volatile uint32_t *a20_marker(unsigned offset)
{
    return (volatile uint32_t *)(a20_gpu+A21_MARKER_OFF+(offset-0x2000u));
}
static void a20_eop_marker(uint32_t **pp,uint64_t address,uint32_t value)
{
    uint32_t *p=*pp;
    *p++=PKT3(0x47,5,0);*p++=0x528;*p++=(uint32_t)address;
    *p++=0x40000000u|((uint32_t)(address>>32)&0xffffu);*p++=value;*p++=0;*pp=p;
}
static int a20_patch_probe_labels(uint32_t *p,unsigned n,uint64_t old_ready,uint64_t old_done)
{
    unsigned pos=0,patched=0;
    while (pos<n) {
        uint32_t h=p[pos];unsigned len=((h>>16)&0x3fffu)+2u,op=(h>>8)&255u;
        if ((h>>30)!=3u || len>n-pos) return 0;
        if ((op==0x47 && len==6) || (op==0x3c && len==7)) {
            uint64_t a=(uint64_t)p[pos+2]|((uint64_t)(p[pos+3]&(op==0x47?0xffffu:0xffffffffu))<<32);
            uint64_t to=0;
            if (a==old_ready) to=(uint64_t)(uintptr_t)a20_marker(0x2100);
            if (a==old_done) to=(uint64_t)(uintptr_t)a20_marker(0x2108);
            if (to) {
                p[pos+2]=(uint32_t)to;
                p[pos+3]=op==0x47?(p[pos+3]&0xffff0000u)|((uint32_t)(to>>32)&0xffffu):(uint32_t)(to>>32);
                patched++;
            }
        }
        pos+=len;
    }
    return patched==4;
}

typedef struct {
    uint64_t gpu[7],submit_cpu,completed_cpu;
    int64_t arg;
    uint32_t seq,slot,kind,stats[8];
} A18Timing;
static A18Timing a20_times[A20_PAIR_LIMIT];
static uint64_t a20_last_i_time;
static int64_t a20_last_i_arg;
static unsigned a20_cadence_n,a20_cadence_late;
static uint32_t a20_cadence_bits;
static int a20_pacing_warning;
static uint64_t a20_warning_log_at;
static unsigned a20_warning_reports;
static uint8_t *a20_work_address(unsigned off)
{
#if A21_FAST540
    // Called only with buffer start offsets; each buffer lies wholly in one window.
    if (off<W8_L1B) return a20_low+off;                                  // L1A
    if (off>=W8_L2A && off<W8_L3A) return a20_low+(W8_L1B-W8_L1A)+(off-W8_L2A); // L2A, L2B
    if (off<W8_L2A) return a20_high+(off-W8_L1B);                        // L1B
    return a20_high+(W8_L2A-W8_L1B)+(off-W8_L3A);                        // L3A..FT
#else
    return off<W8_L1B ? a20_low+off : a20_high+(off-W8_L1B);
#endif
}
static void a20_table(uint32_t *tb,uint64_t previous,uint64_t current,uint8_t *sb,unsigned parity)
{
    memset(tb,0,0x190);
    fill_table8(tb,previous,current,(uint64_t)(uintptr_t)a20_image,g_pitch,g_attr.tilingMode==0?10:8,
        parity,2u|(32768u<<16),32u|64u|512u,NULL,(volatile uint32_t *)(sb+0x1e0),(volatile uint32_t *)(sb+0x1c0));
    tb[31]=0; // HUD masking is not configured in this diagnostic.
    make_t(tb+16,(uint64_t)(uintptr_t)a20_image,A21_MID_W,A21_MID_H,A21_MID_W,8);
#define AV(i,o,n) make_v(tb+(i),(uint64_t)(uintptr_t)a20_work_address(o),n)
    AV(32,parity?W8_L1B:W8_L1A,960*540+16);AV(36,parity?W8_L1A:W8_L1B,960*540+16);
    AV(40,parity?W8_L2B:W8_L2A,480*270+16);AV(44,parity?W8_L2A:W8_L2B,480*270+16);
    AV(48,parity?W8_L3B:W8_L3A,240*135+16);AV(52,parity?W8_L3A:W8_L3B,240*135+16);
    AV(56,W8_V3,4096);AV(60,W8_V2,60*34*4);AV(64,parity?W8_FB:W8_FA,120*68*8);
    AV(68,parity?W8_FA:W8_FB,120*68*8);AV(72,W8_MISC,32);AV(80,W8_S2,60*34*4);
    AV(84,W8_GK,120*68*4);AV(96,W8_FT,120*68*8);
#undef AV
}
static void a20_timestamp(uint32_t **pp,uint8_t *sb,unsigned i)
{
    uint64_t a=(uint64_t)(uintptr_t)(sb+0x300+i*8);uint32_t *p=*pp;
    *p++=PKT3(0x47,5,0);*p++=0x528;*p++=(uint32_t)a;
    *p++=0x60000000u|((uint32_t)(a>>32)&0xffffu);*p++=0;*p++=0;*pp=p;
}
static void a20_pass(uint32_t **pp,unsigned pass,unsigned r1,unsigned r2,unsigned x,unsigned y)
{
    uint32_t *p=*pp;uint64_t code=(uint64_t)(uintptr_t)(a20_gpu+a20_shader_off[pass]);
    *p++=PKT3(0x76,3,1);*p++=0x20c;*p++=(uint32_t)(code>>8);*p++=(uint32_t)(code>>40);
    *p++=PKT3(0x76,3,1);*p++=0x212;*p++=r1;*p++=r2;
    *p++=PKT3(0x15,4,1);*p++=x;*p++=y;*p++=1;*p++=1;
    *p++=PKT3(0x46,1,0);*p++=0x407;
    *p++=PKT3(0x58,6,0);*p++=0x80400000;*p++=1;*p++=0;*p++=0;*p++=0;*p++=0xa;
    *pp=p;
}
static void a20_collect_slot(unsigned slot)
{
    if (!(a20_valid_slots&(1u<<slot))) return;
    uint8_t *sb=a20_gpu+A20_SLOT_BASE+slot*A20_SLOT_BYTES;
    unsigned seq=a20_slot_seq[slot];
    if (!seq || *(volatile uint32_t *)(sb+0x280)!=seq) return;
    A18Timing *r=&a20_times[(seq-1)%A20_PAIR_LIMIT];
    if (r->completed_cpu) return;
    for (unsigned i=0;i<7;i++) r->gpu[i]=*(volatile uint64_t *)(sb+0x300+i*8);
    for (unsigned i=0;i<8;i++) r->stats[i]=*(volatile uint32_t *)(sb+0x1c0+i*4);
    r->completed_cpu=sceKernelGetProcessTime();
}
static void a20_save_times(void)
{
    if (a20_gpu) for (unsigned i=0;i<A20_SLOTS;i++) a20_collect_slot(i);
    char path[160];snprintf(path,sizeof(path),LOG_DIR "/a20_gpu_%u.csv",a20_scene);
    FILE *f=fopen(path,"w");
    if (f) {
        fprintf(f,"seq,arg,job_kind,submit_cpu_us,completion_observed_cpu_us,gpu_before_wait,gpu_after_wait,gpu_pyramids_done,gpu_motion_done,gpu_phase_done,gpu_warp_done,gpu_late_wait_done,cut_raw,gm_x,gm_y,foreground,fade,reserved5,reserved6,reserved7\n");
        for (unsigned i=0;i<a20_copies && i<A20_PAIR_LIMIT;i++) {
            A18Timing *r=&a20_times[(a20_copies>A20_PAIR_LIMIT?a20_copies-A20_PAIR_LIMIT+i:i)%A20_PAIR_LIMIT];
            fprintf(f,"%u,%lld,%u,%llu,%llu",r->seq,(long long)r->arg,r->kind,(unsigned long long)r->submit_cpu,(unsigned long long)r->completed_cpu);
            for (unsigned j=0;j<7;j++) fprintf(f,",%llu",(unsigned long long)r->gpu[j]);
            for (unsigned j=0;j<8;j++) fprintf(f,",%u",r->stats[j]);
            fprintf(f,"\n");
        }
        fclose(f);
    }
    lg("A20_GPU_TIMES scene=%u rows=%u file_ok=%d units=raw_gpu_counter calibration=offline_cpu_span_estimate\n",a20_scene,a20_copies<A20_PAIR_LIMIT?a20_copies:A20_PAIR_LIMIT,f!=NULL);
    lg("A20_CADENCE scene=%u samples=%u late=%u threshold=8_of_rolling_32 metric=I_arg_scaled_33ms_not_absolute_deadline\n",a20_scene,a20_cadence_n,a20_cadence_late);
}
static int a20_release_low(void)
{
    if (!a20_low_owned) return 0;
    int um=a20_low?sceKernelMunmap(a20_low,A20_LOWER_WORK):0;
    int rr=um?um:sceKernelReleaseDirectMemory(a20_low_phys,A20_LOWER_WORK);
    lg("A20_LOW_RELEASE unmap_rc=0x%08x release_rc=0x%08x\n",(unsigned)um,(unsigned)rr);
    if (!um && !rr) {a20_low=NULL;a20_high=NULL;a20_low_owned=0;return 0;}
    a20_retained=1;return -1;
}
static uint64_t a20_pad_last_cpu,a20_swallow_until;
static void a20_append_observation(A18Observation row)
{
    a20_ring[a20_ring_total++%A20_RING]=row;
    if (a20_ring_count<A20_RING) a20_ring_count++;else a20_ring_dropped++;
}
#if A21_SLAB2
// User preferences (vcfg.ini). Read by the submit thread with relaxed atomics; written by pad/worker.
static unsigned a21_preset;        // 0 = QUALITY 2X, 1 = FAST (target refresh)
static unsigned a21_sharp;         // 0..100 %, RCAS sharpness on generated frames
static unsigned a21_enhance;       // 0 = off, 1 = light, 2 = strong (post_cb directional AA)
static unsigned a21_overlay;       // 0 = off, 1..8 = small/detailed x TL/TR/BR/BL (overlay_label order)
static unsigned a21_opacity=80;    // menu / overlay alpha, 20..100 %
static void a21_parse_key(const char *line,int *enabled)
{
    int v;
    if (sscanf(line,"enabled=%d",&v)==1) *enabled=v==1;
    else if (sscanf(line,"preset=%d",&v)==1 && v>=0 && v<=1) a21_preset=(unsigned)v;
    else if (sscanf(line,"sharp=%d",&v)==1 && v>=0 && v<=100) a21_sharp=(unsigned)v;
    else if (sscanf(line,"enhance=%d",&v)==1 && v>=0 && v<=2) a21_enhance=(unsigned)v;
    else if (sscanf(line,"overlay=%d",&v)==1 && v>=0 && v<=8) a21_overlay=(unsigned)v;
    else if (sscanf(line,"opacity=%d",&v)==1 && v>=20 && v<=100) a21_opacity=(unsigned)v;
}
#endif
static void a20_load_config(void)
{
    FILE *f=fopen(LOG_DIR "/vcfg.ini","r");int value=0;
#if A21_SLAB2
    if (f) {char line[96];while (fgets(line,sizeof(line),f)) a21_parse_key(line,&value);fclose(f);}
    __atomic_store_n(&a20_enabled,value==1,__ATOMIC_RELEASE);
    lg("A20_CONFIG_LOAD enabled=%d preset=%u sharp=%u enhance=%u overlay=%u opacity=%u\n",value==1,
        a21_preset,a21_sharp,a21_enhance,a21_overlay,a21_opacity);
#else
    if (f) {if (fscanf(f,"enabled=%d",&value)!=1) value=0;fclose(f);}
    __atomic_store_n(&a20_enabled,value==1,__ATOMIC_RELEASE);
    lg("A20_CONFIG_LOAD enabled=%d\n",value==1);
#endif
}
static void a20_write_config(void)
{
    int value=__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE);
    const char *tmp=LOG_DIR "/vcfg.ini.tmp",*path=LOG_DIR "/vcfg.ini";
    FILE *f=fopen(tmp,"w");int rc=-1;
#if A21_SLAB2
    if (f) {int n=fprintf(f,"enabled=%d\npreset=%u\nsharp=%u\nenhance=%u\noverlay=%u\nopacity=%u\n",value,
        __atomic_load_n(&a21_preset,__ATOMIC_RELAXED),__atomic_load_n(&a21_sharp,__ATOMIC_RELAXED),
        __atomic_load_n(&a21_enhance,__ATOMIC_RELAXED),__atomic_load_n(&a21_overlay,__ATOMIC_RELAXED),
        __atomic_load_n(&a21_opacity,__ATOMIC_RELAXED));
        int close_rc=fclose(f);if (n>0 && !close_rc) rc=rename(tmp,path);}
#else
    if (f) {int n=fprintf(f,"enabled=%d\n",value);int close_rc=fclose(f);if (n>0 && !close_rc) rc=rename(tmp,path);}
#endif
    lg("A20_CONFIG_SAVE enabled=%d rc=%d\n",value,rc);
}
#if A21_MENU
#define A21_MENU_ROWS 7u
static unsigned a21_menu_sel;
static void a21_menu_adjust(unsigned row,int dir,int cross)
{
    if (row==0) {
        int on=__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE);
        __atomic_store_n(&a20_enabled,cross?!on:dir>0,__ATOMIC_RELEASE);
    } else if (row==1) {
#if A21_SCHED
        __atomic_fetch_xor(&a21_preset,1u,__ATOMIC_ACQ_REL);
        __atomic_store_n(&a21_sched_reset,1,__ATOMIC_RELEASE);
#else
        return; // FAST needs the scheduler stage.
#endif
    } else if (row==6) {
        lg("A21_FPS_LOCK_UNAVAILABLE reason=no_game_pacing_contract\n");return;
    } else {
        unsigned *field=row==2?&a21_sharp:row==3?&a21_enhance:row==4?&a21_overlay:&a21_opacity;
        int lo=row==5?20:0,hi=row==2||row==5?100:row==3?2:8,step=row==2||row==5?5:1;
        int v=(int)__atomic_load_n(field,__ATOMIC_ACQUIRE);
        if (cross && (row==3 || row==4)) v=v>=hi?lo:v+1;  // cycle
        else {v+=dir>0?step:-step;if (v<lo) v=(row==3||row==4)?hi:lo;if (v>hi) v=(row==3||row==4)?lo:hi;}
        __atomic_store_n(field,(unsigned)v,__ATOMIC_RELEASE);
    }
    __atomic_store_n(&a20_save_config,1,__ATOMIC_RELEASE);
    lg("A21_SETTING row=%u enabled=%d preset=%u sharp=%u enhance=%u overlay=%u opacity=%u\n",row,
        __atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE),a21_preset,a21_sharp,a21_enhance,a21_overlay,a21_opacity);
}
#endif
static int a20_pad_step(uint32_t buttons,uint64_t stamp,uint64_t now)
{
    if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)!=2) {
        __atomic_store_n(&a20_menu_open,0,__ATOMIC_RELEASE);return 0;
    }
    if (__atomic_exchange_n(&a20_pad_lock,1,__ATOMIC_ACQUIRE))
        return __atomic_load_n(&a20_menu_open,__ATOMIC_ACQUIRE);
    int menu=__atomic_load_n(&a20_menu_open,__ATOMIC_ACQUIRE),was_menu=menu;
    if ((stamp && stamp==a20_pad_stamp) || (!stamp && now-a20_pad_last_cpu<2000ull)) goto done;
    a20_pad_stamp=stamp;a20_pad_last_cpu=now;
    uint32_t edge=buttons&~a20_pad_buttons;a20_pad_buttons=buttons;
    if ((buttons&6u)==6u) { // L3+R3 from PadDataRaw button ABI.
        if (!a20_pad_hold) {a20_pad_hold=now;lg("A20_COMBO_BEGIN stamp=%llu\n",(unsigned long long)stamp);}
        if (!a20_pad_fired && now-a20_pad_hold>=3000000ull) {
            menu=!menu;a20_pad_fired=1;
            __atomic_store_n(&a20_menu_open,menu,__ATOMIC_RELEASE);
            if (!menu) {
                __atomic_store_n(&a20_cooldown,2,__ATOMIC_RELEASE);
                __atomic_store_n(&a20_cadence_reset,1,__ATOMIC_RELEASE);a20_swallow_until=now+250000ull;
            }
            lg("A20_MENU open=%d tid=%llx\n",menu,diag_thread_id());
        }
    } else {a20_pad_hold=0;a20_pad_fired=0;}
    if (menu && (edge&(0x2000u|8u))) { // Circle/Options close.
        menu=0;__atomic_store_n(&a20_menu_open,0,__ATOMIC_RELEASE);
        __atomic_store_n(&a20_cooldown,2,__ATOMIC_RELEASE);
        __atomic_store_n(&a20_cadence_reset,1,__ATOMIC_RELEASE);a20_swallow_until=now+250000ull;
        lg("A20_MENU open=0 button=close\n");
    }
#if A21_MENU
    else if (menu && (edge&(0x10u|0x40u))) { // Up/Down select a row.
        unsigned sel=__atomic_load_n(&a21_menu_sel,__ATOMIC_ACQUIRE);
        sel=(sel+((edge&0x10u)?A21_MENU_ROWS-1u:1u))%A21_MENU_ROWS;
        __atomic_store_n(&a21_menu_sel,sel,__ATOMIC_RELEASE);
    } else if (menu && (edge&(0x4000u|0x80u|0x20u))) { // Cross toggles, Left/Right change the selected row.
        a21_menu_adjust(__atomic_load_n(&a21_menu_sel,__ATOMIC_ACQUIRE),(edge&0x80u)?-1:1,(edge&0x4000u)!=0);
    }
#else
    else if (menu && (edge&(0x4000u|0x80u|0x20u))) { // Cross, Left, Right.
        { // Manual FG toggles remain available after slow frames.
            int enabled=__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE);
            if (edge&0x4000u) enabled=!enabled;
            if (edge&0x80u) enabled=0;
            if (edge&0x20u) enabled=1;
            __atomic_store_n(&a20_enabled,enabled,__ATOMIC_RELEASE);
            __atomic_store_n(&a20_save_config,1,__ATOMIC_RELEASE);
            lg("A20_TOGGLE enabled=%d menu_pauses_fg=1\n",enabled);
        }
    }
#endif
done:
    __atomic_store_n(&a20_pad_lock,0,__ATOMIC_RELEASE);
    return was_menu || menu || now<a20_swallow_until;
}
#if A21_MENU
static int a21_vov_state; // 0 not tried, 1 ready, -1 no memory
static char a21_vov_title[48];
static int a21_vov_ready(void)
{
    if (a21_vov_state) return a21_vov_state>0;
    if (!a20_gpu) return 0;
    uint8_t *mem=a20_gpu+A20_GPU_BYTES+A20_IMAGE_BYTES+A20_UPPER_WORK; // tail of the startup allocation
    vov_init_size(mem,A20_OVL_BYTES);
    vov_read_title(a21_vov_title,sizeof(a21_vov_title));
    if (!a21_vov_title[0]) vov_cpy(a21_vov_title,g_title_id,sizeof(a21_vov_title));
    lg("A21_OVERLAY ready %p title '%s'\n",mem,a21_vov_title);
    a21_vov_state=1;return 1;
}
static void a21_vov_row(VovMenu *m,const char *label,int kind,int on,int pct,const char *value)
{
    VovRow *r=&m->rows[m->nrows++];
    vov_cpy(r->label,label,sizeof(r->label));vov_cpy(r->value,value,sizeof(r->value));r->kind=kind;r->on=on;r->pct=pct;
}
static void a20_build_menu(uint32_t *tb,uint8_t *sb)
{
    (void)sb;
    tb[136]=tb[137]=tb[138]=tb[139]=0;
    if (!a21_vov_ready()) return;
    static VovMenu m;memset(&m,0,sizeof(m));
    int enabled=__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE);
    unsigned en=__atomic_load_n(&a21_enhance,__ATOMIC_ACQUIRE);
    vov_cpy(m.title,a21_vov_title,sizeof(m.title));
    m.game_fps_x10=__atomic_load_n(&g_osd_real_fps_x10,__ATOMIC_ACQUIRE);
    m.out_fps_x10=enabled?m.game_fps_x10*2u:m.game_fps_x10;
    m.sel=(int)__atomic_load_n(&a21_menu_sel,__ATOMIC_ACQUIRE);
    vov_cpy(m.status,"FG paused while the menu is open",sizeof(m.status));
    a21_vov_row(&m,"Frame generation",VOV_TOGGLE,enabled,0,"");
    a21_vov_row(&m,"Mode",VOV_TEXT,0,0,!A21_SCHED?"Quality":__atomic_load_n(&a21_preset,__ATOMIC_ACQUIRE)?"Fast":"Quality");
    a21_vov_row(&m,"Sharpness",VOV_SEGMENTS,0,(int)__atomic_load_n(&a21_sharp,__ATOMIC_ACQUIRE),"");
    a21_vov_row(&m,"Anti-aliasing",VOV_TEXT,0,0,en==2?"Strong":en==1?"Light":"Off");
    a21_vov_row(&m,"FPS counter",VOV_TEXT,0,0,overlay_label((int)__atomic_load_n(&a21_overlay,__ATOMIC_ACQUIRE)));
    a21_vov_row(&m,"Opacity",VOV_SEGMENTS,0,(int)__atomic_load_n(&a21_opacity,__ATOMIC_ACQUIRE),"");
    a21_vov_row(&m,"FPS lock",VOV_TEXT,0,0,"Keep game");
    vov_cpy(m.hints,"\xe2\x86\x95 \xe2\x86\x90 \xe2\x86\x92  \xc3\x97 SET   \xe2\x97\x8b CLOSE",sizeof(m.hints));
    const uint8_t *pal,*pix;unsigned w,h;
    if (!vov_menu(&m,&pal,&pix,&w,&h)) return;
    make_v(tb+128,(uint64_t)(uintptr_t)pal,VOV_PAL_SZ);
    make_v(tb+132,(uint64_t)(uintptr_t)pix,VOV_PIX_SZ);
    unsigned alpha=__atomic_load_n(&a21_opacity,__ATOMIC_ACQUIRE)*255u/100u;
    tb[138]=64u|(64u<<16);tb[139]=vov_wh(w,h,alpha,2u);
}
#else
static void a20_build_menu(uint32_t *tb,uint8_t *sb)
{
    uint8_t *mem=sb+0x1d00;memset(mem,0,576);
    uint32_t *styles=(uint32_t *)mem;char (*grid)[32]=(char (*)[32])(mem+64);
    char line[80];int enabled=__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE);
#if A21_MENU
    unsigned sel=__atomic_load_n(&a21_menu_sel,__ATOMIC_ACQUIRE),en=__atomic_load_n(&a21_enhance,__ATOMIC_ACQUIRE);
    unsigned gfps=__atomic_load_n(&g_osd_real_fps_x10,__ATOMIC_ACQUIRE);
    snprintf(line,sizeof(line)," VCFG %s %s GAME %u.%u",g_title_id,A21_FAST540?"540P":"720P",gfps/10,gfps%10);
    osd_set_row(styles,grid,0,1,32,0,0,line);
    snprintf(line,sizeof(line)," FRAME GENERATION: %s",enabled?"ON":"OFF");
    osd_set_row(styles,grid,1,sel==0?2u:0u,32,0,0,line);
    snprintf(line,sizeof(line)," MODE: %s",!A21_SCHED?"QUALITY 2X (FAST: N/A)":
        __atomic_load_n(&a21_preset,__ATOMIC_ACQUIRE)?"FAST (TARGET 60)":"QUALITY 2X");
    osd_set_row(styles,grid,2,sel==1?2u:0u,32,0,0,line);
    snprintf(line,sizeof(line)," SHARPNESS: %u%%%s",__atomic_load_n(&a21_sharp,__ATOMIC_ACQUIRE),A21_POST?"":" (N/A)");
    osd_set_row(styles,grid,3,sel==2?2u:0u,32,0,0,line);
    snprintf(line,sizeof(line)," ENHANCE AA: %s%s",en==2?"STRONG":en==1?"LIGHT":"OFF",A21_POST?"":" (N/A)");
    osd_set_row(styles,grid,4,sel==3?2u:0u,32,0,0,line);
    snprintf(line,sizeof(line)," OVERLAY: %s",overlay_label((int)__atomic_load_n(&a21_overlay,__ATOMIC_ACQUIRE)));
    osd_set_row(styles,grid,5,sel==4?2u:0u,32,0,0,line);
    snprintf(line,sizeof(line)," OPACITY: %u%%",__atomic_load_n(&a21_opacity,__ATOMIC_ACQUIRE));
    osd_set_row(styles,grid,6,sel==5?2u:0u,32,0,0,line);
    osd_set_row(styles,grid,7,sel==6?2u:0u,32,0,0," FPS LOCK: KEEP GAME (NO PATCH)");
    osd_set_row(styles,grid,8,3,32,0,0," UP/DOWN ROW  LEFT/RIGHT/X SET");
    osd_set_row(styles,grid,9,3,32,0,0," O CLOSE  (FG PAUSED IN MENU)");
    make_v(tb+128,(uint64_t)(uintptr_t)(a20_gpu+A21_FONT_OFF),768);
    make_v(tb+132,(uint64_t)(uintptr_t)mem,576);
    unsigned alpha=__atomic_load_n(&a21_opacity,__ATOMIC_ACQUIRE)*255u/100u;
    tb[136]=tb[137]=0;tb[138]=64u|(64u<<16);tb[139]=32u|(10u<<8)|(alpha<<16)|(2u<<24);
    return;
#endif
    osd_set_row(styles,grid,0,1,32,0,0," VCFG REF - 720P");
    snprintf(line,sizeof(line)," FRAME GENERATION: %s",enabled?"ON":"OFF");
    osd_set_row(styles,grid,1,2,18,enabled?1u:2u,0,line);
    uint32_t fps=__atomic_load_n(&g_osd_real_fps_x10,__ATOMIC_ACQUIRE);
    snprintf(line,sizeof(line)," GAME: %u.%u FPS",fps/10,fps%10);
    osd_set_row(styles,grid,2,0,32,0,0,line);
    osd_set_row(styles,grid,3,0,32,0,0,enabled?" FG OUTPUT: 720P + 1080P":" OUTPUT: NATIVE 1080P");
    osd_set_row(styles,grid,4,0,32,0,0," MODE: QUALITY 2X");
    osd_set_row(styles,grid,5,0,32,0,0," MEMORY PRESSURE: AUTO OFF");
    osd_set_row(styles,grid,6,3,32,0,0," [X] TOGGLE   [O] CLOSE");
    osd_set_row(styles,grid,7,3,32,0,0," L3+R3: HOLD 3 SECONDS");
    make_v(tb+128,(uint64_t)(uintptr_t)(a20_gpu+A21_FONT_OFF),768);
    make_v(tb+132,(uint64_t)(uintptr_t)mem,576);
    tb[136]=tb[137]=0;tb[138]=64u|(64u<<16);tb[139]=32u|(8u<<8)|(200u<<16)|(2u<<24);
}
#endif
static void a20_record(const VoFlipStatus *fs,const VoVblankStatus *vb,int wr,int sr,int vr,int events,unsigned kind)
{
    A18Observation row={.cpu=sceKernelGetProcessTime(),.count=fs->count,.shown=fs->processTime,.vblank=vb->count,
        .arg=fs->flipArg,.index=fs->currentBuffer,.pending=fs->flipPendingNum,.wait_rc=wr,.status_rc=sr,
        .vb_rc=vr,.events=events,.kind=kind,.label_readable=(uint32_t)a20_label_readable,.recheck_index=-1};
    if (sr) __atomic_add_fetch(&a20_status_errors,1,__ATOMIC_RELAXED);
    if (!sr && fs->count!=a20_seen_count) {
        if (a20_seen_count && fs->count>a20_seen_count+1)
            __atomic_add_fetch(&a20_unobserved,fs->count-a20_seen_count-1,__ATOMIC_RELAXED);
        if (a20_seen_count && fs->count==a20_seen_count+1 && A21_IS_MID(a20_seen_index) && fs->processTime>=a20_seen_time) {
            __atomic_add_fetch(&a20_i_hold_us,fs->processTime-a20_seen_time,__ATOMIC_RELAXED);
            __atomic_add_fetch(&a20_i_holds,1,__ATOMIC_RELAXED);
        }
        // A21: a surface that stays on screen for more than one 16.7 ms vblank is a repeat, not a new image.
        if (a20_seen_count && fs->count==a20_seen_count+1 && fs->processTime>a20_seen_time+25000ull) {
            if (A21_IS_MID(a20_seen_index)) __atomic_add_fetch(&a21_i_hold_repeat,1,__ATOMIC_RELAXED);
            else if (a20_seen_index>=0 && a20_seen_index<a22_mid) __atomic_add_fetch(&a21_n_hold_repeat,1,__ATOMIC_RELAXED);
        }
        if (A21_IS_MID(fs->currentBuffer)) __atomic_add_fetch(&a20_shown_i,1,__ATOMIC_RELAXED);
        else if (fs->currentBuffer>=0 && fs->currentBuffer<a22_mid) __atomic_add_fetch(&a20_shown_n,1,__ATOMIC_RELAXED);
        a20_seen_count=fs->count;a20_seen_time=fs->processTime;a20_seen_index=fs->currentBuffer;
    }
    if (a20_label_readable) row.label2=*a20_driver_label;
    row.probe_enter=*a20_marker(0x2000);row.probe_done=*a20_marker(0x2008);
    unsigned seq=__atomic_load_n(&a20_probe_seq,__ATOMIC_ACQUIRE);
    if (!sr && seq && __atomic_load_n(&a20_probe_submitted,__ATOMIC_ACQUIRE) && fs->flipArg==a20_probe_arg) {
        if (A21_IS_MID(fs->currentBuffer) && row.label_readable && row.label2 && row.probe_enter==seq && row.probe_done!=seq)
            __atomic_store_n(&a20_probe_blocked_seen,1,__ATOMIC_RELEASE);
        if (row.probe_done==seq) {
            uint64_t zero=0;
            __atomic_compare_exchange_n(&a20_probe_first_done_cpu,&zero,row.cpu,0,__ATOMIC_RELEASE,__ATOMIC_RELAXED);
            if (A21_IS_MID(fs->currentBuffer)) {
                VoFlipStatus after;
                if (HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),a20_handle,&after)==0) {
                    row.recheck_count=after.count;row.recheck_index=after.currentBuffer;
                    if (after.count==fs->count && A21_IS_MID(after.currentBuffer))
                        __atomic_store_n(&a20_probe_unsafe,1,__ATOMIC_RELEASE);
                }
            }
        }
    }
    if (__atomic_exchange_n(&a20_cadence_reset,0,__ATOMIC_ACQ_REL)) {
        a20_last_i_time=0;a20_last_i_arg=0;a20_cadence_bits=0;
    }
    if (!sr && A21_IS_MID(fs->currentBuffer) && fs->processTime!=a20_last_i_time) {
        if (a20_last_i_time && fs->flipArg>a20_last_i_arg) {
            uint64_t expected=(uint64_t)(fs->flipArg-a20_last_i_arg)*33367ull;
            a20_cadence_n++;
            unsigned late=fs->processTime>a20_last_i_time+expected+8000ull;
            if (late) a20_cadence_late++;
            a20_cadence_bits=(a20_cadence_bits<<1)|late;
            if (a20_cadence_n>=32 && __builtin_popcount(a20_cadence_bits)>=8)
                __atomic_store_n(&a20_pacing_warning,1,__ATOMIC_RELEASE);
        }
        a20_last_i_time=fs->processTime;a20_last_i_arg=fs->flipArg;
    }
    if (!sr && fs->currentBuffer>=0 && fs->currentBuffer<a22_mid) {
        __atomic_store_n(&a20_native_count,fs->count,__ATOMIC_RELAXED);
        __atomic_store_n(&a20_native_arg,fs->flipArg,__ATOMIC_RELEASE);
    }
    a20_append_observation(row);
    if (__atomic_load_n(&a20_snapshot_request,__ATOMIC_ACQUIRE)==1) {
        a20_snapshot_count=a20_ring_count;
        for (unsigned i=0;i<a20_snapshot_count;i++)
            a20_snapshot[i]=a20_ring[(a20_ring_total-a20_ring_count+i)%A20_RING];
        __atomic_store_n(&a20_snapshot_request,2,__ATOMIC_RELEASE);
    }
}
static void *a20_observer(void *unused)
{
    (void)unused;
    while (!__atomic_load_n(&a20_observer_stop,__ATOMIC_ACQUIRE)) {
        OrbisKernelEvent ev[4];int32_t n=0;OrbisKernelUseconds timeout=50000;
        int wr=HOOK_CONTINUE(sceKernelWaitEqueue,
            int32_t (*)(OrbisKernelEqueue,OrbisKernelEvent *,int32_t,int32_t *,OrbisKernelUseconds *),
            a20_queue,ev,4,&n,&timeout);
        if ((uint32_t)wr==0x8002003cu || (uint32_t)wr==0x80020004u) continue;
        if (wr) {
            a20_append_observation((A18Observation){.cpu=sceKernelGetProcessTime(),.wait_rc=wr});
            break;
        }
        VoFlipStatus fs={0};VoVblankStatus vb={0};
        int sr=HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),a20_handle,&fs);
        int vr=sceVideoOutGetVblankStatus(a20_handle,&vb);
        a20_record(&fs,&vb,wr,sr,vr,n,0);
        if (!sr && A21_IS_MID(fs.currentBuffer) && __atomic_load_n(&a20_probe_submitted,__ATOMIC_ACQUIRE) &&
            fs.flipArg==a20_probe_arg && !__atomic_load_n(&a20_probe_first_done_cpu,__ATOMIC_ACQUIRE)) {
            uint64_t deadline=sceKernelGetProcessTime()+25000ull;
            uint64_t last_count=fs.count;
            uint32_t last_enter=*a20_marker(0x2000),last_done=*a20_marker(0x2008);
            while (sceKernelGetProcessTime()<deadline && !__atomic_load_n(&a20_observer_stop,__ATOMIC_ACQUIRE)) {
                sceKernelUsleep(100);
                sr=HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),a20_handle,&fs);
                vr=sceVideoOutGetVblankStatus(a20_handle,&vb);
                uint32_t enter=*a20_marker(0x2000),done=*a20_marker(0x2008);
                if (sr || fs.count!=last_count || enter!=last_enter || done!=last_done) {
                    a20_record(&fs,&vb,0,sr,vr,0,1);
                    last_count=fs.count;last_enter=enter;last_done=done;
                }
                if (done==__atomic_load_n(&a20_probe_seq,__ATOMIC_ACQUIRE) || sr) break;
            }
        }
    }
    __atomic_store_n(&a20_observer_done,1,__ATOMIC_RELEASE);
    return NULL;
}
static void a20_write_observations(void)
{
    char path[160];snprintf(path,sizeof(path),LOG_DIR "/a20_scanout_%u.csv",a20_scene);
    FILE *f=fopen(path,"w");
    if (f) {
        fprintf(f,"cpu_us,count,shown_us,vblank,arg,index,pending,wait_rc,status_rc,vblank_rc,events,kind,label_readable,label2,probe_enter,probe_done,recheck_count,recheck_index\n");
        for (unsigned i=0;i<a20_ring_count;i++) {
            A18Observation *r=&a20_ring[(a20_ring_total-a20_ring_count+i)%A20_RING];
            fprintf(f,"%llu,%llu,%llu,%llu,%lld,%d,%d,%d,%d,%d,%d,%u,%u,%u,%u,%u,%llu,%d\n",
                (unsigned long long)r->cpu,(unsigned long long)r->count,(unsigned long long)r->shown,
                (unsigned long long)r->vblank,(long long)r->arg,r->index,r->pending,r->wait_rc,r->status_rc,r->vb_rc,r->events,
                r->kind,r->label_readable,r->label2,r->probe_enter,r->probe_done,(unsigned long long)r->recheck_count,r->recheck_index);
        }
        fclose(f);
    }
    lg("A20_OBSERVER_SAVED rows=%u ring_dropped=%u file_ok=%d\n",a20_ring_count,a20_ring_dropped,f!=NULL);
}
static void a20_save_observations(void)
{
    if (!a20_thread_started || !__atomic_load_n(&a20_observer_done,__ATOMIC_ACQUIRE)) return;
    scePthreadJoin(a20_thread,NULL);a20_thread_started=0;
    if (!a20_urgent_io) a20_write_observations();
    if (a20_event_added) { int r=sceVideoOutDeleteFlipEvent(a20_queue,a20_handle);lg("A20_DELETE_EVENT rc=0x%08x\n",(unsigned)r);if (!r) a20_event_added=0; }
    if (a20_queue_created && !a20_event_added) { int r=sceKernelDeleteEqueue(a20_queue);lg("A20_DELETE_QUEUE rc=0x%08x\n",(unsigned)r);if (!r) a20_queue_created=0; }
    if (a20_event_added || a20_queue_created) {
        a20_retained=1;lg("A20_EVENT_RESOURCES_RETAINED next_phase_disabled=1\n");
    }
}
static void a20_begin_stop(const char *reason)
{
    int expected=2;
    if (__atomic_compare_exchange_n(&a20_state,&expected,3,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))
        lg("A20_STOP_REQUEST reason=%s copies=%u\n",reason,a20_copies);
}
static int32_t a20_alloc_pressure(long start,long end,size_t length,size_t alignment,int type,long *out,int32_t original_rc)
{
    if ((uint32_t)original_rc!=0x80020023u) return original_rc;
    int state=__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE);
    if (state!=2 && state!=3) return original_rc;
    if (diag_thread_id()==__atomic_load_n(&a20_io_tid,__ATOMIC_ACQUIRE)) {
        lg("A20_VALVE_REFUSED reason=io_worker_cannot_wait_for_itself\n");return original_rc;
    }
    uint64_t begin=sceKernelGetProcessTime(),zero=0;
    __atomic_compare_exchange_n(&a20_valve_start,&zero,begin,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
    __atomic_store_n(&a20_valve_requested,1,__ATOMIC_RELEASE);
    __atomic_store_n(&a20_menu_open,0,__ATOMIC_RELEASE);
#if A21_PREF_SPLIT
    // The user's preference stays as chosen; only this session stops (state 3/4 already blocks all jobs).
    a21_stop_reason="memory_valve";
#else
    __atomic_store_n(&a20_enabled,0,__ATOMIC_RELEASE);
    __atomic_store_n(&a20_save_config,1,__ATOMIC_RELEASE);
#endif
    a20_begin_stop("allocation_pressure");
    lg("A20_VALVE_REQUEST tid=%llx initial_rc=0x%08x len=0x%zx align=0x%zx search=[0x%lx,0x%lx) wait_limit_us=300000\n",
        diag_thread_id(),(unsigned)original_rc,length,alignment,(unsigned long)start,(unsigned long)end);
    while (!__atomic_load_n(&a20_valve_success,__ATOMIC_ACQUIRE) && sceKernelGetProcessTime()<begin+300000ull) {
        if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)==4) break;
        sceKernelUsleep(1000);
    }
    if (!__atomic_load_n(&a20_valve_success,__ATOMIC_ACQUIRE)) {
        lg("A20_VALVE_ABORT waited_us=%llu original_rc=0x%08x no_unsafe_retry=1\n",
            (unsigned long long)(sceKernelGetProcessTime()-begin),(unsigned)original_rc);
        return original_rc;
    }
    int32_t rc=HOOK_CONTINUE(sceKernelAllocateDirectMemory,int32_t (*)(long,long,size_t,size_t,int,long *),
        start,end,length,alignment,type,out);
    lg("A20_VALVE_RETRY waited_us=%llu rc=0x%08x phys=0x%lx fg_disabled_session=1\n",
        (unsigned long long)(sceKernelGetProcessTime()-begin),(unsigned)rc,
        (unsigned long)((!rc && out)?*out:-1L));
    return rc;
}
#if A22_UNIVERSAL
// A22 memory planner. Reads the live DirectMemory map (allocated ranges, non-atomic) and places our blocks at the TOP
// of the highest free range: the kernel serves the game's later requests lowest-first, so the top is wanted last.
// REF keeps its measured windows. The allocator valve still handles a later EAGAIN of the game.
typedef struct { uint64_t start,end; int32_t type; uint32_t pad; } A22DmInfo;
static int a22_plan(size_t hi_len,size_t lo_len,long *hs,long *he,long *ls,long *le)
{
    if (a22_runtime_title() && strcmp(g_title_id,"CUSA07439")==0) {
        *hs=0x11FB00000L;*he=0x120000000L;*ls=0x130000L;*le=0x200000L;lg("A22_PLAN profile=ref_measured\n");return 1;
    }
    uint64_t pool=sceKernelGetDirectMemorySize(),pos=0,gs[64],ge[64];unsigned ng=0;
    for (unsigned i=0;i<256 && pos<pool;i++) {
        A22DmInfo d={0};int r=sceKernelDirectMemoryQuery((off_t)pos,1,&d,sizeof(d));
        if (r || d.end<=pos || d.start>=d.end || d.end>pool) { if (ng<64) {gs[ng]=pos;ge[ng]=pool;ng++;} break; }
        if (d.start>pos && ng<64) {gs[ng]=pos;ge[ng]=d.start;ng++;}
        pos=d.end;
        if (pos>=pool) break;
    }
    for (unsigned i=0;i<ng && i<16;i++) lg("A22_FREE i=%u start=0x%llx end=0x%llx bytes=0x%llx\n",i,
        (unsigned long long)gs[i],(unsigned long long)ge[i],(unsigned long long)(ge[i]-gs[i]));
    const uint64_t A=0x10000u;int hi=-1;
    for (int i=(int)ng-1;i>=0;i--) if (ge[i]-gs[i]>=hi_len+A) {hi=i;break;}
    if (hi<0) {lg("A22_PLAN_FAIL reason=no_free_range need=0x%zx ranges=%u\n",hi_len,ng);return 0;}
    uint64_t h0=((ge[hi]-hi_len)&~(A-1));*hs=(long)h0;*he=(long)ge[hi];
    // Low work: below our upper block in the same range if it fits, else the top of the next highest free range.
    if (h0>=gs[hi]+lo_len+A) {uint64_t l0=((h0-lo_len)&~(A-1));*ls=(long)l0;*le=(long)h0;}
    else {
        int lo=-1;for (int i=hi-1;i>=0;i--) if (ge[i]-gs[i]>=lo_len+A) {lo=i;break;}
        if (lo<0) {lg("A22_PLAN_FAIL reason=no_low_range need=0x%zx\n",lo_len);return 0;}
        uint64_t l0=((ge[lo]-lo_len)&~(A-1));*ls=(long)l0;*le=(long)ge[lo];
    }
    lg("A22_PLAN profile=live_map pool=0x%llx hi=[0x%lx,0x%lx) lo=[0x%lx,0x%lx) ranges=%u\n",
       (unsigned long long)pool,*hs,*he,*ls,*le,ng);
    return 1;
}
#endif
static void a20_init(void)
{
    __atomic_store_n(&a20_state,1,__ATOMIC_RELEASE);
    lg("A20_PHASE_START scene=%u meaning=%s\n",a20_scene,a20_scene==1?"title":"game");
    a20_handle=g_handle;
    long a22_hs=0x11FB00000L,a22_he=0x120000000L,a22_ls=0x130000L,a22_le=0x200000L;
#if A22_UNIVERSAL
    a22_mid=g_ngame;
    // 10-bit A2R10G10B10 VideoOut formats (0x88xxxxxx) are read/written as 10_10_10_2; A8R8G8B8 keeps 8_8_8_8.
    g_a22_tfmt=((g_attr.format&0xff000000u)==0x88000000u)?8u:10u;
    lg("A22_TITLE title=%s ngame=%d mid_index=%d format=0x%08x tiling=%u tfmt=%u %ux%u pitch=%u\n",g_title_id,g_ngame,a22_mid,
       g_attr.format,g_attr.tilingMode,g_a22_tfmt,g_bw,g_bh,g_pitch);
    if (!a22_plan(A20_TOTAL_BYTES,A20_LOWER_WORK,&a22_hs,&a22_he,&a22_ls,&a22_le)) {
        __atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return;
    }
#endif
    int r=HOOK_CONTINUE(sceKernelAllocateDirectMemory,
        int32_t (*)(long,long,size_t,size_t,int,long *),a22_hs,a22_he,A20_TOTAL_BYTES,0x10000,3,&a20_phys);
    void *va=NULL;int mr=r;
    if (!r) mr=sceKernelMapDirectMemory(&va,A20_TOTAL_BYTES,0x33,0,a20_phys,0x10000);
    lg("A20_ALLOC phys=0x%lx va=%p total=0x%x gpu=0x%x image=0x%x alloc_rc=0x%08x map_rc=0x%08x\n",
       (unsigned long)a20_phys,va,A20_TOTAL_BYTES,A20_GPU_BYTES,A20_IMAGE_BYTES,(unsigned)r,(unsigned)mr);
    if (r || mr || !va) {
        if (!r && sceKernelReleaseDirectMemory(a20_phys,A20_TOTAL_BYTES)!=0) a20_retained=1;
        __atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return;
    }
    a20_gpu=(uint8_t *)va;a20_image=a20_gpu+A20_GPU_BYTES;
    memset(a20_gpu,0,A20_GPU_BYTES);memset(a20_image,0xCD,A20_IMAGE_BYTES);
    // Explicit windows only; low region contains L1A, high region all offsets from L1B.
    a20_high=a20_gpu+A20_GPU_BYTES+A20_IMAGE_BYTES;
    r=HOOK_CONTINUE(sceKernelAllocateDirectMemory,int32_t (*)(long,long,size_t,size_t,int,long *),
        a22_ls,a22_le,A20_LOWER_WORK,0x10000,3,&a20_low_phys);
    if (!r) a20_low_owned=1;
    void *low_va=NULL;
    int low_map=r?r:sceKernelMapDirectMemory(&low_va,A20_LOWER_WORK,0x33,0,a20_low_phys,0x10000);
    a20_low=(uint8_t *)low_va;
    lg("A20_WORK low_phys=0x%lx low_va=%p low_bytes=0x%x high_phys=0x%lx high_va=%p high_bytes=0x%x alloc_rc=0x%08x map_rc=0x%08x\n",
        (unsigned long)a20_low_phys,low_va,A20_LOWER_WORK,(unsigned long)(a20_phys+A20_GPU_BYTES+A20_IMAGE_BYTES),a20_high,A20_UPPER_WORK,(unsigned)r,(unsigned)low_map);
    if (r || low_map || !low_va) goto unused_failure;
    memset(a20_low,0,A20_LOWER_WORK);memset(a20_high,0,A20_UPPER_WORK);
    // Index 11 (rcas8) is only loaded with A21_SLAB_MAP; it is not dispatched in this build.
#if A21_SLAB2
    // Index 11 = post_cb (replaces the racy in-place rcas8); S9: index 8 = warp540 (same size as warp720).
#if A21_FAST540
#define A21_WARP_CODE warp540_code
#else
#define A21_WARP_CODE warp720_code
#endif
    const uint32_t *codes[]={pyr8_code,me3_code,me2_code,gm8_code,fgs8_code,me1_code,mf8_code,phase8_code,A21_WARP_CODE,sum_previous_code,osd8_code,post_cb_code};
    const size_t lengths[]={sizeof(pyr8_code),sizeof(me3_code),sizeof(me2_code),sizeof(gm8_code),sizeof(fgs8_code),sizeof(me1_code),sizeof(mf8_code),sizeof(phase8_code),sizeof(A21_WARP_CODE),sizeof(sum_previous_code),sizeof(osd8_code),sizeof(post_cb_code)};
#else
    const uint32_t *codes[]={pyr8_code,me3_code,me2_code,gm8_code,fgs8_code,me1_code,mf8_code,phase8_code,warp720_code,sum_previous_code,osd8_code,rcas8_code};
    const size_t lengths[]={sizeof(pyr8_code),sizeof(me3_code),sizeof(me2_code),sizeof(gm8_code),sizeof(fgs8_code),sizeof(me1_code),sizeof(mf8_code),sizeof(phase8_code),sizeof(warp720_code),sizeof(sum_previous_code),sizeof(osd8_code),sizeof(rcas8_code)};
#endif
    unsigned code_end=0x100;
    for (unsigned i=0;i<A21_NSHADERS;i++) {
        if (code_end+lengths[i]>A21_CODE_LIMIT) {lg("A20_CODE_OVERFLOW\n");goto unused_failure;}
        a20_shader_off[i]=code_end;memcpy(a20_gpu+code_end,codes[i],lengths[i]);code_end+=(lengths[i]+255u)&~255u;
    }
    lg("A20_CODE end=0x%x limit=0x%x font=0x%x markers=0x%x slots=0x%x slot_bytes=0x%x shaders=%u low_alignment=0x%lx upper_alignment=0x%lx\n",
        code_end,A21_CODE_LIMIT,A21_FONT_OFF,A21_MARKER_OFF,A20_SLOT_BASE,A20_SLOT_BYTES,A21_NSHADERS,
        (unsigned long)(a20_low_phys&0xffffu),(unsigned long)(a20_phys&0xffffu));
    memcpy(a20_gpu+A21_FONT_OFF,g_font8x8,sizeof(g_font8x8));
    __asm__ volatile("sfence" ::: "memory");
    VoAttr attr;memset(&attr,0,sizeof(attr));
    // A22: our mid uses the game's pixel format (8-bit or 10-bit), linear, 720p.
    sceVideoOutSetBufferAttribute(&attr,A22_UNIVERSAL?g_attr.format:0x80000000u,1,0,A21_MID_W,A21_MID_H,A21_MID_W);
    void *ptrs[2]={a20_image,a20_image+A21_MID_BYTES};
    r=HOOK_CONTINUE(sceVideoOutRegisterBuffers,int32_t (*)(int32_t,int32_t,void *const *,int32_t,const VoAttr *),
        a20_handle,a22_mid,ptrs,(int32_t)A21_NMIDS,&attr);
    lg("A20_REGISTER index=%d count=%u physical=0x%lx va=%p linear=1 w=%u h=%u pitch=%u size=0x%x result=0x%08x group=%d\n",
       a22_mid,A21_NMIDS,(unsigned long)(a20_phys+A20_GPU_BYTES),a20_image,A21_MID_W,A21_MID_H,A21_MID_W,A21_MID_BYTES,(unsigned)r,r);
    if (r<0) goto unused_failure;
    a20_group=r;
    uint32_t wait_packet[7]={0};
    r=sceGnmInsertWaitFlipDone(wait_packet,7,a20_handle,a22_mid);
    uintptr_t label_address=(uintptr_t)wait_packet[2]|((uintptr_t)wait_packet[3]<<32);
    if (r || wait_packet[1]!=0x13u || wait_packet[4]!=0 || wait_packet[5]!=0xffffffffu) {
        lg("A20_WAIT_LABEL_UNSUPPORTED rc=0x%08x\n",(unsigned)r);goto unused_failure;
    }
    a20_label_readable=dcb_mem_ok(label_address,sizeof(uint32_t));
    a20_driver_label=(volatile uint32_t *)label_address;
#if A21_FAST540
    {
        uint32_t w3[7]={0};
        int r3=sceGnmInsertWaitFlipDone(w3,7,a20_handle,3);
        uintptr_t la3=(uintptr_t)w3[2]|((uintptr_t)w3[3]<<32);
        lg("A21_LABEL3 rc=0x%08x va=0x%lx readable=%d\n",(unsigned)r3,(unsigned long)la3,r3?0:dcb_mem_ok(la3,4));
        if (r3 || w3[1]!=0x13u || w3[4]!=0 || w3[5]!=0xffffffffu || !dcb_mem_ok(la3,4)) goto unused_failure;
        a21_label3=(volatile uint32_t *)la3;
    }
#endif
    lg("A20_LABEL_ADDRESS va=0x%lx cpu_readable=%d initial=%u\n",(unsigned long)label_address,
       a20_label_readable,a20_label_readable?*a20_driver_label:0);
    r=sceKernelCreateEqueue(&a20_queue,"fg_a18_observer");
    lg("A20_CREATE_QUEUE rc=0x%08x queue=0x%llx\n",(unsigned)r,(unsigned long long)a20_queue);
    if (r) goto unused_failure;
    a20_queue_created=1;
    r=HOOK_CONTINUE(sceVideoOutAddFlipEvent,int32_t (*)(OrbisKernelEqueue,int32_t,void *),a20_queue,a20_handle,NULL);
    lg("A20_ADD_OWN_EVENT rc=0x%08x\n",(unsigned)r);
    if (r) goto unused_failure;
    a20_event_added=1;
    r=scePthreadCreate(&a20_thread,NULL,a20_observer,NULL,"fg_a18_observer");
    lg("A20_OBSERVER_THREAD rc=0x%08x\n",(unsigned)r);
    if (r) goto unused_failure;
    a20_thread_started=1;a20_ready_at=sceKernelGetProcessTime();
    __atomic_store_n(&a20_state,2,__ATOMIC_RELEASE);
    lg("A20_READY continuous=1 menu=L3_R3_3sec toggle=CROSS off_by_default=1 synthesis=A13_exact_shader_arrays menu=enabled\n");
    NotifyStatic(TEX_ICON_SYSTEM,"VCFG REF ready. Hold L3+R3 for 3 seconds to open menu.");
    return;
unused_failure:
    // None of these resources have been submitted to GPU or selected for scanout.
    if (a20_event_added && sceVideoOutDeleteFlipEvent(a20_queue,a20_handle)==0) a20_event_added=0;
    if (a20_queue_created && !a20_event_added && sceKernelDeleteEqueue(a20_queue)==0) a20_queue_created=0;
    if (a20_event_added || a20_queue_created) a20_retained=1;
    if (a20_group>=0) {
        int ur=sceVideoOutUnregisterBuffers(a20_handle,a20_group);
        lg("A20_UNUSED_UNREGISTER rc=0x%08x\n",(unsigned)ur);
        if (ur<0) { a20_retained=1;__atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return; }
        a20_group=-1;
    }
    int um=sceKernelMunmap(a20_gpu,A20_TOTAL_BYTES);
    int rr=um?um:sceKernelReleaseDirectMemory(a20_phys,A20_TOTAL_BYTES);
    lg("A20_UNUSED_CLEANUP unmap_rc=0x%08x release_rc=0x%08x\n",(unsigned)um,(unsigned)rr);
    if (!um && !rr) { a20_gpu=NULL;a20_image=NULL;a20_release_low(); } else a20_retained=1;
    __atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);
}
static int a20_readable(const void *p,size_t n,void *unused) { (void)unused;return dcb_mem_cached((uintptr_t)p,n); }
static uint64_t a20_clock(void *unused) { (void)unused;return sceKernelGetProcessTime(); }
// A21 structural flip-tail recogniser. The SDK flip placeholder is a type-3 NOP of 32..128 dwords whose first body
// dword is 0x687507xx (REF: ..77 as the last packet; CUSA00411 dump: ..78 followed by register-state packets). Accept it
// only when it is the last packet or is followed exclusively by state packets (SET_SH/CONTEXT/UCONFIG_REG,
// CONTEXT_CONTROL, NUM_INSTANCES, INDEX_TYPE, NOP). Draws, dispatches, events, waits, IBs or unknown -> refuse.
// Returns tail = marker..end (replayed in every flip submit). Telemetry-only in A21c: A20 parser stays authoritative.
static int a21_find_flip_tail_struct(const uint32_t *dw,uint32_t nd,uint32_t *off_out,uint32_t *tail_out,
                                     uint32_t *marker_out,uint32_t *trail_out)
{
    if (!dw || nd<32u || nd>65536u) return 0;
    uint32_t pos=0,moff=0,mval=0,found=0,trail=0;
    while (pos<nd) {
        uint32_t h=dw[pos],type=h>>30;
        if (type==2u) {pos++;continue;}
        if (type==1u) return 0;
        uint32_t cnt=(h>>16)&0x3fffu,op=type==3u?((h>>8)&0xffu):0xffffu;
        uint32_t len=(type==3u && op==0x10u && cnt==0x3fffu)?1u:cnt+2u;
        if (pos+len>nd) return 0;
        if (type==3u && op==0x10u && len>=32u && len<=128u && (dw[pos+1]>>8)==0x687507u) {found=1;moff=pos;mval=dw[pos+1];trail=0;}
        else if (found) {
            if (op!=0x76u && op!=0x69u && op!=0x79u && op!=0x28u && op!=0x2fu && op!=0x2au && op!=0x10u) return 0;
            trail++;
        }
        pos+=len;
    }
    if (!found || pos!=nd) return 0;
    *off_out=moff;*tail_out=nd-moff;*marker_out=mval;*trail_out=trail;
    return 1;
}
#if A21_SIDE_WALK
// Read-only walk of the game's non-flip submits. Called from the sceGnmSubmitCommandBuffers hook BEFORE the original
// call; the hook decrements a21_side_inflight after the original returns. All shared state is under a21_side_lock.
static pm4_frame_t a21_side;
static int a21_side_lock;
static volatile int a21_side_inflight;
static int a21_side_dirty;                       // some submit since the last flip could not be fully walked
static uint32_t a21_side_mask,a21_side_val[PM4_NCS];   // CS registers written by walked submits since the last flip
static unsigned a21_side_walks,a21_side_clean,a21_side_dirty_n,a21_side_aborts,a21_side_ccb,a21_side_workload;
static unsigned a21_side_inflight_hits,a21_side_cs_writes,a21_side_us_max,a21_side_dw_max;
static uint64_t a21_side_us_total;
static void a21_side_lock_take(void) { while (__atomic_exchange_n(&a21_side_lock,1,__ATOMIC_ACQUIRE)) {} }
static void a21_side_lock_drop(void) { __atomic_store_n(&a21_side_lock,0,__ATOMIC_RELEASE); }
static void a21_side_mark_dirty(int workload)
{
    a21_side_lock_take();a21_side_dirty=1;a21_side_dirty_n++;if (workload) a21_side_workload++;a21_side_lock_drop();
}
static void a21_side_submit(uint32_t count,void **dcb,const uint32_t *sizes,void **ccb,const uint32_t *csizes)
{
    __atomic_add_fetch(&a21_side_inflight,1,__ATOMIC_ACQ_REL);
    a21_side_lock_take();
    a21_side_walks++;
    int dirty=!dcb || !sizes || count==0 || count>64;
    for (uint32_t i=0;!dirty && ccb && csizes && i<count;i++) if (ccb[i] && csizes[i]) {dirty=1;a21_side_ccb++;}
    if (!dirty) {
        a21_side.mode=0;a21_side.readable=a20_readable;a21_side.clock=a20_clock;
        a21_side.budget_us=400;a21_side.max_dwords=0x40000u;
        // Own frame struct (never touches a20_shadow, which the flip thread walks); only cs_seen and the written
        // sh[] values of this walk are used.
        pm4_frame_begin(&a21_side);pm4_call_begin(&a21_side);
        for (uint32_t i=0;i<count;i++) {
            if (!sizes[i] || (sizes[i]&3u) || !dcb[i]) {dirty=1;break;}
            if (pm4_walk_entry(&a21_side,(const uint32_t *)dcb[i],sizes[i]/4,(int32_t)i)!=PM4_OK) break;
        }
        if (a21_side.abort) a21_side_aborts++;
        if (a21_side.abort || a21_side.ib_unreadable || a21_side.loads || a21_side.type0) dirty=1;
        a21_side_us_total+=a21_side.us;if (a21_side.us>a21_side_us_max) a21_side_us_max=a21_side.us;
        if (a21_side.dwords>a21_side_dw_max) a21_side_dw_max=a21_side.dwords;
        if (!dirty && a21_side.cs_seen) {
            for (unsigned k=0;k<PM4_NCS;k++) if (a21_side.cs_seen&(1u<<k)) a21_side_val[k]=a21_side.sh[pm4_cs_reg_idx[k]];
            a21_side_mask|=a21_side.cs_seen;a21_side_cs_writes++;
        }
    }
    if (dirty) {a21_side_dirty=1;a21_side_dirty_n++;} else a21_side_clean++;
    a21_side_lock_drop();
}
static void a21_side_submit_done(void) { __atomic_sub_fetch(&a21_side_inflight,1,__ATOMIC_ACQ_REL); }
// At every flip call: take what happened since the previous flip and reset. Returns 1 if every intermediate submit
// was walked cleanly; their compute-register writes are folded into the shadow (before the flip call is walked).
static int a21_side_take(void)
{
    a21_side_lock_take();
    int clean=!a21_side_dirty;
    if (__atomic_load_n(&a21_side_inflight,__ATOMIC_ACQUIRE)) {clean=0;a21_side_inflight_hits++;}
    for (unsigned k=0;k<PM4_NCS;k++) if (a21_side_mask&(1u<<k)) a20_shadow.sh[pm4_cs_reg_idx[k]]=a21_side_val[k];
    a21_side_mask=0;a21_side_dirty=0;
    a21_side_lock_drop();
    return clean;
}
#endif
static void a21_discovery_report(uint64_t now)
{
#if A21_DISCOVERY
    if (now<a21_disc_report_at) return;
    a21_disc_report_at=now+10000000ull;
    lg("A21_DISCOVERY tail_old_ok=%u tail_struct_ok=%u disagree=%u trailing=%u marker77=%u marker78=%u "
       "cs_first_full=%u cs_first_partial=%u no_dispatch_calls=%u skip_no_dispatch=%u skip_with_dispatch=%u "
       "carry_used=%u carry_refused=%u nonflip_submits=%u n_repeat_holds=%llu i_repeat_holds=%llu count_submits=%d\n",
       a21_tail_old_ok,a21_tail_struct_ok,a21_tail_disagree,a21_tail_trailing,a21_tail_m77,a21_tail_m78,
       a21_cs_first_full,a21_cs_first_partial,a21_cs_nodispatch,a21_skip_nodispatch,a21_skip_dispatch,
       a21_carry_used,a21_carry_refused,__atomic_load_n(&a21_nonflip_submits,__ATOMIC_RELAXED),
       (unsigned long long)__atomic_load_n(&a21_n_hold_repeat,__ATOMIC_RELAXED),
       (unsigned long long)__atomic_load_n(&a21_i_hold_repeat,__ATOMIC_RELAXED),A21_COUNT_SUBMITS);
#if A21_SIDE_WALK
    lg("A21_SIDE walks=%u clean=%u dirty=%u aborts=%u ccb=%u workload=%u inflight_at_flip=%u cs_writes=%u "
       "us_total=%llu us_max=%u dwords_max=%u carry_used=%u carry_refused=%u\n",
       a21_side_walks,a21_side_clean,a21_side_dirty_n,a21_side_aborts,a21_side_ccb,a21_side_workload,a21_side_inflight_hits,
       a21_side_cs_writes,(unsigned long long)a21_side_us_total,a21_side_us_max,a21_side_dw_max,a21_carry_used,a21_carry_refused);
#endif
#else
    (void)now;
#endif
}
#if A21_POST
// RCAS strength as in the independent model: con ~ 0.25 * 2^-stops, stops = (100 - sharp) / 50; faded in below 25 %.
static float a21_rcas_con(unsigned sharp)
{
    if (!sharp) return 0.0f;
    float stops=(float)(100u-sharp)*0.02f;
    float con=0.25f/(1.0f+stops*(0.693147f+stops*0.240226f));
    if (sharp<25u) con*=(float)sharp*0.04f;
    return con;
}
// Four checkerboard post_cb dispatches on one generated surface (in place). Tables k=0..3 are 0x20 apart and share
// the T# copies at +0x20..+0xC0 (src == dst); each has its own config at +0x220.
static void a21_emit_post_wh(uint32_t **pp,uint8_t *tables,uint64_t surface,unsigned w,unsigned h,unsigned pitch,
                             unsigned tmode,unsigned sharp,unsigned enhance)
{
    uint32_t *tb=(uint32_t *)tables;memset(tb,0,0x290);
    for (unsigned k=0;k<5;k++) make_t(tb+8+k*8,surface,w,h,pitch,tmode);
    float con=a21_rcas_con(sharp);uint32_t conb;memcpy(&conb,&con,4);
    for (unsigned k=0;k<4;k++) {uint32_t *cfg=tb+136+k*8;cfg[0]=conb;cfg[1]=(enhance&3u)<<2;cfg[2]=k;cfg[3]=0;}
    uint32_t *p=*pp;
    for (unsigned k=0;k<4;k++) {
        uint64_t a=(uint64_t)(uintptr_t)(tables+k*0x20u);
        *p++=PKT3(0x76,3,1);*p++=0x240;*p++=(uint32_t)a;*p++=(uint32_t)(a>>32);
        a20_pass(&p,11,POST_CB_RSRC1,POST_CB_RSRC2,((w+7u)/8u+1u)/2u,((h+7u)/8u+1u)/2u);
    }
    *pp=p;
}
static void a21_emit_post(uint32_t **pp,uint8_t *tables,uint64_t surface,unsigned sharp,unsigned enhance)
{
    a21_emit_post_wh(pp,tables,surface,A21_MID_W,A21_MID_H,A21_MID_W,8,sharp,enhance);
}
#endif
#if A21_MENU
// Corner overlay drawn on the native frame C BEFORE the FG passes read it, so it is a static HUD element in both P and
// C; the warp copies that rectangle from C (HUD protection, tb[31]) into every mid -> same size on mids and natives.
// The counter (overlay/vov.c) fits in a fixed A21_OVL_W x A21_OVL_H box; right corners align it to the right edge.
#define A21_OVL_W 224u
#define A21_OVL_H 64u
static uint32_t a21_overlay_rect(unsigned ovl,unsigned *x,unsigned *y,unsigned *rows)
{
    unsigned corner=(ovl-1u)/2u;
    *rows=A21_OVL_H/16u;
    unsigned w=A21_OVL_W,h=A21_OVL_H;
    *x=(corner==1 || corner==2)?1920u-w-32u:32u;
    *y=corner>=2?1080u-h-32u:32u;
    // HUD rectangle in 1080p source pixels, 8 px units, padded by 8 px.
    return ((*x-8u)/8u)|(((*y-8u)/8u)<<8)|(((*x+w+15u)/8u)<<16)|(((*y+h+15u)/8u)<<24);
}
static void a21_emit_overlay(uint32_t **pp,uint8_t *sb,uint64_t target,unsigned ovl,unsigned do_fg,unsigned mids)
{
    (void)mids;
    if (!a21_vov_ready()) return;
    unsigned x,y,rows;a21_overlay_rect(ovl,&x,&y,&rows);
    unsigned g=__atomic_load_n(&g_osd_real_fps_x10,__ATOMIC_ACQUIRE),d=__atomic_load_n(&a21_disp_x10,__ATOMIC_ACQUIRE);
    unsigned us=__atomic_load_n(&a21_gpu_us,__ATOMIC_ACQUIRE);
    VovCounter k={do_fg?d:g,(us+50u)/100u,(ovl&1u)==0};
    const uint8_t *pal,*pix;unsigned w,h;
    if (!vov_counter(&k,&pal,&pix,&w,&h)) return;
    if (w>A21_OVL_W) w=A21_OVL_W;
    unsigned corner=(ovl-1u)/2u;
    if (corner==1 || corner==2) x+=A21_OVL_W-w;
    unsigned alpha=__atomic_load_n(&a21_opacity,__ATOMIC_ACQUIRE)*255u/100u;
    uint32_t *p=*pp;
    // Drawn after the FG passes: once on the native frame C (1:1) and, with FG, once on the 1280x720 mid I at 2/3 scale
    // (s = 2 in osd_xy), so both look the same on screen. The warp ignores this rectangle in P (it was drawn there).
    for (unsigned pass=0;pass<(do_fg?2u:1u);pass++) {
        uint32_t *tb=(uint32_t *)(sb+(pass?A21_OVL_I_TB:A21_OVL_N_TB));memset(tb,0,0x230);
        unsigned tw=w,th=h,tx=x,ty=y,sc=0;
        if (pass) { tw=(w*2u+2u)/3u;th=(h*2u+2u)/3u;tx=x*2u/3u;ty=y*2u/3u;sc=2u; }
        uint64_t surf=pass?(uint64_t)(uintptr_t)a20_image:target;
        if (pass) { make_t(tb+8,surf,A21_MID_W,A21_MID_H,A21_MID_W,8);make_t(tb+16,surf,A21_MID_W,A21_MID_H,A21_MID_W,8); }
        else { make_t(tb+8,surf,g_bw,g_bh,g_pitch,g_attr.tilingMode==0?10:8);make_t(tb+16,surf,g_bw,g_bh,g_pitch,g_attr.tilingMode==0?10:8); }
        make_v(tb+128,(uint64_t)(uintptr_t)pal,VOV_PAL_SZ);
        make_v(tb+132,(uint64_t)(uintptr_t)pix,VOV_PIX_SZ);
        tb[136]=tb[137]=0;tb[138]=tx|(sc<<12)|(ty<<16);tb[139]=vov_wh(w,h,alpha,2u); // flags 2: write T#cur
        uint64_t a=(uint64_t)(uintptr_t)tb;
        *p++=PKT3(0x76,3,1);*p++=0x240;*p++=(uint32_t)a;*p++=(uint32_t)(a>>32);
        a20_pass(&p,10,OSD8_RSRC1,OSD8_RSRC2,(tw+7u)/8u,(th+7u)/8u);
    }
    *pp=p;
}
#endif
static int a20_try_submit(uint32_t count,void **dcb,uint32_t *sizes,void **ccb,uint32_t *csizes,
                        uint32_t handle,uint32_t index,uint32_t mode,int64_t arg,int32_t *result)
{
    // Every flip call gets an id; a carried shadow is valid only from the immediately preceding, fully walked call.
    static uint32_t a21_call_id,a21_known_call_id;
    uint32_t a21_this_call=++a21_call_id;
    a21_prev_full_known=a21_known_call_id && a21_known_call_id+1u==a21_this_call;
#if A21_SIDE_WALK
    // Fold the walked intermediate submits into the shadow now (they ran before this flip call on the ring).
    int a21_side_ok=a21_side_take();
    if (!a21_side_ok) a21_prev_full_known=0;
#endif
    // A22: the previous game frame is the buffer of the previous flip call (2 or 3 game buffers).
    static int32_t a22_last_idx=-1;
    int32_t a22_prev_idx=a22_last_idx;
    if (handle==(uint32_t)a20_handle && (int)index<a22_mid) a22_last_idx=(int32_t)index;
    if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)!=2 || (sceKernelGetProcessTime()<a20_ready_at+2000000ull && !__atomic_load_n(&a20_menu_open,__ATOMIC_ACQUIRE))) return 0;
    {
        const char *gate=NULL;
        if (handle!=(uint32_t)a20_handle) gate="other_handle";
        else if ((int)index>=a22_mid) gate="index";
        else if (mode!=1) gate="flip_mode";
        else if (g_bw!=1920 || g_bh!=1080) gate="resolution";
#if A22_UNIVERSAL
        else if (g_attr.format!=0x80000000u && (g_attr.format&0xff000000u)!=0x88000000u) gate="format";
#else
        else if (g_attr.format!=0x80000000u) gate="format";
#endif
        else if (count==0 || count>16 || !dcb || !sizes) gate="dcb_count";
        if (gate) {
            static unsigned a22_gate_n;
            if (a22_gate_n++<8 || a22_gate_n%600u==0) lg("A22_GATE reason=%s n=%u handle=%u index=%u mode=%u count=%u\n",gate,a22_gate_n,handle,index,mode,count);
            return 0;
        }
    }
    if (__atomic_load_n(&a20_capture_gate,__ATOMIC_ACQUIRE)) return 0;
    int menu=__atomic_load_n(&a20_menu_open,__ATOMIC_ACQUIRE);
    int enabled=__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE);
    if (!menu && __atomic_load_n(&a20_cooldown,__ATOMIC_ACQUIRE)>0) {
        __atomic_fetch_sub(&a20_cooldown,1,__ATOMIC_ACQ_REL);return 0;
    }
#if A21_MENU
    unsigned ovl=menu?0u:__atomic_load_n(&a21_overlay,__ATOMIC_ACQUIRE);
#else
    const unsigned ovl=0;
#endif
    if (!menu && !enabled && !ovl) return 0;
    int do_fg=enabled && !menu;
    if (__atomic_exchange_n(&a20_busy,1u,__ATOMIC_ACQUIRE)) return 0;
    const char *skip_reason="validation_or_slot";
    // Presentation plan: QUALITY = one mid at t=0.5 (A20). FAST (S8/S9) asks the scheduler how many outputs this
    // game frame deserves at the display refresh; a plan of 0 mids presents the native frame only.
    unsigned mids=do_fg?1u:0u;uint32_t t_q16[2]={32768u,32768u};
#if A21_SCHED
    FgSchedule plan={0};int planned=0;
    if (__atomic_exchange_n(&a21_sched_reset,0,__ATOMIC_ACQ_REL)) a21_sched=(FgScheduler){0};
#endif
    // Slow game frames are telemetry only: never suspend or persist FG OFF.
    if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)!=2) goto skip;
    ++a20_eligible; // Forced fallback was covered in A9-A12; measure natural pacing here.
    unsigned slot=a20_copies%A20_SLOTS;
    uint8_t *sb=a20_gpu+A20_SLOT_BASE+slot*A20_SLOT_BYTES;
    volatile uint32_t *label=(volatile uint32_t *)(sb+0x280);
    VoFlipStatus fs;
    skip_reason="flip_status";
    if (HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),a20_handle,&fs)!=0) goto skip;
    skip_reason="slot_busy";
    if ((a20_valid_slots&(1u<<slot)) && (*label!=a20_slot_seq[slot] || *(volatile uint32_t *)(sb+0x298)!=a20_slot_seq[slot] || fs.flipArg<=a20_slot_arg[slot])) goto skip;
#if A21_SCHED
    if (do_fg && __atomic_load_n(&a21_preset,__ATOMIC_ACQUIRE)==1u) {
        FgScheduleInput in={.preset=FG_FAST,.period_us=g_ema_frame_us,.tick_us=16683,.preference_on=1,
            .available_mids=A21_NMIDS,.free_flip_entries=0,.gpu_budget_ok=1,.source_ready=1};
        // Our batch needs mids+1 flip entries; the queue depth limit 4 is an assumption (independent model).
        in.free_flip_entries=(fs.flipPendingNum<0 || fs.flipPendingNum>=4)?0u:4u-(uint32_t)fs.flipPendingNum;
        fg_schedule(&a21_sched,&in,&plan);planned=1;
        mids=plan.mids;
        for (unsigned k=0;k<mids && k<2;k++) t_q16[k]=plan.t_q16[k];
        if (!mids) {
            fg_schedule_commit(&a21_sched,&plan,1);planned=0;do_fg=0;
            if (!ovl) {__atomic_store_n(&a20_busy,0,__ATOMIC_RELEASE);return 0;}
        }
    }
#endif
    uint32_t off=0,tail_dw=0,last=count-1;
    for (unsigned i=0;i<count;i++) {
        // A22: large single-call titles (RE2: up to ~220 KB per DCB) get a 1 MiB limit; REF keeps 256 KiB.
        const uint32_t a22_dcb_max=(A22_UNIVERSAL && strcmp(g_title_id,"CUSA07439")!=0)?0x100000u:0x40000u;
        if (ccb && csizes && ccb[i] && csizes[i]) {skip_reason="ccb_present";goto a22_skip_dcb;}
        if (!sizes[i] || (sizes[i]&3u) || sizes[i]>a22_dcb_max) {skip_reason="dcb_size";goto a22_skip_dcb;}
        if (!dcb_mem_cached((uintptr_t)dcb[i],sizes[i])) {skip_reason="dcb_unreadable";goto a22_skip_dcb;}
        continue;
    a22_skip_dcb:
        {
            static unsigned a22_dcb_logs;
            if (a22_dcb_logs++<12) lg("A22_DCB_SKIP reason=%s entry=%u/%u size=0x%x ccb=%p csize=0x%x\n",skip_reason,i,count,
                sizes[i],ccb?ccb[i]:NULL,(ccb && csizes)?csizes[i]:0u);
        }
        goto skip;
    }
    int a21_old_ok=find_flip_tail_pm4((const uint32_t *)dcb[last],sizes[last]/4,&off,&tail_dw);
#if A21_DISCOVERY
    {
        uint32_t so=0,st=0,mk=0,tr=0;
        int s_ok=a21_find_flip_tail_struct((const uint32_t *)dcb[last],sizes[last]/4,&so,&st,&mk,&tr);
        if (a21_old_ok) a21_tail_old_ok++;
        if (s_ok) {
            a21_tail_struct_ok++;if (tr) a21_tail_trailing++;
            if ((mk&0xffu)==0x77u) a21_tail_m77++;else if ((mk&0xffu)==0x78u) a21_tail_m78++;
        }
        if (s_ok!=a21_old_ok || (s_ok && (so!=off || st!=tail_dw))) a21_tail_disagree++;
    }
#endif
    skip_reason="flip_tail_unknown";
    if (!a21_old_ok || !off) goto skip;
#if A21_FAST540
    // A second mid replays the flip tail once more. Only the plain SDK placeholder (64 dwords, 0x68750777, no label or
    // interrupt variant) is known to be side-effect free to replay (independent model); otherwise degrade to one mid.
    if (mids>1 && (tail_dw!=64u || ((uint32_t *)dcb[last])[off+1]!=0x68750777u)) {
        mids=1;t_q16[0]=32768u;
        static unsigned a21_tail_degrade;
        if (a21_tail_degrade++<4) lg("A21_FAST_DEGRADE reason=flip_tail_not_plain tail_dw=%u marker=0x%08x\n",tail_dw,((uint32_t *)dcb[last])[off+1]);
    }
#endif
    // Require complete current-call knowledge of every overwritten compute register.
    // Do not guess default values, follow unreadable IBs or trust memory-loaded state.
    a20_shadow.mode=0;a20_shadow.readable=a20_readable;a20_shadow.clock=a20_clock;
    // A22: single-call titles (RE2 ~55k dwords, ~900 draws) need a larger walk budget; REF keeps 1.5 ms / 256K dwords.
    if (A22_UNIVERSAL && strcmp(g_title_id,"CUSA07439")!=0) {a20_shadow.budget_us=4000;a20_shadow.max_dwords=0x100000u;}
    else {a20_shadow.budget_us=1500;a20_shadow.max_dwords=0x40000u;}
    pm4_frame_begin(&a20_shadow);pm4_call_begin(&a20_shadow);
    for (unsigned i=0;i<count;i++) if (pm4_walk_entry(&a20_shadow,(const uint32_t *)dcb[i],sizes[i]/4,(int32_t)i)!=PM4_OK) break;
    const uint32_t a21_full=(1u<<PM4_NCS)-1u;
    int a21_clean=!a20_shadow.abort && !a20_shadow.ib_unreadable && !a20_shadow.loads && !a20_shadow.type0;
#if A21_DISCOVERY
    if (a21_clean) {
        if (!a20_shadow.cs_first_set) a21_cs_nodispatch++;
        else if (a20_shadow.cs_first==a21_full) a21_cs_first_full++;
        else a21_cs_first_partial++;
    }
#endif
    int a21_carry=0;
#if A21_CARRY_SHADOW
    // The register shadow persists across calls (pm4_frame_begin keeps sh[]). A call that writes no compute register
    // and dispatches nothing leaves the hardware state of the previous fully known flip call unchanged, PROVIDED no
    // other submit (counted by the pass-through hook) ran on the ring in between.
#if A21_SIDE_WALK
    // Every register is known: previous fully known flip + cleanly walked intermediate submits (folded) + this call.
    if (a21_clean && a20_shadow.cs_seen!=a21_full) {
        if (a21_prev_full_known) {a21_carry=1;a21_carry_used++;} else a21_carry_refused++;
    }
#else
    if (a21_clean && a20_shadow.cs_seen==0 && !a20_shadow.dispatches && a21_prev_full_known) {
        if (__atomic_load_n(&a21_nonflip_submits,__ATOMIC_ACQUIRE)==a21_submits_at_prev) {a21_carry=1;a21_carry_used++;}
        else a21_carry_refused++;
    }
#endif
#endif
    if (!a21_clean || (a20_shadow.cs_seen!=a21_full && !a21_carry)) {
#if A21_DISCOVERY
        if (a20_shadow.dispatches) a21_skip_dispatch++;else a21_skip_nodispatch++;
#endif
        skip_reason="compute_shadow_unknown";
        if (a20_skips<4 || (a20_skips%120u)==0)
            lg("A20_SHADOW_SKIP known=0x%x abort=%d ib_unreadable=%u loads=%u type0=%u dwords=%u us=%u dispatches=%u draws=%u prev_known=%d\n",
               a20_shadow.cs_seen,a20_shadow.abort,a20_shadow.ib_unreadable,a20_shadow.loads,a20_shadow.type0,a20_shadow.dwords,a20_shadow.us,
               a20_shadow.dispatches,a20_shadow.draws,a21_prev_full_known);
        goto skip;
    }
    // Hardware compute state after this call equals the shadow (game's own writes or carried state), whether or not
    // we inject: our job restores the same values.
    a21_known_call_id=a21_this_call;a21_submits_at_prev=__atomic_load_n(&a21_nonflip_submits,__ATOMIC_ACQUIRE);
    if (a21_carry && (a21_carry_used<=4 || a21_carry_used%120u==0))
        lg("A21_CARRY_SHADOW used=%u refused=%u dwords=%u\n",a21_carry_used,a21_carry_refused,a20_shadow.dwords);
    a20_collect_slot(slot);
    uint32_t *table=(uint32_t *)sb,*seed=(uint32_t *)(sb+0x1a00),*cmd=(uint32_t *)(sb+0x400),*p=cmd;
    uint32_t seq=a20_copies+1;
#if A22_UNIVERSAL
    if (a22_prev_idx<0 || a22_prev_idx>=a22_mid || a22_prev_idx==(int32_t)index) {skip_reason="no_previous_frame";goto skip;}
    uint64_t previous=g_buf[a22_prev_idx],current=g_buf[index];
    if (!previous || previous==current || g_ngame!=a22_mid) {skip_reason="source_pair_invalid";goto skip;}
#else
    uint64_t previous=g_buf[index^1u],current=g_buf[index];
    if (!previous || previous==current || g_ngame!=2) {skip_reason="source_pair_invalid";goto skip;}
#endif
    // Both source buffers are read before I(arg) can release the previous one.
    // Rebuild both luma pyramids each job; skipped frames cannot leave stale history.
    memset(sb+0x1c0,0,0x40);memset(sb+0x300,0,7*8);
    a20_table(table,previous,current,sb,1);a20_table(seed,previous,previous,sb,0);
#if A21_SCHED
    if (do_fg) table[29]=2u|(t_q16[0]<<16);   // mid index 2 | phase override (QUALITY: 0x8000 = A20 default)
#endif
#if A21_MENU
    unsigned ovx=0,ovy=0,ovrows=0;
    if (ovl) { // the counter is drawn after the FG passes; P carries last frame's copy, so the warp skips its rectangle
        a21_overlay_rect(ovl,&ovx,&ovy,&ovrows);
        table[138]=ovx|(ovy<<16);table[139]=vov_wh(A21_OVL_W,A21_OVL_H,255u,0u);
    }
#endif
#if A21_FAST540
    uint32_t *second=(uint32_t *)(sb+A21_SECOND_TB);
    if (do_fg && mids>1) {
        memcpy(second,table,0x190);
        make_t(second+16,(uint64_t)(uintptr_t)(a20_image+A21_MID_BYTES),A21_MID_W,A21_MID_H,A21_MID_W,8);
        second[29]=3u|0x8000u|(t_q16[1]<<16); // mid index 3, do not overwrite the frame statistics
    }
#endif
    uint64_t lbl=(uint64_t)(uintptr_t)label,ready=lbl+8,tbl=(uint64_t)(uintptr_t)table;
    *label=0;
    *(volatile uint32_t *)(sb+0x298)=0;
    *(volatile uint32_t *)(uintptr_t)ready=0;
    *p++=PKT3(0x46,1,0);*p++=0x2e;
    *p++=PKT3(0x46,1,0);*p++=0x407;
    *p++=PKT3(0x47,5,0);*p++=0x528;*p++=(uint32_t)ready;*p++=0x40000000u|((uint32_t)(ready>>32)&0xffffu);*p++=seq;*p++=0;
    *p++=PKT3(0x3c,6,0);*p++=0x13;*p++=(uint32_t)ready;*p++=(uint32_t)(ready>>32);*p++=seq;*p++=0xffffffff;*p++=0xa;
    *p++=PKT3(0x58,6,0);*p++=0x82003fc0;*p++=(g_pitch*((g_bh+15u)&~15u)*4u)>>8;*p++=0;*p++=(uint32_t)(g_buf[index]>>8);*p++=(uint32_t)(g_buf[index]>>40);*p++=0xa;
    *p++=PKT3(0x58,6,0);*p++=0x80c40000;*p++=0xffffffff;*p++=0;*p++=0;*p++=0;*p++=0xa;
    *p++=PKT3(0x58,6,0);*p++=0x88400000;*p++=1;*p++=0;*p++=0;*p++=0;*p++=0xa;
    a20_timestamp(&p,sb,0);
    int wr=0;
#if A21_WAIT_LATE
    // Only warp720 writes I; pyramids/motion touch work memory. In the menu path osd8 writes C, not I, so the
    // wait is not needed there; it is kept before warp720 only.
    if (!do_fg) {
#endif
    wr=sceGnmInsertWaitFlipDone(p,7,a20_handle,a22_mid);
    if (!a20_copies) lg("A20_WAIT_TARGET index=2 rc=0x%08x packet=%08x,%08x,%08x,%08x,%08x,%08x,%08x\n",
        (unsigned)wr,p[0],p[1],p[2],p[3],p[4],p[5],p[6]);
    if (wr) { a20_begin_stop("wait_builder_failed");goto skip; }p+=7;
#if A21_WAIT_LATE
    }
#endif
#if A21_FAST540
    if (do_fg && mids>1) { // the second mid surface may still be on screen from the previous job
        wr=sceGnmInsertWaitFlipDone(p,7,a20_handle,3);
        if (wr) { a20_begin_stop("wait3_builder_failed");goto skip; }p+=7;
    }
#endif
    a20_timestamp(&p,sb,1);
    *p++=PKT3(0x76,4,1);*p++=0x207;*p++=64;*p++=1;*p++=1;
    if (do_fg) {
    uint64_t seed_address=(uint64_t)(uintptr_t)seed;
    *p++=PKT3(0x76,3,1);*p++=0x240;*p++=(uint32_t)seed_address;*p++=(uint32_t)(seed_address>>32);
    a20_pass(&p,0,PYR8_RSRC1,PYR8_RSRC2,30,68);
    a20_pass(&p,9,SUM_PREVIOUS_RSRC1,SUM_PREVIOUS_RSRC2,1,1);
    *p++=PKT3(0x58,6,0);*p++=0x88400000;*p++=1;*p++=0;*p++=0;*p++=0;*p++=0xa;
    *p++=PKT3(0x76,3,1);*p++=0x240;*p++=(uint32_t)tbl;*p++=(uint32_t)(tbl>>32);
    a20_pass(&p,0,PYR8_RSRC1,PYR8_RSRC2,30,68);
    a20_timestamp(&p,sb,2);
    a20_pass(&p,1,ME3_RSRC1,ME3_RSRC2,30,17);
    a20_pass(&p,2,ME2_RSRC1,ME2_RSRC2,15,34);
    a20_pass(&p,3,GM8_RSRC1,GM8_RSRC2,1,1);
    a20_pass(&p,4,FGS8_RSRC1,FGS8_RSRC2,32,1);
    a20_pass(&p,5,ME1_RSRC1,ME1_RSRC2,30,68);
    a20_pass(&p,6,MF8_RSRC1,MF8_RSRC2,30,68);
    a20_timestamp(&p,sb,3);
    a20_pass(&p,7,PHASE8_RSRC1,PHASE8_RSRC2,1,1);
    a20_timestamp(&p,sb,4);
#if A21_WAIT_LATE
    // gpu_warp_done-gpu_phase_done now includes the scanout wait; timestamp 6 marks the end of the wait.
    wr=sceGnmInsertWaitFlipDone(p,7,a20_handle,a22_mid);
    if (!a20_copies) lg("A21_WAIT_LATE index=2 rc=0x%08x\n",(unsigned)wr);
    if (wr) { a20_begin_stop("wait_builder_failed");goto skip; }p+=7;
    a20_timestamp(&p,sb,6);
#endif
#if A21_FAST540
    a20_pass(&p,8,WARP540_RSRC1,WARP540_RSRC2,A21_MID_W/8u,(A21_MID_H+7u)/8u);
    if (mids>1) { // second mid: same flow field, phase t2 (phase8 + warp540 on the second table); P/C reads end here
        uint64_t sa=(uint64_t)(uintptr_t)second;
        *p++=PKT3(0x76,3,1);*p++=0x240;*p++=(uint32_t)sa;*p++=(uint32_t)(sa>>32);
        a20_pass(&p,7,PHASE8_RSRC1,PHASE8_RSRC2,1,1);
        a20_pass(&p,8,WARP540_RSRC1,WARP540_RSRC2,A21_MID_W/8u,(A21_MID_H+7u)/8u);
    }
#else
    a20_pass(&p,8,WARP720_RSRC1,WARP720_RSRC2,160,90);
#endif
#if A21_POST
    {   // Generated surfaces; the native frame C is post-processed below, after the FG passes have read it.
        unsigned sharp=__atomic_load_n(&a21_sharp,__ATOMIC_ACQUIRE),en=__atomic_load_n(&a21_enhance,__ATOMIC_ACQUIRE);
        if (sharp || en) {
            a21_emit_post(&p,sb+A21_POST_TB,(uint64_t)(uintptr_t)a20_image,sharp,en);
#if A21_FAST540
            if (mids>1) a21_emit_post(&p,sb+A21_POST2_TB,(uint64_t)(uintptr_t)(a20_image+A21_MID_BYTES),sharp,en);
#endif
            a21_post_jobs++;
        }
    }
#endif
    }
#if A21_POST
    if (!menu) { // native frame C gets the same AA / sharpening as the mids (after the FG passes read it)
        unsigned sharp=__atomic_load_n(&a21_sharp,__ATOMIC_ACQUIRE),en=__atomic_load_n(&a21_enhance,__ATOMIC_ACQUIRE);
        if (sharp || en) a21_emit_post_wh(&p,sb+A21_POSTC_TB,current,g_bw,g_bh,g_pitch,g_attr.tilingMode==0?10:8,sharp,en);
    }
#endif
#if A21_MENU
    if (ovl && !menu) {a21_emit_overlay(&p,sb,current,ovl,(unsigned)do_fg,mids);a21_overlay_jobs++;}
#endif
    if (menu && !do_fg) {
        a20_build_menu(table,sb);
        *p++=PKT3(0x76,3,1);*p++=0x240;*p++=(uint32_t)tbl;*p++=(uint32_t)(tbl>>32);
        if (table[139]) a20_pass(&p,10,OSD8_RSRC1,OSD8_RSRC2,(table[139]&255u)*2u,((table[139]>>8)&255u)*2u);
    }
    a20_timestamp(&p,sb,5);
    *p++=PKT3(0x58,6,0);*p++=0x80c40000;*p++=0xffffffff;*p++=0;*p++=0;*p++=0;*p++=0xa;
    *p++=PKT3(0x47,5,0);*p++=0x528;*p++=(uint32_t)lbl;*p++=0x40000000u|((uint32_t)(lbl>>32)&0xffffu);*p++=seq;*p++=0;
    *p++=PKT3(0x3c,6,0);*p++=0x13;*p++=(uint32_t)lbl;*p++=(uint32_t)(lbl>>32);*p++=seq;*p++=0xffffffff;*p++=0xa;
    for (unsigned i=0;i<PM4_NCS;i++) { *p++=PKT3(0x76,2,1);*p++=pm4_cs_reg_idx[i];*p++=a20_shadow.sh[pm4_cs_reg_idx[i]]; }
    for (unsigned i=0;i<tail_dw;i++) *p++=((uint32_t *)dcb[last])[off+i];
    if ((size_t)(p-cmd)*4u>0x1000u) { a20_begin_stop("command_overflow");goto skip; }
    uint32_t *native=(uint32_t *)(sb+0x1600);
    memcpy(native,(uint32_t *)dcb[last]+off,tail_dw*4u);
    uint32_t *queue_fence=(uint32_t *)(sb+0x1800),*queue_fence_end=queue_fence;
    *queue_fence_end++=PKT3(0x10,1,0);*queue_fence_end++=0;
    a20_eop_marker(&queue_fence_end,(uint64_t)(uintptr_t)(sb+0x298),seq);
    a20_times[(seq-1)%A20_PAIR_LIMIT]=(A18Timing){.submit_cpu=sceKernelGetProcessTime(),.arg=arg,.seq=seq,.slot=slot,.kind=do_fg?1u:0u};
    __asm__ volatile("sfence" ::: "memory");
    void *d[17],*c[17];uint32_t z[17],cz[17];
    for (unsigned i=0;i<count;i++) {d[i]=dcb[i];z[i]=sizes[i];c[i]=ccb?ccb[i]:NULL;cz[i]=csizes?csizes[i]:0;}
    z[last]=off*4u;d[count]=cmd;z[count]=(uint32_t)(p-cmd)*4u;c[count]=NULL;cz[count]=0;
    if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)!=2) goto skip;
    a20_valid_slots|=1u<<slot;a20_slot_seq[slot]=seq;a20_slot_arg[slot]=arg;a20_last_arg=arg;
    *result=HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
        int32_t (*)(uint32_t,void **,uint32_t *,void **,uint32_t *,uint32_t,uint32_t,uint32_t,int64_t),
        count+1,d,z,ccb?c:NULL,csizes?cz:NULL,handle,do_fg?(uint32_t)a22_mid:index,mode,arg);
    int native_rc=*result,probe_rc=0;
#if A21_FAST540
    // I1(arg) -> I2(arg) -> N(arg): every read of P/C finished in the first command buffer, before I1 releases P.
    // Not atomic: if I2 or N is refused after I1 was accepted the game stream cannot be resent -> stop and drain.
    if (!*result && do_fg && mids>1) {
        uint32_t *mt=(uint32_t *)(sb+A21_MID_TAIL);memcpy(mt,(uint32_t *)dcb[last]+off,tail_dw*4u);
        __asm__ volatile("sfence" ::: "memory");
        void *md[1]={mt};uint32_t ms[1]={tail_dw*4u};
        native_rc=HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
            int32_t (*)(uint32_t,void **,uint32_t *,void **,uint32_t *,uint32_t,uint32_t,uint32_t,int64_t),
            1,md,ms,NULL,NULL,handle,3,mode,arg);
    }
    if (!*result && !native_rc && do_fg) {
#else
    if (!*result && do_fg) {
#endif
        void *nd[1]={native};uint32_t ns[1]={tail_dw*4u};
        native_rc=HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
            int32_t (*)(uint32_t,void **,uint32_t *,void **,uint32_t *,uint32_t,uint32_t,uint32_t,int64_t),
            1,nd,ns,NULL,NULL,handle,index,mode,arg);
    }
    if (!*result && !native_rc) {
        void *qd[1]={queue_fence};uint32_t qs[1]={(uint32_t)(queue_fence_end-queue_fence)*4u};
        // Our own fence submit passes through the counting hook; it changes no compute register, so exclude it.
        a21_own_submit=1;
        probe_rc=sceGnmSubmitCommandBuffers(1,qd,qs,NULL,NULL);
        a21_own_submit=0;
    }

    a20_copies++;
    if (do_fg) {a20_fg_pairs++;a21_total_mids+=mids;} else a20_ui_frames++;
#if A21_SCHED
    if (planned) {
        fg_schedule_commit(&a21_sched,&plan,(!*result && !native_rc && !probe_rc)?mids+1u:0u); // a degraded batch resets the carry
        if (seq<=8 || seq%120==0) lg("A21_PLAN seq=%u period_us=%u outputs=%u desired=%u mids=%u t0=%u t1=%u reason=%s total_mids=%llu\n",
            seq,g_ema_frame_us,plan.outputs,plan.desired_outputs,mids,t_q16[0],t_q16[1],fg_reason(plan.reason),
            (unsigned long long)a21_total_mids);
    }
#endif
    if (seq<=120 || seq%120==0 || *result || native_rc || probe_rc) lg("A20_JOB seq=%u scene=%u arg=%lld source_idx=%u kind=%u native_flip=%u first_rc=0x%08x native_rc=0x%08x fence_rc=0x%08x restore_known=0x%x\n",
       seq,a20_scene,(long long)arg,index,do_fg?1u:0u,index,(unsigned)*result,(unsigned)native_rc,(unsigned)probe_rc,a20_shadow.cs_seen);
    if (*result || native_rc || probe_rc) a20_begin_stop("pair_submit_failed");

    __atomic_store_n(&a20_busy,0,__ATOMIC_RELEASE);

    return 1;
skip:
#if A21_SCHED
    if (planned) fg_schedule_commit(&a21_sched,&plan,0); // no speculative credit after a native fallback
#endif
    a20_skips++;
    if (a20_skips<16 || a20_skips%120==0) lg("A20_NATIVE_ONLY scene=%u arg=%lld index=%u reason=%s skips=%u\n",a20_scene,(long long)arg,index,skip_reason,a20_skips);
    __atomic_store_n(&a20_busy,0,__ATOMIC_RELEASE);return 0;
}
static int a20_valve_worker_tick(uint64_t now)
{
    if (!__atomic_load_n(&a20_valve_requested,__ATOMIC_ACQUIRE)) return 0;
    if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)!=3) return 1;
    if (__atomic_load_n(&a20_busy,__ATOMIC_ACQUIRE)) return 1;
    if (!a20_stop_at) a20_stop_at=now;
    int gpu_done=1,queue_done=1;
    for (unsigned i=0;i<A20_SLOTS;i++) if (a20_valid_slots&(1u<<i)) {
        uint8_t *sb=a20_gpu+A20_SLOT_BASE+i*A20_SLOT_BYTES;
        if (*(volatile uint32_t *)(sb+0x280)!=a20_slot_seq[i]) gpu_done=0;
        if (*(volatile uint32_t *)(sb+0x298)!=a20_slot_seq[i]) queue_done=0;
    }
    VoFlipStatus fs={0};
    int fr=HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),a20_handle,&fs);
    int native=!fr && fs.currentBuffer>=0 && fs.currentBuffer<a22_mid && fs.flipArg>=a20_last_arg && fs.flipPendingNum==0;
    int observed=native && __atomic_load_n(&a20_native_arg,__ATOMIC_ACQUIRE)>=a20_last_arg &&
        __atomic_load_n(&a20_native_count,__ATOMIC_ACQUIRE)>=fs.count;
    int label_clear=a20_label_readable && *a20_driver_label==0 && (!a21_label3 || *a21_label3==0);
    if (!gpu_done || !queue_done || !native || !observed || !label_clear) {
        if (now>a20_stop_at+4000000ull) {
            // Keep mappings owned. A failed wait is never permission to free them.
            a20_retained=1;__atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);
            lg("A20_VALVE_RETAIN gpu=%d queue=%d native=%d observer=%d label_clear=%d\n",gpu_done,queue_done,native,observed,label_clear);
        }
        return 1;
    }
    __atomic_store_n(&a20_observer_stop,1,__ATOMIC_RELEASE);
    if (a20_thread_started && !__atomic_load_n(&a20_observer_done,__ATOMIC_ACQUIRE)) return 1;
    for (unsigned i=0;i<A20_SLOTS;i++) a20_collect_slot(i);
    a20_urgent_io=1;a20_save_observations();a20_urgent_io=0;
    if (a20_retained) {__atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return 1;}
    lg("A20_VALVE_DRAIN gpu=1 post_native_queue_fence=1 native=1 observer=1 pending=0 label_clear=1 count=%llu arg=%lld\n",
        (unsigned long long)fs.count,(long long)fs.flipArg);
    int ur=sceVideoOutUnregisterBuffers(a20_handle,a20_group);
    lg("A20_VALVE_UNREGISTER group=%d rc=0x%08x\n",a20_group,(unsigned)ur);
    if (ur<0) {a20_retained=1;__atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return 1;}
    a20_group=-1;
    int um=sceKernelMunmap(a20_gpu,A20_TOTAL_BYTES);
    int rr=um?um:sceKernelReleaseDirectMemory(a20_phys,A20_TOTAL_BYTES);
    lg("A20_VALVE_UPPER_RELEASE unmap_rc=0x%08x release_rc=0x%08x\n",(unsigned)um,(unsigned)rr);
    if (um || rr) {a20_retained=1;__atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return 1;}
    a20_gpu=NULL;a20_image=NULL;
    if (a20_release_low()!=0) {__atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return 1;}
    a20_logs_pending=1;
    lg("A20_VALVE_RELEASED elapsed_us=%llu disabled_until_exit=1\n",(unsigned long long)(sceKernelGetProcessTime()-a20_valve_start));
    __atomic_store_n(&a20_valve_success,1,__ATOMIC_RELEASE);
    __atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);
    return 1;
}

static size_t a20_dump_file(const char *name,const void *data,size_t bytes)
{
    char path[160];snprintf(path,sizeof(path),LOG_DIR "/%s",name);
    FILE *f=fopen(path,"wb");if (!f) return 0;
    size_t n=fwrite(data,1,bytes,f);if (fclose(f)) return 0;return n;
}
static void a20_capture_tick(uint64_t now)
{
    if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)!=2 || __atomic_load_n(&a20_valve_requested,__ATOMIC_ACQUIRE)) {
        __atomic_store_n(&a20_capture_gate,0,__ATOMIC_RELEASE);return;
    }
    if (!__atomic_load_n(&a20_capture_gate,__ATOMIC_ACQUIRE)) {
        if (now<a20_capture_poll) return;
        a20_capture_poll=now+1000000ull;
        FILE *request=fopen(LOG_DIR "/a20_capture","r");
        int manual=request!=NULL;if (request) {fclose(request);remove(LOG_DIR "/a20_capture");}
        if (!manual && (a20_auto_captured || a20_fg_pairs<300 ||
            !__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE) || __atomic_load_n(&a20_menu_open,__ATOMIC_ACQUIRE))) return;
        a20_auto_captured=1;a20_capture_started=now;
        __atomic_store_n(&a20_snapshot_request,1,__ATOMIC_RELEASE);
        __atomic_store_n(&a20_capture_gate,1,__ATOMIC_RELEASE);
        lg("A20_CAPTURE_BEGIN manual=%d fg_pairs=%u short_native_fallback=1\n",manual,a20_fg_pairs);
    }
    if (now>a20_capture_started+500000ull) {
        __atomic_store_n(&a20_capture_gate,0,__ATOMIC_RELEASE);
        lg("A20_CAPTURE_ABORT reason=drain_or_observer_timeout no_free=1\n");return;
    }
    if (__atomic_load_n(&a20_snapshot_request,__ATOMIC_ACQUIRE)!=2) return;
    unsigned unlocked=0;
    if (!__atomic_compare_exchange_n(&a20_busy,&unlocked,1,0,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) return;
    int done=1;
    for (unsigned i=0;i<A20_SLOTS;i++) if (a20_valid_slots&(1u<<i)) {
        uint8_t *sb=a20_gpu+A20_SLOT_BASE+i*A20_SLOT_BYTES;
        if (*(volatile uint32_t *)(sb+0x280)!=a20_slot_seq[i] ||
            *(volatile uint32_t *)(sb+0x298)!=a20_slot_seq[i]) done=0;
    }
    if (!done) {__atomic_store_n(&a20_busy,0,__ATOMIC_RELEASE);return;}
    // No injected jobs can write image/work/timing slots until gate is released.
    for (unsigned i=0;i<A20_SLOTS;i++) a20_collect_slot(i);
    size_t image=a20_dump_file("a20_live720.rgba",a20_image,A20_IMAGE_BYTES);
    size_t low=a20_dump_file("a20_live_work_low.bin",a20_low,A20_LOWER_WORK);
    size_t high=a20_dump_file("a20_live_work_high.bin",a20_high,A20_UPPER_WORK);
    unsigned slot=a20_copies?(a20_copies-1)%A20_SLOTS:0;
    size_t table=a20_dump_file("a20_live_slot.bin",a20_gpu+A20_SLOT_BASE+slot*A20_SLOT_BYTES,A20_SLOT_BYTES);
    FILE *f=fopen(LOG_DIR "/a20_live_scanout.csv","w");
    if (f) {
        fprintf(f,"cpu_us,count,shown_us,vblank,arg,index,pending,status_rc\n");
        for (unsigned i=0;i<a20_snapshot_count;i++) {
            A18Observation *r=&a20_snapshot[i];
            fprintf(f,"%llu,%llu,%llu,%llu,%lld,%d,%d,%d\n",(unsigned long long)r->cpu,
                (unsigned long long)r->count,(unsigned long long)r->shown,(unsigned long long)r->vblank,
                (long long)r->arg,r->index,r->pending,r->status_rc);
        }
        fclose(f);
    }
    a20_save_times();
    volatile uint32_t *misc=(volatile uint32_t *)a20_work_address(W8_MISC);
    lg("A20_SYNTH_STATE last_job_kind=%u cut_count=%u phase_bits=0x%08x previous_luma_sum=%u current_luma_sum=%u fade=%u\n",
        a20_copies?a20_times[(a20_copies-1)%A20_PAIR_LIMIT].kind:0u,misc[0],misc[1],misc[2],misc[3],misc[7]);
    lg("A20_CAPTURE_DONE seq=%u image=%zu low=%zu high=%zu slot=%zu scanout_rows=%u scanout_open=%d elapsed_us=%llu\n",
        a20_copies,image,low,high,table,a20_snapshot_count,f!=NULL,
        (unsigned long long)(sceKernelGetProcessTime()-a20_capture_started));
    __atomic_store_n(&a20_busy,0,__ATOMIC_RELEASE);
    __atomic_store_n(&a20_capture_gate,0,__ATOMIC_RELEASE);
}

static void a20_worker_tick(void)
{
    static int controller_started;
    static int stopped_notified;
    if (!controller_started) {
        controller_started=1;__atomic_store_n(&a20_io_tid,diag_thread_id(),__ATOMIC_RELEASE);remove(LOG_DIR "/a20_arm_game");
        lg("A20_RUNTIME_START menu=L3_R3_3sec resources_after=600_flips slow_frames=telemetry_only mode=quality_2x\n");
        lg("A21_BUILD tag=%s pref_split=%d discovery=%d count_submits=%d carry_shadow=%d wait_late=%d slab_map=%d "
           "slab2=%d post=%d menu=%d sched=%d fast540=%d mids=%u mid=%ux%u\n",A21_BUILD_TAG,
            A21_PREF_SPLIT,A21_DISCOVERY,A21_COUNT_SUBMITS,A21_CARRY_SHADOW,A21_WAIT_LATE,A21_SLAB_MAP,
            A21_SLAB2,A21_POST,A21_MENU,A21_SCHED,A21_FAST540,A21_NMIDS,A21_MID_W,A21_MID_H);
        a20_load_config();
    }
    int state=__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE);
    uint64_t now=sceKernelGetProcessTime();
    if (!state && g_flips>=600 && g_handle>=0 && (g_ngame==2 || (A22_UNIVERSAL && g_ngame==3))) a20_init();
    state=__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE);
    now=sceKernelGetProcessTime();
    if (state==2 && __atomic_exchange_n(&a20_pacing_warning,0,__ATOMIC_ACQ_REL)) {
        if (now>=a20_warning_log_at) {
            a20_warning_log_at=now+5000000ull;a20_warning_reports++;
            lg("A20_PACING_WARNING action=continue_fg reports=%u game_fps_x10=%u enabled=%d policy=quality_2x\n",
                a20_warning_reports,__atomic_load_n(&g_osd_real_fps_x10,__ATOMIC_ACQUIRE),
                __atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE));
        }
    }
    if (state==4 && !stopped_notified) {
        stopped_notified=1;
        NotifyStatic(TEX_ICON_SYSTEM,__atomic_load_n(&a20_valve_requested,__ATOMIC_ACQUIRE)?
            (A21_PREF_SPLIT?"VCFG paused for this session: memory needed by game. Setting kept for next start.":
                            "VCFG disabled: memory needed by game. Restart REF to enable again."):
            "VCFG unavailable in this session. Restart REF to try again.");
        lg("A21_SESSION_STOP reason=%s valve=%d user_pref_enabled=%d pref_persisted_unchanged=%d\n",a21_stop_reason,
            __atomic_load_n(&a20_valve_requested,__ATOMIC_ACQUIRE),__atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE),A21_PREF_SPLIT);
    }
    if (state==2 && __atomic_load_n(&a20_probe_unsafe,__ATOMIC_ACQUIRE)) a20_begin_stop("guard_probe_unsafe");
    if (state==2 && __atomic_load_n(&a20_observer_done,__ATOMIC_ACQUIRE)) a20_begin_stop("observer_ended");

    if ((!__atomic_load_n(&a20_valve_requested,__ATOMIC_ACQUIRE) || state==4) &&
        __atomic_exchange_n(&a20_save_config,0,__ATOMIC_ACQ_REL)) a20_write_config();
#if A21_MENU
    {   // Overlay metrics once per second: D = flips shown, U = generated surfaces shown (each a new image), R = surfaces
        // held for more than one vblank (repeats), S = native fallbacks. GPU time from timings the submit thread already
        // collected (lags up to 8 jobs); A13 calibration ~99.77 counter ticks per us.
        static uint64_t at,pi,pn,pr,ps;
        if (state==2 && now>=at+1000000ull) {
            uint64_t si=__atomic_load_n(&a20_shown_i,__ATOMIC_RELAXED),sn=__atomic_load_n(&a20_shown_n,__ATOMIC_RELAXED);
            uint64_t rp=__atomic_load_n(&a21_n_hold_repeat,__ATOMIC_RELAXED)+__atomic_load_n(&a21_i_hold_repeat,__ATOMIC_RELAXED);
            uint64_t sk=a20_skips,dt=at?now-at:0;
            if (dt) {
                __atomic_store_n(&a21_disp_x10,(uint32_t)((si+sn-pi-pn)*10000000ull/dt),__ATOMIC_RELEASE);
                __atomic_store_n(&a21_uniq_x10,(uint32_t)((si-pi)*10000000ull/dt),__ATOMIC_RELEASE);
                __atomic_store_n(&a21_rep_x10,(uint32_t)((rp-pr)*10000000ull/dt),__ATOMIC_RELEASE);
                __atomic_store_n(&a21_skip_x10,(uint32_t)((sk-ps)*10000000ull/dt),__ATOMIC_RELEASE);
            }
            for (unsigned back=0;back<A20_PAIR_LIMIT && back<a20_copies;back++) {
                const A18Timing *r=&a20_times[(a20_copies-1-back)%A20_PAIR_LIMIT];
                if (r->completed_cpu && r->kind && r->gpu[5]>r->gpu[1]) {
                    __atomic_store_n(&a21_gpu_us,(uint32_t)((r->gpu[5]-r->gpu[1])*100ull/9977ull),__ATOMIC_RELEASE);
                    break;
                }
                if (back>=16) break;
            }
            at=now;pi=si;pn=sn;pr=rp;ps=sk;
        }
    }
#endif
    if (state==2 && now>=a20_runtime_report_at) {
        a20_runtime_report_at=now+5000000ull;
        uint64_t shown_i=__atomic_load_n(&a20_shown_i,__ATOMIC_RELAXED);
        uint64_t shown_n=__atomic_load_n(&a20_shown_n,__ATOMIC_RELAXED);
        uint64_t holds=__atomic_load_n(&a20_i_holds,__ATOMIC_RELAXED);
        lg("A20_SCANOUT cpu_us=%llu window_us=%llu shown_fg_total=%llu shown_native_total=%llu fg_delta=%llu native_delta=%llu unobserved=%llu status_errors=%llu fg_hold_avg_us=%llu\n",
            (unsigned long long)now,(unsigned long long)(a20_report_at?now-a20_report_at:0),
            (unsigned long long)shown_i,(unsigned long long)shown_n,
            (unsigned long long)(shown_i-a20_report_i),(unsigned long long)(shown_n-a20_report_n),
            (unsigned long long)__atomic_load_n(&a20_unobserved,__ATOMIC_RELAXED),
            (unsigned long long)__atomic_load_n(&a20_status_errors,__ATOMIC_RELAXED),
            (unsigned long long)(holds?__atomic_load_n(&a20_i_hold_us,__ATOMIC_RELAXED)/holds:0));
        a20_report_i=shown_i;a20_report_n=shown_n;a20_report_at=now;
        a21_discovery_report(now);
        lg("A20_RUNTIME enabled=%d menu=%d jobs=%u fg_pairs=%u ui_frames=%u skips=%u recent_scanouts=%u overwritten=%u\n",
            __atomic_load_n(&a20_enabled,__ATOMIC_ACQUIRE),__atomic_load_n(&a20_menu_open,__ATOMIC_ACQUIRE),
            a20_copies,a20_fg_pairs,a20_ui_frames,a20_skips,a20_ring_count,a20_ring_dropped);
#if A21_SLAB2
        lg("A21_RUNTIME total_mids=%llu post_jobs=%llu overlay_jobs=%llu preset=%u sharp=%u enhance=%u overlay=%u\n",
            (unsigned long long)a21_total_mids,(unsigned long long)a21_post_jobs,(unsigned long long)a21_overlay_jobs,
            a21_preset,a21_sharp,a21_enhance,a21_overlay);
#endif
    }
    if (a20_logs_pending) {
        a20_logs_pending=0;a20_write_observations();a20_save_times();
        lg("A20_PHASE_DONE scene=%u retained=%d waiting_for_game_arm=0 fg_disabled_session=1\n",a20_scene,a20_retained);
    }
    if (a20_valve_worker_tick(now)) return;
    a20_capture_tick(now);
    if (__atomic_load_n(&a20_state,__ATOMIC_ACQUIRE)==3) {
        if (!a20_stop_at) {
            a20_stop_at=now;VoFlipStatus st;
            if (HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),a20_handle,&st)==0) a20_stop_count=st.count;
            lg("A20_STOP_COUNT scene=%u count=%llu last_arg=%lld\n",a20_scene,(unsigned long long)a20_stop_count,(long long)a20_last_arg);
        }
        if (__atomic_load_n(&a20_busy,__ATOMIC_ACQUIRE)) return;
        int done=1;
        for (unsigned i=0;i<A20_SLOTS;i++) if ((a20_valid_slots&(1u<<i)) &&
            *(volatile uint32_t *)(a20_gpu+A20_SLOT_BASE+i*A20_SLOT_BYTES+0x280)!=a20_slot_seq[i]) done=0;
        for (unsigned i=0;i<A20_SLOTS;i++) if ((a20_valid_slots&(1u<<i)) &&
            *(volatile uint32_t *)(a20_gpu+A20_SLOT_BASE+i*A20_SLOT_BYTES+0x298)!=a20_slot_seq[i]) done=0;
        VoFlipStatus fs;int fr=HOOK_CONTINUE(sceVideoOutGetFlipStatus,int32_t (*)(int32_t,VoFlipStatus *),a20_handle,&fs);
        int tail_done=!a20_copies || (!fr && fs.flipArg>a20_last_arg && fs.currentBuffer>=0 && fs.currentBuffer<a22_mid);
        int observer_native=!a20_copies || (__atomic_load_n(&a20_native_arg,__ATOMIC_ACQUIRE)>a20_last_arg &&
            __atomic_load_n(&a20_native_count,__ATOMIC_ACQUIRE)>a20_stop_count);
        int label_clear=(!a20_label_readable || *a20_driver_label==0) && (!a21_label3 || *a21_label3==0);
        int probe_done=!__atomic_load_n(&a20_probe_submitted,__ATOMIC_ACQUIRE) ||
            *a20_marker(0x2008)==__atomic_load_n(&a20_probe_seq,__ATOMIC_ACQUIRE);
        int safe=done && tail_done && observer_native && label_clear && probe_done;
        if ((safe && now>a20_stop_at+2000000ull) || now>a20_stop_at+4000000ull) {
            __atomic_store_n(&a20_observer_stop,1,__ATOMIC_RELEASE);
            if (a20_thread_started && !__atomic_load_n(&a20_observer_done,__ATOMIC_ACQUIRE)) return;
            a20_save_observations();
            a20_save_times();
            lg("A20_DRAIN_CHECK scene=%u gpu_done=%d later_native=%d observer_native=%d label_clear=%d probe_done=%d\n",
               a20_scene,done,tail_done,observer_native,label_clear,probe_done);
            lg("A20_PROBE_RESULT scene=%u submitted=%d seq=%u blocked_observed=%d unsafe=%d first_done_cpu_us=%llu\n",
               a20_scene,a20_probe_submitted,a20_probe_seq,a20_probe_blocked_seen,a20_probe_unsafe,(unsigned long long)a20_probe_first_done_cpu);
            if (!safe) {
                a20_retained=1;
                lg("A20_RETAIN scene=%u copies=%u; no unsafe free, next phase disabled\n",a20_scene,a20_copies);
                __atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return;
            }
            char path[160];snprintf(path,sizeof(path),LOG_DIR "/a20_copy720_%u.rgba",a20_scene);
            FILE *f=fopen(path,"wb");
            size_t written=0;if (f) {written=fwrite(a20_image,1,A20_IMAGE_BYTES,f);fclose(f);}
            size_t sentinel=0,nonzero=0;
            for (size_t i=0;i<A20_IMAGE_BYTES/4u;i++) {
                uint32_t v=((volatile uint32_t *)a20_image)[i];
                if (v==0xcdcdcdcdu) sentinel++;
                if (v) nonzero++;
            }
            lg("A20_IMAGE words=%u sentinel_words=%zu nonzero_words=%zu\n",A20_IMAGE_BYTES/4u,sentinel,nonzero);
            lg("A20_DRAINED copies=%u skips=%u gpu_done=1 later_native_shown=1 snapshot_bytes=0x%zx\n",a20_copies,a20_skips,written);
            snprintf(path,sizeof(path),LOG_DIR "/a20_work_low_%u.bin",a20_scene);
            f=fopen(path,"wb");size_t low_written=0;
            if (f) {low_written=fwrite(a20_low,1,A20_LOWER_WORK,f);fclose(f);}
            snprintf(path,sizeof(path),LOG_DIR "/a20_work_high_%u.bin",a20_scene);
            f=fopen(path,"wb");size_t high_written=0;
            if (f) {high_written=fwrite(a20_high,1,A20_UPPER_WORK,f);fclose(f);}
            unsigned last_slot=a20_copies?(a20_copies-1)%A20_SLOTS:0;
            uint8_t *last_sb=a20_gpu+A20_SLOT_BASE+last_slot*A20_SLOT_BYTES;
            snprintf(path,sizeof(path),LOG_DIR "/a20_last_table_%u.bin",a20_scene);
            f=fopen(path,"wb");size_t table_written=0;
            if (f) {table_written=fwrite(last_sb,1,0x190,f);fclose(f);}
            lg("A20_QUALITY_SNAPSHOT last_seq=%u last_arg=%lld low_bytes=0x%zx high_bytes=0x%zx table_bytes=0x%zx layout=fullscreen720_oldwarp\n",
                a20_copies,(long long)a20_last_arg,low_written,high_written,table_written);
            int ur=sceVideoOutUnregisterBuffers(a20_handle,a20_group);
            lg("A20_UNREGISTER group=%d rc=0x%08x\n",a20_group,(unsigned)ur);
            if (ur<0) {a20_retained=1;__atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);return;}
            a20_group=-1;
            int um=sceKernelMunmap(a20_gpu,A20_TOTAL_BYTES);
            int rr=um?um:sceKernelReleaseDirectMemory(a20_phys,A20_TOTAL_BYTES);
            lg("A20_RELEASE unmap_rc=0x%08x release_rc=0x%08x owned_bytes=0x%x\n",(unsigned)um,(unsigned)rr,(um||rr)?A20_TOTAL_BYTES:0);
            if (!um && !rr) {a20_gpu=NULL;a20_image=NULL;a20_release_low();} else a20_retained=1;
            lg("A20_PHASE_DONE scene=%u retained=%d waiting_for_game_arm=%d\n",a20_scene,a20_retained,a20_scene==1 && !a20_retained);
            if (__atomic_load_n(&a20_valve_requested,__ATOMIC_ACQUIRE) && !a20_retained && !a20_gpu && !a20_low_owned)
                __atomic_store_n(&a20_valve_success,1,__ATOMIC_RELEASE);
            __atomic_store_n(&a20_state,4,__ATOMIC_RELEASE);
        }
    }
}
