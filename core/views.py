import csv
import io
from typing import Any, cast

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .constants import (
    EMPLOYEE_PROFILE_FILTERSET_FIELDS,
    EMPLOYEE_PROFILE_ORDERING_FIELDS,
    EMPLOYEE_PROFILE_SEARCH_FIELDS,
)
from .models import Asset, Assignment, Permission, ReplacementLog, Role, UserProfile
from .permissions import IsHRAdminOrReadOnlyOwnProfile, has_asset_permission
from .serializers import (
    APIRootResponseSerializer,
    AssetCreateSerializer,
    AssetSerializer,
    AssignmentCreateSerializer,
    AssignmentReturnSerializer,
    AssignmentSerializer,
    AvatarUploadSerializer,
    EmployeeProfileSerializer,
    GoogleExchangeSerializer,
    LoginSerializer,
    RegisterSerializer,
    ReplacementLogSerializer,
    TokenSerializer,
    UpdatePermissionsSerializer,
    UpdateRoleSerializer,
    UploadRolePermissionsResponseSerializer,
    UserProfileSerializer,
    UserSerializer,
)
from .shared.employee_utils import soft_delete_employee_profile
from .utils import (
    generate_secure_password,
    generate_unique_username,
    get_role_permissions_bitmap,
    upgrade_google_picture_url,
)


@extend_schema(
    tags=["API Root"],
    responses={200: APIRootResponseSerializer},
    description="List of available API endpoints.",
)
class APIRootView(APIView):
    """API Root: list of available endpoints."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "message": "BloomHub Backend API",
                "endpoints": {
                    "auth": {
                        "register": "POST /api/auth/register/",
                        "login": "POST /api/auth/login/",
                        "google_exchange": "POST /api/auth/google/exchange/",
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


@extend_schema(
    tags=["Auth"],
    request=RegisterSerializer,
    responses={201: TokenSerializer, 400: None},
    description="Register a new user. Returns JWT refresh, access, and user.",
)
class RegisterView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [parsers.JSONParser]

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


@extend_schema(
    tags=["Auth"],
    request=LoginSerializer,
    responses={200: TokenSerializer, 400: None, 401: None},
    description="Login with email and password. Returns JWT refresh, access, and user.",
)
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


@extend_schema(
    tags=["Auth"],
    request=GoogleExchangeSerializer,
    responses={200: TokenSerializer, 400: None, 401: None},
    description="Exchange a Google ID token for native JWT access/refresh tokens.",
)
class GoogleExchangeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = GoogleExchangeSerializer(data=request.data)
        if serializer.is_valid():
            payload = serializer.validated_data["id_token"]

            email = payload.get("email")
            first_name = payload.get("given_name", "")
            last_name = payload.get("family_name", "")
            picture_url = payload.get("picture", "")
            # Upgrade to high-quality version of the Google photo
            if picture_url:
                picture_url = upgrade_google_picture_url(picture_url)

            if not email:
                return Response(
                    {"error": "Token payload missing email"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                user = User.objects.get(email=email)
                profile = user.profile
                # Always refresh avatar_url from Google on every login
                if picture_url:
                    profile.avatar_url = picture_url
                    profile.save(update_fields=["avatar"])
            except User.DoesNotExist:
                # Provision new user
                username = generate_unique_username(email)
                password = generate_secure_password()

                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                )

                # The post_save signal already creates the UserProfile
                profile = user.profile
                update_fields = ["email_address", "full_name"]
                profile.email_address = email
                profile.full_name = f"{first_name} {last_name}".strip()
                if picture_url:
                    profile.avatar_url = picture_url
                    update_fields.append("avatar")
                profile.save(update_fields=update_fields)

            refresh = RefreshToken.for_user(cast(User, user))
            token_data = {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": UserSerializer(user).data,
            }
            return Response(token_data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={205: None, 400: None},
    description='Blacklist the refresh token. Send JSON: { "refresh": "<refresh_token>" }.',
)
class LogoutView(APIView):
    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response(status=status.HTTP_205_RESET_CONTENT)
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Auth"],
    responses={200: UserSerializer},
    description="Get current authenticated user profile. Requires Bearer token.",
)
class UserProfileView(APIView):
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


@extend_schema(
    tags=["Auth"],
    request={"multipart/form-data": AvatarUploadSerializer},
    responses={200: UserSerializer, 400: None},
    description=(
        "Upload a new avatar for the current user. "
        "Accepts multipart/form-data with an `avatar` image file (max 5 MB). "
        "Stores the file via the configured storage backend (Cloudflare R2 when credentials "
        "are set). Returns the updated user object including the new `avatar_url`."
    ),
)
class AvatarUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def post(self, request):
        serializer = AvatarUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        avatar_file = serializer.validated_data["avatar"]
        profile = request.user.profile

        # Save the uploaded file to the storage backend (R2 or local)
        profile.avatar.save("avatar.png", avatar_file, save=False)
        # Clear any stored external URL so the uploaded file takes precedence
        profile.avatar_url = None
        profile.save(update_fields=["avatar", "avatar_url"])

        return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={200: TokenSerializer},
    description='Refresh access token. Send JSON: { "refresh": "<refresh_token>" }. Returns new access + user.',
)
class TokenRefreshViewCustom(TokenRefreshView):
    """Refresh JWT access token; response includes user data."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200 and response.data:
            # Add user data to response
            user = request.user
            response.data["user"] = UserSerializer(user).data
        return response


@extend_schema(
    tags=["Admin"],
    request=None,
    responses={
        200: UploadRolePermissionsResponseSerializer,
        400: None,
        403: None,
    },
    description="Upload a CSV to set role permissions. Staff/superuser only. CSV: role_id, module_name, feature_action, permission (YES/NO), operation_type (override/add/remove/merge).",
)
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


_EMPLOYEE_LIST_PARAMETERS = [
    OpenApiParameter(
        "role__name",
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        description="Exact match on related role name.",
    ),
    OpenApiParameter(
        "department",
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        description="Exact match on department.",
    ),
    OpenApiParameter(
        "is_active",
        OpenApiTypes.BOOL,
        OpenApiParameter.QUERY,
        description="Filter by active flag.",
    ),
    OpenApiParameter(
        "employment_status",
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        description="Exact match on employment status (e.g. active, inactive).",
    ),
    OpenApiParameter(
        "search",
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        description=(
            "Search across full name, email, username, and employee id "
            f"({', '.join(EMPLOYEE_PROFILE_SEARCH_FIELDS)})."
        ),
    ),
    OpenApiParameter(
        "ordering",
        OpenApiTypes.STR,
        OpenApiParameter.QUERY,
        description=(
            "Order results. Prefix with `-` for descending. "
            f"Allowed: {', '.join(EMPLOYEE_PROFILE_ORDERING_FIELDS)}."
        ),
    ),
]


@extend_schema_view(
    list=extend_schema(
        summary="List employee profiles",
        description=(
            "Returns all profiles for HR/Admin; non-admin users only see their own. "
            "Supports filtering, search, and ordering via query parameters."
        ),
        parameters=_EMPLOYEE_LIST_PARAMETERS,
        responses={200: EmployeeProfileSerializer(many=True)},
    ),
    create=extend_schema(
        summary="Create employee profile",
        description=(
            "Creates a `User` and linked `UserProfile`. HR/Admin only. "
            "A random password is generated server-side for the new account."
        ),
        request=EmployeeProfileSerializer,
        responses={201: EmployeeProfileSerializer, 400: None, 403: None},
    ),
    retrieve=extend_schema(
        summary="Retrieve employee profile",
        description="Fetch one profile by id. HR/Admin any id; others only their own.",
        responses={200: EmployeeProfileSerializer, 403: None, 404: None},
    ),
    update=extend_schema(
        summary="Replace employee profile",
        description="Full update (PUT). HR/Admin can edit any profile; others read-only.",
        request=EmployeeProfileSerializer,
        responses={200: EmployeeProfileSerializer, 400: None, 403: None, 404: None},
    ),
    partial_update=extend_schema(
        summary="Patch employee profile",
        description="Partial update (PATCH). Same permission rules as PUT.",
        request=EmployeeProfileSerializer,
        responses={200: EmployeeProfileSerializer, 400: None, 403: None, 404: None},
    ),
    destroy=extend_schema(
        summary="Soft-delete employee profile",
        description=(
            "Does not remove the profile row. Deletes avatar and document files, "
            "removes related assignments and salary rows, clears PII, marks the profile "
            "inactive, and anonymizes plus deactivates the linked Django `User`. "
            "HR/Admin only for arbitrary profiles."
        ),
        responses={204: None, 403: None, 404: None},
    ),
)
@extend_schema(tags=["Employee Profiles"])
class EmployeeProfileViewSet(viewsets.ModelViewSet):
    """
    CRUD endpoints for employee profiles.
    Permissions: HR/Admin can fully manage. Employees can do read-only operations on their own profile.
    """

    serializer_class = EmployeeProfileSerializer
    permission_classes = [IsHRAdminOrReadOnlyOwnProfile]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = EMPLOYEE_PROFILE_FILTERSET_FIELDS
    search_fields = EMPLOYEE_PROFILE_SEARCH_FIELDS
    ordering_fields = EMPLOYEE_PROFILE_ORDERING_FIELDS

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return UserProfile.objects.none()

        perm = IsHRAdminOrReadOnlyOwnProfile()
        if perm._is_hr_admin(user):
            return UserProfile.objects.all()

        return UserProfile.objects.filter(user=user)

    def perform_destroy(self, instance):
        soft_delete_employee_profile(instance)

    @extend_schema(
        summary="Update employee role",
        description=(
            "Sets `role` and recomputes the permissions bitmap from that role's permissions. "
            "HR/Admin only."
        ),
        request=UpdateRoleSerializer,
        responses={200: EmployeeProfileSerializer, 400: None, 404: None, 403: None},
    )
    @action(detail=True, methods=["post"], url_path="update-role")
    def update_role(self, request, pk=None):
        instance = self.get_object()
        serializer = UpdateRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        role_id = serializer.validated_data["role_id"]
        try:
            role = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            return Response(
                {"error": "Role does not exist."}, status=status.HTTP_404_NOT_FOUND
            )

        instance.role = role
        instance.permissions = get_role_permissions_bitmap(role)

        instance.save()
        return Response(self.get_serializer(instance).data)

    @extend_schema(
        summary="Override permissions bitmap",
        description=(
            "Replaces the profile's stored permissions string with the given binary bitmap. "
            "HR/Admin only."
        ),
        request=UpdatePermissionsSerializer,
        responses={200: EmployeeProfileSerializer, 400: None, 403: None},
    )
    @action(detail=True, methods=["post"], url_path="update-permissions")
    def update_permissions(self, request, pk=None):
        instance = self.get_object()
        serializer = UpdatePermissionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Override the additional permissions bitmap (it came in as a valid binary string)
        instance.permissions = serializer.validated_data["permissions_bitmap"]
        instance.save()

        return Response(self.get_serializer(instance).data)


# Asset Management API Views


@extend_schema(
    tags=["Asset Management"],
    responses={200: AssetSerializer(many=True)},
    description="Get list of all assets with filtering options",
    parameters=[
        OpenApiParameter(
            name="status",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Filter by asset status (active, lost, returned, damaged)",
            required=False,
        ),
        OpenApiParameter(
            name="condition",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Filter by asset condition (excellent, good, fair, poor, damaged)",
            required=False,
        ),
        OpenApiParameter(
            name="available",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            description="Filter by availability (true/false)",
            required=False,
        ),
    ],
)
class AssetListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get list of assets with optional filtering, scoped by visibility permissions."""
        user = request.user

        if has_asset_permission(user, "view_all_assets"):
            assets = Asset.objects.all()
        elif has_asset_permission(user, "view_team_assets"):
            # Assets assigned to direct reports of this user
            try:
                profile = user.profile
            except Exception:
                return Response([], status=status.HTTP_200_OK)
            team_ids = profile.direct_reports.values_list("id", flat=True)
            assigned_asset_ids = Assignment.objects.filter(
                employee_id__in=list(team_ids) + [profile.id],
                returned_at__isnull=True,
            ).values_list("asset_id", flat=True)
            assets = Asset.objects.filter(id__in=assigned_asset_ids)
        elif has_asset_permission(user, "view_own_assigned_assets"):
            try:
                profile = user.profile
            except Exception:
                return Response([], status=status.HTTP_200_OK)
            assigned_asset_ids = Assignment.objects.filter(
                employee=profile, returned_at__isnull=True
            ).values_list("asset_id", flat=True)
            assets = Asset.objects.filter(id__in=assigned_asset_ids)
        else:
            assets = Asset.objects.none()

        # Apply additional filters
        status_filter = request.query_params.get("status")
        if status_filter:
            assets = assets.filter(status=status_filter)

        condition_filter = request.query_params.get("condition")
        if condition_filter:
            assets = assets.filter(condition=condition_filter)

        available_filter = request.query_params.get("available")
        if available_filter is not None:
            if available_filter.lower() == "true":
                assets = [asset for asset in assets if asset.is_available]
            else:
                assets = [asset for asset in assets if not asset.is_available]

        serializer = AssetSerializer(assets, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=AssetCreateSerializer,
        responses={201: AssetSerializer, 400: None},
        description="Create a new asset",
    )
    def post(self, request):
        """Create a new asset"""
        serializer = AssetCreateSerializer(data=request.data)
        if serializer.is_valid():
            asset = serializer.save()
            response_serializer = AssetSerializer(asset)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    responses={200: AssetSerializer, 404: None},
    description="Get, update, or delete a specific asset",
)
class AssetDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        """Get asset by ID"""
        try:
            return Asset.objects.get(pk=pk)
        except Asset.DoesNotExist:
            return None

    @extend_schema(
        responses={200: AssetSerializer, 404: None},
        description="Get a specific asset by ID",
    )
    def get(self, request, pk):
        """Get asset details"""
        asset = self.get_object(pk)
        if not asset:
            return Response(
                {"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = AssetSerializer(asset)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=AssetCreateSerializer,
        responses={200: AssetSerializer, 400: None, 404: None},
        description="Update an asset",
    )
    def put(self, request, pk):
        """Update asset"""
        asset = self.get_object(pk)
        if not asset:
            return Response(
                {"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = AssetCreateSerializer(asset, data=request.data)
        if serializer.is_valid():
            asset = serializer.save()
            response_serializer = AssetSerializer(asset)
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        responses={204: None, 404: None},
        description="Delete an asset",
    )
    def delete(self, request, pk):
        """Delete asset"""
        asset = self.get_object(pk)
        if not asset:
            return Response(
                {"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND
            )

        asset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Asset Management"],
    responses={200: AssignmentSerializer(many=True)},
    description="Get list of all assignments with filtering options",
    parameters=[
        OpenApiParameter(
            name="active",
            type=OpenApiTypes.BOOL,
            location=OpenApiParameter.QUERY,
            description="Filter by active assignments (true/false)",
            required=False,
        ),
        OpenApiParameter(
            name="employee",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description="Filter by employee ID",
            required=False,
        ),
        OpenApiParameter(
            name="asset",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description="Filter by asset ID",
            required=False,
        ),
    ],
)
class AssignmentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get list of assignments with optional filtering, scoped by visibility permissions."""
        user = request.user

        if has_asset_permission(user, "view_all_assets"):
            assignments = Assignment.objects.all()
        elif has_asset_permission(user, "view_team_assets"):
            try:
                profile = user.profile
            except Exception:
                return Response([], status=status.HTTP_200_OK)
            team_ids = profile.direct_reports.values_list("id", flat=True)
            assignments = Assignment.objects.filter(
                employee_id__in=list(team_ids) + [profile.id]
            )
        elif has_asset_permission(user, "view_own_assigned_assets"):
            try:
                profile = user.profile
            except Exception:
                return Response([], status=status.HTTP_200_OK)
            assignments = Assignment.objects.filter(employee=profile)
        else:
            assignments = Assignment.objects.none()

        # Apply additional filters
        active_filter = request.query_params.get("active")
        if active_filter is not None:
            if active_filter.lower() == "true":
                assignments = assignments.filter(returned_at__isnull=True)
            else:
                assignments = assignments.filter(returned_at__isnull=False)

        employee_filter = request.query_params.get("employee")
        if employee_filter:
            assignments = assignments.filter(employee_id=employee_filter)

        asset_filter = request.query_params.get("asset")
        if asset_filter:
            assignments = assignments.filter(asset_id=asset_filter)

        serializer = AssignmentSerializer(assignments, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=AssignmentCreateSerializer,
        responses={201: AssignmentSerializer, 400: None, 403: None},
        description="Create a new assignment (HR / designated roles only). `assigned_by` is set automatically to the authenticated user.",
    )
    def post(self, request):
        """Create a new assignment"""
        if not has_asset_permission(request.user, "assign_assets_to_employees"):
            return Response(
                {"error": "You do not have permission to assign assets."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AssignmentCreateSerializer(data=request.data)
        if serializer.is_valid():
            assignment = serializer.save(assigned_by=request.user.profile)
            response_serializer = AssignmentSerializer(assignment)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    responses={200: AssignmentSerializer, 404: None},
    description="Get, update, or delete a specific assignment",
)
class AssignmentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        """Get assignment by ID"""
        try:
            return Assignment.objects.get(pk=pk)
        except Assignment.DoesNotExist:
            return None

    @extend_schema(
        responses={200: AssignmentSerializer, 404: None},
        description="Get a specific assignment by ID",
    )
    def get(self, request, pk):
        """Get assignment details"""
        assignment = self.get_object(pk)
        if not assignment:
            return Response(
                {"error": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = AssignmentSerializer(assignment)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=AssignmentSerializer,
        responses={200: AssignmentSerializer, 400: None, 404: None},
        description="Update an assignment",
    )
    def put(self, request, pk):
        """Update assignment"""
        assignment = self.get_object(pk)
        if not assignment:
            return Response(
                {"error": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = AssignmentSerializer(assignment, data=request.data, partial=True)
        if serializer.is_valid():
            assignment = serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        responses={204: None, 404: None},
        description="Delete an assignment",
    )
    def delete(self, request, pk):
        """Delete assignment"""
        assignment = self.get_object(pk)
        if not assignment:
            return Response(
                {"error": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND
            )

        assignment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Asset Management"],
    request=AssignmentReturnSerializer,
    responses={200: AssignmentSerializer, 400: None, 404: None},
    description="Return an assigned asset",
)
class AssignmentReturnView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        """Get assignment by ID"""
        try:
            return Assignment.objects.get(pk=pk)
        except Assignment.DoesNotExist:
            return None

    @extend_schema(
        request=AssignmentReturnSerializer,
        responses={200: AssignmentSerializer, 400: None, 404: None},
        description="Return an assigned asset (mark as returned and set condition)",
    )
    def post(self, request, pk):
        """Return an assigned asset"""
        if not has_asset_permission(request.user, "process_asset_return"):
            return Response(
                {"error": "You do not have permission to process asset returns."},
                status=status.HTTP_403_FORBIDDEN,
            )
        assignment = self.get_object(pk)
        if not assignment:
            return Response(
                {"error": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = AssignmentReturnSerializer(assignment, data=request.data)
        if serializer.is_valid():
            from django.utils import timezone

            assignment.returned_at = timezone.now()
            assignment.return_condition = serializer.validated_data.get(
                "return_condition"
            )
            assignment.notes = serializer.validated_data.get("notes", assignment.notes)
            assignment.save()

            response_serializer = AssignmentSerializer(assignment)
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    responses={200: ReplacementLogSerializer(many=True)},
    description="Get list of all replacement logs with filtering options",
    parameters=[
        OpenApiParameter(
            name="asset",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description="Filter by asset ID",
            required=False,
        ),
        OpenApiParameter(
            name="replaced_by",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description="Filter by user who performed replacement",
            required=False,
        ),
    ],
)
class ReplacementLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get list of replacement logs with optional filtering"""
        logs = ReplacementLog.objects.all()

        # Apply filters
        asset_filter = request.query_params.get("asset")
        if asset_filter:
            logs = logs.filter(asset_id=asset_filter)

        replaced_by_filter = request.query_params.get("replaced_by")
        if replaced_by_filter:
            logs = logs.filter(replaced_by_id=replaced_by_filter)

        serializer = ReplacementLogSerializer(logs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=ReplacementLogSerializer,
        responses={201: ReplacementLogSerializer, 400: None},
        description="Create a new replacement log",
    )
    def post(self, request):
        """Create a new replacement log"""
        serializer = ReplacementLogSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    responses={200: ReplacementLogSerializer, 404: None},
    description="Get, update, or delete a specific replacement log",
)
class ReplacementLogDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        """Get replacement log by ID"""
        try:
            return ReplacementLog.objects.get(pk=pk)
        except ReplacementLog.DoesNotExist:
            return None

    @extend_schema(
        responses={200: ReplacementLogSerializer, 404: None},
        description="Get a specific replacement log by ID",
    )
    def get(self, request, pk):
        """Get replacement log details"""
        log = self.get_object(pk)
        if not log:
            return Response(
                {"error": "Replacement log not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = ReplacementLogSerializer(log)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=ReplacementLogSerializer,
        responses={200: ReplacementLogSerializer, 400: None, 404: None},
        description="Update a replacement log",
    )
    def put(self, request, pk):
        """Update replacement log"""
        log = self.get_object(pk)
        if not log:
            return Response(
                {"error": "Replacement log not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = ReplacementLogSerializer(log, data=request.data, partial=True)
        if serializer.is_valid():
            log = serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        responses={204: None, 404: None},
        description="Delete a replacement log",
    )
    def delete(self, request, pk):
        """Delete replacement log"""
        log = self.get_object(pk)
        if not log:
            return Response(
                {"error": "Replacement log not found"}, status=status.HTTP_404_NOT_FOUND
            )

        log.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Asset Management"],
    responses={200: UserProfileSerializer(many=True)},
    description="Get list of user profiles for assignment purposes",
)
class UserProfileListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get list of user profiles"""
        profiles = UserProfile.objects.select_related("user").all()
        serializer = UserProfileSerializer(profiles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
