#!/usr/bin/env python3
"""Validates the latest generated video catalog before publication."""
import json
import sys
from datetime import datetime
from pathlib import Path

OUTPUT = Path("output")


def main():
    pointer_path = OUTPUT / "current" / "catalog.json"
    if not pointer_path.exists():
        print("No current catalog pointer found")
        return 1

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    version = pointer.get("version")
    catalog_path = OUTPUT / f"v{version}" / "catalog.json"
    if not catalog_path.exists():
        print(f"Missing catalog for version {version}")
        return 1

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("version") != version:
        print("Catalog version mismatch")
        return 1

    items = catalog.get("items", [])
    if not items:
        print("Catalog contains no video items")
        return 1

    for item in items:
        if not item.get("id") or not item.get("title"):
            print("Invalid video item: missing id or title")
            return 1
        playback = item.get("playback", {})
        if not playback.get("url"):
            print(f"Invalid video item {item.get('id')}: missing playback URL")
            return 1
        expires_at = playback.get("expiresAt")
        if expires_at:
            try:
                datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                print(f"Invalid expiry timestamp for item {item.get('id')}")
                return 1

    print(f"Validated catalog version {version} with {len(items)} video items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
