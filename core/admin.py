from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Certificate,
    ChangeLog,
    CPFLevel,
    Document,
    DocumentCategory,
    EmployeeDocument,
    Equipment,
    EquipmentAssignment,
    LeaveAdjustment,
    LeaveApprovalWorkflow,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    PeerSession,
    PerformanceReview,
    PerformanceReviewActionPoint,
    PerformanceReviewAttachment,
    PerformanceReviewHistoryEvent,
    PerformanceReviewNote,
    PerformanceReviewReminder,
    Project,
    ProjectAssignment,
    Role,
    SalaryRecord,
    TechnologyTag,
    TrainingBudget,
    TrainingEntry,
    UserProfile,
)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    @admin.display(description="Avatar")
    def avatar_thumb(self, obj: UserProfile):
        if not getattr(obj, "avatar", None):
            return "-"
        try:
            return format_html(
                '<img src="{}" style="height:40px;width:40px;border-radius:6px;object-fit:cover;" />',
                obj.avatar.url,
            )
        except Exception:
            return "-"

    @admin.display(description="Managers")
    def managers_list(self, obj: UserProfile):
        return ", ".join([m.full_name or m.user.username for m in obj.managers.all()])

    list_display = (
        "user",
        "full_name",
        "email_address",
        "role",
        "managers_list",
        "avatar_thumb",
        "employee_id",
        "department",
        "start_date",
        "cpf_level",
        "employment_status",
    )
    list_filter = (
        "role",
        "managers",
        "department",
        "employment_status",
    )
    search_fields = (
        "user__username",
        "employee_id",
        "department",
        "full_name",
        "email_address",
    )


@admin.register(TechnologyTag)
class TechnologyTagAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(CPFLevel)
class CPFLevelAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "project_type",
        "status",
        "client",
        "owner",
        "start_date",
        "end_date",
    )
    list_filter = ("project_type", "status")
    search_fields = ("name", "client")


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "serial_number")
    search_fields = ("name", "serial_number")


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ("user_profile", "doc_type", "version", "uploaded_at")
    list_filter = ("doc_type",)
    search_fields = ("user_profile__user__username",)


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "visibility_rule")
    search_fields = ("name", "visibility_rule")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "employee",
        "category",
        "uploaded_at",
        "expiry_date",
        "signed_at",
    )
    list_filter = ("category", "uploaded_at", "expiry_date", "signed_at")
    search_fields = (
        "name",
        "file_key",
        "employee__full_name",
        "employee__user__username",
        "category__name",
    )


@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "user_profile",
        "project",
        "role",
        "allocation_percentage",
        "status",
        "start_date",
        "end_date",
    )
    list_filter = ("project", "status")
    search_fields = ("user_profile__user__username", "project__name")


@admin.register(EquipmentAssignment)
class EquipmentAssignmentAdmin(admin.ModelAdmin):
    list_display = ("equipment", "user_profile", "assigned_date", "returned_date")
    list_filter = ("equipment",)
    search_fields = ("equipment__serial_number", "user_profile__user__username")


@admin.register(SalaryRecord)
class SalaryRecordAdmin(admin.ModelAdmin):
    list_display = ("user_profile", "amount", "effective_date")
    list_filter = ("effective_date",)
    search_fields = ("user_profile__user__username",)


@admin.register(ChangeLog)
class ChangeLogAdmin(admin.ModelAdmin):
    list_display = ("user_profile", "field_name", "changed_at", "changed_by")
    list_filter = ("field_name",)
    search_fields = ("user_profile__user__username", "field_name")


# ──────────────────────────────────────────
# Leave Management Admin
# ──────────────────────────────────────────


@admin.register(LeavePolicy)
class LeavePolicyAdmin(admin.ModelAdmin):
    list_display = (
        "leave_type",
        "allocated_days_per_year",
        "carryover_days",
        "requires_approval",
        "min_notice_in_days",
    )
    list_filter = ("requires_approval", "requires_covering_employee")
    search_fields = ("leave_type",)


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "leave_type",
        "year",
        "allocated",
        "used",
        "remaining_display",
        "carryover",
    )
    list_filter = ("leave_type", "year")
    search_fields = (
        "employee__user__username",
        "employee__full_name",
        "employee__user__email",
    )
    ordering = ["-year", "employee"]

    @admin.display(description="Remaining")
    def remaining_display(self, obj):
        return obj.remaining


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "leave_type",
        "start_date",
        "end_date",
        "days_display",
        "status",
        "submitted_date",
        "approver",
    )
    list_filter = ("status", "leave_type", "submitted_date")
    search_fields = (
        "employee__user__username",
        "employee__full_name",
        "reason",
    )
    ordering = ["-submitted_date"]
    readonly_fields = ("submitted_date", "days_display")

    @admin.display(description="Days")
    def days_display(self, obj):
        return obj.days

    fieldsets = (
        (
            "Request Information",
            {
                "fields": (
                    "employee",
                    "leave_type",
                    "start_date",
                    "end_date",
                    "days_display",
                    "reason",
                    "covering_employee",
                )
            },
        ),
        (
            "Status & Approval",
            {
                "fields": (
                    "status",
                    "submitted_date",
                    "approver",
                    "approved_date",
                    "approval_comments",
                    "rejection_reason",
                )
            },
        ),
    )


class LeaveAdjustmentInline(admin.TabularInline):
    model = LeaveAdjustment
    extra = 0
    readonly_fields = ("adjusted_at",)
    fields = (
        "leave_type",
        "old_allocated",
        "new_allocated",
        "reason",
        "adjusted_by",
        "adjusted_at",
    )


@admin.register(LeaveAdjustment)
class LeaveAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "leave_type",
        "old_allocated",
        "new_allocated",
        "adjusted_by",
        "adjusted_at",
    )
    list_filter = ("leave_type", "adjusted_at")
    search_fields = (
        "employee__user__username",
        "employee__full_name",
        "reason",
    )
    readonly_fields = ("adjusted_at",)
    ordering = ["-adjusted_at"]


@admin.register(LeaveApprovalWorkflow)
class LeaveApprovalWorkflowAdmin(admin.ModelAdmin):
    list_display = (
        "leave_request",
        "status",
        "current_step",
        "current_approver",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "leave_request__employee__user__username",
        "leave_request__employee__full_name",
    )
    readonly_fields = ("created_at", "updated_at")
    ordering = ["-created_at"]


# ──────────────────────────────────────────
# Performance Review Admin
# ──────────────────────────────────────────


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "reviewer",
        "review_type",
        "scheduled_date",
        "status",
        "overall_rating",
        "cpf_current_level",
        "cpf_recommended_level",
    )
    list_filter = ("status", "review_type", "scheduled_date")
    search_fields = (
        "employee__full_name",
        "employee__user__username",
        "reviewer__full_name",
        "reviewer__user__username",
        "title",
    )
    ordering = ["-scheduled_date", "-created_at"]


@admin.register(PerformanceReviewNote)
class PerformanceReviewNoteAdmin(admin.ModelAdmin):
    list_display = ("review", "author", "visibility", "created_at", "updated_at")
    list_filter = ("visibility", "created_at")
    search_fields = (
        "review__employee__full_name",
        "review__employee__user__username",
        "author__full_name",
        "content",
    )
    ordering = ["-created_at"]


@admin.register(PerformanceReviewActionPoint)
class PerformanceReviewActionPointAdmin(admin.ModelAdmin):
    list_display = ("title", "review", "owner", "status", "progress", "due_date")
    list_filter = ("status", "due_date")
    search_fields = (
        "title",
        "review__employee__full_name",
        "owner__full_name",
    )
    ordering = ["due_date", "created_at"]


@admin.register(PerformanceReviewAttachment)
class PerformanceReviewAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        "review",
        "uploaded_by",
        "original_name",
        "size_bytes",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = (
        "review__employee__full_name",
        "original_name",
        "description",
    )
    ordering = ["-created_at"]


@admin.register(PerformanceReviewReminder)
class PerformanceReviewReminderAdmin(admin.ModelAdmin):
    list_display = (
        "review",
        "recipient",
        "reminder_type",
        "scheduled_for",
        "is_sent",
        "is_read",
    )
    list_filter = ("reminder_type", "is_sent", "is_read", "scheduled_for")
    search_fields = (
        "review__employee__full_name",
        "recipient__full_name",
        "message",
    )
    ordering = ["-scheduled_for", "-created_at"]


@admin.register(PerformanceReviewHistoryEvent)
class PerformanceReviewHistoryEventAdmin(admin.ModelAdmin):
    list_display = ("review", "event_type", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = (
        "review__employee__full_name",
        "description",
    )
    ordering = ["-created_at"]


# ──────────────────────────────────────────
# Training & Development Admin
# ──────────────────────────────────────────


@admin.register(TrainingEntry)
class TrainingEntryAdmin(admin.ModelAdmin):
    list_display = (
        "course_title",
        "employee",
        "provider",
        "training_date",
        "training_type",
        "cost",
        "completed_at",
    )
    list_filter = ("training_type", "training_date", "completed_at")
    search_fields = (
        "course_title",
        "provider",
        "employee__user__username",
        "employee__full_name",
    )
    ordering = ["-training_date"]
    readonly_fields = ("created_at", "updated_at")


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "employee",
        "issuer",
        "issued_date",
        "expiration_date",
        "is_expired_display",
    )
    list_filter = ("issued_date", "expiration_date")
    search_fields = (
        "title",
        "issuer",
        "employee__user__username",
        "employee__full_name",
    )
    ordering = ["-issued_date"]
    readonly_fields = ("created_at", "updated_at", "is_expired_display")

    @admin.display(description="Expired", boolean=True)
    def is_expired_display(self, obj):
        return obj.is_expired


@admin.register(PeerSession)
class PeerSessionAdmin(admin.ModelAdmin):
    list_display = (
        "topic",
        "employee",
        "session_date",
        "duration_minutes",
        "incentive_id",
    )
    list_filter = ("session_date",)
    search_fields = (
        "topic",
        "employee__user__username",
        "employee__full_name",
        "description",
    )
    ordering = ["-session_date"]
    readonly_fields = ("created_at", "updated_at")


@admin.register(TrainingBudget)
class TrainingBudgetAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "fiscal_year",
        "allocated_budget",
        "used_budget",
        "remaining_display",
        "percentage_used_display",
    )
    list_filter = ("fiscal_year",)
    search_fields = (
        "employee__user__username",
        "employee__full_name",
    )
    ordering = ["-fiscal_year", "employee"]
    readonly_fields = (
        "created_at",
        "updated_at",
        "remaining_display",
        "percentage_used_display",
    )

    @admin.display(description="Remaining Budget")
    def remaining_display(self, obj):
        return f"${obj.remaining_budget:.2f}"

    @admin.display(description="% Used")
    def percentage_used_display(self, obj):
        return f"{obj.budget_percentage_used:.1f}%"
