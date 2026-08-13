#!/usr/bin/env python3
"""Validate synchronized FlexDisplay platform and release metadata."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_URL = re.compile(r"^(?:(?:https|ssh)://[^\s/]+/.+|[^@\s]+@[^:\s]+:.+)$")
PROVENANCE_PATH = ROOT / "flexdisplay_bridge/firmware/provenance.json"
PLACEHOLDERS = {"n/a", "na", "none", "pending", "tbd", "todo", "unknown", "unresolved"}
PROVENANCE_FIELDS = (
    "source_repository",
    "source_tag",
    "target_family",
    "target_board",
    "build_command",
    "toolchain",
    "artifact_type",
    "partition_write_scope",
)


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
                    return str(ast.literal_eval(node.value))
    raise ValueError(f"__version__ not found in {path}")


def android_version() -> str:
    text = (ROOT / "rook_receiver/app/build.gradle").read_text()
    match = re.search(r'^\s*versionName\s+["\']([^"\']+)', text, re.MULTILINE)
    if not match:
        raise ValueError("Android versionName was not found")
    return match.group(1)


def versions() -> dict[str, str]:
    return {
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


def validate_release_documents(expected: str) -> list[str]:
    errors: list[str] = []
    changelog = (ROOT / "flexdisplay_bridge/CHANGELOG.md").read_text()
    headings = re.findall(rf"^##\s+{re.escape(expected)}\s*$", changelog, re.MULTILINE)
    if len(headings) != 1:
        errors.append(
            f"flexdisplay_bridge/CHANGELOG.md must contain exactly one ## {expected} heading"
        )

    compatibility = (ROOT / "docs/COMPATIBILITY.md").read_text()
    if not re.search(
        rf"^\|\s*FlexDisplay platform\s*\|\s*{re.escape(expected)}\s*\|",
        compatibility,
        re.MULTILINE,
    ):
        errors.append(f"docs/COMPATIBILITY.md platform row is not {expected}")

    receiver = android_version()
    for family in ("Echo Spot receiver", "Echo Show 5 receiver"):
        if not re.search(
            rf"^\|\s*{re.escape(family)}\s*\|\s*{re.escape(receiver)}\s*\|",
            compatibility,
            re.MULTILINE,
        ):
            errors.append(
                f"docs/COMPATIBILITY.md {family} row is not Android versionName {receiver}"
            )
    return errors


def yaml_options(path: Path) -> dict[str, object]:
    options: dict[str, object] = {}
    in_options = False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_options = stripped == "options:"
            continue
        if in_options and indent == 2:
            key, separator, raw = stripped.partition(":")
            if not separator:
                raise ValueError(f"invalid App option: {stripped}")
            raw = raw.strip()
            if raw.startswith(("\"", "'")):
                value: object = ast.literal_eval(raw)
            elif re.fullmatch(r"[-+]?\d[\d_]*", raw):
                value = int(raw.replace("_", ""))
            else:
                value = raw
            options[key] = value
    return options


def literal_dictionary(path: Path, name: str) -> dict[str, object]:
    module = ast.parse(path.read_text(), filename=str(path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return value
    raise ValueError(f"literal dictionary {name} not found in {path}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provenance_issues(artifact: dict[str, Any]) -> list[str]:
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        return ["provenance must be an object"]
    issues = [
        f"{field} is missing or unresolved"
        for field in PROVENANCE_FIELDS
        if not isinstance(provenance.get(field), str)
        or not str(provenance[field]).strip()
        or str(provenance[field]).strip().casefold() in PLACEHOLDERS
    ]
    repository = provenance.get("source_repository")
    if isinstance(repository, str) and repository.strip() and not REPOSITORY_URL.fullmatch(repository.strip()):
        issues.append("source_repository must be an HTTPS, SSH, or SCP-style URL")
    commit = provenance.get("source_commit")
    if not isinstance(commit, str) or not GIT_SHA.fullmatch(commit.lower()):
        issues.append("source_commit must be a full 40-character hexadecimal SHA")
    if provenance.get("source_clean") is not True:
        issues.append("source_clean must be true")
    return issues


def validate_packaged_firmware(*, release: bool) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = json.loads(PROVENANCE_PATH.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return [f"cannot read {PROVENANCE_PATH}: {error}"], warnings
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return [f"{PROVENANCE_PATH} must be a schema_version 1 object"], warnings
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return [f"{PROVENANCE_PATH} artifacts must be a non-empty list"], warnings

    options = yaml_options(ROOT / "flexdisplay_bridge/config.yaml")
    firmware_root = (ROOT / "flexdisplay_bridge/firmware").resolve()
    runner_path = ROOT / "flexdisplay_bridge/app_runner.py"
    listed_paths: set[str] = set()
    seen_ids: set[str] = set()
    for index, raw in enumerate(artifacts, start=1):
        if not isinstance(raw, dict):
            errors.append(f"firmware artifact #{index} must be an object")
            continue
        artifact: dict[str, Any] = raw
        label = str(artifact.get("id") or f"artifact #{index}")
        if label in seen_ids:
            errors.append(f"duplicate firmware artifact id {label!r}")
        seen_ids.add(label)
        supplied = artifact.get("path")
        if not isinstance(supplied, str) or not supplied:
            errors.append(f"{label}: path must be repository-relative")
            continue
        resolved = (ROOT / supplied).resolve()
        if not resolved.is_relative_to(firmware_root) or not resolved.is_file():
            errors.append(f"{label}: packaged artifact path is absent or unsafe")
            continue
        listed_paths.add(supplied)

        advertised = {
            "version": artifact.get("version"),
            "url": artifact.get("url"),
            "size": artifact.get("size"),
            "sha256": artifact.get("sha256"),
        }
        if not isinstance(advertised["version"], str) or not advertised["version"]:
            errors.append(f"{label}: version must be a non-empty string")
        if not isinstance(advertised["url"], str) or not advertised["url"]:
            errors.append(f"{label}: url must be a non-empty string")
        if isinstance(advertised["size"], bool) or not isinstance(advertised["size"], int):
            errors.append(f"{label}: size must be an integer")
        if not isinstance(advertised["sha256"], str) or not SHA256.fullmatch(advertised["sha256"]):
            errors.append(f"{label}: sha256 must be 64 lowercase hexadecimal characters")
        if advertised["size"] != resolved.stat().st_size:
            errors.append(f"{label}: packaged size does not match provenance metadata")
        if advertised["sha256"] != sha256_file(resolved):
            errors.append(f"{label}: packaged SHA-256 does not match provenance metadata")

        prefix = artifact.get("config_prefix")
        constant = artifact.get("app_runner_constant")
        if not isinstance(prefix, str) or not isinstance(constant, str):
            errors.append(f"{label}: config_prefix and app_runner_constant are required")
        else:
            runner_values = literal_dictionary(runner_path, constant)
            for suffix, expected in advertised.items():
                key = f"{prefix}_{suffix}"
                if options.get(key) != expected:
                    errors.append(f"{label}: config.yaml {key} does not match provenance")
                if runner_values.get(key) != expected:
                    errors.append(f"{label}: app_runner.py {constant}.{key} does not match provenance")

        unresolved = provenance_issues(artifact)
        if unresolved:
            message = f"{label}: " + "; ".join(unresolved)
            (errors if release else warnings).append(message)

    packaged = {
        path.relative_to(ROOT).as_posix()
        for path in firmware_root.rglob("*.bin")
        if path.is_file()
    }
    for missing in sorted(packaged - listed_paths):
        errors.append(f"packaged firmware {missing} is absent from provenance metadata")
    return errors, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", nargs="?", help="expected X.Y.Z version")
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument(
        "--release",
        action="store_true",
        help="also require changelog and compatibility release metadata",
    )
    args = parser.parse_args()
    if args.print_version and args.expected:
        parser.error("--print-version does not accept an expected version")
    return args


def main() -> int:
    args = parse_args()
    found = versions()
    expected = args.expected or next(iter(found.values()))
    if not SEMVER.fullmatch(expected):
        print("expected version is not valid semantic version text", file=sys.stderr)
        return 2
    errors = [f"{name}: {version}" for name, version in found.items() if version != expected]
    firmware_errors, firmware_warnings = validate_packaged_firmware(release=args.release)
    errors.extend(firmware_errors)
    if args.release or args.expected:
        errors.extend(validate_release_documents(expected))
    if errors:
        print(f"FlexDisplay metadata validation failed for {expected}:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    for warning in firmware_warnings:
        print(f"WARNING: firmware provenance unresolved: {warning}", file=sys.stderr)
    if args.print_version:
        print(expected)
    else:
        scope = "release metadata" if args.release or args.expected else "platform metadata"
        print(f"FlexDisplay {scope} is consistent at {expected}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
