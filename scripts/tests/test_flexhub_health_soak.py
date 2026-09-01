from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "flexhub_health_soak.py"
SPEC = importlib.util.spec_from_file_location("flexhub_health_soak", SCRIPT)
assert SPEC and SPEC.loader
soak = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = soak
SPEC.loader.exec_module(soak)


def sample(**overrides):
    value = {
        "schema_version": 1,
        "sampled_at_ms": 123456,
        "sample_age_ms": 850,
        "activity": "fleet",
        "uptime_seconds": 123,
        "reset_reason": 1,
        "memory": {
            "internal_free_bytes": 30720,
            "internal_min_free_bytes": 6144,
            "internal_largest_block_bytes": 12288,
            "psram_free_bytes": 6291456,
            "psram_size_bytes": 8388608,
        },
    }
    value.update(overrides)
    return value


class HealthSoakTests(unittest.TestCase):
    def test_health_url_is_fixed_and_rejects_credentials(self) -> None:
        self.assertEqual(
            soak.health_url("http://flexhub.local/"),
            "http://flexhub.local/api/flexhub/health",
        )
        with self.assertRaisesRegex(ValueError, "credentials"):
            soak.health_url("http://user:secret@flexhub.local")
        with self.assertRaisesRegex(ValueError, "query"):
            soak.health_url("http://flexhub.local?token=secret")
        with self.assertRaisesRegex(ValueError, "without a path"):
            soak.health_url("http://flexhub.local/admin")

    def test_valid_contract_is_normalized(self) -> None:
        self.assertEqual(soak.validate_sample(sample()), sample())

    def test_contract_rejects_boolean_integer_and_unknown_activity(self) -> None:
        with self.assertRaises(soak.ContractError):
            soak.validate_sample(sample(uptime_seconds=True))
        with self.assertRaises(soak.ContractError):
            soak.validate_sample(sample(activity="unknown"))

    def test_summary_tracks_activity_minima_and_age(self) -> None:
        summary = soak.Summary()
        summary.observe(sample())
        lower = sample(sample_age_ms=1500, activity="fleet")
        lower["memory"] = {**lower["memory"], "internal_largest_block_bytes": 8192}
        summary.observe(lower)
        self.assertEqual(summary.samples, 2)
        self.assertEqual(summary.max_sample_age_ms, 1500)
        self.assertEqual(
            summary.activity_minima["fleet"]["internal_largest_block_bytes"], 8192
        )

    def test_reboot_like_regressions_are_counted(self) -> None:
        summary = soak.Summary()
        summary.observe(sample(sampled_at_ms=200000, uptime_seconds=200))
        summary.observe(sample(sampled_at_ms=1000, uptime_seconds=1, reset_reason=3))
        self.assertEqual(summary.uptime_regressions, 1)
        self.assertEqual(summary.sample_clock_regressions, 1)
        self.assertEqual(summary.reset_reason_changes, 1)

    def test_expected_uint32_millis_rollover_is_not_regression(self) -> None:
        summary = soak.Summary()
        summary.observe(sample(sampled_at_ms=(1 << 32) - 100, uptime_seconds=5000000))
        summary.observe(sample(sampled_at_ms=50, uptime_seconds=5000001))
        self.assertEqual(summary.sample_clock_rollovers, 1)
        self.assertEqual(summary.sample_clock_regressions, 0)

    def test_error_classes_are_separate(self) -> None:
        summary = soak.Summary()
        summary.record_error(soak.RequestError("timeout"))
        summary.record_error(soak.ContractError("bad schema"))
        self.assertEqual(summary.request_errors, 1)
        self.assertEqual(summary.contract_errors, 1)


if __name__ == "__main__":
    unittest.main()
