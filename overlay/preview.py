"""Renders the in-game overlay on the PC with the console drawing code: python -I overlay/preview.py <out_dir>"""
import ctypes, pathlib, sys
import numpy as np
from PIL import Image
ROOT = pathlib.Path(__file__).resolve().parents[1]
out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
dll = ctypes.CDLL(str(ROOT / 'build' / 'preview' / 'vov_preview.dll'))
mem = ctypes.create_string_buffer(4 << 20)
bg = np.zeros((760, 1100, 3), np.uint8); bg[:] = (59, 69, 55); bg[480:] = (105, 122, 90)
for scene, name in [(0, 'menu'), (1, 'counter_detail'), (2, 'counter_small'), (3, 'menu_status'), (4, 'menu_small_memory'), (5, 'menu_hdr_raw')]:
    pal, pix, w, h = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_uint(), ctypes.c_uint()
    dll.vov_preview(mem, scene, ctypes.byref(pal), ctypes.byref(pix), ctypes.byref(w), ctypes.byref(h))
    P = np.frombuffer(ctypes.string_at(pal.value, 1024), np.uint8).reshape(256, 4)
    I = np.frombuffer(ctypes.string_at(pix.value, w.value * h.value), np.uint8).reshape(h.value, w.value)
    rgba = P[I]; rgb = rgba[..., [2, 1, 0]].astype(float); a = (rgba[..., 3:4] / 255.0)   # stored B,G,R,A
    img = bg.copy().astype(float); y0, x0 = 40, 40
    img[y0:y0 + h.value, x0:x0 + w.value] = img[y0:y0 + h.value, x0:x0 + w.value] * (1 - a) + rgb * a
    Image.fromarray(img.astype(np.uint8)).save(out / f'{name}.png'); print(name, w.value, h.value, 'palette 1..5 (B,G,R):', [tuple(P[i][:3]) for i in range(1, 6)])
