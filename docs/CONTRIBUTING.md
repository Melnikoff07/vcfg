# Contributing

## Game reports (no coding needed)

1. Build or download `vcfg_discovery.prx` and install it for all games:
   `python -I tools/console.py default build/vcfg_discovery/vcfg_discovery.prx`.
   Games that have their own `[CUSAxxxxx]` section in `plugins.ini` are skipped by the plugin itself.
2. Play each game for 5 minutes (reach gameplay). If a game crashes at start, exclude it:
   `python -I tools/console.py exclude CUSAxxxxx` and report that too.
3. `python -I tools/console.py logs` then `python -I tools/analyze.py logs > report.md`; open an issue with the report,
   the game name, region and patch version.

## VCFG test reports

Same as above with the VCFG plugin set for that title. Please say what you saw (smooth / flicker / artifacts /
colours) and attach `report.md`. The plugin writes why a frame was not generated; that is what we need.

## Code

- One variable per change; keep a CUSA07439 run in your report.
- Shader changes go through IR → `plugin/build_shader.py` → header; `tools/elf_arrays.py` must pass.
- Do not add hooks to `scePadRead*` (they hung game start before); the pad is polled.
- Keep the safety rules from the README. A change that frees or unregisters memory must explain how GPU, command and
  scanout retirement are proven first.
