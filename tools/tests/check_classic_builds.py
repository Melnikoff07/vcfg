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
for mode in range(5):
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
bench = ROOT / 'build/classic-bench'
if (bench / 'vcfg_classic.elf').exists():
    elf = (bench / 'vcfg_classic.elf').read_bytes()
    bench_arrays = arrays | mod.arrays(bench)
    if any(elf.count(data) != 1 for data in bench_arrays.values()):
        raise SystemExit('classic-bench: shader array mismatch')
    sources = json.loads((bench / 'bench_sources.json').read_text())
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest for path, digest in sources['members'].items()):
        raise SystemExit('classic-bench: sources changed since compilation; rebuild the benchmark')
    data = (bench / 'vcfg_classic.prx').read_bytes()
    manifest['benchmark'] = {'path': 'build/classic-bench/vcfg_classic.prx', 'bytes': len(data),
                             'sha256': hashlib.sha256(data).hexdigest(), 'source_hash': sources['sha256'],
                             'arrays_exact': len(bench_arrays)}
    print(f'classic-bench: {len(bench_arrays)}/{len(bench_arrays)} arrays exact; source fingerprint matches; bytes={len(data)}')
sources = list((ROOT / 'classic/source').glob('*.[ch]')) + list((ROOT / 'overlay').glob('*.[ch]'))
for path in sorted(sources):
    manifest['source_sha256'][str(path.relative_to(ROOT)).replace('\\', '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
(ROOT / 'build/verification/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print('manifest: build/verification/manifest.json')
