"""Render launcher screens on the PC with the exact console drawing code (vcfg_preview.dll).
Usage: python -I launcher/preview/preview.py <out_dir> [title=id ...]
"""
import ctypes, pathlib, sys, struct, zlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FONTS = HERE.parent / 'assets' / 'fonts'

def write_png(path, fb_bytes, w, h):
    raw = bytearray()
    mv = memoryview(fb_bytes)
    for y in range(h):
        raw.append(0)
        row = mv[y * w * 4:(y + 1) * w * 4]
        for x in range(0, w * 4, 4):
            raw.extend((row[x + 2], row[x + 1], row[x + 0]))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    pathlib.Path(path).write_bytes(png)

def main():
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'preview_out'); out.mkdir(parents=True, exist_ok=True)
    pairs = [a.split('=', 1) for a in sys.argv[2:]] or [['Example game one', 'CUSA00001'], ['Example game with a long title', 'CUSA00002'],
                                                        ['Example three', 'CUSA00003'], ['Example four', 'CUSA00004'], ['Example five', 'CUSA00005']]
    dll = ctypes.CDLL(str(ROOT / 'build' / 'preview' / 'vcfg_preview.dll'))
    logo = (FONTS / 'InterDisplay-Medium.ttf').read_bytes(); text = (FONTS / 'Inter-Medium.ttf').read_bytes()
    mono = (FONTS / 'JetBrainsMono-Medium.ttf').read_bytes()
    n = len(pairs)
    titles = (ctypes.c_char_p * n)(*[p[0].encode() for p in pairs]); ids = (ctypes.c_char_p * n)(*[p[1].encode() for p in pairs])
    icon_buf = (ctypes.c_ubyte * (256 * 256 * 4))(*([40, 80, 140, 255] * (256 * 256)))
    icons = (ctypes.c_void_p * n)(*[ctypes.cast(icon_buf, ctypes.c_void_p) for _ in range(n)])
    iw = (ctypes.c_int * n)(*[256] * n); ih = (ctypes.c_int * n)(*[256] * n)
    W, H = 1920, 1080
    for scene, name in [(0, 'games'), (1, 'game_settings'), (2, 'defaults'), (3, 'overlay'), (4, 'install'), (5, 'games_scrolled'),
                        (6, 'dev_compact_dark'), (7, 'dev_expanded_dark'), (8, 'dev_compact_light'), (9, 'dev_expanded_light')]:
        fb = (ctypes.c_uint32 * (W * H))()
        mem = ctypes.create_string_buffer(64 << 20)
        used = dll.vcfg_preview(fb, W, H, logo, text, mono, mem, ctypes.c_size_t(64 << 20), scene, n,
                                titles, ids, icons, iw, ih)
        write_png(out / f'{scene}_{name}.png', bytes(fb), W, H)
        print(name, 'arena bytes', used)

if __name__ == '__main__':
    main()
