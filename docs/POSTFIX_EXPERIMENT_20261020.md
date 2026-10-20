# Raw-real and immutable-source post experiment, 2026-10-20

Status: isolated CUSA00557 test uploaded; activation, GPU timing and visual
acceptance require a full owner-controlled game restart. No hardware success
is inferred from CPU/shader compilation tests. This is not a release.

Subsequent hardware result: `logs/postfix_feedback_20261020/` confirms active=1,
successful scratch allocation and stable 30+30 cadence in the final gameplay
windows. Those windows report about 3.8-4.8 ms GPU work and about 62.6 ms real
handover-to-screen proxy, which is not input-to-photon latency. Other windows
include native/menu transitions and report gaps: the entire session is not
claimed gap-free. No new GPU fault was observed in the captured test log.
The owner's verdict is that flicker remains. This diagnostic is not visually
accepted. The next AA/raw-history test is in POSTSPLIT_EXPERIMENT_20261020.md.

## Runtime change

The prior runtime post-processed the real game surface C in place. The next
warp read it as P, although its retained luminance pyramid had been built from
raw C. The reference experiments had supplied raw colors to both sides.
The existing LDS tile preload/barrier also does not isolate neighboring groups
reading the same image while other groups overwrite it.

The test uses raw real game surfaces throughout, including startup/native mode.
It does not sharpen or apply AA to real frames. The normal 16x16 warp renders
M, then a dedicated postout8 kernel reads immutable M and writes a distinct
aligned scratch surface. A dispatch barrier precedes copying the finished
scratch back to M. Overlays are restricted to generated surfaces; native
post/overlay returns without submitting work. The test profile has overlay off.

The first diagnostic artifact deliberately uses the original 16x16 motion
pipeline. The previous 16x16/8x8 mix is absent. Therefore the in-game comparison
against gridmix b changes reconstruction as well as post/history handling;
visual differences must not all be attributed to a single fix.

The post gate requires Quality, Sharpness 100, Enhance off, overlay off, one
mid frame and optical-only motion. Unsupported profiles or allocation failure
disable this post rather than dispatching the old in-place post; activation is
logged. Other titles and an absent title marker return before hooks/allocation.
Cut replacement is disabled in the isolated warp, matching the prior gridmix
experiment; scene cuts remain an independent acceptance item.

## Corrected PC chain

`experiment_post_chain.py` preserves the cached raw motion/pyramid state while
passing post(A) and raw B to legacy color warps. Fine-grid refinement in the
gridmix baseline also uses raw luma, matching retained GPU pyramids. Legacy
in-place post is approximated by deterministic NumPy post: scheduling races
are not simulated. Warm cached states and phase 0.5 remain approximations,
not an exact replay of a console trace. All measurements below use the same
12 archived CUSA00557 triples and the 100 / AA-off profile.

For a common raw GT and raw endpoints (directly comparable reconstruction task):

| Chain | PSNR | SSIM | LPIPS lower | raw-step TCE lower | static PSNR |
|---|---:|---:|---:|---:|---:|
| legacy16 with post(A) feedback | 30.91678 | 0.846989 | 0.0914653 | 2.743997 | 37.16333 |
| deployed gridmix b model with post(A) feedback | 31.04758 | 0.848715 | 0.0883752 | 2.716876 | 37.21282 |
| raw-real, mid-only, 16x16 diagnostic | 31.10736 | 0.852013 | 0.0888622 | 2.676592 | 36.91372 |

The new diagnostic is not a win on every measure: LPIPS and static scores are
slightly worse than gridmix b under this common reference. Do not claim full
visual acceptance or a universal quality improvement from this table.

Against each chain's own displayed-real target and endpoints:

| Chain | PSNR | LPIPS lower | display-step TCE lower | HF / displayed GT |
|---|---:|---:|---:|---:|
| legacy16 | 30.37674 | 0.0975132 | 2.922148 | 0.78814 |
| legacy gridmix b | 30.50249 | 0.0949608 | 2.896145 | 0.76746 |
| raw-real mid-only 16x16 | 31.10736 | 0.0888622 | 2.676592 | 1.02553 |

For legacy chains the displayed target/endpoints are post(GT), post(A), post(B).
For the diagnostic they are raw GT, A, B. These are different image references:
do not interpret their cross-row difference as a common-target quality gain.
The legacy16 row independently reproduces the corresponding 100/AA-off row
in the owner's stairs_report.md. Other profiles and its scheduling-race error
percentages were not independently reproduced in this round. HF is an absolute
luma-Laplacian proxy, not a visual-acceptance score. No HUD mask exists.

Measured artifacts: `bench/post_chain_v1/manifest.json`, `results.json` and
canonical `runtime_display_results.json`. The initial results file used raw
endpoints for its displayed-target TCE; `finalize_post_chain.py` corrects that
TCE/static-mask calculation while preserving image-only PSNR/SSIM/LPIPS.
Future runs finalize automatically into a fresh output directory:

```powershell
$env:PYTHONPATH='E:/Documents/ps4-framegen/pylibs'
python tools/bench/experiment_post_chain.py --out bench/post_chain_recheck --lpips
```

## Implementation and verification

`build_postfix_plugin.py` stages C, shader sources and headers under
`build/postfix/`; production C/shaders/headers are not replaced. One 10 MiB
flexible mapping supplies a 2 MiB aligned 8 MiB tiled surface, retained for
process lifetime. All 128 possible 16 KiB page offsets fit. Extra T# is at
word 144 / byte 576, 32-byte aligned; primary table ends at 0xD60 in its slot.
A separate immutable copy table uses the unused second-mid table region.

GPU/DCB reservation is 0x40000 / four 0x2000-byte DCB slots starting at 0x38000.
The extra post shader compiles for gfx700 to 784 words, 47 VGPR, 29 SGPR,
1152 bytes LDS and no scratch/spills. This does not establish GPU performance.

- `check_postout_host.py` executes the actual generated post IR with host image
  emulation. The complete LDS tile is preloaded before serial lane invocation.
  Across 0000/0008 and 100/AA0, 50/AA1, 0/AA2, it checks 37,324,800 RGB components;
  93 differ from NumPy by one code of 1023, none by more. The source hashes,
  destination guard bands and alpha all remain correct. Host fast-math can
  differ at quantization boundaries; this is not a GPU tiling/scheduling test.
- `check_postfix_commands.py` host-executes the actual staged C builders across
  48 slot/profile/allocation/present-copy cases. It decodes emitted dispatches,
  checks that no legacy post executes, verifies separate source/destination
  descriptors and original flip tail, and tests native pass-through. Maximum
  observed DCB size is 2280 / 8192 bytes. This is not driver execution.
- Seven existing benchmark tests pass; `git diff --check` passes.

## Uploaded artifact and next hardware check

- Artifact: `build/postfix/vcfg_postfix.prx`, 409488 bytes.
- SHA-256: `d3f7b0ce88ee827ceef7160014cfd10056825b75940686f4c83bde7f5f744d14`.
- Remote: `/data/GoldHEN/plugins/vcfg_classic_postfix_20261020a.prx`.
- Marker: `/data/bb_probe/postfix_CUSA00557`, staged and read back.
- Prior mapping: `vcfg_classic_gridmix_20261020b.prx`.
- Backup: `/data/GoldHEN/plugins.ini.before_CUSA00557_20261020_201059`.
- Snapshot: `logs/postfix_before_20261020/`; unchanged Quality/100/AA0/overlay0.
- Klog capture: `logs/postfix_before_20261020/hardware_klog.txt`.

Only CUSA00557's plugin mapping changed, through console.py set with staged PRX,
readback and INI backup. Other title sections and all profiles remain unchanged.
The owner has been asked to fully restart the game without opening the launcher
and compare turns around fences/windows/thin contours for 1-2 minutes.
Check `POSTFIX active=1`, allocation pointers, GPU faults, cadence, total GPU
time and latency proxy before acceptance. A restart is still required from
the owner. `rollback_postfix_test.py` restores gridmix b and moves the marker
to quarantine; it deletes no console artifacts. No package/push/release was made.
