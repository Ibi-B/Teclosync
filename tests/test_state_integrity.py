import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from teclosync.build import build_artifacts
from teclosync.external_state import SnapshotStore
from teclosync.refresh import refresh_source
from teclosync.staging import StagingDatabase


class FakeClient:
    class exceptions:
        class ClientError(Exception):
            pass

    def __init__(self):
        self.get_object = MagicMock()


class SourceIsolationTest(unittest.TestCase):
    def test_unexpected_parser_error_is_isolated(self):
        database = MagicMock()
        source = {
            'id': 'bad-source',
            'format': 'm3u',
            'url': 'https://example.test/bad.m3u',
            'min_records': 1,
            'timeout_seconds': 1,
        }
        with patch('teclosync.refresh.fetch_text', side_effect=ValueError('malformed playlist')):
            result = asyncio.run(refresh_source(MagicMock(), database, source, 'run-1', 'started'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'ValueError')
        database.fail_source_run.assert_called_once_with('bad-source', 'run-1')


class RetainedDataTest(unittest.TestCase):
    def _database_with_channels(self, root):
        database = StagingDatabase(Path(root) / 'state.db')
        source = {
            'id': 'retained-source',
            'name': 'Retained Source',
            'category': 'live_tv',
            'format': 'm3u',
            'url': 'https://example.test/retained.m3u',
            'min_records': 1,
            'timeout_seconds': 1,
        }
        database.register_source(source)
        database.begin_source_run('retained-source', 'old-run', '2026-01-01T00:00:00+00:00')
        record = {
            'channel_key': 'retained-key',
            'source_id': 'retained-source',
            'source_run_id': 'old-run',
            'source_record_index': 1,
            'name': 'Retained Channel',
            'tvg_id': '',
            'tvg_name': '',
            'tvg_logo': '',
            'group_title': '',
            'country': 'se',
            'language': 'sv',
            'stream_url': 'https://example.test/retained-stream',
            'original_stream_url': 'https://example.test/retained-stream',
            'stream_type': 'hls',
            'quality': 'unknown',
            'radio': False,
            'provenance': {'source': 'retained-source'},
        }
        database.replace_source_channels('retained-source', 'old-run', [record])
        return database

    def test_retained_channels_are_published_and_labelled(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self._database_with_channels(directory)
            statuses = [{
                'source_id': 'retained-source',
                'status': 'failed',
                'data_state': 'retained_last_good',
                'channel_count': 1,
                'reason': 'http_503',
            }]
            manifest = build_artifacts(
                database,
                Path(directory) / 'artifacts',
                'new-run',
                source_status=statuses,
            )
            self.assertEqual(manifest['sources'][0]['data_state'], 'retained_last_good')
            self.assertEqual(manifest['artifacts'][-1]['record_count'], 1)


class StatePointerTest(unittest.TestCase):
    def test_previous_pointer_can_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(
                'bucket',
                'https://example.test',
                'access',
                'secret',
                prefix='telclosync/state',
            )
            store.client = FakeClient()
            body = json.dumps({'run_id': 'old'}).encode()
            store.client.get_object.return_value = {'Body': MagicMock(read=lambda: body)}
            previous = store.read_pointer('current.json')
            self.assertEqual(previous, {'run_id': 'old'})

    def test_missing_state_database_returns_false(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(
                'bucket',
                'https://example.test',
                'access',
                'secret',
                prefix='telclosync/state',
            )
            store.client = FakeClient()
            error = FakeClient.exceptions.ClientError()
            error.response = {'Error': {'Code': 'NoSuchKey'}}
            store.client.get_object.side_effect = error
            self.assertFalse(store.download_state_database(Path(directory) / 'state.db'))


if __name__ == '__main__':
    unittest.main()
