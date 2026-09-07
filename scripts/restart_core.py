#!/usr/bin/env python3
"""Restart DumbHA Core only for an already-staged tagged FlexDisplay integration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import tempfile

from deploy_bridge import (
    DeploymentError,
    HOME_ASSISTANT_PORT,
    SEMVER,
    SHA40,
    SHA256,
    TARGET_HOST,
    TARGET_NAME,
    TARGET_SSH_PORT,
    TARGET_SSH_USER,
    http_json,
    load_secret,
    parse_json,
    require_http_200,
    require_mapping,
    restricted_environment,
    run_command,
    validate_health,
    validate_known_hosts,
)
from deploy_integration import validate_status


CONFIRMATION = "restart-dumbha-core-for-flexdisplay-integration"
RECONCILIATION_CONFIRMATION = "reconcile-dumbha-flexdisplay-core-restart"
REQUESTED_AT = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--failed-run-id", type=int)
    parser.add_argument("--expected-requested-at")
    parser.add_argument("--expected-staged-version")
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
    reconcile = arguments.confirmation == RECONCILIATION_CONFIRMATION
    if arguments.confirmation not in (CONFIRMATION, RECONCILIATION_CONFIRMATION):
        raise DeploymentError("Core restart confirmation phrase does not match")
    recovery_values = (
        arguments.failed_run_id,
        arguments.expected_requested_at,
        arguments.expected_staged_version,
    )
    if reconcile:
        if arguments.failed_run_id is None or arguments.failed_run_id < 1:
            raise DeploymentError("reconciliation requires a positive failed run ID")
        if not isinstance(
            arguments.expected_requested_at, str
        ) or not REQUESTED_AT.fullmatch(arguments.expected_requested_at):
            raise DeploymentError("reconciliation requires the exact requested timestamp")
        if not isinstance(
            arguments.expected_staged_version, str
        ) or not SEMVER.fullmatch(arguments.expected_staged_version):
            raise DeploymentError("reconciliation requires the staged integration version")
    elif any(value is not None for value in recovery_values):
        raise DeploymentError("restart mode refuses reconciliation arguments")
    if not arguments.receiver.is_file():
        raise DeploymentError("reviewed deployment receiver is missing from the tag")
    receiver_sha256 = hashlib.sha256(arguments.receiver.read_bytes()).hexdigest()
    if not SHA256.fullmatch(receiver_sha256):
        raise DeploymentError("deployment receiver checksum is invalid")

    private_key = load_secret("FLEXDISPLAY_DUMBHA_DEPLOY_KEY")
    known_hosts_value = load_secret("FLEXDISPLAY_DUMBHA_KNOWN_HOSTS")
    with tempfile.TemporaryDirectory(prefix="flexdisplay-core-restart-") as directory:
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
        integration_version = (
            arguments.expected_staged_version if reconcile else target_version
        )
        before = validate_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            bridge_version=target_version,
            integration_version=integration_version,
            receiver_sha256=receiver_sha256,
            staged_version=integration_version,
            restart_state="requested" if reconcile else "not_started",
        )
        validate_health(http_json("/healthz"), target_version)
        require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")
        print(
            json.dumps(
                {
                    "phase": "preflight",
                    "target": TARGET_NAME,
                    "tag": arguments.tag,
                    "source_commit": arguments.source_commit,
                    "home_assistant_core": before.get("core_version"),
                    "integration_version": integration_version,
                    "operation": "reconcile" if reconcile else "restart",
                },
                sort_keys=True,
            ),
            flush=True,
        )

        command = (
            f"reconcile-core {integration_version} {receiver_sha256} "
            f"{arguments.expected_requested_at} {arguments.failed_run_id}"
            if reconcile
            else f"restart-core {target_version} {receiver_sha256}"
        )
        result = require_mapping(
            parse_json(
                run_command(
                    [*ssh_prefix, command],
                    environment,
                    timeout=600,
                ),
                "remote Core restart",
            ),
            "remote Core restart",
        )
        if result.get("target_version") != integration_version:
            raise DeploymentError("Core restart record has the wrong integration version")
        if result.get("receiver_sha256") != receiver_sha256:
            raise DeploymentError("Core restart record has the wrong receiver checksum")
        if result.get("core_restart_performed") is not True:
            raise DeploymentError("remote command did not confirm the Core restart")
        if result.get("core_restart_state") != "verified":
            raise DeploymentError("remote command did not verify the Core restart")
        if reconcile:
            evidence = require_mapping(
                result.get("core_restart_reconciliation"),
                "Core restart reconciliation evidence",
            )
            if result.get("core_restart_reconciled") is not True:
                raise DeploymentError("remote command did not record reconciliation")
            if evidence.get("failed_workflow_run_id") != str(arguments.failed_run_id):
                raise DeploymentError("reconciliation recorded the wrong failed run")
        for field in ("rollback_directory", "rollback_backup", "home_assistant_core"):
            if not isinstance(result.get(field), str) or not result[field]:
                raise DeploymentError(f"Core restart record omitted {field}")

        validate_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            bridge_version=target_version,
            integration_version=integration_version,
            receiver_sha256=receiver_sha256,
            staged_version=integration_version,
            restart_state="verified",
        )
        validate_health(http_json("/healthz"), target_version)
        require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")
        print(
            json.dumps(
                {
                    "phase": "core_restart_reconciled" if reconcile else "core_restarted",
                    "target": TARGET_NAME,
                    "tag": arguments.tag,
                    "source_commit": arguments.source_commit,
                    "integration_version": integration_version,
                    "home_assistant_core": result["home_assistant_core"],
                    "rollback_directory": result["rollback_directory"],
                    "rollback_backup": result["rollback_backup"],
                    "bridge_health": "ok",
                    "core_restart_performed": True,
                    "core_restart_reconciled": reconcile,
                },
                sort_keys=True,
            )
        )
        del private_key, known_hosts_value
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeploymentError as error:
        raise SystemExit(f"Core restart blocked: {error}") from None
