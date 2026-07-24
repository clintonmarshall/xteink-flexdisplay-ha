from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests

from .config import EntityConfig, HomeAssistantConfig


@dataclass(frozen=True)
class EntityState:
    entity_id: str
    label: str
    state: str
    unit: str
    available: bool


class HomeAssistantClient:
    def __init__(self, config: HomeAssistantConfig):
        self.config = config
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.config.token)

    def fetch(self, entities: Iterable[EntityConfig]) -> tuple[list[EntityState], str]:
        results: list[EntityState] = []
        if not self.config.token:
            for entity in entities:
                results.append(EntityState(entity.entity_id, entity.label, "--", entity.unit, False))
            return results, "HA token not configured"

        headers = {"Authorization": f"Bearer {self.config.token}", "Content-Type": "application/json"}
        error = ""
        for entity in entities:
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
                    )
                )
            except (requests.RequestException, ValueError) as exc:
                if not error:
                    error = f"Home Assistant request failed: {exc}"
                results.append(EntityState(entity.entity_id, entity.label, "--", entity.unit, False))
        return results, error
