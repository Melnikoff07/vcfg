#pragma once
#include <stddef.h>
#include <stdint.h>

/* Pure planning/ownership code. No kernel calls, memory writes to games, or GPU packets. */
#define FG_MAX_WINDOWS 16u
#define FG_MAX_BLOCKS 32u
#define FG_MAX_MIDS 3u
#define FG_MAX_EVENTS 64u

typedef enum { FG_NATIVE, FG_QUALITY, FG_FAST } FgPreset;
typedef enum { FG_API_UNKNOWN, FG_API_GNM, FG_API_CPU, FG_API_WORKLOAD, FG_API_REQUEST, FG_API_REQUEST_WORKLOAD } FgSubmitApi;
typedef enum {
    FG_OK, FG_INVALID, FG_OVERFLOW, FG_NO_MEMORY, FG_UNVERIFIED, FG_NO_FENCE,
    FG_BAD_FORMAT, FG_BAD_LAYOUT, FG_NO_SHADOW, FG_BACKPRESSURE, FG_BUSY,
    FG_DISCONTINUITY, FG_HITCH, FG_CAPACITY, FG_NO_LEASE
} FgReason;

typedef struct { uint64_t begin, end; uint32_t type; } FgWindow;
typedef struct { uint64_t bytes, alignment; uint32_t id, type, scanout; } FgBlock;
typedef struct { uint64_t physical, bytes; uint32_t id, window, scanout; } FgPlacement;
typedef struct {
    FgWindow free[FG_MAX_WINDOWS]; uint32_t count;
    uint64_t remaining, guard;
} FgArena;

FgReason fg_arena_init(FgArena *, const FgWindow *, uint32_t, uint64_t guard_bytes);
/* Work on a copy: any failure leaves arena and placements unchanged. Only plans; no allocation. */
FgReason fg_place(FgArena *, const FgBlock *, uint32_t, FgPlacement *);
FgReason fg_linear_size(uint32_t width, uint32_t height, uint32_t pitch,
                        uint32_t bytes_per_pixel, uint64_t alignment, uint64_t *bytes);

typedef struct {
    FgSubmitApi api;
    uint32_t width,height,pitch,format,native_buffers,tiling,vsync;
    uint32_t gpu_ready_proven,source_ownership_proven,status_contract_proven;
    uint32_t tail_verified,ccb_present,cs_shadow_complete,ace_lease_proven;
    uint32_t renderer_dimensions_supported,linear_output_verified;
} FgCaps;
FgReason fg_can_synthesize(const FgCaps *, int use_ace);

typedef struct { int64_t carry_us; uint64_t previous_period; } FgScheduler;
typedef struct {
    FgPreset preset; uint64_t period_us,tick_us;
    uint32_t preference_on,available_mids,free_flip_entries,gpu_budget_ok,source_ready;
} FgScheduleInput;
typedef struct {
    uint32_t outputs,mids,desired_outputs,capacity_limited;
    uint32_t t_q16[FG_MAX_MIDS];
    FgReason reason;
    FgScheduler next;
} FgSchedule;
/* Inspect/plan first; commit next only after the planned batch was submitted completely. */
FgReason fg_schedule(const FgScheduler *, const FgScheduleInput *, FgSchedule *);
void fg_schedule_commit(FgScheduler *, const FgSchedule *, uint32_t actual_outputs);

typedef struct {
    uint64_t job; int64_t arg; uint32_t native_index,present_index,mids,ordinal;
} FgPresentEvent;
typedef struct {
    FgPresentEvent events[FG_MAX_EVENTS];
    uint64_t read,write,physical_count,logical_count,last_job;
    uint64_t game_done_us,observed_mids,observed_native;
    int64_t game_arg; uint32_t game_index,valid,completed_valid;
} FgLedger;
typedef struct { uint64_t count,done_us,pending_jobs; int64_t arg; uint32_t index; } FgGameStatus;
void fg_ledger_begin(FgLedger *, uint64_t physical_count,uint64_t logical_count);
FgReason fg_ledger_enqueue(FgLedger *,uint64_t job,int64_t arg,uint32_t native_index,const uint32_t *mid_indices,uint32_t mids);
/* Single owned handle; initial hardware queue must be empty. Epoch invalidated on unowned flips. */
FgReason fg_ledger_observe(FgLedger *,uint64_t count,uint64_t shown_us,int32_t index,int64_t arg);
FgReason fg_ledger_status(const FgLedger *,FgGameStatus *);

typedef struct {
    uint32_t pending_jobs,gpu_readers,command_readers;
    uint32_t native_scanout,observer_native,registered,queue_detached;
} FgDrain;
/* This is a logical gate, not an operation that waits or frees GPU resources. */
int fg_can_unregister(const FgDrain *);
int fg_can_free(const FgDrain *);
const char *fg_reason(FgReason);
