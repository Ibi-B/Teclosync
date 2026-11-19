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

The latest recorded successful run before the R2 verification script refinement is [44629668080](https://github.com/Ibi-B/Teclosync/actions/runs/44629668080): Python compilation, configuration validation, and **59 unit tests** all passed. The latest targeted checks cover state restore checksum failure, preservation of the existing local state file, release-package copy verification, diagnostics upload, current/previous/archive rotation, and ambiguous-write rollback.

## Isolated live R2 verification

A manual workflow is available at `.github/workflows/verify-r2.yml`. It runs `scripts/verify_r2_roundtrip.py` against a unique `telclosync/verification/<workflow-run-id>/` prefix. It does not read, overwrite, or rotate production pointers.

The live check creates four tiny test releases and verifies:
- upload and read-back of the manifest, M3U artifact, and SQLite snapshot;
- current/previous rotation and archived-release creation;
- SHA-256 validation when restoring current, previous, and archived SQLite snapshots;
- rollback after simulating an ambiguous current-pointer write;
- removal of the temporary verification prefix after a successful run.

The manual R2 workflow has not yet been run. It requires the existing R2 repository variables and secrets to be available to GitHub Actions.

## Separate legacy workflow

The scheduled `Refresh video catalog` workflow on `main` remains a separate failing legacy pipeline: its configured source catalogue currently produces no video items. It is not part of this Teclosync R2 round-trip test or the new feature-branch validation workflow; migrate or retire it separately rather than weakening the Teclosync checks.

## Deferred scope

- EPG sources remain disabled. XMLTV parser support is not EPG activation.
- Cross-provider canonical station reconciliation is not implemented. Preserve source records and alternatives until a conservative identity model is designed.
- Keep refresh manual-only; do not enable scheduled ingestion or automatic source discovery.
- Do not merge this foundation to `main` until the isolated live R2 workflow succeeds, the R2 archive-only 90-day lifecycle rule is configured, and the remaining catalogue inspection is complete.
