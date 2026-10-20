# PC motion-grid experiment, 2026-10-20

Decision: do not promote this refinement into the runtime. Perceptual distance
improves, but PSNR, SSIM and static-pixel error regress on average. This result
does not reject a complete 8x8 motion estimator or a two-layer estimator.

## Scope and reproduction

Dataset: all 12 full-resolution triplets in `bench/clips/CUSA00557_full1`.
Motion: existing `bench/state_full1_warm.pkl`, an approximation of warm history;
this is not a new warm-history hardware capture. All cached fade flags are false.
The cache is paired with triplets by index; its length and geometry are checked,
but the old cache has no input-content manifest to verify its provenance further.

Fixed rendering: `qmax=1023`, `sharpness=40`, `enhance=1`, phase 0.5,
scene-change replacement disabled (`warp_flags=8192`). These explicit settings
define the baseline below; do not combine it with historical scores under other
sharpening or scene-change settings.

Three variants:

- `baseline16`: original cached field and original 16-pixel warp grid.
- `inherited8`: nearest replication of that field onto an 8-pixel grid; the warp
  now interpolates between the smaller cells. This control changes interpolation,
  even though it adds no newly estimated vectors.
- `refined8`: each 8x8 full-resolution cell evaluates 4x4 L1 bilateral SAD using
  seeds from the original 3x3 field neighborhood, then searches +/-1 L1 pixel
  around the winning seed. A displacement penalty of 4 per L1 pixel discourages
  departure from the inherited vector. Ground truth is used only for scoring.
  The coarse search, foreground map and temporal history remain from the original
  algorithm. This is a seeded refinement, not a full me1/mf8 conversion.

Run from the repository with the configured Python and local LPIPS dependencies:

```powershell
$env:PYTHONPATH='E:/Documents/ps4-framegen/pylibs'
& 'C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' tools/bench/experiment_grid8.py --clip bench/clips/CUSA00557_full1 --states bench/state_full1_warm.pkl --out bench/grid8_warm --lpips
```

Use only trusted locally generated pickle caches. Local ignored outputs:
`bench/grid8_warm/results.json` (cache hash, parameters, per-frame scores and
means), `source_hashes.json` (four reference/experiment source hashes), and
`NNNN_<variant>.npy` (quantized float32 outputs, before PNG conversion).

## Results

Arithmetic means of per-triplet scores, using the existing metrics implementation:

| Variant | PSNR up | SSIM up | LPIPS down | PSNR static up | Static flicker proxy down | TCE down |
|---|---:|---:|---:|---:|---:|---:|
| baseline16 | 31.5852 | 0.864615 | 0.115513 | 37.5105 | 0.719965 | 2.577206 |
| inherited8 | 31.1987 | 0.858179 | 0.110019 | 37.1926 | 0.740354 | 2.659936 |
| refined8 | 31.5220 | 0.863266 | 0.107627 | 37.4693 | 0.721612 | 2.572780 |

Refined versus baseline: PSNR -0.0632 dB, SSIM -0.001349, LPIPS -6.83%,
PSNR static -0.0411 dB, static flicker proxy +0.23%, TCE -0.17%.
LPIPS improves on all 12 triplets; PSNR improves on only 2 (0009 and 0011).
The largest PSNR improvement is +0.4286 dB on 0009; the largest loss is
-0.2290 dB on 0000.

There is no HUD mask. `PSNR_HUD` is unavailable, and the tool's `HUD_flicker`
column uses the static-pixel fallback. It cannot establish actual HUD quality.
TCE here measures the A/mid/B step error per triplet, not a continuous clip's
temporal stability. These sparse samples cannot establish in-game shimmer,
black-patch frequency, scene-cut handling or GPU cost.

## Verification and next experiment

- Default 16-pixel warp output was bit-identical to the version at `e5efea2`
  on a random field and image pair that exercise the noncoherent path.
- A flat static input retained a zero field with the expected 8x8 grid geometry.
- A known 4-pixel horizontal translation retained the correct vector away from
  the image borders.
- All 7 existing `test_bench.py` tests passed.

No runtime shader, plugin, launcher or console configuration was changed.
No package was built or published. Pre-existing reference edits were copied to
`backups/continuation_20261020/` before further work.

Next useful experiment: retain two candidate vectors around motion boundaries
and choose/blend them using local sample agreement, rather than expecting one
vector per smaller cell to describe both layers. Validate on separate scenes
and a clip with a real HUD mask before any runtime adoption.
