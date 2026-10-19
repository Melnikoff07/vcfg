// Engine-side helpers for the overlay (needs libc: stdio). Included once by each engine's main.c.
#pragma once
#include <stdio.h>
#include <string.h>
#include "vov.h"

// Reads TITLE from the running game's /app0/sce_sys/param.sfo. Leaves out empty on failure.
static void vov_read_title(char *out, size_t cap)
{
    out[0] = 0;
    FILE *f = fopen("/app0/sce_sys/param.sfo", "rb");
    if (!f) return;
    static uint8_t b[64 * 1024];
    size_t n = fread(b, 1, sizeof(b), f); fclose(f);
    if (n < 20 || memcmp(b, "\0PSF", 4)) return;
    uint32_t kt = b[8] | b[9] << 8 | b[10] << 16 | (uint32_t)b[11] << 24;
    uint32_t dt = b[12] | b[13] << 8 | b[14] << 16 | (uint32_t)b[15] << 24;
    uint32_t cnt = b[16] | b[17] << 8 | b[18] << 16 | (uint32_t)b[19] << 24;
    for (uint32_t i = 0; i < cnt && 20 + i * 16 + 16 <= n; i++) {
        const uint8_t *e = b + 20 + i * 16;
        uint32_t ko = e[0] | e[1] << 8, dl = e[4] | e[5] << 8 | e[6] << 16 | (uint32_t)e[7] << 24;
        uint32_t doff = e[12] | e[13] << 8 | e[14] << 16 | (uint32_t)e[15] << 24;
        if ((uint64_t)kt + ko + 6 > n || memcmp(b + kt + ko, "TITLE", 6)) continue;   // key and its NUL in the file
        if ((uint64_t)dt + doff + dl > n) return;
        size_t l = dl < cap ? dl : cap - 1;
        memcpy(out, b + dt + doff, l); out[l] = 0;
        return;
    }
}

static void vov_cpy(char *d, const char *s, size_t cap) { snprintf(d, cap, "%s", s ? s : ""); }

// Overlay panel size -> osd_wh word for osd8.
static inline uint32_t vov_wh(unsigned w, unsigned h, unsigned alpha, unsigned flags)
{
    return (w / 16u) | ((h / 16u) << 8) | ((alpha & 255u) << 16) | ((flags & 3u) << 24);
}
