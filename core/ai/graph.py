from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from datetime import time as dt_time
from typing import Any, TypedDict

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.ai.entities import collect_entities, find_spans
from core.ai.errors import (
    LLMInvocationError,
    LLMParseError,
    LLMUnavailableError,
    OrchestratorRoutingError,
    PermanentAIError,
    SensitiveActionDenied,
    TransientAIError,
)
from core.ai.json_utils import make_json_safe
from core.ai.message_display import display_chat_message_content
from core.ai.subagents import (
    create_react_subagent,
    infer_module,
    invoke_subagent,
    module_manifest,
)
from core.ai.tooling import execute_tool, pending_is_expired
from core.ai.tools import registry
from core.ai.usage import extract_token_usage
from core.models import AIChatMessage, AIChatSession

_AFFIRMATIVE_BARE = frozenset(
    {
        "yes",
        "y",
        "yep",
        "yeah",
        "yup",
        "sure",
        "ok",
        "okay",
        "confirm",
        "confirmed",
        "proceed",
        "approve",
        "approved",
        "continue",
        "do it",
        "do that",
        "go ahead",
        "submit",
        "submit it",
        "go for it",
        "please do",
        "please proceed",
        "let's go",
        "lets go",
        "sounds good",
        "looks good",
        "all good",
        # Bosnian/Serbian/Croatian
        "da",
        "potvrdi",
        "potvrdjujem",
        "potvrđujem",
        "naravno",
        "hajde",
        "samo daj",
        "u redu",
        "ok je",
        "moze",
        "može",
    }
)

# Token / phrase that, appearing as the FIRST word(s) of a stripped message,
# marks the whole message as an affirmation (regardless of trailing detail).
_AFFIRMATIVE_PREFIXES = (
    "yes",
    "yeah",
    "yep",
    "yup",
    "ok",
    "okay",
    "sure",
    "confirm",
    "confirmed",
    "submit",
    "submit it",
    "go ahead",
    "go for it",
    "do it",
    "do that",
    "proceed",
    "approve",
    "approved",
    "continue",
    "please do",
    "please proceed",
    "sounds good",
    "looks good",
    "all good",
    "let's go",
    "lets go",
    "da",
    "naravno",
    "potvrdi",
    "potvrdjujem",
    "potvrđujem",
    "hajde",
    "samo daj",
    "u redu",
    "moze",
    "može",
)

_NEGATIVE_BARE = frozenset(
    {
        "no",
        "n",
        "nope",
        "nah",
        "cancel",
        "abort",
        "stop",
        "don't",
        "dont",
        "do not",
        "never mind",
        "nevermind",
        # Bosnian/Serbian/Croatian
        "ne",
        "otkazi",
        "otkaži",
        "stani",
        "nemoj",
        "prekini",
        "stop",
    }
)
_NEGATIVE_PREFIXES = (
    "no,",
    "no.",
    "no!",
    "no ",
    "cancel",
    "abort",
    "stop",
    "don't",
    "dont",
    "ne,",
    "ne.",
    "ne ",
    "nemoj",
    "otkazi",
    "otkaži",
    "stani",
    "prekini",
)
# Words that, if present anywhere in a candidate-affirmative message, veto it
# (e.g. "yes but actually no", "yes don't submit").
_NEGATION_VETO = (
    " not ",
    " no ",
    " don't ",
    " dont ",
    " never ",
    " cancel ",
    " abort ",
    " stop ",
    " nemoj ",
    " ne ",
    " otkazi ",
    " otkaži ",
)


def _normalize_msg(message: str) -> str:
    return (message or "").strip().lower().rstrip("?!. ").strip()


def _looks_affirmative(message: str) -> bool:
    s = _normalize_msg(message)
    if not s:
        return False
    if s in _AFFIRMATIVE_BARE:
        return True
    # First-token affirmative followed by anything (e.g. "yes, submit that").
    padded = f" {s} "
    if any(v in padded for v in _NEGATION_VETO):
        return False
    for prefix in _AFFIRMATIVE_PREFIXES:
        if s == prefix:
            return True
        if s.startswith(prefix + " ") or s.startswith(prefix + ","):
            return True
    return False


def _looks_negative(message: str) -> bool:
    s = _normalize_msg(message)
    if not s:
        return False
    if s in _NEGATIVE_BARE:
        return True
    for prefix in _NEGATIVE_PREFIXES:
        if s.startswith(prefix):
            return True
    return False


def classify_confirmation_response(message: str) -> str:
    """Classify a reply to a pending confirmation."""
    if _looks_affirmative(message):
        return "positive"
    if _looks_negative(message):
        return "negative"
    return "unknown"


logger = logging.getLogger(__name__)

try:
    import langgraph  # noqa: F401
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover
    langgraph = None
    END = None
    StateGraph = None

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None


class AssistantState(TypedDict, total=False):
    session_id: int
    message: str
    tool_name: str | None
    arguments: dict[str, Any]
    result: dict[str, Any]
    final_response: str
    requires_confirmation: bool
    iterations: int


class OrchestratedResult(TypedDict):
    module: str
    response_text: str
    result: dict[str, Any]


def _langgraph_available() -> bool:
    return langgraph is not None and StateGraph is not None and END is not None


def runtime_status() -> dict[str, Any]:
    has_key = bool(getattr(settings, "OPENROUTER_API_KEY", ""))
    return {
        "openrouter_api_key_configured": has_key,
        "langchain_openai_available": ChatOpenAI is not None,
        "langgraph_available": _langgraph_available(),
        "orchestrator_enabled": has_key
        and ChatOpenAI is not None
        and _langgraph_available(),
        "model": getattr(settings, "OPENROUTER_MODEL", None),
        "temperature": getattr(settings, "OPENROUTER_TEMPERATURE", None),
        "max_tokens": getattr(settings, "OPENROUTER_MAX_TOKENS", None),
        "max_tool_steps": getattr(settings, "AI_AGENT_MAX_TOOL_STEPS", None),
        "max_iterations": getattr(settings, "AI_AGENT_MAX_ITERATIONS", None),
    }


def get_llm(*, temperature: float | None = None, max_tokens: int | None = None):
    """Return the configured OpenRouter chat model when LangChain is installed."""
    if not getattr(settings, "OPENROUTER_API_KEY", ""):
        logger.warning("[AI] llm.disabled reason=no_openrouter_api_key")
        return None
    if ChatOpenAI is None:
        logger.warning("[AI] llm.disabled reason=langchain_openai_missing")
        return None
    return ChatOpenAI(
        model=settings.OPENROUTER_MODEL,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        temperature=(
            temperature
            if temperature is not None
            else getattr(settings, "OPENROUTER_TEMPERATURE", 0.1)
        ),
        max_tokens=(
            max_tokens
            if max_tokens is not None
            else getattr(settings, "OPENROUTER_MAX_TOKENS", 2048)
        ),
        timeout=getattr(settings, "OPENROUTER_REQUEST_TIMEOUT", 60),
        max_retries=0,  # retry handled here so we control backoff + error types
    )


_TRANSIENT_TOKENS = (
    "timeout",
    "timed out",
    "rate limit",
    "rate_limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "temporarily unavailable",
    "connection",
)


def _classify_llm_error(exc: BaseException) -> TransientAIError | PermanentAIError:
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(token in text for token in _TRANSIENT_TOKENS):
        return LLMInvocationError(str(exc))
    return LLMInvocationError(str(exc))  # default transient; let retry decide


def invoke_llm_with_retry(llm, prompt, *, retries: int | None = None):
    """Invoke an LLM with bounded retries on transient errors.

    Raises LLMInvocationError after exhausting retries.
    """
    if llm is None:
        raise LLMUnavailableError("LLM is not configured.")
    attempts = (
        retries
        if retries is not None
        else getattr(settings, "OPENROUTER_MAX_RETRIES", 2)
    ) + 1
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return llm.invoke(prompt)
        except Exception as exc:  # noqa: BLE001 — classified below
            last_exc = exc
            classified = _classify_llm_error(exc)
            logger.warning(
                "[AI] llm.invoke_error attempt=%s/%s type=%s msg=%s",
                attempt,
                attempts,
                type(exc).__name__,
                exc,
            )
            if attempt >= attempts or isinstance(classified, PermanentAIError):
                raise classified from exc
            time.sleep(min(2 ** (attempt - 1), 8))
    # unreachable; loop always raises or returns
    raise LLMInvocationError(str(last_exc) if last_exc else "unknown LLM failure")


def recent_history(
    session: AIChatSession,
    limit: int | None = None,
    char_budget: int | None = None,
    per_msg_cap: int | None = None,
) -> list[dict[str, str]]:
    """Most-recent-first windowed history with a total character budget.

    Each message content is truncated to per_msg_cap chars. Oldest messages
    are dropped first if the budget is exceeded. Final list is returned in
    chronological order.
    """
    limit = limit or getattr(settings, "AI_AGENT_HISTORY_LIMIT", 8)
    char_budget = char_budget or getattr(settings, "AI_AGENT_HISTORY_CHAR_BUDGET", 6000)
    per_msg_cap = per_msg_cap or getattr(settings, "AI_AGENT_HISTORY_MSG_CHAR_CAP", 800)

    messages = list(session.messages.order_by("-created_at", "-id")[:limit])
    items: list[dict[str, str]] = []
    used = 0
    for message in messages:  # newest first
        content = (message.content or "")[:per_msg_cap]
        cost = len(content) + len(message.role) + 16
        if used + cost > char_budget and items:
            break
        items.append({"role": message.role, "content": content})
        used += cost
    items.reverse()
    return items


def pending_confirmation_context(
    session: AIChatSession,
) -> list[dict[str, str]]:
    """Expose open pending action state to prompt builders.

    The model needs a compact reminder of the staged tool call when the user
    replies with short follow-ups like "go ahead" or "make it the morning one".
    """
    pending = session.pending_confirmation or {}
    if not pending or pending_is_expired(pending):
        return []

    payload = {
        "tool_name": pending.get("tool_name"),
        "module": pending.get("module"),
        "confirmation_label": pending.get("confirmation_label"),
        "confirmation_help": pending.get("confirmation_help"),
        "requires_input": bool(pending.get("requires_input")),
        "question": pending.get("question"),
        "arguments": pending.get("arguments") or {},
        "missing_fields": pending.get("missing_fields") or [],
        "expires_at": pending.get("expires_at"),
    }
    return [
        {
            "role": "system",
            "content": (
                "Open pending confirmation context. Treat this as active state "
                "for the current turn:\n"
                + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ),
        }
    ]


def conversation_history(session: AIChatSession) -> list[dict[str, str]]:
    history = recent_history(session)
    pending_context = pending_confirmation_context(session)
    if pending_context:
        return pending_context + history
    return history


def build_graph():
    """Build the LangGraph state machine."""
    if not _langgraph_available():
        return None

    graph = StateGraph(AssistantState)
    graph.add_node("load_context", lambda state: state)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("plan_tools", lambda state: state)
    graph.add_node("confirmation_gate", lambda state: state)
    graph.add_node("execute_tools", lambda state: state)
    graph.add_node("repair_or_retry", lambda state: state)
    graph.add_node("final_response", lambda state: state)
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "classify_intent")
    graph.add_edge("classify_intent", "plan_tools")
    graph.add_edge("plan_tools", "confirmation_gate")
    graph.add_edge("confirmation_gate", "execute_tools")
    graph.add_edge("execute_tools", "repair_or_retry")
    graph.add_edge("repair_or_retry", "final_response")
    graph.add_edge("final_response", END)
    return graph.compile()


def run_graph_state(state: AssistantState) -> AssistantState:
    graph = build_graph()
    if graph is None:
        return classify_intent(state)
    return graph.invoke(state)


def classify_intent(state: AssistantState) -> AssistantState:
    message = (state.get("message") or "").strip()
    if state.get("tool_name"):
        return state
    tool_name, arguments = infer_tool(message)
    state["tool_name"] = tool_name
    state["arguments"] = arguments
    return state


def infer_tool(message: str) -> tuple[str | None, dict[str, Any]]:
    text = message.lower()
    if not text:
        return None, {}
    if any(term in text for term in ("who am i", "my profile", "my account")):
        return "get_current_user_context", {}
    if "manager" in text and any(term in text for term in ("who", "whose", "manager")):
        return "get_employee_managers", {
            "query": _employee_name_from_manager_prompt(message)
        }
    if any(
        term in text
        for term in (
            "highest salary",
            "top salary",
            "biggest salary",
            "largest salary",
            "highest paid",
            "top paid",
            "salary ranking",
            "najveca plata",
            "najveća plata",
            "najvecom platom",
            "najvećom platom",
            "najvece plate",
            "najveće plate",
            "plata",
            "platom",
            "salary",
        )
    ):
        return "list_top_paid_employees", {"limit": _extract_limit(message, default=5)}
    if "leave" in text and any(
        term in text for term in ("balance", "days left", "remaining")
    ):
        return "list_leave_balances", {}
    if "leave polic" in text:
        return "list_leave_policies", {}
    if "leave" in text and any(
        term in text for term in ("pending", "requests", "request list")
    ):
        return "list_leave_requests", {}
    count_only = _is_count_question(message)
    limit = 50 if _wants_all_results(message) or count_only else 10
    direct_asset_tool, direct_asset_args = _infer_asset_tool(message)
    if direct_asset_tool:
        return direct_asset_tool, direct_asset_args
    if "asset" in text or "equipment" in text:
        return "list_assets", {
            "query": _query_from_prompt(message, "asset", "equipment"),
            "limit": limit,
            "count_only": count_only,
        }
    if "template" in text and (
        "document" in text or "contract" in text or "policy" in text
    ):
        return "list_document_templates", {
            "query": _query_from_prompt(message, "template"),
            "limit": limit,
            "count_only": count_only,
        }
    if "document" in text or "policy" in text:
        return "list_documents", {
            "query": _query_from_prompt(message, "document", "policy"),
            "expired": any(term in text for term in ("expired", "expiring", "istekao")),
            "limit": limit,
            "count_only": count_only,
        }
    if (
        "announcement" in text
        or any(term in text for term in ("broadcast", "announce"))
        or ("publish" in text and "title" in text and "body" in text)
    ):
        direct_tool, direct_args = _infer_announcement_tool(message)
        if direct_tool:
            return direct_tool, direct_args
    if "notification" in text or "unread" in text:
        if "mark" in text and "read" in text:
            return "mark_all_notifications_read", {}
        return "list_notifications", {"unread": "unread" in text}
    direct_time_tool, direct_time_args = _infer_time_entry_tool(message)
    if direct_time_tool:
        return direct_time_tool, direct_time_args
    if "time" in text and any(
        term in text for term in ("entry", "entries", "timesheet")
    ):
        return "list_time_entries", {}
    if "task" in text and "time" in text:
        return "list_time_tasks", {}
    if "employee" in text or "people" in text or "who is" in text:
        return "search_employees", {
            "query": _query_from_prompt(message, "employee", "people"),
            "limit": limit,
            "count_only": count_only,
        }
    if "role" in text or "department" in text or "reference" in text:
        return "list_reference_data", {}
    return None, {}


ORCHESTRATOR_SYSTEM = (
    "You are the BloomHub AI orchestrator. Pick exactly ONE module subagent for "
    "the user's request. Do NOT pick tools — the subagent owns tool selection.\n\n"
    "Rules:\n"
    "- Output ONLY a JSON object with keys `module` and `prompt`. No prose, no "
    "code fences.\n"
    "- `module` MUST be one of the modules listed.\n"
    "- `prompt` is a short, self-contained restatement of the task in English, "
    "preserving any IDs, names, dates, and quantities the user provided.\n"
    "- If the request is ambiguous, still pick the closest module and pass the "
    "user message verbatim as `prompt`.\n"
    "- Never invent SQL. The system has no raw-SQL tool.\n"
)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`")
    text = text.removeprefix("json").strip()
    return text


def _parse_orchestrator_payload(
    content: str, valid_modules: set[str]
) -> dict[str, str]:
    stripped = _strip_code_fence(content)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"orchestrator JSON parse failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMParseError("orchestrator response was not a JSON object")
    module = payload.get("module")
    if module not in valid_modules:
        raise OrchestratorRoutingError(f"invalid module from orchestrator: {module!r}")
    return {
        "module": module,
        "prompt": str(payload.get("prompt") or "").strip(),
    }


def orchestrator_decision(
    *,
    llm,
    message: str,
    history: list[dict[str, str]],
) -> tuple[dict[str, str] | None, dict[str, Any]]:
    if llm is None:
        return None, {}
    logger.warning("[AI] orchestrator.start message=%s", message[:200])
    started_at = time.perf_counter()
    prompt_chars = len(message or "")
    history_chars = sum(len(entry.get("content") or "") for entry in history or [])
    user_prompt = (
        f"Modules:\n{json.dumps(module_manifest(), ensure_ascii=False)}\n\n"
        f"Recent chat history:\n{json.dumps(history, ensure_ascii=False)}\n\n"
        f"User message:\n{message}"
    )
    valid_modules = {item["module"] for item in module_manifest()} | {"general"}

    last_err: str | None = None
    attempts = 0
    response_chars = 0
    usage: dict[str, int] = {}
    for attempt in range(2):
        attempts = attempt + 1
        prompt = ORCHESTRATOR_SYSTEM + "\n\n" + user_prompt
        if last_err:
            prompt += (
                f"\n\nPrevious attempt failed: {last_err}. "
                "Respond with ONLY a JSON object. No commentary."
            )
        try:
            response = invoke_llm_with_retry(llm, prompt)
        except LLMUnavailableError:
            metrics = {
                "prompt_chars": prompt_chars,
                "history_chars": history_chars,
                "attempts": attempts,
                "retries": max(0, attempts - 1),
                "response_chars": response_chars,
                "elapsed_ms": (time.perf_counter() - started_at) * 1000,
                **usage,
            }
            logger.warning(
                "[AI] orchestrator.complete module=%s attempts=%s retries=%s prompt_chars=%s history_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f reason=%s",
                None,
                metrics["attempts"],
                metrics["retries"],
                metrics["prompt_chars"],
                metrics["history_chars"],
                metrics["response_chars"],
                metrics.get("prompt_tokens"),
                metrics.get("completion_tokens"),
                metrics.get("total_tokens"),
                metrics["elapsed_ms"],
                "llm_unavailable",
            )
            return None, metrics
        except TransientAIError as exc:
            logger.warning("[AI] orchestrator.transient_error %s", exc)
            metrics = {
                "prompt_chars": prompt_chars,
                "history_chars": history_chars,
                "attempts": attempts,
                "retries": max(0, attempts - 1),
                "response_chars": response_chars,
                "elapsed_ms": (time.perf_counter() - started_at) * 1000,
                **usage,
            }
            logger.warning(
                "[AI] orchestrator.complete module=%s attempts=%s retries=%s prompt_chars=%s history_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f reason=%s",
                None,
                metrics["attempts"],
                metrics["retries"],
                metrics["prompt_chars"],
                metrics["history_chars"],
                metrics["response_chars"],
                metrics.get("prompt_tokens"),
                metrics.get("completion_tokens"),
                metrics.get("total_tokens"),
                metrics["elapsed_ms"],
                "transient_error",
            )
            return None, metrics
        content = getattr(response, "content", str(response))
        response_chars = len(content or "")
        usage = extract_token_usage(response)
        logger.warning("[AI] orchestrator.raw_response=%s", content[:1000])
        try:
            decision = _parse_orchestrator_payload(content, valid_modules)
        except LLMParseError as exc:
            last_err = str(exc)
            continue
        except OrchestratorRoutingError as exc:
            logger.warning("[AI] orchestrator.routing_error %s", exc)
            metrics = {
                "prompt_chars": prompt_chars,
                "history_chars": history_chars,
                "attempts": attempts,
                "retries": max(0, attempts - 1),
                "response_chars": response_chars,
                "elapsed_ms": (time.perf_counter() - started_at) * 1000,
                **usage,
            }
            logger.warning(
                "[AI] orchestrator.complete module=%s attempts=%s retries=%s prompt_chars=%s history_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f reason=%s",
                None,
                metrics["attempts"],
                metrics["retries"],
                metrics["prompt_chars"],
                metrics["history_chars"],
                metrics["response_chars"],
                metrics.get("prompt_tokens"),
                metrics.get("completion_tokens"),
                metrics.get("total_tokens"),
                metrics["elapsed_ms"],
                "routing_error",
            )
            return None, metrics
        if not decision["prompt"]:
            decision["prompt"] = message
        logger.warning(
            "[AI] orchestrator.decision module=%s prompt=%s",
            decision["module"],
            decision["prompt"][:300],
        )
        metrics = {
            "prompt_chars": prompt_chars,
            "history_chars": history_chars,
            "attempts": attempts,
            "retries": max(0, attempts - 1),
            "response_chars": response_chars,
            "elapsed_ms": (time.perf_counter() - started_at) * 1000,
            **usage,
        }
        logger.warning(
            "[AI] orchestrator.complete module=%s attempts=%s retries=%s prompt_chars=%s history_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f",
            decision["module"],
            metrics["attempts"],
            metrics["retries"],
            metrics["prompt_chars"],
            metrics["history_chars"],
            metrics["response_chars"],
            metrics.get("prompt_tokens"),
            metrics.get("completion_tokens"),
            metrics.get("total_tokens"),
            metrics["elapsed_ms"],
        )
        return decision, metrics

    metrics = {
        "prompt_chars": prompt_chars,
        "history_chars": history_chars,
        "attempts": attempts,
        "retries": max(0, attempts - 1),
        "response_chars": response_chars,
        "elapsed_ms": (time.perf_counter() - started_at) * 1000,
        **usage,
    }
    logger.warning(
        "[AI] orchestrator.parse_exhausted attempts=%s retries=%s prompt_chars=%s history_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f last_error=%s",
        metrics["attempts"],
        metrics["retries"],
        metrics["prompt_chars"],
        metrics["history_chars"],
        metrics["response_chars"],
        metrics.get("prompt_tokens"),
        metrics.get("completion_tokens"),
        metrics.get("total_tokens"),
        metrics["elapsed_ms"],
        last_err,
    )
    return None, metrics


def run_orchestrated_turn(
    *,
    user,
    session: AIChatSession,
    message: str,
    confirmed: bool = False,
) -> OrchestratedResult | None:
    turn_started = time.perf_counter()
    try:
        llm = get_llm()
    except Exception as exc:
        logger.exception("[AI] orchestrated_turn.llm_init_error %s", exc)
        return None
    if llm is None:
        return None

    try:
        history = conversation_history(session)
        decision, orchestrator_metrics = orchestrator_decision(
            llm=llm,
            message=message,
            history=history,
        )
    except PermanentAIError as exc:
        logger.warning("[AI] orchestrator.permanent_error %s", exc)
        return None

    if not decision:
        logger.warning("[AI] orchestrator.fallback reason=no_decision")
        return None

    try:
        agent = create_react_subagent(
            user=user,
            session=session,
            module=decision["module"],
            registry=registry,
            llm=llm,
            confirmed=confirmed,
        )
    except LLMUnavailableError as exc:
        logger.warning(
            "[AI] subagent.unavailable module=%s reason=%s",
            decision["module"],
            exc,
        )
        return None
    if agent is None:
        logger.warning(
            "[AI] subagent.fallback reason=create_failed module=%s",
            decision["module"],
        )
        return None

    max_iter = getattr(settings, "AI_AGENT_MAX_ITERATIONS", 4)
    last_error: str | None = None
    history = conversation_history(session)
    request_chars = len(message or "")
    history_chars = sum(len(entry.get("content") or "") for entry in history or [])

    def _log_turn_complete(
        *,
        module_name: str,
        tool_name: str | None,
        response_text: str,
        iterations: int,
        result: dict[str, Any],
        subagent_metrics: dict[str, Any] | None = None,
    ) -> None:
        elapsed_ms = (time.perf_counter() - turn_started) * 1000
        logger.warning(
            "[AI] chat.turn_end session=%s module=%s tool=%s orchestrator_attempts=%s orchestrator_retries=%s subagent_iterations=%s subagent_retries=%s request_chars=%s history_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f",
            session.id,
            module_name,
            tool_name,
            orchestrator_metrics.get("attempts"),
            orchestrator_metrics.get("retries"),
            iterations,
            max(0, iterations - 1),
            request_chars,
            history_chars,
            len(response_text or ""),
            orchestrator_metrics.get("prompt_tokens"),
            orchestrator_metrics.get("completion_tokens"),
            orchestrator_metrics.get("total_tokens"),
            elapsed_ms,
        )
        if subagent_metrics is not None:
            logger.warning(
                "[AI] chat.turn_subagent_usage session=%s module=%s prompt_chars=%s history_chars=%s planner_hint_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                session.id,
                module_name,
                subagent_metrics.get("prompt_chars"),
                subagent_metrics.get("history_chars"),
                subagent_metrics.get("planner_hint_chars"),
                subagent_metrics.get("response_chars"),
                subagent_metrics.get("prompt_tokens"),
                subagent_metrics.get("completion_tokens"),
                subagent_metrics.get("total_tokens"),
            )

    for attempt in range(1, max_iter + 1):
        try:
            logger.warning(
                "[AI] subagent.invoke attempt=%s module=%s prompt=%s history_msgs=%d",
                attempt,
                decision["module"],
                decision["prompt"][:300],
                len(history),
            )
            subagent_response = invoke_subagent(
                agent,
                message,
                history=history,
                planner_hint=decision["prompt"],
                module=decision["module"],
            )
            if isinstance(subagent_response, tuple):
                response_text = str(subagent_response[0]) if subagent_response else ""
                subagent_metrics = (
                    subagent_response[1]
                    if len(subagent_response) > 1
                    and isinstance(subagent_response[1], dict)
                    else {}
                )
            else:
                response_text = str(subagent_response)
                subagent_metrics = {}
            logger.warning(
                "[AI] subagent.response module=%s response=%s",
                decision["module"],
                response_text[:1000],
            )
            if _looks_like_tool_metadata_response(response_text):
                direct_tool, direct_args = infer_tool(message)
                direct_assistant_tool = (
                    registry.get(direct_tool) if direct_tool else None
                )
                if direct_assistant_tool and direct_assistant_tool.mutating:
                    logger.warning(
                        "[AI] subagent.response_replaced module=%s tool=%s reason=tool_metadata",
                        decision["module"],
                        direct_tool,
                    )
                    result = execute_tool(
                        registry=registry,
                        session=session,
                        user=user,
                        tool_name=direct_tool,
                        arguments=direct_args,
                        confirmed=confirmed,
                    )
                    selected_module = direct_assistant_tool.module
                    response_text = render_response(result, direct_tool)
                    _log_turn_complete(
                        module_name=selected_module,
                        tool_name=direct_tool,
                        response_text=response_text,
                        iterations=attempt,
                        result=result,
                        subagent_metrics=subagent_metrics,
                    )
                    return {
                        "module": selected_module,
                        "response_text": response_text,
                        "result": result,
                    }
            if not (response_text or "").strip():
                # Fall back so the user sees actionable text even if the model
                # produced an empty AI message after a tool call.
                pending = session.pending_confirmation or {}
                if pending.get("tool_name"):
                    response_text = (
                        f"I've prepared `{pending['tool_name']}` for review. "
                        "Confirm the form to proceed, or tell me what to adjust."
                    )
                else:
                    response_text = (
                        "I didn't get a clear response. Could you rephrase or "
                        "give me a bit more detail?"
                    )
            _log_turn_complete(
                module_name=decision["module"],
                tool_name=None,
                response_text=response_text,
                iterations=attempt,
                result={
                    "summary": response_text,
                    "orchestrator": decision,
                    "subagent": decision["module"],
                    "iterations": attempt,
                },
                subagent_metrics=subagent_metrics,
            )
            return {
                "module": decision["module"],
                "response_text": response_text,
                "result": {
                    "summary": response_text,
                    "orchestrator": decision,
                    "subagent": decision["module"],
                    "iterations": attempt,
                },
            }
        except TransientAIError as exc:
            last_error = str(exc)
            logger.warning(
                "[AI] subagent.transient attempt=%s module=%s err=%s",
                attempt,
                decision["module"],
                exc,
            )
            if attempt >= max_iter:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
        except SensitiveActionDenied as exc:
            # User-recoverable: tell them to re-login, do not fall through to
            # the fallback router which would just spit a generic refusal.
            logger.warning(
                "[AI] subagent.sensitive_denied module=%s err=%s",
                decision["module"],
                exc,
            )
            text = (
                f"{exc} Please log out and log back in, then try again. "
                "Sensitive actions require a fresh authentication window."
            )
            result = {
                "summary": text,
                "blocked": True,
                "reason": "recent_auth_required",
            }
            _log_turn_complete(
                module_name=decision["module"],
                tool_name=None,
                response_text=text,
                iterations=attempt,
                result=result,
            )
            return {
                "module": decision["module"],
                "response_text": text,
                "result": result,
            }
        except PermanentAIError as exc:
            logger.warning(
                "[AI] subagent.permanent module=%s err=%s",
                decision["module"],
                exc,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — unexpected, log and stop
            logger.exception(
                "[AI] subagent.unexpected module=%s err=%s",
                decision["module"],
                exc,
            )
            return None

    logger.warning(
        "[AI] subagent.exhausted module=%s last_error=%s",
        decision["module"],
        last_error,
    )
    return None


def _extract_limit(message: str, default: int = 10) -> int:
    match = re.search(r"\b(\d{1,2})\b", message)
    if not match:
        return default
    return max(1, min(int(match.group(1)), 50))


def _employee_name_from_manager_prompt(message: str) -> str:
    text = message.strip().strip("?")
    text = re.sub(r"(?i)^who\s+is\s+", "", text).strip()
    text = re.sub(r"(?i)'s\s+manager$", "", text).strip()
    text = re.sub(r"(?i)\s+manager$", "", text).strip()
    return text


def _quoted_or_tail(message: str, marker: str) -> str:
    return _query_from_prompt(message, marker)


def _extract_quoted_field(message: str, label: str) -> str:
    patterns = (
        rf'(?is)\b{re.escape(label)}\b\s*(?:is|:|-)?\s*["“]([^"”]+)["”]',
        rf"(?is)\b{re.escape(label)}\b\s*(?:is|:|-)?\s*'([^']+)'",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1).strip()
    return ""


def _infer_announcement_tool(message: str) -> tuple[str | None, dict[str, Any]]:
    text = message.strip()
    lower = text.lower()
    if (
        "announcement" not in lower
        and "announce" not in lower
        and "broadcast" not in lower
    ):
        return None, {}
    if not any(
        term in lower for term in ("create", "new", "publish", "schedule", "post")
    ):
        return None, {}

    args: dict[str, Any] = {}
    title = _extract_quoted_field(message, "title")
    body = _extract_quoted_field(message, "body")
    announcement_type = _extract_quoted_field(message, "type")
    if not announcement_type:
        match = re.search(
            r"(?is)\btype\b\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z _-]{0,50}?)(?=,|\band\b|\btitle\b|\bbody\b|$)",
            text,
        )
        if match:
            announcement_type = match.group(1).strip().rstrip(".,")

    if title:
        args["title"] = title
    if body:
        args["body"] = body
    if announcement_type:
        args["type"] = announcement_type
    if re.search(
        r"(?i)\b(publish it now|publish now|now|immediately|right away)\b", text
    ):
        args["scheduled_at"] = None
    if "email" in lower and any(term in lower for term in ("send", "notify")):
        args["send_email_notifications"] = True
    return "create_announcement", args


def _normalize_asset_choice(value: str) -> str:
    normalized = re.sub(r"[\s_-]+", "_", (value or "").strip().lower())
    aliases = {
        "laptop": "laptops",
        "laptops": "laptops",
        "phone": "phones",
        "phones": "phones",
        "monitor": "monitors",
        "monitors": "monitors",
        "headphone": "headphones",
        "headphones": "headphones",
        "camera": "cameras",
        "cameras": "cameras",
        "vehicle": "vehicles",
        "vehicles": "vehicles",
        "furniture": "furniture",
        "other": "other",
        "excellent": "excellent",
        "good": "good",
        "fair": "fair",
        "poor": "poor",
        "damaged": "damaged",
    }
    return aliases.get(normalized, normalized)


def _infer_asset_tool(message: str) -> tuple[str | None, dict[str, Any]]:
    text = message.strip()
    lower = text.lower()
    if "asset" not in lower and "equipment" not in lower:
        return None, {}
    if not any(term in lower for term in ("create", "new", "add", "register", "make")):
        return None, {}

    args: dict[str, Any] = {}

    def quoted(label: str) -> str:
        return _extract_quoted_field(message, label)

    asset_id = quoted("asset id") or quoted("id")
    name = quoted("name")
    category = quoted("category")
    condition = quoted("condition")
    purchase_date = quoted("purchase date")
    serial_number = quoted("serial number")
    model = quoted("model")
    manufacturer = quoted("manufacturer")
    purchase_price = quoted("purchase price")
    description = quoted("description")

    if not asset_id:
        match = re.search(
            r"(?i)\b(?:asset\s+)?id\s*(?:is|:|-)?\s*([A-Za-z0-9._-]+)",
            text,
        )
        if match:
            asset_id = match.group(1).strip()
    if not name:
        match = re.search(
            r"(?i)\bname\s*(?:is|:|-)?\s*([A-Za-z0-9][A-Za-z0-9 ._/&()-]{0,199})",
            text,
        )
        if match:
            name = match.group(1).strip().rstrip(".,")
    if not category:
        match = re.search(
            r"(?i)\bcategory\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z _-]{0,50})",
            text,
        )
        if match:
            category = match.group(1).strip().rstrip(".,")
    if not condition:
        match = re.search(
            r"(?i)\bcondition\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z _-]{0,50})",
            text,
        )
        if match:
            condition = match.group(1).strip().rstrip(".,")
    if not purchase_date:
        match = re.search(
            r"(?i)\bpurchase\s+date\s*(?:is|:|-)?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
            text,
        )
        if match:
            purchase_date = match.group(1).strip()
    if not serial_number:
        match = re.search(
            r"(?i)\bserial\s+number\s*(?:is|:|-)?\s*([A-Za-z0-9._-]+)",
            text,
        )
        if match:
            serial_number = match.group(1).strip()
    if not model:
        match = re.search(
            r"(?i)\bmodel\s*(?:is|:|-)?\s*([A-Za-z0-9][A-Za-z0-9 ._/&()-]{0,99})",
            text,
        )
        if match:
            model = match.group(1).strip().rstrip(".,")
    if not manufacturer:
        match = re.search(
            r"(?i)\bmanufacturer\s*(?:is|:|-)?\s*([A-Za-z0-9][A-Za-z0-9 ._/&()-]{0,99})",
            text,
        )
        if match:
            manufacturer = match.group(1).strip().rstrip(".,")
    if not purchase_price:
        match = re.search(
            r"(?i)\bpurchase\s+price\s*(?:is|:|-)?\s*([$€£]?[0-9][0-9,]*(?:\.[0-9]{1,2})?)",
            text,
        )
        if match:
            purchase_price = match.group(1).strip()
    if not description:
        match = re.search(r"(?i)\bdescription\s*(?:is|:|-)?\s*(.+)$", text)
        if match:
            description = match.group(1).strip()

    if asset_id:
        args["asset_id"] = asset_id
    if name:
        args["name"] = name
    if category:
        args["category"] = _normalize_asset_choice(category)
    if condition:
        args["condition"] = _normalize_asset_choice(condition)
    if purchase_date:
        args["purchase_date"] = purchase_date
    if serial_number:
        args["serial_number"] = serial_number
    if model:
        args["model"] = model
    if manufacturer:
        args["manufacturer"] = manufacturer
    if purchase_price:
        args["purchase_price"] = purchase_price
    if description:
        args["description"] = description

    return "create_asset", args


def _is_count_question(message: str) -> bool:
    return bool(
        re.search(
            r"\b(how many|number of|count of|total(?: number)? of|koliko)\b",
            message,
            re.IGNORECASE,
        )
    )


def _wants_all_results(message: str) -> bool:
    return bool(
        re.search(
            r"\b(show|list|get|display)\s+(me\s+)?(all|every)\b|\b(all|every)\b",
            message,
            re.IGNORECASE,
        )
    )


def _query_from_prompt(message: str, *markers: str) -> str:
    quoted = re.search(r"[\"']([^\"']+)[\"']", message)
    if quoted:
        return quoted.group(1).strip()
    if _is_count_question(message) or _wants_all_results(message):
        return ""
    lower = message.lower()
    marker_terms: list[str] = []
    for marker in markers:
        marker = marker.strip().lower()
        if not marker:
            continue
        marker_terms.append(re.escape(marker))
        if not marker.endswith("s"):
            marker_terms.append(re.escape(marker + "s"))
    if not marker_terms:
        return ""
    match = re.search(rf"\b(?:{'|'.join(marker_terms)})\b", lower)
    if match:
        tail = message[match.end() :].strip(" :?.")
        tail = re.sub(
            r"(?i)^(named|called|with|matching|that match|where|whose)\s+",
            "",
            tail,
        ).strip(" :?.")
        if re.fullmatch(
            r"(?i)(do we have|are there|we have|in the system|available|records?)",
            tail,
        ):
            return ""
        return tail
    return ""


def _parse_time_token(token: str) -> dt_time | None:
    value = (token or "").strip().lower().replace(".", "")
    if not value:
        return None
    for fmt in (
        "%I:%M%p",
        "%I:%M %p",
        "%I%p",
        "%I %p",
        "%H:%M",
        "%H:%M:%S",
    ):
        try:
            return datetime.strptime(value.upper(), fmt).time()
        except ValueError:
            continue
    return None


def _parse_time_entry_hours(message: str) -> str | None:
    text = message.lower()

    explicit = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", text)
    if explicit:
        return f"{float(explicit.group(1)):.2f}"

    range_match = re.search(
        r"(?i)\b(?:from|between)\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)\s+"
        r"(?:to|til|till|until|-)\s+([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)\b",
        message,
    )
    if not range_match:
        return None

    start = _parse_time_token(range_match.group(1))
    end = _parse_time_token(range_match.group(2))
    if start is None or end is None:
        return None

    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60
    duration_hours = (end_minutes - start_minutes) / 60.0
    return f"{duration_hours:.2f}"


def _infer_time_entry_tool(message: str) -> tuple[str | None, dict[str, Any]]:
    text = message.lower()
    if "time" not in text and "timelog" not in text and "timesheet" not in text:
        return None, {}

    create_intent = bool(
        re.search(r"\b(create|log|add|record|enter|new|make)\b", text)
        or "timelog" in text
    )
    if not create_intent:
        return None, {}

    args: dict[str, Any] = {}

    if re.search(r"\b(today|this date|current date|now)\b", text):
        args["work_date"] = timezone.localdate().isoformat()
    else:
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        if match:
            args["work_date"] = match.group(1)

    hours = _parse_time_entry_hours(message)
    if hours:
        args["hours"] = hours

    project_query = _query_from_prompt(message, "project")
    project_query = re.sub(
        r"(?i)\b(?:no|without)\s+task\b.*$",
        "",
        project_query,
    ).strip(" ,.;:")
    if project_query:
        from core.models import Project

        exact_matches = list(
            Project.objects.filter(name__iexact=project_query).order_by("id")[:2]
        )
        if len(exact_matches) == 1:
            args["project_id"] = exact_matches[0].id
        else:
            partial_matches = list(
                Project.objects.filter(name__icontains=project_query).order_by("id")[:2]
            )
            if len(partial_matches) == 1:
                args["project_id"] = partial_matches[0].id

    task_query = _query_from_prompt(message, "task")
    task_query = re.sub(
        r"(?i)\b(?:no|without)\s+task\b.*$",
        "",
        task_query,
    ).strip(" ,.;:")
    if task_query and not re.fullmatch(r"(?i)(no|none|without)\s+task", task_query):
        from core.models import TimeTask

        task_matches = list(TimeTask.objects.filter(name__iexact=task_query)[:2])
        if len(task_matches) == 1:
            args["task_id"] = task_matches[0].id

    return "create_time_entry", args


def render_response(result: dict[str, Any], tool_name: str | None = None) -> str:
    # Slot-fill: backend collected partial arguments and needs the user to
    # supply the rest. Prefer the structured question over the canned confirm.
    if result.get("requires_input"):
        pending = result.get("pending_confirmation") or {}
        return str(
            pending.get("question")
            or result.get("summary")
            or "I need more details to proceed."
        )
    if result.get("requires_confirmation"):
        pending = result.get("pending_confirmation") or {}
        return (
            f"I need confirmation before running `{pending.get('tool_name', tool_name)}`. "
            "Reply with `confirm` to proceed."
        )
    summary = result.get("summary")
    if summary:
        return str(summary)
    if tool_name:
        return f"Ran `{tool_name}`."
    return (
        "I can help with HR workflows, but I need a bit more detail for that request."
    )


def _looks_like_json_payload(text: str) -> bool:
    stripped = _strip_code_fence(text).strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, (dict, list))


def _summarize_json_payload(payload: Any) -> str:
    def summarize_items(label: str, items: list[Any]) -> str:
        count = len(items)
        normalized_label = label.replace("_", " ").strip()
        display_label = normalized_label
        if count == 1:
            if normalized_label.endswith("ies"):
                display_label = f"{normalized_label[:-3]}y"
            elif normalized_label.endswith("s") and not normalized_label.endswith(
                ("ss", "us")
            ):
                display_label = normalized_label[:-1]
        plural = "s" if count != 1 else ""
        titles: list[str] = []
        for item in items[:5]:
            if isinstance(item, dict):
                title = (
                    item.get("title")
                    or item.get("name")
                    or item.get("label")
                    or item.get("id")
                )
                if title is not None:
                    titles.append(str(title))
        if titles:
            snippet = ", ".join(f"`{title}`" for title in titles)
            suffix = " and more" if count > len(titles) else ""
            return f"Loaded {count} {display_label}{plural}: {snippet}{suffix}."
        return f"Loaded {count} {display_label}{plural}."

    if isinstance(payload, dict):
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()

        collection_summaries = [
            summarize_items(key, value)
            for key, value in payload.items()
            if isinstance(value, list)
        ]
        if collection_summaries:
            if len(collection_summaries) == 1:
                return collection_summaries[0]
            joined = "; ".join(
                summary.removeprefix("Loaded ").rstrip(".")
                for summary in collection_summaries
            )
            return f"Loaded {joined}."

        if "announcement" in payload and isinstance(payload["announcement"], dict):
            title = payload["announcement"].get("title")
            if title:
                return f"Loaded announcement `{title}`."
            return "Loaded announcement."

        if len(payload) == 1:
            key, value = next(iter(payload.items()))
            label = key.replace("_", " ")
            if isinstance(value, list):
                return summarize_items(label, value)
            if isinstance(value, dict):
                title = value.get("title") or value.get("name") or value.get("label")
                if title:
                    return f"Loaded {label} `{title}`."
                return f"Loaded {label}."

        keys = ", ".join(list(payload.keys())[:6])
        return f"Returned fields: {keys}" if keys else "Returned structured data."

    if isinstance(payload, list):
        if not payload:
            return "No results found."
        titles: list[str] = []
        for item in payload[:5]:
            if isinstance(item, dict):
                title = item.get("title") or item.get("name") or item.get("id")
                if title is not None:
                    titles.append(str(title))
        count = len(payload)
        if titles:
            snippet = ", ".join(f"`{title}`" for title in titles)
            suffix = " and more" if count > len(titles) else ""
            return f"Loaded {count} item{'s' if count != 1 else ''}: {snippet}{suffix}."
        return f"Loaded {count} item{'s' if count != 1 else ''}."

    return str(payload)


def normalize_assistant_message(text: str) -> str:
    """Turn raw structured LLM output into plain chat text."""
    if not text:
        return text
    stripped = _strip_code_fence(text).strip()
    if not _looks_like_json_payload(stripped):
        return text
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    return _summarize_json_payload(payload)


def _user_requested_raw_json(message: str) -> bool:
    text = _normalize_msg(message)
    if not text or "json" not in text:
        return False
    if re.search(r"\b(raw|plain|verbatim)\s+json\b", text):
        return True
    if re.search(r"\bjson\b", text) and re.search(
        r"\b(show|return|give|output|print|respond with|display|dump|as)\b",
        text,
    ):
        return True
    return False


def _looks_like_tool_metadata_response(text: str) -> bool:
    stripped = _strip_code_fence(text).strip()
    if not stripped or not stripped.startswith("{"):
        return False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    if {"name", "module", "summary"} <= payload.keys():
        return True
    if {"name", "required_permissions", "can_run"} <= payload.keys():
        return True
    summary = str(payload.get("summary") or "")
    return summary.startswith("You can run ") and "required_permissions" in payload


def _strip_markdown_table_separator_rows(text: str) -> str:
    """Remove markdown alignment rows for the frontend's custom table renderer."""
    lines = str(text or "").splitlines()
    cleaned: list[str] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{1,}:?", cell or "") for cell in cells):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _build_ui_action(result: dict[str, Any]) -> dict[str, Any]:
    pending = result.get("pending_confirmation") or {}
    if not pending:
        return {"type": "message"}

    tool_name = pending.get("tool_name")
    if result.get("requires_input") or pending.get("requires_input"):
        action_type = "form"
    elif tool_name == "approve_leave_request":
        action_type = "approval"
    elif tool_name in {"create_leave_request"}:
        action_type = "form"
    else:
        action_type = "confirmation"

    return {
        "type": action_type,
        "tool_name": tool_name,
        "module": pending.get("module"),
        "label": pending.get("confirmation_label"),
        "help": pending.get("confirmation_help"),
        "arguments": pending.get("arguments") or {},
        "args_schema": pending.get("args_schema"),
        "expires_at": pending.get("expires_at"),
    }


def run_assistant_turn(
    *,
    user,
    session: AIChatSession,
    message: str,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    turn_started = time.perf_counter()
    logger.warning(
        "[AI] chat.turn_start session=%s user=%s message=%s explicit_tool=%s confirm=%s",
        session.id,
        getattr(user, "id", None),
        message[:200],
        tool_name,
        confirm,
    )
    user_message_content = display_chat_message_content(
        role=AIChatMessage.Role.USER,
        content=message,
        metadata={"tool_name": tool_name, "confirm": confirm},
    )
    AIChatMessage.objects.create(
        session=session,
        role=AIChatMessage.Role.USER,
        content=user_message_content,
        metadata={"tool_name": tool_name, "confirm": confirm},
    )

    # Per-turn entity accumulator; execute_tool appends here for every
    # tool call that runs during this turn.
    session._ai_turn_entities = []

    # Bind the session to the user object so per-tool handlers that need
    # access to the conversation (e.g. confirm_pending_action) can resolve
    # it without changing the generic execute_tool signature.
    try:
        user._ai_active_session = session
    except Exception:
        pass

    # Natural-language confirm/cancel: if a pending action exists and the
    # user replies with "yes"/"confirm"/"go ahead" (or a negation), promote
    # this turn into a confirm/cancel without requiring the explicit
    # `confirm` flag from the frontend. Critical for chat-only UX.
    session.refresh_from_db(fields=["pending_confirmation"])
    pending_now = session.pending_confirmation or {}
    logger.warning(
        "[AI] nl_gate.probe session=%s confirm=%s tool_name=%s pending_tool=%s expired=%s msg=%r",
        session.id,
        confirm,
        tool_name,
        pending_now.get("tool_name") if pending_now else None,
        pending_is_expired(pending_now) if pending_now else None,
        message[:80],
    )
    if not confirm and pending_now and not pending_is_expired(pending_now):
        msg_norm = (message or "").strip()
        confirmation_sentiment = classify_confirmation_response(msg_norm)
        if confirmation_sentiment == "positive":
            confirm = True
            tool_name = None
            logger.warning(
                "[AI] chat.nl_confirm session=%s tool=%s",
                session.id,
                pending_now.get("tool_name"),
            )
        elif confirmation_sentiment == "negative":
            tool_name = None
            cancelled_tool = pending_now.get("tool_name")
            session.pending_confirmation = {}
            session.save(update_fields=["pending_confirmation", "updated_at"])
            logger.warning(
                "[AI] chat.nl_cancel session=%s tool=%s",
                session.id,
                cancelled_tool,
            )
            cancel_text = (
                f"Okay — cancelled the pending `{cancelled_tool}` request."
                if cancelled_tool
                else "Okay — cancelled the pending action."
            )
            AIChatMessage.objects.create(
                session=session,
                role=AIChatMessage.Role.ASSISTANT,
                content=cancel_text,
                metadata={
                    "cancelled_tool": cancelled_tool,
                    "module": "general",
                },
            )
            session.state = {
                **(session.state or {}),
                "last_action": "cancelled",
                "cancelled_tool": cancelled_tool,
            }
            session.save(update_fields=["state", "updated_at"])
            return {
                "session_id": session.id,
                "message": cancel_text,
                "tool_name": None,
                "module": "general",
                "result": {"summary": cancel_text, "cancelled_tool": cancelled_tool},
                "entities": [],
                "entity_spans": [],
                "ui_action_type": "message",
                "ui_action": {"type": "message"},
                "requires_confirmation": False,
                "requires_input": False,
                "pending_confirmation": {},
            }

    if confirm:
        pending = session.pending_confirmation or {}
        if not pending:
            raise ValidationError(
                {"confirm": "There is no pending tool call to confirm."}
            )
        if pending_is_expired(pending):
            session.pending_confirmation = {}
            session.save(update_fields=["pending_confirmation", "updated_at"])
            raise ValidationError(
                {
                    "confirm": "Pending confirmation expired; please re-request the action."
                }
            )
        tool_name = pending["tool_name"]
        stored_args = pending.get("arguments") or {}
        # Caller may override individual fields via `arguments` payload; user
        # edits win over the LLM-proposed values. Pydantic re-validation in
        # execute_tool guarantees type safety.
        edited = arguments or {}
        merged = {**stored_args, **edited}
        arguments = merged

    selected_tool = tool_name
    selected_args = arguments or {}
    orchestrated = None
    if not confirm and not selected_tool:
        orchestrated = run_orchestrated_turn(
            user=user,
            session=session,
            message=message,
            confirmed=confirm,
        )

    if orchestrated:
        selected_module = orchestrated["module"]
        result = orchestrated["result"]
        response_text = orchestrated["response_text"]
        pending_after_orchestration = session.pending_confirmation or {}
        selected_tool = (
            result.get("tool_name")
            or (
                pending_after_orchestration.get("tool_name")
                if pending_after_orchestration
                else None
            )
            or selected_tool
        )
        if pending_after_orchestration and not pending_is_expired(
            pending_after_orchestration
        ):
            result.setdefault("requires_confirmation", True)
            result.setdefault("pending_confirmation", pending_after_orchestration)
            if pending_after_orchestration.get("requires_input"):
                result.setdefault("requires_input", True)
    else:
        logger.warning("[AI] fallback_router.start session=%s", session.id)
        state: AssistantState = {
            "session_id": session.id,
            "message": message,
            "tool_name": selected_tool,
            "arguments": selected_args,
        }
        state = run_graph_state(state)
        selected_tool = state.get("tool_name")
        selected_args = state.get("arguments") or {}
        logger.warning(
            "[AI] fallback_router.selected tool=%s args=%s",
            selected_tool,
            selected_args,
        )
        selected_module = infer_module(
            message,
            registry.get(selected_tool).module if selected_tool else None,
        )

        if not selected_tool:
            result = {
                "summary": (
                    "I can help with profiles, leave, assets, documents, time tracking, "
                    "onboarding, and notifications. Please ask for a specific action."
                )
            }
        else:
            try:
                result = execute_tool(
                    registry=registry,
                    session=session,
                    user=user,
                    tool_name=selected_tool,
                    arguments=selected_args,
                    confirmed=confirm,
                )
            except SensitiveActionDenied as exc:
                logger.warning(
                    "[AI] fallback_router.sensitive_denied tool=%s err=%s",
                    selected_tool,
                    exc,
                )
                result = {
                    "summary": (
                        f"{exc} Please log out and log back in, then retry. "
                        "Sensitive actions require a fresh authentication window."
                    ),
                    "blocked": True,
                    "reason": "recent_auth_required",
                }
        response_text = render_response(result, selected_tool)

    if not orchestrated and not selected_tool:
        result = {
            "summary": (
                "I can help with profiles, leave, assets, documents, time tracking, "
                "onboarding, and notifications. Please ask for a specific action."
            )
        }
        response_text = render_response(result, selected_tool)
    raw_requested = _user_requested_raw_json(message)
    if raw_requested:
        response_text = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        response_text = normalize_assistant_message(response_text)
    response_text = _strip_markdown_table_separator_rows(response_text)
    if isinstance(result, dict) and not raw_requested:
        result["summary"] = response_text

    subagent_enabled = bool(orchestrated)

    # Merge entities collected by every tool call this turn with any extracted
    # directly from the final result (covers fallback path where the dict is
    # returned verbatim) and compute character spans in the assistant text.
    turn_entities = list(getattr(session, "_ai_turn_entities", []))
    turn_entities.extend(collect_entities(result))
    turn_entities = _dedupe_entities(turn_entities)
    entity_spans = find_spans(response_text, turn_entities)
    if turn_entities:
        result.setdefault("entities", turn_entities)
    if entity_spans:
        result["entity_spans"] = entity_spans

    ui_action = _build_ui_action(result)
    result.setdefault("ui_action_type", ui_action["type"])
    result.setdefault("ui_action", ui_action)

    result = make_json_safe(result)
    turn_entities = make_json_safe(turn_entities)
    entity_spans = make_json_safe(entity_spans)
    ui_action = make_json_safe(ui_action)

    AIChatMessage.objects.create(
        session=session,
        role=AIChatMessage.Role.ASSISTANT,
        content=response_text,
        metadata={
            "tool_name": selected_tool,
            "module": selected_module,
            "result": result,
            "entities": turn_entities,
            "entity_spans": entity_spans,
            "ui_action_type": ui_action["type"],
            "ui_action": ui_action,
            "langgraph_available": _langgraph_available(),
            "subagent_enabled": subagent_enabled,
        },
    )
    if not session.title:
        session.title = message[:157] + ("..." if len(message) > 160 else "")
    session.state = {
        "last_tool": selected_tool,
        "last_module": selected_module,
        "last_result_summary": (
            response_text if raw_requested else result.get("summary", "")
        ),
        "langgraph_available": _langgraph_available(),
        "subagent_enabled": subagent_enabled,
    }
    session.save(update_fields=["title", "state", "updated_at"])
    logger.warning(
        "[AI] chat.turn_end session=%s module=%s tool=%s orchestrated=%s request_chars=%s response_chars=%s elapsed_ms=%.1f",
        session.id,
        selected_module,
        selected_tool,
        bool(orchestrated),
        len(message or ""),
        len(response_text or ""),
        (time.perf_counter() - turn_started) * 1000,
    )
    return make_json_safe(
        {
            "session_id": session.id,
            "message": response_text,
            "tool_name": selected_tool,
            "module": selected_module,
            "result": result,
            "entities": turn_entities,
            "entity_spans": entity_spans,
            "ui_action_type": ui_action["type"],
            "ui_action": ui_action,
            "requires_confirmation": bool(result.get("requires_confirmation")),
            "requires_input": bool(result.get("requires_input")),
            "pending_confirmation": session.pending_confirmation,
        }
    )


def _dedupe_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, Any]] = set()
    out: list[dict[str, Any]] = []
    for entity in entities:
        key = (entity.get("type"), entity.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(entity)
    return out
