// CPU cost of the plugin's own work on the console (no console API): the opt-in cuts and the arithmetic of the
// "CPU plugin" line. Compiled into main.c and, unchanged, into the host tests (tools/tests/test_cpu_cost.py).
//
// Each cut is switched on by its own file LOG_DIR/<name>_<TITLE>, read once when the io thread starts, so a scene can
// be compared with and without it:
//   monev     the monitor thread waits on vblank + flip events of the game's handle (about 120 wake-ups/s) instead of
//             sceKernelUsleep(500) (about 860/s, two VideoOut calls each); a 20 ms timeout still samples
//   iolazy    the io thread sleeps 10 ms while it has nothing to write (2 ms right after a write), and its periodic
//             jobs (10 s inert check, 0.5 s config reload) run by elapsed time instead of by wake-up count
//   nospin    the timer pacers sleep for the remaining time minus the measured sleep lateness and never spin
//   tracelite the flight recorder appends the new lines in place (header and end marker kept) instead of rewriting
//             the whole file (about 50 KB) every 2 s; it is rewritten whole only when the appended tail is full
//   quietlog  periodic diagnostic lines go to probe.log only (debug log on); without the debug log they are skipped
//             before formatting, and they never reach klog or the flight recorder
#pragma once
#include <stdint.h>

#define CC_MON_POLL_US       500u      // today's monitor period (sceKernelUsleep)
#define CC_MON_EV_TIMEOUT_US 20000u    // event wait timeout: a sample still happens if no event comes
#define CC_IO_BUSY_US        2000u     // today's io period; with iolazy right after a write
#define CC_IO_IDLE_US        10000u    // iolazy: nothing written in the last wake-up
#define CC_IO_INERT_US       10000000ull   // the "no flip hook" check, 10 s after the io thread starts
#define CC_IO_CFG_US         500000ull     // config reload / adapt save (250 wake-ups of 2 ms today)
#define CC_SPIN_MARGIN_US    1000u     // today's pacers sleep until 1 ms before the target and spin the rest
#define CC_SPIN_MIN_US       1500u     // today's pacers do not sleep below this, they spin all of it
#define CC_NOSPIN_FLOOR_US   200u      // nospin: a remaining time below lateness + floor is not slept at all
#define CC_LATE_CAP_US       4000u     // one late wake-up above this is clamped before it enters the average

// Sleep lateness average (us): 1/8 per sample, lateness clamped to CC_LATE_CAP_US.
static inline uint32_t cc_late_ema(uint32_t ema, uint64_t asked_us, uint64_t slept_us)
{
    uint64_t late = slept_us > asked_us ? slept_us - asked_us : 0u;
    if (late > CC_LATE_CAP_US) late = CC_LATE_CAP_US;
    return ema ? (uint32_t)(((uint64_t)ema * 7u + late + 4u) / 8u) : (uint32_t)late;
}

// Today's pacer: sleep this long (0: no sleep), then spin until the target.
static inline uint32_t cc_spin_sleep_us(uint64_t rem_us)
{
    return rem_us > CC_SPIN_MIN_US ? (uint32_t)(rem_us - CC_SPIN_MARGIN_US) : 0u;
}

// nospin: sleep this long and return (0: return at once). The expected wake-up is the target itself; a remaining time
// shorter than the average lateness plus the floor is released early rather than spun.
static inline uint32_t cc_nospin_sleep_us(uint64_t rem_us, uint32_t late_ema_us)
{
    uint64_t lead = (uint64_t)late_ema_us + CC_NOSPIN_FLOOR_US;
    if (rem_us <= lead) return 0u;
    uint64_t s = rem_us - late_ema_us;
    return s > 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)s;
}

// io thread period: today 2 ms always; iolazy 10 ms unless the last wake-up wrote something.
static inline uint32_t cc_io_sleep_us(int lazy, int busy)
{
    return (lazy && !busy) ? CC_IO_IDLE_US : CC_IO_BUSY_US;
}

// Monitor events: the udata we register each event with.
#define CC_EV_VBLANK ((void *)(uintptr_t)1u)
#define CC_EV_FLIP   ((void *)(uintptr_t)2u)

// A per-second rate from a count over a window of dt_us.
static inline double cc_per_s(uint64_t n, uint64_t dt_us)
{
    return dt_us ? (double)n * 1e6 / (double)dt_us : 0.0;
}

// A thread or process CPU clock as us (timespec seconds + nanoseconds).
static inline uint64_t cc_ts_us(int64_t sec, int64_t nsec)
{
    return sec < 0 || nsec < 0 ? 0u : (uint64_t)sec * 1000000ull + (uint64_t)nsec / 1000ull;
}

// Periodic diagnostic lines (quietlog): marked by a leading byte in the log queue so lg_write knows to keep them out of
// klog and the flight recorder.
#define CC_QUIET_MARK '\x01'
