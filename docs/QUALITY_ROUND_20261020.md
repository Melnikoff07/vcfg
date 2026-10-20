# Two-grid quality experiment, 2026-10-20

## Subsequent validity correction: runtime post-processing feedback

Review of the owner's `stairs_report.md` confirmed a missing runtime effect:
`rcas8` modifies the current real game surface in place, and the following
warp reads that surface as P. The PC experiments below supplied raw A/raw B.
Their numbers remain measurements of that offline algorithm, but are not a
faithful reproduction of the deployed chain or proof of its in-game gains.
The cached motion stages may use retained raw luminance pyramids; do not
blindly replace all motion inputs with sharpened images when fixing the model.
Also, the LDS tile barrier does not protect border reads against other
workgroups writing the same image. The current post shader aliases input and
output for both mid and cur. Exact effect sizes in the external report have
not been independently reproduced here. Revalidate previous candidates with
the correct post/history chain before promoting them.

An offline candidate improved PSNR, SSIM, LPIPS and TCE together on the existing
12 CUSA00557 triplets under the clear profile. A separate title-gated plugin is
uploaded for hardware testing. Corrected build b activates successfully with
steady cadence, but the owner reports only a small visual improvement and
unacceptable crawling/flickering contours during camera turns. It is not accepted.

## Candidate and measurements

Keep the existing 16x16 motion pipeline and its temporal history. Refine a
second 8x8 field from that field using 4x4 L1 bilateral SAD, nine neighboring
seeds and a +/-1 L1-pixel local search, with the existing experiment's fixed
penalty of 4 per L1 pixel. Render both fields and post-process each with
Sharpness 100, Enhance AA off. Mix their outputs equally, apply a small
neighbor-clamped unsharp correction (gain 0.05), then retain the original
16x16 output wherever max-channel abs(A-B) < 1/255. Ground truth is used only
for scoring, never for field estimation, mixing or static protection.

Scene-change replacement is disabled in this experiment, including its
baseline. Geometry is 1920x1080, phase 0.5, scanout codes are quantized to 1023.
Motion uses `bench/state_full1_warm.pkl`: an emulated warm-history cache,
not a warm-history capture from hardware.

| Metric | Original clear profile | Candidate | Change |
|---|---:|---:|---:|
| PSNR, higher better | 31.10736 | 31.24256 | +0.13520 dB |
| SSIM, higher better | 0.852013 | 0.853722 | +0.001709 |
| LPIPS, lower better | 0.088862 | 0.085790 | -3.46% |
| TCE, lower better | 2.676592 | 2.648656 | -1.04% |
| PSNR static | 36.91372 | 36.91372 | identical by design |
| Static flicker proxy | 0.748658 | 0.748658 | identical by design |
| Mean HF / GT | 1.02553 | 1.00117 | aggregate energy near GT |

PSNR, LPIPS and TCE improve on every triplet. SSIM improves on 10/12; small
losses remain on 0002 (-0.000056) and 0010 (-0.000046).

Even-index triplets were used for screening in this round; odd-index triplets
were scored after fixing the final gain to 0.05. Training changes: PSNR
+0.10221 dB, LPIPS -3.55%, SSIM +0.001643, TCE -0.02765. Control-half changes:
PSNR +0.16819 dB, LPIPS -3.36%, SSIM +0.001775, TCE -0.02822.
The same capture was explored in earlier experiments, so this split is not an
independent unseen-scene validation. Fresh scenes remain necessary.

HF is mean absolute four-neighbor luma Laplacian divided by GT. It is only a
texture-energy proxy. It does not prove that blur is absent: triplet 0008
remains difficult, with HF 0.8000 -> 0.7197 despite better image metrics.
The fence still shows wrong/soft detail. Do not describe it as fixed.

The diagnostic fraction of pixels with GT luma > 0.6 and an output at least
0.2 darker changes from 0.07430% to 0.07341% (about -1.20% relative). This is
not a causal black-artifact detector, and some individual samples regress.

There is no HUD mask. HUD PSNR is unavailable and the metric named
`HUD_flicker` uses the static fallback. Exact preservation of that fallback
does not establish minimap/subtitle quality. TCE is a triplet step-error proxy;
continuous temporal shimmer and input latency are unmeasured by these PNGs.

## Reproduction and rejected paths

Run the configured Python with the existing local LPIPS dependencies:

```powershell
$env:PYTHONPATH='E:/Documents/ps4-framegen/pylibs'
& 'C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' tools/bench/experiment_clear_profile.py --split train --lpips
& 'C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' tools/bench/experiment_clear_profile.py --split validation --gains 0.05 --lpips
```

Ignored outputs: `bench/clear_grid_mix/{train,validation}_results.json`,
`combined_summary.json`, `NNNN_grid16.npy`, `NNNN_grid8.npy` and
`NNNN_gain0.05.npy`. Result JSON includes parameters and source/cache hashes.
Use trusted local pickle caches only. Cached source PNGs have no historical
input-content manifest; original cache provenance is limited to the handoff.

Other fixed-split PC experiments are retained for reproduction:

- `experiment_layers.py`: choosing the best one/two of the original four
  vectors loses about 0.20 / 0.10 dB on the screening half. Global CR changes
  add PSNR but do not reliably improve LPIPS.
- `experiment_static_guard.py`: unconditionally retain static weight under
  foreground splats; static PSNR loses 0.78 dB. Rejected.
- `experiment_subpixel.py`: regularized bilateral local LK updates; half/full
  strength lose 0.016 / 0.037 dB on the screening half. Rejected.
- `experiment_motion_layers.py`: a raw pre-filter field as an alternate helps
  PSNR but slightly worsens LPIPS. A separate camera layer does not help.
- `experiment_detail_mix.py`: complementary 16x16/8x8 outputs improve several
  metrics under the old 40/AA-light profile, but lose texture energy; this
  prompted the matched clear-profile experiment above.

## Isolated plugin and verification

`build_gridmix_plugin.py` copies C and shader sources into `build/gridmix/`,
applies checked source anchors there, generates/validates kernels and compiles
without C optimization. Production runtime sources and generated headers are
not replaced. Its internal version remains the working branch's 2.16; it is
an experimental artifact, not a versioned package/release.

Runtime gates: only CUSA00557 attaches, and only when the title-specific marker
exists at process load. The extra passes require Quality / Sharpness 100 /
Enhance AA off and optical motion. Other titles return before hooks/allocations.
The corrected scratch allocation is deferred to GPU initialization and reserves
18 MiB of flexible memory for two aligned 8 MiB surfaces; a 259200-byte field
occupies verified spare work memory.
GPU code/DCB allocation grows by 128 KiB. Allocation failure retains the base
path and logs the state. The test build disables cut replacement; cuts need
separate evaluation even when its extra passes are inactive.

Fine-field history does not replace the normal temporal field. Both warps run
before post-processing the real frame. The real frame is post-processed once;
fine mid post, blend and copy use separate immutable descriptor tables and
GPU barriers. Fine scratch alpha carries the pre-post static decision and is
never flipped; the final output restores original alpha. Scratch is retained
for process lifetime and is not freed while commands may reference it.

Validation completed before upload:

- Existing 7 benchmark tests passed; default PC warp remained bit-identical.
- Flat field and known 4-pixel translation checks passed; local-window halo
  results were independent of chunk size.
- `check_gridrefine_host.py`: the actual refinement IR, with host buffer
  emulation, matched all 388800 reference vectors exactly. Float64 cost
  addition preserves the NumPy SAD/penalty tie-break; its hardware cost is
  included in the forthcoming GPU test, not assumed free.
- `check_gridblend_host.py`: actual blend IR matched all 4147200 pixels of
  0000/0008 after output quantization; static pixels and alpha were preserved.
- gfx700 compilation and strict raw-code checks passed: refinement 49 VGPR /
  36 SGPR, fine warp 58 / 70, blend 23 / 26, no LDS or spill/scratch allocation
  in those three kernels. Host execution does not prove GPU execution.

Uploaded artifact:

- Local: `build/gridmix/vcfg_gridmix.prx`, 425984 bytes.
- Remote: `/data/GoldHEN/plugins/vcfg_classic_gridmix_20261020a.prx`.
- SHA-256: `e4e1e120b3cb91bf1c1aecfe200a70c7c52114c832dca7a0f6f0c23046ef15fe`.
- Marker: `/data/bb_probe/gridmix_CUSA00557` (new; staged and read back).
- Only CUSA00557 mapping changed, using `tools/console.py set` with stage,
  readback and backup. Previous mapping: `vcfg_classic_cut2.prx`.
- INI backup: `/data/GoldHEN/plugins.ini.before_CUSA00557_20261020_173520`.
- Original profiles/logs: `logs/gridmix_before_20261020/`; profile was already
  Quality / 100 / AA off and was not changed.
- Interactive klog: `logs/gridmix_before_20261020/hardware_klog.txt`.

The owner has been asked to fully restart CUSA00557 and compare fast turns,
windows and fence detail for 1-2 minutes. Check `GRIDMIX active=1`, memory
allocation, real/output cadence, total GPU time and visible artifacts before
accepting the build. The launcher must remain closed between tests because it
rewrites plugin mappings. No package, push or release was made.

## First hardware failure and alignment correction

Build `20261020a` crashed at the first generated frame (about 5.25 seconds).
Plugin load, all shader packaging checks and the flexible allocation succeeded;
the log confirmed `GRIDMIX active=1`. The GPU reported a TC2 write protection
fault at `0x881db0000`, whereas the flexible mapping began at `0x881db4000`:
the tiled address fell 16 KiB before the mapped allocation. Host vector/image
checks cannot detect this native tiling-address alignment requirement.

The CUSA00557 mapping was immediately restored to `vcfg_classic_cut2.prx`,
with a fresh backup; the test marker was moved to quarantine. Failure snapshot:
`logs/gridmix_failed_20261020/`.

The corrected allocator reserves 18 MiB of flexible memory, aligns the usable
base upward to 2 MiB (matching the established native mid-buffer allocation),
and retains two 8 MiB surfaces inside the mapping. All 128 possible 16 KiB page
offsets within the 2 MiB interval were checked: both surfaces fit entirely in
the mapped region. Vector conformance (388800 blocks) and blend conformance
(4147200 pixels) were rerun and still matched exactly.

Corrected artifact: `vcfg_classic_gridmix_20261020b.prx`, 425984 bytes,
SHA-256 `a9a1240599d4e4a4521b52583b82705d29f819782f66385dd62e7493ce4a3655`.
It was uploaded under a new name. Snapshot `logs/gridmix_b_feedback_20261020/`
confirms `GRIDMIX active=1`, mapping `0x881db4000`, aligned base `0x881e00000`.
The sampled log reports 30 real + 30 generated cadence, refresh 59.943 Hz,
no missed flips/timeouts/resyncs, GPU passes about 7.8-8.6 ms, and real-frame
handover-to-screen proxy about 58.9 ms. This proxy is not input-to-photon latency.
No new GPU protection fault was observed in that snapshot. The owner reports
slightly better visuals, but severe flickering/crawling stair steps remain on
aliased geometry when turning. Stable pacing does not establish visual quality.
No failed PRX was overwritten or deleted. Subsequent PC research is recorded
in `EDGE_STABILITY_RESEARCH_20261020.md`; no later build has been deployed.
