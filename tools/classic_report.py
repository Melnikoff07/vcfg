"""Summarize saved classic traces without treating a snapshot as a complete session.

python -I tools/classic_report.py logs/console --out docs/LOCAL_TRACE_REPORT.md
Only trace files are used for the title matrix. Full logs can contain multiple
starts/settings and must not be silently combined with the first/last trace rows.
"""
import argparse
import collections
import pathlib
import re
import statistics


def last(pattern, text, default='-'):
    values = re.findall(pattern, text, re.M)
    return values[-1] if values else default


def parse_trace(path):
    text = path.read_text(encoding='utf-8', errors='replace')
    title = last(r'^title: (CUSA\d{5})$', text, last(r'title[= ](CUSA\d{5})', text, '?'))
    attr = last(r'attr: fmt=(0x[0-9a-fA-F]+).*? (\d+x\d+) pitch=(\d+)', text, ('-', '-', '-'))
    cadence = last(r'CADENCE .*?mid 1:(\d+) 2:(\d+) 3:(\d+) 4\+:(\d+) \| real 1:(\d+) 2:(\d+) 3:(\d+) 4\+:(\d+)', text, None)
    stats = [float(x) for x in re.findall(r'STAT gnm: ([\d.]+) flips/s', text)]
    # Last five retained samples: this is not the whole session and can be a menu.
    tail = stats[-5:]
    lat = last(r'LATENCY .*?avg ([\d.]+) max ([\d.]+) ms.*?\| FG (on|off)', text, None)
    inert = last(r"inert='([^']*)'", text, None)
    if inert is None:
        state = 'unknown (no decision retained)'
    elif inert:
        state = inert
    else:
        state = 'no startup refusal (visual result unverified)'
    if 'inject skipped: tail marker not found' in text:
        state = 'INERT: NO FLIP TAIL'
    if lat and lat[2] == 'off':
        state += '; latest FG off'
    build = last(r'CLASSIC_BUILD (.+)', text, 'not recorded')
    return dict(title=title, file=path.name, engine=last(r'^engine: (\S+)', text),
                version=last(r'^version: (\S+)', text), build=build,
                buffers=last(r'RegisterBuffers handle=\d+ start=0 num=(\d+)', text),
                format=attr[0], resolution=attr[1], state=state,
                fps=f'{min(tail):.1f}–{max(tail):.1f} (median {statistics.median(tail):.1f})' if tail else '-',
                cadence=('mid ' + '/'.join(cadence[:4]) + '; real ' + '/'.join(cadence[4:])) if cadence else '-',
                missed=last(r'CADENCE .*?missed (\d+)', text),
                pending=last(r'CADENCE .*?pending max (\d+)', text),
                latency=(f'{lat[0]} / {lat[1]} ({lat[2]})' if lat else '-'),
                wait=last(r'WAIT_API totals (.+)', text),
                test=last(r'CLASSIC_BUILD .*?test=(\d+)', text),
                cpu=last(r'game cpu release->submit avg ([\d.]+)', text),
                gpu=last(r'GPU time of our passes: .*?= ([\d.]+) ms', text),
                hook=last(r'HOOK ms per FG frame: (.+)', text),
                pacing=last(r'pacing: (\w+)', text, 'not recorded'),
                copies='available' if 'present copy available' in text else 'not recorded',
                truncated=sum(len(line) >= 198 for line in text.splitlines()))


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def table(rows, columns):
    return '\n'.join(['| ' + ' | '.join(c for c in columns) + ' |', '|' + '---|' * len(columns)] +
                     ['| ' + ' | '.join(cell(r.get(c, '-')) for c in columns) + ' |' for r in rows])


def report(folder):
    current = [parse_trace(p) for p in sorted(folder.glob('trace-CUSA*.txt')) if not p.name.endswith('.prev.txt')]
    previous = [parse_trace(p) for p in sorted(folder.glob('trace-CUSA*.prev.txt'))]
    configured = {p.stem.removeprefix('config-') for p in folder.glob('config-CUSA*.ini')}
    observed = {r['title'] for r in current}
    cols = ['title', 'version', 'buffers', 'format', 'resolution', 'fps', 'cadence', 'missed', 'pending', 'latency', 'state']
    lines = ['# Local trace snapshot report', '',
             'Saved evidence only; no hardware tests were performed by this report. FPS uses the last five retained STAT samples. '
             'Cadence is the last retained window, with bins 1/2/3/4+ vblanks; latency is average / maximum ms. '
             'These windows are asynchronous. Startup and tail can describe different menus/settings. '
             'Missed observations make cadence incomplete; no automatic WORKS or CRASHED verdict is inferred.', '',
             '## Current trace files', '', table(current, cols), '',
             '## Previous trace files (separate starts)', '', table(previous, cols), '',
             '## Build attribution and evidence limits', '',
             table(current, ['title', 'file', 'engine', 'build', 'truncated', 'wait']), '',
             'truncated counts retained rows at the flight recorder width; their missing fields cannot be recovered.', '',
             f'Configured titles: {len(configured)}. Current traces: {len(observed)}. No current trace: {len(configured - observed)}.', '',
             '## One-minute tests after the two-buffer comparison', '',
             'For each title: enable only its own profile, start gameplay, open/close the menu, turn the camera, '
             'and collect a snapshot before moving to the next title. Record artifacts and input feel separately. '
             'A missing trace is untested, not a crash.', '',
             table([{'title': t, 'test': 'gameplay + overlay + trace; classify format/buffers/resolution/inert'} for t in sorted(configured - observed)], ['title', 'test'])]
    reasons = collections.Counter(r['state'] for r in current)
    lines += ['', '## Retained startup decisions', ''] + [f'- {v} trace(s): {k}' for k, v in reasons.items()]
    return '\n'.join(lines) + '\n'


def session_report(folder):
    rows = [parse_trace(p) for p in sorted(folder.glob('*.log'))]
    return ('\n## Archived full logs (each file kept separate)\n\n'
            'Files may include settings changes and overlap with snapshots; sample ranges are not controlled benchmarks. '
            'Copies describes availability, not whether present_copy was enabled. '
            'Pacing says only what the file explicitly logged. CPU and GPU are the last retained measurements in ms.\n\n' +
            table(rows, ['file', 'title', 'buffers', 'copies', 'pacing', 'fps', 'cpu', 'gpu', 'missed', 'latency']) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('folder', type=pathlib.Path)
    ap.add_argument('--out', type=pathlib.Path)
    ap.add_argument('--sessions', type=pathlib.Path, help='optional directory of archived full logs')
    args = ap.parse_args()
    if not args.folder.is_dir():
        ap.error('snapshot directory not found')
    result = report(args.folder)
    if args.sessions:
        if not args.sessions.is_dir():
            ap.error('session directory not found')
        result += session_report(args.sessions)
    if args.out:
        args.out.write_text(result, encoding='utf-8')
    else:
        import sys
        sys.stdout.reconfigure(encoding='utf-8')
        print(result, end='')


if __name__ == '__main__':
    main()
