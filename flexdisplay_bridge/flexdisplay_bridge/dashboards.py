from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .home_assistant import EntityState


@dataclass(frozen=True)
class DashboardPage:
    title: str
    entities: tuple[EntityState, ...]


def _identity(entity: EntityState) -> str:
    return f"{entity.entity_id} {entity.label}".lower()


def _climate(entity: EntityState) -> bool:
    identity = _identity(entity)
    return "temperature" in identity or "_temp" in identity or "humidity" in identity or "moisture" in identity


def _energy(entity: EntityState) -> bool:
    identity = _identity(entity)
    return (
        "battery" in identity
        or "charge" in identity
        or "power" in identity
        or "solar" in identity
        or entity.unit in {"W", "kW"}
    )


def _duration(value: Any) -> str:
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "--"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{round(seconds / 60)} min"
    return f"{seconds} sec"


def _system_page(device: dict[str, Any]) -> DashboardPage:
    battery = device.get("battery_percent")
    rssi = device.get("rssi")
    uptime = device.get("uptime_seconds")
    return DashboardPage(
        "DEVICE STATUS",
        (
            EntityState("device.battery", "Device Battery", str(battery if battery is not None else "--"), "%", battery is not None),
            EntityState("device.wifi", "Wi-Fi Signal", str(rssi if rssi is not None else "--"), "dBm", rssi is not None),
            EntityState("device.uptime", "Uptime", _duration(uptime), "", uptime is not None),
            EntityState(
                "device.storage",
                "SD Card",
                "Ready" if device.get("sd_ready") else "Missing",
                "",
                device.get("sd_ready") is not None,
            ),
        ),
    )


def build_dashboard_pages(
    entities: list[EntityState],
    device: dict[str, Any],
) -> tuple[DashboardPage, ...]:
    """Create readable built-in pages from the configured Home Assistant values."""
    pages = [DashboardPage("OVERVIEW", tuple(entities[:4]))]
    climate = tuple(entity for entity in entities if _climate(entity))[:4]
    energy = tuple(entity for entity in entities if _energy(entity))[:4]
    if climate:
        pages.append(DashboardPage("CLIMATE", climate))
    if energy:
        pages.append(DashboardPage("ENERGY", energy))
    pages.append(_system_page(device))
    return tuple(pages)
