from __future__ import annotations

from typing import Any
from unittest.mock import Mock


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_from_mapping(payload: dict[str, Any]) -> dict[str, int]:
    metrics: dict[str, int] = {}

    def set_metric(name: str, value: Any) -> None:
        coerced = _coerce_int(value)
        if coerced is not None:
            metrics[name] = coerced

    if "total_tokens" in payload:
        set_metric("total_tokens", payload.get("total_tokens"))
    if "prompt_tokens" in payload:
        set_metric("prompt_tokens", payload.get("prompt_tokens"))
    if "completion_tokens" in payload:
        set_metric("completion_tokens", payload.get("completion_tokens"))
    if "input_tokens" in payload:
        set_metric("prompt_tokens", payload.get("input_tokens"))
        set_metric("input_tokens", payload.get("input_tokens"))
    if "output_tokens" in payload:
        set_metric("completion_tokens", payload.get("output_tokens"))
        set_metric("output_tokens", payload.get("output_tokens"))

    nested = payload.get("token_usage")
    if isinstance(nested, dict):
        nested_metrics = _extract_from_mapping(nested)
        for key, value in nested_metrics.items():
            metrics.setdefault(key, value)

    nested = payload.get("usage_metadata")
    if isinstance(nested, dict):
        nested_metrics = _extract_from_mapping(nested)
        for key, value in nested_metrics.items():
            metrics.setdefault(key, value)

    nested = payload.get("usage")
    if isinstance(nested, dict):
        nested_metrics = _extract_from_mapping(nested)
        for key, value in nested_metrics.items():
            metrics.setdefault(key, value)

    return metrics


def extract_token_usage(payload: Any) -> dict[str, int]:
    """Best-effort token usage extraction from LangChain/OpenAI payloads."""

    seen: set[int] = set()
    stack: list[Any] = [payload]
    extracted: dict[str, int] = {}

    while stack:
        item = stack.pop()
        if item is None:
            continue
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)

        if isinstance(item, dict):
            mapped = _extract_from_mapping(item)
            for key, value in mapped.items():
                extracted.setdefault(key, value)
            for key in ("response_metadata", "usage_metadata", "llm_output"):
                nested = item.get(key)
                if nested is not None:
                    stack.append(nested)
            messages = item.get("messages")
            if isinstance(messages, list):
                stack.extend(messages)
            choices = item.get("choices")
            if isinstance(choices, list):
                stack.extend(choices)
            continue

        # unittest.mock objects fabricate attributes on access. Treat them as
        # opaque payloads so tests can stub response objects without creating
        # recursive walk explosions.
        if isinstance(item, Mock):
            continue

        for attr in ("response_metadata", "usage_metadata", "llm_output"):
            nested = getattr(item, attr, None)
            if nested is not None:
                stack.append(nested)

        message = getattr(item, "message", None)
        if message is not None:
            stack.append(message)

        choices = getattr(item, "choices", None)
        if isinstance(choices, list):
            stack.extend(choices)

    if "prompt_tokens" not in extracted and "input_tokens" in extracted:
        extracted["prompt_tokens"] = extracted["input_tokens"]
    if "completion_tokens" not in extracted and "output_tokens" in extracted:
        extracted["completion_tokens"] = extracted["output_tokens"]
    return extracted
