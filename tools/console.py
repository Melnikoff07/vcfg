"""Console helper over the GoldHEN FTP server (port 2121). Host/port come from config.json.

  python -I tools/console.py ini                                   show /data/GoldHEN/plugins.ini
  python -I tools/console.py set CUSA07439 build/vcfg/vcfg.prx [--name vcfg_v1.prx] [--reuse]
                                                               upload under a unique name, point that game at it
  python -I tools/console.py default build/vcfg_discovery/vcfg_discovery.prx [--name ...]
                                                               put a plugin into [default] (replaces the [default] line)
  python -I tools/console.py exclude CUSA01113                     empty section: [default] plugins stay out of that game
  python -I tools/console.py remove CUSA09171                      delete that game's section
  python -I tools/console.py logs [--out logs] [--sessions 6]      fetch probe.log (split per session) + discovery logs
  python -I tools/console.py snapshot [--out logs/snapshot]       read-only snapshot: traces, profiles and logs

Rules (learned the hard way): never overwrite a PRX that a running game may have loaded (always a new name), change
only the requested section, back up plugins.ini on the console and locally, refuse if the ini changed meanwhile.
Changes take effect at the next full start of the game; there is no hot reload.
"""
import ftplib, hashlib, io, json, pathlib, re, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
INI = '/data/GoldHEN/plugins.ini'
PLUG = '/data/GoldHEN/plugins/'
LOGDIR = '/data/bb_probe/'

def cfg():
    p = ROOT / 'config.json'
    if not p.exists(): sys.exit('config.json not found: copy config.example.json and set ps4_host')
    return json.loads(p.read_text())

def connect():
    c = cfg(); f = ftplib.FTP(); f.connect(c['ps4_host'], int(c.get('ps4_ftp_port', 2121)), timeout=10); f.login(); return f

def get(f, path):
    b = []; f.retrbinary('RETR ' + path, b.append); return b''.join(b)

def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default

def sections(lines):
    """Yield (title or None, [line indices]) for every section."""
    cur, idx, out = None, [], []
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith(b'[') and s.endswith(b']'):
            out.append((cur, idx)); cur, idx = s[1:-1].decode(), [i]
        else:
            idx.append(i)
    out.append((cur, idx)); return out

def write_ini(f, original, new_lines, tag):
    nl = b'\r\n' if b'\r\n' in original else b'\n'
    updated = nl.join(new_lines) + nl
    if updated == original: print('NO_CHANGE'); return
    stamp = time.strftime('%Y%m%d_%H%M%S'); backup = f'{INI}.before_{tag}_{stamp}'
    (ROOT / 'backups').mkdir(exist_ok=True); (ROOT / 'backups' / f'plugins.ini.before_{tag}_{stamp}').write_bytes(original)
    f.storbinary('STOR ' + backup, io.BytesIO(original))
    if get(f, backup) != original: raise RuntimeError('remote backup differs')
    tmp = INI + '.vcfg.tmp'; f.storbinary('STOR ' + tmp, io.BytesIO(updated))
    if get(f, tmp) != updated: raise RuntimeError('staged ini differs')
    if get(f, INI) != original: raise RuntimeError('plugins.ini changed meanwhile; refusing')
    try:
        f.rename(tmp, INI)
        if get(f, INI) != updated: raise RuntimeError('ini readback differs')
    except Exception:
        f.storbinary('STOR ' + INI, io.BytesIO(original))
        if get(f, INI) != original: raise RuntimeError('INI ROLLBACK FAILED; backup at ' + backup)
        raise
    print(updated.decode(errors='replace')); print('BACKUP', backup)

def upload(f, local, name):
    data = pathlib.Path(local).read_bytes(); target = PLUG + name
    if name in {n.rsplit('/', 1)[-1] for n in f.nlst(PLUG)}:
        if '--reuse' not in sys.argv: raise RuntimeError(f'{target} exists; pick a new --name or pass --reuse')
        print('PRX_REUSED', target); return target
    stg = target + '.stage'; f.storbinary('STOR ' + stg, io.BytesIO(data))
    if get(f, stg) != data: raise RuntimeError('staged PRX differs')
    f.rename(stg, target); print('PRX_UPLOADED', target, len(data)); return target

def cmd_set(title, local):
    name = opt('--name', pathlib.Path(local).stem + time.strftime('_%Y%m%d_%H%M') + '.prx')
    with connect() as f:
        original = get(f, INI); lines = original.splitlines(); target = upload(f, local, name).encode()
        secs = dict((t, i) for t, i in sections(lines) if t)
        if title not in secs:
            pos = next((i for i, l in enumerate(lines) if l.strip() == b'[default]'), len(lines))
            new = lines[:pos] + [f'[{title}]'.encode(), target] + lines[pos:]
        else:
            body = [i for i in secs[title][1:] if lines[i].strip()]
            if len(body) > 1: raise RuntimeError(f'[{title}] has several plugin lines; edit by hand')
            new = list(lines)
            if body: new[body[0]] = target
            else: new.insert(secs[title][0] + 1, target)
        write_ini(f, original, new, title)

def cmd_default(local):
    name = opt('--name', pathlib.Path(local).stem + time.strftime('_%Y%m%d_%H%M') + '.prx')
    with connect() as f:
        original = get(f, INI); lines = original.splitlines(); target = upload(f, local, name).encode()
        secs = dict((t, i) for t, i in sections(lines) if t)
        if 'default' in secs:
            keep = set(secs['default'][1:]); new = [l for i, l in enumerate(lines) if i not in keep]
            pos = new.index(b'[default]') + 1; new.insert(pos, target)
        else:
            new = lines + [b'[default]', target]
        write_ini(f, original, new, 'default')

def cmd_exclude(title):
    with connect() as f:
        original = get(f, INI); lines = original.splitlines()
        if any(t == title for t, _ in sections(lines)): raise RuntimeError(f'[{title}] already exists')
        pos = next((i for i, l in enumerate(lines) if l.strip() == b'[default]'), len(lines))
        write_ini(f, original, lines[:pos] + [f'[{title}]'.encode()] + lines[pos:], 'exclude_' + title)

def cmd_remove(title):
    with connect() as f:
        original = get(f, INI); lines = original.splitlines()
        drop = set(next((i for t, i in sections(lines) if t == title), []))
        if not drop: print('NO_SUCH_SECTION'); return
        write_ini(f, original, [l for i, l in enumerate(lines) if i not in drop], 'remove_' + title)

def cmd_logs():
    out = ROOT / opt('--out', 'logs'); out.mkdir(exist_ok=True); n = int(opt('--sessions', '6'))
    with connect() as f:
        t = get(f, LOGDIR + 'probe.log').decode(errors='replace')
        st = [m.start() for m in re.finditer('io thread started:', t)]
        for k, a in enumerate(st[-n:]):
            j = st.index(a); s = t[a:(st[j + 1] if j + 1 < len(st) else len(t))]
            title = (re.search(r'title=(CUSA\d+)', s) or re.search(r'(CUSA\d{5})', s))
            (out / f'session_{k:02d}_{title[1] if title else "unknown"}.log').write_text(s)
        for name in f.nlst(LOGDIR):
            name = name.rsplit('/', 1)[-1]
            if name.startswith('discovery'):
                (out / name).write_bytes(get(f, LOGDIR + name))
    print('saved to', out)

def cmd_snapshot():
    out = ROOT / opt('--out', 'logs/snapshot_' + time.strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'files': {}, 'errors': {}}
    allowed = re.compile(r'(?:trace-CUSA\d{5}(?:\.prev)?\.txt|config-CUSA\d{5}\.ini|'
                         r'probe(?:\.prev)?\.log|vcfg(?:_launcher)?\.ini|result-CUSA\d{5}\.ini)\Z')
    with connect() as f:
        paths = [INI]
        paths += [LOGDIR + p.rsplit('/', 1)[-1] for p in f.nlst(LOGDIR)
                  if allowed.fullmatch(p.rsplit('/', 1)[-1])]
        for remote in sorted(set(paths)):
            name = remote.rsplit('/', 1)[-1]
            chunks, size = [], 0
            def receive(chunk):
                nonlocal size
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise RuntimeError('snapshot file exceeds 32 MiB limit')
                chunks.append(chunk)
            try:
                f.retrbinary('RETR ' + remote, receive)
                data = b''.join(chunks)
                (out / name).write_bytes(data)
                manifest['files'][name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            except ftplib.error_perm as e:
                manifest['errors'][name] = str(e)
    (out / 'snapshot.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('snapshot saved to', out, 'files', len(manifest['files']), 'errors', len(manifest['errors']))

def main():
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    if a[0] == 'ini':
        with connect() as f: print(get(f, INI).decode(errors='replace'))
    elif a[0] == 'set': cmd_set(a[1], a[2])
    elif a[0] == 'default': cmd_default(a[1])
    elif a[0] == 'exclude': cmd_exclude(a[1])
    elif a[0] == 'remove': cmd_remove(a[1])
    elif a[0] == 'logs': cmd_logs()
    elif a[0] == 'snapshot': cmd_snapshot()
    else: sys.exit(__doc__)

if __name__ == '__main__':
    main()
