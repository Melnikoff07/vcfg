// Frame size -> dispatch geometry of the mode 8 passes, in one place.
// Fixed-Pyramid Pro Scaling architecture:
//  - The luma pyramid (L1: 960x540, L2: 480x270, L3: 240x135) and motion vector grid (F/FT/GK: 120x68, V2/S2: 60x34,
//    V3: 30x17) are FIXED at the 1080p reference grid across all resolutions (720p, 900p, 1080p, 1440p, 2160p).
//    This keeps WORK_SZ_8 = 0x190000 (1.56 MB, 0 extra bytes of Garlic RAM) and ME time at ~0.29 ms even at 4K.
//  - Per-pixel passes (warp8, warp8g, rcas8, pcopy8) dispatch at the native output resolution: wx = (w+7)/8, wy = (h+7)/8.
//  - pyr8.ll downsamples any native (w, h) into 960x540 L1; warp8.ll / warp8g.ll map native (x, y) to the 120x68 grid
//    and scale 1080p motion vectors by (w / 1920.0, h / 1080.0).
#pragma once
#include <stdint.h>

#define FG_BX(w)  (((w) + 15u) / 16u)          // motion blocks of 16x16 full-res px per row (120 at 1080p)
#define FG_BY(h)  (((h) + 15u) / 16u)          // and per column (68 at 1080p)
#define FG_PX(w)  ((w) / 64u)                  // pyr8 / me1 / mf8 / gv8 groups x (30 at 1080p)
#define FG_PY(h)  FG_BY(h)                     // 68 at 1080p
#define FG_M3Y(h) ((FG_BY(h) + 3u) / 4u)       // me3 groups y (17 at 1080p)
#define FG_M2X(w) ((w) / 128u)                 // me2 groups x (15 at 1080p)
#define FG_M2Y(h) (FG_BY(h) / 2u)              // 34 at 1080p
#define FG_WX(w)  (((w) + 7u) / 8u)            // per-pixel passes, 8x8 groups (160/200/240/320/480)
#define FG_WY(h)  (((h) + 7u) / 8u)            // (90/113/135/180/270)
#define FG_ZX(w)  (FG_BX(w) / 2u + 1u)         // gvz8 (61)
#define FG_ZY(h)  (FG_BY(h) / 2u + 1u)         // 35
#define FG_DX(w)  ((w) / 16u)                  // dump8 (120)

_Static_assert(FG_PX(1920u) == 30u && FG_PY(1080u) == 68u, "pyr8/me1/mf8 groups");
_Static_assert(FG_PX(1920u) == 30u && FG_M3Y(1080u) == 17u, "me3 groups");
_Static_assert(FG_M2X(1920u) == 15u && FG_M2Y(1080u) == 34u, "me2 groups");
_Static_assert(FG_WX(1920u) == 240u && FG_WY(1080u) == 135u, "warp8/rcas8/pcopy8 1080p groups");
_Static_assert(FG_WX(2560u) == 320u && FG_WY(1440u) == 180u, "warp8/rcas8/pcopy8 1440p groups");
_Static_assert(FG_WX(3840u) == 480u && FG_WY(2160u) == 270u, "warp8/rcas8/pcopy8 2160p groups");
_Static_assert(FG_ZX(1920u) == 61u && FG_ZY(1080u) == 35u, "gvz8 groups");
_Static_assert(FG_DX(1920u) == 120u && FG_BX(1920u) == 120u && FG_BY(1080u) == 68u, "dump8 / block grid");

typedef struct { uint32_t px, py, m3x, m3y, m2x, m2y, wx, wy, zx, zy, dx, dy; } FgDim;

static inline FgDim fg_dim(uint32_t w, uint32_t h)
{
    // Pyramid and ME passes stay fixed on the 960x540 L1 / 120x68 block grid; warp8/rcas8/pcopy8 scale to native (w, h).
    FgDim d = { FG_PX(1920u), FG_PY(1080u), FG_PX(1920u), FG_M3Y(1080u), FG_M2X(1920u), FG_M2Y(1080u),
                FG_WX(w), FG_WY(h), FG_ZX(1920u), FG_ZY(1080u), FG_DX(1920u), FG_WY(1080u) };
    return d;
}

// Sizes the passes can run at (all 5 standard PS4 / PS4 Pro scanout resolutions enabled with Fixed-Pyramid scaling).
typedef struct { uint32_t w, h; int ready; } FgResEntry;
static const FgResEntry k_fg_res[] = {
    { 3840u, 2160u, 1 },   // 2160p (4K PS4 Pro)
    { 2560u, 1440u, 1 },   // 1440p (PS4 Pro)
    { 1920u, 1080u, 1 },   // 1080p
    { 1600u,  900u, 1 },   // 900p
    { 1280u,  720u, 1 },   // 720p
};

static inline int fg_res_supported(uint32_t w, uint32_t h)
{
    for (unsigned i = 0; i < sizeof(k_fg_res) / sizeof(k_fg_res[0]); i++)
        if (k_fg_res[i].w == w && k_fg_res[i].h == h) return k_fg_res[i].ready;
    return 0;
}
