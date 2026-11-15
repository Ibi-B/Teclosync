#!/usr/bin/env python3
"""Validate the current or candidate CloudStream and Telegram release."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(value: Any, schema_name: str, label: str) -> None:
    schema = _load(SCHEMAS / schema_name)
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(f"{label} at {'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors[:10])
        raise ValueError(details)


def validate_version(output_dir: Path, version: str) -> dict[str, int]:
    cloud_root = output_dir / "cloudstream" / f"v{version}"
    telegram_root = output_dir / "telegram" / f"v{version}"

    cloud_manifest = _load(cloud_root / "manifest.json")
    _validate_schema(cloud_manifest, "cloudstream-manifest.schema.json", "CloudStream manifest")
    repositories = _load(cloud_root / cloud_manifest["files"]["repositories"])
    plugins = _load(cloud_root / cloud_manifest["files"]["plugins"])
    _validate_schema(repositories, "cloudstream-repositories.schema.json", "CloudStream repositories")
    _validate_schema(plugins, "cloudstream-plugins.schema.json", "CloudStream plugins")
    if len(repositories) != cloud_manifest["counts"]["repositories"]:
        raise ValueError("CloudStream repository count does not match its manifest")
    if len(plugins) != cloud_manifest["counts"]["plugins"]:
        raise ValueError("CloudStream plugin count does not match its manifest")

    telegram_manifest = _load(telegram_root / "manifest.json")
    _validate_schema(telegram_manifest, "telegram-manifest.schema.json", "Telegram manifest")
    channels = _load(telegram_root / telegram_manifest["files"]["channels"])
    _validate_schema(channels, "telegram-channels.schema.json", "Telegram channels")
    if len(channels) != telegram_manifest["counts"]["channels"]:
        raise ValueError("Telegram channel count does not match its manifest")
    total_posts = 0
    for channel in channels:
        channel_path = telegram_root / channel["contentPath"]
        document = _load(channel_path)
        _validate_schema(document, "telegram-channel.schema.json", f"Telegram channel {channel['username']}")
        if document["username"].casefold() != channel["username"].casefold():
            raise ValueError(f"Telegram channel username mismatch for {channel['username']}")
        if len(document["posts"]) != channel["postCount"]:
            raise ValueError(f"Telegram post count mismatch for {channel['username']}")
        total_posts += len(document["posts"])
    if total_posts != telegram_manifest["counts"]["posts"]:
        raise ValueError("Telegram post count does not match its manifest")

    return {
        "cloudstreamRepositories": len(repositories),
        "cloudstreamPlugins": len(plugins),
        "telegramChannels": len(channels),
        "telegramPosts": total_posts,
    }


def validate_current(output_dir: Path) -> dict[str, int]:
    pointer_path = output_dir / "catalogue.json"
    pointer = _load(pointer_path)
    _validate_schema(pointer, "catalogue-pointer.schema.json", "Root catalogue pointer")
    if pointer.get("schemaVersion") != 1:
        raise ValueError("Root catalogue pointer has an unsupported schema version")
    version = str(pointer.get("version", ""))
    modules = pointer.get("modules", {})
    if not version or not isinstance(modules, dict):
        raise ValueError("Root catalogue pointer is missing its version or module paths")
    for module in ("cloudstream", "telegram"):
        relative = modules.get(module)
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"Root catalogue pointer is missing the {module} manifest")
        manifest_path = (output_dir / relative).resolve()
        if not manifest_path.is_file():
            raise ValueError(f"{module} manifest does not exist: {manifest_path}")
        manifest = _load(manifest_path)
        if manifest.get("version") != version:
            raise ValueError(f"{module} manifest version does not match the root pointer")
    return validate_version(output_dir, version)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--version", help="Validate a candidate version before moving current pointers")
    args = parser.parse_args()
    try:
        counts = validate_version(args.output_dir, args.version) if args.version else validate_current(args.output_dir)
    except Exception as error:
        print(f"Catalogue validation failed: {error}", file=sys.stderr)
        return 1
    print("Validated release: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
