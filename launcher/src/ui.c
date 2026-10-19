#include "ui.h"

void ui_memset(void *d, int v, size_t n) { uint8_t *p = (uint8_t *)d; while (n--) *p++ = (uint8_t)v; }
void ui_memcpy(void *d, const void *s, size_t n) { uint8_t *p = (uint8_t *)d; const uint8_t *q = (const uint8_t *)s; while (n--) *p++ = *q++; }
size_t ui_strlen(const char *s) { size_t n = 0; while (s[n]) n++; return n; }

void *arena_alloc(Arena *a, size_t n)
{
    n = (n + 15) & ~(size_t)15;
    if (!a || a->used + n > a->size) return 0;
    void *p = a->base + a->used; a->used += n; return p;
}

// ---- freestanding maths for stb_truetype (only the rasteriser paths are used; SDF helpers are never called) ----
#if defined(_MSC_VER)
#include <emmintrin.h>
static double ui_sqrt(double x) { return _mm_cvtsd_f64(_mm_sqrt_sd(_mm_set_sd(x), _mm_set_sd(x))); }
#else
static double ui_sqrt(double x) { return __builtin_sqrt(x); }
#endif
static int ui_ifloor(double x) { int i = (int)x; return (x < i) ? i - 1 : i; }
static int ui_iceil(double x) { int i = (int)x; return (x > i) ? i + 1 : i; }
static double ui_fabs(double x) { return x < 0 ? -x : x; }
static double ui_fmod(double x, double y) { return x - (double)(long long)(x / y) * y; }
static double ui_pow(double x, double y) { double r = 1; int n = (int)y; while (n-- > 0) r *= x; return r; }
static double ui_cos(double x) { double x2 = x * x; return 1 - x2 / 2 + x2 * x2 / 24 - x2 * x2 * x2 / 720; }
static double ui_acos(double x) { return 1.5707963267948966 - x - x * x * x / 6; }
static void *ui_stb_alloc(size_t n, void *u) { return arena_alloc((Arena *)u, n); }

#define STBTT_ifloor(x) ui_ifloor(x)
#define STBTT_iceil(x) ui_iceil(x)
#define STBTT_sqrt(x) ui_sqrt(x)
#define STBTT_pow(x, y) ui_pow(x, y)
#define STBTT_fmod(x, y) ui_fmod(x, y)
#define STBTT_cos(x) ui_cos(x)
#define STBTT_acos(x) ui_acos(x)
#define STBTT_fabs(x) ui_fabs(x)
#define STBTT_malloc(x, u) ui_stb_alloc(x, u)
#define STBTT_free(x, u) ((void)(x), (void)(u))
#define STBTT_assert(x) ((void)0)
#define STBTT_strlen(x) ui_strlen(x)
#define STBTT_memcpy ui_memcpy
#define STBTT_memset ui_memset
#define STB_TRUETYPE_IMPLEMENTATION
#define STBTT_STATIC
#include "../third_party/stb_truetype.h"

// ---- pixels ----
static inline uint32_t blend(uint32_t dst, uint32_t src, unsigned a)   // a: 0..255 coverage * src alpha
{
    if (a >= 255) return src | 0xff000000u;
    if (!a) return dst;
    unsigned ia = 255 - a;
    unsigned r = (((src >> 16) & 255) * a + ((dst >> 16) & 255) * ia + 127) / 255;
    unsigned g = (((src >> 8) & 255) * a + ((dst >> 8) & 255) * ia + 127) / 255;
    unsigned b = ((src & 255) * a + (dst & 255) * ia + 127) / 255;
    return 0xff000000u | (r << 16) | (g << 8) | b;
}
static inline void put(Canvas *c, int x, int y, uint32_t col, unsigned cov)
{
    if ((unsigned)x >= (unsigned)c->w || (unsigned)y >= (unsigned)c->h) return;
    unsigned a = (cov * (col >> 24) + 127) / 255;
    uint32_t *p = &c->px[(size_t)y * c->pitch + x]; *p = blend(*p, col, a);
}
void fill_rect(Canvas *c, int x, int y, int w, int h, uint32_t col)
{
    int x0 = x < 0 ? 0 : x, y0 = y < 0 ? 0 : y, x1 = x + w > c->w ? c->w : x + w, y1 = y + h > c->h ? c->h : y + h;
    if ((col >> 24) == 255) {
        for (int j = y0; j < y1; j++) { uint32_t *p = &c->px[(size_t)j * c->pitch]; for (int i = x0; i < x1; i++) p[i] = col; }
    } else {
        for (int j = y0; j < y1; j++) for (int i = x0; i < x1; i++) put(c, i, j, col, 255);
    }
}
// Coverage of pixel (i,j) inside a rounded rect, 4x4 supersampled at the corners only.
static unsigned round_cov(int i, int j, int x, int y, int w, int h, int r)
{
    if (r <= 0 || ((i >= x + r && i < x + w - r) || (j >= y + r && j < y + h - r))) return 255;
    float ix0 = (float)(x + r), ix1 = (float)(x + w - r), iy0 = (float)(y + r), iy1 = (float)(y + h - r);
    unsigned in = 0;
    for (int sy = 0; sy < 4; sy++) for (int sx = 0; sx < 4; sx++) {
        float px = i + (sx + 0.5f) / 4.f, py = j + (sy + 0.5f) / 4.f;
        float qx = px < ix0 ? ix0 : (px > ix1 ? ix1 : px), qy = py < iy0 ? iy0 : (py > iy1 ? iy1 : py);
        float dx = px - qx, dy = py - qy;
        if (dx * dx + dy * dy <= (float)r * r) in++;
    }
    return in * 255 / 16;
}
void fill_round(Canvas *c, int x, int y, int w, int h, int r, uint32_t col)
{
    if (r <= 0) { fill_rect(c, x, y, w, h, col); return; }
    if (r * 2 > w) r = w / 2;
    if (r * 2 > h) r = h / 2;
    fill_rect(c, x + r, y, w - 2 * r, h, col);
    fill_rect(c, x, y + r, r, h - 2 * r, col);
    fill_rect(c, x + w - r, y + r, r, h - 2 * r, col);
    for (int j = 0; j < r; j++) for (int i = 0; i < r; i++) {
        put(c, x + i, y + j, col, round_cov(x + i, y + j, x, y, w, h, r));
        put(c, x + w - 1 - i, y + j, col, round_cov(x + w - 1 - i, y + j, x, y, w, h, r));
        put(c, x + i, y + h - 1 - j, col, round_cov(x + i, y + h - 1 - j, x, y, w, h, r));
        put(c, x + w - 1 - i, y + h - 1 - j, col, round_cov(x + w - 1 - i, y + h - 1 - j, x, y, w, h, r));
    }
}
void stroke_rect(Canvas *c, int x, int y, int w, int h, int t, uint32_t col)
{
    fill_rect(c, x, y, w, t, col); fill_rect(c, x, y + h - t, w, t, col);
    fill_rect(c, x, y + t, t, h - 2 * t, col); fill_rect(c, x + w - t, y + t, t, h - 2 * t, col);
}
void dashed_rect(Canvas *c, int x, int y, int w, int h, int t, int dash, uint32_t col)
{
    for (int i = 0; i < w; i += dash * 2) { int L = i + dash > w ? w - i : dash; fill_rect(c, x + i, y, L, t, col); fill_rect(c, x + i, y + h - t, L, t, col); }
    for (int j = 0; j < h; j += dash * 2) { int L = j + dash > h ? h - j : dash; fill_rect(c, x, y + j, t, L, col); fill_rect(c, x + w - t, y + j, t, L, col); }
}
void hatch(Canvas *c, int x, int y, int w, int h, int r, uint32_t a, uint32_t b, int period)
{
    fill_round(c, x, y, w, h, r, a);
    for (int j = 0; j < h; j++) for (int i = 0; i < w; i++)
        if (((i + j) % period) < period / 2 && round_cov(x + i, y + j, x, y, w, h, r) == 255) put(c, x + i, y + j, b, 255);
}
void blit_scaled(Canvas *c, const uint8_t *s, int sw, int sh, int x, int y, int w, int h, int r)
{
    if (!s || w <= 0 || h <= 0) return;
    for (int j = 0; j < h; j++) {
        float fy = ((j + 0.5f) * sh) / h - 0.5f; int y0 = (int)fy; if (fy < 0) { fy = 0; y0 = 0; }
        int y1 = y0 + 1 < sh ? y0 + 1 : sh - 1; float ty = fy - y0;
        for (int i = 0; i < w; i++) {
            float fx = ((i + 0.5f) * sw) / w - 0.5f; int x0 = (int)fx; if (fx < 0) { fx = 0; x0 = 0; }
            int x1 = x0 + 1 < sw ? x0 + 1 : sw - 1; float tx = fx - x0;
            const uint8_t *p00 = s + ((size_t)y0 * sw + x0) * 4, *p01 = s + ((size_t)y0 * sw + x1) * 4;
            const uint8_t *p10 = s + ((size_t)y1 * sw + x0) * 4, *p11 = s + ((size_t)y1 * sw + x1) * 4;
            float v[4];
            for (int k = 0; k < 4; k++) v[k] = (p00[k] * (1 - tx) + p01[k] * tx) * (1 - ty) + (p10[k] * (1 - tx) + p11[k] * tx) * ty;
            uint32_t col = ((uint32_t)v[3] << 24) | ((uint32_t)v[0] << 16) | ((uint32_t)v[1] << 8) | (uint32_t)v[2];
            put(c, x + i, y + j, col | 0, r ? round_cov(x + i, y + j, x, y, w, h, r) : 255);
        }
    }
}

// ---- text ----
int face_init(Face *f, const uint8_t *ttf, int px, Arena *arena)
{
    ui_memset(f, 0, sizeof(*f));
    stbtt_fontinfo *info = (stbtt_fontinfo *)arena_alloc(arena, sizeof(stbtt_fontinfo));
    if (!info || !stbtt_InitFont(info, ttf, stbtt_GetFontOffsetForIndex(ttf, 0))) return 0;
    info->userdata = arena;
    f->ttf = ttf; f->info = info; f->arena = arena; f->px = px;
    f->scale = stbtt_ScaleForMappingEmToPixels(info, (float)px);
    int asc, desc, gap; stbtt_GetFontVMetrics(info, &asc, &desc, &gap);
    f->ascent = (int)(asc * f->scale + 0.5f); f->descent = (int)(desc * f->scale - 0.5f);
    return 1;
}
static void make_glyph(Face *f, Glyph *g, int ch)
{
    stbtt_fontinfo *info = (stbtt_fontinfo *)f->info;
    int adv, lsb; stbtt_GetCodepointHMetrics(info, ch, &adv, &lsb);
    g->adv = (int)(adv * f->scale + 0.5f);
    int x0, y0, x1, y1; stbtt_GetCodepointBitmapBox(info, ch, f->scale, f->scale, &x0, &y0, &x1, &y1);
    g->w = x1 - x0; g->h = y1 - y0; g->xoff = x0; g->yoff = y0;
    if (g->w > 0 && g->h > 0) {
        g->bmp = (uint8_t *)arena_alloc(f->arena, (size_t)g->w * g->h);
        if (g->bmp) stbtt_MakeCodepointBitmap(info, g->bmp, g->w, g->h, g->w, f->scale, f->scale, ch);
    }
    g->ready = 1;
}
static Glyph *glyph(Face *f, int ch)
{
    static Glyph none;
    if (ch >= 32 && ch < 127) { Glyph *g = &f->g[ch]; if (!g->ready) make_glyph(f, g, ch); return g; }
    if (!stbtt_FindGlyphIndex((stbtt_fontinfo *)f->info, ch)) return &none;   // not in the font: draw nothing
    for (int i = 0; i < f->next; i++) if (f->ext_cp[i] == ch) return &f->ext[i];
    if (f->next >= 96) return &none;
    Glyph *g = &f->ext[f->next]; f->ext_cp[f->next++] = ch; make_glyph(f, g, ch); return g;
}
// UTF-8 decoder: returns the next code point, 0 at the end; marks (TM, R, C) and control chars are skipped.
static int next_cp(const char **ps)
{
    for (;;) {
        const unsigned char *s = (const unsigned char *)*ps; int cp, n;
        if (!*s) return 0;
        if (s[0] < 0x80) { cp = s[0]; n = 1; }
        else if ((s[0] & 0xE0) == 0xC0 && (s[1] & 0xC0) == 0x80) { cp = ((s[0] & 31) << 6) | (s[1] & 63); n = 2; }
        else if ((s[0] & 0xF0) == 0xE0 && (s[1] & 0xC0) == 0x80 && (s[2] & 0xC0) == 0x80) { cp = ((s[0] & 15) << 12) | ((s[1] & 63) << 6) | (s[2] & 63); n = 3; }
        else { *ps += 1; continue; }                                    // invalid byte (e.g. a cut sequence)
        *ps += n;
        if (cp < 32 || cp == 0x2122 || cp == 0xAE || cp == 0xA9 || cp == 0x2120) continue;
        return cp;
    }
}
int text_width(Face *f, const char *s, int tracking)
{
    int w = 0, n = 0, cp;
    while ((cp = next_cp(&s))) { w += glyph(f, cp)->adv + tracking; n++; }
    return n ? w - tracking : 0;
}
int draw_text(Canvas *c, Face *f, int x, int base, const char *s, uint32_t col, int tracking)
{
    int x0 = x, cp;
    while ((cp = next_cp(&s))) {
        Glyph *g = glyph(f, cp);
        if (g->bmp)
            for (int j = 0; j < g->h; j++) for (int i = 0; i < g->w; i++) {
                unsigned a = g->bmp[j * g->w + i];
                if (a) put(c, x + g->xoff + i, base + g->yoff + j, col, a);
            }
        x += g->adv + tracking;
    }
    return x - x0 - tracking;
}

// ---- controller button glyphs drawn as shapes (no font dependency) ----
static void line(Canvas *c, float x0, float y0, float x1, float y1, float t, uint32_t col)
{
    float dx = x1 - x0, dy = y1 - y0, L2 = dx * dx + dy * dy;
    int minx = (int)((x0 < x1 ? x0 : x1) - t), maxx = (int)((x0 > x1 ? x0 : x1) + t);
    int miny = (int)((y0 < y1 ? y0 : y1) - t), maxy = (int)((y0 > y1 ? y0 : y1) + t);
    for (int y = miny; y <= maxy; y++) for (int x = minx; x <= maxx; x++) {
        unsigned in = 0;
        for (int s = 0; s < 4; s++) {
            float px = x + (s & 1 ? 0.75f : 0.25f), py = y + (s & 2 ? 0.75f : 0.25f);
            float u = L2 > 0 ? ((px - x0) * dx + (py - y0) * dy) / L2 : 0; if (u < 0) u = 0; if (u > 1) u = 1;
            float qx = x0 + u * dx - px, qy = y0 + u * dy - py;
            if (qx * qx + qy * qy <= t * t / 4) in++;
        }
        if (in) put(c, x, y, col, in * 255 / 4);
    }
}
static void ring(Canvas *c, float cx, float cy, float r, float t, uint32_t col)
{
    for (int y = (int)(cy - r - t); y <= (int)(cy + r + t); y++) for (int x = (int)(cx - r - t); x <= (int)(cx + r + t); x++) {
        unsigned in = 0;
        for (int s = 0; s < 4; s++) {
            float px = x + (s & 1 ? 0.75f : 0.25f) - cx, py = y + (s & 2 ? 0.75f : 0.25f) - cy;
            float d = (float)ui_sqrt(px * px + py * py) - r; if (d < 0) d = -d;
            if (d <= t / 2) in++;
        }
        if (in) put(c, x, y, col, in * 255 / 4);
    }
}
void draw_button(Canvas *c, int kind, int x, int cy, int s, uint32_t col)
{
    float h = (float)s, t = h * 0.14f, m = h / 2;
    switch (kind) {
    case BTN_CROSS: line(c, x + h * 0.15f, cy - m * 0.7f, x + h * 0.85f, cy + m * 0.7f, t, col); line(c, x + h * 0.85f, cy - m * 0.7f, x + h * 0.15f, cy + m * 0.7f, t, col); break;
    case BTN_CIRCLE: ring(c, x + m, (float)cy, m * 0.72f, t, col); break;
    case BTN_TRIANGLE: line(c, x + m, cy - m * 0.75f, x + h * 0.1f, cy + m * 0.6f, t, col); line(c, x + h * 0.1f, cy + m * 0.6f, x + h * 0.9f, cy + m * 0.6f, t, col); line(c, x + h * 0.9f, cy + m * 0.6f, x + m, cy - m * 0.75f, t, col); break;
    case BTN_SQUARE: stroke_rect(c, x + (int)(h * 0.15f), cy - (int)(m * 0.7f), (int)(h * 0.7f), (int)(h * 0.7f), (int)t, col); break;
    case BTN_LR: line(c, x + h * 0.35f, cy - m * 0.55f, x + h * 0.05f, (float)cy, t, col); line(c, x + h * 0.05f, (float)cy, x + h * 0.35f, cy + m * 0.55f, t, col);
                 line(c, x + h * 0.65f, cy - m * 0.55f, x + h * 0.95f, (float)cy, t, col); line(c, x + h * 0.95f, (float)cy, x + h * 0.65f, cy + m * 0.55f, t, col); break;
    case BTN_RIGHT: line(c, x + h * 0.3f, cy - m * 0.75f, x + h * 0.75f, (float)cy, t * 1.3f, col); line(c, x + h * 0.75f, (float)cy, x + h * 0.3f, cy + m * 0.75f, t * 1.3f, col); break;
    case BTN_UD: line(c, x + h * 0.2f, cy - m * 0.3f, x + m, cy - m * 0.85f, t, col); line(c, x + m, cy - m * 0.85f, x + h * 0.8f, cy - m * 0.3f, t, col);
                 line(c, x + h * 0.2f, cy + m * 0.3f, x + m, cy + m * 0.85f, t, col); line(c, x + m, cy + m * 0.85f, x + h * 0.8f, cy + m * 0.3f, t, col); break;
    }
}
