import unittest

from scripts.cleanup_production_r2 import validate_legacy_pointer_reference, verify_staging_copy


class ProductionCleanupTests(unittest.TestCase):
    def setUp(self):
        self.manifest = (
            b'{"run_id":"run-1","artifacts":['
            b'{"path":"live.m3u8","sha256":"unused"}]}'
        )
        self.state = b"verified-snapshot"
        self.artifact = b"#EXTM3U\n"
        self.retained = {
            "run_id": "run-1",
            "objects": {
                "manifest.json": self.manifest,
                "teclo-sync.db": self.state,
                "live.m3u8": self.artifact,
            },
        }


    def test_legacy_pointer_must_target_exact_retained_release_objects(self):
        retained = {
            "run-1": {
                "slot": "archive",
                "pointer": {"state_sha256": "a" * 64},
            }
        }
        pointer = {
            "run_id": "run-1",
            "manifest_key": "telclosync/state/releases/run-1/manifest.json",
            "state_key": "telclosync/state/releases/run-1/teclo-sync.db",
            "state_sha256": "a" * 64,
        }

        result = validate_legacy_pointer_reference(
            "telclosync/state",
            "telclosync/state/current.json",
            pointer,
            retained,
        )

        self.assertEqual(result["retained_slot"], "archive")
        self.assertTrue(result["scheduled_for_removal"])

    def test_legacy_pointer_cannot_mix_manifest_and_state_release_paths(self):
        retained = {
            "run-1": {
                "slot": "archive",
                "pointer": {"state_sha256": "a" * 64},
            }
        }
        pointer = {
            "run_id": "run-1",
            "manifest_key": "telclosync/state/releases/run-1/manifest.json",
            "state_key": "telclosync/state/archive/run-1/teclo-sync.db",
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "legacy_pointer_reference_path_mismatch",
        ):
            validate_legacy_pointer_reference(
                "telclosync/state",
                "telclosync/state/current.json",
                pointer,
                retained,
            )

    def test_staging_copy_without_legacy_state_file_requires_and_accepts_exact_legacy_state(self):
        staging = {
            "manifest.json": self.manifest,
            "live.m3u8": self.artifact,
        }

        total = verify_staging_copy(
            "run-1",
            staging,
            self.retained,
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
                self.retained,
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
            verify_staging_copy("run-1", staging, self.retained)

    def test_staging_copy_rejects_unexpected_object(self):
        staging = {
            "manifest.json": self.manifest,
            "live.m3u8": self.artifact,
            "teclo-sync.db": self.state,
            "untracked.bin": b"not in manifest",
        }

        with self.assertRaisesRegex(RuntimeError, "staging_object_set_mismatch"):
            verify_staging_copy("run-1", staging, self.retained)


if __name__ == "__main__":
    unittest.main()
