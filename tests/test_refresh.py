import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from teclosync.refresh import refresh_sources
from teclosync.staging import StagingDatabase

class RefreshTests(unittest.TestCase):
    def source(self):
        return {'id': 'test-source', 'name': 'Test', 'category': 'live_tv', 'format': 'm3u', 'url': 'https://example.org/feed.m3u', 'min_records': 1, 'timeout_seconds': 5}

    def run(self, sources):
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            for source in sources:
                database.register_source(source)
            result = asyncio.run(refresh_sources(database, sources, run_id='run-1'))
            return database, result

    def test_successful_refresh_stages_channels(self):
        m3u = '#EXTM3U\n#EXTINF:-1,Test\nhttps://example.org/stream\n'
        async def fake_fetch(session, source):
            return m3u
        with patch('teclosync.refresh.fetch_text', fake_fetch):
            database, result = self.run([self.source()])
        self.assertEqual(result['sources'][0]['status'], 'healthy')
        self.assertEqual(database.source_channel_count('test-source'), 1)

    def test_failed_refresh_preserves_channels(self):
        async def fake_fetch(session, source):
            raise SourceRefreshError(source['id'], 'network_error')
        from teclosync.refresh import SourceRefreshError
        with patch('teclosync.refresh.fetch_text', fake_fetch):
            database, result = self.run([self.source()])
        self.assertEqual(result['sources'][0]['status'], 'failed')
        self.assertEqual(database.source_channel_count('test-source'), 0)

    def test_empty_source_below_threshold_fails(self):
        async def fake_fetch(session, source):
            return '#EXTM3U\n'
        with patch('teclosync.refresh.fetch_text', fake_fetch):
            database, result = self.run([self.source()])
        self.assertEqual(result['sources'][0]['status'], 'failed')
        self.assertEqual(database.source_channel_count('test-source'), 0)

if __name__ == '__main__':
    unittest.main()
