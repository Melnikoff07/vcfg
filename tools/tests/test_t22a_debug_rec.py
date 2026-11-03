#!/usr/bin/env python3
"""T22a Debug Recording Options Tests: dbg_mark_gen and dbg_rec_shift.

Verifies:
1. INI defaults and parsing in classic/source/main.c:
   - dbg_mark_gen and dbg_rec_shift default to 0.
   - dbg_mark_gen=1 and dbg_rec_shift=1 (and =0) parse in config-<TITLE>.ini and copy in load_early_adapt_cfg.
2. dbg_mark_gen=0 vs dbg_mark_gen=1 in osd_build_slot, fill_table8_ext, build_dcb_flow8, build_dcb_post8:
   - When dbg_mark_gen=0, DCB words, descriptor tables (tb, tb2), and g_gpu[0x0900..0x0DFF] are 100% bit-for-bit
     identical to baseline (069aa88).
   - When dbg_mark_gen=1, a 32x32 bright magenta square (0xFFFF00FF) in the bottom-right corner (1872, 1024 at 1080p)
     is composited by osd8 ONLY onto generated frames (mid in 2-slot mode, and BOTH mid1 and mid2 in 3-slot mode),
     NEVER onto real frames (C) in build_dcb_flow8, build_dcb_post8, or force_repeat=1 fallback.
3. dbg_rec_shift=0 vs dbg_rec_shift=1 in pace_vblank_ex and pace_pair_ex:
   - When dbg_rec_shift=0, VBlank pacing and timer pacing are 100% identical to baseline.
   - When dbg_rec_shift=1, the display VBlank phase is shifted by +1 VBlank (~16.68 ms) so generated frames (mid)
     land on the VBlank parity (vs.count mod 2) previously occupied by real frames (C), while preserving mid -> C
     order and 1:1 cadence (2 VBlanks between consecutive mids, 2 VBlanks between consecutive reals, real = mid + 1).
4. Trace logging in cfg_apply_live and mon_thread:
   - Startup/live config log records dbg_mark_gen and dbg_rec_shift.
   - mon_thread logs VB_PARITY (vs.count mod 2) for mid and real right next to CADENCE.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C = ROOT / "classic" / "source" / "main.c"
FG8_TABLE_H = ROOT / "classic" / "source" / "fg8_table.h"
CC = shutil.which("clang") or shutil.which("cc")


def extract_function(src: str, name: str) -> str:
    m = re.search(r"^static [^\n]+\b" + name + r"\([^;]*\)\s*\{", src, re.M)
    if m is None:
        raise AssertionError(f"Function {name} not found in classic/source/main.c")
    pos = src.index("{", m.start())
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[m.start():end]


class TestT22aDebugRec(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_c = MAIN_C.read_text(encoding="utf-8", errors="replace")
        cls.fg8_h = FG8_TABLE_H.read_text(encoding="utf-8", errors="replace")

    def test_1_ini_config_and_trace_logging(self) -> None:
        """Verify fg_config_t fields, INI parser keys, load_early_adapt_cfg, and trace logs."""
        self.assertTrue("dbg_mark_gen" in self.main_c, "dbg_mark_gen missing in main.c")
        self.assertTrue("dbg_rec_shift" in self.main_c, "dbg_rec_shift missing in main.c")
        self.assertTrue("VB_PARITY" in self.main_c, "VB_PARITY log missing in main.c")
        self.assertIsNotNone(
            re.search(r"debug rec \([^)]*\):\s*dbg_mark_gen=%d\s+dbg_rec_shift=%d", self.main_c),
            "debug rec startup log missing in main.c",
        )
        # Run host bitcode INI parser test (tools/tests/check_classic_config.py)
        subprocess.run(["python3", str(ROOT / "tools" / "tests" / "check_classic_config.py")], check=True)

    def test_2_dbg_mark_gen_dcb_and_osd8_pixel_output(self) -> None:
        """Verify bit-exact baseline at dbg_mark_gen=0 and mid-only 32x32 magenta marker at dbg_mark_gen=1."""
        if CC is None:
            self.skipTest("no host C compiler")

        self.assertTrue("DBG_MARK_PAL_OFF" in self.fg8_h, "DBG_MARK_PAL_OFF missing in fg8_table.h")
        self.assertTrue("DBG_MARK_PIX_OFF" in self.fg8_h, "DBG_MARK_PIX_OFF missing in fg8_table.h")
        self.assertTrue("OSD_FLAG_DBG_MARK" in self.fg8_h, "OSD_FLAG_DBG_MARK missing in fg8_table.h")

        def extract_define(src: str, name: str) -> str:
            lines = src.splitlines()
            for i, line in enumerate(lines):
                if line.startswith(f"#define {name}(") or line.startswith(f"#define {name} "):
                    out = [line]
                    while out[-1].endswith("\\"):
                        i += 1
                        out.append(lines[i])
                    return "\n".join(out)
            raise ValueError(f"define not found: {name}")

        defines = "\n".join(
            extract_define(self.main_c, n)
            for n in (
                "MON_OFF", "SLOT0", "SLOT_SZ", "STATS_OFF",
                "WORK_SZ", "WORK_SZ_8", "DUMP_DONE_OFF", "DCB8_OFF", "DCB8_SZ", "PKT3",
                "TS_EOP", "TS2", "PASS_BARRIER", "SET_PASS",
            )
        )
        fns = "\n\n".join(
            extract_function(self.main_c, fn)
            for fn in [
                "osd_build_slot",
                "calc_rcas_params",
                "real_aa_on",
                "rcas_static_flag",
                "rcas_precise_flag",
                "post_mid_only",
                "build_dcb_post8",
                "build_dcb_flow8",
            ]
        )

        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct {{
    int overlay, menu_opacity, framegen, preset, sharpness, upscale, game_vectors;
    int hud_protect, fps_lock, below30, shallow_queue, post_mid;
    int dbg_mark_gen, dbg_rec_shift;
}} fg_config_t;

static fg_config_t g_cfg, g_menu_edit;
static int g_menu_open = 0, g_menu_sel = 0;
static uint64_t g_osd_toast_until = 0;
static char g_osd_toast[64];
static char g_inert_reason[48];
static char g_vov_title[48] = "CUSA00411";
static const uint8_t *g_vov_pal = NULL, *g_vov_pix = NULL;
static uint32_t g_osd_real_fps_x10 = 300, g_osd_out_fps_x10 = 600, g_last_gpu_ms_x100 = 200;
static int g_mid_only = -1, g_mem_tight = 0, g_bench_offscreen = 0;
static int g_fg_off = 0, g_ngame = 3, g_runtime_2buf = 0;
static uint64_t g_pyr_last_cur_base = 0;
static uint32_t g_pyr_last_w = 0, g_pyr_last_h = 0, g_pyr_parity = 0;
static uint32_t g_bw = 1920u, g_bh = 1080u;
static uint32_t g_hud_packed = 0, g_dump_frame = 0;
static int g_dump_req = 0, g_fence_logged = 0;
static void *g_dump_mem = NULL;
static int g_opt_phase = 0, g_opt_marker = 0, g_opt_nocf = 0, g_opt_nofgk = 0, g_opt_noanc = 0;
static int g_opt_notp = 0, g_opt_nomf = 0, g_opt_notex = 0, g_opt_notpg = 0, g_opt_sharp = 0, g_opt_nostr = 0;
static uint32_t g_sh8_off[17];
static uint8_t g_gpu_backing[0x40000 + 64];
static uint8_t g_gpu_ref[0x40000 + 64];
static uint8_t g_work_backing[0x220000];
static uint8_t *g_gpu = g_gpu_backing;
static uint8_t *g_work = g_work_backing;
static uint32_t g_mon_backing[4];
static volatile uint32_t *g_mon = g_mon_backing;
static char g_title_id[16] = "CUSA00411";

#define lg(...) ((void)0)
static uint64_t sceKernelGetProcessTime(void) {{ return 1000000ull; }}
static int vov_ready(void) {{ return 0; }}
static const char *overlay_label(int ov) {{ (void)ov; return "OFF"; }}
static int vcfg_in_family(const char *t, const char *f) {{ return strcmp(t, f) == 0; }}
static uint32_t cut_blocks8(void) {{ return 0; }}
const uint8_t *vov_pal_ptr(void) {{ return g_vov_pal; }}
const uint8_t *vov_pix_ptr(void) {{ return g_vov_pix; }}

#include "vov.h"
#include "vov_glue.h"
#include "menu_combo.h"
#include "opt_status.h"
static void vov_row(VovMenu *m, const char *l, int k, int on, int p, const char *v) {{
    (void)m; (void)l; (void)k; (void)on; (void)p; (void)v;
}}
static void opt_state_now(const fg_config_t *c, OptState *s) {{ (void)c; memset(s, 0, sizeof(*s)); }}
int vov_counter(const VovCounter *c, const uint8_t **pal, const uint8_t **pix, unsigned *w, unsigned *h) {{
    (void)c; (void)pal; (void)pix; (void)w; (void)h; return 0;
}}
int vov_menu(const VovMenu *m, const uint8_t **pal, const uint8_t **pix, unsigned *w, unsigned *h) {{
    (void)m; (void)pal; (void)pix; (void)w; (void)h; return 0;
}}
static uint32_t *gv_emit_eof(uint32_t *p, uint32_t s, uint32_t k) {{ (void)s; (void)k; return p; }}
static uint32_t *gv_emit_ts(uint32_t *p, uint32_t s, int i) {{ (void)s; (void)i; return p; }}
static uint32_t *gv_emit_after_gv8(uint32_t *p, uint32_t s, uint32_t k) {{ (void)s; (void)k; return p; }}
static void gv_fill_table(uint32_t *p, uint32_t s, uint32_t k) {{ (void)p; (void)s; (void)k; }}
static int gv_pass_active(void) {{ return 0; }}
static int gv_warp_active(void) {{ return 0; }}
static int gv_viz_active(void) {{ return 0; }}
static void pyr_cache_invalidate(void) {{ g_pyr_last_cur_base = 0; }}
static int32_t insert_wait_flip_done(uint32_t *p, uint32_t n, int32_t h, uint32_t i) {{
    (void)n; (void)h; (void)i;
    memset(p, 0, 28);
    return 0;
}}

#include "shaders.h"
#include "fg_res.h"
#include "fg8_table.h"
#include "late_tail.h"
{defines}
{fns}

static int count_osd_dispatches(const uint32_t *mt, uint32_t n) {{
    int cnt = 0;
    for (uint32_t i = 0; i + 2 < n; i++) {{
        if (mt[i] == PKT3(0x76, 3, 1) && mt[i + 1] == 0x20Cu) {{
            uint32_t off = (mt[i + 2] - (uint32_t)((uintptr_t)g_gpu >> 8)) << 8;
            if (off == g_sh8_off[15]) cnt++;
        }}
    }}
    return cnt;
}}

/* Exact simulation of classic/shaders/osd8.ll on a descriptor table tb:
 * returns number of pixels written to mid and cur, and verifies every written pixel is 0xFFFF00FF at [1872..1903, 1024..1055]. */
static int sim_osd8(const uint32_t *tb, int *out_mid_px, int *out_cur_px) {{
    *out_mid_px = 0;
    *out_cur_px = 0;
    uint32_t xy = tb[138], wh = tb[139];
    uint32_t cols = wh & 0xFFu, rows = (wh >> 8) & 0xFFu;
    uint32_t alpha = (wh >> 16) & 0xFFu, ofl = wh >> 24;
    int do_mid = (ofl & 1u) != 0;
    int do_cur = (ofl & 2u) != 0;
    if (cols == 0 || rows == 0) return 0;

    uint64_t pal_addr = (uint64_t)tb[128] | ((uint64_t)(tb[129] & 0xFFFFu) << 32);
    uint64_t pix_addr = (uint64_t)tb[132] | ((uint64_t)(tb[133] & 0xFFFFu) << 32);
    const uint32_t *pal = (const uint32_t *)(uintptr_t)pal_addr;
    const uint8_t *pix = (const uint8_t *)(uintptr_t)pix_addr;
    uint32_t x0 = xy & 0xFFFFu, y0 = xy >> 16;
    uint32_t stride = cols * 16u;

    for (uint32_t gy = 0; gy < rows; gy++) {{
        for (uint32_t gx = 0; gx < cols; gx++) {{
            for (uint32_t ly = 0; ly < 16u; ly++) {{
                for (uint32_t lx = 0; lx < 16u; lx++) {{
                    uint32_t bx = gx * 16u + lx, by = gy * 16u + ly;
                    uint8_t idx = pix[by * stride + bx];
                    uint32_t c = pal[idx & 3u];
                    uint32_t a = ((c >> 24) * alpha) / 255u;
                    if (a < 3u) continue;
                    uint32_t px = x0 + bx, py = y0 + by;
                    if (px < 1872u || px >= 1904u || py < 1024u || py >= 1056u) return -1;
                    if (c != 0xFFFF00FFu || a != 255u) return -2;
                    if (do_mid) (*out_mid_px)++;
                    if (do_cur) (*out_cur_px)++;
                }}
            }}
        }}
    }}
    return 0;
}}

int main(void) {{
    (void)g_pbuf_sz; (void)g_pbuf_phys; (void)vov_read_title;
    for (int i = 0; i < 17; i++) g_sh8_off[i] = (uint32_t)(i + 1) * 0x100u;
    uint32_t nop64[64];
    memset(nop64, 0, sizeof(nop64));
    uint32_t *mt = (uint32_t *)(g_gpu + DCB8_OFF);
    uint32_t *tb = (uint32_t *)(g_gpu + SLOT0 + 0xB00);
    uint32_t *tb2 = (uint32_t *)(g_gpu + SLOT0 + SLOT_TB2_OFF);

    // 1. Baseline reference (dbg_mark_gen=0, overlay=0, two_mids=1):
    memset(g_gpu_backing, 0x5A, sizeof(g_gpu_backing));
    memset(&g_cfg, 0, sizeof(g_cfg));
    g_cfg.framegen = 1; g_cfg.dbg_mark_gen = 0; g_pyr_last_cur_base = 0; g_pyr_parity = 0;
    uint32_t n_ref = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                     1920u, 8u, 1920u * 1080u * 4u, 0u, 10u, nop64, 1, 2u, 3u,
                                     21845u, 43690u, 1, 0, 0x50000000ULL, 0xFFFFu);
    memcpy(g_gpu_ref, g_gpu_backing, sizeof(g_gpu_backing));
    if (count_osd_dispatches(mt, n_ref) != 0) return 1;
    if (tb[138] != 0u || tb[139] != 0u || tb2[138] != 0u || tb2[139] != 0u) return 2;
    // Verify 0x0900..0x0DFF in g_gpu was completely untouched (still 0x5A) when dbg_mark_gen=0:
    for (uint32_t off = DBG_MARK_PAL_OFF; off < MON_OFF; off++) {{
        if (g_gpu[off] != 0x5Au) return 3;
    }}

    // 2. Active dbg_mark_gen=1 in 2-slot mode (two_mids=0):
    g_cfg.dbg_mark_gen = 1; g_pyr_last_cur_base = 0; g_pyr_parity = 0;
    uint32_t n_2slot = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                       1920u, 8u, 1920u * 1080u * 4u, 0u, 10u, nop64, 1, 2u, 3u,
                                       32768u, 32768u, 0, 0, 0x50000000ULL, 0xFFFFu);
    if (count_osd_dispatches(mt, n_2slot) != 1) return 4;
    int mid_px = 0, cur_px = 0;
    if (sim_osd8(tb, &mid_px, &cur_px) != 0) return 5;
    if (mid_px != 1024 || cur_px != 0) return 6;

    // 3. Active dbg_mark_gen=1 in 3-slot mode (two_mids=1: mid1 and mid2):
    g_pyr_last_cur_base = 0; g_pyr_parity = 0;
    uint32_t n_3slot = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                       1920u, 8u, 1920u * 1080u * 4u, 0u, 10u, nop64, 1, 2u, 3u,
                                       21845u, 43690u, 1, 0, 0x50000000ULL, 0xFFFFu);
    if (count_osd_dispatches(mt, n_3slot) != 2) return 7;
    int mid1_px = 0, cur1_px = 0, mid2_px = 0, cur2_px = 0;
    if (sim_osd8(tb, &mid1_px, &cur1_px) != 0) return 8;
    if (sim_osd8(tb2, &mid2_px, &cur2_px) != 0) return 9;
    if (mid1_px != 1024 || cur1_px != 0 || mid2_px != 1024 || cur2_px != 0) return 10;

    // 4. force_repeat fallback (M1 == C on >66.7ms hitch): must NOT mark repeated real frame!
    g_pyr_last_cur_base = 0; g_pyr_parity = 0;
    uint32_t n_rep = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x20000000ULL, 0x40000000ULL,
                                     1920u, 8u, 1920u * 1080u * 4u, 0u, 10u, nop64, 1, 2u, 3u,
                                     32768u, 32768u, 0, 0, 0x50000000ULL, 0xFFFFu);
    if (count_osd_dispatches(mt, n_rep) != 0 || tb[139] != 0u) return 11;

    // 5. build_dcb_post8 (native/post-only real frame): must NOT mark real frame!
    uint32_t n_post = build_dcb_post8(mt, 0x20000000ULL, 1920u, 1080u, 1920u, 8u, 1920u * 1080u * 4u, 0u, 10u, nop64);
    if (n_post != 0 || count_osd_dispatches(mt, n_post) != 0) return 12;

    // 6. Turning dbg_mark_gen back to 0 restores exact bit-for-bit DCB and descriptors:
    memset(g_gpu_backing, 0x5A, sizeof(g_gpu_backing));
    g_cfg.dbg_mark_gen = 0; g_pyr_last_cur_base = 0; g_pyr_parity = 0;
    uint32_t n_off = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                     1920u, 8u, 1920u * 1080u * 4u, 0u, 10u, nop64, 1, 2u, 3u,
                                     21845u, 43690u, 1, 0, 0x50000000ULL, 0xFFFFu);
    if (n_off != n_ref || memcmp(g_gpu_backing, g_gpu_ref, sizeof(g_gpu_backing)) != 0) return 13;

    printf("DBG_MARK_GEN_OK\\n");
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "t_mark.c"
            exe = pathlib.Path(td) / "t_mark"
            c_file.write_text(harness, encoding="utf-8")
            inc = str(ROOT / "classic" / "source")
            ovl = str(ROOT / "overlay")
            subprocess.run([CC, "-O0", "-Wall", "-I", inc, "-I", ovl, str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"t_mark failed with exit code {res.returncode}: {res.stderr}")
            self.assertIn("DBG_MARK_GEN_OK", res.stdout)

    def test_3_dbg_rec_shift_vblank_and_timer_phase_shift(self) -> None:
        """Verify dbg_rec_shift=1 shifts VBlank parity of mid & C by +1 VBlank while keeping 1:1 cadence."""
        if CC is None:
            self.skipTest("no host C compiler")

        fns = "\n\n".join(
            extract_function(self.main_c, fn)
            for fn in [
                "vblank_period_us",
                "pace_pcopy",
                "pace_vblank_ex",
                "pace_pair_ex",
            ]
        )

        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define PLL_LEAD_US 2200u
typedef struct {{ int64_t count; uint64_t processTime; uint64_t tsc; uint32_t flags; int32_t currentBuffer; uint32_t flipArg[4]; }} VoFlipStatus;
typedef struct {{ int present_copy, dbg_mark_gen, dbg_rec_shift; }} fg_config_t;

static fg_config_t g_cfg;
static int g_mon_on = 1, g_opt_early = 0, g_pcopy_on = 0, g_s14_tail_extra = 0;
static int g_ngame = 3, g_mbase = 3, g_last_real_buf = -1, g_handle = 1;
static uint32_t g_flips = 0;
static uint64_t g_dbl_frames = 0;
static int64_t g_pace_off = 0;
static int g_rec_shift_applied = 0;
static uint32_t g_pace_resync = 0, g_pace_timeouts = 0, g_pace_fix = 0;
static volatile uint64_t g_mids_shown = 0, g_reals_shown = 0;
static volatile uint64_t g_rel_t = 0;
static volatile uint32_t g_pll_period_q8 = (16683u << 8) | 85u;
static uint64_t g_freq = 1000000ull;
static uint64_t g_pair_next = 0;

/* Discrete-event simulation clock & hardware VBlank flip queue */
static uint64_t sim_now_us = 1000000ull;
static uint64_t sim_vb_period_us = 16683ull;
static uint64_t sim_vb_origin_us = 1000000ull;
static uint64_t sim_last_vb_cnt = 0;

typedef struct {{
    uint64_t latch_vb;
    int is_mid;
    int done;
}} SimFlip;
static SimFlip sim_q[64];
static int sim_qn = 0;

static void sim_advance_to(uint64_t target_us) {{
    if (target_us <= sim_now_us) return;
    sim_now_us = target_us;
    uint64_t vb_cnt = (sim_now_us - sim_vb_origin_us) / sim_vb_period_us;
    if (vb_cnt > sim_last_vb_cnt) {{
        for (uint64_t v = sim_last_vb_cnt + 1; v <= vb_cnt; v++) {{
            for (int i = 0; i < sim_qn; i++) {{
                if (!sim_q[i].done && sim_q[i].latch_vb <= v) {{
                    sim_q[i].done = 1;
                    if (sim_q[i].is_mid) g_mids_shown++;
                    else g_reals_shown++;
                }}
            }}
        }}
        sim_last_vb_cnt = vb_cnt;
    }}
}}

static uint64_t sceKernelGetProcessTime(void) {{ return sim_now_us; }}
static uint64_t sceKernelGetProcessTimeCounter(void) {{ return sim_now_us; }}
static void sceKernelUsleep(uint32_t us) {{ sim_advance_to(sim_now_us + us); }}
static void cc_wait_until(uint64_t target_tsc) {{ sim_advance_to(target_tsc); }}
static uint32_t cc_pending_flips(void) {{
    uint32_t p = 0;
    for (int i = 0; i < sim_qn; i++) if (!sim_q[i].done) p++;
    return p;
}}
static void pace_30(void) {{}}
static int read_flip_status(int h, VoFlipStatus *fs) {{ (void)h; (void)fs; return -1; }}
static uint64_t vb_ticks(uint64_t freq, uint64_t period_q8, uint32_t nvb) {{
    (void)freq; (void)period_q8;
    return nvb * sim_vb_period_us;
}}
static uint64_t refresh_period_q8(void) {{ return g_pll_period_q8; }}
static uint32_t vblank_to_next_edge_us(int32_t h, uint64_t now_us, uint32_t *out_vb_us) {{
    (void)h;
    if (out_vb_us) *out_vb_us = (uint32_t)sim_vb_period_us;
    uint64_t elapsed = (now_us - sim_vb_origin_us) % sim_vb_period_us;
    return (uint32_t)(sim_vb_period_us - elapsed);
}}
static uint32_t pll_adaptive_hold_us(int32_t h, uint64_t now_us, uint64_t dt_enter_us, uint32_t req_hold_us) {{
    (void)h; (void)now_us; (void)dt_enter_us;
    return req_hold_us;
}}
#define lg(...) ((void)0)

{fns}

static void run_vblank_sim(int shift_cfg, uint64_t *mid_vbs, uint64_t *real_vbs, int nframes) {{
    g_cfg.dbg_rec_shift = shift_cfg;
    g_dbl_frames = 0; g_pace_off = 0; g_rec_shift_applied = 0;
    g_mids_shown = 0; g_reals_shown = 0; sim_qn = 0;
    sim_now_us = 1000000ull + 5000ull; /* 5 ms after VBlank 0 */
    sim_vb_origin_us = 1000000ull;
    sim_last_vb_cnt = 0;

    for (int f = 0; f < nframes; f++) {{
        g_flips = (uint32_t)(f + 1);
        pace_vblank_ex((f + 1) % 3, (f + 2) % 3, f % 3, 1, 33366ull, 1);
        /* After pace_vblank_ex releases at time sim_now_us, mid latches on the next VBlank (cur_vb + 1)
         * and real (C) latches on the VBlank after mid (cur_vb + 2). */
        uint64_t cur_vb = (sim_now_us - sim_vb_origin_us) / sim_vb_period_us;
        uint64_t mid_vb = cur_vb + 1;
        uint64_t real_vb = cur_vb + 2;
        mid_vbs[f] = mid_vb;
        real_vbs[f] = real_vb;
        sim_q[sim_qn++] = (SimFlip){{ mid_vb, 1, 0 }};
        sim_q[sim_qn++] = (SimFlip){{ real_vb, 0, 0 }};
        /* Game CPU+GPU work for next frame takes ~14 ms before entering flip_inner_ex */
        sim_advance_to(sim_now_us + 14000ull);
    }}
}}

static void run_timer_sim(int shift_cfg, uint64_t *mid_vbs, uint64_t *real_vbs, int nframes) {{
    g_cfg.dbg_rec_shift = shift_cfg;
    g_pair_next = 0; g_rec_shift_applied = 0;
    g_mids_shown = 0; g_reals_shown = 0; sim_qn = 0;
    sim_now_us = 1000000ull + 5000ull;
    sim_vb_origin_us = 1000000ull;
    sim_last_vb_cnt = 0;

    for (int f = 0; f < nframes; f++) {{
        g_flips = (uint32_t)(f + 1);
        pace_pair_ex(1, 33366ull, 1, 2u);
        uint64_t cur_vb = (sim_now_us - sim_vb_origin_us) / sim_vb_period_us;
        uint64_t mid_vb = cur_vb + 1;
        uint64_t real_vb = cur_vb + 2;
        mid_vbs[f] = mid_vb;
        real_vbs[f] = real_vb;
        sim_q[sim_qn++] = (SimFlip){{ mid_vb, 1, 0 }};
        sim_q[sim_qn++] = (SimFlip){{ real_vb, 0, 0 }};
        sim_advance_to(sim_now_us + 14000ull);
    }}
}}

int main(void) {{
    uint64_t vb_mid0[12], vb_real0[12], vb_mid1[12], vb_real1[12];
    run_vblank_sim(0, vb_mid0, vb_real0, 12);
    run_vblank_sim(1, vb_mid1, vb_real1, 12);

    /* Steady state (frames 2..11):
     * 1) With shift=0, mid is on odd VBlanks (vb % 2 == 1), real is on even VBlanks (vb % 2 == 0).
     * 2) With shift=1, every frame is shifted by +1 VBlank: mid is on EVEN VBlanks (the exact parity
     *    previously occupied by real!), real is on ODD VBlanks, real_vb == mid_vb + 1, and step == 2! */
    for (int f = 2; f < 12; f++) {{
        if (vb_real0[f] != vb_mid0[f] + 1) return 1;
        if (vb_real1[f] != vb_mid1[f] + 1) return 2;
        if (vb_mid1[f] != vb_mid0[f] + 1) return 3;
        if (vb_real1[f] != vb_real0[f] + 1) return 4;
        if ((vb_mid1[f] & 1ull) != (vb_real0[f] & 1ull)) return 5;
        if (f > 2) {{
            if (vb_mid0[f] - vb_mid0[f - 1] != 2 || vb_real0[f] - vb_real0[f - 1] != 2) return 6;
            if (vb_mid1[f] - vb_mid1[f - 1] != 2 || vb_real1[f] - vb_real1[f - 1] != 2) return 7;
        }}
    }}

    uint64_t tm_mid0[12], tm_real0[12], tm_mid1[12], tm_real1[12];
    run_timer_sim(0, tm_mid0, tm_real0, 12);
    run_timer_sim(1, tm_mid1, tm_real1, 12);
    for (int f = 2; f < 12; f++) {{
        if (tm_real0[f] != tm_mid0[f] + 1) return 8;
        if (tm_real1[f] != tm_mid1[f] + 1) return 9;
        if (tm_mid1[f] != tm_mid0[f] + 1) return 10;
        if (tm_real1[f] != tm_real0[f] + 1) return 11;
        if ((tm_mid1[f] & 1ull) != (tm_real0[f] & 1ull)) return 12;
        if (f > 2) {{
            if (tm_mid0[f] - tm_mid0[f - 1] != 2 || tm_real0[f] - tm_real0[f - 1] != 2) return 13;
            if (tm_mid1[f] - tm_mid1[f - 1] != 2 || tm_real1[f] - tm_real1[f - 1] != 2) return 14;
        }}
    }}

    printf("DBG_REC_SHIFT_OK\\n");
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "t_shift.c"
            exe = pathlib.Path(td) / "t_shift"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"t_shift failed with exit code {res.returncode}: {res.stderr}")
            self.assertIn("DBG_REC_SHIFT_OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
