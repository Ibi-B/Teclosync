"""Create a scoped, public read-only Teclosync IPTV catalogue export.

R2 snapshots remain the canonical rollback/state store. This export is a
best-effort delivery mirror, not a guarantee that every accepted stream is
safe or authorized for redistribution.
"""
import ipaddress
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from teclosync.build import write_m3u


_PUBLIC_STREAM_SCHEMES = {
    'http', 'https', 'rtsp', 'rtmp', 'rtmps', 'mms', 'mmsh', 'udp', 'rtp', 'srt'
}
_ARTWORK_SCHEMES = {'http', 'https'}
_SENSITIVE_QUERY_KEYS = {
    'token', 'accesstoken', 'refreshtoken', 'apikey', 'auth',
    'authorization', 'password', 'passwd', 'secret', 'signature',
    'sig', 'jwt', 'key', 'xamzcredential', 'xamzsignature',
    'xamzsecuritytoken', 'xgoogcredential', 'xgoogsignature',
    'hdnea', 'hdntl', 'policy', 'keypairid',
}
_PRIVATE_HOST_SUFFIXES = (
    '.localhost', '.local', '.internal', '.lan', '.home', '.home.arpa',
    '.corp', '.intranet', '.private', '.example', '.invalid', '.test',
)
_PRIVATE_HOSTS = {'localhost', 'localhost.localdomain'}


def _has_sensitive_query(parsed):
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = re.sub(r'[^a-z0-9]', '', key.lower())
        if normalized in _SENSITIVE_QUERY_KEYS:
            return True
    return False


def _has_private_or_local_host(host):
    hostname = (host or '').strip().rstrip('.').lower()
    if not hostname or hostname in _PRIVATE_HOSTS:
        return True
    if hostname.endswith(_PRIVATE_HOST_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(unquote(hostname))
    except ValueError:
        # This is a conservative syntax check. It does not resolve DNS, so it
        # cannot detect a public-looking hostname that resolves to a private IP.
        return False
    return not address.is_global


def _has_credential_path(parsed):
    path = unquote(parsed.path).replace('\\\\', '/')
    segments = [segment for segment in path.split('/') if segment]
    if any(
        re.search(r'(?:token|password|passwd|secret|api[_-]?key)=', segment, re.I)
        for segment in segments
    ):
        return True
    # Xtream-style stream paths encode username/password in the URL path.
    return len(segments) >= 4 and segments[0].lower() in {'live', 'movie', 'series'}


def _is_redistributable_url(value, allowed_schemes):
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        # Accessing port validates malformed port syntax as well.
        _ = parsed.port
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if scheme not in allowed_schemes:
        return False
    if parsed.username or parsed.password or not parsed.hostname:
        return False
    if _has_private_or_local_host(parsed.hostname):
        return False
    if _has_sensitive_query(parsed) or _has_credential_path(parsed):
        return False
    return True


def _is_public_stream(record):
    value = record.get('stream_url')
    if not _is_redistributable_url(value, _PUBLIC_STREAM_SCHEMES):
        return False
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return bool(parsed.netloc or parsed.scheme.lower() in {'udp', 'rtp'})


def _sanitize_public_record(record):
    """Copy one accepted row and independently remove unsafe artwork URLs."""
    safe_record = dict(record)
    logo = safe_record.get('tvg_logo')
    if logo and not _is_redistributable_url(logo, _ARTWORK_SCHEMES):
        safe_record['tvg_logo'] = ''
    return safe_record


def _valid_path_component(value):
    return isinstance(value, str) and bool(
        re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value)
    )


def write_public_catalogue(database, sources, source_status, output_dir, run_id, generated_at=None):
    """Write immutable release playlists and the mutable current index.

    Unsafe stream rows are omitted. Unsafe artwork references are blanked while
    retaining an otherwise acceptable stream row. DNS is not resolved, and no
    policy can prove that a third-party endpoint is authorized for redistribution.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', run_id):
        raise ValueError('invalid_public_release_id')

    output = Path(output_dir)
    release_playlists_dir = output / 'releases' / run_id / 'playlists'
    release_playlists_dir.mkdir(parents=True, exist_ok=True)

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
        if not _valid_path_component(source_id):
            continue
        safe_records = [
            _sanitize_public_record(row)
            for row in records_by_source.get(source_id, [])
            if _is_public_stream(row)
        ]
        if not safe_records:
            continue

        filename = f'{source_id}.m3u8'
        write_m3u(release_playlists_dir / filename, safe_records)
        status = status_by_source.get(source_id, {})
        playlists.append({
            'id': source_id,
            'name': source.get('name') or source_id,
            'category': source.get('category') or 'other',
            'region': source.get('region') or '',
            'channelCount': len(safe_records),
            'status': status.get('status') or 'unknown',
            'dataState': status.get('data_state') or 'unknown',
            'path': f'releases/{run_id}/playlists/{filename}',
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
        json.dumps(catalogue, indent=2, ensure_ascii=False) + '\\n',
        encoding='utf-8',
    )
    return catalogue
