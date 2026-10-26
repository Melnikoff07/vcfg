"""AA Light on the real frame too, for CUSA18723 (post_mid = 0, Enhance on, Sharpness 0), with the static handling.

The real frame is post-processed in place and is the next pair's P (3-buffer ring, no history copy). AA on its static
pixels made warp8 stop seeing them as static, so rcas8 flag bit 5 makes the cur pass:
  a) skip the AA where warp8's static flag (P == C, read by the mid pass of the same lane) is set, and
  b) write the pixel back raw where its AA result equals P's stored code (static, but P holds last frame's AA of it).
Without b), every edge that once moved stays AA'd in P and never compares static again.

1. Reference (tools/bench/vcfg_warp.rcas8_real): static pixels untouched; write-back pixels raw; the rest equals the
   plain rcas8; a static scene that starts AA'd in P (after a camera pan) is raw again after one pair with b) and stays
   AA'd forever with a) alone; moving edges still get the AA.
2. IR (classic/shaders/rcas8.ll): bit 5 parse, the mid's static flag carried to the cur pass, the write-back test
   against T#prev (+0x00) on the packed RGB codes, the store skipped when it matches.
3. main.c, compiled and executed: real_aa_default(), post_mid_only() and rcas_static_flag() extracted literally; only
   the CUSA18723 family with post_mid 0, Enhance > 0, Sharpness 0, warp8 and P in the game's buffer gets flags 3 + 48;
   every other case keeps today's flags.
"""
import json
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

import vcfg_warp as vw  # noqa: E402

RCAS8 = (ROOT / "classic" / "shaders" / "rcas8.ll").read_text(encoding="utf-8")
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8", errors="replace")
F32 = np.float32


def scene(seed, h=48, w=64, shift=0):
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 40.0, F32)
    img[:, :] += rng.integers(0, 3, (h, w, 1)).astype(F32)
    yy, xx = np.mgrid[0:h, 0:w]
    img[(xx + shift) > 20 + yy // 3] = 220.0          # aliased staircase edge (moves with shift)
    img[30:32, 8:56] = 250.0                          # static 2-px line (HUD-like)
    return img / F32(255.0)


def q(x):
    return np.rint(np.clip(x, 0, 1) * F32(255.0))


class ReferenceRealS1(unittest.TestCase):
    def test_cases(self):
        a, b = scene(1, shift=0), scene(1, shift=3)                  # the staircase moves, the line does not
        p = q(vw.rcas8(a, 0.0, 1)) / F32(255.0)                      # P after a pan: everything AA'd
        out = vw.rcas8_real(b, p, 0.0, 1)
        st = vw.static_px(p, b)
        full = q(vw.rcas8(b, 0.0, 1)) / F32(255.0)
        wb = (q(full) == q(p)).all(-1)
        self.assertTrue(np.array_equal(out[st], b[st]))
        self.assertTrue(wb.any() and np.array_equal(out[wb], b[wb]))
        rest = ~st & ~wb
        self.assertTrue(np.array_equal(q(out[rest]), q(full[rest])))
        moved = rest & (q(full) != q(b)).any(-1)
        self.assertTrue(moved.any(), "no moving edge got the AA: the scene does not discriminate")

    def test_static_scene_recovers_after_one_pair(self):
        a = scene(2)
        p = q(vw.rcas8(a, 0.0, 1)) / F32(255.0)
        self.assertTrue((q(p) != q(a)).any())
        naive = p
        for _ in range(3):                                           # rule a) alone: P stays AA'd
            naive = q(vw.rcas8(a, 0.0, 1, skip=vw.static_px(naive, a))) / F32(255.0)
        self.assertTrue((q(naive) != q(a)).any())
        p1 = vw.rcas8_real(a, p, 0.0, 1)                             # with b): raw after one pair, then stable
        self.assertTrue(np.array_equal(p1, a))
        self.assertTrue(np.array_equal(vw.rcas8_real(a, p1, 0.0, 1), a))


class IrRealS1(unittest.TestCase):
    def test_bit5_and_carried_flag(self):
        self.assertRegex(RCAS8, r"%rsb = and i32 %rfl, 32\n\s*%real_s1 = icmp ne i32 %rsb, 0")
        self.assertIn("%st_skip = or i1 %st_skip0, %st_ext", RCAS8)
        self.assertIn("%oms = extractvalue { <4 x float>, i1, i1 } %omr, 2", RCAS8)
        self.assertRegex(RCAS8, r"%mid_st = phi i1 \[ %oms, %mid_join \], \[ false, %entry \]\n\s*%cur_st = and i1 %mid_st, %real_s1")
        self.assertRegex(RCAS8, r"%omr = call \{ <4 x float>, i1, i1 \} @eval_tile\([^)]*i1 %use_st, i1 false\)")
        self.assertRegex(RCAS8, r"%ocr = call \{ <4 x float>, i1, i1 \} @eval_tile\([^)]*i1 false, i1 %cur_st\)")

    def test_write_back_against_prev(self):
        wb = RCAS8.split("wb_chk:", 1)[1].split("st_cur:", 1)[0]
        self.assertIn("%tp = load <8 x i32>, ptr addrspace(4) %tbl, align 32", wb)   # T#prev at +0x00
        self.assertIn("image.load.2d.v4f32.i32(i32 15, i32 %x, i32 %y, <8 x i32> %tp", wb)
        self.assertRegex(wb, r"%pk_rgb = and i32 %pk_x, 16777215\n\s*%wb_same = icmp eq i32 %pk_rgb, 0\n"
                             r"\s*br i1 %wb_same, label %done, label %st_cur")
        self.assertRegex(RCAS8, r"%wb_try = and i1 %real_s1, %occ\n\s*br i1 %wb_try, label %wb_chk, label %st_cur")


def extract_function(src, name):
    m = re.search(r"^static [^\n]+\b" + name + r"\([^;]*\)\s*\{", src, re.M)
    assert m is not None, name
    pos = src.index("{", m.start())
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[m.start():end]


HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "title_families.h"
typedef struct { int post_mid, upscale, sharpness; } fg_config_t;
static fg_config_t g_cfg;
static char g_title_id[16];
static int g_t_prev_half16, g_gv;
static int g_mid_only = -1;
static int gv_warp_active(void) { return g_gv; }
#define lg(...) ((void)0)
__FNS__
static void row(const char *name, const char *title, int post, int up, int sharp, int gv, int half)
{
    snprintf(g_title_id, sizeof(g_title_id), "%s", title);
    g_cfg.post_mid = post; g_cfg.upscale = up; g_cfg.sharpness = sharp; g_gv = gv; g_t_prev_half16 = half;
    g_mid_only = -1;
    uint32_t enh = (uint32_t)up << 2;
    printf("%s mid_only=%d f1=%u f3=%u\n", name, post_mid_only(), rcas_static_flag(1u | enh), rcas_static_flag(3u | enh));
}
int main(void)
{
    row("on", "CUSA18723", 0, 1, 0, 0, 0);
    row("on_region", "CUSA18742", 0, 1, 0, 0, 0);
    row("strong", "CUSA18723", 0, 2, 0, 0, 0);
    row("other", "CUSA00001", 0, 1, 0, 0, 0);
    row("sharp", "CUSA18723", 0, 1, 35, 0, 0);
    row("enh_off", "CUSA18723", 0, 0, 0, 0, 0);
    row("pm1", "CUSA18723", 1, 1, 0, 0, 0);
    row("pm2", "CUSA18723", 2, 1, 0, 0, 0);
    row("gv", "CUSA18723", 0, 1, 0, 1, 0);
    row("half16", "CUSA18723", 0, 1, 0, 0, 1);
    return 0;
}
"""


def run_harness():
    fns = "\n".join(extract_function(MAIN_C, n) for n in ("real_aa_default", "rcas_static_flag", "post_mid_only"))
    src = HARNESS.replace("__FNS__", fns)
    inc = ["-I" + str(ROOT / "classic" / "source"), "-I" + str(ROOT / "overlay")]
    flags = ["-O0", "-Wall", "-Wno-unused-variable", "-Wno-unused-function"]
    with tempfile.TemporaryDirectory() as td:
        c = pathlib.Path(td) / "real_s1.c"
        c.write_text(src, encoding="utf-8")
        cfg = ROOT / "config.json"
        llvm = pathlib.Path(json.loads(cfg.read_text(encoding="utf-8"))["llvm_bin"]) if cfg.exists() else None
        if llvm is not None and (llvm / "clang.exe").exists() and (llvm / "lli.exe").exists():
            bc = pathlib.Path(td) / "real_s1.bc"
            subprocess.run([str(llvm / "clang.exe"), "--target=x86_64-pc-windows-msvc", *flags, *inc, "-emit-llvm", "-c",
                            str(c), "-o", str(bc)], check=True)
            cmd = [str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bc)]
        else:
            cc = shutil.which("clang") or shutil.which("cc")
            if cc is None:
                raise unittest.SkipTest("no C compiler (config.json llvm_bin or host clang/cc)")
            exe = pathlib.Path(td) / "real_s1"
            subprocess.run([cc, *flags, *inc, str(c), "-o", str(exe)], check=True)
            cmd = [str(exe)]
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return {ln.split()[0]: dict(kv.split("=") for kv in ln.split()[1:]) for ln in out.splitlines()}


class MainRealS1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_harness()

    def test_on_for_the_family_only(self):
        for k, enh in (("on", 4), ("on_region", 4), ("strong", 8)):
            self.assertEqual(self.r[k], {"mid_only": "0", "f1": str(1 | enh | 16), "f3": str(3 | enh | 48)}, k)

    def test_everything_else_unchanged(self):
        self.assertEqual(self.r["other"], {"mid_only": "1", "f1": "21", "f3": "7"})
        self.assertEqual(self.r["sharp"], {"mid_only": "1", "f1": "21", "f3": "7"})
        self.assertEqual(self.r["enh_off"], {"mid_only": "1", "f1": "17", "f3": "3"})
        self.assertEqual(self.r["pm1"], {"mid_only": "1", "f1": "21", "f3": "7"})
        self.assertEqual(self.r["pm2"], {"mid_only": "0", "f1": "21", "f3": "7"})   # explicit post_mid 2: no bit 5
        self.assertEqual(self.r["gv"], {"mid_only": "0", "f1": "5", "f3": "7"})     # warp8g keeps the game's alpha
        self.assertEqual(self.r["half16"], {"mid_only": "1", "f1": "21", "f3": "7"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
