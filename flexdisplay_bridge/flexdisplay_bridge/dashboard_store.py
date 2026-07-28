from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import (
    DashboardPageConfig,
    DashboardProfileConfig,
    EntityConfig,
    PageActivationConfig,
)

PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
LAYOUTS = {"auto", "single", "rows", "columns", "grid"}
TILE_STYLES = {"value", "gauge", "progress", "history", "qr", "image", "name_card"}
TILE_SOURCES = {"home_assistant", "static"}
IMAGE_FITS = {"cover", "contain"}
BADGE_THEMES = {"classic", "bold", "diagonal", "halftone"}
BADGE_ASSET_PATTERN = re.compile(r"^[a-f0-9]{24}$")
ICONS = {
    "auto",
    "home",
    "temperature",
    "humidity",
    "battery",
    "power",
    "solar",
    "wifi",
    "storage",
    "clock",
    "weather",
    "rain",
    "light",
    "lock",
    "alert",
}
ACTIVATION_TYPES = {"always", "schedule", "condition"}
CONDITION_OPERATORS = {
    "equals",
    "not_equals",
    "above",
    "below",
    "contains",
    "on",
    "off",
    "unavailable",
}


class DashboardValidationError(ValueError):
    """Raised when a Dashboard Studio profile is not safe to persist."""


def _bounded_text(value: Any, fallback: str, maximum: int) -> str:
    selected = str(value or fallback).replace("\r", " ").replace("\n", " ").strip()
    return selected[:maximum] or fallback


def _bounded_value(value: Any, maximum: int = 1024) -> str:
    selected = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return selected[:maximum]


def _number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _integer(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


def _clock(value: Any, fallback: str) -> str:
    candidate = str(value or fallback)
    try:
        hour, minute = (int(part) for part in candidate.split(":", 1))
    except (TypeError, ValueError):
        return fallback
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return fallback
    return f"{hour:02d}:{minute:02d}"


def _image_url(value: Any) -> str:
    selected = _bounded_text(value, "", 2048)
    if not selected:
        return ""
    parsed = urlparse(selected)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DashboardValidationError("Image URLs must use http:// or https://")
    if parsed.username or parsed.password:
        raise DashboardValidationError("Image URLs must not contain embedded credentials")
    return selected


def _activation(raw: Any, page_number: int) -> PageActivationConfig:
    value = raw if isinstance(raw, dict) else {}
    activation_type = str(value.get("type") or "always")
    if activation_type not in ACTIVATION_TYPES:
        raise DashboardValidationError(
            f"Page {page_number} has an unsupported activation type"
        )
    entity_id = _bounded_text(value.get("entity_id"), "", 128)
    operator = str(value.get("operator") or "equals")
    if activation_type == "condition":
        if not entity_id or "." not in entity_id:
            raise DashboardValidationError(
                f"Page {page_number} condition needs a Home Assistant entity ID"
            )
        if operator not in CONDITION_OPERATORS:
            raise DashboardValidationError(
                f"Page {page_number} has an unsupported condition operator"
            )
    start = _clock(value.get("start"), "06:00")
    end = _clock(value.get("end"), "22:00")
    if activation_type == "schedule" and start == end:
        raise DashboardValidationError(
            f"Page {page_number} schedule start and end must differ"
        )
    return PageActivationConfig(
        type=activation_type,
        entity_id=entity_id,
        operator=operator,
        value=_bounded_text(value.get("value"), "", 128),
        priority=_integer(value.get("priority"), 50, 0, 100),
        expires_after_seconds=_integer(
            value.get("expires_after_seconds"),
            0,
            0,
            86400,
        ),
        start=start,
        end=end,
    )


def parse_profile(name: str, payload: dict[str, Any]) -> DashboardProfileConfig:
    """Validate an API payload and translate it into the renderer model."""
    if not PROFILE_PATTERN.fullmatch(name):
        raise DashboardValidationError(
            "Profile names must contain only letters, numbers, underscores, and hyphens"
        )
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list) or not 1 <= len(raw_pages) <= 12:
        raise DashboardValidationError("A profile must contain between 1 and 12 pages")

    pages: list[DashboardPageConfig] = []
    for page_index, raw_page in enumerate(raw_pages):
        if not isinstance(raw_page, dict):
            raise DashboardValidationError(f"Page {page_index + 1} must be an object")
        layout = str(raw_page.get("layout") or "auto")
        if layout not in LAYOUTS:
            raise DashboardValidationError(f"Page {page_index + 1} has an unsupported layout")
        raw_entities = raw_page.get("entities") or []
        if not isinstance(raw_entities, list) or len(raw_entities) > 4:
            raise DashboardValidationError(f"Page {page_index + 1} may contain at most four tiles")

        entities: list[EntityConfig] = []
        for tile_index, raw_entity in enumerate(raw_entities):
            if not isinstance(raw_entity, dict):
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} must be an object"
                )
            style = str(raw_entity.get("style") or "value")
            source = str(raw_entity.get("source") or "home_assistant")
            icon = str(raw_entity.get("icon") or "auto")
            if style not in TILE_STYLES:
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} has an unsupported style"
                )
            if source not in TILE_SOURCES:
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} has an unsupported data source"
                )
            if style == "name_card" and source != "static":
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} name cards must use fixed content"
                )
            if style == "image" and source == "static":
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} images must use an image URL or Home Assistant entity"
                )
            image_url = _image_url(raw_entity.get("image_url")) if style == "image" else ""
            entity_id = _bounded_text(raw_entity.get("entity_id"), "", 128)
            if image_url and (not entity_id or entity_id == "sensor.example"):
                entity_id = f"image_url.page_{page_index + 1}_tile_{tile_index + 1}"
            if source == "static":
                entity_id = (
                    entity_id
                    if entity_id.startswith("static.")
                    else f"static.page_{page_index + 1}_tile_{tile_index + 1}"
                )
            if not entity_id or "." not in entity_id:
                source_hint = (
                    "an image URL or Home Assistant entity ID"
                    if style == "image"
                    else "an entity ID"
                )
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} needs {source_hint}"
                )
            if icon not in ICONS:
                raise DashboardValidationError(f"{entity_id} has an unsupported icon")
            image_fit = str(raw_entity.get("image_fit") or "cover")
            if image_fit not in IMAGE_FITS:
                raise DashboardValidationError(f"{entity_id} has an unsupported image fit")
            minimum = _number(raw_entity.get("minimum"), 0.0)
            maximum = _number(raw_entity.get("maximum"), 100.0)
            if maximum <= minimum:
                raise DashboardValidationError(f"{entity_id} maximum must be greater than minimum")
            static_value = _bounded_value(raw_entity.get("value"))
            if source == "static" and not static_value:
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} needs fixed content"
                )
            badge_theme = str(raw_entity.get("badge_theme") or "classic").lower()
            if badge_theme not in BADGE_THEMES:
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} has an unsupported badge theme"
                )
            badge_photo_id = _bounded_text(
                raw_entity.get("badge_photo_id"),
                "",
                24,
            )
            if badge_photo_id and not BADGE_ASSET_PATTERN.fullmatch(badge_photo_id):
                raise DashboardValidationError(
                    f"Tile {tile_index + 1} on page {page_index + 1} has an invalid profile photo"
                )
            if style != "name_card":
                badge_photo_id = ""
                badge_theme = "classic"
            text_scale = _integer(
                raw_entity.get("text_scale"),
                100,
                60,
                180,
            )
            qr_scale = _integer(
                raw_entity.get("qr_scale"),
                100,
                50,
                150,
            )
            entities.append(
                EntityConfig(
                    entity_id=entity_id,
                    label=_bounded_text(raw_entity.get("label"), entity_id, 64),
                    unit=_bounded_text(raw_entity.get("unit"), "", 64),
                    icon=icon,
                    style=style,
                    minimum=minimum,
                    maximum=maximum,
                    image_url=image_url,
                    image_fit=image_fit,
                    source=source,
                    value=static_value,
                    badge_photo_id=badge_photo_id,
                    badge_photo_filename=(
                        _bounded_text(
                            raw_entity.get("badge_photo_filename"),
                            "",
                            160,
                        )
                        if badge_photo_id
                        else ""
                    ),
                    badge_theme=badge_theme,
                    text_scale=text_scale,
                    qr_scale=qr_scale,
                )
            )
        pages.append(
            DashboardPageConfig(
                title=_bounded_text(raw_page.get("title"), f"PAGE {page_index + 1}", 40).upper(),
                entities=tuple(entities),
                layout=layout,
                activation=_activation(raw_page.get("activation"), page_index + 1),
            )
        )

    try:
        rotation = int(payload.get("auto_rotate_seconds", 0))
    except (TypeError, ValueError) as err:
        raise DashboardValidationError("Automatic rotation must be a number of seconds") from err
    return DashboardProfileConfig(
        name=name,
        pages=tuple(pages),
        auto_rotate_seconds=max(0, min(86400, rotation)),
    )


def profile_payload(profile: DashboardProfileConfig) -> dict[str, Any]:
    return {
        "name": profile.name,
        "auto_rotate_seconds": profile.auto_rotate_seconds,
        "pages": [
            {
                "title": page.title,
                "layout": page.layout,
                "activation": asdict(page.activation),
                "entities": [asdict(entity) for entity in page.entities],
            }
            for page in profile.pages
        ],
    }


class DashboardProfileStore:
    """Persist visual profiles separately from hand-maintained YAML configuration."""

    def __init__(
        self,
        path: Path,
        configured: dict[str, DashboardProfileConfig],
        default_profile: str,
    ):
        self.path = path
        self._lock = threading.RLock()
        self._profiles = dict(configured)
        if not self._profiles:
            self._profiles[default_profile] = DashboardProfileConfig(name=default_profile)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            values = raw.get("profiles") if isinstance(raw, dict) else None
            if not isinstance(values, dict):
                return
            loaded: dict[str, DashboardProfileConfig] = {}
            for name, value in values.items():
                if not isinstance(value, dict):
                    continue
                selected = str(name)
                if value.get("pages") == []:
                    loaded[selected] = DashboardProfileConfig(
                        name=selected,
                        auto_rotate_seconds=max(
                            0,
                            min(86400, int(value.get("auto_rotate_seconds", 0))),
                        ),
                    )
                else:
                    loaded[selected] = parse_profile(selected, value)
            if loaded:
                self._profiles = loaded
        except (OSError, json.JSONDecodeError, DashboardValidationError):
            # Keep the last valid YAML-backed profiles if an interrupted write or
            # hand-edited state file cannot be parsed.
            return

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 2,
                    "profiles": {
                        name: profile_payload(profile)
                        for name, profile in sorted(self._profiles.items())
                    },
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._profiles)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [profile_payload(self._profiles[name]) for name in sorted(self._profiles)]

    def get(self, name: str) -> DashboardProfileConfig | None:
        with self._lock:
            return self._profiles.get(name)

    def fallback(self) -> DashboardProfileConfig:
        with self._lock:
            return self._profiles[sorted(self._profiles)[0]]

    def resolve(self, name: str) -> DashboardProfileConfig:
        return self.get(name) or self.fallback()

    def put(self, name: str, payload: dict[str, Any]) -> DashboardProfileConfig:
        profile = parse_profile(name, payload)
        with self._lock:
            self._profiles[name] = profile
            self._save()
        return profile

    def delete(self, name: str) -> None:
        with self._lock:
            if name not in self._profiles:
                raise KeyError(name)
            if len(self._profiles) == 1:
                raise DashboardValidationError("At least one dashboard profile must remain")
            del self._profiles[name]
            self._save()

    @staticmethod
    def entity_configs(profile: DashboardProfileConfig) -> tuple[EntityConfig, ...]:
        unique: dict[str, EntityConfig] = {}
        for page in profile.pages:
            for entity in page.entities:
                key = f"{entity.entity_id}\0{entity.image_url}" if entity.image_url else entity.entity_id
                unique[key] = entity
            if (
                page.activation.type == "condition"
                and page.activation.entity_id
                and page.activation.entity_id not in unique
            ):
                unique[page.activation.entity_id] = EntityConfig(
                    page.activation.entity_id,
                    page.activation.entity_id,
                )
        return tuple(unique.values())
