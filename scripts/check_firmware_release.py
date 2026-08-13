#!/usr/bin/env python3
"""Classify packaged device-firmware changes relative to a prior release tag."""

from __future__ import annotations

import argparse
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


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def current_version() -> str:
    return subprocess.run(
        [sys.executable, "scripts/check_release_metadata.py", "--print-version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def previous_tag(version: str) -> str:
    for tag in git_output(
        "tag", "--merged", "HEAD", "--sort=-version:refname", "v*"
    ).splitlines():
        if tag != f"v{version}":
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


def detected_classification(tag: str) -> tuple[str, list[str]]:
    changed: list[str] = []
    for path in FIRMWARE_PATHS:
        if git_output("rev-parse", f"HEAD:{path}") != git_output(
            "rev-parse", f"{tag}:{path}"
        ):
            changed.append(path)
    current_config = (ROOT / "flexdisplay_bridge/config.yaml").read_text()
    previous_config = git_output("show", f"{tag}:flexdisplay_bridge/config.yaml")
    current_values = config_values(current_config)
    previous_values = config_values(previous_config)
    changed.extend(
        f"flexdisplay_bridge/config.yaml:{key}"
        for key in CONFIG_KEYS
        if current_values[key] != previous_values[key]
    )
    return ("firmware-bearing" if changed else "software-only"), changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--classification",
        choices=("software-only", "firmware-bearing"),
        help="require the detected classification to match",
    )
    parser.add_argument("--previous-tag", help="compare with this exact prior tag")
    parser.add_argument("--print-previous-tag", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        tag = args.previous_tag or previous_tag(current_version())
        detected, changed = detected_classification(tag)
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"Firmware classification failed: {error}", file=sys.stderr)
        return 1
    if args.print_previous_tag:
        print(tag)
        return 0
    if args.classification and args.classification != detected:
        print(
            f"Release is {detected} relative to {tag}, not {args.classification}",
            file=sys.stderr,
        )
        for path in changed:
            print(f"- {path}", file=sys.stderr)
        return 1
    print(f"FlexDisplay release is {detected} relative to {tag}.")
    for path in changed:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
