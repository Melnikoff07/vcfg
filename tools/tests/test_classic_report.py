import importlib.util
import pathlib
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('classic_report', TOOLS / 'classic_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
TEMP = TOOLS.parents[0] / 'build' / 'tests'
TEMP.mkdir(parents=True, exist_ok=True)


class TraceReportTest(unittest.TestCase):
    def test_truncated_and_missing_fields_do_not_become_success(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            p = pathlib.Path(d) / 'trace-CUSA05571.txt'
            p.write_text("VCFG trace\ntitle: CUSA05571\nversion: 2.14\nengine: classic\n"
                         "0.00 RegisterBuffers handle=1 start=0 num=2\n"
                         "0.00 attr: fmt=0x80000000 (supported=1) tiling=0 aspect=0 1920x1080 pitch=1920\n"
                         "4.00 CADENCE (vblanks on screen) mid 1:60 2:0 3:0 4+:0 | real 1:60 2:0 3:0 4+:0 | game cpu relea\n")
            row = report.parse_trace(p)
            self.assertEqual(row['title'], 'CUSA05571')
            self.assertEqual(row['buffers'], '2')
            self.assertEqual(row['fps'], '-')
            self.assertEqual(row['latency'], '-')
            self.assertIn('unknown', row['state'])
            self.assertNotIn('WORKS', row['state'])

    def test_previous_start_stays_separate_and_title_without_trace_is_untested(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            root = pathlib.Path(d)
            for name, rate in [('trace-CUSA05571.txt', 27), ('trace-CUSA05571.prev.txt', 40)]:
                (root / name).write_text(f'title: CUSA05571\nSTAT gnm: {rate}.0 flips/s\n')
            (root / 'config-CUSA01113.ini').write_text('framegen=1\n')
            text = report.report(root)
            self.assertIn('27.0–27.0', text)
            self.assertIn('40.0–40.0', text)
            self.assertNotIn('27.0–40.0', text)
            self.assertIn('CUSA01113', text)
            self.assertIn('A missing trace is untested', text)

    def test_opaque_error_string_is_escaped_in_table(self):
        self.assertIn(r'failed \| reason', report.table([{'state': 'failed | reason'}], ['state']))


if __name__ == '__main__':
    unittest.main()
