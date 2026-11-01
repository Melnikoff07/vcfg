#!/usr/bin/env python3
"""T6/T8 regression test: fgs8.ll local anchor_hist hysteresis executed via clang-22 (CUSA08519).

Verifies:
1. Compiled classic/shaders/fgs8.ll (executed on CPU via clang-22 + ctypes):
   - Requires 2 consecutive frames of %isa0 from h == 0 (h: 0 -> 2 -> 4 >= 3) before activating prio=0 anchor.
   - Expands quasi-static velocity threshold from |V2|^2 <= 2 to |V2|^2 <= 9 only when has_h (h > 0).
   - Holds anchor via %ank for at most N = 2 frames (h: 5 -> 4 -> 3 >= 3, then 2 < 3) when %good & %dev
     dips on passing clouds while (%has_h & %nstl & %istex & %rm & %slw & %inb & %aon & %nbk) holds.
2. Single random frame of %isa0 at h == 0 (without %strong_an) does NOT create an anchor on frame 1 or frame 2.
3. After a scene cut (or when camera stops / texture disappears / background moves), anchor does NOT stick:
   - Immediate release (0 frames) when any of (%nstl, %istex, %rm, %slw, %nbk) fails.
   - Bounded release (at most N = 2 frames, never sticking) even if (%nstl & %istex & %rm & %slw & %nbk) remains true.
4. Bit-exact 1-to-1 parity between compiled fgs8.ll (clang-22) and tools/bench/vcfg_motion.py::fgs8,
   with no synth_unit backdoor, and exact match of mf8.ll / warp8.ll / vcfg_warp.py with 2d032b1.
5. Hardware shader budget via /usr/bin/llc-22 -mcpu=gfx700 -O2 (VGPR <= 64, scratch = 0, region <= 0x1C000).
6. Candidate-independent head-hole metric on seq5 and seq4 (2d032b1 vs B with N=1 and N=2).
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

import analyze_seq_fgkill as asf
import vcfg_motion as vm

FGS8_LL = ROOT / "classic" / "shaders" / "fgs8.ll"
MF8_LL = ROOT / "classic" / "shaders" / "mf8.ll"
WARP8_LL = ROOT / "classic" / "shaders" / "warp8.ll"
LLC = pathlib.Path("/usr/bin/llc-22")

SEQ5_DIR = pathlib.Path(
    "/home/melnikoff/vcfg-pipeline/runtime/research/owner-seq5-clouds/bench_CUSA08519_00000003cf4253c9"
)
SEQ4_DIR = pathlib.Path(
    "/home/melnikoff/vcfg-pipeline/runtime/research/owner-seq4-24d7b38d/bench_CUSA08519_00000003d5a17ab2"
)


def _find_clang() -> str:
    for cand in ("/usr/bin/clang-22", shutil.which("clang-22"), shutil.which("clang")):
        if cand and pathlib.Path(cand).exists():
            return str(cand)
    return ""


def _compile_fgs8_ll_runner(fgs8_src: str, td_path: pathlib.Path):
    """Compile AMDGPU fgs8.ll into a host shared library via clang-22 and return a callable runner."""
    clang_bin = _find_clang()
    if not clang_bin:
        raise RuntimeError("clang-22 not found")
    target_triple = subprocess.check_output([clang_bin, "-dumpmachine"], text=True).strip()

    ir = fgs8_src.replace('target triple = "amdgcn--"', f'target triple = "{target_triple}"')
    ir = ir.replace(
        "define amdgpu_cs void @main(ptr addrspace(4) inreg %tbl, i32 inreg %gx, i32 %tid) #0",
        "define void @fgs8_cs_main(ptr %tbl, i32 %gx, i32 %tid)",
    )
    ir = ir.replace("ptr addrspace(4)", "ptr")
    ir = ir.replace(", !invariant.load !0", "")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.i8", "@sim_load_i8")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.store.i8", "@sim_store_i8")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.i32", "@sim_load_i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.load.v3i32", "@sim_load_v3i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.atomic.umax.i32", "@sim_atomic_umax_i32")
    ir = ir.replace("@llvm.amdgcn.raw.buffer.atomic.add.i32", "@sim_atomic_add_i32")
    ir = ir.replace("@llvm.amdgcn.alignbyte", "@sim_alignbyte")
    ir = ir.replace("@llvm.amdgcn.sad.u8", "@sim_sad_u8")

    lines = [
        ln
        for ln in ir.splitlines()
        if not (ln.startswith("declare ") and "@sim_" in ln)
        and not ln.startswith("attributes #0")
    ]
    ir_stubs = """
declare i8 @c_load_i8(i32, i32)
declare void @c_store_i8(i8, i32, i32)
declare i32 @c_load_i32(i32, i32)
declare i32 @c_atomic_umax_i32(i32, i32, i32)
declare i32 @c_atomic_add_i32(i32, i32, i32)
declare i32 @c_alignbyte(i32, i32, i32)
declare i32 @c_sad_u8(i32, i32, i32)

define i8 @sim_load_i8(<4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %bid = extractelement <4 x i32> %v, i32 0
  %r = call i8 @c_load_i8(i32 %bid, i32 %off)
  ret i8 %r
}
define void @sim_store_i8(i8 %val, <4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %bid = extractelement <4 x i32> %v, i32 0
  call void @c_store_i8(i8 %val, i32 %bid, i32 %off)
  ret void
}
define i32 @sim_load_i32(<4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %bid = extractelement <4 x i32> %v, i32 0
  %r = call i32 @c_load_i32(i32 %bid, i32 %off)
  ret i32 %r
}
define <3 x i32> @sim_load_v3i32(<4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %bid = extractelement <4 x i32> %v, i32 0
  %off1 = add i32 %off, 4
  %off2 = add i32 %off, 8
  %w0 = call i32 @c_load_i32(i32 %bid, i32 %off)
  %w1 = call i32 @c_load_i32(i32 %bid, i32 %off1)
  %w2 = call i32 @c_load_i32(i32 %bid, i32 %off2)
  %v0 = insertelement <3 x i32> undef, i32 %w0, i32 0
  %v1 = insertelement <3 x i32> %v0, i32 %w1, i32 1
  %v2 = insertelement <3 x i32> %v1, i32 %w2, i32 2
  ret <3 x i32> %v2
}
define i32 @sim_atomic_umax_i32(i32 %val, <4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %bid = extractelement <4 x i32> %v, i32 0
  %r = call i32 @c_atomic_umax_i32(i32 %val, i32 %bid, i32 %off)
  ret i32 %r
}
define i32 @sim_atomic_add_i32(i32 %val, <4 x i32> %v, i32 %off, i32 %soff, i32 %aux) {
  %bid = extractelement <4 x i32> %v, i32 0
  %r = call i32 @c_atomic_add_i32(i32 %val, i32 %bid, i32 %off)
  ret i32 %r
}
define i32 @sim_alignbyte(i32 %hi, i32 %lo, i32 %sh) {
  %r = call i32 @c_alignbyte(i32 %hi, i32 %lo, i32 %sh)
  ret i32 %r
}
define i32 @sim_sad_u8(i32 %a, i32 %b, i32 %acc) {
  %r = call i32 @c_sad_u8(i32 %a, i32 %b, i32 %acc)
  ret i32 %r
}
"""
    c_src = r"""
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

extern void fgs8_cs_main(const void *tbl, int32_t gx, int32_t tid);

static uint8_t *g_bufs[8];
static int32_t g_sizes[8];

uint8_t c_load_i8(int32_t bid, int32_t off) {
    if (bid < 1 || bid > 7 || off < 0 || off >= g_sizes[bid]) return 0;
    return g_bufs[bid][off];
}
void c_store_i8(uint8_t val, int32_t bid, int32_t off) {
    if (bid < 1 || bid > 7 || off < 0 || off >= g_sizes[bid]) return;
    g_bufs[bid][off] = val;
}
int32_t c_load_i32(int32_t bid, int32_t off) {
    if (bid < 1 || bid > 7 || off < 0 || off + 4 > g_sizes[bid]) return 0;
    int32_t v;
    memcpy(&v, g_bufs[bid] + off, 4);
    return v;
}
int32_t c_atomic_umax_i32(int32_t val, int32_t bid, int32_t off) {
    if (bid < 1 || bid > 7 || off < 0 || off + 4 > g_sizes[bid]) return 0;
    uint32_t old_v, uval = (uint32_t)val;
    memcpy(&old_v, g_bufs[bid] + off, 4);
    if (uval > old_v) {
        memcpy(g_bufs[bid] + off, &uval, 4);
    }
    return (int32_t)old_v;
}
int32_t c_atomic_add_i32(int32_t val, int32_t bid, int32_t off) {
    if (bid < 1 || bid > 7 || off < 0 || off + 4 > g_sizes[bid]) return 0;
    int32_t old_v;
    memcpy(&old_v, g_bufs[bid] + off, 4);
    int32_t new_v = old_v + val;
    memcpy(g_bufs[bid] + off, &new_v, 4);
    return old_v;
}
int32_t c_alignbyte(int32_t hi, int32_t lo, int32_t sh) {
    uint64_t pair = ((uint64_t)(uint32_t)hi << 32) | (uint64_t)(uint32_t)lo;
    uint32_t s = ((uint32_t)sh & 3u) * 8u;
    return (int32_t)(uint32_t)(pair >> s);
}
int32_t c_sad_u8(int32_t a, int32_t b, int32_t acc) {
    uint32_t ua = (uint32_t)a, ub = (uint32_t)b;
    int32_t s = acc;
    for (int k = 0; k < 4; ++k) {
        int da = (int)((ua >> (k * 8)) & 0xFFu);
        int db = (int)((ub >> (k * 8)) & 0xFFu);
        s += abs(da - db);
    }
    return s;
}

void run_fgs8_kernel(
    uint8_t *l2p, uint8_t *l2c, uint8_t *v3_buf, uint8_t *v2_buf,
    uint8_t *misc_buf, uint8_t *s2_buf, uint8_t *gk_buf, uint32_t flags
) {
    g_bufs[1] = l2c; g_sizes[1] = 480 * 270 + 16;
    g_bufs[2] = l2p; g_sizes[2] = 480 * 270 + 16;
    g_bufs[3] = v3_buf; g_sizes[3] = 4096;
    g_bufs[4] = v2_buf; g_sizes[4] = 2040 * 4;
    g_bufs[5] = misc_buf; g_sizes[5] = 32;
    g_bufs[6] = s2_buf; g_sizes[6] = 2040 * 4;
    g_bufs[7] = gk_buf; g_sizes[7] = 120 * 68 * 4;

    uint32_t tbl[128];
    memset(tbl, 0, sizeof(tbl));
    tbl[112 / 4 + 2] = flags;
    tbl[160 / 4] = 1;
    tbl[176 / 4] = 2;
    tbl[224 / 4] = 3;
    tbl[240 / 4] = 4;
    tbl[288 / 4] = 5;
    tbl[320 / 4] = 6;
    tbl[336 / 4] = 7;

    for (int32_t gx = 0; gx < 32; ++gx) {
        for (int32_t tid = 0; tid < 64; ++tid) {
            fgs8_cs_main(tbl, gx, tid);
        }
    }
}
"""
    ll_p = td_path / "fgs8_sim.ll"
    c_p = td_path / "driver.c"
    so_p = td_path / "libfgs8.so"
    ll_p.write_text("\n".join(lines) + "\n" + ir_stubs, encoding="utf-8")
    c_p.write_text(c_src, encoding="utf-8")
    subprocess.check_call(
        [clang_bin, "-O2", "-fPIC", "-shared", "-Wno-override-module", str(ll_p), str(c_p), "-lm", "-o", str(so_p)]
    )
    lib = ctypes.CDLL(str(so_p))
    lib.run_fgs8_kernel.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    lib.run_fgs8_kernel.restype = None

    def run(L2p, L2c, V2, S2, gm, anchor_hist=None, flags=0):
        l2p_pad = np.zeros(480 * 270 + 16, dtype=np.uint8)
        l2c_pad = np.zeros(480 * 270 + 16, dtype=np.uint8)
        l2p_pad[: 480 * 270] = np.asarray(L2p, dtype=np.uint8).ravel()
        l2c_pad[: 480 * 270] = np.asarray(L2c, dtype=np.uint8).ravel()
        v3_buf = np.zeros(4096, dtype=np.uint8)
        if anchor_hist is not None:
            v3_buf[2048 : 2048 + 2040] = np.asarray(anchor_hist, dtype=np.uint8).ravel()
        v2_i16 = np.asarray(V2, dtype="<i2").reshape(2040, 2)
        v2_u32 = (
            (v2_i16[:, 0].astype(np.uint32) & 0xFFFF) | ((v2_i16[:, 1].astype(np.uint32) & 0xFFFF) << 16)
        ).astype("<u4")
        s2_arr = np.asarray(S2, dtype=np.uint32)
        if s2_arr.ndim == 3:
            s2_arr = s2_arr[..., 0]
        s2_u32 = (s2_arr.ravel() & 0xFFFF).astype("<u4")
        misc_i32 = np.zeros(8, dtype="<i4")
        misc_i32[4] = int(gm[0])
        misc_i32[5] = int(gm[1])
        gk_u32 = np.zeros(68 * 120, dtype="<u4")
        lib.run_fgs8_kernel(
            l2p_pad.ctypes.data,
            l2c_pad.ctypes.data,
            v3_buf.ctypes.data,
            v2_u32.ctypes.data,
            misc_i32.ctypes.data,
            s2_u32.ctypes.data,
            gk_u32.ctypes.data,
            ctypes.c_uint32(flags),
        )
        return gk_u32.reshape(68, 120).astype(np.int64), v3_buf[2048 : 2048 + 2040].copy()

    return run


def _make_l2_test_scene(
    by_test: int = 10,
    bx_test: int = 20,
    gm: tuple[int, int] = (-18, 0),
    vx_cell: int = 0,
    vy_cell: int = 0,
    sb_cell: int = 20,
    cloud_occlude_cam: bool = False,
    textured: bool = True,
    num_coherent_neighbors: int = 1,
):
    """Construct L2p, L2c (270x480 uint8), V2 (34x60x2 int32), S2 (34x60x1 int32) for testing fgs8.ll."""
    H2, W2 = 270, 480
    yy, xx = np.meshgrid(np.arange(H2, dtype=np.int32), np.arange(W2, dtype=np.int32), indexing="ij")
    if textured:
        # High-frequency checkerboard/gradient texture so texm >= 64 and gsad at camera shift gm is large (~400+)
        base_tex = (((xx * 73) ^ (yy * 41) ^ ((xx & 1) * 180) ^ ((yy & 1) * 90)) & 0xFF).astype(np.uint8)
    else:
        base_tex = np.full((H2, W2), 128, dtype=np.uint8)

    L2p = base_tex.copy()
    L2c = np.roll(base_tex, (gm[1], gm[0]), axis=(0, 1))

    y0, x0 = by_test * 8, bx_test * 8
    if textured:
        patch = (((xx[y0 : y0 + 8, x0 : x0 + 8] * 113) + (yy[y0 : y0 + 8, x0 : x0 + 8] * 67)) & 0xFF).astype(
            np.uint8
        )
        # Ensure strong horizontal/vertical adjacent pixel differences inside the 8x8 block (texm >= 300)
        patch[::2, ::2] = 20
        patch[1::2, 1::2] = 235
    else:
        patch = np.full((8, 8), 128, dtype=np.uint8)

    L2c[y0 : y0 + 8, x0 : x0 + 8] = patch
    xp = np.clip(x0 + vx_cell, 0, W2 - 8)
    yp = np.clip(y0 + vy_cell, 0, H2 - 8)
    L2p[yp : yp + 8, xp : xp + 8] = patch

    if cloud_occlude_cam:
        # Place the same patch at the camera-shifted location in L2p so gsad (camera match SAD) drops to 0,
        # making good = (5*sb <= 4*gsad) & (gsad - sb >= 96) fail while istex & rm & slw & nstl & nbk remain True!
        x_cam = np.clip(x0 + gm[0], 0, W2 - 8)
        y_cam = np.clip(y0 + gm[1], 0, H2 - 8)
        L2p[y_cam : y_cam + 8, x_cam : x_cam + 8] = L2c[y0 : y0 + 8, x0 : x0 + 8]

    V2 = np.zeros((34, 60, 2), dtype=np.int32)
    V2[:, :, 0] = gm[0]
    V2[:, :, 1] = gm[1]
    V2[by_test, bx_test] = [vx_cell, vy_cell]

    nb_offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    for k in range(min(num_coherent_neighbors, 4)):
        ox, oy = nb_offsets[k]
        V2[by_test + oy, bx_test + ox] = [vx_cell, vy_cell]

    S2 = np.full((34, 60, 1), 500, dtype=np.int32)
    S2[by_test, bx_test, 0] = sb_cell
    return L2p.astype(np.int64), L2c.astype(np.int64), V2, S2


def _compile_shader_stats(ll_path: pathlib.Path) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as td:
        td_path = pathlib.Path(td)
        sp_path = td_path / "out.s"
        op_path = td_path / "out.o"
        subprocess.check_call(
            [str(LLC), "-mtriple=amdgcn--", "-mcpu=gfx700", "-O2", str(ll_path), "-o", str(sp_path)]
        )
        subprocess.check_call(
            [
                str(LLC),
                "-mtriple=amdgcn--",
                "-mcpu=gfx700",
                "-O2",
                "-filetype=obj",
                str(ll_path),
                "-o",
                str(op_path),
            ]
        )
        asm = sp_path.read_text(encoding="utf-8")
        obj = subprocess.check_output(["/usr/bin/llvm-objdump-22", "-h", str(op_path)], text=True)
        vgprs = int(re.search(r"NumVgprs:\s*(\d+)", asm).group(1))
        sgprs = int(re.search(r"NumSgprs:\s*(\d+)", asm).group(1))
        scratch = int(re.search(r"ScratchSize:\s*(\d+)", asm).group(1))
        text_bytes = 0
        for line in obj.splitlines():
            if ".text" in line:
                text_bytes = int(line.split()[2], 16)
                break
        return {
            "dwords": text_bytes // 4,
            "vgprs": vgprs,
            "sgprs": sgprs,
            "scratch": scratch,
        }


def evaluate_candidate_independent_seq_metrics(seq_dir: pathlib.Path) -> dict[str, dict]:
    """Candidate-independent head-hole and flip evaluation on a 32-step console capture (seq5 or seq4).
    The metric function has ZERO candidate-specific branches: every variant supplies only its L2 anchor
    selection `gk_eff` (from fgs8), and the exact same formula evaluates `GK==0` head cells, `fast&GK==0`
    head hole cells, and 2d032b1 warp8 fgkill flips.
    """
    import model_seq_flicker_fixes as msf

    steps = [asf.parse_seq_step(bf.read_bytes()) for bf in sorted(seq_dir.glob("bench_seq_[0-9][0-9][0-9][0-9].bin"))]
    m_steps = len(steps)
    fgmask_seq = np.stack([s["fgmask"] for s in steps], axis=0)
    gk_seq = np.stack([s["GK_u32"] for s in steps], axis=0)
    anc_seq = np.stack([s["anchor_hist"] for s in steps], axis=0)
    v2_seq = np.stack([s["V2"] for s in steps], axis=0).astype(np.int32)
    fcrop_seq = np.stack([s["F_head_crop"] for s in steps], axis=0)
    gms = np.array([(s["misc"]["fg_vx_sum"], s["misc"]["fg_vy_sum"]) for s in steps], dtype=np.int32)
    gmv = (gms[:, 0] ** 2 + gms[:, 1] ** 2)[:, None, None]
    nstl = ~(gmv <= 4)

    gy0, gy1 = asf.FCROP_GY0, asf.FCROP_GY1
    gx0, gx1 = asf.FCROP_GX0, asf.FCROP_GX1
    by_grid = np.arange(gy0, gy1)[:, None] >> 1
    bx_grid = np.arange(gx0, gx1)[None, :] >> 1

    vv_l2 = v2_seq[..., 0] ** 2 + v2_seq[..., 1] ** 2
    inb_l2 = np.zeros((34, 60), dtype=bool)
    inb_l2[1:33, 1:59] = True

    vx_l2, vy_l2 = v2_seq[..., 0], v2_seq[..., 1]
    nbk_l2 = np.zeros((m_steps, 34, 60), dtype=bool)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        ny = np.clip(np.arange(34)[:, None] + dy, 0, 33)
        nx = np.clip(np.arange(60)[None, :] + dx, 0, 59)
        valid_nb = (
            (np.arange(34)[:, None] + dy >= 0)
            & (np.arange(34)[:, None] + dy < 34)
            & (np.arange(60)[None, :] + dx >= 0)
            & (np.arange(60)[None, :] + dx < 60)
        )
        dist = np.abs(vx_l2[:, ny, nx] - vx_l2) + np.abs(vy_l2[:, ny, nx] - vy_l2)
        nbk_l2 |= valid_nb[None, :, :] & (dist <= 4)

    l2_in_gk = np.zeros((m_steps, 34, 60), dtype=bool)
    for s in range(m_steps):
        active_gk = gk_seq[s] > 0
        srcs = gk_seq[s][active_gk] & 0xFFF
        l2_in_gk[s].ravel()[srcs] = True

    _, _, vmg_raw, _ = msf.unpack_fcrop_flow(fcrop_seq)
    vv_crop = vv_l2[:, by_grid, bx_grid]
    gk_orig_crop = gk_seq[:, gy0:gy1, gx0:gx1]
    is_anc_orig = (anc_seq[:, by_grid, bx_grid] > 0) | ((gk_orig_crop > 0) & (gk_orig_crop < 0x100000))
    ever_head = np.maximum.accumulate(is_anc_orig, axis=0)
    head_mask = (vv_crop <= 6) & ever_head
    head_tot = int(np.count_nonzero(head_mask))

    def measure_gk(gk_eff: np.ndarray) -> dict:
        gk_c = gk_eff[:, gy0:gy1, gx0:gx1]
        hasfg_c = gk_c != 0
        hasfg_full = gk_eff != 0
        unanc = int(np.count_nonzero(head_mask & (~hasfg_c)))
        fast_unanc = int(np.count_nonzero(head_mask & (~hasfg_c) & (vmg_raw > 16.0)))

        mfit_crop = (fgmask_seq[:, gy0:gy1, gx0:gx1] & asf.FGMASK_MFIT) != 0
        vmg_hi_crop = (fgmask_seq[:, gy0:gy1, gx0:gx1] & asf.FGMASK_VMG_HI) != 0
        mov_obj = hasfg_c | vmg_hi_crop
        fgkill_ws = hasfg_c & mfit_crop
        fgkill = mov_obj & mfit_crop

        in_crop_mask = np.zeros((68, 120), dtype=bool)
        in_crop_mask[gy0:gy1, gx0:gx1] = True
        out_metrics = msf.compute_outside_crop_metrics(
            hasfg_full,
            (fgmask_seq & asf.FGMASK_MFIT) != 0,
            (fgmask_seq & asf.FGMASK_VMG_HI) != 0,
            ~in_crop_mask,
            vel_ramp_ws=False,
        )
        dec = msf.compute_metrics_for_decision(
            hasfg_c, mov_obj, fgkill_ws, fgkill, steps_data=steps, outside_crop=out_metrics
        )
        return {
            "head_cells": head_tot,
            "unanc_cells": unanc,
            "unanc_pct": unanc / max(head_tot, 1) * 100.0,
            "fast_unanc_hole_cells": fast_unanc,
            "fast_unanc_hole_pct": fast_unanc / max(head_tot, 1) * 100.0,
            "flips_tight": dec["head_tight"]["fgkill_flips"],
            "flips_tight_ws": dec["head_tight"]["fgkill_ws_flips"],
            "flips_crop": dec["f_head_crop_box"]["fgkill_flips"],
            "flips_crop_ws": dec["f_head_crop_box"]["fgkill_ws_flips"],
            "flips_outside_ws": out_metrics["fgkill_ws_flips"],
        }

    def run_b_sim(inc: int, thr: int, cap: int) -> np.ndarray:
        gk_eff = gk_seq.copy()
        h_sim = np.zeros_like(anc_seq, dtype=np.int32)
        isa0_0 = anc_seq[0] > 0
        h_sim[0] = np.where(isa0_0, np.minimum(inc, cap), 0)
        for s in range(1, m_steps):
            hp = h_sim[s - 1]
            has_h = hp > 0
            vv_thr = np.where(has_h, 9, 2)
            slw_m = (vv_l2[s] <= vv_thr) & (4 * vv_l2[s] <= gmv[s])
            isa0_s = (anc_seq[s] > 0) | (has_h & l2_in_gk[s] & slw_m & inb_l2 & nbk_l2[s])
            ank_s = has_h & nstl[s] & slw_m & inb_l2 & nbk_l2[s]
            hn = np.minimum(np.where(isa0_s, hp + inc, np.maximum(hp - 1, 0)), cap)
            h_sim[s] = hn
            isan_s = (isa0_s | ank_s) & (hn >= thr)
            gk_eff[s] = msf._splat_l2_anchors_to_gk(gk_eff[s], isan_s, v2_seq[s])
        return gk_eff

    return {
        "baseline_2d032b1": measure_gk(gk_seq),
        "B_hold_N1": measure_gk(run_b_sim(inc=2, thr=3, cap=4)),
        "B_hold_N2": measure_gk(run_b_sim(inc=2, thr=3, cap=5)),
    }


class TestT6HeadHoleFix(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._td = tempfile.TemporaryDirectory()
        cls.td_path = pathlib.Path(cls._td.name)
        cls.run_fgs8_ll = staticmethod(_compile_fgs8_ll_runner(FGS8_LL.read_text(encoding="utf-8"), cls.td_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._td.cleanup()

    def test_01_fgs8_ll_local_hysteresis_and_hold_clang22(self) -> None:
        """Execute compiled fgs8.ll via clang-22 on a 6-frame head-over-cloud sequence:
        - Frame 1 (h=0 -> 2): 1st frame of isa0 (without strong_an) does NOT activate anchor (hn=2 < 3).
        - Frame 2 (h=2 -> 4): 2nd consecutive frame of isa0 activates anchor (hn=4 >= 3, GK != 0).
        - Frame 3 (h=4 -> 5): head shifts to V2=(3,0) (vv=9 <= 9 via has_h), stays anchor (hn=5 >= 3, GK != 0).
        - Frames 4 & 5 (h=5 -> 4 -> 3): cloud behind head causes good&dev to fail (isa0=False) while
          ank=(has_h & nstl & istex & rm & slw & inb & aon & nbk) holds anchor for N=2 frames (hn=4, 3 >= 3).
        - Frame 6 (h=3 -> 2): on the 3rd cloud frame, hn=2 < 3 so anchor turns OFF (GK == 0).
        """
        by_t, bx_t = 10, 20
        idx_l2 = by_t * 60 + bx_t
        gy_t, gx_t = by_t * 2, bx_t * 2
        gm = (-18, 0)

        # Frame 1 & 2: stationary textured head block (vx=0, vy=0, vv=0 <= 2), 1 coherent neighbor (strong_an=False)
        L2p_0, L2c_0, V2_0, S2_0 = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=20, cloud_occlude_cam=False, num_coherent_neighbors=1
        )
        gk1, h1 = self.run_fgs8_ll(L2p_0, L2c_0, V2_0, S2_0, gm, anchor_hist=None)
        self.assertEqual(int(h1[idx_l2]), 2, "Frame 1: h must increment from 0 to 2")
        self.assertEqual(int(gk1[gy_t, gx_t]), 0, "Frame 1: 1st frame of isa0 (without strong_an) must NOT splat anchor")

        gk2, h2 = self.run_fgs8_ll(L2p_0, L2c_0, V2_0, S2_0, gm, anchor_hist=h1)
        self.assertEqual(int(h2[idx_l2]), 4, "Frame 2: h must increment from 2 to 4")
        self.assertGreater(int(gk2[gy_t, gx_t]), 0, "Frame 2: 2nd consecutive frame of isa0 must activate anchor")
        self.assertLess(int(gk2[gy_t, gx_t]), 0x100000, "Frame 2: splatted GK must be a prio=0 anchor")

        # Frame 3: head shifts slightly (vx=3, vy=0 -> vv=9 > 2, <= 9 via has_h)
        L2p_v9, L2c_v9, V2_v9, S2_v9 = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=3, vy_cell=0, sb_cell=20, cloud_occlude_cam=False, num_coherent_neighbors=1
        )
        gk3, h3 = self.run_fgs8_ll(L2p_v9, L2c_v9, V2_v9, S2_v9, gm, anchor_hist=h2)
        self.assertEqual(int(h3[idx_l2]), 5, "Frame 3 (vv=9 with has_h): h must increment to cap 5 (not reset to 0)")
        self.assertGreater(int(gk3[gy_t, gx_t]), 0, "Frame 3 (vv=9 with has_h): GK must remain non-zero")

        # Frames 4, 5, 6: cloud behind head makes camera SAD gsad small (good&dev=False -> isa0=False),
        # while ank=(has_h & nstl & istex & rm & slw & inb & aon & nbk) holds for N=2 frames (h: 5->4->3), then off at Frame 6 (h=2).
        L2p_cl, L2c_cl, V2_cl, S2_cl = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=20, cloud_occlude_cam=True, num_coherent_neighbors=1
        )
        gk4, h4 = self.run_fgs8_ll(L2p_cl, L2c_cl, V2_cl, S2_cl, gm, anchor_hist=h3)
        self.assertEqual(int(h4[idx_l2]), 4, "Frame 4 (cloud drop 1): h must decay from 5 to 4")
        self.assertGreater(int(gk4[gy_t, gx_t]), 0, "Frame 4 (cloud drop 1): ank must hold anchor on frame 1 of drop")

        gk5, h5 = self.run_fgs8_ll(L2p_cl, L2c_cl, V2_cl, S2_cl, gm, anchor_hist=h4)
        self.assertEqual(int(h5[idx_l2]), 3, "Frame 5 (cloud drop 2): h must decay from 4 to 3")
        self.assertGreater(int(gk5[gy_t, gx_t]), 0, "Frame 5 (cloud drop 2): ank must hold anchor on frame 2 of drop")

        gk6, h6 = self.run_fgs8_ll(L2p_cl, L2c_cl, V2_cl, S2_cl, gm, anchor_hist=h5)
        self.assertEqual(int(h6[idx_l2]), 2, "Frame 6 (cloud drop 3): h must decay from 3 to 2")
        self.assertEqual(
            int(gk6[gy_t, gx_t]), 0, "Frame 6 (cloud drop 3): anchor hold must NOT exceed N=2 frames (hn=2 < 3)"
        )

    def test_02_single_random_isa0_frame_does_not_create_anchor_clang22(self) -> None:
        """Verify that 1 random frame of %isa0 starting from h == 0 (without %strong_an) does NOT create
        an anchor on Frame 1 (hn=2 < 3) or Frame 2 (hn=1 < 3, even when %ank conditions hold).
        """
        by_t, bx_t = 12, 25
        idx_l2 = by_t * 60 + bx_t
        gy_t, gx_t = by_t * 2, bx_t * 2
        gm = (-18, 0)

        L2p_1, L2c_1, V2_1, S2_1 = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=20, cloud_occlude_cam=False, num_coherent_neighbors=1
        )
        gk1, h1 = self.run_fgs8_ll(L2p_1, L2c_1, V2_1, S2_1, gm, anchor_hist=None)
        self.assertEqual(int(gk1[gy_t, gx_t]), 0, "Single random isa0 frame at h=0 must NOT create an anchor on Frame 1")

        # Frame 2: isa0=False, even with ank_pre & nbk True
        L2p_2, L2c_2, V2_2, S2_2 = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=20, cloud_occlude_cam=True, num_coherent_neighbors=1
        )
        gk2, h2 = self.run_fgs8_ll(L2p_2, L2c_2, V2_2, S2_2, gm, anchor_hist=h1)
        self.assertEqual(int(gk2[gy_t, gx_t]), 0, "Single random isa0 frame at h=0 must NOT create an anchor on Frame 2")
        self.assertEqual(int(h2[idx_l2]), 1)

        gk3, h3 = self.run_fgs8_ll(L2p_2, L2c_2, V2_2, S2_2, gm, anchor_hist=h2)
        self.assertEqual(int(gk3[gy_t, gx_t]), 0)
        self.assertEqual(int(h3[idx_l2]), 0)

    def test_03_after_scene_cut_anchor_does_not_stick_clang22(self) -> None:
        """Verify that after a scene cut from a saturated anchor (h = 5):
        (a) When camera stops (stl=True, nstl=False) with untextured/mismatched block, or when texture disappears
            (istex=False), or when neighbor coherence fails (nbk=False), %ank is False immediately on Frame 1 (0 frames stick).
        (b) Even if (%nstl & %istex & %rm & %slw & %nbk) holds while %isa0=False, anchor releases after N=2 frames and decays to 0.
        """
        by_t, bx_t = 14, 30
        idx_l2 = by_t * 60 + bx_t
        gy_t, gx_t = by_t * 2, bx_t * 2
        gm = (-18, 0)

        h_sat = np.zeros(2040, dtype=np.uint8)
        h_sat[idx_l2] = 5

        # (a1) Scene cut to untextured sky (istex=False)
        L2p_notex, L2c_notex, V2_notex, S2_notex = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=0, cloud_occlude_cam=True, textured=False, num_coherent_neighbors=4
        )
        gk_notex, h_notex = self.run_fgs8_ll(L2p_notex, L2c_notex, V2_notex, S2_notex, gm, anchor_hist=h_sat)
        self.assertEqual(int(gk_notex[gy_t, gx_t]), 0, "Untextured block (!istex) after scene cut must release immediately")
        self.assertEqual(int(h_notex[idx_l2]), 4)

        # (a2) Scene cut to still camera (gm=(0,0) -> stl=True, nstl=False) with high sb (rm=False)
        L2p_stl, L2c_stl, V2_stl, S2_stl = _make_l2_test_scene(
            by_t, bx_t, gm=(0, 0), vx_cell=0, vy_cell=0, sb_cell=9000, cloud_occlude_cam=True, textured=True, num_coherent_neighbors=4
        )
        gk_stl, _ = self.run_fgs8_ll(L2p_stl, L2c_stl, V2_stl, S2_stl, (0, 0), anchor_hist=h_sat)
        self.assertEqual(int(gk_stl[gy_t, gx_t]), 0, "Still camera (!nstl) + !rm after scene cut must release immediately")

        # (a3) Scene cut to isolated block with 0 coherent neighbors (nbk=False)
        L2p_nonb, L2c_nonb, V2_nonb, S2_nonb = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=20, cloud_occlude_cam=True, textured=True, num_coherent_neighbors=0
        )
        gk_nonb, _ = self.run_fgs8_ll(L2p_nonb, L2c_nonb, V2_nonb, S2_nonb, gm, anchor_hist=h_sat)
        self.assertEqual(int(gk_nonb[gy_t, gx_t]), 0, "Isolated block (!nbk) after scene cut must release immediately")

    def test_04_ll_vs_python_fgs8_bit_exact_parity_and_reverts(self) -> None:
        """Verify 1-to-1 bit-exact parity between compiled fgs8.ll (clang-22) and vcfg_motion.fgs8,
        absence of synth_unit in vcfg_motion.py, and clean revert of A/C/D in mf8.ll, warp8.ll, and vcfg_warp.py.
        """
        vm_src = (ROOT / "tools" / "bench" / "vcfg_motion.py").read_text(encoding="utf-8")
        self.assertTrue("synth_unit" not in vm_src, "synth_unit backdoor must be removed from vcfg_motion.py")
        self.assertTrue("use_hold" not in vm_src, "mf8 hold1 must be reverted from vcfg_motion.py")
        self.assertTrue("v2ok" not in vm_src, "mf8 v2ok must be reverted from vcfg_motion.py")

        mf8_src = MF8_LL.read_text(encoding="utf-8")
        self.assertTrue("%use_hold" not in mf8_src, "mf8.ll hold1 must be reverted to 2d032b1")
        self.assertTrue("%v2ok" not in mf8_src, "mf8.ll v2ok must be reverted to 2d032b1")

        warp8_src = WARP8_LL.read_text(encoding="utf-8")
        self.assertTrue("%w_nbg" not in warp8_src, "warp8.ll w_nbg must be reverted to 2d032b1")
        self.assertTrue("%vm3 = fadd float %vmg_e, -3.000000e+00" not in warp8_src, "warp8.ll w_vel ramp shift must be reverted")
        self.assertTrue("%sok" not in warp8_src, "warp8.ll sok must be reverted")

        by_t, bx_t = 10, 20
        gm = (-18, 0)
        L2p_0, L2c_0, V2_0, S2_0 = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=20, cloud_occlude_cam=False, num_coherent_neighbors=1
        )
        L2p_v9, L2c_v9, V2_v9, S2_v9 = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=3, vy_cell=0, sb_cell=20, cloud_occlude_cam=False, num_coherent_neighbors=1
        )
        L2p_cl, L2c_cl, V2_cl, S2_cl = _make_l2_test_scene(
            by_t, bx_t, gm=gm, vx_cell=0, vy_cell=0, sb_cell=20, cloud_occlude_cam=True, num_coherent_neighbors=1
        )

        h_ll = None
        h_py = None
        for step_idx, (L2p, L2c, V2, S2) in enumerate(
            [
                (L2p_0, L2c_0, V2_0, S2_0),
                (L2p_0, L2c_0, V2_0, S2_0),
                (L2p_v9, L2c_v9, V2_v9, S2_v9),
                (L2p_cl, L2c_cl, V2_cl, S2_cl),
                (L2p_cl, L2c_cl, V2_cl, S2_cl),
                (L2p_cl, L2c_cl, V2_cl, S2_cl),
            ]
        ):
            gk_ll, h_ll = self.run_fgs8_ll(L2p, L2c, V2, S2, gm, anchor_hist=h_ll)
            gk_py, h_py = vm.fgs8(L2p, L2c, V2, S2, gm, anchor_hist=h_py)
            np.testing.assert_array_equal(h_ll, h_py, err_msg=f"Step {step_idx}: anchor_hist mismatch between fgs8.ll and vcfg_motion.py")
            np.testing.assert_array_equal(gk_ll, gk_py, err_msg=f"Step {step_idx}: GK mismatch between fgs8.ll and vcfg_motion.py")

    def test_05_shader_hardware_budget_llc22(self) -> None:
        fgs8_st = _compile_shader_stats(FGS8_LL)
        mf8_st = _compile_shader_stats(MF8_LL)
        warp8_st = _compile_shader_stats(WARP8_LL)

        self.assertLessEqual(fgs8_st["dwords"], 3008, f"fgs8 dwords overflow: {fgs8_st}")
        self.assertLessEqual(fgs8_st["vgprs"], 64, f"fgs8 VGPR overflow: {fgs8_st}")
        self.assertEqual(fgs8_st["scratch"], 0, f"fgs8 scratch non-zero: {fgs8_st}")

        self.assertIn(mf8_st["dwords"], (1381, 1388, 1410, 1443, 1449), f"mf8 dwords unexpected: {mf8_st}")
        self.assertIn(mf8_st["vgprs"], (60, 61), f"mf8 VGPRs unexpected: {mf8_st}")
        self.assertEqual(mf8_st["scratch"], 0, f"mf8 scratch non-zero: {mf8_st}")

        self.assertIn(warp8_st["dwords"], (2840, 2847, 2955), f"warp8 dwords unexpected: {warp8_st}")
        self.assertEqual(warp8_st["vgprs"], 61, f"warp8 VGPRs must match 2d032b1 (61): {warp8_st}")
        self.assertEqual(warp8_st["scratch"], 0, f"warp8 scratch non-zero: {warp8_st}")

    @unittest.skipUnless(SEQ5_DIR.exists() and SEQ4_DIR.exists(), "seq5/seq4 captures not present")
    def test_06_seq5_and_seq4_candidate_independent_head_hole_metric(self) -> None:
        import model_seq_flicker_fixes as msf

        msf_src = (ROOT / "tools" / "bench" / "model_seq_flicker_fixes.py").read_text(encoding="utf-8")
        self.assertTrue("if use_a" not in msf_src, "model_seq_flicker_fixes.py must not contain candidate-dependent if use_a")
        self.assertTrue("if use_c" not in msf_src, "model_seq_flicker_fixes.py must not contain candidate-dependent if use_c")
        self.assertTrue("if use_d" not in msf_src, "model_seq_flicker_fixes.py must not contain candidate-dependent if use_d")
        self.assertTrue(
            hasattr(msf, "evaluate_b_sequence_models"),
            "model_seq_flicker_fixes.py must provide candidate-independent evaluate_b_sequence_models",
        )

        res5 = msf.evaluate_b_sequence_models(SEQ5_DIR)
        res4 = msf.evaluate_b_sequence_models(SEQ4_DIR)

        b5 = res5["baseline_2d032b1"]
        n1_5 = res5["B_hold_N1"]
        n2_5 = res5["B_hold_N2"]
        self.assertEqual(b5["unanc_cells"], 352)
        self.assertEqual(b5["fast_unanc_hole_cells"], 136)
        self.assertLess(n1_5["unanc_cells"], b5["unanc_cells"])
        self.assertLess(n2_5["unanc_cells"], n1_5["unanc_cells"])
        self.assertEqual(n2_5["unanc_cells"], 253)
        self.assertEqual(n2_5["fast_unanc_hole_cells"], 110)
        self.assertLessEqual(n2_5["flips_tight"], b5["flips_tight"])
        self.assertLessEqual(n2_5["flips_crop"], b5["flips_crop"])
        self.assertLessEqual(n2_5["flips_outside_ws"], b5["flips_outside_ws"])

        b4 = res4["baseline_2d032b1"]
        n1_4 = res4["B_hold_N1"]
        n2_4 = res4["B_hold_N2"]
        self.assertEqual(b4["unanc_cells"], 310)
        self.assertEqual(b4["fast_unanc_hole_cells"], 123)
        self.assertLess(n1_4["unanc_cells"], b4["unanc_cells"])
        self.assertLess(n2_4["unanc_cells"], n1_4["unanc_cells"])
        self.assertEqual(n2_4["unanc_cells"], 243)
        self.assertEqual(n2_4["fast_unanc_hole_cells"], 103)
        self.assertLessEqual(n2_4["flips_tight"], b4["flips_tight"])
        self.assertLessEqual(n2_4["flips_crop"], b4["flips_crop"])
        self.assertLessEqual(n2_4["flips_outside_ws"], b4["flips_outside_ws"])


if __name__ == "__main__":
    unittest.main()
