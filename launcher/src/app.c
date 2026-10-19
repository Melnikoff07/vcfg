#include "app.h"

// Palette (approved design: variant 2 colours, horizontal card row).
#define CREAM 0xFFEFEEE8u
#define INK   0xFF0B0B0Bu
#define RED   0xFFF2502Eu
#define YEL   0xFFF5D23Au
#define BLUE  0xFF4F86E6u
#define DIM   0xFF6E6C66u

#define M 77            // outer margin
#define FOOT 72         // footer height
static const uint32_t k_card[4] = { YEL, BLUE, RED, CREAM };

static void itoa_(int v, char *o) { char t[12]; int n = 0, neg = v < 0; if (neg) v = -v; do { t[n++] = (char)('0' + v % 10); v /= 10; } while (v); int k = 0; if (neg) o[k++] = '-'; while (n) o[k++] = t[--n]; o[k] = 0; }
static void cat(char *d, const char *s) { while (*d) d++; while (*s) *d++ = *s++; *d = 0; }

static const char *mode_name(int p) { return p ? "FAST" : "QUALITY"; }
static const char *aa_name(int e) { return e == 2 ? "STRONG" : e == 1 ? "LIGHT" : "OFF"; }
static const char *engine_name(int e) { return e == ENGINE_NEW ? "NEW" : "CLASSIC"; }
static const char *corner_name(int ov)
{
    static const char *n[9] = { "OFF", "SMALL TL", "DETAIL TL", "SMALL TR", "DETAIL TR", "SMALL BR", "DETAIL BR", "SMALL BL", "DETAIL BL" };
    return (ov >= 0 && ov <= 8) ? n[ov] : "OFF";
}

// ---- header: logo plate, subtitle, tabs ----
static int draw_header(Canvas *c, App *a)
{
    Fonts *f = a->f;
    int top = 58, cap = (int)(f->logo.px * 0.727f);
    int lw = text_width(&f->logo, "VCFG", -6);
    int ph = cap + 17 + 12;
    fill_rect(c, M, top, lw + 56, ph, RED);
    draw_text(c, &f->logo, M + 28, top + 17 + cap, "VCFG", INK, -6);
    int bottom = top + ph;
    draw_text(c, &f->mono, M + lw + 56 + 34, bottom - 46, "VIBE CODED", INK, 1);
    draw_text(c, &f->mono, M + lw + 56 + 34, bottom - 10, "FRAME GENERATOR", INK, 1);
    static const char *tabs[TAB_COUNT] = { "01 GAMES", "02 DEFAULTS", "03 OVERLAY", "04 INSTALL" };
    int x = c->w - M;
    for (int i = TAB_COUNT - 1; i >= 0; i--) {
        int w = text_width(&f->mono, tabs[i], 1);
        x -= w;
        if (i == a->tab) { fill_rect(c, x - 14, bottom - 40, w + 28, 50, INK); draw_text(c, &f->mono, x, bottom - 6, tabs[i], CREAM, 1); }
        else draw_text(c, &f->mono, x, bottom - 6, tabs[i], INK, 1);
        x -= 52;
    }
    return bottom;
}

// ---- footer: pixel strip + black hint bar ----
typedef struct { int btn; const char *label; } Hint;
static void draw_footer(Canvas *c, App *a, const Hint *h, int n)
{
    Fonts *f = a->f;
    int y = c->h - FOOT, bw = 60;
    for (int i = 0; i < n; i++) bw += 44 + text_width(&f->mono, h[i].label, 1) + 40;
    int bx = c->w - bw;
    fill_rect(c, bx, y, bw, FOOT, INK);
    int x = bx + 40;
    for (int i = 0; i < n; i++) {
        draw_button(c, h[i].btn, x, y + FOOT / 2, 30, YEL);
        x += 44;
        x += draw_text(c, &f->mono, x, y + FOOT / 2 + 10, h[i].label, CREAM, 1) + 40;
    }
    // Pixel strip: two rows of 36 px cells, sparse top row, dense bottom row (deterministic pattern).
    static const uint32_t pal[4] = { RED, YEL, BLUE, BLUE };
    int cell = 36;
    for (int col = 0; col * cell < bx; col++) {
        unsigned hsh = (unsigned)(col * 2654435761u);
        if ((hsh >> 7) % 3 == 0) fill_rect(c, col * cell, y, cell, cell, pal[(hsh >> 11) % 3]);
        fill_rect(c, col * cell, y + cell, cell, cell, (hsh >> 13) % 4 == 0 ? pal[(hsh >> 17) % 2] : BLUE);
    }
}

// Word wrap a title into at most two lines that fit width w (second line is shortened with "..").
static void wrap2(Face *f, const char *s, int w, char *l1, char *l2)
{
    int n = 0, cut = -1; l1[0] = l2[0] = 0;
    for (int i = 0; s[i] && i < 62; i++) {
        l1[i] = s[i]; l1[i + 1] = 0; n = i + 1;
        if (s[i] == ' ') cut = i;
        if (text_width(f, l1, 0) > w) {
            if (cut > 0) { l1[cut] = 0; s += cut + 1; } else { l1[i] = 0; s += i; }
            int k = 0;
            for (; s[k] && k < 60; k++) {
                l2[k] = s[k]; l2[k + 1] = 0;
                if (text_width(f, l2, 0) > w) { while (k > 0 && text_width(f, l2, 0) > w - text_width(f, "..", 0)) l2[k--] = 0; l2[k] = 0; cat(l2, ".."); break; }
            }
            return;
        }
    }
    (void)n;
}

// ---- games: horizontal card row ----
static void draw_card(Canvas *c, App *a, Game *g, int idx, int x, int y, int w, int h, int sel)
{
    Fonts *f = a->f;
    uint32_t bg = g->testing ? CREAM : k_card[idx % 3];
    if (!g->installed) bg = CREAM;
    if (sel) { fill_round(c, x, y, w, h, 22, INK); fill_round(c, x + 8, y + 8, w - 16, h - 16, 15, bg); }
    else fill_round(c, x, y, w, h, 22, bg);
    if (!sel && (g->testing || !g->installed)) dashed_rect(c, x, y, w, h, 4, 14, INK);
    int p = 22, isz = w - 2 * p;
    if (g->icon) blit_scaled(c, g->icon, g->iw, g->ih, x + p, y + p, isz, isz, 11);
    else hatch(c, x + p, y + p, isz, isz, 11, INK, 0xFF232323u, 34);
    char l1[64], l2[64]; wrap2(&f->title, g->title[0] ? g->title : g->id, isz, l1, l2);
    int ty = y + p + isz + 50;
    draw_text(c, &f->title, x + p, ty, l1, INK, 0);
    if (l2[0]) draw_text(c, &f->title, x + p, ty + 44, l2, INK, 0);
    if (g->installed) { char eng[32] = ""; cat(eng, engine_name(g->engine)); cat(eng, " ENGINE"); draw_text(c, &f->mono, x + p, ty + (l2[0] ? 92 : 48), eng, INK, 1); }
    int ry = y + h - 98;
    fill_rect(c, x + p, ry, isz, 4, INK);
    char lab[48] = "";
    if (!g->installed) cat(lab, "NOT INSTALLED");
    else if (g->testing) cat(lab, "UNSTABLE, OFF");
    else if (!g->s.fg) cat(lab, "FG OFF");
    else cat(lab, mode_name(g->s.preset));
    draw_text(c, &f->mono, x + p, y + h - 30, lab, INK, 1);
    const char *num = (g->installed && g->s.fg) ? "60" : "30";
    int nw = text_width(&f->num, num, -3);
    draw_text(c, &f->num, x + w - p - nw, y + h - 26, num, (g->installed && g->s.fg) ? INK : RED, -3);
}
static void draw_games(Canvas *c, App *a, int top)
{
    int y = top + 58, h = c->h - FOOT - 58 - y, w = 403, gap = 46;
    if (!a->ngames) { draw_text(c, &a->f->title, M, y + 60, "No supported games found on this console.", INK, 0); return; }
    int visible_w = c->w - M;
    int first = 0;   // scroll so the selected card is fully visible with the next one peeking
    while (M + (a->sel - first) * (w + gap) + w > visible_w - 120 && first < a->sel) first++;
    for (int i = first; i < a->ngames; i++) {
        int x = M + (i - first) * (w + gap);
        if (x > c->w) break;
        draw_card(c, a, &a->games[i], i, x, y, w, h, i == a->sel);
    }
    if (M + (a->ngames - first) * (w + gap) - gap > c->w) {       // more cards to the right
        fill_rect(c, c->w - 70, y + h / 2 - 40, 70, 80, INK);
        draw_button(c, BTN_RIGHT, c->w - 54, y + h / 2, 40, CREAM);
    }
    if (first > 0) { fill_rect(c, 0, y + h / 2 - 40, 50, 80, INK); }
}

// ---- settings panel (game detail, defaults) ----
typedef struct { const char *name; char value[32]; int kind; int level; } Row;   // kind 0 text, 1 pill on/off, 2 blocks
static void draw_rows(Canvas *c, App *a, int x, int y, int w, const char *head, const char *bignum, const char *bigsub,
                      Row *r, int n)
{
    Fonts *f = a->f;
    int hh = 250, rh = 92;
    fill_round(c, x, y, w, hh + rh * n + 34, 22, CREAM);
    fill_round(c, x, y, w, hh + 22, 22, YEL);
    fill_rect(c, x, y + hh, w, 22, CREAM);
    draw_text(c, &f->mono, x + 34, y + 60, head, INK, 1);
    int bw = draw_text(c, &f->big, x + 34, y + hh - 34, bignum, INK, -6);
    draw_text(c, &f->mono, x + 34 + bw + 22, y + hh - 40, bigsub, INK, 1);
    int ry = y + hh;
    for (int i = 0; i < n; i++) {
        int yy = ry + i * rh;
        if (i) fill_rect(c, x + 34, yy, w - 68, 4, INK);
        int sel = i == a->row;
        if (sel) fill_rect(c, x, yy + 4, 12, rh - 4, INK);
        draw_text(c, &f->title, x + 34, yy + 60, r[i].name, INK, 0);
        if (sel) draw_text(c, &f->title, x + 34 + text_width(&f->title, r[i].name, 0) + 14, yy + 60, "<  >", DIM, 0);
        if (r[i].kind == 1) {
            int on = r[i].level, vw = text_width(&f->mono, r[i].value, 1);
            fill_round(c, x + w - 34 - vw - 40, yy + 26, vw + 40, 48, 24, on ? RED : INK);
            draw_text(c, &f->mono, x + w - 34 - vw - 20, yy + 60, r[i].value, on ? INK : CREAM, 1);
        } else if (r[i].kind == 2) {
            for (int k = 0; k < 10; k++) {
                int bx = x + w - 34 - (10 - k) * 30;
                if (k < r[i].level) fill_rect(c, bx, yy + 36, 22, 22, BLUE); else stroke_rect(c, bx, yy + 36, 22, 22, 3, INK);
            }
        } else {
            int vw = text_width(&f->mono, r[i].value, 1);
            draw_text(c, &f->mono, x + w - 34 - vw, yy + 60, r[i].value, INK, 1);
        }
    }
}
static int settings_rows(Settings *s, Row *r)
{
    r[0] = (Row){ "Frame generation", "", 1, s->fg }; cat(r[0].value, s->fg ? "ON" : "OFF");
    r[1] = (Row){ "Mode", "", 0, 0 }; cat(r[1].value, mode_name(s->preset));
    r[2] = (Row){ "Sharpness", "", 2, s->sharp / 10 };
    r[3] = (Row){ "Anti-aliasing", "", 0, 0 }; cat(r[3].value, aa_name(s->enhance));
    r[4] = (Row){ "Performance overlay", "", 0, 0 }; cat(r[4].value, corner_name(s->overlay));
    return 5;
}
static void draw_detail(Canvas *c, App *a, int top, Game *g, Settings *s, const char *head)
{
    Fonts *f = a->f;
    int y = top + 58, left = 520;
    if (g) {
        if (g->icon) blit_scaled(c, g->icon, g->iw, g->ih, M, y, left - 60, left - 60, 18);
        else hatch(c, M, y, left - 60, left - 60, 18, INK, 0xFF232323u, 40);
        char l1[64], l2[64]; wrap2(&f->title, g->title[0] ? g->title : g->id, left - 60, l1, l2);
        draw_text(c, &f->title, M, y + left - 60 + 54, l1, INK, 0);
        if (l2[0]) draw_text(c, &f->title, M, y + left - 60 + 98, l2, INK, 0);
        char lab[48] = ""; cat(lab, g->id); cat(lab, " / "); cat(lab, engine_name(g->engine)); cat(lab, " ENGINE");
        draw_text(c, &f->mono, M, y + left - 60 + (l2[0] ? 146 : 102), lab, DIM, 1);
    }
    Row r[6]; int n = settings_rows(s, r);
    draw_rows(c, a, M + left, y, c->w - 2 * M - left, head, s->fg ? "60" : "30", s->fg ? "FROM 30" : "NATIVE", r, n);
}

// ---- install tab ----
static void draw_install(Canvas *c, App *a, int top)
{
    Fonts *f = a->f;
    int y = top + 58, w = c->w - 2 * M;
    static const char *states[3] = { "NOT INSTALLED", "INSTALLED", "UPDATE AVAILABLE" };
    fill_round(c, M, y, w, 230, 22, a->plugin_state == 1 ? YEL : RED);
    draw_text(c, &f->mono, M + 34, y + 60, "PLUGINS IN /DATA/GOLDHEN/PLUGINS", INK, 1);
    draw_text(c, &f->big, M + 34, y + 190, states[a->plugin_state % 3], INK, -6);
    int ly = y + 300;
    draw_text(c, &f->title, M, ly, "Press cross to install or update. Your plugins.ini is backed up first.", INK, 0);
    draw_text(c, &f->title, M, ly + 52, "Press square to remove VCFG from plugins.ini (files stay, nothing else changes).", INK, 0);
    for (int i = 0; i < a->nlog; i++) draw_text(c, &f->mono, M, ly + 140 + i * 42, a->log[i], DIM, 1);
    if (a->status[0]) draw_text(c, &f->mono, M, c->h - FOOT - 40, a->status, INK, 1);
}

void app_draw(Canvas *c, App *a)
{
    fill_rect(c, 0, 0, c->w, c->h, CREAM);
    int top = draw_header(c, a);
    if (a->tab == TAB_GAMES && !a->detail) {
        draw_games(c, a, top);
        Hint h[] = { { BTN_LR, "BROWSE" }, { BTN_CROSS, "TOGGLE" }, { BTN_TRIANGLE, "SETTINGS" }, { BTN_SQUARE, "SAVE AND EXIT" } };
        draw_footer(c, a, h, 4);
    } else if (a->tab == TAB_GAMES) {
        Game *g = &a->games[a->sel];
        char head[64] = "SETTINGS / "; cat(head, g->id);
        draw_detail(c, a, top, g, &g->s, head);
        Hint h[] = { { BTN_UD, "SELECT" }, { BTN_LR, "CHANGE" }, { BTN_CIRCLE, "BACK" } };
        draw_footer(c, a, h, 3);
    } else if (a->tab == TAB_DEFAULTS) {
        draw_detail(c, a, top, 0, &a->defaults, "DEFAULTS FOR EVERY GAME");
        Hint h[] = { { BTN_UD, "SELECT" }, { BTN_LR, "CHANGE" }, { BTN_CROSS, "APPLY TO ALL" } };
        draw_footer(c, a, h, 3);
    } else if (a->tab == TAB_OVERLAY) {
        Settings s = a->defaults;
        Row r[3];
        r[0] = (Row){ "Position", "", 0, 0 }; cat(r[0].value, corner_name(a->overlay_pos ? a->overlay_pos : 0));
        r[1] = (Row){ "Opacity", "", 2, a->overlay_opacity / 10 };
        r[2] = (Row){ "Shown in", "", 0, 0 }; cat(r[2].value, "ALL GAMES");
        (void)s;
        draw_rows(c, a, M + 520, top + 58, c->w - 2 * M - 520, "PERFORMANCE OVERLAY", "60", "GAME 30 / GPU 1.7 MS", r, 3);
        draw_text(c, a->f->mono.ttf ? &a->f->mono : &a->f->mono, M, top + 120, "SHOWS GAME FPS,", INK, 1);
        draw_text(c, &a->f->mono, M, top + 162, "FRAMES ON SCREEN", INK, 1);
        draw_text(c, &a->f->mono, M, top + 204, "AND GPU TIME", INK, 1);
        Hint h[] = { { BTN_UD, "SELECT" }, { BTN_LR, "CHANGE" } };
        draw_footer(c, a, h, 2);
    } else {
        draw_install(c, a, top);
        Hint h[] = { { BTN_CROSS, "INSTALL" }, { BTN_SQUARE, "REMOVE" } };
        draw_footer(c, a, h, 2);
    }
}

// ---- input ----
static void step(int *v, int dir, int lo, int hi, int wrap) { *v += dir; if (*v < lo) *v = wrap ? hi : lo; if (*v > hi) *v = wrap ? lo : hi; }
static void change(Settings *s, int row, int dir)
{
    switch (row) {
    case 0: s->fg = !s->fg; break;
    case 1: s->preset = !s->preset; break;
    case 2: step(&s->sharp, dir * 10, 0, 100, 0); break;
    case 3: step(&s->enhance, dir, 0, 2, 1); break;
    case 4: step(&s->overlay, dir, 0, 8, 1); break;
    }
}
int app_input(App *a, uint32_t b)
{
    if (b & PAD_L1) { step(&a->tab, -1, 0, TAB_COUNT - 1, 1); a->detail = 0; a->row = 0; return ACT_NONE; }
    if (b & PAD_R1) { step(&a->tab, 1, 0, TAB_COUNT - 1, 1); a->detail = 0; a->row = 0; return ACT_NONE; }
    if (b & PAD_OPTIONS) return ACT_SAVE_EXIT;
    if (a->tab == TAB_GAMES && !a->detail) {
        if (b & PAD_LEFT && a->sel > 0) a->sel--;
        if (b & PAD_RIGHT && a->sel + 1 < a->ngames) a->sel++;
        if (b & PAD_CROSS && a->ngames && a->games[a->sel].installed) { a->games[a->sel].s.fg = !a->games[a->sel].s.fg; a->dirty = 1; }
        if (b & PAD_TRIANGLE && a->ngames) { a->detail = 1; a->row = 0; }
        if (b & PAD_SQUARE) return ACT_SAVE_EXIT;
        return ACT_NONE;
    }
    Settings *s = a->tab == TAB_GAMES ? &a->games[a->sel].s : &a->defaults;
    int rows = a->tab == TAB_OVERLAY ? 3 : 5;
    if (a->tab == TAB_INSTALL) {
        if (b & PAD_CROSS) return ACT_INSTALL;
        if (b & PAD_SQUARE) return ACT_REMOVE;
        return ACT_NONE;
    }
    if (b & PAD_UP) step(&a->row, -1, 0, rows - 1, 1);
    if (b & PAD_DOWN) step(&a->row, 1, 0, rows - 1, 1);
    int dir = (b & PAD_RIGHT) ? 1 : (b & PAD_LEFT) ? -1 : 0;
    if (dir) {
        if (a->tab == TAB_OVERLAY) {
            if (a->row == 0) step(&a->overlay_pos, dir, 1, 8, 1);
            else if (a->row == 1) step(&a->overlay_opacity, dir * 10, 20, 100, 0);
        } else change(s, a->row, dir);
        a->dirty = 1;
    }
    if (b & PAD_CIRCLE) { a->detail = 0; return ACT_SAVE; }
    if (a->tab == TAB_DEFAULTS && (b & PAD_CROSS)) {
        for (int i = 0; i < a->ngames; i++) if (!a->games[i].testing) a->games[i].s = a->defaults;
        a->dirty = 1;
    }
    return ACT_NONE;
}
