"""Turn fetched logs (tools/console.py logs) into a Markdown compatibility report.
Usage: python -I tools/analyze.py [logs_dir] > report.md
VCFG sessions: what the runtime decided (buffers, format, memory plan, registration) and how many frames got a
generated image versus a native fallback, with the reasons. Discovery logs: how a game presents frames.
"""
import collections, pathlib, re, sys

def last(pattern, text, default=None):
    m = re.findall(pattern, text)
    return m[-1] if m else default

def fg_session(path):
    s = path.read_text(errors='replace')
    title = last(r'title=(CUSA\d+)', s, '?')
    build = last(r'A21_BUILD tag=(\S+)', s, '-')
    t = re.search(r'A22_TITLE .*?ngame=(\d+) .*?format=(0x[0-9a-f]+) .*?(\d+x\d+)', s)
    reg = last(r'A20_REGISTER .*?result=(0x[0-9a-f]+)', s, '-')
    rt = re.findall(r'A20_RUNTIME enabled=\d+ menu=\d+ jobs=(\d+) fg_pairs=(\d+) ui_frames=\d+ skips=(\d+)', s)
    jobs, pairs, skips = (map(int, rt[-1]) if rt else (0, 0, 0))
    reasons = collections.Counter(re.findall(r'reason=(\w+)', s))
    gates = collections.Counter(re.findall(r'A22_GATE reason=(\w+)', s))
    stop = last(r'A20_STOP_REQUEST reason=(\w+)', s, '')
    total = pairs + skips
    return {
        'file': path.name, 'title': title, 'build': build,
        'buffers': t[1] if t else ('2' if 'A20_READY' in s else '-'), 'format': t[2] if t else '-',
        'res': t[3] if t else '-', 'register': reg, 'fg_frames': pairs, 'native_fallback': skips,
        'fg_share': f'{100 * pairs / total:.0f}%' if total else '-',
        'reasons': ', '.join(f'{k} x{v}' for k, v in reasons.most_common(4)) or '-',
        'gates': ', '.join(f'{k} x{v}' for k, v in gates.most_common(3)) or '-', 'stop': stop or '-'}

def discovery(path):
    s = path.read_text(errors='replace')
    title = last(r'title=(CUSA\d+)', s, '?')
    d = re.findall(r'^DISCOVERY t=\d+ h=\S+ .*?format=(0x[0-9a-f]+) tile=(\d+) dims=(\d+x\d+) pitch=(\d+) registered=(0x[0-9a-f]+)', s, re.M)
    fmt, tile, dims, pitch, reg = d[-1] if d else ('-',) * 5
    nbuf = bin(int(reg, 16)).count('1') if reg != '-' else '-'
    callers = sorted(set(re.findall(r'CALLER api=(\w+)', s)))
    sub = last(r'SUBMITS plain=(\d+) workload_plain=(\d+)', s, ('-', '-'))
    flips = last(r'FLIP_EVENTS handle=\S+ events=(\d+)', s, '-')
    largest = last(r'DM_LARGEST rc=\S+ start=\S+ bytes=(0x[0-9a-f]+)', s, '-')
    return {'file': path.name, 'title': title, 'buffers': nbuf, 'dims': dims, 'format': fmt, 'tile': tile,
            'flip_apis': ','.join(callers) or '-', 'plain_submits': sub[0], 'flip_events': flips,
            'largest_free': f'{int(largest, 16) / 2**20:.1f} MiB' if largest != '-' else '-'}

def table(rows, cols):
    out = ['| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
    out += ['| ' + ' | '.join(str(r[c]) for c in cols) + ' |' for r in rows]
    return '\n'.join(out)

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'logs')
    fg = [fg_session(p) for p in sorted(d.glob('session_*.log'))]
    disc = [discovery(p) for p in sorted(d.glob('discovery*.log'))]
    print('# Compatibility report\n')
    if fg:
        print('## VCFG sessions\n')
        print(table(fg, ['title', 'build', 'buffers', 'format', 'res', 'register', 'fg_frames', 'native_fallback',
                         'fg_share', 'reasons', 'gates', 'stop']))
    if disc:
        print('\n## Discovery (passive)\n')
        print(table(disc, ['title', 'buffers', 'dims', 'format', 'tile', 'flip_apis', 'plain_submits', 'flip_events',
                           'largest_free']))

if __name__ == '__main__':
    main()
