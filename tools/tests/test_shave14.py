#!/usr/bin/env python3
"""shave14: the 2nd generated-frame buffer from a 16 MiB display reserve (with below30, by default for the CUSA18723
family, opt-in by LOG_DIR/shave14_<TITLE> for other titles).

1. Boot replay of the CUSA18723 PS4 base 1080p boot (the v2.34 console boot log: 12 game allocations, then the game's
   RegisterBuffers of 3) through the real valve, Arena Shaving, flexible-tail mapping and history-buffer code of
   classic/source/debug_hooks.h and main.c, with the test_real_traces.py harness compiled on the host:
   - without shave14: as today (8 MiB reserve, arena shaved by 0x600000, one generated-frame buffer, 8 MiB history
     buffer in FlexibleMemory);
   - with shave14: 16 MiB reserve, arena shaved by 0xE00000, two generated-frame buffers (g_nmid 2, extended to 5), no
     history buffer, and the FlexibleMemory left at the end equal to the run without shave14; every game allocation and
     map succeeds and the free DirectMemory is the same;
   - with shave14 but less FlexibleMemory than tail + S14_FLEX_FLOOR: exactly the run without shave14.
2. s14_decide compiled with stubs and the real title family table: the CUSA18723 family is a candidate without the
   file once below30 is on, any other title only with the file; not a candidate means off and no log line; each
   disqualifying setting keeps it off.
3. main.c / debug_hooks.h call sites: the decision runs in plugin_load before the early display reserve, the boot valve
   step 3 keeps S14_KEEP, the shave budget falls back to step 5, every reserve shrink reports, RegisterBuffers reports
   in both registration paths, the below-30 regime treats the two shave14 buffers as not tight, and the BELOW30 line
   names the buffer.

Same compiler choice as test_pace_policy.py (config.json llvm_bin, otherwise the host clang/cc).
"""
import pathlib
import re
import unittest

from test_pace_policy import MAIN_C, ROOT, compile_and_run, extract_function

HOOKS_H = (ROOT / "classic" / "source" / "debug_hooks.h").read_text(encoding="utf-8", errors="replace")

# CUSA18723 v1.24, PS4 base, plugin 2.34, 1080p: artifacts ps4-runcq-20261027T1400/roundE-boot-CUSA18723.txt
# (s, e, len, align, type) in call order; the 11th is the 64 MiB arena the valve shaves.
BOOT_ALLOCS = [
    (0x0, 0x128000000, 0x2a00000, 0x200000, 0),
    (0x0, 0x128000000, 0x3be00000, 0x4000, 0),
    (0x0, 0x128000000, 0x24c00000, 0x4000, 0),
    (0x0, 0x128000000, 0x3600000, 0x4000, 0),
    (0x0, 0x128000000, 0x10600000, 0x4000, 0),
    (0x0, 0x128000000, 0x5400000, 0x10000, 0),
    (0x0, 0x128000000, 0x86e00000, 0x10000, 3),
    (0x0, 0x128000000, 0x20000000, 0x10000, 3),
    (0x0, 0x128000000, 0xa00000, 0x10000, 0),
    (0x0, 0x128000000, 0x4000000, 0x10000, 3),
    (0x0, 0x128000000, 0x20000, 0x10000, 3),
]


def _real_traces_ns() -> dict:
    """test_real_traces.py's harness strings, without its config.json requirement (that file only picks clang/lli)."""
    path = ROOT / "tools" / "tests" / "test_real_traces.py"
    src = path.read_text(encoding="utf-8")
    line = 'cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))'
    assert line in src
    ns = {"__name__": "test_real_traces_harness", "__file__": str(path)}
    exec(compile(src.replace(line, "cfg = {}"), str(path), "exec"), ns)
    return ns


def _replay_source() -> str:
    rt = _real_traces_ns()
    mbase = re.search(r"static int g_mbase\s*=\s*(\d+);", rt["MAIN_MBASE_DECLS"])
    harness = rt["HARNESS_C"].replace("__MAIN_MBASE_DECLS__", rt["MAIN_MBASE_DECLS"]).replace(
        "__INIT_G_MBASE__", mbase.group(1) if mbase else "3")
    replay = rt["REPLAY_HELPER_C"]
    for key in ("ON_OUR_BUF_LINE", "FG8_RESOLVE_FN", "FLIP_PCOPY_BLOCK", "REG_START0_RESET", "REG_FMT_BLOCK",
                "LATENCY_FG_EXPR", "REG_BUF_LOOP", "PCOPY_AVAIL_BLOCK", "REG_FALLBACK_BLOCK"):
        replay = replay.replace("__%s__" % key, rt[key])
    allocs = "\n".join(
        "        { long p = 0; void *va = NULL; int32_t r = sceKernelAllocateDirectMemory(0x%xLL, 0x%xLL, 0x%xULL, 0x%xULL, %d, &p);\n"
        "          ASSERT_MSG(r == 0, tag, %d, 0x%xULL);\n"
        "          int32_t mr = sceKernelMapDirectMemory(&va, 0x%xULL, 0x33, 0, p, 0x%xULL);\n"
        "          ASSERT_MSG(mr == 0 && va != NULL, tag, %d, 0x%xULL); }"
        % (s, e, n, a, t, i, n, n, a, i, n) for i, (s, e, n, a, t) in enumerate(BOOT_ALLOCS))
    main = r"""
static void run(const char *tag, int s14_on, size_t flex)
{
    apply_title_defaults_sim("CUSA18723");
    g_cfg.two_buffer = 1;                    /* the console profile: the history buffer is taken today */
    g_s14_on = s14_on; g_s14_state = 0; g_s14_tail_extra = 0; g_s14_flex_before = 0; g_s14_pbuf_logged = 0;
    setup_plugin_load_early(0x128000000ULL, 0x10000LL);
    g_sim_flex_avail = flex;
    g_flips = 0;
__ALLOCS__
    replay_register_buffers(0, 3, 1920u, 1080u, 1920u);
    size_t one = 0x800000u;
    s14_after_register(g_nbuf - g_mbase, one, (const uint8_t *)g_pre_mem, g_mbase + 1);
    size_t shaved = 0;
    for (int i = 0; i < g_shave_n; i++) shaved += g_shaves[i].orig_len - g_shaves[i].real_len;
    printf("%s shave_n=%d shaved=0x%zx pre=0x%zx nmid=%d mbase=%d nbuf=%d state=%d extra=0x%zx pbuf=0x%zx flex=0x%zx "
           "free64=0x%zx free2m=0x%zx fails=%u tail_mapped=%d\n",
           tag, g_shave_n, shaved, g_pre_size, g_nmid, g_mbase, g_nbuf, g_s14_state, g_s14_tail_extra,
           g_pbuf ? g_pbuf_sz : (size_t)0, g_sim_flex_avail, dm_avail(0x10000u), dm_avail(0x200000u),
           g_dbg_alloc_fail, g_shave_tail_mapped);
}
int main(void)
{
    run("off", 0, 0x4C00000u);               /* 70 MiB free at RegisterBuffers on the console + the 6 MiB tail */
    run("on", 1, 0x4C00000u);
    run("short_off", 0, 0x1500000u);         /* 21 MiB: < tail 14 MiB + floor 16 MiB */
    run("short_on", 1, 0x1500000u);
    run("big_off", 0, 0x40000000u);
    run("big_on", 1, 0x40000000u);
    return 0;
}
""".replace("__ALLOCS__", allocs)
    full = "\n".join([
        harness, rt["FAMILIES_BODY"], rt["HOOKS_DECLS"], rt["HOOKS_VMAP"], rt["MAIN_ALLOC_PART1"], rt["HOOKS_CORE"],
        rt["MAIN_ALLOC_PART2"], replay, S14_LOG_DECLS, extract_function(MAIN_C, "static void s14_log_decision("),
        extract_function(MAIN_C, "static void s14_after_register("), main])
    full = re.sub(r"\b(0x[0-9a-fA-F]+)L\b", r"\1LL", full)
    full = re.sub(r"\b(\d+)L\b", r"\1LL", full)
    full = full.replace("unsigned long long", "uint64_t").replace("long long", "int64_t").replace("unsigned long", "uint64_t")
    return re.sub(r"\blong\b", "int64_t", full)


S14_LOG_DECLS = """
#ifndef LOG_DIR
#define LOG_DIR "/data/bb_probe"
#endif
static int g_s14_opt = 0;
static const char *g_s14_why = 0;
"""


def _rows(out: str) -> dict:
    res = {}
    for line in out.splitlines():
        parts = line.split()
        if parts and "=" in line:
            res[parts[0]] = {k: v for k, v in (p.split("=", 1) for p in parts[1:])}
    return res


DECIDE = r"""
#include <stdio.h>
#include <string.h>
#define LOG_DIR "/data/bb_probe"
typedef struct { int shave_mode, below30, present_copy, two_buffer; } Cfg;
static Cfg g_cfg;
__FAMILIES__
static char g_title_id[16] = "CUSA18723";
static int g_bench_enabled, g_safe_never, g_file, g_lines, g_af;
static int g_s14_on, g_s14_opt;
static const char *g_s14_why;
static int cusa10249_audio_fix(void) { return g_af; }
static int sceKernelOpen(const char *p, int f, int m)
{
    char w[64]; snprintf(w, sizeof(w), "/shave14_%s", g_title_id); (void)f; (void)m;
    return g_file && strstr(p, w) ? 3 : -1;
}
static int sceKernelClose(int fd) { (void)fd; return 0; }
#define STAGE(...) ((void)0)
static void lg(const char *f, ...) { (void)f; g_lines++; }
__DECIDE__
static void base(void)
{
    g_file = 1; g_bench_enabled = 0; g_safe_never = 0; g_af = 0; g_lines = 0; strcpy(g_title_id, "CUSA18723");
    g_cfg.shave_mode = 0; g_cfg.below30 = 1; g_cfg.present_copy = 0; g_cfg.two_buffer = 1;
}
#define T(name, setup, mode) do { base(); setup; s14_decide(mode); printf("%s %d %d\n", name, g_s14_on, g_lines); } while (0)
int main(void)
{
    T("console", (void)0, 8);
    T("nofile", g_file = 0, 8);
    T("member", (g_file = 0, strcpy(g_title_id, "CUSA28863")), 8);
    T("fam_b30off", (g_file = 0, g_cfg.below30 = 0), 8);
    T("other", (g_file = 0, strcpy(g_title_id, "CUSA07439")), 8);
    T("other_file", strcpy(g_title_id, "CUSA07439"), 8);
    T("other_file_b30off", (strcpy(g_title_id, "CUSA07439"), g_cfg.below30 = 0), 8);
    T("other_twobuf0", (g_file = 0, strcpy(g_title_id, "CUSA07439"), g_cfg.two_buffer = 0), 8);
    T("mode4", (void)0, 4);
    T("bench", g_bench_enabled = 1, 8);
    T("observe", g_safe_never = 1, 8);
    T("shave1", g_cfg.shave_mode = 1, 8);
    T("shave2", g_cfg.shave_mode = 2, 8);
    T("b30off", g_cfg.below30 = 0, 8);
    T("twobuf0", g_cfg.two_buffer = 0, 8);
    T("pcopy1", (g_cfg.two_buffer = 0, g_cfg.present_copy = 1), 8);
    T("pcopy2", g_cfg.present_copy = 2, 8);
    T("audio", g_af = 1, 8);
    return 0;
}
"""


class Shave14BootReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = _rows(compile_and_run(_replay_source()))

    def test_without_shave14_is_todays_boot(self):
        off = self.r["off"]
        self.assertEqual((off["shave_n"], off["shaved"], off["pre"], off["nmid"], off["nbuf"]),
                         ("1", "0x600000", "0x800000", "1", "4"))
        self.assertEqual((off["state"], off["extra"], off["pbuf"], off["fails"], off["tail_mapped"]),
                         ("0", "0x0", "0x800000", "0", "1"))

    def test_shave14_two_buffers_and_longer_tail(self):
        on = self.r["on"]
        self.assertEqual((on["shave_n"], on["shaved"], on["pre"], on["nmid"], on["mbase"], on["nbuf"]),
                         ("1", "0xe00000", "0x1000000", "2", "3", "5"))
        self.assertEqual((on["state"], on["extra"], on["pbuf"], on["fails"], on["tail_mapped"]),
                         ("2", "0x800000", "0x0", "0", "1"))

    def test_shave14_keeps_the_games_memory(self):
        off, on = self.r["off"], self.r["on"]
        self.assertEqual(on["flex"], off["flex"])        # +8 MiB tail, -8 MiB history buffer
        self.assertEqual(on["free64"], off["free64"])    # the extra 8 MiB of DirectMemory is our 2nd buffer
        self.assertEqual(on["free2m"], off["free2m"])
        big_off, big_on = self.r["big_off"], self.r["big_on"]
        self.assertEqual((big_on["shaved"], big_on["nmid"], big_on["pbuf"]), ("0xe00000", "2", "0x0"))
        self.assertEqual(big_on["flex"], big_off["flex"])

    def test_short_flexible_memory_falls_back_to_today(self):
        so, sn = self.r["short_off"], self.r["short_on"]
        self.assertEqual(sn["state"], "-1")
        for k in ("shave_n", "shaved", "pre", "nmid", "nbuf", "extra", "pbuf", "flex", "free64", "free2m", "fails"):
            self.assertEqual(sn[k], so[k], k)
        self.assertEqual((so["shaved"], so["pre"], so["nmid"]), ("0x600000", "0x800000", "1"))


class Shave14Decide(unittest.TestCase):
    def test_decision(self):
        fam = (ROOT / "overlay" / "title_families.h").read_text(encoding="utf-8").replace("#pragma once", "")
        src = DECIDE.replace("__DECIDE__", extract_function(MAIN_C, "static void s14_decide(")).replace("__FAMILIES__", fam)
        r = {p[0]: (p[1], p[2]) for p in (l.split() for l in compile_and_run(src).splitlines())}
        # second field: lg() calls. s14_decide runs inside plugin_load, where lg()'s stdio (fopen of probe.log) faults
        # in libSceLibcInternal: SIGBUS at boot on the console. It must log with STAGE only.
        self.assertEqual(r["console"], ("1", "0"))
        # CUSA18723 family: a candidate without the file (it was validated there), only with below30 on
        self.assertEqual(r["nofile"], ("1", "0"))
        self.assertEqual(r["member"], ("1", "0"))
        self.assertEqual(r["fam_b30off"], ("0", "0"))
        # any other title: only with the file; without it the boot is today's (no reserve change, no line)
        self.assertEqual(r["other"], ("0", "0"))
        self.assertEqual(r["other_twobuf0"], ("0", "0"))
        self.assertEqual(r["other_file"], ("1", "0"))
        self.assertEqual(r["other_file_b30off"], ("0", "0"))
        self.assertEqual(r["pcopy1"], ("1", "0"))
        for k in ("mode4", "bench", "observe", "shave1", "shave2", "b30off", "twobuf0", "pcopy2", "audio"):
            self.assertEqual(r[k], ("0", "0"), k)


class Shave14CallSites(unittest.TestCase):
    def test_plugin_load_decides_before_the_early_reserve(self):
        pl = MAIN_C[MAIN_C.index("s32 attr_public plugin_load("):]
        self.assertLess(pl.index("load_early_adapt_cfg();"), pl.index("s14_decide(m);"))
        self.assertLess(pl.index("s14_decide(m);"), pl.index("early display memory try"))

    def test_no_stdio_from_plugin_load(self):
        # every shave14 function plugin_load calls runs before libc is usable: raw kernel I/O and STAGE only
        pl = extract_function(MAIN_C, "s32 attr_public plugin_load(")
        called = sorted(set(re.findall(r"\b(s14_\w+)\(", pl)))
        self.assertEqual(called, ["s14_decide"])
        for name in called:
            body = extract_function(MAIN_C, "static void %s(" % name)
            for bad in ("lg(", "fopen(", "fprintf(", "fputs(", "fflush(", " printf(", "klog("):
                self.assertNotIn(bad, body, "%s calls %s inside plugin_load" % (name, bad))
        self.assertIn("s14_log_decision();", extract_function(MAIN_C, "static void s14_after_register("))

    def test_valve_step3_keeps_two_buffers_once_at_boot(self):
        hook = extract_function(HOOKS_H, "int32_t sceKernelAllocateDirectMemory_hook(")
        step3 = hook.index("if (g_s14_on && g_s14_state == 0 && boot_phase && g_pre_size >= S14_KEEP) {")
        self.assertLess(hook.index("// Step 3:"), step3)
        self.assertLess(step3, hook.index("shrink_early_mem_to(keep);"))
        self.assertLess(hook.index("shrink_early_mem_to(keep);"), hook.index("// Step 4:"))
        step5 = hook[hook.index("// Step 5:"):]
        self.assertIn("shrink_early_mem_to(0x800000u);", step5[:400])

    def test_shave_budget(self):
        shave = extract_function(HOOKS_H, "static int32_t shave_game_alloc(")
        self.assertIn("flex_av >= need_tail + S14_FLEX_FLOOR", shave)
        self.assertLess(shave.index("int flex_ok = "), shave.index("if (g_s14_state == 1 && g_pre_size >= S14_KEEP) {"))
        self.assertIn("g_s14_tail_extra = (len - shave_av) < S14_EXTRA ? (len - shave_av) : S14_EXTRA;", shave)
        self.assertRegex(MAIN_C, r"#define S14_KEEP\s+0x1000000u")
        self.assertRegex(MAIN_C, r"#define S14_FLEX_FLOOR\s+0x1000000u")
        # floor = alloc_pbuf_history's full-buffer threshold (0x1800000) - S14_EXTRA (0x800000)
        self.assertIn("flex_av < 0x1800000u", extract_function(MAIN_C, "static void *alloc_pbuf_history("))

    def test_every_reserve_shrink_reports(self):
        self.assertEqual(HOOKS_H.count("s14_reserve_now("), 4)
        self.assertIn('s14_reserve_now(0, "released");', extract_function(MAIN_C, "static void release_early_mem("))
        pb = extract_function(MAIN_C, "static void *alloc_pbuf_history(")
        self.assertLess(pb.index("if (g_s14_tail_extra) {"), pb.index("sceKernelAvailableFlexibleMemorySize)(&flex_av)"))

    def test_register_and_regime(self):
        reg = MAIN_C[MAIN_C.index("int32_t sceVideoOutRegisterBuffers_hook("):MAIN_C.index("// ---- in-game L3+R3 (3s) menu")]
        self.assertIn("s14_after_register(nm, one, m, base + 1);", reg)
        self.assertIn("s14_after_register(nm, one, m, g_ngame + 1);", reg)
        self.assertIn("b30_available(g_cfg.below30, g_nmid, g_mem_tight && g_s14_state != 2,", MAIN_C)
        self.assertIn('g_s14_state == 2 ? " | 2nd buffer: shave14 DirectMemory reserve"', MAIN_C)


if __name__ == "__main__":
    unittest.main()
