import asyncio
import sqlite3
from datetime import datetime, timezone

import aiohttp

from teclosync.normalizers.m3u import is_usable_stream_url, normalize_m3u_entry
from teclosync.parsers.m3u import parse_m3u

MAX_CONCURRENT_SOURCES = 3
MAX_REDIRECTS = 5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SourceRefreshError(Exception):
    def __init__(
        self,
        source_id,
        reason,
        source_record_count=0,
        duplicate_count=0,
    ):
        super().__init__(f'{source_id}: {reason}')
        self.source_id = source_id
        self.reason = reason
        self.source_record_count = source_record_count
        self.duplicate_count = duplicate_count


async def fetch_text(session, source):
    timeout = aiohttp.ClientTimeout(total=source['timeout_seconds'])
    last_error = None
    for attempt in range(3):
        try:
            async with session.get(
                source['url'],
                timeout=timeout,
                allow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                headers={'User-Agent': 'Teclosync/0.1'},
            ) as response:
                if response.status in RETRYABLE_STATUS and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if response.status != 200:
                    raise SourceRefreshError(source['id'], f'http_{response.status}')
                return await response.text(errors='replace')
        except (asyncio.TimeoutError, aiohttp.ClientError) as error:
            last_error = error
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    raise SourceRefreshError(
        source['id'],
        type(last_error).__name__ if last_error else 'unknown_error',
    )


def ingest_m3u(source, source_url, text, retrieved_at, diagnostics=None):
    records = []
    rejected = []
    for entry in parse_m3u(text.splitlines(), source['id']):
        record = normalize_m3u_entry(entry, source, source_url, retrieved_at)
        if not is_usable_stream_url(record['stream_url']):
            if len(rejected) < 20:
                rejected.append({
                    'source_record_index': entry.source_record_index,
                    'name': record['name'],
                    'stream_url': record['stream_url'][:180],
                    'reason': 'invalid_or_placeholder_stream_url',
                })
            continue
        records.append(record)
    if diagnostics is not None:
        diagnostics['rejected_stream_count'] = sum(
            1 for entry in parse_m3u(text.splitlines(), source['id'])
            if not is_usable_stream_url(entry.url)
        )
        diagnostics['rejected_stream_examples'] = rejected
    return records


def _diagnostic_reason(error):
    error_type = type(error).__name__
    if isinstance(error, (ValueError, sqlite3.Error)):
        detail = str(error).strip()
        if detail:
            return f'{error_type}: {detail[:240]}'
    return error_type


async def refresh_source(session, database, source, run_id, started_at):
    try:
        if source['format'] != 'm3u':
            raise SourceRefreshError(source['id'], 'unsupported_format_for_refresh')
        text = await fetch_text(session, source)
        ingest_diagnostics = {}
        records = ingest_m3u(source, source['url'], text, run_id, diagnostics=ingest_diagnostics)
        summary = database.summarize_source_channels(source['id'], records)
        if summary['record_count'] < source['min_records']:
            database.record_source_duplicates(source['id'], run_id, records)
            raise SourceRefreshError(
                source['id'],
                f'record_count_{summary["record_count"]}_below_minimum_{source["min_records"]}',
                source_record_count=summary['source_record_count'],
                duplicate_count=summary['duplicate_count'],
            )
        stored_summary = database.replace_source_channels(
            source['id'],
            run_id,
            records,
        )
        if not isinstance(stored_summary, dict):
            stored_summary = summary
        return {
            'source_id': source['id'],
            'status': 'healthy',
            'record_count': stored_summary.get('record_count', summary['record_count']),
            'source_record_count': stored_summary.get(
                'source_record_count',
                summary['source_record_count'],
            ),
            'duplicate_count': stored_summary.get(
                'duplicate_count',
                summary['duplicate_count'],
            ),
            'rejected_stream_count': ingest_diagnostics.get('rejected_stream_count', 0),
            'rejected_stream_examples': ingest_diagnostics.get('rejected_stream_examples', []),
        }
    except SourceRefreshError as error:
        database.fail_source_run(source['id'], run_id)
        return {
            'source_id': source['id'],
            'status': 'failed',
            'reason': error.reason,
            'record_count': 0,
            'source_record_count': error.source_record_count,
            'duplicate_count': error.duplicate_count,
        }
    except Exception as error:
        database.fail_source_run(source['id'], run_id)
        return {
            'source_id': source['id'],
            'status': 'failed',
            'reason': _diagnostic_reason(error),
            'record_count': 0,
            'source_record_count': 0,
            'duplicate_count': 0,
        }


async def refresh_sources(database, sources, run_id=None):
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    started_at = datetime.now(timezone.utc).isoformat()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SOURCES)
    async with aiohttp.ClientSession() as session:
        async def run(source):
            async with semaphore:
                return await refresh_source(session, database, source, run_id, started_at)

        results = await asyncio.gather(*(run(source) for source in sources))
    return {'run_id': run_id, 'started_at': started_at, 'sources': list(results)}
