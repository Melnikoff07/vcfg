#!/usr/bin/env python3
"""Offline Telemetry Corpus Analyzer & Runtime Auto-Healer Advisor (Chat 17).

Accepts any directory, single report file, or .zip archive containing user telemetry
(`vcfg_report.txt`, `trace-CUSAxxxxx.txt`, `boot-CUSAxxxxx.txt`, `crash-CUSAxxxxx.txt`,
`crash.log`, `probe.log`) and produces:
  1. Per-CUSAxxxxx health classification:
     - ACTIVE_60FPS
     - SAFE_FALLBACK
     - INERT_NO_MBUF
     - CRASH_OOM
     - CRASH_SIGSEGV
     - LOADING_ONLY
     - USER_DISABLED
  2. Automatic root-cause diagnosis and recommended `adapt-CUSAxxxxx.ini` recipe
     plus code-level fix recommendations for every non-ACTIVE_60FPS session.
  3. Strict sanitization & verification that zero game titles/names ever appear
     in the output (only CUSAxxxxx Title IDs).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, asdict
import json
import pathlib
import re
import sys
import zipfile
from typing import Dict, List, Optional, Tuple


HEALTH_CLASSES = (
    "ACTIVE_60FPS",
    "SAFE_FALLBACK",
    "INERT_NO_MBUF",
    "CRASH_OOM",
    "CRASH_SIGSEGV",
    "LOADING_ONLY",
    "USER_DISABLED",
)

CUSA_RE = re.compile(r"\b(CUSA\d{5})\b")
SECTION_RE = re.compile(r"^====\s+(.+?)\s+====\s*$", re.M)
SECTION_PATH_RE = re.compile(r"\(([^,\)]+),\s*\d+\s*bytes")
TITLE_ATTR_RE = re.compile(r"\btitle=(?:'[^']*'|\"[^\"]*\")")


@dataclass
class TitleTelemetry:
    title_id: str
    sources: List[str] = field(default_factory=list)
    trace_text: str = ""
    boot_text: str = ""
    crash_text: str = ""
    config_text: str = ""
    adapt_text: str = ""
    summary_text: str = ""
    games_line: str = ""


@dataclass
class TitleDiagnosis:
    title_id: str
    classification: str
    flips: int
    vo_flips: int
    ngame: int
    nbuf: int
    alloc_fail: int
    shaves: int
    fg_enabled: bool
    root_cause: str
    adapt_ini: str
    code_fix: str
    sources: List[str] = field(default_factory=list)


def scrub_game_names(raw: str) -> str:
    """Strip any `title='...'` metadata from launcher game lists on ingestion."""
    return TITLE_ATTR_RE.sub("title='REDACTED'", raw)


def verify_no_game_names(output_text: str, forbidden_names: Optional[List[str]] = None) -> bool:
    """Verify that zero game titles/names leak into the generated analyzer output."""
    if re.search(r"title='(?!REDACTED')[^']+'", output_text):
        raise ValueError("Forbidden raw title='...' attribute found in output")
    if forbidden_names:
        low = output_text.lower()
        for name in forbidden_names:
            clean = name.strip().lower()
            if clean and len(clean) >= 3 and clean in low:
                raise ValueError(f"Forbidden title name leaked into output: {clean!r}")
    return True


def _ensure_entry(store: Dict[str, TitleTelemetry], title_id: str, source: str) -> TitleTelemetry:
    if title_id not in store:
        store[title_id] = TitleTelemetry(title_id=title_id)
    ent = store[title_id]
    if source and source not in ent.sources:
        ent.sources.append(source)
    return ent


def _ingest_single_file(name: str, content: str, store: Dict[str, TitleTelemetry], source_label: str) -> None:
    content = scrub_game_names(content)
    base = pathlib.PurePath(name.replace("\\", "/")).name

    # Check if this is a multi-section vcfg_report.txt or auto-crash bundle
    if "==== " in content or content.startswith("VCFG bug report") or content.startswith("VCFG AUTO CRASH REPORT"):
        _ingest_report_bundle(content, store, source_label)
        return

    m_name = CUSA_RE.search(base)
    tid = m_name.group(1) if m_name else None
    if not tid:
        m_body = re.search(r"\btitle[=_ ]*(CUSA\d{5})\b", content) or CUSA_RE.search(content)
        if m_body:
            tid = m_body.group(1)
    if not tid:
        return

    ent = _ensure_entry(store, tid, source_label)
    low = base.lower()
    if low.startswith("crash") or "=== CRASH " in content:
        ent.crash_text += "\n" + content
    elif low.startswith("boot"):
        ent.boot_text += "\n" + content
    elif low.startswith("config"):
        ent.config_text += "\n" + content
    elif low.startswith("adapt"):
        ent.adapt_text += "\n" + content
    else:
        ent.trace_text += "\n" + content


def _ingest_report_bundle(content: str, store: Dict[str, TitleTelemetry], source_label: str) -> None:
    content = scrub_game_names(content)
    header_tid_m = re.search(r"^Title:\s*(CUSA\d{5})\b", content, re.M)
    default_tid = header_tid_m.group(1) if header_tid_m else None

    matches = list(SECTION_RE.finditer(content))
    if not matches:
        if default_tid:
            ent = _ensure_entry(store, default_tid, source_label)
            if "=== CRASH " in content:
                ent.crash_text += "\n" + content
            else:
                ent.trace_text += "\n" + content
        return

    for idx, m in enumerate(matches):
        sec_title = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        if not body or body == "(missing)":
            continue

        if sec_title.startswith("summary"):
            # Parse per-title summary paragraphs inside `==== summary (...) ====`
            cur_tid: Optional[str] = None
            for line in body.splitlines():
                m_line = re.match(r"^(CUSA\d{5})\b", line.strip())
                if m_line:
                    cur_tid = m_line.group(1)
                    if "NOT COLLECTED" in line:
                        cur_tid = None
                        continue
                    ent = _ensure_entry(store, cur_tid, source_label)
                    ent.summary_text += line + "\n"
                elif cur_tid and line.startswith("    "):
                    ent = _ensure_entry(store, cur_tid, source_label)
                    ent.summary_text += line + "\n"
            continue

        if sec_title == "games":
            for line in body.splitlines():
                m_g = re.match(r"^(CUSA\d{5})\b", line.strip())
                if m_g:
                    tid = m_g.group(1)
                    # Only record games_line if the title already has trace/boot/crash or if it's the only info
                    if tid in store:
                        store[tid].games_line = line.strip()
            continue

        path_m = SECTION_PATH_RE.search(sec_title)
        sec_path = path_m.group(1) if path_m else sec_title
        tid_m = CUSA_RE.search(sec_path) or CUSA_RE.search(body)
        tid = tid_m.group(1) if tid_m else default_tid
        if not tid:
            continue

        ent = _ensure_entry(store, tid, source_label)
        low_sec = sec_title.lower()
        if "crash" in low_sec or "=== CRASH " in body:
            ent.crash_text += "\n" + body
        elif "early start" in low_sec or "boot" in low_sec:
            ent.boot_text += "\n" + body
        elif "adapt" in low_sec:
            ent.adapt_text += "\n" + body
        elif "game settings" in low_sec or "game config" in low_sec or "config-" in low_sec:
            ent.config_text += "\n" + body
        elif "flight recorder" in low_sec or "plugin log" in low_sec or "trace-" in low_sec or "probe." in low_sec:
            ent.trace_text += "\n" + body


def collect_telemetry(target: pathlib.Path) -> Dict[str, TitleTelemetry]:
    """Collect and group telemetry by CUSAxxxxx from a file, directory, or .zip archive."""
    store: Dict[str, TitleTelemetry] = {}
    if not target.exists():
        raise FileNotFoundError(f"Telemetry path not found: {target}")

    def _read_zip(zpath: pathlib.Path) -> None:
        with zipfile.ZipFile(zpath, "r") as zf:
            for info in sorted(zf.infolist(), key=lambda x: x.filename):
                if info.is_dir():
                    continue
                low = info.filename.lower()
                if not low.endswith((".txt", ".log", ".ini")):
                    continue
                raw = zf.read(info.filename).decode("utf-8", errors="replace")
                _ingest_single_file(info.filename, raw, store, f"{zpath.name}:{info.filename}")

    if target.is_file():
        if target.suffix.lower() == ".zip":
            _read_zip(target)
        else:
            raw = target.read_text(encoding="utf-8", errors="replace")
            _ingest_single_file(target.name, raw, store, target.name)
    else:
        for p in sorted(target.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".zip":
                _read_zip(p)
            elif p.suffix.lower() in (".txt", ".log", ".ini"):
                raw = p.read_text(encoding="utf-8", errors="replace")
                rel = str(p.relative_to(target)).replace("\\", "/")
                _ingest_single_file(rel, raw, store, rel)

    return store


def _max_int(pattern: str, text: str) -> int:
    vals = [int(x) for x in re.findall(pattern, text)]
    return max(vals) if vals else 0


def classify_title_session(ent: TitleTelemetry) -> TitleDiagnosis:
    """Classify a CUSAxxxxx session into one of the 7 health states and generate a diagnosis + adapt.ini."""
    combined = "\n".join([ent.trace_text, ent.boot_text, ent.crash_text, ent.summary_text])
    cfg_all = "\n".join([ent.config_text, ent.games_line, combined])

    flips = max(
        _max_int(r"\bflips=(\d+)", combined),
        _max_int(r"\bgnm #(\d+)\b", combined),
        _max_int(r"\binject #(\d+)\b", combined),
        _max_int(r"\bmode4 #(\d+)\b", combined),
        _max_int(r"safe mode (?:activated at )?frame (\d+)", combined),
        _max_int(r"reached frame (\d+)", combined),
    )
    vo_flips = max(
        _max_int(r"\bvo_flips=(\d+)", combined),
        _max_int(r"\bvoSubmitFlips=(\d+)", combined),
        _max_int(r"\bvoFlips=(\d+)", combined),
        _max_int(r"\bvout (?:inject|fg) #(\d+)\b", combined),
        _max_int(r"VideoOutSubmitFlip #(\d+)", combined),
    )

    ngame = _max_int(r"\bngame=(\d+)", combined)
    if ngame == 0:
        m_reg0 = re.findall(r"RegisterBuffers(?: enter)?:?\s+handle=\d+\s+start=0\s+num=(\d+)", combined)
        if m_reg0:
            ngame = int(m_reg0[-1])
        elif "double-buffered game" in combined or ("idx=0 " in combined and "idx=1 " in combined and "idx=2 " not in combined):
            ngame = 2

    nbuf_vals = [int(x) for x in re.findall(r"\bnbuf=(\d+)", combined)]
    m_no_mbuf = re.search(r"INERT: NO MBUF \((\d+)\)", combined)
    if m_no_mbuf:
        nbuf_vals.append(int(m_no_mbuf.group(1)))
    nbuf = max(nbuf_vals) if nbuf_vals else (ngame if "extended to" in combined or "separate top group" in combined else 0)

    alloc_fail = max(
        _max_int(r"\balloc_fail=(\d+)", combined),
        _max_int(r"\ballocs=\d+\(fail=(\d+)\)", combined),
        len(re.findall(r"AllocateDirectMemory\([^)]*\)\s*=\s*0x80020023", combined)),
        len(re.findall(r"mbufs allocate FAILED", combined)),
    )
    shaves = max(
        _max_int(r"\bshave=(\d+)", combined),
        _max_int(r"\bshaves=(\d+)", combined),
        _max_int(r"arena shave #(\d+)", combined),
    )
    hibernates = _max_int(r"memory hibernate #(\d+)", combined)

    fg_disabled_cfg = bool(
        re.search(r"\bframegen\s*=\s*0\b", cfg_all)
        or re.search(r"\bfg_off=1\b", combined)
        or re.search(r"\bfg=0\b", cfg_all)
    )
    fg_enabled_cfg = bool(
        re.search(r"\bframegen\s*=\s*1\b", cfg_all)
        or re.search(r"\bfg_off=0\b", combined)
        or re.search(r"\bfg=1\b", cfg_all)
    )
    fg_enabled = fg_enabled_cfg or not fg_disabled_cfg

    has_crash = ("=== CRASH " in combined) or bool(re.search(r"\bCRASH\s+SIG\w+", combined))
    has_oom_err = (alloc_fail > 0) or ("0x80020023" in combined) or ("NO DISPLAY MEM" in combined) or ("NO GPU MEM" in combined) or ("NO WORK MEM" in combined)

    has_fg_active = bool(
        re.search(r"\bmode4 #\d+", combined)
        or re.search(r"\bvout fg #\d+", combined)
        or re.search(r"\binject #\d+:\s*count", combined)
        or re.search(r"\bvout inject #\d+:", combined)
        or "GPU time of our passes:" in combined
        or "FG frames yes" in combined
        or "60FPS NATIVE (FG OFF)" in combined
    )
    has_inert = bool(
        re.search(r"inert='INERT:[^']+'", combined)
        or "INERT: NO MBUF" in combined
        or "INERT: NO GNM FLIP" in combined
        or "INERT: NO FLIP TAIL" in combined
        or "inert: no flip hook" in combined
        or "inject skipped: tail marker not found" in combined
    )
    has_safe_refusal = bool(
        "INERT: SAFE " in combined
        or "safe mode: refused" in combined
        or "passive state=2" in combined
        or ("safe mode frame" in combined and "refused" in combined)
        or hibernates > 0
    )

    total_flips = max(flips, vo_flips)

    # 1. Classification decision tree
    if has_crash:
        if has_oom_err or (shaves > 0 and "free_2mb=0x0" in combined):
            classification = "CRASH_OOM"
        else:
            classification = "CRASH_SIGSEGV"
    elif has_oom_err and not has_fg_active and not has_safe_refusal:
        classification = "CRASH_OOM"
    elif has_safe_refusal:
        classification = "SAFE_FALLBACK"
    elif has_fg_active and not has_inert:
        classification = "ACTIVE_60FPS"
    elif fg_disabled_cfg and not fg_enabled_cfg and not has_inert:
        classification = "USER_DISABLED"
    elif has_inert or (total_flips >= 120 and not has_fg_active):
        classification = "INERT_NO_MBUF"
    else:
        classification = "LOADING_ONLY"

    # 2. Root-cause diagnosis & adapt-<TITLE>.ini synthesis
    adapt_lines: List[str] = [f"; auto-generated diagnosis for {ent.title_id}"]
    root_cause = "Healthy 60 Hz frame generation pipeline."
    code_fix = "None required (session is healthy)."

    has_gnm_flip = (flips > 0) or ("first SubmitAndFlip" in combined) or ("gnm #1 " in combined)
    has_vout_flip = (vo_flips > 0) or ("first VideoOutSubmitFlip" in combined) or ("voSubmitFlips=" in combined and vo_flips > 0)
    has_multi_reg = bool(re.search(r"RegisterBuffers(?: enter)?:?\s+handle=\d+\s+start=[1-9]", combined)) or ("slot-0 split" in combined)
    has_batch_map = ("BatchMap" in combined) and (_max_int(r"batch_maps=(\d+)", combined) > 0 or "game BatchMap" in combined)

    if classification == "ACTIVE_60FPS":
        if "60FPS NATIVE" in combined:
            root_cause = "Native 60 FPS title auto-detected during warmup; FG cleanly bypassed."
        else:
            root_cause = "Active 30->60 FPS frame generation running normally."
        adapt_ini = ""
    elif classification == "USER_DISABLED":
        root_cause = "Frame generation explicitly disabled by user/profile configuration (framegen=0)."
        adapt_lines.append("framegen = 1")
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = "Enable framegen=1 in config-<TITLE>.ini or via in-game L3+R3 menu."
    elif (has_gnm_flip and has_vout_flip) or ("INERT: NO FLIP TAIL" in combined and vo_flips > 0) or ("INERT: NO GNM FLIP" in combined and vo_flips > 0):
        # Dual Flip-Path Transition (CUSA00552 pattern) or decoupled VideoOutSubmitFlip
        root_cause = (
            "Dual Flip-Path Transition: engine uses sceGnmSubmitAndFlipCommandBuffers during loading/splash "
            f"(flips={flips}) and switches to decoupled sceGnmSubmitCommandBuffers + sceVideoOutSubmitFlip "
            f"in gameplay (vo_flips={vo_flips})."
        )
        adapt_lines.append("vout_path = 1")
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_lines.append("reg_mode = 1")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = (
            "In sceVideoOutSubmitFlip_hook (classic/source/main.c), check active SubmitAndFlip timestamp window "
            "(gnm_flip_active < 250ms) instead of static g_flips > 0, and persist vout_path=1 via save_adapt_ini()."
        )
    elif classification == "INERT_NO_MBUF" and (nbuf == 0 or "INERT: NO MBUF (0)" in combined):
        # Late / split RegisterBuffers (CUSA04607 pattern)
        root_cause = (
            f"Late or unhooked RegisterBuffers (nbuf={nbuf} at flips={total_flips}): game registered scanout "
            "buffers before hook attachment or across split groups while flipping a 2-buffer ring."
        )
        adapt_lines.append("two_buffer = 1")
        adapt_lines.append("reg_mode = 2")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = (
            "Enable deferred/late buffer registration (reg_mode=2) and 2-buffer virtual flip queue (two_buffer=1) "
            "or reconstruct scanout buffer table from flip index ring."
        )
    elif classification in ("CRASH_OOM", "SAFE_FALLBACK") and (total_flips > 120 or hibernates > 0):
        # Post-Menu World-Arena Allocation (CUSA09311 pattern)
        root_cause = (
            f"Post-Menu World-Arena Allocation at flips={total_flips} (shaves={shaves}, alloc_fail={alloc_fail}, "
            f"hibernates={hibernates}): engine allocates a massive DirectMemory world arena after main menu."
        )
        adapt_lines.append("shave_mode = 2")
        adapt_lines.append("reg_mode = 1")
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = (
            "Use late DirectMemory reservation (shave_mode=2) + top-group registration (reg_mode=1) and "
            "runtime memory hibernation (Step 6 in debug_hooks.h) so post-menu world-arena allocation succeeds."
        )
    elif classification == "CRASH_OOM":
        # Boot DirectMemory exhaustion / sub-2MB chunk probing (CUSA16734 / CUSA08519 pattern)
        root_cause = (
            f"Boot DirectMemory Exhaustion before frame 120 (flips={total_flips}, alloc_fail={alloc_fail}, "
            f"shaves={shaves}): engine probes or claims nearly 100% of DirectMemory during startup."
        )
        adapt_lines.append("shave_mode = 2")
        adapt_lines.append("reg_mode = 2")
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = (
            "Skip early DirectMemory allocation at plugin_load (shave_mode=2, reg_mode=2) and reserve 8 MB "
            "on demand when the remaining pool drops below 8 MB."
        )
    elif has_multi_reg or has_batch_map:
        # Multi-Group RegisterBuffers + BatchMap (CUSA11456 / CUSA13323 pattern)
        root_cause = (
            f"Multi-Group RegisterBuffers + BatchMap collision (flips={total_flips}, ngame={ngame}): "
            "engine registers secondary buffer groups (start > 0) and maps arenas via sceKernelBatchMap."
        )
        if ngame == 2 or "start=2" in combined:
            adapt_lines.append("two_buffer = 1")
        adapt_lines.append("reg_mode = 1")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = (
            "Place extra FG display buffers in separate top group 16-N (reg_mode=1) and split straddling "
            "sceKernelBatchMap entries into real DirectMemory prefix + FlexibleMemory tail."
        )
    elif classification == "CRASH_SIGSEGV":
        root_cause = (
            f"Runtime SIGSEGV/SIGBUS fault at flips={total_flips} (ngame={ngame}, shaves={shaves}): "
            "command-stream or flip-queue pacing collision."
        )
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_lines.append("reg_mode = 1")
        adapt_lines.append("pace_order = 2")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = (
            "Isolate FG buffers in top group (reg_mode=1) and switch to post-submit pacing (pace_order=2)."
        )
    elif classification == "SAFE_FALLBACK":
        root_cause = (
            f"Safe-mode fallback / refusal at flips={total_flips} (ngame={ngame}, nbuf={nbuf}): "
            "late activation lacked contiguous DirectMemory or encountered an unverified buffer layout."
        )
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_lines.append("shave_mode = 2")
        adapt_lines.append("reg_mode = 1")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = "Persist shave_mode=2 and reg_mode=1 to adapt-<TITLE>.ini for clean next-boot activation."
    elif classification == "LOADING_ONLY":
        root_cause = (
            f"Session ended during initial loading/splash screen (flips={total_flips} < 120) "
            "before FG activation threshold."
        )
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_lines.append("shave_mode = 2")
        adapt_lines.append("reg_mode = 2")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = (
            "If the title exited due to an early boot watchdog, defer DirectMemory allocation and "
            "RegisterBuffers to frame 120 (shave_mode=2, reg_mode=2)."
        )
    else:
        root_cause = (
            f"FG pipeline inert at flips={total_flips} (ngame={ngame}, nbuf={nbuf}, alloc_fail={alloc_fail})."
        )
        if ngame == 2:
            adapt_lines.append("two_buffer = 1")
        adapt_lines.append("reg_mode = 1")
        adapt_ini = "\n".join(adapt_lines) + "\n"
        code_fix = "Enable top-group buffer registration (reg_mode=1) and verify scanout format/resolution."

    return TitleDiagnosis(
        title_id=ent.title_id,
        classification=classification,
        flips=flips,
        vo_flips=vo_flips,
        ngame=ngame,
        nbuf=nbuf,
        alloc_fail=alloc_fail,
        shaves=shaves,
        fg_enabled=fg_enabled,
        root_cause=root_cause,
        adapt_ini=adapt_ini,
        code_fix=code_fix,
        sources=ent.sources,
    )


def analyze_path(target: pathlib.Path) -> List[TitleDiagnosis]:
    """Analyze a file, directory, or .zip archive and return sorted per-CUSAxxxxx diagnoses."""
    store = collect_telemetry(target)
    results = [classify_title_session(ent) for _, ent in sorted(store.items())]
    return results


def format_markdown_report(results: List[TitleDiagnosis]) -> str:
    """Format the corpus analysis results as a clean Markdown report (zero game names)."""
    counts: Dict[str, int] = {k: 0 for k in HEALTH_CLASSES}
    for r in results:
        counts[r.classification] = counts.get(r.classification, 0) + 1

    lines: List[str] = [
        "# VCFG Telemetry Corpus Analysis Report",
        "",
        f"- **Total Titles Analyzed**: {len(results)}",
    ]
    for k in HEALTH_CLASSES:
        if counts.get(k, 0) > 0:
            lines.append(f"- **`{k}`**: {counts[k]}")
    lines.append("")
    lines.append("## Per-Title Health Summary")
    lines.append("")
    lines.append("| Title ID | Health Class | Gnm Flips | Vo Flips | Buffers (`ngame`/`nbuf`) | Alloc Fail | Shaves | Root Cause |")
    lines.append("|---|---|---:|---:|---|---:|---:|---|")
    for r in results:
        lines.append(
            f"| `{r.title_id}` | `{r.classification}` | {r.flips} | {r.vo_flips} | "
            f"{r.ngame}/{r.nbuf} | {r.alloc_fail} | {r.shaves} | {r.root_cause} |"
        )

    non_ok = [r for r in results if r.classification != "ACTIVE_60FPS"]
    if non_ok:
        lines.append("")
        lines.append("## Recommended `adapt-CUSAxxxxx.ini` & Code Fixes for Non-OK Sessions")
        for r in non_ok:
            lines.append("")
            lines.append(f"### `{r.title_id}` (`{r.classification}`)")
            lines.append(f"- **Diagnosis**: {r.root_cause}")
            lines.append(f"- **Code Fix**: {r.code_fix}")
            if r.adapt_ini.strip():
                lines.append(f"- **Recommended `/data/bb_probe/adapt-{r.title_id}.ini`**:")
                lines.append("```ini")
                lines.append(r.adapt_ini.strip())
                lines.append("```")

    out = "\n".join(lines) + "\n"
    verify_no_game_names(out)
    return out


def format_json_report(results: List[TitleDiagnosis]) -> str:
    payload = {
        "total_titles": len(results),
        "counts": {k: sum(1 for r in results if r.classification == k) for k in HEALTH_CLASSES},
        "titles": [asdict(r) for r in results],
    }
    out = json.dumps(payload, indent=2)
    verify_no_game_names(out)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Analyze VCFG telemetry corpus (directories, files, or .zip archives).")
    ap.add_argument("paths", nargs="+", help="Telemetry files, directories, or .zip archives to analyze")
    ap.add_argument("--json", action="store_true", help="Emit JSON output instead of Markdown")
    ap.add_argument("--out", type=str, default="", help="Write report to file instead of stdout")
    ap.add_argument("--write-adapt-dir", type=str, default="", help="Optional directory to write adapt-CUSAxxxxx.ini files")
    args = ap.parse_args(argv)

    merged_store: Dict[str, TitleTelemetry] = {}
    for raw_p in args.paths:
        p = pathlib.Path(raw_p)
        sub = collect_telemetry(p)
        for tid, ent in sub.items():
            dst = _ensure_entry(merged_store, tid, "")
            for s in ent.sources:
                if s not in dst.sources:
                    dst.sources.append(s)
            dst.trace_text += ent.trace_text
            dst.boot_text += ent.boot_text
            dst.crash_text += ent.crash_text
            dst.config_text += ent.config_text
            dst.adapt_text += ent.adapt_text
            dst.summary_text += ent.summary_text
            if ent.games_line:
                dst.games_line = ent.games_line

    results = [classify_title_session(ent) for _, ent in sorted(merged_store.items())]
    if args.write_adapt_dir:
        adir = pathlib.Path(args.write_adapt_dir)
        adir.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r.classification != "ACTIVE_60FPS" and r.adapt_ini.strip():
                verify_no_game_names(r.adapt_ini)
                (adir / f"adapt-{r.title_id}.ini").write_text(r.adapt_ini, encoding="utf-8")

    output = format_json_report(results) if args.json else format_markdown_report(results)
    if args.out:
        pathlib.Path(args.out).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
