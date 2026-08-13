#!/usr/bin/env python3
"""Print one canonical release section from the Bridge changelog."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_notes(version: str) -> str:
    text = (ROOT / "flexdisplay_bridge/CHANGELOG.md").read_text()
    match = re.search(
        rf"^##\s+{re.escape(version)}\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"changelog section {version!r} was not found")
    body = match.group(1).strip()
    if not body:
        raise ValueError(f"changelog section {version!r} is empty")
    return body


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: release_notes.py X.Y.Z|vX.Y.Z", file=sys.stderr)
        return 2
    version = sys.argv[1].removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        print("release version is invalid", file=sys.stderr)
        return 2
    try:
        print(release_notes(version))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
