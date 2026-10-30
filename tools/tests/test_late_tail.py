#!/usr/bin/env python3
"""Option late_tail (classic/source/late_tail.h): build_dcb_flow8 compiled from the source and executed.

The current build_dcb_flow8 of classic/source/main.c and the one the DCB had before the option
(fixtures/late_tail/build_dcb_flow8_before.c.txt, extracted literally, plus the fade_hi table flag) are compiled into one
C harness, with the real fg8_table.h, fg_res.h, shaders.h, late_tail.h and the rcas8 flag helpers of main.c, and run on the same inputs:

1. late_tail off (g_lt_req = 0), and on for frames it does not apply to (3-slot pair, present copy to the screen,
   no history copy, frame dump, rcas8 bit 5): the DCB words, the whole GPU slot memory and the history state are
   identical to the reference, across the B profile (no post-process), the C profile (Sharpness + Enhance AA on both
   frames, CUSA02299 early history copy), another title with post_mid = 2, the generated-frame-only post-process, with
   and without the overlay; no DCB2 is reported.
2. late_tail on: DCB1 has no pcopy8 and post-processes the generated frame only (rcas8 / overlay target bit 0); DCB2
   (g_lt_n[slot] dwords at LT_DCB2_DW) starts with a timestamp, a barrier and the thread count, sets the user data
   and the shader before every dispatch, holds the history copy (into g_pbuf) and, with post-process on both frames,
   rcas8 and the overlay for the real frame only (table in SLOT_TB2_OFF, target bit 1), in the reference order
   (CUSA02299: copy before rcas8; other titles: rcas8, overlay, copy), and ends with an end-of-pipe label and its wait.
   Together, DCB1 and DCB2 dispatch the reference's passes with the same shaders.
3. Ring order: DCB1(N), DCB2(N), DCB1(N+1) walked as the GPU would (a small PM4 walker that runs pcopy8 as a copy
   and rcas8's real-frame pass as an in-place change of C): the copy that writes g_pbuf comes after the real frame's
   pixels are final for the copy (raw C on CUSA02299), an end-of-pipe wait separates it from the next pair's warp8,
   and that warp8's T#prev is g_pbuf holding frame N, after the game has rewritten frame N's buffer.

Compiler: config.json llvm_bin (clang.exe + lli.exe, as the other harnesses) when present, otherwise the host
clang/cc building a native binary.
"""
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "classic" / "source"
MAIN_C = (SRC / "main.c").read_text(encoding="utf-8", errors="replace")
REF = (ROOT / "tools" / "tests" / "fixtures" / "late_tail" / "build_dcb_flow8_before.c.txt").read_text(encoding="utf-8")


def extract_function(src: str, name: str) -> str:
    match = re.search(r"^static [^\n]+\b" + name + r"\([^;]*\)\s*\{", src, re.M)
    assert match is not None, f"function not found: {name}"
    pos = src.index("{", match.start())
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[match.start():end]


def extract_define(src: str, name: str) -> str:
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"#define " + name + r"(?:\(|\s)", line):
            out = [line]
            while out[-1].endswith("\\"):
                i += 1
                out.append(lines[i])
            return "\n".join(out)
    raise ValueError(f"define not found: {name}")


DEFINES = "\n".join(extract_define(MAIN_C, n) for n in [
    "GPU_SZ", "SLOT_SZ", "SLOT0", "DCB8_OFF", "DCB8_SZ", "STATS_OFF", "DUMP_DONE_OFF", "PKT3", "TS_EOP", "TS2",
    "PASS_BARRIER", "SET_PASS"])
HELPERS = "\n".join(extract_function(MAIN_C, n) for n in [
    "calc_rcas_params", "real_aa_on", "rcas_static_flag", "rcas_precise_flag"])
CUR = extract_function(MAIN_C, "build_dcb_flow8")
REF_FN = REF[REF.index("static uint32_t build_dcb_flow8("):].replace("build_dcb_flow8(", "build_dcb_flow8_ref(", 1)

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ASSERT(c) do { if (!(c)) { printf("ASSERT FAILED line %d: %s\n", __LINE__, #c); exit(1); } } while (0)
#define CUT_BLOCKS 5600u
#define FG8_FLAG_PYR_WARM_FALLBACK 0

typedef struct { int sharpness, upscale, post_mid; } cfg_t;
static cfg_t g_cfg;
static char g_title_id[16] = "CUSA02299";
static int g_mem_tight = 0, g_runtime_2buf = 0, g_ngame = 2, g_fg_off = 0, g_bench_offscreen = 0;
static int g_dump_req = 0, g_fence_logged = 0, g_pb_level = 0;
static volatile int g_fade_hi = 1;   // option fade_hi, default on (both functions set FG8_FLAG_FADE_HI)
static uint8_t *g_dump_mem = NULL;
static uint32_t g_dump_frame = 0;
static uint32_t g_bw = 1920, g_bh = 1080;
static int g_opt_phase = 0, g_opt_marker = 0, g_opt_nocf = 0, g_opt_nofgk = 0, g_opt_noanc = 0;
static int g_opt_notp = 0, g_opt_nomf = 0, g_opt_notex = 0, g_opt_notpg = 0, g_opt_sharp = 0, g_opt_nostr = 0;
static uint64_t g_pyr_last_cur_base = 0;
static uint32_t g_pyr_last_w = 0, g_pyr_last_h = 0, g_pyr_parity = 0;
static uint32_t g_sh8_off[17];
static uint8_t g_gpu_backing[0x20000] __attribute__((aligned(4096)));
static uint8_t *g_gpu = g_gpu_backing;
static uint8_t g_work_backing[0x1000];
static uint8_t *g_work = g_work_backing;
static uint32_t g_mon_backing[8];
static volatile uint32_t *g_mon = g_mon_backing;
static int g_post_both = 0, g_osd_on = 0, g_rcas_bit5 = 0;
static uint32_t g_hud_packed = 0;

static void lg(const char *fmt, ...) { (void)fmt; }
static int vcfg_in_family(const char *t, const char *f) { return strcmp(t, f) == 0; }
const uint8_t *vov_pal_ptr(void) { return NULL; }
const uint8_t *vov_pix_ptr(void) { return NULL; }
static uint32_t cut_blocks8(void) { return 0; }
static int post_mid_only(void) { return !g_post_both; }
static int gv_warp_active(void) { return 0; }
static int gv_pass_active(void) { return 0; }
static int gv_viz_active(void) { return 0; }
static uint32_t *gv_emit_eof(uint32_t *p, uint32_t s, uint32_t k) { (void)s; (void)k; return p; }
static uint32_t *gv_emit_ts(uint32_t *p, uint32_t s, int i) { (void)s; (void)i; return p; }
static uint32_t *gv_emit_after_gv8(uint32_t *p, uint32_t s, uint32_t k) { (void)s; (void)k; return p; }
static void gv_fill_table(uint32_t *p, uint32_t s, uint32_t k) { (void)p; (void)s; (void)k; }
static void pb_eff(int level, int sharp, int enh, int *so, int *eo) { (void)level; *so = sharp; *eo = enh; }
static void osd_build_slot(uint8_t *m, uint32_t *xy, uint32_t *wh, uint32_t flags)
{
    (void)m;
    *xy = 0; *wh = 0;
    if (g_osd_on) { *xy = 16u | (16u << 16); *wh = 12u | (3u << 8) | (200u << 16) | (flags << 24); }
}
static int32_t insert_wait_flip_done(uint32_t *p, uint32_t n, int32_t h, uint32_t i)
{
    (void)n; (void)h; (void)i;
    p[0] = 0xC0000000u | (5u << 16) | (0x10u << 8);   /* a 7-dword NOP stands for the wait packet */
    for (int k = 1; k < 7; k++) p[k] = 0;
    return 0;
}

#include "shaders.h"
#include "fg_res.h"
#include "fg8_table.h"
#include "late_tail.h"

@DEFINES@
@HELPERS@
@REF@
@CUR@

/* ---- state kept apart between the reference and the current function ---- */
typedef struct {
    uint8_t gpu[0x20000];
    uint64_t pyr_base; uint32_t pyr_w, pyr_h, pyr_par;
    int pbuf_valid; uint32_t pbuf_seq; uint64_t pbuf_prev; int prev_half, dst_half, fence;
    int dump_req; uint32_t dump_frame;
} snap_t;
static snap_t s_a, s_b;
static void snap(snap_t *s)
{
    memcpy(s->gpu, g_gpu_backing, sizeof(s->gpu));
    s->pyr_base = g_pyr_last_cur_base; s->pyr_w = g_pyr_last_w; s->pyr_h = g_pyr_last_h; s->pyr_par = g_pyr_parity;
    s->pbuf_valid = g_pbuf_valid; s->pbuf_seq = g_pbuf_last_seq; s->pbuf_prev = g_pbuf_prev_va;
    s->prev_half = g_t_prev_half16; s->dst_half = g_pcopy_dst_half16; s->fence = g_fence_logged;
    s->dump_req = g_dump_req; s->dump_frame = g_dump_frame;
}
static void restore(const snap_t *s)
{
    memcpy(g_gpu_backing, s->gpu, sizeof(s->gpu));
    g_pyr_last_cur_base = s->pyr_base; g_pyr_last_w = s->pyr_w; g_pyr_last_h = s->pyr_h; g_pyr_parity = s->pyr_par;
    g_pbuf_valid = s->pbuf_valid; g_pbuf_last_seq = s->pbuf_seq; g_pbuf_prev_va = s->pbuf_prev;
    g_t_prev_half16 = s->prev_half; g_pcopy_dst_half16 = s->dst_half; g_fence_logged = s->fence;
    g_dump_req = s->dump_req; g_dump_frame = s->dump_frame;
}
static int same_state(const snap_t *a, const snap_t *b)
{
    return !memcmp(a->gpu, b->gpu, sizeof(a->gpu)) && a->pyr_base == b->pyr_base && a->pyr_par == b->pyr_par &&
           a->pbuf_valid == b->pbuf_valid && a->pbuf_seq == b->pbuf_seq && a->pbuf_prev == b->pbuf_prev &&
           a->prev_half == b->prev_half && a->dst_half == b->dst_half && a->dump_req == b->dump_req && a->dump_frame == b->dump_frame;
}

/* frame buffers: tiny surfaces, the tables only hold their addresses */
#define NPX 256
static uint32_t s_buf[3][NPX] __attribute__((aligned(256)));
static uint32_t s_pbuf_mem[NPX] __attribute__((aligned(256)));
static uint64_t B(int i) { return (uint64_t)(uintptr_t)s_buf[i]; }
static uint32_t s_nop64[64];

typedef struct { uint32_t slot, seq; int cur, prev, two_mids, copy_idx_screen, no_copy; } frame_t;
static uint32_t build(int ref, const frame_t *f, uint32_t *out)
{
    uint64_t P = B(f->prev), C = B(f->cur), M = B(2);
    uint64_t copy_dst = f->copy_idx_screen ? B(2) : 0ull;
    uint32_t copy_idx = f->copy_idx_screen ? 2u : 0u;
    if (f->no_copy) g_pbuf = 0;
    else g_pbuf = s_pbuf_mem;
    uint32_t n = ref ? build_dcb_flow8_ref(out, P, C, M, M, 1920, 10u, 1920u * 1088u * 4u, f->slot, f->seq, s_nop64, 1, 2u, 2u,
                                           32768u, 43690u, f->two_mids, 0, copy_dst, copy_idx)
                     : build_dcb_flow8(out, P, C, M, M, 1920, 10u, 1920u * 1088u * 4u, f->slot, f->seq, s_nop64, 1, 2u, 2u,
                                       32768u, 43690u, f->two_mids, 0, copy_dst, copy_idx);
    return n;
}

/* ---- a small PM4 walker ---- */
enum { EV_DISPATCH = 1, EV_EOP_WAIT = 2 };
typedef struct { int type; int pass; uint64_t table; } ev_t;
static uint64_t pgm_base(void) { return (uint64_t)(uintptr_t)g_gpu; }
static int walk(const uint32_t *d, uint32_t n, ev_t *ev, int max)
{
    int k = 0; uint64_t pgm = 0, ud = 0; uint64_t last_eop = 0; int have_eop = 0;
    for (uint32_t i = 0; i < n;) {
        uint32_t h = d[i];
        ASSERT((h >> 30) == 3u);
        uint32_t len = ((h >> 16) & 0x3FFFu) + 2u, op = (h >> 8) & 0xFFu;
        ASSERT(i + len <= n);
        if (op == 0x76u && d[i + 1] == 0x20Cu) pgm = ((uint64_t)d[i + 2] << 8) | ((uint64_t)(d[i + 3] & 0xFFu) << 40);
        if (op == 0x76u && d[i + 1] == 0x240u) ud = (uint64_t)d[i + 2] | ((uint64_t)d[i + 3] << 32);
        if (op == 0x15u) { ASSERT(k < max); ev[k].type = EV_DISPATCH; ev[k].pass = (int)((pgm - pgm_base()) / 0x100u) - 1; ev[k].table = ud; k++; }
        if (op == 0x47u && (d[i + 3] >> 29) == 2u) { last_eop = (uint64_t)d[i + 2] | ((uint64_t)(d[i + 3] & 0xFFFFu) << 32); have_eop = 1; }
        if (op == 0x3Cu && have_eop && ((uint64_t)d[i + 2] | ((uint64_t)d[i + 3] << 32)) == last_eop) {
            ASSERT(k < max); ev[k].type = EV_EOP_WAIT; ev[k].pass = -1; ev[k].table = last_eop; k++;
        }
        i += len;
    }
    return k;
}
static uint64_t t_addr(const uint32_t *t) { return ((uint64_t)t[0] << 8) | ((uint64_t)(t[1] & 0xFFu) << 40); }
static int npass(const ev_t *e, int k, int pass) { int c = 0; for (int i = 0; i < k; i++) c += e[i].type == EV_DISPATCH && e[i].pass == pass; return c; }
static int first(const ev_t *e, int k, int pass) { for (int i = 0; i < k; i++) if (e[i].type == EV_DISPATCH && e[i].pass == pass) return i; return -1; }

static uint32_t s_ref[1024], s_cur[1024];

/* 1: identical DCB, GPU memory and history state; returns 1 if the current call reported a DCB2 */
static int compare(const frame_t *f, const char *what)
{
    snap(&s_a);
    memset(s_ref, 0xAB, sizeof(s_ref)); memset(s_cur, 0xAB, sizeof(s_cur));
    uint32_t nr = build(1, f, s_ref);
    snap(&s_b);
    restore(&s_a);
    uint32_t nc = build(0, f, s_cur);
    snap(&s_a);
    int ok = nr == nc && !memcmp(s_ref, s_cur, nr * 4u) && same_state(&s_a, &s_b);
    printf("%-58s ref %3u dw, cur %3u dw: %s\n", what, nr, nc, ok ? "identical" : "DIFFERENT");
    ASSERT(ok);
    return g_lt_n[f->slot & 3u] != 0;
}

typedef struct { const char *name, *title; int sharp, up, post_mid, both, osd; } prof_t;
static const prof_t PROFILES[] = {
    { "B: no post-process", "CUSA02299", 0, 0, 2, 1, 0 },
    { "B + overlay", "CUSA02299", 0, 0, 2, 1, 1 },
    { "C: sharp 70 + AA 2 on both, early copy", "CUSA02299", 70, 2, 2, 1, 0 },
    { "C + overlay", "CUSA02299", 70, 2, 2, 1, 1 },
    { "post_mid 2 on another title (copy at the end)", "CUSA08519", 70, 2, 2, 1, 1 },
    { "generated frame only (post_mid 1)", "CUSA02299", 60, 1, 1, 0, 1 },
};
static void set_profile(const prof_t *p)
{
    memset(g_title_id, 0, sizeof(g_title_id)); memcpy(g_title_id, p->title, strlen(p->title));
    g_cfg.sharpness = p->sharp; g_cfg.upscale = p->up; g_cfg.post_mid = p->post_mid; g_post_both = p->both; g_osd_on = p->osd;
}

int main(void)
{
    for (int i = 0; i < 17; i++) g_sh8_off[i] = (uint32_t)(i + 1) * 0x100u;
    s_nop64[0] = 0xC03E1000u;   /* the game's flip packet header (64 dwords: count 62) */
    g_t_dfmt = 10u;
    uint32_t np = sizeof(PROFILES) / sizeof(PROFILES[0]);

    /* ---- 1. off, and on where it does not apply: identical ---- */
    for (uint32_t pi = 0; pi < np; pi++) {
        set_profile(&PROFILES[pi]);
        g_pbuf_valid = 0;
        for (int fr = 0; fr < 4; fr++) {
            frame_t f = { (uint32_t)fr & 3u, 200u + (uint32_t)fr, fr & 1, (fr & 1) ^ 1, 0, 0, 0 };
            char w[96];
            g_lt_req = 0;
            snprintf(w, sizeof(w), "off  %s f%d", PROFILES[pi].name, fr);
            ASSERT(!compare(&f, w));
        }
        frame_t f3 = { 1, 300, 1, 0, 1, 0, 0 };   /* 3-slot pair */
        g_lt_req = 1; ASSERT(!compare(&f3, "on, 3-slot pair"));
        frame_t fs = { 2, 301, 0, 1, 0, 1, 0 };   /* present copy to the screen */
        g_lt_req = 1; ASSERT(!compare(&fs, "on, present copy to the screen"));
        frame_t fn = { 3, 302, 1, 0, 0, 0, 1 };   /* no history buffer */
        g_lt_req = 1; ASSERT(!compare(&fn, "on, no history copy"));
        g_dump_req = 1; static uint8_t dm[16]; g_dump_mem = dm;
        frame_t fd = { 0, 303, 0, 1, 0, 0, 0 };
        g_lt_req = 1; ASSERT(!compare(&fd, "on, frame dump"));
        g_dump_req = 0; g_dump_mem = NULL;
        g_lt_req = 0;
    }
    { /* rcas8 bit 5 (real_aa_on: post_mid 3, AA, Sharpness 0) */
        set_profile(&PROFILES[0]); g_cfg.post_mid = 3; g_cfg.upscale = 1; g_cfg.sharpness = 0; g_post_both = 1;
        frame_t f = { 1, 310, 1, 0, 0, 0, 0 };
        g_lt_req = 1; ASSERT(!compare(&f, "on, rcas8 bit 5")); g_lt_req = 0;
    }

    /* ---- 2. on: DCB1 / DCB2 contents against the reference ---- */
    for (uint32_t pi = 0; pi < np; pi++) {
        set_profile(&PROFILES[pi]);
        g_pbuf_valid = 0;
        frame_t f = { 1, 400u + pi, 1, 0, 0, 0, 0 };
        snap(&s_a);
        uint32_t nr = build(1, &f, s_ref);
        uint32_t *tb_slot = (uint32_t *)(g_gpu + SLOT0 + 1u * SLOT_SZ + 0xB00);
        uint32_t ref_flags = tb_slot[137], ref_osd = tb_slot[139];
        restore(&s_a);
        uint32_t *mine = (uint32_t *)(g_gpu + DCB8_OFF + 1u * DCB8_SZ);
        g_lt_req = 1;
        uint32_t n1 = build(0, &f, mine);
        g_lt_req = 0;
        uint32_t n2 = g_lt_n[1];
        ASSERT(n2 > 0 && n1 + 64u <= LT_DCB2_DW && n2 + 128u <= DCB8_SZ / 4u - LT_DCB2_DW);
        uint32_t *d2 = mine + LT_DCB2_DW;
        ev_t er[64], e1[64], e2[64];
        int kr = walk(s_ref, nr, er, 64), k1 = walk(mine, n1, e1, 64), k2 = walk(d2, n2, e2, 64);
        /* DCB1 ends with the flip packet, DCB2 without (the hook appends the game's) */
        ASSERT(!memcmp(mine + n1 - 64, s_nop64, sizeof(s_nop64)));
        /* the reference's passes, split between DCB1 and DCB2 with the same shaders */
        int both_rcas = (ref_flags & 3u) == 3u, osd_cur = (ref_osd >> 24) & 2u;
        ASSERT(npass(e1, k1, 16) == 0 && npass(e2, k2, 16) == 1 && npass(er, kr, 16) == 1);
        for (int ps = 0; ps < 17; ps++) {
            if (ps == 16) continue;
            int moved = (ps == 14 || (ps == 15 && osd_cur)) && both_rcas;
            ASSERT(npass(e1, k1, ps) + npass(e2, k2, ps) == npass(er, kr, ps) + (moved ? 1 : 0));
            if (!moved) ASSERT(npass(e2, k2, ps) == 0);
        }
        /* DCB1 table: generated frame only when the real frame's half moved */
        uint32_t f1 = tb_slot[137], o1 = tb_slot[139];
        uint32_t *tbr = (uint32_t *)(g_gpu + SLOT0 + 1u * SLOT_SZ + SLOT_TB2_OFF);
        if (both_rcas) {
            ASSERT(f1 == ((ref_flags & ~3u) | 1u));
            ASSERT(tbr[137] == ((ref_flags & ~3u) | 2u) && tbr[136] == tb_slot[136]);
            ASSERT(!memcmp(tbr, tb_slot, 128 * 4));   /* same surfaces and parameters */
            if (osd_cur) ASSERT(o1 == (ref_osd & ~(2u << 24)) && tbr[139] == (ref_osd & ~(1u << 24)));
            int ir = first(e2, k2, 14), ic = first(e2, k2, 16);
            ASSERT(e2[ir].table == (uint64_t)(uintptr_t)tbr);
            if (osd_cur) { int io = first(e2, k2, 15); ASSERT(io > ir && e2[io].table == (uint64_t)(uintptr_t)tbr); }
            /* reference order of the copy against rcas8 */
            int rr = first(er, kr, 14), rc = first(er, kr, 16);
            ASSERT((rc < rr) == (ic < ir));
        } else {
            ASSERT(f1 == ref_flags && o1 == ref_osd);
        }
        /* DCB2 framing: timestamp, barrier, thread count; every dispatch has its user data and shader set inside DCB2 */
        ASSERT(d2[0] == PKT3(0x47, 5, 0) && d2[3] >> 29 == 3u);
        ASSERT(d2[6] == PKT3(0x46, 1, 0) && d2[7] == 0x407u);
        ASSERT(d2[15] == PKT3(0x76, 4, 1) && d2[16] == 0x207u && d2[17] == 64u && d2[18] == 1u && d2[19] == 1u);
        {
            int have_ud = 0, have_pgm = 0, have_rsrc = 0;
            for (uint32_t i = 0; i < n2;) {
                uint32_t h = d2[i], len = ((h >> 16) & 0x3FFFu) + 2u, op = (h >> 8) & 0xFFu;
                if (op == 0x76u && d2[i + 1] == 0x240u) have_ud = 1;
                if (op == 0x76u && d2[i + 1] == 0x20Cu) have_pgm = 1;
                if (op == 0x76u && d2[i + 1] == 0x212u) have_rsrc = 1;
                if (op == 0x15u) ASSERT(have_ud && have_pgm && have_rsrc);
                i += len;
            }
        }
        ASSERT(e2[k2 - 1].type == EV_EOP_WAIT && e2[k2 - 1].table == (uint64_t)(uintptr_t)(g_gpu + SLOT0 + SLOT_SZ + LT_LBL_OFF));
        printf("on   %-50s DCB1 %3u dw (ref %3u), DCB2 %3u dw: ok\n", PROFILES[pi].name, n1, nr, n2);
    }

    /* ---- 3. ring order DCB1(N) DCB2(N) DCB1(N+1): the next warp reads g_pbuf after the copy wrote frame N ---- */
    for (uint32_t pi = 0; pi < np; pi++) {
        set_profile(&PROFILES[pi]);
        g_pbuf_valid = 0;
        for (int k = 0; k < NPX; k++) { s_buf[0][k] = 0x11000000u + k; s_buf[1][k] = 0x22000000u + k; s_pbuf_mem[k] = 0; }
        uint32_t *m0 = (uint32_t *)(g_gpu + DCB8_OFF), *m1 = (uint32_t *)(g_gpu + DCB8_OFF + DCB8_SZ);
        frame_t fa = { 0, 500, 1, 0, 0, 0, 0 }, fb = { 1, 501, 0, 1, 0, 0, 0 };
        g_lt_req = 1;
        uint32_t na1 = build(0, &fa, m0); uint32_t na2 = g_lt_n[0];
        uint32_t *tb3a = (uint32_t *)(g_gpu + SLOT0 + OSD_SLOT_OFF);
        uint64_t cp_src = ((uint64_t)tb3a[8] << 8) | ((uint64_t)(tb3a[9] & 0xFFu) << 40);
        uint64_t cp_dst = ((uint64_t)tb3a[16] << 8) | ((uint64_t)(tb3a[17] & 0xFFu) << 40);
        ASSERT(cp_src == B(1) && cp_dst == (uint64_t)(uintptr_t)s_pbuf_mem);
        /* walk frame N: DCB1, then DCB2 with the passes run on the tiny surfaces */
        ev_t ea[64], eb[64], ec[64];
        int ka = walk(m0, na1, ea, 64), kb = walk(m0 + LT_DCB2_DW, na2, eb, 64);
        (void)ka;
        int copied_raw = -1;
        for (int i = 0; i < kb; i++) {
            if (eb[i].type != EV_DISPATCH) continue;
            if (eb[i].pass == 14) {   /* rcas8, real frame target: in place on C */
                const uint32_t *t = (const uint32_t *)(uintptr_t)eb[i].table;
                if (t[137] & 2u) for (int k = 0; k < NPX; k++) s_buf[1][k] ^= 0x00F00000u;
            }
            if (eb[i].pass == 16) {
                memcpy(s_pbuf_mem, (void *)(uintptr_t)cp_src, sizeof(s_pbuf_mem));
                copied_raw = s_pbuf_mem[5] == 0x22000005u;
            }
        }
        ASSERT(copied_raw >= 0);
        int early = !strcmp(g_title_id, "CUSA02299") && g_post_both && g_cfg.sharpness;
        if (early || !g_post_both || !g_cfg.sharpness) ASSERT(copied_raw == 1);   /* the copy saw C before the real's rcas8 */
        else ASSERT(copied_raw == 0);   /* other titles keep their order: the copy after rcas8, as before */
        int eop_after_copy = 0;
        for (int i = first(eb, kb, 16) + 1; i < kb; i++) eop_after_copy |= eb[i].type == EV_EOP_WAIT;
        ASSERT(eop_after_copy);
        uint32_t want = s_pbuf_mem[7];
        /* the game rewrites frame N's buffer (s_buf[1]) before frame N+1 */
        for (int k = 0; k < NPX; k++) s_buf[1][k] = 0xDEAD0000u + k;
        uint32_t nb1 = build(0, &fb, m1);
        g_lt_req = 0;
        int kc = walk(m1, nb1, ec, 64);
        int iw = first(ec, kc, 5);
        ASSERT(iw >= 0);
        int eop_before_warp = 0;
        for (int i = 0; i < iw; i++) eop_before_warp |= ec[i].type == EV_EOP_WAIT;
        ASSERT(eop_before_warp);
        const uint32_t *tw = (const uint32_t *)(uintptr_t)ec[iw].table;
        ASSERT(t_addr(tw) == (uint64_t)(uintptr_t)s_pbuf_mem);
        ASSERT(((const uint32_t *)(uintptr_t)t_addr(tw))[7] == want && want != 0xDEAD0007u);
        printf("ring %-50s copy in DCB2(N) -> EOP wait -> warp8(N+1) reads g_pbuf: ok\n", PROFILES[pi].name);
    }
    printf("late_tail: all checks passed\n");
    return 0;
}
"""


def harness() -> str:
    return (HARNESS.replace("@DEFINES@", DEFINES).replace("@HELPERS@", HELPERS)
            .replace("@REF@", REF_FN).replace("@CUR@", CUR))


def compile_and_run(c_src: str) -> str:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="late_tail_"))
    c_path = tmp / "t.c"
    c_path.write_text(c_src, encoding="utf-8")
    inc = ["-I" + str(SRC), "-I" + str(ROOT / "overlay")]
    flags = ["-O0", "-Wall", "-Wextra", "-Wno-unused-variable", "-Wno-unused-function", "-Wno-unused-parameter",
             "-Wno-unused-but-set-variable", "-Wno-sign-compare"]
    cfg_path = ROOT / "config.json"
    llvm = None
    if cfg_path.exists():
        llvm_bin = pathlib.Path(json.loads(cfg_path.read_text(encoding="utf-8")).get("llvm_bin", ""))
        if (llvm_bin / "clang.exe").exists() and (llvm_bin / "lli.exe").exists():
            llvm = llvm_bin
    if llvm:
        bc = tmp / "t.bc"
        subprocess.run([str(llvm / "clang.exe"), "--target=x86_64-pc-windows-msvc", *flags, *inc, "-emit-llvm", "-c",
                        str(c_path), "-o", str(bc)], check=True)
        out = subprocess.run([str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bc)], capture_output=True, text=True)
    else:
        cc = shutil.which("clang") or shutil.which("cc")
        exe = tmp / "t"
        subprocess.run([cc, *flags, *inc, str(c_path), "-o", str(exe)], check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(out.stdout + out.stderr)
    return out.stdout


class TestLateTail(unittest.TestCase):
    def test_dcb_off_identical_on_split_and_ring_order(self) -> None:
        out = compile_and_run(harness())
        print(out)
        self.assertIn("late_tail: all checks passed", out)

    def test_hook_submits_dcb2_and_keeps_other_callers_off(self) -> None:
        flip = extract_function(MAIN_C, "flip_inner_ex")
        # only the gnm flip path of mode 8 asks for DCB2, around its own call, and clears the request after it
        self.assertEqual(MAIN_C.count("g_lt_req = lt_on && dcb8 && !third_flip;"), 1)
        i_req = flip.index("g_lt_req = lt_on && dcb8 && !third_flip;")
        i_call = flip.index("n = build_dcb_flow8(mine,", i_req)
        self.assertLess(i_call, flip.index("g_lt_req = 0;", i_call))
        # a stale DCB2 size never outlives its frame
        self.assertLess(flip.index("g_lt_n[slot & 3u] = 0;"), flip.index("int can_fg8 ="))
        # the real frame's flip carries DCB2 + the game's flip packet when there is one
        i_d3 = flip.index("void *d3[1] = { fb };")
        sub = flip[i_d3:flip.index("int32_t r2 = gnm_submit_and_flip_raw(", i_d3)]
        self.assertIn("uint32_t *q = mine + LT_DCB2_DW;", sub)
        self.assertIn("q[lt_n + i] = tail[i];", sub)
        self.assertIn("s3[0] = (lt_n + tail_dw) * 4u;", sub)
        # option: every load resets it (hot reload), the key sets it, a profile save keeps it; the hook asks for DCB2
        # only from the option, so late_tail = 0 is g_lt_req = 0, the case test_dcb_off_identical_on_split_and_ring_order
        # proves byte for byte equal to the reference
        self.assertIn('g_late_tail       = 1;', extract_function(MAIN_C, "cfg_defaults"))
        self.assertIn('else if (strcmp(key, "late_tail") == 0) g_late_tail = (val != 0);', MAIN_C)
        self.assertIn('fprintf(f, "late_tail = %d\\n", g_late_tail ? 1 : 0);', MAIN_C)
        self.assertEqual(flip.count("int lt_on = g_late_tail ? 1 : 0;"), 1)
        self.assertEqual(MAIN_C.count("g_late_tail"), 4)   # cfg_defaults, parse, save, the hook

    def test_option_default_on_key_zero_off_and_saved(self) -> None:
        """A file without the key turns it on, late_tail = 0 turns it off, and the menu save writes the key either way."""
        import re as _re
        from test_menu_app_settings import MAIN as M, build_run, function
        self.assertIn("static volatile int g_late_tail = 1;", (SRC / "late_tail.h").read_text(encoding="utf-8"))
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
static int g_known_game, g_detected_60fps, g_tile_override = -1, g_tile_sweep, g_t6_bit_override = -1, g_adapt_early_crashes = 0, g_post_budget = 1, g_late_tail = 1, g_fade_hi = 1;
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
static int has(const char *path, const char *line) {
    char buf[4096]; FILE *f = fopen(path, "r"); size_t n = fread(buf, 1, sizeof(buf) - 1, f); fclose(f); buf[n] = 0;
    return strstr(buf, line) != NULL;
}
#define BASE "[bb_probe]\nframegen = 1\nmode = 8\npreset = 0\nsharpness = 70\nenhance = 1\n"
int main(int argc, char **argv) {
    snprintf(g_dir, sizeof(g_dir), "%s", argv[1]);
    char path[600]; snprintf(path, sizeof(path), "%s/config-CUSA02299.ini", g_dir);
    fg_config_t c;
    g_late_tail = 0; put(path, BASE);
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("absent %d\n", g_late_tail);
    put(path, BASE "late_tail = 0\n");
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("off %d\n", g_late_tail);
    g_cfg = c; cfg_save_game_ini();
    printf("saved0 line %d\n", has(path, "late_tail = 0\n"));
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("saved0 %d\n", g_late_tail);
    put(path, BASE);
    cfg_defaults(&c); parse_ini_file(path, &c, 0);
    g_cfg = c; cfg_save_game_ini();
    printf("saved1 line %d\n", has(path, "late_tail = 1\n"));
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("saved1 %d\n", g_late_tail);
    put(path, BASE "late_tail = 0\n");
    cfg_defaults(&c); parse_ini_file(path, &c, 0);
    put(path, BASE "late_tail = 1\n");
    cfg_defaults(&c); parse_ini_file(path, &c, 0); printf("on %d\n", g_late_tail);
    return 0;
}
"""
        r = build_run(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout.splitlines()
        self.assertIn("absent 1", out)        # opt-in version: "absent 0"
        self.assertIn("off 0", out)           # late_tail = 0 still turns it off (hot reload, every load)
        self.assertIn("saved0 line 1", out)   # the menu save writes the key off ...
        self.assertIn("saved0 0", out)        # ... so the saved profile stays off under the new default
        self.assertIn("saved1 line 1", out)   # and on
        self.assertIn("saved1 1", out)
        self.assertIn("on 1", out)


if __name__ == "__main__":
    unittest.main()
