#!/usr/bin/env python3
"""Collect the compact FlexHub health contract without changing the device."""

from __future__ import annotations

import argparse
import getpass
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


MAX_RESPONSE_BYTES = 16 * 1024
HEALTH_PATH = "/api/flexhub/health"
UINT32_MODULUS = 1 << 32
ACTIVITIES = {"idle", "http", "scanning", "sending", "storage", "fleet"}
MEMORY_FIELDS = (
    "internal_free_bytes",
    "internal_min_free_bytes",
    "internal_largest_block_bytes",
    "psram_free_bytes",
    "psram_size_bytes",
)


class SoakError(RuntimeError):
    """Base class for a safely reportable soak error."""


class RequestError(SoakError):
    """The health request could not be completed."""


class ContractError(SoakError):
    """The response did not satisfy health schema version 1."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RequestError(f"redirect refused ({code})")


def health_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("FlexHub URL must use http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("FlexHub URL must contain a host and no embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("FlexHub URL must not contain a query or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("FlexHub URL must be a base address without a path")
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return f"{origin}{HEALTH_PATH}"


def nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{field_name} must be a non-negative integer")
    return value


def validate_sample(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("health response must be a JSON object")
    if value.get("schema_version") != 1:
        raise ContractError("health schema_version must be 1")

    sample = {
        "schema_version": 1,
        "sampled_at_ms": nonnegative_int(value.get("sampled_at_ms"), "sampled_at_ms"),
        "sample_age_ms": nonnegative_int(value.get("sample_age_ms"), "sample_age_ms"),
        "uptime_seconds": nonnegative_int(value.get("uptime_seconds"), "uptime_seconds"),
        "reset_reason": nonnegative_int(value.get("reset_reason"), "reset_reason"),
    }
    activity = value.get("activity")
    if activity not in ACTIVITIES:
        raise ContractError(f"activity must be one of {', '.join(sorted(ACTIVITIES))}")
    sample["activity"] = activity

    memory = value.get("memory")
    if not isinstance(memory, dict):
        raise ContractError("memory must be a JSON object")
    sample["memory"] = {
        name: nonnegative_int(memory.get(name), f"memory.{name}")
        for name in MEMORY_FIELDS
    }
    return sample


def fetch_sample(url: str, pin: str | None, timeout: float) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if pin:
        headers["X-FlexHub-Token"] = pin
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except RequestError:
        raise
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise RequestError(str(exc)) from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ContractError("health response exceeds 16 KiB")
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("health response is not valid UTF-8 JSON") from exc
    return validate_sample(decoded)


def is_expected_uint32_rollover(previous: int, current: int) -> bool:
    return previous > UINT32_MODULUS * 3 // 4 and current < UINT32_MODULUS // 4


@dataclass
class Summary:
    samples: int = 0
    request_errors: int = 0
    contract_errors: int = 0
    uptime_regressions: int = 0
    sample_clock_regressions: int = 0
    sample_clock_rollovers: int = 0
    reset_reason_changes: int = 0
    max_sample_age_ms: int = 0
    activity_samples: dict[str, int] = field(default_factory=dict)
    activity_minima: dict[str, dict[str, int]] = field(default_factory=dict)
    previous_uptime: int | None = None
    previous_sampled_at: int | None = None
    previous_reset_reason: int | None = None

    def observe(self, sample: dict[str, Any]) -> None:
        uptime = sample["uptime_seconds"]
        sampled_at = sample["sampled_at_ms"]
        reset_reason = sample["reset_reason"]
        activity = sample["activity"]
        memory = sample["memory"]

        if self.previous_uptime is not None and uptime < self.previous_uptime:
            self.uptime_regressions += 1
        if self.previous_sampled_at is not None and sampled_at < self.previous_sampled_at:
            if is_expected_uint32_rollover(self.previous_sampled_at, sampled_at):
                self.sample_clock_rollovers += 1
            else:
                self.sample_clock_regressions += 1
        if self.previous_reset_reason is not None and reset_reason != self.previous_reset_reason:
            self.reset_reason_changes += 1

        self.previous_uptime = uptime
        self.previous_sampled_at = sampled_at
        self.previous_reset_reason = reset_reason
        self.samples += 1
        self.max_sample_age_ms = max(self.max_sample_age_ms, sample["sample_age_ms"])
        self.activity_samples[activity] = self.activity_samples.get(activity, 0) + 1
        minima = self.activity_minima.setdefault(activity, {})
        for name in MEMORY_FIELDS:
            minima[name] = min(minima.get(name, memory[name]), memory[name])

    def record_error(self, error: SoakError) -> None:
        if isinstance(error, ContractError):
            self.contract_errors += 1
        else:
            self.request_errors += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "request_errors": self.request_errors,
            "contract_errors": self.contract_errors,
            "uptime_regressions": self.uptime_regressions,
            "sample_clock_regressions": self.sample_clock_regressions,
            "sample_clock_rollovers": self.sample_clock_rollovers,
            "reset_reason_changes": self.reset_reason_changes,
            "max_sample_age_ms": self.max_sample_age_ms,
            "activity_samples": self.activity_samples,
            "activity_minima": self.activity_minima,
        }


def write_event(output: TextIO, event: dict[str, Any]) -> None:
    event["observed_at"] = datetime.now(timezone.utc).isoformat()
    output.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    output.flush()


def collect(
    url: str,
    pin: str | None,
    duration: float,
    interval: float,
    timeout: float,
    phase: str,
    output: TextIO,
) -> Summary:
    summary = Summary()
    started = time.monotonic()
    deadline = started + duration
    sequence = 0
    while time.monotonic() < deadline:
        sequence += 1
        iteration_started = time.monotonic()
        try:
            sample = fetch_sample(url, pin, timeout)
            summary.observe(sample)
            write_event(
                output,
                {"event": "sample", "sequence": sequence, "phase": phase, "health": sample},
            )
        except SoakError as exc:
            summary.record_error(exc)
            write_event(
                output,
                {
                    "event": "error",
                    "sequence": sequence,
                    "phase": phase,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(max(0.0, interval - (time.monotonic() - iteration_started)), remaining))
    write_event(
        output,
        {
            "event": "summary",
            "phase": phase,
            "duration_seconds": round(time.monotonic() - started, 3),
            **summary.as_dict(),
        },
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="FlexHub base URL; credentials are not accepted in the URL")
    parser.add_argument("--duration", type=float, default=1800.0, help="collection duration in seconds")
    parser.add_argument("--interval", type=float, default=5.0, help="sample interval in seconds")
    parser.add_argument("--timeout", type=float, default=3.0, help="per-request timeout in seconds")
    parser.add_argument("--phase", default="baseline", help="workload phase label recorded with each event")
    parser.add_argument("--output", type=Path, required=True, help="JSONL output path")
    parser.add_argument("--prompt-pin", action="store_true", help="prompt privately for the optional FlexHub PIN")
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0 or args.timeout <= 0:
        parser.error("duration, interval, and timeout must be positive")
    try:
        args.url = health_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    args = parse_args()
    pin = getpass.getpass("FlexHub PIN: ") if args.prompt_pin else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        summary = collect(
            args.url,
            pin,
            args.duration,
            args.interval,
            args.timeout,
            args.phase,
            output,
        )
    if summary.samples == 0:
        return 2
    return 1 if summary.request_errors or summary.contract_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
