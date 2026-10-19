# Code review, 2026-10-19 (v2.11)

Scope: the whole repository, with the most attention on the code that is easy to get wrong without hardware feedback:
the launcher (`launcher/src/`), the overlay (`overlay/`), the shared plugin helpers and the config/profile parsers of
both engines. The GPU paths (`plugin/source/a20_copy_impl.h`, `gv.c`, `pm4scan.c`, shaders) were read for structure
and parsing only; their behaviour can only be judged on the console.

Nothing in this review touches a GPU path, an allocation, a hook or a flip. The plugin binaries change only in
`overlay/vov.c` / `vov_glue.h` (input hardening) and in the unused `Notify` helper, so they still need the usual
hardware check before a release.

## Fixed on this branch

| # | Where | Problem | Fix |
|---|---|---|---|
| 1 | `launcher/src/main.c` | `App.dirty` was set by every change but never read. Toggling a game with Cross or editing DEFAULTS / OVERLAY and then leaving with the PS button lost the change, although the PLUGIN tab says "they save as you change them". | Settings save by themselves 0.8 s after the last change once no button is held (key repeat does not write on every step). Circle / Square / Options still save at once. |
| 2 | `launcher/src/main.c` `sec_flush` | "TURN OFF VCFG" (Square on the PLUGIN tab) left an empty `[CUSA…]` section for each verified title. An empty section keeps `[default]` plugins out of that game, so turning VCFG off broke the user's other plugins in those four games. Reproduced with a host build of `ini_update`. | On removal every section of ours that ended up empty is dropped; foreign lines in it are kept as before. |
| 3 | `launcher/src/main.c` `load_settings` | Values from the ini files were used unchecked (`overlay_pos=0`, `overlay_opacity=5`, `sharp=37`…), so a hand-edited file could put values into the plugins' files that the menu can never produce. | Clamped to the menu's ranges and steps on load. |
| 4 | `launcher/src/main.c` `save_settings` | Write errors were ignored. | Logged and shown in the status line. |
| 5 | `launcher/src/main.c` `sfo_title`, `overlay/vov_glue.h` `vov_read_title` | 32-bit sums of offsets read from `param.sfo` could wrap and pass the bounds check; `strcmp` on a key at the end of the buffer could read past it. | 64-bit bounds checks; the key is compared with its NUL inside the file. |
| 6 | `overlay/vov.c` `next_cp` | A UTF-8 sequence cut short (a label or hint truncated mid-character) read past the string's NUL and kept drawing whatever followed. | The decoder stops at a non-continuation byte; the cut character is skipped. |
| 7 | `overlay/vov.c` `vov_menu` | With overlay memory smaller than one header + footer, `nrows` became negative. | Clamped to 0. |
| 8 | `*/plugin_common.c` (3 copies) | `Notify` used `vsprintf` / `strcpy` into fixed buffers; the classic copy also left the request uninitialised. Unused today, but a trap. | Bounded copies; the three copies are identical again. |
| 9 | `launcher/src/app.c` | Dead code: `a->f->mono.ttf ? &a->f->mono : &a->f->mono`, unused `Settings s`. | Removed. |

Checked on the host: `ui.c`, `app.c`, `vov.c` and the launcher `main.c` (with stub console headers) compile without
warnings at `-Wall`; `ini_update` was run against an installed `plugins.ini` and a section with a foreign plugin,
before and after the fix; `vov_menu` was run with 128 rows of overlay memory and a truncated UTF-8 hint.
**Not yet checked on the console**: everything above needs one launcher run (toggle a game, press PS, reopen: the
change must be there; Square on PLUGIN: `plugins.ini` must have no empty VCFG sections).

## Open findings (not changed here)

Ordered by how much they can hurt.

1. **One `vcfg.ini` for every new-engine title.** The launcher writes `/data/bb_probe/vcfg.ini` once per game with
   `ENGINE_NEW`; the last one wins. Fine while CUSA07439 is the only one, wrong as soon as a second title moves to
   the new engine (roadmap step "one engine"). Make it per title (`vcfg-<TITLE>.ini`) in the plugin and the
   launcher together.
2. **Duplicated engine sources drift.** `gv.c` (88 diff lines), `gvcal.c`, `pm4scan.c`, `fg8_table.h`, `gv.h`,
   `gvcal.h`, `pm4scan.h` exist in `classic/source/` and `plugin/source/` with small differences, and
   `plugin_common.c/.h` + `git_ver.h` exist three times. A fix in one copy silently misses the other. Until the
   engines merge, list the intended differences in `docs/ARCHITECTURE.md` or move the shared files into one
   directory with `#if` for the differences.
3. **Stage flags.** `plugin/source/main.c` (3.4k lines) and `a20_copy_impl.h` (1.8k lines, included as a header)
   carry every A21/A22 stage behind `#if`. Only one combination is built for release (`build.ps1`). The other paths
   are not compiled in CI and will rot; once the release set is confirmed on hardware, delete the stages that lost.
4. **Classic build differs from the new one.** `classic/build.sh` has no `-Werror=format` (the new engine and the
   launcher do), runs the gen step and rewrites the tracked `classic/source/git_ver.h` on every build, and needs
   Git Bash (`pwd -W`). Add `-Werror=format` after one clean build; generate `git_ver.h` into `build/`.
5. **Overlay double buffering re-draws on alternation.** `pick()` only checks the current buffer. If the menu and the
   counter are requested in turn, both buffers are redrawn every call although the other one already holds the
   wanted content. A two-line fix (switch to the other buffer when its hash matches, no write) is safe by the same
   rule the double buffer exists for, but it changes the overlay's timing on CUSA07439 while issue 1 in
   `NEXT_STEPS.md` (flicker) is still open, so it is left for after that is confirmed.
6. **Menu rows cut by small memory hide the selection.** In `vov_menu`, when memory limits `nrows`, `m->sel` can
   point at a row that is not drawn. Scroll the visible window to keep `sel` in it.
7. **`ini_update` truncates foreign lines longer than 511 bytes** in `plugins.ini`. Unlikely (paths), but it rewrites
   the user's data; refuse to write (like "TOO LARGE") instead of truncating.
8. **No automated tests.** The launcher's `ini_update`, `ini_int`, `sfo_title` and the overlay renderer are
   freestanding and run on a PC (the preview already builds `ui.c`/`app.c`). A small host test for `plugins.ini`
   rewriting would have caught finding 2 above; it is the code that can break other people's setups.
9. **Private network detail in docs.** `docs/NEXT_STEPS.md` names the console's LAN address; `config.json` already
   holds it per machine. Use a placeholder in the public docs.

## What is in good shape

- `plugins.ini` handling: backup before every write, write to a temp file, read back and compare, then rename;
  `tools/console.py` additionally refuses when the file changed meanwhile.
- Plugin file names change per release and the launcher never unloads; the rules in `NEXT_STEPS.md` are followed by
  the code.
- Config parsers in the engines clamp every value and ignore unknown keys.
- The overlay commits only changed bytes from a CPU scratch buffer, so the GPU never sees a half-drawn panel.
- Shader arrays are checked against the built ELF on every new-engine build (`tools/elf_arrays.py`).
