#!/usr/bin/env python3
"""CPU cost of the plugin's own work (classic/source/cpu_cost.h, opt-in cuts monev / iolazy / nospin / tracelite /
quietlog, and the "CPU plugin" line).

1. Helpers of cpu_cost.h compiled on the host: today's pacer rule (sleep until 1 ms before the target, nothing below
   1.5 ms) is kept as the default, nospin's sleep, the lateness average, the io period.
2. Vblank PLL (vblank_pll_update from main.c, compiled unchanged) fed by the two monitor schedules: today's
   sceKernelUsleep(500) polling with the measured wake-up lateness, and monev's vblank events. The PLL only reads the
   vblank's own timestamp, so when no vblank is skipped both feed it the same samples and the state is bit for bit the
   same; with event wake-ups late by more than a refresh (skipped vblanks) the prediction error stays within today's.
3. Flight recorder (overlay/vcfg_trace.h compiled on the host, real files in a temporary directory): without the lite
   switch the file is byte for byte the format of today at every flush; with it the readers see the same structure
   (header counts, the first lines, a contiguous tail with the skipped count, the end marker last, so the app's "last
   line" is the same) while far fewer bytes are written. Switching lite on in the middle of a game start rewrites once.
4. main.c call sites: every cut is read from its own file, the default paths are today's, the periodic lines go
   through lgd, and the CPU plugin line fits the 384-byte log buffer.

Same compiler choice as test_pace_policy.py (config.json llvm_bin, otherwise the host clang/cc).
"""
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

from test_pace_policy import MAIN_C, ROOT, compile_and_run, extract_function, rows

CPU_COST_H = (ROOT / "classic" / "source" / "cpu_cost.h").read_text(encoding="utf-8")
TRACE_H = ROOT / "overlay" / "vcfg_trace.h"


HELPERS = r"""
#include <stdio.h>
#include "cpu_cost.h"
int main(void)
{
    unsigned long long rem[8] = { 0, 1000, 1500, 1501, 2000, 16683, 33366, 100000 };
    printf("spin");
    for (int i = 0; i < 8; i++) printf(" %u", cc_spin_sleep_us(rem[i]));
    printf("\nnospin");
    for (int i = 0; i < 8; i++) printf(" %u", cc_nospin_sleep_us(rem[i], 675u));
    printf("\nnospin0");
    for (int i = 0; i < 8; i++) printf(" %u", cc_nospin_sleep_us(rem[i], 0u));
    uint32_t e = 0;
    e = cc_late_ema(e, 1000, 1675); printf("\nema %u", e);
    e = cc_late_ema(e, 1000, 1000); printf(" %u", e);
    e = cc_late_ema(e, 1000, 900); printf(" %u", e);
    e = cc_late_ema(e, 1000, 100000); printf(" %u", e);
    printf("\nio %u %u %u %u\n", cc_io_sleep_us(0, 0), cc_io_sleep_us(0, 1), cc_io_sleep_us(1, 0), cc_io_sleep_us(1, 1));
    printf("ts %llu %llu %llu\n", (unsigned long long)cc_ts_us(2, 345678999), (unsigned long long)cc_ts_us(-1, 0),
           (unsigned long long)cc_ts_us(0, 999));
    printf("rate %.1f %.1f\n", cc_per_s(1720, 2000000), cc_per_s(5, 0));
    return 0;
}
"""


def pll_globals() -> str:
    out = []
    for name in ("g_pll_phi_us", "g_pll_vb_cnt", "g_pll_period_q8", "g_pll_lock"):
        m = re.search(r"^static volatile [^\n]*\b%s\b = [^;]+;" % name, MAIN_C, re.M)
        out.append(m.group(0))
    return "\n".join(out)


PLL_SIM = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
%(globals)s
%(update)s

static uint64_t rs;
static double urand(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (double)(rs >> 11) * (1.0 / 9007199254740992.0); }
#define NVB 7200                                 /* 2 minutes of vblanks */
static const double P = 1e6 / 59.939;            /* the TV of the CUSA18723 runs */
static uint64_t vb_ts[NVB];                      /* hardware timestamp of each vblank (us) */
static double vb_true[NVB];
static uint64_t phi_at[NVB]; static uint32_t per_at[NVB]; static int seen_at[NVB];

static void reset_pll(void) { g_pll_phi_us = 0; g_pll_vb_cnt = 0; g_pll_period_q8 = (16683u << 8) | 85u; g_pll_lock = 0; }
static int latest(double t) { int k = (int)((t - 1000000.0) / P) + 1; while (k >= 0 && (k >= NVB || vb_true[k] > t)) k--; while (k + 1 < NVB && vb_true[k + 1] <= t) k++; return k; }
static void sample(double t)
{
    int k = latest(t);
    if (k < 0 || k >= NVB) return;
    vblank_pll_update((uint64_t)k + 1000u, vb_ts[k]);
    phi_at[k] = g_pll_phi_us; per_at[k] = g_pll_period_q8; seen_at[k] = 1;
}
/* Prediction error of the next vblank edge right after each vblank is first sampled (us), and wake-ups per second. */
static void report(const char *tag, uint64_t wakes, double dur_us)
{
    double emax = 0.0, esum = 0.0; int n = 0, skipped = 0; double e99[NVB]; int ne = 0;
    for (int k = 600; k < NVB - 1; k++) {
        if (!seen_at[k]) { skipped++; continue; }
        double pred = (double)phi_at[k] + (double)per_at[k] / 256.0;
        double e = pred - vb_true[k + 1]; if (e < 0) e = -e;
        if (e > emax) emax = e;
        esum += e; n++; e99[ne++] = e;
    }
    for (int i = 1; i < ne; i++) { double v = e99[i]; int j = i - 1; while (j >= 0 && e99[j] > v) { e99[j + 1] = e99[j]; j--; } e99[j + 1] = v; }
    printf("%%s wakes_per_s %%.1f skipped %%d err_avg %%.2f err_p99 %%.2f err_max %%.2f\n", tag, wakes * 1e6 / dur_us, skipped,
           n ? esum / n : 0.0, ne ? e99[(int)(ne * 0.99)] : 0.0, emax);
}
static uint64_t poll_phi[NVB]; static uint32_t poll_per[NVB]; static int poll_seen[NVB];
int main(void)
{
    rs = 0x9E3779B97F4A7C15ull;
    for (int k = 0; k < NVB; k++) {
        vb_true[k] = 1000000.0 + k * P;
        double j = (urand() - 0.5) * 16.0;                    /* timestamp residual avg ~8 us ... */
        if (urand() < 0.001) j += 600.0 * (urand() - 0.5);    /* ... rare ~600 us (max 623 measured) */
        vb_ts[k] = (uint64_t)(vb_true[k] + j + 0.5);
    }
    double end_t = vb_true[NVB - 1];
    /* today: sceKernelUsleep(500) + lateness (measured avg 657-684 us, p90 of maxima 5.1 ms, max 6.1 ms) */
    reset_pll(); memset(seen_at, 0, sizeof(seen_at));
    uint64_t wakes = 0;
    for (double t = 1000000.0; t < end_t; ) {
        double u = urand(), late = u < 0.80 ? 400 + 300 * urand() : u < 0.97 ? 700 + 800 * urand() : u < 0.997 ? 1500 + 2500 * urand() : 4000 + 2100 * urand();
        t += 500.0 + late; wakes++;
        sample(t);
    }
    report("poll", wakes, end_t - 1000000.0);
    memcpy(poll_phi, phi_at, sizeof(phi_at)); memcpy(poll_per, per_at, sizeof(per_at)); memcpy(poll_seen, seen_at, sizeof(seen_at));
    /* monev, every event on time (wake 30-3000 us after the vblank; flip events add one wake per vblank) */
    for (int sc = 0; sc < 2; sc++) {
        reset_pll(); memset(seen_at, 0, sizeof(seen_at));
        wakes = 0;
        double last_wake = 0.0;
        for (int k = 0; k < NVB; k++) {
            double u = urand(), lat = u < 0.95 ? 30 + 270 * urand() : 300 + 2700 * urand();
            if (sc == 1 && urand() < 0.01) lat = 17000 + 20000 * urand();   /* 1 %% of wake-ups late by 1-3 refreshes */
            double t = vb_true[k] + lat;
            if (t <= last_wake) continue;              /* still busy: the coalesced event is consumed by that wake-up */
            last_wake = t; wakes += 2;                 /* the vblank event and the flip event of that refresh */
            sample(t);
        }
        report(sc ? "event_late" : "event", wakes, end_t - 1000000.0);
        if (sc == 0) {
            int same = 1, both = 0;
            for (int k = 0; k < NVB; k++) if (seen_at[k] && poll_seen[k]) { both++; if (phi_at[k] != poll_phi[k] || per_at[k] != poll_per[k]) same = 0; }
            printf("identical %%d %%d\n", same, both);
        }
    }
    return 0;
}
"""


TRACE_HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define LOG_DIR "%(dir)s"
static char g_title_id[16] = "CUSA18723";
typedef struct { uint64_t Size; char VersionString[0x1C]; uint32_t Version; } OrbisKernelSwVersion;
static uint64_t g_now = 5000000ull;
static uint64_t sceKernelGetProcessTime(void) { return g_now; }
static int sceKernelMkdir(const char *p, int m) { (void)p; (void)m; return 0; }
int32_t sceKernelGetSystemSwVersion(OrbisKernelSwVersion *v) { strcpy(v->VersionString, "13.508.001"); return 0; }
#define VCFG_VER 2.34
#define VCFG_ENGINE "classic"
#include "vcfg_trace.h"
static void dump(unsigned at)
{
    char p[160]; snprintf(p, sizeof(p), LOG_DIR "/trace-CUSA18723.txt", 0);
    FILE *f = fopen(p, "rb"); if (!f) { printf("@@SNAP %%u none\n", at); return; }
    static char buf[1 << 20]; size_t n = fread(buf, 1, sizeof(buf), f); fclose(f);
    printf("@@SNAP %%u %%zu\n", at, n); fwrite(buf, 1, n, stdout); printf("@@END\n");
}
int main(int argc, char **argv)
{
    int mode = argv[1][0] - '0';                  /* 0 today, 1 lite, 2 lite from line 120 */
    unsigned n = 2700;                             /* 10 minutes at 4.5 lines/s */
    uint64_t seed = 12345;
    g_tr_lite = mode == 1;
    for (unsigned i = 0; i < n; i++) {
        if (mode == 2 && i == 120) g_tr_lite = 1;
        seed = seed * 6364136223846793005ull + 1442695040888963407ull;
        unsigned len = 20 + (unsigned)((seed >> 33) %% 260u);
        char line[400]; int k = snprintf(line, sizeof(line), "line %%05u %%s", i, (i %% 97 == 13) ? "FAIL " : "");
        while (k < (int)len && k < 380) line[k++] = (char)('a' + (i + k) %% 26);
        line[k++] = '\n'; line[k] = 0;
        g_now += 222222ull;
        vcfg_trace(line);
        if (i %% 50 == 49 || i == 199 || i == 200 || i == 201 || i == n - 1) dump(i);
    }
    g_now += 3000000ull; vcfg_trace("final crash marker\n"); dump(n);
    printf("@@STATS flushes %%u bytes %%llu\n", g_tr_flushes, (unsigned long long)g_tr_bytes);
    return 0;
}
"""


def trace_run(mode: int):
    cc = shutil.which("clang") or shutil.which("cc")
    if cc is None:
        raise unittest.SkipTest("no host C compiler")
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "trace.c"
        src.write_text(TRACE_HARNESS % {"dir": td}, encoding="utf-8")
        exe = pathlib.Path(td) / "trace"
        subprocess.run([cc, "-O0", "-w", "-I" + str(TRACE_H.parent), str(src), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe), str(mode)], check=True, capture_output=True).stdout.decode("latin-1")
    snaps, stats = [], None
    for part in out.split("@@SNAP ")[1:]:
        head, _, rest = part.partition("\n")
        at, size = head.split()
        if size == "none":
            continue
        body = rest.split("@@END\n")[0]
        snaps.append((int(at), body))
        if "@@STATS" in rest:
            stats = rest.split("@@STATS ")[1].split()
    return snaps, {"flushes": int(stats[1]), "bytes": int(stats[3])}


def expected_lines():
    """The lines the harness logs (before the 200-byte cut and the timestamp), to check content and order."""
    seed, lines = 12345, []
    for i in range(2700):
        seed = (seed * 6364136223846793005 + 1442695040888963407) & (2 ** 64 - 1)
        ln = 20 + ((seed >> 33) % 260)
        s = "line %05u %s" % (i, "FAIL " if i % 97 == 13 else "")
        s = list(s)
        k = len(s)
        while k < ln and k < 380:
            s.append(chr(ord('a') + (i + k) % 26))
            k += 1
        lines.append("".join(s))
    return lines + ["final crash marker"]


def parse_trace(body: str):
    ls = body.split("\n")
    assert ls[0] == "VCFG trace" and ls[2] == "engine: classic" and ls[3] == "title: CUSA18723", ls[:6]
    total = int(ls[5].split(":", 1)[1].strip())
    secs = int(ls[6].split(":", 1)[1].strip())
    assert ls[7] == "---- first lines ----"
    i, head, tail, skipped = 8, [], [], None
    while not ls[i].startswith("----"):
        head.append(ls[i]); i += 1
    if ls[i].startswith("---- last lines ("):
        skipped = int(re.match(r"---- last lines \((\d+) skipped before\) ----", ls[i]).group(1)); i += 1
        while not ls[i].startswith("----"):
            tail.append(ls[i]); i += 1
    assert ls[i] == "---- end of trace ----" and ls[i + 1:] == [""], ls[i:]
    return total, secs, head, tail, skipped


def line_index(row: str) -> int:
    m = re.match(r"\s*\d+\.\d\d (?:line (\d{5})|final crash marker)", row)
    assert m, row
    return int(m.group(1)) if m.group(1) else 2700


def render_today(total, secs, head, tail, skipped, firmware="13.508.001"):
    s = "VCFG trace\nversion: 2.34\nengine: classic\ntitle: CUSA18723\nfirmware: %s\nlog lines: %u\nseconds since start: %u\n" % (
        firmware, total, secs)
    s += "---- first lines ----\n" + "".join(h + "\n" for h in head)
    if skipped is not None:
        s += "---- last lines (%u skipped before) ----\n" % skipped + "".join(t + "\n" for t in tail)
    return s + "---- end of trace ----\n"


class CpuCostHelpers(unittest.TestCase):
    def test_helpers(self):
        r = rows(compile_and_run(HELPERS))
        # today's pacer: sleep rem - 1000 above 1500 us, otherwise spin all of it
        self.assertEqual(r["spin"].split(), ["0", "0", "0", "501", "1000", "15683", "32366", "99000"])
        # nospin: sleep rem - lateness, nothing when rem <= lateness + 200
        self.assertEqual(r["nospin"].split(), ["0", "325", "825", "826", "1325", "16008", "32691", "99325"])
        self.assertEqual(r["nospin0"].split(), ["0", "1000", "1500", "1501", "2000", "16683", "33366", "100000"])
        self.assertEqual(r["ema"].split(), ["675", "591", "517", "952"])   # 1/8 steps, a 99 ms outlier clamped to 4 ms
        self.assertEqual(r["io"].split(), ["2000", "2000", "10000", "2000"])
        self.assertEqual(r["ts"].split(), ["2345678", "0", "0"])
        self.assertEqual(r["rate"].split(), ["860.0", "0.0"])


class MonitorEventsKeepThePll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = PLL_SIM % {"globals": pll_globals(), "update": extract_function(MAIN_C, "static void vblank_pll_update(")}
        cls.r = {}
        for line in compile_and_run(src).splitlines():
            parts = line.split()
            if parts[0] == "identical":
                cls.identical = (int(parts[1]), int(parts[2]))
            else:
                cls.r[parts[0]] = {parts[i]: float(parts[i + 1]) for i in range(1, len(parts) - 1, 2)}

    def test_same_samples_same_pll_state(self):
        same, both = self.identical
        self.assertGreater(both, 7000)
        self.assertEqual(same, 1, "with every vblank sampled the PLL state differs between polling and events")
        self.assertEqual(self.r["poll"]["skipped"], 0)
        self.assertEqual(self.r["event"]["skipped"], 0)
        self.assertEqual(self.r["event"]["err_max"], self.r["poll"]["err_max"])

    def test_wake_ups(self):
        self.assertGreater(self.r["poll"]["wakes_per_s"], 700)       # ~860/s on the console (monitor thread wake count)
        self.assertLessEqual(self.r["event"]["wakes_per_s"], 2 * 59.94 + 0.5)

    def test_late_events_stay_within_todays_error(self):
        late, poll = self.r["event_late"], self.r["poll"]
        self.assertGreater(late["skipped"], 30)                       # the scenario does skip vblanks
        self.assertLessEqual(late["err_p99"], poll["err_p99"] + 2.0)
        self.assertLessEqual(late["err_max"], max(poll["err_max"] * 1.5, 100.0))
        self.assertLess(late["err_avg"], 20.0)


class FlightRecorderLite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = expected_lines()
        cls.runs = {m: trace_run(m) for m in (0, 1, 2)}

    def check_structure(self, snaps, max_tail):
        for at, body in snaps:
            total, secs, head, tail, skipped = parse_trace(body)
            self.assertEqual([line_index(h) for h in head], list(range(min(total, 200))), at)
            if total > 200:
                self.assertIsNotNone(skipped, at)
                idx = [line_index(t) for t in tail]
                self.assertEqual(idx, list(range(200 + skipped, total)), at)   # contiguous, ends with the latest line
                self.assertLessEqual(len(tail), max_tail, at)
            else:
                self.assertIsNone(skipped, at)
            last = (tail or head)[-1]
            self.assertEqual(line_index(last), total - 1, at)                  # the app's "last line"
            for row in head + tail:
                k = line_index(row)
                self.assertTrue(row.endswith(self.lines[k][:max(0, 200 - 2 - 8)][-20:]) or len(row) >= 190, (at, row))
            self.assertGreaterEqual(at + 1, total, at)
            self.assertGreaterEqual(total, at + 1 - 10, at)                    # flushed at most 2 s (9 lines) behind

    def test_today_format_is_unchanged(self):
        snaps, _ = self.runs[0]
        self.check_structure(snaps, 200)
        for at, body in snaps:
            self.assertEqual(body, render_today(*parse_trace(body)), at)

    def test_lite_same_structure(self):
        snaps, _ = self.runs[1]
        self.check_structure(snaps, 400)
        for at, body in snaps:
            for hl in body.split("\n")[5:7]:
                self.assertRegex(hl, r"^(log lines|seconds since start): \d+ *$")

    def test_lite_same_lines_at_every_checkpoint(self):
        today = {at: parse_trace(b) for at, b in self.runs[0][0]}
        for at, body in self.runs[1][0]:
            total, secs, head, tail, skipped = parse_trace(body)
            t_total, t_secs, t_head, t_tail, t_skipped = today[at]
            self.assertEqual((total, secs, head), (t_total, t_secs, t_head), at)
            self.assertEqual(tail[len(tail) - len(t_tail):] if t_tail else [], t_tail, at)

    def test_lite_writes_far_less(self):
        b0, b1 = self.runs[0][1]["bytes"], self.runs[1][1]["bytes"]
        self.assertGreater(b0, 10_000_000)      # 10 minutes: ~50 KB every 2 s plus the 200 head rewrites
        self.assertLess(b1 * 10, b0, (b0, b1))

    def test_lite_switched_on_mid_start(self):
        self.check_structure(self.runs[2][0], 400)


def c_format_len(fmt: str, values) -> int:
    py = re.sub(r"%(-?\d*)(?:ll|l|z)?([udx])", r"%\1d", fmt)
    py = py.replace("%x", "%d")
    return len(py % tuple(values))


class CallSites(unittest.TestCase):
    def test_each_cut_has_its_own_file(self):
        fn = extract_function(MAIN_C, "static void cc_read_cuts(void)")
        for name, var in (("monev", "g_cc_monev"), ("iolazy", "g_cc_iolazy"), ("nospin", "g_cc_nospin"),
                          ("tracelite", "g_tr_lite"), ("quietlog", "g_cc_quiet")):
            self.assertIn('%s = cc_file("%s")' % (var, name), fn)
        self.assertIn('snprintf(p, sizeof(p), LOG_DIR "/%s_%s", name, g_title_id)', extract_function(MAIN_C, "static int cc_file("))
        start = extract_function(MAIN_C, "static void io_start(void)")
        self.assertLess(start.index("cc_read_cuts();"), start.index("scePthreadCreate"))
        for v in ("g_cc_monev = 0", "g_cc_iolazy = 0", "g_cc_nospin = 0", "g_cc_quiet = 0"):
            self.assertIn(v, MAIN_C)
        self.assertIn("static int g_tr_lite = 0;", TRACE_H.read_text(encoding="utf-8"))

    def test_monitor_default_is_todays_poll(self):
        w = extract_function(MAIN_C, "static int cc_mon_wait(void)")
        self.assertIn("if (g_cc_monev && g_handle >= 0 && cc_mon_attach(g_handle))", w)
        self.assertTrue(w.rstrip().endswith("sceKernelUsleep(CC_MON_POLL_US);\n    return 0;\n}"))
        self.assertIn("#define CC_MON_POLL_US       500u", CPU_COST_H)
        mon = extract_function(MAIN_C, "static void *mon_thread(void *arg)")
        self.assertIn("int woke_vb = cc_mon_wait();", mon)
        self.assertNotIn("sceKernelUsleep", mon)
        att = extract_function(MAIN_C, "static int cc_mon_attach(int32_t h)")
        self.assertIn("sceVideoOutAddVblankEvent(g_cc_eq, h, CC_EV_VBLANK)", att)
        self.assertIn("if (!g_cc_eq_vb) g_cc_monev = 0;", att)          # refused: polling as before

    def test_pending_flip_decisions_are_fresh_with_events(self):
        self.assertEqual(MAIN_C.count("cc_pending_flips()"), 6)
        fn = extract_function(MAIN_C, "static uint32_t cc_pending_flips(void)")
        self.assertIn("read_flip_status(g_handle, &fs)", fn)
        self.assertIn("return __atomic_load_n(&g_cur_pending_flips, __ATOMIC_ACQUIRE);", fn)

    def test_pacers_wait_through_cc_wait_until(self):
        for sig in ("static void pace_hz(uint32_t hz)", "static void pace_pair_ex("):
            fn = extract_function(MAIN_C, sig)
            self.assertIn("cc_wait_until(", fn)
            self.assertNotIn("while (sceKernelGetProcessTimeCounter() <", fn)
        w = extract_function(MAIN_C, "static void cc_wait_until(uint64_t target)")
        self.assertIn("g_cc_nospin ? cc_nospin_sleep_us(rem_us, g_cc_late_ema) : cc_spin_sleep_us(rem_us)", w)
        self.assertIn("if (!g_cc_nospin) {", w)

    def test_io_default_periods(self):
        io = extract_function(MAIN_C, "static void *io_thread(void *arg)")
        self.assertIn("sceKernelUsleep(cc_io_sleep_us(g_cc_iolazy, busy || g_cfg.game_vectors));", io)
        self.assertIn("int inert_due = g_cc_iolazy ? (!inert_checked && io_now - io_t0 >= CC_IO_INERT_US) : (poll_n == 5000);", io)
        self.assertIn("int cfg_due = g_cc_iolazy ? (io_now - cfg_t >= CC_IO_CFG_US) : ((poll_n % 250) == 0);", io)
        self.assertIn("if (cfg_due && g_inject_on == 8 && !g_bench_enabled) {", io)

    def test_periodic_lines_use_lgd(self):
        for pre in ("LATENCY real frame", "CADENCE (vblanks", "CPU release->submit", "STAT gnm:",
                    "PASSES ms:", "PASS RAW last", "PASSES split ms", "FRAMES %u", "HOOK ms per FG", "QUEUE shallow %s",
                    "BELOW30 %s", "CPU plugin:", "OWN fg-off:"):
            self.assertIn('lgd("' + pre, MAIN_C, pre)
            self.assertNotIn(' lg("' + pre, MAIN_C, pre)
        lgd = extract_function(MAIN_C, "static void lgd(const char *fmt, ...)")
        self.assertIn("if (g_cc_quiet && !g_log) return;", lgd)
        w = extract_function(MAIN_C, "static void lg_write(const char *buf)")
        self.assertLess(w.index("if (buf[0] == CC_QUIET_MARK)"), w.index("klog("))

    def test_cpu_line_fits_the_log_buffer(self):
        m = re.search(r'lgd\("CPU plugin: (.*?)\\n",', MAIN_C, re.S)
        fmt = "CPU plugin: " + "".join(re.findall(r'"?([^"]*)"?\s*', m.group(1).replace('"\n            "', "")))
        fmt = fmt.replace('"', "")
        worst = [999.0, 999, 999, 999, 99999.0, 999999, 9999.0, 999.0, 99.99, 99.99, 99.99, 9999.0, 9999.0, 99.99, 9999.0,
                 999999, 9999, 9999.0, 999, 999999.0, 999.9, 999999.0, 1, 1, 1, 1, 1, 0x80020016, 99.9]
        n = c_format_len(fmt, worst)
        self.assertLess(n, 383, (n, fmt))

    def test_fgoff_own_time_is_apart_from_the_pacers(self):
        fn = extract_function(MAIN_C, "static void cc_fgoff_log(void)")
        self.assertNotIn("g_cpu_", fn)
        self.assertIn("if (!g_mon_on && g_log) g_cc_off_rel = sceKernelGetProcessTime();", MAIN_C)
        self.assertIn("if (!g_mon_on) cc_fgoff_log();", MAIN_C)


if __name__ == "__main__":
    unittest.main()
