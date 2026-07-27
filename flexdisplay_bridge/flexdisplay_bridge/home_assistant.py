from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from .config import EntityConfig, HomeAssistantConfig


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
        if not self.config.token:
            for entity in selected_entities:
                results.append(
                    EntityState(
                        entity.entity_id,
                        entity.label,
                        "--",
                        entity.unit,
                        False,
                        entity.icon,
                        entity.style,
                        entity.minimum,
                        entity.maximum,
                    )
                )
            needs_home_assistant = any(
                not entity.entity_id.startswith("device.") for entity in selected_entities
            )
            return results, "HA token not configured" if needs_home_assistant else ""

        headers = {"Authorization": f"Bearer {self.config.token}", "Content-Type": "application/json"}
        error = ""
        for entity in selected_entities:
            # device.* values are synthetic FlexDisplay telemetry. They are
            # resolved by dashboards.py and must never be requested from HA.
            if entity.entity_id.startswith("device."):
                continue
            try:
                response = self.session.get(
                    f"{self.config.base_url}/api/states/{entity.entity_id}",
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                    verify=self.config.verify_tls,
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                attributes = payload.get("attributes") or {}
                unit = entity.unit or str(attributes.get("unit_of_measurement") or "")
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
                    )
                )
            except (requests.RequestException, ValueError) as exc:
                if not error:
                    error = f"Home Assistant request failed: {exc}"
                results.append(
                    EntityState(
                        entity.entity_id,
                        entity.label,
                        "--",
                        entity.unit,
                        False,
                        entity.icon,
                        entity.style,
                        entity.minimum,
                        entity.maximum,
                    )
                )
        return results, error

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
