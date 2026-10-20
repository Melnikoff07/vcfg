// Hotkey of the in-game menu: the pad buttons that must be held together (for the hold time, 3 s by default).
// Shared by the launcher (choice + recording + files), the classic engine and the new engine, so all three agree.
//
// A combination is any set of at least two of L1, R1, L2, R2, L3, R3 (the app offers four presets and lets the player
// record their own). Anything else (a damaged file, a value written by hand, an old or newer app) falls back to L3 + R3,
// the original combination: the menu can never become unreachable and never opens by accident from a single button,
// a d-pad direction, or a button the menu itself uses (Options, Circle, Cross, Square, Triangle, PS, touchpad).
// ScePad button bits: L3 0x2, R3 0x4, L2 0x100, R2 0x200, L1 0x400, R1 0x800.
#pragma once
#include <stdint.h>

#define MENU_COMBO_DEFAULT 0x0006u
#define MENU_COMBO_ALLOWED 0x0F06u   // the only bits a hotkey may use
#define MENU_COMBO_COUNT 4           // presets

static const uint32_t k_menu_combo_mask[MENU_COMBO_COUNT] = { 0x0006u, 0x0C06u, 0x0306u, 0x0F00u };
static const char *const k_menu_combo_name[MENU_COMBO_COUNT] = { "L3 + R3", "L1 + R1 + L3 + R3", "L2 + R2 + L3 + R3", "L1 + R1 + L2 + R2" };

// buttons in display order: L1 R1 L2 R2 L3 R3
static const uint32_t k_menu_combo_bits[6] = { 0x0400u, 0x0800u, 0x0100u, 0x0200u, 0x0002u, 0x0004u };
static const char *const k_menu_combo_btn[6] = { "L1", "R1", "L2", "R2", "L3", "R3" };

static inline int menu_combo_buttons(uint32_t m) { int n = 0; for (int i = 0; i < 6; i++) if (m & k_menu_combo_bits[i]) n++; return n; }
// valid: only the six allowed buttons, at least two of them
static inline int menu_combo_valid(uint32_t m) { return (m & ~MENU_COMBO_ALLOWED) == 0 && menu_combo_buttons(m) >= 2; }
// preset index of a mask, -1 for a recorded (custom) or invalid mask
static inline int menu_combo_index(uint32_t m)
{
    for (int i = 0; i < MENU_COMBO_COUNT; i++) if (k_menu_combo_mask[i] == m) return i;
    return -1;
}
static inline uint32_t menu_combo_sanitize(uint32_t m) { return menu_combo_valid(m) ? m : MENU_COMBO_DEFAULT; }
static inline uint32_t menu_combo_from_index(int i) { return (i >= 0 && i < MENU_COMBO_COUNT) ? k_menu_combo_mask[i] : MENU_COMBO_DEFAULT; }
// "L1 + R1 + L3 + R3" (at most 27 characters), or the compact form without spaces "L1+R1+L3+R3" (at most 17)
static inline void menu_combo_text(uint32_t m, char *out, int n, int compact)
{
    int o = 0, first = 1;
    m = menu_combo_sanitize(m);
    for (int i = 0; i < 6; i++) {
        if (!(m & k_menu_combo_bits[i])) continue;
        const char *sep = first ? "" : (compact ? "+" : " + ");
        for (const char *s = sep; *s && o + 1 < n; s++) out[o++] = *s;
        for (const char *s = k_menu_combo_btn[i]; *s && o + 1 < n; s++) out[o++] = *s;
        first = 0;
    }
    if (n > 0) out[o] = 0;
}
static inline void menu_combo_name(uint32_t m, char *out, int n) { menu_combo_text(m, out, n, 0); }
// name without spaces, for 32-column OSD rows ("L1+R1+L2+R2")
static inline void menu_combo_compact(uint32_t m, char *out, int n) { menu_combo_text(m, out, n, 1); }
