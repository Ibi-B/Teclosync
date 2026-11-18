import tempfile
import unittest
from pathlib import Path
from teclosync.staging import StagingDatabase

class StagingTests(unittest.TestCase):
    def source(self):
        return {'id': 'test-source', 'name': 'Test Source', 'category': 'live_tv', 'format': 'm3u', 'url': 'https://example.org/feed.m3u', 'min_records': 1, 'timeout_seconds': 30}

    def record(self, index=1):
        return {'channel_key': f'key-{index}', 'source_id': 'test-source', 'source_run_id': 'run-1', 'source_record_index': index, 'name': f'Channel {index}', 'tvg_id': '', 'tvg_name': '', 'tvg_logo': '', 'group_title': '', 'country': '', 'language': '', 'stream_url': f'https://example.org/{index}', 'original_stream_url': f'https://example.org/{index}', 'stream_type': 'direct', 'quality': 'unknown', 'radio': False, 'provenance': {'source': 'test-source', 'source_url': 'https://example.org/feed.m3u', 'retrieved_at': '2026-01-01T00:00:00Z', 'source_record_index': index, 'content_hash': f'key-{index}'}}

    def test_insert_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            database.register_source(self.source())
            database.begin_source_run('test-source', 'run-1', '2026-01-01T00:00:00Z')
            database.replace_source_channels('test-source', 'run-1', [self.record(1), self.record(2)])
            self.assertEqual(database.source_channel_count('test-source'), 2)
            self.assertEqual(database.channel_count(), 2)

    def test_source_replacement_preserves_other_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            source = self.source()
            database.register_source(source)
            database.begin_source_run('test-source', 'run-1', '2026-01-01T00:00:00Z')
            database.replace_source_channels('test-source', 'run-1', [self.record(1)])
            other = dict(source, id='other-source')
            database.register_source(other)
            database.begin_source_run('other-source', 'run-1', '2026-01-01T00:00:00Z')
            other_record = dict(self.record(1), source_id='other-source', channel_key='other-key')
            database.replace_source_channels('other-source', 'run-1', [other_record])
            self.assertEqual(database.source_channel_count('test-source'), 1)
            self.assertEqual(database.source_channel_count('other-source'), 1)
            self.assertEqual(database.channel_count(), 2)

    def test_failed_run_does_not_replace_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            database.register_source(self.source())
            database.begin_source_run('test-source', 'run-1', '2026-01-01T00:00:00Z')
            database.replace_source_channels('test-source', 'run-1', [self.record(1)])
            database.begin_source_run('test-source', 'run-2', '2026-01-02T00:00:00Z')
            database.fail_source_run('test-source', 'run-2')
            self.assertEqual(database.source_channel_count('test-source'), 1)

if __name__ == '__main__':
    unittest.main()
