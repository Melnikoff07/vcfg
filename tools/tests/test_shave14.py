#!/usr/bin/env python3
"""shave14: the 2nd generated-frame buffer from a 16 MiB display reserve (when the game starts in Fast or with below30,
by default for the CUSA18723 family, opt-in by LOG_DIR/shave14_<TITLE> for other titles).

1. Boot replay of the CUSA18723 PS4 base 1080p boot (the v2.34 console boot log: 12 game allocations, then the game's
   RegisterBuffers of 3) through the real valve, Arena Shaving, flexible-tail mapping and history-buffer code of
   classic/source/debug_hooks.h and main.c, with the test_real_traces.py harness compiled on the host:
   - without shave14: as today (8 MiB reserve, arena shaved by 0x600000, one generated-frame buffer, 8 MiB history
     buffer in FlexibleMemory);
   - with shave14: 16 MiB reserve, arena shaved by 0xE00000, two generated-frame buffers (g_nmid 2, extended to 5), no
     history buffer, and the FlexibleMemory left at the end equal to the run without shave14; every game allocation and
     map succeeds and the free DirectMemory is the same;
   - with shave14 but less FlexibleMemory than tail + S14_FLEX_FLOOR: exactly the run without shave14;
   - s14_decide itself on that boot (start in Quality + below30, Fast, Fast + below30, Quality, Sharp): Fast without
     below30 now gets the same two buffers as below30, every allocation and map succeeds and the FlexibleMemory and free
     DirectMemory equal the run without shave14; Quality / Sharp without below30 stay today's boot (negative control);
   - the CUSA18723 boot traces of tools/tests/real_traces.json, replayed the same way with each start: no allocation
     or map fails, and Fast without below30 boots exactly like below30.
2. s14_decide compiled with stubs and the real title family table: the CUSA18723 family is a candidate without the
   file once the start asks for two generated frames per pair (Fast or below30), any other title only with the file;
   not a candidate means off and no log line; each disqualifying setting keeps it off, in Fast too.
3. shave14 and the CUSA10249 audio_fix never run in the same boot: s14_decide and the real
   cusa10249_audio_fix() of debug_hooks.h over every title of overlay/title_families.h and every combination of the
   settings s14_decide reads plus audio_fix; both still turn on where they should (non-vacuous). Over the same
   enumeration the decision equals the v2.35 rule with below30 replaced by "below30 or Fast": unchanged wherever
   below30 = 1, and the only new boots with shave14 are Fast starts.
4. main.c / debug_hooks.h call sites: the decision runs in plugin_load before the early display reserve, the boot valve
   step 3 keeps S14_KEEP, the shave budget falls back to step 5, every reserve shrink reports, RegisterBuffers reports
   in both registration paths, the below-30 regime treats the two shave14 buffers as not tight, and the BELOW30 line
   names the buffer.

Same compiler choice as test_pace_policy.py (config.json llvm_bin, otherwise the host clang/cc).
"""
import json
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


def decide_src() -> str:
    """s14_decide with what it reads from main.c: the boot preset, the start's mode helper and its two strings."""
    decls = [re.search(r"^static int g_boot_preset = .*$", MAIN_C, re.M).group(0),
             re.search(r"^static const char \*g_s14_boot = .*$", MAIN_C, re.M).group(0),
             re.search(r"^static const char k_s14_why_one_gen\[\] = .*$", MAIN_C, re.M).group(0)]
    return "\n".join(decls + [extract_function(MAIN_C, "static int s14_two_gen("),
                               extract_function(MAIN_C, "static void s14_decide(")])


FAMILY = ("CUSA18723", "CUSA18742", "CUSA28863")
STARTS = (("q_b30", 0, 1), ("fast", 1, 0), ("fast_b30", 1, 1), ("q", 0, 0), ("sharp", 2, 0))   # tag, preset, below30


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
    cfg_end = "    int audio_fix;\n} fg_config_t;"
    assert cfg_end in harness
    harness = harness.replace(cfg_end, "    int audio_fix;\n    int below30;\n} fg_config_t;")
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
static int g_dec_preset, g_dec_b30;
/* s14_on < 0: the real s14_decide(8) with preset g_dec_preset and below30 g_dec_b30 (no LOG_DIR/shave14 file) */
static void run_s14(const char *tag, int s14_on, size_t flex)
{
    apply_title_defaults_sim("CUSA18723");
    g_cfg.two_buffer = 1;                    /* the console profile: the history buffer is taken today */
    if (s14_on < 0) { g_boot_preset = g_dec_preset; g_cfg.below30 = g_dec_b30; s14_decide(8); s14_on = g_s14_on; }
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
    g_pcopy_on = g_pcopy_avail && g_cfg.present_copy != 2;   /* as the mode-8 FG branch sets it every frame */
    printf("%s s14=%d shave_n=%d shaved=0x%zx pre=0x%zx nmid=%d mbase=%d nbuf=%d state=%d extra=0x%zx pbuf=0x%zx flex=0x%zx "
           "free64=0x%zx free2m=0x%zx fails=%u tail_mapped=%d pcopy_on=%d pace_pcopy=%d\n",
           tag, g_s14_on, g_shave_n, shaved, g_pre_size, g_nmid, g_mbase, g_nbuf, g_s14_state, g_s14_tail_extra,
           g_pbuf ? g_pbuf_sz : (size_t)0, g_sim_flex_avail, dm_avail(0x10000u), dm_avail(0x200000u),
           g_dbg_alloc_fail, g_shave_tail_mapped, (int)g_pcopy_on, pace_pcopy());
}
static void run(const char *tag, int s14_on, size_t flex) { run_s14(tag, s14_on, flex); }
static void run_dec(const char *tag, int preset, int b30, size_t flex)
{
    g_dec_preset = preset; g_dec_b30 = b30;
    run_s14(tag, -1, flex);
}
__TRACE_FNS__
int main(void)
{
    run("off", 0, 0x4C00000u);               /* 70 MiB free at RegisterBuffers on the console + the 6 MiB tail */
    run("on", 1, 0x4C00000u);
    run("short_off", 0, 0x1500000u);         /* 21 MiB: < tail 14 MiB + floor 16 MiB */
    run("short_on", 1, 0x1500000u);
    run("big_off", 0, 0x40000000u);
    run("big_on", 1, 0x40000000u);
    /* the decision itself, one start each: (tag, preset, below30) */
    run_dec("dec_q_b30", 0, 1, 0x4C00000u);
    run_dec("dec_fast", 1, 0, 0x4C00000u);
    run_dec("dec_fast_b30", 1, 1, 0x4C00000u);
    run_dec("dec_q", 0, 0, 0x4C00000u);
    run_dec("dec_sharp", 2, 0, 0x4C00000u);
    run_dec("dec_fast_short", 1, 0, 0x1500000u);
    run_dec("dec_fast_big", 1, 0, 0x40000000u);
__TRACE_CALLS__
    return 0;
}
""".replace("__ALLOCS__", allocs)
    fns, calls = _trace_c()
    main = main.replace("__TRACE_FNS__", fns).replace("__TRACE_CALLS__", calls)
    full = "\n".join([
        harness, rt["FAMILIES_BODY"], rt["HOOKS_DECLS"], rt["HOOKS_VMAP"], rt["MAIN_ALLOC_PART1"], rt["HOOKS_CORE"],
        rt["MAIN_ALLOC_PART2"], replay, S14_LOG_DECLS, S14_DECIDE_STUBS, decide_src(),
        extract_function(MAIN_C, "static void s14_log_decision("),
        extract_function(MAIN_C, "static void s14_after_register("), extract_function(MAIN_C, "static int pace_pcopy("),
        main])
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


S14_DECIDE_STUBS = """
#include <stdio.h>
static int g_bench_enabled = 0;
static int sceKernelOpen(const char *p, int f, int m) { (void)p; (void)f; (void)m; return -1; }   /* no shave14 file */
static int sceKernelClose(int fd) { (void)fd; return 0; }
"""


def _family_traces() -> list:
    traces = json.loads((ROOT / "tools" / "tests" / "real_traces.json").read_text(encoding="utf-8"))
    return [t for t in traces if t["title_id"] in FAMILY]


def _trace_c() -> tuple:
    """One C function per CUSA18723-family boot trace of real_traces.json (allocations and the game's RegisterBuffers,
    as test_real_traces.py replays them) and one call per start (STARTS) x two_buffer (0, 1) x FlexibleMemory (the
    console's 0x4C00000 and the harness default). Every allocation and map that succeeded on the console must succeed."""
    fns, calls = [], []
    for ti, tr in enumerate(_family_traces()):
        key = tr["trace_key"]
        body = []
        for step, ev in enumerate(tr["events"]):
            if ev["op"] == "alloc":
                if ev["orig_ret"] == "0x0":
                    body.append("    { long p = 0; void *va = NULL;\n"
                                "      int32_t r = sceKernelAllocateDirectMemory(%sLL, %sLL, %sULL, %sULL, %d, &p);\n"
                                "      ASSERT_MSG(r == 0, tag, %d, %sULL);\n"
                                "      int32_t mr = sceKernelMapDirectMemory(&va, %sULL, 0x33, 0, p, %sULL);\n"
                                "      ASSERT_MSG(mr == 0 && va != NULL, tag, %d, %sULL); }"
                                % (ev["s"], ev["e"], ev["len"], ev["align"], ev["type"], step, ev["len"], ev["len"],
                                   ev["align"], step, ev["len"]))
                else:
                    body.append("    { long p = 0; if (sceKernelAllocateDirectMemory(%sLL, %sLL, %sULL, %sULL, %d, &p) == 0) "
                                "sceKernelReleaseDirectMemory(p, %sULL); }"
                                % (ev["s"], ev["e"], ev["len"], ev["align"], ev["type"], ev["len"]))
            elif ev["op"] == "reg_buffers":
                calc = ev["pitch"] * ((ev["h"] + 63) & ~63) * 4
                one = (calc + 0x7FFFF) & ~0x7FFFF   # replay_register_buffers' surface size
                body.append("    replay_register_buffers(%d, %d, %du, %du, %du);" % (ev["start"], ev["num"], ev["w"], ev["h"], ev["pitch"]))
                body.append("    if (g_mbuf_state == 1) s14_after_register(g_nbuf - g_mbase, 0x%xu, (const uint8_t *)g_pre_mem, g_mbase + 1);" % one)
        fns.append("""static void trace_%d(const char *tag, int preset, int b30, int tb, size_t flex)
{
    apply_title_defaults_sim("%s");
    g_cfg.two_buffer = tb; g_boot_preset = preset; g_cfg.below30 = b30;
    s14_decide(8);
    g_s14_state = 0; g_s14_tail_extra = 0; g_s14_flex_before = 0; g_s14_pbuf_logged = 0;
    setup_plugin_load_early(%sULL, %sLL);
    if (flex) g_sim_flex_avail = flex;
    g_flips = 0;
%s
    printf("%%s s14=%%d state=%%d nmid=%%d mbase=%%d nbuf=%%d pre=0x%%zx extra=0x%%zx pbuf=0x%%zx flex=0x%%zx free64=0x%%zx "
           "free2m=0x%%zx fails=%%u\\n", tag, g_s14_on, g_s14_state, g_nmid, g_mbase, g_nbuf, g_pre_size, g_s14_tail_extra,
           g_pbuf ? g_pbuf_sz : (size_t)0, g_sim_flex_avail, dm_avail(0x10000u), dm_avail(0x200000u), g_dbg_alloc_fail);
}""" % (ti, tr["title_id"], tr["dm_size"], tr["pre_occupied_low"], "\n".join(body)))
        for tag, preset, b30 in STARTS:
            for tb in (0, 1):
                for fl, flex in (("c", "0x4C00000u"), ("d", "0")):
                    calls.append('    trace_%d("tr:%s:%s:tb%d:%s", %d, %d, %d, %s);' % (ti, key, tag, tb, fl, preset, b30, tb, flex))
    return "\n".join(fns), "\n".join(calls)


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
    g_cfg.shave_mode = 0; g_cfg.below30 = 1; g_cfg.present_copy = 0; g_cfg.two_buffer = 1; g_boot_preset = 0;
}
#define T(name, setup, mode) do { base(); setup; s14_decide(mode); \
    printf("%s %d %d|%s|%s\n", name, g_s14_on, g_lines, g_s14_why ? g_s14_why : "-", g_s14_boot); } while (0)
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
    /* Fast (preset 1) asks for the 2nd buffer like below30 */
    T("fam_fast", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1), 8);
    T("member_fast", (g_file = 0, strcpy(g_title_id, "CUSA18742"), g_cfg.below30 = 0, g_boot_preset = 1), 8);
    T("fam_fast_b30", (g_file = 0, g_boot_preset = 1), 8);
    T("fam_sharp", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 2), 8);
    T("fam_sharp_b30", (g_file = 0, g_boot_preset = 2), 8);
    T("file_q", (g_cfg.below30 = 0), 8);
    T("other_fast", (g_file = 0, strcpy(g_title_id, "CUSA07439"), g_cfg.below30 = 0, g_boot_preset = 1), 8);
    T("other_file_fast", (strcpy(g_title_id, "CUSA07439"), g_cfg.below30 = 0, g_boot_preset = 1), 8);
    T("fast_mode4", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1), 4);
    T("fast_bench", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1, g_bench_enabled = 1), 8);
    T("fast_observe", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1, g_safe_never = 1), 8);
    T("fast_shave1", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1, g_cfg.shave_mode = 1), 8);
    T("fast_twobuf0", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1, g_cfg.two_buffer = 0), 8);
    T("fast_pcopy1", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1, g_cfg.two_buffer = 0, g_cfg.present_copy = 1), 8);
    T("fast_pcopy2", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1, g_cfg.present_copy = 2), 8);
    T("fast_audio", (g_file = 0, g_cfg.below30 = 0, g_boot_preset = 1, g_af = 1), 8);
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

    def test_pacing_keeps_the_present_copy_decision(self):
        # 150d759 / de139b7 on the console: no history buffer -> g_pcopy_on 0 -> pace_timer_on() 0 -> b30_available 0,
        # every BELOW30 line "off". The pacers must see the title's decision without shave14.
        off, on = self.r["off"], self.r["on"]
        self.assertEqual((off["pcopy_on"], off["pace_pcopy"]), ("1", "1"))
        self.assertEqual((on["pcopy_on"], on["pace_pcopy"]), ("0", "1"))
        self.assertEqual(self.r["short_on"]["pace_pcopy"], self.r["short_off"]["pace_pcopy"])

    def test_short_flexible_memory_falls_back_to_today(self):
        so, sn = self.r["short_off"], self.r["short_on"]
        self.assertEqual(sn["state"], "-1")
        for k in ("shave_n", "shaved", "pre", "nmid", "nbuf", "extra", "pbuf", "flex", "free64", "free2m", "fails"):
            self.assertEqual(sn[k], so[k], k)
        self.assertEqual((so["shaved"], so["pre"], so["nmid"]), ("0x600000", "0x800000", "1"))

    def test_decided_below30_is_unchanged(self):
        # below30 = 1 starts (Quality and Fast) decide exactly the forced-on boot (the v2.35 decision for below30 = 1)
        for k in ("dec_q_b30", "dec_fast_b30"):
            self.assertEqual(self.r[k], self.r["on"], k)

    def test_decided_fast_gets_the_second_buffer(self):
        fast, off = self.r["dec_fast"], self.r["off"]
        self.assertEqual(fast, self.r["on"])
        self.assertEqual((fast["s14"], fast["state"], fast["nmid"], fast["pre"], fast["pbuf"], fast["fails"]),
                         ("1", "2", "2", "0x1000000", "0x0", "0"))
        self.assertEqual((fast["flex"], fast["free64"], fast["free2m"]), (off["flex"], off["free64"], off["free2m"]))
        self.assertEqual(self.r["dec_fast_big"], self.r["big_on"])
        # short FlexibleMemory: Fast falls back to today's boot like below30 does
        self.assertEqual(self.r["dec_fast_short"]["state"], "-1")
        for k in ("shave_n", "shaved", "pre", "nmid", "nbuf", "extra", "pbuf", "flex", "free64", "free2m", "fails"):
            self.assertEqual(self.r["dec_fast_short"][k], self.r["short_off"][k], k)

    def test_decided_one_generated_frame_is_todays_boot(self):
        # negative control: Quality / Sharp without below30 keep the boot without shave14
        for k in ("dec_q", "dec_sharp"):
            self.assertEqual(self.r[k], self.r["off"], k)

    def test_family_traces_every_start(self):
        rows = {k: v for k, v in self.r.items() if k.startswith("tr:")}
        traces = _family_traces()
        self.assertGreaterEqual(len(traces), 2)
        self.assertEqual(len(rows), len(traces) * len(STARTS) * 2 * 2)
        for k, v in rows.items():
            self.assertEqual(v["fails"], "0", k)   # ASSERT_MSG already exits on a failed console allocation or map
        on_somewhere = 0
        for tr in traces:
            for tb in (0, 1):
                for fl in ("c", "d"):
                    row = lambda start: rows["tr:%s:%s:tb%d:%s" % (tr["trace_key"], start, tb, fl)]
                    for start in ("fast", "fast_b30"):
                        self.assertEqual(row(start), row("q_b30"), (tr["trace_key"], start, tb, fl))
                    self.assertEqual(row("sharp"), row("q"), (tr["trace_key"], tb, fl))
                    self.assertEqual(row("q")["s14"], "0")
                    self.assertEqual(row("q")["state"], "0")
                    if tb == 0:   # no history buffer to give back: never a candidate that turns on
                        self.assertEqual(row("fast")["s14"], "0")
                    on_somewhere += row("fast")["state"] == "2"
        self.assertGreater(on_somewhere, 0)   # non-vacuous: Fast does get two buffers on a real trace


AUDIO_EXCLUSIVE = r"""
#include <stdio.h>
#include <string.h>
#define LOG_DIR "/data/bb_probe"
typedef struct { int shave_mode, below30, present_copy, two_buffer, audio_fix; } Cfg;
static Cfg g_cfg;
__FAMILIES__
static char g_title_id[16] = "UNKNOWN";
static int g_bench_enabled, g_safe_never, g_file;
static int g_s14_on, g_s14_opt;
static const char *g_s14_why;
__AUDIO__
static int sceKernelOpen(const char *p, int f, int m)
{
    char w[64]; snprintf(w, sizeof(w), "/shave14_%s", g_title_id); (void)f; (void)m;
    return g_file && strstr(p, w) ? 3 : -1;
}
static int sceKernelClose(int fd) { (void)fd; return 0; }
#define STAGE(...) ((void)0)
static void lg(const char *f, ...) { (void)f; }
__DECIDE__
/* v2.35's s14_decide as a pure rule: a candidate with the file or as a CUSA18723-family title with below30,
 * then every disqualifying setting. */
static int s14_on_v235(int file, int fam, int b30, int mode, int shave, int pc, int tb, int audio)
{
    int opt = file || (fam && b30);
    return opt && mode == 8 && shave == 0 && b30 && !(pc == 2 || (pc != 1 && tb != 1)) && !audio;
}
static int decide_p(const char *id, int file, int b30, int pc, int tb, int af, int mode, int shave, int preset, int *audio)
{
    strcpy(g_title_id, id);
    g_file = file; g_bench_enabled = 0; g_safe_never = 0;
    g_cfg.below30 = b30; g_cfg.present_copy = pc; g_cfg.two_buffer = tb; g_cfg.audio_fix = af; g_cfg.shave_mode = shave;
    g_boot_preset = preset;
    s14_decide(mode);
    *audio = cusa10249_audio_fix();
    return g_s14_on;
}
static int decide(const char *id, int file, int b30, int pc, int tb, int af, int mode, int shave, int *audio)
{
    return decide_p(id, file, b30, pc, tb, af, mode, shave, 0, audio);
}
int main(void)
{
    static const char *extra[] = { "UNKNOWN", "CUSA00000" };
    const int n = (int)(sizeof(k_vcfg_title_aliases) / sizeof(k_vcfg_title_aliases[0]));
    static const int modes[] = { 4, 8 };
    long combos = 0, s14 = 0, audio = 0, both = 0, audio_in_18723 = 0, b30_changed = 0, rule_off = 0, fast_only = 0;
    for (int i = 0; i < n + 2; i++) {
        const char *id = i < n ? k_vcfg_title_aliases[i].id : extra[i - n];
        for (int file = 0; file < 2; file++) for (int b30 = 0; b30 < 2; b30++) for (int pc = 0; pc < 3; pc++)
        for (int tb = 0; tb < 2; tb++) for (int af = 0; af < 3; af++) for (int mi = 0; mi < 2; mi++)
        for (int shave = 0; shave < 3; shave++) for (int preset = 0; preset < 3; preset++) {
            int a = 0, on = decide_p(id, file, b30, pc, tb, af, modes[mi], shave, preset, &a);
            int fam = vcfg_in_family(id, "CUSA18723");
            if (b30 && on != s14_on_v235(file, fam, 1, modes[mi], shave, pc, tb, a)) b30_changed++;
            if (on != s14_on_v235(file, fam, b30 || preset == 1, modes[mi], shave, pc, tb, a)) rule_off++;
            if (on && !b30) fast_only++;
            combos++; s14 += on; audio += a != 0;
            if (a && vcfg_in_family(id, "CUSA18723")) audio_in_18723++;
            if (on && a) {
                both++;
                printf("BOTH %s file=%d b30=%d pc=%d tb=%d af=%d mode=%d shave=%d preset=%d\n", id, file, b30, pc, tb, af, modes[mi], shave, preset);
            }
        }
    }
    printf("total combos=%ld s14=%ld audio=%ld both=%ld audio_in_18723=%ld b30_changed=%ld rule_off=%ld fast_only=%ld\n",
           combos, s14, audio, both, audio_in_18723, b30_changed, rule_off, fast_only);
    int a = 0, on;
    /* the shave14 opt-in file on CUSA10249, console profile (two_buffer=1, below30=1, mode 8), in Quality and Fast */
    on = decide("CUSA10249", 1, 1, 0, 1, 1, 8, 0, &a); printf("c10249_af1 on=%d audio=%d why=%s\n", on, a, g_s14_why ? g_s14_why : "-");
    on = decide("CUSA10249", 1, 1, 0, 1, 2, 8, 0, &a); printf("c10249_af2 on=%d audio=%d why=%s\n", on, a, g_s14_why ? g_s14_why : "-");
    on = decide_p("CUSA10249", 1, 0, 0, 1, 1, 8, 0, 1, &a); printf("c10249_fast_af1 on=%d audio=%d why=%s\n", on, a, g_s14_why ? g_s14_why : "-");
    on = decide("CUSA10249", 1, 1, 0, 1, 0, 8, 0, &a); printf("c10249_af0 on=%d audio=%d why=%s\n", on, a, g_s14_why ? g_s14_why : "-");
    /* CUSA18723 with audio_fix forced in config.ini: shave14 stays on, the audio fix does not apply */
    on = decide("CUSA18723", 0, 1, 0, 1, 1, 8, 0, &a); printf("c18723_af1 on=%d audio=%d why=%s\n", on, a, g_s14_why ? g_s14_why : "-");
    return 0;
}
"""


class Shave14AudioFixExclusive(unittest.TestCase):
    """shave14 (2nd buffer from a 16 MiB display reserve, longer arena tail) and the CUSA10249 audio_fix (arena tail
    pre-shaved for the audio allocation, no FlexibleMemory history buffer) both decide where the arena's last
    MiBs and the display-adjacent DirectMemory go; they must never run in the same boot."""

    @classmethod
    def setUpClass(cls):
        fam = (ROOT / "overlay" / "title_families.h").read_text(encoding="utf-8").replace("#pragma once", "")
        audio = extract_function(HOOKS_H, "static inline int cusa10249_audio_fix(")
        src = (AUDIO_EXCLUSIVE.replace("__DECIDE__", decide_src())
               .replace("__FAMILIES__", fam).replace("__AUDIO__", audio))
        cls.out = compile_and_run(src)
        cls.r = _rows("\n".join(ln for ln in cls.out.splitlines() if not ln.startswith("BOTH ")))

    def test_never_both_in_one_boot(self):
        self.assertNotIn("BOTH ", self.out)
        t = self.r["total"]
        self.assertEqual(t["both"], "0")
        self.assertEqual(t["audio_in_18723"], "0")
        # non-vacuous: each one still turns on somewhere in the same enumeration
        self.assertGreater(int(t["s14"]), 0)
        self.assertGreater(int(t["audio"]), 0)
        self.assertGreater(int(t["combos"]), 10000)

    def test_rule_is_v235_with_below30_or_fast(self):
        t = self.r["total"]
        self.assertEqual(t["b30_changed"], "0")   # below30 = 1: the same decision as v2.35 in every combination
        self.assertEqual(t["rule_off"], "0")      # everywhere: v2.35 with below30 replaced by (below30 or Fast)
        self.assertGreater(int(t["fast_only"]), 0)

    def test_cusa10249_with_the_opt_in_file(self):
        for k in ("c10249_af1", "c10249_af2", "c10249_fast_af1"):
            self.assertEqual((self.r[k]["on"], self.r[k]["why"]), ("0", "audio_fix"), k)
            self.assertNotEqual(self.r[k]["audio"], "0", k)
        # audio_fix=0: the opt-in file still turns shave14 on
        self.assertEqual((self.r["c10249_af0"]["on"], self.r["c10249_af0"]["audio"]), ("1", "0"))

    def test_cusa18723_ignores_audio_fix(self):
        self.assertEqual((self.r["c18723_af1"]["on"], self.r["c18723_af1"]["audio"]), ("1", "0"))


class Shave14Decide(unittest.TestCase):
    def test_decision(self):
        fam = (ROOT / "overlay" / "title_families.h").read_text(encoding="utf-8").replace("#pragma once", "")
        src = DECIDE.replace("__DECIDE__", decide_src()).replace("__FAMILIES__", fam)
        out = compile_and_run(src).splitlines()
        r = {l.split()[0]: tuple(l.split("|")[0].split()[1:3]) for l in out}
        w = {l.split()[0]: tuple(l.split("|")[1:3]) for l in out}
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
        # Fast: the family is a candidate without below30 and turns on in the console profile
        for k in ("fam_fast", "member_fast", "fam_fast_b30", "other_file_fast", "fast_pcopy1"):
            self.assertEqual(r[k], ("1", "0"), k)
        self.assertEqual(w["fam_fast"], ("-", "fast"))
        self.assertEqual(w["fam_fast_b30"], ("-", "fast, below30"))
        self.assertEqual(w["console"], ("-", "below30"))
        # Sharp / Quality need below30 as before; other titles still need the file
        self.assertEqual(r["fam_sharp"], ("0", "0"))
        self.assertEqual(w["fam_sharp"], ("no file", ""))   # not a candidate: no line
        self.assertEqual(r["fam_sharp_b30"], ("1", "0"))
        self.assertEqual(r["other_fast"], ("0", "0"))
        self.assertEqual((r["file_q"], w["file_q"][0]), (("0", "0"), "neither Fast nor below30 at start"))
        self.assertIn("g_s14_why == k_s14_why_one_gen) ? OPT_BUF2_OFF_B30", MAIN_C)   # opt_state reads the same constant
        # every disqualifying setting keeps Fast off too
        for k in ("fast_mode4", "fast_bench", "fast_observe", "fast_shave1", "fast_twobuf0", "fast_pcopy2", "fast_audio"):
            self.assertEqual(r[k], ("0", "0"), k)


BOOT_CFG = r"""
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#define LOG_DIR "/data/bb_probe"
__MENU_COMBO__
__FAMILIES__
__CFG_T__
static char g_title_id[16] = "CUSA18723";
static int g_known_game = 1, g_detected_60fps, g_is_neo, g_fg_off, g_adapt_early_crashes, g_adapt_shave2_failed;
static int g_bench_enabled, g_safe_never, g_s14_on, g_s14_opt;
static const char *g_s14_why;
/* parse_ini_file_ex also reads dev keys into these */
static volatile int g_gv_cal_winner = -1, g_tile_override = -1, g_tile_sweep = -1, g_t6_bit_override = -1, g_post_budget = 1;
__G_CFG__
/* a tiny file system: the files plugin_load reads (config.ini, config-<TITLE>.ini, adapt-<TITLE>.ini, shave14_<TITLE>) */
static const char *g_fs_path[4], *g_fs_text[4];
static int g_fs_n;
static int sceKernelOpen(const char *p, int f, int m)
{
    (void)f; (void)m;
    for (int i = 0; i < g_fs_n; i++) if (strcmp(p, g_fs_path[i]) == 0) return 10 + i;
    return -1;
}
static long sceKernelRead(int fd, void *b, size_t n)
{
    const char *t = g_fs_text[fd - 10]; size_t l = strlen(t); if (l > n) l = n; memcpy(b, t, l); return (long)l;
}
static int sceKernelClose(int fd) { (void)fd; return 0; }
static int cusa10249_audio_fix(void) { return 0; }
#define STAGE(...) ((void)0)
#define lg(...) ((void)0)
__FNS__
int main(void)
{
    /* config.ini as the app writes it (key = value, spaces), the console profile of 29/10 */
    static const char *cases[][2] = {
        { "fast_b30off", "framegen = 1\nmode = 8\npreset = 1\nbelow30 = 0\ntwo_buffer = 1\nshallow_queue = 1\n" },
        { "quality_b30off", "framegen = 1\nmode = 8\npreset = 0\nbelow30 = 0\ntwo_buffer = 1\nshallow_queue = 1\n" },
        { "quality_b30on", "framegen = 1\nmode = 8\npreset = 0\nbelow30 = 1\ntwo_buffer = 1\nshallow_queue = 1\n" },
        { "sharp_b30off", "framegen = 1\nmode = 8\npreset = 2\nbelow30 = 0\ntwo_buffer = 1\nshallow_queue = 1\n" },
        { "fast_nospace", "framegen=1\nmode=8\npreset=1\nbelow30=0\ntwo_buffer=1\n" },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        g_fs_n = 0;
        g_fs_path[g_fs_n] = LOG_DIR "/config.ini"; g_fs_text[g_fs_n++] = cases[i][1];
        load_early_adapt_cfg();
        s14_decide(8);
        printf("%s on=%d opt=%d cfg_preset=%d boot=%s\n", cases[i][0], g_s14_on, g_s14_opt, (int)g_cfg.preset,
               g_s14_boot[0] ? g_s14_boot : "-");
    }
    /* the per-title file wins over config.ini, as plugin_load reads them */
    g_fs_n = 0;
    g_fs_path[g_fs_n] = LOG_DIR "/config.ini"; g_fs_text[g_fs_n++] = "preset = 0\nbelow30 = 0\ntwo_buffer = 1\n";
    g_fs_path[g_fs_n] = LOG_DIR "/config-CUSA18723.ini"; g_fs_text[g_fs_n++] = "preset = 1\n";
    load_early_adapt_cfg();
    s14_decide(8);
    printf("title_fast on=%d opt=%d cfg_preset=%d boot=%s\n", g_s14_on, g_s14_opt, (int)g_cfg.preset, g_s14_boot[0] ? g_s14_boot : "-");
    return 0;
}
"""


class Shave14BootConfig(unittest.TestCase):
    """The real boot path, nothing injected: config.ini parsed by parse_ini_file, load_early_adapt_cfg, then s14_decide,
    with g_cfg at main.c's static initializer (s14_decide runs in plugin_load before the first cfg_apply_live). The
    console run of 1d0943c (29/10, preset = 1, below30 = 0) printed no shave14 line: the preset never reached it."""

    @classmethod
    def setUpClass(cls):
        fam = (ROOT / "overlay" / "title_families.h").read_text(encoding="utf-8").replace("#pragma once", "")
        cfg_t = MAIN_C[MAIN_C.index("typedef struct {\n    int framegen;"):]
        cfg_t = cfg_t[:cfg_t.index("} fg_config_t;") + len("} fg_config_t;")]
        g_cfg = re.search(r"^static volatile fg_config_t g_cfg = .*$", MAIN_C, re.M).group(0)
        boot = re.search(r"^static int g_boot_preset = .*$", MAIN_C, re.M)   # absent in 1d0943c
        fns = [extract_function(MAIN_C, "static uint32_t fnv1a_bytes("),
               extract_function(MAIN_C, "static void cfg_apply_title_defaults("),
               extract_function(MAIN_C, "static void cfg_defaults("),
               extract_function(MAIN_C, "static int parse_ini_file_ex("),
               extract_function(MAIN_C, "static int parse_ini_file("),
               extract_function(MAIN_C, "static int load_adapt_ini("),
               extract_function(MAIN_C, "static void apply_adapt_ini("),
               boot.group(0) if boot else "",
               extract_function(MAIN_C, "static void load_early_adapt_cfg("),
               re.search(r"^static const char \*g_s14_boot = .*$", MAIN_C, re.M).group(0),
               re.search(r"^static const char k_s14_why_one_gen\[\] = .*$", MAIN_C, re.M).group(0),
               extract_function(MAIN_C, "static int s14_two_gen("),
               extract_function(MAIN_C, "static void s14_decide(")]
        src = (BOOT_CFG.replace("__FAMILIES__", fam).replace("__CFG_T__", cfg_t).replace("__G_CFG__", g_cfg)
               .replace("__FNS__", "\n".join(fns)))
        combo = (ROOT / "overlay" / "menu_combo.h").read_text(encoding="utf-8").replace("#pragma once", "")
        cls.r = _rows(compile_and_run(src.replace("__MENU_COMBO__", combo)))

    def test_fast_start_turns_shave14_on(self):
        for k in ("fast_b30off", "fast_nospace", "title_fast"):
            self.assertEqual((self.r[k]["on"], self.r[k]["opt"], self.r[k]["boot"]), ("1", "2", "fast"), k)

    def test_quality_without_below30_stays_off(self):
        self.assertEqual((self.r["quality_b30off"]["on"], self.r["quality_b30off"]["opt"]), ("0", "0"))
        self.assertEqual((self.r["sharp_b30off"]["on"], self.r["sharp_b30off"]["opt"]), ("0", "0"))
        self.assertEqual((self.r["quality_b30on"]["on"], self.r["quality_b30on"]["boot"]), ("1", "below30"))

    def test_g_cfg_preset_untouched_before_init(self):
        # every other reader of g_cfg.preset before the first cfg_apply_live keeps v2.35's value (the static default)
        for k in ("fast_b30off", "quality_b30off", "title_fast"):
            self.assertEqual(self.r[k]["cfg_preset"], "0", k)


PACE = r"""
#include <stdio.h>
#include <string.h>
#define LOG_DIR "/data/bb_probe"
typedef struct { int pace_order, present_copy; } Cfg;
static Cfg g_cfg;
static char g_title_id[16] = "CUSA18723";
static volatile int g_pcopy_on;
static size_t g_s14_tail_extra;
static int g_file;
static int vcfg_in_family(const char *t, const char *f) { return strcmp(t, f) == 0; }
static void lg(const char *f, ...) { (void)f; }
#define fopen(p, m) ((void)(p), (void)(m), g_file ? stdin : (FILE *)0)
#define fclose(f) ((void)(f), 0)
__FNS__
int main(void)
{
    /* pcopy_on, tail_extra, present_copy, pace_order, pace_timer file */
    static const int c[][5] = { {1, 0, 0, 0, 0}, {0, 1, 0, 0, 0}, {0, 0, 0, 0, 0}, {0, 0, 0, 0, 1}, {0, 1, 2, 0, 0},
                                {0, 1, 0, 2, 0} };
    for (unsigned i = 0; i < sizeof(c) / sizeof(c[0]); i++) {
        g_pcopy_on = c[i][0]; g_s14_tail_extra = c[i][1] ? 0x800000u : 0u; g_cfg.present_copy = c[i][2];
        g_cfg.pace_order = c[i][3]; g_file = c[i][4]; g_pace_timer = -1;
        printf("%d%d ", pace_pcopy(), pace_timer_on());
    }
    printf("\n");
    return 0;
}
"""


class Shave14Pacing(unittest.TestCase):
    def test_timer_pacing_with_the_history_buffer_withheld(self):
        fns = "static int g_pace_timer = -1;\n" + extract_function(MAIN_C, "static int pace_pcopy(") + "\n" + \
            extract_function(MAIN_C, "static int pace_timer_on(")
        out = compile_and_run(PACE.replace("__FNS__", fns)).split()
        # today with the history buffer; shave14 without it; neither (vblank); neither + pace_timer file;
        # present_copy=2 (no history buffer anyway); pace_order=2 (post-submit forced, not the timer)
        self.assertEqual(out, ["11", "11", "00", "01", "00", "10"])

    def test_every_pacer_reads_pace_pcopy(self):
        for sig in ("static void pace_vblank_ex(", "static void pace_pair_ex(", "static int pace_timer_on("):
            body = extract_function(MAIN_C, sig)
            self.assertNotIn("g_pcopy_on ?", body, sig)
            self.assertNotIn("(g_pcopy_on ||", body, sig)
            self.assertNotIn("&& g_pcopy_on)", body, sig)
        self.assertEqual(MAIN_C.count("(g_cfg.pace_order == 2) ? 0 : (!pace_pcopy());"), 2)
        self.assertNotIn("(g_cfg.pace_order == 2) ? 0 : (!g_pcopy_on);", MAIN_C)


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
