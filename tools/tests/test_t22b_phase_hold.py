#!/usr/bin/env python3
"""T22b Continuous VBlank Phase Hold Tests for dbg_rec_shift=1.

Verifies:
1. flip_vblank_count() and rec_shift_on_flip() in classic/source/main.c:
   - Exact processTime correction matching VB_PARITY in mon_thread:
     vs.processTime > fs.processTime + 8341 -> vs.count - 1
     fs.processTime > vs.processTime + 8341 -> vs.count + 1
   - Continuous holding when dbg_rec_shift=1: if >= 8 consecutive mid frames land on odd VBlanks
     (flip_vb mod 2 == 1), requests another one-shot +1 VBlank shift (at most once per 2 s) and logs the reason.
   - Streaks of < 8 odd mid frames reset to 0 as soon as an even mid frame occurs.
2. Bit-exactness when dbg_rec_shift=0:
   - Both pace_vblank_ex and pace_pair_ex produce bit-exact identical trajectories (including across hitches)
     when dbg_rec_shift=0.
3. Synthetic hitch ("рывок") sequence in both pace_vblank_ex (including 4-deep queue / g_pace_off=-1) and pace_pair_ex:
   - After initial lock (mid on even VBlanks, real on odd VBlanks), a hitch inverts VBlank phase so mid lands on odd VBlanks.
   - Continuous phase hold detects 8 consecutive odd mid frames, applies +1 VBlank shift with log reason, and restores
     mid to even VBlanks within <= 1.0 s (~0.30 s).
   - Rate-limiting: a second phase inversion within < 2.0 s waits until the 2.0 s cooldown expires before shifting again.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C = ROOT / "classic" / "source" / "main.c"
CC = shutil.which("clang") or shutil.which("cc")


def extract_function(src: str, name: str) -> str:
    m = re.search(r"^static (?:inline )?[^\n]+\b" + name + r"\([^;]*\)\s*\{", src, re.M)
    if m is None:
        raise AssertionError(f"Function {name} not found in classic/source/main.c")
    pos = src.index("{", m.start())
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[m.start():end]


class TestT22bPhaseHold(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_c = MAIN_C.read_text(encoding="utf-8", errors="replace")

    def test_1_flip_vblank_parity_and_streak_detector(self) -> None:
        """Verify flip_vblank_count processTime correction, >=8 odd mid streak trigger, 2s cooldown, and reason log."""
        if CC is None:
            self.skipTest("no host C compiler")

        fns = "\n\n".join(
            extract_function(self.main_c, fn)
            for fn in [
                "flip_vblank_count",
                "rec_shift_note_event",
                "rec_shift_on_flip",
            ]
        )

        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#define REC_SHIFT_STREAK_THR  8u
#define REC_SHIFT_COOLDOWN_US 2000000ull

typedef struct {{ int dbg_rec_shift; }} fg_config_t;
static fg_config_t g_cfg;
static volatile int g_rec_shift_applied = 0;
static uint32_t g_rec_odd_streak = 0;
static uint64_t g_rec_last_shift_us = 0;
static const char * volatile g_rec_last_event = NULL;
static const char * volatile g_rec_shift_reason = "init";

static char g_log_buf[4096];
static void lg(const char *fmt, ...) {{
    char tmp[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (strlen(g_log_buf) + strlen(tmp) + 1 < sizeof(g_log_buf))
        strcat(g_log_buf, tmp);
}}

{fns}

int main(void) {{
    /* 1. Verify flip_vblank_count processTime correction (matches mon_thread VB_PARITY) */
    if (flip_vblank_count(100ull, 100000ull, 100000ull) != 100ull) return 1;
    if (flip_vblank_count(100ull, 108341ull, 100000ull) != 100ull) return 2;
    if (flip_vblank_count(100ull, 108342ull, 100000ull) != 99ull) return 3;
    if (flip_vblank_count(100ull, 100000ull, 108341ull) != 100ull) return 4;
    if (flip_vblank_count(100ull, 100000ull, 108342ull) != 101ull) return 5;

    /* 2. When dbg_rec_shift == 0, odd mid frames never trigger a shift */
    g_cfg.dbg_rec_shift = 0;
    g_rec_shift_applied = 1;
    for (int i = 0; i < 20; i++) {{
        if (rec_shift_on_flip(1, 101ull + i * 2ull, 1000000ull + i * 33366ull) != 0) return 6;
    }}
    if (g_rec_shift_applied != 1 || g_rec_odd_streak != 0) return 7;

    /* 3. When dbg_rec_shift == 1, 7 odd mids followed by 1 even mid resets streak without shifting */
    g_cfg.dbg_rec_shift = 1;
    g_rec_shift_applied = 1;
    g_rec_last_shift_us = 0;
    uint64_t t = 5000000ull;
    for (int i = 0; i < 7; i++) {{
        if (rec_shift_on_flip(1, 201ull + i * 2ull, t) != 0) return 8;
        if (rec_shift_on_flip(0, 202ull + i * 2ull, t + 16683ull) != 0) return 9;
        t += 33366ull;
    }}
    if (g_rec_odd_streak != 7) return 10;
    /* Even mid resets streak */
    if (rec_shift_on_flip(1, 216ull, t) != 0) return 11;
    if (g_rec_odd_streak != 0 || g_rec_shift_applied != 1) return 12;

    /* 4. 8 consecutive odd mids trigger a shift request (g_rec_shift_applied < 0) and log reason */
    rec_shift_note_event("real_hold");
    g_log_buf[0] = 0;
    for (int i = 0; i < 7; i++) {{
        if (rec_shift_on_flip(1, 301ull + i * 2ull, t) != 0) return 13;
        t += 33366ull;
    }}
    if (g_rec_shift_applied != 1) return 14;
    if (rec_shift_on_flip(1, 315ull, t) != 1) return 15;
    if (g_rec_shift_applied >= 0) return 16;
    if (strstr(g_log_buf, "reason=real_hold") == NULL) return 17;

    /* Simulate pacer consuming the shift at time t */
    g_rec_shift_applied = -g_rec_shift_applied + 1; /* becomes 2 */
    uint64_t first_shift_t = g_rec_last_shift_us;
    if (first_shift_t != t) return 18;

    /* 5. Cooldown check: 8 odd mids only 0.5 s later must NOT trigger until t >= first_shift_t + 2.0 s */
    t = first_shift_t + 500000ull;
    rec_shift_note_event("single_flip");
    for (int i = 0; i < 8; i++) {{
        if (rec_shift_on_flip(1, 401ull + i * 2ull, t) != 0) return 19;
        t += 33366ull;
    }}
    if (g_rec_shift_applied != 2) return 20;
    /* Advance to 1.99 s after first_shift_t: still rate-limited */
    if (rec_shift_on_flip(1, 419ull, first_shift_t + 1990000ull) != 0) return 21;
    /* At exactly 2.00 s after first_shift_t: triggers immediately! */
    g_log_buf[0] = 0;
    if (rec_shift_on_flip(1, 421ull, first_shift_t + 2000000ull) != 1) return 22;
    if (g_rec_shift_applied >= 0) return 23;
    if (strstr(g_log_buf, "reason=single_flip") == NULL) return 24;

    printf("STREAK_DETECTOR_OK\\n");
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "t_streak.c"
            exe = pathlib.Path(td) / "t_streak"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"t_streak failed with exit code {res.returncode}: {res.stderr}")
            self.assertIn("STREAK_DETECTOR_OK", res.stdout)

    def test_2_synthetic_hitch_recovery_vblank_and_timer(self) -> None:
        """Verify synthetic hitch inverts phase and continuous hold restores mid to even VBlanks in <= 1s."""
        if CC is None:
            self.skipTest("no host C compiler")

        fns = "\n\n".join(
            extract_function(self.main_c, fn)
            for fn in [
                "flip_vblank_count",
                "rec_shift_note_event",
                "rec_shift_on_flip",
                "vblank_period_us",
                "pace_pcopy",
                "pace_vblank_ex",
                "pace_pair_ex",
            ]
        )

        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#define PLL_LEAD_US           2200u
#define REC_SHIFT_STREAK_THR  8u
#define REC_SHIFT_COOLDOWN_US 2000000ull

typedef struct {{ int64_t count; uint64_t processTime; uint64_t tsc; uint32_t flags; int32_t currentBuffer; uint32_t flipArg[4]; }} VoFlipStatus;
typedef struct {{ int present_copy, dbg_mark_gen, dbg_rec_shift; }} fg_config_t;

static fg_config_t g_cfg;
static int g_mon_on = 1, g_opt_early = 0, g_pcopy_on = 0, g_s14_tail_extra = 0;
static int g_ngame = 3, g_mbase = 3, g_last_real_buf = -1, g_handle = 1;
static uint32_t g_flips = 0;
static uint64_t g_dbl_frames = 0;
static int64_t g_pace_off = 0;
static volatile int g_rec_shift_applied = 0;
static uint32_t g_rec_odd_streak = 0;
static uint64_t g_rec_last_shift_us = 0;
static const char * volatile g_rec_last_event = NULL;
static const char * volatile g_rec_shift_reason = "init";
static uint32_t g_pace_resync = 0, g_pace_timeouts = 0, g_pace_fix = 0;
static volatile uint64_t g_mids_shown = 0, g_reals_shown = 0;
static volatile uint64_t g_rel_t = 0;
static volatile uint32_t g_pll_period_q8 = (16683u << 8) | 85u;
static uint64_t g_freq = 1000000ull;
static uint64_t g_pair_next = 0;

static char g_log_buf[8192];
static void lg(const char *fmt, ...) {{
    char tmp[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (strlen(g_log_buf) + strlen(tmp) + 1 < sizeof(g_log_buf))
        strcat(g_log_buf, tmp);
}}

/* Discrete-event simulation clock & hardware VBlank flip queue */
static uint64_t sim_now_us = 1000000ull;
static uint64_t sim_vb_period_us = 16683ull;
static uint64_t sim_vb_origin_us = 1000000ull;
static uint64_t sim_last_vb_cnt = 0;

typedef struct {{
    uint64_t latch_vb;
    int is_mid;
    int done;
}} SimFlip;
static SimFlip sim_q[256];
static int sim_qn = 0;

static inline uint64_t flip_vblank_count(uint64_t vs_count, uint64_t vs_pt, uint64_t fs_pt);
static int rec_shift_on_flip(int is_mid, uint64_t flip_vb, uint64_t now_us);

static void sim_advance_to(uint64_t target_us) {{
    if (target_us <= sim_now_us) return;
    sim_now_us = target_us;
    uint64_t vb_cnt = (sim_now_us - sim_vb_origin_us) / sim_vb_period_us;
    if (vb_cnt > sim_last_vb_cnt) {{
        for (uint64_t v = sim_last_vb_cnt + 1; v <= vb_cnt; v++) {{
            uint64_t vb_pt = sim_vb_origin_us + v * sim_vb_period_us;
            for (int i = 0; i < sim_qn; i++) {{
                if (!sim_q[i].done && sim_q[i].latch_vb <= v) {{
                    sim_q[i].done = 1;
                    if (sim_q[i].is_mid) g_mids_shown++;
                    else g_reals_shown++;
                    uint64_t fvb = flip_vblank_count(v, vb_pt + 400ull, vb_pt + 50ull);
                    rec_shift_on_flip(sim_q[i].is_mid, fvb, vb_pt + 400ull);
                }}
            }}
        }}
        sim_last_vb_cnt = vb_cnt;
    }}
}}

static uint64_t sceKernelGetProcessTime(void) {{ return sim_now_us; }}
static uint64_t sceKernelGetProcessTimeCounter(void) {{ return sim_now_us; }}
static void sceKernelUsleep(uint32_t us) {{ sim_advance_to(sim_now_us + us); }}
static void cc_wait_until(uint64_t target_tsc) {{ sim_advance_to(target_tsc); }}
static uint32_t cc_pending_flips(void) {{
    uint32_t p = 0;
    for (int i = 0; i < sim_qn; i++) if (!sim_q[i].done) p++;
    return p;
}}
static void pace_30(void) {{}}
static int read_flip_status(int h, VoFlipStatus *fs) {{ (void)h; (void)fs; return -1; }}
static uint64_t vb_ticks(uint64_t freq, uint64_t period_q8, uint32_t nvb) {{
    (void)freq; (void)period_q8;
    return nvb * sim_vb_period_us;
}}
static uint64_t refresh_period_q8(void) {{ return g_pll_period_q8; }}
static uint32_t vblank_to_next_edge_us(int32_t h, uint64_t now_us, uint32_t *out_vb_us) {{
    (void)h;
    if (out_vb_us) *out_vb_us = (uint32_t)sim_vb_period_us;
    uint64_t elapsed = (now_us - sim_vb_origin_us) % sim_vb_period_us;
    return (uint32_t)(sim_vb_period_us - elapsed);
}}
static uint32_t pll_adaptive_hold_us(int32_t h, uint64_t now_us, uint64_t dt_enter_us, uint32_t req_hold_us) {{
    (void)h; (void)now_us; (void)dt_enter_us;
    return req_hold_us;
}}

{fns}

static void sim_submit_pair(uint64_t *mid_vb_out, uint64_t *real_vb_out) {{
    uint64_t cur_vb = (sim_now_us - sim_vb_origin_us) / sim_vb_period_us;
    uint64_t last_queued_vb = cur_vb;
    for (int i = 0; i < sim_qn; i++) {{
        if (!sim_q[i].done && sim_q[i].latch_vb > last_queued_vb)
            last_queued_vb = sim_q[i].latch_vb;
    }}
    uint64_t mid_vb = last_queued_vb + 1;
    uint64_t real_vb = last_queued_vb + 2;
    *mid_vb_out = mid_vb;
    *real_vb_out = real_vb;
    sim_q[sim_qn++] = (SimFlip){{ mid_vb, 1, 0 }};
    sim_q[sim_qn++] = (SimFlip){{ real_vb, 0, 0 }};
}}

static void reset_sim(int shift_cfg) {{
    g_cfg.dbg_rec_shift = shift_cfg;
    g_dbl_frames = 0; g_pace_off = 0; g_rec_shift_applied = 0;
    g_rec_odd_streak = 0; g_rec_last_shift_us = 0;
    g_rec_last_event = NULL; g_rec_shift_reason = "init";
    g_mids_shown = 0; g_reals_shown = 0; sim_qn = 0;
    g_pair_next = 0;
    sim_now_us = 1000000ull + 5000ull;
    sim_vb_origin_us = 1000000ull;
    sim_last_vb_cnt = 0;
    g_log_buf[0] = 0;
}}

int main(void) {{
    /* A. VBlank pacer synthetic hitch test (with 2-pair and 4-pair queue depth) */
    for (int deep_q = 0; deep_q <= 1; deep_q++) {{
        reset_sim(1);
        uint64_t mid_vbs[60], real_vbs[60];
        uint64_t hitch_t_us = 0, recov_t_us = 0;
        int hitch_frame = 15;
        int recov_frame = -1;

        for (int f = 0; f < 50; f++) {{
            g_flips = (uint32_t)(f + 1);
            if (deep_q && f == 3) {{
                /* Simulate CUSA00411 g_pace_off = -1 (4-flip deep queue) */
                g_pace_off = -1;
            }}
            pace_vblank_ex((f + 1) % 3, (f + 2) % 3, f % 3, 1, 33366ull, 1);
            sim_submit_pair(&mid_vbs[f], &real_vbs[f]);

            if (f == hitch_frame) {{
                /* Inject a "рывок" (hitch): drain queue + 1 extra VBlank so real holds for 2 VBlanks,
                 * inverting the VBlank phase of subsequent mid frames from even to odd! */
                uint64_t target_vb = real_vbs[f] + 1;
                sim_advance_to(sim_vb_origin_us + target_vb * sim_vb_period_us + 5000ull);
                g_pace_off = (int64_t)g_reals_shown - (int64_t)g_dbl_frames - (deep_q ? 1 : 0);
                rec_shift_note_event("real_hold");
                hitch_t_us = sim_now_us;
            }} else {{
                sim_advance_to(sim_now_us + 10000ull);
            }}

            if (f > hitch_frame && recov_frame < 0 && (mid_vbs[f] & 1ull) == 0ull) {{
                recov_frame = f;
                recov_t_us = sim_now_us;
            }}
        }}

        /* Before hitch (frames 2..hitch_frame): mid is on EVEN VBlanks */
        for (int f = 2; f <= hitch_frame; f++) {{
            if ((mid_vbs[f] & 1ull) != 0ull || (real_vbs[f] & 1ull) != 1ull) return 10 + deep_q;
        }}
        /* Immediately after hitch (frames hitch_frame+1 .. hitch_frame+8): mid flipped to ODD VBlanks */
        for (int f = hitch_frame + 1; f <= hitch_frame + 8; f++) {{
            if ((mid_vbs[f] & 1ull) != 1ull) return 20 + deep_q;
        }}
        /* Recovery must happen within <= 1.0 s (1000000 us) after hitch */
        if (recov_frame < 0) return 30 + deep_q;
        if (recov_t_us - hitch_t_us > 1000000ull) return 40 + deep_q;
        /* After recovery (recov_frame .. 49): mid stays locked on EVEN VBlanks */
        for (int f = recov_frame; f < 50; f++) {{
            if ((mid_vbs[f] & 1ull) != 0ull || (real_vbs[f] & 1ull) != 1ull) return 50 + deep_q;
        }}
        if (strstr(g_log_buf, "reason=real_hold") == NULL) return 60 + deep_q;
        printf("VBLANK_HITCH_DEEP%d: hitch_f=%d recov_f=%d dt_ms=%.1f shifts=%d\\n",
               deep_q, hitch_frame, recov_frame, (recov_t_us - hitch_t_us) / 1000.0, g_rec_shift_applied);
    }}

    /* B. Timer pacer (pace_pair_ex) synthetic hitch test */
    {{
        reset_sim(1);
        uint64_t mid_vbs[60], real_vbs[60];
        uint64_t hitch_t_us = 0, recov_t_us = 0;
        int hitch_frame = 15;
        int recov_frame = -1;

        for (int f = 0; f < 50; f++) {{
            g_flips = (uint32_t)(f + 1);
            pace_pair_ex(1, 33366ull, 1, 2u);
            sim_submit_pair(&mid_vbs[f], &real_vbs[f]);

            if (f == hitch_frame) {{
                /* Inject a 3-VBlank hitch (~50 ms) that shifts timer pacer onto odd mid VBlanks */
                uint64_t target_vb = real_vbs[f] + 1;
                sim_advance_to(sim_vb_origin_us + target_vb * sim_vb_period_us + 5000ull);
                g_pair_next = sim_now_us + 2 * sim_vb_period_us;
                rec_shift_note_event("timer_overrun");
                hitch_t_us = sim_now_us;
            }} else {{
                sim_advance_to(sim_now_us + 10000ull);
            }}

            if (f > hitch_frame && recov_frame < 0 && (mid_vbs[f] & 1ull) == 0ull) {{
                recov_frame = f;
                recov_t_us = sim_now_us;
            }}
        }}

        for (int f = 2; f <= hitch_frame; f++) {{
            if ((mid_vbs[f] & 1ull) != 0ull || (real_vbs[f] & 1ull) != 1ull) return 70;
        }}
        for (int f = hitch_frame + 1; f <= hitch_frame + 8; f++) {{
            if ((mid_vbs[f] & 1ull) != 1ull) return 71;
        }}
        if (recov_frame < 0) return 72;
        if (recov_t_us - hitch_t_us > 1000000ull) return 73;
        for (int f = recov_frame; f < 50; f++) {{
            if ((mid_vbs[f] & 1ull) != 0ull || (real_vbs[f] & 1ull) != 1ull) return 74;
        }}
        if (strstr(g_log_buf, "reason=timer_overrun") == NULL) return 75;
        printf("TIMER_HITCH: hitch_f=%d recov_f=%d dt_ms=%.1f shifts=%d\\n",
               hitch_frame, recov_frame, (recov_t_us - hitch_t_us) / 1000.0, g_rec_shift_applied);
    }}

    /* C. Verify dbg_rec_shift=0 is bit-exact identical to baseline (never shifts even across hitches) */
    {{
        reset_sim(0);
        uint64_t mid_vbs[50], real_vbs[50], rel_ts[50];
        for (int f = 0; f < 50; f++) {{
            g_flips = (uint32_t)(f + 1);
            pace_vblank_ex((f + 1) % 3, (f + 2) % 3, f % 3, 1, 33366ull, 1);
            rel_ts[f] = g_rel_t;
            sim_submit_pair(&mid_vbs[f], &real_vbs[f]);
            if (f == 15) {{
                uint64_t target_vb = real_vbs[f] + 1;
                sim_advance_to(sim_vb_origin_us + target_vb * sim_vb_period_us + 5000ull);
                g_pace_off = (int64_t)g_reals_shown - (int64_t)g_dbl_frames;
                rec_shift_note_event("real_hold");
            }} else {{
                sim_advance_to(sim_now_us + 10000ull);
            }}
        }}
        if (g_rec_shift_applied != 0 || g_rec_odd_streak != 0 || g_log_buf[0] != 0) return 80;
        /* Before f=15: unshifted mid is on odd VBlanks; after 1-vb hitch at f=15: stays on even VBlanks forever */
        for (int f = 2; f <= 15; f++) {{
            if ((mid_vbs[f] & 1ull) != 1ull || (real_vbs[f] & 1ull) != 0ull) return 81;
            if (mid_vbs[f] - mid_vbs[f - 1] != 2ull) return 82;
        }}
        for (int f = 16; f < 50; f++) {{
            if ((mid_vbs[f] & 1ull) != 0ull || (real_vbs[f] & 1ull) != 1ull) return 83;
            if (f > 16 && mid_vbs[f] - mid_vbs[f - 1] != 2ull) return 84;
        }}
        (void)rel_ts;
    }}

    printf("SYNTH_HITCH_RECOVERY_OK\\n");
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "t_hitch.c"
            exe = pathlib.Path(td) / "t_hitch"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"t_hitch failed with exit code {res.returncode}: {res.stderr}")
            self.assertIn("SYNTH_HITCH_RECOVERY_OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
