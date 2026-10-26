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

// ---- FTRACE: one record of 3 x 32 bits per real frame ----
//  [0] hand-over time (hook entry), low 32 bits of the process time in us
//  [1] submit - hand-over in us (bits 0..15, saturated) | the game's own time release -> hand-over in us (bits 16..31)
//  [2] on screen - hand-over in us (bits 0..23, 0 = not seen yet) | vblanks the real stayed (24..27) | vblanks the
//      generated frame before it stayed (28..31)
#define FT_N 128u
static inline uint32_t ft_sat16(uint64_t v) { return v > 0xFFFFu ? 0xFFFFu : (uint32_t)v; }
static inline uint32_t ft_pack_sub(uint64_t dsub_us, uint64_t own_us) { return ft_sat16(dsub_us) | (ft_sat16(own_us) << 16); }
static inline uint32_t ft_with_screen(uint32_t w, uint64_t dscr_us)
{
    uint32_t v = dscr_us > 0xFFFFFFu ? 0xFFFFFFu : (uint32_t)dscr_us;
    return (w & 0xFF000000u) | (v ? v : 1u);
}
static inline uint32_t ft_with_real_vb(uint32_t w, uint32_t nv) { return (w & ~0x0F000000u) | ((nv > 15u ? 15u : nv) << 24); }
static inline uint32_t ft_with_mid_vb(uint32_t w, uint32_t nv) { return (w & ~0xF0000000u) | ((nv > 15u ? 15u : nv) << 28); }
// Formats records [0, n) as "FTRACE <base_us> | dh,dsub,dscr,rv/mv,own;..." where dh is the hand-over interval to the
// record before (the first one uses prev_handover_us). Returns the number of records written (stops before overflow).
static inline uint32_t ft_format(char *out, size_t cap, const uint32_t (*rec)[3], uint32_t n, uint32_t prev_handover_us)
{
    if (!cap) return 0;
    int k = snprintf(out, cap, "FTRACE %u |", n ? rec[0][0] : 0u);
    if (k < 0 || (size_t)k >= cap) { out[0] = 0; return 0; }
    size_t pos = (size_t)k;
    uint32_t prev = prev_handover_us, done = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t h = rec[i][0], s = rec[i][1], c = rec[i][2];
        char item[64];
        int m = snprintf(item, sizeof(item), " %u,%u,%u,%u/%u,%u;", prev ? h - prev : 0u, s & 0xFFFFu, c & 0xFFFFFFu,
                         (c >> 24) & 15u, c >> 28, s >> 16);
        if (m < 0 || pos + (size_t)m + 2u > cap) break;
        for (int j = 0; j < m; j++) out[pos++] = item[j];
        prev = h;
        done++;
    }
    out[pos++] = '\n';
    out[pos] = 0;
    return done;
}
