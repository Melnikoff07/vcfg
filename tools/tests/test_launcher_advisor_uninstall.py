#!/usr/bin/env python3
"""Native host test harness for VCFG launcher (Advisor, settings, plugins.ini, uninstall).

Compiles the file/config/Advisor/install/uninstall logic from launcher/src/main.c
into a native shared library using the configured LLVM clang toolchain, redirecting
console paths (/data/..., /app0/...) into an isolated temporary directory per test.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MAIN_C = ROOT / "launcher" / "src" / "main.c"
APP_C = ROOT / "launcher" / "src" / "app.c"
APP_H = ROOT / "launcher" / "src" / "app.h"
UI_H = ROOT / "launcher" / "src" / "ui.h"
CONFIG_JSON = ROOT / "config.json"
FALLBACK_TOOLCHAINS = Path("/home/melnikoff/vcfg-pipeline/toolchains.json")

# Constants from launcher/src/app.h
ENGINE_NEW = 0
ENGINE_CLASSIC = 1
ENGINE_CLASSIC_BB = 2
ENGINE_CLASSIC_ALT = 3

SEL_AUTO = 0
SEL_CLASSIC = 1
SEL_ALT = 2
SEL_NEW = 3

BUF_AUTO = 0
BUF_2BUF = 1
BUF_STD = 2

TAB_GAMES = 0
TAB_DEFAULTS = 1
TAB_OVERLAY = 2
TAB_INSTALL = 3
TAB_DEV = 4
TAB_COUNT = 5

ACT_NONE = 0
ACT_SAVE_EXIT = 1
ACT_INSTALL = 2
ACT_REMOVE = 3
ACT_SAVE = 4
ACT_DEBUG = 5
ACT_REPORT = 6
ACT_CLEAN = 7
ACT_RESET_DEV = 8
ACT_LAUNCH = 9
ACT_APPLY_ADVICE = 10
ACT_TELEMETRY_TOGGLE = 11

DIAG_NONE = 0
DIAG_OK = 1
DIAG_OOM_BOOT = 2
DIAG_REG_CONFLICT = 3
DIAG_2BUF_NEEDED = 4
DIAG_FLIP_STALL = 5
DIAG_BOOT_EARLY = 6
DIAG_INERT = 7


class Settings(ctypes.Structure):
    _fields_ = [
        ("fg", ctypes.c_int),
        ("preset", ctypes.c_int),
        ("sharp", ctypes.c_int),
        ("enhance", ctypes.c_int),
        ("overlay", ctypes.c_int),
    ]


class DevCfg(ctypes.Structure):
    _fields_ = [
        ("manual_mode", ctypes.c_int),
        ("shave_mode", ctypes.c_int),
        ("reg_mode", ctypes.c_int),
        ("pace_order", ctypes.c_int),
        ("post_mid", ctypes.c_int),
        ("fps_lock", ctypes.c_int),
        ("tile_mode", ctypes.c_int),
        ("safe_state", ctypes.c_int),
        ("has_adapt", ctypes.c_int),
        ("has_crash", ctypes.c_int),
        ("diag_kind", ctypes.c_int),
        ("rec_row", ctypes.c_int),
        ("rec_mask", ctypes.c_int),
        ("rec_applied", ctypes.c_int),
        ("had_watchdog", ctypes.c_int),
        ("rec_manual", ctypes.c_int),
        ("rec_eng_sel", ctypes.c_int),
        ("rec_buf_sel", ctypes.c_int),
        ("rec_shave_mode", ctypes.c_int),
        ("rec_reg_mode", ctypes.c_int),
        ("rec_pace_order", ctypes.c_int),
        ("rec_post_mid", ctypes.c_int),
        ("rec_fps_lock", ctypes.c_int),
        ("rec_tile_mode", ctypes.c_int),
        ("diag_cause", ctypes.c_char * 64),
        ("diag_stage", ctypes.c_char * 64),
        ("diag_advice", ctypes.c_char * 64),
        ("diag_detail", ctypes.c_char * 192),
        ("step1", ctypes.c_char * 64),
        ("step2", ctypes.c_char * 64),
        ("step3", ctypes.c_char * 64),
        ("log_tail", (ctypes.c_char * 80) * 4),
        ("n_tail", ctypes.c_int),
    ]


class Game(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_char * 12),
        ("title", ctypes.c_char * 64),
        ("version", ctypes.c_char * 16),
        ("engine", ctypes.c_int),
        ("def_buf", ctypes.c_int),
        ("installed", ctypes.c_int),
        ("testing", ctypes.c_int),
        ("experimental", ctypes.c_int),
        ("eng_sel", ctypes.c_int),
        ("buf_sel", ctypes.c_int),
        ("icon", ctypes.c_void_p),
        ("iw", ctypes.c_int),
        ("ih", ctypes.c_int),
        ("s", Settings),
        ("dev", DevCfg),
    ]


class App(ctypes.Structure):
    _fields_ = [
        ("games", Game * 512),
        ("ngames", ctypes.c_int),
        ("tab", ctypes.c_int),
        ("sel", ctypes.c_int),
        ("detail", ctypes.c_int),
        ("row", ctypes.c_int),
        ("defaults", Settings),
        ("overlay_pos", ctypes.c_int),
        ("overlay_detail", ctypes.c_int),
        ("overlay_opacity", ctypes.c_int),
        ("menu_combo", ctypes.c_uint32),
        ("rec", ctypes.c_int),
        ("rec_prev", ctypes.c_uint32),
        ("rec_peak", ctypes.c_uint32),
        ("rec_msg", ctypes.c_char * 64),
        ("plugin_state", ctypes.c_int),
        ("debug_log", ctypes.c_int),
        ("net_telemetry", ctypes.c_int),
        ("telemetry_host", ctypes.c_char * 64),
        ("telemetry_port", ctypes.c_int),
        ("dark_theme", ctypes.c_int),
        ("dev_expand", ctypes.c_int),
        ("version", ctypes.c_char * 16),
        ("status", ctypes.c_char * 128),
        ("log", (ctypes.c_char * 96) * 6),
        ("nlog", ctypes.c_int),
        ("dirty", ctypes.c_int),
        ("clean_armed", ctypes.c_int),
        ("turned_off", ctypes.c_int),
        ("f", ctypes.c_void_p),
    ]


def _find_clang() -> str:
    for cfg_path in (CONFIG_JSON, FALLBACK_TOOLCHAINS):
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            llvm_bin = cfg.get("llvm_bin")
            if llvm_bin:
                cand = Path(llvm_bin) / "clang"
                if cand.exists():
                    return str(cand)
    found = shutil.which("clang")
    if not found:
        raise RuntimeError("clang not found")
    return found


def _extract_launcher_core_c() -> str:
    src = MAIN_C.read_text(encoding="utf-8")
    start_marker = "#define W 1920"
    end_marker = "// ---- debug log switch and bug report ----"
    s_idx = src.find(start_marker)
    e_idx = src.find(end_marker)
    if s_idx < 0 or e_idx < 0 or e_idx <= s_idx:
        raise RuntimeError("Failed to slice launcher/src/main.c markers")
    core_slice = src[s_idx:e_idx]

    startup_start = src.find("    load_settings(a);\n    a->debug_log = file_exists(")
    startup_end = src.find("    check_auto_send_crashes(a);", startup_start)
    if startup_start < 0 or startup_end < 0:
        raise RuntimeError("Failed to slice startup block from launcher/src/main.c")
    startup_slice = src[startup_start:startup_end]

    act_start = src.find("            if (act == ACT_INSTALL)")
    act_end = src.find("            else if (act == ACT_LAUNCH)", act_start)
    if act_start < 0 or act_end < 0:
        raise RuntimeError("Failed to slice act dispatch block from launcher/src/main.c")
    act_slice = src[act_start:act_end]

    app_c_src = (
        APP_C.read_text(encoding="utf-8")
        .replace('#include "app.h"', "")
        .replace('#include "../../overlay/menu_combo.h"', "")
    )
    return f"""
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdarg.h>
#include <dirent.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "{APP_H.as_posix()}"
#include "{(ROOT / 'overlay' / 'menu_combo.h').as_posix()}"
#include "{(ROOT / 'overlay' / 'title_families.h').as_posix()}"

static char g_drawn_texts[65536];
static size_t g_drawn_len;

int text_width(Face *f, const char *s, int tracking)
{{
    (void)f; (void)tracking;
    return s ? (int)strlen(s) * 8 : 0;
}}

int draw_text(Canvas *c, Face *f, int x, int baseline, const char *s, uint32_t color, int tracking)
{{
    (void)c; (void)f; (void)x; (void)baseline; (void)color; (void)tracking;
    if (!s || !*s) return 0;
    size_t l = strlen(s);
    if (g_drawn_len + l + 2 < sizeof(g_drawn_texts)) {{
        memcpy(g_drawn_texts + g_drawn_len, s, l);
        g_drawn_len += l;
        g_drawn_texts[g_drawn_len++] = '\\n';
        g_drawn_texts[g_drawn_len] = 0;
    }}
    return (int)l * 8;
}}

void fill_rect(Canvas *c, int x, int y, int w, int h, uint32_t color) {{ (void)c; (void)x; (void)y; (void)w; (void)h; (void)color; }}
void fill_round(Canvas *c, int x, int y, int w, int h, int r, uint32_t color) {{ (void)c; (void)x; (void)y; (void)w; (void)h; (void)r; (void)color; }}
void stroke_rect(Canvas *c, int x, int y, int w, int h, int t, uint32_t color) {{ (void)c; (void)x; (void)y; (void)w; (void)h; (void)t; (void)color; }}
void dashed_rect(Canvas *c, int x, int y, int w, int h, int t, int dash, uint32_t color) {{ (void)c; (void)x; (void)y; (void)w; (void)h; (void)t; (void)dash; (void)color; }}
void hatch(Canvas *c, int x, int y, int w, int h, int r, uint32_t a, uint32_t b, int period) {{ (void)c; (void)x; (void)y; (void)w; (void)h; (void)r; (void)a; (void)b; (void)period; }}
void blit_scaled(Canvas *c, const uint8_t *rgba, int sw, int sh, int x, int y, int w, int h, int r) {{ (void)c; (void)rgba; (void)sw; (void)sh; (void)x; (void)y; (void)w; (void)h; (void)r; }}
void draw_button(Canvas *c, int kind, int x, int cy, int size, uint32_t color) {{ (void)c; (void)kind; (void)x; (void)cy; (void)size; (void)color; }}

{app_c_src}

static char g_test_root[512];
static char g_outside_removed[512];

static const char *test_map_path(const char *path, char *buf, size_t cap)
{{
    if (!path) return NULL;
    if (g_test_root[0] && path[0] == '/') {{
        snprintf(buf, cap, "%s%s", g_test_root, path);
        return buf;
    }}
    return path;
}}

static void test_mkdir_p(const char *path)
{{
    char tmp[1024];
    snprintf(tmp, sizeof(tmp), "%s", path);
    for (char *p = tmp + 1; *p; p++) {{
        if (*p == '/') {{
            *p = 0;
            mkdir(tmp, 0777);
            *p = '/';
        }}
    }}
    mkdir(tmp, 0777);
}}

static FILE *test_fopen(const char *path, const char *mode)
{{
    char buf[1024];
    const char *mpath = test_map_path(path, buf, sizeof(buf));
    if (mode && (strchr(mode, 'w') || strchr(mode, 'a'))) {{
        char dir[1024];
        snprintf(dir, sizeof(dir), "%s", mpath);
        char *slash = strrchr(dir, '/');
        if (slash && slash != dir) {{
            *slash = 0;
            test_mkdir_p(dir);
        }}
    }}
    return fopen(mpath, mode);
}}

static int test_remove(const char *path)
{{
    if (path && strncmp(path, "/data/", 6) != 0 && strncmp(path, "/app0/", 6) != 0) {{
        snprintf(g_outside_removed, sizeof(g_outside_removed), "%s", path);
    }}
    char buf[1024];
    return remove(test_map_path(path, buf, sizeof(buf)));
}}

static int test_rmdir(const char *path)
{{
    if (path && strncmp(path, "/data/", 6) != 0 && strncmp(path, "/app0/", 6) != 0) {{
        snprintf(g_outside_removed, sizeof(g_outside_removed), "%s", path);
    }}
    char buf[1024];
    return rmdir(test_map_path(path, buf, sizeof(buf)));
}}

static int test_rename(const char *oldp, const char *newp)
{{
    char b1[1024], b2[1024];
    return rename(test_map_path(oldp, b1, sizeof(b1)), test_map_path(newp, b2, sizeof(b2)));
}}

static DIR *test_opendir(const char *path)
{{
    char buf[1024];
    return opendir(test_map_path(path, buf, sizeof(buf)));
}}

static int sceKernelMkdir(const char *path, int mode)
{{
    (void)mode;
    char buf[1024];
    test_mkdir_p(test_map_path(path, buf, sizeof(buf)));
    return 0;
}}

static unsigned char *stbi_load(const char *p, int *w, int *h, int *ch, int req)
{{
    (void)p; (void)w; (void)h; (void)ch; (void)req;
    return NULL;
}}

#define fopen test_fopen
#define remove test_remove
#define rmdir test_rmdir
#define rename test_rename
#define opendir test_opendir

{core_slice}

static void do_debug_toggle(App *a) {{ (void)a; }}
static void do_telemetry_toggle(App *a) {{ (void)a; }}
static void do_report(App *a) {{ (void)a; }}

void harness_startup(App *a)
{{
{startup_slice}
}}

void harness_dispatch_act(App *a, int act)
{{
{act_slice}
}}

#undef fopen
#undef remove
#undef rmdir
#undef rename
#undef opendir

static Fonts g_test_fonts;

const char *harness_draw_screen(App *a, int tab, int detail, int dev_expand)
{{
    g_test_fonts.logo.px = 124;
    g_test_fonts.title.px = 37;
    g_test_fonts.mono.px = 25;
    g_test_fonts.num.px = 80;
    g_test_fonts.big.px = 150;
    a->f = &g_test_fonts;
    a->tab = tab;
    a->detail = detail;
    a->dev_expand = dev_expand;
    g_drawn_len = 0;
    g_drawn_texts[0] = 0;
    Canvas c = {{ NULL, 1920, 1080, 1920 }};
    app_draw(&c, a);
    return g_drawn_texts;
}}

void harness_set_root(const char *root)
{{
    snprintf(g_test_root, sizeof(g_test_root), "%s", root ? root : "");
    g_outside_removed[0] = 0;
}}

const char *harness_outside_removed(void)
{{
    return g_outside_removed;
}}

void harness_init_app(App *a)
{{
    memset(a, 0, sizeof(*a));
    a->defaults = (Settings){{ 1, 0, 40, 1, 0 }};
    a->overlay_pos = 4;
    a->overlay_opacity = 80;
    a->menu_combo = MENU_COMBO_DEFAULT;
    a->dark_theme = 1;
    a->net_telemetry = 1;
    snprintf(a->telemetry_host, sizeof(a->telemetry_host), "62.60.230.108");
    a->telemetry_port = 8765;
    for (int i = 0; i < NSUP; i++) {{
        Game *g = &a->games[a->ngames++];
        snprintf(g->id, sizeof(g->id), "%s", k_sup[i].id);
        g->engine = k_sup[i].engine;
        g->def_buf = auto_2buf(g->id) ? BUF_2BUF : BUF_STD;
        g->testing = k_sup[i].testing;
        g->installed = 1;
        g->s = a->defaults;
        if (g->testing) g->s.fg = 0;
    }}
}}

int harness_add_game(App *a, const char *id, int experimental)
{{
    for (int i = 0; i < a->ngames; i++) {{
        if (!strcmp(a->games[i].id, id)) {{
            a->games[i].installed = 1;
            a->sel = i;
            return i;
        }}
    }}
    int idx = a->ngames++;
    Game *g = &a->games[idx];
    memset(g, 0, sizeof(*g));
    snprintf(g->id, sizeof(g->id), "%s", id);
    const Supported *sup = sup_for_title(id);
    g->engine = sup ? sup->engine : (auto_alt(id) ? ENGINE_CLASSIC_ALT : ENGINE_CLASSIC);
    g->def_buf = auto_2buf(id) ? BUF_2BUF : BUF_STD;
    g->testing = sup ? sup->testing : 0;
    g->experimental = sup ? 0 : experimental;
    g->installed = 1;
    g->s = a->defaults;
    if (g->testing || g->experimental) g->s.fg = 0;
    a->sel = idx;
    return idx;
}}

void harness_refresh_dev_status(Game *g) {{ refresh_dev_status(g); }}
void harness_load_settings(App *a) {{ load_settings(a); }}
void harness_save_settings(App *a) {{ save_settings(a); }}
void harness_do_apply_advice(App *a) {{ do_apply_advice(a); }}
void harness_do_reset_dev(App *a) {{ do_reset_dev(a); }}
void harness_do_install(App *a) {{ do_install(a); }}
void harness_do_clean(App *a) {{ do_clean(a); }}
int  harness_do_ini(App *a, int install) {{ return do_ini(a, install); }}
int  harness_plugin_state(void) {{ return plugin_state(); }}
int  harness_is_off(void) {{ return is_off(); }}
void harness_set_off(int on) {{ set_off(on); }}

char *harness_ini_update(App *a, const char *old_ini, int install)
{{
    build_map(a);
    size_t outn = 0;
    return ini_update(old_ini, install, &outn);
}}

void harness_free(void *p) {{ free(p); }}
"""


class LauncherHarness:
    def __init__(self) -> None:
        self._build_dir = tempfile.TemporaryDirectory(prefix="vcfg_launcher_lib_")
        c_path = Path(self._build_dir.name) / "launcher_harness.c"
        so_path = Path(self._build_dir.name) / "liblauncher_harness.so"
        c_path.write_text(_extract_launcher_core_c(), encoding="utf-8")
        clang = _find_clang()
        subprocess.run(
            [clang, "-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-variable", str(c_path), "-o", str(so_path)],
            check=True,
        )
        self.lib = ctypes.CDLL(str(so_path))
        self.lib.harness_set_root.argtypes = [ctypes.c_char_p]
        self.lib.harness_set_root.restype = None
        self.lib.harness_outside_removed.argtypes = []
        self.lib.harness_outside_removed.restype = ctypes.c_char_p
        self.lib.harness_init_app.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_init_app.restype = None
        self.lib.harness_add_game.argtypes = [ctypes.POINTER(App), ctypes.c_char_p, ctypes.c_int]
        self.lib.harness_add_game.restype = ctypes.c_int
        self.lib.harness_refresh_dev_status.argtypes = [ctypes.POINTER(Game)]
        self.lib.harness_refresh_dev_status.restype = None
        self.lib.harness_load_settings.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_load_settings.restype = None
        self.lib.harness_save_settings.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_save_settings.restype = None
        self.lib.harness_do_apply_advice.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_do_apply_advice.restype = None
        self.lib.harness_do_reset_dev.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_do_reset_dev.restype = None
        self.lib.harness_do_install.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_do_install.restype = None
        self.lib.harness_do_clean.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_do_clean.restype = None
        self.lib.harness_do_ini.argtypes = [ctypes.POINTER(App), ctypes.c_int]
        self.lib.harness_do_ini.restype = ctypes.c_int
        self.lib.harness_plugin_state.argtypes = []
        self.lib.harness_plugin_state.restype = ctypes.c_int
        self.lib.harness_is_off.argtypes = []
        self.lib.harness_is_off.restype = ctypes.c_int
        self.lib.harness_set_off.argtypes = [ctypes.c_int]
        self.lib.harness_set_off.restype = None
        self.lib.harness_startup.argtypes = [ctypes.POINTER(App)]
        self.lib.harness_startup.restype = None
        self.lib.harness_dispatch_act.argtypes = [ctypes.POINTER(App), ctypes.c_int]
        self.lib.harness_dispatch_act.restype = None
        self.lib.harness_draw_screen.argtypes = [ctypes.POINTER(App), ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.lib.harness_draw_screen.restype = ctypes.c_char_p
        self.lib.harness_ini_update.argtypes = [ctypes.POINTER(App), ctypes.c_char_p, ctypes.c_int]
        self.lib.harness_ini_update.restype = ctypes.c_void_p
        self.lib.harness_free.argtypes = [ctypes.c_void_p]
        self.lib.harness_free.restype = None

    def draw_screen(self, app: App, tab: int, detail: int = 0, dev_expand: int = 0) -> str:
        raw = self.lib.harness_draw_screen(ctypes.byref(app), tab, detail, dev_expand)
        return raw.decode("utf-8", errors="replace") if raw else ""

    def close(self) -> None:
        self._build_dir.cleanup()

    def ini_update(self, app: App, old_ini: str, install: int) -> str:
        ptr = self.lib.harness_ini_update(ctypes.byref(app), old_ini.encode("utf-8"), install)
        if not ptr:
            raise RuntimeError("ini_update returned NULL")
        try:
            return ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
        finally:
            self.lib.harness_free(ptr)


class Sandbox:
    def __init__(self, harness: LauncherHarness) -> None:
        self.h = harness
        self._tmp = tempfile.TemporaryDirectory(prefix="vcfg_ps4_fs_")
        self.root = Path(self._tmp.name)
        (self.root / "data" / "bb_probe").mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "GoldHEN" / "plugins").mkdir(parents=True, exist_ok=True)
        (self.root / "app0" / "plugins").mkdir(parents=True, exist_ok=True)
        for prx in ("vcfg16.prx", "vcfg_classic51.prx", "vcfg_alt_16.prx"):
            (self.root / "app0" / "plugins" / prx).write_bytes(f"ELF_{prx}".encode("utf-8"))
        self.h.lib.harness_set_root(str(self.root).encode("utf-8"))
        self.app = App()
        self.h.lib.harness_init_app(ctypes.byref(self.app))

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._tmp.cleanup()

    @property
    def cfgdir(self) -> Path:
        return self.root / "data" / "bb_probe"

    @property
    def goldhen(self) -> Path:
        return self.root / "data" / "GoldHEN"

    def write_cfg(self, name: str, content: str | bytes) -> Path:
        p = self.cfgdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        return p

    def select_game(self, title_id: str, experimental: bool = True) -> Game:
        idx = self.h.lib.harness_add_game(
            ctypes.byref(self.app), title_id.encode("utf-8"), 1 if experimental else 0
        )
        return self.app.games[idx]


def test_harness_baseline(h: LauncherHarness) -> None:
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA06548", experimental=False)
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_NONE, g.dev.diag_kind
        assert g.dev.rec_manual == 0, g.dev.rec_manual
        assert b"NO CRASH RECORDED" in g.dev.diag_cause, g.dev.diag_cause

        # Install populates /data/GoldHEN/plugins and /data/GoldHEN/plugins.ini
        h.lib.harness_do_install(ctypes.byref(sb.app))
        assert h.lib.harness_plugin_state() == 1
        ini_text = (sb.goldhen / "plugins.ini").read_text(encoding="utf-8")
        assert "[CUSA06548]\n/data/GoldHEN/plugins/vcfg_classic51.prx" in ini_text


def test_shave2_never_recommended_after_shave2_crash_or_failed(h: LauncherHarness) -> None:
    # Case 1: CUSA06548 (log 01c316fb35) — crash at flips=531 with shave_mode=2, alloc_fail=1
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA06548", experimental=False)
        sb.write_cfg(
            "crash-CUSA06548.txt",
            "==== VCFG CRASH REPORT v2.34 (CUSA06548, classic-3buf) ====\n"
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x20\n"
            "STAGE:  flips=531 in_submit=0 reg=1 gv=0 alloc_fail=1 shave=0\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=2 reg_mode=1 pace_order=2\n"
            "SELF-ADAPT: crash (flips=531, alloc_fail=1, reg=0) -> wrote shave_mode=0 reg_mode=1 pace_order=2 (rollback shave2_failed=1)\n",
        )
        sb.write_cfg(
            "boot-CUSA06548.txt",
            "0.00 [BOOT] 0.00 s (+0.00 s): plugin_load enter: CUSA06548 v2.34 (classic-3buf) fg=1 shave_mode=2 reg_mode=1 pace_order=2\n"
            "11.61 [BOOT] 11.61 s (+0.23 s): AllocateDirectMemory FAIL: size=53248KB align=64KB type=3 rc=0x8002000c (ENOMEM)\n",
        )
        sb.write_cfg(
            "adapt-CUSA06548.ini",
            "shave_mode = 0\nreg_mode = 1\npace_order = 2\nshave2_failed = 1\nadapt_ver = 234\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_OOM_BOOT, g.dev.diag_kind
        assert g.dev.rec_shave_mode == 0, f"CUSA06548 expected rec_shave_mode=0, got {g.dev.rec_shave_mode}"
        assert g.dev.rec_reg_mode == 1, f"CUSA06548 expected rec_reg_mode=1, got {g.dev.rec_reg_mode}"

    # Case 2: CUSA39420 (log bb7cd7aa3c) — crash at flips=482 with shave_mode=2, alloc_fail=1
    # Even if boot-<TITLE>.txt was rotated/truncated so only CFG_STATE in crash-<TITLE>.txt has shave_mode=2:
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA39420", experimental=True)
        sb.write_cfg(
            "crash-CUSA39420.txt",
            "==== VCFG CRASH REPORT v2.34 (CUSA39420, classic-3buf) ====\n"
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x28\n"
            "STAGE:  flips=482 in_submit=0 reg=1 gv=0 alloc_fail=1 shave=0\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=2 reg_mode=1 pace_order=2\n"
            "SELF-ADAPT: crash (flips=482, alloc_fail=1, reg=0) -> wrote shave_mode=0 reg_mode=1 pace_order=2 (rollback shave2_failed=1)\n",
        )
        sb.write_cfg(
            "boot-CUSA39420.txt",
            "14.11 [BOOT] 14.11 s (+0.23 s): AllocateDirectMemory FAIL: size=16384KB align=64KB type=3 rc=0x8002000c (ENOMEM)\n"
            "SELF-ADAPT: crash (flips=482, alloc_fail=1, reg=0) -> wrote shave_mode=0 reg_mode=1 pace_order=2 (rollback shave2_failed=1)\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_OOM_BOOT, g.dev.diag_kind
        assert g.dev.rec_shave_mode == 0, f"CUSA39420 expected rec_shave_mode=0, got {g.dev.rec_shave_mode}"

    # Case 3: CUSA25651 (log 2f560542e0) — adapt-CUSA25651.ini has shave2_failed=1, subsequent crash at shave_mode=0
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA25651", experimental=True)
        sb.write_cfg(
            "adapt-CUSA25651.ini",
            "shave_mode = 0\nreg_mode = 1\npace_order = 2\nshave2_failed = 1\nadapt_ver = 234\n",
        )
        sb.write_cfg(
            "crash-CUSA25651.txt",
            "==== VCFG CRASH REPORT v2.34 (CUSA25651, classic-3buf) ====\n"
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x0\n"
            "STAGE:  flips=380 in_submit=0 reg=1 gv=0 alloc_fail=1 shave=0\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=0 reg_mode=1 pace_order=2\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_shave_mode != 2, f"CUSA25651 with shave2_failed=1 must not recommend shave_mode=2, got {g.dev.rec_shave_mode}"

    # Case 4: load_settings — adapt-<TITLE>.ini has shave2_failed=1, config-<TITLE>.ini has manual_mode=1 and shave_mode=2
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA06548", experimental=False)
        sb.write_cfg(
            "adapt-CUSA06548.ini",
            "shave_mode = 0\nreg_mode = 1\npace_order = 2\nshave2_failed = 1\nadapt_ver = 234\n",
        )
        sb.write_cfg(
            "config-CUSA06548.ini",
            "framegen=1\nmanual_mode=1\nshave_mode=2\nreg_mode=1\npace_order=2\n",
        )
        h.lib.harness_load_settings(ctypes.byref(sb.app))
        assert g.dev.shave_mode == 0, f"load_settings let config-CUSA06548.ini override shave2_failed=1 with shave_mode={g.dev.shave_mode}"


def test_has_shave_log_matches_real_boot_and_crash_strings(h: LauncherHarness) -> None:
    # Case 1: boot log has "arena shave #1 OK:" (as in real log 01c316fb35:234 / bb7cd7aa3c:248), crash log has "shave=1"
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA18000", experimental=True)
        sb.write_cfg(
            "boot-CUSA18000.txt",
            "0.00 [BOOT] 0.00 s (+0.00 s): plugin_load enter: CUSA18000 v2.34 (classic-3buf) fg=1 shave_mode=0 reg_mode=1 pace_order=2\n"
            "0.00 [BOOT] 0.00 s (+0.00 s): arena shave #1 OK: addr=0x226800000 orig=256MB -> 208MB (shaved=48MB, total_shaved=48MB, swaps=1)\n"
            "5.91 [BOOT] 5.91 s (+0.04 s): AllocateDirectMemory FAIL: size=53248KB align=64KB type=3 rc=0x8002000c (ENOMEM)\n",
        )
        sb.write_cfg(
            "crash-CUSA18000.txt",
            "==== VCFG CRASH REPORT v2.34 (CUSA18000, classic-3buf) ====\n"
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x20\n"
            "STAGE:  flips=57 in_submit=0 reg=1 gv=1 alloc_fail=1 shave=1\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=0 reg_mode=1 pace_order=2\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_OOM_BOOT, g.dev.diag_kind
        assert g.dev.rec_shave_mode == 1, f"CUSA18000 with arena shave #1 OK expected rec_shave_mode=1, got {g.dev.rec_shave_mode}"

    # Case 2: crash log with multi-shave count (e.g. shave=12 in real log bb7cd7aa3c:13,295) and tail-only chunk (bb7cd7aa3c:260)
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA07408", experimental=True)
        sb.write_cfg(
            "boot-CUSA07408.txt",
            "0.00 [BOOT] 0.00 s (+0.00 s): arena shave #2 (tail-only chunk) OK: orig=128MB -> 96MB + tail=16MB\n",
        )
        sb.write_cfg(
            "crash-CUSA07408.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x0\n"
            "STAGE:  flips=14 in_submit=0 reg=1 gv=0 alloc_fail=2 shave=12\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=0 reg_mode=1 pace_order=2\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_shave_mode == 1, f"CUSA07408 with shave=12 expected rec_shave_mode=1, got {g.dev.rec_shave_mode}"


def test_do_apply_advice_removes_adapt_ini_on_manual_mode(h: LauncherHarness) -> None:
    # CUSA07408 (real log 0a1690ad06:110-112): stale adapt-CUSA07408.ini has shave_mode=2, reg_mode=2,
    # while Advisor recommends manual_mode=1, shave_mode=1, reg_mode=1.
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA07408", experimental=True)
        adapt_path = sb.write_cfg(
            "adapt-CUSA07408.ini",
            "shave_mode = 2\nreg_mode = 2\npace_order = 2\nadapt_ver = 234\n",
        )
        sb.write_cfg(
            "boot-CUSA07408.txt",
            "0.00 [BOOT] 0.00 s (+0.00 s): plugin_load enter: CUSA07408 v2.34 (classic-3buf) fg=1 shave_mode=0 reg_mode=1 pace_order=2\n"
            "0.00 [BOOT] 0.00 s (+0.00 s): arena shave #1 OK: addr=0x226800000 orig=256MB -> 208MB\n",
        )
        sb.write_cfg(
            "crash-CUSA07408.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x0\n"
            "STAGE:  flips=14 in_submit=0 reg=1 gv=0 alloc_fail=2 shave=12\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=0 reg_mode=1 pace_order=2\n",
        )
        h.lib.harness_do_apply_advice(ctypes.byref(sb.app))
        assert g.dev.manual_mode == 1
        assert g.dev.shave_mode == 1
        assert g.dev.reg_mode == 1
        assert not adapt_path.exists(), "do_apply_advice must remove adapt-CUSA07408.ini even when manual_mode=1"
        cfg_text = (sb.cfgdir / "config-CUSA07408.ini").read_text(encoding="utf-8")
        assert "manual_mode=1\n" in cfg_text
        assert "shave_mode=1\n" in cfg_text
        assert "reg_mode=1\n" in cfg_text


def test_inert_checked_before_safe_state_and_safe_no_work_mem(h: LauncherHarness) -> None:
    # Case 1: CUSA13323 (synthetic v2.34 safe_refuse("NO WORK MEM") at frame 120 based on classic/source/main.c:2140-2177
    # and multi_reg >= 2 from real log 38c675bb1d:541,554,561) writes safe-CUSA13323.state=1.
    # Must NOT diagnose "PREVIOUS BOOT STOPPED EARLY" and must recommend rec_shave_mode=0 (not 1 or 2).
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA13323", experimental=True)
        sb.write_cfg("safe-CUSA13323.state", "1\n")
        sb.write_cfg(
            "boot-CUSA13323.txt",
            "0.00 [BOOT] 0.00 s (+0.00 s): plugin_load enter: CUSA13323 v2.34 (classic-3buf) fg=1 shave_mode=2 reg_mode=2 pace_order=2\n"
            "0.04 [BOOT] 0.04 s (+0.04 s): RegisterBuffers enter: handle=1309737216 start=0 num=3\n"
            "4.12 [BOOT] 4.12 s (+4.08 s): RegisterBuffers enter: handle=1309737216 start=0 num=3\n"
            "6.50 [BOOT] 6.50 s (+2.38 s): safe mode frame 120: refused (NO WORK MEM) -> the game stays native\n",
        )
        sb.write_cfg(
            "trace-CUSA13323.txt",
            "6.50 safe mode: refused at frame 120 (NO WORK MEM)\n"
            "6.50 injection mode 8 (late=1, safe=1, inert='INERT: SAFE NO WORK MEM')\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert b"PREVIOUS BOOT STOPPED EARLY" not in g.dev.diag_cause, g.dev.diag_cause
        assert g.dev.rec_shave_mode == 0, f"CUSA13323 INERT: SAFE NO WORK MEM expected rec_shave_mode=0, got {g.dev.rec_shave_mode}"
        assert g.dev.rec_reg_mode == 1, f"CUSA13323 expected rec_reg_mode=1, got {g.dev.rec_reg_mode}"

    # Case 2: synthetic v2.34 INERT: SAFE NO WORK MEM with safe_state=0 (classic/source/main.c:2177)
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA03694", experimental=True)
        sb.write_cfg(
            "boot-CUSA03694.txt",
            "0.00 [BOOT] 0.00 s (+0.00 s): plugin_load enter: CUSA03694 v2.34 (classic-3buf) fg=1 shave_mode=2 reg_mode=2 pace_order=2\n"
            "5.20 [BOOT] 5.20 s (+5.20 s): safe mode frame 120: refused (NO WORK MEM) -> the game stays native\n",
        )
        sb.write_cfg(
            "trace-CUSA03694.txt",
            "5.20 safe mode: refused at frame 120 (NO WORK MEM)\n"
            "5.20 injection mode 8 (late=1, safe=1, inert='INERT: SAFE NO WORK MEM')\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_shave_mode == 0, f"CUSA03694 INERT: SAFE NO WORK MEM expected rec_shave_mode=0, got {g.dev.rec_shave_mode}"
        assert g.dev.rec_reg_mode == 1

    # Case 3: INERT: HDR/FMT in log while safe_state=1 is present on disk
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA06548", experimental=False)
        sb.write_cfg("safe-CUSA06548.state", "1\n")
        sb.write_cfg("boot-CUSA06548.txt", "0.00 [BOOT] 0.00 s: INERT: HDR/FMT 0x88740000\n")
        sb.write_cfg("trace-CUSA06548.txt", "0.10 inert='INERT: HDR/FMT'\n")
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_INERT, g.dev.diag_kind
        assert b"HDR10" in g.dev.diag_cause, g.dev.diag_cause


def test_no_false_is_2b_log_on_3buf_games_with_splash_num2(h: LauncherHarness) -> None:
    # Case 1: CUSA00341 (log cfabd8b5f6) — splash RegisterBuffers num=2 at 0.00s, then num=3 at 17.29s, ngame=3
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA00341", experimental=True)
        sb.write_cfg(
            "boot-CUSA00341.prev.txt",
            "17.29 [BOOT] 17.29 s (+0.02 s): RegisterBuffers enter: handle=1309737216 start=0 num=3\n",
        )
        sb.write_cfg(
            "trace-CUSA00341.txt",
            "0.00 RegisterBuffers handle=1309737216 start=0 num=2\n"
            "0.02 first SubmitAndFlip: count=2 handle=1309737216 index=0 flipMode=1\n"
            "17.29 RegisterBuffers handle=1309737216 start=0 num=3\n",
        )
        sb.write_cfg(
            "crash-CUSA00341.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x0\n"
            "STAGE:  flips=574 in_submit=1 reg=1 gv=1 alloc_fail=0 shave=1\n"
            "FG_STATE: active=1 vout_fg=0 two_buffer=0 pcopy=0 ngame=3 nmid=1\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=1 reg_mode=1 pace_order=1\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_buf_sel == BUF_AUTO, f"CUSA00341 3-buf game got false rec_buf_sel={g.dev.rec_buf_sel}"
        assert g.dev.rec_pace_order == 2, f"CUSA00341 3-buf game got false rec_pace_order={g.dev.rec_pace_order}"

    # Case 2: CUSA00918 (log 05a65b3c50) — RegisterBuffers num=2 at 0.00s then num=3 at 14.12s
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA00918", experimental=False)
        sb.write_cfg(
            "boot-CUSA00918.txt",
            "0.00 [BOOT] 0.00 s: RegisterBuffers enter: handle=1 start=0 num=2\n"
            "14.12 [BOOT] 14.12 s: RegisterBuffers enter: handle=1 start=0 num=3\n",
        )
        sb.write_cfg(
            "crash-CUSA00918.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x0\n"
            "STAGE:  flips=52 in_submit=0 reg=1 gv=0 alloc_fail=1 shave=1\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=0 reg_mode=1 pace_order=2\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_buf_sel == BUF_AUTO, f"CUSA00918 3-buf game got false rec_buf_sel={g.dev.rec_buf_sel}"
        assert g.dev.rec_pace_order == 2, f"CUSA00918 3-buf game got false rec_pace_order={g.dev.rec_pace_order}"

    # Case 2: CUSA10249 (log 472b1bc8fb) — RegisterBuffers num=2 at 0.00s then num=3 at 16.40s, SubmitAndFlip count=2
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA10249", experimental=True)
        sb.write_cfg(
            "boot-CUSA10249.txt",
            "0.00 [BOOT] 0.00 s: RegisterBuffers enter: handle=1 start=0 num=2\n"
            "16.40 [BOOT] 16.40 s: RegisterBuffers enter: handle=1 start=0 num=3\n",
        )
        sb.write_cfg(
            "trace-CUSA10249.txt",
            "0.00 RegisterBuffers handle=1 start=0 num=2\n"
            "0.02 first SubmitAndFlip: count=2 handle=1 index=0 flipMode=1\n"
            "16.40 RegisterBuffers handle=1 start=0 num=3\n",
        )
        sb.write_cfg(
            "crash-CUSA10249.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x0\n"
            "STAGE:  flips=48 in_submit=0 reg=1 gv=0 alloc_fail=1 shave=1\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=0 reg_mode=1 pace_order=2\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_buf_sel == BUF_AUTO, f"CUSA10249 3-buf game got false rec_buf_sel={g.dev.rec_buf_sel}"
        assert g.dev.rec_pace_order == 2, f"CUSA10249 3-buf game got false rec_pace_order={g.dev.rec_pace_order}"

    # Case 3: Genuine 2-buffer game log (only num=2, no num=3/4) still gets BUF_2BUF
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA90002", experimental=True)
        sb.write_cfg("boot-CUSA90002.txt", "0.00 [BOOT] 0.00 s: RegisterBuffers enter: handle=1 start=0 num=2\n")
        sb.write_cfg("trace-CUSA90002.txt", "0.10 inert: no mid buffers\n")
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_2BUF_NEEDED, g.dev.diag_kind
        assert g.dev.rec_buf_sel == BUF_2BUF, g.dev.rec_buf_sel


def test_advisor_ui_labels_for_zero_shave_and_reg_mode(h: LauncherHarness) -> None:
    # Case 1: DIAG_OOM_BOOT at flips=0 with shave_mode=2 -> rec_shave_mode=0, rec_reg_mode=0
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA06548", experimental=False)
        sb.write_cfg(
            "crash-CUSA06548.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x20\n"
            "STAGE:  flips=0 in_submit=0 reg=1 gv=0 alloc_fail=1 shave=0\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=2 reg_mode=2 pace_order=2\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_OOM_BOOT
        assert g.dev.rec_shave_mode == 0 and g.dev.rec_reg_mode == 0
        assert b"EARLIEST" in g.dev.diag_advice, f"Expected EARLIEST in diag_advice, got {g.dev.diag_advice!r}"
        assert b"ALL BUFFERS" in g.dev.diag_advice, f"Expected ALL BUFFERS in diag_advice, got {g.dev.diag_advice!r}"
        assert b"LATE (F120)" not in g.dev.diag_advice, g.dev.diag_advice
        assert b"DEFER REG" not in g.dev.diag_advice, g.dev.diag_advice
        assert b"EARLIEST" in g.dev.diag_detail and b"ALL BUFFERS" in g.dev.diag_detail, g.dev.diag_detail
        assert b"EARLIEST" in g.dev.step2 and b"ALL BUFFERS" in g.dev.step2, g.dev.step2

    # Case 2: DIAG_BOOT_EARLY at flips=0 with shave_mode=2, alloc_fail=0 -> rec_shave_mode=0, rec_reg_mode=0
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA07408", experimental=True)
        sb.write_cfg(
            "crash-CUSA07408.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x10\n"
            "STAGE:  flips=0 in_submit=0 reg=1 gv=0 alloc_fail=0 shave=0\n"
            "CFG_STATE: fg=1 mode=0 preset=0 shave_mode=2 reg_mode=2 pace_order=2\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_BOOT_EARLY
        assert g.dev.rec_shave_mode == 0 and g.dev.rec_reg_mode == 0
        assert b"EARLIEST" in g.dev.diag_advice and b"ALL BUFFERS" in g.dev.diag_advice, g.dev.diag_advice
        assert b"VALVE ONLY" not in g.dev.diag_advice and b"TOP GROUP" not in g.dev.diag_advice, g.dev.diag_advice
        assert b"EARLIEST" in g.dev.diag_detail and b"ALL BUFFERS" in g.dev.diag_detail, g.dev.diag_detail
        assert b"EARLIEST" in g.dev.step2 and b"ALL BUFFERS" in g.dev.step2, g.dev.step2


def test_third_party_prx_fault_any_module_and_nonzero_afail(h: LauncherHarness) -> None:
    # Case 1: CUSA18534 (log 8a95f0180a) — v2.33 stack trace has (OxagonLite_156.prx+0x87329), exact module name preserved
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA18534", experimental=False)
        sb.write_cfg(
            "crash-CUSA18534.txt",
            "==== VCFG CRASH REPORT v2.33 (CUSA18534, classic-3buf) ====\n"
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x0\n"
            "STAGE:  flips=12 in_submit=0 reg=1 gv=0 alloc_fail=0 shave=1\n"
            "STACK BACKTRACE (RSP):\n"
            "  [rsp+0x10] = 0x12345678 (OxagonLite_156.prx+0x87329)\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_manual == 0, g.dev.rec_manual
        assert b"OxagonLite_156.prx" in g.dev.diag_cause, f"Expected OxagonLite_156.prx in diag_cause, got {g.dev.diag_cause!r}"
        assert b"OxagonLite_156.prx" in g.dev.diag_advice, g.dev.diag_advice

    # Case 2: THIRD_PARTY_PRX_FAULT: module='CustomCheat.prx' with alloc_fail=1 (afail != 0)
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA06548", experimental=False)
        sb.write_cfg(
            "crash-CUSA06548.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0xdeadbeef\n"
            "STAGE:  flips=40 in_submit=0 reg=1 gv=0 alloc_fail=1 shave=1\n"
            "THIRD_PARTY_PRX_FAULT: module='CustomCheat.prx' -> SELF-ADAPT skipped (not a VCFG fault)\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_BOOT_EARLY, f"Expected third-party PRX fault even with alloc_fail=1, got {g.dev.diag_kind}"
        assert g.dev.rec_manual == 0, g.dev.rec_manual
        assert b"CustomCheat.prx" in g.dev.diag_cause, g.dev.diag_cause
        assert b"CustomCheat.prx" in g.dev.diag_advice, g.dev.diag_advice
        assert b"CustomCheat.prx" in g.dev.diag_detail, g.dev.diag_detail

    # Case 3: Unlisted third-party PRX in FAULT_ADDR / stack without THIRD_PARTY_PRX_FAULT marker and with alloc_fail=2
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA07408", experimental=True)
        sb.write_cfg(
            "crash-CUSA07408.txt",
            "SIGNAL: 11 (SIGSEGV) si_code=1 fault_addr=0x90001234\n"
            "STAGE:  flips=5 in_submit=0 reg=1 gv=0 alloc_fail=2 shave=0\n"
            "FAULT_ADDR: 0x90001234 -> FpsUnlockerMod.prx+0x1234 [seg0 base=0x90000000]\n"
            "  [rsp+0x00] = 0x80001000 (libkernel.sprx+0x1000)\n"
            "  [rsp+0x08] = 0x81002000 (vcfg_classic51.prx+0x2000)\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.rec_manual == 0, g.dev.rec_manual
        assert b"FpsUnlockerMod.prx" in g.dev.diag_cause, g.dev.diag_cause
        assert b"FpsUnlockerMod.prx" in g.dev.diag_advice, g.dev.diag_advice

    # Case 4: Real crash dump (CUSA06548 log 01c316fb35 / CUSA00341 log cfabd8b5f6) with LOADED MODULES listing
    # PS4Util.prx / Il2CppUserAssemblies.prx / game_patch.prx while the fault is in eboot.bin must NOT false-positive
    with Sandbox(h) as sb:
        g = sb.select_game("CUSA06548", experimental=False)
        sb.write_cfg(
            "crash-CUSA06548.txt",
            "=== CRASH SIGSEGV (sig=11 code=1) at 31.690s | flips=531 vo_flips=1061 in_submit=0 alloc_fail=1 shave=21 tight=1 ===\n"
            "  FAULT_ADDR: si_addr=0x0000000000000020 (?)\n"
            "  CFG_STATE: fg=1 mode=8 preset=0 shave_mode=2 reg_mode=1 pace_order=2 pcopy_cfg=0 two_buf=0 gv=1\n"
            "  UCONTEXT QWORDS (0..35):\n"
            "    uc[28] (+0x0e0) = 0x000000000462ef87  -> eboot.bin+0x422ef87 (seg0)\n"
            "  STACK BACKTRACE (from RSP=0x7efa5bd20):\n"
            "    [rsp+0x008] 0x0000000004610abc (eboot.bin+0x4210abc (seg0))\n"
            "    [rsp+0x010] 0x0000000802b41100 (PS4Util.prx+0x1100 (seg0))\n"
            "  LOADED MODULES (33):\n"
            "    [ 0] eboot.bin                    seg0=0x400000..0x6ef0000 seg1=0x6ef0000..0xb8f4000\n"
            "    [ 3] PS4Util.prx                  seg0=0x802b4000..0x802b8000 seg1=0x806b4000..0x806b8000\n"
            "    [ 4] Il2CppUserAssemblies.prx     seg0=0x80a00000..0x84000000 seg1=0x84000000..0x85000000\n"
            "    [31] game_patch.prx               seg0=0x8009a8000..0x8009b8000 seg1=0x8009c4000..0x8009c8000\n",
        )
        h.lib.harness_refresh_dev_status(ctypes.byref(g))
        assert g.dev.diag_kind == DIAG_OOM_BOOT, f"Expected DIAG_OOM_BOOT, got {g.dev.diag_kind} ({g.dev.diag_cause!r})"
        assert g.dev.rec_manual == 1
        assert g.dev.rec_shave_mode == 0


def test_uninstall_and_plugins_ini_preserves_logs_and_foreign_plugins(h: LauncherHarness) -> None:
    with Sandbox(h) as sb:
        g1 = sb.select_game("CUSA06548", experimental=False)
        g2 = sb.select_game("CUSA07408", experimental=True)
        g2.s.fg = 1

        # 1. Populate /data/GoldHEN/plugins with VCFG and foreign plugins
        plug_dir = sb.goldhen / "plugins"
        for vcfg_prx in ("vcfg16.prx", "vcfg_classic51.prx", "vcfg_alt_16.prx", "bb_probe.prx"):
            (plug_dir / vcfg_prx).write_bytes(b"VCFG_PRX")
        (plug_dir / "game_patch.prx").write_bytes(b"FOREIGN_PATCH")
        (plug_dir / "GoldHEN_cheat.prx").write_bytes(b"FOREIGN_CHEAT")

        # 2. Populate /data/GoldHEN/plugins.ini with mixed VCFG and foreign sections/lines
        initial_ini = (
            "; GoldHEN plugins.ini header comment\n"
            "[default]\n"
            "/data/GoldHEN/plugins/GoldHEN_cheat.prx\n"
            "[CUSA06548]\n"
            "/data/GoldHEN/plugins/game_patch.prx\n"
            "/data/GoldHEN/plugins/vcfg_classic51.prx\n"
            "[CUSA07408]\n"
            "/data/GoldHEN/plugins/vcfg16.prx\n"
            "[CUSA08519]\n"
            "[CUSA55555]\n"
            "/data/GoldHEN/plugins/vcfg16.prx\n"
            "[CUSA99999]\n"
            "/data/GoldHEN/plugins/AFR.prx\n"
        )
        (sb.goldhen / "plugins.ini").write_text(initial_ini, encoding="utf-8")
        (sb.goldhen / "plugins.ini.vcfg_backup_1700000000").write_text(initial_ini, encoding="utf-8")
        (sb.goldhen / "config.ini").write_text("goldhen_setting=1\n", encoding="utf-8")

        # 3. Populate /data/bb_probe with all non-log VCFG files + subdirectories
        non_log_files = [
            "vcfg_launcher.ini",
            "vcfg_engines.ini",
            "vcfg_buffers.ini",
            "vcfg.ini",
            "config.ini",
            "config-CUSA06548.ini",
            "config-CUSA07408.ini",
            "adapt-CUSA06548.ini",
            "safe-CUSA06548.state",
            "safe-CUSA06548.boot",
            "vcfg_ver_CUSA06548",
            "tile_CUSA06548",
            "state-CUSA06548.ini",
            "debug_log",
            "sent_crashes.ini",
            "cut_blocks_CUSA06548",
            "postfix_CUSA06548",
            "postfix_split_CUSA06548",
            "gridmix_CUSA06548",
            "post_mid_only_CUSA06548",
            "pace_timer_CUSA06548",
            "hud",
            "hud_CUSA06548",
            "inject",
            "gvbias",
            "scan_0.txt",
            "gv_vel_480x270_f32x4.bin",
            "dump_mid.rgba",
            "bench_capture",
            "bench_go",
            "a20_gpu_CUSA07439.csv",
            "profiles/CUSA06548.ini",
            "bench_CUSA06548_1/bench_0001.json",
        ]
        for rel in non_log_files:
            sb.write_cfg(rel, "vcfg_data=1\n")

        # 4. Populate log files that MUST be preserved
        kept_logs = [
            "boot-CUSA06548.txt",
            "boot-CUSA06548.prev.txt",
            "trace-CUSA06548.txt",
            "trace-CUSA06548.prev.txt",
            "crash-CUSA06548.txt",
            "voutrace-CUSA06548.txt",
            "discovery2-CUSA06548.log",
            "probe.log",
            "probe.prev.log",
            "telemetry.ini",
        ]
        for rel in kept_logs:
            sb.write_cfg(rel, f"LOG_CONTENT_{rel}\n")
        (sb.root / "data" / "vcfg_launcher.log").write_text("launcher log\n", encoding="utf-8")
        (sb.root / "data" / "vcfg_report.txt").write_text("report log\n", encoding="utf-8")
        (sb.root / "data" / "foreign_app.dat").write_text("keep me\n", encoding="utf-8")

        # Run uninstall
        h.lib.harness_do_clean(ctypes.byref(sb.app))

        # Verify nothing outside /data or /app0 was touched
        assert h.lib.harness_outside_removed() == b""
        assert (sb.root / "data" / "foreign_app.dat").exists()
        assert (sb.goldhen / "config.ini").exists()

        # Verify VCFG .prx and backups are gone, foreign .prx remain
        for vcfg_prx in ("vcfg16.prx", "vcfg_classic51.prx", "vcfg_alt_16.prx", "bb_probe.prx"):
            assert not (plug_dir / vcfg_prx).exists(), f"{vcfg_prx} was not deleted"
        assert (plug_dir / "game_patch.prx").exists()
        assert (plug_dir / "GoldHEN_cheat.prx").exists()
        assert not list(sb.goldhen.glob("plugins.ini.vcfg_backup_*"))

        # Verify plugins.ini has NO VCFG lines or empty VCFG sections, and preserves foreign sections/plugins
        cleaned_ini = (sb.goldhen / "plugins.ini").read_text(encoding="utf-8")
        assert "vcfg" not in cleaned_ini and "bb_probe" not in cleaned_ini, cleaned_ini
        assert "[CUSA07408]" not in cleaned_ini, f"Empty VCFG section [CUSA07408] remained:\n{cleaned_ini}"
        assert "[CUSA08519]" not in cleaned_ini, f"Empty VCFG section [CUSA08519] remained:\n{cleaned_ini}"
        assert "[CUSA55555]" not in cleaned_ini, f"Uninstalled VCFG section [CUSA55555] remained:\n{cleaned_ini}"
        assert "[default]\n/data/GoldHEN/plugins/GoldHEN_cheat.prx\n" in cleaned_ini, cleaned_ini
        assert "[CUSA06548]\n/data/GoldHEN/plugins/game_patch.prx\n" in cleaned_ini, cleaned_ini
        assert "[CUSA99999]\n/data/GoldHEN/plugins/AFR.prx\n" in cleaned_ini, cleaned_ini

        # Verify all non-log VCFG files and subdirectories are gone
        for rel in non_log_files:
            assert not (sb.cfgdir / rel).exists(), f"Non-log VCFG file {rel} was not deleted"
        assert not (sb.cfgdir / "profiles").exists(), "profiles/ directory was not removed"
        assert not (sb.cfgdir / "bench_CUSA06548_1").exists(), "bench_CUSA06548_1/ directory was not removed"

        # Verify ALL logs are preserved!
        for rel in kept_logs:
            assert (sb.cfgdir / rel).exists(), f"Log file {rel} was deleted by uninstall!"
        assert (sb.root / "data" / "vcfg_launcher.log").exists(), "vcfg_launcher.log was deleted!"
        assert (sb.root / "data" / "vcfg_report.txt").exists(), "vcfg_report.txt was deleted!"

        # Verify plugin_state() is 0 (NOT INSTALLED) and turned-off marker is set
        assert h.lib.harness_plugin_state() == 0
        assert h.lib.harness_is_off() == 1
        assert g1.dev.manual_mode == 0


def test_turned_off_banner_and_no_save_while_off(h: LauncherHarness) -> None:
    """Scenario: wipe all -> install new PKG -> launch new launcher while /data/bb_probe/vcfg_off exists."""
    with Sandbox(h) as sb:
        sb.select_game("CUSA06548", experimental=False)
        h.lib.harness_do_install(ctypes.byref(sb.app))
        assert h.lib.harness_plugin_state() == 1

        # 1. User wipes VCFG (leaves /data/bb_probe/vcfg_off)
        h.lib.harness_do_clean(ctypes.byref(sb.app))
        assert h.lib.harness_is_off() == 1
        assert h.lib.harness_plugin_state() == 0
        assert (sb.cfgdir / "vcfg_off").exists()

        # 2. User installs new PKG (updates /app0/plugins/*.prx) and launches fresh App instance
        for prx in ("vcfg16.prx", "vcfg_classic51.prx", "vcfg_alt_16.prx"):
            (sb.root / "app0" / "plugins" / prx).write_bytes(f"NEW_VER_{prx}".encode("ascii") * 64)
        fresh_app = App()
        h.lib.harness_init_app(ctypes.byref(fresh_app))
        h.lib.harness_startup(ctypes.byref(fresh_app))

        # Marker must NOT be auto-removed on startup, and .prx must remain uninstalled
        assert h.lib.harness_is_off() == 1
        assert h.lib.harness_plugin_state() == 0
        for prx in ("vcfg16.prx", "vcfg_classic51.prx", "vcfg_alt_16.prx"):
            assert not (sb.goldhen / "plugins" / prx).exists()

        # 3. Prominent "VCFG IS TURNED OFF" banner must be drawn on ALL tabs (01 GAMES, 02 DEFAULTS, 03 OVERLAY, 04 PLUGIN, 05 DEV)
        for tab, detail, dev_exp in [
            (TAB_GAMES, 0, 0),
            (TAB_GAMES, 1, 0),
            (TAB_DEFAULTS, 0, 0),
            (TAB_OVERLAY, 0, 0),
            (TAB_INSTALL, 0, 0),
            (TAB_DEV, 0, 0),
            (TAB_DEV, 0, 1),
        ]:
            drawn = h.draw_screen(fresh_app, tab, detail, dev_exp)
            assert "VCFG IS TURNED OFF" in drawn and "PLUGIN" in drawn, (
                f"Tab {tab} (detail={detail}, dev_expand={dev_exp}) missing turned-off banner:\n{drawn}"
            )

        # 4. Triggering ACT_SAVE (or save_settings / do_apply_advice / do_reset_dev) while off must NOT write game configs
        h.lib.harness_dispatch_act(ctypes.byref(fresh_app), ACT_SAVE)
        h.lib.harness_do_apply_advice(ctypes.byref(fresh_app))
        h.lib.harness_do_reset_dev(ctypes.byref(fresh_app))
        assert not (sb.cfgdir / "vcfg_launcher.ini").exists(), "vcfg_launcher.ini was written while VCFG is turned off!"
        assert not (sb.cfgdir / "config-CUSA06548.ini").exists(), "config-CUSA06548.ini was written while VCFG is turned off!"
        assert not (sb.cfgdir / "vcfg.ini").exists(), "vcfg.ini was written while VCFG is turned off!"
        assert not (sb.cfgdir / "vcfg_engines.ini").exists(), "vcfg_engines.ini was written while VCFG is turned off!"
        assert not (sb.cfgdir / "vcfg_buffers.ini").exists(), "vcfg_buffers.ini was written while VCFG is turned off!"
        assert b"VCFG IS TURNED OFF" in fresh_app.status, f"Expected turned-off status, got {fresh_app.status!r}"

        # 5. Pressing Cross on 04 PLUGIN (ACT_INSTALL) enables VCFG, clears vcfg_off, installs .prx, writes configs, and removes banner
        h.lib.harness_dispatch_act(ctypes.byref(fresh_app), ACT_INSTALL)
        assert h.lib.harness_is_off() == 0
        assert h.lib.harness_plugin_state() == 1
        assert (sb.cfgdir / "vcfg_launcher.ini").exists()
        assert (sb.cfgdir / "config-CUSA06548.ini").exists()
        drawn_after = h.draw_screen(fresh_app, TAB_GAMES, 0, 0)
        assert "VCFG IS TURNED OFF" not in drawn_after, f"Banner still shown after ACT_INSTALL:\n{drawn_after}"


def main() -> int:
    h = LauncherHarness()
    try:
        test_harness_baseline(h)
        test_shave2_never_recommended_after_shave2_crash_or_failed(h)
        test_has_shave_log_matches_real_boot_and_crash_strings(h)
        test_do_apply_advice_removes_adapt_ini_on_manual_mode(h)
        test_inert_checked_before_safe_state_and_safe_no_work_mem(h)
        test_no_false_is_2b_log_on_3buf_games_with_splash_num2(h)
        test_advisor_ui_labels_for_zero_shave_and_reg_mode(h)
        test_third_party_prx_fault_any_module_and_nonzero_afail(h)
        test_uninstall_and_plugins_ini_preserves_logs_and_foreign_plugins(h)
        test_turned_off_banner_and_no_save_while_off(h)
    finally:
        h.close()
    print("OK: launcher test harness checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
