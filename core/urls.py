from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    APIRootView,
    EmployeeProfileViewSet,
    LoginView,
    LogoutView,
    RegisterView,
    TokenRefreshViewCustom,
    UploadRolePermissionsView,
    UserProfileView,
)

app_name = "core"

router = DefaultRouter()
router.register(r"employees", EmployeeProfileViewSet, basename="employee")

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

urlpatterns += router.urls
