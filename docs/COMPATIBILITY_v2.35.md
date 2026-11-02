# Compatibility — v2.35 (historical)

**Audit: 2 November 2026 (Moscow). This page describes v2.35 only.** For the current release see [v2.36 compatibility](COMPATIBILITY.md). [v2.35 release notes](https://github.com/Melnikoff07/vcfg/releases/tag/v2.35).

Reused the full SHA-256-verified archive of **4,793 original text files**. Primary v2.35 evidence comes from **874 single-version originals**, yielding **2,012 deduplicated boot/trace/crash section snapshots for 237 exact CUSA IDs**. All these originals were uploaded after the public v2.35 release. Mixed-version containers and unversioned sections are excluded, even when the launcher header says v2.35. Repeated uploads and changing snapshots are not independent users or sessions.

Archive cutoffs: VPS **2 November, 14:46 MSK**; Discord **14:36 MSK**. Observations after v2.36 was released still belong to v2.35 when the runtime stamp says v2.35. No v2.36 fix or mode behavior is retroactively applied here.

## Status definitions

- **FG OBSERVED:** generated presentations are recorded in a trace reaching at least 120 seconds, with no competing crash/inactive/symptom observation for this CUSA in the eligible sample.
- **SHORT FG TRACE:** generation is recorded, but the strongest positive trace is shorter than 120 seconds.
- **MIXED:** positive generated output coexists with crash, inactive or telemetry-symptom evidence across launches/configurations.
- **CRASH LOGGED:** crash marker exists without a qualifying positive generated-output observation. This does not establish VCFG causality.
- **FG INACTIVE:** inert/safe/off observation without proven generation; deliberately disabled FG is not proof of incompatibility.
- **INSUFFICIENT:** no generated output or crash is established by the attributable startup/runtime evidence.
- **KNOWN PRO ISSUE:** the v2.35 release explicitly warns about CUSA10249 on PS4 Pro. This restriction does not apply to Base/Slim merely because the CUSA matches.

Trace elapsed time is **since startup, not verified gameplay duration or uninterrupted FG time**. FG telemetry does not certify visual quality, audio, loading transitions or stability. Symptom flags are heuristic leads and may reflect loading/menu context. Hardware comes from runtime `neo`; resolution means source-buffer layout, not display/HDR mode. Game update and exact PRX binary identity are not established. Unknown fields stay unknown. Aliases do not inherit a result; installed-game inventories and host replay tests are not compatibility evidence.

Report IDs and original/section hashes are in the [v2.35 evidence index](compatibility-evidence-v2.35.json).

## Summary

Counts for v2.35 and v2.36 come from different evidence pools and are not directly comparable as success or regression rates. A regression requires matching CUSA/update, console, settings and reproducible before/after evidence.

| Result | CUSA count |
|---|---|
| CRASH LOGGED | 25 |
| MIXED | 60 |
| FG OBSERVED | 60 |
| SHORT FG TRACE | 35 |
| INSUFFICIENT | 26 |
| FG INACTIVE | 30 |
| KNOWN PRO ISSUE | 1 |

## Runtime evidence

| CUSA | Result | Console / source buffers | Observation | Evidence |
|---|---|---|---|---|
| CUSA00000 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven | #24DCA9, #353C1B, #A18076 |
| CUSA00002 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 21s; crash marker; causality unproven | #A7E305 |
| CUSA00004 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #243055, #FC7CDA, #FD6B2B |
| CUSA00010 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 495s | #47D4D7, #5EB71C |
| CUSA00093 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 85s | #0DB4CA, #0E8D77, #3C11F6 |
| CUSA00109 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 523s | #47D4D7, #5EB71C |
| CUSA00113 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 4396s | #E9B9E1 |
| CUSA00127 | **SHORT FG TRACE** | Pro; 1920x1080 | FG logged; trace reaches 62s | #45A284 |
| CUSA00128 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 43s | #6734C4, #6BA15F |
| CUSA00135 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 377s; crash marker; causality unproven | #D84606 |
| CUSA00190 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 24s; crash marker; causality unproven | #B6F4F3 |
| CUSA00239 | **INSUFFICIENT** | Base/Slim; 1920x1080 | symptom flag: LOW_FPS_LOCK; startup/trace only; no generated output proven | #AD8178 |
| CUSA00243 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #357B27 |
| CUSA00265 | **FG OBSERVED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 809s | #4CCECC, #8BFEC2 |
| CUSA00341 | **MIXED** | Base/Slim/Pro; 1920x1080/2560x1440 | FG logged; trace reaches 2336s; crash marker; causality unproven | #019A73, #24DCA9, #318C18 |
| CUSA00359 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 7s; crash marker; causality unproven | #7A1EB5 |
| CUSA00411 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 5342s; crash marker; causality unproven; inert/safe/off observation | #29BE78, #6CE095, #D8B963 |
| CUSA00416 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 238s | #47D4D7, #5EB71C |
| CUSA00419 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 8928s; crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #12D0FD, #1BC07B, #20039E |
| CUSA00552 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1612s | #D61770 |
| CUSA00556 | **FG OBSERVED** | Base/Slim/Pro; 1920x1080/3200x1800 | FG logged; trace reaches 1957s | #4AB9D3, #D444E8 |
| CUSA00557 | **FG OBSERVED** | Pro; 3200x1800 | FG logged; trace reaches 133s | #4AB9D3 |
| CUSA00663 | **FG OBSERVED** | Base/Slim; 1600x900 | FG logged; trace reaches 396s | #562DAF |
| CUSA00725 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1590s | #1D1BC0, #46DEB2, #96B20C |
| CUSA00744 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 318s | #9EFA71 |
| CUSA00900 | **MIXED** | Base/Slim; 1280x720/1920x1080 | FG logged; trace reaches 762s; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #3311D0, #390BF6, #5362D4 |
| CUSA00918 | **MIXED** | Base/Slim/Pro; 1920x1080/2560x1440 | FG logged; trace reaches 1168s; crash marker; causality unproven; inert/safe/off observation | #1D1F47, #252475, #309068 |
| CUSA00960 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 88s; crash marker; causality unproven | #0F8CF8 |
| CUSA01015 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 8101s; crash marker; causality unproven; inert/safe/off observation | #318C18, #AD16F6, #B939B8 |
| CUSA01047 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 209s | #73FEF2, #89B3CC, #AAFF14 |
| CUSA01073 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 736s | #B56A0D |
| CUSA01111 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 369s; crash marker; causality unproven | #05C806, #75036D |
| CUSA01163 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 359s; crash marker; causality unproven | #5976BD, #7C604C, #FCF683 |
| CUSA01599 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 238s | #357B27 |
| CUSA01623 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #24DCA9, #A18076, #C18380 |
| CUSA01715 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #BFEE8D |
| CUSA01761 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 179s | #39E9B6 |
| CUSA01765 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 3433s | #EF50D9 |
| CUSA01940 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1235s | #3BC277 |
| CUSA01967 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 34s; crash marker; causality unproven; symptom flag: LOW_FPS_LOCK | #43EDF9, #C45061, #F78E23 |
| CUSA02000 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #41AE24 |
| CUSA02010 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 3673s | #25505B |
| CUSA02152 | **INSUFFICIENT** | Pro; 1920x1080 | startup/trace only; no generated output proven | #6734C4, #6BA15F |
| CUSA02218 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #25505B |
| CUSA02290 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 107s; crash marker; causality unproven | #825F34 |
| CUSA02299 | **MIXED** | Base/Slim/Pro; 1280x720/1920x1080/3840x2160 | FG logged; trace reaches 111s; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #39E9B6, #4AB9D3, #AD8178 |
| CUSA02320 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1398s; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #09ACA1, #870BCD |
| CUSA02389 | **FG OBSERVED** | Pro; 2880x1620 | FG logged; trace reaches 273s | #562DAF |
| CUSA02412 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #24DCA9, #A18076, #C18380 |
| CUSA02624 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 304s; crash marker; causality unproven | #21F9D1, #6CB235 |
| CUSA02636 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 12s; crash marker; causality unproven | #93F4BE, #A6DA0A |
| CUSA02703 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 573s | #3F03C9 |
| CUSA02902 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 8s | #123B2C |
| CUSA03004 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 572s; crash marker; causality unproven | #D69402 |
| CUSA03005 | **INSUFFICIENT** | Pro; resolution unknown | startup/trace only; no generated output proven | #3311D0 |
| CUSA03041 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 2309s; crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #0D5492, #3A2552, #3BC277 |
| CUSA03173 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 892s; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #4AB9D3, #4CCECC, #8BFEC2 |
| CUSA03214 | **INSUFFICIENT** | Base/Slim; 1920x1080 | symptom flag: LOW_FPS_LOCK; startup/trace only; no generated output proven | #D61770 |
| CUSA03309 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 295s | #0714F0, #7107A4 |
| CUSA03450 | **FG OBSERVED** | Pro; 2560x1440 | FG logged; trace reaches 176s | #64659D |
| CUSA03589 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 283s; crash marker; causality unproven | #357B27, #B6257F |
| CUSA03627 | **INSUFFICIENT** | Pro; 3360x1890 | symptom flag: LOW_FPS_LOCK; startup/trace only; no generated output proven | #4AB9D3 |
| CUSA03694 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven | #44A54C, #BB0C62 |
| CUSA03979 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #A6566D |
| CUSA04439 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 142s | #1D1BC0, #ED5AED |
| CUSA04459 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 3116s | #3DE4BD |
| CUSA04794 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 17s | #BD66D8, #F53A5D |
| CUSA04865 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 18s | #8EDEBB |
| CUSA04885 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 79s | #A3756C |
| CUSA04893 | **SHORT FG TRACE** | Pro; 1920x1080/3840x2160 | FG logged; trace reaches 71s | #47D4D7, #5EB71C |
| CUSA05392 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1166s | #0355C7, #84334A, #F33C3D |
| CUSA05486 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 978s | #75E54C |
| CUSA05574 | **SHORT FG TRACE** | Base/Slim; 1280x720/1920x1080 | FG logged; trace reaches 116s | #1D1F47, #252475, #562DAF |
| CUSA05625 | **FG OBSERVED** | Base/Slim/Pro; 1920x1080/2880x1620 | FG logged; trace reaches 1625s | #48AF8B, #6734C4, #6BA15F |
| CUSA05725 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1643s; inert/safe/off observation | #0C97B8, #586A9F, #7AF108 |
| CUSA05794 | **FG OBSERVED** | Pro; 1920x1080/3840x2160 | FG logged; trace reaches 316s | #6734C4, #6BA15F |
| CUSA05849 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1790s | #8A7F7F |
| CUSA05904 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 945s | #90B799, #98761D |
| CUSA05969 | **FG INACTIVE** | Pro; 3840x2160 | inert/safe/off observation | #64659D |
| CUSA05999 | **FG INACTIVE** | Base/Slim/Pro; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #357B27 |
| CUSA06014 | **FG OBSERVED** | Pro; 3840x2160 | FG logged; trace reaches 146s | #1DD3CA, #5AFF87 |
| CUSA06212 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; symptom flag: LOW_FPS_LOCK | #13DF78, #6690C4 |
| CUSA06548 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 21s | #E6C104 |
| CUSA06569 | **FG OBSERVED** | Pro; 1920x1080 | FG logged; trace reaches 122s | #357B27 |
| CUSA06782 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 7490s | #B9EC2C |
| CUSA07104 | **INSUFFICIENT** | unknown; 1920x1080 | startup/trace only; no generated output proven | #7F6085, #DAFB15 |
| CUSA07320 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 19s | #C6FB6B |
| CUSA07399 | **INSUFFICIENT** | unknown; 1920x1080 | startup/trace only; no generated output proven | #1D1F47, #252475 |
| CUSA07408 | **MIXED** | Base/Slim/Pro; 1920x1080/1920x720/3840x2160 | FG logged; trace reaches 4003s; inert/safe/off observation | #203430, #3C9917, #78E3F7 |
| CUSA07410 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 2410s; inert/safe/off observation | #48AF8B, #DDB12C |
| CUSA07439 | **FG OBSERVED** | Pro; 1920x1080 | FG logged; trace reaches 398s | #64659D |
| CUSA07575 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 85s | #1D5D40 |
| CUSA07737 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 402s | #07D3CE, #23A00B, #6561E8 |
| CUSA07820 | **MIXED** | Base/Slim/Pro; 1920x1080/2560x1440 | FG logged; trace reaches 876s; crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #0CDA6D, #0D7C96, #B18242 |
| CUSA08004 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 193s | #D0C002 |
| CUSA08006 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 59s | #3DE4BD |
| CUSA08155 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1086s | #2D81E0, #576013 |
| CUSA08252 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 499s | #C4FDBC |
| CUSA08344 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #D61770 |
| CUSA08495 | **SHORT FG TRACE** | Pro; 3200x1800 | FG logged; trace reaches 44s | #4AB9D3 |
| CUSA08519 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 2068s; crash marker; causality unproven; inert/safe/off observation | #24DCA9, #6418B7, #A18076 |
| CUSA08600 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 200s | #1C98ED |
| CUSA08809 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 1687s; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #4AB9D3, #A6566D |
| CUSA08966 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 6287s | #48AF8B, #897A8D |
| CUSA08975 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 78s | #DE8A87 |
| CUSA09171 | **MIXED** | Base/Slim/Pro; 1920x1080/2880x1620 | FG logged; trace reaches 507s; crash marker; causality unproven; inert/safe/off observation | #0E8D77, #1D1F47, #252475 |
| CUSA09175 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 159s; crash marker; causality unproven | #2CE575, #A53AE9 |
| CUSA09193 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1996s; crash marker; causality unproven | #2A972E, #71F077, #C1ACC7 |
| CUSA09303 | **FG OBSERVED** | Pro; 2880x1620 | FG logged; trace reaches 334s | #15311F |
| CUSA09417 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #E702C3, #F552B7 |
| CUSA09554 | **FG OBSERVED** | Pro; 1920x1080/3200x1800 | FG logged; trace reaches 225s | #537628 |
| CUSA09690 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 232s | #A6566D |
| CUSA09771 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 457s | #1D1BC0, #ED5AED |
| CUSA10212 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 12s; crash marker; causality unproven | #66C89C, #A79212 |
| CUSA10213 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 16s; crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK; v2.35 release also lists memory-pressure crashes | #64659D, #9862A8, #E1550C |
| CUSA10218 | **INSUFFICIENT** | Base/Slim; 1920x1080 | symptom flag: LOW_FPS_LOCK; startup/trace only; no generated output proven | #98761D |
| CUSA10237 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 18s; crash marker; causality unproven; inert/safe/off observation | #47111F, #913F09, #91C11D |
| CUSA10249 | **KNOWN PRO ISSUE** | Base/Slim/Pro; 1920x1080/2560x1440 | FG logged; trace reaches 5691s; crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK; v2.35 release warns of startup crashes/FG off on Pro; disable there if affected; Base/Slim not covered by that warning | #39E9B6, #603CDD, #7A0759 |
| CUSA10326 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 866s | #317B59, #DC81EE |
| CUSA10688 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 43s | #5DF8D9 |
| CUSA10812 | **FG OBSERVED** | Pro; 3840x2160 | FG logged; trace reaches 287s | #562DAF |
| CUSA10960 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #D61770 |
| CUSA11154 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation | #9868C7, #9B227F |
| CUSA11379 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 157s | #13EE3D |
| CUSA11395 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 150s | #D801F3 |
| CUSA11454 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 4768s | #492ED4 |
| CUSA11456 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 890s; crash marker; causality unproven; inert/safe/off observation | #48AF8B, #4AB9D3, #E49C54 |
| CUSA11638 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 2921s | #897A8D |
| CUSA11986 | **INSUFFICIENT** | Base/Slim; 1920x1080 | symptom flag: LOW_FPS_LOCK; startup/trace only; no generated output proven | #D24CDA |
| CUSA11995 | **INSUFFICIENT** | Base/Slim/Pro; 1920x1080/3840x2160 | startup/trace only; no generated output proven | #1802EB, #52AEC2, #D801F3 |
| CUSA12047 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 368s; crash marker; causality unproven | #2ACD06, #3EC09B, #5594EF |
| CUSA12125 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #8F6434 |
| CUSA12398 | **CRASH LOGGED** | Pro; 1920x1080 | crash marker; causality unproven | #DD652D |
| CUSA12529 | **FG INACTIVE** | Base/Slim; 1280x720/1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #8C6482, #AADED0, #C04D4E |
| CUSA12555 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; symptom flag: LOW_FPS_LOCK | #017B8C, #2CA5EB, #7AF108 |
| CUSA12605 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: AUDIO_DM_STARVATION | #F14B0E |
| CUSA12771 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #754077, #CFEC13, #DD58EC |
| CUSA13285 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 17s | #4B383C |
| CUSA13323 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 7520s; crash marker; causality unproven; inert/safe/off observation | #19A0B2, #3080FF, #AD8178 |
| CUSA13529 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 144s; symptom flag: LOW_FPS_LOCK | #07D3CE, #23A00B, #6561E8 |
| CUSA13794 | **CRASH LOGGED** | Base/Slim; resolution unknown | crash marker; causality unproven | #2C208B, #90DA84 |
| CUSA13795 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #A6D29E |
| CUSA13801 | **FG INACTIVE** | Pro; 3200x1800 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #4AB9D3 |
| CUSA14204 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 126s | #BFEE8D |
| CUSA14835 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #3DE4BD |
| CUSA14876 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 23s; crash marker; causality unproven; inert/safe/off observation | #0E1CB0, #1A505C, #8530B5 |
| CUSA14925 | **CRASH LOGGED** | Pro; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #8645D3 |
| CUSA15010 | **INSUFFICIENT** | Pro; 1920x1080 | startup/trace only; no generated output proven | #7F23FB |
| CUSA15081 | **FG INACTIVE** | Pro; 3840x2160 | inert/safe/off observation; symptom flag: AUDIO_DM_STARVATION, LOW_FPS_LOCK | #67E436, #A23DCE |
| CUSA15090 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: AUDIO_DM_STARVATION, LOW_FPS_LOCK | #8C6482, #AADED0, #C04D4E |
| CUSA15278 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation | #0A60A1 |
| CUSA15532 | **CRASH LOGGED** | Pro; 1920x1080 | crash marker; causality unproven | #80D47F |
| CUSA15778 | **MIXED** | Base/Slim/Pro; 3840x2160 | FG logged; trace reaches 325s; crash marker; causality unproven | #0714F0, #1BFA4D, #2CCC23 |
| CUSA16195 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 134s; crash marker; causality unproven | #8E3085 |
| CUSA16283 | **INSUFFICIENT** | unknown; 1920x1080 | startup/trace only; no generated output proven | #E4EC46 |
| CUSA16345 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 168s | #793F9B |
| CUSA16485 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #A6D29E |
| CUSA16579 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven | #2CE612 |
| CUSA16596 | **MIXED** | Base/Slim; 1280x720/1920x1080 | FG logged; trace reaches 2767s; crash marker; causality unproven; symptom flag: LOW_FPS_LOCK | #7AF108, #AE60DE, #D525E1 |
| CUSA16602 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 69s; crash marker; causality unproven | #00ABFA |
| CUSA16734 | **CRASH LOGGED** | Base/Slim; 1600x900 | crash marker; causality unproven; inert/safe/off observation | #7E0ACE, #D14D20 |
| CUSA16742 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #68782D |
| CUSA16972 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven | #203FAA, #5EFF83 |
| CUSA17225 | **FG INACTIVE** | Pro; 3840x2160 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #BEC6E0 |
| CUSA17722 | **FG INACTIVE** | Base/Slim/Pro; 1920x1080 | inert/safe/off observation | #A6D29E |
| CUSA17776 | **CRASH LOGGED** | Base/Slim/Pro; 1920x1080/3840x2160 | crash marker; causality unproven; inert/safe/off observation | #36EC6C, #38ECC5, #87ECA9 |
| CUSA18058 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 160s; symptom flag: LOW_FPS_LOCK | #896EA4, #B705BD |
| CUSA18097 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #C3F84F |
| CUSA18278 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 208s | #9F031E |
| CUSA18534 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 58s | #215A4E, #D2FA02, #FAE379 |
| CUSA18648 | **CRASH LOGGED** | Base/Slim/Pro; 1920x1080/3840x2160 | crash marker; causality unproven; inert/safe/off observation | #22A7AF, #2A0FE1, #754B0B |
| CUSA18676 | **CRASH LOGGED** | Base/Slim/Pro; 1920x1080 | crash marker; causality unproven; inert/safe/off observation | #0EF368, #1ABC0D |
| CUSA18723 | **MIXED** | Base/Slim/Pro; 1920x1080/3200x1800 | FG logged; trace reaches 229s; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #0714F0, #64659D, #7107A4 |
| CUSA18740 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation | #2279F6 |
| CUSA18742 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation | #2A6317, #443EAA |
| CUSA19177 | **SHORT FG TRACE** | Base/Slim; 1600x900 | FG logged; trace reaches 28s | #A840A4 |
| CUSA20177 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation | #1802EB, #52AEC2, #D801F3 |
| CUSA20499 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 129s | #90957B |
| CUSA23264 | **FG OBSERVED** | Pro; 3840x2160 | FG logged; trace reaches 524s | #357B27 |
| CUSA23265 | **SHORT FG TRACE** | Pro; 3840x2160 | FG logged; trace reaches 78s | #64659D |
| CUSA23827 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #8EF430 |
| CUSA24103 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 261s | #30E690, #4AAB26, #76EE4E |
| CUSA24705 | **SHORT FG TRACE** | Pro; 3200x1800 | FG logged; trace reaches 41s; community v2.35 report: base console ~20 real/~40 output FPS and crash dump | #1153E9 |
| CUSA24853 | **FG INACTIVE** | Pro; 2560x1440 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #1B1B33, #4F9511 |
| CUSA24899 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #0714F0, #2CCC23, #5E6B6B |
| CUSA25651 | **SHORT FG TRACE** | Base/Slim; 1600x900 | FG logged; trace reaches 25s | #CE674C |
| CUSA25761 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #3BC277 |
| CUSA26491 | **INSUFFICIENT** | Base/Slim; 1600x900 | symptom flag: LOW_FPS_LOCK; startup/trace only; no generated output proven | #40A31D |
| CUSA26618 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #06AB6B, #3F03C9 |
| CUSA26619 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #41AE24 |
| CUSA26967 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 38s | #2CB5D7, #3EFAE3 |
| CUSA26996 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #71F077, #C1ACC7 |
| CUSA27043 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #9E405F, #A73426, #A97896 |
| CUSA27110 | **INSUFFICIENT** | Base/Slim; 1920x1080 | symptom flag: AUDIO_DM_STARVATION; startup/trace only; no generated output proven | #3DE4BD |
| CUSA27309 | **INSUFFICIENT** | Pro; 1920x1080 | symptom flag: LOW_FPS_LOCK; startup/trace only; no generated output proven | #E9EC57 |
| CUSA27372 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #46D833 |
| CUSA27484 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 11s; crash marker; causality unproven | #167730, #357B27, #4B800C |
| CUSA27793 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 14s | #67F20D |
| CUSA28561 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 438s; inert/safe/off observation | #2258DA, #7AF108, #95B2AB |
| CUSA28863 | **MIXED** | Base/Slim/Pro; 1280x720/1920x1080/3200x1800 | FG logged; trace reaches 11556s; crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #22F0D1, #61440E, #64B8F2 |
| CUSA28972 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #357B27 |
| CUSA29199 | **MIXED** | Pro; 1920x1080 | FG logged; trace reaches 15s; crash marker; causality unproven; symptom flag: LOW_FPS_LOCK | #4736C3, #660D34, #6736A1 |
| CUSA30582 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #0E3A7F |
| CUSA30856 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 10s; crash marker; causality unproven | #1AB45C, #4964DE, #5B3753 |
| CUSA31498 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 249s; symptom flag: LOW_FPS_LOCK | #4B85F2 |
| CUSA31580 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 15s | #B3708D |
| CUSA31992 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 12s | #5B1B94 |
| CUSA32184 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 27s | #B054D8 |
| CUSA32836 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation | #383637, #A73426, #AC0A96 |
| CUSA33151 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 10316s | #0E3A7F, #E7EE5D |
| CUSA33334 | **MIXED** | Pro; 1920x1080 | FG logged; trace reaches 27s; crash marker; causality unproven | #72EF25 |
| CUSA33387 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 699s; crash marker; causality unproven | #43FF07, #9E598C, #AE21C6 |
| CUSA33388 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 3813s; crash marker; causality unproven; inert/safe/off observation | #086BB6, #22F0D1, #896EA4 |
| CUSA33534 | **INSUFFICIENT** | Base/Slim; 1920x1080 | startup/trace only; no generated output proven | #718F50 |
| CUSA33778 | **CRASH LOGGED** | Pro; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #E74C1D |
| CUSA34384 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1501s; inert/safe/off observation | #8627F4, #A8C2AF |
| CUSA34582 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 19s | #96531B |
| CUSA36464 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 20s | #658E45 |
| CUSA36843 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 655s; inert/safe/off observation | #210FC7, #4CCECC, #8BFEC2 |
| CUSA36848 | **CRASH LOGGED** | Pro; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #CF4F48 |
| CUSA38355 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 25s | #BB8A82 |
| CUSA39420 | **SHORT FG TRACE** | Base/Slim/Pro; 1920x1080/2560x1440 | FG logged; trace reaches 19s; v2.35 release also lists memory-pressure crashes | #3AC417, #586F0E |
| CUSA42881 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #196C7E, #714CD0, #794FAF |
| CUSA43772 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 20s; crash marker; causality unproven | #0079E2, #C374DD, #D2687D |
| CUSA44317 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 16s | #E6A8C7 |
| CUSA44372 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1140s | #BFEE8D |
| CUSA44732 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker; causality unproven; inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #103064, #493523, #F8862A |
| CUSA45096 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 19s | #557003 |
| CUSA45894 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 17s | #F559EA |
| CUSA46876 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; symptom flag: LOW_FPS_LOCK | #2CCC23, #5E6B6B, #6B7092 |
| CUSA47038 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 123s; crash marker; causality unproven | #A2852D |
| CUSA48576 | **INSUFFICIENT** | unknown; 1920x1080 | startup/trace only; no generated output proven | #3311D0 |
| CUSA52342 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 86s; symptom flag: AUDIO_DM_STARVATION | #71F077, #C1ACC7 |
| CUSA55613 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 22s | #F37736 |
| CUSA57220 | **FG OBSERVED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 8737s | #14142F, #C143A9 |
| CUSA57547 | **FG OBSERVED** | Base/Slim/Pro; 1920x1080 | FG logged; trace reaches 1299s | #383637, #AC0A96, #D0C002 |
| CUSA67411 | **INSUFFICIENT** | Base/Slim; resolution unknown | startup/trace only; no generated output proven | #7C604C |

## Additional community evidence

[Report from 1 November](https://discord.com/channels/1561812282273431703/1561812285608034305/1566343015943897168) explicitly attributes **CUSA24705 to v2.35**, on a base console: approximately 30 FPS with FG off, approximately 20 real / 40 output FPS with FG on, with a crash dump in the attachment. It is a community report, not a measured benchmark. The same message attributes CUSA18097 to v2.36, so that part is excluded here.

## Version-specific release limitations

The [v2.35 release notes](https://github.com/Melnikoff07/vcfg/releases/tag/v2.35) identify memory-pressure crashes for CUSA39420, CUSA10213, CUSA02962 and CUSA04841. They also warn that CUSA10249 on Pro can crash at startup or disable FG; turn FG off there if affected. They describe remaining presentation-queue latency and residual contour flicker. Targeted image-quality fixes and successful memory replays do not establish universal gameplay compatibility.

For new issues, report the version you actually ran. v2.35 observations remain historical; retest on the current release before claiming a current regression. [Report instructions](CONTRIBUTING.md).
