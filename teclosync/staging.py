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
CREATE TABLE IF NOT EXISTS channel_duplicates (
  duplicate_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_record_index INTEGER NOT NULL,
  channel_key TEXT NOT NULL,
  reason TEXT NOT NULL,
  record_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  UNIQUE (source_id, source_run_id, source_record_index),
  FOREIGN KEY (source_id, source_run_id) REFERENCES source_runs(source_id, run_id)
);
CREATE TABLE IF NOT EXISTS programmes (
  programme_key TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_record_index INTEGER NOT NULL,
  channel_id TEXT NOT NULL,
  start_time TEXT NOT NULL,
  stop_time TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  provenance_json TEXT NOT NULL,
  PRIMARY KEY (source_id, programme_key),
  FOREIGN KEY (source_id, source_run_id) REFERENCES source_runs(source_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_channels_source ON channels(source_id);
CREATE INDEX IF NOT EXISTS idx_channels_tvg_id ON channels(tvg_id);
CREATE INDEX IF NOT EXISTS idx_channels_name ON channels(name);
CREATE INDEX IF NOT EXISTS idx_channels_stream ON channels(stream_url);
CREATE INDEX IF NOT EXISTS idx_channel_duplicates_source ON channel_duplicates(source_id);
CREATE INDEX IF NOT EXISTS idx_programmes_source ON programmes(source_id);
CREATE INDEX IF NOT EXISTS idx_programmes_channel ON programmes(channel_id);
CREATE INDEX IF NOT EXISTS idx_programmes_start ON programmes(start_time);
"""


class StagingDatabase:
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute('PRAGMA foreign_keys = ON')
        return connection

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    def register_source(self, source):
        with closing(self._connect()) as connection:
            connection.execute(
                'INSERT INTO sources '
                '(source_id, name, category, format, url, min_records, timeout_seconds) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(source_id) DO UPDATE SET '
                'name = excluded.name, category = excluded.category, '
                'format = excluded.format, url = excluded.url, '
                'min_records = excluded.min_records, '
                'timeout_seconds = excluded.timeout_seconds',
                (
                    source['id'],
                    source['name'],
                    source['category'],
                    source['format'],
                    source['url'],
                    source['min_records'],
                    source['timeout_seconds'],
                ),
            )
            connection.commit()

    def begin_source_run(self, source_id, run_id, started_at):
        with closing(self._connect()) as connection:
            connection.execute(
                'INSERT INTO source_runs '
                '(source_id, run_id, started_at, status, record_count) '
                'VALUES (?, ?, ?, ?, 0) '
                'ON CONFLICT(source_id, run_id) DO UPDATE SET '
                'started_at = excluded.started_at, '
                'status = excluded.status, record_count = 0',
                (source_id, run_id, started_at, 'running'),
            )
            connection.commit()

    @staticmethod
    def _channel_payload(record):
        # Run/index/provenance vary by occurrence and are not channel content.
        return {
            key: value
            for key, value in record.items()
            if key not in {'source_run_id', 'source_record_index', 'provenance'}
        }

    @classmethod
    def _partition_source_channels(cls, source_id, records):
        unique_records = {}
        duplicate_records = []

        for record in records:
            if record['source_id'] != source_id:
                raise ValueError(
                    f"record source_id {record['source_id']!r} does not match {source_id!r}"
                )
            channel_key = record['channel_key']
            existing = unique_records.get(channel_key)
            if existing is None:
                unique_records[channel_key] = record
                continue
            if cls._channel_payload(existing) != cls._channel_payload(record):
                raise ValueError(
                    f'channel_key collision for source {source_id!r} '
                    f'at records {existing["source_record_index"]} '
                    f'and {record["source_record_index"]}'
                )
            duplicate_records.append(record)

        return unique_records, duplicate_records

    def summarize_source_channels(self, source_id, records):
        unique_records, duplicate_records = self._partition_source_channels(
            source_id,
            records,
        )
        return {
            'record_count': len(unique_records),
            'source_record_count': len(records),
            'duplicate_count': len(duplicate_records),
        }

    def record_source_duplicates(self, source_id, run_id, records):
        _, duplicate_records = self._partition_source_channels(source_id, records)
        if not duplicate_records:
            return 0

        with closing(self._connect()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                connection.executemany(
                    '''INSERT OR REPLACE INTO channel_duplicates (
                        source_id, source_run_id, source_record_index, channel_key,
                        reason, record_json, provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    [
                        (
                            record['source_id'],
                            record['source_run_id'],
                            record['source_record_index'],
                            record['channel_key'],
                            'exact_duplicate',
                            json.dumps(record, sort_keys=True, ensure_ascii=False),
                            json.dumps(record['provenance'], sort_keys=True, ensure_ascii=False),
                        )
                        for record in duplicate_records
                    ],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return len(duplicate_records)

    def replace_source_channels(self, source_id, run_id, records):
        unique_records, duplicate_records = self._partition_source_channels(
            source_id,
            records,
        )

        with closing(self._connect()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                connection.execute('DELETE FROM channels WHERE source_id = ?', (source_id,))
                connection.executemany(
                    '''INSERT INTO channels (
                        channel_key, source_id, source_run_id, source_record_index,
                        name, tvg_id, tvg_name, tvg_logo, group_title, country,
                        language, stream_url, original_stream_url, stream_type,
                        quality, radio, provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    [
                        (
                            record['channel_key'],
                            record['source_id'],
                            record['source_run_id'],
                            record['source_record_index'],
                            record['name'],
                            record['tvg_id'],
                            record['tvg_name'],
                            record['tvg_logo'],
                            record['group_title'],
                            record['country'],
                            record['language'],
                            record['stream_url'],
                            record['original_stream_url'],
                            record['stream_type'],
                            record['quality'],
                            int(record['radio']),
                            json.dumps(record['provenance'], sort_keys=True),
                        )
                        for record in unique_records.values()
                    ],
                )
                connection.executemany(
                    '''INSERT OR REPLACE INTO channel_duplicates (
                        source_id, source_run_id, source_record_index, channel_key,
                        reason, record_json, provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    [
                        (
                            record['source_id'],
                            record['source_run_id'],
                            record['source_record_index'],
                            record['channel_key'],
                            'exact_duplicate',
                            json.dumps(record, sort_keys=True, ensure_ascii=False),
                            json.dumps(record['provenance'], sort_keys=True, ensure_ascii=False),
                        )
                        for record in duplicate_records
                    ],
                )
                connection.execute(
                    'UPDATE source_runs SET status = ?, record_count = ? '
                    'WHERE source_id = ? AND run_id = ?',
                    ('complete', len(unique_records), source_id, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return {
            'record_count': len(unique_records),
            'source_record_count': len(records),
            'duplicate_count': len(duplicate_records),
        }

    def source_duplicate_count(self, source_id):
        with closing(self._connect()) as connection:
            return connection.execute(
                'SELECT COUNT(*) FROM channel_duplicates WHERE source_id = ?',
                (source_id,),
            ).fetchone()[0]

    def iter_channel_duplicates(self, source_id=None):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            if source_id is None:
                rows = connection.execute(
                    'SELECT * FROM channel_duplicates '
                    'ORDER BY source_id, source_record_index'
                )
            else:
                rows = connection.execute(
                    'SELECT * FROM channel_duplicates WHERE source_id = ? '
                    'ORDER BY source_record_index',
                    (source_id,),
                )
            for row in rows:
                duplicate = dict(row)
                duplicate['record'] = json.loads(duplicate.pop('record_json'))
                duplicate['provenance'] = json.loads(duplicate.pop('provenance_json'))
                yield duplicate

    def replace_source_programmes(self, source_id, run_id, records):
        with closing(self._connect()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                connection.execute('DELETE FROM programmes WHERE source_id = ?', (source_id,))
                connection.executemany(
                    '''INSERT INTO programmes (
                        programme_key, source_id, source_run_id, source_record_index,
                        channel_id, start_time, stop_time, title, description,
                        provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    [
                        (
                            record['programme_key'],
                            record['source_id'],
                            record['source_run_id'],
                            record['source_record_index'],
                            record['channel_id'],
                            record['start_time'],
                            record['stop_time'],
                            record['title'],
                            record['description'],
                            json.dumps(record['provenance'], sort_keys=True),
                        )
                        for record in records
                    ],
                )
                connection.execute(
                    'UPDATE source_runs SET status = ?, record_count = ? '
                    'WHERE source_id = ? AND run_id = ?',
                    ('complete', len(records), source_id, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fail_source_run(self, source_id, run_id, status='failed'):
        with closing(self._connect()) as connection:
            connection.execute(
                'UPDATE source_runs SET status = ? WHERE source_id = ? AND run_id = ?',
                (status, source_id, run_id),
            )
            connection.commit()

    def iter_channels(self):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute(
                'SELECT * FROM channels ORDER BY source_id, source_record_index'
            ):
                record = dict(row)
                record['radio'] = bool(record['radio'])
                record['provenance'] = json.loads(record['provenance_json'])
                del record['provenance_json']
                yield record

    def source_channel_count(self, source_id):
        with closing(self._connect()) as connection:
            return connection.execute(
                'SELECT COUNT(*) FROM channels WHERE source_id = ?',
                (source_id,),
            ).fetchone()[0]

    def source_programme_count(self, source_id):
        with closing(self._connect()) as connection:
            return connection.execute(
                'SELECT COUNT(*) FROM programmes WHERE source_id = ?',
                (source_id,),
            ).fetchone()[0]

    def channel_count(self):
        with closing(self._connect()) as connection:
            return connection.execute('SELECT COUNT(*) FROM channels').fetchone()[0]

    def programme_count(self):
        with closing(self._connect()) as connection:
            return connection.execute('SELECT COUNT(*) FROM programmes').fetchone()[0]
