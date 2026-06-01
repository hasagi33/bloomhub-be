from __future__ import annotations

from typing import Any


def display_chat_message_content(
    *,
    role: str,
    content: str | None,
    metadata: dict[str, Any] | None = None,
) -> str:
    meta = metadata or {}
    if role == "user":
        if meta.get("confirm") is True:
            return "Confirm"

        tool_name = str(meta.get("tool_name") or "").strip()
        if tool_name == "confirm_pending_action":
            return "Confirm"
        if tool_name == "cancel_pending_action":
            return "Cancel"

        action = str(meta.get("action") or "").strip().lower()
        if action == "confirm":
            return "Confirm"
        if action == "cancel":
            return "Cancel"

    text = (content or "").strip()
    if text:
        return text

    return text
