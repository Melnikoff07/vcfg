#!/usr/bin/env python3
"""Advisor flip-queue and GPU-headroom diagnosis on real hardware traces (PS4 base, 2.35, 30/10).

The Advisor used to read the first CADENCE line of the trace (the 7 s warm-up window) and called any
"missed > 40" a FLIP PACING CONTENTION with an R2 recipe that changes the registration and pacing.
CADENCE "missed" counts flips the monitor thread did not see: on CUSA02299 the unpinned monitor saw
3-14 of 60 generated frames per 2 s while VideoOut showed all of them (FLIPLOG M1R1 and the camera).
Now:
  - a stuck queue (STALL SNAPSHOT, or a median of more than 10 pace timeouts per window) keeps the recipe;
  - real frames held past their slot in a quarter of the regime windows with our GPU passes at 6 ms or
    more is NO GPU HEADROOM: advice to lower sharpness / AA, and R2 changes nothing;
  - "missed" alone decides nothing.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_launcher_advisor_uninstall import (  # noqa: E402
    DIAG_FLIP_STALL,
    DIAG_OK,
    LauncherHarness,
    Sandbox,
)

DIAG_GPU_HEADROOM = 8
FIX = Path(__file__).resolve().parent / "fixtures" / "advisor_traces"


def _diag(h: LauncherHarness, title: str, trace: str, adapt: str | None = None):
    sb = Sandbox(h).__enter__()
    g = sb.select_game(title, experimental=True)
    sb.write_cfg(f"trace-{title}.txt", trace)
    if adapt is not None:
        sb.write_cfg(f"adapt-{title}.ini", adapt)
    h.lib.harness_refresh_dev_status(ctypes.byref(g))
    return sb, g


def test_enum_value_matches_app_h() -> None:
    app_h = (Path(__file__).resolve().parents[2] / "launcher" / "src" / "app.h").read_text(encoding="utf-8")
    line = next(l for l in app_h.splitlines() if "DIAG_NONE" in l)
    names = [n.strip() for n in line[line.index("{") + 1 : line.index("}")].split(",")]
    assert names.index("DIAG_GPU_HEADROOM") == DIAG_GPU_HEADROOM, names
    assert names.index("DIAG_FLIP_STALL") == DIAG_FLIP_STALL and names.index("DIAG_OK") == DIAG_OK, names


def test_cusa02299_no_gpu_headroom_not_contention(h: LauncherHarness) -> None:
    # 30/10, preset 2, sharpness 70: CADENCE missed 39-104 per window, pace timeouts 0, no STALL SNAPSHOT;
    # our passes 5.9-8.2 ms (rcas8+osd 3.7-4.5), real frames held 50 ms in 6 of the last 16 STAT windows.
    adapt = "; runtime auto-healer profile for CUSA02299\nadapt_ver = 234\ntwo_buffer = 1\ngv_conv = 1\n"
    sb, g = _diag(h, "CUSA02299", (FIX / "trace-CUSA02299-20261030.txt").read_text(encoding="utf-8"), adapt)
    try:
        d = g.dev
        assert d.diag_kind == DIAG_GPU_HEADROOM, (d.diag_kind, d.diag_cause)
        cause = d.diag_cause.decode()
        assert cause.startswith("NO GPU HEADROOM"), cause
        assert "6/16" in cause, cause
        assert "RCAS8" in d.diag_detail.decode(), d.diag_detail
        assert "SHARPNESS" in d.diag_advice.decode(), d.diag_advice
        assert "CONTENTION" not in cause
        # R2 changes nothing: no registration / pacing / engine change, adapt-*.ini kept.
        before = (d.manual_mode, d.shave_mode, d.reg_mode, d.pace_order, d.post_mid, d.fps_lock, d.tile_mode,
                  g.eng_sel, g.buf_sel)
        h.lib.harness_do_apply_advice(ctypes.byref(sb.app))
        after = (d.manual_mode, d.shave_mode, d.reg_mode, d.pace_order, d.post_mid, d.fps_lock, d.tile_mode,
                 g.eng_sel, g.buf_sel)
        assert before == after, (before, after)
        assert (sb.cfgdir / "adapt-CUSA02299.ini").exists(), "R2 on NO GPU HEADROOM must keep adapt-CUSA02299.ini"
        assert "NOTHING CHANGED" in sb.app.status.decode(), sb.app.status
        # The screen shows it as an issue.
        drawn = h.draw_screen(sb.app, 4, detail=1, dev_expand=0)
        assert "CRASH ADVISOR [ISSUE]" in drawn and "NO GPU HEADROOM" in drawn, drawn
    finally:
        sb.__exit__(None, None, None)


def test_first_window_alone_decides_nothing(h: LauncherHarness) -> None:
    # Only the warm-up part of the trace (first lines: missed 48-54, our passes 4.1 ms, 30.0 flips/s):
    # no stall, no drops with our GPU above 6 ms -> not FLIP PACING CONTENTION, and not GPU headroom.
    text = (FIX / "trace-CUSA02299-20261030.txt").read_text(encoding="utf-8")
    first = text[: text.index("---- last lines")]
    sb, g = _diag(h, "CUSA02299", first)
    try:
        assert g.dev.diag_kind == DIAG_OK, (g.dev.diag_kind, g.dev.diag_cause)
    finally:
        sb.__exit__(None, None, None)


def test_cpu_bound_titles_are_not_gpu_headroom(h: LauncherHarness) -> None:
    # CUSA18723 (26.6 fps median, drops 13/16, our passes 3.5 ms) and CUSA07737 (our passes 3.7 ms): the game is
    # late, but our GPU cost is small; the diagnosis must not blame our GPU passes nor the flip queue.
    for title, name in (("CUSA18723", "trace-CUSA18723-20261030.txt"), ("CUSA07737", "trace-CUSA07737-20261030.txt")):
        sb, g = _diag(h, title, (FIX / name).read_text(encoding="utf-8"))
        try:
            assert g.dev.diag_kind == DIAG_OK, (title, g.dev.diag_kind, g.dev.diag_cause)
        finally:
            sb.__exit__(None, None, None)


def test_stuck_queue_keeps_the_contention_recipe(h: LauncherHarness) -> None:
    # No full trace of a stuck queue exists in the corpus: the real STALL SNAPSHOT line from a hardware report
    # (test_runtime_autohealer.py) is appended to the real CUSA02299 trace, which otherwise is GPU headroom.
    text = (FIX / "trace-CUSA02299-20261030.txt").read_text(encoding="utf-8")
    stall = "1232.50 STALL SNAPSHOT (4s no flip at flips=149 vo=1344): curBuf=1 pend=0 gcq=0\n"
    sb, g = _diag(h, "CUSA02299", text + stall)
    try:
        assert g.dev.diag_kind == DIAG_FLIP_STALL, (g.dev.diag_kind, g.dev.diag_cause)
        assert g.dev.diag_cause.decode().startswith("FLIP PACING CONTENTION"), g.dev.diag_cause
    finally:
        sb.__exit__(None, None, None)
    # Pace timeouts in the regime windows (median > 10) are a stuck queue too; the warm-up window is not read.
    tail = text[text.index("---- last lines"):]
    timed_out = text[: text.index("---- last lines")] + tail.replace("pace timeouts 0 ", "pace timeouts 25 ")
    sb, g = _diag(h, "CUSA02299", timed_out)
    try:
        assert g.dev.diag_kind == DIAG_FLIP_STALL, (g.dev.diag_kind, g.dev.diag_cause)
        assert "(25 TO," in g.dev.diag_cause.decode(), g.dev.diag_cause
    finally:
        sb.__exit__(None, None, None)
    warm_only = text[: text.index("---- last lines")].replace("pace timeouts 0 ", "pace timeouts 25 ") + tail
    sb, g = _diag(h, "CUSA02299", warm_only)
    try:
        assert g.dev.diag_kind == DIAG_GPU_HEADROOM, (g.dev.diag_kind, g.dev.diag_cause)
    finally:
        sb.__exit__(None, None, None)


def main() -> int:
    test_enum_value_matches_app_h()
    h = LauncherHarness()
    try:
        test_cusa02299_no_gpu_headroom_not_contention(h)
        test_first_window_alone_decides_nothing(h)
        test_cpu_bound_titles_are_not_gpu_headroom(h)
        test_stuck_queue_keeps_the_contention_recipe(h)
    finally:
        h.close()
    print("OK: launcher GPU headroom checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
