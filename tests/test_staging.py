import tempfile
import unittest
from pathlib import Path

from teclosync.staging import StagingDatabase


class StagingTests(unittest.TestCase):
    def source(self):
        return {
            'id': 'test-source',
            'name': 'Test Source',
            'category': 'live_tv',
            'format': 'm3u',
            'url': 'https://example.org/feed.m3u',
            'min_records': 1,
            'timeout_seconds': 30,
        }

    def record(self, index=1):
        return {
            'channel_key': f'key-{index}',
            'source_id': 'test-source',
            'source_run_id': 'run-1',
            'source_record_index': index,
            'name': f'Channel {index}',
            'tvg_id': '',
            'tvg_name': '',
            'tvg_logo': '',
            'group_title': '',
            'country': '',
            'language': '',
            'stream_url': f'https://example.org/{index}',
            'original_stream_url': f'https://example.org/{index}',
            'stream_type': 'direct',
            'quality': 'unknown',
            'radio': False,
            'provenance': {
                'source': 'test-source',
                'source_url': 'https://example.org/feed.m3u',
                'retrieved_at': '2026-01-01T00:00:00Z',
                'source_record_index': index,
                'content_hash': f'key-{index}',
            },
        }

    def setup_database(self, directory):
        database = StagingDatabase(Path(directory) / 'state.db')
        database.register_source(self.source())
        database.begin_source_run('test-source', 'run-1', '2026-01-01T00:00:00Z')
        return database

    def test_insert_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.setup_database(directory)
            database.replace_source_channels(
                'test-source',
                'run-1',
                [self.record(1), self.record(2)],
            )
            self.assertEqual(database.source_channel_count('test-source'), 2)
            self.assertEqual(database.channel_count(), 2)

    def test_source_replacement_preserves_other_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.setup_database(directory)
            database.replace_source_channels('test-source', 'run-1', [self.record(1)])
            other = dict(self.source(), id='other-source')
            database.register_source(other)
            database.begin_source_run('other-source', 'run-1', '2026-01-01T00:00:00Z')
            other_record = dict(
                self.record(1),
                source_id='other-source',
                channel_key='other-key',
                provenance={'source': 'other-source'},
            )
            database.replace_source_channels('other-source', 'run-1', [other_record])
            self.assertEqual(database.source_channel_count('test-source'), 1)
            self.assertEqual(database.source_channel_count('other-source'), 1)
            self.assertEqual(database.channel_count(), 2)

    def test_failed_run_does_not_replace_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.setup_database(directory)
            database.replace_source_channels('test-source', 'run-1', [self.record(1)])
            database.begin_source_run('test-source', 'run-2', '2026-01-02T00:00:00Z')
            database.fail_source_run('test-source', 'run-2')
            self.assertEqual(database.source_channel_count('test-source'), 1)

    def test_exact_duplicate_is_kept_as_a_provenance_record(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.setup_database(directory)
            first = self.record(1)
            duplicate = dict(
                first,
                source_record_index=2,
                provenance={**first['provenance'], 'source_record_index': 2},
            )

            summary = database.replace_source_channels(
                'test-source',
                'run-1',
                [first, duplicate],
            )

            self.assertEqual(summary, {
                'record_count': 1,
                'source_record_count': 2,
                'duplicate_count': 1,
            })
            self.assertEqual(database.source_channel_count('test-source'), 1)
            self.assertEqual(database.source_duplicate_count('test-source'), 1)
            stored = list(database.iter_channels())
            self.assertEqual(stored[0]['source_record_index'], 1)
            duplicates = list(database.iter_channel_duplicates('test-source'))
            self.assertEqual(duplicates[0]['reason'], 'exact_duplicate')
            self.assertEqual(duplicates[0]['record']['source_record_index'], 2)
            self.assertEqual(duplicates[0]['provenance']['source_record_index'], 2)

    def test_distinct_stream_alternatives_with_same_name_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.setup_database(directory)
            first = self.record(1)
            alternative = dict(
                first,
                channel_key='key-alternative',
                source_record_index=2,
                stream_url='https://example.org/alternative',
                original_stream_url='https://example.org/alternative',
                provenance={**first['provenance'], 'source_record_index': 2},
            )

            summary = database.replace_source_channels(
                'test-source',
                'run-1',
                [first, alternative],
            )

            self.assertEqual(summary['record_count'], 2)
            self.assertEqual(database.source_channel_count('test-source'), 2)
            self.assertEqual(
                {record['stream_url'] for record in database.iter_channels()},
                {
                    'https://example.org/1',
                    'https://example.org/alternative',
                },
            )

    def test_conflicting_records_with_same_key_fail_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            database = self.setup_database(directory)
            original = self.record(1)
            database.replace_source_channels('test-source', 'run-1', [original])
            conflicting = dict(original, name='Unexpected name')

            with self.assertRaisesRegex(ValueError, 'channel_key collision'):
                database.replace_source_channels(
                    'test-source',
                    'run-1',
                    [original, conflicting],
                )

            self.assertEqual(database.source_channel_count('test-source'), 1)
            self.assertEqual(list(database.iter_channels())[0]['name'], 'Channel 1')


if __name__ == '__main__':
    unittest.main()
