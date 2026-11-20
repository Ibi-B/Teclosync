import io
import unittest
from unittest.mock import MagicMock

from scripts.cleanup_production_r2 import (
    plan_cleanup,
    sha256,
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


    def build_memory_production(self, corrupt_legacy_manifest=False):
        class MemoryPaginator:
            def __init__(self, store):
                self.store = store

            def paginate(self, Bucket, Prefix):
                items = [
                    {"Key": key, "Size": len(value)}
                    for key, value in self.store.objects.items()
                    if key.startswith(Prefix)
                ]
                yield {"Contents": items}

        class MemoryS3:
            def __init__(self, objects):
                self.objects = dict(objects)

            def get_object(self, Bucket, Key):
                return {"Body": io.BytesIO(self.objects[Key])}

            def get_paginator(self, name):
                return MemoryPaginator(self)

        base = "telclosync/state"
        objects = {}
        releases = {}

        def add_release(slot, run_id, add_staging_db=True):
            state = f"database:{run_id}".encode()
            artifact = f"#EXTM3U\\n# {run_id}\\n".encode()
            manifest = (
                __import__("json").dumps({
                    "run_id": run_id,
                    "artifacts": [{
                        "path": "live.m3u8",
                        "sha256": sha256(artifact),
                        "bytes": len(artifact),
                    }],
                }, separators=(",", ":")).encode()
            )
            slot_prefix = f"{base}/{slot}/{run_id}/"
            objects[slot_prefix + "manifest.json"] = manifest
            objects[slot_prefix + "teclo-sync.db"] = state
            objects[slot_prefix + "live.m3u8"] = artifact
            pointer = {
                "run_id": run_id,
                "manifest_key": slot_prefix + "manifest.json",
                "state_key": slot_prefix + "teclo-sync.db",
                "state_sha256": sha256(state),
            }
            if slot in {"current", "previous"}:
                objects[f"{base}/{slot}/current.json"] = __import__("json").dumps(pointer).encode()
            else:
                objects[f"{base}/archive/{run_id}/pointer.json"] = __import__("json").dumps(pointer).encode()

            staging_prefix = f"{base}/releases/{run_id}/"
            objects[staging_prefix + "manifest.json"] = manifest
            objects[staging_prefix + "live.m3u8"] = artifact
            if add_staging_db:
                objects[staging_prefix + "teclo-sync.db"] = state
            releases[run_id] = {
                "pointer": pointer,
                "state": state,
                "manifest": manifest,
                "artifact": artifact,
                "staging_prefix": staging_prefix,
            }
            return pointer

        current = add_release("current", "20261119T234644Z")
        previous = add_release("previous", "20261119T224927Z")
        archive = add_release("archive", "20261118T195146Z", add_staging_db=False)
        objects[f"{base}/state/teclo-sync.db"] = archive["state"]
        legacy_pointer = {
            "run_id": archive["pointer"]["run_id"],
            "manifest_key": archive["staging_prefix"] + "manifest.json",
        }
        if corrupt_legacy_manifest:
            objects[legacy_pointer["manifest_key"]] = b'{"run_id":"changed"}'
        objects[f"{base}/current.json"] = __import__("json").dumps(legacy_pointer).encode()
        audit = {"read_only": True, "production_prefix": base}
        return MemoryS3(objects), audit, releases

    def test_cleanup_plan_handles_older_pointer_format_and_deletes_only_mirrors(self):
        store, audit, releases = self.build_memory_production()

        plan = plan_cleanup(store, "bucket", "telclosync/state", audit)

        self.assertEqual(plan["delete_object_count"], 9)
        self.assertIn("telclosync/state/current.json", plan["delete_object_keys"])
        self.assertEqual(len(plan["staging_releases"]), 3)
        self.assertFalse(plan["skipped_objects"])
        self.assertIn(
            {"key": "telclosync/state/state/teclo-sync.db", "bytes": len(releases["20261118T195146Z"]["state"])},
            plan["legacy_state_objects_preserved"],
        )
        self.assertNotIn("telclosync/state/state/teclo-sync.db", plan["delete_object_keys"])

    def test_one_unreconciled_legacy_pointer_does_not_block_verified_sibling_cleanup(self):
        store, audit, releases = self.build_memory_production(corrupt_legacy_manifest=True)

        plan = plan_cleanup(store, "bucket", "telclosync/state", audit)

        self.assertNotIn("telclosync/state/current.json", plan["delete_object_keys"])
        self.assertTrue(any(
            item["object"] == "telclosync/state/current.json"
            for item in plan["skipped_objects"]
        ))
        self.assertEqual(
            {item["run_id"] for item in plan["staging_releases"]},
            {"20261119T234644Z", "20261119T224927Z"},
        )
        self.assertNotIn(
            "telclosync/state/releases/20261118T195146Z/manifest.json",
            plan["delete_object_keys"],
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
