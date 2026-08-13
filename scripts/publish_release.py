#!/usr/bin/env python3
"""Promote and publish one verified software-only release in Forgejo."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEMVER_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class ForgejoClient:
    def __init__(self, origin: str, repository: str, token: str) -> None:
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Forgejo origin must be an absolute HTTP(S) origin")
        if (
            parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Forgejo origin must not contain credentials, path, query, or fragment"
            )
        if re.search(r"[\r\n]", token) or not token:
            raise ValueError("Forgejo token is absent or unsafe for an HTTP header")
        owner_repo = repository.split("/")
        if len(owner_repo) != 2 or not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", item) for item in owner_repo
        ):
            raise ValueError("Forgejo repository must be OWNER/REPO")
        self.origin = origin.rstrip("/")
        self.repository = repository
        self.token = token
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.origin}/api/v1{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "flexdisplay-release-publisher/1",
            },
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"Forgejo API {method} {path} failed with HTTP {error.code}"
            ) from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"Forgejo API {method} {path} failed") from error

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)


def run(*args: str) -> str:
    return subprocess.run(
        list(args), cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def verify_local(tag: str, commit_sha: str) -> str:
    if not SEMVER_TAG.fullmatch(tag) or not SHA.fullmatch(commit_sha):
        raise ValueError("tag or full commit SHA is invalid")
    if run("git", "rev-parse", "HEAD") != commit_sha:
        raise ValueError("checked-out HEAD does not match the requested commit")
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked files in the release checkout are not clean")
    version = run(
        sys.executable, "scripts/check_release_metadata.py", "--print-version"
    )
    if tag != f"v{version}":
        raise ValueError("release tag does not match synchronized platform version")
    run(sys.executable, "scripts/check_release_metadata.py", "--release", version)
    run(
        sys.executable,
        "scripts/check_firmware_release.py",
        "--classification",
        "software-only",
    )
    return version


def tag_sha(payload: dict[str, Any]) -> str:
    commit = payload.get("commit") or {}
    value = commit.get("sha") or commit.get("id") or payload.get("target")
    return str(value or "")


def verify_remote(client: ForgejoClient, commit_sha: str, tag: str) -> None:
    repository = client.get(f"/repos/{client.repository}")
    if repository.get("default_branch") != "main":
        raise ValueError("Forgejo default branch is not main")
    branch = client.get(f"/repos/{client.repository}/branches/main")
    if not branch.get("protected"):
        raise ValueError("Forgejo main is not protected")
    branch_commit = branch.get("commit") or {}
    branch_sha = branch_commit.get("sha") or branch_commit.get("id")
    if branch_sha != commit_sha:
        raise ValueError("requested release commit is not the current Forgejo main")
    status = client.get(f"/repos/{client.repository}/commits/{commit_sha}/status")
    if status.get("state") != "success":
        raise ValueError(
            "authoritative Forgejo status is not successful for the commit"
        )
    protections = client.get(f"/repos/{client.repository}/tag_protections")
    patterns = [
        str(item.get("name_pattern") or item.get("pattern") or "")
        for item in protections
    ]
    if not any(pattern and fnmatch.fnmatchcase(tag, pattern) for pattern in patterns):
        raise ValueError(f"no Forgejo tag-protection rule matches {tag}")


def get_optional(client: ForgejoClient, path: str) -> Any | None:
    try:
        return client.get(path)
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return None
        raise


def promote(
    client: ForgejoClient, tag: str, commit_sha: str, confirmation: str
) -> None:
    expected = f"promote {tag} at {commit_sha}"
    if confirmation != expected:
        raise ValueError(f"promotion confirmation must exactly equal: {expected}")
    existing = get_optional(client, f"/repos/{client.repository}/tags/{tag}")
    if existing is not None:
        if tag_sha(existing) != commit_sha:
            raise ValueError("existing Forgejo tag resolves to a different commit")
        print(f"Forgejo tag {tag} already resolves to {commit_sha}.")
        return
    client.post(
        f"/repos/{client.repository}/tags",
        {
            "tag_name": tag,
            "target": commit_sha,
            "message": f"FlexDisplay {tag}",
        },
    )
    created = client.get(f"/repos/{client.repository}/tags/{tag}")
    if tag_sha(created) != commit_sha:
        raise RuntimeError(
            "created Forgejo tag did not resolve to the requested commit"
        )
    print(f"Promoted {tag} at {commit_sha} in Forgejo.")


def publish(
    client: ForgejoClient,
    tag: str,
    commit_sha: str,
    version: str,
    confirmation: str,
) -> None:
    expected = f"publish {tag} at {commit_sha}"
    if confirmation != expected:
        raise ValueError(f"publication confirmation must exactly equal: {expected}")
    remote_tag = client.get(f"/repos/{client.repository}/tags/{tag}")
    if tag_sha(remote_tag) != commit_sha:
        raise ValueError("Forgejo tag does not resolve to the requested commit")
    existing = get_optional(client, f"/repos/{client.repository}/releases/tags/{tag}")
    if existing is not None:
        if tag_sha(existing) and tag_sha(existing) != commit_sha:
            raise ValueError("existing Forgejo release resolves to a different commit")
        print(f"Forgejo release {tag} already exists.")
        return
    notes = run(sys.executable, "scripts/release_notes.py", version)
    client.post(
        f"/repos/{client.repository}/releases",
        {
            "tag_name": tag,
            "target_commitish": commit_sha,
            "name": f"FlexDisplay {tag}",
            "body": notes,
            "draft": False,
            "prerelease": False,
        },
    )
    if get_optional(client, f"/repos/{client.repository}/releases/tags/{tag}") is None:
        raise RuntimeError("Forgejo release could not be verified after creation")
    print(f"Published Forgejo release {tag} at {commit_sha}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("dry-run", "tag-only", "release-only"), required=True
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--classification", choices=("software-only",), required=True)
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--origin", default=os.environ.get("FORGEJO_ORIGIN", ""))
    parser.add_argument(
        "--repository", default=os.environ.get("FORGEJO_REPOSITORY", "")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        version = verify_local(args.tag, args.commit_sha)
        token = os.environ.get("FORGEJO_TOKEN", "")
        client = ForgejoClient(args.origin, args.repository, token)
        verify_remote(client, args.commit_sha, args.tag)
        if args.mode == "tag-only":
            promote(client, args.tag, args.commit_sha, args.confirmation)
        elif args.mode == "release-only":
            publish(client, args.tag, args.commit_sha, version, args.confirmation)
        else:
            print(
                f"Dry run passed for {args.tag} at {args.commit_sha}; no tag or release was created."
            )
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Release publication blocked: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
