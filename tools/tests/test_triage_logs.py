#!/usr/bin/env python3
"""
Unit tests for tools/triage_logs.py (R20 Log Triage & Supported Title Miner v2.0).

Tests:
1. All 5 classification buckets:
   - BUCKET_1_OLD_VERSION
   - BUCKET_2_L2_NO_CRASH
   - BUCKET_3_NEW_TITLE_CRASH
   - BUCKET_4_VERIFIED_REGRESSION
   - BUCKET_5_NEW_SUPPORTED_CANDIDATE
2. All 3 hidden symptom sub-classifiers:
   - SYMPTOM_LOW_FPS_LOCK (real flips < 20.0/s or mids_shown == 0 with FG ON)
   - SYMPTOM_AUDIO_DM_STARVATION (0x8002000c on 16 KB..1 MB after render start)
   - SYMPTOM_LOADING_FREEZE (RepeatCount >= 10 after DirectMemory alloc/map with no subsequent Present #)
3. Supported CUSAxxxxx resolution (direct k_sup in launcher/src/main.c + regional aliases in overlay/title_families.h)
   vs new BUCKET_5 candidates, including auto_2buf detection (ngame == 2 / two_buffer) and k_sup C-line generation.
4. APP_VER vs plugin version disambiguation and multi-title bundle splitting.
5. Idempotent --export-traces / --extract-traces and --report-md / --json CLI flags.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import triage_logs  # noqa: E402


class TestTriageLogsR20(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cur_ver = triage_logs.load_current_version()
        cls.k_sup_ids, cls.auto_2buf_ids, cls.auto_alt_ids = triage_logs.load_k_sup()
        cls.verified, cls.aliases = triage_logs.load_verified_families()

    def test_supported_database_loads_k_sup_and_families(self) -> None:
        """Verify Read-Only parser extracts k_sup from launcher/src/main.c and families from overlay/title_families.h."""
        self.assertGreaterEqual(len(self.k_sup_ids), 20)
        self.assertIn("CUSA07439", self.k_sup_ids)
        self.assertIn("CUSA00411", self.k_sup_ids)
        self.assertIn("CUSA08519", self.k_sup_ids)

        # Direct k_sup entry
        canon_dir, is_sup_dir = triage_logs.resolve_family("CUSA07439", self.verified, self.aliases)
        self.assertTrue(is_sup_dir)
        self.assertEqual(canon_dir, "CUSA07439")

        # Regional family alias in overlay/title_families.h (CUSA00552 aliases to CUSA00557, CUSA03041 aliases to CUSA08519)
        canon_alias, is_sup_alias = triage_logs.resolve_family("CUSA00552", self.verified, self.aliases)
        self.assertTrue(is_sup_alias)
        self.assertEqual(canon_alias, "CUSA00557")

        canon_alias2, is_sup_alias2 = triage_logs.resolve_family("CUSA03041", self.verified, self.aliases)
        self.assertTrue(is_sup_alias2)
        self.assertEqual(canon_alias2, "CUSA08519")

        # Unlisted title ID
        canon_new, is_sup_new = triage_logs.resolve_family("CUSA99901", self.verified, self.aliases)
        self.assertFalse(is_sup_new)
        self.assertEqual(canon_new, "CUSA99901")

    def test_version_extraction_ignores_app_ver(self) -> None:
        """Ensure game patch version (APP_VER) is never confused with VCFG plugin version."""
        text_only_app_ver = (
            "[VCFG] GV start: title CUSA00411 version 01.38\n"
            "CUSA00552 version=01.00 installed=1\n"
            "[VCFG] Present #10: mids_Built=5 mids_shown=5\n"
        )
        ver, has_explicit = triage_logs.extract_plugin_version(text_only_app_ver, default_ver=self.cur_ver)
        self.assertFalse(has_explicit)
        self.assertEqual(ver, self.cur_ver)

        text_with_plugin_ver = (
            f"plugin_load enter: title=CUSA00411 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] GV start: title CUSA00411 version 01.38\n"
        )
        ver2, has_explicit2 = triage_logs.extract_plugin_version(text_with_plugin_ver, default_ver="0.00")
        self.assertTrue(has_explicit2)
        self.assertEqual(ver2, self.cur_ver)

    def test_bucket_1_old_version_with_and_without_crash(self) -> None:
        """Logs from older plugin versions (< VERSION) must go to BUCKET_1_OLD_VERSION even without a crash."""
        log_no_crash_old = (
            "plugin_load enter: title=CUSA07408 version=v2.14 dm=0x128000000\n"
            "[VCFG] Present #500: real flips/s: 30.0 mids_shown=490\n"
        )
        rec1 = triage_logs.analyze_report_text(
            "old_nocrash.txt", log_no_crash_old, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec1["bucket"], "BUCKET_1_OLD_VERSION")
        self.assertEqual(rec1["version"], "2.14")
        self.assertEqual(rec1["crash"], "NONE")

        log_crash_old = (
            "plugin_load enter: title=CUSA07408 version=v2.20 dm=0x128000000\n"
            "[CRASH] SIGSEGV\n"
        )
        rec2 = triage_logs.analyze_report_text(
            "old_crash.txt", log_crash_old, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec2["bucket"], "BUCKET_1_OLD_VERSION")
        self.assertEqual(rec2["crash"], "SIGSEGV")

    def test_bucket_2_l2_no_crash_for_supported_and_short_sessions(self) -> None:
        """Supported titles or short sessions without crash land in BUCKET_2_L2_NO_CRASH."""
        # Supported title CUSA07408 with healthy long run stays in BUCKET_2 (already in k_sup)
        log_supported = (
            f"plugin_load enter: title=CUSA07408 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #600: real flips/s: 30.0 mids_shown=590\n"
        )
        rec_sup = triage_logs.analyze_report_text(
            "sup_ok.txt", log_supported, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_sup["bucket"], "BUCKET_2_L2_NO_CRASH")
        self.assertTrue(rec_sup["verified"])

        # Regional alias CUSA00552 with healthy long run also stays in BUCKET_2
        log_alias = (
            f"plugin_load enter: title=CUSA00552 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #600: real flips/s: 30.0 mids_shown=590\n"
        )
        rec_alias = triage_logs.analyze_report_text(
            "alias_ok.txt", log_alias, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_alias["bucket"], "BUCKET_2_L2_NO_CRASH")
        self.assertTrue(rec_alias["verified"])
        self.assertEqual(rec_alias["family"], "CUSA00557")

        # Unlisted title CUSA99900 with too few frames (< 300 Presents) stays in BUCKET_2
        log_short = (
            f"plugin_load enter: title=CUSA99900 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #120: real flips/s: 30.0 mids_shown=110\n"
        )
        rec_short = triage_logs.analyze_report_text(
            "short_unlisted.txt", log_short, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_short["bucket"], "BUCKET_2_L2_NO_CRASH")

    def test_bucket_3_new_title_crash_and_bucket_4_verified_regression(self) -> None:
        """Unverified crash on current version -> BUCKET_3; verified crash/INERT_NO_MBUF on current version -> BUCKET_4."""
        log_unverified_crash = (
            f"plugin_load enter: title=CUSA99988 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #40\n"
            "=== CRASH SIGSEGV (sig=11 code=1) at 12.4s | flips=40 ===\n"
        )
        rec3 = triage_logs.analyze_report_text(
            "unverified_crash.txt", log_unverified_crash, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec3["bucket"], "BUCKET_3_NEW_TITLE_CRASH")
        self.assertFalse(rec3["verified"])
        self.assertEqual(rec3["crash"], "SIGSEGV")

        log_verified_regression = (
            f"plugin_load enter: title=CUSA07408 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #45\n"
            "[CRASH] SIGBUS\n"
        )
        rec4 = triage_logs.analyze_report_text(
            "verified_reg.txt", log_verified_regression, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec4["bucket"], "BUCKET_4_VERIFIED_REGRESSION")
        self.assertTrue(rec4["verified"])
        self.assertEqual(rec4["crash"], "SIGBUS")

        log_verified_inert = (
            f"plugin_load enter: title=CUSA00552 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] status: INERT: NO MBUF\n"
        )
        rec4_inert = triage_logs.analyze_report_text(
            "verified_inert.txt", log_verified_inert, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec4_inert["bucket"], "BUCKET_4_VERIFIED_REGRESSION")
        self.assertEqual(rec4_inert["crash"], "INERT_NO_MBUF")

    def test_bucket_5_new_supported_candidate_and_auto_2buf(self) -> None:
        """Unlisted CUSAxxxxx with >= 300 Presents, >= 100 mids_shown, no crash/ENOMEM/symptoms -> BUCKET_5."""
        log_candidate_std = (
            f"plugin_load enter: title=CUSA99901 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] rt_match: ngame=3 w=1920 h=1080\n"
            "[VCFG] Present #450: real flips/s: 30.0 mids_shown=415\n"
        )
        rec_std = triage_logs.analyze_report_text(
            "cand_std.txt", log_candidate_std, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_std["bucket"], "BUCKET_5_NEW_SUPPORTED_CANDIDATE")
        self.assertFalse(rec_std["needs_auto_2buf"])
        self.assertEqual(
            rec_std["k_sup_line"],
            '{ "CUSA99901", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },',
        )

        # Candidate with ngame=2 (2-buffer flip chain)
        log_candidate_2buf = (
            f"plugin_load enter: title=CUSA99902 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] rt_match: ngame=2 two_buffer=1 w=1920 h=1080\n"
            "[VCFG] Present #520: real flips/s: 29.8 mids_shown=495\n"
        )
        rec_2buf = triage_logs.analyze_report_text(
            "cand_2buf.txt", log_candidate_2buf, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_2buf["bucket"], "BUCKET_5_NEW_SUPPORTED_CANDIDATE")
        self.assertTrue(rec_2buf["needs_auto_2buf"])
        self.assertEqual(rec_2buf["ngame"], 2)
        self.assertEqual(
            rec_2buf["k_sup_line"],
            '{ "CUSA99902", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },',
        )

    def test_symptom_low_fps_lock_detection(self) -> None:
        """Test SYMPTOM_LOW_FPS_LOCK via < 20.0 real flips/s and via mids_shown == 0 with FG ON."""
        # Case A: real flips/s < 20.0 after >= 300 Presents
        log_low_fps = (
            f"plugin_load enter: title=CUSA99903 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #350: real flips/s: 14.5 mids_shown=300\n"
        )
        rec_a = triage_logs.analyze_report_text(
            "low_fps.txt", log_low_fps, self.cur_ver, self.verified, self.aliases
        )
        self.assertIn("SYMPTOM_LOW_FPS_LOCK", rec_a["symptoms"])
        # Because of hidden symptom, unlisted CUSA99903 must NOT enter BUCKET_5
        self.assertEqual(rec_a["bucket"], "BUCKET_2_L2_NO_CRASH")

        # Case B: FG ON but mids_shown == 0 after >= 300 Presents
        log_zero_mids = (
            f"plugin_load enter: title=CUSA07408 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] FG ON\n"
            "[VCFG] Present #400: real flips/s: 30.0 mids_shown=0\n"
        )
        rec_b = triage_logs.analyze_report_text(
            "zero_mids.txt", log_zero_mids, self.cur_ver, self.verified, self.aliases
        )
        self.assertIn("SYMPTOM_LOW_FPS_LOCK", rec_b["symptoms"])

    def test_symptom_audio_dm_starvation_detection(self) -> None:
        """Test SYMPTOM_AUDIO_DM_STARVATION on 0x8002000c for 16 KB..1 MB allocations after render start."""
        # Positive case: 64 KB (0x10000) DirectMemory ENOMEM after Present #1
        log_audio_starve = (
            f"plugin_load enter: title=CUSA99904 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #1: render start\n"
            "[VCFG] Present #350: real flips/s: 30.0 mids_shown=340\n"
            "[VCFG] AllocateDirectMemory failed ret=0x8002000c size=0x10000\n"
        )
        rec_pos = triage_logs.analyze_report_text(
            "audio_starve.txt", log_audio_starve, self.cur_ver, self.verified, self.aliases
        )
        self.assertIn("SYMPTOM_AUDIO_DM_STARVATION", rec_pos["symptoms"])
        self.assertEqual(rec_pos["bucket"], "BUCKET_2_L2_NO_CRASH")

        # Negative case: large 64 MB (0x4000000) ENOMEM at boot (f:0) is NOT audio starvation
        log_large_pre = (
            f"plugin_load enter: title=CUSA07408 version=v{self.cur_ver} dm=0x128000000\n"
            "[ t:10 f:0] game AllocateDirectMemory(len=0x4000000 align=0x200000 type=3 range=0x0..0x128000000) = 0x8002000c phys=0x0\n"
            "[VCFG] Present #1: render start\n"
            "[VCFG] Present #350: real flips/s: 30.0 mids_shown=340\n"
        )
        rec_neg = triage_logs.analyze_report_text(
            "large_pre.txt", log_large_pre, self.cur_ver, self.verified, self.aliases
        )
        self.assertNotIn("SYMPTOM_AUDIO_DM_STARVATION", rec_neg["symptoms"])

    def test_symptom_loading_freeze_detection(self) -> None:
        """Test SYMPTOM_LOADING_FREEZE when RepeatCount >= 10 follows DirectMemory alloc/map with no new Present #."""
        # Positive case: DirectMemory map followed by Overlay RepeatCount=14 and no subsequent Present #
        log_freeze = (
            f"plugin_load enter: title=CUSA99905 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #200: real flips/s: 30.0 mids_shown=190\n"
            "[VCFG] hook sceKernelAllocateDirectMemory len=0x2000000 ok\n"
            "[VCFG] hook sceKernelMapDirectMemory addr=0x100000000 len=0x2000000\n"
            "[Overlay] RepeatCount=14 msg=waiting_for_flip\n"
        )
        rec_freeze = triage_logs.analyze_report_text(
            "loading_freeze.txt", log_freeze, self.cur_ver, self.verified, self.aliases
        )
        self.assertIn("SYMPTOM_LOADING_FREEZE", rec_freeze["symptoms"])

        # Negative case: Present # resumes after RepeatCount >= 10
        log_resumed = (
            f"plugin_load enter: title=CUSA99905 version=v{self.cur_ver} dm=0x128000000\n"
            "[VCFG] Present #200: real flips/s: 30.0 mids_shown=190\n"
            "[VCFG] hook sceKernelAllocateDirectMemory len=0x2000000 ok\n"
            "[Overlay] RepeatCount=14 msg=waiting_for_flip\n"
            "[VCFG] Present #350: real flips/s: 30.0 mids_shown=340\n"
        )
        rec_resumed = triage_logs.analyze_report_text(
            "loading_resumed.txt", log_resumed, self.cur_ver, self.verified, self.aliases
        )
        self.assertNotIn("SYMPTOM_LOADING_FREEZE", rec_resumed["symptoms"])

    def test_cli_export_traces_idempotent_and_report_md(self) -> None:
        """Verify --export-traces is idempotent and --report-md generates a clean CUSAxxxxx-only Markdown report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            logs_dir = tmp_path / "logs"
            logs_dir.mkdir()

            sample_log = (
                f"plugin_load enter: title=CUSA99910 version=v{self.cur_ver} dm=0x128000000\n"
                "[VCFG] rt_match: ngame=2 w=1920 h=1080\n"
                "[VCFG] Present #500: real flips/s: 30.0 mids_shown=475\n"
                "==== boot stages (/data/bb_probe/boot-CUSA99910.txt) ====\n"
                f"plugin_load enter: title=CUSA99910 version=v{self.cur_ver} dm=0x128000000\n"
                "[ t:10 f:0] game AllocateDirectMemory(len=0x1000000 align=0x200000 type=3 range=0x0..0x128000000) = 0x0 phys=0x20000000\n"
                "[ t:20 f:0] RegisterBuffers enter: handle=1 start=0 num=2\n"
                "attr: fmt=0x80000000 (supported=1) tiling=0 aspect=0 1920x1080 pitch=1920\n"
                "==== end ====\n"
            )
            (logs_dir / "sample_report.txt").write_text(sample_log, encoding="utf-8")

            traces_json = tmp_path / "real_traces_test.json"
            report_md = tmp_path / "dashboard.md"

            cmd = [
                sys.executable,
                str(REPO_ROOT / "tools" / "triage_logs.py"),
                str(logs_dir),
                "--export-traces",
                str(traces_json),
                "--report-md",
                str(report_md),
                "--json",
            ]
            res1 = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data1 = json.loads(res1.stdout)

            self.assertEqual(
                len(data1["buckets"]["BUCKET_5_NEW_SUPPORTED_CANDIDATE"]),
                1,
            )
            self.assertEqual(len(data1["k_sup_candidates"]), 1)
            self.assertEqual(data1["k_sup_candidates"][0]["title_id"], "CUSA99910")
            self.assertTrue(data1["k_sup_candidates"][0]["needs_auto_2buf"])

            # Verify exported trace JSON and idempotency on second run
            exported_1 = json.loads(traces_json.read_text(encoding="utf-8"))
            self.assertEqual(len(exported_1), 1)
            self.assertEqual(exported_1[0]["title_id"], "CUSA99910")

            res2 = subprocess.run(cmd, capture_output=True, text=True, check=True)
            exported_2 = json.loads(traces_json.read_text(encoding="utf-8"))
            self.assertEqual(len(exported_2), 1)
            self.assertEqual(exported_1, exported_2)

            # Verify Markdown report contents
            md_text = report_md.read_text(encoding="utf-8")
            self.assertIn("# VCFG Telemetry Triage & Supported Title Miner Dashboard", md_text)
            self.assertIn("BUCKET_5_NEW_SUPPORTED_CANDIDATE", md_text)
            self.assertIn("CUSA99910", md_text)
            self.assertIn('{ "CUSA99910", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },', md_text)
            self.assertIn("needs auto_2buf", md_text)
            self.assertTrue(triage_logs.verify_no_game_names(md_text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
