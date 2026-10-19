# Architecture

## Frame order and ownership (2 or 3 game buffers)

```
game renders N into buffer C ─┐
                              ├─ our GPU job (appended to the game's flip submit):
previous flip's buffer = P ───┘    wait until our surface I is not on screen (WaitFlipDone(mid index))
                                   read P and C → pyramids → motion → phase → warp → I (1280×720)
                                   [post: RCAS/AA on I only]   restore the game's compute registers
flip I  (arg = N)   ← releases P to the game (the game waits only for arg ≥ previous frame)
flip C  (arg = N)   ← the real frame, separate tiny submit with a copy of the game's flip tail
```

- Every read of P and C finishes before the I flip: after it the game may render into P again.
- Nothing ever writes the native frame C while FG runs, except the optional corner overlay, which is drawn on C
  *before* our passes read it (a static HUD element in both P and C); the warp copies that rectangle from C (HUD
  protection), so it has the same size on generated and real frames.
- Our surface is registered at index = number of game buffers (2 in CUSA07439, 3 in CUSA09171 and CUSA13801), same pixel format as
  the game (8-bit A8R8G8B8 or 10-bit A2R10G10B10), linear 1280×720.

## Per-frame admission (any failure → that frame is native)

| Check | Why |
|---|---|
| handle, buffer index, flip mode, 1920×1080, supported format | the shaders assume a 1080p source and our layout |
| no constant command buffer, DCB size, DCB readable | we append to and copy from the game's command buffers |
| flip tail recognised (SDK NOP `0x687507xx` at the end) | we replay that tail for the real-frame flip |
| compute-register state known | our job overwrites 9 CS registers and must restore the game's values |
| previous frame buffer known | the first frame after a skip has no P |

Reasons are logged (`A20_NATIVE_ONLY reason=…`, `A22_GATE`, `A22_DCB_SKIP`).

## Memory

One upper allocation: GPU slab 0x20000 (code ≤0xA800, font, markers, 8 job slots × 0x2800) + 1280×720 surface
0x384000 + high work 0x110000; one low allocation 0x80000 (L1 pyramid). The planner reads the DirectMemory map and
places both at the top of the highest free range (the kernel serves later game allocations lowest-first). CUSA07439 uses
its measured windows. When the game later fails an allocation (EAGAIN), the valve stops FG, drains GPU/command/scanout
work, unregisters, frees and retries the game's request once; FG stays off for that session only.

## Presentation modes

- **QUALITY**: one generated image per game frame at t = 0.5 (best for 30 fps games).
- **FAST**: a scheduler (fractional carry, no debt after hitches, flip-queue backpressure) decides per game frame
  whether a generated image is needed to reach the display refresh: 40 fps → about every second frame, 60 fps → none.

## Compute-register state (the main portability problem)

Our passes set COMPUTE_PGM/RSRC/NUM_THREAD/USER_DATA. To give the GPU back to the game we restore the values the
game last wrote, which requires walking the game's command buffers. This works in CUSA07439 (≈98 % of frames). It fails
when the game writes those registers in other submits (CUSA13801, CUSA08519: dozens per frame) or when a frame is huge (CUSA09171:
up to 1.5 MiB of commands). Planned fixes: verify per title that the game re-sets all compute registers before its
first dispatch (then no restore is needed), and, later, an own compute queue.

## Files

- `plugin/source/a20_copy_impl.h`: the runtime (init, planner, per-frame job, observer, menu, valve, capture).
- `plugin/source/main.c`: hooks, title profiles, an older generation path used by other plugin versions.
- `plugin/source/a21_config.h`: compile-time stage switches; `build.ps1` sets the release combination.
- `plugin/source/pm4scan.c`: conservative PM4 walker (register shadow, IB following, budgets).
- `plugin/source/fg_core.c`: scheduler and planning helpers (pure C, no kernel calls).
