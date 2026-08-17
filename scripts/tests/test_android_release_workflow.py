from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".forgejo/workflows/android-release.yml"
RELEASE_DOCS = ROOT / "docs/RELEASE.md"


class AndroidReleaseWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")
        marker = "python3 -I - <<'PY'\n"
        script_start = self.workflow.index(marker) + len(marker)
        script_end = self.workflow.index("\n          PY", script_start)
        self.preflight_script = textwrap.dedent(
            self.workflow[script_start:script_end]
        )

    def run_status_check(
        self, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            status_file = Path(temporary_directory) / "status.json"
            status_file.write_text(json.dumps(payload), encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "STATUS_JSON": str(status_file),
                    "EXPECTED_REPOSITORY": "clintonmarshall/xteink-flexdisplay-ha",
                    "EXPECTED_SHA": "a" * 40,
                    "REQUIRED_CONTEXT": "Validate / bridge (push)",
                }
            )
            return subprocess.run(
                [sys.executable, "-I", "-c", self.preflight_script],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

    @staticmethod
    def successful_status() -> dict[str, object]:
        return {
            "sha": "a" * 40,
            "state": "success",
            "repository": {"full_name": "clintonmarshall/xteink-flexdisplay-ha"},
            "statuses": [
                {"context": "Validate / bridge (push)", "status": "success"}
            ],
        }

    def test_exact_status_preflight_runs_before_checkout_and_repo_scripts(self) -> None:
        preflight = self.workflow.index("- name: Verify exact Forgejo validation status")
        checkout = self.workflow.index("- name: Check out the exact release tag")
        repository_script = self.workflow.index("scripts/check_release_metadata.py")
        self.assertLess(preflight, checkout)
        self.assertLess(checkout, repository_script)
        self.assertIn(
            "commits/$CONFIRMED_SOURCE_COMMIT/status?limit=100",
            self.workflow[preflight:checkout],
        )
        self.assertIn("REQUIRED_CONTEXT='Validate / bridge (push)'", self.workflow)

    def test_preflight_uses_only_the_step_scoped_automatic_token(self) -> None:
        preflight_start = self.workflow.index(
            "- name: Verify exact Forgejo validation status"
        )
        checkout_start = self.workflow.index("- name: Check out the exact release tag")
        preflight = self.workflow[preflight_start:checkout_start]
        self.assertIn("RELEASE_API_TOKEN: ${{ forgejo.token }}", preflight)
        self.assertIn('FORGEJO_TOKEN: ""', preflight)
        self.assertIn('GITHUB_TOKEN: ""', preflight)
        self.assertIn(
            "unset RELEASE_API_TOKEN FORGEJO_TOKEN GITHUB_TOKEN", preflight
        )

    def test_release_attempts_are_serialized_by_tag(self) -> None:
        self.assertIn("group: android-companion-${{ inputs.tag }}", self.workflow)
        self.assertNotIn(
            "group: android-companion-${{ inputs.tag }}-${{ inputs.draft_release_id }}",
            self.workflow,
        )

    def test_release_runner_installs_the_pinned_android_toolchain(self) -> None:
        checkout = self.workflow.index("- name: Check out the exact release tag")
        install = self.workflow.index("- name: Install pinned Android release toolchain")
        metadata = self.workflow.index("- name: Verify reviewed release source")
        self.assertLess(checkout, install)
        self.assertLess(install, metadata)
        self.assertIn(
            "3fab261d5219d582321db0c5670b3bbafd563096bce3f6277eb358807fc15f6e",
            self.workflow[install:metadata],
        )
        self.assertIn("'platforms;android-33' 'build-tools;30.0.3'", self.workflow)
        self.assertIn("ANDROID_SDK_ROOT: /tmp/flexdisplay-android-sdk", self.workflow)

    def test_release_checkout_uses_native_git_without_node_or_credentials(self) -> None:
        self.assertNotIn("uses:", self.workflow)
        self.assertIn("git init .", self.workflow)
        self.assertIn("GIT_CONFIG_GLOBAL: /dev/null", self.workflow)
        self.assertIn("GIT_CONFIG_SYSTEM: /dev/null", self.workflow)
        self.assertIn(
            "-c credential.helper= -c http.followRedirects=false", self.workflow
        )
        self.assertIn(
            "+refs/heads/main:refs/remotes/origin/main", self.workflow
        )
        self.assertIn(
            '"+refs/tags/v*:refs/tags/v*"',
            self.workflow,
        )
        self.assertNotIn("--depth=1", self.workflow)
        self.assertIn(
            'test "$(git rev-parse refs/remotes/origin/main)" = '
            '"$CONFIRMED_SOURCE_COMMIT"',
            self.workflow,
        )
        self.assertIn('test -z "$FORGEJO_TOKEN$GITHUB_TOKEN"', self.workflow)

    def test_status_and_exact_main_checks_are_fail_closed(self) -> None:
        self.assertIn('payload.get("sha") != expected_sha', self.workflow)
        self.assertIn('payload.get("state") != "success"', self.workflow)
        self.assertIn('required[0].get("status") != "success"', self.workflow)
        self.assertIn(
            'test "$(git rev-parse origin/main)" = "$CONFIRMED_SOURCE_COMMIT"',
            self.workflow,
        )

    def test_status_preflight_accepts_only_exact_green_required_context(self) -> None:
        accepted = self.run_status_check(self.successful_status())
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        mutations = (
            ("wrong SHA", {"sha": "b" * 40}),
            ("combined failure", {"state": "failure"}),
            ("missing required context", {"statuses": []}),
            (
                "required context failed",
                {
                    "statuses": [
                        {
                            "context": "Validate / bridge (push)",
                            "status": "failure",
                        }
                    ]
                },
            ),
        )
        for label, mutation in mutations:
            with self.subTest(label=label):
                payload = self.successful_status()
                payload.update(mutation)
                rejected = self.run_status_check(payload)
                self.assertNotEqual(rejected.returncode, 0)

    def test_tag_protection_is_an_explicit_admin_precondition(self) -> None:
        release_docs = RELEASE_DOCS.read_text(encoding="utf-8")
        normalised_docs = " ".join(release_docs.split())
        self.assertIn(
            "automatic Actions token has repository-write rather than "
            "repository-admin access",
            normalised_docs,
        )
        self.assertIn("Settings > Tags", release_docs)

    def test_signing_authorisation_does_not_claim_environment_protection(self) -> None:
        release_docs = RELEASE_DOCS.read_text(encoding="utf-8")
        self.assertNotIn("environment: android-release", self.workflow)
        self.assertIn("just in time", release_docs)
        self.assertIn("remove the secrets and runner registration", release_docs)

    def test_draft_assets_are_verified_through_authenticated_uuid_routes(self) -> None:
        upload = self.workflow.index("- name: Upload or reconcile exact draft assets")
        workflow = self.workflow[upload:]
        self.assertIn("asset.get('uuid')", workflow)
        self.assertIn('remote_url="$forgejo_server/attachments/$asset_uuid"', workflow)
        self.assertIn("asset.get('type') != 'attachment'", workflow)
        self.assertIn('test "$remote_size" = "$local_size"', workflow)
        self.assertIn("--noproxy '*'", workflow)
        self.assertIn("--max-redirs 0", workflow)
        self.assertNotIn("browser_download_url", workflow)
        self.assertNotIn("--location", workflow)


if __name__ == "__main__":
    unittest.main()
