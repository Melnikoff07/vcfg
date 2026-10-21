// VCFG launcher drawing core. Freestanding (no libc): the same code runs on the console and in the PC preview DLL.
#pragma once
#include <stdint.h>
#include <stddef.h>

typedef struct { uint32_t *px; int w, h, pitch; } Canvas;          // 0xAARRGGBB, pitch in pixels

typedef struct { uint8_t *base; size_t size, used; } Arena;
void *arena_alloc(Arena *a, size_t n);

typedef struct {
    uint8_t *bmp; int w, h, xoff, yoff, adv, ready;
} Glyph;
typedef struct {
    const uint8_t *ttf; void *info;          // stbtt_fontinfo
    float scale; int ascent, descent, px;
    Glyph g[128];
    int ext_cp[96]; Glyph ext[96]; int next;   // non-ASCII glyphs (accents, Cyrillic), filled on demand
    Arena *arena;
} Face;
int  face_init(Face *f, const uint8_t *ttf, int pixel_height, Arena *arena);
int  text_width(Face *f, const char *s, int tracking);
int  draw_text(Canvas *c, Face *f, int x, int baseline, const char *s, uint32_t color, int tracking);

void fill_rect(Canvas *c, int x, int y, int w, int h, uint32_t color);
void fill_round(Canvas *c, int x, int y, int w, int h, int r, uint32_t color);
void stroke_rect(Canvas *c, int x, int y, int w, int h, int t, uint32_t color);
void dashed_rect(Canvas *c, int x, int y, int w, int h, int t, int dash, uint32_t color);
void hatch(Canvas *c, int x, int y, int w, int h, int r, uint32_t a, uint32_t b, int period);
// RGBA8 source (stb_image order), bilinear, clipped to a rounded rect of radius r.
void blit_scaled(Canvas *c, const uint8_t *rgba, int sw, int sh, int x, int y, int w, int h, int r);

enum { BTN_CROSS, BTN_CIRCLE, BTN_TRIANGLE, BTN_SQUARE, BTN_LR, BTN_UD, BTN_RIGHT, BTN_R2, BTN_L2 };
void draw_button(Canvas *c, int kind, int x, int cy, int size, uint32_t color);   // returns nothing; size = height

void ui_memset(void *d, int v, size_t n);
void ui_memcpy(void *d, const void *s, size_t n);
size_t ui_strlen(const char *s);
