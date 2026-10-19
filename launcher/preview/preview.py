"""Render launcher screens on the PC with the exact console drawing code (vcfg_preview.dll).
Usage: python -I launcher/preview/preview.py <out_dir> [title=id ...]
Icons are generated placeholders (no game artwork in the repository); titles default to generic examples.
"""
import ctypes, pathlib, sys
import numpy as np
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FONTS = HERE.parent / 'assets' / 'fonts'

def placeholder(seed, n=256):
    rng = np.random.default_rng(seed)
    pal = np.array([[11, 11, 11], [242, 80, 46], [245, 210, 58], [79, 134, 230], [239, 238, 232]], np.uint8)
    cells = rng.integers(0, 5, (8, 8))
    img = pal[cells].repeat(n // 8, 0).repeat(n // 8, 1)
    return np.dstack([img, np.full((n, n), 255, np.uint8)]).copy()

def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'preview_out'); out.mkdir(parents=True, exist_ok=True)
    pairs = [a.split('=', 1) for a in sys.argv[2:]] or [['Example game one', 'CUSA00001'], ['Example game with a long title', 'CUSA00002'],
                                                        ['Example three', 'CUSA00003'], ['Example four', 'CUSA00004'], ['Example five', 'CUSA00005']]
    dll = ctypes.CDLL(str(ROOT / 'build' / 'preview' / 'vcfg_preview.dll'))
    logo = (FONTS / 'InterDisplay-Medium.ttf').read_bytes(); text = (FONTS / 'Inter-Medium.ttf').read_bytes()
    mono = (FONTS / 'JetBrainsMono-Medium.ttf').read_bytes()
    n = len(pairs)
    titles = (ctypes.c_char_p * n)(*[p[0].encode() for p in pairs]); ids = (ctypes.c_char_p * n)(*[p[1].encode() for p in pairs])
    icons_np = [placeholder(i) for i in range(n)]
    icons = (ctypes.c_void_p * n)(*[ic.ctypes.data for ic in icons_np])
    iw = (ctypes.c_int * n)(*[256] * n); ih = (ctypes.c_int * n)(*[256] * n)
    W, H = 1920, 1080
    for scene, name in [(0, 'games'), (1, 'game_settings'), (2, 'defaults'), (3, 'overlay'), (4, 'install'), (5, 'games_scrolled')]:
        fb = np.zeros((H, W), np.uint32); mem = ctypes.create_string_buffer(64 << 20)
        used = dll.vcfg_preview(fb.ctypes.data_as(ctypes.c_void_p), W, H, logo, text, mono, mem, ctypes.c_size_t(64 << 20), scene, n,
                                titles, ids, icons, iw, ih)
        rgb = np.dstack([(fb >> 16) & 255, (fb >> 8) & 255, fb & 255]).astype(np.uint8)
        Image.fromarray(rgb).save(out / f'{scene}_{name}.png')
        print(name, 'arena bytes', used)

if __name__ == '__main__':
    main()
