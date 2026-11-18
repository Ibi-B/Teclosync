import tempfile
import unittest
from pathlib import Path
from teclosync.validation import validate_refresh, ValidationError
from teclosync.staging import StagingDatabase

class ValidationTests(unittest.TestCase):
    def source(self, source_id='test-source', minimum=1):
        return {'id': source_id, 'name': 'Test', 'category': 'live_tv', 'format': 'm3u', 'url': 'https://example.org/feed.m3u', 'min_records': minimum, 'timeout_seconds': 5}

    def database(self, directory, source_id, count):
        database = StagingDatabase(Path(directory) / 'state.db')
        source = self.source(source_id)
        database.register_source(source)
        database.begin_source_run(source_id, 'run-1', '2026-01-01T00:00:00Z')
        records = [{'channel_key': f'{source_id}-{index}', 'source_id': source_id, 'source_run_id': 'run-1', 'source_record_index': index, 'name': f'Channel {index}', 'tvg_id': '', 'tvg_name': '', 'tvg_logo': '', 'group_title': '', 'country': '', 'language': '', 'stream_url': f'https://example.org/{source_id}/{index}', 'original_stream_url': f'https://example.org/{source_id}/{index}', 'stream_type': 'direct', 'quality': 'unknown', 'radio': False, 'provenance': {}} for index in range(1, count + 1)]
        database.replace_source_channels(source_id, 'run-1', records)
        return database

    def result(self, source_id, status, count=0, reason=''):
        return {'run_id': 'run-1', 'started_at': '2026-01-01T00:00:00Z', 'sources': [{'source_id': source_id, 'status': status, 'record_count': count, 'reason': reason}]}

    def test_healthy_refresh_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.database(directory, 'test-source', 2)
            report = validate_refresh(database, [self.source()], self.result('test-source', 'healthy', 2))
            self.assertTrue(report['publication_allowed'])
            self.assertEqual(report['total_channels'], 2)

    def test_below_threshold_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.database(directory, 'test-source', 0)
            with self.assertRaises(ValidationError):
                validate_refresh(database, [self.source()], self.result('test-source', 'healthy', 0))

    def test_failed_source_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.database(directory, 'test-source', 2)
            report = validate_refresh(database, [self.source()], self.result('test-source', 'failed', 0, 'http_403'))
            self.assertEqual(report['failed_sources'][0]['reason'], 'http_403')

if __name__ == '__main__':
    unittest.main()
