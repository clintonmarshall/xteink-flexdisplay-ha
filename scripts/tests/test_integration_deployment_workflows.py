from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_integration  # noqa: E402
from deploy_bridge import DeploymentError  # noqa: E402


INTEGRATION_WORKFLOW = ROOT / ".forgejo/workflows/deploy-integration.yml"
RESTART_WORKFLOW = ROOT / ".forgejo/workflows/restart-core.yml"
RECEIVER = ROOT / "scripts/flexdisplay_bridge_deploy_receiver.sh"


class IntegrationDeploymentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.integration_workflow = INTEGRATION_WORKFLOW.read_text(encoding="utf-8")
        self.restart_workflow = RESTART_WORKFLOW.read_text(encoding="utf-8")
        self.receiver = RECEIVER.read_text(encoding="utf-8")

    def test_workflows_are_manual_tag_scoped_and_separately_confirmed(self) -> None:
        self.assertIn("workflow_dispatch:", self.integration_workflow)
        self.assertIn("workflow_dispatch:", self.restart_workflow)
        self.assertIn(
            "stage-flexdisplay-integration-on-dumbha", self.integration_workflow
        )
        self.assertNotIn(
            "restart-dumbha-core-for-flexdisplay-integration",
            self.integration_workflow,
        )
        self.assertIn(
            "restart-dumbha-core-for-flexdisplay-integration",
            self.restart_workflow,
        )
        self.assertNotIn("stage-flexdisplay-integration-on-dumbha", self.restart_workflow)
        for workflow in (self.integration_workflow, self.restart_workflow):
            self.assertIn("runs-on: dumbha-flexdisplay-production", workflow)
            self.assertIn('release.get("draft") is not False', workflow)
            self.assertIn('release.get("prerelease") is not False', workflow)
            self.assertIn(
                'test "$(git rev-parse origin/main)" = "$CONFIRMED_SOURCE_COMMIT"',
                workflow,
            )

    def test_external_actions_are_immutable(self) -> None:
        for workflow in (self.integration_workflow, self.restart_workflow):
            uses = re.findall(r"^\s*uses:\s*(\S+)", workflow, re.MULTILINE)
            self.assertEqual(len(uses), 1)
            self.assertRegex(uses[0], r"@[0-9a-f]{40}$")

    def test_staging_is_backup_first_and_stops_before_restart(self) -> None:
        stage = self.receiver.split("restart_core_for_integration()", 1)[0]
        self.assertIn("backups new", stage)
        self.assertIn("--folders homeassistant", stage)
        self.assertIn("--homeassistant-exclude-database", stage)
        self.assertIn('cp -a "$INTEGRATION_DIR"', stage)
        self.assertGreaterEqual(stage.count("core check --no-progress --raw-json"), 2)
        self.assertNotIn("core restart", stage.lower())
        self.assertIn("stage-integration", self.receiver)
        self.assertIn("restart-core", self.receiver)
        self.assertIn('.core_restart_state == "not_started"', self.receiver)
        self.assertIn('.core_restart_state = "requested"', self.receiver)
        self.assertIn('.core_restart_state = "verified"', self.receiver)

    def test_archive_is_deterministic_and_contains_only_integration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "flexdisplay"
            root.mkdir()
            (root / "manifest.json").write_text(
                json.dumps({"domain": "flexdisplay", "version": "0.50.1"}),
                encoding="utf-8",
            )
            (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            first = deploy_integration.build_integration_archive(root)
            second = deploy_integration.build_integration_archive(root)
            self.assertEqual(first, second)
            with gzip.GzipFile(fileobj=io.BytesIO(first), mode="rb") as compressed:
                with tarfile.open(fileobj=io.BytesIO(compressed.read()), mode="r:") as archive:
                    names = archive.getnames()
            self.assertTrue(names)
            self.assertTrue(
                all(
                    name == "custom_components/flexdisplay"
                    or name.startswith("custom_components/flexdisplay/")
                    for name in names
                )
            )

    def test_archive_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "flexdisplay"
            root.mkdir()
            (root / "manifest.json").write_text("{}\n", encoding="utf-8")
            (root / "escape").symlink_to(root / "manifest.json")
            with self.assertRaises(DeploymentError):
                deploy_integration.build_integration_archive(root)

    def test_remote_status_requires_exact_versions_and_receiver(self) -> None:
        receiver = "a" * 64
        status = {
            "receiver_sha256": receiver,
            "core_version": "2026.9.0",
            "integration": {"version": "0.43.0", "stage": None},
            "app": {
                "slug": "629898c9_flexdisplay_bridge",
                "repository": "629898c9",
                "source_url": "https://github.com/clintonmarshall/xteink-flexdisplay-ha",
                "version": "0.50.1",
                "state": "started",
                "auto_update": False,
            },
        }
        deploy_integration.validate_status(
            status,
            bridge_version="0.50.1",
            integration_version="0.43.0",
            receiver_sha256=receiver,
        )
        status["integration"]["version"] = "0.50.0"
        with self.assertRaises(DeploymentError):
            deploy_integration.validate_status(
                status,
                bridge_version="0.50.1",
                integration_version="0.43.0",
                receiver_sha256=receiver,
            )

    def test_remote_status_blocks_pending_or_mismatched_stage(self) -> None:
        receiver = "a" * 64
        status = {
            "receiver_sha256": receiver,
            "integration": {
                "version": "0.50.1",
                "stage": {
                    "target_version": "0.50.1",
                    "receiver_sha256": receiver,
                    "core_restart_performed": False,
                    "core_restart_state": "not_started",
                },
            },
            "app": {
                "slug": "629898c9_flexdisplay_bridge",
                "repository": "629898c9",
                "source_url": "https://github.com/clintonmarshall/xteink-flexdisplay-ha",
                "version": "0.50.1",
                "state": "started",
                "auto_update": False,
            },
        }
        with self.assertRaises(DeploymentError):
            deploy_integration.validate_status(
                status,
                bridge_version="0.50.1",
                integration_version="0.50.1",
                receiver_sha256=receiver,
            )
        deploy_integration.validate_status(
            status,
            bridge_version="0.50.1",
            integration_version="0.50.1",
            receiver_sha256=receiver,
            staged_version="0.50.1",
            restart_state="not_started",
        )


if __name__ == "__main__":
    unittest.main()
