"""Org-chart endpoints: single-roundtrip tree fetch + recent activity feed."""

from datetime import datetime
from typing import Any

from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .enums import (
    EmploymentStatus,
    OrgChartEventKind,
    ProjectAssignmentStatus,
    ProjectStatus,
    TrackedField,
)
from .models import (
    Department,
    EmployeeProfileChangeHistory,
    Project,
    ProjectAssignment,
    UserProfile,
)

# ── Serializers (schema only) ──────────────────────────────────────────────


class OrgChartRoleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class OrgChartTechTagSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class OrgChartEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    email = serializers.EmailField(allow_null=True)
    phone_number = serializers.CharField(allow_null=True)
    location = serializers.CharField(allow_null=True)
    start_date = serializers.DateField(allow_null=True)
    employment_status = serializers.CharField()
    is_remote = serializers.BooleanField()
    is_manager = serializers.BooleanField()
    primary_manager_id = serializers.IntegerField(allow_null=True)
    department_id = serializers.IntegerField(allow_null=True)
    role = OrgChartRoleSerializer(allow_null=True)
    technology_tags = OrgChartTechTagSerializer(many=True)
    skills = serializers.ListField(child=serializers.CharField())


class OrgChartDepartmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    color = serializers.CharField()
    color_soft = serializers.CharField()
    employee_count = serializers.IntegerField()
    head_employee_id = serializers.IntegerField(allow_null=True)


class OrgChartProjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()
    member_ids = serializers.ListField(child=serializers.IntegerField())


class OrgChartResponseSerializer(serializers.Serializer):
    employees = OrgChartEmployeeSerializer(many=True)
    departments = OrgChartDepartmentSerializer(many=True)
    projects = OrgChartProjectSerializer(many=True)


class OrgChartEventSerializer(serializers.Serializer):
    id = serializers.CharField()
    kind = serializers.CharField()
    text = serializers.CharField()
    at = serializers.DateTimeField()
    actor_id = serializers.IntegerField(allow_null=True)
    subject_id = serializers.IntegerField(allow_null=True)


class OrgChartRecentUpdatesResponseSerializer(serializers.Serializer):
    results = OrgChartEventSerializer(many=True)


# ── Helpers ────────────────────────────────────────────────────────────────


def _split_name(profile: UserProfile) -> tuple[str, str]:
    name = (profile.full_name or "").strip()
    if name:
        parts = name.split(" ", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""
    user = profile.user
    return (user.first_name or "", user.last_name or "")


def _format_event_text(
    kind: str, subject_name: str, event: EmployeeProfileChangeHistory
) -> str:
    if kind == OrgChartEventKind.HIRE:
        return f"{subject_name} joined the company"
    if kind == OrgChartEventKind.PROMOTE:
        new_role = event.new_value if isinstance(event.new_value, str) else None
        if new_role:
            return f"{subject_name} promoted to {new_role}"
        return f"{subject_name} promoted"
    if kind == OrgChartEventKind.REASSIGN:
        if event.field == TrackedField.MANAGER_IDS:
            return f"{subject_name} reassigned to a new manager"
        if event.field == TrackedField.DEPARTMENT:
            new_dept = event.new_value if isinstance(event.new_value, str) else None
            if new_dept:
                return f"{subject_name} moved to {new_dept}"
            return f"{subject_name} changed department"
    if kind == OrgChartEventKind.LEAVE:
        return f"{subject_name} is on leave"
    return f"{subject_name} updated"


def _kind_for(event: EmployeeProfileChangeHistory) -> str | None:
    if event.field in (TrackedField.ROLE, TrackedField.CPF_LEVEL):
        return OrgChartEventKind.PROMOTE
    if event.field in (TrackedField.MANAGER_IDS, TrackedField.DEPARTMENT):
        return OrgChartEventKind.REASSIGN
    if event.field == TrackedField.EMPLOYMENT_STATUS:
        new = event.new_value
        if new == EmploymentStatus.ON_LEAVE or new == EmploymentStatus.INACTIVE:
            return OrgChartEventKind.LEAVE
    return None


# ── Endpoints ──────────────────────────────────────────────────────────────


@extend_schema(tags=["org-chart"])
class OrgChartView(APIView):
    """Single-roundtrip fetch of active workforce, departments, and projects."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OrgChartResponseSerializer})
    def get(self, request):
        # Employees: active, with primary_manager subquery for is_manager.
        is_manager_sq = UserProfile.objects.filter(primary_manager=OuterRef("pk"))

        employees_qs = (
            UserProfile.objects.filter(is_active=True)
            .select_related("user", "role", "department_fk")
            .prefetch_related("tech_tags")
            .annotate(_is_manager=Exists(is_manager_sq))
            .order_by("id")
        )

        employees: list[dict[str, Any]] = []
        for emp in employees_qs:
            first, last = _split_name(emp)
            tech_tags = list(emp.tech_tags.all())
            employees.append(
                {
                    "id": emp.id,
                    "first_name": first,
                    "last_name": last,
                    "email": emp.email_address or emp.user.email or None,
                    "phone_number": emp.phone_number,
                    "location": emp.address,
                    "start_date": emp.start_date,
                    "employment_status": emp.employment_status,
                    "is_remote": emp.is_remote,
                    "is_manager": bool(emp._is_manager),
                    "primary_manager_id": emp.primary_manager_id,
                    "department_id": emp.department_fk_id,
                    "role": (
                        {"id": emp.role.id, "name": emp.role.name} if emp.role else None
                    ),
                    "technology_tags": [
                        {"id": t.id, "name": t.name} for t in tech_tags
                    ],
                    "skills": [t.name for t in tech_tags],
                }
            )

        # Departments with active employee counts.
        departments_qs = Department.objects.annotate(
            _employee_count=Count(
                "members", filter=Q(members__is_active=True), distinct=True
            )
        ).order_by("name")
        departments = [
            {
                "id": d.id,
                "name": d.name,
                "color": d.color,
                "color_soft": d.color_soft,
                "employee_count": d._employee_count,
                "head_employee_id": d.head_employee_id,
            }
            for d in departments_qs
        ]

        # Projects + currently-active assignments.
        today = timezone.now().date()
        active_assignments = ProjectAssignment.objects.filter(
            status=ProjectAssignmentStatus.ACTIVE,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))

        projects_qs = (
            Project.objects.exclude(status=ProjectStatus.ARCHIVED)
            .prefetch_related(
                Prefetch(
                    "assignments",
                    queryset=active_assignments,
                    to_attr="_active_assignments",
                )
            )
            .order_by("name")
        )
        projects = [
            {
                "id": p.id,
                "name": p.name,
                "status": p.status,
                "member_ids": sorted(
                    {a.user_profile_id for a in getattr(p, "_active_assignments", [])}
                ),
            }
            for p in projects_qs
        ]

        return Response(
            {
                "employees": employees,
                "departments": departments,
                "projects": projects,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["org-chart"])
class OrgChartRecentUpdatesView(APIView):
    """Feed of recent org changes derived from EmployeeProfileChangeHistory."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int, description="Max 50, default 10."),
            OpenApiParameter(
                "since",
                str,
                description="ISO 8601 lower bound (inclusive) on `changed_at`.",
            ),
        ],
        responses={200: OrgChartRecentUpdatesResponseSerializer},
    )
    def get(self, request):
        limit_raw = request.query_params.get("limit", "10")
        try:
            limit = max(1, min(int(limit_raw), 50))
        except (TypeError, ValueError):
            limit = 10

        events: list[dict[str, Any]] = []

        # Hire events: derived from UserProfile.created_at (start_date <= now).
        since_raw = request.query_params.get("since")
        since_dt: datetime | None = None
        if since_raw:
            parsed = _parse_iso(since_raw)
            if parsed is not None:
                since_dt = parsed

        hires_qs = (
            UserProfile.objects.filter(is_active=True)
            .filter(
                Q(start_date__isnull=True) | Q(start_date__lte=timezone.now().date())
            )
            .select_related("user")
            .order_by("-created_at")
        )
        if since_dt is not None:
            hires_qs = hires_qs.filter(created_at__gte=since_dt)

        for hire in hires_qs[: limit * 2]:
            name = hire.full_name or hire.user.get_full_name() or hire.user.username
            events.append(
                {
                    "id": f"hire-{hire.id}",
                    "kind": OrgChartEventKind.HIRE,
                    "text": f"{name} joined the company",
                    "at": hire.created_at,
                    "actor_id": None,
                    "subject_id": hire.id,
                }
            )

        # Profile-change events: promote / reassign / leave.
        change_qs = (
            EmployeeProfileChangeHistory.objects.filter(
                field__in=[
                    TrackedField.ROLE,
                    TrackedField.CPF_LEVEL,
                    TrackedField.MANAGER_IDS,
                    TrackedField.DEPARTMENT,
                    TrackedField.EMPLOYMENT_STATUS,
                ]
            )
            .select_related("employee", "employee__user", "changed_by")
            .order_by("-changed_at")
        )
        if since_dt is not None:
            change_qs = change_qs.filter(changed_at__gte=since_dt)

        for ev in change_qs[: limit * 4]:
            kind = _kind_for(ev)
            if kind is None:
                continue
            subject = ev.employee
            name = (
                subject.full_name
                or subject.user.get_full_name()
                or subject.user.username
            )
            events.append(
                {
                    "id": f"chg-{ev.id}",
                    "kind": kind,
                    "text": _format_event_text(kind, name, ev),
                    "at": ev.changed_at,
                    "actor_id": (
                        getattr(ev.changed_by, "profile", None).id
                        if ev.changed_by
                        and hasattr(ev.changed_by, "profile")
                        and ev.changed_by.profile is not None
                        else None
                    ),
                    "subject_id": subject.id,
                }
            )

        events.sort(key=lambda e: e["at"], reverse=True)
        return Response({"results": events[:limit]}, status=status.HTTP_200_OK)


def _parse_iso(value: str) -> datetime | None:
    try:
        # Django's parse_datetime accepts the same formats DRF uses.
        from django.utils.dateparse import parse_datetime

        return parse_datetime(value)
    except (TypeError, ValueError):
        return None
