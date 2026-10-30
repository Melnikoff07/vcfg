// Option late_tail (config-<TITLE>.ini, default 1, late_tail = 0 turns it off; read on every load, so the hot reload
// switches it in game).
//
// With frame generation the real frame is flipped one refresh after the generated one, and the generated frame's flip
// packet sits at the end of our DCB. Some of the work in that DCB is needed only by the real frame or by the next pair:
// the history copy of C into g_pbuf (2-buffer games, read by the next warp only) and, with post_mid = 2, the real
// frame's half of the rcas8 dispatch and of the overlay. With late_tail = 1 that work leaves the generated frame's DCB
// (DCB1) and goes into the real frame's flip submission (DCB2), which until now carried the flip packet alone, so the
// generated frame flips earlier by the cost of that work and the GPU total stays the same:
//
//   DCB1: ... warp8 -> rcas8 mid only -> overlay mid only -> drain -> flip packet (generated frame)
//   DCB2: history copy -> rcas8 real only -> overlay real only -> drain -> flip packet (real frame)
//         (titles without the early history copy keep their order: rcas8 real -> overlay real -> history copy)
//
// DCB2 is built by build_dcb_flow8 in the same call as DCB1, at dword LT_DCB2_DW of the slot's DCB8 region (1024
// dwords; DCB1 is ~450 dwords there), and starts by setting again every compute register our passes rely on (thread
// count, user data, shader address and resources), since it is a separate submission. The real frame's table is a
// copy of the slot table in the SLOT_TB2_OFF region (free without the 3-slot pair, which disables late_tail), with
// rcas8 / overlay targeting the real frame only. When DCB1 is too long for DCB2 to fit after it, the same work is
// emitted inline at the end of DCB1 instead (counted in g_lt_fallbacks), never dropped.
//
// Not used (DCB unchanged): the 3-slot pair (two_mids), a present copy that goes to the screen (copy_idx != 0xFFFF),
// no history copy, the bench capture, a frame dump, rcas8 bit 5 (real_aa_on: its real pass reads what the mid pass of
// the same lane found), the post_mid_only real-frame AA pass, and every caller but the gnm flip path of mode 8, which
// sets g_lt_req around its call and submits DCB2 (g_lt_n[slot] dwords + the game's flip packet) as the real frame's
// flip. Both DCBs of a frame come from one call, so a switch in game takes effect between two frames, and the next
// warp always reads g_pbuf after the copy that wrote it (DCB2 runs before the next DCB1 on the same ring, whose first
// packets drain everything before them).
#pragma once
#include <stdint.h>

#define LT_DCB2_DW 640u      // DCB2 offset in dwords inside the slot's DCB8 region (DCB8_SZ = 0x1000 bytes)
#define LT_LBL_OFF 0x9D8u    // in each slotb, after t2[0..10] (0x980..0x9D7): DCB2 end-of-pipe label
#define LT_TS_OFF  0x9E0u    // DCB2 timestamps: +0 start, +8 end (0x9E0..0x9EF; the 0xA00 scratch stays free)

static volatile int g_late_tail = 1;   // option late_tail, default on
static int g_lt_req = 0;               // set by the caller that submits DCB2, around its build_dcb_flow8 call
static uint32_t g_lt_n[4];             // per slot: DCB2 body in dwords (0: no DCB2 this frame), without the flip packet
static uint32_t g_lt_fallbacks = 0;    // frames whose real-frame work went inline into DCB1 (no room for DCB2)
