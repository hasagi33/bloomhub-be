import csv
import io
from typing import Any, cast

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .models import Permission, Role
from .serializers import (
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
)


class APIRootView(APIView):
    """
    API Root view showing available endpoints
    """

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "message": "BloomHub Backend API",
                "endpoints": {
                    "auth": {
                        "register": "POST /api/auth/register/",
                        "login": "POST /api/auth/login/",
                        "refresh": "POST /api/auth/refresh/",
                        "logout": "POST /api/auth/logout/",
                        "profile": "GET /api/auth/profile/",
                    },
                    "admin": {
                        "upload_role_permissions": "POST /api/admin/upload-role-permissions/",
                        "supported_operations": ["override", "add", "remove", "merge"],
                    },
                    "django_admin": "GET /admin/",
                },
            }
        )


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            assert user is not None
            refresh = RefreshToken.for_user(cast(User, user))
            token_data = {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": UserSerializer(user).data,
            }
            return Response(token_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            data = cast(dict[str, Any], serializer.validated_data)
            try:
                user = User.objects.get(email=data["email"])
                user = authenticate(username=user.username, password=data["password"])
            except User.DoesNotExist:
                user = None

            if user:
                refresh = RefreshToken.for_user(cast(User, user))
                token_data = {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                    "user": UserSerializer(user).data,
                }
                return Response(token_data, status=status.HTTP_200_OK)
            return Response(
                {"error": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LogoutView(APIView):
    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response(status=status.HTTP_205_RESET_CONTENT)
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)


class UserProfileView(APIView):
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


class TokenRefreshViewCustom(TokenRefreshView):
    """
    Custom refresh view that returns user data along with new tokens
    """

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200 and response.data:
            # Add user data to response
            user = request.user
            response.data["user"] = UserSerializer(user).data
        return response


class UploadRolePermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {"error": "Admin access required"}, status=status.HTTP_403_FORBIDDEN
            )

        csv_file = request.FILES.get("file")
        if not csv_file:
            return Response(
                {"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST
            )

        if not csv_file.name.endswith(".csv"):
            return Response(
                {"error": "File must be a CSV"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Save the file to media
        file_path = default_storage.save(
            f"uploads/role_permissions/{csv_file.name}", ContentFile(csv_file.read())
        )

        try:
            # Process the CSV
            csv_file.seek(0)  # Reset file pointer
            file_content = csv_file.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(file_content))

            roles_operations = {}
            for row in reader:
                role_id = row.get("role_id")
                module_name = row.get("module_name")
                feature_action = row.get("feature_action")
                permission_str = row.get("permission")
                operation_type = row.get("operation_type", "override").lower()

                if (
                    not role_id
                    or not module_name
                    or not feature_action
                    or not permission_str
                ):
                    continue

                # Get or create permission
                permission, _ = Permission.objects.get_or_create(
                    module_name=module_name, feature_action=feature_action
                )

                # Get or create role
                role, _ = Role.objects.get_or_create(
                    name=role_id, defaults={"description": f"Role {role_id}"}
                )

                # Initialize operations for role
                if role_id not in roles_operations:
                    roles_operations[role_id] = {
                        "override": set(),
                        "add": set(),
                        "remove": set(),
                        "merge": {},
                    }

                ops = roles_operations[role_id]
                desired = permission_str.upper() == "YES"

                if operation_type == "override" and desired:
                    ops["override"].add(permission)
                elif operation_type == "add" and desired:
                    ops["add"].add(permission)
                elif operation_type == "remove" and desired:
                    ops["remove"].add(permission)
                elif operation_type == "merge":
                    ops["merge"][permission] = desired

            # Now apply operations to roles
            for role_id, ops in roles_operations.items():
                role = Role.objects.get(name=role_id)

                if ops["override"]:
                    role.permissions.set(ops["override"])
                else:
                    if ops["add"]:
                        role.permissions.add(*ops["add"])
                    if ops["remove"]:
                        role.permissions.remove(*ops["remove"])
                    if ops["merge"]:
                        current = set(role.permissions.all())
                        for perm, desired in ops["merge"].items():
                            if desired:
                                current.add(perm)
                            else:
                                current.discard(perm)
                        role.permissions.set(current)

            return Response(
                {
                    "message": "Role permissions uploaded and processed successfully",
                    "file_path": file_path,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
