from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import DashboardPageConfig, PageActivationConfig
from .home_assistant import EntityState


@dataclass(frozen=True)
class DashboardPage:
    title: str
    entities: tuple[EntityState, ...]
    layout: str = "auto"
    activation: PageActivationConfig = PageActivationConfig()


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


def _clock_minutes(value: str, fallback: int) -> int:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    except (AttributeError, TypeError, ValueError):
        pass
    return fallback


def _within_schedule(activation: PageActivationConfig, now: datetime) -> bool:
    current = now.hour * 60 + now.minute
    start = _clock_minutes(activation.start, 6 * 60)
    end = _clock_minutes(activation.end, 22 * 60)
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _condition_matches(
    activation: PageActivationConfig,
    states: dict[str, EntityState],
    now: datetime,
) -> bool:
    entity = states.get(activation.entity_id)
    operator = activation.operator
    if operator == "unavailable":
        return entity is None or not entity.available or entity.state.lower() == "unavailable"
    if entity is None or not entity.available:
        return False
    state = entity.state.strip()
    expected = activation.value.strip()
    normalized = state.casefold()
    expected_normalized = expected.casefold()
    if operator == "equals":
        matched = normalized == expected_normalized
    elif operator == "not_equals":
        matched = normalized != expected_normalized
    elif operator == "contains":
        matched = expected_normalized in normalized
    elif operator == "on":
        matched = normalized in {"on", "open", "active", "detected", "true", "1"}
    elif operator == "off":
        matched = normalized in {"off", "closed", "inactive", "clear", "false", "0"}
    elif operator in {"above", "below"}:
        try:
            current_number = float(state)
            expected_number = float(expected)
            matched = (
                current_number > expected_number
                if operator == "above"
                else current_number < expected_number
            )
        except ValueError:
            matched = False
    else:
        matched = False
    if (
        matched
        and activation.expires_after_seconds > 0
        and entity.last_changed is not None
    ):
        changed = entity.last_changed
        if changed.tzinfo is None:
            changed = changed.replace(tzinfo=UTC)
        matched = (now.astimezone(UTC) - changed.astimezone(UTC)).total_seconds() < (
            activation.expires_after_seconds
        )
    return matched


def select_active_pages(
    pages: tuple[DashboardPage, ...],
    entities: list[EntityState],
    device: dict[str, Any],
    timezone: str,
    now: datetime | None = None,
) -> tuple[tuple[DashboardPage, ...], str]:
    """Select alert or scheduled page sets while retaining a safe default playlist."""
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_now = current.astimezone(zone)
    device_entities = _health_page(device).entities + _connectivity_page(device).entities
    by_id = {entity.entity_id: entity for entity in (*entities, *device_entities)}

    alerts = [
        (index, page)
        for index, page in enumerate(pages)
        if page.activation.type == "condition"
        and _condition_matches(page.activation, by_id, current)
    ]
    scheduled = tuple(
        page
        for page in pages
        if page.activation.type == "schedule"
        and _within_schedule(page.activation, local_now)
    )
    defaults = tuple(page for page in pages if page.activation.type == "always")
    base = scheduled or defaults
    if alerts:
        ordered_alerts = tuple(
            page
            for _, page in sorted(
                alerts,
                key=lambda item: (-item[1].activation.priority, item[0]),
            )
        )
        return ordered_alerts + base, "alert"
    if scheduled:
        return scheduled, "schedule"
    if defaults:
        return defaults, "default"
    # Profiles containing only inactive rules must still produce an image.
    return pages[:1], "fallback"


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
                    (
                        EntityState(
                            current.entity_id,
                            configured.label,
                            current.state,
                            configured.unit or current.unit,
                            current.available,
                            configured.icon,
                            configured.style,
                            configured.minimum,
                            configured.maximum,
                            current.history,
                        )
                        if (current := by_id.get(configured.entity_id))
                        else EntityState(
                            configured.entity_id,
                            configured.label,
                            "--",
                            configured.unit,
                            False,
                            configured.icon,
                            configured.style,
                            configured.minimum,
                            configured.maximum,
                        )
                    )
                    for configured in page.entities
                )[:4],
                page.layout,
                page.activation,
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
