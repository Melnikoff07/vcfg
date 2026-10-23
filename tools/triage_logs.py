#!/usr/bin/env python3
"""
VCFG Telemetry Triage, Hidden Symptom Sub-Classifier & Supported Title Miner (R20).

Usage:
  python tools/triage_logs.py <dir_or_zip_or_file> [...]
      [--version <ver>]
      [--export-traces [path]]
      [--report-md <path>]
      [--json]

1. Classifies every telemetry report into 5 strict buckets:
   - BUCKET_1_OLD_VERSION: Captured on plugin version < current release version.
   - BUCKET_2_L2_NO_CRASH: Clean run or L2 LOWMEM / DirectMemory shave working without crash.
   - BUCKET_3_NEW_TITLE_CRASH: Real crash ([CRASH] or fatal ENOMEM) in an unverified CUSAxxxxx title on current version.
   - BUCKET_4_VERIFIED_REGRESSION: Crash, fatal ENOMEM, or INERT: NO MBUF in a supported/verified CUSAxxxxx family on current version.
   - BUCKET_5_NEW_SUPPORTED_CANDIDATE: Unverified CUSAxxxxx (not in launcher/src/main.c k_sup or overlay/title_families.h)
     showing a healthy gameplay session (Present # >= 300, mids_shown >= 100 or FG ON, no [CRASH], no fatal ENOMEM, no hidden symptoms).
     Automatically synthesizes the ready C entry for k_sup in launcher/src/main.c and flags whether auto_2buf is required.

2. Detects 3 hidden telemetry symptoms even when a session does not crash with [CRASH]:
   - SYMPTOM_LOW_FPS_LOCK: In 3D gameplay (Present # >= 300), average real flips/s < 20.0 or mids_shown == 0 while FG is enabled.
   - SYMPTOM_AUDIO_DM_STARVATION: DirectMemory ERR (0x8002000c) refusals on small allocations 0x4000..0x100000 (16 KB - 1 MB) after render start.
   - SYMPTOM_LOADING_FREEZE: Log terminates on a series of RepeatCount >= 10 immediately after AllocateDirectMemory / MapDirectMemory without further Present #.

3. With --export-traces (or --extract-traces), idempotently extracts deterministic DirectMemory + RegisterBuffers
   boot traces into tools/tests/real_traces.json (or a custom JSON path).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import zipfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
FAMILIES_H = ROOT / "overlay" / "title_families.h"
LAUNCHER_MAIN_C = ROOT / "launcher" / "src" / "main.c"
REAL_TRACES_JSON = ROOT / "tools" / "tests" / "real_traces.json"

BUCKET_KEYS: tuple[str, ...] = (
    "BUCKET_1_OLD_VERSION",
    "BUCKET_2_L2_NO_CRASH",
    "BUCKET_3_NEW_TITLE_CRASH",
    "BUCKET_4_VERIFIED_REGRESSION",
    "BUCKET_5_NEW_SUPPORTED_CANDIDATE",
)

SYMPTOM_KEYS: tuple[str, ...] = (
    "SYMPTOM_LOW_FPS_LOCK",
    "SYMPTOM_AUDIO_DM_STARVATION",
    "SYMPTOM_LOADING_FREEZE",
)

TITLE_ATTR_RE = re.compile(r"\btitle=(?:'[^']*'|\"[^\"]*\")")
CUSA_TOKEN_RE = re.compile(r"\b(CUSA\d{5})\b")
SECTION_RE = re.compile(r"^[ \t]*====\s+(.+?)\s+====[ \t]*$", re.M)


def scrub_game_names(raw: str) -> str:
    """Strip any title='...' metadata from launcher game lists on ingestion."""
    return TITLE_ATTR_RE.sub("title='REDACTED'", raw)


def verify_no_game_names(output_text: str, forbidden_names: list[str] | None = None) -> bool:
    """Verify that zero game titles/names leak into generated output."""
    if re.search(r"title='(?!REDACTED')[^']+'", output_text):
        raise ValueError("Forbidden raw title='...' attribute found in output")
    if forbidden_names:
        low = output_text.lower()
        for name in forbidden_names:
            clean = name.strip().lower()
            if clean and len(clean) >= 3 and clean in low:
                raise ValueError(f"Forbidden title name leaked into output: {clean!r}")
    return True


def load_current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "2.33"


def load_k_sup(launcher_path: pathlib.Path = LAUNCHER_MAIN_C) -> tuple[set[str], set[str], set[str]]:
    """Parse k_sup, auto_2buf, and auto_alt CUSAxxxxx IDs from launcher/src/main.c (Read-Only)."""
    if not launcher_path.exists():
        return set(), set(), set()
    text = launcher_path.read_text(encoding="utf-8")
    k_sup_ids: set[str] = set()
    sup_block = re.search(r"k_sup\[\]\s*=\s*\{(.*?)\};", text, re.S)
    if sup_block:
        k_sup_ids = set(re.findall(r'"(CUSA\d{5})"', sup_block.group(1)))

    auto_2buf_ids: set[str] = set()
    a2_block = re.search(r"auto_2buf\s*\([^)]*\)\s*\{(.*?)\}", text, re.S)
    if a2_block:
        auto_2buf_ids = set(re.findall(r'"(CUSA\d{5})"', a2_block.group(1)))

    auto_alt_ids: set[str] = set()
    aa_block = re.search(r"auto_alt\s*\([^)]*\)\s*\{(.*?)\}", text, re.S)
    if aa_block:
        auto_alt_ids = set(re.findall(r'"(CUSA\d{5})"', aa_block.group(1)))

    return k_sup_ids, auto_2buf_ids, auto_alt_ids


def load_verified_families(
    families_path: pathlib.Path = FAMILIES_H,
    launcher_path: pathlib.Path = LAUNCHER_MAIN_C,
) -> tuple[set[str], dict[str, str]]:
    """Load all supported CUSAxxxxx IDs and regional aliases from overlay/title_families.h and launcher/src/main.c."""
    verified: set[str] = set()
    aliases: dict[str, str] = {}

    if families_path.exists():
        text = families_path.read_text(encoding="utf-8")
        fam_block = re.search(r"k_ver\[\]\s*=\s*\{(.*?)\};", text, re.S)
        if fam_block:
            verified.update(re.findall(r'"(CUSA\d{5})"', fam_block.group(1)))
        alias_block = re.search(r"k_vcfg_title_aliases\[\]\s*=\s*\{(.*?)\};", text, re.S)
        if alias_block:
            for m in re.finditer(r'\{\s*"(CUSA\d{5})"\s*,\s*"(CUSA\d{5})"\s*\}', alias_block.group(1)):
                aliases[m.group(1)] = m.group(2)
                verified.add(m.group(1))
                verified.add(m.group(2))

    k_sup_ids, _, _ = load_k_sup(launcher_path)
    verified.update(k_sup_ids)
    return verified, aliases


def resolve_family(tid: str, verified: set[str], aliases: dict[str, str]) -> tuple[str, bool]:
    canon = aliases.get(tid, tid)
    is_sup = (canon in verified) or (tid in verified) or (tid in aliases)
    return canon, is_sup


def parse_version_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v or "0.0")
    return tuple(int(x) for x in parts) if parts else (0, 0)


RE_TITLE = re.compile(r"(?:Title:\s*|title[=:\s]+)(CUSA\d{5})\b")
RE_CRASH = re.compile(
    r"===\s*CRASH\s+([A-Z0-9_]+)\s*\(sig=(\d+)\s+code=(-?\d+)\)\s*at\s*([\d.]+)s\s*\|\s*flips=(\d+)"
)
RE_CRASH_TAG = re.compile(r"\[CRASH\](?:\s*([A-Z0-9_]+))?|\bCRASH:\s*([A-Z0-9_]+)|\bCRASH\s+(SIG[A-Z0-9]+)\b")
RE_PLUGIN_LOAD = re.compile(
    r"plugin_load enter:\s*title=(CUSA\d{5}).*?version=v?(\d+\.\d+)(?:.*?dm=0x([0-9a-fA-F]+))?"
)
RE_EARLY_TRY1 = re.compile(
    r"early display memory try 1 \(top\):\s*len=0x([0-9a-fA-F]+)\s+align=0x([0-9a-fA-F]+)\s+type=(\d+)\s+"
    r"range=0x([0-9a-fA-F]+)\.\.0x([0-9a-fA-F]+)\s+r=0x([0-9a-fA-F]+)\s+phys=0x([0-9a-fA-F]+)"
)
RE_GAME_ALLOC = re.compile(
    r"(?:\[\s*t:\d+\s+f:(\d+)\]\s+)?game AllocateDirectMemory\(len=0x([0-9a-fA-F]+)\s+align=0x([0-9a-fA-F]+)\s+"
    r"type=(\d+)\s+range=0x([0-9a-fA-F]+)\.\.0x([0-9a-fA-F]+)\)\s*=\s*0x([0-9a-fA-F]+)\s+phys=0x([0-9a-fA-F]+)"
)
RE_GAME_REL = re.compile(
    r"(?:\[\s*t:\d+\s+f:(\d+)\]\s+)?game ReleaseDirectMemory\(phys=0x([0-9a-fA-F]+)\s+len=0x([0-9a-fA-F]+)\)\s*=\s*0x([0-9a-fA-F]+)"
)
RE_REG_ENTER = re.compile(
    r"(?:\[\s*t:\d+\s+f:(\d+)\]\s+)?RegisterBuffers(?: enter)?:\s*handle=(\d+)\s+start=(\d+)\s+num=(\d+)"
)
RE_REG_ATTR = re.compile(
    r"attr:\s*fmt=0x([0-9a-fA-F]+)(?:\s+\(supported=\d+\))?\s+tiling=(\d+)\s+aspect=(\d+)\s+(\d+)x(\d+)\s+pitch=(\d+)"
)


def extract_plugin_version(text: str, default_ver: str) -> tuple[str, bool]:
    """Extract VCFG plugin/app version while ignoring game APP_VER (e.g. 'CUSAxxxxx version=01.00' or 'GV start: ... version 01.38')."""
    patterns = (
        r"(?m)^#\s*Title:.*?\|\s*Version:\s*v?(\d+\.\d+)",
        r"(?m)^(?:Version|app version|version):\s*v?(\d+\.\d+)",
        r"plugin_load enter:.*?version=v?(\d+\.\d+)",
        r"CLASSIC_BUILD\s+version=v?(\d+\.\d+)",
        r"(?:last game start|game start before):\s*version:\s*v?(\d+\.\d+)",
        r"(?<!GV start: title CUSA\d{5} )(?<!CUSA\d{5} )\b(?:Version|version)[=:\s]+v?([1-9]\d*\.\d+)\b",
    )
    best_pos: int | None = None
    best_ver: str | None = None
    for pat in patterns:
        m = re.search(pat, text)
        if m and (best_pos is None or m.start() < best_pos):
            best_pos = m.start()
            best_ver = m.group(1)
    if best_ver is not None:
        return best_ver, True
    return default_ver, False


def _extract_single_boot_section(boot_txt: str) -> dict[str, Any] | None:
    if "plugin_load enter:" not in boot_txt:
        return None
    boot_txt = "plugin_load enter:" + boot_txt.split("plugin_load enter:")[-1]
    m_load = RE_PLUGIN_LOAD.search(boot_txt)
    if not m_load:
        return None
    tid = m_load.group(1)
    ver = m_load.group(2)
    dm_size = int(m_load.group(3), 16) if m_load.group(3) else 0x128000000
    if "middle lines omitted" in boot_txt:
        return None

    events: list[dict[str, Any]] = []
    lines = boot_txt.splitlines()
    i = 0
    first_2m_phys: int | None = None
    sub_phys_candidates: list[int] = []
    live_blocks: list[tuple[int, int, int]] = []  # (orig_phys, len, alloc_id)
    next_alloc_id = 1
    re_own_alloc = re.compile(
        r"alloc_dm ok:\s*len=0x([0-9a-fA-F]+)\s+align=0x([0-9a-fA-F]+)\s+type=(\d+)\s+"
        r"range=0x0\.\.0x([0-9a-fA-F]+)\s+phys=0x([0-9a-fA-F]+)"
    )
    while i < len(lines):
        ln = lines[i]
        mo = re_own_alloc.search(ln)
        if mo:
            p_own = int(mo.group(5), 16)
            if p_own < 0x40000000:
                sub_phys_candidates.append(p_own)
        ma = RE_GAME_ALLOC.search(ln)
        if ma:
            flips = int(ma.group(1)) if ma.group(1) else 0
            length = int(ma.group(2), 16)
            align = int(ma.group(3), 16)
            mtype = int(ma.group(4))
            rng_s = int(ma.group(5), 16)
            rng_e = int(ma.group(6), 16)
            ret = int(ma.group(7), 16)
            phys = int(ma.group(8), 16)
            if rng_e > dm_size:
                return None
            if ret == 0 and rng_s == 0 and phys < dm_size - 0x4000000:
                if first_2m_phys is None and length >= 0x200000 and (phys & 0x1FFFFF) == 0:
                    first_2m_phys = phys
                elif first_2m_phys is None or phys < first_2m_phys:
                    sub_phys_candidates.append(phys)
            aid = 0
            if ret == 0 and length > 0:
                still_live: list[tuple[int, int, int]] = []
                for lp, llen, lid in live_blocks:
                    if phys < lp + llen and phys + length > lp:
                        events.append({"op": "release_id", "flips": flips, "id": lid})
                    else:
                        still_live.append((lp, llen, lid))
                live_blocks = still_live
                aid = next_alloc_id
                next_alloc_id += 1
                live_blocks.append((phys, length, aid))
            events.append(
                {
                    "op": "alloc",
                    "id": aid,
                    "flips": flips,
                    "s": hex(rng_s),
                    "e": hex(rng_e),
                    "len": hex(length),
                    "align": hex(align),
                    "type": mtype,
                    "orig_ret": hex(ret),
                    "orig_phys": hex(phys),
                }
            )
            i += 1
            continue
        mr = RE_GAME_REL.search(ln)
        if mr:
            r_flips = int(mr.group(1)) if mr.group(1) else 0
            r_phys = int(mr.group(2), 16)
            r_len = int(mr.group(3), 16)
            still_live = []
            for lp, llen, lid in live_blocks:
                if r_phys < lp + llen and r_phys + r_len > lp:
                    events.append({"op": "release_id", "flips": r_flips, "id": lid})
                else:
                    still_live.append((lp, llen, lid))
            live_blocks = still_live
            i += 1
            continue
        mreg = RE_REG_ENTER.search(ln)
        if mreg:
            flips = int(mreg.group(1)) if mreg.group(1) else 0
            start_idx = int(mreg.group(3))
            num_buf = int(mreg.group(4))
            bw, bh, pitch, fmt = 1920, 1080, 1920, 0x80000000
            if i + 1 < len(lines):
                mattr = RE_REG_ATTR.search(lines[i + 1])
                if mattr:
                    fmt = int(mattr.group(1), 16)
                    bw = int(mattr.group(4))
                    bh = int(mattr.group(5))
                    pitch = int(mattr.group(6))
            events.append(
                {
                    "op": "reg_buffers",
                    "flips": flips,
                    "start": start_idx,
                    "num": num_buf,
                    "w": bw,
                    "h": bh,
                    "pitch": pitch,
                    "fmt": hex(fmt),
                }
            )
            i += 1
            continue
        i += 1

    pre_occ = first_2m_phys or 0
    if first_2m_phys:
        valid_subs = [p for p in sub_phys_candidates if 0 <= p < first_2m_phys and p >= (first_2m_phys - 0x200000)]
        if valid_subs:
            pre_occ = min(valid_subs)
    trimmed_events: list[dict[str, Any]] = []
    sim_live: dict[int, int] = {}
    max_cap = dm_size - pre_occ
    for ev in events:
        if ev["op"] == "release_id":
            sim_live.pop(ev["id"], None)
            trimmed_events.append(ev)
        elif ev["op"] == "alloc":
            elen = int(ev["len"], 16)
            if ev["orig_ret"] == "0x0":
                if sum(sim_live.values()) + elen > max_cap or len(trimmed_events) >= 80:
                    break
                sim_live[ev["id"]] = elen
            trimmed_events.append(ev)
        else:
            trimmed_events.append(ev)
    alloc_events = [e for e in trimmed_events if e["op"] == "alloc" and e["orig_ret"] == "0x0"]
    if len(alloc_events) < 1:
        return None
    return {
        "trace_key": f"{tid}_{hex(dm_size)}",
        "title_id": tid,
        "log_version": ver,
        "dm_size": hex(dm_size),
        "pre_occupied_low": hex(pre_occ),
        "events": trimmed_events,
    }


def extract_all_boot_traces(text: str) -> list[dict[str, Any]]:
    """Extract all deterministic DirectMemory + RegisterBuffers boot traces from a log or bundle."""
    results: list[dict[str, Any]] = []
    boot_sec_re = re.compile(
        r"====\s*(?:boot stages|early start stages[^=]*)\s*\(/data/bb_probe/boot-(CUSA\d{5})\.txt[^=]*====\n(.*?)(?=\n[ \t]*====\s+|\Z)",
        re.S,
    )
    matches = list(boot_sec_re.finditer(text))
    if matches:
        for m in matches:
            tr = _extract_single_boot_section(m.group(2))
            if tr and tr["title_id"] != "UNKNOWN":
                results.append(tr)
        return results

    tr = _extract_single_boot_section(text)
    if tr and tr["title_id"] != "UNKNOWN":
        results.append(tr)
    return results


def extract_boot_trace(text: str) -> dict[str, Any] | None:
    """Backward-compatible single boot trace extractor."""
    traces = extract_all_boot_traces(text)
    return traces[0] if traces else None


def _extract_present_flips(text: str) -> int:
    patterns = (
        r"\bPresent\s*(?:#\s*|\s*[:=]\s*)(\d+)",
        r"\bgnm\s*#(\d+)",
        r"\binject\s*#(\d+)",
        r"\bvout\s+(?:inject|fg)\s*#(\d+)",
        r"\bmode4\s*#(\d+)",
        r"\bSubmitAndFlip\s*#(\d+)",
        r"\bVideoOutSubmitFlip\s*#(\d+)",
        r"\bflips=(\d+)",
        r"\bvo_flips=(\d+)",
        r"\bvoSubmitFlips=(\d+)",
        r"\[\s*t:\d+\s+f:(\d+)\]",
        r"reached frame (\d+)",
        r"safe mode (?:activated at )?frame (\d+)",
        r"at flip (\d+)",
        r"frames (\d+) walked",
    )
    max_val = 0
    for pat in patterns:
        for m in re.findall(pat, text, re.I):
            v = int(m)
            if v > max_val:
                max_val = v
    return max_val


def _extract_mids_shown(text: str) -> int | None:
    explicit = [int(x) for x in re.findall(r"\bmids_shown\s*[:=]\s*(\d+)", text, re.I)]
    if explicit:
        return max(explicit)
    cadence_matches = re.findall(
        r"CADENCE\s+\(vblanks on screen\)\s+mid\s+1:(\d+)\s+2:(\d+)\s+3:(\d+)\s+4\+:(\d+)",
        text,
    )
    if cadence_matches:
        return sum(int(a) + int(b) + int(c) + int(d) for a, b, c, d in cadence_matches)
    return None


def _extract_avg_real_fps(text: str) -> float | None:
    explicit_vals: list[float] = []
    for m in re.finditer(
        r"(?:real\s+flips/s\s*[:=]?\s*([\d.]+)|([\d.]+)\s+real\s+flips/s|real[_\s]fps\s*[:=]\s*([\d.]+))",
        text,
        re.I,
    ):
        raw = m.group(1) or m.group(2) or m.group(3)
        if raw:
            try:
                explicit_vals.append(float(raw))
            except ValueError:
                pass
    if explicit_vals:
        return round(sum(explicit_vals) / len(explicit_vals), 2)

    stat_vals = [float(x) for x in re.findall(r"STAT\s+gnm:\s*([\d.]+)\s*flips/s", text)]
    if stat_vals:
        return round(sum(stat_vals) / len(stat_vals), 2)
    return None


def detect_symptoms(
    text: str,
    flips: int | None = None,
    mids_shown: int | None = None,
    avg_real_fps: float | None = None,
    fg_enabled: bool | None = None,
) -> list[str]:
    """Detect hidden session telemetry symptoms (SYMPTOM_LOW_FPS_LOCK, SYMPTOM_AUDIO_DM_STARVATION, SYMPTOM_LOADING_FREEZE)."""
    if flips is None:
        flips = _extract_present_flips(text)
    if mids_shown is None:
        mids_shown = _extract_mids_shown(text)
    if avg_real_fps is None:
        avg_real_fps = _extract_avg_real_fps(text)
    if fg_enabled is None:
        fg_disabled = bool(
            re.search(r"\b(?:framegen\s*=\s*0|fg_off\s*=\s*1|fg\s*=\s*0|FG\s+OFF)\b", text, re.I)
            and not re.search(r"\b(?:FG\s+ON|mode4\s*#\d+|vout\s+fg\s*#\d+)\b", text, re.I)
        )
        fg_enabled = bool(
            re.search(
                r"\b(?:FG\s+ON|framegen\s*=\s*1|fg_off\s*=\s*0|fg\s*=\s*1|FG\s+frames\s+yes|mode4\s*#\d+|vout\s+fg\s*#\d+)\b",
                text,
                re.I,
            )
            or (not fg_disabled and "injection mode 8" in text)
        )

    symptoms: list[str] = []

    # 1. SYMPTOM_LOW_FPS_LOCK: in 3D gameplay (Present # >= 300), average real flips/s < 20.0 OR mids_shown == 0 with FG enabled.
    if flips >= 300:
        low_fps = avg_real_fps is not None and avg_real_fps < 20.0
        zero_mids_with_fg = bool(fg_enabled and mids_shown is not None and mids_shown == 0)
        if low_fps or zero_mids_with_fg:
            symptoms.append("SYMPTOM_LOW_FPS_LOCK")

    # 2. SYMPTOM_AUDIO_DM_STARVATION: DirectMemory ERR (0x8002000c) refusals on small allocations 0x4000..0x100000 (16 KB - 1 MB) after render start.
    render_started = False
    audio_starved = False
    lines = text.splitlines()
    re_render_marker = re.compile(
        r"(?:first\s+SubmitAndFlip|first\s+VideoOutSubmitFlip|Present\s*(?:#\s*|\s*[:=]\s*)[1-9]\d*|"
        r"\bgnm\s*#[1-9]\d*|\binject\s*#\d+|\bmode4\s*#\d+|\bvout\s+(?:fg|inject)\s*#\d+|"
        r"CADENCE\s+\(vblanks|STAT\s+gnm:|render\s+start)",
        re.I,
    )
    re_frame_tag = re.compile(
        r"(?:\[\s*t:\d+\s+f:(\d+)\]|flips=(\d+)|Present\s*(?:#\s*|\s*[:=]\s*)(\d+)|\bframe\s+(\d+))",
        re.I,
    )

    for ln in lines:
        if re_render_marker.search(ln):
            render_started = True
        f_matches = [int(x) for g in re_frame_tag.findall(ln) for x in g if x]
        if any(f > 0 for f in f_matches):
            render_started = True

        ln_low = ln.lower()
        if "0x8002000c" in ln_low or "directmemory err" in ln_low:
            explicit_boot_zero = bool(f_matches and all(f == 0 for f in f_matches) and not render_started)
            is_after_render = (render_started or any(f > 0 for f in f_matches) or flips > 0) and not explicit_boot_zero
            if is_after_render:
                sizes: list[int] = []
                for m_sz in re.finditer(r"(?:len|length|sz|size)\s*[=:]?\s*0x([0-9a-fA-F]+)", ln, re.I):
                    sizes.append(int(m_sz.group(1), 16))
                if not sizes:
                    cleaned_ln = re.sub(r"range=0x[0-9a-fA-F]+\.\.0x[0-9a-fA-F]+", "", ln, flags=re.I)
                    for m_hex in re.finditer(r"0x([0-9a-fA-F]+)", cleaned_ln):
                        val = int(m_hex.group(1), 16)
                        if val not in (0x8002000C, 0x80020023):
                            sizes.append(val)
                if any(0x4000 <= sz <= 0x100000 for sz in sizes):
                    audio_starved = True
                    break

    if audio_starved:
        symptoms.append("SYMPTOM_AUDIO_DM_STARVATION")

    # 3. SYMPTOM_LOADING_FREEZE: log truncates on a series of RepeatCount >= 10 right after AllocateDirectMemory / MapDirectMemory without further Present #.
    re_dm_op = re.compile(
        r"(?:AllocateDirectMemory|AllocDM|DM_ALLOC|MapDirectMemory|MapNamedDirectMemory|MapDirectMemory2|DM_MAP|BatchMap)",
        re.I,
    )
    re_repeat = re.compile(r"Repeat[_\s]?Count\s*(?:>=|[=:])?\s*(\d+)", re.I)
    re_present_ev = re.compile(
        r"(?:Present\s*#\s*\d+|\bgnm\s*#\d+|\binject\s*#\d+|\bmode4\s*#\d+|\bvout\s+(?:fg|inject)\s*#\d+|\bSubmitAndFlip\s*#\d+)",
        re.I,
    )

    last_dm_idx = -1
    repeat_after_dm_count = 0
    max_repeat_after_dm = 0
    present_after_repeat = False

    for idx, ln in enumerate(lines):
        s_ln = ln.strip()
        if not s_ln or s_ln.startswith("---- end of trace") or s_ln.startswith("=== END CRASH"):
            continue
        is_dm = bool(re_dm_op.search(s_ln))
        m_rep = re_repeat.search(s_ln)
        is_pres = bool(re_present_ev.search(s_ln)) and not is_dm and not m_rep

        if is_dm:
            last_dm_idx = idx
            repeat_after_dm_count = 0
            max_repeat_after_dm = 0
            present_after_repeat = False
        if m_rep and last_dm_idx >= 0:
            rep_val = int(m_rep.group(1))
            repeat_after_dm_count += 1
            if rep_val > max_repeat_after_dm:
                max_repeat_after_dm = rep_val
            present_after_repeat = False
        elif is_pres and last_dm_idx >= 0:
            if max_repeat_after_dm > 0 or repeat_after_dm_count > 0:
                present_after_repeat = True
            else:
                # A Present # occurred after DM before any RepeatCount, so reset DM sequence
                last_dm_idx = -1

    if (
        last_dm_idx >= 0
        and (max_repeat_after_dm >= 10 or repeat_after_dm_count >= 10)
        and not present_after_repeat
    ):
        symptoms.append("SYMPTOM_LOADING_FREEZE")

    return symptoms


def analyze_report_text(
    name: str,
    text: str,
    cur_ver: str | None = None,
    verified: set[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Classify a single telemetry log/report into one of the 5 buckets, run symptom detectors, and mine k_sup candidates."""
    text = scrub_game_names(text)
    if cur_ver is None:
        cur_ver = load_current_version()
    if verified is None or aliases is None:
        verified, aliases = load_verified_families()

    m_tid = RE_TITLE.search(text)
    if m_tid:
        tid = m_tid.group(1)
    else:
        m_name_tid = CUSA_TOKEN_RE.search(name)
        if m_name_tid:
            tid = m_name_tid.group(1)
        else:
            m_any_tid = CUSA_TOKEN_RE.search(text)
            tid = m_any_tid.group(1) if m_any_tid else "UNKNOWN"

    ver, has_explicit_ver = extract_plugin_version(text, default_ver=cur_ver)
    canon, is_verified = resolve_family(tid, verified, aliases)

    flips = _extract_present_flips(text)
    mids_shown = _extract_mids_shown(text)
    avg_real_fps = _extract_avg_real_fps(text)

    m_crash = RE_CRASH.search(text)
    m_crash_tag = RE_CRASH_TAG.search(text) if not m_crash else None

    if m_crash:
        sig = m_crash.group(1)
        crash_flips = int(m_crash.group(5))
        if crash_flips > flips:
            flips = crash_flips
        has_crash = True
    elif m_crash_tag:
        sig = m_crash_tag.group(1) or m_crash_tag.group(2) or m_crash_tag.group(3) or "CRASH"
        has_crash = True
    else:
        sig = "NONE"
        has_crash = False

    l2_shave = bool(
        re.search(
            r"L2\s*\(?LOWMEM\)?|LOWMEM|arena shave|memory valve:\s*(?:shrunk|relocated|trimmed)|shave=[1-9]|shaves=[1-9]",
            text,
            re.I,
        )
    )

    # Check for fatal ENOMEM (unhandled 0x80020023 / ENOMEM where L2/shave did not sustain rendering)
    has_explicit_enomem = bool(re.search(r"\b(?:FATAL[_\s]+)?ENOMEM\b", text))
    has_raw_oom_code = bool(
        re.search(r"AllocateDirectMemory\([^)]*\)\s*=\s*0x80020023|AllocDM\([^)]*\)\s*=\s*0x80020023|alloc_fail=[1-9]", text)
    )
    has_fatal_enomem = False
    if not has_crash:
        if has_explicit_enomem and (flips < 300 or not l2_shave):
            has_fatal_enomem = True
            sig = "ENOMEM"
        elif has_raw_oom_code and flips < 120 and not l2_shave:
            has_fatal_enomem = True
            sig = "ENOMEM"

    has_inert_no_mbuf = "INERT: NO MBUF" in text
    has_inert = bool(
        has_inert_no_mbuf
        or re.search(r"inert='INERT:[^']+'|INERT:\s*SAFE|safe mode(?:\s+frame\s+\d+)?:\s*refused", text, re.I)
    )

    fg_disabled = bool(
        re.search(r"\b(?:framegen\s*=\s*0|fg_off\s*=\s*1|fg\s*=\s*0|FG\s+OFF)\b", text, re.I)
        and not re.search(r"\b(?:FG\s+ON|mode4\s*#\d+|vout\s+fg\s*#\d+)\b", text, re.I)
    )
    fg_on = bool(
        not has_inert
        and (
            re.search(
                r"\b(?:FG\s+ON|FG\s+frames\s+yes|mode4\s*#\d+|vout\s+fg\s*#\d+|GPU time of our passes:|PASSES ms:\s*pyr8)\b",
                text,
                re.I,
            )
            or (mids_shown is not None and mids_shown >= 100)
        )
    )
    fg_enabled = bool(
        fg_on
        or re.search(r"\b(?:FG\s+ON|framegen\s*=\s*1|fg_off\s*=\s*0|fg\s*=\s*1)\b", text, re.I)
        or (not fg_disabled and "injection mode 8" in text)
    )

    # Parse ngame and two_buffer state
    ngame_matches = [int(x) for x in re.findall(r"\bngame\s*(?:==|[=:])\s*(\d+)", text, re.I)]
    if ngame_matches:
        ngame = max(ngame_matches)
    else:
        reg0_matches = [
            int(x) for x in re.findall(r"RegisterBuffers(?: enter)?:?\s*handle=\d+\s+start=0\s+num=(\d+)", text)
        ]
        if reg0_matches:
            ngame = reg0_matches[-1]
        elif "double-buffered game" in text:
            ngame = 2
        else:
            ngame = 0

    two_buf_triggered = bool(
        re.search(
            r"\b(?:two_buffer\s*(?:==|[=:])\s*(?:1|true|on|yes)|two_buf\s*=\s*1|g_runtime_2buf\s*=\s*1|vq_on\s*=\s*1|"
            r"two_buffer\s+(?:on|enabled|active|triggered)|triggered\s+two_buffer|double-buffered game|present copy available)\b",
            text,
            re.I,
        )
        or ("two_buffer" in text and not re.search(r"two_buffer\s*[=:]\s*0\b", text, re.I))
    )
    needs_auto_2buf = (ngame == 2) or two_buf_triggered

    symptoms = detect_symptoms(
        text,
        flips=flips,
        mids_shown=mids_shown,
        avg_real_fps=avg_real_fps,
        fg_enabled=fg_enabled,
    )

    is_old = has_explicit_ver and (parse_version_tuple(ver) < parse_version_tuple(cur_ver))

    has_plugin_inventory = "==== plugin files ====" in text or "==== plugins.ini" in text
    if (
        "ENGINE_CLASSIC_ALT" in text
        or re.search(r"^engine:\s*alt\b", text, re.M)
        or (not has_plugin_inventory and "vcfg_alt_16.prx" in text)
    ):
        prx, eng = "vcfg_alt_16.prx", "ENGINE_CLASSIC_ALT"
    elif (
        "ENGINE_NEW" in text
        or re.search(r"^engine:\s*new\b", text, re.M)
        or (not has_plugin_inventory and "vcfg16.prx" in text)
    ):
        prx, eng = "vcfg16.prx", "ENGINE_NEW"
    else:
        prx, eng = "vcfg_classic51.prx", "ENGINE_CLASSIC"

    k_sup_line = f'{{ "{tid}", "{prx}", {eng}, 0 }},' if CUSA_TOKEN_RE.fullmatch(tid) else ""

    is_failure = has_crash or has_fatal_enomem or (is_verified and has_inert_no_mbuf)
    if is_verified and has_inert_no_mbuf and sig == "NONE":
        sig = "INERT_NO_MBUF"

    if is_old:
        bucket = "BUCKET_1_OLD_VERSION"
    elif is_failure:
        bucket = "BUCKET_4_VERIFIED_REGRESSION" if is_verified else "BUCKET_3_NEW_TITLE_CRASH"
    elif (
        not is_verified
        and CUSA_TOKEN_RE.fullmatch(tid) is not None
        and flips >= 300
        and ((mids_shown is not None and mids_shown >= 100) or fg_on)
        and not has_inert
        and not symptoms
    ):
        bucket = "BUCKET_5_NEW_SUPPORTED_CANDIDATE"
    else:
        bucket = "BUCKET_2_L2_NO_CRASH"

    return {
        "file": name,
        "title_id": tid,
        "family": canon,
        "verified": is_verified,
        "version": ver,
        "crash": sig,
        "flips": flips,
        "mids_shown": mids_shown,
        "real_fps": avg_real_fps,
        "fg_on": fg_on,
        "ngame": ngame,
        "needs_auto_2buf": needs_auto_2buf,
        "l2_shave": l2_shave,
        "symptoms": symptoms,
        "k_sup_line": k_sup_line,
        "bucket": bucket,
    }


def _split_multi_title_bundle(name: str, text: str) -> list[tuple[str, str]]:
    """If a VCFG bug report contains telemetry sections for multiple CUSAxxxxx titles, split per active CUSAxxxxx."""
    text = scrub_game_names(text)
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return [(name, text)]

    header_end = matches[0].start()
    header_txt = text[:header_end]

    # Collect active sections (trace-, boot-, crash-) keyed by CUSAxxxxx
    active_by_tid: dict[str, list[str]] = {}
    prev_by_tid: dict[str, list[str]] = {}
    summary_by_tid: dict[str, list[str]] = {}

    for idx, m in enumerate(matches):
        sec_header = m.group(0)
        sec_title = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body or body == "(missing)":
            continue

        if sec_title.startswith("summary"):
            cur_tid: str | None = None
            for ln in body.splitlines():
                m_ln = re.match(r"^(CUSA\d{5})\b", ln.strip())
                if m_ln:
                    if "NOT COLLECTED" in ln:
                        cur_tid = None
                        continue
                    cur_tid = m_ln.group(1)
                    summary_by_tid.setdefault(cur_tid, []).append(ln)
                elif cur_tid and ln.startswith("    "):
                    summary_by_tid.setdefault(cur_tid, []).append(ln)
            continue

        m_sec_file = re.search(r"/data/bb_probe/(trace|boot|crash)-(CUSA\d{5})(\.prev)?\.txt", sec_title)
        if m_sec_file:
            tid = m_sec_file.group(2)
            is_prev = bool(m_sec_file.group(3))
            full_sec = f"{sec_header}\n{body}\n"
            if is_prev:
                prev_by_tid.setdefault(tid, []).append(full_sec)
            else:
                active_by_tid.setdefault(tid, []).append(full_sec)

    all_tids = sorted(set(active_by_tid.keys()) | set(prev_by_tid.keys()))
    if not all_tids:
        return [(name, text)]

    m_hdr_tid = RE_TITLE.search(header_txt)
    hdr_tid = m_hdr_tid.group(1) if m_hdr_tid else None
    has_prev_trace = any(
        any(f"trace-{t}.prev.txt" in s for s in prev_by_tid.get(t, [])) for t in all_tids
    )
    has_stale_trace_in_crash = False
    for t in all_tids:
        a_secs = active_by_tid.get(t, [])
        c_secs = [s for s in a_secs if f"crash-{t}.txt" in s]
        t_secs = [s for s in a_secs if f"trace-{t}.txt" in s]
        if c_secs and t_secs:
            mc = RE_CRASH.search(c_secs[0])
            c_flips = int(mc.group(5)) if mc else 0
            t_flips = _extract_present_flips(t_secs[0])
            if t_flips > c_flips + 200:
                has_stale_trace_in_crash = True
                break

    if (
        len(all_tids) == 1
        and hdr_tid == all_tids[0]
        and not has_prev_trace
        and not has_stale_trace_in_crash
    ):
        return [(name, text)]

    out: list[tuple[str, str]] = []
    for tid in all_tids:
        a_secs = active_by_tid.get(tid, [])
        p_secs = prev_by_tid.get(tid, [])
        sum_lines = summary_by_tid.get(tid, [])
        last_sum = [ln for ln in sum_lines if "game start before:" not in ln]
        prev_sum = [ln for ln in sum_lines if "last game start:" not in ln]

        clean_hdr = re.sub(r"(?m)^#\s*Summary:.*$", "", header_txt)
        clean_hdr = re.sub(r"(?m)^#\s*Title:\s*CUSA\d{5}\s*\|\s*", f"# Title: {tid} | ", clean_hdr)
        if f"Title: {tid}" not in clean_hdr:
            clean_hdr = f"Title: {tid}\n" + clean_hdr

        prev_hdr = re.sub(r"(?m)^#\s*Title:.*$", f"# Title: {tid}", clean_hdr)
        prev_hdr = re.sub(r"(?m)^(?:app version|Version):\s*v?\d+\.\d+\s*$", "", prev_hdr)

        a_full = "\n".join(a_secs)
        tid_2buf_hint = ""
        if re.search(r"\b(?:ngame\s*[=:]\s*2|RegisterBuffers(?: enter)?:?\s*handle=\d+\s+start=0\s+num=2|double-buffered game)\b", a_full):
            tid_2buf_hint = "\n# tid_meta: ngame=2 two_buffer=1\n"

        if a_secs:
            c_secs = [s for s in a_secs if f"crash-{tid}.txt" in s]
            t_secs = [s for s in a_secs if f"trace-{tid}.txt" in s]
            other_a_secs = [s for s in a_secs if f"trace-{tid}.txt" not in s]
            mc = RE_CRASH.search(c_secs[0]) if c_secs else None
            c_flips = int(mc.group(5)) if mc else 0
            t_flips = _extract_present_flips(t_secs[0]) if t_secs else 0

            if c_secs and t_secs and t_flips > c_flips + 200:
                # Crash dump is from a short boot crash while trace-<tid>.txt on disk is an unrotated prior long session
                sum_block = ("\n".join(last_sum) + "\n") if last_sum else ""
                combined_crash = clean_hdr + "\n" + sum_block + "\n".join(other_a_secs)
                sub_name = f"{name}:{tid}" if len(all_tids) > 1 else name
                out.append((sub_name, combined_crash))

                combined_trace = f"Title: {tid}\n" + "\n".join(t_secs) + tid_2buf_hint + "\n" + clean_hdr
                out.append((f"{name}:trace-{tid}", combined_trace))
            else:
                sum_block = ("\n".join(last_sum) + "\n") if last_sum else ""
                combined = clean_hdr + "\n" + sum_block + "\n".join(a_secs)
                sub_name = f"{name}:{tid}" if len(all_tids) > 1 else name
                out.append((sub_name, combined))

        if p_secs and any(f"trace-{tid}.prev.txt" in s for s in p_secs):
            p_sum_block = ("\n".join(prev_sum) + "\n") if prev_sum else ""
            combined_prev = f"Title: {tid}\n" + p_sum_block + "\n".join(p_secs) + tid_2buf_hint + "\n" + clean_hdr
            out.append((f"{name}:{tid}.prev", combined_prev))
        elif not a_secs and p_secs:
            sum_block = ("\n".join(sum_lines) + "\n") if sum_lines else ""
            combined = clean_hdr + "\n" + sum_block + "\n".join(p_secs)
            sub_name = f"{name}:{tid}" if len(all_tids) > 1 else name
            out.append((sub_name, combined))

    return out


def _has_telemetry_content(text: str) -> bool:
    return bool(
        "VCFG" in text
        or "plugin_load enter" in text
        or CUSA_TOKEN_RE.search(text)
        or "Present #" in text
        or "RegisterBuffers" in text
        or "AllocateDirectMemory" in text
        or "=== CRASH" in text
        or "[CRASH]" in text
    )


def iter_log_texts(paths: list[pathlib.Path]):
    """Yield (source_name, text) from files, directories, or .zip archives."""
    valid_exts = (".txt", ".log", ".md")
    for p in paths:
        if not p.exists():
            continue
        if p.is_file() and p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p, "r") as zf:
                for zi in sorted(zf.infolist(), key=lambda x: x.filename):
                    if zi.is_dir():
                        continue
                    if pathlib.PurePath(zi.filename).suffix.lower() in valid_exts:
                        raw = scrub_game_names(zf.read(zi.filename).decode("utf-8", errors="replace"))
                        for sub_name, sub_txt in _split_multi_title_bundle(f"{p.name}:{zi.filename}", raw):
                            yield sub_name, sub_txt
        elif p.is_file():
            raw = scrub_game_names(p.read_text(encoding="utf-8", errors="replace"))
            for sub_name, sub_txt in _split_multi_title_bundle(p.name, raw):
                yield sub_name, sub_txt
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                rel = str(f.relative_to(p)).replace("\\", "/")
                if f.suffix.lower() == ".zip":
                    with zipfile.ZipFile(f, "r") as zf:
                        for zi in sorted(zf.infolist(), key=lambda x: x.filename):
                            if zi.is_dir():
                                continue
                            if pathlib.PurePath(zi.filename).suffix.lower() in valid_exts:
                                raw = scrub_game_names(zf.read(zi.filename).decode("utf-8", errors="replace"))
                                for sub_name, sub_txt in _split_multi_title_bundle(f"{rel}:{zi.filename}", raw):
                                    yield sub_name, sub_txt
                elif f.suffix.lower() in valid_exts:
                    raw = scrub_game_names(f.read_text(encoding="utf-8", errors="replace"))
                    for sub_name, sub_txt in _split_multi_title_bundle(rel, raw):
                        yield sub_name, sub_txt


def triage_paths(
    paths: list[pathlib.Path],
    cur_ver: str | None = None,
    export_traces_path: pathlib.Path | None = None,
    verified: set[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run full triage, symptom detection, k_sup candidate mining, and optional trace export across paths."""
    if cur_ver is None:
        cur_ver = load_current_version()
    if verified is None or aliases is None:
        verified, aliases = load_verified_families()

    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in BUCKET_KEYS}
    extracted: dict[str, dict[str, Any]] = {}
    existing_keys: set[str] = set()

    if export_traces_path is not None and export_traces_path.exists():
        try:
            for item in json.loads(export_traces_path.read_text(encoding="utf-8")):
                k = item.get("trace_key", f"{item['title_id']}_{item['dm_size']}")
                extracted[k] = item
                existing_keys.add(k)
        except Exception:
            pass

    newly_extracted: dict[str, dict[str, Any]] = {}

    for name, text in iter_log_texts(paths):
        if not _has_telemetry_content(text):
            continue
        info = analyze_report_text(name, text, cur_ver, verified, aliases)
        buckets[info["bucket"]].append(info)

        for tr in extract_all_boot_traces(text):
            k = tr["trace_key"]
            prev = extracted.get(k)
            if not prev or len(tr["events"]) > len(prev["events"]):
                extracted[k] = tr
                newly_extracted[k] = tr

    if export_traces_path is not None:
        sorted_traces = [extracted[k] for k in sorted(extracted.keys())]
        export_traces_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(sorted_traces, indent=2) + "\n"
        verify_no_game_names(payload)
        export_traces_path.write_text(payload, encoding="utf-8")

    # Deduplicate BUCKET_5 k_sup candidates by CUSAxxxxx
    k_sup_candidates: list[dict[str, Any]] = []
    seen_cand: set[str] = set()
    for it in buckets["BUCKET_5_NEW_SUPPORTED_CANDIDATE"]:
        tid = it["title_id"]
        if tid not in seen_cand:
            seen_cand.add(tid)
            k_sup_candidates.append(
                {
                    "title_id": tid,
                    "k_sup_line": it["k_sup_line"],
                    "needs_auto_2buf": it["needs_auto_2buf"],
                    "auto_2buf_expr": f'vcfg_in_family(id, "{tid}")' if it["needs_auto_2buf"] else "",
                    "flips": it["flips"],
                    "mids_shown": it["mids_shown"],
                    "file": it["file"],
                }
            )

    symptoms_summary: dict[str, int] = {s: 0 for s in SYMPTOM_KEYS}
    for items in buckets.values():
        for it in items:
            for s in it["symptoms"]:
                symptoms_summary[s] = symptoms_summary.get(s, 0) + 1

    return {
        "version": cur_ver,
        "buckets": buckets,
        "k_sup_candidates": k_sup_candidates,
        "symptoms_summary": symptoms_summary,
        "exported_traces": [extracted[k] for k in sorted(extracted.keys())],
        "new_traces": [newly_extracted[k] for k in sorted(newly_extracted.keys())],
    }


def generate_markdown_report(triage_data: dict[str, Any]) -> str:
    """Generate a clean Markdown triage dashboard (--report-md) using strictly CUSAxxxxx IDs."""
    cur_ver = triage_data["version"]
    buckets: dict[str, list[dict[str, Any]]] = triage_data["buckets"]
    k_sup_candidates: list[dict[str, Any]] = triage_data["k_sup_candidates"]
    symptoms_summary: dict[str, int] = triage_data["symptoms_summary"]
    new_traces: list[dict[str, Any]] = triage_data.get("new_traces", [])
    exported_traces: list[dict[str, Any]] = triage_data.get("exported_traces", [])

    total_logs = sum(len(v) for v in buckets.values())
    lines: list[str] = [
        f"# VCFG Telemetry Triage & Supported Title Miner Dashboard (v{cur_ver})",
        "",
        f"- **Target Release Version**: `v{cur_ver}`",
        f"- **Total Telemetry Logs Analyzed**: `{total_logs}`",
        f"- **New `k_sup` Candidates (`BUCKET_5`)**: `{len(k_sup_candidates)}`",
        f"- **Hardware Boot Traces (`real_traces.json`)**: `{len(exported_traces)}` total (`{len(new_traces)}` new/updated)",
        "",
        "## 1. Bucket Summary",
        "",
        "| Bucket | Count | Policy / Action |",
        "|---|---:|---|",
        f"| `BUCKET_1_OLD_VERSION` | {len(buckets['BUCKET_1_OLD_VERSION'])} | Captured on `< v{cur_ver}` — ignore for code changes |",
        f"| `BUCKET_2_L2_NO_CRASH` | {len(buckets['BUCKET_2_L2_NO_CRASH'])} | Clean session or `L2 LOWMEM` / `shave` working as designed |",
        f"| `BUCKET_3_NEW_TITLE_CRASH` | {len(buckets['BUCKET_3_NEW_TITLE_CRASH'])} | Unverified `CUSAxxxxx` crash / fatal `ENOMEM` on `v{cur_ver}` |",
        f"| `BUCKET_4_VERIFIED_REGRESSION` | {len(buckets['BUCKET_4_VERIFIED_REGRESSION'])} | Verified `CUSAxxxxx` family regression on `v{cur_ver}` (highest priority) |",
        f"| `BUCKET_5_NEW_SUPPORTED_CANDIDATE` | {len(buckets['BUCKET_5_NEW_SUPPORTED_CANDIDATE'])} | New working `CUSAxxxxx` ready for `k_sup` in `launcher/src/main.c` |",
        "",
        "## 2. Hidden Symptom Sub-Classifier Summary",
        "",
        "| Symptom | Count | Description |",
        "|---|---:|---|",
        f"| `SYMPTOM_LOW_FPS_LOCK` | {symptoms_summary.get('SYMPTOM_LOW_FPS_LOCK', 0)} | `Present # >= 300` with avg `real flips/s < 20.0` or `mids_shown == 0` while FG ON |",
        f"| `SYMPTOM_AUDIO_DM_STARVATION` | {symptoms_summary.get('SYMPTOM_AUDIO_DM_STARVATION', 0)} | `DirectMemory ERR` (`0x8002000c`) on `0x4000..0x100000` (16 KB - 1 MB) after render start |",
        f"| `SYMPTOM_LOADING_FREEZE` | {symptoms_summary.get('SYMPTOM_LOADING_FREEZE', 0)} | Log ends on `RepeatCount >= 10` after `AllocateDirectMemory` / `MapDirectMemory` without further `Present #` |",
        "",
        "## 3. Triage Table Across All 5 Buckets",
        "",
        "| Bucket | Title ID | Family | Status | Version | Present # | Mids Shown | Real FPS | Crash | Symptoms | `auto_2buf` | Source |",
        "|---|---|---|---|---|---:|---:|---:|---|---|---|---|",
    ]

    for b_key in BUCKET_KEYS:
        for it in buckets[b_key]:
            status = "SUPPORTED" if it["verified"] else "NEW"
            mids_str = str(it["mids_shown"]) if it["mids_shown"] is not None else "-"
            fps_str = f"{it['real_fps']:.1f}" if it["real_fps"] is not None else "-"
            sym_str = ", ".join(f"`{s}`" for s in it["symptoms"]) if it["symptoms"] else "NONE"
            a2_str = "YES" if it["needs_auto_2buf"] else "NO"
            src_clean = str(it["file"]).replace("|", "\\|")
            lines.append(
                f"| `{b_key}` | `{it['title_id']}` | `{it['family']}` | {status} | `v{it['version']}` | "
                f"{it['flips']} | {mids_str} | {fps_str} | `{it['crash']}` | {sym_str} | {a2_str} | `{src_clean}` |"
            )

    lines.extend(
        [
            "",
            "## 4. Ready `k_sup` Entries for `launcher/src/main.c` (`BUCKET_5_NEW_SUPPORTED_CANDIDATE`)",
            "",
        ]
    )
    if k_sup_candidates:
        lines.append("```c")
        for cand in k_sup_candidates:
            comment = " // needs auto_2buf" if cand["needs_auto_2buf"] else ""
            lines.append(f"    {cand['k_sup_line']}{comment}")
        lines.append("```")
        lines.append("")
        lines.append("| Title ID | Ready `k_sup` Line | Needs `auto_2buf` | `auto_2buf` Snippet | Present # | Mids Shown |")
        lines.append("|---|---|---|---|---:|---:|")
        for cand in k_sup_candidates:
            mids_str = str(cand["mids_shown"]) if cand["mids_shown"] is not None else "-"
            a2_flag = "YES" if cand["needs_auto_2buf"] else "NO"
            a2_code = f"`{cand['auto_2buf_expr']}`" if cand["needs_auto_2buf"] else "-"
            lines.append(
                f"| `{cand['title_id']}` | `{cand['k_sup_line']}` | {a2_flag} | {a2_code} | {cand['flips']} | {mids_str} |"
            )
    else:
        lines.append("_No new `BUCKET_5_NEW_SUPPORTED_CANDIDATE` titles found in this batch._")

    lines.extend(
        [
            "",
            "## 5. Hardware Boot Traces for `tools/tests/real_traces.json`",
            "",
        ]
    )
    trace_list = new_traces if new_traces else exported_traces
    if trace_list:
        lines.append("| Trace Key | Title ID | Version | DirectMemory Pool (`dm_size`) | `pre_occupied_low` | Events |")
        lines.append("|---|---|---|---|---|---:|")
        for tr in trace_list:
            lines.append(
                f"| `{tr['trace_key']}` | `{tr['title_id']}` | `v{tr['log_version']}` | "
                f"`{tr['dm_size']}` | `{tr['pre_occupied_low']}` | {len(tr['events'])} |"
            )
    else:
        lines.append("_No deterministic hardware boot traces extracted in this batch._")

    out = "\n".join(lines) + "\n"
    verify_no_game_names(out)
    return out


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Triage VCFG telemetry logs into 5 buckets, detect hidden symptoms, mine k_sup candidates, and extract replay traces."
    )
    ap.add_argument("paths", nargs="*", type=pathlib.Path, help="Directories, files, or .zip archives")
    ap.add_argument(
        "--version",
        dest="version",
        type=str,
        default=None,
        help="Target release version to compare against (default: read from VERSION)",
    )
    ap.add_argument(
        "--export-traces",
        "--extract-traces",
        dest="export_traces",
        nargs="?",
        const=str(REAL_TRACES_JSON),
        default=None,
        help="Extract deterministic hardware boot traces to JSON file (default: tools/tests/real_traces.json)",
    )
    ap.add_argument(
        "--report-md",
        dest="report_md",
        type=pathlib.Path,
        default=None,
        help="Write structured Markdown triage dashboard to the given path",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit triage results as JSON to stdout",
    )
    args = ap.parse_args(argv)

    # Handle `python tools/triage_logs.py --export-traces <dir_or_log>` where positional path follows flag
    if not args.paths and args.export_traces and args.export_traces != str(REAL_TRACES_JSON):
        cand = pathlib.Path(args.export_traces)
        if cand.exists() and (cand.is_dir() or cand.suffix.lower() in (".txt", ".log", ".zip", ".md")):
            args.paths = [cand]
            args.export_traces = str(REAL_TRACES_JSON)

    if not args.paths:
        ap.error("the following arguments are required: paths")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_cli_args(argv)
    cur_ver = args.version.lstrip("vV") if args.version else load_current_version()
    export_path = pathlib.Path(args.export_traces) if args.export_traces else None

    triage_data = triage_paths(
        paths=args.paths,
        cur_ver=cur_ver,
        export_traces_path=export_path,
    )

    if args.report_md is not None:
        md_text = generate_markdown_report(triage_data)
        args.report_md.parent.mkdir(parents=True, exist_ok=True)
        args.report_md.write_text(md_text, encoding="utf-8")

    if args.json:
        json_out = json.dumps(triage_data, indent=2)
        verify_no_game_names(json_out)
        print(json_out)
        return 0

    buckets = triage_data["buckets"]
    print(f"=== VCFG TELEMETRY TRIAGE (Current Version: {cur_ver}) ===")
    for b_key in BUCKET_KEYS:
        items = buckets[b_key]
        print(f"\n[{b_key}] ({len(items)} logs)")
        for it in items[:25]:
            status = "VERIFIED" if it["verified"] else "UNVERIFIED"
            sym_sfx = f" | symptoms={','.join(it['symptoms'])}" if it["symptoms"] else ""
            cand_sfx = (
                f" | k_sup={it['k_sup_line']} | auto_2buf={int(it['needs_auto_2buf'])}"
                if b_key == "BUCKET_5_NEW_SUPPORTED_CANDIDATE"
                else ""
            )
            print(
                f"  - {it['title_id']} (fam={it['family']}, {status}) | v{it['version']} | "
                f"crash={it['crash']} | flips={it['flips']}{sym_sfx}{cand_sfx} | {it['file']}"
            )
        if len(items) > 25:
            print(f"  ... and {len(items) - 25} more")

    if triage_data["k_sup_candidates"]:
        print("\n=== NEW SUPPORTED CANDIDATES FOR launcher/src/main.c (k_sup) ===")
        for cand in triage_data["k_sup_candidates"]:
            a2 = " [auto_2buf=YES]" if cand["needs_auto_2buf"] else " [auto_2buf=NO]"
            print(f"  {cand['k_sup_line']}{a2}")

    if export_path is not None:
        print(
            f"\nSaved {len(triage_data['exported_traces'])} unique CUSAxxxxx hardware boot traces "
            f"({len(triage_data['new_traces'])} new/updated) to {export_path}"
        )

    if args.report_md is not None:
        print(f"Saved Markdown triage dashboard to {args.report_md}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
