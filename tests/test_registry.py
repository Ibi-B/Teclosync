import json
import tempfile
import unittest
from pathlib import Path
from teclosync.config import load_sources

class RegistryTests(unittest.TestCase):
    def source(self):
        return dict(id='example-feed', enabled=False, status='candidate', format='m3u', url='https://example.org/feed.m3u?token=keep', min_records=1, timeout_seconds=30)

    def load(self, sources):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sources.json'
            path.write_text(json.dumps({'version': 1, 'sources': sources}), encoding='utf-8')
            return load_sources(path)

    def test_preserves_original_url(self):
        source = self.source()
        self.assertEqual(self.load([source])[0]['url'], source['url'])

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            self.load([self.source(), self.source()])

    def test_unvalidated_enablement_rejected(self):
        source = self.source()
        source['enabled'] = True
        with self.assertRaises(ValueError):
            self.load([source])

    def test_index_cannot_be_enabled(self):
        source = self.source()
        source.update(enabled=True, status='validated', format='directory')
        with self.assertRaises(ValueError):
            self.load([source])

    def test_invalid_threshold_rejected(self):
        source = self.source()
        source['min_records'] = True
        with self.assertRaises(ValueError):
            self.load([source])

    def test_validated_source_allowed(self):
        source = self.source()
        source.update(enabled=True, status='validated')
        self.assertTrue(self.load([source])[0]['enabled'])

if __name__ == '__main__':
    unittest.main()
