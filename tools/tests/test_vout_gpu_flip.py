"""CUSA06381 vout-path GPU-synchronized flips: run the real helpers from classic/source/main.c as host bitcode.

Locks the hardware fix for the present-copy deadlock at frame 122 (CPU flips of our mid/copy reached the screen before
the GPU wrote them; the copy's WaitFlipDone then waited forever): only CUSA06381 takes the SubmitAndFlip path, the
placeholder it builds is the prepareFlip tail the gnm path already recognizes, and every failure falls back to CPU flips.
Requires the configured LLVM tools; no console or plugin execution is involved.
"""
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
cfg = json.loads((ROOT / 'config.json').read_text())
text = (ROOT / 'classic/source/main.c').read_text()


def function(name):
    start = re.search(r'^static [^\n]+\b' + name + r'\([^\n]*\)\n\{', text, re.M).start()
    pos = text.index('{', start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def require(pattern, what):
    if not re.search(pattern, text, re.S):
        raise SystemExit('vout gpu flip: missing ' + what)


# The vout hook must gate the GPU path on the title + present copy, and keep the CPU flips as the fallback.
require(r'int gpu_flip = vout_gpu_flip_title\(\) && g_pcopy_on && second_flip && !third_flip && show_idx != u_idx',
        'gpu_flip gate (title + present copy + mid/real pair)')
require(r'if \(!gpu_flip\) s_rc = vout_submit_dcb\(mine, n\);', 'CPU submit when the GPU flip is off or failed')
require(r'if \(!gpu_flip\)\s+vr1 = HOOK_CONTINUE\(sceVideoOutSubmitFlip', 'CPU mid flip fallback')
require(r'show_idx = u_idx;\s+\}\s+\}\s+if \(vr2 != 0\)\s+vr2 = HOOK_CONTINUE\(sceVideoOutSubmitFlip',
        'CPU real flip fallback to the game buffer')
# The gnm path recognizes game flip tails with this predicate; our placeholder must satisfy it.
require(r"t64\[0\] == 0xC03E1000u && \(t64\[1\] >> 8\) == 0x687507u", 'gnm-path prepareFlip tail predicate')

# File-scope definition (a function-local all-zero k_nop64_pkt also exists; the vout helpers use this one).
k_nop = re.search(r'^static const uint32_t k_nop64_pkt\[64\] = \{[^\n]*\};', text, re.M)[0]
out = ROOT / 'build/tests'
out.mkdir(parents=True, exist_ok=True)
source = out / 'vout_gpu_flip.c'
source.write_text('''#include <stdint.h>
extern int strcmp(const char *, const char *);
extern char *strcpy(char *, const char *);
#define PKT3(op, nbody, st) (0xC0000000u | (((uint32_t)(nbody) - 1u) << 16) | ((uint32_t)(op) << 8) | ((uint32_t)(st) << 1))
static char g_title_id[16] = "CUSA00000";
''' + k_nop + '\n' + function('vout_gpu_flip_title') + '\n' + function('vout_prepare_flip_tail') + '''
int main(void)
{
    if (vout_gpu_flip_title()) return 1;
    strcpy(g_title_id, "CUSA06381");
    if (!vout_gpu_flip_title()) return 2;
    // Titles verified on the CPU-flip path must keep it.
    const char *keep[] = { "CUSA05571", "CUSA08809", "CUSA07820", "CUSA10249", "CUSA00552" };
    for (unsigned i = 0; i < sizeof(keep) / sizeof(keep[0]); i++) {
        strcpy(g_title_id, keep[i]);
        if (vout_gpu_flip_title()) return 3;
    }
    uint32_t t64[64];
    for (int i = 0; i < 64; i++) t64[i] = 0xDEADBEEFu;
    vout_prepare_flip_tail(t64);
    if (t64[0] != k_nop64_pkt[0] || t64[0] != 0xC03E1000u) return 4;          // NOP header covering all 64 dwords
    if (t64[1] != 0x68750777u || (t64[1] >> 8) != 0x687507u) return 5;         // prepareFlip marker
    for (int i = 2; i < 64; i++) if (t64[i] != 0u) return 6;                   // driver-owned space, zeroed
    return 0;
}
''', encoding='utf-8')
bitcode = out / 'vout_gpu_flip.bc'
llvm = pathlib.Path(cfg['llvm_bin'])
subprocess.run([str(llvm / 'clang.exe'), '--target=x86_64-pc-windows-msvc', '-O0', '-fno-builtin',
                '-emit-llvm', '-c', str(source), '-o', str(bitcode)], check=True)
subprocess.run([str(llvm / 'lli.exe'), '--dlopen=msvcrt.dll', str(bitcode)], check=True)
print('vout gpu flip: CUSA06381-only gate, prepareFlip placeholder and CPU fallbacks passed')
