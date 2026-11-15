# Teclosync

Teclosync prepares CloudStream repository discovery data and public Telegram channel data for BeastED. Collection, parsing, normalization and JSON generation happen here; BeastED remains responsible for the user experience and media playback.

## Current modules

- **CloudStream** reads configured repository manifests and plugin lists, normalizes the repository and plugin metadata, and publishes separate repository and plugin indexes. It does not execute extensions or resolve playable links; those remain in BeastED's existing Android CloudStream runtime.
- **Telegram** reads configured public channel previews (`t.me/s/<username>`), paginates a configured number of preview pages, and publishes channel metadata and normalized posts/media references. This scheduled pipeline serves a curated channel list. Arbitrary user-entered channels need a separate on-demand parser service or an app-side parser.

The modules have separate source configurations, output directories, schemas and catalogue manifests. They share only common fetch and publication helpers.

## Configuration

Add every CloudStream repository to `config/cloudstream_repositories.json`:

```json
{
  "schemaVersion": 1,
  "repositories": [
    {
      "key": "example-repository",
      "name": "Example Repository",
      "url": "https://example.org/repository.json"
    }
  ]
}
```

`url` may point to a CloudStream repository manifest, plugin-list JSON array, wrapper object containing `plugins`/`extensions`, or a single plugin record. `pluginLists` can be a single URL or a list of URLs. Relative plugin-list and package URLs are resolved against the document that contains them. GitHub `/blob/` links are converted to `raw.githubusercontent.com` links.

Add public Telegram channels to `config/telegram_channels.json`:

```json
{
  "schemaVersion": 1,
  "channels": [
    {"username": "@example_channel", "maxPages": 3}
  ]
}
```

A username, `@username`, `t.me/username` or `https://t.me/s/username` can be used. `maxPages` controls how many preview pages are collected for that channel. The first page is fetched with GET; older pages use Telegram's `before` cursor through the XMLHttpRequest POST used by its infinite-scroll endpoint. The parser preserves both plain post text and the original inner HTML markup.

No provider approval, trust score, provider-ID allowlist, signature check or source-approval gate is applied. Every configured repository and channel is processed. Structural parsing errors and upstream fetch failures are reported in each module's manifest warnings; one failed source does not suppress successful sibling sources.

## Output layout

GitHub Pages publishes the `output/` directory:

```text
output/
  catalogue.json                 # one atomic pointer for both modules
  cloudstream/
    v<version>/manifest.json
    v<version>/repositories.json
    v<version>/plugins.json
  telegram/
    v<version>/manifest.json
    v<version>/channels.json
    v<version>/channels/<username>.json
```

`catalogue.json` is a small root pointer to both immutable versioned manifests. It is promoted atomically only after both module outputs validate, so BeastED cannot accidentally read mismatched CloudStream and Telegram versions. The module manifests include a schema version, catalogue version, generated timestamp, counts, file paths and warnings. BeastED should read the root pointer, select the relevant module manifest, and then load that module's files. It should not assume CloudStream plugin data and Telegram posts share the same record model.

## Local development

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/build_catalogs.py
python scripts/validate_catalog.py
```

The default source configurations are intentionally empty, so a local build produces valid empty catalogues without making live requests. Add actual source entries to exercise live collection. Tests use checked-in fixtures and do not depend on external websites.

You can build into another directory or choose a deterministic version for testing:

```bash
python scripts/build_catalogs.py --config-dir config --output-dir /tmp/teclosync-output --version test-run
python scripts/validate_catalog.py --output-dir /tmp/teclosync-output
```

## Workflows

- `validate.yml` runs compilation, fixture tests, an empty/default catalogue build and catalogue validation on pushes, pull requests and manual dispatch.
- `refresh.yml` runs on a daily schedule or manual dispatch, builds and validates both modules, then publishes `output/` through GitHub Pages.

The first successful Pages deployment establishes the live base URL. The app integration should use that URL as the root and keep CloudStream and Telegram consumers independent.
