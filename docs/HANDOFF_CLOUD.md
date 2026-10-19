# VCFG handoff for a cloud model (no console, no PS4 toolchain), 2026-10-20

VCFG is frame generation (30 -> 60) for games on a PS4 with GoldHEN: GoldHEN plugins (C + AMDGPU gfx700 compute
shaders in LLVM IR) and an app (PKG). You get the repository and real logs in a zip. You cannot run anything on the
console; the owner and a local model test your work. Goal of this round: **improve what exists, support more games,
debug everything, without breaking the games that already work.**

## 1. What is in the zip

| Path | What |
|---|---|
| `vcfg/` | the repository at the current commit (see `vcfg/docs/NEXT_STEPS.md` first, then `HANDOFF_LOCAL.md` for the console side) |
| `logs/console/` | from the console on 2026-10-20: `trace-<TITLE>.txt` (+ `.prev`), `probe.log`, `probe.prev.log`, `config-<TITLE>.ini`, `plugins.ini`, app log |
| `logs/witcher_sessions/` | 14 full plugin logs of one two-buffer title (CUSA05571) across the experiments of NEXT_STEPS 4c/4d |

Key code:

| File | What |
|---|---|
| `classic/source/main.c` | classic engine: hooks (`flip_inner`, `sceVideoOutRegisterBuffers` hook), DCB builders (`build_dcb_flow8`, `build_dcb_post8`), pacing (`pace_vblank`, `pace_pair`, `pace_hz`), cadence monitor (`mon_thread`), FAST/QUALITY scheduling, present copy (`g_pcopy_*`), config parser |
| `classic/source/fg8_table.h` | T#/V# builders (`make_t`, `make_v`), table and work-memory layout |
| `classic/shaders/*.ll` | passes: pyr8 (luma pyramid), me3/me2/me1 (motion search), gm8/fgs8/mf8 (filtering), phase8, warp8 (mid frame), rcas8 (AA + sharpening), osd8 (overlay compositor), pcopy8 (present copy) |
| `plugin/source/a20_copy_impl.h` | new engine (one title), separate design |
| `overlay/` | menu/counter renderer shared by both engines; `vcfg_trace.h` flight recorder |
| `launcher/src/` | the app (UI in `app.c`/`ui.c`, platform in `main.c`) |

Log line reference: `HANDOFF_LOCAL.md` section 5.

## 2. Rules for your changes

- One branch per topic (`cloud/<topic>`), small commits, each with the reason. Deliver as a git bundle or patch
  series plus a short `CLOUD_REPORT.md` (what, why, how to test, what the log must show).
- Do not change behaviour of the already working paths (3-buffer 8-bit titles CUSA00411, CUSA08519, CUSA00207 and
  the new-engine title CUSA07439) unless the change is opt-in or provably identical. Put new behaviour behind a
  condition (format, buffer count, title flag) or a config key.
- Never text-edit generated shader arrays (`plugin/source/shaders.h`, `warp720.h`, `post_cb.h`); change the `.ll`
  and say which arrays must be regenerated.
- No trademarks in repository files (games by title ID only).
- No GOT pad patching, no `scePadRead` detours, nothing that touches non-`CUSA` processes.

## 3. Tasks, in order

1. **Log analyzer -> compatibility table.** Extend `tools/analyze.py` (or a new tool) to read `trace-*.txt` /
   `probe*.log` and print per title: format, buffers, resolution, FG on/off and inert reason, real fps with and
   without FG, CADENCE quality (share of mid 1 / real 1), latency, queue depth, HOOK breakdown. Run it on
   `logs/console` and `logs/witcher_sessions`; put the output in your report. This is what the owner will use for
   every community report.
2. **Two-buffer titles** (`NEXT_STEPS.md` 4c/4d, logs in `witcher_sessions`). Study the sessions in order and the
   current code; propose and implement the most promising fix as an opt-in: e.g. flip the mid with the game's flip
   argument (so the game's own wait ends a refresh earlier) or a 2/3-refresh pulldown for games stuck at 25-29 fps.
   Explain in the report which log numbers will prove it.
3. **Any input resolution.** Classic mode 8 assumes 1080p (pyramid 960x540 / 480x270 / 240x135, warp dispatch
   240x135, 1920x1080 T#s, block grid 120x68). Design and implement parametric sizes (720p, 900p, 1080p) with the
   1080p path bit-identical to today.
4. **Latency.** With FG the flip queue reaches 4 and hand-over -> screen is ~50-55 ms (33 ms without FG). Two
   attempts that blocked the game's thread made fps worse. Propose a design that keeps <= 2 flips queued without
   blocking the game (for example submitting our second flip from a helper thread when the mid is on screen), with
   the ordering problems it must solve.
5. **Quality.** Cheaper warp/post (warp8 + rcas8 + osd8 take 2-2.8 ms of ~3 ms GPU), better disocclusion and
   fast-motion handling in `warp8.ll`. Keep the IR valid for `llc -mcpu=gfx700` (no spills, user_sgprs=2 ABI).
6. **Engine unification design** (document, no code required): what of the classic engine (post, overlay, HDR
   handling, flip tail forms, present copy) the new engine lacks and vice versa, and a step plan to one engine.

## 4. Facts that save time

- Display is 60 Hz. QUALITY = cap 30 and one mid per real frame. FAST = for games below 30 (2/3-refresh pulldown);
  FAST above 30 was tried and reverted (uneven holds judder).
- The classic engine extends the game's `sceVideoOutRegisterBuffers` with its own buffers (mids, copies) and flips
  them with `sceGnmSubmitAndFlipCommandBuffers` using the game's flip argument; GPU waits for a buffer to leave the
  screen via `sceGnmInsertWaitFlipDone` in our command buffer.
- 10-bit scanout (0x88xxxxxx) works by switching the T# data format (`g_t_dfmt` 9 vs 10); HDR10 (0x88740000) only
  re-encodes the overlay palette.
- The overlay is drawn after the warp; the warp treats the overlay rectangle in the previous frame as out of frame.
