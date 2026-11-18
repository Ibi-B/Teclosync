import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from teclosync.build import BuildError, build_artifacts
from teclosync.config import load_sources
from teclosync.external_state import ExternalStateError, SnapshotStore
from teclosync.refresh import refresh_sources
from teclosync.staging import StagingDatabase


def write_diagnostics(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def run_refresh(args):
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime('%Y%m%dT%H%M%SZ')
    sources = load_sources(args.config)
    enabled_sources = [source for source in sources if source.get('enabled', False)]
    database = StagingDatabase(args.database)
    for source in enabled_sources:
        database.register_source(source)
        database.begin_source_run(source['id'], run_id, started_at.isoformat())
    refresh_result = asyncio.run(refresh_sources(database, enabled_sources, run_id))
    diagnostics = {
        'run_id': run_id,
        'started_at': started_at.isoformat(),
        'publish_requested': args.publish,
        'publish_succeeded': False,
        'sources': refresh_result['sources'],
    }
    healthy = [result for result in refresh_result['sources'] if result['status'] == 'healthy']
    try:
        if not healthy:
            raise RuntimeError('no_healthy_sources')
        manifest = build_artifacts(database, args.output, run_id)
        diagnostics['artifacts'] = manifest['artifacts']
        if args.publish:
            store = SnapshotStore(
                bucket=os.environ['TECLOSYNC_S3_BUCKET'],
                endpoint_url=os.environ['TECLOSYNC_S3_ENDPOINT'],
                access_key_id=os.environ['TECLOSYNC_S3_ACCESS_KEY_ID'],
                secret_access_key=os.environ['TECLOSYNC_S3_SECRET_ACCESS_KEY'],
                prefix=os.environ.get('TECLOSYNC_S3_PREFIX', 'telclosync/state'),
                region=os.environ.get('TECLOSYNC_S3_REGION', 'auto'),
            )
            output = Path(args.output)
            uploaded = []
            for artifact in manifest['artifacts']:
                local_path = output / artifact['path']
                key = store.upload_verified(local_path, f"current/{artifact['path']}", artifact['sha256'])
                uploaded.append({'artifact': artifact['id'], 'key': key})
            manifest_key = store.publish_manifest(manifest, run_id)
            diagnostics['publish_succeeded'] = True
            diagnostics['uploaded'] = uploaded
            diagnostics['manifest_key'] = manifest_key
        diagnostics['completed_at'] = datetime.now(timezone.utc).isoformat()
        write_diagnostics(Path(args.diagnostics) / f'refresh-{run_id}.json', diagnostics)
        print(json.dumps({'run_id': run_id, 'healthy_sources': len(healthy), 'published': args.publish and diagnostics['publish_succeeded']}))
        return 0
    except (BuildError, ExternalStateError, RuntimeError, KeyError, OSError) as error:
        diagnostics['completed_at'] = datetime.now(timezone.utc).isoformat()
        diagnostics['error'] = f'{type(error).__name__}: {error}'
        write_diagnostics(Path(args.diagnostics) / f'refresh-{run_id}.json', diagnostics)
        print(json.dumps(diagnostics), file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description='Teclosync source registry and controlled refresh tools')
    parser.add_argument('command', choices=['validate-config', 'list-sources', 'refresh'])
    parser.add_argument('--config', default='config/sources.json')
    parser.add_argument('--database', default='state/teclo-sync.db')
    parser.add_argument('--output', default='state/artifacts')
    parser.add_argument('--diagnostics', default='state/diagnostics')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.command == 'refresh':
        raise SystemExit(run_refresh(args))
    try:
        sources = load_sources(args.config)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Configuration error: {error}\n')
    if args.command == 'list-sources':
        print(json.dumps(sources, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({'valid': True, 'sources': len(sources), 'enabled': sum(source.get('enabled', False) for source in sources)}))


if __name__ == '__main__':
    main()
