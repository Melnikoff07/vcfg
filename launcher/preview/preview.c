// PC preview: renders a launcher screen into a caller buffer. Built as a dependency-free DLL (tools/preview.py).
#include "../src/app.h"

int _fltused = 0;
void *memset(void *d, int v, size_t n) { ui_memset(d, v, n); return d; }
void *memcpy(void *d, const void *s, size_t n) { ui_memcpy(d, s, n); return d; }

static App g_app;
static Fonts g_fonts;
static Arena g_arena;

__declspec(dllexport) int vcfg_preview(uint32_t *px, int w, int h, const uint8_t *logo, const uint8_t *text, const uint8_t *mono,
                                       uint8_t *mem, size_t memsize, int scene, int ngames, const char **titles, const char **ids,
                                       const uint8_t **icons, const int *iw, const int *ih)
{
    g_arena = (Arena){ mem, memsize, 0 };
    if (!face_init(&g_fonts.logo, logo, 124, &g_arena) || !face_init(&g_fonts.title, text, 37, &g_arena) ||
        !face_init(&g_fonts.mono, mono, 25, &g_arena) || !face_init(&g_fonts.num, logo, 80, &g_arena) ||
        !face_init(&g_fonts.big, logo, 150, &g_arena)) return -1;
    ui_memset(&g_app, 0, sizeof(g_app));
    App *a = &g_app; a->f = &g_fonts;
    a->defaults = (Settings){ 1, 0, 40, 1, 0 };
    a->overlay_pos = 4; a->overlay_opacity = 80; a->plugin_state = 1;
    for (int i = 0; i < ngames && i < 24; i++) {
        Game *g = &a->games[i];
        for (int k = 0; titles[i][k] && k < 63; k++) g->title[k] = titles[i][k];
        for (int k = 0; ids[i][k] && k < 11; k++) g->id[k] = ids[i][k];
        g->engine = i == 0 ? ENGINE_NEW : ENGINE_CLASSIC; g->installed = i < 4; g->testing = i == 3;
        g->icon = icons[i]; g->iw = iw[i]; g->ih = ih[i];
        g->s = (Settings){ i != 3, i == 1, 40, 1, 0 };
    }
    a->ngames = ngames;
    ui_memcpy(a->version, "2.26", 5);
    a->dark_theme = (scene == 9) ? 0 : 1;
    a->tab = scene == 2 ? TAB_DEFAULTS : scene == 3 ? TAB_OVERLAY : scene == 4 ? TAB_INSTALL :
             (scene >= 6 && scene <= 9) ? TAB_DEV : TAB_GAMES;
    a->detail = scene == 1; a->row = (scene >= 6 && scene <= 9) ? 0 : (scene == 1 ? 5 : 0); a->sel = scene == 5 ? 3 : 0;
    a->dev_expand = (scene == 7 || scene == 9) ? 1 : 0;
    if (scene >= 6 && scene <= 9 && ngames > 0) {
        DevCfg *d = &a->games[0].dev;
        d->manual_mode = (scene == 8 || scene == 7) ? 1 : 0;
        d->shave_mode = (scene == 8 || scene == 7) ? 2 : 0;
        d->has_crash = 1; d->safe_state = 1; d->has_adapt = 1;
        d->diag_kind = 1; d->rec_row = 2;
        ui_memcpy(d->diag_cause, "SIGSEGV: GPU / DIRECT MEM OOM", 30);
        ui_memcpy(d->diag_stage, "VRAM EXHAUSTED AT FLIP 14", 26);
        ui_memcpy(d->diag_advice, "MEMORY HOOK -> LATE (F120) / ALT", 33);
        ui_memcpy(d->diag_detail,
                  "Game ran out of DirectMemory (SIGSEGV at flip 14, alloc_fail=1). Early 64MB shave conflicted with engine boot pools.",
                  117);
        ui_memcpy(d->step1, "1. Press R2 to set Memory Hook -> LATE (F120).", 47);
        ui_memcpy(d->step2, "2. Or set Engine / Buffers -> ALT (LOW VRAM).", 46);
        ui_memcpy(d->step3, "3. Press SQUARE to re-test the game.", 37);
        d->n_tail = 4;
        ui_memcpy(d->log_tail[0], "[vcfg] boot title=CUSA00001 safe_state=1", 41);
        ui_memcpy(d->log_tail[1], "[vcfg] reg_buffers count=3 w=1920 h=1080 tile=13", 49);
        ui_memcpy(d->log_tail[2], "[vcfg] dmem_FAIL: AllocateDirectMemory 64MB err=0x8002000c", 59);
        ui_memcpy(d->log_tail[3], "CRASH SIGSEGV flips=14 in_submit=0 alloc_fail=1", 48);
    }
    if (scene == 4) { a->debug_log = 1; a->net_telemetry = 1; ui_memcpy(a->telemetry_host, "192.168.1.100", 14); a->telemetry_port = 8765; a->nlog = 2; ui_memcpy(a->log[0], "COPIED VCFG.PRX (395 KB)", 25); ui_memcpy(a->log[1], "PLUGINS.INI BACKED UP, 4 GAMES MAPPED", 38); }
    Canvas c = { px, w, h, w };
    app_draw(&c, a);
    return (int)g_arena.used;
}
