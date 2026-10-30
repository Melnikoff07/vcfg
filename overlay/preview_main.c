// PC preview entry points for overlay/preview.py (built as a DLL, see that script).
#include "vov.h"
int _fltused = 0;   // MSVC ABI marker for float use (no CRT in the preview DLL)
void *memset(void *d, int v, size_t n) { volatile uint8_t *p = d; while (n--) *p++ = (uint8_t)v; return d; }
void *memcpy(void *d, const void *s, size_t n) { volatile uint8_t *p = d; const uint8_t *q = s; while (n--) *p++ = *q++; return d; }
static void cpy(char *d, const char *s, int n) { int i = 0; for (; s[i] && i < n - 1; i++) d[i] = s[i]; d[i] = 0; }
__declspec(dllexport) int vov_preview(uint8_t *mem, int scene, const uint8_t **pal, const uint8_t **pix, unsigned *w, unsigned *h)
{
    if (scene == 4) vov_init_size(mem, 0x48000); else vov_init(mem);
    vov_set_hdr(scene == 5);
    if (scene == 1) { VovCounter k = { 600, 167, 1 }; return vov_counter(&k, pal, pix, w, h); }
    if (scene == 2) { VovCounter k = { 598, 167, 0 }; return vov_counter(&k, pal, pix, w, h); }
    VovMenu m = {0};
    cpy(m.title, "Example Game\xe2\x84\xa2 Title", 40); m.out_fps_x10 = 598; m.game_fps_x10 = 301; m.sel = 0;
    // the classic engine's 11 rows (classic/source/main.c osd_build_slot)
    const char *lab[] = { "Frame generation", "Mode", "Sharpness", "Anti-aliasing", "FPS counter", "Game vectors", "HUD protection",
                          "FPS lock", "Below 30 FPS", "Low latency", "Opacity" };
    for (int i = 0; i < 11; i++) cpy(m.rows[i].label, lab[i], 28);
    m.rows[0].kind = VOV_TOGGLE; m.rows[0].on = 1;
    cpy(m.rows[1].value, "Quality", 24); m.rows[2].kind = VOV_SEGMENTS; m.rows[2].pct = 50;
    cpy(m.rows[3].value, "Light", 24); cpy(m.rows[4].value, "Detail TL", 24); m.rows[5].kind = VOV_TOGGLE;
    cpy(m.rows[6].value, "Conservative", 24); cpy(m.rows[7].value, "30", 24);
    m.rows[8].kind = VOV_TOGGLE; m.rows[8].on = 1; m.rows[9].kind = VOV_TOGGLE; m.rows[9].on = 1;
    m.rows[10].kind = VOV_SEGMENTS; m.rows[10].pct = 80;
    m.nrows = scene == 4 ? 7 : 11; if (scene == 3 || scene == 4) { m.sel = 2; cpy(m.status, "Inert: 60 fps game", 40); }
    cpy(m.hints, "\xe2\x86\x95 \xe2\x86\x90 \xe2\x86\x92  \xc3\x97 APPLY  \xe2\x96\xb3 SAVE  \xe2\x97\x8b CLOSE", 48);
    return vov_menu(&m, pal, pix, w, h);
}
