import json
import re
from pathlib import Path
from urllib.parse import urlsplit

FORMATS = {'m3u', 'xmltv', 'xmltv_gz', 'json', 'directory', 'repository'}

def load_sources(path='config/sources.json'):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('Expected registry version 1')
    sources = data.get('sources')
    if not isinstance(sources, list):
        raise ValueError('sources must be a list')
    seen = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('Each source must be an object')
        sid = source.get('id')
        if not isinstance(sid, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', sid) or sid in seen:
            raise ValueError('Invalid or duplicate source ID')
        seen.add(sid)
        if type(source.get('enabled')) is not bool:
            raise ValueError(f'{sid}: enabled must be boolean')
        if source.get('format') not in FORMATS:
            raise ValueError(f'{sid}: unsupported format')
        url = source.get('url')
        if not isinstance(url, str):
            raise ValueError(f'{sid}: URL required')
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f'{sid}: invalid source URL')
        if source.get('status') not in {'candidate', 'validated', 'disabled'}:
            raise ValueError(f'{sid}: invalid status')
        if source['enabled'] and (source['status'] != 'validated' or source['format'] in {'directory', 'repository'}):
            raise ValueError(f'{sid}: source must be validated and directly ingestible before enablement')
        for key in ('min_records', 'timeout_seconds'):
            value = source.get(key)
            if type(value) is not int or value < 1:
                raise ValueError(f'{sid}: {key} must be a positive integer')
    return sources
