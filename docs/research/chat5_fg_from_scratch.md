# Task: Ground-Up Re-Architecture of the VCFG Frame Generation Pipeline (`vcfg-r5-fg-clean`)

You are working on the PS4 Frame Generation project (**VCFG**) on Windows.
Your mission is an ambitious, deep overnight research & engineering task: **question the existing Mode 8 optical-flow and warp synthesis heuristics from first principles, design a clean ground-up FG architecture on CPU (`tools/bench/`), prove that it eliminates moving-object warping and screen-border distortions on real & synthetic frame triplets, and compile the winning architecture into `classic/shaders/*.ll`.**

---

## 1. MANDATORY ISOLATION & WORKSPACE SETUP (DO THIS FIRST!)
Do **NOT** modify `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` or `E:\Documents\ChatGPT\FRAMGEN-PS4\vcfg` directly.
Create your own isolated git worktree and copy `config.json` (which is gitignored and required by the build scripts):

```powershell
git -C "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" worktree add -b research/fg-from-scratch "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r5-fg-clean" HEAD
Copy-Item "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt\config.json" "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r5-fg-clean\config.json"
```

- Do **ALL** your work inside `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r5-fg-clean`.
- Do **NOT** push to `origin` and do **NOT** touch PS4 FTP (`192.168.1.21`).
- **STRICT RULE:** NEVER write any game titles/names in code, comments, logs, or commit messages — use **ONLY `CUSAxxxxx` Title IDs**.

---

## 2. PROJECT LAYOUT, HARDWARE CONSTRAINTS & BUILD COMMANDS

### Key Files
- `classic/source/main.c` — Descriptor table setup (`fill_table8_wh`, `fill_table8_ext`), work buffer layout (`WORK_SZ_8 = 0x200000` = 2 MB), and compute pass dispatch (`build_dcb_flow8`, lines 1046–1239).
- `classic/source/fg_res.h` — Dispatch grid dimensions (`FgDim`) for 720p, 900p, 1080p, 1440p, 2160p.
- `classic/shaders/*.ll` — GCN2 (`gfx700` Sea Islands / Bonaire) LLVM IR compute shaders:
  - `pyr8.ll` (3-level luma pyramid `L1` 960x540, `L2` 480x270, `L3` 240x135)
  - `me3.ll` (coarse search on `L3` 30x17)
  - `me2.ll` (intermediate search on `L2` 60x34)
  - `gm8.ll` (global camera motion vector `gm` on `L2`)
  - `fgs8.ll` (foreground / anchor layer splatting into `V#GK`)
  - `me1.ll` (fine motion search on `L1` 120x68 blocks = 16x16 full-res px)
  - `mf8.ll` (spatial MRF / ICM vector regularizer producing `V#F`)
  - `phase8.ll` (interpolation phase `t`)
  - `warp8.ll` (final pixel synthesis at phase `t`)
- `tools/bench/vcfg_motion.py`, `tools/bench/vcfg_warp.py`, `tools/bench/vcfg_ref.py`, `tools/bench/metrics.py` — 1:1 bit-accurate NumPy reference implementation and benchmark suite for Mode 8.

### Hardware Limits (Enforced by `classic/gen_shaders.py`)
1. **`vgprs <= 64`** for every shader (to maintain 4 waves/EU occupancy on PS4 GCN2).
2. **`ScratchSize == 0`** (no register spills to scratch memory allowed).
3. **`shader region <= 0x1C000`** (`0x8000 + aligned_bytes <= 0x1C000`; currently `0x17100`, leaving ~20 KB of code headroom).
4. **Total GPU time budget:** `<= 1.85 ms` per generated frame at 1080p on base PS4.

### How to Build & Verify in Your Worktree
```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg-r5-fg-clean/classic' && ./build.sh && git checkout -- source/git_ver.h"
python "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r5-fg-clean\tools\tests\check_classic_config.py"
```

---

## 3. ROOT-CAUSE DIAGNOSIS OF THE CURRENT PIPELINE

Read `classic/shaders/me1.ll`, `mf8.ll`, `fgs8.ll`, `warp8.ll`, and `tools/bench/vcfg_motion.py`, `vcfg_warp.py` carefully.
The current pipeline suffers from heavy warping distortions on moving objects and severe tearing along screen borders for four structural reasons:

1. **Grid Convention Mismatch Between `me1.ll` and `mf8.ll`:**
   - `me1.ll` (line 511) evaluates candidate vectors on the **midpoint (symmetric bilateral) grid**: `P(X + dx/2)` vs `C(X - dx/2)`.
   - `mf8.ll` (lines 424–430) shifts `%Xu = %X + %scx` (`cx >> 1`) whenever `%nfgk` is true (i.e. any block without a splatted foreground key `gk >= 1<<20`), evaluating `@bsad` on the **`C`-anchored unidirectional grid**: `P(X + cx)` vs `C(X)`!
   - Consequently, any moving object or limb not tagged by `fgs8` has its `me1` midpoint vector tested at the wrong spatial location in `mf8` and overwritten by background vectors.
2. **Silent Border Clamping in `@bsad` (`me3.ll`, `me2.ll`, `me1.ll`, `mf8.ll`):**
   - `@bsad` clamps `xp` and `xc` to `[0, wl - 8]` and `yp, yc` to `[0, hl - 1]` with **zero out-of-bounds penalty**. Near the 4 screen edges during camera rotation, out-of-bounds candidates compare clamped border strips against interior pixels, corrupting the outer 1–4 block rings (`16..64 px` around the screen).
3. **Coarse `16x16` Block Granularity & Single-Layer Midpoint Ambiguity:**
   - `V#F` is `120 x 68` (`16x16` pixels at 1080p). Moving object silhouettes cut through `16x16` blocks. Furthermore, at occlusion/disocclusion boundaries (`P` covered or `C` uncovered), symmetric midpoint SAD `|P(x + v/2) - C(x - v/2)|` fails because the background is only visible in ONE of the two frames.
4. **Over-Accumulated `warp8.ll` Heuristics (`@mlp_cw` + Static `fillc` Fallback on Moving Objects):**
   - When `emov = |P(x + t*v) - C(x - (1-t)*v)|` rises (`> 0.15..0.25`) on a moving/blurred object or occlusion boundary, `warp8.ll` blends the motion-compensated sample `mov` with the **un-warped static sample `fillc = lerp(sp, sc, tsc)` at pixel `x`** via `cw` (`cw2`, `bgw`, `dsw`, `@mlp_cw`). Blending a moving object at `x + t*v` with the static pixel at `x` tears the moving object apart ("jelly/warp distortion").

---

## 4. RESEARCH & RE-ARCHITECTURE OBJECTIVES

Design, benchmark, and implement a **clean, ground-up FG pipeline** that solves these problems fundamentally:

### Phase A: Offline Benchmark & Diagnostic Harness (`tools/tests/bench_fg_rearch.py`)
1. Build a comprehensive benchmark script in `tools/tests/bench_fg_rearch.py` that evaluates both the baseline (`vcfg_motion.py` + `vcfg_warp.py`) and your new architecture on:
   - Existing captured console triplets (if present in `tools/bench/` or `build/`) AND a rich suite of **synthetic ground-truth 1080p/540p sequences** covering the exact failure modes:
     1. Fast camera pan/rotation (`24..64 px/frame`) with textured borders entering/leaving all 4 edges of the frame.
     2. Articulated moving foreground object (character + swinging limb/weapon) crossing a textured moving background.
     3. Third-person character rotating slower than the camera pan (`|V_obj| < |V_cam|`) with fine edge detail.
     4. Moving background under thin static HUD crosshairs/subtitles.
2. Measure separate quantitative metrics for:
   - **Global PSNR / SSIM**
   - **Screen-Border Ring PSNR** (outermost 48 px on all 4 sides)
   - **Moving-Object Mask PSNR & Edge Tearing Error**
   - **Static HUD Preservation PSNR**

### Phase B: Clean Motion Estimation & Synthesis Architecture
Explore and implement the best-performing design that fits within the PS4 `gfx700` budget:
1. **Consistent Coordinate Grid + Sub-Block `8x8` Precision:**
   - Eliminate the midpoint-vs-cur grid mismatch between `me1.ll` and `mf8.ll`.
   - Investigate either:
     - Upgrading `me1`/`mf8` or adding a lightweight `8x8` (`240x135`)hierarchical refinement / split-quad pass using the already-allocated `2 MB` `g_work` buffer (`WORK_SZ_8`), OR
     - Computing per-`8x8`-subblock best vector selection so moving object boundaries are resolved at `8x8` (or pixel-adaptive) resolution instead of `16x16` blocks.
2. **Border-Aware Motion Estimation & Camera Vector Extrapolation:**
   - Fix `@bsad` / candidate scoring in `me3.ll`, `me2.ll`, `me1.ll`, `mf8.ll` so out-of-bounds block taps are detected (`xp0 < 0 || xp0 > wl - 8 || yp0 < 0 || yp0 > hl - 8`).
   - When a block near the screen edge cannot evaluate one side of a motion vector because it leaves the screen, use unidirectional matching on the valid side + strong regularization toward the global camera vector `gm` (`gm8`) and inward interior neighbors so the border ring smoothly continues the camera/scene motion without clamping artifacts.
3. **Clean Occlusion-Aware Warp Synthesis (`warp8.ll`) From Scratch:**
   - Replace the tangled `@side_lerp` + `@mlp_cw` + `bg3` + `dsw` + `fillc` stack with a clean, physically grounded synthesis model:
     - Evaluate candidate vectors from the local neighborhood (including foreground layer `V#GK` / max-velocity candidate and background/camera min-velocity candidate).
     - **Explicit Uncovered / Covered Selection:** Compare forward-warped `a = P(x + t*v)` and backward-warped `b = C(x - (1-t)*v)` against local spatial/temporal structure and vector divergence ($\nabla \cdot \mathbf{v}$). When a pixel is disoccluded (`emov` is high because background is visible in only `P` or only `C`), select the **unoccluded motion-compensated sample (`a` or `b`)** instead of falling back to unwarped static `fillc = lerp(sp, sc, t)`!
     - **Strict Static-HUD Gate:** Only blend toward static `lerp(sp, sc, t)` when `|sp - sc|` is tiny AND the pixel has a sharp local HUD stroke contrast that is NOT explained by the moving texture (`a` or `b`), preventing any static ghosting on moving objects.
     - **Clean Screen-Edge OOB Handling:** When `up` is out-of-bounds (`oobp`) and `uc` is in-bounds, output `b = C(uc, vc)` cleanly (and vice versa when `oobc` is true) without artificial `0.1` error distortion or static border blending.

### Phase C: Port to `classic/shaders/*.ll`, Validate & Commit
1. Port your winning motion estimation and warp synthesis architecture into `classic/shaders/*.ll` (and update `tools/bench/vcfg_motion.py` / `vcfg_warp.py` if needed, or keep a clean reference implementation).
2. Run `./build.sh` in `classic/` and verify:
   - Zero build errors or warnings.
   - `vgprs <= 64` and ` scratch = 0` on every shader.
   - `shader region <= 0x1C000`.
3. Run `tools/tests/check_classic_config.py` and your new `tools/tests/bench_fg_rearch.py` benchmark.
4. Commit all work cleanly on branch `research/fg-from-scratch` and write a detailed report (`RESEARCH_REPORT.md` in the worktree root) with before/after metrics, shader resource counts, and architectural diagrams.
