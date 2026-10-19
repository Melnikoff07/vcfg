#pragma once
// A21 presentation scheduler for QUALITY / FAST (design prototype; NOT called by the A21c runtime builds).
// Pure integer code, no console calls: the flip hook gives it the game submit interval and the observer's pending-flip
// count, it returns how many vblanks this game frame should occupy and the phase of each interpolated image.
//
// Model (vsync flips, one flip per vblank): for the pair (P = frame N-1, C = frame N) we queue n_inter images
// I(t_1..t_n) and then N. N is therefore shown n_inter vblanks after the first I. If the game frame lasts m vblanks
// and only n_inter < m-1 images can be produced, N stays on screen for the remaining vblanks: those are REPEATS and
// are counted as such, never reported as generated frames.
#include <stdint.h>

enum { A21_MODE_OFF = 0, A21_MODE_QUALITY = 1, A21_MODE_FAST = 2 };

typedef struct {
    uint32_t vblank_us;     // measured display period (REF observer: ~16683 us)
    uint32_t ema_dt_us;     // smoothed game submit interval
    uint32_t dt_hist[3];    // last raw intervals (median-of-3 rejects single hitches)
    int32_t  acc_q16;       // sigma-delta remainder of (dt / vblank) in Q16
    uint32_t max_inter;     // interpolated surfaces that can be in flight: 1 (one 720p I) or 2 (two 540p I)
    uint32_t pending_limit; // queued flips above which FAST drops one image to drain the queue (start: 3)
} a21_sched_t;

typedef struct {
    uint32_t m;             // vblanks planned for this game frame (1 = native only)
    uint32_t n_inter;       // interpolated images to synthesise and flip before N
    uint32_t repeats;       // vblanks in which N is shown again (not new images)
    uint32_t t_q16[2];      // phases of the images, 0..65535 (0.5 = 32768)
    uint32_t reason;        // 0 ok, 1 too fast for FG (>= ~41/57 fps), 2 hitch (> 4 vblanks), 3 backpressure
} a21_plan_t;

static inline uint32_t a21_med3(uint32_t a, uint32_t b, uint32_t c)
{
    if (a > b) { uint32_t t = a; a = b; b = t; }
    if (b > c) b = c;
    return a > b ? a : b;
}

static inline void a21_sched_init(a21_sched_t *s, uint32_t max_inter)
{
    s->vblank_us = 16683u; s->ema_dt_us = 33367u;
    s->dt_hist[0] = s->dt_hist[1] = s->dt_hist[2] = 33367u;
    s->acc_q16 = 0; s->max_inter = max_inter ? (max_inter > 2u ? 2u : max_inter) : 1u; s->pending_limit = 3u;
}

static inline a21_plan_t a21_sched_plan(a21_sched_t *s, int mode, uint32_t dt_us, uint32_t pending_flips)
{
    a21_plan_t r = { 1u, 0u, 0u, { 32768u, 32768u }, 0u };
    if (dt_us > 1000000u) dt_us = 1000000u;
    s->dt_hist[0] = s->dt_hist[1]; s->dt_hist[1] = s->dt_hist[2]; s->dt_hist[2] = dt_us;
    uint32_t dt = a21_med3(s->dt_hist[0], s->dt_hist[1], s->dt_hist[2]);
    s->ema_dt_us = (s->ema_dt_us * 7u + dt) / 8u;
    uint32_t vb = s->vblank_us ? s->vblank_us : 16683u;
    if (mode == A21_MODE_OFF) { s->acc_q16 = 0; return r; }
    if (dt_us > 4u * vb + vb / 2u) { s->acc_q16 = 0; r.reason = 2u; return r; }   // hitch: no interpolation across it

    if (mode == A21_MODE_QUALITY) {
        // Exactly one image per real frame. Two vsync flips need >= 2 vblanks: below 1.75 vblanks (~34 fps and
        // faster) QUALITY would throttle the game to 30 fps, so the frame is passed through natively.
        if (dt * 4u < vb * 7u) { r.reason = 1u; return r; }
        // The I is always shown one vblank before N, so its content time is N - vblank: t = 1 - vb/dt.
        // Steady 30 fps gives exactly 0.5 (A20 behaviour); 25 fps -> 0.583. Clamp to [0.5, 0.75].
        uint32_t t = 65536u - (uint32_t)(((uint64_t)vb << 16) / (dt ? dt : 1u));
        if (t < 32768u) t = 32768u;
        if (t > 49152u) t = 49152u;
        r.m = 2u; r.n_inter = 1u; r.t_q16[0] = t;
        return r;
    }

    // FAST: sigma-delta over dt/vblank so the long-run number of shown vblanks equals the game time.
    if (dt * 4u < vb * 5u) { s->acc_q16 = 0; r.reason = 1u; return r; }           // >= ~48 fps: native
    int32_t ratio = (int32_t)(((uint64_t)dt << 16) / vb);                          // Q16 vblanks per frame
    int32_t want = s->acc_q16 + ratio;
    int32_t m = (want + 32768) >> 16;
    if (m < 1) m = 1;
    if (m > 4) m = 4;
    if (pending_flips >= s->pending_limit && m > 1) { m--; r.reason = 3u; }      // drain a growing flip queue
    s->acc_q16 = want - (m << 16);
    if (s->acc_q16 > 65536) s->acc_q16 = 65536;
    if (s->acc_q16 < -65536) s->acc_q16 = -65536;
    r.m = (uint32_t)m;
    r.n_inter = r.m - 1u > s->max_inter ? s->max_inter : r.m - 1u;
    r.repeats = r.m - 1u - r.n_inter;
    // Equal content steps between the images that are actually shown; any repeat lands on N (end of frame).
    for (uint32_t j = 0; j < r.n_inter; j++) r.t_q16[j] = (uint32_t)(((j + 1u) << 16) / (r.n_inter + 1u));
    return r;
}
