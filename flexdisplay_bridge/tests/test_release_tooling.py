from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_notes_are_canonical_and_bounded() -> None:
    release_notes = load_script("release_notes")
    notes = release_notes.release_notes("0.46.0")
    assert "Bridge & Connections" in notes
    assert "## 0.45.1" not in notes


def test_firmware_config_parser_requires_every_release_field() -> None:
    firmware = load_script("check_firmware_release")
    values = firmware.config_values(
        "\n".join(
            f"{key}: value-{index}" for index, key in enumerate(firmware.CONFIG_KEYS)
        )
    )
    assert set(values) == set(firmware.CONFIG_KEYS)
    with pytest.raises(ValueError, match="absent"):
        firmware.config_values("firmware_version: 1")


def test_forgejo_client_rejects_credential_bearing_origins_and_unsafe_tokens() -> None:
    publisher = load_script("publish_release")
    with pytest.raises(ValueError, match="credentials"):
        publisher.ForgejoClient(
            "https://user:secret@forgejo.example", "owner/repository", "token"
        )
    with pytest.raises(ValueError, match="unsafe"):
        publisher.ForgejoClient(
            "https://forgejo.example", "owner/repository", "bad\ntoken"
        )
    with pytest.raises(ValueError, match="path"):
        publisher.ForgejoClient(
            "https://forgejo.example/other", "owner/repository", "token"
        )


def test_forgejo_tag_sha_accepts_api_sha_and_id_shapes() -> None:
    publisher = load_script("publish_release")
    assert publisher.tag_sha({"commit": {"sha": "a" * 40}}) == "a" * 40
    assert publisher.tag_sha({"commit": {"id": "b" * 40}}) == "b" * 40


def test_forgejo_api_error_does_not_reflect_remote_body_or_reason() -> None:
    publisher = load_script("publish_release")
    client = publisher.ForgejoClient(
        "https://forgejo.example", "owner/repository", "token"
    )
    sentinel = "LEAK-SENTINEL-CREDENTIAL"

    class FailingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                500,
                sentinel,
                {},
                io.BytesIO(f'{{"error":"{sentinel}"}}'.encode()),
            )

    client.opener = FailingOpener()
    with pytest.raises(RuntimeError) as failure:
        client.get("/repos/owner/repository")
    assert sentinel not in str(failure.value)


def test_forgejo_remote_verification_requires_current_protected_main() -> None:
    publisher = load_script("publish_release")
    commit_sha = "a" * 40

    class FakeClient:
        repository = "owner/repository"

        def get(self, path: str):
            responses = {
                "/repos/owner/repository": {"default_branch": "main"},
                "/repos/owner/repository/branches/main": {
                    "protected": True,
                    "commit": {"id": commit_sha},
                },
                f"/repos/owner/repository/commits/{commit_sha}/status": {
                    "state": "success"
                },
                "/repos/owner/repository/tag_protections": [{"name_pattern": "v*"}],
            }
            return responses[path]

    publisher.verify_remote(FakeClient(), commit_sha, "v1.2.3")

    class StaleMainClient(FakeClient):
        def get(self, path: str):
            payload = super().get(path)
            if path.endswith("/branches/main"):
                return {"protected": True, "commit": {"id": "b" * 40}}
            return payload

    with pytest.raises(ValueError, match="current Forgejo main"):
        publisher.verify_remote(StaleMainClient(), commit_sha, "v1.2.3")


def test_github_annotated_tag_is_peeled_to_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = load_script("dispatch_github_release")

    def fake_request(method: str, path: str, token: str, payload=None):
        assert method == "GET"
        assert path == f"/git/tags/{'a' * 40}"
        assert token == "token"
        return {"object": {"type": "commit", "sha": "b" * 40}}

    monkeypatch.setattr(dispatcher, "request", fake_request)
    ref = {"object": {"type": "tag", "sha": "a" * 40}}
    assert dispatcher.referenced_commit(ref, "token") == "b" * 40
