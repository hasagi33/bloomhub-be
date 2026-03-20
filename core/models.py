import sys

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
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


class Project(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, null=True)

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
    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_reports",
    )
    employee_id = models.CharField(max_length=20, unique=True, blank=True, null=True)

    full_name = models.CharField(max_length=150, blank=True, null=True)
    email_address = models.EmailField(max_length=254, blank=True, null=True)

    department = models.CharField(max_length=100, blank=True, null=True)
    start_date = models.DateField(blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True, null=True)
    birthday = models.DateField(blank=True, null=True)
    career_level = models.CharField(max_length=100, blank=True, null=True)
    cpf_level = models.CharField(max_length=100, blank=True, null=True)
    tech_tags = models.ManyToManyField(TechnologyTag, blank=True, related_name="users")

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

    permissions = models.CharField(
        max_length=255, default=""
    )  # Bitmap stored as binary string

    def __str__(self):
        display_name = self.full_name or self.user.get_full_name() or self.user.username
        return f"{display_name} - " f"{self.role.name if self.role else 'No Role'}"

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
        profile.save(update_fields=["full_name", "email_address"])

        if not profile.avatar:
            try:
                # Prevent avatar generation locally (DEBUG=True) or during test runs
                if (
                    getattr(settings, "DEBUG", False)
                    or "test" in sys.argv
                    or any("pytest" in arg for arg in sys.argv)
                ):
                    return

                initials = get_initials(profile.full_name, profile.user.username)
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
