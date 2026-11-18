import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from teclosync.refresh import ingest_m3u, refresh_source, refresh_sources
from teclosync.staging import StagingDatabase


class RefreshTests(unittest.TestCase):
    def source(self):
        return {
            'id': 'test-source',
            'name': 'Test',
            'category': 'live_tv',
            'format': 'm3u',
            'url': 'https://example.org/feed.m3u',
            'min_records': 1,
            'timeout_seconds': 5,
        }

    def run_refresh_fixture(self, sources, m3u):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        database = StagingDatabase(Path(temporary_directory.name) / 'state.db')
        for source in sources:
            database.register_source(source)
            database.begin_source_run(source['id'], 'run-1', '2026-01-01T00:00:00Z')

        async def fake_fetch(session, source):
            return m3u

        with patch('teclosync.refresh.fetch_text', side_effect=fake_fetch):
            result = asyncio.run(
                refresh_sources(database, sources, run_id='run-1')
            )
        return database, result

    def test_successful_refresh_stages_channels(self):
        m3u = '#EXTM3U\n#EXTINF:-1,Test\nhttps://example.org/stream\n'
        database, result = self.run_refresh_fixture([self.source()], m3u)
        self.assertEqual(result['sources'][0]['status'], 'healthy')
        self.assertEqual(database.source_channel_count('test-source'), 1)

    def test_exact_duplicates_are_reported_without_losing_provenance(self):
        m3u = (
            '#EXTM3U\n'
            '#EXTINF:-1 tvg-id="test.id",Same Channel\n'
            'https://example.org/stream\n'
            '#EXTINF:-1 tvg-id="test.id",Same Channel\n'
            'https://example.org/stream\n'
        )
        database, result = self.run_refresh_fixture([self.source()], m3u)
        source_result = result['sources'][0]

        self.assertEqual(source_result['status'], 'healthy')
        self.assertEqual(source_result['record_count'], 1)
        self.assertEqual(source_result['source_record_count'], 2)
        self.assertEqual(source_result['duplicate_count'], 1)
        self.assertEqual(database.source_channel_count('test-source'), 1)
        duplicate = list(database.iter_channel_duplicates('test-source'))[0]
        self.assertEqual(duplicate['provenance']['source_record_index'], 2)

    def test_duplicate_rows_do_not_replace_last_good_when_unique_count_is_low(self):
        source = self.source()
        source['min_records'] = 2
        good_m3u = (
            '#EXTM3U\n'
            '#EXTINF:-1,Known One\nhttps://example.org/known-one\n'
            '#EXTINF:-1,Known Two\nhttps://example.org/known-two\n'
        )
        duplicate_m3u = (
            '#EXTM3U\n'
            '#EXTINF:-1 tvg-id="same.id",Repeated\nhttps://example.org/repeated\n'
            '#EXTINF:-1 tvg-id="same.id",Repeated\nhttps://example.org/repeated\n'
        )

        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            database.register_source(source)
            database.begin_source_run('test-source', 'run-1', '2026-01-01T00:00:00Z')
            good_records = ingest_m3u(source, source['url'], good_m3u, 'run-1')
            database.replace_source_channels('test-source', 'run-1', good_records)
            database.begin_source_run('test-source', 'run-2', '2026-01-02T00:00:00Z')

            async def fake_fetch(session, source):
                return duplicate_m3u

            with patch('teclosync.refresh.fetch_text', side_effect=fake_fetch):
                result = asyncio.run(
                    refresh_source(
                        MagicMock(),
                        database,
                        source,
                        'run-2',
                        '2026-01-02T00:00:00Z',
                    )
                )

            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['reason'], 'record_count_1_below_minimum_2')
            self.assertEqual(result['source_record_count'], 2)
            self.assertEqual(result['duplicate_count'], 1)
            self.assertEqual(database.source_channel_count('test-source'), 2)
            self.assertEqual(
                {record['stream_url'] for record in database.iter_channels()},
                {
                    'https://example.org/known-one',
                    'https://example.org/known-two',
                },
            )

    def test_failed_refresh_preserves_channels(self):
        source = self.source()
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            database.register_source(source)
            database.begin_source_run('test-source', 'run-1', '2026-01-01T00:00:00Z')
            record = {
                'channel_key': 'known-good',
                'source_id': 'test-source',
                'source_run_id': 'run-1',
                'source_record_index': 1,
                'name': 'Known good',
                'tvg_id': '',
                'tvg_name': '',
                'tvg_logo': '',
                'group_title': '',
                'country': '',
                'language': '',
                'stream_url': 'https://example.org/good',
                'original_stream_url': 'https://example.org/good',
                'stream_type': 'direct',
                'quality': 'unknown',
                'radio': False,
                'provenance': {'source': 'test-source'},
            }
            database.replace_source_channels('test-source', 'run-1', [record])
            database.begin_source_run('test-source', 'run-2', '2026-01-02T00:00:00Z')

            async def fail_fetch(session, source):
                raise RuntimeError('source temporarily unavailable')

            with patch('teclosync.refresh.fetch_text', side_effect=fail_fetch):
                result = asyncio.run(
                    refresh_sources(database, [source], run_id='run-2')
                )
            self.assertEqual(result['sources'][0]['status'], 'failed')
            self.assertEqual(database.source_channel_count('test-source'), 1)

    def test_empty_source_below_threshold_fails(self):
        database, result = self.run_refresh_fixture([self.source()], '#EXTM3U\n')
        self.assertEqual(result['sources'][0]['status'], 'failed')
        self.assertEqual(database.source_channel_count('test-source'), 0)

    def test_parser_error_contains_actionable_detail(self):
        database = MagicMock()
        source = self.source()
        malformed = '#EXTM3U\n#EXTINF:-1,Incomplete entry\n'

        async def fake_fetch(session, source):
            return malformed

        with patch('teclosync.refresh.fetch_text', side_effect=fake_fetch):
            result = asyncio.run(
                refresh_source(MagicMock(), database, source, 'run-1', 'started')
            )

        self.assertEqual(result['status'], 'failed')
        self.assertEqual(
            result['reason'],
            'ValueError: EXTINF without following stream URL',
        )
        database.fail_source_run.assert_called_once_with('test-source', 'run-1')

    def test_sqlite_error_contains_constraint_detail(self):
        database = MagicMock()
        database.summarize_source_channels.return_value = {
            'record_count': 1,
            'source_record_count': 1,
            'duplicate_count': 0,
        }
        database.replace_source_channels.side_effect = sqlite3.IntegrityError(
            'UNIQUE constraint failed: channels.source_id, channels.channel_key'
        )
        source = self.source()
        m3u = '#EXTM3U\n#EXTINF:-1,Test\nhttps://example.org/stream\n'

        async def fake_fetch(session, source):
            return m3u

        with patch('teclosync.refresh.fetch_text', side_effect=fake_fetch):
            result = asyncio.run(
                refresh_source(MagicMock(), database, source, 'run-1', 'started')
            )

        self.assertEqual(result['status'], 'failed')
        self.assertEqual(
            result['reason'],
            'IntegrityError: UNIQUE constraint failed: channels.source_id, channels.channel_key',
        )
        database.fail_source_run.assert_called_once_with('test-source', 'run-1')


if __name__ == '__main__':
    unittest.main()
