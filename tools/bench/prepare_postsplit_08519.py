"""Prepare or restore the explicitly requested second-title split-post test."""
import argparse
import io
import json
import pathlib
import re
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import console

TITLE = 'CUSA08519'
OLD = b'/data/GoldHEN/plugins/vcfg_classic36.prx'
NEW = b'/data/GoldHEN/plugins/vcfg_classic_postsplit_08519_20261020a.prx'
PROFILE = '/data/bb_probe/config-' + TITLE + '.ini'
MARKER = '/data/bb_probe/postfix_split_' + TITLE
DEST = console.ROOT / 'logs/postsplit_08519_before_20261020'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--rollback', action='store_true')
    args = ap.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    with console.connect() as ftp:
        ini = console.get(ftp, console.INI)
        lines = ini.splitlines()
        sec = next(idx for title, idx in console.sections(lines) if title == TITLE)
        body = [i for i in sec[1:] if lines[i].strip() and not lines[i].lstrip().startswith((b';', b'#'))]
        if len(body) != 1:
            raise RuntimeError('unexpected title section')
        mapping = lines[body[0]].strip()
        current = console.get(ftp, PROFILE)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        if args.rollback:
            before = (DEST / 'profile_before.ini').read_bytes()
            tested = (DEST / 'profile_test.ini').read_bytes()
            if mapping not in (OLD, NEW) or current not in (before, tested):
                raise RuntimeError('profile or mapping changed externally; inspect before rollback')
            updated = before
        else:
            if mapping != OLD or (DEST / 'prepare.json').exists():
                raise RuntimeError('unexpected mapping or existing preparation; inspect first')
            values = dict(line.split('=', 1) for line in current.decode().splitlines() if '=' in line)
            for key, value in [('framegen', '1'), ('preset', '0'), ('sharpness', '40'), ('enhance', '1'), ('overlay', '0')]:
                if values.get(key) != value:
                    raise RuntimeError('unexpected profile: ' + key)
            updated, count = re.subn(rb'(?m)^sharpness=40(?=\r?$)', b'sharpness=100', current)
            if count != 1:
                raise RuntimeError('sharpness entry not unique')
            (DEST / 'profile_before.ini').write_bytes(current)
            (DEST / 'profile_test.ini').write_bytes(updated)
            (DEST / 'plugins_before.ini').write_bytes(ini)
        backup = PROFILE + '.before_' + ('restore_' if args.rollback else 'postsplit_') + stamp
        ftp.storbinary('STOR ' + backup, io.BytesIO(current))
        if console.get(ftp, backup) != current:
            raise RuntimeError('backup readback mismatch')
        stage = PROFILE + '.stage_' + stamp
        ftp.storbinary('STOR ' + stage, io.BytesIO(updated))
        if console.get(ftp, stage) != updated or console.get(ftp, PROFILE) != current or console.get(ftp, console.INI) != ini:
            raise RuntimeError('staging or concurrency mismatch')
        ftp.rename(stage, PROFILE)
        if console.get(ftp, PROFILE) != updated:
            raise RuntimeError('final profile mismatch; backup at ' + backup)
        if args.rollback:
            newlines = list(lines)
            newlines[body[0]] = OLD
            console.write_ini(ftp, ini, newlines, TITLE + '_restore')
            ftp.rename(MARKER, MARKER + '.disabled_' + stamp)
        else:
            data = b'isolated split post test ' + TITLE.encode() + b'\n'
            ftp.storbinary('STOR ' + MARKER + '.stage_' + stamp, io.BytesIO(data))
            if console.get(ftp, MARKER + '.stage_' + stamp) != data:
                raise RuntimeError('marker staging mismatch')
            ftp.rename(MARKER + '.stage_' + stamp, MARKER)
            if console.get(ftp, MARKER) != data:
                raise RuntimeError('marker final mismatch')
        result = dict(title=TITLE, profile=PROFILE, backup=backup, marker=MARKER,
                      old_mapping=OLD.decode(), test_mapping=NEW.decode(), rollback=args.rollback)
        (DEST / ('restore.json' if args.rollback else 'prepare.json')).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
