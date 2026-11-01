#!/bin/bash
# Fills the Docker build context of build.Dockerfile (default: dl/ next to this script, git-ignored):
#   toolchain-llvm-18.tar.gz  OpenOrbis PS4 toolchain v0.5.4 release asset, checked against its sha256
#   goldhen-sdk/              GoldHEN Plugins SDK clone at a pinned commit
#
#   fetch-deps.sh [dl_dir]
#
# Re-running is safe: a tarball with the right sha256 and an SDK clone at the pinned commit are kept.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DL=${1:-$HERE/dl}
OO_URL=https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain/releases/download/v0.5.4/toolchain-llvm-18.tar.gz
OO_SHA256=3c7cd5bb593ca74fa1c13fd59f3938dc0fc07985167f7275063019e63abe4526
SDK_URL=https://github.com/GoldHEN/GoldHEN_Plugins_SDK.git
SDK_COMMIT=bcea3c7ef01dac6d7f9f49ebf9e90fe66d86f5f7

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
die() { echo "fetch-deps: $*" >&2; exit 1; }
for t in curl git; do command -v "$t" >/dev/null || die "$t is required"; done
mkdir -p "$DL"; DL=$(cd "$DL" && pwd)

TGZ=$DL/toolchain-llvm-18.tar.gz
if [ -f "$TGZ" ] && [ "$(sha256 "$TGZ")" = "$OO_SHA256" ]; then
  echo "toolchain: $TGZ already present, sha256 OK"
else
  echo "toolchain: downloading $OO_URL"
  curl -fsSL --retry 3 -o "$TGZ.part" "$OO_URL" || die "download failed: $OO_URL"
  got=$(sha256 "$TGZ.part")
  if [ "$got" != "$OO_SHA256" ]; then
    rm -f "$TGZ.part"
    die "sha256 mismatch for toolchain-llvm-18.tar.gz: got $got, expected $OO_SHA256. Not using it."
  fi
  mv "$TGZ.part" "$TGZ"; echo "toolchain: sha256 OK ($OO_SHA256)"
fi

SDK=$DL/goldhen-sdk
if [ -d "$SDK/.git" ] && [ "$(git -C "$SDK" rev-parse HEAD 2>/dev/null)" = "$SDK_COMMIT" ] && [ -z "$(git -C "$SDK" status --porcelain)" ]; then
  echo "goldhen-sdk: $SDK already at $SDK_COMMIT"
else
  [ ! -e "$SDK" ] || die "$SDK exists but is not a clean clone at $SDK_COMMIT; remove it and re-run"
  echo "goldhen-sdk: cloning $SDK_URL at $SDK_COMMIT"
  git clone -q "$SDK_URL" "$SDK" || die "clone failed: $SDK_URL"
  git -C "$SDK" -c advice.detachedHead=false checkout -q "$SDK_COMMIT" || die "commit $SDK_COMMIT not found in $SDK_URL"
  [ "$(git -C "$SDK" rev-parse HEAD)" = "$SDK_COMMIT" ] || die "goldhen-sdk is not at $SDK_COMMIT"
fi

printf 'goldhen-sdk/.git\n*.part\n' > "$DL/.dockerignore"
echo "OK: build context ready in $DL"
