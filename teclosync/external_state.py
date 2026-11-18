import hashlib
import json
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
        self.prefix = prefix.rstrip('/')
        self.client = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(retries={'max_attempts': 3}, s3={'addressing_style': 'path'}),
        )

    def object_key(self, relative_key):
        return f'{self.prefix}/{relative_key}'

    def _missing_key(self, error):
        code = error.response.get('Error', {}).get('Code', '')
        return code in {'NoSuchKey', '404', 'NotFound'}

    def download_to_bytes(self, key):
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read()

    def download_state_database(self, local_path, key=None):
        if key:
            prefix = f'{self.prefix}/'
            object_key = key if key.startswith(prefix) else self.object_key(key)
        else:
            # Backward-compatible fallback for state written before release pointers
            # referenced immutable database snapshots.
            object_key = self.object_key('state/teclo-sync.db')
        try:
            payload = self.download_to_bytes(object_key)
        except self.client.exceptions.ClientError as error:
            if self._missing_key(error):
                return False
            raise
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return True

    def upload_state_database(self, local_path, run_id=None):
        path = Path(local_path)
        expected = sha256_file(path)
        relative_key = (
            f'releases/{run_id}/teclo-sync.db'
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
        return json.loads(payload)

    def write_pointer(self, name, payload):
        key = self.object_key(name)
        body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode('utf-8')
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType='application/json')
        downloaded = self.download_to_bytes(key)
        if downloaded != body:
            raise ExternalStateError(f'pointer_verification_failed:{name}')
        return key

    def publish_release(self, manifest, run_id, output_dir):
        output = Path(output_dir)
        for artifact in manifest['artifacts']:
            local_path = output / artifact['path']
            self.upload_verified(local_path, f'releases/{run_id}/{artifact["path"]}', artifact['sha256'])
        payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8')
        manifest_key = self.object_key(f'releases/{run_id}/manifest.json')
        self.client.put_object(Bucket=self.bucket, Key=manifest_key, Body=payload, ContentType='application/json')
        downloaded = self.download_to_bytes(manifest_key)
        if downloaded != payload:
            raise ExternalStateError('manifest_verification_failed')
        return manifest_key

    def object_exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False
