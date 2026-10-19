"""Run the actual classic INI parser/defaults as host bitcode, with in-memory file IO.

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


struct = re.search(r'typedef struct \{.*?\} fg_config_t;', text, re.S)[0]
out = ROOT / 'build/tests'
out.mkdir(parents=True, exist_ok=True)
source = out / 'classic_config.c'
source.write_text('''#include <stdint.h>
#include <stddef.h>
extern int sscanf(const char *, const char *, ...);
extern int strcmp(const char *, const char *);
extern size_t strlen(const char *);
extern void *memcpy(void *, const void *, size_t);
static int g_known_game, g_detected_60fps;
static const char *input;
static int sceKernelOpen(const char *p, int f, int m) { return input ? 1 : -1; }
static long sceKernelRead(int fd, void *p, size_t n) {
    size_t k = strlen(input); if(k>n)k=n; memcpy(p,input,k); return (long)k;
}
static int sceKernelClose(int fd) { return 0; }
''' + struct + '\n' + '\n'.join(function(n) for n in ['fnv1a_bytes', 'cfg_defaults', 'parse_ini_file']) + '''
int main(void) {
    fg_config_t c;
    cfg_defaults(&c);
    input="present_copy=2\\n";
    if (!parse_ini_file("profile", &c, 0) || c.present_copy != 2) return 1;
    // Removing the key must return to AUTO, not retain the previous OFF value.
    cfg_defaults(&c); input="framegen=1\\n";
    if (!parse_ini_file("profile", &c, 0) || c.present_copy != 0) return 2;
    // A missing per-title key still inherits the global value.
    input="present_copy=1\\n"; parse_ini_file("global", &c, 0);
    input="preset=0\\n"; parse_ini_file("title", &c, 0);
    if (c.present_copy != 1) return 3;
    input="present_copy=2\\n"; parse_ini_file("title", &c, 0);
    if (c.present_copy != 2) return 4;
    input="present_copy=-1\\n"; parse_ini_file("title", &c, 0);
    if (c.present_copy != 0) return 5;
    input="present_copy=99\\n"; parse_ini_file("title", &c, 0);
    if (c.present_copy != 2) return 6;
    return 0;
}
''', encoding='utf-8')
bitcode = out / 'classic_config.bc'
llvm = pathlib.Path(cfg['llvm_bin'])
subprocess.run([str(llvm / 'clang.exe'), '--target=x86_64-pc-windows-msvc', '-O0', '-fno-builtin',
                '-emit-llvm', '-c', str(source), '-o', str(bitcode)], check=True)
subprocess.run([str(llvm / 'lli.exe'), '--dlopen=msvcrt.dll', str(bitcode)], check=True)
print('classic INI parser: reset, inheritance, per-title override and clamps passed')
