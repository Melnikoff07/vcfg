#!/usr/bin/env python3
"""Below-30 regime (3-slot pairs with two generated frames) and the player settings below30 / shallow_queue:
classic/source/pace_policy.h compiled and executed on the host, plus the call sites in classic/source/main.c.

Same compiler choice as test_pace_policy.py (config.json llvm_bin, otherwise the host clang/cc).
"""
import pathlib
import re
import unittest

from test_pace_policy import MAIN_C, ROOT, compile_and_run, extract_function, rows

DEBUG_HOOKS = (ROOT / "classic" / "source" / "debug_hooks.h").read_text(encoding="utf-8", errors="replace")
REFRESH_US = 1e6 / 59.939

POLICY_HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "pace_policy.h"
static uint32_t q8 = %(q8)uu;
/* own times of one real frame each: jitter of +-1.3 ms around the mean (deterministic) */
static uint32_t own_at(double mean_ms, int i) { static const int j[8] = { 0, 9, -7, 13, -13, 4, -4, 11 }; return (uint32_t)(mean_ms * 1000.0 + j[i & 7] * 100); }
static void regime(const char *name, double mean_ms, int n)
{
    Below30 s; b30_reset(&s);
    printf("%%s ", name);
    for (int i = 0; i < n; i++) putchar('0' + b30_regime_step(&s, own_at(mean_ms, i), q8));
    putchar('\n');
}
static void slots(const char *name, double mean_ms, int n, uint32_t qahead_q8)
{
    Below30 s; b30_reset(&s);
    uint32_t sum = 0, k = 0, three = 0, cuts = 0;
    printf("%%s ", name);
    for (int i = 0; i < n; i++) {
        int on = b30_regime_step(&s, own_at(mean_ms, i), q8);
        int cut = 0;
        uint32_t m = b30_slots(&s, (uint32_t)(mean_ms * 1000.0), q8, qahead_q8, &cut);
        if (i < 64) putchar('0' + (int)m);
        if (on) { sum += m; k++; three += m == 3u; cuts += (uint32_t)cut; }
    }
    printf(" mean %%.3f three %%u cuts %%u\n", k ? (double)sum / k : 0.0, three, cuts);
}
int main(void)
{
    regime("steady30", 1000.0 / 30.0, 600);
    regime("steady28", 1000.0 / 28.0, 64);
    regime("steady25", 40.0, 64);
    regime("steady20", 50.0, 64);
    regime("steady45", 1000.0 / 45.0, 64);
    { /* hysteresis: 25 fps, then 34.5 ms (2.07 refreshes, inside the band), then 33.0 ms (1.98, below the exit) */
        Below30 s; b30_reset(&s);
        printf("band ");
        for (int i = 0; i < 40; i++) putchar('0' + b30_regime_step(&s, 40000u, q8));
        for (int i = 0; i < 40; i++) putchar('0' + b30_regime_step(&s, 34500u, q8));
        for (int i = 0; i < 40; i++) putchar('0' + b30_regime_step(&s, 33000u, q8));
        for (int i = 0; i < 40; i++) putchar('0' + b30_regime_step(&s, 34500u, q8));
        putchar('\n');
    }
    { /* not eligible (own 0: setting off, buffer missing, FG off): resets at once */
        Below30 s; b30_reset(&s);
        for (int i = 0; i < 20; i++) b30_regime_step(&s, 40000u, q8);
        int before = (int)s.on;
        int after = b30_regime_step(&s, 0u, q8);
        int again = 0;
        for (int i = 0; i < 7; i++) again |= b30_regime_step(&s, 40000u, q8);
        printf("reset %%d %%d %%d\n", before, after, again);
    }
    { /* setting off (or no 2nd buffer): main.c feeds own 0 -> the q/pacing pair (2 slots, old phase, 2-refresh pacer) */
        Below30 s; b30_reset(&s);
        uint32_t on = 0, two = 0;
        for (int i = 0; i < 600; i++) {
            int ok = b30_available(0, 2, 0, 0, 0, 1, 30);
            on += (uint32_t)b30_regime_step(&s, ok ? own_at(50.0, i) : 0u, q8);
            two += b30_slots(&s, 50000u, q8, 0u, NULL) == 2u && b30_pace_nvb(2u) == 2u;
        }
        printf("off20 %%u %%u\n", on, two);
    }
    slots("slots25", 40.0, 600, 0u);
    slots("slots28", 1000.0 / 28.0, 600, 0u);
    slots("slots20", 50.0, 600, 0u);
    slots("slots30", 1000.0 / 30.0, 600, 0u);
    slots("slots20cut", 50.0, 600, B30_QCUT_Q8 + 1u);
    slots("slots20edge", 50.0, 600, B30_QCUT_Q8);
    printf("qahead %%u %%u %%u %%u\n", b30_queue_ahead_q8(0u, 9000u, q8), b30_queue_ahead_q8(1u, 8342u, q8),
           b30_queue_ahead_q8(2u, 8342u, q8), b30_queue_ahead_q8(3u, 16000u, q8));
    /* phases: the 2-slot pair keeps the old formula bit for bit */
    int same = 1;
    for (uint32_t p = 1; p <= 6; p++) {
        uint32_t c = p + 1u, old = ((p << 16) + (c >> 1)) / c;
        if (old < 21845u) old = 21845u;
        if (old > 43691u) old = 43691u;
        uint32_t t1 = 0, t2 = 0;
        pll_slot_phases_q16(p, 1u, &t1, &t2);
        same &= (t1 == old);
    }
    printf("phase2_same %%d\n", same);
    for (uint32_t p = 1; p <= 4; p++) {
        uint32_t t1 = 0, t2 = 0;
        pll_slot_phases_q16(p, 2u, &t1, &t2);
        printf("phase3_%%u %%u %%u\n", p, t1, t2);
    }
    printf("avail %%d %%d %%d %%d %%d %%d %%d %%d %%d\n",
           b30_available(1, 2, 0, 0, 0, 1, 30), b30_available(0, 2, 0, 0, 0, 1, 30), b30_available(1, 1, 0, 0, 0, 1, 30),
           b30_available(1, 2, 1, 0, 0, 1, 30), b30_available(1, 2, 0, 1, 0, 1, 30), b30_available(1, 2, 0, 0, 1, 1, 30),
           b30_available(1, 2, 0, 0, 0, 0, 30), b30_available(1, 2, 0, 0, 0, 1, 20), b30_available(1, 3, 0, 2, 0, 1, 0));
    printf("nvb %%u %%u follows %%d\n", b30_pace_nvb(2u), b30_pace_nvb(3u), B30_PACE_FOLLOWS_SLOTS);
    uint32_t w = ft_with_ngen(0u, 2u);
    w = ft_with_screen(w, 50040u);
    w = ft_with_mid_vb(ft_with_real_vb(w, 1u), 2u);
    printf("ft %%u %%u %%u %%u\n", w & 0x3FFFFFu, (w >> 22) & 3u, (w >> 24) & 15u, w >> 28);
    printf("ftsat %%u\n", ft_with_screen(ft_with_ngen(0u, 1u), 9000000u) & 0x3FFFFFu);
    uint32_t rec[2][3] = { { 1000u, ft_pack_sub(10u, 40000u), ft_with_ngen(ft_with_screen(0u, 70000u), 2u) },
                           { 41000u, ft_pack_sub(12u, 39000u), ft_with_ngen(ft_with_screen(0u, 52000u), 1u) } };
    char buf[256];
    ft_format(buf, sizeof(buf), (const uint32_t (*)[3])rec, 2, 0u);
    printf("ftline %%s", buf);
    return 0;
}
"""


class TestBelow30Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        q8 = int(round(REFRESH_US * 256))
        cls.r = rows(compile_and_run(POLICY_HARNESS % {"q8": q8}))

    def test_regime_needs_more_than_two_refreshes(self) -> None:
        self.assertNotIn("1", self.r["steady30"], "a steady 30 fps game (2.0 refreshes with jitter) never enters")
        self.assertNotIn("1", self.r["steady45"])
        self.assertEqual(self.r["steady25"].index("1"), 7, "25 fps: on once the ring of 8 own times is full")
        self.assertEqual(self.r["steady20"].index("1"), 7)
        self.assertNotIn("0", self.r["steady25"][7:])

    def test_hysteresis(self) -> None:
        b = self.r["band"]
        self.assertEqual(b[7:80], "1" * 73, "2.07 refreshes is inside the band: stays on")
        self.assertIn("0", b[80:120], "1.98 refreshes: off")
        self.assertEqual(b[88:160], "0" * 72, "and 2.07 does not bring it back (enter needs > 2.15)")

    def test_ineligible_frame_resets(self) -> None:
        self.assertEqual(self.r["reset"].split(), ["1", "0", "0"], "off at once, and the ring refills before re-entry")

    def test_slots_follow_the_own_time(self) -> None:
        m25 = float(self.r["slots25"].split("mean")[1].split()[0])
        self.assertAlmostEqual(m25, 40000 / REFRESH_US, delta=0.02, msg="2.40 refreshes per real at 25 fps")
        m20 = self.r["slots20"]
        self.assertAlmostEqual(float(m20.split("mean")[1].split()[0]), 50000 / REFRESH_US, delta=0.01, msg="2.997 refreshes: almost every pair has 3 slots at 20 fps")
        self.assertEqual(set(self.r["slots30"].split()[0]), {"2"}, "outside the regime every pair has 2 slots")
        m28 = float(self.r["slots28"].split("mean")[1].split()[0])
        self.assertTrue(m28 == 0.0 or abs(m28 - (1e6 / 28) / REFRESH_US) < 0.03, m28)

    def test_queue_cut_drops_the_second_generated_frame(self) -> None:
        cut = self.r["slots20cut"]
        self.assertEqual(float(cut.split("mean")[1].split()[0]), 2.0)
        self.assertGreater(int(cut.split("cuts")[1]), 500)
        self.assertEqual(int(self.r["slots20edge"].split("cuts")[1]), 0, "exactly 2.5 refreshes ahead does not cut")

    def test_queue_ahead(self) -> None:
        a = [int(x) for x in self.r["qahead"].split()]
        self.assertEqual(a[0], 0)
        self.assertAlmostEqual(a[1] / 256, 0.5, delta=0.01)
        self.assertAlmostEqual(a[2] / 256, 1.5, delta=0.01)
        self.assertAlmostEqual(a[3] / 256, 2 + 16000 / REFRESH_US, delta=0.01)

    def test_setting_off_is_the_q_pacing_pair(self) -> None:
        self.assertEqual(self.r["off20"].split(), ["0", "600"], "20 fps with below30 = 0: never on, 600 of 600 pairs of 2 slots")

    def test_two_slot_phase_is_unchanged(self) -> None:
        self.assertEqual(self.r["phase2_same"], "1")

    def test_three_slot_phases(self) -> None:
        t1, t2 = (int(x) for x in self.r["phase3_1"].split())
        self.assertEqual((t1, t2), (21845, 43691), "1/3 and 2/3 when the previous real stayed 1 refresh")
        for p in range(1, 5):
            t1, t2 = (int(x) for x in self.r["phase3_%d" % p].split())
            self.assertTrue(21845 <= t1 <= 43691 and t1 <= t2 <= 49152, (p, t1, t2))   # phase8 override: 0.25 .. 0.75

    def test_availability(self) -> None:
        self.assertEqual(self.r["avail"].split(), ["1", "0", "0", "0", "0", "0", "0", "0", "1"],
                         "on / setting off / one buffer / memory tight / FAST / FG off / no timer pacer / fps_lock 20 / Sharp")

    def test_pacer_period(self) -> None:
        self.assertEqual(self.r["nvb"].split(), ["2", "2", "follows", "0"])

    def test_ftrace_generated_count(self) -> None:
        self.assertEqual(self.r["ft"].split(), ["50040", "2", "1", "2"])
        self.assertEqual(self.r["ftsat"], str(0x3FFFFF), "on-screen time saturates at 4.19 s without touching the count")
        items = [x.strip() for x in self.r["ftline"].split("|")[1].split(";") if x.strip()]
        self.assertEqual([x.split(",")[-1] for x in items], ["2", "1"], "6th field: generated frames of that real")
        self.assertEqual(items[0].split(",")[:5], ["0", "10", "70000", "0/0", "40000"])


class TestBelow30MainC(unittest.TestCase):
    FLIP = extract_function(MAIN_C, "static int32_t flip_inner_ex(")

    def test_quality_branch_uses_the_existing_second_buffer_only(self) -> None:
        fn = self.FLIP
        q = fn[fn.index("// 1.1 FG Quality (strict 2x)"):fn.index("on_flip_2buf_track(index);")]
        # memory tight, except the two buffers of the shave14 reserve (opt-in, test_shave14.py)
        self.assertIn("b30_available(g_cfg.below30, g_nmid, g_mem_tight && g_s14_state != 2, g_cfg.preset, g_fg_off, pace_timer_on(), g_cfg.fps_lock)", q)
        self.assertIn("b30_regime_step(&g_b30, (b30_ok && !force_repeat)", q)
        self.assertIn("uint32_t m_out = b30_slots(&g_b30, g_cpu_ema_us, pq8,", q, "accumulator on the game's own time")
        self.assertIn("if (m_out == 3u && g_nmid >= 2 && mi1 != mi2) {", q)
        self.assertIn("t2_q16 = __atomic_load_n(&g_pll_t2_q16, __ATOMIC_ACQUIRE);", q)
        self.assertNotIn("g_ema_frame_us", q, "never the hand-over interval (wall clock)")

    def test_allocation_never_depends_on_the_setting(self) -> None:
        reg = MAIN_C[MAIN_C.index("int32_t sceVideoOutRegisterBuffers_hook("):MAIN_C.index("// ---- in-game L3+R3 (3s) menu")]
        alloc = extract_function(MAIN_C, "static void *alloc_mbufs(")
        for src in (reg, alloc, DEBUG_HOOKS):
            self.assertIsNone(re.search(r"below30|b30|g_pair_slots", src))

    def test_present_copy_drops_the_second_frame_only_when_it_runs(self) -> None:
        fn = self.FLIP
        self.assertIn("int b30_two = two_mids && g_cfg.preset != 1 && !tb_fast;", fn)
        restore = fn.index("if (b30_two && !two_mids) {")
        self.assertIn("if (!pcopy) { two_mids = 1; third_flip = 1; mid2_flip_idx = b30_mid2; }", fn[restore:restore + 400])
        self.assertLess(fn.index("if (!tb_fast) { two_mids = 0; third_flip = 0; }"), restore)
        self.assertLess(restore, fn.index("n = build_dcb_flow8(mine, g_buf[g_prev_idx]"))

    def test_late_real_repeats_the_real(self) -> None:
        fn = self.FLIP
        hold = fn.index("if (third_flip) hold_mid_us = 0;")
        mid2 = fn.index("handle, mid2_flip_idx, flipMode, flipArg);")
        real = fn.index("handle, show_idx, flipMode, real_arg);")
        self.assertLess(hold, mid2)
        self.assertLess(mid2, real, "mid1 (with the DCB), mid2, then the real: nothing holds a generated frame")
        self.assertLess(fn.index("int32_t rc = gnm_submit_and_flip_raw(has_wl, workload, ncount, d2, s2"), mid2)

    def test_pacer_and_pll_follow_the_pair(self) -> None:
        fn = self.FLIP
        self.assertIn("pace_pair_ex((int32_t)handle, dt_enter_us, 1, b30_pace_nvb(g_pair_slots));", fn)
        self.assertIn("pace_pair_ex((int32_t)handle, dt_enter_us, 0, b30_pace_nvb(g_pair_slots));", fn)
        pace = extract_function(MAIN_C, "static void pace_pair_ex(")
        self.assertIn("vb_ticks(g_freq, refresh_period_q8(), nvb)", pace)
        for name in ("static uint32_t pll_predict_t_interp_q16(", "static void pll_commit_frame_vblank("):
            self.assertIn("(__atomic_load_n(&g_pair_slots, __ATOMIC_ACQUIRE) == 3u ? 2u : 1u) + extra_mid_vb", extract_function(MAIN_C, name))
        self.assertIn("pll_slot_phases_q16(vb_p_to_m, vb_m_to_c, &t_q16, &t2_q16);", extract_function(MAIN_C, "static uint32_t pll_predict_t_interp_q16("))
        after = fn[fn.index("uint32_t t_pll_q16 = pll_predict_t_interp_q16((int32_t)handle, t_s1"):]
        self.assertIn("tb2_slot[29] = (tb2_slot[29] & 0xFFFFu) | ((t2n & 0xFFFFu) << 16);", after[:900])

    def test_shallow_queue_is_a_setting_and_never_in_the_regime(self) -> None:
        fn = self.FLIP
        ok = fn.index("int shq_ok = g_inject_on == 8 && second_flip && !third_flip")
        gate = fn.index("if (!g_cfg.shallow_queue || g_b30.on) shq_ok = 0;")
        step = fn.index("int shq_on = shq_step(&s_shq, shq_ok ? g_cpu_ema_us : 0u);")
        self.assertLess(ok, gate)
        self.assertLess(gate, step, "shq_step(0) resets the dwell while off")

    def test_logs(self) -> None:
        fn = self.FLIP
        self.assertIn('lg("BELOW30 %s: regime %u of %u frames (%.1f s), 3-slot pairs %u, 2nd generated dropped by the queue %u, "', fn)
        self.assertIn("ft_with_ngen(0u, (second_flip && can_fg8 && want_fg", fn)

    def test_settings_read_written_and_logged(self) -> None:
        self.assertIn('else if (strcmp(key, "below30") == 0) c->below30 = (val != 0);', MAIN_C)
        self.assertIn('else if (strcmp(key, "shallow_queue") == 0) c->shallow_queue = (val != 0);', MAIN_C)
        self.assertIn('fprintf(f, "below30 = %d\\n", c.below30);', MAIN_C)
        self.assertIn('fprintf(f, "shallow_queue = %d\\n", c.shallow_queue);', MAIN_C)
        self.assertIn("below30=%d shallow_queue=%d\\n", MAIN_C)
        d = extract_function(MAIN_C, "static void cfg_defaults(")
        self.assertIn("c->below30        = 0;", d)
        self.assertIn("c->shallow_queue  = 1;", d)
        self.assertEqual(MAIN_C.count("0, 0, 0, 0, 0, 0, 0, 0, -1, 0, 0, 1 };"), 2, "g_cfg and g_menu_edit initialisers")
        st = MAIN_C[MAIN_C.index("typedef struct {\n    int framegen;"):MAIN_C.index("} fg_config_t;")]
        self.assertTrue(st.rstrip().endswith("int shallow_queue;   // 0 = off (the game is handed back with 3-4 flips pending, as before), 1 = on (default)"))


if __name__ == "__main__":
    unittest.main()
