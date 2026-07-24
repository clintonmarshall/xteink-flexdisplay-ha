from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import DashboardPageConfig
from .home_assistant import EntityState


@dataclass(frozen=True)
class DashboardPage:
    title: str
    entities: tuple[EntityState, ...]


def _identity(entity: EntityState) -> str:
    return f"{entity.entity_id} {entity.label}".lower()


def _temperature(entity: EntityState) -> bool:
    identity = _identity(entity)
    return "temperature" in identity or "_temp" in identity


def _humidity(entity: EntityState) -> bool:
    identity = _identity(entity)
    return "humidity" in identity or "moisture" in identity


def _battery(entity: EntityState) -> bool:
    identity = _identity(entity)
    return "battery" in identity or "charge" in identity


def _power(entity: EntityState) -> bool:
    identity = _identity(entity)
    return "power" in identity or "solar" in identity or entity.unit in {"W", "kW"}


def _energy(entity: EntityState) -> bool:
    return _battery(entity) or _power(entity)


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


def _memory(value: Any) -> str:
    try:
        return f"{max(0, int(value)) / 1024:.0f} KB"
    except (TypeError, ValueError):
        return "--"


def _health_page(device: dict[str, Any]) -> DashboardPage:
    battery = device.get("battery_percent")
    uptime = device.get("uptime_seconds")
    free_heap = device.get("free_heap")
    return DashboardPage(
        "DEVICE HEALTH",
        (
            EntityState("device.battery", "Device Battery", str(battery if battery is not None else "--"), "%", battery is not None),
            EntityState("device.uptime", "Uptime", _duration(uptime), "", uptime is not None),
            EntityState(
                "device.storage",
                "SD Card",
                "Ready" if device.get("sd_ready") else "Missing",
                "",
                device.get("sd_ready") is not None,
            ),
            EntityState("device.memory", "Free Memory", _memory(free_heap), "", free_heap is not None),
        ),
    )


def _connectivity_page(device: dict[str, Any]) -> DashboardPage:
    rssi = device.get("rssi")
    usb = device.get("usb_connected")
    return DashboardPage(
        "CONNECTIVITY",
        (
            EntityState("device.wifi", "Wi-Fi Signal", str(rssi if rssi is not None else "--"), "dBm", rssi is not None),
            EntityState("device.mode", "Display Mode", str(device.get("mode") or "--").replace("_", " ").title(), "", bool(device.get("mode"))),
            EntityState("device.wake", "Wake Reason", str(device.get("wake_reason") or "--").replace("_", " ").title(), "", bool(device.get("wake_reason"))),
            EntityState("device.usb", "USB Power", "Connected" if usb else "Unplugged", "", usb is not None),
        ),
    )


def build_dashboard_pages(
    entities: list[EntityState],
    device: dict[str, Any],
    configured_pages: tuple[DashboardPageConfig, ...] = (),
) -> tuple[DashboardPage, ...]:
    """Create readable built-in pages from the configured Home Assistant values."""
    if configured_pages:
        device_entities = _health_page(device).entities + _connectivity_page(device).entities
        by_id = {entity.entity_id: entity for entity in (*entities, *device_entities)}
        return tuple(
            DashboardPage(
                page.title,
                tuple(
                    by_id.get(
                        configured.entity_id,
                        EntityState(
                            configured.entity_id,
                            configured.label,
                            "--",
                            configured.unit,
                            False,
                        ),
                    )
                    for configured in page.entities
                )[:4],
            )
            for page in configured_pages
        )
    pages = [DashboardPage("OVERVIEW", tuple(entities[:4]))]
    temperatures = tuple(entity for entity in entities if _temperature(entity))[:4]
    humidity = tuple(entity for entity in entities if _humidity(entity))[:4]
    batteries = tuple(entity for entity in entities if _battery(entity))[:4]
    power = tuple(entity for entity in entities if _power(entity))[:4]
    energy = tuple(entity for entity in entities if _energy(entity))[:4]
    if temperatures:
        pages.append(DashboardPage("TEMPERATURES", temperatures))
    if humidity:
        pages.append(DashboardPage("HUMIDITY", humidity))
    if batteries:
        pages.append(DashboardPage("BATTERIES", batteries))
    if power:
        pages.append(DashboardPage("POWER", power))
    if energy:
        pages.append(DashboardPage("ENERGY", energy))
    pages.append(_health_page(device))
    pages.append(_connectivity_page(device))
    return tuple(pages)
