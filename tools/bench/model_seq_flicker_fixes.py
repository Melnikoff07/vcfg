"""Unified hardware-exact evaluator and shader-byte compiler for CUSA08519 sequence head-flicker fixes.

Every variant in VARIANT_SPECS defines in a single place:
  1) Exact LLVM IR patch functions (`patch_fgs8`, `patch_warp8`) for `classic/shaders/fgs8.ll` and `warp8.ll`.
  2) Exact 1-to-1 Python evaluation (`eval_seq`) operating ONLY on buffers that actually exist in GPU memory
     (`F_cur`, 1-step `F_prev` `%vm_p` at advected `(up_bx, up_by)`, `GK`, `V2`, `anchor_hist`, and captured `mfit`).
     Zero multi-frame recursive flow/envelope/weight state arrays are used.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import analyze_seq_fgkill as asf

USED_SHADER_ORDER = (
    "pyr8", "me3", "me2", "me1", "mf8", "gm8", "fgs8", "phase8",
    "warp8", "warp8g", "rcas8", "gv8", "gvc8", "gvz8", "ui8", "osd8", "pcopy8",
)

BIT_SPEC = [
    ("hasfg", asf.FGMASK_HASFG, 0),
    ("isanc", asf.FGMASK_ISANC, 1),
    ("vmg_hi", asf.FGMASK_VMG_HI, 2),
    ("mov_obj", asf.FGMASK_MOV_OBJ, 3),
    ("mfit", asf.FGMASK_MFIT, 4),
    ("fgkill_ws", asf.FGMASK_FGKILL_WS, 5),
    ("fgkill", asf.FGMASK_FGKILL, 6),
    ("sub8", asf.FGMASK_SUB8, 7),
]

COMPUTABLE_FROM_SEQ_BIN = [
    "Full 68x120 grid (8,160 cells): GK_u32 (prio, wt, src), fgmask (8 bits), L2 V2 (34x60x2 int16), L2 anchor_hist (34x60 uint8)",
    "f_head_crop_box (gy=13..40, gx=42..77, 28x36 = 1,008 cells): exact float32 F_head_crop (rx, ry) with sub8 unpacking",
    "f_head_crop_box 2x2 quad coherence (%coh = spr <= max(0.125 * vm4, 2.0)), 4-corner min L1 (%vmn), and slow-branch %vmg = 0.0 vs %vmn",
    "f_head_crop_box 1-step advected previous flow %vm_p = |F_prev(up_bx, up_by)|_1 (warp8.ll:746-774)",
    "f_head_crop_box velocity gate (%mv_act = (%vmg > 2.0)) for %hl_act (warp8.ll:1077-1120; note: %hl_act uses 5-tap @sample_hl on T#prev/T#cur and does not read F_prev)",
    "Outside f_head_crop_box (7,152 cells): exact baseline FlipRate of all 8 fgmask bits, exact hasfg/isanc/fgkill_ws changes from GK & anchor_hist rules, and vmg_hi-paired fgkill changes",
]

NOT_COMPUTABLE_FROM_SEQ_BIN = [
    "Full-resolution luma textures T#prev / T#cur (960x544 / 1920x1080): emov (mfit = emov < 0.25) is read from captured fgmask bit 4 rather than recomputed from pixel luma diffs",
    "Luma contrast gate in %hl_act (@sample_hl 5-tap luma range hl_mov and static/moving luma diff dl_s on full-res T#prev/T#cur; in pack16.ll this is evaluated on GPU at capture time and OR-ed into fgmask bit 6)",
    "Exact fgs8.ll %good, %dev, %istex, %rm, %gdif, %nb2k intermediate flags on L2 blocks where captured anchor_hist == 0 (only %slw = (|V2|^2 <= 2) and captured anchor_hist / GK splats are in the 64 KiB step buffer)",
    "Continuous float32 F(gx, gy) outside f_head_crop_box (7,152 cells): only thresholded vmg_hi = (vmg > 8.0) and sub8 flag are saved in fgmask bits 2 and 7 outside the 28x36 crop box",
]


def unpack_fcrop_flow(fcrop: np.ndarray):
    """Exact GPU bit-level unpacking of F_head_crop (pack16.ll:184-199, warp8.ll:160-175)."""
    rx = fcrop[..., 0]
    ry = fcrop[..., 1]
    bxi = rx.view(np.int32)
    byi = ry.view(np.int32)
    sub8 = (bxi & 15) != 0
    v1x = (bxi & np.int32(-4096)).view(np.float32)
    v1y = (byi & np.int32(-256)).view(np.float32)
    fx = np.where(sub8, v1x, rx).astype(np.float32)
    fy = np.where(sub8, v1y, ry).astype(np.float32)
    vmg = np.abs(fx) + np.abs(fy)
    return fx, fy, vmg, sub8


def compute_quad_coherence_crop(fx: np.ndarray, fy: np.ndarray, vmg: np.ndarray):
    """Compute 2x2 quad coherence (%coh) and 4-corner minimum L1 (%vmn) inside f_head_crop_box (28x36).

    Returns both:
      - `coh_warp8`: exact `warp8.ll:902-926` (`spr = max(sprx, spry)`, `vm4 = max(|mxx|,|mnx|) + max(|mxy|,|mny|)`)
      - `coh_audit`: `audit_44cc9a9.py:83-97` (`spr = sprx + spry`, `vm4 = l00 + l10 + l01 + l11`)
      - `vmn`: exact `warp8.ll:999-1013` (`min(l00, l10, l01, l11)`)
    """
    _, ch, cw = fx.shape
    y0 = np.arange(ch)[:, None]
    x0 = np.arange(cw)[None, :]
    y1 = np.minimum(y0 + 1, ch - 1)
    x1 = np.minimum(x0 + 1, cw - 1)

    fx00, fy00 = fx[:, y0, x0], fy[:, y0, x0]
    fx10, fy10 = fx[:, y0, x1], fy[:, y0, x1]
    fx01, fy01 = fx[:, y1, x0], fy[:, y1, x0]
    fx11, fy11 = fx[:, y1, x1], fy[:, y1, x1]

    mxx = np.maximum(np.maximum(fx00, fx10), np.maximum(fx01, fx11))
    mnx = np.minimum(np.minimum(fx00, fx10), np.minimum(fx01, fx11))
    mxy = np.maximum(np.maximum(fy00, fy10), np.maximum(fy01, fy11))
    mny = np.minimum(np.minimum(fy00, fy10), np.minimum(fy01, fy11))
    sprx = mxx - mnx
    spry = mxy - mny

    spr_warp8 = np.maximum(sprx, spry)
    vm4_warp8 = np.maximum(np.abs(mxx), np.abs(mnx)) + np.maximum(np.abs(mxy), np.abs(mny))
    coh_warp8 = spr_warp8 <= np.maximum(np.float32(0.125) * vm4_warp8, np.float32(2.0))

    l00 = vmg[:, y0, x0]
    l10 = vmg[:, y0, x1]
    l01 = vmg[:, y1, x0]
    l11 = vmg[:, y1, x1]
    vmn = np.minimum(np.minimum(l00, l10), np.minimum(l01, l11))

    spr_audit = sprx + spry
    vm4_audit = l00 + l10 + l01 + l11
    coh_audit = spr_audit <= np.maximum(np.float32(0.125) * vm4_audit, np.float32(2.0))

    return coh_warp8, coh_audit, vmn


# =============================================================================
# Exact LLVM IR Patch Functions for `classic/shaders/fgs8.ll` and `warp8.ll`
# =============================================================================

def patch_noop(src: str) -> str:
    return src


def patch_v1_fgs8(src: str) -> str:
    """V1b/V2/V3/V4 fgs8.ll patch: +1 on %isa0, hold max(h,2) on %isfg (%an2|%isa0 with %slw |V2|^2<=2), -1 decay."""
    old = """  %h1 = add i32 %h, 1
  %h1c = call i32 @llvm.umin.i32(i32 %h1, i32 255)
  %hn = select i1 %isa0, i32 %h1c, i32 0
  %hn8 = trunc i32 %hn to i8
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %hn8, <4 x i32> %vv3, i32 %hoff, i32 0, i32 0)
  %nstl = xor i1 %stl, true
  %g160 = icmp sge i32 %gdif, 160
  %sa0 = and i1 %nstl, %nb2k
  %strong_an = and i1 %sa0, %g160
  %hok0 = icmp uge i32 %hn, 2
  %hok = or i1 %hok0, %strong_an
  %isan = and i1 %isa0, %hok"""
    new = """  %h1 = add i32 %h, 1
  %h1c = call i32 @llvm.umin.i32(i32 %h1, i32 7)
  %hm1 = add i32 %h, -1
  %hdec = call i32 @llvm.smax.i32(i32 %hm1, i32 0)
  %hhld = call i32 @llvm.smax.i32(i32 %h, i32 2)
  %hfg = select i1 %isfg, i32 %hhld, i32 %hdec
  %hn = select i1 %isa0, i32 %h1c, i32 %hfg
  %hn8 = trunc i32 %hn to i8
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %hn8, <4 x i32> %vv3, i32 %hoff, i32 0, i32 0)
  %nstl = xor i1 %stl, true
  %g160 = icmp sge i32 %gdif, 160
  %sa0 = and i1 %nstl, %nb2k
  %strong_an = and i1 %sa0, %g160
  %hok0 = icmp uge i32 %hn, 2
  %hok = or i1 %hok0, %strong_an
  %isan0 = and i1 %an2, %hok0
  %isan1 = and i1 %isa0, %hok
  %isan = or i1 %isan0, %isan1"""
    assert old in src, "Failed to match fgs8.ll target block"
    return src.replace(old, new, 1)


def patch_v5_fgs8(src: str) -> str:
    """V5 fgs8.ll patch: +2 on %isa0 (cap 7), hold max(h,2) on %isfg (%an2|%isa0 with %slw |V2|^2<=2), -1 decay."""
    old = """  %h1 = add i32 %h, 1
  %h1c = call i32 @llvm.umin.i32(i32 %h1, i32 255)
  %hn = select i1 %isa0, i32 %h1c, i32 0
  %hn8 = trunc i32 %hn to i8
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %hn8, <4 x i32> %vv3, i32 %hoff, i32 0, i32 0)
  %nstl = xor i1 %stl, true
  %g160 = icmp sge i32 %gdif, 160
  %sa0 = and i1 %nstl, %nb2k
  %strong_an = and i1 %sa0, %g160
  %hok0 = icmp uge i32 %hn, 2
  %hok = or i1 %hok0, %strong_an
  %isan = and i1 %isa0, %hok"""
    new = """  %h1 = add i32 %h, 2
  %h1c = call i32 @llvm.umin.i32(i32 %h1, i32 7)
  %hm1 = add i32 %h, -1
  %hdec = call i32 @llvm.smax.i32(i32 %hm1, i32 0)
  %hhld = call i32 @llvm.smax.i32(i32 %h, i32 2)
  %hfg = select i1 %isfg, i32 %hhld, i32 %hdec
  %hn = select i1 %isa0, i32 %h1c, i32 %hfg
  %hn8 = trunc i32 %hn to i8
  call void @llvm.amdgcn.raw.buffer.store.i8(i8 %hn8, <4 x i32> %vv3, i32 %hoff, i32 0, i32 0)
  %nstl = xor i1 %stl, true
  %g160 = icmp sge i32 %gdif, 160
  %sa0 = and i1 %nstl, %nb2k
  %strong_an = and i1 %sa0, %g160
  %hok0 = icmp uge i32 %hn, 2
  %hok = or i1 %hok0, %strong_an
  %isan0 = and i1 %an2, %hok0
  %isan1 = and i1 %isa0, %hok
  %isan = or i1 %isan0, %isan1"""
    assert old in src, "Failed to match fgs8.ll target block"
    return src.replace(old, new, 1)


def _normalize_warp8_to_base(src: str) -> str:
    """Normalize warp8.ll to the pre-V5b baseline so all patch_v*_warp8 functions work before and after V5b is applied."""
    v5b_block1 = """  %vmp_k = fmul float %vm_p, 0x3FEE147AE0000000
  %vmg_e = call float @llvm.maxnum.f32(float %vm_c, float %vmp_k)
  %wv0 = fsub float %vmg_e, 4.0
  %wv1 = fmul float %wv0, 0x3FC5555560000000
  %w_vel = call float @sat(float %wv1)
  %w_mov = select i1 %hasfg, float 1.0, float %w_vel
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %mfit_ok = and i1 %mfit, %nofgn
  %w_fgk = select i1 %mfit_ok, float %w_mov, float 0.0
  %ws_keep = fsub float 1.0, %w_fgk
  %ws0_s = fmul float %ws0, %ws_keep
  %ws = call float @llvm.maxnum.f32(float %ws0_s, float %w_hud)"""
    base_block1 = """  %ws0_h = call float @llvm.maxnum.f32(float %ws0, float %w_hud)
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %fgkill_ws0 = and i1 %hasfg, %mfit
  %fgkill_ws = and i1 %fgkill_ws0, %nofgn
  %fgkill0 = and i1 %mov_obj, %mfit
  %fgkill1 = and i1 %fgkill0, %nofgn
  %fgkill = or i1 %fgkill1, %hl_act
  %ws = select i1 %fgkill_ws, float 0.0, float %ws0_h"""
    v5b_block2 = "  %cw3_s = fmul float %cw3_1, %ws_keep\n  %cw3 = select i1 %hl_act, float 0.0, float %cw3_s"
    base_block2 = "  %cw3 = select i1 %fgkill, float 0.0, float %cw3_1"
    if v5b_block1 in src:
        src = src.replace("  %hasfg = icmp ne i32 %gk, 0", "  %hasfg = icmp uge i32 %gk, 1048576", 1)
        src = src.replace(v5b_block1, base_block1, 1)
        src = src.replace(v5b_block2, base_block2, 1)
    return src


def patch_v1_warp8(src: str) -> str:
    """V1a/V1b warp8.ll patch: %hasfg = icmp ne i32 %gk, 0."""
    src = _normalize_warp8_to_base(src)
    old = "  %hasfg = icmp uge i32 %gk, 1048576"
    new = "  %hasfg = icmp ne i32 %gk, 0"
    assert old in src, "Failed to match warp8.ll V1 target"
    return src.replace(old, new, 1)


def patch_v2_warp8(src: str) -> str:
    """V2 warp8.ll patch: 1-step %vm_p hysteresis on %hasfg and %vmg_hi."""
    src = _normalize_warp8_to_base(src)
    src = src.replace(
        "  %hasfg = icmp uge i32 %gk, 1048576",
        "  %hasfg0 = icmp ne i32 %gk, 0\n  %vmp_hi = fcmp ogt float %vm_p, 8.0\n  %vmg_lo0 = fcmp ogt float %vmg, 3.5\n  %h_hold = and i1 %vmp_hi, %vmg_lo0\n  %hasfg = or i1 %hasfg0, %h_hold",
        1,
    )
    old = """  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi"""
    new = """  %vmp_k = fmul float %vm_p, 0x3FEB333340000000
  %vmg_e = call float @llvm.maxnum.f32(float %vmg, float %vmp_k)
  %vmg_eh = fcmp ogt float %vmg_e, 8.0
  %vmg_h0 = and i1 %vmg_lo0, %vmg_eh
  %vmg_h1 = fcmp ogt float %vmg, 8.0
  %vmg_hi = or i1 %vmg_h1, %vmg_h0
  %mov_obj = or i1 %hasfg, %vmg_hi"""
    assert old in src, "Failed to match warp8.ll V2 target"
    return src.replace(old, new, 1)


def patch_v3_warp8(src: str) -> str:
    """V3/V4 warp8.ll patch: 1-step %vm_p envelope (gated by %vmg > 3.0) + soft linear ramp [4, 10]."""
    src = _normalize_warp8_to_base(src)
    src = src.replace("  %hasfg = icmp uge i32 %gk, 1048576", "  %hasfg = icmp ne i32 %gk, 0", 1)
    old1 = """  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %fgkill_ws0 = and i1 %hasfg, %mfit
  %fgkill_ws = and i1 %fgkill_ws0, %nofgn
  %fgkill0 = and i1 %mov_obj, %mfit
  %fgkill1 = and i1 %fgkill0, %nofgn
  %fgkill = or i1 %fgkill1, %hl_act
  %ws = select i1 %fgkill_ws, float 0.0, float %ws0_h"""
    new1 = """  %vmp_k = fmul float %vm_p, 0x3FEE147AE0000000
  %vmg_mx = call float @llvm.maxnum.f32(float %vmg, float %vmp_k)
  %vmg_lo = fcmp ogt float %vmg, 3.0
  %vmg_e = select i1 %vmg_lo, float %vmg_mx, float %vmg
  %wv0 = fsub float %vmg_e, 4.0
  %wv1 = fmul float %wv0, 0x3FC5555560000000
  %w_vel = call float @sat(float %wv1)
  %w_gk = select i1 %hasfg, float 1.0, float 0.0
  %w_mov = call float @llvm.maxnum.f32(float %w_gk, float %w_vel)
  %wv_ws = fmul float %w_vel, 0x3FEB333340000000
  %w_ws0 = call float @llvm.maxnum.f32(float %w_gk, float %wv_ws)
  %vmg_hi = fcmp oge float %w_mov, 0.5
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %mfit_ok = and i1 %mfit, %nofgn
  %w_fgk = select i1 %mfit_ok, float %w_mov, float 0.0
  %w_ws = select i1 %mfit_ok, float %w_ws0, float 0.0
  %ws_keep = fsub float 1.0, %w_ws
  %ws = fmul float %ws0_h, %ws_keep"""
    assert old1 in src, "Failed to match warp8.ll V3 block 1"
    src = src.replace(old1, new1, 1)
    old2 = "  %cw3 = select i1 %fgkill, float 0.0, float %cw3_1"
    new2 = "  %cw_keep = fsub float 1.0, %w_fgk\n  %cw3_s = fmul float %cw3_1, %cw_keep\n  %cw3 = select i1 %hl_act, float 0.0, float %cw3_s"
    assert old2 in src, "Failed to match warp8.ll V3 block 2"
    return src.replace(old2, new2, 1)


def patch_v4b_warp8(src: str) -> str:
    """V4b/V4c warp8.ll patch: use %vmn on slow: branch (`[ %vmn, %slow ]` at line 1076) + ungated max(%vmg, 0.94*%vm_p)
    + HUD-safe static suppression (`ws = max(ws0 * (1 - w_ws), w_hud)`).
    Compiles to 2,922 raw words (12,032 B aligned, +0 B vs baseline, region_end = 0x1BE00 <= 0x1C000, 512 B free margin).
    """
    src = _normalize_warp8_to_base(src)
    old_phi = "  %vmg = phi float [ %vmb, %fastzero ], [ %vmb, %fastside ], [ 0.0, %slow ]"
    new_phi = "  %vmg = phi float [ %vmb, %fastzero ], [ %vmb, %fastside ], [ %vmn, %slow ]"
    assert old_phi in src, "Failed to match warp8.ll:1076 %vmg phi"
    src = src.replace(old_phi, new_phi, 1)
    src = src.replace("  %hasfg = icmp uge i32 %gk, 1048576", "  %hasfg = icmp ne i32 %gk, 0", 1)
    old1 = """  %ws0_h = call float @llvm.maxnum.f32(float %ws0, float %w_hud)
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %fgkill_ws0 = and i1 %hasfg, %mfit
  %fgkill_ws = and i1 %fgkill_ws0, %nofgn
  %fgkill0 = and i1 %mov_obj, %mfit
  %fgkill1 = and i1 %fgkill0, %nofgn
  %fgkill = or i1 %fgkill1, %hl_act
  %ws = select i1 %fgkill_ws, float 0.0, float %ws0_h"""
    new1 = """  %vmp_k = fmul float %vm_p, 0x3FEE147AE0000000
  %vmg_e = call float @llvm.maxnum.f32(float %vmg, float %vmp_k)
  %wv0 = fsub float %vmg_e, 4.0
  %wv1 = fmul float %wv0, 0x3FC5555560000000
  %w_vel = call float @sat(float %wv1)
  %w_mov = select i1 %hasfg, float 1.0, float %w_vel
  %wv_ws = fmul float %w_vel, 0x3FEB333340000000
  %w_ws0 = select i1 %hasfg, float 1.0, float %wv_ws
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %mfit_ok = and i1 %mfit, %nofgn
  %w_fgk = select i1 %mfit_ok, float %w_mov, float 0.0
  %w_ws = select i1 %mfit_ok, float %w_ws0, float 0.0
  %ws_keep = fsub float 1.0, %w_ws
  %ws0_s = fmul float %ws0, %ws_keep
  %ws = call float @llvm.maxnum.f32(float %ws0_s, float %w_hud)"""
    assert old1 in src, "Failed to match warp8.ll V4b block 1"
    src = src.replace(old1, new1, 1)
    old2 = "  %cw3 = select i1 %fgkill, float 0.0, float %cw3_1"
    new2 = "  %cw_keep = fsub float 1.0, %w_fgk\n  %cw3_s = fmul float %cw3_1, %cw_keep\n  %cw3 = select i1 %hl_act, float 0.0, float %cw3_s"
    assert old2 in src, "Failed to match warp8.ll V4b block 2"
    return src.replace(old2, new2, 1)


def patch_v5_warp8(src: str) -> str:
    """V5/V5b warp8.ll patch: use %vm_c (warp8.ll:771) and %vm_p (warp8.ll:774) in vmg_e = max(%vm_c, 0.94*%vm_p),
    with unified w_fgk = w_ws = mfit_ok ? max(w_gk, sat((vmg_e - 4.0)/6.0)) : 0.0 and HUD-safe static suppression
    (`ws = max(ws0 * (1 - w_fgk), w_hud)`).
    Compiles to 2,929 raw words <= 2,944 limit (12,032 B aligned, +0 B vs baseline, region_end = 0x1BE00 <= 0x1C000, 512 B free margin).
    """
    src = _normalize_warp8_to_base(src)
    src = src.replace("  %hasfg = icmp uge i32 %gk, 1048576", "  %hasfg = icmp ne i32 %gk, 0", 1)
    old1 = """  %ws0_h = call float @llvm.maxnum.f32(float %ws0, float %w_hud)
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %fgkill_ws0 = and i1 %hasfg, %mfit
  %fgkill_ws = and i1 %fgkill_ws0, %nofgn
  %fgkill0 = and i1 %mov_obj, %mfit
  %fgkill1 = and i1 %fgkill0, %nofgn
  %fgkill = or i1 %fgkill1, %hl_act
  %ws = select i1 %fgkill_ws, float 0.0, float %ws0_h"""
    new1 = """  %vmp_k = fmul float %vm_p, 0x3FEE147AE0000000
  %vmg_e = call float @llvm.maxnum.f32(float %vm_c, float %vmp_k)
  %wv0 = fsub float %vmg_e, 4.0
  %wv1 = fmul float %wv0, 0x3FC5555560000000
  %w_vel = call float @sat(float %wv1)
  %w_mov = select i1 %hasfg, float 1.0, float %w_vel
  %vmg_hi = fcmp ogt float %vmg, 8.0
  %mov_obj = or i1 %hasfg, %vmg_hi
  %nmov_obj = xor i1 %mov_obj, true
  %mfit = fcmp olt float %emov, 0.25
  %pcnf = getelementptr i8, ptr addrspace(4) %tbl, i64 112
  %cnf = load <4 x i32>, ptr addrspace(4) %pcnf, align 16, !invariant.load !0
  %flgf = extractelement <4 x i32> %cnf, i32 2
  %nofgb = and i32 %flgf, 16
  %nofgk = icmp ne i32 %nofgb, 0
  %nofgn = xor i1 %nofgk, true
  %mfit_ok = and i1 %mfit, %nofgn
  %w_fgk = select i1 %mfit_ok, float %w_mov, float 0.0
  %ws_keep = fsub float 1.0, %w_fgk
  %ws0_s = fmul float %ws0, %ws_keep
  %ws = call float @llvm.maxnum.f32(float %ws0_s, float %w_hud)"""
    assert old1 in src, "Failed to match warp8.ll V5 block 1"
    src = src.replace(old1, new1, 1)
    old2 = "  %cw3 = select i1 %fgkill, float 0.0, float %cw3_1"
    new2 = "  %cw3_s = fmul float %cw3_1, %ws_keep\n  %cw3 = select i1 %hl_act, float 0.0, float %cw3_s"
    assert old2 in src, "Failed to match warp8.ll V5 block 2"
    return src.replace(old2, new2, 1)


# =============================================================================
# 1-to-1 Hardware-Buffer Python Evaluators for Each Variant
# =============================================================================

def _simulate_fgs8_anchor_hist(anc_seq: np.ndarray, slw_l2: np.ndarray, inc_step: int):
    """Simulate fgs8.ll anchor_hist update on L2 grid (34x60) using only hardware V3+2048 buffer and %slw."""
    m_steps = anc_seq.shape[0]
    h_out = np.zeros_like(anc_seq, dtype=np.int32)
    h_out[0] = anc_seq[0].astype(np.int32)
    for s in range(1, m_steps):
        isa0 = anc_seq[s] > 0
        isfg = isa0 | slw_l2[s]
        hp = h_out[s - 1]
        h_inc = np.minimum(hp + inc_step, 7)
        h_hld = np.maximum(hp, 2)
        h_dec = np.maximum(hp - 1, 0)
        h_out[s] = np.where(isa0, h_inc, np.where(isfg, h_hld, h_dec))
    return h_out


def _eval_v1a(ctx: dict) -> dict:
    hasfg = ctx["gk_crop"] != 0
    vmg_hi = ctx["vmg"] > 8.0
    mov_obj = hasfg | vmg_hi
    fgkill_ws = hasfg & ctx["mfit_base"]
    fgkill = mov_obj & ctx["mfit_base"]
    hasfg_full = ctx["gk_seq"] != 0
    return {
        "hasfg": hasfg,
        "mov_obj": mov_obj,
        "fgkill_ws": fgkill_ws,
        "fgkill": fgkill,
        "w_fgk": fgkill.astype(np.float32),
        "w_ws": fgkill_ws.astype(np.float32),
        "hasfg_full": hasfg_full,
        "vel_ramp_ws": False,
    }


def _eval_v1b(ctx: dict) -> dict:
    ever_anc_or_gk = np.maximum.accumulate((ctx["anc_crop"] > 0) | (ctx["gk_crop"] > 0), axis=0)
    hasfg = (ctx["gk_crop"] != 0) | (ever_anc_or_gk & ctx["slw_crop"])
    vmg_hi = ctx["vmg"] > 8.0
    mov_obj = hasfg | vmg_hi
    fgkill_ws = hasfg & ctx["mfit_base"]
    fgkill = mov_obj & ctx["mfit_base"]
    by_f, bx_f = ctx["by_full"], ctx["bx_full"]
    ever_full = np.maximum.accumulate((ctx["anc_seq"][:, by_f, bx_f] > 0) | (ctx["gk_seq"] > 0), axis=0)
    hasfg_full = (ctx["gk_seq"] != 0) | (ever_full & ctx["slw_l2"][:, by_f, bx_f])
    return {
        "hasfg": hasfg,
        "mov_obj": mov_obj,
        "fgkill_ws": fgkill_ws,
        "fgkill": fgkill,
        "w_fgk": fgkill.astype(np.float32),
        "w_ws": fgkill_ws.astype(np.float32),
        "hasfg_full": hasfg_full,
        "vel_ramp_ws": False,
    }


def _eval_v2(ctx: dict) -> dict:
    vmg = ctx["vmg"]
    vm_p = ctx["vm_p"]
    hasfg0 = ctx["gk_crop"] != 0
    vmp_hi = vm_p > 8.0
    vmg_lo0 = vmg > 3.5
    hasfg = hasfg0 | (vmp_hi & vmg_lo0)
    vmg_e = np.maximum(vmg, np.float32(0.85) * vm_p)
    vmg_hi = (vmg > 8.0) | (vmg_lo0 & (vmg_e > 8.0))
    mov_obj = hasfg | vmg_hi
    fgkill_ws = hasfg & ctx["mfit_base"]
    fgkill = mov_obj & ctx["mfit_base"]
    return {
        "hasfg": hasfg,
        "mov_obj": mov_obj,
        "fgkill_ws": fgkill_ws,
        "fgkill": fgkill,
        "w_fgk": fgkill.astype(np.float32),
        "w_ws": fgkill_ws.astype(np.float32),
        "hasfg_full": ctx["gk_seq"] != 0,
        "vel_ramp_ws": False,
    }


def _eval_v3_common(
    ctx: dict,
    vmg_input: np.ndarray,
    require_gt3: bool,
    fgs8_inc_step: int | None = None,
) -> dict:
    vm_p = ctx["vm_p"]
    if fgs8_inc_step is not None:
        h_fgs8 = _simulate_fgs8_anchor_hist(ctx["anc_seq"], ctx["slw_l2"], inc_step=fgs8_inc_step)
        by_c, bx_c = ctx["by_grid"], ctx["bx_grid"]
        hasfg = (ctx["gk_crop"] != 0) | (ctx["slw_crop"] & (h_fgs8[:, by_c, bx_c] >= 2))
        by_f, bx_f = ctx["by_full"], ctx["bx_full"]
        hasfg_full = (ctx["gk_seq"] != 0) | (ctx["slw_l2"][:, by_f, bx_f] & (h_fgs8[:, by_f, bx_f] >= 2))
    else:
        hasfg = ctx["gk_crop"] != 0
        hasfg_full = ctx["gk_seq"] != 0
    vmg_mx = np.maximum(vmg_input, np.float32(0.94) * vm_p)
    vmg_e = np.where(vmg_input > 3.0, vmg_mx, vmg_input) if require_gt3 else vmg_mx
    w_vel = np.clip((vmg_e - np.float32(4.0)) * np.float32(0.16666667), np.float32(0.0), np.float32(1.0))
    w_gk = hasfg.astype(np.float32)
    w_mov = np.maximum(w_gk, w_vel)
    w_ws0 = np.maximum(w_gk, np.float32(0.85) * w_vel)
    w_mfit = ctx["mfit_base"].astype(np.float32)
    w_fgk = w_mov * w_mfit
    w_ws = w_ws0 * w_mfit
    mov_obj = hasfg | (w_mov >= np.float32(0.5))
    fgkill = w_fgk >= np.float32(0.5)
    fgkill_ws = w_ws >= np.float32(0.5)
    return {
        "hasfg": hasfg,
        "mov_obj": mov_obj,
        "fgkill_ws": fgkill_ws,
        "fgkill": fgkill,
        "w_fgk": w_fgk,
        "w_ws": w_ws,
        "hasfg_full": hasfg_full,
        "vel_ramp_ws": True,
    }


def _eval_v3(ctx: dict) -> dict:
    return _eval_v3_common(ctx, ctx["vmg"], require_gt3=True, fgs8_inc_step=None)


def _eval_v4_coh_only_v3(ctx: dict) -> dict:
    return _eval_v3_common(ctx, ctx["vmg_coh0"], require_gt3=True, fgs8_inc_step=None)


def _eval_v4b_coh_vmn_v3(ctx: dict) -> dict:
    return _eval_v3_common(ctx, ctx["vmg_coh_vmn"], require_gt3=False, fgs8_inc_step=1)


def _eval_v4c_coh_vmn_warp8_only(ctx: dict) -> dict:
    return _eval_v3_common(ctx, ctx["vmg_coh_vmn"], require_gt3=False, fgs8_inc_step=None)


def _eval_v5(ctx: dict) -> dict:
    h_v5 = _simulate_fgs8_anchor_hist(ctx["anc_seq"], ctx["slw_l2"], inc_step=2)
    by_c, bx_c = ctx["by_grid"], ctx["bx_grid"]
    hasfg = (ctx["gk_crop"] != 0) | (ctx["slw_crop"] & (h_v5[:, by_c, bx_c] >= 2))
    vmg_e = np.maximum(ctx["vmg"], np.float32(0.94) * ctx["vm_p"])
    w_vel = np.clip((vmg_e - np.float32(4.0)) * np.float32(0.16666667), np.float32(0.0), np.float32(1.0))
    w_gk = hasfg.astype(np.float32)
    w_mov = np.maximum(w_gk, w_vel)
    w_mfit = ctx["mfit_base"].astype(np.float32)
    w_fgk = w_mov * w_mfit
    w_ws = w_fgk
    mov_obj = hasfg | (w_mov >= np.float32(0.5))
    fgkill = w_fgk >= np.float32(0.5)
    fgkill_ws = w_ws >= np.float32(0.5)
    by_f, bx_f = ctx["by_full"], ctx["bx_full"]
    hasfg_full = (ctx["gk_seq"] != 0) | (ctx["slw_l2"][:, by_f, bx_f] & (h_v5[:, by_f, bx_f] >= 2))
    return {
        "hasfg": hasfg,
        "mov_obj": mov_obj,
        "fgkill_ws": fgkill_ws,
        "fgkill": fgkill,
        "w_fgk": w_fgk,
        "w_ws": w_ws,
        "hasfg_full": hasfg_full,
        "vel_ramp_ws": True,
    }


def _eval_v5b(ctx: dict) -> dict:
    hasfg = ctx["gk_crop"] != 0
    vmg_e = np.maximum(ctx["vmg"], np.float32(0.94) * ctx["vm_p"])
    w_vel = np.clip((vmg_e - np.float32(4.0)) * np.float32(0.16666667), np.float32(0.0), np.float32(1.0))
    w_gk = hasfg.astype(np.float32)
    w_mov = np.maximum(w_gk, w_vel)
    w_mfit = ctx["mfit_base"].astype(np.float32)
    w_fgk = w_mov * w_mfit
    w_ws = w_fgk
    mov_obj = hasfg | (w_mov >= np.float32(0.5))
    fgkill = w_fgk >= np.float32(0.5)
    fgkill_ws = w_ws >= np.float32(0.5)
    return {
        "hasfg": hasfg,
        "mov_obj": mov_obj,
        "fgkill_ws": fgkill_ws,
        "fgkill": fgkill,
        "w_fgk": w_fgk,
        "w_ws": w_ws,
        "hasfg_full": ctx["gk_seq"] != 0,
        "vel_ramp_ws": True,
    }


VARIANT_SPECS = {
    "V1a_ir_gk_nonzero_only": {
        "description": "warp8.ll only: %hasfg = (gk != 0) so prio=0 anchors in GK are included in hasfg.",
        "shader_target": "classic/shaders/warp8.ll",
        "formula": "hasfg = (gk != 0); vmg_hi = (vmg > 8.0); mov_obj = hasfg | vmg_hi; fgkill_ws = hasfg & mfit; fgkill = mov_obj & mfit",
        "patch_fgs8": patch_noop,
        "patch_warp8": patch_v1_warp8,
        "eval_seq": _eval_v1a,
    },
    "V1b_ir_gk_nonzero_plus_slw_le2_hold": {
        "description": "fgs8.ll (+1/-1 anchor_hist hold >=2 gated by %slw |V2|^2<=2) + warp8.ll (%hasfg = gk != 0).",
        "shader_target": "classic/shaders/fgs8.ll + classic/shaders/warp8.ll",
        "formula": "fgs8: hn = isa0 ? min(h+1,7) : (isfg ? max(h,2) : max(h-1,0)), isan = (an2 & (hn>=2)) | (isa0 & hok) with slw=(|V2|^2<=2); warp8: hasfg = (gk != 0)",
        "patch_fgs8": patch_v1_fgs8,
        "patch_warp8": patch_v1_warp8,
        "eval_seq": _eval_v1b,
    },
    "V2_ir_1step_F_prev": {
        "description": "1-step hardware F_prev (%vm_p at advected up_bx, up_by) binary hysteresis on %hasfg and %vmg_hi (warp8.ll only).",
        "shader_target": "classic/shaders/warp8.ll",
        "formula": "vm_p = |F_prev(up_bx, up_by)|_1; hasfg = (gk != 0) | ((vm_p > 8.0) & (vmg > 3.5)); vmg_hi = (vmg > 8.0) | ((vmg > 3.5) & (max(vmg, 0.85*vm_p) > 8.0))",
        "patch_fgs8": patch_noop,
        "patch_warp8": patch_v2_warp8,
        "eval_seq": _eval_v2,
    },
    "V3_ir_1step_F_prev": {
        "description": "1-step hardware F_prev (%vm_p) envelope gated by %vmg > 3.0 + soft linear ramp [4, 10] (evaluated on raw cell vmg, warp8.ll only).",
        "shader_target": "classic/shaders/warp8.ll",
        "formula": "hasfg = (gk != 0); vmg_e = (vmg > 3.0) ? max(vmg, 0.94*vm_p) : vmg; w_vel = sat((vmg_e - 4.0)/6.0); w_fgk = max(w_gk, w_vel)*mfit; w_ws = max(w_gk, 0.85*w_vel)*mfit",
        "patch_fgs8": patch_noop,
        "patch_warp8": patch_v3_warp8,
        "eval_seq": _eval_v3,
    },
    "V4_ir_coh_only_v3": {
        "description": "Exact V3 warp8.ll patch evaluated with warp8.ll:1076 slow-branch %vmg = (coh ? vmb : 0.0) (warp8.ll only).",
        "shader_target": "classic/shaders/warp8.ll",
        "formula": "vmg_coh0 = coh ? vmg : 0.0; vmg_e = (vmg_coh0 > 3.0) ? max(vmg_coh0, 0.94*vm_p) : vmg_coh0; w_vel = sat((vmg_e - 4.0)/6.0)",
        "patch_fgs8": patch_noop,
        "patch_warp8": patch_v3_warp8,
        "eval_seq": _eval_v4_coh_only_v3,
    },
    "V4b_ir_coh_vmn_v3": {
        "description": "Coherence-aware warp8.ll:1076 using 4-corner min L1 (%vmn, already at warp8.ll:1013) on slow: branch + ungated max(%vmg, 0.94*%vm_p) + fgs8.ll +1/-1 anchor_hist hold.",
        "shader_target": "classic/shaders/fgs8.ll + classic/shaders/warp8.ll",
        "formula": "fgs8: +1/-1 anchor_hist hold >=2 on slw; warp8: vmg_coh_vmn = coh ? vmg : min(l00,l10,l01,l11); vmg_e = max(vmg_coh_vmn, 0.94*vm_p); w_vel = sat((vmg_e - 4.0)/6.0)",
        "patch_fgs8": patch_v1_fgs8,
        "patch_warp8": patch_v4b_warp8,
        "eval_seq": _eval_v4b_coh_vmn_v3,
    },
    "V4c_ir_coh_vmn_warp8_only": {
        "description": "Pure warp8.ll version of V4b (no fgs8.ll changes): coherence-aware warp8.ll:1076 (%vmn on slow: branch) + ungated max(%vmg, 0.94*%vm_p).",
        "shader_target": "classic/shaders/warp8.ll",
        "formula": "hasfg = (gk != 0); vmg_coh_vmn = coh ? vmg : min(l00,l10,l01,l11); vmg_e = max(vmg_coh_vmn, 0.94*vm_p); w_vel = sat((vmg_e - 4.0)/6.0)",
        "patch_fgs8": patch_noop,
        "patch_warp8": patch_v4b_warp8,
        "eval_seq": _eval_v4c_coh_vmn_warp8_only,
    },
    "V5_ir_vmc_fgs8_slw_ramp": {
        "description": "Entry-block %vm_c (warp8.ll:771) and 1-step %vm_p (warp8.ll:774) in vmg_e = max(%vm_c, 0.94*%vm_p) + unified w_fgk/w_ws ramp + fgs8.ll +2/-1 anchor_hist hold.",
        "shader_target": "classic/shaders/fgs8.ll + classic/shaders/warp8.ll",
        "formula": "fgs8: +2/-1 anchor_hist hold >=2 on slw; warp8: vmg_e = max(vm_c, 0.94*vm_p); w_vel = sat((vmg_e - 4.0)/6.0); w_fgk = w_ws = max(w_gk, w_vel)*mfit",
        "patch_fgs8": patch_v5_fgs8,
        "patch_warp8": patch_v5_warp8,
        "eval_seq": _eval_v5,
    },
    "V5b_ir_vmc_gated_fgs8_slw_ramp": {
        "description": "Pure warp8.ll version of V5 (no fgs8.ll changes): hasfg = (gk != 0), vmg_e = max(vm_c, 0.94*vm_p), unified w_fgk/w_ws ramp.",
        "shader_target": "classic/shaders/warp8.ll",
        "formula": "hasfg = (gk != 0); vmg_e = max(vm_c, 0.94*vm_p); w_vel = sat((vmg_e - 4.0)/6.0); w_fgk = w_ws = max(w_gk, w_vel)*mfit",
        "patch_fgs8": patch_noop,
        "patch_warp8": patch_v5_warp8,
        "eval_seq": _eval_v5b,
    },
}


def compute_region_bit_flip_rates(fgmask_seq: np.ndarray):
    """Compute FlipRate of every fgmask bit inside f_head_crop_box, head_tight, and outside f_head_crop_box."""
    m_steps = fgmask_seq.shape[0]
    n_pairs = max(m_steps - 1, 1)

    in_crop = np.zeros((68, 120), dtype=bool)
    in_crop[asf.FCROP_GY0:asf.FCROP_GY1, asf.FCROP_GX0:asf.FCROP_GX1] = True

    in_tight = np.zeros((68, 120), dtype=bool)
    in_tight[asf.HEAD_TIGHT_GY0:asf.HEAD_TIGHT_GY1, asf.HEAD_TIGHT_GX0:asf.HEAD_TIGHT_GX1] = True

    out_crop = ~in_crop

    regions = {
        "f_head_crop_box": in_crop,
        "head_tight": in_tight,
        "outside_f_head_crop_box": out_crop,
    }
    out = {}
    for rname, rmask in regions.items():
        n_cells = int(np.count_nonzero(rmask))
        denom = n_cells * n_pairs
        rout = {"cells": n_cells, "transitions_total": denom}
        for bname, bmask, bidx in BIT_SPEC:
            bit_arr = ((fgmask_seq[:, rmask] & bmask) != 0)
            flips = int(np.count_nonzero(bit_arr[1:] != bit_arr[:-1])) if m_steps > 1 else 0
            active_mean = float(np.mean(bit_arr)) * float(n_cells)
            rout[bname] = {
                "bit": bidx,
                "flips": flips,
                "flip_rate": float(flips) / float(denom) if denom > 0 else 0.0,
                "mean_active_cells": active_mean,
            }
        out[rname] = rout
    return out


def compute_outside_crop_metrics(
    hasfg_full: np.ndarray,
    mfit_full: np.ndarray,
    vmg_hi_full: np.ndarray,
    out_mask: np.ndarray,
    vel_ramp_ws: bool = False,
):
    """Compute exact outside-f_head_crop_box (7,152 cells) metrics from full-grid buffers."""
    m_steps = hasfg_full.shape[0]
    n_pairs = max(m_steps - 1, 1)
    n_cells = int(np.count_nonzero(out_mask))
    denom = n_cells * n_pairs

    h_out = hasfg_full[:, out_mask]
    mfit_out = mfit_full[:, out_mask]
    vhi_out = vmg_hi_full[:, out_mask]

    ws_gk_only = h_out & mfit_out
    fk_vhi_paired = (h_out | vhi_out) & mfit_out
    ws_eff = fk_vhi_paired if vel_ramp_ws else ws_gk_only

    h_flips = int(np.count_nonzero(h_out[1:] != h_out[:-1])) if m_steps > 1 else 0
    ws_gk_flips = int(np.count_nonzero(ws_gk_only[1:] != ws_gk_only[:-1])) if m_steps > 1 else 0
    ws_eff_flips = int(np.count_nonzero(ws_eff[1:] != ws_eff[:-1])) if m_steps > 1 else 0
    fk_flips = int(np.count_nonzero(fk_vhi_paired[1:] != fk_vhi_paired[:-1])) if m_steps > 1 else 0

    return {
        "cells": n_cells,
        "transitions_total": denom,
        "hasfg_flips": h_flips,
        "hasfg_flip_rate": float(h_flips) / float(denom),
        "fgkill_ws_gk_only_flips": ws_gk_flips,
        "fgkill_ws_gk_only_flip_rate": float(ws_gk_flips) / float(denom),
        "fgkill_ws_flips": ws_eff_flips,
        "fgkill_ws_flip_rate": float(ws_eff_flips) / float(denom),
        "fgkill_vmg_hi_paired_flips": fk_flips,
        "fgkill_vmg_hi_paired_flip_rate": float(fk_flips) / float(denom),
    }


def compute_metrics_for_decision(
    hasfg: np.ndarray,
    mov_obj: np.ndarray,
    fgkill_ws: np.ndarray,
    fgkill: np.ndarray,
    w_fgk: np.ndarray = None,
    w_ws: np.ndarray = None,
    steps_data: list = None,
    outside_crop: dict = None,
):
    """Compute FlipRate metrics on f_head_crop_box (28x36) and head_tight (20x24 subset: cy=1..20, cx=6..29)."""
    m_steps = fgkill.shape[0]
    n_pairs = max(m_steps - 1, 1)
    if w_fgk is None:
        w_fgk = fgkill.astype(np.float32)
    if w_ws is None:
        w_ws = fgkill_ws.astype(np.float32)

    ty0 = asf.HEAD_TIGHT_GY0 - asf.FCROP_GY0  # 1
    ty1 = asf.HEAD_TIGHT_GY1 - asf.FCROP_GY0  # 21
    tx0 = asf.HEAD_TIGHT_GX0 - asf.FCROP_GX0  # 6
    tx1 = asf.HEAD_TIGHT_GX1 - asf.FCROP_GX0  # 30

    def summarize_box(h_arr, m_arr, ws_arr, fk_arr, wf_arr, ww_arr):
        n_cells = int(fk_arr.shape[1] * fk_arr.shape[2])
        denom = n_cells * n_pairs
        h_flips = int(np.count_nonzero(h_arr[1:] != h_arr[:-1])) if m_steps > 1 else 0
        m_flips = int(np.count_nonzero(m_arr[1:] != m_arr[:-1])) if m_steps > 1 else 0
        ws_flips = int(np.count_nonzero(ws_arr[1:] != ws_arr[:-1])) if m_steps > 1 else 0
        fk_diffs = (fk_arr[1:] != fk_arr[:-1]) if m_steps > 1 else np.zeros_like(fk_arr[:0], dtype=bool)
        fk_flips = int(np.count_nonzero(fk_diffs))
        active_union = np.any(fk_arr, axis=0)
        active_union_cells = int(np.count_nonzero(active_union))
        fk_active_union_rate = (
            float(np.mean(fk_diffs[:, active_union])) if (m_steps > 1 and active_union_cells > 0) else 0.0
        )
        dw_fk = np.abs(wf_arr[1:] - wf_arr[:-1]) if m_steps > 1 else np.zeros((1, 1, 1), dtype=np.float32)
        dw_ws = np.abs(ww_arr[1:] - ww_arr[:-1]) if m_steps > 1 else np.zeros((1, 1, 1), dtype=np.float32)
        return {
            "cells": n_cells,
            "transitions_total": denom,
            "hasfg_flips": h_flips,
            "hasfg_flip_rate": float(h_flips) / float(denom),
            "mov_obj_flips": m_flips,
            "mov_obj_flip_rate": float(m_flips) / float(denom),
            "fgkill_ws_flips": ws_flips,
            "fgkill_ws_flip_rate": float(ws_flips) / float(denom),
            "fgkill_flips": fk_flips,
            "fgkill_flip_rate": float(fk_flips) / float(denom),
            "fgkill_active_union_cells": active_union_cells,
            "fgkill_flip_rate_active_union": fk_active_union_rate,
            "mean_abs_dw_fgkill": float(np.mean(dw_fk)),
            "mean_abs_dw_fgkill_ws": float(np.mean(dw_ws)),
            "severe_jump_rate_fgkill_ge_05": float(np.mean(dw_fk >= 0.5)),
        }

    crop_res = summarize_box(hasfg, mov_obj, fgkill_ws, fgkill, w_fgk, w_ws)
    tight_res = summarize_box(
        hasfg[:, ty0:ty1, tx0:tx1],
        mov_obj[:, ty0:ty1, tx0:tx1],
        fgkill_ws[:, ty0:ty1, tx0:tx1],
        fgkill[:, ty0:ty1, tx0:tx1],
        w_fgk[:, ty0:ty1, tx0:tx1],
        w_ws[:, ty0:ty1, tx0:tx1],
    )

    if steps_data is not None and m_steps > 1:
        mc_flips = 0
        mc_total = (ty1 - ty0) * (tx1 - tx0) * n_pairs
        for s in range(1, m_steps):
            dx_c, dy_c = asf.estimate_head_velocity_cells(steps_data[s])
            shift_x = int(np.round(dx_c))
            shift_y = int(np.round(dy_c))
            cy_idx = np.clip(np.arange(ty0, ty1)[:, None] - shift_y, 0, 27)
            cx_idx = np.clip(np.arange(tx0, tx1)[None, :] - shift_x, 0, 35)
            prev_shifted = fgkill[s - 1, cy_idx, cx_idx]
            cur_roi = fgkill[s, ty0:ty1, tx0:tx1]
            mc_flips += int(np.count_nonzero(cur_roi != prev_shifted))
        tight_res["fgkill_mc_flips"] = mc_flips
        tight_res["fgkill_mc_flip_rate"] = float(mc_flips) / float(mc_total)

    res = {
        "f_head_crop_box": crop_res,
        "head_tight": tight_res,
    }
    if outside_crop is not None:
        res["outside_f_head_crop_box"] = outside_crop
    return res


def evaluate_sequence_models(seq_dir: pathlib.Path):
    seq_dir = pathlib.Path(seq_dir)
    bin_files = sorted(seq_dir.glob("bench_seq_[0-9][0-9][0-9][0-9].bin"))
    if not bin_files:
        raise FileNotFoundError(f"No bench_seq_XXXX.bin files in {seq_dir}")

    steps = [asf.parse_seq_step(bf.read_bytes()) for bf in bin_files]
    m_steps = len(steps)

    fgmask_seq = np.stack([s["fgmask"] for s in steps], axis=0)  # (M, 68, 120)
    gk_seq = np.stack([s["GK_u32"] for s in steps], axis=0)  # (M, 68, 120)
    anc_seq = np.stack([s["anchor_hist"] for s in steps], axis=0)  # (M, 34, 60)
    v2_seq = np.stack([s["V2"] for s in steps], axis=0).astype(np.int32)  # (M, 34, 60, 2)
    fcrop_seq = np.stack([s["F_head_crop"] for s in steps], axis=0)  # (M, 28, 36, 2)

    bit_flip_rates = compute_region_bit_flip_rates(fgmask_seq)

    gy0, gy1 = asf.FCROP_GY0, asf.FCROP_GY1
    gx0, gx1 = asf.FCROP_GX0, asf.FCROP_GX1
    ch, cw = gy1 - gy0, gx1 - gx0
    fgmask_crop = fgmask_seq[:, gy0:gy1, gx0:gx1]
    gk_crop = gk_seq[:, gy0:gy1, gx0:gx1]
    prio_crop = (gk_crop >> 20).astype(np.int32)
    wt_crop = ((gk_crop >> 12) & 0xFF).astype(np.int32)

    gy_grid = np.arange(gy0, gy1, dtype=np.int32)[:, None]
    gx_grid = np.arange(gx0, gx1, dtype=np.int32)[None, :]
    by_grid = gy_grid >> 1
    bx_grid = gx_grid >> 1
    by_full = np.arange(68, dtype=np.int32)[:, None] >> 1
    bx_full = np.arange(120, dtype=np.int32)[None, :] >> 1

    anc_crop = anc_seq[:, by_grid, bx_grid]
    v2_crop = v2_seq[:, by_grid, bx_grid]
    vv_crop = v2_crop[..., 0] ** 2 + v2_crop[..., 1] ** 2
    vv_l2 = v2_seq[..., 0] ** 2 + v2_seq[..., 1] ** 2
    slw_l2 = vv_l2 <= 2
    slw_crop = vv_crop <= 2

    fx, fy, vmg, sub8_rec = unpack_fcrop_flow(fcrop_seq)
    coh_warp8, coh_audit, vmn = compute_quad_coherence_crop(fx, fy, vmg)
    vmg_coh0 = np.where(coh_warp8, vmg, np.float32(0.0))
    vmg_coh_vmn = np.where(coh_warp8, vmg, vmn)

    # Reconstruct baseline bits in f_head_crop_box (pack16.ll:165-242)
    hasfg_base = gk_crop >= 0x100000
    isanc_gk_base = (gk_crop > 0) & (gk_crop < 0x100000)
    isanc_h_base = anc_crop >= 2
    isanc_base = isanc_gk_base | isanc_h_base
    vmg_hi_base = vmg > 8.0
    mov_obj_base = hasfg_base | vmg_hi_base
    mfit_base = (fgmask_crop & asf.FGMASK_MFIT) != 0
    fgkill_ws_base = hasfg_base & mfit_base
    fgkill_base = mov_obj_base & mfit_base

    rec_mask = (
        (hasfg_base.astype(np.uint8) << 0)
        | (isanc_base.astype(np.uint8) << 1)
        | (vmg_hi_base.astype(np.uint8) << 2)
        | (mov_obj_base.astype(np.uint8) << 3)
        | (mfit_base.astype(np.uint8) << 4)
        | (fgkill_ws_base.astype(np.uint8) << 5)
        | (fgkill_base.astype(np.uint8) << 6)
        | (sub8_rec.astype(np.uint8) << 7)
    )
    exact_matches = int(np.count_nonzero(rec_mask == fgmask_crop))

    # Full-grid masks for outside-crop evaluation
    in_crop_mask = np.zeros((68, 120), dtype=bool)
    in_crop_mask[gy0:gy1, gx0:gx1] = True
    out_crop_mask = ~in_crop_mask
    mfit_full = (fgmask_seq & asf.FGMASK_MFIT) != 0
    vmg_hi_full = (fgmask_seq & asf.FGMASK_VMG_HI) != 0
    hasfg_full_base = gk_seq >= 0x100000

    # Coherence discrepancy stats (both audit_44cc9a9 formula and exact warp8.ll:916-926 formula)
    incoh_audit = ~coh_audit
    incoh_warp8 = ~coh_warp8
    coherence_discrepancy = {
        "audit_44cc9a9_sum_l1_formula": {
            "incoherent_vmg_hi_cell_steps": int(np.count_nonzero(vmg_hi_base & incoh_audit)),
            "incoherent_vmg_hi_only_mov_obj_cell_steps": int(np.count_nonzero(vmg_hi_base & ~hasfg_base & incoh_audit)),
            "incoherent_fgkill_only_vmg_hi_cell_steps": int(np.count_nonzero(vmg_hi_base & ~hasfg_base & mfit_base & incoh_audit)),
        },
        "warp8_ll_916_926_exact_formula": {
            "incoherent_vmg_hi_cell_steps": int(np.count_nonzero(vmg_hi_base & incoh_warp8)),
            "incoherent_vmg_hi_only_mov_obj_cell_steps": int(np.count_nonzero(vmg_hi_base & ~hasfg_base & incoh_warp8)),
            "incoherent_fgkill_only_vmg_hi_cell_steps": int(np.count_nonzero(vmg_hi_base & ~hasfg_base & mfit_base & incoh_warp8)),
        },
    }

    # Mechanism forensics on f_head_crop_box transitions
    if m_steps > 1:
        fk_flips_mask = fgkill_base[1:] != fgkill_base[:-1]
        ws_flips_mask = fgkill_ws_base[1:] != fgkill_ws_base[:-1]
        mov_flips_mask = mov_obj_base[1:] != mov_obj_base[:-1]
        mfit_flips_mask = mfit_base[1:] != mfit_base[:-1]
        hasfg_flips_mask = hasfg_base[1:] != hasfg_base[:-1]
        vmg_flips_mask = vmg_hi_base[1:] != vmg_hi_base[:-1]

        state_prev = np.where(gk_crop[:-1] == 0, 0, np.where(gk_crop[:-1] < 0x100000, 1, 2))
        state_cur = np.where(gk_crop[1:] == 0, 0, np.where(gk_crop[1:] < 0x100000, 1, 2))
        anc_to_fg = (state_prev == 1) & (state_cur == 2)
        fg_to_anc = (state_prev == 2) & (state_cur == 1)
        anc_fg_toggle = anc_to_fg | fg_to_anc
        anc_to_zero = (state_prev == 1) & (state_cur == 0)
        zero_to_anc = (state_prev == 0) & (state_cur == 1)
        anc_zero_toggle = anc_to_zero | zero_to_anc

        # 1) Midpoint vmg at all 1,441 fgkill flips (includesGK_anc->0 dropouts where vmg jumps ~28 px)
        vmg_at_fk_flip = 0.5 * (vmg[:-1][fk_flips_mask] + vmg[1:][fk_flips_mask])
        vv_min_at_fk_flip = np.minimum(vv_crop[:-1][fk_flips_mask], vv_crop[1:][fk_flips_mask])

        # 2) Endpoint vmg at pure vmg_hi flips (where hasfg == 0 on both steps and mfit == 1)
        pure_vmg_fk_mask = fk_flips_mask & vmg_flips_mask & (~hasfg_base[:-1]) & (~hasfg_base[1:])
        vmg_prev_pure = vmg[:-1][pure_vmg_fk_mask]
        vmg_cur_pure = vmg[1:][pure_vmg_fk_mask]
        vmg_endpoints_pure = np.concatenate([vmg_prev_pure, vmg_cur_pure])
        vmg_closest_dist_to_8 = np.minimum(np.abs(vmg_prev_pure - 8.0), np.abs(vmg_cur_pure - 8.0))

        df_l1 = np.abs(fx[1:] - fx[:-1]) + np.abs(fy[1:] - fy[:-1])
        df_at_anc_to_zero = df_l1[anc_to_zero]

        mechanism_stats = {
            "f_head_crop_fgkill_flips_total": int(np.count_nonzero(fk_flips_mask)),
            "caused_by_mov_obj_only_mfit_1": int(np.count_nonzero(fk_flips_mask & mov_flips_mask & ~mfit_flips_mask)),
            "caused_by_mfit_only_mov_obj_1": int(np.count_nonzero(fk_flips_mask & ~mov_flips_mask & mfit_flips_mask)),
            "caused_by_both_mov_obj_and_mfit": int(np.count_nonzero(fk_flips_mask & mov_flips_mask & mfit_flips_mask)),
            "with_hasfg_flip": int(np.count_nonzero(fk_flips_mask & hasfg_flips_mask)),
            "with_vmg_hi_flip": int(np.count_nonzero(fk_flips_mask & vmg_flips_mask)),
            "gk_anc_prio0_to_fg_transitions": int(np.count_nonzero(anc_fg_toggle)),
            "gk_anc_prio0_to_fg_unidirectional": int(np.count_nonzero(anc_to_fg)),
            "gk_fg_to_anc_prio0_unidirectional": int(np.count_nonzero(fg_to_anc)),
            "gk_anc_prio0_to_fg_fgkill_ws_flips": int(np.count_nonzero(anc_fg_toggle & ws_flips_mask)),
            "gk_anc_prio0_to_fg_fgkill_flips": int(np.count_nonzero(anc_fg_toggle & fk_flips_mask)),
            "gk_anc_prio0_to_zero_unidirectional": int(np.count_nonzero(anc_to_zero)),
            "gk_zero_to_anc_prio0_unidirectional": int(np.count_nonzero(zero_to_anc)),
            "gk_anc_prio0_to_zero_bidirectional_total": int(np.count_nonzero(anc_zero_toggle)),
            "gk_anc_prio0_to_zero_transitions": int(np.count_nonzero(anc_zero_toggle)),
            "df_at_anc_to_zero_mean": float(np.mean(df_at_anc_to_zero)) if df_at_anc_to_zero.size else 0.0,
            "df_at_anc_to_zero_p90": float(np.percentile(df_at_anc_to_zero, 90)) if df_at_anc_to_zero.size else 0.0,
            "df_at_anc_to_zero_gt_20_count": int(np.count_nonzero(df_at_anc_to_zero > 20.0)),
            "fgkill_flips_with_min_vv_le_2": int(np.count_nonzero(vv_min_at_fk_flip <= 2)),
            "vmg_midpoint_at_all_fgkill_flips_median": float(np.median(vmg_at_fk_flip)) if vmg_at_fk_flip.size else 0.0,
            "vmg_midpoint_at_all_fgkill_flips_in_6_to_10_share": (
                float(np.mean((vmg_at_fk_flip >= 6.0) & (vmg_at_fk_flip <= 10.0))) if vmg_at_fk_flip.size else 0.0
            ),
            "vmg_at_fgkill_flip_median": float(np.median(vmg_at_fk_flip)) if vmg_at_fk_flip.size else 0.0,
            "vmg_at_fgkill_flip_in_6_to_10_share": (
                float(np.mean((vmg_at_fk_flip >= 6.0) & (vmg_at_fk_flip <= 10.0))) if vmg_at_fk_flip.size else 0.0
            ),
            "pure_vmg_hi_fgkill_flips_count": int(np.count_nonzero(pure_vmg_fk_mask)),
            "pure_vmg_hi_endpoint_median": float(np.median(vmg_endpoints_pure)) if vmg_endpoints_pure.size else 0.0,
            "pure_vmg_hi_closest_endpoint_in_6_to_10_share": (
                float(np.mean(vmg_closest_dist_to_8 <= 2.0)) if vmg_closest_dist_to_8.size else 0.0
            ),
        }
    else:
        mechanism_stats = {}

    # Top flipping cells inside f_head_crop_box
    top_cells = []
    if m_steps > 1:
        cell_fk_flips = np.sum(fgkill_base[1:] != fgkill_base[:-1], axis=0)
        cell_ws_flips = np.sum(fgkill_ws_base[1:] != fgkill_ws_base[:-1], axis=0)
        cell_hasfg_flips = np.sum(hasfg_base[1:] != hasfg_base[:-1], axis=0)
        flat_order = np.argsort(cell_fk_flips.ravel())[::-1]
        for idx in flat_order[:15]:
            cy, cx = divmod(int(idx), 36)
            flips_cnt = int(cell_fk_flips[cy, cx])
            if flips_cnt == 0:
                break
            gy_c = gy0 + cy
            gx_c = gx0 + cx
            top_cells.append({
                "gy": gy_c,
                "gx": gx_c,
                "by": gy_c >> 1,
                "bx": gx_c >> 1,
                "in_head_tight": bool(
                    asf.HEAD_TIGHT_GY0 <= gy_c < asf.HEAD_TIGHT_GY1
                    and asf.HEAD_TIGHT_GX0 <= gx_c < asf.HEAD_TIGHT_GX1
                ),
                "fgkill_flips": flips_cnt,
                "fgkill_ws_flips": int(cell_ws_flips[cy, cx]),
                "hasfg_flips": int(cell_hasfg_flips[cy, cx]),
                "vv_min": int(np.min(vv_crop[:, cy, cx])),
                "vv_median": float(np.median(vv_crop[:, cy, cx])),
                "vv_max": int(np.max(vv_crop[:, cy, cx])),
                "anchor_hist_min": int(np.min(anc_crop[:, cy, cx])),
                "anchor_hist_max": int(np.max(anc_crop[:, cy, cx])),
                "steps_gk_prio0_anc": int(np.count_nonzero((gk_crop[:, cy, cx] > 0) & (prio_crop[:, cy, cx] == 0))),
                "steps_gk_prio_ge1_fg": int(np.count_nonzero(prio_crop[:, cy, cx] >= 1)),
                "steps_gk_zero": int(np.count_nonzero(gk_crop[:, cy, cx] == 0)),
                "gk_weight_max": int(np.max(wt_crop[:, cy, cx])),
                "vmg_min": float(np.min(vmg[:, cy, cx])),
                "vmg_median": float(np.median(vmg[:, cy, cx])),
                "vmg_max": float(np.max(vmg[:, cy, cx])),
            })

    # Exact 1-step advected previous-frame flow lookup inside f_head_crop_box (warp8.ll:746-774)
    cy_base = np.arange(ch, dtype=np.int32)[:, None]
    cx_base = np.arange(cw, dtype=np.int32)[None, :]
    d_bx = np.trunc(fx * np.float32(0.0625)).astype(np.int32)
    d_by = np.trunc(fy * np.float32(0.0625)).astype(np.int32)
    adv_cy = np.clip(cy_base + d_by, 0, ch - 1)
    adv_cx = np.clip(cx_base + d_bx, 0, cw - 1)

    vm_p = np.zeros_like(vmg)
    vm_p[0] = vmg[0]
    for s in range(1, m_steps):
        vm_p[s] = vmg[s - 1, adv_cy[s], adv_cx[s]]

    ctx = {
        "m_steps": m_steps,
        "gk_seq": gk_seq,
        "gk_crop": gk_crop,
        "anc_seq": anc_seq,
        "anc_crop": anc_crop,
        "slw_l2": slw_l2,
        "slw_crop": slw_crop,
        "by_grid": by_grid,
        "bx_grid": bx_grid,
        "by_full": by_full,
        "bx_full": bx_full,
        "fx": fx,
        "fy": fy,
        "vmg": vmg,
        "vmg_coh0": vmg_coh0,
        "vmg_coh_vmn": vmg_coh_vmn,
        "vm_p": vm_p,
        "mfit_base": mfit_base,
    }

    base_outside = compute_outside_crop_metrics(
        hasfg_full_base, mfit_full, vmg_hi_full, out_crop_mask, vel_ramp_ws=False
    )
    base_metrics = compute_metrics_for_decision(
        hasfg_base, mov_obj_base, fgkill_ws_base, fgkill_base, steps_data=steps, outside_crop=base_outside
    )

    coh0_mov_obj = hasfg_base | (vmg_coh0 > 8.0)
    coh0_fgkill = coh0_mov_obj & mfit_base
    base_coh0_metrics = compute_metrics_for_decision(
        hasfg_base, coh0_mov_obj, fgkill_ws_base, coh0_fgkill, steps_data=steps, outside_crop=base_outside
    )

    variants_out = {
        "baseline": {
            "description": "Captured baseline (pack16.ll raw cell vmg > 8.0, hasfg = gk >= 0x100000).",
            "shader_target": "none",
            "formula": "hasfg = (gk >= 0x100000); vmg_hi = (vmg > 8.0); mov_obj = hasfg | vmg_hi; fgkill_ws = hasfg & mfit; fgkill = mov_obj & mfit",
            **base_metrics,
        },
        "baseline_warp8_coh0": {
            "description": "Production warp8.ll:1076 baseline where incoherent 2x2 quads (!%coh) set %vmg = 0.0.",
            "shader_target": "none",
            "formula": "vmg_coh0 = coh ? vmg : 0.0; hasfg = (gk >= 0x100000); vmg_hi = (vmg_coh0 > 8.0); fgkill = (hasfg | vmg_hi) & mfit",
            **base_coh0_metrics,
        },
    }

    for vkey, spec in VARIANT_SPECS.items():
        ev = spec["eval_seq"](ctx)
        out_metrics = compute_outside_crop_metrics(
            ev["hasfg_full"], mfit_full, vmg_hi_full, out_crop_mask, vel_ramp_ws=ev["vel_ramp_ws"]
        )
        v_metrics = compute_metrics_for_decision(
            ev["hasfg"],
            ev["mov_obj"],
            ev["fgkill_ws"],
            ev["fgkill"],
            w_fgk=ev["w_fgk"],
            w_ws=ev["w_ws"],
            steps_data=steps,
            outside_crop=out_metrics,
        )
        variants_out[vkey] = {
            "description": spec["description"],
            "shader_target": spec["shader_target"],
            "formula": spec["formula"],
            **v_metrics,
        }

    return {
        "seq_dir": str(seq_dir),
        "steps": m_steps,
        "computable_from_seq_bin": COMPUTABLE_FROM_SEQ_BIN,
        "not_computable_from_seq_bin": NOT_COMPUTABLE_FROM_SEQ_BIN,
        "baseline_reconstruction": {
            "f_head_crop_total_cell_steps": int(fgmask_crop.size),
            "f_head_crop_exact_cell_step_matches": exact_matches,
            "f_head_crop_match_rate": float(exact_matches) / float(fgmask_crop.size),
        },
        "pack16_vs_warp8_slow_branch": coherence_discrepancy,
        "region_bit_flip_rates": bit_flip_rates,
        "mechanism_stats": mechanism_stats,
        "top_flipping_cells": top_cells,
        "variants": variants_out,
    }


def compile_variant_shader_bytes(llc_path: str, shaders_dir: pathlib.Path):
    """Compile baseline and all VARIANT_SPECS shaders using the exact same patch_fgs8 / patch_warp8 functions."""
    classic_dir = ROOT / "classic"
    if str(classic_dir) not in sys.path:
        sys.path.insert(0, str(classic_dir))
    import gen_shaders

    shaders_dir = pathlib.Path(shaders_dir)

    def compile_ll(ll_file: pathlib.Path):
        asm = subprocess.run(
            [llc_path, "-mcpu=gfx700", "-O2", "-filetype=asm", str(ll_file), "-o", "-"],
            capture_output=True, text=True, check=True,
        ).stdout
        obj = subprocess.run(
            [llc_path, "-mcpu=gfx700", "-O2", "-filetype=obj", str(ll_file), "-o", "-"],
            capture_output=True, check=True,
        ).stdout
        nv = gen_shaders.stat(asm, "NumVgprs")
        ns = gen_shaders.stat(asm, "TotalNumSgprs", "NumSgprs")
        scratch = gen_shaders.stat(asm, "ScratchSize")
        lds = gen_shaders.stat(asm, "LDSByteSize")
        raw_words = gen_shaders.text_words(obj)
        padded_words = len(raw_words) + ((-len(raw_words)) % 64 + 16)
        raw_bytes = len(raw_words) * 4
        padded_bytes = padded_words * 4
        aligned_region_bytes = (padded_bytes + 255) & ~255
        return {
            "raw_words": len(raw_words),
            "raw_bytes": raw_bytes,
            "padded_words": padded_words,
            "padded_bytes": padded_bytes,
            "aligned_region_bytes": aligned_region_bytes,
            "vgprs": nv,
            "sgprs": ns,
            "lds": lds,
            "scratch": scratch,
        }

    fgs8_base_src = (shaders_dir / "fgs8.ll").read_text(encoding="utf-8")
    warp8_base_src = _normalize_warp8_to_base((shaders_dir / "warp8.ll").read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as td:
        tpath = pathlib.Path(td)
        base_w_ll = tpath / "warp8_baseline.ll"
        base_w_ll.write_text(warp8_base_src, encoding="utf-8")

        base_shaders = {}
        for name in USED_SHADER_ORDER:
            ll = base_w_ll if name == "warp8" else (shaders_dir / f"{name}.ll")
            if ll.exists():
                base_shaders[name] = compile_ll(ll)

        base_total_aligned = sum(v["aligned_region_bytes"] for v in base_shaders.values())
        base_end = 0x8000 + base_total_aligned

        results = {
            "baseline": {
                "fgs8": base_shaders["fgs8"],
                "warp8": base_shaders["warp8"],
                "total_aligned_bytes": base_total_aligned,
                "region_end_hex": f"0x{base_end:X}",
                "region_limit_hex": "0x1C000",
                "free_margin_bytes": 0x1C000 - base_end,
                "fits_in_0x1C000": base_end <= 0x1C000,
            }
        }
        for vkey, spec in VARIANT_SPECS.items():
            fgs8_patched = spec["patch_fgs8"](fgs8_base_src)
            warp8_patched = spec["patch_warp8"](warp8_base_src)

            f_ll = tpath / f"fgs8_{vkey}.ll"
            w_ll = tpath / f"warp8_{vkey}.ll"
            f_ll.write_text(fgs8_patched, encoding="utf-8")
            w_ll.write_text(warp8_patched, encoding="utf-8")

            f_info = compile_ll(f_ll)
            w_info = compile_ll(w_ll)

            tot_aligned = (
                base_total_aligned
                - base_shaders["fgs8"]["aligned_region_bytes"]
                - base_shaders["warp8"]["aligned_region_bytes"]
                + f_info["aligned_region_bytes"]
                + w_info["aligned_region_bytes"]
            )
            end_addr = 0x8000 + tot_aligned
            results[vkey] = {
                "shader_target": spec["shader_target"],
                "fgs8": f_info,
                "warp8": w_info,
                "delta_fgs8_raw_bytes": f_info["raw_bytes"] - base_shaders["fgs8"]["raw_bytes"],
                "delta_fgs8_aligned_bytes": f_info["aligned_region_bytes"] - base_shaders["fgs8"]["aligned_region_bytes"],
                "delta_warp8_raw_bytes": w_info["raw_bytes"] - base_shaders["warp8"]["raw_bytes"],
                "delta_warp8_aligned_bytes": w_info["aligned_region_bytes"] - base_shaders["warp8"]["aligned_region_bytes"],
                "total_aligned_bytes": tot_aligned,
                "delta_total_aligned_bytes": tot_aligned - base_total_aligned,
                "region_end_hex": f"0x{end_addr:X}",
                "region_limit_hex": "0x1C000",
                "free_margin_bytes": 0x1C000 - end_addr,
                "fits_in_0x1C000": end_addr <= 0x1C000,
            }

    return results


def main():
    ap = argparse.ArgumentParser(description="Unified hardware-exact sequence head-flicker evaluator & shader compiler.")
    ap.add_argument("seq_dir", nargs="?", type=pathlib.Path, help="Directory with bench_seq_XXXX.bin files")
    ap.add_argument("-o", "--out", type=pathlib.Path, help="Output JSON path for sequence model evaluation")
    ap.add_argument("--compile-shaders", type=str, help="Path to llc executable to compile all VARIANT_SPECS shaders")
    ap.add_argument(
        "--shaders-dir",
        type=pathlib.Path,
        default=ROOT / "classic/shaders",
        help="Path to classic/shaders directory",
    )
    ap.add_argument("--shaders-out", type=pathlib.Path, help="Output JSON path for compiled shader byte counts")
    args = ap.parse_args()

    if args.compile_shaders:
        sres = compile_variant_shader_bytes(args.compile_shaders, args.shaders_dir)
        if args.shaders_out:
            args.shaders_out.parent.mkdir(parents=True, exist_ok=True)
            args.shaders_out.write_text(json.dumps(sres, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(sres, indent=2))
        if args.seq_dir is None:
            return 0

    if args.seq_dir is None:
        ap.error("Either seq_dir or --compile-shaders must be provided")

    res = evaluate_sequence_models(args.seq_dir)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")

    b_crop = res["variants"]["baseline"]["f_head_crop_box"]
    b_tight = res["variants"]["baseline"]["head_tight"]
    print(f"Steps: {res['steps']}")
    print(
        f"Baseline reconstruction matches: "
        f"{res['baseline_reconstruction']['f_head_crop_exact_cell_step_matches']} / "
        f"{res['baseline_reconstruction']['f_head_crop_total_cell_steps']}"
    )
    print(
        f"Baseline f_head_crop_box: fgkill_flip={b_crop['fgkill_flip_rate']:.6f} ({b_crop['fgkill_flips']}), "
        f"fgkill_ws_flip={b_crop['fgkill_ws_flip_rate']:.6f} ({b_crop['fgkill_ws_flips']})"
    )
    print(
        f"Baseline head_tight:      fgkill_flip={b_tight['fgkill_flip_rate']:.6f} ({b_tight['fgkill_flips']}), "
        f"mc_flip={b_tight.get('fgkill_mc_flip_rate', 0.0):.6f} ({b_tight.get('fgkill_mc_flips', 0)}), "
        f"fgkill_ws_flip={b_tight['fgkill_ws_flip_rate']:.6f} ({b_tight['fgkill_ws_flips']})"
    )
    for k in ("baseline_warp8_coh0", *VARIANT_SPECS.keys()):
        vc = res["variants"][k]["f_head_crop_box"]
        vt = res["variants"][k]["head_tight"]
        vo = res["variants"][k]["outside_f_head_crop_box"]
        print(
            f"{k:36s}: crop_fk={vc['fgkill_flip_rate']:.6f} ({vc['fgkill_flips']:4d}), "
            f"crop_ws={vc['fgkill_ws_flip_rate']:.6f} ({vc['fgkill_ws_flips']:4d}) | "
            f"tight_fk={vt['fgkill_flip_rate']:.6f} ({vt['fgkill_flips']:4d}), "
            f"tight_ws={vt['fgkill_ws_flip_rate']:.6f} ({vt['fgkill_ws_flips']:4d}) | "
            f"out_ws={vo['fgkill_ws_flips']:5d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
