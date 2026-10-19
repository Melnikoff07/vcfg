// gvcal: decides from our own optical flow whether a captured buffer IS the game's velocity buffer and learns its
// encoding: per axis  flow = a * raw + b  (a: units, sign, Y flip, direction; b: centre offset of UNORM encodings,
// jitter), plus "objects only" buffers whose untouched pixels hold one clear value (sentinel). Pure C (no console calls),
// runs in the plugin's IO thread on a snapshot written by gv8.ll and on a PC in tests/test_gvcal.c.
// Math and thresholds: docs/VELOCITY_RU.md, section D.
#pragma once
#include <stdint.h>

#define GV_BW 120
#define GV_BH 68
#define GV_NB (GV_BW * GV_BH)

// one block of the snapshot (gv8.ll writes it, 40 bytes, layout shared with the shader)
typedef struct {
    float fx, fy;       // our final flow F of this frame (full-res px, mid grid, cur(x) ~ prev(x + d))
    float fpx, fpy;     // F of the previous frame (stale-buffer test)
    float rx, ry;       // raw candidate value (2 channels as read through the T#), sampled at the cur position m - f/2
    uint32_t sad;       // bilateral L1 SAD of the flow vector (8x8 L1 block, sqrt-luma bytes)
    uint32_t tex;       // texture: self-SAD of the cur block shifted by 2 L1 px in x plus in y
    uint32_t flags;     // GVREC_*
    float z;            // depth (nearest of the cell) when a depth buffer is captured, else 0
} gv_rec_t;
#define GVREC_GK     1u   // fgs8 splatted a foreground vector on this block (flow less trustworthy)
#define GVREC_GVALID 2u   // a candidate sample exists for this block (capture valid this frame)

#define GVCAL_MAXF 64

typedef struct {
    double n[2], sr[2], sf[2], srr[2], sff[2], srf[2];      // inlier sums per axis over all informative frames
    float  fmr[2][GVCAL_MAXF], fmf[2][GVCAL_MAXF], fa[2][GVCAL_MAXF];   // per frame: mean raw, mean flow, slope
    uint32_t nf[2];                                          // informative frames per axis (stored up to MAXF)
    uint32_t classes;                                        // motion classes seen (bit mask)
    uint32_t frames, informative, flat, stale_votes, const_frames;
    uint32_t mode_x, mode_y, mode_frames;                    // repeated exact raw value (sentinel candidate)
    double mode_n, mode_sfx, mode_sfy, mode_sff;
    double mode_share;
} gvcal_t;

typedef struct {
    int   state;            // 0 pending, 1 accepted, -1 rejected
    int   soft;             // rejection that may change with the scene (retry this candidate in a later scan)
    char  reason[80];
    float ax, bx, ay, by;   // flow = a * raw + b (per axis)
    float r2x, r2y, jit_x, jit_y, slope_spread;
    int   sentinel; uint32_t sent_x, sent_y;   // raw bit pattern meaning "not written" (objects-only buffer)
    uint32_t frames, blocks, classes;
    char  units[48];
} gvcal_result_t;

// last frame's numbers, for the log
typedef struct {
    uint32_t n_textured, n_rel, n_inl[2];
    float r2[2], a[2], b[2], r2_prev[2], mode_share, mean_fx, mean_fy, std_f;
    int informative, stale;
} gvcal_frame_t;

void gvcal_reset(gvcal_t *c);
// one snapshot; returns 1 if the frame was informative
int  gvcal_add(gvcal_t *c, const gv_rec_t *rec, gvcal_frame_t *fo);
// 0 pending, 1 accept, -1 reject (fills res either way)
int  gvcal_decide(const gvcal_t *c, gvcal_result_t *res);
// re-validation of a stored profile on one snapshot: 1 pass, 0 fail, -1 not informative; *frac = share of reliable
// blocks the profile predicts within max(1.5 px, 10 %)
int  gvcal_check(const gvcal_result_t *p, const gv_rec_t *rec, float *frac, uint32_t *nrel);
