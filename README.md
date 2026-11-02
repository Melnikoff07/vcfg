# VCFG

**Current release: [v2.36](https://github.com/Melnikoff07/vcfg/releases/tag/v2.36) (1 November 2026).** For install and update details, use the release notes for the PKG you actually installed.

VCFG (vibe coded frame generator): optical-flow frame generation for games on PS4 consoles running the
GoldHEN homebrew enabler, as a GoldHEN plugin. The plugin hooks the game's frame
submission, builds a motion field between the previous and the current game frame on the GPU, synthesises an
in-between image and presents it before the real frame: a 30 fps game is shown at 60 Hz.

**Status: experimental.** Compatibility depends on the CUSA ID, game update, console model and output mode.
See [compatibility and known issues](docs/COMPATIBILITY.md) before enabling FG for a title.

## New in v2.36

- **Quality** keeps the existing quality path. **Fast** reduces the present queue and uses timer pacing for lower latency; **Sharp** applies stronger sharpening to the real frame before generation.
- Reduced repeating-texture shimmer and thin-object flicker, improved contour and screen-edge warping, and support for 3200×1800 and non-16:9 frames in the warp pass.
- Audio, memory and crash fixes for multiple CUSA families; menus show effective option states (ACTIVE / READY / INERT / LIMITED).
- Linux/macOS builds via [Docker](tools/docker-build/README.md).

Install **VCFG_v2.36.pkg** from [the release](https://github.com/Melnikoff07/vcfg/releases/tag/v2.36).
Existing `config-<TITLE>.ini` settings remain valid; per-title `adapt-*` data resets on first run.
Keep only one VCFG plugin active. Disable VCFG for **CUSA10237**, which is currently unsupported.
See the release notes for the full change list and limitations.

Русское описание: [README_RU.md](README_RU.md).

*Independent hobby project. Not affiliated with, endorsed or sponsored by Sony Interactive Entertainment or any game
publisher. "PS4" is a trademark of its owner and is used here only to say which hardware the code runs on. Games are
referred to by their title IDs. No game code or assets are included.*

## How it works

1. **Observe and admit.** Inspect game buffers, pixel format, command submissions and available DirectMemory before enabling generation.
2. **Generate.** GPU motion estimation and warping synthesize an intermediate image from consecutive game frames. Memory and presentation paths depend on the engine and title profile.
3. **Present.** Quality, Fast and Sharp choose different pacing or sharpening behavior. When a runtime safety gate rejects a frame, it falls back to native presentation.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
powershell -NoProfile -ExecutionPolicy Bypass -File classic/build.ps1  # classic engine -> build/classic/vcfg_classic.prx
powershell -NoProfile -ExecutionPolicy Bypass -File launcher/build_pkg.ps1 -Version 2.36   # -> build/launcher/VCFG_v2.36.pkg
python -I overlay/preview.py build/preview/vov                         # overlay preview PNGs (after building the DLL, see the script)
```

The new engine keeps generated shader arrays in `plugin/source/*.h`; after changing IR in `plugin/shaders`,
regenerate the arrays (`plugin/build_shader.py`) and replace only that shader's block. The classic engine
regenerates `classic/source/shaders.h` on every build.

## Install

1. Download `VCFG_vX.pkg` from [Releases](https://github.com/Melnikoff07/vcfg/releases) and install it with the
   GoldHEN Package Installer (e.g. from `/data/pkg`).
2. Enable **GoldHEN → Settings → Plugins**. Start **VCFG** from the home screen. On start it copies the plugins to
   `/data/GoldHEN/plugins/`, backs up `plugins.ini` and maps eligible games (your other plugins are kept).
3. Pick a game (cross toggles FG, triangle opens its settings, square launches it). Other games appear as
   EXPERIMENTAL and are off until you switch them on.
4. Start the game, wait ~20 s, hold **L3+R3 for 3 s** for the in-game menu (the buttons can be changed in the app: OVERLAY tab, "Menu hotkey"; takes effect at the next game start).
   Rows that cannot act under the other settings are hidden (Below 30 FPS needs Quality or Sharp, in the app too). An
   option that can be inert shows what it does now (ACTIVE, READY, INERT or LIMITED) and the header gives the reason of
   the selected row; `probe.log` gets an `options effective:` line at start, at each change and when a state changes.

**Disable / clean reset:** in **04 PLUGIN**, Square (**TURN OFF**) and R2 twice (**WIPE ALL**) both remove
VCFG plugin files, VCFG `plugins.ini` entries/backups, and VCFG settings, adaptation and profiles. They keep logs in
`/data/bb_probe/` and leave third-party plugins alone. A red **VCFG IS TURNED OFF** banner remains until you press
Cross (**INSTALL**) in **04 PLUGIN**. Fully restart the game after changing plugin state. Save a report before cleaning
if you need a single file to share.

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

The most useful contribution right now is a reproducible current-version report with `vcfg_report.txt`.
See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md), [docs/ROADMAP.md](docs/ROADMAP.md).

## Rules that keep consoles and saves safe

- Never text-replace inside `shaders.h` / `warp720.h` / `post_cb.h`: regenerate from IR. A global rename once
  corrupted 75 machine words and silently broke motion search.
- Never overwrite a PRX a game may have loaded; always a new file name. No hot unload.
- Never free memory or unregister surfaces before GPU, command and scanout retirement are all proven.
