# Teclosync implementation status

The feature branch contains the Teclosync foundation: source registry, M3U ingestion, SQLite staging, bounded refresh, validation, XMLTV parsing support, artifact building, verified R2-compatible storage, and controlled manual workflows.

## Data-integrity work completed

- M3U entry fingerprints cover source ID, display name, stream URL, and parsed metadata attributes.
- Exact repeated records within one source are separated from distinct stream or metadata variants.
- The first exact record remains in the channel table; every repeated occurrence is retained with its source-run, record-index, full-record, and provenance data in \`channel_duplicates\`.
- Duplicate evidence is retained for successful and under-threshold rejected refreshes.
- Minimum source counts are checked after exact duplicates are excluded, before replacing the source's last-good channel set.
- Parser and SQLite integrity failures include actionable diagnostics.
- Release manifests report source accepted-record, raw-record, and duplicate counts.
- Existing SQLite databases receive the duplicate-audit table through the normal schema initialization path.

## Publication and recovery work completed

- The state database is uploaded to an immutable run-specific release key and verified by SHA-256.
- \`current.json\` references both the published manifest and the exact state database snapshot used by that release.
- Subsequent refreshes restore the database referenced by \`current.json\`; legacy pointers without a state key use the prior fixed state-object path for compatibility.
- The previous pointer is preserved before current-pointer promotion. If current-pointer verification fails, Teclosync attempts to restore the last-known-good pointer.
- Automated tests cover failed state upload, ambiguous current-pointer write failure, rollback, duplicate handling, database migration, and source isolation.

## Validation status

The GitHub validation workflow passed configuration validation and all **51 unit tests** on the feature branch on 18 November 2026.

This is CI evidence using test fixtures and mocked object storage. A controlled live R2 restore/publication verification is still required before treating the updated pointer/state lifecycle as production-verified.

## Deferred

- Keep the refresh workflow manual-only.
- Do not enable scheduling, automatic source discovery, or R2 lifecycle expiration.
- EPG sources remain disabled; EPG activation is a separate task.
- Cross-provider canonical station reconciliation is not implemented. Preserve source records and alternatives until a conservative identity model is designed.
- Do not merge this foundation into \`main\` until live restore/publication verification and the remaining catalogue inspection are complete.
