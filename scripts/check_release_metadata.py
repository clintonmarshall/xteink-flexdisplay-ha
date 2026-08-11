#!/usr/bin/env python3
"""Validate that all FlexDisplay platform version markers agree."""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def yaml_version(path: Path) -> str:
    match = re.search(r'^version:\s*["\']?([^"\'\s]+)', path.read_text(), re.MULTILINE)
    if not match:
        raise ValueError(f"version not found in {path}")
    return match.group(1)


def package_version(path: Path) -> str:
    module = ast.parse(path.read_text(), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    return ast.literal_eval(node.value)
    raise ValueError(f"__version__ not found in {path}")


versions = {
    "Home Assistant app": yaml_version(ROOT / "flexdisplay_bridge/config.yaml"),
    "Python package": tomllib.loads(
        (ROOT / "flexdisplay_bridge/pyproject.toml").read_text()
    )["project"]["version"],
    "Bridge module": package_version(
        ROOT / "flexdisplay_bridge/flexdisplay_bridge/__init__.py"
    ),
    "HA integration": json.loads(
        (ROOT / "custom_components/flexdisplay/manifest.json").read_text()
    )["version"],
}

print_version = sys.argv[1:] == ["--print-version"]
expected = (
    sys.argv[1]
    if len(sys.argv) > 1 and not print_version
    else next(iter(versions.values()))
)
errors = [f"{name}: {version}" for name, version in versions.items() if version != expected]

if errors:
    print(f"Expected every platform version to be {expected}:", file=sys.stderr)
    print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
    raise SystemExit(1)

if print_version:
    print(expected)
else:
    print(f"FlexDisplay platform metadata is consistent at {expected}.")
