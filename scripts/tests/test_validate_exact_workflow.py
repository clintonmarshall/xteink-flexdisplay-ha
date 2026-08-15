from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".forgejo/workflows/validate-exact.yml"


class ValidateExactWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_coordinated_release_markers_force_android_validation(self) -> None:
        for marker in (
            "^flexdisplay_bridge/config\\.yaml$",
            "^flexdisplay_bridge/pyproject\\.toml$",
            "^flexdisplay_bridge/flexdisplay_bridge/__init__\\.py$",
            "^custom_components/flexdisplay/manifest\\.json$",
        ):
            self.assertIn(marker, self.workflow)
        self.assertIn("release: ${{ steps.paths.outputs.release }}", self.workflow)
        self.assertIn(
            "changed '^rook_receiver/' || [ \"$release\" = \"true\" ]",
            self.workflow,
        )
        self.assertIn('echo "release=$release"', self.workflow)

    def test_release_android_package_runs_on_the_exact_head(self) -> None:
        release_step = self.workflow.index(
            "- name: Validate release Android package on the exact release head"
        )
        required_job = self.workflow.index("\n  required:", release_step)
        block = self.workflow[release_step:required_job]
        self.assertIn(
            "if: ${{ needs.changes.outputs.release == 'true' }}", block
        )
        self.assertIn("testCompanionReleaseUnitTest", block)
        self.assertIn("lintCompanionRelease", block)
        self.assertIn("assembleCompanionRelease", block)

    def test_required_gate_still_requires_the_android_job(self) -> None:
        required = self.workflow[self.workflow.index("\n  required:") :]
        self.assertIn("- android", required)
        self.assertIn("ANDROID_REQUIRED: ${{ needs.changes.outputs.android }}", required)
        self.assertIn(
            'require_optional android "$ANDROID_REQUIRED" "$ANDROID_RESULT"',
            required,
        )


if __name__ == "__main__":
    unittest.main()
