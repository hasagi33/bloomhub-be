"""Entity extraction + linking for AI assistant responses.

Tool results carry rows that frontend may want to render as clickable chips
(or inline links inside the assistant message). This module:

1. Pulls candidate entities out of a tool result by inspecting known keys.
2. Scans a free-form assistant response for those entity names and emits
   character offset spans so the frontend can wrap them in anchors.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

EntityType = str  # "employee" | "leave_request" | "asset" | "document" | etc.


def _employee_record(row: dict[str, Any]) -> dict[str, Any] | None:
    employee_id = row.get("id") or row.get("employee_id")
    if not employee_id:
        return None
    name = (
        row.get("full_name")
        or row.get("name")
        or row.get("email")
        or row.get("username")
    )
    if not name:
        return None
    return {
        "type": "employee",
        "id": employee_id,
        "name": str(name),
        "email": row.get("email"),
        "url": f"/employees/{employee_id}",
    }


def _generic_record(
    row: dict[str, Any],
    *,
    entity_type: str,
    url_prefix: str,
    name_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    entity_id = row.get("id")
    if not entity_id:
        return None
    name = next((row.get(k) for k in name_keys if row.get(k)), None)
    if not name:
        return None
    return {
        "type": entity_type,
        "id": entity_id,
        "name": str(name),
        "url": f"{url_prefix}/{entity_id}",
    }


_EMPLOYEE_KEYS = ("employees",)
_LEAVE_KEYS = ("leave_requests",)
_ASSET_KEYS = ("assets",)
_DOCUMENT_KEYS = ("documents",)
_DOCUMENT_TEMPLATE_KEYS = ("document_templates", "templates")
_NOTIFICATION_KEYS = ("notifications",)
_TIME_ENTRY_KEYS = ("time_entries",)


def collect_entities(result: Any) -> list[dict[str, Any]]:
    """Walk a tool result dict, extract clickable entity descriptors."""
    if not isinstance(result, dict):
        return []
    entities: list[dict[str, Any]] = []

    for key in _EMPLOYEE_KEYS:
        for row in _rows(result.get(key)):
            entity = _employee_record(row)
            if entity:
                entities.append(entity)

    # Standalone single-employee tool results (get_employee_profile / managers)
    for row in _rows(result.get("employee")):
        entity = _employee_record(row)
        if entity:
            entities.append(entity)
    for row in (
        result.get("employees", []) if isinstance(result.get("employees"), list) else []
    ):
        for manager in _rows(row.get("managers") if isinstance(row, dict) else None):
            entity = _employee_record(manager)
            if entity:
                entities.append(entity)

    for key in _LEAVE_KEYS:
        for row in _rows(result.get(key)):
            entity = _generic_record(
                row,
                entity_type="leave_request",
                url_prefix="/leave/requests",
                name_keys=("leave_type", "summary", "reason"),
            )
            if entity:
                entities.append(entity)

    for key in _ASSET_KEYS:
        for row in _rows(result.get(key)):
            entity = _generic_record(
                row,
                entity_type="asset",
                url_prefix="/assets",
                name_keys=("name", "asset_id", "serial_number"),
            )
            if entity:
                entities.append(entity)

    for key in _DOCUMENT_KEYS:
        for row in _rows(result.get(key)):
            entity = _generic_record(
                row,
                entity_type="document",
                url_prefix="/documents",
                name_keys=("name", "title"),
            )
            if entity:
                entities.append(entity)

    for key in _DOCUMENT_TEMPLATE_KEYS:
        for row in _rows(result.get(key)):
            entity = _generic_record(
                row,
                entity_type="document_template",
                url_prefix="/documents/templates",
                name_keys=("name", "title"),
            )
            if entity:
                entities.append(entity)

    for key in _TIME_ENTRY_KEYS:
        for row in _rows(result.get(key)):
            entity = _generic_record(
                row,
                entity_type="time_entry",
                url_prefix="/time/entries",
                name_keys=("description", "project_name"),
            )
            if entity:
                entities.append(entity)

    for key in _NOTIFICATION_KEYS:
        for row in _rows(result.get(key)):
            entity = _generic_record(
                row,
                entity_type="notification",
                url_prefix="/notifications",
                name_keys=("title", "message"),
            )
            if entity:
                entities.append(entity)

    return _dedupe(entities)


def _rows(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                yield row


def _dedupe(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, Any]] = set()
    out: list[dict[str, Any]] = []
    for entity in entities:
        key = (entity["type"], entity["id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(entity)
    return out


def find_spans(text: str, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find non-overlapping case-insensitive occurrences of entity names in text.

    Longest names matched first so "Senad Halilović" wins over "Senad". Returns
    spans with `start`, `end`, and the entity payload.
    """
    if not text or not entities:
        return []

    candidates = sorted(
        entities,
        key=lambda e: len(e.get("name") or ""),
        reverse=True,
    )
    occupied: list[tuple[int, int]] = []
    spans: list[dict[str, Any]] = []

    for entity in candidates:
        name = (entity.get("name") or "").strip()
        if len(name) < 2:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if _overlaps(start, end, occupied):
                continue
            occupied.append((start, end))
            spans.append(
                {
                    "type": entity["type"],
                    "id": entity["id"],
                    "url": entity.get("url"),
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                }
            )

    spans.sort(key=lambda s: s["start"])
    return spans


def _overlaps(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
    for o_start, o_end in occupied:
        if not (end <= o_start or start >= o_end):
            return True
    return False
