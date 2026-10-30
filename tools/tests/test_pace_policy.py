#!/usr/bin/env python3
"""Pacing policy of the classic plugin: classic/source/pace_policy.h compiled and executed on the host, plus the call
sites in classic/source/main.c that must use it.

Compiler: config.json llvm_bin (clang.exe + lli.exe, as the other harnesses) when present, otherwise the host clang/cc
building a native binary.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8", errors="replace")

REFRESH_US = 1e6 / 59.939      # the TV of the CUSA18723 runs (CADENCE "refresh 59.939 Hz")


def extract_function(src: str, signature: str) -> str:
    start = src.index(signature)
    pos = src.index("{", src.index(")", start))
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[start:end]


def compile_and_run(src: str) -> str:
    inc = ["-I" + str(ROOT / "classic" / "source")]
    flags = ["-O0", "-Wall", "-Wno-unused-variable", "-Wno-unused-function", "-Wno-unused-but-set-variable"]
    with tempfile.TemporaryDirectory() as td:
        c = pathlib.Path(td) / "pace_policy_test.c"
        c.write_text(src, encoding="utf-8")
        cfg_path = ROOT / "config.json"
        llvm = pathlib.Path(json.loads(cfg_path.read_text(encoding="utf-8"))["llvm_bin"]) if cfg_path.exists() else None
        if llvm is not None and (llvm / "clang.exe").exists() and (llvm / "lli.exe").exists():
            bc = pathlib.Path(td) / "pace_policy_test.bc"
            subprocess.run([str(llvm / "clang.exe"), "--target=x86_64-pc-windows-msvc", *flags, *inc,
                            "-emit-llvm", "-c", str(c), "-o", str(bc)], check=True)
            cmd = [str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bc)]
        else:
            cc = shutil.which("clang") or shutil.which("cc")
            if cc is None:
                raise unittest.SkipTest("no C compiler (config.json llvm_bin or host clang/cc)")
            exe = pathlib.Path(td) / "pace_policy_test"
            subprocess.run([cc, *flags, *inc, str(c), "-o", str(exe)], check=True)
            cmd = [str(exe)]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def rows(out: str) -> dict:
    d = {}
    for line in out.splitlines():
        k, _, v = line.partition(" ")
        d[k] = v
    return d


PERIOD_HARNESS = r"""
#include <stdio.h>
#include "pace_policy.h"
int main(void)
{
    const double vb = %(vb).6f;
    const uint64_t freq = 1600000000ull;           /* any counter rate; the drift is relative */
    /* one 2-s window of the monitor: 120 vblanks, timestamps in whole us */
    uint64_t dt = (uint64_t)(120.0 * vb + 0.5);
    uint32_t q8 = vb_period_q8_from_window(120, dt);
    printf("q8_us %%.4f\n", q8 / 256.0);
    printf("bad_window %%u %%u %%u\n", vb_period_q8_from_window(0, dt), vb_period_q8_from_window(10, dt),
           vb_period_q8_from_window(120, 120ull * 40000ull));
    printf("hz10_fallback_us %%.3f\n", vb_period_q8_from_hz10(599) / 256.0);
    /* pair releases every 2 refreshes for 60 s: offset of release k from the true vblank grid */
    uint64_t pair = vb_ticks(freq, q8, 2), pair_old = freq * 20ull / 599ull;
    double worst = 0, worst_old = 0;
    for (uint64_t k = 1; k <= 1800; k++) {
        double t = (double)(k * pair) * 1e6 / (double)freq, t_old = (double)(k * pair_old) * 1e6 / (double)freq;
        double e = t - 2.0 * vb * (double)k, e_old = t_old - 2.0 * vb * (double)k;
        if (e < 0) e = -e;
        if (e_old < 0) e_old = -e_old;
        if (e > worst) worst = e;
        if (k <= 60 && e_old > worst_old) worst_old = e_old;
    }
    printf("drift_60s_us %%.2f\n", worst);
    printf("drift_old_2s_us %%.2f\n", worst_old);
    printf("per_frame %%u %%u %%u %%u\n", vb_per_frame(60, q8), vb_per_frame(30, q8), vb_per_frame(20, q8), vb_per_frame(0, q8));
    return 0;
}
""" % {"vb": REFRESH_US}


class TestRefreshPeriod(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = rows(compile_and_run(PERIOD_HARNESS))

    def test_window_measures_the_exact_period(self) -> None:
        self.assertAlmostEqual(float(self.r["q8_us"]), REFRESH_US, delta=0.01)
        self.assertEqual(self.r["bad_window"], "0 0 0", "too few vblanks or an out-of-range period must be rejected")
        self.assertAlmostEqual(float(self.r["hz10_fallback_us"]), 1e7 / 599, delta=0.01)

    def test_pair_releases_stay_on_the_vblank_grid(self) -> None:
        # 1800 pairs = 60 s: below 1/10 of the 1.55 ms latch margin, so no vblank slip in a session
        self.assertLess(float(self.r["drift_60s_us"]), 150.0)
        # the rounded 59.9 Hz drifted 1.3 ms every 2 s (the console sawtooth); keeps the discriminator honest
        self.assertGreater(float(self.r["drift_old_2s_us"]), 1000.0)

    def test_frame_caps_are_whole_refreshes(self) -> None:
        self.assertEqual(self.r["per_frame"], "1 2 3 1")

    def test_main_c_pacers_use_the_measured_period(self) -> None:
        for sig in ("static void pace_pair_ex(", "static void pace_hz(", "static uint32_t vblank_period_us("):
            body = extract_function(MAIN_C, sig)
            self.assertNotIn("g_last_hz_x10", body, f"{sig} must not use the 0.1 Hz rounded rate")
            self.assertIn("refresh_period_q8()", body, f"{sig} must use the measured refresh period")
        mon = extract_function(MAIN_C, "static void *mon_thread(")
        self.assertIn("vb_period_q8_from_window(vs.count - win_vb, vs.processTime - win_t)", mon)
        self.assertRegex(extract_function(MAIN_C, "static void pace_pair_ex("),
                         r"vb_ticks\(g_freq, refresh_period_q8\(\), nvb\)")   # nvb: 2 refreshes, 3 for a 3-slot pair (test_below30.py)


SHQ_HARNESS = r"""
#include <stdio.h>
#include "pace_policy.h"
static void run(const char *name, const uint32_t *ema, int n)
{
    ShallowQueue q = {0, 0};
    printf("%s ", name);
    for (int i = 0; i < n; i++) putchar(shq_step(&q, ema[i]) ? '1' : '0');
    putchar('\n');
}
int main(void)
{
    static uint32_t a[400], b[400], c[400], d[400];
    for (int i = 0; i < 400; i++) {
        a[i] = 25000u;                                              /* steady 40 fps game: own 25 ms */
        b[i] = i < 200 ? 25000u : (i < 300 ? 31500u : 32500u);     /* creeps up to 31.5 ms, then past 32 ms */
        c[i] = ((i / 30) & 1) ? 33300u : 25000u;                   /* 30 <-> 40 fps every second */
        d[i] = i == 250 ? 0u : 25000u;                             /* eligibility lost for one frame */
    }
    run("steady", a, 400);
    run("creep", b, 400);
    run("swing", c, 400);
    run("reset", d, 400);
    return 0;
}
"""


class TestShallowQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = rows(compile_and_run(SHQ_HARNESS))

    def test_enters_after_the_dwell_only(self) -> None:
        s = self.r["steady"]
        self.assertEqual(s.index("1"), 120, "on at the 121st frame below 30 ms (about 4 s)")
        self.assertNotIn("0", s[120:])

    def test_hysteresis_keeps_it_on_until_32_ms(self) -> None:
        s = self.r["creep"]
        self.assertEqual(s[120:300], "1" * 180, "31.5 ms is inside the hysteresis band: stays on")
        self.assertEqual(s[300:], "0" * 100, "above 32 ms: off at once and no re-entry")

    def test_a_game_swinging_between_30_and_40_fps_keeps_the_old_queue(self) -> None:
        self.assertNotIn("1", self.r["swing"])

    def test_losing_eligibility_restarts_the_dwell(self) -> None:
        s = self.r["reset"]
        self.assertEqual(s[250], "0")
        self.assertEqual(s.index("1", 251), 251 + 120)

    def test_main_c_gates_the_submit_on_the_raw_queue(self) -> None:
        fn = extract_function(MAIN_C, "static int32_t flip_inner_ex(")
        gate = fn.index("int shq_on = shq_step(&s_shq, shq_ok ? g_cpu_ema_us : 0u);")
        self.assertIn("int shq_ok = g_inject_on == 8 && second_flip && !third_flip && g_cfg.fps_lock != 20 && pace_timer_on();", fn)
        wait = fn.index("qs.flipPendingNum <= SHQ_MAX_PENDING", gate)
        self.assertIn("read_flip_status((int32_t)handle, &qs)", fn[gate:wait])   # raw status, not the virtual queue
        self.assertLess(wait, fn.index("pace_pair_ex((int32_t)handle, dt_enter_us, 1, "), "before the pre-submit pacer")
        self.assertLess(wait, fn.index("gnm_submit_and_flip_raw(has_wl, workload, ncount, d2, s2"), "before the submit")
        self.assertIn("sceKernelGetProcessTime() - t_shq0 > 40000ull", fn, "bounded wait")


FTRACE_HARNESS = r"""
#include <stdio.h>
#include "pace_policy.h"
int main(void)
{
    uint32_t rec[20][3];
    uint32_t h = 4294900000u;                      /* hand-over clock wraps past 2^32 inside the trace */
    for (int i = 0; i < 20; i++) {
        h += 33367u + (i & 1);
        rec[i][0] = h;
        rec[i][1] = ft_pack_sub(120u + i, i == 3 ? 900000u : 24600u + i);     /* own time saturates at 65535 */
        uint32_t c = 0;
        if (i != 5) c = ft_with_screen(c, 50040u + i);                         /* record 5: never seen on screen */
        c = ft_with_mid_vb(c, 1);
        c = ft_with_real_vb(c, i == 7 ? 20u : 1u);                             /* clamps at 15 */
        rec[i][2] = c;
    }
    char buf[512];
    uint32_t n = ft_format(buf, sizeof(buf), (const uint32_t (*)[3])rec, 15, rec[0][0] - 33367u);
    printf("n %u\n", n);
    printf("line %s", buf);
    char small[120];
    printf("small %u\n", ft_format(small, sizeof(small), (const uint32_t (*)[3])rec, 15, 0));
    printf("small_line %s", small);
    printf("sizeof_ring %u\n", (unsigned)(FT_N * 3u * sizeof(uint32_t)));
    return 0;
}
"""


class TestFtrace(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = rows(compile_and_run(FTRACE_HARNESS))

    def parse(self, line: str):
        head, _, body = line.partition("|")
        self.assertTrue(head.startswith("FTRACE "))
        out = []
        for item in body.strip().split(";"):
            if not item.strip():
                continue
            dh, dsub, dscr, vb, own = item.strip().split(",")[:5]   # 6th field (generated frames): test_below30.py
            rv, mv = vb.split("/")
            out.append((int(dh), int(dsub), int(dscr), int(rv), int(mv), int(own)))
        return int(head.split()[1]), out

    def test_fields_round_trip(self) -> None:
        self.assertEqual(self.r["n"], "15")
        base, recs = self.parse(self.r["line"])
        self.assertEqual(len(recs), 15)
        self.assertEqual(recs[0][0], 33367, "first dh uses the previous line's last hand-over")
        self.assertEqual([x[0] for x in recs[1:4]], [33368, 33367, 33368], "dh across the 2^32 wrap")
        self.assertEqual(recs[2][1], 122)
        self.assertEqual(recs[2][2], 50042)
        self.assertEqual(recs[5][2], 0, "not seen on screen stays 0")
        self.assertEqual(recs[3][5], 65535, "own time saturates")
        self.assertEqual((recs[7][3], recs[7][4]), (15, 1), "vblank counts clamp at 15")
        self.assertEqual(recs[0][5], 24600)

    def test_line_never_overflows(self) -> None:
        n = int(self.r["small"])
        self.assertGreater(n, 0)
        self.assertLess(n, 15)
        self.assertEqual(len(self.parse(self.r["small_line"])[1]), n)
        self.assertLess(len("small_line " + self.r["small_line"]), 120 + 12)

    def test_ring_is_small(self) -> None:
        self.assertLessEqual(int(self.r["sizeof_ring"]), 2048)

    def test_main_c_traces_only_with_the_debug_log(self) -> None:
        fn = extract_function(MAIN_C, "static int32_t flip_inner_ex(")
        self.assertIn("if (g_log && show_idx < 16u) {   // FTRACE record", fn)
        io = extract_function(MAIN_C, "static void *io_thread(")
        self.assertIn("ft_format(fb, sizeof(fb)", io)
        self.assertIn("lg_file_only(fb)", io)
        mon = extract_function(MAIN_C, "static void *mon_thread(")
        self.assertIn("ft_with_screen(fv, dscr)", mon)
        self.assertIn("ft_with_real_vb(", mon)
        self.assertIn("static uint32_t g_ft[FT_N][3];", MAIN_C)


class TestFgOffTimings(unittest.TestCase):
    """GPU time and CPU release -> submit with frame generation off (post-only path, build_dcb_post8)."""

    def test_post8_writes_fresh_timestamps(self) -> None:
        fn = extract_function(MAIN_C, "static uint32_t build_dcb_post8(")
        early = fn.index("if (!rcas_flags && !osd_wh) return 0;")
        clear = fn.index("*(volatile uint64_t *)(slotb + 0x910 + i * 8) = 0;")
        self.assertGreater(clear, early)
        self.assertIn("((volatile uint32_t *)(slotb + STATS_OFF))[i] = 0xFFFFFFFFu;", fn, "no stale FRAMES statistics")
        ts0 = fn.index("TS_EOP(0); TS_EOP(1); TS_EOP(2); TS_EOP(3);")
        first_pass = fn.index("SET_PASS(")
        last_dispatch = fn.rindex("PKT3(0x15, 4, 1)")
        ts4 = fn.index("TS_EOP(4);")
        final_label = fn.rindex("PKT3(0x47, 5, 0)")
        self.assertLess(clear, ts0)
        self.assertLess(ts0, first_pass, "pyramid / motion / fence stamps before the post pass (they read 0 ms)")
        self.assertLess(last_dispatch, ts4)
        self.assertLess(ts4, final_label, "end stamp before the completion label")

    def test_reader_consumes_each_sample_once(self) -> None:
        fn = extract_function(MAIN_C, "static int32_t flip_inner_ex(")
        seen = fn.index("if (ts[0] && ts[4] > ts[0] && g_ts_slot_seen != ts[0]) {")
        clear = fn.index("ts[0] = 0;   // consumed", seen)
        self.assertLess(clear, fn.index("g_slot_cpu[slot] = cpu;", seen), "cleared inside the consuming block")
        self.assertGreater(clear, fn.index("g_ts_n++;", seen))

    def test_release_time_is_set_without_a_second_flip(self) -> None:
        fn = extract_function(MAIN_C, "static int32_t flip_inner_ex(")
        self.assertIn("if (!second_flip) g_rel_t = sceKernelGetProcessTime();", fn)
        n0 = fn.index("if (n == 0) {")
        self.assertIn("g_rel_t = sceKernelGetProcessTime();\n                    return rc0;", fn[n0:n0 + 900])


def extract_block(src: str, marker: str) -> str:
    start = src.index(marker)
    pos = src.index("{", start)
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[pos:end]


TICK = extract_function(MAIN_C, "static void tick(")
RATE_BLOCK = extract_block(TICK, "{   // Track frame rate class transitions")
_fn = extract_function(MAIN_C, "static int32_t flip_inner_ex(")
WANT_FG = _fn[_fn.index("int want_fg = ") + len("int want_fg = "):]
WANT_FG = WANT_FG[:WANT_FG.index(";")]

REENABLE_HARNESS = r"""
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "pace_policy.h"
#define INJECT_AFTER_FLIPS 120
static struct { int framegen, fps_lock; } g_cfg;
static int g_detected_60fps, g_known_game = 1, g_menu_open, g_fg_off, g_inject_on = 8, g_ab_x_flag;
static uint32_t g_last_disk_hash, g_bw = 1920, g_bh = 1080;
static char g_inert_reason[32];
static void gv_start(int on) { (void)on; }
static int fg_res_supported(uint32_t w, uint32_t h) { (void)w; (void)h; return 1; }
#define lg(...) ((void)0)
static void window(uint32_t n, uint32_t sm_rfps)
{
    double rfps = sm_rfps / 10.0;
    (void)rfps;
    __BLOCK__
}
int main(void)
{
    const int locks[4] = { 0, 20, 30, 60 };
    for (int k = 0; k < 4; k++) {
        /* the run2 sequence: framegen=1 in the file, 60 fps in the warm-up, then the 30 fps class for good */
        g_cfg.framegen = 1; g_cfg.fps_lock = locks[k]; g_fg_off = 0; g_last_disk_hash = 7;
        uint32_t n = 60;
        for (int i = 0; i < 4; i++, n += 30) window(n, 600);
        g_detected_60fps = 1;
        snprintf(g_inert_reason, sizeof(g_inert_reason), "60FPS NATIVE (FG OFF)");
        int want_before = __WANT_FG__;
        for (int i = 0; i < 10; i++, n += 15) window(n, 340);
        printf("lock%d before %d after %d framegen %d detected %d inert %s\n", locks[k], want_before, __WANT_FG__,
               g_cfg.framegen, g_detected_60fps, g_inert_reason[0] ? "set" : "clear");
    }
    return 0;
}
"""


class TestFpsLock60(unittest.TestCase):
    """The auto re-enable block of tick() and the want_fg expression, both taken from main.c and executed."""

    @classmethod
    def setUpClass(cls) -> None:
        src = REENABLE_HARNESS.replace("__BLOCK__", RATE_BLOCK).replace("__WANT_FG__", WANT_FG)
        cls.r = rows(compile_and_run(src))

    def test_fps_lock_60_is_kept_after_the_drop(self) -> None:
        self.assertEqual(self.r["lock60"], "before 0 after 0 framegen 1 detected 1 inert set")

    def test_other_values_keep_todays_behaviour(self) -> None:
        self.assertEqual(self.r["lock0"], "before 0 after 1 framegen 1 detected 0 inert clear")
        self.assertEqual(self.r["lock20"], "before 0 after 1 framegen 1 detected 0 inert clear")
        self.assertEqual(self.r["lock30"], "before 1 after 1 framegen 1 detected 0 inert clear")


if __name__ == "__main__":
    unittest.main()
