#!/usr/bin/env python3
"""Builds a versioned, atomic video catalog from configured sources."""
import json
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path("output")


def main():
    version = int(datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    version_dir = OUTPUT / f"v{version}"
    version_dir.mkdir(parents=True, exist_ok=True)

    config = json.loads(Path("config/sources.json").read_text(encoding="utf-8"))
    items = []

    for source in config.get("sources", []):
        source_id = source.get("id")
        if not source_id or not source.get("enabled", False):
            continue
        catalog_path = Path("sources") / f"{source_id}.json"
        if not catalog_path.exists():
            continue
        source_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        for item in source_catalog.get("items", []):
            item["sourceId"] = source_id
            items.append(item)

    catalog = {
        "version": version,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "itemCount": len(items),
        "items": items,
    }
    (version_dir / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    if not items:
        raise RuntimeError("No video items were produced; refusing to update current catalog")

    current = OUTPUT / "current"
    current.mkdir(exist_ok=True)
    pointer = {"version": version, "catalog": f"v{version}/catalog.json"}
    (current / "catalog.json").write_text(json.dumps(pointer, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
