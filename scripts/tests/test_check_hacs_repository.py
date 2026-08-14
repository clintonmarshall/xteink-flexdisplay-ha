from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "check_hacs_repository.py"
SPEC = importlib.util.spec_from_file_location("check_hacs_repository", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)


def valid_repository() -> dict[str, object]:
    return {
        "full_name": validator.REPOSITORY,
        "private": False,
        "archived": False,
        "disabled": False,
        "description": "FlexDisplay distribution mirror",
        "has_issues": True,
        "topics": ["hacs", "home-assistant"],
        "license": {"spdx_id": "GPL-3.0"},
    }


class HacsRepositoryMetadataTests(unittest.TestCase):
    def test_valid_public_repository_passes(self) -> None:
        self.assertEqual(validator.repository_errors(valid_repository()), [])

    def test_each_required_property_fails_closed(self) -> None:
        invalid_values = {
            "full_name": "someone/else",
            "private": True,
            "archived": True,
            "disabled": True,
            "description": "",
            "has_issues": False,
            "topics": [],
            "license": {"spdx_id": "NOASSERTION"},
        }
        for key, value in invalid_values.items():
            with self.subTest(key=key):
                repository = valid_repository()
                repository[key] = value
                self.assertTrue(validator.repository_errors(repository))

    def test_network_or_schema_failure_fails_main(self) -> None:
        for error in (OSError("offline"), RuntimeError("unexpected response")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(validator, "fetch_repository", side_effect=error):
                    self.assertEqual(validator.main(), 1)


if __name__ == "__main__":
    unittest.main()
