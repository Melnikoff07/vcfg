"""Fingerprint the exact source inputs of a local benchmark build."""
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[2]
out = pathlib.Path(sys.argv[1])
members = {}
for folder in ('classic/source', 'classic/shaders', 'classic/bench/shaders', 'overlay'):
    for p in sorted((root / folder).rglob('*')):
        if p.is_file() and p.suffix in ('.c', '.h', '.ll'):
            members[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
digest = hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest()
(out / 'bench_build.h').write_text(f'#define VCFG_BENCH_SOURCE_HASH "{digest}"\n', encoding='ascii')
(out / 'bench_sources.json').write_text(json.dumps({'sha256': digest, 'members': members}, indent=2) + '\n')
print('benchmark source SHA256', digest)
