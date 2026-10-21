// pm4scan.c - see pm4scan.h. Every read of command-buffer memory goes through f->readable() first (top-level
// entries, nested IBs, descriptor tables); every packet size is checked against the end of its buffer; the walk
// stops at f->max_dwords or f->budget_us. Nothing here writes to game memory.
#include "pm4scan.h"
#include <string.h>

#define PKT_TYPE(h)   ((h) >> 30)
#define PKT_COUNT(h)  (((h) >> 16) & 0x3FFFu)
#define PKT_OP(h)     (((h) >> 8) & 0xFFu)

// PM4 type-3 opcodes (Mesa sid.h PKT3_*, shadPS4 PM4ItOpcode)
enum {
    OP_NOP = 0x10, OP_CLEAR_STATE = 0x12, OP_DISPATCH_DIRECT = 0x15, OP_DISPATCH_INDIRECT = 0x16,
    OP_DRAW_INDIRECT = 0x24, OP_DRAW_INDEX_INDIRECT = 0x25, OP_DRAW_INDEX_2 = 0x27, OP_DRAW_INDIRECT_MULTI = 0x2C,
    OP_DRAW_INDEX_AUTO = 0x2D, OP_DRAW_INDEX_IMMD = 0x2E, OP_DRAW_INDEX_MULTI_AUTO = 0x30,
    OP_INDIRECT_BUFFER_CONST = 0x33, OP_DRAW_INDEX_OFFSET_2 = 0x35, OP_DRAW_INDEX_INDIRECT_MULTI = 0x38,
    OP_INDIRECT_BUFFER = 0x3F, OP_DMA_DATA = 0x50, OP_LOAD_SH_REG = 0x5F, OP_LOAD_CONFIG_REG = 0x60,
    OP_LOAD_CONTEXT_REG = 0x61, OP_SET_CONTEXT_REG = 0x69, OP_SET_CONTEXT_REG_INDIRECT = 0x73, OP_SET_SH_REG = 0x76,
    OP_DRAW_INDIRECT_COUNT_MULTI = 0x9C, OP_DRAW_INDEX_INDIRECT_COUNT_MULTI = 0x9D,
};
// GNM driver payloads in NOP packets (shadPS4 PM4CmdNop::PayloadType)
#define NOP_MARKER_PUSH       0x68750001u
#define NOP_SET_TSHARP_UDATA  0x68750005u
#define NOP_COLOR_MARKER_PUSH 0x6875000Eu

uint32_t pm4_cb_bpp(uint32_t fmt)
{
    switch (fmt) {
    case 1: return 1;                                   // 8
    case 2: case 3: case 16: case 17: case 18: case 19: return 2;   // 16, 8_8, 5_6_5, 1_5_5_5, 5_5_5_1, 4_4_4_4
    case 4: case 5: case 6: case 7: case 8: case 9: case 10: case 20: case 21: return 4;
    case 11: case 12: case 22: return 8;                // 32_32, 16_16_16_16, X24_8_32_FLOAT
    case 14: return 16;                                 // 32_32_32_32
    default: return 0;
    }
}
uint32_t pm4_cb_nch(uint32_t fmt)
{
    switch (fmt) {
    case 1: case 2: case 4: return 1;
    case 3: case 5: case 11: case 20: case 21: case 22: return 2;
    case 6: case 7: case 16: return 3;
    case 8: case 9: case 10: case 12: case 14: case 17: case 18: case 19: return 4;
    default: return 0;
    }
}
const char *pm4_fmt_name(uint32_t depth, uint32_t fmt)
{
    if (depth) return fmt == 1 ? "Z16" : fmt == 2 ? "Z24" : fmt == 3 ? "Z32F" : "Z?";
    static const char *n[23] = { "INV", "8", "16", "8_8", "32", "16_16", "10_11_11", "11_11_10", "10_10_10_2",
                                 "2_10_10_10", "8_8_8_8", "32_32", "16_16_16_16", "?13", "32_32_32_32", "?15", "5_6_5",
                                 "1_5_5_5", "5_5_5_1", "4_4_4_4", "8_24", "24_8", "X24_8_32F" };
    return fmt < 23 ? n[fmt] : "?";
}
const char *pm4_num_name(uint32_t num)
{
    static const char *n[8] = { "UNORM", "SNORM", "USCALED", "SSCALED", "UINT", "SINT", "SRGB", "FLOAT" };
    return n[num & 7];
}
int pm4_desc_eq(const pm4_desc_t *a, const pm4_desc_t *b)
{
    return a->depth == b->depth && a->fmt == b->fmt && a->num == b->num && a->pitch == b->pitch &&
           a->alloc_h == b->alloc_h && a->tile == b->tile;
}

static void save_cs(const pm4_frame_t *f, pm4_csregs_t *o)
{
    for (int i = 0; i < PM4_NCS; i++) o->v[i] = f->sh[pm4_cs_reg_idx[i]];
    o->known = f->cs_seen;
}

void pm4_frame_begin(pm4_frame_t *f)
{
    // keep configuration and the register shadow, clear the per-frame part
    f->cs_seen = 0;
    f->dwords = f->packets = f->ibs = f->ib_unreadable = f->draws = f->dispatches = f->passes = f->dma = 0;
    f->loads = f->clear_state = f->tsharp_hits = f->tables_read = f->rt_overflow = f->type0 = f->depth_max = 0;
    f->calls = f->entries = f->ce_entries = 0;
    f->t_start = f->clock ? f->clock(f->user) : 0;
    f->us = 0; f->abort = 0; f->abort_at_dw = 0;
    f->nrt = 0; f->nmark = 0; f->cur_mark = 0;
    memset(&f->trk, 0, sizeof(f->trk));
    f->trk.lw_entry = -1; f->trk.al_entry = -1;
    f->last_set = 0; f->pending_tsharp = 0;
    memset(f->seen_tbl, 0, sizeof(f->seen_tbl));
    f->ord_n = 0;
}

void pm4_call_begin(pm4_frame_t *f)
{
    f->calls++;
    // entry indices restart: a last write / alias found in an earlier call is now "before this call"
    if (f->trk.written) f->trk.lw_entry = -1;
    if (f->trk.aliased) f->trk.al_entry = -1;
}

// ---------------------------------------------------------------------------------------------------------------
static int ranges_overlap(uint64_t a, uint64_t as, uint64_t b, uint64_t bs)
{
    return as && bs && a < b + bs && b < a + as;
}

typedef struct { uint64_t base, size; pm4_desc_t d; uint32_t slot, w, h, bpp, flags, clr[2]; } tgt_t;

static uint32_t br_x(uint32_t v) { return v & 0x7FFFu; }
static uint32_t br_y(uint32_t v) { return (v >> 16) & 0x7FFFu; }

static float bits_f32(uint32_t u)
{
    union { uint32_t u; float f; } x = { u };
    return x.f < 0.0f ? -x.f : x.f;
}

// Strip macro-tile vertical alignment padding when a 16:9 surface's allocation height was rounded up
// (e.g., 1920x1088 -> 1920x1080, 3840x2176 -> 3840x2160, 2560x1472 -> 2560x1440, 1600x928 -> 1600x900, 1280x736 -> 1280x720)
static uint32_t unpad_alloc_h(uint32_t w, uint32_t h)
{
    if (w >= 640u && (w * 9u) % 16u == 0u) {
        uint32_t h16 = (w * 9u) / 16u;
        if (h > h16 && h - h16 <= 64u && (h & 7u) == 0u) return h16;
    }
    return h;
}

static void active_area(const pm4_frame_t *f, uint32_t pitch, uint32_t ah, uint32_t *w, uint32_t *h)
{
    const uint16_t r[4] = { CTX_PA_SC_SCREEN_SCISSOR_BR, CTX_PA_SC_WINDOW_SCISSOR_BR, CTX_PA_SC_GENERIC_SCISSOR_BR,
                            CTX_PA_SC_VPORT_SCISSOR_0_BR };
    uint32_t mw = pitch, mh = ah;
    for (int i = 0; i < 4; i++) {
        uint32_t v = f->ctx[r[i]], x = br_x(v), y = br_y(v);
        if (x >= 8 && x < mw) mw = x;   // scissors that are "wide open" (0x4000) are clamped to the surface
        if (y >= 8 && y < mh) mh = y;
    }
    // Check PA_CL_VPORT_XSCALE / YSCALE for engines that apply dynamic resolution scaling via viewport transform
    // while leaving hardware scissors at full allocation size.
    if (f->ctx[CTX_PA_CL_VPORT_XSCALE] && f->ctx[CTX_PA_CL_VPORT_YSCALE]) {
        float xs = bits_f32(f->ctx[CTX_PA_CL_VPORT_XSCALE]);
        float xo = bits_f32(f->ctx[CTX_PA_CL_VPORT_XOFFSET]);
        float ys = bits_f32(f->ctx[CTX_PA_CL_VPORT_YSCALE]);
        float yo = bits_f32(f->ctx[CTX_PA_CL_VPORT_YOFFSET]);
        if (xs >= 160.0f && xs <= 4096.0f && ys >= 90.0f && ys <= 4096.0f) {
            float fw = (xo >= xs * 0.5f && xo <= xs * 1.5f) ? (xo + xs) : (xs * 2.0f);
            float fh = (yo >= ys * 0.5f && yo <= ys * 1.5f) ? (yo + ys) : (ys * 2.0f);
            uint32_t vw = (uint32_t)(fw + 0.5f), vh = (uint32_t)(fh + 0.5f);
            if (vw >= 320u && vw < mw) mw = vw;
            if (vh >= 180u && vh < mh) mh = vh;
        }
    }
    if (mh == ah) mh = unpad_alloc_h(mw, mh);
    *w = mw; *h = mh;
}

// bound targets of the current draw; returns the count (<= 9)
static int bound_targets(pm4_frame_t *f, tgt_t *t, uint32_t *zfunc_out, int *zon)
{
    int n = 0;
    uint32_t mode = (f->ctx[CTX_CB_COLOR_CONTROL] >> 4) & 7u, mask = f->ctx[CTX_CB_TARGET_MASK];
    // a slot that is enabled in CB_TARGET_MASK but not exported by the pixel shader (CB_SHADER_MASK) is not written
    // (idea from the bb_probe_85 variant). Only applied once the game has set a shader mask (shadow starts at 0)
    if (f->ctx[CTX_CB_SHADER_MASK]) mask &= f->ctx[CTX_CB_SHADER_MASK];
    if (mode != 0) {
        for (int i = 0; i < 8; i++) {
            if (!((mask >> (4 * i)) & 0xFu)) continue;
            const uint32_t *cb = f->ctx + CTX_CB_COLOR0_BASE + CB_STRIDE * i;
            uint32_t info = cb[CB_INFO], fmt = (info >> 2) & 0x1Fu;
            if (!fmt || !cb[CB_BASE]) continue;
            uint32_t bpp = pm4_cb_bpp(fmt);
            if (!bpp) continue;
            tgt_t *o = &t[n++];
            memset(o, 0, sizeof(*o));
            o->base = (uint64_t)cb[CB_BASE] << 8;                          // CB_COLORn_BASE = address >> 8
            o->d.depth = 0; o->d.fmt = fmt; o->d.num = (info >> 8) & 7u;
            o->d.pitch = ((cb[CB_PITCH] & 0x7FFu) + 1u) * 8u;                // TILE_MAX = pitch / 8 - 1
            uint32_t slice = (cb[CB_SLICE] & 0x3FFFFFu) + 1u;               // TILE_MAX = pitch * height / 64 - 1
            o->d.alloc_h = (uint32_t)((uint64_t)slice * 64u / o->d.pitch);
            o->d.tile = cb[CB_ATTRIB] & 0x1Fu;                               // TILE_MODE_INDEX
            o->bpp = bpp;
            o->size = (uint64_t)o->d.pitch * o->d.alloc_h * bpp;
            o->slot = (uint32_t)i;
            if (info & (1u << 13)) o->flags |= PM4RT_FASTCLEAR;
            if (info & (1u << 14)) o->flags |= PM4RT_COMPRESSED;
            if (mode != 1) o->flags |= PM4RT_ELIMINATE;
            if ((cb[CB_ATTRIB] >> 12) & 7u) o->flags |= PM4RT_MSAA;          // NUM_SAMPLES (log2)
            o->clr[0] = cb[CB_CLEAR0]; o->clr[1] = cb[CB_CLEAR1];
            active_area(f, o->d.pitch, o->d.alloc_h, &o->w, &o->h);
        }
    }
    uint32_t zi = f->ctx[CTX_DB_Z_INFO], zf = zi & 3u, dc = f->ctx[CTX_DB_DEPTH_CONTROL];
    uint32_t rc = f->ctx[CTX_DB_RENDER_CONTROL];
    *zon = zf && (dc & 2u);                                       // Z_ENABLE
    *zfunc_out = (dc >> 4) & 7u;
    if (zf && (((dc & 2u) && (dc & 4u)) || (rc & 1u)) && f->ctx[CTX_DB_Z_WRITE_BASE]) {   // Z_WRITE_ENABLE / DEPTH_CLEAR
        tgt_t *o = &t[n++];
        memset(o, 0, sizeof(*o));
        uint32_t ds = f->ctx[CTX_DB_DEPTH_SIZE];
        o->base = (uint64_t)f->ctx[CTX_DB_Z_WRITE_BASE] << 8;
        o->d.depth = 1; o->d.fmt = zf; o->d.num = zf == 3 ? 7u : 0u;
        o->d.pitch = ((ds & 0x7FFu) + 1u) * 8u;                      // PITCH_TILE_MAX
        o->d.alloc_h = (((ds >> 11) & 0x7FFu) + 1u) * 8u;            // HEIGHT_TILE_MAX
        o->d.tile = (zi >> 20) & 7u;                                 // TILE_MODE_INDEX (3 bits on CIK)
        o->bpp = zf == 1 ? 2u : 4u;
        o->size = (uint64_t)o->d.pitch * o->d.alloc_h * o->bpp;
        o->slot = 8;
        if (zi & (1u << 29)) o->flags |= PM4RT_COMPRESSED;          // TILE_SURFACE_ENABLE (HTILE)
        if (rc & 1u) o->flags |= PM4RT_ZCLEAR;
        if ((zi >> 2) & 3u) o->flags |= PM4RT_MSAA;
        active_area(f, o->d.pitch, o->d.alloc_h, &o->w, &o->h);
    }
    return n;
}

static pm4_rt_t *find_rt(pm4_frame_t *f, const tgt_t *t, int add)
{
    for (uint32_t i = 0; i < f->nrt; i++)
        if (f->rt[i].base == t->base && pm4_desc_eq(&f->rt[i].d, &t->d)) return &f->rt[i];
    if (!add) return NULL;
    if (f->nrt >= PM4_MAX_RT) { f->rt_overflow++; return NULL; }
    pm4_rt_t *r = &f->rt[f->nrt++];
    memset(r, 0, sizeof(*r));
    r->base = t->base; r->size = t->size; r->d = t->d; r->bpp = t->bpp;
    r->nch = t->d.depth ? 1u : pm4_cb_nch(t->d.fmt);
    r->first_draw = f->draws; r->first_pass = f->passes;
    r->mark = f->cur_mark;
    for (uint32_t i = 0; i < f->ndisplay; i++)
        if (ranges_overlap(f->display[i], 1, r->base, r->size)) r->flags |= PM4RT_DISPLAY;
    uint32_t ord = 0;   // ordinal among same-descriptor targets
    for (uint32_t i = 0; i + 1 < f->nrt; i++) if (pm4_desc_eq(&f->rt[i].d, &t->d)) ord++;
    r->ordinal = ord;
    return r;
}

// a draw / DMA wrote [b, b + s) with descriptor d: earlier-written targets that it overlaps are now aliased
static void mark_alias_full(pm4_frame_t *f, uint64_t b, uint64_t s, const pm4_desc_t *d)
{
    for (uint32_t i = 0; i < f->nrt; i++) {
        pm4_rt_t *r = &f->rt[i];
        if (r->draws && ranges_overlap(r->base, r->size, b, s) && (!d || r->base != b || !pm4_desc_eq(&r->d, d)))
            r->flags |= PM4RT_ALIASED;
    }
}

static void track_alias(pm4_frame_t *f, uint64_t b, uint64_t s)
{
    pm4_track_t *k = &f->trk;
    if (!f->trk_on || !k->written || k->aliased) return;
    if (!ranges_overlap(k->addr, k->size, b, s)) return;
    k->aliased = 1;
    k->al_entry = f->cur_entry;
    k->al_off = f->top_chained ? f->top_chain_off : f->cur_top_off;
    k->al_nested = f->cur_depth > 0 || f->top_chained;
    save_cs(f, &k->al_cs);
}

// Check if a T# descriptor is a single-mip 2D velocity or depth surface that can be discovered directly from
// shader user data (e.g., compute-written velocity buffers or surfaces bound without a prior CB/DB draw).
static int tsharp_is_surface_candidate(const uint32_t *t, uint64_t base, tgt_t *out)
{
    uint32_t type = t[3] >> 28, dfmt = (t[1] >> 20) & 0x3Fu, nfmt = (t[1] >> 26) & 0xFu;
    if (type != 9u || base < 0x100000ull || base >= (1ull << 47) || (base & 0xFFull)) return 0;
    uint32_t base_lvl = (t[3] >> 12) & 0xFu, last_lvl = (t[3] >> 16) & 0xFu, tile = (t[3] >> 20) & 0x1Fu;
    if (base_lvl != 0u || last_lvl != 0u || tile == 8u) return 0;   // single-mip tiled 2D surface only
    if ((t[4] & 0x1FFFu) != 0u || (t[4] >> 27) != 0u || (t[5] & 0x1FFFFFu) != 0u) return 0;
    uint32_t w = (t[2] & 0x3FFFu) + 1u, h = ((t[2] >> 14) & 0x3FFFu) + 1u;
    uint32_t pitch = ((t[4] >> 13) & 0x3FFFu) + 1u;
    if (w < 640u || w > 4096u || h < 360u || h > 2304u || pitch < w || pitch > 4096u || (pitch & 7u) != 0u) return 0;
    // Aspect ratio: ~16:9 (within 10%) or half-width 8:9 checkerboard
    uint64_t lhs = (uint64_t)w * 9u, rhs = (uint64_t)h * 16u;
    int ar_ok = (lhs * 100u >= rhs * 90u && lhs * 100u <= rhs * 110u) ||
                (lhs * 200u >= rhs * 95u && lhs * 200u <= rhs * 105u);
    if (!ar_ok) return 0;
    int is_vel = (dfmt == 5u || dfmt == 12u) && (nfmt == 7u || nfmt == 1u || nfmt == 0u);
    int is_z32 = (dfmt == 4u && nfmt == 7u);
    int is_z24 = ((dfmt == 20u || dfmt == 21u) && nfmt == 0u);
    if (!is_vel && !is_z32 && !is_z24) return 0;
    memset(out, 0, sizeof(*out));
    out->base = base;
    out->d.depth = (is_z32 || is_z24) ? 1u : 0u;
    out->d.fmt = is_z32 ? 3u : is_z24 ? 2u : dfmt;
    out->d.num = is_z32 ? 7u : is_z24 ? 0u : nfmt;
    out->d.pitch = pitch;
    out->d.alloc_h = (h + 7u) & ~7u;
    out->d.tile = tile;
    out->w = w;
    out->h = h;
    out->bpp = out->d.depth ? 4u : pm4_cb_bpp(dfmt);
    out->size = (uint64_t)out->d.pitch * out->d.alloc_h * out->bpp;
    out->slot = out->d.depth ? 8u : 0u;
    return 1;
}

// ---- T# (image resource) recognition for "was this target sampled later" ----
static void tsharp_match(pm4_frame_t *f, const uint32_t *t, int cs)
{
    uint32_t type = t[3] >> 28, dfmt = (t[1] >> 20) & 0x3Fu;
    if (!(type == 9 || type == 13 || type == 14 || type == 15) || !dfmt) return;   // 2D, 2D array, 2D MSAA (array)
    // T# word 0/1: BASE_ADDRESS is 38 bits (address >> 8) = word 0 + word 1 bits 5:0; bits 7:6 are MTYPE_L2
    // (shadPS4 AmdGpu::Image), so they must not take part in the address compare
    uint64_t base = ((uint64_t)t[0] << 8) | ((uint64_t)(t[1] & 0x3Fu) << 40);
    if (!base) return;
    int matched = 0;
    for (uint32_t i = 0; i < f->nrt; i++) {
        pm4_rt_t *r = &f->rt[i];
        if (r->base != base || !r->draws) continue;
        matched = 1;
        if (!r->samp_draw) { r->samp_draw = f->draws + f->dispatches + 1u; memcpy(r->samp_t, t, sizeof(r->samp_t)); }
        if (cs) r->samp_cs++; else r->samp_ps++;
        r->flags |= PM4RT_SAMPLED;
        // Refine active dimensions from the sampling T# when the draw scissors were left at full allocation size
        if (type == 9u) {
            uint32_t tw = (t[2] & 0x3FFFu) + 1u, th = ((t[2] >> 14) & 0x3FFFu) + 1u;
            if (tw >= 320u && tw <= r->d.pitch && th >= 180u && th <= r->d.alloc_h &&
                r->w == r->d.pitch && (tw < r->w || th < r->h)) {
                r->w = tw; r->h = th;
            }
        }
        f->tsharp_hits++;
    }
    if (!matched && (f->mode & PM4_FULL)) {
        tgt_t st;
        if (tsharp_is_surface_candidate(t, base, &st)) {
            for (uint32_t i = 0; i < f->ndisplay; i++)
                if (ranges_overlap(f->display[i], 1, st.base, st.size)) return;
            pm4_rt_t *r = find_rt(f, &st, 1);
            if (r) {
                if (!r->draws) {
                    r->draws = 20u;
                    r->zdraws = 20u;
                    r->w = st.w;
                    r->h = st.h;
                    r->slot_mask |= 1u << st.slot;
                    if (st.d.depth) r->zfunc[6] = 20u;
                }
                r->last_draw = f->draws;
                r->last_pass = f->passes;
                if (!r->samp_draw) {
                    r->samp_draw = f->draws + f->dispatches + 1u;
                    memcpy(r->samp_t, t, sizeof(r->samp_t));
                }
                if (cs) r->samp_cs++; else r->samp_ps++;
                r->flags |= PM4RT_SAMPLED;
                f->tsharp_hits++;
            }
        }
    }
}

static int tbl_seen(pm4_frame_t *f, uint64_t p)
{
    uint32_t h = (uint32_t)((p >> 4) * 0x9E3779B1u) & 255u;
    for (int i = 0; i < 16; i++) {
        uint32_t j = (h + (uint32_t)i) & 255u;
        if (f->seen_tbl[j] == p) return 1;
        if (!f->seen_tbl[j]) { f->seen_tbl[j] = p; return 0; }
    }
    return 1;   // table full: treat as seen (bounds the work)
}

static uint32_t readable_table_dwords(pm4_frame_t *f, const uint32_t *tb)
{
    if (!f->readable) return 0;
    static const uint32_t probes[5] = { 128u, 64u, 32u, 16u, 8u };
    for (int k = 0; k < 5; k++) {
        if (f->readable(tb, (size_t)probes[k] * 4u, f->user)) return probes[k];
    }
    return 0;
}

// 16 user-data dwords of a shader stage: inline T# (8 dwords) or a pointer (2 dwords) to a table of descriptors.
// GNM puts small resource sets inline in user SGPRs and larger ones behind a pointer (Gnm::InputUsageSlot
// kShaderInputUsageImmResource / kShaderInputUsagePtrResourceTable / ExtendedUserData). We do not know the shader's
// input usage table, so both readings are tried; inside EUD tables, descriptors are 2-dword (8-byte) aligned because
// 64-bit pointers occupy 2 dwords before 8-dword T# descriptors.
static void scan_user_data(pm4_frame_t *f, const uint32_t *ud, int cs)
{
    for (int i = 0; i + 8 <= 16; i++) tsharp_match(f, ud + i, cs);
    for (int i = 0; i + 2 <= 16; i++) {
        uint64_t p = (uint64_t)ud[i] | ((uint64_t)ud[i + 1] << 32);
        if ((p & 3u) || p < 0x100000u || p >= (1ull << 47)) continue;
        if (f->tables_read >= 4096 || tbl_seen(f, p)) continue;
        const uint32_t *tb = (const uint32_t *)(uintptr_t)p;
        uint32_t max_dw = readable_table_dwords(f, tb);
        if (!max_dw) continue;
        f->tables_read++;
        for (uint32_t j = 0; j + 8u <= max_dw; j += 2u) tsharp_match(f, tb + j, cs);
        // Support 1 level of nested EUD -> ResourceTable pointer indirection in the first 16 EUD slots
        for (uint32_t j = 0; j + 2u <= max_dw && j < 32u; j += 2u) {
            uint64_t p2 = (uint64_t)tb[j] | ((uint64_t)tb[j + 1] << 32);
            if ((p2 & 7u) || p2 < 0x100000u || p2 >= (1ull << 47)) continue;
            if (f->tables_read >= 4096 || tbl_seen(f, p2)) continue;
            const uint32_t *tb2 = (const uint32_t *)(uintptr_t)p2;
            uint32_t max_dw2 = readable_table_dwords(f, tb2);
            if (!max_dw2) continue;
            f->tables_read++;
            for (uint32_t k = 0; k + 8u <= max_dw2; k += 2u) tsharp_match(f, tb2 + k, cs);
        }
    }
}

static void on_draw(pm4_frame_t *f)
{
    f->draws++;
    tgt_t t[9];
    uint32_t zfunc = 0; int zon = 0;
    int n = bound_targets(f, t, &zfunc, &zon);
    uint64_t set = 0x9E3779B97F4A7C15ull;
    for (int i = 0; i < n; i++) set = (set ^ (t[i].base + t[i].slot)) * 0x100000001B3ull;
    int newpass = set != f->last_set;
    if (newpass) { f->passes++; f->last_set = set; }
    int ncb = 0;
    for (int i = 0; i < n; i++) ncb += t[i].slot < 8;
    for (int i = 0; i < n; i++) {
        if (f->mode & PM4_FULL) {
            if (newpass) mark_alias_full(f, t[i].base, t[i].size, &t[i].d);
            pm4_rt_t *r = find_rt(f, &t[i], 1);
            if (r) {
                r->draws++;
                if (zon) r->zdraws++;
                r->last_draw = f->draws; r->last_pass = f->passes;
                if (!r->w || (t[i].w >= 320u && t[i].h >= 180u)) {
                    r->w = t[i].w; r->h = t[i].h;
                }
                r->slot_mask |= 1u << t[i].slot;
                r->flags |= t[i].flags;
                if (ncb > 1 && t[i].slot < 8) r->flags |= PM4RT_WITHMRT;
                if (t[i].d.depth) r->zfunc[zfunc]++;
                r->clear_word[0] = t[i].clr[0]; r->clear_word[1] = t[i].clr[1];
                r->flags &= ~PM4RT_ALIASED;      // written again: the alias (if any) was before this epoch
            }
        }
        if (f->trk_on) {
            pm4_track_t *k = &f->trk;
            if (pm4_desc_eq(&t[i].d, &f->trk_desc)) {
                uint32_t o = 0;
                while (o < f->ord_n && f->ord_addr[o] != t[i].base) o++;
                if (o == f->ord_n && f->ord_n < 8) f->ord_addr[f->ord_n++] = t[i].base;
                k->ord_seen = f->ord_n;
                if (o == f->trk_ordinal) {
                    if (k->aliased) { k->rewritten++; k->aliased = 0; k->al_entry = -1; }
                    k->written = 1; k->addr = t[i].base; k->size = t[i].size;
                    if (!k->w || (t[i].w >= 320u && t[i].h >= 180u)) { k->w = t[i].w; k->h = t[i].h; }
                    k->lw_entry = f->cur_entry;
                    k->lw_end = f->top_chained ? 0xFFFFFFFFu : f->cur_top_end;   // after a chain: no cut inside
                    continue;
                }
            }
            track_alias(f, t[i].base, t[i].size);
        }
    }
    if (f->mode & PM4_FULL) {
        scan_user_data(f, f->sh + SH_USER_DATA_PS_0, 0);
        scan_user_data(f, f->sh + SH_USER_DATA_VS_0, 0);
    }
}

static void on_dispatch(pm4_frame_t *f)
{
    f->dispatches++;
    if (f->mode & PM4_FULL) scan_user_data(f, f->sh + SH_COMPUTE_USER_DATA_0, 1);
}

static void on_dma(pm4_frame_t *f, const uint32_t *b, uint32_t nb)
{
    if (nb < 6) return;
    f->dma++;
    uint32_t dst_sel = (b[0] >> 20) & 3u, src_sel = (b[0] >> 29) & 3u;
    if (dst_sel != 0) return;                                    // 0 = DST_ADDR (memory); 1 = GDS
    uint64_t dst = (uint64_t)b[3] | ((uint64_t)(b[4] & 0xFFFFu) << 32);
    uint64_t nbytes = b[5] & 0x1FFFFFu;
    if (f->mode & PM4_FULL) {
        for (uint32_t i = 0; i < f->nrt; i++) {
            pm4_rt_t *r = &f->rt[i];
            if (!ranges_overlap(r->base, r->size, dst, nbytes)) continue;
            r->flags |= src_sel == 2 ? PM4RT_DMACLEAR : PM4RT_DMAWRITE;   // 2 = DATA (fill)
            if (r->draws) r->flags |= PM4RT_ALIASED;
        }
    }
    track_alias(f, dst, nbytes);
}

static void set_regs(uint32_t *regs, uint32_t off, const uint32_t *v, uint32_t n)
{
    for (uint32_t i = 0; i < n && off + i < 0x400u; i++) regs[off + i] = v[i];
}

static void on_load_regs(pm4_frame_t *f, uint32_t *regs, const uint32_t *b, uint32_t nb, int is_sh)
{
    f->loads++;
    if (nb < 4 || !f->readable) return;
    uint64_t base = (uint64_t)(b[0] & ~3u) | ((uint64_t)(b[1] & 0xFFFFu) << 32);
    if ((base & 3u) || base < 0x100000ull || base >= (1ull << 47)) return;
    for (uint32_t k = 2; k + 1 < nb; k += 2) {
        uint32_t off = b[k] & 0xFFFFu, cnt = b[k + 1] & 0x3FFFu;
        if (!cnt || cnt > 0x400u || off + cnt > 0x400u) continue;
        // GNM LOAD_CONTEXT_REG / LOAD_SH_REG: values sit at base + off*4 (or at base if single range at offset 0)
        const uint32_t *src = (const uint32_t *)(uintptr_t)(base + (uint64_t)off * 4u);
        if (!f->readable(src, (size_t)cnt * 4u, f->user)) {
            src = (const uint32_t *)(uintptr_t)base;
            if (k != 2 || !f->readable(src, (size_t)cnt * 4u, f->user)) continue;
        }
        set_regs(regs, off, src, cnt);
        if (is_sh) {
            for (int i = 0; i < PM4_NCS; i++)
                if (pm4_cs_reg_idx[i] >= off && pm4_cs_reg_idx[i] < off + cnt) f->cs_seen |= 1u << i;
        }
    }
}

static void on_marker(pm4_frame_t *f, const uint32_t *b, uint32_t nb, int color)
{
    if (!(f->mode & PM4_FULL) || f->nmark >= PM4_MAX_MARK) return;
    uint32_t first = color ? 2u : 1u;                          // colour marker: one colour dword before the text
    if (nb <= first) return;
    const char *s = (const char *)(b + first);
    size_t maxc = (size_t)(nb - first) * 4u, k = 0;
    char *o = f->marks[f->nmark];
    while (k < maxc && k + 1 < PM4_MAX_PASSNAME && s[k]) { char c = s[k]; o[k] = (c >= 32 && c < 127) ? c : '?'; k++; }
    o[k] = 0;
    if (k) f->cur_mark = ++f->nmark;
}

static int budget_hit(pm4_frame_t *f)
{
    if (f->dwords > f->max_dwords) { f->abort = PM4_ABORT_SIZE; return 1; }
    if (f->clock && f->budget_us && (f->packets & 511u) == 0) {
        uint64_t now = f->clock(f->user);   // walking time of this frame = earlier entries + this one so far
        if (f->us + (now - f->t_start) > f->budget_us) { f->abort = PM4_ABORT_TIME; return 1; }
    }
    return 0;
}

int pm4_walk_entry(pm4_frame_t *f, const uint32_t *dcb, uint32_t ndw, int32_t entry)
{
    if (f->abort) return f->abort;
    f->entries++;
    f->cur_entry = entry;
    f->t_start = f->clock ? f->clock(f->user) : 0;   // t_start = start of THIS entry, f->us = time of earlier ones
    f->top_chained = 0;
    if (entry >= 0 && entry < PM4_MAX_ENTRY) save_cs(f, &f->entry_cs[entry]);
    if (!dcb || !ndw) return PM4_OK;
    if (((uintptr_t)dcb & 3u) || !f->readable || !f->readable(dcb, (size_t)ndw * 4u, f->user)) {
        f->abort = PM4_ABORT_UNREADABLE; f->abort_at_dw = f->dwords; return f->abort;
    }
    struct { const uint32_t *base, *p, *end; } st[PM4_MAX_DEPTH + 1];
    int d = 0;
    uint32_t entry_ibs = 0;
    st[0].base = dcb; st[0].p = dcb; st[0].end = dcb + ndw;
    while (d >= 0) {
        if (st[d].p >= st[d].end) { d--; continue; }
        const uint32_t *p = st[d].p;
        uint32_t h = p[0], avail = (uint32_t)(st[d].end - p), type = PKT_TYPE(h), n;
        f->cur_depth = (uint32_t)d;
        if (type == 2) { st[d].p++; f->dwords++; continue; }              // type-2 filler: one dword
        if (type == 1) { f->abort = PM4_ABORT_BADPKT; break; }            // does not exist on GCN
        n = PKT_COUNT(h) + 2u;                                             // header + (count + 1) body dwords
        if (type == 3 && PKT_OP(h) == OP_NOP && PKT_COUNT(h) == 0x3FFFu) n = 1;   // 0xFFFF1000: 1-dword NOP
        if (n > avail) { f->abort = PM4_ABORT_BADPKT; break; }           // packet runs past its buffer
        if (d == 0 && !f->top_chained) { f->cur_top_off = (uint32_t)(p - dcb); f->cur_top_end = f->cur_top_off + n; }
        f->packets++;
        f->dwords += n;
        if (budget_hit(f)) break;
        st[d].p += n;
        const uint32_t *b = p + 1;
        uint32_t nb = n - 1;
        if (type == 0) {                                                   // register writes by absolute index
            f->type0++;
            uint32_t reg = h & 0xFFFFu;
            if (reg >= 0xA000u && reg < 0xA400u) set_regs(f->ctx, reg - 0xA000u, b, nb);
            else if (reg >= 0x2C00u && reg < 0x3000u) set_regs(f->sh, reg - 0x2C00u, b, nb);
            continue;
        }
        switch (PKT_OP(h)) {
        case OP_SET_CONTEXT_REG:
            if (nb >= 2) set_regs(f->ctx, b[0] & 0xFFFFu, b + 1, nb - 1);
            break;
        case OP_SET_SH_REG:
            if (nb >= 2) {
                uint32_t off = b[0] & 0xFFFFu;
                set_regs(f->sh, off, b + 1, nb - 1);
                for (int i = 0; i < PM4_NCS; i++)
                    if (pm4_cs_reg_idx[i] >= off && pm4_cs_reg_idx[i] < off + nb - 1) f->cs_seen |= 1u << i;
                if (f->pending_tsharp && (f->mode & PM4_FULL) && nb - 1 >= 8) tsharp_match(f, b + 1, off >= 0x200);
            }
            f->pending_tsharp = 0;
            break;
        case OP_CLEAR_STATE:
            f->clear_state++;
            memset(f->ctx, 0, sizeof(f->ctx));
            break;
        case OP_LOAD_CONTEXT_REG:
            on_load_regs(f, f->ctx, b, nb, 0);
            break;
        case OP_LOAD_SH_REG:
            on_load_regs(f, f->sh, b, nb, 1);
            break;
        case OP_LOAD_CONFIG_REG: case OP_SET_CONTEXT_REG_INDIRECT:
            f->loads++;                    // state loaded from memory: our shadow may be wrong (logged)
            break;
        case OP_DRAW_INDIRECT: case OP_DRAW_INDEX_INDIRECT: case OP_DRAW_INDEX_2: case OP_DRAW_INDIRECT_MULTI:
        case OP_DRAW_INDEX_AUTO: case OP_DRAW_INDEX_IMMD: case OP_DRAW_INDEX_MULTI_AUTO: case OP_DRAW_INDEX_OFFSET_2:
        case OP_DRAW_INDEX_INDIRECT_MULTI: case OP_DRAW_INDIRECT_COUNT_MULTI: case OP_DRAW_INDEX_INDIRECT_COUNT_MULTI:
            on_draw(f);
            break;
        case OP_DISPATCH_DIRECT: case OP_DISPATCH_INDIRECT:
            on_dispatch(f);
            break;
        case OP_DMA_DATA:
            on_dma(f, b, nb);
            break;
        case OP_NOP:
            if (nb >= 1) {
                if (b[0] == NOP_SET_TSHARP_UDATA) f->pending_tsharp = 1;
                else if (b[0] == NOP_MARKER_PUSH) on_marker(f, b, nb, 0);
                else if (b[0] == NOP_COLOR_MARKER_PUSH) on_marker(f, b, nb, 1);
            }
            break;
        case OP_INDIRECT_BUFFER_CONST:
            f->ce_entries++;               // constant-engine IB: no render target state in it
            break;
        case OP_INDIRECT_BUFFER: {
            if (nb < 3) break;
            uint64_t a = (uint64_t)(b[0] & ~3u) | ((uint64_t)(b[1] & 0xFFFFu) << 32);   // IB_BASE_LO 31:2, HI 15:0
            uint32_t sz = b[2] & 0xFFFFFu, chain = (b[2] >> 20) & 1u;                    // IB_SIZE 19:0, CHAIN bit 20
            f->ibs++;
            if (!sz) break;
            if ((a & 3u) || a < 0x100000ull || a >= (1ull << 47) || ++entry_ibs > 4096u) {
                f->ib_unreadable++;
                if (f->trk_on) { f->abort = PM4_ABORT_UNREADABLE; f->abort_at_dw = f->dwords; }
                break;
            }
            const uint32_t *ib = (const uint32_t *)(uintptr_t)a;
            int cyclic = 0;
            for (int k = 0; k <= d; k++) if (st[k].base == ib) { cyclic = 1; break; }
            if (cyclic || !f->readable || !f->readable(ib, (size_t)sz * 4u, f->user)) {
                f->ib_unreadable++;
                if (f->trk_on) { f->abort = PM4_ABORT_UNREADABLE; f->abort_at_dw = f->dwords; }   // could hide a write
                break;
            }
            if (chain) {                   // the rest of this buffer is not executed: continue in the target
                if (d == 0) { f->top_chained = 1; f->top_chain_off = f->cur_top_off; }
                st[d].base = ib; st[d].p = ib; st[d].end = ib + sz;
            } else if (d < PM4_MAX_DEPTH) {
                d++; st[d].base = ib; st[d].p = ib; st[d].end = ib + sz;
                if ((uint32_t)d > f->depth_max) f->depth_max = (uint32_t)d;
            } else {
                f->ib_unreadable++;        // deeper than we follow: treat as unknown
                if (f->trk_on) { f->abort = PM4_ABORT_UNREADABLE; f->abort_at_dw = f->dwords; }
            }
            break;
        }
        default:
            break;
        }
        if (f->abort) break;
    }
    if (f->clock) f->us += (uint32_t)(f->clock(f->user) - f->t_start);
    if (f->abort && !f->abort_at_dw) f->abort_at_dw = f->dwords;
    return f->abort;
}

int pm4_rt_game_tsharp(const pm4_rt_t *r, uint32_t *tile, uint32_t *pitch)
{
    if (!r->samp_draw) return 0;
    const uint32_t *t = r->samp_t;
    uint32_t type = t[3] >> 28, dfmt = (t[1] >> 20) & 0x3Fu;
    if (type != 9) return 0;
    int fmt_ok = 0;
    if (r->d.depth) {
        if (r->d.fmt == 3) fmt_ok = (dfmt == 4u || dfmt == 22u);        // Z32F: 32 or X24_8_32_FLOAT
        else if (r->d.fmt == 2) fmt_ok = (dfmt == 20u || dfmt == 21u);  // Z24: 8_24 or 24_8
        else if (r->d.fmt == 1) fmt_ok = (dfmt == 2u);                  // Z16: 16
    } else {
        fmt_ok = (r->d.fmt != 0u && dfmt == r->d.fmt);
    }
    if (!fmt_ok) return 0;
    uint32_t p = ((t[4] >> 13) & 0x3FFFu) + 1u, w = (t[2] & 0x3FFFu) + 1u;
    if (p < w || p > 16384u) return 0;
    *tile = (t[3] >> 20) & 0x1Fu;
    *pitch = p;
    return 1;
}

// ---------------------------------------------------------------------------------------------------------------
// Candidate ranking. Thresholds and what breaks them: docs/VELOCITY_RU.md, section B.
static int near_ratio(uint32_t a, uint32_t b, uint32_t num, uint32_t den)   // a ~ b * num / den within 3 %
{
    uint64_t t = (uint64_t)b * num, x = (uint64_t)a * den;
    return x * 100u >= t * 97u && x * 100u <= t * 103u;
}

void pm4_rank(pm4_frame_t *f, uint32_t sw, uint32_t sh)
{
    uint32_t tot = f->draws ? f->draws : 1u;
    for (uint32_t i = 0; i < f->nrt; i++) {
        pm4_rt_t *r = &f->rt[i];
        int s = 0;
        int full = near_ratio(r->w, sw, 1, 1) && near_ratio(r->h, sh, 1, 1);
        int half = near_ratio(r->w, sw, 1, 2) && near_ratio(r->h, sh, 1, 2);
        int ar16_9 = near_ratio(r->w * 9u, r->h * 16u, 1, 1) || near_ratio(r->w * sh, r->h * sw, 1, 1);
        // Dynamic resolution: 50%..100% of display resolution, 1280x720..3840x2160 16:9 (including PS4 Pro neo=1
        // supersampling or sub-4K DRS), or horizontal DRS / checkerboard (50%..100% width at full height).
        int dyn_hi = !full && !half && ar16_9 &&
                     ((r->w * 100u >= sw * 63u && r->w <= sw && r->h * 100u >= sh * 63u && r->h <= sh) ||
                      (r->w >= 1248u && r->w <= 3840u && r->h >= 702u && r->h <= 2160u));
        int dyn_lo = !full && !half && !dyn_hi &&
                     ((ar16_9 && r->w * 2u >= sw && r->w <= sw && r->h * 2u >= sh && r->h <= sh) ||
                      (near_ratio(r->h, sh, 1, 1) && r->w * 2u >= sw && r->w <= sw));
        int dyn = dyn_hi || dyn_lo;
        if (r->d.depth) {
            if (full) s += 40; else if (dyn) s += 25; else if (half) s += 5; else s -= 40;
            if (r->d.fmt == 3) s += 10;
            else if (r->d.fmt == 2) s += 8;                             // Z24 depth buffer
            if (r->draws >= 50) s += 20; else if (r->draws >= 10) s += 10;
            if (r->flags & PM4RT_SAMPLED) s += 10;
            if (r->flags & PM4RT_MSAA) s -= 50;
            if (r->first_draw * 2u > tot) s -= 10;                      // depth of a late pass (shadow / UI)
        } else {
            if (r->flags & PM4RT_DISPLAY) { r->score = -1000; continue; }
            if (r->nch == 2) s += 40;
            else if (r->nch == 4 && r->bpp == 8) s += 15;               // RGBA16: velocity in .xy or .zw
            else if (r->nch == 1) s -= 20;
            else if (r->nch == 4 && r->bpp == 4) s -= 5;                // RGBA8 G-buffer (packed velocity: rare)
            if (r->d.num == 7 || r->d.num == 1) s += 10; else if (r->d.num == 0) s += 5;
            else if (r->d.num == 6) s -= 30; else s -= 5;
            if (full) s += 25; else if (dyn_hi) s += 20; else if (half || dyn_lo) s += 15; else s -= 30;
            if (r->draws >= 100) s += 15; else if (r->draws >= 20) s += 10; else if (r->draws <= 2) s -= 5;
            if (r->zdraws * 2u >= r->draws && r->draws) s += 15;         // rasterised geometry with depth test
            if (r->flags & PM4RT_WITHMRT) s += 5;
            if (r->flags & PM4RT_SAMPLED) s += 10;                       // read later (TAA / motion blur)
            if (r->first_draw * 2u < tot) s += 5;                        // written in the first half of the frame
            if (r->flags & PM4RT_MSAA) s -= 50;
            if (r->bpp >= 16) s -= 10;
        }
        r->score = s;
    }
}
