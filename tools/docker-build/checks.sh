#!/bin/bash
# Host wrapper: runs run_checks.sh in the container on a work tree kept by build_all.sh --keep-work.
#   checks.sh <work_src> <msvc|host>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); W=$(cd "$1" && pwd)
exec docker run --rm --platform linux/amd64 -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$HERE:$HERE:ro" -v "$W:$W" \
  "${VCFG_IMAGE:-vcfg-build:oo054-llvm18}" bash "$HERE/run_checks.sh" "$W" "$HERE/shim/$2"
