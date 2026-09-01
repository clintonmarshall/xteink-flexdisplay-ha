from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReleaseStatusTests(unittest.TestCase):
    def test_checked_in_status_is_current(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/render_release_status.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("matches release-manifest.json", result.stdout)


if __name__ == "__main__":
    unittest.main()
