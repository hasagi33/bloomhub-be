from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ChangeLog,
    CPFLevel,
    EmployeeDocument,
    Equipment,
    EquipmentAssignment,
    Project,
    ProjectAssignment,
    Role,
    SalaryRecord,
    TechnologyTag,
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
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "serial_number")
    search_fields = ("name", "serial_number")


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ("user_profile", "doc_type", "version", "uploaded_at")
    list_filter = ("doc_type",)
    search_fields = ("user_profile__user__username",)


@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user_profile", "project", "role", "start_date", "end_date")
    list_filter = ("project",)
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
