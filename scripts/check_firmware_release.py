#!/usr/bin/env python3
"""Classify packaged device-firmware changes relative to a prior release tag."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_PATHS = (
    "flexdisplay_bridge/firmware/firmware.bin",
    "flexdisplay_bridge/firmware/note4.bin",
)
CONFIG_KEYS = (
    "firmware_version",
    "firmware_url",
    "firmware_sha256",
    "firmware_size",
    "note4_firmware_version",
    "note4_firmware_url",
    "note4_firmware_sha256",
    "note4_firmware_size",
)


def git_output(*args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=not binary
    )
    return result.stdout if binary else result.stdout.strip()


def previous_tag(current_version: str) -> str:
    tags = str(git_output("tag", "--merged", "HEAD", "--sort=-version:refname", "v*")).splitlines()
    for tag in tags:
        if tag != f"v{current_version}":
            return tag
    raise ValueError("no preceding merged release tag was found")


def config_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in CONFIG_KEYS:
        match = re.search(rf"^\s*{key}:\s*[\"']?([^\"'\s]+)", text, re.MULTILINE)
        if not match:
            raise ValueError(f"{key} is absent from flexdisplay_bridge/config.yaml")
        values[key] = match.group(1)
    return values


def classify(tag: str) -> tuple[str, list[str], list[str]]:
    changed: list[str] = []
    evidence: list[str] = []
    for path in FIRMWARE_PATHS:
        current = (ROOT / path).read_bytes()
        previous = bytes(git_output("show", f"{tag}:{path}", binary=True))
        current_sha = hashlib.sha256(current).hexdigest()
        previous_sha = hashlib.sha256(previous).hexdigest()
        evidence.append(
            f"{path}: current={len(current)}:{current_sha} previous={len(previous)}:{previous_sha}"
        )
        if current != previous:
            changed.append(path)
    current_values = config_values((ROOT / "flexdisplay_bridge/config.yaml").read_text())
    previous_values = config_values(str(git_output("show", f"{tag}:flexdisplay_bridge/config.yaml")))
    for key in CONFIG_KEYS:
        if current_values[key] != previous_values[key]:
            changed.append(f"flexdisplay_bridge/config.yaml:{key}")
    return ("firmware-bearing" if changed else "software-only"), changed, evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classification", choices=("software-only", "firmware-bearing"))
    parser.add_argument("--previous-tag")
    args = parser.parse_args()
    try:
        version = subprocess.run(
            [sys.executable, "scripts/check_release_metadata.py", "--print-version"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tag = args.previous_tag or previous_tag(version)
        detected, changed, evidence = classify(tag)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"Firmware classification failed: {error}", file=sys.stderr)
        return 1
    if args.classification and args.classification != detected:
        print(f"Release is {detected} relative to {tag}, not {args.classification}", file=sys.stderr)
        for item in changed:
            print(f"- {item}", file=sys.stderr)
        return 1
    print(f"FlexDisplay release is {detected} relative to {tag}.")
    for item in evidence:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
