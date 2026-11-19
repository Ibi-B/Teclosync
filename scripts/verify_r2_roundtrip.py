#!/usr/bin/env python3
"""Verify real R2 snapshot upload, restore, rotation, archive, and rollback.

This script is deliberately restricted to a unique telclosync/verification/*
prefix. It never reads or changes the production current/previous pointers.
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from teclosync.external_state import SnapshotStore, sha256_file
from teclosync.retention import StateRotator


def build_test_release(store, root, run_id, value):
    output = root / run_id
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / 'live-global.m3u8'
    artifact.write_text(
        '#EXTM3U\n'
        f'#EXTINF:-1 tvg-id="verify.{value}" group-title="Verification",Test {value}\n'
        f'https://example.invalid/verification/{value}\n',
        encoding='utf-8',
    )
    digest = sha256_file(artifact)
    manifest = {
        'schema_version': 1,
        'run_id': run_id,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'artifacts': [{
            'id': 'live-global',
            'path': artifact.name,
            'sha256': digest,
            'bytes': artifact.stat().st_size,
            'channel_count': 1,
        }],
        'sources': [],
    }
    manifest_key = store.publish_release(
        manifest,
        run_id,
        output,
        relative_prefix='staging',
    )

    database_path = output / 'teclo-sync.db'
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            'CREATE TABLE verification_state (release_id TEXT PRIMARY KEY, value INTEGER NOT NULL)'
        )
        connection.execute(
            'INSERT INTO verification_state (release_id, value) VALUES (?, ?)',
            (run_id, value),
        )
        connection.commit()
    state_key = store.upload_state_database(
        database_path,
        run_id=run_id,
        relative_prefix='staging',
    )
    return {
        'run_id': run_id,
        'manifest_key': manifest_key,
        'state_key': state_key,
        'state_sha256': sha256_file(database_path),
    }


class AmbiguousCurrentWrite:
    """Simulate an R2 PUT that commits but whose acknowledgement is lost."""

    def __init__(self, store, failing_run_id):
        self.store = store
        self.failing_run_id = failing_run_id
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.store, name)

    def write_pointer(self, name, payload):
        result = self.store.write_pointer(name, payload)
        if (
            name == 'current/current.json'
            and payload.get('run_id') == self.failing_run_id
            and not self.failed
        ):
            self.failed = True
            raise RuntimeError('simulated_ambiguous_current_pointer_write')
        return result


def assert_release_is_readable(store, pointer, expected_value):
    manifest = json.loads(store.download_to_bytes(pointer['manifest_key']))
    if manifest.get('run_id') != pointer['run_id']:
        raise RuntimeError(f'manifest_run_id_mismatch:{pointer["run_id"]}')
    for artifact in manifest['artifacts']:
        payload = store.download_to_bytes(
            f'{pointer["manifest_key"].rsplit("/", 1)[0]}/{artifact["path"]}'
        )
        if hashlib.sha256(payload).hexdigest() != artifact['sha256']:
            raise RuntimeError(f'artifact_checksum_mismatch:{artifact["path"]}')

    with tempfile.TemporaryDirectory() as directory:
        restored = Path(directory) / 'restored.db'
        store.download_state_database(
            restored,
            key=pointer['state_key'],
            expected_sha256=pointer['state_sha256'],
        )
        with sqlite3.connect(restored) as connection:
            row = connection.execute(
                'SELECT value FROM verification_state WHERE release_id = ?',
                (pointer['run_id'],),
            ).fetchone()
        if row != (expected_value,):
            raise RuntimeError(f'state_restore_mismatch:{pointer["run_id"]}:{row}')


def main():
    configured_prefix = os.environ.get('TECLOSYNC_S3_PREFIX', '').strip('/')
    if not configured_prefix.startswith('telclosync/verification/'):
        raise SystemExit(
            'Refusing to run: TECLOSYNC_S3_PREFIX must be a unique '
            'telclosync/verification/<run-id> prefix.'
        )
    if configured_prefix.count('/') < 2:
        raise SystemExit('Refusing to run: verification prefix must include a unique run ID.')

    store = SnapshotStore(
        bucket=os.environ['TECLOSYNC_S3_BUCKET'],
        endpoint_url=os.environ['TECLOSYNC_S3_ENDPOINT'],
        access_key_id=os.environ['TECLOSYNC_S3_ACCESS_KEY_ID'],
        secret_access_key=os.environ['TECLOSYNC_S3_SECRET_ACCESS_KEY'],
        prefix=configured_prefix,
        region=os.environ.get('TECLOSYNC_S3_REGION', 'auto'),
    )
    result = {
        'verification_prefix': configured_prefix,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'checks': [],
        'success': False,
    }

    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rotator = StateRotator(store)
            releases = [
                build_test_release(store, root, f'check-{index}', index)
                for index in range(1, 5)
            ]

            first = rotator.promote(releases[0])
            second = rotator.promote(releases[1])
            third = rotator.promote(releases[2])

            current = store.read_current_pointer()
            previous = store.read_previous_pointer()
            if current != third['current'] or previous != third['previous']:
                raise RuntimeError('current_previous_pointer_rotation_mismatch')
            result['checks'].append('current and previous release rotation')

            if not store.object_exists(
                store.object_key('archive/check-1/manifest.json')
            ):
                raise RuntimeError('older_previous_release_not_archived')
            if not store.object_exists(
                store.object_key('archive/check-1/teclo-sync.db')
            ):
                raise RuntimeError('older_previous_state_not_archived')
            if not store.object_exists(
                store.object_key('archive/check-1/pointer.json')
            ):
                raise RuntimeError('archive_pointer_not_written')
            result['checks'].append('complete previous release archived')

            assert_release_is_readable(store, current, 3)
            assert_release_is_readable(store, previous, 2)
            result['checks'].append('current and previous artifact/state restore with SHA-256')

            failing_store = AmbiguousCurrentWrite(store, 'check-4')
            try:
                StateRotator(failing_store).promote(releases[3])
            except RuntimeError as error:
                if 'simulated_ambiguous_current_pointer_write' not in str(error):
                    raise
            else:
                raise RuntimeError('ambiguous_pointer_failure_was_not_injected')

            if store.read_current_pointer() != current:
                raise RuntimeError('current_pointer_rollback_failed')
            if store.read_previous_pointer() != previous:
                raise RuntimeError('previous_pointer_rollback_failed')
            result['checks'].append('ambiguous current write rolls back both pointers')

            # Remove only the orphan copies created by the intentionally failed
            # promotion. The three verified live/archive snapshots are retained
            # until the complete verification has succeeded.
            store.delete_prefix('current/check-4')
            store.delete_prefix('previous/check-3')

        result['success'] = True
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
        result['cleanup'] = 'verification prefix removed'
        store.delete_prefix('')
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
        Path('state/diagnostics').mkdir(parents=True, exist_ok=True)
        Path('state/diagnostics/r2-verification.json').write_text(
            json.dumps(result, indent=2),
            encoding='utf-8',
        )
        print(json.dumps(result), file=sys.stderr)
        raise SystemExit(1)

    Path('state/diagnostics').mkdir(parents=True, exist_ok=True)
    Path('state/diagnostics/r2-verification.json').write_text(
        json.dumps(result, indent=2),
        encoding='utf-8',
    )
    print(json.dumps(result))


if __name__ == '__main__':
    main()
