#!/usr/bin/env python3
"""Safely remove only verified, unreferenced legacy R2 pointer and staging objects."""
import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path, PurePosixPath

import boto3
from botocore.config import Config


AUDIT_REPORT = Path("state/diagnostics/production-r2-audit.json")
RESULT_REPORT = Path("state/diagnostics/production-r2-cleanup.json")


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def client_from_env():
    required = (
        "TECLOSYNC_S3_BUCKET",
        "TECLOSYNC_S3_ENDPOINT",
        "TECLOSYNC_S3_ACCESS_KEY_ID",
        "TECLOSYNC_S3_SECRET_ACCESS_KEY",
        "TECLOSYNC_S3_PREFIX",
    )
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError("missing_required_environment:" + ",".join(missing))
    return boto3.client(
        "s3",
        endpoint_url=os.environ["TECLOSYNC_S3_ENDPOINT"],
        region_name=os.environ.get("TECLOSYNC_S3_REGION", "auto"),
        aws_access_key_id=os.environ["TECLOSYNC_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["TECLOSYNC_S3_SECRET_ACCESS_KEY"],
        config=Config(retries={"max_attempts": 3}, s3={"addressing_style": "path"}),
    )


def read_object(s3, bucket, key):
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def list_objects(s3, bucket, prefix):
    paginator = s3.get_paginator("list_objects_v2")
    result = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        result.extend(page.get("Contents", []))
    return result


def safe_artifact_path(value):
    if not isinstance(value, str) or not value:
        raise RuntimeError("release_artifact_path_missing")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."} or str(path) != value:
        raise RuntimeError(f"unsafe_release_artifact_path:{value}")
    return value


def verify_retained_release(s3, bucket, base, slot, pointer):
    if not isinstance(pointer, dict):
        raise RuntimeError(f"{slot}_pointer_invalid")
    run_id = pointer.get("run_id")
    manifest_key = pointer.get("manifest_key")
    state_key = pointer.get("state_key")
    state_hash = pointer.get("state_sha256")
    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        raise RuntimeError(f"{slot}_pointer_run_id_invalid")
    expected_prefix = f"{base}/{slot}/{run_id}/"
    if manifest_key != expected_prefix + "manifest.json":
        raise RuntimeError(f"{slot}_manifest_path_mismatch")
    if state_key != expected_prefix + "teclo-sync.db":
        raise RuntimeError(f"{slot}_state_path_mismatch")
    if not isinstance(state_hash, str) or len(state_hash) != 64:
        raise RuntimeError(f"{slot}_state_hash_missing")

    manifest_payload = read_object(s3, bucket, manifest_key)
    try:
        manifest = json.loads(manifest_payload)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{slot}_manifest_invalid_json") from error
    if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
        raise RuntimeError(f"{slot}_manifest_run_id_mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError(f"{slot}_manifest_artifacts_missing")

    retained_objects = {
        "manifest.json": manifest_payload,
        "teclo-sync.db": read_object(s3, bucket, state_key),
    }
    if sha256(retained_objects["teclo-sync.db"]) != state_hash:
        raise RuntimeError(f"{slot}_state_hash_mismatch")

    for artifact in artifacts:
        relative = safe_artifact_path(artifact.get("path"))
        if relative in retained_objects:
            raise RuntimeError(f"{slot}_manifest_artifact_path_collision:{relative}")
        payload = read_object(s3, bucket, expected_prefix + relative)
        if sha256(payload) != artifact.get("sha256"):
            raise RuntimeError(f"{slot}_artifact_hash_mismatch:{relative}")
        expected_bytes = artifact.get("bytes")
        if expected_bytes is not None and len(payload) != expected_bytes:
            raise RuntimeError(f"{slot}_artifact_size_mismatch:{relative}")
        retained_objects[relative] = payload

    return {
        "run_id": run_id,
        "slot": slot,
        "pointer": pointer,
        "objects": retained_objects,
        "total_bytes": sum(len(value) for value in retained_objects.values()),
    }


def verify_staging_copy(run_id, staging_objects, retained_release, legacy_state_payload=None):
    if not retained_release or retained_release.get("run_id") != run_id:
        raise RuntimeError(f"staging_release_has_no_retained_copy:{run_id}")
    retained = retained_release["objects"]
    manifest = json.loads(retained["manifest.json"])
    artifact_paths = {safe_artifact_path(item.get("path")) for item in manifest["artifacts"]}
    expected_without_db = {"manifest.json"} | artifact_paths
    expected_with_db = expected_without_db | {"teclo-sync.db"}
    actual = set(staging_objects)
    if actual not in (expected_without_db, expected_with_db):
        missing = sorted(expected_without_db - actual)
        unexpected = sorted(actual - expected_with_db)
        raise RuntimeError(
            f"staging_object_set_mismatch:{run_id}:missing={missing}:unexpected={unexpected}"
        )
    if "teclo-sync.db" not in actual and legacy_state_payload != retained["teclo-sync.db"]:
        raise RuntimeError(f"legacy_state_snapshot_not_verified_for_staging_release:{run_id}")
    for relative, payload in staging_objects.items():
        if relative not in retained or payload != retained[relative]:
            raise RuntimeError(f"staging_copy_differs_from_retained_release:{run_id}:{relative}")
    return sum(len(payload) for payload in staging_objects.values())


def validate_initial_audit(report, base):
    if report.get("success") is not True or report.get("read_only") is not True:
        raise RuntimeError("successful_read_only_production_audit_required")
    if report.get("production_prefix", "").strip("/") != base:
        raise RuntimeError("production_audit_prefix_mismatch")
    for slot in ("current", "previous"):
        release = report.get(slot) or {}
        state = release.get("database_and_catalogue") or {}
        catalogue = state.get("catalogue") or {}
        if release.get("state_sha256_verified") is not True:
            raise RuntimeError(f"{slot}_state_integrity_not_verified")
        if catalogue.get("matches_database_keys") is not True:
            raise RuntimeError(f"{slot}_catalogue_reconciliation_not_verified")
    archive = report.get("archive") or {}
    if archive.get("all_pointer_references_scoped_to_archive") is not True:
        raise RuntimeError("archive_pointer_scope_not_verified")


def plan_cleanup(s3, bucket, base, audit_report):
    validate_initial_audit(audit_report, base)
    root = base + "/"
    objects = list_objects(s3, bucket, root)
    keys = {item["Key"] for item in objects}

    current_key = f"{base}/current/current.json"
    previous_key = f"{base}/previous/current.json"
    if current_key not in keys or previous_key not in keys:
        raise RuntimeError("canonical_current_and_previous_pointers_required")

    retained = {}
    pointer_payloads = {}
    for slot, key in (("current", current_key), ("previous", previous_key)):
        pointer = json.loads(read_object(s3, bucket, key))
        if pointer.get("run_id") != (audit_report.get(slot) or {}).get("run_id"):
            raise RuntimeError(f"{slot}_pointer_changed_since_audit")
        release = verify_retained_release(s3, bucket, base, slot, pointer)
        retained[release["run_id"]] = release
        pointer_payloads[key] = pointer

    archive_prefix = f"{base}/archive/"
    archive_pointer_keys = sorted(
        key for key in keys
        if key.startswith(archive_prefix) and key.endswith("/pointer.json")
    )
    for key in archive_pointer_keys:
        pointer = json.loads(read_object(s3, bucket, key))
        run_id = pointer.get("run_id")
        expected_key = f"{archive_prefix}{run_id}/pointer.json"
        if key != expected_key:
            raise RuntimeError(f"archive_pointer_path_mismatch:{key}")
        if run_id in retained:
            raise RuntimeError(f"duplicate_retained_run_id:{run_id}")
        release = verify_retained_release(s3, bucket, base, "archive", pointer)
        retained[run_id] = release
        pointer_payloads[key] = pointer

    if not archive_pointer_keys:
        raise RuntimeError("archive_pointer_required_for_staging_cleanup")

    # Inspect every JSON pointer-like object, not only the expected pointer names.
    # Only the two known root-level compatibility pointers may refer to releases/,
    # and those are removed after their run IDs are proven to exist in retained slots.
    legacy_keys = {
        key for key in (f"{base}/current.json", f"{base}/previous.json")
        if key in keys
    }
    legacy_pointer_reports = []
    for key in sorted(legacy_keys):
        try:
            payload = json.loads(read_object(s3, bucket, key))
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"legacy_pointer_invalid_json:{key}") from error
        if not isinstance(payload, dict) or not payload.get("manifest_key") or not payload.get("state_key"):
            raise RuntimeError(f"legacy_pointer_invalid_shape:{key}")
        run_id = payload.get("run_id")
        if run_id not in retained:
            raise RuntimeError(f"legacy_pointer_release_not_retained:{key}:{run_id}")
        legacy_pointer_reports.append({
            "key": key,
            "run_id": run_id,
            "retained_slot": retained[run_id]["slot"],
            "scheduled_for_removal": True,
        })

    for item in objects:
        key = item["Key"]
        if not key.endswith(".json") or key in legacy_keys:
            continue
        try:
            payload = json.loads(read_object(s3, bucket, key))
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not payload.get("manifest_key") or not payload.get("state_key"):
            continue
        points_to_staging = (
            str(payload["manifest_key"]).startswith(f"{base}/releases/")
            or str(payload["state_key"]).startswith(f"{base}/releases/")
        )
        if points_to_staging:
            raise RuntimeError(f"non_legacy_pointer_references_staging:{key}")

    staging_prefix = f"{base}/releases/"
    staging_groups = defaultdict(dict)
    for item in objects:
        key = item["Key"]
        if not key.startswith(staging_prefix):
            continue
        relative = key[len(staging_prefix):]
        parts = relative.split("/")
        if len(parts) != 2 or not all(parts):
            raise RuntimeError(f"unexpected_staging_object_path:{key}")
        run_id, filename = parts
        if filename in staging_groups[run_id]:
            raise RuntimeError(f"duplicate_staging_object:{key}")
        staging_groups[run_id][filename] = read_object(s3, bucket, key)

    legacy_state_key = f"{base}/state/teclo-sync.db"
    legacy_state_payload = (
        read_object(s3, bucket, legacy_state_key)
        if legacy_state_key in keys else None
    )
    staging_reports = []
    staging_delete_keys = []
    for run_id, staged_objects in sorted(staging_groups.items()):
        retained_release = retained.get(run_id)
        staged_bytes = verify_staging_copy(
            run_id, staged_objects, retained_release, legacy_state_payload
        )
        staging_reports.append({
            "run_id": run_id,
            "retained_slot": retained_release["slot"],
            "object_count": len(staged_objects),
            "bytes": staged_bytes,
            "byte_for_byte_match": True,
        })
        staging_delete_keys.extend(
            f"{staging_prefix}{run_id}/{relative}"
            for relative in sorted(staged_objects)
        )

    # The old non-run-scoped database is intentionally preserved as a forensic
    # fallback copy. Runtime code no longer restores it when canonical pointers
    # are absent, and it is outside this narrowly scoped cleanup operation.
    legacy_state_prefix = f"{base}/state/"
    legacy_state_objects = [
        {"key": item["Key"], "bytes": int(item.get("Size", 0) or 0)}
        for item in objects if item["Key"].startswith(legacy_state_prefix)
    ]

    return {
        "legacy_pointers": legacy_pointer_reports,
        "staging_releases": staging_reports,
        "staging_delete_keys": staging_delete_keys,
        "legacy_state_objects_preserved": legacy_state_objects,
        "retained_run_ids": {
            run_id: release["slot"] for run_id, release in sorted(retained.items())
        },
        "delete_object_keys": sorted(legacy_keys) + sorted(staging_delete_keys),
        "delete_object_count": len(legacy_keys) + len(staging_delete_keys),
        "delete_bytes": sum(int(item.get("Size", 0) or 0) for item in objects
                            if item["Key"] in legacy_keys or item["Key"] in set(staging_delete_keys)),
    }


def apply_cleanup(s3, bucket, plan):
    deleted = []
    for key in plan["delete_object_keys"]:
        s3.delete_object(Bucket=bucket, Key=key)
        deleted.append(key)

    remaining = []
    for key in deleted:
        try:
            s3.head_object(Bucket=bucket, Key=key)
            remaining.append(key)
        except s3.exceptions.ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = error.response.get("Error", {}).get("Code", "")
            if status != 404 and code not in {"404", "NoSuchKey", "NotFound"}:
                raise
    if remaining:
        raise RuntimeError(f"cleanup_objects_still_present:{remaining}")
    return deleted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the guarded pointer and staging cleanup")
    args = parser.parse_args()
    RESULT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    result = {"success": False, "applied": False, "read_only": not args.apply}
    try:
        base = os.environ["TECLOSYNC_S3_PREFIX"].strip("/")
        bucket = os.environ["TECLOSYNC_S3_BUCKET"]
        audit_report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
        s3 = client_from_env()
        plan = plan_cleanup(s3, bucket, base, audit_report)
        result.update({
            "bucket": bucket,
            "production_prefix": base,
            "planned_legacy_pointers": plan["legacy_pointers"],
            "verified_staging_releases": plan["staging_releases"],
            "retained_run_ids": plan["retained_run_ids"],
            "legacy_state_objects_preserved": plan["legacy_state_objects_preserved"],
            "planned_delete_object_count": plan["delete_object_count"],
            "planned_delete_bytes": plan["delete_bytes"],
        })
        if args.apply:
            deleted = apply_cleanup(s3, bucket, plan)
            result["deleted_object_count"] = len(deleted)
            result["deleted_object_keys"] = deleted
            result["applied"] = True
        else:
            result["message"] = "All safety gates passed. No production object was changed."
        result["success"] = True
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    RESULT_REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
