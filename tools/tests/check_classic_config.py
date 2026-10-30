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
families_h = (ROOT / 'overlay/title_families.h').read_text().replace('#include <string.h>', '').replace('#pragma once', '')
source.write_text('''#include <stdint.h>
#include "menu_combo.h"
#include <stddef.h>
extern int sscanf(const char *, const char *, ...);
extern int strcmp(const char *, const char *);
extern char *strstr(const char *, const char *);
extern size_t strlen(const char *);
extern void *memcpy(void *, const void *, size_t);
''' + families_h + '''
static int g_known_game, g_detected_60fps, g_tile_override = -1, g_tile_sweep, g_t6_bit_override = -1, g_adapt_early_crashes = 0, g_post_budget = 1;
static char g_title_id[16] = "CUSA00000";
static char g_family_id[16] = "CUSA00000";
static void tile_load(int idx) { (void)idx; }
static const char *input;
static int sceKernelOpen(const char *p, int f, int m) { return input ? 1 : -1; }
static long sceKernelRead(int fd, void *p, size_t n) {
    size_t k = strlen(input); if(k>n)k=n; memcpy(p,input,k); return (long)k;
}
static int sceKernelClose(int fd) { return 0; }
''' + struct + '\n' + '\n'.join(function(n) for n in ['fnv1a_bytes', 'cfg_apply_title_defaults', 'cfg_defaults', 'parse_ini_file_ex', 'parse_ini_file']) + '''
static void set_title(const char *id) {
    size_t n = strlen(id);
    memcpy(g_title_id, id, n + 1);
    const char *fam = vcfg_title_family(id);
    size_t fn = strlen(fam);
    memcpy(g_family_id, fam, fn + 1);
}
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
    input="two_buffer=1\\n"; parse_ini_file("title", &c, 0);
    if (c.two_buffer != 1) return 7;
    // Verify per-family defaults from telemetry logs:
    set_title("CUSA11456"); cfg_defaults(&c);
    if (c.present_copy != 2 || c.two_buffer != 0 || c.shave_mode != 2 || c.reg_mode != 2 || c.pace_order != 3 || c.fps_lock != 30) return 8;
    set_title("CUSA13323"); cfg_defaults(&c);
    if (c.present_copy != 2 || c.two_buffer != 0 || c.shave_mode != 2 || c.reg_mode != 2 || c.pace_order != 3 || c.fps_lock != 30) return 8;
    set_title("CUSA16596"); cfg_defaults(&c);
    if (c.reg_mode != 1 || c.pace_order != 2) return 9;
    set_title("CUSA09311"); cfg_defaults(&c);
    if (c.reg_mode != 1 || c.game_vectors != 0) return 10;
    set_title("CUSA18534"); cfg_defaults(&c);
    if (c.reg_mode != 1 || c.game_vectors != 0) return 11;
    set_title("CUSA28561"); cfg_defaults(&c);
    if (c.shave_mode != 2) return 12;
    set_title("CUSA11260"); cfg_defaults(&c);
    if (c.pace_order != 2) return 13;
    // Verify INI override still takes precedence over per-family default:
    input="game_vectors=1\\n"; parse_ini_file("title", &c, 0);
    if (c.game_vectors != 1) return 14;
    // Verify audio_fix defaults to 1 on CUSA10249 and 0 on all other titles, and parses 0/1/2:
    set_title("CUSA07408"); cfg_defaults(&c);
    if (c.audio_fix != 0) return 15;
    set_title("CUSA10249"); cfg_defaults(&c);
    if (c.audio_fix != 1) return 16;
    input="audio_fix = 2\\n"; parse_ini_file("title", &c, 0);
    if (c.audio_fix != 2) return 17;
    input="audio_fix=0\\n"; parse_ini_file("title", &c, 0);
    if (c.audio_fix != 0) return 18;
    return 0;
}
''', encoding='utf-8')
bitcode = out / 'classic_config.bc'
llvm = pathlib.Path(cfg['llvm_bin'])
subprocess.run([str(llvm / 'clang.exe'), '--target=x86_64-pc-windows-msvc', '-O0', '-fno-builtin',
                '-emit-llvm', '-I', str(ROOT / 'overlay'), '-c', str(source), '-o', str(bitcode)], check=True)
subprocess.run([str(llvm / 'lli.exe'), '--dlopen=msvcrt.dll', str(bitcode)], check=True)
print('classic INI parser: reset, inheritance, per-title override and clamps passed')
