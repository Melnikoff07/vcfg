#!/usr/bin/env python3
"""
R18: End-to-End Lifecycle & Memory Valve Fuzzer (v2.33)
=======================================================
Deterministic stateful fuzzer and Clang/LLI bitcode harness testing interactions
across all 5 core runtime subsystems in classic/source/:
  1. 6-Step Low-Memory Valve & Lazy Pre-Mem Release (debug_hooks.h, main.c)
  2. Runtime 2-Buffer Auto-Detection & History Copy (two_buffer_test.h, fg8_table.h, main.c)
  3. Pyramid Ping-Pong & Cache Invalidation (fg8_table.h, main.c)
  4. Loading Screen / Hibernation Gate & Recovery (main.c, debug_hooks.h)
  5. Runtime Crash & Hibernation Auto-Healer (adapt-<TITLE>.ini) (main.c, debug_hooks.h)

Strictly adheres to the CUSAxxxxx-only naming policy and read-only source audit policy.
"""

import json
import os
import random
import re
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_C_PATH = REPO_ROOT / "classic" / "source" / "main.c"
DEBUG_HOOKS_PATH = REPO_ROOT / "classic" / "source" / "debug_hooks.h"
TWO_BUF_PATH = REPO_ROOT / "classic" / "source" / "two_buffer_test.h"
FG8_TABLE_PATH = REPO_ROOT / "classic" / "source" / "fg8_table.h"
CONFIG_PATH = REPO_ROOT / "config.json"


# Lifecycle states required by the R18 specification
PHASE_BOOT = "BOOT"
PHASE_MENU_2BUF = "MENU_2BUF"
PHASE_LOADING_SCREEN = "LOADING_SCREEN"
PHASE_GAMEPLAY_3BUF = "GAMEPLAY_3BUF"
PHASE_DYNAMIC_RES_CHANGE = "DYNAMIC_RES_CHANGE"
PHASE_CUTSCENE_30FPS = "CUTSCENE_30FPS"
PHASE_LOWMEM_STEP6_PRESSURE = "LOWMEM_STEP6_PRESSURE"
PHASE_RECOVERY = "RECOVERY"

ALL_PHASES = (
    PHASE_BOOT,
    PHASE_MENU_2BUF,
    PHASE_LOADING_SCREEN,
    PHASE_GAMEPLAY_3BUF,
    PHASE_DYNAMIC_RES_CHANGE,
    PHASE_CUTSCENE_30FPS,
    PHASE_LOWMEM_STEP6_PRESSURE,
    PHASE_RECOVERY,
)

# Synthetic CUSAxxxxx profiles used for deterministic lifecycle fuzzing
CUSA_PROFILES = (
    "CUSA00001",
    "CUSA00002",
    "CUSA00003",
    "CUSA00004",
    "CUSA00005",
    "CUSA00006",
    "CUSA00007",
    "CUSA00008",
)

FG8_FLAG_PYR_WARM = 0x1000


def load_pbuf_skip_gate(main_c: str) -> Tuple[bool, Tuple[str, ...]]:
    """The history-copy skip gate of build_dcb_flow8 (`int pbuf_skip = ...;`): (present, titles). titles holds the
    CUSA families of a `vcfg_in_family(g_title_id, "CUSAxxxxx")` test in the gate, and is empty when the gate applies
    to every title. (False, ()) when the gate is absent."""
    m = re.search(r"int pbuf_skip = ([^;]*);", main_c)
    if not m:
        return False, ()
    return True, tuple(re.findall(r'"(CUSA\d{5})"', m.group(1)))


PBUF_SKIP_GATE, PBUF_SKIP_TITLES = load_pbuf_skip_gate(MAIN_C_PATH.read_text(encoding="utf-8", errors="replace"))
MAX_SHAVES = 512
MAX_FLEX_VMAP = 512
HIBERNATE_COOLDOWN_FLIPS = 180
HIBERNATE_RETRY_FLIPS = 360


def load_llvm_tools() -> Tuple[Optional[str], Optional[str]]:
    """Locate clang.exe and lli.exe from config.json if available."""
    if not CONFIG_PATH.exists():
        return None, None
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        llvm_dir = Path(cfg.get("llvm_dir", ""))
        clang = llvm_dir / "clang.exe"
        lli = llvm_dir / "lli.exe"
        if clang.exists() and lli.exists():
            return str(clang), str(lli)
    except Exception:
        pass
    return None, None


@dataclass
class ShaveEntry:
    orig_off: int = 0
    shaved_off: int = 0
    shaved_len: int = 0
    orig_len: int = 0


@dataclass
class FlexVmapEntry:
    va_start: int = 0
    va_end: int = 0
    shaved_off: int = 0
    shaved_len: int = 0
    orig_len: int = 0


@dataclass
class DmBlock:
    phys: int = 0
    va: int = 0
    size: int = 0


@dataclass
class FrameStepResult:
    frame_no: int
    phase: str
    dispatched_fg8: bool
    dispatched_post: bool
    pyr_warm: bool
    used_pbuf: bool
    hibernating: bool
    recovered_this_frame: bool
    P_va: int
    C_va: int
    M1_va: int
    copy_to_pbuf: bool = False  # pcopy8 copied C into g_pbuf this frame (copy_dst == g_pbuf in build_dcb_flow8)


class V233RuntimeModel:
    """
    Cycle-accurate stateful model of the v2.33 state machines in:
      - classic/source/main.c
      - classic/source/debug_hooks.h
      - classic/source/two_buffer_test.h
      - classic/source/fg8_table.h

    When `apply_r18_fixes=False`, reproduces exact v2.33 behavior (including R18-BUG-01..05).
    When `apply_r18_fixes=True`, applies the R18 invariant fixes and enforces all 5 safety
    invariants without a single violation across arbitrary randomized lifecycles.
    """

    def __init__(self, title_id: str = "CUSA00001", apply_r18_fixes: bool = True, vout_path: int = 0):
        self.title_id = title_id
        self.apply_r18_fixes = apply_r18_fixes
        self.vout_path = vout_path

        # Boot & early memory state
        self.g_boot_phase: int = 1
        self.g_pre_off: int = 0
        self.g_pre_mem: int = 0
        self.g_pre_sz: int = 0
        self.g_early_released: int = 0

        # DirectMemory / FlexibleMemory blocks for work buffers (M1..M6)
        self.g_dm_blocks: List[DmBlock] = []
        self.g_mbuf_state: int = 0  # 0=uninit, 1=ready, -1=failed/released
        self.g_mbase: int = 0
        self.g_nbuf: int = 0
        self.g_ngame: int = 3
        self.g_buf: Dict[int, int] = {}

        # Low-memory valve & hibernation state
        self.g_mem_hibernating: int = 0
        self.g_hibernated_at_flip: int = 0
        self.g_mem_hibernate_count: int = 0
        self.g_flips: int = 0
        self.g_vo_submit_flips: int = 0

        # Shave & Flex VMap tables (MAX_SHAVES=512, MAX_FLEX_VMAP=512)
        self.g_shaves: List[ShaveEntry] = [ShaveEntry() for _ in range(MAX_SHAVES)]
        self.g_shave_n: int = 0
        self.g_flex_vmap: List[FlexVmapEntry] = [FlexVmapEntry() for _ in range(MAX_FLEX_VMAP)]
        self.g_flex_vmap_n: int = 0
        self.active_flex_bridges: Dict[Tuple[int, int], int] = {}  # (va_start, va_end) -> size
        self.direct_mem_allocated_by_plugin: int = 0
        self.flex_mem_allocated_by_plugin: int = 0

        # 2-Buffer detection & P-buffer history state
        self.g_cfg_two_buffer: int = 0
        self.g_cfg_present_copy: int = 1  # 0=off, 1=auto, 2=force_off
        self.g_runtime_2buf: int = 0
        self.g_vq_on: int = 0
        self.g_2buf_alt_streak: int = 0
        self.g_2buf_idx_a: int = -1
        self.g_2buf_idx_b: int = -1
        self.g_2buf_prev1: int = -1
        self.g_2buf_prev2: int = -1
        self.g_pcopy_avail: int = 0
        self.g_pcopy_on: int = 0
        self.g_pbuf: int = 0
        self.g_pbuf_sz: int = 0
        self.g_pbuf_prev_va: int = 0
        self.g_pbuf_last_w: int = 0
        self.g_pbuf_last_h: int = 0
        self.g_pbuf_last_seq: int = 0
        self.g_pbuf_valid: int = 0
        self.g_pbuf_dm_bytes: int = 0  # DirectMemory bytes used by pbuf (must be 0 for Invariant 5)

        # Pyramid cache & frame sequence state
        self.g_bw: int = 1920
        self.g_bh: int = 1080
        self.g_pitch: int = 1920
        self.g_prev_idx: int = -1
        self.g_frame_seq: int = 0
        self.g_pyr_last_cur_base: int = 0
        self.g_pyr_last_w: int = 0
        self.g_pyr_last_h: int = 0
        self.g_pyr_last_pitch: int = 0
        self.g_pyr_last_seq: int = 0
        self.g_pyr_last_2buf: int = 0
        self.g_repeat_count: int = 0

        # Adapt INI state (Subsystem 5)
        self.adapt_ini: Dict[str, int] = {
            "two_buffer": 0,
            "vout_path": vout_path,
            "gv_conv": 0,
            "shave_mode": 0,
            "reg_mode": 0,
        }
        self.adapt_ini_saved: int = 0

        # Mapped virtual address registry for Use-After-Free verification (Invariant 1)
        self.valid_va_ranges: List[Tuple[int, int, str]] = []

    def _register_va_range(self, va: int, size: int, tag: str) -> None:
        self.valid_va_ranges.append((va, va + size, tag))

    def _unregister_va_range(self, va: int, size: int) -> None:
        target = (va, va + size)
        self.valid_va_ranges = [
            (s, e, t) for (s, e, t) in self.valid_va_ranges if (s, e) != target
        ]

    def is_va_mapped(self, va: int) -> bool:
        for s, e, _ in self.valid_va_ranges:
            if s <= va < e:
                return True
        return False

    def total_flips(self) -> int:
        if self.apply_r18_fixes:
            return self.g_flips + self.g_vo_submit_flips
        return self.g_flips

    # ------------------------------------------------------------------
    # Subsystem 1: Boot Pre-Mem, Work Buffers, and 6-Step Low-Memory Valve
    # ------------------------------------------------------------------
    def boot_reserve_early_mem(self, size: int = 64 * 1024 * 1024) -> None:
        """Simulates early DirectMemory reservation at plugin boot."""
        self.g_boot_phase = 1
        self.g_pre_off = 0x1000_0000
        self.g_pre_mem = 0x5000_0000
        self.g_pre_sz = size
        self.g_early_released = 0
        self.direct_mem_allocated_by_plugin += size
        self._register_va_range(self.g_pre_mem, self.g_pre_sz, "g_pre_mem")

    def register_game_buffers(self, ngame: int = 3, width: int = 1920, height: int = 1080, pitch: int = 1920) -> None:
        """Simulates sceVideoOutRegisterBuffers."""
        self.g_ngame = ngame
        self.g_bw = width
        self.g_bh = height
        self.g_pitch = pitch
        for i in range(ngame):
            va = 0x2000_0000 + i * 0x0100_0000
            self.g_buf[i] = va
            if not self.is_va_mapped(va):
                self._register_va_range(va, 0x0080_0000, f"game_buf_{i}")

        self.g_mbase = ngame
        self.g_nbuf = ngame + 6
        self.pyr_cache_invalidate()
        self.g_prev_idx = -1
        self.g_repeat_count = 0

    def alloc_work_buffers_from_pre_mem(self) -> bool:
        """Simulates init_extra_DirectMemory_buffers using g_pre_mem fast path (main.c:2140)."""
        if self.g_pre_mem == 0 or self.g_pre_sz < 6 * 0x0080_0000:
            return False
        for i in range(6):
            idx = self.g_mbase + i
            self.g_buf[idx] = self.g_pre_mem + i * 0x0080_0000
        self.g_mbuf_state = 1
        self.g_pcopy_avail = 1
        self.g_pcopy_on = 1 if self.g_cfg_present_copy != 0 else 0
        return True

    def alloc_work_buffers_scattered(self, use_flex_only: bool = False) -> bool:
        """Simulates scattered allocation into g_dm_blocks[] during recovery or post-release init."""
        self.free_all_dm_blocks()
        for i in range(6):
            sz = 0x0080_0000
            va = 0x6000_0000 + i * 0x0100_0000
            phys = -1 if use_flex_only else (0x3000_0000 + i * sz)
            self.g_dm_blocks.append(DmBlock(phys=phys, va=va, size=sz))
            self._register_va_range(va, sz, f"dm_block_{i}")
            if phys == -1:
                self.flex_mem_allocated_by_plugin += sz
            else:
                self.direct_mem_allocated_by_plugin += sz
            self.g_buf[self.g_mbase + i] = va
        self.g_mbuf_state = 1
        self.g_pcopy_avail = 1
        self.g_pcopy_on = 1 if self.g_cfg_present_copy != 0 else 0
        return True

    def alloc_pbuf_history(self) -> bool:
        """
        Simulates alloc_pbuf_history() in main.c:2102-2126.
        Strictly uses alloc_flex_gpu (FlexibleMemory ONLY, 0 bytes of DirectMemory - Invariant 5).
        """
        if self.g_pbuf != 0:
            return True
        sz = 0x0080_0000
        va = 0x7000_0000
        self.g_pbuf = va
        self.g_pbuf_sz = sz
        self.g_pbuf_dm_bytes = 0  # Never uses DirectMemory
        self.flex_mem_allocated_by_plugin += sz
        self._register_va_range(va, sz, "g_pbuf_flex")
        self.g_pcopy_avail = 1
        return True

    def free_pbuf_history(self) -> None:
        if self.g_pbuf != 0:
            self._unregister_va_range(self.g_pbuf, self.g_pbuf_sz)
            self.flex_mem_allocated_by_plugin = max(0, self.flex_mem_allocated_by_plugin - self.g_pbuf_sz)
            self.g_pbuf = 0
            self.g_pbuf_sz = 0
            self.g_pbuf_prev_va = 0
            self.g_pbuf_valid = 0

    def release_early_mem(self) -> None:
        """Simulates release_early_mem() in main.c:519-522 + free_pre_mem() in main.c:503-517."""
        if self.g_early_released:
            return
        self.g_early_released = 1
        if self.g_pre_mem != 0:
            self._unregister_va_range(self.g_pre_mem, self.g_pre_sz)
            self.direct_mem_allocated_by_plugin = max(0, self.direct_mem_allocated_by_plugin - self.g_pre_sz)
            self.g_pre_mem = 0
            self.g_pre_off = 0
            self.g_pre_sz = 0

        if self.apply_r18_fixes:
            # R18-BUG-01 Fix: If M1..M6 were backed by g_pre_mem (no g_dm_blocks),
            # invalidate g_mbuf_state and scrub dangling g_buf[mbase..nbuf-1] VAs.
            if self.g_mbuf_state == 1 and len(self.g_dm_blocks) == 0:
                self.g_mbuf_state = -1
                self.g_pcopy_avail = 1 if self.g_pbuf != 0 else 0
                self.g_pcopy_on = 0
                for idx in range(self.g_mbase, self.g_nbuf):
                    self.g_buf[idx] = 0
            self.pyr_cache_invalidate()

    def free_all_dm_blocks(self) -> None:
        for blk in self.g_dm_blocks:
            self._unregister_va_range(blk.va, blk.size)
            if blk.phys == -1:
                self.flex_mem_allocated_by_plugin = max(0, self.flex_mem_allocated_by_plugin - blk.size)
            else:
                self.direct_mem_allocated_by_plugin = max(0, self.direct_mem_allocated_by_plugin - blk.size)
        self.g_dm_blocks.clear()

    def valve_hibernate_and_drain_gpu(self) -> None:
        """Simulates valve_hibernate_and_drain_gpu() in debug_hooks.h:522-536."""
        self.g_mem_hibernating = 1
        flips_now = self.total_flips()
        self.g_hibernated_at_flip = flips_now if flips_now != 0 else 1
        if self.apply_r18_fixes:
            self.pyr_cache_invalidate()

    def hibernate_work_dm(self) -> int:
        """Simulates hibernate_work_dm() in debug_hooks.h:538-557."""
        if len(self.g_dm_blocks) == 0:
            return 0
        self.valve_hibernate_and_drain_gpu()
        self.g_mbuf_state = -1
        self.g_pcopy_avail = 1 if (self.apply_r18_fixes and self.g_pbuf != 0) else 0
        self.g_pcopy_on = 0
        freed = sum(b.size for b in self.g_dm_blocks)
        self.free_all_dm_blocks()
        if self.apply_r18_fixes:
            for idx in range(self.g_mbase, self.g_nbuf):
                self.g_buf[idx] = 0
            self.g_mem_hibernate_count += 1
        return freed

    def trigger_lowmem_valve(self, step: int) -> None:
        """
        Simulates the 6-step low-memory valve in my_sceKernelAllocateDirectMemory (debug_hooks.h:894-1036).
          step 1: boot_phase release_early_mem (if g_mbuf_state != 1)
          step 2: runtime hibernate_work_dm (releases g_dm_blocks[])
          step 5b: emergency runtime release_early_mem (when g_pre_mem != 0)
          step 6: lazy post-boot release_early_mem (when !boot_phase and g_pre_mem != 0)
        """
        if step == 1:
            if self.g_boot_phase and not self.g_early_released and self.g_mbuf_state != 1:
                self.release_early_mem()
        elif step == 2:
            if not self.g_boot_phase and len(self.g_dm_blocks) > 0:
                self.hibernate_work_dm()
        elif step in (5, 6):
            if not self.g_early_released and self.g_pre_mem != 0:
                if self.g_mbuf_state == 1:
                    self.valve_hibernate_and_drain_gpu()
                self.release_early_mem()
                if self.apply_r18_fixes:
                    self.g_mem_hibernate_count += 1

    def try_recover_hibernated_work_at(self, cur_flips: int, allow_reinit_success: bool = True) -> int:
        """Simulates try_recover_hibernated_work_at() in main.c:732-767."""
        if not self.g_mem_hibernating:
            return 1
        hib_flip = self.g_hibernated_at_flip
        if hib_flip == 0:
            self.g_hibernated_at_flip = cur_flips if cur_flips != 0 else 1
            return 0
        if cur_flips < hib_flip or (cur_flips - hib_flip) < HIBERNATE_COOLDOWN_FLIPS:
            return 0

        if self.apply_r18_fixes:
            # R18-BUG-01 Fix: Verify backing memory still exists before declaring g_mbuf_state == 1 ready!
            backing_ok = (self.g_pre_mem != 0) or (len(self.g_dm_blocks) > 0)
            if self.g_mbuf_state == 1 and backing_ok:
                self.g_mem_hibernating = 0
                self.g_hibernated_at_flip = 0
                self.pyr_cache_invalidate()
                return 1
            if self.g_mbuf_state == 1 and not backing_ok:
                self.g_mbuf_state = -1
        else:
            # Unpatched v2.33 bug: trusts g_mbuf_state == 1 even when g_pre_mem == NULL and g_dm_blocks is empty!
            if self.g_mbuf_state == 1:
                self.g_mem_hibernating = 0
                self.g_hibernated_at_flip = 0
                return 1

        # Attempt re-allocation (using FlexibleMemory / scattered blocks)
        self.g_mbuf_state = 0
        if allow_reinit_success:
            self.alloc_work_buffers_scattered(use_flex_only=True)
        else:
            self.g_mbuf_state = -1

        if self.g_mbuf_state == 1:
            self.g_mem_hibernating = 0
            self.g_hibernated_at_flip = 0
            if self.apply_r18_fixes:
                self.pyr_cache_invalidate()
            return 1

        self.g_hibernated_at_flip = cur_flips if cur_flips != 0 else 1
        return 0

    # ------------------------------------------------------------------
    # Subsystem 1b: Shave & Flex VMap Tracking (Invariant 2)
    # ------------------------------------------------------------------
    def record_shave(self, orig_off: int, shaved_off: int, shaved_len: int, orig_len: int) -> bool:
        """Simulates record_shave in debug_hooks.h:605-635."""
        for i in range(min(self.g_shave_n, MAX_SHAVES)):
            if self.g_shaves[i].orig_len == 0 or self.g_shaves[i].shaved_off == shaved_off:
                self.g_shaves[i] = ShaveEntry(orig_off, shaved_off, shaved_len, orig_len)
                return True
        if self.g_shave_n >= MAX_SHAVES:
            return False
        idx = self.g_shave_n
        self.g_shave_n += 1
        self.g_shaves[idx] = ShaveEntry(orig_off, shaved_off, shaved_len, orig_len)
        return True

    def record_flex_vmap_ex(self, vs: int, ve: int, shaved_off: int, shaved_len: int, orig_len: int) -> bool:
        """Simulates record_flex_vmap_ex in debug_hooks.h:123-152."""
        limit = min(self.g_flex_vmap_n, MAX_FLEX_VMAP)
        for i in range(limit):
            entry = self.g_flex_vmap[i]
            if entry.va_start == vs and entry.va_end == ve:
                self.g_flex_vmap[i] = FlexVmapEntry(vs, ve, shaved_off, shaved_len, orig_len)
                return True
            if self.apply_r18_fixes and entry.va_start == 0 and entry.va_end == 0:
                # R18-BUG-02 Fix: Reuse zeroed tombstone slots!
                self.g_flex_vmap[i] = FlexVmapEntry(vs, ve, shaved_off, shaved_len, orig_len)
                return True

        if self.g_flex_vmap_n >= MAX_FLEX_VMAP:
            return False
        idx = self.g_flex_vmap_n
        self.g_flex_vmap_n += 1
        self.g_flex_vmap[idx] = FlexVmapEntry(vs, ve, shaved_off, shaved_len, orig_len)
        return True

    def sim_shaved_alloc_and_map(self, phys_off: int, orig_len: int, shaved_len: int, va_base: int) -> bool:
        """Simulates AllocateDirectMemory (Step 4 shave) + MapDirectMemory (Flex bridge)."""
        ok_shave = self.record_shave(phys_off, phys_off, shaved_len, orig_len)
        tail_va_start = va_base + shaved_len
        tail_va_end = va_base + orig_len
        tail_sz = orig_len - shaved_len
        self.active_flex_bridges[(tail_va_start, tail_va_end)] = tail_sz
        self.flex_mem_allocated_by_plugin += tail_sz
        ok_vmap = self.record_flex_vmap_ex(tail_va_start, tail_va_end, phys_off, shaved_len, orig_len)
        return ok_shave and ok_vmap

    def sim_release_direct_memory(self, phys_off: int, release_len: int) -> int:
        """Simulates my_sceKernelReleaseDirectMemory + unmap_shave_flex_vmaps (debug_hooks.h:1422-1467)."""
        rel_end = phys_off + release_len
        unmapped_count = 0
        limit_vmap = min(self.g_flex_vmap_n, MAX_FLEX_VMAP)
        for i in range(limit_vmap):
            entry = self.g_flex_vmap[i]
            if entry.va_start == 0 and entry.va_end == 0:
                continue
            orig_span = entry.orig_len if entry.orig_len > entry.shaved_len else entry.shaved_len
            sh_end = entry.shaved_off + orig_span
            if entry.shaved_off < rel_end and sh_end > phys_off:
                key = (entry.va_start, entry.va_end)
                if key in self.active_flex_bridges:
                    sz = self.active_flex_bridges.pop(key)
                    self.flex_mem_allocated_by_plugin = max(0, self.flex_mem_allocated_by_plugin - sz)
                self.g_flex_vmap[i] = FlexVmapEntry(0, 0, 0, 0, 0)
                unmapped_count += 1

        limit_shave = min(self.g_shave_n, MAX_SHAVES)
        for i in range(limit_shave):
            s = self.g_shaves[i]
            if s.orig_len == 0:
                continue
            s_end = s.shaved_off + s.orig_len
            if s.shaved_off < rel_end and s_end > phys_off:
                self.g_shaves[i] = ShaveEntry(0, 0, 0, 0)

        return unmapped_count

    # ------------------------------------------------------------------
    # Subsystem 2 & 3: 2-Buffer Tracking, P-Buf History, Pyramid Cache
    # ------------------------------------------------------------------
    def pyr_cache_invalidate(self) -> None:
        """Simulates pyr_cache_invalidate() in main.c:345-350."""
        self.g_pyr_last_cur_base = 0
        self.g_pyr_last_w = 0
        self.g_pyr_last_h = 0
        if self.apply_r18_fixes:
            self.g_pyr_last_pitch = 0
            self.g_pyr_last_seq = 0
            self.g_pbuf_valid = 0
            self.g_pbuf_prev_va = 0

    def on_flip_2buf_track(self, index: int) -> None:
        """Simulates on_flip_2buf_track() in main.c:2000-2039 + two_buffer_test.h:43-94.
        Current source tracks the ring whatever two_buffer says, and only leaves runtime 2-buffer mode with
        two_buffer == 0."""
        if self.g_cfg_two_buffer and not self.apply_r18_fixes:
            return
        if index < 0 or index >= self.g_ngame:
            return

        if self.g_ngame == 2:
            if not self.g_runtime_2buf:
                self.g_runtime_2buf = 1
                if not self.g_vq_on:
                    self.g_vq_on = 1
                self.alloc_pbuf_history()
                if self.apply_r18_fixes:
                    self.pyr_cache_invalidate()
            return

        # g_ngame >= 3: detect A, B, A, B, A, B alternating streak (6 flips)
        if index == self.g_2buf_prev1:
            return
        if self.g_2buf_prev2 >= 0 and index == self.g_2buf_prev2:
            self.g_2buf_alt_streak += 1
            if self.g_2buf_alt_streak >= 6 and not self.g_runtime_2buf:
                self.g_runtime_2buf = 1
                self.g_2buf_idx_a = self.g_2buf_prev1
                self.g_2buf_idx_b = index
                if not self.g_vq_on:
                    self.g_vq_on = 1
                self.alloc_pbuf_history()
                if self.apply_r18_fixes:
                    self.pyr_cache_invalidate()
        else:
            self.g_2buf_alt_streak = 1
            if (self.g_runtime_2buf and index != self.g_2buf_idx_a and index != self.g_2buf_idx_b
                    and not self.g_cfg_two_buffer):
                # 3rd distinct buffer index seen -> switch back to 3-buffer mode
                self.g_runtime_2buf = 0
                self.g_2buf_idx_a = -1
                self.g_2buf_idx_b = -1
                if self.g_vq_on:
                    self.g_vq_on = 0
                if self.apply_r18_fixes:
                    # R18-BUG-03 / R18-BUG-04 Fix: Invalidate pbuf history and pyramid cache on 2buf->3buf switch
                    self.g_pbuf_valid = 0
                    self.g_pbuf_prev_va = 0
                    self.pyr_cache_invalidate()

        self.g_2buf_prev2 = self.g_2buf_prev1
        self.g_2buf_prev1 = index

    def fg8_resolve_2buf_history(
        self,
        P_io: int,
        C: int,
        w: int,
        h: int,
        seq: int,
        two_buf_active: bool,
    ) -> Tuple[int, bool, bool]:
        """
        Simulates fg8_resolve_2buf_history() in fg8_table.h:34-68.
        Returns (new_P, used_pbuf, need_copy_c_to_pbuf).
        """
        if self.apply_r18_fixes and not two_buf_active:
            # R18-BUG-03 Fix: Never redirect P to g_pbuf when 2-buffer mode is inactive!
            return P_io, False, False

        if self.g_pbuf == 0 or C == 0 or w == 0 or h == 0:
            return P_io, False, False

        if self.g_pbuf_last_w != w or self.g_pbuf_last_h != h:
            self.g_pbuf_valid = 0
            self.g_pbuf_prev_va = 0
            self.g_pbuf_last_w = w
            self.g_pbuf_last_h = h

        if self.apply_r18_fixes:
            # R18-BUG-03 Fix: Enforce consecutive sequence continuity (last_seq + 1 == seq) and P != C
            is_consecutive = self.g_pbuf_valid == 1 and self.g_pbuf_prev_va != 0 and (self.g_pbuf_last_seq + 1 == seq)
        else:
            is_consecutive = self.g_pbuf_valid == 1 and self.g_pbuf_prev_va != 0

        used_pbuf = False
        new_P = P_io
        if is_consecutive:
            new_P = self.g_pbuf_prev_va
            used_pbuf = True

        self.g_pbuf_prev_va = self.g_pbuf
        self.g_pbuf_last_seq = seq
        self.g_pbuf_valid = 1
        return new_P, used_pbuf, True

    # ------------------------------------------------------------------
    # Subsystem 4: Frame Submission & Hibernation Gate
    # ------------------------------------------------------------------
    def step_frame(
        self,
        phase: str,
        index: int,
        dcb_valid: bool = True,
        allow_reinit_success: bool = True,
    ) -> FrameStepResult:
        """Simulates one flip through flip_inner_ex / sceVideoOutSubmitFlip_hook."""
        if self.vout_path:
            self.g_vo_submit_flips += 1
        else:
            self.g_flips += 1

        if self.total_flips() >= 10:
            self.g_boot_phase = 0

        self.g_frame_seq += 1
        seq = self.g_frame_seq

        # Subsystem 4: Hibernation Gate & Recovery (on_sub_hibernation_gate)
        recovered_this_frame = False
        if self.g_mem_hibernating:
            if self.apply_r18_fixes:
                self.pyr_cache_invalidate()
            rec = self.try_recover_hibernated_work_at(self.total_flips(), allow_reinit_success=allow_reinit_success)
            if rec == 0:
                self.g_prev_idx = index
                return FrameStepResult(
                    frame_no=self.total_flips(),
                    phase=phase,
                    dispatched_fg8=False,
                    dispatched_post=False,
                    pyr_warm=False,
                    used_pbuf=False,
                    hibernating=True,
                    recovered_this_frame=False,
                    P_va=0,
                    C_va=self.g_buf.get(index, 0),
                    M1_va=0,
                )
            recovered_this_frame = True

        # Early exit on invalid DCB / non-renderable flip (main.c:3147-3188, 3604)
        if not dcb_valid:
            if self.apply_r18_fixes:
                self.pyr_cache_invalidate()
                self.g_prev_idx = index
            return FrameStepResult(
                frame_no=self.total_flips(),
                phase=phase,
                dispatched_fg8=False,
                dispatched_post=False,
                pyr_warm=False,
                used_pbuf=False,
                hibernating=False,
                recovered_this_frame=recovered_this_frame,
                P_va=0,
                C_va=self.g_buf.get(index, 0),
                M1_va=0,
            )

        # Loading screen static repeat detection (index == g_prev_idx)
        is_repeat_flip = (self.g_prev_idx >= 0 and index == self.g_prev_idx)
        if is_repeat_flip:
            self.g_repeat_count += 1
        else:
            self.g_repeat_count = 0

        if self.apply_r18_fixes and is_repeat_flip:
            # R18-BUG-04 Fix: Same-index repeat flip (P == C) must not run optical flow or warm pyramid
            self.pyr_cache_invalidate()
            self.g_prev_idx = index
            return FrameStepResult(
                frame_no=self.total_flips(),
                phase=phase,
                dispatched_fg8=False,
                dispatched_post=False,
                pyr_warm=False,
                used_pbuf=False,
                hibernating=False,
                recovered_this_frame=recovered_this_frame,
                P_va=self.g_buf.get(index, 0),
                C_va=self.g_buf.get(index, 0),
                M1_va=0,
            )

        # Subsystem 2: Update 2-buffer tracker BEFORE computing pcopy/two_buf_active (R18-BUG-03 Fix)
        prev_2buf_mode = int(bool(self.g_cfg_two_buffer or self.g_runtime_2buf))
        if self.apply_r18_fixes:
            self.on_flip_2buf_track(index)
        cur_2buf_mode = int(bool(self.g_cfg_two_buffer or self.g_runtime_2buf))

        can_fg8 = (self.g_mbuf_state == 1) and (self.g_prev_idx >= 0) and (self.g_ngame >= 2)
        C_va = self.g_buf.get(index, 0)
        P_va = self.g_buf.get(self.g_prev_idx, 0) if self.g_prev_idx >= 0 else 0

        used_pbuf = False
        copy_to_pbuf = False
        pyr_warm = False
        M1_va = 0

        if can_fg8:
            M1_va = self.g_buf.get(self.g_mbase, 0)
            two_buf_active = bool(self.g_cfg_two_buffer or self.g_runtime_2buf)
            if self.apply_r18_fixes:
                if two_buf_active and self.g_pbuf != 0 and self.g_cfg_present_copy != 2:
                    if self.pbuf_skip():
                        # build_dcb_flow8 gate: 3-buffer ring of a listed title, no copy, P stays in the game's buffer
                        self.g_pbuf_valid = 0
                    else:
                        P_va, used_pbuf, copy_to_pbuf = self.fg8_resolve_2buf_history(
                            P_va, C_va, self.g_bw, self.g_bh, seq, two_buf_active=True
                        )
            else:
                # Unpatched v2.33: calls fg8_resolve_2buf_history whenever g_pbuf != NULL
                if self.g_pbuf != 0 and self.g_cfg_present_copy != 2:
                    P_va, used_pbuf, _ = self.fg8_resolve_2buf_history(
                        P_va, C_va, self.g_bw, self.g_bh, seq, two_buf_active=two_buf_active
                    )

            # Subsystem 3: Pyramid Ping-Pong Warm Check (build_dcb_flow8 in main.c:1312)
            if self.apply_r18_fixes:
                pyr_warm = (
                    P_va != 0
                    and P_va != C_va
                    and P_va == self.g_pyr_last_cur_base
                    and self.g_bw == self.g_pyr_last_w
                    and self.g_bh == self.g_pyr_last_h
                    and self.g_pitch == self.g_pyr_last_pitch
                    and seq == self.g_pyr_last_seq + 1
                    and cur_2buf_mode == self.g_pyr_last_2buf
                )
            else:
                pyr_warm = (
                    P_va != 0
                    and P_va == self.g_pyr_last_cur_base
                    and self.g_bw == self.g_pyr_last_w
                    and self.g_bh == self.g_pyr_last_h
                )

            # Update pyramid cache state
            self.g_pyr_last_cur_base = C_va if not used_pbuf else self.g_pbuf
            self.g_pyr_last_w = self.g_bw
            self.g_pyr_last_h = self.g_bh
            self.g_pyr_last_pitch = self.g_pitch
            self.g_pyr_last_seq = seq
            self.g_pyr_last_2buf = cur_2buf_mode

        if not self.apply_r18_fixes:
            # In unpatched v2.33, on_flip_2buf_track is called AFTER build_dcb_flow8 in flip_inner_ex
            self.on_flip_2buf_track(index)

        self.g_prev_idx = index
        return FrameStepResult(
            frame_no=self.total_flips(),
            phase=phase,
            dispatched_fg8=can_fg8,
            dispatched_post=can_fg8,
            pyr_warm=pyr_warm,
            used_pbuf=used_pbuf,
            hibernating=False,
            recovered_this_frame=recovered_this_frame,
            P_va=P_va,
            C_va=C_va,
            M1_va=M1_va,
            copy_to_pbuf=copy_to_pbuf,
        )

    def pbuf_skip(self) -> bool:
        """build_dcb_flow8: `pbuf_skip = !copy_dst && g_ngame >= 3 && !g_runtime_2buf` (plus a title test when the
        gate in main.c carries one). The model has no present copy into an own display buffer, so copy_dst is always 0
        here."""
        if not PBUF_SKIP_GATE:
            return False
        if PBUF_SKIP_TITLES and self.title_id not in PBUF_SKIP_TITLES:
            return False
        return self.g_ngame >= 3 and not self.g_runtime_2buf

    def register_buffers_two_buffer_cfg(self) -> None:
        """on_register_buffers_2buf() with two_buffer=1 in the config: virtual queue on and g_pbuf reserved,
        whatever the ring size (main.c, RegisterBuffers)."""
        self.g_cfg_two_buffer = 1
        if self.g_cfg_present_copy != 2:
            self.g_vq_on = 1
            self.alloc_pbuf_history()

    # ------------------------------------------------------------------
    # Subsystem 5: Adapt INI Persistence & Crash Signal Handler
    # ------------------------------------------------------------------
    def save_adapt_ini(self) -> Dict[str, int]:
        """Simulates save_adapt_ini() in main.c:1671-1725."""
        if self.g_cfg_two_buffer or self.g_runtime_2buf:
            self.adapt_ini["two_buffer"] = 1
        if self.apply_r18_fixes:
            # R18-BUG-05 Fix: Only escalate shave_mode=2/reg_mode=1 on actual runtime hibernation,
            # not harmless boot-time shaves.
            if self.g_mem_hibernate_count > 0:
                self.adapt_ini["shave_mode"] = 2
                self.adapt_ini["reg_mode"] = 1
        else:
            if self.g_mem_hibernate_count > 0 or self.g_shave_n > 0:
                self.adapt_ini["shave_mode"] = 2
                self.adapt_ini["reg_mode"] = 1
        self.adapt_ini_saved = 1
        return dict(self.adapt_ini)

    def simulate_crash_sig_handler(self, is_oom_crash: bool = True) -> Dict[str, int]:
        """Simulates crash_sig_handler() in debug_hooks.h:425-445."""
        if not is_oom_crash:
            return dict(self.adapt_ini)
        if self.apply_r18_fixes:
            # R18-BUG-05 Fix: Preserve existing keys (two_buffer, vout_path, gv_conv)
            self.adapt_ini["shave_mode"] = 2
            self.adapt_ini["reg_mode"] = 1
        else:
            # Unpatched v2.33 overwrites file with O_TRUNC and ONLY writes shave_mode=2, reg_mode=1
            self.adapt_ini = {
                "two_buffer": 0,
                "vout_path": 0,
                "gv_conv": 0,
                "shave_mode": 2,
                "reg_mode": 1,
            }
        return dict(self.adapt_ini)


class TestR18LifecycleFuzzer(unittest.TestCase):
    """
    R18 Verification Suite:
      1. Static & Structural Source Audit of v2.33 in classic/source/
      2. Deterministic Reproduction of R18-BUG-01..05 on Unpatched v2.33 Model & LLVM Bitcode
      3. 120-Lifecycle (44,000+ Frames) Stateful Fuzzer across all 8 Phases & 5 Safety Invariants
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.main_c = MAIN_C_PATH.read_text(encoding="utf-8", errors="replace")
        cls.debug_hooks_h = DEBUG_HOOKS_PATH.read_text(encoding="utf-8", errors="replace")
        cls.two_buf_h = TWO_BUF_PATH.read_text(encoding="utf-8", errors="replace")
        cls.fg8_table_h = FG8_TABLE_PATH.read_text(encoding="utf-8", errors="replace")
        cls.initial_mtimes = {
            p: p.stat().st_mtime_ns
            for p in (MAIN_C_PATH, DEBUG_HOOKS_PATH, TWO_BUF_PATH, FG8_TABLE_PATH)
        }
        cls.clang, cls.lli = load_llvm_tools()

    # ==================================================================
    # 1. Source Audit & Invariant Verification on classic/source/
    # ==================================================================
    def test_01_source_files_present_and_unmodified(self) -> None:
        """Verify all 4 target source files exist and are untouched by the R18 audit/fuzzer."""
        for p, orig_mtime in self.initial_mtimes.items():
            self.assertTrue(p.exists(), f"Missing target source file: {p}")
            self.assertEqual(
                p.stat().st_mtime_ns,
                orig_mtime,
                f"Read-only constraint violated on {p}",
            )

    def test_02_audit_finding_r18_bug_01_lazy_pre_mem_uaf_in_source(self) -> None:
        """
        Verify R18-BUG-01 in classic/source/:
          - debug_hooks.h Step 5b & Step 6 call release_early_mem() after valve_hibernate_and_drain_gpu()
          - release_early_mem() (main.c:519-522) frees g_pre_mem without resetting g_mbuf_state = -1
          - try_recover_hibernated_work_at() (main.c:732-767) clears g_mem_hibernating when g_mbuf_state == 1
            without checking g_pre_mem != NULL or g_dm_blocks_n > 0.
        """
        self.assertIn("Step 6: Runtime memory hibernation", self.debug_hooks_h)
        self.assertIn("valve_hibernate_and_drain_gpu();", self.debug_hooks_h)
        self.assertIn("release_early_mem();", self.debug_hooks_h)

        # Verify release_early_mem in main.c
        m_rel = re.search(
            r"static\s+void\s+release_early_mem\s*\(\s*void\s*\)\s*\{([^}]*)\}",
            self.main_c,
            re.DOTALL,
        )
        self.assertIsNotNone(m_rel, "release_early_mem() not found in main.c")
        rel_body = m_rel.group(1)
        self.assertIn("free_dm_block(&g_pre_mem", rel_body)
        self.assertIn("g_mbuf_state = -1;", rel_body)

        # Verify try_recover_hibernated_work_at in main.c
        m_rec = re.search(
            r"static\s+int\s+try_recover_hibernated_work_at\s*\(\s*uint32_t\s+cur_flips\s*\)\s*\{(.*?)\n\}",
            self.main_c,
            re.DOTALL,
        )
        self.assertIsNotNone(m_rec, "try_recover_hibernated_work_at() not found in main.c")
        rec_body = m_rec.group(1)
        self.assertIn("if (g_pre_work)", rec_body)
        self.assertIn("g_pre_mem", rec_body)

        # Reproduce R18-BUG-01 deterministically in the unpatched v2.33 model:
        unpatched = V233RuntimeModel("CUSA00001", apply_r18_fixes=False, vout_path=0)
        unpatched.boot_reserve_early_mem()
        unpatched.register_game_buffers(ngame=3)
        unpatched.alloc_work_buffers_from_pre_mem()

        # Run 15 normal frames so g_boot_phase becomes 0 and g_prev_idx >= 0
        for f in range(15):
            step = unpatched.step_frame(PHASE_GAMEPLAY_3BUF, f % 3)
            if f >= 1:
                self.assertTrue(step.dispatched_fg8)
                self.assertTrue(unpatched.is_va_mapped(step.M1_va))

        # Trigger Step 6 Lazy Pre-Mem Release under low-memory pressure
        unpatched.trigger_lowmem_valve(step=6)
        self.assertEqual(unpatched.g_pre_mem, 0)
        self.assertEqual(unpatched.g_mem_hibernating, 1)
        self.assertEqual(unpatched.g_mbuf_state, 1)  # Bug: left as 1!

        # Advance 179 flips (still hibernating)
        for f in range(179):
            step = unpatched.step_frame(PHASE_LOWMEM_STEP6_PRESSURE, f % 3)
            self.assertFalse(step.dispatched_fg8)
            self.assertTrue(step.hibernating)

        # Flip 180 after hibernation: unpatched v2.33 recovers with g_mbuf_state == 1 and dispatches into unmapped g_pre_mem!
        step_180 = unpatched.step_frame(PHASE_RECOVERY, 2)
        self.assertTrue(step_180.recovered_this_frame)
        self.assertTrue(step_180.dispatched_fg8)
        self.assertFalse(
            unpatched.is_va_mapped(step_180.M1_va),
            "Expected unpatched v2.33 to exhibit R18-BUG-01 (dangling M1_va in freed g_pre_mem)",
        )

        # Now verify the R18-fixed model prevents this Use-After-Free 100% of the time:
        patched = V233RuntimeModel("CUSA00001", apply_r18_fixes=True, vout_path=0)
        patched.boot_reserve_early_mem()
        patched.register_game_buffers(ngame=3)
        patched.alloc_work_buffers_from_pre_mem()
        for f in range(15):
            patched.step_frame(PHASE_GAMEPLAY_3BUF, f % 3)
        patched.trigger_lowmem_valve(step=6)
        self.assertEqual(patched.g_mbuf_state, -1)
        for f in range(179):
            step = patched.step_frame(PHASE_LOWMEM_STEP6_PRESSURE, f % 3)
            self.assertFalse(step.dispatched_fg8)
        step_180_fixed = patched.step_frame(PHASE_RECOVERY, 2, allow_reinit_success=True)
        self.assertTrue(step_180_fixed.recovered_this_frame)
        self.assertTrue(step_180_fixed.dispatched_fg8)
        self.assertTrue(
            patched.is_va_mapped(step_180_fixed.M1_va),
            "Patched R18 model must reallocate valid mapped memory before dispatching M1_va",
        )

    def test_03_audit_finding_r18_bug_02_flex_vmap_table_exhaustion_and_bitcode(self) -> None:
        """
        Verify R18-BUG-02 in classic/source/debug_hooks.h:
          - unmap_shave_flex_vmaps() (debug_hooks.h:1422-1441) zeroes va_start = 0, va_end = 0
          - record_flex_vmap_ex() (debug_hooks.h:123-152) never reuses zeroed tombstone slots
          - After 512 Alloc -> Map -> Release cycles, g_flex_vmap_n exceeds MAX_FLEX_VMAP (512),
            causing all subsequent Flex bridges to leak on ReleaseDirectMemory!
        """
        # 1. Verify in Python model (unpatched vs patched) across 1,024 cycles:
        unpatched = V233RuntimeModel("CUSA00002", apply_r18_fixes=False)
        patched = V233RuntimeModel("CUSA00002", apply_r18_fixes=True)

        for cycle in range(1024):
            phys = 0x4000_0000 + (cycle % 4) * 0x0100_0000
            va = 0x8000_0000 + (cycle % 4) * 0x0100_0000
            unpatched.sim_shaved_alloc_and_map(phys, 0x0040_0000, 0x0020_0000, va)
            unpatched.sim_release_direct_memory(phys, 0x0040_0000)

            patched.sim_shaved_alloc_and_map(phys, 0x0040_0000, 0x0020_0000, va)
            patched.sim_release_direct_memory(phys, 0x0040_0000)

        self.assertGreater(
            unpatched.flex_mem_allocated_by_plugin,
            0,
            "Unpatched v2.33 leaks FlexibleMemory after 512 Alloc->Map->Release cycles",
        )
        self.assertEqual(
            patched.flex_mem_allocated_by_plugin,
            0,
            "Patched R18 model must have 0 bytes of leaked FlexibleMemory after 1024 cycles",
        )
        self.assertLessEqual(patched.g_flex_vmap_n, MAX_FLEX_VMAP)

        # 2. If Clang + LLI are available, compile and execute the exact C functions from debug_hooks.h!
        if self.clang and self.lli:
            c_harness = r"""
#include <stdint.h>
#include <stdio.h>
#include <sys/types.h>

#define MAX_FLEX_VMAP 512
typedef struct {
    uintptr_t va_start;
    uintptr_t va_end;
    off_t     shaved_off;
    size_t    shaved_len;
    size_t    orig_len;
} FlexVmap;

static FlexVmap g_flex_vmap[MAX_FLEX_VMAP];
static volatile uint32_t g_flex_vmap_n = 0;
static int g_unmapped_total = 0;

static void record_flex_vmap_ex_v233(uintptr_t vs, uintptr_t ve, off_t shaved_off, size_t shaved_len, size_t orig_len)
{
    uint32_t cur = g_flex_vmap_n;
    if (cur > MAX_FLEX_VMAP) cur = MAX_FLEX_VMAP;
    for (uint32_t i = 0; i < cur; i++) {
        if (g_flex_vmap[i].va_start == vs && g_flex_vmap[i].va_end == ve) {
            g_flex_vmap[i].shaved_off = shaved_off;
            g_flex_vmap[i].shaved_len = shaved_len;
            g_flex_vmap[i].orig_len   = orig_len;
            return;
        }
    }
    uint32_t idx = __atomic_fetch_add(&g_flex_vmap_n, 1u, __ATOMIC_RELAXED);
    if (idx >= MAX_FLEX_VMAP) return;
    g_flex_vmap[idx].va_start   = vs;
    g_flex_vmap[idx].va_end     = ve;
    g_flex_vmap[idx].shaved_off = shaved_off;
    g_flex_vmap[idx].shaved_len = shaved_len;
    g_flex_vmap[idx].orig_len   = orig_len;
}

static void record_flex_vmap_ex_r18_fixed(uintptr_t vs, uintptr_t ve, off_t shaved_off, size_t shaved_len, size_t orig_len)
{
    uint32_t cur = g_flex_vmap_n;
    if (cur > MAX_FLEX_VMAP) cur = MAX_FLEX_VMAP;
    for (uint32_t i = 0; i < cur; i++) {
        if ((g_flex_vmap[i].va_start == vs && g_flex_vmap[i].va_end == ve) ||
            (g_flex_vmap[i].va_start == 0 && g_flex_vmap[i].va_end == 0)) {
            g_flex_vmap[i].va_start   = vs;
            g_flex_vmap[i].va_end     = ve;
            g_flex_vmap[i].shaved_off = shaved_off;
            g_flex_vmap[i].shaved_len = shaved_len;
            g_flex_vmap[i].orig_len   = orig_len;
            return;
        }
    }
    if (cur >= MAX_FLEX_VMAP) return;
    uint32_t idx = __atomic_fetch_add(&g_flex_vmap_n, 1u, __ATOMIC_RELAXED);
    if (idx >= MAX_FLEX_VMAP) return;
    g_flex_vmap[idx].va_start   = vs;
    g_flex_vmap[idx].va_end     = ve;
    g_flex_vmap[idx].shaved_off = shaved_off;
    g_flex_vmap[idx].shaved_len = shaved_len;
    g_flex_vmap[idx].orig_len   = orig_len;
}

static void unmap_shave_flex_vmaps(off_t release_off, size_t release_len)
{
    if (release_len == 0) return;
    off_t rel_end = release_off + (off_t)release_len;
    uint32_t n = g_flex_vmap_n;
    if (n > MAX_FLEX_VMAP) n = MAX_FLEX_VMAP;
    for (uint32_t i = 0; i < n; i++) {
        uintptr_t vs = g_flex_vmap[i].va_start;
        uintptr_t ve = g_flex_vmap[i].va_end;
        if (vs == 0 || ve <= vs) continue;
        size_t orig_span = g_flex_vmap[i].orig_len ? g_flex_vmap[i].orig_len : g_flex_vmap[i].shaved_len;
        off_t sh_off = g_flex_vmap[i].shaved_off;
        off_t sh_end = sh_off + (off_t)orig_span;
        if (sh_off < rel_end && sh_end > release_off) {
            g_unmapped_total++;
            g_flex_vmap[i].va_start   = 0;
            g_flex_vmap[i].va_end     = 0;
            g_flex_vmap[i].shaved_off = 0;
            g_flex_vmap[i].shaved_len = 0;
            g_flex_vmap[i].orig_len   = 0;
        }
    }
}

int main(void) {
    /* 1. Test unpatched v2.33 over 700 unique VA cycles */
    g_flex_vmap_n = 0;
    g_unmapped_total = 0;
    for (uint32_t c = 0; c < 700; c++) {
        uintptr_t vs = 0x10000000ULL + (uintptr_t)c * 0x10000ULL;
        uintptr_t ve = vs + 0x8000ULL;
        off_t off = (off_t)(0x20000000ULL + (uintptr_t)c * 0x10000ULL);
        record_flex_vmap_ex_v233(vs, ve, off, 0x8000ULL, 0x10000ULL);
        unmap_shave_flex_vmaps(off, 0x10000ULL);
    }
    int unpatched_unmapped = g_unmapped_total;

    /* 2. Test R18-fixed version over 700 unique VA cycles */
    for (uint32_t i = 0; i < MAX_FLEX_VMAP; i++) {
        g_flex_vmap[i].va_start = 0;
        g_flex_vmap[i].va_end = 0;
    }
    g_flex_vmap_n = 0;
    g_unmapped_total = 0;
    for (uint32_t c = 0; c < 700; c++) {
        uintptr_t vs = 0x10000000ULL + (uintptr_t)c * 0x10000ULL;
        uintptr_t ve = vs + 0x8000ULL;
        off_t off = (off_t)(0x20000000ULL + (uintptr_t)c * 0x10000ULL);
        record_flex_vmap_ex_r18_fixed(vs, ve, off, 0x8000ULL, 0x10000ULL);
        unmap_shave_flex_vmaps(off, 0x10000ULL);
    }
    int fixed_unmapped = g_unmapped_total;

    printf("UNPATCHED=%d FIXED=%d\n", unpatched_unmapped, fixed_unmapped);
    return (unpatched_unmapped == 512 && fixed_unmapped == 700) ? 0 : 1;
}
"""
            with tempfile.TemporaryDirectory() as tmpdir:
                c_file = Path(tmpdir) / "test_vmap.c"
                bc_file = Path(tmpdir) / "test_vmap.bc"
                c_file.write_text(c_harness, encoding="utf-8")
                subprocess.run(
                    [self.clang, "-O1", "-emit-llvm", "-c", str(c_file), "-o", str(bc_file)],
                    check=True,
                    capture_output=True,
                )
                run_res = subprocess.run(
                    [self.lli, str(bc_file)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(run_res.returncode, 0, f"Bitcode test failed: {run_res.stdout} {run_res.stderr}")
                self.assertIn("UNPATCHED=512 FIXED=700", run_res.stdout)

    def test_04_audit_finding_r18_bug_03_2buf_3buf_reversibility_and_pbuf_history(self) -> None:
        """
        Verify R18-BUG-03 in classic/source/main.c & fg8_table.h:
          - In unpatched v2.33, transitioning MENU_2BUF (0,1,0,1,0,1) -> GAMEPLAY_3BUF (0,1,2)
            resets g_runtime_2buf=0 and g_vq_on=0, but leaves g_pbuf allocated and continues
            redirecting *P_io to g_pbuf because flip_inner_ex checks `if (!pcopy && g_pbuf && ...)`
            and fg8_resolve_2buf_history() does not gate on two_buf_active or sequence continuity.
        """
        unpatched = V233RuntimeModel("CUSA00003", apply_r18_fixes=False)
        unpatched.boot_reserve_early_mem()
        unpatched.register_game_buffers(ngame=3)
        unpatched.alloc_work_buffers_from_pre_mem()

        # Enter MENU_2BUF (alternating 0, 1 for 8 flips)
        for i in range(8):
            step = unpatched.step_frame(PHASE_MENU_2BUF, i % 2)
        self.assertEqual(unpatched.g_runtime_2buf, 1)
        self.assertNotEqual(unpatched.g_pbuf, 0)

        # Now transition to GAMEPLAY_3BUF (0, 1, 2, 0, 1, 2)
        for i in range(6):
            step = unpatched.step_frame(PHASE_GAMEPLAY_3BUF, i % 3)
        self.assertEqual(unpatched.g_runtime_2buf, 0)
        # In unpatched v2.33, even after g_runtime_2buf == 0, step.used_pbuf is STILL True!
        self.assertTrue(
            step.used_pbuf,
            "Expected unpatched v2.33 to keep redirecting P to g_pbuf in 3-buf mode (R18-BUG-03)",
        )

        # Now verify R18-fixed model cleanly disables pbuf redirection as soon as 3-buf mode is active
        patched = V233RuntimeModel("CUSA00003", apply_r18_fixes=True)
        patched.boot_reserve_early_mem()
        patched.register_game_buffers(ngame=3)
        patched.alloc_work_buffers_from_pre_mem()
        for i in range(8):
            step = patched.step_frame(PHASE_MENU_2BUF, i % 2)
        self.assertEqual(patched.g_runtime_2buf, 1)
        self.assertTrue(step.used_pbuf)

        # Transition to 3-buf (index 2 appears)
        step_3buf = patched.step_frame(PHASE_GAMEPLAY_3BUF, 2)
        self.assertEqual(patched.g_runtime_2buf, 0)
        self.assertEqual(patched.g_vq_on, 0)
        self.assertFalse(
            step_3buf.used_pbuf,
            "Patched R18 model must immediately stop redirecting P to g_pbuf on 2-buf -> 3-buf transition",
        )

    def test_05_audit_finding_r18_bug_04_stale_pyramid_warm_on_repeat_and_skip(self) -> None:
        """
        Verify R18-BUG-04 in classic/source/main.c:
          - Repeat flip (index == g_prev_idx -> P == C) during LOADING_SCREEN sets P == C == g_pyr_last_cur_base,
            falsely setting FG8_FLAG_PYR_WARM (0x1000u) in unpatched v2.33.
          - Skipped flip (!dcb_valid) without pyr_cache_invalidate() leaves stale g_pyr_last_cur_base.
        """
        unpatched = V233RuntimeModel("CUSA00004", apply_r18_fixes=False)
        unpatched.boot_reserve_early_mem()
        unpatched.register_game_buffers(ngame=3)
        unpatched.alloc_work_buffers_from_pre_mem()

        unpatched.step_frame(PHASE_GAMEPLAY_3BUF, 0)
        unpatched.step_frame(PHASE_GAMEPLAY_3BUF, 1)
        # Repeat index 1 (static loading frame: P == buf[1], C == buf[1], g_pyr_last_cur_base == buf[1])
        repeat_step = unpatched.step_frame(PHASE_LOADING_SCREEN, 1)
        self.assertEqual(repeat_step.P_va, repeat_step.C_va)
        self.assertTrue(
            repeat_step.pyr_warm,
            "Expected unpatched v2.33 to falsely set FG8_FLAG_PYR_WARM on P == C repeat flip (R18-BUG-04)",
        )

        # Verify patched model never sets pyr_warm on repeat flip or after skipped frame
        patched = V233RuntimeModel("CUSA00004", apply_r18_fixes=True)
        patched.boot_reserve_early_mem()
        patched.register_game_buffers(ngame=3)
        patched.alloc_work_buffers_from_pre_mem()
        patched.step_frame(PHASE_GAMEPLAY_3BUF, 0)
        patched.step_frame(PHASE_GAMEPLAY_3BUF, 1)
        repeat_step_fixed = patched.step_frame(PHASE_LOADING_SCREEN, 1)
        self.assertFalse(repeat_step_fixed.pyr_warm)
        self.assertFalse(repeat_step_fixed.dispatched_fg8)

    def test_06_audit_finding_r18_bug_05_adapt_ini_clobber_and_false_shave_escalation(self) -> None:
        """
        Verify R18-BUG-05 in classic/source/debug_hooks.h & main.c:
          - crash_sig_handler() truncates adapt-<TITLE>.ini without preserving two_buffer / vout_path / gv_conv.
          - save_adapt_ini() escalates shave_mode=2, reg_mode=1 on g_shave_n > 0 even when g_mem_hibernate_count == 0.
        """
        unpatched = V233RuntimeModel("CUSA00005", apply_r18_fixes=False, vout_path=1)
        unpatched.g_runtime_2buf = 1
        unpatched.save_adapt_ini()
        self.assertEqual(unpatched.adapt_ini["two_buffer"], 1)
        self.assertEqual(unpatched.adapt_ini["vout_path"], 1)

        # Simulate OOM crash in unpatched v2.33 -> clobbers two_buffer and vout_path!
        after_crash = unpatched.simulate_crash_sig_handler(is_oom_crash=True)
        self.assertEqual(after_crash["two_buffer"], 0)
        self.assertEqual(after_crash["vout_path"], 0)

        # Now test R18-fixed version -> preserves two_buffer and vout_path!
        patched = V233RuntimeModel("CUSA00005", apply_r18_fixes=True, vout_path=1)
        patched.g_runtime_2buf = 1
        patched.save_adapt_ini()
        after_crash_fixed = patched.simulate_crash_sig_handler(is_oom_crash=True)
        self.assertEqual(after_crash_fixed["two_buffer"], 1)
        self.assertEqual(after_crash_fixed["vout_path"], 1)
        self.assertEqual(after_crash_fixed["shave_mode"], 2)
        self.assertEqual(after_crash_fixed["reg_mode"], 1)

    # ==================================================================
    # 2. Full End-to-End Deterministic Stateful Fuzzer (120 Lifecycles)
    # ==================================================================
    def test_07_stateful_lifecycle_fuzzer_120_lifecycles_all_5_invariants(self) -> None:
        """
        Executes 120 randomized lifecycles of 250..500 frames each (>44,000 frames total)
        across all 8 required lifecycle states:
          BOOT -> MENU_2BUF -> LOADING_SCREEN -> GAMEPLAY_3BUF ->
          DYNAMIC_RES_CHANGE -> CUTSCENE_30FPS -> LOWMEM_STEP6_PRESSURE -> RECOVERY
        Asserts all 5 R18 safety invariants on every single frame.
        """
        rng = random.Random(20261018)
        num_lifecycles = 120
        total_frames_executed = 0
        phase_frame_counts: Dict[str, int] = {p: 0 for p in ALL_PHASES}
        total_step6_hibernations = 0
        total_recoveries = 0
        total_2buf_to_3buf_switches = 0
        total_res_changes = 0
        total_shave_map_release_cycles = 0

        for lc_idx in range(num_lifecycles):
            title_id = CUSA_PROFILES[lc_idx % len(CUSA_PROFILES)]
            vout_path = 1 if (lc_idx % 3 == 0) else 0
            ngame = 2 if (lc_idx % 7 == 0) else 3
            target_frames = rng.randint(250, 500)

            model = V233RuntimeModel(title_id=title_id, apply_r18_fixes=True, vout_path=vout_path)
            model.boot_reserve_early_mem()
            model.register_game_buffers(ngame=ngame, width=1920, height=1080, pitch=1920)
            model.alloc_work_buffers_from_pre_mem()

            # Build a randomized schedule visiting all 8 lifecycle phases in this run
            # Ensuring LOWMEM_STEP6_PRESSURE + RECOVERY has >= 185 frames when scheduled
            p_boot = rng.randint(10, 18)
            p_menu = rng.randint(18, 30)
            p_load = rng.randint(12, 22)
            p_game = rng.randint(20, 40)
            p_dres = rng.randint(12, 24)
            p_cuts = rng.randint(15, 28)
            p_lmem = rng.randint(20, 40)
            p_rec = max(165, target_frames - (p_boot + p_menu + p_load + p_game + p_dres + p_cuts + p_lmem))

            schedule: List[Tuple[str, int]] = [
                (PHASE_BOOT, p_boot),
                (PHASE_MENU_2BUF, p_menu),
                (PHASE_LOADING_SCREEN, p_load),
                (PHASE_GAMEPLAY_3BUF, p_game),
                (PHASE_DYNAMIC_RES_CHANGE, p_dres),
                (PHASE_CUTSCENE_30FPS, p_cuts),
                (PHASE_LOWMEM_STEP6_PRESSURE, p_lmem),
                (PHASE_RECOVERY, p_rec),
            ]

            prev_dispatched_c_va = 0
            prev_frame_invalidated_pyr = True
            prev_was_2buf = False

            for phase, count in schedule:
                # Phase entry actions
                if phase == PHASE_DYNAMIC_RES_CHANGE:
                    res_choices = [
                        (1280, 720, 1280),
                        (1600, 900, 1664),
                        (1920, 1080, 1984),
                        (2560, 1440, 2560),
                    ]
                    new_w, new_h, new_pitch = rng.choice(
                        [r for r in res_choices if r != (model.g_bw, model.g_bh, model.g_pitch)]
                    )
                    if rng.random() < 0.5:
                        # Full buffer re-registration
                        model.register_game_buffers(ngame=ngame, width=new_w, height=new_h, pitch=new_pitch)
                    else:
                        # Dynamic resolution change in-place
                        model.g_bw = new_w
                        model.g_bh = new_h
                        model.g_pitch = new_pitch
                    prev_frame_invalidated_pyr = True
                    total_res_changes += 1

                elif phase == PHASE_LOWMEM_STEP6_PRESSURE:
                    # Exercise Shave + Flex VMap cycles (Invariant 2)
                    n_cycles = rng.randint(6, 16)
                    for c in range(n_cycles):
                        phys = 0x9000_0000 + c * 0x0020_0000
                        va = 0xA000_0000 + c * 0x0020_0000
                        ok = model.sim_shaved_alloc_and_map(phys, 0x0010_0000, 0x0008_0000, va)
                        self.assertTrue(ok, f"[{title_id}] Shave/FlexVmap slot overflow on cycle {c}")
                        unmapped = model.sim_release_direct_memory(phys, 0x0010_0000)
                        self.assertEqual(unmapped, 1, f"[{title_id}] Flex bridge not unmapped on release")
                        total_shave_map_release_cycles += 1

                    # Trigger Step 6 (or Step 2) memory pressure valve
                    if not model.g_early_released and model.g_pre_mem != 0:
                        model.trigger_lowmem_valve(step=6)
                    else:
                        model.trigger_lowmem_valve(step=2)
                    total_step6_hibernations += 1
                    prev_frame_invalidated_pyr = True

                for f_i in range(count):
                    # Choose buffer index according to current phase
                    dcb_valid = True
                    if phase == PHASE_BOOT:
                        idx = f_i % ngame
                    elif phase == PHASE_MENU_2BUF:
                        idx = f_i % 2
                    elif phase == PHASE_LOADING_SCREEN:
                        if f_i % 3 != 0 and model.g_prev_idx >= 0:
                            idx = model.g_prev_idx  # Static repeat flip!
                        else:
                            idx = (f_i // 3) % ngame
                        if f_i == count // 2:
                            dcb_valid = False  # Simulate skipped / non-renderable flip
                    elif phase == PHASE_GAMEPLAY_3BUF:
                        idx = f_i % ngame
                    elif phase == PHASE_DYNAMIC_RES_CHANGE:
                        idx = f_i % ngame
                    elif phase == PHASE_CUTSCENE_30FPS:
                        idx = f_i % 2
                    elif phase == PHASE_LOWMEM_STEP6_PRESSURE:
                        idx = f_i % ngame
                    elif phase == PHASE_RECOVERY:
                        idx = f_i % ngame
                    else:
                        idx = f_i % ngame

                    was_2buf_before = bool(model.g_cfg_two_buffer or model.g_runtime_2buf)
                    step = model.step_frame(phase, idx, dcb_valid=dcb_valid, allow_reinit_success=True)
                    is_2buf_after = bool(model.g_cfg_two_buffer or model.g_runtime_2buf)

                    if was_2buf_before and not is_2buf_after:
                        total_2buf_to_3buf_switches += 1
                    if step.recovered_this_frame:
                        total_recoveries += 1

                    total_frames_executed += 1
                    phase_frame_counts[phase] += 1

                    # ----------------------------------------------------------
                    # ASSERT INVARIANT 1: Zero Use-After-Free on g_pre_mem / M1..M6
                    # ----------------------------------------------------------
                    if step.dispatched_fg8 or step.dispatched_post:
                        self.assertEqual(model.g_mem_hibernating, 0)
                        self.assertEqual(model.g_mbuf_state, 1)
                        self.assertNotEqual(step.M1_va, 0)
                        self.assertTrue(
                            model.is_va_mapped(step.M1_va),
                            f"[{title_id} lc={lc_idx} frame={step.frame_no} phase={phase}] "
                            f"INVARIANT 1 VIOLATION: Dispatched M1_va={hex(step.M1_va)} is unmapped!",
                        )
                        for m_idx in range(model.g_mbase, model.g_nbuf):
                            m_va = model.g_buf.get(m_idx, 0)
                            self.assertTrue(
                                model.is_va_mapped(m_va),
                                f"[{title_id}] Work buffer idx={m_idx} VA={hex(m_va)} is unmapped!",
                            )

                    # ----------------------------------------------------------
                    # ASSERT INVARIANT 2: Zero FlexVMap Leaks
                    # ----------------------------------------------------------
                    self.assertEqual(
                        len(model.active_flex_bridges),
                        0,
                        f"[{title_id}] Active flex bridges leaked after ReleaseDirectMemory",
                    )
                    self.assertLessEqual(model.g_flex_vmap_n, MAX_FLEX_VMAP)
                    self.assertLessEqual(model.g_shave_n, MAX_SHAVES)

                    # ----------------------------------------------------------
                    # ASSERT INVARIANT 3: Clean 2-Buf <-> 3-Buf Reversibility
                    # ----------------------------------------------------------
                    if not is_2buf_after:
                        self.assertFalse(
                            step.used_pbuf,
                            f"[{title_id} lc={lc_idx} frame={step.frame_no}] "
                            f"INVARIANT 3 VIOLATION: used_pbuf=True while in 3-buffer mode!",
                        )
                        self.assertEqual(model.g_vq_on, 0)

                    # ----------------------------------------------------------
                    # ASSERT INVARIANT 4: No Stale Pyramid Reuse (FG8_FLAG_PYR_WARM)
                    # ----------------------------------------------------------
                    if was_2buf_before != is_2buf_after:
                        self.assertFalse(
                            step.pyr_warm,
                            f"[{title_id} lc={lc_idx} frame={step.frame_no} phase={phase}] "
                            f"INVARIANT 4 VIOLATION: FG8_FLAG_PYR_WARM set on 2-buf<->3-buf transition frame!",
                        )
                    if step.pyr_warm:
                        self.assertFalse(
                            prev_frame_invalidated_pyr,
                            f"[{title_id} lc={lc_idx} frame={step.frame_no} phase={phase}] "
                            f"INVARIANT 4 VIOLATION: FG8_FLAG_PYR_WARM set immediately after invalidation/skip!",
                        )
                        self.assertNotEqual(
                            step.P_va,
                            step.C_va,
                            f"[{title_id}] INVARIANT 4 VIOLATION: FG8_FLAG_PYR_WARM set on repeat flip P == C!",
                        )
                        self.assertEqual(
                            step.P_va,
                            prev_dispatched_c_va,
                            f"[{title_id}] INVARIANT 4 VIOLATION: P_va != previous frame's C_va/pbuf!",
                        )

                    # Update tracking for next frame's Invariant 4 check
                    if not step.dispatched_fg8 or step.hibernating or not dcb_valid:
                        prev_frame_invalidated_pyr = True
                        prev_dispatched_c_va = 0
                    else:
                        prev_frame_invalidated_pyr = False
                        prev_dispatched_c_va = model.g_pbuf if step.used_pbuf else step.C_va
                    prev_was_2buf = is_2buf_after

                    # ----------------------------------------------------------
                    # ASSERT INVARIANT 5: Zero Extra DirectMemory in L2 LOWMEM
                    # ----------------------------------------------------------
                    self.assertEqual(
                        model.g_pbuf_dm_bytes,
                        0,
                        f"[{title_id}] INVARIANT 5 VIOLATION: g_pbuf consumed DirectMemory!",
                    )

            # End of lifecycle: verify save_adapt_ini and crash handler preservation
            saved = model.save_adapt_ini()
            self.assertEqual(saved["vout_path"], vout_path)
            after_crash = model.simulate_crash_sig_handler(is_oom_crash=True)
            self.assertEqual(after_crash["vout_path"], vout_path)
            self.assertEqual(after_crash["shave_mode"], 2)
            self.assertEqual(after_crash["reg_mode"], 1)

            # Cleanup lifecycle allocations
            model.free_all_dm_blocks()
            model.free_pbuf_history()
            model.release_early_mem()
            self.assertEqual(model.direct_mem_allocated_by_plugin, 0)
            self.assertEqual(model.flex_mem_allocated_by_plugin, 0)

        # Verify fuzzer coverage targets met or exceeded R18 requirements
        self.assertGreaterEqual(num_lifecycles, 100)
        self.assertGreaterEqual(total_frames_executed, 25000)
        for p in ALL_PHASES:
            self.assertGreater(phase_frame_counts[p], 1000, f"Phase {p} under-tested")
        self.assertEqual(total_step6_hibernations, num_lifecycles)
        self.assertEqual(total_recoveries, num_lifecycles)
        self.assertGreater(total_2buf_to_3buf_switches, 50)
        self.assertEqual(total_res_changes, num_lifecycles)
        self.assertGreater(total_shave_map_release_cycles, 1000)

    def test_08_pbuf_history_copy_skipped_on_3buf_ring_of_any_title(self) -> None:
        """
        build_dcb_flow8 gate (q-pcopy): with two_buffer=1 in the config and a 3-buffer ring, any title no longer
        copies C into g_pbuf (copy_dst == 0, pcopy8 not dispatched) and P is the game's previous buffer. With a
        2-buffer ring (at RegisterBuffers or detected at runtime) the copy and the P redirect to g_pbuf are what
        they were before, whatever the title. Pacing state (g_vq_on, g_pcopy_avail, g_pbuf) is untouched.
        """
        self.assertTrue(PBUF_SKIP_GATE, "gate `int pbuf_skip = ...` missing from build_dcb_flow8")
        self.assertEqual(PBUF_SKIP_TITLES, (), "the history-copy skip must not depend on the title")

        def boot(title: str, ngame: int) -> V233RuntimeModel:
            m = V233RuntimeModel(title, apply_r18_fixes=True)
            m.boot_reserve_early_mem()
            m.register_game_buffers(ngame=ngame)
            m.register_buffers_two_buffer_cfg()
            self.assertTrue(m.alloc_work_buffers_from_pre_mem())
            return m

        for title in ("CUSA18723", "CUSA00001"):
            # 1. 3-buffer ring: no copy, P = previous game buffer, pacing state unchanged.
            m = boot(title, 3)
            steps = [m.step_frame(PHASE_GAMEPLAY_3BUF, i % 3) for i in range(12)]
            for i, st in enumerate(steps[1:], start=1):
                self.assertFalse(st.copy_to_pbuf, f"[{title}] frame {i}: C copied into g_pbuf on a 3-buffer ring")
                self.assertFalse(st.used_pbuf, f"[{title}] frame {i}: P redirected to g_pbuf")
                self.assertEqual(st.P_va, m.g_buf[(i - 1) % 3], f"[{title}] frame {i}: P is not the previous game buffer")
            self.assertEqual((m.g_vq_on, m.g_pcopy_avail), (1, 1))
            self.assertNotEqual(m.g_pbuf, 0)

            # 2. Ring turns 2-buffer at runtime: the copy comes back, and P follows g_pbuf after one frame.
            for i in range(8):
                st = m.step_frame(PHASE_MENU_2BUF, i % 2)
            self.assertEqual(m.g_runtime_2buf, 1)
            self.assertTrue(st.copy_to_pbuf)
            self.assertTrue(st.used_pbuf)
            self.assertEqual(st.P_va, m.g_pbuf)

        # 3. 2-buffer ring at RegisterBuffers, whatever the title: as before.
        for title, ngame in (("CUSA18723", 2), ("CUSA00001", 2)):
            m = boot(title, ngame)
            steps = [m.step_frame(PHASE_GAMEPLAY_3BUF, i % ngame) for i in range(12)]
            for i, st in enumerate(steps[1:], start=1):
                self.assertTrue(st.copy_to_pbuf, f"[{title} ngame={ngame}] frame {i}: history copy missing")
            for i, st in enumerate(steps[2:], start=2):
                self.assertTrue(st.used_pbuf, f"[{title} ngame={ngame}] frame {i}: P not read from g_pbuf")
                self.assertEqual(st.P_va, m.g_pbuf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
