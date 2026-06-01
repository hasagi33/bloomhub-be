from __future__ import annotations

import logging
import time
from typing import Any

from django.conf import settings

from core.ai.errors import LLMUnavailableError
from core.ai.tooling import ToolRegistry, execute_tool
from core.ai.usage import extract_token_usage
from core.models import AIChatSession

logger = logging.getLogger(__name__)

try:
    from langchain_core.tools import StructuredTool
    from langgraph.prebuilt import create_react_agent
    from pydantic import BaseModel, Field, create_model
except Exception:  # pragma: no cover
    StructuredTool = None
    create_react_agent = None
    BaseModel = None
    Field = None
    create_model = None


SHARED_DOCTRINE = (
    "Operating doctrine:\n"
    "- Backend is Django + PostgreSQL. Read-only data access goes through the "
    "exposed tools only. Never invent or run raw SQL — there is no raw SQL tool.\n"
    "- Treat tool descriptions and argument schemas as authoritative. Do not "
    "invent fields or pass arguments that are not in the schema.\n"
    "- Multi-tenant: visibility is enforced server-side by RBAC. Do not attempt "
    "to bypass filters or assume access to records the caller cannot see.\n"
    "- Mutations (mutating=True) MUST be confirmed by the human via the "
    "pending_confirmation flow. Surface the proposed arguments and ask for "
    "confirmation in plain language; do not execute repeatedly.\n"
    "- When the user replies with a natural-language affirmative (yes, "
    "confirm, go ahead, da, potvrdi, etc.) AND there is a pending "
    "confirmation, the backend short-circuits to execute the stored tool "
    "automatically. Do NOT re-prompt for confirmation in this case — your "
    "next turn should already see the executed result. If you find yourself "
    "asking 'shall I go ahead?' a second time, stop and instead acknowledge "
    "what just happened.\n"
    "- If required arguments are missing or ambiguous, ask the user for them "
    "instead of guessing. Prefer one clarifying question over a wrong call.\n"
    "- Slot-filling flow for mutating tools: when the user has clearly "
    "expressed intent but is missing some required fields (e.g. 'request "
    "leave from June 11 to June 20' lacks `leave_type` and `reason`), call "
    "the mutating tool with the partial arguments you DO have. The backend "
    "will respond with `requires_input: true`, a list of `missing_fields`, "
    "and a `question`. Relay the question to the user in natural language "
    "(e.g. 'Which leave type — vacation, sick, personal? And what's the "
    "reason?'). When the user replies, call the same tool again with the "
    "new fields merged in.\n"
    "- Stay on the current task. Treat the orchestrator's planner hint as the "
    "single source of truth for what this turn is about. Do not drift into "
    "adjacent capabilities, summarize unrelated data, or invent follow-up "
    "actions unless the current task clearly requires them.\n"
    "- When a tool can answer the question, use the tool. Do not answer from "
    "memory or widen the task scope because you recognize a similar request.\n"
    "- Never surface raw tool JSON to the user unless the user explicitly "
    "asks for JSON. Summarize tool payloads into concise prose or a markdown "
    "table. If the user explicitly requests JSON, return the tool result as "
    "JSON instead of prose.\n"
    "- For any request that depends on the current moment or relative time "
    "(`now`, `today`, `tomorrow`, `yesterday`, `in 5 minutes`, `5 minutes "
    "from now`, scheduling, deadlines, expirations), call "
    "`get_current_datetime` first. Use `get_current_date` or "
    "`get_current_time` if you only need one part. Do not guess the current "
    "date/time.\n"
    "- Keep responses short, factual, and grounded in tool results. Never "
    "fabricate names, IDs, salaries, dates, or counts.\n"
    "- If you include a pipe table, do not include markdown alignment rows "
    "like `|---|---|` or `|:-:|:---|`; the frontend renders tables itself.\n"
    "\n"
    "Permission-aware help (CRITICAL):\n"
    "- Use `check_permission(tool_name=...)` or "
    "`list_available_actions(module=...)` when the user is asking about "
    "capability, access, or workflow, or after a tool call is blocked. Do "
    "not spend a direct action request on a permission lookup if you can "
    "call the action tool itself.\n"
    "- When a user asks 'How do I X?', 'Can I X?', 'Explain X', or sounds "
    "confused about a workflow, call `explain_workflow(topic=...)` (topics: "
    "create_employee, request_leave, approve_leave, submit_timesheet, "
    "create_role, create_asset, view_compensation, find_manager, "
    "list_documents). If unsure of the topic, call `list_workflows` first.\n"
    "- The result includes `can_run` (true/false), `deny_reason`, the UI path, "
    "and AI tools that automate parts. Quote those in your answer.\n"
    "- If the user CAN run an action, offer to do it for them via the AI "
    "(name the tool). If they CANNOT, tell them exactly why and point them "
    "to the UI path or the right team.\n"
    "- Never say 'I don't have access to that feature' without first probing "
    "via the above tools. The assistant has more tools than it seems.\n"
)


MODULE_PROMPTS = {
    "employees": (
        "You are the Employees subagent. Domain: employee profiles, departments, "
        "roles, managers, CPF levels, profile history. Tools: search_employees, "
        "get_employee_managers, get_employee_profile, list_reference_data. "
        "Never expose records outside the caller's permissions.\n\n"
        "If the user asks how to create or onboard an employee, call "
        "`explain_workflow(topic='create_employee')` — the assistant cannot yet "
        "create employees, but the workflow tool returns the UI path and the "
        "required role.\n\n" + SHARED_DOCTRINE
    ),
    "vacations": (
        "You are the Vacations subagent. Domain: leave balances, policies, "
        "requests, approvals. Tools: list_leave_balances, list_leave_policies, "
        "list_leave_requests, create_leave_request, approve_leave_request.\n\n"
        "Whenever you mention leave/vacation balances or remaining day counts, "
        "include a markdown table with at least `Leave Type` and "
        "`Remaining Days`. Preserve the `list_leave_balances` table when that "
        "tool returns one; do not convert it to prose-only text.\n\n"
        "When an approval, creation, or update is concrete enough to show the "
        "user a button or editable form, call the matching mutating tool once "
        "with proposed arguments so the backend returns structured "
        "`pending_confirmation` and `ui_action_type`. Do not ask a prose-only "
        "'would you like me to...' question when the tool can safely stage the "
        "same action for confirmation.\n\n"
        "Balance-first workflow for leave creation:\n"
        "1. BEFORE proposing a leave_request, call `list_leave_balances` to "
        "see how many days the user has remaining for the leave_type they "
        "want (default to vacation if unspecified — confirm with user).\n"
        "2. Compute requested business days only: Monday through Friday, "
        "inclusive of start_date and end_date, always excluding weekends. "
        "Vacations/leave balances are always working-day counts.\n"
        "3. If requested days > remaining: do NOT silently proceed. State "
        "the conflict ('You have X days remaining but asked for Y'), then "
        "propose the largest feasible range that fits the balance, anchored "
        "to the requested start_date (e.g. user asks 11.06 to 20.06 with "
        "4 days remaining → suggest 11.06 to 14.06). Ask the user to choose: "
        "shorter range, different dates, or submit anyway over-budget.\n"
        "4. If user picks a shorter range or different dates, call "
        "`create_leave_request` with the agreed values.\n"
        "5. Always confirm before `create_leave_request` or "
        "`approve_leave_request`. When the user replies 'yes' / 'confirm' / "
        "'go ahead' the backend will auto-execute the pending action — do "
        "NOT keep re-asking the same confirmation.\n\n" + SHARED_DOCTRINE
    ),
    "assets": (
        "You are the Assets subagent. Domain: equipment, assignments, returns. "
        "Tools: list_assets, create_asset, update_asset_status. Asset RBAC is "
        "enforced server-side.\n\n"
        "When the user clearly wants to create/register/add an asset, call "
        "`create_asset` with any fields you can infer. If required fields are "
        "missing, still call it once so the backend can ask for the missing "
        "details via slot filling.\n\n" + SHARED_DOCTRINE
    ),
    "documents": (
        "You are the Documents subagent. Domain: document metadata, visibility, "
        "expiry, document templates, AND document content. Tools: "
        "list_documents, list_document_templates, read_document_content.\n\n"
        "Content Q&A flow: when the user asks 'what does X say', 'summarize X', "
        "'is there a clause about Y', etc.:\n"
        "1. Call `list_documents` with a query that matches the document name "
        "or category. Pick the best matching `document.id`.\n"
        "2. If multiple plausible matches, ask the user which one before "
        "reading.\n"
        "3. Call `read_document_content(document_id=<id>)`. The tool returns "
        "extracted plain text from PDF/DOCX/TXT files (truncated to ~8k chars "
        "by default). If `truncated` is true, mention the document is large "
        "and offer to read a different section.\n"
        "4. Ground your answer in the returned `text`. Quote short snippets "
        "verbatim when relevant. NEVER fabricate clauses or numbers — if the "
        "text does not contain the answer, say so.\n"
        "5. Respect permissions: a `403 PermissionDenied` from "
        "`read_document_content` means the user cannot access that document.\n\n"
        "Templates: if the user asks for templates (offer letters, contract "
        "templates), call `list_document_templates`, not `list_documents`.\n\n"
        + SHARED_DOCTRINE
    ),
    "time_tracking": (
        "You are the Time Tracking subagent. Domain: time tasks, entries, weekly "
        "submission. Tools: list_time_entries, list_time_tasks, create_time_entry, "
        "submit_time_week, list_reference_data, get_current_date, "
        "get_current_datetime.\n\n"
        "Create-entry workflow: when the user wants to log or create a manual "
        "time entry, do not answer with a generic context tool or list tool. "
        "Resolve the project name with `list_reference_data` if needed, use "
        "`get_current_date` or `get_current_datetime` for phrases like "
        "'today', 'this date', or 'now', and compute `hours` from ranges like "
        "'9am til 3pm'. Then call `create_time_entry` once with the best "
        "arguments you have. If a required field is still missing, let the "
        "backend slot-fill it instead of stalling.\n\n"
        "Always confirm mutating calls.\n\n" + SHARED_DOCTRINE
    ),
    "onboarding": (
        "You are the Onboarding subagent. Domain: checklist templates and "
        "instances. Tools: create_onboarding_instance (HR-only). Confirm before "
        "creating.\n\n" + SHARED_DOCTRINE
    ),
    "reviews": (
        "You are the Reviews subagent. Domain: performance reviews, notes, "
        "action points, attachments.\n\n"
        "Read tools: list_reviews, get_review, list_review_notes, "
        "list_review_action_points.\n"
        "Mutating (confirmation required): schedule_review (HR/manager), "
        "add_review_note, add_review_action_point, update_action_point_status, "
        "close_review.\n\n"
        "Visibility: private notes are only seen by their author + HR. "
        "Action points are visible to the owner + reviewer + employee + HR.\n\n"
        + SHARED_DOCTRINE
    ),
    "training": (
        "You are the Training subagent. Domain: training entries, certificates, "
        "peer sessions, conference registrations, training budgets.\n\n"
        "Read: list_training_entries, list_certificates "
        "(use expiring_within_days for renewal reminders), list_peer_sessions, "
        "list_conference_registrations, get_training_budget, "
        "list_training_budgets (HR only).\n"
        "Mutating: create_training_entry, log_peer_session, "
        "register_for_conference.\n\n"
        "Default to the caller's own data when employee_id is omitted.\n\n"
        + SHARED_DOCTRINE
    ),
    "mobility_compensation": (
        "You are the Mobility and Compensation subagent. Domain: salaries, "
        "bonuses, benefits, CPF levels, promotions, job listings, applications, "
        "payroll snapshots.\n\n"
        "Read tools (any employee for own data; HR/comp admin for others):\n"
        "- list_bonuses(employee_id?, year?, bonus_type?) — bonus history\n"
        "- get_bonus_totals(employee_id?, year?) — totals grouped by bonus_type\n"
        "- list_compensation_policies() — NET salary per CPF level\n"
        "- list_benefits() — global active benefits\n"
        "- get_payroll_snapshot(snapshot_date?) — aggregate (HR-only)\n"
        "- list_promotion_history(employee_id?)\n"
        "- list_cpf_level_changes(employee_id?)\n"
        "- get_compensation_overview(employee_id?) — consolidated snapshot: "
        "latest salary + CPF policy + YTD bonuses + monthly benefits\n"
        "- list_top_paid_employees(limit?) — HR-only, sensitive\n"
        "- list_job_listings(status?, query?) — internal openings\n"
        "- list_applications(listing_id?, status?) — employees see own, HR sees all\n\n"
        "Mutating (require confirmation):\n"
        "- create_bonus_record(...) — compensation admin\n"
        "- record_promotion(employee_id, new_role_id?, date, new_cpf_level?, "
        "notes?, related_listing_id?) — HR; writes PromotionHistory + (if CPF "
        "differs) a CPFLevelChange row + updates the employee's role/CPF\n"
        "- record_cpf_level_change(employee_id, new_level, effective_date, "
        "source?, cpf_score?) — HR; direct CPF change without a promotion\n"
        "- set_compensation_policy(cpf_level, net_monthly, currency?, "
        "effective_date, notes?) — comp admin; upserts the NET policy\n"
        "- create_job_listing(title, description?, department_id?, open_at, "
        "close_at, status?) — HR\n"
        "- apply_to_job_listing(listing_id, cover_note) — any employee\n"
        "- update_application_status(application_id, status, decision_note) — HR\n"
        "- withdraw_application(application_id, reason?) — applicant or HR\n\n"
        "Rules:\n"
        "- Do NOT reveal another employee's salary, CPF policy, or bonus data "
        "to non-HR / non-comp-admin users.\n"
        "- For 'what's my comp?' / 'show my bonuses' just call the tool with "
        "no employee_id — it defaults to the caller.\n"
        "- For aggregate questions ('what's our total payroll?', 'how many "
        "people earn over X?') use get_payroll_snapshot or "
        "list_top_paid_employees as appropriate.\n"
        "- If user asks 'apply for X', call list_job_listings to find the id, "
        "then apply_to_job_listing.\n\n" + SHARED_DOCTRINE
    ),
    "announcements": (
        "You are the Announcements subagent. Domain: announcements, schedules, "
        "comments, reactions, and Discord announcement channels. Tools: "
        "list_announcements, get_announcement, create_announcement, "
        "update_announcement, delete_announcement, list_announcement_comments, "
        "add_announcement_comment, delete_announcement_comment, "
        "toggle_announcement_reaction, list_discord_announcement_channels, "
        "create_discord_announcement_channel, "
        "update_discord_announcement_channel, "
        "delete_discord_announcement_channel.\n\n"
        "For direct create/schedule/update/delete requests, call the matching "
        "announcement tool immediately with the best arguments you have. Do "
        "not stop after `check_permission` or paste tool metadata back to the "
        "user. Mutating announcement tools already stage human confirmation in "
        "the backend, so your job is to trigger the tool and then explain the "
        "confirmation step in plain language if needed. For any relative "
        "schedule phrasing like 'in 5 minutes', 'tomorrow morning', or "
        "'next Friday', call `get_current_datetime` before choosing "
        "`scheduled_at`.\n\n"
        "If the user is asking whether they can do something, or how the "
        "announcement workflow works, then use the permission and workflow "
        "helpers.\n\n" + SHARED_DOCTRINE
    ),
    "notifications": (
        "You are the Notifications subagent. Tools: list_notifications, "
        "mark_all_notifications_read.\n\n" + SHARED_DOCTRINE
    ),
    "admin": (
        "You are the Admin subagent. No write tools are exposed. Refuse "
        "system-mutating requests and route the user to a human admin.\n\n"
        + SHARED_DOCTRINE
    ),
    "general": (
        "You are the General BloomHub assistant. Route to the smallest safe tool "
        "set. Ask for missing required fields before calling any tool.\n\n"
        + SHARED_DOCTRINE
    ),
}


KEYWORD_MODULES = (
    ("employees", ("employee", "profile", "manager", "department", "role", "cpf")),
    ("vacations", ("leave", "vacation", "pto", "sick", "balance", "calendar")),
    ("assets", ("asset", "equipment", "laptop", "qr", "maintenance", "return")),
    ("documents", ("document", "signature", "template", "policy", "contract")),
    (
        "announcements",
        (
            "announcement",
            "announcements",
            "publish",
            "post",
            "broadcast",
        ),
    ),
    ("time_tracking", ("time", "timesheet", "worklog", "jira", "tempo", "hours")),
    ("onboarding", ("onboarding", "offboarding", "checklist", "task")),
    ("reviews", ("review", "performance", "feedback", "action point")),
    ("training", ("training", "certificate", "budget", "course", "conference")),
    (
        "mobility_compensation",
        (
            "job",
            "application",
            "promotion",
            "salary",
            "bonus",
            "benefit",
            "compensation",
        ),
    ),
    ("notifications", ("notification", "unread", "bell")),
    ("admin", ("integration", "permission", "security", "api key", "system")),
)


def module_manifest() -> list[dict[str, str]]:
    return [
        {"module": module, "prompt": prompt}
        for module, prompt in MODULE_PROMPTS.items()
        if module != "general"
    ]


def infer_module(message: str, tool_module: str | None = None) -> str:
    if tool_module:
        return tool_module
    text = message.lower()
    for module, keywords in KEYWORD_MODULES:
        if any(keyword in text for keyword in keywords):
            return module
    return "general"


def module_tools(registry: ToolRegistry, module: str):
    tools = [tool for tool in registry._tools.values() if tool.module == module]
    if module != "general":
        tools.extend(
            tool for tool in registry._tools.values() if tool.module == "general"
        )
    return tools


def create_react_subagent(
    *,
    user,
    session: AIChatSession,
    module: str,
    registry: ToolRegistry,
    llm,
    confirmed: bool = False,
):
    """Create a LangGraph prebuilt ReAct subagent for one BloomHub module.

    Returns None only when there is no LLM. When the LLM is configured but
    LangChain/LangGraph dependencies are missing, raises LLMUnavailableError
    so callers can surface a clear error instead of silently degrading.
    """
    if llm is None:
        return None
    if (
        StructuredTool is None
        or create_react_agent is None
        or BaseModel is None
        or Field is None
    ):
        raise LLMUnavailableError(
            "LangChain/LangGraph not installed; ReAct subagent cannot be built."
        )

    lc_tools = []
    for assistant_tool in module_tools(registry, module):
        schema = assistant_tool.args_schema or _fallback_args_schema()

        def run_tool(*, _tool=assistant_tool, **arguments):
            return execute_tool(
                registry=registry,
                session=session,
                user=user,
                tool_name=_tool.name,
                arguments=arguments or {},
                confirmed=confirmed,
            )

        lc_tools.append(
            StructuredTool.from_function(
                func=run_tool,
                name=assistant_tool.name,
                description=assistant_tool.description,
                args_schema=schema,
            )
        )

    if not lc_tools:
        return None
    return create_react_agent(
        model=llm,
        tools=lc_tools,
        prompt=MODULE_PROMPTS.get(module, MODULE_PROMPTS["general"]),
    )


def _fallback_args_schema():
    if create_model is None or BaseModel is None or Field is None:
        return None
    return create_model(
        "ToolArguments",
        arguments=(dict[str, Any], Field(default_factory=dict)),
    )


def invoke_subagent(
    agent,
    prompt: str,
    history: list[dict[str, str]] | None = None,
    planner_hint: str | None = None,
    module: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Run the ReAct subagent with prior conversation context.

    History is a list of {role, content} dicts (chronological). Roles map:
    user → ("user", ...), assistant → ("ai", ...), system → ("system", ...).
    Tool messages are filtered (the subagent re-derives them from its own
    tool calls; injecting them naively breaks LangGraph state).

    `planner_hint` is the orchestrator's task rewrite. It is injected as a
    system message right before the final user turn so the subagent acts
    on it as instruction rather than echoing it as user content.
    """
    started_at = time.perf_counter()
    messages: list[tuple[str, str]] = []
    for entry in history or []:
        role = entry.get("role")
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            messages.append(("user", content))
        elif role == "assistant":
            messages.append(("ai", content))
        elif role == "system":
            messages.append(("system", content))
        # skip "tool" — LangGraph manages its own tool messages

    # Drop a trailing user turn that duplicates the current prompt so we do
    # not feed two consecutive user messages to the model.
    if (
        messages
        and messages[-1][0] == "user"
        and messages[-1][1].strip() == prompt.strip()
    ):
        messages.pop()

    if planner_hint:
        messages.append(
            (
                "system",
                "Current task from the orchestrator. This is the highest-priority "
                "instruction for this turn. Do not drift from it or replace it "
                "with a different topic:\n" + planner_hint,
            )
        )
    messages.append(("user", prompt))

    recursion_limit = int(getattr(settings, "AI_AGENT_MAX_TOOL_STEPS", 6))
    response = agent.invoke(
        {"messages": messages},
        config={"recursion_limit": recursion_limit},
    )
    usage = extract_token_usage(response)
    out_messages = response.get("messages", []) if isinstance(response, dict) else []
    if not out_messages:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.warning(
            "[AI] subagent.complete module=%s prompt_chars=%s history_chars=%s planner_hint_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f",
            module,
            len(prompt or ""),
            sum(len(entry.get("content") or "") for entry in history or []),
            len(planner_hint or ""),
            0,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            elapsed_ms,
        )
        return "", usage
    # Walk from the end and return the last non-empty AI text. Tool calls
    # often produce messages with empty content but populated tool_calls;
    # skip those so we return human-facing text.
    for message in reversed(out_messages):
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "[AI] subagent.complete module=%s prompt_chars=%s history_chars=%s planner_hint_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f",
                module,
                len(prompt or ""),
                sum(len(entry.get("content") or "") for entry in history or []),
                len(planner_hint or ""),
                len(content),
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
                elapsed_ms,
            )
            return content, usage
        if isinstance(content, list):
            # LangChain message content can be a list of content blocks
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p).strip()
            if joined:
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                logger.warning(
                    "[AI] subagent.complete module=%s prompt_chars=%s history_chars=%s planner_hint_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f",
                    module,
                    len(prompt or ""),
                    sum(len(entry.get("content") or "") for entry in history or []),
                    len(planner_hint or ""),
                    len(joined),
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    elapsed_ms,
                )
                return joined, usage
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.warning(
        "[AI] subagent.complete module=%s prompt_chars=%s history_chars=%s planner_hint_chars=%s response_chars=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s elapsed_ms=%.1f",
        module,
        len(prompt or ""),
        sum(len(entry.get("content") or "") for entry in history or []),
        len(planner_hint or ""),
        0,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
        elapsed_ms,
    )
    return "", usage
