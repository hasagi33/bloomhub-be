import sys
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.text import slugify

from .avatar_utils import generate_initials_avatar_png, get_initials


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
    class EmploymentStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

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


class DocumentType(models.TextChoices):
    CV = "cv", "CV"
    OTHER = "other", "Other"


class EmployeeDocument(models.Model):
    user_profile = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(
        max_length=20, choices=DocumentType.choices, default=DocumentType.CV
    )
    file = models.FileField(upload_to="employee_documents/%Y/%m/%d/")
    version = models.PositiveIntegerField(default=1)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user_profile", "doc_type", "version")
        verbose_name = "Employee Document"
        verbose_name_plural = "Employee Documents"

    def __str__(self):
        return f"{self.user_profile.user.username} - {self.doc_type} (v{self.version})"


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
        choices=[
            ("active", "Active"),
            ("completed", "Completed"),
            ("on_hold", "On Hold"),
        ],
        default="active",
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


class AssetStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    LOST = "lost", "Lost"
    RETURNED = "returned", "Returned"
    DAMAGED = "damaged", "Damaged"


class AssetCondition(models.TextChoices):
    EXCELLENT = "excellent", "Excellent"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair"
    POOR = "poor", "Poor"
    DAMAGED = "damaged", "Damaged"


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

    class LeaveType(models.TextChoices):
        VACATION = "vacation", "Vacation"
        SICK = "sick", "Sick Leave"
        WFH = "wfh", "Work From Home"
        PERSONAL = "personal", "Personal"
        MATERNITY = "maternity", "Maternity"
        PATERNITY = "paternity", "Paternity"
        BEREAVEMENT = "bereavement", "Bereavement"
        UNPAID = "unpaid", "Unpaid Leave"

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
    leave_type = models.CharField(max_length=20, choices=LeavePolicy.LeaveType.choices)
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

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE, related_name="leave_requests"
    )
    leave_type = models.CharField(max_length=20, choices=LeavePolicy.LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
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

    class WorkflowStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_REVIEW = "in_review", "In Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

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
        max_length=20, choices=WorkflowStatus.choices, default=WorkflowStatus.PENDING
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
    leave_type = models.CharField(max_length=20, choices=LeavePolicy.LeaveType.choices)
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
# Onboarding / Offboarding Tracker
# ──────────────────────────────────────────


class ChecklistTemplate(models.Model):
    class Type(models.TextChoices):
        ONBOARDING = "onboarding", "Onboarding"
        OFFBOARDING = "offboarding", "Offboarding"

    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=Type.choices)

    def __str__(self):
        return f"{self.name} ({self.type})"

    class Meta:
        verbose_name = "Checklist Template"
        verbose_name_plural = "Checklist Templates"


class TaskTemplate(models.Model):
    class Role(models.TextChoices):
        HR = "HR", "HR"
        IT = "IT", "IT"
        MANAGER = "Manager", "Manager"

    checklist_template = models.ForeignKey(
        ChecklistTemplate,
        on_delete=models.CASCADE,
        related_name="task_templates",
    )
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)
    role_responsible = models.CharField(
        max_length=20, choices=Role.choices, default=Role.HR
    )

    def __str__(self):
        return f"{self.title} ({self.role_responsible})"

    class Meta:
        ordering = ["order"]
        verbose_name = "Task Template"
        verbose_name_plural = "Task Templates"


class ChecklistInstance(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

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
        max_length=20, choices=Status.choices, default=Status.IN_PROGRESS
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee} - {self.template.name} ({self.status})"

    class Meta:
        verbose_name = "Checklist Instance"
        verbose_name_plural = "Checklist Instances"


class ChecklistTask(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

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
        max_length=20, choices=Status.choices, default=Status.TODO
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
