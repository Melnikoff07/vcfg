"""Self-test for tools/bench/metrics.py on a synthetic clip (moving square, static HUD bar).
Usage: python -I tools/bench/selftest.py <out_dir>   -> builds clip/, perfect/, frozen/ and prints the table.
Expected: perfect = best everywhere (PSNR 99), cross-fade worse in PSNR/TCE, frozen (= A) worse in PSNR."""
import pathlib
import subprocess
import sys

import numpy as np
from PIL import Image

W, H, N = 320, 180, 6


def frame(t):
    rng = np.random.default_rng(1)
    bg = (rng.random((H, W, 3)) * 0.5 + 0.25)
    img = bg.copy()
    x = int(20 + t * 6)                      # square moves 6 px per 60 fps frame
    img[60:110, x:x + 50] = [0.9, 0.2, 0.1]
    img[150:170, 10:120] = [0.95, 0.95, 0.95]   # HUD bar, never moves
    return (img * 255).astype(np.uint8)


def save(a, p):
    Image.fromarray(a).save(p)


def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'bench_selftest')
    clip, perfect, frozen = out / 'clip', out / 'perfect', out / 'frozen'
    for d in (clip, perfect, frozen):
        d.mkdir(parents=True, exist_ok=True)
    for i in range(N):
        t = 2 * i
        a, gt, b = frame(t), frame(t + 1), frame(t + 2)
        n = f'{i:04d}'
        save(a, clip / f'{n}_a.png'); save(gt, clip / f'{n}_gt.png'); save(b, clip / f'{n}_b.png')
        save(gt, perfect / f'{n}_mid.png'); save(a, frozen / f'{n}_mid.png')
    hud = np.zeros((H, W), np.uint8); hud[150:170, 10:120] = 255
    save(hud, clip / 'hud_mask.png')
    here = pathlib.Path(__file__).resolve().parent
    subprocess.run([sys.executable, '-I', str(here / 'metrics.py'), str(clip), str(perfect), str(frozen), '--crossfade'], check=True)


if __name__ == '__main__':
    main()
