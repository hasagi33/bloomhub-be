"""Human-readable workflow documentation surfaced through the AI assistant.

The point of this registry is to give the assistant something useful to say
when a user asks "How do I X?" or "Can I X?". Each entry links a topic to
its description, the UI path users can follow manually, the AI tools that
automate parts of it, and the required permissions. Permission gating is
re-checked at probe time so the assistant tells the user, specifically,
whether *they* can do something — not just whether the feature exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PermissionPredicate = Callable[[Any], bool]


@dataclass(frozen=True)
class Workflow:
    topic: str
    title: str
    description: str
    module: str
    ui_path: str = ""
    ai_tools: tuple[str, ...] = field(default_factory=tuple)
    required_permissions: tuple[str, ...] = field(default_factory=tuple)
    steps: tuple[str, ...] = field(default_factory=tuple)
    can_run: PermissionPredicate | None = None
    deny_reason: str = ""


# Permission predicates — defined lazily to avoid import cycles
def _is_authenticated(user) -> bool:
    return bool(getattr(user, "is_authenticated", False))


def _is_staff_or_super(user) -> bool:
    return bool(
        getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
    )


def _is_hr(user) -> bool:
    from core.ai.permissions import is_hr_admin

    return is_hr_admin(user)


def _is_compensation_admin(user) -> bool:
    from core.permissions import is_compensation_admin

    return is_compensation_admin(user)


def _can_configure_assets(user) -> bool:
    from core.permissions import has_asset_permission

    return has_asset_permission(user, "configure_asset_types")


def _can_assign_assets(user) -> bool:
    from core.permissions import has_asset_permission

    return has_asset_permission(user, "assign_assets")


def _is_manager_or_hr(user) -> bool:
    if _is_hr(user):
        return True
    from core.models import UserProfile

    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return UserProfile.objects.filter(managers=profile).exists()


WORKFLOWS: dict[str, Workflow] = {
    "create_employee": Workflow(
        topic="create_employee",
        title="Create a new employee",
        description=(
            "Onboard a new employee record (user account + profile). The AI "
            "cannot create employees yet; this must be done through the UI by "
            "an HR admin."
        ),
        module="employees",
        ui_path="/people/new",
        ai_tools=(),
        required_permissions=("HR admin",),
        can_run=_is_hr,
        deny_reason=(
            "Only HR admins can create employees. Ask your HR team to add the "
            "person, or open `/people/new` if you have HR access."
        ),
        steps=(
            "Go to People → New Employee in the sidebar.",
            "Fill in name, email, role, department, CPF level, start date.",
            "Invite the employee — they will receive a signup link.",
            "Optionally assign managers and start an onboarding checklist via "
            "`create_onboarding_instance`.",
        ),
    ),
    "request_leave": Workflow(
        topic="request_leave",
        title="Request a leave / vacation",
        description="Submit a vacation, sick, or personal-day request.",
        module="vacations",
        ui_path="/leave/new",
        ai_tools=("create_leave_request",),
        required_permissions=("any employee",),
        can_run=_is_authenticated,
        steps=(
            "Tell the assistant: 'Request vacation from 2026-06-01 to "
            "2026-06-05 for family trip'.",
            "Review the pre-filled form. Adjust dates, leave type, reason, or "
            "covering employee.",
            "Click Confirm. The request goes to your manager for approval.",
        ),
    ),
    "approve_leave": Workflow(
        topic="approve_leave",
        title="Approve a leave request",
        description="Approve a direct report's leave (manager) or final-sign as HR.",
        module="vacations",
        ui_path="/leave/inbox",
        ai_tools=("approve_leave_request", "list_leave_requests"),
        required_permissions=("manager of the requester OR HR admin",),
        can_run=_is_manager_or_hr,
        deny_reason="You must manage the requester or be an HR admin to approve.",
        steps=(
            "Ask 'show pending leave requests' to list inbox.",
            "Ask 'approve request 42' (or specify hr_final=true for HR sign-off).",
            "Review pre-filled form and Confirm.",
        ),
    ),
    "submit_timesheet": Workflow(
        topic="submit_timesheet",
        title="Submit weekly time entries",
        description="Submit your weekly time entries for approval.",
        module="time_tracking",
        ui_path="/time/week",
        ai_tools=("submit_time_week", "create_time_entry", "list_time_entries"),
        required_permissions=("any employee",),
        can_run=_is_authenticated,
        steps=(
            "Ensure entries for the week are recorded (create_time_entry).",
            "Ask 'submit time for week of 2026-05-25'.",
            "Confirm the pre-filled form.",
        ),
    ),
    "create_role": Workflow(
        topic="create_role",
        title="Create or edit a role",
        description="Define a new role with a permission set, or update an existing one.",
        module="admin",
        ui_path="/admin/roles",
        ai_tools=("create_role", "update_role", "delete_role", "list_permissions"),
        required_permissions=("staff or superuser",),
        can_run=_is_staff_or_super,
        deny_reason="Only staff or superusers can manage roles.",
        steps=(
            "Ask 'list permissions' to see available permission_ids.",
            "Ask 'create role X with permissions A, B, C'.",
            "Review and confirm the pre-filled form.",
        ),
    ),
    "create_asset": Workflow(
        topic="create_asset",
        title="Register a new asset",
        description="Add a piece of equipment (laptop, monitor, phone, etc.) to inventory.",
        module="assets",
        ui_path="/assets/new",
        ai_tools=("create_asset", "update_asset_status", "list_assets"),
        required_permissions=("Asset Management → configure_asset_types",),
        can_run=_can_configure_assets,
        deny_reason="You need the Asset Management 'configure_asset_types' permission.",
        steps=(
            "Ask 'create asset BLHB-LP-042, name MacBook Pro 16, category LAPTOP, "
            "purchase date 2026-05-01'.",
            "Confirm the form. Adjust manufacturer, model, serial number, price.",
        ),
    ),
    "view_compensation": Workflow(
        topic="view_compensation",
        title="View top paid employees / salaries",
        description="Inspect salary records and compensation policies.",
        module="mobility_compensation",
        ui_path="/compensation",
        ai_tools=("list_top_paid_employees",),
        required_permissions=("Compensation admin (HR)",),
        can_run=_is_compensation_admin,
        deny_reason="Salary data is restricted to HR / Compensation admins.",
        steps=(
            "Ask 'show top 10 paid employees'.",
            "Salary source (record vs policy) is annotated per row.",
        ),
    ),
    "find_manager": Workflow(
        topic="find_manager",
        title="Find an employee's manager",
        description="Look up who manages a given person.",
        module="employees",
        ui_path="/people",
        ai_tools=("get_employee_managers", "search_employees"),
        required_permissions=("any employee",),
        can_run=_is_authenticated,
        steps=(
            "Ask 'who is <name>'s manager?' — the assistant will look up the "
            "org chart and return the manager(s).",
        ),
    ),
    "list_documents": Workflow(
        topic="list_documents",
        title="Find documents and policies",
        description="Search HR documents you have access to.",
        module="documents",
        ui_path="/documents",
        ai_tools=("list_documents", "list_document_templates"),
        required_permissions=("any employee",),
        can_run=_is_authenticated,
        steps=(
            "Ask 'show expiring documents' or 'find document <keyword>'.",
            "Ask 'list document templates' to see reusable templates.",
            "Document visibility is filtered by access rules server-side.",
        ),
    ),
    "read_document": Workflow(
        topic="read_document",
        title="Read or summarize a document's content",
        description=(
            "Pull the actual text inside a PDF, DOCX, or plain-text document "
            "so the assistant can answer questions about it."
        ),
        module="documents",
        ui_path="/documents",
        ai_tools=("list_documents", "read_document_content"),
        required_permissions=("any employee with access to the document",),
        can_run=_is_authenticated,
        steps=(
            "Ask 'summarize <document name>' or 'what does <doc> say about X'.",
            "The assistant calls list_documents to find the id, then "
            "read_document_content to fetch the extracted text.",
            "Answers are grounded in the extracted content; large docs are "
            "truncated to ~8k chars by default.",
            "Confidential documents you do not have access to will be blocked "
            "by server-side permissions.",
        ),
    ),
}


def describe_workflow(workflow: Workflow, user) -> dict[str, Any]:
    can_run = workflow.can_run(user) if workflow.can_run else True
    return {
        "topic": workflow.topic,
        "title": workflow.title,
        "description": workflow.description,
        "module": workflow.module,
        "ui_path": workflow.ui_path,
        "ai_tools": list(workflow.ai_tools),
        "required_permissions": list(workflow.required_permissions),
        "steps": list(workflow.steps),
        "can_run": can_run,
        "deny_reason": "" if can_run else workflow.deny_reason,
    }


def workflow_index(user) -> list[dict[str, Any]]:
    return [describe_workflow(w, user) for w in WORKFLOWS.values()]
