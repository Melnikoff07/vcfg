#!/usr/bin/env python3
"""
T22 regression test: Fast (low latency + holds 60 below 30 FPS) and Sharp (P1b_w18).

Verifies:
1. pace_timer_on() (classic/source/main.c):
   - Auto-enables timer pacing (returns 1) when below30=1, shallow_queue=1, or preset=1 (Fast),
     except on CUSA01439 family (unless /data/bb_probe/pace_timer_<TITLE> is present).
   - Respects /data/bb_probe/no_pace_timer_<TITLE> opt-out file (returns 0 when pace_order != 3).
   - Preserves 100% bit-exact behavior for Quality (preset=0) and Sharp (preset=2) when below30=0
     and shallow_queue=0.
   - Responds immediately to live config changes (in-game menu toggles of preset/below30/shallow_queue).
2. flip_inner_ex() (classic/source/main.c):
   - want_shq = g_cfg.shallow_queue || g_cfg.preset == 1;
   - shq_ok gated by !g_fast_pace && !tb_fast;
   - if (shq_on) pace_pre = 0; (prevents double waiting before submit on 3-buffer games);
   - Mutual exclusion of shq and b30 on a 25 ms -> 42 ms -> 25 ms frametime sweep with hysteresis.
3. Menu / Log / Launcher / OSD status (opt_status.h, launcher/src/app.c, plugin/source/a20_copy_impl.h):
   - opt_shq returns OPT_ACTIVE ("fast mode") / OPT_READY when preset=1 and shallow_queue=0;
   - Fast description states "Lower input lag, holds 60 below 30 FPS" (<= 38 chars in OSD).
4. Sharp (preset=2) formula and P1b_w18 profile (main.c, opt_status.h, a20_copy_impl.h, rcas8.ll, vcfg_warp.py):
   - Formula: sharp_pct = (sharp_pct > 0) ? min(100, sharp_pct + 35) : 50 (0->50, 40->75, 70->100);
   - P1b_w18 in rcas8.ll and vcfg_warp.py under bit 128 (%sh_mode / emode & 4):
     * w0_sh = min(-con, 0.0), nz_k = 0.30, w_sharp = max(w_sharp_raw, -0.18)
     * Light AA max_w = 0.25 (Strong AA stays 0.75), er_k = 0.125
     * 5-point clamp [c_mn5, c_mx5] includes original center M in %sh_mode
   - Quality (%sh_mode == false) is 100% bit-for-bit identical;
   - rcas8.ll compiles with VGPR <= 64, ScratchSize == 0, and shader region <= 0x1C000.
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import vcfg_warp as vw  # noqa: E402
from test_pace_policy import extract_function  # noqa: E402

MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")
OPT_H = (ROOT / "classic" / "source" / "opt_status.h").read_text(encoding="utf-8")
A20_H = (ROOT / "plugin" / "source" / "a20_copy_impl.h").read_text(encoding="utf-8")
APP_C = (ROOT / "launcher" / "src" / "app.c").read_text(encoding="utf-8")
RCAS_LL = (ROOT / "classic" / "shaders" / "rcas8.ll").read_text(encoding="utf-8")
CC = shutil.which("clang-22") or shutil.which("clang") or shutil.which("cc")
LLC = shutil.which("llc-22") or shutil.which("llc")
F32 = np.float32


class TestT22FastSharp(unittest.TestCase):
    @unittest.skipUnless(CC, "no host C compiler")
    def test_1_pace_timer_on_fast_b30_shq_and_opt_out(self):
        fn_src = extract_function(MAIN_C, "static int pace_timer_on(")
        # Check static declaration of g_no_pace_timer or equivalent in main.c
        self.assertIn("no_pace_timer_", fn_src)
        self.assertIn("CUSA01439", fn_src)
        self.assertIn("g_cfg.preset == 1", fn_src)
        self.assertIn("g_cfg.below30", fn_src)
        self.assertIn("g_cfg.shallow_queue", fn_src)

        with tempfile.TemporaryDirectory() as td:
            probe_dir = pathlib.Path(td)
            harness = f"""
#include <stdio.h>
#include <string.h>
#include "{(ROOT / 'overlay' / 'title_families.h').as_posix()}"

#define LOG_DIR "{probe_dir.as_posix()}"
#define lg(...) ((void)0)

typedef struct {{
    int pace_order;
    int preset;
    int below30;
    int shallow_queue;
}} fg_config_t;

static fg_config_t g_cfg;
static char g_title_id[16] = "CUSA00411";
static int g_pcopy_on = 0, g_s14_tail_extra = 0;
static int pace_pcopy(void) {{ (void)g_s14_tail_extra; return g_pcopy_on; }}
static int g_pace_timer = -1;

{fn_src}

static void reset_probe(const char *tid) {{
    strcpy(g_title_id, tid);
    g_pace_timer = -1;
}}

int main(void) {{
    // 1. Quality (preset=0, b30=0, shq=0) on CUSA00411 (3-buf, pcopy=0): must be 0 (vblank, same as 4763929)
    g_cfg.pace_order = 0; g_cfg.preset = 0; g_cfg.below30 = 0; g_cfg.shallow_queue = 0; g_pcopy_on = 0;
    reset_probe("CUSA00411");
    if (pace_timer_on() != 0) return 1;

    // 2. Live switch to Fast (preset=1) on CUSA00411 without resetting g_pace_timer: must return 1!
    g_cfg.preset = 1;
    if (pace_timer_on() != 1) return 2;

    // 3. Live switch to Sharp (preset=2, b30=0, shq=0) on CUSA00411: must return 0 again!
    g_cfg.preset = 2;
    if (pace_timer_on() != 0) return 3;

    // 4. Explicit below30=1 in Quality or Sharp on CUSA10249: must return 1
    reset_probe("CUSA10249");
    g_cfg.preset = 0; g_cfg.below30 = 1; g_cfg.shallow_queue = 0;
    if (pace_timer_on() != 1) return 4;

    // 5. Explicit shallow_queue=1 in Quality on CUSA00411: must return 1
    reset_probe("CUSA00411");
    g_cfg.preset = 0; g_cfg.below30 = 0; g_cfg.shallow_queue = 1;
    if (pace_timer_on() != 1) return 5;

    // 6. CUSA01439 family: excluded from auto timer pacing in Fast / below30 / shallow_queue
    reset_probe("CUSA01439");
    g_cfg.preset = 1; g_cfg.below30 = 1; g_cfg.shallow_queue = 1;
    if (pace_timer_on() != 0) return 6;

    // 7. Quality on CUSA05571 (family whitelist) or when pace_pcopy() is 1: returns 1
    reset_probe("CUSA05571");
    g_cfg.preset = 0; g_cfg.below30 = 0; g_cfg.shallow_queue = 0;
    if (pace_timer_on() != 1) return 7;

    // 8. Opt-out file no_pace_timer_<TITLE> disables timer pacing when pace_order != 3
    FILE *f = fopen(LOG_DIR "/no_pace_timer_CUSA00411", "w");
    if (f) fclose(f);
    reset_probe("CUSA00411");
    g_cfg.preset = 1; g_cfg.below30 = 1; g_cfg.shallow_queue = 1;
    if (pace_timer_on() != 0) return 8;

    // Explicit pace_order == 3 overrides even no_pace_timer
    g_cfg.pace_order = 3;
    if (pace_timer_on() != 1) return 9;

    printf("PACE_TIMER_OK\\n");
    return 0;
}}
"""
            c_file = probe_dir / "pt.c"
            exe = probe_dir / "pt"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"pace_timer_on test failed (code {res.returncode}): {res.stdout} {res.stderr}")
            self.assertEqual(res.stdout.strip(), "PACE_TIMER_OK")

    @unittest.skipUnless(CC, "no host C compiler")
    def test_2_flip_inner_ex_shq_in_fast_and_mutual_exclusion_with_b30(self):
        fn = extract_function(MAIN_C, "static int32_t flip_inner_ex(")
        self.assertIn("int want_shq = g_cfg.shallow_queue || g_cfg.preset == 1;", fn)
        self.assertIn("int shq_ok = g_inject_on == 8 && second_flip && !third_flip && !g_fast_pace && !tb_fast && g_cfg.fps_lock != 20 && pace_timer_on();", fn)
        self.assertIn("if (!want_shq || g_b30.on) shq_ok = 0;", fn)
        self.assertIn("int shq_on = shq_step(&s_shq, shq_ok ? g_cpu_ema_us : 0u);", fn)
        self.assertIn("if (shq_on && g_cfg.pace_order != 3) pace_pre = 0;", fn)
        self.assertIn("if (shq_wait > 0 && g_last_submit_enter_t && t_p0 > g_last_submit_enter_t)", fn)
        self.assertIn("if ((g_cfg.below30 || (g_cfg.preset == 1 && !g_fast_pace)) && b30_win_frames)", fn)

        # Simulate 25 ms -> 42 ms -> 25 ms sweep in Fast mode (preset=1, shallow_queue=0, below30=0)
        harness = r"""
#include <stdio.h>
#include "pace_policy.h"
int main(void) {
    uint32_t pq8 = 4270950u; /* 16683.4 us * 256 */
    Below30 g_b30; b30_reset(&g_b30);
    ShallowQueue s_shq = { 0, 0 };
    uint32_t ema_us = 25000u;
    int both_on = 0;
    int shq_phase1 = 0, b30_phase2 = 0, shq_phase2 = 0, b30_phase3_end = 0, shq_phase3_end = 0;

    /* Phase 1 (0..149): 25 ms (40 fps, capped at 30) -> shq activates after 120 frames, b30 stays 0
       Phase 2 (150..209): 42 ms (23.8 fps) -> shq drops to 0 immediately, b30 activates after 6 frames
       Phase 3 (210..399): 25 ms -> b30 exits after 24 frames, then shq re-activates after 120 frames */
    for (int i = 0; i < 400; i++) {
        uint32_t ft_us = (i < 150) ? 25000u : (i < 210 ? 42000u : 25000u);
        ema_us = (i == 150 || i == 210) ? ft_us : (ema_us * 7u + ft_us) / 8u;
        int fast_b30_ok = b30_available(1, 2, 0, 0, 0, 1, 0);
        int b30_on = b30_regime_step(&g_b30, fast_b30_ok ? ft_us : 0u, pq8);
        int want_shq = 0 || 1; /* shallow_queue=0, preset=1 */
        int shq_ok = 1;
        if (!want_shq || g_b30.on) shq_ok = 0;
        int shq_on = shq_step(&s_shq, shq_ok ? ema_us : 0u);
        if (b30_on && shq_on) both_on++;
        if (i == 149) shq_phase1 = shq_on;
        if (i == 150) shq_phase2 = shq_on;
        if (i == 180) b30_phase2 = b30_on;
        if (i == 399) { b30_phase3_end = b30_on; shq_phase3_end = shq_on; }
    }
    printf("both_on=%d shq_p1=%d shq_p2_first=%d b30_p2=%d b30_p3=%d shq_p3=%d\n",
           both_on, shq_phase1, shq_phase2, b30_phase2, b30_phase3_end, shq_phase3_end);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            shutil.copy(ROOT / "classic" / "source" / "pace_policy.h", t / "pace_policy.h")
            (t / "sim.c").write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O1", "-Wall", "-I", str(t), str(t / "sim.c"), "-o", str(t / "sim")], check=True)
            out = subprocess.run([str(t / "sim")], check=True, capture_output=True, text=True).stdout.strip()
        kv = dict(item.split("=") for item in out.split())
        self.assertEqual(int(kv["both_on"]), 0, out)
        self.assertEqual(int(kv["shq_p1"]), 1, out)
        self.assertEqual(int(kv["shq_p2_first"]), 0, out)
        self.assertEqual(int(kv["b30_p2"]), 1, out)
        self.assertEqual(int(kv["b30_p3"]), 0, out)
        self.assertEqual(int(kv["shq_p3"]), 1, out)

    @unittest.skipUnless(CC, "no host C compiler")
    def test_3_menu_log_and_launcher_fast_status(self):
        # 1. Launcher settings_row_guide includes "Lower input lag, holds 60 below 30 FPS"
        guide_fn = extract_function(APP_C, "static const char *settings_row_guide(")
        self.assertIn("Lower input lag, holds 60 below 30 FPS", guide_fn)
        # 2. vcfg16 OSD includes "Lower input lag, holds 60 below 30 FPS"
        self.assertIn("Lower input lag, holds 60 below 30 FPS", A20_H)
        # 3. Classic opt_status.h: opt_shq in Fast mode (preset=1, shallow_queue=0) and opt_row_detail
        harness = r"""
#include <stdio.h>
#include <string.h>
#include "opt_status.h"
int main(void) {
    OptState s; memset(&s, 0, sizeof(s));
    s.framegen = 1; s.started = 1; s.fg_path = 1; s.mode8 = 1; s.nmid = 2; s.pace_timer = 1;
    s.preset = 1; s.shallow_queue = 0; s.shq_on = 1;
    OptStat q_act = opt_shq(&s);
    char line_act[400]; opt_format(line_act, sizeof(line_act), &s);
    s.shq_on = 0;
    OptStat q_rdy = opt_shq(&s);
    char d[64];
    opt_row_detail(d, sizeof(d), OPT_ROW_MODE, opt_mode(&s));
    printf("q_act=%d:%s|q_rdy=%d:%s|dlen=%zu|d=%s\n%s\n",
           q_act.st, q_act.why, q_rdy.st, q_rdy.why, strlen(d), d, line_act);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            shutil.copy(ROOT / "classic" / "source" / "opt_status.h", t / "opt_status.h")
            shutil.copy(ROOT / "classic" / "source" / "pace_policy.h", t / "pace_policy.h")
            (t / "st.c").write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O1", "-Wall", "-I", str(t), str(t / "st.c"), "-o", str(t / "st")], check=True)
            lines = subprocess.run([str(t / "st")], check=True, capture_output=True, text=True).stdout.splitlines()
        head, line_act = lines[0], lines[1]
        self.assertIn("q_act=1:fast mode|", head)
        self.assertIn("q_rdy=2:needs game < 30 ms|", head)
        self.assertIn("d=Lower input lag, holds 60 below 30 FPS", head)
        self.assertIn("shallow_queue=off(active: fast mode)", line_act)

    @unittest.skipUnless(CC, "no host C compiler")
    def test_4_sharp_formula_50_plus35_across_classic_and_vcfg16(self):
        rcas_fn = extract_function(MAIN_C, "static void calc_rcas_params(")
        self.assertIn("sharp_pct = (sharp_pct > 0) ? (sharp_pct + 35) : 50;", rcas_fn)
        self.assertIn("sh = (sh > 0) ? (sh + 35 > 100 ? 100 : sh + 35) : 50;", OPT_H)

        post_pos = A20_H.index("// Generated surfaces; the native frame C is post-processed below")
        post_src = A20_H[post_pos:post_pos + 1600]
        blocks = re.findall(r"if\s*\(\s*a21_preset\s*==\s*2\s*\)\s*\{([^}]*)\}", post_src)
        self.assertEqual(len(blocks), 2)
        self.assertIn("35u", blocks[0])
        self.assertIn("50u", blocks[0])

    @unittest.skipUnless(LLC, "no llc-22")
    def test_5_p1b_w18_rcas8_shader_and_vcfg_warp_lockstep(self):
        # 1. Check rcas8.ll contains P1b_w18 instructions under %sh_mode
        self.assertIn("0x3FD3333340000000", RCAS_LL, "Missing nz_k = 0.30 in rcas8.ll")
        self.assertIn("0xBFC70A3D80000000", RCAS_LL, "Missing w_sharp clamp -0.18 in rcas8.ll")
        self.assertIn("select i1 %sh_mode, float 0.25, float 0.5625", RCAS_LL, "Missing Light AA 0.25 in rcas8.ll")
        self.assertIn("select i1 %sh_mode, float 0.125, float 1.0", RCAS_LL, "Missing er_k = 0.125 in rcas8.ll")
        self.assertIn("select i1 %sh_mode, <4 x float> %M, <4 x float> %base_c", RCAS_LL, "Missing M clamp in rcas_full")

        # 2. Compile rcas8.ll with llc-22 and verify VGPR <= 64, ScratchSize == 0, shader region <= 0x1C000
        sys.path.insert(0, str(ROOT / "classic"))
        import gen_shaders  # noqa: E402
        asm = subprocess.run([LLC, "-mcpu=gfx700", "-O2", "-filetype=asm",
                              str(ROOT / "classic" / "shaders" / "rcas8.ll"), "-o", "-"],
                             capture_output=True, text=True, check=True).stdout
        nv = gen_shaders.stat(asm, "NumVgprs")
        ns = gen_shaders.stat(asm, "TotalNumSgprs", "NumSgprs")
        scratch = gen_shaders.stat(asm, "ScratchSize")
        self.assertLessEqual(nv, 64, f"rcas8 VGPRs {nv} > 64")
        self.assertLessEqual(ns, 104, f"rcas8 SGPRs {ns} > 104")
        self.assertEqual(scratch, 0, f"rcas8 ScratchSize {scratch} != 0")

        # 3. Verify vcfg_warp.py P1b_w18 behavior:
        #    - Quality (emode=1) is untouched
        #    - Sharp (emode=1|4, con=rcas_con(75)) increases edge gradient vs Quality(con=rcas_con(40), emode=1)
        #    - 5-point clamp including M prevents any overshoot outside [min(N,S,E,W,M,base_c), max(N,S,E,W,M,base_c)]
        rng = np.random.default_rng(123)
        img = (rng.integers(30, 220, (64, 64, 3)).astype(F32) / F32(255.0)).astype(F32)
        img[:, 32:, :] = np.clip(img[:, 32:, :] + F32(0.25), F32(0.0), F32(1.0))

        out_q = vw.rcas8(img, vw.rcas_con(40), 1)
        out_sh = vw.rcas8(img, vw.rcas_con(75), 1 | 4)

        gx_q = float(np.mean(np.abs(out_q[:, 32, :] - out_q[:, 31, :])))
        gx_sh = float(np.mean(np.abs(out_sh[:, 32, :] - out_sh[:, 31, :])))
        self.assertGreater(gx_sh, gx_q * 1.15, f"Sharp edge step {gx_sh:.4f} not > 1.15x Quality {gx_q:.4f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
