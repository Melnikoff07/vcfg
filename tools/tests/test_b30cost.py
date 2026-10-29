#!/usr/bin/env python3
"""Below-30 regime cost (q-b30cost): generated-frame buffer order (mid_lru) and the regime fed without the GPU time it
adds itself (b30x), both part of the regime with below30 on; classic/source/pace_policy.h compiled and executed on the
host, plus the call sites in classic/source/main.c.

1. mid_lru: with 2-slot pairs only it is today's round robin (g_mbase + g_flips % g_nmid); after a 3-slot pair the next
   pair writes its first generated frame into the buffer flipped longest ago, never the one flipped last.
2. b30x / b30_feed_us: fence + the 2nd generated frame's block beyond the 2-slot post block, EMA 1/8; own - extra,
   0 stays 0, never below half the own time.
3. Closed loop with the helpers above: a GPU-serialised game of ~36 ms per frame (the console logs of 27/10: the game
   span between two of our DCBs stays 32.7 ms with the regime off and on), VideoOut one flip per vblank, WaitFlipDone
   until the buffer left the screen. Today's buffer order and feed lock the regime on (most pairs 3-slot, the real
   frame rate falls by more than 5 %); with mid_lru and the corrected feed the 3-slot share stays near what the game
   needs and the real frame rate within 2 % of the regime off, with far fewer repeated real frames.
4. main.c: no per-title file; the state is set up once on the game thread (never in plugin_load), the regime and the
   accumulator read b30_feed() (own - extra), the buffer order and the extra are used only with below30 on and 2..4
   generated buffers outside a present copy (below30 off: v2.35's g_flips round robin), the B30COST line reports the
   fences and the fed-back time.

Same compiler choice as test_pace_policy.py (config.json llvm_bin, otherwise the host clang/cc).
"""
import re
import unittest

from test_pace_policy import MAIN_C, compile_and_run, extract_function, rows

HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include <math.h>
#include "pace_policy.h"
static void lru_rr(uint32_t n)
{
    MidLru l; mid_lru_reset(&l, n);
    int same = 1;
    for (uint32_t f = 0; f < 60; f++) {
        uint32_t a = mid_lru_pick(&l, 0u);
        same &= a == f % n;
        mid_lru_flipped(&l, a, 0, mid_lru_pick(&l, 1u));
    }
    printf("rr%u %d\n", n, same);
}
/* pattern of slots per pair; for each pair: mid1 differs from the buffer the pair before flipped last, mid1 != mid2 */
static void lru_pattern(const char *name, const int *m, int np)
{
    MidLru l; mid_lru_reset(&l, 2u);
    int last = -1, ok_last = 1, ok_diff = 1, today_hits = 0;
    for (int k = 0; k < np; k++) {
        uint32_t a = mid_lru_pick(&l, 0u), b = mid_lru_pick(&l, 1u);
        uint32_t ta = (uint32_t)k % 2u, tb = ((uint32_t)k + 1u) % 2u;   /* today: g_flips % 2, (g_flips + 1) % 2 */
        if (k && (int)a == last) ok_last = 0;
        if (k && (int)ta == last) today_hits++;
        if (a == b) ok_diff = 0;
        (void)tb;
        mid_lru_flipped(&l, a, m[k] == 3, b);
        last = (int)(m[k] == 3 ? b : a);
    }
    printf("%s %d %d %d\n", name, ok_last, ok_diff, today_hits);
}
/* closed loop: see the module docstring */
#define T_US 16683.4
#define NK 6000
static double nv(double t) { return ceil(t / T_US - 1e-9) * T_US; }
static void loop(const char *name, int regime, int lru, int corr)
{
    double rel[2] = { -1e12, -1e12 }, last_latch = -1e12, E = 0.0, H0 = 0.0, Hp = 0.0, fence_sum = 0.0;
    int last_buf = -1;   /* -1 none, 0..1 ours, 2 a real */
    Below30 s; b30_reset(&s);
    MidLru l; mid_lru_reset(&l, 2u);
    B30Extra x; b30x_reset(&x);
    double xf[4] = { 0 }, xp[4] = { 0 }; int x3[4] = { 0 };
    uint32_t q8 = vb_period_q8_from_window(600u, (uint64_t)(600.0 * T_US + 0.5));
    uint32_t ema = 0, three = 0, rep = 0, n = 0;
    double prev_real_latch = -1e12;
    for (int k = 0; k < NK; k++) {
        static const int j[8] = { 0, 9, -7, 13, -13, 4, -4, 11 };
        double w = 36100.0 + j[k & 7] * 130.0 + 900.0 * sin(k / 37.0);
        double H = E + 2500.0;
        uint32_t own = k ? (uint32_t)(H - Hp) : (uint32_t)w;
        if (!k) H0 = H;
        Hp = H;
        if (k >= 3) b30x_add(&x, (uint32_t)xf[(k - 3) & 3], (uint32_t)xp[(k - 3) & 3], x3[(k - 3) & 3]);
        uint32_t fo = corr ? b30_feed_us(own, x.ema_us) : own;
        ema = ema ? (ema * 7u + own) / 8u : own;
        uint32_t fe = corr ? b30_feed_us(ema, x.ema_us) : ema;
        uint32_t m = 2u;
        if (regime && k) {
            b30_regime_step(&s, fo, q8);
            m = b30_slots(&s, fe, q8, 0u, NULL);
        }
        uint32_t a = lru ? mid_lru_pick(&l, 0u) : (uint32_t)k % 2u, b = lru ? mid_lru_pick(&l, 1u) : ((uint32_t)k + 1u) % 2u;
        double reach = H + w - 2850.0 - 2500.0;
        double t = reach > rel[a] ? reach : rel[a];
        double f1 = t - reach, f2 = 0.0, post = 790.0;
        t += 2850.0;
        if (m == 3u) { double t2 = t > rel[b] ? t : rel[b]; f2 = t2 - t; t = t2 + 2850.0; post += f2 + 2850.0; three++; }
        E = t;
        fence_sum += f1 + f2;
        xf[k & 3] = f1; xp[k & 3] = post; x3[k & 3] = m == 3u;
        /* flips: mid1, [mid2], real; a buffer is free once the flip after it latched */
        int fl[3] = { (int)a, (int)b, 2 }; int nf = m == 3u ? 3 : 2;
        if (m != 3u) fl[1] = 2;
        double first = 0.0;
        for (int i = 0; i < nf; i++) {
            double L = nv(E + 1550.0); if (L < last_latch + T_US) L = last_latch + T_US;
            if (last_buf >= 0 && last_buf < 2) rel[last_buf] = L;
            if (fl[i] < 2) rel[fl[i]] = 1e18;
            last_buf = fl[i]; last_latch = L;
            if (!i) first = L;
        }
        if (k && first - prev_real_latch > 1.5 * T_US) rep++;
        prev_real_latch = last_latch;
        if (lru) mid_lru_flipped(&l, a, m == 3u, b);
        n++;
    }
    printf("%s fps %.2f three %.3f repeat %.3f fence %.2f\n", name, (n - 1) / ((Hp - H0) / 1e6), (double)three / n,
           (double)rep / (n - 1), fence_sum / n / 1000.0);
}
int main(void)
{
    lru_rr(2u); lru_rr(3u); lru_rr(4u);
    { MidLru l; mid_lru_reset(&l, 1u); printf("one %u %u\n", mid_lru_pick(&l, 0u), mid_lru_pick(&l, 1u)); }
    static const int alt[8] = { 3, 2, 3, 2, 3, 2, 3, 2 };
    static const int burst[10] = { 2, 3, 3, 3, 2, 2, 3, 2, 2, 3 };
    lru_pattern("alt", alt, 8);
    lru_pattern("burst", burst, 10);
    printf("feed %u %u %u %u\n", b30_feed_us(0u, 3000u), b30_feed_us(40000u, 3000u), b30_feed_us(40000u, 30000u),
           b30_feed_us(40000u, 0u));
    printf("ticks %u %u %u\n", b30x_ticks_us(99751u, 99751u), b30x_ticks_us(250000u, 99751u), b30x_ticks_us(5u, 0u));
    {
        B30Extra x; b30x_reset(&x);
        b30x_add(&x, 0u, 800u, 0);            /* seeds the 2-slot post block (and the EMA at 0) */
        b30x_add(&x, 2500u, 800u + 3000u, 1);  /* fence 2.5 ms + 3.0 ms of 2nd generated frame */
        printf("x %u %u %u %u %u %u\n", x.post2_us, x.ema_us, x.fence_sum_us, x.g2_sum_us, x.nw, x.n3);
    }
    loop("off", 0, 0, 0);
    loop("today", 1, 0, 0);
    loop("lru", 1, 1, 0);
    loop("corr", 1, 0, 1);
    loop("both", 1, 1, 1);
    return 0;
}
"""


class TestB30CostPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = compile_and_run(HARNESS)
        cls.r = {k: v.split() for k, v in rows(cls.out).items()}

    def test_round_robin_without_3_slot_pairs(self):
        for n in (2, 3, 4):
            self.assertEqual(self.r["rr%d" % n], ["1"], "2-slot pairs only: g_mbase + g_flips %% %d" % n)
        self.assertEqual(self.r["one"], ["0", "0"])

    def test_first_generated_frame_never_in_the_buffer_flipped_last(self):
        for name in ("alt", "burst"):
            ok_last, ok_diff, today_hits = (int(v) for v in self.r[name])
            self.assertEqual(ok_last, 1, name)
            self.assertEqual(ok_diff, 1, name)
            self.assertGreater(today_hits, 0, "%s: today's g_flips %% 2 writes into the buffer flipped last" % name)

    def test_feed_and_extra(self):
        self.assertEqual(self.r["feed"], ["0", "37000", "20000", "40000"])
        self.assertEqual(self.r["ticks"], ["1000", "2506", "0"])
        self.assertEqual(self.r["x"], ["800", "687", "2500", "3000", "2", "1"])

    def _loop(self, name):
        v = self.r[name]
        return {v[i]: float(v[i + 1]) for i in range(0, len(v), 2)}

    def test_closed_loop(self):
        off, today, both = self._loop("off"), self._loop("today"), self._loop("both")
        self.assertTrue(0.08 <= off["repeat"] <= 0.30, off)
        self.assertLess(today["fps"], off["fps"] * 0.95, "today's order and feed lock the regime on: %s" % today)
        self.assertGreater(today["three"], 0.30, today)
        self.assertGreater(today["fence"], 1.0, today)
        self.assertGreaterEqual(both["fps"], off["fps"] * 0.98, both)
        self.assertLess(both["three"], 0.30, both)
        self.assertLess(both["repeat"], off["repeat"] * 0.5, both)
        self.assertLess(both["fence"], 0.5, both)


class TestB30CostMainC(unittest.TestCase):
    FLIP = extract_function(MAIN_C, "static int32_t flip_inner_ex(")

    def test_no_per_title_file_and_state_on_the_game_thread(self):
        for gone in ("b30lru_", "b30corr_", "g_b30lru", "g_b30corr", "b30cost_opts"):
            self.assertNotIn(gone, MAIN_C)
        init = extract_function(MAIN_C, "static void b30cost_init(")
        self.assertIn("mid_lru_reset(&g_mid_lru, (uint32_t)g_nmid);", init)
        self.assertIn("b30x_reset(&g_b30x);", init)
        self.assertIn("b30cost_init();", self.FLIP)
        self.assertNotIn("b30cost_init", extract_function(MAIN_C, "s32 attr_public plugin_load("))

    def test_feed_is_own_minus_extra(self):
        feed = extract_function(MAIN_C, "static uint32_t b30_feed(")
        self.assertIn("return b30_feed_us(o, g_b30x.ema_us);", feed)
        self.assertIn("b30_regime_step(&g_b30, (b30_ok && !force_repeat) ? b30_feed(ft_own_us) : 0u, pq8);", self.FLIP)
        self.assertIn("uint32_t m_out = b30_slots(&g_b30, b30_feed(g_cpu_ema_us), pq8,", self.FLIP)

    def test_buffer_order_with_below30_or_fast(self):
        f = self.FLIP
        self.assertIn("int mid_lru_on = (g_cfg.below30 || g_cfg.preset == 1) && !(pcopy && g_pcopy_k >= 2) && g_nmid >= 2 && (uint32_t)g_nmid <= MID_LRU_MAX;", f)
        # Quality / Sharp with below30 off, or one generated-frame buffer: v2.35's order
        self.assertIn("(uint32_t)g_mbase + (g_flips % (uint32_t)g_nmid);", f[:f.index("int mid_lru_on =")])
        i = f.index("int mid_lru_on =")
        self.assertIn("mi1 = (uint32_t)g_mbase + mid_lru_pick(&g_mid_lru, 0u);", f[i:i + 900])
        self.assertIn("mi2 = (uint32_t)g_mbase + mid_lru_pick(&g_mid_lru, 1u);", f[i:i + 900])
        # the order is updated once the pair's slots are final, before the DCB is built
        upd = f.index("mid_lru_flipped(&g_mid_lru,")
        self.assertLess(f.index("if (b30_two && !two_mids) {"), upd)
        self.assertLess(upd, f.index("n = build_dcb_flow8(mine,"))
        self.assertLess(f.index("g_slot_three[slot] = (uint8_t)(third_flip ? 1 : 0);"), f.index("n = build_dcb_flow8(mine,"))

    def test_extra_from_the_read_back_slot(self):
        f = self.FLIP
        self.assertIn("if (g_cfg.below30 && g_ts_ticks_per_ms)\n", f)
        self.assertIn("b30x_add(&g_b30x, b30x_ticks_us(ts[3] > ts[2] ? ts[3] - ts[2] : 0u, g_ts_ticks_per_ms),", f)
        self.assertIn("b30x_ticks_us(t2[7] - t2[5], g_ts_ticks_per_ms), g_slot_three[ps]);", f)
        self.assertIn("if (unit > 1e6 && unit < 1e10) g_ts_ticks_per_ms = (uint32_t)(unit / 1000.0 + 0.5);", f)
        self.assertIn('lg("B30COST: fence before the 1st generated frame avg %.2f ms (%u), after a 3-slot pair %.2f ms (%u) | "', f)


ONE_BUFFER = r"""
#include <stdio.h>
#include "pace_policy.h"
static struct { int below30, preset, fps_lock; } g_cfg;
static int g_nmid, g_mem_tight, g_s14_state, g_fg_off, g_pcopy_k;
static int pace_timer_on(void) { return 1; }
int main(void)
{
    for (int preset = 0; preset <= 2; preset++)
    for (int b30 = 0; b30 <= 1; b30++)
        for (g_nmid = 1; g_nmid <= 2; g_nmid++)
            for (int pcopy = 0; pcopy <= 1; pcopy++) {
                g_cfg.below30 = b30; g_cfg.preset = preset; g_cfg.fps_lock = 30; g_pcopy_k = pcopy ? 2 : 0;
                __MID_LRU_ON__
                __B30_OK__
                printf("%sb%d_n%d_p%d %d %d\n", preset == 0 ? "" : preset == 1 ? "fast_" : "sharp_", b30, g_nmid, pcopy, mid_lru_on, b30_ok);
            }
    return 0;
}
"""


class TestB30OneBuffer(unittest.TestCase):
    def test_one_generated_buffer_is_v235(self):
        # below30 is off by default; a player who turns it on for a title with no spare memory (one generated-frame
        # buffer: no 2nd DirectMemory buffer and no shave14) keeps v2.35's path: no regime, v2.35's buffer order.
        # The real expressions of flip_inner_ex, compiled with the real b30_available().
        f = extract_function(MAIN_C, "static int32_t flip_inner_ex(")
        lru = re.search(r"int mid_lru_on = [^;]+;", f)[0]
        ok = re.search(r"int b30_ok = b30_available\([^;]+;", f)[0]
        r = {k: v.split() for k, v in rows(compile_and_run(
            ONE_BUFFER.replace("__MID_LRU_ON__", lru).replace("__B30_OK__", ok))).items()}
        for b30 in (0, 1):
            for pcopy in (0, 1):
                self.assertEqual(r["b%d_n1_p%d" % (b30, pcopy)], ["0", "0"], "one buffer, below30=%d" % b30)
                self.assertEqual(r["b0_n2_p%d" % pcopy], ["0", "0"], "below30 off")
        # the same title with a 2nd generated-frame buffer and below30 on does get the regime and the order
        self.assertEqual(r["b1_n2_p0"], ["1", "1"])
        self.assertEqual(r["b1_n2_p1"], ["0", "1"])   # a running present copy keeps its own buffer order
        # FG Fast (3-slot pairs without below30) takes the order with 2 buffers, never the below30 regime
        for b30 in (0, 1):
            self.assertEqual(r["fast_b%d_n2_p0" % b30], ["1", "0"])
            self.assertEqual(r["fast_b%d_n2_p1" % b30], ["0", "0"])
            self.assertEqual(r["fast_b%d_n1_p0" % b30], ["0", "0"])
        # Sharp is Quality's pacing: v2.35's order with below30 off
        self.assertEqual(r["sharp_b0_n2_p0"], ["0", "0"])
        self.assertEqual(r["sharp_b1_n2_p0"], ["1", "1"])

if __name__ == "__main__":
    unittest.main()
