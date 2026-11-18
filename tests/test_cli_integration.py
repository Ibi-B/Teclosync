import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from teclosync.__main__ import run_refresh
from teclosync.staging import StagingDatabase


class CliIntegrationTest(unittest.TestCase):
    def _record(self, index):
        return {
            'channel_key': f'key-{index}',
            'source_id': 'test-source',
            'source_run_id': '20260101T000000Z',
            'source_record_index': index,
            'name': f'Test Channel {index}',
            'tvg_id': '',
            'tvg_name': '',
            'tvg_logo': '',
            'group_title': '',
            'country': 'se',
            'language': 'sv',
            'stream_url': f'https://example.test/stream-{index}',
            'original_stream_url': f'https://example.test/stream-{index}',
            'stream_type': 'hls',
            'quality': 'unknown',
            'radio': False,
            'provenance': {'source': 'test-source'},
        }

    def _environment(self):
        return {
            'TECLOSYNC_S3_BUCKET': 'test-bucket',
            'TECLOSYNC_S3_ENDPOINT': 'https://example.test',
            'TECLOSYNC_S3_ACCESS_KEY_ID': 'test-access',
            'TECLOSYNC_S3_SECRET_ACCESS_KEY': 'test-secret',
        }

    def test_refresh_builds_validates_and_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'sources.json'
            database_path = root / 'state' / 'test.db'
            output = root / 'state' / 'artifacts'
            diagnostics = root / 'state' / 'diagnostics'
            source = {
                'id': 'test-source',
                'name': 'Test Source',
                'category': 'live_tv',
                'format': 'm3u',
                'url': 'https://example.test/playlist.m3u',
                'enabled': True,
                'status': 'validated',
                'priority': 10,
                'min_records': 1,
                'timeout_seconds': 5,
            }
            config.write_text(
                json.dumps({'version': 1, 'sources': [source]}),
                encoding='utf-8',
            )
            database = StagingDatabase(database_path)
            database.register_source(source)
            database.begin_source_run(
                'test-source',
                '20260101T000000Z',
                '2026-01-01T00:00:00+00:00',
            )
            database.replace_source_channels(
                'test-source',
                '20260101T000000Z',
                [self._record(index) for index in range(12)],
            )
            refresh_result = {
                'run_id': '20260101T000000Z',
                'started_at': '2026-01-01T00:00:00+00:00',
                'sources': [{
                    'source_id': 'test-source',
                    'status': 'healthy',
                    'record_count': 12,
                    'source_record_count': 12,
                    'duplicate_count': 0,
                }],
            }
            args = Namespace(
                config=str(config),
                database=str(database_path),
                output=str(output),
                diagnostics=str(diagnostics),
                publish=True,
            )
            with (
                patch.dict(os.environ, self._environment()),
                patch('teclosync.__main__.refresh_sources', return_value=refresh_result),
                patch('teclosync.__main__.SnapshotStore') as store_class,
            ):
                store = store_class.return_value
                store.download_state_database.return_value = False
                store.publish_release.return_value = (
                    'telclosync/state/releases/20260101T000000Z/manifest.json'
                )
                store.read_pointer.return_value = None
                store.upload_state_database.return_value = (
                    'telclosync/state/state/teclo-sync.db'
                )
                status = run_refresh(args)

            self.assertEqual(status, 0)
            self.assertTrue((output / 'manifest.json').exists())
            self.assertTrue((output / 'live-global.m3u8').exists())
            self.assertTrue((output / 'tv-catalog.jsonl.zst').exists())
            diagnostic_files = list(diagnostics.glob('refresh-*.json'))
            self.assertEqual(len(diagnostic_files), 1)
            payload = json.loads(
                diagnostic_files[0].read_text(encoding='utf-8')
            )
            self.assertTrue(payload['publish_succeeded'])
            self.assertTrue(payload['validation']['publication_allowed'])
            self.assertEqual(payload['validation']['total_channels'], 12)
            self.assertEqual(payload['artifacts'][-1]['record_count'], 12)
            store.publish_release.assert_called_once()
            store.upload_state_database.assert_called_once()
            store.write_pointer.assert_called_once()

    def test_validation_failure_blocks_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'sources.json'
            database_path = root / 'state' / 'test.db'
            output = root / 'state' / 'artifacts'
            diagnostics = root / 'state' / 'diagnostics'
            source = {
                'id': 'test-source',
                'name': 'Test Source',
                'category': 'live_tv',
                'format': 'm3u',
                'url': 'https://example.test/playlist.m3u',
                'enabled': True,
                'status': 'validated',
                'priority': 10,
                'min_records': 10,
                'timeout_seconds': 5,
            }
            config.write_text(
                json.dumps({'version': 1, 'sources': [source]}),
                encoding='utf-8',
            )
            database = StagingDatabase(database_path)
            database.register_source(source)
            database.begin_source_run(
                'test-source',
                '20260101T000000Z',
                '2026-01-01T00:00:00+00:00',
            )
            database.replace_source_channels(
                'test-source',
                '20260101T000000Z',
                [self._record(index) for index in range(3)],
            )
            refresh_result = {
                'run_id': '20260101T000000Z',
                'started_at': '2026-01-01T00:00:00+00:00',
                'sources': [{
                    'source_id': 'test-source',
                    'status': 'healthy',
                    'record_count': 3,
                    'source_record_count': 3,
                    'duplicate_count': 0,
                }],
            }
            args = Namespace(
                config=str(config),
                database=str(database_path),
                output=str(output),
                diagnostics=str(diagnostics),
                publish=True,
            )
            with (
                patch.dict(os.environ, self._environment()),
                patch('teclosync.__main__.refresh_sources', return_value=refresh_result),
                patch('teclosync.__main__.SnapshotStore') as store_class,
            ):
                store_class.return_value.download_state_database.return_value = False
                status = run_refresh(args)

            self.assertEqual(status, 1)
            store = store_class.return_value
            store.upload_state_database.assert_not_called()
            store.publish_release.assert_not_called()
            store.write_pointer.assert_not_called()
            self.assertFalse((output / 'manifest.json').exists())
            diagnostic_files = list(diagnostics.glob('refresh-*.json'))
            self.assertEqual(len(diagnostic_files), 1)
            payload = json.loads(
                diagnostic_files[0].read_text(encoding='utf-8')
            )
            self.assertFalse(payload['publish_succeeded'])
            self.assertIn('global_source_collapse', payload['error'])


if __name__ == '__main__':
    unittest.main()
