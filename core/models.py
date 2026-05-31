import sys
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from .avatar_utils import generate_initials_avatar_png, get_initials
from .enums import (
    ActionPointStatus,
    ApplicationStatus,
    AssetCondition,
    AssetStatus,
    BenefitType,
    BonusType,
    ChecklistInstanceStatus,
    ChecklistTaskStatus,
    ChecklistType,
    ConferenceCourseRegistrationStatus,
    CPFChangeSource,
    DocumentAccessRole,
    DocumentSignatureStatus,
    DocumentSignerStatus,
    EmployeeDocumentProviderType,
    EmployeeDocumentSourceType,
    EmployeeDocumentType,
    EmploymentStatus,
    ImportBatchSource,
    ImportBatchStatus,
    ImportRowStatus,
    JobListingStatus,
    LeaveRequestStatus,
    LeaveType,
    LeaveWorkflowStatus,
    ProjectAssignmentStatus,
    ProjectStage,
    ProjectStatus,
    ProjectType,
    QuestionType,
    ReminderType,
    ReviewEventType,
    ReviewNoteVisibility,
    ReviewOutcome,
    ReviewStatus,
    ReviewType,
    SuggestionStatus,
    SurveyStatus,
    TaskRole,
    TemplateCategory,
    TemplateFieldType,
    TemplateStatus,
    TemplateVisibility,
    TimeEntryAuditEventType,
    TimeEntrySourceType,
    TimeEntryStatus,
    TrackedField,
)
from .enums import (
    DocumentCategory as _DocumentCategory,
)
from .services.credential_encryption import decrypt_secret, encrypt_secret

DEFAULT_LEAVE_POLICIES = [
    {
        "leave_type": LeaveType.VACATION,
        "allocated_days_per_year": 25,
        "carryover_days": 5,
        "requires_approval": True,
        "requires_covering_employee": False,
        "min_notice_in_days": 7,
        "max_consecutive_days": 20,
    },
    {
        "leave_type": LeaveType.SICK,
        "allocated_days_per_year": 10,
        "carryover_days": 0,
        "requires_approval": False,
        "requires_covering_employee": False,
        "min_notice_in_days": 0,
        "max_consecutive_days": None,
    },
    {
        "leave_type": LeaveType.WFH,
        "allocated_days_per_year": 52,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": False,
        "min_notice_in_days": 1,
        "max_consecutive_days": 5,
    },
    {
        "leave_type": LeaveType.PERSONAL,
        "allocated_days_per_year": 3,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": False,
        "min_notice_in_days": 3,
        "max_consecutive_days": 3,
    },
    {
        "leave_type": LeaveType.MATERNITY,
        "allocated_days_per_year": 120,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": True,
        "min_notice_in_days": 30,
        "max_consecutive_days": None,
    },
    {
        "leave_type": LeaveType.PATERNITY,
        "allocated_days_per_year": 10,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": True,
        "min_notice_in_days": 7,
        "max_consecutive_days": None,
    },
    {
        "leave_type": LeaveType.BEREAVEMENT,
        "allocated_days_per_year": 5,
        "carryover_days": 0,
        "requires_approval": False,
        "requires_covering_employee": False,
        "min_notice_in_days": 0,
        "max_consecutive_days": 5,
    },
    {
        "leave_type": LeaveType.UNPAID,
        "allocated_days_per_year": 365,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": True,
        "min_notice_in_days": 14,
        "max_consecutive_days": None,
    },
]

ASSET_MANAGEMENT_MODULE = "Asset Management"
DEFAULT_ASSET_PERMISSION_ACTIONS = (
    "view_own_assets",
    "process_asset_return",
    "initiate_asset_return",
)
DEFAULT_USER_ASSET_PERMISSION_ACTIONS = (
    "view_own_assets",
    "initiate_asset_return",
)


def employee_document_upload_to(instance: "EmployeeDocument", filename: str) -> str:
    """
    Group uploads under employee_documents/{first}-{last}-{profile_id}/{year}/{month}/
    so R2/S3 prefixes match a person's name for easier browsing.
    """

    profile = instance.user_profile
    user = profile.user

    first_raw = (user.first_name or "").strip()
    last_raw = (user.last_name or "").strip()
    if not first_raw and not last_raw and profile.full_name:
        parts = profile.full_name.strip().split(None, 1)
        first_raw = parts[0] if parts else ""
        last_raw = parts[1] if len(parts) > 1 else ""

    first = slugify(first_raw) or "user"
    last = slugify(last_raw) or "user"

    path = Path(filename)
    ext = path.suffix.lower() or ".pdf"
    stem = slugify(path.stem) or "document"

    now = timezone.now()
    return (
        f"employee_documents/{first}-{last}-{profile.pk}/{now:%Y}/{now:%m}/{stem}{ext}"
    )


def user_avatar_upload_to(instance: "UserProfile", filename: str) -> str:
    """
    Store avatars under a stable Cloudflare/R2 key:
    avatars/{user_id}-{first_name}-{last_name}/avatar.png
    """

    user = instance.user
    first = slugify(user.first_name) or "user"
    last = slugify(user.last_name) or "user"
    return f"avatars/{user.id}-{first}-{last}/avatar.png"


class Permission(models.Model):
    module_name = models.CharField(max_length=100)
    feature_action = models.CharField(max_length=100)
    bit_position = models.PositiveIntegerField(unique=True, editable=False)

    class Meta:
        unique_together = ("module_name", "feature_action")
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return f"{self.module_name}: {self.feature_action}"

    def save(self, *args, **kwargs):
        if not self.bit_position:
            # Assign the next available bit position
            max_bit = (
                Permission.objects.aggregate(models.Max("bit_position"))[
                    "bit_position__max"
                ]
                or 0
            )
            self.bit_position = max_bit + 1
        super().save(*args, **kwargs)


def ensure_asset_permissions(actions=None):
    if actions is None:
        actions = DEFAULT_ASSET_PERMISSION_ACTIONS

    permissions = []
    for action in actions:
        permission, _ = Permission.objects.get_or_create(
            module_name=ASSET_MANAGEMENT_MODULE,
            feature_action=action,
        )
        permissions.append(permission)
    return permissions


class Role(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")

    def __str__(self):
        return self.name

    def has_permission(self, permission):
        return self.permissions.filter(id=permission.id).exists()

    def add_permission(self, permission):
        self.permissions.add(permission)

    def remove_permission(self, permission):
        self.permissions.remove(permission)

    class Meta:
        verbose_name = "Role"
        verbose_name_plural = "Roles"


class TechnologyTag(models.Model):
    name = models.CharField(max_length=60, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Technology Tag"
        verbose_name_plural = "Technology Tags"


class CPFLevel(models.Model):
    """Reference table for CPF (Career Progression Framework) levels."""

    name = models.CharField(max_length=100, unique=True)
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cpf_levels",
    )
    order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Rank within the role's CPF ladder (1 = entry level).",
    )

    class Meta:
        ordering = ["role", "order", "name"]
        verbose_name = "CPF Level"
        verbose_name_plural = "CPF Levels"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Department(models.Model):
    """Reference table for organizational departments."""

    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default="#475569")
    color_soft = models.CharField(max_length=7, default="#f1f5f9")
    head_employee = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="headed_departments",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Department"
        verbose_name_plural = "Departments"

    def __str__(self) -> str:
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, null=True)
    client = models.CharField(max_length=150, blank=True, null=True)
    app_stack = models.CharField(
        max_length=200, blank=True, null=True
    )  # e.g., "React, Django, PostgreSQL"
    project_type = models.CharField(
        max_length=20,
        choices=ProjectType.choices,
        default=ProjectType.CLIENT,
    )
    status = models.CharField(
        max_length=20,
        choices=ProjectStatus.choices,
        default=ProjectStatus.PLANNED,
    )
    stage = models.CharField(
        max_length=20,
        choices=ProjectStage.choices,
        default=ProjectStage.INTAKE,
    )
    stage_note = models.TextField(blank=True, default="")
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    owner = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        related_name="owned_projects",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if not self.name or not self.name.strip():
            errors["name"] = "Name is required."
        if self.project_type == ProjectType.CLIENT and not (self.client or "").strip():
            errors["client"] = "Client is required for client projects."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot be before start date."
        if errors:
            raise ValidationError(errors)

    class Meta:
        verbose_name = "Project"
        verbose_name_plural = "Projects"
        ordering = ["name"]


class TimeTask(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="time_tasks"
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    jira_issue_key = models.CharField(max_length=50, blank=True, default="")
    jira_project_key = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Time Task"
        verbose_name_plural = "Time Tasks"
        ordering = ["project__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"], name="unique_time_task_name_per_project"
            ),
            models.UniqueConstraint(
                fields=["jira_issue_key"],
                condition=~models.Q(jira_issue_key=""),
                name="unique_time_task_jira_issue_key",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "is_active"]),
            models.Index(fields=["jira_issue_key"]),
            models.Index(fields=["jira_project_key"]),
        ]

    def __str__(self):
        return f"{self.project.name}: {self.name}"


class JiraConnection(models.Model):
    base_url = models.URLField(blank=True, default="")
    auth_email = models.EmailField(blank=True, default="")
    api_token_encrypted = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=False)
    last_test_status = models.CharField(max_length=30, blank=True, default="")
    last_test_message = models.TextField(blank=True, default="")
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Jira Connection"
        verbose_name_plural = "Jira Connections"

    @classmethod
    def get_solo(cls):
        connection, _ = cls.objects.get_or_create(pk=1)
        return connection

    def set_api_token(self, token: str):
        self.api_token_encrypted = encrypt_secret(token)

    def get_api_token(self) -> str:
        return decrypt_secret(
            self.api_token_encrypted, legacy_salt="bloomhub-jira-api-token"
        )

    @property
    def has_api_token(self) -> bool:
        return bool(self.api_token_encrypted)

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.enabled and not all(
            [self.base_url, self.auth_email, self.has_api_token]
        ):
            raise ValidationError(
                "Enabled Jira connection requires base URL, auth email, and API token."
            )

    def __str__(self):
        return self.base_url or "Jira Connection"


class JiraUserMapping(models.Model):
    jira_account_id = models.CharField(max_length=150, unique=True)
    jira_display_name = models.CharField(max_length=255, blank=True, default="")
    employee = models.ForeignKey(
        "UserProfile", on_delete=models.CASCADE, related_name="jira_user_mappings"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Jira User Mapping"
        verbose_name_plural = "Jira User Mappings"
        ordering = ["jira_display_name", "jira_account_id"]

    def __str__(self):
        return f"{self.jira_account_id} -> {self.employee}"


class JiraProjectMapping(models.Model):
    jira_project_key = models.CharField(max_length=50, unique=True)
    jira_project_name = models.CharField(max_length=255, blank=True, default="")
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="jira_project_mappings"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Jira Project Mapping"
        verbose_name_plural = "Jira Project Mappings"
        ordering = ["jira_project_key"]

    def save(self, *args, **kwargs):
        self.jira_project_key = (self.jira_project_key or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.jira_project_key} -> {self.project}"


class JiraIssueMapping(models.Model):
    jira_issue_key = models.CharField(max_length=50, unique=True)
    jira_issue_id = models.CharField(max_length=100, blank=True, default="")
    task = models.ForeignKey(
        TimeTask, on_delete=models.CASCADE, related_name="jira_issue_mappings"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Jira Issue Mapping"
        verbose_name_plural = "Jira Issue Mappings"
        ordering = ["jira_issue_key"]

    def save(self, *args, **kwargs):
        self.jira_issue_key = (self.jira_issue_key or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.jira_issue_key} -> {self.task}"


class TempoConnection(models.Model):
    base_url = models.URLField(default="https://api.tempo.io/4")
    api_token_encrypted = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=False)
    last_test_status = models.CharField(max_length=30, blank=True, default="")
    last_test_message = models.TextField(blank=True, default="")
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tempo Connection"
        verbose_name_plural = "Tempo Connections"

    @classmethod
    def get_solo(cls):
        connection, _ = cls.objects.get_or_create(pk=1)
        return connection

    def set_api_token(self, token: str):
        self.api_token_encrypted = encrypt_secret(token)

    def get_api_token(self) -> str:
        return decrypt_secret(
            self.api_token_encrypted, legacy_salt="bloomhub-tempo-api-token"
        )

    @property
    def has_api_token(self) -> bool:
        return bool(self.api_token_encrypted)

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.enabled and not all([self.base_url, self.has_api_token]):
            raise ValidationError(
                "Enabled Tempo connection requires base URL and API token."
            )

    def __str__(self):
        return self.base_url or "Tempo Connection"


class TempoUserMapping(models.Model):
    tempo_user_id = models.CharField(max_length=150, unique=True)
    tempo_display_name = models.CharField(max_length=255, blank=True, default="")
    employee = models.ForeignKey(
        "UserProfile", on_delete=models.CASCADE, related_name="tempo_user_mappings"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tempo User Mapping"
        verbose_name_plural = "Tempo User Mappings"
        ordering = ["tempo_display_name", "tempo_user_id"]

    def __str__(self):
        return f"{self.tempo_user_id} -> {self.employee}"


class TempoAccountMapping(models.Model):
    tempo_account_id = models.CharField(max_length=150, unique=True)
    tempo_account_key = models.CharField(max_length=150, blank=True, default="")
    tempo_account_name = models.CharField(max_length=255, blank=True, default="")
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="tempo_account_mappings"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tempo Account Mapping"
        verbose_name_plural = "Tempo Account Mappings"
        ordering = ["tempo_account_key", "tempo_account_id"]

    def save(self, *args, **kwargs):
        self.tempo_account_key = (self.tempo_account_key or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tempo_account_id} -> {self.project}"


class TempoProjectMapping(models.Model):
    tempo_project_id = models.CharField(max_length=150, unique=True)
    tempo_project_key = models.CharField(max_length=150, blank=True, default="")
    tempo_project_name = models.CharField(max_length=255, blank=True, default="")
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="tempo_project_mappings"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tempo Project Mapping"
        verbose_name_plural = "Tempo Project Mappings"
        ordering = ["tempo_project_key", "tempo_project_id"]

    def save(self, *args, **kwargs):
        self.tempo_project_key = (self.tempo_project_key or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tempo_project_id} -> {self.project}"


class TempoTeamMapping(models.Model):
    tempo_team_id = models.CharField(max_length=150, unique=True)
    tempo_team_name = models.CharField(max_length=255, blank=True, default="")
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="tempo_team_mappings"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Tempo Team Mapping"
        verbose_name_plural = "Tempo Team Mappings"
        ordering = ["tempo_team_name", "tempo_team_id"]

    def __str__(self):
        return f"{self.tempo_team_id} -> {self.project}"


class Equipment(models.Model):
    name = models.CharField(max_length=150)
    serial_number = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.name} ({self.serial_number})"

    class Meta:
        verbose_name = "Equipment"
        verbose_name_plural = "Equipment"


class UserProfile(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    EmploymentStatus = EmploymentStatus

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)
    managers = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="direct_reports",
    )
    primary_manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_direct_reports",
    )
    employee_id = models.CharField(max_length=20, unique=True, blank=True, null=True)

    full_name = models.CharField(max_length=150, blank=True, null=True)
    email_address = models.EmailField(max_length=254, blank=True, null=True)

    department = models.CharField(max_length=100, blank=True, null=True)
    department_fk = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )
    is_remote = models.BooleanField(default=False)
    start_date = models.DateField(blank=True, null=True, default=None)
    hire_date = models.DateField(blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True, null=True)
    birthday = models.DateField(blank=True, null=True)
    career_level = models.CharField(max_length=100, blank=True, null=True)
    cpf_level = models.CharField(max_length=100, blank=True, null=True)
    tech_tags = models.ManyToManyField(TechnologyTag, blank=True, related_name="users")
    assigned_projects = models.ManyToManyField(
        Project,
        blank=True,
        related_name="employees",
        through="ProjectAssignment",
    )

    is_active = models.BooleanField(default=True, editable=False)
    employment_status = models.CharField(
        max_length=10, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Profile picture (employee avatar).
    # Auto-generated on registration when not provided.
    avatar = models.ImageField(
        upload_to=user_avatar_upload_to,
        blank=True,
        null=True,
    )
    # Stores a direct, permanent URL to the avatar (e.g. Google CDN, R2 public URL).
    # Preferred over `avatar` when set. Written by Google OAuth login.
    avatar_url = models.URLField(blank=True, null=True)
    intro_announcement = models.ForeignKey(
        "Announcement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="introduced_profiles",
    )
    intro_announcement_published_at = models.DateTimeField(null=True, blank=True)

    permissions = models.CharField(
        max_length=255, default=""
    )  # Bitmap stored as binary string

    def __str__(self):
        display_name = self.full_name or self.user.get_full_name() or self.user.username
        return f"{display_name} - {self.role.name if self.role else 'No Role'}"

    def _get_permissions_int(self):
        if not self.permissions:
            return 0
        try:
            return int(str(self.permissions), 2)
        except ValueError:
            # Fallback if old base-10 value survives before migration completes cleanup
            try:
                return int(str(self.permissions))
            except ValueError:
                return 0

    def has_permission(self, permission):
        # Check role permissions or user permissions
        if self.role and self.role.has_permission(permission):
            return True
        return (self._get_permissions_int() & (1 << permission.bit_position)) != 0

    def add_permission(self, permission):
        val = self._get_permissions_int()
        val |= 1 << permission.bit_position
        self.permissions = bin(val)[2:]
        self.save()

    def remove_permission(self, permission):
        val = self._get_permissions_int()
        val &= ~(1 << permission.bit_position)
        self.permissions = bin(val)[2:]
        self.save()

    @property
    def computed_permissions_bitmap(self):
        bitmap = self._get_permissions_int()
        if self.role:
            for perm in self.role.permissions.all():
                bitmap |= 1 << perm.bit_position
        return bitmap

    @property
    def current_salary(self):
        salary = self.salary_records.order_by("-effective_date").first()
        return salary.amount if salary else None

    @property
    def current_net_salary(self):
        """Resolve NET salary via CompensationPolicy keyed on cpf_level."""
        from core.services.compensation_service import resolve_policy

        policy = resolve_policy(self)
        return policy.net_monthly if policy else None

    def save(self, *args, **kwargs):
        self.is_active = self.employment_status != self.EmploymentStatus.INACTIVE
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Employee Profile"
        verbose_name_plural = "Employee Profiles"


# Backward-compat alias: views import DocumentType from .models
DocumentType = EmployeeDocumentType


class EmployeeDocument(models.Model):
    # Enum aliases — defined in core/enums.py; kept here for backward-compat access
    SourceType = EmployeeDocumentSourceType
    ProviderType = EmployeeDocumentProviderType

    user_profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(
        max_length=20,
        choices=EmployeeDocumentType.choices,
        default=EmployeeDocumentType.CV,
    )
    file = models.FileField(
        upload_to=employee_document_upload_to, null=True, blank=True
    )
    version = models.PositiveIntegerField(default=1)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_current = models.BooleanField(default=False)
    source_type = models.CharField(
        max_length=20,
        choices=EmployeeDocumentSourceType.choices,
        default=EmployeeDocumentSourceType.FILE,
    )
    provider = models.CharField(
        max_length=20,
        choices=EmployeeDocumentProviderType.choices,
        default=EmployeeDocumentProviderType.INTERNAL,
    )
    external_url = models.URLField(blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True, null=True)
    file_size = models.PositiveBigIntegerField(blank=True, null=True)
    mime_type = models.CharField(max_length=100, blank=True, null=True)
    canva_design_id = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        unique_together = ("user_profile", "doc_type", "version")
        verbose_name = "Employee Document"
        verbose_name_plural = "Employee Documents"

    def __str__(self):
        return f"{self.user_profile.user.username} - {self.doc_type} (v{self.version})"


class DocumentCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    visibility_rule = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Document Category"
        verbose_name_plural = "Document Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class DocumentCategoryDefault(models.Model):
    category = models.CharField(
        max_length=20,
        choices=_DocumentCategory.choices,
        unique=True,
    )
    allowed_roles = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "Document Category Default"
        verbose_name_plural = "Document Category Defaults"
        ordering = ["category"]

    def __str__(self):
        return f"{self.category}: {self.allowed_roles}"


class Document(models.Model):
    Category = _DocumentCategory
    SignatureStatus = DocumentSignatureStatus
    AccessRole = DocumentAccessRole
    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="managed_documents",
        null=True,
        blank=True,
    )
    uploaded_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
    )
    category = models.CharField(
        max_length=20,
        choices=_DocumentCategory.choices,
        default=_DocumentCategory.OTHER,
    )
    file_key = models.CharField(max_length=500)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    original_filename = models.CharField(max_length=255, blank=True, default="")
    file_size = models.PositiveBigIntegerField(default=0)
    mime_type = models.CharField(max_length=100, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expiry_date = models.DateField(blank=True, null=True)
    signed_at = models.DateTimeField(blank=True, null=True)
    signature_status = models.CharField(
        max_length=20,
        choices=DocumentSignatureStatus.choices,
        default=DocumentSignatureStatus.NOT_REQUIRED,
    )
    is_confidential = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    allowed_roles = models.JSONField(default=list, blank=True)

    class VisibilityScope(models.TextChoices):
        ROLES = "roles", "Roles"
        ONLY_ME = "only_me", "Only Me"
        PROJECT_GROUP = "project_group", "Project Group"

    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        default=VisibilityScope.ROLES,
    )
    archived = models.BooleanField(default=False)
    current_version = models.CharField(max_length=20, default="1.0")

    class Meta:
        verbose_name = "Document"
        verbose_name_plural = "Documents"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["signature_status"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["archived"]),
            models.Index(fields=["visibility_scope"]),
        ]

    def __str__(self):
        return self.name


class DocumentSigner(models.Model):
    Status = DocumentSignerStatus
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="signers"
    )
    name = models.CharField(max_length=255)
    email = models.EmailField()
    signed_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_signatures",
    )
    status = models.CharField(
        max_length=20,
        choices=DocumentSignerStatus.choices,
        default=DocumentSignerStatus.NOT_SENT,
    )
    signed_at = models.DateTimeField(null=True, blank=True)
    signature_metadata = models.JSONField(default=dict, blank=True)
    signature_hash = models.CharField(max_length=128, null=True, blank=True)
    signing_token_hash = models.CharField(max_length=128, null=True, blank=True)
    requested_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_document_signatures",
    )
    last_reminded_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Document Signer"
        verbose_name_plural = "Document Signers"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["document", "status"]),
            models.Index(fields=["document", "email"]),
            models.Index(fields=["signed_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.email}) – {self.status}"


class DocumentSignatureAuditLog(models.Model):
    class Event(models.TextChoices):
        REQUESTED = "requested", "Requested"
        VIEWED = "viewed", "Viewed"
        SIGNED = "signed", "Signed"
        REJECTED = "rejected", "Rejected"
        REMINDED = "reminded", "Reminded"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="signature_audit_events"
    )
    signer = models.ForeignKey(
        DocumentSigner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_signature_audit_events",
    )
    event = models.CharField(max_length=20, choices=Event.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Document Signature Audit Log"
        verbose_name_plural = "Document Signature Audit Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["document", "event"]),
            models.Index(fields=["signer", "event"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.document_id} {self.event} at {self.created_at}"


class DocumentVersion(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.CharField(max_length=20)
    file_key = models.CharField(max_length=500)
    file_size = models.PositiveBigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_versions",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("document", "version")

        verbose_name = "Document Version"

        verbose_name_plural = "Document Versions"

        ordering = ["version"]

    def __str__(self):
        return f"{self.document.name} v{self.version}"


class ProjectAssignment(models.Model):
    user_profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="project_assignments"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="assignments"
    )
    role = models.CharField(max_length=100, blank=True, null=True)
    allocation_percentage = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percent of full-time allocation, 0–100.",
    )
    weekly_allocation_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(40)],
        help_text="Planned weekly time allotment for this project assignment.",
    )
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=ProjectAssignmentStatus.choices,
        default=ProjectAssignmentStatus.ACTIVE,
    )
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Project Assignment"
        verbose_name_plural = "Project Assignments"
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["user_profile", "project", "status"]),
            models.Index(fields=["project", "end_date"]),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if not self.start_date:
            errors["start_date"] = "Start date is required."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot be before start date."
        if self.allocation_percentage is None or not (
            0 <= int(self.allocation_percentage) <= 100
        ):
            errors["allocation_percentage"] = "Allocation must be between 0 and 100."
        if self.weekly_allocation_hours is not None and not (
            0 <= self.weekly_allocation_hours <= 40
        ):
            errors["weekly_allocation_hours"] = (
                "Weekly allocation hours must be between 0 and 40."
            )
        if errors:
            raise ValidationError(errors)

    @property
    def is_current(self) -> bool:
        return self.end_date is None and self.status == ProjectAssignmentStatus.ACTIVE

    def save(self, *args, **kwargs):
        if (
            self.weekly_allocation_hours is None
            and self.allocation_percentage is not None
        ):
            self.weekly_allocation_hours = (
                Decimal("40.00") * Decimal(self.allocation_percentage) / Decimal("100")
            ).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        status = "current" if not self.end_date else f"until {self.end_date}"
        return f"{self.user_profile.user.username} @ {self.project.name} ({status})"


class TimeEntry(models.Model):
    employee = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="time_entries"
    )
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="time_entries"
    )
    task = models.ForeignKey(
        TimeTask,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_entries",
    )
    work_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01")),
            MaxValueValidator(Decimal("24")),
        ],
    )
    notes = models.TextField(blank=True, default="")
    source_type = models.CharField(
        max_length=30,
        choices=TimeEntrySourceType.choices,
        default=TimeEntrySourceType.MANUAL,
    )
    status = models.CharField(
        max_length=20,
        choices=TimeEntryStatus.choices,
        default=TimeEntryStatus.DRAFT,
    )
    source_external_id = models.CharField(max_length=150, blank=True, default="")
    source_metadata = models.JSONField(default=dict, blank=True)
    duplicate_fingerprint = models.CharField(max_length=64, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_time_entries",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_time_entries",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rejected_time_entries",
    )
    rejection_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Time Entry"
        verbose_name_plural = "Time Entries"
        ordering = ["-work_date", "start_time", "employee_id", "project_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "source_external_id"],
                condition=~models.Q(source_external_id=""),
                name="unique_time_entry_external_source",
            ),
        ]
        indexes = [
            models.Index(fields=["employee", "work_date"]),
            models.Index(fields=["employee", "work_date", "start_time"]),
            models.Index(fields=["project", "work_date"]),
            models.Index(fields=["status", "work_date"]),
            models.Index(fields=["source_type", "source_external_id"]),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.task_id and self.project_id and self.task.project_id != self.project_id:
            errors["task"] = "Task must belong to the selected project."
        if self.hours is None or self.hours <= 0:
            errors["hours"] = "Hours must be greater than zero."
        if self.hours and self.hours > Decimal("24"):
            errors["hours"] = "Hours cannot exceed 24 for a single day."
        if self.source_type != TimeEntrySourceType.MANUAL and not self.source_metadata:
            errors["source_metadata"] = (
                "Imported entries must preserve source metadata."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.employee} {self.work_date} {self.hours}h"


class TimeEntryAuditEvent(models.Model):
    time_entry = models.ForeignKey(
        TimeEntry, on_delete=models.CASCADE, related_name="audit_events"
    )
    event_type = models.CharField(
        max_length=30, choices=TimeEntryAuditEventType.choices
    )
    actor = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_entry_audit_events",
    )
    message = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Time Entry Audit Event"
        verbose_name_plural = "Time Entry Audit Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["time_entry", "event_type"]),
            models.Index(fields=["actor", "created_at"]),
        ]

    def __str__(self):
        return f"{self.time_entry_id} {self.event_type}"


def time_import_upload_to(instance: "TimeImportBatch", filename: str) -> str:
    now = timezone.now()
    return f"time_imports/{now:%Y/%m}/{filename}"


class TimeImportBatch(models.Model):
    source_type = models.CharField(
        max_length=30,
        choices=ImportBatchSource.choices,
        default=ImportBatchSource.DOCUMENT_IMPORT,
    )
    file_name = models.CharField(max_length=255, blank=True, default="")
    source_file = models.FileField(
        upload_to=time_import_upload_to, null=True, blank=True
    )
    uploaded_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_import_batches",
    )
    requested_filters = models.JSONField(default=dict, blank=True)
    column_mapping = models.JSONField(default=dict, blank=True)
    detected_columns = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=30,
        choices=ImportBatchStatus.choices,
        default=ImportBatchStatus.UPLOADED,
    )
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    skipped_rows = models.PositiveIntegerField(default=0)
    committed_rows = models.PositiveIntegerField(default=0)
    validation_messages = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Time Import Batch"
        verbose_name_plural = "Time Import Batches"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "status"]),
            models.Index(fields=["uploaded_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.source_type} import {self.pk or 'new'}"


class TimeImportRow(models.Model):
    batch = models.ForeignKey(
        TimeImportBatch, on_delete=models.CASCADE, related_name="rows"
    )
    sheet_name = models.CharField(max_length=150, blank=True, default="")
    table_index = models.PositiveIntegerField(null=True, blank=True)
    row_number = models.PositiveIntegerField()
    row_index = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict, blank=True)
    parsed_data = models.JSONField(default=dict, blank=True)
    original_row_fingerprint = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=30,
        choices=ImportRowStatus.choices,
        default=ImportRowStatus.PENDING,
    )
    validation_messages = models.JSONField(default=list, blank=True)
    committed_entry = models.ForeignKey(
        TimeEntry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_rows",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Time Import Row"
        verbose_name_plural = "Time Import Rows"
        ordering = ["batch_id", "row_index"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["original_row_fingerprint"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_index"],
                name="unique_time_import_row_index_per_batch",
            ),
        ]

    def __str__(self):
        return f"Batch {self.batch_id} row {self.row_number}"


class EquipmentAssignment(models.Model):
    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE, related_name="assignments"
    )
    user_profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="equipment_assignments"
    )
    assigned_date = models.DateField()
    returned_date = models.DateField(blank=True, null=True)

    class Meta:
        verbose_name = "Equipment Assignment"
        verbose_name_plural = "Equipment Assignments"
        ordering = ["-assigned_date"]

    def __str__(self):
        status = "returned" if self.returned_date else "assigned"
        return f"{self.equipment} -> {self.user_profile.user.username} ({status})"


class SalaryRecord(models.Model):
    user_profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="salary_records"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    effective_date = models.DateField()
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Salary Record"
        verbose_name_plural = "Salary Records"
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.user_profile.user.username}: {self.amount} from {self.effective_date}"


class ChangeLog(models.Model):
    user_profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="change_logs"
    )
    changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    field_name = models.CharField(max_length=100)
    old_value = models.TextField(blank=True, null=True)
    new_value = models.TextField(blank=True, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Change Log"
        verbose_name_plural = "Change Logs"
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.user_profile.user.username} – {self.field_name} @ {self.changed_at.isoformat()}"


class EmployeeProfileChangeHistory(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    TrackedField = TrackedField

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="profile_change_history",
    )
    field = models.CharField(max_length=32, choices=TrackedField.choices)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile_changes_made",
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "Employee Profile Change History"
        verbose_name_plural = "Employee Profile Change History"
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["employee", "-changed_at"]),
            models.Index(fields=["field"]),
        ]

    def __str__(self):
        return f"{self.employee.user.username} - {self.field} @ {self.changed_at.isoformat()}"


class AssetCategory(models.TextChoices):
    LAPTOPS = "laptops", "Laptops"
    PHONES = "phones", "Phones"
    MONITORS = "monitors", "Monitors"
    HEADPHONES = "headphones", "Headphones"
    CAMERAS = "cameras", "Cameras"
    VEHICLES = "vehicles", "Vehicles"
    FURNITURE = "furniture", "Furniture"
    OTHER = "other", "Other"


class Asset(models.Model):
    """
    Comprehensive Asset model for equipment management
    """

    asset_id = models.CharField(
        max_length=50, unique=True, help_text="Unique identifier for the asset"
    )
    name = models.CharField(max_length=200, help_text="Asset name/type")
    category = models.CharField(
        max_length=20,
        choices=AssetCategory.choices,
        default=AssetCategory.OTHER,
        help_text="Asset category",
    )
    condition = models.CharField(
        max_length=20,
        choices=AssetCondition.choices,
        default=AssetCondition.GOOD,
        help_text="Current condition of the asset",
    )
    warranty_until = models.DateField(
        null=True, blank=True, help_text="Warranty expiration date"
    )
    purchase_date = models.DateField(help_text="Date when the asset was purchased")
    status = models.CharField(
        max_length=20,
        choices=AssetStatus.choices,
        default=AssetStatus.ACTIVE,
        help_text="Current status of the asset",
    )

    # Additional useful fields for comprehensive asset management
    serial_number = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        help_text="Manufacturer serial number",
    )
    model = models.CharField(
        max_length=100, null=True, blank=True, help_text="Asset model"
    )
    manufacturer = models.CharField(
        max_length=100, null=True, blank=True, help_text="Asset manufacturer"
    )
    purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Purchase price of the asset",
    )
    description = models.TextField(
        blank=True, null=True, help_text="Additional description or notes"
    )
    qr_code_payload = models.URLField(
        max_length=500,
        unique=True,
        null=True,
        blank=True,
        help_text="Stable frontend asset URL encoded in the asset QR code",
    )
    qr_code_image = models.FileField(
        blank=True,
        help_text="Persisted PNG QR image for the asset",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Asset"
        verbose_name_plural = "Assets"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.asset_id} - {self.name}"

    @property
    def is_under_warranty(self):
        """Check if asset is still under warranty"""
        if not self.warranty_until:
            return False
        from django.utils import timezone

        return self.warranty_until > timezone.now().date()

    @property
    def current_assignment(self):
        """Get current active assignment if any"""
        active_assignments = getattr(self, "active_assignments", None)
        if active_assignments is not None:
            return active_assignments[0] if active_assignments else None

        return (
            self.assignments.filter(returned_at__isnull=True)
            .select_related("employee__user")
            .first()
        )

    @property
    def is_available(self):
        """Check if asset is available for assignment"""
        has_active_assignment = getattr(self, "has_active_assignment", None)
        if has_active_assignment is not None:
            return self.status == AssetStatus.ACTIVE and not has_active_assignment

        return self.status == AssetStatus.ACTIVE and not self.current_assignment


@receiver(post_save, sender=Asset)
def ensure_asset_qr_code_after_save(sender, instance, created, **kwargs):
    if kwargs.get("raw") or not instance.pk:
        return

    if instance.qr_code_payload and instance.qr_code_image:
        return

    from core.services.asset_qr import ensure_asset_qr_code as ensure_qr_code

    ensure_qr_code(instance)


class Assignment(models.Model):
    """
    Asset assignment to employees
    """

    class ReturnRequestStatus(models.TextChoices):
        NONE = "none", "None"
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="assignments",
        help_text="Asset being assigned",
    )
    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="asset_assignments",
        help_text="Employee receiving the asset",
    )
    assigned_at = models.DateTimeField(
        auto_now_add=True, help_text="When the asset was assigned"
    )
    returned_at = models.DateTimeField(
        null=True, blank=True, help_text="When the asset was returned (optional)"
    )
    return_request_status = models.CharField(
        max_length=20,
        choices=ReturnRequestStatus.choices,
        default=ReturnRequestStatus.NONE,
        help_text="Two-step return workflow status",
    )
    return_requested_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_requests_made",
        help_text="Who requested asset return",
    )
    return_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When return was requested",
    )
    return_reviewed_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_requests_reviewed",
        help_text="Who reviewed return request",
    )
    return_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When return request was reviewed",
    )
    return_rejection_reason = models.TextField(
        blank=True,
        null=True,
        help_text="Reason provided when return request is rejected",
    )
    return_description = models.TextField(
        blank=True,
        null=True,
        help_text="Description entered when requesting a return",
    )
    return_checklist = models.JSONField(
        default=list,
        blank=True,
        help_text="Structured checklist submitted as part of the return request",
    )

    # Additional useful fields
    assigned_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments_made",
        help_text="Who made the assignment",
    )
    return_condition = models.CharField(
        max_length=20,
        choices=AssetCondition.choices,
        null=True,
        blank=True,
        help_text="Condition when returned",
    )
    notes = models.TextField(
        blank=True, null=True, help_text="Additional notes about the assignment"
    )
    asset_id_snapshot = models.CharField(max_length=50, default="", blank=True)
    asset_name_snapshot = models.CharField(max_length=200, default="", blank=True)

    class Meta:
        verbose_name = "Assignment"
        verbose_name_plural = "Assignments"
        ordering = ["-assigned_at"]

    def __str__(self):
        status = (
            "Active" if not self.returned_at else f"Returned {self.returned_at.date()}"
        )
        return f"{self.asset.asset_id} → {self.employee.user.get_full_name() or self.employee.user.username} ({status})"

    def save(self, *args, **kwargs):
        if self.asset_id and not self.asset_id_snapshot:
            self.asset_id_snapshot = self.asset.asset_id
        if self.asset_id and not self.asset_name_snapshot:
            self.asset_name_snapshot = self.asset.name
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        """Check if assignment is currently active"""
        return self.returned_at is None

    @property
    def duration_days(self):
        """Calculate assignment duration in days"""
        from django.utils import timezone

        end_date = self.returned_at or timezone.now()
        return (end_date.date() - self.assigned_at.date()).days


class ReplacementLog(models.Model):
    """
    Log of asset replacements and reasons
    """

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="replacement_logs",
        help_text="Asset that was replaced",
    )
    reason = models.TextField(help_text="Reason for replacement")
    date = models.DateField(help_text="Date when the replacement occurred")
    asset_status_before = models.CharField(
        max_length=20,
        choices=AssetStatus.choices,
        null=True,
        blank=True,
        help_text="Asset status before the replacement or maintenance event",
    )
    asset_status_after = models.CharField(
        max_length=20,
        choices=AssetStatus.choices,
        null=True,
        blank=True,
        help_text="Asset status after the replacement or maintenance event",
    )
    asset_condition_before = models.CharField(
        max_length=20,
        choices=AssetCondition.choices,
        null=True,
        blank=True,
        help_text="Asset condition before the replacement or maintenance event",
    )
    asset_condition_after = models.CharField(
        max_length=20,
        choices=AssetCondition.choices,
        null=True,
        blank=True,
        help_text="Asset condition after the replacement or maintenance event",
    )

    # Additional useful fields
    replaced_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replacements_made",
        help_text="Who performed the replacement",
    )
    replacement_asset = models.ForeignKey(
        Asset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaced_assets",
        help_text="New asset that replaced this one",
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Cost of replacement",
    )

    class Meta:
        verbose_name = "Replacement Log"
        verbose_name_plural = "Replacement Logs"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.asset.asset_id} replaced on {self.date} - {self.reason[:50]}"


class ScheduledMaintenance(models.Model):
    """One-off planned maintenance event for an asset."""

    class MaintenanceType(models.TextChoices):
        PREVENTIVE = "preventive", "Preventive"
        REPAIR = "repair", "Repair"
        INSPECTION = "inspection", "Inspection"
        WARRANTY = "warranty", "Warranty"
        REPLACEMENT = "replacement", "Replacement"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="scheduled_maintenance",
        help_text="Asset that needs maintenance",
    )
    due_date = models.DateField(help_text="Date when maintenance is due")
    reason = models.TextField(help_text="Reason maintenance is needed")
    maintenance_type = models.CharField(
        max_length=20,
        choices=MaintenanceType.choices,
        help_text="Type of scheduled maintenance",
    )
    owner = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_scheduled_maintenance",
        help_text="Optional person responsible for the maintenance",
    )
    estimated_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Estimated cost of the scheduled maintenance",
    )
    vendor = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Optional external vendor or service provider",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        help_text="Scheduled maintenance lifecycle status",
    )
    cancelled_reason = models.TextField(
        blank=True,
        default="",
        help_text="Optional reason the scheduled maintenance was cancelled",
    )
    completed_log = models.OneToOneField(
        ReplacementLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_maintenance",
        help_text="Historical maintenance log created when this schedule is completed",
    )
    created_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_scheduled_maintenance",
        help_text="User who created the scheduled maintenance",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Scheduled Maintenance"
        verbose_name_plural = "Scheduled Maintenance"
        ordering = ["due_date", "-created_at"]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["asset", "status"]),
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["maintenance_type", "due_date"]),
        ]

    @property
    def due_state(self):
        if self.status != self.Status.SCHEDULED:
            return None
        today = timezone.localdate()
        if self.due_date < today:
            return "overdue"
        if self.due_date == today:
            return "due_today"
        return "upcoming"

    def __str__(self):
        return f"{self.asset.asset_id} maintenance due {self.due_date}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        full_name = instance.get_full_name() or instance.username
        email_address = instance.email

        profile, _ = UserProfile.objects.get_or_create(
            user=instance,
            defaults={"full_name": full_name, "email_address": email_address},
        )

        profile.full_name = profile.full_name or full_name
        profile.email_address = profile.email_address or email_address

        default_asset_permissions = ensure_asset_permissions()

        # If superuser, assign all permissions
        if instance.is_superuser:
            # Get all permissions and build a bitmap with all their bits set
            permissions = Permission.objects.all()
            if permissions.exists():
                all_permissions_int = 0
                for perm in permissions:
                    # Set bit at position perm.bit_position
                    all_permissions_int |= 1 << perm.bit_position
                profile.permissions = bin(all_permissions_int)[2:]

        profile.save(update_fields=["full_name", "email_address", "permissions"])
        initialize_leave_balances_for_profile(profile)

        for permission in default_asset_permissions:
            if permission.feature_action not in DEFAULT_USER_ASSET_PERMISSION_ACTIONS:
                continue
            if not profile.has_permission(permission):
                profile.add_permission(permission)

        if not profile.avatar:
            try:
                # Prevent avatar generation locally (DEBUG=True) or during test runs
                if (
                    getattr(settings, "DEBUG", False)
                    or "test" in sys.argv
                    or any("pytest" in arg for arg in sys.argv)
                ):
                    return

                # Use full_name for avatar initials, fallback to first/last name or username
                name_for_avatar = (
                    profile.full_name or instance.get_full_name() or instance.username
                )
                initials = get_initials(name_for_avatar, profile.user.username)
                seed = f"{profile.user.id}:{profile.user.username}"
                png_bytes = generate_initials_avatar_png(initials, seed=seed)
                profile.avatar.save(
                    "avatar.png",
                    ContentFile(png_bytes),
                    save=True,
                )
            except Exception:
                pass


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    try:
        instance.profile.save()
    except Exception:
        pass


# ──────────────────────────────────────────
# Leave Management System
# ──────────────────────────────────────────


class LeavePolicy(models.Model):
    """
    Defines organizational leave policies for different leave types.
    """

    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    LeaveType = LeaveType

    leave_type = models.CharField(max_length=20, choices=LeaveType.choices, unique=True)
    allocated_days_per_year = models.PositiveIntegerField(
        default=0, help_text="Number of days allocated per year"
    )
    carryover_days = models.PositiveIntegerField(
        default=0, help_text="Maximum days that can be carried over to next year"
    )
    requires_approval = models.BooleanField(
        default=True, help_text="Whether this leave type requires manager approval"
    )
    requires_covering_employee = models.BooleanField(
        default=False, help_text="Whether a covering employee must be assigned"
    )
    min_notice_in_days = models.PositiveIntegerField(
        default=0, help_text="Minimum days notice required before leave start date"
    )
    max_consecutive_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum consecutive days allowed (optional)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leave Policy"
        verbose_name_plural = "Leave Policies"
        ordering = ["leave_type"]

    def __str__(self):
        return f"{self.get_leave_type_display()} Policy ({self.allocated_days_per_year} days/year)"


class LeaveBalance(models.Model):
    """
    Tracks leave balance for each employee per leave type per year.
    """

    employee = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="leave_balances"
    )
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    allocated = models.PositiveIntegerField(
        default=0, help_text="Total days allocated for this period"
    )
    used = models.PositiveIntegerField(
        default=0, help_text="Days already used/approved"
    )
    carryover = models.PositiveIntegerField(
        default=0, help_text="Days carried over from previous year"
    )
    year = models.PositiveIntegerField(help_text="Calendar year for this balance")
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leave Balance"
        verbose_name_plural = "Leave Balances"
        unique_together = ("employee", "leave_type", "year")
        ordering = ["-year", "employee", "leave_type"]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.get_leave_type_display()} {self.year} ({self.remaining} days remaining)"

    @property
    def remaining(self):
        """Calculate remaining leave days."""
        return max(0, (self.allocated + self.carryover) - self.used)


def ensure_default_leave_policies() -> int:
    """Create the default leave policies if they are missing."""
    created_count = 0
    for policy_data in DEFAULT_LEAVE_POLICIES:
        _, created = LeavePolicy.objects.get_or_create(
            leave_type=policy_data["leave_type"],
            defaults=policy_data,
        )
        if created:
            created_count += 1
    return created_count


def initialize_leave_balances_for_profile(
    employee: UserProfile, year: int | None = None
) -> int:
    """Create current-year leave balances for a user profile."""
    if year is None:
        year = timezone.now().year

    ensure_default_leave_policies()

    created_count = 0
    for policy in LeavePolicy.objects.all():
        _, created = LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=policy.leave_type,
            year=year,
            defaults={
                "allocated": policy.allocated_days_per_year,
                "used": 0,
                "carryover": 0,
            },
        )
        if created:
            created_count += 1
    return created_count


class LeaveRequest(models.Model):
    """
    Represents an employee's leave request.
    """

    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    Status = LeaveRequestStatus

    employee = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="leave_requests"
    )
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=LeaveRequestStatus.choices,
        default=LeaveRequestStatus.PENDING,
    )
    covering_employee = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="covering_for",
        help_text="Employee covering during leave",
    )
    submitted_date = models.DateTimeField(auto_now_add=True)
    lead_approver = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_approved_leaves",
        help_text="Tech Lead who gave first-level approval",
    )
    lead_approved_date = models.DateTimeField(null=True, blank=True)
    lead_approval_comments = models.TextField(
        blank=True, help_text="Comments from Tech Lead"
    )
    approver = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_leaves",
        help_text="HR who gave final approval/rejection",
    )
    approved_date = models.DateTimeField(null=True, blank=True)
    approval_comments = models.TextField(
        blank=True, help_text="Comments from HR approver"
    )
    rejection_reason = models.TextField(blank=True, help_text="Reason for rejection")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leave Request"
        verbose_name_plural = "Leave Requests"
        ordering = ["-submitted_date"]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.get_leave_type_display()} ({self.start_date} to {self.end_date}) - {self.status}"

    @property
    def days(self):
        """Calculate number of working days (excluding weekends)."""
        if not self.start_date or not self.end_date:
            return 0

        from datetime import timedelta

        current = self.start_date
        count = 0
        while current <= self.end_date:
            # 0 = Monday, 6 = Sunday
            if current.weekday() < 5:  # Monday to Friday
                count += 1
            current += timedelta(days=1)
        return count

    def is_overlapping(self, exclude_self=True):
        """Check if this leave request overlaps with other approved/pending leaves."""
        overlapping = LeaveRequest.objects.filter(
            employee=self.employee,
            start_date__lte=self.end_date,
            end_date__gte=self.start_date,
        ).exclude(status__in=[self.Status.REJECTED, self.Status.CANCELLED])

        if exclude_self and self.pk:
            overlapping = overlapping.exclude(pk=self.pk)

        return overlapping.exists()


def _rebuild_leave_analytics_for(employee_id: int) -> None:
    from django.db import transaction as _transaction

    from core.services.leave_analytics_service import (
        materialize_leave_monthly_aggregates,
    )

    def _run():
        employee = UserProfile.objects.filter(pk=employee_id).first()
        if employee is None:
            return
        materialize_leave_monthly_aggregates(employee=employee)

    _transaction.on_commit(_run)


@receiver(post_save, sender=LeaveRequest)
def rebuild_leave_analytics_on_request_save(sender, instance, **kwargs):
    if kwargs.get("raw") or instance.employee_id is None:
        return
    _rebuild_leave_analytics_for(instance.employee_id)


@receiver(post_delete, sender=LeaveRequest)
def rebuild_leave_analytics_on_request_delete(sender, instance, **kwargs):
    if instance.employee_id is None:
        return
    _rebuild_leave_analytics_for(instance.employee_id)


class LeaveApprovalWorkflow(models.Model):
    """
    Manages multi-level approval workflow for leave requests.
    """

    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    WorkflowStatus = LeaveWorkflowStatus

    leave_request = models.OneToOneField(
        LeaveRequest, on_delete=models.CASCADE, related_name="approval_workflow"
    )
    approval_chain = models.JSONField(
        default=list,
        help_text="List of approver user profile IDs in order",
    )
    current_step = models.PositiveIntegerField(default=0)
    current_approver = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_approvals",
    )
    status = models.CharField(
        max_length=20,
        choices=LeaveWorkflowStatus.choices,
        default=LeaveWorkflowStatus.PENDING,
    )
    comments = models.JSONField(
        default=list, help_text="List of comments from each approval step"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leave Approval Workflow"
        verbose_name_plural = "Leave Approval Workflows"

    def __str__(self):
        return f"Workflow for {self.leave_request} - Step {self.current_step + 1} of {len(self.approval_chain)}"

    def get_next_approver(self):
        """Get the next approver in the chain."""
        if self.current_step < len(self.approval_chain):
            approver_id = self.approval_chain[self.current_step]
            try:
                return UserProfile.objects.get(id=approver_id)
            except UserProfile.DoesNotExist:
                return None
        return None

    def advance_workflow(self, approved, comment=""):
        """Move workflow to next step or complete it."""
        if comment:
            self.comments.append(
                {
                    "step": self.current_step,
                    "approver_id": (
                        self.current_approver.id if self.current_approver else None
                    ),
                    "comment": comment,
                    "approved": approved,
                }
            )

        if not approved:
            self.status = self.WorkflowStatus.REJECTED
            self.save()
            return False

        self.current_step += 1

        if self.current_step >= len(self.approval_chain):
            # All approvals completed
            self.status = self.WorkflowStatus.APPROVED
            self.current_approver = None
        else:
            # Move to next approver
            self.status = self.WorkflowStatus.IN_REVIEW
            self.current_approver = self.get_next_approver()

        self.save()
        return True


class LeaveAdjustment(models.Model):
    """
    Tracks manual adjustments to leave balances by HR/Admin.
    Provides audit trail for compliance.
    """

    employee = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="leave_adjustments"
    )
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    old_allocated = models.PositiveIntegerField(help_text="Previous allocated days")
    new_allocated = models.PositiveIntegerField(help_text="New allocated days")
    reason = models.TextField(help_text="Reason for adjustment")
    adjusted_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="adjustments_made",
    )
    adjusted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Leave Adjustment"
        verbose_name_plural = "Leave Adjustments"
        ordering = ["-adjusted_at"]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.get_leave_type_display()} adjusted from {self.old_allocated} to {self.new_allocated} days"


# ──────────────────────────────────────────
# Leave Analytics
# ──────────────────────────────────────────


class LeaveMonthlyAggregate(models.Model):
    """
    Pre-computed fact table for leave analytics. One row per
    (employee, leave_type, year, month) bucket. Sourced from LeaveRequest by
    `leave_analytics_service.materialize_leave_monthly_aggregates`.

    All `*_days` fields are working-day counts (Mon-Fri), distributed across the
    months they actually fall in (so a leave spanning two months produces two
    rows). Downstream reports aggregate further by year, dept, etc.
    """

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="leave_monthly_aggregates",
    )
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    year = models.PositiveIntegerField(help_text="Calendar year")
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text="Calendar month (1-12)",
    )
    approved_days = models.PositiveIntegerField(
        default=0, help_text="Working days from approved leaves in this bucket"
    )
    pending_days = models.PositiveIntegerField(
        default=0, help_text="Working days from pending leaves in this bucket"
    )
    rejected_days = models.PositiveIntegerField(
        default=0, help_text="Working days from rejected leaves in this bucket"
    )
    cancelled_days = models.PositiveIntegerField(
        default=0, help_text="Working days from cancelled leaves in this bucket"
    )
    requests_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of distinct LeaveRequest rows contributing to this bucket",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leave Monthly Aggregate"
        verbose_name_plural = "Leave Monthly Aggregates"
        unique_together = ("employee", "leave_type", "year", "month")
        ordering = ["-year", "-month", "employee", "leave_type"]
        indexes = [
            models.Index(fields=["year", "month"]),
            models.Index(fields=["leave_type", "year", "month"]),
            models.Index(fields=["employee", "year"]),
        ]

    def __str__(self):
        return (
            f"{self.employee.user.get_full_name()} · "
            f"{self.get_leave_type_display()} · {self.year}-{self.month:02d} · "
            f"{self.approved_days}d approved"
        )


class LeaveBalanceSnapshot(models.Model):
    """
    Point-in-time snapshot of a `LeaveBalance` row, kept for historical reporting.
    Allows charting balance drift over time even after the live `LeaveBalance`
    has been adjusted, carried over, or reset for a new year.
    """

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="leave_balance_snapshots",
    )
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    year = models.PositiveIntegerField(help_text="Calendar year for the balance")
    snapshot_date = models.DateField(
        help_text="Date the snapshot was taken (UTC date)",
    )
    allocated = models.PositiveIntegerField(default=0)
    used = models.PositiveIntegerField(default=0)
    carryover = models.PositiveIntegerField(default=0)
    remaining = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Leave Balance Snapshot"
        verbose_name_plural = "Leave Balance Snapshots"
        unique_together = ("employee", "leave_type", "year", "snapshot_date")
        ordering = ["-snapshot_date", "employee", "leave_type"]
        indexes = [
            models.Index(fields=["snapshot_date"]),
            models.Index(fields=["employee", "year"]),
        ]

    def __str__(self):
        return (
            f"{self.employee.user.get_full_name()} · "
            f"{self.get_leave_type_display()} · {self.year} @ {self.snapshot_date} · "
            f"{self.remaining}d remaining"
        )


# ──────────────────────────────────────────
# Performance Reviews
# ──────────────────────────────────────────


class PerformanceReview(models.Model):
    # Enum aliases — defined in core/enums.py; kept here for backward-compat access
    ReviewType = ReviewType
    Status = ReviewStatus
    Outcome = ReviewOutcome

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="performance_reviews",
    )
    reviewer = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews_to_conduct",
    )
    created_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_reviews_created",
    )
    updated_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_reviews_updated",
    )
    review_type = models.CharField(
        max_length=20,
        choices=ReviewType.choices,
        default=ReviewType.QUARTERLY,
    )
    title = models.CharField(max_length=200, blank=True, default="")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    scheduled_date = models.DateField(
        help_text="Date on which the formal performance review is due."
    )
    next_review_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional follow-up review date for the next cycle.",
    )
    status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.SCHEDULED,
    )
    outcome = models.CharField(
        max_length=30,
        choices=ReviewOutcome.choices,
        blank=True,
        default="",
    )
    overall_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Overall review rating on a 1-5 scale.",
    )
    performance_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Optional normalized performance score (0-100).",
    )
    cpf_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Optional CPF score (0-100).",
    )
    cpf_current_level = models.CharField(max_length=100, blank=True, default="")
    cpf_recommended_level = models.CharField(max_length=100, blank=True, default="")
    summary = models.TextField(
        blank=True,
        default="",
        help_text="High-level review outcome summary.",
    )
    employee_comments = models.TextField(blank=True, default="")
    reviewer_comments = models.TextField(blank=True, default="")
    reminder_offsets_days = models.JSONField(
        default=list,
        blank=True,
        help_text="Reminder offsets (days before scheduled date) used by reminder jobs.",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Performance Review"
        verbose_name_plural = "Performance Reviews"
        ordering = ["-scheduled_date", "-created_at"]
        indexes = [
            models.Index(fields=["status", "scheduled_date"]),
            models.Index(fields=["employee", "scheduled_date"]),
            models.Index(fields=["reviewer", "scheduled_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(overall_rating__isnull=True)
                    | models.Q(overall_rating__gte=1, overall_rating__lte=5)
                ),
                name="perf_review_rating_between_1_and_5",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(performance_score__isnull=True)
                    | models.Q(performance_score__gte=0, performance_score__lte=100)
                ),
                name="perf_review_performance_score_between_0_100",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cpf_score__isnull=True)
                    | models.Q(cpf_score__gte=0, cpf_score__lte=100)
                ),
                name="perf_review_cpf_score_between_0_100",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(period_start__isnull=True)
                    | models.Q(period_end__isnull=True)
                    | models.Q(period_start__lte=models.F("period_end"))
                ),
                name="perf_review_period_start_before_end",
            ),
        ]

    def __str__(self):
        employee_name = (
            self.employee.user.get_full_name() or self.employee.user.username
        )
        return f"{employee_name} - {self.get_review_type_display()} ({self.scheduled_date})"


class PerformanceReviewNote(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    Visibility = ReviewNoteVisibility

    review = models.ForeignKey(
        PerformanceReview,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    author = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="performance_review_notes",
    )
    visibility = models.CharField(
        max_length=20,
        choices=ReviewNoteVisibility.choices,
        default=ReviewNoteVisibility.SHARED,
    )
    content = models.TextField()
    edited_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_review_notes_edited",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Performance Review Note"
        verbose_name_plural = "Performance Review Notes"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["review", "visibility"]),
            models.Index(fields=["author", "created_at"]),
        ]

    def __str__(self):
        return f"Note #{self.pk} ({self.visibility}) for review #{self.review_id}"


class PerformanceReviewActionPoint(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    Status = ActionPointStatus

    review = models.ForeignKey(
        PerformanceReview,
        on_delete=models.CASCADE,
        related_name="action_points",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_review_action_points",
    )
    created_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_review_action_points_created",
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ActionPointStatus.choices,
        default=ActionPointStatus.PENDING,
    )
    progress = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Performance Review Action Point"
        verbose_name_plural = "Performance Review Action Points"
        ordering = ["due_date", "created_at"]
        indexes = [
            models.Index(fields=["review", "status"]),
            models.Index(fields=["owner", "due_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress__gte=0, progress__lte=100),
                name="perf_review_action_point_progress_between_0_100",
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.status})"


class PerformanceReviewAttachment(models.Model):
    review = models.ForeignKey(
        PerformanceReview,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="performance_review_attachments",
    )
    file = models.FileField(upload_to="performance_reviews/attachments/%Y/%m/%d/")
    original_name = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=100, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Performance Review Attachment"
        verbose_name_plural = "Performance Review Attachments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["review", "created_at"]),
            models.Index(fields=["uploaded_by", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.file:
            if not self.original_name:
                self.original_name = self.file.name.split("/")[-1]
            if not self.size_bytes:
                self.size_bytes = self.file.size
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.original_name or self.file.name} (review #{self.review_id})"


class PerformanceReviewReminder(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    ReminderType = ReminderType

    review = models.ForeignKey(
        PerformanceReview,
        on_delete=models.CASCADE,
        related_name="reminders",
    )
    recipient = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="performance_review_reminders",
    )
    reminder_type = models.CharField(max_length=20, choices=ReminderType.choices)
    message = models.CharField(max_length=255, blank=True, default="")
    scheduled_for = models.DateTimeField()
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Performance Review Reminder"
        verbose_name_plural = "Performance Review Reminders"
        ordering = ["-scheduled_for", "-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "scheduled_for"]),
            models.Index(fields=["is_sent", "scheduled_for"]),
            models.Index(fields=["review", "reminder_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["review", "recipient", "reminder_type", "scheduled_for"],
                name="perf_review_reminder_unique_slot",
            )
        ]

    def __str__(self):
        return (
            f"{self.get_reminder_type_display()} reminder for "
            f"review #{self.review_id} -> user_profile #{self.recipient_id}"
        )


class PerformanceReviewHistoryEvent(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    EventType = ReviewEventType

    review = models.ForeignKey(
        PerformanceReview,
        on_delete=models.CASCADE,
        related_name="history_events",
    )
    actor = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_review_history_events",
    )
    event_type = models.CharField(max_length=30, choices=ReviewEventType.choices)
    description = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Performance Review History Event"
        verbose_name_plural = "Performance Review History Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["review", "created_at"]),
            models.Index(fields=["event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()} (review #{self.review_id})"


# ──────────────────────────────────────────
# Onboarding / Offboarding Tracker
# ──────────────────────────────────────────


class ChecklistTemplate(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    Type = ChecklistType
    Role = TaskRole

    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=ChecklistType.choices)
    role_responsible = models.CharField(
        max_length=20, choices=TaskRole.choices, default=TaskRole.HR
    )

    def __str__(self):
        return f"{self.name} ({self.type})"

    class Meta:
        verbose_name = "Checklist Template"
        verbose_name_plural = "Checklist Templates"


class TaskTemplate(models.Model):
    checklist_template = models.ForeignKey(
        ChecklistTemplate,
        on_delete=models.CASCADE,
        related_name="task_templates",
    )
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ["order"]
        verbose_name = "Task Template"
        verbose_name_plural = "Task Templates"


class ChecklistInstance(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    Status = ChecklistInstanceStatus

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="checklist_instances",
    )
    template = models.ForeignKey(
        ChecklistTemplate,
        on_delete=models.CASCADE,
        related_name="instances",
    )
    status = models.CharField(
        max_length=20,
        choices=ChecklistInstanceStatus.choices,
        default=ChecklistInstanceStatus.IN_PROGRESS,
    )
    due_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_checklist_instances",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee} - {self.template.name} ({self.status})"

    def get_assignee_for_role(self, role_responsible: str):
        """Return the appropriate assignee for a task role."""
        if role_responsible == ChecklistTemplate.Role.MANAGER:
            return self.employee.managers.first()

        if role_responsible in {ChecklistTemplate.Role.HR, ChecklistTemplate.Role.IT}:
            return (
                UserProfile.objects.filter(
                    role__name__iexact=role_responsible,
                    is_active=True,
                )
                .order_by("created_at", "id")
                .first()
            )

        return None

    def create_tasks_from_template(self):
        """Create checklist tasks from the associated checklist template."""
        assignee = self.get_assignee_for_role(self.template.role_responsible)
        if assignee is None:
            assignee = self.created_by
        for task_template in self.template.task_templates.all():
            ChecklistTask.objects.create(
                checklist_instance=self,
                task_template=task_template,
                title=task_template.title,
                assigned_to=assignee,
                due_date=self.due_date,
            )

    class Meta:
        verbose_name = "Checklist Instance"
        verbose_name_plural = "Checklist Instances"


class ChecklistTask(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    Status = ChecklistTaskStatus

    checklist_instance = models.ForeignKey(
        ChecklistInstance,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    task_template = models.ForeignKey(
        TaskTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklist_tasks",
    )
    title = models.CharField(max_length=200)
    status = models.CharField(
        max_length=20,
        choices=ChecklistTaskStatus.choices,
        default=ChecklistTaskStatus.TODO,
    )
    assigned_to = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} - {self.status}"

    class Meta:
        verbose_name = "Checklist Task"
        verbose_name_plural = "Checklist Tasks"


# ──────────────────────────────────────────
# Training & Development Management
# ──────────────────────────────────────────


def certificate_upload_to(instance: "Certificate", filename: str) -> str:
    """
    Store certificates under training_certificates/{first}-{last}-{profile_id}/{year}/{month}/{filename}
    Follows the same pattern as employee_document_upload_to for consistency.
    """
    profile = instance.employee
    user = profile.user

    first_raw = (user.first_name or "").strip()
    last_raw = (user.last_name or "").strip()
    if not first_raw and not last_raw and profile.full_name:
        parts = profile.full_name.strip().split(None, 1)
        first_raw = parts[0] if parts else ""
        last_raw = parts[1] if len(parts) > 1 else ""

    first = slugify(first_raw) or "user"
    last = slugify(last_raw) or "user"

    path = Path(filename)
    ext = path.suffix.lower() or ".pdf"
    stem = slugify(path.stem) or "certificate"

    now = timezone.now()
    return (
        f"training_certificates/{first}-{last}-{profile.pk}/"
        f"{now:%Y}/{now:%m}/{stem}{ext}"
    )


class TrainingEntry(models.Model):
    """
    Tracks individual training, courses, conferences, and certifications
    completed by employees.
    """

    class TrainingType(models.TextChoices):
        COURSE = "course", "Course"
        CONFERENCE = "conference", "Conference"
        WORKSHOP = "workshop", "Workshop"
        WEBINAR = "webinar", "Webinar"
        CERTIFICATION = "certification", "Certification"
        OTHER = "other", "Other"

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="training_entries",
        help_text="Employee who participated in training",
    )
    course_title = models.CharField(
        max_length=255, help_text="Name or title of the training/course"
    )
    provider = models.CharField(
        max_length=255, help_text="Training provider or organization"
    )
    training_date = models.DateField(help_text="Date when training occurred")
    completed_at = models.DateTimeField(
        null=True, blank=True, help_text="When employee completed the training"
    )
    training_type = models.CharField(
        max_length=20,
        choices=TrainingType.choices,
        default=TrainingType.COURSE,
        help_text="Type of training activity",
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Cost of training (for budget tracking)",
    )
    description = models.TextField(
        blank=True, null=True, help_text="Additional notes or description"
    )
    certificate_link = models.URLField(
        blank=True, null=True, help_text="HTTPS URL link to the training certificate"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Training Entry"
        verbose_name_plural = "Training Entries"
        ordering = ["-training_date"]
        indexes = [
            models.Index(fields=["employee", "-training_date"]),
            models.Index(fields=["training_type"]),
        ]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.course_title} ({self.training_date})"


class Certificate(models.Model):
    """
    Stores certificates earned by employees through training or certification programs.
    """

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="certificates",
        help_text="Employee who earned the certificate",
    )
    title = models.CharField(max_length=255, help_text="Certificate title/name")
    file = models.FileField(
        upload_to=certificate_upload_to,
        help_text="Certificate file (PDF, image, etc.)",
    )
    issued_date = models.DateField(help_text="Date when certificate was issued")
    expiration_date = models.DateField(
        null=True, blank=True, help_text="Certificate expiration date (if applicable)"
    )
    issuer = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Organization or body that issued the certificate",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Certificate"
        verbose_name_plural = "Certificates"
        ordering = ["-issued_date"]
        indexes = [
            models.Index(fields=["employee", "-issued_date"]),
            models.Index(fields=["expiration_date"]),
        ]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.title}"

    @property
    def is_expired(self):
        """Check if certificate has expired."""
        if not self.expiration_date:
            return False
        return self.expiration_date < timezone.now().date()


class PeerSession(models.Model):
    """
    Records peer-to-peer learning sessions between employees.
    Tracks knowledge sharing activities and optional links to incentive programs.
    """

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="peer_sessions",
        help_text="Employee who participated in the peer session",
    )
    topic = models.CharField(
        max_length=255, help_text="Topic or skill shared in the session"
    )
    session_date = models.DateField(help_text="Date when the peer session occurred")
    incentive_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Reference to associated incentive (FK when model exists)",
    )
    duration_minutes = models.PositiveIntegerField(
        null=True, blank=True, help_text="Duration of the session in minutes"
    )
    description = models.TextField(
        blank=True, null=True, help_text="Additional details about the session"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Peer Session"
        verbose_name_plural = "Peer Sessions"
        ordering = ["-session_date"]
        indexes = [
            models.Index(fields=["employee", "-session_date"]),
        ]

    def __str__(self):
        return (
            f"{self.employee.user.get_full_name()} - {self.topic} ({self.session_date})"
        )


class TrainingBudget(models.Model):
    """
    Manages training budget allocation and spending per employee per fiscal year.
    """

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="training_budgets",
        help_text="Employee assigned the budget",
    )
    fiscal_year = models.PositiveIntegerField(
        help_text="Fiscal year for which budget is allocated"
    )
    allocated_budget = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Total budget allocated for training this fiscal year",
    )
    used_budget = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Budget amount spent on training so far",
    )
    threshold_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the 80% threshold alert was last sent",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Training Budget"
        verbose_name_plural = "Training Budgets"
        unique_together = ("employee", "fiscal_year")
        ordering = ["-fiscal_year", "employee"]
        indexes = [
            models.Index(fields=["employee", "-fiscal_year"]),
        ]

    def __str__(self):
        remaining = self.allocated_budget - self.used_budget
        return f"{self.employee.user.get_full_name()} - {self.fiscal_year} (${remaining:.2f} remaining)"

    @property
    def remaining_budget(self):
        """Calculate remaining budget."""
        return max(Decimal("0.00"), self.allocated_budget - self.used_budget)

    @property
    def budget_percentage_used(self):
        """Calculate percentage of budget used."""
        if self.allocated_budget == 0:
            return 0
        return (self.used_budget / self.allocated_budget) * 100

    def add_usage(self, amount):
        """Safely add budget usage."""
        if amount < 0:
            raise ValueError("Budget usage amount cannot be negative")
        self.used_budget += amount
        self.save(update_fields=["used_budget"])


class ConferenceCourseRegistration(models.Model):
    """
    Tracks an employee's registration for a conference or course, including
    attendance status and any related notes. Distinct from ``TrainingEntry``,
    which records completed training history.
    """

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="conference_course_registrations",
        help_text="Employee registered for the conference or course",
    )
    name = models.CharField(
        max_length=255, help_text="Name of the conference or course"
    )
    date = models.DateField(help_text="Scheduled date of the conference or course")
    status = models.CharField(
        max_length=20,
        choices=ConferenceCourseRegistrationStatus.choices,
        default=ConferenceCourseRegistrationStatus.REGISTERED,
        help_text="Attendance status",
    )
    notes = models.TextField(blank=True, default="", help_text="Additional notes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conference / Course Registration"
        verbose_name_plural = "Conference / Course Registrations"
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["employee", "-date"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.name} ({self.date})"


@receiver(post_save, sender=ChecklistInstance)
def create_tasks_for_checklist_instance(sender, instance, created, **kwargs):
    if created:
        instance.create_tasks_from_template()


# ──────────────────────────────────────────────────────────────────────────────
# Document Templates
# ──────────────────────────────────────────────────────────────────────────────


class DocumentTemplate(models.Model):
    """
    Reusable document template with dynamic field definitions.

    System templates (is_system_template=True) are read-only for all users.
    PRIVATE templates are only visible to their creator.
    SHARED templates are visible to the whole organisation but editable only
    by the creator or an admin.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=20,
        choices=TemplateCategory.choices,
        default=TemplateCategory.OTHER,
    )
    content = models.TextField(blank=True, default="")
    visibility = models.CharField(
        max_length=10,
        choices=TemplateVisibility.choices,
        default=TemplateVisibility.PRIVATE,
    )
    status = models.CharField(
        max_length=10,
        choices=TemplateStatus.choices,
        default=TemplateStatus.DRAFT,
    )
    is_system_template = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Document Template"
        verbose_name_plural = "Document Templates"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["visibility"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["is_system_template"]),
            models.Index(fields=["created_by"]),
        ]

    def __str__(self):
        return self.name


class TemplateField(models.Model):
    """
    Dynamic field definition attached to a DocumentTemplate.

    Each field_key corresponds to a {{field_key}} placeholder in the template
    content that will be replaced at document generation time.
    """

    template = models.ForeignKey(
        DocumentTemplate,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    label = models.CharField(max_length=255)
    field_key = models.CharField(max_length=100)
    field_type = models.CharField(
        max_length=15,
        choices=TemplateFieldType.choices,
        default=TemplateFieldType.TEXT,
    )
    placeholder = models.CharField(max_length=255, blank=True, default="")
    default_value = models.CharField(max_length=500, blank=True, default="")
    is_required = models.BooleanField(default=False)
    options = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Template Field"
        verbose_name_plural = "Template Fields"
        ordering = ["order", "id"]
        unique_together = ("template", "field_key")

    def __str__(self):
        return f"{self.template.name} — {self.label}"


class UserTemplateSnippet(models.Model):
    user_profile = models.ForeignKey(
        "UserProfile",
        on_delete=models.CASCADE,
        related_name="template_snippets",
    )
    label = models.CharField(max_length=255)
    html = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Template Snippet"
        verbose_name_plural = "User Template Snippets"
        ordering = ["sort_order", "-updated_at"]

    def __str__(self):
        return f"{self.user_profile_id}: {self.label}"


class TemplateGeneratedDocument(models.Model):
    """
    Document produced by instantiating a DocumentTemplate with user-supplied
    field values.  The resolved_content stores the fully substituted JSON
    content; field_values keeps the raw inputs for auditing.
    """

    name = models.CharField(max_length=255)
    source_template = models.ForeignKey(
        DocumentTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_documents",
    )
    resolved_content = models.TextField(blank=True, default="")
    field_values = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Generated Document"
        verbose_name_plural = "Generated Documents"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


# ──────────────────────────────────────────
# In-app notifications
# ──────────────────────────────────────────


class Announcement(models.Model):
    """Company announcement with rich text content stored as HTML."""

    class Type(models.TextChoices):
        GENERAL = "general", "General"
        NEWS = "news", "News"
        CELEBRATION = "celebration", "Celebration"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=255)
    body = models.TextField(help_text="Rich text HTML content")
    author = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        related_name="authored_announcements",
    )
    published_at = models.DateTimeField(default=timezone.now, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    notifications_sent_at = models.DateTimeField(null=True, blank=True)
    notifications_sent_count = models.PositiveIntegerField(default=0)
    email_notifications_sent_at = models.DateTimeField(null=True, blank=True)
    email_notifications_sent_count = models.PositiveIntegerField(default=0)
    type = models.CharField(
        max_length=32,
        choices=Type.choices,
        blank=True,
        default="",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["type", "published_at"]),
            models.Index(fields=["author", "published_at"]),
            models.Index(fields=["scheduled_at", "published_at"]),
        ]

    def __str__(self):
        return self.title


class AnnouncementSettings(models.Model):
    """Admin-controlled announcement automation flags."""

    auto_employee_intro_on_registration = models.BooleanField(default=True)
    auto_employee_intro_on_employee_create = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Announcement Settings"
        verbose_name_plural = "Announcement Settings"

    def __str__(self):
        return "Announcement Settings"

    @classmethod
    def load(cls):
        settings_obj, _ = cls.objects.get_or_create(pk=1)
        return settings_obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class DiscordAnnouncementChannel(models.Model):
    """Discord webhook mapping for one announcement type/channel."""

    announcement_type = models.CharField(
        max_length=32, choices=Announcement.Type.choices
    )
    channel_name = models.CharField(max_length=255)
    webhook_url_encrypted = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Discord Announcement Channel"
        verbose_name_plural = "Discord Announcement Channels"
        ordering = ["announcement_type", "channel_name"]
        indexes = [
            models.Index(fields=["announcement_type", "enabled"]),
        ]

    def set_webhook_url(self, webhook_url: str):
        self.webhook_url_encrypted = encrypt_secret(webhook_url)

    def get_webhook_url(self) -> str:
        return decrypt_secret(self.webhook_url_encrypted)

    @property
    def has_webhook_url(self) -> bool:
        return bool(self.webhook_url_encrypted)

    def __str__(self):
        return f"{self.get_announcement_type_display()} -> {self.channel_name}"


class DiscordAnnouncementDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="discord_deliveries",
    )
    discord_channel = models.ForeignKey(
        DiscordAnnouncementChannel,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    discord_message_id = models.CharField(max_length=128, blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Discord Announcement Delivery"
        verbose_name_plural = "Discord Announcement Deliveries"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "discord_channel"],
                name="unique_discord_delivery_per_announcement_channel",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "attempt_count"]),
            models.Index(fields=["announcement", "status"]),
        ]

    def __str__(self):
        return f"{self.announcement_id} -> {self.discord_channel_id} ({self.status})"


class AnnouncementReaction(models.Model):
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        "UserProfile",
        on_delete=models.CASCADE,
        related_name="announcement_reactions",
    )
    reaction_type = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Announcement Reaction"
        verbose_name_plural = "Announcement Reactions"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "user", "reaction_type"],
                name="unique_announcement_user_reaction_type",
            ),
        ]
        indexes = [
            models.Index(fields=["announcement", "reaction_type"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user} reacted {self.reaction_type} to {self.announcement}"


class AnnouncementComment(models.Model):
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        related_name="announcement_comments",
    )
    body = models.TextField()
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Announcement Comment"
        verbose_name_plural = "Announcement Comments"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["announcement", "created_at"]),
            models.Index(fields=["author", "created_at"]),
        ]

    def __str__(self):
        return f"{self.author} commented on {self.announcement}"


class CelebrationEvent(models.Model):
    class Type(models.TextChoices):
        BIRTHDAY = "birthday", "Birthday"
        ANNIVERSARY = "anniversary", "Anniversary"
        CUSTOM = "custom", "Custom"

    title = models.CharField(max_length=255)
    event_type = models.CharField(max_length=32, choices=Type.choices)
    employee = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="celebration_events",
    )
    event_date = models.DateField(db_index=True)
    recurs_annually = models.BooleanField(default=True)
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_celebration_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Celebration Event"
        verbose_name_plural = "Celebration Events"
        ordering = ["event_date", "title"]
        indexes = [
            models.Index(fields=["event_type", "event_date"]),
            models.Index(fields=["employee", "event_type"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.event_date})"


class Notification(models.Model):
    """A user-targeted in-app notification (bell icon list)."""

    class Type(models.TextChoices):
        INFO = "info", "Info"
        SUCCESS = "success", "Success"
        WARNING = "warning", "Warning"
        ALERT = "alert", "Alert"

    class Module(models.TextChoices):
        DOCUMENTS = "documents", "Documents"
        VACATIONS = "vacations", "Vacations"
        REVIEWS = "reviews", "Reviews"
        ONBOARDING = "onboarding", "Onboarding"
        ANNOUNCEMENTS = "announcements", "Announcements"
        TRAINING = "training", "Training"
        GENERAL = "general", "General"

    recipient = models.ForeignKey(
        "UserProfile",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    module = models.CharField(
        max_length=32, choices=Module.choices, default=Module.GENERAL
    )
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.INFO)
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True, default="")
    link = models.CharField(max_length=512, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"Notification({self.recipient_id}, {self.title})"


# ──────────────────────────────────────────────────────────────────────────────
# Internal Mobility & Promotions
# ──────────────────────────────────────────────────────────────────────────────


class JobListing(models.Model):
    """Internal job opening that employees can apply for."""

    title = models.CharField(max_length=255, help_text="Title of the internal role")
    description = models.TextField(
        blank=True, default="", help_text="Detailed description of the role"
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_listings",
        help_text="Department offering the position",
    )
    open_at = models.DateTimeField(help_text="When the listing opens for applications")
    close_at = models.DateTimeField(
        help_text="When the listing closes for applications"
    )
    status = models.CharField(
        max_length=20,
        choices=JobListingStatus.choices,
        default=JobListingStatus.DRAFT,
        help_text="Current status of the listing",
    )
    created_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_job_listings",
        help_text="User who created the listing",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Job Listing"
        verbose_name_plural = "Job Listings"
        ordering = ["-open_at"]
        indexes = [
            models.Index(fields=["status", "-open_at"]),
            models.Index(fields=["department", "status"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.status})"


class Application(models.Model):
    """An employee's application to a JobListing."""

    listing = models.ForeignKey(
        JobListing,
        on_delete=models.CASCADE,
        related_name="applications",
        help_text="The job listing being applied to",
    )
    applicant = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="job_applications",
        help_text="Employee submitting the application",
    )
    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SUBMITTED,
        help_text="Current status of the application",
    )
    applied_at = models.DateTimeField(
        default=timezone.now, help_text="When the application was submitted"
    )
    cover_note = models.TextField(
        blank=True, default="", help_text="Optional note from the applicant"
    )
    decision_note = models.TextField(
        blank=True,
        default="",
        help_text="Reviewer note captured when the application reaches a terminal state",
    )
    decided_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_applications",
        help_text="Reviewer who set the current terminal status",
    )
    decided_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the terminal decision (accepted/rejected/withdrawn) was recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Job Application"
        verbose_name_plural = "Job Applications"
        ordering = ["-applied_at"]
        unique_together = ("listing", "applicant")
        indexes = [
            models.Index(fields=["listing", "status"]),
            models.Index(fields=["applicant", "-applied_at"]),
        ]

    def __str__(self):
        return f"{self.applicant} → {self.listing} ({self.status})"


class PromotionHistory(models.Model):
    """Records an employee's promotion / role change with an optional CPF snapshot."""

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="promotion_history",
        help_text="Employee being promoted",
    )
    previous_role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotion_history_previous",
        help_text="Role held before the promotion",
    )
    new_role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotion_history_new",
        help_text="Role held after the promotion",
    )
    date = models.DateField(help_text="Effective date of the promotion")
    notes = models.TextField(
        blank=True, default="", help_text="Additional context about the promotion"
    )
    # Optional CPF snapshot — captures the employee's CPF level at promotion time.
    previous_cpf_level = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Snapshot of CPF level prior to promotion",
    )
    new_cpf_level = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Snapshot of CPF level after promotion",
    )
    related_listing = models.ForeignKey(
        JobListing,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotions",
        help_text="Internal listing that led to the promotion, if any",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Promotion History"
        verbose_name_plural = "Promotion Histories"
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["employee", "-date"]),
        ]

    def __str__(self):
        return f"{self.employee} promoted on {self.date}"


# ──────────────────────────────────────────
# Feedback & Surveys
# ──────────────────────────────────────────


class Survey(models.Model):
    """A feedback or pulse survey collected from employees."""

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    is_anonymous = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=SurveyStatus.choices,
        default=SurveyStatus.DRAFT,
        help_text=(
            "Lifecycle state. Closed surveys keep responses but reject new ones."
        ),
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional cutoff. Submissions are rejected after this date.",
    )
    forbidden_users = models.ManyToManyField(
        "UserProfile",
        blank=True,
        related_name="forbidden_surveys",
        help_text="Users explicitly blocked from seeing or submitting this survey.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="surveys_created",
    )

    class Meta:
        verbose_name = "Survey"
        verbose_name_plural = "Surveys"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Question(models.Model):
    """A single question that belongs to a Survey."""

    survey = models.ForeignKey(
        Survey, on_delete=models.CASCADE, related_name="questions"
    )
    text = models.TextField()
    type = models.CharField(
        max_length=16, choices=QuestionType.choices, default=QuestionType.TEXT
    )
    order = models.PositiveIntegerField(default=0)
    options = models.JSONField(
        default=list,
        blank=True,
        help_text="List of option strings for choice questions; ignored otherwise.",
    )
    required = models.BooleanField(
        default=True,
        help_text="If true, respondents must provide a non-empty answer.",
    )

    class Meta:
        verbose_name = "Question"
        verbose_name_plural = "Questions"
        ordering = ["survey_id", "order", "id"]

    def __str__(self):
        return f"{self.survey_id} · {self.text[:50]}"


class Response(models.Model):
    """A single submission of a survey by a respondent (or anonymous)."""

    survey = models.ForeignKey(
        Survey, on_delete=models.CASCADE, related_name="responses"
    )
    respondent = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="survey_responses",
        help_text="Null if the survey is anonymous.",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Survey Response"
        verbose_name_plural = "Survey Responses"
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["survey", "-submitted_at"]),
        ]

    def __str__(self):
        who = self.respondent_id if self.respondent_id else "anonymous"
        return f"Response({self.survey_id}, {who})"

    def save(self, *args, **kwargs):
        # Hard guarantee: anonymous surveys never persist a respondent FK.
        if self.survey_id and self.survey.is_anonymous:
            self.respondent = None
        super().save(*args, **kwargs)


class Answer(models.Model):
    """An answer to a single Question within a Response."""

    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="answers"
    )
    response = models.ForeignKey(
        Response, on_delete=models.CASCADE, related_name="answers"
    )
    value = models.TextField(
        blank=True,
        default="",
        help_text="Free text for text questions, selected option for choice, numeric string for scale.",
    )

    class Meta:
        verbose_name = "Answer"
        verbose_name_plural = "Answers"
        constraints = [
            models.UniqueConstraint(
                fields=["question", "response"],
                name="uniq_answer_per_question_response",
            ),
        ]
        indexes = [
            models.Index(fields=["question"]),
            models.Index(fields=["response"]),
        ]

    def __str__(self):
        return f"Answer(q={self.question_id}, r={self.response_id})"


class Suggestion(models.Model):
    """A standalone suggestion submitted through the suggestion box."""

    employee = models.ForeignKey(
        "UserProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suggestions",
        help_text="Null if submitted anonymously.",
    )
    category = models.CharField(max_length=64, blank=True, default="")
    text = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=SuggestionStatus.choices,
        default=SuggestionStatus.NEW,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Suggestion"
        verbose_name_plural = "Suggestions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Suggestion({self.category or 'general'}, {self.status})"


class CPFLevelChange(models.Model):
    """A single CPF (Career Progression Framework) level change for an employee.

    Records the longitudinal CPF history used to render career-progression
    timelines. Each row may optionally link to the performance review or
    promotion that drove the change.
    """

    employee = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="cpf_level_changes",
        help_text="Employee whose CPF level changed",
    )
    previous_level = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="CPF level before this change",
    )
    new_level = models.CharField(
        max_length=100,
        help_text="CPF level after this change",
    )
    effective_date = models.DateField(help_text="Date the new level took effect")
    source = models.CharField(
        max_length=20,
        choices=CPFChangeSource.choices,
        default=CPFChangeSource.MANUAL,
        help_text="What triggered the level change",
    )
    cpf_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Optional CPF score (0-100) recorded at the time of change",
    )
    performance_review = models.ForeignKey(
        PerformanceReview,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cpf_level_changes",
        help_text="Review whose outcome drove this change, if any",
    )
    promotion = models.ForeignKey(
        PromotionHistory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cpf_level_changes",
        help_text="Promotion record linked to this change, if any",
    )
    notes = models.TextField(blank=True, default="")
    recorded_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_cpf_level_changes",
        help_text="User who recorded the change",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CPF Level Change"
        verbose_name_plural = "CPF Level Changes"
        ordering = ["-effective_date", "-created_at"]
        indexes = [
            models.Index(fields=["employee", "-effective_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(cpf_score__isnull=True)
                    | models.Q(cpf_score__gte=0, cpf_score__lte=100)
                ),
                name="cpf_level_change_score_between_0_100",
            ),
        ]

    def __str__(self):
        return f"{self.employee} → {self.new_level} ({self.effective_date})"


class BonusRecord(models.Model):
    BonusType = BonusType

    user_profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="bonus_records"
    )
    bonus_type = models.CharField(max_length=20, choices=BonusType.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="BAM")
    effective_date = models.DateField()
    reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bonuses_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Bonus Record"
        verbose_name_plural = "Bonus Records"
        ordering = ["-effective_date"]
        indexes = [
            models.Index(fields=["user_profile", "-effective_date"]),
            models.Index(fields=["bonus_type"]),
        ]

    def __str__(self):
        return f"{self.user_profile.user.username}: {self.amount} {self.currency} ({self.bonus_type} @ {self.effective_date})"


class PayrollSnapshot(models.Model):
    snapshot_date = models.DateField(unique=True)
    total_monthly = models.DecimalField(max_digits=14, decimal_places=2)
    avg_salary = models.DecimalField(max_digits=12, decimal_places=2)
    median_salary = models.DecimalField(max_digits=12, decimal_places=2)
    headcount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="BAM")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Payroll Snapshot"
        verbose_name_plural = "Payroll Snapshots"
        ordering = ["-snapshot_date"]

    def __str__(self):
        return f"PayrollSnapshot {self.snapshot_date} total={self.total_monthly}"


class CompensationPolicy(models.Model):
    """One NET-salary policy per CPF level. Unique on cpf_level."""

    cpf_level = models.CharField(max_length=100, unique=True)
    net_monthly = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="BAM")
    effective_date = models.DateField()
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="comp_policies_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compensation Policy"
        verbose_name_plural = "Compensation Policies"
        ordering = ["cpf_level"]

    def __str__(self):
        return f"{self.cpf_level}: NET {self.net_monthly} {self.currency}"


class BenefitCatalog(models.Model):
    """Global benefit catalog. Every active entry applies to every employee."""

    Type = BenefitType

    benefit_type = models.CharField(max_length=20, choices=BenefitType.choices)
    name = models.CharField(max_length=120)
    monthly_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="BAM")
    is_active = models.BooleanField(default=True)
    effective_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="benefit_catalog_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Benefit (Catalog)"
        verbose_name_plural = "Benefits Catalog"
        ordering = ["benefit_type", "name"]
        indexes = [
            models.Index(fields=["benefit_type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return (
            f"{self.name} ({self.benefit_type}) {self.monthly_amount} {self.currency}"
        )
