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


if __name__ == '__main__':
    unittest.main()
