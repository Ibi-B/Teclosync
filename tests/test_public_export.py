import json
from pathlib import Path

import pytest

from teclosync.public_export import write_public_catalogue


class FakeDatabase:
    def __init__(self, records):
        self.records = records

    def iter_channels(self):
        yield from self.records


def _record(source_id, name, url, index):
    return {
        'source_id': source_id,
        'source_record_index': index,
        'name': name,
        'tvg_id': '',
        'tvg_name': name,
        'tvg_logo': '',
        'group_title': 'News',
        'stream_url': url,
    }


def test_public_export_writes_source_playlist_and_safe_catalogue(tmp_path):
    database = FakeDatabase([
        _record('iptv-org-sweden', 'SVT 1', 'https://streams.example/svt1.m3u8', 1),
        _record('iptv-org-sweden', 'Private', 'https://user:password@private.example/live', 2),
    ])
    catalogue = write_public_catalogue(
        database,
        [{
            'id': 'iptv-org-sweden',
            'name': 'IPTV-Org Sweden',
            'category': 'live_tv',
            'region': 'se',
        }],
        [{
            'source_id': 'iptv-org-sweden',
            'status': 'failed',
            'data_state': 'retained_last_good',
        }],
        tmp_path / 'public' / 'iptv',
        '20261125T120000Z',
        generated_at='2026-11-25T12:00:00+00:00',
    )

    assert catalogue['schemaVersion'] == 1
    assert catalogue['releaseId'] == '20261125T120000Z'
    assert catalogue['playlistCount'] == 1
    assert catalogue['channelCount'] == 1
    playlist = catalogue['playlists'][0]
    assert playlist['dataState'] == 'retained_last_good'
    assert playlist['path'] == 'playlists/iptv-org-sweden.m3u8'

    index = json.loads((tmp_path / 'public' / 'iptv' / 'catalogue.json').read_text())
    assert index == catalogue
    m3u = (tmp_path / 'public' / 'iptv' / playlist['path']).read_text()
    assert 'SVT 1' in m3u
    assert 'private.example' not in m3u


def test_public_export_refuses_to_replace_index_with_empty_catalogue(tmp_path):
    with pytest.raises(ValueError, match='no_public_playlists_to_publish'):
        write_public_catalogue(
            FakeDatabase([]),
            [{'id': 'iptv-org-sweden', 'name': 'IPTV-Org Sweden'}],
            [],
            tmp_path,
            '20261125T120000Z',
        )
    assert not (tmp_path / 'catalogue.json').exists()
