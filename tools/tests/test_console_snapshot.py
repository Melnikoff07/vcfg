import contextlib
import ftplib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('console_tool', ROOT / 'tools/console.py')
console = importlib.util.module_from_spec(spec)
spec.loader.exec_module(console)
TEMP = ROOT / 'build/tests'
TEMP.mkdir(parents=True, exist_ok=True)


class ReadOnlyFTP:
    def __init__(self, missing=False):
        self.reads = []
        self.missing = missing

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def nlst(self, path):
        return [path + 'trace-CUSA05571.txt', path + 'config-CUSA05571.ini', path + 'dump_cur.rgba',
                path + '../evil.txt', path + 'debug_log']

    def retrbinary(self, command, receive):
        if not command.startswith('RETR '):
            raise AssertionError('unexpected FTP command')
        self.reads.append(command)
        if self.missing and 'trace-' in command:
            raise ftplib.error_perm('550 no trace')
        receive(b'example data\n')

    def storbinary(self, *args):
        raise AssertionError('snapshot must not write remotely')

    def rename(self, *args):
        raise AssertionError('snapshot must not rename remotely')

    def delete(self, *args):
        raise AssertionError('snapshot must not delete remotely')


class ConsoleSnapshotTest(unittest.TestCase):
    def snapshot(self, missing=False):
        with tempfile.TemporaryDirectory(dir=TEMP) as d:
            ftp = ReadOnlyFTP(missing)
            with patch.object(console, 'ROOT', pathlib.Path(d)), patch.object(console, 'connect', return_value=ftp), \
                    patch.object(console, 'opt', return_value='snapshot'), contextlib.redirect_stdout(io.StringIO()):
                console.cmd_snapshot()
            out = pathlib.Path(d) / 'snapshot'
            return json.loads((out / 'snapshot.json').read_text()), ftp.reads, {p.name for p in out.iterdir()}

    def test_reads_only_allowed_files_and_records_checksums(self):
        manifest, reads, names = self.snapshot()
        self.assertEqual(len(reads), 3)
        self.assertEqual(names, {'plugins.ini', 'trace-CUSA05571.txt', 'config-CUSA05571.ini', 'snapshot.json'})
        self.assertEqual(len(manifest['files']), 3)
        self.assertFalse(manifest['errors'])
        self.assertEqual(len(manifest['files']['plugins.ini']['sha256']), 64)

    def test_missing_trace_is_reported_without_becoming_an_empty_trace(self):
        manifest, reads, names = self.snapshot(missing=True)
        self.assertIn('trace-CUSA05571.txt', manifest['errors'])
        self.assertNotIn('trace-CUSA05571.txt', names)
        self.assertNotIn('trace-CUSA05571.txt', manifest['files'])


if __name__ == '__main__':
    unittest.main()
