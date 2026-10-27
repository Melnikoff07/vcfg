#include "app.h"
#include "../../overlay/menu_combo.h"

// Palette (approved design: variant 2 colours, horizontal card row + dark theme).
#define CREAM 0xFFEFEEE8u
#define INK   0xFF0B0B0Bu
#define RED   0xFFF2502Eu
#define YEL   0xFFF5D23Au
#define BLUE  0xFF4F86E6u
#define DIM   0xFF6E6C66u

#define M 77            // outer margin
#define FOOT 72         // footer height
static const uint32_t k_card[4] = { YEL, BLUE, RED, CREAM };

typedef struct {
    uint32_t bg, panel, card_off, ink, dim, div, tab_bg, tab_fg, foot_bg, foot_fg, head_bg;
} Theme;

static Theme theme_of(const App *a)
{
    if (a->dark_theme) {
        return (Theme){
            0xFF0E1014u, // bg
            0xFF171A22u, // panel
            0xFF13161Du, // card_off
            0xFFF0EFEAu, // ink
            0xFF8C93A4u, // dim
            0xFF282D3Au, // div
            YEL,         // tab_bg
            INK,         // tab_fg
            0xFF181C24u, // foot_bg
            0xFFF0EFEAu, // foot_fg
            0xFF202532u  // head_bg
        };
    }
    return (Theme){
        CREAM, CREAM, CREAM, INK, DIM, INK, INK, CREAM, INK, CREAM, YEL
    };
}

static void itoa_(int v, char *o) { char t[12]; int n = 0, neg = v < 0; if (neg) v = -v; do { t[n++] = (char)('0' + v % 10); v /= 10; } while (v); int k = 0; if (neg) o[k++] = '-'; while (n) o[k++] = t[--n]; o[k] = 0; }
static void cat(char *d, const char *s) { while (*d) d++; while (*s) *d++ = *s++; *d = 0; }

static const char *mode_name(int p) { return p ? "FAST" : "QUALITY"; }
static const char *aa_name(int e) { return e == 2 ? "STRONG" : e == 1 ? "LIGHT" : "OFF"; }
static const char *engine_name(int e) { return e == ENGINE_NEW ? "NEW" : e == ENGINE_CLASSIC_ALT ? "ALT" : "CLASSIC"; }
static const char *buf_name(int b) { return b == BUF_2BUF ? "2-BUFFER" : "STANDARD"; }
static void pipe_preset_label(const Game *g, char *out)
{
    out[0] = 0;
    int p = g->dev.manual_mode ? game_pipe_preset(g) : 0;
    if (p == 0) {
        cat(out, "AUTO: ");
        cat(out, engine_name(g->engine));
        cat(out, g->def_buf == BUF_2BUF ? " / 2-BUF" : " / 3-BUF");
    } else if (p == 1) cat(out, "CLASSIC / 3-BUF");
    else if (p == 2) cat(out, "CLASSIC / 2-BUF (PCOPY)");
    else if (p == 3) cat(out, "ALT (LOW VRAM) / 3-BUF");
    else if (p == 4) cat(out, "ALT (LOW VRAM) / 2-BUF");
    else cat(out, "NEW / 3-BUF");
}
static const char *corner_name(int ov)
{
    static const char *n[9] = { "OFF", "SMALL TL", "DETAIL TL", "SMALL TR", "DETAIL TR", "SMALL BR", "DETAIL BR", "SMALL BL", "DETAIL BL" };
    return (ov >= 0 && ov <= 8) ? n[ov] : "OFF";
}
static const char *shave_name(int v)
{
    static const char *n[3] = { "AUTO: VALVE+SHAVE", "VALVE ONLY", "LATE (FRAME 120)" };
    return (v >= 0 && v <= 2) ? n[v] : n[0];
}
static const char *reg_name(int v)
{
    static const char *n[3] = { "AUTO: EXTEND+RELOC", "TOP GROUP (16-N)", "DEFERRED (F120)" };
    return (v >= 0 && v <= 2) ? n[v] : n[0];
}
static const char *pace_name(int v)
{
    static const char *n[4] = { "AUTO: PRE ON STD", "PRE-SUBMIT", "POST-SUBMIT", "TIMER (PAIR)" };
    return (v >= 0 && v <= 3) ? n[v] : n[0];
}
static const char *post_name(int v)
{
    static const char *n[4] = { "AUTO", "MID ONLY", "MID + REAL", "MID + REAL AA" };
    return (v >= 0 && v <= 3) ? n[v] : n[0];
}
static const char *fps_lock_name(int v)
{
    static const char *n[4] = { "30 FPS", "20 FPS", "60 FPS", "UNLOCKED" };
    return (v >= 0 && v <= 3) ? n[v] : n[0];
}
static const char *tile_mode_name(int v)
{
    static const char *n[7] = { "AUTO", "IDX 10 (STD 2D)", "IDX 14 (NEO A)", "IDX 13 (NEO B)", "IDX 9", "IDX 12", "IDX 11" };
    return (v >= 0 && v <= 6) ? n[v] : n[0];
}

// ---- header: logo plate, subtitle, tabs ----
static int draw_header(Canvas *c, App *a)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    int top = 58, cap = (int)(f->logo.px * 0.727f);
    int lw = text_width(&f->logo, "VCFG", -6);
    int ph = cap + 17 + 12;
    fill_rect(c, M, top, lw + 56, ph, RED);
    draw_text(c, &f->logo, M + 28, top + 17 + cap, "VCFG", INK, -6);
    int bottom = top + ph;
    if (a->version[0]) { char v[24] = "V"; cat(v, a->version); draw_text(c, &f->mono, M + lw + 56 + 34, bottom - 82, v, th.dim, 1); }
    draw_text(c, &f->mono, M + lw + 56 + 34, bottom - 46, "VIBE CODED", th.ink, 1);
    draw_text(c, &f->mono, M + lw + 56 + 34, bottom - 10, "FRAME GENERATOR", th.ink, 1);
    static const char *tabs[TAB_COUNT] = { "01 GAMES", "02 DEFAULTS", "03 OVERLAY", "04 PLUGIN", "05 DEV" };
    int x = c->w - M;
    for (int i = TAB_COUNT - 1; i >= 0; i--) {
        int w = text_width(&f->mono, tabs[i], 1);
        x -= w;
        if (i == a->tab) { fill_rect(c, x - 14, bottom - 40, w + 28, 50, th.tab_bg); draw_text(c, &f->mono, x, bottom - 6, tabs[i], th.tab_fg, 1); }
        else draw_text(c, &f->mono, x, bottom - 6, tabs[i], th.ink, 1);
        x -= 44;
    }
    if (a->turned_off) {
        int by = bottom + 8, bh = 40, bw = c->w - 2 * M;
        fill_round(c, M, by, bw, bh, 10, RED);
        draw_button(c, BTN_CROSS, M + 26, by + bh / 2, 22, YEL);
        draw_text(c, &f->mono, M + 52, by + 28, "VCFG IS TURNED OFF - PRESS CROSS ON 04 PLUGIN TO ENABLE", CREAM, 1);
    }
    return bottom;
}

// ---- footer: pixel strip + black hint bar ----
typedef struct { int btn; const char *label; } Hint;
static void draw_footer(Canvas *c, App *a, const Hint *h, int n)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    int y = c->h - FOOT, bw = 60;
    for (int i = 0; i < n; i++) bw += 44 + text_width(&f->mono, h[i].label, 1) + 40;
    int bx = c->w - bw;
    fill_rect(c, bx, y, bw, FOOT, th.foot_bg);
    int x = bx + 40;
    for (int i = 0; i < n; i++) {
        if (h[i].btn == BTN_R2) {
            fill_round(c, x - 4, y + FOOT / 2 - 16, 40, 32, 8, YEL);
            draw_text(c, &f->mono, x + 2, y + FOOT / 2 + 9, "R2", INK, 0);
        } else if (h[i].btn == BTN_L2) {
            fill_round(c, x - 4, y + FOOT / 2 - 16, 40, 32, 8, YEL);
            draw_text(c, &f->mono, x + 2, y + FOOT / 2 + 9, "L2", INK, 0);
        } else {
            draw_button(c, h[i].btn, x, y + FOOT / 2, 30, YEL);
        }
        x += 44;
        x += draw_text(c, &f->mono, x, y + FOOT / 2 + 10, h[i].label, th.foot_fg, 1) + 40;
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

static int draw_text_clip(Canvas *c, Face *f, int x, int base, int max_w, const char *s, uint32_t col, int tracking)
{
    if (!s || !*s || max_w <= 0) return 0;
    if (text_width(f, s, tracking) <= max_w) return draw_text(c, f, x, base, s, col, tracking);
    char buf[160];
    int k = 0;
    for (; s[k] && k < (int)sizeof(buf) - 4; k++) buf[k] = s[k];
    buf[k] = 0;
    int dots_w = text_width(f, "..", tracking);
    while (k > 0 && text_width(f, buf, tracking) + dots_w > max_w) buf[--k] = 0;
    cat(buf, "..");
    return draw_text(c, f, x, base, buf, col, tracking);
}

static int draw_wrapped(Canvas *c, Face *f, int x, int y0, int line_h, int max_w, int max_lines,
                        const char *s, uint32_t col, int tracking)
{
    if (!s || !*s || max_w <= 0 || max_lines <= 0) return 0;
    int line = 0;
    while (*s && line < max_lines) {
        while (*s == ' ') s++;
        if (!*s) break;
        if (line + 1 == max_lines) {
            draw_text_clip(c, f, x, y0 + line * line_h, max_w, s, col, tracking);
            line++;
            break;
        }
        char buf[160];
        int i = 0, last_space = -1;
        for (; s[i] && i < (int)sizeof(buf) - 2; i++) {
            buf[i] = s[i];
            buf[i + 1] = 0;
            if (s[i] == ' ') last_space = i;
            if (text_width(f, buf, tracking) > max_w) {
                if (last_space > 0) {
                    buf[last_space] = 0;
                    s += last_space + 1;
                } else {
                    buf[i] = 0;
                    s += i;
                }
                goto draw_it;
            }
        }
        s += i;
    draw_it:
        draw_text(c, f, x, y0 + line * line_h, buf, col, tracking);
        line++;
    }
    return line;
}

// ---- games: horizontal card row ----
static void draw_card(Canvas *c, App *a, Game *g, int idx, int x, int y, int w, int h, int sel)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    uint32_t accent = k_card[idx % 3];
    int fg_on = !a->turned_off && g->installed && g->s.fg;
    int active_fg = !a->turned_off && g->installed && !g->testing && (!g->experimental || g->s.fg);
    uint32_t bg, fg_ink, sub_ink, bar_col, num_col;
    if (a->dark_theme) {
        bg = active_fg ? (sel ? 0xFF1F2430u : th.panel) : th.card_off;
        fg_ink = th.ink;
        sub_ink = active_fg ? accent : th.dim;
        bar_col = fg_on ? accent : th.div;
        num_col = fg_on ? accent : RED;
        if (sel) { fill_round(c, x, y, w, h, 22, active_fg ? accent : YEL); fill_round(c, x + 7, y + 7, w - 14, h - 14, 16, bg); }
        else fill_round(c, x, y, w, h, 22, bg);
        if (!sel && (a->turned_off || g->testing || !g->installed || g->experimental)) dashed_rect(c, x, y, w, h, 4, 14, th.div);
    } else {
        bg = (a->turned_off || g->testing) ? CREAM : accent;
        if (!g->installed || (g->experimental && !g->s.fg)) bg = CREAM;
        fg_ink = INK;
        sub_ink = INK;
        bar_col = INK;
        num_col = fg_on ? INK : RED;
        if (sel) { fill_round(c, x, y, w, h, 22, INK); fill_round(c, x + 8, y + 8, w - 16, h - 16, 15, bg); }
        else fill_round(c, x, y, w, h, 22, bg);
        if (!sel && (a->turned_off || g->testing || !g->installed || g->experimental)) dashed_rect(c, x, y, w, h, 4, 14, INK);
    }
    int p = 22, isz = w - 2 * p;
    if (g->icon) blit_scaled(c, g->icon, g->iw, g->ih, x + p, y + p, isz, isz, 11);
    else hatch(c, x + p, y + p, isz, isz, 11, INK, 0xFF232323u, 34);
    char l1[64], l2[64]; wrap2(&f->title, g->title[0] ? g->title : g->id, isz, l1, l2);
    int ty = y + p + isz + 50;
    draw_text(c, &f->title, x + p, ty, l1, fg_ink, 0);
    if (l2[0]) draw_text(c, &f->title, x + p, ty + 44, l2, fg_ink, 0);
    if (g->installed) {
        char eng[40] = "";
        if (g->dev.manual_mode) {
            cat(eng, "PIPELINE: DEV CUSTOM");
            draw_text(c, &f->mono, x + p, ty + (l2[0] ? 92 : 48), eng, a->dark_theme ? YEL : RED, 1);
        } else {
            cat(eng, "AUTO: ");
            cat(eng, engine_name(game_engine(g)));
            cat(eng, game_buf(g) == BUF_2BUF ? " / 2-BUF" : " / 3-BUF");
            draw_text(c, &f->mono, x + p, ty + (l2[0] ? 92 : 48), eng, sub_ink, 1);
        }
    }
    int ry = y + h - 98;
    if (g->dev.has_crash || g->dev.safe_state > 0 || g->dev.diag_kind >= DIAG_OOM_BOOT) {
        draw_text(c, &f->mono, x + p, ry - 14, "! CRASH: SEE 05 DEV", RED, 1);
    }
    fill_rect(c, x + p, ry, isz, 4, bar_col);
    char lab[48] = "";
    if (!g->installed) cat(lab, "NOT INSTALLED");
    else if (a->turned_off) cat(lab, "VCFG TURNED OFF");
    else if (g->testing) cat(lab, "UNSTABLE, OFF");
    else if (g->experimental) cat(lab, g->s.fg ? "EXPERIMENTAL" : "EXPERIMENTAL, OFF");
    else if (!g->s.fg) cat(lab, "FG OFF");
    else cat(lab, mode_name(g->s.preset));
    draw_text(c, &f->mono, x + p, y + h - 30, lab, fg_ink, 1);
    const char *num = fg_on ? "60" : "30";
    int nw = text_width(&f->num, num, -3);
    draw_text(c, &f->num, x + w - p - nw, y + h - 26, num, num_col, -3);
}
static void draw_games(Canvas *c, App *a, int top)
{
    Theme th = theme_of(a);
    int y = top + 58, h = c->h - FOOT - 58 - y, w = 403, gap = 46;
    if (!a->ngames) { draw_text(c, &a->f->title, M, y + 60, "No supported games found on this console.", th.ink, 0); return; }
    int visible_w = c->w - M;
    int first = 0;   // scroll so the selected card is fully visible with the next one peeking
    while (M + (a->sel - first) * (w + gap) + w > visible_w - 120 && first < a->sel) first++;
    for (int i = first; i < a->ngames; i++) {
        int x = M + (i - first) * (w + gap);
        if (x > c->w) break;
        draw_card(c, a, &a->games[i], i, x, y, w, h, i == a->sel);
    }
    if (M + (a->ngames - first) * (w + gap) - gap > c->w) {       // more cards to the right
        fill_rect(c, c->w - 70, y + h / 2 - 40, 70, 80, th.foot_bg);
        draw_button(c, BTN_RIGHT, c->w - 54, y + h / 2, 40, YEL);
    }
    if (first > 0) { fill_rect(c, 0, y + h / 2 - 40, 50, 80, th.foot_bg); }
}

// ---- settings panel (game detail, defaults) ----
typedef struct { const char *name; char value[36]; int kind; int level; } Row;   // kind 0 text, 1 pill on/off, 2 blocks, 3 recommended fix, 4 auto-dimmed
static void draw_rows(Canvas *c, App *a, int x, int y, int w, const char *head, const char *bignum, const char *bigsub,
                      Row *r, int n)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    int hh = n > 7 ? 214 : (n > 6 ? 236 : 250);
    int rh = n > 8 ? 56  : (n > 7 ? 64  : (n > 6 ? 70  : (n > 5 ? 80 : 92)));
    int ty_off = rh <= 56 ? 41 : (rh <= 64 ? 45 : (rh <= 72 ? 50 : 60));
    int pill_y = rh <= 56 ? 7 : (rh <= 64 ? 11 : (rh <= 72 ? 15 : 26));
    int pill_h = rh <= 64 ? 42 : 48;
    int pill_ty = rh <= 64 ? 30 : 34;
    int blk_y  = rh <= 56 ? 17 : (rh <= 64 ? 21 : (rh <= 72 ? 26 : 36));
    fill_round(c, x, y, w, hh + rh * n + 30, 22, th.panel);
    fill_round(c, x, y, w, hh + 22, 22, th.head_bg);
    fill_rect(c, x, y + hh, w, 22, th.panel);
    if (a->dark_theme) fill_rect(c, x, y + hh - 4, w, 4, YEL);
    draw_text(c, &f->mono, x + 34, y + 60, head, a->dark_theme ? YEL : INK, 1);
    int bw = draw_text(c, &f->big, x + 34, y + hh - 34, bignum, th.ink, -6);
    draw_text(c, &f->mono, x + 34 + bw + 22, y + hh - 40, bigsub, a->dark_theme ? th.dim : INK, 1);
    int ry = y + hh;
    for (int i = 0; i < n; i++) {
        int yy = ry + i * rh;
        if (i) fill_rect(c, x + 34, yy, w - 68, 4, th.div);
        int sel = i == a->row;
        if (sel) fill_rect(c, x, yy + 4, 12, rh - 4, a->dark_theme ? YEL : INK);
        uint32_t ncol = r[i].kind == 3 ? (a->dark_theme ? YEL : RED) : (r[i].kind == 4 ? th.dim : th.ink);
        draw_text(c, &f->title, x + 34, yy + ty_off, r[i].name, ncol, 0);
        if (sel) draw_text(c, &f->title, x + 34 + text_width(&f->title, r[i].name, 0) + 14, yy + ty_off, "<  >", th.dim, 0);
        if (r[i].kind == 1) {
            int on = r[i].level, vw = text_width(&f->mono, r[i].value, 1);
            fill_round(c, x + w - 34 - vw - 40, yy + pill_y, vw + 40, pill_h, pill_h / 2, on ? RED : (a->dark_theme ? 0xFF282D3Au : INK));
            draw_text(c, &f->mono, x + w - 34 - vw - 20, yy + pill_y + pill_ty, r[i].value, on ? INK : CREAM, 1);
        } else if (r[i].kind == 2) {
            for (int k = 0; k < 10; k++) {
                int bx = x + w - 34 - (10 - k) * 30;
                if (k < r[i].level) fill_rect(c, bx, yy + blk_y, 22, 22, BLUE); else stroke_rect(c, bx, yy + blk_y, 22, 22, 3, th.div);
            }
        } else {
            int vw = text_width(&f->mono, r[i].value, 1);
            uint32_t vcol = r[i].kind == 3 ? (a->dark_theme ? YEL : RED) : (r[i].kind == 4 ? th.dim : th.ink);
            draw_text(c, &f->mono, x + w - 34 - vw, yy + ty_off, r[i].value, vcol, 1);
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
    r[5] = (Row){ "Below 30 FPS", "", 1, s->b30 }; cat(r[5].value, s->b30 ? "ON" : "OFF");
    r[6] = (Row){ "Low latency", "", 1, s->shq }; cat(r[6].value, s->shq ? "ON" : "OFF");
    return 7;
}
// one-line help for the selected row of the settings panel (game detail and defaults)
static const char *settings_row_guide(int row, int game)
{
    switch (row) {
    case 0: return "FRAME GENERATION: a generated frame between game frames, 30 FPS shown at 60 Hz.";
    case 1: return "MODE: QUALITY for the best image; FAST is a fixed 60 Hz pulldown.";
    case 2: return "SHARPNESS: sharpens the generated frame. 0 = off.";
    case 3: return "ANTI-ALIASING: smooths jagged edges. OFF, LIGHT or STRONG.";
    case 4: return "PERFORMANCE OVERLAY: FPS counter and its corner.";
    case 5: return "BELOW 30 FPS: two generated frames when the game drops under 30 FPS (needs spare memory).";
    case 6: return "LOW LATENCY: a shorter frame queue while the game has spare time.";
    default: return game ? "PIPELINE CONTROL: AUTO, or your CUSTOM 05 DEV profile (Triangle opens it)." : "THEME: dark or light app theme.";
    }
}
static void draw_detail(Canvas *c, App *a, int top, Game *g, Settings *s, const char *head)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    int y = top + 58, left = 520;
    if (g) {
        if (g->icon) blit_scaled(c, g->icon, g->iw, g->ih, M, y, left - 60, left - 60, 18);
        else hatch(c, M, y, left - 60, left - 60, 18, INK, 0xFF232323u, 40);
        char l1[64], l2[64]; wrap2(&f->title, g->title[0] ? g->title : g->id, left - 60, l1, l2);
        draw_text(c, &f->title, M, y + left - 60 + 54, l1, th.ink, 0);
        if (l2[0]) draw_text(c, &f->title, M, y + left - 60 + 98, l2, th.ink, 0);
        int ty = y + left - 60 + (l2[0] ? 146 : 102);
        char lab[64] = ""; cat(lab, g->id); cat(lab, " / ");
        if (g->dev.manual_mode) cat(lab, "CUSTOM DEV PROFILE");
        else {
            cat(lab, "AUTO: "); cat(lab, engine_name(game_engine(g)));
            cat(lab, game_buf(g) == BUF_2BUF ? " / 2-BUF" : " / 3-BUF");
        }
        draw_text_clip(c, &f->mono, M, ty, left - 60, lab, g->dev.manual_mode ? (a->dark_theme ? YEL : RED) : th.dim, 0);
        if (g->dev.has_crash || g->dev.safe_state > 0 || g->dev.diag_kind >= DIAG_OOM_BOOT) {
            draw_text_clip(c, &f->mono, M, ty + 38, left - 60, "! CRASH DETECTED: PRESS TRIANGLE", RED, 0);
            if (g->dev.diag_advice[0]) draw_text_clip(c, &f->mono, M, ty + 72, left - 60, g->dev.diag_advice, a->dark_theme ? YEL : RED, 0);
        }
    }
    Row r[10]; int n = settings_rows(s, r);
    if (g) {   // clear separation: 01 GAMES has visual settings + master Pipeline Control indicator (TRIANGLE opens 05 DEV)
        r[n] = (Row){ "Pipeline control", "", 1, g->dev.manual_mode };
        cat(r[n].value, g->dev.manual_mode ? "CUSTOM (05 DEV)" : "AUTO (ALGORITHM)");
        n++;
    } else {
        r[n] = (Row){ "Theme", "", 1, a->dark_theme };
        cat(r[n].value, a->dark_theme ? "DARK" : "LIGHT");
        n++;
    }
    draw_rows(c, a, M + left, y, c->w - 2 * M - left, head, s->fg ? "60" : "30", s->fg ? "FROM 30" : "NATIVE", r, n);
    int crash = g && (g->dev.has_crash || g->dev.safe_state > 0 || g->dev.diag_kind >= DIAG_OOM_BOOT);   // its lines use that space
    if (!crash) draw_wrapped(c, &f->mono, M, c->h - FOOT - 156, 34, left - 60, 4, settings_row_guide(a->row, g != 0), th.dim, 0);
}

// ---- install tab ----
static void draw_install(Canvas *c, App *a, int top)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    int y = top + 58, w = c->w - 2 * M, gap = 24;
    static const char *states[3] = { "NOT INSTALLED", "UP TO DATE", "NEEDS UPDATE" };
    int dw = 390, tw = 450, pw = w - dw - tw - 2 * gap;
    fill_round(c, M, y, pw, 230, 22, (!a->turned_off && a->plugin_state == 1) ? YEL : RED);
    draw_text(c, &f->mono, M + 30, y + 56, "PLUGIN ON THIS CONSOLE", INK, 1);
    const char *st_txt = a->turned_off ? "TURNED OFF" : states[a->plugin_state % 3];
    Face *st_face = text_width(&f->big, st_txt, -6) <= pw - 50 ? &f->big : &f->logo;
    draw_text_clip(c, st_face, M + 30, y + 190, pw - 50, st_txt, INK, -6);

    int dx = M + pw + gap;   // debug log switch
    if (a->debug_log) fill_round(c, dx, y, dw, 230, 22, BLUE);
    else { fill_round(c, dx, y, dw, 230, 22, th.panel); dashed_rect(c, dx, y, dw, 230, 4, 14, th.div); }
    draw_text(c, &f->mono, dx + 30, y + 56, "DEBUG LOG [TRIANGLE]", a->debug_log ? INK : th.ink, 1);
    draw_text(c, &f->big, dx + 30, y + 190, a->debug_log ? "ON" : "OFF", a->debug_log ? INK : th.ink, -6);

    int nx = dx + dw + gap;  // network crash auto-send switch
    int net_on = a->net_telemetry;
    if (net_on) fill_round(c, nx, y, tw, 230, 22, BLUE);
    else { fill_round(c, nx, y, tw, 230, 22, th.panel); dashed_rect(c, nx, y, tw, 230, 4, 14, th.div); }
    draw_text(c, &f->mono, nx + 30, y + 56, "NET CRASH SEND [L2]", net_on ? INK : th.ink, 1);
    draw_text(c, &f->big, nx + 30, y + 172, net_on ? "ON" : "OFF", net_on ? INK : th.ink, -6);
    char ep[64] = "VPS: ";
    const char *thost = a->telemetry_host[0] ? a->telemetry_host : "62.60.230.108";
    cat(ep, thost);
    char pb[16]; itoa_(a->telemetry_port > 0 ? a->telemetry_port : 8765, pb);
    cat(ep, ":"); cat(ep, pb);
    draw_text_clip(c, &f->mono, nx + 30, y + 212, tw - 50, ep, net_on ? INK : th.dim, 0);

    int ly = y + 292;
    draw_text(c, &f->title, M, ly, "The app installs and updates the plugin automatically on startup. Cross repeats that if needed.", th.ink, 0);
    draw_text(c, &f->title, M, ly + 50, "Triangle: toggle full boot/API debug log. L2: toggle automatic network crash report upload to VPS.", th.ink, 0);
    draw_text(c, &f->title, M, ly + 100, "Circle: save /data/vcfg_report.txt and upload it to VPS (shows a 6-character #ID on screen).", th.ink, 0);
    draw_text(c, &f->title, M, ly + 150, "R2 twice / Square: uninstall VCFG (plugins, settings; logs kept) from this console.", th.ink, 0);
    for (int i = 0; i < a->nlog; i++) draw_text(c, &f->mono, M, ly + 212 + i * 40, a->log[i], th.dim, 1);
    if (a->status[0]) draw_text(c, &f->mono, M, c->h - FOOT - 40, a->status, a->dark_theme ? YEL : INK, 1);
}

// ---- developer compatibility tab & expandable crash advisor ----
static const char *dev_row_guide_full(int row, const char **short1, const char **short2)
{
    switch (row) {
    case 0:
        if (short1) *short1 = "AUTO = built-in plugin algorithm decides everything.";
        if (short2) *short2 = "CUSTOM = your manual 05 DEV choices override the algorithm.";
        return "CONTROL MODE: AUTO lets VCFG choose all hooks. CUSTOM forces your manual 05 DEV settings.";
    case 1:
        if (short1) *short1 = "Selects plugin binary (CLASSIC / ALT / NEW) & buffers.";
        if (short2) *short2 = "2-BUF fixes 2-buffer games; ALT saves 64MB DirectMem.";
        return "ENGINE & BUFFERS: Chooses plugin (CLASSIC, ALT low-VRAM, NEW) and buffer ring (3-BUF vs 2-BUF).";
    case 2:
        if (short1) *short1 = "Controls how VCFG reserves GPU DirectMemory at startup.";
        if (short2) *short2 = "Boot crash / OOM -> use VALVE ONLY or LATE (F120).";
        return "MEMORY HOOK: GPU VRAM reservation. Boot crash or OOM -> use VALVE ONLY or LATE (F120).";
    case 3:
        if (short1) *short1 = "How VCFG registers extra display buffers in VideoOut.";
        if (short2) *short2 = "Black screen / index conflict -> TOP GROUP or DEFERRED.";
        return "BUFFER REG: VideoOut slot layout. Black screen or index conflict -> TOP GROUP or DEFERRED.";
    case 4:
        if (short1) *short1 = "Controls VBlank frame delivery and CPU/GPU pacing.";
        if (short2) *short2 = "Stutter, judder or hang -> try POST-SUBMIT or TIMER.";
        return "FLIP PACING: VBlank frame timing. Stutter, judder or flip hang -> POST-SUBMIT or TIMER.";
    case 5:
        if (short1) *short1 = "Selects whether RCAS & OSD run on mid or real frames.";
        if (short2) *short2 = "Flickering HUD or missing OSD -> MID ONLY / MID+REAL.";
        return "POST TARGET: RCAS & OSD target. Flickering HUD or hidden OSD -> MID ONLY or MID + REAL.";
    case 6:
        if (short1) *short1 = "Internal frame rate cap before Frame Generation.";
        if (short2) *short2 = "30 FPS = smooth 60 FG. 20 FPS = heavy Pro/4K modes.";
        return "FPS LOCK: Caps game FPS. 30 FPS gives steady 60 Hz; 20 FPS helps heavy scenes.";
    default:
        if (short1) *short1 = "Overrides GPU macro-tile swizzle index for scanout.";
        if (short2) *short2 = "Garbled/scrambled image -> try IDX 10 or IDX 14/13.";
        return "TILE MODE: GPU macro-tile swizzle. Scrambled or striped image -> try IDX 10 or IDX 14/13.";
    }
}

static void format_recipe(const DevCfg *d, char *rc_line)
{
    static const char *lk_s[4] = { "30", "20", "60", "A" };
    static const char *ti_s[7] = { "A", "10", "14", "13", "9", "12", "11" };
    char nb[12];
    rc_line[0] = 0;
    cat(rc_line, "M"); itoa_(d->shave_mode, nb); cat(rc_line, nb);
    cat(rc_line, "-R"); itoa_(d->reg_mode, nb); cat(rc_line, nb);
    cat(rc_line, "-P"); itoa_(d->pace_order, nb); cat(rc_line, nb);
    cat(rc_line, "-T"); itoa_(d->post_mid, nb); cat(rc_line, nb);
    cat(rc_line, "-F"); cat(rc_line, lk_s[d->fps_lock >= 0 && d->fps_lock < 4 ? d->fps_lock : 0]);
    cat(rc_line, "-TL"); cat(rc_line, ti_s[d->tile_mode >= 0 && d->tile_mode < 7 ? d->tile_mode : 0]);
}

static int row_needs_fix(const DevCfg *d, int i)
{
    if (d->rec_applied) return 0;
    if (d->rec_mask) return (d->rec_mask & (1 << i)) != 0;
    return d->rec_row == i;
}

static void draw_dev_expanded(Canvas *c, App *a, int top, Game *g, DevCfg *d)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    int y = top + 58, w = c->w - 2 * M, h = 756;
    uint32_t sub_box = a->dark_theme ? 0xFF11141Bu : 0xFFE4E2DAu;
    int has_issue = !d->rec_applied && (d->diag_kind >= DIAG_OOM_BOOT || d->has_crash || d->safe_state > 0 || d->rec_mask != 0);
    uint32_t adv_col = has_issue ? RED : ((d->rec_applied || d->diag_kind == DIAG_OK) ? BLUE : th.div);

    fill_round(c, M, y, w, h, 22, th.panel);
    fill_round(c, M, y, w, 72, 22, th.head_bg);
    fill_rect(c, M, y + 52, w, 20, th.head_bg);
    fill_rect(c, M, y + 70, w, 4, adv_col);

    static const char *st_names[3] = { "EARLY", "DEFERRED", "PASSIVE" };
    char rinfo[96] = "BOOT: ";
    cat(rinfo, st_names[d->safe_state >= 0 && d->safe_state <= 2 ? d->safe_state : 0]);
    cat(rinfo, " | ");
    char rc_short[40]; format_recipe(d, rc_short); cat(rinfo, rc_short);
    int rw = text_width(&f->mono, rinfo, 0);
    draw_text(c, &f->mono, M + w - 28 - rw, y + 46, rinfo, a->dark_theme ? YEL : INK, 0);

    char hdr[128] = "ADVISOR / ";
    cat(hdr, g->id);
    if (g->title[0]) { cat(hdr, " — "); cat(hdr, g->title); }
    draw_text_clip(c, &f->title, M + 28, y + 48, w - rw - 84, hdr, a->dark_theme ? th.ink : INK, 0);

    // ---- Left Column: Full Diagnosis, 3-Step Plan & Log Tail ----
    int lx = M + 26, lw = 820, iw = lw - 32;

    // 1. Diagnosis Box
    int y1 = y + 90, h1 = 204;
    fill_round(c, lx, y1, lw, h1, 16, sub_box);
    stroke_rect(c, lx, y1, lw, h1, 2, adv_col);
    char c_line[96] = "CAUSE: ";
    cat(c_line, d->diag_cause[0] ? d->diag_cause : "NO CRASH RECORDED");
    draw_text_clip(c, &f->mono, lx + 16, y1 + 34, iw, c_line, has_issue ? RED : BLUE, 0);

    char s_line[96] = "STAGE: ";
    cat(s_line, d->diag_stage[0] ? d->diag_stage : "READY FOR TEST");
    draw_text_clip(c, &f->mono, lx + 16, y1 + 68, iw, s_line, th.ink, 0);

    fill_rect(c, lx + 16, y1 + 84, iw, 2, th.div);
    const char *det = d->diag_detail[0] ? d->diag_detail :
        "No crash dump or boot failure was found for this title. You can launch the game with SQUARE or experiment with the parameters on the right.";
    draw_wrapped(c, &f->mono, lx + 16, y1 + 116, 32, iw, 3, det, th.dim, 0);

    // 2. Step-by-Step Recommendations Box
    int y2 = y1 + h1 + 14, h2 = 204;
    fill_round(c, lx, y2, lw, h2, 16, sub_box);
    stroke_rect(c, lx, y2, lw, h2, 2, th.div);
    draw_text_clip(c, &f->mono, lx + 16, y2 + 32, iw,
                   d->rec_applied ? "ADVISOR PROFILE ACTIVE (PRESS SQUARE TO LAUNCH):" : "ADVISOR ACTION PLAN (PRESS R2 TO APPLY ALL):",
                   d->rec_applied ? BLUE : (a->dark_theme ? YEL : INK), 0);

    const char *st1 = d->step1[0] ? d->step1 : "1. Memory hook -> LATE (F120) if game crashes on boot.";
    const char *st2 = d->step2[0] ? d->step2 : "2. Buffer reg -> TOP GROUP (16-N) or DEFERRED if black screen.";
    const char *st3 = d->step3[0] ? d->step3 : "3. Flip pacing -> POST-SUBMIT or TIMER (PAIR) if stutter/hang.";
    draw_text_clip(c, &f->mono, lx + 16, y2 + 70,  iw, st1, !d->rec_applied && (d->rec_row > 0 || d->rec_mask) ? (a->dark_theme ? YEL : RED) : th.ink, 0);
    draw_text_clip(c, &f->mono, lx + 16, y2 + 106, iw, st2, th.ink, 0);
    draw_text_clip(c, &f->mono, lx + 16, y2 + 142, iw, st3, th.dim, 0);
    draw_text_clip(c, &f->mono, lx + 16, y2 + 182, iw,
                   a->status[0] ? a->status : "D-PAD: TUNE | R2: APPLY ALL ADVISOR SETTINGS | SQUARE: LAUNCH",
                   a->status[0] ? (d->rec_applied ? BLUE : RED) : BLUE, 0);

    // 3. Log Tail Box
    int y3 = y2 + h2 + 14, h3 = 216;
    fill_round(c, lx, y3, lw, h3, 16, sub_box);
    stroke_rect(c, lx, y3, lw, h3, 2, th.div);
    draw_text_clip(c, &f->mono, lx + 16, y3 + 32, iw, "RECENT CONSOLE LOG LINES (CRASH / BOOT / TRACE):", th.dim, 0);
    if (d->n_tail > 0) {
        for (int i = 0; i < d->n_tail && i < 4; i++) {
            draw_text_clip(c, &f->mono, lx + 16, y3 + 70 + i * 36, iw, d->log_tail[i], th.ink, 0);
        }
    } else {
        draw_text_clip(c, &f->mono, lx + 16, y3 + 76, iw, "No log lines recorded yet for this title.", th.dim, 0);
        draw_text_clip(c, &f->mono, lx + 16, y3 + 114, iw, "Enable DEBUG LOG in 04 PLUGIN for full boot/API traces.", th.dim, 0);
    }

    // ---- Right Column: All 7 Dev Parameters with Live Controls & Full Descriptions ----
    int rx = lx + lw + 24, rw_box = w - (lx - M) - lw - 24 - 26;
    Row r[8];
    r[1] = (Row){ "1. Engine / Buffers", "", row_needs_fix(d, 1) ? 3 : 0, 0 }; pipe_preset_label(g, r[1].value);
    r[2] = (Row){ "2. Memory hook",      "", row_needs_fix(d, 2) ? 3 : 0, 0 }; cat(r[2].value, shave_name(d->manual_mode ? d->shave_mode : 0));
    r[3] = (Row){ "3. Buffer reg",       "", row_needs_fix(d, 3) ? 3 : 0, 0 }; cat(r[3].value, reg_name(d->manual_mode ? d->reg_mode : 0));
    r[4] = (Row){ "4. Flip pacing",      "", row_needs_fix(d, 4) ? 3 : 0, 0 }; cat(r[4].value, pace_name(d->manual_mode ? d->pace_order : 0));
    r[5] = (Row){ "5. Post target",      "", row_needs_fix(d, 5) ? 3 : 0, 0 }; cat(r[5].value, post_name(d->manual_mode ? d->post_mid : 0));
    r[6] = (Row){ "6. FPS lock",         "", row_needs_fix(d, 6) ? 3 : 0, 0 }; cat(r[6].value, fps_lock_name(d->manual_mode ? d->fps_lock : 0));
    r[7] = (Row){ "7. Tile mode",        "", row_needs_fix(d, 7) ? 3 : 0, 0 }; cat(r[7].value, tile_mode_name(d->manual_mode ? d->tile_mode : 0));

    for (int i = 1; i <= 7; i++) {
        int cy = y1 + (i - 1) * 94, ch = 86;
        int sel = (a->row == i) || (a->row == 0 && i == 1);
        int rec = row_needs_fix(d, i);
        uint32_t cbg = sel ? (a->dark_theme ? 0xFF202634u : 0xFFF7E48Au) : sub_box;
        uint32_t cbd = rec ? RED : (sel ? (a->dark_theme ? YEL : INK) : th.div);
        fill_round(c, rx, cy, rw_box, ch, 14, cbg);
        stroke_rect(c, rx, cy, rw_box, ch, sel || rec ? 3 : 2, cbd);

        draw_text_clip(c, &f->mono, rx + 16, cy + 28, 340, r[i].name, rec ? RED : (sel && a->dark_theme ? YEL : th.ink), 0);
        char val_buf[48] = "";
        if (rec) cat(val_buf, "[FIX] ");
        if (sel) cat(val_buf, "< ");
        cat(val_buf, r[i].value);
        if (sel) cat(val_buf, " >");
        int vw = text_width(&f->mono, val_buf, 0);
        draw_text(c, &f->mono, rx + rw_box - 16 - vw, cy + 28, val_buf, rec ? (a->dark_theme ? YEL : RED) : th.ink, 0);

        const char *s1 = "", *s2 = "";
        dev_row_guide_full(i, &s1, &s2);
        draw_text_clip(c, &f->mono, rx + 16, cy + 54, rw_box - 32, s1, th.dim, 0);
        draw_text_clip(c, &f->mono, rx + 16, cy + 78, rw_box - 32, s2, th.dim, 0);
    }
}

static void draw_dev(Canvas *c, App *a, int top)
{
    Theme th = theme_of(a);
    Fonts *f = a->f;
    int y = top + 58, left = 520, lw = left - 36, iw = lw - 36;
    if (!a->ngames) {
        draw_text(c, &f->title, M, y + 60, "No games found on this console.", th.ink, 0);
        return;
    }
    Game *g = &a->games[a->sel];
    DevCfg *d = &g->dev;
    if (a->dev_expand) {
        draw_dev_expanded(c, a, top, g, d);
        return;
    }

    // ---- Block 1: Compact Game Header Card (y .. y+128) ----
    fill_round(c, M, y, lw, 128, 18, th.panel);
    stroke_rect(c, M, y, lw, 128, 2, th.div);
    int isz = 88;
    if (g->icon) blit_scaled(c, g->icon, g->iw, g->ih, M + 14, y + 20, isz, isz, 12);
    else hatch(c, M + 14, y + 20, isz, isz, 12, INK, 0xFF232323u, 24);
    int tx = M + 116, tw = lw - 130;
    draw_text_clip(c, &f->title, tx, y + 42, tw, g->title[0] ? g->title : g->id, th.ink, 0);
    char info1[64] = ""; cat(info1, g->id); cat(info1, "  [L2: GAME]");
    draw_text_clip(c, &f->mono, tx, y + 76, tw, info1, th.dim, 0);
    char rc_short[40]; format_recipe(d, rc_short);
    draw_text_clip(c, &f->mono, tx, y + 108, tw, rc_short, d->manual_mode ? (a->dark_theme ? YEL : RED) : BLUE, 0);

    // ---- Block 2: Crash & Log Advisor Box (y+142 .. y+482, height 340) ----
    int ay = y + 142, ah = 340;
    int has_issue = !d->rec_applied && (d->diag_kind >= DIAG_OOM_BOOT || d->has_crash || d->safe_state > 0 || d->rec_mask != 0);
    uint32_t adv_border = has_issue ? RED : ((d->rec_applied || d->diag_kind == DIAG_OK) ? BLUE : th.div);
    fill_round(c, M, ay, lw, ah, 18, th.panel);
    stroke_rect(c, M, ay, lw, ah, 3, adv_border);
    fill_rect(c, M, ay, lw, 42, adv_border);
    const char *adv_hdr = has_issue ? "CRASH ADVISOR [ISSUE]" :
                          (d->rec_applied && d->rec_manual ? "CRASH ADVISOR [FIX APPLIED]" :
                          (d->diag_kind == DIAG_OK ? "CRASH ADVISOR [HEALTHY]" : "CRASH ADVISOR [READY]"));
    draw_text_clip(c, &f->mono, M + 18, ay + 29, iw, adv_hdr,
                   has_issue || d->rec_applied || d->diag_kind == DIAG_OK ? CREAM : th.ink, 1);

    static const char *st_names[3] = { "0 (EARLY)", "1 (DEFER)", "2 (SAFE)" };
    char st_line[64] = "BOOT: ";
    cat(st_line, st_names[d->safe_state >= 0 && d->safe_state <= 2 ? d->safe_state : 0]);
    cat(st_line, d->manual_mode ? " | MODE: CUSTOM" : " | MODE: AUTO");
    draw_text_clip(c, &f->mono, M + 18, ay + 72, iw, st_line, d->safe_state ? RED : th.dim, 0);

    draw_text_clip(c, &f->mono, M + 18, ay + 106, iw,
                   d->diag_cause[0] ? d->diag_cause : "NO CRASH RECORDED", has_issue ? RED : th.ink, 0);
    draw_text_clip(c, &f->mono, M + 18, ay + 140, iw,
                   d->diag_stage[0] ? d->diag_stage : "WAITING FOR FIRST RUN", th.ink, 0);

    fill_rect(c, M + 18, ay + 156, iw, 2, th.div);
    draw_text_clip(c, &f->mono, M + 18, ay + 186, iw,
                   !d->rec_applied && (d->rec_row > 0 || d->rec_mask) ? "RECOMMENDED FIX (R2: APPLY ALL):" :
                   (d->rec_applied && d->rec_manual ? "ADVISOR FIX ACTIVE (SQ: LAUNCH):" : "ADVISOR STATUS:"), th.dim, 0);
    draw_wrapped(c, &f->mono, M + 18, ay + 220, 32, iw, 2,
                 d->diag_advice[0] ? d->diag_advice : "DEFAULT SETTINGS ARE OK",
                 !d->rec_applied && (d->rec_row > 0 || d->rec_mask) ? (a->dark_theme ? YEL : RED) : BLUE, 0);

    fill_round(c, M + 14, ay + 280, lw - 28, 46, 12, a->dark_theme ? 0xFF222836u : INK);
    draw_button(c, BTN_CIRCLE, M + 28, ay + 303, 24, YEL);
    draw_text_clip(c, &f->mono, M + 62, ay + 312, lw - 84, "CIRCLE: EXPAND ADVISOR", CREAM, 0);

    // ---- Block 3: Selected Setting Guide Box (y+496 .. y+756, height 260) ----
    int gy = y + 496, gh = 260;
    fill_round(c, M, gy, lw, gh, 18, th.panel);
    stroke_rect(c, M, gy, lw, gh, 2, th.div);
    draw_text_clip(c, &f->mono, M + 18, gy + 34, iw, "SELECTED PARAMETER GUIDE:", BLUE, 0);
    const char *full_g = dev_row_guide_full(a->row, 0, 0);
    draw_wrapped(c, &f->mono, M + 18, gy + 72, 34, iw, 4, full_g, th.ink, 0);

    fill_rect(c, M + 18, gy + 198, iw, 2, th.div);
    const char *st_msg = a->status[0] ? a->status : "L2: GAME | R2: APPLY ADVISOR | SQ: RUN";
    draw_text_clip(c, &f->mono, M + 18, gy + 234, iw, st_msg, a->status[0] ? (d->rec_applied ? BLUE : (a->dark_theme ? YEL : RED)) : th.dim, 0);

    int auto_k = d->manual_mode ? 0 : 4;
    Row r[8];
    r[0] = (Row){ "Control mode", "", 1, d->manual_mode };
    cat(r[0].value, d->manual_mode ? "CUSTOM (MANUAL)" : "AUTO (ALGORITHM)");

    r[1] = (Row){ "Engine / Buffers", "", row_needs_fix(d, 1) ? 3 : auto_k, 0 };
    if (row_needs_fix(d, 1)) cat(r[1].value, "[!] ");
    pipe_preset_label(g, r[1].value + (row_needs_fix(d, 1) ? 4 : 0));

    r[2] = (Row){ "Memory hook", "", row_needs_fix(d, 2) ? 3 : auto_k, 0 };
    if (row_needs_fix(d, 2)) cat(r[2].value, "[!] ");
    cat(r[2].value, shave_name(d->manual_mode ? d->shave_mode : 0));

    r[3] = (Row){ "Buffer reg", "", row_needs_fix(d, 3) ? 3 : auto_k, 0 };
    if (row_needs_fix(d, 3)) cat(r[3].value, "[!] ");
    cat(r[3].value, reg_name(d->manual_mode ? d->reg_mode : 0));

    r[4] = (Row){ "Flip pacing", "", row_needs_fix(d, 4) ? 3 : auto_k, 0 };
    if (row_needs_fix(d, 4)) cat(r[4].value, "[!] ");
    cat(r[4].value, pace_name(d->manual_mode ? d->pace_order : 0));

    r[5] = (Row){ "Post target", "", row_needs_fix(d, 5) ? 3 : auto_k, 0 };
    if (row_needs_fix(d, 5)) cat(r[5].value, "[!] ");
    cat(r[5].value, post_name(d->manual_mode ? d->post_mid : 0));

    r[6] = (Row){ "FPS lock", "", row_needs_fix(d, 6) ? 3 : auto_k, 0 };
    if (row_needs_fix(d, 6)) cat(r[6].value, "[!] ");
    cat(r[6].value, fps_lock_name(d->manual_mode ? d->fps_lock : 0));

    r[7] = (Row){ "Tile mode", "", row_needs_fix(d, 7) ? 3 : auto_k, 0 };
    if (row_needs_fix(d, 7)) cat(r[7].value, "[!] ");
    cat(r[7].value, tile_mode_name(d->manual_mode ? d->tile_mode : 0));

    char head[64] = "TECHNICAL PIPELINE / "; cat(head, g->id);
    const char *bnum = d->manual_mode ? "CUSTOM" : "AUTO";
    const char *sub  = d->manual_mode ? "MANUAL DEV OVERRIDE ACTIVE" : "ALGORITHM IN CONTROL (CHANGE ANY ROW FOR CUSTOM)";
    draw_rows(c, a, M + left, y, c->w - 2 * M - left, head, bnum, sub, r, 8);
}

void app_draw(Canvas *c, App *a)
{
    Theme th = theme_of(a);
    fill_rect(c, 0, 0, c->w, c->h, th.bg);
    int top = draw_header(c, a);
    if (a->tab == TAB_GAMES && !a->detail) {
        draw_games(c, a, top);
        Hint h[] = { { BTN_LR, "BROWSE" }, { BTN_CROSS, "TOGGLE" }, { BTN_TRIANGLE, "SETTINGS" }, { BTN_SQUARE, "LAUNCH GAME" } };
        draw_footer(c, a, h, 4);
    } else if (a->tab == TAB_GAMES) {
        Game *g = &a->games[a->sel];
        char head[64] = "SETTINGS / "; cat(head, g->id);
        draw_detail(c, a, top, g, &g->s, head);
        Hint h[] = { { BTN_UD, "SELECT" }, { BTN_LR, "CHANGE" }, { BTN_TRIANGLE, "DEV ADVISOR" }, { BTN_SQUARE, "LAUNCH GAME" }, { BTN_CIRCLE, "BACK" } };
        draw_footer(c, a, h, 5);
    } else if (a->tab == TAB_DEFAULTS) {
        draw_detail(c, a, top, 0, &a->defaults, "DEFAULTS FOR EVERY GAME");
        Hint h[] = { { BTN_UD, "SELECT" }, { BTN_LR, "CHANGE" }, { BTN_CROSS, "APPLY TO ALL" } };
        draw_footer(c, a, h, 3);
    } else if (a->tab == TAB_OVERLAY) {
        Settings s = a->defaults;
        Row r[4];
        r[0] = (Row){ "Position", "", 0, 0 }; cat(r[0].value, corner_name(a->overlay_pos ? a->overlay_pos : 0));
        r[1] = (Row){ "Opacity", "", 2, a->overlay_opacity / 10 };
        r[2] = (Row){ "Shown in", "", 0, 0 }; cat(r[2].value, "ALL GAMES");
        r[3] = (Row){ "Menu hotkey", "", 0, 0 };
        if (a->rec) {   // live: the largest set held together so far
            if (!a->rec_peak) cat(r[3].value, "PRESS BUTTONS...");
            else for (int i = 0; i < 6; i++) if (a->rec_peak & k_menu_combo_bits[i]) { if (r[3].value[0]) cat(r[3].value, "+"); cat(r[3].value, k_menu_combo_btn[i]); }
        }
        else menu_combo_name(a->menu_combo, r[3].value, (int)sizeof(r[3].value));
        (void)s;
        draw_rows(c, a, M + 520, top + 58, c->w - 2 * M - 520, "PERFORMANCE OVERLAY", "60", "GAME 30 / GPU 1.7 MS", r, 4);
        draw_text(c, a->f->mono.ttf ? &a->f->mono : &a->f->mono, M, top + 120, "SHOWS GAME FPS,", th.ink, 1);
        draw_text(c, &a->f->mono, M, top + 162, "FRAMES ON SCREEN", th.ink, 1);
        draw_text(c, &a->f->mono, M, top + 204, "AND GPU TIME", th.ink, 1);
        if (a->rec) {
            draw_text(c, &a->f->mono, M, top + 288, "PRESS YOUR BUTTONS TOGETHER", RED, 1);
            draw_text(c, &a->f->mono, M, top + 330, "(L1 R1 L2 R2 L3 R3), THEN CROSS", RED, 1);
            draw_text(c, &a->f->mono, M, top + 372, "TO SAVE. CIRCLE: CANCEL.", th.dim, 1);
        } else {
            draw_text(c, &a->f->mono, M, top + 288, "MENU HOTKEY: HOLD THE", th.ink, 1);
            draw_text(c, &a->f->mono, M, top + 330, "BUTTONS 3 SECONDS IN A GAME.", th.ink, 1);
            draw_text(c, &a->f->mono, M, top + 372, a->rec_msg[0] ? a->rec_msg : "APPLIES AT NEXT GAME START.", a->rec_msg[0] ? RED : th.dim, 1);
        }
        Hint h[] = { { BTN_UD, "SELECT" }, { BTN_LR, "CHANGE" }, { BTN_CROSS, a->rec ? "SAVE HOTKEY" : "RECORD HOTKEY" } };
        draw_footer(c, a, h, a->row == 3 ? 3 : 2);
    } else if (a->tab == TAB_DEV) {
        draw_dev(c, a, top);
        Hint h[] = { { BTN_LR, "CHANGE" }, { BTN_CIRCLE, a->dev_expand ? "BACK" : "ADVISOR" }, { BTN_R2, "APPLY FIX" }, { BTN_TRIANGLE, "RESET" }, { BTN_CROSS, "SAVE" }, { BTN_SQUARE, "LAUNCH" } };
        draw_footer(c, a, h, 6);
    } else {
        draw_install(c, a, top);
        Hint h[] = { { BTN_CROSS, "INSTALL" }, { BTN_SQUARE, "TURN OFF" }, { BTN_R2, "WIPE ALL" }, { BTN_TRIANGLE, "DEBUG LOG" }, { BTN_L2, "NET SEND" }, { BTN_CIRCLE, "REPORT" } };
        draw_footer(c, a, h, 6);
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
    case 5: s->b30 = !s->b30; break;
    case 6: s->shq = !s->shq; break;
    }
}
static void set_msg(App *a, const char *s) { a->rec_msg[0] = 0; cat(a->rec_msg, s); }
// Recording of the hotkey (Cross starts it, Cross saves): the largest set of L1 R1 L2 R2 L3 R3 held at the same moment during
// the latest press. Other buttons are ignored while recording, so Cross and Circle never end up in the combination.
int app_pad(App *a, uint32_t held)
{
    if (!a->rec) return 0;
    held &= MENU_COMBO_ALLOWED;
    int redraw = 0;
    if (held && !a->rec_prev) { a->rec_peak = held; redraw = 1; }   // a new press starts over
    else if (held && __builtin_popcount(held) > __builtin_popcount(a->rec_peak)) { a->rec_peak = held; redraw = 1; }
    a->rec_prev = held;
    return redraw;
}
static void rec_finish(App *a)
{
    uint32_t m = a->rec_peak;
    a->rec = 0; a->rec_peak = 0; a->rec_prev = 0;
    if (menu_combo_buttons(m) < 2) set_msg(a, "NOT SAVED: PRESS TWO BUTTONS OR MORE.");
    else { a->menu_combo = m; a->dirty = 1; set_msg(a, ""); }
}
int app_input(App *a, uint32_t b)
{
    int armed = a->clean_armed; a->clean_armed = 0;   // any other button cancels the delete confirmation
    if (a->rec) {   // recording the hotkey: Cross saves, Circle cancels, the rest belongs to the recording
        if (b & PAD_CROSS) rec_finish(a);
        else if (b & PAD_CIRCLE) { a->rec = 0; a->rec_peak = 0; a->rec_prev = 0; set_msg(a, "CANCELLED."); }
        return ACT_NONE;
    }
    if (b & PAD_L1) { step(&a->tab, -1, 0, TAB_COUNT - 1, 1); a->detail = 0; a->dev_expand = 0; a->row = 0; return ACT_NONE; }
    if (b & PAD_R1) { step(&a->tab, 1, 0, TAB_COUNT - 1, 1); a->detail = 0; a->dev_expand = 0; a->row = 0; return ACT_NONE; }
    if (b & PAD_OPTIONS) return ACT_SAVE_EXIT;
    if (a->tab == TAB_GAMES && !a->detail) {
        if (b & PAD_LEFT && a->sel > 0) a->sel--;
        if (b & PAD_RIGHT && a->sel + 1 < a->ngames) a->sel++;
        if (b & PAD_CROSS && a->ngames && a->games[a->sel].installed) { a->games[a->sel].s.fg = !a->games[a->sel].s.fg; a->dirty = 1; }
        if (b & PAD_TRIANGLE && a->ngames) { a->detail = 1; a->row = 0; }
        if (b & PAD_SQUARE) return a->ngames ? ACT_LAUNCH : ACT_SAVE_EXIT;
        return ACT_NONE;
    }
    Settings *s = a->tab == TAB_GAMES ? &a->games[a->sel].s : &a->defaults;
    int rows = a->tab == TAB_OVERLAY ? 4 : 8;
    if (a->tab == TAB_INSTALL) {
        if (b & PAD_R2) {
            if (armed) { a->status[0] = 0; return ACT_CLEAN; }
            a->clean_armed = 1; a->status[0] = 0; cat(a->status, "PRESS R2 AGAIN TO DELETE EVERY VCFG FILE FROM THIS CONSOLE. ANY OTHER BUTTON CANCELS."); return ACT_NONE;
        }
        if (b & PAD_CROSS) return ACT_INSTALL;
        if (b & PAD_SQUARE) return ACT_REMOVE;
        if (b & PAD_TRIANGLE) return ACT_DEBUG;
        if (b & PAD_L2) return ACT_TELEMETRY_TOGGLE;
        if (b & PAD_CIRCLE) return ACT_REPORT;
        return ACT_NONE;
    }
    int r_lo = (a->tab == TAB_DEV && a->dev_expand) ? 1 : 0;
    if (b & PAD_UP) step(&a->row, -1, r_lo, rows - 1, 1);
    if (b & PAD_DOWN) step(&a->row, 1, r_lo, rows - 1, 1);
    int dir = (b & PAD_RIGHT) ? 1 : (b & PAD_LEFT) ? -1 : 0;
    if (a->tab == TAB_DEV) {
        if (!a->ngames) return ACT_NONE;
        Game *g = &a->games[a->sel];
        DevCfg *d = &g->dev;
        if (b & PAD_L2) {
            step(&a->sel, 1, 0, a->ngames - 1, 1);
            a->status[0] = 0;
            return ACT_NONE;
        }
        if (b & PAD_CIRCLE) {
            a->dev_expand = !a->dev_expand;
            if (a->dev_expand && a->row == 0)
                a->row = (d->rec_row >= 1 && d->rec_row <= 7) ? d->rec_row : 1;
            return ACT_NONE;
        }
        if (dir) {
            if (a->row == 0) {
                d->manual_mode = !d->manual_mode;
                if (!d->manual_mode) return ACT_RESET_DEV;
                a->dirty = 1; a->status[0] = 0;
                cat(a->status, "CUSTOM MODE ON (PRESS CROSS TO SAVE)");
                return ACT_NONE;
            }
            d->manual_mode = 1;
            if (a->row == 1) {
                int p = game_pipe_preset(g);
                step(&p, dir, 0, 5, 1);
                game_set_pipe_preset(g, p);
            }
            else if (a->row == 2) step(&d->shave_mode, dir, 0, 2, 1);
            else if (a->row == 3) step(&d->reg_mode, dir, 0, 2, 1);
            else if (a->row == 4) step(&d->pace_order, dir, 0, 3, 1);
            else if (a->row == 5) step(&d->post_mid, dir, 0, 3, 1);
            else if (a->row == 6) step(&d->fps_lock, dir, 0, 3, 1);
            else if (a->row == 7) step(&d->tile_mode, dir, 0, 6, 1);
            a->dirty = 1; a->status[0] = 0; cat(a->status, "CUSTOM OVERRIDE (PRESS CROSS TO SAVE)");
        }
        if (b & PAD_R2) return ACT_APPLY_ADVICE;
        if (b & PAD_TRIANGLE) return ACT_RESET_DEV;
        if (b & PAD_CROSS) {
            a->status[0] = 0;
            cat(a->status, d->manual_mode ? "CUSTOM DEV PROFILE SAVED." : "AUTO (ALGORITHM) RESTORED.");
            return ACT_SAVE;
        }
        if (b & PAD_SQUARE) return ACT_LAUNCH;
        return ACT_NONE;
    }
    if (dir) {
        if (a->tab == TAB_OVERLAY) {
            if (a->row == 0) step(&a->overlay_pos, dir, 1, 8, 1);
            else if (a->row == 1) step(&a->overlay_opacity, dir * 10, 20, 100, 0);
            else if (a->row == 3) {   // presets; a recorded combination continues from the start / the end of the list
                int i = menu_combo_index(a->menu_combo); if (i < 0) i = dir > 0 ? -1 : MENU_COMBO_COUNT;
                step(&i, dir, 0, MENU_COMBO_COUNT - 1, 1); a->menu_combo = menu_combo_from_index(i); set_msg(a, "");
            }
        } else if (a->tab == TAB_GAMES && a->row == 7) {
            Game *g = &a->games[a->sel];
            g->dev.manual_mode = !g->dev.manual_mode;
            if (!g->dev.manual_mode) return ACT_RESET_DEV;
        }
        else if (a->tab == TAB_DEFAULTS && a->row == 7) a->dark_theme = !a->dark_theme;
        else change(s, a->row, dir);
        a->dirty = 1;
    }
    if (a->tab == TAB_GAMES && (b & PAD_TRIANGLE)) {
        a->tab = TAB_DEV; a->detail = 0;
        int rr = a->games[a->sel].dev.rec_row;
        a->row = (rr >= 1 && rr <= 7) ? rr : 0;
        return ACT_SAVE;
    }
    if (a->tab == TAB_GAMES && (b & PAD_SQUARE)) return ACT_LAUNCH;
    if (a->tab == TAB_OVERLAY && a->row == 3 && (b & PAD_CROSS)) { a->rec = 1; a->rec_prev = a->rec_peak = 0; set_msg(a, ""); }
    if (b & PAD_CIRCLE) { a->detail = 0; return ACT_SAVE; }
    if (a->tab == TAB_DEFAULTS && (b & PAD_CROSS)) {
        for (int i = 0; i < a->ngames; i++) if (!a->games[i].testing && !a->games[i].experimental) a->games[i].s = a->defaults;
        a->dirty = 1;
    }
    return ACT_NONE;
}
