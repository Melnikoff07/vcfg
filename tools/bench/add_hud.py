"""Overlay a synthetic static HUD on every frame of a benchmark clip (see docs/BENCHMARK.md).

Usage: python -I tools/bench/add_hud.py <clip_dir> <out_clip_dir>
The same overlay is composited on A, GT and B, so every HUD pixel is "static" while the scene moves under the
semi-transparent parts (minimap, subtitle bar), like in a game. Writes hud_mask.png (white = any HUD pixel).
Elements (1920x1080): opaque health / stamina bars (top left), text block (top right), round semi-transparent
minimap with a cross and a marker (bottom left), ammo counter (bottom right), subtitle bar with text (bottom centre).
"""
import argparse
import pathlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def font(size):
    for p in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_overlay(w=1920, h=1080):
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # health / stamina bars (opaque)
    d.rectangle([60, 50, 460, 74], fill=(20, 20, 20, 255), outline=(240, 240, 240, 255), width=2)
    d.rectangle([64, 54, 380, 70], fill=(200, 40, 40, 255))
    d.rectangle([60, 86, 360, 104], fill=(20, 20, 20, 255), outline=(240, 240, 240, 255), width=2)
    d.rectangle([64, 90, 300, 100], fill=(60, 180, 70, 255))
    # text block top right
    f = font(30)
    d.text((1560, 50), 'QUEST: Find the lighthouse', font=f, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
    d.text((1560, 92), 'Distance 1240 m', font=f, fill=(255, 220, 120, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
    # minimap (semi-transparent disc)
    cx, cy, r = 190, 880, 130
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(10, 20, 40, 150), outline=(255, 255, 255, 255), width=3)
    d.line([cx - r + 10, cy, cx + r - 10, cy], fill=(255, 255, 255, 110), width=1)
    d.line([cx, cy - r + 10, cx, cy + r - 10], fill=(255, 255, 255, 110), width=1)
    d.polygon([(cx, cy - 14), (cx - 9, cy + 10), (cx + 9, cy + 10)], fill=(255, 210, 0, 255))
    d.ellipse([cx + 55, cy - 60, cx + 67, cy - 48], fill=(230, 50, 50, 255))
    # ammo counter
    d.text((1640, 960), '24 / 120', font=font(64), fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
    # subtitle bar (semi-transparent) with text
    d.rectangle([520, 940, 1400, 1010], fill=(0, 0, 0, 140))
    d.text((560, 953), 'Hold on, the bridge is down. We go around.', font=font(36), fill=(255, 255, 255, 255))
    return np.asarray(im).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('clip')
    ap.add_argument('out')
    args = ap.parse_args()
    src, dst = pathlib.Path(args.clip), pathlib.Path(args.out)
    dst.mkdir(parents=True, exist_ok=True)
    ov = make_overlay()
    alpha = ov[..., 3:4] / 255.0
    rgb = ov[..., :3]
    Image.fromarray(((alpha[..., 0] > 0) * 255).astype(np.uint8)).save(dst / 'hud_mask.png')
    n = 0
    for p in sorted(src.glob('*_*.png')):
        a = np.asarray(Image.open(p).convert('RGB')).astype(np.float32)
        o = np.clip(np.rint(a * (1 - alpha) + rgb * alpha), 0, 255).astype(np.uint8)
        Image.fromarray(o).save(dst / p.name, compress_level=1)
        n += 1
    print(f'{n} frames -> {dst}')


if __name__ == '__main__':
    main()
