#!/usr/bin/env python3
"""below30 / shallow_queue in the in-game menu and in the app.

1. In-game menu (classic/source/main.c, overlay/vov.c): one row per setting before Opacity, toggled by Left/Right,
   MENU_NROWS equal to the rows drawn, and the overlay draws that many rows.
2. Plugin INI (compiled from main.c with host clang): an old config-<TITLE>.ini without the two keys loads the
   defaults 0 / 1; the menu toggles them; the menu save (cfg_save_game_ini) writes them and they read back.
3. App (launcher/src, compiled with host clang): the game settings panel toggles the two fields with Left/Right
   (Pipeline control moved to the last row), an old ini without the keys loads the defaults, and both files the app
   writes carry them.
"""
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8", errors="replace")
APP_C = (ROOT / "launcher" / "src" / "app.c").read_text(encoding="utf-8", errors="replace")
APP_MAIN = (ROOT / "launcher" / "src" / "main.c").read_text(encoding="utf-8", errors="replace")
CLANG = shutil.which("clang-22") or shutil.which("clang")


def function(text, name):
    m = re.search(r"^static [^\n]*\b" + re.escape(name) + r"\([^\n]*\)\n\{", text, re.M)
    pos = text.index("{", m.start())
    depth, end = 1, pos + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[m.start():end]


def build_run(src, extra_args=()):
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        (t / "t.c").write_text(src)
        exe = t / "t"
        r = subprocess.run([CLANG, "-O0", "-w", *extra_args, str(t / "t.c"), "-o", str(exe)], capture_output=True, text=True)
        if r.returncode:
            raise AssertionError("build failed:\n" + r.stderr[-3000:])
        return subprocess.run([str(exe), str(t)], capture_output=True, text=True)


class TestMenuRows(unittest.TestCase):
    def test_rows_and_adjust(self) -> None:
        osd = function(MAIN, "osd_build_slot")
        menu = osd[osd.index("m.sel = g_menu_sel;"):osd.index("vov_cpy(m.hints")]
        labels = re.findall(r'vov_row\(&m, "([^"]+)"', menu)
        self.assertEqual(labels[-3:], ["Below 30 FPS", "Low latency", "Opacity"])
        self.assertIn('vov_row(&m, "Below 30 FPS", VOV_TOGGLE, c.below30, 0, "");', menu)
        self.assertIn('vov_row(&m, "Low latency", VOV_TOGGLE, c.shallow_queue, 0, "");', menu)
        n = int(re.search(r"#define MENU_NROWS (\d+)", MAIN).group(1))
        self.assertEqual(n, len(labels))
        adj = function(MAIN, "menu_adjust_row")
        for row, field in ((8, "below30"), (9, "shallow_queue")):
            self.assertIn(f"case {row}: c->{field} = !c->{field}; break;", adj)
        self.assertIn("case 10: c->menu_opacity", adj)
        vh = (ROOT / "overlay" / "vov.h").read_text()
        vc = (ROOT / "overlay" / "vov.c").read_text()
        cap = int(re.search(r"VovRow rows\[(\d+)\];", vh).group(1))
        self.assertGreaterEqual(cap, n)
        self.assertIn(f"int nrows = m->nrows > {cap} ? {cap} : m->nrows", vc)
        # the 11 rows fit the full 720 px panel without dropping one (status line on: HEAD 168, FOOT 56, ROW >= 40)
        self.assertLessEqual(168 + n * 40 + 56, int(re.search(r"#define VOV_MAX_H\s+(\d+)u", vh).group(1)))
        self.assertIn("if (ROW < 40)", vc)


@unittest.skipUnless(CLANG, "no host clang")
class TestPluginIni(unittest.TestCase):
    def test_old_ini_defaults_menu_toggle_save_and_reload(self) -> None:
        struct = re.search(r"typedef struct \{.*?\} fg_config_t;", MAIN, re.S)[0]
        fam = (ROOT / "overlay" / "title_families.h").read_text().replace("#pragma once", "")
        combo = (ROOT / "overlay" / "menu_combo.h").read_text().replace("#pragma once", "")
        funcs = "\n".join(function(MAIN, n) for n in ("fnv1a_bytes", "cfg_apply_title_defaults", "cfg_defaults",
                                                     "parse_ini_file_ex", "parse_ini_file", "menu_adjust_row",
                                                     "cfg_save_game_ini"))
        src = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
""" + combo + fam + r"""
static char g_dir[512];
#define LOG_DIR "%s"
static int g_known_game, g_detected_60fps, g_tile_override = -1, g_tile_sweep, g_t6_bit_override = -1, g_adapt_early_crashes = 0, g_post_budget = 1, g_late_tail = 1;
static char g_title_id[16] = "CUSA18723";
static char g_family_id[16] = "CUSA18723";
static char g_osd_toast[32]; static uint64_t g_osd_toast_until; static uint32_t g_last_disk_hash;
static void tile_load(int idx) { (void)idx; }
static void lg(const char *f, ...) { (void)f; }
static uint64_t sceKernelGetProcessTime(void) { return 0; }
static int sceKernelMkdir(const char *p, int m) { (void)p; (void)m; return 0; }
static FILE *g_rf;
static int sceKernelOpen(const char *p, int f, int m) { (void)f; (void)m; g_rf = fopen(p, "rb"); return g_rf ? 3 : -1; }
static long sceKernelRead(int fd, void *b, size_t n) { (void)fd; return (long)fread(b, 1, n, g_rf); }
static int sceKernelClose(int fd) { (void)fd; fclose(g_rf); return 0; }
""" + struct + r"""
static fg_config_t g_cfg;
static void cfg_reload_from_disk(int force) { (void)force; }
""" + funcs.replace('LOG_DIR "/config-%s.ini", g_title_id', 'LOG_DIR "/config-%s.ini", g_dir, g_title_id') + r"""
int main(int argc, char **argv) {
    snprintf(g_dir, sizeof(g_dir), "%s", argv[1]);
    char path[600]; snprintf(path, sizeof(path), "%s/config-CUSA18723.ini", g_dir);
    FILE *f = fopen(path, "w");   // a config written before the two settings existed
    fputs("[bb_probe]\nframegen = 1\nmode = 8\npreset = 0\nsharpness = 0\nupscale = 1\nfps_lock = 30\nhud_protect = 1\n", f);
    fclose(f);
    fg_config_t c; cfg_defaults(&c);
    if (!parse_ini_file(path, &c, 0)) return 10;
    printf("old %d %d\n", c.below30, c.shallow_queue);
    menu_adjust_row(&c, 8, 1); menu_adjust_row(&c, 9, -1);
    printf("menu %d %d\n", c.below30, c.shallow_queue);
    g_cfg = c;
    cfg_save_game_ini();
    fg_config_t d; cfg_defaults(&d);
    if (!parse_ini_file(path, &d, 0)) return 11;
    printf("reload %d %d\n", d.below30, d.shallow_queue);
    f = fopen(path, "r"); char line[128];
    while (fgets(line, sizeof line, f)) if (strstr(line, "below30") || strstr(line, "shallow_queue")) printf("file %s", line);
    fclose(f);
    return 0;
}
"""
        r = build_run(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout.splitlines()
        self.assertIn("old 0 1", out)
        self.assertIn("menu 1 0", out)
        self.assertIn("reload 1 0", out)
        self.assertIn("file below30 = 1", out)
        self.assertIn("file shallow_queue = 0", out)


@unittest.skipUnless(CLANG, "no host clang")
class TestApp(unittest.TestCase):
    def test_panel_rows_toggle_the_settings(self) -> None:
        src = r"""
#include "app.h"
#include <stdio.h>
static App g_app;
int main(void) {
    App *a = &g_app;
    a->ngames = 1; a->games[0].installed = 1; a->games[0].s = SETTINGS_DEFAULT;
    a->tab = TAB_GAMES; a->detail = 1; a->row = 0;
    Settings *s = &a->games[0].s;
    printf("default %d %d\n", s->b30, s->shq);
    for (int i = 0; i < 5; i++) app_input(a, PAD_DOWN);
    app_input(a, PAD_RIGHT);                       // row 5
    app_input(a, PAD_DOWN); app_input(a, PAD_LEFT); // row 6
    printf("toggled %d %d row %d dirty %d\n", s->b30, s->shq, a->row, a->dirty);
    app_input(a, PAD_DOWN); int r7 = a->row;
    app_input(a, PAD_DOWN); int wrap = a->row;        // row 7 is the last: Down wraps to 0
    app_input(a, PAD_UP);
    printf("last %d wrap %d now %d\n", r7, wrap, a->row);
    int act = app_input(a, PAD_RIGHT);               // Pipeline control: CUSTOM
    printf("pipeline %d act %d\n", a->games[0].dev.manual_mode, act);
    a->tab = TAB_DEFAULTS; a->row = 7; a->dark_theme = 1; app_input(a, PAD_RIGHT);
    printf("theme %d\n", a->dark_theme);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as td:
            t = pathlib.Path(td)
            (t / "h.c").write_text(src)
            exe = t / "h"
            r = subprocess.run([CLANG, "-O0", "-w", "-fno-builtin", "-I", str(ROOT / "launcher" / "src"), "-I", str(ROOT / "overlay"),
                                str(t / "h.c"), str(ROOT / "launcher" / "src" / "app.c"), str(ROOT / "launcher" / "src" / "ui.c"),
                                "-o", str(exe), "-lm"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
        self.assertIn("default 0 1", out)
        self.assertIn("toggled 1 0 row 6 dirty 1", out)
        self.assertIn("last 7 wrap 0 now 7", out)
        self.assertIn("pipeline 1 act 0", out)
        self.assertIn("theme 0", out)

    def test_old_ini_loads_the_defaults_and_both_files_carry_the_keys(self) -> None:
        ini_int = function(APP_MAIN, "ini_int")
        r = build_run("#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n" + ini_int + r"""
int main(void) {
    const char *old = "framegen=1\npreset=0\nsharpness=40\nenhance=1\noverlay=0\nfps_lock=30\n";
    const char *now = "framegen=1\nbelow30=0\nshallow_queue=0\n";
    printf("old %d %d\n", ini_int(old, "below30", 0) != 0, ini_int(old, "shallow_queue", 1) != 0);
    printf("new %d %d\n", ini_int(now, "below30", 0) != 0, ini_int(now, "shallow_queue", 1) != 0);
    return 0;
}
""")
        self.assertIn("old 0 1", r.stdout)
        self.assertIn("new 0 0", r.stdout)
        load = APP_MAIN[APP_MAIN.index("static void load_settings("):APP_MAIN.index("static void save_settings(")]
        self.assertEqual(load.count('ini_int(s, "below30", 0) != 0, ini_int(s, "shallow_queue", 1) != 0 }'), 1)
        self.assertEqual(load.count('ini_int(t, "below30", 0) != 0, ini_int(t, "shallow_queue", 1) != 0 }'), 1)
        save = function(APP_MAIN, "save_settings")
        self.assertEqual(save.count('below30=%d\\nshallow_queue=%d\\n'), 2)   # launcher ini + config-<TITLE>.ini
        self.assertIn("a->defaults.b30, a->defaults.shq,", save)
        self.assertIn("lk, ti, s->b30, s->shq);", save)
        self.assertIn("#define SETTINGS_DEFAULT ((Settings){ 1, 0, 40, 1, 0, 0, 1 })", (ROOT / "launcher" / "src" / "app.h").read_text())


if __name__ == "__main__":
    unittest.main()
