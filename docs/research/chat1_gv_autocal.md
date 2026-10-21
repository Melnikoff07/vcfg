# Task: Universal Game Motion Vector (GMV) & Depth Buffer Auto-Discovery (`vcfg-r1-gvscan`)

You are working on the PS4 Frame Generation project (**VCFG**) on Windows.

## 1. MANDATORY ISOLATION & WORKSPACE SETUP (DO THIS FIRST!)
Do **NOT** modify `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` or `E:\Documents\ChatGPT\FRAMGEN-PS4\vcfg` directly.
Create your own isolated git worktree and copy `config.json` (which is gitignored and required by the build scripts):

```powershell
git -C "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" worktree add -b research/gv-autocal "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r1-gvscan" HEAD
Copy-Item "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt\config.json" "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r1-gvscan\config.json"
```

- Do **ALL** your work inside `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r1-gvscan`.
- Do **NOT** push to `origin` and do **NOT** touch PS4 FTP (`192.168.1.21`).
- **STRICT RULE:** NEVER write any game titles/names in code, comments, logs, or commit messages — use **ONLY `CUSAxxxxx` Title IDs**.

---

## 2. PROJECT LAYOUT & BUILD COMMANDS
- `classic/source/main.c` — Main Classic/ALT plugin (`sceGnmSubmitCommandBuffers`, `sceGnmSubmitAndFlipCommandBuffers`, `sceVideoOutSubmitFlip`, `build_dcb_flow`, `gv_*` integration).
- `classic/source/gv.h` — Game Motion Vector (GMV) scanner, PM4 DCB parser, candidate `T#` descriptor matcher, and `warp8g` integration.
- `classic/shaders/*.ll` — GCN2 (`gfx700` Sea Islands) LLVM IR compute shaders (`gv8.ll`, `gvc8.ll`, `gvz8.ll`, `warp8g.ll`, `me1.ll`, `gm8.ll`). Compiled into `classic/source/shaders.h` by `classic/gen_shaders.py`.
  - **Hardware limits:** Total shader binary region must fit in `0x8000..0x1C000` (`shader region <= 0x1C000`), and each shader must keep `vgprs <= 64`.
- `overlay/title_families.h` — Canonical `CUSAxxxxx` families.

### How to build and verify your changes in your worktree:
```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg-r1-gvscan/classic' && ./build.sh && git checkout -- source/git_ver.h"
python "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r1-gvscan\tools\tests\check_classic_config.py"
```

---

## 3. RESEARCH & IMPLEMENTATION OBJECTIVES
Read `classic/source/gv.h`, `classic/source/main.c`, and `classic/shaders/gv8.ll`, `gvc8.ll`, `gvz8.ll`, `warp8g.ll` thoroughly.

Currently, `gv.h` scans PM4 command buffers for velocity/depth surfaces, but needs stronger universal auto-discovery and self-calibration across arbitrary `CUSAxxxxx` engines (including dynamic resolution scaling and varied velocity encodings):

1. **Audit & Harden PM4 DCB Traversal in `classic/source/gv.h`:**
   - Check how candidate velocity (`RG16_FLOAT`, `RG16_SNORM`, `RG16_UNORM`, `RGBA16_FLOAT`) and depth (`Z32_FLOAT`, `Z24`) `T#` descriptors and render targets (`CB_COLOR#`) are discovered and calibrated against optical flow (`me1` / `gm8`).
   - Safely traverse indirect buffers (`PKT3_INDIRECT_BUFFER` / chained DCBs) and compute/pixel shader user-data tables (`SPI_SHADER_USER_DATA_PS_*` / `COMPUTE_USER_DATA_*` extended EUD tables) using readable-range validation (`sceKernelVirtualQuery` / cached valid ranges) so it can never fault on unmapped pointers.
2. **Dynamic Resolution & Velocity Encoding Auto-Calibration:**
   - Support dynamic-resolution velocity buffers (e.g., `65%..100%` of display resolution or `1280x720..3840x2160` on PS4 Pro `neo=1`) and automatically detect UV-normalized vs pixel-space encodings, axis signs, and bias when correlating against `me1`/`gm8`.
3. **Standalone Unit Test & Verification:**
   - Add a standalone host unit test in `tools/tests/test_gv_scanner.py` (or `.c`) that feeds synthetic PM4 DCB command streams and `T#` descriptor tables into your scanner logic and verifies detection, safety bounds, and auto-calibration.
   - Build `build/classic/vcfg_classic.prx` cleanly with zero warnings/errors, run all tests, commit on branch `research/gv-autocal`, and output a concise summary of your changes.
