#!/usr/bin/env python3
"""Render or verify the human-readable release status from release-manifest.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import check_release_metadata


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/RELEASE_STATUS.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if docs/RELEASE_STATUS.md differs from the manifest",
    )
    args = parser.parse_args()
    rendered = check_release_metadata.render_release_status(
        check_release_metadata.release_manifest()
    )
    if args.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != rendered:
            print(
                "docs/RELEASE_STATUS.md is stale; run "
                "python3 scripts/render_release_status.py",
                file=sys.stderr,
            )
            return 1
        print("Generated release status matches release-manifest.json.")
        return 0
    OUTPUT.write_text(rendered)
    print(f"Updated {OUTPUT.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
