"""CloudStream repository and plugin discovery ingestion."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp

from scripts.common import fetch_json as default_fetch_json, http_url, iso_now

FetchJson = Callable[[aiohttp.ClientSession, str], Awaitable[Any]]


def _text(value: Any) -> str | None:
    result = str(value).strip() if value is not None else ""
    return result or None


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _plugin_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("plugins", payload.get("extensions"))
        if rows is None and "url" in payload:
            rows = [payload]
    else:
        rows = None
    if not isinstance(rows, list):
        raise ValueError("Plugin list must be an array or contain plugins/extensions")
    return [row for row in rows if isinstance(row, dict)]


def parse_repository_manifest(payload: Any, manifest_url: str):
    if isinstance(payload, list):
        return {"name": urlsplit(manifest_url).netloc}, [], _plugin_rows(payload)
    if not isinstance(payload, dict):
        raise ValueError("Repository manifest must be an object or array")
    metadata = {
        "name": _text(payload.get("name")) or urlsplit(manifest_url).netloc,
        "description": _text(payload.get("description")),
        "websiteUrl": _text(payload.get("websiteUrl")),
        "iconUrl": _text(payload.get("iconUrl")),
    }
    if "pluginLists" in payload:
        raw = payload["pluginLists"]
        raw = [raw] if isinstance(raw, str) else raw
        if not isinstance(raw, list):
            raise ValueError("pluginLists must be a URL or array of URLs")
        return metadata, [http_url(str(item), manifest_url) for item in raw], None
    if "plugins" in payload or "extensions" in payload or "url" in payload:
        return metadata, [], _plugin_rows(payload)
    raise ValueError("Unrecognized CloudStream repository manifest")


def normalize_plugin(row: dict[str, Any], base_url: str, repository: dict[str, Any]) -> dict[str, Any]:
    raw_url = _text(row.get("url"))
    if not raw_url:
        raise ValueError("Plugin record has no url")
    url = http_url(raw_url, base_url)
    name = _text(row.get("name"))
    internal_name = _text(row.get("internalName")) or name or url.rsplit("/", 1)[-1]
    def optional_url(key: str):
        value = _text(row.get(key))
        return http_url(value, base_url) if value else None
    try:
        file_size = int(row["fileSize"]) if row.get("fileSize") is not None else None
    except (ValueError, TypeError):
        file_size = None
    return {
        "repositoryKey": repository["key"],
        "repositoryName": repository["name"],
        "name": name or internal_name,
        "internalName": internal_name,
        "url": url,
        "status": _int(row.get("status"), 1),
        "version": max(0, _int(row.get("version"), 0)),
        "apiVersion": max(0, _int(row.get("apiVersion"), 1)),
        "authors": _list(row.get("authors")),
        "description": _text(row.get("description")),
        "repositoryUrl": optional_url("repositoryUrl"),
        "tvTypes": _list(row.get("tvTypes")),
        "language": _text(row.get("language")),
        "iconUrl": optional_url("iconUrl"),
        "fileSize": max(0, file_size) if file_size is not None else None,
        "sourceUrl": base_url,
    }


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def collect_cloudstream(
    configured_repositories: list[dict[str, Any]],
    session: aiohttp.ClientSession,
    fetch_json: FetchJson = default_fetch_json,
) -> dict[str, Any]:
    repositories: list[dict[str, Any]] = []
    plugins: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    semaphore = asyncio.Semaphore(8)

    async def collect_one(config: dict[str, Any]):
        key = str(config.get("key", "")).strip()
        configured_url = str(config.get("url", "")).strip()
        if not key or not configured_url:
            return [], [], [{"source": key or "<missing-key>", "message": "Repository requires key and url"}]
        local_warnings: list[dict[str, str]] = []
        try:
            manifest_url = http_url(configured_url)
            async with semaphore:
                manifest_json = await fetch_json(session, manifest_url)
            metadata, list_urls, inline_rows = parse_repository_manifest(manifest_json, manifest_url)
            repository = {
                "key": key,
                "name": str(config.get("name") or metadata["name"]),
                "url": manifest_url,
                "description": config.get("description") or metadata.get("description"),
                "websiteUrl": config.get("websiteUrl") or metadata.get("websiteUrl"),
                "iconUrl": config.get("iconUrl") or metadata.get("iconUrl"),
                "pluginLists": list_urls,
                "fetchedAt": iso_now(),
            }
            raw_rows: list[tuple[dict[str, Any], str]] = []
            if inline_rows is not None:
                raw_rows.extend((row, manifest_url) for row in inline_rows)
            else:
                async def load_list(list_url: str):
                    try:
                        async with semaphore:
                            payload = await fetch_json(session, list_url)
                        return [(row, list_url) for row in _plugin_rows(payload)]
                    except Exception as error:
                        local_warnings.append({"source": key, "message": f"Plugin list {list_url}: {error}"})
                        return []
                loaded = await asyncio.gather(*(load_list(url) for url in list_urls))
                for rows in loaded:
                    raw_rows.extend(rows)

            by_name: dict[str, dict[str, Any]] = {}
            for raw, base in raw_rows:
                try:
                    item = normalize_plugin(raw, base, repository)
                    by_name.setdefault(item["internalName"].casefold(), item)
                except Exception as error:
                    local_warnings.append({"source": key, "message": f"Skipped malformed plugin row: {error}"})
            repository["pluginCount"] = len(by_name)
            return [repository], list(by_name.values()), local_warnings
        except Exception as error:
            return [], [], [{"source": key, "message": str(error)}]

    results = await asyncio.gather(*(collect_one(row) for row in configured_repositories))
    for repo_rows, plugin_rows, warning_rows in results:
        repositories.extend(repo_rows)
        plugins.extend(plugin_rows)
        warnings.extend(warning_rows)
    return {"repositories": repositories, "plugins": plugins, "warnings": warnings, "generatedAt": iso_now()}
