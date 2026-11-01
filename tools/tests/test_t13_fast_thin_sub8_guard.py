#!/usr/bin/env python3
"""T13 regression test: mf8 Pass 2 SUB8 guard against false quadrant splits on fast-panning thin structures.

Root cause verified on CUSA00411 (fast driving along thin structures: trees, poles, lattice struts):
- In `mf8` Pass 1 (`sub = 0..9`), the 16x16 median/smoothness filter (`wincx, wincy`) locks onto the true camera pan
  velocity (`sc1 = |wincx - gmx2| + |wincy - gmy2| <= 3`) on non-foreground blocks (`hasfg == False`, `gk == 0`).
- When camera pan is fast (`|gmx2| + |gmy2| >= 22` L1 px, i.e., `>= 44` full-res px/frame) and a thin structure
  (1-3 px wide) crosses a smooth sky/gradient background, the structure vacates the unshifted midpoint `(X, Y)` in at
  least one of Frame A or Frame B (`ztex_min <= 160`).
- In `mf8` Pass 2 (`sub = 0..15`), unnormalized 4x4 quadrant SADs `qs` allow two classes of false secondary candidates
  to win 1..3 quadrants via `qwin`:
  1) Smooth-sky near-zero escapes (`(2, 0)` / `(-2, 0)`) when `ztex_min <= 112`.
  2) Diagonal aperture outliers (`|cy - gmy2| >= 16`) along thin struts when `ztex_min <= 160`.
- Because `sc1 <= 3 < sc2_win`, `swap_l` places the bogus outlier into Layer 2 (`v2`), causing `warp8` to unpack `v2`,
  break coherence (`spr > coh_thr`), enter `slow (!coh)`, and boost the bogus layer by 4x via `is_fg_sub`, erasing the
  thin object into sky background (30 Hz flicker between real and mid frames).

Single-table formula (`fast_thin_bg` in `mf8` Pass 2):
  gm_mag       = |gmx2| + |gmy2|
  fast_gm      = (gm_mag >= 22)
  zm_le160     = ~(ztex_min > 160)
  zm_le112     = (ztex_min <= 112)
  dy_ge16      = (|cy - gmy2| >= 16)
  thin_esc     = zm_le160 & (zm_le112 | dy_ge16)
  fast_thin_bg = (~hasfg) & (sc1 <= 3) & fast_gm & thin_esc
  ok2          = ok2_prev & (~fast_thin_bg)
"""
from __future__ import annotations

import ctypes
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))

import vcfg_motion as vm
import vcfg_ref as vr
import vcfg_warp as vw

MF8_LL = ROOT / "classic" / "shaders" / "mf8.ll"
SHADERS_H = ROOT / "classic" / "source" / "shaders.h"
LLC = pathlib.Path("/usr/bin/llc-22")
F32 = np.float32


def _find_clang() -> str:
    for cand in ("/usr/bin/clang-22", shutil.which("clang-22"), shutil.which("clang")):
        if cand and pathlib.Path(cand).exists():
            return str(cand)
    return ""


def _build_compiled_mf8_runner(mf8_ir: str, td_path: pathlib.Path):
    clang_bin = _find_clang()
    if not clang_bin:
        raise unittest.SkipTest("clang-22 required for compiled mf8.ll execution")
    ir = mf8_ir.replace('target triple = "amdgcn--"', "")
    ir = ir.replace(
        "define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 inreg %gy, i32 %tid) #0",
        "define void @mf8_cs_main(ptr %tbl, i32 %gx, i32 %gy, i32 %tid)",
    )
    ir = ir.replace("ptr addrspace(4)", "ptr")
    ir = ir.replace("ptr addrspace(3)", "ptr")
    ir = ir.replace("addrspace(3) global", "global")
    ir = ir.replace(", !invariant.load !0", "")
    ir = ir.replace("@llvm.amdgcn.s.barrier", "@sim_barrier")
    ir = ir.replace("@llvm.amdgcn.ds.swizzle", "@sim_ds_swizzle")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.v3i32", "@sim_buf_load_v3i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.v2i32", "@sim_buf_load_v2i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.i32", "@sim_buf_load_i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.v2f32", "@sim_buf_load_v2f32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.store.i32", "@sim_buf_store_i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.store.v2f32", "@sim_buf_store_v2f32")
    ir = ir.replace("@llvm.amdgcn.alignbyte", "@sim_alignbyte")
    ir = ir.replace("@llvm.amdgcn.sad.u8", "@sim_sad_u8")
    lines = [
        ln
        for ln in ir.splitlines()
        if not (ln.startswith("declare ") and "@sim_" in ln)
        and not ln.startswith("attributes #0")
    ]
    ir_stubs = """
declare void @c_barrier()
declare i32 @c_ds_swizzle(i32, i32)
declare i32 @c_alignbyte(i32, i32, i32)
declare i32 @c_sad_u8(i32, i32, i32)
declare i32 @c_buf_load_i32(i32, i32)
declare float @c_buf_load_f32(i32, i32)
declare void @c_buf_store_i32(i32, i32, i32)
declare void @c_buf_store_f32(float, i32, i32)

define void @sim_barrier() {
  call void @c_barrier()
  ret void
}
define i32 @sim_ds_swizzle(i32 %val, i32 %off) {
  %r = call i32 @c_ds_swizzle(i32 %val, i32 %off)
  ret i32 %r
}
define i32 @sim_alignbyte(i32 %hi, i32 %lo, i32 %sh) {
  %r = call i32 @c_alignbyte(i32 %hi, i32 %lo, i32 %sh)
  ret i32 %r
}
define i32 @sim_sad_u8(i32 %a, i32 %b, i32 %c) {
  %r = call i32 @c_sad_u8(i32 %a, i32 %b, i32 %c)
  ret i32 %r
}
define <3 x i32> @sim_buf_load_v3i32(<4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %id = extractelement <4 x i32> %v, i32 0
  %o0 = add i32 %off, %soff
  %o1 = add i32 %o0, 4
  %o2 = add i32 %o0, 8
  %x0 = call i32 @c_buf_load_i32(i32 %id, i32 %o0)
  %x1 = call i32 @c_buf_load_i32(i32 %id, i32 %o1)
  %x2 = call i32 @c_buf_load_i32(i32 %id, i32 %o2)
  %r0 = insertelement <3 x i32> undef, i32 %x0, i32 0
  %r1 = insertelement <3 x i32> %r0, i32 %x1, i32 1
  %r2 = insertelement <3 x i32> %r1, i32 %x2, i32 2
  ret <3 x i32> %r2
}
define <2 x i32> @sim_buf_load_v2i32(<4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %id = extractelement <4 x i32> %v, i32 0
  %o0 = add i32 %off, %soff
  %o1 = add i32 %o0, 4
  %x0 = call i32 @c_buf_load_i32(i32 %id, i32 %o0)
  %x1 = call i32 @c_buf_load_i32(i32 %id, i32 %o1)
  %r0 = insertelement <2 x i32> undef, i32 %x0, i32 0
  %r1 = insertelement <2 x i32> %r0, i32 %x1, i32 1
  ret <2 x i32> %r1
}
define i32 @sim_buf_load_i32(<4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %id = extractelement <4 x i32> %v, i32 0
  %o0 = add i32 %off, %soff
  %x0 = call i32 @c_buf_load_i32(i32 %id, i32 %o0)
  ret i32 %x0
}
define <2 x float> @sim_buf_load_v2f32(<4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %id = extractelement <4 x i32> %v, i32 0
  %o0 = add i32 %off, %soff
  %o1 = add i32 %o0, 4
  %x0 = call float @c_buf_load_f32(i32 %id, i32 %o0)
  %x1 = call float @c_buf_load_f32(i32 %id, i32 %o1)
  %r0 = insertelement <2 x float> undef, float %x0, i32 0
  %r1 = insertelement <2 x float> %r0, float %x1, i32 1
  ret <2 x float> %r1
}
define void @sim_buf_store_i32(i32 %val, <4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %id = extractelement <4 x i32> %v, i32 0
  %o0 = add i32 %off, %soff
  call void @c_buf_store_i32(i32 %val, i32 %id, i32 %o0)
  ret void
}
define void @sim_buf_store_v2f32(<2 x float> %val, <4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %id = extractelement <4 x i32> %v, i32 0
  %o0 = add i32 %off, %soff
  %o1 = add i32 %o0, 4
  %x0 = extractelement <2 x float> %val, i32 0
  %x1 = extractelement <2 x float> %val, i32 1
  call void @c_buf_store_f32(float %x0, i32 %id, i32 %o0)
  call void @c_buf_store_f32(float %x1, i32 %id, i32 %o1)
  ret void
}
"""
    c_src = r"""
#include <stdint.h>
#include <stdlib.h>
#include <ucontext.h>

extern void mf8_cs_main(void *tbl, int32_t gx, int32_t gy, int32_t tid);

static ucontext_t g_ctx[64], g_main_ctx;
static char g_stack[64][65536] __attribute__((aligned(16)));
static int g_cur_tid = 0;
static void *g_tbl = 0;
static int32_t g_gx = 0, g_gy = 0;
static uint32_t g_swz[64];
static void *g_bufs[16];
static uint32_t g_sizes[16];

void c_barrier(void) {
    int cur = g_cur_tid;
    int nxt = (cur + 1) & 63;
    g_cur_tid = nxt;
    swapcontext(&g_ctx[cur], &g_ctx[nxt]);
}
int32_t c_ds_swizzle(int32_t val, int32_t offset) {
    int cur = g_cur_tid;
    g_swz[cur] = (uint32_t)val;
    c_barrier();
    int and_m = offset & 0x1f;
    int or_m  = (offset >> 5) & 0x1f;
    int xor_m = (offset >> 10) & 0x1f;
    int src = (cur & ~31) | ((((cur & 31) & and_m) | or_m) ^ xor_m);
    int32_t res = (int32_t)g_swz[src];
    c_barrier();
    return res;
}
int32_t c_alignbyte(int32_t hi, int32_t lo, int32_t sh) {
    uint64_t u = ((uint64_t)(uint32_t)hi << 32) | (uint32_t)lo;
    return (int32_t)(uint32_t)(u >> ((sh & 3) * 8));
}
int32_t c_sad_u8(int32_t a, int32_t b, int32_t c) {
    uint32_t ua = (uint32_t)a, ub = (uint32_t)b;
    int32_t s = c;
    for (int i = 0; i < 4; i++) {
        int da = (int)((ua >> (i*8)) & 0xff) - (int)((ub >> (i*8)) & 0xff);
        s += da < 0 ? -da : da;
    }
    return s;
}
int32_t c_buf_load_i32(int32_t id, int32_t off) {
    uint8_t *p = (uint8_t*)g_bufs[id]; uint32_t sz = g_sizes[id];
    uint32_t o = (uint32_t)off;
    return (o + 4 <= sz) ? *(int32_t*)(p + o) : 0;
}
float c_buf_load_f32(int32_t id, int32_t off) {
    uint8_t *p = (uint8_t*)g_bufs[id]; uint32_t sz = g_sizes[id];
    uint32_t o = (uint32_t)off;
    return (o + 4 <= sz) ? *(float*)(p + o) : 0.0f;
}
void c_buf_store_i32(int32_t val, int32_t id, int32_t off) {
    uint8_t *p = (uint8_t*)g_bufs[id]; uint32_t sz = g_sizes[id];
    uint32_t o = (uint32_t)off;
    if (o + 4 <= sz) *(int32_t*)(p + o) = val;
}
void c_buf_store_f32(float val, int32_t id, int32_t off) {
    uint8_t *p = (uint8_t*)g_bufs[id]; uint32_t sz = g_sizes[id];
    uint32_t o = (uint32_t)off;
    if (o + 4 <= sz) *(float*)(p + o) = val;
}
static void lane_entry(int tid) {
    mf8_cs_main(g_tbl, g_gx, g_gy, tid);
    if (tid < 63) {
        g_cur_tid = tid + 1;
        setcontext(&g_ctx[tid + 1]);
    } else {
        setcontext(&g_main_ctx);
    }
}
void run_mf8_grid(void *l1c, void *l1p, void *v2, void *f_out, void *misc, void *gk, void *ft, int nx_groups, int ny_groups) {
    uint8_t tbl[1024] __attribute__((aligned(64)));
    for (int i = 0; i < 1024; i++) tbl[i] = 0;
    *(int32_t*)(tbl + 128) = 1; g_bufs[1] = l1c;  g_sizes[1] = 960 * 540;
    *(int32_t*)(tbl + 144) = 2; g_bufs[2] = l1p;  g_sizes[2] = 960 * 540;
    *(int32_t*)(tbl + 240) = 3; g_bufs[3] = v2;   g_sizes[3] = 60 * 34 * 4;
    *(int32_t*)(tbl + 256) = 4; g_bufs[4] = f_out;g_sizes[4] = 120 * 68 * 8;
    *(int32_t*)(tbl + 288) = 5; g_bufs[5] = misc; g_sizes[5] = 64;
    *(int32_t*)(tbl + 336) = 6; g_bufs[6] = gk;   g_sizes[6] = 120 * 68 * 4;
    *(int32_t*)(tbl + 384) = 7; g_bufs[7] = ft;   g_sizes[7] = 120 * 68 * 8;
    g_tbl = tbl;
    for (int gy = 0; gy < ny_groups; gy++) {
        for (int gx = 0; gx < nx_groups; gx++) {
            g_gx = gx; g_gy = gy;
            for (int t = 0; t < 64; t++) {
                getcontext(&g_ctx[t]);
                g_ctx[t].uc_stack.ss_sp = g_stack[t];
                g_ctx[t].uc_stack.ss_size = sizeof(g_stack[t]);
                g_ctx[t].uc_link = 0;
                makecontext(&g_ctx[t], (void(*)(void))lane_entry, 1, t);
            }
            g_cur_tid = 0;
            swapcontext(&g_main_ctx, &g_ctx[0]);
        }
    }
}
"""
    ll_p = td_path / "mf8.ll"
    c_p = td_path / "wrap.c"
    so_p = td_path / "mf8.so"
    ll_p.write_text("\n".join(lines) + "\n" + ir_stubs, encoding="utf-8")
    c_p.write_text(c_src, encoding="utf-8")
    subprocess.run(
        [clang_bin, "-O2", "-ffp-contract=off", "-fPIC", "-shared", "-Wno-override-module", str(ll_p), str(c_p), "-o", str(so_p)],
        check=True,
    )
    lib = ctypes.CDLL(str(so_p))
    lib.run_mf8_grid.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ]
    lib.run_mf8_grid.restype = None

    def runner(l1p: np.ndarray, l1c: np.ndarray, ft: np.ndarray, gk: np.ndarray, v2: np.ndarray, gm: tuple[int, int], fade: int = 0, nx_groups: int = 4, ny_groups: int = 8) -> np.ndarray:
        l1p_u8 = np.ascontiguousarray(l1p.astype(np.uint8))
        l1c_u8 = np.ascontiguousarray(l1c.astype(np.uint8))
        v2_i16 = np.ascontiguousarray(v2.astype(np.int16))
        gk_u32 = np.ascontiguousarray(gk.astype(np.uint32))
        ft_f32 = np.ascontiguousarray(ft.astype(np.float32))
        f_out = np.zeros((68, 120, 2), dtype=np.float32)
        misc = np.zeros(16, dtype=np.int32)
        misc[4] = int(gm[0])
        misc[5] = int(gm[1])
        misc[7] = int(fade)
        lib.run_mf8_grid(
            l1c_u8.ctypes.data_as(ctypes.c_void_p),
            l1p_u8.ctypes.data_as(ctypes.c_void_p),
            v2_i16.ctypes.data_as(ctypes.c_void_p),
            f_out.ctypes.data_as(ctypes.c_void_p),
            misc.ctypes.data_as(ctypes.c_void_p),
            gk_u32.ctypes.data_as(ctypes.c_void_p),
            ft_f32.ctypes.data_as(ctypes.c_void_p),
            int(nx_groups),
            int(ny_groups),
        )
        return f_out

    return runner


class TestT13FastThinSub8Guard(unittest.TestCase):
    def test_01_ir_and_hardware_budget(self) -> None:
        mf8_src = MF8_LL.read_text(encoding="utf-8")
        for tok in (
            "%gm_mag = add i32 %gm_ax, %gm_ay",
            "%fast_gm = icmp sge i32 %gm_mag, 22",
            "%zm_le160 = xor i1 %zm_gt160, true",
            "%zm_le112 = icmp sle i32 %zmin, 112",
            "%dy_ge16 = icmp sge i32 %scdy, 16",
            "%thin_esc1 = and i1 %zm_le160, %thin_esc0",
            "%thin_esc = or i1 %thin_esc1, %per_esc",
            "%fast_thin_bg = and i1 %ftb1, %thin_esc",
            "%ok2 = and i1 %ok2e, %n_ftb",
        ):
            self.assertIn(tok, mf8_src, f"Missing {tok} in classic/shaders/mf8.ll")

        shaders_h = SHADERS_H.read_text(encoding="utf-8")
        m_rsrc1 = re.search(r"#define\s+MF8_RSRC1\s+0x([0-9a-fA-F]+)", shaders_h)
        m_rsrc2 = re.search(r"#define\s+MF8_RSRC2\s+0x([0-9a-fA-F]+)", shaders_h)
        nwords_map = dict(re.findall(r"#define\s+(\w+)_NWORDS\s+(\d+)", shaders_h))
        self.assertIsNotNone(m_rsrc1)
        self.assertIsNotNone(m_rsrc2)
        mf8_vgprs = ((int(m_rsrc1.group(1), 16) & 0x3F) + 1) * 4
        mf8_scratch = int(m_rsrc2.group(1), 16) & 1
        self.assertLessEqual(mf8_vgprs, 64, "MF8 VGPRs must be <= 64")
        self.assertEqual(mf8_scratch, 0, "MF8 scratch must be == 0")
        mode8_set = (
            "PYR8", "ME3", "ME2", "ME1", "PHASE8", "WARP8", "DUMP8", "GM8",
            "FGS8", "MF8", "GV8", "GVC8", "WARP8G", "GVZ8", "RCAS8", "OSD8", "PCOPY8",
        )
        end_off = 0x8000 + sum(((int(nwords_map[k]) * 4 + 255) & ~255) for k in mode8_set)
        self.assertLessEqual(end_off, 0x1C000, f"Shader region end 0x{end_off:X} > 0x1C000")

        if LLC.exists():
            with tempfile.TemporaryDirectory() as td:
                sp = pathlib.Path(td) / "mf8.s"
                subprocess.check_call([str(LLC), "-mtriple=amdgcn--", "-mcpu=gfx700", "-O2", str(MF8_LL), "-o", str(sp)])
                asm = sp.read_text(encoding="utf-8")
                vgprs = int(re.search(r"NumVgprs:\s*(\d+)", asm).group(1))
                scratch = int(re.search(r"ScratchSize:\s*(\d+)", asm).group(1))
                self.assertLessEqual(vgprs, 64)
                self.assertEqual(scratch, 0)

    def test_02_fast_pan_thin_structure_no_false_sub8_split_and_compiled_parity(self) -> None:
        """Under fast camera pan gm = (11, 0) L2 (= 44 full-res px/frame), a 2-3 px vertical pole / palm trunk
        and a diagonal lattice strut over smooth sky (ztex_min <= 112 and ztex_min <= 160 with |cy - gmy2| >= 16)
        must NOT suffer false Pass 2 SUB8 quadrant splits from smooth-sky or aperture-sliding neighbour outliers,
        and compiled mf8.ll must match vcfg_motion.mf8 bit-for-bit.
        """
        yy, xx = np.mgrid[0:128, 0:256].astype(F32)
        vx_cam = F32(44.0)
        gm = (np.int32(11), np.int32(0))  # gmx2 = 22, gm_mag = 22 >= 22

        def render_frame(t_shift: float) -> np.ndarray:
            # Smooth sky gradient (ztex_min <= 112 at vacated midpoint)
            sky = np.stack(
                [
                    0.56 + 0.02 * (yy / 128.0),
                    0.70 + 0.02 * (yy / 128.0),
                    0.84 + 0.01 * (yy / 128.0),
                ],
                axis=-1,
            ).astype(F32)
            x_world = xx - t_shift * vx_cam
            # Thin 3-px vertical trunk/pole at x_world in [82, 85), y in [16, 96) (inside block bx=5, by=2..5)
            pole = (yy >= 16) & (yy < 96) & (x_world >= 82.0) & (x_world < 85.0)
            # Thin 2-px diagonal lattice strut at block bx=9, by=3..5
            strut_x = 148.0 + 0.35 * (yy - 48.0)
            strut = (yy >= 48) & (yy < 96) & (np.abs(x_world - strut_x) < 1.2)
            thin_col = np.array([0.12, 0.14, 0.10], dtype=F32)
            return np.where((pole | strut)[..., None], thin_col, sky)

        tp = render_frame(+0.5)
        gt = render_frame(0.0)
        tc = render_frame(-0.5)

        L1p_full = np.full((540, 960), 175, dtype=np.int64)
        L1c_full = np.full((540, 960), 175, dtype=np.int64)
        p1, _, _ = vr.pyr8(tp)
        c1, _, _ = vr.pyr8(tc)
        L1p_full[:64, :128] = p1
        L1c_full[:64, :128] = c1

        FT = np.zeros((68, 120, 2), dtype=F32)
        FT[:, :, 0] = vx_cam
        # Inject the two real CUSA00411 Pass 2 outlier types in 2-hop neighbours:
        # 1) Smooth-sky near-zero escape (2, 0) at (by=3, bx=3) -> 2-hop neighbour ox=-2 of pole block (by=3, bx=5)
        FT[3, 3] = np.array([2.0, 0.0], dtype=F32)
        # 2) Diagonal aperture outlier (40, -54) (|cy - gmy2| = 27 >= 16) at (by=4, bx=11) -> 2-hop neighbour ox=+2 of strut block (by=4, bx=9)
        FT[4, 11] = np.array([40.0, -54.0], dtype=F32)

        GK = np.zeros((68, 120), dtype=np.int64)
        V2 = np.zeros((34, 60, 2), dtype=np.int64)
        V2[:, :, 0] = 11

        F_py = vm.mf8(L1p_full, L1c_full, FT, GK, V2, gm, fade=0)
        m_pole = int(F_py[3, 5, 0].view(np.uint32) & np.uint32(0xF))
        m_strut = int(F_py[4, 9, 0].view(np.uint32) & np.uint32(0xF))
        self.assertEqual(m_pole, 0, f"Pole block (3,5) must not suffer false SUB8 split under fast pan, got mask={m_pole}")
        self.assertEqual(m_strut, 0, f"Strut block (4,9) must not suffer false SUB8 split under fast pan, got mask={m_strut}")

        # Verify warp8 reconstructs the thin pole and strut without erasing them into sky background
        out_warp = vw.warp8(tp, tc, F_py[:8, :16], GK[:8, :16], cutn=0, t=0.5)
        pole_roi_err = int(np.sum(np.abs(out_warp[48:64, 80:96] - gt[48:64, 80:96]).max(-1) > F32(0.08)))
        strut_roi_err = int(np.sum(np.abs(out_warp[64:80, 144:160] - gt[64:80, 144:160]).max(-1) > F32(0.08)))
        self.assertEqual(pole_roi_err, 0, f"Thin pole pixels erased in warp8: {pole_roi_err} error px")
        self.assertLessEqual(strut_roi_err, 2, f"Thin strut pixels erased in warp8: {strut_roi_err} error px")

        # Verify bit-for-bit parity between compiled mf8.ll and Python vcfg_motion.mf8
        with tempfile.TemporaryDirectory() as td:
            runner = _build_compiled_mf8_runner(MF8_LL.read_text(encoding="utf-8"), pathlib.Path(td))
            F_c = runner(L1p_full, L1c_full, FT, GK, V2, gm, fade=0, nx_groups=4, ny_groups=8)
            bit_diffs = int(np.count_nonzero(F_c[:8, :16].view(np.uint32) != F_py[:8, :16].view(np.uint32)))
            self.assertEqual(bit_diffs, 0, f"Compiled mf8.ll vs vcfg_motion.mf8 bit diffs: {bit_diffs}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
