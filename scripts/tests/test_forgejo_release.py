from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/forgejo_release.py"
PROMOTE_WORKFLOW = ROOT / ".forgejo/workflows/promote-release-tag.yml"
PUBLISH_WORKFLOW = ROOT / ".forgejo/workflows/publish-release.yml"

spec = importlib.util.spec_from_file_location("forgejo_release", SCRIPT)
assert spec is not None and spec.loader is not None
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class FakeReadClient:
    def __init__(self, commit: str, *, contexts: tuple[str, ...] | None = None) -> None:
        self.commit = commit
        selected = contexts if contexts is not None else release.REQUIRED_PUSH_CONTEXTS
        self.responses = {
            release.api_path(): {
                "full_name": release.REPOSITORY,
                "default_branch": "main",
            },
            release.api_path("/branches/main"): {
                "protected": True,
                "commit": {"id": commit},
            },
            release.api_path(f"/commits/{commit}/status?limit=100"): {
                "sha": commit,
                "state": "success",
                "statuses": [
                    {"context": context, "status": "success"}
                    for context in selected
                ],
            },
            release.api_path("/tag_protections"): [
                {
                    "name_pattern": "v*",
                    "whitelist_usernames": [release.REPOSITORY_OWNER],
                }
            ],
        }

    def get(self, path: str):
        return self.responses[path]


class ForgejoReleaseTests(unittest.TestCase):
    def test_attachment_client_uses_fixed_same_origin_uuid_route(self) -> None:
        attachment_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"draft asset"
        client = release.ForgejoClient("http://forgejo.test/api/v1", "test-token")
        client.opener.open = mock.Mock(return_value=response)

        self.assertEqual(client.download_attachment(attachment_uuid), b"draft asset")
        request = client.opener.open.call_args.args[0]
        self.assertEqual(
            request.full_url,
            f"http://forgejo.test/attachments/{attachment_uuid}",
        )
        self.assertEqual(request.get_method(), "GET")
        with self.assertRaisesRegex(release.ReleaseError, "UUID"):
            client.download_attachment("../release-draft")

    def test_release_identity_is_strict_stable_semver_and_full_sha(self) -> None:
        self.assertEqual(release.validate_identity("v0.47.0", "a" * 40), "0.47.0")
        for tag in ("0.47.0", "v0.47", "v01.2.3", "v1.2.3-rc.1"):
            with self.subTest(tag=tag):
                with self.assertRaises(release.ReleaseError):
                    release.validate_identity(tag, "a" * 40)
        with self.assertRaises(release.ReleaseError):
            release.validate_identity("v0.47.0", "a" * 39)

    def test_authoritative_controls_require_every_release_push_context(self) -> None:
        commit = "b" * 40
        release.verify_authoritative_controls(
            FakeReadClient(commit), "v0.47.0", commit
        )
        missing_android = tuple(
            context
            for context in release.REQUIRED_PUSH_CONTEXTS
            if context != "Validate / android (push)"
        )
        with self.assertRaisesRegex(release.ReleaseError, "android"):
            release.verify_authoritative_controls(
                FakeReadClient(commit, contexts=missing_android),
                "v0.47.0",
                commit,
            )

    def test_release_tag_must_match_one_owner_only_protection(self) -> None:
        commit = "c" * 40
        client = FakeReadClient(commit)
        client.responses[release.api_path("/tag_protections")][0][
            "whitelist_usernames"
        ] = []
        with self.assertRaisesRegex(release.ReleaseError, "repository owner"):
            release.verify_authoritative_controls(client, "v0.47.0", commit)

    def test_promotion_is_idempotent_only_for_the_exact_existing_tag_and_draft(self) -> None:
        commit = "d" * 40
        tag = "v0.47.0"
        notes = "Reviewed notes"
        existing = {
            "id": 47,
            "tag_name": tag,
            "target_commitish": commit,
            "name": f"FlexDisplay {tag}",
            "body": notes,
            "draft": True,
            "prerelease": False,
        }

        class Client:
            def get(self, path: str):
                if path.endswith(f"/releases/tags/{tag}"):
                    return existing
                if path.endswith(f"/tags/{tag}"):
                    return {"commit": {"sha": commit}}
                raise AssertionError(path)

            def post(self, path: str, payload: dict[str, object]):
                raise AssertionError("exact existing promotion must not mutate")

        with (
            mock.patch.object(release, "verify_local_release", return_value=("0.47.0", notes)),
            mock.patch.object(release, "verify_authoritative_controls"),
            mock.patch.object(release, "fetch_tag"),
        ):
            self.assertEqual(
                release.promote(
                    Client(), tag, commit, f"promote-{tag}-at-{commit}"
                ),
                47,
            )

    def test_publication_downloads_exact_draft_attachment_uuids(self) -> None:
        tag = "v0.47.1"
        commit = "e" * 40
        contract = json.loads(
            (ROOT / "rook_receiver/release/companion-release.json").read_text(
                encoding="utf-8"
            )
        )
        basename = contract["artifact_basename"]
        apk_name = f"{basename}.apk"
        checksum_name = f"{basename}.apk.sha256"
        metadata_name = f"{basename}.metadata.json"
        apk = b"signed companion apk"
        apk_sha = hashlib.sha256(apk).hexdigest()
        signer = (
            ROOT / "rook_receiver/release/companion-release-cert.sha256"
        ).read_text(encoding="utf-8").strip()
        payloads = {
            apk_name: apk,
            checksum_name: f"{apk_sha}  {apk_name}\n".encode(),
            metadata_name: json.dumps(
                {
                    "schema_version": 1,
                    "artifact": apk_name,
                    "application_id": contract["application_id"],
                    "version_name": contract["version_name"],
                    "version_code": contract["version_code"],
                    "source_tag": tag,
                    "source_commit": commit,
                    "apk_sha256": apk_sha,
                    "signer_sha256": signer,
                }
            ).encode(),
        }
        uuids = {
            apk_name: "11111111-1111-4111-8111-111111111111",
            checksum_name: "22222222-2222-4222-8222-222222222222",
            metadata_name: "33333333-3333-4333-8333-333333333333",
        }
        assets = [
            {
                "id": index,
                "uuid": uuids[name],
                "name": name,
                "size": len(payload),
                "type": "attachment",
                "browser_download_url": "https://invalid.example/draft-route",
            }
            for index, (name, payload) in enumerate(payloads.items(), start=1)
        ]
        client = mock.Mock()
        client.download_attachment.side_effect = {
            uuid: payloads[name] for name, uuid in uuids.items()
        }.get
        with mock.patch.object(release, "companion_changed", return_value=True):
            release.verify_companion_assets(
                client, {"assets": assets}, tag, commit
            )
        self.assertEqual(
            {call.args[0] for call in client.download_attachment.call_args_list},
            set(uuids.values()),
        )

    def test_publication_rejects_browser_only_draft_assets(self) -> None:
        contract = json.loads(
            (ROOT / "rook_receiver/release/companion-release.json").read_text(
                encoding="utf-8"
            )
        )
        basename = contract["artifact_basename"]
        assets = [
            {
                "id": index,
                "name": name,
                "size": 1,
                "type": "attachment",
                "browser_download_url": "https://invalid.example/draft-route",
            }
            for index, name in enumerate(
                (
                    f"{basename}.apk",
                    f"{basename}.apk.sha256",
                    f"{basename}.metadata.json",
                ),
                start=1,
            )
        ]
        with (
            mock.patch.object(release, "companion_changed", return_value=True),
            self.assertRaisesRegex(release.ReleaseError, "immutable attachment UUID"),
        ):
            release.verify_companion_assets(
                mock.Mock(), {"assets": assets}, "v0.47.1", "f" * 40
            )


class ReleaseWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.promote = PROMOTE_WORKFLOW.read_text(encoding="utf-8")
        self.publish = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        self.script = SCRIPT.read_text(encoding="utf-8")

    def test_workflows_are_separate_manual_owner_only_trusted_runner_stages(self) -> None:
        for workflow in (self.promote, self.publish):
            self.assertIn("workflow_dispatch:", workflow)
            self.assertIn("forgejo.actor == forgejo.repository_owner", workflow)
            self.assertIn("runs-on: trusted-release", workflow)
            self.assertIn("enable-openid-connect: true", workflow)
            self.assertNotIn("secrets.FORGEJO_RELEASE_TOKEN", workflow)
        self.assertIn("forgejo_release.py promote", self.promote)
        self.assertIn("forgejo_release.py publish", self.publish)

    def test_authority_is_short_lived_and_scoped_to_each_exact_workflow(self) -> None:
        self.assertIn("FLEXDISPLAY_RELEASE_PROMOTION_AUDIENCE", self.promote)
        self.assertNotIn("FLEXDISPLAY_RELEASE_PUBLICATION_AUDIENCE", self.promote)
        self.assertIn("FLEXDISPLAY_RELEASE_PUBLICATION_AUDIENCE", self.publish)
        self.assertNotIn("FLEXDISPLAY_RELEASE_PROMOTION_AUDIENCE", self.publish)
        for workflow in (self.promote, self.publish):
            self.assertIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", workflow)
            self.assertIn("Remove one-run", workflow)
            self.assertIn("chmod 600", workflow)

    def test_trusted_release_checkout_avoids_node_and_is_exact(self) -> None:
        for workflow in (self.promote, self.publish):
            uses = [
                line.split("@", 1)[1]
                for line in workflow.splitlines()
                if line.strip().startswith("uses:")
            ]
            self.assertEqual(uses, [])
            self.assertIn("git init .", workflow)
            self.assertIn("GIT_CONFIG_GLOBAL: /dev/null", workflow)
            self.assertIn("GIT_CONFIG_SYSTEM: /dev/null", workflow)
            self.assertIn("-c credential.helper= -c http.followRedirects=false", workflow)
            self.assertIn("+refs/heads/main:refs/remotes/origin/main", workflow)
            self.assertIn('"+refs/tags/v*:refs/tags/v*"', workflow)
            self.assertNotIn("--depth=1", workflow)
            self.assertIn('test -z "$FORGEJO_TOKEN$GITHUB_TOKEN"', workflow)
            self.assertIn(
                'test "$FORGEJO_REPOSITORY" = '
                '"clintonmarshall/xteink-flexdisplay-ha"',
                workflow,
            )
        self.assertIn(
            'test "$(git rev-parse refs/remotes/origin/main)" = '
            '"$RELEASE_COMMIT"',
            self.promote,
        )
        self.assertNotIn("refs/tags/$RELEASE_TAG", self.promote)
        self.assertIn(
            'test "$(git cat-file -t "refs/tags/$RELEASE_TAG")" = tag',
            self.publish,
        )

    def test_publication_rechecks_tag_assets_and_separate_confirmation(self) -> None:
        self.assertIn("draft_release_id:", self.publish)
        self.assertIn("publish-$RELEASE_TAG-at-$RELEASE_COMMIT", self.publish)
        self.assertIn("verify_companion_assets", self.script)
        self.assertIn("source_commit", self.script)
        self.assertIn("apk_sha256", self.script)
        self.assertIn('"draft": False', self.script)


if __name__ == "__main__":
    unittest.main()
