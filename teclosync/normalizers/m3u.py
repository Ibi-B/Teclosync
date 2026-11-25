from urllib.parse import urlsplit


NETWORK_SCHEMES = {'http', 'https', 'rtmp', 'rtmps', 'rtsp', 'mms', 'mmsh', 'udp', 'rtp', 'srt'}


def is_usable_stream_url(value: str) -> bool:
    """Reject placeholder text and malformed endpoints before catalogue publication."""
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = value.strip()
    if candidate.startswith('[') and candidate.endswith(']'):
        return False
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    return parsed.scheme.lower() in NETWORK_SCHEMES and bool(
        parsed.netloc or parsed.scheme.lower() in {'udp', 'rtp'}
    )
from teclosync.parsers.m3u import M3UEntry

def normalize_m3u_entry(entry: M3UEntry, source: dict, source_url: str, retrieved_at: str) -> dict:
    attributes = entry.attributes
    parsed = urlsplit(entry.url)
    stream_type = 'hls' if entry.url.lower().endswith(('.m3u8', '.m3u')) or 'm3u8' in parsed.path.lower() else 'direct'
    return {
        'source_id': source['id'],
        'source_run_id': retrieved_at,
        'source_record_index': entry.source_record_index,
        'channel_key': entry.content_hash,
        'name': entry.name or attributes.get('tvg-name') or '',
        'tvg_id': attributes.get('tvg-id') or '',
        'tvg_name': attributes.get('tvg-name') or '',
        'tvg_logo': attributes.get('tvg-logo') or '',
        'group_title': attributes.get('group-title') or '',
        'country': source.get('region') or '',
        'language': attributes.get('tvg-language') or '',
        'stream_url': entry.url,
        'original_stream_url': entry.url,
        'stream_type': stream_type,
        'quality': attributes.get('tvg-quality') or 'unknown',
        'radio': attributes.get('radio', '').lower() == 'true',
        'provenance': {'source': source['id'], 'source_url': source_url, 'retrieved_at': retrieved_at, 'source_record_index': entry.source_record_index, 'content_hash': entry.content_hash}
    }
