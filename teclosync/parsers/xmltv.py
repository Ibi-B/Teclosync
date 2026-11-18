import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterator

@dataclass(frozen=True)
class Programme:
    channel_id: str
    start: str
    stop: str
    title: str
    description: str
    source_record_index: int

def _text(element, tag):
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else ''

def parse_xmltv(stream, source_id: str) -> Iterator[Programme]:
    index = 0
    for event, element in ET.iterparse(stream, events=('end',)):
        if element.tag.endswith('programme'):
            index += 1
            yield Programme(channel_id=element.get('channel', ''), start=element.get('start', ''), stop=element.get('stop', ''), title=_text(element, 'title'), description=_text(element, 'desc'), source_record_index=index)
            element.clear()

def parse_xmltv_file(path, source_id: str) -> Iterator[Programme]:
    path = str(path)
    if path.endswith('.gz'):
        with gzip.open(path, 'rb') as stream:
            yield from parse_xmltv(stream, source_id)
    else:
        with open(path, 'rb') as stream:
            yield from parse_xmltv(stream, source_id)
