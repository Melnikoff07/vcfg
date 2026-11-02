# Compatibility — v2.36

**Evidence audit: 2 November 2026 (Moscow).** [Release notes](https://github.com/Melnikoff07/vcfg/releases/tag/v2.36).

Reviewed **4,793 SHA-256-verified original text files** (4,798 saved attachment records), including all 4,760 VPS records in the intake snapshot. VPS cutoff: **14:46 MSK**; Discord collection cutoff: **14:36 MSK**. Also searched 4,828 stored Discord messages. The 196 skipped attachments were media/packages/other non-log files, not omitted text logs.

The archive contains 138 files with only v2.36 session stamps, 4,381 historical files, 273 mixed-version files and 1 without an attributable plugin version. There are **49 CUSA IDs with eligible v2.36 runtime evidence** and 629 across all archived explicit plugin versions.

## Read the results correctly

- **FG OBSERVED:** generated presentations are recorded and the trace reaches at least 120 seconds; no competing crash/inactive or telemetry-symptom observation in this eligible sample. This does not establish visual quality, audio, gameplay coverage or long-term stability.
- **SHORT FG TRACE:** generated output is recorded, but the available trace is shorter than 120 seconds.
- **MIXED:** generated output plus crash/inactive or telemetry-symptom evidence exist for this CUSA; configurations and launches can differ.
- **CRASH LOGGED:** a crash marker exists and no positive generated-output observation met the criteria. This does not prove VCFG caused the crash.
- **FG INACTIVE:** inert/safe/off state is recorded without proven generation. User-disabled FG is not proof of incompatibility.
- **INSUFFICIENT:** startup or runtime evidence exists, but it proves neither generated output nor a crash.
- **UNSUPPORTED:** a known release limitation overrides short positive telemetry.

Each row is specific to the **exact CUSA**, not every regional alias. No support is inferred from the launcher list, installed-game inventory, release-fix list, or a host trace replay. Current/previous files and boot/crash records are separate observations; repeated uploads are deduplicated by section content, not counted as independent users or sessions. Mixed-version originals and unversioned sections are excluded from the primary v2.36 verdicts. A version stamp does not verify the exact PRX binary hash.

Trace elapsed time below is time since startup in the strongest positive trace, **not verified gameplay duration or continuous FG time**. Hardware is reported only when a runtime `neo` marker is available; unknown values are left unknown. Resolution is the observed source-buffer layout, not the TV/HDR output mode. The artifact does not establish a game update version. See the [evidence index](compatibility-evidence.json) for report IDs, original SHA-256 and section SHA-256.

## Current runtime evidence

| CUSA | Result | Console / source buffers | Observation | Evidence |
|---|---|---|---|---|
| CUSA00191 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 332s; crash marker in boot/trace; causality unproven; inert/safe/off observation | #1960FC |
| CUSA00265 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 23s | #B60B4A |
| CUSA00359 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 7s; crash marker in boot/trace; causality unproven | #ED2D8C |
| CUSA00411 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 301s; crash marker in boot/trace; causality unproven | #A8FD0C, #C979FF, #ED0A40 |
| CUSA00419 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 288s | #899A18 |
| CUSA00462 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1110s | #5A1A3B, #6F766D |
| CUSA00744 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 1034s | #5A1A3B, #6F766D |
| CUSA01015 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; telemetry symptom flag: LOW_FPS_LOCK | #D07DD6 |
| CUSA03041 | **MIXED** | Pro; 1920x1080 | FG logged; trace reaches 1082s; inert/safe/off observation | #EF1FC9 |
| CUSA04488 | **INSUFFICIENT** | unknown; 1920x1080 | startup/trace only; no generated output proven | #58870A, #61A97F |
| CUSA07319 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 22s | #2347CC |
| CUSA07820 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 118s; crash marker in boot/trace; causality unproven; inert/safe/off observation | #166190, #42074A, #46A7AC |
| CUSA07875 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 50s; crash marker in boot/trace; causality unproven | #6E70CE |
| CUSA07990 | **INSUFFICIENT** | unknown; 1920x1080 | startup/trace only; no generated output proven | #8B1496 |
| CUSA08519 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation | #4F5071, #710086, #9B22E5 |
| CUSA09249 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation | #2BFB41 |
| CUSA10212 | **CRASH LOGGED** | Pro; 3840x2160 | crash marker in boot/trace; causality unproven | #9100DC |
| CUSA10213 | **CRASH LOGGED** | Pro; 3840x2160 | crash marker in boot/trace; causality unproven; inert/safe/off observation | #646E39, #A9449F |
| CUSA10237 | **UNSUPPORTED** | Base/Slim; 1920x1080 | disabled per v2.36 release notes; short FG telemetry does not override the known crash | #10295D, #2DE659, #5D4E98 |
| CUSA10326 | **FG OBSERVED** | Pro; 2560x1440 | FG logged; trace reaches 181s | #EF1FC9 |
| CUSA11456 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 496s; crash marker in boot/trace; causality unproven; inert/safe/off observation | #4CDABA, #7E9166, #AD98C9 |
| CUSA11995 | **INSUFFICIENT** | Pro; 3840x2160 | startup/trace only; no generated output proven | #06A49F |
| CUSA12771 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation | #730C8B, #95B810, #FF5C7A |
| CUSA13323 | **MIXED** | Base/Slim/Pro; 1920x1080/3840x2160 | FG logged; trace reaches 331s; crash marker in boot/trace; causality unproven; inert/safe/off observation | #E57A63, #FBDAE3 |
| CUSA14655 | **FG INACTIVE** | Pro; 3840x2160 | inert/safe/off observation | #EF1FC9 |
| CUSA15633 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 132s; inert/safe/off observation; telemetry symptom flag: LOW_FPS_LOCK | #B54EF8, #BF88B8, #CA9827 |
| CUSA18097 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation; current community report: menu hotkey does not open overlay | #EF1FC9 |
| CUSA18676 | **CRASH LOGGED** | Base/Slim; resolution unknown | crash marker in boot/trace; causality unproven | #752535 |
| CUSA18723 | **CRASH LOGGED** | Pro; 3200x1800 | crash marker in boot/trace; causality unproven | #997848 |
| CUSA18740 | **CRASH LOGGED** | Base/Slim/Pro; 1920x1080/3840x2160 | crash marker in boot/trace; causality unproven; inert/safe/off observation | #3CF4F3, #761D46, #96A3F4 |
| CUSA24705 | **SHORT FG TRACE** | Pro; 3200x1800 | FG logged; trace reaches 37s | #F52488 |
| CUSA26347 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 25s | #682E90, #9586BB |
| CUSA26506 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 39s | #19A88A |
| CUSA26619 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation | #D07DD6 |
| CUSA28561 | **MIXED** | Base/Slim/Pro; 1920x1080/3200x1800 | FG logged; trace reaches 44s; crash marker in boot/trace; causality unproven; telemetry symptom flag: AUDIO_DM_STARVATION | #0A8A2C, #40596A |
| CUSA28863 | **FG INACTIVE** | Pro; 3200x1800 | inert/safe/off observation | #EF1FC9 |
| CUSA31695 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 20s | #2FC876 |
| CUSA33151 | **FG OBSERVED** | Base/Slim/Pro; 1920x1080/2560x1440 | FG logged; trace reaches 323s | #4F26A5, #8EC4C8 |
| CUSA33387 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker in boot/trace; causality unproven | #EB48B6 |
| CUSA33388 | **FG OBSERVED** | Base/Slim; 1280x720/1920x1080 | FG logged; trace reaches 121s | #79C111 |
| CUSA34016 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation | #A8FD0C |
| CUSA36843 | **FG OBSERVED** | Base/Slim; 1920x1080 | FG logged; trace reaches 970s | #D07DD6 |
| CUSA42378 | **INSUFFICIENT** | Pro; 1920x1080 | startup/trace only; no generated output proven | #ED24E7 |
| CUSA43772 | **SHORT FG TRACE** | Base/Slim; 1920x1080 | FG logged; trace reaches 18s | #04F9F0, #9CC47B |
| CUSA43932 | **CRASH LOGGED** | Base/Slim; 1920x1080 | crash marker in boot/trace; causality unproven | #55A4CC |
| CUSA44372 | **FG INACTIVE** | Base/Slim; 1920x1080 | inert/safe/off observation; telemetry symptom flag: LOW_FPS_LOCK | #899A18 |
| CUSA44878 | **FG INACTIVE** | Pro; 1920x1080 | inert/safe/off observation | #880803 |
| CUSA57220 | **MIXED** | Base/Slim; 1920x1080 | FG logged; trace reaches 79s; inert/safe/off observation; telemetry symptom flag: LOW_FPS_LOCK | #899A18, #D07DD6 |
| CUSA81396 | **INSUFFICIENT** | unknown; 1920x1080 | startup/trace only; no generated output proven | #66EF5E |

## Additional community evidence

[CUSA18097 report, 1 November](https://discord.com/channels/1561812282273431703/1561812285608034305/1566343015943897168): the author explicitly reports v2.36 on a base console, no crash, and a non-opening overlay hotkey. This is a **PARTIAL community result**, not proof of active FG. The same message labels CUSA24705 as **v2.35**; that complaint is historical and is not reassigned to v2.36. Pre-release development summaries are not counted as user compatibility tests.

## Known release limitations

- **CUSA10237 remains unsupported:** disable VCFG for it. The release reports native 60 FPS and a crash with VCFG on base PS4; short generated-frame traces do not establish a working gameplay session.
- Keep only one VCFG plugin active. Other VCFG PRX files or frame-rate/scaler plugins can cause crashes.
- Repeating-texture shimmer, thin-object flicker and bright-sky character halos are reduced, not eliminated.
- Fast timer pacing is excluded for the CUSA01439 family.

## Historical archive

[Historical runtime index](COMPATIBILITY_HISTORY.md) covers CUSA IDs with explicit older-version evidence. These results are **not current compatibility claims**. Current release fixes and historical successful starts do not replace a v2.36 retest.

## Report or correct a result

Include exact VCFG build, CUSA/game update, console/firmware/GoldHEN, resolution/HDR, Quality/Fast/Sharp, engine/buffers, AUTO/CUSTOM and recipe, other plugins, reproduction, duration and FG OFF comparison. Attach `vcfg_report.txt` or a successful upload `#ID`; use the [reporting workflow](CONTRIBUTING.md). A title absent from this table has no eligible v2.36 runtime observation in this snapshot, not an automatic failure.
