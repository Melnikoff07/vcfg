// Post-process budget (option post_budget, default on): Enhance AA and Sharpness step down by themselves while the
// game's real frames come late, and come back when they stop.
//
// Measured on CUSA02299 (30/10, same scene, camera spin): Sharpness 70 + Enhance AA Strong cost 8.1 ms of our GPU per
// generated frame (rcas8 ~4.5 ms of it, real and generated frame in one dispatch) and the real frame took 50 to 67 ms in
// 3 of 3 spins; Sharpness 0 + AA off cost 5.7 ms and no real frame was late in 2 spins. Standing still both held
// 30.0 flips/s. A late real frame is a judder on screen and doubles the motion the next generated frame interpolates.
//
// Input, once per 2-s STAT window of the flip hook: flips and length of the window, the longest flip interval (max dt),
// the refresh period, and the rcas8 time of the last GPU timing line. A window is late when max dt is longer than
// n + 0.5 refreshes (n = the mean interval in refreshes, 2 at 30 fps on 60 Hz) and at most n + 2.5: a longer stall
// (loading, a system dialog, the Share button) is not one missed refresh of our GPU, and clears the history instead.
//
// Levels, applied to every rcas8 dispatch alike (generated and real frame, so the two never differ more than before):
//   0 = the player's settings; 2 = Sharpness and Enhance AA off (no rcas8 pass). Level 1 (Enhance AA off, Sharpness
//   kept) is never entered: on the console (CUSA02299, 30/10, camera spin) it took rcas8 from 4.60 to only
//   4.05 ms, while level 2 took our warp/post from 6.25 to 3.46 ms. pb_eff still defines it.
//
// Step down: 2 late windows among the last 3, rcas8 >= 0.5 ms at the current level, at least 2 windows between two
// changes. A window does not count when it had a scene cut or a fade, real frames not generated, or fewer than 80 %
// of the flips its cadence expects (loading, transitions: CUSA02299 30/10 stepped 0 -> 1 -> 2 at 137-141 s on the
// tutorial-to-loading transition before these guards), nor a late window without motion (avg unmatched blocks < 20 and
// foreground in < 30 % of frames): the post-process costs most where pixels move, and a still late frame is not ours.
// Step up: one level after `hold` windows in a row without a late one (30 s; doubled, up to 4 min, each time the
// game is late again within 20 s of a step up, and back to 30 s after 5 min at level 0). No gain: still late in half of
// the windows 16 s after reaching the last level that can help -> back to level 0 and no step down for 5 min (doubled
// each time, up to 20 min): the lateness is not ours (a game bound by its CPU, a 27 fps scene).
// Off while frames are not generated or the menu is open, and for 15 windows after either ends or the player changes
// Sharpness / Enhance. FG Fast and the below-30 regime never step (level 0), but the detector keeps running there.
//
// Nothing to cede (CUSA18723, 30/10: Sharpness 0, AA off, below30 1): in the spin our warp/post went from 1.2-1.4 ms
// to 3.3 ms and the game from 28.4 to 27 real fps (29.1 with framegen=0), max dt 42-46 ms against 37-39 standing
// still. The real frame is late without any post-process: when 2 of the last 3 windows are late at level 0 and
// there is no rcas8 to cede (both settings off or rcas8 < 0.5 ms), or the regime does not step, the detector reports
// it once (PB_NOPOST, level unchanged) with our warp/post time, and again only after 15 clean windows.
#pragma once
#include <stdint.h>

#define PB_GRACE        15u    // windows ignored after (re)activation or a settings change
#define PB_LATE_IN3     2u     // late windows among the last 3 to step down
#define PB_DWELL        3u     // windows since the last change before stepping down again (2 in between)
#define PB_MIN_COST     50u    // rcas8 at the current level, 0.01 ms (0.5 ms)
#define PB_HOLD_MIN     15u    // clean windows in a row to step up (30 s)
#define PB_HOLD_MAX     120u   // 4 min
#define PB_REENTRY      10u    // a step down within 20 s of a step up to level 0 doubles the hold
#define PB_HOLD_RESET   150u   // windows at level 0 that reset hold and lock length (5 min)
#define PB_GAIN_WIN     8u     // windows at the last useful level before the no-gain check (16 s)
#define PB_LOCK_MIN     150u   // no step down for 5 min after a no-gain return
#define PB_LOCK_MAX     600u   // 20 min

enum { PB_NONE = 0, PB_LATE, PB_CLEAN, PB_NOGAIN, PB_INACTIVE, PB_SETTINGS, PB_NOPOST };
enum { PB_NOPOST_NONE = 0, PB_NOPOST_NOTHING, PB_NOPOST_REGIME };   // PbEvt.sub of PB_NOPOST

typedef struct {
    int enabled;           // option post_budget
    int diag;              // frames generated, menu closed: the detector runs
    int active;            // diag and not FG Fast, not the below-30 regime: the level may step down
    uint32_t win_n;        // flips in the window
    uint32_t win_us;       // window length
    uint32_t maxdt_us;     // longest flip interval in the window
    uint32_t vb_us;        // refresh period
    uint32_t cost_x100;    // rcas8 of the last GPU timing line, 0.01 ms
    uint32_t warp_x100;    // warp/post of the last GPU timing line (warp8 + rcas8 + osd), 0.01 ms
    int sharp, enh;        // the player's Sharpness (0..100) and Enhance AA (0..2)
    int disturbed;         // scene cut or fade frames, or real frames not generated, in the window
    int motion;            // avg unmatched blocks >= 20 or foreground in >= 30 % of the window's frames
} PbIn;

typedef struct {
    int level;
    uint32_t grace, dwell, clean, late_since, hold, lock, lock_len, since_up, at0;
    uint32_t hist;         // late bits of the last 3 windows, newest in bit 0
    int last_sharp, last_enh, seen, nopost_said;
} PostBudget;

typedef struct {
    int from, to, why, sub;
    uint32_t late3, late_since, dwell, maxdt_us, thr_us, cost_x100, warp_x100, hold, lock;
} PbEvt;

static inline void pb_reset(PostBudget *s)
{
    s->level = 0; s->grace = PB_GRACE; s->dwell = 0; s->clean = 0; s->late_since = 0; s->hist = 0;
    s->hold = PB_HOLD_MIN; s->lock = 0; s->lock_len = PB_LOCK_MIN; s->since_up = 0xFFFFu; s->at0 = 0;
    s->last_sharp = -1; s->last_enh = -1; s->seen = 0; s->nopost_said = 0;
}

// Effective Sharpness / Enhance AA at a level.
static inline void pb_eff(int level, int sharp, int enh, int *sharp_out, int *enh_out)
{
    *sharp_out = level >= 2 ? 0 : sharp;
    *enh_out = level >= 1 ? 0 : enh;
}

static inline int pb_same(int a, int b, int sharp, int enh)
{
    int sa, ea, sb, eb;
    pb_eff(a, sharp, enh, &sa, &ea);
    pb_eff(b, sharp, enh, &sb, &eb);
    return sa == sb && ea == eb;
}

// Next level down that changes the picture (and the cost), or `level` when none does.
static inline int pb_next(int level, int sharp, int enh)
{
    for (int l = level + 1; l <= 2; l++)
        if (l != 1 && !pb_same(l, level, sharp, enh)) return l;   // level 1 skipped (see the top)
    return level;
}

// Level one step up: the lowest level that shows the same as level - 1 (so a step up is never a no-op).
static inline int pb_prev(int level, int sharp, int enh)
{
    if (level <= 0) return 0;
    if (level == 2) return 0;                                       // level 1 is never entered
    int t = level - 1;
    while (t > 0 && pb_same(t - 1, t, sharp, enh)) t--;
    while (t > 0 && pb_same(t, level, sharp, enh)) t--;
    return t;
}

static inline uint32_t pb_pop3(uint32_t h) { return (h & 1u) + ((h >> 1) & 1u) + ((h >> 2) & 1u); }

// One STAT window. Returns 1 and fills *ev when the level changes.
static inline int pb_window(PostBudget *s, const PbIn *in, PbEvt *ev)
{
    ev->from = ev->to = s->level; ev->why = PB_NONE; ev->sub = PB_NOPOST_NONE; ev->warp_x100 = in->warp_x100;
    ev->late3 = 0; ev->late_since = 0; ev->dwell = 0; ev->maxdt_us = in->maxdt_us; ev->thr_us = 0; ev->cost_x100 = in->cost_x100;
    ev->hold = s->hold; ev->lock = s->lock;
    int settings = s->seen && (in->sharp != s->last_sharp || in->enh != s->last_enh);
    if (!in->enabled || !in->diag || settings || !s->seen) {
        s->seen = 1; s->last_sharp = in->sharp; s->last_enh = in->enh;
        s->hist = 0; s->clean = 0; s->dwell = 0; s->late_since = 0; s->grace = PB_GRACE; s->nopost_said = 0;
        if (s->level) {
            ev->why = settings ? PB_SETTINGS : PB_INACTIVE;
            s->level = 0; ev->to = 0;
            return 1;
        }
        return 0;
    }
    if (s->level && !in->active) {          // FG Fast or the below-30 regime started: back to the settings, no grace
        ev->why = PB_INACTIVE; ev->to = 0;
        s->level = 0; s->hist = 0; s->clean = 0; s->dwell = 0; s->late_since = 0;
        return 1;
    }
    if (s->grace) { s->grace--; return 0; }
    if (s->lock) s->lock--;
    if (s->since_up < 0xFFFFu) s->since_up++;
    if (!in->win_n || !in->vb_us) return 0;
    uint32_t n = (uint32_t)(((uint64_t)in->win_us + (uint64_t)in->win_n * in->vb_us / 2u) / ((uint64_t)in->win_n * in->vb_us));
    if (n < 1u) n = 1u;
    if (n > 4u) n = 4u;
    uint32_t thr = (2u * n + 1u) * in->vb_us / 2u, cap = (2u * n + 5u) * in->vb_us / 2u;
    ev->thr_us = thr;
    if (in->maxdt_us > cap) { s->hist = 0; s->clean = 0; return 0; }   // a stall that is not ours
    if (in->disturbed || (uint64_t)in->win_n * n * in->vb_us * 10u < (uint64_t)in->win_us * 8u) {   // loading, transition
        s->hist = 0; s->clean = 0;
        return 0;
    }
    int late = in->maxdt_us > thr;
    if (late && !in->motion) return 0;   // late standing still: neither late nor clean
    s->hist = ((s->hist << 1) | (uint32_t)late) & 0x7u;
    s->clean = late ? 0u : s->clean + 1u;
    if (s->clean >= PB_HOLD_MIN) s->nopost_said = 0;
    s->dwell++;
    s->late_since += (uint32_t)late;
    if (s->level == 0 && !s->lock) {
        if (++s->at0 >= PB_HOLD_RESET) { s->hold = PB_HOLD_MIN; s->lock_len = PB_LOCK_MIN; s->at0 = PB_HOLD_RESET; }
    }
    int nxt = pb_next(s->level, in->sharp, in->enh);
    ev->late3 = pb_pop3(s->hist); ev->late_since = s->late_since; ev->dwell = s->dwell;
    if (ev->late3 >= PB_LATE_IN3 && s->level == 0 && !s->nopost_said &&
        (nxt == s->level || in->cost_x100 < PB_MIN_COST || !in->active)) {   // late, but no rcas8 to cede here
        s->nopost_said = 1;
        ev->why = PB_NOPOST; ev->sub = (nxt == s->level || in->cost_x100 < PB_MIN_COST) ? PB_NOPOST_NOTHING : PB_NOPOST_REGIME;
        return 1;
    }
    if (ev->late3 >= PB_LATE_IN3 && nxt != s->level && in->cost_x100 >= PB_MIN_COST && s->dwell >= PB_DWELL && !s->lock &&
        in->active) {
        if (s->level == 0 && s->since_up <= PB_REENTRY) s->hold = s->hold * 2u > PB_HOLD_MAX ? PB_HOLD_MAX : s->hold * 2u;
        ev->why = PB_LATE; ev->to = nxt; ev->hold = s->hold;
        s->level = nxt; s->hist = 0; s->dwell = 0; s->late_since = 0; s->clean = 0; s->at0 = 0;
        return 1;
    }
    if (s->level > 0 && s->dwell >= PB_GAIN_WIN && s->late_since * 2u >= s->dwell) {
        ev->why = PB_NOGAIN; ev->to = 0;
        s->lock = s->lock_len; ev->lock = s->lock;
        s->lock_len = s->lock_len * 2u > PB_LOCK_MAX ? PB_LOCK_MAX : s->lock_len * 2u;
        s->level = 0; s->hist = 0; s->dwell = 0; s->late_since = 0; s->clean = 0; s->since_up = 0xFFFFu; s->at0 = 0;
        return 1;
    }
    if (s->level > 0 && s->clean >= s->hold) {
        int to = pb_prev(s->level, in->sharp, in->enh);
        ev->why = PB_CLEAN; ev->to = to;
        s->level = to; s->dwell = 0; s->late_since = 0; s->clean = 0;
        if (to == 0) s->since_up = 0;
        return 1;
    }
    return 0;
}
