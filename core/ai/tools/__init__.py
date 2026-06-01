from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from django.db import transaction
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from pydantic import BaseModel, ConfigDict, Field
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.ai.permissions import (
    compact_user,
    is_hr_admin,
    is_privileged_global_viewer,
    require_profile,
)
from core.ai.tooling import AssistantTool, ToolRegistry, probe_permission
from core.ai.workflows import WORKFLOWS, describe_workflow, workflow_index
from core.enums import LeaveType, TemplateVisibility
from core.models import (
    Announcement,
    AnnouncementComment,
    AnnouncementReaction,
    Application,
    Asset,
    AssetCategory,
    Assignment,
    BenefitCatalog,
    BonusRecord,
    Certificate,
    ChecklistInstance,
    ChecklistTemplate,
    CompensationPolicy,
    ConferenceCourseRegistration,
    CPFLevelChange,
    Department,
    DiscordAnnouncementChannel,
    Document,
    DocumentTemplate,
    EmployeeDocument,
    EquipmentAssignment,
    JobListing,
    LeaveBalance,
    LeaveMonthlyAggregate,
    LeavePolicy,
    LeaveRequest,
    Notification,
    PayrollSnapshot,
    PeerSession,
    PerformanceReview,
    PerformanceReviewActionPoint,
    PerformanceReviewNote,
    Project,
    ProjectAssignment,
    PromotionHistory,
    ReplacementLog,
    Role,
    SalaryRecord,
    ScheduledMaintenance,
    TemplateField,
    TemplateGeneratedDocument,
    TimeEntry,
    TimeTask,
    TrainingBudget,
    TrainingEntry,
    UserProfile,
    UserTemplateSnippet,
)
from core.models import (
    Permission as PermissionModel,
)
from core.permissions import (
    IsHRAdminForAdjustment,
    IsManagerForApproval,
    can_add_announcement_reactions,
    can_manage_announcements,
    can_moderate_announcement_comments,
    can_schedule_announcements,
    can_view_anniversaries,
    can_view_announcements,
    can_view_asset,
    can_view_asset_maintenance_logs,
    can_view_birthdays,
    has_asset_permission,
    has_leave_analytics_refresh_permission,
    has_leave_analytics_view_permission,
    has_own_leave_history_permission,
    is_compensation_admin,
)
from core.serializers import (
    AnnouncementCommentSerializer,
    AnnouncementDetailSerializer,
    AnnouncementListSerializer,
    AnnouncementReactionSerializer,
    AnnouncementWriteSerializer,
    AssetSerializer,
    ChecklistInstanceSerializer,
    DiscordAnnouncementChannelSerializer,
    DocumentListSerializer,
    DocumentTemplateCreateUpdateSerializer,
    DocumentTemplateDetailSerializer,
    DocumentTemplateListSerializer,
    DocumentTemplatePartialUpdateSerializer,
    EmployeeProfileSerializer,
    LeaveBalanceSerializer,
    LeavePolicySerializer,
    LeaveRequestCreateSerializer,
    LeaveRequestDetailSerializer,
    LeaveRequestListSerializer,
    NotificationSerializer,
    ScheduledMaintenanceCancelSerializer,
    ScheduledMaintenanceCompleteSerializer,
    ScheduledMaintenanceSerializer,
    TemplateGeneratedDocumentSerializer,
    TimeEntrySerializer,
    TimeTaskSerializer,
    UpcomingCelebrationSerializer,
    UserTemplateSnippetSerializer,
)
from core.services.announcement_notification_service import (
    announcement_is_published,
    notify_announcement_published,
)
from core.services.celebrations import build_upcoming_profile_celebrations
from core.services.document_service import (
    filter_accessible_documents,
    get_document_category_defaults,
)
from core.services.leave_analytics_service import (
    employee_history as build_leave_employee_history,
)
from core.services.leave_analytics_service import (
    materialize_leave_monthly_aggregates,
    snapshot_leave_balances,
    team_availability,
    yearly_totals_by_type,
)
from core.services.leave_service import (
    approve_leave_request_hr,
    approve_leave_request_lead,
)
from core.services.time_tracking_service import (
    active_time_tracking_allocations,
    can_edit_time_entry,
    can_view_employee_timesheet,
    has_time_tracking_permission,
    profile_for_user,
    submit_entries_for_week,
    weekly_allocation_summary,
)
from core.utils import (
    clone_template,
    resolve_template_content,
    validate_template_fields,
)

registry = ToolRegistry()


def _request_for(user):
    return SimpleNamespace(user=user)


def _limit(value: int | None, default: int = 10, maximum: int = 50) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def _can_view_all_leave(user) -> bool:
    return is_hr_admin(user) or is_privileged_global_viewer(user)


def _can_view_all_time(user) -> bool:
    return is_hr_admin(user) or is_privileged_global_viewer(user)


def _check_hr_admin(user) -> tuple[bool, str]:
    if is_hr_admin(user):
        return True, ""
    return False, "HR admin required."


def _check_compensation_admin(user) -> tuple[bool, str]:
    if is_compensation_admin(user):
        return True, ""
    return False, "Compensation admin (HR) required."


def _check_staff(user) -> tuple[bool, str]:
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True, ""
    return False, "Staff or superuser required."


def _check_asset_configure(user) -> tuple[bool, str]:
    from core.permissions import has_asset_permission

    if has_asset_permission(user, "configure_asset_types"):
        return True, ""
    return False, "Asset Management 'configure_asset_types' permission required."


def _check_manager_or_hr(user) -> tuple[bool, str]:
    if is_hr_admin(user):
        return True, ""
    profile = getattr(user, "profile", None)
    if profile and UserProfile.objects.filter(managers=profile).exists():
        return True, ""
    return False, "Must be a direct manager of the requester or HR admin."


def _strip_diacritics(text: str) -> str:
    if not text:
        return ""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _match_query(profile, query_norm: str) -> bool:
    """Case- and diacritic-insensitive substring match across name/email fields."""
    if not query_norm:
        return True
    candidates = [
        profile.full_name,
        getattr(profile.user, "first_name", ""),
        getattr(profile.user, "last_name", ""),
        getattr(profile.user, "email", ""),
        profile.email_address,
        profile.employee_id,
    ]
    haystack = " ".join(_strip_diacritics(c or "").lower() for c in candidates)
    return query_norm in haystack


class _ArgsBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyArgs(_ArgsBase):
    pass


class SearchEmployeesArgs(_ArgsBase):
    query: str = ""
    limit: int | None = Field(default=10, ge=1, le=50)
    count_only: bool = False


class GetEmployeeManagersArgs(_ArgsBase):
    query: str = Field(..., min_length=1)
    limit: int | None = Field(default=5, ge=1, le=50)


class GetEmployeeProfileArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)


class ListTopPaidEmployeesArgs(_ArgsBase):
    limit: int | None = Field(default=5, ge=1, le=50)


class ListLeaveBalancesArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=2000, le=2100)


class CreateLeaveRequestArgs(_ArgsBase):
    leave_type: str = Field(..., min_length=1)
    start_date: str = Field(..., min_length=8)
    end_date: str = Field(..., min_length=8)
    reason: str = ""
    covering_employee_id: int | None = Field(default=None, ge=1)


class ListLeaveRequestsArgs(_ArgsBase):
    status: str | None = None
    limit: int | None = Field(default=10, ge=1, le=50)


class ApproveLeaveRequestArgs(_ArgsBase):
    leave_request_id: int = Field(..., ge=1)
    comments: str = ""
    hr_final: bool = False


class ListAssetsArgs(_ArgsBase):
    query: str = ""
    limit: int | None = Field(default=10, ge=1, le=50)
    count_only: bool = False


class ListDocumentsArgs(_ArgsBase):
    query: str = ""
    expired: bool = False
    limit: int | None = Field(default=10, ge=1, le=50)
    count_only: bool = False


class ListDocumentTemplatesArgs(_ArgsBase):
    query: str = ""
    category: str | None = None
    visibility: str | None = None
    is_system_template: bool | None = None
    limit: int | None = Field(default=10, ge=1, le=50)
    count_only: bool = False


class ListTimeEntriesArgs(_ArgsBase):
    date_from: str | None = None
    date_to: str | None = None
    limit: int | None = Field(default=10, ge=1, le=50)


class CreateTimeEntryArgs(_ArgsBase):
    project_id: int = Field(..., ge=1)
    task_id: int | None = Field(default=None, ge=1)
    work_date: str = Field(..., min_length=8)
    hours: str = Field(..., min_length=1)
    description: str = ""


class SubmitTimeWeekArgs(_ArgsBase):
    week_start: str = Field(..., min_length=8)
    employee_id: int | None = Field(default=None, ge=1)


class ListTimeTasksArgs(_ArgsBase):
    project_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=20, ge=1, le=50)


class CreateOnboardingInstanceArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    template_id: int = Field(..., ge=1)
    due_date: str | None = None


class ListNotificationsArgs(_ArgsBase):
    unread: bool = False
    limit: int | None = Field(default=10, ge=1, le=50)


def _current_time_snapshot() -> dict[str, Any]:
    now = timezone.localtime(timezone.now())
    tz_name = str(timezone.get_current_timezone())
    return {
        "date": now.date().isoformat(),
        "time": now.time().replace(microsecond=0).isoformat(),
        "datetime": now.isoformat(),
        "timezone": tz_name,
        "summary": (f"Current local date/time is {now:%Y-%m-%d %H:%M:%S} {tz_name}."),
    }


def get_current_user_context(*, user) -> dict[str, Any]:
    profile = require_profile(user)
    permissions = []
    if getattr(profile, "role", None):
        permissions.extend(
            f"{perm.module_name}:{perm.feature_action}"
            for perm in profile.role.permissions.all().order_by(
                "module_name", "feature_action"
            )
        )
    return {
        "user": compact_user(user),
        "profile": EmployeeProfileSerializer(profile).data,
        "permissions": permissions,
        "summary": "Loaded current user context.",
    }


def get_current_datetime(*, user) -> dict[str, Any]:
    return _current_time_snapshot()


def get_current_date(*, user) -> dict[str, Any]:
    snapshot = _current_time_snapshot()
    return {
        "date": snapshot["date"],
        "timezone": snapshot["timezone"],
        "summary": f"Today is {snapshot['date']} {snapshot['timezone']}.",
    }


def get_current_time(*, user) -> dict[str, Any]:
    snapshot = _current_time_snapshot()
    return {
        "time": snapshot["time"],
        "timezone": snapshot["timezone"],
        "summary": f"Current time is {snapshot['time']} {snapshot['timezone']}.",
    }


def search_employees(
    *, user, query: str = "", limit: int | None = 10, count_only: bool = False
) -> dict[str, Any]:
    require_profile(user)
    qs = (
        UserProfile.objects.filter(is_active=True)
        .select_related("user", "role")
        .prefetch_related("managers")
        .order_by("full_name", "id")
    )
    query_norm = _strip_diacritics(query).lower().strip()
    if query_norm:
        candidates = list(qs[:500])
        matched = [p for p in candidates if _match_query(p, query_norm)]
        total_count = len(matched)
        results = matched[: _limit(limit)]
    else:
        total_count = qs.count()
        results = list(qs[: _limit(limit)])
    if count_only:
        label = f" matching `{query}`" if query_norm else ""
        return {
            "employees": [],
            "total_count": total_count,
            "returned_count": 0,
            "summary": f"There are {total_count} employee profile(s){label}.",
        }
    return {
        "employees": EmployeeProfileSerializer(results, many=True).data,
        "total_count": total_count,
        "returned_count": len(results),
        "summary": f"Found {len(results)} of {total_count} employee profile(s).",
    }


def get_employee_managers(*, user, query: str, limit: int | None = 5) -> dict[str, Any]:
    require_profile(user)
    qs = (
        UserProfile.objects.filter(is_active=True)
        .select_related("user", "role")
        .prefetch_related("managers__user", "managers__role")
        .order_by("full_name", "id")
    )
    query_norm = _strip_diacritics(query).lower().strip()
    if query_norm:
        candidates = list(qs[:500])
        matched = [p for p in candidates if _match_query(p, query_norm)]
        selected = matched[: _limit(limit)]
    else:
        selected = list(qs[: _limit(limit)])
    employees = []
    for employee in selected:
        managers = [
            {
                "id": manager.id,
                "full_name": manager.full_name
                or manager.user.get_full_name()
                or manager.user.username,
                "email": manager.user.email or manager.email_address,
                "role": manager.role.name if manager.role else None,
            }
            for manager in employee.managers.all()
        ]
        employees.append(
            {
                "id": employee.id,
                "full_name": employee.full_name
                or employee.user.get_full_name()
                or employee.user.username,
                "email": employee.user.email or employee.email_address,
                "managers": managers,
            }
        )
    if not employees:
        summary = f"No visible employee found for `{query}`."
    else:
        lines = []
        for employee in employees:
            manager_names = ", ".join(
                manager["full_name"] for manager in employee["managers"]
            )
            lines.append(
                f"{employee['full_name']}: {manager_names or 'no manager assigned'}"
            )
        summary = "Managers:\n" + "\n".join(lines)
    return {"employees": employees, "summary": summary}


def get_employee_profile(*, user, employee_id: int | None = None) -> dict[str, Any]:
    profile = require_profile(user)
    target_id = employee_id or profile.id
    try:
        employee = UserProfile.objects.select_related("user", "role").get(pk=target_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee profile not found."}) from exc
    if employee.user_id != user.id and not is_hr_admin(user):
        raise PermissionDenied("You can only view your own employee profile.")
    return {
        "employee": EmployeeProfileSerializer(employee).data,
        "summary": "Loaded employee profile.",
    }


def list_reference_data(*, user) -> dict[str, Any]:
    require_profile(user)
    return {
        "departments": list(Department.objects.order_by("name").values("id", "name")),
        "roles": list(Role.objects.order_by("name").values("id", "name")),
        "projects": list(
            Project.objects.order_by("name").values("id", "name", "status")
        ),
        "summary": "Loaded reference data.",
    }


def list_top_paid_employees(*, user, limit: int | None = 5) -> dict[str, Any]:
    require_profile(user)
    if not is_compensation_admin(user):
        raise PermissionDenied("Compensation data is HR-only.")

    latest_gross_salary = (
        SalaryRecord.objects.filter(user_profile=OuterRef("pk"))
        .order_by("-effective_date", "-id")
        .values("amount")[:1]
    )
    qs = list(
        UserProfile.objects.select_related("user", "role")
        .annotate(
            ai_gross_salary=Subquery(
                latest_gross_salary,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .filter(is_active=True)
    )
    policies = {
        policy.cpf_level: policy.net_monthly
        for policy in CompensationPolicy.objects.all()
    }

    ranked = []
    for profile in qs:
        gross_salary = profile.ai_gross_salary
        net_salary = policies.get(profile.cpf_level)
        salary_value = gross_salary if gross_salary is not None else net_salary
        if salary_value is None:
            continue
        ranked.append((Decimal(salary_value), gross_salary, net_salary, profile))

    ranked.sort(
        key=lambda item: (
            item[0],
            item[3].full_name or item[3].user.get_full_name() or item[3].user.username,
        ),
        reverse=True,
    )
    employees = []
    for salary_value, gross_salary, net_salary, profile in ranked[
        : _limit(limit, default=5)
    ]:
        employees.append(
            {
                "id": profile.id,
                "employee_id": profile.employee_id,
                "full_name": profile.full_name
                or profile.user.get_full_name()
                or profile.user.username,
                "email": profile.user.email or profile.email_address,
                "department": profile.department,
                "role": profile.role.name if profile.role else None,
                "cpf_level": profile.cpf_level,
                "current_salary": (
                    str(gross_salary) if gross_salary is not None else None
                ),
                "current_net_salary": (
                    str(net_salary) if net_salary is not None else None
                ),
                "ranking_salary": str(salary_value),
                "salary_source": (
                    "salary_record"
                    if gross_salary is not None
                    else "compensation_policy"
                ),
            }
        )
    return {
        "employees": employees,
        "summary": f"Found {len(employees)} employee(s) with highest salary.",
    }


def list_leave_balances(
    *, user, employee_id: int | None = None, year: int | None = None
) -> dict[str, Any]:
    profile = require_profile(user)
    target = profile
    if employee_id and employee_id != profile.id:
        if not IsHRAdminForAdjustment().has_permission(_request_for(user), None):
            raise PermissionDenied("You cannot view another employee's leave balances.")
        target = UserProfile.objects.get(pk=employee_id)
    qs = LeaveBalance.objects.filter(employee=target).select_related("employee__user")
    if year:
        qs = qs.filter(year=year)
    balances = LeaveBalanceSerializer(qs.order_by("leave_type"), many=True).data
    return {"balances": balances, "summary": _format_leave_balance_summary(balances)}


def list_leave_policies(*, user) -> dict[str, Any]:
    require_profile(user)
    qs = LeavePolicy.objects.order_by("leave_type")
    return {
        "policies": LeavePolicySerializer(qs, many=True).data,
        "summary": "Loaded leave policies.",
    }


def _normalize_leave_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    for choice in LeaveType:
        candidates = {
            choice.value.lower(),
            choice.label.lower(),
            choice.name.lower(),
            choice.label.replace(" ", "_").lower(),
        }
        if normalized in candidates:
            return choice.value
    return str(value or "").strip()


def _leave_type_table_label(item: dict[str, Any]) -> str:
    labels = {
        "vacation": "Vacation",
        "sick": "Sick Leave",
        "personal": "Personal",
        "unpaid": "Unpaid",
        "wfh": "Work From Home",
    }
    icons = {
        "vacation": "🏖️",
        "sick": "😷",
        "personal": "👤",
        "unpaid": "💼",
        "wfh": "🏡",
    }
    leave_type = str(item.get("leave_type") or "")
    label = labels.get(leave_type) or str(
        item.get("leave_type_display") or leave_type.replace("_", " ").title()
    )
    icon = icons.get(leave_type)
    return f"{icon} {label}" if icon else label


def _format_leave_balance_summary(balances: list[dict[str, Any]]) -> str:
    if not balances:
        return "No leave balances found."
    table_rows = [
        "| Leave Type | Remaining Days | Used | Allocated |",
        "| --- | ---: | ---: | ---: |",
    ]
    for item in balances:
        table_rows.append(
            "| {label} | {remaining} | {used} | {allocated} |".format(
                label=_leave_type_table_label(item),
                remaining=item["remaining"],
                used=item["used"],
                allocated=item["allocated"],
            )
        )
    return "Leave balances:\n\n" + "\n".join(table_rows)


def _normalize_leave_date(value: str) -> str:
    text = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue
    return text


def create_leave_request(
    *,
    user,
    leave_type: str,
    start_date: str,
    end_date: str,
    reason: str = "",
    covering_employee_id: int | None = None,
) -> dict[str, Any]:
    require_profile(user)
    payload = {
        "leave_type": _normalize_leave_type(leave_type),
        "start_date": _normalize_leave_date(start_date),
        "end_date": _normalize_leave_date(end_date),
        "reason": (reason or "").strip() or "Not specified",
    }
    if covering_employee_id is not None:
        payload["covering_employee_id"] = covering_employee_id
    serializer = LeaveRequestCreateSerializer(
        data=payload, context={"request": _request_for(user)}
    )
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        leave_request = serializer.save()
    return {
        "leave_request": LeaveRequestDetailSerializer(leave_request).data,
        "summary": "Created leave request.",
    }


def list_leave_requests(
    *, user, status: str | None = None, limit: int | None = 10
) -> dict[str, Any]:
    profile = require_profile(user)
    if _can_view_all_leave(user):
        qs = LeaveRequest.objects.all()
    else:
        qs = LeaveRequest.objects.filter(
            Q(employee=profile) | Q(employee__managers=profile)
        )
    if status:
        qs = qs.filter(status=status)
    qs = qs.select_related("employee__user", "covering_employee__user").order_by(
        "-submitted_date"
    )[: _limit(limit)]
    return {
        "leave_requests": LeaveRequestListSerializer(qs, many=True).data,
        "summary": f"Loaded {len(qs)} leave request(s).",
    }


def approve_leave_request(
    *, user, leave_request_id: int, comments: str = "", hr_final: bool = False
) -> dict[str, Any]:
    require_profile(user)
    try:
        leave_request = LeaveRequest.objects.get(pk=leave_request_id)
    except LeaveRequest.DoesNotExist as exc:
        raise ValidationError({"leave_request_id": "Leave request not found."}) from exc
    if hr_final:
        if not IsHRAdminForAdjustment().has_permission(_request_for(user), None):
            raise PermissionDenied("You do not have permission for HR leave approval.")
        with transaction.atomic():
            success, error = approve_leave_request_hr(
                leave_request=leave_request,
                approver=user.profile,
                comments=comments,
            )
    else:
        gate = IsManagerForApproval()
        if not gate.has_permission(
            _request_for(user), None
        ) or not gate.has_object_permission(_request_for(user), None, leave_request):
            raise PermissionDenied(
                "You do not have permission to approve this request."
            )
        with transaction.atomic():
            success, error = approve_leave_request_lead(
                leave_request=leave_request,
                approver=user.profile,
                comments=comments,
            )
    if not success:
        raise ValidationError({"detail": error})
    leave_request.refresh_from_db()
    return {
        "leave_request": LeaveRequestDetailSerializer(leave_request).data,
        "summary": "Approved leave request.",
    }


def list_assets(
    *, user, query: str = "", limit: int | None = 10, count_only: bool = False
) -> dict[str, Any]:
    require_profile(user)
    qs = Asset.objects.all()
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(asset_id__icontains=query)
            | Q(serial_number__icontains=query)
        )
    visible = [
        asset for asset in qs.order_by("name", "id") if can_view_asset(user, asset)
    ]
    total_count = len(visible)
    if count_only:
        label = f" matching `{query}`" if query else ""
        return {
            "assets": [],
            "total_count": total_count,
            "returned_count": 0,
            "summary": f"There are {total_count} visible asset(s){label}.",
        }
    visible = visible[: _limit(limit)]
    return {
        "assets": AssetSerializer(visible, many=True).data,
        "total_count": total_count,
        "returned_count": len(visible),
        "summary": f"Loaded {len(visible)} of {total_count} asset(s).",
    }


def list_documents(
    *,
    user,
    query: str = "",
    expired: bool = False,
    limit: int | None = 10,
    count_only: bool = False,
) -> dict[str, Any]:
    base_qs = Document.objects.all()
    if query:
        base_qs = base_qs.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )
    if expired:
        base_qs = base_qs.filter(expiry_date__lt=timezone.now().date())
    docs = list(filter_accessible_documents(user, base_qs.order_by("-uploaded_at")))
    total_count = len(docs)
    if count_only:
        label = " expired" if expired else " accessible"
        suffix = f" matching `{query}`" if query else ""
        return {
            "documents": [],
            "total_count": total_count,
            "returned_count": 0,
            "summary": f"There are {total_count}{label} document(s){suffix}.",
        }
    docs = docs[: _limit(limit)]
    serialized = DocumentListSerializer(docs, many=True).data
    if serialized:
        label = "Expired documents" if expired else "Accessible documents"
        lines = [
            f"{item['name']} ({item['category']})"
            + (f" — expires {item['expiry_date']}" if item.get("expiry_date") else "")
            for item in serialized
        ]
        summary = f"{label}:\n" + "\n".join(lines)
    else:
        summary = (
            "No expired documents found."
            if expired
            else "No accessible documents found."
        )
    return {
        "documents": serialized,
        "total_count": total_count,
        "returned_count": len(serialized),
        "summary": summary,
    }


def list_document_templates(
    *,
    user,
    query: str = "",
    category: str | None = None,
    visibility: str | None = None,
    is_system_template: bool | None = None,
    limit: int | None = 10,
    count_only: bool = False,
) -> dict[str, Any]:
    profile = require_profile(user)
    qs = (
        DocumentTemplate.objects.filter(is_active=True)
        .select_related("created_by__user")
        .prefetch_related("fields")
    )
    if (
        not is_hr_admin(user)
        and not getattr(user, "is_staff", False)
        and not getattr(user, "is_superuser", False)
    ):
        qs = qs.filter(Q(visibility=TemplateVisibility.SHARED) | Q(created_by=profile))
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(description__icontains=query))
    if category:
        qs = qs.filter(category=category)
    if visibility:
        qs = qs.filter(visibility=visibility)
    if is_system_template is not None:
        qs = qs.filter(is_system_template=is_system_template)

    total_count = qs.count()
    if count_only:
        suffix = f" matching `{query}`" if query else ""
        return {
            "document_templates": [],
            "templates": [],
            "total_count": total_count,
            "returned_count": 0,
            "summary": f"There are {total_count} document template(s){suffix}.",
        }
    templates = list(qs.order_by("-updated_at")[: _limit(limit)])
    serialized = DocumentTemplateListSerializer(templates, many=True).data
    if serialized:
        lines = [
            f"{item['name']} ({item['category']}, {item['visibility']}, {item['status']})"
            for item in serialized
        ]
        summary = "Document templates:\n" + "\n".join(lines)
    else:
        summary = "No document templates found."
    return {
        "document_templates": serialized,
        "templates": serialized,
        "total_count": total_count,
        "returned_count": len(serialized),
        "summary": summary,
    }


def list_time_entries(
    *,
    user,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = 10,
) -> dict[str, Any]:
    profile = require_profile(user)
    qs = TimeEntry.objects.select_related("employee__user", "project", "task")
    if not _can_view_all_time(user):
        qs = qs.filter(Q(employee=profile) | Q(employee__managers=profile))
    if date_from:
        qs = qs.filter(work_date__gte=date_from)
    if date_to:
        qs = qs.filter(work_date__lte=date_to)
    qs = qs.order_by("-work_date", "-id")[: _limit(limit)]
    return {
        "time_entries": TimeEntrySerializer(qs, many=True).data,
        "summary": f"Loaded {len(qs)} time entrie(s).",
    }


def create_time_entry(
    *,
    user,
    project_id: int,
    task_id: int | None,
    work_date: str,
    hours: str,
    description: str = "",
) -> dict[str, Any]:
    profile = require_profile(user)
    payload = {
        "employee_id": profile.id,
        "project_id": project_id,
        "task_id": task_id,
        "work_date": work_date,
        "hours": hours,
        "notes": description,
    }
    serializer = TimeEntrySerializer(
        data=payload, context={"request": _request_for(user)}
    )
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        unsaved = TimeEntry(**serializer.validated_data)
        if not can_edit_time_entry(user, unsaved):
            raise PermissionDenied(
                "You do not have permission to create this time entry."
            )
        entry = serializer.save()
    return {
        "time_entry": TimeEntrySerializer(entry).data,
        "summary": "Created time entry.",
    }


def submit_time_week(
    *, user, week_start: str, employee_id: int | None = None
) -> dict[str, Any]:
    employee = profile_for_user(user)
    if employee_id:
        employee = UserProfile.objects.get(pk=employee_id)
    if employee is None:
        raise ValidationError({"employee_id": "Employee is required."})
    with transaction.atomic():
        entries = submit_entries_for_week(
            user=user, employee=employee, week_start=week_start
        )
    return {
        "time_entries": TimeEntrySerializer(entries, many=True).data,
        "summary": "Submitted time entries for the week.",
    }


def list_time_tasks(
    *, user, project_id: int | None = None, limit: int | None = 20
) -> dict[str, Any]:
    require_profile(user)
    qs = TimeTask.objects.select_related("project")
    if project_id:
        qs = qs.filter(project_id=project_id)
    qs = qs.order_by("project__name", "name")[: _limit(limit, default=20)]
    return {
        "time_tasks": TimeTaskSerializer(qs, many=True).data,
        "summary": "Loaded time tasks.",
    }


def create_onboarding_instance(
    *, user, employee_id: int, template_id: int, due_date: str | None = None
) -> dict[str, Any]:
    profile = require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR/admin users can create onboarding checklists.")
    try:
        employee = UserProfile.objects.get(pk=employee_id)
        template = ChecklistTemplate.objects.get(pk=template_id)
    except (UserProfile.DoesNotExist, ChecklistTemplate.DoesNotExist) as exc:
        raise ValidationError({"detail": "Employee or template not found."}) from exc
    with transaction.atomic():
        instance = ChecklistInstance.objects.create(
            employee=employee,
            template=template,
            due_date=due_date,
            created_by=profile,
        )
        instance.create_tasks_from_template()
    return {
        "checklist_instance": ChecklistInstanceSerializer(instance).data,
        "summary": "Created onboarding checklist instance.",
    }


def list_notifications(
    *, user, unread: bool = False, limit: int | None = 10
) -> dict[str, Any]:
    profile = require_profile(user)
    qs = Notification.objects.filter(recipient=profile)
    if unread:
        qs = qs.filter(is_read=False)
    qs = qs.order_by("-created_at")[: _limit(limit)]
    return {
        "notifications": NotificationSerializer(qs, many=True).data,
        "unread_count": Notification.objects.filter(
            recipient=profile, is_read=False
        ).count(),
        "summary": "Loaded notifications.",
    }


def mark_all_notifications_read(*, user) -> dict[str, Any]:
    profile = require_profile(user)
    with transaction.atomic():
        updated = Notification.objects.filter(recipient=profile, is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
    return {
        "updated": updated,
        "summary": f"Marked {updated} notification(s) as read.",
    }


registry.register(
    AssistantTool(
        "get_current_user_context",
        "Get current user, profile, and permissions.",
        get_current_user_context,
        module="general",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "get_current_datetime",
        "Get current local date and time for scheduling and relative-time calculations.",
        get_current_datetime,
        module="general",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "get_current_date",
        "Get current local date for date-sensitive scheduling.",
        get_current_date,
        module="general",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "get_current_time",
        "Get current local time for time-sensitive scheduling.",
        get_current_time,
        module="general",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "search_employees",
        "Search visible employee profiles.",
        search_employees,
        module="employees",
        args_schema=SearchEmployeesArgs,
    )
)
registry.register(
    AssistantTool(
        "get_employee_managers",
        "Find visible employee managers by employee name or email.",
        get_employee_managers,
        module="employees",
        args_schema=GetEmployeeManagersArgs,
        workflow_topic="find_manager",
    )
)
registry.register(
    AssistantTool(
        "get_employee_profile",
        "Get an employee profile by profile id.",
        get_employee_profile,
        module="employees",
        args_schema=GetEmployeeProfileArgs,
    )
)
registry.register(
    AssistantTool(
        "list_reference_data",
        "List departments, roles, and projects.",
        list_reference_data,
        module="employees",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "list_top_paid_employees",
        "List employees with highest current salary. HR-only.",
        list_top_paid_employees,
        module="mobility_compensation",
        sensitive=True,
        args_schema=ListTopPaidEmployeesArgs,
        permission_check=_check_compensation_admin,
        required_permissions=("Compensation admin (HR)",),
        workflow_topic="view_compensation",
    )
)
registry.register(
    AssistantTool(
        "list_leave_balances",
        "List leave balances.",
        list_leave_balances,
        module="vacations",
        args_schema=ListLeaveBalancesArgs,
    )
)
registry.register(
    AssistantTool(
        "list_leave_policies",
        "List leave policies.",
        list_leave_policies,
        module="vacations",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "list_leave_requests",
        "List visible leave requests.",
        list_leave_requests,
        module="vacations",
        args_schema=ListLeaveRequestsArgs,
    )
)
registry.register(
    AssistantTool(
        "create_leave_request",
        "Create a leave request.",
        create_leave_request,
        module="vacations",
        mutating=True,
        args_schema=CreateLeaveRequestArgs,
    )
)
registry.register(
    AssistantTool(
        "approve_leave_request",
        "Approve a leave request.",
        approve_leave_request,
        module="vacations",
        mutating=True,
        args_schema=ApproveLeaveRequestArgs,
        permission_check=_check_manager_or_hr,
        required_permissions=("Manager of the requester OR HR admin",),
        workflow_topic="approve_leave",
    )
)
registry.register(
    AssistantTool(
        "list_assets",
        "List visible assets.",
        list_assets,
        module="assets",
        args_schema=ListAssetsArgs,
    )
)
registry.register(
    AssistantTool(
        "list_documents",
        "List accessible document metadata.",
        list_documents,
        module="documents",
        args_schema=ListDocumentsArgs,
    )
)
registry.register(
    AssistantTool(
        "list_document_templates",
        "List document templates visible to the current user.",
        list_document_templates,
        module="documents",
        args_schema=ListDocumentTemplatesArgs,
    )
)
registry.register(
    AssistantTool(
        "list_time_entries",
        "List visible time entries.",
        list_time_entries,
        module="time_tracking",
        args_schema=ListTimeEntriesArgs,
    )
)
registry.register(
    AssistantTool(
        "create_time_entry",
        "Create a manual time entry.",
        create_time_entry,
        module="time_tracking",
        mutating=True,
        args_schema=CreateTimeEntryArgs,
    )
)
registry.register(
    AssistantTool(
        "submit_time_week",
        "Submit time entries for a week.",
        submit_time_week,
        module="time_tracking",
        mutating=True,
        sensitive=True,
        args_schema=SubmitTimeWeekArgs,
    )
)
registry.register(
    AssistantTool(
        "list_time_tasks",
        "List time tracking tasks.",
        list_time_tasks,
        module="time_tracking",
        args_schema=ListTimeTasksArgs,
    )
)
registry.register(
    AssistantTool(
        "create_onboarding_instance",
        "Create onboarding checklist instance.",
        create_onboarding_instance,
        module="onboarding",
        mutating=True,
        sensitive=True,
        args_schema=CreateOnboardingInstanceArgs,
        permission_check=_check_hr_admin,
        required_permissions=("HR admin",),
    )
)
registry.register(
    AssistantTool(
        "list_notifications",
        "List notifications.",
        list_notifications,
        module="notifications",
        args_schema=ListNotificationsArgs,
    )
)
registry.register(
    AssistantTool(
        "mark_all_notifications_read",
        "Mark all notifications as read.",
        mark_all_notifications_read,
        module="notifications",
        mutating=True,
        args_schema=EmptyArgs,
    )
)


# -- Admin: Role management ---------------------------------------------------


class CreateRoleArgs(_ArgsBase):
    name: str = Field(..., min_length=1, max_length=50, description="Role name")
    description: str = Field(default="", description="Optional role description")
    permission_ids: list[int] = Field(
        default_factory=list, description="Permission IDs granted to this role"
    )


class UpdateRoleArgs(_ArgsBase):
    role_id: int = Field(..., ge=1, description="Existing role id")
    name: str | None = Field(default=None, max_length=50)
    description: str | None = None
    permission_ids: list[int] | None = Field(
        default=None,
        description="Replace role permissions with this set. Omit to leave unchanged.",
    )


class DeleteRoleArgs(_ArgsBase):
    role_id: int = Field(..., ge=1)


def _require_role_admin(user) -> None:
    if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        raise PermissionDenied("Only staff/admin users can manage roles.")


def create_role(
    *,
    user,
    name: str,
    description: str = "",
    permission_ids: list[int] | None = None,
) -> dict[str, Any]:
    _require_role_admin(user)
    if Role.objects.filter(name__iexact=name.strip()).exists():
        raise ValidationError({"name": "Role with this name already exists."})
    with transaction.atomic():
        role = Role.objects.create(name=name.strip(), description=description or "")
        if permission_ids:
            permissions = PermissionModel.objects.filter(id__in=permission_ids)
            role.permissions.set(permissions)
    return {
        "role": {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "permission_ids": list(role.permissions.values_list("id", flat=True)),
        },
        "summary": f"Created role `{role.name}`.",
    }


def update_role(
    *,
    user,
    role_id: int,
    name: str | None = None,
    description: str | None = None,
    permission_ids: list[int] | None = None,
) -> dict[str, Any]:
    _require_role_admin(user)
    try:
        role = Role.objects.get(pk=role_id)
    except Role.DoesNotExist as exc:
        raise ValidationError({"role_id": "Role not found."}) from exc
    with transaction.atomic():
        updates: list[str] = []
        if name is not None and name.strip() and name.strip() != role.name:
            if (
                Role.objects.exclude(pk=role.id)
                .filter(name__iexact=name.strip())
                .exists()
            ):
                raise ValidationError({"name": "Another role already has this name."})
            role.name = name.strip()
            updates.append("name")
        if description is not None and description != role.description:
            role.description = description
            updates.append("description")
        if updates:
            role.save(update_fields=updates)
        if permission_ids is not None:
            permissions = PermissionModel.objects.filter(id__in=permission_ids)
            role.permissions.set(permissions)
    return {
        "role": {
            "id": role.id,
            "name": role.name,
            "description": role.description,
            "permission_ids": list(role.permissions.values_list("id", flat=True)),
        },
        "summary": f"Updated role `{role.name}`.",
    }


def delete_role(*, user, role_id: int) -> dict[str, Any]:
    _require_role_admin(user)
    try:
        role = Role.objects.get(pk=role_id)
    except Role.DoesNotExist as exc:
        raise ValidationError({"role_id": "Role not found."}) from exc
    name = role.name
    if UserProfile.objects.filter(role=role).exists():
        raise ValidationError(
            {"role_id": "Role is assigned to one or more employees; reassign first."}
        )
    with transaction.atomic():
        role.delete()
    return {"summary": f"Deleted role `{name}`."}


def list_permissions(*, user) -> dict[str, Any]:
    _require_role_admin(user)
    perms = list(
        PermissionModel.objects.order_by("module_name", "feature_action").values(
            "id", "module_name", "feature_action"
        )
    )
    return {
        "permissions": perms,
        "summary": f"Loaded {len(perms)} permission(s).",
    }


# -- Assets: create / update --------------------------------------------------


class CreateAssetArgs(_ArgsBase):
    asset_id: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(default=AssetCategory.OTHER.value)
    condition: str = Field(default="GOOD")
    purchase_date: str = Field(..., description="ISO date e.g. 2026-01-15")
    serial_number: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    purchase_price: str | None = Field(
        default=None,
        description="Decimal string e.g. '1299.99'",
    )
    description: str = ""


class UpdateAssetStatusArgs(_ArgsBase):
    asset_id: int = Field(..., ge=1, description="Asset DB id")
    status: str = Field(..., description="New status")
    condition: str | None = None
    description: str | None = None


def _require_asset_admin(user) -> None:
    if not has_asset_permission(user, "configure_asset_types"):
        raise PermissionDenied("You do not have permission to manage assets.")


def create_asset(
    *,
    user,
    asset_id: str,
    name: str,
    category: str = AssetCategory.OTHER.value,
    condition: str = "GOOD",
    purchase_date: str,
    serial_number: str | None = None,
    model: str | None = None,
    manufacturer: str | None = None,
    purchase_price: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    _require_asset_admin(user)
    if Asset.objects.filter(asset_id=asset_id).exists():
        raise ValidationError({"asset_id": "Asset with this asset_id already exists."})
    payload = {
        "asset_id": asset_id,
        "name": name,
        "category": category,
        "condition": condition,
        "purchase_date": purchase_date,
        "serial_number": serial_number,
        "model": model,
        "manufacturer": manufacturer,
        "purchase_price": purchase_price,
        "description": description,
    }
    serializer = AssetSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        asset = serializer.save()
    return {
        "asset": AssetSerializer(asset).data,
        "summary": f"Created asset `{asset.asset_id}`.",
    }


def update_asset_status(
    *,
    user,
    asset_id: int,
    status: str,
    condition: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    _require_asset_admin(user)
    try:
        asset = Asset.objects.get(pk=asset_id)
    except Asset.DoesNotExist as exc:
        raise ValidationError({"asset_id": "Asset not found."}) from exc
    fields: list[str] = []
    with transaction.atomic():
        if status and status != asset.status:
            asset.status = status
            fields.append("status")
        if condition and condition != asset.condition:
            asset.condition = condition
            fields.append("condition")
        if description is not None and description != (asset.description or ""):
            asset.description = description
            fields.append("description")
        if fields:
            asset.save(update_fields=fields)
    return {
        "asset": AssetSerializer(asset).data,
        "summary": f"Updated asset `{asset.asset_id}` ({', '.join(fields) or 'no changes'}).",
    }


registry.register(
    AssistantTool(
        "list_permissions",
        "List all available permissions for role management.",
        list_permissions,
        module="admin",
        args_schema=EmptyArgs,
        permission_check=_check_staff,
        required_permissions=("Staff or superuser",),
        workflow_topic="create_role",
    )
)
registry.register(
    AssistantTool(
        "create_role",
        "Create a new role with optional permissions. Staff/admin only.",
        create_role,
        module="admin",
        mutating=True,
        sensitive=True,
        args_schema=CreateRoleArgs,
        confirmation_label="Create role",
        confirmation_help=(
            "Review the role name, description, and permission set. Use "
            "list_permissions to discover valid permission_ids."
        ),
        examples=(
            {
                "name": "Engineering Lead",
                "description": "Owns engineering team.",
                "permission_ids": [],
            },
        ),
        permission_check=_check_staff,
        required_permissions=("Staff or superuser",),
        workflow_topic="create_role",
    )
)
registry.register(
    AssistantTool(
        "update_role",
        "Update an existing role (name, description, permissions). Staff/admin only.",
        update_role,
        module="admin",
        mutating=True,
        sensitive=True,
        args_schema=UpdateRoleArgs,
        confirmation_label="Update role",
        confirmation_help="Omit any field to leave it unchanged. permission_ids REPLACES the current set.",
        permission_check=_check_staff,
        required_permissions=("Staff or superuser",),
        workflow_topic="create_role",
    )
)
registry.register(
    AssistantTool(
        "delete_role",
        "Delete a role. Fails if any employee currently has the role.",
        delete_role,
        module="admin",
        mutating=True,
        sensitive=True,
        args_schema=DeleteRoleArgs,
        confirmation_label="Delete role",
        confirmation_help="Irreversible. Reassign employees off the role first.",
        permission_check=_check_staff,
        required_permissions=("Staff or superuser",),
        workflow_topic="create_role",
    )
)
registry.register(
    AssistantTool(
        "create_asset",
        "Register a new asset in inventory. Requires Asset configure permission.",
        create_asset,
        module="assets",
        mutating=True,
        sensitive=True,
        args_schema=CreateAssetArgs,
        confirmation_label="Create asset",
        confirmation_help=(
            "asset_id must be unique. category enum: see model AssetCategory. "
            "purchase_date is ISO format YYYY-MM-DD."
        ),
        permission_check=_check_asset_configure,
        required_permissions=("Asset Management: configure_asset_types",),
        workflow_topic="create_asset",
    )
)
registry.register(
    AssistantTool(
        "update_asset_status",
        "Update an asset's status, condition, or description.",
        update_asset_status,
        module="assets",
        mutating=True,
        sensitive=True,
        args_schema=UpdateAssetStatusArgs,
        confirmation_label="Update asset status",
        confirmation_help="status enum: see AssetStatus. condition enum: see AssetCondition.",
        permission_check=_check_asset_configure,
        required_permissions=("Asset Management: configure_asset_types",),
        workflow_topic="create_asset",
    )
)


# -- Permission-aware introspection / explanation tools -----------------------


class CheckPermissionArgs(_ArgsBase):
    tool_name: str = Field(..., min_length=1, description="Name of the tool to check")


class ListAvailableActionsArgs(_ArgsBase):
    module: str | None = Field(default=None, description="Optional module filter")


class ExplainWorkflowArgs(_ArgsBase):
    topic: str = Field(
        ...,
        min_length=1,
        description=(
            "Workflow topic key, e.g. 'create_employee', 'request_leave', "
            "'submit_timesheet', 'create_role', 'create_asset', "
            "'view_compensation', 'find_manager', 'list_documents', "
            "'approve_leave'."
        ),
    )


def _tool_summary(tool: AssistantTool, user) -> dict[str, Any]:
    can_run, reason = probe_permission(tool, user)
    return {
        "name": tool.name,
        "description": tool.description,
        "module": tool.module,
        "mutating": tool.mutating,
        "sensitive": tool.sensitive,
        "requires_confirmation": tool.requires_confirmation,
        "ui_path": tool.ui_path,
        "required_permissions": list(tool.required_permissions or ()),
        "workflow_topic": tool.workflow_topic,
        "can_run": can_run,
        "deny_reason": reason,
    }


def check_permission(*, user, tool_name: str) -> dict[str, Any]:
    require_profile(user)
    try:
        tool = registry.get(tool_name)
    except Exception as exc:
        raise ValidationError({"tool_name": f"Unknown tool: {tool_name}"}) from exc
    summary = _tool_summary(tool, user)
    verdict = "can" if summary["can_run"] else "cannot"
    reason_tail = f" Reason: {summary['deny_reason']}" if summary["deny_reason"] else ""
    return {
        **summary,
        "summary": f"You {verdict} run `{tool_name}`.{reason_tail}",
    }


def list_available_actions(*, user, module: str | None = None) -> dict[str, Any]:
    require_profile(user)
    actions = []
    for tool in registry.values():
        if module and tool.module != module:
            continue
        actions.append(_tool_summary(tool, user))
    actions.sort(key=lambda item: (item["module"], item["name"]))
    runnable = [a for a in actions if a["can_run"]]
    blocked = [a for a in actions if not a["can_run"]]
    return {
        "actions": actions,
        "runnable_count": len(runnable),
        "blocked_count": len(blocked),
        "summary": (
            f"You can run {len(runnable)} action(s); {len(blocked)} require "
            "additional permission."
        ),
    }


def explain_workflow(*, user, topic: str) -> dict[str, Any]:
    require_profile(user)
    workflow = WORKFLOWS.get(topic)
    if workflow is None:
        # Best-effort fuzzy match so the LLM can pass slightly off topic strings.
        topic_norm = topic.lower().replace(" ", "_").replace("-", "_")
        workflow = WORKFLOWS.get(topic_norm)
    if workflow is None:
        available = ", ".join(sorted(WORKFLOWS.keys()))
        raise ValidationError(
            {"topic": (f"Unknown workflow `{topic}`. Available topics: {available}.")}
        )
    payload = describe_workflow(workflow, user)
    # Decorate AI tool entries with current can_run so the LLM can pick.
    payload["ai_tool_details"] = [
        _tool_summary(registry.get(name), user)
        for name in workflow.ai_tools
        if name in {t.name for t in registry.values()}
    ]
    lines = [
        f"**{payload['title']}** ({'available to you' if payload['can_run'] else 'blocked for your role'})",
        payload["description"],
    ]
    if payload["ui_path"]:
        lines.append(f"UI: `{payload['ui_path']}`")
    if payload["required_permissions"]:
        lines.append("Required: " + ", ".join(payload["required_permissions"]))
    if not payload["can_run"] and payload["deny_reason"]:
        lines.append(f"Why blocked: {payload['deny_reason']}")
    if payload["steps"]:
        lines.append("Steps:\n" + "\n".join(f"- {s}" for s in payload["steps"]))
    if payload["ai_tools"]:
        lines.append("AI tools that automate this: " + ", ".join(payload["ai_tools"]))
    payload["summary"] = "\n\n".join(lines)
    return payload


def list_workflows(*, user) -> dict[str, Any]:
    require_profile(user)
    items = workflow_index(user)
    runnable = [w for w in items if w["can_run"]]
    return {
        "workflows": items,
        "runnable_count": len(runnable),
        "summary": (f"{len(runnable)} of {len(items)} workflows are available to you."),
    }


registry.register(
    AssistantTool(
        "check_permission",
        "Check whether the current user can run a specific tool, and why not if blocked.",
        check_permission,
        module="general",
        args_schema=CheckPermissionArgs,
    )
)
registry.register(
    AssistantTool(
        "list_available_actions",
        "List every assistant action, annotated with can_run + reason for the current user. Optionally filter by module.",
        list_available_actions,
        module="general",
        args_schema=ListAvailableActionsArgs,
    )
)
registry.register(
    AssistantTool(
        "explain_workflow",
        "Explain how to do a specific workflow (e.g. create_employee, request_leave) with steps, required permissions, and AI tools that automate it.",
        explain_workflow,
        module="general",
        args_schema=ExplainWorkflowArgs,
    )
)
registry.register(
    AssistantTool(
        "list_workflows",
        "List every documented workflow with availability for the current user.",
        list_workflows,
        module="general",
        args_schema=EmptyArgs,
    )
)


# -- Confirmation control tools (deterministic confirm/cancel) ----------------


class ConfirmPendingActionArgs(_ArgsBase):
    overrides: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional field overrides merged on top of the pending arguments. "
            "Use when the user edited a value while confirming."
        ),
    )


class CancelPendingActionArgs(_ArgsBase):
    reason: str = Field(default="", description="Optional reason for cancellation.")


class ClassifyConfirmationResponseArgs(_ArgsBase):
    response: str = Field(..., min_length=1)


_CONFIRM_POSITIVE_BARE = frozenset(
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
_CONFIRM_POSITIVE_PREFIXES = (
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
_CONFIRM_NEGATIVE_BARE = frozenset(
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
        "ne",
        "otkazi",
        "otkaži",
        "stani",
        "nemoj",
        "prekini",
    }
)
_CONFIRM_NEGATIVE_PREFIXES = (
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
_CONFIRM_NEGATION_VETO = (
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


def _normalize_confirmation_response(response: str) -> str:
    return (response or "").strip().lower().rstrip("?!. ").strip()


def classify_confirmation_response(*, user, response: str) -> dict[str, Any]:
    """Classify a user reply to a pending confirmation."""
    require_profile(user)
    normalized = _normalize_confirmation_response(response)
    sentiment = "unknown"
    if normalized in _CONFIRM_POSITIVE_BARE:
        sentiment = "positive"
    else:
        padded = f" {normalized} "
        has_veto = any(veto in padded for veto in _CONFIRM_NEGATION_VETO)
        if normalized and not has_veto:
            for prefix in _CONFIRM_POSITIVE_PREFIXES:
                if (
                    normalized == prefix
                    or normalized.startswith(prefix + " ")
                    or normalized.startswith(prefix + ",")
                ):
                    sentiment = "positive"
                    break
    if sentiment == "unknown":
        if normalized in _CONFIRM_NEGATIVE_BARE:
            sentiment = "negative"
        else:
            for prefix in _CONFIRM_NEGATIVE_PREFIXES:
                if normalized.startswith(prefix):
                    sentiment = "negative"
                    break
    return {
        "sentiment": sentiment,
        "is_positive": sentiment == "positive",
        "is_negative": sentiment == "negative",
        "summary": f"Confirmation response classified as `{sentiment}`.",
    }


def _import_execute_tool():
    from core.ai.tooling import execute_tool

    return execute_tool


def confirm_pending_action(
    *, user, session, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Execute the action that is currently sitting in `session.pending_confirmation`.

    Use this when the user has expressed a clear affirmative ("yes", "confirm",
    "go ahead", etc.) in response to a previously-staged mutating tool. The
    pending payload is loaded, optional `overrides` are merged on top, and the
    underlying tool is re-invoked with `confirmed=True`.
    """
    from core.ai.tooling import pending_is_expired

    require_profile(user)
    session.refresh_from_db(fields=["pending_confirmation"])
    pending = session.pending_confirmation or {}
    if not pending:
        return {
            "executed": False,
            "summary": (
                "There is no pending action waiting on confirmation. Ask the "
                "user for what they want to do and call the relevant tool."
            ),
        }
    if pending_is_expired(pending):
        session.pending_confirmation = {}
        session.save(update_fields=["pending_confirmation", "updated_at"])
        return {
            "executed": False,
            "summary": (
                "The pending action expired. Ask the user to restate their "
                "request and call the tool again."
            ),
        }
    tool_name = pending.get("tool_name")
    stored_args = pending.get("arguments") or {}
    merged = {**stored_args, **(overrides or {})}
    execute_tool = _import_execute_tool()
    result = execute_tool(
        registry=registry,
        session=session,
        user=user,
        tool_name=tool_name,
        arguments=merged,
        confirmed=True,
    )
    summary = result.get("summary") or f"Executed `{tool_name}`."
    return {
        "executed": True,
        "tool_name": tool_name,
        "result": result,
        "summary": summary,
    }


def cancel_pending_action(*, user, session, reason: str = "") -> dict[str, Any]:
    """Cancel the pending action stored on this session."""
    require_profile(user)
    session.refresh_from_db(fields=["pending_confirmation"])
    pending = session.pending_confirmation or {}
    if not pending:
        return {"cancelled": False, "summary": "There was no pending action to cancel."}
    tool_name = pending.get("tool_name")
    session.pending_confirmation = {}
    session.save(update_fields=["pending_confirmation", "updated_at"])
    suffix = f" Reason: {reason}" if reason else ""
    return {
        "cancelled": True,
        "tool_name": tool_name,
        "summary": f"Cancelled the pending `{tool_name}` action.{suffix}",
    }


# Wrappers so the existing tool runner injects `session` automatically. The
# generic `execute_tool` only passes `user`; these wrappers reach back into
# the session via a stored attribute set by run_assistant_turn.
def _confirm_pending_action_handler(
    *, user, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    session = getattr(user, "_ai_active_session", None)
    if session is None:
        return {
            "executed": False,
            "summary": "Internal: chat session not bound to user; cannot confirm.",
        }
    return confirm_pending_action(user=user, session=session, overrides=overrides)


def _cancel_pending_action_handler(*, user, reason: str = "") -> dict[str, Any]:
    session = getattr(user, "_ai_active_session", None)
    if session is None:
        return {
            "cancelled": False,
            "summary": "Internal: chat session not bound to user; cannot cancel.",
        }
    return cancel_pending_action(user=user, session=session, reason=reason)


registry.register(
    AssistantTool(
        "confirm_pending_action",
        "Execute the pending mutating action awaiting the user's confirmation. Call this when the user replies with a clear affirmative (yes / confirm / go ahead) to a previously-staged action.",
        _confirm_pending_action_handler,
        module="general",
        args_schema=ConfirmPendingActionArgs,
    )
)
registry.register(
    AssistantTool(
        "classify_confirmation_response",
        "Classify a user reply to a pending confirmation as positive, negative, or unknown. If positive, call confirm_pending_action next; if negative, call cancel_pending_action.",
        classify_confirmation_response,
        module="general",
        args_schema=ClassifyConfirmationResponseArgs,
    )
)
registry.register(
    AssistantTool(
        "cancel_pending_action",
        "Cancel the pending mutating action. Call this when the user replies no / cancel / abort to a staged action.",
        _cancel_pending_action_handler,
        module="general",
        args_schema=CancelPendingActionArgs,
    )
)


# -- Documents: read content --------------------------------------------------


class ReadDocumentContentArgs(_ArgsBase):
    document_id: int = Field(..., ge=1, description="Document DB id")
    max_chars: int = Field(default=8000, ge=200, le=20000)
    max_pages: int = Field(default=20, ge=1, le=100)


def read_document_content(
    *,
    user,
    document_id: int,
    max_chars: int = 8000,
    max_pages: int = 20,
) -> dict[str, Any]:
    """Fetch + extract text from a single document the caller can access.

    Permission is enforced by reusing `filter_accessible_documents` — the
    document_id must be visible to the user under the same rules that govern
    `list_documents`. Returns extracted text (truncated to max_chars) so the
    LLM can summarize or answer questions about the content.
    """
    from core.ai.document_reader import extract_text

    require_profile(user)
    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist as exc:
        raise ValidationError({"document_id": "Document not found."}) from exc

    # Re-run access filter against a single-row queryset; if the document is
    # not accessible to this user the filtered queryset is empty.
    accessible = filter_accessible_documents(
        user, Document.objects.filter(pk=document.pk)
    )
    if not accessible.exists():
        raise PermissionDenied("You do not have access to this document.")

    try:
        extraction = extract_text(
            file_key=document.file_key,
            mime_type=document.mime_type,
            file_name=document.original_filename or document.name,
            max_pages=max_pages,
            max_chars=max_chars,
        )
    except RuntimeError as exc:
        raise ValidationError({"document_id": str(exc)}) from exc

    snippet = extraction["text"]
    truncated_note = " (truncated)" if extraction["truncated"] else ""
    summary = (
        f"Read `{document.name}` ({extraction['kind']}, "
        f"{extraction['char_count']} chars{truncated_note})."
    )
    return {
        "document": {
            "id": document.id,
            "name": document.name,
            "category": document.category,
            "mime_type": document.mime_type,
            "expiry_date": (
                document.expiry_date.isoformat() if document.expiry_date else None
            ),
        },
        "kind": extraction["kind"],
        "text": snippet,
        "truncated": extraction["truncated"],
        "char_count": extraction["char_count"],
        "summary": summary,
    }


# -- Compensation & Mobility module ------------------------------------------


class ListBonusesArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=2000, le=2100)
    bonus_type: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)


class GetBonusTotalsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=2000, le=2100)


class CreateBonusRecordArgs(_ArgsBase):
    user_profile: int = Field(..., ge=1, description="Employee profile id")
    bonus_type: str = Field(..., min_length=1)
    amount: str = Field(..., min_length=1, description="Decimal string e.g. '1500.00'")
    currency: str = "BAM"
    effective_date: str = Field(..., min_length=8)
    reason: str = ""


class ListPromotionHistoryArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=10, ge=1, le=50)


class ListCPFLevelChangesArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=10, ge=1, le=50)


class ListJobListingsArgs(_ArgsBase):
    status: str | None = None
    query: str = ""
    limit: int | None = Field(default=20, ge=1, le=50)


class ListApplicationsArgs(_ArgsBase):
    listing_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    limit: int | None = Field(default=20, ge=1, le=50)


class ApplyToJobListingArgs(_ArgsBase):
    listing_id: int = Field(..., ge=1)
    cover_note: str = ""


class UpdateApplicationStatusArgs(_ArgsBase):
    application_id: int = Field(..., ge=1)
    status: str = Field(..., min_length=1)
    decision_note: str = ""


class GetCompensationOverviewArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)


class GetPayrollSnapshotArgs(_ArgsBase):
    snapshot_date: str | None = Field(
        default=None, description="ISO date YYYY-MM-DD. Latest if omitted."
    )


class RecordPromotionArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    new_role_id: int | None = Field(default=None, ge=1)
    date: str = Field(..., min_length=8, description="ISO date YYYY-MM-DD")
    new_cpf_level: str | None = None
    notes: str = ""
    related_listing_id: int | None = Field(default=None, ge=1)
    record_cpf_change: bool = Field(
        default=True,
        description="Also write a CPFLevelChange row (source=promotion) if new_cpf_level differs.",
    )


class RecordCPFLevelChangeArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    new_level: str = Field(..., min_length=1)
    effective_date: str = Field(..., min_length=8)
    source: str = Field(default="manual")
    cpf_score: int | None = Field(default=None, ge=0, le=100)
    notes: str = ""


class SetCompensationPolicyArgs(_ArgsBase):
    cpf_level: str = Field(..., min_length=1)
    net_monthly: str = Field(..., min_length=1, description="Decimal string")
    currency: str = "BAM"
    effective_date: str = Field(..., min_length=8)
    notes: str = ""


class CreateJobListingArgs(_ArgsBase):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    department_id: int | None = Field(default=None, ge=1)
    open_at: str = Field(..., min_length=10, description="ISO datetime")
    close_at: str = Field(..., min_length=10, description="ISO datetime")
    status: str = Field(default="draft")


class WithdrawApplicationArgs(_ArgsBase):
    application_id: int = Field(..., ge=1)
    reason: str = ""


def _profile_for(user, employee_id: int | None) -> UserProfile:
    actor = require_profile(user)
    if employee_id is None or employee_id == actor.id:
        return actor
    if not is_compensation_admin(user):
        raise PermissionDenied(
            "Only compensation admins can view other employees' compensation data."
        )
    try:
        return UserProfile.objects.get(pk=employee_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee not found."}) from exc


def list_bonuses(
    *,
    user,
    employee_id: int | None = None,
    year: int | None = None,
    bonus_type: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = BonusRecord.objects.filter(user_profile=target).select_related(
        "user_profile__user", "created_by"
    )
    if year:
        qs = qs.filter(effective_date__year=year)
    if bonus_type:
        qs = qs.filter(bonus_type=bonus_type)
    qs = qs.order_by("-effective_date", "-id")[: _limit(limit, default=20, maximum=100)]
    rows = []
    total = Decimal("0")
    for record in qs:
        rows.append(
            {
                "id": record.id,
                "employee_id": record.user_profile_id,
                "employee_name": record.user_profile.full_name
                or record.user_profile.user.get_full_name()
                or record.user_profile.user.username,
                "bonus_type": record.bonus_type,
                "amount": str(record.amount),
                "currency": record.currency,
                "effective_date": record.effective_date.isoformat(),
                "reason": record.reason,
            }
        )
        total += Decimal(record.amount)
    label = f"for `{target.full_name or target.user.username}`"
    return {
        "bonuses": rows,
        "total_amount": str(total),
        "currency": rows[0]["currency"] if rows else "BAM",
        "summary": f"Loaded {len(rows)} bonus record(s) {label}; total={total}.",
    }


def get_bonus_totals(
    *, user, employee_id: int | None = None, year: int | None = None
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = BonusRecord.objects.filter(user_profile=target)
    if year:
        qs = qs.filter(effective_date__year=year)
    from collections import defaultdict

    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    count = 0
    for amount, btype in qs.values_list("amount", "bonus_type"):
        totals[btype] += Decimal(amount)
        count += 1
    grand_total = sum(totals.values(), Decimal("0"))
    return {
        "employee_id": target.id,
        "year": year,
        "by_type": {k: str(v) for k, v in totals.items()},
        "grand_total": str(grand_total),
        "count": count,
        "summary": (
            f"{count} bonus(es), grand total {grand_total} "
            f"({'all years' if not year else year}) for "
            f"`{target.full_name or target.user.username}`."
        ),
    }


def create_bonus_record(
    *,
    user,
    user_profile: int,
    bonus_type: str,
    amount: str,
    currency: str = "BAM",
    effective_date: str,
    reason: str = "",
) -> dict[str, Any]:
    require_profile(user)
    if not is_compensation_admin(user):
        raise PermissionDenied("Only compensation admins can record bonuses.")
    try:
        employee = UserProfile.objects.get(pk=user_profile)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"user_profile": "Employee not found."}) from exc
    with transaction.atomic():
        record = BonusRecord.objects.create(
            user_profile=employee,
            bonus_type=bonus_type,
            amount=Decimal(amount),
            currency=currency,
            effective_date=effective_date,
            reason=reason,
            created_by=user,
        )
    return {
        "bonus": {
            "id": record.id,
            "employee_id": employee.id,
            "bonus_type": record.bonus_type,
            "amount": str(record.amount),
            "currency": record.currency,
            "effective_date": record.effective_date.isoformat(),
            "reason": record.reason,
        },
        "summary": (
            f"Recorded {record.bonus_type} bonus of {record.amount} {record.currency} "
            f"for `{employee.full_name or employee.user.username}` "
            f"effective {record.effective_date}."
        ),
    }


def list_compensation_policies(*, user) -> dict[str, Any]:
    require_profile(user)
    qs = CompensationPolicy.objects.order_by("cpf_level")
    rows = [
        {
            "id": p.id,
            "cpf_level": p.cpf_level,
            "net_monthly": str(p.net_monthly),
            "currency": p.currency,
            "effective_date": p.effective_date.isoformat(),
            "notes": p.notes,
        }
        for p in qs
    ]
    return {
        "policies": rows,
        "summary": f"Loaded {len(rows)} compensation polic(y/ies).",
    }


def list_benefits(*, user) -> dict[str, Any]:
    require_profile(user)
    qs = BenefitCatalog.objects.filter(is_active=True).order_by("benefit_type", "name")
    rows = [
        {
            "id": b.id,
            "benefit_type": b.benefit_type,
            "name": b.name,
            "monthly_amount": str(b.monthly_amount),
            "currency": b.currency,
            "effective_date": b.effective_date.isoformat(),
            "end_date": b.end_date.isoformat() if b.end_date else None,
        }
        for b in qs
    ]
    total = sum((Decimal(r["monthly_amount"]) for r in rows), Decimal("0"))
    return {
        "benefits": rows,
        "monthly_total": str(total),
        "summary": (
            f"{len(rows)} active benefit(s); total monthly value {total} "
            f"{rows[0]['currency'] if rows else 'BAM'}."
        ),
    }


def get_payroll_snapshot(*, user, snapshot_date: str | None = None) -> dict[str, Any]:
    require_profile(user)
    if not is_compensation_admin(user):
        raise PermissionDenied("Payroll snapshots are HR-only.")
    qs = PayrollSnapshot.objects.all()
    if snapshot_date:
        snapshot = qs.filter(snapshot_date=snapshot_date).first()
    else:
        snapshot = qs.order_by("-snapshot_date").first()
    if not snapshot:
        return {"snapshot": None, "summary": "No payroll snapshot available."}
    return {
        "snapshot": {
            "snapshot_date": snapshot.snapshot_date.isoformat(),
            "total_monthly": str(snapshot.total_monthly),
            "avg_salary": str(snapshot.avg_salary),
            "median_salary": str(snapshot.median_salary),
            "headcount": snapshot.headcount,
            "currency": snapshot.currency,
        },
        "summary": (
            f"Payroll {snapshot.snapshot_date}: total {snapshot.total_monthly} "
            f"{snapshot.currency}, avg {snapshot.avg_salary}, median "
            f"{snapshot.median_salary}, headcount {snapshot.headcount}."
        ),
    }


def list_promotion_history(
    *, user, employee_id: int | None = None, limit: int | None = 10
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = (
        PromotionHistory.objects.filter(employee=target)
        .select_related("previous_role", "new_role")
        .order_by("-effective_date", "-id")[: _limit(limit, default=10, maximum=50)]
    )
    rows = [
        {
            "id": p.id,
            "employee_id": p.employee_id,
            "previous_role": p.previous_role.name if p.previous_role else None,
            "new_role": p.new_role.name if p.new_role else None,
            "effective_date": p.effective_date.isoformat(),
            "notes": getattr(p, "notes", ""),
        }
        for p in qs
    ]
    return {
        "promotions": rows,
        "summary": f"Loaded {len(rows)} promotion record(s).",
    }


def list_cpf_level_changes(
    *, user, employee_id: int | None = None, limit: int | None = 10
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = CPFLevelChange.objects.filter(employee=target).order_by(
        "-effective_date", "-id"
    )[: _limit(limit, default=10, maximum=50)]
    rows = [
        {
            "id": c.id,
            "employee_id": c.employee_id,
            "previous_level": c.previous_level,
            "new_level": c.new_level,
            "effective_date": c.effective_date.isoformat(),
            "source": c.source,
            "notes": getattr(c, "notes", ""),
        }
        for c in qs
    ]
    return {
        "changes": rows,
        "summary": f"Loaded {len(rows)} CPF level change(s).",
    }


def list_job_listings(
    *, user, status: str | None = None, query: str = "", limit: int | None = 20
) -> dict[str, Any]:
    require_profile(user)
    qs = JobListing.objects.select_related("department").order_by("-open_at", "-id")
    if status:
        qs = qs.filter(status=status)
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(description__icontains=query))
    qs = qs[: _limit(limit, default=20, maximum=50)]
    rows = [
        {
            "id": j.id,
            "title": j.title,
            "department": j.department.name if j.department else None,
            "status": j.status,
            "open_at": j.open_at.isoformat() if j.open_at else None,
            "close_at": j.close_at.isoformat() if j.close_at else None,
        }
        for j in qs
    ]
    return {
        "job_listings": rows,
        "summary": f"Loaded {len(rows)} job listing(s).",
    }


def list_applications(
    *,
    user,
    listing_id: int | None = None,
    status: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    actor = require_profile(user)
    qs = Application.objects.select_related("listing", "applicant__user").order_by(
        "-applied_at"
    )
    if not is_hr_admin(user):
        qs = qs.filter(applicant=actor)
    if listing_id:
        qs = qs.filter(listing_id=listing_id)
    if status:
        qs = qs.filter(status=status)
    qs = qs[: _limit(limit, default=20, maximum=50)]
    rows = [
        {
            "id": a.id,
            "listing_id": a.listing_id,
            "listing_title": a.listing.title if a.listing else None,
            "applicant_id": a.applicant_id,
            "applicant_name": a.applicant.full_name
            or a.applicant.user.get_full_name()
            or a.applicant.user.username,
            "status": a.status,
            "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            "cover_note": a.cover_note,
            "decision_note": a.decision_note,
        }
        for a in qs
    ]
    return {
        "applications": rows,
        "summary": f"Loaded {len(rows)} application(s).",
    }


def apply_to_job_listing(
    *, user, listing_id: int, cover_note: str = ""
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        listing = JobListing.objects.get(pk=listing_id)
    except JobListing.DoesNotExist as exc:
        raise ValidationError({"listing_id": "Job listing not found."}) from exc
    if Application.objects.filter(listing=listing, applicant=actor).exists():
        raise ValidationError(
            {"listing_id": "You have already applied to this listing."}
        )
    with transaction.atomic():
        application = Application.objects.create(
            listing=listing,
            applicant=actor,
            cover_note=cover_note,
        )
    return {
        "application": {
            "id": application.id,
            "listing_id": listing.id,
            "listing_title": listing.title,
            "status": application.status,
            "applied_at": application.applied_at.isoformat(),
        },
        "summary": f"Applied to `{listing.title}`.",
    }


def update_application_status(
    *, user, application_id: int, status: str, decision_note: str = ""
) -> dict[str, Any]:
    actor = require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR admins can update application status.")
    try:
        application = Application.objects.get(pk=application_id)
    except Application.DoesNotExist as exc:
        raise ValidationError({"application_id": "Application not found."}) from exc
    fields = ["status", "decision_note", "decided_by", "decided_at"]
    with transaction.atomic():
        application.status = status
        application.decision_note = decision_note
        application.decided_by = actor
        application.decided_at = timezone.now()
        application.save(update_fields=fields)
    return {
        "application": {
            "id": application.id,
            "listing_id": application.listing_id,
            "status": application.status,
            "decision_note": application.decision_note,
            "decided_at": application.decided_at.isoformat(),
        },
        "summary": f"Updated application {application.id} to `{status}`.",
    }


def get_compensation_overview(
    *, user, employee_id: int | None = None
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    latest_salary = (
        SalaryRecord.objects.filter(user_profile=target)
        .order_by("-effective_date", "-id")
        .first()
    )
    net_policy = (
        CompensationPolicy.objects.filter(cpf_level=target.cpf_level).first()
        if target.cpf_level
        else None
    )
    year = timezone.now().year
    ytd_bonus_total = sum(
        (
            Decimal(amt)
            for amt in BonusRecord.objects.filter(
                user_profile=target, effective_date__year=year
            ).values_list("amount", flat=True)
        ),
        Decimal("0"),
    )
    benefits = list(
        BenefitCatalog.objects.filter(is_active=True).values(
            "name", "monthly_amount", "currency"
        )
    )
    monthly_benefits = sum(
        (Decimal(b["monthly_amount"]) for b in benefits), Decimal("0")
    )

    summary_lines = [
        f"Compensation overview for `{target.full_name or target.user.username}`:",
        f"- Latest gross salary: {latest_salary.amount if latest_salary else 'n/a'}",
        f"- CPF level: {target.cpf_level or 'n/a'}"
        + (
            f" (NET policy {net_policy.net_monthly} {net_policy.currency})"
            if net_policy
            else ""
        ),
        f"- Year-to-date bonuses ({year}): {ytd_bonus_total}",
        f"- Monthly benefits total: {monthly_benefits}",
    ]
    return {
        "employee_id": target.id,
        "latest_salary": str(latest_salary.amount) if latest_salary else None,
        "salary_effective_date": (
            latest_salary.effective_date.isoformat() if latest_salary else None
        ),
        "cpf_level": target.cpf_level,
        "net_policy": (
            {
                "net_monthly": str(net_policy.net_monthly),
                "currency": net_policy.currency,
            }
            if net_policy
            else None
        ),
        "ytd_bonus_total": str(ytd_bonus_total),
        "year": year,
        "monthly_benefits_total": str(monthly_benefits),
        "benefits": [
            {
                "name": b["name"],
                "monthly_amount": str(b["monthly_amount"]),
                "currency": b["currency"],
            }
            for b in benefits
        ],
        "summary": "\n".join(summary_lines),
    }


def record_promotion(
    *,
    user,
    employee_id: int,
    new_role_id: int | None = None,
    date: str,
    new_cpf_level: str | None = None,
    notes: str = "",
    related_listing_id: int | None = None,
    record_cpf_change: bool = True,
) -> dict[str, Any]:
    require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR admins can record promotions.")
    try:
        employee = UserProfile.objects.select_related("role").get(pk=employee_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee not found."}) from exc

    new_role = None
    if new_role_id is not None:
        try:
            new_role = Role.objects.get(pk=new_role_id)
        except Role.DoesNotExist as exc:
            raise ValidationError({"new_role_id": "Role not found."}) from exc

    related_listing = None
    if related_listing_id is not None:
        try:
            related_listing = JobListing.objects.get(pk=related_listing_id)
        except JobListing.DoesNotExist as exc:
            raise ValidationError(
                {"related_listing_id": "Job listing not found."}
            ) from exc

    previous_role = employee.role
    previous_cpf = employee.cpf_level or ""
    next_cpf = new_cpf_level if new_cpf_level is not None else previous_cpf

    with transaction.atomic():
        promotion = PromotionHistory.objects.create(
            employee=employee,
            previous_role=previous_role,
            new_role=new_role,
            date=date,
            notes=notes,
            previous_cpf_level=previous_cpf,
            new_cpf_level=next_cpf,
            related_listing=related_listing,
        )
        # Apply the change to the profile
        profile_fields: list[str] = []
        if new_role is not None and new_role != previous_role:
            employee.role = new_role
            profile_fields.append("role")
        if new_cpf_level is not None and new_cpf_level != previous_cpf:
            employee.cpf_level = new_cpf_level
            profile_fields.append("cpf_level")
        if profile_fields:
            employee.save(update_fields=profile_fields)

        cpf_change_id = None
        if (
            record_cpf_change
            and new_cpf_level is not None
            and new_cpf_level != previous_cpf
        ):
            cpf_change = CPFLevelChange.objects.create(
                employee=employee,
                previous_level=previous_cpf,
                new_level=new_cpf_level,
                effective_date=date,
                source="promotion",
                notes=notes,
            )
            cpf_change_id = cpf_change.id

    return {
        "promotion": {
            "id": promotion.id,
            "employee_id": employee.id,
            "previous_role": previous_role.name if previous_role else None,
            "new_role": new_role.name if new_role else None,
            "previous_cpf_level": previous_cpf or None,
            "new_cpf_level": next_cpf or None,
            "date": promotion.date.isoformat(),
            "related_listing_id": related_listing.id if related_listing else None,
        },
        "cpf_change_id": cpf_change_id,
        "summary": (
            f"Recorded promotion for `{employee.full_name or employee.user.username}` "
            f"on {promotion.date}"
            + (f" → role `{new_role.name}`" if new_role else "")
            + (f", CPF `{next_cpf}`" if next_cpf and next_cpf != previous_cpf else "")
            + (f" (CPF change row id={cpf_change_id})" if cpf_change_id else "")
            + "."
        ),
    }


def record_cpf_level_change(
    *,
    user,
    employee_id: int,
    new_level: str,
    effective_date: str,
    source: str = "manual",
    cpf_score: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR admins can record CPF level changes.")
    try:
        employee = UserProfile.objects.get(pk=employee_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee not found."}) from exc
    previous_level = employee.cpf_level or ""
    with transaction.atomic():
        change = CPFLevelChange.objects.create(
            employee=employee,
            previous_level=previous_level,
            new_level=new_level,
            effective_date=effective_date,
            source=source,
            cpf_score=cpf_score,
            notes=notes,
        )
        if new_level != previous_level:
            employee.cpf_level = new_level
            employee.save(update_fields=["cpf_level"])
    return {
        "change": {
            "id": change.id,
            "employee_id": employee.id,
            "previous_level": previous_level or None,
            "new_level": new_level,
            "effective_date": change.effective_date.isoformat(),
            "source": change.source,
        },
        "summary": (
            f"CPF change `{previous_level or '∅'}` → `{new_level}` for "
            f"`{employee.full_name or employee.user.username}` effective "
            f"{change.effective_date} (source={source})."
        ),
    }


def set_compensation_policy(
    *,
    user,
    cpf_level: str,
    net_monthly: str,
    currency: str = "BAM",
    effective_date: str,
    notes: str = "",
) -> dict[str, Any]:
    require_profile(user)
    if not is_compensation_admin(user):
        raise PermissionDenied("Only compensation admins can change policies.")
    with transaction.atomic():
        policy, created = CompensationPolicy.objects.update_or_create(
            cpf_level=cpf_level,
            defaults={
                "net_monthly": Decimal(net_monthly),
                "currency": currency,
                "effective_date": effective_date,
                "notes": notes,
            },
        )
        if created and getattr(policy, "created_by_id", None) is None:
            policy.created_by = user
            policy.save(update_fields=["created_by"])
    return {
        "policy": {
            "id": policy.id,
            "cpf_level": policy.cpf_level,
            "net_monthly": str(policy.net_monthly),
            "currency": policy.currency,
            "effective_date": policy.effective_date.isoformat(),
        },
        "created": created,
        "summary": (
            f"{'Created' if created else 'Updated'} policy for CPF `{policy.cpf_level}`: "
            f"NET {policy.net_monthly} {policy.currency} (effective {policy.effective_date})."
        ),
    }


def create_job_listing(
    *,
    user,
    title: str,
    description: str = "",
    department_id: int | None = None,
    open_at: str,
    close_at: str,
    status: str = "draft",
) -> dict[str, Any]:
    actor = require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR admins can create job listings.")
    department = None
    if department_id is not None:
        try:
            department = Department.objects.get(pk=department_id)
        except Department.DoesNotExist as exc:
            raise ValidationError({"department_id": "Department not found."}) from exc
    with transaction.atomic():
        listing = JobListing.objects.create(
            title=title,
            description=description,
            department=department,
            open_at=open_at,
            close_at=close_at,
            status=status,
            created_by=actor,
        )
    return {
        "job_listing": {
            "id": listing.id,
            "title": listing.title,
            "department": listing.department.name if listing.department else None,
            "status": listing.status,
            "open_at": listing.open_at.isoformat(),
            "close_at": listing.close_at.isoformat(),
        },
        "summary": f"Created job listing `{listing.title}` (status={listing.status}).",
    }


def withdraw_application(
    *, user, application_id: int, reason: str = ""
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        application = Application.objects.select_related("listing", "applicant").get(
            pk=application_id
        )
    except Application.DoesNotExist as exc:
        raise ValidationError({"application_id": "Application not found."}) from exc
    if application.applicant_id != actor.id and not is_hr_admin(user):
        raise PermissionDenied("You can only withdraw your own applications.")
    if application.status in ("withdrawn", "rejected", "accepted"):
        raise ValidationError(
            {
                "application_id": f"Application already in terminal state `{application.status}`."
            }
        )
    with transaction.atomic():
        application.status = "withdrawn"
        application.decision_note = reason or application.decision_note
        application.decided_by = actor
        application.decided_at = timezone.now()
        application.save(
            update_fields=["status", "decision_note", "decided_by", "decided_at"]
        )
    return {
        "application": {
            "id": application.id,
            "listing_id": application.listing_id,
            "listing_title": application.listing.title,
            "status": application.status,
        },
        "summary": f"Withdrew application {application.id} from `{application.listing.title}`.",
    }


registry.register(
    AssistantTool(
        "list_bonuses",
        "List bonus records for an employee (self by default; HR can pass employee_id). Filter by year or bonus_type.",
        list_bonuses,
        module="mobility_compensation",
        sensitive=True,
        args_schema=ListBonusesArgs,
    )
)
registry.register(
    AssistantTool(
        "get_bonus_totals",
        "Get total bonuses grouped by type for an employee in a year (or all-time).",
        get_bonus_totals,
        module="mobility_compensation",
        sensitive=True,
        args_schema=GetBonusTotalsArgs,
    )
)
registry.register(
    AssistantTool(
        "create_bonus_record",
        "Record a new bonus payment for an employee. Compensation admin only.",
        create_bonus_record,
        module="mobility_compensation",
        mutating=True,
        sensitive=True,
        args_schema=CreateBonusRecordArgs,
        permission_check=_check_compensation_admin,
        required_permissions=("Compensation admin (HR)",),
        confirmation_label="Record bonus",
        confirmation_help="bonus_type: performance, retention, referral, project, education, spot. amount is a decimal string.",
    )
)
registry.register(
    AssistantTool(
        "list_compensation_policies",
        "List NET-salary compensation policies by CPF level.",
        list_compensation_policies,
        module="mobility_compensation",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "list_benefits",
        "List active global benefits and their monthly value.",
        list_benefits,
        module="mobility_compensation",
        args_schema=EmptyArgs,
    )
)
registry.register(
    AssistantTool(
        "get_payroll_snapshot",
        "Get a payroll aggregate snapshot (total, avg, median, headcount). HR-only. Latest if no date given.",
        get_payroll_snapshot,
        module="mobility_compensation",
        sensitive=True,
        permission_check=_check_compensation_admin,
        required_permissions=("Compensation admin (HR)",),
        args_schema=GetPayrollSnapshotArgs,
    )
)
registry.register(
    AssistantTool(
        "list_promotion_history",
        "List an employee's promotion history (self by default; HR for others).",
        list_promotion_history,
        module="mobility_compensation",
        args_schema=ListPromotionHistoryArgs,
    )
)
registry.register(
    AssistantTool(
        "list_cpf_level_changes",
        "List an employee's CPF level change history.",
        list_cpf_level_changes,
        module="mobility_compensation",
        args_schema=ListCPFLevelChangesArgs,
    )
)
registry.register(
    AssistantTool(
        "list_job_listings",
        "List internal job listings, filterable by status (draft, open, closed, cancelled) and search query.",
        list_job_listings,
        module="mobility_compensation",
        args_schema=ListJobListingsArgs,
    )
)
registry.register(
    AssistantTool(
        "list_applications",
        "List job applications. Employees see their own; HR sees all. Filter by listing_id and status.",
        list_applications,
        module="mobility_compensation",
        args_schema=ListApplicationsArgs,
    )
)
registry.register(
    AssistantTool(
        "apply_to_job_listing",
        "Submit an application to an internal job listing as the current user.",
        apply_to_job_listing,
        module="mobility_compensation",
        mutating=True,
        args_schema=ApplyToJobListingArgs,
        confirmation_label="Apply to job listing",
    )
)
registry.register(
    AssistantTool(
        "update_application_status",
        "Update the status of a job application (HR-only). Statuses: submitted, under_review, shortlisted, rejected, withdrawn, accepted.",
        update_application_status,
        module="mobility_compensation",
        mutating=True,
        sensitive=True,
        permission_check=_check_hr_admin,
        required_permissions=("HR admin",),
        args_schema=UpdateApplicationStatusArgs,
        confirmation_label="Update application status",
    )
)
registry.register(
    AssistantTool(
        "get_compensation_overview",
        "Get a consolidated compensation snapshot for an employee: latest salary, CPF policy, YTD bonuses, monthly benefits.",
        get_compensation_overview,
        module="mobility_compensation",
        sensitive=True,
        args_schema=GetCompensationOverviewArgs,
    )
)
registry.register(
    AssistantTool(
        "record_promotion",
        "Record a promotion for an employee. Optionally updates role + CPF level and writes a CPFLevelChange row. HR-only.",
        record_promotion,
        module="mobility_compensation",
        mutating=True,
        sensitive=True,
        permission_check=_check_hr_admin,
        required_permissions=("HR admin",),
        args_schema=RecordPromotionArgs,
        confirmation_label="Record promotion",
        confirmation_help="Sets the employee's new role and/or CPF level. If new_cpf_level differs from current and record_cpf_change=true, also writes to CPFLevelChange history.",
    )
)
registry.register(
    AssistantTool(
        "record_cpf_level_change",
        "Record a direct CPF level change for an employee (without a promotion). HR-only.",
        record_cpf_level_change,
        module="mobility_compensation",
        mutating=True,
        sensitive=True,
        permission_check=_check_hr_admin,
        required_permissions=("HR admin",),
        args_schema=RecordCPFLevelChangeArgs,
        confirmation_label="Record CPF level change",
        confirmation_help="source enum: manual, performance_review, promotion. cpf_score is optional 0-100.",
    )
)
registry.register(
    AssistantTool(
        "set_compensation_policy",
        "Create or update the NET-salary policy for a CPF level. Compensation admin only.",
        set_compensation_policy,
        module="mobility_compensation",
        mutating=True,
        sensitive=True,
        permission_check=_check_compensation_admin,
        required_permissions=("Compensation admin (HR)",),
        args_schema=SetCompensationPolicyArgs,
        confirmation_label="Set compensation policy",
        confirmation_help="Upsert by cpf_level. Replaces net_monthly for that level.",
    )
)
registry.register(
    AssistantTool(
        "create_job_listing",
        "Create a new internal job listing. HR-only.",
        create_job_listing,
        module="mobility_compensation",
        mutating=True,
        sensitive=True,
        permission_check=_check_hr_admin,
        required_permissions=("HR admin",),
        args_schema=CreateJobListingArgs,
        confirmation_label="Create job listing",
        confirmation_help="status enum: draft, open, closed, cancelled. open_at/close_at are ISO datetimes.",
    )
)
registry.register(
    AssistantTool(
        "withdraw_application",
        "Withdraw a job application. The applicant withdraws their own; HR can withdraw any.",
        withdraw_application,
        module="mobility_compensation",
        mutating=True,
        args_schema=WithdrawApplicationArgs,
        confirmation_label="Withdraw application",
    )
)


registry.register(
    AssistantTool(
        "read_document_content",
        "Read the extracted text content of a document the caller can access (PDF, DOCX, plain text). Use this AFTER list_documents to look up the document_id, then summarize or answer questions about the content.",
        read_document_content,
        module="documents",
        args_schema=ReadDocumentContentArgs,
        workflow_topic="read_document",
    )
)


# ============================================================================
# REVIEWS MODULE
# ============================================================================


class ListReviewsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)


class GetReviewArgs(_ArgsBase):
    review_id: int = Field(..., ge=1)


class ScheduleReviewArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    reviewer_id: int = Field(..., ge=1)
    review_type: str = "annual"
    title: str = ""
    period_start: str
    period_end: str
    scheduled_date: str


class AddReviewNoteArgs(_ArgsBase):
    review_id: int = Field(..., ge=1)
    content: str = Field(..., min_length=1)
    visibility: str = Field(default="shared", description="shared | private")


class ListReviewNotesArgs(_ArgsBase):
    review_id: int = Field(..., ge=1)
    limit: int | None = Field(default=50, ge=1, le=200)


class AddReviewActionPointArgs(_ArgsBase):
    review_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    description: str = ""
    owner_id: int | None = Field(default=None, ge=1)
    due_date: str | None = None


class ListReviewActionPointsArgs(_ArgsBase):
    review_id: int | None = Field(default=None, ge=1)
    owner_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    limit: int | None = Field(default=50, ge=1, le=200)


class UpdateActionPointStatusArgs(_ArgsBase):
    action_point_id: int = Field(..., ge=1)
    status: str = Field(..., min_length=1)
    progress: int | None = Field(default=None, ge=0, le=100)


class CloseReviewArgs(_ArgsBase):
    review_id: int = Field(..., ge=1)
    outcome_summary: str = ""


def _can_view_review(user, review: PerformanceReview) -> bool:
    if is_hr_admin(user):
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return review.employee_id == profile.id or review.reviewer_id == profile.id


def list_reviews(
    *,
    user,
    employee_id: int | None = None,
    status: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    actor = require_profile(user)
    qs = PerformanceReview.objects.select_related(
        "employee__user", "reviewer__user"
    ).order_by("-scheduled_date", "-id")
    if not is_hr_admin(user):
        qs = qs.filter(Q(employee=actor) | Q(reviewer=actor))
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    if status:
        qs = qs.filter(status=status)
    qs = qs[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": r.id,
            "employee_id": r.employee_id,
            "employee_name": r.employee.full_name
            or r.employee.user.get_full_name()
            or r.employee.user.username,
            "reviewer_id": r.reviewer_id,
            "reviewer_name": r.reviewer.full_name if r.reviewer else None,
            "review_type": r.review_type,
            "title": r.title,
            "status": r.status,
            "scheduled_date": (
                r.scheduled_date.isoformat() if r.scheduled_date else None
            ),
            "period_start": r.period_start.isoformat() if r.period_start else None,
            "period_end": r.period_end.isoformat() if r.period_end else None,
        }
        for r in qs
    ]
    return {"reviews": rows, "summary": f"Loaded {len(rows)} review(s)."}


def get_review(*, user, review_id: int) -> dict[str, Any]:
    require_profile(user)
    try:
        review = PerformanceReview.objects.select_related(
            "employee__user", "reviewer__user"
        ).get(pk=review_id)
    except PerformanceReview.DoesNotExist as exc:
        raise ValidationError({"review_id": "Review not found."}) from exc
    if not _can_view_review(user, review):
        raise PermissionDenied("You cannot view this review.")
    return {
        "review": {
            "id": review.id,
            "employee_id": review.employee_id,
            "reviewer_id": review.reviewer_id,
            "review_type": review.review_type,
            "title": review.title,
            "status": review.status,
            "scheduled_date": (
                review.scheduled_date.isoformat() if review.scheduled_date else None
            ),
            "period_start": (
                review.period_start.isoformat() if review.period_start else None
            ),
            "period_end": review.period_end.isoformat() if review.period_end else None,
        },
        "summary": f"Loaded review {review.id} ({review.status}).",
    }


def schedule_review(
    *,
    user,
    employee_id: int,
    reviewer_id: int,
    review_type: str = "annual",
    title: str = "",
    period_start: str,
    period_end: str,
    scheduled_date: str,
) -> dict[str, Any]:
    actor = require_profile(user)
    if not (is_hr_admin(user) or _is_manager(user)):
        raise PermissionDenied("Only HR or managers can schedule reviews.")
    try:
        employee = UserProfile.objects.get(pk=employee_id)
        reviewer = UserProfile.objects.get(pk=reviewer_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"detail": "Employee or reviewer not found."}) from exc
    with transaction.atomic():
        review = PerformanceReview.objects.create(
            employee=employee,
            reviewer=reviewer,
            created_by=actor,
            updated_by=actor,
            review_type=review_type,
            title=title or f"{review_type.title()} review",
            period_start=period_start,
            period_end=period_end,
            scheduled_date=scheduled_date,
        )
    return {
        "review": {
            "id": review.id,
            "scheduled_date": review.scheduled_date.isoformat(),
        },
        "summary": f"Scheduled review #{review.id} for `{employee.full_name or employee.user.username}` on {scheduled_date}.",
    }


def _is_manager(user) -> bool:
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return UserProfile.objects.filter(managers=profile).exists()


def add_review_note(
    *, user, review_id: int, content: str, visibility: str = "shared"
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        review = PerformanceReview.objects.get(pk=review_id)
    except PerformanceReview.DoesNotExist as exc:
        raise ValidationError({"review_id": "Review not found."}) from exc
    if not _can_view_review(user, review):
        raise PermissionDenied("You cannot add notes to this review.")
    with transaction.atomic():
        note = PerformanceReviewNote.objects.create(
            review=review,
            author=actor,
            content=content,
            visibility=visibility,
        )
    return {
        "note": {
            "id": note.id,
            "visibility": note.visibility,
            "content": note.content[:200],
        },
        "summary": f"Added {visibility} note to review #{review_id}.",
    }


def list_review_notes(
    *, user, review_id: int, limit: int | None = 50
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        review = PerformanceReview.objects.get(pk=review_id)
    except PerformanceReview.DoesNotExist as exc:
        raise ValidationError({"review_id": "Review not found."}) from exc
    if not _can_view_review(user, review):
        raise PermissionDenied("You cannot view this review.")
    qs = PerformanceReviewNote.objects.filter(review=review).select_related(
        "author__user"
    )
    # Private notes only visible to author + HR
    if not is_hr_admin(user):
        qs = qs.filter(Q(visibility="shared") | Q(author=actor))
    qs = qs.order_by("-created_at")[: _limit(limit, default=50, maximum=200)]
    rows = [
        {
            "id": n.id,
            "author_id": n.author_id,
            "author_name": n.author.full_name if n.author else None,
            "visibility": n.visibility,
            "content": n.content,
            "created_at": n.created_at.isoformat(),
        }
        for n in qs
    ]
    return {"notes": rows, "summary": f"Loaded {len(rows)} note(s)."}


def add_review_action_point(
    *,
    user,
    review_id: int,
    title: str,
    description: str = "",
    owner_id: int | None = None,
    due_date: str | None = None,
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        review = PerformanceReview.objects.get(pk=review_id)
    except PerformanceReview.DoesNotExist as exc:
        raise ValidationError({"review_id": "Review not found."}) from exc
    if not _can_view_review(user, review):
        raise PermissionDenied("You cannot add action points to this review.")
    owner = None
    if owner_id is not None:
        try:
            owner = UserProfile.objects.get(pk=owner_id)
        except UserProfile.DoesNotExist as exc:
            raise ValidationError({"owner_id": "Owner not found."}) from exc
    with transaction.atomic():
        ap = PerformanceReviewActionPoint.objects.create(
            review=review,
            title=title,
            description=description,
            owner=owner,
            created_by=actor,
            due_date=due_date,
        )
    return {
        "action_point": {"id": ap.id, "title": ap.title, "status": ap.status},
        "summary": f"Added action point `{ap.title}` to review #{review_id}.",
    }


def list_review_action_points(
    *,
    user,
    review_id: int | None = None,
    owner_id: int | None = None,
    status: str | None = None,
    limit: int | None = 50,
) -> dict[str, Any]:
    actor = require_profile(user)
    qs = PerformanceReviewActionPoint.objects.select_related("review", "owner__user")
    if review_id:
        qs = qs.filter(review_id=review_id)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if status:
        qs = qs.filter(status=status)
    if not is_hr_admin(user):
        qs = qs.filter(
            Q(owner=actor) | Q(review__employee=actor) | Q(review__reviewer=actor)
        )
    qs = qs.order_by("-id")[: _limit(limit, default=50, maximum=200)]
    rows = [
        {
            "id": a.id,
            "review_id": a.review_id,
            "title": a.title,
            "owner_id": a.owner_id,
            "due_date": a.due_date.isoformat() if a.due_date else None,
            "status": a.status,
            "progress": a.progress,
        }
        for a in qs
    ]
    return {"action_points": rows, "summary": f"Loaded {len(rows)} action point(s)."}


def update_action_point_status(
    *,
    user,
    action_point_id: int,
    status: str,
    progress: int | None = None,
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        ap = PerformanceReviewActionPoint.objects.select_related("review").get(
            pk=action_point_id
        )
    except PerformanceReviewActionPoint.DoesNotExist as exc:
        raise ValidationError({"action_point_id": "Action point not found."}) from exc
    if not (
        is_hr_admin(user)
        or ap.owner_id == actor.id
        or _can_view_review(user, ap.review)
    ):
        raise PermissionDenied("You cannot update this action point.")
    fields = ["status"]
    ap.status = status
    if progress is not None:
        ap.progress = progress
        fields.append("progress")
    if status == "done":
        ap.completed_at = timezone.now()
        fields.append("completed_at")
    with transaction.atomic():
        ap.save(update_fields=fields)
    return {
        "action_point": {"id": ap.id, "status": ap.status, "progress": ap.progress},
        "summary": f"Updated action point #{ap.id} → `{status}`.",
    }


def close_review(*, user, review_id: int, outcome_summary: str = "") -> dict[str, Any]:
    actor = require_profile(user)
    try:
        review = PerformanceReview.objects.get(pk=review_id)
    except PerformanceReview.DoesNotExist as exc:
        raise ValidationError({"review_id": "Review not found."}) from exc
    if not (is_hr_admin(user) or review.reviewer_id == actor.id):
        raise PermissionDenied("Only the reviewer or HR can close a review.")
    review.status = "completed"
    review.updated_by = actor
    fields = ["status", "updated_by", "updated_at"]
    if outcome_summary and hasattr(review, "outcome_summary"):
        review.outcome_summary = outcome_summary
        fields.append("outcome_summary")
    with transaction.atomic():
        review.save(update_fields=fields)
    return {
        "review": {"id": review.id, "status": review.status},
        "summary": f"Closed review #{review.id}.",
    }


# ============================================================================
# TRAINING & DEVELOPMENT MODULE
# ============================================================================


class ListTrainingEntriesArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    training_type: str | None = None
    year: int | None = Field(default=None, ge=2000, le=2100)
    limit: int | None = Field(default=20, ge=1, le=100)


class CreateTrainingEntryArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    course_title: str = Field(..., min_length=1)
    provider: str = ""
    training_date: str
    training_type: str = "course"
    cost: str = "0"
    description: str = ""


class ListCertificatesArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    expiring_within_days: int | None = Field(default=None, ge=1, le=730)
    limit: int | None = Field(default=20, ge=1, le=100)


class ListPeerSessionsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=20, ge=1, le=100)


class LogPeerSessionArgs(_ArgsBase):
    topic: str = Field(..., min_length=1)
    session_date: str
    duration_minutes: int = Field(..., ge=1, le=480)
    description: str = ""


class GetTrainingBudgetArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    fiscal_year: int | None = Field(default=None, ge=2000, le=2100)


class ListTrainingBudgetsArgs(_ArgsBase):
    fiscal_year: int | None = Field(default=None, ge=2000, le=2100)
    limit: int | None = Field(default=50, ge=1, le=200)


class ListConferenceRegistrationsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)


class RegisterForConferenceArgs(_ArgsBase):
    name: str = Field(..., min_length=1)
    date: str
    notes: str = ""


def list_training_entries(
    *,
    user,
    employee_id: int | None = None,
    training_type: str | None = None,
    year: int | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = TrainingEntry.objects.filter(employee=target).order_by("-training_date", "-id")
    if training_type:
        qs = qs.filter(training_type=training_type)
    if year:
        qs = qs.filter(training_date__year=year)
    qs = qs[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": t.id,
            "employee_id": t.employee_id,
            "course_title": t.course_title,
            "provider": t.provider,
            "training_date": t.training_date.isoformat() if t.training_date else None,
            "training_type": t.training_type,
            "cost": str(t.cost),
        }
        for t in qs
    ]
    return {
        "training_entries": rows,
        "summary": f"Loaded {len(rows)} training entry(ies).",
    }


def create_training_entry(
    *,
    user,
    employee_id: int,
    course_title: str,
    provider: str = "",
    training_date: str,
    training_type: str = "course",
    cost: str = "0",
    description: str = "",
) -> dict[str, Any]:
    actor = require_profile(user)
    if employee_id != actor.id and not is_hr_admin(user):
        raise PermissionDenied(
            "You can only log training for yourself or HR for others."
        )
    try:
        employee = UserProfile.objects.get(pk=employee_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee not found."}) from exc
    with transaction.atomic():
        entry = TrainingEntry.objects.create(
            employee=employee,
            course_title=course_title,
            provider=provider,
            training_date=training_date,
            training_type=training_type,
            cost=Decimal(cost),
            description=description,
        )
    return {
        "training_entry": {"id": entry.id, "course_title": entry.course_title},
        "summary": f"Logged training `{course_title}` for `{employee.full_name or employee.user.username}`.",
    }


def list_certificates(
    *,
    user,
    employee_id: int | None = None,
    expiring_within_days: int | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = Certificate.objects.filter(employee=target).order_by("-issued_date", "-id")
    if expiring_within_days is not None:
        from datetime import timedelta as _td

        cutoff = timezone.now().date() + _td(days=expiring_within_days)
        qs = qs.filter(expiration_date__lte=cutoff, expiration_date__isnull=False)
    qs = qs[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": c.id,
            "title": c.title,
            "issuer": c.issuer,
            "issued_date": c.issued_date.isoformat() if c.issued_date else None,
            "expiration_date": (
                c.expiration_date.isoformat() if c.expiration_date else None
            ),
        }
        for c in qs
    ]
    return {"certificates": rows, "summary": f"Loaded {len(rows)} certificate(s)."}


def list_peer_sessions(
    *, user, employee_id: int | None = None, limit: int | None = 20
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = PeerSession.objects.filter(employee=target).order_by("-session_date", "-id")[
        : _limit(limit, default=20, maximum=100)
    ]
    rows = [
        {
            "id": s.id,
            "topic": s.topic,
            "session_date": s.session_date.isoformat() if s.session_date else None,
            "duration_minutes": s.duration_minutes,
        }
        for s in qs
    ]
    return {"peer_sessions": rows, "summary": f"Loaded {len(rows)} peer session(s)."}


def log_peer_session(
    *,
    user,
    topic: str,
    session_date: str,
    duration_minutes: int,
    description: str = "",
) -> dict[str, Any]:
    actor = require_profile(user)
    with transaction.atomic():
        session = PeerSession.objects.create(
            employee=actor,
            topic=topic,
            session_date=session_date,
            duration_minutes=duration_minutes,
            description=description,
        )
    return {
        "peer_session": {"id": session.id, "topic": session.topic},
        "summary": f"Logged peer session `{topic}` ({duration_minutes}m).",
    }


def get_training_budget(
    *, user, employee_id: int | None = None, fiscal_year: int | None = None
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    fy = fiscal_year or timezone.now().year
    try:
        budget = TrainingBudget.objects.get(employee=target, fiscal_year=fy)
    except TrainingBudget.DoesNotExist:
        return {
            "budget": None,
            "summary": f"No training budget set for `{target.full_name or target.user.username}` in {fy}.",
        }
    remaining = Decimal(budget.allocated_budget) - Decimal(budget.used_budget)
    return {
        "budget": {
            "fiscal_year": budget.fiscal_year,
            "allocated_budget": str(budget.allocated_budget),
            "used_budget": str(budget.used_budget),
            "remaining": str(remaining),
        },
        "summary": (
            f"Training budget {fy}: allocated {budget.allocated_budget}, "
            f"used {budget.used_budget}, remaining {remaining}."
        ),
    }


def list_training_budgets(
    *, user, fiscal_year: int | None = None, limit: int | None = 50
) -> dict[str, Any]:
    require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR can list all training budgets.")
    qs = TrainingBudget.objects.select_related("employee__user").order_by(
        "-fiscal_year", "employee__full_name"
    )
    if fiscal_year:
        qs = qs.filter(fiscal_year=fiscal_year)
    qs = qs[: _limit(limit, default=50, maximum=200)]
    rows = [
        {
            "id": b.id,
            "employee_id": b.employee_id,
            "employee_name": b.employee.full_name or b.employee.user.get_full_name(),
            "fiscal_year": b.fiscal_year,
            "allocated_budget": str(b.allocated_budget),
            "used_budget": str(b.used_budget),
        }
        for b in qs
    ]
    return {"budgets": rows, "summary": f"Loaded {len(rows)} training budget(s)."}


def list_conference_registrations(
    *,
    user,
    employee_id: int | None = None,
    status: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = ConferenceCourseRegistration.objects.filter(employee=target).order_by(
        "-date", "-id"
    )
    if status:
        qs = qs.filter(status=status)
    qs = qs[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": r.id,
            "name": r.name,
            "date": r.date.isoformat() if r.date else None,
            "status": r.status,
            "notes": r.notes,
        }
        for r in qs
    ]
    return {"registrations": rows, "summary": f"Loaded {len(rows)} registration(s)."}


def register_for_conference(
    *, user, name: str, date: str, notes: str = ""
) -> dict[str, Any]:
    actor = require_profile(user)
    with transaction.atomic():
        reg = ConferenceCourseRegistration.objects.create(
            employee=actor, name=name, date=date, notes=notes
        )
    return {
        "registration": {"id": reg.id, "name": reg.name, "status": reg.status},
        "summary": f"Registered for `{name}` on {date}.",
    }


# ============================================================================
# EMPLOYEES MODULE — extensions
# ============================================================================


class UpdateEmployeeProfileArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    full_name: str | None = None
    email_address: str | None = None
    phone: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    cpf_level: str | None = None


class ListProjectAssignmentsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)


class ListEquipmentAssignmentsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=20, ge=1, le=100)


class ListSalaryHistoryArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    limit: int | None = Field(default=20, ge=1, le=100)


class AssignManagerArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    manager_id: int = Field(..., ge=1)


def update_employee_profile(
    *,
    user,
    employee_id: int,
    full_name: str | None = None,
    email_address: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    emergency_contact: str | None = None,
    cpf_level: str | None = None,
) -> dict[str, Any]:
    actor = require_profile(user)
    if employee_id != actor.id and not is_hr_admin(user):
        raise PermissionDenied("You can only update your own profile (HR for others).")
    try:
        employee = UserProfile.objects.get(pk=employee_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee not found."}) from exc
    if cpf_level is not None and not is_hr_admin(user):
        raise PermissionDenied("Only HR can change CPF level.")
    fields: list[str] = []
    for fname, value in [
        ("full_name", full_name),
        ("email_address", email_address),
        ("phone", phone),
        ("address", address),
        ("emergency_contact", emergency_contact),
        ("cpf_level", cpf_level),
    ]:
        if value is not None and hasattr(employee, fname):
            setattr(employee, fname, value)
            fields.append(fname)
    if not fields:
        return {"updated": False, "summary": "No changes."}
    with transaction.atomic():
        employee.save(update_fields=fields)
    return {
        "employee_id": employee.id,
        "updated_fields": fields,
        "summary": f"Updated employee #{employee.id}: {', '.join(fields)}.",
    }


def list_project_assignments(
    *,
    user,
    employee_id: int | None = None,
    project_id: int | None = None,
    status: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    actor = require_profile(user)
    qs = ProjectAssignment.objects.select_related("user_profile__user", "project")
    if not is_hr_admin(user):
        qs = qs.filter(user_profile=actor)
    if employee_id:
        qs = qs.filter(user_profile_id=employee_id)
    if project_id:
        qs = qs.filter(project_id=project_id)
    if status:
        qs = qs.filter(status=status)
    qs = qs.order_by("-start_date", "-id")[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": a.id,
            "employee_id": a.user_profile_id,
            "project_id": a.project_id,
            "project_name": a.project.name if a.project else None,
            "role": a.role,
            "allocation_percentage": (
                str(a.allocation_percentage)
                if a.allocation_percentage is not None
                else None
            ),
            "start_date": a.start_date.isoformat() if a.start_date else None,
            "end_date": a.end_date.isoformat() if a.end_date else None,
            "status": a.status,
        }
        for a in qs
    ]
    return {
        "assignments": rows,
        "summary": f"Loaded {len(rows)} project assignment(s).",
    }


def list_equipment_assignments(
    *, user, employee_id: int | None = None, limit: int | None = 20
) -> dict[str, Any]:
    actor = require_profile(user)
    qs = EquipmentAssignment.objects.select_related("equipment", "user_profile__user")
    if not is_hr_admin(user):
        qs = qs.filter(user_profile=actor)
    if employee_id:
        qs = qs.filter(user_profile_id=employee_id)
    qs = qs.order_by("-assigned_date", "-id")[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": a.id,
            "employee_id": a.user_profile_id,
            "equipment_id": a.equipment_id,
            "assigned_date": a.assigned_date.isoformat() if a.assigned_date else None,
            "returned_date": a.returned_date.isoformat() if a.returned_date else None,
        }
        for a in qs
    ]
    return {
        "assignments": rows,
        "summary": f"Loaded {len(rows)} equipment assignment(s).",
    }


def list_salary_history(
    *, user, employee_id: int, limit: int | None = 20
) -> dict[str, Any]:
    require_profile(user)
    if not is_compensation_admin(user):
        raise PermissionDenied("Salary history is HR / comp admin only.")
    try:
        employee = UserProfile.objects.get(pk=employee_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee not found."}) from exc
    qs = SalaryRecord.objects.filter(user_profile=employee).order_by(
        "-effective_date", "-id"
    )[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": s.id,
            "amount": str(s.amount),
            "currency": getattr(s, "currency", "BAM"),
            "effective_date": (
                s.effective_date.isoformat() if s.effective_date else None
            ),
        }
        for s in qs
    ]
    return {"salary_history": rows, "summary": f"Loaded {len(rows)} salary record(s)."}


def assign_manager(*, user, employee_id: int, manager_id: int) -> dict[str, Any]:
    require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR can assign managers.")
    try:
        employee = UserProfile.objects.get(pk=employee_id)
        manager = UserProfile.objects.get(pk=manager_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"detail": "Employee or manager not found."}) from exc
    if manager_id == employee_id:
        raise ValidationError(
            {"manager_id": "An employee cannot be their own manager."}
        )
    with transaction.atomic():
        employee.managers.add(manager)
    return {
        "summary": f"Assigned `{manager.full_name or manager.user.username}` as manager of `{employee.full_name or employee.user.username}`.",
    }


# ============================================================================
# VACATIONS MODULE — extensions
# ============================================================================


class CancelLeaveRequestArgs(_ArgsBase):
    leave_request_id: int = Field(..., ge=1)
    reason: str = ""


class AdjustLeaveBalanceArgs(_ArgsBase):
    employee_id: int = Field(..., ge=1)
    leave_type: str
    year: int = Field(..., ge=2000, le=2100)
    allocated_delta: str = "0"
    used_delta: str = "0"
    reason: str = ""


class GetLeaveSummaryArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    year: int | None = Field(default=None, ge=2000, le=2100)


class GetTeamLeaveCalendarArgs(_ArgsBase):
    date_from: str
    date_to: str
    department_id: int | None = Field(default=None, ge=1)


def cancel_leave_request(
    *, user, leave_request_id: int, reason: str = ""
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        request_row = LeaveRequest.objects.get(pk=leave_request_id)
    except LeaveRequest.DoesNotExist as exc:
        raise ValidationError({"leave_request_id": "Leave request not found."}) from exc
    if request_row.employee_id != actor.id and not is_hr_admin(user):
        raise PermissionDenied("You can only cancel your own leave request.")
    request_row.status = "cancelled"
    with transaction.atomic():
        request_row.save(update_fields=["status"])
    return {
        "leave_request": {"id": request_row.id, "status": request_row.status},
        "summary": f"Cancelled leave request #{request_row.id}.",
    }


def adjust_leave_balance(
    *,
    user,
    employee_id: int,
    leave_type: str,
    year: int,
    allocated_delta: str = "0",
    used_delta: str = "0",
    reason: str = "",
) -> dict[str, Any]:
    require_profile(user)
    if not IsHRAdminForAdjustment().has_permission(_request_for(user), None):
        raise PermissionDenied("Only HR admins can adjust leave balances.")
    try:
        employee = UserProfile.objects.get(pk=employee_id)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError({"employee_id": "Employee not found."}) from exc
    with transaction.atomic():
        balance, _ = LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=leave_type,
            year=year,
            defaults={"allocated": 0, "used": 0, "carryover": 0},
        )
        balance.allocated = (balance.allocated or 0) + Decimal(allocated_delta)
        balance.used = (balance.used or 0) + Decimal(used_delta)
        balance.save(update_fields=["allocated", "used"])
    return {
        "balance": {
            "leave_type": balance.leave_type,
            "year": balance.year,
            "allocated": str(balance.allocated),
            "used": str(balance.used),
        },
        "summary": (
            f"Adjusted {leave_type} balance for `{employee.full_name or employee.user.username}` "
            f"({year}): allocated {allocated_delta:+}, used {used_delta:+}."
        ),
    }


def get_leave_summary(
    *, user, employee_id: int | None = None, year: int | None = None
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    fy = year or timezone.now().year
    balances = LeaveBalance.objects.filter(employee=target, year=fy)
    used_total = sum((Decimal(b.used or 0) for b in balances), Decimal("0"))
    allocated_total = sum((Decimal(b.allocated or 0) for b in balances), Decimal("0"))
    by_type = {
        b.leave_type: {"allocated": str(b.allocated), "used": str(b.used)}
        for b in balances
    }
    return {
        "year": fy,
        "by_type": by_type,
        "totals": {"allocated": str(allocated_total), "used": str(used_total)},
        "summary": (
            f"Leave summary {fy} for `{target.full_name or target.user.username}`: "
            f"{used_total} used / {allocated_total} allocated across {len(by_type)} types."
        ),
    }


def get_team_leave_calendar(
    *,
    user,
    date_from: str,
    date_to: str,
    department_id: int | None = None,
) -> dict[str, Any]:
    actor = require_profile(user)
    qs = LeaveRequest.objects.select_related("employee__user").filter(
        start_date__lte=date_to, end_date__gte=date_from, status="approved"
    )
    if department_id:
        qs = qs.filter(employee__department_id=department_id)
    elif not is_hr_admin(user):
        # default: own team (employees who share a manager or are own reports)
        qs = qs.filter(
            Q(employee=actor)
            | Q(employee__managers=actor)
            | Q(
                employee__in=UserProfile.objects.filter(
                    managers__in=actor.managers.all()
                )
            )
        )
    rows = [
        {
            "id": r.id,
            "employee_id": r.employee_id,
            "employee_name": r.employee.full_name or r.employee.user.get_full_name(),
            "leave_type": r.leave_type,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
        }
        for r in qs.order_by("start_date")
    ]
    return {
        "leaves": rows,
        "summary": f"{len(rows)} approved leaves between {date_from} and {date_to}.",
    }


# ============================================================================
# ONBOARDING MODULE — extensions
# ============================================================================


class ListChecklistTemplatesArgs(_ArgsBase):
    limit: int | None = Field(default=20, ge=1, le=100)


class ListChecklistInstancesArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)


class GetChecklistInstanceArgs(_ArgsBase):
    instance_id: int = Field(..., ge=1)


class ListChecklistTasksArgs(_ArgsBase):
    instance_id: int = Field(..., ge=1)
    status: str | None = None


class UpdateChecklistTaskStatusArgs(_ArgsBase):
    task_id: int = Field(..., ge=1)
    status: str = Field(..., min_length=1)


def list_checklist_templates(*, user, limit: int | None = 20) -> dict[str, Any]:
    require_profile(user)
    qs = ChecklistTemplate.objects.all().order_by("name")[
        : _limit(limit, default=20, maximum=100)
    ]
    rows = [
        {
            "id": t.id,
            "name": t.name,
            "description": getattr(t, "description", ""),
            "kind": getattr(t, "kind", None),
        }
        for t in qs
    ]
    return {"templates": rows, "summary": f"Loaded {len(rows)} template(s)."}


def list_checklist_instances(
    *,
    user,
    employee_id: int | None = None,
    status: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    actor = require_profile(user)
    qs = ChecklistInstance.objects.select_related("employee__user", "template")
    if not is_hr_admin(user):
        qs = qs.filter(employee=actor)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    if status and hasattr(ChecklistInstance, "status"):
        qs = qs.filter(status=status)
    qs = qs.order_by("-id")[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": i.id,
            "employee_id": i.employee_id,
            "template_id": i.template_id,
            "template_name": i.template.name if i.template else None,
            "due_date": i.due_date.isoformat() if i.due_date else None,
        }
        for i in qs
    ]
    return {"instances": rows, "summary": f"Loaded {len(rows)} checklist instance(s)."}


def get_checklist_instance(*, user, instance_id: int) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        instance = ChecklistInstance.objects.select_related("template", "employee").get(
            pk=instance_id
        )
    except ChecklistInstance.DoesNotExist as exc:
        raise ValidationError({"instance_id": "Checklist instance not found."}) from exc
    if instance.employee_id != actor.id and not is_hr_admin(user):
        raise PermissionDenied("You cannot view this checklist instance.")
    return {
        "instance": {
            "id": instance.id,
            "employee_id": instance.employee_id,
            "template_name": instance.template.name if instance.template else None,
            "due_date": instance.due_date.isoformat() if instance.due_date else None,
        },
        "summary": f"Loaded checklist instance #{instance.id}.",
    }


def list_checklist_tasks(
    *, user, instance_id: int, status: str | None = None
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        instance = ChecklistInstance.objects.get(pk=instance_id)
    except ChecklistInstance.DoesNotExist as exc:
        raise ValidationError({"instance_id": "Instance not found."}) from exc
    if instance.employee_id != actor.id and not is_hr_admin(user):
        raise PermissionDenied("You cannot view this instance's tasks.")
    qs = instance.tasks.all() if hasattr(instance, "tasks") else []
    if status and hasattr(qs, "filter"):
        qs = qs.filter(status=status)
    rows = []
    for task in qs:
        rows.append(
            {
                "id": task.id,
                "name": getattr(task, "name", "") or getattr(task, "title", ""),
                "status": getattr(task, "status", None),
                "owner_id": getattr(task, "owner_id", None),
                "due_date": (
                    task.due_date.isoformat()
                    if getattr(task, "due_date", None)
                    else None
                ),
            }
        )
    return {"tasks": rows, "summary": f"Loaded {len(rows)} task(s)."}


def update_checklist_task_status(*, user, task_id: int, status: str) -> dict[str, Any]:
    actor = require_profile(user)
    from django.apps import apps

    Task = None
    for model in apps.get_models():
        if model.__name__ == "ChecklistTask":
            Task = model
            break
    if Task is None:
        raise ValidationError({"task_id": "ChecklistTask model not found."})
    try:
        task = Task.objects.select_related("instance").get(pk=task_id)
    except Task.DoesNotExist as exc:
        raise ValidationError({"task_id": "Task not found."}) from exc
    instance = getattr(task, "instance", None)
    if instance and instance.employee_id != actor.id and not is_hr_admin(user):
        if getattr(task, "owner_id", None) != actor.id:
            raise PermissionDenied("You cannot update this task.")
    task.status = status
    with transaction.atomic():
        task.save(update_fields=["status"])
    return {
        "task": {"id": task.id, "status": task.status},
        "summary": f"Updated task #{task.id} → `{status}`.",
    }


# ============================================================================
# ASSETS MODULE — extensions
# ============================================================================


class AssignAssetArgs(_ArgsBase):
    asset_id: int = Field(..., ge=1)
    employee_id: int = Field(..., ge=1)


class ReturnAssetArgs(_ArgsBase):
    assignment_id: int = Field(..., ge=1)
    note: str = ""


class ListAssetAssignmentsArgs(_ArgsBase):
    asset_id: int | None = Field(default=None, ge=1)
    employee_id: int | None = Field(default=None, ge=1)
    active_only: bool = True
    limit: int | None = Field(default=20, ge=1, le=100)


class ListAssetReplacementsArgs(_ArgsBase):
    asset_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=20, ge=1, le=100)


def assign_asset_to_employee(
    *, user, asset_id: int, employee_id: int
) -> dict[str, Any]:
    require_profile(user)
    from core.permissions import has_asset_permission

    if not has_asset_permission(user, "assign_assets"):
        raise PermissionDenied("You do not have permission to assign assets.")
    try:
        asset = Asset.objects.get(pk=asset_id)
        employee = UserProfile.objects.get(pk=employee_id)
    except (Asset.DoesNotExist, UserProfile.DoesNotExist) as exc:
        raise ValidationError({"detail": "Asset or employee not found."}) from exc
    with transaction.atomic():
        assignment = Assignment.objects.create(asset=asset, employee=employee)
    return {
        "assignment": {
            "id": assignment.id,
            "asset_id": asset.id,
            "employee_id": employee.id,
        },
        "summary": f"Assigned asset `{asset.asset_id}` to `{employee.full_name or employee.user.username}`.",
    }


def return_asset(*, user, assignment_id: int, note: str = "") -> dict[str, Any]:
    actor = require_profile(user)
    from core.permissions import has_asset_permission

    try:
        assignment = Assignment.objects.select_related("asset", "employee").get(
            pk=assignment_id
        )
    except Assignment.DoesNotExist as exc:
        raise ValidationError({"assignment_id": "Assignment not found."}) from exc
    is_owner = assignment.employee_id == actor.id
    if not (is_owner or has_asset_permission(user, "process_asset_return")):
        raise PermissionDenied(
            "Only the owner or asset return processors can return assets."
        )
    if assignment.returned_at is not None:
        raise ValidationError({"assignment_id": "Asset already returned."})
    with transaction.atomic():
        assignment.returned_at = timezone.now()
        assignment.save(update_fields=["returned_at"])
    return {
        "assignment": {
            "id": assignment.id,
            "returned_at": assignment.returned_at.isoformat(),
        },
        "summary": f"Returned asset assignment #{assignment.id}.",
    }


def list_asset_assignments(
    *,
    user,
    asset_id: int | None = None,
    employee_id: int | None = None,
    active_only: bool = True,
    limit: int | None = 20,
) -> dict[str, Any]:
    require_profile(user)
    qs = Assignment.objects.select_related("asset", "employee__user")
    if asset_id:
        qs = qs.filter(asset_id=asset_id)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    if active_only:
        qs = qs.filter(returned_at__isnull=True)
    qs = qs.order_by("-assigned_at", "-id")[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": a.id,
            "asset_id": a.asset_id,
            "asset_name": a.asset.name if a.asset else None,
            "employee_id": a.employee_id,
            "employee_name": a.employee.full_name if a.employee else None,
            "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
            "returned_at": a.returned_at.isoformat() if a.returned_at else None,
        }
        for a in qs
    ]
    return {"assignments": rows, "summary": f"Loaded {len(rows)} asset assignment(s)."}


def list_asset_replacements(
    *, user, asset_id: int | None = None, limit: int | None = 20
) -> dict[str, Any]:
    require_profile(user)
    qs = ReplacementLog.objects.select_related("asset")
    if asset_id:
        qs = qs.filter(asset_id=asset_id)
    qs = qs.order_by("-date", "-id")[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": r.id,
            "asset_id": r.asset_id,
            "reason": r.reason,
            "date": r.date.isoformat() if r.date else None,
        }
        for r in qs
    ]
    return {"replacements": rows, "summary": f"Loaded {len(rows)} replacement(s)."}


# ============================================================================
# DOCUMENTS MODULE — extensions
# ============================================================================


class DeleteDocumentArgs(_ArgsBase):
    document_id: int = Field(..., ge=1)
    reason: str = ""


class ListEmployeeDocumentsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    doc_type: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)


def delete_document(*, user, document_id: int, reason: str = "") -> dict[str, Any]:
    require_profile(user)
    if not is_hr_admin(user):
        raise PermissionDenied("Only HR can delete documents.")
    try:
        doc = Document.objects.get(pk=document_id)
    except Document.DoesNotExist as exc:
        raise ValidationError({"document_id": "Document not found."}) from exc
    name = doc.name
    with transaction.atomic():
        doc.delete()
    return {
        "summary": f"Deleted document `{name}` (id={document_id}). Reason: {reason or 'n/a'}."
    }


def list_employee_documents(
    *,
    user,
    employee_id: int | None = None,
    doc_type: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    target = _profile_for(user, employee_id)
    qs = EmployeeDocument.objects.filter(user_profile=target)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)
    qs = qs.order_by("-uploaded_at", "-id")[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": d.id,
            "doc_type": d.doc_type,
            "version": d.version,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "is_current": d.is_current,
        }
        for d in qs
    ]
    return {"documents": rows, "summary": f"Loaded {len(rows)} employee document(s)."}


# ============================================================================
# TIME TRACKING MODULE — extensions
# ============================================================================


class ApproveTimeEntryArgs(_ArgsBase):
    time_entry_id: int = Field(..., ge=1)
    comments: str = ""


class RejectTimeEntryArgs(_ArgsBase):
    time_entry_id: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1)


class ListPendingTimeApprovalsArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=20, ge=1, le=100)


def approve_time_entry(
    *, user, time_entry_id: int, comments: str = ""
) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        entry = TimeEntry.objects.select_related("employee").get(pk=time_entry_id)
    except TimeEntry.DoesNotExist as exc:
        raise ValidationError({"time_entry_id": "Time entry not found."}) from exc
    if not (is_hr_admin(user) or entry.employee.managers.filter(pk=actor.id).exists()):
        raise PermissionDenied("Only the manager or HR can approve time entries.")
    with transaction.atomic():
        entry.status = "approved"
        entry.save(update_fields=["status"])
    return {
        "time_entry": {"id": entry.id, "status": entry.status},
        "summary": f"Approved time entry #{entry.id}.",
    }


def reject_time_entry(*, user, time_entry_id: int, reason: str) -> dict[str, Any]:
    actor = require_profile(user)
    try:
        entry = TimeEntry.objects.select_related("employee").get(pk=time_entry_id)
    except TimeEntry.DoesNotExist as exc:
        raise ValidationError({"time_entry_id": "Time entry not found."}) from exc
    if not (is_hr_admin(user) or entry.employee.managers.filter(pk=actor.id).exists()):
        raise PermissionDenied("Only the manager or HR can reject time entries.")
    with transaction.atomic():
        entry.status = "rejected"
        if hasattr(entry, "rejection_reason"):
            entry.rejection_reason = reason
            entry.save(update_fields=["status", "rejection_reason"])
        else:
            entry.save(update_fields=["status"])
    return {
        "time_entry": {"id": entry.id, "status": entry.status},
        "summary": f"Rejected time entry #{entry.id}. Reason: {reason}.",
    }


def list_pending_time_approvals(
    *, user, employee_id: int | None = None, limit: int | None = 20
) -> dict[str, Any]:
    actor = require_profile(user)
    if not (is_hr_admin(user) or _is_manager(user)):
        raise PermissionDenied("Only managers or HR can list pending approvals.")
    qs = TimeEntry.objects.select_related("employee__user", "project").filter(
        status="submitted"
    )
    if not is_hr_admin(user):
        qs = qs.filter(employee__managers=actor)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    qs = qs.order_by("-work_date", "-id")[: _limit(limit, default=20, maximum=100)]
    rows = [
        {
            "id": t.id,
            "employee_id": t.employee_id,
            "employee_name": t.employee.full_name if t.employee else None,
            "work_date": t.work_date.isoformat() if t.work_date else None,
            "hours": str(t.hours),
            "project": t.project.name if t.project else None,
        }
        for t in qs
    ]
    return {"pending_entries": rows, "summary": f"{len(rows)} pending time entry/ies."}


# ============================================================================
# ANNOUNCEMENTS MODULE
# ============================================================================


class ListAnnouncementsArgs(_ArgsBase):
    query: str = ""
    type: str | None = None
    include_scheduled: bool = False
    limit: int | None = Field(default=20, ge=1, le=100)


class GetAnnouncementArgs(_ArgsBase):
    announcement_id: int = Field(..., ge=1)


class CreateAnnouncementArgs(_ArgsBase):
    title: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1)
    type: str = ""
    scheduled_at: str | None = None
    send_email_notifications: bool = False


class UpdateAnnouncementArgs(_ArgsBase):
    announcement_id: int = Field(..., ge=1)
    title: str | None = Field(default=None, max_length=255)
    body: str | None = None
    type: str | None = None
    scheduled_at: str | None = None
    send_email_notifications: bool = False


class DeleteAnnouncementArgs(_ArgsBase):
    announcement_id: int = Field(..., ge=1)


class AnnouncementCommentArgs(_ArgsBase):
    announcement_id: int = Field(..., ge=1)
    body: str = Field(..., min_length=1)


class DeleteAnnouncementCommentArgs(_ArgsBase):
    announcement_id: int = Field(..., ge=1)
    comment_id: int = Field(..., ge=1)


class ToggleAnnouncementReactionArgs(_ArgsBase):
    announcement_id: int = Field(..., ge=1)
    reaction_type: str = Field(..., min_length=1, max_length=64)


class ListDiscordAnnouncementChannelsArgs(_ArgsBase):
    announcement_type: str | None = None
    enabled: bool | None = None
    limit: int | None = Field(default=50, ge=1, le=100)


class CreateDiscordAnnouncementChannelArgs(_ArgsBase):
    announcement_type: str = Field(..., min_length=1)
    channel_name: str = Field(..., min_length=1, max_length=255)
    webhook_url: str = Field(..., min_length=1)
    enabled: bool = True


class UpdateDiscordAnnouncementChannelArgs(_ArgsBase):
    channel_id: int = Field(..., ge=1)
    announcement_type: str | None = None
    channel_name: str | None = Field(default=None, max_length=255)
    webhook_url: str | None = None
    enabled: bool | None = None


def _announcement_queryset(user, *, include_scheduled: bool = False):
    if not can_view_announcements(user):
        raise PermissionDenied("You do not have permission to view announcements.")
    qs = Announcement.objects.select_related("author__user")
    if can_manage_announcements(user) or can_schedule_announcements(user):
        if not include_scheduled:
            qs = qs.filter(
                Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now())
            )
        return qs
    return qs.filter(Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now()))


def _get_visible_announcement(user, announcement_id: int) -> Announcement:
    announcement = (
        _announcement_queryset(user, include_scheduled=True)
        .filter(pk=announcement_id)
        .first()
    )
    if announcement is None:
        raise ValidationError({"announcement_id": "Announcement not found."})
    return announcement


def _require_announcement_manager(user) -> None:
    if not can_manage_announcements(user):
        raise PermissionDenied("Announcement manager permission required.")


def _announcement_request(user):
    return SimpleNamespace(user=user)


def _save_announcement_from_payload(
    *, user, payload: dict[str, Any], instance: Announcement | None = None
) -> dict[str, Any]:
    _require_announcement_manager(user)
    scheduled_at = payload.get("scheduled_at")
    if scheduled_at and not can_schedule_announcements(user):
        raise PermissionDenied("Scheduling announcements requires permission.")
    serializer = AnnouncementWriteSerializer(
        instance, data=payload, partial=instance is not None
    )
    serializer.is_valid(raise_exception=True)
    was_published = announcement_is_published(instance) if instance else False
    profile = require_profile(user)
    with transaction.atomic():
        announcement = (
            serializer.save(author=profile) if instance is None else serializer.save()
        )
        should_notify = (
            not was_published
            and announcement_is_published(announcement)
            and payload.get("send_email_notifications", False)
        )
        if should_notify:
            notify_announcement_published(announcement, send_email=True)
    return {
        "announcement": AnnouncementDetailSerializer(
            announcement, context={"request": _announcement_request(user)}
        ).data,
        "summary": f"{'Created' if instance is None else 'Updated'} announcement `{announcement.title}`.",
    }


def list_announcements(
    *,
    user,
    query: str = "",
    type: str | None = None,
    include_scheduled: bool = False,
    limit: int | None = 20,
) -> dict[str, Any]:
    qs = _announcement_queryset(user, include_scheduled=include_scheduled)
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(body__icontains=query))
    if type:
        qs = qs.filter(type=type)
    qs = qs.order_by("-published_at", "-created_at")[: _limit(limit, 20, 100)]
    return {
        "announcements": AnnouncementListSerializer(
            qs, many=True, context={"request": _announcement_request(user)}
        ).data,
        "summary": "Loaded announcements.",
    }


def get_announcement(*, user, announcement_id: int) -> dict[str, Any]:
    announcement = _get_visible_announcement(user, announcement_id)
    return {
        "announcement": AnnouncementDetailSerializer(
            announcement, context={"request": _announcement_request(user)}
        ).data,
        "summary": f"Loaded announcement `{announcement.title}`.",
    }


def create_announcement(
    *,
    user,
    title: str,
    body: str,
    type: str = "",
    scheduled_at: str | None = None,
    send_email_notifications: bool = False,
) -> dict[str, Any]:
    return _save_announcement_from_payload(
        user=user,
        payload={
            "title": title,
            "body": body,
            "type": type,
            "scheduled_at": scheduled_at,
            "send_email_notifications": send_email_notifications,
        },
    )


def update_announcement(
    *,
    user,
    announcement_id: int,
    title: str | None = None,
    body: str | None = None,
    type: str | None = None,
    scheduled_at: str | None = None,
    send_email_notifications: bool = False,
) -> dict[str, Any]:
    announcement = Announcement.objects.filter(pk=announcement_id).first()
    if announcement is None:
        raise ValidationError({"announcement_id": "Announcement not found."})
    payload = {
        k: v
        for k, v in {
            "title": title,
            "body": body,
            "type": type,
            "scheduled_at": scheduled_at,
            "send_email_notifications": send_email_notifications,
        }.items()
        if v is not None
    }
    return _save_announcement_from_payload(
        user=user, payload=payload, instance=announcement
    )


def delete_announcement(*, user, announcement_id: int) -> dict[str, Any]:
    _require_announcement_manager(user)
    announcement = Announcement.objects.filter(pk=announcement_id).first()
    if announcement is None:
        raise ValidationError({"announcement_id": "Announcement not found."})
    title = announcement.title
    with transaction.atomic():
        announcement.delete()
    return {"summary": f"Deleted announcement `{title}`."}


def list_announcement_comments(*, user, announcement_id: int) -> dict[str, Any]:
    announcement = _get_visible_announcement(user, announcement_id)
    comments = announcement.comments.select_related("author__user").filter(
        deleted_at__isnull=True
    )
    return {
        "comments": AnnouncementCommentSerializer(comments, many=True).data,
        "summary": f"Loaded {comments.count()} comment(s).",
    }


def add_announcement_comment(
    *, user, announcement_id: int, body: str
) -> dict[str, Any]:
    announcement = _get_visible_announcement(user, announcement_id)
    profile = require_profile(user)
    serializer = AnnouncementCommentSerializer(
        AnnouncementComment.objects.create(
            announcement=announcement, author=profile, body=body.strip()
        )
    )
    return {"comment": serializer.data, "summary": "Added announcement comment."}


def delete_announcement_comment(
    *, user, announcement_id: int, comment_id: int
) -> dict[str, Any]:
    announcement = _get_visible_announcement(user, announcement_id)
    profile = require_profile(user)
    comment = announcement.comments.filter(
        pk=comment_id, deleted_at__isnull=True
    ).first()
    if comment is None:
        raise ValidationError({"comment_id": "Comment not found."})
    if not (
        comment.author_id == profile.id
        or announcement.author_id == profile.id
        or can_moderate_announcement_comments(user)
    ):
        raise PermissionDenied("You do not have permission to delete this comment.")
    comment.deleted_at = timezone.now()
    comment.save(update_fields=["deleted_at", "updated_at"])
    return {"summary": f"Deleted comment #{comment_id}."}


def list_announcement_reactions(*, user, announcement_id: int) -> dict[str, Any]:
    announcement = _get_visible_announcement(user, announcement_id)
    reactions = announcement.reactions.select_related("user__user")
    return {
        "reactions": AnnouncementReactionSerializer(reactions, many=True).data,
        "summary": f"Loaded {reactions.count()} reaction(s).",
    }


def toggle_announcement_reaction(
    *, user, announcement_id: int, reaction_type: str
) -> dict[str, Any]:
    if not can_add_announcement_reactions(user):
        raise PermissionDenied("Adding reactions requires add_reactions permission.")
    announcement = _get_visible_announcement(user, announcement_id)
    profile = require_profile(user)
    reaction_type = reaction_type.strip().lower()
    reaction = AnnouncementReaction.objects.filter(
        announcement=announcement, user=profile, reaction_type=reaction_type
    ).first()
    if reaction:
        reaction.delete()
        return {
            "active": False,
            "reaction_type": reaction_type,
            "summary": "Removed reaction.",
        }
    reaction = AnnouncementReaction.objects.create(
        announcement=announcement, user=profile, reaction_type=reaction_type
    )
    return {
        "active": True,
        "reaction": AnnouncementReactionSerializer(reaction).data,
        "summary": "Added reaction.",
    }


def list_discord_announcement_channels(
    *,
    user,
    announcement_type: str | None = None,
    enabled: bool | None = None,
    limit: int | None = 50,
) -> dict[str, Any]:
    _require_announcement_manager(user)
    qs = DiscordAnnouncementChannel.objects.all()
    if announcement_type:
        qs = qs.filter(announcement_type=announcement_type)
    if enabled is not None:
        qs = qs.filter(enabled=enabled)
    qs = qs.order_by("announcement_type", "channel_name")[: _limit(limit, 50, 100)]
    return {
        "channels": DiscordAnnouncementChannelSerializer(qs, many=True).data,
        "summary": "Loaded Discord announcement channels.",
    }


def create_discord_announcement_channel(
    *,
    user,
    announcement_type: str,
    channel_name: str,
    webhook_url: str,
    enabled: bool = True,
) -> dict[str, Any]:
    _require_announcement_manager(user)
    serializer = DiscordAnnouncementChannelSerializer(
        data={
            "announcement_type": announcement_type,
            "channel_name": channel_name,
            "webhook_url": webhook_url,
            "enabled": enabled,
        }
    )
    serializer.is_valid(raise_exception=True)
    channel = serializer.save()
    return {
        "channel": DiscordAnnouncementChannelSerializer(channel).data,
        "summary": f"Created Discord channel `{channel.channel_name}`.",
    }


def update_discord_announcement_channel(
    *,
    user,
    channel_id: int,
    announcement_type: str | None = None,
    channel_name: str | None = None,
    webhook_url: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    _require_announcement_manager(user)
    channel = DiscordAnnouncementChannel.objects.filter(pk=channel_id).first()
    if channel is None:
        raise ValidationError({"channel_id": "Discord announcement channel not found."})
    payload = {
        k: v
        for k, v in {
            "announcement_type": announcement_type,
            "channel_name": channel_name,
            "webhook_url": webhook_url,
            "enabled": enabled,
        }.items()
        if v is not None
    }
    serializer = DiscordAnnouncementChannelSerializer(
        channel, data=payload, partial=True
    )
    serializer.is_valid(raise_exception=True)
    channel = serializer.save()
    return {
        "channel": DiscordAnnouncementChannelSerializer(channel).data,
        "summary": f"Updated Discord channel `{channel.channel_name}`.",
    }


# ============================================================================
# CELEBRATIONS MODULE
# ============================================================================


class ListUpcomingCelebrationsArgs(_ArgsBase):
    days: int = Field(default=30, ge=1, le=365)
    type: str = Field(default="all", pattern="^(all|birthday|anniversary)$")


def list_upcoming_celebrations(
    *, user, days: int = 30, type: str = "all"
) -> dict[str, Any]:
    allowed_types: set[str] = set()
    if can_view_birthdays(user):
        allowed_types.add("birthday")
    if can_view_anniversaries(user):
        allowed_types.add("anniversary")
    if not allowed_types:
        raise PermissionDenied("Viewing celebrations requires permission.")
    if type != "all" and type not in allowed_types:
        raise PermissionDenied("Viewing this celebration type requires permission.")
    event_types = allowed_types if type == "all" else {type}
    events = build_upcoming_profile_celebrations(days=days, event_types=event_types)
    return {
        "celebrations": UpcomingCelebrationSerializer(events, many=True).data,
        "summary": f"Loaded {len(events)} upcoming celebration(s).",
    }


# ============================================================================
# LEAVE ANALYTICS MODULE
# ============================================================================


class LeaveAnalyticsYearArgs(_ArgsBase):
    year: int | None = Field(default=None, ge=2000, le=2100)
    department: str | None = None
    month: int | None = Field(default=None, ge=1, le=12)


class LeaveAnalyticsMonthlyArgs(LeaveAnalyticsYearArgs):
    leave_type: str | None = None


class LeaveEmployeeHistoryArgs(_ArgsBase):
    employee_id: int | None = Field(default=None, ge=1)
    year_from: int | None = Field(default=None, ge=2000, le=2100)
    year_to: int | None = Field(default=None, ge=2000, le=2100)
    leave_type: str | None = None


class LeaveAvailabilityArgs(_ArgsBase):
    start_date: str = Field(..., min_length=8)
    end_date: str = Field(..., min_length=8)
    project_id: int | None = Field(default=None, ge=1)
    leave_types: list[str] | None = None
    statuses: list[str] | None = None


class RefreshLeaveAnalyticsArgs(_ArgsBase):
    year_from: int | None = Field(default=None, ge=2000, le=2100)
    year_to: int | None = Field(default=None, ge=2000, le=2100)


def _require_leave_analytics_access(user) -> UserProfile:
    profile = require_profile(user)
    if not (
        has_leave_analytics_view_permission(user)
        or has_own_leave_history_permission(user)
    ):
        raise PermissionDenied("Leave analytics permission required.")
    return profile


def _leave_scoped_aggregates(user):
    qs = LeaveMonthlyAggregate.objects.select_related("employee__user")
    if has_leave_analytics_view_permission(user):
        return qs
    return qs.filter(employee=require_profile(user))


def get_leave_analytics_monthly(
    *,
    user,
    year: int | None = None,
    department: str | None = None,
    month: int | None = None,
    leave_type: str | None = None,
) -> dict[str, Any]:
    _require_leave_analytics_access(user)
    year = year or timezone.now().year
    qs = _leave_scoped_aggregates(user).filter(year=year)
    if department:
        qs = qs.filter(employee__department=department)
    if month:
        qs = qs.filter(month=month)
    if leave_type:
        qs = qs.filter(leave_type=leave_type)
    rows = list(
        qs.values("month", "leave_type")
        .annotate(total=Sum("approved_days"), pending=Sum("pending_days"))
        .order_by("month", "leave_type")
    )
    return {
        "rows": rows,
        "summary": f"Loaded {len(rows)} monthly leave analytics row(s).",
    }


def get_leave_analytics_yearly_totals(
    *,
    user,
    year: int | None = None,
    department: str | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    _require_leave_analytics_access(user)
    year = year or timezone.now().year
    if has_leave_analytics_view_permission(user):
        totals = yearly_totals_by_type(year, department=department, month=month)
    else:
        qs = _leave_scoped_aggregates(user).filter(year=year)
        if month:
            qs = qs.filter(month=month)
        totals = {
            row["leave_type"]: row["total"] or 0
            for row in qs.values("leave_type").annotate(total=Sum("approved_days"))
        }
    return {
        "year": year,
        "total": sum(totals.values()),
        "by_type": totals,
        "summary": "Loaded yearly leave totals.",
    }


def get_leave_analytics_departments(
    *,
    user,
    year: int | None = None,
    month: int | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    if not has_leave_analytics_view_permission(user):
        raise PermissionDenied("Department-level leave analytics permission required.")
    year = year or timezone.now().year
    qs = LeaveMonthlyAggregate.objects.select_related("employee").filter(year=year)
    if month:
        qs = qs.filter(month=month)
    if department:
        qs = qs.filter(employee__department=department)
    rows = list(
        qs.values("employee__department", "leave_type")
        .annotate(total=Sum("approved_days"))
        .order_by("employee__department", "leave_type")
    )
    return {"rows": rows, "summary": f"Loaded {len(rows)} department analytics row(s)."}


def get_leave_analytics_employees(
    *,
    user,
    year: int | None = None,
    department: str | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    _require_leave_analytics_access(user)
    year = year or timezone.now().year
    qs = _leave_scoped_aggregates(user).filter(year=year)
    if department:
        qs = qs.filter(employee__department=department)
    if month:
        qs = qs.filter(month=month)
    rows = list(
        qs.values("employee_id", "employee__full_name", "leave_type")
        .annotate(total=Sum("approved_days"), pending=Sum("pending_days"))
        .order_by("employee__full_name", "leave_type")
    )
    return {"rows": rows, "summary": f"Loaded {len(rows)} employee analytics row(s)."}


def get_leave_employee_history(
    *,
    user,
    employee_id: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    leave_type: str | None = None,
) -> dict[str, Any]:
    profile = _require_leave_analytics_access(user)
    target_id = employee_id or profile.id
    if target_id != profile.id and not has_leave_analytics_view_permission(user):
        raise PermissionDenied("You can only view your own leave history.")
    employee = UserProfile.objects.select_related("user").filter(pk=target_id).first()
    if employee is None:
        raise ValidationError({"employee_id": "Employee not found."})
    payload = build_leave_employee_history(
        employee, year_from=year_from, year_to=year_to, leave_type=leave_type
    )
    return {
        "employee_id": employee.id,
        "employee_name": employee.full_name or employee.user.username,
        **payload,
        "summary": "Loaded leave employee history.",
    }


def get_leave_availability(
    *,
    user,
    start_date: str,
    end_date: str,
    project_id: int | None = None,
    leave_types: list[str] | None = None,
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    profile = _require_leave_analytics_access(user)
    start = parse_date(start_date)
    end = parse_date(end_date)
    if start is None or end is None or end < start:
        raise ValidationError({"date": "Provide valid start_date/end_date ISO dates."})
    project = Project.objects.filter(pk=project_id).first() if project_id else None
    if project_id and project is None:
        raise ValidationError({"project_id": "Project not found."})
    fallback_employees = None
    if not has_leave_analytics_view_permission(user):
        fallback_employees = [profile]
        project = None
    payload = team_availability(
        start_date=start,
        end_date=end,
        project=project,
        employees=fallback_employees,
        leave_types=leave_types,
        statuses=statuses,
    )
    return {**payload, "summary": "Loaded leave availability."}


def refresh_leave_analytics(
    *, user, year_from: int | None = None, year_to: int | None = None
) -> dict[str, Any]:
    if not has_leave_analytics_refresh_permission(user):
        raise PermissionDenied("Leave analytics refresh permission required.")
    year_range = None
    if year_from is not None or year_to is not None:
        if year_from is None or year_to is None or year_from > year_to:
            raise ValidationError("Pass valid year_from and year_to together.")
        year_range = (year_from, year_to)
    agg_stats = materialize_leave_monthly_aggregates(year_range=year_range)
    snap_stats = snapshot_leave_balances()
    return {
        **agg_stats,
        "snapshots": snap_stats,
        "summary": "Refreshed leave analytics.",
    }


# ============================================================================
# ASSET MAINTENANCE MODULE EXTENSIONS
# ============================================================================


class ListScheduledMaintenanceArgs(_ArgsBase):
    asset_id: int | None = Field(default=None, ge=1)
    owner_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    maintenance_type: str | None = None
    due_from: str | None = None
    due_to: str | None = None
    limit: int | None = Field(default=20, ge=1, le=100)


class CreateScheduledMaintenanceArgs(_ArgsBase):
    asset_id: int = Field(..., ge=1)
    due_date: str = Field(..., min_length=8)
    reason: str = Field(..., min_length=1)
    maintenance_type: str = Field(..., min_length=1)
    owner_id: int | None = Field(default=None, ge=1)
    estimated_cost: str | None = None
    vendor: str = ""


class UpdateScheduledMaintenanceArgs(CreateScheduledMaintenanceArgs):
    maintenance_id: int = Field(..., ge=1)
    asset_id: int | None = Field(default=None, ge=1)
    due_date: str | None = None
    reason: str | None = None
    maintenance_type: str | None = None


class CompleteScheduledMaintenanceArgs(_ArgsBase):
    maintenance_id: int = Field(..., ge=1)
    date: str = Field(..., min_length=8)
    reason: str = Field(..., min_length=1)
    cost: str | None = None
    asset_status_after: str | None = None
    asset_condition_after: str | None = None
    replacement_asset_id: int | None = Field(default=None, ge=1)


class CancelScheduledMaintenanceArgs(_ArgsBase):
    maintenance_id: int = Field(..., ge=1)
    cancelled_reason: str = ""


def _require_asset_history_write(user) -> None:
    if not has_asset_permission(user, "log_asset_replacement"):
        raise PermissionDenied("Asset history write permission required.")


def list_scheduled_maintenance(
    *,
    user,
    asset_id: int | None = None,
    owner_id: int | None = None,
    status: str | None = None,
    maintenance_type: str | None = None,
    due_from: str | None = None,
    due_to: str | None = None,
    limit: int | None = 20,
) -> dict[str, Any]:
    if not can_view_asset_maintenance_logs(user):
        raise PermissionDenied("Asset maintenance history permission required.")
    qs = ScheduledMaintenance.objects.select_related("asset", "owner__user")
    if asset_id:
        qs = qs.filter(asset_id=asset_id)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if status:
        qs = qs.filter(status=status)
    if maintenance_type:
        qs = qs.filter(maintenance_type=maintenance_type)
    if due_from:
        qs = qs.filter(due_date__gte=due_from)
    if due_to:
        qs = qs.filter(due_date__lte=due_to)
    qs = qs.order_by("due_date", "id")[: _limit(limit, 20, 100)]
    return {
        "scheduled_maintenance": ScheduledMaintenanceSerializer(qs, many=True).data,
        "summary": "Loaded scheduled maintenance.",
    }


def create_scheduled_maintenance(
    *,
    user,
    asset_id: int,
    due_date: str,
    reason: str,
    maintenance_type: str,
    owner_id: int | None = None,
    estimated_cost: str | None = None,
    vendor: str = "",
) -> dict[str, Any]:
    _require_asset_history_write(user)
    payload = {
        "asset": asset_id,
        "due_date": due_date,
        "reason": reason,
        "maintenance_type": maintenance_type,
        "owner": owner_id,
        "estimated_cost": estimated_cost,
        "vendor": vendor,
    }
    serializer = ScheduledMaintenanceSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    schedule = serializer.save(created_by=require_profile(user))
    return {
        "scheduled_maintenance": ScheduledMaintenanceSerializer(schedule).data,
        "summary": f"Created scheduled maintenance #{schedule.id}.",
    }


def update_scheduled_maintenance(
    *,
    user,
    maintenance_id: int,
    asset_id: int | None = None,
    due_date: str | None = None,
    reason: str | None = None,
    maintenance_type: str | None = None,
    owner_id: int | None = None,
    estimated_cost: str | None = None,
    vendor: str = "",
) -> dict[str, Any]:
    _require_asset_history_write(user)
    schedule = ScheduledMaintenance.objects.filter(pk=maintenance_id).first()
    if schedule is None:
        raise ValidationError({"maintenance_id": "Scheduled maintenance not found."})
    payload = {
        k: v
        for k, v in {
            "asset": asset_id,
            "due_date": due_date,
            "reason": reason,
            "maintenance_type": maintenance_type,
            "owner": owner_id,
            "estimated_cost": estimated_cost,
            "vendor": vendor,
        }.items()
        if v is not None
    }
    serializer = ScheduledMaintenanceSerializer(schedule, data=payload, partial=True)
    serializer.is_valid(raise_exception=True)
    schedule = serializer.save()
    return {
        "scheduled_maintenance": ScheduledMaintenanceSerializer(schedule).data,
        "summary": f"Updated scheduled maintenance #{schedule.id}.",
    }


def complete_scheduled_maintenance(
    *,
    user,
    maintenance_id: int,
    date: str,
    reason: str,
    cost: str | None = None,
    asset_status_after: str | None = None,
    asset_condition_after: str | None = None,
    replacement_asset_id: int | None = None,
) -> dict[str, Any]:
    _require_asset_history_write(user)
    schedule = (
        ScheduledMaintenance.objects.select_related("asset")
        .filter(pk=maintenance_id)
        .first()
    )
    if schedule is None:
        raise ValidationError({"maintenance_id": "Scheduled maintenance not found."})
    payload = {
        "date": date,
        "reason": reason,
        "cost": cost,
        "asset_status_after": asset_status_after,
        "asset_condition_after": asset_condition_after,
        "replacement_asset": replacement_asset_id,
    }
    serializer = ScheduledMaintenanceCompleteSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    with transaction.atomic():
        replacement_log = ReplacementLog.objects.create(
            asset=schedule.asset,
            reason=data["reason"],
            date=data["date"],
            asset_status_before=schedule.asset.status,
            asset_status_after=data.get("asset_status_after"),
            asset_condition_before=schedule.asset.condition,
            asset_condition_after=data.get("asset_condition_after"),
            replacement_asset=data.get("replacement_asset"),
            cost=data.get("cost"),
            replaced_by=require_profile(user),
        )
        if data.get("asset_status_after"):
            schedule.asset.status = data["asset_status_after"]
        if data.get("asset_condition_after"):
            schedule.asset.condition = data["asset_condition_after"]
        schedule.asset.save()
        schedule.status = ScheduledMaintenance.Status.COMPLETED
        schedule.completed_log = replacement_log
        schedule.save(update_fields=["status", "completed_log", "updated_at"])
    return {
        "scheduled_maintenance": ScheduledMaintenanceSerializer(schedule).data,
        "summary": f"Completed scheduled maintenance #{schedule.id}.",
    }


def cancel_scheduled_maintenance(
    *, user, maintenance_id: int, cancelled_reason: str = ""
) -> dict[str, Any]:
    _require_asset_history_write(user)
    schedule = ScheduledMaintenance.objects.filter(pk=maintenance_id).first()
    if schedule is None:
        raise ValidationError({"maintenance_id": "Scheduled maintenance not found."})
    serializer = ScheduledMaintenanceCancelSerializer(
        data={"cancelled_reason": cancelled_reason}
    )
    serializer.is_valid(raise_exception=True)
    schedule.status = ScheduledMaintenance.Status.CANCELLED
    schedule.cancelled_reason = serializer.validated_data.get("cancelled_reason", "")
    schedule.save(update_fields=["status", "cancelled_reason", "updated_at"])
    return {
        "scheduled_maintenance": ScheduledMaintenanceSerializer(schedule).data,
        "summary": f"Cancelled scheduled maintenance #{schedule.id}.",
    }


# ============================================================================
# DOCUMENT TEMPLATE EXTENSIONS
# ============================================================================


class GetDocumentTemplateArgs(_ArgsBase):
    template_id: int = Field(..., ge=1)


class CreateDocumentTemplateArgs(_ArgsBase):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    category: str = "other"
    content: str = ""
    visibility: str = TemplateVisibility.PRIVATE.value
    status: str = "draft"
    fields: list[dict[str, Any]] = Field(default_factory=list)


class UpdateDocumentTemplateArgs(CreateDocumentTemplateArgs):
    template_id: int = Field(..., ge=1)
    name: str | None = Field(default=None, max_length=255)
    category: str | None = None
    visibility: str | None = None
    status: str | None = None
    replace_fields: bool = False


class UseDocumentTemplateArgs(_ArgsBase):
    template_id: int = Field(..., ge=1)
    field_values: dict[str, Any] = Field(default_factory=dict)
    document_name: str = ""


class ListUserTemplateSnippetsArgs(_ArgsBase):
    limit: int | None = Field(default=50, ge=1, le=100)


class CreateUserTemplateSnippetArgs(_ArgsBase):
    label: str = Field(..., min_length=1, max_length=255)
    html: str = Field(..., min_length=1)
    sort_order: int = Field(default=0, ge=0)


def _template_visible_queryset(user):
    profile = require_profile(user)
    qs = (
        DocumentTemplate.objects.filter(is_active=True)
        .select_related("created_by__user")
        .prefetch_related("fields")
    )
    if (
        is_hr_admin(user)
        or getattr(user, "is_staff", False)
        or getattr(user, "is_superuser", False)
    ):
        return qs
    return qs.filter(Q(visibility=TemplateVisibility.SHARED) | Q(created_by=profile))


def _get_visible_template(user, template_id: int) -> DocumentTemplate:
    template = _template_visible_queryset(user).filter(pk=template_id).first()
    if template is None:
        raise ValidationError({"template_id": "Document template not found."})
    return template


def _can_edit_template(user, template: DocumentTemplate) -> bool:
    profile = getattr(user, "profile", None)
    return bool(
        is_hr_admin(user)
        or getattr(user, "is_staff", False)
        or getattr(user, "is_superuser", False)
        or (profile is not None and template.created_by_id == profile.id)
    )


def get_document_template(*, user, template_id: int) -> dict[str, Any]:
    template = _get_visible_template(user, template_id)
    return {
        "template": DocumentTemplateDetailSerializer(template).data,
        "summary": f"Loaded document template `{template.name}`.",
    }


def create_document_template(
    *,
    user,
    name: str,
    description: str = "",
    category: str = "other",
    content: str = "",
    visibility: str = TemplateVisibility.PRIVATE.value,
    status: str = "draft",
    fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile = require_profile(user)
    payload = {
        "name": name,
        "description": description,
        "category": category,
        "content": content,
        "visibility": visibility,
        "status": status,
        "fields": fields or [],
    }
    serializer = DocumentTemplateCreateUpdateSerializer(
        data=payload, context={"instance": None}
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    fields_data = data.pop("fields", [])
    with transaction.atomic():
        template = DocumentTemplate.objects.create(
            **data,
            is_system_template=False,
            is_active=True,
            created_by=profile,
        )
        for field_data in fields_data:
            TemplateField.objects.create(template=template, **field_data)
    return {
        "template": DocumentTemplateDetailSerializer(template).data,
        "summary": f"Created document template `{template.name}`.",
    }


def update_document_template(
    *,
    user,
    template_id: int,
    name: str | None = None,
    description: str = "",
    category: str | None = None,
    content: str = "",
    visibility: str | None = None,
    status: str | None = None,
    fields: list[dict[str, Any]] | None = None,
    replace_fields: bool = False,
) -> dict[str, Any]:
    template = _get_visible_template(user, template_id)
    if template.is_system_template or not _can_edit_template(user, template):
        raise PermissionDenied("You do not have permission to edit this template.")
    payload = {
        k: v
        for k, v in {
            "name": name,
            "description": description,
            "category": category,
            "content": content,
            "visibility": visibility,
            "status": status,
        }.items()
        if v is not None
    }
    if replace_fields:
        payload["fields"] = fields or []
    serializer = DocumentTemplatePartialUpdateSerializer(
        data=payload, context={"instance": template}
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    fields_data = data.pop("fields", None)
    with transaction.atomic():
        for attr, value in data.items():
            setattr(template, attr, value)
        template.save()
        if fields_data is not None:
            template.fields.all().delete()
            for field_data in fields_data:
                TemplateField.objects.create(template=template, **field_data)
    return {
        "template": DocumentTemplateDetailSerializer(template).data,
        "summary": f"Updated document template `{template.name}`.",
    }


def duplicate_document_template(*, user, template_id: int) -> dict[str, Any]:
    template = _get_visible_template(user, template_id)
    new_template = clone_template(template, require_profile(user))
    return {
        "template": DocumentTemplateDetailSerializer(new_template).data,
        "summary": f"Duplicated document template `{template.name}`.",
    }


def deactivate_document_template(*, user, template_id: int) -> dict[str, Any]:
    template = _get_visible_template(user, template_id)
    if template.is_system_template or not _can_edit_template(user, template):
        raise PermissionDenied("You do not have permission to delete this template.")
    template.is_active = False
    template.save(update_fields=["is_active", "updated_at"])
    return {"summary": f"Deactivated document template `{template.name}`."}


def use_document_template(
    *,
    user,
    template_id: int,
    field_values: dict[str, Any] | None = None,
    document_name: str = "",
) -> dict[str, Any]:
    template = _get_visible_template(user, template_id)
    field_values = field_values or {}
    missing = validate_template_fields(template.fields.all(), field_values)
    if missing:
        raise ValidationError({"missing_fields": missing})
    generated_doc = TemplateGeneratedDocument.objects.create(
        name=document_name or f"{template.name} generated",
        source_template=template,
        resolved_content=resolve_template_content(template.content, field_values),
        field_values=field_values,
        created_by=require_profile(user),
    )
    return {
        "generated_document": TemplateGeneratedDocumentSerializer(generated_doc).data,
        "summary": f"Generated document from `{template.name}`.",
    }


def list_document_category_defaults(*, user) -> dict[str, Any]:
    require_profile(user)
    return {
        "category_defaults": get_document_category_defaults(),
        "summary": "Loaded document category defaults.",
    }


def list_user_template_snippets(*, user, limit: int | None = 50) -> dict[str, Any]:
    profile = require_profile(user)
    qs = UserTemplateSnippet.objects.filter(user_profile=profile).order_by(
        "sort_order", "label"
    )[: _limit(limit, 50, 100)]
    return {
        "snippets": UserTemplateSnippetSerializer(qs, many=True).data,
        "summary": "Loaded user template snippets.",
    }


def create_user_template_snippet(
    *, user, label: str, html: str, sort_order: int = 0
) -> dict[str, Any]:
    serializer = UserTemplateSnippetSerializer(
        data={"label": label, "html": html, "sort_order": sort_order}
    )
    serializer.is_valid(raise_exception=True)
    snippet = serializer.save(user_profile=require_profile(user))
    return {
        "snippet": UserTemplateSnippetSerializer(snippet).data,
        "summary": f"Created template snippet `{snippet.label}`.",
    }


# ============================================================================
# TIME TRACKING REPORT EXTENSIONS
# ============================================================================


class TimeWeeklySummaryArgs(_ArgsBase):
    week_start: str = Field(..., min_length=8)
    employee_id: int | None = Field(default=None, ge=1)


class TimeDateArgs(_ArgsBase):
    work_date: str | None = None
    employee_id: int | None = Field(default=None, ge=1)


class TimeDashboardArgs(_ArgsBase):
    week_start: str | None = None
    employee_id: int | None = Field(default=None, ge=1)


class TimePlannedVsActualArgs(_ArgsBase):
    date_from: str | None = None
    date_to: str | None = None
    week_start: str | None = None
    employee_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)


class ExportTimeEntriesArgs(_ArgsBase):
    date_from: str | None = None
    date_to: str | None = None
    employee_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)
    status: str | None = None
    limit: int | None = Field(default=100, ge=1, le=500)


def _time_employee_for(user, employee_id: int | None = None) -> UserProfile:
    employee = (
        UserProfile.objects.select_related("user").filter(pk=employee_id).first()
        if employee_id
        else profile_for_user(user)
    )
    if employee is None:
        raise ValidationError({"employee_id": "Employee not found."})
    if not can_view_employee_timesheet(user, employee):
        raise PermissionDenied("You do not have permission to view this timesheet.")
    return employee


def _scoped_time_entries_for_ai(user):
    base = TimeEntry.objects.select_related("employee__user", "project", "task")
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return base
    profile = profile_for_user(user)
    if profile is None:
        return TimeEntry.objects.none()
    scopes = Q()
    if has_time_tracking_permission(user, "view_own_timesheet"):
        scopes |= Q(employee=profile)
    if has_time_tracking_permission(
        user, "view_team_timesheets"
    ) or has_time_tracking_permission(user, "approve_team_timesheets"):
        scopes |= Q(employee__managers=profile)
    if has_time_tracking_permission(user, "view_dept_timesheets"):
        return base
    return base.filter(scopes).distinct() if scopes else base.none()


def get_time_weekly_summary(
    *, user, week_start: str, employee_id: int | None = None
) -> dict[str, Any]:
    start = parse_date(week_start)
    if start is None:
        raise ValidationError({"week_start": "week_start must be YYYY-MM-DD."})
    employee = _time_employee_for(user, employee_id)
    return {
        **weekly_allocation_summary(employee=employee, week_start=start),
        "summary": "Loaded weekly summary.",
    }


def get_time_active_allocations(
    *, user, work_date: str | None = None, employee_id: int | None = None
) -> dict[str, Any]:
    target_date = parse_date(work_date) if work_date else timezone.localdate()
    if target_date is None:
        raise ValidationError({"work_date": "work_date must be YYYY-MM-DD."})
    employee = _time_employee_for(user, employee_id)
    return {
        "allocations": active_time_tracking_allocations(
            employee=employee, work_date=target_date
        ),
        "summary": "Loaded active allocations.",
    }


def get_time_weekly_dashboard(
    *, user, week_start: str | None = None, employee_id: int | None = None
) -> dict[str, Any]:
    start = (
        parse_date(week_start)
        if week_start
        else timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    )
    if start is None:
        raise ValidationError({"week_start": "week_start must be YYYY-MM-DD."})
    end = start + timedelta(days=6)
    qs = _scoped_time_entries_for_ai(user).filter(
        work_date__gte=start, work_date__lte=end
    )
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    totals_by_source = {
        row["source_type"]: str(row["total_hours"] or Decimal("0.00"))
        for row in qs.values("source_type").annotate(total_hours=Sum("hours"))
    }
    totals_by_status = {
        row["status"]: str(row["total_hours"] or Decimal("0.00"))
        for row in qs.values("status").annotate(total_hours=Sum("hours"))
    }
    total_hours = qs.aggregate(total=Sum("hours"))["total"] or Decimal("0.00")
    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "total_hours": str(total_hours),
        "totals_by_source": totals_by_source,
        "totals_by_status": totals_by_status,
        "entries": TimeEntrySerializer(
            qs.order_by("work_date", "id")[:100], many=True
        ).data,
        "summary": "Loaded weekly dashboard.",
    }


def get_time_planned_vs_actual(
    *,
    user,
    date_from: str | None = None,
    date_to: str | None = None,
    week_start: str | None = None,
    employee_id: int | None = None,
    project_id: int | None = None,
) -> dict[str, Any]:
    if date_from and date_to:
        start = parse_date(date_from)
        end = parse_date(date_to)
    else:
        start = (
            parse_date(week_start)
            if week_start
            else timezone.localdate() - timedelta(days=timezone.localdate().weekday())
        )
        end = start + timedelta(days=6) if start else None
    if start is None or end is None or end < start:
        raise ValidationError(
            {"date": "Provide valid date_from/date_to or week_start."}
        )
    employees = UserProfile.objects.filter(
        id__in=_scoped_time_entries_for_ai(user)
        .filter(work_date__gte=start, work_date__lte=end)
        .values_list("employee_id", flat=True)
        .distinct()
    ).select_related("user")
    if employee_id:
        employees = employees.filter(id=employee_id)
    rows: list[dict[str, Any]] = []
    current = start - timedelta(days=start.weekday())
    final_week = end - timedelta(days=end.weekday())
    while current <= final_week:
        for employee in employees:
            if not can_view_employee_timesheet(user, employee):
                continue
            summary = weekly_allocation_summary(employee=employee, week_start=current)
            for project in summary["projects"]:
                if project_id and project.get("project_id") != project_id:
                    continue
                rows.append(
                    {
                        "employee_id": employee.id,
                        "employee_name": employee.full_name or employee.user.username,
                        "week_start": summary["week_start"],
                        "week_end": summary["week_end"],
                        **project,
                    }
                )
        current += timedelta(days=7)
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "rows": rows,
        "summary": "Loaded planned-vs-actual.",
    }


def export_time_entries(
    *,
    user,
    date_from: str | None = None,
    date_to: str | None = None,
    employee_id: int | None = None,
    project_id: int | None = None,
    status: str | None = None,
    limit: int | None = 100,
) -> dict[str, Any]:
    if not has_time_tracking_permission(user, "export_timesheets"):
        raise PermissionDenied("You do not have permission to export timesheets.")
    qs = _scoped_time_entries_for_ai(user)
    if date_from:
        qs = qs.filter(work_date__gte=date_from)
    if date_to:
        qs = qs.filter(work_date__lte=date_to)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    if project_id:
        qs = qs.filter(project_id=project_id)
    if status:
        qs = qs.filter(status=status)
    qs = qs.order_by("work_date", "employee__full_name")[: _limit(limit, 100, 500)]
    rows = [
        {
            "id": entry.id,
            "employee_id": entry.employee_id,
            "employee": entry.employee.full_name or entry.employee.user.username,
            "date": entry.work_date.isoformat(),
            "project": entry.project.name if entry.project else "",
            "task": entry.task.name if entry.task else "",
            "hours": str(entry.hours),
            "status": entry.status,
            "source_type": entry.source_type,
        }
        for entry in qs
    ]
    return {"rows": rows, "summary": f"Prepared {len(rows)} export row(s)."}


# ============================================================================
# NOTIFICATIONS EXTENSIONS
# ============================================================================


class MarkNotificationReadArgs(_ArgsBase):
    notification_id: int = Field(..., ge=1)


def mark_notification_read(*, user, notification_id: int) -> dict[str, Any]:
    profile = require_profile(user)
    notification = Notification.objects.filter(
        pk=notification_id, recipient=profile
    ).first()
    if notification is None:
        raise ValidationError({"notification_id": "Notification not found."})
    notification.is_read = True
    notification.read_at = timezone.now()
    notification.save(update_fields=["is_read", "read_at"])
    return {
        "notification": NotificationSerializer(notification).data,
        "summary": f"Marked notification #{notification.id} as read.",
    }


# ============================================================================
# REGISTRATIONS for all new tools
# ============================================================================

# --- Announcements ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "list_announcements",
        "List visible announcements, optionally including scheduled ones for managers.",
        list_announcements,
        ListAnnouncementsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "get_announcement",
        "Get one announcement with body, reactions, and counts.",
        get_announcement,
        GetAnnouncementArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "create_announcement",
        "Create or schedule a rich-text announcement.",
        create_announcement,
        CreateAnnouncementArgs,
        True,
        True,
        None,
        ("Announcements: manage_announcements",),
    ),
    (
        "update_announcement",
        "Update an announcement or change its schedule.",
        update_announcement,
        UpdateAnnouncementArgs,
        True,
        True,
        None,
        ("Announcements: manage_announcements",),
    ),
    (
        "delete_announcement",
        "Delete an announcement.",
        delete_announcement,
        DeleteAnnouncementArgs,
        True,
        True,
        None,
        ("Announcements: manage_announcements",),
    ),
    (
        "list_announcement_comments",
        "List non-deleted comments for an announcement.",
        list_announcement_comments,
        GetAnnouncementArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "add_announcement_comment",
        "Add a comment to an announcement.",
        add_announcement_comment,
        AnnouncementCommentArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "delete_announcement_comment",
        "Soft-delete an announcement comment when author/owner/moderator.",
        delete_announcement_comment,
        DeleteAnnouncementCommentArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "list_announcement_reactions",
        "List reactions for an announcement.",
        list_announcement_reactions,
        GetAnnouncementArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "toggle_announcement_reaction",
        "Toggle the current user's reaction on an announcement.",
        toggle_announcement_reaction,
        ToggleAnnouncementReactionArgs,
        True,
        False,
        None,
        ("Announcements: add_reactions",),
    ),
    (
        "list_discord_announcement_channels",
        "List Discord webhook channels for announcement delivery.",
        list_discord_announcement_channels,
        ListDiscordAnnouncementChannelsArgs,
        False,
        True,
        None,
        ("Announcements: manage_announcements",),
    ),
    (
        "create_discord_announcement_channel",
        "Create a Discord announcement webhook channel.",
        create_discord_announcement_channel,
        CreateDiscordAnnouncementChannelArgs,
        True,
        True,
        None,
        ("Announcements: manage_announcements",),
    ),
    (
        "update_discord_announcement_channel",
        "Update a Discord announcement webhook channel.",
        update_discord_announcement_channel,
        UpdateDiscordAnnouncementChannelArgs,
        True,
        True,
        None,
        ("Announcements: manage_announcements",),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="announcements",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Celebrations ---
registry.register(
    AssistantTool(
        "list_upcoming_celebrations",
        "List upcoming birthdays and work anniversaries.",
        list_upcoming_celebrations,
        module="celebrations",
        args_schema=ListUpcomingCelebrationsArgs,
        required_permissions=("Announcements: view_birthdays/view_anniversaries",),
    )
)

# --- Leave analytics ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _perms in [
    (
        "get_leave_analytics_monthly",
        "Get monthly leave trend rows by leave type.",
        get_leave_analytics_monthly,
        LeaveAnalyticsMonthlyArgs,
        False,
        False,
        ("Leave Analytics: view own/team/org",),
    ),
    (
        "get_leave_analytics_yearly_totals",
        "Get yearly leave totals grouped by leave type.",
        get_leave_analytics_yearly_totals,
        LeaveAnalyticsYearArgs,
        False,
        False,
        ("Leave Analytics: view own/team/org",),
    ),
    (
        "get_leave_analytics_departments",
        "Get department-level leave analytics.",
        get_leave_analytics_departments,
        LeaveAnalyticsYearArgs,
        False,
        True,
        ("Leave Analytics: view_dept_trends",),
    ),
    (
        "get_leave_analytics_employees",
        "Get per-employee leave analytics rows.",
        get_leave_analytics_employees,
        LeaveAnalyticsYearArgs,
        False,
        True,
        ("Leave Analytics: view own/team/org",),
    ),
    (
        "get_leave_employee_history",
        "Get leave history for one employee.",
        get_leave_employee_history,
        LeaveEmployeeHistoryArgs,
        False,
        False,
        ("Leave Analytics: view_own_history/view_dept_trends",),
    ),
    (
        "get_leave_availability",
        "Get day-level team/project leave availability.",
        get_leave_availability,
        LeaveAvailabilityArgs,
        False,
        False,
        ("Leave Analytics: view own/team/org",),
    ),
    (
        "refresh_leave_analytics",
        "Rebuild leave analytics aggregates and snapshots.",
        refresh_leave_analytics,
        RefreshLeaveAnalyticsArgs,
        True,
        True,
        ("Leave Analytics: configure/adjust/override",),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="leave_analytics",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            required_permissions=_perms,
        )
    )

# --- Asset maintenance gaps ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _perms in [
    (
        "list_scheduled_maintenance",
        "List scheduled asset maintenance.",
        list_scheduled_maintenance,
        ListScheduledMaintenanceArgs,
        False,
        False,
        ("Asset Management: view_asset_history",),
    ),
    (
        "create_scheduled_maintenance",
        "Create scheduled maintenance for an asset.",
        create_scheduled_maintenance,
        CreateScheduledMaintenanceArgs,
        True,
        True,
        ("Asset Management: log_asset_replacement",),
    ),
    (
        "update_scheduled_maintenance",
        "Update scheduled asset maintenance.",
        update_scheduled_maintenance,
        UpdateScheduledMaintenanceArgs,
        True,
        True,
        ("Asset Management: log_asset_replacement",),
    ),
    (
        "complete_scheduled_maintenance",
        "Complete scheduled maintenance and create replacement/maintenance log.",
        complete_scheduled_maintenance,
        CompleteScheduledMaintenanceArgs,
        True,
        True,
        ("Asset Management: log_asset_replacement",),
    ),
    (
        "cancel_scheduled_maintenance",
        "Cancel scheduled asset maintenance.",
        cancel_scheduled_maintenance,
        CancelScheduledMaintenanceArgs,
        True,
        True,
        ("Asset Management: log_asset_replacement",),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="assets",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            required_permissions=_perms,
        )
    )

# --- Document template / snippet gaps ---
for _name, _desc, _fn, _schema, _mutating, _sensitive in [
    (
        "get_document_template",
        "Get a visible document template with full content and fields.",
        get_document_template,
        GetDocumentTemplateArgs,
        False,
        False,
    ),
    (
        "create_document_template",
        "Create a document template with optional field definitions.",
        create_document_template,
        CreateDocumentTemplateArgs,
        True,
        False,
    ),
    (
        "update_document_template",
        "Update a document template and optionally replace fields.",
        update_document_template,
        UpdateDocumentTemplateArgs,
        True,
        False,
    ),
    (
        "duplicate_document_template",
        "Duplicate a visible document template into a private copy.",
        duplicate_document_template,
        GetDocumentTemplateArgs,
        True,
        False,
    ),
    (
        "deactivate_document_template",
        "Soft-delete/deactivate a document template.",
        deactivate_document_template,
        GetDocumentTemplateArgs,
        True,
        True,
    ),
    (
        "use_document_template",
        "Generate a document record from a template and field values.",
        use_document_template,
        UseDocumentTemplateArgs,
        True,
        False,
    ),
    (
        "list_document_category_defaults",
        "List default allowed roles by document category.",
        list_document_category_defaults,
        EmptyArgs,
        False,
        False,
    ),
    (
        "list_user_template_snippets",
        "List current user's reusable template snippets.",
        list_user_template_snippets,
        ListUserTemplateSnippetsArgs,
        False,
        False,
    ),
    (
        "create_user_template_snippet",
        "Create a reusable template snippet for the current user.",
        create_user_template_snippet,
        CreateUserTemplateSnippetArgs,
        True,
        False,
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="documents",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
        )
    )

# --- Time tracking report/export gaps ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _perms in [
    (
        "get_time_weekly_summary",
        "Get planned/actual weekly allocation summary for an employee.",
        get_time_weekly_summary,
        TimeWeeklySummaryArgs,
        False,
        False,
        ("Time Tracking: view timesheet",),
    ),
    (
        "get_time_active_allocations",
        "Get active project allocations for an employee on a date.",
        get_time_active_allocations,
        TimeDateArgs,
        False,
        False,
        ("Time Tracking: view timesheet",),
    ),
    (
        "get_time_weekly_dashboard",
        "Get weekly time dashboard totals and entries.",
        get_time_weekly_dashboard,
        TimeDashboardArgs,
        False,
        False,
        ("Time Tracking: view timesheet",),
    ),
    (
        "get_time_planned_vs_actual",
        "Get planned-vs-actual time allocation rows.",
        get_time_planned_vs_actual,
        TimePlannedVsActualArgs,
        False,
        False,
        ("Time Tracking: view timesheet",),
    ),
    (
        "export_time_entries",
        "Return export-ready time entry rows.",
        export_time_entries,
        ExportTimeEntriesArgs,
        False,
        True,
        ("Time Tracking: export_timesheets",),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="time_tracking",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            required_permissions=_perms,
        )
    )

# --- Notification gap ---
registry.register(
    AssistantTool(
        "mark_notification_read",
        "Mark one notification as read.",
        mark_notification_read,
        module="notifications",
        mutating=True,
        args_schema=MarkNotificationReadArgs,
    )
)

# --- Reviews ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "list_reviews",
        "List performance reviews. Employees see own + reviews they conduct; HR sees all.",
        list_reviews,
        ListReviewsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "get_review",
        "Get a single performance review by id.",
        get_review,
        GetReviewArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "schedule_review",
        "Schedule a performance review for an employee. HR or manager.",
        schedule_review,
        ScheduleReviewArgs,
        True,
        True,
        None,
        ("HR or manager",),
    ),
    (
        "add_review_note",
        "Add a note to a review (shared or private visibility).",
        add_review_note,
        AddReviewNoteArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "list_review_notes",
        "List notes for a review. Private notes only visible to author + HR.",
        list_review_notes,
        ListReviewNotesArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "add_review_action_point",
        "Add an action point to a review.",
        add_review_action_point,
        AddReviewActionPointArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "list_review_action_points",
        "List action points (filter by review, owner, status).",
        list_review_action_points,
        ListReviewActionPointsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "update_action_point_status",
        "Update the status / progress of a review action point.",
        update_action_point_status,
        UpdateActionPointStatusArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "close_review",
        "Mark a review as completed. Reviewer or HR.",
        close_review,
        CloseReviewArgs,
        True,
        True,
        None,
        (),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="reviews",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Training ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "list_training_entries",
        "List training entries for an employee.",
        list_training_entries,
        ListTrainingEntriesArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "create_training_entry",
        "Log a new training entry. Self or HR for others.",
        create_training_entry,
        CreateTrainingEntryArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "list_certificates",
        "List certificates for an employee. Filter `expiring_within_days` for renewal reminders.",
        list_certificates,
        ListCertificatesArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "list_peer_sessions",
        "List peer-led learning sessions.",
        list_peer_sessions,
        ListPeerSessionsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "log_peer_session",
        "Log a peer-led learning session you ran.",
        log_peer_session,
        LogPeerSessionArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "get_training_budget",
        "Get the training budget for an employee in a fiscal year.",
        get_training_budget,
        GetTrainingBudgetArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "list_training_budgets",
        "List training budgets across all employees. HR only.",
        list_training_budgets,
        ListTrainingBudgetsArgs,
        False,
        True,
        _check_hr_admin,
        ("HR admin",),
    ),
    (
        "list_conference_registrations",
        "List conference / course registrations.",
        list_conference_registrations,
        ListConferenceRegistrationsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "register_for_conference",
        "Register for a conference or external course.",
        register_for_conference,
        RegisterForConferenceArgs,
        True,
        False,
        None,
        (),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="training",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Employees extensions ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "update_employee_profile",
        "Update an employee profile (self for own; HR for others). cpf_level requires HR.",
        update_employee_profile,
        UpdateEmployeeProfileArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "list_project_assignments",
        "List project assignments for an employee or project.",
        list_project_assignments,
        ListProjectAssignmentsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "list_equipment_assignments",
        "List equipment assignments for an employee.",
        list_equipment_assignments,
        ListEquipmentAssignmentsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "list_salary_history",
        "List salary history for an employee. HR / comp admin only.",
        list_salary_history,
        ListSalaryHistoryArgs,
        False,
        True,
        _check_compensation_admin,
        ("Compensation admin",),
    ),
    (
        "assign_manager",
        "Assign a manager to an employee. HR only.",
        assign_manager,
        AssignManagerArgs,
        True,
        True,
        _check_hr_admin,
        ("HR admin",),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="employees",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Vacations extensions ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "cancel_leave_request",
        "Cancel a leave request (own or HR).",
        cancel_leave_request,
        CancelLeaveRequestArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "adjust_leave_balance",
        "Adjust an employee's leave balance (allocated/used delta). HR only.",
        adjust_leave_balance,
        AdjustLeaveBalanceArgs,
        True,
        True,
        _check_hr_admin,
        ("HR admin",),
    ),
    (
        "get_leave_summary",
        "Per-employee leave summary for a year, grouped by type.",
        get_leave_summary,
        GetLeaveSummaryArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "get_team_leave_calendar",
        "Approved leaves overlapping a date range. Defaults to your team.",
        get_team_leave_calendar,
        GetTeamLeaveCalendarArgs,
        False,
        False,
        None,
        (),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="vacations",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Onboarding extensions ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "list_checklist_templates",
        "List checklist templates available for onboarding/offboarding.",
        list_checklist_templates,
        ListChecklistTemplatesArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "list_checklist_instances",
        "List checklist instances. Employees see own; HR sees all.",
        list_checklist_instances,
        ListChecklistInstancesArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "get_checklist_instance",
        "Get a single checklist instance by id.",
        get_checklist_instance,
        GetChecklistInstanceArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "list_checklist_tasks",
        "List tasks within a checklist instance.",
        list_checklist_tasks,
        ListChecklistTasksArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "update_checklist_task_status",
        "Update a checklist task's status.",
        update_checklist_task_status,
        UpdateChecklistTaskStatusArgs,
        True,
        False,
        None,
        (),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="onboarding",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Assets extensions ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "assign_asset_to_employee",
        "Assign an asset to an employee. Requires assign_assets permission.",
        assign_asset_to_employee,
        AssignAssetArgs,
        True,
        True,
        None,
        ("Asset Management: assign_assets",),
    ),
    (
        "return_asset",
        "Mark an asset assignment as returned. Owner or asset return processor.",
        return_asset,
        ReturnAssetArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "list_asset_assignments",
        "List asset assignments. Filter by asset, employee, or active-only.",
        list_asset_assignments,
        ListAssetAssignmentsArgs,
        False,
        False,
        None,
        (),
    ),
    (
        "list_asset_replacements",
        "List asset replacement logs.",
        list_asset_replacements,
        ListAssetReplacementsArgs,
        False,
        False,
        None,
        (),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="assets",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Documents extensions ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "delete_document",
        "Delete a document. HR only.",
        delete_document,
        DeleteDocumentArgs,
        True,
        True,
        _check_hr_admin,
        ("HR admin",),
    ),
    (
        "list_employee_documents",
        "List per-employee uploaded files (CVs etc.).",
        list_employee_documents,
        ListEmployeeDocumentsArgs,
        False,
        False,
        None,
        (),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="documents",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )

# --- Time tracking extensions ---
for _name, _desc, _fn, _schema, _mutating, _sensitive, _check, _perms in [
    (
        "approve_time_entry",
        "Approve a submitted time entry. Manager or HR.",
        approve_time_entry,
        ApproveTimeEntryArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "reject_time_entry",
        "Reject a time entry with a reason. Manager or HR.",
        reject_time_entry,
        RejectTimeEntryArgs,
        True,
        False,
        None,
        (),
    ),
    (
        "list_pending_time_approvals",
        "List time entries awaiting approval (your reports if non-HR; all if HR).",
        list_pending_time_approvals,
        ListPendingTimeApprovalsArgs,
        False,
        False,
        None,
        (),
    ),
]:
    registry.register(
        AssistantTool(
            _name,
            _desc,
            _fn,
            module="time_tracking",
            mutating=_mutating,
            sensitive=_sensitive,
            args_schema=_schema,
            permission_check=_check,
            required_permissions=_perms,
        )
    )
