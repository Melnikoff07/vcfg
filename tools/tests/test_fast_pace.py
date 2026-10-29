#!/usr/bin/env python3
"""FG Fast pacing (pace_policy.h fast_slots / fast_phases_q16, opt-in file fastpace_<TITLE>).

1. fast_pace 0 is FG Fast of v2.35 bit for bit: the old inline code of main.c, kept below as a reference, against the
   helpers over a deterministic sweep of frame intervals, pending counts and accumulator states.
2. fast_pace 1: the queue guard reads the flips ahead in refreshes (FAST_QCUT_Q8), never the pending count; the phases
   are exactly 1/3, 2/3 and 1/2; the accumulator itself is unchanged (same slot mix at 20-30 fps).
3. Buffer order: with g_flips % 2 the pair after a 3-slot pair writes its first generated frame into the buffer flipped
   last (its WaitFlipDone waits for the real after it); with mid_lru it never does.
4. main.c: the Fast branch goes through the helpers with g_fast_pace, mid_lru covers Fast with or without it, the
   option is read once from LOG_DIR/fastpace_<TITLE> (default off) and logged.

Same compiler choice as test_pace_policy.py (config.json llvm_bin, otherwise the host clang/cc).
"""
import re
import unittest

from test_pace_policy import MAIN_C, compile_and_run, extract_function, rows

HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "pace_policy.h"
/* v2.35 main.c FG Fast block (before fast_slots), verbatim apart from the variable plumbing */
static int legacy(int32_t *g_fast_acc_q16, uint32_t g_ema_frame_us, uint32_t pending, uint32_t *t1_q16, uint32_t *t2_q16)
{
    uint32_t dt_clamped = g_ema_frame_us;
    if (dt_clamped < 25000u) dt_clamped = 25000u;
    if (dt_clamped > 50000u) dt_clamped = 50000u;
    int32_t r_q16 = (int32_t)(((uint64_t)dt_clamped << 16) / 16683ull);
    int32_t acc_prev = *g_fast_acc_q16;
    int32_t acc_new = acc_prev + r_q16;
    int m_out = (int)((acc_new + 32768) >> 16);
    if (m_out < 2) m_out = 2;
    if (m_out > 3) m_out = 3;
    if (pending >= 3u) m_out = 2;
    *g_fast_acc_q16 = acc_new - (m_out << 16);
    if (*g_fast_acc_q16 < -49152) *g_fast_acc_q16 = -49152;
    if (*g_fast_acc_q16 >  49152) *g_fast_acc_q16 =  49152;
    int32_t phase_shift = (acc_prev * 3) >> 5;
    if (m_out == 3) {
        int32_t p1 = 21845 + phase_shift;
        int32_t p2 = 43690 + phase_shift;
        if (p1 < 16384) p1 = 16384; if (p1 > 27525) p1 = 27525;
        if (p2 < 38010) p2 = 38010; if (p2 > 49152) p2 = 49152;
        *t1_q16 = (uint32_t)p1;
        *t2_q16 = (uint32_t)p2;
    } else {
        int32_t p1 = 32768 + phase_shift;
        if (p1 < 22937) p1 = 22937; if (p1 > 42598) p1 = 42598;
        *t1_q16 = (uint32_t)p1;
    }
    return m_out;
}
static uint32_t lcg = 12345u;
static uint32_t rnd(void) { lcg = lcg * 1103515245u + 12345u; return lcg >> 8; }
int main(void)
{
    /* 1. fast_pace 0 == v2.35 */
    uint32_t diff = 0, n = 0;
    int32_t a_old = 0, a_new = 0;
    for (int i = 0; i < 200000; i++) {
        uint32_t ema = 15000u + rnd() % 60000u, pend = rnd() % 6u;
        if ((i & 1023) == 0) { a_old = a_new = (int32_t)(rnd() % 98305u) - 49152; }
        uint32_t o1 = 32768u, o2 = 43690u, n1 = 32768u, n2 = 43690u;
        int m_old = legacy(&a_old, ema, pend, &o1, &o2);
        int32_t prev = 0;
        int m_new = (int)fast_slots(&a_new, ema, 0, pend, 9999u, &prev);
        fast_phases_q16((uint32_t)m_new, prev, 0, &n1, &n2);
        diff += (m_old != m_new) || (a_old != a_new) || (o1 != n1) || (o2 != n2);
        n++;
    }
    printf("legacy_diff %u of %u\n", diff, n);
    /* 2. fast_pace 1: queue cut in refreshes, pending ignored */
    {
        int32_t acc = 0, prev = 0;
        uint32_t m_edge = 0, m_over = 0, m_pend = 0;
        acc = 0; m_edge = fast_slots(&acc, 50000u, 1, 5u, FAST_QCUT_Q8, &prev);
        acc = 0; m_over = fast_slots(&acc, 50000u, 1, 0u, FAST_QCUT_Q8 + 1u, &prev);
        acc = 0; m_pend = fast_slots(&acc, 50000u, 0, 3u, 0u, &prev);
        printf("qcut %u %u old_pending %u\n", m_edge, m_over, m_pend);
    }
    {
        uint32_t t1 = 0, t2 = 777u;
        fast_phases_q16(3u, 40000, 1, &t1, &t2);
        printf("ph3 %u %u ", t1, t2);
        t1 = 0; t2 = 777u;
        fast_phases_q16(2u, -40000, 1, &t1, &t2);
        printf("ph2 %u %u ", t1, t2);
        t1 = 0; t2 = 777u;
        fast_phases_q16(2u, 40000, 0, &t1, &t2);
        printf("ph2old %u %u\n", t1, t2);
    }
    {   /* slot mix with the queue far away: same accumulator for both settings */
        static const double fps[5] = { 20.0, 24.0, 26.0, 28.0, 30.0 };
        for (int k = 0; k < 5; k++) {
            int32_t a0 = 0, a1 = 0, pv = 0;
            uint32_t s0 = 0, s1 = 0;
            for (int i = 0; i < 6000; i++) {
                uint32_t ema = (uint32_t)(1e6 / fps[k]);
                s0 += fast_slots(&a0, ema, 0, 0u, 0u, &pv);
                s1 += fast_slots(&a1, ema, 1, 0u, 0u, &pv);
            }
            printf("mix%d %.4f %.4f\n", (int)fps[k], s0 / 6000.0, s1 / 6000.0);
        }
    }
    /* 3. buffer order after a 3-slot pair: pattern 3,2,2,3,3,2,3,2,2,2,... */
    {
        static const int slots[16] = { 3, 2, 2, 3, 3, 2, 3, 2, 2, 2, 3, 2, 3, 3, 2, 2 };
        MidLru l; mid_lru_reset(&l, 2u);
        int last_flipped = -1, hit_mod = 0, hit_lru = 0, after3 = 0;
        int last_mod = -1;
        for (int k = 0; k < 16; k++) {
            uint32_t mod1 = (uint32_t)k % 2u, mod2 = (uint32_t)(k + 1) % 2u;
            uint32_t lru1 = mid_lru_pick(&l, 0u), lru2 = mid_lru_pick(&l, 1u);
            if (k && slots[k - 1] == 3) { after3++; hit_mod += (int)mod1 == last_mod; hit_lru += (int)lru1 == last_flipped; }
            last_mod = slots[k] == 3 ? (int)mod2 : (int)mod1;
            mid_lru_flipped(&l, lru1, slots[k] == 3, lru2);
            last_flipped = slots[k] == 3 ? (int)lru2 : (int)lru1;
        }
        printf("after3 %d mod_writes_last %d lru_writes_last %d\n", after3, hit_mod, hit_lru);
    }
    return 0;
}
"""


class TestFastPacePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = rows(compile_and_run(HARNESS))

    def test_off_is_v235_bit_for_bit(self) -> None:
        self.assertEqual(self.r["legacy_diff"], "0 of 200000")

    def test_queue_cut_in_refreshes(self) -> None:
        self.assertEqual(self.r["qcut"], "3 2 old_pending 2",
                         "fast_pace 1 keeps the 3rd slot up to FAST_QCUT_Q8 ahead whatever the pending count; 0 keeps the guard")

    def test_phases(self) -> None:
        self.assertEqual(self.r["ph3"], "21845 43691 ph2 32768 777 ph2old 36518 777")

    def test_accumulator_unchanged(self) -> None:
        for f, want in (("20", 3.0), ("24", 2.5), ("26", 2.31), ("28", 2.14), ("30", 2.0)):
            old, new = (float(x) for x in self.r["mix" + f].split())
            self.assertEqual(old, new)
            self.assertAlmostEqual(new, want, delta=0.02, msg=f"{f} fps")

    def test_lru_never_writes_the_buffer_flipped_last(self) -> None:
        m = re.match(r"(\d+) mod_writes_last (\d+) lru_writes_last (\d+)", self.r["after3"])
        after3, mod_hits, lru_hits = (int(x) for x in m.groups())
        self.assertEqual(after3, 7)
        self.assertEqual(mod_hits, after3, "g_flips % 2: every pair after a 3-slot pair waits on the buffer flipped last")
        self.assertEqual(lru_hits, 0)


class TestFastPaceMainC(unittest.TestCase):
    def test_fast_branch_uses_the_helpers(self) -> None:
        i = MAIN_C.index("// 1.1 FG Fast (aggressive 60 Hz output target")
        block = MAIN_C[i:MAIN_C.index("} else if (!g_fg_off) {", i)]
        self.assertIn("fast_slots(&g_fast_acc_q16, g_ema_frame_us, g_fast_pace, f_pend, f_ahead, &acc_prev)", block)
        self.assertIn("fast_phases_q16((uint32_t)m_out, acc_prev, g_fast_pace, &f_t1, &f_t2)", block)
        self.assertIn("uint32_t f_pend = cc_pending_flips(), f_ahead = 0;", block)
        self.assertIn("f_ahead = b30_queue_ahead_q8(f_pend, to_vb, refresh_period_q8());", block)
        self.assertNotIn("phase_shift", block)
        self.assertNotIn("dt_clamped", block)

    def test_mid_lru_covers_fast_with_or_without_the_option(self) -> None:
        # m-int: FG Fast always takes the mid_lru order (m-perf b1eb8b5); fast_pace only changes the cut and the phases
        expr = ("int mid_lru_on = (g_cfg.below30 || g_cfg.preset == 1) && !(pcopy && g_pcopy_k >= 2) && g_nmid >= 2 && "
                "(uint32_t)g_nmid <= MID_LRU_MAX;")
        self.assertIn(expr, MAIN_C)
        i = MAIN_C.index(expr)
        self.assertTrue(MAIN_C[i + len(expr):].lstrip().startswith("if (mid_lru_on) {"))
        self.assertEqual(len(re.findall(r"\bmid_lru_on\s*=", MAIN_C)), 1)
        cuts = extract_function(MAIN_C, "static void cc_read_cuts(void)")
        self.assertNotIn("mid_lru", cuts[cuts.index('lg("fast pace: %d'):])

    def test_option_is_an_opt_in_file_default_off(self) -> None:
        self.assertIn("static int g_fast_pace = 0;", MAIN_C)
        cuts = extract_function(MAIN_C, "static void cc_read_cuts(void)")
        self.assertIn('g_fast_pace = cc_file("fastpace");', cuts)
        self.assertIn('lg("fast pace: %d', cuts)
        self.assertEqual(len(re.findall(r"\bg_fast_pace\s*=", MAIN_C)), 2)


if __name__ == "__main__":
    unittest.main()
