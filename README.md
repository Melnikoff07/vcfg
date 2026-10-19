# VCFG

VCFG (vibe coded frame generator): optical-flow frame generation for games on PS4 consoles running the
GoldHEN homebrew enabler, as a GoldHEN plugin. The plugin hooks the game's frame
submission, builds a motion field between the previous and the current game frame on the GPU, synthesises an
in-between image and presents it before the real frame: a 30 fps game is shown at 60 Hz.

**Status: experimental.** Working on hardware: CUSA07439 (30 → 60, menu, sharpening, overlay). Partly working:
CUSA09171 (about 40 % of frames generated, HUD flicker). Not yet: CUSA13801, CUSA08519 on this runtime (see
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)). Tested only on a base console with GoldHEN. Use at your own risk.

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
| `plugin/source` | runtime (`a20_copy_impl.h`), hooks and old paths (`main.c`), PM4 scanner, scheduler, shader arrays (`*.h`) |
| `plugin/shaders` | LLVM IR (gfx700) of every GPU pass; `plugin/build_shader.py` turns IR into the arrays |
| `discovery/` | passive plugin: logs how any game presents frames (no GPU commands, no allocations) |
| `tools/console.py` | install to a game / `[default]` / exclude / fetch logs over GoldHEN FTP, with backups |
| `tools/analyze.py` | logs → Markdown compatibility report |
| `tools/elf_arrays.py` | build-time check that shader machine arrays are byte-exact in the ELF |

## Build

Requirements: Windows PowerShell, [OpenOrbis toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain),
GoldHEN Plugins SDK, LLVM (clang, ld.lld), Python 3. For shader work also an LLVM `llc` with the AMDGPU target.

```powershell
copy config.example.json config.json   # set your paths and console IP
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1                    # plugin, release flags
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -Target discovery
```

## Install and test

```powershell
python -I tools/console.py set CUSA07439 build/vcfg/vcfg.prx     # CUSA07439 (EU)
python -I tools/console.py default build/vcfg_discovery/vcfg_discovery.prx   # passive logs for all other games
```

Start the game fully (no hot reload). After ~20 s hold **L3+R3 for 3 s** for the menu: FG on/off, QUALITY/FAST,
sharpness, enhance, overlay. Then `python -I tools/console.py logs` and `python -I tools/analyze.py logs`.

## Contributing

The most useful contribution right now is **data**: run the discovery plugin in your games and send the reports.
See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) and [docs/ROADMAP.md](docs/ROADMAP.md).

## Rules that keep consoles and saves safe

- Never text-replace inside `shaders.h` / `warp720.h` / `post_cb.h`: regenerate from IR. A global rename once
  corrupted 75 machine words and silently broke motion search.
- Never overwrite a PRX a game may have loaded; always a new file name. No hot unload.
- Never free memory or unregister surfaces before GPU, command and scanout retirement are all proven.
