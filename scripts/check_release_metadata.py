#!/usr/bin/env python3
"""Validate FlexDisplay platform metadata, with stricter release gates."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@total_ordering
@dataclass(frozen=True, eq=False)
class SemVer:
    """A strict Semantic Version, compared using SemVer precedence rules."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()
    __hash__ = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        self_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if self_core != other_core:
            return self_core < other_core
        if self.prerelease == other.prerelease:
            return False
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for self_identifier, other_identifier in zip(
            self.prerelease, other.prerelease
        ):
            if self_identifier == other_identifier:
                continue
            self_numeric = self_identifier.isdigit()
            other_numeric = other_identifier.isdigit()
            if self_numeric and other_numeric:
                return int(self_identifier) < int(other_identifier)
            if self_numeric != other_numeric:
                return self_numeric
            return self_identifier < other_identifier
        return len(self.prerelease) < len(other.prerelease)


def parse_semver(value: str) -> SemVer:
    """Parse a complete SemVer 2.0.0 value or raise a descriptive error."""

    match = SEMVER.fullmatch(value)
    if not match:
        raise ValueError(f"{value!r} is not strict SemVer")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    for identifier in prerelease:
        if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
            raise ValueError(
                f"{value!r} has a numeric prerelease identifier with a leading zero"
            )
    build = tuple(match.group(5).split(".")) if match.group(5) else ()
    return SemVer(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        prerelease,
        build,
    )


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
            rows[match.group(1)] = match.group(2).strip().strip("`")
    return rows


def receiver_metadata(text: str | None = None) -> tuple[str, int]:
    content = text or (ROOT / "rook_receiver/app/build.gradle").read_text()
    version_name = re.search(r'^\s*versionName\s+["\']([^"\']+)', content, re.MULTILINE)
    version_code = re.search(r"^\s*versionCode\s+([0-9]+)", content, re.MULTILINE)
    if not version_name or not version_code:
        raise ValueError("Android versionName/versionCode not found")
    return version_name.group(1), int(version_code.group(1))


def merged_release_tags() -> list[str]:
    try:
        return subprocess.run(
            ["git", "tag", "--merged", "HEAD", "--list", "v*"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"cannot enumerate preceding release tags: {error}") from error


def tag_targets_head(tag: str) -> bool:
    try:
        tag_commit = subprocess.run(
            ["git", "rev-list", "-n", "1", tag],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        head_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"cannot resolve current release tag {tag}: {error}") from error
    return tag_commit == head_commit


def preceding_release_tags(current_version: str) -> list[str]:
    """Return reachable release tags below current_version, newest first.

    The exact current tag is ignored only when it resolves to HEAD, so the same
    gate works before tag creation and from the immutable tag after creation.
    Any other reachable tag with equal or higher SemVer precedence makes the
    candidate non-monotonic.
    """

    current = parse_semver(current_version)
    exact_current_tag = f"v{current_version}"
    parsed_tags: list[tuple[SemVer, str]] = []
    for tag in merged_release_tags():
        if tag == exact_current_tag and tag_targets_head(tag):
            continue
        try:
            version = parse_semver(tag.removeprefix("v"))
        except ValueError as error:
            raise ValueError(f"reachable release tag {tag!r} is invalid: {error}") from error
        parsed_tags.append((version, tag))

    if parsed_tags:
        latest_version, latest_tag = max(parsed_tags, key=lambda entry: entry[0])
        if current <= latest_version:
            raise ValueError(
                f"release version {current_version} must exceed latest reachable "
                f"release {latest_tag}"
            )
    return [tag for _, tag in sorted(parsed_tags, reverse=True)]


def previous_receiver_metadata(current_version: str) -> tuple[str, int, str]:
    for tag in preceding_release_tags(current_version):
        try:
            content = subprocess.run(
                ["git", "show", f"{tag}:rook_receiver/app/build.gradle"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except subprocess.CalledProcessError:
            continue
        except OSError as error:
            raise ValueError(f"cannot read Android metadata from {tag}: {error}") from error
        try:
            version_name, version_code = receiver_metadata(content)
        except ValueError as error:
            raise ValueError(f"invalid Android metadata in {tag}: {error}") from error
        return version_name, version_code, tag
    raise ValueError("no preceding tagged Android receiver release could be verified")


def git_path_changed(previous_tag: str, path: str) -> bool:
    try:
        comparison = subprocess.run(
            ["git", "diff", "--quiet", previous_tag, "HEAD", "--", path],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise ValueError(f"cannot compare {path} with {previous_tag}: {error}") from error
    if comparison.returncode not in (0, 1):
        raise ValueError(
            f"cannot compare {path} with {previous_tag}: git exited "
            f"{comparison.returncode}"
        )
    return comparison.returncode == 1


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
                f"{label} packaged artifact size is {len(payload)}, expected {expected_size}"
            )
        if actual_sha != expected_sha:
            errors.append(
                f"{label} packaged artifact SHA-256 is {actual_sha}, expected {expected_sha}"
            )
    return errors


def compatibility_errors(expected: str, receiver_version: str) -> list[str]:
    compatibility = compatibility_versions()
    config = ROOT / "flexdisplay_bridge/config.yaml"
    expected_rows = {
        "FlexDisplay platform": expected,
        "Echo Spot receiver": receiver_version,
        "Echo Show 5 receiver": receiver_version,
        "X3/X4 packaged firmware": yaml_scalar(config, "firmware_version"),
        "Note 4 packaged firmware": yaml_scalar(config, "note4_firmware_version"),
    }
    return [
        f"{component} compatibility version is {compatibility.get(component)!r}, "
        f"expected {version!r}"
        for component, version in expected_rows.items()
        if compatibility.get(component) != version
    ]


def release_errors(expected: str) -> list[str]:
    errors: list[str] = []

    changelog = (ROOT / "flexdisplay_bridge/CHANGELOG.md").read_text()
    if not re.search(rf"^##\s+{re.escape(expected)}\s*$", changelog, re.MULTILINE):
        errors.append(f"flexdisplay_bridge/CHANGELOG.md has no '## {expected}' heading")

    receiver_version, receiver_code = receiver_metadata()
    errors.extend(compatibility_errors(expected, receiver_version))
    if receiver_code <= 0:
        errors.append("Android versionCode must be positive")
    try:
        previous_version, previous_code, previous_tag = previous_receiver_metadata(expected)
        receiver_changed = git_path_changed(previous_tag, "rook_receiver")
        if receiver_changed and receiver_code <= previous_code:
            errors.append(
                f"Android versionCode {receiver_code} must exceed {previous_code} from "
                f"{previous_tag} when receiver metadata changes from "
                f"{previous_version} to {receiver_version}"
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
        help="also validate changelog, compatibility, receiver, and artifacts",
    )
    parser.add_argument("--print-version", action="store_true")
    args = parser.parse_args()
    if args.print_version and (args.expected or args.release):
        parser.error("--print-version cannot be combined with an expected version or --release")
    return args


def main() -> int:
    args = parse_args()
    versions = platform_versions()
    expected = args.expected or next(iter(versions.values()))
    errors: list[str] = []
    try:
        parse_semver(expected)
    except ValueError as error:
        errors.append(f"expected platform version is invalid: {error}")
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
