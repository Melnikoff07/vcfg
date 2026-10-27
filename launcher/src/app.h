// VCFG launcher: model, screens and input. Freestanding like ui.c (the platform layer does all IO).
#pragma once
#include "ui.h"

enum { ENGINE_NEW, ENGINE_CLASSIC, ENGINE_CLASSIC_BB, ENGINE_CLASSIC_ALT };
// Engine choice per game in the app: AUTO = the table / verified default, CLASSIC (vcfg_classic24t8.prx),
// ALT (memory-valve / arena-shaving / compact work memory engine, vcfg_alt_06.prx), or NEW (vcfg16.prx).
enum { SEL_AUTO, SEL_CLASSIC, SEL_ALT, SEL_NEW, SEL_COUNT };
// Buffer strategy per game (independent of Engine): AUTO = 2-buffer present copy when the game registers 2 buffers,
// 2-BUFFER = force present copy + virtual flip queue (two_buffer=1, present_copy=1), STANDARD = force standard mid buffers (present_copy=2).
enum { BUF_AUTO, BUF_2BUF, BUF_STD, BUF_COUNT };
enum { TAB_GAMES, TAB_DEFAULTS, TAB_OVERLAY, TAB_INSTALL, TAB_DEV, TAB_COUNT };
enum { DIAG_NONE, DIAG_OK, DIAG_OOM_BOOT, DIAG_REG_CONFLICT, DIAG_2BUF_NEEDED, DIAG_FLIP_STALL, DIAG_BOOT_EARLY, DIAG_INERT };

// preset 0 quality 1 fast; overlay 0..8; b30 / shq = classic engine below30 / shallow_queue (0 / 1)
typedef struct { int fg, preset, sharp, enhance, overlay, b30, shq; } Settings;
#define SETTINGS_DEFAULT ((Settings){ 1, 0, 40, 1, 0, 0, 1 })

typedef struct {
    int manual_mode;  // 0 = AUTO (ALGORITHM DECIDES), 1 = CUSTOM (MANUAL DEV OVERRIDE)
    int shave_mode;   // 0 AUTO (VALVE+SHAVE), 1 VALVE ONLY, 2 LATE ALLOC (FRAME 120)
    int reg_mode;     // 0 AUTO (EXTEND+RELOC), 1 TOP GROUP (16-N), 2 DEFERRED (FRAME 120)
    int pace_order;   // 0 AUTO, 1 PRE-SUBMIT, 2 POST-SUBMIT, 3 TIMER (PAIR)
    int post_mid;     // 0 AUTO, 1 MID ONLY, 2 MID + REAL
    int fps_lock;     // 0 = 30 FPS (default), 1 = 20 FPS, 2 = 60 FPS, 3 = UNLOCKED
    int tile_mode;    // 0 = AUTO, 1..6 = IDX 10, 14, 13, 9, 12, 11
    int safe_state;   // read from safe-<TITLE>.state: 0 EARLY, 1 DEFERRED, 2 PASSIVE
    int has_adapt;    // 1 if adapt-<TITLE>.ini exists
    int has_crash;    // 1 if crash-<TITLE>.txt exists
    int diag_kind;    // DIAG_* analyzed from crash / boot / trace / safe files
    int rec_row;      // 1..7 primary Dev Mode row recommended (0 = none)
    int rec_mask;     // bitmask (1<<1 .. 1<<7) of rows differing from Advisor target recipe
    int rec_applied;  // 1 if current settings already match the full Advisor target recipe
    int had_watchdog; // sticky safe_state > 0 seen in this launcher session
    int rec_manual;   // target manual_mode (0 = AUTO, 1 = CUSTOM)
    int rec_eng_sel;  // target SEL_*
    int rec_buf_sel;  // target BUF_*
    int rec_shave_mode;
    int rec_reg_mode;
    int rec_pace_order;
    int rec_post_mid;
    int rec_fps_lock;
    int rec_tile_mode;
    char diag_cause[64];
    char diag_stage[64];
    char diag_advice[64];
    char diag_detail[192];
    char step1[64], step2[64], step3[64];
    char log_tail[4][80];
    int n_tail;
} DevCfg;

typedef struct {
    char id[12], title[64], version[16];   // version: APP_VER of the installed game (with its update)
    int engine, def_buf, installed, testing, experimental;   // experimental: not verified, plugin only when switched on
    int eng_sel;                               // SEL_*: the player's engine choice (AUTO keeps engine)
    int buf_sel;                               // BUF_*: the player's buffer mode choice (AUTO keeps def_buf)
    const uint8_t *icon; int iw, ih;           // RGBA8, may be 0
    Settings s;
    DevCfg dev;
} Game;

// The engine a game runs with: when manual_mode is 0, AUTO = the table entry.
static inline int game_engine(const Game *g)
{
    if (!g->dev.manual_mode) return g->engine;
    switch (g->eng_sel) {
    case SEL_CLASSIC: return g->engine == ENGINE_CLASSIC_BB ? ENGINE_CLASSIC_BB : ENGINE_CLASSIC;
    case SEL_ALT: return ENGINE_CLASSIC_ALT;
    case SEL_NEW: return ENGINE_NEW;
    default: return g->engine;
    }
}

// The buffer mode a game runs with: when manual_mode is 0, AUTO = def_buf.
static inline int game_buf(const Game *g)
{
    if (!g->dev.manual_mode) return g->def_buf == BUF_2BUF ? BUF_2BUF : BUF_STD;
    switch (g->buf_sel) {
    case BUF_2BUF: return BUF_2BUF;
    case BUF_STD: return BUF_STD;
    default: return g->def_buf == BUF_2BUF ? BUF_2BUF : BUF_STD;
    }
}

// Combined Engine + Buffer preset index (0..5) for 05 DEV Row 1:
// 0 = AUTO, 1 = DEFAULT + 3-BUF, 2 = DEFAULT + 2-BUF, 3 = ALT + 3-BUF, 4 = ALT + 2-BUF, 5 = NEW + 3-BUF
static inline int game_pipe_preset(const Game *g)
{
    if (g->eng_sel == SEL_AUTO && g->buf_sel == BUF_AUTO) return 0;
    if (g->eng_sel == SEL_NEW) return 5;
    int is_alt = (g->eng_sel == SEL_ALT);
    int is_2b  = (g->buf_sel == BUF_2BUF) || (g->buf_sel == BUF_AUTO && g->def_buf == BUF_2BUF);
    if (is_alt) return is_2b ? 4 : 3;
    return is_2b ? 2 : 1;
}

static inline void game_set_pipe_preset(Game *g, int p)
{
    switch (p) {
    case 0: g->eng_sel = SEL_AUTO;    g->buf_sel = BUF_AUTO; break;
    case 1: g->eng_sel = SEL_CLASSIC; g->buf_sel = BUF_STD;  break;
    case 2: g->eng_sel = SEL_CLASSIC; g->buf_sel = BUF_2BUF; break;
    case 3: g->eng_sel = SEL_ALT;     g->buf_sel = BUF_STD;  break;
    case 4: g->eng_sel = SEL_ALT;     g->buf_sel = BUF_2BUF; break;
    case 5: g->eng_sel = SEL_NEW;     g->buf_sel = BUF_STD;  break;
    }
}

typedef struct {
    Face logo, title, mono, num, big;
} Fonts;

typedef struct {
    Game games[512]; int ngames;   // verified table + every installed CUSA on the internal drive and ext0..ext3 (512 supports large multi-TB libraries)
    int tab, sel, detail, row;
    Settings defaults;
    int overlay_pos, overlay_detail, overlay_opacity;   // shared overlay style
    uint32_t menu_combo;                                 // in-game menu hotkey: pad button mask (overlay/menu_combo.h), preset or recorded, L3 + R3 by default
    int rec; uint32_t rec_prev, rec_peak;                // recording of the hotkey: Cross starts, Cross saves; buttons held last frame / largest set of the latest press
    char rec_msg[64];                                    // result of the last recording, shown next to the hotkey
    int plugin_state;                                    // 0 missing, 1 installed, 2 outdated
    int debug_log;
    int net_telemetry;                                   // 1 = auto-send crash reports to VPS + send report on Circle
    char telemetry_host[64];                             // VPS IP or hostname from vcfg_launcher.ini / telemetry.ini
    int telemetry_port;                                  // VPS TCP port (default 8765)
    int dark_theme;                                      // 1 = dark theme (default), 0 = light theme
    int dev_expand;                                      // 1 = expanded full-screen Crash/Log Advisor in 05 DEV
    char version[16];                                    // app and plugin version, shown in the header
    char status[128];
    char log[6][96]; int nlog;
    int dirty;
    int clean_armed;                                     // Install tab: R2 pressed once, a second R2 deletes every VCFG file
    int turned_off;                                      // 1 while /data/bb_probe/vcfg_off exists (VCFG disabled until Cross on 04 PLUGIN)
    Fonts *f;
} App;

enum { ACT_NONE, ACT_SAVE_EXIT, ACT_INSTALL, ACT_REMOVE, ACT_SAVE, ACT_DEBUG, ACT_REPORT, ACT_CLEAN, ACT_RESET_DEV, ACT_LAUNCH, ACT_APPLY_ADVICE, ACT_TELEMETRY_TOGGLE };
enum { PAD_UP = 0x10, PAD_RIGHT = 0x20, PAD_DOWN = 0x40, PAD_LEFT = 0x80, PAD_OPTIONS = 0x08,
       PAD_TRIANGLE = 0x1000, PAD_CIRCLE = 0x2000, PAD_CROSS = 0x4000, PAD_SQUARE = 0x8000,
       PAD_L1 = 0x400, PAD_R1 = 0x800, PAD_L2 = 0x100, PAD_R2 = 0x200 };

void app_draw(Canvas *c, App *a);
int  app_input(App *a, uint32_t pressed);      // edge-triggered buttons -> ACT_*
int  app_pad(App *a, uint32_t held);           // every frame, buttons held now: hotkey recording; 1 when a redraw is needed
