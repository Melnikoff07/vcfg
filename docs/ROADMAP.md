# Development priorities after v2.36

These are investigation areas, not promised features or release dates.

- Investigate CUSA10237 startup failures; keep it disabled until a fix is validated on hardware.
- Reduce remaining repeating-texture shimmer, thin-object flicker and character halos without breaking stable scenes.
- Validate Quality / Fast / Sharp across console models, buffer layouts and output modes. Measure latency on hardware separately from trace-replay estimates.
- Grow current-release CUSA coverage from reproducible reports and original traces; retain memory and presentation safety gates.
- Keep Windows and Docker build instructions aligned with the source and automated regression checks.

For current behavior use the [release notes](https://github.com/Melnikoff07/vcfg/releases/tag/v2.36) and [known issues](COMPATIBILITY.md). Submit evidence through [the reporting workflow](CONTRIBUTING.md).
