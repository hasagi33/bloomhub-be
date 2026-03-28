from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    APIRootView,
    AssetDetailView,
    AssetListView,
    AssignmentDetailView,
    AssignmentListView,
    AssignmentReturnView,
    AvatarUploadView,
    ChecklistTemplateViewSet,
    EmployeeProfileViewSet,
    GoogleExchangeView,
    LoginView,
    LogoutView,
    RegisterView,
    ReplacementLogDetailView,
    ReplacementLogListView,
    TokenRefreshViewCustom,
    UploadRolePermissionsView,
    UserProfileListView,
    UserProfileView,
)

app_name = "core"

router = DefaultRouter()
router.register(r"employees", EmployeeProfileViewSet, basename="employee")
router.register(
    r"onboarding/templates", ChecklistTemplateViewSet, basename="checklist-template"
)

urlpatterns = [
    path("", APIRootView.as_view(), name="api_root"),
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/google/exchange/", GoogleExchangeView.as_view(), name="google_exchange"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/refresh/", TokenRefreshViewCustom.as_view(), name="token_refresh"),
    path("auth/profile/", UserProfileView.as_view(), name="profile"),
    path("auth/profile/avatar/", AvatarUploadView.as_view(), name="avatar_upload"),
    path(
        "admin/upload-role-permissions/",
        UploadRolePermissionsView.as_view(),
        name="upload_role_permissions",
    ),
    # Asset Management API endpoints
    path("assets/", AssetListView.as_view(), name="asset_list"),
    path("assets/<int:pk>/", AssetDetailView.as_view(), name="asset_detail"),
    path("assignments/", AssignmentListView.as_view(), name="assignment_list"),
    path(
        "assignments/<int:pk>/",
        AssignmentDetailView.as_view(),
        name="assignment_detail",
    ),
    path(
        "assignments/<int:pk>/return/",
        AssignmentReturnView.as_view(),
        name="assignment_return",
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
    path("user-profiles/", UserProfileListView.as_view(), name="user_profile_list"),
]

urlpatterns += router.urls
