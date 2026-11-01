#!/usr/bin/env python3
"""
Unit and synthetic trace tests for:
1) FIFO-based real-frame LATENCY measurement in classic/source/main.c (up to pending=8 without
   buffer-slot overwrite when pcopy=1 cycles 2 copy buffers 4 and 5), separate decomposition
   into pace sleep, VideoOut queue wait, and total hand-over -> on screen latency.
2) CUSA05571 2-buffer pcopy missed-flips & lock-step aliasing protection (no false 120..440 ms shelves
   after cur_mid==1 wakeups or mon_thread starvation bursts).
3) Flight-recorder line width TRACE_W in overlay/vcfg_trace.h (no truncation of CADENCE or LATENCY,
   bounded within main.c lg queue buffer size).
"""

from __future__ import annotations
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C_PATH = ROOT / "classic" / "source" / "main.c"
TRACE_H_PATH = ROOT / "overlay" / "vcfg_trace.h"


def compile_and_run_c(c_source: str) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        src_file = tmp / "test_lat.c"
        src_file.write_text(c_source, encoding="utf-8")

        cfg_path = ROOT / "config.json"
        if os.name == "nt" and cfg_path.exists():
            (tmp / "stdio.h").write_text(
                "#pragma once\n"
                "#include <stddef.h>\n"
                "#include <stdarg.h>\n"
                "extern int _snprintf(char *, size_t, const char *, ...);\n"
                "#define snprintf _snprintf\n"
                "extern int _vsnprintf(char *, size_t, const char *, va_list);\n"
                "#define vsnprintf _vsnprintf\n"
                "extern int printf(const char *, ...);\n"
                "extern int strcmp(const char *, const char *);\n"
                "extern char *strcpy(char *, const char *);\n"
                "extern void *memcpy(void *, const void *, size_t);\n"
                "typedef struct {\n"
                "    int used;\n"
                "    char path[320];\n"
                "    char data[256];\n"
                "    size_t len;\n"
                "    size_t pos;\n"
                "} FILE;\n"
                "static FILE g_vfs_files[8];\n"
                "static FILE *fopen(const char *path, const char *mode) {\n"
                "    if (mode[0] == 'w') {\n"
                "        for (int i = 0; i < 8; i++) {\n"
                "            if (!g_vfs_files[i].used || strcmp(g_vfs_files[i].path, path) == 0) {\n"
                "                g_vfs_files[i].used = 1;\n"
                "                strcpy(g_vfs_files[i].path, path);\n"
                "                g_vfs_files[i].len = 0;\n"
                "                g_vfs_files[i].pos = 0;\n"
                "                g_vfs_files[i].data[0] = 0;\n"
                "                return &g_vfs_files[i];\n"
                "            }\n"
                "        }\n"
                "        return (FILE *)0;\n"
                "    }\n"
                "    for (int i = 0; i < 8; i++) {\n"
                "        if (g_vfs_files[i].used && strcmp(g_vfs_files[i].path, path) == 0) {\n"
                "            g_vfs_files[i].pos = 0;\n"
                "            return &g_vfs_files[i];\n"
                "        }\n"
                "    }\n"
                "    return (FILE *)0;\n"
                "}\n"
                "static int fprintf(FILE *f, const char *fmt, ...) {\n"
                "    if (!f) return -1;\n"
                "    va_list ap;\n"
                "    va_start(ap, fmt);\n"
                "    int n = _vsnprintf(f->data + f->len, sizeof(f->data) - f->len, fmt, ap);\n"
                "    va_end(ap);\n"
                "    if (n > 0) f->len += (size_t)n;\n"
                "    return n;\n"
                "}\n"
                "static size_t fread(void *ptr, size_t size, size_t nmemb, FILE *f) {\n"
                "    if (!f || size == 0) return 0;\n"
                "    size_t avail = (f->pos < f->len) ? (f->len - f->pos) : 0;\n"
                "    size_t want = size * nmemb;\n"
                "    if (want > avail) want = avail;\n"
                "    memcpy(ptr, f->data + f->pos, want);\n"
                "    f->pos += want;\n"
                "    return want / size;\n"
                "}\n"
                "static int fclose(FILE *f) { (void)f; return 0; }\n"
                "static int remove(const char *path) {\n"
                "    for (int i = 0; i < 8; i++) {\n"
                "        if (g_vfs_files[i].used && strcmp(g_vfs_files[i].path, path) == 0) {\n"
                "            g_vfs_files[i].used = 0;\n"
                "            return 0;\n"
                "        }\n"
                "    }\n"
                "    return -1;\n"
                "}\n",
                encoding="utf-8",
            )
            (tmp / "string.h").write_text(
                "#pragma once\n"
                "#include <stddef.h>\n"
                "extern void *memset(void *, int, size_t);\n"
                "extern void *memcpy(void *, const void *, size_t);\n"
                "extern int memcmp(const void *, const void *, size_t);\n"
                "extern int strncmp(const char *, const char *, size_t);\n"
                "extern int strcmp(const char *, const char *);\n"
                "extern char *strstr(const char *, const char *);\n"
                "extern size_t strlen(const char *);\n",
                encoding="utf-8",
            )
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            llvm = pathlib.Path(cfg["llvm_bin"])
            bc_file = tmp / "test_lat.bc"
            subprocess.run(
                [
                    str(llvm / "clang.exe"),
                    "--target=x86_64-pc-windows-msvc",
                    f"-I{tmp}",
                    f"-I{ROOT / 'classic' / 'source'}",
                    "-O0",
                    "-fno-stack-protector",
                    "-Wall",
                    "-Wextra",
                    "-Wno-unused-function",
                    "-Wno-unused-variable",
                    "-Werror",
                    "-fno-builtin",
                    "-emit-llvm",
                    "-c",
                    str(src_file),
                    "-o",
                    str(bc_file),
                ],
                check=True,
            )
            res = subprocess.run(
                [str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bc_file)],
                check=True,
                capture_output=True,
                text=True,
            )
            return res.stdout
        else:
            bin_file = tmp / "test_lat_bin"
            cc = "gcc"
            subprocess.run(
                [cc, "-O0", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-variable", "-Werror", f"-I{ROOT / 'classic' / 'source'}", str(src_file), "-o", str(bin_file)],
                check=True,
            )
            res = subprocess.run([str(bin_file)], check=True, capture_output=True, text=True)
            return res.stdout


class LatencyFifoAndTraceWidthTest(unittest.TestCase):
    def test_trace_w_and_buffer_bounds(self):
        trace_h = TRACE_H_PATH.read_text(encoding="utf-8")
        main_c = MAIN_C_PATH.read_text(encoding="utf-8")

        m_tw = re.search(r"#define\s+TRACE_W\s+(\d+)", trace_h)
        self.assertIsNotNone(m_tw, "TRACE_W not found in overlay/vcfg_trace.h")
        trace_w = int(m_tw.group(1))

        m_lgq = re.search(r"static\s+char\s+g_lgq\[LGQ_N\]\[(\d+)\];", main_c)
        self.assertIsNotNone(m_lgq, "g_lgq declaration not found in classic/source/main.c")
        lgq_w = int(m_lgq.group(1))

        # TRACE_W must be large enough for CADENCE (~245 chars with timestamp) and fit within lgq_w (384)
        self.assertGreaterEqual(trace_w, 300, f"TRACE_W={trace_w} is too small and truncates CADENCE")
        self.assertLessEqual(trace_w, lgq_w, f"TRACE_W={trace_w} exceeds g_lgq buffer width {lgq_w}")

    def test_synthetic_pending5_and_pending8_fifo_vs_legacy(self):
        main_c = MAIN_C_PATH.read_text(encoding="utf-8")
        trace_h = TRACE_H_PATH.read_text(encoding="utf-8")

        m_tw = re.search(r"#define\s+TRACE_W\s+(\d+)", trace_h)
        self.assertIsNotNone(m_tw)
        trace_w = int(m_tw.group(1))

        # Extract FIFO block from main.c
        s_fifo = main_c.find("#define LAT_FIFO_N")
        self.assertGreaterEqual(s_fifo, 0, "LAT_FIFO_N FIFO implementation not found in classic/source/main.c")
        e_fifo = main_c.find("static volatile uint32_t g_gv_letters_base", s_fifo)
        self.assertGreater(e_fifo, s_fifo, "End of LAT_FIFO block not found in classic/source/main.c")
        fifo_code = main_c[s_fifo:e_fifo]

        # Extract LATENCY lg/lgd(...) format call from mon_thread in main.c
        s_lat_lg = main_c.find('lgd("LATENCY real frame hand-over -> on screen:')
        if s_lat_lg < 0:
            s_lat_lg = main_c.find('lg("LATENCY real frame hand-over -> on screen:')
        self.assertGreaterEqual(s_lat_lg, 0, "LATENCY lg/lgd() call not found in classic/source/main.c")
        e_lat_lg = main_c.find('lgd("CADENCE (vblanks on screen)', s_lat_lg)
        if e_lat_lg < 0:
            e_lat_lg = main_c.find('lg("CADENCE (vblanks on screen)', s_lat_lg)
        self.assertGreater(e_lat_lg, s_lat_lg, "CADENCE lg/lgd() call not found after LATENCY in classic/source/main.c")
        lat_lg_code = main_c[s_lat_lg:e_lat_lg]

        # Verify decomposition fields in LATENCY log format
        self.assertIn("pace", lat_lg_code, "LATENCY format must report pace sleep")
        self.assertIn("vout", lat_lg_code, "LATENCY format must report VideoOut queue wait")

        c_harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>

#define TRACE_W {trace_w}

typedef struct {{
    int framegen;
    int preset;
}} fg_config_t;

static fg_config_t g_cfg = {{ 1, 0 }};
static int g_fg_off = 0;
static int g_fmt_supported = 1;
static int g_mbuf_state = 1;
static char g_inert_reason[32] = "";

static char g_last_log[512];
static char g_trace_row[TRACE_W];

static void sim_vcfg_trace(const char *line)
{{
    snprintf(g_trace_row, sizeof(g_trace_row), "%7.2f %s", 120.50, line);
}}

static void lg(const char *fmt, ...)
{{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_last_log, sizeof(g_last_log), fmt, ap);
    va_end(ap);
    sim_vcfg_trace(g_last_log);
}}
#define lgd lg

{fifo_code}

/* Legacy g_sub_t[16] implementation from ff683ff for direct A/B comparison on identical inputs */
static uint64_t leg_sub_t[16];
static uint64_t leg_lat_sum, leg_lat_n, leg_lat_max;

static void legacy_on_submit(uint32_t show_idx, uint64_t t_hook_in)
{{
    if (show_idx < 16u) leg_sub_t[show_idx] = t_hook_in;
}}

static void legacy_on_vout(int32_t cur_buf, uint64_t proc_t)
{{
    if (cur_buf >= 0 && cur_buf < 16) {{
        uint64_t t0 = leg_sub_t[cur_buf];
        leg_sub_t[cur_buf] = 0ull;
        if (t0 && proc_t > t0 && proc_t - t0 < 500000ull) {{
            uint64_t l = proc_t - t0;
            leg_lat_sum += l;
            leg_lat_n++;
            if (l > leg_lat_max) leg_lat_max = l;
        }}
    }}
}}

int main(void)
{{
    int pend_max = 5;

    /* =========================================================================
     * CASE 1: pcopy=1 (2 copy buffers: 4 and 5), pending=5, pace_pre=0.
     * Frame k+2 (using the same copy buffer 4+(k&1)) enters SubmitAndFlip at
     * t_in(k+2) = t_in(k) + 66732 us, while Frame k displays at
     * proc_t(k) = t_in(k) + 64000 us (< t_in(k+2)).
     * Legacy g_sub_t[ci] overwrites slot 4/5 with t_in(k+2) BEFORE proc_t(k) is
     * read, so proc_t(k) > t0 is FALSE -> 0 frames!
     * ========================================================================= */
    memset(leg_sub_t, 0, sizeof(leg_sub_t));
    leg_lat_sum = leg_lat_n = leg_lat_max = 0;
    memset((void *)g_lat_fifo, 0, sizeof(g_lat_fifo));
    g_lat_fifo_w = g_lat_fifo_r = 0;
    g_lat_sum = g_lat_n = g_lat_max = 0;
    g_lat_pace_sum = g_lat_pace_max = g_lat_vout_sum = g_lat_vout_max = 0;

    uint64_t base_t = 1000000ull;
    uint64_t period_us = 33366ull;
    uint64_t build_us = 100ull;
    uint64_t true_lat_c1 = 60000ull; /* 60.0 ms total (0.0 ms pace, 59.9 ms vout), < 62.732 ms so proc_t_k < t_in(k+2) */

    /* Prime the first 2 frames into the queue */
    for (int k = 0; k < 2; k++) {{
        uint32_t ci = 4u + (uint32_t)(k & 1);
        uint64_t t_in = base_t + (uint64_t)k * period_us;
        legacy_on_submit(ci, t_in);
        lat_fifo_push(ci, 0, t_in, t_in + build_us, 0ull);
    }}
    /* For 60 frames, frame k+2 submits BEFORE frame k's VBlank is polled */
    for (int k = 0; k < 60; k++) {{
        uint32_t ci_new = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_new = base_t + (uint64_t)(k + 2) * period_us - 4000ull; /* arrives early while pending=5 */
        legacy_on_submit(ci_new, t_in_new);
        lat_fifo_push(ci_new, 0, t_in_new, t_in_new + build_us, 0ull);

        uint32_t ci_out = 4u + (uint32_t)(k & 1);
        uint64_t t_in_k = (k < 2) ? (base_t + (uint64_t)k * period_us)
                                  : (base_t + (uint64_t)k * period_us - 4000ull);
        uint64_t proc_t_k = t_in_k + true_lat_c1;
        legacy_on_vout((int32_t)ci_out, proc_t_k);
        lat_fifo_on_vout((int32_t)ci_out, 0, proc_t_k, 0u);
    }}

    if (leg_lat_n != 0) {{
        printf("FAIL: expected legacy g_sub_t to drop all frames (0 frames), got %llu\\n",
               (unsigned long long)leg_lat_n);
        return 1;
    }}
    if (g_lat_n != 60) {{
        printf("FAIL: expected FIFO to record 60 frames in Case 1, got %llu\\n",
               (unsigned long long)g_lat_n);
        return 2;
    }}
    {lat_lg_code}
    printf("CASE1_LOG: %s", g_last_log);
    if (strlen(g_trace_row) >= TRACE_W - 1) {{
        printf("FAIL: LATENCY trace row truncated at %zu >= %d\\n", strlen(g_trace_row), TRACE_W - 1);
        return 3;
    }}

    /* =========================================================================
     * CASE 2: pcopy=1 (2 copy buffers: 4 and 5), pending=5, pace_pre=0.
     * Frame k displays at proc_t(k) = t_in(k) + 75000 us (> t_in(k+2) = t_in(k) + 66732 us).
     * Legacy g_sub_t[ci] subtracts t_in(k+2) from proc_t(k) and under-reports
     * 75.0 - 66.732 = 8.268 ms (~8.3 ms) instead of 75.0 ms!
     * ========================================================================= */
    memset(leg_sub_t, 0, sizeof(leg_sub_t));
    leg_lat_sum = leg_lat_n = leg_lat_max = 0;
    memset((void *)g_lat_fifo, 0, sizeof(g_lat_fifo));
    g_lat_fifo_w = g_lat_fifo_r = 0;

    uint64_t true_lat_c2 = 75000ull; /* 75.0 ms */
    for (int k = 0; k < 2; k++) {{
        uint32_t ci = 4u + (uint32_t)(k & 1);
        uint64_t t_in = base_t + (uint64_t)k * period_us;
        legacy_on_submit(ci, t_in);
        lat_fifo_push(ci, 0, t_in, t_in + build_us, 0ull);
    }}
    for (int k = 0; k < 60; k++) {{
        uint32_t ci_new = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_new = base_t + (uint64_t)(k + 2) * period_us;
        legacy_on_submit(ci_new, t_in_new);
        lat_fifo_push(ci_new, 0, t_in_new, t_in_new + build_us, 0ull);

        uint32_t ci_out = 4u + (uint32_t)(k & 1);
        uint64_t t_in_k = base_t + (uint64_t)k * period_us;
        uint64_t proc_t_k = t_in_k + true_lat_c2;
        legacy_on_vout((int32_t)ci_out, proc_t_k);
        lat_fifo_on_vout((int32_t)ci_out, 0, proc_t_k, 0u);
    }}

    double leg_avg_c2 = leg_lat_n ? (double)leg_lat_sum / 1000.0 / (double)leg_lat_n : 0.0;
    double fifo_avg_c2 = g_lat_n ? (double)g_lat_sum / 1000.0 / (double)g_lat_n : 0.0;
    if (!(leg_avg_c2 > 8.0 && leg_avg_c2 < 8.6)) {{
        printf("FAIL: expected legacy alias ~8.3 ms in Case 2, got %.2f ms\\n", leg_avg_c2);
        return 4;
    }}
    if (!(fifo_avg_c2 > 74.9 && fifo_avg_c2 < 75.1 && g_lat_n == 60)) {{
        printf("FAIL: expected FIFO avg 75.0 ms (60 frames) in Case 2, got %.2f ms (%llu frames)\\n",
               fifo_avg_c2, (unsigned long long)g_lat_n);
        return 5;
    }}
    {lat_lg_code}
    printf("CASE2_LOG: %s", g_last_log);

    /* =========================================================================
     * CASE 3: pending=8 (4 real pairs in flight) with pace_pre=1 (11.8 ms sleep
     * in pace + 67.7 ms in VideoOut queue + 0.1 ms build = 79.6 ms total).
     * ========================================================================= */
    memset((void *)g_lat_fifo, 0, sizeof(g_lat_fifo));
    g_lat_fifo_w = g_lat_fifo_r = 0;
    pend_max = 8;

    uint64_t pace_sleep_c3 = 11800ull; /* 11.8 ms */
    uint64_t vout_wait_c3  = 67700ull; /* 67.7 ms */
    uint64_t total_lat_c3  = build_us + pace_sleep_c3 + vout_wait_c3; /* 79.6 ms */

    for (int k = 0; k < 4; k++) {{
        uint32_t ci = 4u + (uint32_t)(k & 1);
        uint64_t t_in = base_t + (uint64_t)k * period_us;
        uint64_t t_sub = t_in + build_us + pace_sleep_c3;
        lat_fifo_push(ci, (int64_t)(k + 1), t_in, t_sub, pace_sleep_c3);
    }}
    for (int k = 0; k < 60; k++) {{
        uint32_t ci_new = 4u + (uint32_t)((k + 4) & 1);
        uint64_t t_in_new = base_t + (uint64_t)(k + 4) * period_us;
        uint64_t t_sub_new = t_in_new + build_us + pace_sleep_c3;
        lat_fifo_push(ci_new, (int64_t)(k + 5), t_in_new, t_sub_new, pace_sleep_c3);

        uint32_t ci_out = 4u + (uint32_t)(k & 1);
        uint64_t t_in_k = base_t + (uint64_t)k * period_us;
        uint64_t proc_t_k = t_in_k + total_lat_c3;
        lat_fifo_on_vout((int32_t)ci_out, (int64_t)(k + 1), proc_t_k, 0u);
    }}
    {lat_lg_code}
    printf("CASE3_LOG: %s", g_last_log);
    return 0;
}}
"""
        out = compile_and_run_c(c_harness)
        self.assertIn(
            "CASE1_LOG: LATENCY real frame hand-over -> on screen: avg 60.0 max 60.0 ms (pace 0.0/0.0 vout 59.9/59.9) (60 frames) | FG on preset 0 | pending max 5",
            out,
        )
        self.assertIn(
            "CASE2_LOG: LATENCY real frame hand-over -> on screen: avg 75.0 max 75.0 ms (pace 0.0/0.0 vout 74.9/74.9) (60 frames) | FG on preset 0 | pending max 5",
            out,
        )
        self.assertIn(
            "CASE3_LOG: LATENCY real frame hand-over -> on screen: avg 79.6 max 79.6 ms (pace 11.8/11.8 vout 67.7/67.7) (60 frames) | FG on preset 0 | pending max 8",
            out,
        )

    def test_cusa05571_real_log_pattern_missed_and_2buf_aliasing(self):
        """
        Replays the real CUSA05571 pattern from owner-lat-exp1/A & B:
        - pcopy=1 alternating 2 copy buffers (4 and 5), mid buffers (2 and 3), flipArg=0
        - true latency = 58.0 ms (build 0.1 ms, pace 0.0 ms, vout 57.9 ms)
        - Event 1: mon_thread wakes on a mid buffer after missing 2 real frames (cur_mid == 1, nf = 5),
          which without persistent pend_skipped_reals discarded skipped_reals and locked FIFO into +66.73 ms (m=1, ~124.7 ms).
        - Event 2: CPU starvation burst from B/trace-CUSA05571.txt:225 (t=62.44s, missed 293, nf=294),
          which without 105 ms vout / 150 ms tot eviction and parity-mismatch accounting locked subsequent windows into ~400..458 ms.
        """
        main_c = MAIN_C_PATH.read_text(encoding="utf-8")

        s_fifo = main_c.find("#define LAT_FIFO_N")
        if s_fifo >= 0:
            e_fifo = main_c.find("static volatile uint32_t g_gv_letters_base", s_fifo)
            self.assertGreater(e_fifo, s_fifo)
            fifo_code = main_c[s_fifo:e_fifo]
        else:
            # Fallback shim for pre-FIFO main.c (ff683ff) where mon_thread uses g_sub_t[16]
            fifo_code = """
static volatile uint64_t g_sub_t[16];
static volatile uint64_t g_lat_sum, g_lat_n, g_lat_max;
static volatile uint64_t g_lat_pace_sum, g_lat_pace_max;
static volatile uint64_t g_lat_vout_sum, g_lat_vout_max;
static void lat_fifo_push(uint32_t show_idx, int64_t real_arg, uint64_t t_in, uint64_t t_sub, uint64_t pace_us)
{
    (void)real_arg; (void)t_sub; (void)pace_us;
    if (show_idx < 16u) g_sub_t[show_idx] = t_in;
}
"""

        s_vars = main_c.find("uint64_t last_count = 0, last_t = 0")
        e_vars = main_c.find("for (;;) {", s_vars)
        self.assertGreater(e_vars, s_vars)
        mon_vars_code = main_c[s_vars:e_vars]

        s_step = main_c.find("if (fs.count != last_count) {", e_vars)
        e_step = main_c.find("if (g_mon) g_mon[0] = (uint32_t)vs.count;", s_step)
        self.assertGreater(e_step, s_step)
        mon_step_code = main_c[s_step:e_step]

        c_harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pace_policy.h"

#define IS_MID(b) ((b) == 2 || (b) == 3)

typedef struct {{
    uint64_t count;
    uint64_t processTime;
    int32_t  currentBuffer;
    int64_t  flipArg;
    int32_t  flipPendingNum;
    int32_t  gcQueueNum;
}} VoFlipStatus;

typedef struct {{
    uint64_t count;
    uint64_t processTime;
}} VoVblankStatus;

static void *g_log = NULL;
static uint32_t g_ft[FT_N][3];
static uint32_t g_ft_w = 0;
static uint32_t g_ft_slot[16];
static uint32_t *g_mon = NULL;
static uint32_t g_mon_missed = 0, g_mon_gaps = 0;
static uint64_t g_mids_shown = 0, g_reals_shown = 0;
static int32_t  g_last_real_buf = -1;
static uint32_t g_hist[2][4];
static volatile uint32_t g_last_real_hold_us = 16683u;
static volatile uint32_t g_pll_t_interp_q16 = 0u;

{fifo_code}

/* Naive 32-entry FIFO without pend_skipped_reals and without 105ms/150ms eviction (f345a42 behavior)
 * to verify the test input reproduces the false ~400..458 ms 2-buffer lock-step aliasing on CUSA05571. */
typedef struct {{ uint32_t buf; uint64_t t_in; }} NaiveEntry;
static NaiveEntry n_fifo[32];
static uint32_t n_w = 0, n_r = 0;
static uint64_t n_sum = 0, n_cnt = 0;
static void naive_push(uint32_t buf, uint64_t t_in) {{
    uint32_t w = n_w;
    n_fifo[w & 31u].buf = buf;
    n_fifo[w & 31u].t_in = t_in;
    n_w = w + 1u;
}}
static void naive_on_vout(int32_t cur_buf, uint64_t proc_t, uint32_t imm_skipped) {{
    uint32_t r = n_r, w = n_w;
    if (w - r > 32u) r = w - 32u;
    while (r != w) {{
        uint64_t t0 = n_fifo[r & 31u].t_in;
        if (!t0 || (proc_t > t0 && proc_t - t0 >= 500000ull)) r++;
        else break;
    }}
    while (imm_skipped > 0u && r != w) {{
        if (proc_t >= n_fifo[r & 31u].t_in) {{ r++; imm_skipped--; }}
        else break;
    }}
    for (uint32_t i = r; i != w; i++) {{
        if ((int32_t)n_fifo[i & 31u].buf == cur_buf) {{
            uint64_t t0 = n_fifo[i & 31u].t_in;
            n_r = i + 1u;
            if (t0 && proc_t > t0 && proc_t - t0 < 500000ull) {{
                n_sum += (proc_t - t0);
                n_cnt++;
            }}
            return;
        }}
    }}
    n_r = r;
}}

int main(void)
{{
    VoFlipStatus fs;
    VoVblankStatus vs;
    memset(&fs, 0, sizeof(fs));
    memset(&vs, 0, sizeof(vs));
    {mon_vars_code}
    (void)win_t; (void)win_vb; (void)osd_t; (void)osd_fcnt; (void)pend_max; (void)gcq_max;

    uint64_t t_base = 10000000ull;
    uint64_t vb_us = 16683ull;
    uint64_t pair_us = 33366ull;
    uint64_t build_us = 100ull;
    uint64_t true_lat_us = 58000ull; /* 58.0 ms true latency in CUSA05571 */

    uint64_t flip_cnt = 0;
    /* Prime frames 0 and 1 in queue (pending=4..5: 2 real frames in flight) */
    for (int k = 0; k < 2; k++) {{
        uint32_t ci = 4u + (uint32_t)(k & 1);
        uint64_t t_in = t_base + (uint64_t)k * pair_us;
        lat_fifo_push(ci, 0, t_in, t_in + build_us, 0ull);
        naive_push(ci, t_in);
    }}

    /* 1) 10 normal pairs: mon_thread sees every mid and every real flip; frame k+2 submits before k is polled */
    for (int k = 0; k < 10; k++) {{
        uint32_t next_ci = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_next = t_base + (uint64_t)(k + 2) * pair_us;
        lat_fifo_push(next_ci, 0, t_in_next, t_in_next + build_us, 0ull);
        naive_push(next_ci, t_in_next);

        /* mid flip for frame k */
        flip_cnt++;
        fs.count = flip_cnt;
        fs.currentBuffer = 2 + (k & 1);
        fs.flipArg = 0;
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us - vb_us;
        {mon_step_code}

        /* real flip for frame k */
        flip_cnt++;
        fs.count = flip_cnt;
        fs.currentBuffer = 4 + (k & 1);
        fs.flipArg = 0;
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us;
        {mon_step_code}
        naive_on_vout(fs.currentBuffer, fs.processTime, 0u);
    }}

    /* 2) Event 1 (cur_mid == 1 after 2 missed real frames k=10,11):
     * Frames k=10 (buf 4) and k=11 (buf 5) flip on VideoOut while mon_thread is pre-empted,
     * and mon_thread wakes up on mid of k=12 (cur_mid == 1, nf = 5 from real k=9 to mid k=12). */
    for (int k = 10; k <= 12; k++) {{
        uint32_t next_ci = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_next = t_base + (uint64_t)(k + 2) * pair_us;
        lat_fifo_push(next_ci, 0, t_in_next, t_in_next + build_us, 0ull);
        naive_push(next_ci, t_in_next);
    }}
    /* Skip mid 10, real 10, mid 11, real 11 -> wake on mid 12 (5 flips after real 9) */
    flip_cnt += 5;
    fs.count = flip_cnt;
    fs.currentBuffer = 2 + (12 & 1); /* mid_buf */
    fs.flipArg = 0;
    fs.processTime = t_base + 12ull * pair_us + true_lat_us - vb_us;
    {mon_step_code}

    /* Now real 12 latches on VideoOut (nf = 1 after mid 12 -> imm_skipped = 0 in naive!) */
    flip_cnt++;
    fs.count = flip_cnt;
    fs.currentBuffer = 4 + (12 & 1);
    fs.flipArg = 0;
    fs.processTime = t_base + 12ull * pair_us + true_lat_us;
    {mon_step_code}
    naive_on_vout(fs.currentBuffer, fs.processTime, 0u);

    /* Reset window accumulators and measure 60 normal frames (k=13..72) with 0 missed flips */
    g_lat_sum = g_lat_n = g_lat_max = 0;
    g_lat_pace_sum = g_lat_pace_max = g_lat_vout_sum = g_lat_vout_max = 0;
    n_sum = n_cnt = 0;
    for (int k = 13; k < 73; k++) {{
        uint32_t next_ci = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_next = t_base + (uint64_t)(k + 2) * pair_us;
        lat_fifo_push(next_ci, 0, t_in_next, t_in_next + build_us, 0ull);
        naive_push(next_ci, t_in_next);

        flip_cnt++;
        fs.count = flip_cnt;
        fs.currentBuffer = 2 + (k & 1);
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us - vb_us;
        {mon_step_code}

        flip_cnt++;
        fs.count = flip_cnt;
        fs.currentBuffer = 4 + (k & 1);
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us;
        {mon_step_code}
        naive_on_vout(fs.currentBuffer, fs.processTime, 0u);
    }}
    double avg_after_ev1 = g_lat_n ? (double)g_lat_sum / 1000.0 / (double)g_lat_n : 0.0;
    double naive_ev1 = n_cnt ? (double)n_sum / 1000.0 / (double)n_cnt : 0.0;
    printf("NAIVE_EV1: avg=%.1f ms (n=%llu)\\n", naive_ev1, (unsigned long long)n_cnt);
    printf("AFTER_EV1: avg=%.1f ms (n=%llu)\\n", avg_after_ev1, (unsigned long long)g_lat_n);

    /* 3) Event 2 (missed 293 burst from B/trace-CUSA05571.txt:225 at t=62.44s):
     * 147 real frames (k=73..219, 294 flips with coalesced non-alternating parity) pass while
     * mon_thread is starved for 4.9 s, then mon_thread resumes normal polling at k=220..279. */
    for (int k = 73; k < 220; k++) {{
        uint32_t next_ci = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_next = t_base + (uint64_t)(k + 2) * pair_us;
        lat_fifo_push(next_ci, 0, t_in_next, t_in_next + build_us, 0ull);
        naive_push(next_ci, t_in_next);
        flip_cnt += 2;
    }}
    g_lat_sum = g_lat_n = g_lat_max = 0;
    n_sum = n_cnt = 0;
    for (int k = 220; k < 280; k++) {{
        uint32_t next_ci = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_next = t_base + (uint64_t)(k + 2) * pair_us;
        lat_fifo_push(next_ci, 0, t_in_next, t_in_next + build_us, 0ull);
        naive_push(next_ci, t_in_next);

        /* Simulate parity mismatch on first wakeup (k=220) as in missed 293 (gaps 8) */
        flip_cnt++;
        fs.count = (k == 220) ? (flip_cnt - 1ull) : flip_cnt;
        fs.currentBuffer = 2 + (k & 1);
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us - vb_us;
        {mon_step_code}

        flip_cnt++;
        fs.count = flip_cnt;
        fs.currentBuffer = 4 + (k & 1);
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us;
        {mon_step_code}
        naive_on_vout(fs.currentBuffer, fs.processTime, 0u);
    }}
    double avg_after_ev2 = g_lat_n ? (double)g_lat_sum / 1000.0 / (double)g_lat_n : 0.0;
    double naive_ev2 = n_cnt ? (double)n_sum / 1000.0 / (double)n_cnt : 0.0;
    printf("NAIVE_EV2: avg=%.1f ms (n=%llu)\\n", naive_ev2, (unsigned long long)n_cnt);
    printf("AFTER_EV2: avg=%.1f ms (n=%llu)\\n", avg_after_ev2, (unsigned long long)g_lat_n);
    return 0;
}}
"""
        out = compile_and_run_c(c_harness)
        self.assertIn("NAIVE_EV1: avg=124.7 ms (n=60)", out)
        self.assertIn("NAIVE_EV2: avg=458.4 ms (n=60)", out)
        self.assertIn("AFTER_EV1: avg=58.0 ms (n=60)", out, f"FIFO failed on cur_mid=1 miss:\\n{out}")
        self.assertIn("AFTER_EV2: avg=58.0 ms (n=60)", out, f"FIFO failed on missed=293 burst (false >400 ms):\\n{out}")
        self.assertGreaterEqual(s_fifo, 0, "LAT_FIFO_N FIFO implementation not found in classic/source/main.c")

    def test_nf4_same_buffer_2buf_pcopy_wakeup(self):
        """
        Edge case from REVIEW_STEP1.md §1.3:
        On a 2-buffer game with pcopy=1 (real copy buffers alternate 4, 5 and mid buffers alternate 2, 3),
        when last_buf == 4 (real) and mon_thread sleeps across nf=4 flips (mid 2 -> real 5 -> mid 3 -> real 4),
        the wakeup lands on fs.currentBuffer == 4 == last_buf.
        lat_fifo_on_vout must still be called for the new real 4 flip, consuming pend_skipped_reals=1 (real 5)
        and resetting pend_skipped_reals to 0; across two consecutive nf=4 jumps, subsequent normal frames
        must continue reporting 36.0 ms rather than desynchronizing by +66.7 ms (102.7 ms).
        """
        main_c = MAIN_C_PATH.read_text(encoding="utf-8")

        s_fifo = main_c.find("#define LAT_FIFO_N")
        self.assertGreaterEqual(s_fifo, 0, "LAT_FIFO_N FIFO implementation not found in classic/source/main.c")
        e_fifo = main_c.find("static volatile uint32_t g_gv_letters_base", s_fifo)
        self.assertGreater(e_fifo, s_fifo)
        fifo_code = main_c[s_fifo:e_fifo]

        s_vars = main_c.find("uint64_t last_count = 0, last_t = 0")
        e_vars = main_c.find("for (;;) {", s_vars)
        self.assertGreater(e_vars, s_vars)
        mon_vars_code = main_c[s_vars:e_vars]

        s_step = main_c.find("if (fs.count != last_count) {", e_vars)
        e_step = main_c.find("if (g_mon) g_mon[0] = (uint32_t)vs.count;", s_step)
        self.assertGreater(e_step, s_step)
        mon_step_code = main_c[s_step:e_step]

        c_harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pace_policy.h"

#define IS_MID(b) ((b) == 2 || (b) == 3)

typedef struct {{
    uint64_t count;
    uint64_t processTime;
    int32_t  currentBuffer;
    int64_t  flipArg;
    int32_t  flipPendingNum;
    int32_t  gcQueueNum;
}} VoFlipStatus;

typedef struct {{
    uint64_t count;
    uint64_t processTime;
}} VoVblankStatus;

static void *g_log = NULL;
static uint32_t g_ft[FT_N][3];
static uint32_t g_ft_w = 0;
static uint32_t g_ft_slot[16];
static uint32_t *g_mon = NULL;
static uint32_t g_mon_missed = 0, g_mon_gaps = 0;
static uint64_t g_mids_shown = 0, g_reals_shown = 0;
static int32_t  g_last_real_buf = -1;
static uint32_t g_hist[2][4];
static volatile uint32_t g_last_real_hold_us = 16683u;
static volatile uint32_t g_pll_t_interp_q16 = 0u;

{fifo_code}

int main(void)
{{
    VoFlipStatus fs;
    VoVblankStatus vs;
    memset(&fs, 0, sizeof(fs));
    memset(&vs, 0, sizeof(vs));
    {mon_vars_code}
    (void)win_t; (void)win_vb; (void)osd_t; (void)osd_fcnt; (void)pend_max; (void)gcq_max;

    uint64_t t_base = 20000000ull;
    uint64_t vb_us = 16683ull;
    uint64_t pair_us = 33366ull;
    uint64_t build_us = 100ull;
    uint64_t true_lat_us = 36000ull; /* 36.0 ms true latency (so 36.0 + 2*33.366 = 102.7 ms < 105 ms eviction) */

    /* Prime frames k=0 (buf 4) and k=1 (buf 5) in FIFO */
    for (int k = 0; k < 2; k++) {{
        uint32_t ci = 4u + (uint32_t)(k & 1);
        uint64_t t_in = t_base + (uint64_t)k * pair_us;
        lat_fifo_push(ci, 0, t_in, t_in + build_us, 0ull);
    }}

    /* Frame k=0 (mid 2 -> real 4) flips normally while k=2 submits */
    lat_fifo_push(4u, 0, t_base + 2ull * pair_us, t_base + 2ull * pair_us + build_us, 0ull);

    fs.count = 1;
    fs.currentBuffer = 2;
    fs.flipArg = 0;
    fs.processTime = t_base + true_lat_us - vb_us;
    {mon_step_code}

    fs.count = 2;
    fs.currentBuffer = 4;
    fs.flipArg = 0;
    fs.processTime = t_base + true_lat_us;
    {mon_step_code}

    /* Now last_buf == 4 (real 4 of k=0), last_count == 2.
     * mon_thread sleeps across nf=4 flips:
     *   count=3: mid 2 (k=1)
     *   count=4: real 5 (k=1)  [skipped real #1]
     *   count=5: mid 3 (k=2)
     *   count=6: real 4 (k=2)  [currentBuffer == 4 == last_buf!]
     * Meanwhile frames k=3 (buf 5) and k=4 (buf 4) submit into FIFO. */
    for (int k = 3; k <= 4; k++) {{
        uint32_t ci = 4u + (uint32_t)(k & 1);
        uint64_t t_in = t_base + (uint64_t)k * pair_us;
        lat_fifo_push(ci, 0, t_in, t_in + build_us, 0ull);
    }}
    fs.count = 6; /* nf = 6 - 2 = 4 */
    fs.currentBuffer = 4; /* matches last_buf == 4 */
    fs.flipArg = 0;
    fs.processTime = t_base + 2ull * pair_us + true_lat_us;
    {mon_step_code}

    double avg_nf4_1 = g_lat_n ? (double)g_lat_sum / 1000.0 / (double)g_lat_n : 0.0;
    printf("NF4_STEP1: n=%llu pend_skipped=%u avg=%.1f ms\\n",
           (unsigned long long)g_lat_n, (unsigned)pend_skipped_reals, avg_nf4_1);

    /* Second consecutive nf=4 wakeup (count 6 -> 10: mid 2 -> real 5 (k=3) -> mid 3 -> real 4 (k=4)) */
    for (int k = 5; k <= 6; k++) {{
        uint32_t ci = 4u + (uint32_t)(k & 1);
        uint64_t t_in = t_base + (uint64_t)k * pair_us;
        lat_fifo_push(ci, 0, t_in, t_in + build_us, 0ull);
    }}
    fs.count = 10; /* nf = 10 - 6 = 4 */
    fs.currentBuffer = 4; /* matches last_buf == 4 again */
    fs.flipArg = 0;
    fs.processTime = t_base + 4ull * pair_us + true_lat_us;
    {mon_step_code}

    /* Measure 10 normal pairs (k=5..14) after the two nf=4 wakeups */
    g_lat_sum = g_lat_n = g_lat_max = 0;
    g_lat_pace_sum = g_lat_pace_max = g_lat_vout_sum = g_lat_vout_max = 0;
    uint64_t flip_cnt = 10;
    for (int k = 5; k < 15; k++) {{
        uint32_t next_ci = 4u + (uint32_t)((k + 2) & 1);
        uint64_t t_in_next = t_base + (uint64_t)(k + 2) * pair_us;
        lat_fifo_push(next_ci, 0, t_in_next, t_in_next + build_us, 0ull);

        flip_cnt++;
        fs.count = flip_cnt;
        fs.currentBuffer = 2 + (k & 1);
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us - vb_us;
        {mon_step_code}

        flip_cnt++;
        fs.count = flip_cnt;
        fs.currentBuffer = 4 + (k & 1);
        fs.processTime = t_base + (uint64_t)k * pair_us + true_lat_us;
        {mon_step_code}
    }}
    double avg_after_2x_nf4 = g_lat_n ? (double)g_lat_sum / 1000.0 / (double)g_lat_n : 0.0;
    printf("AFTER_2X_NF4: avg=%.1f ms (n=%llu) pend_skipped=%u\\n",
           avg_after_2x_nf4, (unsigned long long)g_lat_n, (unsigned)pend_skipped_reals);
    return 0;
}}
"""
        out = compile_and_run_c(c_harness)
        self.assertIn(
            "NF4_STEP1: n=2 pend_skipped=0 avg=36.0 ms",
            out,
            f"lat_fifo_on_vout was skipped or pend_skipped_reals not consumed when nf=4 and fs.currentBuffer==last_buf:\\n{out}",
        )
        self.assertIn(
            "AFTER_2X_NF4: avg=36.0 ms (n=10) pend_skipped=0",
            out,
            f"FIFO desynchronized after two consecutive nf=4 same-buffer wakeups:\\n{out}",
        )


if __name__ == "__main__":
    unittest.main()
