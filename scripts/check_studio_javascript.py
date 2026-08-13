#!/usr/bin/env python3
"""Syntax-check every inline script in Dashboard Studio with Node.js."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "flexdisplay_bridge/flexdisplay_bridge/static/dashboard-studio.html"


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("Node.js is required to validate Dashboard Studio", file=sys.stderr)
        return 1
    scripts = re.findall(
        r"<script(?:\s[^>]*)?>(.*?)</script>",
        STUDIO.read_text(),
        re.DOTALL | re.IGNORECASE,
    )
    if not scripts:
        print("Dashboard Studio contains no inline scripts", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="flexdisplay-studio-") as temp_dir:
        for index, source in enumerate(scripts, start=1):
            path = Path(temp_dir) / f"studio-{index}.js"
            path.write_text(source)
            result = subprocess.run(
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                print(result.stderr, file=sys.stderr, end="")
                return result.returncode
    print(
        f"Dashboard Studio JavaScript syntax is valid ({len(scripts)} script block(s))."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
