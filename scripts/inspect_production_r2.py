#!/usr/bin/env python3
"""Read-only audit of the configured Teclosync production R2 release layout."""
import hashlib
import io
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import boto3
import zstandard
from botocore.config import Config


REPORT = Path("state/diagnostics/production-r2-audit.json")
NETWORK_SCHEMES = {"http", "https", "rtmp", "rtmps", "rtsp", "mms", "mmsh", "udp", "rtp", "srt"}


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


def safe_key(base_prefix, key):
    base = base_prefix.rstrip("/") + "/"
    if not isinstance(key, str) or not key.startswith(base):
        raise RuntimeError("pointer_key_outside_configured_prefix")
    if ".." in PurePosixPath(key).parts:
        raise RuntimeError("unsafe_object_key")
    return key


def get_json(s3, bucket, key):
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def list_all(s3, bucket, prefix):
    paginator = s3.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    return objects


def source_and_catalogue_audit(db_bytes, catalogue_bytes, expected_run_id):
    temp = REPORT.parent / "audit-staging.db"
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(db_bytes)
    result = {}
    try:
        conn = sqlite3.connect(f"file:{temp.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        result["integrity_check"] = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        result["foreign_key_violations"] = [list(r) for r in conn.execute("PRAGMA foreign_key_check")]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required_tables = {"sources", "source_runs", "channels", "channel_duplicates", "programmes"}
        result["missing_tables"] = sorted(required_tables - tables)
        if not required_tables.issubset(tables):
            conn.close()
            return result
        total = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        result["database_channel_count"] = total
        result["programme_count"] = conn.execute("SELECT COUNT(*) FROM programmes").fetchone()[0]
        result["duplicate_audit_rows_total"] = conn.execute("SELECT COUNT(*) FROM channel_duplicates").fetchone()[0]
        result["duplicate_rows_for_release_run"] = conn.execute(
            "SELECT COUNT(*) FROM channel_duplicates WHERE source_run_id = ?", (expected_run_id,)
        ).fetchone()[0]
        result["source_count"] = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        result["source_counts"] = [
            dict(r) for r in conn.execute(
                """SELECT s.source_id, s.name, s.category, s.format,
                          COUNT(c.channel_key) AS channel_count,
                          SUM(CASE WHEN c.name IS NULL OR trim(c.name) = '' THEN 1 ELSE 0 END) AS blank_names,
                          SUM(CASE WHEN c.tvg_logo IS NOT NULL AND trim(c.tvg_logo) <> '' THEN 1 ELSE 0 END) AS with_logo,
                          SUM(CASE WHEN c.country IS NOT NULL AND trim(c.country) <> '' THEN 1 ELSE 0 END) AS with_country,
                          SUM(CASE WHEN c.language IS NOT NULL AND trim(c.language) <> '' THEN 1 ELSE 0 END) AS with_language,
                          SUM(CASE WHEN c.radio = 1 THEN 1 ELSE 0 END) AS radio_channels
                   FROM sources s LEFT JOIN channels c ON c.source_id = s.source_id
                   GROUP BY s.source_id, s.name, s.category, s.format
                   ORDER BY channel_count DESC, s.source_id"""
            )
        ]
        missing_names = conn.execute(
            "SELECT COUNT(*) FROM channels WHERE name IS NULL OR trim(name) = ''"
        ).fetchone()[0]
        missing_streams = conn.execute(
            "SELECT COUNT(*) FROM channels WHERE stream_url IS NULL OR trim(stream_url) = ''"
        ).fetchone()[0]
        bad_urls = []
        url_examples = []
        for row in conn.execute("SELECT source_id, name, stream_url FROM channels"):
            raw = row["stream_url"] or ""
            try:
                parsed = urlsplit(raw)
                valid = parsed.scheme.lower() in NETWORK_SCHEMES and bool(parsed.netloc or parsed.scheme in {"udp", "rtp"})
            except ValueError:
                valid = False
            if not valid:
                bad_urls.append(row["source_id"])
                if len(url_examples) < 20:
                    url_examples.append({"source_id": row["source_id"], "name": row["name"], "url": raw[:180]})
        result["quality"] = {
            "blank_names": missing_names,
            "blank_stream_urls": missing_streams,
            "malformed_stream_urls": len(bad_urls),
            "malformed_url_source_counts": dict(Counter(bad_urls)),
            "malformed_url_examples": url_examples,
            "distinct_stream_urls": conn.execute("SELECT COUNT(DISTINCT stream_url) FROM channels").fetchone()[0],
            "urls_shared_by_multiple_sources": conn.execute(
                "SELECT COUNT(*) FROM (SELECT stream_url FROM channels GROUP BY stream_url HAVING COUNT(DISTINCT source_id) > 1)"
            ).fetchone()[0],
            "cross_source_url_overlap_examples": [
                dict(r) for r in conn.execute(
                    """SELECT stream_url, COUNT(*) AS record_count,
                              COUNT(DISTINCT source_id) AS source_count,
                              GROUP_CONCAT(DISTINCT source_id) AS source_ids
                       FROM channels
                       GROUP BY stream_url
                       HAVING COUNT(DISTINCT source_id) > 1
                       ORDER BY source_count DESC, record_count DESC
                       LIMIT 20"""
                )
            ],
            "source_run_rows": conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0],
            "latest_runs": [
                dict(r) for r in conn.execute(
                    """SELECT sr.source_id, sr.run_id, sr.started_at, sr.status, sr.record_count
                       FROM source_runs sr
                       JOIN (SELECT source_id, MAX(started_at) AS latest_started FROM source_runs GROUP BY source_id) x
                         ON x.source_id = sr.source_id AND x.latest_started = sr.started_at
                       ORDER BY sr.source_id"""
                )
            ],
        }
        result["catalogue"] = audit_catalogue(catalogue_bytes, total)
        conn.close()
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return result


def audit_catalogue(payload, expected_db_count):
    stream = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(payload))
    records = 0
    blank_names = 0
    blank_urls = 0
    malformed_urls = 0
    missing_provenance = 0
    source_counts = Counter()
    malformed_examples = []
    for line_no, raw in enumerate(io.BufferedReader(stream), 1):
        if not raw.strip():
            continue
        records += 1
        try:
            record = json.loads(raw)
        except Exception as error:
            raise RuntimeError(f"catalogue_jsonl_invalid_at_line_{line_no}:{type(error).__name__}") from error
        source_id = record.get("source_id") or "(missing)"
        source_counts[source_id] += 1
        if not str(record.get("name") or "").strip():
            blank_names += 1
        url = str(record.get("stream_url") or "").strip()
        if not url:
            blank_urls += 1
        else:
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme.lower() in NETWORK_SCHEMES and bool(parsed.netloc or parsed.scheme in {"udp", "rtp"})
            except ValueError:
                valid = False
            if not valid:
                malformed_urls += 1
                if len(malformed_examples) < 20:
                    malformed_examples.append({"line": line_no, "source_id": source_id, "name": record.get("name"), "url": url[:180]})
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or not provenance.get("source") or not provenance.get("source_url"):
            missing_provenance += 1
    return {
        "record_count": records,
        "matches_database_count": records == expected_db_count,
        "blank_names": blank_names,
        "blank_stream_urls": blank_urls,
        "malformed_stream_urls": malformed_urls,
        "missing_provenance": missing_provenance,
        "source_counts": dict(sorted(source_counts.items())),
        "malformed_url_examples": malformed_examples,
    }


def verify_release(s3, bucket, base, slot, pointer):
    if not isinstance(pointer, dict):
        raise RuntimeError(f"{slot}_pointer_missing_or_invalid")
    run_id = pointer.get("run_id")
    manifest_key = safe_key(base, pointer.get("manifest_key"))
    state_key = safe_key(base, pointer.get("state_key"))
    if not run_id or not manifest_key.endswith("/manifest.json") or not state_key.endswith("/teclo-sync.db"):
        raise RuntimeError(f"{slot}_pointer_incomplete")
    expected_slot_prefix = f"{base.rstrip('/')}/{slot}/"
    if not manifest_key.startswith(expected_slot_prefix) or not state_key.startswith(expected_slot_prefix):
        raise RuntimeError(f"{slot}_pointer_references_wrong_slot")
    manifest_bytes = s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read()
    manifest = json.loads(manifest_bytes)
    if manifest.get("run_id") != run_id or not isinstance(manifest.get("artifacts"), list):
        raise RuntimeError(f"{slot}_manifest_mismatch")
    artifact_results = []
    catalogue_payload = None
    for artifact in manifest["artifacts"]:
        relative = artifact.get("path", "")
        posix = PurePosixPath(relative)
        if not relative or posix.is_absolute() or ".." in posix.parts or len(posix.parts) != 1:
            raise RuntimeError(f"{slot}_unsafe_artifact_path")
        key = safe_key(base, manifest_key.rsplit("/", 1)[0] + "/" + relative)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        digest = sha256(body)
        if digest != artifact.get("sha256") or len(body) != artifact.get("bytes"):
            raise RuntimeError(f"{slot}_artifact_integrity_failure:{relative}")
        item = {"path": relative, "bytes": len(body), "sha256_verified": True}
        if relative == "tv-catalog.jsonl.zst":
            catalogue_payload = body
        if relative.endswith(".m3u8"):
            text = body.decode("utf-8", errors="replace").splitlines()
            item["m3u_entries"] = sum(1 for line in text if line.startswith("#EXTINF:"))
            item["manifest_channel_count"] = artifact.get("channel_count")
            if item["m3u_entries"] != item["manifest_channel_count"]:
                raise RuntimeError(f"{slot}_m3u_entry_count_mismatch:{relative}")
        artifact_results.append(item)
    state_bytes = s3.get_object(Bucket=bucket, Key=state_key)["Body"].read()
    state_hash = sha256(state_bytes)
    expected_state_hash = pointer.get("state_sha256")
    if not expected_state_hash:
        raise RuntimeError(f"{slot}_state_hash_missing")
    if state_hash != expected_state_hash:
        raise RuntimeError(f"{slot}_state_hash_mismatch")
    if catalogue_payload is None:
        raise RuntimeError(f"{slot}_catalogue_artifact_missing")
    state = source_and_catalogue_audit(state_bytes, catalogue_payload, run_id)
    sources = manifest.get("sources", [])
    manifest_source_counts = {item.get("source_id"): item.get("channel_count") for item in sources if isinstance(item, dict)}
    db_source_counts = {item["source_id"]: item["channel_count"] for item in state.get("source_counts", [])}
    source_count_mismatches = {
        source_id: {"manifest": manifest_count, "database": db_source_counts.get(source_id)}
        for source_id, manifest_count in manifest_source_counts.items()
        if db_source_counts.get(source_id) != manifest_count
    }
    return {
        "run_id": run_id,
        "manifest_key": manifest_key,
        "state_key": state_key,
        "manifest_generated_at": manifest.get("generated_at"),
        "artifact_count": len(artifact_results),
        "artifacts": artifact_results,
        "state_sha256_verified": bool(expected_state_hash and state_hash == expected_state_hash),
        "source_statuses": sources,
        "source_count_mismatches": source_count_mismatches,
        "database_and_catalogue": state,
    }


def inspect_lifecycle_rules(bucket):
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not account_id:
        endpoint_host = urlsplit(os.environ.get("TECLOSYNC_S3_ENDPOINT", "")).hostname or ""
        if endpoint_host.endswith(".r2.cloudflarestorage.com"):
            account_id = endpoint_host.split(".", 1)[0]
    if not token or not account_id:
        return {"status": "not_configured", "required": ["CLOUDFLARE_API_TOKEN secret; account ID is derived from TECLOSYNC_S3_ENDPOINT when possible"]}
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/lifecycle"
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    if not payload.get("success"):
        raise RuntimeError("cloudflare_lifecycle_read_failed")
    rules = payload.get("result", {}).get("rules", [])
    base = os.environ["TECLOSYNC_S3_PREFIX"].strip("/")
    protected = [f"{base}/current/", f"{base}/previous/"]
    output = []
    blocking = []
    for rule in rules:
        conditions = rule.get("conditions") or {}
        prefix = conditions.get("prefix", "")
        transition = rule.get("deleteObjectsTransition") or {}
        condition = transition.get("condition") or {}
        enabled = rule.get("enabled") is True
        delete_rule = enabled and bool(transition)
        overlaps_protected = any(
            not prefix or protected_prefix.startswith(prefix) or prefix.startswith(protected_prefix)
            for protected_prefix in protected
        )
        item = {
            "id": rule.get("id"),
            "enabled": enabled,
            "prefix": prefix,
            "delete_after_days": (
                condition.get("maxAge") / 86400
                if condition.get("type") == "Age" and isinstance(condition.get("maxAge"), (int, float))
                else None
            ),
            "delete_rule": delete_rule,
            "overlaps_current_or_previous": bool(delete_rule and overlaps_protected),
        }
        output.append(item)
        if item["overlaps_current_or_previous"]:
            blocking.append(item["id"])
    return {
        "status": "inspected",
        "rule_count": len(output),
        "rules": output,
        "blocking_protected_prefix_rules": blocking,
        "existing_archive_expiry_rules": [
            item for item in output
            if item["delete_rule"] and item["prefix"] == f"{base}/archive/"
        ],
        "safe_to_add_archive_only_expiry": not blocking,
    }


def run():
    bucket = os.environ["TECLOSYNC_S3_BUCKET"]
    base = os.environ["TECLOSYNC_S3_PREFIX"].strip("/")
    s3 = client_from_env()
    root = base + "/"
    objects = list_all(s3, bucket, root)
    top = Counter(obj["Key"][len(root):].split("/", 1)[0] for obj in objects)
    keys = {obj["Key"] for obj in objects}

    current_key = f"{base}/current/current.json"
    previous_key = f"{base}/previous/current.json"
    current_pointer = get_json(s3, bucket, current_key) if current_key in keys else (
        get_json(s3, bucket, f"{base}/current.json") if f"{base}/current.json" in keys else None
    )
    previous_pointer = get_json(s3, bucket, previous_key) if previous_key in keys else (
        get_json(s3, bucket, f"{base}/previous.json") if f"{base}/previous.json" in keys else None
    )
    if not current_pointer:
        raise RuntimeError("production_current_pointer_not_found")

    report = {
        "bucket": bucket,
        "production_prefix": base,
        "read_only": True,
        "listed_object_count": len(objects),
        "top_level_prefix_object_counts": dict(top),
        "legacy_root_current_pointer_present": f"{base}/current.json" in keys,
        "legacy_root_previous_pointer_present": f"{base}/previous.json" in keys,
        "current": verify_release(s3, bucket, base, "current", current_pointer),
        "previous": verify_release(s3, bucket, base, "previous", previous_pointer) if previous_pointer else None,
    }
    archive_prefix = f"{base}/archive/"
    archive_objects = [obj for obj in objects if obj["Key"].startswith(archive_prefix)]
    archive_pointer_keys = [obj["Key"] for obj in archive_objects if obj["Key"].endswith("/pointer.json")]
    archive_pointers = []
    for key in archive_pointer_keys:
        pointer = get_json(s3, bucket, key)
        run_id = pointer.get("run_id") if isinstance(pointer, dict) else None
        manifest_key = pointer.get("manifest_key") if isinstance(pointer, dict) else None
        state_key = pointer.get("state_key") if isinstance(pointer, dict) else None
        valid = bool(
            run_id and manifest_key and state_key
            and manifest_key.startswith(archive_prefix)
            and state_key.startswith(archive_prefix)
            and manifest_key in keys
            and state_key in keys
        )
        archive_pointers.append({"key": key, "run_id": run_id, "references_valid_archive_objects": valid})
    report["archive"] = {
        "object_count": len(archive_objects),
        "pointer_count": len(archive_pointer_keys),
        "pointers": archive_pointers,
        "all_pointer_references_scoped_to_archive": all(item["references_valid_archive_objects"] for item in archive_pointers),
    }
    report["layout"] = {
        "current_prefix_present": top.get("current", 0) > 0,
        "previous_prefix_present": top.get("previous", 0) > 0,
        "archive_prefix_present": top.get("archive", 0) > 0,
        "diagnostics_prefix_present": top.get("diagnostics", 0) > 0,
        "temporary_releases_prefix_present": top.get("releases", 0) > 0,
        "unexpected_top_level_prefixes": sorted(set(top) - {"current", "previous", "archive", "diagnostics", "releases", "verification", "current.json", "previous.json", "state"}),
    }
    return report


def main():
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run()
        result["success"] = True
    except Exception as error:
        result = {
            "success": False,
            "error": f"{type(error).__name__}: {error}",
            "read_only": True,
        }
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
