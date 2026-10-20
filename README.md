# VCFG

VCFG (vibe coded frame generator): optical-flow frame generation for games on PS4 consoles running the
GoldHEN homebrew enabler, as a GoldHEN plugin. The plugin hooks the game's frame
submission, builds a motion field between the previous and the current game frame on the GPU, synthesises an
in-between image and presents it before the real frame: a 30 fps game is shown at 60 Hz.

**Status: experimental.** Verified on hardware (base console, GoldHEN): CUSA07439 (new engine), CUSA08519,
CUSA00411 and CUSA00207 (classic engine): 30 → 60, in-game menu, FPS counter, anti-aliasing and sharpening.
Any other installed game can be switched on in the app as EXPERIMENTAL (classic engine, may not work or may crash).
See [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). Use at your own risk.

Русское описание: [README_RU.md](README_RU.md).

*Independent hobby project. Not affiliated with, endorsed or sponsored by Sony Interactive Entertainment or any game
publisher. "PS4" is a trademark of its owner and is used here only to say which hardware the code runs on. Games are
referred to by their title IDs. No game code or assets are included.*

## How it works

1. **Observe.** For the first ~600 flips the plugin only watches: game buffers, pixel format, free DirectMemory.
2. **Decide.** It allocates its GPU slab, one 1280×720 generated surface and work buffers (≈5 MiB) at the top of the
   highest free DirectMemory range, registers the surface with VideoOut right after the game's buffers, and checks
   every frame (flip tail form, buffer sizes, compute-register state). Any mismatch → that frame is shown natively.
3. **Generate.** GPU passes appended to the game's own flip submit: luma pyramids → hierarchical motion search
   (me3/me2/me1) → filtering → phase → warp to 720p → optional post (RCAS sharpening / AA) → flip the generated image,
   then flip the real frame with the same flip argument.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Measured hardware facts: [docs/FINDINGS.md](docs/FINDINGS.md).

## Layout

| Path | What |
|---|---|
| `launcher/` | the VCFG app (PKG): game cards with console titles and icons, per-game settings, plugin install |
| `plugin/` | new engine (A20/A22 runtime) for CUSA07439: `source/a20_copy_impl.h`, shaders in `shaders/` |
| `classic/` | classic engine (v1.16 line) for the other titles and EXPERIMENTAL games; `classic/build.sh` |
| `overlay/` | in-game menu and FPS counter: CPU renderer (`vov.c`, baked fonts), compositor `osd8.ll`, PC preview |
| `discovery/` | passive plugin: logs how any game presents frames (no GPU commands, no allocations) |
| `tools/` | `console.py` (FTP install / logs with backups), `analyze.py`, `elf_arrays.py` |

## Build

Requirements: Windows PowerShell, Git Bash, [OpenOrbis toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain),
GoldHEN Plugins SDK, LLVM (clang, ld.lld), Python 3 (Pillow for `overlay/bake_fonts.py`), an LLVM `llc` with the
AMDGPU target for shaders.

```powershell
copy config.example.json config.json                                   # paths and console IP
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1          # new engine -> build/vcfg/vcfg.prx
sh classic/build.sh                                                    # classic engine -> build/classic/vcfg_classic.prx
powershell -NoProfile -ExecutionPolicy Bypass -File launcher/build_pkg.ps1 -Version 2.11   # -> build/launcher/VCFG_v2.11.pkg
python -I overlay/preview.py build/preview/vov                         # overlay preview PNGs (after building the DLL, see the script)
```

The new engine keeps generated shader arrays in `plugin/source/*.h`; after changing IR in `plugin/shaders`,
regenerate the arrays (`plugin/build_shader.py`) and replace only that shader's block. The classic engine
regenerates `classic/source/shaders.h` on every build.

## Install

1. Download `VCFG_vX.pkg` from [Releases](https://github.com/Melnikoff07/vcfg/releases) and install it with the
   GoldHEN Package Installer (e.g. from `/data/pkg`).
2. Start **VCFG** from the home screen. On start it copies the plugins to `/data/GoldHEN/plugins/`, backs up
   `plugins.ini` and maps the verified games (your other plugins are kept).
3. Pick a game (cross toggles FG, triangle opens its settings, square saves and exits). Other games appear as
   EXPERIMENTAL and are off until you switch them on.
4. Start the game, wait ~20 s, hold **L3+R3 for 3 s** for the in-game menu (the buttons can be changed in the app: OVERLAY tab, "Menu hotkey"; takes effect at the next game start).

Turn off: tab **04 PLUGIN** → square (removes VCFG from `plugins.ini`; files stay).

## Install and test (developers)

```powershell
python -I tools/console.py set CUSA07439 build/vcfg/vcfg.prx
python -I tools/console.py logs
```

## Bug reports

In the app: tab **04 PLUGIN** → triangle (**debug log on**) → start the game and reproduce → back in the app,
circle (**save report**). Attach `vcfg_report.txt` (USB stick, or `/data/vcfg_report.txt` over FTP) to an
[issue](https://github.com/Melnikoff07/vcfg/issues/new?template=bug_report.md). The report holds the app version,
`plugins.ini`, firmware and game versions, settings, plugin files, an automatic summary per game and the plugin logs of
the last two game starts. A small flight recorder (`/data/bb_probe/trace-<TITLE>.txt`: first and last 80 log lines of
each game start) is always on, so even a report saved with the debug log off shows where a game start stopped.

## Contributing

The most useful contribution right now is **data**: run the discovery plugin in your games and send the reports.
See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md), [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/NEXT_STEPS.md](docs/NEXT_STEPS.md).

## Rules that keep consoles and saves safe

- Never text-replace inside `shaders.h` / `warp720.h` / `post_cb.h`: regenerate from IR. A global rename once
  corrupted 75 machine words and silently broke motion search.
- Never overwrite a PRX a game may have loaded; always a new file name. No hot unload.
- Never free memory or unregister surfaces before GPU, command and scanout retirement are all proven.
