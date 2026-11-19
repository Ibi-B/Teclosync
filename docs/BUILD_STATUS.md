# Teclosync implementation status

The feature branch contains the Teclosync foundation: source registry, M3U ingestion, SQLite staging, bounded refresh, validation, XMLTV parsing support, artifact building, verified R2-compatible storage, and controlled manual workflows.

## Data-integrity work completed

- M3U entry fingerprints cover source ID, display name, stream URL, and parsed metadata attributes.
- Exact repeated records within one source are separated from distinct stream or metadata variants.
- The first exact record remains in the channel table; every repeated occurrence is retained with its source-run, record-index, full-record, and provenance data in `channel_duplicates`.
- Duplicate evidence is retained for successful and under-threshold rejected refreshes.
- Minimum source counts are checked after exact duplicates are excluded, before replacing the source's last-good channel set.
- Parser and SQLite integrity failures include actionable diagnostics.
- Release manifests report source accepted-record, raw-record, and duplicate counts.
- Existing SQLite databases receive the duplicate-audit table through the normal schema initialization path.
- SQLite foreign-key enforcement is enabled on every staging database connection. Source and run registration use conflict-safe updates rather than delete-and-reinsert operations.

## Publication and recovery work completed

- The state database is uploaded to an immutable run-specific release key and verified by SHA-256.
- `current.json` references both the published manifest and the exact state database snapshot used by that release.
- Subsequent refreshes restore the database referenced by `current.json`; legacy pointers without a state key use the prior fixed state-object path for compatibility.
- If a pointer-referenced immutable state snapshot is missing, restore now fails closed rather than proceeding with a potentially empty database.
- The previous pointer is preserved before current-pointer promotion. If current-pointer verification fails, Teclosync attempts to restore the last-known-good pointer.
- Automated tests cover failed state upload, ambiguous current-pointer write failure, rollback, missing pointer-referenced state, duplicate handling, database migration, source isolation, foreign-key constraints, and upsert safety.
- M3U artifact output now separates metadata attributes with spaces, as required by Extended M3U, and includes an exact-format regression test.

## Static and CI validation

The feature branch's validation workflow compiles all Python sources, validates source configuration, and runs the full unit-test suite.

On 19 November 2026, GitHub Actions passed all three gates on commit `8a42895a187d1c5cc6049a22080eea534116270c`: Python compilation, configuration validation, and **56 unit tests**. The successful run is [44624822133](https://github.com/Ibi-B/Teclosync/actions/runs/44624822133).

This is CI evidence using test fixtures and mocked object storage. It does not establish that live Cloudflare R2 restore/publication has worked.

## Not production-verified or intentionally deferred

- A controlled live R2 restore/publication verification remains required. It must check snapshot restore, artifact and manifest upload/read-back, state snapshot upload/read-back, current/previous pointer promotion, and rollback behaviour in an isolated test prefix or bucket.
- The remaining catalogue inspection is outstanding. Passing tests prove pipeline behaviour on fixtures, not that every upstream record is correctly attributed, unique, playable, or suitable for publication.
- Keep the refresh workflow manual-only. Do not enable scheduling, automatic source discovery, or R2 lifecycle expiration.
- `teclosync/retention.py` is scaffolding, not an operational retention implementation: its rotator calls a `publish_manifest` method that the current snapshot store does not provide, and `prune_archive` performs no deletion. Do not invoke it or configure lifecycle expiry on its behalf.
- EPG sources remain disabled; XMLTV parser support is not EPG activation.
- Cross-provider canonical station reconciliation is not implemented. Preserve source records and alternatives until a conservative identity model is designed.
- The legacy `Refresh video catalog` scheduled workflow on `main` is separately failing because its build produces no video items. That is distinct from this feature branch's validation workflow and should be migrated or retired separately.
- Do not merge this foundation into `main` until live restore/publication verification and the remaining catalogue inspection are complete.
