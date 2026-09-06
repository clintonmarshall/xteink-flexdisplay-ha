from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".forgejo/workflows/deploy-bridge.yml"
RECEIVER = ROOT / "scripts/flexdisplay_bridge_deploy_receiver.sh"
CLIENT = ROOT / "scripts/deploy_bridge.py"

spec = importlib.util.spec_from_file_location("deploy_bridge", CLIENT)
assert spec is not None and spec.loader is not None
deploy_bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_bridge)


class BridgeDeploymentWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        self.receiver = RECEIVER.read_text(encoding="utf-8")
        self.client = CLIENT.read_text(encoding="utf-8")

    def test_workflow_is_manual_tag_scoped_and_fixed_target(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("Existing published protected annotated Platform tag", self.workflow)
        self.assertIn("runs-on: dumbha-flexdisplay-production", self.workflow)
        self.assertIn("deploy-flexdisplay-bridge-to-dumbha", self.workflow)
        self.assertIn('TARGET_HOST = "10.200.40.4"', self.client)
        self.assertIn('EXPECTED_APP_SLUG = "629898c9_flexdisplay_bridge"', self.client)
        self.assertIn(
            'EXPECTED_APP_SOURCE_URL = "https://github.com/clintonmarshall/'
            'xteink-flexdisplay-ha"',
            self.client,
        )

    def test_exact_tag_and_all_release_checks_are_required(self) -> None:
        for context in (
            "Validate / bridge (push)",
            "Validate / app (push)",
            "Validate / integration (push)",
            "Validate / android (push)",
            "Validate / required (push)",
        ):
            self.assertIn(context, self.workflow)
        self.assertIn('release.get("draft") is not False', self.workflow)
        self.assertIn('release.get("prerelease") is not False', self.workflow)
        self.assertIn(
            'test "$(git rev-parse origin/main)" = "$CONFIRMED_SOURCE_COMMIT"',
            self.workflow,
        )
        self.assertIn(
            'python3 scripts/check_release_metadata.py --release "${REQUESTED_TAG#v}"',
            self.workflow,
        )

    def test_every_external_action_is_an_immutable_full_sha(self) -> None:
        uses = re.findall(r"^\s*uses:\s*(\S+)", self.workflow, re.MULTILINE)
        self.assertEqual(len(uses), 1)
        self.assertRegex(uses[0], r"@[0-9a-f]{40}$")

    def test_runner_and_credential_controls_fail_closed(self) -> None:
        for variable in (
            "FLEXDISPLAY_DUMBHA_DEPLOYMENT_ENABLED",
            "FLEXDISPLAY_DUMBHA_RUNNER_ISOLATED",
            "FLEXDISPLAY_DUMBHA_CREDENTIAL_PATH_APPROVED",
        ):
            self.assertIn(variable, self.workflow)
        for secret in (
            "FLEXDISPLAY_DUMBHA_DEPLOY_KEY",
            "FLEXDISPLAY_DUMBHA_KNOWN_HOSTS",
            "FLEXDISPLAY_DUMBHA_BRIDGE_API_KEY",
        ):
            self.assertIn(secret, self.workflow)
        self.assertIn("StrictHostKeyChecking=yes", self.client)
        self.assertIn("ClearAllForwardings=yes", self.client)
        self.assertIn("EXPECTED_HOST_FINGERPRINT", self.client)

    def test_bridge_operation_is_backup_first_and_never_restarts_core(self) -> None:
        bridge_operation = self.receiver.split("deploy_bridge()", 1)[1].split(
            'readonly ORIGINAL_COMMAND=', 1
        )[0]
        self.assertIn('core check --no-progress --raw-json', bridge_operation)
        self.assertIn('store reload --no-progress --raw-json', bridge_operation)
        self.assertIn('backups new', bridge_operation)
        self.assertIn('--app "$APP_SLUG"', bridge_operation)
        self.assertIn('apps update "$APP_SLUG"', bridge_operation)
        self.assertIn('.data.auto_update', bridge_operation)
        self.assertIn('= "false"', bridge_operation)
        self.assertNotIn("core restart", bridge_operation.lower())
        self.assertNotIn("auto-update", bridge_operation.lower())

    def test_client_checks_required_live_services_and_preserves_checkins(self) -> None:
        for evidence in (
            'http_json("/healthz")',
            'require_http_200("/", HOME_ASSISTANT_PORT, "Home Assistant")',
            'require_http_200("/studio/", BRIDGE_PORT, "FlexDisplay Studio")',
            'http_json("/api/v1/devices?compact=true", bridge_key)',
            "mqtt_connected",
            "flexhub_connected",
        ):
            self.assertIn(evidence, self.client)

        before = {
            "X3-ONE": datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc),
            "ROOK-ONE": datetime(2026, 8, 15, 1, 5, tzinfo=timezone.utc),
        }
        after = {
            **before,
            "ROOK-ONE": datetime(2026, 8, 15, 1, 6, tzinfo=timezone.utc),
        }
        self.assertEqual(deploy_bridge.compare_device_checkins(before, after), (2, 1))
        with self.assertRaises(deploy_bridge.DeploymentError):
            deploy_bridge.compare_device_checkins(before, {"X3-ONE": before["X3-ONE"]})

    def test_status_validation_requires_disabled_auto_update_and_exact_receiver(self) -> None:
        receiver_sha = "a" * 64
        status = {
            "receiver_sha256": receiver_sha,
            "core_version": "2026.8.2",
            "app": {
                "slug": "629898c9_flexdisplay_bridge",
                "repository": "629898c9",
                "source_url": "https://github.com/clintonmarshall/xteink-flexdisplay-ha",
                "version": "0.46.0",
                "state": "started",
                "auto_update": False,
            },
        }
        deploy_bridge.validate_remote_status(
            status, expected_version="0.46.0", receiver_sha256=receiver_sha
        )
        status["app"]["auto_update"] = True
        with self.assertRaises(deploy_bridge.DeploymentError):
            deploy_bridge.validate_remote_status(
                status, expected_version="0.46.0", receiver_sha256=receiver_sha
            )


if __name__ == "__main__":
    unittest.main()
