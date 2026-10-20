// Host test of overlay/menu_combo.h (the hotkey table shared by the app and both engines).
// Build and run with the toolchain LLVM: clang -O0 -emit-llvm -c menu_combo_test.c -o t.bc && lli t.bc
int printf(const char *, ...);
static unsigned long str_len(const char *s) { unsigned long n = 0; while (s[n]) n++; return n; }
static int has_space(const char *s) { for (; *s; s++) if (*s == 0x20) return 1; return 0; }
#include "../../overlay/menu_combo.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main(void)
{
    // the original combination stays the default and is table entry 0
    CHECK(MENU_COMBO_DEFAULT == 0x0006u && k_menu_combo_mask[0] == MENU_COMBO_DEFAULT && menu_combo_index(MENU_COMBO_DEFAULT) == 0);
    // forbidden bits: Options 0x8 (closes the menu), d-pad 0xF0, face buttons 0xF000, anything above 16 bits (PS, touchpad)
    const uint32_t forbidden = 0x0008u | 0x00F0u | 0xF000u | 0xFFFF0000u;
    for (int i = 0; i < MENU_COMBO_COUNT; i++) {
        uint32_t m = k_menu_combo_mask[i];
        CHECK(__builtin_popcount(m) >= 2);                 // never a single button
        CHECK((m & forbidden) == 0);                        // never a button the menu or the system uses
        CHECK(menu_combo_index(m) == i && menu_combo_from_index(i) == m && menu_combo_sanitize(m) == m);
        for (int j = 0; j < i; j++) CHECK(k_menu_combo_mask[j] != m);   // unique
        char c[24]; menu_combo_compact(m, c, sizeof(c));
        CHECK(str_len(c) > 0 && str_len(c) <= 11 && !has_space(c));      // fits a 32-column OSD row with " ...: HOLD 3 SECONDS"
        CHECK(str_len(k_menu_combo_name[i]) < 32);          // fits Row.value of the app
    }
    // everything else falls back to L3 + R3
    const uint32_t bad[] = { 0u, 0x0002u, 0x0004u, 0x0008u, 0x0001u, 0x4000u, 0x0402u, 0x0C00u, 0x0C07u, 0x0F06u, 0x10006u, 0xFFFFFFFFu, 0x80000000u, 0x0007u };
    for (unsigned i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) { CHECK(menu_combo_index(bad[i]) == -1); CHECK(menu_combo_sanitize(bad[i]) == MENU_COMBO_DEFAULT); }
    // a negative number from an ini (read with %d and cast) is also rejected
    CHECK(menu_combo_sanitize((uint32_t)-1) == MENU_COMBO_DEFAULT && menu_combo_sanitize((uint32_t)-6) == MENU_COMBO_DEFAULT);
    // index helpers are total
    CHECK(menu_combo_from_index(-1) == MENU_COMBO_DEFAULT && menu_combo_from_index(MENU_COMBO_COUNT) == MENU_COMBO_DEFAULT && menu_combo_from_index(1000) == MENU_COMBO_DEFAULT);
    printf(fails ? "menu_combo_test: %d FAILED\n" : "menu_combo_test: all passed\n", fails);
    return fails ? 1 : 0;
}
