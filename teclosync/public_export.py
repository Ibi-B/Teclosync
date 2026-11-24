"""Create a small, public, read-only Teclosync IPTV catalogue export.

The private R2 snapshots remain the authoritative rollback/state store. This
export contains only validated playlist records and safe source metadata for
mobile clients; it never contains R2 pointers, credentials, SQLite state, or
full provenance records.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from teclosync.build import write_m3u


_PUBLIC_SCHEMES = {
    'http', 'https', 'rtsp', 'rtmp', 'rtmps', 'mms', 'mmsh', 'udp', 'rtp', 'srt'
}


def _is_public_stream(record):
    value = record.get('stream_url')
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if scheme not in _PUBLIC_SCHEMES:
        return False
    if parsed.username or parsed.password:
        return False
    sensitive_query_keys = {
        'token', 'access_token', 'refresh_token', 'api_key', 'apikey',
        'auth', 'authorization', 'password', 'passwd', 'secret',
        'signature', 'sig', 'jwt', 'key',
    }
    if any(
        key.lower() in sensitive_query_keys
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        return False
    return bool(parsed.netloc or scheme in {'udp', 'rtp'})


def write_public_catalogue(database, sources, source_status, output_dir, run_id, generated_at=None):
    """Write catalogue.json and one M3U per configured source with safe rows.

    A failed source can still be published when Teclosync retained its last-good
    rows. The source status is shown in the index so consumers can distinguish
    fresh data from retained data.
    """
    output = Path(output_dir)
    playlists_dir = output / 'playlists'
    playlists_dir.mkdir(parents=True, exist_ok=True)

    records_by_source = {}
    for record in database.iter_channels():
        records_by_source.setdefault(record['source_id'], []).append(record)

    status_by_source = {
        row.get('source_id'): row
        for row in source_status
        if isinstance(row, dict) and row.get('source_id')
    }

    playlists = []
    for source in sources:
        source_id = source.get('id')
        if not source_id:
            continue
        safe_records = [
            row for row in records_by_source.get(source_id, [])
            if _is_public_stream(row)
        ]
        if not safe_records:
            continue

        filename = f'{source_id}.m3u8'
        write_m3u(playlists_dir / filename, safe_records)
        status = status_by_source.get(source_id, {})
        playlists.append({
            'id': source_id,
            'name': source.get('name') or source_id,
            'category': source.get('category') or 'other',
            'region': source.get('region') or '',
            'channelCount': len(safe_records),
            'status': status.get('status') or 'unknown',
            'dataState': status.get('data_state') or 'unknown',
            'path': f'playlists/{filename}',
        })

    if not playlists:
        raise ValueError('no_public_playlists_to_publish')

    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    category_counts = {}
    region_counts = {}
    for playlist in playlists:
        category = playlist['category']
        category_counts[category] = category_counts.get(category, 0) + 1
        region = playlist['region']
        if region:
            region_counts[region] = region_counts.get(region, 0) + 1

    catalogue = {
        'schemaVersion': 1,
        'provider': 'Teclosync',
        'releaseId': run_id,
        'generatedAt': generated_at,
        'playlistCount': len(playlists),
        'channelCount': sum(row['channelCount'] for row in playlists),
        'categories': [
            {'id': key, 'playlistCount': value}
            for key, value in sorted(category_counts.items())
        ],
        'regions': [
            {'code': key, 'playlistCount': value}
            for key, value in sorted(region_counts.items())
        ],
        'playlists': playlists,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / 'catalogue.json').write_text(
        json.dumps(catalogue, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    return catalogue
