import csv
import hashlib
import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from urllib.parse import unquote, urlparse

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.validators import URLValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Avg, Exists, Max, OuterRef, Prefetch, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
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
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .constants import (
    CPF_LEVEL_CHANGE_FILTERSET_FIELDS,
    CPF_LEVEL_CHANGE_ORDERING_FIELDS,
    CPF_LEVEL_CHANGE_SEARCH_FIELDS,
    EMPLOYEE_PROFILE_FILTERSET_FIELDS,
    EMPLOYEE_PROFILE_ORDERING_FIELDS,
    EMPLOYEE_PROFILE_SEARCH_FIELDS,
    LEAVE_BALANCE_SNAPSHOT_FILTERSET_FIELDS,
    LEAVE_BALANCE_SNAPSHOT_ORDERING_FIELDS,
    LEAVE_MONTHLY_AGGREGATE_FILTERSET_FIELDS,
    LEAVE_MONTHLY_AGGREGATE_ORDERING_FIELDS,
)
from .enums import (
    ProjectAssignmentStatus,
    TimeEntryAuditEventType,
    TimeEntrySourceChangeFlag,
    TimeEntrySourceType,
    TimeEntryStatus,
)
from .models import (
    Announcement,
    AnnouncementComment,
    AnnouncementReaction,
    Answer,
    Application,
    Asset,
    AssetStatus,
    Assignment,
    BenefitCatalog,
    BonusRecord,
    Certificate,
    ChecklistInstance,
    ChecklistTask,
    ChecklistTemplate,
    CompensationPolicy,
    ConferenceCourseRegistration,
    CPFLevel,
    CPFLevelChange,
    Department,
    DiscordAnnouncementChannel,
    Document,
    DocumentTemplate,
    DocumentType,
    EmployeeDocument,
    EmployeeProfileChangeHistory,
    JiraConnection,
    JiraIssueMapping,
    JiraProjectMapping,
    JiraUserMapping,
    JobListing,
    JobListingStatus,
    LeaveAdjustment,
    LeaveBalance,
    LeaveBalanceSnapshot,
    LeaveMonthlyAggregate,
    LeavePolicy,
    LeaveRequest,
    Notification,
    PeerSession,
    PerformanceReview,
    PerformanceReviewActionPoint,
    PerformanceReviewAttachment,
    PerformanceReviewHistoryEvent,
    PerformanceReviewNote,
    PerformanceReviewReminder,
    Permission,
    Project,
    ProjectAssignment,
    PromotionHistory,
    PulseCheck,
    Question,
    ReplacementLog,
    Role,
    ScheduledMaintenance,
    Survey,
    TaskTemplate,
    TemplateField,
    TemplateGeneratedDocument,
    TempoAccountMapping,
    TempoConnection,
    TempoProjectMapping,
    TempoTeamMapping,
    TempoUserMapping,
    TimeEntry,
    TimeImportBatch,
    TimeTask,
    TrainingBudget,
    TrainingEntry,
    UserProfile,
    UserTemplateSnippet,
)
from .models import (
    Response as SurveyResponse,
)
from .permissions import (
    CanRefreshLeaveAnalytics,
    CanReviewApplication,
    IsCompensationAdminOrOwnReadOnly,
    IsCPFLevelChangeEditor,
    IsEmployeeOrHR,
    IsHRAdminForAdjustment,
    IsHRAdminOrReadOnlyOwnProfile,
    IsHrOrAdmin,
    IsLeaveAnalyticsViewer,
    IsManagerForApproval,
    IsReviewCreator,
    IsReviewEditor,
    IsReviewViewer,
    IsTrainingBudgetEditor,
    _get_user_profile,
    can_add_announcement_reactions,
    can_attach_review_documents,
    can_edit_review_note,
    can_manage_announcements,
    can_manage_cpf_level_changes,
    can_moderate_announcement_comments,
    can_schedule_announcements,
    can_view_anniversaries,
    can_view_announcements,
    can_view_asset,
    can_view_asset_maintenance_logs,
    can_view_assignment,
    can_view_birthdays,
    get_asset_capabilities,
    get_asset_permissions,
    get_asset_scope,
    has_asset_permission,
    has_leave_analytics_view_permission,
    has_review_permission,
    is_compensation_admin,
)
from .serializers import (
    AnnouncementCommentCreateSerializer,
    AnnouncementCommentSerializer,
    AnnouncementDetailSerializer,
    AnnouncementListSerializer,
    AnnouncementReactionSerializer,
    AnnouncementReactionToggleSerializer,
    AnnouncementWriteSerializer,
    APIRootResponseSerializer,
    ApplicationCreateSerializer,
    ApplicationSerializer,
    ApplicationStatusUpdateSerializer,
    ApplicationWithdrawSerializer,
    AssetCreateSerializer,
    AssetExportRequestSerializer,
    AssetSerializer,
    AssignmentCreateSerializer,
    AssignmentRejectReturnSerializer,
    AssignmentRequestReturnSerializer,
    AssignmentReturnSerializer,
    AssignmentSerializer,
    AvatarUploadSerializer,
    BenefitCatalogSerializer,
    BonusRecordSerializer,
    BulkIdsSerializer,
    CelebrationQuerySerializer,
    CertificateCreateUpdateSerializer,
    CertificateDetailSerializer,
    CertificateListSerializer,
    ChecklistInstanceCreateSerializer,
    ChecklistInstanceSerializer,
    ChecklistTaskSerializer,
    ChecklistTemplateSerializer,
    CompensationPolicySerializer,
    ConferenceCourseRegistrationCreateUpdateSerializer,
    ConferenceCourseRegistrationListSerializer,
    CPFLevelChangeSerializer,
    CPFLevelChangeWriteSerializer,
    CPFProgressionSerializer,
    DiscordAnnouncementChannelSerializer,
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
    JiraAssignedIssueImportSerializer,
    JiraConnectionSerializer,
    JiraImportCommitSerializer,
    JiraImportPreviewSerializer,
    JiraIssueMappingSerializer,
    JiraMappingMutationSerializer,
    JiraProjectDiscoverySerializer,
    JiraProjectMappingSerializer,
    JiraUserMappingSerializer,
    JobListingDetailSerializer,
    JobListingListSerializer,
    JobListingWriteSerializer,
    LeaveAdjustmentSerializer,
    LeaveAnalyticsDepartmentRowSerializer,
    LeaveAnalyticsEmployeeHistorySerializer,
    LeaveAnalyticsEmployeeSummarySerializer,
    LeaveAnalyticsMonthRowSerializer,
    LeaveAnalyticsRefreshResponseSerializer,
    LeaveAnalyticsYearTotalsSerializer,
    LeaveBalanceSerializer,
    LeaveBalanceSnapshotSerializer,
    LeaveMonthlyAggregateSerializer,
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
    PeerSessionCreateUpdateSerializer,
    PeerSessionDetailSerializer,
    PeerSessionListSerializer,
    PerformanceReviewActionPointSerializer,
    PerformanceReviewAttachmentSerializer,
    PerformanceReviewCreateUpdateSerializer,
    PerformanceReviewDetailSerializer,
    PerformanceReviewHistoryEventSerializer,
    PerformanceReviewListSerializer,
    PerformanceReviewNoteSerializer,
    PerformanceReviewReminderSerializer,
    ProjectAssignmentSerializer,
    ProjectDetailSerializer,
    ProjectListItemSerializer,
    ProjectSerializer,
    PromotionHistorySerializer,
    PromotionHistoryWriteSerializer,
    PulseCheckSerializer,
    QuestionSerializer,
    RegisterSerializer,
    ReplacementLogSerializer,
    ReplacementLogUpdateSerializer,
    RequestSignatureSerializer,
    ReturnRequestQueueSerializer,
    ScheduledMaintenanceCancelSerializer,
    ScheduledMaintenanceCompleteSerializer,
    ScheduledMaintenanceSerializer,
    SignatureAuditLogSerializer,
    SignDocumentSerializer,
    SurveySerializer,
    TemplateGeneratedDocumentSerializer,
    TemplateUseSerializer,
    TempoAccountMappingSerializer,
    TempoConnectionSerializer,
    TempoImportCommitSerializer,
    TempoImportPreviewSerializer,
    TempoMappingMutationSerializer,
    TempoProjectDiscoverySerializer,
    TempoProjectMappingSerializer,
    TempoTeamMappingSerializer,
    TempoUserMappingSerializer,
    TimeDocumentImportColumnMapSerializer,
    TimeDocumentImportUploadSerializer,
    TimeEntryRejectSerializer,
    TimeEntrySerializer,
    TimeEntrySubmitWeekSerializer,
    TimeImportBatchSerializer,
    TimeSourceChangeResolveSerializer,
    TimeTaskSerializer,
    TokenSerializer,
    TrainingBudgetSerializer,
    TrainingEntryCreateUpdateSerializer,
    TrainingEntryDetailSerializer,
    TrainingEntryListSerializer,
    UpcomingCelebrationSerializer,
    UpdatePermissionsSerializer,
    UpdateRoleSerializer,
    UploadRolePermissionsResponseSerializer,
    UserProfileSerializer,
    UserSerializer,
    UserTemplateSnippetSerializer,
    VacationCapabilitiesSerializer,
)
from .services.announcement_notification_service import (
    announcement_is_published,
    notify_announcement_published,
)
from .services.asset_qr import ensure_asset_qr_code
from .services.celebrations import build_upcoming_profile_celebrations
from .services.cpf_service import (
    build_cpf_progression,
    sync_employee_current_cpf_level,
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
from .services.document_time_import_service import (
    commit_document_import,
    map_columns,
    require_document_import_admin,
    upload_document_import,
    validate_batch_rows,
)
from .services.jira_time_import_service import (
    JiraAssignedIssueImportOptions,
    JiraImportFilters,
    commit_jira_worklogs,
    discover_jira_project_ids,
    fetch_jira_worklogs,
    import_assigned_jira_issues,
    preview_jira_worklogs,
    require_jira_admin,
    test_jira_connection,
)
from .services.performance_review_service import (
    materialize_performance_review_reminders,
    sync_performance_review_reminders_for_review,
)
from .services.profile_change_history import log_employee_profile_change, role_value
from .services.project_service import (
    ProjectFilterError,
    annotate_assignment_counts,
    apply_project_filters,
    archive_project,
    can_modify_projects,
    can_view_project,
    reactivate_project,
    visible_projects_for,
)
from .services.tempo_time_import_service import (
    TempoImportFilters,
    commit_tempo_worklogs,
    discover_tempo_project_ids,
    fetch_tempo_worklogs,
    preview_tempo_worklogs,
    require_tempo_admin,
    test_tempo_connection,
)
from .services.time_tracking_service import (
    active_time_tracking_allocations,
    approve_entry,
    can_delete_time_entry,
    can_edit_time_entry,
    can_view_employee_timesheet,
    find_duplicate,
    fingerprint_for_entry,
    has_time_tracking_permission,
    log_time_entry_event,
    profile_for_user,
    reject_entry,
    submit_entries_for_week,
    weekly_allocation_summary,
)
from .services.training_budget_service import recalculate_budget
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
                        "scheduled_maintenance": "GET/POST /api/scheduled-maintenance/",
                        "maintenance_logs": "GET/POST /api/replacement-logs/",
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
            from django.contrib.auth.models import update_last_login

            update_last_login(None, user)
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
                from django.contrib.auth.models import update_last_login

                update_last_login(None, user)
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

            from django.contrib.auth.models import update_last_login

            update_last_login(None, user)
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
            # Bump last_login on refresh too so the AI assistant's
            # recent-auth gate stays accurate while the session is active.
            if getattr(user, "is_authenticated", False):
                from django.contrib.auth.models import update_last_login

                update_last_login(None, user)
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
        description=(
            "Exact match on employment status (e.g. active, probation, on_leave, inactive)."
        ),
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
            instance, data=request.data, partial=True, context={"request": request}
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


class DepartmentObjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    color = serializers.CharField()
    color_soft = serializers.CharField()
    employee_count = serializers.IntegerField()
    head_employee_id = serializers.IntegerField(allow_null=True)


class DepartmentListResponseSerializer(serializers.ListSerializer):
    child = DepartmentObjectSerializer()


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
        responses={200: DepartmentObjectSerializer(many=True)},
    )
    def get(self, request):
        from django.db.models import Count
        from django.db.models import Q as _Q

        qs = Department.objects.order_by("name").annotate(
            _employee_count=Count(
                "members",
                filter=_Q(members__is_active=True),
                distinct=True,
            )
        )
        data = [
            {
                "id": d.id,
                "name": d.name,
                "color": d.color,
                "color_soft": d.color_soft,
                "employee_count": d._employee_count,
                "head_employee_id": d.head_employee_id,
            }
            for d in qs
        ]
        return Response(data, status=status.HTTP_200_OK)


class ProjectPagination(pagination.PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


@extend_schema(tags=["projects"])
class ProjectListView(APIView):
    """List projects visible to the caller, or create a new project."""

    permission_classes = [IsAuthenticated]
    pagination_class = ProjectPagination

    @extend_schema(
        parameters=[
            OpenApiParameter("search", str, description="Match name or client."),
            OpenApiParameter("status", str, description="Filter by project status."),
            OpenApiParameter("owner", int, description="Owner UserProfile id."),
            OpenApiParameter("active_from", str, description="ISO date (YYYY-MM-DD)."),
            OpenApiParameter("active_to", str, description="ISO date (YYYY-MM-DD)."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ProjectListItemSerializer(many=True)},
    )
    def get(self, request):
        queryset = visible_projects_for(request.user)
        try:
            queryset = apply_project_filters(queryset, request.query_params)
        except ProjectFilterError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        queryset = annotate_assignment_counts(queryset).order_by("name")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ProjectListItemSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        request=ProjectSerializer,
        responses={201: ProjectDetailSerializer, 400: None, 403: None},
    )
    def post(self, request):
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to create projects."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ProjectSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        project = serializer.save()
        annotated = annotate_assignment_counts(
            Project.objects.filter(pk=project.pk)
        ).first()
        return Response(
            ProjectDetailSerializer(annotated).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["projects"])
class ProjectDetailView(APIView):
    """Retrieve, update, archive, or reactivate a single project."""

    permission_classes = [IsAuthenticated]

    def _get_project(self, pk):
        return annotate_assignment_counts(Project.objects.filter(pk=pk)).first()

    @extend_schema(responses={200: ProjectDetailSerializer, 404: None})
    def get(self, request, pk):
        project = self._get_project(pk)
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_view_project(request.user, project):
            return Response(
                {"error": "You do not have permission to view this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(ProjectDetailSerializer(project).data)

    @extend_schema(
        request=ProjectSerializer,
        responses={200: ProjectDetailSerializer, 400: None, 403: None, 404: None},
    )
    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    @extend_schema(
        request=ProjectSerializer,
        responses={200: ProjectDetailSerializer, 400: None, 403: None, 404: None},
    )
    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to update projects."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ProjectSerializer(project, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        project = serializer.save()
        annotated = self._get_project(project.pk)
        return Response(ProjectDetailSerializer(annotated).data)

    @extend_schema(responses={204: None, 403: None, 404: None})
    def delete(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to delete projects."},
                status=status.HTTP_403_FORBIDDEN,
            )
        project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["projects"])
class ProjectArchiveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ProjectDetailSerializer, 403: None, 404: None})
    def post(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to archive projects."},
                status=status.HTTP_403_FORBIDDEN,
            )
        archive_project(project)
        annotated = annotate_assignment_counts(
            Project.objects.filter(pk=project.pk)
        ).first()
        return Response(ProjectDetailSerializer(annotated).data)


@extend_schema(tags=["projects"])
class ProjectReactivateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ProjectDetailSerializer, 403: None, 404: None})
    def post(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to reactivate projects."},
                status=status.HTTP_403_FORBIDDEN,
            )
        reactivate_project(project)
        annotated = annotate_assignment_counts(
            Project.objects.filter(pk=project.pk)
        ).first()
        return Response(ProjectDetailSerializer(annotated).data)


@extend_schema(tags=["projects"])
class ProjectActivityView(APIView):
    """Synthesize an activity feed for a project from existing timestamps.

    No dedicated audit table yet. Events derived from:
      - Project.created_at, Project.updated_at, Project.status (archive marker)
      - ProjectAssignment.created_at (assignment created)
      - ProjectAssignment.updated_at when end_date is set and status=completed
        (assignment ended)
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: None, 403: None, 404: None})
    def get(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_view_project(request.user, project):
            return Response(
                {"error": "You do not have permission to view this project."},
                status=status.HTTP_403_FORBIDDEN,
            )

        owner_name = ""
        if project.owner is not None:
            owner_name = project.owner.full_name or project.owner.user.username

        events: list[dict[str, Any]] = []
        events.append(
            {
                "id": f"p-{project.id}-created",
                "at": project.created_at.isoformat(),
                "actor": owner_name or "—",
                "message": f"Created project {project.name}",
            }
        )

        if (
            project.updated_at
            and project.created_at
            and (project.updated_at - project.created_at).total_seconds() > 1
        ):
            archived = project.status == "archived"
            events.append(
                {
                    "id": f"p-{project.id}-updated",
                    "at": project.updated_at.isoformat(),
                    "actor": owner_name or "—",
                    "message": (
                        f"Archived project {project.name}"
                        if archived
                        else f"Updated project {project.name}"
                    ),
                }
            )

        assignments = (
            ProjectAssignment.objects.select_related("user_profile__user")
            .filter(project=project)
            .order_by("-created_at")
        )
        for a in assignments:
            who = (
                a.user_profile.full_name or a.user_profile.user.username
                if a.user_profile
                else "Unknown"
            )
            role_part = f" as {a.role}" if a.role else ""
            events.append(
                {
                    "id": f"a-{a.id}-created",
                    "at": a.created_at.isoformat(),
                    "actor": "—",
                    "message": (
                        f"Assigned {who}{role_part} at {a.allocation_percentage}%"
                    ),
                }
            )
            if (
                a.end_date
                and a.updated_at
                and (a.updated_at - a.created_at).total_seconds() > 1
            ):
                events.append(
                    {
                        "id": f"a-{a.id}-ended",
                        "at": a.updated_at.isoformat(),
                        "actor": "—",
                        "message": (f"Ended assignment for {who} on {a.end_date}"),
                    }
                )

        events.sort(key=lambda e: e["at"], reverse=True)
        return Response({"events": events})


@extend_schema(tags=["projects"])
class ProjectAssignmentListCreateView(APIView):
    """List or create assignments for a project."""

    permission_classes = [IsAuthenticated]

    def _get_project(self, pk):
        return Project.objects.filter(pk=pk).first()

    def _close_overlapping_history(self, assignment):
        previous_assignments = ProjectAssignment.objects.filter(
            user_profile=assignment.user_profile,
            project=assignment.project,
            start_date__lt=assignment.start_date,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=assignment.start_date))
        for previous in previous_assignments:
            previous.end_date = assignment.start_date - timedelta(days=1)
            if previous.status == ProjectAssignmentStatus.ACTIVE:
                previous.status = ProjectAssignmentStatus.COMPLETED
            previous.save(update_fields=["end_date", "status", "updated_at"])

    @extend_schema(responses={200: ProjectAssignmentSerializer(many=True), 404: None})
    def get(self, request, project_pk):
        project = self._get_project(project_pk)
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_view_project(request.user, project):
            return Response(
                {"error": "You do not have permission to view this project."},
                status=status.HTTP_403_FORBIDDEN,
            )
        assignments = (
            ProjectAssignment.objects.select_related("user_profile__user")
            .filter(project=project)
            .order_by("-start_date")
        )
        return Response(ProjectAssignmentSerializer(assignments, many=True).data)

    @extend_schema(
        request=ProjectAssignmentSerializer,
        responses={201: ProjectAssignmentSerializer, 400: None, 403: None, 404: None},
    )
    def post(self, request, project_pk):
        project = self._get_project(project_pk)
        if project is None:
            return Response(
                {"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to manage project assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )
        data = dict(request.data)
        data["project_id"] = project.pk
        serializer = ProjectAssignmentSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            assignment = serializer.save()
            self._close_overlapping_history(assignment)
        return Response(
            ProjectAssignmentSerializer(assignment).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["projects"])
class ProjectAssignmentDetailView(APIView):
    """Retrieve, update, or end a single project assignment."""

    permission_classes = [IsAuthenticated]
    ALLOCATION_FIELDS = {"allocation_percentage", "weekly_allocation_hours"}

    def _get_assignment(self, pk):
        return (
            ProjectAssignment.objects.select_related("user_profile__user", "project")
            .filter(pk=pk)
            .first()
        )

    def _allocation_change_requires_split(self, assignment, validated_data):
        if not self.ALLOCATION_FIELDS.intersection(validated_data):
            return False
        today = timezone.localdate()
        if assignment.status != ProjectAssignmentStatus.ACTIVE:
            return False
        if assignment.start_date >= today:
            return False
        if assignment.end_date and assignment.end_date < today:
            return False

        if "allocation_percentage" in validated_data and int(
            validated_data["allocation_percentage"]
        ) != int(assignment.allocation_percentage):
            return True
        if "weekly_allocation_hours" in validated_data:
            old_hours = (
                Decimal(assignment.weekly_allocation_hours)
                if assignment.weekly_allocation_hours is not None
                else None
            )
            new_hours = (
                Decimal(validated_data["weekly_allocation_hours"])
                if validated_data["weekly_allocation_hours"] is not None
                else None
            )
            return old_hours != new_hours
        return False

    @transaction.atomic
    def _split_assignment_for_future_allocation(self, assignment, validated_data):
        today = timezone.localdate()
        original_end_date = assignment.end_date
        assignment.end_date = today - timedelta(days=1)
        assignment.status = ProjectAssignmentStatus.COMPLETED
        assignment.save(update_fields=["end_date", "status", "updated_at"])

        new_end_date = validated_data.get("end_date", original_end_date)
        if new_end_date and new_end_date < today:
            new_end_date = None

        new_assignment = ProjectAssignment.objects.create(
            user_profile=validated_data.get("user_profile", assignment.user_profile),
            project=validated_data.get("project", assignment.project),
            role=validated_data.get("role", assignment.role),
            allocation_percentage=validated_data.get(
                "allocation_percentage", assignment.allocation_percentage
            ),
            weekly_allocation_hours=validated_data.get(
                "weekly_allocation_hours", assignment.weekly_allocation_hours
            ),
            start_date=today,
            end_date=new_end_date,
            status=validated_data.get("status", ProjectAssignmentStatus.ACTIVE),
            notes=validated_data.get("notes", assignment.notes),
        )
        return new_assignment

    @extend_schema(responses={200: ProjectAssignmentSerializer, 404: None})
    def get(self, request, pk):
        assignment = self._get_assignment(pk)
        if assignment is None:
            return Response(
                {"error": "Assignment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_view_project(request.user, assignment.project):
            return Response(
                {"error": "You do not have permission to view this assignment."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(ProjectAssignmentSerializer(assignment).data)

    @extend_schema(
        request=ProjectAssignmentSerializer,
        responses={200: ProjectAssignmentSerializer, 400: None, 403: None, 404: None},
    )
    def patch(self, request, pk):
        assignment = self._get_assignment(pk)
        if assignment is None:
            return Response(
                {"error": "Assignment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to update assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ProjectAssignmentSerializer(
            assignment, data=request.data, partial=True
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if self._allocation_change_requires_split(
            assignment, serializer.validated_data
        ):
            assignment = self._split_assignment_for_future_allocation(
                assignment, serializer.validated_data
            )
        else:
            assignment = serializer.save()
        return Response(ProjectAssignmentSerializer(assignment).data)

    @extend_schema(responses={204: None, 403: None, 404: None})
    def delete(self, request, pk):
        assignment = self._get_assignment(pk)
        if assignment is None:
            return Response(
                {"error": "Assignment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to delete assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )
        assignment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["projects"])
class ProjectAssignmentEndView(APIView):
    """Set the end date for an assignment (does not delete the record)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: ProjectAssignmentSerializer, 400: None, 403: None, 404: None},
    )
    def post(self, request, pk):
        assignment = (
            ProjectAssignment.objects.select_related("user_profile__user", "project")
            .filter(pk=pk)
            .first()
        )
        if assignment is None:
            return Response(
                {"error": "Assignment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not can_modify_projects(request.user):
            return Response(
                {"error": "You do not have permission to end assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )
        end_date = request.data.get("end_date")
        if not end_date:
            return Response(
                {"end_date": "End date is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ProjectAssignmentSerializer(
            assignment,
            data={"end_date": end_date, "status": "completed"},
            partial=True,
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        assignment = serializer.save()
        return Response(ProjectAssignmentSerializer(assignment).data)


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
                role_obj = Role.objects.get(name__iexact=role)
                cpf_levels = role_obj.cpf_levels.order_by("order", "name").values_list(
                    "name", flat=True
                )

                return Response(
                    {
                        "requested_role": role_obj.name,
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
        cpf_levels = (
            CPFLevel.objects.order_by("name").values_list("name", flat=True).distinct()
        )

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
            description=(
                "Filter by asset status (active, maintenance, retired, lost, returned, damaged)"
            ),
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

        ensure_asset_qr_code(asset)
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
    responses={200: OpenApiTypes.BINARY, 403: None, 404: None},
    description="Download the stored PNG QR code for an asset.",
)
class AssetQRCodeView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return Asset.objects.get(pk=pk)
        except Asset.DoesNotExist:
            return None

    def get(self, request, pk):
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

        ensure_asset_qr_code(asset)
        if not asset.qr_code_image or not default_storage.exists(
            asset.qr_code_image.name
        ):
            return Response(
                {"error": "Asset QR code image not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        with default_storage.open(asset.qr_code_image.name, "rb") as qr_file:
            response = HttpResponse(qr_file.read(), content_type="image/png")
        filename = asset.qr_code_image.name.rsplit("/", 1)[-1]
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


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
)
class ReplacementLogListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
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
                description="Filter by user who logged or performed replacement",
                required=False,
            ),
        ],
        responses={200: ReplacementLogSerializer(many=True)},
        description="Get list of all replacement logs with filtering options",
    )
    def get(self, request):
        """Get list of replacement logs with optional filtering"""
        if not can_view_asset_maintenance_logs(request.user):
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
        description=(
            "Create a new replacement log. Required fields are asset, reason, "
            "and date. The replaced_by actor is set from the authenticated user."
        ),
    )
    def post(self, request):
        """Create a new replacement log"""
        if not has_asset_permission(request.user, "log_asset_replacement"):
            return Response(
                {"error": "You do not have permission to log asset changes."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ReplacementLogSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(replaced_by=request.user.profile)
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
        if not can_view_asset_maintenance_logs(request.user):
            return Response(
                {"error": "You do not have permission to view asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ReplacementLogSerializer(log)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=ReplacementLogUpdateSerializer,
        responses={200: ReplacementLogSerializer, 400: None, 404: None},
        description=(
            "Update a replacement log. The replaced_by actor remains "
            "server-controlled and cannot be changed by clients."
        ),
    )
    def put(self, request, pk):
        """Update replacement log"""
        return self._update(request, pk)

    @extend_schema(
        request=ReplacementLogUpdateSerializer,
        responses={200: ReplacementLogSerializer, 400: None, 404: None},
        description=(
            "Partially update a replacement log. The replaced_by actor remains "
            "server-controlled and cannot be changed by clients."
        ),
    )
    def patch(self, request, pk):
        """Partially update replacement log"""
        return self._update(request, pk)

    def _update(self, request, pk):
        log = self.get_object(pk)
        if not log:
            return Response(
                {"error": "Replacement log not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if not has_asset_permission(request.user, "log_asset_replacement"):
            return Response(
                {"error": "You do not have permission to update asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ReplacementLogSerializer(log, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
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
        if not has_asset_permission(request.user, "log_asset_replacement"):
            return Response(
                {"error": "You do not have permission to delete asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        log.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Asset Management"],
    responses={200: ScheduledMaintenanceSerializer(many=True)},
    description="List or create one-off scheduled asset maintenance",
)
class ScheduledMaintenanceListView(APIView):
    permission_classes = [IsAuthenticated]

    def _base_queryset(self):
        return ScheduledMaintenance.objects.select_related(
            "asset",
            "owner__user",
            "created_by__user",
            "completed_log",
            "completed_log__asset",
            "completed_log__replacement_asset",
            "completed_log__replaced_by__user",
        )

    def _visible_queryset(self, request):
        schedules = self._base_queryset()
        if can_view_asset_maintenance_logs(request.user):
            return schedules

        if not has_asset_permission(request.user, "view_own_assets"):
            return None

        try:
            profile = request.user.profile
        except Exception:
            return ScheduledMaintenance.objects.none()

        return schedules.filter(
            status=ScheduledMaintenance.Status.SCHEDULED,
            asset__assignments__employee=profile,
            asset__assignments__returned_at__isnull=True,
        ).distinct()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="asset",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter by asset ID",
                required=False,
            ),
            OpenApiParameter(
                name="owner",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter by responsible user profile ID",
                required=False,
            ),
            OpenApiParameter(
                name="status",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter by status: scheduled, completed, or cancelled",
                required=False,
            ),
            OpenApiParameter(
                name="due_from",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description="Filter by due date on or after this date",
                required=False,
            ),
            OpenApiParameter(
                name="due_to",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                description="Filter by due date on or before this date",
                required=False,
            ),
            OpenApiParameter(
                name="due_state",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter scheduled items by upcoming, due_today, or overdue",
                required=False,
            ),
            OpenApiParameter(
                name="maintenance_type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description=(
                    "Filter by maintenance type: preventive, repair, inspection, "
                    "warranty, replacement, or other"
                ),
                required=False,
            ),
        ],
        responses={200: ScheduledMaintenanceSerializer(many=True)},
    )
    def get(self, request):
        schedules = self._visible_queryset(request)
        if schedules is None:
            return Response(
                {"error": "You do not have permission to view asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        asset_filter = request.query_params.get("asset")
        if asset_filter:
            schedules = schedules.filter(asset_id=asset_filter)

        owner_filter = request.query_params.get("owner")
        if owner_filter:
            schedules = schedules.filter(owner_id=owner_filter)

        status_filter = request.query_params.get("status")
        if status_filter:
            schedules = schedules.filter(status=status_filter)

        due_from = request.query_params.get("due_from")
        if due_from:
            schedules = schedules.filter(due_date__gte=due_from)

        due_to = request.query_params.get("due_to")
        if due_to:
            schedules = schedules.filter(due_date__lte=due_to)

        maintenance_type = request.query_params.get("maintenance_type")
        if maintenance_type:
            schedules = schedules.filter(maintenance_type=maintenance_type)

        due_state = request.query_params.get("due_state")
        if due_state:
            today = timezone.localdate()
            schedules = schedules.filter(status=ScheduledMaintenance.Status.SCHEDULED)
            if due_state == "overdue":
                schedules = schedules.filter(due_date__lt=today)
            elif due_state == "due_today":
                schedules = schedules.filter(due_date=today)
            elif due_state == "upcoming":
                schedules = schedules.filter(due_date__gt=today)
            else:
                return Response(
                    {"due_state": "Use upcoming, due_today, or overdue."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = ScheduledMaintenanceSerializer(schedules, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=ScheduledMaintenanceSerializer,
        responses={201: ScheduledMaintenanceSerializer, 400: None},
        description=(
            "Create one-off scheduled maintenance. Required fields are asset, "
            "due_date, reason, and maintenance_type."
        ),
    )
    def post(self, request):
        if not has_asset_permission(request.user, "log_asset_replacement"):
            return Response(
                {"error": "You do not have permission to log asset changes."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ScheduledMaintenanceSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(created_by=request.user.profile)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Asset Management"],
    responses={200: ScheduledMaintenanceSerializer, 404: None},
    description="Get, update, complete, or cancel scheduled asset maintenance",
)
class ScheduledMaintenanceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return ScheduledMaintenance.objects.select_related(
                "asset",
                "owner__user",
                "created_by__user",
                "completed_log",
                "completed_log__asset",
                "completed_log__replacement_asset",
                "completed_log__replaced_by__user",
            ).get(pk=pk)
        except ScheduledMaintenance.DoesNotExist:
            return None

    def _can_view_schedule(self, user, schedule):
        if can_view_asset_maintenance_logs(user):
            return True
        if not has_asset_permission(user, "view_own_assets"):
            return False
        try:
            profile = user.profile
        except Exception:
            return False
        if schedule.status != ScheduledMaintenance.Status.SCHEDULED:
            return False
        return schedule.asset.assignments.filter(
            employee=profile,
            returned_at__isnull=True,
        ).exists()

    @extend_schema(responses={200: ScheduledMaintenanceSerializer, 404: None})
    def get(self, request, pk):
        schedule = self.get_object(pk)
        if not schedule:
            return Response(
                {"error": "Scheduled maintenance not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not self._can_view_schedule(request.user, schedule):
            return Response(
                {"error": "You do not have permission to view asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ScheduledMaintenanceSerializer(schedule)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=ScheduledMaintenanceSerializer,
        responses={200: ScheduledMaintenanceSerializer, 400: None, 404: None},
        description="Partially update scheduled maintenance while it is scheduled.",
    )
    def patch(self, request, pk):
        schedule = self.get_object(pk)
        if not schedule:
            return Response(
                {"error": "Scheduled maintenance not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not has_asset_permission(request.user, "log_asset_replacement"):
            return Response(
                {"error": "You do not have permission to update asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ScheduledMaintenanceSerializer(
            schedule, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ScheduledMaintenanceCompleteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Asset Management"],
        request=ScheduledMaintenanceCompleteSerializer,
        responses={200: ScheduledMaintenanceSerializer, 400: None, 404: None},
        description=(
            "Complete scheduled maintenance and create the linked historical "
            "maintenance log."
        ),
    )
    def post(self, request, pk):
        if not has_asset_permission(request.user, "log_asset_replacement"):
            return Response(
                {"error": "You do not have permission to log asset changes."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ScheduledMaintenanceCompleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        with transaction.atomic():
            try:
                schedule = (
                    ScheduledMaintenance.objects.select_for_update()
                    .select_related("asset")
                    .get(pk=pk)
                )
            except ScheduledMaintenance.DoesNotExist:
                return Response(
                    {"error": "Scheduled maintenance not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if schedule.status == ScheduledMaintenance.Status.COMPLETED:
                return Response(
                    {"error": "Scheduled maintenance is already completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if schedule.status == ScheduledMaintenance.Status.CANCELLED:
                return Response(
                    {"error": "Cancelled scheduled maintenance cannot be completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            replacement_log = ReplacementLog.objects.create(
                asset=schedule.asset,
                reason=data["reason"],
                date=data["date"],
                asset_status_before=data.get(
                    "asset_status_before", schedule.asset.status
                ),
                asset_status_after=data.get("asset_status_after"),
                asset_condition_before=data.get(
                    "asset_condition_before", schedule.asset.condition
                ),
                asset_condition_after=data.get("asset_condition_after"),
                replacement_asset=data.get("replacement_asset"),
                cost=data.get("cost"),
                replaced_by=request.user.profile,
            )
            asset_update_fields = []
            asset_status_after = data.get("asset_status_after")
            if asset_status_after:
                schedule.asset.status = asset_status_after
                asset_update_fields.append("status")
            asset_condition_after = data.get("asset_condition_after")
            if asset_condition_after:
                schedule.asset.condition = asset_condition_after
                asset_update_fields.append("condition")
            if asset_update_fields:
                schedule.asset.save(update_fields=asset_update_fields)

            schedule.status = ScheduledMaintenance.Status.COMPLETED
            schedule.completed_log = replacement_log
            schedule.save(update_fields=["status", "completed_log", "updated_at"])

        response_serializer = ScheduledMaintenanceSerializer(schedule)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


class ScheduledMaintenanceCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Asset Management"],
        request=ScheduledMaintenanceCancelSerializer,
        responses={200: ScheduledMaintenanceSerializer, 400: None, 404: None},
        description="Cancel scheduled maintenance with an optional reason.",
    )
    def post(self, request, pk):
        if not has_asset_permission(request.user, "log_asset_replacement"):
            return Response(
                {"error": "You do not have permission to update asset history."},
                status=status.HTTP_403_FORBIDDEN,
            )

        schedule = ScheduledMaintenance.objects.filter(pk=pk).first()
        if not schedule:
            return Response(
                {"error": "Scheduled maintenance not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if schedule.status != ScheduledMaintenance.Status.SCHEDULED:
            return Response(
                {"error": "Only scheduled maintenance can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ScheduledMaintenanceCancelSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        schedule.status = ScheduledMaintenance.Status.CANCELLED
        schedule.cancelled_reason = serializer.validated_data.get(
            "cancelled_reason", ""
        )
        schedule.save(update_fields=["status", "cancelled_reason", "updated_at"])

        response_serializer = ScheduledMaintenanceSerializer(schedule)
        return Response(response_serializer.data, status=status.HTTP_200_OK)


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


def _build_budget_warning(employee, fiscal_year):
    """Return a warning dict when usage crosses 80% or exceeds the allocation."""
    from decimal import Decimal

    from .constants import TRAINING_BUDGET_WARNING_THRESHOLD

    budget = TrainingBudget.objects.filter(
        employee=employee, fiscal_year=fiscal_year
    ).first()
    if budget is None or not budget.allocated_budget:
        return None

    ratio = (budget.used_budget or Decimal("0.00")) / budget.allocated_budget
    if ratio < TRAINING_BUDGET_WARNING_THRESHOLD:
        return None

    exceeded = budget.used_budget > budget.allocated_budget
    return {
        "level": "exceeded" if exceeded else "approaching_limit",
        "fiscal_year": budget.fiscal_year,
        "allocated_budget": str(budget.allocated_budget),
        "used_budget": str(budget.used_budget),
        "remaining_budget": str(budget.remaining_budget),
        "percent_used": int(round(float(budget.budget_percentage_used))),
    }


def _build_budget_warning(employee, fiscal_year):
    """Return a warning dict when usage crosses 80% or exceeds the allocation."""
    from decimal import Decimal

    from .constants import TRAINING_BUDGET_WARNING_THRESHOLD

    budget = TrainingBudget.objects.filter(
        employee=employee, fiscal_year=fiscal_year
    ).first()
    if budget is None or not budget.allocated_budget:
        return None

    ratio = (budget.used_budget or Decimal("0.00")) / budget.allocated_budget
    if ratio < TRAINING_BUDGET_WARNING_THRESHOLD:
        return None

    exceeded = budget.used_budget > budget.allocated_budget
    return {
        "level": "exceeded" if exceeded else "approaching_limit",
        "fiscal_year": budget.fiscal_year,
        "allocated_budget": str(budget.allocated_budget),
        "used_budget": str(budget.used_budget),
        "remaining_budget": str(budget.remaining_budget),
        "percent_used": int(round(float(budget.budget_percentage_used))),
    }


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
        recalculate_budget(instance.employee, instance.training_date.year)
        recalculate_budget(instance.employee, instance.training_date.year)

    def create(self, request, *args, **kwargs):
        """Override create to return response with status field."""
        response = super().create(request, *args, **kwargs)
        # Re-serialize the created instance with DetailSerializer to include status
        if response.status_code == 201 and hasattr(self, "created_instance"):
            detail_serializer = TrainingEntryDetailSerializer(self.created_instance)
            data = detail_serializer.data
            warning = _build_budget_warning(
                self.created_instance.employee,
                self.created_instance.training_date.year,
            )
            if warning is not None:
                data["budget_warning"] = warning
            response.data = data
            data = detail_serializer.data
            warning = _build_budget_warning(
                self.created_instance.employee,
                self.created_instance.training_date.year,
            )
            if warning is not None:
                data["budget_warning"] = warning
            response.data = data
        return response

    def perform_update(self, serializer):
        """Update entry (employee: own only, HR: any)."""
        previous = serializer.instance
        previous_year = previous.training_date.year if previous else None
        previous_employee = previous.employee if previous else None
        instance = serializer.save()
        self.updated_instance = instance
        recalculate_budget(instance.employee, instance.training_date.year)
        if previous_employee and (
            previous_employee.pk != instance.employee_id
            or previous_year != instance.training_date.year
        ):
            recalculate_budget(previous_employee, previous_year)

    def update(self, request, *args, **kwargs):
        """Override update to return response with Detail serializer shape."""
        response = super().update(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK and hasattr(
            self, "updated_instance"
        ):
            data = TrainingEntryDetailSerializer(self.updated_instance).data
            warning = _build_budget_warning(
                self.updated_instance.employee,
                self.updated_instance.training_date.year,
            )
            if warning is not None:
                data["budget_warning"] = warning
            response.data = data
        return response

    def perform_destroy(self, instance):
        """Delete entry (employee: own only, HR: any)."""
        employee = instance.employee
        year = instance.training_date.year
        employee = instance.employee
        year = instance.training_date.year
        instance.delete()
        recalculate_budget(employee, year)
        recalculate_budget(employee, year)

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
class PeerSessionViewSet(viewsets.ModelViewSet):
    """
    CRUD for peer-to-peer learning sessions.

    Employees can log/view their own sessions; HR/admins can manage any
    employee's sessions and filter by employee via the ``employee`` query
    parameter.
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["employee"]
    search_fields = [
        "topic",
        "description",
        "employee__user__first_name",
        "employee__user__last_name",
    ]
    ordering_fields = ["session_date", "created_at"]
    ordering = ["-session_date"]

    def get_queryset(self):
        user = self.request.user
        base = PeerSession.objects.select_related("employee__user")
        if user.is_staff or user.is_superuser:
            return base
        try:
            return base.filter(employee=user.profile)
        except Exception:
            return PeerSession.objects.none()

    def get_serializer_class(self):
        if self.action == "list":
            return PeerSessionListSerializer
        if self.action == "retrieve":
            return PeerSessionDetailSerializer
        return PeerSessionCreateUpdateSerializer

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
            response.data = PeerSessionDetailSerializer(self.created_instance).data
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            response.data = PeerSessionDetailSerializer(self.get_object()).data
        return response

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="year",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter by year of session_date (e.g., 2024)",
                required=False,
            ),
        ],
        responses={200: PeerSessionListSerializer(many=True)},
        description="List peer sessions with optional year filter",
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        year_filter = request.query_params.get("year")
        if year_filter:
            try:
                year = int(year_filter)
                queryset = queryset.filter(session_date__year=year)
            except (ValueError, TypeError):
                pass
        queryset = self.filter_queryset(queryset)
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema(tags=["Time Tracking"])
class TimeTaskViewSet(viewsets.ModelViewSet):
    serializer_class = TimeTaskSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["project", "is_active", "jira_project_key", "jira_issue_key"]
    search_fields = ["name", "jira_issue_key", "jira_project_key", "project__name"]
    ordering_fields = ["name", "project__name", "created_at"]
    ordering = ["project__name", "name"]

    def get_queryset(self):
        queryset = TimeTask.objects.select_related("project")
        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if not has_time_tracking_permission(self.request.user, "view_dept_timesheets"):
            profile = profile_for_user(self.request.user)
            if profile is None:
                return TimeTask.objects.none()
            queryset = queryset.filter(
                Q(project__assignments__user_profile=profile)
                | Q(project__owner=profile)
            )
        return queryset.distinct()

    def perform_create(self, serializer):
        if not has_time_tracking_permission(
            self.request.user, "approve_team_timesheets"
        ):
            raise PermissionDenied("You do not have permission to manage time tasks.")
        serializer.save()

    def perform_update(self, serializer):
        if not has_time_tracking_permission(
            self.request.user, "approve_team_timesheets"
        ):
            raise PermissionDenied("You do not have permission to manage time tasks.")
        serializer.save()

    def perform_destroy(self, instance):
        if not has_time_tracking_permission(
            self.request.user, "approve_team_timesheets"
        ):
            raise PermissionDenied("You do not have permission to manage time tasks.")
        instance.delete()


@extend_schema(tags=["Time Tracking"])
class TimeEntryViewSet(viewsets.ModelViewSet):
    serializer_class = TimeEntrySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = [
        "employee",
        "project",
        "task",
        "status",
        "source_type",
        "work_date",
    ]
    ordering_fields = ["work_date", "start_time", "hours", "created_at", "updated_at"]
    ordering = ["-work_date", "start_time", "employee_id"]

    def get_queryset(self):
        base = TimeEntry.objects.select_related(
            "employee__user",
            "project",
            "task",
            "submitted_by__user",
            "approved_by__user",
            "rejected_by__user",
        ).prefetch_related("audit_events__actor__user")
        user = self.request.user
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            queryset = base
        else:
            profile = profile_for_user(user)
            if profile is None:
                return TimeEntry.objects.none()
            scopes = Q()
            if has_time_tracking_permission(user, "view_own_timesheet"):
                scopes |= Q(employee=profile)
            if has_time_tracking_permission(
                user, "view_team_timesheets"
            ) or has_time_tracking_permission(user, "approve_team_timesheets"):
                scopes |= Q(employee__managers=profile)
            if has_time_tracking_permission(user, "view_dept_timesheets"):
                scopes |= Q()
                queryset = base
            else:
                queryset = base.filter(scopes) if scopes else base.none()

        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_from:
            queryset = queryset.filter(work_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(work_date__lte=date_to)
        return queryset.distinct()

    def perform_create(self, serializer):
        entry = serializer.save()
        profile = profile_for_user(self.request.user)
        if not can_edit_time_entry(self.request.user, entry):
            entry.delete()
            raise PermissionDenied("You do not have permission to create this entry.")
        if (
            entry.source_type != TimeEntrySourceType.MANUAL
            and not has_time_tracking_permission(
                self.request.user, "approve_team_timesheets"
            )
        ):
            entry.delete()
            raise PermissionDenied("Only approvers can create imported entries.")
        event = (
            TimeEntryAuditEventType.IMPORTED
            if entry.source_type != TimeEntrySourceType.MANUAL
            else TimeEntryAuditEventType.CREATED
        )
        duplicate = find_duplicate(entry)
        metadata = {"duplicate_of": duplicate.id} if duplicate else {}
        log_time_entry_event(entry, event, profile, metadata=metadata)

    def perform_update(self, serializer):
        entry = self.get_object()
        if not can_edit_time_entry(self.request.user, entry):
            raise PermissionDenied("You do not have permission to edit this entry.")
        updated = serializer.save()
        profile = profile_for_user(self.request.user)
        event = (
            TimeEntryAuditEventType.CORRECTED
            if updated.source_type != TimeEntrySourceType.MANUAL
            else TimeEntryAuditEventType.UPDATED
        )
        duplicate = find_duplicate(updated)
        metadata = {"duplicate_of": duplicate.id} if duplicate else {}
        log_time_entry_event(updated, event, profile, metadata=metadata)

    def perform_destroy(self, instance):
        if not can_delete_time_entry(self.request.user, instance):
            raise PermissionDenied("You do not have permission to delete this entry.")
        if (
            instance.source_type == TimeEntrySourceType.MANUAL
            and instance.status not in {TimeEntryStatus.DRAFT, TimeEntryStatus.REJECTED}
        ):
            raise PermissionDenied("Only draft or rejected entries can be deleted.")
        instance.delete()

    @extend_schema(
        request=TimeEntrySubmitWeekSerializer,
        responses={200: TimeEntrySerializer(many=True), 400: None, 403: None},
    )
    @action(detail=False, methods=["post"], url_path="submit-week")
    def submit_week(self, request):
        serializer = TimeEntrySubmitWeekSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee = serializer.validated_data.get("employee") or profile_for_user(
            request.user
        )
        if employee is None:
            return Response(
                {"detail": "Employee is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        entries = submit_entries_for_week(
            user=request.user,
            employee=employee,
            week_start=serializer.validated_data["week_start"],
        )
        return Response(TimeEntrySerializer(entries, many=True).data)

    @extend_schema(responses={200: TimeEntrySerializer, 400: None, 403: None})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        entry = approve_entry(user=request.user, entry=self.get_object())
        return Response(TimeEntrySerializer(entry).data)

    @extend_schema(
        request=TimeEntryRejectSerializer,
        responses={200: TimeEntrySerializer, 400: None, 403: None},
    )
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = TimeEntryRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = reject_entry(
            user=request.user,
            entry=self.get_object(),
            reason=serializer.validated_data["reason"],
        )
        return Response(TimeEntrySerializer(entry).data)


@extend_schema(tags=["Time Tracking"])
class TimeTrackingWeeklySummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "week_start",
                str,
                description="Week start date, ISO format YYYY-MM-DD.",
                required=True,
            ),
            OpenApiParameter(
                "employee",
                int,
                description="UserProfile id. Alias for employee_id.",
                required=False,
            ),
            OpenApiParameter(
                "employee_id",
                int,
                description="UserProfile id. Defaults to current user's profile.",
                required=False,
            ),
        ],
        responses={200: None, 400: None, 403: None, 404: None},
    )
    def get(self, request):
        raw_week_start = request.query_params.get("week_start")
        week_start = parse_date(raw_week_start or "")
        if week_start is None:
            return Response(
                {"detail": "week_start must be an ISO date (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employee_id = request.query_params.get(
            "employee_id"
        ) or request.query_params.get("employee")
        if employee_id:
            try:
                employee_pk = int(employee_id)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "employee_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            employee = (
                UserProfile.objects.select_related("user")
                .filter(pk=employee_pk)
                .first()
            )
            if employee is None:
                return Response(
                    {"detail": "Employee not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            employee = profile_for_user(request.user)
            if employee is None:
                return Response(
                    {"detail": "User profile not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        if not can_view_employee_timesheet(request.user, employee):
            return Response(
                {"detail": "You do not have permission to view this timesheet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            weekly_allocation_summary(employee=employee, week_start=week_start)
        )


@extend_schema(tags=["Time Tracking"])
class TimeTrackingActiveAllocationsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "work_date",
                str,
                description="Date for active allocation lookup, ISO YYYY-MM-DD. Defaults to today.",
                required=False,
            ),
            OpenApiParameter(
                "employee",
                int,
                description="UserProfile id. Alias for employee_id.",
                required=False,
            ),
            OpenApiParameter("employee_id", int, required=False),
        ],
        responses={200: None, 400: None, 403: None, 404: None},
    )
    def get(self, request):
        raw_date = request.query_params.get("work_date")
        work_date = parse_date(raw_date) if raw_date else timezone.localdate()
        if work_date is None:
            return Response(
                {"work_date": "work_date must be an ISO date (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        employee_id = request.query_params.get(
            "employee_id"
        ) or request.query_params.get("employee")
        if employee_id:
            employee = (
                UserProfile.objects.select_related("user")
                .filter(pk=employee_id)
                .first()
            )
            if employee is None:
                return Response(
                    {"detail": "Employee not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            employee = profile_for_user(request.user)
            if employee is None:
                return Response(
                    {"detail": "User profile not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        if not can_view_employee_timesheet(request.user, employee):
            return Response(
                {"detail": "You do not have permission to view this allocation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(
            active_time_tracking_allocations(employee=employee, work_date=work_date)
        )


def _week_start_from_request(request):
    raw_week_start = request.query_params.get("week_start")
    if raw_week_start:
        week_start = parse_date(raw_week_start)
        if week_start is None:
            raise serializers.ValidationError(
                {"week_start": "week_start must be an ISO date (YYYY-MM-DD)."}
            )
        return week_start
    today = timezone.localdate()
    return today - timedelta(days=today.weekday())


def _scoped_time_entries(user):
    base = TimeEntry.objects.select_related(
        "employee__user",
        "project",
        "task",
        "submitted_by__user",
        "approved_by__user",
        "rejected_by__user",
    )
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return base
    profile = profile_for_user(user)
    if profile is None:
        return TimeEntry.objects.none()
    scopes = Q()
    if has_time_tracking_permission(user, "view_own_timesheet"):
        scopes |= Q(employee=profile)
    if has_time_tracking_permission(
        user, "view_team_timesheets"
    ) or has_time_tracking_permission(user, "approve_team_timesheets"):
        scopes |= Q(employee__managers=profile)
    if has_time_tracking_permission(user, "view_dept_timesheets"):
        return base
    return base.filter(scopes).distinct() if scopes else base.none()


def _scoped_time_entry_employees(user):
    base = UserProfile.objects.select_related("user")
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return base
    profile = profile_for_user(user)
    if profile is None:
        return UserProfile.objects.none()
    scopes = Q()
    if has_time_tracking_permission(user, "view_own_timesheet"):
        scopes |= Q(id=profile.id)
    if has_time_tracking_permission(
        user, "view_team_timesheets"
    ) or has_time_tracking_permission(user, "approve_team_timesheets"):
        scopes |= Q(managers=profile)
    if has_time_tracking_permission(user, "view_dept_timesheets"):
        return base
    return base.filter(scopes).distinct() if scopes else base.none()


def _apply_time_entry_filters(queryset, params):
    employee_id = params.get("employee") or params.get("employee_id")
    project_id = params.get("project") or params.get("project_id")
    source_type = params.get("source_type")
    entry_status = params.get("status")
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    if employee_id:
        queryset = queryset.filter(employee_id=employee_id)
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if entry_status:
        queryset = queryset.filter(status=entry_status)
    if date_from:
        queryset = queryset.filter(work_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(work_date__lte=date_to)
    return queryset


def _decimal(value):
    return str((value or Decimal("0.00")).quantize(Decimal("0.01")))


@extend_schema(tags=["Time Tracking"])
class TimeTrackingWeeklyDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: None, 400: None, 403: None})
    def get(self, request):
        week_start = _week_start_from_request(request)
        week_end = week_start + timedelta(days=6)
        queryset = _scoped_time_entries(request.user).filter(
            work_date__gte=week_start,
            work_date__lte=week_end,
        )
        employee_id = request.query_params.get(
            "employee_id"
        ) or request.query_params.get("employee")
        if employee_id:
            queryset = queryset.filter(employee_id=employee_id)

        totals_by_source = {
            row["source_type"]: _decimal(row["total_hours"])
            for row in queryset.values("source_type").annotate(total_hours=Sum("hours"))
        }
        totals_by_status = {
            row["status"]: _decimal(row["total_hours"])
            for row in queryset.values("status").annotate(total_hours=Sum("hours"))
        }
        total_hours = queryset.aggregate(total=Sum("hours"))["total"]
        employees = []
        for employee in (
            queryset.values(
                "employee_id", "employee__full_name", "employee__user__username"
            )
            .annotate(total_hours=Sum("hours"))
            .order_by("employee__full_name")
        ):
            employees.append(
                {
                    "employee_id": employee["employee_id"],
                    "employee_name": employee["employee__full_name"]
                    or employee["employee__user__username"],
                    "total_hours": _decimal(employee["total_hours"]),
                }
            )
        return Response(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "total_hours": _decimal(total_hours),
                "totals_by_source": totals_by_source,
                "totals_by_status": totals_by_status,
                "employees": employees,
                "entries": TimeEntrySerializer(
                    queryset.order_by("work_date"), many=True
                ).data,
            }
        )


@extend_schema(tags=["Time Tracking"])
class TimeTrackingApprovalQueueView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: TimeEntrySerializer(many=True), 400: None, 403: None}
    )
    def get(self, request):
        if not (
            getattr(request.user, "is_staff", False)
            or getattr(request.user, "is_superuser", False)
            or has_time_tracking_permission(request.user, "approve_team_timesheets")
        ):
            return Response(
                {"detail": "You do not have permission to view approval queue."},
                status=status.HTTP_403_FORBIDDEN,
            )
        queryset = _scoped_time_entries(request.user)
        if not request.query_params.get("status"):
            queryset = queryset.filter(status=TimeEntryStatus.SUBMITTED)
        week_start_raw = request.query_params.get("week_start")
        if week_start_raw:
            week_start = parse_date(week_start_raw)
            if week_start is None:
                return Response(
                    {"week_start": "week_start must be an ISO date (YYYY-MM-DD)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(
                work_date__gte=week_start,
                work_date__lte=week_start + timedelta(days=6),
            )
        queryset = _apply_time_entry_filters(queryset, request.query_params)
        return Response(
            TimeEntrySerializer(
                queryset.order_by("employee__full_name", "work_date"), many=True
            ).data
        )


@extend_schema(tags=["Time Tracking"])
class TimeTrackingPlannedVsActualView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: None, 400: None, 403: None})
    def get(self, request):
        raw_date_from = request.query_params.get("date_from")
        raw_date_to = request.query_params.get("date_to")
        if raw_date_from and raw_date_to:
            date_from = parse_date(raw_date_from)
            date_to = parse_date(raw_date_to)
        else:
            week_start = _week_start_from_request(request)
            date_from = week_start
            date_to = week_start + timedelta(days=6)
        if date_from is None or date_to is None or date_to < date_from:
            return Response(
                {"detail": "Provide valid date_from/date_to ISO dates."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = _scoped_time_entries(request.user)
        employee_id = request.query_params.get("employee") or request.query_params.get(
            "employee_id"
        )
        project_id = request.query_params.get("project") or request.query_params.get(
            "project_id"
        )
        entry_employee_entries = queryset.filter(
            work_date__gte=date_from,
            work_date__lte=date_to,
        )
        if project_id:
            entry_employee_entries = entry_employee_entries.filter(
                project_id=project_id
            )
        entry_employee_ids = entry_employee_entries.values_list(
            "employee_id", flat=True
        ).distinct()
        assignment_employee_ids = (
            ProjectAssignment.objects.filter(
                user_profile__in=_scoped_time_entry_employees(request.user)
            )
            .filter(start_date__lte=date_to)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=date_from))
        )
        if project_id:
            assignment_employee_ids = assignment_employee_ids.filter(
                project_id=project_id
            )
        assignment_employee_ids = assignment_employee_ids.values_list(
            "user_profile_id", flat=True
        ).distinct()
        employees = (
            _scoped_time_entry_employees(request.user)
            .filter(Q(id__in=entry_employee_ids) | Q(id__in=assignment_employee_ids))
            .select_related("user")
            .order_by("full_name", "user__username", "id")
        )
        if employee_id:
            employees = employees.filter(id=employee_id)
        rows = []
        current = date_from - timedelta(days=date_from.weekday())
        final_week = date_to - timedelta(days=date_to.weekday())
        while current <= final_week:
            for employee in employees:
                summary = weekly_allocation_summary(
                    employee=employee, week_start=current
                )
                for project in summary["projects"]:
                    if project_id and str(project["project_id"]) != str(project_id):
                        continue
                    rows.append(
                        {
                            "employee_id": employee.id,
                            "employee_name": employee.full_name
                            or employee.user.username,
                            "week_start": summary["week_start"],
                            "week_end": summary["week_end"],
                            **project,
                        }
                    )
            current += timedelta(days=7)
        return Response(
            {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "rows": rows,
            }
        )


def _export_row(entry: TimeEntry, include_source_identifiers: bool):
    metadata = entry.source_metadata or {}
    jira_issue_key = entry.task.jira_issue_key if entry.task_id else ""
    jira_issue_key = jira_issue_key or metadata.get("jira_issue_key", "")
    row = {
        "employee": entry.employee.full_name or entry.employee.user.username,
        "employee_id": entry.employee_id,
        "date": entry.work_date.isoformat(),
        "start_time": entry.start_time.isoformat() if entry.start_time else "",
        "end_time": entry.end_time.isoformat() if entry.end_time else "",
        "project": entry.project.name,
        "project_id": entry.project_id,
        "task": entry.task.name if entry.task_id else "",
        "task_id": entry.task_id or "",
        "jira_issue_key": jira_issue_key,
        "hours": str(entry.hours),
        "notes": entry.notes,
        "source_type": entry.source_type,
        "status": entry.status,
        "submitted_at": entry.submitted_at.isoformat() if entry.submitted_at else "",
        "submitted_by": (
            entry.submitted_by.full_name or entry.submitted_by.user.username
            if entry.submitted_by_id
            else ""
        ),
        "approved_at": entry.approved_at.isoformat() if entry.approved_at else "",
        "approved_by": (
            entry.approved_by.full_name or entry.approved_by.user.username
            if entry.approved_by_id
            else ""
        ),
        "rejected_at": entry.rejected_at.isoformat() if entry.rejected_at else "",
        "rejected_by": (
            entry.rejected_by.full_name or entry.rejected_by.user.username
            if entry.rejected_by_id
            else ""
        ),
        "rejection_reason": entry.rejection_reason,
    }
    if include_source_identifiers:
        row["source_external_id"] = entry.source_external_id
        row["source_metadata"] = entry.source_metadata
    return row


class CSVExportRenderer(BaseRenderer):
    media_type = "text/csv"
    format = "csv"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class XLSXExportRenderer(BaseRenderer):
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    format = "xlsx"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


@extend_schema(tags=["Time Tracking"])
class TimeTrackingTimesheetExportView(APIView):
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer, CSVExportRenderer, XLSXExportRenderer]

    @extend_schema(responses={200: OpenApiTypes.BINARY, 400: None, 403: None})
    def get(self, request):
        if not has_time_tracking_permission(request.user, "export_timesheets"):
            return Response(
                {"detail": "You do not have permission to export timesheets."},
                status=status.HTTP_403_FORBIDDEN,
            )
        export_format = (request.query_params.get("format") or "csv").lower()
        if export_format not in {"csv", "xlsx"}:
            return Response(
                {"format": "format must be csv or xlsx."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        queryset = _apply_time_entry_filters(
            _scoped_time_entries(request.user), request.query_params
        ).order_by("work_date", "employee__full_name", "project__name")
        rows = [_export_row(entry, True) for entry in queryset]
        headers = [
            "employee",
            "employee_id",
            "date",
            "start_time",
            "end_time",
            "project",
            "project_id",
            "task",
            "task_id",
            "jira_issue_key",
            "hours",
            "notes",
            "source_type",
            "status",
            "submitted_at",
            "submitted_by",
            "approved_at",
            "approved_by",
            "rejected_at",
            "rejected_by",
            "rejection_reason",
            "source_external_id",
            "source_metadata",
        ]

        if export_format == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="timesheets.csv"'
            writer = csv.DictWriter(response, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            return response

        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Timesheets"
        sheet.append(headers)
        for row in rows:
            sheet.append([str(row.get(header, "")) for header in headers])
        output = io.BytesIO()
        workbook.save(output)
        response = HttpResponse(
            output.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument." "spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = 'attachment; filename="timesheets.xlsx"'
        return response


@extend_schema(tags=["Time Tracking"])
class JiraSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: JiraConnectionSerializer, 403: None})
    def get(self, request):
        require_jira_admin(request.user)
        return Response(JiraConnectionSerializer(JiraConnection.get_solo()).data)

    @extend_schema(
        request=JiraConnectionSerializer,
        responses={200: JiraConnectionSerializer, 400: None, 403: None},
    )
    def patch(self, request):
        require_jira_admin(request.user)
        connection = JiraConnection.get_solo()
        serializer = JiraConnectionSerializer(
            connection, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        connection = serializer.save()
        return Response(JiraConnectionSerializer(connection).data)


@extend_schema(tags=["Time Tracking"])
class JiraTestConnectionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: JiraConnectionSerializer, 400: None, 403: None})
    def post(self, request):
        require_jira_admin(request.user)
        connection = JiraConnection.get_solo()
        result = test_jira_connection(connection)
        connection.last_test_status = result["status"]
        connection.last_test_message = result["message"]
        connection.last_test_metadata = result["metadata"]
        connection.last_test_at = timezone.now()
        connection.save(
            update_fields=[
                "last_test_status",
                "last_test_message",
                "last_test_metadata",
                "last_test_at",
                "updated_at",
            ]
        )
        response_status = (
            status.HTTP_200_OK
            if result["status"] == "success"
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(
            JiraConnectionSerializer(connection).data, status=response_status
        )


@extend_schema(tags=["Time Tracking"])
class JiraMappingsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: None, 403: None})
    def get(self, request):
        require_jira_admin(request.user)
        return Response(_jira_mappings_payload())

    @extend_schema(
        request=JiraMappingMutationSerializer,
        responses={201: None, 400: None, 403: None},
    )
    def post(self, request):
        require_jira_admin(request.user)
        serializer = JiraMappingMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mapping = _save_jira_mapping(serializer.validated_data)
        return Response(
            _serialize_jira_mapping(mapping), status=status.HTTP_201_CREATED
        )

    @extend_schema(
        request=JiraMappingMutationSerializer,
        responses={200: None, 400: None, 403: None, 404: None},
    )
    def patch(self, request):
        require_jira_admin(request.user)
        serializer = JiraMappingMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if "id" not in serializer.validated_data:
            return Response(
                {"id": "Mapping id is required for patch."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mapping = _save_jira_mapping(serializer.validated_data)
        return Response(_serialize_jira_mapping(mapping))


@extend_schema(tags=["Time Tracking"])
class JiraProjectDiscoveryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=JiraProjectDiscoverySerializer,
        responses={200: None, 400: None, 403: None},
    )
    def post(self, request):
        require_jira_admin(request.user)
        serializer = JiraProjectDiscoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        api_token = data.get("api_token")
        if api_token and data.get("base_url") and data.get("auth_email"):
            connection = JiraConnection(
                base_url=data["base_url"],
                auth_email=data["auth_email"],
                enabled=True,
            )
        else:
            saved_connection = JiraConnection.get_solo()
            connection = JiraConnection(
                base_url=data.get("base_url") or saved_connection.base_url,
                auth_email=data.get("auth_email") or saved_connection.auth_email,
                enabled=True,
            )
            connection.api_token_encrypted = saved_connection.api_token_encrypted
        if api_token:
            connection.set_api_token(api_token)

        return Response(
            discover_jira_project_ids(
                connection,
                date_from=data["date_from"],
                date_to=data["date_to"],
                limit=data["limit"],
            )
        )


def _jira_mappings_payload():
    return {
        "users": JiraUserMappingSerializer(
            JiraUserMapping.objects.select_related("employee__user"), many=True
        ).data,
        "projects": JiraProjectMappingSerializer(
            JiraProjectMapping.objects.select_related("project"), many=True
        ).data,
        "issues": JiraIssueMappingSerializer(
            JiraIssueMapping.objects.select_related("task__project"), many=True
        ).data,
    }


def _serialize_jira_mapping(mapping):
    if isinstance(mapping, JiraUserMapping):
        return {"mapping_type": "user", **JiraUserMappingSerializer(mapping).data}
    if isinstance(mapping, JiraProjectMapping):
        return {"mapping_type": "project", **JiraProjectMappingSerializer(mapping).data}
    return {"mapping_type": "issue", **JiraIssueMappingSerializer(mapping).data}


def _save_jira_mapping(data):
    mapping_type = data["mapping_type"]
    pk = data.get("id")
    if mapping_type == "user":
        instance = JiraUserMapping.objects.filter(pk=pk).first() if pk else None
        payload = {
            "jira_account_id": data.get("jira_account_id"),
            "jira_display_name": data.get("jira_display_name", ""),
            "employee_id": data.get("employee_id"),
            "is_active": data.get("is_active", True),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        serializer = JiraUserMappingSerializer(
            instance, data=payload, partial=bool(instance)
        )
    elif mapping_type == "project":
        instance = JiraProjectMapping.objects.filter(pk=pk).first() if pk else None
        payload = {
            "jira_project_key": data.get("jira_project_key"),
            "jira_project_name": data.get("jira_project_name", ""),
            "project_id": data.get("project_id"),
            "is_active": data.get("is_active", True),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        serializer = JiraProjectMappingSerializer(
            instance, data=payload, partial=bool(instance)
        )
    else:
        instance = JiraIssueMapping.objects.filter(pk=pk).first() if pk else None
        payload = {
            "jira_issue_key": data.get("jira_issue_key"),
            "jira_issue_id": data.get("jira_issue_id", ""),
            "task_id": data.get("task_id"),
            "is_active": data.get("is_active", True),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        serializer = JiraIssueMappingSerializer(
            instance, data=payload, partial=bool(instance)
        )
    if pk and instance is None:
        raise serializers.ValidationError({"id": "Mapping not found."})
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def _jira_filters_from_data(data):
    return JiraImportFilters(
        date_from=data["date_from"],
        date_to=data["date_to"],
        employee_id=data.get("employee_id"),
        project_id=data.get("project_id"),
        jira_project_key=data.get("jira_project_key", ""),
        jira_issue_key=data.get("jira_issue_key", ""),
        worklog_id=data.get("worklog_id", ""),
    )


@extend_schema(tags=["Time Tracking"])
class JiraImportPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=JiraImportPreviewSerializer,
        responses={200: None, 400: None, 403: None},
    )
    def post(self, request):
        require_jira_admin(request.user)
        serializer = JiraImportPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        filters_obj = _jira_filters_from_data(serializer.validated_data)
        worklogs = serializer.validated_data.get("worklogs")
        if worklogs is None:
            worklogs = fetch_jira_worklogs(JiraConnection.get_solo(), filters_obj)
        return Response(
            preview_jira_worklogs(filters=filters_obj, raw_worklogs=worklogs)
        )


@extend_schema(tags=["Time Tracking"])
class JiraAssignedIssueImportView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=JiraAssignedIssueImportSerializer,
        responses={200: None, 400: None, 403: None},
    )
    def post(self, request):
        require_jira_admin(request.user)
        serializer = JiraAssignedIssueImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(
            import_assigned_jira_issues(
                user=request.user,
                connection=JiraConnection.get_solo(),
                options=JiraAssignedIssueImportOptions(
                    employee_id=data["employee_id"],
                    max_results=data["max_results"],
                    dry_run=data["dry_run"],
                ),
            )
        )


@extend_schema(tags=["Time Tracking"])
class JiraImportCommitView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=JiraImportCommitSerializer,
        responses={200: None, 400: None, 403: None},
    )
    def post(self, request):
        require_jira_admin(request.user)
        serializer = JiraImportCommitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        filters_obj = _jira_filters_from_data(serializer.validated_data)
        worklogs = serializer.validated_data.get("worklogs")
        if worklogs is None:
            worklogs = fetch_jira_worklogs(JiraConnection.get_solo(), filters_obj)
        return Response(
            commit_jira_worklogs(
                user=request.user,
                filters=filters_obj,
                raw_worklogs=worklogs,
            )
        )


@extend_schema(tags=["Time Tracking"])
class TempoSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: TempoConnectionSerializer, 403: None})
    def get(self, request):
        require_tempo_admin(request.user)
        return Response(TempoConnectionSerializer(TempoConnection.get_solo()).data)

    @extend_schema(
        request=TempoConnectionSerializer,
        responses={200: TempoConnectionSerializer, 400: None, 403: None},
    )
    def patch(self, request):
        require_tempo_admin(request.user)
        connection = TempoConnection.get_solo()
        serializer = TempoConnectionSerializer(
            connection, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        connection = serializer.save()
        return Response(TempoConnectionSerializer(connection).data)


@extend_schema(tags=["Time Tracking"])
class TempoTestConnectionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=TempoConnectionSerializer,
        responses={200: TempoConnectionSerializer, 400: None, 403: None},
    )
    def post(self, request):
        require_tempo_admin(request.user)
        connection = TempoConnection.get_solo()
        if request.data:
            serializer = TempoConnectionSerializer(
                connection, data=request.data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            connection = serializer.save()
        result = test_tempo_connection(connection)
        connection.last_test_status = result["status"]
        connection.last_test_message = result["message"]
        connection.last_test_metadata = result["metadata"]
        connection.last_test_at = timezone.now()
        connection.save(
            update_fields=[
                "last_test_status",
                "last_test_message",
                "last_test_metadata",
                "last_test_at",
                "updated_at",
            ]
        )
        response_status = (
            status.HTTP_200_OK
            if result["status"] == "success"
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(
            TempoConnectionSerializer(connection).data, status=response_status
        )


@extend_schema(tags=["Time Tracking"])
class TempoMappingsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: None, 403: None})
    def get(self, request):
        require_tempo_admin(request.user)
        return Response(_tempo_mappings_payload())

    @extend_schema(
        request=TempoMappingMutationSerializer,
        responses={201: None, 400: None, 403: None},
    )
    def post(self, request):
        require_tempo_admin(request.user)
        serializer = TempoMappingMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mapping = _save_tempo_mapping(serializer.validated_data)
        return Response(
            _serialize_tempo_mapping(mapping), status=status.HTTP_201_CREATED
        )

    @extend_schema(
        request=TempoMappingMutationSerializer,
        responses={200: None, 400: None, 403: None, 404: None},
    )
    def patch(self, request):
        require_tempo_admin(request.user)
        serializer = TempoMappingMutationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if "id" not in serializer.validated_data:
            return Response(
                {"id": "Mapping id is required for patch."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        mapping = _save_tempo_mapping(serializer.validated_data)
        return Response(_serialize_tempo_mapping(mapping))


@extend_schema(tags=["Time Tracking"])
class TempoProjectDiscoveryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=TempoProjectDiscoverySerializer,
        responses={200: None, 400: None, 403: None},
    )
    def post(self, request):
        require_tempo_admin(request.user)
        serializer = TempoProjectDiscoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        api_token = data.get("api_token")
        if api_token and data.get("base_url"):
            connection = TempoConnection(base_url=data["base_url"], enabled=True)
        else:
            saved_connection = TempoConnection.get_solo()
            connection = TempoConnection(
                base_url=data.get("base_url") or saved_connection.base_url,
                enabled=True,
            )
            connection.api_token_encrypted = saved_connection.api_token_encrypted
        if api_token:
            connection.set_api_token(api_token)

        return Response(
            discover_tempo_project_ids(
                connection,
                date_from=data["date_from"],
                date_to=data["date_to"],
                limit=data["limit"],
            )
        )


def _tempo_mappings_payload():
    return {
        "users": TempoUserMappingSerializer(
            TempoUserMapping.objects.select_related("employee__user"), many=True
        ).data,
        "accounts": TempoAccountMappingSerializer(
            TempoAccountMapping.objects.select_related("project"), many=True
        ).data,
        "projects": TempoProjectMappingSerializer(
            TempoProjectMapping.objects.select_related("project"), many=True
        ).data,
        "teams": TempoTeamMappingSerializer(
            TempoTeamMapping.objects.select_related("project"), many=True
        ).data,
    }


def _serialize_tempo_mapping(mapping):
    if isinstance(mapping, TempoUserMapping):
        return {"mapping_type": "user", **TempoUserMappingSerializer(mapping).data}
    if isinstance(mapping, TempoAccountMapping):
        return {
            "mapping_type": "account",
            **TempoAccountMappingSerializer(mapping).data,
        }
    if isinstance(mapping, TempoProjectMapping):
        return {
            "mapping_type": "project",
            **TempoProjectMappingSerializer(mapping).data,
        }
    return {"mapping_type": "team", **TempoTeamMappingSerializer(mapping).data}


def _save_tempo_mapping(data):
    mapping_type = data["mapping_type"]
    pk = data.get("id")
    if mapping_type == "user":
        instance = TempoUserMapping.objects.filter(pk=pk).first() if pk else None
        payload = {
            "tempo_user_id": data.get("tempo_user_id"),
            "tempo_display_name": data.get("tempo_display_name", ""),
            "employee_id": data.get("employee_id"),
            "is_active": data.get("is_active", True),
        }
        serializer_class = TempoUserMappingSerializer
    elif mapping_type == "account":
        instance = TempoAccountMapping.objects.filter(pk=pk).first() if pk else None
        payload = {
            "tempo_account_id": data.get("tempo_account_id"),
            "tempo_account_key": data.get("tempo_account_key", ""),
            "tempo_account_name": data.get("tempo_account_name", ""),
            "project_id": data.get("project_id"),
            "is_active": data.get("is_active", True),
        }
        serializer_class = TempoAccountMappingSerializer
    elif mapping_type == "project":
        instance = TempoProjectMapping.objects.filter(pk=pk).first() if pk else None
        payload = {
            "tempo_project_id": data.get("tempo_project_id"),
            "tempo_project_key": data.get("tempo_project_key", ""),
            "tempo_project_name": data.get("tempo_project_name", ""),
            "project_id": data.get("project_id"),
            "is_active": data.get("is_active", True),
        }
        serializer_class = TempoProjectMappingSerializer
    else:
        instance = TempoTeamMapping.objects.filter(pk=pk).first() if pk else None
        payload = {
            "tempo_team_id": data.get("tempo_team_id"),
            "tempo_team_name": data.get("tempo_team_name", ""),
            "project_id": data.get("project_id"),
            "is_active": data.get("is_active", True),
        }
        serializer_class = TempoTeamMappingSerializer
    if pk and instance is None:
        raise serializers.ValidationError({"id": "Mapping not found."})
    payload = {key: value for key, value in payload.items() if value is not None}
    serializer = serializer_class(instance, data=payload, partial=bool(instance))
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def _tempo_filters_from_data(data):
    return TempoImportFilters(
        date_from=data["date_from"],
        date_to=data["date_to"],
        employee_id=data.get("employee_id"),
        tempo_team_id=data.get("tempo_team_id", ""),
        tempo_account_id=data.get("tempo_account_id", ""),
        tempo_account_key=data.get("tempo_account_key", ""),
        tempo_project_id=data.get("tempo_project_id", ""),
        project_id=data.get("project_id"),
        jira_issue_key=data.get("jira_issue_key", ""),
        worklog_id=data.get("worklog_id", ""),
    )


@extend_schema(tags=["Time Tracking"])
class TempoImportPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=TempoImportPreviewSerializer,
        responses={200: None, 400: None, 403: None},
    )
    def post(self, request):
        require_tempo_admin(request.user)
        serializer = TempoImportPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        filters_obj = _tempo_filters_from_data(serializer.validated_data)
        worklogs = serializer.validated_data.get("worklogs")
        if worklogs is None:
            worklogs = fetch_tempo_worklogs(TempoConnection.get_solo(), filters_obj)
        return Response(
            preview_tempo_worklogs(filters=filters_obj, raw_worklogs=worklogs)
        )


@extend_schema(tags=["Time Tracking"])
class TempoImportCommitView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=TempoImportCommitSerializer,
        responses={200: None, 400: None, 403: None},
    )
    def post(self, request):
        require_tempo_admin(request.user)
        serializer = TempoImportCommitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        filters_obj = _tempo_filters_from_data(serializer.validated_data)
        worklogs = serializer.validated_data.get("worklogs")
        if worklogs is None:
            worklogs = fetch_tempo_worklogs(TempoConnection.get_solo(), filters_obj)
        return Response(
            commit_tempo_worklogs(
                user=request.user,
                filters=filters_obj,
                raw_worklogs=worklogs,
            )
        )


@extend_schema(tags=["Time Tracking"])
class TimeDocumentImportUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(
        request=TimeDocumentImportUploadSerializer,
        responses={201: TimeImportBatchSerializer, 400: None, 403: None},
    )
    def post(self, request):
        require_document_import_admin(request.user)
        serializer = TimeDocumentImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = upload_document_import(
            user=request.user,
            uploaded_file=serializer.validated_data["file"],
        )
        return Response(
            TimeImportBatchSerializer(batch).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Time Tracking"])
class TimeDocumentImportColumnMapView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_batch(self, batch_id):
        return TimeImportBatch.objects.prefetch_related("rows").get(pk=batch_id)

    @extend_schema(
        request=TimeDocumentImportColumnMapSerializer,
        responses={200: TimeImportBatchSerializer, 400: None, 403: None, 404: None},
    )
    def post(self, request, batch_id):
        require_document_import_admin(request.user)
        try:
            batch = self._get_batch(batch_id)
        except TimeImportBatch.DoesNotExist:
            return Response(
                {"detail": "Import batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = TimeDocumentImportColumnMapSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = map_columns(
            user=request.user,
            batch=batch,
            mapping=serializer.validated_data["column_mapping"],
        )
        return Response(TimeImportBatchSerializer(batch).data)


@extend_schema(tags=["Time Tracking"])
class TimeImportBatchPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: TimeImportBatchSerializer, 403: None, 404: None})
    def get(self, request, batch_id):
        require_document_import_admin(request.user)
        try:
            batch = TimeImportBatch.objects.prefetch_related("rows").get(pk=batch_id)
        except TimeImportBatch.DoesNotExist:
            return Response(
                {"detail": "Import batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        validate_batch_rows(batch)
        return Response(TimeImportBatchSerializer(batch).data)


@extend_schema(tags=["Time Tracking"])
class TimeImportBatchListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: TimeImportBatchSerializer(many=True), 403: None})
    def get(self, request):
        require_document_import_admin(request.user)
        queryset = TimeImportBatch.objects.select_related(
            "uploaded_by__user"
        ).prefetch_related("rows")
        source_type = request.query_params.get("source_type")
        batch_status = request.query_params.get("status")
        uploaded_by = request.query_params.get("uploaded_by")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if source_type:
            queryset = queryset.filter(source_type=source_type)
        if batch_status:
            queryset = queryset.filter(status=batch_status)
        if uploaded_by:
            queryset = queryset.filter(uploaded_by_id=uploaded_by)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        return Response(TimeImportBatchSerializer(queryset, many=True).data)


@extend_schema(tags=["Time Tracking"])
class TimeImportBatchDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: TimeImportBatchSerializer, 403: None, 404: None})
    def get(self, request, batch_id):
        require_document_import_admin(request.user)
        try:
            batch = (
                TimeImportBatch.objects.select_related("uploaded_by__user")
                .prefetch_related("rows")
                .get(pk=batch_id)
            )
        except TimeImportBatch.DoesNotExist:
            return Response(
                {"detail": "Import batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(TimeImportBatchSerializer(batch).data)


@extend_schema(tags=["Time Tracking"])
class TimeTrackingSourceChangeReviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: TimeEntrySerializer(many=True), 403: None})
    def get(self, request):
        if not has_time_tracking_permission(request.user, "approve_team_timesheets"):
            return Response(
                {"detail": "You do not have permission to review source changes."},
                status=status.HTTP_403_FORBIDDEN,
            )
        queryset = _scoped_time_entries(request.user).filter(
            source_metadata__source_change_flag__in=[
                TimeEntrySourceChangeFlag.CHANGED,
                TimeEntrySourceChangeFlag.DELETED,
                TimeEntrySourceChangeFlag.REVIEW_REQUIRED,
            ]
        )
        queryset = _apply_time_entry_filters(queryset, request.query_params)
        return Response(TimeEntrySerializer(queryset, many=True).data)


@extend_schema(tags=["Time Tracking"])
class TimeTrackingSourceChangeResolveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=TimeSourceChangeResolveSerializer,
        responses={200: TimeEntrySerializer, 400: None, 403: None, 404: None},
    )
    def post(self, request, entry_id):
        if not has_time_tracking_permission(request.user, "approve_team_timesheets"):
            return Response(
                {"detail": "You do not have permission to resolve source changes."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            entry = _scoped_time_entries(request.user).get(pk=entry_id)
        except TimeEntry.DoesNotExist:
            return Response(
                {"detail": "Time entry not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = TimeSourceChangeResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        metadata = dict(entry.source_metadata or {})
        if action == "accept_current":
            metadata["source_change_flag"] = TimeEntrySourceChangeFlag.NONE
            entry.source_metadata = metadata
            entry.save(update_fields=["source_metadata", "updated_at"])
            message = "Accepted current BloomHub time entry value."
        elif action == "apply_source":
            if entry.status == TimeEntryStatus.APPROVED:
                return Response(
                    {"detail": "Approved entries cannot be changed by source review."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            pending = metadata.get("source_pending_update") or {}
            if "work_date" in pending:
                parsed_date = parse_date(pending["work_date"])
                if parsed_date is None:
                    return Response(
                        {"detail": "Pending source update contains invalid work_date."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                entry.work_date = parsed_date
            if "hours" in pending:
                entry.hours = Decimal(str(pending["hours"]))
            if "notes" in pending:
                entry.notes = pending["notes"]
            metadata["source_change_flag"] = TimeEntrySourceChangeFlag.NONE
            metadata.pop("source_pending_update", None)
            entry.source_metadata = metadata
            entry.duplicate_fingerprint = fingerprint_for_entry(entry)
            entry.full_clean()
            entry.save()
            message = "Applied source update to unapproved time entry."
        else:
            message = "Left source change flagged for later review."
        log_time_entry_event(
            entry,
            TimeEntryAuditEventType.SOURCE_CHANGED,
            profile_for_user(request.user),
            message,
            {
                "action": action,
                "note": serializer.validated_data.get("note", ""),
            },
        )
        return Response(TimeEntrySerializer(entry).data)


@extend_schema(tags=["Time Tracking"])
class TimeImportBatchCommitView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: TimeImportBatchSerializer, 403: None, 404: None})
    def post(self, request, batch_id):
        require_document_import_admin(request.user)
        try:
            batch = TimeImportBatch.objects.prefetch_related("rows").get(pk=batch_id)
        except TimeImportBatch.DoesNotExist:
            return Response(
                {"detail": "Import batch not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        batch = commit_document_import(user=request.user, batch=batch)
        return Response(TimeImportBatchSerializer(batch).data)


@extend_schema(tags=["Training & Development"])
class TrainingBudgetViewSet(viewsets.ModelViewSet):
    """CRUD for per-employee annual training budgets.

    HR (``Training.configure_budget``) can create/update/delete any budget.
    Employees can view their own (``track_own_budget``); managers can view
    direct reports (``track_team_budget``); HR roles can view all.
    Includes ``?year=`` filter and a ``me`` action for the current employee's
    current-year budget.
    """

    serializer_class = TrainingBudgetSerializer
    permission_classes = [IsAuthenticated, IsTrainingBudgetEditor]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["employee", "fiscal_year"]
    ordering_fields = ["fiscal_year", "allocated_budget", "used_budget"]
    ordering = ["-fiscal_year"]

    def get_queryset(self):
        user = self.request.user
        base = TrainingBudget.objects.select_related("employee__user")

        if user.is_staff or user.is_superuser:
            return base

        from .permissions import _get_user_profile, _has_permission

        profile = _get_user_profile(user)
        if profile is None:
            return TrainingBudget.objects.none()

        if _has_permission(user, "Training", ["configure_budget", "track_dept_budget"]):
            return base

        qs = base.filter(employee=profile)
        if _has_permission(user, "Training", ["track_team_budget"]):
            qs = base.filter(employee__managers=profile) | qs
        return qs.distinct()

    def perform_destroy(self, instance):
        instance.delete()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="year",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Filter by fiscal year",
                required=False,
            ),
        ],
        responses={200: TrainingBudgetSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        year = request.query_params.get("year")
        if year:
            try:
                queryset = queryset.filter(fiscal_year=int(year))
            except (ValueError, TypeError):
                pass
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(self.get_serializer(queryset, many=True).data)

    @extend_schema(
        responses={200: TrainingBudgetSerializer},
        description="Return the current user's training budget for the current year.",
    )
    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        from django.utils import timezone

        from .permissions import _get_user_profile
        from .services.training_budget_service import recalculate_budget

        profile = _get_user_profile(request.user)
        if profile is None:
            return Response(
                {"detail": "User profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        year = int(request.query_params.get("year") or timezone.now().year)
        budget = recalculate_budget(profile, year)
        if budget is None:
            budget = TrainingBudget.objects.filter(
                employee=profile, fiscal_year=year
            ).first()
        if budget is None:
            return Response(
                {
                    "employee_id": profile.id,
                    "employee_name": profile.user.get_full_name(),
                    "fiscal_year": year,
                    "allocated_budget": "0.00",
                    "used_budget": "0.00",
                    "remaining_budget": "0.00",
                    "budget_percentage_used": 0,
                    "threshold_reached": False,
                    "threshold_notified_at": None,
                },
                status=status.HTTP_200_OK,
            )
        return Response(TrainingBudgetSerializer(budget).data)


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

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            response.data = CertificateDetailSerializer(
                self.get_object(), context={"request": request}
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
# Internal Mobility — Job Board
# ──────────────────────────────────────────────────────────────────────────────


def _is_listing_open_for_applications(listing: JobListing) -> bool:
    """A listing accepts applications only while OPEN and within its window."""
    now = timezone.now()
    return (
        listing.status == JobListingStatus.OPEN
        and listing.open_at <= now <= listing.close_at
    )


@extend_schema(tags=["Internal Mobility"])
class JobListingViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Internal job board.

    Employees see only *active* listings (status ``open`` within the
    ``open_at`` / ``close_at`` window) and can apply via the ``apply`` action.

    HR / admin users have full access: they can list every listing regardless
    of status, create new listings, update existing ones, and review the
    applicant roster via the ``applications`` action.
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["department", "status"]
    search_fields = ["title", "description"]
    ordering_fields = ["open_at", "close_at", "created_at"]
    ordering = ["-open_at"]

    def get_queryset(self):
        base = JobListing.objects.select_related(
            "department", "created_by__user"
        ).annotate(application_count=models.Count("applications"))
        user = self.request.user
        if user.is_authenticated and is_hr_or_admin(user):
            return base
        now = timezone.now()
        return base.filter(
            status=JobListingStatus.OPEN,
            open_at__lte=now,
            close_at__gte=now,
        )

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return JobListingWriteSerializer
        if self.action == "retrieve":
            return JobListingDetailSerializer
        return JobListingListSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "applications"):
            return [IsAuthenticated(), IsHrOrAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(created_by=self._get_profile())

    def _get_profile(self) -> UserProfile | None:
        user = self.request.user
        return getattr(user, "profile", None) if user.is_authenticated else None

    @extend_schema(
        summary="Apply to an internal job listing",
        request=ApplicationCreateSerializer,
        responses={201: ApplicationSerializer, 400: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["post"], url_path="apply")
    def apply(self, request, pk=None):
        listing = self.get_object()
        if not _is_listing_open_for_applications(listing):
            return Response(
                {"detail": "This listing is not open for applications."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = self._get_profile()
        if profile is None:
            return Response(
                {"detail": "Authenticated employee profile required to apply."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if Application.objects.filter(listing=listing, applicant=profile).exists():
            return Response(
                {"detail": "You have already applied to this listing."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ApplicationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            application = Application.objects.create(
                listing=listing,
                applicant=profile,
                cover_note=serializer.validated_data.get("cover_note", "").strip(),
            )
        except IntegrityError:
            # Lost the race with another concurrent apply request.
            return Response(
                {"detail": "You have already applied to this listing."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            ApplicationSerializer(application).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="List the current user's applications",
        responses={200: ApplicationSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="my-applications")
    def my_applications(self, request):
        profile = self._get_profile()
        if profile is None:
            return Response([], status=status.HTTP_200_OK)
        qs = (
            Application.objects.filter(applicant=profile)
            .select_related("listing", "applicant__user")
            .order_by("-applied_at")
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ApplicationSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(ApplicationSerializer(qs, many=True).data)

    @extend_schema(
        summary="List applications for a listing (HR/admin only)",
        responses={200: ApplicationSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="applications")
    def applications(self, request, pk=None):
        listing = self.get_object()
        qs = (
            Application.objects.filter(listing=listing)
            .select_related("applicant__user", "listing")
            .order_by("-applied_at")
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ApplicationSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(ApplicationSerializer(qs, many=True).data)


@extend_schema(tags=["Internal Mobility"])
class JobApplicationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Endpoints over the ``Application`` model.

    * ``list`` / ``retrieve`` — HR/admin see every application; an applicant
      sees only their own. ``?listing=<id>`` and ``?status=<value>`` filter.
    * ``update`` / ``partial_update`` — reviewer-driven status transitions
      (HR/admin, the listing creator, or a manager of an employee in the
      hiring department). State machine in
      ``core.services.job_application_service`` enforces legal moves.
    * ``withdraw`` action — applicant-only self-withdrawal.
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
    ]
    filterset_fields = ["listing", "status"]
    ordering_fields = ["applied_at", "created_at", "updated_at"]
    ordering = ["-applied_at"]

    def get_queryset(self):
        base = Application.objects.select_related(
            "listing", "applicant__user", "decided_by__user"
        )
        user = self.request.user
        if user.is_authenticated and is_hr_or_admin(user):
            return base
        profile = getattr(user, "profile", None) if user.is_authenticated else None
        if profile is None:
            return base.none()
        # Reviewers (listing creator + managers of the hiring department)
        # need to see and act on applications outside their own pipeline.
        managed_dept_ids = list(
            profile.direct_reports.values_list("department_fk", flat=True)
            .exclude(department_fk__isnull=True)
            .distinct()
        )
        return base.filter(
            models.Q(applicant=profile)
            | models.Q(listing__created_by=profile)
            | models.Q(listing__department_id__in=managed_dept_ids)
        )

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return ApplicationStatusUpdateSerializer
        if self.action == "withdraw":
            return ApplicationWithdrawSerializer
        return ApplicationSerializer

    def get_permissions(self):
        if self.action in ("update", "partial_update"):
            return [IsAuthenticated(), CanReviewApplication()]
        return [IsAuthenticated()]

    def _get_actor_profile(self) -> UserProfile | None:
        user = self.request.user
        return getattr(user, "profile", None) if user.is_authenticated else None

    def update(self, request, *args, **kwargs):
        from .services.job_application_service import transition_application

        instance = self.get_object()
        write = self.get_serializer(
            instance,
            data=request.data,
            partial=kwargs.get("partial", False),
        )
        write.is_valid(raise_exception=True)
        new_status = write.validated_data.get("status", instance.status)
        note = write.validated_data.get("decision_note", "")
        transition_application(
            instance,
            new_status=new_status,
            actor=self._get_actor_profile(),
            note=note,
        )
        instance.refresh_from_db()
        return Response(ApplicationSerializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @extend_schema(
        summary="Applicant withdraws their own application",
        request=ApplicationWithdrawSerializer,
        responses={200: ApplicationSerializer},
    )
    @action(detail=True, methods=["post"], url_path="withdraw")
    def withdraw(self, request, pk=None):
        from .services.job_application_service import withdraw_application

        instance = self.get_object()
        profile = self._get_actor_profile()
        if profile is None or instance.applicant_id != profile.id:
            raise PermissionDenied(
                "Only the applicant may withdraw their own application."
            )
        write = ApplicationWithdrawSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        withdraw_application(
            instance,
            actor=profile,
            note=write.validated_data.get("decision_note", ""),
        )
        instance.refresh_from_db()
        return Response(ApplicationSerializer(instance).data)


@extend_schema(tags=["Internal Mobility"])
class PromotionHistoryViewSet(viewsets.ModelViewSet):
    """
    CRUD for employee promotion history.

    Employees see only their own promotion records. HR/admin see every
    record (filterable via ``?employee=<id>``) and are the only users who
    may create, update, or delete entries.
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["employee"]
    search_fields = [
        "notes",
        "employee__user__first_name",
        "employee__user__last_name",
    ]
    ordering_fields = ["date", "created_at"]
    ordering = ["-date"]

    def get_queryset(self):
        base = PromotionHistory.objects.select_related(
            "employee__user",
            "previous_role",
            "new_role",
            "related_listing",
        )
        user = self.request.user
        if user.is_authenticated and is_hr_or_admin(user):
            return base
        profile = getattr(user, "profile", None) if user.is_authenticated else None
        if profile is None:
            return base.none()
        return base.filter(employee=profile)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return PromotionHistoryWriteSerializer
        return PromotionHistorySerializer

    def _require_hr(self):
        if not is_hr_or_admin(self.request.user):
            raise PermissionDenied(
                "Only HR or admin users may modify promotion history."
            )

    def create(self, request, *args, **kwargs):
        self._require_hr()
        write = self.get_serializer(data=request.data)
        write.is_valid(raise_exception=True)
        instance = write.save()
        return Response(
            PromotionHistorySerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        self._require_hr()
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        write = self.get_serializer(instance, data=request.data, partial=partial)
        write.is_valid(raise_exception=True)
        instance = write.save()
        return Response(PromotionHistorySerializer(instance).data)

    def perform_destroy(self, instance):
        self._require_hr()
        instance.delete()


@extend_schema(tags=["Internal Mobility"])
class CPFLevelChangeViewSet(viewsets.ModelViewSet):
    """
    CRUD for employee CPF (Career Progression Framework) level changes.

    Employees see only their own CPF history. HR/admin see every record
    (filterable via ``?employee=<id>``) and are the only users who may
    create, update, or delete entries.

    The ``progression`` action returns a consolidated career-progression
    timeline that combines recorded level changes with performance-review
    outcomes — suitable for rendering a timeline visualization.
    """

    permission_classes = [IsCPFLevelChangeEditor]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = CPF_LEVEL_CHANGE_FILTERSET_FIELDS
    search_fields = CPF_LEVEL_CHANGE_SEARCH_FIELDS
    ordering_fields = CPF_LEVEL_CHANGE_ORDERING_FIELDS
    ordering = ["-effective_date"]

    def get_queryset(self):
        base = CPFLevelChange.objects.select_related(
            "employee__user",
            "performance_review",
            "promotion",
            "recorded_by__user",
        )
        user = self.request.user
        if can_manage_cpf_level_changes(user):
            return base
        profile = self._get_profile()
        if profile is None:
            return base.none()
        return base.filter(employee=profile)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return CPFLevelChangeWriteSerializer
        if self.action == "progression":
            return CPFProgressionSerializer
        return CPFLevelChangeSerializer

    def _get_profile(self) -> UserProfile | None:
        user = self.request.user
        return getattr(user, "profile", None) if user.is_authenticated else None

    def create(self, request, *args, **kwargs):
        write = self.get_serializer(data=request.data)
        write.is_valid(raise_exception=True)
        instance = write.save(recorded_by=self._get_profile())
        sync_employee_current_cpf_level(instance.employee, actor=request.user)
        return Response(
            CPFLevelChangeSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        write = self.get_serializer(instance, data=request.data, partial=partial)
        write.is_valid(raise_exception=True)
        instance = write.save()
        sync_employee_current_cpf_level(instance.employee, actor=request.user)
        return Response(CPFLevelChangeSerializer(instance).data)

    def perform_destroy(self, instance):
        employee = instance.employee
        instance.delete()
        sync_employee_current_cpf_level(employee, actor=self.request.user)

    def _resolve_progression_target(self, request) -> UserProfile:
        """Resolve whose CPF timeline to return.

        HR/admin may target any employee via ``?employee=``; everyone else
        always receives their own timeline.
        """
        employee_param = request.query_params.get("employee")
        if can_manage_cpf_level_changes(request.user) and employee_param:
            target = (
                UserProfile.objects.select_related("user")
                .filter(pk=employee_param)
                .first()
            )
            if target is None:
                raise NotFound("Employee not found.")
            return target
        target = self._get_profile()
        if target is None:
            raise ValidationError("Authenticated employee profile required.")
        return target

    @extend_schema(
        summary="CPF career-progression timeline for an employee",
        parameters=[
            OpenApiParameter(
                "employee",
                OpenApiTypes.INT,
                OpenApiParameter.QUERY,
                description=(
                    "Employee profile id. HR/admin only; non-HR users always "
                    "receive their own timeline."
                ),
            )
        ],
        responses={200: CPFProgressionSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="progression",
        serializer_class=CPFProgressionSerializer,
    )
    def progression(self, request):
        target = self._resolve_progression_target(request)
        payload = build_cpf_progression(target)
        return Response(self.get_serializer(payload).data)


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
# Announcements
# ──────────────────────────────────────────


class IsAnnouncementAllowed(permissions.BasePermission):
    """Employees can read; publishing is limited to announcement publisher roles."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        can_access_announcements = (
            can_view_announcements(user)
            or can_manage_announcements(user)
            or can_schedule_announcements(user)
        )
        if request.method in permissions.SAFE_METHODS:
            return can_access_announcements
        if getattr(view, "action", None) in (
            "comments",
            "delete_comment",
            "reactions",
        ):
            return can_access_announcements
        return can_manage_announcements(user)


@extend_schema(tags=["Announcements"])
class AnnouncementViewSet(viewsets.ModelViewSet):
    """
    CRUD API for rich-text announcements.

    Scheduled announcements are hidden from regular readers until
    ``scheduled_at`` is due. Announcement managers can see future rows.
    """

    permission_classes = [IsAuthenticated, IsAnnouncementAllowed]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["type"]
    search_fields = ["title", "body"]
    ordering_fields = ["published_at", "scheduled_at", "created_at", "updated_at"]
    ordering = ["-published_at", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Announcement.objects.none()

        base = Announcement.objects.select_related("author__user")
        user = self.request.user
        if can_manage_announcements(user) or can_schedule_announcements(user):
            return base
        if can_view_announcements(user):
            return base.filter(
                Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now())
            )
        return Announcement.objects.none()

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return AnnouncementWriteSerializer
        if self.action == "retrieve":
            return AnnouncementDetailSerializer
        return AnnouncementListSerializer

    def perform_create(self, serializer):
        self._enforce_schedule_permission(serializer.validated_data.get("scheduled_at"))
        send_email_notifications = serializer.validated_data.get(
            "send_email_notifications", False
        )
        profile = getattr(self.request.user, "profile", None)
        if profile is None:
            raise PermissionDenied("Authenticated employee profile required.")
        announcement = serializer.save(author=profile)
        if announcement_is_published(announcement):
            notify_announcement_published(
                announcement, send_email=send_email_notifications
            )

    def perform_update(self, serializer):
        was_published = announcement_is_published(serializer.instance)
        send_email_notifications = serializer.validated_data.get(
            "send_email_notifications", False
        )
        if "scheduled_at" in serializer.validated_data:
            self._enforce_schedule_permission(
                serializer.validated_data.get("scheduled_at")
            )
        announcement = serializer.save()
        if not was_published and announcement_is_published(announcement):
            notify_announcement_published(
                announcement, send_email=send_email_notifications
            )

    def _enforce_schedule_permission(self, scheduled_at):
        if (
            scheduled_at
            and scheduled_at > timezone.now()
            and not can_schedule_announcements(self.request.user)
        ):
            raise PermissionDenied(
                "Scheduling announcements requires schedule_announcements permission."
            )

    @action(detail=True, methods=["get", "post"], url_path="comments")
    def comments(self, request, pk=None):
        announcement = self.get_object()
        if request.method == "GET":
            comments = announcement.comments.select_related("author__user").filter(
                deleted_at__isnull=True
            )
            serializer = AnnouncementCommentSerializer(
                comments,
                many=True,
                context=self.get_serializer_context(),
            )
            return Response(serializer.data)

        profile = getattr(request.user, "profile", None)
        if profile is None:
            raise PermissionDenied("Authenticated employee profile required.")
        serializer = AnnouncementCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.save(announcement=announcement, author=profile)
        return Response(
            AnnouncementCommentSerializer(
                comment,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"comments/(?P<comment_id>[^/.]+)",
    )
    def delete_comment(self, request, pk=None, comment_id=None):
        announcement = self.get_object()
        try:
            comment = announcement.comments.select_related("author").get(
                pk=comment_id,
                deleted_at__isnull=True,
            )
        except AnnouncementComment.DoesNotExist as exc:
            raise NotFound("Comment not found.") from exc

        profile = getattr(request.user, "profile", None)
        can_delete = profile is not None and (
            comment.author_id == profile.id
            or announcement.author_id == profile.id
            or can_moderate_announcement_comments(request.user)
        )
        if not can_delete:
            raise PermissionDenied("You do not have permission to delete this comment.")

        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="reactions")
    def reactions(self, request, pk=None):
        announcement = self.get_object()
        if request.method == "GET":
            reactions = announcement.reactions.select_related("user__user")
            serializer = AnnouncementReactionSerializer(
                reactions,
                many=True,
                context=self.get_serializer_context(),
            )
            return Response(serializer.data)

        if not can_add_announcement_reactions(request.user):
            raise PermissionDenied(
                "Adding reactions requires add_reactions permission."
            )

        profile = getattr(request.user, "profile", None)
        if profile is None:
            raise PermissionDenied("Authenticated employee profile required.")
        serializer = AnnouncementReactionToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reaction_type = serializer.validated_data["reaction_type"]

        reaction = AnnouncementReaction.objects.filter(
            announcement=announcement,
            user=profile,
            reaction_type=reaction_type,
        ).first()
        if reaction is not None:
            reaction.delete()
            return Response(
                {"reaction_type": reaction_type, "active": False},
                status=status.HTTP_200_OK,
            )

        reaction = AnnouncementReaction.objects.create(
            announcement=announcement,
            user=profile,
            reaction_type=reaction_type,
        )
        data = AnnouncementReactionSerializer(
            reaction,
            context=self.get_serializer_context(),
        ).data
        data["active"] = True
        return Response(data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Announcements"])
class DiscordAnnouncementChannelViewSet(viewsets.ModelViewSet):
    queryset = DiscordAnnouncementChannel.objects.all()
    serializer_class = DiscordAnnouncementChannelSerializer
    permission_classes = [IsAuthenticated, permissions.IsAdminUser]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["announcement_type", "enabled"]
    search_fields = ["channel_name"]
    ordering_fields = ["announcement_type", "channel_name", "created_at", "updated_at"]
    ordering = ["announcement_type", "channel_name"]


@extend_schema(tags=["Celebrations"])
class UpcomingCelebrationsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List upcoming birthdays and work anniversaries",
        parameters=[
            OpenApiParameter(
                name="days",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Inclusive lookahead window in days. Defaults to 30; max 365.",
            ),
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=["all", "birthday", "anniversary"],
                description="Filter event type. Defaults to all.",
            ),
        ],
        responses={200: UpcomingCelebrationSerializer(many=True)},
    )
    def get(self, request):
        query = CelebrationQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        requested_type = query.validated_data["type"]
        allowed_types = set()
        if can_view_birthdays(request.user):
            allowed_types.add("birthday")
        if can_view_anniversaries(request.user):
            allowed_types.add("anniversary")

        if not allowed_types:
            raise PermissionDenied("Viewing celebrations requires permission.")
        if requested_type != "all" and requested_type not in allowed_types:
            raise PermissionDenied("Viewing this celebration type requires permission.")

        event_types = (
            allowed_types if requested_type == "all" else {cast(Any, requested_type)}
        )
        events = build_upcoming_profile_celebrations(
            days=query.validated_data["days"],
            event_types=event_types,
        )
        return Response(UpcomingCelebrationSerializer(events, many=True).data)


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


# ──────────────────────────────────────────
# Leave Analytics
# ──────────────────────────────────────────


@extend_schema_view(
    list=extend_schema(
        tags=["Leave Analytics"],
        summary="List leave monthly aggregate buckets",
    ),
    retrieve=extend_schema(
        tags=["Leave Analytics"],
        summary="Retrieve one aggregate bucket",
    ),
)
class LeaveAnalyticsViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Read-only access to pre-materialized leave analytics.

    Source rows are owned by `LeaveMonthlyAggregate` (per
    employee/leave_type/year/month). Composite reports — monthly trend, yearly
    totals, department breakdown, per-employee summary — are exposed as
    `@action` endpoints that reduce the fact table on the database side.

    Refresh is gated behind `CanRefreshLeaveAnalytics` and writes via the
    `core.services.leave_analytics_service` module rather than mutating the
    table from this view directly.
    """

    queryset = LeaveMonthlyAggregate.objects.select_related("employee__user").all()
    serializer_class = LeaveMonthlyAggregateSerializer
    permission_classes = [IsLeaveAnalyticsViewer]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = LEAVE_MONTHLY_AGGREGATE_FILTERSET_FIELDS
    ordering_fields = LEAVE_MONTHLY_AGGREGATE_ORDERING_FIELDS
    ordering = ["-year", "-month", "employee_id", "leave_type"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if has_leave_analytics_view_permission(user):
            return qs
        profile = _get_user_profile(user)
        if profile is None:
            return qs.none()
        return qs.filter(employee=profile)

    def _scoped_employee_qs(self):
        """Employees the caller is allowed to see in analytics responses."""
        user = self.request.user
        if has_leave_analytics_view_permission(user):
            return UserProfile.objects.select_related("user").all()
        profile = _get_user_profile(user)
        if profile is None:
            return UserProfile.objects.none()
        return UserProfile.objects.select_related("user").filter(id=profile.id)

    @staticmethod
    def _parse_year(raw, *, default=None):
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"year": "year must be an integer"}) from exc

    @staticmethod
    def _parse_month(raw):
        if raw is None or raw == "":
            return None
        try:
            month = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"month": "month must be an integer"}) from exc
        if not (1 <= month <= 12):
            raise ValidationError({"month": "month must be between 1 and 12"})
        return month

    @staticmethod
    def _parse_department(raw):
        if raw is None or raw == "":
            return None
        return raw

    @extend_schema(
        tags=["Leave Analytics"],
        summary="Monthly leave trend for a given year",
        parameters=[
            OpenApiParameter(
                name="year",
                required=True,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="leave_type",
                required=False,
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="department",
                required=False,
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="month",
                required=False,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={200: LeaveAnalyticsMonthRowSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="monthly")
    def monthly(self, request):
        from datetime import date as _date

        from .enums import LeaveType

        year = self._parse_year(
            request.query_params.get("year"), default=timezone.now().year
        )
        leave_type = request.query_params.get("leave_type") or None
        department = self._parse_department(request.query_params.get("department"))
        month_filter = self._parse_month(request.query_params.get("month"))

        qs = self.get_queryset().filter(year=year)
        if leave_type:
            qs = qs.filter(leave_type=leave_type)
        if department is not None:
            qs = qs.filter(employee__department=department)
        if month_filter is not None:
            qs = qs.filter(month=month_filter)

        rows = (
            qs.values("month", "leave_type")
            .annotate(total=models.Sum("approved_days"))
            .order_by("month", "leave_type")
        )

        bucketed: dict[int, dict[str, int]] = {
            m: {lt: 0 for lt in LeaveType.values} for m in range(1, 13)
        }
        for row in rows:
            bucketed[row["month"]][row["leave_type"]] = row["total"] or 0

        payload = []
        for month in range(1, 13):
            total = sum(bucketed[month].values())
            payload.append(
                {
                    "year": year,
                    "month": month,
                    "month_label": _date(year, month, 1).strftime("%b"),
                    "total": total,
                    "by_type": bucketed[month],
                }
            )

        serializer = LeaveAnalyticsMonthRowSerializer(payload, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Leave Analytics"],
        summary="Yearly totals broken down by leave type",
        parameters=[
            OpenApiParameter(
                name="year",
                required=True,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="department",
                required=False,
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="month",
                required=False,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={200: LeaveAnalyticsYearTotalsSerializer},
    )
    @action(detail=False, methods=["get"], url_path="yearly-totals")
    def yearly_totals(self, request):
        from core.services.leave_analytics_service import yearly_totals_by_type

        from .enums import LeaveRequestStatus, LeaveType

        year = self._parse_year(
            request.query_params.get("year"), default=timezone.now().year
        )
        department = self._parse_department(request.query_params.get("department"))
        month_filter = self._parse_month(request.query_params.get("month"))

        scope_qs = self.get_queryset().filter(year=year)
        if department is not None:
            scope_qs = scope_qs.filter(employee__department=department)
        if month_filter is not None:
            scope_qs = scope_qs.filter(month=month_filter)

        if has_leave_analytics_view_permission(request.user):
            totals = yearly_totals_by_type(
                year, department=department, month=month_filter
            )
        else:
            totals = {lt: 0 for lt in LeaveType.values}
            rows = scope_qs.values("leave_type").annotate(
                total=models.Sum("approved_days")
            )
            for row in rows:
                totals[row["leave_type"]] = row["total"] or 0

        pending_total = scope_qs.aggregate(p=models.Sum("pending_days")).get("p") or 0

        if has_leave_analytics_view_permission(request.user):
            headcount_qs = UserProfile.objects.all()
            if department is not None:
                headcount_qs = headcount_qs.filter(department=department)
            headcount = headcount_qs.count()
            today = timezone.now().date()
            on_leave_qs = LeaveRequest.objects.filter(
                status=LeaveRequestStatus.APPROVED,
                start_date__lte=today,
                end_date__gte=today,
            )
            if department is not None:
                on_leave_qs = on_leave_qs.filter(employee__department=department)
            on_leave_today = on_leave_qs.values("employee_id").distinct().count()
        else:
            profile = _get_user_profile(request.user)
            headcount = 1 if profile is not None else 0
            today = timezone.now().date()
            on_leave_today = (
                LeaveRequest.objects.filter(
                    employee=profile,
                    status=LeaveRequestStatus.APPROVED,
                    start_date__lte=today,
                    end_date__gte=today,
                ).exists()
                if profile is not None
                else 0
            )
            on_leave_today = int(bool(on_leave_today))

        serializer = LeaveAnalyticsYearTotalsSerializer(
            {
                "year": year,
                "total": sum(totals.values()),
                "by_type": totals,
                "pending_total": pending_total,
                "headcount": headcount,
                "on_leave_today": on_leave_today,
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Leave Analytics"],
        summary="Department-level breakdown for a year",
        parameters=[
            OpenApiParameter(
                name="year",
                required=True,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="month",
                required=False,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={200: LeaveAnalyticsDepartmentRowSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="departments")
    def departments(self, request):
        from .enums import LeaveType

        if not has_leave_analytics_view_permission(request.user):
            raise PermissionDenied(
                "Department-level analytics require Vacations.view_dept_trends"
            )

        year = self._parse_year(
            request.query_params.get("year"), default=timezone.now().year
        )
        month_filter = self._parse_month(request.query_params.get("month"))

        headcount_by_dept: dict[str, int] = defaultdict(int)
        for profile in UserProfile.objects.only("id", "department"):
            headcount_by_dept[profile.department or "Unassigned"] += 1

        rows_qs = LeaveMonthlyAggregate.objects.filter(year=year)
        if month_filter is not None:
            rows_qs = rows_qs.filter(month=month_filter)
        rows = (
            rows_qs.select_related("employee")
            .values("employee__department", "leave_type")
            .annotate(total=models.Sum("approved_days"))
        )

        bucketed: dict[str, dict[str, int]] = defaultdict(
            lambda: {lt: 0 for lt in LeaveType.values}
        )
        for row in rows:
            dept = row["employee__department"] or "Unassigned"
            bucketed[dept][row["leave_type"]] = row["total"] or 0

        payload = []
        for dept, by_type in bucketed.items():
            payload.append(
                {
                    "department": dept,
                    "headcount": headcount_by_dept.get(dept, 0),
                    "total": sum(by_type.values()),
                    "by_type": by_type,
                }
            )
        payload.sort(key=lambda r: r["total"], reverse=True)

        serializer = LeaveAnalyticsDepartmentRowSerializer(payload, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Leave Analytics"],
        summary="Per-employee yearly summary",
        parameters=[
            OpenApiParameter(
                name="year",
                required=True,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="department",
                required=False,
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="month",
                required=False,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={
            200: LeaveAnalyticsEmployeeSummarySerializer(many=True),
        },
    )
    @action(detail=False, methods=["get"], url_path="employees")
    def employees(self, request):
        from .enums import LeaveType

        year = self._parse_year(
            request.query_params.get("year"), default=timezone.now().year
        )
        department = self._parse_department(request.query_params.get("department"))
        month_filter = self._parse_month(request.query_params.get("month"))

        employees_qs = self._scoped_employee_qs()
        if department is not None:
            employees_qs = employees_qs.filter(department=department)
        employee_ids = list(employees_qs.values_list("id", flat=True))

        balances = {
            (b.employee_id, b.leave_type): b
            for b in LeaveBalance.objects.filter(
                year=year, employee_id__in=employee_ids
            )
        }
        policy_allowance = {
            p.leave_type: p.allocated_days_per_year for p in LeavePolicy.objects.all()
        }

        agg_qs = LeaveMonthlyAggregate.objects.filter(
            year=year, employee_id__in=employee_ids
        )
        if month_filter is not None:
            agg_qs = agg_qs.filter(month=month_filter)
        agg_rows = agg_qs.values("employee_id", "leave_type").annotate(
            total=models.Sum("approved_days")
        )

        per_employee: dict[int, dict[str, int]] = defaultdict(
            lambda: {lt: 0 for lt in LeaveType.values}
        )
        for row in agg_rows:
            per_employee[row["employee_id"]][row["leave_type"]] = row["total"] or 0

        payload = []
        for emp in employees_qs:
            by_type = per_employee.get(emp.id, {lt: 0 for lt in LeaveType.values})
            vacation_used = by_type.get(LeaveType.VACATION, 0)
            balance = balances.get((emp.id, LeaveType.VACATION))
            allocation = (
                balance.allocated + balance.carryover
                if balance is not None
                else policy_allowance.get(LeaveType.VACATION, 0)
            )
            payload.append(
                {
                    "employee_id": emp.id,
                    "employee_name": emp.user.get_full_name() or emp.user.username,
                    "role": getattr(emp.role, "name", None),
                    "department": emp.department,
                    "total": sum(by_type.values()),
                    "vacation_used": vacation_used,
                    "vacation_remaining": max(allocation - vacation_used, 0),
                    "by_type": by_type,
                }
            )
        payload.sort(key=lambda r: r["total"], reverse=True)

        serializer = LeaveAnalyticsEmployeeSummarySerializer(payload, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Leave Analytics"],
        summary="Per-employee leave history",
        parameters=[
            OpenApiParameter(
                name="employee",
                required=True,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="year_from",
                required=False,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="year_to",
                required=False,
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
            ),
            OpenApiParameter(
                name="leave_type",
                required=False,
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
            ),
        ],
        responses={200: LeaveAnalyticsEmployeeHistorySerializer},
    )
    @action(detail=False, methods=["get"], url_path="employee-history")
    def employee_history(self, request):
        from core.services.leave_analytics_service import (
            employee_history as build_employee_history,
        )

        employee_id = request.query_params.get("employee")
        if employee_id is None or employee_id == "":
            raise ValidationError({"employee": "employee is required"})
        try:
            employee_id = int(employee_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"employee": "employee must be an integer"}) from exc

        scoped_employees = self._scoped_employee_qs()
        employee = scoped_employees.filter(id=employee_id).first()
        if employee is None:
            raise NotFound("Employee not found or not accessible.")

        year_from = self._parse_year(request.query_params.get("year_from"))
        year_to = self._parse_year(request.query_params.get("year_to"))
        if year_from is not None and year_to is not None and year_from > year_to:
            raise ValidationError("year_from cannot exceed year_to.")

        leave_type = request.query_params.get("leave_type") or None

        payload = build_employee_history(
            employee,
            year_from=year_from,
            year_to=year_to,
            leave_type=leave_type,
        )
        serializer = LeaveAnalyticsEmployeeHistorySerializer(
            {
                "employee_id": employee.id,
                "employee_name": employee.user.get_full_name()
                or employee.user.username,
                **payload,
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Leave Analytics"],
        summary="Rebuild the analytics fact table",
        request=None,
        responses={200: LeaveAnalyticsRefreshResponseSerializer},
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="refresh",
        permission_classes=[CanRefreshLeaveAnalytics],
    )
    def refresh(self, request):
        from core.services.leave_analytics_service import (
            materialize_leave_monthly_aggregates,
            snapshot_leave_balances,
        )

        year_from = self._parse_year(request.data.get("year_from"))
        year_to = self._parse_year(request.data.get("year_to"))
        year_range = None
        if year_from is not None or year_to is not None:
            if year_from is None or year_to is None:
                raise ValidationError(
                    "Pass year_from and year_to together, or neither."
                )
            if year_from > year_to:
                raise ValidationError("year_from cannot exceed year_to.")
            year_range = (year_from, year_to)

        agg_stats = materialize_leave_monthly_aggregates(year_range=year_range)
        snap_stats = snapshot_leave_balances()

        serializer = LeaveAnalyticsRefreshResponseSerializer(
            {
                **agg_stats,
                "snapshots": snap_stats,
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(
        tags=["Leave Analytics"],
        summary="List historical leave balance snapshots",
    ),
)
class LeaveBalanceSnapshotViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Read-only access to `LeaveBalanceSnapshot` for trend reporting."""

    queryset = LeaveBalanceSnapshot.objects.select_related("employee__user").all()
    serializer_class = LeaveBalanceSnapshotSerializer
    permission_classes = [IsLeaveAnalyticsViewer]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = LEAVE_BALANCE_SNAPSHOT_FILTERSET_FIELDS
    ordering_fields = LEAVE_BALANCE_SNAPSHOT_ORDERING_FIELDS
    ordering = ["-snapshot_date", "employee_id", "leave_type"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if has_leave_analytics_view_permission(user):
            return qs
        profile = _get_user_profile(user)
        if profile is None:
            return qs.none()
        return qs.filter(employee=profile)


class BonusRecordViewSet(viewsets.ModelViewSet):
    """Bonus records (Compensation module).

    HR can list/create/update/delete bonuses for any employee.
    Non-HR users may only list bonuses they own (filtered by queryset).
    """

    serializer_class = BonusRecordSerializer
    permission_classes = [IsAuthenticated, IsCompensationAdminOrOwnReadOnly]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["user_profile", "bonus_type"]
    ordering_fields = ["effective_date", "amount", "created_at"]
    ordering = ["-effective_date"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return BonusRecord.objects.none()
        qs = BonusRecord.objects.select_related("user_profile__user", "created_by")
        user = self.request.user
        if not user or not user.is_authenticated:
            return BonusRecord.objects.none()

        employee_id = self.request.query_params.get("employee_id")
        if employee_id:
            qs = qs.filter(user_profile_id=employee_id)
        since = self.request.query_params.get("since")
        if since:
            qs = qs.filter(effective_date__gte=since)
        bonus_type = self.request.query_params.get("bonus_type")
        if bonus_type:
            qs = qs.filter(bonus_type=bonus_type)

        if is_compensation_admin(user):
            return qs

        try:
            profile = user.profile
        except Exception:
            return BonusRecord.objects.none()
        return qs.filter(user_profile=profile)


class EmployeeBonusListView(APIView):
    """GET /api/employees/{id}/bonuses/ — per-employee bonus history."""

    permission_classes = [IsAuthenticated]

    def get(self, request, employee_id: int):
        try:
            profile = UserProfile.objects.get(pk=employee_id)
        except UserProfile.DoesNotExist:
            raise NotFound("Employee not found.")

        if not is_compensation_admin(request.user):
            try:
                own = request.user.profile
            except Exception:
                raise PermissionDenied("Forbidden.")
            if own.id != profile.id:
                raise PermissionDenied("Forbidden.")

        qs = profile.bonus_records.select_related("created_by").all()
        serializer = BonusRecordSerializer(qs, many=True)
        return Response(serializer.data)


class CompensationOverviewView(APIView):
    """GET /api/compensation/overview/ — aggregated compensation dashboard payload.

    HR-only. Returns the full CompensationOverview shape consumed by the frontend.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_compensation_admin(request.user):
            raise PermissionDenied("Compensation overview is HR-only.")
        from .services.compensation_service import build_overview

        return Response(build_overview())


class CompensationPolicyViewSet(viewsets.ModelViewSet):
    """One NET-salary policy per CPF level. HR-only (read + write)."""

    serializer_class = CompensationPolicySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["cpf_level"]
    ordering_fields = ["cpf_level", "net_monthly", "effective_date"]
    ordering = ["cpf_level"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CompensationPolicy.objects.none()
        return CompensationPolicy.objects.select_related("created_by").all()

    def check_permissions(self, request):
        super().check_permissions(request)
        if not is_compensation_admin(request.user):
            raise PermissionDenied("HR-only.")

    def _policy_salary_value(self, value):
        if value in (None, ""):
            return None
        return float(value)

    def _log_policy_salary_change(self, *, policy, old_value, new_value, source):
        for employee in UserProfile.objects.filter(cpf_level=policy.cpf_level):
            log_employee_profile_change(
                employee=employee,
                field=EmployeeProfileChangeHistory.TrackedField.SALARY,
                old_value=self._policy_salary_value(old_value),
                new_value=self._policy_salary_value(new_value),
                changed_by=(
                    self.request.user if self.request.user.is_authenticated else None
                ),
                metadata={
                    "source": source,
                    "policy_id": policy.id,
                    "cpf_level": policy.cpf_level,
                    "salary_type": "net_monthly_policy",
                },
            )

    def perform_create(self, serializer):
        policy = serializer.save()
        self._log_policy_salary_change(
            policy=policy,
            old_value=None,
            new_value=policy.net_monthly,
            source="compensation_policy_created",
        )

    def perform_update(self, serializer):
        old_net = serializer.instance.net_monthly
        policy = serializer.save()
        self._log_policy_salary_change(
            policy=policy,
            old_value=old_net,
            new_value=policy.net_monthly,
            source="compensation_policy_updated",
        )

    def perform_destroy(self, instance):
        old_net = instance.net_monthly
        cpf_level = instance.cpf_level
        policy_id = instance.id
        affected = list(UserProfile.objects.filter(cpf_level=cpf_level))
        instance.delete()
        for employee in affected:
            log_employee_profile_change(
                employee=employee,
                field=EmployeeProfileChangeHistory.TrackedField.SALARY,
                old_value=self._policy_salary_value(old_net),
                new_value=None,
                changed_by=(
                    self.request.user if self.request.user.is_authenticated else None
                ),
                metadata={
                    "source": "compensation_policy_deleted",
                    "policy_id": policy_id,
                    "cpf_level": cpf_level,
                    "salary_type": "net_monthly_policy",
                },
            )


class BenefitCatalogViewSet(viewsets.ModelViewSet):
    """Global benefit catalog. HR-only (read + write)."""

    serializer_class = BenefitCatalogSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["benefit_type", "is_active"]
    ordering_fields = ["benefit_type", "name", "monthly_amount", "effective_date"]
    ordering = ["benefit_type", "name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return BenefitCatalog.objects.none()
        return BenefitCatalog.objects.select_related("created_by").all()

    def check_permissions(self, request):
        super().check_permissions(request)
        if not is_compensation_admin(request.user):
            raise PermissionDenied("HR-only.")


# ──────────────────────────────────────────
# Feedback & Surveys
# ──────────────────────────────────────────


class IsHROrStaffForSurveyWrite(permissions.BasePermission):
    """Read for any authenticated user; write only for HR / staff / superuser."""

    SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.method in self.SAFE_METHODS:
            return True
        if getattr(request.user, "is_staff", False) or getattr(
            request.user, "is_superuser", False
        ):
            return True
        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            return False
        role = getattr(profile, "role", None)
        return bool(role and role.name and "hr" in role.name.lower())


@extend_schema(tags=["Feedback & Surveys"])
class SurveyViewSet(viewsets.ModelViewSet):
    """
    CRUD for feedback surveys.

    - GET  /api/surveys/                — list
    - POST /api/surveys/                — create with nested questions
    - GET  /api/surveys/{id}/           — retrieve with questions
    - PATCH /api/surveys/{id}/          — update (questions replaced if provided)
    - DELETE /api/surveys/{id}/         — delete (only if no responses)
    - POST /api/surveys/{id}/close/     — mark closed (keeps responses, blocks new ones)
    """

    serializer_class = SurveySerializer
    permission_classes = [IsHROrStaffForSurveyWrite]
    queryset = (
        Survey.objects.select_related("created_by__user")
        .prefetch_related("questions")
        .all()
    )

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list":
            # `?mine=true` narrows the list to surveys created by the current
            # user — used by the management table so each HR user sees only
            # their own work (including ones they forbade themselves for
            # testing).
            mine = self.request.query_params.get("mine") == "true"
            try:
                profile = self.request.user.profile
            except (AttributeError, UserProfile.DoesNotExist):
                profile = None
            if mine:
                if profile is None:
                    return qs.none()
                return qs.filter(created_by=profile)
            # For the "available" list, hide surveys the current user is
            # forbidden from — applies to everyone, including HR/superusers,
            # because being forbidden is a per-user policy not a permission.
            if profile is not None:
                qs = qs.exclude(forbidden_users=profile)
        return qs

    def get_permissions(self):
        # `submit_response` is open to any authenticated user — that's the
        # whole point of letting employees take surveys. HR gating still
        # applies to everything else (create/update/delete/analytics/close).
        if getattr(self, "action", None) == "submit_response":
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    def perform_create(self, serializer):
        profile = None
        try:
            profile = self.request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            profile = None
        serializer.save(created_by=profile)

    def _is_locked(self, survey) -> bool:
        return bool(survey.end_date and survey.end_date < timezone.localdate())

    def _locked_response(self, survey):
        return Response(
            {
                "detail": (
                    f"This survey ended on {survey.end_date.isoformat()} "
                    "and can no longer be modified."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def update(self, request, *args, **kwargs):
        survey = self.get_object()
        if self._is_locked(survey):
            return self._locked_response(survey)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        survey = self.get_object()
        if self._is_locked(survey):
            return self._locked_response(survey)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        survey = self.get_object()
        if survey.responses.exists():
            return Response(
                {
                    "detail": (
                        "Cannot delete a survey that has responses. "
                        "Close it instead."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        summary="Close a survey",
        description=(
            "Marks the survey as closed. Existing responses are preserved but "
            "no new responses can be submitted."
        ),
        responses={200: SurveySerializer},
    )
    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        from .enums import SurveyStatus

        survey = self.get_object()
        if survey.status == SurveyStatus.CLOSED:
            return Response(
                {"detail": "Survey is already closed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        survey.status = SurveyStatus.CLOSED
        survey.save(update_fields=["status"])
        return Response(self.get_serializer(survey).data)

    @extend_schema(
        summary="Add a question to a survey",
        description=(
            "Appends a single question to the end of the survey. For bulk "
            "edits, PATCH the survey itself with a full `questions` array."
        ),
        request=QuestionSerializer,
        responses={201: QuestionSerializer},
    )
    @action(detail=True, methods=["post"], url_path="questions")
    def add_question(self, request, pk=None):
        survey = self.get_object()
        serializer = QuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        next_order = (
            (survey.questions.order_by("-order").first().order + 1)
            if survey.questions.exists()
            else 0
        )
        question = Question.objects.create(
            survey=survey,
            text=serializer.validated_data["text"],
            type=serializer.validated_data["type"],
            options=serializer.validated_data.get("options", []),
            order=serializer.validated_data.get("order", next_order),
        )
        return Response(
            QuestionSerializer(question).data, status=status.HTTP_201_CREATED
        )

    def _user_is_hr_or_staff(self, request) -> bool:
        if getattr(request.user, "is_staff", False) or getattr(
            request.user, "is_superuser", False
        ):
            return True
        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            return False
        role = getattr(profile, "role", None)
        return bool(role and role.name and "hr" in role.name.lower())

    @extend_schema(
        summary="Get aggregated survey analytics",
        description=(
            "Returns per-question aggregations for charting plus a daily "
            "response trend. HR / staff / superuser only. "
            "Filters (all optional): "
            "?department=<str>&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD. "
            "Note: department filter has no effect on anonymous surveys "
            "because respondents are not linked."
        ),
        parameters=[
            OpenApiParameter("department", str, OpenApiParameter.QUERY),
            OpenApiParameter("start_date", str, OpenApiParameter.QUERY),
            OpenApiParameter("end_date", str, OpenApiParameter.QUERY),
        ],
        responses={200: dict},
    )
    @action(detail=True, methods=["get"], url_path="analytics")
    def analytics(self, request, pk=None):
        if not self._user_is_hr_or_staff(request):
            return Response(
                {"detail": "Survey analytics are HR-only."},
                status=status.HTTP_403_FORBIDDEN,
            )

        survey = self.get_object()
        department = (request.query_params.get("department") or "").strip()
        start_date = (request.query_params.get("start_date") or "").strip()
        end_date = (request.query_params.get("end_date") or "").strip()

        responses = SurveyResponse.objects.filter(survey=survey)
        if start_date:
            responses = responses.filter(submitted_at__date__gte=start_date)
        if end_date:
            responses = responses.filter(submitted_at__date__lte=end_date)
        # Department filter only meaningful for non-anonymous surveys.
        if department and not survey.is_anonymous:
            responses = responses.filter(respondent__department__iexact=department)

        response_ids = list(responses.values_list("id", flat=True))
        total_responses = len(response_ids)

        # Per-question aggregations
        questions_payload = []
        for question in survey.questions.order_by("order", "id"):
            answers = Answer.objects.filter(
                question=question, response_id__in=response_ids
            )
            response_count = answers.count()

            entry: dict[str, Any] = {
                "question_id": question.id,
                "text": question.text,
                "type": question.type,
                "response_count": response_count,
            }

            if question.type == "choice":
                # Counts per option label.
                counts: dict[str, int] = defaultdict(int)
                for value in answers.values_list("value", flat=True):
                    counts[value] += 1
                # Always include every defined option (zero-fill).
                options = question.options if isinstance(question.options, list) else []
                distribution = [
                    {"value": opt, "count": counts.get(opt, 0)} for opt in options
                ]
                # Also surface any "other" answer values not in the defined options.
                for value, cnt in counts.items():
                    if value not in options:
                        distribution.append({"value": value, "count": cnt})
                entry["distribution"] = distribution

            elif question.type == "scale":
                numeric_values: list[int] = []
                for raw in answers.values_list("value", flat=True):
                    try:
                        numeric_values.append(int(raw))
                    except (TypeError, ValueError):
                        continue
                avg = (
                    sum(numeric_values) / len(numeric_values) if numeric_values else 0.0
                )
                bucket_counts: dict[int, int] = defaultdict(int)
                for v in numeric_values:
                    bucket_counts[v] += 1
                entry["average"] = round(avg, 2)
                entry["distribution"] = [
                    {"value": str(v), "count": bucket_counts[v]}
                    for v in sorted(bucket_counts.keys())
                ]

            else:  # text
                samples = list(
                    answers.exclude(value="").values_list("value", flat=True)[:10]
                )
                entry["samples"] = samples

            questions_payload.append(entry)

        # Daily response trend.
        trend_counts: dict[str, int] = defaultdict(int)
        for submitted_at in responses.values_list("submitted_at", flat=True):
            day = submitted_at.date().isoformat() if submitted_at else "unknown"
            trend_counts[day] += 1
        responses_over_time = [
            {"date": day, "count": count} for day, count in sorted(trend_counts.items())
        ]

        return Response(
            {
                "survey_id": survey.id,
                "survey_title": survey.title,
                "is_anonymous": survey.is_anonymous,
                "total_responses": total_responses,
                "filters_applied": {
                    "department": department or None,
                    "start_date": start_date or None,
                    "end_date": end_date or None,
                },
                "questions": questions_payload,
                "responses_over_time": responses_over_time,
            }
        )

    @extend_schema(
        summary="Submit a response to a survey",
        description=(
            "Posts a full response with one answer per question. "
            "Only `active` surveys accept submissions. "
            "For non-anonymous surveys a given user can only submit once; "
            "anonymous surveys allow unlimited submissions. "
            'Payload shape: `{ "answers": [{ "question_id": int, "value": str }, ...] }`.'
        ),
        request={
            "type": "object",
            "properties": {
                "answers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_id": {"type": "integer"},
                            "value": {"type": "string"},
                        },
                        "required": ["question_id"],
                    },
                }
            },
            "required": ["answers"],
        },
        responses={
            201: {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "survey": {"type": "integer"},
                    "submitted_at": {"type": "string", "format": "date-time"},
                },
            },
            400: dict,
            403: dict,
        },
    )
    @extend_schema(
        summary="List individual responses for a survey (HR-only)",
        description=(
            "Returns every response for the given survey with the respondent's "
            "name (or 'Anonymous' for anonymous surveys) and each question's "
            "answer. HR / staff / superuser only."
        ),
        responses={200: list, 403: dict},
    )
    @action(detail=True, methods=["get"], url_path="individual-responses")
    def individual_responses(self, request, pk=None):
        if not self._user_is_hr_or_staff(request):
            return Response(
                {"detail": "Individual responses are HR-only."},
                status=status.HTTP_403_FORBIDDEN,
            )

        survey = self.get_object()
        questions = list(survey.questions.order_by("order", "id"))

        responses_qs = (
            SurveyResponse.objects.filter(survey=survey)
            .select_related("respondent__user")
            .order_by("-submitted_at")
        )

        # Pre-fetch all answers in one query.
        from collections import defaultdict as _dd

        answers_by_response: dict[int, list[Answer]] = _dd(list)
        for ans in Answer.objects.filter(response__in=responses_qs):
            answers_by_response[ans.response_id].append(ans)

        payload = []
        for r in responses_qs:
            if survey.is_anonymous or r.respondent is None:
                respondent_name = "Anonymous"
                respondent_id = None
            else:
                u = r.respondent.user
                full = f"{u.first_name} {u.last_name}".strip()
                respondent_name = full or u.username
                respondent_id = r.respondent_id

            value_by_qid = {a.question_id: a.value for a in answers_by_response[r.id]}
            payload.append(
                {
                    "response_id": r.id,
                    "respondent_id": respondent_id,
                    "respondent_name": respondent_name,
                    "submitted_at": r.submitted_at.isoformat(),
                    "answers": [
                        {
                            "question_id": q.id,
                            "question_text": q.text,
                            "question_type": q.type,
                            "value": value_by_qid.get(q.id, ""),
                        }
                        for q in questions
                    ],
                }
            )

        return Response(
            {
                "survey_id": survey.id,
                "survey_title": survey.title,
                "is_anonymous": survey.is_anonymous,
                "responses": payload,
            }
        )

    @action(detail=True, methods=["post"], url_path="responses")
    def submit_response(self, request, pk=None):
        from .enums import SurveyStatus

        survey = self.get_object()
        if survey.status != SurveyStatus.ACTIVE:
            return Response(
                {"detail": "This survey is not accepting responses."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if survey.end_date and survey.end_date < timezone.localdate():
            return Response(
                {"detail": (f"This survey ended on {survey.end_date.isoformat()}.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            profile = None

        # Block users explicitly forbidden from this survey.
        if (
            profile is not None
            and survey.forbidden_users.filter(pk=profile.pk).exists()
        ):
            return Response(
                {"detail": "You are not allowed to take this survey."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Override behaviour: for non-anonymous surveys, a second submission by
        # the same user replaces the previous one (cascade-deletes old answers).
        # Anonymous surveys always accept new submissions because there's no
        # respondent linkage to dedupe on.
        if not survey.is_anonymous and profile is not None:
            SurveyResponse.objects.filter(survey=survey, respondent=profile).delete()

        answers_payload = request.data.get("answers")
        if not isinstance(answers_payload, list) or not answers_payload:
            return Response(
                {"detail": "Provide a non-empty `answers` list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate every question_id belongs to this survey.
        survey_questions = list(survey.questions.all())
        valid_question_ids = {q.id for q in survey_questions}
        required_ids = {q.id for q in survey_questions if q.required}
        clean: list[tuple[int, str]] = []
        answered_ids: set[int] = set()
        for item in answers_payload:
            if not isinstance(item, dict):
                return Response(
                    {"detail": "Each answer must be an object."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qid = item.get("question_id")
            if qid not in valid_question_ids:
                return Response(
                    {"detail": (f"Question {qid} does not belong to this survey.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            value = str(item.get("value", ""))
            if qid in required_ids and not value.strip():
                return Response(
                    {
                        "detail": (
                            f"Question {qid} is required and must have a "
                            "non-empty answer."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            answered_ids.add(int(qid))
            clean.append((int(qid), value))

        missing_required = required_ids - answered_ids
        if missing_required:
            return Response(
                {
                    "detail": (
                        f"Missing required answers for question(s): "
                        f"{sorted(missing_required)}"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Persist response + answers in a transaction.
        from django.db import transaction

        with transaction.atomic():
            # The Response.save() override automatically nulls respondent
            # when the survey is anonymous — no extra logic needed here.
            response = SurveyResponse(survey=survey, respondent=profile)
            response.save()
            Answer.objects.bulk_create(
                [
                    Answer(question_id=qid, response=response, value=value)
                    for qid, value in clean
                ]
            )

        return Response(
            {
                "id": response.id,
                "survey": survey.id,
                "submitted_at": response.submitted_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


# ──────────────────────────────────────────
# Pulse Check (BHB-452)
# ──────────────────────────────────────────


@extend_schema(tags=["Feedback & Surveys"])
class PulseCheckViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    One-tap sentiment feedback (1–5).

    - POST /api/pulse-checks/            — submit a pulse (any authenticated user)
    - GET  /api/pulse-checks/            — list (HR / staff only)
    - GET  /api/pulse-checks/summary/    — aggregated avg + daily breakdown
        ?days=7 (default) | ?days=30
    """

    serializer_class = PulseCheckSerializer
    queryset = PulseCheck.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def _is_hr_or_staff(self, request) -> bool:
        if getattr(request.user, "is_staff", False) or getattr(
            request.user, "is_superuser", False
        ):
            return True
        try:
            profile = request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            return False
        role = getattr(profile, "role", None)
        return bool(role and role.name and "hr" in role.name.lower())

    def list(self, request, *args, **kwargs):
        # Raw pulse data is HR-only — exposes per-user sentiment.
        if not self._is_hr_or_staff(request):
            return Response(
                {"detail": "Pulse check data is HR-only."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        try:
            profile = self.request.user.profile
        except (AttributeError, UserProfile.DoesNotExist):
            profile = None
        serializer.save(employee=profile)

    @extend_schema(
        summary="Pulse check summary",
        description=(
            "Returns average sentiment and per-day counts over the last N days "
            "(default 7). HR / staff only."
        ),
        parameters=[
            OpenApiParameter("days", int, OpenApiParameter.QUERY),
        ],
        responses={
            200: {
                "type": "object",
                "properties": {
                    "days": {"type": "integer"},
                    "count": {"type": "integer"},
                    "average": {"type": "number"},
                    "by_day": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string", "format": "date"},
                                "count": {"type": "integer"},
                                "average": {"type": "number"},
                            },
                        },
                    },
                    "distribution": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "integer"},
                                "count": {"type": "integer"},
                            },
                        },
                    },
                },
            }
        },
    )
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        if not self._is_hr_or_staff(request):
            return Response(
                {"detail": "Pulse check summary is HR-only."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            days = int(request.query_params.get("days", "7"))
        except ValueError:
            days = 7
        days = max(1, min(days, 365))

        cutoff = timezone.now() - timedelta(days=days)
        qs = PulseCheck.objects.filter(created_at__gte=cutoff)

        rows = list(qs.values_list("value", "created_at", "category"))
        values = [r[0] for r in rows]
        count = len(values)
        average = round(sum(values) / count, 2) if count else 0.0

        # Per-day aggregation (all categories combined).
        by_day_map: dict[str, list[int]] = defaultdict(list)
        for value, created_at, _ in rows:
            by_day_map[created_at.date().isoformat()].append(value)
        by_day = [
            {
                "date": day,
                "count": len(vals),
                "average": round(sum(vals) / len(vals), 2),
            }
            for day, vals in sorted(by_day_map.items())
        ]

        # Distribution across the 1–5 scale, zero-filled.
        dist_counts: dict[int, int] = {n: 0 for n in range(1, 6)}
        for v in values:
            dist_counts[v] = dist_counts.get(v, 0) + 1
        distribution = [{"value": n, "count": dist_counts[n]} for n in range(1, 6)]

        # Per-category averages (overall, workload, management, culture).
        by_cat_map: dict[str, list[int]] = defaultdict(list)
        for value, _, category in rows:
            by_cat_map[category].append(value)
        by_category = [
            {
                "category": cat,
                "count": len(by_cat_map.get(cat, [])),
                "average": (
                    round(sum(by_cat_map[cat]) / len(by_cat_map[cat]), 2)
                    if by_cat_map.get(cat)
                    else 0.0
                ),
            }
            for cat, _ in PulseCheck.CATEGORY_CHOICES
        ]

        return Response(
            {
                "days": days,
                "count": count,
                "average": average,
                "by_day": by_day,
                "distribution": distribution,
                "by_category": by_category,
            }
        )
