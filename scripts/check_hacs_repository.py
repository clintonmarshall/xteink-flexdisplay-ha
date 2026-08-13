#!/usr/bin/env python3
"""Run deterministic local HACS repository checks without GitHub API access."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    hacs = json.loads((ROOT / "hacs.json").read_text())
    manifest = json.loads(
        (ROOT / "custom_components/flexdisplay/manifest.json").read_text()
    )
    if hacs.get("name") != "FlexDisplay":
        errors.append("hacs.json name must be FlexDisplay")
    if manifest.get("domain") != "flexdisplay":
        errors.append("integration manifest domain must be flexdisplay")
    if not re.fullmatch(
        r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", manifest.get("version", "")
    ):
        errors.append("integration manifest version must be semantic")
    if not isinstance(manifest.get("codeowners"), list) or not manifest["codeowners"]:
        errors.append("integration manifest must declare at least one code owner")
    for required in ("__init__.py", "manifest.json", "config_flow.py"):
        if not (ROOT / "custom_components/flexdisplay" / required).is_file():
            errors.append(f"custom_components/flexdisplay/{required} is missing")
    if errors:
        print("Local HACS repository checks failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    print("Local HACS repository checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
