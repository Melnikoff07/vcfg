#!/usr/bin/env python3
"""
VCFG Telemetry Triage & Hardware Trace Extractor.

Usage:
  python tools/triage_logs.py <dir_or_zip> [<dir_or_zip> ...] [--extract-traces]

1. Classifies every telemetry report into 4 strict buckets so old-version noise
   and L2 non-crash dumps are never confused with active regressions:
   - BUCKET 1 [OLD_VER_OR_FIXED]: Captured on plugin version < VERSION.
   - BUCKET 2 [L2_NO_CRASH / SAFE_CANDIDATE]: Manual L2 dump or clean run (no signal crash).
     Highlights unverified CUSAxxxxx titles running cleanly at 60 Hz for whitelist promotion.
   - BUCKET 3 [NEW_TITLE_CRASH]: Crash in an unverified CUSAxxxxx title on current VERSION.
   - BUCKET 4 [VERIFIED_REGRESSION]: Crash or INERT: NO MBUF in a verified CUSAxxxxx family on current VERSION.

2. With --extract-traces, extracts deterministic DirectMemory + RegisterBuffers traces from
   boot-CUSAxxxxx.txt sections into tools/tests/real_traces.json for tools/tests/test_real_traces.py.
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
REAL_TRACES_JSON = ROOT / "tools" / "tests" / "real_traces.json"


def load_current_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def load_verified_families() -> tuple[set[str], dict[str, str]]:
    text = FAMILIES_H.read_text(encoding="utf-8")
    fam_block = re.search(r"k_ver\[\]\s*=\s*\{(.*?)\};", text, re.S)
    verified: set[str] = set()
    if fam_block:
        verified = set(re.findall(r'"(CUSA\d{5})"', fam_block.group(1)))
    aliases: dict[str, str] = {}
    alias_block = re.search(r"k_vcfg_title_aliases\[\]\s*=\s*\{(.*?)\};", text, re.S)
    if alias_block:
        for m in re.finditer(r'\{\s*"(CUSA\d{5})"\s*,\s*"(CUSA\d{5})"\s*\}', alias_block.group(1)):
            aliases[m.group(1)] = m.group(2)
    return verified, aliases


def resolve_family(tid: str, verified: set[str], aliases: dict[str, str]) -> tuple[str, bool]:
    canon = aliases.get(tid, tid)
    return canon, (canon in verified or tid in verified)


def parse_version_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v or "0.0")
    return tuple(int(x) for x in parts) if parts else (0, 0)


RE_TITLE = re.compile(r"(?:Title:\s*|title[=:\s]+)(CUSA\d{5})")
RE_VER = re.compile(r"(?:Version:\s*|version[=:\s]+)(\d+\.\d+)")
RE_CRASH = re.compile(r"===\s*CRASH\s+([A-Z0-9]+)\s*\(sig=(\d+)\s+code=(-?\d+)\)\s*at\s*([\d.]+)s\s*\|\s*flips=(\d+)")
RE_PLUGIN_LOAD = re.compile(
    r"plugin_load enter:\s*title=(CUSA\d{5}).*?version=(\d+\.\d+).*?dm=0x([0-9a-fA-F]+)"
)
RE_EARLY_TRY1 = re.compile(
    r"early display memory try 1 \(top\):\s*len=0x([0-9a-fA-F]+)\s+align=0x([0-9a-fA-F]+)\s+type=(\d+)\s+"
    r"range=0x([0-9a-fA-F]+)\.\.0x([0-9a-fA-F]+)\s+r=0x([0-9a-fA-F]+)\s+phys=0x([0-9a-fA-F]+)"
)
RE_GAME_ALLOC = re.compile(
    r"\[\s*t:\d+\s+f:(\d+)\]\s+game AllocateDirectMemory\(len=0x([0-9a-fA-F]+)\s+align=0x([0-9a-fA-F]+)\s+"
    r"type=(\d+)\s+range=0x([0-9a-fA-F]+)\.\.0x([0-9a-fA-F]+)\)\s*=\s*0x([0-9a-fA-F]+)\s+phys=0x([0-9a-fA-F]+)"
)
RE_GAME_REL = re.compile(
    r"\[\s*t:\d+\s+f:(\d+)\]\s+game ReleaseDirectMemory\(phys=0x([0-9a-fA-F]+)\s+len=0x([0-9a-fA-F]+)\)\s*=\s*0x([0-9a-fA-F]+)"
)
RE_REG_ENTER = re.compile(
    r"\[\s*t:\d+\s+f:(\d+)\]\s+RegisterBuffers enter:\s*handle=(\d+)\s+start=(\d+)\s+num=(\d+)"
)
RE_REG_ATTR = re.compile(
    r"attr:\s*fmt=0x([0-9a-fA-F]+)\s+tiling=(\d+)\s+aspect=(\d+)\s+(\d+)x(\d+)\s+pitch=(\d+)"
)


def extract_boot_trace(text: str) -> dict[str, Any] | None:
    m_boot = re.search(r"==== boot stages \(/data/bb_probe/boot-(CUSA\d{5})\.txt[^=]*====\n(.*?)(?=\n  ==== |\Z)", text, re.S)
    boot_txt = m_boot.group(2) if m_boot else text
    if "plugin_load enter:" not in boot_txt:
        return None
    boot_txt = "plugin_load enter:" + boot_txt.split("plugin_load enter:")[-1]
    m_load = RE_PLUGIN_LOAD.search(boot_txt)
    if not m_load:
        return None
    tid = m_load.group(1)
    ver = m_load.group(2)
    dm_size = int(m_load.group(3), 16)
    if "middle lines omitted" in boot_txt:
        return None

    events: list[dict[str, Any]] = []
    lines = boot_txt.splitlines()
    i = 0
    first_2m_phys: int | None = None
    sub_phys_candidates: list[int] = []
    live_blocks: list[tuple[int, int, int]] = []  # (orig_phys, len, alloc_id)
    next_alloc_id = 1
    RE_OWN_ALLOC = re.compile(
        r"alloc_dm ok:\s*len=0x([0-9a-fA-F]+)\s+align=0x([0-9a-fA-F]+)\s+type=(\d+)\s+"
        r"range=0x0\.\.0x([0-9a-fA-F]+)\s+phys=0x([0-9a-fA-F]+)"
    )
    while i < len(lines):
        ln = lines[i]
        mo = RE_OWN_ALLOC.search(ln)
        if mo:
            p_own = int(mo.group(5), 16)
            if p_own < 0x40000000:
                sub_phys_candidates.append(p_own)
        ma = RE_GAME_ALLOC.search(ln)
        if ma:
            flips = int(ma.group(1))
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
                # If this physical range overlaps an earlier live block, the game released the earlier block:
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
            r_flips = int(mr.group(1))
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
            flips = int(mreg.group(1))
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
    # Trim any trailing unlogged-release streaming churn where cumulative live bytes would exceed pool capacity:
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


def analyze_report_text(name: str, text: str, cur_ver: str, verified: set[str], aliases: dict[str, str]) -> dict[str, Any]:
    m_tid = RE_TITLE.search(text)
    tid = m_tid.group(1) if m_tid else "UNKNOWN"
    m_ver = RE_VER.search(text)
    ver = m_ver.group(1) if m_ver else "0.0"
    canon, is_verified = resolve_family(tid, verified, aliases)
    m_crash = RE_CRASH.search(text)
    has_crash = m_crash is not None
    sig = m_crash.group(1) if m_crash else "NONE"
    flips = int(m_crash.group(5)) if m_crash else 0
    if not m_crash:
        flips_all = [int(x) for x in re.findall(r"gnm #(\d+)|flips=(\d+)", text) for x in x if x]
        if flips_all:
            flips = max(flips_all)

    is_old = parse_version_tuple(ver) < parse_version_tuple(cur_ver)
    if not has_crash:
        bucket = "BUCKET_2_L2_NO_CRASH"
    elif is_old:
        bucket = "BUCKET_1_OLD_VERSION"
    elif is_verified:
        bucket = "BUCKET_4_VERIFIED_REGRESSION"
    else:
        bucket = "BUCKET_3_NEW_TITLE_CRASH"

    return {
        "file": name,
        "title_id": tid,
        "family": canon,
        "verified": is_verified,
        "version": ver,
        "crash": sig,
        "flips": flips,
        "bucket": bucket,
    }


def iter_log_texts(paths: list[pathlib.Path]):
    for p in paths:
        if p.is_file() and p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p, "r") as zf:
                for zi in zf.infolist():
                    if zi.filename.endswith(".txt") or zi.filename.endswith(".log"):
                        raw = zf.read(zi.filename).decode("utf-8", errors="replace")
                        yield f"{p.name}:{zi.filename}", raw
        elif p.is_file():
            yield p.name, p.read_text(encoding="utf-8", errors="replace")
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in (".txt", ".log"):
                    yield str(f.relative_to(p)), f.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser(description="Triage VCFG telemetry logs into 4 buckets and extract replay traces.")
    ap.add_argument("paths", nargs="+", type=pathlib.Path, help="Directories, files, or .zip archives")
    ap.add_argument("--extract-traces", action="store_true", help="Update tools/tests/real_traces.json")
    args = ap.parse_args()

    cur_ver = load_current_version()
    verified, aliases = load_verified_families()
    buckets: dict[str, list[dict[str, Any]]] = {
        "BUCKET_1_OLD_VERSION": [],
        "BUCKET_2_L2_NO_CRASH": [],
        "BUCKET_3_NEW_TITLE_CRASH": [],
        "BUCKET_4_VERIFIED_REGRESSION": [],
    }
    extracted: dict[str, dict[str, Any]] = {}
    if REAL_TRACES_JSON.exists():
        try:
            for item in json.loads(REAL_TRACES_JSON.read_text(encoding="utf-8")):
                k = item.get("trace_key", f"{item['title_id']}_{item['dm_size']}")
                extracted[k] = item
        except Exception:
            pass

    for name, text in iter_log_texts(args.paths):
        if "VCFG" not in text and "plugin_load enter" not in text:
            continue
        info = analyze_report_text(name, text, cur_ver, verified, aliases)
        buckets[info["bucket"]].append(info)
        if args.extract_traces:
            tr = extract_boot_trace(text)
            if tr and tr["title_id"] != "UNKNOWN":
                k = tr["trace_key"]
                prev = extracted.get(k)
                if not prev or len(tr["events"]) > len(prev["events"]):
                    extracted[k] = tr

    print(f"=== VCFG TELEMETRY TRIAGE (Current Version: {cur_ver}) ===")
    for b_key, items in buckets.items():
        print(f"\n[{b_key}] ({len(items)} logs)")
        for it in items[:25]:
            status = "VERIFIED" if it["verified"] else "UNVERIFIED"
            print(
                f"  - {it['title_id']} (fam={it['family']}, {status}) | v{it['version']} | "
                f"crash={it['crash']} | flips={it['flips']} | {it['file']}"
            )
        if len(items) > 25:
            print(f"  ... and {len(items) - 25} more")

    if args.extract_traces and extracted:
        sorted_traces = [extracted[k] for k in sorted(extracted.keys())]
        REAL_TRACES_JSON.parent.mkdir(parents=True, exist_ok=True)
        REAL_TRACES_JSON.write_text(json.dumps(sorted_traces, indent=2), encoding="utf-8")
        print(f"\nSaved {len(sorted_traces)} unique CUSAxxxxx hardware boot traces to {REAL_TRACES_JSON}")


if __name__ == "__main__":
    main()
