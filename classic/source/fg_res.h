// Frame size -> dispatch geometry of the mode 8 passes, in one place. Until now these numbers were literals in
// build_dcb_flow8 (30 x 68, 240 x 135, ...), valid for 1920x1080 only. The shaders still hard-code the 1080p grid
// (see docs/RES_SHADER_CONSTANTS), so fg_res_supported() stays 1920x1080 only: an entry of the table is switched on when
// its shaders exist. The static asserts below pin every 1080p value to the number it replaced.
#pragma once
#include <stdint.h>

#define FG_BX(w)  (((w) + 15u) / 16u)          // motion blocks of 16x16 full-res px per row (120)
#define FG_BY(h)  (((h) + 15u) / 16u)          // and per column (68)
#define FG_PX(w)  ((w) / 64u)                  // pyr8 / me1 / mf8 / gv8 groups x (30)
#define FG_PY(h)  FG_BY(h)                     // 68
#define FG_M3Y(h) ((FG_BY(h) + 3u) / 4u)       // me3 groups y (17)
#define FG_M2X(w) ((w) / 128u)                 // me2 groups x (15)
#define FG_M2Y(h) (FG_BY(h) / 2u)              // 34
#define FG_WX(w)  ((w) / 8u)                   // per-pixel passes, 8x8 groups (240)
#define FG_WY(h)  (((h) + 7u) / 8u)            // 135
#define FG_ZX(w)  (FG_BX(w) / 2u + 1u)         // gvz8 (61)
#define FG_ZY(h)  (FG_BY(h) / 2u + 1u)         // 35
#define FG_DX(w)  ((w) / 16u)                  // dump8 (120)

_Static_assert(FG_PX(1920u) == 30u && FG_PY(1080u) == 68u, "pyr8/me1/mf8 groups");
_Static_assert(FG_PX(1920u) == 30u && FG_M3Y(1080u) == 17u, "me3 groups");
_Static_assert(FG_M2X(1920u) == 15u && FG_M2Y(1080u) == 34u, "me2 groups");
_Static_assert(FG_WX(1920u) == 240u && FG_WY(1080u) == 135u, "warp8/rcas8/pcopy8 groups");
_Static_assert(FG_ZX(1920u) == 61u && FG_ZY(1080u) == 35u, "gvz8 groups");
_Static_assert(FG_DX(1920u) == 120u && FG_BX(1920u) == 120u && FG_BY(1080u) == 68u, "dump8 / block grid");

typedef struct { uint32_t px, py, m3x, m3y, m2x, m2y, wx, wy, zx, zy, dx, dy; } FgDim;

static inline FgDim fg_dim(uint32_t w, uint32_t h)
{
    FgDim d = { FG_PX(w), FG_PY(h), FG_PX(w), FG_M3Y(h), FG_M2X(w), FG_M2Y(h), FG_WX(w), FG_WY(h), FG_ZX(w), FG_ZY(h),
                FG_DX(w), FG_WY(h) };
    return d;
}

// Sizes the passes can run at. ready = the shaders exist for that size (1 only for 1080p until the shader work is done).
typedef struct { uint32_t w, h; int ready; } FgResEntry;
static const FgResEntry k_fg_res[] = {
    { 1920u, 1080u, 1 },
    { 1600u,  900u, 0 },   // 900p
    { 1280u,  720u, 0 },   // 720p
};

static inline int fg_res_supported(uint32_t w, uint32_t h)
{
    for (unsigned i = 0; i < sizeof(k_fg_res) / sizeof(k_fg_res[0]); i++)
        if (k_fg_res[i].w == w && k_fg_res[i].h == h) return k_fg_res[i].ready;
    return 0;
}
