#!/usr/bin/env python3
"""Safely add a 90-day archive-only Cloudflare R2 lifecycle rule."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

AUDIT_REPORT = Path("state/diagnostics/production-r2-audit.json")
RESULT_REPORT = Path("state/diagnostics/archive-lifecycle-result.json")
RULE_ID = "teclosync-archive-90-days"
AGE_SECONDS = 90 * 24 * 60 * 60


def api_call(method, url, token, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    if result.get("success") is not True:
        raise RuntimeError(f"cloudflare_api_{method.lower()}_failed")
    return result.get("result") or {}


def require_clean_release(release, slot):
    if not isinstance(release, dict):
        raise RuntimeError(f"{slot}_release_not_present")
    state = release.get("database_and_catalogue") or {}
    quality = state.get("quality") or {}
    catalogue = state.get("catalogue") or {}
    if state.get("integrity_check") != ["ok"]:
        raise RuntimeError(f"{slot}_sqlite_integrity_check_failed")
    if state.get("foreign_key_violations"):
        raise RuntimeError(f"{slot}_sqlite_foreign_key_violations")
    if state.get("missing_tables"):
        raise RuntimeError(f"{slot}_sqlite_schema_incomplete")
    if state.get("database_channel_count", 0) <= 0:
        raise RuntimeError(f"{slot}_database_has_no_channels")
    if quality.get("blank_names") or quality.get("blank_stream_urls") or quality.get("malformed_stream_urls"):
        raise RuntimeError(f"{slot}_catalogue_has_invalid_channel_fields")
    if catalogue.get("record_count") != state.get("database_channel_count") or not catalogue.get("matches_database_count"):
        raise RuntimeError(f"{slot}_catalogue_record_count_mismatch")
    if catalogue.get("blank_names") or catalogue.get("blank_stream_urls") or catalogue.get("malformed_stream_urls") or catalogue.get("missing_provenance"):
        raise RuntimeError(f"{slot}_catalogue_quality_gate_failed")
    if release.get("source_count_mismatches"):
        raise RuntimeError(f"{slot}_manifest_source_counts_mismatch")


def validate_audit(report):
    if report.get("success") is not True or report.get("read_only") is not True:
        raise RuntimeError("production_audit_not_successful")
    prefix = report.get("production_prefix")
    if not isinstance(prefix, str) or not prefix.strip("/") or prefix.strip("/") != os.environ.get("TECLOSYNC_S3_PREFIX", "").strip("/"):
        raise RuntimeError("production_prefix_mismatch")
    require_clean_release(report.get("current"), "current")
    require_clean_release(report.get("previous"), "previous")
    archive = report.get("archive") or {}
    if archive.get("all_pointer_references_scoped_to_archive") is not True:
        raise RuntimeError("archive_pointer_layout_invalid")
    lifecycle = report.get("lifecycle") or {}
    if lifecycle.get("status") != "inspected":
        raise RuntimeError("cloudflare_lifecycle_policy_not_inspected")
    if lifecycle.get("blocking_protected_prefix_rules"):
        raise RuntimeError("existing_lifecycle_rule_can_expire_current_or_previous")
    return prefix.strip("/"), lifecycle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the archive-only 90-day rule after all safety gates pass")
    args = parser.parse_args()
    RESULT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    result = {"applied": False, "rule_id": RULE_ID, "days": 90}
    try:
        audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
        base, lifecycle_snapshot = validate_audit(audit)
        bucket = os.environ["TECLOSYNC_S3_BUCKET"]
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        if not account_id:
            from urllib.parse import urlsplit
            endpoint_host = urlsplit(os.environ.get("TECLOSYNC_S3_ENDPOINT", "")).hostname or ""
            if endpoint_host.endswith(".r2.cloudflarestorage.com"):
                account_id = endpoint_host.split(".", 1)[0]
        token = os.environ["CLOUDFLARE_API_TOKEN"]
        if not account_id or not token:
            raise RuntimeError("cloudflare_account_id_or_api_token_missing")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/lifecycle"
        # Re-read immediately before updating to avoid relying on a stale rule list.
        current_result = api_call("GET", url, token)
        current_rules = current_result.get("rules") or []
        protected = [f"{base}/current/", f"{base}/previous/"]
        for rule in current_rules:
            if rule.get("enabled") is True and rule.get("deleteObjectsTransition"):
                old_prefix = (rule.get("conditions") or {}).get("prefix", "")
                if any(
                    not old_prefix or protected_prefix.startswith(old_prefix) or old_prefix.startswith(protected_prefix)
                    for protected_prefix in protected
                ):
                    raise RuntimeError("lifecycle_policy_changed_or_protected_prefix_now_at_risk")
        target_prefix = f"{base}/archive/"
        existing_target = [rule for rule in current_rules if rule.get("id") == RULE_ID]
        if any((rule.get("conditions") or {}).get("prefix") != target_prefix for rule in existing_target):
            raise RuntimeError("lifecycle_rule_id_already_used_for_different_prefix")
        preserved_rules = [rule for rule in current_rules if rule.get("id") != RULE_ID]
        target_rule = {
            "id": RULE_ID,
            "conditions": {"prefix": target_prefix},
            "enabled": True,
            "deleteObjectsTransition": {
                "condition": {"type": "Age", "maxAge": AGE_SECONDS}
            },
        }
        next_rules = preserved_rules + [target_rule]
        result["bucket"] = bucket
        result["production_prefix"] = base
        result["archive_prefix"] = target_prefix
        result["existing_rule_count"] = len(current_rules)
        result["preserved_rule_ids"] = [rule.get("id") for rule in preserved_rules]
        result["target_rule"] = target_rule
        result["audit_current_run_id"] = audit["current"]["run_id"]
        result["audit_previous_run_id"] = audit["previous"]["run_id"]
        result["lifecycle_snapshot_rule_count"] = lifecycle_snapshot.get("rule_count")
        if args.apply:
            api_call("PUT", url, token, {"rules": next_rules})
            after = api_call("GET", url, token)
            after_rules = after.get("rules") or []
            matches = [
                rule for rule in after_rules
                if rule.get("id") == RULE_ID
                and rule.get("enabled") is True
                and (rule.get("conditions") or {}).get("prefix") == target_prefix
                and ((rule.get("deleteObjectsTransition") or {}).get("condition") or {}) == {
                    "type": "Age", "maxAge": AGE_SECONDS
                }
            ]
            if len(matches) != 1:
                raise RuntimeError("lifecycle_rule_post_write_verification_failed")
            observed_ids = {rule.get("id") for rule in after_rules}
            expected_ids = {rule.get("id") for rule in next_rules}
            if observed_ids != expected_ids:
                raise RuntimeError("lifecycle_rule_collection_changed_unexpectedly")
            result["applied"] = True
            result["verified"] = True
            result["final_rule_count"] = len(after_rules)
            result["final_rule_ids"] = [rule.get("id") for rule in after_rules]
        else:
            result["dry_run"] = True
            result["message"] = "All gates passed; no lifecycle configuration was changed."
        result["success"] = True
    except Exception as error:
        result["success"] = False
        result["error"] = f"{type(error).__name__}: {error}"
    RESULT_REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
