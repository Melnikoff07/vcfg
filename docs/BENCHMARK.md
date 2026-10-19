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
