#!/usr/bin/env python3
"""Validate synchronized FlexDisplay platform and release metadata."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


def yaml_scalar(path: Path, key: str) -> str:
    match = re.search(
        rf"^\s*{re.escape(key)}:\s*[\"']?([^\"'\s]+)",
        path.read_text(),
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"{key} not found in {path.relative_to(ROOT)}")
    return match.group(1)


def package_version(path: Path) -> str:
    module = ast.parse(path.read_text(), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    return str(ast.literal_eval(node.value))
    raise ValueError(f"__version__ not found in {path.relative_to(ROOT)}")


def platform_versions() -> dict[str, str]:
    return {
        "Home Assistant app": yaml_scalar(
            ROOT / "flexdisplay_bridge/config.yaml", "version"
        ),
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


def compatibility_versions() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in (ROOT / "docs/COMPATIBILITY.md").read_text().splitlines():
        match = re.match(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", line)
        if match:
            rows[match.group(1).strip()] = match.group(2).strip().strip("`")
    return rows


def receiver_metadata(text: str | None = None) -> tuple[str, int]:
    content = text or (ROOT / "rook_receiver/app/build.gradle").read_text()
    version_name = re.search(r'^\s*versionName\s+["\']([^"\']+)', content, re.MULTILINE)
    version_code = re.search(r"^\s*versionCode\s+([0-9]+)", content, re.MULTILINE)
    if not version_name or not version_code:
        raise ValueError("Android versionName/versionCode not found")
    return version_name.group(1), int(version_code.group(1))


def git_output(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"git {' '.join(args)} failed: {error}") from error


def previous_release_tag(current_version: str) -> str:
    for tag in git_output(
        "tag", "--merged", "HEAD", "--sort=-version:refname", "v*"
    ).splitlines():
        if tag != f"v{current_version}":
            return tag
    raise ValueError("no preceding merged semantic release tag could be verified")


def packaged_artifact_errors() -> list[str]:
    config = ROOT / "flexdisplay_bridge/config.yaml"
    artifacts = (
        ("X3/X4", "firmware", "firmware.bin"),
        ("Note 4", "note4_firmware", "note4.bin"),
    )
    errors: list[str] = []
    for label, prefix, filename in artifacts:
        artifact = ROOT / "flexdisplay_bridge/firmware" / filename
        payload = artifact.read_bytes()
        expected_size = int(yaml_scalar(config, f"{prefix}_size"))
        expected_sha = yaml_scalar(config, f"{prefix}_sha256")
        actual_sha = hashlib.sha256(payload).hexdigest()
        if len(payload) != expected_size:
            errors.append(
                f"{label} artifact size is {len(payload)}, expected {expected_size}"
            )
        if actual_sha != expected_sha:
            errors.append(
                f"{label} artifact SHA-256 is {actual_sha}, expected {expected_sha}"
            )
    return errors


def release_errors(expected: str) -> list[str]:
    errors: list[str] = []
    compatibility = compatibility_versions()
    if compatibility.get("FlexDisplay platform") != expected:
        errors.append(
            "docs/COMPATIBILITY.md FlexDisplay platform row is "
            f"{compatibility.get('FlexDisplay platform')!r}, expected {expected!r}"
        )

    changelog = (ROOT / "flexdisplay_bridge/CHANGELOG.md").read_text()
    if not re.search(rf"^##\s+{re.escape(expected)}\s*$", changelog, re.MULTILINE):
        errors.append(f"flexdisplay_bridge/CHANGELOG.md has no '## {expected}' heading")

    receiver_version, receiver_code = receiver_metadata()
    for component in ("Echo Spot receiver", "Echo Show 5 receiver"):
        if compatibility.get(component) != receiver_version:
            errors.append(
                f"{component} compatibility version is {compatibility.get(component)!r}, "
                f"expected Android versionName {receiver_version!r}"
            )

    try:
        previous_tag = previous_release_tag(expected)
        previous_gradle = git_output(
            "show", f"{previous_tag}:rook_receiver/app/build.gradle"
        )
        _, previous_code = receiver_metadata(previous_gradle)
        receiver_changed = git_output("rev-parse", "HEAD:rook_receiver") != git_output(
            "rev-parse", f"{previous_tag}:rook_receiver"
        )
        if receiver_changed and receiver_code <= previous_code:
            errors.append(
                f"Android versionCode {receiver_code} must exceed {previous_code} from "
                f"{previous_tag} when rook_receiver changes"
            )
    except ValueError as error:
        errors.append(str(error))

    errors.extend(packaged_artifact_errors())
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", nargs="?", help="expected platform version")
    parser.add_argument(
        "--release",
        action="store_true",
        help="also validate changelog, compatibility, Android, and artifacts",
    )
    parser.add_argument("--print-version", action="store_true")
    args = parser.parse_args()
    if args.print_version and (args.expected or args.release):
        parser.error("--print-version cannot be combined with expected or --release")
    return args


def main() -> int:
    args = parse_args()
    versions = platform_versions()
    expected = args.expected or next(iter(versions.values()))
    errors: list[str] = []
    if not SEMVER.fullmatch(expected):
        errors.append(f"expected platform version is not semantic: {expected!r}")
    errors.extend(
        f"{name}: {version} (expected {expected})"
        for name, version in versions.items()
        if version != expected
    )
    if args.release and not errors:
        errors.extend(release_errors(expected))

    if errors:
        print("FlexDisplay metadata validation failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    if args.print_version:
        print(expected)
    else:
        mode = "release metadata" if args.release else "platform metadata"
        print(f"FlexDisplay {mode} is consistent at {expected}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
