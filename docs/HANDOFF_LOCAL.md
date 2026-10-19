# VCFG handoff for a local model (this PC + the console), 2026-10-20

You continue VCFG: frame generation (30 -> 60) for games on a PS4 with GoldHEN, as GoldHEN plugins plus an app
(PKG). Goal of this round: **improve what exists, support more games, debug everything**, without breaking the games
that already work. Read this file fully, then `docs/NEXT_STEPS.md` (state, rules, history of what was tried).

## 1. Where everything is

| What | Where |
|---|---|
| Main repository (git, remote github.com/Melnikoff07/vcfg) | `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg` |
| Logs and traces pulled from the console on 2026-10-20 | `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\handoff_logs` (`console/`, `witcher_sessions/`) |
| Toolchains (paths are in `config.json`, git-ignored) | `E:\Documents\ps4-framegen\` (OpenOrbis, GoldHEN SDK, LLVM, `llvm-amd` llc for gfx700) |
| Python with Pillow (overlay fonts / previews) | `C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` |
| GitHub CLI (logged in as the owner) | `C:\Users\user\tools\gh\bin\gh.exe` - **do not push or release without the owner's yes** |
| Console | `192.168.1.21`, GoldHEN FTP on port `2121`, anonymous. Base PS4, firmware 11.02 |
| Old reference sources (read only) | `E:\Documents\ps4-framegen\pack_r13` (classic engine origin) |

Repository layout:

| Path | What |
|---|---|
| `classic/` | **classic engine** (all games except CUSA07439, and every EXPERIMENTAL title). `classic/build.sh` (Git Bash), output `build/classic/vcfg_classic.prx` |
| `plugin/` | **new engine** (A20/A22 runtime), only CUSA07439. `build.ps1`, output `build/vcfg/vcfg.prx` |
| `overlay/` | in-game menu + FPS counter (CPU renderer `vov.c`, baked fonts, compositor `osd8.ll`), `vcfg_trace.h` flight recorder, PC preview |
| `launcher/` | the VCFG app (PKG): `launcher/build_pkg.ps1`, `launcher/src/{main,app,ui}.c`, PC preview in `launcher/preview/` |
| `discovery/` | passive logger plugin |
| `tools/` | `console.py` (FTP install/logs), `analyze.py`, shader checks |
| `VERSION` | one version for app + both plugins (2.14 now) |

## 2. Work in a copy, never in `main`

```bash
cd "C:/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg"
git worktree add ../vcfg-night -b night/<topic>      # one worktree per topic
cd ../vcfg-night && cp ../vcfg/config.json .           # config.json is not in git
```

Commit small, explained commits on your branch. The owner reviews and merges. Never rewrite `main` history.

## 3. Console rules (these protect the owner's games and saves)

1. **Never overwrite a PRX a game may have loaded.** Every test build gets a new file name:
   `vcfg_classic_n01.prx`, `n02`, ... Upload to a `.stage` name, read it back, compare, then rename.
2. **Back up `/data/GoldHEN/plugins.ini` before every change** (`plugins.ini.bak_<time>`), map only the one game you
   test, put the previous line back afterwards. Never put an engine in `[default]`.
3. Do not delete on the console: move to `/data/vcfg_quarantine_20261019_163934/`.
4. No GOT pad patching, no `scePadRead` detours (crashed CUSA00207, suspected in CUSA00411).
5. Only `CUSA*` processes are touched (the console is also a Remote Play host).
6. The app installed on the console (2.14d) rewrites `plugins.ini` with its own plugin names when the owner saves
   in it; after that, re-map the test game.
7. Builds that change only plugins: deploy over FTP, **no PKG** unless the app itself changes (owner's wish).
8. No trademarks in repository files: games only by title ID; "PS4" only with the existing non-affiliation note.

Upload helper (Python, adapt names):

```python
import ftplib, io, re, time
ftp = ftplib.FTP(); ftp.connect('192.168.1.21', 2121, timeout=120); ftp.login()
d = open('build/classic/vcfg_classic.prx', 'rb').read(); st = '/data/GoldHEN/plugins/vcfg_classic_n01.stage'
ftp.storbinary('STOR ' + st, io.BytesIO(d)); b = io.BytesIO(); ftp.retrbinary('RETR ' + st, b.write); assert b.getvalue() == d
ftp.rename(st, '/data/GoldHEN/plugins/vcfg_classic_n01.prx')
b = io.BytesIO(); ftp.retrbinary('RETR /data/GoldHEN/plugins.ini', b.write); ini = b.getvalue().decode()
ftp.storbinary('STOR /data/GoldHEN/plugins.ini.bak_' + time.strftime('%H%M%S'), io.BytesIO(ini.encode()))
# replace only the line under the [CUSAxxxxx] you test, then STOR plugins.ini
```

**You cannot start games yourself.** The owner starts them in the morning. Overnight: research, code, builds,
log analysis, and a precise test list for the owner (which build, which game, what to look at, 1 minute each).

## 4. Build pitfalls (each cost hours)

- Classic engine must be built **without -O2** (`-O2` crashed CUSA00207); `classic/build.sh` is right as it is.
- New engine shader arrays (`plugin/source/shaders.h`, `warp720.h`, `post_cb.h`) are generated: compile one shader
  with `plugin/build_shader.py` into a temp header and replace only its `#define ..._RSRC1/2 ... _code[] = {...};`
  block. Never text-replace inside these files. Classic regenerates `classic/source/shaders.h` on every build.
- Bash heredocs turn `\n` inside C strings into real newlines: write patch scripts to files, or use the Edit tool.
- PowerShell 5.1: run scripts with `powershell -NoProfile -ExecutionPolicy Bypass -Command "cd '<repo>'; & .\build.ps1"`.

## 5. How to read the logs

Plugins write `/data/bb_probe/probe.log` (+ `probe.prev.log`) only while `/data/bb_probe/debug_log` exists
(it does on this console). Always written: `/data/bb_probe/trace-<TITLE>.txt` (+ `.prev.txt`): first and last 80
lines of each game start. Key lines (classic engine, every 2 s):

| Line | Meaning |
|---|---|
| `attr: fmt=0x... tiling=.. 1920x1080`, `RegisterBuffers num=N` | scanout format (0x80.. 8-bit, 0x88.. 10-bit, 0x8874.. HDR10), buffer count |
| `injection mode 8 (... inert='...')` | FG decision; non-empty inert = reason it stays off |
| `STAT gnm: X flips/s` | game frames per second (real frames) |
| `CADENCE ... mid 1:a 2:b ... real 1:c 2:d ... pending max P ... game cpu release->submit avg G` | refreshes each mid / real frame stayed on screen (ideal QUALITY: mid 1:60 real 1:60), flip queue depth, the game's own time per frame |
| `LATENCY real frame hand-over -> on screen: avg ...` | input-lag proxy (FG off ~33 ms, FG on ~50-55 ms) |
| `HOOK ms per FG frame: build submit hold flip2 pace total | game own` | where the game's thread spends time inside our hook |
| `GPU time of our passes ... | game span` | our GPU cost; game span = full frame interval |

Current per-title picture (from `handoff_logs/console/trace-*.txt`):

| Title | Format / buffers | Result |
|---|---|---|
| CUSA00411 | 8-bit / 3 | works (QUALITY 30->60); FAST above 30 fps was tried and reverted (judder) |
| CUSA00557 | 10-bit / 3 | 60 fps game, FG off by design, menu + AA work |
| CUSA05571 | 8-bit / **2** | 27 fps with FG (40 without). See NEXT_STEPS 4c/4d: game paces on its own previous flip |
| CUSA08809 | 10-bit / **2** | ~28 fps with FG, artifacts reported; same 2-buffer case |
| CUSA09171 | HDR10 / 3 | 60 fps game, FG off by design |
| CUSA13801 | HDR10 / 3 | works 30->60 |
| CUSA08519, CUSA00207, CUSA07439 | | work (verified earlier) |

Community reports: CUSA04294 works fully; a flying/diving title has stutter and glitches in fast motion; two others
feel laggy (latency).

## 6. Tasks, in order

0. **Benchmark first (`docs/BENCHMARK.md`).** Build the bench capture mode in the classic engine (test build, one
   60 fps title, e.g. CUSA00557 or CUSA09171), `tools/bench/fetch.py`, and a first clip set; score `vcfg_classic` and
   the cross-fade baseline with `tools/bench/metrics.py`. From then on every warp / motion change needs a score.
1. **Two-buffer games (CUSA05571, CUSA08809).** Present copy + buffer rings are in place (`g_pcopy_*` in
   `classic/source/main.c`). Remaining cause: the game waits for its own previous flip, and the real frame reaches
   the screen one refresh after its mid. Try, as separate builds:
   a) flip the mid with the game's flip argument and the real frame with a distinct one (check how the game waits:
      `sceVideoOutGetFlipStatus` / flip events / labels; log what it calls);
   b) a FAST-like 2/3-refresh pulldown when the game cannot hold 30 (25-29 fps) so the screen still gets 60.
   Accept only with numbers: `STAT gnm` 30 and `CADENCE mid 1:60 real 1:60`.
2. **More games.** Owner has ~25 titles configured (`handoff_logs/console/config-*.ini`); only 6 have traces with the
   current build. Prepare a test list; after the owner runs them, classify by trace (`inert` reason, format, buffers,
   resolution). Fix the most common refusal first. Known gaps: non-1080p input (pyramids, dispatch sizes and warp are
   fixed for 1080p), unknown flip forms (`INERT: NO FLIP TAIL`), 60 fps detection edge cases.
3. **Latency.** Queue depth with FG reaches 4 (`pending max 4`). Earlier attempts (hold the game until the queue
   drains; timer pacing) made fps worse; see NEXT_STEPS. Look for a way to keep at most 2 flips queued without
   blocking the game's thread (e.g. delay only our second flip, not the game).
4. **Quality.** Fast full-screen motion artifacts; HUD protection per title (`hud_<TITLE>` rectangles exist in the
   warp); cheaper warp/post (2-2.8 ms of our ~3 ms).
5. **App (needs a PKG, ask first):** per-game engine choice (AUTO/CLASSIC/NEW) and `present_copy` (AUTO/ON/OFF) on
   the game settings screen (not in the in-game menu - owner's decision); show each title's trace summary on its
   card; update checker (download PKG + SHA-256 check).

## 7. Report for the morning

Write `docs/NIGHT_REPORT.md` on your branch: what you changed (commits), which test builds are on the console under
which names and for which game, the exact test list for the owner, and what each test should show in the log.
