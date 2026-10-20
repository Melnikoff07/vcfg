"""Cut a 4:2:0 .y4m (e.g. Xiph derf 1080p50) into benchmark triplets (see docs/BENCHMARK.md).

Usage: python -I tools/bench/y4m_to_triplets.py <in.y4m> <out_clip_dir> [--frames N]
Triplet k: A = frame 2k, GT = frame 2k+1, B = frame 2k+2 (the clip as a 25 fps source, GT is the 50 fps midpoint).
Output: NNNN_a.png / NNNN_gt.png / NNNN_b.png, 8-bit RGB (BT.601 limited range, bilinear chroma upsampling).
"""
import argparse
import pathlib

import numpy as np
from PIL import Image


def read_y4m(path, max_frames):
    f = open(path, 'rb')
    hdr = f.readline().decode().split()
    w = int([t for t in hdr if t[0] == 'W'][0][1:])
    h = int([t for t in hdr if t[0] == 'H'][0][1:])
    cs = [t for t in hdr if t[0] == 'C']
    if cs and not cs[0].startswith('C420'):
        raise SystemExit('only 4:2:0 is supported, got ' + cs[0])
    ysz, csz = w * h, (w // 2) * (h // 2)
    n = 0
    while n < max_frames:
        line = f.readline()
        if not line.startswith(b'FRAME'):
            break
        buf = f.read(ysz + 2 * csz)
        if len(buf) < ysz + 2 * csz:
            break
        a = np.frombuffer(buf, np.uint8)
        yield (a[:ysz].reshape(h, w), a[ysz:ysz + csz].reshape(h // 2, w // 2),
               a[ysz + csz:].reshape(h // 2, w // 2))
        n += 1


def up2(c):
    """bilinear x2 chroma upsampling with edge replication, MPEG-2 style (co-sited left) approximated by half-pixel phase"""
    c = c.astype(np.float32)
    p = np.pad(c, 1, mode='edge')
    h, w = c.shape
    out = np.empty((h * 2, w * 2), np.float32)
    # separable: rows then columns, weights 3/4 near, 1/4 far
    r = np.empty((h * 2, w + 2), np.float32)
    r[0::2] = 0.75 * p[1:-1] + 0.25 * p[:-2]
    r[1::2] = 0.75 * p[1:-1] + 0.25 * p[2:]
    out[:, 0::2] = 0.75 * r[:, 1:-1] + 0.25 * r[:, :-2]
    out[:, 1::2] = 0.75 * r[:, 1:-1] + 0.25 * r[:, 2:]
    return out


def to_rgb(y, u, v):
    yy = (y.astype(np.float32) - 16.0) * (255.0 / 219.0)
    uu = (up2(u) - 128.0) * (255.0 / 224.0)
    vv = (up2(v) - 128.0) * (255.0 / 224.0)
    r = yy + 1.402 * vv
    g = yy - 0.344136 * uu - 0.714136 * vv
    b = yy + 1.772 * uu
    return np.clip(np.rint(np.stack([r, g, b], -1)), 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('y4m')
    ap.add_argument('out')
    ap.add_argument('--frames', type=int, default=100)
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, (y, u, v) in enumerate(read_y4m(args.y4m, args.frames)):
        frames.append(to_rgb(y, u, v))
    nt = (len(frames) - 1) // 2
    for k in range(nt):
        for tag, idx in (('a', 2 * k), ('gt', 2 * k + 1), ('b', 2 * k + 2)):
            Image.fromarray(frames[idx]).save(out / f'{k:04d}_{tag}.png', compress_level=1)
    print(f'{len(frames)} frames -> {nt} triplets in {out}')


if __name__ == '__main__':
    main()
