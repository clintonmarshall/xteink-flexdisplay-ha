#!/usr/bin/env python3
"""Run the fixed-target, tag-scoped FlexDisplay Bridge deployment contract."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from typing import Any


TARGET_NAME = "DumbHA"
TARGET_HOST = "10.200.40.4"
TARGET_SSH_PORT = 22
TARGET_SSH_USER = "root"
BRIDGE_PORT = 8099
HOME_ASSISTANT_PORT = 8123
EXPECTED_HOST_FINGERPRINT = "SHA256:3NP9WQelqKqZEoyXfyRwGUlAW0wMj9MFW4hrFWiBVTw"
EXPECTED_APP_SLUG = "629898c9_flexdisplay_bridge"
EXPECTED_APP_REPOSITORY = "629898c9"
EXPECTED_APP_SOURCE_URL = "https://github.com/clintonmarshall/xteink-flexdisplay-ha"
CONFIRMATION = "deploy-flexdisplay-bridge-to-dumbha"
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DeploymentError(RuntimeError):
    """A fail-closed deployment contract violation."""


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} is not a JSON object")
    return value


def parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DeploymentError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise DeploymentError(f"{label} has no timezone")
    return parsed


def validate_remote_status(
    payload: Any,
    *,
    expected_version: str,
    receiver_sha256: str,
) -> dict[str, Any]:
    status = require_mapping(payload, "remote status")
    if status.get("receiver_sha256") != receiver_sha256:
        raise DeploymentError("installed deployment receiver is not the tagged copy")
    core_version = status.get("core_version")
    if not isinstance(core_version, str) or not core_version:
        raise DeploymentError("Home Assistant Core version was not recorded")
    app = require_mapping(status.get("app"), "remote App status")
    expected = {
        "slug": EXPECTED_APP_SLUG,
        "repository": EXPECTED_APP_REPOSITORY,
        "source_url": EXPECTED_APP_SOURCE_URL,
        "version": expected_version,
        "state": "started",
        "auto_update": False,
    }
    for key, value in expected.items():
        if app.get(key) != value:
            raise DeploymentError(f"unexpected Bridge App {key}")
    return status


def validate_health(payload: Any, expected_version: str) -> dict[str, Any]:
    health = require_mapping(payload, "Bridge health")
    if health.get("status") != "ok":
        raise DeploymentError("Bridge health status is not ok")
    if health.get("version") != expected_version:
        raise DeploymentError("Bridge health version does not match the requested tag")
    if health.get("home_assistant_configured") is not True:
        raise DeploymentError("Bridge Home Assistant access is not configured")
    if health.get("mqtt_enabled") is True and health.get("mqtt_connected") is not True:
        raise DeploymentError("Bridge MQTT is enabled but disconnected")
    flexhub = require_mapping(health.get("flexhub"), "FlexHub health")
    if flexhub.get("configured") is True and flexhub.get("connected") is not True:
        raise DeploymentError("FlexHub is configured but disconnected")
    return health


def checked_in_devices(payload: Any) -> dict[str, datetime]:
    fleet = require_mapping(payload, "Bridge device response")
    devices = fleet.get("devices")
    if not isinstance(devices, list):
        raise DeploymentError("Bridge device response has no device list")
    result: dict[str, datetime] = {}
    for item in devices:
        if not isinstance(item, dict):
            continue
        device_id = item.get("device_id")
        last_seen = item.get("last_seen")
        if isinstance(device_id, str) and device_id and isinstance(last_seen, str) and last_seen:
            if device_id in result:
                raise DeploymentError("Bridge device response contains a duplicate identity")
            result[device_id] = parse_timestamp(last_seen, f"{device_id} last_seen")
    if not result:
        raise DeploymentError("no existing device check-ins were available to preserve")
    return result


def compare_device_checkins(
    before: dict[str, datetime], after: dict[str, datetime]
) -> tuple[int, int]:
    missing = sorted(set(before).difference(after))
    if missing:
        raise DeploymentError("existing device check-in records disappeared after deployment")
    advanced = 0
    for device_id, before_seen in before.items():
        after_seen = after[device_id]
        if after_seen < before_seen:
            raise DeploymentError("an existing device check-in timestamp regressed")
        if after_seen > before_seen:
            advanced += 1
    return len(before), advanced


def load_secret(name: str) -> str:
    value = os.environ.pop(name, "")
    if not value:
        raise DeploymentError(f"required deployment secret {name} is unavailable")
    return value


def restricted_environment(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }


def run_command(
    args: list[str], environment: dict[str, str], *, timeout: int = 240
) -> str:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        detail = error.stderr if isinstance(error.stderr, str) else ""
        if len(detail) > 800:
            detail = detail[-800:]
        raise DeploymentError(
            "restricted deployment command timed out; remote state must be reconciled"
            + (f": {detail.strip()}" if detail.strip() else "")
        ) from None
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise DeploymentError(
            f"restricted deployment command failed"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout


def validate_known_hosts(
    known_hosts: Path, environment: dict[str, str]
) -> None:
    found = subprocess.run(
        ["ssh-keygen", "-F", TARGET_HOST, "-f", str(known_hosts)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if found.returncode != 0:
        raise DeploymentError("known_hosts does not bind the exact DumbHA address")
    fingerprints = run_command(
        ["ssh-keygen", "-lf", str(known_hosts), "-E", "sha256"], environment
    )
    observed = [
        fields[1]
        for line in fingerprints.splitlines()
        if len(fields := line.split()) >= 2
    ]
    if observed != [EXPECTED_HOST_FINGERPRINT]:
        raise DeploymentError("DumbHA host key fingerprint is not the pinned inventory value")


def http_get(path: str, port: int, bridge_key: str | None = None) -> tuple[int, bytes]:
    headers = {"Accept": "application/json"}
    if bridge_key is not None:
        headers["X-FlexDisplay-Bridge-Key"] = bridge_key
    connection = http.client.HTTPConnection(TARGET_HOST, port, timeout=15)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read(12 * 1024 * 1024)
        if response.read(1):
            raise DeploymentError("HTTP response exceeded the deployment evidence limit")
        return response.status, body
    finally:
        connection.close()


def http_json(path: str, bridge_key: str | None = None) -> Any:
    status, body = http_get(path, BRIDGE_PORT, bridge_key)
    if status != 200:
        raise DeploymentError(f"Bridge endpoint {path} returned HTTP {status}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise DeploymentError(f"Bridge endpoint {path} returned invalid JSON") from error


def require_http_200(path: str, port: int, label: str) -> None:
    status, _ = http_get(path, port)
    if status != 200:
        raise DeploymentError(f"{label} returned HTTP {status}")


def parse_json(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise DeploymentError(f"{label} did not return valid JSON") from error


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-current-version", required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument(
        "--receiver",
        type=Path,
        default=Path(__file__).with_name("flexdisplay_bridge_deploy_receiver.sh"),
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not arguments.tag.startswith("v") or not SEMVER.fullmatch(arguments.tag[1:]):
        raise DeploymentError("tag must be a stable vX.Y.Z release")
    target_version = arguments.tag[1:]
    if not SHA40.fullmatch(arguments.source_commit):
        raise DeploymentError("source commit must be a full lowercase SHA")
    if not SEMVER.fullmatch(arguments.expected_current_version):
        raise DeploymentError("expected current version must be stable SemVer")
    if arguments.expected_current_version == target_version:
        raise DeploymentError("target version must differ from the installed version")
    if arguments.confirmation != CONFIRMATION:
        raise DeploymentError("deployment confirmation phrase does not match")
    if not arguments.receiver.is_file():
        raise DeploymentError("reviewed deployment receiver is missing from the tag")

    receiver_sha256 = hashlib.sha256(arguments.receiver.read_bytes()).hexdigest()
    if not SHA256.fullmatch(receiver_sha256):
        raise DeploymentError("deployment receiver checksum is invalid")

    private_key = load_secret("FLEXDISPLAY_DUMBHA_DEPLOY_KEY")
    known_hosts_value = load_secret("FLEXDISPLAY_DUMBHA_KNOWN_HOSTS")
    bridge_key = load_secret("FLEXDISPLAY_DUMBHA_BRIDGE_API_KEY")
    if "\n" in bridge_key or "\r" in bridge_key:
        raise DeploymentError("Bridge API key contains a forbidden newline")

    with tempfile.TemporaryDirectory(prefix="flexdisplay-deploy-") as directory:
        temporary = Path(directory)
        private_key_path = temporary / "deploy_key"
        known_hosts_path = temporary / "known_hosts"
        private_key_path.write_text(private_key.rstrip("\n") + "\n", encoding="utf-8")
        known_hosts_path.write_text(
            known_hosts_value.rstrip("\n") + "\n", encoding="utf-8"
        )
        private_key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        known_hosts_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        environment = restricted_environment(temporary)
        validate_known_hosts(known_hosts_path, environment)

        ssh_prefix = [
            "ssh",
            "-F",
            "/dev/null",
            "-i",
            str(private_key_path),
            "-p",
            str(TARGET_SSH_PORT),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "PermitLocalCommand=no",
            f"{TARGET_SSH_USER}@{TARGET_HOST}",
        ]

        pre_status = validate_remote_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            expected_version=arguments.expected_current_version,
            receiver_sha256=receiver_sha256,
        )
        pre_health = validate_health(
            http_json("/healthz"), arguments.expected_current_version
        )
        require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")
        require_http_200("/studio/", BRIDGE_PORT, "FlexDisplay Studio")
        before_devices = checked_in_devices(
            http_json("/api/v1/devices?compact=true", bridge_key)
        )
        print(
            json.dumps(
                {
                    "phase": "preflight",
                    "target": TARGET_NAME,
                    "tag": arguments.tag,
                    "source_commit": arguments.source_commit,
                    "home_assistant_core": pre_status["core_version"],
                    "installed_version": arguments.expected_current_version,
                    "auto_update": False,
                    "existing_checkins": len(before_devices),
                },
                sort_keys=True,
            ),
            flush=True,
        )

        deploy_command = (
            f"deploy {target_version} {arguments.expected_current_version} "
            f"{receiver_sha256}"
        )
        deploy_result = require_mapping(
            parse_json(
                run_command([*ssh_prefix, deploy_command], environment, timeout=600),
                "remote deployment",
            ),
            "remote deployment",
        )
        if deploy_result.get("installed_version") != target_version:
            raise DeploymentError("remote deployment did not install the tag version")
        if deploy_result.get("previous_version") != arguments.expected_current_version:
            raise DeploymentError("remote deployment did not record the expected prior version")
        if deploy_result.get("backup_verified") is not True:
            raise DeploymentError("remote deployment did not verify a rollback backup")
        backup_slug = deploy_result.get("rollback_backup")
        if not isinstance(backup_slug, str) or not backup_slug:
            raise DeploymentError("remote deployment did not return a rollback backup identifier")
        if deploy_result.get("auto_update") is not False:
            raise DeploymentError("remote deployment changed or failed to record auto-update state")
        if deploy_result.get("core_restart_performed") is not False:
            raise DeploymentError("remote deployment reported an unexpected Core restart")
        print(
            json.dumps(
                {
                    "phase": "bridge_updated",
                    "target": TARGET_NAME,
                    "previous_version": arguments.expected_current_version,
                    "installed_version": target_version,
                    "auto_update": False,
                    "rollback_backup": backup_slug,
                    "core_restart_performed": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )

        deadline = time.monotonic() + 180
        while True:
            try:
                post_health = validate_health(http_json("/healthz"), target_version)
                break
            except (DeploymentError, OSError):
                if time.monotonic() >= deadline:
                    raise DeploymentError("Bridge did not become healthy at the tag version")
                time.sleep(5)

        post_status = validate_remote_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            expected_version=target_version,
            receiver_sha256=receiver_sha256,
        )
        require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")
        require_http_200("/studio/", BRIDGE_PORT, "FlexDisplay Studio")
        after_devices = checked_in_devices(
            http_json("/api/v1/devices?compact=true", bridge_key)
        )
        preserved, advanced = compare_device_checkins(before_devices, after_devices)

        summary = {
            "target": TARGET_NAME,
            "tag": arguments.tag,
            "source_commit": arguments.source_commit,
            "home_assistant_core": post_status["core_version"],
            "previous_version": arguments.expected_current_version,
            "installed_version": target_version,
            "auto_update": False,
            "rollback_backup": backup_slug,
            "bridge_health": post_health["status"],
            "mqtt_connected": post_health.get("mqtt_connected"),
            "flexhub_connected": post_health["flexhub"].get("connected"),
            "existing_checkins_preserved": preserved,
            "checkins_advanced_during_deployment": advanced,
            "core_restart_performed": False,
        }
        print(json.dumps(summary, sort_keys=True))
        del private_key, known_hosts_value, bridge_key, pre_status, pre_health
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeploymentError as error:
        raise SystemExit(f"Bridge deployment blocked: {error}") from None
