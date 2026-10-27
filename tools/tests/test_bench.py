import importlib.util
import json
import pathlib
import struct
import subprocess
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

    def test_sequence_capture_n32_host_and_fetch(self):
        import analyze_seq_fgkill as asf
        r = subprocess.run([sys.executable, '-I', str(HERE / 'check_capture.py')],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=f'check_capture.py failed (rc={r.returncode}):\n{r.stdout}\n{r.stderr}')
        self.assertIn('seq2 N=32', r.stdout)
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            root = pathlib.Path(d)
            raw = root / 'raw'
            raw.mkdir(parents=True, exist_ok=True)
            anchor_steps = [0, 31]
            frames_native = {
                s: ((np.arange(48, dtype=np.uint16) + s * 17) * 521).reshape(3, 4, 4)
                for s in [0, 1, 31, 32]
            }
            frames_mid = {
                s: ((np.arange(48, dtype=np.uint16) + s * 31 + 7) * 521).reshape(3, 4, 4)
                for s in anchor_steps
            }
            for s in range(32):
                buf = bytearray(asf.BENCH_SEQ_STEP_BYTES)
                fgmask = np.zeros((68, 120), dtype=np.uint8)
                # Toggle 48 of the 480 cells in ROI head_tight (gy=14..33, gx=48..71) on odd steps -> 10% flip rate
                if s % 2 == 1:
                    fgmask[14:16, 48:72] = asf.FGMASK_HASFG | asf.FGMASK_FGKILL_WS | asf.FGMASK_FGKILL
                buf[asf.OFF_FGMASK:asf.OFF_FGMASK + asf.LEN_FGMASK] = fgmask.tobytes()
                (raw / f'bench_seq_{s:04d}.bin').write_bytes(bytes(buf))
                (raw / f'bench_seq_{s:04d}.json').write_text(json.dumps({
                    'schema': 2, 'mode': 'seq_small', 'title': 'CUSA08519', 'step': s,
                    'flip': 300 + s, 'step_bytes': asf.BENCH_SEQ_STEP_BYTES,
                    'anchor_triplet': s in anchor_steps,
                }))
            for s in anchor_steps:
                m = meta(s)
                m['title'] = 'CUSA08519'
                m['frames'] = [300 + s, 301 + s, 302 + s]
                t0 = 100000 + s * 33366
                m['submit_us'] = [t0, t0 + 33366, t0 + 2 * 33366]
                (raw / f'bench_{s:04d}.json').write_text(json.dumps(m))
                (raw / f'bench_{s:04d}_a.rgba').write_bytes(frames_native[s].astype('<u2').tobytes())
                (raw / f'bench_{s:04d}_gt.rgba').write_bytes(frames_native[s + 1].astype('<u2').tobytes())
                (raw / f'bench_{s:04d}_b.rgba').write_bytes(frames_native[s + 1].astype('<u2').tobytes())
                (raw / f'bench_{s:04d}_mid.rgba').write_bytes(frames_mid[s].astype('<u2').tobytes())
            clip, method, count = fetch.convert(raw, root / 'bench', 'seq32_head')
            self.assertEqual(count, 2)
            for s in anchor_steps:
                np.testing.assert_array_equal(pngio.load16(clip / f'{s:04d}_a.png'), frames_native[s])
                np.testing.assert_array_equal(pngio.load16(clip / f'{s:04d}_b.png'), frames_native[s + 1])
                np.testing.assert_array_equal(pngio.load16(method / f'{s:04d}_mid.png'), frames_mid[s])
            rep = asf.analyze_sequence(raw)
            self.assertEqual(rep['steps'], 32)
            self.assertAlmostEqual(rep['summary']['fgkill_flip_rate_cell_fixed'], 48.0 / 480.0, places=6)
            self.assertAlmostEqual(rep['summary']['fgkill_flip_rate_cell_active_union'], 1.0, places=6)
            self.assertGreater(rep['summary']['fgkill_flip_rate_pixel_fixed'], 0.08)

    def test_flicker_mechanism_and_fix_models(self):
        import analyze_seq_fgkill as asf
        import model_seq_flicker_fixes as msf

        # Verify single unified VARIANT_SPECS table drives both IR patching and Python evaluation
        for vkey in (
            'V1a_ir_gk_nonzero_only',
            'V1b_ir_gk_nonzero_plus_slw_le2_hold',
            'V2_ir_1step_F_prev',
            'V3_ir_1step_F_prev',
            'V4_ir_coh_only_v3',
            'V4b_ir_coh_vmn_v3',
            'V5_ir_vmc_fgs8_slw_ramp',
            'V5b_ir_vmc_gated_fgs8_slw_ramp',
        ):
            self.assertIn(vkey, msf.VARIANT_SPECS)
            spec = msf.VARIANT_SPECS[vkey]
            self.assertTrue(callable(spec['patch_fgs8']))
            self.assertTrue(callable(spec['patch_warp8']))

        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            raw = pathlib.Path(d) / 'seq_osc'
            raw.mkdir(parents=True, exist_ok=True)
            for s in range(8):
                buf = bytearray(asf.BENCH_SEQ_STEP_BYTES)
                v2 = np.zeros((34, 60, 2), dtype='<i2')
                anc = np.zeros((34, 60), dtype=np.uint8)
                gk = np.zeros((68, 120), dtype='<u4')
                fgmask = np.zeros((68, 120), dtype=np.uint8)
                fcrop = np.zeros((28, 36, 2), dtype='<f4')
                odd = (s % 2 == 1)
                v2[18, 24] = [2, 0] if odd else [1, 1]
                anc[18, 24] = 0 if odd else 2
                prio = 2 if odd else 0
                src_l2 = 18 * 60 + 24
                for gy in (18, 36):
                    gx = 50
                    gk[gy, gx] = (prio << 20) | (240 << 12) | src_l2
                    c_gy = gy - asf.FCROP_GY0
                    c_gx = gx - asf.FCROP_GX0
                    fx = 8.4 if odd else 7.6
                    fcrop[c_gy, c_gx] = [fx, 0.0]
                    hasfg = prio >= 1
                    vmg_hi = fx > 8.0
                    mov_obj = hasfg or vmg_hi
                    mfit = True
                    bits = (
                        (asf.FGMASK_HASFG if hasfg else 0)
                        | asf.FGMASK_ISANC
                        | (asf.FGMASK_VMG_HI if vmg_hi else 0)
                        | (asf.FGMASK_MOV_OBJ if mov_obj else 0)
                        | (asf.FGMASK_MFIT if mfit else 0)
                        | (asf.FGMASK_FGKILL_WS if (hasfg and mfit) else 0)
                        | (asf.FGMASK_FGKILL if (mov_obj and mfit) else 0)
                    )
                    fgmask[gy, gx] = bits
                buf[asf.OFF_ANC:asf.OFF_ANC + asf.LEN_ANC] = anc.tobytes()
                buf[asf.OFF_V2:asf.OFF_V2 + asf.LEN_V2] = v2.tobytes()
                buf[asf.OFF_GK:asf.OFF_GK + asf.LEN_GK] = gk.tobytes()
                buf[asf.OFF_FGMASK:asf.OFF_FGMASK + asf.LEN_FGMASK] = fgmask.tobytes()
                buf[asf.OFF_FCROP:asf.OFF_FCROP + asf.LEN_FCROP] = fcrop.tobytes()
                (raw / f'bench_seq_{s:04d}.bin').write_bytes(bytes(buf))
            syn = msf.evaluate_sequence_models(raw)
            base_flips = syn['variants']['baseline']['f_head_crop_box']['fgkill_flips']
            self.assertEqual(base_flips, 14)
            for vkey in (
                'V1a_ir_gk_nonzero_only',
                'V2_ir_1step_F_prev',
                'V3_ir_1step_F_prev',
                'V5_ir_vmc_fgs8_slw_ramp',
            ):
                self.assertEqual(syn['variants'][vkey]['f_head_crop_box']['fgkill_flips'], 0)
            for vkey, spec in msf.VARIANT_SPECS.items():
                self.assertEqual(
                    spec['patch_fgs8'] is not msf.patch_noop,
                    'fgs8.ll' in spec['shader_target'],
                    msg=f"{vkey} patch_fgs8 mismatch with shader_target",
                )

    def test_real_capture_flicker_models(self):
        import os
        import model_seq_flicker_fixes as msf

        env_dir = os.environ.get('VCFG_OWNER_SEQ_DIR')
        candidates = []
        if env_dir:
            candidates.append(pathlib.Path(env_dir))
        candidates.extend([
            pathlib.Path('/home/melnikoff/vcfg-pipeline/runtime/research/owner-seq-8baa93b/bench_CUSA08519_00000003d068b20a'),
            pathlib.Path(r'C:\vcfg-work\owner-seq-8baa93b\bench_CUSA08519_00000003d068b20a'),
        ])
        owner_seq = next((p for p in candidates if p.is_dir()), None)
        if owner_seq is None:
            self.skipTest(
                'Real console sequence capture not found; checked VCFG_OWNER_SEQ_DIR and '
                + ', '.join(str(p) for p in candidates)
            )

        res = msf.evaluate_sequence_models(owner_seq)
        self.assertEqual(res['steps'], 32)
        self.assertEqual(res['baseline_reconstruction']['f_head_crop_exact_cell_step_matches'], 32 * 28 * 36)

        # Verify exact parity with independent audit_44cc9a9_results.json and REVIEW_e63de82.md
        v = res['variants']
        self.assertEqual(v['baseline']['f_head_crop_box']['fgkill_flips'], 1441)
        self.assertEqual(v['baseline']['f_head_crop_box']['fgkill_ws_flips'], 3224)
        self.assertEqual(v['baseline']['head_tight']['fgkill_flips'], 233)
        self.assertEqual(v['baseline']['head_tight']['fgkill_ws_flips'], 1146)

        self.assertEqual(v['V1a_ir_gk_nonzero_only']['f_head_crop_box']['fgkill_flips'], 1240)
        self.assertEqual(v['V1a_ir_gk_nonzero_only']['f_head_crop_box']['fgkill_ws_flips'], 3308)
        self.assertEqual(v['V1a_ir_gk_nonzero_only']['head_tight']['fgkill_flips'], 203)
        self.assertEqual(v['V1a_ir_gk_nonzero_only']['head_tight']['fgkill_ws_flips'], 1130)

        self.assertEqual(v['V1b_ir_gk_nonzero_plus_slw_le2_hold']['f_head_crop_box']['fgkill_flips'], 1039)
        self.assertEqual(v['V1b_ir_gk_nonzero_plus_slw_le2_hold']['f_head_crop_box']['fgkill_ws_flips'], 3571)
        self.assertEqual(v['V1b_ir_gk_nonzero_plus_slw_le2_hold']['head_tight']['fgkill_flips'], 193)
        self.assertEqual(v['V1b_ir_gk_nonzero_plus_slw_le2_hold']['head_tight']['fgkill_ws_flips'], 1196)

        self.assertEqual(v['V2_ir_1step_F_prev']['f_head_crop_box']['fgkill_flips'], 1046)
        self.assertEqual(v['V2_ir_1step_F_prev']['f_head_crop_box']['fgkill_ws_flips'], 2149)
        self.assertEqual(v['V2_ir_1step_F_prev']['head_tight']['fgkill_flips'], 169)
        self.assertEqual(v['V2_ir_1step_F_prev']['head_tight']['fgkill_ws_flips'], 475)

        self.assertEqual(v['V3_ir_1step_F_prev']['f_head_crop_box']['fgkill_flips'], 910)
        self.assertEqual(v['V3_ir_1step_F_prev']['f_head_crop_box']['fgkill_ws_flips'], 946)
        self.assertEqual(v['V3_ir_1step_F_prev']['head_tight']['fgkill_flips'], 105)
        self.assertEqual(v['V3_ir_1step_F_prev']['head_tight']['fgkill_ws_flips'], 112)

        self.assertEqual(v['V4b_ir_coh_vmn_v3']['f_head_crop_box']['fgkill_flips'], 622)
        self.assertEqual(v['V4b_ir_coh_vmn_v3']['f_head_crop_box']['fgkill_ws_flips'], 656)
        self.assertEqual(v['V4b_ir_coh_vmn_v3']['head_tight']['fgkill_flips'], 60)
        self.assertEqual(v['V4b_ir_coh_vmn_v3']['head_tight']['fgkill_ws_flips'], 69)

        self.assertEqual(v['V4c_ir_coh_vmn_warp8_only']['f_head_crop_box']['fgkill_flips'], 739)
        self.assertEqual(v['V4c_ir_coh_vmn_warp8_only']['f_head_crop_box']['fgkill_ws_flips'], 778)
        self.assertEqual(v['V4c_ir_coh_vmn_warp8_only']['head_tight']['fgkill_flips'], 68)
        self.assertEqual(v['V4c_ir_coh_vmn_warp8_only']['head_tight']['fgkill_ws_flips'], 77)

        self.assertEqual(v['V5_ir_vmc_fgs8_slw_ramp']['f_head_crop_box']['fgkill_flips'], 427)
        self.assertEqual(v['V5_ir_vmc_fgs8_slw_ramp']['f_head_crop_box']['fgkill_ws_flips'], 427)
        self.assertEqual(v['V5_ir_vmc_fgs8_slw_ramp']['head_tight']['fgkill_flips'], 21)
        self.assertEqual(v['V5_ir_vmc_fgs8_slw_ramp']['head_tight']['fgkill_ws_flips'], 21)

        self.assertEqual(v['V5b_ir_vmc_gated_fgs8_slw_ramp']['f_head_crop_box']['fgkill_flips'], 513)
        self.assertEqual(v['V5b_ir_vmc_gated_fgs8_slw_ramp']['f_head_crop_box']['fgkill_ws_flips'], 513)
        self.assertEqual(v['V5b_ir_vmc_gated_fgs8_slw_ramp']['head_tight']['fgkill_flips'], 29)
        self.assertEqual(v['V5b_ir_vmc_gated_fgs8_slw_ramp']['head_tight']['fgkill_ws_flips'], 29)

        vcfg_warp_src = (ROOT / 'tools/bench/vcfg_warp.py').read_text(encoding='utf-8')
        self.assertIn('vmg_e = np.maximum(vm_c, F32(0.94000000) * vm_p)', vcfg_warp_src)

        # Verify HUD-safe ws0 damping in patch_v4b_warp8 and patch_v5_warp8 (so w_hud is not zeroed by ws_keep)
        warp8_src = (ROOT / 'classic/shaders/warp8.ll').read_text(encoding='utf-8')
        for patch_fn in (msf.patch_v4b_warp8, msf.patch_v5_warp8):
            patched = patch_fn(warp8_src)
            self.assertIn('%ws0_s = fmul float %ws0, %ws_keep', patched)
            self.assertIn('%ws = call float @llvm.maxnum.f32(float %ws0_s, float %w_hud)', patched)
            self.assertNotIn('%ws = fmul float %ws0_h, %ws_keep', patched)

        # Verify on seq4 (owner-seq4-24d7b38d) if present
        seq4_candidates = [
            pathlib.Path('/home/melnikoff/vcfg-pipeline/runtime/research/owner-seq4-24d7b38d/bench_CUSA08519_00000003d5a17ab2'),
            pathlib.Path(r'C:\vcfg-work\owner-seq4-24d7b38d\bench_CUSA08519_00000003d5a17ab2'),
        ]
        seq4_dir = next((p for p in seq4_candidates if p.is_dir()), None)
        if seq4_dir is not None:
            res4 = msf.evaluate_sequence_models(seq4_dir)
            v4 = res4['variants']
            self.assertEqual(v4['baseline_warp8_coh0']['head_tight']['fgkill_flips'], 1556)
            self.assertEqual(v4['baseline_warp8_coh0']['head_tight']['fgkill_ws_flips'], 136)
            self.assertEqual(v4['V4b_ir_coh_vmn_v3']['head_tight']['fgkill_flips'], 16)
            self.assertEqual(v4['V4c_ir_coh_vmn_warp8_only']['head_tight']['fgkill_flips'], 24)
            self.assertEqual(v4['V5_ir_vmc_fgs8_slw_ramp']['head_tight']['fgkill_flips'], 10)
            self.assertEqual(v4['V5b_ir_vmc_gated_fgs8_slw_ramp']['head_tight']['fgkill_flips'], 10)
            self.assertEqual(v4['V5b_ir_vmc_gated_fgs8_slw_ramp']['head_tight']['fgkill_ws_flips'], 10)

    def test_pack16_coherence_and_hl_act_parity(self):
        import analyze_seq_fgkill as asf
        pack16_ll = ROOT / 'classic/bench/shaders/pack16.ll'

        # Case 1: Coherent 2x2 quad (all 4 corners have F=(10, 0) -> spr=0 <= 2.0, vmg=10 > 8.0, hasfg=0, mfit=1)
        f_coh = np.zeros((68, 120, 2), dtype=np.float32)
        f_coh[20:22, 50:52, 0] = 10.0
        r_coh = asf.eval_pack16_ir_cell(pack16_ll, 50, 20, f_coh)
        self.assertTrue(r_coh['vmg_hi'])
        self.assertTrue(r_coh['mov_obj'])
        self.assertTrue(r_coh['mfit'])
        self.assertTrue(r_coh['fgkill'])

        # Case 2: Incoherent 2x2 quad (F(50,20)=(10,0), F(51,20)=(-10,0) -> spr=20 > max(0.125*10, 2.0)=2.0)
        # In warp8.ll:945,1076, !%coh takes slow: where %vmg = 0.0 -> vmg_hi=False, mov_obj=False, fgkill=False
        f_inc = np.zeros((68, 120, 2), dtype=np.float32)
        f_inc[20, 50, 0] = 10.0
        f_inc[20, 51, 0] = -10.0
        r_inc = asf.eval_pack16_ir_cell(pack16_ll, 50, 20, f_inc)
        self.assertFalse(r_inc['vmg_hi'], msg='Incoherent 2x2 quad must set vmg=0.0 (vmg_hi=False) as in warp8.ll:1076')
        self.assertFalse(r_inc['mov_obj'], msg='Incoherent 2x2 quad with hasfg=0 must have mov_obj=False')
        self.assertFalse(r_inc['fgkill'], msg='Incoherent 2x2 quad with hasfg=0 and hl_act=0 must have fgkill=False')

        # Case 3: Coherent 2x2 quad with moderate velocity F=(4, 0) (2.0 < vmg=4.0 <= 8.0 -> vmg_hi=False, hasfg=0),
        # mfit=False (emov >= 0.25), but bright glint/highlight (%hl_act = True in warp8.ll:1115-1120, 1160):
        # At (up,vp)=(810,328) and (uc,vc)=(806,328), center tap is bright (0.95,0.95,0.95) and neighbor +1 is (0.2,0.2,0.2)
        # while static center (808,328) is dark (0.1,0.1,0.1) -> hl_mov > 0.40 and dl_s < -0.05 -> is_glint=True, hl_act=True
        f_hl = np.zeros((68, 120, 2), dtype=np.float32)
        f_hl[20:22, 50:52, 0] = 4.0

        def img_prev_hl(u, v):
            if abs(u - 810.0) < 0.2 and abs(v - 328.0) < 0.2:
                return np.array([0.95, 0.95, 0.95, 1.0], dtype=np.float32)
            return np.array([0.10, 0.10, 0.10, 1.0], dtype=np.float32)

        def img_cur_hl(u, v):
            if abs(u - 806.0) < 0.2 and abs(v - 328.0) < 0.2:
                return np.array([0.35, 0.35, 0.35, 1.0], dtype=np.float32)
            return np.array([0.10, 0.10, 0.10, 1.0], dtype=np.float32)

        r_hl = asf.eval_pack16_ir_cell(pack16_ll, 50, 20, f_hl, img_prev_fn=img_prev_hl, img_cur_fn=img_cur_hl)
        self.assertFalse(r_hl['vmg_hi'])
        self.assertFalse(r_hl['mov_obj'])
        self.assertTrue(r_hl['fgkill'], msg='Coherent quad with vmg>2.0 and hl_act=True must set fgkill=True as in warp8.ll:1160')

    def test_seq_nonuniform_timing_and_low_motion_warning(self):
        import analyze_seq_fgkill as asf

        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            raw = pathlib.Path(d) / 'seq_timing'
            raw.mkdir(parents=True, exist_ok=True)
            for s in range(8):
                buf = bytearray(asf.BENCH_SEQ_STEP_BYTES)
                v2 = np.zeros((34, 60, 2), dtype='<i2')
                # Low motion (< 2.0) on 6 of 8 steps -> majority low motion warning must trigger
                if s in (1, 2):
                    v2[:] = [4, 2]
                fgmask = np.zeros((68, 120), dtype=np.uint8)
                # Flip all 480 cells in head_tight ONLY on step s=3 (which has timing_ok=False, dt_us=39000)
                if s == 3:
                    fgmask[14:34, 48:72] = asf.FGMASK_FGKILL | asf.FGMASK_FGKILL_WS
                buf[asf.OFF_V2:asf.OFF_V2 + asf.LEN_V2] = v2.tobytes()
                buf[asf.OFF_FGMASK:asf.OFF_FGMASK + asf.LEN_FGMASK] = fgmask.tobytes()
                (raw / f'bench_seq_{s:04d}.bin').write_bytes(bytes(buf))
                dt_us = 39000 if s == 3 else 33366
                timing_ok = (s != 3)
                t0 = 100000 + s * 33366
                (raw / f'bench_seq_{s:04d}.json').write_text(json.dumps({
                    'schema': 2, 'mode': 'seq_small', 'title': 'CUSA08519', 'step': s,
                    'flip': 300 + s, 'submit_us': [t0, t0 + dt_us, t0 + 2 * dt_us],
                    'dt_us': dt_us, 'timing_ok': timing_ok, 'nonuniform_steps': 1,
                    'step_bytes': asf.BENCH_SEQ_STEP_BYTES, 'anchor_triplet': s in (0, 7),
                }))
            rep = asf.analyze_sequence(raw)
            self.assertEqual(rep['summary']['nonuniform_steps_count'], 1)
            self.assertEqual(rep['summary']['timing_ok_steps_count'], 7)
            self.assertEqual(rep['summary']['uniform_pairs_count'], 5)
            # Across all 7 pairs, step 2->3 and 3->4 flip all 480 cells (2/7), but across uniform pairs (where both steps have timing_ok=True), flip rate is 0.0
            self.assertAlmostEqual(rep['summary']['fgkill_flip_rate_cell_fixed'], 2.0 / 7.0, places=6)
            self.assertAlmostEqual(rep['summary']['fgkill_flip_rate_cell_fixed_uniform_only'], 0.0, places=6)
            self.assertTrue(rep['summary']['low_motion_warning'])
            self.assertEqual(rep['summary']['low_motion_steps_count'], 6)


if __name__ == '__main__':
    unittest.main()
