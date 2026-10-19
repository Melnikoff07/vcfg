// VCFG in-game overlay: the menu panel and the corner FPS counter in the launcher design.
// The CPU draws into an 8-bit palette bitmap; osd8.ll composites it onto the frame.
// Freestanding (no libc), shared by both engines and the PC preview.
#pragma once
#include <stdint.h>
#include <stddef.h>

#define VOV_W        512u                 // panel width in px (multiple of 16)
#define VOV_MAX_H    720u                 // max panel height in px
#define VOV_PIX_SZ   (VOV_W * VOV_MAX_H)  // one bitmap
#define VOV_PAL_SZ   1024u                // 256 x BGRA8
#define VOV_BUF_SZ   (VOV_PAL_SZ + VOV_PIX_SZ)
#define VOV_MEM_SZ   (2u * VOV_BUF_SZ)    // double buffered: the GPU may still read the previous one

enum { VOV_TEXT, VOV_TOGGLE, VOV_SEGMENTS };   // value kinds: plain text, ON/OFF pill, 0..100 as six squares

typedef struct {
    char label[28];
    char value[24];       // VOV_TEXT
    int kind, on, pct;    // VOV_TOGGLE uses on, VOV_SEGMENTS uses pct
} VovRow;

typedef struct {
    char title[40];       // game name (ASCII; anything else is skipped)
    unsigned out_fps_x10, game_fps_x10;
    int nrows, sel;
    VovRow rows[10];
    char status[40];      // small line under the big number (inert reason, toast); may be empty
    char hints[48];       // footer, e.g. "↕ ← →   × APPLY   ○ CLOSE"
} VovMenu;

typedef struct {
    unsigned out_fps_x10, frame_ms_x10;
    int detailed;         // 0: number only, 1: frame time + number
} VovCounter;

// mem: VOV_MEM_SZ bytes of CPU-writable, GPU-readable memory.
void vov_init(uint8_t *mem);
// Smaller memory (at least VOV_PAL_SZ + VOV_W * 128): one buffer, panel height limited, menu rows get shorter.
void vov_init_size(uint8_t *mem, size_t size);
// Draw (only when the content changed) and return the buffers for this frame.
// *pal / *pix: addresses for the V#s, *w / *h: panel size in px (w, h multiples of 16).
int vov_menu(const VovMenu *m, const uint8_t **pal, const uint8_t **pix, unsigned *w, unsigned *h);
int vov_counter(const VovCounter *c, const uint8_t **pal, const uint8_t **pix, unsigned *w, unsigned *h);
// 1 when the scanout is HDR10 (0x88740000, BT.2020 PQ): palette colours are re-encoded for it.
void vov_set_hdr(int on);
// Buffers returned by the last vov_menu / vov_counter call (NULL before the first one).
const uint8_t *vov_pal_ptr(void);
const uint8_t *vov_pix_ptr(void);
