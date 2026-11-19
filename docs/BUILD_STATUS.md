# Teclosync implementation status

The feature branch contains the Teclosync ingestion and release foundation: source registry, M3U ingestion, SQLite staging, bounded refresh, source-level validation, artifact building, checksummed R2 storage, release rotation, and manual verification workflows.

## Data-integrity work completed

- M3U entry fingerprints cover source ID, display name, stream URL, and parsed metadata attributes.
- Exact repeats within one source are separated from distinct stream or metadata variants.
- The first exact record remains in `channels`; repeated occurrences retain full record and provenance in `channel_duplicates`, including under-threshold rejected refreshes.
- Minimum source counts are checked after exact duplicates are excluded and before last-good rows are replaced.
- Parser and SQLite integrity failures include actionable diagnostics.
- Release manifests report accepted, raw, and duplicate counts per source.
- Existing SQLite databases receive the duplicate-audit table through schema initialization.
- SQLite foreign-key enforcement is enabled on every connection. Source/run registration uses conflict-safe updates so it does not delete rows referenced by existing channel data.
- Extended M3U output uses space-delimited metadata attributes and a comma before the display name.

## R2 release lifecycle implemented

New refreshes upload a complete release to a temporary `releases/<run-id>/` prefix and verify its artifacts, manifest, and SQLite snapshot before promotion.

The rotator then maintains complete, run-scoped release packages:

- `current/<run-id>/` — newest published release.
- `previous/<run-id>/` — immediately previous good release.
- `archive/<run-id>/` — the release displaced from the previous-good slot.
- `current/current.json` and `previous/current.json` — the active pointers for those slots.
- `archive/<run-id>/pointer.json` — archived release pointer metadata.
- `releases/<run-id>/` — temporary upload area, removed after successful promotion.

Each copied artifact, manifest, and state database is read back and SHA-256 verified. The current pointer stores the state database checksum, and a subsequent restore verifies that checksum before replacing the local SQLite file. Missing or corrupted pointer-referenced snapshots fail closed.

Pointer promotion handles ambiguous object-store writes: if the object write commits but its read-back fails, both current and previous pointers are restored. Source packages are not removed until the new pointers have been verified. Cleanup failures are reported without invalidating a successful promotion.

The rotation keeps current and previous snapshots out of the archive lifecycle. Configure the Cloudflare R2 expiration rule only for:

`<TECLOSYNC_S3_PREFIX>/archive/` — expire objects after 90 days.

Do not apply expiration to `current/`, `previous/`, or the separate diagnostics prefix. The lifecycle rule itself is a bucket setting and has not been changed from this repository.

## Diagnostics

The refresh command now uploads its diagnostic JSON to `TECLOSYNC_DIAGNOSTICS_PREFIX/refresh-<run-id>.json` and also retains the GitHub Actions artifact. When no explicit diagnostics prefix is supplied, it uses `<TECLOSYNC_S3_PREFIX>/diagnostics/`. A diagnostics upload failure is recorded locally and does not reverse an otherwise successful release promotion.

## Validation status

GitHub Actions validation compiles all Python sources, validates the source registry, and runs the complete unit suite.

The latest branch run is [44630096537](https://github.com/Ibi-B/Teclosync/actions/runs/44630096537) on commit `836cbd015a67e1aa77261e2d3c2597ca2884a2ca`: Python compilation, configuration validation, and **59 unit tests** all passed. The live-R2 script is included in Python compilation; its real bucket execution remains a separate manual workflow.

## Isolated live R2 verification

A manual workflow is available at `.github/workflows/verify-r2.yml`. It runs `scripts/verify_r2_roundtrip.py` against a unique `telclosync/verification/<workflow-run-id>/` prefix. It does not read, overwrite, or rotate production pointers.

The live check creates four tiny test releases and verifies:
- upload and read-back of the manifest, M3U artifact, and SQLite snapshot;
- current/previous rotation and archived-release creation;
- SHA-256 validation when restoring current, previous, and archived SQLite snapshots;
- rollback after simulating an ambiguous current-pointer write;
- removal of the temporary verification prefix after a successful run.

The live R2 workflow passed on 19 November 2026 in run [44633287006](https://github.com/Ibi-B/Teclosync/actions/runs/44633287006). It verified current/previous rotation, archived release restore, SHA-256 checks for artifacts and state, pointer rollback after an ambiguous write, and cleanup of the isolated verification prefix. No production objects were used by that test.

## Separate legacy workflow

The scheduled `Refresh video catalog` workflow on `main` remains a separate failing legacy pipeline: its configured source catalogue currently produces no video items. It is not part of this Teclosync R2 round-trip test or the new feature-branch validation workflow; migrate or retire it separately rather than weakening the Teclosync checks.

## Deferred scope

- EPG sources remain disabled. XMLTV parser support is not EPG activation.
- Cross-provider canonical station reconciliation is not implemented. Preserve source records and alternatives until a conservative identity model is designed.
- Keep refresh manual-only; do not enable scheduled ingestion or automatic source discovery.
- The 19 November 2026 production refresh published run `20261119T224927Z` under `telclosync/state`, with diagnostics under the separate `telclosync/diagnostics` prefix. It reported 29 healthy sources out of 33 and published 26,941 accepted records with zero same-source exact duplicates in that run. Four sources were unavailable: Cineverse and Whale TV Plus (M3U parse-shape errors), Samsung TV Plus Nordic Sweden (M3U parse-shape error), and JRomero88 VOD (HTTP 404). The current pointer is run `20261119T224927Z`; the previous pointer is `20261118T195146Z`.
- The production refresh diagnostics provide source-level ingestion counts, but do not alone establish object-layout correctness, SQLite integrity, cross-source duplication, or field-level catalogue quality. A read-only inspection workflow has therefore been added on `main` at `.github/workflows/audit-production-r2.yml`. It downloads and hashes the live manifests, artifacts, and database snapshots, audits SQLite integrity/foreign keys, and checks the compressed catalogue against the database. It also optionally reads existing Cloudflare lifecycle rules.
- `scripts/manage_archive_lifecycle.py` gates the 90-day expiry configuration on a successful production audit, integrity of both current and previous releases, archive pointer isolation, and absence of any enabled expiry rule overlapping current/previous. It preserves unrelated bucket lifecycle rules and then re-reads the policy to verify the new rule.
- Do not merge this foundation to `main` until the production audit completes, the archive-only 90-day lifecycle rule is configured and read back successfully, and the remaining source-quality issues are reviewed.


## Production R2 audit and archive lifecycle controls

- The read-only production audit is launched from the Actions workflow on the default branch: [Audit Teclosync production R2 layout and catalogue](https://github.com/Ibi-B/Teclosync/actions/workflows/audit-production-r2.yml).
- The configured production prefix observed in the 19 November refresh run is `telclosync/state`; diagnostics are deliberately outside that prefix at `telclosync/diagnostics`.
- The workflow defaults to read-only. The optional lifecycle apply input is off by default. Enabling it requests only `telclosync/state/archive/` (90 days) and is gated by the audit report. The Cloudflare API credentials are separate from the R2 S3 credentials: `CLOUDFLARE_ACCOUNT_ID` repository variable and `CLOUDFLARE_API_TOKEN` secret with Workers R2 Storage Write permission.
- No lifecycle rule has been changed yet. The live audit must first confirm the actual pointer/object layout and existing bucket rules.


- The audit checks each run-scoped pointer path against its slot and run ID, requires the immutable state checksum, checks all artifacts and M3U entry counts, reconciles every catalogue `(source_id, channel_key)` against SQLite, checks SQLite integrity and foreign keys, measures blank/malformed stream fields and missing provenance, and reports cross-provider stream URL overlaps without collapsing provider records.
- The lifecycle manager refuses to act if an existing enabled deletion rule overlaps `current/` or `previous/`, or if another deletion rule overlaps `archive/` under a different ID. It preserves other rules exactly, re-reads the lifecycle configuration before applying, and verifies the exact 90-day archive rule after the update.
- The main-branch workflow is available at [audit-production-r2.yml](https://github.com/Ibi-B/Teclosync/actions/workflows/audit-production-r2.yml). The first run should use `apply_archive_expiry=false`. A subsequent run may use `true` only after the audit artifact is reviewed and the Cloudflare API token is configured. The workflow is currently prepared but has not yet been dispatched.
