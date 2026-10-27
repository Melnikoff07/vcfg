// Descriptor helpers and the mode 8 table / work memory layout. Shared by main.c and the x86 test harness
// (tools/emu), so the harness checks exactly the descriptors the plugin builds.
#pragma once
#include "vov.h"
#include <stdint.h>

// Data format of the game's scanout buffers in every T#: 10 = 8_8_8_8 (0x8000xxxx), 9 = 2_10_10_10 (0x88xxxxxx).
// The passes read and write floats, so only this field changes; both are 4 bytes per pixel.
//
// PS4 Pro (Neo, 16 pipes) vs Base (Liverpool, 8 pipes) T# macro-tile mode:
// Disassembly of libSceGnm (sceGnmCreateTexture @ +0x17be5: cmpl $1, 0x28(%r14); sete %dl; shlq $0x38, %rdx -> qword +0x14;
// sceGnmGetTextureSizeAlign @ +0x17d71: movzbl 0x1b(%r14), %ecx; andl $1, %ecx) and shadPS4.exe (@ 0x7000010515fb)
// proves that m_minGpuMode == kGpuModeNeo (alt_tile_mode) is stored at bit 24 of t[6] (0x01000000, byte 0x1b bit 0):
//   t[6] bits [11:0]  = min_lod_warn (12b)
//   t[6] bits [19:12] = counter_bank_id (8b)
//   t[6] bit  [20]    = lod_hdw_cnt_en (1b)
//   t[6] bit  [21]    = compression_en (1b)
//   t[6] bit  [22]    = alpha_is_on_msb (1b)
//   t[6] bit  [23]    = color_transform (1b)
//   t[6] bit  [24]    = alt_tile_mode (1b: 1 = Neo P16 macro-tile table, 0 = Base P8_32x32_8x16 table)
// Without bit 24 of t[6] set on PS4 Pro, tidx = 10 (kTileModeDisplay_2dThin) uses the 8-pipe macro-tile table while
// the game and DCE use the 16-pipe macro-tile table, causing 128-px vertical column striping.
#define GNM_T6_ALT_TILE_MODE_NEO (1u << 24)
static uint32_t g_t_dfmt = 10u;
static int g_t_is_neo = 0;
static int g_t6_bit_override = -1; // optional INI override (0..31); -1 = default bit 24 (GNM_T6_ALT_TILE_MODE_NEO)
static int g_t_prev_half16 = 0;      // 1: T#prev is a 50%-footprint 16-bpp (2 bytes/px) linear history surface (dfmt=16 5_6_5, tidx=8)
static int g_pcopy_dst_half16 = 0;   // 1: pcopy8 T#out is a 50%-footprint 16-bpp (2 bytes/px) linear history surface
static int g_pcopy_ycocg_mode = 0;   // pcopy8 mode: 0 = direct copy, 1 = YCoCg-R 4:2:0 16b encode, 2 = YCoCg-R 4:2:0 16b decode

#ifndef VCFG_PBUF_STATE_DEFINED
#define VCFG_PBUF_STATE_DEFINED
static void *g_pbuf = 0;
static long g_pbuf_phys = 0;
static size_t g_pbuf_sz = 0;
static int g_pbuf_ycocg = 0;
static int g_pbuf_valid = 0;
static uint32_t g_pbuf_last_seq = 0;
static uint64_t g_pbuf_prev_va = 0;
static uint32_t g_flip_ring_hist[8] __attribute__((unused));
static uint32_t g_flip_ring_cnt __attribute__((unused)) = 0;
#endif

static inline void fg8_resolve_2buf_history(uint64_t *P_io, uint64_t C, uint64_t *copy_dst_io, uint32_t *copy_idx_io, uint32_t seq)
{
    uint64_t pbuf_va = (uint64_t)(uintptr_t)g_pbuf;
    g_t_prev_half16 = 0;
    g_pcopy_dst_half16 = 0;
    if (!pbuf_va && *copy_dst_io == 0ull) return;
    if (g_pbuf_valid && g_pbuf_prev_va != 0ull && g_pbuf_prev_va != C && (g_pbuf_last_seq + 1u == seq)) {
        *P_io = g_pbuf_prev_va;
        if (pbuf_va != 0ull && *P_io == pbuf_va && g_pbuf_ycocg) {
            g_t_prev_half16 = 1;
        }
    }
    if (*copy_dst_io == 0ull && pbuf_va != 0ull) {
        *copy_dst_io = pbuf_va;
        *copy_idx_io = 0xFFFFu;
    }
    if (*copy_dst_io != 0ull) {
        if (pbuf_va != 0ull && *copy_dst_io == pbuf_va && g_pbuf_ycocg) {
            g_pcopy_dst_half16 = 1;
        }
        g_pbuf_prev_va = *copy_dst_io;
        g_pbuf_valid = 1;
        g_pbuf_last_seq = seq;
    }
}

static inline void make_t_ex(uint32_t *t, uint64_t a, uint32_t w, uint32_t h, uint32_t pitch, uint32_t tidx, int is_neo)
{
    uint32_t t6 = 0u;
    if (is_neo && tidx != 8u) {
        t6 = (g_t6_bit_override >= 0 && g_t6_bit_override <= 31) ? (1u << g_t6_bit_override) : GNM_T6_ALT_TILE_MODE_NEO;
    }
    t[0] = (uint32_t)(a >> 8);
    t[1] = (uint32_t)((a >> 40) & 0xFFu) | (g_t_dfmt << 20);
    t[2] = (w - 1u) | ((h - 1u) << 14);
    t[3] = 4u | (5u << 3) | (6u << 6) | (7u << 9) | (tidx << 20) | (9u << 28);
    t[4] = (pitch - 1u) << 13;
    t[5] = 0;
    t[6] = t6;
    t[7] = 0;
}
static inline void make_t(uint32_t *t, uint64_t a, uint32_t w, uint32_t h, uint32_t pitch, uint32_t tidx)
{
    make_t_ex(t, a, w, h, pitch, tidx, g_t_is_neo);
}
static inline void make_t_pbuf(uint32_t *t, uint64_t a, uint32_t w, uint32_t h, uint32_t pitch, uint32_t tidx, int is_half_16b)
{
    if (!is_half_16b) {
        make_t(t, a, w, h, pitch, tidx);
        return;
    }
    t[0] = (uint32_t)(a >> 8);
    t[1] = (uint32_t)((a >> 40) & 0xFFu) | (16u << 20);
    t[2] = (w - 1u) | ((h - 1u) << 14);
    t[3] = 4u | (5u << 3) | (6u << 6) | (1u << 9) | (8u << 20) | (9u << 28);
    t[4] = (pitch - 1u) << 13;
    t[5] = 0;
    t[6] = 0;
    t[7] = 0;
}
static inline void make_v(uint32_t *v, uint64_t a, uint32_t nbytes)
{
    v[0] = (uint32_t)a; v[1] = (uint32_t)(a >> 32) & 0xFFFFu; v[2] = nbytes; v[3] = 0x00027FAC;
}

static inline void fill_pcopy8_table(uint32_t *tb3, uint64_t C, uint64_t copy_dst, uint32_t w, uint32_t h,
                                     uint32_t pitch, uint32_t tidx, int is_half_16b, int ycocg_mode)
{
    for (int i = 0; i < 32; i++) tb3[i] = 0u;
    make_t(tb3 + 8, C, w, h, pitch, tidx);
    make_t_pbuf(tb3 + 16, copy_dst, w, h, pitch, tidx, is_half_16b);
    if (is_half_16b || ycocg_mode > 0) {
        uint32_t eff_pitch = (pitch > w) ? pitch : w;
        uint32_t nbytes = (eff_pitch * h * 2u + 255u) & ~255u;
        make_v(tb3 + 24, copy_dst, nbytes);
        tb3[28] = (uint32_t)ycocg_mode;
        tb3[29] = eff_pitch;
        tb3[30] = w;
        tb3[31] = h;
    }
}

// YCoCg-R 4:2:0 16-bit (2 bytes/pixel = 4.15 MB at 1080p) reference helpers matching pcopy8.ll Mode 1 / Mode 2:
// Packs a horizontal pixel pair (2k, y) and (2k+1, y) with 2x2 box-filtered Co/Cg into a single uint32_t:
//   bits  [7:0]  = Y0  (full-res luma of pixel 2k)
//   bits [15:8]  = Y1  (full-res luma of pixel 2k+1)
//   bits [23:16] = Co  (2x2 chroma orange, biased by +128)
//   bits [31:24] = Cg  (2x2 chroma green, biased by +128)
static inline uint8_t ycocg_clamp_u8(int32_t v)
{
    return (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v));
}
static inline void ycocg_rgb_to_ycocg(uint32_t rgba, int32_t *y_out, int32_t *co_out, int32_t *cg_out)
{
    int32_t r = (int32_t)(rgba & 0xFFu);
    int32_t g = (int32_t)((rgba >> 8) & 0xFFu);
    int32_t b = (int32_t)((rgba >> 16) & 0xFFu);
    int32_t co_h = (r - b) >> 1;
    int32_t t    = b + co_h;
    int32_t cg_h = (g - t) >> 1;
    int32_t y    = t + cg_h;
    *y_out  = (int32_t)ycocg_clamp_u8(y);
    *co_out = (int32_t)ycocg_clamp_u8(co_h + 128);
    *cg_out = (int32_t)ycocg_clamp_u8(cg_h + 128);
}
static inline void ycocg420_compress_frame(const uint32_t *rgba_in, uint32_t *ycocg_out, uint32_t w, uint32_t h, uint32_t pitch)
{
    uint32_t half_w = w >> 1;
    for (uint32_t y = 0; y + 1 < h; y += 2) {
        const uint32_t *row0 = rgba_in + (size_t)y * pitch;
        const uint32_t *row1 = rgba_in + (size_t)(y + 1u) * pitch;
        uint32_t *dst0 = ycocg_out + (size_t)y * half_w;
        uint32_t *dst1 = ycocg_out + (size_t)(y + 1u) * half_w;
        for (uint32_t x = 0; x + 1 < w; x += 2) {
            int32_t y00, co00, cg00, y10, co10, cg10, y01, co01, cg01, y11, co11, cg11;
            ycocg_rgb_to_ycocg(row0[x],      &y00, &co00, &cg00);
            ycocg_rgb_to_ycocg(row0[x + 1u], &y10, &co10, &cg10);
            ycocg_rgb_to_ycocg(row1[x],      &y01, &co01, &cg01);
            ycocg_rgb_to_ycocg(row1[x + 1u], &y11, &co11, &cg11);
            uint32_t co_avg = (uint32_t)ycocg_clamp_u8((co00 + co10 + co01 + co11 + 2) >> 2);
            uint32_t cg_avg = (uint32_t)ycocg_clamp_u8((cg00 + cg10 + cg01 + cg11 + 2) >> 2);
            uint32_t chroma = (co_avg << 16) | (cg_avg << 24);
            dst0[x >> 1] = (uint32_t)y00 | ((uint32_t)y10 << 8) | chroma;
            dst1[x >> 1] = (uint32_t)y01 | ((uint32_t)y11 << 8) | chroma;
        }
    }
}
static inline uint32_t ycocg420_unpack_pixel(uint32_t packed_pair, uint32_t x_odd)
{
    int32_t y  = (int32_t)((x_odd ? (packed_pair >> 8) : packed_pair) & 0xFFu);
    int32_t co = (int32_t)((packed_pair >> 16) & 0xFFu) - 128;
    int32_t cg = (int32_t)((packed_pair >> 24) & 0xFFu) - 128;
    int32_t t  = y - cg;
    uint32_t g = (uint32_t)ycocg_clamp_u8(y + cg);
    uint32_t b = (uint32_t)ycocg_clamp_u8(t - co);
    uint32_t r = (uint32_t)ycocg_clamp_u8(t + co);
    return r | (g << 8) | (b << 16) | 0xFF000000u;
}
static inline void ycocg420_decompress_frame(const uint32_t *ycocg_in, uint32_t *rgba_out, uint32_t w, uint32_t h, uint32_t pitch)
{
    uint32_t half_w = w >> 1;
    for (uint32_t y = 0; y < h; y++) {
        const uint32_t *src = ycocg_in + (size_t)y * half_w;
        uint32_t *dst = rgba_out + (size_t)y * pitch;
        for (uint32_t x = 0; x + 1 < w; x += 2) {
            uint32_t pk = src[x >> 1];
            dst[x]      = ycocg420_unpack_pixel(pk, 0u);
            dst[x + 1u] = ycocg420_unpack_pixel(pk, 1u);
        }
    }
}

// work memory layout of mode 8 (GPU only; WC_GARLIC when it could be allocated so, see plugin_load)
#define W8_L1A 0x000000u   // 960x540 u8 luma, ping-pong A/B by frame parity
#define W8_L1B 0x07F000u
#define W8_L2A 0x0FE000u   // 480x270
#define W8_L2B 0x11E000u
#define W8_L3A 0x13E000u   // 240x135
#define W8_L3B 0x146000u
#define W8_V3  0x14E000u   // 30x17 packed int16 x2 (L3 px); 8.5: +2048: u8[60x34] anchor history (fgs8)
#define W8_V2  0x14F000u   // 60x34 packed int16 x2 (L2 px)
#define W8_FA  0x151000u   // 120x68 float2, full-res px, mid grid (ping-pong: current / previous frame)
#define W8_FB  0x161000u
#define W8_MISC 0x171000u  // [0] scene-change count, [1] t, [2..3] luma sums by parity, [4..5] dominant motion (gm8),
                           // [6] foreground L2 blocks (fgs8), [7] fade decision (me1), [8..9] scene-change count of
                           // the pair that wrote F of that parity (phase8: F prev cleared after a scene change)
#define W8_S2  0x172000u   // 60x34 u32: L2 SAD of the best | SAD of the zero vector << 16 (me2)
#define W8_GK  0x174000u   // 120x68 u32: foreground splat keys (fgs8; zeroed by pyr8)
#define W8_FT  0x17C000u   // 120x68 float2: raw me1 field, filtered by mf8 into F cur (8.3)
#define W8_END 0x18C000u

// consts.flags (tb[30], offset +0x78) bit 12: Warm Single-Frame Pyramid Mode in pyr8.ll & me3.ll.
// When set, V#L1prev (+0x90), V#L2prev (+0xB0), V#L3prev (+0xD0), and W8_MISC[2 + (r^1)] are reused directly
// from the previous frame's cur pyramid buffers via ping-pong descriptor swap (0 B extra DirectMemory),
// and pyr8.ll skips all T#prev texture fetches and V#L1prev..L3prev buffer stores (-50% pyr8 bandwidth & ALU).
#define FG8_FLAG_PYR_WARM 0x1000u

// table read by the mode 8 shaders (offsets are hard-coded in shaders/*8.ll and me*.ll)
#define WA(off) ((uint64_t)(uintptr_t)(work + (off)))
static inline void fill_table8_wh(uint32_t *tb, uint64_t P, uint64_t C, uint64_t M, uint32_t w, uint32_t h,
                                  uint32_t pitch, uint32_t tidx, uint32_t r, uint32_t mid_idx, uint32_t flags,
                                  uint8_t *work, volatile uint32_t *mon, volatile uint32_t *stats)
{
    make_t_pbuf(tb + 0, P, w, h, pitch, tidx, g_t_prev_half16);      // +0x00 T#prev
    make_t(tb + 8, C, w, h, pitch, tidx);                            // +0x20 T#cur
    make_t(tb + 16, M, w, h, pitch, tidx);                           // +0x40 T#out
    // +0x60 S#: clamp-to-edge x/y/z, FORCE_UNNORMALIZED, bilinear mag/min, no mips
    tb[24] = 2u | (2u << 3) | (2u << 6) | (1u << 15); tb[25] = 0; tb[26] = (1u << 20) | (1u << 22); tb[27] = 0;
    // +0x70 consts; tb[29] holds mid_idx | (t_override_q16 << 16); flags: bit 0 adaptive phase, bit 1 FG off,
    // bit 2 debug marker, bit 3 'k', bit 4 'g', bit 5 'L', bit 6 'T', bit 7 'M', bit 8 'G', bit 9 'P', bit 10 'S', bit 11 'b',
    // bit 12 FG8_FLAG_PYR_WARM (single-frame warm pyramid fast path)
    tb[28] = r; tb[29] = mid_idx; tb[30] = flags; tb[31] = g_hud_packed;
    if (work) {
        // buffers get 16 bytes of slack: me*.ll read 12 bytes at an aligned-down row start
        make_v(tb + 32, WA(r ? W8_L1B : W8_L1A), 960 * 540 + 16);       // +0x80 L1 cur
        make_v(tb + 36, WA(r ? W8_L1A : W8_L1B), 960 * 540 + 16);       // +0x90 L1 prev
        make_v(tb + 40, WA(r ? W8_L2B : W8_L2A), 480 * 270 + 16);       // +0xA0 L2 cur
        make_v(tb + 44, WA(r ? W8_L2A : W8_L2B), 480 * 270 + 16);       // +0xB0 L2 prev
        make_v(tb + 48, WA(r ? W8_L3B : W8_L3A), 240 * 135 + 16);       // +0xC0 L3 cur
        make_v(tb + 52, WA(r ? W8_L3A : W8_L3B), 240 * 135 + 16);       // +0xD0 L3 prev
        make_v(tb + 56, WA(W8_V3), 4096);                               // +0xE0 V3 (2040 B) + anchor history at +2048 (8.5)
        make_v(tb + 60, WA(W8_V2), 60 * 34 * 4);                        // +0xF0 V2
        make_v(tb + 64, WA(r ? W8_FB : W8_FA), 120 * 68 * 8);           // +0x100 F cur
        make_v(tb + 68, WA(r ? W8_FA : W8_FB), 120 * 68 * 8);           // +0x110 F prev
        make_v(tb + 72, WA(W8_MISC), 48);                               // +0x120 misc
        make_v(tb + 80, WA(W8_S2), 60 * 34 * 4);                        // +0x140 S2
        make_v(tb + 84, WA(W8_GK), 120 * 68 * 4);                       // +0x150 GK
        make_v(tb + 96, WA(W8_FT), 120 * 68 * 8);                       // +0x180 FT: raw me1 field (me1 -> mf8)
    }
    if (mon)   make_v(tb + 76, (uint64_t)(uintptr_t)mon, 16);           // +0x130 monitor values (CPU written)
    if (stats) make_v(tb + 92, (uint64_t)(uintptr_t)stats, 32);         // +0x170 frame statistics (phase8 -> CPU)
}

static inline void fill_table8(uint32_t *tb, uint64_t P, uint64_t C, uint64_t M, uint32_t pitch, uint32_t tidx, uint32_t r,
                               uint32_t mid_idx, uint32_t flags, uint8_t *work, volatile uint32_t *mon,
                               volatile uint32_t *stats)
{
    fill_table8_wh(tb, P, C, M, 1920, 1080, pitch, tidx, r, mid_idx, flags, work, mon, stats);
}
#undef WA

// ---- v1.15/v1.16: RCAS + Enhance AA + OSD overlay + FG Fast second mid-frame slot layout ----
#define FONT8_OFF    0x0600u   // in g_gpu (after rect_code at 0x0400..0x053F, below MON_OFF 0x0E00): 96 * 8 = 768 B
#define SLOT_TB2_OFF 0x0000u   // in each slotb (0x000..0x22F): second descriptor table for FG Fast 3-step mid2
#define SLOT_FB2_OFF 0x0280u   // in each slotb (0x280..0x37F): 64-dword flip command buffer for FG Fast mid2
#define OSD_SLOT_OFF 0x0400u   // in each slotb (0x400..0x63F): 64 B styles (16 rows) + 512 B chars (16x32)

static const uint32_t g_font8x8[192] = {
    0x00000000u, 0x00000000u, /* 0x20 ' ' */ 0x183C3C18u, 0x00180018u, /* 0x21 '!' */
    0x00246666u, 0x00000000u, /* 0x22 '"' */ 0x7E363600u, 0x0036367Eu, /* 0x23 '#' */
    0x307C6018u, 0x00183E06u, /* 0x24 '$' */ 0x180CC662u, 0x00466330u, /* 0x25 '%' */
    0x386C3800u, 0x003B6E76u, /* 0x26 '&' */ 0x00301818u, 0x00000000u, /* 0x27 ''' */
    0x30180C06u, 0x00060C18u, /* 0x28 '(' */ 0x0C183060u, 0x00603018u, /* 0x29 ')' */
    0xFF3C6600u, 0x0000663Cu, /* 0x2A '*' */ 0x7E181800u, 0x00001818u, /* 0x2B '+' */
    0x00000000u, 0x30181800u, /* 0x2C ',' */ 0x7E000000u, 0x00000000u, /* 0x2D '-' */
    0x00000000u, 0x00181800u, /* 0x2E '.' */ 0x180C0603u, 0x00C06030u, /* 0x2F '/' */
    0x766E663Cu, 0x003C667Eu, /* 0x30 '0' */ 0x18183818u, 0x007E1818u, /* 0x31 '1' */
    0x0C06663Cu, 0x007E6030u, /* 0x32 '2' */ 0x1C06663Cu, 0x003C6606u, /* 0x33 '3' */
    0x6C3C1C0Cu, 0x000C7E6Cu, /* 0x34 '4' */ 0x7C60607Eu, 0x003C6606u, /* 0x35 '5' */
    0x7C60603Cu, 0x003C6666u, /* 0x36 '6' */ 0x180C667Eu, 0x00303030u, /* 0x37 '7' */
    0x3C66663Cu, 0x003C6666u, /* 0x38 '8' */ 0x3E66663Cu, 0x003C0606u, /* 0x39 '9' */
    0x00181800u, 0x00181800u, /* 0x3A ':' */ 0x00181800u, 0x30181800u, /* 0x3B ';' */
    0x6030180Cu, 0x000C1830u, /* 0x3C '<' */ 0x007E0000u, 0x00007E00u, /* 0x3D '=' */
    0x060C1830u, 0x0030180Cu, /* 0x3E '>' */ 0x0C06663Cu, 0x00180018u, /* 0x3F '?' */
    0x6E66663Cu, 0x003C606Eu, /* 0x40 '@' */ 0x66663C18u, 0x0066667Eu, /* 0x41 'A' */
    0x7C66667Cu, 0x007C6666u, /* 0x42 'B' */ 0x6060663Cu, 0x003C6660u, /* 0x43 'C' */
    0x66666C78u, 0x00786C66u, /* 0x44 'D' */ 0x7860607Eu, 0x007E6060u, /* 0x45 'E' */
    0x7860607Eu, 0x00606060u, /* 0x46 'F' */ 0x6060663Cu, 0x003E666Eu, /* 0x47 'G' */
    0x7E666666u, 0x00666666u, /* 0x48 'H' */ 0x1818183Cu, 0x003C1818u, /* 0x49 'I' */
    0x0606061Eu, 0x003C6666u, /* 0x4A 'J' */ 0x786C6666u, 0x0066666Cu, /* 0x4B 'K' */
    0x60606060u, 0x007E6060u, /* 0x4C 'L' */ 0xDBFFFFC3u, 0x00C3C3C3u, /* 0x4D 'M' */
    0x7E766666u, 0x00666E7Eu, /* 0x4E 'N' */ 0x6666663Cu, 0x003C6666u, /* 0x4F 'O' */
    0x7C66667Cu, 0x00606060u, /* 0x50 'P' */ 0x6666663Cu, 0x003D666Au, /* 0x51 'Q' */
    0x7C66667Cu, 0x0066666Cu, /* 0x52 'R' */ 0x3C60663Cu, 0x003C6606u, /* 0x53 'S' */
    0x1818187Eu, 0x00181818u, /* 0x54 'T' */ 0x66666666u, 0x003C6666u, /* 0x55 'U' */
    0x66666666u, 0x00183C66u, /* 0x56 'V' */ 0xDBC3C3C3u, 0x0066FFDBu, /* 0x57 'W' */
    0x183C6666u, 0x0066663Cu, /* 0x58 'X' */ 0x183C6666u, 0x00181818u, /* 0x59 'Y' */
    0x180C067Eu, 0x007E6030u, /* 0x5A 'Z' */ 0x3030303Cu, 0x003C3030u, /* 0x5B '[' */
    0x183060C0u, 0x0003060Cu, /* 0x5C '\' */ 0x0C0C0C3Cu, 0x003C0C0Cu, /* 0x5D ']' */
    0x00663C18u, 0x00000000u, /* 0x5E '^' */ 0x00000000u, 0xFF000000u, /* 0x5F '_' */
    0x000C1830u, 0x00000000u, /* 0x60 '`' */ 0x3E063C00u, 0x003E6666u, /* 0x61 'a' */
    0x667C6060u, 0x007C6666u, /* 0x62 'b' */ 0x60663C00u, 0x003C6660u, /* 0x63 'c' */
    0x663E0606u, 0x003E6666u, /* 0x64 'd' */ 0x7E663C00u, 0x003C6060u, /* 0x65 'e' */
    0x7C30301Cu, 0x00303030u, /* 0x66 'f' */ 0x66663E00u, 0x7C063E66u, /* 0x67 'g' */
    0x667C6060u, 0x00666666u, /* 0x68 'h' */ 0x18380018u, 0x003C1818u, /* 0x69 'i' */
    0x0C1C000Cu, 0x386C0C0Cu, /* 0x6A 'j' */ 0x6C666060u, 0x00666C78u, /* 0x6B 'k' */
    0x18181838u, 0x003C1818u, /* 0x6C 'l' */ 0xFFDB6600u, 0x00C3C3DBu, /* 0x6D 'm' */
    0x66667C00u, 0x00666666u, /* 0x6E 'n' */ 0x66663C00u, 0x003C6666u, /* 0x6F 'o' */
    0x66667C00u, 0x60607C66u, /* 0x70 'p' */ 0x66663E00u, 0x06063E66u, /* 0x71 'q' */
    0x60665C00u, 0x00606060u, /* 0x72 'r' */ 0x3C603E00u, 0x007C0606u, /* 0x73 's' */
    0x30307C30u, 0x001C3030u, /* 0x74 't' */ 0x66666600u, 0x003E6666u, /* 0x75 'u' */
    0x66666600u, 0x00183C66u, /* 0x76 'v' */ 0xDBC3C300u, 0x0066FFDBu, /* 0x77 'w' */
    0x3C666600u, 0x0066663Cu, /* 0x78 'x' */ 0x66666600u, 0x7C063E66u, /* 0x79 'y' */
    0x180C7E00u, 0x007E3018u, /* 0x7A 'z' */ 0x3018180Eu, 0x000E1818u, /* 0x7B '{' */
    0x00181818u, 0x00181818u, /* 0x7C '|' */ 0x0C181870u, 0x00701818u, /* 0x7D '}' */
    0x006CD870u, 0x00000000u, /* 0x7E '~' */ 0x7E7E7E7Eu, 0x007E7E7Eu  /* 0x7F block */
};

static inline void fill_table8_ext(uint32_t *tb, uint8_t *gpu, uint8_t *slotb,
                                   uint32_t rcas_con_bits, uint32_t rcas_flags,
                                   uint32_t osd_xy, uint32_t osd_wh)
{
    (void)gpu; (void)slotb;
    make_v(tb + 128, (uint64_t)(uintptr_t)vov_pal_ptr(), VOV_PAL_SZ);          // +0x200 V#palette (overlay/vov.c)
    make_v(tb + 132, (uint64_t)(uintptr_t)vov_pix_ptr(), VOV_PIX_SZ);          // +0x210 V#pixels
    tb[136] = rcas_con_bits;                                                  // +0x220 rcas_con (float bits)
    tb[137] = rcas_flags;                                                     // +0x224 rcas_flags (bit 0=mid, 1=cur, 2..3=enh_mode)
    tb[138] = osd_xy;                                                         // +0x228 osd_xy (x0 | y0 << 16)
    tb[139] = osd_wh;                                                         // +0x22C osd_wh (cols | rows<<8 | alpha<<16 | flags<<24)
}
