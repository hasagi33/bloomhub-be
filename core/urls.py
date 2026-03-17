from django.urls import path

from .views import (
    APIRootView,
    LoginView,
    LogoutView,
    RegisterView,
    TokenRefreshViewCustom,
    UploadRolePermissionsView,
    UserProfileView,
)

app_name = "core"

urlpatterns = [
    path("", APIRootView.as_view(), name="api_root"),
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/refresh/", TokenRefreshViewCustom.as_view(), name="token_refresh"),
    path("auth/profile/", UserProfileView.as_view(), name="profile"),
    path(
        "admin/upload-role-permissions/",
        UploadRolePermissionsView.as_view(),
        name="upload_role_permissions",
    ),
]
