#!/usr/bin/env python3
"""
T20 regression test: Sharp (preset=2) consistency between generated (mid) and real (C) frames.

Verifies Variant V1a ("sharpen C before warp8, no post-warp rcas8 on mid"):
1. rcas_precise_flag(rcas_flags) in classic/source/main.c sets bit 6 (64u, @resid8 10-bit center
   residual restoration) on all non-8_8_8_8 surfaces (g_t_dfmt != 10u) whenever (rcas_flags & 2u) != 0
   (any title when pass_cur is active, including Sharp preset=2 and post_mid=2), while keeping
   mid-only (target_mask=1u in Quality/Fast) gated to CUSA02299 for 100% Quality bit-exactness.
2. In build_dcb_flow8 (classic/source/main.c), when g_cfg.preset == 2 and !post_mid_only() and
   !g_bench_offscreen:
   - RCAS8 is dispatched BEFORE WARP8 (after PYR8..MF8 and PHASE8 / TS2(4)), with target_mask = 2u
     (pass_cur on T#cur in-place);
   - RCAS8 is NOT dispatched after WARP8 (neither on mid1 nor on mid2 when two_mids == 1), so
     mid is warped once from (P_sharpened, C_sharpened) and GPU cost is 1 RCAS8 pass per pair;
   - PCOPY8 (when active) runs at the end of build_dcb_flow8 (after WARP8), copying the sharpened C
     into g_pbuf / display copy buffer so the next pair's P is sharpened identically to C.
3. Numerical model verification (via tools/bench/vcfg_warp.py):
   - 10-bit (2_10_10_10) ramp 0..1023 keeps all 1024 codes exact (0 corrupted codes, max step 1)
     with bit 6 (precise=True), vs 768 corrupted codes and 4..5 code steps without bit 6.
   - On a stationary HUD contour across sequential pairs, V1a preserves P == C (100% on interior
     5x5 static HUD region) and yields 0.0% HUD edge overshoot between mid and real (mid == real),
     eliminating T19's double-sharpening on stationary HUD edges.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
import vcfg_warp as vw  # noqa: E402

MAIN_C_PATH = ROOT / "classic" / "source" / "main.c"
MAIN_C = MAIN_C_PATH.read_text(encoding="utf-8")
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
F32 = np.float32


def extract_c_function(src: str, func_name: str) -> str:
    idx = src.find(func_name + "(")
    if idx < 0:
        raise ValueError(f"Function {func_name} not found")
    line_start = src.rfind("\n", 0, idx) + 1
    brace_open = src.find("{", idx)
    depth = 0
    for i in range(brace_open, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[line_start:i + 1]
    raise ValueError(f"Unclosed brace in {func_name}")


def rcas8_gpu_sim(img: np.ndarray, con: float, emode: int, skip=None, still=None, precise=False) -> np.ndarray:
    out0 = vw.rcas8(img, con, emode, skip=skip, still=still)
    if not precise:
        return out0
    f = img.astype(F32)
    q = np.floor(np.clip(f, 0, 1) * F32(255.0) + F32(0.5)) * vw.RCP255
    resid = (f - q).astype(F32)
    return np.clip(out0 + resid, F32(0.0), F32(1.0)).astype(F32)


class TestT20SharpPreWarp(unittest.TestCase):
    @unittest.skipUnless(CC, "no host C compiler")
    def test_1_rcas_precise_flag_enables_bit6_for_cur_pass_on_all_10bit_titles(self):
        fn_src = extract_c_function(MAIN_C, "rcas_precise_flag")
        harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t g_t_dfmt = 10u;
static char g_title_id[16] = "CUSA08519";
static int vcfg_in_family(const char *tid, const char *fam) {{
    return strcmp(tid, fam) == 0;
}}

{fn_src}

int main(void) {{
    // 1. On 8_8_8_8 (g_t_dfmt == 10u), bit 6 (64u) is never set
    g_t_dfmt = 10u;
    strcpy(g_title_id, "CUSA02299");
    if (rcas_precise_flag(1u) & 64u) return 1;
    if (rcas_precise_flag(2u) & 64u) return 2;
    if (rcas_precise_flag(3u) & 64u) return 3;

    // 2. On 2_10_10_10 (g_t_dfmt == 9u != 10u):
    g_t_dfmt = 9u;
    // Quality/Fast mid-only (target_mask == 1u): bit 6 set ONLY for CUSA02299
    strcpy(g_title_id, "CUSA02299");
    if ((rcas_precise_flag(1u) & 64u) == 0u) return 4;
    strcpy(g_title_id, "CUSA08519");
    if ((rcas_precise_flag(1u) & 64u) != 0u) return 5;
    strcpy(g_title_id, "CUSA00411");
    if ((rcas_precise_flag(1u) & 64u) != 0u) return 6;

    // Sharp pass_cur (target_mask == 2u) or both (target_mask == 3u): bit 6 MUST be set on ALL titles!
    const char *titles[] = {{ "CUSA08519", "CUSA00411", "CUSA05571", "CUSA00557", "CUSA02299" }};
    for (int i = 0; i < 5; i++) {{
        strcpy(g_title_id, titles[i]);
        if ((rcas_precise_flag(2u | 128u) & 64u) == 0u) return 10 + i;
        if ((rcas_precise_flag(3u | 128u) & 64u) == 0u) return 20 + i;
    }}

    // Zero flags remain zero
    if (rcas_precise_flag(0u) != 0u) return 30;
    printf("RCAS_PRECISE_OK\\n");
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "t1.c"
            exe = pathlib.Path(td) / "t1"
            c_file.write_text(harness, encoding="utf-8")
            subprocess.run([CC, "-O0", "-Wall", str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"rcas_precise_flag check failed with code {res.returncode}: {res.stdout} {res.stderr}")
        self.assertEqual(res.stdout.strip(), "RCAS_PRECISE_OK")

    def test_2_build_dcb_flow8_has_pre_warp_rcas8_in_sharp_mode(self):
        flow8 = extract_c_function(MAIN_C, "build_dcb_flow8")
        # Must have pre-warp rcas8 dispatch between TS2(4); (after PHASE8) and WARP8
        pos_ts2_4 = flow8.index("TS2(4);")
        pos_warp8 = flow8.index("WARP8_RSRC1")
        pos_ts2_5 = flow8.index("TS2(5);")
        pos_post_rcas = flow8.index("if (rcas_flags1) {")
        self.assertLess(pos_ts2_4, pos_warp8)
        self.assertLess(pos_warp8, pos_ts2_5)
        self.assertLess(pos_ts2_5, pos_post_rcas)

        pre_warp_slice = flow8[pos_ts2_4:pos_warp8]
        self.assertIn("if (rcas_pre1) {", pre_warp_slice, "Missing pre-warp RCAS8 dispatch before WARP8")
        self.assertIn("RCAS8_RSRC1", pre_warp_slice)
        self.assertIn("PASS_BARRIER();", pre_warp_slice)

        # Verify sharp_pre_warp sets target_mask = 2u (cur only), moves rcas_flags1 to rcas_pre1, and zeroes rcas_flags1
        self.assertIn("sharp_pre_warp", flow8)
        self.assertIn("if (sharp_pre_warp) { rcas_pre1 = rcas_flags1; rcas_flags1 = 0u; }", flow8)

    @unittest.skipUnless(CC, "no host C compiler")
    def test_2b_build_dcb_flow8_pm4_pass_order_and_table_flags(self):
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
            extract_define(MAIN_C, n)
            for n in (
                "SLOT0", "SLOT_SZ", "STATS_OFF", "WORK_SZ", "WORK_SZ_8",
                "DUMP_DONE_OFF", "DCB8_OFF", "DCB8_SZ", "PKT3",
                "TS_EOP", "TS2", "PASS_BARRIER", "SET_PASS",
            )
        )
        fns = "\n\n".join(
            extract_c_function(MAIN_C, n)
            for n in (
                "calc_rcas_params", "real_aa_on", "rcas_static_flag",
                "rcas_precise_flag", "post_mid_only", "build_dcb_flow8",
            )
        )
        harness = f"""
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

typedef struct {{ int preset, sharpness, upscale, post_mid; }} fg_config_t;
static fg_config_t g_cfg;
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
static uint8_t g_work_backing[0x220000];
static uint8_t *g_gpu = g_gpu_backing;
static uint8_t *g_work = g_work_backing;
static uint32_t g_mon_backing[4];
static volatile uint32_t *g_mon = g_mon_backing;
static char g_title_id[16] = "CUSA08519";

#define lg(...) ((void)0)
static int vcfg_in_family(const char *t, const char *f) {{ return strcmp(t, f) == 0; }}
static uint32_t cut_blocks8(void) {{ return 0; }}
const uint8_t *vov_pal_ptr(void) {{ return NULL; }}
const uint8_t *vov_pix_ptr(void) {{ return NULL; }}
static void osd_build_slot(uint8_t *p, uint32_t *x, uint32_t *w, uint32_t flags) {{ (void)p; (void)flags; *x = 0; *w = 0; }}
static uint32_t *gv_emit_eof(uint32_t *p, uint32_t s, uint32_t k) {{ (void)s; (void)k; return p; }}
static uint32_t *gv_emit_ts(uint32_t *p, uint32_t s, int i) {{ (void)s; (void)i; return p; }}
static uint32_t *gv_emit_after_gv8(uint32_t *p, uint32_t s, uint32_t k) {{ (void)s; (void)k; return p; }}
static void gv_fill_table(uint32_t *p, uint32_t s, uint32_t k) {{ (void)p; (void)s; (void)k; }}
static int gv_pass_active(void) {{ return 0; }}
static int gv_warp_active(void) {{ return 0; }}
static int gv_viz_active(void) {{ return 0; }}
static int32_t insert_wait_flip_done(uint32_t *p, uint32_t n, int32_t h, uint32_t i) {{
    (void)n; (void)h; (void)i;
    memset(p, 0, 28);
    return 0;
}}

#include "shaders.h"
#include "fg_res.h"
#include "fg8_table.h"
{defines}
{fns}

static void scan_passes(const uint32_t *mt, uint32_t n, int *pos_warp1, int *pos_rcas1, int *pos_pcopy, int *rcas_cnt) {{
    *pos_warp1 = -1; *pos_rcas1 = -1; *pos_pcopy = -1; *rcas_cnt = 0;
    int k = 0;
    for (uint32_t i = 0; i + 2 < n; i++) {{
        if (mt[i] == PKT3(0x76, 3, 1) && mt[i + 1] == 0x20Cu) {{
            uint32_t off = (mt[i + 2] - (uint32_t)((uintptr_t)g_gpu >> 8)) << 8;
            if (off == g_sh8_off[5] && *pos_warp1 < 0) *pos_warp1 = k;
            if (off == g_sh8_off[14]) {{
                if (*pos_rcas1 < 0) *pos_rcas1 = k;
                (*rcas_cnt)++;
            }}
            if (off == g_sh8_off[16] && *pos_pcopy < 0) *pos_pcopy = k;
            k++;
        }}
    }}
}}

int main(void) {{
    (void)g_pbuf_sz; (void)g_pbuf_phys;
    for (int i = 0; i < 17; i++) g_sh8_off[i] = (uint32_t)(i + 1) * 0x100u;
    uint32_t nop64[64];
    memset(nop64, 0, sizeof(nop64));
    uint32_t *mt = (uint32_t *)(g_gpu + DCB8_OFF);
    uint32_t *tb = (uint32_t *)(g_gpu + SLOT0 + 0xB00);
    uint32_t *tb2 = (uint32_t *)(g_gpu + SLOT0 + SLOT_TB2_OFF);
    int pw, pr, pp, rcnt;

    // 1. Quality (preset=0, post_mid=0) on 10-bit CUSA08519: RCAS8 after WARP8, target=1u, st=16u, prec=0u
    g_cfg.preset = 0; g_cfg.sharpness = 40; g_cfg.upscale = 1; g_cfg.post_mid = 0;
    g_opt_sharp = 0; g_t_dfmt = 9u; strcpy(g_title_id, "CUSA08519"); g_mid_only = -1; g_bench_offscreen = 0;
    uint32_t n0 = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                  1920u, 10u, 1920u * 1080u * 4u, 0u, 10u, nop64, 1, 2u, 3u,
                                  32768u, 32768u, 0, 0, 0x50000000ULL, 0xFFFFu);
    scan_passes(mt, n0, &pw, &pr, &pp, &rcnt);
    if (!(pw >= 0 && pr > pw && pp > pr && rcnt == 1)) return 1;
    if ((tb[137] & 3u) != 1u || (tb[137] & 16u) == 0u || (tb[137] & 64u) != 0u) return 2;

    // 2. Sharp (preset=2, post_mid=0) on 10-bit CUSA08519: RCAS8 BEFORE WARP8, target=2u, prec=64u, clamp=128u, 1 pass
    g_cfg.preset = 2; g_opt_sharp = 1; g_mid_only = -1;
    uint32_t n2 = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                  1920u, 10u, 1920u * 1080u * 4u, 0u, 11u, nop64, 1, 2u, 3u,
                                  32768u, 32768u, 0, 0, 0x50000000ULL, 0xFFFFu);
    scan_passes(mt, n2, &pw, &pr, &pp, &rcnt);
    if (!(pr >= 0 && pw > pr && pp > pw && rcnt == 1)) return 3;
    if ((tb[137] & 3u) != 2u || (tb[137] & 64u) == 0u || (tb[137] & 128u) == 0u) return 4;

    // 3. Sharp (preset=2, post_mid=0) with two_mids=1 (below30=1): still only 1 pre-warp RCAS8 pass, tb2[137]==0
    g_mid_only = -1;
    uint32_t n2_two = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                      1920u, 10u, 1920u * 1080u * 4u, 0u, 12u, nop64, 1, 2u, 3u,
                                      21845u, 43690u, 1, 0, 0x50000000ULL, 0xFFFFu);
    scan_passes(mt, n2_two, &pw, &pr, &pp, &rcnt);
    if (!(pr >= 0 && pw > pr && rcnt == 1)) return 5;
    if (tb2[137] != 0u) return 6;

    // 4. Sharp with explicit post_mid=1: RCAS8 stays after WARP8, target=1u
    g_cfg.post_mid = 1; g_mid_only = -1;
    uint32_t n2_pm1 = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                      1920u, 10u, 1920u * 1080u * 4u, 0u, 13u, nop64, 1, 2u, 3u,
                                      32768u, 32768u, 0, 0, 0x50000000ULL, 0xFFFFu);
    scan_passes(mt, n2_pm1, &pw, &pr, &pp, &rcnt);
    if (!(pw >= 0 && pr > pw && rcnt == 1 && (tb[137] & 3u) == 1u)) return 7;

    // 5. Sharp during bench capture (g_bench_offscreen=1): RCAS8 stays after WARP8 on mid only
    g_cfg.post_mid = 0; g_bench_offscreen = 1; g_mid_only = -1;
    uint32_t n2_cap = build_dcb_flow8(mt, 0x10000000ULL, 0x20000000ULL, 0x30000000ULL, 0x40000000ULL,
                                      1920u, 10u, 1920u * 1080u * 4u, 0u, 14u, nop64, 1, 2u, 3u,
                                      32768u, 32768u, 0, 0, 0x50000000ULL, 0xFFFFu);
    scan_passes(mt, n2_cap, &pw, &pr, &pp, &rcnt);
    if (!(pw >= 0 && pr > pw && rcnt == 1 && (tb[137] & 3u) == 1u)) return 8;

    printf("PM4_FLOW8_OK\\n");
    return 0;
}}
"""
        with tempfile.TemporaryDirectory() as td:
            c_file = pathlib.Path(td) / "t2b.c"
            exe = pathlib.Path(td) / "t2b"
            c_file.write_text(harness, encoding="utf-8")
            inc = str(ROOT / "classic" / "source")
            ovl = str(ROOT / "overlay")
            subprocess.run([CC, "-O0", "-Wall", "-I", inc, "-I", ovl, str(c_file), "-o", str(exe)], check=True)
            res = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"PM4 flow8 check failed with code {res.returncode}: {res.stdout} {res.stderr}")
        self.assertEqual(res.stdout.strip(), "PM4_FLOW8_OK")

    def test_3_numerical_v1a_eliminates_hud_double_sharpening_and_10bit_banding(self):
        # Part A: 10-bit (2_10_10_10) gradient 0..1023
        codes = np.arange(1024, dtype=np.int64)
        f = (codes.astype(F32) / F32(1023.0)).astype(F32)
        grad = np.tile(f[None, :, None], (16, 1, 3)).astype(F32)
        con60 = vw.rcas_con(60)
        q10 = lambda x: np.floor(np.clip(x, 0, 1) * F32(1023.0) + F32(0.5)).astype(np.int64)

        out_no_prec = q10(rcas8_gpu_sim(grad, con60, 1 | 4, precise=False)[8, :, 0])
        out_prec = q10(rcas8_gpu_sim(grad, con60, 1 | 4, precise=True)[8, :, 0])

        self.assertGreater(int(np.sum(out_no_prec != codes)), 700)
        self.assertGreaterEqual(int(np.max(np.abs(np.diff(out_no_prec)))), 4)
        self.assertEqual(int(np.sum(out_prec != codes)), 0)
        self.assertEqual(int(np.max(np.abs(np.diff(out_prec)))), 1)

        # Part B: Stationary HUD contour on sequential pair (A -> B)
        H, W = 64, 64
        rng = np.random.default_rng(42)
        bg_a = (rng.integers(40, 180, (H, W, 3)).astype(F32) / F32(255.0)).astype(F32)
        bg_b = np.roll(bg_a, shift=3, axis=1)  # moving background

        def stamp_hud(img):
            out = img.copy()
            out[16:48, 16:48, :] = F32(0.10)
            out[19:45, 19:45, :] = F32(0.45)
            out[20:44, 20:44, :] = F32(0.90)
            out[22:42, 22:42, :] = F32(0.45)
            out[23:41, 23:41, :] = F32(0.10)
            out[28:36, 28:36, :] = F32(0.85)
            return out

        A = stamp_hud(bg_a)
        B = stamp_hud(bg_b)
        hud_interior = np.zeros((H, W), dtype=bool)
        hud_interior[18:46, 18:46] = True

        # Zero motion on HUD, 3px motion on background
        F_mv = np.zeros((H // 2, W // 2, 2), dtype=F32)
        GK = np.zeros((32, 4), dtype=F32)

        # T19: P = rcas8(A, prec=False), C = raw B -> warp8 -> rcas8(mid, skip=None)
        P_t19 = rcas8_gpu_sim(A, con60, 1 | 4, precise=False)
        C_t19 = B
        st_t19 = vw.static_px(P_t19, C_t19)
        mid_w_t19 = vw.warp8(P_t19, C_t19, F_mv, GK, cutn=0, t=0.5, flags=1024)
        mid_t19 = rcas8_gpu_sim(mid_w_t19, con60, 1 | 4, skip=None, still=None, precise=False)
        real_t19 = rcas8_gpu_sim(B, con60, 1 | 4, precise=False)

        # V1a: P = rcas8(A, prec=True), C = rcas8(B, prec=True) BEFORE warp8 -> warp8 -> no mid rcas8
        P_v1a = rcas8_gpu_sim(A, con60, 1 | 4, precise=True)
        C_v1a = rcas8_gpu_sim(B, con60, 1 | 4, precise=True)
        st_v1a = vw.static_px(P_v1a, C_v1a)
        mid_v1a = vw.warp8(P_v1a, C_v1a, F_mv, GK, cutn=0, t=0.5, flags=1024)
        real_v1a = C_v1a

        # In T19, P != C on the sharpened HUD contours, and mid_t19 != real_t19 (double-sharpened)
        self.assertLess(float(np.mean(st_t19[hud_interior])), 0.85)
        diff_t19_hud = float(np.max(np.abs(mid_t19[hud_interior] - real_t19[hud_interior])))
        self.assertGreater(diff_t19_hud, 0.02)

        # In V1a, P == C on 100% of the HUD interior, and mid_v1a == real_v1a bit-for-bit on the HUD!
        self.assertEqual(float(np.mean(st_v1a[hud_interior])), 1.0)
        self.assertTrue(np.array_equal(mid_v1a[hud_interior], real_v1a[hud_interior]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
