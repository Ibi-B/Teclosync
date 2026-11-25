import unittest
from pathlib import Path

from teclosync.parsers.m3u import parse_m3u
from teclosync.normalizers.m3u import normalize_m3u_entry


class M3UTests(unittest.TestCase):
    def test_parses_entries_and_preserves_url(self):
        text = Path('tests/fixtures/sample.m3u').read_text(encoding='utf-8')
        entries = list(parse_m3u(text.splitlines(), 'test-source'))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].url, 'https://example.org/live.m3u8?token=keep')
        self.assertEqual(entries[0].attributes['tvg-id'], 'example.se')

    def test_normalizer_preserves_provenance(self):
        text = Path('tests/fixtures/sample.m3u').read_text(encoding='utf-8')
        entry = next(iter(parse_m3u(text.splitlines(), 'test-source')))
        source = {'id': 'test-source', 'region': 'se'}
        record = normalize_m3u_entry(
            entry,
            source,
            'https://example.org/feed.m3u',
            '2026-01-01T00:00:00Z',
        )
        self.assertEqual(record['original_stream_url'], entry.url)
        self.assertEqual(record['provenance']['source'], 'test-source')
        self.assertEqual(record['stream_type'], 'hls')

    def test_hash_includes_attributes_and_ignores_attribute_order(self):
        text = '''#EXTM3U
#EXTINF:-1 tvg-id="id-1" group-title="News",Same Channel
https://example.org/stream
#EXTINF:-1 group-title="News" tvg-id="id-1",Same Channel
https://example.org/stream
#EXTINF:-1 tvg-id="id-2" group-title="News",Same Channel
https://example.org/stream
'''
        entries = list(parse_m3u(text.splitlines(), 'test-source'))

        self.assertEqual(entries[0].content_hash, entries[1].content_hash)
        self.assertNotEqual(entries[0].content_hash, entries[2].content_hash)

    def test_url_without_extinf_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Stream URL without preceding EXTINF'):
            list(parse_m3u(['https://example.org/stream'], 'test-source'))


if __name__ == '__main__':
    unittest.main()
