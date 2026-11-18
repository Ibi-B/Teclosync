import tempfile
import unittest
from pathlib import Path
from teclosync.build import build_artifacts
from teclosync.staging import StagingDatabase

class BuildTests(unittest.TestCase):
    def source(self):
        return {'id': 'test-source', 'name': 'Test', 'category': 'live_tv', 'format': 'm3u', 'url': 'https://example.org/feed.m3u', 'min_records': 1, 'timeout_seconds': 5}

    def record(self, index, country='se'):
        return {'channel_key': f'key-{index}', 'source_id': 'test-source', 'source_run_id': 'run-1', 'source_record_index': index, 'name': f'Channel {index}', 'tvg_id': '', 'tvg_name': '', 'tvg_logo': '', 'group_title': '', 'country': country, 'language': '', 'stream_url': f'https://example.org/{index}', 'original_stream_url': f'https://example.org/{index}', 'stream_type': 'direct', 'quality': 'unknown', 'radio': False, 'provenance': {}}

    def test_builds_manifest_and_catalogue(self):
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            database.register_source(self.source())
            database.begin_source_run('test-source', 'run-1', '2026-01-01T00:00:00Z')
            database.replace_source_channels('test-source', 'run-1', [self.record(1), self.record(2, country='us')])
            output = Path(directory) / 'dist'
            manifest = build_artifacts(database, output, 'run-1')
            ids = {artifact['id'] for artifact in manifest['artifacts']}
            self.assertIn('live-nordic', ids)
            self.assertIn('live-global', ids)
            self.assertIn('tv-catalog', ids)
            self.assertTrue((output / 'manifest.json').exists())
            self.assertTrue((output / 'tv-catalog.jsonl.zst').exists())

    def test_empty_catalogue_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            database.register_source(self.source())
            with self.assertRaises(Exception):
                build_artifacts(database, Path(directory) / 'dist', 'run-1')

if __name__ == '__main__':
    unittest.main()
