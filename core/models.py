from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Role(models.Model):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    HR_ADMIN = "hr_admin"
    SUPER_ADMIN = "super_admin"

    ROLE_CHOICES = [
        (EMPLOYEE, "Employee"),
        (MANAGER, "Manager"),
        (HR_ADMIN, "HR Admin"),
        (SUPER_ADMIN, "Super Admin"),
    ]

    name = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.get_name_display()

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
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True)
    employee_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    department = models.CharField(max_length=100, blank=True, null=True)
    hire_date = models.DateField(blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True, null=True)
    birthday = models.DateField(blank=True, null=True)
    career_level = models.CharField(max_length=100, blank=True, null=True)
    cpf_level = models.CharField(max_length=100, blank=True, null=True)
    tech_tags = models.ManyToManyField(TechnologyTag, blank=True, related_name="users")

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.role.name if self.role else 'No Role'}"

    @property
    def current_salary(self):
        salary = self.salary_records.order_by("-effective_date").first()
        return salary.amount if salary else None

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"


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
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()
