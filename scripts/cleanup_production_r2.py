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
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in value.split("/")):
        raise RuntimeError(f"unsafe_release_artifact_path:{value}")
    return path.as_posix()


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
    manifest_path = PurePosixPath(str(manifest_key or ""))
    state_path = PurePosixPath(str(state_key or ""))
    if (
        not isinstance(manifest_key, str)
        or not manifest_key.startswith(expected_prefix)
        or manifest_path.name != "manifest.json"
        or ".." in manifest_path.parts
    ):
        raise RuntimeError(f"{slot}_manifest_path_mismatch")
    if (
        not isinstance(state_key, str)
        or not state_key.startswith(expected_prefix)
        or state_path.name != "teclo-sync.db"
        or ".." in state_path.parts
    ):
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
    if not isinstance(artifacts, list):
        raise RuntimeError(f"{slot}_manifest_artifacts_invalid")

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


def validate_legacy_pointer_reference(s3, bucket, base, key, pointer, retained, legacy_state_payload=None):
    """Accept compatible legacy key layouts only when bytes match a retained release."""
    if not isinstance(pointer, dict):
        raise RuntimeError(f"legacy_pointer_invalid_shape:{key}")
    run_id = pointer.get("run_id")
    if not isinstance(run_id, str) or run_id not in retained:
        raise RuntimeError(f"legacy_pointer_release_not_retained:{key}:{run_id}")
    release = retained[run_id]
    retained_objects = release["objects"]

    manifest_key = pointer.get("manifest_key")
    if not isinstance(manifest_key, str) or not manifest_key:
        raise RuntimeError(f"legacy_pointer_manifest_missing:{key}")
    manifest_payload = read_object(s3, bucket, manifest_key)
    if manifest_payload != retained_objects["manifest.json"]:
        raise RuntimeError(f"legacy_pointer_manifest_not_reconciled:{key}")

    state_key = pointer.get("state_key")
    used_legacy_state = not state_key
    if not state_key:
        state_key = f"{base}/state/teclo-sync.db"
        state_payload = legacy_state_payload
    else:
        normalized_state_key = state_key.strip("/")
        if not normalized_state_key.startswith(base + "/"):
            normalized_state_key = f"{base}/{normalized_state_key}"
        state_payload = read_object(s3, bucket, normalized_state_key)
        state_key = normalized_state_key

    if state_payload is None:
        state_payload = read_object(s3, bucket, state_key)
    retained_state_hash = sha256(retained_objects["teclo-sync.db"])
    if sha256(state_payload) != retained_state_hash:
        raise RuntimeError(f"legacy_pointer_state_not_reconciled:{key}")
    pointer_state_hash = pointer.get("state_sha256")
    if pointer_state_hash and pointer_state_hash != retained_state_hash:
        raise RuntimeError(f"legacy_pointer_state_hash_mismatch:{key}")
    return {
        "key": key,
        "run_id": run_id,
        "retained_slot": release["slot"],
        "manifest_key": manifest_key,
        "state_key": state_key,
        "used_legacy_state_fallback": used_legacy_state,
        "scheduled_for_removal": True,
    }


def validate_initial_audit(report, base):
    # The audit supplies context; live pointer and byte checks below determine
    # which individual objects are eligible. One unavailable slot must not block
    # independent cleanup candidates.
    if report.get("read_only") is not True:
        raise RuntimeError("read_only_audit_required")
    if report.get("production_prefix", "").strip("/") != base:
        raise RuntimeError("production_audit_prefix_mismatch")


def plan_cleanup(s3, bucket, base, audit_report):
    validate_initial_audit(audit_report, base)
    root = base + "/"
    objects = list_objects(s3, bucket, root)
    keys = {item["Key"] for item in objects}
    skipped = []
    retained = {}

    # Re-read and verify each available canonical slot independently. A missing
    # or unreadable slot is reported, not promoted into a repository-wide gate.
    for slot in ("current", "previous"):
        key = f"{base}/{slot}/current.json"
        if key not in keys:
            skipped.append({"object": key, "reason": "canonical_pointer_absent"})
            continue
        try:
            pointer = json.loads(read_object(s3, bucket, key))
            release = verify_retained_release(s3, bucket, base, slot, pointer)
            retained[release["run_id"]] = release
        except Exception as error:
            skipped.append({"object": key, "reason": f"{type(error).__name__}: {error}"})

    # Archive references are also independent. A malformed archive pointer does
    # not prevent cleanup of unrelated releases whose copies can be proven.
    archive_prefix = f"{base}/archive/"
    archive_pointer_keys = sorted(
        key for key in keys
        if key.startswith(archive_prefix) and key.endswith("/pointer.json")
    )
    for key in archive_pointer_keys:
        try:
            pointer = json.loads(read_object(s3, bucket, key))
            run_id = pointer.get("run_id")
            if key != f"{archive_prefix}{run_id}/pointer.json":
                raise RuntimeError("archive_pointer_path_mismatch")
            if run_id in retained:
                if retained[run_id]["slot"] != "archive":
                    raise RuntimeError("run_id_already_retained_in_another_slot")
                continue
            release = verify_retained_release(s3, bucket, base, "archive", pointer)
            retained[release["run_id"]] = release
        except Exception as error:
            skipped.append({"object": key, "reason": f"{type(error).__name__}: {error}"})

    legacy_state_key = f"{base}/state/teclo-sync.db"
    legacy_state_payload = None
    if legacy_state_key in keys:
        try:
            legacy_state_payload = read_object(s3, bucket, legacy_state_key)
        except Exception as error:
            skipped.append({
                "object": legacy_state_key,
                "reason": f"legacy_database_unavailable_for_compatibility_checks: {type(error).__name__}: {error}",
            })

    # Remove only root compatibility pointers whose actual manifest and effective
    # state target resolve byte-for-byte to an immutable retained release.
    legacy_keys = {
        key for key in (f"{base}/current.json", f"{base}/previous.json")
        if key in keys
    }
    legacy_pointer_reports = []
    safe_legacy_keys = []
    unsafe_legacy_payloads = {}
    for key in sorted(legacy_keys):
        try:
            pointer = json.loads(read_object(s3, bucket, key))
            report = validate_legacy_pointer_reference(
                s3, bucket, base, key, pointer, retained, legacy_state_payload
            )
            legacy_pointer_reports.append(report)
            safe_legacy_keys.append(key)
        except Exception as error:
            try:
                unsafe_legacy_payloads[key] = json.loads(read_object(s3, bucket, key))
            except Exception:
                unsafe_legacy_payloads[key] = {}
            skipped.append({"object": key, "reason": f"{type(error).__name__}: {error}"})

    # Unknown or unresolved pointer-like objects protect their referenced staging
    # release. They are preserved rather than blocking unrelated cleanup.
    protected_staging_runs = set()
    for item in objects:
        key = item["Key"]
        if not key.endswith(".json") or key in safe_legacy_keys:
            continue
        try:
            payload = (
                unsafe_legacy_payloads[key]
                if key in unsafe_legacy_payloads
                else json.loads(read_object(s3, bucket, key))
            )
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for field in ("manifest_key", "state_key"):
            value = payload.get(field)
            if isinstance(value, str) and value.startswith(f"{base}/releases/"):
                run_id = value[len(f"{base}/releases/"):].split("/", 1)[0]
                if run_id:
                    protected_staging_runs.add(run_id)

    staging_prefix = f"{base}/releases/"
    staging_groups = defaultdict(dict)
    for item in objects:
        key = item["Key"]
        if not key.startswith(staging_prefix):
            continue
        relative = key[len(staging_prefix):]
        parts = relative.split("/")
        if len(parts) < 2 or not parts[0] or not parts[-1]:
            skipped.append({"object": key, "reason": "unrecognized_staging_path"})
            continue
        run_id = parts[0]
        filename = "/".join(parts[1:])
        if filename in staging_groups[run_id]:
            skipped.append({"object": key, "reason": "duplicate_staging_path"})
            continue
        try:
            staging_groups[run_id][filename] = read_object(s3, bucket, key)
        except Exception as error:
            skipped.append({
                "object": key,
                "reason": f"staging_object_unavailable: {type(error).__name__}: {error}",
            })

    staging_reports = []
    staging_delete_keys = []
    for run_id, staged_objects in sorted(staging_groups.items()):
        if run_id in protected_staging_runs:
            skipped.append({
                "object": f"{staging_prefix}{run_id}/",
                "reason": "referenced_by_another_pointer",
            })
            continue
        try:
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
        except Exception as error:
            skipped.append({
                "object": f"{staging_prefix}{run_id}/",
                "reason": f"{type(error).__name__}: {error}",
            })

    # Preserve the legacy database and anything not explicitly verified above.
    legacy_state_prefix = f"{base}/state/"
    legacy_state_objects = [
        {"key": item["Key"], "bytes": int(item.get("Size", 0) or 0)}
        for item in objects if item["Key"].startswith(legacy_state_prefix)
    ]

    delete_keys = sorted(set(safe_legacy_keys + staging_delete_keys))
    delete_key_set = set(delete_keys)
    planned_bytes = sum(
        int(item.get("Size", 0) or 0)
        for item in objects if item["Key"] in delete_key_set
    )
    return {
        "legacy_pointers": legacy_pointer_reports,
        "staging_releases": staging_reports,
        "staging_delete_keys": staging_delete_keys,
        "legacy_state_objects_preserved": legacy_state_objects,
        "retained_run_ids": {
            run_id: release["slot"] for run_id, release in sorted(retained.items())
        },
        "skipped_objects": skipped,
        "delete_object_keys": delete_keys,
        "delete_object_count": len(delete_keys),
        "delete_bytes": planned_bytes,
    }

def apply_cleanup(s3, bucket, plan):
    deleted = []
    errors = []
    remaining = []
    for key in plan["delete_object_keys"]:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            try:
                s3.head_object(Bucket=bucket, Key=key)
                remaining.append(key)
            except s3.exceptions.ClientError as error:
                status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                code = error.response.get("Error", {}).get("Code", "")
                if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                    deleted.append(key)
                else:
                    errors.append({"key": key, "error": f"{type(error).__name__}: {error}"})
        except Exception as error:
            errors.append({"key": key, "error": f"{type(error).__name__}: {error}"})
    return {"deleted": deleted, "remaining": remaining, "errors": errors}


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
            "skipped_objects": plan["skipped_objects"],
            "planned_delete_object_count": plan["delete_object_count"],
            "planned_delete_bytes": plan["delete_bytes"],
        })
        if args.apply:
            applied = apply_cleanup(s3, bucket, plan)
            result["deleted_object_count"] = len(applied["deleted"])
            result["deleted_object_keys"] = applied["deleted"]
            result["remaining_object_keys"] = applied["remaining"]
            result["cleanup_errors"] = applied["errors"]
            result["applied"] = bool(applied["deleted"])
            result["partial"] = bool(
                applied["remaining"] or applied["errors"] or plan["skipped_objects"]
            )
        else:
            result["message"] = "Plan complete. No production object was changed."
        result["success"] = True
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    RESULT_REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
