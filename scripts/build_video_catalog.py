#!/usr/bin/env python3
"""Compatibility entry point; builds the CloudStream and Telegram catalogues."""
from scripts.build_catalogs import main

if __name__ == "__main__":
    raise SystemExit(main())
