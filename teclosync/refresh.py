import asyncio
from datetime import datetime, timezone

import aiohttp

from teclosync.normalizers.m3u import normalize_m3u_entry
from teclosync.parsers.m3u import parse_m3u

MAX_CONCURRENT_SOURCES = 3
MAX_REDIRECTS = 5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

class SourceRefreshError(Exception):
    def __init__(self, source_id, reason):
        super().__init__(f'{source_id}: {reason}')
        self.source_id = source_id
        self.reason = reason

async def fetch_text(session, source):
    timeout = aiohttp.ClientTimeout(total=source['timeout_seconds'])
    last_error = None
    for attempt in range(3):
        try:
            async with session.get(source['url'], timeout=timeout, allow_redirects=True, max_redirects=MAX_REDIRECTS, headers={'User-Agent': 'Teclosync/0.1'}) as response:
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
    raise SourceRefreshError(source['id'], type(last_error).__name__ if last_error else 'unknown_error')

def ingest_m3u(source, source_url, text, retrieved_at):
    return [normalize_m3u_entry(entry, source, source_url, retrieved_at) for entry in parse_m3u(text.splitlines(), source['id'])]

async def refresh_source(session, database, source, run_id, started_at):
    try:
        if source['format'] != 'm3u':
            raise SourceRefreshError(source['id'], 'unsupported_format_for_refresh')
        text = await fetch_text(session, source)
        records = ingest_m3u(source, source['url'], text, run_id)
        if len(records) < source['min_records']:
            raise SourceRefreshError(source['id'], f'record_count_{len(records)}_below_minimum_{source["min_records"]}')
        database.replace_source_channels(source['id'], run_id, records)
        return {'source_id': source['id'], 'status': 'healthy', 'record_count': len(records)}
    except SourceRefreshError as error:
        database.fail_source_run(source['id'], run_id)
        return {'source_id': source['id'], 'status': 'failed', 'reason': error.reason, 'record_count': 0}

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
