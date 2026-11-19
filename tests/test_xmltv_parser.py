import gzip
import tempfile
import unittest
from pathlib import Path
from teclosync.parsers.xmltv import parse_xmltv_file
from teclosync.staging import StagingDatabase

class XMLTVTests(unittest.TestCase):
    def source(self):
        return {'id': 'test-epg', 'name': 'Test EPG', 'category': 'epg', 'format': 'xmltv', 'url': 'https://example.org/guide.xml', 'min_records': 1, 'timeout_seconds': 30}

    def test_parses_programmes(self):
        programmes = list(parse_xmltv_file(Path('tests/fixtures/sample.xmltv.xml'), 'test-epg'))
        self.assertEqual(len(programmes), 2)
        self.assertEqual(programmes[0].channel_id, 'example.se')
        self.assertEqual(programmes[0].title, 'Morning News')

    def test_stages_programmes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = StagingDatabase(Path(directory) / 'state.db')
            database.register_source(self.source())
            database.begin_source_run('test-epg', 'run-1', '2026-01-01T00:00:00Z')
            programmes = list(parse_xmltv_file(Path('tests/fixtures/sample.xmltv.xml'), 'test-epg'))
            records = [{'programme_key': f'{p.channel_id}-{p.start}-{p.source_record_index}', 'source_id': 'test-epg', 'source_run_id': 'run-1', 'source_record_index': p.source_record_index, 'channel_id': p.channel_id, 'start_time': p.start, 'stop_time': p.stop, 'title': p.title, 'description': p.description, 'provenance': {'source': 'test-epg'}} for p in programmes]
            database.replace_source_programmes('test-epg', 'run-1', records)
            self.assertEqual(database.source_programme_count('test-epg'), 2)

    def test_gzip_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.xml.gz'
            data = Path('tests/fixtures/sample.xmltv.xml').read_bytes()
            with gzip.open(path, 'wb') as stream:
                stream.write(data)
            programmes = list(parse_xmltv_file(path, 'test-epg'))
            self.assertEqual(len(programmes), 2)

if __name__ == '__main__':
    unittest.main()
