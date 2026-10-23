# VCFG Mandatory Engineering Pipeline & Rules

This file is automatically loaded by the agent whenever working in the `vcfg-alt` repository. All rules below are **strict and non-negotiable**.

## 1. Strict Naming Policy (`CUSAxxxxx` Only)
- **NEVER** write or mention any game names anywhere: not in source code, comments, commit messages, documentation, test files, release notes, or chat responses.
- Refer to titles **exclusively** by their `CUSAxxxxx` Title ID (e.g., `CUSA07820`, `CUSA00552`, `CUSA03041`).

## 2. Log Triage Pipeline (When User Drops Logs / Discord Reports)
Whenever the user provides a new batch of logs, `.zip` archives, or Discord bug reports:
1. **Run Automated Triage First** — Do NOT manually guess from raw logs or change code before running:
   ```powershell
   python tools/triage_logs.py <path_to_logs_or_zip> --version <current_release_ver> --export-traces tools/tests/real_traces.json
   ```
2. **Respect the 4 Triage Buckets**:
   - `BUCKET_1_OLD_VERSION`: Logs captured on older versions (`< current_release_ver`). **Ignore for code changes** — ask the user to re-test on the current release if needed.
   - `BUCKET_2_L2_NO_CRASH`: Logs that triggered `L2 (LOWMEM)` or `DirectMemory shave` and continued rendering (`Present # >= 120`, no `[CRASH]`). **Do NOT touch memory allocation logic for these** — `L2` / `shave` is the intended safety valve working as designed.
   - `BUCKET_3_NEW_TITLE_CRASH`: A new `CUSAxxxxx` title on the current version with a real crash (`[CRASH]`, `ENOMEM` with `Present == 0`, or boot freeze). Extract its hardware `DirectMemory` + `RegisterBuffers` trace into `tools/tests/real_traces.json` and design a fix that passes all existing traces.
   - `BUCKET_4_VERIFIED_REGRESSION`: A previously working `CUSAxxxxx` title that broke on the current version. **Highest priority** — fix immediately and lock with a regression test.

## 3. Research Integration Pipeline (When User Drops Overnight Research `chatN`)
Whenever the user provides overnight research reports (`chat1`..`chatN`):
1. **Never Blindly Copy-Paste**: Overnight research agents do not have the full hardware trace corpus (`tools/tests/real_traces.json`).
2. **Hardware Audit Checklist**:
   - **VRAM Budget**: Does the change allocate any extra `DirectMemory`? In `L2 (LOWMEM)` mode (`8 MB`), extra allocations are forbidden (`0 bytes` additional budget).
   - **VGPR Budget**: Do GCN shader modifications keep register pressure within safe limits (`<= 24 VGPRs`) and pass `tools/elf_arrays.py`?
   - **Flip / Ring Safety**: Does the change respect `2-slot` vs `3-slot` flip index wrapping (`num_buffers % 2 != 0` hazard)?
3. **Trace Replay Gate**: Every integrated feature must pass both test suites before committing.

## 4. Mandatory Automated Regression Gate
Before any commit, PRX build, or PKG release, both test suites **must pass 100%** (enforced automatically inside `classic/build.ps1` and `build.ps1`):
- `python tools/tests/test_lowmem_valve.py` — 7 unit & invariant suites for the 5-step `DirectMemory` valve, `EVICT_DRAIN`, `LOWMEM` budget, and `CUSAxxxxx` Title ID database.
- `python tools/tests/test_real_traces.py` — Replays all 69+ real hardware `DirectMemory` + `RegisterBuffers` boot traces from `tools/tests/real_traces.json` (both Base PS4 `5.0 GB` and PS4 Pro `5.5 GB`) and verifies zero `ENOMEM`, zero fatal arena over-shaves, and monotonic compatibility growth.
