#include "fg_core.h"
#include <string.h>
#include <limits.h>

static int pow2(uint64_t x) { return x && !(x&(x-1)); }
static int align_up(uint64_t x,uint64_t a,uint64_t *out) {
    if (!pow2(a) || x>UINT64_MAX-(a-1)) return 0;
    *out=(x+a-1)&~(a-1);return 1;
}
FgReason fg_linear_size(uint32_t w,uint32_t h,uint32_t p,uint32_t bpp,uint64_t a,uint64_t *out) {
    if (!w || !h || p<w || !bpp || !out || !pow2(a)) return FG_INVALID;
    uint64_t row=(uint64_t)p*bpp;
    if (row>UINT64_MAX/h) return FG_OVERFLOW;
    return align_up(row*h,a,out)?FG_OK:FG_OVERFLOW;
}
FgReason fg_arena_init(FgArena *a,const FgWindow *w,uint32_t n,uint64_t guard) {
    if (!a || !w || !n || n>FG_MAX_WINDOWS) return FG_INVALID;
    FgArena tmp={0};tmp.count=n;tmp.guard=guard;
    for (uint32_t i=0;i<n;i++) {
        if (w[i].end<=w[i].begin) return FG_INVALID;
        uint64_t size=w[i].end-w[i].begin;
        if (tmp.remaining>UINT64_MAX-size) return FG_OVERFLOW;
        for (uint32_t j=0;j<i;j++)
            if (w[i].begin<w[j].end && w[j].begin<w[i].end) return FG_INVALID;
        tmp.free[i]=w[i];tmp.remaining+=size;
    }
    if (tmp.remaining<guard) return FG_NO_MEMORY;
    *a=tmp;return FG_OK;
}
FgReason fg_place(FgArena *a,const FgBlock *blocks,uint32_t n,FgPlacement *placements) {
    if (!a || !blocks || !placements || n>FG_MAX_BLOCKS) return FG_INVALID;
    FgArena tmp=*a;FgPlacement result[FG_MAX_BLOCKS];
    for (uint32_t b=0;b<n;b++) {
        const FgBlock *r=&blocks[b];
        if (!r->bytes || !pow2(r->alignment)) return FG_INVALID;
        uint64_t chosen=0,best=UINT64_MAX;uint32_t wi=UINT32_MAX;
        for (uint32_t i=0;i<tmp.count;i++) {
            uint64_t start;
            if (tmp.free[i].type!=r->type || !align_up(tmp.free[i].begin,r->alignment,&start)) continue;
            if (start>tmp.free[i].end || r->bytes>tmp.free[i].end-start) continue;
            uint64_t waste=(tmp.free[i].end-start)-r->bytes;
            if (waste<best) {best=waste;wi=i;chosen=start;}
        }
        if (wi==UINT32_MAX) return FG_NO_MEMORY;
        /* Prefix alignment padding is deliberately stranded: conservative accounting. */
        uint64_t consumed=chosen-tmp.free[wi].begin+r->bytes;
        if (tmp.remaining<consumed || tmp.remaining-consumed<tmp.guard) return FG_NO_MEMORY;
        tmp.remaining-=consumed;
        result[b]=(FgPlacement){chosen,r->bytes,r->id,wi,r->scanout};
        tmp.free[wi].begin=chosen+r->bytes;
    }
    memcpy(placements,result,n*sizeof(*result));*a=tmp;return FG_OK;
}
FgReason fg_can_synthesize(const FgCaps *c,int ace) {
    if (!c || c->native_buffers<2 || c->native_buffers>8 || !c->vsync) return FG_INVALID;
    if (c->format!=0x80000000u) return FG_BAD_FORMAT;
    if (!c->renderer_dimensions_supported || !c->linear_output_verified) return FG_BAD_LAYOUT;
    if (!c->gpu_ready_proven) return FG_NO_FENCE;
    if (!c->source_ownership_proven || !c->status_contract_proven) return FG_UNVERIFIED;
    if (c->api!=FG_API_GNM || !c->tail_verified || c->ccb_present) return FG_UNVERIFIED;
    if (ace && !c->ace_lease_proven) return FG_NO_LEASE;
    if (!ace && !c->cs_shadow_complete) return FG_NO_SHADOW;
    return FG_OK;
}
FgReason fg_schedule(const FgScheduler *s,const FgScheduleInput *i,FgSchedule *o) {
    if (!s || !i || !o || !i->tick_us || i->tick_us>1000000 ||
        i->available_mids>FG_MAX_MIDS || (unsigned)i->preset>FG_FAST) return FG_INVALID;
    FgSchedule p={0};p.outputs=1;p.desired_outputs=1;p.next=*s;
    if (!i->preference_on || i->preset==FG_NATIVE) {p.next.carry_us=0;*o=p;return FG_OK;}
    if (!i->source_ready) {p.reason=FG_NO_FENCE;*o=p;return p.reason;}
    if (!i->gpu_budget_ok) {p.reason=FG_BUSY;*o=p;return p.reason;}
    if (!i->period_us || i->period_us>1000000) {p.reason=FG_HITCH;p.next.carry_us=0;*o=p;return p.reason;}
    uint64_t period=i->period_us;
    /* No work backlog is generated for a long stall. Preference remains ON. */
    if (period>i->tick_us*(FG_MAX_MIDS+1u)) {
        p.reason=FG_HITCH;p.next.carry_us=0;*o=p;return p.reason;
    }
    if (i->preset==FG_QUALITY) {
        /* A fixed 60Hz display cannot show 2x for a game already above ~30fps. */
        p.desired_outputs=(period+i->tick_us/50u>=2*i->tick_us)?2u:1u;
        p.next.carry_us=0;
    } else {
        int64_t carry=s->carry_us;
        if (carry<0 || carry>=(int64_t)i->tick_us) carry=0;
        uint64_t total=period+(uint64_t)carry;
        uint64_t desired=total/i->tick_us;
        p.desired_outputs=(uint32_t)(desired?desired:1u);
        p.next.carry_us=(int64_t)(total%i->tick_us);
    }
    p.next.previous_period=period;
    uint32_t cap=i->available_mids+1u;
    p.outputs=p.desired_outputs;
    if (p.outputs>cap) {p.outputs=cap;p.capacity_limited=1;}
    if (!i->free_flip_entries || p.outputs>i->free_flip_entries) {
        p.outputs=1;p.reason=FG_BACKPRESSURE;p.next.carry_us=0;
    } else if (p.capacity_limited) {p.reason=FG_CAPACITY;p.next.carry_us=0;}
    p.mids=p.outputs-1;
    for (uint32_t k=0;k<p.mids;k++) p.t_q16[k]=(uint32_t)(((uint64_t)(k+1)*65536u)/p.outputs);
    *o=p;return p.reason;
}
void fg_schedule_commit(FgScheduler *s,const FgSchedule *p,uint32_t actual) {
    if (!s || !p) return;
    if (actual==p->outputs) *s=p->next;
    else {s->carry_us=0;s->previous_period=0;} /* No speculative credit survives a partial batch. */
}
void fg_ledger_begin(FgLedger *l,uint64_t physical,uint64_t logical) {
    if (!l) return;
    memset(l,0,sizeof(*l));l->physical_count=physical;l->logical_count=logical;l->valid=1;
}
FgReason fg_ledger_enqueue(FgLedger *l,uint64_t job,int64_t arg,uint32_t index,const uint32_t *mid_indices,uint32_t mids) {
    if (!l || !l->valid || !job || job<=l->last_job || mids>FG_MAX_MIDS || (mids && !mid_indices)) return FG_INVALID;
    uint32_t n=mids+1;
    if (l->write-l->read>FG_MAX_EVENTS-n || l->write>UINT64_MAX-n) return FG_BACKPRESSURE;
    for (uint32_t k=0;k<n;k++)
        l->events[l->write++%FG_MAX_EVENTS]=(FgPresentEvent){job,arg,index,k<mids?mid_indices[k]:index,mids,k};
    l->last_job=job;return FG_OK;
}
FgReason fg_ledger_observe(FgLedger *l,uint64_t count,uint64_t when,int32_t index,int64_t arg) {
    if (!l || !l->valid) return FG_INVALID;
    if (count==l->physical_count) return FG_OK;
    if (count<l->physical_count || count-l->physical_count>l->write-l->read) {
        l->valid=0;return FG_DISCONTINUITY;
    }
    uint64_t n=count-l->physical_count;
    FgPresentEvent last=l->events[(l->read+n-1)%FG_MAX_EVENTS];
    /* Equality only; a game's arg may repeat, be negative, or move backwards. */
    uint32_t expected_index=last.present_index;
    if (arg!=last.arg || (index<0 || (uint32_t)index!=expected_index)) {
        l->valid=0;return FG_DISCONTINUITY;
    }
    for (uint64_t k=0;k<n;k++) {
        FgPresentEvent e=l->events[l->read++%FG_MAX_EVENTS];
        if (!e.ordinal) {
            if (l->logical_count==UINT64_MAX) {l->valid=0;return FG_OVERFLOW;}
            l->logical_count++;l->completed_valid=1;l->game_arg=e.arg;l->game_index=e.native_index;
            /* A coalesced sample doesn't give exact timestamp for earlier completions. */
            l->game_done_us=(k+1==n)?when:0;
        }
        if (e.ordinal<e.mids) {
            if (k+1==n) l->observed_mids++;
        } else if (k+1==n) l->observed_native++;
    }
    l->physical_count=count;return FG_OK;
}
FgReason fg_ledger_status(const FgLedger *l,FgGameStatus *out) {
    if (!l || !out || !l->valid || !l->completed_valid) return FG_UNVERIFIED;
    uint64_t pending=0;
    for (uint64_t i=l->read;i<l->write;i++) if (!l->events[i%FG_MAX_EVENTS].ordinal) pending++;
    *out=(FgGameStatus){l->logical_count,l->game_done_us,pending,l->game_arg,l->game_index};
    return FG_OK;
}
int fg_can_unregister(const FgDrain *d) {
    return d && !d->pending_jobs && !d->gpu_readers && !d->command_readers && d->native_scanout && d->observer_native;
}
int fg_can_free(const FgDrain *d) {return fg_can_unregister(d) && !d->registered && d->queue_detached;}
const char *fg_reason(FgReason r) {
    static const char *names[]={"ok","invalid","overflow","no_memory","unverified","no_ready_fence",
        "unsupported_format","unsupported_layout","unknown_compute_state","backpressure","busy",
        "status_discontinuity","hitch","surface_capacity","no_queue_lease"};
    return (unsigned)r<sizeof(names)/sizeof(names[0])?names[r]:"unknown";
}
