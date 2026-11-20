# Current production and implementation status — 20 November 2026

This is the current operational record. Older dated sections below are historical snapshots; where they differ, the verified outcomes in this section supersede them.

## Production R2 lifecycle and integrity

The archive-retention policy is active and read back from Cloudflare:

- Rule `teclosync-archive-30-days` is enabled for `telclosync/state/archive/` only.
- Age threshold is 30 days (2,592,000 seconds).
- The existing `Default Multipart Abort Rule` is preserved.
- No deletion rule overlaps `current/` or `previous/`.
- The lifecycle apply and read-back passed in [run 44648240346](https://github.com/Ibi-B/Teclosync/actions/runs/44648240346).

The retained releases are:

| Slot | Release | Verified records |
|---|---|---:|
| Current | `20261119T234644Z` | 26,934 |
| Previous | `20261119T224927Z` | 26,941 |
| Archive | `20261118T195146Z` | Older protected rollback snapshot |

Post-maintenance verification confirmed SHA-256 checks for both active snapshots and their artifacts, SQLite `integrity_check=ok`, zero foreign-key violations, and exact catalogue/database key reconciliation. The archive pointer remains correctly scoped to its archived release.

## Completed production housekeeping

The guarded apply ran in [housekeeping run 44671376505](https://github.com/Ibi-B/Teclosync/actions/runs/44671376505). The apply step succeeded. The first post-apply audit encountered a transient Cloudflare HTTP 500; no cleanup retry was run. A separate read-only verification then passed in [run 44671877908](https://github.com/Ibi-B/Teclosync/actions/runs/44671877908).

The apply removed exactly 21 objects (267,755,782 bytes):
- The stale root compatibility pointer `telclosync/state/current.json`, which referenced the older archived release rather than canonical current.
- Six temporary staging objects for `20261118T195146Z`.
- Seven temporary staging objects for `20261119T224927Z`.
- Seven temporary staging objects for `20261119T234644Z`.

All three staging copies were compared byte-for-byte with their retained archive/current/previous packages before deletion. The cleanup report had zero skipped objects, zero remaining targets, and zero deletion errors. The legacy non-run-scoped database `telclosync/state/state/teclo-sync.db` was preserved; current, previous and archive snapshots were not deleted or altered.

The final read-only audit verified:
- 45 objects remain under `telclosync/state`: archive 8, current 21, previous 15, legacy state 1.
- No root-level `current.json` or `previous.json` compatibility pointer remains.
- No temporary `releases/` objects remain.
- No unexpected top-level state prefixes were found.
- Current and previous releases still pass artifact/state checksums, SQLite integrity, FK checks and exact catalogue/database reconciliation.
- The archive pointer still resolves to the archived release.
- The 30-day archive-only Cloudflare lifecycle rule remains enabled and does not overlap current or previous.

Storage after cleanup is 649,310,155 bytes for `telclosync/state` plus 49,785 bytes for diagnostics: 649,359,940 bytes combined (about 0.649 GB). The legacy database was intentionally retained.

## Adaptive operating principles

Teclosync and its consumers should evolve inclusively and adaptively. These are operating defaults, not additional global release gates:

1. **Prefer partial success.** Process healthy sources and records even when another provider is unavailable or has changed format. Isolate errors to the affected source/entry and preserve last-good rows.
2. **Keep the catalogue useful.** A feed warning should not invalidate unrelated feeds. Report freshness, failure reason, rejected-entry counts and retained-last-good state; do not silently erase a source or its previous data.
3. **Recover carefully, not rigidly.** Parser recovery may accept a bounded, well-understood variation with fixtures and diagnostics. Never pair an orphan stream URL with the wrong station. An ambiguous entry can be skipped while safely parsed entries continue.
4. **Preserve ownership and provenance.** User-owned playlists, configuration, favourites and metadata remain additive and are never overwritten by synchronized provider catalogues. Keep provider IDs, provenance and distinct stream alternatives.
5. **Make checks proportional to the operation.** For destructive object cleanup, verify the exact object being removed against its retained replacement. If one object cannot be reconciled, preserve and report that object while proceeding with independent verified candidates.
6. **Use hard stops only at the point of actual integrity risk.** A missing or unverifiable snapshot may prevent restoring that snapshot; it should not disable unrelated healthy sources or make the whole product unavailable when another valid path exists. Do not turn optional metadata, a temporary provider outage or one malformed record into a universal gate.
7. **Evolve compatibly.** Prefer additive schema changes, migration support, tolerant readers and explicit diagnostics. Keep rollback snapshots and user data safe through migrations and refactors.

For BeastED integration, ChronSync A–D, Radsync, Teclosync, Viewsync and user-added IPTV capabilities remain first-class product capabilities. Shared improvements should benefit the unified application without narrowing or replacing existing user workflows.

## Current source freshness

The last controlled refresh published 26,934 accepted records from 29 healthy sources out of 33. Six invalid `[NO PUBLIC STREAM]` placeholders were rejected individually while 2,197 valid `free-tv-curated` records were retained. Four upstream problems remain visible and isolated:

- Cineverse — M3U structure error (`EXTINF without following stream URL`).
- Whale TV Plus — M3U structure error (`Stream URL without preceding EXTINF`).
- Samsung TV Plus Nordic Sweden — M3U structure error (`Stream URL without preceding EXTINF`).
- JRomero88 VOD — HTTP 404.

These are source-specific follow-ups, not R2 publication or storage failures. Last-good data is retained where available. Do not remove/repoint the JRomero88 VOD source to an unrelated live-TV playlist. Parser improvements should be tested and bounded, without risking station misattribution.

## Validation

The latest feature-branch CI run, [44671826829](https://github.com/Ibi-B/Teclosync/actions/runs/44671826829), passed Python compilation, configuration validation and all 77 unit tests. It includes adaptive production audit retries for transient Cloudflare/R2 read failures.

PR [#2](https://github.com/Ibi-B/Teclosync/pull/2) remains an open draft and has not been merged. The production R2 lifecycle and housekeeping actions are complete; remaining provider availability work should be tracked source-by-source and must not be treated as a blanket gate on healthy catalogue operation.

---



## Current production status — 20 November 2026

This section supersedes earlier point-in-time notes below where they say the live audit or lifecycle policy is still pending.

### Verified Cloudflare lifecycle policy

Production audit and apply run [44648240346](https://github.com/Ibi-B/Teclosync/actions/runs/44648240346) completed successfully. The audit artifact confirms that Cloudflare has the enabled rule `teclosync-archive-30-days` scoped exclusively to `telclosync/state/archive/`, with a 30-day age threshold (2,592,000 seconds). The rule was applied and read back successfully. The pre-existing `Default Multipart Abort Rule` was preserved. No expiry rule overlaps `current/` or `previous/`.

The same run verified current release `20261119T234644Z` and previous release `20261119T224927Z`: snapshot and artifact checksums passed, current SQLite integrity and foreign-key checks passed, and the current catalogue reconciled exactly to the database at 26,934 records with zero missing provenance, duplicate keys, blank names, or blank stream URLs.

Measured storage was 917,065,937 bytes under `telclosync/state` and 49,785 bytes under the separate diagnostics prefix, approximately 0.917 GB combined. This is below the configured 10 GB Standard-storage free allowance used for the operational comparison.

### Legacy pointer and staging cleanup — guarded, not yet executed

The audit exposed one stale root compatibility pointer, `telclosync/state/current.json`, which references archived release `20261118T195146Z` instead of canonical current `20261119T234644Z`. Runtime state access has now been changed to use canonical slot pointers only and to fail closed if canonical pointers are missing while legacy pointers or slot objects remain. The unsafe non-pointer `state/teclo-sync.db` recovery fallback was removed; that legacy object is retained as a forensic copy until separately reviewed.

A guarded cleanup script, `scripts/cleanup_production_r2.py`, was added. It requires a successful fresh read-only audit; independently verifies current, previous, and archived manifest/artifact/state bytes; checks legacy pointer references resolve to exact retained or staging manifest/database object pairs; groups temporary `releases/<run-id>/` objects; and only schedules exact staging objects whose complete object sets and bytes match an immutable retained copy. The older `20261118T195146Z` staging package lacks its own SQLite object; cleanup will proceed only if the retained archive database is byte-identical to the legacy state snapshot. Any mismatch, unknown pointer, unexpected object, or changed audit pointer aborts cleanup. The script deletes only the two known root compatibility pointer keys and the exact verified staging object keys; it preserves `telclosync/state/state/teclo-sync.db`.

The `main` branch audit workflow now has a separate `cleanup_legacy_objects` manual input (default false), serializes against refresh via the `teclosync-r2-production` concurrency group, runs the guarded cleanup only when explicitly selected, and re-audits after cleanup. The feature-branch refresh workflow uses the same concurrency group. **The production cleanup has not yet been dispatched or applied.** Run the audit workflow with `source_ref=feature/teelosync-foundation`, `apply_archive_expiry=false`, and `cleanup_legacy_objects=true`, then inspect the cleanup report and post-cleanup audit before declaring the layout clean.

The feature branch currently passes compilation, configuration validation, and 69 unit tests in [validation run 44650317811](https://github.com/Ibi-B/Teclosync/actions/runs/44650317811).

### Remaining upstream source issues

The latest controlled refresh continues to preserve 29 healthy sources and reports four upstream failures. They are separate from R2 storage and lifecycle:

- Cineverse — `ValueError: EXTINF without following stream URL`.
- Whale TV Plus — `ValueError: Stream URL without preceding EXTINF`.
- Samsung TV Plus Nordic Sweden — `ValueError: Stream URL without preceding EXTINF`.
- JRomero88 VOD — HTTP 404.

The Apsattv streams index still lists Cineverse and Whale TV Plus (the page describes these feeds as updated 22 April 2026), and lists the manually compiled Samsung Sweden playlist under non-app-region feeds. The playlist parser errors should be handled by a tested, bounded recovery strategy that never pairs an orphan URL with the wrong station. Do not silently relax parsing or publish misattributed records.

The JRomero88 repository currently documents `VOD.m3u` in its README, but the live `master` tree does not contain that file; the branch commit shown by GitHub is dated 30 August 2021. The repository's `IPTV.m3u` is a separate legacy general-TV playlist, not a verified VOD replacement. Do not repoint the VOD source to it. Keep the failed source visible until an authentic VOD feed is found or the source owner confirms the correct location.

No source was silently removed or replaced during this storage-maintenance work. Cross-provider provenance and distinct records remain intact.
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

`<TECLOSYNC_S3_PREFIX>/archive/` — expire objects after 30 days.

Do not apply expiration to `current/`, `previous/`, or the separate diagnostics prefix. The lifecycle rule itself is a bucket setting and has not been changed from this repository.

## Diagnostics

The refresh command now uploads its diagnostic JSON to `TECLOSYNC_DIAGNOSTICS_PREFIX/refresh-<run-id>.json` and also retains the GitHub Actions artifact. When no explicit diagnostics prefix is supplied, it uses `<TECLOSYNC_S3_PREFIX>/diagnostics/`. A diagnostics upload failure is recorded locally and does not reverse an otherwise successful release promotion.

## Validation status

GitHub Actions validation compiles all Python sources, validates the source registry, and runs the complete unit suite.

The latest completed branch validation is [44641276943](https://github.com/Ibi-B/Teclosync/actions/runs/44641276943) on commit `6de6589c9938726a41fbe42920e15a4ab012007b`: Python compilation, configuration validation, and **60 unit tests** all passed. It includes the 30-day archive manager and production/diagnostics storage-footprint audit code. The live-R2 production audit remains a separate manual workflow.

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
- `scripts/manage_archive_lifecycle.py` gates the 30-day expiry configuration on a successful production audit, integrity of both current and previous releases, archive pointer isolation, and absence of any enabled expiry rule overlapping current/previous. It preserves unrelated bucket lifecycle rules and then re-reads the policy to verify the new rule.
- Do not merge this foundation to `main` until the production audit completes, the archive-only 30-day lifecycle rule is configured and read back successfully, and the remaining source-quality issues are reviewed.


## Production R2 audit and archive lifecycle controls

- The read-only production audit is launched from the Actions workflow on the default branch: [Audit Teclosync production R2 layout and catalogue](https://github.com/Ibi-B/Teclosync/actions/workflows/audit-production-r2.yml).
- The configured production prefix observed in the 19 November refresh run is `telclosync/state`; diagnostics are deliberately outside that prefix at `telclosync/diagnostics`.
- The workflow defaults to read-only. The optional lifecycle apply input is off by default. Enabling it requests only `telclosync/state/archive/` (30 days) and is gated by the audit report. The Cloudflare API credentials are separate from the R2 S3 credentials: `CLOUDFLARE_ACCOUNT_ID` repository variable and `CLOUDFLARE_API_TOKEN` secret with Workers R2 Storage Write permission.
- No lifecycle rule has been changed yet. The live audit must first confirm the actual pointer/object layout and existing bucket rules.


- The audit checks each run-scoped pointer path against its slot and run ID, requires the immutable state checksum, checks all artifacts and M3U entry counts, reconciles every catalogue `(source_id, channel_key)` against SQLite, checks SQLite integrity and foreign keys, measures blank/malformed stream fields and missing provenance, and reports cross-provider stream URL overlaps without collapsing provider records.
- The lifecycle manager refuses to act if an existing enabled deletion rule overlaps `current/` or `previous/`, or if another deletion rule overlaps `archive/` under a different ID. It preserves other rules exactly, re-reads the lifecycle configuration before applying, and verifies the exact 30-day archive rule after the update.
- The main-branch workflow is available at [audit-production-r2.yml](https://github.com/Ibi-B/Teclosync/actions/workflows/audit-production-r2.yml). The first run should use `apply_archive_expiry=false`. A subsequent run may use `true` only after the audit artifact is reviewed and the Cloudflare API token is configured. The workflow is currently prepared but has not yet been dispatched.


## Production audit run 44638474282 — 20 November 2026

- The read-only production audit completed successfully against bucket prefix `telclosync/state`. It verified the current release `20261119T224927Z` and previous release `20261118T195146Z`; both state snapshots and all listed artifacts passed SHA-256 verification, the current SQLite database passed `PRAGMA integrity_check`, foreign-key checks returned no violations, and the current compressed catalogue reconciled exactly to SQLite at 26,941 records with no missing/extra/duplicate keys or missing provenance.
- The audit found six non-playable placeholder URLs in `free-tv-curated`: `[NO PUBLIC STREAM]`. These were incorrectly accepted as stream URLs and caused the lifecycle manager to reject the current catalogue. The source had 2,203 records, so excluding six still leaves it comfortably above its configured minimum of 200.
- The audit also confirmed the previous snapshot predates the `channel_duplicates` table migration. The audit now treats that table as an optional additive schema feature for legacy rollback snapshots; base channel/source/programme tables, SQLite integrity, artifact hashes, and catalogue-to-database key equality remain required.
- The lifecycle gate is now intentionally asymmetric: current must pass strict field/provenance quality checks; previous must pass storage integrity and exact catalogue/database reconciliation but may contain older catalogue-quality defects. It remains protected indefinitely and is not expired by this rule.
- The R2 prefix currently contains `current/`, `previous/`, a legacy root `current.json`, and 13 objects under `releases/`; no `archive/` objects exist yet. The audit has been extended to report the legacy pointer's run ID/equality with the canonical pointer and the exact residual release-staging object keys.
- The lifecycle apply attempt in run 44638474282 stopped at the current-catalogue quality gate before calling Cloudflare's lifecycle update API. The artifact records `applied: false`; no lifecycle rule was added or changed.
- A source-ingestion fix now rejects placeholder/malformed stream endpoints before staging, records rejected counts/examples in source diagnostics and release source metadata, and includes a regression test for `[NO PUBLIC STREAM]`. This preserves valid records from the source instead of failing the entire feed.
- Next operational sequence: validate the feature branch, run one controlled production refresh from the feature branch to publish a clean current release (the old current becomes the protected previous release), rerun the read-only production audit, inspect its artifact and existing Cloudflare lifecycle rules, then apply the 30-day rule only to `telclosync/state/archive/`.

## Production refresh run 44640444925 — 20 November 2026

- The controlled refresh completed successfully from `feature/teelosync-foundation` at commit `7c462f3ad08a2b20567f1e6087ab80f2c4e0232e`, publishing release `20261119T234644Z`.
- The diagnostics artifact confirms 29 healthy sources out of 33 and 26,934 accepted catalogue records. The six `[NO PUBLIC STREAM]` placeholders in `free-tv-curated` were rejected individually; 2,197 valid records from that source were retained.
- The four upstream failures remain Cineverse, Whale TV Plus, Samsung TV Plus Nordic Sweden (M3U shape errors), and JRomero88 VOD (HTTP 404). Publication was allowed and succeeded.
- Rotation advanced to current `20261119T234644Z`, previous `20261119T224927Z`, and archived `20261118T195146Z`. The refresh diagnostics were uploaded to `telclosync/diagnostics/refresh-20261119T234644Z.json`.
- Archive retention target has been tightened from 90 to 30 days, scoped only to `telclosync/state/archive/`. Current and previous remain indefinitely protected. The production audit now reports object counts and bytes by top-level state prefix and separately measures the configured diagnostics prefix, to compare actual occupancy with Cloudflare's 10 GB-month Standard-storage free allowance.
- No lifecycle rule has been applied. Run the read-only production audit first, review its integrity, pointer, lifecycle-rule, and storage-usage results, and only then consider the gated 30-day archive-only apply.
