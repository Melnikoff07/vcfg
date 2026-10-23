#!/usr/bin/env python3
"""Unit & integration tests for Chat 17: Runtime Auto-Healer & Telemetry Corpus Analyzer.

1. Compiles and executes the actual C implementation of `save_adapt_ini`, `load_adapt_ini`,
   `apply_adapt_ini`, `parse_ini_file_ex`, `read_two_buffer_raw`, and `load_early_adapt_cfg`
   from `classic/source/main.c` via Clang + LLI bitcode to verify round-trip persistence.
2. Verifies `tools/analyze_telemetry_corpus.py` across synthetic and real telemetry report
   snippets (`CUSA00552`, `CUSA04607`, `CUSA09311`, `CUSA08364`, `CUSA11456`, plus
   `SAFE_FALLBACK`, `LOADING_ONLY`, `USER_DISABLED`) in both directory and `.zip` formats,
   including strict verification that zero game names appear in any output.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_telemetry_corpus as atc  # noqa: E402


def _extract_c_function(text: str, name: str) -> str:
    m = re.search(r"^static [^\n]+\b" + re.escape(name) + r"\([^\n]*\)\n\{", text, re.M)
    if not m:
        raise RuntimeError(f"Function {name} not found in classic/source/main.c")
    start = m.start()
    pos = text.index("{", start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def test_classic_adapt_ini_roundtrip() -> None:
    """Test C round-trip persistence (save_adapt_ini -> load_adapt_ini) in classic/source/main.c."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    text = (ROOT / "classic/source/main.c").read_text(encoding="utf-8")

    struct_def = re.search(r"typedef struct \{.*?\} fg_config_t;", text, re.S)[0]
    families_h = (
        (ROOT / "overlay/title_families.h")
        .read_text(encoding="utf-8")
        .replace("#include <string.h>", "")
        .replace("#pragma once", "")
    )
    fn_names = [
        "fnv1a_bytes",
        "cfg_apply_title_defaults",
        "cfg_defaults",
        "parse_ini_file_ex",
        "parse_ini_file",
        "load_adapt_ini",
        "apply_adapt_ini",
        "save_adapt_ini",
        "read_present_copy_raw",
        "read_two_buffer_raw",
        "load_early_adapt_cfg",
    ]
    extracted_fns = "\n\n".join(_extract_c_function(text, n) for n in fn_names)

    out_dir = ROOT / "build/tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    c_path = out_dir / "runtime_autohealer_c_test.c"
    bc_path = out_dir / "runtime_autohealer_c_test.bc"

    c_source = f"""#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include "menu_combo.h"

extern int sscanf(const char *, const char *, ...);
extern int vsprintf(char *, const char *, va_list);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, size_t);
extern char *strstr(const char *, const char *);
extern size_t strlen(const char *);
extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);

static int snprintf(char *buf, size_t sz, const char *fmt, ...)
{{
    (void)sz;
    va_list ap;
    va_start(ap, fmt);
    int r = vsprintf(buf, fmt, ap);
    va_end(ap);
    return r;
}}

#define LOG_DIR "/data/bb_probe"
#define INJECT_AFTER_FLIPS 120
#define STAGE(...) do {{}} while (0)

{families_h}

{struct_def}

static char g_title_id[16] = "CUSA00552";
static int g_known_game = 1, g_detected_60fps = 0;
static int g_tile_override = -1, g_tile_sweep = 0, g_t6_bit_override = -1;
static volatile fg_config_t g_cfg = {{ 1, 8, 0, 40, 0, 100, 1, 0, 30, 3000, 80, 0, MENU_COMBO_DEFAULT, 0, 0, 0, 0, 0, 0, 0, 0, -1 }};
static volatile uint32_t g_mem_hibernate_count = 0;
static volatile uint32_t g_vout_fg_inited = 0;
static volatile uint32_t g_injects = 0;
static uint32_t g_last_adapt_sig = 0;
static uint32_t g_flips = 0;
static volatile uint32_t g_vo_submit_flips = 0;
static int g_shave_n = 0;
static int g_ngame = 3;
static int g_game_idx_hi = 3;
volatile int g_gv_cal_winner = -1;
volatile int g_gv_cal_locked = 0;

static void init_title_id(void) {{}}

// Simple in-memory virtual file table for /data/bb_probe/*.ini
static char vfs_adapt[1024];
static char vfs_config[1024];
static char vfs_global[1024];
static int g_last_open_kind = 0; // 1 = adapt, 2 = config, 3 = global

static int sceKernelMkdir(const char *p, int mode) {{ (void)p; (void)mode; return 0; }}
static int sceKernelOpen(const char *p, int flags, int mode) {{
    (void)mode;
    if (strstr(p, "adapt-")) {{
        if (flags != 0) vfs_adapt[0] = 0;
        else if (!vfs_adapt[0]) return -1;
        g_last_open_kind = 1;
        return 10;
    }}
    if (strstr(p, "config-")) {{
        if (!vfs_config[0]) return -1;
        g_last_open_kind = 2;
        return 11;
    }}
    if (strstr(p, "config.ini")) {{
        if (!vfs_global[0]) return -1;
        g_last_open_kind = 3;
        return 12;
    }}
    return -1;
}}
static long sceKernelRead(int fd, void *buf, size_t n) {{
    const char *src = (fd == 10) ? vfs_adapt : (fd == 11 ? vfs_config : (fd == 12 ? vfs_global : ""));
    size_t len = strlen(src);
    if (len > n) len = n;
    memcpy(buf, src, len);
    return (long)len;
}}
static long sceKernelWrite(int fd, const void *buf, size_t n) {{
    if (fd != 10) return -1;
    if (n >= sizeof(vfs_adapt)) n = sizeof(vfs_adapt) - 1;
    memcpy(vfs_adapt, buf, n);
    vfs_adapt[n] = 0;
    return (long)n;
}}
static int sceKernelClose(int fd) {{ (void)fd; return 0; }}

{extracted_fns}

static void reset_runtime_state(const char *tid) {{
    size_t n = strlen(tid);
    memcpy(g_title_id, tid, n + 1);
    vfs_adapt[0] = 0;
    vfs_config[0] = 0;
    vfs_global[0] = 0;
    g_last_adapt_sig = 0;
    g_mem_hibernate_count = 0;
    g_vout_fg_inited = 0;
    g_injects = 0;
    g_flips = 0;
    g_vo_submit_flips = 0;
    g_shave_n = 0;
    g_ngame = 3;
    g_game_idx_hi = 3;
    g_gv_cal_winner = -1;
    g_gv_cal_locked = 0;
    cfg_defaults((fg_config_t *)&g_cfg);
}}

int main(void) {{
    // Test 1: Runtime memory hibernation + 2-buffer ring -> save_adapt_ini -> load_adapt_ini
    reset_runtime_state("CUSA04607");
    if (read_two_buffer_raw() != 0) return 1;
    g_mem_hibernate_count = 1;
    g_ngame = 2;
    g_game_idx_hi = 4; // multi-group 2-buffer
    if (save_adapt_ini() != 1) return 2;
    // Duplicate call without state change must not rewrite
    if (save_adapt_ini() != 0) return 3;

    fg_config_t loaded;
    cfg_defaults(&loaded);
    if (load_adapt_ini(&loaded, NULL) != 1) return 4;
    if (loaded.two_buffer != 1 || loaded.shave_mode != 2 || loaded.reg_mode != 1) return 5;
    if (read_two_buffer_raw() != 1) return 6;

    // Test 2: Post-menu world-arena shaving (g_flips > 120 && g_shave_n > 0)
    reset_runtime_state("CUSA09311");
    g_flips = 208;
    g_shave_n = 1;
    if (save_adapt_ini() != 1) return 7;
    cfg_defaults(&loaded);
    if (load_adapt_ini(&loaded, NULL) != 1) return 8;
    if (loaded.shave_mode != 2 || loaded.reg_mode != 1) return 9;

    // Test 3: VideoOut fallback (g_vout_fg_inited > 0 && g_injects == 0) + calibrated gv_conv
    reset_runtime_state("CUSA00552");
    g_vout_fg_inited = 1;
    g_injects = 0;
    g_gv_cal_winner = 9;
    g_gv_cal_locked = 1;
    if (save_adapt_ini() != 1) return 10;

    g_gv_cal_winner = -1;
    load_early_adapt_cfg();
    if (g_cfg.vout_path != 1 || g_cfg.gv_conv != 9 || g_gv_cal_winner != 9) return 11;

    // Test 4: Incremental merge with existing adapt-<TITLE>.ini
    g_ngame = 2;
    if (save_adapt_ini() != 1) return 12;
    cfg_defaults(&loaded);
    if (load_adapt_ini(&loaded, NULL) != 1) return 13;
    if (loaded.two_buffer != 1 || loaded.vout_path != 1 || loaded.gv_conv != 9) return 14;

    return 0;
}}
"""
    c_path.write_text(c_source, encoding="utf-8")
    llvm = pathlib.Path(cfg["llvm_bin"])
    subprocess.run(
        [
            str(llvm / "clang.exe"),
            "--target=x86_64-pc-windows-msvc",
            "-O0",
            "-fno-builtin",
            "-emit-llvm",
            "-I",
            str(ROOT / "overlay"),
            "-c",
            str(c_path),
            "-o",
            str(bc_path),
        ],
        check=True,
    )
    subprocess.run([str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bc_path)], check=True)


def test_telemetry_corpus_analyzer() -> None:
    """Test tools/analyze_telemetry_corpus.py across all 7 health classes and required CUSAxxxxx cases."""
    forbidden_titles = ["ForbiddenSampleTitleAlpha", "ForbiddenSampleTitleBeta", "SecretProjectOmega"]

    report_bundle = """VCFG bug report
app version: 2.31
firmware: 11.00
time: 1761180000
debug log: on
plugin state: 1

==== summary (flight recorder, trace-<TITLE>.txt, 6 files in /data/bb_probe) ====
CUSA00552, last game start: version: 2.31 | seconds since start: 44.8 | first frame yes, GPU memory yes, FG frames no, OSD/post injects no, menu used no
    reason? inert='INERT: NO FLIP TAIL'
CUSA04607, last game start: version: 2.31 | seconds since start: 38.2 | first frame yes, GPU memory yes, FG frames no, OSD/post injects no, menu used no
    reason? inert='INERT: NO MBUF (0)'
CUSA08364, last game start: version: 2.31 | seconds since start: 120.5 | first frame yes, GPU memory yes, FG frames yes, OSD/post injects yes, menu used yes

==== games ====
CUSA00552 version=01.00 installed=1 experimental=0 fg=1 preset=0 sharp=40 enhance=1 overlay=0 engine_choice=0 engine=1 buf_choice=0 buf=0 title='ForbiddenSampleTitleAlpha'
CUSA04607 version=01.02 installed=1 experimental=0 fg=1 preset=0 sharp=40 enhance=1 overlay=0 engine_choice=0 engine=1 buf_choice=0 buf=0 title='ForbiddenSampleTitleBeta'
CUSA08364 version=01.05 installed=1 experimental=0 fg=1 preset=0 sharp=40 enhance=1 overlay=0 engine_choice=0 engine=1 buf_choice=0 buf=0 title='SecretProjectOmega'
CUSA01111 version=01.00 installed=1 experimental=0 fg=0 preset=0 sharp=40 enhance=0 overlay=0 engine_choice=0 engine=1 buf_choice=0 buf=0 title='ForbiddenSampleTitleAlpha'

==== flight recorder, last game start (/data/bb_probe/trace-CUSA00552.txt, 1024 bytes) ====
[bb_probe] first SubmitAndFlip: count=1 flipMode=1 title=CUSA00552
[bb_probe] RegisterBuffers handle=1 start=0 num=3
[bb_probe] gnm #120 t=4.100s dt=33.33ms idx=0 mode=1
[bb_probe] inject skipped: tail marker not found (nd=128)
[bb_probe] injection mode 8 (gnm_flip, title CUSA00552, known=1, fg_off=0, preset=0, sharpness=40%, enhance=0, overlay=0, inert='INERT: NO FLIP TAIL'), work memory garlic
[bb_probe] first VideoOutSubmitFlip: handle=1 index=0 mode=1 arg=0 gnmSubmits=420 title=CUSA00552
[bb_probe] STALL SNAPSHOT (4s no flip at flips=149 vo=1344): curBuf=1 pend=0 gcq=0

==== flight recorder, last game start (/data/bb_probe/trace-CUSA04607.txt, 1024 bytes) ====
[bb_probe] first SubmitAndFlip: count=1 flipMode=1 title=CUSA04607
[bb_probe] gnm #1 t=0.500s dt=33.33ms idx=0 mode=1
[bb_probe] gnm #2 t=0.533s dt=33.33ms idx=1 mode=1
[bb_probe] gnm #3 t=0.566s dt=33.33ms idx=0 mode=1
[bb_probe] injection mode 8 (gnm_flip, title CUSA04607, known=1, fg_off=0, preset=0, sharpness=40%, enhance=0, overlay=0, inert='INERT: NO MBUF (0)'), work memory garlic
[bb_probe] STAT gnm: 30.0 flips/s, max dt 34.1 ms (window 2.0s) flips=960

==== flight recorder, last game start (/data/bb_probe/trace-CUSA08364.txt, 1024 bytes) ====
[bb_probe] first SubmitAndFlip: count=1 flipMode=1 title=CUSA08364
[bb_probe] RegisterBuffers handle=1 start=0 num=3
[bb_probe] mbufs: 3 extra buffer(s) of 0x800000 at 0x200000000 (early), game RegisterBuffers extended to 6 = 0x00000000
[bb_probe] injection mode 8 (gnm_flip, title CUSA08364, known=1, fg_off=0, preset=0, sharpness=40%, enhance=1, overlay=1, inert=''), work memory garlic
[bb_probe] inject #120: count 1->2, our DCB 412 dw, out buf[0]=200000000
[bb_probe] mode4 #120: gnm flip(mid idx 3)=0x00000000, gnm flip(cur idx 0)=0x00000000
[bb_probe] GPU time of our passes: pyramid 0.42 + motion 1.18 + warp/post 0.55 = 2.15 ms (+ fence wait 0.05) | game span avg 18.2 max 22.4 ms

==== crash dump (/data/bb_probe/crash-CUSA09311.txt, 1024 bytes) ====
=== CRASH SIGSEGV (sig=11 code=1) at 24.512s | flips=208 vo_flips=0 in_submit=0 alloc_fail=1 shave=1 tight=1 | tid=100123 'MainThrd' ===
  FAULT_ADDR: si_addr=0x0000000000000000 (?)
  MEM_STATE: dm_total=0x12d600000 free_2mb=0x0 free_16kb=0x0
  API_COUNTS: allocs=89(fail=1) maps=88 releases=4 batch_maps=0(fail=0) shaves=1(shaved_bytes=0x800000)
  FG_STATE: res=1920x1080 pitch=1920 fmt=0x80000000(sup=1) ngame=3 nmid=1 mbase=15 nbuf=16 mbuf_state=1 gpu_state=1 pcopy=0/0 safe=0/0
  LAST API CALLS (89 total):
    #89 24.510 [t:100123 f:208] AllocDM(len=0x14a00000 al=0x200000 ty=3) = 0x80020023 phys=0x0
=== END CRASH REPORT ===

==== crash dump (/data/bb_probe/crash-CUSA11456.txt, 1024 bytes) ====
=== CRASH SIGSEGV (sig=11 code=2) at 11.200s | flips=125 vo_flips=0 in_submit=1 alloc_fail=0 shave=1 tight=1 | tid=100088 'Render' ===
  FAULT_ADDR: si_addr=0x0000001184000000 (game_arena_va(0x1184000000))
  API_COUNTS: allocs=14(fail=0) maps=6 releases=0 batch_maps=8(fail=0) shaves=1(shaved_bytes=0x2000000)
  FG_STATE: res=1920x1080 pitch=1920 fmt=0x80000000(sup=1) ngame=2 nmid=2 mbase=12 nbuf=16 mbuf_state=1 gpu_state=1 pcopy=1/1 safe=0/0
  LAST API CALLS (22 total):
    #18 6.100 [t:100088 f:0] RegisterBuffers enter: handle=1 start=2 num=2
    #19 6.120 [t:100088 f:0] game BatchMap(n=4 out=4 flags=0x0 e0:op=0 phys=0x110000000 len=0x4000000 va=0x1180000000) = 0x00000000
=== END CRASH REPORT ===

==== flight recorder, last game start (/data/bb_probe/trace-CUSA01111.txt, 512 bytes) ====
[bb_probe] config (init): framegen=0 mode=8 preset=0 sharpness=40 enhance=0 hud_protect=1 game_vectors=0 fps_lock=30 overlay=0 opacity=80%
[bb_probe] injection mode 8 (gnm_flip, title CUSA01111, known=1, fg_off=1, preset=0, sharpness=40%, enhance=0, overlay=0, inert=''), work memory garlic
"""

    boot_safe_fallback = """#01 0.001s [f:0] plugin_load enter: title=CUSA02222 (family=CUSA02222) version=2.31 engine=classic
#02 4.100s [f:120] safe mode frame 120: check (title CUSA02222, 1920x1080 fmt=0x80000000 ngame=2 idx_hi=2 detected60=0 game_alloc_fail=0)
#03 4.102s [f:120] safe mode frame 120: refused (NO DISPLAY MEM) -> the game stays native
"""

    boot_loading_only = """#01 0.001s [f:0] plugin_load enter: title=CUSA03333 (family=CUSA03333) version=2.31 engine=classic
#02 1.200s [f:1] SubmitAndFlip #1 enter: count=2 handle=1 index=0 flipMode=1
#03 2.500s [f:42] gnm #42 t=2.500s dt=33.33ms idx=0 mode=1
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        tpath = pathlib.Path(tmpdir)
        (tpath / "vcfg_report.txt").write_text(report_bundle, encoding="utf-8")
        (tpath / "boot-CUSA02222.txt").write_text(boot_safe_fallback, encoding="utf-8")
        (tpath / "boot-CUSA03333.txt").write_text(boot_loading_only, encoding="utf-8")

        # Also create a .zip archive containing the same corpus to test zip ingestion
        zpath = tpath / "telemetry_bundle.zip"
        with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("reports/vcfg_report.txt", report_bundle)
            zf.writestr("reports/boot-CUSA02222.txt", boot_safe_fallback)
            zf.writestr("reports/boot-CUSA03333.txt", boot_loading_only)

        for target in (tpath, zpath):
            results = atc.analyze_path(target)
            by_id = {r.title_id: r for r in results}

            assert set(by_id.keys()) == {
                "CUSA00552",
                "CUSA01111",
                "CUSA02222",
                "CUSA03333",
                "CUSA04607",
                "CUSA08364",
                "CUSA09311",
                "CUSA11456",
            }

            # 1. CUSA00552: Dual Flip-Path Transition
            r_00552 = by_id["CUSA00552"]
            assert r_00552.classification == "INERT_NO_MBUF"
            assert "Dual Flip-Path Transition" in r_00552.root_cause
            assert "vout_path = 1" in r_00552.adapt_ini

            # 2. CUSA04607: Late/split RegisterBuffers (nbuf=0, 2-buffer ring)
            r_04607 = by_id["CUSA04607"]
            assert r_04607.classification == "INERT_NO_MBUF"
            assert "two_buffer = 1" in r_04607.adapt_ini
            assert "reg_mode = 2" in r_04607.adapt_ini

            # 3. CUSA09311: Post-Menu World-Arena Allocation OOM crash
            r_09311 = by_id["CUSA09311"]
            assert r_09311.classification == "CRASH_OOM"
            assert "Post-Menu World-Arena" in r_09311.root_cause
            assert "shave_mode = 2" in r_09311.adapt_ini
            assert "reg_mode = 1" in r_09311.adapt_ini

            # 4. CUSA08364: Healthy ACTIVE_60FPS
            r_08364 = by_id["CUSA08364"]
            assert r_08364.classification == "ACTIVE_60FPS"
            assert r_08364.adapt_ini == ""

            # 5. CUSA11456: Multi-group RegisterBuffers + BatchMap SIGSEGV
            r_11456 = by_id["CUSA11456"]
            assert r_11456.classification == "CRASH_SIGSEGV"
            assert "BatchMap" in r_11456.root_cause
            assert "two_buffer = 1" in r_11456.adapt_ini
            assert "reg_mode = 1" in r_11456.adapt_ini

            # 6. CUSA01111: USER_DISABLED
            assert by_id["CUSA01111"].classification == "USER_DISABLED"

            # 7. CUSA02222: SAFE_FALLBACK
            assert by_id["CUSA02222"].classification == "SAFE_FALLBACK"

            # 8. CUSA03333: LOADING_ONLY
            assert by_id["CUSA03333"].classification == "LOADING_ONLY"

            # Verify Markdown and JSON outputs contain zero game titles/names
            md_out = atc.format_markdown_report(results)
            js_out = atc.format_json_report(results)
            assert atc.verify_no_game_names(md_out, forbidden_titles)
            assert atc.verify_no_game_names(js_out, forbidden_titles)

        # Test CLI --write-adapt-dir
        adapt_out_dir = tpath / "adapt_out"
        rc = atc.main([str(zpath), "--write-adapt-dir", str(adapt_out_dir), "--out", str(tpath / "out.md")])
        assert rc == 0
        assert (adapt_out_dir / "adapt-CUSA00552.ini").exists()
        assert (adapt_out_dir / "adapt-CUSA04607.ini").exists()
        assert (adapt_out_dir / "adapt-CUSA09311.ini").exists()
        assert (adapt_out_dir / "adapt-CUSA11456.ini").exists()
        assert not (adapt_out_dir / "adapt-CUSA08364.ini").exists()


if __name__ == "__main__":
    test_classic_adapt_ini_roundtrip()
    print("PASS: test_classic_adapt_ini_roundtrip (LLVM bitcode C round-trip)")
    test_telemetry_corpus_analyzer()
    print("PASS: test_telemetry_corpus_analyzer (7 health classes + zip/dir + zero game names)")
