"""Pure helpers for reconciling dynamic Home Assistant entities."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any


def collect_new_entities(
    coordinator: Any,
    records: Iterable[Mapping[str, Any]],
    factory: Callable[[Any, str], Iterable[Any]],
    known_entity_ids: set[str],
) -> list[Any]:
    """Return capability-eligible entities that have not been added before."""
    additions: list[Any] = []
    for record in records:
        device_id = str(record.get("device_id") or "")
        if not device_id:
            continue
        for entity in factory(coordinator, device_id):
            unique_id = str(
                getattr(entity, "unique_id", None)
                or getattr(entity, "_attr_unique_id", None)
                or ""
            )
            if not unique_id or unique_id in known_entity_ids:
                continue
            known_entity_ids.add(unique_id)
            additions.append(entity)
    return additions
