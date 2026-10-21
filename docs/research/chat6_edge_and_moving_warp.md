# Task: Eliminate Screen-Edge Border Warping & Moving-Object Jelly Distortion (`vcfg-r6-edge-obj`)

You are working on the PS4 Frame Generation project (**VCFG**) on Windows.
Your objective is to fix the exact mathematical and shader bugs in `me3.ll`, `me2.ll`, `me1.ll`, `mf8.ll`, and `warp8.ll` that cause:
1. **Heavy warping/shearing rings along the 4 screen borders** during camera pans/turns.
2. **Jelly/tearing distortions on moving objects** caused by the coordinate-grid mismatch between `me1.ll` and `mf8.ll` and by `warp8.ll` pulling moving objects back toward unwarped static `fillc` pixels.

---

## 1. MANDATORY ISOLATION & WORKSPACE SETUP (DO THIS FIRST!)
Do **NOT** modify `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` or `E:\Documents\ChatGPT\FRAMGEN-PS4\vcfg` directly.
Create your own isolated git worktree and copy `config.json`:

```powershell
git -C "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" worktree add -b research/edge-and-moving-warp "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r6-edge-obj" HEAD
Copy-Item "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt\config.json" "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r6-edge-obj\config.json"
```

- Do **ALL** your work inside `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r6-edge-obj`.
- Do **NOT** push to `origin` and do **NOT** touch PS4 FTP (`192.168.1.21`).
- **STRICT RULE:** NEVER write any game titles/names in code, comments, logs, or commit messages — use **ONLY `CUSAxxxxx` Title IDs**.

---

## 2. PROJECT LAYOUT & BUILD COMMANDS
- `classic/shaders/me3.ll`, `me2.ll`, `gm8.ll`, `fgs8.ll`, `me1.ll`, `mf8.ll`, `warp8.ll`, `warp8g.ll` — GCN2 (`gfx700`) compute shaders.
- `tools/bench/vcfg_motion.py`, `tools/bench/vcfg_warp.py`, `tools/bench/vcfg_ref.py`, `tools/bench/metrics.py` — 1:1 NumPy port of Mode 8 shaders.
- **Hardware limits:** `vgprs <= 64` per shader, `scratch == 0`, total shader region `<= 0x1C000`.

### How to Build & Verify in Your Worktree
```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg-r6-edge-obj/classic' && ./build.sh && git checkout -- source/git_ver.h"
python "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r6-edge-obj\tools\tests\check_classic_config.py"
```

---

## 3. SPECIFIC BUGS TO FIX & VERIFY

### Bug 1: `mf8.ll` Coordinate Grid Mismatch vs `me1.ll` (Lines 424–430 of `mf8.ll`)
Inspect `classic/shaders/me1.ll` (line 511) and `classic/shaders/mf8.ll` (lines 424–430):
- `me1.ll` computes the motion vector field `V#FT` on the **midpoint grid**: `@bsad(X, Y, cdx, cdy)` compares `P(X + ceil(d/2))` with `C(X - floor(d/2))`.
- In `mf8.ll`:
  ```llvm
  %hcx = ashr i32 %cx, 1
  %hcy = ashr i32 %cy, 1
  %scx = select i1 %nfgk, i32 %hcx, i32 0
  %scy = select i1 %nfgk, i32 %hcy, i32 0
  %Xu = add i32 %X, %scx
  %Yu = add i32 %Y, %scy
  %sad = call i32 @bsad(<4 x i32> %vp, <4 x i32> %vc, i32 960, i32 540, i32 %Xu, i32 %Yu, i32 %cx, i32 %cy)
  ```
  When `%nfgk` is true (any block without an explicit `fgs8` foreground splat key `gk >= 1<<20`), `mf8.ll` shifts `(X, Y)` by `+(cx/2, cy/2)`, turning `@bsad` into a **`C`-grid unidirectional SAD (`P(X + cx)` vs `C(X)`)**!
- On moving objects (or limbs/medium-speed objects where `fgs8` did not splat a key), the object is at `X` at `t = 0.5` (where `me1` found it), NOT at `X` in `C`! So `mf8` measures a huge SAD for the true vector `slot 0` and overwrites the moving object's vector with a neighbor's background vector, or conversely spreads a neighbor's vector onto the wrong grid position.
- **Task:** Analyze and fix the grid alignment in `mf8.ll` (and `vcfg_motion.py`) so moving objects on the midpoint grid are evaluated consistently without breaking static-head anchor protection.

### Bug 2: Silent Screen-Border Clamping in `@bsad` (`me3.ll`, `me2.ll`, `me1.ll`, `mf8.ll`)
Inspect `@bsad` in `me3.ll`, `me2.ll`, `me1.ll`, and `mf8.ll`:
- In `@bsad`, `xp` and `xc` are clamped to `[0, wl - 8]`, and `yp0..7`, `yc0..7` are clamped to `[0, hl - 1]` with **zero penalty** when the unclamped block coordinates `xp0` or `xc0` (or `ybp`, `ybc`) lie outside `[0, wl - 8] x [0, hl - 8]`.
- During a camera pan/turn (e.g. `gm = (32, 0)`), border blocks testing the true camera vector have one endpoint outside the frame; clamping squashes the 8x8 block against the screen edge, producing garbage high SADs for the true vector and low false SADs for `(0, 0)` or half-clamped vectors.
- **Task:**
  1. Detect out-of-bounds block taps in `me2.ll`, `me1.ll`, and `mf8.ll` when `xp0 < 0 || xp0 > wl - 8 || xc0 < 0 || xc0 > wl - 8 || ybp < 0 || ybp > hl - 8 || ybc < 0 || ybc > hl - 8`.
  2. When a block is in the border zone and a candidate vector pushes one endpoint out of bounds, avoid false zero-vector collapse: penalize non-supported OOB candidates while allowing the global camera vector (`gm` from `gm8`) and inward interior neighbor vectors to propagate cleanly to the screen edge.

### Bug 3: Screen-Border OOB & Moving-Object Static Pullback in `warp8.ll` (and `warp8g.ll`)
Inspect `@taps`, `@tapsq`, and `merge:` in `classic/shaders/warp8.ll`:
1. **Border OOB (`%oobp`, `%oobc`):**
   - Currently, when `%one = or i1 %onlyp, %onlyc` is true (one tap is outside `[0, 1920] x [0, 1080]`), `@taps` copies the in-bounds tap to the out-of-bounds tap (`%a2 = %b2`) and sets a fixed error `%eb = 0.1`.
   - Worse, in `merge:`, `cw` (`fillc = lerp(sp, sc, tsc)`) still blends the border pixel toward the unwarped static `sp`/`sc` when `emov` or `mlp_cw` triggers, creating a ghosted/sheared border frame!
   - Furthermore, check the OOB threshold in `@taps` / `@tapsq`: `up < 0.0 || up > 1920.0` uses `0.0` and `1920.0`, whereas clamp-to-edge bilinear texel centers start at `0.5` and end at `1919.5` (and within `2.0 px` of the edge, Catmull-Rom 5-tap `@cr` samples clamped edge texels!).
   - **Task:** Refine OOB detection near the screen border (including Catmull-Rom margin), smoothly transition to single-sided warping (`b` when `P` is OOB, `a` when `C` is OOB), and **suppress static `fillc` / `ws` fallback (`cw = 0, ws = 0`) when a pixel is in a valid camera-pan border disocclusion zone** (`onlyp` or `onlyc`).
2. **Moving-Object Jelly / Tearing (`cw` blending with unwarped `fillc`):**
   - In `merge:` (lines 940–981 of `warp8.ll`), when `emov > 0.25`, `cw` (`max(cw2, bgw, dsw) + dcw`) blends `mov` (warped at `x + t*v`) toward `fillc = lerp(sp, sc, tsc)` (unwarped at `x`!).
   - When a moving object (`hasfg` or high local velocity `vmg > 6.0` where all 4 surrounding blocks are moving, i.e. `vmn > 2.0`) has moderate `emov` due to rotation, animation, or motion blur, blending `mov` with unwarped `fillc` tears the moving object between its warped position and its static `P`/`C` position!
   - **Task:** Gate `cw` (`cw2`, `bgw`, `dsw`, `mlp_cw`) so that on interior moving objects (where surrounding blocks move coherently or `hasfg` is active, and `es = |sp - sc|` is large), disagreement between `a2` and `b2` resolves by biasing toward the **more reliable motion-compensated tap (`a2` or `b2`)** in `@side_lerp` rather than blending with unwarped static `fillc`!

---

## 4. VERIFICATION & DELIVERABLES
1. Create a standalone test & benchmark script `tools/tests/test_edge_and_moving_warp.py` that:
   - Simulates `me3 -> me2 -> gm8 -> fgs8 -> me1 -> mf8 -> warp8` before and after your fixes on synthetic panning-border and moving-object sequences.
   - Verifies a significant reduction in Screen-Border Ring error and Moving-Object Warp error with zero regression on static HUD protection.
2. Compile all shaders and the plugin via `./build.sh` in `classic/` (`vgprs <= 64`, `scratch == 0`, `shader region <= 0x1C000`).
3. Run `tools/tests/check_classic_config.py` and `tools/tests/test_edge_and_moving_warp.py`.
4. Commit your changes on branch `research/edge-and-moving-warp` and summarize the exact mathematical changes, PSNR gains, and shader VGPR/word counts.
