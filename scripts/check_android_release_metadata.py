#!/usr/bin/env python3
"""Check the committed Android Companion release identity and version contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
GRADLE = ROOT / "rook_receiver/app/build.gradle"
CONTRACT = ROOT / "rook_receiver/release/companion-release.json"
CLIENT = (
    ROOT
    / "rook_receiver/app/src/main/java/au/com/ldcs/flexdisplay/rook/FlexDisplayClient.java"
)
WRAPPER = ROOT / "rook_receiver/gradle/wrapper/gradle-wrapper.properties"
SIGNER = ROOT / "rook_receiver/release/companion-release-cert.sha256"
EXPECTED_APPLICATION_ID = "au.com.ldcs.flexdisplay.rook.companion"


def _one(pattern: str, source: str, label: str) -> str:
    matches = re.findall(pattern, source, flags=re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {label}, found {len(matches)}")
    return str(matches[0])


def check(*, require_signer: bool = False) -> dict[str, object]:
    gradle = GRADLE.read_text(encoding="utf-8")
    base_id = _one(r'^\s*applicationId\s+"([^"]+)"', gradle, "applicationId")
    id_suffix = _one(
        r'^\s*applicationIdSuffix\s+"([^"]+)"', gradle, "applicationIdSuffix"
    )
    base_name = _one(r'^\s*versionName\s+"([^"]+)"', gradle, "versionName")
    name_suffix = _one(
        r'^\s*versionNameSuffix\s+"([^"]+)"', gradle, "versionNameSuffix"
    )
    version_code = int(_one(r"^\s*versionCode\s+(\d+)", gradle, "versionCode"))
    application_id = base_id + id_suffix
    version_name = base_name + name_suffix

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    expected = {
        "application_id": application_id,
        "version_name": version_name,
        "version_code": version_code,
        "artifact_basename": f"flexdisplay-companion-{base_name}-vc{version_code}",
    }
    if application_id != EXPECTED_APPLICATION_ID:
        raise ValueError(
            f"Companion application ID changed: {application_id} != {EXPECTED_APPLICATION_ID}"
        )
    for key, value in expected.items():
        if contract.get(key) != value:
            raise ValueError(
                f"Android release contract mismatch for {key}: "
                f"{contract.get(key)!r} != {value!r}"
            )
    if set(contract) != set(expected):
        raise ValueError(
            f"Unexpected Android release contract fields: {sorted(set(contract) - set(expected))}"
        )

    client = CLIENT.read_text(encoding="utf-8")
    if '"android-" + BuildConfig.VERSION_NAME' not in client:
        raise ValueError("Receiver telemetry must derive its version from BuildConfig.VERSION_NAME")
    if re.search(r'FIRMWARE_VERSION\s*=\s*"android-\d', client):
        raise ValueError("Receiver telemetry contains a hard-coded Android version")

    wrapper = WRAPPER.read_text(encoding="utf-8")
    checksum = _one(
        r"^distributionSha256Sum=([0-9a-f]{64})$",
        wrapper,
        "Gradle distribution checksum",
    )
    if not checksum:
        raise ValueError("Gradle wrapper checksum is empty")
    signer = ""
    if SIGNER.exists():
        signer = SIGNER.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", signer):
            raise ValueError(
                "Committed Companion signer fingerprint must be 64 lowercase hex digits"
            )
    elif require_signer:
        raise ValueError(
            "Companion signer fingerprint is not provisioned; add the independently "
            "verified production certificate SHA-256 in a reviewed release commit"
        )
    expected["signer_sha256"] = signer
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument("--require-signer", action="store_true")
    args = parser.parse_args()
    try:
        metadata = check(require_signer=args.require_signer)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Android release metadata check failed: {error}", file=sys.stderr)
        return 1
    if args.print_version:
        print(metadata["version_name"])
    else:
        print(
            "Android release metadata is consistent: "
            f"{metadata['application_id']} {metadata['version_name']} "
            f"(version code {metadata['version_code']}; "
            f"signer {'provisioned' if metadata['signer_sha256'] else 'not yet provisioned'})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
