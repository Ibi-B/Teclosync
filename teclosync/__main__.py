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
from teclosync.validation import ValidationError, validate_refresh


def write_diagnostics(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def source_freshness(database, sources, refresh_result):
    results = {item['source_id']: item for item in refresh_result['sources']}
    statuses = []
    for source in sources:
        result = results.get(source['id'], {'status': 'missing', 'reason': 'missing_result'})
        count = database.source_channel_count(source['id'])
        if result['status'] == 'healthy':
            data_state = 'fresh'
        elif count > 0:
            data_state = 'retained_last_good'
        else:
            data_state = 'unavailable'
        statuses.append({
            'source_id': source['id'],
            'status': result['status'],
            'data_state': data_state,
            'channel_count': count,
            'accepted_record_count': result.get('record_count', 0),
            'source_record_count': result.get('source_record_count', 0),
            'duplicate_count': result.get('duplicate_count', 0),
            'reason': result.get('reason'),
        })
    return statuses


def publish_current_pointer(store, current_pointer, previous_pointer=None):
    if previous_pointer:
        store.write_pointer('previous.json', previous_pointer)

    try:
        store.write_pointer('current.json', current_pointer)
    except Exception as publication_error:
        if previous_pointer:
            try:
                # A failed read-back can occur after the object-store PUT has
                # committed. Restore the last-known-good pointer before returning.
                store.write_pointer('current.json', previous_pointer)
            except Exception as rollback_error:
                raise ExternalStateError(
                    'current_pointer_update_failed_and_rollback_failed: '
                    f'{publication_error}; {rollback_error}'
                ) from rollback_error
        raise


def run_refresh(args):
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime('%Y%m%dT%H%M%SZ')
    sources = load_sources(args.config)
    enabled_sources = [source for source in sources if source.get('enabled', False)]
    store = None
    previous_pointer = None
    restored = False
    if args.publish:
        store = SnapshotStore(
            bucket=os.environ['TECLOSYNC_S3_BUCKET'],
            endpoint_url=os.environ['TECLOSYNC_S3_ENDPOINT'],
            access_key_id=os.environ['TECLOSYNC_S3_ACCESS_KEY_ID'],
            secret_access_key=os.environ['TECLOSYNC_S3_SECRET_ACCESS_KEY'],
            prefix=os.environ.get('TECLOSYNC_S3_PREFIX', 'telclosync/state'),
            region=os.environ.get('TECLOSYNC_S3_REGION', 'auto'),
        )
        previous_pointer = store.read_pointer('current.json')
        state_key = (
            previous_pointer.get('state_key')
            if isinstance(previous_pointer, dict)
            else None
        )
        restored = store.download_state_database(args.database, key=state_key)
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
        'last_good_restored': restored,
        'sources': refresh_result['sources'],
    }
    try:
        validation_report = validate_refresh(database, enabled_sources, refresh_result)
        diagnostics['validation'] = validation_report
        if not validation_report['publication_allowed']:
            raise ValidationError('publication_not_allowed')
        statuses = source_freshness(database, enabled_sources, refresh_result)
        diagnostics['source_freshness'] = statuses
        manifest = build_artifacts(database, args.output, run_id, source_status=statuses)
        diagnostics['artifacts'] = manifest['artifacts']
        if args.publish:
            manifest_key = store.publish_release(manifest, run_id, args.output)
            state_key = store.upload_state_database(args.database, run_id=run_id)
            current_pointer = {
                'run_id': run_id,
                'manifest_key': manifest_key,
                'state_key': state_key,
            }
            publish_current_pointer(store, current_pointer, previous_pointer)
            diagnostics['publish_succeeded'] = True
            diagnostics['manifest_key'] = manifest_key
            diagnostics['current_pointer'] = current_pointer
            diagnostics['state_key'] = state_key
        diagnostics['completed_at'] = datetime.now(timezone.utc).isoformat()
        write_diagnostics(Path(args.diagnostics) / f'refresh-{run_id}.json', diagnostics)
        print(json.dumps({
            'run_id': run_id,
            'healthy_sources': len(validation_report['healthy_sources']),
            'published': args.publish and diagnostics['publish_succeeded'],
        }))
        return 0
    except Exception as error:
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
        print(json.dumps({
            'valid': True,
            'sources': len(sources),
            'enabled': sum(source.get('enabled', False) for source in sources),
        }))


if __name__ == '__main__':
    main()
