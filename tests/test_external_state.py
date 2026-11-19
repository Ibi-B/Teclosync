import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from teclosync.external_state import SnapshotStore, ExternalStateError


class ExternalStateTests(unittest.TestCase):
    def store(self):
        store = SnapshotStore(
            'test-bucket',
            'https://example.r2.cloudflarestorage.com',
            'access',
            'secret',
            prefix='telclosync/state',
        )
        store.client = MagicMock()
        return store

    def test_upload_rejects_local_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact.m3u8'
            path.write_text('#EXTM3U\n')
            store = self.store()
            with self.assertRaises(ExternalStateError):
                store.upload_verified(path, 'current/artifact.m3u8', 'invalid')

    def test_upload_verifies_remote_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'artifact.m3u8'
            content = b'#EXTM3U\n'
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            store = self.store()
            store.client.get_object.return_value = {'Body': io.BytesIO(content)}
            key = store.upload_verified(path, 'current/artifact.m3u8', digest)
            self.assertEqual(key, 'telclosync/state/current/artifact.m3u8')

    def test_state_database_upload_uses_immutable_run_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            content = b'sqlite-state-snapshot'
            path.write_bytes(content)
            store = self.store()
            store.client.get_object.return_value = {'Body': io.BytesIO(content)}

            key = store.upload_state_database(path, run_id='run-123')

            self.assertEqual(
                key,
                'telclosync/state/releases/run-123/teclo-sync.db',
            )
            store.client.upload_file.assert_called_once_with(
                str(path),
                'test-bucket',
                key,
            )

    def test_state_database_restore_uses_pointer_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'restored' / 'state.db'
            content = b'previous-good-sqlite'
            store = self.store()
            key = 'telclosync/state/releases/previous-run/teclo-sync.db'
            store.client.get_object.return_value = {'Body': io.BytesIO(content)}

            restored = store.download_state_database(path, key=key)

            self.assertTrue(restored)
            self.assertEqual(path.read_bytes(), content)
            store.client.get_object.assert_called_once_with(
                Bucket='test-bucket',
                Key=key,
            )

    def test_manifest_verification_failure(self):
        store = self.store()
        store.client.get_object.return_value = {'Body': io.BytesIO(b'{}')}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExternalStateError):
                store.publish_release({'artifacts': []}, 'run-1', directory)

    def test_pointer_write_verifies_exact_payload(self):
        store = self.store()
        payload = {'run_id': 'run-1', 'manifest_key': 'releases/run-1/manifest.json'}
        body = json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ).encode('utf-8')
        store.client.get_object.return_value = {'Body': io.BytesIO(body)}

        key = store.write_pointer('current.json', payload)

        self.assertEqual(key, 'telclosync/state/current.json')
        store.client.put_object.assert_called_once_with(
            Bucket='test-bucket',
            Key='telclosync/state/current.json',
            Body=body,
            ContentType='application/json',
        )


    def test_restore_rejects_hash_mismatch_without_replacing_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            path.write_bytes(b'known-good-local-state')
            store = self.store()
            store.client.get_object.return_value = {
                'Body': io.BytesIO(b'corrupted-remote-state')
            }

            with self.assertRaisesRegex(ExternalStateError, 'state_snapshot_hash_mismatch'):
                store.download_state_database(
                    path,
                    key='telclosync/state/releases/run-1/teclo-sync.db',
                    expected_sha256=hashlib.sha256(b'expected-state').hexdigest(),
                )

            self.assertEqual(path.read_bytes(), b'known-good-local-state')

    def test_diagnostic_is_uploaded_to_explicit_diagnostics_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'refresh.json'
            payload = b'{"run_id":"run-1","ok":true}'
            path.write_bytes(payload)
            store = self.store()
            store.client.get_object.return_value = {'Body': io.BytesIO(payload)}

            key = store.upload_diagnostic(
                path,
                'run-1',
                diagnostics_prefix='telclosync/diagnostics',
            )

            self.assertEqual(key, 'telclosync/diagnostics/refresh-run-1.json')
            store.client.put_object.assert_called_once_with(
                Bucket='test-bucket',
                Key=key,
                Body=payload,
            )

    def test_copy_release_verifies_and_preserves_manifest_artifacts_and_state(self):
        class MemoryObjectClient:
            class exceptions:
                class ClientError(Exception):
                    pass

            def __init__(self, objects):
                self.objects = dict(objects)

            def get_object(self, Bucket, Key):
                if Key not in self.objects:
                    error = self.exceptions.ClientError(Key)
                    error.response = {'Error': {'Code': 'NoSuchKey'}}
                    raise error
                return {'Body': io.BytesIO(self.objects[Key])}

            def put_object(self, Bucket, Key, Body, **kwargs):
                self.objects[Key] = Body

        artifact = b'#EXTM3U\\n#EXTINF:-1,Station\\nhttps://example.test/live\\n'
        state = b'known-good-sqlite-snapshot'
        manifest = {
            'run_id': 'run-9',
            'artifacts': [{
                'id': 'live-global',
                'path': 'live-global.m3u8',
                'sha256': hashlib.sha256(artifact).hexdigest(),
            }],
        }
        manifest_payload = json.dumps(manifest).encode()
        store = self.store()
        store.client = MemoryObjectClient({
            'telclosync/state/releases/run-9/manifest.json': manifest_payload,
            'telclosync/state/releases/run-9/live-global.m3u8': artifact,
            'telclosync/state/releases/run-9/teclo-sync.db': state,
        })
        pointer = {
            'run_id': 'run-9',
            'manifest_key': 'telclosync/state/releases/run-9/manifest.json',
            'state_key': 'telclosync/state/releases/run-9/teclo-sync.db',
            'state_sha256': hashlib.sha256(state).hexdigest(),
        }

        archived = store.copy_release(pointer, 'archive')

        self.assertEqual(archived['manifest_key'], 'telclosync/state/archive/run-9/manifest.json')
        self.assertEqual(archived['state_key'], 'telclosync/state/archive/run-9/teclo-sync.db')
        self.assertEqual(
            store.client.objects['telclosync/state/archive/run-9/live-global.m3u8'],
            artifact,
        )
        self.assertEqual(
            store.client.objects[archived['state_key']],
            state,
        )


if __name__ == '__main__':
    unittest.main()
