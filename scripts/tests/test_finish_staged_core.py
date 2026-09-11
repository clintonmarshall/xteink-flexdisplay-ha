"""Offline execution of completion guards: no network or real HA commands."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from deploy_integration import build_integration_archive
from deploy_bridge import DeploymentError
import restart_core


class CompletionReceiverTests(unittest.TestCase):
    def setUp(self):
        for executable in ("bash", "jq", "tar"):
            self.assertIsNotNone(shutil.which(executable), f"Receiver tests require {executable}")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "manifest.json").write_text(json.dumps({"domain": "flexdisplay", "version": "0.50.15"}))
        (self.source / "image.py").write_text("# exact source\n")
        self.installed = self.root / "installed"
        shutil.copytree(self.source, self.installed)
        self.rollback_root = self.root / "rollbacks"
        self.rollback = self.rollback_root / "20260911T042530Z-0.50.14"
        (self.rollback / "flexdisplay").mkdir(parents=True)
        (self.rollback / "flexdisplay/manifest.json").write_text(json.dumps({"domain": "flexdisplay", "version": "0.50.14"}))
        self.payload = build_integration_archive(self.source)
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.record = dict(target_version="0.50.15", previous_version="0.50.14",
                           receiver_sha256="a" * 64, source_archive_sha256=self.sha,
                           rollback_backup="backup123", rollback_directory=str(self.rollback),
                           core_restart_state="not_started", core_restart_performed=False)
        self.stage = self.root / "stage.json"
        self.backup_ok = True
        self.core = "2026.9.1"
        self.app_version = "0.50.15"

    def execute(self, success=False):
        self.stage.write_text(json.dumps(self.record))
        receiver = (ROOT / "scripts/flexdisplay_bridge_deploy_receiver.sh").read_text()
        def function(name, next_name):
            return name + "() {" + receiver.split(name + "() {", 1)[1].split(next_name + "() {", 1)[0]
        functions = "\n".join((function("integration_version", "status_json"),
                               function("validate_integration_archive", "stage_integration"),
                               function("finish_staged_core", "reconcile_core_restart")))
        ha = self.root / "ha"
        ha.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(json.dumps({"result": "ok" if self.backup_ok else "error", "data": {"slug": "backup123", "homeassistant": "2026.9.1"}})) + "\n")
        ha.chmod(0o700)
        values = {"temporary_directory": str(self.root), "INTEGRATION_DIR": str(self.installed),
                  "INTEGRATION_STAGE_RECORD": str(self.stage), "INTEGRATION_ROLLBACK_ROOT": str(self.rollback_root),
                  "SELF_SHA256": "b" * 64, "JQ": shutil.which("jq"), "TAR": shutil.which("tar"),
                  "HA_CLI": str(ha)}
        script = "set -Eeuo pipefail\n" + "\n".join(f"{k}={shlex.quote(v)}" for k, v in values.items())
        script += '\nsha_tool() { shasum -a 256 "$@"; }\nSHA256SUM=sha_tool\n' if sys.platform == "darwin" else '\nSHA256SUM=sha256sum\n'
        script += '\ncore_version() { printf "%s" ' + shlex.quote(self.core) + '; }\n'
        app = json.dumps({"data": {"version": self.app_version, "state": "started", "auto_update": False}})
        script += 'app_info() { printf "%s" ' + shlex.quote(app) + ' > "$1"; }\n'
        script += 'restart_core_for_integration() { printf "RESTART_BOUNDARY:%s:%s:%s\\n" "$1" "$2" "$3"; }\n'
        script += functions
        script += '\nfinish_staged_core 0.50.15 ' + 'b' * 64 + ' ' + 'a' * 64 + f' {self.sha} backup123 2026.9.1\n'
        result = subprocess.run(["bash", "-c", script], input=self.payload, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout.decode().count("RESTART_BOUNDARY:"), 1)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"RESTART_BOUNDARY:", result.stdout)

    def test_exact_source_reaches_restart_boundary_once(self):
        self.execute(success=True)

    def test_runtime_bytecode_is_allowed(self):
        (self.installed / "__pycache__").mkdir()
        (self.installed / "__pycache__/image.cpython-313.pyc").write_bytes(b"runtime")
        self.execute(success=True)

    def test_altered_file_blocks(self):
        (self.installed / "image.py").write_text("changed")
        self.execute()

    def test_missing_file_blocks(self):
        (self.installed / "image.py").unlink()
        self.execute()

    def test_extra_source_blocks(self):
        (self.installed / "extra.py").write_text("extra")
        self.execute()

    def test_symlink_blocks(self):
        (self.installed / "link").symlink_to(self.source / "image.py")
        self.execute()

    def test_requested_and_verified_records_block(self):
        for state in ("requested", "verified"):
            with self.subTest(state=state):
                self.record["core_restart_state"] = state
                self.execute()

    def test_stage_bindings_block_mismatch(self):
        for field in ("target_version", "receiver_sha256", "source_archive_sha256", "rollback_backup"):
            with self.subTest(field=field):
                old = self.record[field]
                self.record[field] = "wrong"
                self.execute()
                self.record[field] = old

    def test_missing_backup_blocks(self):
        self.backup_ok = False
        self.execute()

    def test_missing_rollback_blocks(self):
        (self.rollback / "flexdisplay/manifest.json").unlink()
        self.execute()

    def test_changed_core_blocks(self):
        self.core = "2026.9.2"
        self.execute()

    def test_changed_bridge_blocks(self):
        self.app_version = "0.50.16"
        self.execute()

    def test_corrupt_archive_blocks(self):
        self.payload += b"changed"
        self.execute()

    def test_original_restart_keeps_one_time_guard(self):
        receiver = (ROOT / "scripts/flexdisplay_bridge_deploy_receiver.sh").read_text()
        method = receiver.split("restart_core_for_integration() {", 1)[1].split("finish_staged_core() {", 1)[0]
        self.assertIn('staged_receiver_sha=${3:-$expected_receiver_sha}', method)
        self.assertLess(method.index('.core_restart_state = "requested"'), method.index('"$HA_CLI" core restart'))
        self.assertEqual(method.count('"$HA_CLI" core restart'), 1)


class CompletionWorkflowTests(unittest.TestCase):
    def test_completion_requires_its_own_confirmation_before_credentials(self):
        args = ["restart_core.py", "--tag", "v0.50.15", "--source-commit", "a" * 40,
                "--confirmation", restart_core.CONFIRMATION, "--expected-backup", "backup123"]
        with patch.object(sys, "argv", args), patch.object(restart_core, "load_secret") as secret:
            with self.assertRaisesRegex(DeploymentError, "separate confirmation"):
                restart_core.main()
            secret.assert_not_called()

    def test_missing_completion_evidence_rejected_before_credentials(self):
        args = ["restart_core.py", "--tag", "v0.50.15", "--source-commit", "a" * 40,
                "--confirmation", restart_core.FINISH_CONFIRMATION]
        with patch.object(sys, "argv", args), patch.object(restart_core, "load_secret") as secret:
            with self.assertRaisesRegex(DeploymentError, "original receiver checksum"):
                restart_core.main()
            secret.assert_not_called()

    def test_completion_refuses_reconciliation_even_zero_run_id(self):
        args = ["restart_core.py", "--tag", "v0.50.15", "--source-commit", "a" * 40,
                "--confirmation", restart_core.FINISH_CONFIRMATION, "--failed-run-id", "0"]
        with patch.object(sys, "argv", args), patch.object(restart_core, "load_secret") as secret:
            with self.assertRaisesRegex(DeploymentError, "refuses reconciliation"):
                restart_core.main()
            secret.assert_not_called()

    def test_manual_dual_release_and_ancestry_gates(self):
        workflow = (ROOT / ".forgejo/workflows/finish-staged-core.yml").read_text()
        for gate in ('workflow_dispatch:', 'forgejo.actor == forgejo.repository_owner',
                     'runs-on: dumbha-flexdisplay-production', 'cancel-in-progress: false',
                     'for release_kind in control staged',
                     'test "$DISPATCH_REF" = "refs/tags/$REQUESTED_TAG"',
                     'test "$DISPATCH_SHA" = "$CONFIRMED_SOURCE_COMMIT"',
                     'git merge-base --is-ancestor "$STAGED_COMMIT" "$CONFIRMED_SOURCE_COMMIT"',
                     'test "$(git rev-parse origin/main)" = "$CONFIRMED_SOURCE_COMMIT"',
                     '--expected-backup "$EXPECTED_BACKUP"', '--expected-core-version "$EXPECTED_CORE_VERSION"'):
            self.assertIn(gate, workflow)
        self.assertNotIn('test "$(git rev-parse origin/main)" = "$STAGED_COMMIT"', workflow)
        for action in re.findall(r"uses:\s*(\S+)", workflow):
            self.assertRegex(action, r"@[0-9a-f]{40}$")
        self.assertNotIn("ha core restart", workflow)


if __name__ == "__main__":
    unittest.main()
