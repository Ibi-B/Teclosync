import json
import tempfile
import unittest
from pathlib import Path

from teclosync.public_export import write_public_catalogue


class FakeDatabase:
    def __init__(self, records):
        self.records = records

    def iter_channels(self):
        yield from self.records


def _record(source_id, name, url, index):
    return {
        'source_id': source_id,
        'source_record_index': index,
        'name': name,
        'tvg_id': '',
        'tvg_name': name,
        'tvg_logo': '',
        'group_title': 'News',
        'stream_url': url,
    }


class PublicExportTests(unittest.TestCase):
    def test_writes_source_playlist_and_safe_catalogue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = FakeDatabase([
                _record('iptv-org-sweden', 'SVT 1', 'https://streams.example/svt1.m3u8', 1),
                _record('iptv-org-sweden', 'Private', 'https://user:password@private.example/live', 2),
                _record('iptv-org-sweden', 'Tokenized', 'https://streams.example/live?access_token=secret', 3),
            ])
            catalogue = write_public_catalogue(
                database,
                [{
                    'id': 'iptv-org-sweden',
                    'name': 'IPTV-Org Sweden',
                    'category': 'live_tv',
                    'region': 'se',
                }],
                [{
                    'source_id': 'iptv-org-sweden',
                    'status': 'failed',
                    'data_state': 'retained_last_good',
                }],
                root / 'public' / 'iptv',
                '20261125T120000Z',
                generated_at='2026-11-25T12:00:00+00:00',
            )

            self.assertEqual(catalogue['schemaVersion'], 1)
            self.assertEqual(catalogue['releaseId'], '20261125T120000Z')
            self.assertEqual(catalogue['playlistCount'], 1)
            self.assertEqual(catalogue['channelCount'], 1)
            playlist = catalogue['playlists'][0]
            self.assertEqual(playlist['dataState'], 'retained_last_good')
            self.assertEqual(playlist['path'], 'playlists/iptv-org-sweden.m3u8')

            index = json.loads((root / 'public' / 'iptv' / 'catalogue.json').read_text())
            self.assertEqual(index, catalogue)
            m3u = (root / 'public' / 'iptv' / playlist['path']).read_text()
            self.assertIn('SVT 1', m3u)
            self.assertNotIn('private.example', m3u)
            self.assertNotIn('access_token', m3u)
            self.assertNotIn('secret', m3u)

    def test_refuses_to_replace_index_with_empty_catalogue(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaisesRegex(ValueError, 'no_public_playlists_to_publish'):
                write_public_catalogue(
                    FakeDatabase([]),
                    [{'id': 'iptv-org-sweden', 'name': 'IPTV-Org Sweden'}],
                    [],
                    output,
                    '20261125T120000Z',
                )
            self.assertFalse((output / 'catalogue.json').exists())


if __name__ == '__main__':
    unittest.main()
