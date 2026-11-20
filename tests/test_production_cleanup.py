import io
import unittest
from unittest.mock import MagicMock

from scripts.cleanup_production_r2 import (
    validate_legacy_pointer_reference,
    verify_staging_copy,
)


class ProductionCleanupTests(unittest.TestCase):
    def setUp(self):
        self.manifest = (
            b'{"run_id":"run-1","artifacts":['
            b'{"path":"live.m3u8","sha256":"unused"}]}'
        )
        self.state = b"verified-snapshot"
        self.artifact = b"#EXTM3U\n"
        self.retained_release = {
            "run_id": "run-1",
            "slot": "archive",
            "pointer": {"state_sha256": "unused"},
            "objects": {
                "manifest.json": self.manifest,
                "teclo-sync.db": self.state,
                "live.m3u8": self.artifact,
            },
        }
        self.retained = {"run-1": self.retained_release}

    def make_s3(self, objects):
        store = MagicMock()

        def get_object(Bucket, Key):
            return {"Body": io.BytesIO(objects[Key])}

        store.get_object.side_effect = get_object
        return store

    def test_legacy_pointer_without_state_key_uses_verified_legacy_database(self):
        s3 = self.make_s3({
            "telclosync/state/releases/run-1/manifest.json": self.manifest,
        })
        pointer = {
            "run_id": "run-1",
            "manifest_key": "telclosync/state/releases/run-1/manifest.json",
        }

        result = validate_legacy_pointer_reference(
            s3,
            "bucket",
            "telclosync/state",
            "telclosync/state/current.json",
            pointer,
            self.retained,
            legacy_state_payload=self.state,
        )

        self.assertEqual(result["retained_slot"], "archive")
        self.assertTrue(result["used_legacy_state_fallback"])
        self.assertTrue(result["scheduled_for_removal"])

    def test_legacy_pointer_with_relative_legacy_database_key_is_accepted_by_bytes(self):
        s3 = self.make_s3({
            "telclosync/state/releases/run-1/manifest.json": self.manifest,
            "telclosync/state/state/teclo-sync.db": self.state,
        })
        pointer = {
            "run_id": "run-1",
            "manifest_key": "telclosync/state/releases/run-1/manifest.json",
            "state_key": "state/teclo-sync.db",
        }

        result = validate_legacy_pointer_reference(
            s3,
            "bucket",
            "telclosync/state",
            "telclosync/state/current.json",
            pointer,
            self.retained,
        )

        self.assertEqual(
            result["state_key"],
            "telclosync/state/state/teclo-sync.db",
        )
        self.assertFalse(result["used_legacy_state_fallback"])

    def test_legacy_pointer_mismatched_database_is_preserved(self):
        s3 = self.make_s3({
            "telclosync/state/releases/run-1/manifest.json": self.manifest,
            "telclosync/state/state/teclo-sync.db": b"another-snapshot",
        })
        pointer = {
            "run_id": "run-1",
            "manifest_key": "telclosync/state/releases/run-1/manifest.json",
            "state_key": "state/teclo-sync.db",
        }

        with self.assertRaisesRegex(RuntimeError, "legacy_pointer_state_not_reconciled"):
            validate_legacy_pointer_reference(
                s3,
                "bucket",
                "telclosync/state",
                "telclosync/state/current.json",
                pointer,
                self.retained,
            )

    def test_legacy_pointer_mismatched_manifest_is_preserved(self):
        s3 = self.make_s3({
            "telclosync/state/releases/run-1/manifest.json": b'{"run_id":"different"}',
            "telclosync/state/state/teclo-sync.db": self.state,
        })
        pointer = {
            "run_id": "run-1",
            "manifest_key": "telclosync/state/releases/run-1/manifest.json",
            "state_key": "state/teclo-sync.db",
        }

        with self.assertRaisesRegex(RuntimeError, "legacy_pointer_manifest_not_reconciled"):
            validate_legacy_pointer_reference(
                s3,
                "bucket",
                "telclosync/state",
                "telclosync/state/current.json",
                pointer,
                self.retained,
            )

    def test_staging_copy_without_legacy_state_file_requires_and_accepts_exact_legacy_state(self):
        staging = {
            "manifest.json": self.manifest,
            "live.m3u8": self.artifact,
        }

        total = verify_staging_copy(
            "run-1",
            staging,
            self.retained_release,
            legacy_state_payload=self.state,
        )

        self.assertEqual(total, len(self.manifest) + len(self.artifact))

    def test_staging_copy_without_legacy_state_file_rejects_unverified_state(self):
        staging = {
            "manifest.json": self.manifest,
            "live.m3u8": self.artifact,
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "legacy_state_snapshot_not_verified_for_staging_release",
        ):
            verify_staging_copy(
                "run-1",
                staging,
                self.retained_release,
                legacy_state_payload=b"different-state",
            )

    def test_staging_copy_rejects_changed_artifact(self):
        staging = {
            "manifest.json": self.manifest,
            "live.m3u8": b"#EXTM3U\n# changed",
            "teclo-sync.db": self.state,
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "staging_copy_differs_from_retained_release",
        ):
            verify_staging_copy("run-1", staging, self.retained_release)

    def test_staging_copy_rejects_unexpected_object(self):
        staging = {
            "manifest.json": self.manifest,
            "live.m3u8": self.artifact,
            "teclo-sync.db": self.state,
            "untracked.bin": b"not in manifest",
        }

        with self.assertRaisesRegex(RuntimeError, "staging_object_set_mismatch"):
            verify_staging_copy("run-1", staging, self.retained_release)


if __name__ == "__main__":
    unittest.main()
