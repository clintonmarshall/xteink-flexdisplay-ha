#!/usr/bin/env python3
"""Validate the checked-out FlexDisplay integration with HACS source rules.

This script is run inside the content-addressed HACS Action container. It reads
only the local checkout, so Forgejo pull-request jobs do not need a GitHub token
or access to a mirrored ref. Repository-level GitHub metadata is checked by the
separate secretless ``check_hacs_repository.py`` gate.

Forgejo also runs this exact-checkout validator for release-infrastructure
changes, so a commit cannot become a release candidate using only stale
integration evidence.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

from custom_components.hacs.utils.validate import (
    HACS_MANIFEST_JSON_SCHEMA,
    INTEGRATION_MANIFEST_JSON_SCHEMA,
)
from voluptuous import Invalid


ROOT = Path(__file__).resolve().parents[1]
HACS_MANIFEST = ROOT / "hacs.json"
COMPONENTS = ROOT / "custom_components"
INTEGRATION = COMPONENTS / "flexdisplay"
INTEGRATION_MANIFEST = INTEGRATION / "manifest.json"
BRAND_ICON = INTEGRATION / "brand" / "icon.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def load_json(path: Path) -> dict[str, object]:
    """Load a JSON object while rejecting duplicate keys."""

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(path.read_text(), object_pairs_hook=unique_object)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{path.relative_to(ROOT)}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return payload


def main() -> int:
    errors: list[str] = []

    component_directories = sorted(
        path.name for path in COMPONENTS.iterdir() if path.is_dir()
    )
    if component_directories != ["flexdisplay"]:
        errors.append(
            "custom_components must contain exactly the flexdisplay integration; "
            f"found {component_directories!r}"
        )

    for label, path, schema in (
        ("HACS manifest", HACS_MANIFEST, HACS_MANIFEST_JSON_SCHEMA),
        (
            "integration manifest",
            INTEGRATION_MANIFEST,
            INTEGRATION_MANIFEST_JSON_SCHEMA,
        ),
    ):
        try:
            schema(load_json(path))
        except (Invalid, ValueError) as error:
            errors.append(f"{label}: {error}")

    if not (INTEGRATION / "__init__.py").is_file():
        errors.append("custom_components/flexdisplay/__init__.py is missing")
    try:
        icon = BRAND_ICON.read_bytes()
        if not icon.startswith(PNG_SIGNATURE) or len(icon) < 24:
            raise ValueError("not a valid PNG file")
        width, height = struct.unpack(">II", icon[16:24])
        if width != height or width < 256:
            raise ValueError(
                f"must be a square PNG of at least 256 px; found {width} x {height}"
            )
    except (OSError, ValueError, struct.error) as error:
        errors.append(f"custom_components/flexdisplay/brand/icon.png: {error}")
    if not (ROOT / "README.md").is_file():
        errors.append("README.md is missing")
    if not any((ROOT / name).is_file() for name in ("LICENSE", "LICENSE.md")):
        errors.append("a repository LICENSE or LICENSE.md file is required")

    if errors:
        print("Local HACS validation failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1

    print("Exact-checkout HACS source validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
