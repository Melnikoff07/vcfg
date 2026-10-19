# VCFG benchmark: measure and tune frame generation by numbers

Goal: an objective score for every change to the motion search and the warp, and automatic tuning of the
algorithm's parameters (thresholds, weights, radii) against ground truth. VCFG is not a neural network; "training"
here means parameter search first, and maybe a small learned refinement later if it fits ~1-2 ms of PS4 GPU.

## 1. Ground truth

Games that run at a real **60 fps** give ground truth for 30 -> 60: frames N-1 and N+1 are the inputs (as if the
game ran at 30), frame N is the correct answer for the generated frame.

A clip = consecutive triplets `(A, GT, B)`; store as lossless PNG (or the raw RGBA dumps, see section 3):

```
bench/clips/<TITLE>_<scene>/
    0000_a.png 0000_gt.png 0000_b.png
    0001_a.png ...
    hud_mask.png          optional: white = HUD (static overlay), black = scene
```

Scenes to cover: slow pan, fast pan, character in front of a moving background (disocclusion), particles / foliage,
HUD heavy (minimap, subtitles), camera cut, fade.

## 2. Metrics (`tools/bench/metrics.py`)

Per generated frame M, against GT and the inputs A, B (all RGB in 0..1):

| Metric | Definition | Better |
|---|---|---|
| PSNR | 10 log10(1 / MSE(M, GT)) | higher |
| SSIM | mean SSIM of luma (11x11 Gaussian, sigma 1.5, standard C1/C2) | higher |
| LPIPS | AlexNet LPIPS (only if `torch` + `lpips` are installed) | lower |
| PSNR HUD | PSNR inside `hud_mask` | higher |
| PSNR static | PSNR where A and B are equal (max channel diff < 1/255): pixels that must not change | higher |
| HUD flicker | mean abs(M - GT) inside `hud_mask`, x 100 (or inside the static mask if there is no HUD mask) | lower |
| TCE | (mean abs(abs(M-A) - abs(GT-A)) + mean abs(abs(B-M) - abs(B-GT))) x 100: how far the steps A->M->B differ from the true A->GT->B | lower |
| cross-fade baseline | the same metrics for M = (A + B) / 2 | reference |

`tools/bench/metrics.py <clip_dir> <method_dir> [<method_dir> ...]` prints one table (and CSV with `--csv`), one row
per method, averaged over all triplets; a method dir holds `0000_mid.png`, `0001_mid.png`, ...

## 3. Capturing clips on the console (local model + owner)

The classic engine already has a lossless dump (`inject` file flag `D`/`d`, `dump8.ll`: prev / cur / mid, left half
960x1080 RGBA8, plus the motion field). Extend it to a **bench capture mode** (separate test build, separate file
name, mapped to one 60 fps title only):

1. Do not run FG on screen. For frames N-1, N, N+1 of the game: copy N-1 and N+1 as A and B, N as GT, into
   `g_dump_mem` (full 1920x1080 if memory allows, else the left half as today), every K-th frame, M triplets.
2. Run the normal FG passes with P = A, C = B into a mid buffer that is never flipped, and dump the result as the
   VCFG output of that triplet (so the console's real shaders produce the `vcfg_classic` method dir).
3. Write `bench_<n>_{a,gt,b,mid}.rgba` + a JSON with size, format, tiling, title, frame numbers; a PC script
   (`tools/bench/fetch.py`) pulls them over FTP and converts to the layout of section 1.

10-bit titles: dump through the same T# (the passes read floats), store 16-bit PNG.

### Local capture-v1

Implemented locally in `classic/source/bench_capture.h`, with a separate export shader in
`classic/bench/shaders/pack16.ll`. This is an experimental capture build, not a production FG mode.
Hardware validation and the first real-game score are pending; see [NIGHT_REPORT.md](NIGHT_REPORT.md).

Build from Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File classic/build.ps1 -Bench
$py = (Get-Content config.json -Raw | ConvertFrom-Json).python
& $py -I tools/bench/check_capture.py
& $py -I tools/tests/check_classic_builds.py
```

Output: `build/classic-bench/vcfg_classic.prx`. It cannot be combined with `-Test 1..3`.
Normal builds do not include this export kernel or capture state machine. Source shaders for motion/warp are unchanged.

First hardware run, **one title only: CUSA00557**:

1. Capture a fresh `tools/console.py snapshot` and record the live CUSA00557 plugin line and its profile.
   Use the game's native 60 fps mode. Set the VCFG profile to `mode=8`, `preset=0`, `game_vectors=0`.
   Keep sharpness, Enhance AA and HUD protection fixed across all scenes; record a second dataset under a new scene
   name if those settings change. The capture build freezes this profile at startup and draws no VCFG OSD.
2. Create `/data/bb_probe/bench_capture` with text `240 6 960` (K, M, requested width).
   If that marker already exists, back it up before replacing it. The marker affects only this capture build.
   `240 6 1920` requests the full width, with fallback to the left half if export allocation fails.
3. Upload/map the PRX under a new name with the existing helper, which stages/readbacks PRX and backs up the INI:

   ```powershell
   & $py -I tools/console.py set CUSA00557 build/classic-bench/vcfg_classic.prx --name vcfg_classic_bench_n20261020a.prx
   ```

   If the name exists, choose a new one; do not use `--reuse`. Do not map a second title or `[default]`.
4. Fully start the game. During one scene, wait for six accepted samples; nominally about 24 seconds after native
   60 fps settles, plus disk IO and rejected triples. A one-minute scene is the initial target, not a completion guarantee.
   `BENCH saved 6/6` confirms completion. `STAT gnm` should stay near 60 in the measured scene; `BENCH STOP` means
   the capture is unusable until its cause is addressed. Do not interpret an empty run as zero error.
5. Fetch **before** changing scenes. The log prints a unique run directory. List/fetch commands:

   ```powershell
   & $py -I tools/bench/fetch.py --list
   & $py -I tools/bench/fetch.py --remote /data/bb_probe/bench_CUSA00557_<16_hex_run_id> --scene slow_pan
   & $py -I tools/bench/metrics.py bench/clips/CUSA00557_slow_pan bench/vcfg_classic/CUSA00557_slow_pan --crossfade --csv bench/slow_pan.csv
   ```

   Substitute the actual run ID; each scene name must be new. To retry conversion without another FTP read:
   `fetch.py --local-raw bench/raw/<run_directory> --scene slow_pan_retry`.
6. Exit fully and repeat the start for each scene below. A new process gets a new run directory. After capture,
   back up the live INI again, restore only the original CUSA00557 line, and restore its profile/previous marker.
   Move any new unused marker to quarantine rather than deleting it. Do not save in the old launcher between
   mappings: it rewrites plugin names. No PKG is required.

| Scene label | About one minute of action | What it measures |
|---|---|---|
| `slow_pan` | steady slow camera rotation, no menus | clean motion and static detail |
| `fast_pan` | repeated quick camera turns | large motion and ghosting |
| `disocclusion` | character/foreground object over moving background | occlusion edges and newly visible pixels |
| `particles` | particles or foliage while moving | small independent motion and unstable detail |
| `hud_heavy` | HUD/minimap/subtitles visible during camera movement | HUD error and flicker; create an aligned `hud_mask.png` |
| `camera_cut` | repeat a reproducible camera cut | scene-change behavior; only uniform-time triples are retained |
| `fade` | repeat a fade/transition | fade detection and static/HUD preservation |

CUSA09171 is a later, separate HDR-code-value dataset. Do not combine its scores with an SDR clip.
The first run must confirm native 60 fps: allowlisted title IDs alone do not establish the current scene's rate.

Capture details and limits:

- A and mid are full-size tiled offscreen surfaces; GT/B export reads native game surfaces before the one original flip.
  The regular passes run with A's pyramid prepared at parity 0 and B at parity 1, explicit phase 0.5.
  Post/AA targets only the offscreen mid. No scratch surface is registered or flipped.
- Export uses rounded normalized channel values from the same texture descriptor format as the engine and writes
  RGBA16LE. PNG conversion preserves all 16 bits, including for RGB/RGBA files that Pillow would otherwise reduce.
  HDR scores are over encoded code values, not display-light/perceptual HDR distances.
- Each capture clears temporal motion history: metadata is `history=cold`. This deliberately sparse baseline does
  **not** measure warmed temporal predictions. A warm-history capture extension would need separate attribution.
- Three private 8 KiB command buffers and separate descriptor tables are held until GPU completion and IO acknowledgement.
  On timeout/error they are retained and not reused. A JSON commit marker is written only after all four raw files.
  Fetch ignores `.stage` files and refuses wrong sizes, mixed source/profile/geometry and invalid frame/timing metadata.
- Capture memory is two 8 MiB surfaces plus four export planes (~63 MiB full width or ~32 MiB half width), before
  the small command tables and normal work memory. Six raw triplets take ~380 MiB full width or ~190 MiB half width.
  Capture overhead can disturb the game; such triples are filtered, not scored as ground truth.
- Timing is a CPU-submit proxy: each interval 15–18.5 ms, pair 32–35 ms, interval difference at most 1.5 ms.
  This filter supplements native-mode/frame-rate verification; it does not recover exact scanout timestamps.
- The raw run, PNGs, manifest and metric CSV stay inside ignored `bench/`. A result method must contain every
  captured mid at matching dimensions. Missing mids are errors, not silently skipped difficult samples.

Local verification (configured Python includes NumPy/Pillow):

```powershell
& $py -I -m unittest discover -s tools/tests -p 'test_*.py' -v
& $py -I tools/bench/selftest.py bench/selftest
```

Synthetic selftest numbers validate the metric implementation only. Until console captures exist, no VCFG-vs-GT
quality improvement, regression-free runtime behavior or new-engine HUD-flicker result is claimed.

## 4. PC reference and tuning

1. `tools/bench/vcfg_ref.py`: a NumPy port of mode 8 (pyr8 -> me3/me2/me1 -> gm8/fgs8/mf8 -> phase8 -> warp8) with
   every tunable constant in one parameter dict. First milestone: on the captured triplets its output matches the
   console's `mid` within ~0.5 dB PSNR; until then it is not used for decisions.
2. Tuning: random / CMA-ES search over the parameter dict on a training subset of clips, scored by a weighted sum
   (PSNR + SSIM + PSNR HUD + PSNR static - HUD flicker - TCE; LPIPS when available); a held-out subset checks for
   overfitting.
3. The best parameters go back into the `.ll` constants (one change per commit) and are confirmed on the console
   with the capture mode, then in play (fps, cadence, latency must not change).

## 5. Rules

- Clips stay local (`bench/` is git-ignored): they are game frames.
- A change to the warp is accepted only if it does not lose on PSNR HUD / PSNR static / HUD flicker (where VCFG
  classic is strong) while it gains on PSNR / SSIM / LPIPS / TCE.
