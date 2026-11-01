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
//  [2] on screen - hand-over in us (bits 0..21, saturated at 4.19 s, 0 = not seen yet) | generated frames the real
//      had before it (22..23: 0 = none or a repeat, 1, 2 = a 3-slot pair) | vblanks the real stayed (24..27) | vblanks
//      the generated frames before it stayed (28..31)
#define FT_N 128u
static inline uint32_t ft_sat16(uint64_t v) { return v > 0xFFFFu ? 0xFFFFu : (uint32_t)v; }
static inline uint32_t ft_pack_sub(uint64_t dsub_us, uint64_t own_us) { return ft_sat16(dsub_us) | (ft_sat16(own_us) << 16); }
static inline uint32_t ft_with_screen(uint32_t w, uint64_t dscr_us)
{
    uint32_t v = dscr_us > 0x3FFFFFu ? 0x3FFFFFu : (uint32_t)dscr_us;
    return (w & 0xFFC00000u) | (v ? v : 1u);
}
static inline uint32_t ft_with_ngen(uint32_t w, uint32_t ng) { return (w & ~0x00C00000u) | ((ng > 3u ? 3u : ng) << 22); }
static inline uint32_t ft_with_real_vb(uint32_t w, uint32_t nv) { return (w & ~0x0F000000u) | ((nv > 15u ? 15u : nv) << 24); }
static inline uint32_t ft_with_mid_vb(uint32_t w, uint32_t nv) { return (w & ~0xF0000000u) | ((nv > 15u ? 15u : nv) << 28); }
// Formats records [0, n) as "FTRACE <base_us> | dh,dsub,dscr,rv/mv,own,ng;..." where dh is the hand-over interval to
// the record before (the first one uses prev_handover_us) and ng the generated frames of that real. Returns the number
// of records written (stops before overflow).
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
        int m = snprintf(item, sizeof(item), " %u,%u,%u,%u/%u,%u,%u;", prev ? h - prev : 0u, s & 0xFFFFu, c & 0x3FFFFFu,
                         (c >> 24) & 15u, c >> 28, s >> 16, (c >> 22) & 3u);
        if (m < 0 || pos + (size_t)m + 2u > cap) break;
        for (int j = 0; j < m; j++) out[pos++] = item[j];
        prev = h;
        done++;
    }
    out[pos++] = '\n';
    out[pos] = 0;
    return done;
}

// ---- 60 fps detection ----
// A game detected at 60 fps in the warm-up that later drops to the 30 fps class gets frame generation back on its own.
// An explicit fps_lock = 60 is the player's choice of a free-running game without frame generation: it is kept.
static inline int fg_auto_reenable_allowed(int fps_lock)
{
    return fps_lock != 60;
}

// ---- below-30 regime: 3-slot pairs (strategy a') ----
// A game whose own time per frame (release -> submit, g_cpu_ema_us) needs more than 2 refreshes used to show 1
// generated frame and then repeat its real frame (4 to 20 repeats per second at 20-28 fps). In this regime the pair
// P -> C whose real is predicted to take 3 refreshes shows two generated frames (t ~ 1/3 and 2/3) and then the real.
// Entry and exit use the median of the last B30_RING own times in refreshes (enter above B30_ENTER_Q8, leave below
// B30_EXIT_Q8). Inside, FG Fast's accumulator decides 2 or 3 slots per pair, fed by the game's own time: the hand-over
// interval is stretched by the pacer itself and would lock the regime on. A late real repeats the real (the generated
// frames are flipped before it, never held), and a flip queue more than B30_QCUT_Q8 refreshes ahead at the decision
// drops the second generated frame. Thresholds tuned in the q-below30 simulator (sweep_b30, tune_b30): 2.10 made the
// regime toggle 14 times a minute at a steady 30 fps; a 1.25-refresh cut dropped 26-28 % of the 3-slot pairs at
// 20-25 fps and brought back 4.8 repeats/s at 20 fps.
#define B30_RING      8u
#define B30_ENTER_Q8  550u    // 2.15 refreshes (35.9 ms at 59.94 Hz)
#define B30_EXIT_Q8   517u    // 2.02 refreshes (33.7 ms)
#define B30_QCUT_Q8   640u    // 2.5 refreshes of pending flips ahead of the decision
#define B30_OWN_MIN_US 25000u // accumulator input clamp, as FG Fast (40 .. 20 fps)
#define B30_OWN_MAX_US 50000u
// The pacer period stays 2 refreshes in the regime: following the slots (3 refreshes after a 3-slot pair) held the game
// back and cost real frames in the simulator (28 fps: 27.70 against 27.81 real/s, 0.90 against 0.65 repeats/s, pacing
// rms 5.99 against 5.68 ms). Set to 1 to pace each pair by its own slot count (pace_pair_ex nvb = m_out).
#define B30_PACE_FOLLOWS_SLOTS 0
typedef struct {
    uint32_t ring[B30_RING];
    uint32_t n, pos, on, enters;
    int32_t acc_q16;
} Below30;

static inline void b30_reset(Below30 *s)
{
    for (uint32_t i = 0; i < B30_RING; i++) s->ring[i] = 0;
    s->n = s->pos = s->on = 0;
    s->acc_q16 = 0;
}
// Own time in refreshes, Q8 (256 = 1 refresh), from microseconds and the refresh period in Q24.8 us.
static inline uint32_t b30_slots_q8(uint32_t own_us, uint32_t period_q8)
{
    if (!period_q8) return 0u;
    uint64_t q = ((uint64_t)own_us << 16) / period_q8;
    return q > 0xFFFFu ? 0xFFFFu : (uint32_t)q;
}
static inline uint32_t b30_median_q8(const Below30 *s)
{
    uint32_t v[B30_RING];
    uint32_t n = s->n < B30_RING ? s->n : B30_RING;
    if (!n) return 0u;
    for (uint32_t i = 0; i < n; i++) v[i] = s->ring[i];
    for (uint32_t i = 1; i < n; i++) {
        uint32_t x = v[i], j = i;
        while (j && v[j - 1] > x) { v[j] = v[j - 1]; j--; }
        v[j] = x;
    }
    return (n & 1u) ? v[n / 2u] : (v[n / 2u - 1u] + v[n / 2u] + 1u) / 2u;
}
// One real frame: feeds its own time (0 = not eligible: resets) and returns whether the regime is on.
static inline int b30_regime_step(Below30 *s, uint32_t own_us, uint32_t period_q8)
{
    if (!own_us || !period_q8) { b30_reset(s); return 0; }
    s->ring[s->pos] = b30_slots_q8(own_us, period_q8);
    s->pos = (s->pos + 1u) % B30_RING;
    if (s->n < B30_RING) s->n++;
    if (s->n < B30_RING) return (int)s->on;
    uint32_t med = b30_median_q8(s);
    if (!s->on && med > B30_ENTER_Q8) { s->on = 1; s->acc_q16 = 0; s->enters++; }
    else if (s->on && med < B30_EXIT_Q8) { s->on = 0; s->acc_q16 = 0; }
    return (int)s->on;
}
// Refreshes of flips still ahead at the decision, Q8: pending flips, the first of which latches at the next vblank.
static inline uint32_t b30_queue_ahead_q8(uint32_t pending, uint32_t to_next_vb_us, uint32_t period_q8)
{
    if (!pending || !period_q8) return 0u;
    uint64_t ahead_q8us = (uint64_t)(pending - 1u) * period_q8 + ((uint64_t)to_next_vb_us << 8);
    return (uint32_t)((ahead_q8us << 8) / period_q8);
}
// Slots of this pair (2 or 3) from the own-time EMA; *cut is set when the queue dropped a 3 to 2.
static inline uint32_t b30_slots(Below30 *s, uint32_t own_ema_us, uint32_t period_q8, uint32_t queue_ahead_q8, int *cut)
{
    if (cut) *cut = 0;
    if (!s->on || !period_q8) { s->acc_q16 = 0; return 2u; }
    uint32_t x = own_ema_us < B30_OWN_MIN_US ? B30_OWN_MIN_US : (own_ema_us > B30_OWN_MAX_US ? B30_OWN_MAX_US : own_ema_us);
    int32_t r_q16 = (int32_t)(((uint64_t)x << 24) / period_q8);
    int32_t acc_new = s->acc_q16 + r_q16;
    int32_t m = (acc_new + 32768) >> 16;
    if (m < 2) m = 2;
    if (m > 3) m = 3;
    if (m == 3 && queue_ahead_q8 > B30_QCUT_Q8) { m = 2; if (cut) *cut = 1; }
    acc_new -= m << 16;
    if (acc_new < -49152) acc_new = -49152;
    if (acc_new > 49152) acc_new = 49152;
    s->acc_q16 = acc_new;
    return (uint32_t)m;
}
// Refreshes per pair for the timer pacer.
static inline uint32_t b30_pace_nvb(uint32_t m_out)
{
    return (B30_PACE_FOLLOWS_SLOTS && m_out == 3u) ? 3u : 2u;
}
// Display phases of the generated frames, Q16, from the PLL's slot prediction: the previous real stays vb_p_to_m
// refreshes before the first generated frame, which is followed by vb_m_to_c refreshes until the real (1 for a 2-slot
// pair, 2 for a 3-slot pair). t1 keeps the monitor clamp [1/3, 2/3] of the 2-slot pair (bit-identical result for
// vb_m_to_c = 1); t2 = t1 + one refresh, inside phase8's override clamp (<= 0.75) and never below t1.
static inline void pll_slot_phases_q16(uint32_t vb_p_to_m, uint32_t vb_m_to_c, uint32_t *t1_q16, uint32_t *t2_q16)
{
    if (vb_p_to_m < 1u) vb_p_to_m = 1u;
    if (vb_m_to_c < 1u) vb_m_to_c = 1u;
    uint32_t vb_p_to_c = vb_p_to_m + vb_m_to_c;
    uint32_t t1 = ((vb_p_to_m << 16) + (vb_p_to_c >> 1)) / vb_p_to_c;
    if (t1 < 21845u) t1 = 21845u;
    if (t1 > 43691u) t1 = 43691u;
    uint32_t t2 = (((vb_p_to_m + 1u) << 16) + (vb_p_to_c >> 1)) / vb_p_to_c;
    if (t2 > 49152u) t2 = 49152u;
    if (t2 < t1) t2 = t1;
    if (t1_q16) *t1_q16 = t1;
    if (t2_q16) *t2_q16 = t2;
}
// The regime may run only where the second generated-frame buffer already exists (allocated by the mode-8 code, never
// for this mode: g_nmid >= 2), memory is not tight (no valve/shave), the player left it on, FG Quality on the timer
// pacer without fps_lock = 20, and no present copy runs (its buffers take the real's place).
static inline int b30_available(int cfg_on, int nmid, int mem_tight, int preset, int fg_off, int pace_timer, int fps_lock)
{
    return cfg_on && nmid >= 2 && !mem_tight && preset != 1 && !fg_off && pace_timer && fps_lock != 20;
}
// ---- below-30 regime cost: buffer order (mid_lru) and feed (b30x), both part of the regime ----
// On the console (27/10, CUSA18723, 2 generated-frame buffers) the regime cost 11 % of the real frame rate: the game is
// GPU-serialised (the game span between two of our DCBs stays 32.7 ms p50 with the regime off and on, and the interval
// grows by our extra DCB time), and two of our own choices fed each other:
//  * the generated-frame buffer was g_mbase + g_flips % g_nmid. After a 3-slot pair (mid1 in A, mid2 in B) the next
//    pair writes into the buffer the pair before flipped LAST, so its WaitFlipDone holds the graphics ring until the
//    previous real latched ("fence" 2.5 ms p50, 0.0 with the regime off), and the game waits for that ring;
//  * the regime and the accumulator read the game's own time, which then includes that wait and the 2nd generated
//    frame (~2.85 ms): more 3-slot pairs, more waits (median own 2.15 -> 2.48 refreshes, half the pairs 3-slot where
//    the game needs ~17 %).
// mid_lru: the pair writes its first generated frame into the buffer flipped longest ago and the second one into the
// next (round robin, i.e. today's order, while every pair has 2 slots).
#define MID_LRU_MAX 4u
typedef struct { uint8_t ord[MID_LRU_MAX]; uint32_t n; } MidLru;
static inline void mid_lru_reset(MidLru *l, uint32_t n)
{
    l->n = n > MID_LRU_MAX ? MID_LRU_MAX : n;
    for (uint32_t i = 0; i < MID_LRU_MAX; i++) l->ord[i] = (uint8_t)i;
}
// j-th least recently flipped buffer (0 = flipped longest ago), relative to g_mbase.
static inline uint32_t mid_lru_pick(const MidLru *l, uint32_t j)
{
    return l->n ? l->ord[j % l->n] : 0u;
}
static inline void mid_lru_touch(MidLru *l, uint32_t b)
{
    uint32_t i = 0;
    while (i < l->n && l->ord[i] != b) i++;
    if (i >= l->n) return;
    for (; i + 1u < l->n; i++) l->ord[i] = l->ord[i + 1u];
    l->ord[l->n - 1u] = (uint8_t)b;
}
// The pair flipped a (and then b when two): both move to the most recent end, in flip order.
static inline void mid_lru_flipped(MidLru *l, uint32_t a, int two, uint32_t b)
{
    mid_lru_touch(l, a);
    if (two && b != a) mid_lru_touch(l, b);
}
// Feed: our extra GPU time per real frame, from the GPU timestamps of the slot read back 3 frames later: the
// WaitFlipDone before the first generated frame (fence) plus, in a 3-slot pair, the post block beyond the 2-slot one
// (t2[5]..t2[7]: rcas8/osd of the first generated frame, then fence 2 + phase8 + warp8 + rcas8 + osd of the second).
// The regime and the accumulator are fed own - EMA(extra): the game's time without what the regime itself adds.
typedef struct { uint32_t ema_us, post2_us, n2, n, fence_sum_us, g2_sum_us, nw, n3; } B30Extra;   // nw, n3, sums: log window
static inline void b30x_reset(B30Extra *x)
{
    x->ema_us = x->post2_us = x->n2 = x->n = x->fence_sum_us = x->g2_sum_us = x->nw = x->n3 = 0u;
}
static inline uint32_t b30x_ticks_us(uint64_t ticks, uint32_t ticks_per_ms)
{
    if (!ticks_per_ms) return 0u;
    uint64_t us = ticks * 1000u / ticks_per_ms;
    return us > 100000u ? 100000u : (uint32_t)us;
}
// One read-back slot: fence and post-block durations in us, whether that pair had 3 slots.
static inline void b30x_add(B30Extra *x, uint32_t fence_us, uint32_t post_us, int three)
{
    uint32_t g2 = 0u;
    if (!three) {
        x->post2_us = x->n2 ? x->post2_us + (uint32_t)(((int32_t)post_us - (int32_t)x->post2_us) / 8) : post_us;
        x->n2++;
    } else {
        g2 = post_us > x->post2_us ? post_us - x->post2_us : 0u;
        x->n3++;
    }
    uint32_t e = fence_us + g2;
    x->ema_us = x->n ? x->ema_us + (uint32_t)(((int32_t)e - (int32_t)x->ema_us) / 8) : e;
    x->n++;
    x->nw++;
    x->fence_sum_us += fence_us;
    x->g2_sum_us += g2;
}
// Own time fed to the regime: own minus our extra; 0 stays 0 (not eligible: resets), never below half the own time.
static inline uint32_t b30_feed_us(uint32_t own_us, uint32_t extra_us)
{
    if (!own_us) return 0u;
    uint32_t f = own_us > extra_us ? own_us - extra_us : 0u;
    return f < own_us / 2u ? own_us / 2u : f;
}

// ---- FG Fast (preset 1): slots per pair and display phases ----
// fast_pace 0 keeps the slots and phases of FG Fast in v2.35: the accumulator reads g_ema_frame_us (hand-over
// interval), a pair keeps 2 slots while >= 3 flips are pending, and the phases are 1/3, 2/3 (or 1/2) plus 3/32 of the
// accumulator. The buffers are not part of the option: FG Fast writes its generated frames into the mid_lru buffers
// (below-30 order, main.c mid_lru_on) with or without it. With g_flips % 2 the pair after a 3-slot pair wrote into the
// buffer flipped last, and its WaitFlipDone held the graphics ring until the real after it latched (~10 ms in the
// replay; console 29/10: fence 0.5-1.3 ms per real); the interval the accumulator reads included that wait, so more
// 3-slot pairs made longer intervals and more 3-slot pairs. fast_pace 1 (opt-in file fastpace_<TITLE>) changes two
// things, from the m-fast simulator (GPU-serialised replay of console FTRACE intervals, 20-30 fps):
//  * the queue guard measures the flips ahead in refreshes (FAST_QCUT_Q8) instead of counting pending flips;
//  * the phases are exactly 1/3, 2/3 and 1/2: the accumulator shift moved them off the display slots (judder rms
//    -0.25 to -0.33 ms in the replay at 24-30 fps).
#define FAST_QCUT_Q8 576u   // 2.25 refreshes of flips ahead at the decision (simulator sweep 1.5-2.5 at 24/26/28 fps)
static inline uint32_t fast_slots(int32_t *acc_q16, uint32_t ema_frame_us, int fast_pace, uint32_t pending,
                                  uint32_t queue_ahead_q8, int32_t *acc_prev)
{
    uint32_t dt = ema_frame_us;
    if (dt < 25000u) dt = 25000u;   // 40 fps
    if (dt > 50000u) dt = 50000u;   // 20 fps
    int32_t r_q16 = (int32_t)(((uint64_t)dt << 16) / 16683ull);
    int32_t prev = *acc_q16;
    int32_t acc_new = prev + r_q16;
    int m = (int)((acc_new + 32768) >> 16);
    if (m < 2) m = 2;
    if (m > 3) m = 3;
    if (!fast_pace) {
        if (pending >= 3u) m = 2;
    } else if (m == 3 && queue_ahead_q8 > FAST_QCUT_Q8) {
        m = 2;
    }
    int32_t a = acc_new - (m << 16);
    if (a < -49152) a = -49152;
    if (a > 49152) a = 49152;
    *acc_q16 = a;
    if (acc_prev) *acc_prev = prev;
    return (uint32_t)m;
}
// Phases of the generated frames, Q16. A 2-slot pair writes only *t1 (t2 stays what the caller had).
static inline void fast_phases_q16(uint32_t m, int32_t acc_prev, int fast_pace, uint32_t *t1, uint32_t *t2)
{
    if (fast_pace) {
        if (m == 3u) { *t1 = 21845u; *t2 = 43691u; }
        else *t1 = 32768u;
        return;
    }
    int32_t sh = (acc_prev * 3) >> 5;   // ~0.094 * acc_prev
    if (m == 3u) {
        int32_t p1 = 21845 + sh, p2 = 43690 + sh;
        if (p1 < 16384) p1 = 16384;
        if (p1 > 27525) p1 = 27525;
        if (p2 < 38010) p2 = 38010;
        if (p2 > 49152) p2 = 49152;
        *t1 = (uint32_t)p1;
        *t2 = (uint32_t)p2;
    } else {
        int32_t p1 = 32768 + sh;
        if (p1 < 22937) p1 = 22937;
        if (p1 > 42598) p1 = 42598;
        *t1 = (uint32_t)p1;
    }
}
