"""Shared I/O helpers for the Teclosync publishing pipelines."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp

REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "Teclosync/0.1 (catalogue refresh)"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_catalog_version() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def read_config(path: Path, key: str) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get(key, []), list):
        raise ValueError(f"{path}: expected JSON object with array '{key}'")
    rows = value.get(key, [])
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path}: all '{key}' entries must be objects")
    return rows


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def atomic_write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, value)
    temporary.replace(path)


def http_url(value: str, base: str | None = None) -> str:
    resolved = urljoin(base, value) if base else value
    parsed = urlsplit(resolved.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Expected an HTTP(S) URL: {value!r}")
    path = parsed.path
    if parsed.netloc.lower() == "github.com" and "/blob/" in path:
        path = path.replace("/blob/", "/", 1)
        parsed = parsed._replace(netloc="raw.githubusercontent.com", path=path)
    return urlunsplit(parsed)


async def fetch_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(http_url(url), headers={"User-Agent": USER_AGENT},
                           timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)) as response:
        response.raise_for_status()
        return await response.read()


async def fetch_json(session: aiohttp.ClientSession, url: str):
    raw = await fetch_bytes(session, url)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid JSON from {url}: {error}") from error


async def fetch_telegram_page(session: aiohttp.ClientSession, url: str, *, more: bool = False) -> str:
    headers = {"User-Agent": USER_AGENT}
    if more:
        headers.update({"X-Requested-With": "XMLHttpRequest", "Referer": url.split("?", 1)[0]})
        request = session.post(url, headers=headers, data={},
                               timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS))
    else:
        request = session.get(url, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS))
    async with request as response:
        response.raise_for_status()
        body = await response.text(errors="replace")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("html", "fragment", "content"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return body
