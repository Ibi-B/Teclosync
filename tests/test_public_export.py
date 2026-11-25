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


def _record(source_id, name, url, index, logo=''):
    return {
        'source_id': source_id,
        'source_record_index': index,
        'name': name,
        'tvg_id': '',
        'tvg_name': name,
        'tvg_logo': logo,
        'group_title': 'News',
        'stream_url': url,
    }


class PublicExportTests(unittest.TestCase):
    def _write(self, root, records, run_id='20261125T120000Z'):
        return write_public_catalogue(
            FakeDatabase(records),
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
            run_id,
            generated_at='2026-11-25T12:00:00+00:00',
        )

    def test_writes_immutable_release_path_and_sanitizes_all_exported_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [
                _record('iptv-org-sweden', 'SVT 1',
                        'https://streams.example.com/svt1.m3u8', 1,
                        'https://images.example.com/svt.png'),
                _record('iptv-org-sweden', 'Signed logo',
                        'https://streams.example.com/news.m3u8', 2,
                        'https://images.example.com/logo.png?X-Amz-Signature=signed'),
                _record('iptv-org-sweden', 'Path credential logo',
                        'https://streams.example.com/weather.m3u8', 3,
                        'https://images.example.com/live/user/pass/77.png'),
                _record('iptv-org-sweden', 'Path credentials',
                        'https://streams.example.com/live/user123/pass456/77.ts', 4),
                _record('iptv-org-sweden', 'Loopback',
                        'http://127.0.0.1/live.m3u8', 4),
                _record('iptv-org-sweden', 'Private network',
                        'http://192.168.1.8/stream.m3u8', 5),
                _record('iptv-org-sweden', 'Local host',
                        'http://router.local/stream.m3u8', 6),
                _record('iptv-org-sweden', 'Signed stream',
                        'https://streams.example.com/live?access_token=secret', 7),
            ]
            catalogue = self._write(root, records)
            playlist = catalogue['playlists'][0]

            self.assertEqual(catalogue['playlistCount'], 1)
            self.assertEqual(catalogue['channelCount'], 3)
            self.assertEqual(
                playlist['path'],
                'releases/20261125T120000Z/playlists/iptv-org-sweden.m3u8',
            )
            m3u = (
                root / 'public' / 'iptv' / playlist['path']
            ).read_text(encoding='utf-8')
            self.assertIn('SVT 1', m3u)
            self.assertIn('Signed logo', m3u)
            self.assertIn('Path credential logo', m3u)
            self.assertIn('Path credential logo', m3u)
            self.assertIn('https://streams.example.com/news.m3u8', m3u)
            self.assertNotIn('X-Amz-Signature', m3u)
            self.assertNotIn('/live/user/pass/', m3u)
            self.assertNotIn('images.example/logo.png?', m3u)
            self.assertNotIn('user123', m3u)
            self.assertNotIn('127.0.0.1', m3u)
            self.assertNotIn('192.168.1.8', m3u)
            self.assertNotIn('router.local', m3u)
            self.assertNotIn('access_token', m3u)
            self.assertNotIn('secret', m3u)

    def test_count_is_sum_of_published_safe_rows_and_empty_export_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalogue = self._write(
                root,
                [
                    _record('iptv-org-sweden', 'One',
                            'https://streams.example.com/one.m3u8', 1),
                    _record('iptv-org-sweden', 'Two',
                            'https://streams.example.com/two.m3u8', 2),
                ],
            )
            self.assertEqual(catalogue['channelCount'], 2)

            with self.assertRaisesRegex(ValueError, 'no_public_playlists_to_publish'):
                self._write(root / 'empty', [])

    def test_new_publication_keeps_previous_release_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._write(
                root,
                [_record('iptv-org-sweden', 'Old SVT',
                         'https://streams.example.com/old.m3u8', 1)],
                run_id='20261125T120000Z',
            )
            old_path = root / 'public' / 'iptv' / first['playlists'][0]['path']
            old_bytes = old_path.read_bytes()

            second = self._write(
                root,
                [_record('iptv-org-sweden', 'New SVT',
                         'https://streams.example.com/new.m3u8', 1)],
                run_id='20261125T130000Z',
            )
            new_path = root / 'public' / 'iptv' / second['playlists'][0]['path']

            self.assertNotEqual(first['playlists'][0]['path'],
                                second['playlists'][0]['path'])
            self.assertEqual(old_path.read_bytes(), old_bytes)
            self.assertIn(b'Old SVT', old_bytes)
            self.assertIn(b'New SVT', new_path.read_bytes())

    def test_rejects_unsafe_release_id(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'invalid_public_release_id'):
                self._write(
                    Path(directory),
                    [_record('iptv-org-sweden', 'One',
                             'https://streams.example.com/one.m3u8', 1)],
                    run_id='../current',
                )


if __name__ == '__main__':
    unittest.main()
