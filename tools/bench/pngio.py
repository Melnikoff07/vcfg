"""PNG IO that preserves RGB/RGBA 16-bit samples (Pillow RGB loading is 8-bit)."""
import struct
import zlib

import numpy as np
from PIL import Image

MAGIC = b'\x89PNG\r\n\x1a\n'


def _chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def save16(path, rgba):
    a = np.asarray(rgba)
    if a.dtype != np.uint16 or a.ndim != 3 or a.shape[2] not in (3, 4):
        raise ValueError('expected uint16 RGB/RGBA')
    h, w, channels = a.shape
    raw = b''.join(b'\0' + row.astype('>u2').tobytes() for row in a)
    ihdr = struct.pack('>IIBBBBB', w, h, 16, 6 if channels == 4 else 2, 0, 0, 0)
    path.write_bytes(MAGIC + _chunk(b'IHDR', ihdr) + _chunk(b'IDAT', zlib.compress(raw, 3)) + _chunk(b'IEND', b''))


def load16(path):
    data = path.read_bytes()
    if data[:8] != MAGIC:
        raise ValueError('not a PNG')
    offset, compressed, header = 8, [], None
    while offset + 12 <= len(data):
        n = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 8 + n
        if end + 4 > len(data) or zlib.crc32(data[offset + 4:end]) & 0xffffffff != struct.unpack_from('>I', data, end)[0]:
            raise ValueError('truncated PNG or bad CRC')
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', data[offset + 8:end])
        if kind == b'IDAT':
            compressed.append(data[offset + 8:end])
        offset = end + 4
        if kind == b'IEND':
            break
    if header is None:
        raise ValueError('missing IHDR')
    w, h, depth, color, comp, filt, interlace = header
    if depth != 16 or color not in (2, 6) or comp or filt or interlace or not (0 < w <= 8192 and 0 < h <= 8192):
        raise ValueError('expected noninterlaced RGB/RGBA16 PNG')
    channels = 3 if color == 2 else 4
    bpp, stride = channels * 2, w * channels * 2
    expected = (stride + 1) * h
    dec = zlib.decompressobj()
    raw = dec.decompress(b''.join(compressed), expected + 1)
    if len(raw) != expected or not dec.eof:
        raise ValueError('PNG decompressed size mismatch')
    pixels = np.empty((h, stride), np.uint8)
    previous = np.zeros(stride, np.uint8)
    for y in range(h):
        pos = y * (stride + 1)
        mode = raw[pos]
        row = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=pos + 1).copy()
        if mode == 1:
            row = np.cumsum(row.reshape(-1, bpp), axis=0, dtype=np.uint32).astype(np.uint8).reshape(-1)
        elif mode == 2:
            row = (row.astype(np.uint16) + previous).astype(np.uint8)
        elif mode in (3, 4):
            for x in range(stride):
                left = int(row[x - bpp]) if x >= bpp else 0
                above = int(previous[x])
                corner = int(previous[x - bpp]) if x >= bpp else 0
                if mode == 3:
                    predict = (left + above) // 2
                else:
                    p = left + above - corner
                    ds = [abs(p - left), abs(p - above), abs(p - corner)]
                    predict = (left, above, corner)[ds.index(min(ds))]
                row[x] = (int(row[x]) + predict) & 255
        elif mode != 0:
            raise ValueError('unknown PNG filter')
        pixels[y] = row
        previous = row
    return np.frombuffer(pixels.tobytes(), dtype='>u2').astype(np.uint16).reshape(h, w, channels)


def load_rgb(path):
    with path.open('rb') as f:
        header = f.read(29)
    if header[:8] == MAGIC and len(header) >= 29 and header[24] == 16 and header[25] in (2, 6):
        return load16(path)[..., :3].astype(np.float64) / 65535.0
    with Image.open(path) as image:
        if image.mode in ('1', 'P'):
            image = image.convert('RGB')
        a = np.asarray(image)
    maximum = 65535.0 if a.dtype == np.uint16 else 255.0
    if a.ndim == 2:
        a = np.stack([a] * 3, -1)
    return a[..., :3].astype(np.float64) / maximum
