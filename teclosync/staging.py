import json
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  format TEXT NOT NULL,
  url TEXT NOT NULL,
  min_records INTEGER NOT NULL,
  timeout_seconds INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS source_runs (
  source_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  status TEXT NOT NULL,
  record_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (source_id, run_id),
  FOREIGN KEY (source_id) REFERENCES sources(source_id)
);
CREATE TABLE IF NOT EXISTS channels (
  channel_key TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_record_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  tvg_id TEXT NOT NULL DEFAULT '',
  tvg_name TEXT NOT NULL DEFAULT '',
  tvg_logo TEXT NOT NULL DEFAULT '',
  group_title TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT '',
  stream_url TEXT NOT NULL,
  original_stream_url TEXT NOT NULL,
  stream_type TEXT NOT NULL,
  quality TEXT NOT NULL DEFAULT 'unknown',
  radio INTEGER NOT NULL DEFAULT 0,
  provenance_json TEXT NOT NULL,
  PRIMARY KEY (source_id, channel_key),
  FOREIGN KEY (source_id, source_run_id) REFERENCES source_runs(source_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_channels_source ON channels(source_id);
CREATE INDEX IF NOT EXISTS idx_channels_tvg_id ON channels(tvg_id);
CREATE INDEX IF NOT EXISTS idx_channels_name ON channels(name);
CREATE INDEX IF NOT EXISTS idx_channels_stream ON channels(stream_url);
"""

class StagingDatabase:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    def register_source(self, source):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('INSERT OR REPLACE INTO sources (source_id, name, category, format, url, min_records, timeout_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)', (source['id'], source['name'], source['category'], source['format'], source['url'], source['min_records'], source['timeout_seconds']))
            connection.commit()

    def begin_source_run(self, source_id, run_id, started_at):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('INSERT OR REPLACE INTO source_runs (source_id, run_id, started_at, status, record_count) VALUES (?, ?, ?, ?, 0)', (source_id, run_id, started_at, 'running'))
            connection.commit()

    def replace_source_channels(self, source_id, run_id, records):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                connection.execute('DELETE FROM channels WHERE source_id = ?', (source_id,))
                connection.executemany('''INSERT INTO channels (channel_key, source_id, source_run_id, source_record_index, name, tvg_id, tvg_name, tvg_logo, group_title, country, language, stream_url, original_stream_url, stream_type, quality, radio, provenance_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', [(record['channel_key'], record['source_id'], record['source_run_id'], record['source_record_index'], record['name'], record['tvg_id'], record['tvg_name'], record['tvg_logo'], record['group_title'], record['country'], record['language'], record['stream_url'], record['original_stream_url'], record['stream_type'], record['quality'], int(record['radio']), json.dumps(record['provenance'], sort_keys=True)) for record in records])
                connection.execute('UPDATE source_runs SET status = ?, record_count = ? WHERE source_id = ? AND run_id = ?', ('complete', len(records), source_id, run_id))
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fail_source_run(self, source_id, run_id, status='failed'):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('UPDATE source_runs SET status = ? WHERE source_id = ? AND run_id = ?', (status, source_id, run_id))
            connection.commit()

    def source_channel_count(self, source_id):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute('SELECT COUNT(*) FROM channels WHERE source_id = ?', (source_id,)).fetchone()[0]

    def channel_count(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute('SELECT COUNT(*) FROM channels').fetchone()[0]
