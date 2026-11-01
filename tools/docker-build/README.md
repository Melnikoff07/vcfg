# Docker build (Linux and macOS)

Builds `vcfg.prx`, `vcfg_classic.prx` and `VCFG_v<version>.pkg` from a VCFG source tree with Docker, as a Linux
port of `build.ps1`, `classic/build.ps1` and `launcher/build_pkg.ps1`. No Windows machine is needed.

## Prerequisites

- Docker (Docker Desktop on macOS; Apple Silicon runs the amd64 build image under emulation).
- `bash`, `curl`, `git`. About 4 GB of disk for the two images.
- On macOS the source tree and the output directory must be in a directory shared with Docker Desktop (the home
  directory is shared by default).

## One-time setup

```sh
tools/docker-build/fetch-deps.sh      # fills tools/docker-build/dl/ (git-ignored)
tools/docker-build/build-images.sh    # builds the two images from dl/
```

`fetch-deps.sh [dl_dir]` downloads the OpenOrbis toolchain
`https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain/releases/download/v0.5.4/toolchain-llvm-18.tar.gz`, checks its
sha256 (`3c7cd5bb593ca74fa1c13fd59f3938dc0fc07985167f7275063019e63abe4526`) and stops if it does not match, then
clones the GoldHEN Plugins SDK at commit `bcea3c7ef01dac6d7f9f49ebf9e90fe66d86f5f7`.

`build-images.sh [dl_dir]` builds:

| Image | Default tag | Variable | Content |
|---|---|---|---|
| build | `vcfg-build:oo054-llvm18` | `VCFG_IMAGE` | Ubuntu 24.04 amd64, clang/lld 18, OpenOrbis v0.5.4, GoldHEN SDK |
| shaders | `vcfg-llvm22` | `VCFG_SHADER_IMAGE` | Ubuntu 24.04, `llc-22` from apt.llvm.org |

Set the same variables when calling `build_all.sh` to use other tags.

## Build

```sh
tools/docker-build/build_all.sh <src> <out> <version> [--regen-shaders llc-22] [--run-tests] [--keep-work] [--bench]
```

- `<src>` is any VCFG tree (a checkout or a `git worktree`). It is copied to `<out>/.work` and never written.
  `<out>` must be outside `<src>`.
- `<version>` is the PKG version: file name, `param.sfo` `APP_VER`/`VERSION` and the launcher's app version. Both
  plugins take their version from the tree's `VERSION` file, as the official scripts do; `build_inner.sh` prints a
  `WARN` when the two differ.
- `--regen-shaders llc-22` regenerates `classic/source/shaders.h` in the work copy before compiling and records in
  `<out>/shaders-regen.txt` whether it is byte-identical to the versioned header.
- `--run-tests` runs `tools/tests/test_lowmem_valve.py` and `tools/tests/test_real_traces.py` as a gate, like
  `build.ps1`. The tests call `clang.exe`/`lli.exe`; `shim/host` (default) retargets the test bitcode to Linux,
  `VCFG_TEST_SHIM=msvc` keeps the Windows arguments.
- `--bench` is the capture build of `classic/build.ps1 -Bench`: the PKG ships `vcfg_classic_bench.prx` in place of
  `vcfg_classic.prx`.
- `--keep-work` keeps `<out>/.work`; `checks.sh <out>/.work/src host` then re-runs `tools/elf_arrays.py` and the
  tests on it.

Output in `<out>`: `vcfg.prx`, `vcfg_classic.prx` (or `vcfg_classic_bench.prx`), the matching `.elf` files,
`VCFG_v<version>.pkg` (checked with `PkgTool.Core pkg_validate`) and `build.log`, which ends with the sha256 of each
artifact. The build is reproducible: the same tree and images give the same PRX and PKG bytes in any directory.

After editing a shader, regenerate the versioned header with `tools/docker-build/regen-shaders.sh <tree>` and commit
it.

### Why llc 22.1.8

`classic/source/shaders.h` is generated from `classic/shaders/*.ll` by `llc` for gfx700. LLVM 22.1.8 reproduces the
versioned header byte for byte; LLVM 18 to 21 produce different code. apt.llvm.org only serves the latest 22.x, so
`llvm22.Dockerfile` fails if the installed `llc-22` is not 22.1.8. `--build-arg LLC_EXPECT=any` skips that check;
then use `--regen-shaders` and read `shaders-regen.txt` before trusting the result.
