// VCFG in-game overlay renderer (see vov.h). Layout follows the approved in-game menu design.
#include "vov.h"
#include "vov_fonts.h"

// Palette: 0 transparent, 1..7 solid colours, 16.. text blends (16 coverage levels per fg/bg pair).
enum { C_NONE, C_CREAM, C_INK, C_YELLOW, C_RED, C_BLUE, C_GREY, C_SEL, C_N };
static const uint32_t k_rgb[C_N] = { 0, 0xefeee8, 0x0b0b0b, 0xf5d23a, 0xf2502e, 0x4f86e6, 0x8c8a82, 0xe2e0d6 };

typedef struct { uint8_t *pix; uint32_t *pal; unsigned w, h; int npairs; uint8_t pf[15], pb[15]; } Canvas;

static uint8_t *g_mem;
static size_t g_buf_sz = VOV_BUF_SZ;   // bytes per buffer
static unsigned g_nbuf = 2, g_max_h = VOV_MAX_H;
static int g_cur;                 // buffer drawn last
static uint32_t g_hash[2];        // content hash per buffer

static void bset(void *d, int v, size_t n) { uint8_t *p = (uint8_t *)d; while (n--) *p++ = (uint8_t)v; }
static size_t slen(const char *s) { size_t n = 0; while (s[n]) n++; return n; }

// Scanout buffers are BGRA in memory and osd8 maps byte 0 to component 0, so each entry is stored as B, G, R, A.
static uint32_t pack(uint32_t rgb, unsigned a) { return (rgb & 0xFFFFFFu) | (uint32_t)a << 24; }

static uint32_t mix(uint32_t a, uint32_t b, unsigned t) // t 0..15
{
    uint32_t r = 0;
    for (int s = 0; s < 24; s += 8) {
        unsigned x = a >> s & 255, y = b >> s & 255;
        r |= ((x * (15 - t) + y * t + 7) / 15) << s;
    }
    return r;
}

// Drawing happens in CPU memory (g_scratch); vov_commit then writes only the bytes that differ into the GPU buffer,
// each straight to its final value. The GPU may be reading that buffer (one buffer on small memory), and it never
// sees a half-drawn state (a row filled but its text not drawn yet).
static uint32_t g_scratch_pal[256];
static uint8_t g_scratch_pix[VOV_W * VOV_MAX_H];
static void canvas_begin(Canvas *c, int buf, unsigned w, unsigned h)
{
    (void)buf;
    c->pal = g_scratch_pal; c->pix = g_scratch_pix; c->w = w; c->h = h; c->npairs = 0;
    bset(c->pal, 0, VOV_PAL_SZ);
    for (int i = 1; i < C_N; i++) c->pal[i] = pack(k_rgb[i], 255);
    bset(c->pix, C_NONE, (size_t)w * h);
}
static void vov_commit(const Canvas *c, int buf)
{
    uint8_t *base = g_mem + (size_t)buf * g_buf_sz;
    volatile uint32_t *pal = (volatile uint32_t *)base;
    for (int i = 0; i < 256; i++) if (pal[i] != c->pal[i]) pal[i] = c->pal[i];
    volatile uint64_t *d = (volatile uint64_t *)(base + VOV_PAL_SZ);
    const uint64_t *s = (const uint64_t *)c->pix;
    size_t n = ((size_t)c->w * c->h + 7) / 8;
    for (size_t i = 0; i < n; i++) if (d[i] != s[i]) d[i] = s[i];
}

static int pair(Canvas *c, int fg, int bg)
{
    for (int i = 0; i < c->npairs; i++) if (c->pf[i] == fg && c->pb[i] == bg) return i;
    if (c->npairs == 15) return 0;
    int i = c->npairs++; c->pf[i] = (uint8_t)fg; c->pb[i] = (uint8_t)bg;
    for (unsigned t = 0; t < 16; t++) c->pal[16 + i * 16 + t] = pack(mix(k_rgb[bg], k_rgb[fg], t), 255);
    return i;
}

static void rect(Canvas *c, int x, int y, int w, int h, int col)
{
    if (x < 0) { w += x; x = 0; } if (y < 0) { h += y; y = 0; }
    if (x + w > (int)c->w) w = (int)c->w - x; if (y + h > (int)c->h) h = (int)c->h - y;
    for (int j = 0; j < h; j++) bset(c->pix + (size_t)(y + j) * c->w + x, col, (size_t)(w > 0 ? w : 0));
}

static void frame(Canvas *c, int x, int y, int w, int h, int t, int col)
{
    rect(c, x, y, w, t, col); rect(c, x, y + h - t, w, t, col); rect(c, x, y, t, h, col); rect(c, x + w - t, y, t, h, col);
}

// Cuts rounded corners out of the rectangle [x, x+w) x [y, y+h) (the area outside the radius becomes transparent).
static void round_corners(Canvas *c, int x, int y, int w, int h, int r)
{
    for (int j = 0; j < r; j++) for (int i = 0; i < r; i++) {
        int dx = r - i, dy = r - j;
        if (dx * dx + dy * dy <= r * r) continue;
        int xs[2] = { x + i, x + w - 1 - i }, ys[2] = { y + j, y + h - 1 - j };
        for (int a = 0; a < 2; a++) for (int b = 0; b < 2; b++)
            if (xs[a] >= 0 && ys[b] >= 0 && xs[a] < (int)c->w && ys[b] < (int)c->h) c->pix[(size_t)ys[b] * c->w + xs[a]] = C_NONE;
    }
}

static const VovGlyph *glyph(const VovFont *f, int cp)
{
    for (int i = 0; i < f->n; i++) if (f->g[i].cp == cp) return &f->g[i];
    return 0;
}

// A sequence cut short (a label truncated mid-character) yields an unknown code point and stops at the NUL,
// never past it.
static int next_cp(const char **ps)
{
    const uint8_t *s = (const uint8_t *)*ps; int cp = *s++;
    int n = cp >= 0xF0 ? 3 : cp >= 0xE0 ? 2 : cp >= 0xC0 ? 1 : 0;
    if (n) cp &= 63 >> n;
    for (; n; n--) {
        if ((*s & 0xC0) != 0x80) { cp = -1; break; }
        cp = cp << 6 | (*s++ & 63);
    }
    *ps = (const char *)s; return cp;
}

static int text_w(const VovFont *f, const char *s, int track)
{
    int w = 0;
    while (*s) { const VovGlyph *g = glyph(f, next_cp(&s)); if (g) w += g->adv + track; }
    return w > 0 ? w - track : 0;
}

// Draws text with its baseline at y; bg is the colour under the text (for antialiasing). Returns the end x.
static int text(Canvas *c, const VovFont *f, int x, int y, const char *s, int fg, int bg, int track, int max_x)
{
    int base = 16 + pair(c, fg, bg) * 16;
    while (*s) {
        const VovGlyph *g = glyph(f, next_cp(&s));
        if (!g) continue;
        if (max_x && x + g->adv > max_x) break;
        const uint8_t *b = f->bits + g->off;
        for (int j = 0; j < g->h; j++) {
            int py = y + g->y + j; if (py < 0 || py >= (int)c->h) continue;
            for (int i = 0; i < g->w; i++) {
                int px = x + g->x + i; if (px < 0 || px >= (int)c->w) continue;
                unsigned v = b[j * g->w + i] >> 4;
                if (v) c->pix[(size_t)py * c->w + px] = (uint8_t)(base + v);
            }
        }
        x += g->adv + track;
    }
    return x;
}

static void fmt_x10(char *o, unsigned v, int frac)   // "60" or "24.1"
{
    char t[12]; int n = 0; unsigned w = v / 10;
    do { t[n++] = (char)('0' + w % 10); w /= 10; } while (w && n < 10);
    while (n) *o++ = t[--n];
    if (frac) { *o++ = '.'; *o++ = (char)('0' + v % 10); }
    *o = 0;
}

static uint32_t hash(const void *p, size_t n)
{
    const uint8_t *b = (const uint8_t *)p; uint32_t h = 2166136261u;
    while (n--) { h ^= *b++; h *= 16777619u; }
    return h ? h : 1;
}

static const uint8_t *g_last;
void vov_init(uint8_t *mem) { g_mem = mem; g_buf_sz = VOV_BUF_SZ; g_nbuf = 2; g_max_h = VOV_MAX_H; g_cur = 0; g_hash[0] = g_hash[1] = 0; g_last = 0; }
void vov_init_size(uint8_t *mem, size_t size)
{
    if (size >= VOV_MEM_SZ) { vov_init(mem); return; }
    vov_init(mem);
    g_nbuf = 1; g_buf_sz = size;
    size_t h = (size - VOV_PAL_SZ) / VOV_W;
    g_max_h = (unsigned)(h > VOV_MAX_H ? VOV_MAX_H : h) & ~15u;
}
const uint8_t *vov_pal_ptr(void) { return g_last; }
const uint8_t *vov_pix_ptr(void) { return g_last ? g_last + VOV_PAL_SZ : 0; }

static int pick(uint32_t h)  // returns buffer to use; draws needed when its hash differs
{
    if (g_hash[g_cur] == h) return g_cur;
    if (g_nbuf > 1) g_cur ^= 1;
    g_hash[g_cur] = 0; return g_cur;
}

// Small pixel mark from the launcher (top right of the header).
static void mark(Canvas *c, int x, int y, int s)
{
    rect(c, x, y, s, s, C_RED); rect(c, x + s, y, s, s, C_INK); rect(c, x + 3 * s, y, s, s, C_RED);
    rect(c, x + s, y + s, s, s, C_INK); rect(c, x + 2 * s, y + s, s, s, C_RED); rect(c, x + 3 * s, y + s, s, s, C_RED);
}

static void upper(char *d, const char *s, size_t cap)
{
    size_t i = 0;
    while (*s && i + 1 < cap) {
        unsigned char ch = (unsigned char)*s++;
        if (ch >= 0x80) continue;                    // drop non-ASCII (trademark signs, accents)
        d[i++] = (char)(ch >= 'a' && ch <= 'z' ? ch - 32 : ch);
    }
    d[i] = 0;
}

int vov_menu(const VovMenu *m, const uint8_t **pal, const uint8_t **pix, unsigned *w, unsigned *h)
{
    if (!g_mem) return 0;
    const int W = (int)VOV_W, PAD = 24, HEAD = m->status[0] ? 168 : 152, FOOT = 56;
    int nrows = m->nrows > 10 ? 10 : m->nrows, ROW = 58;
    if (nrows && HEAD + nrows * ROW + FOOT > (int)g_max_h) {   // small overlay memory: shorter rows (min 44 px)
        ROW = ((int)g_max_h - HEAD - FOOT) / nrows;
        if (ROW < 44) { ROW = 44; nrows = ((int)g_max_h - HEAD - FOOT) / ROW; }
        if (nrows < 0) nrows = 0;   // memory below one header + footer: header and footer only
    }
    unsigned H = (unsigned)(HEAD + nrows * ROW + FOOT);
    H = (H + 15u) & ~15u; if (H > g_max_h) H = g_max_h;
    static VovMenu key; key = *m;   // only what is shown: the fps numbers are drawn rounded to 10
    key.out_fps_x10 = (m->out_fps_x10 + 5) / 10 * 10; key.game_fps_x10 = (m->game_fps_x10 + 5) / 10 * 10;
    uint32_t hs = hash(&key, sizeof(key)) ^ 0x4D454E55u;
    int buf = pick(hs);
    Canvas c; c.w = W; c.h = H;
    if (!g_hash[buf]) {
        canvas_begin(&c, buf, (unsigned)W, H);
        int bottom = HEAD + nrows * ROW + FOOT;
        rect(&c, 0, 0, W, HEAD, C_YELLOW);
        char line[64], t[44];
        upper(t, m->title[0] ? m->title : "GAME", sizeof(t));
        int x = text(&c, &vov_mono20, PAD, 40, "VCFG · ", C_INK, C_YELLOW, 1, 0);
        text(&c, &vov_mono20, x, 40, t, C_INK, C_YELLOW, 1, W - PAD - 70);
        mark(&c, W - PAD - 56, 22, 14);
        fmt_x10(line, (m->out_fps_x10 + 5) / 10 * 10, 0);
        x = text(&c, &vov_big84, PAD - 4, 134, line, C_INK, C_YELLOW, -2, 0);
        char from[24] = "FROM "; fmt_x10(from + 5, (m->game_fps_x10 + 5) / 10 * 10, 0);
        text(&c, &vov_mono20, x + 12, 130, from, C_INK, C_YELLOW, 1, 0);
        if (m->status[0]) { upper(t, m->status, sizeof(t)); text(&c, &vov_mono20, PAD, 158, t, C_INK, C_YELLOW, 0, W - PAD); }
        for (int i = 0; i < nrows; i++) {
            const VovRow *r = &m->rows[i];
            int y = HEAD + i * ROW, sel = i == m->sel, bg = sel ? C_SEL : C_CREAM;
            rect(&c, 0, y, W, ROW, bg);
            if (sel) rect(&c, 0, y, 6, ROW, C_INK);
            int ty = y + ROW / 2 + 9;
            int bx = text(&c, &vov_text25, PAD, ty, sel ? "› " : "", C_INK, bg, 0, 0);
            text(&c, &vov_text25, bx, ty, r->label, C_INK, bg, 0, W / 2 + 40);
            if (r->kind == VOV_TOGGLE) {
                const char *v = r->on ? "ON" : "OFF";
                int tw = text_w(&vov_mono20, v, 1), pw = tw + 28, px = W - PAD - pw, py = y + (ROW - 30) / 2;
                rect(&c, px, py, pw, 30, r->on ? C_RED : C_GREY); round_corners(&c, px, py, pw, 30, 15);
                for (int j = py; j < py + 30; j++) for (int k = px; k < px + pw; k++)   // corners show the row colour
                    if (c.pix[(size_t)j * c.w + k] == C_NONE) c.pix[(size_t)j * c.w + k] = (uint8_t)bg;
                text(&c, &vov_mono20, px + 14, py + 22, v, r->on ? C_INK : C_CREAM, r->on ? C_RED : C_GREY, 1, 0);
            } else if (r->kind == VOV_SEGMENTS) {
                int n = (r->pct * 6 + 50) / 100, s = 16, gap = 4, x0 = W - PAD - (6 * s + 5 * gap);
                for (int k = 0; k < 6; k++) {
                    int sx = x0 + k * (s + gap), sy = y + (ROW - s) / 2;
                    if (k < n) rect(&c, sx, sy, s, s, C_BLUE); else frame(&c, sx, sy, s, s, 2, C_INK);
                }
            } else {
                char v[28]; upper(v, r->value, sizeof(v));
                int tw = text_w(&vov_mono20, v, 1);
                text(&c, &vov_mono20, W - PAD - tw, ty - 1, v, C_INK, bg, 1, 0);
            }
            if (i + 1 < nrows && !sel && i + 1 != m->sel) rect(&c, PAD, y + ROW - 2, W - 2 * PAD, 2, C_INK);
        }
        int fy = HEAD + nrows * ROW;
        rect(&c, 0, fy, W, (int)H - fy, C_INK);
        text(&c, &vov_mono20, PAD, fy + 35, m->hints, C_CREAM, C_INK, 1, W - PAD);
        if ((int)H > bottom) rect(&c, 0, bottom, W, (int)H - bottom, C_INK);
        round_corners(&c, 0, 0, W, (int)H, 12);
        vov_commit(&c, buf);
        g_hash[buf] = hs;
    }
    uint8_t *base = g_mem + (size_t)buf * g_buf_sz;
    g_last = base; *pal = base; *pix = base + VOV_PAL_SZ; *w = (unsigned)W; *h = H;
    return 1;
}

int vov_counter(const VovCounter *k, const uint8_t **pal, const uint8_t **pix, unsigned *w, unsigned *h)
{
    if (!g_mem) return 0;
    char num[12], ms[20];
    fmt_x10(num, (k->out_fps_x10 + 5) / 10 * 10, 0);
    fmt_x10(ms, k->frame_ms_x10, 1);
    size_t n = slen(ms); ms[n] = ' '; ms[n + 1] = 'M'; ms[n + 2] = 'S'; ms[n + 3] = 0;
    int nw = text_w(&vov_big40, num, -1) + 32, mw = k->detailed ? text_w(&vov_mono20, ms, 1) + 36 : 0;
    unsigned W = ((unsigned)(nw + mw) + 15u) & ~15u, H = 64;
    VovCounter key = *k; key.out_fps_x10 = (k->out_fps_x10 + 5) / 10 * 10;
    uint32_t hs = hash(&key, sizeof(key)) ^ hash(ms, n) ^ 0x434E5452u;
    int buf = pick(hs);
    if (!g_hash[buf]) {
        Canvas c; canvas_begin(&c, buf, W, H);
        int split = (int)W - nw;
        if (!mw) rect(&c, 0, 0, split, (int)H, C_NONE);
        if (mw) { rect(&c, 0, 0, split, (int)H, C_INK); text(&c, &vov_mono20, 18, 40, ms, C_YELLOW, C_INK, 1, 0); }
        rect(&c, split, 0, nw, (int)H, C_YELLOW);
        text(&c, &vov_big40, split + 16, 47, num, C_INK, C_YELLOW, -1, 0);
        round_corners(&c, 0, 0, (int)W, (int)H, 10);
        vov_commit(&c, buf);
        g_hash[buf] = hs;
    }
    uint8_t *base = g_mem + (size_t)buf * g_buf_sz;
    g_last = base; *pal = base; *pix = base + VOV_PAL_SZ; *w = W; *h = H;
    return 1;
}
