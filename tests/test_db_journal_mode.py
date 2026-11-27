"""真实SQLite竞争下的WAL切换、总体时限和不可重试错误。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from angemedia_gateway.db.journal_mode import enable_wal


class JournalModeTest(unittest.TestCase):
    def test_real_busy_retries_until_competing_writer_releases_and_restores_busy_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fresh.db'
            with closing(sqlite3.connect(path, isolation_level=None)) as writer:
                writer.execute('CREATE TABLE fixture(value)'); writer.execute('BEGIN IMMEDIATE')
                contended = threading.Event()
                sleep = time.sleep
                def observed_sleep(seconds): contended.set(); sleep(seconds)
                def connect():
                    with closing(sqlite3.connect(path, isolation_level=None)) as reader:
                        enable_wal(reader)
                        return reader.execute('PRAGMA journal_mode').fetchone()[0], reader.execute('PRAGMA busy_timeout').fetchone()[0]
                with patch('angemedia_gateway.db.journal_mode.time.sleep', side_effect=observed_sleep), ThreadPoolExecutor(1) as pool:
                    future = pool.submit(connect)
                    try: self.assertTrue(contended.wait(2), '必须实际遇到BUSY')
                    finally: writer.execute('COMMIT')
                    self.assertEqual(future.result(timeout=3), ('wal', 5000))

    def test_persistent_busy_is_bounded_and_nonbusy_error_does_not_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fresh.db'
            with closing(sqlite3.connect(path, isolation_level=None)) as writer:
                writer.execute('CREATE TABLE fixture(value)'); writer.execute('BEGIN IMMEDIATE')
                with closing(sqlite3.connect(path, isolation_level=None)) as reader:
                    started = time.monotonic()
                    with self.assertRaises(sqlite3.OperationalError) as error: enable_wal(reader, timeout=0.1)
                    self.assertEqual(error.exception.sqlite_errorcode & 255, sqlite3.SQLITE_BUSY)
                    self.assertLess(time.monotonic() - started, 1)
                    self.assertEqual(reader.execute('PRAGMA busy_timeout').fetchone()[0], 5000)
                writer.execute('COMMIT')
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, isolation_level=None)) as readonly:
                with patch('angemedia_gateway.db.journal_mode.time.sleep') as sleep:
                    with self.assertRaises(sqlite3.OperationalError): enable_wal(readonly)
                    sleep.assert_not_called()
