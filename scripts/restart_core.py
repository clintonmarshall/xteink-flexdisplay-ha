#!/usr/bin/env python3
"""Restart DumbHA Core only for an already-staged tagged FlexDisplay integration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
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
    if arguments.confirmation != CONFIRMATION:
        raise DeploymentError("Core restart confirmation phrase does not match")
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
        before = validate_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            bridge_version=target_version,
            integration_version=target_version,
            receiver_sha256=receiver_sha256,
            staged_version=target_version,
            restart_state="not_started",
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
                    "integration_version": target_version,
                },
                sort_keys=True,
            ),
            flush=True,
        )

        result = require_mapping(
            parse_json(
                run_command(
                    [*ssh_prefix, f"restart-core {target_version} {receiver_sha256}"],
                    environment,
                    timeout=600,
                ),
                "remote Core restart",
            ),
            "remote Core restart",
        )
        if result.get("target_version") != target_version:
            raise DeploymentError("Core restart record has the wrong integration version")
        if result.get("receiver_sha256") != receiver_sha256:
            raise DeploymentError("Core restart record has the wrong receiver checksum")
        if result.get("core_restart_performed") is not True:
            raise DeploymentError("remote command did not confirm the Core restart")
        if result.get("core_restart_state") != "verified":
            raise DeploymentError("remote command did not verify the Core restart")
        for field in ("rollback_directory", "rollback_backup", "home_assistant_core"):
            if not isinstance(result.get(field), str) or not result[field]:
                raise DeploymentError(f"Core restart record omitted {field}")

        validate_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            bridge_version=target_version,
            integration_version=target_version,
            receiver_sha256=receiver_sha256,
            staged_version=target_version,
            restart_state="verified",
        )
        validate_health(http_json("/healthz"), target_version)
        require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")
        print(
            json.dumps(
                {
                    "phase": "core_restarted",
                    "target": TARGET_NAME,
                    "tag": arguments.tag,
                    "source_commit": arguments.source_commit,
                    "integration_version": target_version,
                    "home_assistant_core": result["home_assistant_core"],
                    "rollback_directory": result["rollback_directory"],
                    "rollback_backup": result["rollback_backup"],
                    "bridge_health": "ok",
                    "core_restart_performed": True,
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
