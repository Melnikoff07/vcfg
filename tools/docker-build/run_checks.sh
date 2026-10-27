#!/bin/bash
# Runs INSIDE the vcfg-build container on a work tree produced by build_all.sh --keep-work.
#   run_checks.sh <work_src> <shim_dir>   (shim_dir: shim/msvc = faithful, shim/host = retargeted to Linux)
R=$1; SHIM=$2; cd "$R"
python3 - "$SHIM" <<'PY'
import json,sys; c=json.load(open('config.example.json')); c['llvm_bin']=sys.argv[1]; c['python']='python3'
json.dump(c,open('config.json','w'),indent=2)
PY
step() { echo; echo "== $1"; shift; "$@"; echo "exit=$?"; }
step "elf_arrays plugin (default list)" python3 -I tools/elf_arrays.py build/vcfg/vcfg.elf plugin/source
step "elf_arrays classic (default list)" python3 -I tools/elf_arrays.py build/classic/vcfg_classic.elf classic/source
NAMES=$(python3 -I -c "
import re,pathlib
h=pathlib.Path('classic/source/shaders.h').read_text(); m=pathlib.Path('classic/source/main.c').read_text()
print(','.join(n for n in re.findall(r'static const uint32_t (\w+)_code\[',h) if re.search(r'\b'+n+r'_code\b',m)))")
step "elf_arrays classic (arrays referenced by classic/source/main.c, as check_classic_builds.py)" python3 -I tools/elf_arrays.py build/classic/vcfg_classic.elf classic/source "$NAMES"
step "test_lowmem_valve ($SHIM)" python3 -I tools/tests/test_lowmem_valve.py
step "test_real_traces ($SHIM)" python3 -I tools/tests/test_real_traces.py
