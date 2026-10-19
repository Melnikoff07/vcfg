# VCFG: state and next steps (handoff, 2026-10-19, v2.12)

This file is written for whoever (human or AI) continues the work. It says what exists, how to build and deploy it,
what is broken or unverified, and what to do next, in order.

## 1. What exists

| Part | Where | State |
|---|---|---|
| App (PKG) | `launcher/` | v2.11. Game cards with console titles/icons, per-game settings, defaults, overlay style, plugin tab. Installs/updates the plugins by itself on start. Lists every installed game; unverified ones are EXPERIMENTAL (off by default, classic engine in their own `plugins.ini` section only while on). |
| New engine | `plugin/` (`source/a20_copy_impl.h`) | Used for CUSA07439 only. A20/A22 runtime: one 1280×720 mid surface, flip I(arg) then N(arg). v2.11: post (AA + sharpening) on mids **and** native frames; FPS counter composited after the warp on C and at 2/3 scale on the mid; warp ignores the counter rectangle in P. Overlay bitmap is the tail of the startup allocation (`A20_OVL_BYTES`, the profile allows no later allocation). |
| Classic engine | `classic/` | v1.16 line rebuilt from source (unoptimised build, `-O2` crashed CUSA00207). Used for CUSA08519, CUSA00411, CUSA00207 and every EXPERIMENTAL title. GOT pad patch off for every title (v2.12); the pad is polled. Warp treats the overlay rectangle in P as out of frame. |
| Overlay | `overlay/` | Menu panel + corner FPS counter in the launcher design. CPU draws an 8-bit palette bitmap (baked Inter / JetBrains Mono atlases, `bake_fonts.py`) off-screen and commits only changed bytes; `osd8.ll` composites it (optional 2/3 downscale). PC preview: `overlay/preview.py`. |
| Discovery | `discovery/` | Passive logger plugin for unknown games. |

On the console (`/data/GoldHEN/plugins/`): `vcfg9.prx` (new engine), `vcfg_classic11.prx` (classic). Settings:
`/data/bb_probe/vcfg.ini` (new engine), `/data/bb_probe/config-<TITLE>.ini` (classic), `/data/bb_probe/vcfg_launcher.ini`
(app). Logs: `/data/bb_probe/probe.log` (+ `probe.prev.log`), written only while `/data/bb_probe/debug_log` exists (app: PLUGIN tab, triangle); `/data/vcfg_launcher.log`. Flight recorder (always on): `/data/bb_probe/trace-<TITLE>.txt` + `.prev.txt` (`overlay/vcfg_trace.h`). Version for all builds: `VERSION`. Bug report: app PLUGIN tab, circle → `/data/vcfg_report.txt` (+ USB stick).

## 2. Build and deploy

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1            # new engine
sh classic/build.sh                                                      # classic engine (Git Bash)
powershell -NoProfile -ExecutionPolicy Bypass -File launcher/build_pkg.ps1 -Version 2.12
```

Deploy for testing without reinstalling the PKG: upload the PRX over FTP (`192.168.1.21:2121`, anonymous) under a
**new file name** to a staging name, read it back and compare, rename, then point `plugins.ini` at it (back it up
first). The plugin file names are also in `launcher/src/main.c` (`k_sup`, `k_files`, `k_exp_prx`) and
`launcher/build_pkg.ps1`; bump them together on every release that changes a plugin.

New-engine shaders: the arrays in `plugin/source/shaders.h`, `warp720.h`, `post_cb.h` are generated. After editing IR
in `plugin/shaders/`, compile only that shader with `plugin/build_shader.py` into a temporary header and replace only
its `#define *_RSRC1/2 … *_code[] = {…};` block. Never search/replace text inside these headers.

## 3. Rules (keep them)

- Never overwrite a PRX that a running game may have loaded: always a new file name. No hot unload.
- Back up `plugins.ini` before every change. Never put an engine into `[default]`.
- Never free memory or unregister surfaces before GPU, command and scanout retirement are proven.
- No `scePadRead` detours and no GOT pad patching (off since v2.12: crash suspect on CUSA00411, CUSA00207).
- Do not break the console's role as a Remote Play host (only `CUSA*` processes are touched).
- No trademarks in repository files: games by title ID only; "PS4" only with the non-affiliation note.
- Pushes, releases and anything public only after the owner says yes. Never ask for tokens or passwords in chat.

## 4. Open issues (verify first)

1. **CUSA07439 overlay flicker.** v2.11 draws off-screen and commits only changed bytes (one GPU buffer there). Not
   yet confirmed on hardware. If it still flickers: check whether the counter's mid copy (2/3 scale, `A21_OVL_I_TB`)
   lands on the same screen position as the native copy; try drawing the counter only on C; log `vov_counter`
   redraws per second; consider a second buffer by shrinking the menu (`A20_OVL_BYTES` limit is 0x500000 total).
2. **Native-frame post in CUSA07439** (new in v2.10): watch GPU time (`A21_RUNTIME`, `PASSES`) and frame pacing; the
   1080p post costs more than the 720p one.
3. **EXPERIMENTAL titles**: only one (CUSA10249) has been switched on so far; collect what happens per title.
4. Classic counter artifacts on fast camera turns (fixed in the warp in v2.07): confirm on CUSA00207.
5. `discovery/source/main.c` has an uncommitted local change of unknown origin; review before committing.

## 4b. Tried and reverted

- FAST for games above 30 fps (2026-10-20): letting FAST show frames without a mid (M = 1) and dropping the 30 cap
  uncapped CUSA00411 to 45-52 fps with 10-25 mids/s; on a 60 Hz output this judders and flickers (uneven 1/2-vblank
  holds, mids look different from real frames). Reverted: FAST is for 20..30 fps games, QUALITY (cap 30, 2x) for
  games between 30 and 60. A smooth path for 40-55 fps would need even pacing at a fixed lower rate (e.g. 40 Hz on
  120 Hz output), which the console's 60 Hz output does not offer.
  Idea not done yet ("40/60 retiming", preset 3): pace the game at 40 and show a frame every vblank at its own
  time; per two game frames: odd frame -> only the mid at t = 2/3 (cur not shown), even frame -> mid at t = 1/3,
  then cur. Needs two mid buffers (allocated when the preset is on at game start), no pace_30, fallback to
  QUALITY below ~37 fps. Costs ~40 generated frames per second.

## 4c. Latency and CPU-bound games (measured 2026-10-20, CUSA05571)

- LATENCY log line (classic): real frame hand-over -> on screen. FG off 31-36 ms; FG on (QUALITY) 48-62 ms with up to
  4 flips queued.
- In a CPU-heavy game (~26 ms CPU per frame, 37-42 fps without FG) FG QUALITY dropped the game to 22-24 fps although
  our GPU passes cost only 3.4 ms: the classic engine paces inside the game's submit call (pace_30, mid hold sleeps),
  so the game's render thread is blocked and its next frame starts late. Holding the game until the queue drains
  (tried, reverted) made it worse.
- Tried a release-timer pacer (pace_pair, reverted): no change. The 'game cpu release->submit' counter showed 32-37 ms
  of the game's own time per frame with FG vs ~25 ms without: the game itself is slower, not blocked by us. Causes:
  our 3.4 ms of GPU work in a GPU-bound game, and each real frame holding its buffer one refresh longer (3 game
  buffers, queue up to 4), so the game waits for a free buffer. A frame then needs > 33 ms and QUALITY 2x drops to
  3 refreshes per frame (20-25 fps). Real fixes: cheaper passes (warp/post are ~2.6 of the 3.4 ms), an extra game
  buffer at registration, or not delaying the real frame (show it first, mid after: costs phase accuracy).

## 4d. CUSA05571 (2 display buffers) - where it ended (2026-10-20)

- Present copy + rings of 3 mids / 3 copies (kept): the game's own frame time fell from 37 to 20.5 ms (no more GPU
  stall in WaitFlipDone); with pace_vblank the frame is 20.5 + 16 ms -> 27 fps (was 24).
- Any faster release (timer pacing) makes the game itself wait again (36-38 ms per frame) and mids get dropped:
  the game paces on its own previous flip, and with FG the real frame reaches the screen one refresh later (after
  its mid), so the frame period stays ~36 ms. Not fixable by pacing; ideas: give the game's flip argument to the mid
  flip so its own wait ends a refresh earlier, or FAST-style 2/3-refresh pulldown for games stuck at 25-29 fps.
- HOOK timing line (classic): build / submit / hold / flip2 / pace ms per FG frame, game own time, flip queue.

## 5. Next steps, in order

1. **Confirm v2.11 on hardware** (all four verified titles): menu, counter, AA, no flicker, no artifacts around the
   counter, FPS. Write the results into `docs/COMPATIBILITY.md`.
2. **Per-title result memory for EXPERIMENTAL**: the classic plugin writes `/data/bb_probe/result-<TITLE>.ini`
   (FG ran / inert reason / pad mode / crash suspected = started but never reached N flips); the app shows it on
   the card (WORKS / INERT: reason / CRASHED) and suggests switching off titles that crashed.
3. **Safer start for unknown titles**: in the classic engine, stay passive for the first N seconds and refuse
   unknown flip forms / resolutions with a logged reason instead of trying (the log reason goes to step 2).
4. **One engine.** Port the classic engine's strengths into the new one (its post and its flip-tail handling for
   CUSA08519/CUSA00411), or the other way round: one code path for all titles. Keep CUSA07439 regression-free.
5. **Any input resolution** (720p, 900p, 1080p, and dynamic resolution) for pyramids, dispatch sizes and the warp.
6. **HUD protection per title** (`hud_<TITLE>` rectangles, already read by the warp) with an editor in the app.
7. **Overlay**: Cyrillic in the menu (bake a few more glyphs), counter positions in the app preview, game title
   in the header for EXPERIMENTAL titles.
8. **Quality**: better occlusion handling in the warp (disocclusion fill from the side that is visible), scene-cut
   detection tuning, sharpening defaults per engine.
9. **Release hygiene**: CI build on push (toolchain in a container), PKG attached to releases automatically,
   checksum in the release notes.
