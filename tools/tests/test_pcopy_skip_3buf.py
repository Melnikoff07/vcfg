#!/usr/bin/env python3
"""History copy gate of build_dcb_flow8 (classic/source/main.c), compiled from the source and executed.

The statement of build_dcb_flow8 that calls fg8_resolve_2buf_history() is extracted literally, wrapped in a tiny C
harness that includes the real classic/source/fg8_table.h and overlay/title_families.h, and run for each case:

1. Any title (CUSA18723, a regional id of its family, and an unrelated CUSA00001) with a 3-buffer ring and
   two_buffer=1 (g_pbuf reserved): copy_dst stays 0, so pcopy8 is not dispatched, and P is the game's previous buffer
   on every frame. The gate has no title list.
2. Any title when the ring turns 2-buffer at runtime: the copy into g_pbuf comes back, and P reads g_pbuf from
   the second frame on (the first one has no valid history yet).
3. Any title with a 2-buffer ring, or with a real present copy into an own display buffer (copy_dst != 0): exactly
   what fg8_resolve_2buf_history() does on its own, as before the gate.

Compiler: config.json llvm_bin (clang.exe + lli.exe, as the other harnesses) when present, otherwise the host clang/cc
building a native binary.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN_C = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8", errors="replace")
CALL = "fg8_resolve_2buf_history(&P, C, &copy_dst, &copy_idx, seq);"


def extract_function(src: str, name: str) -> str:
    start = src.index("static uint32_t " + name + "(")
    pos = src.index("{", src.index(")", start))
    end, depth = pos + 1, 1
    while depth:
        depth += (src[end] == "{") - (src[end] == "}")
        end += 1
    return src[start:end]


def extract_gate_statement(fn: str) -> str:
    """The whole `if (!g_bench_offscreen) ...` statement that contains the history call."""
    call = fn.index(CALL)
    start = fn.rindex("if (!g_bench_offscreen)", 0, call)
    pos = start + len("if (!g_bench_offscreen)")
    while fn[pos].isspace():
        pos += 1
    if fn[pos] != "{":
        return fn[start:fn.index(";", pos) + 1]
    end, depth = pos + 1, 1
    while depth:
        depth += (fn[end] == "{") - (fn[end] == "}")
        end += 1
    return fn[start:end]


GATE = extract_gate_statement(extract_function(MAIN_C, "build_dcb_flow8"))

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "title_families.h"
static uint32_t g_hud_packed;   /* read by fill_table8_wh in fg8_table.h, unused here */
#include "fg8_table.h"

static char g_title_id[16];
static int g_ngame = 3, g_runtime_2buf = 0, g_bench_offscreen = 0;
#define lg(...) ((void)0)

#define BUF(i) (0x20000000ull + (uint64_t)(i) * 0x01000000ull)
#define PBUF 0x70000000ull
#define OWN_COPY 0x50000000ull

static void step(uint32_t seq, uint32_t prev, uint32_t cur, uint64_t copy_in, uint64_t *P_out, uint64_t *copy_out)
{
    uint64_t P = BUF(prev), C = BUF(cur), copy_dst = copy_in;
    uint32_t copy_idx = copy_in ? 7u : 0u;
    __GATE__
    *P_out = P;
    *copy_out = copy_dst;
}

static void run(const char *name, const char *title, int ngame, int runtime_from, int nflips, int ring, uint64_t copy_in)
{
    snprintf(g_title_id, sizeof(g_title_id), "%s", title);
    g_ngame = ngame;
    g_runtime_2buf = 0;
    g_pbuf = (void *)(uintptr_t)PBUF;   /* two_buffer=1: reserved at RegisterBuffers whatever the ring size */
    g_pbuf_ycocg = 0;
    g_pbuf_valid = 0; g_pbuf_last_seq = 0; g_pbuf_prev_va = 0;
    for (int f = 1; f <= nflips; f++) {
        int r = (runtime_from && f >= runtime_from) ? 2 : ring;
        if (runtime_from && f == runtime_from) g_runtime_2buf = 1;
        uint64_t P, copy;
        step((uint32_t)f, (uint32_t)((f - 1) % r), (uint32_t)(f % r), copy_in, &P, &copy);
        printf("%s %d P=%s copy=%s\n", name, f,
               P == PBUF ? "pbuf" : (P == BUF((f - 1) % r) ? "game_prev" : "other"),
               copy == PBUF ? "pbuf" : (copy == OWN_COPY ? "own" : (copy == 0 ? "none" : "other")));
    }
}

int main(void)
{
    run("skip3", "CUSA18723", 3, 0, 9, 3, 0);
    run("skip3_region", "CUSA18742", 3, 0, 6, 3, 0);
    run("runtime2", "CUSA18723", 3, 4, 9, 3, 0);
    run("ring2", "CUSA18723", 2, 0, 6, 2, 0);
    run("other3", "CUSA00001", 3, 0, 6, 3, 0);
    run("owncopy3", "CUSA18723", 3, 0, 6, 3, OWN_COPY);
    run("other_runtime2", "CUSA00001", 3, 4, 9, 3, 0);
    run("other_ring2", "CUSA00001", 2, 0, 6, 2, 0);
    run("other_owncopy3", "CUSA00001", 3, 0, 6, 3, OWN_COPY);
    return 0;
}
"""


def compile_and_run(src: str) -> str:
    inc = ["-I" + str(ROOT / "classic" / "source"), "-I" + str(ROOT / "overlay")]
    flags = ["-O0", "-Wall", "-Wno-unused-variable", "-Wno-unused-function", "-Wno-unused-but-set-variable"]
    with tempfile.TemporaryDirectory() as td:
        c = pathlib.Path(td) / "pcopy_gate.c"
        c.write_text(src, encoding="utf-8")
        cfg_path = ROOT / "config.json"
        llvm = pathlib.Path(json.loads(cfg_path.read_text(encoding="utf-8"))["llvm_bin"]) if cfg_path.exists() else None
        if llvm is not None and (llvm / "clang.exe").exists() and (llvm / "lli.exe").exists():
            bc = pathlib.Path(td) / "pcopy_gate.bc"
            subprocess.run([str(llvm / "clang.exe"), "--target=x86_64-pc-windows-msvc", *flags, *inc,
                            "-emit-llvm", "-c", str(c), "-o", str(bc)], check=True)
            cmd = [str(llvm / "lli.exe"), "--dlopen=msvcrt.dll", str(bc)]
        else:
            cc = shutil.which("clang") or shutil.which("cc")
            if cc is None:
                raise unittest.SkipTest("no C compiler (config.json llvm_bin or host clang/cc)")
            exe = pathlib.Path(td) / "pcopy_gate"
            subprocess.run([cc, *flags, *inc, str(c), "-o", str(exe)], check=True)
            cmd = [str(exe)]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


class TestPcopySkip3Buf(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = {}
        for line in compile_and_run(HARNESS.replace("__GATE__", GATE)).splitlines():
            name, frame, p, copy = line.split()
            cls.rows.setdefault(name, []).append((int(frame), p[2:], copy[5:]))

    def test_3buf_any_title_no_copy_and_p_from_game(self) -> None:
        for name in ("skip3", "skip3_region", "other3"):
            for frame, p, copy in self.rows[name]:
                self.assertEqual(copy, "none", f"{name} frame {frame}: copy_dst must stay 0 (no pcopy8)")
                self.assertEqual(p, "game_prev", f"{name} frame {frame}: P must be the game's previous buffer")

    def test_runtime_2buf_brings_the_copy_back(self) -> None:
        for name in ("runtime2", "other_runtime2"):
            for frame, p, copy in self.rows[name]:
                if frame < 4:
                    self.assertEqual((p, copy), ("game_prev", "none"), f"{name} frame {frame} before the 2-buffer switch")
                else:
                    self.assertEqual(copy, "pbuf", f"{name} frame {frame}: history copy must be back after the switch")
                    self.assertEqual(p, "game_prev" if frame == 4 else "pbuf", f"{name} frame {frame}: P after the switch")

    def test_2buf_ring_unchanged(self) -> None:
        for name in ("ring2", "other_ring2"):
            for frame, p, copy in self.rows[name]:
                self.assertEqual(copy, "pbuf", f"{name} frame {frame}: history copy into g_pbuf as before")
                self.assertEqual(p, "game_prev" if frame == 1 else "pbuf", f"{name} frame {frame}: P as before")

    def test_real_present_copy_unchanged(self) -> None:
        for name in ("owncopy3", "other_owncopy3"):
            for frame, p, copy in self.rows[name]:
                self.assertEqual(copy, "own", f"{name} frame {frame}: a present copy into an own buffer keeps its target")
                self.assertEqual(p, "game_prev" if frame == 1 else "other",
                                 f"{name} frame {frame}: P follows the previous copy target, as fg8_resolve_2buf_history does")

    def test_gate_has_no_title_list(self) -> None:
        self.assertIn("int pbuf_skip = ", GATE)
        stmt = GATE[GATE.index("int pbuf_skip = "):]
        stmt = stmt[:stmt.index(";")]
        self.assertNotIn("vcfg_in_family", stmt, "the history-copy skip must not depend on the title")
        self.assertNotIn("g_title_id", stmt, "the history-copy skip must not depend on the title")


if __name__ == "__main__":
    unittest.main(verbosity=2)
