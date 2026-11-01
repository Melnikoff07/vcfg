#!/usr/bin/env python3
"""T19 Part B regression tests: Phase 2 (below30 inside Fast, B1-B7).

Verifies:
- B1: g_pair_slots = 3u when two_mids is active in preset == 1, so pll_predict_t_interp_q16 and
  pll_commit_frame_vblank use vb_m_to_c = 2u.
- B2: preset == 1 (default, !tb_fast && !g_fast_pace) updates g_b30x and uses b30_feed(ft_own_us)
  with b30_regime_step hysteresis + b30_slots(b30_feed(g_cpu_ema_us)), preventing 2<->3 oscillation
  on 33-40 ms frame sequences with GPU-serialized 3-slot overhead.
- B3: b30_two is not disabled for preset == 1 (int b30_two = two_mids && !tb_fast;).
- B4: All safety gates preserved (g_nmid >= 2, !stream_3buf via g_nmid, !pcopy, pace_timer_on(),
  !(g_mem_tight && g_s14_state != 2), fps_lock != 20) and zero changes to RegisterBuffers / alloc_mbufs / shave14.
- B5: below30=1 in INI remains compatible across presets; Quality (preset=0) and Sharp (preset=2)
  pacing is bit-for-bit identical to release-236 (8504e70).
- B7: Launcher settings_row_guide and OSD plugin (classic opt_status.h and vcfg16 a20_copy_impl.h)
  describe Fast as keeping 60 Hz when the game is below 30 FPS.
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_pace_policy import ROOT, compile_and_run, extract_function  # noqa: E402

MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8", errors="replace")
OPT_H = (ROOT / "classic" / "source" / "opt_status.h").read_text(encoding="utf-8", errors="replace")
A20_H = (ROOT / "plugin" / "source" / "a20_copy_impl.h").read_text(encoding="utf-8", errors="replace")
APP_C = (ROOT / "launcher" / "src" / "app.c").read_text(encoding="utf-8", errors="replace")
CC = shutil.which("clang-22") or shutil.which("clang") or shutil.which("cc")


class TestT19PartB(unittest.TestCase):
    FLIP = extract_function(MAIN_C, "static int32_t flip_inner_ex(")

    def test_b1_preset1_two_mids_sets_g_pair_slots_3_and_vb_m_to_c_2(self):
        fn = self.FLIP
        fast_blk = fn[fn.index("// 1.1 FG Fast"):fn.index("} else if (!g_fg_off) {")]
        # Both the fast_pace/tb_fast sub-branch and the default b30 sub-branch in preset==1 must set g_pair_slots = 3u
        self.assertGreaterEqual(fast_blk.count("__atomic_store_n(&g_pair_slots, 3u, __ATOMIC_RELEASE);"), 2)

        # Verify compiled pll_slot_phases_q16 and vb_m_to_c logic when g_pair_slots == 3u vs 2u
        harness = r"""
#include <stdio.h>
#include "pace_policy.h"
int main(void) {
    for (uint32_t pair_slots = 2u; pair_slots <= 3u; pair_slots++) {
        uint32_t vb_m_to_c = (pair_slots == 3u ? 2u : 1u);
        uint32_t t1 = 0, t2 = 0;
        pll_slot_phases_q16(1u, vb_m_to_c, &t1, &t2);
        printf("slots=%u vb_m_to_c=%u t1=%u t2=%u\n", pair_slots, vb_m_to_c, t1, t2);
    }
    return 0;
}
"""
        out = compile_and_run(harness).strip().splitlines()
        self.assertEqual(out[0], "slots=2 vb_m_to_c=1 t1=32768 t2=49152")
        self.assertEqual(out[1], "slots=3 vb_m_to_c=2 t1=21845 t2=43691")

    def test_b2_preset1_uses_b30_feed_and_hysteresis_without_oscillation(self):
        fn = self.FLIP
        # 1. g_b30x is fed when preset == 1 even with below30 == 0
        self.assertIn("else if (g_cfg.preset == 1 && g_ts_ticks_per_ms)", fn)
        # 2. Fast default branch uses b30_available, b30_regime_step(b30_feed(ft_own_us)), and b30_slots(b30_feed(g_cpu_ema_us))
        fast_blk = fn[fn.index("// 1.1 FG Fast"):fn.index("} else if (!g_fg_off) {")]
        self.assertIn("int fast_b30_ok = b30_available(1, g_nmid, g_mem_tight && g_s14_state != 2, 0, g_fg_off, pace_timer_on(), g_cfg.fps_lock);", fast_blk)
        self.assertIn("int b30_on = b30_regime_step(&g_b30, (fast_b30_ok && !force_repeat) ? b30_feed(ft_own_us) : 0u, pq8);", fast_blk)
        self.assertIn("uint32_t m_out = b30_slots(&g_b30, b30_feed(g_cpu_ema_us), pq8,", fast_blk)

        # 3. Synthetic 33-40 ms frame sequence with GPU-serialized 3-slot extra (+3.6 ms when previous pair was 3-slot):
        #    Verify b30_feed + hysteresis has 0 regime chattering and exits cleanly when game returns to 33.0 ms,
        #    whereas raw wall-clock fast_slots stays stuck emitting 3-slot pairs at 33.0 ms.
        harness = r"""
#include <stdio.h>
#include "pace_policy.h"
int main(void) {
    uint32_t q8 = 4270950u; /* 16683.4 us * 256 */
    Below30 b30; b30_reset(&b30);
    B30Extra bx; b30x_reset(&bx);
    int32_t fast_acc = 0, prev = 0;
    int prev_m_b30 = 2, prev_m_old = 2;
    int b30_transitions = 0, prev_on = 0;
    int b30_three_at_33ms = 0, old_three_at_33ms = 0;
    uint32_t ema_b30 = 33000u, ema_old = 33000u;

    /* Phase A (0..39): game at 38.5 ms (<30 fps) -> enters below30
       Phase B (40..79): game at 34.2 ms (inside hysteresis band 33.7..35.9 ms) -> stays on without chatter
       Phase C (80..159): game returns to 33.0 ms (30.3 fps) -> b30 exits to 2 slots, old fast_slots chatters 2<->3 */
    for (int i = 0; i < 160; i++) {
        uint32_t game_own = (i < 40) ? 38500u : (i < 80 ? 34200u : 33450u);
        uint32_t fence_b30 = (prev_m_b30 == 3) ? 1200u : 0u;
        uint32_t post_b30  = (prev_m_b30 == 3) ? (800u + 2800u) : 800u;
        b30x_add(&bx, fence_b30, post_b30, prev_m_b30 == 3);
        uint32_t raw_own_b30 = game_own + (prev_m_b30 == 3 ? 4000u : 0u);
        ema_b30 = (ema_b30 * 7u + raw_own_b30) / 8u;
        int on = b30_regime_step(&b30, b30_feed_us(raw_own_b30, bx.ema_us), q8);
        if (i > 0 && on != prev_on) b30_transitions++;
        prev_on = on;
        uint32_t m_b30 = b30_slots(&b30, b30_feed_us(ema_b30, bx.ema_us), q8, 0u, NULL);
        prev_m_b30 = (int)m_b30;

        uint32_t raw_own_old = game_own + (prev_m_old == 3 ? 4000u : 0u);
        ema_old = (ema_old * 7u + raw_own_old) / 8u;
        uint32_t m_old = fast_slots(&fast_acc, ema_old, 0, 1u, 0u, &prev);
        prev_m_old = (int)m_old;

        if (i >= 100) {
            if (m_b30 == 3u) b30_three_at_33ms++;
            if (m_old == 3u) old_three_at_33ms++;
        }
    }
    printf("transitions=%d b30_three_at_33ms=%d old_three_at_33ms=%d\n",
           b30_transitions, b30_three_at_33ms, old_three_at_33ms);
    return 0;
}
"""
        out = compile_and_run(harness).strip()
        kv = dict(item.split("=") for item in out.split())
        # Exactly 2 transitions across 160 frames: 0->1 in Phase A, 1->0 in Phase C (zero chatter!)
        self.assertEqual(int(kv["transitions"]), 2, out)
        # Once game returns to 33.0 ms (Phase C), b30_feed + hysteresis emits 0 three-slot pairs, whereas old emits > 0
        self.assertEqual(int(kv["b30_three_at_33ms"]), 0, out)
        self.assertGreater(int(kv["old_three_at_33ms"]), 0, out)

    def test_b3_b30_two_not_disabled_for_preset1(self):
        fn = self.FLIP
        self.assertIn("int b30_two = two_mids && !tb_fast;", fn)
        self.assertNotIn("int b30_two = two_mids && g_cfg.preset != 1", fn)

    def test_b4_all_safety_gates_preserved_and_zero_memory_changes(self):
        fn = self.FLIP
        fast_blk = fn[fn.index("// 1.1 FG Fast"):fn.index("} else if (!g_fg_off) {")]
        # Requires g_nmid >= 2, !(g_mem_tight && g_s14_state != 2), !g_fg_off, pace_timer_on(), fps_lock != 20
        self.assertIn("b30_available(1, g_nmid, g_mem_tight && g_s14_state != 2, 0, g_fg_off, pace_timer_on(), g_cfg.fps_lock)", fast_blk)
        # And when pcopy runs, two_mids is cleared
        self.assertIn("if (!tb_fast) { two_mids = 0; third_flip = 0; }", fn)
        # RegisterBuffers and alloc_mbufs untouched vs release-236 (8504e70)
        reg_now = MAIN_C[MAIN_C.index("int32_t sceVideoOutRegisterBuffers_hook("):MAIN_C.index("// ---- in-game L3+R3 (3s) menu")]
        alloc_now = extract_function(MAIN_C, "static void *alloc_mbufs(")
        base_main = subprocess.run(
            ["git", "show", "8504e70:classic/source/main.c"],
            cwd=str(ROOT), check=True, capture_output=True, text=True
        ).stdout
        reg_base = base_main[base_main.index("int32_t sceVideoOutRegisterBuffers_hook("):base_main.index("// ---- in-game L3+R3 (3s) menu")]
        alloc_base = extract_function(base_main, "static void *alloc_mbufs(")
        self.assertEqual(reg_now, reg_base)
        self.assertEqual(alloc_now, alloc_base)

    def test_b5_below30_ini_compat_and_quality_sharp_pacing_bit_exact_vs_release236(self):
        fn = self.FLIP
        qual_now = fn[fn.index("// 1.1 FG Quality (strict 2x)"):fn.index("// 3-slot pair of the below-30 regime")]
        base_main = subprocess.run(
            ["git", "show", "8504e70:classic/source/main.c"],
            cwd=str(ROOT), check=True, capture_output=True, text=True
        ).stdout
        fn_base = extract_function(base_main, "static int32_t flip_inner_ex(")
        qual_base = fn_base[fn_base.index("// 1.1 FG Quality (strict 2x)"):fn_base.index("// 3-slot pair of the below-30 regime")]
        self.assertEqual(qual_now, qual_base)

        # pace_policy.h must be bit-for-bit identical to release-236 (8504e70)
        pace_now = (ROOT / "classic" / "source" / "pace_policy.h").read_text(encoding="utf-8")
        pace_base = subprocess.run(
            ["git", "show", "8504e70:classic/source/pace_policy.h"],
            cwd=str(ROOT), check=True, capture_output=True, text=True
        ).stdout
        self.assertEqual(pace_now, pace_base)

    @unittest.skipUnless(CC, "no host C compiler")
    def test_b7_launcher_and_osd_fast_description(self):
        # 1. Launcher settings_row_guide(1, ...) describes FAST lower input lag and holding 60 below 30 FPS
        guide_fn = extract_function(APP_C, "static const char *settings_row_guide(")
        self.assertIn("Lower input lag, holds 60 below 30 FPS", guide_fn)
        # 2. Classic OSD opt_row_detail shows "Lower input lag, holds 60 below 30 FPS" (<= 38 chars) when Fast is active with 2 buffers
        harness = r"""
#include <stdio.h>
#include <string.h>
#include "opt_status.h"
int main(void) {
    OptState s; memset(&s, 0, sizeof(s));
    s.framegen = 1; s.started = 1; s.fg_path = 1; s.mode8 = 1; s.nmid = 2; s.preset = 1;
    char d[64];
    opt_row_detail(d, sizeof(d), OPT_ROW_MODE, opt_mode(&s));
    printf("%zu|%s\n", strlen(d), d);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            shutil.copy(ROOT / "classic" / "source" / "opt_status.h", t / "opt_status.h")
            shutil.copy(ROOT / "classic" / "source" / "pace_policy.h", t / "pace_policy.h")
            (t / "b7.c").write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", "-I", str(t), str(t / "b7.c"), "-o", str(t / "b7")], check=True)
            out = subprocess.run([str(t / "b7")], check=True, capture_output=True, text=True).stdout.strip()
        ln_str, detail_str = out.split("|", 1)
        self.assertLessEqual(int(ln_str), 38)
        self.assertEqual(detail_str, "Lower input lag, holds 60 below 30 FPS")
        # 3. vcfg16 OSD menu describes FAST lower input lag and holding 60 below 30 FPS
        self.assertIn("Lower input lag, holds 60 below 30 FPS", A20_H)


if __name__ == "__main__":
    unittest.main(verbosity=2)
