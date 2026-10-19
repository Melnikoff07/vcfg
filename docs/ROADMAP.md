# Roadmap

Each step opens a group of games. Every change is tested on CUSA07439 first (no regression), then on the
target games, with the reasons the plugin logs.

1. **Register rule and flip forms.** Find the flip NOP from the end of the last DCB (cheap for any size); support a
   flip call that contains only the flip DCB (CUSA13801, CUSA08519). Measure per title whether the game writes all 9 compute
   registers before its first dispatch after our job; enable "no restore needed" only after thousands of confirming
   calls, keep verifying, fall back at the first violation. Target: CUSA09171, CUSA13801, CUSA08519.
2. **Any input resolution.** Pyramid sizes, dispatch sizes and warp scale are fixed for a 1080p source; make them
   parametric (720p, 900p, 1080p).
3. **Low-memory mode.** Smaller work buffers; refuse cleanly where it does not fit.
4. **One plugin.** Port what the older v1.16 line does for CUSA08519/CUSA00411 (separate flip DCB, `0x68750778` tail) and
   retire it.
5. **Safe in `[default]`.** Disable the old generation path for unknown titles, embed discovery, remember the
   per-title decision (supported / refused + reason) so unknown games are never put at risk twice.
6. **Own compute queue** (`sceGnmMapComputeQueue`/`DingDong`): own register state, no dependency on the game's.
   Prototype as a separate homebrew first.
7. **HUD.** Per-title HUD protection rectangles (`hud_<TITLE>` file, already supported by the warp).
