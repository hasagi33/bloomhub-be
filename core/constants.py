# core/constants.py

from decimal import Decimal

from core.enums import LeaveRequestStatus

REGISTER_FIELDS = [
    "username",
    "email",
    "password",
    "password_confirm",
    "first_name",
    "last_name",
    "avatar",
    "avatar_url",
]

REGISTER_EXTRA_KWARGS = {
    "email": {"required": True},
}

EMPLOYEE_PROFILE_FIELDS = [
    "id",
    "username",
    "employee_id",
    "email",
    "first_name",
    "last_name",
    "full_name",
    "email_address",
    "department",
    "role",
    "role_name",
    "managers",
    "manager_names",
    "start_date",
    "phone_number",
    "emergency_contact_phone",
    "address",
    "employment_status",
    "avatar",
    "is_active",
    "cpf_level",
    "career_level",
    "salary",
    "current_salary",
    "current_net_salary",
    "current_total_monthly",
    "current_bonus_pct",
    "compensation_status",
    "tech_tags",
    "permissions_bitmap",
    "assigned_projects",
    "intro_announcement",
    "intro_announcement_published_at",
]

EMPLOYEE_PROFILE_READ_ONLY_FIELDS = [
    "id",
    "username",
    "is_active",
    "permissions_bitmap",
    "intro_announcement",
    "intro_announcement_published_at",
]

EMPLOYEE_PROFILE_FILTERSET_FIELDS = [
    "role__name",
    "department",
    "is_active",
    "employment_status",
]

EMPLOYEE_PROFILE_SEARCH_FIELDS = [
    "full_name",
    "email_address",
    "user__username",
    "employee_id",
]

EMPLOYEE_PROFILE_ORDERING_FIELDS = ["full_name", "start_date", "created_at"]

CPF_LEVEL_CHANGE_SERIALIZER_FIELDS = [
    "id",
    "employee_id",
    "employee_name",
    "previous_level",
    "new_level",
    "effective_date",
    "source",
    "source_display",
    "cpf_score",
    "performance_review_id",
    "promotion_id",
    "notes",
    "recorded_by_name",
    "created_at",
    "updated_at",
]

CPF_LEVEL_CHANGE_WRITE_FIELDS = [
    "employee_id",
    "previous_level",
    "new_level",
    "effective_date",
    "source",
    "cpf_score",
    "performance_review_id",
    "promotion_id",
    "notes",
]

CPF_LEVEL_CHANGE_FILTERSET_FIELDS = ["employee", "source"]

CPF_LEVEL_CHANGE_SEARCH_FIELDS = [
    "notes",
    "new_level",
    "previous_level",
    "employee__user__first_name",
    "employee__user__last_name",
]

CPF_LEVEL_CHANGE_ORDERING_FIELDS = ["effective_date", "created_at"]

DOCUMENT_ROLE_RANK_ADMIN = 4
DOCUMENT_ROLE_RANK_HR = 3
DOCUMENT_ROLE_RANK_MANAGER = 2
DOCUMENT_ROLE_RANK_EMPLOYEE = 1

TRAINING_BUDGET_WARNING_THRESHOLD = Decimal("0.80")

DOCUMENT_CATEGORY_DEFAULT_VISIBILITY = {
    "contracts": ["hr"],
    "compliance": ["hr"],
    "agreements": ["hr"],
    "policies": ["employee"],
    "onboarding": ["employee"],
    "training": ["employee"],
    "benefits": ["employee"],
    "other": ["employee"],
}

# Leave Analytics ─────────────────────────────────────────────────────────────
# Maps each LeaveRequest.status to the LeaveMonthlyAggregate field it feeds.
# `lead_approved` lands in `pending_days` because the request has not yet
# completed HR approval, so the days are not yet consumed from the balance.
LEAVE_ANALYTICS_STATUS_BUCKET = {
    LeaveRequestStatus.PENDING: "pending_days",
    LeaveRequestStatus.LEAD_APPROVED: "pending_days",
    LeaveRequestStatus.APPROVED: "approved_days",
    LeaveRequestStatus.REJECTED: "rejected_days",
    LeaveRequestStatus.CANCELLED: "cancelled_days",
}

LEAVE_ANALYTICS_TRACKED_STATUSES = tuple(LEAVE_ANALYTICS_STATUS_BUCKET.keys())

LEAVE_MONTHLY_AGGREGATE_FILTERSET_FIELDS = [
    "employee",
    "leave_type",
    "year",
    "month",
]
LEAVE_MONTHLY_AGGREGATE_ORDERING_FIELDS = [
    "year",
    "month",
    "leave_type",
    "approved_days",
    "updated_at",
]
LEAVE_BALANCE_SNAPSHOT_FILTERSET_FIELDS = [
    "employee",
    "leave_type",
    "year",
    "snapshot_date",
]
LEAVE_BALANCE_SNAPSHOT_ORDERING_FIELDS = [
    "snapshot_date",
    "year",
    "leave_type",
    "remaining",
]
