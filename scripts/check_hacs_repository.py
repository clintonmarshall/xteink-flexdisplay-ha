#!/usr/bin/env python3
"""Validate stable public-repository properties required by HACS.

The exact candidate's source tree is checked by ``check_hacs_local.py``. These
repository-level properties are read anonymously from the approved public
GitHub distribution mirror because they cannot differ by Forgejo branch.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import ssl
import sys


API_HOST = "api.github.com"
REPOSITORY = "clintonmarshall/xteink-flexdisplay-ha"
POPULAR_OSI_LICENSES = {
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CDDL-1.0",
    "EPL-2.0",
    "GPL-2.0",
    "GPL-3.0",
    "LGPL-2.1",
    "LGPL-3.0",
    "MIT",
    "MPL-2.0",
}


def fetch_repository() -> dict[str, object]:
    """Read the one fixed public repository without credentials or redirects."""

    tls_context = ssl.create_default_context()
    if ssl.get_default_verify_paths().cafile is None:
        for certificate_file in (
            Path("/etc/ssl/cert.pem"),
            Path("/etc/ssl/certs/ca-certificates.crt"),
        ):
            if certificate_file.is_file():
                tls_context.load_verify_locations(cafile=certificate_file)
                break
    connection = http.client.HTTPSConnection(
        API_HOST,
        context=tls_context,
        timeout=30,
    )
    connection.request(
        "GET",
        f"/repos/{REPOSITORY}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "FlexDisplay-HACS-validator",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    if response.status != 200:
        raise RuntimeError(f"GitHub repository API returned HTTP {response.status}")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise RuntimeError("GitHub repository API did not return an object")
    return result


def repository_errors(repository: dict[str, object]) -> list[str]:
    """Return HACS repository-property failures for an API response."""

    errors: list[str] = []
    if repository.get("full_name") != REPOSITORY:
        errors.append("GitHub returned an unexpected repository identity")
    if repository.get("private") is not False:
        errors.append("the GitHub HACS distribution repository is not public")
    if repository.get("archived") is not False or repository.get("disabled") is not False:
        errors.append("the GitHub HACS distribution repository is archived or disabled")
    if not repository.get("description"):
        errors.append("the GitHub HACS distribution repository has no description")
    if repository.get("has_issues") is not True:
        errors.append("the GitHub HACS distribution repository has issues disabled")
    if not repository.get("topics"):
        errors.append("the GitHub HACS distribution repository has no topics")

    license_info = repository.get("license")
    spdx_id = license_info.get("spdx_id") if isinstance(license_info, dict) else None
    if spdx_id not in POPULAR_OSI_LICENSES:
        errors.append(f"the detected license is not an accepted OSI license: {spdx_id!r}")
    return errors


def main() -> int:
    try:
        repository = fetch_repository()
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        print(f"HACS repository metadata validation failed: {error}", file=sys.stderr)
        return 1

    errors = repository_errors(repository)
    if errors:
        print("HACS repository metadata validation failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1

    print(f"HACS public repository metadata validation passed for {REPOSITORY}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
