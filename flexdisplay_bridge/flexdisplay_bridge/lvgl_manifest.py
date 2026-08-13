from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import (
    COLOR_ROLES,
    CONTROL_STYLES,
    DashboardProfileConfig,
    EntityConfig,
)
from .dashboards import DashboardPage
from .display_profiles import DisplayProfile


LVGL_UI_SCHEMA = "flexdisplay.lvgl-ui"
LVGL_UI_VERSION = 1
LVGL_UI_MEDIA_TYPE = "application/vnd.flexdisplay.lvgl+json;version=1"
LVGL_UI_CAPABILITY = "lvgl-ui-v1"
LVGL_UI_EVENT_GESTURES = {"tap"}
LVGL_UI_LAYOUTS = frozenset({
    "auto",
    "single",
    "rows",
    "columns",
    "grid",
})
MAX_LVGL_MANIFEST_BYTES = 64 * 1024
MAX_PAGE_TITLE_BYTES = 48
MAX_TILE_LABEL_BYTES = 48
MAX_TILE_VALUE_BYTES = 32
MAX_TILE_UNIT_BYTES = 16

_DISPLAY_CONTROL_TRANSLATION = str.maketrans(
    {codepoint: " " for codepoint in (*range(0x20), 0x7F)}
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]+$")
LVGL_UI_TILE_STYLES = frozenset({
    "value",
    "gauge",
    "progress",
})
_ON_STATES = {"1", "active", "detected", "home", "locked", "on", "open", "true"}

_PALETTES: dict[str, dict[str, str]] = {
    "midnight": {
        "background": "#07111F",
        "surface": "#10243B",
        "text": "#F4F8FC",
        "muted": "#93A9BD",
        "primary": "#59C3FF",
        "info": "#76A9FF",
        "success": "#5BDBA5",
        "warning": "#FFC857",
        "danger": "#FF6B7A",
        "outline": "#31516D",
    },
    "ocean": {
        "background": "#031B2B",
        "surface": "#073A52",
        "text": "#ECFBFF",
        "muted": "#8EC5D3",
        "primary": "#28C7D9",
        "info": "#4FA3FF",
        "success": "#53D68B",
        "warning": "#FFD166",
        "danger": "#FF6F76",
        "outline": "#1B7083",
    },
    "sunrise": {
        "background": "#27112B",
        "surface": "#4A1E3E",
        "text": "#FFF8F1",
        "muted": "#DAB5BB",
        "primary": "#FF8A5B",
        "info": "#77B8FF",
        "success": "#67D59A",
        "warning": "#FFD166",
        "danger": "#FF5D73",
        "outline": "#895065",
    },
    "paper": {
        "background": "#F4F0E8",
        "surface": "#FFFDF8",
        "text": "#17202A",
        "muted": "#66717C",
        "primary": "#176B87",
        "info": "#356FA8",
        "success": "#2B7A55",
        "warning": "#A76200",
        "danger": "#B43A4A",
        "outline": "#C7BFAF",
    },
}


@dataclass(frozen=True, slots=True)
class LvglActionBinding:
    action_id: str
    page_id: str
    tile_id: str
    action: dict[str, Any]


class LvglManifestError(ValueError):
    """Raised when a UI snapshot cannot fit the receiver contract."""


def _lvgl_layout(value: Any, page_index: int) -> str:
    layout = str(value or "auto").strip().lower()
    if layout not in LVGL_UI_LAYOUTS:
        raise LvglManifestError(
            f"LVGL receiver v1 does not support the {layout or 'empty'} page "
            f"layout on page {page_index + 1}; use auto, single, rows, "
            "columns, or grid"
        )
    return layout


def validate_lvgl_profile(profile: DashboardProfileConfig) -> None:
    """Reject device-neutral visuals that receiver contract v1 cannot render."""

    for page_index, page in enumerate(profile.pages[:12]):
        _lvgl_layout(page.layout, page_index)
        for tile_index, tile in enumerate(page.entities[:4]):
            style = str(tile.style or "value").strip().lower()
            if style not in LVGL_UI_TILE_STYLES:
                raise LvglManifestError(
                    f"LVGL receiver v1 does not support the {style or 'empty'} tile "
                    f"style on page {page_index + 1}, tile {tile_index + 1}; use "
                    "value, gauge, or progress"
                )


def canonical_manifest_bytes(value: Any) -> bytes:
    """Serialize exactly once with valid JSON and strict UTF-8 output."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as err:
        raise LvglManifestError("LVGL manifest contains invalid JSON or UTF-8 data") from err


def _bounded_text(value: Any, maximum: int) -> str:
    selected = str(value or "").translate(_DISPLAY_CONTROL_TRANSLATION).strip()
    try:
        encoded = selected.encode("utf-8", errors="strict")
    except UnicodeError as err:
        raise LvglManifestError("LVGL display text is not valid UTF-8") from err
    if len(encoded) <= maximum:
        return selected
    return encoded[:maximum].decode("utf-8", errors="ignore")


def _identifier(value: Any, fallback: str, maximum: int) -> str:
    selected = _bounded_text(value, maximum)
    if not selected or not _SAFE_IDENTIFIER.fullmatch(selected):
        return fallback
    return selected


def _choice(value: Any, allowed: set[str], fallback: str) -> str:
    selected = str(value or fallback).strip().lower()
    return selected if selected in allowed else fallback


def _page_id(page_index: int) -> str:
    return f"page-{page_index + 1}"


def _tile_id(page_index: int, tile_index: int) -> str:
    return f"tile-{page_index + 1}-{tile_index + 1}"


def action_id(
    profile_name: str,
    page_index: int,
    tile_index: int,
    action: dict[str, Any],
) -> str:
    """Return an opaque reference without exposing Home Assistant instructions."""

    material = {
        "profile": profile_name,
        "page": page_index,
        "tile": tile_index,
        "action": action,
        "version": LVGL_UI_VERSION,
    }
    return hashlib.sha256(canonical_manifest_bytes(material)).hexdigest()[:24]


def manifest_action_bindings(
    profile: DashboardProfileConfig,
    *,
    interactive: bool = True,
) -> dict[str, LvglActionBinding]:
    bindings: dict[str, LvglActionBinding] = {}
    if not interactive:
        return bindings
    for page_index, page in enumerate(profile.pages[:12]):
        for tile_index, tile in enumerate(page.entities[:4]):
            action = tile.tap_action if isinstance(tile.tap_action, dict) else {"type": "none"}
            if action.get("type") == "none" or _control_style(tile) == "read_only":
                continue
            selected_id = action_id(profile.name, page_index, tile_index, action)
            bindings[selected_id] = LvglActionBinding(
                action_id=selected_id,
                page_id=_page_id(page_index),
                tile_id=_tile_id(page_index, tile_index),
                action=dict(action),
            )
    return bindings


def resolve_manifest_action(
    profile: DashboardProfileConfig,
    selected_action_id: str,
    page_id: str,
    tile_id: str,
) -> LvglActionBinding | None:
    binding = manifest_action_bindings(profile).get(str(selected_action_id or ""))
    if binding is None or binding.page_id != page_id or binding.tile_id != tile_id:
        return None
    return binding


def _theme(selected: str) -> tuple[str, dict[str, str]]:
    theme_id = selected if selected in _PALETTES else "midnight"
    return theme_id, dict(_PALETTES[theme_id])


def color_theme(selected: str) -> dict[str, str]:
    """Map a manifest palette onto the colour preview renderer keys."""

    _, palette = _theme(selected)
    return {
        "background": palette["background"],
        "surface": palette["surface"],
        "foreground": palette["text"],
        "muted": palette["muted"],
        "accent": palette["primary"],
        "accent_secondary": palette["info"],
        "positive": palette["success"],
        "warning": palette["warning"],
        "danger": palette["danger"],
        "grid": palette["outline"],
        "shadow": palette["background"],
    }


def _safe_area(display: DisplayProfile) -> dict[str, int]:
    if display.shape != "round":
        return {"x": 0, "y": 0, "width": display.width, "height": display.height}
    inset = max(12, round(min(display.width, display.height) * 0.07))
    return {
        "x": inset,
        "y": inset,
        "width": display.width - inset * 2,
        "height": display.height - inset * 2,
    }


def _numeric(value: Any) -> float | None:
    try:
        selected = float(value)
    except (TypeError, ValueError):
        return None
    return selected if math.isfinite(selected) else None


def _finite_float(value: Any, fallback: float) -> float:
    selected = _numeric(value)
    return selected if selected is not None else fallback


def _progress(value: Any, minimum: float, maximum: float) -> float | None:
    selected = _numeric(value)
    if selected is None or maximum <= minimum:
        return None
    return round(max(0.0, min(1.0, (selected - minimum) / (maximum - minimum))), 4)


def _control_style(config: EntityConfig | None) -> str:
    if config is None:
        return "read_only"
    selected = _choice(config.control_style, CONTROL_STYLES, "auto")
    if selected != "auto":
        return selected
    action_type = str(config.tap_action.get("type") or "none")
    if action_type == "none":
        return "read_only"
    if (
        action_type == "home_assistant"
        and str(config.tap_action.get("service") or "") == "homeassistant.toggle"
    ):
        return "toggle"
    return "button"


def _widget(style: str, control_style: str) -> str:
    if control_style == "toggle":
        return "toggle"
    if control_style == "button":
        return "button"
    return {
        "gauge": "gauge",
        "progress": "gauge",
    }.get(style, "value")


def _tile_payload(
    state: Any,
    config: EntityConfig | None,
    profile_name: str,
    source_page_index: int,
    tile_index: int,
    display_touch: bool,
) -> dict[str, Any]:
    page_id = _page_id(source_page_index)
    tile_id = _tile_id(source_page_index, tile_index)
    style = str(
        getattr(state, "style", None) or (config.style if config else "value")
    ).strip().lower()
    if style not in LVGL_UI_TILE_STYLES:
        raise LvglManifestError(
            f"LVGL receiver v1 does not support the {style or 'empty'} tile style; "
            "use value, gauge, or progress"
        )
    control_style = _control_style(config)
    raw_state = _bounded_text(
        getattr(state, "state", "--"), MAX_TILE_VALUE_BYTES
    )
    available = bool(getattr(state, "available", False))
    payload: dict[str, Any] = {
        "id": tile_id,
        "page_id": page_id,
        "label": _bounded_text(
            getattr(state, "label", ""), MAX_TILE_LABEL_BYTES
        ) or "Value",
        "value": raw_state,
        "unit": _bounded_text(getattr(state, "unit", ""), MAX_TILE_UNIT_BYTES),
        "widget": _widget(style, control_style),
        "style": style,
        "color_role": _choice(
            config.color_role if config else "auto", COLOR_ROLES, "auto"
        ),
        "control_style": control_style,
        "available": available,
        "on": available and raw_state.casefold() in _ON_STATES,
    }
    minimum = _finite_float(getattr(state, "minimum", 0.0), 0.0)
    maximum = _finite_float(getattr(state, "maximum", 100.0), 100.0)
    if maximum <= minimum:
        maximum = minimum + 1.0
    progress = _progress(raw_state, minimum, maximum)
    if progress is not None:
        payload.update(
            {
                "numeric_value": _numeric(raw_state),
                "minimum": minimum,
                "maximum": maximum,
                "progress": progress,
            }
        )
    action = config.tap_action if config and isinstance(config.tap_action, dict) else {"type": "none"}
    if (
        action.get("type") != "none"
        and control_style != "read_only"
        and display_touch
    ):
        payload["action_id"] = action_id(
            profile_name, source_page_index, tile_index, action
        )
    return payload


def build_lvgl_manifest(
    dashboard_profile: DashboardProfileConfig,
    rendered_pages: tuple[DashboardPage, ...],
    active_pages: tuple[DashboardPage, ...],
    device: dict[str, Any],
    display: DisplayProfile,
    *,
    active_page_index: int = 0,
    ha_error: str = "",
    poll_after_seconds: int = 5,
) -> dict[str, Any]:
    """Build a bounded declarative UI snapshot for an LVGL-capable receiver."""

    validate_lvgl_profile(dashboard_profile)
    source_indexes = {id(page): index for index, page in enumerate(rendered_pages)}
    pages_payload: list[dict[str, Any]] = []
    for fallback_index, page in enumerate(active_pages[:12]):
        source_index = source_indexes.get(id(page), fallback_index)
        configured_page = (
            dashboard_profile.pages[source_index]
            if source_index < len(dashboard_profile.pages)
            else None
        )
        tiles = []
        for tile_index, state in enumerate(page.entities[:4]):
            configured_tile = (
                configured_page.entities[tile_index]
                if configured_page is not None
                and tile_index < len(configured_page.entities)
                else None
            )
            tiles.append(
                _tile_payload(
                    state,
                    configured_tile,
                    dashboard_profile.name,
                    source_index,
                    tile_index,
                    display.touch,
                )
            )
        pages_payload.append(
            {
                "id": _page_id(source_index),
                "title": _bounded_text(page.title, MAX_PAGE_TITLE_BYTES)
                or f"PAGE {source_index + 1}",
                "layout": _lvgl_layout(page.layout, source_index),
                "tiles": tiles,
            }
        )
    if not pages_payload:
        raise LvglManifestError("LVGL manifest must contain at least one page")

    selected_page = max(0, min(active_page_index, len(pages_payload) - 1))
    theme_id, colors = _theme(dashboard_profile.color_theme)
    stable_payload: dict[str, Any] = {
        "schema": LVGL_UI_SCHEMA,
        "version": LVGL_UI_VERSION,
        "profile": {
            "id": display.id,
            "model": display.model,
            "width": display.width,
            "height": display.height,
            "shape": display.shape,
            "pixel_format": display.pixel_format,
            "safe_area": _safe_area(display),
        },
        "theme": {"id": theme_id, "colors": colors},
        "device": {
            "id": _identifier(device.get("device_id"), "device", 64),
            "name": _bounded_text(
                device.get("name") or device.get("device_id"), 64
            ),
            "area": _bounded_text(device.get("area"), 64),
            "online": True,
        },
        "active_page": selected_page,
        "page_count": len(pages_payload),
        "pages": pages_payload,
        "status": {
            "home_assistant": "degraded" if ha_error else "connected",
            "message": _bounded_text(ha_error, 160),
        },
        "events": {
            "path": (
                f"/api/v1/devices/{_identifier(device.get('device_id'), 'device', 64)}"
                "/ui-events"
            ),
            "gestures": sorted(LVGL_UI_EVENT_GESTURES),
        },
        "poll_after_seconds": max(5, min(3600, int(poll_after_seconds))),
    }
    revision = hashlib.sha256(canonical_manifest_bytes(stable_payload)).hexdigest()[:24]
    manifest = {
        **stable_payload,
        "revision": revision,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if len(canonical_manifest_bytes(manifest)) > MAX_LVGL_MANIFEST_BYTES:
        raise LvglManifestError(
            "LVGL manifest exceeds the receiver's 64 KiB response limit"
        )
    return manifest
