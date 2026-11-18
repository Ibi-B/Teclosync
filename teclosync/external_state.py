import hashlib
import io
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
        self.client = boto3.client('s3', endpoint_url=endpoint_url, region_name=region, aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key, config=Config(retries={'max_attempts': 3}, s3={'addressing_style': 'path'}))

    def object_key(self, relative_key):
        return f'{self.prefix}/{relative_key}'

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

    def download_to_bytes(self, key):
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read()

    def object_exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False

    def publish_manifest(self, manifest, run_id):
        payload = json.dumps(manifest, indent=2, ensure_ascii=False).encode('utf-8')
        key = self.object_key(f'current/manifest-{run_id}.json')
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload, ContentType='application/json')
        downloaded = self.download_to_bytes(key)
        if downloaded != payload:
            raise ExternalStateError('manifest_verification_failed')
        return key

    def delete_object(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def cleanup_superseded(self, manifest, previous_manifest):
        if not previous_manifest:
            return []
        current_paths = {artifact['path'] for artifact in manifest['artifacts']}
        removed = []
        for artifact in previous_manifest.get('artifacts', []):
            path = artifact['path']
            if path in current_paths:
                continue
            key = self.object_key(f'archive/{path}')
            if self.object_exists(key):
                self.delete_object(key)
                removed.append(key)
        return removed
