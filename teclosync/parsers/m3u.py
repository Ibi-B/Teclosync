import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class M3UEntry:
    name: str
    url: str
    attributes: dict
    source_record_index: int
    content_hash: str


def _parse_attributes(value: str) -> dict:
    return {
        match.group(1).lower(): match.group(2)
        for match in re.finditer(r'([A-Za-z0-9_-]+)="([^"]*)"', value)
    }


def _entry_content_hash(source_id: str, name: str, url: str, attributes: dict) -> str:
    """Fingerprint the complete parsed entry, not just its display name and URL."""
    canonical_entry = json.dumps(
        {
            'source_id': source_id,
            'name': name,
            'url': url,
            'attributes': attributes,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(canonical_entry.encode('utf-8')).hexdigest()


def parse_m3u(lines: Iterator[str], source_id: str) -> Iterator[M3UEntry]:
    pending = None
    index = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            if pending is not None:
                raise ValueError('EXTINF without following stream URL')
            comma = line.find(',', 8)
            attributes = _parse_attributes(line[8:comma] if comma >= 0 else line[8:])
            pending = (attributes, line[comma + 1:].strip() if comma >= 0 else '')
            continue
        if line.startswith('#'):
            continue
        if pending is None:
            raise ValueError('Stream URL without preceding EXTINF')
        attributes, name = pending
        pending = None
        index += 1
        digest = _entry_content_hash(source_id, name, line, attributes)
        yield M3UEntry(
            name=name,
            url=line,
            attributes=attributes,
            source_record_index=index,
            content_hash=digest,
        )
    if pending is not None:
        raise ValueError('EXTINF without following stream URL')
