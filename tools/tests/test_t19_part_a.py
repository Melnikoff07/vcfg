#!/usr/bin/env python3
"""T19 Part A regression tests (A1-A7).

Verifies:
- A1 & A2: me1.ll and mf8.ll restored to release-236 (no 32768 fast-skip, mf8 dwords == 1410);
  warp8.ll keeps @cr skip (flags & 32768); vcfg_motion.py / vcfg_ref.py have no 4x1 fast-skip model.
- A3: calc_rcas_params() in classic/source/main.c does NOT force target_mask = 1u when preset == 1.
- A4: classic/source/opt_status.h reports OPT_ACTIVE "sharp" for opt_mode() at preset == 2 when FG runs,
  and reports effective sharpness (35 floor / +20 boost, never OPT_OFF) in opt_sharp() and opt_format();
  s->mid_only in main.c matches post_mid_only(); Launcher Crash & Log Advisor handles new options effective lines.
- A5: plugin/source/a20_copy_impl.h (vcfg16) uses the exact same Sharp formula as classic for sharp = 0..100.
- A6: post_mid_only() in classic/source/main.c returns 0 (mid + real) when preset == 2 and post_mid == 0,
  while respecting explicit post_mid == 1 (returns 1) and keeping preset == 0/1 default at 1.
- A7: vcfg16 a21_emit_post processes both generated frames (a20_image) and real frame (current) with identical
  sharp and en (no mid_only mode in vcfg16).
"""
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8", errors="replace")
OPT_H = (ROOT / "classic" / "source" / "opt_status.h").read_text(encoding="utf-8", errors="replace")
A20_H = (ROOT / "plugin" / "source" / "a20_copy_impl.h").read_text(encoding="utf-8", errors="replace")
LAUNCHER_MAIN_C = (ROOT / "launcher" / "src" / "main.c").read_text(encoding="utf-8", errors="replace")
ME1_LL = (ROOT / "classic" / "shaders" / "me1.ll").read_text(encoding="utf-8")
MF8_LL = (ROOT / "classic" / "shaders" / "mf8.ll").read_text(encoding="utf-8")
WARP8_LL = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
VCFG_MOTION_PY = (ROOT / "tools" / "bench" / "vcfg_motion.py").read_text(encoding="utf-8")
VCFG_REF_PY = (ROOT / "tools" / "bench" / "vcfg_ref.py").read_text(encoding="utf-8")

CC = shutil.which("clang-22") or shutil.which("clang") or shutil.which("cc")


def extract_function(src: str, name: str) -> str:
    m = re.search(r"^static [^\n]+\b" + name + r"\([^;]*\)\s*\{", src, re.M)
    assert m is not None, f"Function {name} not found"
    pos = src.index("{", m.start())
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[m.start():end]


class TestT19PartA(unittest.TestCase):
    def test_a1_a2_me1_mf8_restored_and_warp8_cr_skip_kept(self):
        # me1.ll and mf8.ll must not contain FG8_FLAG_FAST (32768) skip
        self.assertNotIn("32768", ME1_LL)
        self.assertNotIn("32768", MF8_LL)
        # warp8.ll keeps @cr skip on bit 15 (32768)
        self.assertIn("and i32 %flags, 32768", WARP8_LL)
        # vcfg_motion.py and vcfg_ref.py must not have the 4x1 wave model fast_skip
        self.assertNotIn("me1_fast_skip_waves", VCFG_MOTION_PY)
        self.assertNotIn("mf8_fast_skip_waves", VCFG_MOTION_PY)
        self.assertNotIn("me1_fast_skip_waves", VCFG_REF_PY)
        self.assertNotIn("mf8_fast_skip_waves", VCFG_REF_PY)

    @unittest.skipUnless(CC, "no host C compiler")
    def test_a3_fast_does_not_force_target_mask_1_in_calc_rcas_params(self):
        fn_src = extract_function(MAIN_C, "calc_rcas_params")
        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct {{ int preset, sharpness, upscale; }} fg_config_t;
static fg_config_t g_cfg;
static uint32_t g_sh8_off[16] = {{ [14] = 0x18D00u }};
{fn_src}
int main(void) {{
    g_cfg.preset = 1;
    g_cfg.sharpness = 40;
    g_cfg.upscale = 1;
    uint32_t cb = 0, fl = 0;
    calc_rcas_params(3u, &cb, &fl);
    printf("fl=%u\\n", fl & 3u);
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "a3.c"
            exe = pathlib.Path(td) / "a3"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "fl=3")

    @unittest.skipUnless(CC, "no host C compiler")
    def test_a4_opt_status_sharp_and_launcher_advisor(self):
        str_fn = extract_function(LAUNCHER_MAIN_C, "strstr_trace_key")
        harness = f"""
#include <stdio.h>
#include <string.h>
#include "opt_status.h"
{str_fn}
int main(void) {{
    OptState s;
    memset(&s, 0, sizeof(s));
    s.framegen = 1;
    s.started = 1;
    s.fg_path = 1;
    s.mode8 = 1;
    s.nmid = 2;
    s.pace_timer = 1;
    s.rcas_shader = 1;
    s.preset = 2;
    s.sharpness = 0;
    s.post_mid = 0;
    s.mid_only = 0;
    OptStat m = opt_mode(&s);
    OptStat sh0 = opt_sharp(&s);
    char line0[400];
    opt_format(line0, sizeof(line0), &s);
    s.sharpness = 40;
    char line40[400];
    opt_format(line40, sizeof(line40), &s);
    char detail[64];
    opt_row_detail(detail, sizeof(detail), OPT_ROW_MODE, m);
    char trace_buf[512];
    snprintf(trace_buf, sizeof(trace_buf), "options effective: %s\\n", line0);
    int false_inert = strstr_trace_key(trace_buf, "inert") != NULL;
    printf("m=%d:%s|sh0=%d:%s|d=%s|fi=%d\\n%s\\n%s\\n",
           m.st, m.why, sh0.st, sh0.why, detail, false_inert, line0, line40);
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            shutil.copy(ROOT / "classic" / "source" / "opt_status.h", t / "opt_status.h")
            shutil.copy(ROOT / "classic" / "source" / "pace_policy.h", t / "pace_policy.h")
            (t / "a4.c").write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", "-I", str(t), str(t / "a4.c"), "-o", str(t / "a4")], check=True)
            lines = subprocess.run([str(t / "a4")], check=True, capture_output=True, text=True).stdout.splitlines()
        head, line0, line40 = lines[0], lines[1], lines[2]
        self.assertIn("m=1:sharp|", head)
        self.assertIn("sh0=1:generated+real|", head)
        self.assertIn("fi=0", head)
        self.assertIn("preset=sharp(active: sharp)", line0)
        self.assertIn("sharpness=50(applied: generated+real)", line0)
        self.assertIn("sharpness=75(applied: generated+real)", line40)
        # Also verify main.c opt_state_now sets s->mid_only = 0 when preset == 2 and post_mid == 0
        self.assertIn("g_cfg.post_mid != 2 && c->preset != 2 && !s->real_aa", MAIN_C)

    @unittest.skipUnless(CC, "no host C compiler")
    def test_a5_vcfg16_and_classic_sharp_formulas_equal_0_to_100(self):
        # Extract the two a21_preset == 2 blocks from #if A21_POST in plugin/source/a20_copy_impl.h
        post_pos = A20_H.index("// Generated surfaces; the native frame C is post-processed below")
        post_src = A20_H[post_pos:post_pos + 1600]
        blocks = re.findall(r"if\s*\(\s*a21_preset\s*==\s*2\s*\)\s*\{([^}]*)\}", post_src)
        self.assertEqual(len(blocks), 2, f"Expected 2 a21_preset==2 blocks in A21_POST, found {len(blocks)}")
        self.assertEqual(blocks[0].strip(), blocks[1].strip())
        vcfg_block = blocks[0]
        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int classic_eff_sharp(int sharp_pct) {{
    if (1) sharp_pct = (sharp_pct > 0) ? (sharp_pct + 35 > 100 ? 100 : sharp_pct + 35) : 50;
    if (sharp_pct > 100) sharp_pct = 100;
    return sharp_pct;
}}
static unsigned vcfg16_eff_sharp(unsigned sharp) {{
    unsigned a21_preset = 2;
    if (a21_preset == 2) {{
        {vcfg_block}
    }}
    if (sharp > 100u) sharp = 100u;
    return sharp;
}}
int main(void) {{
    for (int s = 0; s <= 100; s++) {{
        int c = classic_eff_sharp(s);
        int v = (int)vcfg16_eff_sharp((unsigned)s);
        if (c != v) {{
            printf("MISMATCH s=%d classic=%d vcfg16=%d\\n", s, c, v);
            return 1;
        }}
    }}
    printf("ALL_EQUAL_0_100\\n");
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "a5.c"
            exe = pathlib.Path(td) / "a5"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip(), "ALL_EQUAL_0_100")

    @unittest.skipUnless(CC, "no host C compiler")
    def test_a6_sharp_default_post_mid_0_targets_mid_and_real(self):
        fns = "\n".join(extract_function(MAIN_C, n) for n in ("real_aa_on", "post_mid_only"))
        harness = f"""
#include <stdint.h>
#include <stdio.h>
typedef struct {{ int post_mid, upscale, sharpness, preset; }} fg_config_t;
static fg_config_t g_cfg;
static int g_t_prev_half16 = 0;
static int g_mid_only = -1;
static int gv_warp_active(void) {{ return 0; }}
#define lg(...) ((void)0)
{fns}
int main(void) {{
    for (int preset = 0; preset <= 2; preset++) {{
        for (int pm = 0; pm <= 2; pm++) {{
            g_cfg.preset = preset;
            g_cfg.post_mid = pm;
            g_cfg.upscale = 1;
            g_cfg.sharpness = 40;
            g_mid_only = -1;
            printf("p%d_pm%d=%d\\n", preset, pm, post_mid_only());
        }}
    }}
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "a6.c"
            exe = pathlib.Path(td) / "a6"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
        kv = dict(ln.split("=") for ln in out.strip().splitlines())
        # Quality (p0) and Fast (p1): pm0 -> 1 (mid_only), pm1 -> 1, pm2 -> 0
        self.assertEqual(kv["p0_pm0"], "1")
        self.assertEqual(kv["p0_pm1"], "1")
        self.assertEqual(kv["p0_pm2"], "0")
        self.assertEqual(kv["p1_pm0"], "1")
        self.assertEqual(kv["p1_pm1"], "1")
        self.assertEqual(kv["p1_pm2"], "0")
        # Sharp (p2): pm0 -> 0 (mid + real!), explicit pm1 -> 1 (mid_only respected!), pm2 -> 0
        self.assertEqual(kv["p2_pm0"], "0")
        self.assertEqual(kv["p2_pm1"], "1")
        self.assertEqual(kv["p2_pm2"], "0")

    def test_a7_vcfg16_has_no_mid_only_and_processes_both_mid_and_real(self):
        post_pos = A20_H.index("// Generated surfaces; the native frame C is post-processed below")
        post_src = A20_H[post_pos:post_pos + 1600]
        # Generated frame(s) processed via a21_emit_post on a20_image
        self.assertIn("a21_emit_post(&p,sb+A21_POST_TB,(uint64_t)(uintptr_t)a20_image,sharp,en);", post_src)
        # Real native frame processed via a21_emit_post_wh on current when !menu
        self.assertIn("a21_emit_post_wh(&p,sb+A21_POSTC_TB,current,g_bw,g_bh,g_pitch,g_attr.tilingMode==0?10:8,sharp,en);", post_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
