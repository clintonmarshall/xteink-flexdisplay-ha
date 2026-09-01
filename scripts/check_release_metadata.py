#!/usr/bin/env python3
"""Validate FlexDisplay platform metadata, with stricter release gates."""

from __future__ import annotations

import argparse
import ast
import datetime
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
RELEASE_MANIFEST = ROOT / "release-manifest.json"
RELEASE_STATUS = ROOT / "docs/RELEASE_STATUS.md"
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


def module_dict(path: Path, name: str) -> dict[str, object]:
    """Return a literal top-level dictionary without importing runtime code."""

    module = ast.parse(path.read_text(), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = ast.literal_eval(node.value)
                    if isinstance(value, dict):
                        return value
    raise ValueError(f"{name} not found in {path.relative_to(ROOT)}")


def release_manifest(path: Path = RELEASE_MANIFEST) -> dict[str, object]:
    """Load the canonical coordinated release manifest."""

    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {path.relative_to(ROOT)}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("release-manifest.json must contain a JSON object")
    return value


def _markdown(value: object) -> str:
    return str(value).replace("|", "\\|")


def render_release_status(manifest: dict[str, object]) -> str:
    """Render the checked-in release snapshot for people from the manifest."""

    platform = manifest["platform"]
    distribution = manifest["distribution"]
    artifacts = manifest["packaged_artifacts"]
    assert isinstance(platform, dict)
    assert isinstance(distribution, dict)
    assert isinstance(artifacts, list)

    state_labels = {
        "forgejo_release": "Authoritative Forgejo release",
        "github_compatibility_release": "GitHub compatibility release",
        "home_assistant_deployment": "Home Assistant deployment",
        "android_companion": "Android Companion APK",
        "device_firmware_rollout": "Device firmware rollout",
        "physical_device_behavior": "Physical device behaviour",
    }
    lines = [
        "# Release status",
        "",
        "<!-- Generated by scripts/render_release_status.py; edit release-manifest.json. -->",
        "",
        f"Recorded `{manifest['recorded_at']}` from the checked-in coordinated release",
        "manifest. This is repository evidence, not a live deployment probe. Reverify",
        "Forgejo, GitHub, Home Assistant, devices, and physical behaviour before an",
        "operational claim or change.",
        "",
        "## Coordinated platform release",
        "",
        "| Field | Recorded value |",
        "| --- | --- |",
        f"| Platform | `{_markdown(platform['version'])}` |",
        f"| Tag | `{_markdown(platform['release_tag'])}` |",
        f"| Commit | `{_markdown(platform['release_commit'])}` |",
        f"| Released | `{_markdown(platform['released_at'])}` |",
        f"| Classification | `{_markdown(platform['classification'])}` |",
        "",
        "## Distribution boundaries",
        "",
        "| Boundary | State | Evidence |",
        "| --- | --- | --- |",
    ]
    for key, label in state_labels.items():
        item = distribution[key]
        assert isinstance(item, dict)
        evidence = item.get("evidence") or "No current evidence recorded"
        lines.append(
            f"| {label} | `{_markdown(item['state'])}` | {_markdown(evidence)} |"
        )

    lines.extend(
        [
            "",
            "A published source release is not proof of a Home Assistant deployment,",
            "Android installation, firmware rollout, device check-in, or physical render.",
            "",
            "## Packaged device firmware",
            "",
            "| Artifact | Version | Bytes | SHA-256 | Provenance |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        provenance = artifact["provenance"]
        assert isinstance(provenance, dict)
        lines.append(
            f"| {_markdown(artifact['label'])} | `{_markdown(artifact['version'])}` | "
            f"{artifact['size']} | `{_markdown(artifact['sha256'])}` | "
            f"`{_markdown(provenance['status'])}` |"
        )

    lines.extend(["", "### Known provenance gaps", ""])
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        provenance = artifact["provenance"]
        assert isinstance(provenance, dict)
        gaps = provenance.get("unverified_fields", [])
        if gaps:
            lines.append(
                f"- **{artifact['label']}:** " + "; ".join(str(gap) for gap in gaps) + "."
            )
        else:
            lines.append(f"- **{artifact['label']}:** fully verified in this manifest.")
    lines.extend(
        [
            "",
            "A `partial` record preserves the exact packaged bytes but is not sufficient",
            "provenance for a new firmware-bearing release. Changed firmware must replace",
            "the partial record with complete source, recovery, checksum, and USB-canary",
            "evidence before publication.",
            "",
        ]
    )
    return "\n".join(lines)


def manifest_errors(expected: str) -> list[str]:
    """Validate the canonical manifest and every checked-in mirror it owns."""

    try:
        manifest = release_manifest()
    except ValueError as error:
        return [str(error)]

    errors: list[str] = []
    if manifest.get("$schema") != "docs/release-manifest.schema.json":
        errors.append("release manifest $schema must reference docs/release-manifest.schema.json")
    if manifest.get("schema_version") != 1:
        errors.append("release manifest schema_version must be 1")
    try:
        datetime.date.fromisoformat(str(manifest.get("recorded_at", "")))
    except ValueError:
        errors.append("release manifest recorded_at must be an ISO date")
    platform = manifest.get("platform")
    if not isinstance(platform, dict):
        return errors + ["release manifest platform must be an object"]
    if platform.get("version") != expected:
        errors.append(
            f"release manifest platform version is {platform.get('version')!r}, "
            f"expected {expected!r}"
        )
    if platform.get("release_tag") != f"v{expected}":
        errors.append(f"release manifest tag must be v{expected}")
    if not re.fullmatch(r"[0-9a-f]{40}", str(platform.get("release_commit", ""))):
        errors.append("release manifest release_commit must be a full lowercase Git SHA")
    released_at = platform.get("released_at")
    if released_at is not None:
        try:
            datetime.datetime.fromisoformat(str(released_at))
        except ValueError:
            errors.append("release manifest released_at must be an ISO date-time or null")
    if platform.get("classification") not in {"software-only", "firmware-bearing"}:
        errors.append("release manifest classification is invalid")

    android = manifest.get("android")
    if not isinstance(android, dict):
        errors.append("release manifest android must be an object")
    else:
        receiver_version, receiver_code = receiver_metadata()
        expected_companion = f"{receiver_version}-companion"
        if android.get("source_version") != receiver_version:
            errors.append("release manifest Android source_version does not match Gradle")
        if android.get("version_code") != receiver_code:
            errors.append("release manifest Android version_code does not match Gradle")
        if android.get("companion_version") != expected_companion:
            errors.append(
                f"release manifest companion_version must be {expected_companion!r}"
            )

    distribution = manifest.get("distribution")
    required_boundaries = {
        "forgejo_release",
        "github_compatibility_release",
        "home_assistant_deployment",
        "android_companion",
        "device_firmware_rollout",
        "physical_device_behavior",
    }
    allowed_states = {"published", "unpublished", "deployed", "not-deployed", "unverified"}
    if not isinstance(distribution, dict):
        errors.append("release manifest distribution must be an object")
    else:
        missing = sorted(required_boundaries - distribution.keys())
        if missing:
            errors.append(f"release manifest distribution is missing {', '.join(missing)}")
        for boundary, item in distribution.items():
            if not isinstance(item, dict) or item.get("state") not in allowed_states:
                errors.append(f"release manifest distribution {boundary!r} has invalid state")
                continue
            if item.get("state") != "unverified" and not item.get("evidence"):
                errors.append(
                    f"release manifest distribution {boundary!r} requires evidence"
                )

    artifacts = manifest.get("packaged_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return errors + ["release manifest packaged_artifacts must be a non-empty list"]
    runner_defaults = {
        "firmware": module_dict(
            ROOT / "flexdisplay_bridge/app_runner.py", "DEFAULT_FIRMWARE"
        ),
        "note4_firmware": module_dict(
            ROOT / "flexdisplay_bridge/app_runner.py", "DEFAULT_NOTE4_FIRMWARE"
        ),
    }
    config = ROOT / "flexdisplay_bridge/config.yaml"
    seen_ids: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("release manifest artifact must be an object")
            continue
        artifact_id = str(artifact.get("id", ""))
        if not artifact_id or artifact_id in seen_ids:
            errors.append(f"release manifest artifact id {artifact_id!r} is empty or duplicate")
        seen_ids.add(artifact_id)
        relative_path = Path(str(artifact.get("path", "")))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            errors.append(f"release manifest artifact {artifact_id!r} has unsafe path")
            continue
        path = ROOT / relative_path
        if not path.is_file():
            errors.append(f"release manifest artifact {relative_path} does not exist")
            continue
        payload = path.read_bytes()
        actual_sha = hashlib.sha256(payload).hexdigest()
        if artifact.get("size") != len(payload):
            errors.append(f"release manifest {artifact_id} size does not match packaged bytes")
        if artifact.get("sha256") != actual_sha:
            errors.append(f"release manifest {artifact_id} SHA-256 does not match packaged bytes")
        prefix = str(artifact.get("config_prefix", ""))
        if prefix not in runner_defaults:
            errors.append(f"release manifest artifact {artifact_id} has unknown config_prefix")
            continue
        mirrors = {
            "version": yaml_scalar(config, f"{prefix}_version"),
            "size": int(yaml_scalar(config, f"{prefix}_size")),
            "sha256": yaml_scalar(config, f"{prefix}_sha256"),
        }
        runtime = runner_defaults[prefix]
        runtime_mirrors = {
            "version": runtime[f"{prefix}_version"],
            "size": runtime[f"{prefix}_size"],
            "sha256": runtime[f"{prefix}_sha256"],
        }
        for field in ("version", "size", "sha256"):
            if artifact.get(field) != mirrors[field]:
                errors.append(
                    f"release manifest {artifact_id} {field} does not match config.yaml"
                )
            if artifact.get(field) != runtime_mirrors[field]:
                errors.append(
                    f"release manifest {artifact_id} {field} does not match app_runner.py"
                )
        provenance = artifact.get("provenance")
        if not isinstance(provenance, dict):
            errors.append(f"release manifest {artifact_id} provenance must be an object")
            continue
        status = provenance.get("status")
        gaps = provenance.get("unverified_fields")
        if not provenance.get("source_repository"):
            errors.append(
                f"release manifest {artifact_id} provenance requires source_repository"
            )
        source_revision = provenance.get("source_revision")
        if source_revision is not None and not re.fullmatch(
            r"[0-9a-f]{40}", str(source_revision)
        ):
            errors.append(
                f"release manifest {artifact_id} source_revision must be a full Git SHA"
            )
        recovery_sha = provenance.get("recovery_artifact_sha256")
        if recovery_sha is not None and not re.fullmatch(
            r"[0-9a-f]{64}", str(recovery_sha)
        ):
            errors.append(
                f"release manifest {artifact_id} recovery SHA-256 is invalid"
            )
        if status == "verified":
            required = (
                "source_revision",
                "recovery_artifact_reference",
                "recovery_artifact_sha256",
                "usb_canary_evidence_reference",
            )
            if gaps or any(not provenance.get(field) for field in required):
                errors.append(
                    f"release manifest {artifact_id} verified provenance is incomplete"
                )
        elif status == "partial":
            if not isinstance(gaps, list) or not gaps:
                errors.append(
                    f"release manifest {artifact_id} partial provenance must list gaps"
                )
        else:
            errors.append(f"release manifest {artifact_id} provenance status is invalid")

    expected_ids = {"x3_x4_firmware", "note4_firmware"}
    if seen_ids != expected_ids:
        errors.append(
            "release manifest packaged artifact ids must be exactly "
            "x3_x4_firmware and note4_firmware"
        )

    if not errors:
        rendered = render_release_status(manifest)
        if not RELEASE_STATUS.exists() or RELEASE_STATUS.read_text() != rendered:
            errors.append(
                "docs/RELEASE_STATUS.md is stale; run "
                "python3 scripts/render_release_status.py"
            )
    return errors


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
    manifest = release_manifest()
    artifacts = {
        artifact["id"]: artifact
        for artifact in manifest["packaged_artifacts"]
        if isinstance(artifact, dict)
    }
    expected_rows = {
        "FlexDisplay platform": expected,
        "Echo Spot receiver": receiver_version,
        "Echo Show 5 receiver": receiver_version,
        "X3/X4 packaged firmware": artifacts["x3_x4_firmware"]["version"],
        "Note 4 packaged firmware": artifacts["note4_firmware"]["version"],
    }
    return [
        f"{component} compatibility version is {compatibility.get(component)!r}, "
        f"expected {version!r}"
        for component, version in expected_rows.items()
        if compatibility.get(component) != version
    ]


def firmware_classification_errors(
    manifest: dict[str, object], previous_tag: str
) -> list[str]:
    """Require complete provenance precisely when packaged firmware changed."""

    platform = manifest["platform"]
    artifacts = manifest["packaged_artifacts"]
    assert isinstance(platform, dict)
    assert isinstance(artifacts, list)
    changed = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and git_path_changed(previous_tag, str(artifact["path"]))
    ]
    classification = platform.get("classification")
    errors: list[str] = []
    if changed and classification != "firmware-bearing":
        errors.append(
            "packaged firmware changed but release manifest classification is not "
            "'firmware-bearing'"
        )
    if not changed and classification != "software-only":
        errors.append(
            "packaged firmware is unchanged but release manifest classification is not "
            "'software-only'"
        )
    for artifact in changed:
        provenance = artifact.get("provenance", {})
        if not isinstance(provenance, dict) or provenance.get("status") != "verified":
            errors.append(
                f"changed packaged artifact {artifact.get('id')!r} requires verified "
                "source, recovery, checksum, and USB-canary provenance"
            )
    return errors


def release_errors(expected: str) -> list[str]:
    errors: list[str] = []

    changelog = (ROOT / "flexdisplay_bridge/CHANGELOG.md").read_text()
    if not re.search(rf"^##\s+{re.escape(expected)}\s*$", changelog, re.MULTILINE):
        errors.append(f"flexdisplay_bridge/CHANGELOG.md has no '## {expected}' heading")

    receiver_version, receiver_code = receiver_metadata()
    errors.extend(compatibility_errors(expected, receiver_version))
    if receiver_code <= 0:
        errors.append("Android versionCode must be positive")
    previous_tag: str | None = None
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

    if previous_tag:
        manifest = release_manifest()
        errors.extend(firmware_classification_errors(manifest, previous_tag))
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
    errors.extend(manifest_errors(expected))
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
