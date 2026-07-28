from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, UnidentifiedImageError

from .config import EntityConfig, HomeAssistantConfig

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


@dataclass(frozen=True)
class EntityState:
    entity_id: str
    label: str
    state: str
    unit: str
    available: bool
    icon: str = "auto"
    style: str = "value"
    minimum: float = 0.0
    maximum: float = 100.0
    history: tuple[float, ...] = ()
    last_changed: datetime | None = None
    image_bytes: bytes = b""
    image_fit: str = "cover"
    badge_theme: str = "classic"
    text_scale: int = 100
    qr_scale: int = 100


class HomeAssistantClient:
    def __init__(self, config: HomeAssistantConfig):
        self.config = config
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.config.token)

    def fetch(self, entities: Iterable[EntityConfig]) -> tuple[list[EntityState], str]:
        selected_entities = tuple(entities)
        results: list[EntityState] = []
        error = ""
        for entity in selected_entities:
            if entity.source == "static":
                results.append(
                    EntityState(
                        entity.entity_id,
                        entity.label,
                        entity.value,
                        entity.unit,
                        True,
                        entity.icon,
                        entity.style,
                        entity.minimum,
                        entity.maximum,
                        image_fit=entity.image_fit,
                        text_scale=entity.text_scale,
                        qr_scale=entity.qr_scale,
                    )
                )
                continue
            # device.* values are synthetic FlexDisplay telemetry. They are
            # resolved by dashboards.py and must never be requested from HA.
            if entity.entity_id.startswith("device."):
                continue
            if entity.style == "image" and entity.image_url:
                try:
                    image = self._download_image(entity.image_url, authenticated=False)
                    results.append(self._image_state(entity, image))
                except (requests.RequestException, ValueError) as exc:
                    if not error:
                        error = f"Image request failed: {exc}"
                    results.append(self._unavailable(entity))
                continue
            if not self.config.token:
                if not error:
                    error = "HA token not configured"
                results.append(self._unavailable(entity))
                continue
            try:
                response = self.session.get(
                    f"{self.config.base_url}/api/states/{entity.entity_id}",
                    headers=self._headers(),
                    timeout=self.config.timeout_seconds,
                    verify=self.config.verify_tls,
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                attributes = payload.get("attributes") or {}
                unit = entity.unit or str(attributes.get("unit_of_measurement") or "")
                image = b""
                if entity.style == "image":
                    entity_picture = str(attributes.get("entity_picture") or "")
                    if not entity_picture:
                        raise ValueError(f"{entity.entity_id} does not expose an entity picture")
                    image_url = self._entity_picture_url(entity_picture)
                    image = self._download_image(
                        image_url,
                        authenticated=self._same_origin(image_url, self.config.base_url),
                    )
                results.append(
                    EntityState(
                        entity.entity_id,
                        entity.label,
                        str(payload.get("state") or "--"),
                        unit,
                        True,
                        entity.icon,
                        entity.style,
                        entity.minimum,
                        entity.maximum,
                        self._history(entity.entity_id) if entity.style == "history" else (),
                        self._timestamp(payload.get("last_changed")),
                        image,
                        entity.image_fit,
                        text_scale=entity.text_scale,
                        qr_scale=entity.qr_scale,
                    )
                )
            except (requests.RequestException, ValueError) as exc:
                if not error:
                    error = f"Home Assistant request failed: {exc}"
                results.append(self._unavailable(entity))
        return results, error

    @staticmethod
    def _same_origin(first: str, second: str) -> bool:
        left = urlparse(first)
        right = urlparse(second)
        return (
            left.scheme.lower(),
            left.hostname,
            left.port or (443 if left.scheme.lower() == "https" else 80),
        ) == (
            right.scheme.lower(),
            right.hostname,
            right.port or (443 if right.scheme.lower() == "https" else 80),
        )

    def _entity_picture_url(self, entity_picture: str) -> str:
        """Resolve HA image paths without escaping an internal API proxy prefix."""
        picture = urlparse(entity_picture)
        if picture.scheme or picture.netloc:
            return entity_picture

        base = urlparse(self.config.base_url)
        base_path = base.path.rstrip("/")
        if base_path and picture.path.startswith("/api/"):
            return base._replace(
                path=f"{base_path}{picture.path}",
                params="",
                query=picture.query,
                fragment=picture.fragment,
            ).geturl()
        return urljoin(f"{self.config.base_url}/", entity_picture)

    def _download_image(self, url: str, *, authenticated: bool) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("image URL must use http:// or https://")
        response = self.session.get(
            url,
            headers=self._headers() if authenticated else {"Accept": "image/*"},
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_tls,
            stream=True,
        )
        response.raise_for_status()
        final_url = str(getattr(response, "url", url))
        if urlparse(final_url).scheme not in {"http", "https"}:
            raise ValueError("image redirect used an unsupported URL scheme")
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if content_type and not content_type.startswith("image/"):
            raise ValueError(f"image URL returned {content_type}")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_IMAGE_BYTES:
            raise ValueError("image is larger than 8 MB")
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > MAX_IMAGE_BYTES:
                raise ValueError("image is larger than 8 MB")
        if not content:
            raise ValueError("image response was empty")
        try:
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("image dimensions are too large")
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("response was not a readable image") from exc
        return bytes(content)

    @staticmethod
    def _image_state(entity: EntityConfig, image: bytes) -> EntityState:
        return EntityState(
            entity.entity_id,
            entity.label,
            "Image",
            "",
            True,
            entity.icon,
            entity.style,
            entity.minimum,
            entity.maximum,
            (),
            None,
            image,
            entity.image_fit,
            text_scale=entity.text_scale,
            qr_scale=entity.qr_scale,
        )

    @staticmethod
    def _unavailable(entity: EntityConfig) -> EntityState:
        return EntityState(
            entity.entity_id,
            entity.label,
            "--",
            entity.unit,
            False,
            entity.icon,
            entity.style,
            entity.minimum,
            entity.maximum,
            (),
            None,
            b"",
            entity.image_fit,
            text_scale=entity.text_scale,
            qr_scale=entity.qr_scale,
        )

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }

    def _history(self, entity_id: str) -> tuple[float, ...]:
        """Fetch a compact 24-hour numeric series for an e-ink sparkline."""
        start = (datetime.now(UTC) - timedelta(hours=24)).isoformat(timespec="seconds")
        try:
            response = self.session.get(
                f"{self.config.base_url}/api/history/period/{start}",
                headers=self._headers(),
                params={
                    "filter_entity_id": entity_id,
                    "minimal_response": "true",
                    "no_attributes": "true",
                    "significant_changes_only": "true",
                },
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
            response.raise_for_status()
            payload = response.json()
            values: list[float] = []
            if isinstance(payload, list) and payload and isinstance(payload[0], list):
                for sample in payload[0]:
                    try:
                        values.append(float(sample.get("state")))
                    except (AttributeError, TypeError, ValueError):
                        continue
            if len(values) <= 32:
                return tuple(values)
            step = (len(values) - 1) / 31
            return tuple(values[round(index * step)] for index in range(32))
        except (requests.RequestException, ValueError):
            return ()

    def catalog(self) -> tuple[list[dict[str, Any]], str]:
        """Return the entity metadata needed by Dashboard Studio."""
        if not self.config.token:
            return [], "HA token not configured"
        try:
            response = self.session.get(
                f"{self.config.base_url}/api/states",
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
            response.raise_for_status()
            payload = response.json()
            entities = []
            for item in payload if isinstance(payload, list) else []:
                entity_id = str(item.get("entity_id") or "")
                if not entity_id or "." not in entity_id:
                    continue
                attributes = item.get("attributes") or {}
                entities.append(
                    {
                        "entity_id": entity_id,
                        "label": str(attributes.get("friendly_name") or entity_id),
                        "state": str(item.get("state") or "--"),
                        "unit": str(attributes.get("unit_of_measurement") or ""),
                        "icon": str(attributes.get("icon") or ""),
                        "device_class": str(attributes.get("device_class") or ""),
                        "domain": entity_id.split(".", 1)[0],
                    }
                )
            entities.sort(key=lambda item: (item["domain"], item["label"].lower()))
            return entities, ""
        except (requests.RequestException, ValueError) as exc:
            return [], f"Home Assistant request failed: {exc}"

    def service_catalog(self) -> tuple[list[str], str]:
        """Return callable domain.service names for Dashboard Studio."""
        if not self.config.token:
            return [], "HA token not configured"
        try:
            response = self.session.get(
                f"{self.config.base_url}/api/services",
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Home Assistant returned an invalid service catalogue")
            services: list[str] = []
            for domain in payload:
                if not isinstance(domain, dict):
                    continue
                domain_name = str(domain.get("domain") or "")
                domain_services = domain.get("services")
                if not domain_name or not isinstance(domain_services, dict):
                    continue
                services.extend(f"{domain_name}.{service}" for service in domain_services)
            return sorted(services), ""
        except (requests.RequestException, ValueError) as exc:
            return [], f"Home Assistant service request failed: {exc}"

    def call_service(
        self,
        service: str,
        entity_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Call one pre-validated Home Assistant service."""
        if not self.config.token:
            return False, "HA token not configured"
        if "." not in service:
            return False, "Invalid Home Assistant service"
        domain, service_name = service.split(".", 1)
        payload = dict(data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        try:
            response = self.session.post(
                f"{self.config.base_url}/api/services/{domain}/{service_name}",
                headers=self._headers(),
                json=payload,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
            response.raise_for_status()
            return True, f"called {service}"
        except requests.RequestException as exc:
            return False, f"Home Assistant service failed: {exc}"
