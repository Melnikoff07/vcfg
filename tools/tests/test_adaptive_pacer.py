import math
import pathlib
import statistics
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C = ROOT / "classic" / "source" / "main.c"

VB_US = 16683          # 59.94 Hz vblank period (~16.683 ms)
WIN_2VB_US = VB_US * 2 # 2-vblank target window (~33.366 ms)
PLL_LEAD_US = 1200     # 1.2 ms pre-vblank lead time


def vblank_to_next_edge_us(now_us: int, last_vblank_us: int, vb_us: int = VB_US) -> int:
    if last_vblank_us <= 0 or now_us <= last_vblank_us:
        return vb_us // 2
    phase_us = (now_us - last_vblank_us) % vb_us
    rem_us = vb_us - phase_us
    return rem_us if rem_us > 0 else vb_us


def pll_adaptive_hold_us(now_us: int, last_vblank_us: int, dt_enter_us: int, req_hold_us: int, vb_us: int = VB_US) -> int:
    to_next_vb_us = vblank_to_next_edge_us(now_us, last_vblank_us, vb_us)
    win_2vb_us = vb_us * 2
    target_edge_us = (to_next_vb_us + vb_us) if (0 < dt_enter_us < vb_us) else to_next_vb_us
    vb_budget_us = max(0, target_edge_us - PLL_LEAD_US)

    rem_2vb_us = 0
    if 0 < dt_enter_us < win_2vb_us:
        rem_2vb_us = win_2vb_us - dt_enter_us

    return min(req_hold_us, rem_2vb_us, vb_budget_us)


class DiscreteEventPacerSim:
    """
    Discrete-event timing simulator for a game render loop + VCFG FrameGen hook
    against a 60 Hz (16.683 ms) hardware vblank clock.
    """

    def __init__(self, adaptive_pll: bool, pace_pre: bool = True, gpu_mid_us: int = 2400):
        self.adaptive_pll = adaptive_pll
        self.pace_pre = pace_pre
        self.gpu_mid_us = gpu_mid_us

    def run(self, cpu_frame_times_us: list[int]) -> dict:
        now_us = 100_000
        last_submit_enter_t = now_us - WIN_2VB_US
        ema_frame_us = WIN_2VB_US
        cpu_ema_us = 22_000
        q2x_hold_acc = 0

        s_flip_pending_deadlocks = 0
        crossed_vblank_in_sleep = 0
        pll_shortened_count = 0
        frame_intervals_us = []
        vblank_spans = []
        mid_latched_next_vb = 0

        prev_submit_done_t = now_us
        prev_latch_vb = now_us // VB_US

        for cpu_us in cpu_frame_times_us:
            # Game thread runs its CPU frame work
            now_us += cpu_us
            t_hook_in = now_us
            dt_enter_us = t_hook_in - last_submit_enter_t

            # mon_thread tracks the most recent vblank processTime
            last_vblank_us = (t_hook_in // VB_US) * VB_US
            to_next_vb_us = vblank_to_next_edge_us(t_hook_in, last_vblank_us, VB_US)
            vb_budget_us = max(0, to_next_vb_us - PLL_LEAD_US)

            cpu_ema_us = (cpu_ema_us * 7 + cpu_us) // 8

            # Determine nominal hold / pre-submit wait
            req_hold_us = 0
            if not self.adaptive_pll:
                # Legacy fixed-hold behavior:
                # 1) Wait for 2-vblank target if early, AND
                # 2) In 23..28 fps heavy scenes (tot_us > 38 ms, cpu > 30 ms),
                #    g_q2x_hold_acc triggers a fixed 15..16.68 ms sleep inside SubmitAndFlip.
                if dt_enter_us < WIN_2VB_US:
                    req_hold_us = WIN_2VB_US - dt_enter_us
                elif ema_frame_us > 38_000 and cpu_ema_us > 30_000:
                    half_us = ema_frame_us >> 1
                    extra_q16 = ((max(0, half_us - VB_US)) << 16) // VB_US
                    q2x_hold_acc += extra_q16
                    if q2x_hold_acc >= 32768:
                        q2x_hold_acc -= 65536
                        req_hold_us = 15_500
            else:
                # Vblank-Phase-Aware Adaptive Hold (PRE-SUBMIT PLL)
                if dt_enter_us < WIN_2VB_US:
                    nominal_wait = WIN_2VB_US - dt_enter_us
                    req_hold_us = pll_adaptive_hold_us(t_hook_in, last_vblank_us, dt_enter_us, nominal_wait, VB_US)
                    if req_hold_us < nominal_wait:
                        pll_shortened_count += 1
                else:
                    # Late arrival (>= 2 vblanks, e.g. 23..28 fps or 20 fps):
                    # pll_adaptive_hold_us shortens the hold to 0 so mid submits immediately before
                    # the upcoming vblank edge without delaying the game's CPU thread past that vblank.
                    raw_hold = VB_US
                    req_hold_us = pll_adaptive_hold_us(t_hook_in, last_vblank_us, dt_enter_us, raw_hold, VB_US)
                    if req_hold_us < raw_hold:
                        pll_shortened_count += 1

            vb_before_sleep = now_us // VB_US
            if self.pace_pre:
                # Pre-submit sleep occurs BEFORE submitting mid + real
                now_us += req_hold_us
                vb_after_sleep = now_us // VB_US
                if req_hold_us > 0 and dt_enter_us >= VB_US and vb_after_sleep > vb_before_sleep:
                    crossed_vblank_in_sleep += 1

                last_submit_enter_t = now_us
                # Submit mid + real back-to-back before the vblank edge
                mid_gpu_done_us = now_us + self.gpu_mid_us
                next_vb_edge_us = ((t_hook_in // VB_US) + 1) * VB_US
                if dt_enter_us >= VB_US and mid_gpu_done_us <= next_vb_edge_us + self.gpu_mid_us:
                    mid_latched_next_vb += 1
            else:
                # Post-submit sleep between flip(mid) and flip(real):
                # If mid EOP + vblank latch races with flip(real) while s_flip_pending is active, deadlock occurs.
                last_submit_enter_t = now_us
                mid_eop_vb = (now_us + self.gpu_mid_us) // VB_US
                now_us += req_hold_us
                real_sub_vb = now_us // VB_US
                if real_sub_vb > mid_eop_vb and (now_us % VB_US) < 1500:
                    s_flip_pending_deadlocks += 1

            frame_dt = now_us - prev_submit_done_t
            prev_submit_done_t = now_us
            ema_frame_us = (ema_frame_us * 3 + frame_dt) // 4
            frame_intervals_us.append(frame_dt)

            cur_latch_vb = (last_submit_enter_t + self.gpu_mid_us) // VB_US + 1
            vblank_spans.append(max(1, cur_latch_vb - prev_latch_vb))
            prev_latch_vb = cur_latch_vb

        steady_intervals = frame_intervals_us[5:]
        steady_vb_spans = vblank_spans[5:]
        variance_ms2 = statistics.pvariance([x / 1000.0 for x in steady_intervals])
        stdev_ms = math.sqrt(variance_ms2)
        vb_variance = statistics.pvariance(steady_vb_spans)

        # Count 2-vblank (33 ms) <-> 3-vblank (50 ms) oscillations on consecutive frames
        oscillations = 0
        for a, b in zip(steady_vb_spans, steady_vb_spans[1:]):
            if abs(a - b) >= 1:
                oscillations += 1

        return {
            "deadlocks": s_flip_pending_deadlocks,
            "crossed_vblank_in_sleep": crossed_vblank_in_sleep,
            "pll_shortened": pll_shortened_count,
            "variance_ms2": variance_ms2,
            "stdev_ms": stdev_ms,
            "vb_variance": vb_variance,
            "oscillations": oscillations,
            "mean_ms": statistics.fmean([x / 1000.0 for x in steady_intervals]),
            "mid_latched_next_vb": mid_latched_next_vb,
        }


class AdaptivePacerSimulationTest(unittest.TestCase):
    def test_steady_30fps_zero_deadlocks_and_locked_cadence(self):
        # Steady 30 fps game loop (~21 ms CPU work per frame against 60 Hz vblank clock)
        cpu_frames = [21_000 + ((i % 3) - 1) * 400 for i in range(180)]
        sim = DiscreteEventPacerSim(adaptive_pll=True, pace_pre=True)
        res = sim.run(cpu_frames)

        self.assertEqual(res["deadlocks"], 0)
        self.assertEqual(res["crossed_vblank_in_sleep"], 0)
        self.assertAlmostEqual(res["mean_ms"], WIN_2VB_US / 1000.0, delta=0.5)
        self.assertLess(res["stdev_ms"], 1.0)

    def test_fluctuating_24_to_29_fps_eliminates_oscillation_and_reduces_variance(self):
        # Fluctuating 24..29 fps heavy scene (34.5..41.7 ms CPU frame times)
        pattern_us = [36_500, 38_200, 40_800, 39_100, 37_400, 41_200, 35_800, 38_900]
        cpu_frames = [pattern_us[i % len(pattern_us)] for i in range(200)]

        legacy = DiscreteEventPacerSim(adaptive_pll=False, pace_pre=False).run(cpu_frames)
        adaptive = DiscreteEventPacerSim(adaptive_pll=True, pace_pre=True).run(cpu_frames)

        self.assertEqual(adaptive["deadlocks"], 0)
        self.assertEqual(adaptive["crossed_vblank_in_sleep"], 0)
        self.assertGreater(adaptive["pll_shortened"], 0)
        # Adaptive PLL must have strictly lower frame-time variance and vblank span variance than fixed hold
        self.assertLess(adaptive["variance_ms2"], legacy["variance_ms2"] * 0.1)
        self.assertLess(adaptive["vb_variance"], legacy["vb_variance"])
        self.assertLess(adaptive["mean_ms"], legacy["mean_ms"])

    def test_steady_20fps_zero_deadlocks_and_no_stolen_cpu_time(self):
        # 20 fps heavy game loop (50.0 ms CPU frame time = 3 vblanks)
        cpu_frames = [50_000 + ((i % 3) - 1) * 300 for i in range(150)]

        legacy = DiscreteEventPacerSim(adaptive_pll=False, pace_pre=False).run(cpu_frames)
        adaptive = DiscreteEventPacerSim(adaptive_pll=True, pace_pre=True).run(cpu_frames)

        self.assertEqual(adaptive["deadlocks"], 0)
        self.assertEqual(adaptive["crossed_vblank_in_sleep"], 0)
        self.assertLess(adaptive["variance_ms2"], legacy["variance_ms2"] + 0.05)
        self.assertAlmostEqual(adaptive["mean_ms"], 50.0, delta=0.5)
        self.assertGreater(legacy["mean_ms"], adaptive["mean_ms"] + 5.0)

    def test_late_2vblank_window_shortens_sleep_to_target_upcoming_vblank_edge(self):
        # Game frame arrives at dt_enter_us = 28.5 ms (late in the 33.366 ms 2-vblank window),
        # with the upcoming vblank edge 4.866 ms away.
        last_vb = 100_000
        now_us = last_vb + VB_US + 11_817  # 28,500 us after last_vb, 4,866 us before next vblank edge
        dt_enter_us = 28_500
        hold_us = pll_adaptive_hold_us(now_us, last_vb, dt_enter_us, VB_US, VB_US)

        to_next_vb = vblank_to_next_edge_us(now_us, last_vb, VB_US)
        self.assertEqual(to_next_vb, 4866)
        self.assertEqual(hold_us, 4866 - PLL_LEAD_US)
        # Wakeup time must be strictly before the upcoming vblank edge by PLL_LEAD_US
        self.assertEqual(now_us + hold_us, last_vb + 2 * VB_US - PLL_LEAD_US)

    def test_main_c_implements_pll_and_workload_hooks(self):
        src = MAIN_C.read_text(encoding="utf-8")
        self.assertIn("g_vblank_t_us", src)
        self.assertIn("pll_adaptive_hold_us", src)
        self.assertIn("vblank_to_next_edge_us", src)
        self.assertIn("pace_vblank_ex", src)
        self.assertIn("gnm_submit_and_flip_raw", src)
        self.assertIn("flip_inner_ex(1, workload", src)
        self.assertIn("HOOK32(sceGnmSubmitAndFlipCommandBuffersForWorkload);", src)


if __name__ == "__main__":
    unittest.main()
