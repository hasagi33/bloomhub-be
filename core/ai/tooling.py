from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.ai.entities import collect_entities
from core.ai.errors import SensitiveActionDenied, ToolArgumentError
from core.models import AIChatSession, AIToolCallLog

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., dict[str, Any]]
PermissionCheck = Callable[[Any], tuple[bool, str]]
"""Returns (can_run, reason). reason is human-readable; empty if can_run."""


try:
    from pydantic import BaseModel as _PydanticBaseModel
    from pydantic import ValidationError as _PydanticValidationError
except Exception:  # pragma: no cover
    _PydanticBaseModel = None
    _PydanticValidationError = Exception


@dataclass(frozen=True)
class AssistantTool:
    name: str
    description: str
    handler: ToolHandler
    module: str = "general"
    mutating: bool = False
    sensitive: bool = False
    args_schema: type | None = None
    examples: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    confirmation_label: str = ""
    confirmation_help: str = ""
    permission_check: PermissionCheck | None = None
    ui_path: str = ""  # e.g. "/people/new" — where in the app users do this manually
    required_permissions: tuple[str, ...] = field(default_factory=tuple)
    workflow_topic: str = ""  # links to WORKFLOWS registry key

    @property
    def requires_confirmation(self) -> bool:
        return self.mutating

    @property
    def requires_recent_auth(self) -> bool:
        return self.sensitive


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AssistantTool] = {}

    def register(self, tool: AssistantTool) -> AssistantTool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> AssistantTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValidationError({"tool": f"Unknown assistant tool: {name}"}) from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def public_manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "module": tool.module,
                "mutating": tool.mutating,
                "sensitive": tool.sensitive,
                "requires_confirmation": tool.requires_confirmation,
                "args_schema": _schema_to_dict(tool.args_schema),
                "confirmation_label": tool.confirmation_label,
                "confirmation_help": tool.confirmation_help,
                "examples": list(tool.examples or ()),
                "ui_path": tool.ui_path,
                "required_permissions": list(tool.required_permissions or ()),
                "workflow_topic": tool.workflow_topic,
            }
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def values(self) -> list[AssistantTool]:
        return list(self._tools.values())

    def by_module(self) -> dict[str, list[AssistantTool]]:
        """Group registered tools by module name."""
        out: dict[str, list[AssistantTool]] = {}
        for tool in self._tools.values():
            out.setdefault(tool.module, []).append(tool)
        for module in out:
            out[module].sort(key=lambda t: t.name)
        return dict(sorted(out.items()))

    def module_counts(self) -> dict[str, dict[str, int]]:
        """Per-module tool counts split by read/mutating/sensitive."""
        counts: dict[str, dict[str, int]] = {}
        for module, tools in self.by_module().items():
            counts[module] = {
                "total": len(tools),
                "read": sum(1 for t in tools if not t.mutating),
                "mutating": sum(1 for t in tools if t.mutating),
                "sensitive": sum(1 for t in tools if t.sensitive),
            }
        return counts


def probe_permission(tool: AssistantTool, user) -> tuple[bool, str]:
    """Run a tool's permission predicate without invoking the handler.

    Default policy: tools without an explicit check are runnable by any
    authenticated user. The probe is purposefully forgiving so we do not
    accidentally hide actions the user actually owns.
    """
    if not getattr(user, "is_authenticated", False):
        return False, "Authentication is required."
    if tool.permission_check is None:
        return True, ""
    try:
        return tool.permission_check(user)
    except Exception as exc:  # noqa: BLE001 — surface as deny w/ reason
        return False, f"permission probe failed: {exc}"


def _schema_to_dict(schema: type | None) -> dict[str, Any] | None:
    if schema is None or _PydanticBaseModel is None:
        return None
    try:
        return schema.model_json_schema()
    except Exception:
        return None


def pending_confirmation_ttl_seconds() -> int:
    return int(getattr(settings, "AI_AGENT_PENDING_CONFIRMATION_TTL_SECONDS", 600))


def build_confirmation_payload(
    tool: AssistantTool,
    proposed_arguments: dict[str, Any],
) -> dict[str, Any]:
    """Payload the frontend uses to render a pre-filled, editable form."""
    now = timezone.now()
    expires_at = now + timedelta(seconds=pending_confirmation_ttl_seconds())
    return {
        "tool_name": tool.name,
        "module": tool.module,
        "mutating": tool.mutating,
        "sensitive": tool.sensitive,
        "description": tool.description,
        "confirmation_label": tool.confirmation_label or f"Run `{tool.name}`",
        "confirmation_help": tool.confirmation_help,
        "arguments": proposed_arguments,
        "proposed_arguments": proposed_arguments,
        "args_schema": _schema_to_dict(tool.args_schema),
        "examples": list(tool.examples or ()),
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def pending_is_expired(pending: dict[str, Any] | None) -> bool:
    if not pending:
        return True
    expires_at = pending.get("expires_at")
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return False
    return timezone.now() >= dt


def validate_arguments(
    tool: AssistantTool, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Validate raw tool arguments against the tool's Pydantic schema.

    Returns coerced kwargs ready to splat into the handler. Raises
    ToolArgumentError on schema mismatch.
    """
    if tool.args_schema is None or _PydanticBaseModel is None:
        return dict(arguments or {})
    try:
        model = tool.args_schema.model_validate(arguments or {})
    except _PydanticValidationError as exc:
        err = ToolArgumentError(f"Invalid arguments for `{tool.name}`: {exc}")
        err.pydantic_errors = exc.errors() if hasattr(exc, "errors") else []
        raise err from exc
    return model.model_dump(exclude_unset=False)


def _classify_validation_errors(
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split Pydantic errors into (missing_required, other_invalid)."""
    missing: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for err in errors or []:
        if err.get("type") == "missing":
            loc = err.get("loc") or ()
            field = ".".join(str(x) for x in loc) if loc else ""
            missing.append(
                {"field": field, "message": err.get("msg", "field required")}
            )
        else:
            loc = err.get("loc") or ()
            field = ".".join(str(x) for x in loc) if loc else ""
            invalid.append(
                {
                    "field": field,
                    "message": err.get("msg", "invalid value"),
                    "type": err.get("type", "value_error"),
                }
            )
    return missing, invalid


def build_slot_fill_payload(
    tool: AssistantTool,
    known_arguments: dict[str, Any],
    missing_fields: list[dict[str, Any]],
    invalid_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pending payload representing a partial mutating call awaiting missing data."""
    payload = build_confirmation_payload(tool, known_arguments)
    payload["requires_input"] = True
    payload["missing_fields"] = missing_fields
    payload["invalid_fields"] = invalid_fields or []
    field_names = [m["field"] for m in missing_fields if m.get("field")]
    if field_names:
        nice = ", ".join(f"`{f}`" for f in field_names)
        payload["question"] = (
            f"I need a few more details to run `{tool.name}`: please provide {nice}."
        )
    else:
        payload["question"] = (
            f"I need more details to run `{tool.name}`. Please fill in the form."
        )
    payload["confirmation_label"] = (
        f"Complete `{tool.name}`"
        if tool.confirmation_label
        else payload["confirmation_label"]
    )
    return payload


def _has_recent_auth(user) -> bool:
    max_age = getattr(settings, "AI_AGENT_SENSITIVE_RECENT_AUTH_SECONDS", 900)
    if max_age <= 0:
        return True
    last_login = getattr(user, "last_login", None)
    if last_login is None:
        return False
    delta = (timezone.now() - last_login).total_seconds()
    return delta <= max_age


def summarize_result(result: Any) -> str:
    if isinstance(result, dict):
        if "summary" in result:
            return str(result["summary"])[:1000]
        keys = ", ".join(list(result.keys())[:8])
        return f"Returned fields: {keys}"[:1000]
    return str(result)[:1000]


def log_tool_call(
    *,
    session: AIChatSession,
    user,
    tool_name: str,
    arguments: dict[str, Any],
    status: str,
    result: Any = None,
    error: str = "",
) -> None:
    AIToolCallLog.objects.create(
        session=session,
        user=user,
        tool_name=tool_name,
        arguments=arguments,
        status=status,
        result_summary=summarize_result(result) if result is not None else "",
        error=error,
    )


def execute_tool(
    *,
    registry: ToolRegistry,
    session: AIChatSession,
    user,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    arguments = arguments or {}
    tool = registry.get(tool_name)
    logger.warning(
        "[AI] tool.start session=%s user=%s tool=%s module=%s sensitive=%s mutating=%s args=%s",
        session.id,
        getattr(user, "id", None),
        tool.name,
        tool.module,
        tool.sensitive,
        tool.mutating,
        arguments,
    )

    try:
        validated_args = validate_arguments(tool, arguments)
    except ToolArgumentError as exc:
        pydantic_errors = getattr(exc, "pydantic_errors", []) or []
        missing, invalid = _classify_validation_errors(pydantic_errors)

        # Slot-fill flow: mutating tool invoked with partial args is a
        # legitimate intent — surface a pre-filled form pending the missing
        # fields rather than treating it as a hard failure. Hard-invalid
        # values still fail.
        if tool.mutating and missing and not invalid:
            payload = build_slot_fill_payload(tool, dict(arguments or {}), missing)
            session.pending_confirmation = payload
            session.save(update_fields=["pending_confirmation", "updated_at"])
            log_tool_call(
                session=session,
                user=user,
                tool_name=tool.name,
                arguments=arguments,
                status=AIToolCallLog.Status.PENDING_CONFIRMATION,
                result=payload,
            )
            logger.warning(
                "[AI] tool.needs_input session=%s tool=%s missing=%s",
                session.id,
                tool.name,
                [m["field"] for m in missing],
            )
            return {
                "requires_input": True,
                "requires_confirmation": True,
                "pending_confirmation": payload,
                "missing_fields": missing,
                "summary": payload["question"],
            }

        log_tool_call(
            session=session,
            user=user,
            tool_name=tool.name,
            arguments=arguments,
            status=AIToolCallLog.Status.ERROR,
            error=str(exc),
        )
        raise ValidationError({"arguments": str(exc)}) from exc

    # Sensitive gate is enforced only at execution time (when the user has
    # explicitly confirmed). Staging a mutating action via pending_confirmation
    # is harmless — it just shows the human-in-loop form — so we let it
    # through. For read-only sensitive tools (no confirmation step) we still
    # enforce the recent-auth window here.
    if tool.sensitive and not tool.mutating and not _has_recent_auth(user):
        log_tool_call(
            session=session,
            user=user,
            tool_name=tool.name,
            arguments=validated_args,
            status=AIToolCallLog.Status.BLOCKED,
            error="recent_auth_required",
        )
        raise SensitiveActionDenied(
            f"`{tool.name}` is sensitive; please re-authenticate before retrying."
        )
    if tool.sensitive and tool.mutating and confirmed and not _has_recent_auth(user):
        log_tool_call(
            session=session,
            user=user,
            tool_name=tool.name,
            arguments=validated_args,
            status=AIToolCallLog.Status.BLOCKED,
            error="recent_auth_required",
        )
        raise SensitiveActionDenied(
            f"`{tool.name}` is sensitive; please re-authenticate before confirming."
        )

    if tool.requires_confirmation and not confirmed:
        pending = build_confirmation_payload(tool, validated_args)
        session.pending_confirmation = pending
        session.save(update_fields=["pending_confirmation", "updated_at"])
        log_tool_call(
            session=session,
            user=user,
            tool_name=tool.name,
            arguments=validated_args,
            status=AIToolCallLog.Status.PENDING_CONFIRMATION,
            result=pending,
        )
        logger.warning(
            "[AI] tool.pending_confirmation session=%s tool=%s",
            session.id,
            tool.name,
        )
        return {
            "requires_confirmation": True,
            "pending_confirmation": pending,
            "summary": f"Please confirm before I run `{tool.name}`.",
        }

    try:
        result = tool.handler(user=user, **validated_args)
    except PermissionDenied as exc:
        log_tool_call(
            session=session,
            user=user,
            tool_name=tool.name,
            arguments=validated_args,
            status=AIToolCallLog.Status.BLOCKED,
            error=str(exc),
        )
        logger.warning(
            "[AI] tool.blocked session=%s tool=%s error=%s",
            session.id,
            tool.name,
            exc,
        )
        raise
    except ValidationError as exc:
        log_tool_call(
            session=session,
            user=user,
            tool_name=tool.name,
            arguments=validated_args,
            status=AIToolCallLog.Status.ERROR,
            error=str(exc),
        )
        logger.warning(
            "[AI] tool.invalid session=%s tool=%s error=%s",
            session.id,
            tool.name,
            exc,
        )
        raise
    except Exception as exc:
        log_tool_call(
            session=session,
            user=user,
            tool_name=tool.name,
            arguments=validated_args,
            status=AIToolCallLog.Status.ERROR,
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.exception(
            "[AI] tool.error session=%s tool=%s error=%s",
            session.id,
            tool.name,
            exc,
        )
        raise

    log_tool_call(
        session=session,
        user=user,
        tool_name=tool.name,
        arguments=validated_args,
        status=AIToolCallLog.Status.SUCCESS,
        result=result,
    )
    if confirmed and session.pending_confirmation:
        session.pending_confirmation = {}
        session.save(update_fields=["pending_confirmation", "updated_at"])

    # Accumulate clickable entities for the active turn so the API response
    # can surface them even when the orchestrated subagent obscures the raw
    # tool output behind a free-form message.
    extracted = collect_entities(result)
    if extracted and hasattr(session, "_ai_turn_entities"):
        session._ai_turn_entities.extend(extracted)
        if isinstance(result, dict) and "entities" not in result:
            result["entities"] = extracted

    logger.warning(
        "[AI] tool.success session=%s tool=%s summary=%s",
        session.id,
        tool.name,
        summarize_result(result),
    )
    return result
