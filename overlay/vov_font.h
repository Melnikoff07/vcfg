// Baked glyph atlas types for the in-game overlay.
#pragma once
#include <stdint.h>
typedef struct { int32_t cp, off; int16_t w, h, x, y, adv; } VovGlyph;   // x, y: bitmap offset from pen / baseline
typedef struct { const VovGlyph *g; int n; const uint8_t *bits; int ascent, descent; } VovFont;
