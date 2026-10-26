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

