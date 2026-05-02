import sys
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from .avatar_utils import generate_initials_avatar_png, get_initials
from .enums import (
    ActionPointStatus,
    AssetCondition,
    AssetStatus,
    ChecklistInstanceStatus,
    ChecklistTaskStatus,
    ChecklistType,
    DocumentAccessRole,
    DocumentSignatureStatus,
    DocumentSignerStatus,
    EmployeeDocumentProviderType,
    EmployeeDocumentSourceType,
    EmployeeDocumentType,
    EmploymentStatus,
    LeaveRequestStatus,
    LeaveType,
    LeaveWorkflowStatus,
    ProjectAssignmentStatus,
    ReminderType,
    ReviewEventType,
    ReviewNoteVisibility,
    ReviewOutcome,
    ReviewStatus,
    ReviewType,
    TaskRole,
    TrackedField,
)
from .enums import (
    DocumentCategory as _DocumentCategory,
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

    class Meta:
        ordering = ["name"]
        verbose_name = "CPF Level"
        verbose_name_plural = "CPF Levels"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Department(models.Model):
    """Reference table for organizational departments."""

    name = models.CharField(max_length=100, unique=True)

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

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Project"
        verbose_name_plural = "Projects"


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
    employee_id = models.CharField(max_length=20, unique=True, blank=True, null=True)

    full_name = models.CharField(max_length=150, blank=True, null=True)
    email_address = models.EmailField(max_length=254, blank=True, null=True)

    department = models.CharField(max_length=100, blank=True, null=True)
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

    def save(self, *args, **kwargs):
        self.is_active = self.employment_status == self.EmploymentStatus.ACTIVE
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
    status = models.CharField(
        max_length=20,
        choices=DocumentSignerStatus.choices,
        default=DocumentSignerStatus.NOT_SENT,
    )
    signed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Document Signer"
        verbose_name_plural = "Document Signers"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} ({self.email}) – {self.status}"


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
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=ProjectAssignmentStatus.choices,
        default=ProjectAssignmentStatus.ACTIVE,
    )

    class Meta:
        verbose_name = "Project Assignment"
        verbose_name_plural = "Project Assignments"
        ordering = ["-start_date"]

    def __str__(self):
        status = "current" if not self.end_date else f"until {self.end_date}"
        return f"{self.user_profile.user.username} @ {self.project.name} ({status})"


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


class Asset(models.Model):
    """
    Comprehensive Asset model for equipment management
    """

    asset_id = models.CharField(
        max_length=50, unique=True, help_text="Unique identifier for the asset"
    )
    name = models.CharField(max_length=200, help_text="Asset name/type")
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
        return self.assignments.filter(returned_at__isnull=True).first()

    @property
    def is_available(self):
        """Check if asset is available for assignment"""
        return self.status == AssetStatus.ACTIVE and not self.current_assignment


class Assignment(models.Model):
    """
    Asset assignment to employees
    """

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

    class Meta:
        verbose_name = "Assignment"
        verbose_name_plural = "Assignments"
        ordering = ["-assigned_at"]

    def __str__(self):
        status = (
            "Active" if not self.returned_at else f"Returned {self.returned_at.date()}"
        )
        return f"{self.asset.asset_id} → {self.employee.user.get_full_name() or self.employee.user.username} ({status})"

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
    date = models.DateTimeField(
        auto_now_add=True, help_text="When the replacement occurred"
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
        return (
            f"{self.asset.asset_id} replaced on {self.date.date()} - {self.reason[:50]}"
        )


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
    approver = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_leaves",
        help_text="Manager who approved/rejected",
    )
    approved_date = models.DateTimeField(null=True, blank=True)
    approval_comments = models.TextField(blank=True, help_text="Comments from approver")
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

    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=ChecklistType.choices)

    def __str__(self):
        return f"{self.name} ({self.type})"

    class Meta:
        verbose_name = "Checklist Template"
        verbose_name_plural = "Checklist Templates"


class TaskTemplate(models.Model):
    # Enum alias — defined in core/enums.py; kept here for backward-compat access
    Role = TaskRole

    checklist_template = models.ForeignKey(
        ChecklistTemplate,
        on_delete=models.CASCADE,
        related_name="task_templates",
    )
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)
    role_responsible = models.CharField(
        max_length=20, choices=TaskRole.choices, default=TaskRole.HR
    )

    def __str__(self):
        return f"{self.title} ({self.role_responsible})"

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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee} - {self.template.name} ({self.status})"

    def get_assignee_for_role(self, role_responsible: str):
        """Return the appropriate assignee for a task role."""
        if role_responsible == TaskTemplate.Role.MANAGER:
            return self.employee.managers.first()

        if role_responsible in {TaskTemplate.Role.HR, TaskTemplate.Role.IT}:
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
        for task_template in self.template.task_templates.all():
            ChecklistTask.objects.create(
                checklist_instance=self,
                task_template=task_template,
                title=task_template.title,
                assigned_to=self.get_assignee_for_role(task_template.role_responsible),
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


@receiver(post_save, sender=ChecklistInstance)
def create_tasks_for_checklist_instance(sender, instance, created, **kwargs):
    if created:
        instance.create_tasks_from_template()
