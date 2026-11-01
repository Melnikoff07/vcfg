#!/bin/bash
# Builds vcfg.prx, vcfg_classic.prx and VCFG_v<version>.pkg from any VCFG source tree, in the Linux amd64
# container $VCFG_IMAGE (default vcfg-build:oo054-llvm18, build.Dockerfile; see README.md). The source tree is never written:
# it is copied to a temporary directory first.
#
#   build_all.sh <src_tree> <out_dir> <version> [--regen-shaders <llc>] [--run-tests] [--keep-work] [--bench]
#
#   --regen-shaders <llc>  regenerate classic/source/shaders.h (classic/gen_shaders.py) in the work copy before
#                          compiling, inside the image $VCFG_SHADER_IMAGE (default vcfg-llvm22: Ubuntu 24.04 +
#                          apt.llvm.org llvm-22). <llc> is a name or path inside that image, e.g. llc-22
#                          (LLVM 22.1.8 reproduces the versioned header byte for byte; 18-21 do not).
#                          The comparison with the versioned header goes to <out_dir>/shaders-regen.txt.
#                          Default: no regeneration, the versioned header is compiled as is.
#   --run-tests            also run tools/tests/test_lowmem_valve.py and test_real_traces.py as a gate, like
#                          the official scripts. A config.json is written in the work copy with llvm_bin =
#                          shim/$VCFG_TEST_SHIM (default host: clang retargeted to x86_64-pc-linux-gnu, because
#                          the Windows target does not run under lli on Linux; msvc = unchanged arguments).
#   --keep-work            keep the temporary copy (<out_dir>/.work) for inspection.
#   --bench                capture build, as classic/build.ps1 -Bench: classic/bench/shaders -> build/classic-bench/
#                          bench_shaders.h (gen_shaders.py in $VCFG_SHADER_IMAGE with the --regen-shaders llc, default
#                          llc-22), classic/bench/build_id.py -> bench_build.h + bench_sources.json, classic compiled
#                          with -DVCFG_BENCH=1 into build/classic-bench. Artifacts vcfg_classic_bench.{prx,elf} and
#                          bench_sources.json; the PKG ships the capture PRX in place of vcfg_classic.prx.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=${VCFG_IMAGE:-vcfg-build:oo054-llvm18}
[ $# -ge 3 ] || { sed -n '2,23p' "$0"; exit 2; }
SRC=$(cd "$1" && pwd); mkdir -p "$2"; OUT=$(cd "$2" && pwd); VER=$3; shift 3
REGEN=-; TESTS=0; KEEP=0; BENCH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --regen-shaders) REGEN=$2; shift 2 ;;
    --run-tests) TESTS=1; shift ;;
    --keep-work) KEEP=1; shift ;;
    --bench) BENCH=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$OUT/" in "$SRC"/*) echo "out_dir must be outside the source tree" >&2; exit 2 ;; esac
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "image $IMAGE missing: run fetch-deps.sh and build-images.sh (see README.md)" >&2; exit 1; }
WORK=$OUT/.work; rm -rf "$WORK"; mkdir -p "$WORK/src"
# Copy without any previous build output, git metadata or the Docker build context of fetch-deps.sh.
tar -C "$SRC" --exclude=./build --exclude=./.git --exclude=./tools/docker-build/dl -cf - . | tar -C "$WORK/src" -xf -
if [ "$REGEN" != "-" ]; then
  SIMG=${VCFG_SHADER_IMAGE:-vcfg-llvm22}; H=$WORK/src/classic
  cp "$H/source/shaders.h" "$WORK/shaders.h.versioned"
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$HERE:$HERE:ro" -v "$OUT:$OUT" "$SIMG" \
    bash -c "$REGEN --version | head -3; python3 -I $H/gen_shaders.py \"\$(command -v $REGEN)\" $H/shaders $H/source/shaders.h" \
    > "$OUT/shaders-regen.txt" 2>&1 || { cat "$OUT/shaders-regen.txt"; echo "shader regeneration failed" >&2; exit 1; }
  if cmp -s "$WORK/shaders.h.versioned" "$H/source/shaders.h"; then echo "regen: shaders.h byte-identical to versioned" | tee -a "$OUT/shaders-regen.txt"
  else echo "regen: shaders.h DIFFERS from versioned; building with the regenerated header" | tee -a "$OUT/shaders-regen.txt"; fi
fi
if [ "$BENCH" = 1 ]; then
  SIMG=${VCFG_SHADER_IMAGE:-vcfg-llvm22}; H=$WORK/src/classic; BLLC=$REGEN; [ "$BLLC" != "-" ] || BLLC=llc-22
  mkdir -p "$WORK/src/build/classic-bench"
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$HERE:$HERE:ro" -v "$OUT:$OUT" "$SIMG" \
    bash -c "$BLLC --version | head -3; python3 -I $H/gen_shaders.py \"\$(command -v $BLLC)\" $H/bench/shaders $WORK/src/build/classic-bench/bench_shaders.h" \
    > "$OUT/bench-shaders.txt" 2>&1 || { cat "$OUT/bench-shaders.txt"; echo "bench shader generation failed" >&2; exit 1; }
  echo "bench: build/classic-bench/bench_shaders.h generated with $BLLC (see bench-shaders.txt)"
fi
rm -f "$OUT/vcfg.prx" "$OUT/vcfg_classic.prx" "$OUT/vcfg_classic_bench.prx" "$OUT/VCFG_v$VER.pkg"
set +e
docker run --rm --platform linux/amd64 -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$HERE:$HERE:ro" -v "$OUT:$OUT" \
  "$IMAGE" bash "$HERE/build_inner.sh" "$WORK/src" "$OUT" "$VER" "$REGEN" "$TESTS" "$HERE/shim/${VCFG_TEST_SHIM:-host}" "$BENCH" 2>&1 | tee "$OUT/build.log"
rc=${PIPESTATUS[0]}
set -e
[ "$KEEP" = 1 ] || [ "$rc" != 0 ] || rm -rf "$WORK"
[ "$rc" = 0 ] && echo "OK: $OUT" || echo "FAILED ($rc): log in $OUT/build.log, work tree kept in $WORK"
exit "$rc"
