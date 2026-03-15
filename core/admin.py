from django.contrib import admin

from .models import (
    ChangeLog,
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
    list_display = ("user", "role", "employee_id", "department")
    list_filter = ("role", "department")
    search_fields = ("user__username", "employee_id", "department")


@admin.register(TechnologyTag)
class TechnologyTagAdmin(admin.ModelAdmin):
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
