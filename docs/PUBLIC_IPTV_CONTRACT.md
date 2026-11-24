# Teclosync public IPTV delivery contract

Status: implementation added on `feature/teelosync-foundation`; first production refresh/public publication still requires a manual workflow dispatch.

## Delivery

- Public index: `https://raw.githubusercontent.com/Ibi-B/Teclosync/teclosync-public/iptv/catalogue.json`
- Playlist files: relative paths in the index, currently `playlists/<source-id>.m3u8`.
- Published branch: `teclosync-public` (read-only delivery mirror, not the canonical state store).
- Canonical state remains in protected Cloudflare R2 `telclosync/state/current/`, `previous/`, and `archive/` slots.

## Schema version 1

The root JSON object has `schemaVersion`, `provider`, `releaseId`, `generatedAt`, `playlistCount`, `channelCount`, `categories`, `regions`, and `playlists`.

Each playlist entry provides:

- `id`: stable configured source ID.
- `name`, `category`, `region`.
- `channelCount`: number of included playable entries in this source playlist.
- `status`: latest source refresh status.
- `dataState`: `fresh`, `retained_last_good`, or `unavailable`.
- `path`: relative path to the M3U file.

`channelCount` at the root is the sum of source playlist entries; a channel present in two independent provider lists is intentionally counted in each list. This export does not merge or erase source provenance.

## Public-safety boundary

The export contains only source M3U files and the small index. It does not expose R2 pointers, private SQLite databases, state snapshots, write credentials, or full provenance payloads. Stream endpoints with URL userinfo or common credential-bearing query keys are excluded from the public M3U. The export is optional: if it fails, the Teclosync R2 snapshot validation and rotation continue independently. An empty public export does not replace a previously published public index.

## Consumer behavior

BeastED reads the index with a 1 MiB response cap and a 15-second timeout. It accepts schema version 1 and only same-origin HTTPS paths matching `playlists/<id>.m3u8`. A selected playlist is loaded through the existing BeastED M3U parser and source lifecycle with ID `public-teclosync:<id>`. The IPTV-Org direct category/country browser remains as a separate fallback. No R2 credentials are stored in BeastED.

## Initial publication

The `Refresh Telclosync catalogue` GitHub Actions workflow remains manual. To publish the first static feed, run the workflow on `feature/teelosync-foundation` after reviewing the expected controlled R2 refresh. The job restores the current verified R2 state, performs the existing source-level refresh and validation, publishes the canonical R2 snapshot, then pushes `state/artifacts/public/iptv/` to `teclosync-public` using the workflow's `GITHUB_TOKEN` with `contents: write` permission. Verify that the public index and at least one listed playlist return HTTP 200 before declaring the mobile feed live.

CloudStream and Telegram are separate future integrations and are not part of this IPTV delivery contract.
