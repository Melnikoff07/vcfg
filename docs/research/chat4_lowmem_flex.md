# Task: Ultra-Low `DirectMemory` Footprint & `FlexibleMemory` GPU Fallback (`vcfg-r4-lowmem`)

You are working on the PS4 Frame Generation project (**VCFG**) on Windows.

## 1. MANDATORY ISOLATION & WORKSPACE SETUP (DO THIS FIRST!)
Do **NOT** modify `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` or `E:\Documents\ChatGPT\FRAMGEN-PS4\vcfg` directly.
Create your own isolated git worktree and copy `config.json` (which is gitignored and required by the build scripts):

```powershell
git -C "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" worktree add -b research/lowmem-flex "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r4-lowmem" HEAD
Copy-Item "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt\config.json" "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r4-lowmem\config.json"
```

- Do **ALL** your work inside `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r4-lowmem`.
- Do **NOT** push to `origin` and do **NOT** touch PS4 FTP (`192.168.1.21`).
- **STRICT RULE:** NEVER write any game titles/names in code, comments, logs, or commit messages — use **ONLY `CUSAxxxxx` Title IDs**.

---

## 2. PROJECT LAYOUT & BUILD COMMANDS
- `classic/source/main.c` — DirectMemory allocation (`alloc_dm_range`, `alloc_work_early`, `alloc_mbufs`, `g_pre_gpu`, `g_pre_work`, `g_pre_mem`, `WORK_SZ_8`).
- `classic/source/debug_hooks.h` — `sceKernelAllocateDirectMemory_hook` (Memory Valve `32 MB -> 8 MB -> 0` and Arena Shaving `g_shave_orig_len`), `sceKernelMapFlexibleMemory_hook`.
- `classic/shaders/*.ll` — Shaders reading/writing `g_work` buffers (`lumaCur`, `lumaPrev`, `candS`, `candK`, `mv`, `l4Cur`, `l4Prev`, `coarse`).

### How to build and verify in your worktree:
```powershell
& "C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Users/user/Documents/Codex/2026-10-18/new-chat/work/vcfg-r4-lowmem/classic' && ./build.sh && git checkout -- source/git_ver.h"
python "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-r4-lowmem\tools\tests\check_classic_config.py"
```

---

## 3. RESEARCH & IMPLEMENTATION OBJECTIVES
Inspect `classic/source/main.c` (memory layout of `g_gpu` and `g_work`, `WORK_SZ_8 = 0x220000`) and `classic/source/debug_hooks.h` (`sceKernelAllocateDirectMemory_hook`).

Some memory-critical titles fail or refuse FG because:
- `g_pre_gpu` (`256 KB`..`512 KB`) + `g_pre_work` (`2.25 MB`) + `g_pre_mem` (`8..32 MB`) fragment `DirectMemory`, OR
- When `shave_mode = 2` (`LATE ALLOC` at frame 120) or when the Memory Valve releases `g_pre_mem` during boot, the game has claimed 100% of `DirectMemory`, leaving `0 bytes` for `g_gpu`, `g_work`, or `mbufs`.

Investigate, implement, and test in your worktree:
1. **`FlexibleMemory` (`sceKernelMapFlexibleMemory` with `PROT_CPU_RW | PROT_GPU_RW = 0x33`) Fallback for `g_gpu` and `g_work`:**
   - Currently `alloc_dm_range` and `alloc_work_early` only try `sceKernelAllocateDirectMemory`. If `DirectMemory` is exhausted (especially in `LATE ALLOC` `shave_mode=2` or tight-arena games), allow `g_gpu` and `g_work` to fall back to `sceKernelMapFlexibleMemory` mapped with `0x33` (`PROT_CPU_RW | PROT_GPU_RW`) so `g_gpu` and `g_work` consume **0 bytes of `DirectMemory`**.
   - Implement a safe `alloc_flex_gpu(size_t sz, size_t align)` fallback in `classic/source/main.c` and verify all pointer/alignment requirements (`256-byte` shader alignment, `EOP` label writes, `V#` descriptors).
2. **Multi-Block Arena Shaving & Range-Constrained `DirectMemory` Hook Improvements in `debug_hooks.h`:**
   - Audit `sceKernelAllocateDirectMemory_hook` in `classic/source/debug_hooks.h`. Currently it shaves only when `len == searchEnd - searchStart` (a single full-span request). What if a game queries `sceKernelAvailableDirectMemorySize` first or allocates sequential fixed chunks until `< 8 MB` remains?
   - Implement a clean mechanism in `debug_hooks.h` so `8 MB` for 1 `mid` display buffer is preserved even when a game allocates its pool in multiple chunks or checks available memory beforehand.
3. **Unit Test & Build:**
   - Build `build/classic/vcfg_classic.prx` cleanly, add a unit test in `tools/tests/test_lowmem_valve.py` (or `.c`) for your allocator/valve logic, commit on branch `research/lowmem-flex`, and write a detailed report.
