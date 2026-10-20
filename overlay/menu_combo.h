// Hotkey of the in-game menu: the pad buttons that must be held together (for the hold time, 3 s by default).
// Shared by the launcher (choice + files), the classic engine and the new engine, so all three agree on one table.
//
// Only the combinations below are accepted. Anything else (a damaged file, a value written by hand, an old or newer app)
// falls back to L3 + R3, the original combination: the menu can never become unreachable and never opens by accident
// from a single button, a d-pad direction, or a button the menu itself uses (Options, Circle, Cross, Square, Triangle).
// ScePad button bits: L3 0x2, R3 0x4, L2 0x100, R2 0x200, L1 0x400, R1 0x800.
#pragma once
#include <stdint.h>

#define MENU_COMBO_DEFAULT 0x0006u
#define MENU_COMBO_COUNT 4

static const uint32_t k_menu_combo_mask[MENU_COMBO_COUNT] = { 0x0006u, 0x0C06u, 0x0306u, 0x0F00u };
static const char *const k_menu_combo_name[MENU_COMBO_COUNT] = { "L3 + R3", "L1 + R1 + L3 + R3", "L2 + R2 + L3 + R3", "L1 + R1 + L2 + R2" };

// table index of a mask, -1 when the mask is not one of the accepted combinations
static inline int menu_combo_index(uint32_t m)
{
    for (int i = 0; i < MENU_COMBO_COUNT; i++) if (k_menu_combo_mask[i] == m) return i;
    return -1;
}
static inline uint32_t menu_combo_sanitize(uint32_t m) { return menu_combo_index(m) >= 0 ? m : MENU_COMBO_DEFAULT; }
static inline uint32_t menu_combo_from_index(int i) { return (i >= 0 && i < MENU_COMBO_COUNT) ? k_menu_combo_mask[i] : MENU_COMBO_DEFAULT; }
// name without spaces, for 32-column OSD rows ("L1+R1+L2+R2")
static inline void menu_combo_compact(uint32_t m, char *out, int n)
{
    int i = menu_combo_index(m), o = 0;
    for (const char *s = k_menu_combo_name[i < 0 ? 0 : i]; *s && o + 1 < n; s++) if (*s != ' ') out[o++] = *s;
    if (n > 0) out[o] = 0;
}
