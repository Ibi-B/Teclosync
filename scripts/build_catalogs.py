#!/usr/bin/env python3
"""Build and publish independent CloudStream and Telegram JSON catalogues."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from typing import Any

import aiohttp

from scripts.cloudstream import FetchJson, collect_cloudstream
from scripts.common import atomic_write_json, fetch_json as default_fetch_json, fetch_telegram_page as default_fetch_telegram_page, iso_now, new_catalog_version, read_config, write_json
from scripts.telegram import FetchPage, collect_telegram
from scripts.validate_catalog import validate_version


def _write_cloudstream(output_dir: Path, version: str, result: dict[str, Any]) -> None:
    version_dir = output_dir / "cloudstream" / f"v{version}"
    write_json(version_dir / "repositories.json", result["repositories"])
    write_json(version_dir / "plugins.json", result["plugins"])
    manifest = {
        "schemaVersion": 1,
        "version": version,
        "generatedAt": result["generatedAt"],
        "counts": {"repositories": len(result["repositories"]), "plugins": len(result["plugins"])},
        "files": {"repositories": "repositories.json", "plugins": "plugins.json"},
        "warnings": result["warnings"],
    }
    write_json(version_dir / "manifest.json", manifest)


def _write_telegram(output_dir: Path, version: str, result: dict[str, Any]) -> None:
    version_dir = output_dir / "telegram" / f"v{version}"
    for username, document in result["channelDocuments"].items():
        write_json(version_dir / "channels" / f"{username}.json", document)
    write_json(version_dir / "channels.json", result["channels"])
    post_count = sum(int(channel.get("postCount", 0)) for channel in result["channels"])
    manifest = {
        "schemaVersion": 1,
        "version": version,
        "generatedAt": result["generatedAt"],
        "counts": {"channels": len(result["channels"]), "posts": post_count},
        "files": {"channels": "channels.json"},
        "warnings": result["warnings"],
    }
    write_json(version_dir / "manifest.json", manifest)


def _publish_current_pointer(output_dir: Path, version: str) -> None:
    # A single root pointer promotes both independent module outputs together.
    # This avoids a mixed CloudStream/Telegram release if a process stops
    # between updating two separate current pointers.
    pointer = {
        "schemaVersion": 1,
        "version": version,
        "generatedAt": iso_now(),
        "modules": {
            "cloudstream": f"cloudstream/v{version}/manifest.json",
            "telegram": f"telegram/v{version}/manifest.json",
        },
    }
    atomic_write_json(output_dir / "catalogue.json", pointer)


async def build_release(
    config_dir: Path,
    output_dir: Path,
    version: str | None = None,
    *,
    cloudstream_fetch_json: FetchJson = default_fetch_json,
    telegram_fetch_page: FetchPage = default_fetch_telegram_page,
) -> dict[str, int]:
    version = version or new_catalog_version()
    cloudstream_config = read_config(config_dir / "cloudstream_repositories.json", "repositories")
    telegram_config = read_config(config_dir / "telegram_channels.json", "channels")

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=16)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        cloudstream_result, telegram_result = await asyncio.gather(
            collect_cloudstream(cloudstream_config, session, fetch_json=cloudstream_fetch_json),
            collect_telegram(telegram_config, session, fetch_page=telegram_fetch_page),
        )

    _write_cloudstream(output_dir, version, cloudstream_result)
    _write_telegram(output_dir, version, telegram_result)

    # Validate the fully written immutable version before exposing it through
    # either public current pointer. Empty catalogues are valid when no source
    # has been configured yet; source failures are reported in manifest warnings.
    counts = validate_version(output_dir, version)
    _publish_current_pointer(output_dir, version)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--version", help="Optional explicit version; generated automatically by default")
    args = parser.parse_args()
    try:
        counts = asyncio.run(build_release(args.config_dir, args.output_dir, args.version))
    except Exception as error:
        print(f"Catalogue build failed: {error}")
        return 1
    print("Built release: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
