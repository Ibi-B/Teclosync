import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import zstandard


class BuildError(Exception):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_m3u(path, records):
    with open(path, 'w', encoding='utf-8', newline='\\n') as stream:
        stream.write('#EXTM3U\\n')
        for record in records:
            attributes = []
            if record['tvg_id']:
                attributes.append(f'tvg-id="{record["tvg_id"]}"')
            if record['tvg_name']:
                attributes.append(f'tvg-name="{record["tvg_name"]}"')
            if record['tvg_logo']:
                attributes.append(f'tvg-logo="{record["tvg_logo"]}"')
            if record['group_title']:
                attributes.append(f'group-title="{record["group_title"]}"')
            # Extended M3U attributes are space-delimited; the comma separates
            # the attribute block from the display name.
            prefix = ' '.join(attributes)
            stream.write(f'#EXTINF:-1 {prefix},{record["name"]}\\n')
            stream.write(f'{record["stream_url"]}\\n')


def write_jsonl_zst(path, records):
    compressor = zstandard.ZstdCompressor(level=19)
    with open(path, 'wb') as stream:
        with compressor.stream_writer(stream) as writer:
            for record in records:
                writer.write((json.dumps(record, ensure_ascii=False, sort_keys=True) + '\\n').encode('utf-8'))


def build_artifacts(database, output_dir, run_id, epg_source_path=None, source_status=None):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    slices = {
        'live-nordic': lambda record: record['country'] in {'se', 'no', 'dk', 'fi'},
        'live-global': lambda record: True,
        'fast-pluto-plex': lambda record: record['source_id'] in {
            'pluto-tv-all', 'pluto-tv-se', 'plex-us',
        },
        'vod-movies-series': lambda record: record['source_id'] in {
            'iptv-org-movies', 'iptv-org-series', 'iptv-org-classic',
            'iptv-org-animation', 'orphanedfilms-vod', 'jromero88-vod',
            'freemoviesplus',
        },
    }
    for slice_id, predicate in slices.items():
        records = [record for record in database.iter_channels() if predicate(record)]
        if not records:
            continue
        path = output / f'{slice_id}.m3u8'
        write_m3u(path, records)
        artifacts.append({
            'id': slice_id,
            'path': path.name,
            'sha256': sha256_file(path),
            'bytes': path.stat().st_size,
            'channel_count': len(records),
        })
    records = list(database.iter_channels())
    if not records:
        raise BuildError('no_channels_to_publish')
    catalogue_path = output / 'tv-catalog.jsonl.zst'
    write_jsonl_zst(catalogue_path, records)
    artifacts.append({
        'id': 'tv-catalog',
        'path': catalogue_path.name,
        'sha256': sha256_file(catalogue_path),
        'bytes': catalogue_path.stat().st_size,
        'record_count': len(records),
    })
    if epg_source_path:
        epg_path = output / 'epg-merged.xml.gz'
        with open(epg_source_path, 'rb') as source, open(epg_path, 'wb') as target:
            target.write(source.read())
        artifacts.append({
            'id': 'epg-merged',
            'path': epg_path.name,
            'sha256': sha256_file(epg_path),
            'bytes': epg_path.stat().st_size,
        })
    manifest = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'run_id': run_id,
        'artifacts': artifacts,
        'sources': source_status or [],
    }
    manifest_path = output / 'manifest.json'
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    return manifest
