from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    APIRootView,
    AssetCapabilitiesView,
    AssetDetailView,
    AssetExportView,
    AssetListView,
    AssetQRCodeView,
    AssignmentDetailView,
    AssignmentListView,
    AssignmentRejectReturnView,
    AssignmentRequestReturnView,
    AssignmentReturnView,
    AvatarUploadView,
    CertificateViewSet,
    ChecklistInstanceViewSet,
    ChecklistTaskViewSet,
    ChecklistTemplateViewSet,
    ConferenceCourseRegistrationViewSet,
    CPFLevelListView,
    DepartmentListView,
    DocumentCategoryDefaultsView,
    DocumentTemplateViewSet,
    DocumentViewSet,
    EmployeeProfileChangeHistoryView,
    EmployeeProfileViewSet,
    EmployeeTechLeadsView,
    GoogleExchangeView,
    LeaveAdjustmentViewSet,
    LeaveBalanceViewSet,
    LeavePolicyViewSet,
    LeaveRequestViewSet,
    LoginView,
    LogoutView,
    NotificationViewSet,
    PeerSessionViewSet,
    PerformanceReviewReminderViewSet,
    PerformanceReviewViewSet,
    PermissionsView,
    ProjectListView,
    RegisterView,
    ReplacementLogDetailView,
    ReplacementLogListView,
    ReturnRequestListView,
    RoleListView,
    ScheduledMaintenanceCancelView,
    ScheduledMaintenanceCompleteView,
    ScheduledMaintenanceDetailView,
    ScheduledMaintenanceListView,
    SessionView,
    TokenRefreshViewCustom,
    TrainingBudgetViewSet,
    TrainingEntryViewSet,
    UploadRolePermissionsView,
    UserProfileListView,
    UserProfileView,
    UserTemplateSnippetViewSet,
)

app_name = "core"

router = DefaultRouter()
router.register(
    r"documents/templates", DocumentTemplateViewSet, basename="document-template"
)
router.register(
    r"documents/template-snippets",
    UserTemplateSnippetViewSet,
    basename="user-template-snippet",
)
router.register(r"documents", DocumentViewSet, basename="document")
router.register(r"employees", EmployeeProfileViewSet, basename="employee")
router.register(
    r"onboarding/templates", ChecklistTemplateViewSet, basename="checklist-template"
)
router.register(r"onboarding/tasks", ChecklistTaskViewSet, basename="checklist-task")
router.register(
    r"onboarding/instances", ChecklistInstanceViewSet, basename="checklist-instance"
)
router.register(r"leave-policies", LeavePolicyViewSet, basename="leave-policy")
router.register(r"leave-balances", LeaveBalanceViewSet, basename="leave-balance")
router.register(r"leave-requests", LeaveRequestViewSet, basename="leave-request")
router.register(
    r"leave-adjustments", LeaveAdjustmentViewSet, basename="leave-adjustment"
)
router.register(
    r"performance-reviews", PerformanceReviewViewSet, basename="performance-review"
)
router.register(
    r"performance-review-reminders",
    PerformanceReviewReminderViewSet,
    basename="performance-review-reminder",
)
router.register(r"training-entries", TrainingEntryViewSet, basename="training-entry")
router.register(r"training-budgets", TrainingBudgetViewSet, basename="training-budget")
router.register(r"peer-sessions", PeerSessionViewSet, basename="peer-session")
router.register(r"certificates", CertificateViewSet, basename="certificate")
router.register(
    r"conference-course-registrations",
    ConferenceCourseRegistrationViewSet,
    basename="conference-course-registration",
)
router.register(r"notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    path("", APIRootView.as_view(), name="api_root"),
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/google/exchange/", GoogleExchangeView.as_view(), name="google_exchange"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/refresh/", TokenRefreshViewCustom.as_view(), name="token_refresh"),
    path("auth/profile/", UserProfileView.as_view(), name="profile"),
    path("auth/session/", SessionView.as_view(), name="session"),
    path("auth/permissions/", PermissionsView.as_view(), name="permissions"),
    path("auth/profile/avatar/", AvatarUploadView.as_view(), name="avatar_upload"),
    path(
        "admin/upload-role-permissions/",
        UploadRolePermissionsView.as_view(),
        name="upload_role_permissions",
    ),
    # Asset Management API endpoints
    path("assets/", AssetListView.as_view(), name="asset_list"),
    path(
        "assets/capabilities/",
        AssetCapabilitiesView.as_view(),
        name="asset_capabilities",
    ),
    path("assets/export/", AssetExportView.as_view(), name="asset_export"),
    path("assets/<int:pk>/qr-code/", AssetQRCodeView.as_view(), name="asset_qr_code"),
    path("assets/<int:pk>/", AssetDetailView.as_view(), name="asset_detail"),
    path("assignments/", AssignmentListView.as_view(), name="assignment_list"),
    path(
        "assignments/<int:pk>/",
        AssignmentDetailView.as_view(),
        name="assignment_detail",
    ),
    path(
        "assignments/<int:pk>/request-return/",
        AssignmentRequestReturnView.as_view(),
        name="assignment_request_return",
    ),
    path(
        "assignments/<int:pk>/return/",
        AssignmentReturnView.as_view(),
        name="assignment_return",
    ),
    path(
        "assignments/<int:pk>/reject-return/",
        AssignmentRejectReturnView.as_view(),
        name="assignment_reject_return",
    ),
    path(
        "return-requests/",
        ReturnRequestListView.as_view(),
        name="return_request_list",
    ),
    path(
        "replacement-logs/",
        ReplacementLogListView.as_view(),
        name="replacement_log_list",
    ),
    path(
        "replacement-logs/<int:pk>/",
        ReplacementLogDetailView.as_view(),
        name="replacement_log_detail",
    ),
    path(
        "scheduled-maintenance/",
        ScheduledMaintenanceListView.as_view(),
        name="scheduled_maintenance_list",
    ),
    path(
        "scheduled-maintenance/<int:pk>/",
        ScheduledMaintenanceDetailView.as_view(),
        name="scheduled_maintenance_detail",
    ),
    path(
        "scheduled-maintenance/<int:pk>/complete/",
        ScheduledMaintenanceCompleteView.as_view(),
        name="scheduled_maintenance_complete",
    ),
    path(
        "scheduled-maintenance/<int:pk>/cancel/",
        ScheduledMaintenanceCancelView.as_view(),
        name="scheduled_maintenance_cancel",
    ),
    path("user-profiles/", UserProfileListView.as_view(), name="user_profile_list"),
    # Reference data endpoints
    path("departments/", DepartmentListView.as_view(), name="department_list"),
    path("projects/", ProjectListView.as_view(), name="project_list"),
    path("roles/", RoleListView.as_view(), name="role_list"),
    path("cpf-levels/", CPFLevelListView.as_view(), name="cpf_level_list"),
    path(
        "cpf-levels/<str:role>/", CPFLevelListView.as_view(), name="cpf_level_by_role"
    ),
    path(
        "employees/<int:employee_id>/profile-change-history/",
        EmployeeProfileChangeHistoryView.as_view(),
        name="employee_profile_change_history",
    ),
    path(
        "employees/<int:employee_id>/tech-leads/",
        EmployeeTechLeadsView.as_view(),
        name="employee_tech_leads",
    ),
    path(
        "documents/category-defaults/",
        DocumentCategoryDefaultsView.as_view(),
        name="document_category_defaults",
    ),
    path(
        "documents/category-defaults/<str:category>/",
        DocumentCategoryDefaultsView.as_view(),
        name="document_category_default_detail",
    ),
]

urlpatterns += router.urls
