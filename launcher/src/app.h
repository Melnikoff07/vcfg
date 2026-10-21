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
enum { TAB_GAMES, TAB_DEFAULTS, TAB_OVERLAY, TAB_INSTALL, TAB_COUNT };

typedef struct { int fg, preset, sharp, enhance, overlay; } Settings;   // preset 0 quality 1 fast; overlay 0..8

typedef struct {
    char id[12], title[64], version[16];   // version: APP_VER of the installed game (with its update)
    int engine, def_buf, installed, testing, experimental;   // experimental: not verified, plugin only when switched on
    int eng_sel;                               // SEL_*: the player's engine choice (AUTO keeps engine)
    int buf_sel;                               // BUF_*: the player's buffer mode choice (AUTO keeps def_buf)
    const uint8_t *icon; int iw, ih;           // RGBA8, may be 0
    Settings s;
} Game;

// The engine a game runs with: the player's choice, AUTO = the table entry.
static inline int game_engine(const Game *g)
{
    switch (g->eng_sel) {
    case SEL_CLASSIC: return g->engine == ENGINE_CLASSIC_BB ? ENGINE_CLASSIC_BB : ENGINE_CLASSIC;
    case SEL_ALT: return ENGINE_CLASSIC_ALT;
    case SEL_NEW: return ENGINE_NEW;
    default: return g->engine;
    }
}

// The buffer mode a game runs with: the player's choice, AUTO = def_buf (BUF_2BUF for known 2-buffer titles, BUF_STD otherwise).
static inline int game_buf(const Game *g)
{
    switch (g->buf_sel) {
    case BUF_2BUF: return BUF_2BUF;
    case BUF_STD: return BUF_STD;
    default: return g->def_buf == BUF_2BUF ? BUF_2BUF : BUF_STD;
    }
}

typedef struct {
    Face logo, title, mono, num, big;
} Fonts;

typedef struct {
    Game games[160]; int ngames;   // verified table + every installed CUSA on the internal drive and ext0..ext3 (48 was tight with an HDD)
    int tab, sel, detail, row;
    Settings defaults;
    int overlay_pos, overlay_detail, overlay_opacity;   // shared overlay style
    uint32_t menu_combo;                                 // in-game menu hotkey: pad button mask (overlay/menu_combo.h), preset or recorded, L3 + R3 by default
    int rec; uint32_t rec_prev, rec_peak;                // recording of the hotkey: Cross starts, Cross saves; buttons held last frame / largest set of the latest press
    char rec_msg[64];                                    // result of the last recording, shown next to the hotkey
    int plugin_state;                                    // 0 missing, 1 installed, 2 outdated
    int debug_log;
    char version[16];                                    // app and plugin version, shown in the header
    char status[128];
    char log[6][96]; int nlog;
    int dirty;
    int clean_armed;                                     // Install tab: R2 pressed once, a second R2 deletes every VCFG file
    Fonts *f;
} App;

enum { ACT_NONE, ACT_SAVE_EXIT, ACT_INSTALL, ACT_REMOVE, ACT_SAVE, ACT_DEBUG, ACT_REPORT, ACT_CLEAN };
enum { PAD_UP = 0x10, PAD_RIGHT = 0x20, PAD_DOWN = 0x40, PAD_LEFT = 0x80, PAD_OPTIONS = 0x08,
       PAD_TRIANGLE = 0x1000, PAD_CIRCLE = 0x2000, PAD_CROSS = 0x4000, PAD_SQUARE = 0x8000, PAD_L1 = 0x400, PAD_R1 = 0x800, PAD_R2 = 0x200 };

void app_draw(Canvas *c, App *a);
int  app_input(App *a, uint32_t pressed);      // edge-triggered buttons -> ACT_*
int  app_pad(App *a, uint32_t held);           // every frame, buttons held now: hotkey recording; 1 when a redraw is needed
