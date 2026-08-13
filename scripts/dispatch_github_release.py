#!/usr/bin/env python3
"""Dispatch the fixed GitHub compatibility-release workflow after Forgejo."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

GITHUB_REPOSITORY = "clintonmarshall/xteink-flexdisplay-ha"
WORKFLOW = "release.yml"
TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def request(
    method: str, path: str, token: str, payload: dict[str, Any] | None = None
) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "flexdisplay-downstream-dispatch/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=20) as response:
            data = response.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"GitHub API {method} {path} failed with HTTP {error.code}"
        ) from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"GitHub API {method} {path} failed") from error


def referenced_commit(ref: dict[str, Any], token: str) -> str:
    target = ref.get("object") or {}
    if target.get("type") == "commit":
        return str(target.get("sha") or "")
    if target.get("type") == "tag" and target.get("sha"):
        annotated = request("GET", f"/git/tags/{target['sha']}", token)
        peeled = annotated.get("object") or {}
        if peeled.get("type") == "commit":
            return str(peeled.get("sha") or "")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--attempts", type=int, default=12)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_RELEASE_DISPATCH_TOKEN", "")
    if not TAG.fullmatch(args.tag) or not SHA.fullmatch(args.commit_sha):
        raise SystemExit("invalid tag or full commit SHA")
    if not token or re.search(r"[\r\n]", token):
        raise SystemExit("GITHUB_RELEASE_DISPATCH_TOKEN is absent or unsafe")

    mirrored = False
    for attempt in range(max(args.attempts, 1)):
        try:
            ref = request("GET", f"/git/ref/tags/{args.tag}", token)
            mirrored = referenced_commit(ref, token) == args.commit_sha
        except RuntimeError as error:
            if "HTTP 404" not in str(error):
                raise
        if mirrored:
            break
        if attempt + 1 < args.attempts:
            time.sleep(10)
    if not mirrored:
        raise SystemExit(
            "GitHub mirror did not expose the exact Forgejo tag commit in time"
        )

    request(
        "POST",
        f"/actions/workflows/{WORKFLOW}/dispatches",
        token,
        {"ref": args.tag, "inputs": {"tag": args.tag, "commit_sha": args.commit_sha}},
    )
    print(f"Dispatched downstream GitHub release for {args.tag} at {args.commit_sha}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
