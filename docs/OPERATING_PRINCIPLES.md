# Operating principles: inclusive, adaptive and data-preserving

These principles guide Teclosync and its integration into BeastED. They favour continuity, graceful degradation and useful partial results over blanket rejection or avoidable gates.

## 1. Inclusive by default

A source-specific problem should normally remain source-specific. Continue processing independent healthy providers, records and capabilities. Do not disable unrelated functionality because one provider is unavailable, one optional field is absent, or one entry is malformed.

Where a result is partial, report which part succeeded, which part needs attention, and whether last-good data remains available. Avoid turning warnings into universal publication or feature gates.

## 2. Preserve last-good and user-owned data

- Keep last-known-good records when a provider fails or falls below its accepted refresh threshold.
- Treat Radsync and Teclosync catalogues as additive provider data.
- Never overwrite or merge away user-owned IPTV playlists, personal M3U sources, custom configuration, favourites, metadata or playback-related state.
- Preserve source IDs, provenance, provider distinctions and alternative stream URLs.
- Prefer additive migrations and tolerant readers over destructive schema replacement.

## 3. Adapt to source variation without inventing relationships

Support bounded, tested parser variations when the source format is understood. Keep diagnostics about the variation and the records affected. If an M3U entry cannot be associated safely, isolate or skip that entry; never pair a stream URL with the wrong station. Preserve the other correctly parsed entries whenever possible.

A single provider format change must not silently invalidate unrelated providers or erase last-good data.

## 4. Make safety checks proportional

Apply the narrowest check that protects the operation being performed:

- For a restore, verify the selected snapshot and its checksum.
- For pointer rotation, verify the target release and preserve rollback pointers.
- For object cleanup, verify that exact object's retained replacement and references.
- For catalogue publication, report source-level quality and freshness rather than conflating one failed provider with the whole catalogue.

When one candidate cannot be verified, preserve it and report why. Continue with independent candidates that are fully reconciled. Do not use a repository-wide block where an object-, record- or source-level response is enough.

A hard stop is appropriate when the specific operation would otherwise risk corrupting, misattributing or irreversibly deleting data and there is no verified alternative. It is not a reason to shut down healthy, independent capabilities.

## 5. Keep policy adaptive

Quality thresholds, format assumptions and source availability expectations should be evidence-based and revisitable. Prefer configurable thresholds and diagnostics over hard-coded assumptions that unnecessarily exclude valid data. Do not loosen validation so far that incorrect station/stream relationships are published; adapt around the unsafe item rather than rejecting the whole feed.

## 6. Treat BeastED as one product

ChronSync A–D, Radsync, Teclosync, Viewsync and user-added IPTV capabilities are all first-class. Shared improvements to caching, networking, indexing, state management, discovery and playback should improve the overall app while preserving each capability's established behaviour and data ownership.

Any shared refactor should first identify contracts and downstream dependencies, then preserve IDs, provenance, user metadata, playback state and backwards compatibility.

## 7. Report outcomes honestly

Distinguish success, partial success, warnings, skipped items and actual failure. Record what was preserved and why an item was skipped. Do not label a workflow wholly unsuccessful merely because an optional or independent cleanup candidate could not be processed. Equally, do not claim an operation succeeded when its result was not verified.

The goal is not to avoid all safeguards. It is to keep each safeguard scoped to the real risk, so the repository remains useful, recoverable and inclusive as providers, data formats and product capabilities evolve.
