# Task: Gradient/Structure-Robust Optical Flow Pyramid & Multi-Layer Object Segmentation (`vcfg-r7-flow-pyr`)

You are working on the PS4 Frame Generation project (**VCFG**) on Windows.
Your mission is to upgrade the **Motion Estimation Front-End (`pyr8.ll`, `me3.ll`, `me2.ll`, `gm8.ll`, `fgs8.ll`, `me1.ll`)** so that fast-moving objects, articulated character limbs, dark textures under motion blur, and third-person characters rotating against camera pans receive accurate, rock-solid motion vectors.

---

## 1. MANDATORY ISOLATION & WORKSPACE SETUP (DO THIS FIRST!)
Do **NOT** modify `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` or `E:\Documents\ChatGPT\FRAMGEN-PS4\vcfg` directly.
Create your own isolated git worktree and copy `config.json`:

```powershell
git -C "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" worktree add -b research/optical-flow-pyramid "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r7-flow-pyr" HEAD
Copy-Item "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt\config.json" "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r7-flow-pyr\config.json"
```

- Do **ALL** your work inside `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r7-flow-pyr`.
- Do **NOT** push to `origin` and do **NOT** touch PS4 FTP (`192.168.1.21`).
- **STRICT RULE:** NEVER write any game titles/names in code, comments, logs, or commit messages — use **ONLY `CUSAxxxxx` Title IDs**.

---

## 2. PROJECT LAYOUT & BUILD COMMANDS
- `classic/shaders/pyr8.ll`, `me3.ll`, `me2.ll`, `gm8.ll`, `fgs8.ll`, `me1.ll`, `mf8.ll` — GCN2 (`gfx700`) LLVM IR compute shaders.
- `tools/bench/vcfg_motion.py`, `tools/bench/vcfg_ref.py`, `tools/bench/metrics.py` — 1:1 NumPy port of Mode 8 motion estimation.
- **Hardware limits:** `vgprs <= 64` per shader, `scratch == 0`, total shader region `<= 0x1C000`.

### How to Build & Verify in Your Worktree
```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg-r7-flow-pyr/classic' && ./build.sh && git checkout -- source/git_ver.h"
python "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r7-flow-pyr\tools\tests\check_classic_config.py"
```

---

## 3. RESEARCH & IMPLEMENTATION OBJECTIVES

Read `classic/shaders/pyr8.ll`, `me3.ll`, `me2.ll`, `gm8.ll`, `fgs8.ll`, `me1.ll` and `tools/bench/vcfg_motion.py`.
Currently, the motion estimation front-end loses track of moving objects in three critical scenarios:

### 1. Articulated Limbs & Objects Moving Slower Than Camera Pan in `fgs8.ll`
Inspect `classic/shaders/fgs8.ll` (lines 418–485):
- Currently, `fgs8.ll` classifies an `L2` block (`32x32` full-res px) into only two rigid buckets:
  1. **Foreground (`isfg`):** requires `|V2|^2 > |gm|^2 + 4` (`mov = icmp ugt i32 %vv, %movt`) — i.e. the object MUST move strictly faster in screen space than the camera pan `gm`!
  2. **Anchor (`isan`):** requires `4 * |V2|^2 <= |gm|^2` (`slw = icmp ule i32 %vv4, %gmv`) AND 2 consecutive frames (`hn >= 2`), and then in `me1.ll` / `mf8.ll` applies a brutal escape penalty `apen = 512` to ANY candidate farther than `3 L1 px` from the anchor's `L2` vector!
- **What goes wrong:**
  - Look at the dead zone between `|V2| <= 0.5 * |gm|` (anchor) and `|V2| > |gm|` (foreground): any object or limb moving at `0.5 * |gm| < |V2| <= |gm|` (for example, a character running or swinging an arm while the camera pans, or parallax midground objects) gets **NEITHER a foreground key NOR an anchor key**!
  - Worse, for a third-person character whose torso is an anchor (`|V2| <= 0.5 * |gm|`), their **moving arms, legs, cloak, or weapon** have local `L1` motion that deviates by `> 3 L1 px` from the coarse `32x32` `L2` anchor vector — and `me1.ll` / `mf8.ll` punishes that true limb motion with `apen = 512`, forcing the limbs to freeze at the torso's `L2` vector and causing severe limb/cloak warping!
- **Task:**
  - Redesign the foreground/anchor classification and splatting in `fgs8.ll`, `me1.ll`, and `mf8.ll`:
    - Close the `0.5 * |gm| .. 1.0 * |gm|` dead zone so any well-matched, textured, spatially coherent object deviating from `gm` (`|V2 - gm|^2 > devt`) is splatted as a valid candidate layer into `V#GK`.
    - Replace the rigid `apen = 512` anchor lock in `me1.ll` / `mf8.ll` with a **camera-escape guard** that specifically penalizes jumping onto the *background/camera vector `gm`* (or low-texture sky) while allowing articulated `L1` limb motion around the character's velocity!

### 2. Illumination / Motion-Blur Robustness in `me1.ll` & `me2.ll`
Inspect `@bsad` and phase 1/2/3 candidate scoring in `me1.ll` and `me2.ll`:
- Pure 8x8 `u8` luma SAD (`v_sad_u8`) is sensitive to mean brightness shifts (specular highlights, dynamic shadows, motion blur on fast-moving objects).
- In `me1.ll` (lines 514–554), a triangle-inequality mean-luma penalty (`p1p`) was added against the block center's static luma `sum0` (`|sum1/2 - sum0| - zsad0`), which works for static objects but can penalize legitimate fast-moving objects when `zsad0` happens to be moderate!
- Instead, within `@bsad`, we already compute `qp0_r, qp1_r` (the 8 bytes of `P` in row `r`) and `qc0_r, qc1_r` (the 8 bytes of `C` in row `r`).
- **Task:**
  - Compute the **DC-removed (mean-normalized) SAD** or **vertical/horizontal row-difference structure penalty** directly from the `P` and `C` block sums (`sum_P` vs `sum_C` separately, since `@bsad` can accumulate `sum_P` and `sum_C` in 2 integers instead of mixing them into `md7 = sum_P + sum_C`!).
  - Notice that in `me1.ll` lines 110–113:
    `ma0..mb7` accumulates `sum_P`, and `mc0..md7` adds `sum_C` ON TOP of `sum_P`!
    If instead you accumulate `sum_P` and `sum_C` separately (same number of `v_sad_u8` instructions!), then:
    - `|sum_P - sum_C|` is the exact **cross-frame block DC mismatch** between the two motion-compensated endpoints `P(X + d/2)` and `C(X - d/2)`!
    - `(sum_P + sum_C) >> 1` is still available as `sum1`!
    - A true match on a moving object has `sum_P ≈ sum_C` (`|sum_P - sum_C|` small), whereas a false match jumping onto a different surface or across an occlusion edge has a large `|sum_P - sum_C|`!

### 3. Sub-Pixel Refinement & Aliasing Prevention in `me2.ll` / `me1.ll`
- Audit the phase 2 (`+-1` / `+-2`) and phase 3 (`@equi`) refinement in `me2.ll` and `me1.ll`.
- Ensure that `me1.ll` phase 1 tests a rich enough set of spatial/temporal/layer candidates (all 16 lanes of a 4-block wave are available, and currently lanes `10..15` in phase 1 of `me1.ll` are completely idle!).
- **Look at `me1.ll` lines 497–510:**
  - Lanes `0..3`: 4 nearest `L2` vectors.
  - Lanes `4..6`: 3 temporal predictors (`bx`, `bx+1`, `by+1`).
  - Lane `7`: zero vector `(0, 0)`.
  - Lane `8`: foreground splat vector `gdx, gdy`.
  - Lane `9`: camera vector `gmx2, gmy2`.
  - **Lanes `10..15` (6 lanes per block!) are UNUSED (`val1` is false for `sub >= 10`)!**
  - Since those 6 lanes execute `@bsad` anyway in SIMD lockstep with lanes `0..9`, utilizing lanes `10..15` costs **ZERO extra GPU memory passes or wave instructions**!
  - You can use lanes `10..15` for:
    - Left/upper temporal predictors (`bx-1`, `by-1`) so objects moving right/down are predicted symmetrical to left/up!
    - Outer ring `L2` foreground/neighbor vectors or half-velocity `(gdx/2, gdy/2)` / `(gmx, gmy)` parallax candidates!

---

## 4. VERIFICATION & DELIVERABLES
1. Create a standalone benchmark script `tools/tests/test_optical_flow_pyramid.py` that tests `vcfg_motion.py` + `vcfg_warp.py` before and after your changes on:
   - Articulated moving objects with camera pan (`0.5 * |gm| <= |V_obj| <= |gm|` and swinging limbs on an anchor body).
   - Fast motion in all 4 directions (verifying symmetric temporal prediction using lanes `10..15`).
   - Illumination / specular variation on moving textures.
2. Port all improvements to `classic/shaders/fgs8.ll`, `me1.ll`, `mf8.ll` (and `me2.ll` if applicable) and update `tools/bench/vcfg_motion.py` to match.
3. Build via `./build.sh` in `classic/` and verify `vgprs <= 64`, `scratch == 0`, and `shader region <= 0x1C000`.
4. Run `tools/tests/check_classic_config.py` and `tools/tests/test_optical_flow_pyramid.py`, commit on branch `research/optical-flow-pyramid`, and output a concise summary of End-Point Error (EPE) and PSNR improvements.
