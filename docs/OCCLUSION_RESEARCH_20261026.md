# Occlusion research: the two failing disocclusion tests (October 2026)

PC-only research on the reference (`tools/bench/vcfg_ref.py`). No console run. Result: the root cause of the two
failing tests in `test_disocclusion_and_hud.py` is in `mf8`, not in `warp8`; two `mf8` changes that make both tests pass
were measured on a held-out corpus and rejected, because they lower occlusion and moving-edge quality there.
No runtime change is proposed from this research.

## Root cause

In the opposite-motion scene of the test (foreground moving right 12 px/frame, background moving left 8 px/frame),
`mf8` gives the foreground vector to the background block next to each side of the object (block columns 8 and 15;
the ground-truth field has the background vector there). The two 6-px crescents of the test then fall inside a
coherent foreground-vector region, so `warp8` resamples them with the wrong vector.

| Field fed to `warp8` | Trailing crescent | Leading crescent |
|---|---|---|
| `mf8` output (current) | 24.65 dB (+5.21 over the old baseline) | 25.38 dB (+3.00) |
| ground-truth block field | 35.03 dB (+15.59) | 33.50 dB (+11.12) |

Why `mf8` picks the foreground vector for block 8: the background candidate's bilateral SAD is 3340 because the
foreground covers 10 of its 16 columns in P (quadrants 321 / 1163 / 329 / 1269); the foreground candidate matches
background texture at two offsets for a SAD of 1112. The outward disocclusion shift, which moves the SAD window away
from the disagreeing neighbour and would give a clean background match, is gated off by `ztex <= 160`
(block 8 has `ztex` 543, block 15 has 426).

## Candidates

| Candidate | Change | Unit tests | Held-out corpus (vs current) |
|---|---|---|---|
| M1 | drop the `ztex <= 160` gate | both pass (35.03 / 33.50 dB) | rejected on the training clips already: occlusion -0.40 dB, moving edges -0.14 dB, PSNR -0.08 dB |
| M3 | keep the gate, but also shift where `fgs8` confirms the boundary (no foreground key here, one in a 4-neighbour block) | both pass (35.03 / 33.50 dB) | occlusion -0.30 dB, moving edges -0.08 dB, PSNR -0.04 dB; diagonal scene occlusion -0.77 dB |

M3 fits the hardware budget (`mf8` 1424 to 1488 words, 61 VGPR, no scratch, region end 0x1BD00), so the rejection is
about quality only. M3 helps horizontal motion (+0.12 dB occlusion on the horizontal training scene) and hurts
vertical and diagonal motion; tuning it further on these scenes would fit the test rather than the problem.
A fix probably needs an occlusion-aware bilateral cost for the camera candidate in `mf8` (for example, scoring only
the quadrants away from the disagreeing neighbour) rather than a gate change.

## Corpus and metrics

- Real clips: 10 scenes of a CC-BY 3.0 1080p60 test film (Blender Foundation), 12 triplets each (30 -> 60), with the
  synthetic HUD of `tools/bench/add_hud.py` composited on A, GT and B. 5 clips for training, 5 held out.
- Synthetic clips (exact ground truth, fixed seeds): horizontal, vertical (training), diagonal, screen border,
  camera cut and mostly static scene (held out), with true occlusion and moving-edge masks.
- Profile: sharpness 0, AA off, 8-bit, phase 0.5, temporal field chained between consecutive triplets of a clip.
- Metrics: `tools/bench/metrics.py` plus PSNR inside the occlusion, moving-edge and border masks. LPIPS was not
  available (no `torch`).
- Limits: no game frames (the PC reference outputs 1080p where the console writes 720p), and the reference is not yet
  validated against console captures (docs/BENCHMARK.md, section 4).
