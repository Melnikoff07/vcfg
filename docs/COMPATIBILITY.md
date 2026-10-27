# Compatibility

> **Current release: [v2.35](https://github.com/Melnikoff07/vcfg/releases/tag/v2.35).** The table below is a
> historical log snapshot, not a live support list. The v2.34 release notes describe a built-in FG profile and
> HDR10 handling for CUSA13323/CUSA11456, an audio fix for CUSA10249, and targeted memory/present fixes for
> CUSA08809/CUSA03468. These changes do not prove that every update, console or output mode works. For a useful
> new report, include exact VCFG version, CUSA/update, console/output mode, settings, result in gameplay, and
> `vcfg_report.txt` when something fails.

Which games run with VCFG, built from real session logs that users sent in (versions 2.33 and 2.34; a few titles also 2.35). Games are listed by Title ID only. Counts are per deduplicated session, so a title with a low ratio worked on some launches and failed on others (usually a memory-related crash at start or loading); the ratio is also affected by which settings and firmware the sender had.

**217 titles seen:** 51 work, 41 work with caveats, 44 do not work on 2.34, 81 have no 2.34 data yet.

## How a title is classified

- **Good session**: frame generation really ran (generated frames shown, 30→60), for at least about 1000 frames, and the session did not crash.
- **Works**: at least one good session and no crashes, flicker reports or real failures in any other session.
- **Works with caveats**: at least one good session, but other sessions of the same title crashed or ended with frame generation inactive. Try again, or see [CONTRIBUTING.md](CONTRIBUTING.md) to send a log.
- **Not working**: tested on 2.34, no good session. The main reason from the logs is given.
- **No data for 2.34**: only older logs exist (2.33), none good. Not a verdict on the current version, please test and send a log.

Hardware: *Base* = PS4 / PS4 Slim, *Pro* = PS4 Pro, *Both* = confirmed on both. This is a log-based list, not a guarantee: a result can depend on your game version, patch and settings.

## Works

| Title ID | Versions | Good / total sessions | Hardware | Notes |
| --- | --- | --- | --- | --- |
| CUSA00010 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2314) |
| CUSA00049 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=1067) |
| CUSA00093 | 2.33 | 4 / 4 (100%) | Base | 30→60 confirmed (max mids=821) |
| CUSA00135 | 2.33 | 2 / 3 (67%) | Base | 30→60 confirmed (max mids=2322); 1x short/early exit (<1000f) |
| CUSA00362 | 2.34 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2339) |
| CUSA00663 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2196) |
| CUSA00893 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=1613) |
| CUSA00900 | 2.33, 2.34 | 5 / 5 (100%) | Base | 30→60 confirmed (max mids=2323) |
| CUSA00917 | 2.33, 2.34 | 3 / 4 (75%) | Base | 30→60 confirmed (max mids=2504); 1x short/early exit (<1000f) |
| CUSA01127 | 2.34 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2397) |
| CUSA01765 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=1926) |
| CUSA01766 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=839) |
| CUSA02291 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=928) |
| CUSA03185 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=1662) |
| CUSA03309 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=828) |
| CUSA03528 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2260) |
| CUSA03888 | 2.34 | 2 / 2 (100%) | Pro | 30→60 confirmed (max mids=2278) |
| CUSA04885 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=953) |
| CUSA05625 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2189) |
| CUSA06014 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=976) |
| CUSA06166 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=3182) |
| CUSA06534 | 2.34 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=580) |
| CUSA06840 | 2.34 | 1 / 2 (50%) | Base | 30→60 confirmed (max mids=1189); 1x short/early exit (<1000f) |
| CUSA08600 | 2.33 | 1 / 1 (100%) | Pro | 30→60 confirmed (max mids=1575) |
| CUSA09032 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2158) |
| CUSA10326 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=726) |
| CUSA10706 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=802) |
| CUSA10812 | 2.34 | 3 / 3 (100%) | Base | 30→60 confirmed (max mids=2069) |
| CUSA11155 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=1599) |
| CUSA11395 | 2.34 | 2 / 2 (100%) | Pro | 30→60 confirmed (max mids=2303) |
| CUSA11407 | 2.34 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2300) |
| CUSA11638 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2023) |
| CUSA14204 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=562) |
| CUSA15032 | 2.34 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2338) |
| CUSA15436 | 2.34 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2269) |
| CUSA16163 | 2.34 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2020) |
| CUSA16275 | 2.34 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=1048) |
| CUSA16579 | 2.33, 2.34 | 2 / 3 (67%) | Base | 30→60 confirmed (max mids=2284); 1x short/early exit (<1000f) |
| CUSA17419 | 2.34 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2102) |
| CUSA17425 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=1472) |
| CUSA18534 | 2.33 | 4 / 4 (100%) | Base | 30→60 confirmed (max mids=2227) |
| CUSA20373 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2216) |
| CUSA23264 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2278) |
| CUSA23265 | 2.34 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2278) |
| CUSA24103 | 2.34 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2004) |
| CUSA28863 | 2.33, 2.34 | 2 / 4 (50%) | Base | 30→60 confirmed (max mids=2311); 2x short/early exit (<1000f) |
| CUSA33151 | 2.33 | 1 / 1 (100%) | Base | 30→60 confirmed (max mids=2229) |
| CUSA44422 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=2326) |
| CUSA52342 | 2.33 | 2 / 2 (100%) | Base | 30→60 confirmed (max mids=1633) |
| CUSA57220 | 2.33, 2.34 | 10 / 12 (83%) | Base | 30→60 confirmed (max mids=2020); 2x short/early exit (<1000f) |
| CUSA57548 | 2.33, 2.34 | 5 / 5 (100%) | Base | 30→60 confirmed (max mids=2447) |

## Works with caveats

| Title ID | Versions | Good / total sessions | Hardware | Notes |
| --- | --- | --- | --- | --- |
| CUSA00003 | 2.33 | 3 / 5 (60%) | Base | 30→60 confirmed (max mids=554); caveat: inert: INERT: NO MBUF (2)(2) |
| CUSA00265 | 2.33, 2.34 | 2 / 4 (50%) | Base | 30→60 confirmed (max mids=2128); caveat: inert: 60FPS NATIVE (FG OFF)(2) |
| CUSA00341 | 2.33 | 2 / 13 (15%) | Base | 30→60 confirmed (max mids=1983); caveat: crashes: SIGBUS(8)/SIGSEGV(1) |
| CUSA00411 | 2.33, 2.34 | 16 / 36 (44%) | Base | 30→60 confirmed (max mids=2553); caveat: crashes: SIGBUS(11)/SIGSEGV(1) |
| CUSA00419 | 2.33, 2.34 | 31 / 71 (44%) | Base | 30→60 confirmed (max mids=2691); caveat: crashes: SIGBUS(17)/SIGSEGV(14), inert: NO HOOK FLIPS(2)/INERT: POST ONLY (NO FG)(1) |
| CUSA00918 | 2.33 | 4 / 12 (33%) | Base | 30→60 confirmed (max mids=1361); caveat: crashes: SIGBUS(7)/SIGSEGV(1) |
| CUSA01004 | 2.33, 2.34 | 3 / 7 (43%) | Base | 30→60 confirmed (max mids=2253); caveat: crashes: SIGSEGV(2), inert: INERT: NO MBUF (16)(1) |
| CUSA01015 | 2.33, 2.34 | 6 / 16 (38%) | Both | 30→60 confirmed (max mids=2338); caveat: crashes: SIGSEGV (ENOMEM/shave)(1), inert: 60FPS NATIVE (FG OFF)(3)/INERT: NO FG ACTIVITY(2) |
| CUSA01566 | 2.34 | 1 / 2 (50%) | Base | 30→60 confirmed (max mids=1015); caveat: inert: INERT: MISSED MIDS(1) |
| CUSA02299 | 2.33, 2.34 | 7 / 12 (58%) | Both | 30→60 confirmed (max mids=1364); caveat: crashes: SIGSEGV(1), inert: INERT: MISSED MIDS(2)/INERT: HDR/FMT 0x88000000(1) |
| CUSA02962 | 2.33, 2.34 | 1 / 11 (9%) | Base | 30→60 confirmed (max mids=2338); caveat: crashes: SIGSEGV(9) |
| CUSA03041 | 2.33, 2.34 | 22 / 40 (55%) | Both | 30→60 confirmed (max mids=2416); caveat: crashes: SIGBUS(11)/SIGSEGV(1), inert: STALL (0 FPS HANG)(1) |
| CUSA04459 | 2.33, 2.34 | 2 / 4 (50%) | Base | 30→60 confirmed (max mids=2402); caveat: inert: STALL (0 FPS HANG)(2) |
| CUSA04841 | 2.33, 2.34 | 1 / 6 (17%) | Base | 30→60 confirmed (max mids=779); caveat: crashes: SIGSEGV(3), inert: STALL (0 FPS HANG)(1)/INERT: NO FG ACTIVITY(1) |
| CUSA05571 | 2.33, 2.34 | 1 / 2 (50%) | Base | 30→60 confirmed (max mids=2313); caveat: inert: INERT: NO FG ACTIVITY(1) |
| CUSA07399 | 2.33 | 3 / 6 (50%) | Base | 30→60 confirmed (max mids=2493); caveat: inert: INERT: HDR/FMT 0x88740000(2) |
| CUSA07408 | 2.33, 2.34 | 8 / 9 (89%) | Base | 30→60 confirmed (max mids=2645); caveat: crashes: SIGSEGV(1) |
| CUSA07410 | 2.33 | 2 / 6 (33%) | Both | 30→60 confirmed (max mids=1937); caveat: crashes: SIGSEGV(2), inert: INERT: POST ONLY (NO FG)(2) |
| CUSA07820 | 2.33, 2.34 | 7 / 27 (26%) | Both | 30→60 confirmed (max mids=2256); caveat: crashes: SIGBUS(14), inert: INERT: NO MBUF (3)(4)/INERT: SAFE GIVEN UP(2) |
| CUSA08519 | 2.33, 2.34, 2.35 | 43 / 79 (54%) | Both | 30→60 confirmed (max mids=2418); caveat: crashes: SIGBUS(14)/SIGSEGV(8), inert: INERT: NO FG ACTIVITY(2)/INERT: NO MBUF (16)(1) |
| CUSA08809 | 2.33 | 2 / 22 (9%) | Both | 30→60 confirmed (max mids=2493); caveat: crashes: SIGSEGV(9)/SIGBUS(4), inert: STALL (0 FPS HANG)(3)/INERT: NO MBUF (2)(1) |
| CUSA08966 | 2.33, 2.34 | 3 / 13 (23%) | Both | 30→60 confirmed (max mids=2360); caveat: crashes: SIGSEGV(3)/SIGABRT(1), inert: NO HOOK FLIPS(1) |
| CUSA09171 | 2.33, 2.34 | 2 / 5 (40%) | Base | 30→60 confirmed (max mids=2378); caveat: inert: 60FPS NATIVE (FG OFF)(1) |
| CUSA10249 | 2.33, 2.34 | 2 / 22 (9%) | Both | 30→60 confirmed (max mids=2319); caveat: crashes: SIGSEGV(7)/SIGBUS(4), inert: INERT: NO MBUF (3)(4)/INERT: SAFE NO DISPLAY MEM(3) |
| CUSA10865 | 2.33 | 1 / 3 (33%) | Base | 30→60 confirmed (max mids=1527); caveat: crashes: SIGSEGV(2) |
| CUSA11260 | 2.33, 2.34 | 1 / 7 (14%) | Base | 30→60 confirmed (max mids=2054); caveat: crashes: SIGSEGV(3)/SIGSEGV (ENOMEM/shave)(1) |
| CUSA11456 | 2.33 | 7 / 67 (10%) | Both | 30→60 confirmed (max mids=1313); caveat: crashes: SIGSEGV(49)/SIGILL(4), inert: INERT: SAFE NO DISPLAY MEM(2)/60FPS NATIVE (FG OFF)(1) |
| CUSA12047 | 2.33, 2.34 | 1 / 3 (33%) | Base | 30→60 confirmed (max mids=2082); caveat: inert: INERT: MISSED MIDS(2) |
| CUSA12605 | 2.33 | 1 / 3 (33%) | Base | 30→60 confirmed (max mids=1379); caveat: crashes: SIGILL(2) |
| CUSA13323 | 2.33, 2.34 | 13 / 92 (14%) | Both | 30→60 confirmed (max mids=2497); caveat: crashes: SIGSEGV(42)/SIGILL(15), inert: STALL (0 FPS HANG)(2)/INERT: SAFE NO DISPLAY MEM(1) |
| CUSA13795 | 2.33, 2.34 | 1 / 3 (33%) | Base | 30→60 confirmed (max mids=236); caveat: inert: INERT: MISSED MIDS(2) |
| CUSA13801 | 2.33 | 5 / 7 (71%) | Both | 30→60 confirmed (max mids=2384); caveat: inert: INERT: NO MBUF (3)(2) |
| CUSA14876 | 2.33 | 3 / 10 (30%) | Both | 30→60 confirmed (max mids=2042); caveat: crashes: SIGFPE(1), inert: INERT: MISSED MIDS(4)/INERT: NO MBUF (2)(1) |
| CUSA15027 | 2.33, 2.34 | 3 / 5 (60%) | Base | 30→60 confirmed (max mids=2261); caveat: crashes: SIGABRT (ENOMEM/shave)(1) |
| CUSA15467 | 2.33 | 1 / 2 (50%) | Base | 30→60 confirmed (max mids=877); caveat: inert: NO HOOK FLIPS(1) |
| CUSA16596 | 2.33, 2.34 | 3 / 7 (43%) | Both | 30→60 confirmed (max mids=2075); caveat: crashes: SIGBUS (ENOMEM/shave)(1)/SIGSEGV(1), inert: INERT: SAFE GIVEN UP(2) |
| CUSA18723 | 2.33, 2.34 | 6 / 12 (50%) | Both | 30→60 confirmed (max mids=2224); caveat: inert: INERT: NO MBUF (3)(4) |
| CUSA33387 | 2.33, 2.34 | 2 / 5 (40%) | Base | 30→60 confirmed (max mids=2242); caveat: crashes: SIGSEGV(1), inert: NO HOOK FLIPS(1) |
| CUSA33388 | 2.33, 2.34 | 15 / 25 (60%) | Both | 30→60 confirmed (max mids=2314); caveat: crashes: SIGSEGV(3), inert: 60FPS NATIVE (FG OFF)(3)/INERT: NO MBUF (3)(2) |
| CUSA34384 | 2.33, 2.34 | 1 / 6 (17%) | Both | 30→60 confirmed (max mids=2226); caveat: crashes: SIGSEGV(3)/SIGBUS(1), inert: NO HOOK FLIPS(1) |
| CUSA36843 | 2.33, 2.34 | 9 / 11 (82%) | Both | 30→60 confirmed (max mids=2295); caveat: inert: 60FPS NATIVE (FG OFF)(2) |

## Not working on 2.34

| Title ID | Versions | Sessions | Hardware | Main reason |
| --- | --- | --- | --- | --- |
| CUSA00004 | 2.33, 2.34 | 4 | Base | INERT: NO MBUF (4) |
| CUSA00190 | 2.34 | 4 | Base | SIGBUS |
| CUSA00220 | 2.34 | 2 | Base | INERT: MISSED MIDS |
| CUSA00223 | 2.33, 2.34 | 7 | Both | INERT: NO MBUF (4) |
| CUSA00239 | 2.34 | 1 | Base | INERT: HDR/FMT 0xc1060000 |
| CUSA00683 | 2.34 | 3 | Base | STALL (0 FPS HANG) |
| CUSA02000 | 2.34 | 2 | Base | INERT: NO MBUF (2) |
| CUSA02010 | 2.34 | 1 | Base | ENOMEM PRE-F120 |
| CUSA02320 | 2.33, 2.34 | 4 | Base | INERT: NO MBUF (3) |
| CUSA03617 | 2.34 | 1 | Base | INERT: NO MBUF (2) |
| CUSA04284 | 2.34 | 2 | Base | STALL (0 FPS HANG) |
| CUSA04607 | 2.33, 2.34 | 2 | Both | INERT: NO MBUF (2) |
| CUSA04865 | 2.33, 2.34 | 3 | Base | SIGSEGV |
| CUSA05794 | 2.33, 2.34 | 3 | Base | STALL (0 FPS HANG) |
| CUSA06782 | 2.33, 2.34 | 7 | Base | SIGSEGV (ENOMEM/shave) |
| CUSA07671 | 2.34 | 1 | Unknown | SIGABRT (ENOMEM/shave) |
| CUSA08025 | 2.34 | 1 | Base | INERT: NO MBUF (2) |
| CUSA09303 | 2.34 | 1 | Base | SIGSEGV |
| CUSA10213 | 2.33, 2.34 | 18 | Both | SIGILL |
| CUSA10237 | 2.33, 2.34 | 15 | Base | SIGILL |
| CUSA11993 | 2.33, 2.34 | 5 | Base | INERT: MISSED MIDS |
| CUSA11995 | 2.33, 2.34 | 5 | Base | INERT: MISSED MIDS |
| CUSA12529 | 2.33, 2.34 | 2 | Base | INERT: NO MBUF (2) |
| CUSA12555 | 2.33, 2.34 | 3 | Base | SIGABRT |
| CUSA12771 | 2.33, 2.34 | 3 | Base | INERT: NO MBUF (2) |
| CUSA13055 | 2.33, 2.34 | 2 | Base | INERT: NO MBUF (2) |
| CUSA14835 | 2.33, 2.34 | 3 | Base | INERT: NO MBUF (2) |
| CUSA15090 | 2.33, 2.34 | 8 | Base | INERT: NO MBUF (3) |
| CUSA16746 | 2.34 | 1 | Base | INERT: NO MBUF (2) |
| CUSA17776 | 2.34 | 2 | Base | INERT: SAFE NO WORK MEM |
| CUSA18097 | 2.33, 2.34 | 3 | Base | INERT: NO MBUF (2) |
| CUSA18824 | 2.34 | 1 | Base | SIGSEGV |
| CUSA20133 | 2.34 | 1 | Pro | INERT: NO MBUF (2) |
| CUSA25272 | 2.34 | 1 | Base | INERT: NO MBUF (2) |
| CUSA28561 | 2.33, 2.34 | 13 | Base | SIGSEGV |
| CUSA31855 | 2.34 | 2 | Base | STALL (0 FPS HANG) |
| CUSA32536 | 2.34 | 1 | Base | SIGSEGV |
| CUSA32836 | 2.34 | 1 | Base | STALL (0 FPS HANG) |
| CUSA36847 | 2.33, 2.34 | 5 | Base | INERT: NO MBUF (2) |
| CUSA36848 | 2.33, 2.34 | 5 | Base | INERT: NO MBUF (2) |
| CUSA39420 | 2.33, 2.34 | 7 | Base | SIGSEGV |
| CUSA43772 | 2.33, 2.34 | 3 | Base | SIGSEGV |
| CUSA44317 | 2.33, 2.34 | 2 | Base | SIGSEGV |
| CUSA56425 | 2.34 | 1 | Unknown | SIGBUS |

## No data for 2.34

Only 2.33 logs exist and none was good. Title IDs: CUSA00018, CUSA00194, CUSA00556, CUSA00744, CUSA01623, CUSA01967, CUSA02412, CUSA03468, CUSA03522, CUSA03946, CUSA04294, CUSA04529, CUSA04602, CUSA05379, CUSA05574, CUSA05720, CUSA05725, CUSA06548, CUSA07187, CUSA07211, CUSA07439, CUSA07875, CUSA07990, CUSA08252, CUSA08992, CUSA09175, CUSA09193, CUSA09313, CUSA10001, CUSA10002, CUSA10211, CUSA10212, CUSA10872, CUSA11690, CUSA12025, CUSA12398, CUSA12979, CUSA13529, CUSA13793, CUSA13917, CUSA15633, CUSA17195, CUSA17722, CUSA18201, CUSA18560, CUSA18648, CUSA18676, CUSA18740, CUSA18742, CUSA18750, CUSA19177, CUSA19549, CUSA19925, CUSA20177, CUSA24705, CUSA25453, CUSA25651, CUSA26506, CUSA26618, CUSA27106, CUSA28260, CUSA28261, CUSA28676, CUSA30313, CUSA33324, CUSA34388, CUSA35639, CUSA35761, CUSA37521, CUSA37522, CUSA42359, CUSA42503, CUSA44372, CUSA45905, CUSA48310, CUSA48576, CUSA49957, CUSA54862, CUSA56720, CUSA84473, CUSA89978.

## Add your game

Run the game with VCFG and send the log (see [CONTRIBUTING.md](CONTRIBUTING.md)); once a version 2.34 or newer session is in, the title moves into the right list. Earlier hand-written notes (v2.11 and older, discovery-plugin results) are superseded by this list.
