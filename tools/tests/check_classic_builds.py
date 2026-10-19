"""Verify shader packaging and test-hook isolation in all four built classic ELFs."""
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('elf_arrays', ROOT / 'tools/elf_arrays.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
arrays = mod.arrays(ROOT / 'classic/source')
main_source = (ROOT / 'classic/source/main.c').read_text()
arrays = {name: data for name, data in arrays.items() if re.search(r'\b' + re.escape(name) + r'_code\b', main_source)}
hooks = ['Detour_sceVideoOutGetFlipStatus', 'Detour_sceVideoOutAddFlipEvent', 'Detour_sceGnmInsertWaitFlipDone']
manifest = {'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'builds': [], 'source_sha256': {}}
for mode in range(4):
    folder = 'classic' if mode == 0 else f'classic-test{mode}'
    elf = (ROOT / 'build' / folder / 'vcfg_classic.elf').read_bytes()
    bad = {name: elf.count(data) for name, data in arrays.items() if elf.count(data) != 1}
    if bad:
        raise SystemExit(f'{folder}: shader array mismatch: {bad}')
    for hook in hooks:
        if (hook.encode() + b'\0' in elf) != (mode != 0):
            raise SystemExit(f'{folder}: unexpected hook presence: {hook}')
    path = ROOT / 'build' / folder / 'vcfg_classic.prx'
    data = path.read_bytes()
    manifest['builds'].append({'mode': mode, 'path': str(path.relative_to(ROOT)).replace('\\', '/'),
                               'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                               'arrays_exact': len(arrays)})
    print(f'{folder}: {len(arrays)}/{len(arrays)} arrays exact; test hooks isolated; bytes={len(data)}')
sources = list((ROOT / 'classic/source').glob('*.[ch]')) + list((ROOT / 'overlay').glob('*.[ch]'))
for path in sorted(sources):
    manifest['source_sha256'][str(path.relative_to(ROOT)).replace('\\', '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
(ROOT / 'build/verification/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print('manifest: build/verification/manifest.json')
