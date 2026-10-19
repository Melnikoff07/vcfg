#!/bin/sh
# Builds the classic engine plugin: build/classic/vcfg_classic.prx
set -e
H=$(cd "$(dirname "$0")" && pwd -W); R=$(dirname "$H")
j() { "$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$R/config.json" "$1"; }
PY=$(python -c "import json;print(json.load(open('$R/config.json'))['python'])")
TC=$(j openorbis_toolchain); SDK=$(j goldhen_sdk); LL=$(j llvm_bin); LLC=$(j llc_gfx700)
export OO_PS4_TOOLCHAIN="$TC" DOTNET_ROLL_FORWARD=Major
VER=$(head -n 1 "$R/VERSION" | tr -cd "0-9.")
B="$R/build/classic"; mkdir -p "$B"
"$PY" -I "$H/gen_shaders.py" "$LLC" "$H/shaders" "$H/source/shaders.h"
printf '#define GIT_COMMIT "vcfg"\n#define GIT_VER "vcfg"\n#define GIT_NUM 0\n#define BUILD_DATE "vcfg"\n' > "$H/source/git_ver.h"
F="--target=x86_64-pc-freebsd12-elf -fPIC -funwind-tables -isysroot $TC -isystem $TC/include -I$SDK/include -I$H/source -I$R/overlay -Wno-c99-designator -D_BSD_SOURCE=1 -D__BSD_VISIBLE=1 -D__PS4__=1 -DOO=1 -D__OPENORBIS__=1 -D__OOPS4__=1 -D__FINAL__=1 -D__USE_PRINTF__ -DVCFG_VER=$VER"
O=""
for f in "$H"/source/*.c "$R/overlay/vov.c"; do n=$(basename "$f" .c); "$LL/clang.exe" $F -c "$f" -o "$B/$n.o"; O="$O $B/$n.o"; done
"$LL/ld.lld.exe" "$SDK/build/crtprx.o" $O -o "$B/vcfg_classic.elf" -m elf_x86_64 -pie -e _init --script "$TC/link.x" --eh-frame-hdr -L"$TC/lib" -L"$SDK" \
  -lSceLibcInternal -lSceGnmDriver -lSceVideoOut -lScePad -lGoldHEN_Hook -lkernel -lSceSysmodule -lSceSystemService -lSceUserService
"$TC/bin/windows/create-fself.exe" -in="$B/vcfg_classic.elf" -out="$B/vcfg_classic.oelf" -lib="$B/vcfg_classic.prx" --paid 0x3800000000000011
ls -la "$B/vcfg_classic.prx"
