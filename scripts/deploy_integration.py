#!/usr/bin/env python3
"""Stage the exact tagged FlexDisplay integration on DumbHA without restarting Core."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
from typing import Any

from deploy_bridge import (
    DeploymentError,
    EXPECTED_APP_REPOSITORY,
    EXPECTED_APP_SLUG,
    EXPECTED_APP_SOURCE_URL,
    EXPECTED_HOST_FINGERPRINT,
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


CONFIRMATION = "stage-flexdisplay-integration-on-dumbha"


def build_integration_archive(integration: Path) -> bytes:
    if not integration.is_dir():
        raise DeploymentError("tagged integration directory is missing")
    paths = [integration, *sorted(integration.rglob("*"))]
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in paths:
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise DeploymentError("integration source contains an unsupported entry")
            relative = path.relative_to(integration)
            arcname = Path("custom_components/flexdisplay") / relative
            info = archive.gettarinfo(str(path), arcname=str(arcname))
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as source:
                    archive.addfile(info, source)
            else:
                archive.addfile(info)
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0) as output:
        output.write(tar_buffer.getvalue())
    return compressed.getvalue()


def validate_status(
    payload: Any,
    *,
    bridge_version: str,
    integration_version: str,
    receiver_sha256: str,
    staged_version: str | None = None,
    restart_state: str | None = None,
) -> dict[str, Any]:
    status = require_mapping(payload, "remote status")
    if status.get("receiver_sha256") != receiver_sha256:
        raise DeploymentError("installed deployment receiver is not the tagged copy")
    app = require_mapping(status.get("app"), "remote App status")
    expected_app = {
        "slug": EXPECTED_APP_SLUG,
        "repository": EXPECTED_APP_REPOSITORY,
        "source_url": EXPECTED_APP_SOURCE_URL,
        "version": bridge_version,
        "state": "started",
        "auto_update": False,
    }
    for key, expected in expected_app.items():
        if app.get(key) != expected:
            raise DeploymentError(f"unexpected Bridge App {key}")
    integration = require_mapping(status.get("integration"), "remote integration status")
    if integration.get("version") != integration_version:
        raise DeploymentError("unexpected installed integration version")
    if staged_version is not None or restart_state is not None:
        stage = require_mapping(integration.get("stage"), "remote integration stage")
        if stage.get("target_version") != staged_version:
            raise DeploymentError("unexpected staged integration version")
        if stage.get("receiver_sha256") != receiver_sha256:
            raise DeploymentError("staged integration used a different receiver")
        if stage.get("core_restart_state") != restart_state:
            raise DeploymentError("unexpected Core restart state")
        expected_performed = restart_state == "verified"
        if stage.get("core_restart_performed") is not expected_performed:
            raise DeploymentError("unexpected Core restart completion state")
    else:
        stage = integration.get("stage")
        if isinstance(stage, dict) and stage.get("core_restart_performed") is False:
            raise DeploymentError("an earlier integration stage is still pending")
    return status


def run_command_with_input(
    args: list[str], environment: dict[str, str], payload: bytes, *, timeout: int
) -> str:
    try:
        completed = subprocess.run(
            args,
            input=payload,
            check=False,
            capture_output=True,
            env=environment,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        detail = (error.stderr or b"").decode("utf-8", errors="replace")[-800:]
        raise DeploymentError(
            "restricted integration command timed out; remote state must be reconciled"
            + (f": {detail.strip()}" if detail.strip() else "")
        ) from None
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-800:]
        raise DeploymentError(
            "restricted integration command failed"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout.decode("utf-8", errors="strict")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-bridge-version", required=True)
    parser.add_argument("--expected-current-integration-version", required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument(
        "--receiver",
        type=Path,
        default=Path(__file__).with_name("flexdisplay_bridge_deploy_receiver.sh"),
    )
    parser.add_argument(
        "--integration",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "custom_components/flexdisplay",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not arguments.tag.startswith("v") or not SEMVER.fullmatch(arguments.tag[1:]):
        raise DeploymentError("tag must be a stable vX.Y.Z release")
    target_version = arguments.tag[1:]
    if not SHA40.fullmatch(arguments.source_commit):
        raise DeploymentError("source commit must be a full lowercase SHA")
    for value, label in (
        (arguments.expected_bridge_version, "expected Bridge version"),
        (
            arguments.expected_current_integration_version,
            "expected current integration version",
        ),
    ):
        if not SEMVER.fullmatch(value):
            raise DeploymentError(f"{label} must be stable SemVer")
    if arguments.expected_bridge_version != target_version:
        raise DeploymentError("Bridge must already be running the tagged version")
    if arguments.expected_current_integration_version == target_version:
        raise DeploymentError("target integration version must differ from the installed version")
    if arguments.confirmation != CONFIRMATION:
        raise DeploymentError("integration staging confirmation phrase does not match")
    if not arguments.receiver.is_file():
        raise DeploymentError("reviewed deployment receiver is missing from the tag")

    receiver_sha256 = hashlib.sha256(arguments.receiver.read_bytes()).hexdigest()
    archive = build_integration_archive(arguments.integration)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    if not SHA256.fullmatch(receiver_sha256) or not SHA256.fullmatch(archive_sha256):
        raise DeploymentError("deployment checksum is invalid")

    private_key = load_secret("FLEXDISPLAY_DUMBHA_DEPLOY_KEY")
    known_hosts_value = load_secret("FLEXDISPLAY_DUMBHA_KNOWN_HOSTS")
    with tempfile.TemporaryDirectory(prefix="flexdisplay-integration-") as directory:
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
        validate_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            bridge_version=arguments.expected_bridge_version,
            integration_version=arguments.expected_current_integration_version,
            receiver_sha256=receiver_sha256,
        )
        validate_health(http_json("/healthz"), arguments.expected_bridge_version)
        require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")
        print(
            json.dumps(
                {
                    "phase": "preflight",
                    "target": TARGET_NAME,
                    "tag": arguments.tag,
                    "source_commit": arguments.source_commit,
                    "bridge_version": arguments.expected_bridge_version,
                    "integration_version": arguments.expected_current_integration_version,
                    "integration_archive_sha256": archive_sha256,
                },
                sort_keys=True,
            ),
            flush=True,
        )

        command = (
            f"stage-integration {target_version} "
            f"{arguments.expected_current_integration_version} {archive_sha256} "
            f"{receiver_sha256}"
        )
        result = require_mapping(
            parse_json(
                run_command_with_input(
                    [*ssh_prefix, command], environment, archive, timeout=900
                ),
                "remote integration staging",
            ),
            "remote integration staging",
        )
        expected_result = {
            "target_version": target_version,
            "previous_version": arguments.expected_current_integration_version,
            "source_archive_sha256": archive_sha256,
            "receiver_sha256": receiver_sha256,
            "core_restart_performed": False,
            "core_restart_state": "not_started",
        }
        for key, expected in expected_result.items():
            if result.get(key) != expected:
                raise DeploymentError(f"integration staging returned unexpected {key}")
        for field in ("rollback_directory", "rollback_backup"):
            if not isinstance(result.get(field), str) or not result[field]:
                raise DeploymentError(f"integration staging omitted {field}")

        validate_status(
            parse_json(run_command([*ssh_prefix, "status"], environment), "remote status"),
            bridge_version=arguments.expected_bridge_version,
            integration_version=target_version,
            receiver_sha256=receiver_sha256,
            staged_version=target_version,
            restart_state="not_started",
        )
        validate_health(http_json("/healthz"), arguments.expected_bridge_version)
        require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")
        print(
            json.dumps(
                {
                    "phase": "integration_staged",
                    "target": TARGET_NAME,
                    **expected_result,
                    "rollback_directory": result["rollback_directory"],
                    "rollback_backup": result["rollback_backup"],
                },
                sort_keys=True,
            )
        )
        del private_key, known_hosts_value, archive
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeploymentError as error:
        raise SystemExit(f"Integration staging blocked: {error}") from None
