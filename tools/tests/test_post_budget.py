#!/usr/bin/env python3
"""Post-process budget (post_budget.h, option post_budget): Enhance AA and Sharpness step down while real frames come
late and come back when they stop.

Replays the STAT windows of the console runs of 30/10 (tools/tests/fixtures/post_budget/, the lines the decision reads,
verbatim, plus the phases of the motion protocol) and of the advisor traces through pb_window:

1. CUSA02299, Sharpness 70 + Enhance AA Strong: the first step down comes in the camera spin of every run whose spin
   had late real frames (spin runs r1 and r2, protocol run r1); protocol run r3 steps down in its movement phase,
   the only phase it was late in; protocol run r2 had no late window and never steps down.
2. Never a step down standing still, idle, with Sharpness 0 + AA off (CUSA02299 spins, CUSA18723 with late windows of
   its own at 26-28 fps), on the advisor traces of CUSA07737 and CUSA18723 (below-30 regime), nor during the game's
   boot (grace) or around a stall longer than n + 2.5 refreshes.
3. No oscillation: replayed as recorded (lateness kept after the step, a pessimistic model) and with the step working
   (late windows after the step read as the Sharpness 0 runs did, 35 ms), at most one step per direction in each run,
   and on synthetic 20-min sequences the level changes at most once per hold, the hold doubles on a quick relapse,
   and a lateness that is not ours (late at every level) returns to level 0 and locks.
4. Level 0 is the old calc_rcas_params bit for bit; level 2 is no rcas8 pass. Level 1 (Enhance AA off) keeps its
   definition but is never entered: in the c1b spin it took rcas8 only from 4.60 to 4.06 ms.
5. Late with nothing to cede (CUSA18723, Sharpness 0, AA off, below30 1: our warp/post 1.2 -> 3.3 ms in the spin,
   27 real fps against 29.1 with framegen=0): the level never moves, the detector says it once per episode in the
   spin (PB_NOPOST, "nothing to cede", with the warp/post time); with framegen=0 nothing at all. CUSA02299 at
   Sharpness 70 never takes that branch (it has rcas8 to cede).
6. Loading and transitions never step (a version without these guards stepped 0 -> 1 -> 2 at 137-141 s on CUSA02299's tutorial-to-loading
   transition, max dt 49.9-50.2 ms; the ring kept only 156-190 s of that run): a window with a scene cut or a fade,
   with real frames not generated or with too few flips does not count, a late window without motion is neither
   late nor clean, and two changes are at least 2 windows apart.
7. The option post_budget comes back to its default (on) on every load without the key, and the menu save writes it.
8. main.c: the option is read from the config, default on, the level goes through calc_rcas_params (every rcas8
   dispatch, generated and real), the decision runs once per STAT window, and every change is one "post budget:" line.

Same compiler choice as test_pace_policy.py (config.json llvm_bin, otherwise the host clang/cc).
"""
import pathlib
import re
import unittest

from test_pace_policy import MAIN_C, compile_and_run, extract_function

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "tools" / "tests" / "fixtures" / "post_budget"
ADV = ROOT / "tools" / "tests" / "fixtures" / "advisor_traces"
PB_H = (ROOT / "classic" / "source" / "post_budget.h").read_text(encoding="utf-8")
VB_US = 16683                     # 59.94 Hz, the TV of these runs
ENH = {"off": 0, "light": 1, "strong": 2}


def windows(path):
    """One dict per STAT window, with the inputs pb_window gets on the console."""
    phases, out = [], []
    sharp = enh = 0
    preset, fg_active, b30 = 0, False, False
    rcas, warp = 0.0, 0.0
    frames = []                   # (end time, motion, cut or fade) of each FRAMES line, 90 real frames = ~3 s
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# phase "):
            _, _, n, a, b = line.split()
            phases.append((n, float(a), float(b)))
            continue
        m = re.match(r"\s*(\d+\.\d+) (.*)", line)
        if not m:
            continue
        t, s = float(m.group(1)), m.group(2)
        if s.startswith("config ("):
            g = re.search(r"preset=(\d) sharpness=(\d+) enhance=(\d)", s)
            preset, sharp, enh = int(g.group(1)), int(g.group(2)), int(g.group(3))
        elif s.startswith("options effective:"):
            fg_active = "framegen=on(active)" in s
            b30 = "below30=on(active" in s
            g = re.search(r" sharpness=(\d+)", s)
            if g:
                sharp = int(g.group(1))
            g = re.search(r" enhance=(off|light|strong)", s)
            if g:
                enh = ENH[g.group(1)]
        elif s.startswith("FRAMES 90:"):
            g = re.search(r"unmatched blocks avg (\d+) max \d+, scene-cut frames (\d+), fade frames (\d+) \| foreground: in (\d+) frames", s)
            if g:
                unm, cut, fade, fg = (int(v) for v in g.groups())
                frames.append((t, int(unm >= 20 or fg * 10 >= 90 * 3), int(cut > 0 or fade > 0)))
        elif s.startswith("GPU time of our passes: pyramid"):
            g = re.search(r"warp/post ([\d.]+) =", s)
            warp = float(g.group(1)) if g else 0.0
        elif s.startswith("PASSES split ms:"):
            g = re.search(r"\| rcas8 ([\d.]+) rest", s)
            rcas = float(g.group(1)) if g else 0.0
        elif s.startswith("STAT gnm:"):
            g = re.search(r"([\d.]+) flips/s, max dt ([\d.]+) ms \(window ([\d.]+)s\)", s)
            fps, mx, secs = float(g.group(1)), float(g.group(2)), float(g.group(3))
            ph = next((n for n, a, b in phases if a <= t < b), "")
            out.append(dict(t=t, n=int(round(fps * secs)), us=int(secs * 1e6), dt=int(mx * 1000), cost=int(round(rcas * 100)),
                            warp=int(round(warp * 100)), sharp=sharp, enh=enh, diag=int(fg_active),
                            active=int(fg_active and preset != 1 and not b30), phase=ph, dist=0, motion=1))
    # The plugin counts the window's own frames; the trace has one FRAMES line per ~3 s. A window takes the lines whose
    # 3 s overlap it (motion if any has motion, disturbed if any had a cut or fade); with no line (the ring dropped it),
    # motion stays unknown (1) and the window undisturbed.
    for w in out:
        ov = [f for f in frames if f[0] - 3.0 < w["t"] and f[0] > w["t"] - w["us"] / 1e6]
        if ov:
            w["motion"] = int(any(f[1] for f in ov))
            w["dist"] = int(any(f[2] for f in ov))
    return out


HARNESS = r"""
#include <stdio.h>
#include "post_budget.h"
typedef struct { unsigned n, us, dt, cost, warp; int sharp, enh, diag, active, dist, motion; } W;
static const W w[] = { __ROWS__ };
int main(void)
{
    /* mode 0: as recorded (lateness kept after a step; rcas8 at level 1 modelled as 20 % of level 0, 0 at level 2)
       mode 1: the step works (a late window after a step reads 35 ms, as the Sharpness 0 runs did) */
    for (int mode = 0; mode < 2; mode++) {
        PostBudget s; pb_reset(&s);
        for (unsigned i = 0; i < sizeof(w) / sizeof(w[0]); i++) {
            PbIn in = { 1, w[i].diag, w[i].active, w[i].n, w[i].us, w[i].dt, __VB__, w[i].cost, w[i].warp, w[i].sharp, w[i].enh,
                        w[i].dist, w[i].motion };
            if (s.level == 1) in.cost_x100 = w[i].cost / 5u;
            if (s.level >= 2) in.cost_x100 = 0;
            if (mode == 1 && s.level > 0 && in.maxdt_us > 41700u && in.maxdt_us <= 75100u) in.maxdt_us = 35000u;
            PbEvt e;
            if (pb_window(&s, &in, &e)) printf("T %d %u %d %d %d %d %u\n", mode, i, e.from, e.to, e.why, e.sub, e.warp_x100);
        }
        printf("END %d %d\n", mode, s.level);
    }
    return 0;
}
"""


def replay(ws):
    rows = ",\n".join("{%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d}" % (x["n"], x["us"], x["dt"], x["cost"], x["warp"], x["sharp"], x["enh"],
                                                             x["diag"], x["active"], x["dist"], x["motion"]) for x in ws)
    out = compile_and_run(HARNESS.replace("__ROWS__", rows).replace("__VB__", str(VB_US)))
    tr = {0: [], 1: []}
    for line in out.splitlines():
        f = line.split()
        if f[0] == "T":
            tr[int(f[1])].append(dict(i=int(f[2]), frm=int(f[3]), to=int(f[4]), why=int(f[5]), sub=int(f[6]), warp=int(f[7])))
    return tr


PB_LATE, PB_CLEAN, PB_NOGAIN, PB_INACTIVE, PB_SETTINGS, PB_NOPOST = 1, 2, 3, 4, 5, 6
PB_NOPOST_NOTHING = 1


def changes(t):
    return [x for x in t if x["why"] != PB_NOPOST]


class ConsoleRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {p.stem: windows(p) for p in sorted(FIX.glob("*.txt"))}
        for name in ("trace-CUSA07737-20261030", "trace-CUSA18723-20261030"):
            cls.runs[name] = windows(ADV / (name + ".txt"))
        # earlier CUSA02299 session of 30/10: the ring lost the settings lines, but rcas8 4.4 ms from t 1191 s means the
        # post-process was on (the title's profile of that day: Sharpness 70 + Enhance AA Strong); real frames 47-57 ms in
        # 13 of 16 windows. The ring dropped the ~580 windows between t 33 s and 1189 s: 16 clean windows at those
        # settings stand in for them, so the replay starts past the grace as the console was
        early = [x for x in windows(ADV / "trace-CUSA02299-20261030.txt") if x["t"] >= 1189.0]
        gap = [dict(early[0], t=1100.0 + 2 * k, dt=34700, cost=440) for k in range(16)]
        for x in gap + early:
            x["sharp"], x["enh"] = 70, 2
        cls.runs["trace-CUSA02299-20261030-s70aa2"] = gap + early
        cls.tr = {k: replay(v) for k, v in cls.runs.items()}

    def first_down(self, name, mode=0):
        t = [x for x in self.tr[name][mode] if x["why"] == PB_LATE]
        return t[0] if t else None

    def test_fixtures_are_the_runs(self):
        ws = self.runs["cusa02299-spin-s70aa2-r1"]
        spin = [x for x in ws if x["phase"] == "spin"]
        self.assertGreaterEqual(len(spin), 10)
        self.assertEqual(sum(x["dt"] > 41700 for x in spin), 4)                 # 50.8 49.8 50.3 49.9 ms
        self.assertTrue(all(x["sharp"] == 70 and x["enh"] == 2 and x["cost"] >= 440 for x in spin))
        s0 = [x for x in self.runs["cusa02299-spin-s0aa0-r1"] if x["phase"] == "spin"]
        self.assertTrue(s0 and all(x["sharp"] == 0 and x["enh"] == 0 and x["dt"] < 36500 for x in s0))
        er = [x for x in self.runs["cusa18723-protocol-s0aa0"] if x["active"]]
        self.assertGreaterEqual(sum(41700 < x["dt"] <= 75100 for x in er), 3)    # late windows of its own, no post-process

    def test_steps_down_in_the_spin_with_sharpness_70(self):
        for name, phase in (("cusa02299-spin-s70aa2-r1", "spin"), ("cusa02299-spin-s70aa2-r2", "spin"),
                            ("cusa02299-protocol-s70aa2-r3", "move"), ("cusa02299-c1b-pb1", "spin"),
                            ("cusa02299-c1v-s70aa2", "spin")):
            for mode in (0, 1):
                d = self.first_down(name, mode)
                self.assertIsNotNone(d, name)
                self.assertEqual((d["frm"], d["to"]), (0, 2), name)               # straight to no rcas8 pass
                self.assertEqual(self.runs[name][d["i"]]["phase"], phase, name)
        # protocol run r4 was late before its protocol started (t 146-158 s, Sharpness 70): it steps down there
        d = self.first_down("cusa02299-protocol-s70aa2-r4")
        self.assertIsNotNone(d)
        self.assertLess(self.runs["cusa02299-protocol-s70aa2-r4"][d["i"]]["t"], 160.0)

    def test_sustained_lateness_with_post_process(self):
        name = "trace-CUSA02299-20261030-s70aa2"
        ws = self.runs[name]
        t1 = changes(self.tr[name][1])     # the step works: post-process off once; a scene cut 10 windows later restarts the hold
        self.assertEqual([(x["frm"], x["to"], x["why"]) for x in t1], [(0, 2, PB_LATE)])
        self.assertLess(ws[t1[0]["i"]]["t"], 1196.0)
        t0 = changes(self.tr[name][0])                         # lateness kept without post-process: no gain, back to 0
        self.assertEqual([(x["frm"], x["to"], x["why"]) for x in t0], [(0, 2, PB_LATE), (2, 0, PB_NOGAIN)])
        self.assertGreaterEqual(t0[1]["i"] - t0[0]["i"], 8)

    def test_never_steps_down_still_idle_sharpness_0_or_other_titles(self):
        # protocol run r1: its two 66.9-ms windows in the spin phase are Share screenshots with no motion in the frames
        # (unmatched avg <= 12, foreground <= 9 of 90): neither late nor clean
        for name in ("cusa02299-protocol-s70aa2-r1", "cusa02299-c1-pb0-r1", "cusa02299-c1-pb1-r1", "cusa02299-c1v-s0aa0",
                     "cusa02299-protocol-s70aa2-r2", "cusa02299-still-ab", "cusa02299-idle", "cusa02299-spin-s0aa0-r1",
                     "cusa02299-spin-s0aa0-r2", "cusa18723-protocol-s0aa0", "trace-CUSA07737-20261030",
                     "trace-CUSA18723-20261030", "cusa18723-protocol-s0aa0-r2", "cusa18723-protocol-fg0"):
            for mode in (0, 1):
                self.assertEqual(changes(self.tr[name][mode]), [], name)
        for name in ("cusa02299-protocol-s70aa2-r2", "cusa02299-still-ab", "cusa02299-idle", "cusa02299-spin-s0aa0-r1",
                     "cusa02299-spin-s0aa0-r2", "cusa18723-protocol-fg0"):
            self.assertEqual(self.tr[name][0], [], name)                     # not even a report

    def test_late_with_nothing_to_cede_is_reported_not_stepped(self):
        for name in ("cusa18723-protocol-s0aa0-r2", "cusa18723-protocol-s0aa0"):
            ws, t = self.runs[name], self.tr[name][0]
            spin = [x for x in ws if x["phase"] == "spin" and x["active"] or x["phase"] == "spin" and x["diag"]]
            self.assertTrue(spin and all(x["sharp"] == 0 and x["enh"] == 0 and x["cost"] == 0 for x in spin), name)
            self.assertGreaterEqual(max(x["warp"] for x in spin), 300, name)             # warp/post 3.3 ms in the spin
            self.assertLessEqual(max(x["warp"] for x in ws if x["phase"] == "still0"), 140, name)
            self.assertTrue(t, name)
            self.assertTrue(all(x["why"] == PB_NOPOST and x["sub"] == PB_NOPOST_NOTHING and x["frm"] == x["to"] == 0
                                for x in t), name)
            # late windows standing still (run r1: 42-44 ms before the spin) have no motion: the report comes in the spin
            self.assertEqual(ws[t[0]["i"]]["phase"], "spin", name)
            self.assertLessEqual(len(t), 3, name)                                 # once per episode, not per window
        # CUSA07737 at 22 fps (no post-process): late windows reported, never a step
        self.assertTrue(all(x["why"] == PB_NOPOST for x in self.tr["trace-CUSA07737-20261030"][0]))
        # framegen=0 in the same scene: 29.1 fps, max dt <= 40.8 ms, no frames generated -> the detector is off
        fg0 = self.runs["cusa18723-protocol-fg0"]
        self.assertTrue(all(not x["diag"] for x in fg0 if x["phase"]))
        # CUSA02299 at Sharpness 70 has rcas8 to cede: the first event of a spin run is a step, never the report
        for name in ("cusa02299-spin-s70aa2-r1", "cusa02299-spin-s70aa2-r2", "cusa02299-c1b-pb1"):
            self.assertEqual(self.tr[name][0][0]["why"], PB_LATE, name)

    def test_console_run_of_the_two_step_version(self):
        # the spin of c1b (a version with level 1 and without the fade guard, on the console): 0 -> 1 at 1430.6 s,
        # 1 -> 2 at 1444.2 s after a 50.1-ms window at 1434.6 s whose frames had a fade; now one step,
        # straight to level 2 (level 1 saved only 0.55 ms of rcas8)
        name = "cusa02299-c1b-pb1"
        src = (FIX / (name + ".txt")).read_text(encoding="utf-8")
        self.assertIn("1430.62 post budget: level 0 -> 1", src)
        self.assertIn("1444.18 post budget: level 1 -> 2", src)
        t = changes(self.tr[name][0])
        self.assertEqual((t[0]["frm"], t[0]["to"], t[0]["why"]), (0, 2, PB_LATE))
        self.assertEqual(sum(x["why"] == PB_LATE for x in t), 1)
        self.assertIn("rcas8+osd 4.60 pcopy8", src)                                                     # level 0 in the spin
        self.assertIn("rcas8+osd 4.06 pcopy8", src)                                                     # level 1, same spin
        # c1-pb0 r1 kept 156-190 s of the run that stepped at 137 and 141 s (the ring dropped 28-156 s): nothing there
        ws = self.runs["cusa02299-c1-pb0-r1"]
        self.assertTrue(any(156.0 <= x["t"] <= 190.0 for x in ws))
        self.assertIn("189.19 post budget: level 2 -> 0", (FIX / "cusa02299-c1-pb0-r1.txt").read_text(encoding="utf-8"))
        self.assertEqual(changes(self.tr["cusa02299-c1-pb0-r1"][0]), [])

    def test_boot_and_long_stalls_are_ignored(self):
        # every run starts with the game's boot: late windows of 44-133 ms with Sharpness 70 inside the grace
        ws = self.runs["cusa02299-spin-s70aa2-r1"]
        boot = [i for i, x in enumerate(ws[:12]) if x["dt"] > 41700 and x["active"]]
        self.assertGreaterEqual(len(boot), 3)
        self.assertTrue(all(x["i"] > 12 for x in self.tr["cusa02299-spin-s70aa2-r1"][0]))
        # idle run: 63.4 ms, a 322 ms stall, 48.8 ms, then 66.7 ms three windows later: the stall clears the history
        idle = self.runs["cusa02299-idle"]
        self.assertTrue(any(x["dt"] > 300000 for x in idle))

    def test_no_oscillation_on_the_runs(self):
        for name, tr in self.tr.items():
            for mode in (0, 1):
                t = changes(tr[mode])
                downs = [x for x in t if x["why"] == PB_LATE]
                ups = [x for x in t if x["why"] in (PB_CLEAN, PB_NOGAIN)]
                self.assertLessEqual(len(downs), 2, name)
                self.assertLessEqual(len(ups), 2, name)
                for a, b in zip(t, t[1:]):
                    self.assertGreaterEqual(b["i"] - a["i"], 2, name)
                    if a["why"] == PB_CLEAN:                                    # after a step up, never back within 2 windows
                        self.assertGreaterEqual(b["i"] - a["i"], 2, name)
        # with the step working, the spin runs go 0 -> 1 once and back to 0 once, after the 30-s hold
        for name in ("cusa02299-spin-s70aa2-r1", "cusa02299-spin-s70aa2-r2"):
            t = changes(self.tr[name][1])
            self.assertEqual([(x["frm"], x["to"], x["why"]) for x in t], [(0, 2, PB_LATE), (2, 0, PB_CLEAN)], name)
            self.assertGreaterEqual(t[1]["i"] - t[0]["i"], 15, name)


SYNTH = r"""
#include <stdio.h>
#include "post_budget.h"
static unsigned lcg = 7u;
static unsigned rnd(void) { lcg = lcg * 1103515245u + 12345u; return (lcg >> 8) & 0xFFFFu; }
int main(void)
{
    /* 0: heavy scene for 20 min, late in 1 of 2 windows at level 0, never late at level >= 1
       1: lateness that is not ours: late in 3 of 4 windows at every level, 40 min
       2: GPU with headroom, 20 min, an isolated 50-ms frame in 1 of 100 windows (twice the still run: 1 of 216) and a
          300-ms stall in 1 of 100 */
    for (int sc = 0; sc < 3; sc++) {
        PostBudget s; pb_reset(&s);
        unsigned nwin = sc == 1 ? 1200u : 600u;
        for (unsigned i = 0; i < nwin; i++) {
            unsigned dt = 34700u, r = rnd() % 100u;
            if (sc == 0 && s.level == 0 && (r & 1u)) dt = 50100u;
            if (sc == 1 && r < 75u) dt = 50100u;
            if (sc == 2 && r == 0u) dt = 50100u;
            if (sc == 2 && r == 99u) dt = 300000u;
            unsigned cost = s.level == 0 ? 460u : (s.level == 1 ? 90u : 0u);
            PbIn in = { 1, 1, 1, 60u, 2000000u, dt, 16683u, cost, 300u, 70, 2, 0, 1 };
            PbEvt e;
            if (pb_window(&s, &in, &e)) printf("T %d %u %d %d %d %u %u\n", sc, i, e.from, e.to, e.why, e.hold, e.lock);
        }
    }
    return 0;
}
"""


class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = {0: [], 1: [], 2: []}
        for line in compile_and_run(SYNTH).splitlines():
            f = line.split()
            cls.t[int(f[1])].append(dict(i=int(f[2]), frm=int(f[3]), to=int(f[4]), why=int(f[5]), hold=int(f[6]), lock=int(f[7])))

    def test_heavy_scene_backs_off(self):
        t = self.t[0]
        self.assertEqual(t[0]["why"], PB_LATE)
        ups = [x for x in t if x["why"] == PB_CLEAN]
        # the hold doubles on each quick relapse: 15, 30, 60, 120, 120 windows at level 1 before each step up
        downs = [x for x in t if x["why"] == PB_LATE]
        stays = [u["i"] - d["i"] for d, u in zip(downs, ups)]
        self.assertEqual(stays[:4], [15, 30, 60, 120])
        self.assertTrue(all(s == 120 for s in stays[4:]))
        self.assertLessEqual(len(t), 16)                       # 600 windows (20 min): at most 8 down/up pairs
        self.assertTrue(all(x["to"] in (0, 2) for x in t))     # level 1 is never entered

    def test_lateness_not_ours_locks(self):
        t = self.t[1]
        self.assertEqual([(x["frm"], x["to"], x["why"]) for x in t[:2]], [(0, 2, PB_LATE), (2, 0, PB_NOGAIN)])
        locks = [x["lock"] for x in t if x["why"] == PB_NOGAIN]
        self.assertEqual(locks[:3], [150, 300, 600])
        self.assertLessEqual(len(t), 12)                       # 40 min: 4 tries at most

    def test_headroom_never_steps(self):
        self.assertEqual(self.t[2], [])


LOAD = r"""
#include <stdio.h>
#include "post_budget.h"
int main(void)
{
    /* 0: 20 windows past the grace, then a transition like 137-141 s of the c1 run: 50-ms windows with a fade or a
          scene cut, then without motion (loading screen), then with few flips, then clean
       1: the same 50-ms windows with motion and nothing else: a real spin
       2: late with motion in every window: changes at least 3 windows apart */
    for (int sc = 0; sc < 3; sc++) {
        PostBudget s; pb_reset(&s);
        for (unsigned i = 0; i < 60u; i++) {
            unsigned dt = 34700u, n = 60u, cost = s.level == 0 ? 460u : (s.level == 1 ? 400u : 0u);
            int dist = 0, mot = 1;
            if (sc == 0 && i >= 20u && i < 26u) { dt = 50100u; dist = 1; }
            if (sc == 0 && i >= 26u && i < 32u) { dt = 50000u; mot = 0; }
            if (sc == 0 && i >= 32u && i < 36u) { dt = 49900u; n = 40u; }
            if (sc == 1 && i >= 20u && i < 26u) dt = 50100u;
            if (sc == 2 && i >= 20u) dt = 50100u;
            PbIn in = { 1, 1, 1, n, 2000000u, dt, 16683u, cost, 600u, 70, 2, dist, mot };
            PbEvt e;
            if (pb_window(&s, &in, &e)) printf("T %d %u %d %d %d\n", sc, i, e.from, e.to, e.why);
        }
    }
    return 0;
}
"""


class LoadingAndDwell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = {0: [], 1: [], 2: []}
        for line in compile_and_run(LOAD).splitlines():
            f = line.split()
            cls.t[int(f[1])].append(dict(i=int(f[2]), frm=int(f[3]), to=int(f[4]), why=int(f[5])))

    def test_transition_and_loading_never_step(self):
        self.assertEqual(self.t[0], [])

    def test_the_same_lateness_in_motion_steps(self):
        self.assertEqual((self.t[1][0]["frm"], self.t[1][0]["to"], self.t[1][0]["why"]), (0, 2, PB_LATE))

    def test_two_windows_between_changes(self):
        t = changes(self.t[2])
        self.assertGreaterEqual(len(t), 2)
        for a, b in zip(t, t[1:]):
            self.assertGreaterEqual(b["i"] - a["i"], 3)


class ConfigDefault(unittest.TestCase):
    """post_budget = 0 in one file, then a file without the key: the option is back on; the menu save writes it."""

    def test_default_on_every_load_and_saved(self):
        import re as _re
        from test_menu_app_settings import MAIN as M, build_run, function
        struct = _re.search(r"typedef struct \{.*?\} fg_config_t;", M, _re.S)[0]
        fam = (ROOT / "overlay" / "title_families.h").read_text().replace("#pragma once", "")
        combo = (ROOT / "overlay" / "menu_combo.h").read_text().replace("#pragma once", "")
        funcs = "\n".join(function(M, n) for n in ("fnv1a_bytes", "cfg_apply_title_defaults", "cfg_defaults",
                                                   "parse_ini_file_ex", "parse_ini_file", "cfg_save_game_ini"))
        src = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
""" + combo + fam + r"""
static char g_dir[512];
#define LOG_DIR "%s"
static int g_known_game, g_detected_60fps, g_tile_override = -1, g_tile_sweep, g_t6_bit_override = -1, g_adapt_early_crashes = 0, g_post_budget = 1;
static int g_is_neo = 0;
static char g_title_id[16] = "CUSA02299";
static char g_family_id[16] = "CUSA02299";
static char g_osd_toast[32]; static uint64_t g_osd_toast_until; static uint32_t g_last_disk_hash;
static void tile_load(int idx) { (void)idx; }
static void lg(const char *f, ...) { (void)f; }
static uint64_t sceKernelGetProcessTime(void) { return 0; }
static int sceKernelMkdir(const char *p, int m) { (void)p; (void)m; return 0; }
static FILE *g_rf;
static int sceKernelOpen(const char *p, int f, int m) { (void)f; (void)m; g_rf = fopen(p, "rb"); return g_rf ? 3 : -1; }
static long sceKernelRead(int fd, void *b, size_t n) { (void)fd; return (long)fread(b, 1, n, g_rf); }
static int sceKernelClose(int fd) { (void)fd; fclose(g_rf); return 0; }
""" + struct + r"""
static fg_config_t g_cfg;
static void cfg_reload_from_disk(int force) { (void)force; }
""" + funcs.replace('LOG_DIR "/config-%s.ini", g_title_id', 'LOG_DIR "/config-%s.ini", g_dir, g_title_id') + r"""
static void put(const char *path, const char *txt) { FILE *f = fopen(path, "w"); fputs(txt, f); fclose(f); }
int main(int argc, char **argv) {
    snprintf(g_dir, sizeof(g_dir), "%s", argv[1]);
    char path[600]; snprintf(path, sizeof(path), "%s/config-CUSA02299.ini", g_dir);
    fg_config_t c;
    put(path, "[bb_probe]\nframegen = 1\nmode = 8\npreset = 2\nsharpness = 70\nupscale = 2\npost_budget = 0\n");
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("off %d\n", g_post_budget);
    put(path, "[bb_probe]\nframegen = 1\nmode = 8\npreset = 2\nsharpness = 70\nupscale = 2\n");
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("absent %d\n", g_post_budget);
    put(path, "[bb_probe]\nframegen = 1\nmode = 8\npreset = 2\nsharpness = 70\nupscale = 2\npost_budget = 0\n");
    cfg_defaults(&c); parse_ini_file(path, &c, 0);
    g_cfg = c; cfg_save_game_ini();
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("saved %d\n", g_post_budget);
    return 0;
}
"""
        r = build_run(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout.splitlines()
        self.assertIn("off 0", out)
        self.assertIn("absent 1", out)        # without the reset in cfg_defaults: "absent 0", the option stayed off until the game restarted
        self.assertIn("saved 0", out)         # the menu save keeps the player's choice


LEVEL_HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "post_budget.h"
typedef struct { int sharpness, upscale; } fg_config_t;
static fg_config_t g_cfg;
static uint32_t g_sh8_off[17];
static int g_pb_level;
/* calc_rcas_params before the budget, verbatim */
static void old_params(uint32_t target_mask, uint32_t *con_bits_out, uint32_t *flags_out)
{
    uint32_t rcas_con_bits = 0, rcas_flags = 0;
    int sharp_pct = g_cfg.sharpness;
    int enh_mode = g_cfg.upscale;
    if (enh_mode < 0) enh_mode = 0;
    if (enh_mode > 2) enh_mode = 2;
    if ((sharp_pct > 0 || enh_mode > 0) && g_sh8_off[14]) {
        if (sharp_pct > 100) sharp_pct = 100;
        if (sharp_pct > 0) {
            float stops = (float)(100 - sharp_pct) * 0.02f;
            float con = 0.25f / (1.0f + stops * (0.693147f + stops * 0.240226f));
            if (sharp_pct < 25) con *= (float)sharp_pct * 0.04f;
            memcpy(&rcas_con_bits, &con, 4);
        }
        rcas_flags = (target_mask & 3u) | ((uint32_t)enh_mode << 2);
    }
    *con_bits_out = rcas_con_bits;
    *flags_out = rcas_flags;
}
__FN__
int main(void)
{
    unsigned bad[3] = { 0, 0, 0 }, n = 0;
    for (int off = 0; off < 2; off++) for (int sh = -5; sh <= 105; sh++) for (int en = -1; en <= 3; en++)
    for (unsigned m = 0; m < 4; m++) for (int lv = 0; lv < 3; lv++) {
        g_sh8_off[14] = off ? 0x100u : 0u;
        uint32_t c0, f0, c1, f1;
        g_cfg.sharpness = sh; g_cfg.upscale = en; g_pb_level = lv;
        calc_rcas_params(m, &c1, &f1);
        g_cfg.sharpness = lv >= 2 ? 0 : sh; g_cfg.upscale = lv >= 1 ? 0 : en;
        old_params(m, &c0, &f0);
        if (c0 != c1 || f0 != f1) bad[lv]++;
        n++;
    }
    printf("n %u bad0 %u bad1 %u bad2 %u\n", n, bad[0], bad[1], bad[2]);
    return 0;
}
"""


class LevelsAndWiring(unittest.TestCase):
    def test_level_0_is_the_old_function(self):
        fn = extract_function(MAIN_C, "static void calc_rcas_params(")
        out = compile_and_run(LEVEL_HARNESS.replace("__FN__", fn))
        self.assertIn("bad0 0 bad1 0 bad2 0", out)
        self.assertIn("pb_eff(__atomic_load_n(&g_pb_level, __ATOMIC_ACQUIRE), sharp_pct, enh_mode, &sharp_pct, &enh_mode);", fn)

    def test_main_wiring(self):
        self.assertIn('#include "post_budget.h"', MAIN_C)
        self.assertIn("static volatile int g_post_budget = 1;", MAIN_C)
        self.assertIn('else if (strcmp(key, "post_budget") == 0) g_post_budget = (val != 0);', MAIN_C)
        stat = MAIN_C.index('lgd("STAT gnm: %.1f flips/s, max dt %.1f ms (window %.1fs)\\n"')
        self.assertIn("post_budget_window(secs);", MAIN_C[stat:stat + 200])
        fn = extract_function(MAIN_C, "static void post_budget_window(")
        self.assertIn("in.diag = g_inject_on == 8 && __atomic_load_n(&g_st_fg_on, __ATOMIC_RELAXED) && !g_menu_open", fn)
        self.assertEqual(fn.count('lg("post budget: level %d -> %d (%s)'), 4)     # one line per change, with the reason
        self.assertIn('lg("post budget: real frame late in %u of the last 3 windows (max dt %.1f ms > %.1f), nothing to cede', fn)
        self.assertIn("in.active = in.diag && g_cfg.preset != 1 && !g_b30.on;", fn)
        self.assertIn("__atomic_store_n(&g_pb_level, e.to, __ATOMIC_RELEASE);", fn)
        # the published rcas8 time is the 1st mid's rcas8 of the GPU timing line
        self.assertIn("double rc = g_pp_split_n ? (double)g_pp_split[3] / g_pp_split_n / unit * 1000.0 : 0.0;", MAIN_C)
        # every rcas8 dispatch of mode 8 takes its parameters from calc_rcas_params
        self.assertEqual(MAIN_C.count("calc_rcas_params("), 4)


if __name__ == "__main__":
    unittest.main()
