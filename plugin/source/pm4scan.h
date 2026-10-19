// pm4scan: bounded walker over GNM draw command buffers (PM4, GFX7 / Liverpool) for the velocity-buffer scanner.
// Pure C, no console calls: memory checks and the clock come in as callbacks, so tests/test_pm4scan.c runs it on a PC
// with synthetic command buffers. Two jobs:
//  * FULL (scanner frames, 1 in N): table of every render / depth target the frame writes (address, size, format,
//    tiling, draws, passes, MRT, depth test, clears, aliasing, later sampling through a T# in user data).
//  * TRACK (every frame while a candidate is being captured): where the tracked target was written last and where
//    its memory is overwritten by another target (= the latest point at which a copy is still valid), plus the
//    compute shader registers the game had at that point (our copy dispatch must restore them).
// Register offsets (dword index inside the context / SH register space) and field layouts are GFX7 (CIK) values,
// checked against linux drivers/gpu/drm/amd/include/asic_reg/gca/gfx_7_2_{d,sh_mask,enum}.h; PM4 opcodes against
// Mesa sid.h / shadPS4 pm4_opcodes.h. See docs/VELOCITY_RU.md, section "Registers".
#pragma once
#include <stdint.h>
#include <stddef.h>

#define PM4_MAX_RT     64     // distinct targets per frame (more are counted in `rt_overflow`)
#define PM4_MAX_ENTRY  64     // top-level DCB entries per submit call that we can track
#define PM4_MAX_DEPTH  3      // IB nesting we follow (GNM uses 1 level for "call" command buffers)
#define PM4_MAX_PASSNAME 24
#define PM4_MAX_MARK   48     // debug-marker pass names remembered per frame

// ---- context register indices: (byte address - 0x28000) / 4 ----
#define CTX_DB_RENDER_CONTROL   0x000
#define CTX_DB_DEPTH_VIEW       0x002
#define CTX_DB_HTILE_DATA_BASE  0x005
#define CTX_PA_SC_SCREEN_SCISSOR_BR 0x00D
#define CTX_DB_DEPTH_INFO       0x00F
#define CTX_DB_Z_INFO           0x010
#define CTX_DB_STENCIL_INFO     0x011
#define CTX_DB_Z_READ_BASE      0x012
#define CTX_DB_Z_WRITE_BASE     0x014
#define CTX_DB_DEPTH_SIZE       0x016
#define CTX_DB_DEPTH_SLICE      0x017
#define CTX_PA_SC_WINDOW_SCISSOR_BR 0x082
#define CTX_CB_TARGET_MASK      0x08E
#define CTX_CB_SHADER_MASK      0x08F   // which MRT outputs the bound pixel shader exports (set with the PS state)
#define CTX_PA_SC_GENERIC_SCISSOR_BR 0x091
#define CTX_PA_SC_VPORT_SCISSOR_0_BR 0x095
#define CTX_PA_CL_VPORT_XSCALE  0x10F
#define CTX_PA_CL_VPORT_YSCALE  0x111
#define CTX_DB_DEPTH_CONTROL    0x200
#define CTX_CB_COLOR_CONTROL    0x202
#define CTX_CB_COLOR0_BASE      0x318   // + 15 * n for target n (0x3C bytes per target)
#define CB_STRIDE 15
#define CB_BASE   0
#define CB_PITCH  1
#define CB_SLICE  2
#define CB_VIEW   3
#define CB_INFO   4
#define CB_ATTRIB 5
#define CB_CMASK  7
#define CB_FMASK  9
#define CB_CLEAR0 11
#define CB_CLEAR1 12
// ---- SH register indices: (byte address - 0xB000) / 4 ----
#define SH_USER_DATA_PS_0       0x00C
#define SH_USER_DATA_VS_0       0x04C
#define SH_COMPUTE_NUM_THREAD_X 0x207
#define SH_COMPUTE_PGM_LO       0x20C
#define SH_COMPUTE_PGM_RSRC1    0x212
#define SH_COMPUTE_USER_DATA_0  0x240

// compute registers our copy dispatch overwrites (restored after it, see pm4_csregs_t)
#define PM4_NCS 9
static const uint16_t pm4_cs_reg_idx[PM4_NCS] = { 0x207, 0x208, 0x209, 0x20C, 0x20D, 0x212, 0x213, 0x240, 0x241 };
typedef struct { uint32_t v[PM4_NCS]; uint32_t known; } pm4_csregs_t;   // known: bit i = value i was seen this frame

// descriptor of a target: what stays the same from frame to frame and run to run (addresses do not)
typedef struct {
    uint32_t depth;     // 0 colour (CB), 1 depth (DB)
    uint32_t fmt;       // CB_COLOR_INFO.FORMAT (= IMG_DATA_FORMAT numbering) or DB_Z_INFO.FORMAT (1 Z16, 2 Z24, 3 Z32F)
    uint32_t num;       // CB_COLOR_INFO.NUMBER_TYPE (0 UNORM 1 SNORM 4 UINT 5 SINT 6 SRGB 7 FLOAT), 7 for Z32F
    uint32_t pitch;     // pixels
    uint32_t alloc_h;   // rows of the allocation (CB_COLOR_SLICE / DB_DEPTH_SIZE)
    uint32_t tile;      // tile mode index (CB_COLOR_ATTRIB / DB_Z_INFO)
} pm4_desc_t;

#define PM4RT_FASTCLEAR   0x0001u  // CB_COLOR_INFO.FAST_CLEAR (CMASK fast clear possible: untouched tiles not in memory)
#define PM4RT_COMPRESSED  0x0002u  // CB_COLOR_INFO.COMPRESSION (FMASK) or DB_Z_INFO.TILE_SURFACE_ENABLE (HTILE)
#define PM4RT_ELIMINATE   0x0004u  // written by a CB_COLOR_CONTROL.MODE != NORMAL pass (eliminate fast clear, resolve)
#define PM4RT_DMACLEAR    0x0008u  // DMA_DATA fill over its range
#define PM4RT_DMAWRITE    0x0010u  // DMA_DATA copy into its range
#define PM4RT_ALIASED     0x0020u  // another target / DMA wrote over its memory after its last write
#define PM4RT_ZCLEAR      0x0040u  // DB_RENDER_CONTROL.DEPTH_CLEAR_ENABLE draw
#define PM4RT_MSAA        0x0080u
#define PM4RT_SAMPLED     0x0100u  // a T# with its base address was bound after its first write
#define PM4RT_DISPLAY     0x0200u  // it is one of the game's display buffers
#define PM4RT_WITHMRT     0x0400u  // written together with other colour targets (MRT)

typedef struct {
    uint64_t base, size;        // bytes
    pm4_desc_t d;
    uint32_t w, h;              // active area at the last write (scissors), pixels
    uint32_t bpp, nch;          // bytes per pixel, channels
    uint32_t slot_mask;         // CB slots (bits 0..7) it was bound to; bit 8 = DB
    uint32_t draws, zdraws;     // draws that wrote it, of those with depth test on
    uint32_t first_draw, last_draw, first_pass, last_pass;
    uint32_t flags;
    uint32_t samp_draw;         // first draw / dispatch index that bound it as a texture after its first write (0 none)
    uint32_t samp_ps, samp_cs;  // how many draws / dispatches bound it as a texture (after the first write)
    uint32_t zfunc[8];          // depth: ZFUNC of the draws that wrote it (reversed Z = GREATER / GEQUAL)
    uint32_t ordinal;           // n-th distinct address with this descriptor, in first-write order (0-based)
    uint32_t clear_word[2];     // CB_COLOR_CLEAR_WORD0/1 at the last write (fast clear value)
    uint32_t mark;              // index of the debug-marker pass name active at the first write (+1, 0 = none)
    uint32_t samp_t[8];         // merged build: the first T# the game bound with this base address (valid if samp_draw)
    int32_t score;              // velocity / depth candidate score (pm4_rank)
} pm4_rt_t;

// what the frame tracker found for the tracked target in one submit call
typedef struct {
    int      written;           // written at least once this frame (so far)
    uint64_t addr, size;        // address of the tracked instance this frame (the `ordinal`-th one)
    uint32_t w, h;
    // last write: entry index in the CURRENT call (-1 = in an earlier call of this frame) and the top-level dword
    // offset just after the top-level packet that contained the write
    int32_t  lw_entry; uint32_t lw_end;
    // first overwrite after the last write (alias): entry + top-level packet offset (cut BEFORE that packet)
    int      aliased;
    int32_t  al_entry; uint32_t al_off;
    int      al_nested;         // the overwrite was inside a nested IB (we can only cut before the whole IB packet)
    pm4_csregs_t al_cs;         // game compute registers at the alias point
    uint32_t rewritten;         // written again after an alias (several epochs per frame: which one is velocity?)
    uint32_t ord_seen;          // distinct addresses with the tracked descriptor this frame
} pm4_track_t;

typedef int      (*pm4_readable_fn)(const void *p, size_t n, void *user);   // 1: [p, p+n) is CPU-readable
typedef uint64_t (*pm4_clock_us_fn)(void *user);

enum { PM4_FULL = 1, PM4_TRACK = 2 };
enum { PM4_OK = 0, PM4_ABORT_TIME = 1, PM4_ABORT_SIZE = 2, PM4_ABORT_BADPKT = 3, PM4_ABORT_UNREADABLE = 4 };

typedef struct {
    // ---- configuration (set before pm4_frame_begin) ----
    uint32_t mode;              // PM4_FULL | PM4_TRACK
    pm4_readable_fn readable; pm4_clock_us_fn clock; void *user;
    uint32_t budget_us;         // walk time per frame (all calls), then abort
    uint32_t max_dwords;        // total dwords per frame
    uint64_t display[8]; uint32_t ndisplay;  // display buffer addresses (excluded from candidates)
    pm4_desc_t trk_desc; uint32_t trk_ordinal; int trk_on;   // tracked target
    // ---- register shadow (current state while walking). Kept across frames: the hardware keeps register state
    // between submits too, and a game may bind a target once and draw into it in the next frame's first call ----
    uint32_t ctx[0x400], sh[0x400];
    uint32_t cs_seen;           // bit i: pm4_cs_reg_idx[i] was written THIS frame (restore only what the game set)
    uint32_t cs_first;          // A21: cs_seen at the first dispatch of this frame (valid when cs_first_set)
    int      cs_first_set;
    int      top_chained; uint32_t top_chain_off, cur_top_end;
    // ---- per frame results ----
    uint32_t dwords, packets, ibs, ib_unreadable, draws, dispatches, passes, dma, loads, clear_state, tsharp_hits;
    uint32_t tables_read, rt_overflow, type0, depth_max, calls, entries, ce_entries;
    uint64_t t_start; uint32_t us; int abort; uint32_t abort_at_dw;   // us = CPU time spent walking this frame
    pm4_rt_t rt[PM4_MAX_RT]; uint32_t nrt;
    char marks[PM4_MAX_MARK][PM4_MAX_PASSNAME]; uint32_t nmark, cur_mark;
    pm4_track_t trk;
    pm4_csregs_t entry_cs[PM4_MAX_ENTRY];   // game compute registers at the START of each entry of the current call
    // ---- internal ----
    uint64_t last_set;          // hash of the bound target set (pass counter)
    uint32_t pending_tsharp;    // a NOP "T# in the next SET_SH_REG" marker was seen
    uint64_t seen_tbl[256];     // descriptor tables already scanned this frame (open addressing, 0 = empty)
    uint32_t ord_n; uint64_t ord_addr[8];   // distinct addresses seen with the tracked descriptor
    int32_t  cur_entry; uint32_t cur_top_off; uint32_t cur_depth;
} pm4_frame_t;

void pm4_frame_begin(pm4_frame_t *f);
// start of one sceGnmSubmit* call (the tracker's entry indices are per call)
void pm4_call_begin(pm4_frame_t *f);
// walk one top-level entry (entry = its index in the current call). Returns PM4_OK or an abort code; after an
// abort the rest of the frame is not walked (results are marked incomplete).
int pm4_walk_entry(pm4_frame_t *f, const uint32_t *dcb, uint32_t ndw, int32_t entry);
// rank the table: sets rt[i].score for velocity (colour) and depth candidates, screen = display size
void pm4_rank(pm4_frame_t *f, uint32_t screen_w, uint32_t screen_h);
const char *pm4_fmt_name(uint32_t depth, uint32_t fmt);
const char *pm4_num_name(uint32_t num);
int pm4_desc_eq(const pm4_desc_t *a, const pm4_desc_t *b);
// bytes per pixel / channel count of a CB format (0 if unknown)
uint32_t pm4_cb_bpp(uint32_t fmt);
uint32_t pm4_cb_nch(uint32_t fmt);
// merged build (idea of the mode85_mv_scanner variant): tiling index and pitch of the T# the game itself samples this
// target with, if one was seen and its data format fits the target. Returns 1 and fills *tile / *pitch, else 0.
// The capture then uses these instead of the CB / DB derived ones when they differ ([HYP 6]: CB tile index == texture
// tile index; the game's own T# is by construction what reads the surface correctly).
int pm4_rt_game_tsharp(const pm4_rt_t *r, uint32_t *tile, uint32_t *pitch);
