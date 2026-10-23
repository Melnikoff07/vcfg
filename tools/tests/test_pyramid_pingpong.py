"""Unit and hardware-budget verification suite for R13 Temporal Pyramid Ping-Pong Cache
and Warm Single-Frame Fast Path (research/r13-pyramid-pingpong).

Verifies:
  1. Test 1 (Cold Start -> Warm Mode): First frame builds both pyramids (pyr_warm == 0, pyr_evals == 2),
     subsequent consecutive frames (P == prev C) enable pyr_warm == 1 (FG8_FLAG_PYR_WARM = 0x1000),
     toggle pyr_parity (0 -> 1 -> 0 -> 1), and execute only 1 pyramid build per frame (50% reduction).
  2. Test 2 (Frame Drop / Scene Cut / Resolution Change / Post-Pass Hibernation): On any skipped frame
     (P != prev C), explicit cache invalidation, or resolution change, pyr_warm resets to 0 and rebuilds
     both pyramids (prev + cur) and both luma sums (sump + sumc) without parity desynchronization.
  3. Test 3 (Bit-Exact 10-Frame Handoff & OSD Contamination Resistance): Across a 10-frame sequence,
     all pyramid levels (L1prev..L3prev / L0prev..L2prev, Eprev and L1cur..L3cur / L0cur..L2cur, Ecur),
     luma sums (sump, sumc), and final motion fields (V3, V2, GK, FT, F) in Ping-Pong mode match a
     naive dual-frame recomputation bit-for-bit (max_diff == 0). Furthermore, when an in-place OSD or
     post-processing pass modifies the displayed cur buffer at the end of frame N-1, Warm Ping-Pong mode
     preserves the uncontaminated pyramid on frame N (max_diff == 0 vs clean reference), whereas naive
     dual-frame re-sampling suffers OSD motion contamination.
  4. Test 4 (Hardware Register, Scratch, Shader Region, and DirectMemory Budgets): Verifies pyr8.ll,
     me3.ll, and all classic/shaders/*.ll compile cleanly via classic/gen_shaders.py with vgprs <= 64,
     scratch == 0, total shader region <= 0x1C000, and zero extra DirectMemory (WORK_SZ = 0x220000,
     WORK_SZ_8 = 0x190000).
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
BENCH = ROOT / "tools" / "bench"
sys.path.insert(0, str(BENCH))

import vcfg_motion as vm

H, W = 1080, 1920


def make_synthetic_sequence(num_frames=11, h=H, w=W, seed=42):
    """Generate a deterministic 1920x1080 sequence with camera pan, foreground object motion, and illumination."""
    rng = np.random.default_rng(seed)
    yy, xx = np.meshgrid(
        np.linspace(0.0, 1.0, h, dtype=np.float32),
        np.linspace(0.0, 1.0, w, dtype=np.float32),
        indexing="ij",
    )
    noise_tile = rng.uniform(-0.08, 0.08, size=(128, 128, 3)).astype(np.float32)
    frames = []
    for t in range(num_frames):
        pan_x = t * 6
        pan_y = t * 2
        bg_r = 0.25 + 0.20 * np.sin((xx * w + pan_x) * np.float32(0.025))
        bg_g = 0.30 + 0.20 * np.cos((yy * h + pan_y) * np.float32(0.030))
        bg_b = 0.35 + 0.15 * np.sin(((xx * w + pan_x) + (yy * h + pan_y)) * np.float32(0.020))
        frame = np.stack([bg_r, bg_g, bg_b], axis=-1).astype(np.float32)

        # Add tiled high-frequency texture
        ty = (np.arange(h) + pan_y) % 128
        tx = (np.arange(w) + pan_x) % 128
        frame += noise_tile[ty[:, None], tx[None, :]]

        # Add moving foreground rectangle
        ox0 = 320 + t * 14
        oy0 = 260 + t * 4
        ox1 = min(ox0 + 160, w)
        oy1 = min(oy0 + 200, h)
        frame[oy0:oy1, ox0:ox1, 0] = 0.82
        frame[oy0:oy1, ox0:ox1, 1] = 0.22 + 0.10 * np.cos(xx[oy0:oy1, ox0:ox1] * np.float32(40.0))
        frame[oy0:oy1, ox0:ox1, 2] = 0.18 + 0.10 * np.sin(yy[oy0:oy1, ox0:ox1] * np.float32(40.0))

        frames.append(np.clip(frame, 0.0, 1.0).astype(np.float32))
    return frames


def find_llc():
    """Find llc-gfx700 compiler from config.json or neighboring worktrees."""
    candidates = [
        ROOT / "config.json",
        ROOT.parent / "vcfg-alt" / "config.json",
    ]
    for cfg_path in candidates:
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            llc = cfg.get("llc_gfx700")
            if llc and pathlib.Path(llc).is_file():
                return str(llc)
    llc_which = shutil.which("llc")
    return llc_which


class TestPyramidPingPong(unittest.TestCase):
    def test_01_cold_start_to_warm_transition_and_parity_toggle(self):
        """Test 1: Cold start builds both pyramids (pyr_warm == 0); consecutive frames use pyr_warm == 1 and toggle parity."""
        frames = make_synthetic_sequence(num_frames=6, h=270, w=480)
        addrs = [0x100000000 + i * 0x800000 for i in range(len(frames))]
        cache = vm.PyramidPingPongCache()

        # Pair 0 (F0 -> F1): Cold start
        st0 = cache.step(frames[0], frames[1], addrs[0], addrs[1])
        self.assertFalse(st0["pyr_warm"])
        self.assertEqual(st0["flags"], 0)
        self.assertEqual(st0["parity"], 0)
        self.assertEqual(st0["pyr_evals"], 2)
        self.assertEqual(cache.cold_misses, 1)
        self.assertEqual(cache.warm_hits, 0)

        # Pair 1..4 (F1 -> F2, F2 -> F3, F3 -> F4, F4 -> F5): Consecutive warm frames
        expected_parities = [1, 0, 1, 0]
        for idx, exp_par in enumerate(expected_parities, start=1):
            st = cache.step(frames[idx], frames[idx + 1], addrs[idx], addrs[idx + 1])
            self.assertTrue(st["pyr_warm"], f"Expected warm hit on pair {idx}")
            self.assertEqual(st["flags"], vm.FG8_FLAG_PYR_WARM)
            self.assertEqual(st["parity"], exp_par)
            self.assertEqual(st["pyr_evals"], 1)

        self.assertEqual(cache.cold_misses, 1)
        self.assertEqual(cache.warm_hits, 4)

    def test_02_discontinuity_scene_cut_and_resolution_change_fallback(self):
        """Test 2: Frame drop (P != prev C), explicit invalidation, or resolution change resets pyr_warm to 0."""
        frames = make_synthetic_sequence(num_frames=7, h=270, w=480)
        addrs = [0x200000000 + i * 0x800000 for i in range(len(frames))]
        cache = vm.PyramidPingPongCache()

        # Pair 0 (F0 -> F1): Cold
        st0 = cache.step(frames[0], frames[1], addrs[0], addrs[1])
        self.assertFalse(st0["pyr_warm"])
        self.assertEqual(st0["parity"], 0)

        # Pair 1 (F1 -> F2): Warm (parity -> 1)
        st1 = cache.step(frames[1], frames[2], addrs[1], addrs[2])
        self.assertTrue(st1["pyr_warm"])
        self.assertEqual(st1["parity"], 1)

        # Simulate a skipped frame (F2 -> F3 skipped; next pair is F3 -> F4, so P = addrs[3] != addrs[2])
        st_drop = cache.step(frames[3], frames[4], addrs[3], addrs[4])
        self.assertFalse(st_drop["pyr_warm"])
        self.assertEqual(st_drop["flags"], 0)
        self.assertEqual(st_drop["parity"], 0)
        self.assertEqual(st_drop["pyr_evals"], 2)
        # Verify both sumc and sump were freshly populated on cold fallback
        _, exp_sumc, exp_sump = vm.me3_dual(st_drop["L3p"], st_drop["L3c"])
        self.assertEqual(st_drop["lumasum_cur"], exp_sumc)
        self.assertEqual(st_drop["lumasum_prev"], exp_sump)

        # Next consecutive frame (F4 -> F5) warms right back up
        st_warm = cache.step(frames[4], frames[5], addrs[4], addrs[5])
        self.assertTrue(st_warm["pyr_warm"])
        self.assertEqual(st_warm["parity"], 1)

        # Simulate resolution change on (F5 -> F6) -> must trigger Cold Dual-Frame rebuild
        st_res = cache.step(frames[5], frames[6], addrs[5], addrs[6], width=1280, height=720)
        self.assertFalse(st_res["pyr_warm"])
        self.assertEqual(st_res["flags"], 0)
        self.assertEqual(st_res["parity"], 0)
        self.assertEqual(st_res["pyr_evals"], 2)

        # Simulate post-only / FG-off hibernation via invalidate()
        st_warm2 = cache.step(frames[5], frames[6], addrs[6], addrs[0], width=1280, height=720)
        self.assertTrue(st_warm2["pyr_warm"])
        cache.invalidate()
        st_after_inv = cache.step(frames[6], frames[0], addrs[0], addrs[1], width=1280, height=720)
        self.assertFalse(st_after_inv["pyr_warm"])
        self.assertEqual(st_after_inv["pyr_evals"], 2)

    def test_03_bit_exact_10_frame_handoff_and_osd_uncontaminated_pyramid(self):
        """Test 3: Across 10 consecutive 1920x1080 frame pairs, Ping-Pong mode matches naive dual-frame rebuild with max_diff == 0,
        and resists in-place OSD contamination on frame N-1."""
        frames = make_synthetic_sequence(num_frames=11, h=H, w=W, seed=1337)
        addrs = [0x300000000 + i * 0x800000 for i in range(len(frames))]

        warm_cache = vm.PyramidPingPongCache()
        naive_cache = vm.PyramidPingPongCache()

        prevF_warm, hist_warm = None, None
        prevF_naive, hist_naive = None, None

        total_warm_evals = 0
        total_naive_evals = 0

        for i in range(10):
            P_frame, C_frame = frames[i], frames[i + 1]
            P_addr, C_addr = addrs[i], addrs[i + 1]

            st_warm = warm_cache.step(
                P_frame, C_frame, P_addr, C_addr,
                prevF=prevF_warm, anchor_hist=hist_warm, force_cold=False,
            )
            st_naive = naive_cache.step(
                P_frame, C_frame, P_addr, C_addr,
                prevF=prevF_naive, anchor_hist=hist_naive, force_cold=True,
            )

            if i == 0:
                self.assertFalse(st_warm["pyr_warm"])
            else:
                self.assertTrue(st_warm["pyr_warm"])

            total_warm_evals += st_warm["pyr_evals"]
            total_naive_evals += st_naive["pyr_evals"]

            # Verify bit-exact pyramid equality on all levels (L1..L3 and aliases L0..L2, E)
            for key in ("L1p", "L2p", "L3p", "L1c", "L2c", "L3c"):
                max_diff = int(np.max(np.abs(st_warm[key] - st_naive[key])))
                self.assertEqual(max_diff, 0, f"Frame pair {i} mismatch in {key}: max_diff={max_diff}")

            for alias in ("L0", "L1", "L2", "E"):
                diff_prev = int(np.max(np.abs(st_warm["slot_prev"][alias] - st_naive["slot_prev"][alias])))
                diff_cur = int(np.max(np.abs(st_warm["slot_cur"][alias] - st_naive["slot_cur"][alias])))
                self.assertEqual(diff_prev, 0)
                self.assertEqual(diff_cur, 0)

            # Verify exact luma sums and motion estimation outputs
            self.assertEqual(st_warm["lumasum_prev"], st_naive["lumasum_prev"])
            self.assertEqual(st_warm["lumasum_cur"], st_naive["lumasum_cur"])
            self.assertEqual(st_warm["gm"], st_naive["gm"])
            self.assertEqual(st_warm["cutn"], st_naive["cutn"])
            self.assertEqual(st_warm["fade"], st_naive["fade"])
            self.assertEqual(int(np.max(np.abs(st_warm["V3"] - st_naive["V3"]))), 0)
            self.assertEqual(int(np.max(np.abs(st_warm["V2"] - st_naive["V2"]))), 0)
            self.assertEqual(int(np.max(np.abs(st_warm["GK"] - st_naive["GK"]))), 0)
            self.assertEqual(float(np.max(np.abs(st_warm["FT"] - st_naive["FT"]))), 0.0)
            self.assertEqual(float(np.max(np.abs(st_warm["F"] - st_naive["F"]))), 0.0)

            prevF_warm, hist_warm = st_warm["F"], st_warm["anchor_hist"]
            prevF_naive, hist_naive = st_naive["F"], st_naive["anchor_hist"]

        # 10 frame pairs: Warm Ping-Pong performs 2 + 9*1 = 11 pyramid builds vs 20 in naive mode (45% total / 50% steady-state reduction)
        self.assertEqual(total_warm_evals, 11)
        self.assertEqual(total_naive_evals, 20)

        # Verify OSD / in-place post-processing immunity on frame N:
        # At the end of pair 0 (frames[0] -> frames[1]), osd8 stamps an overlay onto the displayed scanout buffer frames[1] in-place.
        # On pair 1 (frames[1]_with_osd -> frames[2]), Warm Ping-Pong reuses the clean pyramid built BEFORE osd8 ran!
        clean_cache = vm.PyramidPingPongCache()
        clean_cache.step(frames[0], frames[1], addrs[0], addrs[1])
        st_clean_pair1 = clean_cache.step(frames[1], frames[2], addrs[1], addrs[2])

        osd_cache = vm.PyramidPingPongCache()
        osd_cache.step(frames[0], frames[1], addrs[0], addrs[1])
        # Simulate in-place OSD / post-process stamp on frames[1] AFTER pair 0's pyr8 has already completed
        frames1_osd_contaminated = frames[1].copy()
        frames1_osd_contaminated[24:88, 24:320, :] = 1.0  # bright white OSD box in top-left corner
        st_warm_osd_pair1 = osd_cache.step(frames1_osd_contaminated, frames[2], addrs[1], addrs[2])

        # Warm Ping-Pong must be 100% immune to the in-place OSD stamp on frames[1]
        self.assertEqual(int(np.max(np.abs(st_warm_osd_pair1["L1p"] - st_clean_pair1["L1p"]))), 0)
        self.assertEqual(int(np.max(np.abs(st_warm_osd_pair1["L2p"] - st_clean_pair1["L2p"]))), 0)
        self.assertEqual(int(np.max(np.abs(st_warm_osd_pair1["L3p"] - st_clean_pair1["L3p"]))), 0)
        self.assertEqual(float(np.max(np.abs(st_warm_osd_pair1["F"] - st_clean_pair1["F"]))), 0.0)

    def test_04_hardware_register_scratch_and_memory_budgets(self):
        """Test 4: Verify shader compilation, VGPR <= 64, scratch == 0, shader region <= 0x1C000, and zero extra DirectMemory."""
        fg8_hdr = (ROOT / "classic" / "source" / "fg8_table.h").read_text(encoding="utf-8")
        main_src = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")

        self.assertIn("#define FG8_FLAG_PYR_WARM 0x1000u", fg8_hdr)
        self.assertIn("#define WORK_SZ   0x220000u", main_src)
        self.assertIn("#define WORK_SZ_8 0x190000u", main_src)
        self.assertIn("FG8_FLAG_PYR_WARM", main_src)
        self.assertIn("pyr_cache_invalidate", main_src)

        llc = find_llc()
        self.assertIsNotNone(llc, "llc-gfx700 binary not found")

        gen_script = ROOT / "classic" / "gen_shaders.py"
        shaders_dir = ROOT / "classic" / "shaders"
        with tempfile.TemporaryDirectory() as tmpdir:
            out_h = pathlib.Path(tmpdir) / "shaders.h"
            proc = subprocess.run(
                [sys.executable, str(gen_script), llc, str(shaders_dir), str(out_h)],
                capture_output=True,
                text=True,
                check=True,
            )
            output = proc.stdout

        shader_stats = {}
        for line in output.splitlines():
            m = re.match(
                r"^(\w+):\s+(\d+)\s+words,\s+vgprs=(\d+)\s+sgprs=(\d+)\s+user=(\d+)\s+lds=(\d+)\s+scratch=(\d+)",
                line.strip(),
            )
            if m:
                name = m.group(1)
                shader_stats[name] = {
                    "words": int(m.group(2)),
                    "vgprs": int(m.group(3)),
                    "sgprs": int(m.group(4)),
                    "user": int(m.group(5)),
                    "lds": int(m.group(6)),
                    "scratch": int(m.group(7)),
                }
                self.assertLessEqual(shader_stats[name]["vgprs"], 64, f"{name} exceeds 64 VGPRs")
                self.assertEqual(shader_stats[name]["scratch"], 0, f"{name} has nonzero scratch")

        self.assertIn("pyr8", shader_stats)
        self.assertIn("me3", shader_stats)
        self.assertLessEqual(shader_stats["pyr8"]["vgprs"], 24)
        self.assertLessEqual(shader_stats["me3"]["vgprs"], 56)

        region_match = re.search(r"shader region:\s+0x8000\s+\+\s+0x([0-9a-fA-F]+)\s+=\s+0x([0-9a-fA-F]+)", output)
        self.assertIsNotNone(region_match, "shader region line missing from gen_shaders.py output")
        region_end = int(region_match.group(2), 16)
        self.assertLessEqual(region_end, 0x1C000, f"Shader region 0x{region_end:X} exceeds 0x1C000")


if __name__ == "__main__":
    unittest.main()
