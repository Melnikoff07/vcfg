import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import unittest
import zlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = ROOT / 'tools/bench'
sys.path.insert(0, str(HERE))
import pngio
import metrics
spec = importlib.util.spec_from_file_location('bench_fetch', HERE / 'fetch.py')
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)
TEMP = ROOT / 'build/tests'
TEMP.mkdir(parents=True, exist_ok=True)


def meta(sample=0):
    return dict(schema=1, build='capture-v1', source_hash='0' * 64, title='CUSA00557', sample=sample,
                width=4, height=3, plane_bytes=4 * 3 * 8, encoding='rgba16le', frames=[9, 10, 11],
                submit_us=[100000, 116683, 133366], timing_valid=True, phase_q16=32768,
                generated_flipped=False, history='cold', format=0x88000000, tiling=0, pitch=1920,
                profile={'preset': 0}, crop_x=0, crop_y=0)


def make_raw(raw, sample=0):
    raw.mkdir(parents=True, exist_ok=True)
    m = meta(sample)
    (raw / f'bench_{sample:04d}.json').write_text(json.dumps(m))
    a = (np.arange(48, dtype=np.uint16) * 1031).reshape(3, 4, 4)
    a[0, 0] = [0, 1, 65534, 65535]
    for part in fetch.PARTS:
        (raw / f'bench_{sample:04d}_{part}.rgba').write_bytes(a.astype('<u2').tobytes())
    return a


class BenchmarkTest(unittest.TestCase):
    def test_16bit_roundtrip_and_metric_low_bits(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            p = pathlib.Path(d) / '16.png'
            a = np.full((12, 12, 4), 40000, np.uint16)
            a[..., 3] = 65535
            pngio.save16(p, a)
            np.testing.assert_array_equal(pngio.load16(p), a)
            x = metrics.load(p)
            a[..., :3] += 1
            pngio.save16(p, a)
            y = metrics.load(p)
            np.testing.assert_allclose(y - x, 1 / 65535, rtol=0, atol=1e-15)
            self.assertAlmostEqual(metrics.psnr(x, y), 20 * np.log10(65535), places=8)

    def test_png_crc_is_checked(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            p = pathlib.Path(d) / 'bad.png'
            pngio.save16(p, np.zeros((2, 2, 4), np.uint16))
            data = bytearray(p.read_bytes()); data[-5] ^= 1; p.write_bytes(data)
            with self.assertRaises(ValueError):
                pngio.load16(p)

    def test_all_png_filters_preserve_16bit(self):
        original = (np.arange(48, dtype=np.uint16) * 1027).reshape(3, 4, 4)
        rows = original.astype('>u2').view(np.uint8).reshape(3, -1)
        for mode in range(5):
            encoded = bytearray()
            for y, row in enumerate(rows):
                encoded.append(mode)
                for x, value in enumerate(row):
                    a = int(row[x - 8]) if x >= 8 else 0
                    b = int(rows[y - 1, x]) if y else 0
                    c = int(rows[y - 1, x - 8]) if y and x >= 8 else 0
                    p = a + b - c
                    distances = [abs(p - a), abs(p - b), abs(p - c)]
                    predict = [0, a, b, (a + b) // 2, (a, b, c)[distances.index(min(distances))]][mode]
                    encoded.append((int(value) - predict) & 255)
            with tempfile.TemporaryDirectory(dir=TEMP) as d:
                p = pathlib.Path(d) / 'filter.png'
                ihdr = struct.pack('>IIBBBBB', 4, 3, 16, 6, 0, 0, 0)
                p.write_bytes(pngio.MAGIC + pngio._chunk(b'IHDR', ihdr) + pngio._chunk(b'IDAT', zlib.compress(encoded)) + pngio._chunk(b'IEND', b''))
                np.testing.assert_array_equal(pngio.load16(p), original)

    def test_fetch_end_to_end_layout_and_precision(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            root = pathlib.Path(d)
            a = make_raw(root / 'raw')
            clip, method, count = fetch.convert(root / 'raw', root / 'bench', 'slow_pan')
            self.assertEqual(count, 1)
            np.testing.assert_array_equal(pngio.load16(clip / '0000_gt.png'), a)
            np.testing.assert_array_equal(pngio.load16(method / '0000_mid.png'), a)
            self.assertEqual(json.loads((clip / 'capture.json').read_text())['triplets'][0]['metadata']['frames'], [9, 10, 11])
            with self.assertRaises(FileExistsError):
                fetch.convert(root / 'raw', root / 'bench', 'slow_pan')

    def test_partial_raw_does_not_publish_a_clip(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            root = pathlib.Path(d); make_raw(root / 'raw')
            (root / 'raw/bench_0000_mid.rgba').write_bytes(b'bad')
            with self.assertRaises(ValueError):
                fetch.convert(root / 'raw', root / 'bench', 'broken')
            self.assertFalse((root / 'bench/clips/CUSA00557_broken').exists())

    def test_missing_mid_cannot_improve_score_by_skipping_a_hard_frame(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            root = pathlib.Path(d)
            raw = root / 'raw'; make_raw(raw)
            clip, method, _ = fetch.convert(raw, root / 'bench', 'complete')
            with self.assertRaises(FileNotFoundError):
                metrics.evaluate(clip, root / 'absent_method')

    def test_incorrect_pair_timing_and_visible_fg_are_refused(self):
        m = meta(); m['submit_us'][1] += 10000
        with self.assertRaises(ValueError):
            fetch.metadata(json.dumps(m))
        m = meta(); m['generated_flipped'] = True
        with self.assertRaises(ValueError):
            fetch.metadata(json.dumps(m))
        m = meta(); m['frames'] = [9, 11, 12]
        with self.assertRaises(ValueError):
            fetch.metadata(json.dumps(m))


if __name__ == '__main__':
    unittest.main()
