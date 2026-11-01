#!/usr/bin/env python3
"""Motion history after a scene change (mode 8).

warp8 reads the previous pair's field (F prev, +0x110) for its quadratic trajectory offset. After a scene change that
field is the motion between two unrelated images, and the first generated frame after the cut lost 4 to 15 dB against
the same frame without history in the reference. The fix: the pair that writes F records its unmatched-block count
(misc dword 8 + parity), and phase8 of the next pair clears F prev when that count is above the scene-change threshold,
after the motion stages used it as a candidate source and before warp8 reads it.

1. Reference (tools/bench): vcfg_ref.warp_prevF / motion(prev_cutn=...) clear the field warp8 sees after a cut, keep
   it otherwise, and the frame then equals the frame rendered without history.
2. IR conformance: phase8.ll reads the count of the F prev writer, decodes the same threshold as warp8.ll, clears the
   whole 120 x 68 float2 buffer, records its own count only for the first mid frame; fg8_table.h sizes V#misc for it.
3. Execution (host clang, when available): phase8.ll compiled for the host and run over its 64 lanes clears F prev
   exactly in the reset cases and records the count in the right slot.
"""
import ctypes
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "bench"))
import vcfg_ref as vref  # noqa: E402
import vcfg_warp as vw  # noqa: E402

F32 = np.float32
SH = ROOT / "classic" / "shaders"
SRC = ROOT / "classic" / "source"
NBLK = 120 * 68


def _tex(h, w, seed):
    rng = np.random.default_rng(seed)
    yy, xx = np.meshgrid(np.arange(h, dtype=F32), np.arange(w, dtype=F32), indexing="ij")
    out = []
    for c in range(3):
        acc = np.full((h, w), 0.5, F32)
        for _ in range(5):
            fx, fy, ph = rng.uniform(0.08, 0.5), rng.uniform(0.08, 0.5), rng.uniform(0, 6.28)
            acc += F32(0.09) * np.sin(fx * xx + fy * yy + ph).astype(F32)
        out.append(acc)
    return np.clip(np.stack(out, -1), 0, 1).astype(F32)


class TestReference(unittest.TestCase):
    def test_warp_prevF_cases(self) -> None:
        pf = np.full((68, 120, 2), 9.0, F32)
        self.assertIs(vref.warp_prevF(pf, None), pf)                      # no chain information
        self.assertIs(vref.warp_prevF(pf, 5600), pf)                      # at the threshold: not a cut
        self.assertIsNone(vref.warp_prevF(None, 8000))
        self.assertFalse(vref.warp_prevF(pf, 5601).any())                 # above: cleared
        self.assertEqual(vref.warp_prevF(pf, 5601).shape, pf.shape)
        self.assertIs(vref.warp_prevF(pf, 0xDEADBEEF), pf)                # not a count: uninitialised memory
        self.assertIs(vref.warp_prevF(pf, 4500), pf)
        self.assertFalse(vref.warp_prevF(pf, 4500, 4000).any())           # per-title threshold
        self.assertEqual(vref.cut_threshold(0), 5600)
        self.assertEqual(vref.cut_threshold(4000), 4000)

    def _scene(self):
        h, w = 96, 160
        tp = _tex(h, w, 11)
        tc = np.roll(tp, 6, axis=1)
        F = np.zeros((h // 16, w // 16, 2), F32)
        F[..., 0] = 6.0
        GK = np.zeros((h // 16, w // 16), np.int64)
        stale = np.zeros_like(F)
        stale[..., 0] = 3.0                      # same direction, |a| <= 1.25 vmin: the quadratic offset is active
        return tp, tc, F, GK, stale

    def test_stale_history_moves_the_frame_and_the_reset_removes_it(self) -> None:
        tp, tc, F, GK, stale = self._scene()
        cold = vw.warp8(tp, tc, F, GK, 0, t=1.0 / 3.0, prevF=None)
        warm = vw.warp8(tp, tc, F, GK, 0, t=1.0 / 3.0, prevF=stale)
        self.assertGreater(float(np.abs(warm - cold).max()), 0.01, "precondition: the old field changes the frame")
        reset = vw.warp8(tp, tc, F, GK, 0, t=1.0 / 3.0, prevF=vref.warp_prevF(stale, 7000))
        np.testing.assert_array_equal(reset, cold)

    def test_motion_keeps_the_candidates_and_hands_warp8_the_cleared_field(self) -> None:
        h, w = 1080, 1920                        # the reference's temporal candidates assume the 120 x 68 grid
        a = _tex(h, w, 3)
        b = np.roll(a, 8, axis=1)
        prm = dict(qmax=255.0, sharpness=0, enhance=0, prev_luma_sum=None, t=0.5)
        pf = np.zeros((68, 120, 2), F32)
        pf[..., 0] = 7.0
        st_keep = vref.motion(a, b, dict(prm, prevF=pf))
        st_cut = vref.motion(a, b, dict(prm, prevF=pf, prev_cutn=6000))
        st_nocut = vref.motion(a, b, dict(prm, prevF=pf, prev_cutn=1200))
        np.testing.assert_array_equal(st_cut["F"], st_keep["F"])      # the motion stages still saw the old field
        self.assertIs(st_keep["prevF"], pf)
        self.assertIs(st_nocut["prevF"], pf)
        self.assertFalse(st_cut["prevF"].any())
        r_cut = vref.render(a, b, st_cut, prm)
        r_cold = vref.render(a, b, dict(st_keep, prevF=None), prm)
        np.testing.assert_array_equal(r_cut, r_cold)


def _fn(src, name):
    i = src.index(name)
    return src[i:src.index("\n}\n", i)]


class TestIRConformance(unittest.TestCase):
    def setUp(self) -> None:
        self.ph = (SH / "phase8.ll").read_text()
        self.wp = (SH / "warp8.ll").read_text()
        self.tab = (SRC / "fg8_table.h").read_text()

    def test_reads_the_writer_count_and_uses_warp8_threshold(self) -> None:
        ph = self.ph
        self.assertIn("%h_skip0 = and i32 %h_mid, 32768", ph)
        self.assertIn("br i1 %h_skip, label %hist_done, label %hist", ph)
        self.assertIn("%h_pq = xor i32 %h_par, 1", ph)
        self.assertIn("%h_off = add i32 %h_o0, 32", ph)
        self.assertIn("call i32 @llvm.amdgcn.raw.buffer.load.i32(<4 x i32> %vm, i32 %h_off, i32 0, i32 0)", ph)
        # the same decode as warp8.ll's scene-change rule
        for a, b in (("%cbk = lshr i32 %flc, 16", "%h_cbk = lshr i32 %h_fl, 16"),
                     ("%cbk8 = shl i32 %cbk, 3", "%h_cbk8 = shl i32 %h_cbk, 3"),
                     ("%cthr = select i1 %cbz, i32 5600, i32 %cbk8", "%h_thr = select i1 %h_cbz, i32 5600, i32 %h_cbk8"),
                     ("%cutc = icmp ugt i32 %cutn, %cthr", "%h_cut0 = icmp ugt i32 %h_pc, %h_thr")):
            self.assertIn(a, self.wp)
            self.assertIn(b, ph)
        self.assertIn("%h_vld = icmp ule i32 %h_pc, 8160", ph)
        self.assertEqual(vref.cut_threshold(0), 5600)

    def test_clears_all_of_F_prev(self) -> None:
        ph = self.ph
        self.assertIn("%pfp = getelementptr i8, ptr addrspace(4) %tbl, i64 272", ph)    # +0x110 F prev
        self.assertIn("%hz_i = phi i32 [ %tid, %hz_pre ], [ %hz_n, %hz ]", ph)
        self.assertIn("call void @llvm.amdgcn.raw.buffer.store.v4i32(<4 x i32> zeroinitializer, <4 x i32> %vfp, i32 %hz_o", ph)
        self.assertIn("%hz_n = add i32 %hz_i, 64", ph)
        self.assertIn(f"%hz_c = icmp ult i32 %hz_n, {NBLK * 8 // 16}", ph)
        self.assertIn("make_v(tb + 68, WA(r ? W8_FA : W8_FB), 120 * 68 * 8);", self.tab)

    def test_records_its_count_for_the_next_pair(self) -> None:
        ph = self.ph
        wr = ph[ph.index("wr_stats:"):]
        self.assertIn("%c_off = add i32 %c_o0, 32", wr)
        self.assertIn("call void @llvm.amdgcn.raw.buffer.store.i32(i32 %cutn, <4 x i32> %vm, i32 %c_off, i32 0, i32 0)", wr)
        self.assertNotIn("%c_off", ph[:ph.index("wr_stats:")])
        m = re.search(r"make_v\(tb \+ 72, WA\(W8_MISC\), (\d+)\);", self.tab)
        self.assertGreaterEqual(int(m.group(1)), 40, "V#misc must cover dwords 8 and 9")


WRAPPER = r"""
#include <stdint.h>
#include <string.h>
typedef int32_t v4i32 __attribute__((vector_size(16)));
static uint8_t *vbase(v4i32 v, uint32_t *n) {
    uint64_t a = (uint32_t)v[0] | ((uint64_t)((uint32_t)v[1] & 0xFFFFu) << 32);
    *n = (uint32_t)v[2];
    return (uint8_t *)(uintptr_t)a;
}
int32_t sim_load_i32(v4i32 v, int32_t off, int32_t so, int32_t aux) {
    uint32_t n; uint8_t *b = vbase(v, &n); int32_t r = 0;
    if (b && (uint32_t)off + 4u <= n) memcpy(&r, b + off, 4);
    return r;
}
v4i32 sim_load_v4i32(v4i32 v, int32_t off, int32_t so, int32_t aux) {
    uint32_t n; uint8_t *b = vbase(v, &n); v4i32 r = {0, 0, 0, 0};
    if (b && (uint32_t)off + 16u <= n) memcpy(&r, b + off, 16);
    return r;
}
void sim_store_i32(int32_t x, v4i32 v, int32_t off, int32_t so, int32_t aux) {
    uint32_t n; uint8_t *b = vbase(v, &n);
    if (b && (uint32_t)off + 4u <= n) memcpy(b + off, &x, 4);
}
void sim_store_f32(float x, v4i32 v, int32_t off, int32_t so, int32_t aux) {
    uint32_t n; uint8_t *b = vbase(v, &n);
    if (b && (uint32_t)off + 4u <= n) memcpy(b + off, &x, 4);
}
void sim_store_v4i32(v4i32 x, v4i32 v, int32_t off, int32_t so, int32_t aux) {
    uint32_t n; uint8_t *b = vbase(v, &n);
    if (b && (uint32_t)off + 16u <= n) memcpy(b + off, &x, 16);
}
void shader_main(const uint8_t *tbl, int32_t gx, int32_t tid);
void run_wave(const uint8_t *tbl) { for (int t = 0; t < 64; t++) shader_main(tbl, 0, t); }
"""


def _host_ll(src):
    s = src.replace('target triple = "amdgcn--"', "")
    s = s.replace("addrspace(4)", "")
    s = s.replace("define amdgpu_cs void @main(", "define void @shader_main(")
    s = s.replace(" inreg ", " ")
    for k, v in (("raw.buffer.load.v4i32", "sim_load_v4i32"), ("raw.buffer.load.i32", "sim_load_i32"),
                 ("raw.buffer.store.v4i32", "sim_store_v4i32"), ("raw.buffer.store.i32", "sim_store_i32"),
                 ("raw.buffer.store.f32", "sim_store_f32")):
        s = s.replace("@llvm.amdgcn." + k, "@" + v)
    s = s.replace(" #0 {", " {")
    s = re.sub(r"attributes #0 = \{[^}]*\}", "", s)
    return s.replace("i32 immarg", "i32")


class TestPhase8Execution(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        clang = shutil.which("clang-22") or shutil.which("clang")
        if not clang:
            raise unittest.SkipTest("no host clang")
        cls.tmp = tempfile.TemporaryDirectory()
        t = pathlib.Path(cls.tmp.name)
        (t / "w.c").write_text(WRAPPER)
        (t / "p.ll").write_text(_host_ll((SH / "phase8.ll").read_text()))
        so = t / "libphase8.so"
        r = subprocess.run([clang, "-O1", "-fPIC", "-shared", "-Wno-override-module", str(t / "w.c"), str(t / "p.ll"),
                            "-o", str(so)], capture_output=True, text=True)
        if r.returncode != 0:
            raise unittest.SkipTest("host clang cannot build phase8.ll: " + r.stderr[-400:])
        cls.lib = ctypes.CDLL(str(so))
        cls.lib.run_wave.argtypes = [ctypes.c_void_p]
        cls.tab = (SRC / "fg8_table.h").read_text()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _run(self, parity, prev_count, cutn=4321, skip_st=False, cbk=0):
        misc_n = int(re.search(r"make_v\(tb \+ 72, WA\(W8_MISC\), (\d+)\);", self.tab).group(1))
        tbl = np.zeros(144, np.uint32)
        misc = np.zeros(misc_n // 4, np.uint32)
        misc[0] = cutn
        misc[8 + (parity ^ 1)] = prev_count & 0xFFFFFFFF
        misc[8 + parity] = 77
        fprev = np.full(NBLK * 2 + 8, 1.5, F32)            # + one guard row of 8 floats past the V# range
        mon = np.zeros(4, np.uint32)
        stats = np.zeros(8, np.uint32)

        def setv(off, buf, n):
            a = buf.ctypes.data
            tbl[off:off + 4] = [a & 0xFFFFFFFF, (a >> 32) & 0xFFFF, n, 0]
        setv(68, fprev, NBLK * 8)
        setv(72, misc, misc_n)
        setv(76, mon, 16)
        setv(92, stats, 32)
        tbl[28] = parity
        tbl[29] = 3 | (0x8000 if skip_st else 0)
        tbl[30] = (cbk & 0xFFFF) << 16
        self.lib.run_wave(tbl.ctypes.data)
        return fprev, misc, stats

    def test_reset_after_a_cut(self) -> None:
        for parity in (0, 1):
            fprev, misc, stats = self._run(parity, 6000)
            self.assertFalse(fprev[:NBLK * 2].any(), parity)
            self.assertTrue((fprev[NBLK * 2:] == 1.5).all(), "nothing written past F prev")
            self.assertEqual(int(misc[8 + parity]), 4321)
            self.assertEqual(int(misc[8 + (parity ^ 1)]), 6000)
            self.assertEqual(int(stats[0]), 4321)

    def test_no_reset_otherwise(self) -> None:
        for prev in (0, 5600, 0xDEADBEEF, 8161):
            fprev, misc, _ = self._run(1, prev)
            self.assertTrue((fprev == 1.5).all(), prev)
            self.assertEqual(int(misc[9]), 4321)

    def test_per_title_threshold(self) -> None:
        self.assertFalse(self._run(0, 4500, cbk=500)[0][:NBLK * 2].any())     # 500 * 8 = 4000 blocks
        self.assertTrue((self._run(0, 3900, cbk=500)[0] == 1.5).all())

    def test_second_mid_frame_neither_clears_nor_records(self) -> None:
        fprev, misc, stats = self._run(0, 7000, skip_st=True)
        self.assertTrue((fprev == 1.5).all())
        self.assertEqual(int(misc[8]), 77)
        self.assertFalse(stats.any())


if __name__ == "__main__":
    unittest.main()
