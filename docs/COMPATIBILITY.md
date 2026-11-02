# Compatibility — v2.36

Current release: [v2.36, 1 November 2026](https://github.com/Melnikoff07/vcfg/releases/tag/v2.36).
VCFG is experimental. A profile or targeted fix does not guarantee every game update, console model or output mode.
Use CUSA IDs when reporting compatibility.

## Known issues

- **CUSA10237:** unsupported; the release notes report native 60 FPS and a crash with VCFG on base PS4. Disable VCFG for this title in the launcher.
- Keep only one VCFG plugin active. Multiple VCFG PRX files or other frame-rate/scaler plugins can cause crashes.
- Repeating-texture shimmer, thin-object flicker and bright-sky character halos are reduced, not eliminated.
- Fast timer pacing is excluded for the CUSA01439 family.

## Targeted changes in this release

These entries summarize the release notes, not a universal hardware certification table.

| CUSA IDs | v2.36 change |
|---|---|
| CUSA04529 family: CUSA00341, CUSA00917, CUSA07820, CUSA10249 | Audio fix on base PS4 / Slim |
| CUSA36848 | Added to the CUSA36842 family |
| CUSA02299 | Boot/FG and flicker fixes; preserve low bits in 10-bit frames |
| CUSA18723, CUSA18742, CUSA28863 | Second generated buffer in FG Fast |
| CUSA00411 | Reduced repeating-texture shimmer; SetFlipRate stub compatibility fix |
| CUSA08519 | Reduced bright-sky character halo |

The release also lists memory/crash fixes for CUSA06782, CUSA13323 Pro, CUSA02636, CUSA44372, CUSA10454, CUSA01967, CUSA12555, CUSA26967, CUSA31580, CUSA07408, CUSA00552, CUSA32836, CUSA34386, CUSA16734, CUSA24653, CUSA18534, CUSA07820 and CUSA44732.

## Report a result

Include exact VCFG version, CUSA and game update, console model, firmware/GoldHEN, resolution/HDR mode, FG mode and settings, other plugins, and the result in gameplay. Attach `vcfg_report.txt` for failures; see [report instructions](CONTRIBUTING.md).
Reports from older releases remain historical evidence and must be retested before calling an issue a v2.36 regression.
