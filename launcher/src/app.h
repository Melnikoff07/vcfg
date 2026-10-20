// VCFG launcher: model, screens and input. Freestanding like ui.c (the platform layer does all IO).
#pragma once
#include "ui.h"

enum { ENGINE_NEW, ENGINE_CLASSIC, ENGINE_CLASSIC_BB };
enum { TAB_GAMES, TAB_DEFAULTS, TAB_OVERLAY, TAB_INSTALL, TAB_COUNT };

typedef struct { int fg, preset, sharp, enhance, overlay; } Settings;   // preset 0 quality 1 fast; overlay 0..8

typedef struct {
    char id[12], title[64], version[16];   // version: APP_VER of the installed game (with its update)
    int engine, installed, testing, experimental;   // experimental: not verified, plugin only when switched on
    const uint8_t *icon; int iw, ih;           // RGBA8, may be 0
    Settings s;
} Game;

typedef struct {
    Face logo, title, mono, num, big;
} Fonts;

typedef struct {
    Game games[48]; int ngames;
    int tab, sel, detail, row;
    Settings defaults;
    int overlay_pos, overlay_detail, overlay_opacity;   // shared overlay style
    uint32_t menu_combo;                                 // in-game menu hotkey: pad button mask (overlay/menu_combo.h), preset or recorded, L3 + R3 by default
    int rec, rec_wait; uint32_t rec_peak;                // recording of the hotkey: waiting for release / largest set held together
    char rec_msg[64];                                    // result of the last recording, shown next to the hotkey
    int plugin_state;                                    // 0 missing, 1 installed, 2 outdated
    int debug_log;
    char version[16];                                    // app and plugin version, shown in the header                                       // plugins write /data/bb_probe/probe.log
    char status[128];
    char log[6][96]; int nlog;
    int dirty;
    Fonts *f;
} App;

enum { ACT_NONE, ACT_SAVE_EXIT, ACT_INSTALL, ACT_REMOVE, ACT_SAVE, ACT_DEBUG, ACT_REPORT };
enum { PAD_UP = 0x10, PAD_RIGHT = 0x20, PAD_DOWN = 0x40, PAD_LEFT = 0x80, PAD_OPTIONS = 0x08,
       PAD_TRIANGLE = 0x1000, PAD_CIRCLE = 0x2000, PAD_CROSS = 0x4000, PAD_SQUARE = 0x8000, PAD_L1 = 0x400, PAD_R1 = 0x800 };

void app_draw(Canvas *c, App *a);
int  app_input(App *a, uint32_t pressed);      // edge-triggered buttons -> ACT_*
int  app_pad(App *a, uint32_t held);           // every frame, buttons held now: hotkey recording; 1 when a redraw is needed
