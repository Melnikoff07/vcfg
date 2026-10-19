#pragma once
// A21-best stage switches (work\A21_best). Each stage adds ONE independent variable for a hardware run.
// S1..S4 are the A21c stages (claude_A21_exp). S5..S9 build cumulatively on S1 (S5 = S1 + slab2, S6 = S5 + post, ...),
// so a failing S2..S4 never blocks them; once S2..S4 are settled the passing flags can be combined.
// Shader arrays (shaders.h, warp720.h) are byte-identical to A13/A20 and must never be text-edited. New shaders
// (post_cb.h, warp540.h) are generated from IR by build_shader.py only.

// S1: memory-valve stop no longer persists enabled=0 to vcfg.ini (user preference != session state).
#ifndef A21_PREF_SPLIT
#define A21_PREF_SPLIT 1
#endif
// S1: passive telemetry only (structural flip-tail parse compared with the A20 parser, compute-register coverage
// before the first dispatch, characterisation of compute_shadow_unknown frames, unique/repeat presentation counts).
#ifndef A21_DISCOVERY
#define A21_DISCOVERY 1
#endif
// S2: pass-through hook of sceGnmSubmitCommandBuffers that only counts calls between flips (needed to prove that
// compute-register state carried from the previous flip call is still the hardware state).
#ifndef A21_COUNT_SUBMITS
#define A21_COUNT_SUBMITS 0
#endif
// S3: accept a flip call without any compute writes/dispatches by carrying the shadow of the previous fully known
// call, only when no other submit happened in between (requires A21_COUNT_SUBMITS=1).
#ifndef A21_CARRY_SHADOW
#define A21_CARRY_SHADOW 0
#endif
// S3b: s2 showed ~27 game submits between flips, so S3 alone never carries. S3b walks every non-flip game submit
// read-only with the PM4 scanner (time budget) and folds its compute-register writes into the shadow; the shadow stays
// valid across flips only while every intermediate submit was walked cleanly (no abort/unreadable IB/loads/type0/CCB,
// no Workload-variant submit, none in flight at the flip). Requires A21_CARRY_SHADOW.
#ifndef A21_SIDE_WALK
#define A21_SIDE_WALK 0
#endif
// S4: emit WaitFlipDone(index2) right before warp720 instead of before the pyramids (I is the only surface written).
#ifndef A21_WAIT_LATE
#define A21_WAIT_LATE 0
#endif
// A21c s5 slab map (kept for reproducing that build only; superseded by A21_SLAB2).
#ifndef A21_SLAB_MAP
#define A21_SLAB_MAP 0
#endif
// S5: slab2. Code [0x100,0xA800) with the A20 shaders + post_cb (loaded, NOT dispatched), font 0xA800,
// markers 0xAC00, slots unchanged (8 x 0x2800 from 0xC000). vcfg.ini is parsed/saved as key=value.
// Native C is never written while FG runs (menu/OSD writes C only while FG is paused or off).
#ifndef A21_SLAB2
#define A21_SLAB2 0
#endif
// S6: post_cb on the generated surface I only (4 checkerboard dispatches, in place, race-free). Active only when
// sharp>0 or enhance>0 in the ini; with defaults (0/0) S6 behaves exactly as S5.
#ifndef A21_POST
#define A21_POST 0
#endif
// S7: full menu (FG, mode, sharp, enhance, overlay, opacity, FPS lock = keep game) and honest corner overlay
// (G game submits/s, D displayed flips/s, U new FG images/s, R repeated vblanks/s, S native fallbacks/s, GPU ms).
#ifndef A21_MENU
#define A21_MENU 0
#endif
// S8: presentation scheduler (fg_core, independent model) for FAST with one 720p surface: fractional carry, no debt
// after hitches, flip-queue backpressure. QUALITY path is unchanged (1 mid, t=0.5).
#ifndef A21_SCHED
#define A21_SCHED 0
#endif
// S9: two 960x540 mid surfaces (index 2,3), warp540 from IR, repacked work (low 0xC0000 / high 0xD0000), FAST up
// to 2 different mids per game frame. Plain 64-dword 0x68750777 flip tail only (side-effect free replay).
#ifndef A21_FAST540
#define A21_FAST540 0
#endif

// A22-s1: universal runtime, stage 1. The A20/A21 runtime is enabled for an allow-list of titles; per title it
// discovers 2 or 3 game buffers (our mid is registered right after them), accepts 8-bit A8R8G8B8 and 10-bit
// A2R10G10B10 (sRGB / BT2020 PQ) 1920x1080 frames (T# data format and our VideoOut attribute follow the game), and
// plans its DirectMemory from the live free map (top of the highest free range) instead of the REF constants.
// REF keeps its measured windows. Input resolutions other than 1920x1080 need shader work (stage 2).
#ifndef A22_UNIVERSAL
#define A22_UNIVERSAL 0
#endif
#if A22_UNIVERSAL && (!A21_SLAB2 || A21_FAST540)
#error "A22_UNIVERSAL builds on the 720p single-mid runtime (s5..s8)"
#endif
#if A21_CARRY_SHADOW && !A21_COUNT_SUBMITS
#error "A21_CARRY_SHADOW needs A21_COUNT_SUBMITS"
#endif
#if A21_SIDE_WALK && !A21_CARRY_SHADOW
#error "A21_SIDE_WALK needs A21_CARRY_SHADOW"
#endif
#if A21_SLAB2 && A21_SLAB_MAP
#error "A21_SLAB2 replaces A21_SLAB_MAP"
#endif
#if (A21_POST || A21_MENU || A21_SCHED || A21_FAST540) && !A21_SLAB2
#error "S6..S9 need A21_SLAB2"
#endif
#if A21_FAST540 && !A21_SCHED
#error "A21_FAST540 needs A21_SCHED"
#endif
#if A21_FAST540 && A21_WAIT_LATE
#error "A21_WAIT_LATE is a 720p single-surface experiment"
#endif

#define A21_BUILD_TAG "A21best"
