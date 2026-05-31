from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypedDict

from django.conf import settings
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
from core.ai.subagents import (
    create_react_subagent,
    infer_module,
    invoke_subagent,
    module_manifest,
)
from core.ai.tooling import execute_tool, pending_is_expired
from core.ai.tools import registry
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
    if "asset" in text or "equipment" in text:
        return "list_assets", {"query": _quoted_or_tail(message, "asset")}
    if "template" in text and (
        "document" in text or "contract" in text or "policy" in text
    ):
        return "list_document_templates", {
            "query": _quoted_or_tail(message, "template")
        }
    if "document" in text or "policy" in text:
        return "list_documents", {
            "query": _quoted_or_tail(message, "document"),
            "expired": any(term in text for term in ("expired", "expiring", "istekao")),
        }
    if "notification" in text or "unread" in text:
        if "mark" in text and "read" in text:
            return "mark_all_notifications_read", {}
        return "list_notifications", {"unread": "unread" in text}
    if "time" in text and any(
        term in text for term in ("entry", "entries", "timesheet")
    ):
        return "list_time_entries", {}
    if "task" in text and "time" in text:
        return "list_time_tasks", {}
    if "employee" in text or "people" in text or "who is" in text:
        return "search_employees", {"query": _quoted_or_tail(message, "employee")}
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
) -> dict[str, str] | None:
    if llm is None:
        return None
    logger.warning("[AI] orchestrator.start message=%s", message[:200])
    user_prompt = (
        f"Modules:\n{json.dumps(module_manifest(), ensure_ascii=False)}\n\n"
        f"Recent chat history:\n{json.dumps(history, ensure_ascii=False)}\n\n"
        f"User message:\n{message}"
    )
    valid_modules = {item["module"] for item in module_manifest()} | {"general"}

    last_err: str | None = None
    for attempt in range(2):
        prompt = ORCHESTRATOR_SYSTEM + "\n\n" + user_prompt
        if last_err:
            prompt += (
                f"\n\nPrevious attempt failed: {last_err}. "
                "Respond with ONLY a JSON object. No commentary."
            )
        try:
            response = invoke_llm_with_retry(llm, prompt)
        except LLMUnavailableError:
            return None
        except TransientAIError as exc:
            logger.warning("[AI] orchestrator.transient_error %s", exc)
            return None
        content = getattr(response, "content", str(response))
        logger.warning("[AI] orchestrator.raw_response=%s", content[:1000])
        try:
            decision = _parse_orchestrator_payload(content, valid_modules)
        except LLMParseError as exc:
            last_err = str(exc)
            continue
        except OrchestratorRoutingError as exc:
            logger.warning("[AI] orchestrator.routing_error %s", exc)
            return None
        if not decision["prompt"]:
            decision["prompt"] = message
        logger.warning(
            "[AI] orchestrator.decision module=%s prompt=%s",
            decision["module"],
            decision["prompt"][:300],
        )
        return decision

    logger.warning("[AI] orchestrator.parse_exhausted last_error=%s", last_err)
    return None


def run_orchestrated_turn(
    *,
    user,
    session: AIChatSession,
    message: str,
    confirmed: bool = False,
) -> OrchestratedResult | None:
    try:
        llm = get_llm()
    except Exception as exc:
        logger.exception("[AI] orchestrated_turn.llm_init_error %s", exc)
        return None
    if llm is None:
        return None

    try:
        decision = orchestrator_decision(
            llm=llm,
            message=message,
            history=recent_history(session),
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
    history = recent_history(session)
    for attempt in range(1, max_iter + 1):
        try:
            logger.warning(
                "[AI] subagent.invoke attempt=%s module=%s prompt=%s history_msgs=%d",
                attempt,
                decision["module"],
                decision["prompt"][:300],
                len(history),
            )
            response_text = invoke_subagent(
                agent,
                message,
                history=history,
                planner_hint=decision["prompt"],
            )
            logger.warning(
                "[AI] subagent.response module=%s response=%s",
                decision["module"],
                response_text[:1000],
            )
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
            return {
                "module": decision["module"],
                "response_text": text,
                "result": {
                    "summary": text,
                    "blocked": True,
                    "reason": "recent_auth_required",
                },
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
    quoted = re.search(r"[\"']([^\"']+)[\"']", message)
    if quoted:
        return quoted.group(1).strip()
    lower = message.lower()
    if marker in lower:
        return message[lower.index(marker) + len(marker) :].strip(" :?.")
    return ""


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
    logger.warning(
        "[AI] chat.turn_start session=%s user=%s message=%s explicit_tool=%s confirm=%s",
        session.id,
        getattr(user, "id", None),
        message[:200],
        tool_name,
        confirm,
    )
    AIChatMessage.objects.create(
        session=session,
        role=AIChatMessage.Role.USER,
        content=message,
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
    response_text = _strip_markdown_table_separator_rows(response_text)

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
        "last_result_summary": result.get("summary", ""),
        "langgraph_available": _langgraph_available(),
        "subagent_enabled": subagent_enabled,
    }
    session.save(update_fields=["title", "state", "updated_at"])
    logger.warning(
        "[AI] chat.turn_end session=%s module=%s tool=%s orchestrated=%s",
        session.id,
        selected_module,
        selected_tool,
        bool(orchestrated),
    )
    return {
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
