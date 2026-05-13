import csv
import hashlib
import io
from collections import defaultdict
from datetime import timedelta
from typing import Any, cast
from urllib.parse import unquote, urlparse

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.validators import URLValidator
from django.db import models, transaction
from django.db.models import Avg, Exists, Max, OuterRef, Prefetch, Q
from django.http import HttpResponse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import (
    filters,
    mixins,
    pagination,
    parsers,
    permissions,
    serializers,
    status,
    viewsets,
)
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
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
from .models import (
    Asset,
    AssetStatus,
    Assignment,
    Certificate,
    ChecklistInstance,
    ChecklistTask,
    ChecklistTemplate,
    ConferenceCourseRegistration,
    CPFLevel,
    Department,
    Document,
    DocumentTemplate,
    DocumentType,
    EmployeeDocument,
    EmployeeProfileChangeHistory,
    LeaveAdjustment,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    Notification,
    PerformanceReview,
    PerformanceReviewActionPoint,
    PerformanceReviewAttachment,
    PerformanceReviewHistoryEvent,
    PerformanceReviewNote,
    PerformanceReviewReminder,
    Permission,
    Project,
    ProjectAssignment,
    ReplacementLog,
    Role,
    TaskTemplate,
    TemplateField,
    TemplateGeneratedDocument,
    TrainingEntry,
    UserProfile,
    UserTemplateSnippet,
)
from .permissions import (
    IsEmployeeOrHR,
    IsHRAdminForAdjustment,
    IsHRAdminOrReadOnlyOwnProfile,
    IsManagerForApproval,
    IsReviewCreator,
    IsReviewEditor,
    IsReviewViewer,
    can_attach_review_documents,
    can_edit_review_note,
    can_view_asset,
    can_view_assignment,
    get_asset_capabilities,
    get_asset_permissions,
    get_asset_scope,
    has_asset_permission,
    has_review_permission,
)
from .serializers import (
    APIRootResponseSerializer,
    AssetCreateSerializer,
    AssetExportRequestSerializer,
    AssetSerializer,
    AssignmentCreateSerializer,
    AssignmentRejectReturnSerializer,
    AssignmentRequestReturnSerializer,
    AssignmentReturnSerializer,
    AssignmentSerializer,
    AvatarUploadSerializer,
    BulkIdsSerializer,
    CertificateCreateUpdateSerializer,
    CertificateDetailSerializer,
    CertificateListSerializer,
    ChecklistInstanceCreateSerializer,
    ChecklistInstanceSerializer,
    ChecklistTaskSerializer,
    ChecklistTemplateSerializer,
    ConferenceCourseRegistrationCreateUpdateSerializer,
    ConferenceCourseRegistrationListSerializer,
    DocumentCategoryDefaultUpdateSerializer,
    DocumentCreateSerializer,
    DocumentListSerializer,
    DocumentSignerSerializer,
    DocumentTemplateCreateUpdateSerializer,
    DocumentTemplateDetailSerializer,
    DocumentTemplateListSerializer,
    DocumentTemplatePartialUpdateSerializer,
    DocumentVersionSerializer,
    DocumentVisibilityUpdateSerializer,
    EmployeeCVSerializer,
    EmployeeProfileChangeHistorySerializer,
    EmployeeProfileSerializer,
    GoogleExchangeSerializer,
    LeaveAdjustmentSerializer,
    LeaveBalanceSerializer,
    LeavePolicySerializer,
    LeaveRequestApproveSerializer,
    LeaveRequestCreateSerializer,
    LeaveRequestDetailSerializer,
    LeaveRequestHRApproveSerializer,
    LeaveRequestListSerializer,
    LeaveRequestRejectSerializer,
    LeaveTeamMemberSerializer,
    LoginSerializer,
    NotificationSerializer,
    PerformanceReviewActionPointSerializer,
    PerformanceReviewAttachmentSerializer,
    PerformanceReviewCreateUpdateSerializer,
    PerformanceReviewDetailSerializer,
    PerformanceReviewHistoryEventSerializer,
    PerformanceReviewListSerializer,
    PerformanceReviewNoteSerializer,
    PerformanceReviewReminderSerializer,
    RegisterSerializer,
    ReplacementLogSerializer,
    RequestSignatureSerializer,
    ReturnRequestQueueSerializer,
    SignatureAuditLogSerializer,
    SignDocumentSerializer,
    TemplateGeneratedDocumentSerializer,
    TemplateUseSerializer,
    TokenSerializer,
    TrainingEntryCreateUpdateSerializer,
    TrainingEntryDetailSerializer,
    TrainingEntryListSerializer,
    UpdatePermissionsSerializer,
    UpdateRoleSerializer,
    UploadRolePermissionsResponseSerializer,
    UserProfileSerializer,
    UserSerializer,
    UserTemplateSnippetSerializer,
    VacationCapabilitiesSerializer,
)
from .services.document_query_service import (
    DocumentFilterError,
    apply_document_list_filters,
    document_queryset,
    get_document_for_api,
    get_document_for_response,
)
from .services.document_service import (
    archive_document,
    build_document_inline_preview_url,
    bulk_archive,
    bulk_hard_delete,
    export_documents_csv,
    filter_accessible_documents,
    generate_presigned_url,
    generate_zip_url,
    get_document_category_defaults,
    hard_delete_document,
    is_admin_user,
    is_document_accessible,
    is_hr_or_admin,
    set_document_category_default,
    unarchive_document,
    update_document_visibility,
)
from .services.document_signature_permissions import (
    can_initiate_signature_request,
    can_send_signature_reminder,
    can_sign_for,
)
from .services.document_signature_service import (
    ActiveSignatureWorkflowError,
    get_signature_audit_events,
    remind_pending_signers,
    request_document_signatures,
    reset_document_signatures,
    sign_document,
)
from .services.performance_review_service import (
    materialize_performance_review_reminders,
    sync_performance_review_reminders_for_review,
)
from .services.profile_change_history import log_employee_profile_change, role_value
from .shared.employee_utils import soft_delete_employee_profile
from .utils import (
    clone_template,
    generate_secure_password,
    generate_unique_username,
    get_role_permissions_bitmap,
    get_template_or_404,
    resolve_template_content,
    upgrade_google_picture_url,
    validate_template_fields,
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
                    "performance_reviews": {
                        "reviews": "GET/POST /api/performance-reviews/",
                        "review_detail": "GET/PATCH/DELETE /api/performance-reviews/{id}/",
                        "summary": "GET /api/performance-reviews/summary/",
                        "review_reminders": "GET /api/performance-review-reminders/",
                    },
                    "assets": {
                        "capabilities": "GET /api/assets/capabilities/",
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
                users = User.objects.filter(email=data["email"])
                if users.count() != 1:
                    return Response(
                        {"error": "Invalid credentials"},
                        status=status.HTTP_401_UNAUTHORIZED,
                    )
                user = users.first()
                # Verify user has a profile in core_userprofile
                if not hasattr(user, "profile") or user.profile is None:
                    return Response(
                        {
                            "error": "User profile not found. Please contact administrator."
                        },
                        status=status.HTTP_401_UNAUTHORIZED,
                    )
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
    permission_classes = [AllowAny]

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
    responses={200: UserSerializer},
    description="Get current session information including user profile and permissions. Requires Bearer token.",
)
class SessionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return current user's session information."""
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


@extend_schema(
    tags=["Auth"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "username": {"type": "string"},
                "role": {"type": "string", "nullable": True},
                "permissions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "module_name": {"type": "string"},
                            "feature_action": {"type": "string"},
                            "bit_position": {"type": "integer"},
                        },
                    },
                },
                "total_permissions": {"type": "integer"},
            },
        }
    },
    description="Get current user's permissions based on bearer token. Returns permission list with module_name and feature_action.",
)
class PermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return current user's permissions."""
        profile = request.user.profile

        # Get all permissions the user has
        user_permissions = []
        all_permissions = Permission.objects.all()

        for perm in all_permissions:
            if profile.has_permission(perm):
                user_permissions.append(
                    {
                        "id": perm.id,
                        "module_name": perm.module_name,
                        "feature_action": perm.feature_action,
                        "bit_position": perm.bit_position,
                    }
                )

        return Response(
            {
                "user_id": request.user.id,
                "username": request.user.username,
                "role": profile.role.name if profile.role else None,
                "permissions": user_permissions,
                "total_permissions": len(user_permissions),
            }
        )


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

    def get_permissions(self):
        if self.action == "cvs" and self.request.method == "POST":
            return [IsAuthenticated()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return UserProfile.objects.none()

        perm = IsHRAdminOrReadOnlyOwnProfile()
        if perm._is_hr_admin(user):
            qs = UserProfile.objects.all()
        else:
            qs = UserProfile.objects.filter(user=user)

        action_name = getattr(self, "action", None)
        if action_name == "profile_page_bundle":
            qs = qs.select_related("user", "role").prefetch_related(
                "managers",
                "project_assignments__project",
                "tech_tags",
            )
        elif action_name == "profile_modal_bundle":
            qs = qs.select_related("user", "role").prefetch_related(
                "managers",
                "project_assignments__project",
                "tech_tags",
                Prefetch(
                    "documents",
                    queryset=EmployeeDocument.objects.filter(
                        doc_type=DocumentType.CV
                    ).order_by("-uploaded_at"),
                ),
            )
        return qs

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
        old_role = role_value(instance.role)
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
        log_employee_profile_change(
            employee=instance,
            field=EmployeeProfileChangeHistory.TrackedField.ROLE,
            old_value=old_role,
            new_value=role_value(instance.role),
            changed_by=request.user if request.user.is_authenticated else None,
        )
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

    @extend_schema(
        summary="Get employees eligible to be managers",
        description=(
            "Returns employees with MGR, MGR+, or ADMIN roles who can be assigned as managers. "
            "Optionally filter by role using ?role=MGR or ?role=MGR%2B,ADMIN query parameter (comma-separated for multiple roles, use %2B for + character)."
        ),
        parameters=[
            OpenApiParameter(
                name="role",
                description="Filter by role(s) - comma-separated list (e.g., MGR or MGR,MGR%2B,ADMIN)",
                required=False,
                type=str,
            ),
        ],
        responses={200: EmployeeProfileSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="managers")
    def get_managers(self, request):
        """Get employees who can be assigned as managers (MGR, MGR+, ADMIN roles)"""
        # Get the role filter from query parameters
        role_param = request.query_params.get("role", None)

        # Define which roles can be managers
        manager_roles = ["MGR", "MGR+", "ADMIN"]

        if role_param:
            # URL decode the parameter to handle special characters like +
            role_param = unquote(role_param)
            # Parse comma-separated roles
            requested_roles = [r.strip().upper() for r in role_param.split(",")]

            # Validate all requested roles
            invalid_roles = [r for r in requested_roles if r not in manager_roles]
            if invalid_roles:
                return Response(
                    {
                        "error": f"Invalid role(s): {', '.join(invalid_roles)}. Must be one of: {', '.join(manager_roles)}"
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            manager_roles = requested_roles

        # Get employees with manager roles
        employees = (
            UserProfile.objects.filter(role__name__in=manager_roles)
            .select_related("user", "role")
            .prefetch_related("managers", "project_assignments__project")
            .order_by("full_name")
        )

        serializer = self.get_serializer(employees, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Employee Profiles page bundle",
        description=(
            "Single payload for the HR Employee Profiles screen: current user's permission "
            "bitmask (integer), the same employee list as GET /api/employees/, and shared "
            "lookup data (departments, roles, projects, eligible managers, global CPF levels). "
            "Uses the same RBAC as listing employees."
        ),
        responses={200: None, 403: None},
    )
    @action(detail=False, methods=["get"], url_path="profile-page-bundle")
    def profile_page_bundle(self, request):
        try:
            actor_profile = request.user.profile
        except Exception:
            return Response(
                {"detail": "User profile not found."},
                status=status.HTTP_403_FORBIDDEN,
            )

        bits = actor_profile.computed_permissions_bitmap

        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        employees_payload = serializer.data
        total = queryset.count()

        lookups = {
            "departments": _profile_modal_bundle_departments(),
            "roles": _profile_modal_bundle_roles(),
            "projects": _profile_modal_bundle_projects(),
            "managers": _profile_modal_bundle_managers(request),
            "cpf_levels": list(
                CPFLevel.objects.order_by("name").values_list("name", flat=True)
            ),
        }

        return Response(
            {
                "permissions": bits,
                "permission_bits": bits,
                "permissions_bitmap": bits,
                "employees": {"results": employees_payload, "count": total},
                "lookups": lookups,
            }
        )

    @extend_schema(
        summary="Bulk update employee profile fields",
        description=(
            "Update multiple employee fields in a single request. Accepts any combination of "
            "editable fields (first_name, last_name, full_name, email_address, department, role, "
            "managers, start_date, hire_date, phone_number, address, employment_status, "
            "emergency_contact_name, emergency_contact_phone, birthday, career_level, cpf_level, "
            "tech_tags, avatar, avatar_url). Requires 'Employee Profiles / update_any_profile' permission."
        ),
        request=EmployeeProfileSerializer,
        responses={200: EmployeeProfileSerializer, 400: None, 404: None, 403: None},
    )
    @action(detail=True, methods=["patch"], url_path="bulk-update")
    def bulk_update(self, request, pk=None):
        """
        Update multiple employee profile fields at once.
        Much more efficient than making individual requests.
        Requires 'employee_profiles.update' permission.
        """
        instance = self.get_object()

        # Check permission to update employee profiles
        try:
            perm = Permission.objects.get(
                module_name="Employee Profiles", feature_action="update_any_profile"
            )
            if not request.user.profile.has_permission(perm):
                return Response(
                    {
                        "error": "You do not have permission to update employee profiles."
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        except Permission.DoesNotExist:
            # If permission doesn't exist, deny access for safety
            return Response(
                {"error": "Permission check failed. Contact administrator."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = EmployeeProfileSerializer(
            instance, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Profile modal bundle",
        description=(
            "Single round-trip payload for the employee profile modal: employee detail, "
            "CV versions, shared lookups (departments, roles, projects, managers), and "
            "CPF levels for the employee's role. Optional `sections` selects subsets; omit "
            "to return all. Supports conditional GET via If-None-Match (ETag)."
        ),
        parameters=[
            OpenApiParameter(
                name="sections",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Comma-separated: employee, cvs, lookups, cpf_levels. Default: all."
                ),
            ),
        ],
        responses={200: None, 304: None, 400: None},
    )
    @action(detail=True, methods=["get"], url_path="profile-modal-bundle")
    def profile_modal_bundle(self, request, pk=None):
        profile = self.get_object()

        sections_raw = (request.query_params.get("sections") or "").strip()
        if sections_raw:
            sections_set = frozenset(
                s.strip().lower() for s in sections_raw.split(",") if s.strip()
            )
            invalid = sections_set - _PROFILE_MODAL_BUNDLE_SECTIONS
            if invalid:
                return Response(
                    {
                        "detail": (
                            "Unknown section(s): "
                            f"{', '.join(sorted(invalid))}. "
                            f"Allowed: {', '.join(sorted(_PROFILE_MODAL_BUNDLE_SECTIONS))}."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            sections = sections_set
        else:
            sections = _PROFILE_MODAL_BUNDLE_SECTIONS

        etag = _profile_modal_bundle_etag(profile, sections)
        if_none_match = (request.META.get("HTTP_IF_NONE_MATCH") or "").strip()
        if if_none_match == etag:
            not_modified = Response(status=status.HTTP_304_NOT_MODIFIED)
            not_modified["ETag"] = etag
            return not_modified

        payload: dict[str, Any] = {}

        if "employee" in sections:
            payload["employee"] = EmployeeProfileSerializer(
                profile, context={"request": request}
            ).data

        if "cvs" in sections:
            cvs = profile.documents.filter(doc_type=DocumentType.CV).order_by(
                "-uploaded_at"
            )
            payload["cv_versions"] = EmployeeCVSerializer(cvs, many=True).data

        if "lookups" in sections:
            payload["lookups"] = {
                "departments": _profile_modal_bundle_departments(),
                "roles": _profile_modal_bundle_roles(),
                "projects": _profile_modal_bundle_projects(),
                "managers": _profile_modal_bundle_managers(request),
            }

        if "cpf_levels" in sections:
            payload["cpf_levels_for_role"] = _cpf_level_names_for_profile(profile)

        response = Response(payload)
        response["ETag"] = etag
        return response

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="cvs",
        parser_classes=[
            parsers.MultiPartParser,
            parsers.FormParser,
            parsers.JSONParser,
        ],
    )
    def cvs(self, request, pk=None):
        profile = self.get_object()

        if request.method == "GET":
            cvs = profile.documents.filter(doc_type=DocumentType.CV).order_by(
                "-uploaded_at"
            )
            return Response(EmployeeCVSerializer(cvs, many=True).data)

        if not self._can_upload_cv(request.user, profile):
            return Response(
                {
                    "detail": "You do not have permission to upload CVs for this profile."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        is_file_upload = bool(request.FILES.get("file"))
        if is_file_upload:
            return self._create_file_cv(request, profile)
        return self._create_external_cv(request, profile)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"cvs/(?P<cv_id>\d+)/download",
    )
    def cv_download(self, request, pk=None, cv_id=None):
        profile = self.get_object()
        try:
            cv = profile.documents.get(pk=cv_id, doc_type=DocumentType.CV)
        except EmployeeDocument.DoesNotExist:
            return Response(
                {"detail": "CV record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if cv.source_type == EmployeeDocument.SourceType.EXTERNAL_LINK:
            if not cv.external_url:
                return Response(
                    {"detail": "External URL is not available for this CV."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"url": cv.external_url})

        if not cv.file:
            return Response(
                {"detail": "File is not available for this CV."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"signed_url": cv.file.url})

    def _can_upload_cv(self, user, profile: UserProfile) -> bool:
        perm = IsHRAdminOrReadOnlyOwnProfile()
        if perm._is_hr_admin(user):
            return True
        return getattr(profile, "user_id", None) == getattr(user, "id", None)

    def _create_file_cv(self, request, profile: UserProfile):
        cv_file = request.FILES.get("file")
        if not cv_file:
            return Response(
                {"detail": "File is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_size_bytes = 10 * 1024 * 1024
        if cv_file.size > max_size_bytes:
            return Response(
                {"detail": "File size exceeds 10MB limit."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed_extensions = {".pdf", ".doc", ".docx"}
        filename = getattr(cv_file, "name", "") or ""
        lower_name = filename.lower()
        if not any(lower_name.endswith(ext) for ext in allowed_extensions):
            return Response(
                {"detail": "Only PDF, DOC, and DOCX files are allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        latest_version = (
            profile.documents.filter(doc_type=DocumentType.CV).aggregate(
                max_version=models.Max("version")
            )["max_version"]
            or 0
        )

        with transaction.atomic():
            profile.documents.filter(doc_type=DocumentType.CV, is_current=True).update(
                is_current=False
            )
            doc = EmployeeDocument.objects.create(
                user_profile=profile,
                doc_type=DocumentType.CV,
                file=cv_file,
                version=latest_version + 1,
                is_current=True,
                source_type=EmployeeDocument.SourceType.FILE,
                provider=EmployeeDocument.ProviderType.INTERNAL,
                file_name=filename,
                file_size=cv_file.size,
                mime_type=getattr(cv_file, "content_type", "") or None,
            )

        return Response(EmployeeCVSerializer(doc).data, status=status.HTTP_201_CREATED)

    def _create_external_cv(self, request, profile: UserProfile):
        source_type = request.data.get("source_type", EmployeeDocument.SourceType.FILE)
        if source_type != EmployeeDocument.SourceType.EXTERNAL_LINK:
            return Response(
                {
                    "detail": "Provide a file for uploads, or use source_type=external_link with external_url."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        external_url = request.data.get("external_url")
        if not external_url:
            return Response(
                {"detail": "external_url is required for external_link CVs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            URLValidator()(external_url)
        except DjangoValidationError:
            return Response(
                {"detail": "external_url must be a valid URL."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider = request.data.get("provider", EmployeeDocument.ProviderType.OTHER)
        allowed_providers = {
            EmployeeDocument.ProviderType.CANVA,
            EmployeeDocument.ProviderType.OTHER,
            EmployeeDocument.ProviderType.INTERNAL,
        }
        if provider not in allowed_providers:
            return Response(
                {"detail": "provider must be one of: internal, canva, other."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        latest_version = (
            profile.documents.filter(doc_type=DocumentType.CV).aggregate(
                max_version=models.Max("version")
            )["max_version"]
            or 0
        )

        canva_design_id = request.data.get("canva_design_id")
        if provider == EmployeeDocument.ProviderType.CANVA and not canva_design_id:
            parsed = urlparse(external_url)
            path_parts = [part for part in parsed.path.split("/") if part]
            if path_parts:
                canva_design_id = path_parts[-1]

        with transaction.atomic():
            profile.documents.filter(doc_type=DocumentType.CV, is_current=True).update(
                is_current=False
            )
            doc = EmployeeDocument.objects.create(
                user_profile=profile,
                doc_type=DocumentType.CV,
                version=latest_version + 1,
                is_current=True,
                source_type=EmployeeDocument.SourceType.EXTERNAL_LINK,
                provider=provider,
                external_url=external_url,
                file_name=request.data.get("file_name") or None,
                canva_design_id=canva_design_id or None,
            )

        return Response(EmployeeCVSerializer(doc).data, status=status.HTTP_201_CREATED)


_PROFILE_MODAL_BUNDLE_SECTIONS = frozenset({"employee", "cvs", "lookups", "cpf_levels"})


def _profile_modal_bundle_departments() -> list[str]:
    return list(Department.objects.order_by("name").values_list("name", flat=True))


def _profile_modal_bundle_roles() -> list[dict[str, Any]]:
    return list(Role.objects.all().order_by("name").values("id", "name"))


def _profile_modal_bundle_projects() -> list[dict[str, Any]]:
    assignments = ProjectAssignment.objects.select_related(
        "project", "user_profile__user"
    ).all()

    leaders_by_project: dict[int, list] = defaultdict(list)
    members_by_project: dict[int, list] = defaultdict(list)

    for assignment in assignments:
        ap = assignment.user_profile
        person = {
            "id": ap.user_id,
            "name": ap.full_name or ap.user.username,
        }
        members_by_project[assignment.project_id].append(person)
        if ap.career_level and "lead" in ap.career_level.lower():
            leaders_by_project[assignment.project_id].append(person)

    projects = Project.objects.all().order_by("name")
    return [
        {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "client": project.client,
            "app_stack": project.app_stack,
            "leaders": leaders_by_project.get(project.id, []),
            "members": members_by_project.get(project.id, []),
        }
        for project in projects
    ]


def _profile_modal_bundle_managers(request) -> list[dict[str, Any]]:
    manager_roles = ["MGR", "MGR+", "ADMIN"]
    employees = (
        UserProfile.objects.filter(role__name__in=manager_roles)
        .select_related("user", "role")
        .prefetch_related("managers", "project_assignments__project")
        .order_by("full_name")
    )
    return EmployeeProfileSerializer(
        employees, many=True, context={"request": request}
    ).data


def _cpf_level_names_for_profile(profile: UserProfile) -> list[str]:
    role = getattr(profile, "role", None)
    if not role:
        return []
    candidates = [role.name]
    upper_name = role.name.upper()
    if upper_name != role.name:
        candidates.append(upper_name)
    for lookup_name in candidates:
        try:
            role_obj = Role.objects.prefetch_related("cpf_levels").get(name=lookup_name)
            return list(
                role_obj.cpf_levels.order_by("name").values_list("name", flat=True)
            )
        except Role.DoesNotExist:
            continue
    return []


def _profile_modal_bundle_etag(profile: UserProfile, sections: frozenset[str]) -> str:
    sections_key = ",".join(sorted(sections))
    revision_bits = [
        str(profile.pk),
        profile.updated_at.isoformat() if profile.updated_at else "",
        sections_key,
    ]
    if "lookups" in sections:
        revision_bits.append(
            "l:"
            f"{Department.objects.aggregate(m=Max('id'))['m'] or 0}:"
            f"{Role.objects.aggregate(m=Max('id'))['m'] or 0}:"
            f"{Project.objects.aggregate(m=Max('id'))['m'] or 0}:"
            f"{UserProfile.objects.aggregate(m=Max('id'))['m'] or 0}"
        )
    digest = hashlib.sha256("|".join(revision_bits).encode()).hexdigest()[:32]
    return f'W/"pm-bundle-{digest}"'


# Dropdown/Reference Data Endpoints


class DepartmentListResponseSerializer(serializers.Serializer):
    departments = serializers.ListField(child=serializers.CharField())


class ProjectPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class ProjectSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True, allow_null=True)
    client = serializers.CharField(allow_blank=True, allow_null=True)
    app_stack = serializers.CharField(allow_blank=True, allow_null=True)
    leaders = ProjectPersonSerializer(many=True)
    members = ProjectPersonSerializer(many=True)


class ProjectListResponseSerializer(serializers.Serializer):
    projects = ProjectSummarySerializer(many=True)


class RoleSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class RoleListResponseSerializer(serializers.Serializer):
    roles = RoleSummarySerializer(many=True)


class CPFLevelListResponseSerializer(serializers.Serializer):
    user_role = serializers.CharField(allow_null=True)
    requested_role = serializers.CharField(required=False)
    cpf_levels = serializers.ListField(child=serializers.CharField())
    error = serializers.CharField(required=False)


class TechLeadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    display_name = serializers.CharField()
    projects = serializers.ListField(child=serializers.CharField(), required=False)
    is_cto = serializers.BooleanField(required=False)


class EmployeeTechLeadsDataSerializer(serializers.Serializer):
    tech_leads = TechLeadSerializer(many=True)


class EmployeeTechLeadsResponseSerializer(serializers.Serializer):
    data = EmployeeTechLeadsDataSerializer()


class DepartmentListView(APIView):
    """Get all unique departments"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["departments"],
        responses={200: DepartmentListResponseSerializer},
    )
    def get(self, request):
        departments = Department.objects.order_by("name").values_list("name", flat=True)
        return Response(
            {"departments": list(departments)},
            status=status.HTTP_200_OK,
        )


class ProjectListView(APIView):
    """Get all projects with leaders and members"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["projects"],
        responses={200: ProjectListResponseSerializer},
    )
    def get(self, request):
        from collections import defaultdict

        # Fetch all assignments in one query, grouped by project
        assignments = ProjectAssignment.objects.select_related(
            "project", "user_profile__user"
        ).all()

        leaders_by_project: dict[int, list] = defaultdict(list)
        members_by_project: dict[int, list] = defaultdict(list)

        for assignment in assignments:
            profile = assignment.user_profile
            person = {
                "id": profile.user_id,
                "name": profile.full_name or profile.user.username,
            }
            members_by_project[assignment.project_id].append(person)
            if profile.career_level and "lead" in profile.career_level.lower():
                leaders_by_project[assignment.project_id].append(person)

        projects = Project.objects.all().order_by("name")
        data = [
            {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "client": project.client,
                "app_stack": project.app_stack,
                "leaders": leaders_by_project.get(project.id, []),
                "members": members_by_project.get(project.id, []),
            }
            for project in projects
        ]

        return Response({"projects": data}, status=status.HTTP_200_OK)


class RoleListView(APIView):
    """Get all roles"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["roles"],
        responses={200: RoleListResponseSerializer},
    )
    def get(self, request):
        roles = Role.objects.all().order_by("name").values("id", "name")
        return Response({"roles": list(roles)}, status=status.HTTP_200_OK)


class CPFLevelListView(APIView):
    """Get CPF levels, optionally filtered by role"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["cpf-levels"],
        responses={200: CPFLevelListResponseSerializer},
    )
    def get(self, request, role=None):
        # Get user's role
        user_profile = (
            request.user.profile if hasattr(request.user, "profile") else None
        )
        user_role = (
            user_profile.role.name if user_profile and user_profile.role else None
        )

        # If role parameter is provided, filter by that role
        if role:
            try:
                # Convert role parameter to uppercase for matching
                role_upper = role.upper()
                role_obj = Role.objects.get(name=role_upper)
                cpf_levels = role_obj.cpf_levels.order_by("name").values_list(
                    "name", flat=True
                )

                return Response(
                    {
                        "requested_role": role_upper,
                        "user_role": user_role,
                        "cpf_levels": list(cpf_levels),
                    },
                    status=status.HTTP_200_OK,
                )
            except Role.DoesNotExist:
                return Response(
                    {
                        "error": f"Role '{role}' not found",
                        "requested_role": role,
                        "user_role": user_role,
                        "cpf_levels": [],
                    },
                    status=status.HTTP_200_OK,
                )

        # Otherwise return all CPF levels
        cpf_levels = CPFLevel.objects.order_by("name").values_list("name", flat=True)

        return Response(
            {"user_role": user_role, "cpf_levels": list(cpf_levels)},
            status=status.HTTP_200_OK,
        )


class EmployeeTechLeadsView(APIView):
    """
    Get tech leads for a specific employee based on their project assignments.
    Returns list of tech leads with their associated project names.
    Format: "John Doe (Project Name)"

    If employee is tech lead level, returns CTO info instead.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["employees"],
        responses={
            200: EmployeeTechLeadsResponseSerializer,
            404: OpenApiTypes.OBJECT,
        },
    )
    def get(self, request, employee_id):
        try:
            employee = UserProfile.objects.get(user_id=employee_id)
        except UserProfile.DoesNotExist:
            return Response(
                {"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND
            )

        # If employee is tech lead level, return CTO
        if employee.career_level and "lead" in employee.career_level.lower():
            cto = UserProfile.objects.filter(career_level__icontains="CTO").first()
            if cto:
                return Response(
                    {
                        "data": {
                            "tech_leads": [
                                {
                                    "id": cto.user.id,
                                    "name": cto.full_name or cto.user.get_full_name(),
                                    "display_name": f"{cto.full_name or cto.user.get_full_name()} (CTO)",
                                    "is_cto": True,
                                }
                            ]
                        }
                    },
                    status=status.HTTP_200_OK,
                )

        # Get all projects assigned to this employee
        assignments = list(employee.project_assignments.select_related("project").all())
        project_names = {a.project_id: a.project.name for a in assignments}
        project_ids = list(project_names.keys())

        if not project_ids:
            return Response(
                {"data": {"tech_leads": []}},
                status=status.HTTP_200_OK,
            )

        # Fetch all leads for those projects in a single query
        leads = (
            UserProfile.objects.filter(
                project_assignments__project_id__in=project_ids,
                career_level__icontains="lead",
            )
            .distinct()
            .values(
                "user_id",
                "full_name",
                "user__username",
                "project_assignments__project_id",
            )
        )

        tech_leads_dict: dict[int, dict[str, Any]] = (
            {}
        )  # To avoid duplicates across projects

        for lead in leads:
            lead_id = lead["user_id"]
            project_id = lead["project_assignments__project_id"]

            if project_id not in project_names:
                continue

            lead_name = lead["full_name"] or lead["user__username"]
            if lead_id not in tech_leads_dict:
                tech_leads_dict[lead_id] = {
                    "id": lead_id,
                    "name": lead_name,
                    "projects": [],
                }
            project_name = project_names[project_id]
            if project_name not in tech_leads_dict[lead_id]["projects"]:
                tech_leads_dict[lead_id]["projects"].append(project_name)

        # Format the response with display names
        tech_leads_list = [
            {
                "id": lead["id"],
                "name": lead["name"],
                "display_name": f"{lead['name']} ({', '.join(lead['projects'])})",
                "projects": lead["projects"],
            }
            for lead in tech_leads_dict.values()
        ]

        return Response(
            {"data": {"tech_leads": tech_leads_list}},
            status=status.HTTP_200_OK,
        )


class EmployeeProfileChangeHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["employees"],
        responses={200: EmployeeProfileChangeHistorySerializer(many=True)},
    )
    def get(self, request, employee_id):
        try:
            employee_profile = UserProfile.objects.get(user_id=employee_id)
        except UserProfile.DoesNotExist:
            return Response(
                {"detail": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_hr_admin = IsHRAdminOrReadOnlyOwnProfile()._is_hr_admin(request.user)
        if not is_hr_admin and request.user.id != employee_id:
            return Response(
                {"detail": "You do not have permission to view this history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        history = (
            EmployeeProfileChangeHistory.objects.filter(employee=employee_profile)
            .select_related("changed_by")
            .order_by("-changed_at")
        )
        serializer = EmployeeProfileChangeHistorySerializer(history, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# Asset Management API Views


def _active_assignment_queryset():
    return Assignment.objects.filter(returned_at__isnull=True).select_related(
        "employee__user"
    )


def _with_asset_availability(assets):
    active_assignments = _active_assignment_queryset()
    return assets.annotate(
        has_active_assignment=Exists(
            Assignment.objects.filter(
                asset_id=OuterRef("pk"),
                returned_at__isnull=True,
            )
        )
    ).prefetch_related(
        Prefetch(
            "assignments",
            queryset=active_assignments,
            to_attr="active_assignments",
        )
    )


def _get_assets_for_user(user):
    if has_asset_permission(user, "view_all_assets"):
        return Asset.objects.all()
    if has_asset_permission(user, "view_team_assets"):
        try:
            profile = user.profile
        except Exception:
            return Asset.objects.none()
        team_ids = profile.direct_reports.values_list("id", flat=True)
        assigned_asset_ids = Assignment.objects.filter(
            employee_id__in=list(team_ids) + [profile.id],
            returned_at__isnull=True,
        ).values_list("asset_id", flat=True)
        return Asset.objects.filter(id__in=assigned_asset_ids)
    if has_asset_permission(user, "view_own_assets"):
        try:
            profile = user.profile
        except Exception:
            return Asset.objects.none()
        assigned_asset_ids = Assignment.objects.filter(
            employee=profile, returned_at__isnull=True
        ).values_list("asset_id", flat=True)
        return Asset.objects.filter(id__in=assigned_asset_ids)
    return Asset.objects.none()


def _apply_asset_filters(assets, filter_data):
    status_filter = filter_data.get("status")
    if status_filter:
        assets = assets.filter(status=status_filter)

    condition_filter = filter_data.get("condition")
    if condition_filter:
        assets = assets.filter(condition=condition_filter)

    category_filter = filter_data.get("category")
    if category_filter:
        assets = assets.filter(category=category_filter)

    assigned_employee_id = filter_data.get("assigned_employee_id")
    if assigned_employee_id:
        assets = assets.filter(
            assignments__employee_id=assigned_employee_id,
            assignments__returned_at__isnull=True,
        ).distinct()

    available_filter = filter_data.get("available")
    assets = _with_asset_availability(assets)
    if available_filter is not None:
        if available_filter:
            assets = assets.filter(
                status=AssetStatus.ACTIVE,
                has_active_assignment=False,
            )
        else:
            assets = assets.filter(
                Q(has_active_assignment=True) | ~Q(status=AssetStatus.ACTIVE)
            )

    return assets


def _assignment_update_permission(request) -> str:
    return_fields = {
        "returned_at",
        "return_request_status",
        "return_requested_by",
        "return_requested_at",
        "return_reviewed_by",
        "return_reviewed_at",
        "return_rejection_reason",
        "return_description",
        "return_checklist",
        "return_condition",
    }
    if return_fields.intersection(set(request.data.keys())):
        return "process_asset_return"
    return "assign_assets"


@extend_schema(
    tags=["Asset Management"],
    responses={200: OpenApiTypes.OBJECT},
    description="Return canonical asset permissions and UI capabilities for the current user.",
)
class AssetCapabilitiesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "permissions": get_asset_permissions(request.user),
                "capabilities": get_asset_capabilities(request.user),
                "scope": get_asset_scope(request.user),
            },
            status=status.HTTP_200_OK,
        )


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
            name="category",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description="Filter by asset category (laptops, phones, monitors, headphones, cameras, vehicles, furniture, other)",
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
        assets = _get_assets_for_user(request.user)
        available_query = request.query_params.get("available")
        filter_data = {
            "status": request.query_params.get("status"),
            "condition": request.query_params.get("condition"),
            "category": request.query_params.get("category"),
            "available": (
                available_query.lower() == "true"
                if available_query is not None
                else None
            ),
        }
        assets = _apply_asset_filters(assets, filter_data)

        serializer = AssetSerializer(assets, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=AssetCreateSerializer,
        responses={201: AssetSerializer, 400: None},
        description="Create a new asset",
    )
    def post(self, request):
        """Create a new asset"""
        if not has_asset_permission(request.user, "configure_asset_types"):
            return Response(
                {"error": "You do not have permission to create assets."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AssetCreateSerializer(data=request.data)
        if serializer.is_valid():
            asset = serializer.save()
            response_serializer = AssetSerializer(asset, context={"request": request})
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    request=AssetExportRequestSerializer,
    responses={200: OpenApiTypes.BINARY, 400: None, 403: None},
    description=(
        "Export assets as CSV. Requires `export_inventory` permission. "
        "Supports optional filters and optional assignment columns."
    ),
)
class AssetExportView(APIView):
    permission_classes = [IsAuthenticated]

    ASSIGNMENT_COLUMNS = [
        "current_assignment_id",
        "current_assignment_employee_id",
        "current_assignment_employee_name",
        "current_assignment_assigned_at",
    ]

    def post(self, request):
        if not has_asset_permission(request.user, "export_inventory"):
            return Response(
                {"error": "You do not have permission to export inventory."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AssetExportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        selected_fields = (
            payload.get("fields") or AssetExportRequestSerializer.ASSET_FIELDS
        )
        include_assignment = payload.get("include_assignment", True)
        filter_data = payload.get("filters") or {}

        assets = _apply_asset_filters(Asset.objects.all(), filter_data)

        output = io.StringIO()
        writer = csv.writer(output)
        headers = list(selected_fields)
        if include_assignment:
            headers.extend(self.ASSIGNMENT_COLUMNS)
        writer.writerow(headers)

        for asset in assets:
            row = [
                self._serialize_asset_field(asset, field_name)
                for field_name in selected_fields
            ]
            if include_assignment:
                row.extend(self._assignment_values(asset))
            writer.writerow(row)

        filename = payload.get("filename") or "asset_export.csv"
        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    def _serialize_asset_field(self, asset, field_name):
        if field_name == "is_under_warranty":
            value = asset.is_under_warranty
        elif field_name == "is_available":
            value = asset.is_available
        else:
            value = getattr(asset, field_name, None)

        if value is None:
            return ""
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    def _assignment_values(self, asset):
        assignment = asset.current_assignment
        if not assignment:
            return ["", "", "", ""]

        employee_name = (
            assignment.employee.user.get_full_name()
            or assignment.employee.user.username
        )
        return [
            str(assignment.id),
            str(assignment.employee_id),
            employee_name,
            assignment.assigned_at.isoformat() if assignment.assigned_at else "",
        ]


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
            return _with_asset_availability(Asset.objects).get(pk=pk)
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
        if not can_view_asset(request.user, asset):
            return Response(
                {"error": "You do not have permission to view this asset."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AssetSerializer(asset, context={"request": request})
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
        if not has_asset_permission(request.user, "configure_asset_types"):
            return Response(
                {"error": "You do not have permission to update assets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AssetCreateSerializer(asset, data=request.data)
        if serializer.is_valid():
            asset = serializer.save()
            response_serializer = AssetSerializer(asset, context={"request": request})
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        request=AssetCreateSerializer,
        responses={200: AssetSerializer, 400: None, 404: None},
        description="Partially update an asset",
    )
    def patch(self, request, pk):
        """Partially update asset"""
        asset = self.get_object(pk)
        if not asset:
            return Response(
                {"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if not has_asset_permission(request.user, "configure_asset_types"):
            return Response(
                {"error": "You do not have permission to update assets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AssetCreateSerializer(asset, data=request.data, partial=True)
        if serializer.is_valid():
            asset = serializer.save()
            response_serializer = AssetSerializer(asset, context={"request": request})
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
        if not has_asset_permission(request.user, "configure_asset_types"):
            return Response(
                {"error": "You do not have permission to delete assets."},
                status=status.HTTP_403_FORBIDDEN,
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
        elif has_asset_permission(user, "view_own_assets"):
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

        serializer = AssignmentSerializer(
            assignments, many=True, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=AssignmentCreateSerializer,
        responses={201: AssignmentSerializer, 400: None, 403: None},
        description="Create a new assignment (HR / designated roles only). `assigned_by` is set automatically to the authenticated user.",
    )
    def post(self, request):
        """Create a new assignment"""
        if not has_asset_permission(request.user, "assign_assets"):
            return Response(
                {"error": "You do not have permission to assign assets."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AssignmentCreateSerializer(data=request.data)
        if serializer.is_valid():
            assignment = serializer.save(assigned_by=request.user.profile)
            response_serializer = AssignmentSerializer(
                assignment, context={"request": request}
            )
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
        if not can_view_assignment(request.user, assignment):
            return Response(
                {"error": "You do not have permission to view this assignment."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AssignmentSerializer(assignment, context={"request": request})
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
        required_permission = _assignment_update_permission(request)
        if not has_asset_permission(request.user, required_permission):
            return Response(
                {"error": "You do not have permission to update assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AssignmentSerializer(assignment, data=request.data, partial=True)
        if serializer.is_valid():
            assignment = serializer.save()
            return Response(
                AssignmentSerializer(assignment, context={"request": request}).data,
                status=status.HTTP_200_OK,
            )
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
        if not has_asset_permission(request.user, "assign_assets"):
            return Response(
                {"error": "You do not have permission to delete assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        assignment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Asset Management"],
    request=AssignmentRequestReturnSerializer,
    responses={200: AssignmentSerializer, 400: None, 403: None, 404: None},
    description="Request return for an assigned asset",
)
class AssignmentRequestReturnView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return Assignment.objects.get(pk=pk)
        except Assignment.DoesNotExist:
            return None

    def post(self, request, pk):
        if not has_asset_permission(request.user, "initiate_asset_return"):
            return Response(
                {"error": "You do not have permission to initiate asset returns."},
                status=status.HTTP_403_FORBIDDEN,
            )

        assignment = self.get_object(pk)
        if not assignment:
            return Response(
                {"error": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not request.user.is_staff and not request.user.is_superuser:
            try:
                requester_profile = request.user.profile
            except Exception:
                return Response(
                    {"error": "User profile not found."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if assignment.employee_id != requester_profile.id:
                return Response(
                    {"error": "You can only request return for your own assignments."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer = AssignmentRequestReturnSerializer(assignment, data=request.data)
        if serializer.is_valid():
            from django.utils import timezone

            assignment.return_request_status = Assignment.ReturnRequestStatus.PENDING
            assignment.return_requested_by = request.user.profile
            assignment.return_requested_at = timezone.now()
            assignment.return_reviewed_by = None
            assignment.return_reviewed_at = None
            assignment.return_rejection_reason = None
            assignment.return_description = serializer.validated_data.get(
                "return_description", assignment.return_description
            )
            assignment.return_checklist = serializer.validated_data.get(
                "return_checklist", assignment.return_checklist
            )
            assignment.notes = serializer.validated_data.get("notes", assignment.notes)
            assignment.save(
                update_fields=[
                    "return_request_status",
                    "return_requested_by",
                    "return_requested_at",
                    "return_reviewed_by",
                    "return_reviewed_at",
                    "return_rejection_reason",
                    "return_description",
                    "return_checklist",
                    "notes",
                ]
            )
            return Response(
                AssignmentSerializer(assignment, context={"request": request}).data,
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    parameters=[
        OpenApiParameter(
            name="status",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Filter return requests by status (pending, approved, rejected). Defaults to pending.",
        ),
    ],
    responses={200: ReturnRequestQueueSerializer(many=True), 403: None},
    description="List return requests for HR/Admin review queue.",
)
class ReturnRequestListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not has_asset_permission(request.user, "process_asset_return"):
            return Response(
                {"error": "You do not have permission to view return requests."},
                status=status.HTTP_403_FORBIDDEN,
            )

        allowed_statuses = {
            Assignment.ReturnRequestStatus.PENDING,
            Assignment.ReturnRequestStatus.APPROVED,
            Assignment.ReturnRequestStatus.REJECTED,
        }
        status_filter = request.query_params.get(
            "status", Assignment.ReturnRequestStatus.PENDING
        )
        if status_filter not in allowed_statuses:
            return Response(
                {"error": "Invalid status filter. Use pending, approved, or rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        requests_qs = Assignment.objects.filter(
            return_request_status=status_filter
        ).select_related("asset", "employee__user", "return_requested_by__user")
        serializer = ReturnRequestQueueSerializer(
            requests_qs, many=True, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Asset Management"],
    request=AssignmentReturnSerializer,
    responses={200: AssignmentSerializer, 400: None, 403: None, 404: None},
    description="Approve and complete a pending asset return",
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
        responses={200: AssignmentSerializer, 400: None, 403: None, 404: None},
        description="Return an assigned asset (mark as returned and set condition)",
    )
    def post(self, request, pk):
        """Return an assigned asset"""
        if not has_asset_permission(request.user, "process_asset_return"):
            return Response(
                {"error": "You do not have permission to approve asset returns."},
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
            assignment.return_request_status = Assignment.ReturnRequestStatus.APPROVED
            assignment.return_reviewed_by = request.user.profile
            assignment.return_reviewed_at = timezone.now()
            assignment.return_rejection_reason = None
            assignment.return_condition = serializer.validated_data.get(
                "return_condition"
            )
            assignment.notes = serializer.validated_data.get("notes", assignment.notes)
            assignment.save()

            response_serializer = AssignmentSerializer(
                assignment, context={"request": request}
            )
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    request=AssignmentRejectReturnSerializer,
    responses={200: AssignmentSerializer, 400: None, 403: None, 404: None},
    description="Reject a pending asset return request",
)
class AssignmentRejectReturnView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return Assignment.objects.get(pk=pk)
        except Assignment.DoesNotExist:
            return None

    def post(self, request, pk):
        if not has_asset_permission(request.user, "process_asset_return"):
            return Response(
                {"error": "You do not have permission to reject asset returns."},
                status=status.HTTP_403_FORBIDDEN,
            )

        assignment = self.get_object(pk)
        if not assignment:
            return Response(
                {"error": "Assignment not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = AssignmentRejectReturnSerializer(data=request.data)
        if serializer.is_valid():
            if (
                assignment.return_request_status
                != Assignment.ReturnRequestStatus.PENDING
            ):
                return Response(
                    {
                        "error": "This assignment does not have a pending return request."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            from django.utils import timezone

            assignment.return_request_status = Assignment.ReturnRequestStatus.REJECTED
            assignment.return_reviewed_by = request.user.profile
            assignment.return_reviewed_at = timezone.now()
            assignment.return_rejection_reason = serializer.validated_data.get(
                "rejection_reason", ""
            )
            assignment.save(
                update_fields=[
                    "return_request_status",
                    "return_reviewed_by",
                    "return_reviewed_at",
                    "return_rejection_reason",
                ]
            )
            return Response(
                AssignmentSerializer(assignment, context={"request": request}).data,
                status=status.HTTP_200_OK,
            )
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
        if not has_asset_permission(request.user, "view_asset_history"):
            return Response(
                {"error": "You do not have permission to view asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )
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
        if not has_asset_permission(request.user, "log_asset_lost"):
            return Response(
                {"error": "You do not have permission to log asset changes."},
                status=status.HTTP_403_FORBIDDEN,
            )
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
        if not has_asset_permission(request.user, "view_asset_history"):
            return Response(
                {"error": "You do not have permission to view asset history."},
                status=status.HTTP_403_FORBIDDEN,
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
        if not has_asset_permission(request.user, "log_asset_lost"):
            return Response(
                {"error": "You do not have permission to update asset history."},
                status=status.HTTP_403_FORBIDDEN,
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
        if not has_asset_permission(request.user, "log_asset_lost"):
            return Response(
                {"error": "You do not have permission to delete asset history."},
                status=status.HTTP_403_FORBIDDEN,
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
        if not has_asset_permission(request.user, "assign_assets"):
            return Response(
                {"error": "You do not have permission to list assignment targets."},
                status=status.HTTP_403_FORBIDDEN,
            )
        profiles = UserProfile.objects.select_related("user").all()
        serializer = UserProfileSerializer(profiles, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ──────────────────────────────────────────
# Onboarding / Offboarding Views
# ──────────────────────────────────────────


class IsHROnly(permissions.BasePermission):
    """Only HR admins can manage checklist templates."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if getattr(request.user, "is_staff", False) or getattr(
            request.user, "is_superuser", False
        ):
            return True

        # Fall back to onboarding-specific permissions for non-staff, non-superusers
        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            return False

        onboarding_perms = Permission.objects.filter(
            module_name="Onboarding",
            feature_action__in=["create_checklist_templates", "configure_templates"],
        )

        for perm in onboarding_perms:
            if profile.has_permission(perm):
                return True

        return False


@extend_schema(tags=["Onboarding / Offboarding"])
class ChecklistTemplateViewSet(viewsets.ModelViewSet):
    """
    CRUD endpoints for checklist templates.
    HR only — create, update, delete, and clone templates.
    """

    serializer_class = ChecklistTemplateSerializer
    permission_classes = [IsHROnly]
    queryset = ChecklistTemplate.objects.prefetch_related("task_templates").all()

    @extend_schema(
        summary="Clone a checklist template",
        description="Creates a full copy of an existing template including all its tasks.",
        responses={201: ChecklistTemplateSerializer},
    )
    @action(detail=True, methods=["post"], url_path="clone")
    def clone(self, request, pk=None):
        original = self.get_object()
        cloned = ChecklistTemplate.objects.create(
            name=f"{original.name} (Copy)",
            type=original.type,
            role_responsible=original.role_responsible,
        )
        for task in original.task_templates.all():
            TaskTemplate.objects.create(
                checklist_template=cloned,
                title=task.title,
                order=task.order,
            )
        serializer = self.get_serializer(cloned)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Onboarding / Offboarding"])
class ChecklistTaskViewSet(viewsets.ModelViewSet):
    serializer_class = ChecklistTaskSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]
    queryset = ChecklistTask.objects.select_related(
        "checklist_instance",
        "checklist_instance__employee__user",
        "checklist_instance__template",
        "task_template",
        "assigned_to__user",
    ).all()

    def list(self, request, *args, **kwargs):
        return self.my_tasks(request)

    @extend_schema(
        summary="Update task status",
        description=(
            "Updates the status of a checklist task. "
            "Only the assigned user or HR/staff may update. "
            "Setting status to 'done' automatically records completed_at."
        ),
        responses={200: ChecklistTaskSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        task = self.get_object()

        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            return Response(
                {"detail": "User profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_hr_or_staff = (
            request.user.is_staff
            or request.user.is_superuser
            or (profile.role and profile.role.name.lower() == "hr")
        )
        if not (is_hr_or_staff or task.assigned_to == profile):
            return Response(
                {"detail": "Only the assigned user or HR/staff can update this task."},
                status=status.HTTP_403_FORBIDDEN,
            )

        new_status = request.data.get("status")
        valid_statuses = {s.value for s in ChecklistTask.Status}
        if new_status not in valid_statuses:
            return Response(
                {
                    "detail": f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        task.status = new_status
        task.completed_at = (
            timezone.now() if new_status == ChecklistTask.Status.DONE else None
        )
        task.save(update_fields=["status", "completed_at"])

        return Response(self.get_serializer(task).data)

    @extend_schema(
        summary="Get tasks assigned to the authenticated user",
        responses={200: ChecklistTaskSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="my-tasks")
    def my_tasks(self, request):
        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            # Superusers/staff without a profile have no assigned tasks
            return Response([])

        tasks = self.queryset.filter(assigned_to=profile)
        serializer = self.get_serializer(tasks, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Get onboarding tasks for a specific employee",
        description=(
            "HR users and managers can retrieve checklist tasks for a specific employee. "
            "Managers may only see tasks for employees they manage."
        ),
        responses={200: ChecklistTaskSerializer(many=True)},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"employee/(?P<employee_id>[^/.]+)",
    )
    def employee_tasks(self, request, employee_id=None):
        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            profile = None

        try:
            employee_profile = UserProfile.objects.get(pk=employee_id)
        except UserProfile.DoesNotExist:
            return Response(
                {"detail": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        can_view = (
            request.user.is_staff
            or request.user.is_superuser
            or (
                profile is not None
                and profile.role
                and profile.role.name.lower() == "hr"
            )
            or (
                profile is not None
                and employee_profile.managers.filter(pk=profile.pk).exists()
            )
        )

        if not can_view:
            return Response(
                {"detail": "You do not have permission to view this employee's tasks."},
                status=status.HTTP_403_FORBIDDEN,
            )

        tasks = self.queryset.filter(checklist_instance__employee=employee_profile)
        serializer = self.get_serializer(tasks, many=True)
        return Response(serializer.data)


@extend_schema(tags=["Onboarding / Offboarding"])
class ChecklistInstanceViewSet(
    viewsets.GenericViewSet,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
):
    serializer_class = ChecklistInstanceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ChecklistInstance.objects.select_related(
            "employee__user", "template"
        ).all()

    def _require_hr_or_manager(self, request):
        try:
            profile = UserProfile.objects.select_related("role").get(user=request.user)
        except UserProfile.DoesNotExist:
            # Staff/superusers are privileged even without a profile record
            if request.user.is_staff or request.user.is_superuser:
                return None, None
            return None, Response(
                {"detail": "User profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        is_privileged = (
            request.user.is_staff
            or request.user.is_superuser
            or (profile.role and profile.role.name.lower() in {"hr", "manager"})
        )
        if not is_privileged:
            return None, Response(
                {"detail": "Only HR or managers can manage checklists."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return profile, None

    @extend_schema(
        summary="Assign a checklist template to an employee",
        request=ChecklistInstanceCreateSerializer,
        responses={201: ChecklistInstanceSerializer},
    )
    def create(self, request, *args, **kwargs):
        creator_profile, err = self._require_hr_or_manager(request)
        if err:
            return err

        serializer = ChecklistInstanceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee_id = serializer.validated_data["employee"]
        template_id = serializer.validated_data["template"]
        due_date = serializer.validated_data.get("due_date")
        task_due_dates = serializer.validated_data.get("task_due_dates") or {}

        try:
            employee = UserProfile.objects.get(pk=employee_id)
        except UserProfile.DoesNotExist:
            return Response(
                {"detail": "Employee not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            template = ChecklistTemplate.objects.get(pk=template_id)
        except ChecklistTemplate.DoesNotExist:
            return Response(
                {"detail": "Template not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if ChecklistInstance.objects.filter(
            employee=employee, template=template
        ).exists():
            return Response(
                {"detail": "This checklist is already assigned to this employee."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # post_save signal calls create_tasks_from_template(); due_date and created_by
        # must be set before save so the signal can read them.
        instance = ChecklistInstance.objects.create(
            employee=employee,
            template=template,
            due_date=due_date,
            created_by=creator_profile,
        )

        # Override individual task due dates where provided (key = task_template_id).
        if task_due_dates:
            for task in instance.tasks.all():
                key = str(task.task_template_id)
                if key in task_due_dates:
                    task.due_date = task_due_dates[key]
                    task.save(update_fields=["due_date"])

        return Response(
            ChecklistInstanceSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="List all checklist instances",
        responses={200: ChecklistInstanceSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        _, err = self._require_hr_or_manager(request)
        if err:
            return err
        return super().list(request, *args, **kwargs)

    @extend_schema(summary="Remove a checklist instance and all its tasks")
    def destroy(self, request, *args, **kwargs):
        _, err = self._require_hr_or_manager(request)
        if err:
            return err
        return super().destroy(request, *args, **kwargs)


# ──────────────────────────────────────────
# Leave Management Views
# ──────────────────────────────────────────


@extend_schema(tags=["Leave Management"])
class LeavePolicyViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for leave policies (read-only).
    Lists organizational leave policies.
    """

    queryset = LeavePolicy.objects.all()
    serializer_class = LeavePolicySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["leave_type", "requires_approval"]


@extend_schema(tags=["Leave Management"])
class LeaveBalanceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for leave balances.
    Employees can view their own balances, HR can view all.
    """

    serializer_class = LeaveBalanceSerializer
    permission_classes = [IsAuthenticated, IsEmployeeOrHR]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["leave_type", "year"]

    def get_queryset(self):
        """Filter balances based on user permissions."""
        user = self.request.user

        # HR admins can see all balances
        if user.is_staff or user.is_superuser:
            return LeaveBalance.objects.all().select_related("employee__user")

        # Regular employees see only their own
        try:
            return LeaveBalance.objects.filter(employee=user.profile).select_related(
                "employee__user"
            )
        except Exception:
            return LeaveBalance.objects.none()

    @extend_schema(
        request={
            "employee_id": int,
            "leave_type": str,
            "allocated": int,
            "reason": str,
        },
        responses={200: LeaveBalanceSerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsHRAdminForAdjustment],
    )
    def adjust(self, request, pk=None):
        """Adjust leave balance (admin only)."""

        from core.services.leave_service import adjust_leave_balance

        balance = self.get_object()
        new_allocated = request.data.get("allocated")
        reason = request.data.get("reason", "Manual adjustment")

        if new_allocated is None:
            return Response(
                {"error": "allocated field is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            new_allocated = int(new_allocated)
        except (ValueError, TypeError):
            return Response(
                {"error": "allocated must be a valid integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Use service to adjust
        success, error, updated_balance = adjust_leave_balance(
            employee=balance.employee,
            leave_type=balance.leave_type,
            new_allocated=new_allocated,
            reason=reason,
            adjusted_by=request.user.profile,
            year=balance.year,
        )

        if not success:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(updated_balance)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(tags=["Leave Management"])
class LeaveRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for leave requests.
    Employees can create and view their own, managers can approve/reject.
    """

    permission_classes = [IsAuthenticated, IsEmployeeOrHR]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "leave_type", "employee"]
    ordering_fields = ["submitted_date", "start_date"]
    ordering = ["-submitted_date"]

    def get_queryset(self):
        """Filter requests based on user permissions."""
        user = self.request.user

        # HR admins can see all requests
        if user.is_staff or user.is_superuser:
            return LeaveRequest.objects.all().select_related(
                "employee__user",
                "covering_employee__user",
                "approver__user",
            )

        # Regular employees see only their own and their team's
        try:
            profile = user.profile
            # Get own requests and requests from direct reports
            own_requests = LeaveRequest.objects.filter(employee=profile)
            team_requests = LeaveRequest.objects.filter(employee__manager=profile)
            return (
                (own_requests | team_requests)
                .distinct()
                .select_related(
                    "employee__user",
                    "covering_employee__user",
                    "approver__user",
                )
            )
        except Exception:
            return LeaveRequest.objects.none()

    def get_serializer_class(self):
        """Use different serializers for different actions."""
        if self.action == "create":
            return LeaveRequestCreateSerializer
        elif self.action in ["retrieve"]:
            return LeaveRequestDetailSerializer
        return LeaveRequestListSerializer

    def create(self, request, *args, **kwargs):
        """Create a leave request and return the full object."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        # Return full details so the client keeps the submitted reason and approval metadata.
        response_serializer = LeaveRequestDetailSerializer(instance)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        """Save the leave request with the current user as employee."""
        serializer.save()

    @extend_schema(
        request=LeaveRequestApproveSerializer,
        responses={200: LeaveRequestDetailSerializer},
        summary="Tech Lead first-level approval (PENDING → LEAD_APPROVED)",
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsManagerForApproval],
    )
    def approve(self, request, pk=None):
        """Tech Lead approves a pending request — moves it to 'Lead Approved' and notifies HR."""
        from core.services.leave_service import approve_leave_request_lead

        leave_request = self.get_object()
        serializer = LeaveRequestApproveSerializer(
            data=request.data, context={"leave_request": leave_request}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        success, error = approve_leave_request_lead(
            leave_request=leave_request,
            approver=request.user.profile,
            comments=serializer.validated_data.get("comments", ""),
        )

        if not success:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            LeaveRequestDetailSerializer(leave_request).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=LeaveRequestHRApproveSerializer,
        responses={200: LeaveRequestDetailSerializer},
        summary="HR final approval (LEAD_APPROVED → APPROVED)",
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="hr-approve",
        permission_classes=[IsAuthenticated, IsHRAdminForAdjustment],
    )
    def hr_approve(self, request, pk=None):
        """HR gives final approval — moves request to 'Approved', deducts balance, notifies employee."""
        from core.services.leave_service import approve_leave_request_hr

        leave_request = self.get_object()
        serializer = LeaveRequestHRApproveSerializer(
            data=request.data, context={"leave_request": leave_request}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        success, error = approve_leave_request_hr(
            leave_request=leave_request,
            approver=request.user.profile,
            comments=serializer.validated_data.get("comments", ""),
        )

        if not success:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            LeaveRequestDetailSerializer(leave_request).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=LeaveRequestRejectSerializer,
        responses={200: LeaveRequestDetailSerializer},
        summary="Reject at any approval stage",
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, IsManagerForApproval],
    )
    def reject(self, request, pk=None):
        """Reject a leave request — works at both PENDING (Tech Lead) and LEAD_APPROVED (HR) stages."""
        from core.services.leave_service import reject_leave_request

        leave_request = self.get_object()
        serializer = LeaveRequestRejectSerializer(
            data=request.data, context={"leave_request": leave_request}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        success, error = reject_leave_request(
            leave_request=leave_request,
            approver=request.user.profile,
            reason=serializer.validated_data.get("reason"),
        )

        if not success:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            LeaveRequestDetailSerializer(leave_request).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(responses={200: LeaveRequestDetailSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel own leave request."""
        from core.services.leave_service import cancel_leave_request

        leave_request = self.get_object()

        if leave_request.employee.user != request.user:
            return Response(
                {"error": "You can only cancel your own requests"},
                status=status.HTTP_403_FORBIDDEN,
            )

        success, error = cancel_leave_request(leave_request)

        if not success:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            LeaveRequestDetailSerializer(leave_request).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        responses={200: LeaveRequestListSerializer(many=True)},
        summary="Pending requests for Tech Lead — requests awaiting first-level approval",
    )
    @action(
        detail=False,
        methods=["get"],
        permission_classes=[IsAuthenticated, IsManagerForApproval],
    )
    def pending(self, request):
        """
        Returns PENDING requests for the current Tech Lead (their direct reports).
        Staff/superusers see all PENDING requests.
        """
        user = request.user

        if user.is_staff or user.is_superuser:
            pending_requests = LeaveRequest.objects.filter(
                status=LeaveRequest.Status.PENDING
            )
        else:
            try:
                pending_requests = LeaveRequest.objects.filter(
                    employee__managers=user.profile,
                    status=LeaveRequest.Status.PENDING,
                )
            except Exception:
                pending_requests = LeaveRequest.objects.none()

        pending_requests = pending_requests.select_related(
            "employee__user",
            "covering_employee__user",
        ).order_by("-submitted_date")

        serializer = LeaveRequestListSerializer(pending_requests, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={200: LeaveRequestListSerializer(many=True)},
        summary="Lead-approved requests awaiting HR final decision",
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="hr-pending",
        permission_classes=[IsAuthenticated, IsHRAdminForAdjustment],
    )
    def hr_pending(self, request):
        """Returns LEAD_APPROVED requests for HR to review."""
        hr_queue = (
            LeaveRequest.objects.filter(status=LeaveRequest.Status.LEAD_APPROVED)
            .select_related(
                "employee__user",
                "lead_approver__user",
                "covering_employee__user",
            )
            .order_by("-lead_approved_date")
        )
        serializer = LeaveRequestListSerializer(hr_queue, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={
            200: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "employeeId": {"type": "string"},
                        "employeeName": {"type": "string"},
                        "leaveType": {"type": "string"},
                        "startDate": {"type": "string"},
                        "endDate": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
            }
        }
    )
    @action(detail=False, methods=["get"], url_path="team-calendar")
    def team_calendar(self, request):
        """Get approved leave requests for team calendar view."""

        user = request.user

        # Get date range from query params (optional)
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        # HR admins see all approved leaves
        if user.is_staff or user.is_superuser:
            requests = LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED)
        else:
            # Employees see their own and their team's approved leaves
            try:
                profile = user.profile
                requests = LeaveRequest.objects.filter(
                    status=LeaveRequest.Status.APPROVED
                ).filter(
                    models.Q(employee=profile) | models.Q(employee__manager=profile)
                )
            except Exception:
                requests = LeaveRequest.objects.none()

        # Apply date filtering if provided
        if start_date:
            requests = requests.filter(end_date__gte=start_date)
        if end_date:
            requests = requests.filter(start_date__lte=end_date)

        requests = requests.select_related("employee__user").order_by("start_date")

        # Format for calendar
        calendar_events = []
        for req in requests:
            calendar_events.append(
                {
                    "id": str(req.id),
                    "employeeId": str(req.employee.id),
                    "employeeName": req.employee.user.get_full_name(),
                    "leaveType": req.leave_type,
                    "startDate": req.start_date.isoformat(),
                    "endDate": req.end_date.isoformat(),
                    "status": req.status,
                }
            )

        return Response(calendar_events, status=status.HTTP_200_OK)

    @extend_schema(
        responses={200: LeaveTeamMemberSerializer(many=True)},
        summary="Selectable covering employees from the requester's active project teams",
    )
    @action(detail=False, methods=["get"], url_path="team-members")
    def team_members(self, request):
        """Active employees who share at least one active project with the requester (self excluded)."""
        from core.services.leave_service import get_team_members_for_employee

        profile = getattr(request.user, "profile", None)
        if profile is None:
            return Response([], status=status.HTTP_200_OK)

        members = get_team_members_for_employee(profile)
        serializer = LeaveTeamMemberSerializer(members, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={200: VacationCapabilitiesSerializer},
        summary="Per-feature capability flags for the Vacations module",
    )
    @action(detail=False, methods=["get"])
    def capabilities(self, request):
        """Return what the current user can do in the Vacations module (drives FE UI gating)."""
        from core.services.leave_service import get_vacation_capabilities

        return Response(
            get_vacation_capabilities(request.user),
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Leave Management"])
class LeaveAdjustmentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for leave adjustments (audit trail).
    Read-only view of all balance adjustments.
    """

    queryset = LeaveAdjustment.objects.all().select_related(
        "employee__user", "adjusted_by__user"
    )
    serializer_class = LeaveAdjustmentSerializer
    permission_classes = [IsAuthenticated, IsHRAdminForAdjustment]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["employee", "leave_type"]
    ordering = ["-adjusted_at"]


# ──────────────────────────────────────────
# Performance Reviews Views
# ──────────────────────────────────────────


@extend_schema(tags=["Performance Reviews"])
class PerformanceReviewViewSet(viewsets.ModelViewSet):
    """
    ViewSet for performance reviews with nested notes, action points,
    attachments, summary metrics, and history.
    """

    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = [
        "status",
        "review_type",
        "employee",
        "reviewer",
        "scheduled_date",
    ]
    ordering_fields = ["scheduled_date", "created_at", "updated_at"]
    ordering = ["-scheduled_date", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PerformanceReview.objects.none()

        queryset = (
            PerformanceReview.objects.select_related(
                "employee__user",
                "reviewer__user",
                "created_by__user",
                "updated_by__user",
            )
            .prefetch_related(
                "notes__author__user",
                "action_points__owner__user",
                "attachments__uploaded_by__user",
                "history_events__actor__user",
            )
            .all()
        )
        user = self.request.user

        if user.is_staff or user.is_superuser:
            return queryset

        if has_review_permission(user, "view_any_review_history"):
            return queryset

        profile = user.profile
        visibility_filter = models.Q(employee=profile) | models.Q(reviewer=profile)
        if has_review_permission(user, "view_team_reviews"):
            visibility_filter |= models.Q(employee__managers=profile)

        return queryset.filter(visibility_filter).distinct()

    def get_permissions(self):
        if self.action == "create":
            permission_classes = [IsAuthenticated, IsReviewCreator]
        elif self.action in ["update", "partial_update", "destroy", "update_status"]:
            permission_classes = [IsAuthenticated, IsReviewEditor]
        else:
            permission_classes = [IsAuthenticated, IsReviewViewer]
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.action == "list":
            return PerformanceReviewListSerializer
        if self.action == "retrieve":
            return PerformanceReviewDetailSerializer
        if self.action in ["create", "update", "partial_update"]:
            return PerformanceReviewCreateUpdateSerializer
        return PerformanceReviewDetailSerializer

    def _get_actor_profile(self):
        return self.request.user.profile

    def _log_history(
        self,
        review,
        event_type,
        description="",
        metadata=None,
        actor=None,
    ):
        PerformanceReviewHistoryEvent.objects.create(
            review=review,
            actor=actor if actor is not None else self._get_actor_profile(),
            event_type=event_type,
            description=description,
            metadata=metadata or {},
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = request.user.profile

        review = serializer.save(created_by=profile, updated_by=profile)
        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.CREATED,
            description="Performance review created.",
            metadata={
                "status": review.status,
                "scheduled_date": review.scheduled_date.isoformat(),
            },
        )
        sync_performance_review_reminders_for_review(review, actor=profile)

        response_serializer = PerformanceReviewDetailSerializer(review)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        review = self.get_object()
        old_status = review.status
        old_outcome = review.outcome

        serializer = self.get_serializer(review, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        updated_review = serializer.save(updated_by=request.user.profile)

        self._log_history(
            review=updated_review,
            event_type=PerformanceReviewHistoryEvent.EventType.UPDATED,
            description="Performance review updated.",
        )

        if old_status != updated_review.status:
            self._log_history(
                review=updated_review,
                event_type=PerformanceReviewHistoryEvent.EventType.STATUS_CHANGED,
                description=f"Status changed from {old_status} to {updated_review.status}.",
                metadata={"from": old_status, "to": updated_review.status},
            )
        if old_outcome != updated_review.outcome:
            self._log_history(
                review=updated_review,
                event_type=PerformanceReviewHistoryEvent.EventType.OUTCOME_UPDATED,
                description="Review outcome updated.",
                metadata={"from": old_outcome, "to": updated_review.outcome},
            )
        sync_performance_review_reminders_for_review(
            updated_review,
            actor=request.user.profile,
        )

        response_serializer = PerformanceReviewDetailSerializer(updated_review)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["status"],
        },
        responses={200: PerformanceReviewDetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="status")
    def update_status(self, request, pk=None):
        review = self.get_object()
        new_status = request.data.get("status")
        comment = request.data.get("comment", "")
        allowed_statuses = {choice[0] for choice in PerformanceReview.Status.choices}
        if new_status not in allowed_statuses:
            return Response(
                {"error": "Invalid status value."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = review.status
        review.status = new_status
        review.updated_by = request.user.profile
        if new_status == PerformanceReview.Status.COMPLETED and not review.completed_at:
            review.completed_at = timezone.now()
        review.save()

        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.STATUS_CHANGED,
            description=comment or f"Status changed from {old_status} to {new_status}.",
            metadata={"from": old_status, "to": new_status},
        )
        sync_performance_review_reminders_for_review(review, actor=request.user.profile)

        serializer = PerformanceReviewDetailSerializer(review)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={
            200: {
                "type": "object",
                "properties": {
                    "due_this_week": {"type": "integer"},
                    "in_progress": {"type": "integer"},
                    "completed_this_quarter": {"type": "integer"},
                    "average_rating": {"type": "number"},
                },
            }
        }
    )
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        queryset = self.get_queryset()
        today = timezone.now().date()
        week_end = today + timedelta(days=7)
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        quarter_start = today.replace(month=quarter_start_month, day=1)

        due_this_week = queryset.filter(
            status__in=[
                PerformanceReview.Status.SCHEDULED,
                PerformanceReview.Status.IN_PROGRESS,
            ],
            scheduled_date__gte=today,
            scheduled_date__lte=week_end,
        ).count()
        in_progress = queryset.filter(
            status=PerformanceReview.Status.IN_PROGRESS
        ).count()
        completed_this_quarter = queryset.filter(
            status=PerformanceReview.Status.COMPLETED,
            completed_at__date__gte=quarter_start,
            completed_at__date__lte=today,
        ).count()
        average_rating = (
            queryset.filter(
                status=PerformanceReview.Status.COMPLETED,
                overall_rating__isnull=False,
            ).aggregate(avg_rating=Avg("overall_rating"))["avg_rating"]
            or 0
        )

        return Response(
            {
                "due_this_week": due_this_week,
                "in_progress": in_progress,
                "completed_this_quarter": completed_this_quarter,
                "average_rating": round(float(average_rating), 2),
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=PerformanceReviewNoteSerializer,
        responses={
            200: PerformanceReviewNoteSerializer(many=True),
            201: PerformanceReviewNoteSerializer,
        },
    )
    @action(detail=True, methods=["get", "post"], url_path="notes")
    def notes(self, request, pk=None):
        review = self.get_object()

        if request.method == "GET":
            serializer = PerformanceReviewNoteSerializer(review.notes.all(), many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = PerformanceReviewNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = PerformanceReviewNote(
            review=review,
            author=request.user.profile,
            visibility=serializer.validated_data["visibility"],
            content=serializer.validated_data["content"],
        )

        if not can_edit_review_note(request.user, note):
            return Response(
                {"error": "You do not have permission to create this note type."},
                status=status.HTTP_403_FORBIDDEN,
            )

        note.save()
        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.NOTE_ADDED,
            description=f"{note.visibility.capitalize()} note added.",
            metadata={"note_id": note.id, "visibility": note.visibility},
        )
        response_serializer = PerformanceReviewNoteSerializer(note)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=PerformanceReviewNoteSerializer,
        responses={200: PerformanceReviewNoteSerializer, 204: None},
    )
    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"notes/(?P<note_id>[^/.]+)",
    )
    def note_detail(self, request, pk=None, note_id=None):
        review = self.get_object()
        try:
            note = review.notes.get(pk=note_id)
        except PerformanceReviewNote.DoesNotExist:
            return Response(
                {"error": "Note not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if not can_edit_review_note(request.user, note):
            return Response(
                {"error": "You do not have permission to modify this note."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.method == "PATCH":
            serializer = PerformanceReviewNoteSerializer(
                note, data=request.data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            updated_note = serializer.save(edited_by=request.user.profile)
            self._log_history(
                review=review,
                event_type=PerformanceReviewHistoryEvent.EventType.NOTE_UPDATED,
                description=f"Note #{updated_note.id} updated.",
                metadata={"note_id": updated_note.id},
            )
            return Response(
                PerformanceReviewNoteSerializer(updated_note).data,
                status=status.HTTP_200_OK,
            )

        note_id_value = note.id
        note.delete()
        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.NOTE_UPDATED,
            description=f"Note #{note_id_value} deleted.",
            metadata={"note_id": note_id_value},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=PerformanceReviewActionPointSerializer,
        responses={
            200: PerformanceReviewActionPointSerializer(many=True),
            201: PerformanceReviewActionPointSerializer,
        },
    )
    @action(detail=True, methods=["get", "post"], url_path="action-points")
    def action_points(self, request, pk=None):
        review = self.get_object()

        if request.method == "GET":
            serializer = PerformanceReviewActionPointSerializer(
                review.action_points.all(), many=True
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        if not IsReviewEditor().has_object_permission(request, self, review):
            return Response(
                {"error": "You do not have permission to add action points."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PerformanceReviewActionPointSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action_point = serializer.save(
            review=review,
            created_by=request.user.profile,
        )
        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.ACTION_POINT_ADDED,
            description=f"Action point '{action_point.title}' added.",
            metadata={"action_point_id": action_point.id},
        )
        return Response(
            PerformanceReviewActionPointSerializer(action_point).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=PerformanceReviewActionPointSerializer,
        responses={200: PerformanceReviewActionPointSerializer, 204: None},
    )
    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"action-points/(?P<action_point_id>[^/.]+)",
    )
    def action_point_detail(self, request, pk=None, action_point_id=None):
        review = self.get_object()
        if not IsReviewEditor().has_object_permission(request, self, review):
            return Response(
                {"error": "You do not have permission to modify action points."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            action_point = review.action_points.get(pk=action_point_id)
        except PerformanceReviewActionPoint.DoesNotExist:
            return Response(
                {"error": "Action point not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if request.method == "PATCH":
            serializer = PerformanceReviewActionPointSerializer(
                action_point, data=request.data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            updated_action_point = serializer.save()
            self._log_history(
                review=review,
                event_type=PerformanceReviewHistoryEvent.EventType.ACTION_POINT_UPDATED,
                description=f"Action point '{updated_action_point.title}' updated.",
                metadata={"action_point_id": updated_action_point.id},
            )
            return Response(
                PerformanceReviewActionPointSerializer(updated_action_point).data,
                status=status.HTTP_200_OK,
            )

        deleted_id = action_point.id
        action_point.delete()
        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.ACTION_POINT_UPDATED,
            description=f"Action point #{deleted_id} deleted.",
            metadata={"action_point_id": deleted_id},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=PerformanceReviewAttachmentSerializer,
        responses={
            200: PerformanceReviewAttachmentSerializer(many=True),
            201: PerformanceReviewAttachmentSerializer,
        },
    )
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="attachments",
        parser_classes=[parsers.MultiPartParser, parsers.FormParser],
    )
    def attachments(self, request, pk=None):
        review = self.get_object()

        if request.method == "GET":
            serializer = PerformanceReviewAttachmentSerializer(
                review.attachments.all(), many=True
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        if not can_attach_review_documents(request.user, review):
            return Response(
                {"error": "You do not have permission to upload attachments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PerformanceReviewAttachmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data["file"]
        attachment = serializer.save(
            review=review,
            uploaded_by=request.user.profile,
            content_type=getattr(uploaded_file, "content_type", ""),
        )
        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.ATTACHMENT_ADDED,
            description=f"Attachment '{attachment.original_name}' uploaded.",
            metadata={"attachment_id": attachment.id, "name": attachment.original_name},
        )
        return Response(
            PerformanceReviewAttachmentSerializer(attachment).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(responses={204: None})
    @action(
        detail=True,
        methods=["delete"],
        url_path=r"attachments/(?P<attachment_id>[^/.]+)",
    )
    def delete_attachment(self, request, pk=None, attachment_id=None):
        review = self.get_object()
        if not can_attach_review_documents(request.user, review):
            return Response(
                {"error": "You do not have permission to delete attachments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            attachment = review.attachments.get(pk=attachment_id)
        except PerformanceReviewAttachment.DoesNotExist:
            return Response(
                {"error": "Attachment not found."}, status=status.HTTP_404_NOT_FOUND
            )

        deleted_id = attachment.id
        deleted_name = attachment.original_name
        attachment.delete()
        self._log_history(
            review=review,
            event_type=PerformanceReviewHistoryEvent.EventType.ATTACHMENT_REMOVED,
            description=f"Attachment '{deleted_name}' deleted.",
            metadata={"attachment_id": deleted_id, "name": deleted_name},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={200: PerformanceReviewHistoryEventSerializer(many=True)})
    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        review = self.get_object()
        serializer = PerformanceReviewHistoryEventSerializer(
            review.history_events.all(), many=True
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(tags=["Performance Reviews"])
class PerformanceReviewReminderViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PerformanceReviewReminderSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["is_read", "is_sent", "reminder_type"]
    ordering_fields = ["scheduled_for", "created_at"]
    ordering = ["-scheduled_for", "-created_at"]

    def list(self, request, *args, **kwargs):
        actor = request.user.profile if hasattr(request.user, "profile") else None
        materialize_performance_review_reminders(actor=actor)
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PerformanceReviewReminder.objects.none()

        queryset = PerformanceReviewReminder.objects.select_related(
            "review__employee__user",
            "recipient__user",
        ).filter(is_sent=True)
        user = self.request.user
        if (
            user.is_staff
            or user.is_superuser
            or has_review_permission(user, "view_any_review_history")
        ):
            return queryset
        return queryset.filter(recipient=user.profile)

    @extend_schema(responses={200: PerformanceReviewReminderSerializer})
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        reminder = self.get_object()
        is_owner = reminder.recipient_id == request.user.profile.id
        is_admin = (
            request.user.is_staff
            or request.user.is_superuser
            or has_review_permission(request.user, "view_any_review_history")
        )
        if not (is_owner or is_admin):
            return Response(
                {"error": "You do not have permission to mark this reminder as read."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not reminder.is_read:
            reminder.is_read = True
            reminder.read_at = timezone.now()
            reminder.save(update_fields=["is_read", "read_at"])
            PerformanceReviewHistoryEvent.objects.create(
                review=reminder.review,
                actor=request.user.profile,
                event_type=PerformanceReviewHistoryEvent.EventType.REMINDER_READ,
                description="Reminder marked as read.",
                metadata={"reminder_id": reminder.id},
            )

        serializer = self.get_serializer(reminder)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ──────────────────────────────────────────
# Documents
# ──────────────────────────────────────────


class DocumentPagination(pagination.PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class IsDocumentHROrAdmin(permissions.BasePermission):
    """Restrict write/delete/archive operations to HR and admin users."""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and is_hr_or_admin(request.user)
        )


@extend_schema(tags=["Documents"])
class DocumentViewSet(viewsets.GenericViewSet):
    """
    Full document management viewset.

    Endpoints:
      GET    /api/documents/                    list
      POST   /api/documents/                    create (upload)
      GET    /api/documents/{id}/               retrieve
      DELETE /api/documents/{id}/               destroy (HR/admin)
      GET    /api/documents/{id}/download/      signed download URL
      GET    /api/documents/{id}/preview/       signed preview URL
      POST   /api/documents/{id}/archive/       soft-delete (HR/admin)
      POST   /api/documents/{id}/unarchive/     restore archived doc (HR/admin)
      POST   /api/documents/{id}/request-signature/  start e-sig workflow (HR/admin)
      POST   /api/documents/{id}/send-reminder/ resend signature emails
      GET    /api/documents/{id}/versions/      version history
      POST   /api/documents/bulk-delete/        bulk hard-delete (HR/admin)
      POST   /api/documents/bulk-archive/       bulk soft-delete (HR/admin)
      POST   /api/documents/bulk-download/      ZIP download URL
      GET    /api/documents/export/             CSV export URL (HR/admin)
    """

    permission_classes = [IsAuthenticated]
    pagination_class = DocumentPagination

    serializer_class = DocumentListSerializer
    queryset = Document.objects.none()

    # ── queryset helpers ──────────────────────────────────────────────

    def _base_queryset(self, request):
        include_archived = request.query_params.get("archived", "").lower() == "true"
        return document_queryset(include_archived=include_archived)

    def _get_document_or_404(self, pk):
        return get_document_for_api(pk)

    def _apply_filters(self, queryset, request):
        try:
            return apply_document_list_filters(queryset, request.query_params), None
        except DocumentFilterError as exc:
            return None, Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # ── list ──────────────────────────────────────────────────────────

    @extend_schema(
        summary="List documents",
        parameters=[
            OpenApiParameter("category", str, description="Filter by category"),
            OpenApiParameter("search", str, description="Search name/description/tags"),
            OpenApiParameter(
                "signature_status", str, description="Filter by signature status"
            ),
            OpenApiParameter(
                "expiry_filter", str, description="expiring_soon or expired"
            ),
            OpenApiParameter("ordering", str, description="Field to sort by"),
            OpenApiParameter(
                "archived", str, description="Pass true to list archived docs"
            ),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: DocumentListSerializer(many=True)},
    )
    def list(self, request):
        queryset = self._base_queryset(request)
        queryset, err = self._apply_filters(queryset, request)
        if err:
            return err

        accessible = filter_accessible_documents(request.user, queryset)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(accessible, request)
        serializer = DocumentListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    # ── create ────────────────────────────────────────────────────────

    @extend_schema(
        summary="Upload a document",
        request=DocumentCreateSerializer,
        responses={201: DocumentListSerializer},
    )
    def create(self, request):
        # Build data dict manually to avoid deep-copying file objects from the QueryDict.
        data = {
            key: request.data[key]
            for key in request.data
            if key not in ("tags", "allowed_roles", "file")
        }
        data["tags"] = request.data.getlist("tags")
        data["allowed_roles"] = request.data.getlist("allowed_roles")
        scope_value = request.data.get("visibility_scope")
        if scope_value:
            data["visibility_scope"] = scope_value
        if "file" in request.data:
            data["file"] = request.data["file"]

        serializer = DocumentCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        profile = getattr(request.user, "profile", None)
        uploaded_file = v["file"]

        stored_key = default_storage.save(
            f"documents/{uploaded_file.name}", uploaded_file
        )

        document = Document.objects.create(
            uploaded_by=profile,
            category=v["category"],
            file_key=stored_key,
            name=v["name"],
            description=v.get("description", ""),
            original_filename=getattr(uploaded_file, "name", ""),
            file_size=getattr(uploaded_file, "size", 0) or 0,
            mime_type=getattr(uploaded_file, "content_type", "") or "",
            expiry_date=v.get("expiry_date"),
            tags=v.get("tags", []),
            allowed_roles=v.get("allowed_roles")
            or [Document.AccessRole.EMPLOYEE.value],
            visibility_scope=v.get("visibility_scope", Document.VisibilityScope.ROLES),
            signature_status=Document.SignatureStatus.NOT_REQUIRED,
            current_version="1.0",
        )

        return Response(
            DocumentListSerializer(document).data,
            status=status.HTTP_201_CREATED,
        )

    # ── retrieve ──────────────────────────────────────────────────────

    @extend_schema(
        summary="Get a single document", responses={200: DocumentListSerializer}
    )
    def retrieve(self, request, pk=None):
        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            DocumentListSerializer(document).data, status=status.HTTP_200_OK
        )

    # ── destroy ───────────────────────────────────────────────────────

    @extend_schema(summary="Delete a document (HR/admin only)", responses={204: None})
    def destroy(self, request, pk=None):
        if not is_hr_or_admin(request.user):
            return Response(
                {"detail": "Only HR or admin users can delete documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )

        hard_delete_document(document)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── download ──────────────────────────────────────────────────────

    @extend_schema(
        summary="Get a signed download URL",
        responses={
            200: {"type": "object", "properties": {"signed_url": {"type": "string"}}}
        },
    )
    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )

        signed_url = generate_presigned_url(document.file_key, expiry_seconds=600)
        return Response({"signed_url": signed_url}, status=status.HTTP_200_OK)

    # ── preview ───────────────────────────────────────────────────────

    @extend_schema(
        summary="Get a signed preview URL",
        responses={
            200: {"type": "object", "properties": {"preview_url": {"type": "string"}}}
        },
    )
    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None):
        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )

        preview_url, preview_err = build_document_inline_preview_url(document)
        if preview_err or not preview_url:
            return Response(
                {"detail": preview_err or "Preview not available for this file type."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        return Response({"preview_url": preview_url}, status=status.HTTP_200_OK)

    # ── archive ───────────────────────────────────────────────────────

    @extend_schema(
        summary="Archive a document (HR/admin only)",
        responses={200: DocumentListSerializer},
    )
    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        if not is_hr_or_admin(request.user):
            return Response(
                {"detail": "Only HR or admin users can archive documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )

        document = archive_document(document)
        return Response(
            DocumentListSerializer(document).data, status=status.HTTP_200_OK
        )

    @extend_schema(
        summary="Restore an archived document (HR/admin only)",
        responses={200: DocumentListSerializer},
    )
    @action(detail=True, methods=["post"], url_path="unarchive")
    def unarchive(self, request, pk=None):
        if not is_hr_or_admin(request.user):
            return Response(
                {"detail": "Only HR or admin users can restore archived documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )

        document = unarchive_document(document)
        return Response(
            DocumentListSerializer(document).data, status=status.HTTP_200_OK
        )

    @extend_schema(
        summary="Update document visibility (HR/admin only)",
        request=DocumentVisibilityUpdateSerializer,
        responses={200: DocumentListSerializer},
    )
    @action(detail=True, methods=["patch"], url_path="visibility")
    def update_visibility(self, request, pk=None):
        if not is_hr_or_admin(request.user):
            return Response(
                {"detail": "Only HR or admin users can update document visibility."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = DocumentVisibilityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        update_document_visibility(
            document,
            serializer.validated_data["allowed_roles"],
            visibility_scope=serializer.validated_data.get(
                "visibility_scope", Document.VisibilityScope.ROLES
            ),
        )
        return Response(
            DocumentListSerializer(document).data, status=status.HTTP_200_OK
        )

    # ── request-signature ─────────────────────────────────────────────

    @extend_schema(
        summary="Initiate e-signature workflow",
        request=RequestSignatureSerializer,
        responses={200: DocumentListSerializer},
    )
    @action(detail=True, methods=["post"], url_path="request-signature")
    def request_signature(self, request, pk=None):
        if not can_initiate_signature_request(request.user):
            return Response(
                {"detail": "You do not have permission to request signatures."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if document.archived:
            return Response(
                {"detail": "Archived documents cannot be sent for signature."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = RequestSignatureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            document, _ = request_document_signatures(
                document,
                serializer.validated_data["signers"],
                requested_by=request.user,
                request_context=request,
            )
        except ActiveSignatureWorkflowError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(
            DocumentListSerializer(get_document_for_response(document.pk)).data,
            status=status.HTTP_200_OK,
        )

    # ── sign ─────────────────────────────────────────────────────────

    @extend_schema(
        summary="Record an electronic signature",
        request=SignDocumentSerializer,
        responses={200: DocumentListSerializer},
    )
    @action(detail=True, methods=["post"], url_path="sign")
    def sign(self, request, pk=None):
        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if document.archived:
            return Response(
                {"detail": "Archived documents cannot be signed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SignDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        signer_email = serializer.validated_data["signer_email"].lower()
        signer = document.signers.filter(email__iexact=signer_email).first()
        if signer is None:
            return Response(
                {"detail": "Signer not found for this document."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not can_sign_for(request.user, signer):
            return Response(
                {"detail": "You do not have permission to sign for this signer."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document, signer = sign_document(
            document,
            signer,
            actor=request.user,
            signature_payload=serializer.validated_data["signature"],
            request_context=request,
        )
        document = get_document_for_response(document.pk)
        return Response(
            {
                "document": DocumentListSerializer(document).data,
                "signer": DocumentSignerSerializer(signer).data,
            },
            status=status.HTTP_200_OK,
        )

    # ── reset signatures (testing helper) ────────────────────────────

    @extend_schema(
        summary="Reset signature workflow (testing/admin only) — clears all signers",
        responses={200: DocumentListSerializer},
    )
    @action(detail=True, methods=["post"], url_path="reset-signatures")
    def reset_signatures(self, request, pk=None):
        if not can_initiate_signature_request(request.user):
            return Response(
                {"detail": "You do not have permission to reset signatures."},
                status=status.HTTP_403_FORBIDDEN,
            )
        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = reset_document_signatures(
            document, actor=request.user, request_context=request
        )
        return Response(
            DocumentListSerializer(get_document_for_response(document.pk)).data,
            status=status.HTTP_200_OK,
        )

    # ── signatures ───────────────────────────────────────────────────

    @extend_schema(summary="List document signature status and audit events")
    @action(detail=True, methods=["get"], url_path="signatures")
    def signatures(self, request, pk=None):
        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )

        events = get_signature_audit_events(document)
        return Response(
            {
                "document_id": document.pk,
                "signature_status": document.signature_status,
                "signers": DocumentSignerSerializer(
                    document.signers.all(), many=True
                ).data,
                "audit_events": SignatureAuditLogSerializer(events, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    # ── send-reminder ─────────────────────────────────────────────────

    @extend_schema(summary="Re-send signature reminder emails", responses={204: None})
    @action(detail=True, methods=["post"], url_path="send-reminder")
    def send_reminder(self, request, pk=None):
        if not can_send_signature_reminder(request.user):
            return Response(
                {"detail": "You do not have permission to send signature reminders."},
                status=status.HTTP_403_FORBIDDEN,
            )

        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )

        pending_count = remind_pending_signers(
            document,
            actor=request.user,
            request_context=request,
        )
        if pending_count == 0:
            return Response(
                {"detail": "No pending signers to remind."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"reminded_count": pending_count}, status=status.HTTP_200_OK)

    # ── versions ──────────────────────────────────────────────────────

    @extend_schema(
        summary="List version history for a document",
        responses={200: DocumentVersionSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="versions")
    def versions(self, request, pk=None):
        document = self._get_document_or_404(pk)
        if not document:
            return Response(
                {"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not is_document_accessible(request.user, document):
            return Response(
                {"detail": "You do not have permission to access this document."},
                status=status.HTTP_403_FORBIDDEN,
            )

        version_qs = document.versions.select_related("uploaded_by__user").order_by(
            "version"
        )
        count = version_qs.count()
        serializer = DocumentVersionSerializer(version_qs, many=True)
        return Response(
            {"count": count, "results": serializer.data}, status=status.HTTP_200_OK
        )

    # ── bulk-delete ───────────────────────────────────────────────────

    @extend_schema(
        summary="Bulk hard-delete documents (HR/admin only)",
        request=BulkIdsSerializer,
        responses={204: None},
    )
    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        if not is_hr_or_admin(request.user):
            return Response(
                {"detail": "Only HR or admin users can bulk delete documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkIdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]

        found = list(Document.objects.filter(pk__in=ids).prefetch_related("versions"))
        found_ids = {doc.pk for doc in found}
        missing = [i for i in ids if i not in found_ids]
        if missing:
            return Response({"not_found": missing}, status=status.HTTP_404_NOT_FOUND)

        bulk_hard_delete(found)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── bulk-archive ──────────────────────────────────────────────────

    @extend_schema(
        summary="Bulk archive documents (HR/admin only)",
        request=BulkIdsSerializer,
        responses={204: None},
    )
    @action(detail=False, methods=["post"], url_path="bulk-archive")
    def bulk_archive(self, request):
        if not is_hr_or_admin(request.user):
            return Response(
                {"detail": "Only HR or admin users can bulk archive documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkIdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]

        documents = list(Document.objects.filter(pk__in=ids))
        found_ids = {doc.pk for doc in documents}
        missing = [i for i in ids if i not in found_ids]
        if missing:
            return Response({"not_found": missing}, status=status.HTTP_404_NOT_FOUND)

        bulk_archive(documents)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── bulk-download ─────────────────────────────────────────────────

    @extend_schema(
        summary="Bulk download documents as a ZIP",
        request=BulkIdsSerializer,
        responses={
            200: {"type": "object", "properties": {"download_url": {"type": "string"}}}
        },
    )
    @action(detail=False, methods=["post"], url_path="bulk-download")
    def bulk_download(self, request):
        serializer = BulkIdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]

        if len(ids) > 50:
            return Response(
                {"detail": "Maximum 50 documents per bulk download."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        documents = list(Document.objects.filter(pk__in=ids))
        accessible = [d for d in documents if is_document_accessible(request.user, d)]

        if not accessible:
            return Response(
                {"detail": "You do not have access to any of the requested documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        download_url = generate_zip_url(accessible, request.user)
        return Response({"download_url": download_url}, status=status.HTTP_200_OK)

    # ── export ────────────────────────────────────────────────────────

    @extend_schema(
        summary="Export document list as CSV (HR/admin only)",
        parameters=[
            OpenApiParameter("category", str, description="Optional category filter")
        ],
        responses={
            200: {"type": "object", "properties": {"export_url": {"type": "string"}}}
        },
    )
    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        if not is_hr_or_admin(request.user):
            return Response(
                {"detail": "Only HR or admin users can export documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = self._base_queryset(request).filter(archived=False)
        category = request.query_params.get("category")
        if category:
            if category not in Document.Category.values:
                return Response(
                    {"detail": "Invalid category filter."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(category=category)

        accessible = filter_accessible_documents(request.user, queryset)
        export_url = export_documents_csv(accessible)
        return Response({"export_url": export_url}, status=status.HTTP_200_OK)


class IsDocumentAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and is_admin_user(request.user))


@extend_schema(tags=["Documents"])
class DocumentCategoryDefaultsView(APIView):
    permission_classes = [IsDocumentAdmin]

    @extend_schema(
        summary="List category default visibility",
        responses={
            200: {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            }
        },
    )
    def get(self, request):
        return Response(get_document_category_defaults(), status=status.HTTP_200_OK)

    @extend_schema(
        summary="Update default visibility for a category (admin only)",
        request=DocumentCategoryDefaultUpdateSerializer,
        responses={
            200: {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "allowed_roles": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            }
        },
    )
    def patch(self, request, category: str | None = None):
        if category is None or category not in Document.Category.values:
            return Response(
                {"detail": "Unknown document category."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = DocumentCategoryDefaultUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = set_document_category_default(
            category, serializer.validated_data["allowed_roles"]
        )
        return Response(
            {"category": row.category, "allowed_roles": list(row.allowed_roles or [])},
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────
# Training & Development Views
# ──────────────────────────────────────────


@extend_schema(tags=["Training & Development"])
class TrainingEntryViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing training entries.
    Employees can manage their own entries, HR can manage all.
    """

    serializer_class = TrainingEntryCreateUpdateSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["training_type", "employee"]
    search_fields = [
        "course_title",
        "provider",
        "description",
        "employee__user__first_name",
        "employee__user__last_name",
    ]
    ordering_fields = ["training_date", "created_at", "cost"]
    ordering = ["-training_date"]

    def get_queryset(self):
        """Filter entries based on user permissions."""
        user = self.request.user

        # HR admins can see all entries
        if user.is_staff or user.is_superuser:
            return TrainingEntry.objects.all().select_related("employee__user")

        # Regular employees see only their own
        try:
            return TrainingEntry.objects.filter(employee=user.profile).select_related(
                "employee__user"
            )
        except Exception:
            return TrainingEntry.objects.none()

    def get_serializer_class(self):
        """Use different serializers for different actions."""
        if self.action == "list":
            return TrainingEntryListSerializer
        elif self.action == "retrieve":
            return TrainingEntryDetailSerializer
        return TrainingEntryCreateUpdateSerializer

    def perform_create(self, serializer):
        """Create entry for authenticated employee or specified employee (HR only)."""
        user = self.request.user
        employee = user.profile

        # HR can specify a different employee via employee_id in request data
        if user.is_staff or user.is_superuser:
            employee_id = self.request.data.get("employee_id")
            if employee_id:
                try:
                    employee = UserProfile.objects.get(id=employee_id)
                except UserProfile.DoesNotExist:
                    pass

        instance = serializer.save(employee=employee)
        # Store instance for use in create method
        self.created_instance = instance

    def create(self, request, *args, **kwargs):
        """Override create to return response with status field."""
        response = super().create(request, *args, **kwargs)
        # Re-serialize the created instance with DetailSerializer to include status
        if response.status_code == 201 and hasattr(self, "created_instance"):
            detail_serializer = TrainingEntryDetailSerializer(self.created_instance)
            response.data = detail_serializer.data
        return response

    def perform_update(self, serializer):
        """Update entry (employee: own only, HR: any)."""
        serializer.save()

    def perform_destroy(self, instance):
        """Delete entry (employee: own only, HR: any)."""
        instance.delete()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="year",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter by year of training_date (e.g., 2024)",
                required=False,
            ),
        ],
        responses={200: TrainingEntryListSerializer(many=True)},
        description="List training entries with optional year filter",
    )
    def list(self, request, *args, **kwargs):
        """List training entries with optional year filter."""
        queryset = self.get_queryset()

        # Filter by year if provided
        year_filter = request.query_params.get("year")
        if year_filter:
            try:
                year = int(year_filter)
                queryset = queryset.filter(training_date__year=year)
            except (ValueError, TypeError):
                pass

        # Apply other filters and ordering
        queryset = self.filter_queryset(queryset)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema(tags=["Training & Development"])
class ConferenceCourseRegistrationViewSet(viewsets.ModelViewSet):
    """
    CRUD for conference and course registrations.

    Employees manage their own registrations; HR/admins can manage and filter
    by any employee via the ``employee`` query parameter.
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["status", "employee"]
    search_fields = [
        "name",
        "notes",
        "employee__user__first_name",
        "employee__user__last_name",
    ]
    ordering_fields = ["date", "created_at", "status"]
    ordering = ["-date"]

    def get_queryset(self):
        user = self.request.user
        base = ConferenceCourseRegistration.objects.select_related("employee__user")
        if user.is_staff or user.is_superuser:
            return base
        try:
            return base.filter(employee=user.profile)
        except Exception:
            return ConferenceCourseRegistration.objects.none()

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return ConferenceCourseRegistrationListSerializer
        return ConferenceCourseRegistrationCreateUpdateSerializer

    def perform_create(self, serializer):
        user = self.request.user
        employee = user.profile
        if user.is_staff or user.is_superuser:
            employee_id = self.request.data.get("employee_id")
            if employee_id:
                try:
                    employee = UserProfile.objects.get(id=employee_id)
                except UserProfile.DoesNotExist:
                    pass
        self.created_instance = serializer.save(employee=employee)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        if response.status_code == status.HTTP_201_CREATED and hasattr(
            self, "created_instance"
        ):
            response.data = ConferenceCourseRegistrationListSerializer(
                self.created_instance
            ).data
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            instance = self.get_object()
            response.data = ConferenceCourseRegistrationListSerializer(instance).data
        return response

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="year",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter by year of registration date (e.g., 2026)",
                required=False,
            ),
        ],
        responses={200: ConferenceCourseRegistrationListSerializer(many=True)},
        description="List conference / course registrations with optional year filter",
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        year_filter = request.query_params.get("year")
        if year_filter:
            try:
                queryset = queryset.filter(date__year=int(year_filter))
            except (ValueError, TypeError):
                pass

        queryset = self.filter_queryset(queryset)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema(tags=["Training & Development"])
class CertificateViewSet(viewsets.ModelViewSet):
    """
    CRUD for employee certificates.

    Employees can manage their own certificates; HR/admins can manage any
    employee's certificates and filter by ``employee`` query parameter.

    Files are uploaded to the configured default storage (R2 in production).
    Download endpoint returns a short-lived presigned URL.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["employee"]
    search_fields = [
        "title",
        "issuer",
        "employee__user__first_name",
        "employee__user__last_name",
    ]
    ordering_fields = ["issued_date", "expiration_date", "created_at"]
    ordering = ["-issued_date"]

    def get_queryset(self):
        user = self.request.user
        base = Certificate.objects.select_related("employee__user")
        if user.is_staff or user.is_superuser:
            return base
        try:
            return base.filter(employee=user.profile)
        except Exception:
            return Certificate.objects.none()

    def get_serializer_class(self):
        if self.action == "list":
            return CertificateListSerializer
        if self.action == "retrieve":
            return CertificateDetailSerializer
        return CertificateCreateUpdateSerializer

    def _resolve_target_employee(self) -> UserProfile:
        user = self.request.user
        employee = user.profile
        if user.is_staff or user.is_superuser:
            employee_id = self.request.data.get("employee_id")
            if employee_id:
                try:
                    employee = UserProfile.objects.get(id=int(employee_id))
                except (UserProfile.DoesNotExist, ValueError, TypeError):
                    pass
        return employee

    def perform_create(self, serializer):
        self.created_instance = serializer.save(
            employee=self._resolve_target_employee()
        )

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        if response.status_code == status.HTTP_201_CREATED and hasattr(
            self, "created_instance"
        ):
            response.data = CertificateDetailSerializer(
                self.created_instance, context={"request": request}
            ).data
        return response

    @extend_schema(
        summary="Get a signed download URL for a certificate file",
        responses={
            200: {"type": "object", "properties": {"signed_url": {"type": "string"}}}
        },
    )
    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        certificate = self.get_object()
        if not certificate.file:
            return Response(
                {"detail": "Certificate has no file attached."},
                status=status.HTTP_404_NOT_FOUND,
            )

        signed_url = generate_presigned_url(certificate.file.name, expiry_seconds=600)
        return Response({"signed_url": signed_url}, status=status.HTTP_200_OK)


# ──────────────────────────────────────────────────────────────────────────────
# Document Templates
# ──────────────────────────────────────────────────────────────────────────────


class DocumentTemplatePagination(pagination.PageNumberPagination):
    page_size = 20
    max_page_size = 100
    page_size_query_param = "page_size"


class DocumentTemplateViewSet(viewsets.GenericViewSet):
    """
    Full document template management viewset.

    Endpoints:
      GET    /api/documents/templates/                    list
      POST   /api/documents/templates/                    create
      GET    /api/documents/templates/{id}/               retrieve
      PUT    /api/documents/templates/{id}/               full update
      PATCH  /api/documents/templates/{id}/               partial update
      DELETE /api/documents/templates/{id}/               soft delete
      POST   /api/documents/templates/{id}/duplicate/     clone template
      POST   /api/documents/templates/{id}/use/           generate document
      GET    /api/documents/templates/categories/         enum values
    """

    permission_classes = [IsAuthenticated]
    pagination_class = DocumentTemplatePagination
    serializer_class = DocumentTemplateListSerializer
    queryset = DocumentTemplate.objects.none()

    # ── permission helpers ────────────────────────────────────────────────────

    def _get_profile(self, request):
        """Return the UserProfile for the authenticated user, or None."""
        try:
            return request.user.profile
        except Exception:
            return None

    def _is_admin(self, request) -> bool:
        """True when the user is staff, superuser, or has an HR/admin role."""
        if request.user.is_staff or request.user.is_superuser:
            return True
        try:
            from .services.document_service import is_hr_or_admin

            return is_hr_or_admin(request.user)
        except Exception:
            return False

    def _can_edit(self, request, template) -> bool:
        """True when the user is the template creator or an admin."""
        profile = self._get_profile(request)
        if profile and template.created_by_id == profile.pk:
            return True
        return self._is_admin(request)

    def _build_error(self, code: str, message: str, details: dict | None = None):
        return {"code": code, "message": message, "details": details or {}}

    # ── queryset helpers ──────────────────────────────────────────────────────

    def _base_queryset(self, request):
        """
        Return the queryset scoped to what the requesting user may see.

        - SHARED templates are visible to all authenticated users.
        - PRIVATE templates are only visible to their creator.
        - Inactive (soft-deleted) templates are always excluded.
        """
        from django.db.models import Q

        from .enums import TemplateVisibility

        profile = self._get_profile(request)
        qs = (
            DocumentTemplate.objects.filter(is_active=True)
            .select_related("created_by__user")
            .prefetch_related("fields")
        )
        if self._is_admin(request):
            return qs
        if profile:
            return qs.filter(
                Q(visibility=TemplateVisibility.SHARED) | Q(created_by=profile)
            )
        # Unauthenticated users see only SHARED (should not reach here due to IsAuthenticated)
        return qs.filter(visibility=TemplateVisibility.SHARED)

    # ── CRUD actions ──────────────────────────────────────────────────────────

    @extend_schema(
        tags=["Document Templates"],
        summary="List document templates",
        description=(
            "Return all active templates visible to the requesting user. "
            "PRIVATE templates are only shown to their creator. "
            "Supports filtering by category, visibility, created_by, and is_system_template."
        ),
        parameters=[
            OpenApiParameter(
                "category", str, description="Filter by TemplateCategory value"
            ),
            OpenApiParameter(
                "visibility", str, description="Filter by TemplateVisibility value"
            ),
            OpenApiParameter(
                "created_by", int, description="Filter by creator profile ID"
            ),
            OpenApiParameter(
                "is_system_template",
                bool,
                description="Filter system vs user templates",
            ),
            OpenApiParameter(
                "search", str, description="Search by name or description"
            ),
        ],
        responses={200: DocumentTemplateListSerializer(many=True)},
    )
    def list(self, request):
        """
        List all active templates visible to the requesting user.

        Filters: category, visibility, created_by, is_system_template, search.
        """
        from .enums import TemplateCategory, TemplateVisibility

        qs = self._base_queryset(request)
        params = request.query_params

        category = params.get("category")
        if category:
            if category not in TemplateCategory.values:
                return Response(
                    self._build_error("VALIDATION_ERROR", "Invalid category filter."),
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(category=category)

        visibility = params.get("visibility")
        if visibility:
            if visibility not in TemplateVisibility.values:
                return Response(
                    self._build_error("VALIDATION_ERROR", "Invalid visibility filter."),
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(visibility=visibility)

        created_by = params.get("created_by")
        if created_by:
            qs = qs.filter(created_by__pk=created_by)

        is_system = params.get("is_system_template")
        if is_system is not None:
            qs = qs.filter(is_system_template=is_system.lower() == "true")

        search = params.get("search", "").strip()
        if search:
            from django.db.models import Q

            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

        qs = qs.order_by("-updated_at")
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = DocumentTemplateListSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = DocumentTemplateListSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Document Templates"],
        summary="Retrieve a template",
        description="Fetch a single template with its full content and all field definitions.",
        responses={
            200: DocumentTemplateDetailSerializer,
            404: None,
        },
    )
    def retrieve(self, request, pk=None):
        """
        Fetch a single template with full content and all fields.

        Returns 404 when the template does not exist or has been soft-deleted.
        Returns 403 when a PRIVATE template is requested by a non-owner.
        """
        from .enums import ErrorCode, TemplateVisibility

        template = get_template_or_404(pk)

        # Visibility gate — PRIVATE templates are owner-only
        if template.visibility == TemplateVisibility.PRIVATE:
            profile = self._get_profile(request)
            if not self._is_admin(request) and (
                not profile or template.created_by_id != profile.pk
            ):
                return Response(
                    self._build_error(
                        ErrorCode.FORBIDDEN,
                        "You do not have permission to view this template.",
                    ),
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer = DocumentTemplateDetailSerializer(template)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Document Templates"],
        summary="Create a template",
        description="Create a new document template with metadata, content, and field definitions.",
        request=DocumentTemplateCreateUpdateSerializer,
        responses={
            201: DocumentTemplateDetailSerializer,
            400: None,
            409: None,
        },
    )
    def create(self, request):
        """
        Create a new document template.

        Accepts a nested ``fields`` array to define the template's dynamic fields.
        Returns 409 when a template with the same name already exists.
        """
        profile = self._get_profile(request)
        serializer = DocumentTemplateCreateUpdateSerializer(
            data=request.data, context={"instance": None}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        fields_data = data.pop("fields", [])

        template = DocumentTemplate.objects.create(
            name=data["name"],
            description=data.get("description", ""),
            category=data.get("category", "other"),
            content=data.get("content", ""),
            visibility=data.get("visibility", "private"),
            status=data.get("status", "draft"),
            is_system_template=False,
            is_active=True,
            created_by=profile,
        )

        for field_data in fields_data:
            TemplateField.objects.create(template=template, **field_data)

        result = (
            DocumentTemplate.objects.prefetch_related("fields")
            .select_related("created_by__user")
            .get(pk=template.pk)
        )
        return Response(
            DocumentTemplateDetailSerializer(result).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Document Templates"],
        summary="Full update a template",
        description=(
            "Replace all fields of an existing template. "
            "System templates and templates not owned by the user return 403."
        ),
        request=DocumentTemplateCreateUpdateSerializer,
        responses={
            200: DocumentTemplateDetailSerializer,
            400: None,
            403: None,
            404: None,
            409: None,
        },
    )
    def update(self, request, pk=None):
        """
        Full update — replaces name, description, category, content, visibility,
        status, and all field definitions.

        Returns 403 for system templates or when the user lacks edit rights.
        """
        from .enums import ErrorCode

        template = get_template_or_404(pk)

        if template.is_system_template:
            return Response(
                self._build_error(
                    ErrorCode.SYSTEM_TEMPLATE_IMMUTABLE,
                    "System templates cannot be modified.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )
        if not self._can_edit(request, template):
            return Response(
                self._build_error(
                    ErrorCode.FORBIDDEN,
                    "You do not have permission to edit this template.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DocumentTemplateCreateUpdateSerializer(
            data=request.data, context={"instance": template}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        fields_data = data.pop("fields", [])

        for attr, value in data.items():
            setattr(template, attr, value)
        template.save()

        # Replace fields in full
        template.fields.all().delete()
        for field_data in fields_data:
            TemplateField.objects.create(template=template, **field_data)

        result = (
            DocumentTemplate.objects.prefetch_related("fields")
            .select_related("created_by__user")
            .get(pk=template.pk)
        )
        return Response(
            DocumentTemplateDetailSerializer(result).data, status=status.HTTP_200_OK
        )

    @extend_schema(
        tags=["Document Templates"],
        summary="Partial update a template",
        description=(
            "Update one or more fields of an existing template without replacing the rest. "
            "Providing ``fields`` replaces all field definitions."
        ),
        request=DocumentTemplatePartialUpdateSerializer,
        responses={
            200: DocumentTemplateDetailSerializer,
            400: None,
            403: None,
            404: None,
        },
    )
    def partial_update(self, request, pk=None):
        """
        Partial update — only the provided keys are modified.

        Providing ``fields`` in the payload replaces all field definitions.
        Returns 403 for system templates or insufficient permissions.
        """
        from .enums import ErrorCode

        template = get_template_or_404(pk)

        if template.is_system_template:
            return Response(
                self._build_error(
                    ErrorCode.SYSTEM_TEMPLATE_IMMUTABLE,
                    "System templates cannot be modified.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )
        if not self._can_edit(request, template):
            return Response(
                self._build_error(
                    ErrorCode.FORBIDDEN,
                    "You do not have permission to edit this template.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DocumentTemplatePartialUpdateSerializer(
            data=request.data, context={"instance": template}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        fields_data = data.pop("fields", None)

        for attr, value in data.items():
            setattr(template, attr, value)
        template.save()

        if fields_data is not None:
            template.fields.all().delete()
            for field_data in fields_data:
                TemplateField.objects.create(template=template, **field_data)

        result = (
            DocumentTemplate.objects.prefetch_related("fields")
            .select_related("created_by__user")
            .get(pk=template.pk)
        )
        return Response(
            DocumentTemplateDetailSerializer(result).data, status=status.HTTP_200_OK
        )

    @extend_schema(
        tags=["Document Templates"],
        summary="Soft delete a template",
        description=(
            "Deactivate a template by setting is_active=False. "
            "The record is never hard-deleted."
        ),
        responses={
            204: None,
            403: None,
            404: None,
        },
    )
    def destroy(self, request, pk=None):
        """
        Soft delete — sets is_active=False, never hard-deletes.

        Returns 403 for system templates or when the user lacks delete rights.
        """
        from .enums import ErrorCode

        template = get_template_or_404(pk)

        if template.is_system_template:
            return Response(
                self._build_error(
                    ErrorCode.SYSTEM_TEMPLATE_IMMUTABLE,
                    "System templates cannot be deleted.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )
        if not self._can_edit(request, template):
            return Response(
                self._build_error(
                    ErrorCode.FORBIDDEN,
                    "You do not have permission to delete this template.",
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        template.is_active = False
        template.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── extra actions ─────────────────────────────────────────────────────────

    @extend_schema(
        tags=["Document Templates"],
        summary="Duplicate a template",
        description=(
            "Clone an existing template (including all its field definitions) as a new "
            "PRIVATE copy owned by the requesting user. Works regardless of the source "
            "template's visibility or ownership."
        ),
        responses={
            201: DocumentTemplateDetailSerializer,
            404: None,
        },
    )
    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        """
        Clone a template as a new PRIVATE user-owned copy.

        The clone always gets visibility=PRIVATE, is_system_template=False, and
        a 'Copy of …' name prefix regardless of the source template's settings.
        """
        template = get_template_or_404(pk)
        profile = self._get_profile(request)
        new_template = clone_template(template, profile)
        return Response(
            DocumentTemplateDetailSerializer(new_template).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Document Templates"],
        summary="Generate document from template",
        description=(
            "Instantiate a template by supplying values for its dynamic fields. "
            "All required fields must be provided. Placeholders in the template content "
            "are replaced and the resulting document is persisted."
        ),
        request=TemplateUseSerializer,
        responses={
            201: TemplateGeneratedDocumentSerializer,
            404: None,
            422: None,
        },
    )
    @action(detail=True, methods=["post"])
    def use(self, request, pk=None):
        """
        Create a new document from a template with user-supplied field values.

        Request body:
            document_name  — name for the generated document
            field_values   — dict mapping field_key → value

        Returns 422 with field-level errors when required fields are missing.
        Returns 404 when the template does not exist or is inactive.
        """
        from .enums import ErrorCode

        template = get_template_or_404(pk)

        serializer = TemplateUseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        field_values = serializer.validated_data.get("fieldValues", {})
        output_format = serializer.validated_data.get("format", "pdf")
        document_name = (
            serializer.validated_data.get("document_name")
            or f"{template.name} — {output_format.upper()}"
        )

        missing = validate_template_fields(template.fields.all(), field_values)
        if missing:
            return Response(
                self._build_error(
                    ErrorCode.VALIDATION_ERROR,
                    "Required fields are missing.",
                    details={"missing_fields": missing},
                ),
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        resolved_content = resolve_template_content(template.content, field_values)

        profile = self._get_profile(request)
        generated_doc = TemplateGeneratedDocument.objects.create(
            name=document_name,
            source_template=template,
            resolved_content=resolved_content,
            field_values=field_values,
            created_by=profile,
        )

        return Response(
            TemplateGeneratedDocumentSerializer(generated_doc).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Document Templates"],
        summary="List template categories",
        description="Return all available TemplateCategory enum values with labels.",
        responses={200: None},
    )
    @action(detail=False, methods=["get"])
    def categories(self, request):
        """
        Return all TemplateCategory enum values with labels for the frontend dropdown.

        Response shape:
            [{"value": "HR", "label": "HR"}, ...]
        """
        from .enums import TemplateCategory

        data = [
            {"value": value, "label": label}
            for value, label in TemplateCategory.choices
        ]
        return Response(data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Document Templates"],
        summary="List generated documents",
        description="Return all TemplateGeneratedDocument records visible to the requesting user.",
        responses={200: TemplateGeneratedDocumentSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def generated(self, request):
        """Return generated documents created by this user (admins see all)."""
        profile = self._get_profile(request)
        if self._is_admin(request):
            qs = TemplateGeneratedDocument.objects.all()
        elif profile:
            qs = TemplateGeneratedDocument.objects.filter(created_by=profile)
        else:
            qs = TemplateGeneratedDocument.objects.none()

        qs = qs.select_related("source_template", "created_by__user").order_by(
            "-created_at"
        )

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = TemplateGeneratedDocumentSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = TemplateGeneratedDocumentSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Document Templates"],
    summary="Personal template editor snippets",
    description=(
        "CRUD for reusable HTML fragments owned by the authenticated user's profile. "
        "Used by the HR template builder snippets menu."
    ),
)
class UserTemplateSnippetViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = UserTemplateSnippetSerializer

    def get_queryset(self):
        profile = getattr(self.request.user, "profile", None)
        if profile is None:
            return UserTemplateSnippet.objects.none()
        return UserTemplateSnippet.objects.filter(user_profile=profile)

    def perform_create(self, serializer):
        profile = getattr(self.request.user, "profile", None)
        if profile is None:
            raise PermissionDenied(detail="User profile required.")
        serializer.save(user_profile=profile)


# ──────────────────────────────────────────
# Notifications (in-app)
# ──────────────────────────────────────────


@extend_schema(tags=["Notifications"])
class NotificationViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/notifications/                 list current user's notifications
    POST   /api/notifications/{id}/mark-read/  mark a single notification read
    POST   /api/notifications/mark-all-read/   mark all current-user notifications read
    GET    /api/notifications/unread-count/    return { count: int }
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        profile = getattr(self.request.user, "profile", None)
        if profile is None:
            return Notification.objects.none()
        qs = Notification.objects.filter(recipient=profile)
        unread_only = self.request.query_params.get("unread", "").lower() == "true"
        if unread_only:
            qs = qs.filter(is_read=False)
        return qs

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at"])
        return Response(
            self.get_serializer(notification).data, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated = (
            self.get_queryset()
            .filter(is_read=False)
            .update(is_read=True, read_at=timezone.now())
        )
        return Response({"updated": updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = self.get_queryset().filter(is_read=False).count()
        return Response({"count": count}, status=status.HTTP_200_OK)
