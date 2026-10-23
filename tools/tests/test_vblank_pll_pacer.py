#!/usr/bin/env python3
"""Discrete-event simulation and verification suite for the Sub-Millisecond VBlank PLL
Frame Pacer & Dynamic Temporal Interpolation Fraction (t_interp).

Models:
  1. Hardware VBlank clock at 59.94 Hz (16,683.35 us) with OS thread scheduling
     and polling jitter (+/- 150 us).
  2. Four game frametime regimes:
     - Locked 30 FPS (33.37 ms +/- 0.5 ms)
     - Heavy scene 24-27 FPS (37-42 ms, 3-VBlank regime)
     - Unlocked 38-45 FPS (22-26 ms)
     - Sudden frametime spike (33 ms -> 55 ms -> 33 ms)
  3. Quantitative comparison of Baseline Pacer vs. VBlank PLL + Dynamic t_interp Pacer:
     - VBlank Miss Rate (%)
     - Display Motion Judder Error (px) at constant pan velocity v = 12 px/VBlank
"""

from __future__ import annotations

import math
import pathlib
import random
import subprocess
import unittest
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C = ROOT / "classic" / "source" / "main.c"
PHASE8_LL = ROOT / "classic" / "shaders" / "phase8.ll"
WARP8_LL = ROOT / "classic" / "shaders" / "warp8.ll"
SH8_H = ROOT / "classic" / "build" / "sh8.h"

TRUE_VB_US = 16683.35  # 59.94 Hz exact period in microseconds
PAN_VEL_PX_PER_VB = 12.0  # Constant camera pan speed: 12 px per VBlank (~719.3 px/s)
GPU_FG_LATCH_US = 1550.0  # GPU FG compute + flip queue latch time before VBlank edge


@dataclass
class SimMetrics:
    regime: str
    frames: int
    baseline_miss_rate_pct: float
    pll_miss_rate_pct: float
    baseline_rms_judder_px: float
    pll_rms_judder_px: float
    baseline_max_judder_px: float
    pll_max_judder_px: float
    judder_reduction_pct: float


class VBlankPLLModel:
    """Bit-exact Python mirror of classic/source/main.c VBlank PLL & t_interp predictor."""

    PLL_LEAD_US = 2200
    PLL_LATCH_US = 1550

    def __init__(self) -> None:
        self.g_vblank_t_us: int = 0
        self.g_vblank_cnt: int = 0
        self.g_pll_phi_us: int = 0
        self.g_pll_vb_cnt: int = 0
        self.g_pll_period_q8: int = (16683 << 8) | 85  # 16683.332 us in Q24.8
        self.g_pll_last_real_vb_us: int = 0
        self.g_pll_t_interp_q16: int = 32768
        self.g_pll_t_permille: int = 500
        self.g_ema_frame_us: int = 33366
        self.g_pll_shortened: int = 0

    def vblank_pll_update(self, vb_cnt: int, vb_time_us: int) -> None:
        if vb_time_us <= 0:
            return
        prev_phi = self.g_pll_phi_us
        prev_cnt = self.g_pll_vb_cnt
        period_q8 = self.g_pll_period_q8
        if (
            prev_phi == 0
            or vb_cnt <= prev_cnt
            or (vb_cnt - prev_cnt) > 120
            or vb_time_us <= prev_phi
        ):
            if vb_cnt != prev_cnt:
                self.g_pll_phi_us = vb_time_us
                self.g_pll_vb_cnt = vb_cnt
        else:
            dc = vb_cnt - prev_cnt
            dt_us = vb_time_us - prev_phi
            meas_q8 = (dt_us << 8) // dc
            if (10000 << 8) <= meas_q8 <= (25000 << 8):
                pred_us = prev_phi + ((dc * period_q8 + 128) >> 8)
                err_us = vb_time_us - pred_us
                if -4000 < err_us < 4000:
                    self.g_pll_phi_us = pred_us + (err_us >> 3)
                    p_adj = int(err_us / (dc << 2))
                    new_p = period_q8 + p_adj
                    nom_p = (16683 << 8) | 85
                    if nom_p - 32 <= new_p <= nom_p + 32:
                        self.g_pll_period_q8 = new_p
                else:
                    period_q8 = (period_q8 * 15 + meas_q8 + 8) >> 4
                    self.g_pll_period_q8 = period_q8
                    self.g_pll_phi_us = vb_time_us
                self.g_pll_vb_cnt = vb_cnt
            else:
                self.g_pll_phi_us = vb_time_us
                self.g_pll_vb_cnt = vb_cnt
        self.g_vblank_t_us = vb_time_us
        self.g_vblank_cnt = vb_cnt

    def vblank_period_us(self) -> int:
        p_q8 = self.g_pll_period_q8
        if (10000 << 8) <= p_q8 <= (25000 << 8):
            return (p_q8 + 128) >> 8
        return 16683

    def vblank_to_next_edge_us(self, now_us: int) -> tuple[int, int]:
        vb_us = self.vblank_period_us()
        phi = self.g_pll_phi_us or self.g_vblank_t_us
        p_q8 = self.g_pll_period_q8
        if p_q8 < (10000 << 8) or p_q8 > (25000 << 8):
            p_q8 = vb_us << 8
        if not phi:
            return vb_us >> 1, vb_us
        if now_us <= phi:
            diff = phi - now_us
            return (diff if 0 < diff <= vb_us else (vb_us >> 1)), vb_us
        elapsed_q8 = (now_us - phi) << 8
        k = (elapsed_q8 // p_q8) + 1
        next_edge_us = phi + ((k * p_q8 + 128) >> 8)
        if next_edge_us <= now_us:
            k += 1
            next_edge_us = phi + ((k * p_q8 + 128) >> 8)
        rem_us = next_edge_us - now_us
        return (rem_us if rem_us > 0 else vb_us), vb_us

    def pll_adaptive_hold_us(self, now_us: int, dt_enter_us: int, req_hold_us: int) -> int:
        to_next_vb_us, vb_us = self.vblank_to_next_edge_us(now_us)
        win_2vb_us = vb_us * 2
        if dt_enter_us > 0 and (dt_enter_us < vb_us or to_next_vb_us < self.PLL_LEAD_US):
            target_edge_us = to_next_vb_us + vb_us
        else:
            target_edge_us = to_next_vb_us
        vb_budget_us = (target_edge_us - self.PLL_LEAD_US) if target_edge_us > self.PLL_LEAD_US else 0

        rem_2vb_us = 0
        if 0 < dt_enter_us < win_2vb_us:
            rem_2vb_us = win_2vb_us - dt_enter_us

        hold_us = req_hold_us
        if hold_us > rem_2vb_us:
            hold_us = rem_2vb_us
        if hold_us > vb_budget_us:
            hold_us = vb_budget_us
        if to_next_vb_us > hold_us:
            rem_after = to_next_vb_us - hold_us
            if (self.PLL_LATCH_US - 200) <= rem_after <= (self.PLL_LATCH_US + 200):
                nudge_us = rem_after - (self.PLL_LATCH_US - 250)
                if hold_us + nudge_us <= req_hold_us:
                    hold_us += nudge_us
        if hold_us < req_hold_us:
            self.g_pll_shortened += 1
        return hold_us

    def pll_predict_t_interp_q16(self, now_us: int, dt_enter_us: int, hold_mid_us: int) -> int:
        to_next_vb_us, vb_us = self.vblank_to_next_edge_us(now_us)
        to_mid_vb_us = to_next_vb_us if to_next_vb_us >= self.PLL_LATCH_US else (to_next_vb_us + vb_us)
        v_m_us = now_us + to_mid_vb_us
        v_p_us = self.g_pll_last_real_vb_us
        if v_p_us > 0 and v_m_us < v_p_us + vb_us:
            v_m_us = v_p_us + vb_us

        extra_mid_vb = ((hold_mid_us + (vb_us >> 1)) // vb_us) if hold_mid_us > (vb_us >> 1) else 0
        vb_m_to_c = 1 + extra_mid_vb

        if v_p_us > 0 and v_m_us > v_p_us and (v_m_us - v_p_us) <= vb_us * 6:
            vb_p_to_m = ((v_m_us - v_p_us) + (vb_us >> 1)) // vb_us
            if vb_p_to_m < 1:
                vb_p_to_m = 1
        else:
            dt_ref = (
                dt_enter_us
                if (vb_us <= dt_enter_us <= vb_us * 6)
                else (self.g_ema_frame_us if self.g_ema_frame_us > 20000 else vb_us * 2)
            )
            total_vb = (dt_ref + (vb_us >> 1)) // vb_us
            total_vb = max(2, min(5, total_vb))
            vb_p_to_m = (total_vb - vb_m_to_c) if total_vb > vb_m_to_c else 1

        vb_p_to_c = vb_p_to_m + vb_m_to_c
        t_q16 = ((vb_p_to_m << 16) + (vb_p_to_c >> 1)) // vb_p_to_c
        t_q16 = max(21845, min(43691, t_q16))

        t_permille = (vb_p_to_m * 1000 + (vb_p_to_c >> 1)) // vb_p_to_c
        t_permille = max(333, min(667, t_permille))

        self.g_pll_t_interp_q16 = t_q16
        self.g_pll_t_permille = t_permille
        return t_q16

    def pll_commit_frame_vblank(self, submit_us: int, hold_mid_us: int) -> None:
        to_next_vb_us, vb_us = self.vblank_to_next_edge_us(submit_us)
        to_mid_vb_us = to_next_vb_us if to_next_vb_us >= self.PLL_LATCH_US else (to_next_vb_us + vb_us)
        v_m_us = submit_us + to_mid_vb_us
        prev_vp = self.g_pll_last_real_vb_us
        if prev_vp > 0 and v_m_us < prev_vp + vb_us:
            v_m_us = prev_vp + vb_us
        extra_mid_vb = ((hold_mid_us + (vb_us >> 1)) // vb_us) if hold_mid_us > (vb_us >> 1) else 0
        v_c_us = v_m_us + (1 + extra_mid_vb) * vb_us
        self.g_pll_last_real_vb_us = v_c_us


def phase8_decode_t(raw_val: int, is_override: bool = True) -> float:
    """Exact mirror of classic/shaders/phase8.ll decoding of permille (1..1000) or Q0.16 (1001..65535)."""
    raw_val &= 0xFFFF
    if raw_val == 0:
        return 0.5
    if raw_val <= 1000:
        t = float(raw_val) * 0.001
    else:
        t = float(raw_val) * (1.0 / 65536.0)
    if is_override:
        return max(0.25, min(0.75, t))
    return max(1.0 / 3.0, min(2.0 / 3.0, t))


def simulate_regime(regime_name: str, frametimes_us: list[float], seed: int = 42) -> SimMetrics:
    """Run discrete-event simulation comparing Baseline Pacer vs. VBlank PLL + Dynamic t_interp Pacer."""
    rng_base = random.Random(seed)
    rng_pll = random.Random(seed)

    vb_0_us = 100_000.0  # Hardware VBlank #0 reference timestamp

    def hw_vb_time(k: int) -> float:
        return vb_0_us + k * TRUE_VB_US

    def next_hw_vb_index(t_us: float) -> int:
        if t_us <= vb_0_us:
            return 0
        return int(math.floor((t_us - vb_0_us) / TRUE_VB_US)) + 1

    # --- 1. Baseline Pacer Simulation (PLL_LEAD_US=1200, raw timestamp, fixed/lagged t=0.50) ---
    base_misses = 0
    base_judder_sq_sum = 0.0
    base_max_judder = 0.0

    # Warm up VBlank status at k = 10
    cur_vb_k = 10
    t_prev_submit_base = hw_vb_time(cur_vb_k) - 2000.0
    last_real_disp_vb_base = cur_vb_k + 1  # V_P for first frame
    prev_real_hold_us_base = 16683
    ema_frame_us_base = 33366

    for ft_us in frametimes_us:
        t_arrive = t_prev_submit_base + ft_us
        dt_enter_us = int(round(t_arrive - t_prev_submit_base))
        ema_frame_us_base = (ema_frame_us_base * 3 + dt_enter_us) >> 2

        # mon_thread raw VBlank sample with +/- 150 us polling/timestamp jitter
        latest_vb_idx = max(0, next_hw_vb_index(t_arrive) - 1)
        raw_vt = int(round(hw_vb_time(latest_vb_idx) + rng_base.uniform(-150.0, 150.0)))
        vb_period_base = 16683

        if t_arrive <= raw_vt:
            to_next_vb = vb_period_base >> 1
        else:
            phase_us = int(round(t_arrive - raw_vt)) % vb_period_base
            to_next_vb = (vb_period_base - phase_us) or vb_period_base

        # Baseline pll_adaptive_hold_us with PLL_LEAD_US = 1200
        lead_base = 1200
        win_2vb = vb_period_base * 2
        target_edge_us = (to_next_vb + vb_period_base) if (0 < dt_enter_us < vb_period_base) else to_next_vb
        vb_budget_us = (target_edge_us - lead_base) if target_edge_us > lead_base else 0
        rem_2vb_us = (win_2vb - dt_enter_us) if (0 < dt_enter_us < win_2vb) else 0
        hold_us = min(60000, rem_2vb_us, vb_budget_us)

        # Intended VBlank edge for M before sleep + wakeup jitter
        intended_edge_time = t_arrive + target_edge_us
        intended_vb_m = int(round((intended_edge_time - vb_0_us) / TRUE_VB_US))
        intended_vb_m = max( intended_vb_m, last_real_disp_vb_base + 1 )

        # OS sleep wakeup jitter (+/- 150 us)
        wakeup_jitter = rng_base.uniform(-150.0, 150.0) if hold_us > 0 else rng_base.uniform(-50.0, 50.0)
        t_submit = t_arrive + hold_us + max(0.0, wakeup_jitter)

        # Actual hardware VBlank where M latches (requires GPU_FG_LATCH_US before VBlank edge)
        actual_vb_m = next_hw_vb_index(t_submit + GPU_FG_LATCH_US)
        actual_vb_m = max(actual_vb_m, last_real_disp_vb_base + 1)
        if actual_vb_m > intended_vb_m:
            base_misses += 1

        actual_vb_c = actual_vb_m + 1

        # Baseline t1_q16 calculation: lagged prev_real_hold_us, 50/50 blend with 0.5, or forced 0.5 in >38ms
        tot_us = max(20000, ema_frame_us_base)
        if tot_us > 38000:
            t_q16_base = 32768  # Forced 0.50 in baseline 3-VBlank branch
        else:
            t_real = (prev_real_hold_us_base << 16) // tot_us
            t_q16_base = max(22937, min(42598, (t_real + 32768) >> 1))
        t_interp_base = phase8_decode_t(t_q16_base, is_override=True)

        # True camera positions at P, C, and ideal position at M's display VBlank
        pos_p = PAN_VEL_PX_PER_VB * last_real_disp_vb_base
        pos_c = PAN_VEL_PX_PER_VB * actual_vb_c
        pos_m_ideal = PAN_VEL_PX_PER_VB * actual_vb_m
        pos_m_shown = pos_p + t_interp_base * (pos_c - pos_p)

        judder_err = abs(pos_m_shown - pos_m_ideal)
        base_judder_sq_sum += judder_err * judder_err
        if judder_err > base_max_judder:
            base_max_judder = judder_err

        prev_real_hold_us_base = int(round((actual_vb_m - last_real_disp_vb_base) * TRUE_VB_US))
        last_real_disp_vb_base = actual_vb_c
        t_prev_submit_base = t_submit

    # --- 2. New VBlank PLL + Dynamic t_interp Pacer Simulation ---
    pll = VBlankPLLModel()
    # Seed PLL over 10 VBlanks during startup (flips < INJECT_AFTER_FLIPS)
    for k in range(1, 11):
        sample_t = int(round(hw_vb_time(k) + rng_pll.uniform(-150.0, 150.0)))
        pll.vblank_pll_update(k, sample_t)

    pll_misses = 0
    pll_judder_sq_sum = 0.0
    pll_max_judder = 0.0

    t_prev_submit_pll = hw_vb_time(10) - 2000.0
    last_real_disp_vb_pll = 11
    pll.g_pll_last_real_vb_us = int(round(hw_vb_time(last_real_disp_vb_pll)))

    for ft_us in frametimes_us:
        t_arrive = t_prev_submit_pll + ft_us
        dt_enter_us = int(round(t_arrive - t_prev_submit_pll))
        pll.g_ema_frame_us = (pll.g_ema_frame_us * 3 + dt_enter_us) >> 2

        # Update PLL with latest VBlank sample (with +/- 150 us OS jitter)
        latest_vb_idx = max(1, next_hw_vb_index(t_arrive) - 1)
        sample_t = int(round(hw_vb_time(latest_vb_idx) + rng_pll.uniform(-150.0, 150.0)))
        pll.vblank_pll_update(latest_vb_idx, sample_t)

        now_us_int = int(round(t_arrive))
        to_next_vb_us, vb_us = pll.vblank_to_next_edge_us(now_us_int)
        hold_us = pll.pll_adaptive_hold_us(now_us_int, dt_enter_us, 60000)

        # Intended VBlank edge for M under PLL pacer
        if dt_enter_us < vb_us or to_next_vb_us < pll.PLL_LEAD_US:
            target_edge_us = to_next_vb_us + vb_us
        else:
            target_edge_us = to_next_vb_us
        intended_vb_m = int(round((t_arrive + target_edge_us - vb_0_us) / TRUE_VB_US))
        intended_vb_m = max(intended_vb_m, last_real_disp_vb_pll + 1)

        # OS sleep wakeup jitter (+/- 150 us)
        wakeup_jitter = rng_pll.uniform(-150.0, 150.0) if hold_us > 0 else rng_pll.uniform(-50.0, 50.0)
        t_submit = t_arrive + hold_us + max(0.0, wakeup_jitter)
        t_submit_int = int(round(t_submit))

        # Re-evaluate t_interp right at t_s1 (post-pace_pre, pre-submit) as in classic/source/main.c
        t_q16_pll = pll.pll_predict_t_interp_q16(t_submit_int, dt_enter_us, 0)
        pll.pll_commit_frame_vblank(t_submit_int, 0)
        t_interp_pll = phase8_decode_t(t_q16_pll, is_override=True)

        # Actual hardware VBlank where M latches
        actual_vb_m = next_hw_vb_index(t_submit + GPU_FG_LATCH_US)
        actual_vb_m = max(actual_vb_m, last_real_disp_vb_pll + 1)
        if actual_vb_m > intended_vb_m:
            pll_misses += 1

        actual_vb_c = actual_vb_m + 1

        pos_p = PAN_VEL_PX_PER_VB * last_real_disp_vb_pll
        pos_c = PAN_VEL_PX_PER_VB * actual_vb_c
        pos_m_ideal = PAN_VEL_PX_PER_VB * actual_vb_m
        pos_m_shown = pos_p + t_interp_pll * (pos_c - pos_p)

        judder_err = abs(pos_m_shown - pos_m_ideal)
        pll_judder_sq_sum += judder_err * judder_err
        if judder_err > pll_max_judder:
            pll_max_judder = judder_err

        last_real_disp_vb_pll = actual_vb_c
        t_prev_submit_pll = t_submit

    n = len(frametimes_us)
    base_rms = math.sqrt(base_judder_sq_sum / n)
    pll_rms = math.sqrt(pll_judder_sq_sum / n)
    red_pct = ((base_rms - pll_rms) / base_rms * 100.0) if base_rms > 1e-6 else 0.0

    return SimMetrics(
        regime=regime_name,
        frames=n,
        baseline_miss_rate_pct=(base_misses / n) * 100.0,
        pll_miss_rate_pct=(pll_misses / n) * 100.0,
        baseline_rms_judder_px=base_rms,
        pll_rms_judder_px=pll_rms,
        baseline_max_judder_px=base_max_judder,
        pll_max_judder_px=pll_max_judder,
        judder_reduction_pct=red_pct,
    )


class VBlankPLLPacerTests(unittest.TestCase):
    """Test suite verifying VBlank PLL accuracy, dynamic t_interp, and C/LLVM integration."""

    def test_pll_tracking_filters_os_jitter(self) -> None:
        """PLL period and phase estimator converges to sub-millisecond accuracy under +/- 150 us jitter."""
        pll = VBlankPLLModel()
        rng = random.Random(12345)
        vb0 = 500_000.0
        max_phase_err_us = 0.0

        for k in range(1, 300):
            true_edge = vb0 + k * TRUE_VB_US
            jittery_sample = int(round(true_edge + rng.uniform(-150.0, 150.0)))
            pll.vblank_pll_update(k, jittery_sample)
            if k >= 30:
                # Query 5 ms before next VBlank edge
                query_t = int(round(true_edge + TRUE_VB_US - 5000.0))
                rem_us, vb_us = pll.vblank_to_next_edge_us(query_t)
                predicted_next_edge = query_t + rem_us
                true_next_edge = true_edge + TRUE_VB_US
                err = abs(predicted_next_edge - true_next_edge)
                if err > max_phase_err_us:
                    max_phase_err_us = err
                self.assertEqual(vb_us, 16683)

        # Phase prediction error must be well below 150 us raw jitter (< 80 us)
        self.assertLess(max_phase_err_us, 80.0)

    def test_four_frametime_regimes_miss_rate_and_judder(self) -> None:
        """Evaluate all 4 required frametime regimes and assert >60% judder reduction and 0% miss rate."""
        rng = random.Random(202610)

        # 1. Locked 30 FPS (33.37 ms +/- 0.5 ms)
        ft_locked_30 = [33366.7 + rng.uniform(-500.0, 500.0) for _ in range(240)]
        m_locked = simulate_regime("Locked 30 FPS (33.37 ms)", ft_locked_30, seed=101)

        # 2. Heavy scene 24-27 FPS (37-42 ms, 3-VBlank regime)
        ft_heavy_24_27 = [rng.uniform(37000.0, 42000.0) for _ in range(240)]
        m_heavy = simulate_regime("Heavy 24-27 FPS (37-42 ms)", ft_heavy_24_27, seed=202)

        # 3. Unlocked 38-45 FPS (22-26 ms)
        ft_unlocked_38_45 = [rng.uniform(22000.0, 26000.0) for _ in range(240)]
        m_unlocked = simulate_regime("Unlocked 38-45 FPS (22-26 ms)", ft_unlocked_38_45, seed=303)

        # 4. Sudden frametime spike (33 ms -> 55 ms -> 33 ms)
        ft_spike: list[float] = []
        for i in range(240):
            if i % 20 in (8, 9, 10):
                ft_spike.append(55000.0)
            else:
                ft_spike.append(33366.7)
        m_spike = simulate_regime("Sudden Spike (33->55->33 ms)", ft_spike, seed=404)

        all_metrics = [m_locked, m_heavy, m_unlocked, m_spike]
        for m in all_metrics:
            print(
                f"[{m.regime:28s}] "
                f"Miss: {m.baseline_miss_rate_pct:5.1f}% -> {m.pll_miss_rate_pct:4.1f}% | "
                f"RMS Judder: {m.baseline_rms_judder_px:5.2f}px -> {m.pll_rms_judder_px:5.2f}px "
                f"(Max: {m.baseline_max_judder_px:5.2f}px -> {m.pll_max_judder_px:5.2f}px, "
                f"Reduction: {m.judder_reduction_pct:5.1f}%)"
            )

        # Assert 0% VBlank miss rate under normal jitter across all regimes
        self.assertEqual(m_locked.pll_miss_rate_pct, 0.0)
        self.assertEqual(m_heavy.pll_miss_rate_pct, 0.0)
        self.assertEqual(m_unlocked.pll_miss_rate_pct, 0.0)
        self.assertEqual(m_spike.pll_miss_rate_pct, 0.0)

        # Assert > 60% reduction in display motion judder error during 24-28 FPS drops
        self.assertGreater(m_heavy.judder_reduction_pct, 60.0)
        self.assertLess(m_heavy.pll_rms_judder_px, 0.5)
        self.assertGreater(m_spike.judder_reduction_pct, 60.0)

    def test_phase8_permille_and_q16_decoding(self) -> None:
        """Verify phase8.ll decodes both permille (333..667) and Q0.16 (21845..43691) accurately."""
        # 1/3 VBlank slot ratio
        self.assertAlmostEqual(phase8_decode_t(333, is_override=True), 0.333, places=4)
        self.assertAlmostEqual(phase8_decode_t(21845, is_override=True), 1.0 / 3.0, places=4)
        # 1/2 VBlank slot ratio
        self.assertAlmostEqual(phase8_decode_t(500, is_override=True), 0.500, places=4)
        self.assertAlmostEqual(phase8_decode_t(32768, is_override=True), 0.500, places=4)
        # 2/3 VBlank slot ratio
        self.assertAlmostEqual(phase8_decode_t(667, is_override=True), 0.667, places=4)
        self.assertAlmostEqual(phase8_decode_t(43691, is_override=True), 2.0 / 3.0, places=4)

    def test_source_and_shader_constraints(self) -> None:
        """Verify classic/source/main.c, phase8.ll, and warp8.ll adhere to all hardware constraints."""
        main_src = MAIN_C.read_text(encoding="utf-8")
        for sym in (
            "#define PLL_LEAD_US  2200u",
            "#define PLL_LATCH_US 1550u",
            "vblank_pll_update",
            "pll_predict_t_interp_q16",
            "pll_commit_frame_vblank",
            "pll_adaptive_hold_us",
            "vblank_to_next_edge_us",
            "g_pll_period_q8",
            "g_pll_phi_us",
            "g_pll_t_interp_q16",
            "g_pll_t_permille",
        ):
            self.assertIn(sym, main_src, f"Missing {sym} in classic/source/main.c")

        phase8_src = PHASE8_LL.read_text(encoding="utf-8")
        self.assertIn("icmp ule i32 %tover_q16, 1000", phase8_src)
        self.assertIn("0x3F50624DE0000000", phase8_src)  # 0.001f permille scale
        self.assertIn("0x3FD5555560000000", phase8_src)  # 1/3 lower clamp
        self.assertIn("0x3FE5555560000000", phase8_src)  # 2/3 upper clamp

        # Verify warp8.ll reads t_interp from V#misc[1] written by phase8.ll
        warp8_src = (ROOT / "classic" / "shaders" / "warp8.ll").read_text(encoding="utf-8")
        self.assertIn("%tbits = extractelement <2 x i32> %misc, i32 1", warp8_src)
        self.assertIn("%t = bitcast i32 %tbits to float", warp8_src)


if __name__ == "__main__":
    unittest.main()
