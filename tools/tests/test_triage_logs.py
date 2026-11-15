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

        canon_alias3, is_sup_alias3 = triage_logs.resolve_family("CUSA36848", self.verified, self.aliases)
        self.assertTrue(is_sup_alias3)
        self.assertEqual(canon_alias3, "CUSA36842")

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

    def test_options_effective_line_does_not_false_positive_inert(self) -> None:
        """Verify options effective lines with ready/non-FG inert tokens do not block BUCKET_5, while framegen=on(inert: no mbuf) is caught."""
        log_healthy_with_opt = (
            f"plugin_load enter: title=CUSA99911 version=v{self.cur_ver} dm=0x128000000\n"
            "options effective: framegen=on(ready: starting) preset=quality below30=on(inert: no 2nd buffer) "
            "shallow_queue=on(inert: no timer pacing) game_vectors=on(ready: fg starting) hud_protect=1(ready: fg starting) | init\n"
            "options effective: framegen=on(active) preset=quality below30=on(inert: no 2nd buffer) "
            "shallow_queue=on(inert: no timer pacing) game_vectors=on(active) hud_protect=1(active: no hud file) | framegen ready->active\n"
            "[VCFG] rt_match: ngame=3 w=1920 h=1080\n"
            "[VCFG] Present #450: real flips/s: 30.0 mids_shown=420\n"
        )
        rec_ok = triage_logs.analyze_report_text(
            "opt_ok.txt", log_healthy_with_opt, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_ok["bucket"], "BUCKET_5_NEW_SUPPORTED_CANDIDATE")

        log_fg_inert_no_mbuf = (
            f"plugin_load enter: title=CUSA00552 version=v{self.cur_ver} dm=0x128000000\n"
            "options effective: framegen=on(inert: no mbuf) preset=quality | frame 120\n"
        )
        rec_inert = triage_logs.analyze_report_text(
            "opt_inert.txt", log_fg_inert_no_mbuf, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_inert["bucket"], "BUCKET_4_VERIFIED_REGRESSION")
        self.assertEqual(rec_inert["crash"], "INERT_NO_MBUF")

    def test_terminal_raw_oom_burst_vs_recovered_probe(self) -> None:
        """Terminal >=3 raw 0x80020023 burst classifies as fatal ENOMEM even past 120 flips; recovered probes stay BUCKET_2."""
        boot_fatal = (
            f"plugin_load enter: title=CUSA99912 version=v{self.cur_ver} dm=0x14aa00000\n"
            "13.843 [t:101941 f:265] memory valve (step 2 hibernation): released g_work_phys at flip 265\n"
            "13.852 [t:101941 f:265] game AllocateDirectMemory(len=0x80000 align=0x4000 type=0 range=0x0..0x14aa00000) = 0x80020023 phys=0x0\n"
            "13.853 [t:101941 f:265] game AllocateDirectMemory(len=0x80000 align=0x4000 type=0 range=0x0..0x14aa00000) = 0x80020023 phys=0x0\n"
            "14.117 [t:101941 f:266] game AllocateDirectMemory(len=0x80000 align=0x4000 type=0 range=0x0..0x14aa00000) = 0x80020023 phys=0x0\n"
        )
        rec_fatal = triage_logs.analyze_report_text(
            "boot-CUSA99912.txt", boot_fatal, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_fatal["bucket"], "BUCKET_3_NEW_TITLE_CRASH")
        self.assertEqual(rec_fatal["crash"], "ENOMEM")

        bundled_report = (
            f"VCFG bug report\napp version: {self.cur_ver}\n\n"
            "==== flight recorder (/data/bb_probe/trace-CUSA99912.txt) ====\n"
            f"title: CUSA99912\nversion: {self.cur_ver}\nmode4 #131\n\n"
            "==== early start stages (/data/bb_probe/boot-CUSA99912.txt) ====\n"
            + boot_fatal
            + "\n==== plugin log (/data/bb_probe/probe.log) ====\nmode4 #131\n"
        )
        rec_bundle = triage_logs.analyze_report_text(
            "vcfg_report.txt", bundled_report, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_bundle["bucket"], "BUCKET_3_NEW_TITLE_CRASH")
        self.assertEqual(rec_bundle["crash"], "ENOMEM")

        recovered_probe = (
            f"plugin_load enter: title=CUSA99912 version=v{self.cur_ver} dm=0x14aa00000\n"
            "2.000 [t:100100 f:0] game AllocateDirectMemory(len=0x80000 align=0x4000 type=0 range=0x0..0x14aa00000) = 0x80020023 phys=0x0\n"
            "2.001 [t:100100 f:0] game AllocateDirectMemory(len=0x80000 align=0x4000 type=0 range=0x0..0x14aa00000) = 0x80020023 phys=0x0\n"
            "2.002 [t:100100 f:0] game AllocateDirectMemory(len=0x80000 align=0x4000 type=0 range=0x0..0x14aa00000) = 0x80020023 phys=0x0\n"
            "[VCFG] Present #150: real flips/s: 30.0 mids_shown=140\n"
        )
        rec_ok = triage_logs.analyze_report_text(
            "recovered.txt", recovered_probe, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec_ok["bucket"], "BUCKET_2_L2_NO_CRASH")
        self.assertEqual(rec_ok["crash"], "NONE")

    # ---- CUSA01967: a terminal 0x80020023 burst in boot/report logs is not a crash when the session kept rendering
    def _cusa01967_boot_tail(self, last_flip: int = 2505) -> str:
        """Boot-log tail mirroring the live CUSA01967 session: hibernation + crisis + a terminal OOM burst at f:2505."""
        lines = [
            f"plugin_load enter: title=CUSA01967 version=v{self.cur_ver} dm=0x14aa00000 neo=1 shave_mode=2 reg_mode=2",
            "9.031 [t:101670 f:120] mbufs ok: len=0x2000000 phys=0x148a00000 va=881400000",
            "13.821 [t:102689 f:264] memory valve (step 2 hibernation): drained GPU (waits=0) and released g_work_phys at flip 264",
            "13.834 [t:102689 f:264] memory valve (crisis): released the registered mid reserve va=881400000 phys=0x148a00000 "
            "len=0x2000000 unmap=0x00000000 release=0x00000000 at flip 264 (hibernated at 264, retired=1, attempt 1)",
        ]
        for i, f in enumerate((2499, 2499, 2500, 2501, 2503, last_flip, last_flip)):
            lines.append(
                f"54.{900 + i:03d} [t:102690 f:{f}] game AllocateDirectMemory(len=0x80000 align=0x4000 type=0 "
                f"range=0x0..0x14aa00000) = 0x80020023 phys=0x0 caller=60a6d5"
            )
        return "\n".join(lines) + "\n"

    def _cusa01967_probe(self, flips: int) -> str:
        """Paired live session log: crash-free rendering well past the boot log's last flip."""
        return (
            f"plugin_load enter: title=CUSA01967 version=v{self.cur_ver} dm=0x14aa00000 neo=1 shave_mode=2 reg_mode=2\n"
            f"11.31 memory valve (crisis): released the registered mid reserve phys=0x148a00000 len=0x2000000 "
            f"(FG native for this session)\n"
            f"[VCFG] Present #{flips}: real flips/s: 30.0\n"
        )

    def test_boot_oom_burst_downgraded_when_probe_kept_rendering(self) -> None:
        """boot-<CUSA>.txt ends on an OOM burst at f:2505 but probe.log of the same run reached flips=3829 crash-free:
        the burst is the game's own mid-session allocator management, not a crash -> BUCKET_2."""
        boot = triage_logs.analyze_report_text(
            "boot-CUSA01967.txt", self._cusa01967_boot_tail(), self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(boot["crash"], "ENOMEM")
        self.assertEqual(boot["oom_reason"], "terminal_burst")
        self.assertEqual(boot["oom_burst_flip"], 2505)
        self.assertEqual(boot["bucket"], "BUCKET_3_NEW_TITLE_CRASH")

        probe = triage_logs.analyze_report_text(
            "probe.log", self._cusa01967_probe(3829), self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(probe["crash"], "NONE")
        self.assertEqual(probe["flips"], 3829)

        triage_logs._downgrade_live_oom([boot, probe])
        self.assertEqual(boot["crash"], "NONE")
        self.assertEqual(boot["bucket"], "BUCKET_2_L2_NO_CRASH")

    def test_report_oom_burst_downgraded_by_own_flip_progression(self) -> None:
        """vcfg_report.txt embeds a boot-log section ending on the f:2505 burst while a later section's flip counter
        reached 3829: the within-file progression across sections proves the session lived on -> BUCKET_2."""
        report = (
            "==== boot log ====\n"
            + self._cusa01967_boot_tail()
            + "==== summary ====\n"
            + f"[VCFG] Present #3829: real flips/s: 30.0\n"
        )
        rec = triage_logs.analyze_report_text(
            "vcfg_report.txt", report, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec["crash"], "ENOMEM")
        self.assertEqual(rec["oom_reason"], "terminal_burst")
        self.assertEqual(rec["oom_burst_flip"], 2505)
        self.assertEqual(rec["flips"], 3829)
        triage_logs._downgrade_live_oom([rec])
        self.assertEqual(rec["crash"], "NONE")
        self.assertEqual(rec["bucket"], "BUCKET_2_L2_NO_CRASH")

    def test_boot_oom_burst_stays_bucket_3_without_live_sibling(self) -> None:
        """Negative control: the same boot log with no probe/trace sibling and no own progression stays a real crash."""
        boot = triage_logs.analyze_report_text(
            "boot-CUSA01967.txt", self._cusa01967_boot_tail(), self.cur_ver, self.verified, self.aliases
        )
        triage_logs._downgrade_live_oom([boot])
        self.assertEqual(boot["crash"], "ENOMEM")
        self.assertEqual(boot["bucket"], "BUCKET_3_NEW_TITLE_CRASH")

    def test_boot_oom_burst_stays_bucket_3_when_sibling_margin_too_small(self) -> None:
        """Negative control: a sibling only marginally past the burst (< 120 flips) does not prove liveness."""
        boot = triage_logs.analyze_report_text(
            "boot-CUSA01967.txt", self._cusa01967_boot_tail(), self.cur_ver, self.verified, self.aliases
        )
        probe = triage_logs.analyze_report_text(
            "probe.log", self._cusa01967_probe(2505 + 119), self.cur_ver, self.verified, self.aliases
        )
        triage_logs._downgrade_live_oom([boot, probe])
        self.assertEqual(boot["crash"], "ENOMEM")
        self.assertEqual(boot["bucket"], "BUCKET_3_NEW_TITLE_CRASH")

    def test_explicit_crash_tag_never_downgraded(self) -> None:
        """Negative control: a real [CRASH] tag is a crash regardless of any live sibling."""
        boot = self._cusa01967_boot_tail() + "[CRASH] SIGSEGV\n"
        rec = triage_logs.analyze_report_text(
            "boot-CUSA01967.txt", boot, self.cur_ver, self.verified, self.aliases
        )
        self.assertEqual(rec["oom_reason"], "")
        probe = triage_logs.analyze_report_text(
            "probe.log", self._cusa01967_probe(3829), self.cur_ver, self.verified, self.aliases
        )
        triage_logs._downgrade_live_oom([rec, probe])
        self.assertNotEqual(rec["bucket"], "BUCKET_2_L2_NO_CRASH")
        self.assertIn("SIGSEGV", rec["crash"])

    # ---- CUSA01967: a crisis-release session must not mine a k_sup supported-candidate line
    def _cusa01967_report_fg_evidence(self, with_crisis_line: bool = True) -> str:
        """Report-shaped session with real FG evidence (options effective + mids), optionally with the crisis line."""
        return (
            f"plugin_load enter: title=CUSA01967 version=v{self.cur_ver} dm=0x14aa00000 neo=1 shave_mode=2 reg_mode=2\n"
            "options effective: framegen=on(active) preset=quality below30=off shallow_queue=off sharpness=40"
            "(applied: generated) enhance=light(applied: generated) gen_buffers=1 game_vectors=off "
            "hud_protect=1(active: no hud file) fps_lock=30 | hot-reload\n"
            "[VCFG] Present #400: real flips/s: 30.0 mids_shown=380\n"
            + ("memory valve (crisis): released the registered mid reserve phys=0x148a00000 len=0x2000000 "
               "(FG native for this session)\n" if with_crisis_line else "")
            + "[VCFG] Present #5400: real flips/s: 30.0 mids_shown=0\n"
        )

    def test_crisis_release_session_not_a_supported_candidate(self) -> None:
        """FG evidence from a crisis session predates the release (FG runs native for the whole remainder), so the
        session must not mine a k_sup line even with flips >= 300 and mids_shown >= 100."""
        rec = triage_logs.analyze_report_text(
            "vcfg_report.txt", self._cusa01967_report_fg_evidence(), self.cur_ver, self.verified, self.aliases
        )
        self.assertTrue(rec["crisis_released"])
        self.assertEqual(rec["crash"], "NONE")
        self.assertEqual(rec["bucket"], "BUCKET_2_L2_NO_CRASH")
        # k_sup candidates are mined from BUCKET_5 entries only; a BUCKET_2 crisis session contributes none.

    def test_rotated_crisis_session_still_excluded_via_post_crisis_options(self) -> None:
        """The boot section may rotate past the crisis line before the report is saved; the post-crisis
        options-effective evidence (native: memory crisis / framegen=on with gen_buffers=0) must exclude the
        session from BUCKET_5 all the same."""
        rotated = self._cusa01967_report_fg_evidence(with_crisis_line=False)
        rotated = rotated.replace(
            "options effective: framegen=on(active) preset=quality below30=off shallow_queue=off sharpness=40"
            "(applied: generated) enhance=light(applied: generated) gen_buffers=1 game_vectors=off "
            "hud_protect=1(active: no hud file) fps_lock=30 | hot-reload\n",
            "options effective: framegen=on(native: memory crisis) preset=quality below30=off shallow_queue=off "
            "sharpness=40(applied: real) enhance=light(applied: real) gen_buffers=0 game_vectors=off "
            "hud_protect=1(ready: fg starting) fps_lock=30(inert: fg not running) | runtime\n",
        )
        rec = triage_logs.analyze_report_text(
            "vcfg_report.txt", rotated, self.cur_ver, self.verified, self.aliases
        )
        self.assertTrue(rec["crisis_released"])
        self.assertEqual(rec["bucket"], "BUCKET_2_L2_NO_CRASH")

        # variant: the transient post-crisis 'change' line (framegen=on(active) with gen_buffers=0)
        rotated2 = self._cusa01967_report_fg_evidence(with_crisis_line=False).replace(
            "gen_buffers=1 game_vectors=off", "gen_buffers=0 game_vectors=off", 1
        )
        rec2 = triage_logs.analyze_report_text(
            "vcfg_report.txt", rotated2, self.cur_ver, self.verified, self.aliases
        )
        self.assertTrue(rec2["crisis_released"])
        self.assertEqual(rec2["bucket"], "BUCKET_2_L2_NO_CRASH")

    def test_same_fg_evidence_without_crisis_still_a_candidate(self) -> None:
        """Control: identical FG evidence without a crisis release keeps BUCKET_5 candidacy (k_sup mined)."""
        rec = triage_logs.analyze_report_text(
            "vcfg_report.txt",
            self._cusa01967_report_fg_evidence().replace(
                "memory valve (crisis): released the registered mid reserve phys=0x148a00000 len=0x2000000 "
                "(FG native for this session)\n",
                "",
            ),
            self.cur_ver,
            self.verified,
            self.aliases,
        )
        self.assertFalse(rec["crisis_released"])
        self.assertEqual(rec["bucket"], "BUCKET_5_NEW_SUPPORTED_CANDIDATE")
        self.assertIn("CUSA01967", rec["k_sup_line"])

    def test_superseded_prev_save_bundle_does_not_mint_candidate(self) -> None:
        """A report's `.prev` region is the previous save; when a newer save of the same title exists in the same
        report, the stale bundle must not mint a k_sup line (its embedded logs may have rotated past the crisis
        evidence at save time - the CUSA01967 report split into exactly this pair)."""
        current = triage_logs.analyze_report_text(
            "vcfg_report.txt", self._cusa01967_report_fg_evidence(), self.cur_ver, self.verified, self.aliases
        )
        stale = triage_logs.analyze_report_text(
            "vcfg_report.txt:CUSA01967.prev",
            self._cusa01967_report_fg_evidence().replace(
                "memory valve (crisis): released the registered mid reserve phys=0x148a00000 len=0x2000000 "
                "(FG native for this session)\n",
                "",
            ),
            self.cur_ver,
            self.verified,
            self.aliases,
        )
        self.assertEqual(stale["bucket"], "BUCKET_5_NEW_SUPPORTED_CANDIDATE")   # pre-demotion state
        triage_logs._demote_superseded_prev_candidates([current, stale])
        self.assertEqual(stale["bucket"], "BUCKET_2_L2_NO_CRASH")
        self.assertEqual(stale["k_sup_line"], "")
        self.assertEqual(current["bucket"], "BUCKET_2_L2_NO_CRASH")             # crisis session stays demoted

    def test_prev_bundle_keeps_candidate_when_title_absent_from_newer_save(self) -> None:
        """Control: if the newer save does not cover the title (title switch), the `.prev` bundle stays a candidate."""
        stale = triage_logs.analyze_report_text(
            "vcfg_report.txt:CUSA01967.prev",
            self._cusa01967_report_fg_evidence().replace(
                "memory valve (crisis): released the registered mid reserve phys=0x148a00000 len=0x2000000 "
                "(FG native for this session)\n",
                "",
            ),
            self.cur_ver,
            self.verified,
            self.aliases,
        )
        other = triage_logs.analyze_report_text(
            "vcfg_report.txt", self._cusa01967_report_fg_evidence().replace("CUSA01967", "CUSA00552"),
            self.cur_ver, self.verified, self.aliases,
        )
        triage_logs._demote_superseded_prev_candidates([stale, other])
        self.assertEqual(stale["bucket"], "BUCKET_5_NEW_SUPPORTED_CANDIDATE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
