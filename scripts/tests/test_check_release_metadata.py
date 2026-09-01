from __future__ import annotations

import importlib.util
import sys
import unittest
from subprocess import CompletedProcess
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "check_release_metadata.py"
SPEC = importlib.util.spec_from_file_location("check_release_metadata", SCRIPT)
assert SPEC and SPEC.loader
metadata = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = metadata
SPEC.loader.exec_module(metadata)


class ReleaseMetadataTests(unittest.TestCase):
    def test_strict_semver_accepts_complete_versions(self) -> None:
        valid = (
            "0.0.0",
            "1.2.3",
            "1.2.3-0",
            "1.2.3-alpha.1+build.01",
        )
        for value in valid:
            with self.subTest(value=value):
                self.assertIsInstance(metadata.parse_semver(value), metadata.SemVer)

    def test_strict_semver_rejects_invalid_versions(self) -> None:
        invalid = (
            "v1.2.3",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2",
            "1.2.3-",
            "1.2.3-01",
            "1.2.3-alpha..1",
            "1.2.3+",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    metadata.parse_semver(value)

    def test_semver_comparison_follows_precedence(self) -> None:
        ordered = (
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        )
        parsed = [metadata.parse_semver(value) for value in ordered]
        self.assertEqual(sorted(reversed(parsed)), parsed)
        self.assertEqual(
            metadata.parse_semver("1.0.0+build.1"),
            metadata.parse_semver("1.0.0+build.2"),
        )

    def test_preceding_release_tags_use_semver_order(self) -> None:
        with mock.patch.object(
            metadata,
            "merged_release_tags",
            return_value=["v1.2.0", "v1.10.0", "v2.0.0", "v1.9.0"],
        ):
            self.assertEqual(
                metadata.preceding_release_tags("2.1.0"),
                ["v2.0.0", "v1.10.0", "v1.9.0", "v1.2.0"],
            )

    def test_release_version_must_exceed_latest_reachable_tag(self) -> None:
        cases = (
            (["v1.3.0"], "1.2.0"),
            (["v1.2.0+old"], "1.2.0+new"),
        )
        for tags, current in cases:
            with self.subTest(tags=tags, current=current):
                with (
                    mock.patch.object(
                        metadata, "merged_release_tags", return_value=tags
                    ),
                    self.assertRaisesRegex(ValueError, "must exceed"),
                ):
                    metadata.preceding_release_tags(current)

    def test_exact_current_release_tag_is_excluded(self) -> None:
        with (
            mock.patch.object(
                metadata,
                "merged_release_tags",
                return_value=["v1.2.3", "v1.2.2"],
            ),
            mock.patch.object(metadata, "tag_targets_head", return_value=True),
        ):
            self.assertEqual(
                metadata.preceding_release_tags("1.2.3"), ["v1.2.2"]
            )

    def test_existing_current_version_tag_at_another_commit_is_rejected(self) -> None:
        with (
            mock.patch.object(
                metadata, "merged_release_tags", return_value=["v1.2.3"]
            ),
            mock.patch.object(metadata, "tag_targets_head", return_value=False),
            self.assertRaisesRegex(ValueError, "must exceed"),
        ):
            metadata.preceding_release_tags("1.2.3")

    def test_invalid_reachable_release_tag_fails_closed(self) -> None:
        with mock.patch.object(
            metadata,
            "merged_release_tags",
            return_value=["v1.2"],
        ):
            with self.assertRaisesRegex(ValueError, "reachable release tag"):
                metadata.preceding_release_tags("2.0.0")

    def test_receiver_metadata(self) -> None:
        version, code = metadata.receiver_metadata(
            'defaultConfig {\n  versionCode 17\n  versionName "2.4.1"\n}\n'
        )
        self.assertEqual((version, code), ("2.4.1", 17))

    def test_current_platform_markers_agree(self) -> None:
        versions = metadata.platform_versions()
        self.assertEqual(len(set(versions.values())), 1)

    def test_current_release_manifest_and_generated_status_agree(self) -> None:
        version = next(iter(metadata.platform_versions().values()))
        self.assertEqual(metadata.manifest_errors(version), [])

    def test_release_status_keeps_distribution_boundaries_separate(self) -> None:
        rendered = metadata.render_release_status(metadata.release_manifest())
        self.assertIn("Authoritative Forgejo release", rendered)
        self.assertIn("Home Assistant deployment", rendered)
        self.assertIn("Android Companion APK", rendered)
        self.assertIn("Device firmware rollout", rendered)
        self.assertIn("Physical device behaviour", rendered)
        self.assertIn("not proof of a Home Assistant deployment", rendered)

    def test_runtime_firmware_default_drift_is_rejected(self) -> None:
        manifest = metadata.release_manifest()
        artifacts = {
            artifact["config_prefix"]: artifact
            for artifact in manifest["packaged_artifacts"]
        }

        def runtime_default(_path: Path, name: str) -> dict[str, object]:
            prefix = "firmware" if name == "DEFAULT_FIRMWARE" else "note4_firmware"
            artifact = artifacts[prefix]
            result = {
                f"{prefix}_version": artifact["version"],
                f"{prefix}_size": artifact["size"],
                f"{prefix}_sha256": artifact["sha256"],
            }
            if prefix == "firmware":
                result["firmware_size"] = int(artifact["size"]) - 1
            return result

        version = next(iter(metadata.platform_versions().values()))
        with mock.patch.object(metadata, "module_dict", side_effect=runtime_default):
            errors = metadata.manifest_errors(version)
        self.assertTrue(any("does not match app_runner.py" in error for error in errors))

    def test_changed_partial_firmware_is_release_blocking(self) -> None:
        manifest = {
            "platform": {"classification": "software-only"},
            "packaged_artifacts": [
                {
                    "id": "x3_x4_firmware",
                    "path": "firmware.bin",
                    "provenance": {"status": "partial"},
                }
            ],
        }
        with mock.patch.object(metadata, "git_path_changed", return_value=True):
            errors = metadata.firmware_classification_errors(manifest, "v1.0.0")
        self.assertTrue(any("classification" in error for error in errors))
        self.assertTrue(any("requires verified" in error for error in errors))

    def test_unchanged_firmware_must_be_software_only(self) -> None:
        manifest = {
            "platform": {"classification": "firmware-bearing"},
            "packaged_artifacts": [
                {
                    "id": "x3_x4_firmware",
                    "path": "firmware.bin",
                    "provenance": {"status": "verified"},
                }
            ],
        }
        with mock.patch.object(metadata, "git_path_changed", return_value=False):
            errors = metadata.firmware_classification_errors(manifest, "v1.0.0")
        self.assertEqual(len(errors), 1)
        self.assertIn("software-only", errors[0])

    def test_default_mode_does_not_apply_release_only_checks(self) -> None:
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT)]),
            mock.patch.object(
                metadata,
                "release_errors",
                side_effect=AssertionError("release checks must not run"),
            ),
        ):
            self.assertEqual(metadata.main(), 0)

    def test_android_change_detection_covers_receiver_subtree(self) -> None:
        with mock.patch.object(
            metadata.subprocess,
            "run",
            return_value=CompletedProcess([], 1, "", ""),
        ) as run:
            self.assertTrue(metadata.git_path_changed("v1.0.0", "rook_receiver"))
        self.assertEqual(
            run.call_args.args[0],
            ["git", "diff", "--quiet", "v1.0.0", "HEAD", "--", "rook_receiver"],
        )

    def test_compatibility_checks_packaged_firmware_versions(self) -> None:
        rows = {
            "FlexDisplay platform": "1.2.3",
            "Echo Spot receiver": "0.4.0",
            "Echo Show 5 receiver": "0.4.0",
            "X3/X4 packaged firmware": "stale-x3",
            "Note 4 packaged firmware": "stale-note4",
        }
        config_versions = {
            "firmware_version": "1.5.0",
            "note4_firmware_version": "1.2.2",
        }
        with (
            mock.patch.object(metadata, "compatibility_versions", return_value=rows),
            mock.patch.object(
                metadata,
                "yaml_scalar",
                side_effect=lambda _path, key: config_versions[key],
            ),
        ):
            errors = metadata.compatibility_errors("1.2.3", "0.4.0")
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("X3/X4 packaged firmware" in error for error in errors))
        self.assertTrue(any("Note 4 packaged firmware" in error for error in errors))

    def test_current_release_metadata_passes_strict_gate(self) -> None:
        version = next(iter(metadata.platform_versions().values()))
        # This assertion models validation from the immutable release tag.
        # A later feature-branch HEAD at the already-published version must
        # remain ineligible for release, even though its checked-in metadata
        # should still be verified against the released state.
        with mock.patch.object(metadata, "tag_targets_head", return_value=True):
            self.assertEqual(metadata.release_errors(version), [])


if __name__ == "__main__":
    unittest.main()
