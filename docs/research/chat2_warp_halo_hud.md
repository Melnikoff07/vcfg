# Task: Disocclusion Halo Reduction & Inline HUD/Subtitle Protection (`vcfg-r2-warp`)

You are working on the PS4 Frame Generation project (**VCFG**) on Windows.

## 1. MANDATORY ISOLATION & WORKSPACE SETUP (DO THIS FIRST!)
Do **NOT** modify `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` or `E:\Documents\ChatGPT\FRAMGEN-PS4\vcfg` directly.
Create your own isolated git worktree and copy `config.json` (which is gitignored and required by the build scripts):

```powershell
git -C "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" worktree add -b research/warp-halo-hud "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r2-warp" HEAD
Copy-Item "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt\config.json" "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r2-warp\config.json"
```

- Do **ALL** your work inside `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r2-warp`.
- Do **NOT** push to `origin` and do **NOT** touch PS4 FTP (`192.168.1.21`).
- **STRICT RULE:** NEVER write any game titles/names in code, comments, logs, or commit messages — use **ONLY `CUSAxxxxx` Title IDs**.

---

## 2. PROJECT LAYOUT & BUILD COMMANDS
- `classic/shaders/warp8.ll`, `classic/shaders/fgs8.ll`, `classic/shaders/mf8.ll`, `classic/shaders/gm8.ll`, `classic/shaders/me1.ll` — GCN2 (`gfx700` Sea Islands) LLVM IR compute shaders.
- `classic/gen_shaders.py` — Compiles `.ll` shaders via `E:/Documents/ps4-framegen/llvm-amd/clang64/bin/llc.exe` (`-mcpu=bonaire`) into `classic/source/shaders.h` and checks VGPR/SGPR limits and shader region size.
  - **Hardware limits:** `vgprs <= 64` per shader, and total shader region must stay `<= 0x1C000` (currently `0x17100`).
- `classic/source/main.c` — Descriptor table setup in `build_dcb_flow` (`tb[0..59]`).

### How to build shaders + plugin in your worktree:
```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg-r2-warp/classic' && ./build.sh && git checkout -- source/git_ver.h"
python "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r2-warp\tools\tests\check_classic_config.py"
```

---

## 3. RESEARCH & IMPLEMENTATION OBJECTIVES
Inspect `classic/shaders/warp8.ll` (currently `1936 words, vgprs=61 sgprs=56`) and `classic/shaders/fgs8.ll` (`2256 words, vgprs=58 sgprs=36`).

We want to improve the visual quality of synthesized `mid` frames on two fronts while keeping `vgprs <= 64`, `shader region <= 0x1C000`, and GPU time within the `~1.7 ms` budget:

1. **Asymmetric Disocclusion / Silhouette Halo Reduction in `warp8.ll`:**
   - Currently, `warp8.ll` evaluates forward/backward luma agreement between `P(x + 0.5*v)` and `C(x - 0.5*v)` across the `3x3` block neighborhood and blends `sP` and `sC` symmetrically (or falls back toward `bg3` when `emov > 0.25`).
   - At foreground/background boundaries (e.g. rotating camera around a foreground character), an occluded background pixel is valid in **ONLY `P`** (about to be covered) or **ONLY `C`** (newly uncovered). Blending `0.5*sP + 0.5*sC` or falling back to static `(P+C)*0.5` creates a silhouette halo.
   - Design and implement an asymmetric occlusion/selection weight in `warp8.ll` (using min vs max vector spread in the `3x3` neighborhood and directional luma consistency against background vs foreground vectors) so uncovered/covered boundary pixels bias toward the unoccluded frame (`sC` or `sP`) instead of ghosting.
2. **Inline Static HUD / Crosshair / Subtitle Protection in `warp8.ll`:**
   - Strengthen the static-element / HUD stroke gate so thin high-contrast static UI elements (1–3px crosshairs, subtitles, HUD icons drawn directly into the display buffer when `|P(x) - C(x)|` is near zero and local contrast is high while the `8x8` block MV `|v|` is large from background motion) stay crisp and un-warped without causing dark-spot artifacts on moving textures (`emov` gate).
3. **Offline Verification & Build:**
   - Compile with `./build.sh` (verify `vgprs <= 64` and `shader region <= 0x1C000`).
   - Write an offline CPU reference/PSNR test script in `tools/tests/` that tests your new `warp8` blending math on synthetic disocclusion + moving-background-under-static-HUD triplets and verifies improved PSNR/edge sharpness with zero dark-spot regressions.
   - Commit on branch `research/warp-halo-hud` and provide a clear summary of the math and instruction/VGPR counts.
