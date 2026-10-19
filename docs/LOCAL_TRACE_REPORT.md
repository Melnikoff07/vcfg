# Local trace snapshot report

Saved evidence only; no hardware tests were performed by this report. FPS uses the last five retained STAT samples. Cadence is the last retained window, with bins 1/2/3/4+ vblanks; latency is average / maximum ms. These windows are asynchronous. Startup and tail can describe different menus/settings. Missed observations make cadence incomplete; no automatic WORKS or CRASHED verdict is inferred.

## Current trace files

| title | version | buffers | format | resolution | fps | cadence | missed | pending | latency | state |
|---|---|---|---|---|---|---|---|---|---|---|
| CUSA00411 | 2.14 | 3 | 0x80000000 | 1920x1080 | 45.6–46.1 (median 45.8) | mid 25/0/0/0; real 89/3/0/0 | 0 | 4 | - | no startup refusal (visual result unverified) |
| CUSA00557 | 2.14 | 3 | 0x88000000 | 1920x1080 | 30.0–60.0 (median 58.1) | mid 0/0/0/0; real 120/0/0/0 | 0 | 2 | - | no startup refusal (visual result unverified) |
| CUSA05571 | 2.14 | 2 | 0x80000000 | 1920x1080 | 24.2–28.7 (median 24.8) | mid 6/0/1/1; real 4/23/3/9 | 70 | 3 | 51.1 / 59.7 (on) | no startup refusal (visual result unverified) |
| CUSA08809 | 2.14 | 2 | 0x88000000 | 1920x1080 | 28.5–29.9 (median 29.5) | mid 57/0/0/0; real 49/7/0/0 | 0 | 4 | - | no startup refusal (visual result unverified) |
| CUSA09171 | 2.14 | 3 | 0x88740000 | 1920x1080 | 30.0–30.0 (median 30.0) | mid 60/0/0/0; real 60/0/0/0 | 0 | 4 | - | no startup refusal (visual result unverified) |
| CUSA13801 | 2.14 | 3 | 0x88740000 | 1920x1080 | 28.1–30.1 (median 30.0) | mid 58/0/0/0; real 57/3/0/0 | 0 | 4 | - | no startup refusal (visual result unverified) |

## Previous trace files (separate starts)

| title | version | buffers | format | resolution | fps | cadence | missed | pending | latency | state |
|---|---|---|---|---|---|---|---|---|---|---|
| CUSA00411 | 2.14 | 3 | 0x80000000 | 1920x1080 | 29.9–30.0 (median 30.0) | mid 60/0/0/0; real 60/0/0/0 | 0 | 4 | - | no startup refusal (visual result unverified) |
| CUSA00557 | 2.14 | 3 | 0x88000000 | 1920x1080 | 59.9–60.0 (median 60.0) | mid 0/0/0/0; real 121/0/0/0 | 0 | 2 | - | INERT: HDR/FMT 0x88000000 |
| CUSA05571 | 2.14 | 2 | 0x80000000 | 1920x1080 | 20.7–27.4 (median 27.3) | mid 54/0/0/0; real 44/11/0/0 | 0 | 4 | 50.9 / 59.6 (on) | no startup refusal (visual result unverified) |
| CUSA08809 | 2.14 | 2 | 0x88000000 | 1920x1080 | 29.9–30.5 (median 30.0) | mid 58/0/0/0; real 59/0/1/0 | 0 | 4 | - | no startup refusal (visual result unverified) |
| CUSA09171 | 2.14 | 3 | 0x88740000 | 1920x1080 | 48.2–50.1 (median 49.7) | mid 0/0/0/0; real 82/19/0/0 | 0 | 2 | - | INERT: HDR/FMT 0x88740000 |
| CUSA13801 | 2.14 | 3 | 0x88740000 | 1920x1080 | 29.2–30.0 (median 30.0) | mid 0/0/0/0; real 1/58/1/0 | 0 | 2 | - | INERT: HDR/FMT 0x88740000 |

## Build attribution and evidence limits

| title | file | engine | build | truncated | wait |
|---|---|---|---|---|---|
| CUSA00411 | trace-CUSA00411.txt | classic | not recorded | 13 | - |
| CUSA00557 | trace-CUSA00557.txt | classic | not recorded | 13 | - |
| CUSA05571 | trace-CUSA05571.txt | classic | not recorded | 23 | - |
| CUSA08809 | trace-CUSA08809.txt | classic | not recorded | 20 | - |
| CUSA09171 | trace-CUSA09171.txt | classic | not recorded | 19 | - |
| CUSA13801 | trace-CUSA13801.txt | classic | not recorded | 19 | - |

truncated counts retained rows at the flight recorder width; their missing fields cannot be recovered.

Configured titles: 26. Current traces: 6. No current trace: 20.

## One-minute tests after the two-buffer comparison

For each title: enable only its own profile, start gameplay, open/close the menu, turn the camera, and collect a snapshot before moving to the next title. Record artifacts and input feel separately. A missing trace is untested, not a crash.

| title | test |
|---|---|
| CUSA00207 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA01113 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA01163 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA02532 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA03679 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA04865 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA05952 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA08308 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA08495 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA08519 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA10249 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA10416 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA11751 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA11898 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA16746 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA16799 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA17419 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA20499 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA27107 | gameplay + overlay + trace; classify format/buffers/resolution/inert |
| CUSA27321 | gameplay + overlay + trace; classify format/buffers/resolution/inert |

## Retained startup decisions

- 6 trace(s): no startup refusal (visual result unverified)

## Archived full logs (each file kept separate)

Files may include settings changes and overlap with snapshots; sample ranges are not controlled benchmarks. Copies describes availability, not whether present_copy was enabled. Pacing says only what the file explicitly logged. CPU and GPU are the last retained measurements in ms.

| file | title | buffers | copies | pacing | fps | cpu | gpu | missed | latency |
|---|---|---|---|---|---|---|---|---|---|
| w10.log | CUSA05571 | 2 | available | vblank | 23.3–25.0 (median 24.8) | 25.7 | 2.85 | 0 | 52.5 / 60.6 (on) |
| w11.log | CUSA05571 | 2 | available | vblank | 23.4–26.2 (median 25.1) | 27.7 | 3.66 | 0 | 55.0 / 66.1 (on) |
| w12.log | CUSA05571 | 2 | available | vblank | 20.4–26.5 (median 22.9) | 54.5 | 2.85 | 34 | 55.4 / 74.5 (on) |
| w13.log | CUSA05571 | 2 | available | vblank | 27.2–27.4 (median 27.3) | 19.9 | 2.95 | 0 | 51.8 / 63.1 (on) |
| w14.log | CUSA05571 | 2 | available | not recorded | 23.9–28.1 (median 24.6) | 38.2 | 3.64 | 45 | 54.6 / 71.7 (on) |
| w2.log | CUSA05571 | 2 | not recorded | not recorded | 22.5–22.6 (median 22.5) | 29.2 | 2.83 | 0 | 53.5 / 61.3 (on) |
| w3.log | CUSA05571 | 2 | not recorded | not recorded | 24.9–25.7 (median 25.6) | 36.7 | 2.73 | 48 | 52.5 / 62.5 (on) |
| w4.log | CUSA05571 | 2 | not recorded | not recorded | 24.7–25.0 (median 24.8) | 25.1 | 2.74 | 0 | 53.5 / 65.6 (on) |
| w5.log | CUSA05571 | 2 | not recorded | timer | 25.6–29.2 (median 29.2) | 33.9 | 2.71 | 69 | 48.7 / 58.8 (on) |
| w6.log | CUSA05571 | 2 | available | vblank | 30.0–30.0 (median 30.0) | 9.7 | 3.08 | 0 | 55.5 / 56.1 (on) |
| w7.log | CUSA05571 | 2 | available | vblank | 24.5–24.9 (median 24.8) | 25.0 | 3.73 | 0 | 52.7 / 67.9 (on) |
| w8.log | CUSA05571 | 2 | available | not recorded | 27.3–28.7 (median 28.6) | 36.0 | 3.21 | 55 | 51.1 / 60.9 (on) |
| w9.log | CUSA05571 | 2 | available | vblank | 16.1–24.1 (median 23.8) | 29.5 | 2.89 | 2 | 54.1 / 70.3 (on) |
| w_probe.log | CUSA05571 | 2 | not recorded | not recorded | 27.3–28.2 (median 27.8) | 0.0 | 0.00 | 0 | 41.4 / 53.0 (off) |
| w_probe.prev.log | CUSA05571 | 2 | not recorded | not recorded | 29.9–30.0 (median 30.0) | 33.2 | 5.51 | 85 | 60.4 / 62.0 (on) |
