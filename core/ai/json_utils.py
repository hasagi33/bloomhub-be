from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def make_json_safe(value: Any) -> Any:
    """Recursively convert AI payloads into JSONField/JSONRenderer-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return make_json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [make_json_safe(item) for item in value]
    return str(value)
