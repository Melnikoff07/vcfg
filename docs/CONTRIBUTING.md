# Contributing

## Current-release reports

1. Install [v2.36](https://github.com/Melnikoff07/vcfg/releases/tag/v2.36). Keep only one VCFG plugin active.
2. In the launcher, **04 PLUGIN → Triangle → DEBUG LOG ON**.
3. Start the title, reach gameplay if possible and reproduce the issue.
4. Reopen VCFG, **04 PLUGIN → Circle → save report**.
5. Attach `vcfg_report.txt` from USB or `/data/vcfg_report.txt` over FTP to a [bug report](https://github.com/Melnikoff07/vcfg/issues/new?template=bug_report.md).

Include VCFG version, CUSA ID and game update, console/firmware/GoldHEN, output resolution/HDR, Quality/Fast/Sharp and settings, reproduction steps, other plugins and whether it also happens with FG disabled. Use CUSA IDs rather than game names. A video helps for visual artifacts. Save a report before wiping settings.

## Code changes

- Keep changes focused and distinguish trace/host results from PS4 validation.
- Follow `AGENTS.md`: before committing, both `python tools/tests/test_lowmem_valve.py` and `python tools/tests/test_real_traces.py` must pass.
- Shader changes go through IR → generated arrays; run `python tools/elf_arrays.py`.
- Do not text-replace generated shader machine code or overwrite a PRX that a running title may have loaded.
- Memory release/unregistration requires proof that GPU, command and scanout work have retired.
- Describe target CUSA, evidence, settings and validation limits in the change.

Build instructions: [Windows](../README.md#build), [Linux/macOS Docker](../tools/docker-build/README.md).
