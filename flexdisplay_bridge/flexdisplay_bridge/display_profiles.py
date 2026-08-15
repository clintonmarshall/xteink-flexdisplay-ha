from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


DISPLAY_PROFILE_SCHEMA_VERSION = 1
DISPLAY_PROFILE_STORE_VERSION = 1
MAX_CUSTOM_PROFILES = 32
MAX_ALIASES = 12
MAX_DIMENSION = 2048
MAX_PIXELS = 2_097_152

PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
HARDWARE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._ /-]{0,47}$")
PIXEL_FORMATS = {"RGB565": 16, "RGB888": 24}
SHAPES = {"rect", "round"}


class DisplayProfileValidationError(ValueError):
    """Raised when a display profile is unsafe or ambiguous."""


class DisplayProfileStateError(RuntimeError):
    """Raised when persisted profile state cannot be trusted."""


@dataclass(frozen=True, slots=True)
class DisplayProfile:
    """Versioned physical and renderer capabilities for one display family."""

    id: str
    display_name: str
    model: str
    technology: Literal["eink", "color"]
    width: int
    height: int
    shape: Literal["rect", "round"]
    pixel_format: str
    color_depth: int
    touch: bool
    lvgl: bool
    display_controller: str = ""
    touch_controller: str = ""
    mcu: str = ""
    flash_bytes: int = 0
    psram_bytes: int = 0
    aliases: tuple[str, ...] = ()
    builtin: bool = False
    version: int = DISPLAY_PROFILE_SCHEMA_VERSION

    @property
    def is_color(self) -> bool:
        return self.technology == "color"

    @property
    def resolution(self) -> tuple[int, int]:
        return self.width, self.height

    def to_payload(self) -> dict[str, Any]:
        return profile_payload(self)


X3_PROFILE = DisplayProfile(
    id="x3",
    display_name="XTEINK X3",
    model="XTEINK_X3",
    technology="eink",
    width=528,
    height=792,
    shape="rect",
    pixel_format="MONO1",
    color_depth=1,
    touch=False,
    lvgl=False,
    aliases=("x3", "xteink_x3", "xteink-x3", "xteink x3"),
    builtin=True,
)

X4_PROFILE = DisplayProfile(
    id="x4",
    display_name="XTEINK X4",
    model="XTEINK_X4",
    technology="eink",
    width=480,
    height=800,
    shape="rect",
    pixel_format="MONO1",
    color_depth=1,
    touch=False,
    lvgl=False,
    aliases=("x4", "xteink_x4", "xteink-x4", "xteink x4"),
    builtin=True,
)

X4_PRO_PROFILE = DisplayProfile(
    id="x4_pro",
    display_name="XTEINK X4 Pro",
    model="X4_PRO",
    technology="eink",
    width=480,
    height=800,
    shape="rect",
    pixel_format="MONO1",
    color_depth=1,
    touch=False,
    lvgl=False,
    aliases=(
        "x4_pro",
        "x4-pro",
        "x4 pro",
        "xteink_x4_pro",
        "xteink-x4-pro",
        "xteink x4 pro",
    ),
    builtin=True,
)

JC3636_PROFILE = DisplayProfile(
    id="jc3636",
    display_name="JC3636W518EN",
    model="JC3636W518EN",
    technology="color",
    width=360,
    height=360,
    shape="round",
    pixel_format="RGB565",
    color_depth=16,
    touch=True,
    lvgl=True,
    display_controller="ST77916",
    touch_controller="CST816S",
    mcu="ESP32-S3",
    flash_bytes=16 * 1024 * 1024,
    psram_bytes=8 * 1024 * 1024,
    aliases=(
        "jc3636w518",
        "jc3636w518en",
        "jc3636-w518",
        "guition-jc3636w518",
        "taichi-pi",
    ),
    builtin=True,
)

BUILTIN_DISPLAY_PROFILES: dict[str, DisplayProfile] = {
    profile.id: profile
    for profile in (X3_PROFILE, X4_PROFILE, X4_PRO_PROFILE, JC3636_PROFILE)
}

# Known non-LVGL families must not be redefined as custom colour receivers.
# Keys use the same separator-insensitive normalization as profile aliases.
RESERVED_NON_LVGL_IDENTIFIERS = frozenset(
    {
        "x3",
        "xteinkx3",
        "x4",
        "xteinkx4",
        "x4pro",
        "xteinkx4pro",
        "n4",
        "note4",
        "zectrixnote4",
        "rook",
        "echospot",
        "echospot2017",
        "amazonechospot",
        "checkers",
        "echoshow5",
        "echoshow52019",
        "amazonechoshow5",
    }
)


def _normalise_key(value: Any) -> str:
    selected = str(value or "").strip().casefold()
    return re.sub(r"[^a-z0-9]+", "", selected)


def _bounded_hardware_name(value: Any, field: str, *, required: bool = False) -> str:
    selected = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not selected:
        if required:
            raise DisplayProfileValidationError(f"{field} is required")
        return ""
    if not HARDWARE_NAME_PATTERN.fullmatch(selected):
        raise DisplayProfileValidationError(f"{field} contains unsupported characters")
    return selected


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DisplayProfileValidationError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise DisplayProfileValidationError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _profile_keys(profile: DisplayProfile) -> set[str]:
    return {
        key
        for key in (
            _normalise_key(profile.id),
            _normalise_key(profile.model),
            _normalise_key(profile.display_name),
            *(_normalise_key(alias) for alias in profile.aliases),
        )
        if key
    }


def profile_payload(profile: DisplayProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "version": profile.version,
        "display_name": profile.display_name,
        "model": profile.model,
        "technology": profile.technology,
        "width": profile.width,
        "height": profile.height,
        "shape": profile.shape,
        "pixel_format": profile.pixel_format,
        "color_depth": profile.color_depth,
        "touch": profile.touch,
        "lvgl": profile.lvgl,
        "display_controller": profile.display_controller,
        "touch_controller": profile.touch_controller,
        "mcu": profile.mcu,
        "flash_bytes": profile.flash_bytes,
        "psram_bytes": profile.psram_bytes,
        "aliases": list(profile.aliases),
        "builtin": profile.builtin,
    }


def parse_custom_profile(profile_id: str, payload: dict[str, Any]) -> DisplayProfile:
    """Validate a custom colour/LVGL profile without executable configuration."""

    selected_id = str(profile_id or "").strip().lower()
    if not PROFILE_ID_PATTERN.fullmatch(selected_id):
        raise DisplayProfileValidationError(
            "Profile IDs may contain lowercase letters, numbers, underscores, and hyphens"
        )
    if not isinstance(payload, dict):
        raise DisplayProfileValidationError("Display profile payload must be an object")
    payload_id = str(payload.get("id") or selected_id).strip().lower()
    if payload_id != selected_id:
        raise DisplayProfileValidationError("Payload ID must match the profile URL ID")
    version = _integer(
        payload.get("version", DISPLAY_PROFILE_SCHEMA_VERSION),
        "version",
        DISPLAY_PROFILE_SCHEMA_VERSION,
        DISPLAY_PROFILE_SCHEMA_VERSION,
    )
    technology = str(payload.get("technology") or "color").strip().lower()
    if technology != "color":
        raise DisplayProfileValidationError("Custom profiles must target a colour display")
    if payload.get("lvgl", True) is not True:
        raise DisplayProfileValidationError("Custom colour profiles must use LVGL")

    width = _integer(payload.get("width"), "width", 128, MAX_DIMENSION)
    height = _integer(payload.get("height"), "height", 128, MAX_DIMENSION)
    if width * height > MAX_PIXELS:
        raise DisplayProfileValidationError("Display profile has too many pixels")
    shape = str(payload.get("shape") or "rect").strip().lower()
    if shape not in SHAPES:
        raise DisplayProfileValidationError("Display shape must be rect or round")
    if shape == "round" and width != height:
        raise DisplayProfileValidationError("Round display profiles must be square")

    pixel_format = str(payload.get("pixel_format") or "RGB565").strip().upper()
    if pixel_format not in PIXEL_FORMATS:
        raise DisplayProfileValidationError("Custom LVGL profiles support RGB565 or RGB888")
    expected_depth = PIXEL_FORMATS[pixel_format]
    color_depth = _integer(
        payload.get("color_depth", expected_depth),
        "color_depth",
        expected_depth,
        expected_depth,
    )
    touch = payload.get("touch", False)
    if not isinstance(touch, bool):
        raise DisplayProfileValidationError("touch must be a boolean")
    touch_controller = _bounded_hardware_name(
        payload.get("touch_controller"), "touch_controller"
    )
    if touch and not touch_controller:
        raise DisplayProfileValidationError(
            "touch_controller is required when touch is enabled"
        )
    if not touch and touch_controller:
        raise DisplayProfileValidationError(
            "touch_controller must be empty when touch is disabled"
        )

    raw_aliases = payload.get("aliases") or []
    if not isinstance(raw_aliases, list) or len(raw_aliases) > MAX_ALIASES:
        raise DisplayProfileValidationError(
            f"aliases must contain at most {MAX_ALIASES} values"
        )
    aliases: list[str] = []
    seen_aliases: set[str] = set()
    for raw_alias in raw_aliases:
        alias = _bounded_hardware_name(raw_alias, "alias", required=True)
        key = _normalise_key(alias)
        if not key or key in seen_aliases or key == _normalise_key(selected_id):
            continue
        seen_aliases.add(key)
        aliases.append(alias)

    flash_bytes = _integer(
        payload.get("flash_bytes", 0), "flash_bytes", 0, 64 * 1024 * 1024
    )
    psram_bytes = _integer(
        payload.get("psram_bytes", 0), "psram_bytes", 0, 32 * 1024 * 1024
    )
    profile = DisplayProfile(
        id=selected_id,
        version=version,
        display_name=_bounded_hardware_name(
            payload.get("display_name") or selected_id,
            "display_name",
            required=True,
        ),
        model=_bounded_hardware_name(
            payload.get("model") or selected_id.upper(), "model", required=True
        ),
        technology="color",
        width=width,
        height=height,
        shape=shape,
        pixel_format=pixel_format,
        color_depth=color_depth,
        touch=touch,
        lvgl=True,
        display_controller=_bounded_hardware_name(
            payload.get("display_controller"),
            "display_controller",
            required=True,
        ),
        touch_controller=touch_controller,
        mcu=_bounded_hardware_name(payload.get("mcu"), "mcu"),
        flash_bytes=flash_bytes,
        psram_bytes=psram_bytes,
        aliases=tuple(aliases),
        builtin=False,
    )
    conflicts = sorted(_profile_keys(profile).intersection(RESERVED_NON_LVGL_IDENTIFIERS))
    if conflicts:
        raise DisplayProfileValidationError(
            "Custom colour profile identity conflicts with a known non-LVGL device family"
        )
    return profile


class DisplayProfileStore:
    """Thread-safe built-in registry plus atomically persisted custom profiles."""

    def __init__(self, path: str | Path, *, load: bool = True):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._custom: dict[str, DisplayProfile] = {}
        if load:
            self._load()

    def _profiles(self) -> dict[str, DisplayProfile]:
        return {**BUILTIN_DISPLAY_PROFILES, **self._custom}

    @staticmethod
    def _alias_index(profiles: dict[str, DisplayProfile]) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for profile_id, profile in profiles.items():
            for key in _profile_keys(profile):
                existing = aliases.get(key)
                if existing is not None and existing != profile_id:
                    raise DisplayProfileValidationError(
                        f"Display alias {key!r} is already owned by {existing}"
                    )
                aliases[key] = profile_id
        return aliases

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as err:
            raise DisplayProfileStateError(
                f"Display profile state is unreadable: {self.path}"
            ) from err
        if (
            not isinstance(document, dict)
            or document.get("version") != DISPLAY_PROFILE_STORE_VERSION
            or not isinstance(document.get("profiles"), dict)
        ):
            raise DisplayProfileStateError(
                f"Display profile state has an unsupported schema: {self.path}"
            )
        if len(document["profiles"]) > MAX_CUSTOM_PROFILES:
            raise DisplayProfileStateError(
                f"Display profile state exceeds the {MAX_CUSTOM_PROFILES}-profile limit"
            )
        loaded: dict[str, DisplayProfile] = {}
        for profile_id, raw_profile in sorted(document["profiles"].items()):
            if not isinstance(raw_profile, dict):
                raise DisplayProfileStateError(
                    f"Display profile {profile_id!r} is not an object"
                )
            try:
                profile = parse_custom_profile(str(profile_id), raw_profile)
                self._alias_index(
                    {**BUILTIN_DISPLAY_PROFILES, **loaded, profile.id: profile}
                )
            except DisplayProfileValidationError as err:
                raise DisplayProfileStateError(
                    f"Display profile {profile_id!r} is invalid"
                ) from err
            loaded[profile.id] = profile
        self._custom = loaded

    def _save(self, custom: dict[str, DisplayProfile] | None = None) -> None:
        selected = self._custom if custom is None else custom
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {
                        "version": DISPLAY_PROFILE_STORE_VERSION,
                        "profiles": {
                            profile_id: profile_payload(profile)
                            for profile_id, profile in sorted(selected.items())
                        },
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def payload(self) -> dict[str, Any]:
        with self._lock:
            profiles = self._profiles()
            aliases = self._alias_index(profiles)
            return {
                "version": DISPLAY_PROFILE_STORE_VERSION,
                "profiles": [
                    profile_payload(profiles[profile_id])
                    for profile_id in sorted(profiles)
                ],
                "aliases": dict(sorted(aliases.items())),
                "capabilities": {
                    "custom_profiles": True,
                    "custom_technology": "color",
                    "custom_ui": "lvgl",
                    "pixel_formats": sorted(PIXEL_FORMATS),
                    "shapes": sorted(SHAPES),
                    "maximum_custom_profiles": MAX_CUSTOM_PROFILES,
                    "maximum_dimension": MAX_DIMENSION,
                    "maximum_pixels": MAX_PIXELS,
                },
            }

    def get(self, profile_id: str) -> DisplayProfile | None:
        with self._lock:
            return self._profiles().get(str(profile_id or "").strip().lower())

    def resolve(
        self,
        identifier: str | None,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> DisplayProfile | None:
        """Resolve a canonical ID, model, alias, or unambiguous resolution."""

        with self._lock:
            profiles = self._profiles()
            key = _normalise_key(identifier)
            if key:
                profile_id = self._alias_index(profiles).get(key)
                if profile_id:
                    return profiles[profile_id]
            if width is None or height is None:
                return None
            matches = [
                profile
                for profile in profiles.values()
                if profile.width == width and profile.height == height
            ]
            return matches[0] if len(matches) == 1 else None

    def put(self, profile_id: str, payload: dict[str, Any]) -> DisplayProfile:
        profile = parse_custom_profile(profile_id, payload)
        if profile.id in BUILTIN_DISPLAY_PROFILES:
            raise DisplayProfileValidationError("Built-in display profiles are immutable")
        with self._lock:
            if profile.id not in self._custom and len(self._custom) >= MAX_CUSTOM_PROFILES:
                raise DisplayProfileValidationError("Too many custom display profiles")
            proposed_custom = {**self._custom, profile.id: profile}
            self._alias_index({**BUILTIN_DISPLAY_PROFILES, **proposed_custom})
            self._save(proposed_custom)
            self._custom = proposed_custom
            return profile

    def delete(self, profile_id: str) -> None:
        selected = str(profile_id or "").strip().lower()
        if selected in BUILTIN_DISPLAY_PROFILES:
            raise DisplayProfileValidationError("Built-in display profiles are immutable")
        with self._lock:
            if selected not in self._custom:
                raise KeyError(selected)
            proposed_custom = {
                profile_id: profile
                for profile_id, profile in self._custom.items()
                if profile_id != selected
            }
            self._save(proposed_custom)
            self._custom = proposed_custom
