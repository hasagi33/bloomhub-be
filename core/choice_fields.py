from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers


def _normalize_choice_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _flatten_choices(choices: Any):
    if isinstance(choices, Mapping):
        for key, value in choices.items():
            if isinstance(value, Mapping):
                yield from _flatten_choices(value)
            elif (
                isinstance(value, (list, tuple))
                and value
                and all(
                    isinstance(item, (list, tuple)) and len(item) == 2 for item in value
                )
            ):
                yield from _flatten_choices(value)
            else:
                yield key, value
        return

    for item in choices or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            key, value = item
            if isinstance(value, Mapping):
                yield from _flatten_choices(value)
            elif (
                isinstance(value, (list, tuple))
                and value
                and all(
                    isinstance(subitem, (list, tuple)) and len(subitem) == 2
                    for subitem in value
                )
            ):
                yield from _flatten_choices(value)
            else:
                yield key, value
        else:
            yield item, item


def _choice_candidates(key: Any, label: Any) -> set[str]:
    candidates = {
        _normalize_choice_text(key),
        _normalize_choice_text(label),
    }

    key_text = str(key or "").strip()
    label_text = str(label or "").strip()

    if key_text:
        candidates.add(_normalize_choice_text(key_text.replace("_", " ")))
        candidates.add(_normalize_choice_text(key_text.replace("-", " ")))
    if label_text:
        candidates.add(_normalize_choice_text(label_text.replace("_", " ")))
    return {candidate for candidate in candidates if candidate}


class CaseInsensitiveChoiceField(serializers.ChoiceField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._casefold_choice_map = self._build_casefold_choice_map()

    def _build_casefold_choice_map(self) -> dict[str, Any]:
        mapped: dict[str, Any] = {}
        for key, label in _flatten_choices(self.choices):
            for candidate in _choice_candidates(key, label):
                mapped.setdefault(candidate, key)
        return mapped

    def to_internal_value(self, data):
        if isinstance(data, str):
            mapped = self._casefold_choice_map.get(_normalize_choice_text(data))
            if mapped is not None:
                return mapped
        return super().to_internal_value(data)


class CaseInsensitiveMultipleChoiceField(serializers.MultipleChoiceField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._casefold_choice_map = self._build_casefold_choice_map()

    def _build_casefold_choice_map(self) -> dict[str, Any]:
        mapped: dict[str, Any] = {}
        for key, label in _flatten_choices(self.choices):
            for candidate in _choice_candidates(key, label):
                mapped.setdefault(candidate, key)
        return mapped

    def to_internal_value(self, data):
        if isinstance(data, str):
            data = [data]

        if isinstance(data, (list, tuple, set)):
            normalized = []
            for item in data:
                if isinstance(item, str):
                    mapped = self._casefold_choice_map.get(_normalize_choice_text(item))
                    if mapped is not None:
                        normalized.append(mapped)
                        continue
                normalized.append(item)
            data = normalized

        return super().to_internal_value(data)


def patch_serializer_choice_fields() -> None:
    serializers.ChoiceField = CaseInsensitiveChoiceField
    serializers.MultipleChoiceField = CaseInsensitiveMultipleChoiceField


patch_serializer_choice_fields()
