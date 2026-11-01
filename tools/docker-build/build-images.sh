#!/bin/bash
# Builds the two images used by build_all.sh:
#   $VCFG_IMAGE         (default vcfg-build:oo054-llvm18)  build.Dockerfile, context = dl_dir from fetch-deps.sh
#   $VCFG_SHADER_IMAGE  (default vcfg-llvm22)               llvm22.Dockerfile, llc 22.1.8 for the shaders
#
#   build-images.sh [dl_dir]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DL=${1:-$HERE/dl}
IMAGE=${VCFG_IMAGE:-vcfg-build:oo054-llvm18}
SIMG=${VCFG_SHADER_IMAGE:-vcfg-llvm22}
[ -f "$DL/toolchain-llvm-18.tar.gz" ] && [ -d "$DL/goldhen-sdk" ] || { echo "run fetch-deps.sh first (missing $DL)" >&2; exit 1; }
docker build --platform linux/amd64 -t "$IMAGE" -f "$HERE/build.Dockerfile" "$DL"
docker build -t "$SIMG" - < "$HERE/llvm22.Dockerfile"
echo "OK: $IMAGE and $SIMG"
