# Hardware findings (base console, GoldHEN, October 2026)

Measured on the console unless marked *(hypothesis)*.

## VideoOut

- Registering an extra 1280×720 linear surface next to 1920×1080 game buffers works (8-bit, and 10-bit
  `0x88740000` in CUSA09171 and CUSA13801).
- **960×540 is rejected** (`0x80290005` INVALID_RESOLUTION), pitch 960 or 1024. Two 540p surfaces for a second
  generated image are therefore not an option.
- Pixel formats seen: `0x80000000` (CUSA07439), `0x88000000` (CUSA08809), `0x88740000` (CUSA09171, CUSA13801).
  10-bit is read/written by our shaders as `10_10_10_2` *(colour correctness still to be checked on screen)*.

## Frame submission

| Title ID | Flip | Other submits per frame | Buffers |
|---|---|---|---|
| CUSA07439 | `sceGnmSubmitAndFlipCommandBuffers`, tail = 64-dword NOP `0x68750777` last | ~27 plain + some `ForWorkload` | 2 |
| CUSA09171 | same API, whole frame in one call, DCB up to 1.5 MiB | none | 3 |
| CUSA13801 | same API, flip call = only a 256-byte DCB (the flip NOP) | ~14 plain | 3 |
| CUSA08519 | separate 256-byte flip DCB (older notes) | many | 2 |
| CUSA00411 | tail marker `0x68750778` followed by register packets | — | 3 |

## Memory

- CUSA07439: only two free windows after the game is up: `[0x130000,0x200000)` (832 KiB) and
  `[0x11FB00000,0x120000000)` (5 MiB). A reservation inside the game's 3 GiB arena broke the game's later allocation.
- CUSA09171: 112 MiB free at the top of the pool; CUSA13801: 24 MiB; CUSA08809: 0.8 MiB (no FG possible as is).

## Algorithm

- Motion search was silently broken in builds A15–A19 by a global text replace that changed 75 machine words; A20
  restored the arrays byte for byte and smoothness came back. Every shader array is now checked at build time.
- GPU time of the full synthesis in CUSA07439: median 1.73 ms, p95 1.81 ms.
- In CUSA07439 ~2 % of frames have no compute writes at all in the flip call → shown natively.
- Walking all intermediate submits to track the registers (S3b) costs ~1.3 ms CPU per frame in CUSA07439 and still fails
  (ForWorkload submits, 17 % walk aborts, CCB) → abandoned.
- Old RCAS ran in place and raced between workgroups; the current post shader runs four checkerboard passes.
- Sharpening only applies to generated frames (the real frame must stay untouched as next frame's history), so its
  visible effect is small.
