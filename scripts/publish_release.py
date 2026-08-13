#!/usr/bin/env python3
"""Promote and publish one verified software-only FlexDisplay release."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORGEJO_ORIGIN = "http://10.200.40.231:3000"
FORGEJO_REPOSITORY = "clintonmarshall/xteink-flexdisplay-ha"
TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
TOKEN_ENV_NAMES = {"FORGEJO_RELEASE_TOKEN", "GITHUB_RELEASE_DISPATCH_TOKEN"}


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int) -> None:
        super().__init__(f"Forgejo API {method} {path} failed with HTTP {status}")
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, request: Any, response: Any, code: int, message: str, headers: Any, newurl: str
    ) -> None:
        return None


class ForgejoClient:
    def __init__(self, token: str) -> None:
        if not token or re.search(r"[\r\n]", token):
            raise ValueError("FORGEJO_RELEASE_TOKEN is absent or unsafe")
        self.token = token
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        if not path.startswith("/api/v1/") or re.search(r"[\r\n]", path):
            raise ValueError("internal API path is invalid")
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{FORGEJO_ORIGIN}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "flexdisplay-trusted-release/1",
            },
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            raise ApiError(method, path, error.code) from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"Forgejo API {method} {path} failed") from error

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload)


def child_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in TOKEN_ENV_NAMES}


def run(*args: str) -> str:
    return subprocess.run(
        list(args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=child_environment(),
    ).stdout.strip()


def api_path(suffix: str) -> str:
    return f"/api/v1/repos/{FORGEJO_REPOSITORY}{suffix}"


def get_optional(client: ForgejoClient, path: str) -> Any | None:
    try:
        return client.get(path)
    except ApiError as error:
        if error.status == 404:
            return None
        raise


def tag_commit(payload: dict[str, Any]) -> str:
    commit = payload.get("commit") or {}
    return str(commit.get("sha") or commit.get("id") or payload.get("target") or "")


def previous_tag(current_tag: str) -> str:
    for tag in run("git", "tag", "--merged", "HEAD", "--sort=-version:refname", "v*").splitlines():
        if tag != current_tag:
            return tag
    raise ValueError("no preceding merged release tag was found")


def prior_release_snapshot(client: ForgejoClient, current_tag: str) -> tuple[Any, ...]:
    prior = previous_tag(current_tag)
    tag = client.get(api_path(f"/tags/{urllib.parse.quote(prior, safe='')}"))
    release = client.get(api_path(f"/releases/tags/{urllib.parse.quote(prior, safe='')}"))
    return (
        prior,
        tag_commit(tag),
        release.get("id"),
        bool(release.get("draft")),
        bool(release.get("prerelease")),
        len(release.get("assets") or []),
    )


def verify_local(tag: str, commit_sha: str) -> str:
    if not TAG_PATTERN.fullmatch(tag) or not SHA_PATTERN.fullmatch(commit_sha):
        raise ValueError("tag or full commit SHA is invalid")
    if run("git", "rev-parse", "HEAD") != commit_sha:
        raise ValueError("checked-out HEAD does not match the requested commit")
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked files in the release checkout are not clean")
    version = run(sys.executable, "scripts/check_release_metadata.py", "--print-version")
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


def verify_authoritative_main(client: ForgejoClient, commit_sha: str, tag: str) -> None:
    repository = client.get(api_path(""))
    if repository.get("default_branch") != "main":
        raise ValueError("Forgejo default branch is not main")
    branch = client.get(api_path("/branches/main"))
    if not branch.get("protected"):
        raise ValueError("Forgejo main is not protected")
    branch_sha = str((branch.get("commit") or {}).get("id") or (branch.get("commit") or {}).get("sha") or "")
    if branch_sha != commit_sha:
        raise ValueError("Forgejo main does not equal the requested commit")

    status = client.get(api_path(f"/commits/{commit_sha}/status"))
    if status.get("state") != "success":
        raise ValueError("combined Forgejo commit status is not successful")
    contexts = {
        str(item.get("context")): str(item.get("status"))
        for item in status.get("statuses") or []
    }
    if contexts.get("Validate / bridge (push)") != "success":
        raise ValueError("required Forgejo push context is not successful")

    runs = client.get(api_path("/actions/runs?limit=50"))
    matching = [
        run
        for run in runs.get("workflow_runs") or []
        if run.get("commit_sha") == commit_sha
        and run.get("event") == "push"
        and run.get("prettyref") == "main"
        and run.get("workflow_id") == "validate.yml"
    ]
    if not matching or matching[0].get("status") != "success":
        raise ValueError("exact post-merge main validation run is not successful")

    protections = client.get(api_path("/tag_protections"))
    patterns = [
        str(item.get("name_pattern") or item.get("pattern") or "")
        for item in protections
    ]
    if not any(pattern and fnmatch.fnmatchcase(tag, pattern) for pattern in patterns):
        raise ValueError(f"no protected-tag rule matches {tag}")


def wait_for_tag(client: ForgejoClient, tag: str, commit_sha: str) -> dict[str, Any]:
    path = api_path(f"/tags/{urllib.parse.quote(tag, safe='')}")
    for _ in range(12):
        payload = get_optional(client, path)
        if payload is not None and tag_commit(payload) == commit_sha:
            return payload
        time.sleep(1)
    raise RuntimeError("Forgejo did not expose the exact pushed tag in time")


def promote_tag(client: ForgejoClient, tag: str, commit_sha: str, confirmation: str) -> None:
    expected = f"promote {tag} at {commit_sha}"
    if confirmation != expected:
        raise ValueError(f"promotion confirmation must exactly equal: {expected}")
    quoted = urllib.parse.quote(tag, safe="")
    if get_optional(client, api_path(f"/tags/{quoted}")) is not None:
        raise ValueError("release tag already exists; retagging is forbidden")
    if get_optional(client, api_path(f"/releases/tags/{quoted}")) is not None:
        raise ValueError("release object already exists unexpectedly")
    prior = prior_release_snapshot(client, tag)
    client.post(
        api_path("/tags"),
        {
            "tag_name": tag,
            "target": commit_sha,
            "message": f"FlexDisplay {tag}",
        },
    )
    wait_for_tag(client, tag, commit_sha)
    run("git", "fetch", "--force", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    if run("git", "cat-file", "-t", f"refs/tags/{tag}") != "tag":
        raise RuntimeError("Forgejo release tag is not annotated")
    if run("git", "rev-list", "-n", "1", tag) != commit_sha:
        raise RuntimeError("fetched Forgejo tag does not peel to the requested commit")
    if prior_release_snapshot(client, tag) != prior:
        raise RuntimeError("the preceding published release changed during tag promotion")
    print(f"Promoted protected annotated tag {tag} at {commit_sha} in Forgejo.")


def publish_release(
    client: ForgejoClient,
    tag: str,
    commit_sha: str,
    version: str,
    confirmation: str,
) -> None:
    expected = f"publish {tag} at {commit_sha}"
    if confirmation != expected:
        raise ValueError(f"publication confirmation must exactly equal: {expected}")
    quoted = urllib.parse.quote(tag, safe="")
    remote_tag = client.get(api_path(f"/tags/{quoted}"))
    if tag_commit(remote_tag) != commit_sha:
        raise ValueError("Forgejo tag does not resolve to the requested commit")
    if get_optional(client, api_path(f"/releases/tags/{quoted}")) is not None:
        raise ValueError("Forgejo release already exists; replacement is forbidden")
    prior = prior_release_snapshot(client, tag)
    notes = run(sys.executable, "scripts/release_notes.py", version)
    client.post(
        api_path("/releases"),
        {
            "tag_name": tag,
            "target_commitish": commit_sha,
            "name": f"FlexDisplay {tag}",
            "body": notes,
            "draft": False,
            "prerelease": False,
        },
    )
    release = client.get(api_path(f"/releases/tags/{quoted}"))
    if release.get("draft") or release.get("prerelease") or release.get("body", "").strip() != notes:
        raise RuntimeError("published Forgejo release did not match canonical metadata")
    if prior_release_snapshot(client, tag) != prior:
        raise RuntimeError("the preceding published release changed during publication")
    print(f"Published authoritative Forgejo release {tag} at {commit_sha}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "tag-only", "release-only"), required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--classification", choices=("software-only",), required=True)
    parser.add_argument("--confirmation", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        version = verify_local(args.tag, args.commit_sha)
        client = ForgejoClient(os.environ.get("FORGEJO_RELEASE_TOKEN", ""))
        verify_authoritative_main(client, args.commit_sha, args.tag)
        if args.mode == "tag-only":
            promote_tag(client, args.tag, args.commit_sha, args.confirmation)
        elif args.mode == "release-only":
            publish_release(client, args.tag, args.commit_sha, version, args.confirmation)
        else:
            quoted = urllib.parse.quote(args.tag, safe="")
            if get_optional(client, api_path(f"/tags/{quoted}")) is not None:
                raise ValueError("release tag already exists")
            if get_optional(client, api_path(f"/releases/tags/{quoted}")) is not None:
                raise ValueError("release object already exists")
            prior_release_snapshot(client, args.tag)
            print(f"Dry run passed for {args.tag} at {args.commit_sha}; no mutation occurred.")
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Release publication blocked: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
