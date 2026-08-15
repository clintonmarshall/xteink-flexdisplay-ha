#!/usr/bin/env python3
"""Verify a FlexDisplay Companion APK before it becomes a release asset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NS}}}"
EXPECTED_PACKAGE = "au.com.ldcs.flexdisplay.rook.companion"
EXPECTED_MIN_SDK = 24
EXPECTED_TARGET_SDK = 33
MAIN_ACTIVITY = "au.com.ldcs.flexdisplay.rook.MainActivity"
DOCK_SERVICE = "au.com.ldcs.flexdisplay.rook.CompanionDockTileService"
ALLOWED_PERMISSIONS = {
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.ACCESS_WIFI_STATE",
    "android.permission.CAMERA",
    "android.permission.INTERNET",
    "android.permission.RECORD_AUDIO",
}
PERMISSION_ELEMENTS = {
    "uses-permission",
    "uses-permission-sdk-23",
    "uses-permission-sdk-m",
}
ALLOWED_ROOT_ATTRIBUTES = {
    "package",
    f"{ANDROID}compileSdkVersion",
    f"{ANDROID}compileSdkVersionCodename",
    f"{ANDROID}versionCode",
    f"{ANDROID}versionName",
    "platformBuildVersionCode",
    "platformBuildVersionName",
}
ALLOWED_APPLICATION_ATTRIBUTES = {
    f"{ANDROID}allowBackup",
    f"{ANDROID}debuggable",
    f"{ANDROID}extractNativeLibs",
    f"{ANDROID}icon",
    f"{ANDROID}label",
    f"{ANDROID}networkSecurityConfig",
    f"{ANDROID}supportsRtl",
    f"{ANDROID}testOnly",
    f"{ANDROID}theme",
    f"{ANDROID}usesCleartextTraffic",
}
ALLOWED_ACTIVITY_ATTRIBUTES = {
    f"{ANDROID}configChanges",
    f"{ANDROID}excludeFromRecents",
    f"{ANDROID}exported",
    f"{ANDROID}launchMode",
    f"{ANDROID}name",
    f"{ANDROID}screenOrientation",
}
ALLOWED_SERVICE_ATTRIBUTES = {
    f"{ANDROID}exported",
    f"{ANDROID}icon",
    f"{ANDROID}label",
    f"{ANDROID}name",
    f"{ANDROID}permission",
}


class VerificationError(RuntimeError):
    """Raised when an APK violates the release contract."""


def _run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode:
        raise VerificationError(
            f"Command failed ({completed.returncode}): {command[0]}\n{completed.stdout}"
        )
    return completed.stdout


def _find_tool(name: str, explicit: str | None) -> str:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise VerificationError(f"{name} is not executable: {path}")
        return str(path)
    located = shutil.which(name)
    if located:
        return located
    sdk_root = os.getenv("ANDROID_SDK_ROOT") or os.getenv("ANDROID_HOME")
    if sdk_root:
        root = Path(sdk_root).expanduser()
        candidates = list(root.glob(f"cmdline-tools/*/bin/{name}"))
        candidates.extend(root.glob(f"build-tools/*/{name}"))
        for path in sorted(candidates, reverse=True):
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
    raise VerificationError(
        f"Unable to locate {name}; pass --{name} or configure ANDROID_SDK_ROOT"
    )


def _normalise_fingerprint(value: str) -> str:
    normalised = re.sub(r"[^0-9a-fA-F]", "", value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalised):
        raise VerificationError("Expected signer SHA-256 must contain exactly 64 hex digits")
    return normalised


def _manifest(apkanalyzer: str, apk: Path) -> ET.Element:
    rendered = _run([apkanalyzer, "manifest", "print", str(apk)])
    try:
        return ET.fromstring(rendered)
    except ET.ParseError as error:
        raise VerificationError(f"apkanalyzer returned invalid manifest XML: {error}") from error


def _intent_values(component: ET.Element, element: str) -> set[str]:
    return {
        item.get(f"{ANDROID}name", "")
        for intent in component.findall("intent-filter")
        for item in intent.findall(element)
    }


def _verify_single_intent_filter(
    component: ET.Element,
    *,
    actions: set[str],
    categories: set[str],
) -> None:
    filters = component.findall("intent-filter")
    if len(filters) != 1:
        raise VerificationError("Published components must have one intent filter")
    selected = filters[0]
    if selected.attrib:
        raise VerificationError("Intent filters may not contain extra attributes")
    unexpected_children = sorted(
        {item.tag for item in selected if item.tag not in {"action", "category"}}
    )
    if unexpected_children:
        raise VerificationError(
            f"Unexpected intent filter declarations: {unexpected_children}"
        )
    if any(set(item.attrib) != {f"{ANDROID}name"} for item in selected):
        raise VerificationError("Intent declarations may contain only android:name")
    if len(selected.findall("action")) != len(actions):
        raise VerificationError("Component action declarations are not exact")
    if len(selected.findall("category")) != len(categories):
        raise VerificationError("Component category declarations are not exact")
    if _intent_values(component, "action") != actions:
        raise VerificationError("Component has unexpected actions")
    if _intent_values(component, "category") != categories:
        raise VerificationError("Component has unexpected categories")


def _verify_manifest(
    root: ET.Element,
    *,
    expected_package: str,
    expected_version_name: str,
    expected_version_code: int,
    expected_min_sdk: int,
    expected_target_sdk: int,
) -> None:
    actual_package = root.get("package", "")
    actual_name = root.get(f"{ANDROID}versionName", "")
    actual_code = root.get(f"{ANDROID}versionCode", "")
    if actual_package != expected_package:
        raise VerificationError(
            f"Unexpected application ID: {actual_package!r} != {expected_package!r}"
        )
    if actual_name != expected_version_name:
        raise VerificationError(
            f"Unexpected versionName: {actual_name!r} != {expected_version_name!r}"
        )
    if actual_code != str(expected_version_code):
        raise VerificationError(
            f"Unexpected versionCode: {actual_code!r} != {expected_version_code}"
        )
    unexpected_root_attributes = sorted(set(root.attrib) - ALLOWED_ROOT_ATTRIBUTES)
    if unexpected_root_attributes:
        raise VerificationError(
            f"Unexpected manifest attributes: {unexpected_root_attributes}"
        )

    sdk_elements = root.findall("uses-sdk")
    if len(sdk_elements) != 1:
        raise VerificationError(f"Expected one uses-sdk element, found {len(sdk_elements)}")
    sdk = sdk_elements[0]
    expected_sdk_attributes = {
        f"{ANDROID}minSdkVersion",
        f"{ANDROID}targetSdkVersion",
    }
    if set(sdk.attrib) != expected_sdk_attributes:
        raise VerificationError("uses-sdk contains unexpected release constraints")
    if sdk.get(f"{ANDROID}minSdkVersion") != str(expected_min_sdk):
        raise VerificationError("Companion minSdkVersion does not match the release contract")
    if sdk.get(f"{ANDROID}targetSdkVersion") != str(expected_target_sdk):
        raise VerificationError("Companion targetSdkVersion does not match the release contract")

    permission_elements = [item for item in root if item.tag in PERMISSION_ELEMENTS]
    alternate_permission_elements = sorted(
        {item.tag for item in permission_elements if item.tag != "uses-permission"}
    )
    if alternate_permission_elements:
        raise VerificationError(
            "Alternate permission declarations are forbidden: "
            f"{alternate_permission_elements}"
        )
    permissions = {
        item.get(f"{ANDROID}name", "") for item in permission_elements
    }
    if any(set(item.attrib) != {f"{ANDROID}name"} for item in permission_elements):
        raise VerificationError("Permission declarations may contain only android:name")
    if permissions != ALLOWED_PERMISSIONS or len(permission_elements) != len(
        ALLOWED_PERMISSIONS
    ):
        missing = sorted(ALLOWED_PERMISSIONS - permissions)
        extra = sorted(permissions - ALLOWED_PERMISSIONS)
        raise VerificationError(
            f"Companion permissions must match the exact allowlist; missing={missing}, extra={extra}"
        )

    forbidden_root_declarations = {
        "instrumentation",
        "permission",
        "permission-group",
        "permission-tree",
    }
    found_root_declarations = sorted(
        {item.tag for item in root if item.tag in forbidden_root_declarations}
    )
    if found_root_declarations:
        raise VerificationError(
            f"Forbidden manifest declarations: {found_root_declarations}"
        )
    allowed_root_elements = {"application", "uses-permission", "uses-sdk"}
    unexpected_root_elements = sorted(
        {item.tag for item in root if item.tag not in allowed_root_elements}
    )
    if unexpected_root_elements:
        raise VerificationError(
            f"Unexpected top-level manifest elements: {unexpected_root_elements}"
        )

    applications = root.findall("application")
    if len(applications) != 1:
        raise VerificationError(
            f"Expected one application element, found {len(applications)}"
        )
    application = applications[0]
    unexpected_application_attributes = sorted(
        set(application.attrib) - ALLOWED_APPLICATION_ATTRIBUTES
    )
    if unexpected_application_attributes:
        raise VerificationError(
            "Unexpected application attributes: "
            f"{unexpected_application_attributes}"
        )
    if application.get(f"{ANDROID}debuggable", "false").lower() == "true":
        raise VerificationError("Published Companion APK must not be debuggable")
    if application.get(f"{ANDROID}testOnly", "false").lower() == "true":
        raise VerificationError("Published Companion APK must not be test-only")
    if application.get(f"{ANDROID}allowBackup", "true").lower() != "false":
        raise VerificationError("Published Companion APK must set android:allowBackup=false")

    activities = application.findall("activity")
    if len(activities) != 1:
        raise VerificationError(f"Expected one activity, found {len(activities)}")
    activity = activities[0]
    unexpected_activity_attributes = sorted(
        set(activity.attrib) - ALLOWED_ACTIVITY_ATTRIBUTES
    )
    if unexpected_activity_attributes:
        raise VerificationError(
            f"Unexpected activity attributes: {unexpected_activity_attributes}"
        )
    if activity.get(f"{ANDROID}name") != MAIN_ACTIVITY:
        raise VerificationError("Unexpected exported activity")
    if activity.get(f"{ANDROID}exported") != "true":
        raise VerificationError("Main activity must be exported for launcher use")
    _verify_single_intent_filter(
        activity,
        actions={"android.intent.action.MAIN"},
        categories={"android.intent.category.LAUNCHER"},
    )

    services = application.findall("service")
    if len(services) != 1:
        raise VerificationError(f"Expected one service, found {len(services)}")
    service = services[0]
    unexpected_service_attributes = sorted(
        set(service.attrib) - ALLOWED_SERVICE_ATTRIBUTES
    )
    if unexpected_service_attributes:
        raise VerificationError(
            f"Unexpected service attributes: {unexpected_service_attributes}"
        )
    if service.get(f"{ANDROID}name") != DOCK_SERVICE:
        raise VerificationError("Unexpected companion service")
    if service.get(f"{ANDROID}exported") != "true":
        raise VerificationError("Dock tile service must be exported to System UI")
    if service.get(f"{ANDROID}permission") != "android.permission.BIND_QUICK_SETTINGS_TILE":
        raise VerificationError("Dock tile service is missing its system binding permission")
    _verify_single_intent_filter(
        service,
        actions={"android.service.quicksettings.action.QS_TILE"},
        categories=set(),
    )

    forbidden_components = {
        "activity-alias",
        "provider",
        "receiver",
    }
    found_components = sorted(
        {item.tag for item in application if item.tag in forbidden_components}
    )
    if found_components:
        raise VerificationError(
            f"Companion release contains forbidden components: {found_components}"
        )
    allowed_application_elements = {"activity", "service"}
    unexpected_application_elements = sorted(
        {
            item.tag
            for item in application
            if item.tag not in allowed_application_elements
        }
    )
    if unexpected_application_elements:
        raise VerificationError(
            "Unexpected application manifest elements: "
            f"{unexpected_application_elements}"
        )


def _verify_signature(apksigner: str, apk: Path, expected: str) -> str:
    output = _run([apksigner, "verify", "--verbose", "--print-certs", str(apk)])
    if "Verifies" not in output:
        raise VerificationError("apksigner did not confirm verification")
    match = re.search(
        r"Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F:]+)", output
    )
    if not match:
        raise VerificationError("Unable to read signer SHA-256 from apksigner")
    if not re.search(r"Number of signers:\s*1\b", output):
        raise VerificationError("Published Companion APK must have exactly one signer")
    if "Android Debug" in output:
        raise VerificationError("Android Debug certificate is forbidden for publication")
    actual = _normalise_fingerprint(match.group(1))
    wanted = _normalise_fingerprint(expected)
    if actual != wanted:
        raise VerificationError(f"Unexpected signer SHA-256: {actual} != {wanted}")
    return actual


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True)
    parser.add_argument("--apkanalyzer")
    parser.add_argument("--apksigner")
    parser.add_argument("--expected-package", default=EXPECTED_PACKAGE)
    parser.add_argument("--expected-version-name", required=True)
    parser.add_argument("--expected-version-code", required=True, type=int)
    parser.add_argument("--expected-min-sdk", default=EXPECTED_MIN_SDK, type=int)
    parser.add_argument("--expected-target-sdk", default=EXPECTED_TARGET_SDK, type=int)
    parser.add_argument("--expected-signer-sha256")
    parser.add_argument("--skip-signature", action="store_true")
    parser.add_argument("--metadata-out")
    parser.add_argument("--source-tag", default="")
    parser.add_argument("--source-commit", default="")
    args = parser.parse_args()

    try:
        candidate = Path(args.apk).expanduser()
        if candidate.is_symlink():
            raise VerificationError("APK must be a regular, non-symlink file")
        apk = candidate.resolve(strict=True)
        if not apk.is_file():
            raise VerificationError("APK must be a regular, non-symlink file")
        apkanalyzer = _find_tool("apkanalyzer", args.apkanalyzer)
        root = _manifest(apkanalyzer, apk)
        _verify_manifest(
            root,
            expected_package=args.expected_package,
            expected_version_name=args.expected_version_name,
            expected_version_code=args.expected_version_code,
            expected_min_sdk=args.expected_min_sdk,
            expected_target_sdk=args.expected_target_sdk,
        )

        signer = ""
        if args.skip_signature:
            if args.expected_signer_sha256:
                raise VerificationError(
                    "Do not combine --skip-signature with --expected-signer-sha256"
                )
        else:
            if not args.expected_signer_sha256:
                raise VerificationError(
                    "--expected-signer-sha256 is required unless --skip-signature is used"
                )
            apksigner = _find_tool("apksigner", args.apksigner)
            signer = _verify_signature(
                apksigner, apk, args.expected_signer_sha256
            )

        apk_sha256 = _sha256(apk)
        if args.metadata_out:
            metadata = {
                "schema_version": 1,
                "artifact": apk.name,
                "application_id": args.expected_package,
                "version_name": args.expected_version_name,
                "version_code": args.expected_version_code,
                "source_tag": args.source_tag,
                "source_commit": args.source_commit,
                "apk_sha256": apk_sha256,
                "signer_sha256": signer,
            }
            output = Path(args.metadata_out)
            output.write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(
            json.dumps(
                {
                    "apk": str(apk),
                    "sha256": apk_sha256,
                    "signer_sha256": signer,
                    "verified": True,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, VerificationError) as error:
        print(f"Android release verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
