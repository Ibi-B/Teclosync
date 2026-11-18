# Teclosync implementation status

Slice 2 adds the complete candidate source inventory and streaming M3U parser/normalizer. No source is enabled; all are candidates pending validation. Original URLs and query parameters are preserved. Movie and series playlists are classified as live-channel sources, not on-demand VOD.

Run:

```sh
python -m teclosync validate-config
python -m unittest discover -s tests -v
```

Remaining: HTTP ingestion, SQLite staging, refresh/failure isolation, coverage reporting, XMLTV, artifact generation, verified object storage, transactional publication, rotation tests, and controlled live validation.
