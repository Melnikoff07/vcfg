#!/bin/bash
# Regenerates <tree>/classic/source/shaders.h in place from classic/shaders/*.ll, with llc 22.1.8 in
# $VCFG_SHADER_IMAGE (default vcfg-llvm22). Run it after editing a shader, then commit the header.
#
#   regen-shaders.sh <tree> [llc]     (llc: name or path inside the image, default llc-22)
set -euo pipefail
[ $# -ge 1 ] || { sed -n '2,5p' "$0"; exit 2; }
T=$(cd "$1" && pwd -P); LLC=${2:-llc-22}
[ -f "$T/classic/gen_shaders.py" ] || { echo "$T is not a VCFG tree (no classic/gen_shaders.py)" >&2; exit 2; }
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$T:/src" "${VCFG_SHADER_IMAGE:-vcfg-llvm22}" \
  bash -c "$LLC --version | head -2; python3 -I /src/classic/gen_shaders.py \"\$(command -v $LLC)\" /src/classic/shaders /src/classic/source/shaders.h"
git -C "$T" diff --stat -- classic/source/shaders.h 2>/dev/null || true
