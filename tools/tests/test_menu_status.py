#!/usr/bin/env python3
"""Menu rows that apply, and the live state of every option (classic/source/opt_status.h, overlay/vov.c, launcher).

1. Availability, compiled from opt_status.h with host clang over every combination that matters:
   - Below 30 FPS is ready/active exactly where the flip hook's b30_available() lets the regime run (plus mode 8 on
     the SubmitAndFlip path, where the regime code is), active only inside the regime, and each inert case names its
     reason (Fast, fps_lock 20, no 2nd buffer, memory, timer pacing, restart after a below30 = 0 start);
   - Low latency is ready/active exactly where the hook's shq_ok can be true, paused by the below-30 regime;
   - Sharpness / Anti-aliasing say which frames they touch and the memory case that skips the sharpening;
   - Fast says when it is limited to 1 generated frame per pair (no 2nd buffer, below30 = 0 at start);
   - row visibility: Below 30 FPS hidden with Fast, FG off or fps_lock 20; Mode / Game vectors / HUD with FG off;
   - every header detail fits the 38 characters of the overlay status line and the log line fits a log slot.
   Negative controls: the same checks run on mutated copies of opt_status.h (a dropped condition) and must fail.
2. main.c: the rows drawn follow opt_row_visible, row by row, in the order of menu_adjust_row; the decisions the
   status copies are unchanged (same expressions as before the status existed).
3. Overlay (overlay/vov.c compiled on the host): with every state word, label, word and value never overlap and stay
   inside the 512 px panel; the words get their own colours.
4. App (launcher/src compiled on the host): Mode cycles QUALITY -> FAST -> SHARP, each named; Below 30 FPS and Low
   latency are skipped by Up/Down when they do not apply.
"""
import itertools
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "classic" / "source"
OPT_H = (SRC / "opt_status.h").read_text(encoding="utf-8")
MAIN = (SRC / "main.c").read_text(encoding="utf-8", errors="replace")
CLANG = shutil.which("clang-22") or shutil.which("clang")

ST = {0: "off", 1: "active", 2: "ready", 3: "inert", 4: "limited"}

# Harness: prints one line per combination with the inputs and the statuses (code and reason).
HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "opt_status.h"
static void pr(const char *k, OptStat t) { printf(" %s=%d:%s", k, t.st, t.why); }
int main(void) {
    static const int presets[3] = { 0, 1, 2 }, locks[4] = { 0, 20, 30, 60 };
    for (int b30 = 0; b30 < 2; b30++) for (int fg = 0; fg < 2; fg++) for (int pi = 0; pi < 3; pi++) for (int li = 0; li < 4; li++)
    for (int fgs = 0; fgs < 3; fgs++) for (int m8 = 0; m8 < 2; m8++) for (int path = 1; path <= 2; path++)
    for (int nmid = 1; nmid <= 2; nmid++) for (int mt = 0; mt < 2; mt++) for (int pt = 0; pt < 2; pt++) for (int on = 0; on < 2; on++)
    for (int shq = 0; shq < 2; shq++) for (int sqon = 0; sqon < 2; sqon++) for (int bb = 0; bb < 4; bb++) {
        OptState s; memset(&s, 0, sizeof(s));
        s.below30 = b30; s.framegen = fg; s.preset = presets[pi]; s.fps_lock = locks[li]; s.shallow_queue = shq;
        s.started = fgs != 1;                 // fgs: 0 running, 1 starting, 2 inert (reason)
        s.fg_path = fgs == 0 ? path : 0;
        if (fgs == 2) opt_norm_reason(s.fg_why, sizeof(s.fg_why), "INERT: RES 1280x720");
        s.mode8 = m8; s.nmid = nmid; s.mem_tight_b30 = mt; s.pace_timer = pt; s.b30_on = on; s.shq_on = sqon; s.buf2_boot = bb;
        printf("C b30=%d fg=%d preset=%d lock=%d fgs=%d m8=%d path=%d nmid=%d mt=%d pt=%d on=%d shq=%d sqon=%d bb=%d avail=%d",
               b30, fg, s.preset, s.fps_lock, fgs, m8, path, nmid, mt, pt, on, shq, sqon, bb,
               b30_available(b30, nmid, mt, s.preset, !fg, pt, s.fps_lock));
        pr("B", opt_b30(&s)); pr("Q", opt_shq(&s)); pr("F", opt_fg(&s)); pr("M", opt_mode(&s));
        int vis[OPT_ROW_N], n = opt_menu_rows(fg, s.preset, s.fps_lock, vis);
        printf(" vis=");
        for (int i = 0; i < n; i++) printf("%d,", vis[i]);
        for (int r = 0; r < OPT_ROW_N; r++) {
            char d[64]; if (opt_row_detail(d, sizeof(d), r, opt_row_stat(&s, r))) printf(" |D%zu", strlen(d));
        }
        char line[400]; size_t ln = opt_format(line, sizeof(line), &s);
        printf(" L=%zu\n", ln);
    }
    {   // a line cut short drops whole tokens
        OptState s; memset(&s, 0, sizeof(s)); s.framegen = 1; s.below30 = 1; s.shallow_queue = 1; s.sharpness = 40; s.upscale = 2;
        s.hud_protect = 1; s.fps_lock = 30; s.started = 1; s.fg_path = 1; s.mode8 = 1; s.nmid = 2; s.pace_timer = 1; s.rcas_shader = 1;
        char full[400], cut[400]; opt_format(full, sizeof(full), &s);
        for (size_t cap = 1; cap < 200; cap += 7) { opt_format(cut, cap, &s); printf("T %zu|%s|%s\n", cap, cut, full); }
    }
    // post pass targets and memory: sharpness / AA / game vectors / HUD / fps lock
    for (int run = 0; run < 2; run++) for (int mt = 0; mt < 2; mt++) for (int up = 0; up < 3; up++) for (int pm = 0; pm < 4; pm++)
    for (int raa = 0; raa < 2; raa++) for (int sh = 0; sh < 2; sh++) for (int fast = 0; fast < 2; fast++) for (int nm = 1; nm <= 2; nm++)
    for (int on = 0; on < 2; on++) {
        OptState s; memset(&s, 0, sizeof(s));
        s.framegen = 1; s.started = 1; s.fg_path = run; s.mode8 = 1; s.rcas_shader = sh; s.mem_tight = mt; s.upscale = up;
        s.sharpness = 40; s.post_mid = pm; s.real_aa = raa; s.mid_only = pm == 1 || (pm != 2 && !raa);
        s.preset = fast; s.nmid = nm; s.b30_on = on;
        printf("P run=%d mt=%d up=%d pm=%d raa=%d sh=%d fast=%d nm=%d on=%d", run, mt, up, pm, raa, sh, fast, nm, on);
        pr("S", opt_sharp(&s)); pr("A", opt_aa(&s)); printf("\n");
    }
    return 0;
}
"""


def run_harness(header_text):
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        (t / "opt_status.h").write_text(header_text)
        shutil.copy(SRC / "pace_policy.h", t / "pace_policy.h")
        (t / "h.c").write_text(HARNESS)
        r = subprocess.run([CLANG, "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter", "-I", str(t),
                            str(t / "h.c"), "-o", str(t / "h")], capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("build failed:\n" + r.stderr[-3000:])
        out = subprocess.run([str(t / "h")], capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        if line.startswith("T "):
            cap, cut, full = line[2:].split("|", 2)
            rows.append(("T", (int(cap), cut, full), {}, [], None, 0))
            continue
        kv = dict(re.findall(r"(\w+)=(-?\d+)(?=[ \n]|$)", line.split(" B=")[0] if line.startswith("C ") else line.split(" S=")[0]))
        st = {k: (int(c), w.strip()) for k, c, w in re.findall(r" ([BQFMSA])=(\d):([^=|]*?)(?= [A-Z]=| vis=| \||$| L=)", line)}
        d = [int(x) for x in re.findall(r"\|D(\d+)", line)]
        m = re.search(r" vis=([\d,]*)", line)
        lm = re.search(r" L=(\d+)", line)
        rows.append((line[0], {k: int(v) for k, v in kv.items()}, st, d,
                     [int(x) for x in m.group(1).split(",") if x] if m else None, int(lm.group(1)) if lm else 0))
    return rows


def subprocess_lines_T(rows):
    return [r[1] for r in rows if r[0] == "T"]


def check_rules(rows):
    """Every availability rule; returns the list of violations (empty = all hold)."""
    bad = []
    for kind, c, st, details, vis, ln in rows:
        if kind != "C":
            continue
        b_st, b_why = st["B"]
        q_st, q_why = st["Q"]
        fg_run = c["fg"] and c["fgs"] == 0
        # Below 30: ready/active <=> the regime may run (b30_available, mode 8, SubmitAndFlip path, FG generating)
        can = c["avail"] and c["m8"] and c["path"] == 1 and fg_run
        starting = c["fgs"] == 1   # before the first generated frame: ready ("fg starting") where nothing known forbids it
        static_ok = c["b30"] and c["fg"] and c["preset"] != 1 and c["lock"] != 20 and c["m8"] and c["nmid"] >= 2 and not c["mt"]
        if starting:
            if (st["B"] == (2, "fg starting")) != bool(static_ok):
                bad.append(("b30 while starting", c, st["B"]))
        elif (b_st in (1, 2)) != bool(can):
            bad.append(("b30 vs b30_available", c, st["B"]))
        if can and (b_st == 1) != bool(c["on"]):
            bad.append(("b30 active <=> regime on", c, st["B"]))
        if c["b30"] and c["fg"] and c["preset"] == 1 and (b_st, b_why) != (3, "fast mode"):
            bad.append(("b30 inert in Fast", c, st["B"]))
        if c["b30"] and c["fg"] and c["preset"] != 1 and c["lock"] == 20 and (b_st, b_why) != (3, "fps lock 20"):
            bad.append(("b30 inert with fps_lock 20", c, st["B"]))
        if b_st == 3 and b_why.startswith("no 2nd buf") and (c["bb"] == 2) != ("restart" in b_why):
            bad.append(("b30 says restart only after a below30=0 start", c, st["B"]))
        if not c["b30"] and (b_st != 0 or (b_why == "save drops 2nd buffer") != (c["bb"] == 1)):
            bad.append(("b30 off, warns when a save drops the 2nd buffer", c, st["B"]))
        # Low latency: ready/active <=> shq_ok can be true (mode 8, fps_lock != 20, timer pacing, FG generating, gnm)
        shq_can = c["shq"] and c["fg"] and c["lock"] != 20 and c["m8"] and c["pt"] and fg_run and c["path"] == 1
        if starting:
            if (st["Q"] == (2, "fg starting")) != bool(c["shq"] and c["fg"] and c["lock"] != 20 and c["m8"]):
                bad.append(("shq while starting", c, st["Q"]))
        elif (q_st in (1, 2)) != bool(shq_can):
            bad.append(("shq vs shq_ok", c, st["Q"]))
        if shq_can and c["on"] and (q_st, q_why) != (2, "paused by below 30"):
            bad.append(("shq paused by the regime", c, st["Q"]))
        if shq_can and not c["on"] and (q_st == 1) != bool(c["sqon"]):
            bad.append(("shq active <=> shallow queue on", c, st["Q"]))
        # Fast limited without the 2nd buffer
        m_st, m_why = st["M"]
        if c["preset"] == 1 and fg_run:
            want = (1, "up to 2 generated") if c["nmid"] >= 2 else (4, "1 gen/pair: below30 off at boot" if c["bb"] == 2 else "1 gen/pair: no 2nd buffer")
            if (m_st, m_why) != want:
                bad.append(("fast limited", c, st["M"]))
        elif m_st != 0:
            bad.append(("mode state only for Fast", c, st["M"]))
        # visibility
        # OPT_ROW_*: Mode 1, Game vectors 5, HUD 6 need FG; Below 30 (8) also no Fast, no fps_lock 20; Low latency (9) no 20
        want_rows = [r for r in range(11)
                     if not ((r in (1, 5, 6) and not c["fg"])
                             or (r == 8 and (not c["fg"] or c["preset"] == 1 or c["lock"] == 20))
                             or (r == 9 and (not c["fg"] or c["lock"] == 20)))]
        if vis != want_rows:
            bad.append(("visible rows", c, vis))
        if any(d > 38 for d in details):
            bad.append(("detail longer than 38", c, details))
        if ln > 384 - len("options effective: ") - len(" | hot-reload") - 2:   # one log slot, cause of a reload included
            bad.append(("log line too long", c, ln))
    return bad


@unittest.skipUnless(CLANG, "no host clang")
class TestAvailability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = run_harness(OPT_H)

    def test_rules_hold_for_every_combination(self):
        n = sum(1 for r in self.rows if r[0] == "C")
        self.assertEqual(n, 2 * 2 * 3 * 4 * 3 * 2 * 2 * 2 * 2 * 2 * 2 * 2 * 2 * 4)
        bad = check_rules(self.rows)
        self.assertEqual(bad[:5], [], f"{len(bad)} violations")

    def test_each_state_and_reason_is_reached(self):
        seen = {(st[k][0], st[k][1]) for kind, c, st, *_ in self.rows if kind == "C" for k in ("B", "Q", "M")}
        for want in [(1, "game under 30 fps"), (2, "game at 30 fps"), (3, "fast mode"), (3, "fps lock 20"), (3, "fg off"),
                     (3, "memory tight"), (3, "no 2nd buffer"), (3, "no 2nd buf, restart"), (3, "no timer pacing"),
                     (3, "videoout flip path"), (3, "fg mode not 8"), (3, "fg inert"), (2, "fg starting"),
                     (2, "paused by below 30"), (2, "needs game < 30 ms"), (4, "1 gen/pair: below30 off at boot"),
                     (4, "1 gen/pair: no 2nd buffer"), (1, "up to 2 generated"), (0, "save drops 2nd buffer")]:
            self.assertIn(want, seen)

    def test_cut_line_keeps_whole_tokens(self):
        n = 0
        for line in subprocess_lines_T(self.rows):
            cap, cut, full = line
            self.assertLess(len(cut), max(cap, 1))
            self.assertTrue(full.startswith(cut), (cut, full))
            self.assertTrue(cut == "" or len(cut) == len(full) or full[len(cut)] == " ", (cut, full))
            n += 1
        self.assertGreater(n, 20)

    def test_post_targets(self):
        for kind, c, st, *_ in self.rows:
            if kind != "P":
                continue
            s, a = st["S"], st["A"]
            if not c["sh"]:
                self.assertEqual(s, (3, "no post shader"))
                continue
            if not c["run"]:
                self.assertEqual(s, (1, "real"))           # FG not generating: the native post pass, real frames
            elif c["mt"] and c["up"] == 0:   # build_dcb_flow8: !g_mem_tight || upscale > 0 for mid1; mid2 of a 3-slot pair ungated
                three = (c["fast"] and c["nm"] >= 2) or c["on"]
                self.assertEqual(s, (4, "memory: 2nd gen frame only") if three else (3, "memory, needs aa"))
            else:
                mid_only = c["pm"] == 1 or (c["pm"] != 2 and not c["raa"])
                self.assertEqual(s, (1, "generated" if mid_only else "generated+real"))
            if c["up"] == 0:
                self.assertEqual(a[0], 0)
            elif c["run"]:
                self.assertEqual(a, (1, "generated+real" if c["raa"] or c["pm"] == 2 else "generated"))

    def test_negative_controls_fail(self):
        mutants = {
            "b30 ignores the 2nd buffer": ('if (s->nmid < 2) return opt_stat(OPT_INERT,', 'if (0) return opt_stat(OPT_INERT,'),
            "b30 shown with Fast": ("case OPT_ROW_B30: return framegen != 0 && preset != 1 && fps_lock != 20;",
                                    "case OPT_ROW_B30: return framegen != 0 && fps_lock != 20;"),
            "shq not paused by the regime": ('if (s->b30_on) return opt_stat(OPT_READY, "paused by below 30");', ""),
            "fast never limited": ('return opt_stat(OPT_LIMITED,', 'return opt_stat(OPT_ACTIVE,'),
            "restart said after any start": ('s->buf2_boot == OPT_BUF2_OFF_B30 ? "no 2nd buf, restart"', '1 ? "no 2nd buf, restart"'),
        }
        for name, (old, new) in mutants.items():
            self.assertIn(old, OPT_H, name)
            bad = check_rules(run_harness(OPT_H.replace(old, new)))
            self.assertTrue(bad, f"mutant not detected: {name}")


class TestMainMenu(unittest.TestCase):
    def test_rows_follow_opt_row_visible(self):
        osd = MAIN[MAIN.index("static void osd_build_slot("):]
        osd = osd[:osd.index("vov_cpy(m.hints")]
        rows = re.findall(r'^\s*(?:if \(VIS\((OPT_ROW_\w+)\)\) )?vov_row\(&m, "([^"]+)"', osd, re.M)
        ids = re.search(r"enum \{ (OPT_ROW_FG.*?) \};", OPT_H, re.S).group(1).replace("\n", " ").split(",")
        ids = [i.strip() for i in ids if i.strip() and i.strip() != "OPT_ROW_N"]
        self.assertEqual(len(rows), len(ids))
        gated = {"OPT_ROW_MODE", "OPT_ROW_GV", "OPT_ROW_HUD", "OPT_ROW_B30", "OPT_ROW_SHQ"}
        for (cond, label), rid in zip(rows, ids):
            self.assertEqual(cond, rid if rid in gated else "", label)   # each gated row tests its own id, in OPT_ROW order
        self.assertIn("int vis[OPT_ROW_N], nvis = opt_menu_rows(c.framegen, c.preset, c.fps_lock, vis);", osd)
        self.assertIn("m.rows[i].tag = t.st;", osd)
        vh = (ROOT / "overlay" / "vov.h").read_text()
        self.assertIn("enum { VOV_TAG_NONE, VOV_TAG_ACTIVE, VOV_TAG_READY, VOV_TAG_INERT, VOV_TAG_LIMITED };", vh)
        self.assertIn("enum { OPT_OFF, OPT_ACTIVE, OPT_READY, OPT_INERT, OPT_LIMITED };", OPT_H)
        pad = MAIN[MAIN.index("static void pad_menu_step("):MAIN.index("int32_t scePadReadState_hook(")]
        self.assertIn("int row = vis[g_menu_sel];", pad)
        self.assertIn("menu_adjust_row(&g_menu_edit, row, dir);", pad)
        self.assertNotIn("% MENU_NROWS", pad)

    def test_decisions_unchanged(self):
        # The status only copies these; each expression is the one of the base (a9ea6e5), unchanged.
        for line in [
            "int b30_ok = b30_available(g_cfg.below30, g_nmid, g_mem_tight && g_s14_state != 2, g_cfg.preset, g_fg_off, pace_timer_on(), g_cfg.fps_lock);",
            "int b30_on = b30_regime_step(&g_b30, (b30_ok && !force_repeat) ? b30_feed(ft_own_us) : 0u, pq8);",
            "int shq_ok = g_inject_on == 8 && second_flip && !third_flip && g_cfg.fps_lock != 20 && pace_timer_on();",
            "if (!g_cfg.shallow_queue || g_b30.on) shq_ok = 0;",
            "int shq_on = shq_step(&s_shq, shq_ok ? g_cpu_ema_us : 0u);",
            "int want_fg = (!g_fg_off || g_ab_x_flag) && (!g_detected_60fps || g_cfg.fps_lock == 30);",
            "if (!g_mem_tight || g_cfg.upscale > 0)",
            # mid_lru_on: the integrated expression (m-perf: FG Fast takes the below30 buffer order too)
            "int mid_lru_on = (g_cfg.below30 || g_cfg.preset == 1) && !(pcopy && g_pcopy_k >= 2) && g_nmid >= 2 && (uint32_t)g_nmid <= MID_LRU_MAX;",
        ]:
            self.assertIn(line, MAIN)
        self.assertEqual(MAIN.count("int want_fg = (!g_fg_off || g_ab_x_flag) && (!g_detected_60fps || g_cfg.fps_lock == 30);"), 2)
        # status globals are written by the hooks and read only by the status code
        for g in ("g_st_fg_path", "g_st_shq_on", "g_st_pace_timer", "g_st_gv_active"):
            for m in re.finditer(re.escape(g), MAIN):
                ctx = MAIN[MAIN.rfind("\n", 0, m.start()):MAIN.find("\n", m.end())]
                self.assertTrue("__atomic_store_n(&" + g in ctx or "__atomic_load_n(&" + g in ctx
                                or "static volatile int" in ctx or "s->" in ctx, ctx)

    def test_log_lines(self):
        self.assertIn('lg("options effective: %s%s\\n", b, tail);', MAIN)
        apply_live = MAIN[MAIN.index("static void cfg_apply_live("):]
        apply_live = apply_live[:apply_live.index("\n}\n")]
        self.assertIn("opt_log_effective(reason);", apply_live)   # boot (init), hot-reload, menu apply/defaults/save
        self.assertIn("opt_status_tick(sceKernelGetProcessTime());", MAIN)
        self.assertIn("#define OPT_MIN_GAP_US 2000000ull", MAIN)


MODES_HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "opt_status.h"
int main(void) {
    for (int preset = 0; preset <= 2; preset++) for (int b30 = 0; b30 <= 1; b30++) for (int fp = 0; fp <= 1; fp++) {
        OptState s; memset(&s, 0, sizeof(s));
        s.framegen = 1; s.preset = preset; s.below30 = b30; s.fast_pace = fp; s.fps_lock = 30; s.started = 1; s.fg_path = 1;
        s.mode8 = 1; s.nmid = 2; s.pace_timer = 1;
        s.mid_lru = b30 || preset == 1;   // main.c: s->mid_lru copies mid_lru_on (pcopy off, 2 buffers)
        char line[400]; opt_format(line, sizeof(line), &s);
        printf("p%d_b%d_f%d|%s\n", preset, b30, fp, line);
    }
    return 0;
}
"""


class TestModesLog(unittest.TestCase):
    """m-int: Fast takes the mid_lru order without below30, and the fastpace_<TITLE> option is in the line."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            shutil.copy(SRC / "opt_status.h", t / "opt_status.h")
            shutil.copy(SRC / "pace_policy.h", t / "pace_policy.h")
            (t / "m.c").write_text(MODES_HARNESS)
            r = subprocess.run([CLANG, "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter",
                                "-I", str(t), str(t / "m.c"), "-o", str(t / "m")], capture_output=True, text=True)
            if r.returncode:
                raise AssertionError("build failed:\n" + r.stderr[-3000:])
            out = subprocess.run([str(t / "m")], capture_output=True, text=True, check=True).stdout
        cls.L = dict(l.split("|", 1) for l in out.splitlines())

    def test_mid_order_follows_the_decision(self):
        for k, line in self.L.items():
            preset, b30 = int(k[1]), int(k[4])
            self.assertEqual(" mid_order=lru" in " " + line, bool(b30 or preset == 1), (k, line))
        self.assertIn("s->mid_lru = (c->below30 || c->preset == 1) && !(g_pcopy_on && g_pcopy_k >= 2) && g_nmid >= 2 && "
                      "(uint32_t)g_nmid <= MID_LRU_MAX;", MAIN)

    def test_fast_pace_token(self):
        for k, line in self.L.items():
            preset, fp = int(k[1]), int(k[7])
            if preset == 1:
                self.assertRegex(line, r"preset=fast(\([^)]*\))? fast_pace=" + ("on" if fp else "off") + " ")
            elif fp:
                self.assertIn(" fast_pace=on(fast only) ", line)
            else:
                self.assertNotIn("fast_pace", line)
        self.assertIn("s->fast_pace = g_fast_pace;", MAIN)


VOV_HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include "vov.c"
static uint8_t mem[VOV_MEM_SZ];
static void cpy(char *d, const char *s, int n) { int i = 0; for (; s[i] && i < n - 1; i++) d[i] = s[i]; d[i] = 0; }
int main(int argc, char **argv) {
    (void)argc;
    vov_init(mem);
    const char *lab[] = { "Frame generation", "Mode", "Sharpness", "Anti-aliasing", "FPS counter", "Game vectors", "HUD protection",
                          "FPS lock", "Below 30 FPS", "Low latency", "Opacity" };
    int kinds[] = { VOV_TOGGLE, VOV_TEXT, VOV_SEGMENTS, VOV_TEXT, VOV_TEXT, VOV_TOGGLE, VOV_TEXT, VOV_TEXT, VOV_TOGGLE, VOV_TOGGLE, VOV_SEGMENTS };
    const char *vals[] = { "", "Quality", "", "Strong", "Detail TL", "", "Conservative", "Auto", "", "", "" };
    for (int tag = 0; tag <= VOV_TAG_LIMITED; tag++) for (int on = 0; on < 2; on++) for (int sel = 0; sel < 11; sel++) {
        VovMenu m; memset(&m, 0, sizeof(m));
        cpy(m.title, "CUSA18723", 40); m.out_fps_x10 = 598; m.game_fps_x10 = 281; m.sel = sel; m.nrows = 11;
        cpy(m.status, "GAME VECTORS: FG INERT, SAVE DROPS 2ND", 40);
        for (int i = 0; i < 11; i++) {
            cpy(m.rows[i].label, lab[i], 28); m.rows[i].kind = kinds[i]; m.rows[i].on = on; m.rows[i].pct = 100;
            int tagged = i == 0 || i == 1 || i == 2 || i == 3 || i == 5 || i == 8 || i == 9;   // rows opt_row_stat gives a state
            cpy(m.rows[i].value, i == 1 ? "Fast" : vals[i], 24); m.rows[i].tag = tagged ? tag : 0;
        }
        const uint8_t *pal, *pix; unsigned w, h;
        if (!vov_menu(&m, &pal, &pix, &w, &h)) return 1;
        for (int i = 0; i < 11; i++) {
            const VovRow *r = &m.rows[i];
            int bx = PADX + (i == sel ? text_w(&vov_text25, "› ", 0) : 0);
            int lend = bx + text_w(&vov_text25, r->label, 0);
            int vx = row_value_x(r, 512, PADX);
            int tg = r->tag;
            int tw = tg ? text_w(&vov_mono20, tg == 1 ? "ACTIVE" : tg == 2 ? "READY" : tg == 3 ? "INERT" : "LIMITED", 1) : 0;
            int tx = tg ? vx - 12 - tw : vx;
            printf("R tag=%d on=%d sel=%d row=%d lend=%d tx=%d tend=%d vx=%d w=%u h=%u\n", tg, on, sel, i, lend, tx, tg ? tx + tw : tx, vx, w, h);
        }
        if (tag && !on && sel == 0) {   // one bitmap per state word for the colour check
            char p[600]; snprintf(p, sizeof(p), "%s/menu_tag%d.bin", argv[1], tag);
            FILE *f = fopen(p, "wb"); fwrite(pal, 1, VOV_PAL_SZ, f); fwrite(pix, 1, (size_t)w * h, f); fclose(f);
            printf("B tag=%d w=%u h=%u\n", tag, w, h);
        }
    }
    return 0;
}
"""


@unittest.skipUnless(CLANG, "no host clang")
class TestOverlayLayout(unittest.TestCase):
    def test_words_fit_between_label_and_value(self):
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            (t / "h.c").write_text(VOV_HARNESS.replace("PADX", "24"))
            r = subprocess.run([CLANG, "-O1", "-w", "-I", str(ROOT / "overlay"), str(t / "h.c"), "-o", str(t / "h")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            out = subprocess.run([str(t / "h"), str(t)], capture_output=True, text=True, check=True).stdout
            n = 0
            for line in out.splitlines():
                if not line.startswith("R "):
                    continue
                v = dict((k, int(x)) for k, x in re.findall(r"(\w+)=(\d+)", line))
                n += 1
                self.assertLessEqual(v["lend"], (v["tx"] - 8) if v["tag"] else 296, line)   # label never clipped
                if v["tag"]:
                    self.assertEqual(v["tend"], v["vx"] - 12, line)
                self.assertLessEqual(v["vx"], 512 - 24, line)
                self.assertEqual(v["w"], 512)
                self.assertLessEqual(v["h"], 720)
            self.assertEqual(n, 5 * 2 * 11 * 11)
            # the state word pixels carry its colour: blue ACTIVE, ink READY, red INERT / LIMITED (palette B,G,R,A)
            want = {1: (0x4f, 0x86, 0xe6), 2: (0x0b, 0x0b, 0x0b), 3: (0xf2, 0x50, 0x2e), 4: (0xf2, 0x50, 0x2e)}
            for tag, rgb in want.items():
                b = (t / f"menu_tag{tag}.bin").read_bytes()
                pal, pix = b[:1024], b[1024:]
                # row 0 (Frame generation, toggle off): its word ends 12 px left of the OFF pill
                cols = set()
                head = 168
                rowh = (720 - head - 56) // 11
                y0, y1 = head + rowh // 2 - 12, head + rowh // 2 + 8
                for y in range(y0, y1):
                    for x in range(300, 410):
                        i = pix[y * 512 + x]
                        if i >= 16:
                            e = pal[i * 4:i * 4 + 4]
                            cols.add((e[2], e[1], e[0]))
                full = max(cols, key=lambda c: sum(abs(a - b) for a, b in zip(c, (0xef, 0xee, 0xe8))))
                self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(full, rgb)), (tag, full, rgb))


APP_HARNESS = r"""
#include "app.h"
#include <stdio.h>
static App g_app;
int main(void) {
    App *a = &g_app;
    a->ngames = 1; a->games[0].installed = 1; a->games[0].s = SETTINGS_DEFAULT;
    a->tab = TAB_GAMES; a->detail = 1; a->row = 1;
    Settings *s = &a->games[0].s;
    printf("preset");
    for (int i = 0; i < 4; i++) { app_input(a, PAD_RIGHT); printf(" %d", s->preset); }
    printf("\nback");
    for (int i = 0; i < 3; i++) { app_input(a, PAD_LEFT); printf(" %d", s->preset); }
    printf("\n");
    s->preset = 1; a->row = 4; app_input(a, PAD_DOWN); printf("fast down from 4 -> %d\n", a->row);
    app_input(a, PAD_UP); printf("fast up from 6 -> %d\n", a->row);
    s->preset = 2; a->row = 4; app_input(a, PAD_DOWN); printf("sharp down from 4 -> %d\n", a->row);
    s->preset = 0; s->fg = 0; a->row = 0; app_input(a, PAD_DOWN); printf("fg off down from 0 -> %d\n", a->row);
    a->row = 4; app_input(a, PAD_DOWN); printf("fg off down from 4 -> %d\n", a->row);
    s->fg = 1; a->games[0].dev.manual_mode = 1; a->games[0].dev.fps_lock = 1; a->row = 4; app_input(a, PAD_DOWN);
    printf("lock20 down from 4 -> %d\n", a->row);
    a->games[0].dev.manual_mode = 0; a->games[0].dev.fps_lock = 0;
    a->tab = TAB_DEFAULTS; a->defaults = SETTINGS_DEFAULT; a->defaults.preset = 1; a->row = 4; app_input(a, PAD_DOWN);
    printf("defaults fast down from 4 -> %d\n", a->row);
    return 0;
}
"""


@unittest.skipUnless(CLANG, "no host clang")
class TestApp(unittest.TestCase):
    def test_sharp_and_hidden_rows(self):
        app = (ROOT / "launcher" / "src" / "app.c").read_text()
        self.assertIn('static const char *mode_name(int p) { return p == 1 ? "FAST" : p == 2 ? "SHARP" : "QUALITY"; }', app)
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            (t / "h.c").write_text(APP_HARNESS)
            r = subprocess.run([CLANG, "-O0", "-w", "-fno-builtin", "-I", str(ROOT / "launcher" / "src"), "-I", str(ROOT / "overlay"),
                                str(t / "h.c"), str(ROOT / "launcher" / "src" / "app.c"), str(ROOT / "launcher" / "src" / "ui.c"),
                                "-o", str(t / "h"), "-lm"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()
        self.assertIn("preset 1 2 0 1", out)        # QUALITY -> FAST -> SHARP -> QUALITY
        self.assertIn("back 0 2 1", out)
        self.assertIn("fast down from 4 -> 6", out)   # Below 30 FPS (5) skipped with FAST
        self.assertIn("fast up from 6 -> 4", out)
        self.assertIn("sharp down from 4 -> 5", out)
        self.assertIn("fg off down from 0 -> 2", out)  # Mode skipped with FG off
        self.assertIn("fg off down from 4 -> 7", out)  # Below 30 FPS and Low latency skipped with FG off
        self.assertIn("lock20 down from 4 -> 7", out)  # CUSTOM 20 FPS lock: neither applies
        self.assertIn("defaults fast down from 4 -> 6", out)


if __name__ == "__main__":
    unittest.main()
