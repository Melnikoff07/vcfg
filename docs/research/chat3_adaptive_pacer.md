# Task: Adaptive Vblank-Phase Pacer & Workload/Split Flip Support (`vcfg-r3-pacer`)

You are working on the PS4 Frame Generation project (**VCFG**) on Windows.

## 1. MANDATORY ISOLATION & WORKSPACE SETUP (DO THIS FIRST!)
Do **NOT** modify `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` or `E:\Documents\ChatGPT\FRAMGEN-PS4\vcfg` directly.
Create your own isolated git worktree and copy `config.json` (which is gitignored and required by the build scripts):

```powershell
git -C "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" worktree add -b research/adaptive-pacer "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r3-pacer" HEAD
Copy-Item "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt\config.json" "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r3-pacer\config.json"
```

- Do **ALL** your work inside `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r3-pacer`.
- Do **NOT** push to `origin` and do **NOT** touch PS4 FTP (`192.168.1.21`).
- **STRICT RULE:** NEVER write any game titles/names in code, comments, logs, or commit messages — use **ONLY `CUSAxxxxx` Title IDs**.

---

## 2. PROJECT LAYOUT & BUILD COMMANDS
- `classic/source/main.c` — Flip hooks (`sceGnmSubmitAndFlipCommandBuffers_hook`, `sceVideoOutSubmitFlip_hook`, `sceVideoOutGetFlipStatus_hook`, `mon_thread`, `pair_thread`, `pace_hz`, `g_q2x_hold_acc`, `g_cfg.pace_order`).
- `classic/source/two_buffer_test.h` — Virtual flip queue (`tb_*`) for 2-buffer games.

### How to build and verify in your worktree:
```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg-r3-pacer/classic' && ./build.sh && git checkout -- source/git_ver.h"
python "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r3-pacer\tools\tests\check_classic_config.py"
```

---

## 3. RESEARCH & IMPLEMENTATION OBJECTIVES
Read `classic/source/main.c` around `mon_thread`, `pair_thread`, `sceGnmSubmitAndFlipCommandBuffers_hook`, and `sceVideoOutSubmitFlip_hook` (lines 1720–3250).

Currently:
- In `PRE-SUBMIT` mode (`use_pre_pace`, default on `STANDARD` 3-buffer games when `pace_order == 0` or `1`), we submit the `mid` frame via `sceGnmSubmitCommandBuffers` + `sceVideoOutSubmitFlip(mid)`, sleep `half_frame` (`~15.5..16.6 ms`), and then call `HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers, ...)` for the real frame.
- While this eliminates `s_flip_pending` deadlocks on steady 30 fps games (`33.3 ms` frame time), if a game drops to `23..28 fps` (`36..43 ms` frame time) in a heavy scene, sleeping a full fixed `16.6 ms` inside `SubmitAndFlip` steals `16.6 ms` of CPU submission time from the game's render thread on the next frame, causing `33 ms <-> 50 ms` vblank oscillation.

Design, simulate, and implement in `classic/source/main.c`:
1. **Vblank-Phase-Aware Adaptive Hold (`PRE-SUBMIT` PLL):**
   - Use the vblank timestamp tracked by `mon_thread` (`vs.processTime` / `sceVideoOutGetVblankStatus`) and elapsed CPU frame interval (`now - last_submit_enter_t`) so that when a game frame arrives late in the 2-vblank window, the pre-submit sleep automatically shortens to target the upcoming vblank edge (guaranteeing `mid` is latched on the next vblank without delaying the game's CPU thread past that vblank).
2. **Workload Submit & Split-Thread Flip Support:**
   - Audit how `sceVideoOutSubmitFlip_hook` (the `vout` path for games that call `sceGnmSubmitCommandBuffers` + `sceVideoOutSubmitFlip` separately) and `ForWorkload` variants (`sceGnmSubmitCommandBuffersForWorkload`, `sceGnmSubmitAndFlipCommandBuffersForWorkload`) interact with `pace_order`.
   - Ensure games that use `sceGnmSubmitAndFlipCommandBuffersForWorkload` instead of `sceGnmSubmitAndFlipCommandBuffers` are hooked and routed into the FG pipeline cleanly.
3. **Discrete-Event Timing Simulation Test:**
   - Write a discrete-event timing simulation test in `tools/tests/test_adaptive_pacer.py` that simulates steady `30 fps`, fluctuating `24..29 fps`, and `20 fps` game loops against a `60 Hz` vblank clock, proving zero `s_flip_pending` deadlocks and reduced frame-time variance.
   - Build `build/classic/vcfg_classic.prx` cleanly, commit on branch `research/adaptive-pacer`, and write a clear technical summary.
