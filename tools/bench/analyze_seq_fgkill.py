"""Analyze M-step lightweight sequence captures (bench_seq_XXXX.bin) for fgkill FlipRate on ROI head_tight."""
import argparse
import json
import pathlib
import struct
import numpy as np

BENCH_SEQ_STEP_BYTES = 0x10000
OFF_V3 = 0x0000
LEN_V3 = 2040
OFF_ANC = 0x0800
LEN_ANC = 2040
OFF_V2 = 0x1000
LEN_V2 = 8160
OFF_MISC = 0x2FE0
LEN_MISC = 32
OFF_GK = 0x3000
LEN_GK = 32640
OFF_FGMASK = 0xAF80
LEN_FGMASK = 8160
OFF_FCROP = 0xCF60
LEN_FCROP = 8064

FCROP_GY0 = 13
FCROP_GY1 = 41  # 28 rows: gy = 13..40
FCROP_GX0 = 42
FCROP_GX1 = 78  # 36 cols: gx = 42..77

# ROI head_tight in 1920x1080 pixels: y = 224..544 (320 px), x = 768..1152 (384 px)
# In 8x8 blocks (120x68): gy8 = 28..67 (40 blocks), gx8 = 96..143 -> wait:
# Wait: 120x68 is in 960x544 L0 pixels (each 8x8 block in L0 is 16x16 in 1920x1080)!
# Let's keep both conventions explicit:
# - In 1920x1080 pixels: y in [224, 544), x in [768, 1152) -> 320 x 384 = 122,880 full-res pixels.
#   Wait: in warp8.ll, thread tid_x in [0..959], tid_y in [0..539] processes a 2x2 quad of 1920x1080 pixels,
#   and sample_f uses fx = tid_x * 0.125 - 0.5 (so each 8x8 block in 960x540 L0 corresponds to 16x16 in 1920x1080!).
#   Therefore, in the 120x68 grid (where each entry is 16x16 full-res pixels = 8x8 L0 pixels):
#   gy = 224 // 16 .. 544 // 16 = 14..34 (20 rows: gy = 14..33)
#   gx = 768 // 16 .. 1152 // 16 = 48..72 (24 cols: gx = 48..71)
HEAD_TIGHT_Y0 = 224
HEAD_TIGHT_Y1 = 544
HEAD_TIGHT_X0 = 768
HEAD_TIGHT_X1 = 1152
HEAD_TIGHT_GY0 = HEAD_TIGHT_Y0 // 16  # 14
HEAD_TIGHT_GY1 = HEAD_TIGHT_Y1 // 16  # 34 (20 rows: 14..33)
HEAD_TIGHT_GX0 = HEAD_TIGHT_X0 // 16  # 48
HEAD_TIGHT_GX1 = HEAD_TIGHT_X1 // 16  # 72 (24 cols: 48..71)

FGMASK_HASFG = 0x01
FGMASK_ISANC = 0x02
FGMASK_VMG_HI = 0x04
FGMASK_MOV_OBJ = 0x08
FGMASK_MFIT = 0x10
FGMASK_FGKILL_WS = 0x20
FGMASK_FGKILL = 0x40
FGMASK_SUB8 = 0x80


def parse_seq_step(raw: bytes):
    if len(raw) < BENCH_SEQ_STEP_BYTES:
        raise ValueError(f"Expected {BENCH_SEQ_STEP_BYTES} bytes, got {len(raw)}")
    v3 = np.frombuffer(raw, dtype="<i2", count=17 * 30 * 2, offset=OFF_V3).reshape(17, 30, 2)
    anc = np.frombuffer(raw, dtype=np.uint8, count=34 * 60, offset=OFF_ANC).reshape(34, 60)
    v2 = np.frombuffer(raw, dtype="<i2", count=34 * 60 * 2, offset=OFF_V2).reshape(34, 60, 2)
    misc = struct.unpack_from("<8I", raw, OFF_MISC)
    gk_u32 = np.frombuffer(raw, dtype="<u4", count=68 * 120, offset=OFF_GK).reshape(68, 120)
    gk_prio = (gk_u32 >> 20).astype(np.uint16)
    gk_weight = ((gk_u32 >> 12) & 0xFF).astype(np.uint8)
    gk_src_l2 = (gk_u32 & 0xFFF).astype(np.uint16)
    fgmask = np.frombuffer(raw, dtype=np.uint8, count=68 * 120, offset=OFF_FGMASK).reshape(68, 120)
    fcrop = np.frombuffer(raw, dtype="<f4", count=28 * 36 * 2, offset=OFF_FCROP).reshape(28, 36, 2)
    return {
        "V3": v3,
        "anchor_hist": anc,
        "V2": v2,
        "misc": {
            "scene_sum": int(misc[0]),
            "hud_cnt": int(misc[1]),
            "l0_sad_sum": int(misc[2]),
            "sh_cnt": int(misc[3]),
            "fg_vx_sum": struct.unpack("<i", struct.pack("<I", misc[4]))[0],
            "fg_vy_sum": struct.unpack("<i", struct.pack("<I", misc[5]))[0],
            "fg_cnt": int(misc[6]),
        },
        "GK_u32": gk_u32,
        "GK_prio": gk_prio,
        "GK_weight": gk_weight,
        "GK_src_l2": gk_src_l2,
        "fgmask": fgmask,
        "F_head_crop": fcrop,
    }


def reconstruct_head_tight_pixels(step_data):
    """Reconstruct exact pixel-level fgkill and flow on ROI head_tight (320x384 full-res pixels, 160x192 warp8 threads)."""
    fgmask = step_data["fgmask"]
    fcrop = step_data["F_head_crop"]

    # In warp8.ll, each thread (tx in 0..959, ty in 0..539) covers a 2x2 full-res pixel quad:
    # full-res y in [224..544) -> ty in [112..272) (160 threads)
    # full-res x in [768..1152) -> tx in [384..576) (192 threads)
    ty = np.arange(HEAD_TIGHT_Y0 // 2, HEAD_TIGHT_Y1 // 2, dtype=np.float32)[:, None]  # (160, 1)
    tx = np.arange(HEAD_TIGHT_X0 // 2, HEAD_TIGHT_X1 // 2, dtype=np.float32)[None, :]  # (1, 192)

    fx = np.clip((tx + 0.5) * np.float32(0.125) - np.float32(0.5), np.float32(0.0), np.float32(119.0))
    fy = np.clip((ty + 0.5) * np.float32(0.125) - np.float32(0.5), np.float32(0.0), np.float32(67.0))
    ix0 = fx.astype(np.int32)  # in 47..71
    iy0 = fy.astype(np.int32)  # in 13..33

    mask_at_thread = fgmask[iy0, ix0]
    fgkill_thread = (mask_at_thread & FGMASK_FGKILL) != 0
    hasfg_thread = (mask_at_thread & FGMASK_HASFG) != 0
    sub8_thread = (mask_at_thread & FGMASK_SUB8) != 0

    # Expand each 2x2 quad to full 1920x1080 pixel resolution (320x384)
    fgkill_px = np.repeat(np.repeat(fgkill_thread, 2, axis=0), 2, axis=1)
    hasfg_px = np.repeat(np.repeat(hasfg_thread, 2, axis=0), 2, axis=1)
    sub8_px = np.repeat(np.repeat(sub8_thread, 2, axis=0), 2, axis=1)

    # Also check how many threads have non-zero difference between raw 8x8/16x16 flow and background-clamped flow
    c_iy0 = np.clip(iy0 - FCROP_GY0, 0, 27)
    c_ix0 = np.clip(ix0 - FCROP_GX0, 0, 35)
    f00 = fcrop[c_iy0, c_ix0]  # (160, 192, 2)
    return {
        "fgkill_px": fgkill_px,
        "hasfg_px": hasfg_px,
        "sub8_px": sub8_px,
        "fgkill_thread": fgkill_thread,
        "f00_thread": f00,
    }


def estimate_head_velocity_cells(step_data):
    """Estimate head motion (dx_cells, dy_cells) in 120x68 grid units (1 cell = 16x16 full-res px)."""
    fgmask = step_data["fgmask"][HEAD_TIGHT_GY0:HEAD_TIGHT_GY1, HEAD_TIGHT_GX0:HEAD_TIGHT_GX1]
    c_y0 = HEAD_TIGHT_GY0 - FCROP_GY0
    c_y1 = HEAD_TIGHT_GY1 - FCROP_GY0
    c_x0 = HEAD_TIGHT_GX0 - FCROP_GX0
    c_x1 = HEAD_TIGHT_GX1 - FCROP_GX0
    fcrop_roi = step_data["F_head_crop"][c_y0:c_y1, c_x0:c_x1]
    vx = fcrop_roi[..., 0]
    vy = fcrop_roi[..., 1]
    mov = (fgmask & FGMASK_MOV_OBJ) != 0
    if int(np.count_nonzero(mov)) >= 4:
        med_vx = float(np.median(vx[mov]))
        med_vy = float(np.median(vy[mov]))
    else:
        misc = step_data["misc"]
        if misc["fg_cnt"] > 0:
            med_vx = float(misc["fg_vx_sum"]) / float(misc["fg_cnt"])
            med_vy = float(misc["fg_vy_sum"]) / float(misc["fg_cnt"])
        else:
            med_vx, med_vy = 0.0, 0.0
    # F_head_crop is in 1920x1080 pixels, where 1 grid cell is 16 pixels.
    return med_vx / 16.0, med_vy / 16.0


def analyze_sequence(seq_dir: pathlib.Path):
    seq_dir = pathlib.Path(seq_dir)
    bin_files = sorted(seq_dir.glob("bench_seq_[0-9][0-9][0-9][0-9].bin"))
    if not bin_files:
        raise FileNotFoundError(f"No bench_seq_XXXX.bin files found in {seq_dir}")

    steps = []
    meta_list = []
    for bf in bin_files:
        idx = int(bf.stem.split("_")[-1])
        jf = seq_dir / f"bench_seq_{idx:04d}.json"
        meta = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {"step": idx}
        parsed = parse_seq_step(bf.read_bytes())
        steps.append(parsed)
        meta_list.append(meta)

    m_steps = len(steps)
    dt_us_list = []
    timing_ok_list = []
    mean_abs_v2_list = []
    for s in range(m_steps):
        meta = meta_list[s]
        if "dt_us" in meta:
            dt_us = int(meta["dt_us"])
        elif isinstance(meta.get("submit_us"), list) and len(meta["submit_us"]) >= 2:
            dt_us = int(meta["submit_us"][1]) - int(meta["submit_us"][0])
        else:
            dt_us = 33366
        dt_us_list.append(dt_us)

        if "timing_ok" in meta:
            tok = bool(meta["timing_ok"])
        else:
            tok = 28000 <= dt_us <= 38333
            if tok and s > 0 and timing_ok_list[s - 1]:
                d1 = dt_us_list[s - 1]
                if abs(d1 - dt_us) > 5000 or (d1 + dt_us) < 54000 or (d1 + dt_us) > 95000:
                    tok = False
        timing_ok_list.append(tok)

        v2_s = steps[s]["V2"].astype(np.float32)
        mean_abs_v2_list.append(float(np.mean(np.abs(v2_s))))

    timing_ok_arr = np.array(timing_ok_list, dtype=bool)
    timing_ok_steps_count = int(np.sum(timing_ok_arr))
    nonuniform_steps_count = int(m_steps - timing_ok_steps_count)
    mean_abs_v2_arr = np.array(mean_abs_v2_list, dtype=np.float32)
    low_motion_steps_count = int(np.sum(mean_abs_v2_arr < 2.0))
    low_motion_warning = bool(low_motion_steps_count > (m_steps / 2.0))

    # Extract 20x24 ROI head_tight on the 120x68 grid (gy = 14..33, gx = 48..71)
    fgmask_roi = np.stack(
        [s["fgmask"][HEAD_TIGHT_GY0:HEAD_TIGHT_GY1, HEAD_TIGHT_GX0:HEAD_TIGHT_GX1] for s in steps],
        axis=0,
    )  # (M, 20, 24)

    fgkill_cells = (fgmask_roi & FGMASK_FGKILL) != 0
    fgkill_ws_cells = (fgmask_roi & FGMASK_FGKILL_WS) != 0
    hasfg_cells = (fgmask_roi & FGMASK_HASFG) != 0
    isanc_cells = (fgmask_roi & FGMASK_ISANC) != 0
    mfit_cells = (fgmask_roi & FGMASK_MFIT) != 0

    # Pixel-level reconstruction on ROI head_tight (320x384 pixels)
    px_list = [reconstruct_head_tight_pixels(s) for s in steps]
    fgkill_px = np.stack([p["fgkill_px"] for p in px_list], axis=0)  # (M, 320, 384)
    hasfg_px = np.stack([p["hasfg_px"] for p in px_list], axis=0)

    if m_steps > 1:
        cell_flips = fgkill_cells[1:] != fgkill_cells[:-1]
        ws_flips = fgkill_ws_cells[1:] != fgkill_ws_cells[:-1]
        hasfg_flips = hasfg_cells[1:] != hasfg_cells[:-1]
        isanc_flips = isanc_cells[1:] != isanc_cells[:-1]
        px_flips = fgkill_px[1:] != fgkill_px[:-1]

        flip_rate_cell_fixed = float(np.mean(cell_flips))
        flip_rate_ws_fixed = float(np.mean(ws_flips))
        flip_rate_hasfg_fixed = float(np.mean(hasfg_flips))
        flip_rate_isanc_fixed = float(np.mean(isanc_flips))
        flip_rate_px_fixed = float(np.mean(px_flips))

        # Active union mask (cells/pixels where fgkill is active at least once across the sequence)
        active_cells = np.any(fgkill_cells, axis=0)
        active_px = np.any(fgkill_px, axis=0)
        flip_rate_cell_active_union = (
            float(np.mean(cell_flips[:, active_cells])) if np.any(active_cells) else 0.0
        )
        flip_rate_px_active_union = (
            float(np.mean(px_flips[:, active_px])) if np.any(active_px) else 0.0
        )

        pair_timing_ok = timing_ok_arr[:-1] & timing_ok_arr[1:]
        uniform_pairs_count = int(np.sum(pair_timing_ok))

        # Motion-compensated cell FlipRate along estimated V_head
        mc_flips = []
        per_pair = []
        for s in range(1, m_steps):
            dx_c, dy_c = estimate_head_velocity_cells(steps[s])
            shift_x = int(np.round(dx_c))
            shift_y = int(np.round(dy_c))
            cur_full = (steps[s]["fgmask"] & FGMASK_FGKILL) != 0
            prev_full = (steps[s - 1]["fgmask"] & FGMASK_FGKILL) != 0
            gy_idx = np.clip(
                np.arange(HEAD_TIGHT_GY0, HEAD_TIGHT_GY1)[:, None] - shift_y, 0, 67
            )
            gx_idx = np.clip(
                np.arange(HEAD_TIGHT_GX0, HEAD_TIGHT_GX1)[None, :] - shift_x, 0, 119
            )
            prev_shifted = prev_full[gy_idx, gx_idx]
            cur_roi = cur_full[HEAD_TIGHT_GY0:HEAD_TIGHT_GY1, HEAD_TIGHT_GX0:HEAD_TIGHT_GX1]
            mc_diff = cur_roi != prev_shifted
            mc_flips.append(mc_diff)
            per_pair.append({
                "step_prev": s - 1,
                "step_cur": s,
                "pair_timing_ok": bool(pair_timing_ok[s - 1]),
                "fgkill_cell_flip_rate": float(np.mean(cell_flips[s - 1])),
                "fgkill_cell_flip_count": int(np.sum(cell_flips[s - 1])),
                "fgkill_px_flip_rate": float(np.mean(px_flips[s - 1])),
                "fgkill_px_flip_count": int(np.sum(px_flips[s - 1])),
                "fgkill_mc_cell_flip_rate": float(np.mean(mc_diff)),
                "shift_cells": [shift_x, shift_y],
            })
        flip_rate_cell_mc = float(np.mean(mc_flips))
        if uniform_pairs_count > 0:
            flip_rate_cell_fixed_uniform = float(np.mean(cell_flips[pair_timing_ok]))
            flip_rate_ws_fixed_uniform = float(np.mean(ws_flips[pair_timing_ok]))
            flip_rate_px_fixed_uniform = float(np.mean(px_flips[pair_timing_ok]))
            flip_rate_cell_mc_uniform = float(np.mean(np.asarray(mc_flips)[pair_timing_ok]))
        else:
            flip_rate_cell_fixed_uniform = 0.0
            flip_rate_ws_fixed_uniform = 0.0
            flip_rate_px_fixed_uniform = 0.0
            flip_rate_cell_mc_uniform = 0.0
    else:
        flip_rate_cell_fixed = 0.0
        flip_rate_ws_fixed = 0.0
        flip_rate_hasfg_fixed = 0.0
        flip_rate_isanc_fixed = 0.0
        flip_rate_px_fixed = 0.0
        flip_rate_cell_active_union = 0.0
        flip_rate_px_active_union = 0.0
        flip_rate_cell_mc = 0.0
        uniform_pairs_count = 0
        flip_rate_cell_fixed_uniform = 0.0
        flip_rate_ws_fixed_uniform = 0.0
        flip_rate_px_fixed_uniform = 0.0
        flip_rate_cell_mc_uniform = 0.0
        per_pair = []

    per_step = []
    for s in range(m_steps):
        per_step.append({
            "step": s,
            "flip": meta_list[s].get("flip", s),
            "dt_us": dt_us_list[s],
            "timing_ok": timing_ok_list[s],
            "mean_abs_v2": mean_abs_v2_list[s],
            "fgkill_cells": int(np.sum(fgkill_cells[s])),
            "fgkill_ws_cells": int(np.sum(fgkill_ws_cells[s])),
            "hasfg_cells": int(np.sum(hasfg_cells[s])),
            "isanc_cells": int(np.sum(isanc_cells[s])),
            "mfit_cells": int(np.sum(mfit_cells[s])),
            "fgkill_pixels": int(np.sum(fgkill_px[s])),
            "hasfg_pixels": int(np.sum(hasfg_px[s])),
        })

    return {
        "seq_dir": str(seq_dir),
        "steps": m_steps,
        "roi_head_tight": {
            "pixel_box": [HEAD_TIGHT_Y0, HEAD_TIGHT_Y1, HEAD_TIGHT_X0, HEAD_TIGHT_X1],
            "pixel_count": (HEAD_TIGHT_Y1 - HEAD_TIGHT_Y0) * (HEAD_TIGHT_X1 - HEAD_TIGHT_X0),
            "cell_box_120x68": [HEAD_TIGHT_GY0, HEAD_TIGHT_GY1, HEAD_TIGHT_GX0, HEAD_TIGHT_GX1],
            "cell_count": (HEAD_TIGHT_GY1 - HEAD_TIGHT_GY0) * (HEAD_TIGHT_GX1 - HEAD_TIGHT_GX0),
        },
        "summary": {
            "fgkill_flip_rate_cell_fixed": flip_rate_cell_fixed,
            "fgkill_flip_rate_cell_motion_comp": flip_rate_cell_mc,
            "fgkill_flip_rate_cell_active_union": flip_rate_cell_active_union,
            "fgkill_flip_rate_pixel_fixed": flip_rate_px_fixed,
            "fgkill_flip_rate_pixel_active_union": flip_rate_px_active_union,
            "fgkill_ws_flip_rate_cell_fixed": flip_rate_ws_fixed,
            "hasfg_flip_rate_cell_fixed": flip_rate_hasfg_fixed,
            "isanc_flip_rate_cell_fixed": flip_rate_isanc_fixed,
            "timing_ok_steps_count": timing_ok_steps_count,
            "nonuniform_steps_count": nonuniform_steps_count,
            "uniform_pairs_count": uniform_pairs_count,
            "fgkill_flip_rate_cell_fixed_uniform_only": flip_rate_cell_fixed_uniform,
            "fgkill_flip_rate_cell_motion_comp_uniform_only": flip_rate_cell_mc_uniform,
            "fgkill_ws_flip_rate_cell_fixed_uniform_only": flip_rate_ws_fixed_uniform,
            "fgkill_flip_rate_pixel_fixed_uniform_only": flip_rate_px_fixed_uniform,
            "mean_abs_v2_overall": float(np.mean(mean_abs_v2_arr)) if m_steps > 0 else 0.0,
            "mean_abs_v2_min": float(np.min(mean_abs_v2_arr)) if m_steps > 0 else 0.0,
            "mean_abs_v2_max": float(np.max(mean_abs_v2_arr)) if m_steps > 0 else 0.0,
            "low_motion_steps_count": low_motion_steps_count,
            "low_motion_warning": low_motion_warning,
            "mean_fgkill_cells_per_step": float(np.mean(np.sum(fgkill_cells, axis=(1, 2)))),
            "mean_fgkill_pixels_per_step": float(np.mean(np.sum(fgkill_px, axis=(1, 2)))),
        },
        "per_step": per_step,
        "per_pair": per_pair,
    }


def eval_pack16_ir_cell(
    ll_path: pathlib.Path,
    x: int,
    y: int,
    f_field_120x68: np.ndarray,
    gk_120x68: np.ndarray = None,
    anc_34x60: np.ndarray = None,
    img_prev_fn=None,
    img_cur_fn=None,
    flags: int = 0,
):
    """Execute classic/bench/shaders/pack16.ll SSA IR directly on a cell (x, y) in Mode B (dims.y == 68)."""
    import re

    src = pathlib.Path(ll_path).read_text(encoding="utf-8")

    # Parse defined functions
    funcs = {}
    fn_pat = re.compile(
        r"define\s+(?:internal\s+|amdgpu_cs\s+)?(.+?)\s+@([A-Za-z0-9_]+)\((.*?)\)\s*(?:#[0-9]+\s*)?(?:alwaysinline\s*)?\{(.*?)\n\}",
        re.S,
    )
    for m in fn_pat.finditer(src):
        ret_t, fname, arg_s, body = m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4)
        arg_names = []
        if arg_s:
            for part in arg_s.split(","):
                toks = part.strip().split()
                if toks:
                    arg_names.append(toks[-1])
        blocks = {}
        cur_b = "entry"
        blocks[cur_b] = []
        for raw_line in body.splitlines():
            line = raw_line.split(";")[0].strip()
            if not line:
                continue
            if line.endswith(":") and " " not in line:
                cur_b = line[:-1]
                blocks.setdefault(cur_b, [])
            else:
                blocks[cur_b].append(line)
        funcs[fname] = (arg_names, blocks)

    # Prepare work buffer vw (starting at W8_V3: +0 V3, +2048 anc, +4096 V2, +16384 FA, +143360 MISC, +155648 GK)
    vw = bytearray(256 * 1024)
    foff_base = 16384
    if anc_34x60 is not None:
        vw[2048:2048 + 2040] = np.asarray(anc_34x60, dtype=np.uint8).tobytes()
    if gk_120x68 is not None:
        vw[155648:155648 + 68 * 120 * 4] = np.asarray(gk_120x68, dtype="<u4").tobytes()
    f_bytes = np.asarray(f_field_120x68, dtype="<f4").tobytes()
    vw[foff_base:foff_base + len(f_bytes)] = f_bytes

    vo = bytearray(BENCH_SEQ_STEP_BYTES)

    if img_prev_fn is None:
        img_prev_fn = lambda u, v: np.array([0.25, 0.25, 0.25, 1.0], dtype=np.float32)
    if img_cur_fn is None:
        img_cur_fn = lambda u, v: np.array([0.25, 0.25, 0.25, 1.0], dtype=np.float32)

    def parse_lit(tok: str):
        tok = tok.strip()
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok in ("undef", "zeroinitializer"):
            return np.zeros(4, dtype=np.float32)
        if tok.startswith("0x") or tok.startswith("-0x"):
            bits64 = int(tok, 16) & 0xFFFFFFFFFFFFFFFF
            return np.float32(struct.unpack("<d", struct.pack("<Q", bits64))[0])
        if "." in tok or "e" in tok or "E" in tok:
            return np.float32(float(tok))
        return int(tok)

    def eval_val(tok: str, env: dict):
        tok = tok.strip()
        if tok.startswith("%"):
            return env[tok]
        return parse_lit(tok)

    def split_top_level(s: str):
        out, cur, depth = [], [], 0
        for ch in s:
            if ch in "<({":
                depth += 1
            elif ch in ">)}":
                depth -= 1
            if ch == "," and depth == 0:
                out.append("".join(cur).strip())
                cur = []
            else:
                cur.append(ch)
        if cur:
            out.append("".join(cur).strip())
        return out

    def strip_type_prefix(arg: str):
        arg = arg.strip()
        for pfx in (
            "ptr addrspace(4)", "<8 x i32>", "<4 x i32>", "<2 x i32>",
            "<4 x float>", "<2 x float>", "{ float, float, i1 }",
            "i32 immarg", "i1 immarg", "float", "i64", "i32", "i16", "i8", "i1",
        ):
            if arg.startswith(pfx + " "):
                return arg[len(pfx):].strip()
        return arg.split()[-1]

    def run_fn(fname: str, args_vals: list):
        arg_names, blocks = funcs[fname]
        env = dict(zip(arg_names, args_vals))
        bname = "entry"
        prev_b = None
        while True:
            for inst in blocks[bname]:
                if inst.startswith("ret "):
                    rest = inst[4:].strip()
                    if rest == "void":
                        return None
                    vtok = strip_type_prefix(rest)
                    return eval_val(vtok, env)
                if inst.startswith("br "):
                    rest = inst[3:].strip()
                    if rest.startswith("label "):
                        prev_b, bname = bname, rest.split("%")[1].strip()
                    else:
                        parts = split_top_level(rest)
                        cond = bool(eval_val(strip_type_prefix(parts[0]), env))
                        t_b = parts[1].split("%")[1].strip()
                        f_b = parts[2].split("%")[1].strip()
                        prev_b, bname = bname, (t_b if cond else f_b)
                    break
                if inst.startswith("call void "):
                    m_c = re.match(r"call void @([A-Za-z0-9_\.]+)\((.*)\)$", inst)
                    cname, cargs_s = m_c.group(1), m_c.group(2)
                    cargs = [eval_val(strip_type_prefix(a), env) for a in split_top_level(cargs_s)]
                    if cname == "llvm.amdgcn.raw.buffer.store.i32":
                        val, buf, off = int(cargs[0]) & 0xFFFFFFFF, cargs[1], int(cargs[2])
                        struct.pack_into("<I", buf, off, val)
                    elif cname == "llvm.amdgcn.raw.buffer.store.v2f32":
                        val, buf, off = cargs[0], cargs[1], int(cargs[2])
                        struct.pack_into("<2f", buf, off, float(val[0]), float(val[1]))
                    elif cname == "llvm.amdgcn.raw.buffer.store.i8":
                        val, buf, off = int(cargs[0]) & 0xFF, cargs[1], int(cargs[2])
                        buf[off] = val
                    continue

                lhs, rhs = [p.strip() for p in inst.split("=", 1)]
                if rhs.startswith("load "):
                    if "%tbl" in rhs:
                        env[lhs] = "tc"
                    else:
                        ptr_name = rhs.split(",")[1].strip().split()[-1]
                        env[lhs] = env[ptr_name]
                elif rhs.startswith("getelementptr "):
                    off = int(rhs.split(",")[-1].strip().split()[-1])
                    mapping = {
                        32: vo,
                        48: [120, 68, flags, foff_base],
                        64: "tcur",
                        96: vw,
                        112: [2, 0, 5, 0],
                    }
                    env[lhs] = mapping.get(off, None)
                elif rhs.startswith("extractelement "):
                    parts = split_top_level(rhs[len("extractelement "):])
                    vec = eval_val(strip_type_prefix(parts[0]), env)
                    idx = int(eval_val(strip_type_prefix(parts[1]), env))
                    env[lhs] = vec[idx]
                elif rhs.startswith("insertelement "):
                    parts = split_top_level(rhs[len("insertelement "):])
                    vec = np.array(eval_val(strip_type_prefix(parts[0]), env), dtype=np.float32).copy()
                    val = np.float32(eval_val(strip_type_prefix(parts[1]), env))
                    idx = int(eval_val(strip_type_prefix(parts[2]), env))
                    vec[idx] = val
                    env[lhs] = vec
                elif rhs.startswith("extractvalue "):
                    parts = split_top_level(rhs[len("extractvalue "):])
                    agg = eval_val(strip_type_prefix(parts[0]), env)
                    idx = int(parts[1].strip())
                    env[lhs] = agg[idx]
                elif rhs.startswith("insertvalue "):
                    parts = split_top_level(rhs[len("insertvalue "):])
                    agg0 = eval_val(strip_type_prefix(parts[0]), env)
                    agg = [None, None, None] if isinstance(agg0, np.ndarray) else list(agg0)
                    val = eval_val(strip_type_prefix(parts[1]), env)
                    idx = int(parts[2].strip())
                    agg[idx] = val
                    env[lhs] = agg
                elif rhs.startswith("call "):
                    m_c = re.match(r"call\s+(?:arcp\s+|afn\s+)*[^@]+@([A-Za-z0-9_\.]+)\((.*)\)$", rhs)
                    cname, cargs_s = m_c.group(1), m_c.group(2)
                    cargs = [eval_val(strip_type_prefix(a), env) for a in split_top_level(cargs_s)]
                    if cname in funcs:
                        env[lhs] = run_fn(cname, cargs)
                    elif cname == "llvm.amdgcn.raw.buffer.load.i32":
                        buf, off = cargs[0], int(cargs[1])
                        env[lhs] = struct.unpack_from("<I", buf, off)[0]
                    elif cname == "llvm.amdgcn.raw.buffer.load.v2f32":
                        buf, off = cargs[0], int(cargs[1])
                        env[lhs] = np.array(struct.unpack_from("<2f", buf, off), dtype=np.float32)
                    elif cname == "llvm.amdgcn.image.sample.lz.2d.v4f32.f32":
                        u_c, v_c, t_tag = float(cargs[1]), float(cargs[2]), cargs[3]
                        fn_img = img_prev_fn if t_tag == "tc" else img_cur_fn
                        env[lhs] = np.asarray(fn_img(u_c, v_c), dtype=np.float32)
                    elif cname == "llvm.fabs.f32":
                        env[lhs] = np.float32(abs(float(cargs[0])))
                    elif cname in ("llvm.maxnum.f32", "llvm.maxnum.v4f32"):
                        env[lhs] = np.maximum(cargs[0], cargs[1])
                    elif cname in ("llvm.minnum.f32", "llvm.minnum.v4f32"):
                        env[lhs] = np.minimum(cargs[0], cargs[1])
                    elif cname == "llvm.umin.i32":
                        env[lhs] = min(int(cargs[0]) & 0xFFFFFFFF, int(cargs[1]) & 0xFFFFFFFF)
                    elif cname == "llvm.smax.i32":
                        a0 = struct.unpack("<i", struct.pack("<I", int(cargs[0]) & 0xFFFFFFFF))[0]
                        a1 = struct.unpack("<i", struct.pack("<I", int(cargs[1]) & 0xFFFFFFFF))[0]
                        env[lhs] = max(a0, a1)
                    elif cname == "llvm.smin.i32":
                        a0 = struct.unpack("<i", struct.pack("<I", int(cargs[0]) & 0xFFFFFFFF))[0]
                        a1 = struct.unpack("<i", struct.pack("<I", int(cargs[1]) & 0xFFFFFFFF))[0]
                        env[lhs] = min(a0, a1)
                    else:
                        raise NotImplementedError(f"Unsupported intrinsic: {cname}")
                elif rhs.startswith("select "):
                    parts = split_top_level(rhs[len("select "):])
                    cond = bool(eval_val(strip_type_prefix(parts[0]), env))
                    v_t = eval_val(strip_type_prefix(parts[1]), env)
                    v_f = eval_val(strip_type_prefix(parts[2]), env)
                    env[lhs] = v_t if cond else v_f
                elif rhs.startswith("icmp "):
                    rest = rhs[len("icmp "):]
                    pred = rest.split()[0]
                    parts = split_top_level(rest[len(pred):].strip())
                    a = int(eval_val(strip_type_prefix(parts[0]), env)) & 0xFFFFFFFF
                    b = int(eval_val(strip_type_prefix(parts[1]), env)) & 0xFFFFFFFF
                    sa = struct.unpack("<i", struct.pack("<I", a))[0]
                    sb = struct.unpack("<i", struct.pack("<I", b))[0]
                    env[lhs] = {
                        "eq": a == b, "ne": a != b,
                        "ult": a < b, "ule": a <= b, "ugt": a > b, "uge": a >= b,
                        "slt": sa < sb, "sle": sa <= sb, "sgt": sa > sb, "sge": sa >= sb,
                    }[pred]
                elif rhs.startswith("fcmp "):
                    rest = rhs[len("fcmp "):]
                    pred = rest.split()[0]
                    parts = split_top_level(rest[len(pred):].strip())
                    a = float(eval_val(strip_type_prefix(parts[0]), env))
                    b = float(eval_val(strip_type_prefix(parts[1]), env))
                    env[lhs] = {
                        "oeq": a == b, "one": a != b,
                        "olt": a < b, "ole": a <= b, "ogt": a > b, "oge": a >= b,
                    }[pred]
                elif rhs.startswith("bitcast "):
                    m_b = re.match(r"bitcast\s+(.+?)\s+to\s+(.+)$", rhs)
                    val = eval_val(strip_type_prefix(m_b.group(1)), env)
                    dst_t = m_b.group(2).strip()
                    if dst_t == "i32":
                        env[lhs] = struct.unpack("<i", struct.pack("<f", float(val)))[0]
                    elif dst_t == "float":
                        env[lhs] = np.float32(struct.unpack("<f", struct.pack("<I", int(val) & 0xFFFFFFFF))[0])
                elif rhs.startswith(("zext ", "trunc ", "uitofp ", "sitofp ")):
                    op = rhs.split()[0]
                    m_c = re.match(r"[a-z]+\s+(.+?)\s+to\s+(.+)$", rhs)
                    val = eval_val(strip_type_prefix(m_c.group(1)), env)
                    if op in ("zext", "trunc"):
                        env[lhs] = int(bool(val)) if isinstance(val, bool) else (int(val) & 0xFFFFFFFF)
                    else:
                        env[lhs] = np.float32(float(int(val)))
                else:
                    op = rhs.split()[0]
                    rest = rhs[len(op):].strip()
                    for flag_tok in ("arcp", "afn", "nsw", "nuw"):
                        if rest.startswith(flag_tok + " "):
                            rest = rest[len(flag_tok):].strip()
                    parts = split_top_level(rest)
                    a = eval_val(strip_type_prefix(parts[0]), env)
                    b = eval_val(strip_type_prefix(parts[1]), env)
                    if op == "fadd":
                        env[lhs] = np.float32(a) + np.float32(b)
                    elif op == "fsub":
                        env[lhs] = np.float32(a) - np.float32(b)
                    elif op == "fmul":
                        env[lhs] = np.float32(a) * np.float32(b)
                    elif op == "fdiv":
                        env[lhs] = np.float32(a) / np.float32(b)
                    elif op in ("and", "or", "xor") and isinstance(a, bool):
                        ba, bb = bool(a), bool(b)
                        env[lhs] = {"and": ba and bb, "or": ba or bb, "xor": ba != bb}[op]
                    else:
                        ia, ib = int(a) & 0xFFFFFFFF, int(b) & 0xFFFFFFFF
                        sa = struct.unpack("<i", struct.pack("<I", ia))[0]
                        env[lhs] = {
                            "add": (ia + ib) & 0xFFFFFFFF,
                            "sub": (ia - ib) & 0xFFFFFFFF,
                            "mul": (ia * ib) & 0xFFFFFFFF,
                            "shl": (ia << (ib & 31)) & 0xFFFFFFFF,
                            "lshr": (ia >> (ib & 31)) & 0xFFFFFFFF,
                            "ashr": (sa >> (ib & 31)) & 0xFFFFFFFF,
                            "and": ia & ib,
                            "or": ia | ib,
                            "xor": ia ^ ib,
                        }[op]

    gx = x // 8
    gy = y // 8
    tid = (y % 8) * 8 + (x % 8)
    run_fn("main", ["tbl", gx, gy, tid])
    mask_byte = int(vo[OFF_FGMASK + y * 120 + x])
    return {
        "fgmask": mask_byte,
        "hasfg": bool(mask_byte & FGMASK_HASFG),
        "isanc": bool(mask_byte & FGMASK_ISANC),
        "vmg_hi": bool(mask_byte & FGMASK_VMG_HI),
        "mov_obj": bool(mask_byte & FGMASK_MOV_OBJ),
        "mfit": bool(mask_byte & FGMASK_MFIT),
        "fgkill_ws": bool(mask_byte & FGMASK_FGKILL_WS),
        "fgkill": bool(mask_byte & FGMASK_FGKILL),
        "sub8": bool(mask_byte & FGMASK_SUB8),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("seq_dir", type=pathlib.Path, help="Directory with bench_seq_XXXX.bin files")
    ap.add_argument("-o", "--out", type=pathlib.Path, default=None, help="Optional JSON output path")
    args = ap.parse_args()
    res = analyze_sequence(args.seq_dir)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    s = res["summary"]
    print(
        f"steps={res['steps']} "
        f"nonuniform_steps={s['nonuniform_steps_count']}/{res['steps']} "
        f"fgkill_flip_cell={s['fgkill_flip_rate_cell_fixed']:.4f} "
        f"(mc={s['fgkill_flip_rate_cell_motion_comp']:.4f}, active={s['fgkill_flip_rate_cell_active_union']:.4f}, "
        f"uniform_only={s['fgkill_flip_rate_cell_fixed_uniform_only']:.4f} on {s['uniform_pairs_count']} pairs) "
        f"fgkill_flip_px={s['fgkill_flip_rate_pixel_fixed']:.4f} "
        f"(active={s['fgkill_flip_rate_pixel_active_union']:.4f}) "
        f"mean|V2|={s['mean_abs_v2_overall']:.2f} ({s['mean_abs_v2_min']:.2f}..{s['mean_abs_v2_max']:.2f})"
    )
    if s["low_motion_warning"]:
        print(
            f"WARNING: LOW MOTION — mean|V2| < 2.0 on {s['low_motion_steps_count']}/{res['steps']} steps "
            f"(range {s['mean_abs_v2_min']:.2f}..{s['mean_abs_v2_max']:.2f} qpx); "
            f"camera/object was mostly static during capture!"
        )


if __name__ == "__main__":
    main()
