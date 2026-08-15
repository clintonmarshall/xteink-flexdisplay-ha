#!/usr/bin/env python3
"""Promote and publish one verified FlexDisplay release on Forgejo Actions.

This program is intentionally fixed to the FlexDisplay Platform repository.  It
accepts only a short-lived Forgejo Authorized Integration JWT stored in an
owner-only file by a reviewed workflow running on the protected release runner.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "clintonmarshall/xteink-flexdisplay-ha"
REPOSITORY_OWNER = "clintonmarshall"
TAG_PATTERN = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PUSH_CONTEXTS = (
    "Validate / bridge (push)",
    "Validate / app (push)",
    "Validate / integration (push)",
    "Validate / android (push)",
    "Validate / required (push)",
)


class ReleaseError(RuntimeError):
    """A fail-closed release-contract violation."""


class ApiError(ReleaseError):
    def __init__(self, method: str, path: str, status: int) -> None:
        super().__init__(f"Forgejo API {method} {path} failed with HTTP {status}")
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Any,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _normalise_api_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api/v1"
    ):
        raise ReleaseError("FORGEJO_API_URL is not an exact Forgejo /api/v1 origin")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def _read_token(path_value: str) -> str:
    path = Path(path_value)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ReleaseError("release JWT file is accessible outside its owner")
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ReleaseError("release JWT file is unavailable") from error
    if not token or re.search(r"[\r\n]", token):
        raise ReleaseError("release JWT is absent or malformed")
    return token


class ForgejoClient:
    """Small, no-proxy, no-redirect client for reviewed repository routes."""

    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = _normalise_api_url(api_url)
        if not token or re.search(r"[\r\n]", token):
            raise ReleaseError("release JWT is absent or malformed")
        self.token = token
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not path.startswith(f"/repos/{REPOSITORY}") or re.search(r"[\r\n]", path):
            raise ReleaseError("refusing an API path outside the fixed repository")
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "flexdisplay-protected-release/1",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            raise ApiError(method, path, error.code) from None
        except urllib.error.URLError as error:
            raise ReleaseError(f"Forgejo API {method} {path} failed") from error

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("PATCH", path, payload)

    def download(self, url: str) -> bytes:
        expected = urllib.parse.urlsplit(self.api_url)
        candidate = urllib.parse.urlsplit(url)
        if (
            candidate.scheme != expected.scheme
            or candidate.netloc != expected.netloc
            or candidate.username
            or candidate.password
            or candidate.fragment
        ):
            raise ReleaseError("release asset URL left the authoritative Forgejo origin")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/octet-stream",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "flexdisplay-protected-release/1",
            },
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            raise ReleaseError("authoritative release asset download failed") from error


def api_path(suffix: str = "") -> str:
    return f"/repos/{REPOSITORY}{suffix}"


def run(*args: str) -> str:
    environment = os.environ.copy()
    for name in (
        "FLEXDISPLAY_RELEASE_TOKEN_FILE",
        "FORGEJO_TOKEN",
        "GITEA_TOKEN",
        "GITHUB_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    ):
        environment.pop(name, None)
    try:
        return subprocess.run(
            list(args),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseError(f"command failed: {' '.join(args)}") from error


def get_optional(client: ForgejoClient, path: str) -> Any | None:
    try:
        return client.get(path)
    except ApiError as error:
        if error.status == 404:
            return None
        raise


def validate_identity(tag: str, commit: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if not match or not SHA_PATTERN.fullmatch(commit):
        raise ReleaseError("release tag or full commit SHA is invalid")
    return tag.removeprefix("v")


def release_notes(version: str) -> str:
    changelog = (ROOT / "flexdisplay_bridge/CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^##\s+{re.escape(version)}\s*$\n(.*?)(?=^##\s+|\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    if not match or not match.group(1).strip():
        raise ReleaseError(f"changelog section {version!r} is absent or empty")
    return match.group(1).strip()


def verify_local_release(tag: str, commit: str, require_tag: bool) -> tuple[str, str]:
    version = validate_identity(tag, commit)
    if run("git", "rev-parse", "HEAD") != commit:
        raise ReleaseError("checked-out HEAD does not equal the requested commit")
    if run("git", "rev-parse", "origin/main") != commit:
        raise ReleaseError("origin/main does not equal the requested release commit")
    if run("git", "status", "--porcelain", "--untracked-files=all"):
        raise ReleaseError("release checkout is not clean")
    run(sys.executable, "scripts/check_release_metadata.py", "--release", version)
    run(sys.executable, "scripts/check_android_release_metadata.py", "--require-signer")
    tag_ref = f"refs/tags/{tag}"
    if require_tag:
        if run("git", "cat-file", "-t", tag_ref) != "tag":
            raise ReleaseError("release tag is not annotated")
        if run("git", "rev-list", "-n", "1", tag_ref) != commit:
            raise ReleaseError("release tag does not peel to the requested commit")
    return version, release_notes(version)


def tag_matches(pattern: str, tag: str) -> bool:
    if len(pattern) > 2 and pattern.startswith("/") and pattern.endswith("/"):
        try:
            return re.search(pattern[1:-1], tag) is not None
        except re.error as error:
            raise ReleaseError("Forgejo returned an invalid protected-tag regex") from error
    return fnmatch.fnmatchcase(tag, pattern)


def verify_authoritative_controls(
    client: ForgejoClient, tag: str, commit: str, require_release_actor: bool = True
) -> None:
    repository = client.get(api_path())
    if repository.get("full_name") != REPOSITORY or repository.get("default_branch") != "main":
        raise ReleaseError("Forgejo repository identity or default branch changed")
    branch = client.get(api_path("/branches/main"))
    branch_commit = branch.get("commit") or {}
    branch_sha = str(branch_commit.get("id") or branch_commit.get("sha") or "")
    if branch.get("protected") is not True or branch_sha != commit:
        raise ReleaseError("protected Forgejo main does not equal the requested commit")

    status = client.get(api_path(f"/commits/{commit}/status?limit=100"))
    if status.get("sha") != commit or status.get("state") != "success":
        raise ReleaseError("exact release commit does not have all-success Forgejo status")
    statuses = status.get("statuses")
    if not isinstance(statuses, list):
        raise ReleaseError("Forgejo status response contained no contexts")
    for context in REQUIRED_PUSH_CONTEXTS:
        matches = [
            item
            for item in statuses
            if item.get("context") == context
            and (item.get("status") or item.get("state")) == "success"
        ]
        if len(matches) != 1:
            raise ReleaseError(f"required exact-commit context is not green: {context}")

    protections = client.get(api_path("/tag_protections"))
    matching = [
        rule
        for rule in protections
        if tag_matches(str(rule.get("name_pattern") or ""), tag)
    ]
    if len(matching) != 1:
        raise ReleaseError("exactly one reviewed protected-tag rule must match the release tag")
    if require_release_actor:
        allowed = matching[0].get("whitelist_usernames") or []
        if allowed != [REPOSITORY_OWNER]:
            raise ReleaseError(
                "protected release tags must allow only the repository owner identity"
            )


def tag_commit(payload: dict[str, Any]) -> str:
    commit = payload.get("commit") or {}
    return str(commit.get("sha") or commit.get("id") or payload.get("target") or "")


def fetch_tag(tag: str, commit: str) -> None:
    run("git", "fetch", "--force", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    if run("git", "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise ReleaseError("created release tag is not annotated")
    if run("git", "rev-list", "-n", "1", f"refs/tags/{tag}") != commit:
        raise ReleaseError("created release tag does not resolve to the requested commit")


def promote(client: ForgejoClient, tag: str, commit: str, confirmation: str) -> int:
    version, notes = verify_local_release(tag, commit, require_tag=False)
    verify_authoritative_controls(client, tag, commit)
    expected = f"promote-{tag}-at-{commit}"
    if confirmation != expected:
        raise ReleaseError(f"promotion confirmation must exactly equal {expected}")
    quoted_tag = urllib.parse.quote(tag, safe="")
    existing_tag = get_optional(client, api_path(f"/tags/{quoted_tag}"))
    if existing_tag is None:
        created_tag = client.post(
            api_path("/tags"),
            {"tag_name": tag, "target": commit, "message": f"FlexDisplay {tag}"},
        )
        if tag_commit(created_tag) != commit:
            raise ReleaseError("Forgejo did not create the requested release tag")
    elif tag_commit(existing_tag) != commit:
        raise ReleaseError("existing release tag targets a different commit")
    fetch_tag(tag, commit)
    existing_release = get_optional(client, api_path(f"/releases/tags/{quoted_tag}"))
    if existing_release is not None:
        release_id = existing_release.get("id")
        if not isinstance(release_id, int):
            raise ReleaseError("existing draft release has no stable numeric identity")
        verify_release_record(existing_release, release_id, tag, commit, notes, draft=True)
        print(json.dumps({"tag": tag, "commit": commit, "draft_release_id": release_id}))
        return release_id
    draft = client.post(
        api_path("/releases"),
        {
            "tag_name": tag,
            "target_commitish": commit,
            "name": f"FlexDisplay {tag}",
            "body": notes,
            "draft": True,
            "prerelease": False,
        },
    )
    release_id = draft.get("id")
    if not isinstance(release_id, int) or draft.get("draft") is not True:
        raise ReleaseError("Forgejo did not create the expected draft release")
    verified = client.get(api_path(f"/releases/{release_id}"))
    verify_release_record(verified, release_id, tag, commit, notes, draft=True)
    print(json.dumps({"tag": tag, "commit": commit, "draft_release_id": release_id}))
    return release_id


def verify_release_record(
    release: dict[str, Any],
    release_id: int,
    tag: str,
    commit: str,
    notes: str,
    *,
    draft: bool,
) -> None:
    if (
        release.get("id") != release_id
        or release.get("tag_name") != tag
        or release.get("target_commitish") not in {commit, "main", tag}
        or release.get("name") != f"FlexDisplay {tag}"
        or str(release.get("body") or "").strip() != notes
        or release.get("draft") is not draft
        or release.get("prerelease") is not False
    ):
        raise ReleaseError("Forgejo release record does not match reviewed metadata")


def previous_release_tag(tag: str) -> str:
    tags = run("git", "tag", "--merged", "HEAD", "--list", "v*", "--sort=-version:refname")
    for candidate in tags.splitlines():
        if candidate != tag:
            return candidate
    raise ReleaseError("no preceding release tag was found")


def companion_changed(tag: str) -> bool:
    previous = previous_release_tag(tag)
    comparison = subprocess.run(
        ["git", "diff", "--quiet", previous, "HEAD", "--", "rook_receiver"],
        cwd=ROOT,
        check=False,
    )
    if comparison.returncode not in (0, 1):
        raise ReleaseError("could not classify Android receiver changes")
    return comparison.returncode == 1


def verify_companion_assets(
    client: ForgejoClient,
    release: dict[str, Any],
    tag: str,
    commit: str,
) -> None:
    assets = release.get("assets") or []
    if not isinstance(assets, list):
        raise ReleaseError("Forgejo release assets are malformed")
    if not companion_changed(tag):
        if assets:
            raise ReleaseError("release has unreviewed assets although Companion is unchanged")
        return

    contract = json.loads(
        (ROOT / "rook_receiver/release/companion-release.json").read_text(encoding="utf-8")
    )
    basename = str(contract["artifact_basename"])
    expected_names = {
        f"{basename}.apk",
        f"{basename}.apk.sha256",
        f"{basename}.metadata.json",
    }
    by_name = {str(asset.get("name")): asset for asset in assets}
    if len(by_name) != len(assets) or set(by_name) != expected_names:
        raise ReleaseError("Companion release assets are missing, duplicated, or unexpected")
    payloads: dict[str, bytes] = {}
    for name, asset in by_name.items():
        url = str(asset.get("browser_download_url") or "")
        if not url:
            raise ReleaseError(f"release asset {name} has no authoritative download URL")
        payloads[name] = client.download(url)

    apk_name = f"{basename}.apk"
    apk_sha = hashlib.sha256(payloads[apk_name]).hexdigest()
    checksum = payloads[f"{basename}.apk.sha256"].decode("utf-8").strip()
    if checksum != f"{apk_sha}  {apk_name}":
        raise ReleaseError("Companion checksum sidecar does not match the immutable APK")
    metadata = json.loads(payloads[f"{basename}.metadata.json"])
    signer = (
        ROOT / "rook_receiver/release/companion-release-cert.sha256"
    ).read_text(encoding="utf-8").strip()
    expected_metadata = {
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
    if metadata != expected_metadata:
        raise ReleaseError("Companion metadata does not match the reviewed release contract")


def publish(
    client: ForgejoClient,
    tag: str,
    commit: str,
    release_id: int,
    confirmation: str,
) -> None:
    _, notes = verify_local_release(tag, commit, require_tag=True)
    verify_authoritative_controls(client, tag, commit)
    expected = f"publish-{tag}-at-{commit}"
    if confirmation != expected:
        raise ReleaseError(f"publication confirmation must exactly equal {expected}")
    quoted_tag = urllib.parse.quote(tag, safe="")
    remote_tag = client.get(api_path(f"/tags/{quoted_tag}"))
    if tag_commit(remote_tag) != commit:
        raise ReleaseError("authoritative tag no longer resolves to the requested commit")
    draft = client.get(api_path(f"/releases/{release_id}"))
    verify_release_record(draft, release_id, tag, commit, notes, draft=True)
    verify_companion_assets(client, draft, tag, commit)

    client.patch(
        api_path(f"/releases/{release_id}"),
        {
            "tag_name": tag,
            "target_commitish": commit,
            "name": f"FlexDisplay {tag}",
            "body": notes,
            "draft": False,
            "prerelease": False,
        },
    )
    published = client.get(api_path(f"/releases/{release_id}"))
    verify_release_record(published, release_id, tag, commit, notes, draft=False)
    verify_companion_assets(client, published, tag, commit)
    print(json.dumps({"tag": tag, "commit": commit, "release_id": release_id, "published": True}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("promote", "publish"):
        child = subparsers.add_parser(command)
        child.add_argument("--tag", required=True)
        child.add_argument("--commit", required=True)
        child.add_argument("--confirmation", required=True)
        child.add_argument("--token-file", required=True)
        child.add_argument("--api-url", required=True)
    subparsers.choices["publish"].add_argument("--release-id", required=True, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if os.environ.get("FORGEJO_REPOSITORY") != REPOSITORY:
            raise ReleaseError("workflow repository identity is not FlexDisplay Platform")
        token = _read_token(args.token_file)
        client = ForgejoClient(args.api_url, token)
        if args.command == "promote":
            promote(client, args.tag, args.commit, args.confirmation)
        else:
            publish(
                client,
                args.tag,
                args.commit,
                args.release_id,
                args.confirmation,
            )
    except (KeyError, ValueError, json.JSONDecodeError, ReleaseError) as error:
        print(f"Release operation blocked: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
