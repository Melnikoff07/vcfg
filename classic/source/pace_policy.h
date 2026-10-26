// Pure pacing helpers of the classic plugin (no console API).
// Compiled into main.c and, unchanged, into the host tests (tools/tests/test_pace_policy.py).
#pragma once
#include <stdint.h>
#include <stdio.h>

// ---- refresh period ----
// Measured refresh period in Q24.8 us from a window of dvb vblanks spanning dt_us; 0 when the window is unusable.
// 59.939 Hz is 16683.63 us: the old 0.1 Hz rounding (599) made a 2-refresh pair 22 us too long (1.3 ms every 2 s).
static inline uint32_t vb_period_q8_from_window(uint64_t dvb, uint64_t dt_us)
{
    if (dvb < 30u || dvb > 100000u || !dt_us) return 0u;
    uint64_t q8 = ((dt_us << 8) + dvb / 2u) / dvb;
    return (q8 >= (10000ull << 8) && q8 <= (25000ull << 8)) ? (uint32_t)q8 : 0u;
}
// Fallback when no window was measured yet: the rounded rate in 0.1 Hz (599 = 59.9 Hz) as before.
static inline uint32_t vb_period_q8_from_hz10(uint32_t hz10)
{
    if (hz10 <= 400u || hz10 >= 800u) hz10 = 599u;
    return (uint32_t)(((10000000ull << 8) + hz10 / 2u) / hz10);
}
// n refreshes of period_q8 in counter ticks of a freq Hz clock.
static inline uint64_t vb_ticks(uint64_t freq, uint32_t period_q8, uint32_t n)
{
    return freq * (uint64_t)n * (uint64_t)period_q8 / 256000000ull;
}
// Refreshes per frame for a frame-rate cap: 60 -> 1, 30 -> 2, 20 -> 3 at 59.94 Hz (nearest whole number, >= 1).
static inline uint32_t vb_per_frame(uint32_t hz, uint32_t period_q8)
{
    if (!hz || !period_q8) return 1u;
    uint64_t frame_q8 = (1000000ull << 8) / hz;
    uint32_t n = (uint32_t)((frame_q8 + period_q8 / 2u) / period_q8);
    return n ? n : 1u;
}


// ---- shallow flip queue ----
// With frame generation the game is handed back while up to 3-4 flips are pending (WAIT_EXIT: 3 raw at exit). When its
// own time per frame (release -> submit, g_cpu_ema_us) stays below SHQ_ENTER_US for SHQ_DWELL real frames in a row
// (about 4 s), the hook waits before the submit until at most SHQ_MAX_PENDING flip is pending; it goes back to the old
// queue as soon as the EMA passes SHQ_EXIT_US. The dwell keeps a game that swings between 30 and 40 fps on the old queue:
// there the shallow queue would cost repeats (q-pacing simulator, scenarios osc_30_40_1s and sine_30_40_4s).
#define SHQ_ENTER_US    30000u
#define SHQ_EXIT_US     32000u
#define SHQ_DWELL       120u
#define SHQ_MAX_PENDING 1
typedef struct { uint32_t on, below; } ShallowQueue;
static inline int shq_step(ShallowQueue *s, uint32_t cpu_ema_us)
{
    if (!cpu_ema_us) { s->on = 0; s->below = 0; return 0; }
    if (cpu_ema_us < SHQ_ENTER_US) { if (s->below < 0xFFFFu) s->below++; }
    else s->below = 0;
    if (!s->on) s->on = s->below > SHQ_DWELL;
    else if (cpu_ema_us > SHQ_EXIT_US) s->on = 0;
    return (int)s->on;
}
