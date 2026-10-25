"""Fetch committed benchmark triplets over FTP or convert an existing raw directory.

python -I tools/bench/fetch.py --remote /data/bb_probe/bench_CUSA00557_<run> --scene slow_pan
python -I tools/bench/fetch.py --local-raw bench/raw/<run> --scene slow_pan
Only RETR/NLST commands are used on the console. Source frames stay under ignored bench/.
"""
import argparse
import ftplib
import hashlib
import json
import pathlib
import re
import sys
import uuid

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pngio import save16

ROOT = HERE.parents[1]
PARTS = ('a', 'gt', 'b', 'mid')


def metadata(data):
    meta = json.loads(data)
    if meta.get('schema') != 1 or meta.get('encoding') != 'rgba16le':
        raise ValueError('unsupported capture schema/encoding')
    if not re.fullmatch(r'CUSA\d{5}', str(meta.get('title', ''))):
        raise ValueError('invalid title')
    if not re.fullmatch(r'[0-9a-f]{64}', str(meta.get('source_hash', ''))):
        raise ValueError('missing exact source fingerprint')
    w, h = meta.get('width'), meta.get('height')
    if type(w) is not int or type(h) is not int or not (1 <= w <= 1920 and 1 <= h <= 1080):
        raise ValueError('invalid dimensions')
    if meta.get('plane_bytes') != w * h * 8:
        raise ValueError('invalid plane size')
    n = meta.get('sample')
    if type(n) is not int or not 0 <= n < 10000:
        raise ValueError('invalid sample number')
    frames, times = meta.get('frames'), meta.get('submit_us')
    if not isinstance(frames, list) or len(frames) != 3 or any(type(x) is not int for x in frames) or frames != list(range(frames[0], frames[0] + 3)):
        raise ValueError('triplet frames are not consecutive')
    if not isinstance(times, list) or len(times) != 3 or any(type(x) is not int for x in times):
        raise ValueError('invalid timestamps')
    dt = [times[1] - times[0], times[2] - times[1]]
    is_30hz = meta.get('title') in ('CUSA03041', 'CUSA05571', 'CUSA08519')
    if is_30hz:
        if meta.get('timing_valid') is not True or not all(26000 <= d <= 48000 for d in dt) or abs(dt[0] - dt[1]) > 5000 or not 54000 <= sum(dt) <= 95000:
            raise ValueError('not a uniform native-30 triplet')
    else:
        if meta.get('timing_valid') is not True or not all(15000 <= d <= 18500 for d in dt) or abs(dt[0] - dt[1]) > 1500 or not 32000 <= sum(dt) <= 35000:
            raise ValueError('not a uniform native-60 triplet')
    if meta.get('generated_flipped') is not False or meta.get('phase_q16') != 32768:
        raise ValueError('not an offscreen midpoint capture')
    return meta


def get(ftp, path, expected):
    chunks, size = [], 0
    def receive(data):
        nonlocal size
        size += len(data)
        if size > expected:
            raise ValueError('FTP object larger than declared size: ' + path)
        chunks.append(data)
    ftp.retrbinary('RETR ' + path, receive)
    data = b''.join(chunks)
    if len(data) != expected:
        raise ValueError('FTP object shorter than declared size: ' + path)
    return data


def convert(raw, out, scene):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', scene):
        raise ValueError('scene must use 1..64 ASCII letters/digits/_/-')
    metas = sorted(raw.glob('bench_[0-9][0-9][0-9][0-9].json'))
    if not metas:
        raise ValueError('no committed triplets found')
    entries, signature, title = [], None, None
    stage = out / ('.convert-' + uuid.uuid4().hex)
    clip_stage, method_stage = stage / 'clip', stage / 'method'
    clip_stage.mkdir(parents=True)
    method_stage.mkdir()
    for p in metas:
        meta = metadata(p.read_bytes())
        if p.name != f'bench_{meta["sample"]:04d}.json':
            raise ValueError('metadata filename/sample mismatch')
        sig = {k: meta.get(k) for k in ('title', 'width', 'height', 'format', 'tiling', 'pitch', 'history', 'profile', 'build', 'source_hash', 'crop_x', 'crop_y')}
        if signature is not None and sig != signature:
            raise ValueError('mixed geometry/profile/history/build in one clip')
        signature, title = sig, meta['title']
        n = f'{meta["sample"]:04d}'
        entry = {'metadata': meta, 'raw_sha256': {}}
        # Verify all four raw planes before publishing any PNG of this triplet.
        planes = {}
        for part in PARTS:
            data = (raw / f'bench_{n}_{part}.rgba').read_bytes()
            if len(data) != meta['plane_bytes']:
                raise ValueError(f'truncated plane: {n}_{part}')
            planes[part] = np.frombuffer(data, dtype='<u2').reshape(meta['height'], meta['width'], 4)
            entry['raw_sha256'][part] = hashlib.sha256(data).hexdigest()
        clip = out / 'clips' / f'{title}_{scene}'
        method = out / 'vcfg_classic' / f'{title}_{scene}'
        if clip.exists() or method.exists():
            raise FileExistsError('clip/method already exists; choose a new scene name')
        for part, array in planes.items():
            target = (method_stage if part == 'mid' else clip_stage) / f'{n}_{part}.png'
            save16(target, array)
        entries.append(entry)
    manifest = {'schema': 1, 'source': str(raw.resolve()), 'clip': f'{title}_{scene}', 'triplets': entries}
    # Publish the method first, then the complete input clip. A failed conversion
    # leaves its staging directory for inspection and exposes no partial clip.
    clip.parent.mkdir(parents=True, exist_ok=True)
    method.parent.mkdir(parents=True, exist_ok=True)
    (clip_stage / 'capture.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    method_stage.rename(method)
    clip_stage.rename(clip)
    stage.rmdir()
    return clip, method, len(entries)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--remote')
    src.add_argument('--local-raw', type=pathlib.Path)
    src.add_argument('--list', action='store_true', help='list available console capture runs (read only)')
    ap.add_argument('--scene')
    ap.add_argument('--out', type=pathlib.Path, default=ROOT / 'bench')
    args = ap.parse_args()
    if args.list:
        config = json.loads((ROOT / 'config.json').read_text())
        with ftplib.FTP() as ftp:
            ftp.connect(config['ps4_host'], int(config.get('ps4_ftp_port', 2121)), timeout=10)
            ftp.login()
            runs = sorted({p.rsplit('/', 1)[-1] for p in ftp.nlst('/data/bb_probe/')
                           if re.fullmatch(r'bench_CUSA\d{5}_[0-9a-fA-F]{16}', p.rsplit('/', 1)[-1])})
            for run in runs:
                print('/data/bb_probe/' + run)
            if not runs:
                print('No benchmark capture runs found')
        return
    if not args.scene:
        ap.error('--scene is required for conversion/fetch')
    if args.remote:
        if not re.fullmatch(r'/data/bb_probe/bench_CUSA\d{5}_[0-9a-fA-F]{16}', args.remote):
            ap.error('remote must be an exact benchmark run directory')
        config = json.loads((ROOT / 'config.json').read_text())
        raw = args.out / 'raw' / args.remote.rsplit('/', 1)[-1]
        raw.mkdir(parents=True, exist_ok=True)
        with ftplib.FTP() as ftp:
            ftp.connect(config['ps4_host'], int(config.get('ps4_ftp_port', 2121)), timeout=30)
            ftp.login()
            names = sorted({s.rsplit('/', 1)[-1] for s in ftp.nlst(args.remote)
                            if re.fullmatch(r'bench_\d{4}\.json', s.rsplit('/', 1)[-1])})
            for name in names:
                # JSON is small and published last by the plugin.
                chunks = []
                def small(data):
                    chunks.append(data)
                    if sum(map(len, chunks)) > 16384:
                        raise ValueError('oversized metadata')
                ftp.retrbinary('RETR ' + args.remote + '/' + name, small)
                data = b''.join(chunks)
                meta = metadata(data)
                if name != f'bench_{meta["sample"]:04d}.json':
                    raise ValueError('metadata filename/sample mismatch')
                for part in PARTS:
                    filename = f'bench_{meta["sample"]:04d}_{part}.rgba'
                    payload = get(ftp, args.remote + '/' + filename, meta['plane_bytes'])
                    local = raw / filename
                    if local.exists() and local.read_bytes() != payload:
                        raise ValueError('existing raw plane differs: ' + filename)
                    local.write_bytes(payload)
                (raw / name).write_bytes(data)
    else:
        raw = args.local_raw
    clip, method, count = convert(raw, args.out, args.scene)
    print(f'{count} triplets\nclip: {clip}\nmethod: {method}')


if __name__ == '__main__':
    main()
