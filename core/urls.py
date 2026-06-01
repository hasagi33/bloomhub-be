from django.urls import path
from rest_framework.routers import DefaultRouter

from .ai.api import (
    AIChatCapabilitiesView,
    AIChatSessionDetailView,
    AIChatSessionListView,
    AIChatToolCoverageView,
    AIChatView,
)
from .org_chart import OrgChartRecentUpdatesView, OrgChartView
from .views import (
    AnnouncementViewSet,
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
    BenefitCatalogViewSet,
    BonusRecordViewSet,
    CertificateViewSet,
    ChecklistInstanceViewSet,
    ChecklistTaskViewSet,
    ChecklistTemplateViewSet,
    CompensationOverviewView,
    CompensationPolicyViewSet,
    ConferenceCourseRegistrationViewSet,
    CPFLevelChangeViewSet,
    CPFLevelListView,
    DepartmentListView,
    DiscordAnnouncementChannelViewSet,
    DocumentCategoryDefaultsView,
    DocumentTemplateViewSet,
    DocumentViewSet,
    EmployeeBonusListView,
    EmployeeProfileChangeHistoryView,
    EmployeeProfileViewSet,
    EmployeeTechLeadsView,
    GoogleExchangeView,
    JiraAssignedIssueImportView,
    JiraImportCommitView,
    JiraImportPreviewView,
    JiraMappingsView,
    JiraOAuthAuthorizeView,
    JiraOAuthCallbackView,
    JiraOAuthDisconnectView,
    JiraOAuthStatusView,
    JiraProjectDiscoveryView,
    JiraSettingsView,
    JiraSyncView,
    JiraTestConnectionView,
    JobApplicationViewSet,
    JobListingViewSet,
    LeaveAdjustmentViewSet,
    LeaveAnalyticsViewSet,
    LeaveBalanceSnapshotViewSet,
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
    ProjectActivityView,
    ProjectArchiveView,
    ProjectAssignmentDetailView,
    ProjectAssignmentEndView,
    ProjectAssignmentListCreateView,
    ProjectDetailView,
    ProjectListView,
    ProjectReactivateView,
    PromotionHistoryViewSet,
    PulseCheckViewSet,
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
    SurveyViewSet,
    TempoImportCommitView,
    TempoImportPreviewView,
    TempoMappingsView,
    TempoOAuthAuthorizeView,
    TempoOAuthCallbackView,
    TempoOAuthDisconnectView,
    TempoOAuthStatusView,
    TempoProjectDiscoveryView,
    TempoSettingsView,
    TempoTestConnectionView,
    TimeDocumentImportColumnMapView,
    TimeDocumentImportUploadView,
    TimeEntryViewSet,
    TimeImportBatchCommitView,
    TimeImportBatchDetailView,
    TimeImportBatchListView,
    TimeImportBatchPreviewView,
    TimeTaskViewSet,
    TimeTrackingActiveAllocationsView,
    TimeTrackingApprovalQueueView,
    TimeTrackingPlannedVsActualView,
    TimeTrackingSourceChangeResolveView,
    TimeTrackingSourceChangeReviewView,
    TimeTrackingTimesheetExportView,
    TimeTrackingWeeklyDashboardView,
    TimeTrackingWeeklySummaryView,
    TokenRefreshViewCustom,
    TrainingBudgetViewSet,
    TrainingEntryViewSet,
    UpcomingCelebrationsView,
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
router.register(r"leave-analytics", LeaveAnalyticsViewSet, basename="leave-analytics")
router.register(
    r"leave-balance-snapshots",
    LeaveBalanceSnapshotViewSet,
    basename="leave-balance-snapshot",
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
router.register(r"time-tasks", TimeTaskViewSet, basename="time-task")
router.register(r"time-entries", TimeEntryViewSet, basename="time-entry")
router.register(r"training-budgets", TrainingBudgetViewSet, basename="training-budget")
router.register(r"peer-sessions", PeerSessionViewSet, basename="peer-session")
router.register(r"certificates", CertificateViewSet, basename="certificate")
router.register(
    r"conference-course-registrations",
    ConferenceCourseRegistrationViewSet,
    basename="conference-course-registration",
)
router.register(r"notifications", NotificationViewSet, basename="notification")
router.register(r"announcements", AnnouncementViewSet, basename="announcement")
router.register(
    r"announcement-discord-channels",
    DiscordAnnouncementChannelViewSet,
    basename="announcement-discord-channel",
)
router.register(r"job-listings", JobListingViewSet, basename="job-listing")
router.register(r"job-applications", JobApplicationViewSet, basename="job-application")
router.register(r"surveys", SurveyViewSet, basename="survey")
router.register(r"pulse-checks", PulseCheckViewSet, basename="pulse-check")
router.register(
    r"promotion-history", PromotionHistoryViewSet, basename="promotion-history"
)
router.register(
    r"cpf-level-changes", CPFLevelChangeViewSet, basename="cpf-level-change"
)
router.register(r"bonuses", BonusRecordViewSet, basename="bonus")
router.register(
    r"compensation/policies", CompensationPolicyViewSet, basename="compensation-policy"
)
router.register(
    r"compensation/benefits", BenefitCatalogViewSet, basename="benefit-catalog"
)

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
    path("ai/chat/", AIChatView.as_view(), name="ai_chat"),
    path(
        "ai/chat/capabilities/",
        AIChatCapabilitiesView.as_view(),
        name="ai_chat_capabilities",
    ),
    path(
        "ai/chat/tool-coverage/",
        AIChatToolCoverageView.as_view(),
        name="ai_chat_tool_coverage",
    ),
    path(
        "ai/chat/sessions/",
        AIChatSessionListView.as_view(),
        name="ai_chat_sessions",
    ),
    path(
        "ai/chat/sessions/<int:pk>/",
        AIChatSessionDetailView.as_view(),
        name="ai_chat_session_detail",
    ),
    path(
        "celebrations/upcoming/",
        UpcomingCelebrationsView.as_view(),
        name="upcoming_celebrations",
    ),
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
    path("org-chart/", OrgChartView.as_view(), name="org_chart"),
    path(
        "org-chart/recent-updates/",
        OrgChartRecentUpdatesView.as_view(),
        name="org_chart_recent_updates",
    ),
    path("projects/", ProjectListView.as_view(), name="project_list"),
    path("projects/<int:pk>/", ProjectDetailView.as_view(), name="project_detail"),
    path(
        "projects/<int:pk>/archive/",
        ProjectArchiveView.as_view(),
        name="project_archive",
    ),
    path(
        "projects/<int:pk>/reactivate/",
        ProjectReactivateView.as_view(),
        name="project_reactivate",
    ),
    path(
        "projects/<int:pk>/activity/",
        ProjectActivityView.as_view(),
        name="project_activity",
    ),
    path(
        "projects/<int:project_pk>/assignments/",
        ProjectAssignmentListCreateView.as_view(),
        name="project_assignment_list",
    ),
    path(
        "project-assignments/<int:pk>/",
        ProjectAssignmentDetailView.as_view(),
        name="project_assignment_detail",
    ),
    path(
        "project-assignments/<int:pk>/end/",
        ProjectAssignmentEndView.as_view(),
        name="project_assignment_end",
    ),
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
        "time-tracking/weekly-summary/",
        TimeTrackingWeeklySummaryView.as_view(),
        name="time_tracking_weekly_summary",
    ),
    path(
        "time-tracking/active-allocations/",
        TimeTrackingActiveAllocationsView.as_view(),
        name="time_tracking_active_allocations",
    ),
    path(
        "time-tracking/weekly-dashboard/",
        TimeTrackingWeeklyDashboardView.as_view(),
        name="time_tracking_weekly_dashboard",
    ),
    path(
        "time-tracking/approval-queue/",
        TimeTrackingApprovalQueueView.as_view(),
        name="time_tracking_approval_queue",
    ),
    path(
        "time-tracking/planned-vs-actual/",
        TimeTrackingPlannedVsActualView.as_view(),
        name="time_tracking_planned_vs_actual",
    ),
    path(
        "time-tracking/exports/timesheets/",
        TimeTrackingTimesheetExportView.as_view(),
        name="time_tracking_timesheet_export",
    ),
    path(
        "time-tracking/source-change-review/",
        TimeTrackingSourceChangeReviewView.as_view(),
        name="time_tracking_source_change_review",
    ),
    path(
        "time-tracking/source-change-review/<int:entry_id>/resolve/",
        TimeTrackingSourceChangeResolveView.as_view(),
        name="time_tracking_source_change_resolve",
    ),
    path(
        "time-integrations/jira/settings/",
        JiraSettingsView.as_view(),
        name="time_jira_settings",
    ),
    path(
        "time-integrations/jira/test-connection/",
        JiraTestConnectionView.as_view(),
        name="time_jira_test_connection",
    ),
    path(
        "time-integrations/jira/mappings/",
        JiraMappingsView.as_view(),
        name="time_jira_mappings",
    ),
    path(
        "time-integrations/jira/project-discovery/",
        JiraProjectDiscoveryView.as_view(),
        name="time_jira_project_discovery",
    ),
    path(
        "time-integrations/jira/oauth/authorize/",
        JiraOAuthAuthorizeView.as_view(),
        name="time_jira_oauth_authorize",
    ),
    path(
        "time-integrations/jira/oauth/callback/",
        JiraOAuthCallbackView.as_view(),
        name="time_jira_oauth_callback",
    ),
    path(
        "time-integrations/jira/oauth/status/",
        JiraOAuthStatusView.as_view(),
        name="time_jira_oauth_status",
    ),
    path(
        "time-integrations/jira/oauth/connection/",
        JiraOAuthDisconnectView.as_view(),
        name="time_jira_oauth_disconnect",
    ),
    path(
        "time-integrations/jira/sync/",
        JiraSyncView.as_view(),
        name="time_jira_sync",
    ),
    path(
        "time-imports/jira/preview/",
        JiraImportPreviewView.as_view(),
        name="time_jira_import_preview",
    ),
    path(
        "time-imports/jira/assigned-issues/",
        JiraAssignedIssueImportView.as_view(),
        name="time_jira_assigned_issue_import",
    ),
    path(
        "time-imports/jira/commit/",
        JiraImportCommitView.as_view(),
        name="time_jira_import_commit",
    ),
    path(
        "time-integrations/tempo/settings/",
        TempoSettingsView.as_view(),
        name="time_tempo_settings",
    ),
    path(
        "time-integrations/tempo/test-connection/",
        TempoTestConnectionView.as_view(),
        name="time_tempo_test_connection",
    ),
    path(
        "time-integrations/tempo/mappings/",
        TempoMappingsView.as_view(),
        name="time_tempo_mappings",
    ),
    path(
        "time-integrations/tempo/project-discovery/",
        TempoProjectDiscoveryView.as_view(),
        name="time_tempo_project_discovery",
    ),
    path(
        "time-integrations/tempo/oauth/authorize/",
        TempoOAuthAuthorizeView.as_view(),
        name="time_tempo_oauth_authorize",
    ),
    path(
        "time-integrations/tempo/oauth/callback/",
        TempoOAuthCallbackView.as_view(),
        name="time_tempo_oauth_callback",
    ),
    path(
        "time-integrations/tempo/oauth/status/",
        TempoOAuthStatusView.as_view(),
        name="time_tempo_oauth_status",
    ),
    path(
        "time-integrations/tempo/oauth/connection/",
        TempoOAuthDisconnectView.as_view(),
        name="time_tempo_oauth_disconnect",
    ),
    path(
        "time-imports/tempo/preview/",
        TempoImportPreviewView.as_view(),
        name="time_tempo_import_preview",
    ),
    path(
        "time-imports/tempo/commit/",
        TempoImportCommitView.as_view(),
        name="time_tempo_import_commit",
    ),
    path(
        "time-imports/documents/upload/",
        TimeDocumentImportUploadView.as_view(),
        name="time_document_import_upload",
    ),
    path(
        "time-imports/documents/<int:batch_id>/map-columns/",
        TimeDocumentImportColumnMapView.as_view(),
        name="time_document_import_map_columns",
    ),
    path(
        "time-imports/",
        TimeImportBatchListView.as_view(),
        name="time_import_batch_list",
    ),
    path(
        "time-imports/<int:batch_id>/",
        TimeImportBatchDetailView.as_view(),
        name="time_import_batch_detail",
    ),
    path(
        "time-imports/<int:batch_id>/preview/",
        TimeImportBatchPreviewView.as_view(),
        name="time_import_batch_preview",
    ),
    path(
        "time-imports/<int:batch_id>/commit/",
        TimeImportBatchCommitView.as_view(),
        name="time_import_batch_commit",
    ),
    path(
        "employees/<int:employee_id>/tech-leads/",
        EmployeeTechLeadsView.as_view(),
        name="employee_tech_leads",
    ),
    path(
        "employees/<int:employee_id>/bonuses/",
        EmployeeBonusListView.as_view(),
        name="employee_bonuses",
    ),
    path(
        "compensation/overview/",
        CompensationOverviewView.as_view(),
        name="compensation_overview",
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
