import hashlib
import json
import os
from pathlib import Path

import boto3
from botocore.config import Config


class ExternalStateError(Exception):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class SnapshotStore:
    def __init__(self, bucket, endpoint_url, access_key_id, secret_access_key, prefix='telclosync/state', region='auto'):
        self.bucket = bucket
        self.prefix = prefix.strip('/')
        self.client = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(retries={'max_attempts': 3}, s3={'addressing_style': 'path'}),
        )

    def object_key(self, relative_key):
        return f'{self.prefix}/{relative_key.strip("/")}' if self.prefix else relative_key.strip('/')

    def _missing_key(self, error):
        code = error.response.get('Error', {}).get('Code', '')
        return code in {'NoSuchKey', '404', 'NotFound'}

    def download_to_bytes(self, key):
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read()

    def _put_verified_bytes(self, key, payload, expected_sha256=None):
        expected = expected_sha256 or hashlib.sha256(payload).hexdigest()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ExternalStateError(f'local_hash_mismatch:{key}')
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload)
        downloaded = self.download_to_bytes(key)
        if hashlib.sha256(downloaded).hexdigest() != expected:
            raise ExternalStateError(f'remote_hash_mismatch:{key}')
        return key

    def download_state_database(self, local_path, key=None, expected_sha256=None):
        if key:
            prefix = f'{self.prefix}/' if self.prefix else ''
            object_key = key if not prefix or key.startswith(prefix) else self.object_key(key)
        else:
            # Backward-compatible fallback for state written before release pointers
            # referenced immutable database snapshots.
            object_key = self.object_key('state/teclo-sync.db')
        try:
            payload = self.download_to_bytes(object_key)
        except self.client.exceptions.ClientError as error:
            if self._missing_key(error):
                if key:
                    raise ExternalStateError(
                        f'state_snapshot_missing:{object_key}'
                    ) from error
                return False
            raise
        actual = hashlib.sha256(payload).hexdigest()
        if expected_sha256 and actual != expected_sha256:
            raise ExternalStateError(
                f'state_snapshot_hash_mismatch:{object_key}:'
                f'expected={expected_sha256}:actual={actual}'
            )
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f'{path.name}.restore-tmp')
        try:
            temporary_path.write_bytes(payload)
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return True

    def upload_state_database(self, local_path, run_id=None, relative_prefix='releases'):
        path = Path(local_path)
        expected = sha256_file(path)
        relative_key = (
            f'{relative_prefix}/{run_id}/teclo-sync.db'
            if run_id
            else 'state/teclo-sync.db'
        )
        key = self.object_key(relative_key)
        self.client.upload_file(str(path), self.bucket, key)
        downloaded = self.download_to_bytes(key)
        if hashlib.sha256(downloaded).hexdigest() != expected:
            raise ExternalStateError('state_database_remote_hash_mismatch')
        return key

    def upload_verified(self, local_path, relative_key, expected_sha256):
        actual = sha256_file(local_path)
        if actual != expected_sha256:
            raise ExternalStateError(f'local_hash_mismatch:{relative_key}')
        key = self.object_key(relative_key)
        self.client.upload_file(str(local_path), self.bucket, key)
        downloaded = self.download_to_bytes(key)
        if hashlib.sha256(downloaded).hexdigest() != expected_sha256:
            raise ExternalStateError(f'remote_hash_mismatch:{relative_key}')
        return key

    def read_pointer(self, name):
        key = self.object_key(name)
        try:
            payload = self.download_to_bytes(key)
        except self.client.exceptions.ClientError as error:
            if self._missing_key(error):
                return None
            raise
        try:
            pointer = json.loads(payload)
        except (TypeError, ValueError) as error:
            raise ExternalStateError(f'pointer_invalid_json:{name}') from error
        if not isinstance(pointer, dict):
            raise ExternalStateError(f'pointer_invalid_shape:{name}')
        return pointer

    def read_current_pointer(self):
        pointer = self.read_pointer('current/current.json')
        return pointer if pointer is not None else self.read_pointer('current.json')

    def read_previous_pointer(self):
        pointer = self.read_pointer('previous/current.json')
        return pointer if pointer is not None else self.read_pointer('previous.json')

    def write_pointer(self, name, payload):
        key = self.object_key(name)
        body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode('utf-8')
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType='application/json',
        )
        downloaded = self.download_to_bytes(key)
        if downloaded != body:
            raise ExternalStateError(f'pointer_verification_failed:{name}')
        return key

    def delete_pointer(self, name):
        self.client.delete_object(Bucket=self.bucket, Key=self.object_key(name))

    def copy_release(self, pointer, destination_prefix):
        if not isinstance(pointer, dict):
            raise ExternalStateError('release_pointer_missing')
        run_id = pointer.get('run_id')
        manifest_key = pointer.get('manifest_key')
        state_key = pointer.get('state_key')
        if not run_id or not manifest_key:
            raise ExternalStateError('release_pointer_incomplete')

        manifest_payload = self.download_to_bytes(manifest_key)
        try:
            manifest = json.loads(manifest_payload)
        except (TypeError, ValueError) as error:
            raise ExternalStateError('release_manifest_invalid_json') from error
        if not isinstance(manifest, dict) or not isinstance(manifest.get('artifacts'), list):
            raise ExternalStateError('release_manifest_invalid_shape')

        source_prefix = manifest_key.rsplit('/', 1)[0]
        target_prefix = self.object_key(f'{destination_prefix.strip("/")}/{run_id}')
        for artifact in manifest['artifacts']:
            relative_path = artifact.get('path')
            expected = artifact.get('sha256')
            if not relative_path or not expected or relative_path.startswith('/') or '..' in Path(relative_path).parts:
                raise ExternalStateError('release_artifact_manifest_invalid')
            source_key = f'{source_prefix}/{relative_path}'
            payload = self.download_to_bytes(source_key)
            actual = hashlib.sha256(payload).hexdigest()
            if actual != expected:
                raise ExternalStateError(f'release_artifact_hash_mismatch:{relative_path}')
            self._put_verified_bytes(f'{target_prefix}/{relative_path}', payload, expected)

        if not state_key:
            # Compatibility with pointers created before the database snapshot was
            # recorded in the pointer. New publications always set this explicitly.
            state_key = self.object_key('state/teclo-sync.db')
        state_payload = self.download_to_bytes(state_key)
        state_sha256 = hashlib.sha256(state_payload).hexdigest()
        expected_state_sha256 = pointer.get('state_sha256')
        if expected_state_sha256 and state_sha256 != expected_state_sha256:
            raise ExternalStateError('release_state_snapshot_hash_mismatch')
        target_state_key = f'{target_prefix}/teclo-sync.db'
        self._put_verified_bytes(target_state_key, state_payload, state_sha256)
        target_manifest_key = f'{target_prefix}/manifest.json'
        self._put_verified_bytes(
            target_manifest_key,
            manifest_payload,
            hashlib.sha256(manifest_payload).hexdigest(),
        )
        return {
            'run_id': run_id,
            'manifest_key': target_manifest_key,
            'state_key': target_state_key,
            'state_sha256': state_sha256,
        }

    def publish_release(self, manifest, run_id, output_dir, relative_prefix='releases'):
        output = Path(output_dir)
        release_prefix = f'{relative_prefix.strip("/")}/{run_id}'
        for artifact in manifest['artifacts']:
            local_path = output / artifact['path']
            self.upload_verified(
                local_path,
                f'{release_prefix}/{artifact["path"]}',
                artifact['sha256'],
            )
        payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8')
        manifest_key = self.object_key(f'{release_prefix}/manifest.json')
        self._put_verified_bytes(
            manifest_key,
            payload,
            hashlib.sha256(payload).hexdigest(),
        )
        return manifest_key

    def upload_diagnostic(self, local_path, run_id, diagnostics_prefix=None):
        path = Path(local_path)
        payload = path.read_bytes()
        configured_prefix = (diagnostics_prefix or '').strip('/')
        if configured_prefix:
            key = f'{configured_prefix}/refresh-{run_id}.json'
        else:
            key = self.object_key(f'diagnostics/refresh-{run_id}.json')
        self._put_verified_bytes(key, payload, hashlib.sha256(payload).hexdigest())
        return key

    def delete_prefix(self, relative_prefix):
        prefix = self.object_key(relative_prefix).rstrip('/') + '/'
        paginator = self.client.get_paginator('list_objects_v2')
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{'Key': item['Key']} for item in page.get('Contents', [])]
            for offset in range(0, len(objects), 1000):
                batch = objects[offset:offset + 1000]
                if not batch:
                    continue
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={'Objects': batch, 'Quiet': True},
                )
                errors = response.get('Errors', [])
                if errors:
                    raise ExternalStateError(f'prefix_delete_failed:{prefix}:{errors[0]}')
                deleted += len(batch)
        return deleted

    def object_exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False
