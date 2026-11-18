# Teclosync implementation status

This commit introduces a standalone registry validator and CLI without replacing existing scripts or changing workflows. It does not implement ingestion or publication.

Run from the repository root with Python 3.12:

```sh
python -m teclosync validate-config
python -m teclosync list-sources
python -m unittest discover -s tests -v
```

The existing version-1 registry format is preserved. Empty registries validate structurally but do not imply a usable catalogue; future publication must reject empty builds. Candidates cannot be enabled before validation. Repository and directory references are tracked separately from directly ingestible feeds. Original URLs and query parameters remain unchanged.

Remaining: complete source inventory, bounded HTTP fetching, M3U parsing, SQLite staging, per-source last-good retention, coverage diagnostics, XMLTV integration with existing EPG services, artifact generation, verified object storage and transactional manifest publication, rotation tests and controlled live validation.

Do not interpret movie or series live-channel playlists as on-demand VOD. Preserve source-scoped EPG identifiers and stream alternatives. Do not copy Radsync's unresolved production issues into this implementation.

No R2 credentials, lifecycle rules, deployment settings or production schedules are changed by this commit. Tests are supplied but not claimed to have run in GitHub Actions.
