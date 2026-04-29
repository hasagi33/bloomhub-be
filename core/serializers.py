from typing import Any

from django.contrib.auth.models import User
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.constants import (
    EMPLOYEE_PROFILE_FIELDS,
    EMPLOYEE_PROFILE_READ_ONLY_FIELDS,
    REGISTER_EXTRA_KWARGS,
    REGISTER_FIELDS,
)
from core.models import (
    Asset,
    Assignment,
    ChecklistTask,
    ChecklistTemplate,
    EmployeeDocument,
    EmployeeProfileChangeHistory,
    LeaveAdjustment,
    LeaveApprovalWorkflow,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    PerformanceReview,
    PerformanceReviewActionPoint,
    PerformanceReviewAttachment,
    PerformanceReviewHistoryEvent,
    PerformanceReviewNote,
    PerformanceReviewReminder,
    Project,
    ProjectAssignment,
    ReplacementLog,
    SalaryRecord,
    TaskTemplate,
    TechnologyTag,
    UserProfile,
)
from core.services.profile_change_history import (
    log_employee_profile_change,
    manager_payload_from_ids,
    normalize_enum_like,
    normalize_iso_date,
    normalize_manager_ids,
    normalize_trimmed_string,
    role_value,
)
from core.utils import (
    apply_profile_updates_and_save,
    download_and_save_avatar,
    generate_secure_password,
    generate_unique_username,
    get_role_permissions_bitmap,
    verify_google_id_token,
)


class GoogleExchangeSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)

    def validate_id_token(self, value):
        try:
            payload = verify_google_id_token(value)
            return payload
        except Exception as e:
            raise serializers.ValidationError(f"Invalid Google token: {str(e)}")


class Base64ImageField(serializers.ImageField):
    """
    A custom serializer field to handle base64-encoded image data.
    """

    def to_internal_value(self, data):
        import base64
        import uuid

        from django.core.files.base import ContentFile

        if isinstance(data, str):
            if "base64," in data:
                # Remove header if present (e.g., data:image/png;base64,)
                data = data.split("base64,")[1]

            try:
                decoded_file = base64.b64decode(data)
            except Exception:
                self.fail("invalid_image")

            file_name = str(uuid.uuid4())[:12]
            file_extension = "png"  # Default to png
            complete_file_name = f"{file_name}.{file_extension}"

            data = ContentFile(decoded_file, name=complete_file_name)

        return super().to_internal_value(data)


class UserSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    career_level = serializers.CharField(source="profile.career_level", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "avatar_url",
            "career_level",
            "is_staff",
            "is_superuser",
        ]

    def get_avatar_url(self, obj: User) -> str | None:
        try:
            profile = obj.profile
        except Exception:
            return None
        # Prefer the direct URL field (set by Google OAuth, etc.)
        if getattr(profile, "avatar_url", None):
            return profile.avatar_url
        # Fall back to the ImageField presigned URL
        if not getattr(profile, "avatar", None):
            return None
        try:
            return profile.avatar.url
        except Exception:
            return None


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    password_confirm = serializers.CharField(write_only=True, required=True)
    avatar = Base64ImageField(required=False, allow_null=True)
    avatar_url = serializers.URLField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = REGISTER_FIELDS
        extra_kwargs = REGISTER_EXTRA_KWARGS

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password": "Password fields didn't match."}
            )
        return attrs

    def create(self, validated_data):
        avatar_file = validated_data.pop("avatar", None)
        avatar_url = validated_data.pop("avatar_url", None)
        validated_data.pop("password_confirm")
        user = User.objects.create_user(**validated_data)

        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "full_name": user.get_full_name() or user.username,
                "email_address": user.email,
            },
        )

        try:
            profile = user.profile
            if avatar_file:
                profile.avatar.save(
                    "avatar.png",
                    avatar_file,
                    save=True,
                )
            elif avatar_url:
                download_and_save_avatar(profile, avatar_url)
        except Exception:
            # Keep registration functional; avatar can be generated later.
            pass

        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class AvatarUploadSerializer(serializers.Serializer):
    avatar = serializers.ImageField(required=True)

    def validate_avatar(self, value):
        max_size_mb = 5
        if value.size > max_size_mb * 1024 * 1024:
            raise serializers.ValidationError(
                f"Avatar image must be under {max_size_mb} MB."
            )
        return value


class TokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    access = serializers.CharField()
    user = UserSerializer()


class APIRootResponseSerializer(serializers.Serializer):
    """Response shape for GET /api/."""

    message = serializers.CharField()
    endpoints = serializers.JSONField(
        help_text="Nested map of endpoint names to paths or options."
    )


class UploadRolePermissionsResponseSerializer(serializers.Serializer):
    """Response shape for successful role permissions upload."""

    message = serializers.CharField()
    file_path = serializers.CharField()


class ProjectAssignmentSerializer(serializers.ModelSerializer):
    project_id = serializers.PrimaryKeyRelatedField(
        source="project", queryset=Project.objects.all()
    )
    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = ProjectAssignment
        fields = [
            "id",
            "project_id",
            "project_name",
            "role",
            "start_date",
            "end_date",
            "status",
        ]


class TechnologyTagIdsField(serializers.Field):
    def to_representation(self, value):
        tag_id_by_name = {
            name: tag_id for tag_id, name in TECHNOLOGY_TAG_NAME_BY_ID.items()
        }
        return [tag_id_by_name.get(tag.name, tag.id) for tag in value.all()]

    def to_internal_value(self, data):
        if data is None:
            return []
        if not isinstance(data, list):
            raise serializers.ValidationError("Expected a list of technology tag IDs.")

        parsed_ids = []
        for raw_value in data:
            try:
                parsed_ids.append(int(raw_value))
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    "All technology tag values must be valid integer IDs."
                )
        return parsed_ids


TECHNOLOGY_TAG_NAME_BY_ID: dict[int, str] = {
    1: "React",
    2: "Angular",
    3: "Vue.js",
    4: "TypeScript",
    5: "JavaScript",
    6: "Python",
    7: "Django",
    8: "Node.js",
    9: "Next.js",
    10: "PostgreSQL",
    11: "Docker",
    12: "AWS",
    13: "Tailwind CSS",
    14: "GraphQL",
    15: "Redis",
    16: "Git",
    17: "Java",
    18: "C#",
    19: ".NET",
    20: "Go",
    21: "Rust",
    22: "Kubernetes",
    23: "Flutter",
    24: "Swift",
    25: "Kotlin",
    26: "MongoDB",
    27: "MySQL",
}


class EmployeeProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    salary = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, write_only=True
    )
    current_salary = serializers.SerializerMethodField(read_only=True)
    manager_names = serializers.SerializerMethodField()
    permissions_bitmap = serializers.SerializerMethodField()
    tech_tags = TechnologyTagIdsField(required=False)
    assigned_projects = ProjectAssignmentSerializer(
        source="project_assignments", many=True, required=False
    )

    def get_manager_names(self, obj) -> str:
        return ", ".join([m.full_name or m.user.username for m in obj.managers.all()])

    def get_permissions_bitmap(self, obj) -> str:
        return bin(obj.computed_permissions_bitmap)[2:]

    def get_current_salary(self, obj):
        return obj.current_salary

    class Meta:
        model = UserProfile
        fields = EMPLOYEE_PROFILE_FIELDS
        read_only_fields = EMPLOYEE_PROFILE_READ_ONLY_FIELDS

    def validate_email(self, value):
        user = getattr(self.instance, "user", None)
        query = User.objects.filter(email=value)
        if user:
            query = query.exclude(id=user.id)
        if query.exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def create(self, validated_data):
        user_data = validated_data.pop("user", {})
        managers_data = validated_data.pop("managers", [])
        tech_tag_ids = validated_data.pop("tech_tags", None)
        new_salary = validated_data.pop("salary", None)
        validated_data.pop("project_assignments", None)
        email = user_data.get("email")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")

        password = generate_secure_password()
        username = generate_unique_username(email)

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        profile = getattr(user, "profile", None)
        if not profile:
            profile = UserProfile.objects.create(user=user)

        profile.email_address = email
        instance = apply_profile_updates_and_save(profile, validated_data)
        if managers_data:
            instance.managers.set(managers_data)
        if tech_tag_ids is not None:
            instance.tech_tags.set(self._resolve_technology_tags(tech_tag_ids))
        if new_salary is not None:
            SalaryRecord.objects.create(
                user_profile=instance,
                amount=new_salary,
                effective_date=instance.updated_at.date(),
            )
        return instance

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        managers_data = validated_data.pop("managers", None)
        tech_tag_ids = validated_data.pop("tech_tags", None)
        projects_data = validated_data.pop("project_assignments", None)
        new_salary = validated_data.pop("salary", None)
        old_role = role_value(instance.role)
        old_cpf_level = instance.cpf_level
        old_salary = instance.current_salary
        old_department = normalize_trimmed_string(instance.department)
        old_employment_status = normalize_enum_like(instance.employment_status)
        old_career_level = normalize_trimmed_string(instance.career_level)
        old_start_date = normalize_iso_date(instance.start_date)
        old_manager_ids = normalize_manager_ids(instance.managers.all())
        request = self.context.get("request")
        changed_by = request.user if request and request.user.is_authenticated else None

        if user_data:
            user = instance.user
            for attr, value in user_data.items():
                setattr(user, attr, value)
            user.save()

        if "email" in user_data:
            instance.email_address = user_data["email"]

        if "role" in validated_data:
            role = validated_data["role"]
            instance.permissions = get_role_permissions_bitmap(role) if role else ""

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if managers_data is not None:
            instance.managers.set(managers_data)

        if tech_tag_ids is not None:
            instance.tech_tags.set(self._resolve_technology_tags(tech_tag_ids))

        if projects_data is not None:
            # Simple sync logic for project assignments
            # For a more robust solution, we'd match by ID, but for now:
            instance.project_assignments.all().delete()
            for project_item in projects_data:
                ProjectAssignment.objects.create(user_profile=instance, **project_item)

        if "role" in validated_data:
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.ROLE,
                old_value=old_role,
                new_value=role_value(instance.role),
                changed_by=changed_by,
            )

        if "cpf_level" in validated_data:
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.CPF_LEVEL,
                old_value=old_cpf_level,
                new_value=instance.cpf_level,
                changed_by=changed_by,
            )

        if "department" in validated_data:
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.DEPARTMENT,
                old_value={"value": old_department},
                new_value={"value": normalize_trimmed_string(instance.department)},
                changed_by=changed_by,
            )

        if "employment_status" in validated_data:
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.EMPLOYMENT_STATUS,
                old_value={"value": old_employment_status},
                new_value={"value": normalize_enum_like(instance.employment_status)},
                changed_by=changed_by,
            )

        if "career_level" in validated_data:
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.CAREER_LEVEL,
                old_value={"value": old_career_level},
                new_value={"value": normalize_trimmed_string(instance.career_level)},
                changed_by=changed_by,
            )

        if "start_date" in validated_data:
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.START_DATE,
                old_value={"value": old_start_date},
                new_value={"value": normalize_iso_date(instance.start_date)},
                changed_by=changed_by,
            )

        if managers_data is not None:
            new_manager_ids = normalize_manager_ids(instance.managers.all())
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.MANAGER_IDS,
                old_value=manager_payload_from_ids(old_manager_ids),
                new_value=manager_payload_from_ids(new_manager_ids),
                changed_by=changed_by,
            )

        if new_salary is not None:
            if old_salary != new_salary:
                SalaryRecord.objects.create(
                    user_profile=instance,
                    amount=new_salary,
                    effective_date=instance.updated_at.date(),
                )
            log_employee_profile_change(
                employee=instance,
                field=EmployeeProfileChangeHistory.TrackedField.SALARY,
                old_value=float(old_salary) if old_salary is not None else None,
                new_value=float(new_salary),
                changed_by=changed_by,
            )

        return instance

    def _resolve_technology_tags(self, tech_tag_ids: list[int]) -> list[TechnologyTag]:
        tag_name_by_id = TECHNOLOGY_TAG_NAME_BY_ID
        unresolved_ids = sorted(
            {tag_id for tag_id in tech_tag_ids if tag_id not in tag_name_by_id}
        )
        if unresolved_ids:
            raise serializers.ValidationError(
                {
                    "tech_tags": f"Unsupported technology tag ID(s): {', '.join(str(tag_id) for tag_id in unresolved_ids)}"
                }
            )

        tags: list[TechnologyTag] = []
        for tag_id in dict.fromkeys(tech_tag_ids):
            tag_name = tag_name_by_id[tag_id]
            tag, _ = TechnologyTag.objects.get_or_create(name=tag_name)
            tags.append(tag)
        return tags


class UpdateRoleSerializer(serializers.Serializer):
    role_id = serializers.IntegerField(
        required=True, help_text="ID of the Role to assign to the user."
    )


class UpdatePermissionsSerializer(serializers.Serializer):
    permissions_bitmap = serializers.CharField(
        required=True,
        help_text="Binary string (1s and 0s) representing the user's additional permissions.",
    )

    def validate_permissions_bitmap(self, value):
        try:
            int(value, 2)
            return value
        except ValueError:
            raise serializers.ValidationError(
                "Must be a valid binary string containing only 1s and 0s."
            )


class EmployeeCVSerializer(serializers.ModelSerializer):
    profile = serializers.IntegerField(source="user_profile_id", read_only=True)
    file_key = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeDocument
        fields = [
            "id",
            "profile",
            "file_key",
            "uploaded_at",
            "is_current",
            "file_name",
            "file_size",
            "mime_type",
            "source_type",
            "provider",
            "external_url",
            "canva_design_id",
        ]

    def get_file_key(self, obj: EmployeeDocument) -> str | None:
        if not obj.file:
            return None
        return obj.file.name


class EmployeeProfileChangeHistorySerializer(serializers.ModelSerializer):
    employee_id = serializers.IntegerField(source="employee.user_id", read_only=True)
    changed_by = serializers.IntegerField(source="changed_by_id", read_only=True)
    changed_by_name = serializers.SerializerMethodField()
    changed_by_email = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeProfileChangeHistory
        fields = [
            "id",
            "employee_id",
            "field",
            "old_value",
            "new_value",
            "changed_by",
            "changed_by_name",
            "changed_by_email",
            "changed_at",
        ]

    def get_changed_by_name(self, obj):
        if not obj.changed_by:
            return None
        return obj.changed_by.get_full_name() or obj.changed_by.username

    def get_changed_by_email(self, obj):
        if not obj.changed_by:
            return None
        return obj.changed_by.email


# Asset Management Serializers


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for UserProfile model used in Asset Management"""

    user = UserSerializer(read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            "id",
            "user",
            "employee_id",
            "department",
            "hire_date",
            "phone_number",
            "emergency_contact_phone",
            "career_level",
        ]


class AssetSerializer(serializers.ModelSerializer):
    """Serializer for Asset model"""

    current_assignment = serializers.SerializerMethodField()
    is_under_warranty = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id",
            "asset_id",
            "name",
            "condition",
            "warranty_until",
            "purchase_date",
            "status",
            "serial_number",
            "model",
            "manufacturer",
            "purchase_price",
            "description",
            "created_at",
            "updated_at",
            "current_assignment",
            "is_under_warranty",
            "is_available",
        ]
        read_only_fields = ["created_at", "updated_at"]

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_current_assignment(self, obj) -> dict[str, Any] | None:
        """Get current active assignment if any"""
        current = obj.current_assignment
        if current:
            return {
                "id": current.id,
                "employee": current.employee.user.get_full_name()
                or current.employee.user.username,
                "assigned_at": current.assigned_at,
            }
        return None

    @extend_schema_field(serializers.BooleanField())
    def get_is_under_warranty(self, obj) -> bool:
        """Check if asset is under warranty"""
        return obj.is_under_warranty

    @extend_schema_field(serializers.BooleanField())
    def get_is_available(self, obj) -> bool:
        """Check if asset is available for assignment"""
        return obj.is_available


class AssignmentSerializer(serializers.ModelSerializer):
    """Serializer for Assignment model"""

    asset_details = AssetSerializer(source="asset", read_only=True)
    employee_details = UserProfileSerializer(source="employee", read_only=True)
    assigned_by_details = UserProfileSerializer(source="assigned_by", read_only=True)
    is_active = serializers.SerializerMethodField()
    duration_days = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = [
            "id",
            "asset",
            "employee",
            "assigned_at",
            "returned_at",
            "assigned_by",
            "return_condition",
            "notes",
            "asset_details",
            "employee_details",
            "assigned_by_details",
            "is_active",
            "duration_days",
        ]
        read_only_fields = ["assigned_at"]

    def validate(self, data):
        """Validate assignment data"""
        asset = data.get("asset")
        returned_at = data.get("returned_at")

        # If this is a new assignment (no returned_at), check if asset is available
        if not returned_at and asset and not asset.is_available:
            raise serializers.ValidationError(
                "Asset is not available for assignment. It may already be assigned or not in active status."
            )

        return data

    @extend_schema_field(serializers.BooleanField())
    def get_is_active(self, obj) -> bool:
        """Check if assignment is active (not returned)"""
        return obj.is_active

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_duration_days(self, obj) -> int | None:
        """Get duration of assignment in days"""
        return obj.duration_days


class ReplacementLogSerializer(serializers.ModelSerializer):
    """Serializer for ReplacementLog model"""

    asset_details = AssetSerializer(source="asset", read_only=True)
    replacement_asset_details = AssetSerializer(
        source="replacement_asset", read_only=True
    )
    replaced_by_details = UserProfileSerializer(source="replaced_by", read_only=True)

    class Meta:
        model = ReplacementLog
        fields = [
            "id",
            "asset",
            "reason",
            "date",
            "replaced_by",
            "replacement_asset",
            "cost",
            "asset_details",
            "replacement_asset_details",
            "replaced_by_details",
        ]
        read_only_fields = ["date"]


class AssetCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for creating assets"""

    class Meta:
        model = Asset
        fields = [
            "asset_id",
            "name",
            "condition",
            "warranty_until",
            "purchase_date",
            "status",
            "serial_number",
            "model",
            "manufacturer",
            "purchase_price",
            "description",
        ]


class AssignmentCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for creating assignments"""

    class Meta:
        model = Assignment
        fields = ["asset", "employee", "assigned_by", "notes"]
        read_only_fields = ["assigned_by"]

    def validate_asset(self, value):
        """Validate that asset is available for assignment"""
        if not value.is_available:
            raise serializers.ValidationError(
                "Asset is not available for assignment. It may already be assigned or not in active status."
            )
        return value


class AssignmentReturnSerializer(serializers.ModelSerializer):
    """Serializer for returning assets"""

    class Meta:
        model = Assignment
        fields = ["return_condition", "notes"]

    def validate(self, data):
        """Validate return data"""
        if not self.instance.is_active:
            raise serializers.ValidationError("This assignment is already returned.")
        return data


# ──────────────────────────────────────────
# Leave Management Serializers
# ──────────────────────────────────────────


class LeavePolicySerializer(serializers.ModelSerializer):
    """Serializer for LeavePolicy model."""

    leave_type_display = serializers.CharField(
        source="get_leave_type_display", read_only=True
    )

    class Meta:
        model = LeavePolicy
        fields = [
            "id",
            "leave_type",
            "leave_type_display",
            "allocated_days_per_year",
            "carryover_days",
            "requires_approval",
            "requires_covering_employee",
            "min_notice_in_days",
            "max_consecutive_days",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class LeaveBalanceSerializer(serializers.ModelSerializer):
    """Serializer for LeaveBalance model."""

    employee_id = serializers.IntegerField(source="employee.id", read_only=True)
    employee_name = serializers.CharField(
        source="employee.user.get_full_name", read_only=True
    )
    leave_type_display = serializers.CharField(
        source="get_leave_type_display", read_only=True
    )
    remaining = serializers.ReadOnlyField()

    class Meta:
        model = LeaveBalance
        fields = [
            "id",
            "employee_id",
            "employee_name",
            "leave_type",
            "leave_type_display",
            "allocated",
            "used",
            "remaining",
            "carryover",
            "year",
            "last_updated",
        ]
        read_only_fields = ["employee_id", "employee_name", "remaining", "last_updated"]


class LeaveRequestListSerializer(serializers.ModelSerializer):
    """Minimal serializer for listing leave requests."""

    employee_id = serializers.IntegerField(source="employee.id", read_only=True)
    employee_name = serializers.CharField(
        source="employee.user.get_full_name", read_only=True
    )
    employee_avatar = serializers.SerializerMethodField()
    leave_type_display = serializers.CharField(
        source="get_leave_type_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    days = serializers.ReadOnlyField()

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee_id",
            "employee_name",
            "employee_avatar",
            "leave_type",
            "leave_type_display",
            "start_date",
            "end_date",
            "days",
            "status",
            "status_display",
            "submitted_date",
        ]

    def get_employee_avatar(self, obj):
        """Get employee avatar URL."""
        try:
            profile = obj.employee
            if profile.avatar_url:
                return profile.avatar_url
            if profile.avatar:
                return profile.avatar.url
        except Exception:
            pass
        return None


class LeaveRequestDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for leave request with all information."""

    employee_id = serializers.IntegerField(source="employee.id", read_only=True)
    employee_name = serializers.CharField(
        source="employee.user.get_full_name", read_only=True
    )
    employee_avatar = serializers.SerializerMethodField()
    leave_type_display = serializers.CharField(
        source="get_leave_type_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    days = serializers.ReadOnlyField()

    covering_employee_id = serializers.IntegerField(
        source="covering_employee.id", read_only=True, allow_null=True
    )
    covering_employee_name = serializers.CharField(
        source="covering_employee.user.get_full_name", read_only=True, allow_null=True
    )

    approver_id = serializers.IntegerField(
        source="approver.id", read_only=True, allow_null=True
    )
    approver_name = serializers.CharField(
        source="approver.user.get_full_name", read_only=True, allow_null=True
    )

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee_id",
            "employee_name",
            "employee_avatar",
            "leave_type",
            "leave_type_display",
            "start_date",
            "end_date",
            "days",
            "reason",
            "status",
            "status_display",
            "covering_employee_id",
            "covering_employee_name",
            "submitted_date",
            "approver_id",
            "approver_name",
            "approved_date",
            "approval_comments",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "employee_id",
            "employee_name",
            "employee_avatar",
            "days",
            "submitted_date",
            "created_at",
            "updated_at",
        ]

    def get_employee_avatar(self, obj):
        """Get employee avatar URL."""
        try:
            profile = obj.employee
            if profile.avatar_url:
                return profile.avatar_url
            if profile.avatar:
                return profile.avatar.url
        except Exception:
            pass
        return None


class LeaveRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating leave requests with validation."""

    covering_employee_id = serializers.IntegerField(
        source="covering_employee.id", required=False, allow_null=True
    )

    class Meta:
        model = LeaveRequest
        fields = [
            "leave_type",
            "start_date",
            "end_date",
            "reason",
            "covering_employee_id",
        ]

    def validate(self, data):
        """Validate leave request data."""
        from datetime import date

        start_date = data.get("start_date")
        end_date = data.get("end_date")
        leave_type = data.get("leave_type")

        # Validate date range
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be after start date."}
            )

        # Validate not in the past
        if start_date and start_date < date.today():
            raise serializers.ValidationError(
                {"start_date": "Start date cannot be in the past."}
            )

        # Get employee from context
        request = self.context.get("request")
        if not request or not hasattr(request.user, "profile"):
            raise serializers.ValidationError("User profile not found.")

        employee = request.user.profile

        # Check for overlapping requests
        temp_request = LeaveRequest(
            employee=employee,
            start_date=start_date,
            end_date=end_date,
            leave_type=leave_type,
        )
        if temp_request.is_overlapping(exclude_self=False):
            raise serializers.ValidationError(
                "You already have an approved or pending leave request during this period."
            )

        # Check leave policy requirements
        try:
            policy = LeavePolicy.objects.get(leave_type=leave_type)

            # Check minimum notice
            if policy.min_notice_in_days > 0:
                notice_days = (start_date - date.today()).days
                if notice_days < policy.min_notice_in_days:
                    raise serializers.ValidationError(
                        f"This leave type requires at least {policy.min_notice_in_days} days notice."
                    )

            # Check covering employee requirement
            covering_employee_data = data.get("covering_employee")
            if policy.requires_covering_employee and not covering_employee_data:
                raise serializers.ValidationError(
                    {
                        "covering_employee_id": "This leave type requires a covering employee."
                    }
                )

            # Check max consecutive days
            if policy.max_consecutive_days:
                days = temp_request.days
                if days > policy.max_consecutive_days:
                    raise serializers.ValidationError(
                        f"This leave type allows maximum {policy.max_consecutive_days} consecutive days."
                    )

        except LeavePolicy.DoesNotExist:
            raise serializers.ValidationError(
                f"Leave policy for {leave_type} not found."
            )

        # Check sufficient balance
        from datetime import datetime

        current_year = datetime.now().year
        try:
            balance = LeaveBalance.objects.get(
                employee=employee, leave_type=leave_type, year=current_year
            )
            if balance.remaining < temp_request.days:
                raise serializers.ValidationError(
                    f"Insufficient leave balance. You have {balance.remaining} days remaining, but requesting {temp_request.days} days."
                )
        except LeaveBalance.DoesNotExist:
            raise serializers.ValidationError(
                f"Leave balance for {leave_type} not found for year {current_year}."
            )

        return data

    def create(self, validated_data):
        """Create leave request."""
        request = self.context.get("request")
        employee = request.user.profile

        # Handle covering_employee
        covering_employee_data = validated_data.pop("covering_employee", None)
        covering_employee = None
        if covering_employee_data:
            covering_employee_id = covering_employee_data.get("id")
            if covering_employee_id:
                try:
                    covering_employee = UserProfile.objects.get(id=covering_employee_id)
                except UserProfile.DoesNotExist:
                    pass

        leave_request = LeaveRequest.objects.create(
            employee=employee,
            covering_employee=covering_employee,
            **validated_data,
        )

        return leave_request


class LeaveRequestApproveSerializer(serializers.Serializer):
    """Serializer for approving leave requests."""

    comments = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        """Validate approval data."""
        leave_request = self.context.get("leave_request")
        if not leave_request:
            raise serializers.ValidationError("Leave request not found.")

        if leave_request.status != LeaveRequest.Status.PENDING:
            raise serializers.ValidationError("Only pending requests can be approved.")

        return data


class LeaveRequestRejectSerializer(serializers.Serializer):
    """Serializer for rejecting leave requests."""

    reason = serializers.CharField(required=True)

    def validate(self, data):
        """Validate rejection data."""
        leave_request = self.context.get("leave_request")
        if not leave_request:
            raise serializers.ValidationError("Leave request not found.")

        if leave_request.status != LeaveRequest.Status.PENDING:
            raise serializers.ValidationError("Only pending requests can be rejected.")

        return data


class LeaveAdjustmentSerializer(serializers.ModelSerializer):
    """Serializer for leave adjustments."""

    employee_id = serializers.IntegerField(source="employee.id", read_only=True)
    employee_name = serializers.CharField(
        source="employee.user.get_full_name", read_only=True
    )
    leave_type_display = serializers.CharField(
        source="get_leave_type_display", read_only=True
    )
    adjusted_by_id = serializers.IntegerField(
        source="adjusted_by.id", read_only=True, allow_null=True
    )
    adjusted_by_name = serializers.CharField(
        source="adjusted_by.user.get_full_name", read_only=True, allow_null=True
    )

    class Meta:
        model = LeaveAdjustment
        fields = [
            "id",
            "employee_id",
            "employee_name",
            "leave_type",
            "leave_type_display",
            "old_allocated",
            "new_allocated",
            "reason",
            "adjusted_by_id",
            "adjusted_by_name",
            "adjusted_at",
        ]
        read_only_fields = [
            "employee_id",
            "employee_name",
            "adjusted_by_id",
            "adjusted_by_name",
            "adjusted_at",
        ]


class LeaveApprovalWorkflowSerializer(serializers.ModelSerializer):
    """Serializer for leave approval workflow."""

    request_id = serializers.IntegerField(source="leave_request.id", read_only=True)
    current_approver_id = serializers.IntegerField(
        source="current_approver.id", read_only=True, allow_null=True
    )
    current_approver_name = serializers.CharField(
        source="current_approver.user.get_full_name", read_only=True, allow_null=True
    )
    current_approval_step = serializers.IntegerField(
        source="current_step", read_only=True
    )

    class Meta:
        model = LeaveApprovalWorkflow
        fields = [
            "id",
            "request_id",
            "approval_chain",
            "current_approval_step",
            "current_approver_id",
            "current_approver_name",
            "status",
            "comments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


# ──────────────────────────────────────────
# Performance Review Serializers
# ──────────────────────────────────────────


class PerformanceReviewNoteSerializer(serializers.ModelSerializer):
    author_id = serializers.IntegerField(source="author.id", read_only=True)
    author_name = serializers.CharField(
        source="author.user.get_full_name", read_only=True, allow_null=True
    )
    edited_by_id = serializers.IntegerField(
        source="edited_by.id", read_only=True, allow_null=True
    )
    edited_by_name = serializers.CharField(
        source="edited_by.user.get_full_name", read_only=True, allow_null=True
    )

    class Meta:
        model = PerformanceReviewNote
        fields = [
            "id",
            "author_id",
            "author_name",
            "visibility",
            "content",
            "edited_by_id",
            "edited_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "author_id",
            "author_name",
            "edited_by_id",
            "edited_by_name",
            "created_at",
            "updated_at",
        ]


class PerformanceReviewActionPointSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=UserProfile.objects.all(), required=False, allow_null=True
    )
    owner_id = serializers.IntegerField(
        source="owner.id", read_only=True, allow_null=True
    )
    owner_name = serializers.CharField(
        source="owner.user.get_full_name", read_only=True, allow_null=True
    )
    created_by_id = serializers.IntegerField(
        source="created_by.id", read_only=True, allow_null=True
    )
    created_by_name = serializers.CharField(
        source="created_by.user.get_full_name", read_only=True, allow_null=True
    )

    class Meta:
        model = PerformanceReviewActionPoint
        fields = [
            "id",
            "title",
            "description",
            "owner",
            "owner_id",
            "owner_name",
            "due_date",
            "status",
            "progress",
            "completed_at",
            "created_by_id",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "owner_id",
            "owner_name",
            "created_by_id",
            "created_by_name",
            "created_at",
            "updated_at",
        ]


class PerformanceReviewAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_id = serializers.IntegerField(
        source="uploaded_by.id", read_only=True, allow_null=True
    )
    uploaded_by_name = serializers.CharField(
        source="uploaded_by.user.get_full_name", read_only=True, allow_null=True
    )
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceReviewAttachment
        fields = [
            "id",
            "file",
            "file_url",
            "original_name",
            "content_type",
            "size_bytes",
            "description",
            "uploaded_by_id",
            "uploaded_by_name",
            "created_at",
        ]
        read_only_fields = [
            "file_url",
            "original_name",
            "content_type",
            "size_bytes",
            "uploaded_by_id",
            "uploaded_by_name",
            "created_at",
        ]

    def validate_file(self, value):
        max_size_mb = 10
        if value.size > max_size_mb * 1024 * 1024:
            raise serializers.ValidationError(
                f"Attachment must be under {max_size_mb} MB."
            )
        return value

    def get_file_url(self, obj):
        try:
            return obj.file.url
        except Exception:
            return None


class PerformanceReviewHistoryEventSerializer(serializers.ModelSerializer):
    actor_id = serializers.IntegerField(
        source="actor.id", read_only=True, allow_null=True
    )
    actor_name = serializers.CharField(
        source="actor.user.get_full_name", read_only=True, allow_null=True
    )
    event_type_display = serializers.CharField(
        source="get_event_type_display", read_only=True
    )

    class Meta:
        model = PerformanceReviewHistoryEvent
        fields = [
            "id",
            "event_type",
            "event_type_display",
            "description",
            "metadata",
            "actor_id",
            "actor_name",
            "created_at",
        ]
        read_only_fields = fields


class PerformanceReviewReminderSerializer(serializers.ModelSerializer):
    review_id = serializers.IntegerField(source="review.id", read_only=True)
    employee_id = serializers.IntegerField(source="review.employee.id", read_only=True)
    employee_name = serializers.CharField(
        source="review.employee.user.get_full_name", read_only=True
    )
    review_type = serializers.CharField(source="review.review_type", read_only=True)
    review_type_display = serializers.CharField(
        source="review.get_review_type_display", read_only=True
    )
    recipient_id = serializers.IntegerField(source="recipient.id", read_only=True)
    recipient_name = serializers.CharField(
        source="recipient.user.get_full_name", read_only=True
    )
    reminder_type_display = serializers.CharField(
        source="get_reminder_type_display", read_only=True
    )

    class Meta:
        model = PerformanceReviewReminder
        fields = [
            "id",
            "review_id",
            "employee_id",
            "employee_name",
            "review_type",
            "review_type_display",
            "recipient_id",
            "recipient_name",
            "reminder_type",
            "reminder_type_display",
            "message",
            "scheduled_for",
            "is_sent",
            "sent_at",
            "is_read",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields


class PerformanceReviewListSerializer(serializers.ModelSerializer):
    employee_id = serializers.IntegerField(source="employee.id", read_only=True)
    employee_name = serializers.CharField(
        source="employee.user.get_full_name", read_only=True
    )
    employee_avatar = serializers.SerializerMethodField()
    reviewer_id = serializers.IntegerField(
        source="reviewer.id", read_only=True, allow_null=True
    )
    reviewer_name = serializers.CharField(
        source="reviewer.user.get_full_name", read_only=True, allow_null=True
    )
    review_type_display = serializers.CharField(
        source="get_review_type_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceReview
        fields = [
            "id",
            "employee_id",
            "employee_name",
            "employee_avatar",
            "reviewer_id",
            "reviewer_name",
            "review_type",
            "review_type_display",
            "title",
            "scheduled_date",
            "status",
            "status_display",
            "overall_rating",
            "outcome",
            "progress",
            "cpf_score",
            "created_at",
            "updated_at",
        ]

    def get_employee_avatar(self, obj):
        profile = obj.employee
        if profile.avatar_url:
            return profile.avatar_url
        if profile.avatar:
            return profile.avatar.url
        return None

    def get_progress(self, obj):
        if obj.status == PerformanceReview.Status.COMPLETED:
            return 100
        if obj.status == PerformanceReview.Status.SCHEDULED:
            return 0

        completed_fields = 0
        if obj.overall_rating is not None:
            completed_fields += 1
        if obj.summary.strip():
            completed_fields += 1
        if obj.notes.exists():
            completed_fields += 1
        if obj.action_points.exists():
            completed_fields += 1

        return int((completed_fields / 4) * 100)


class PerformanceReviewDetailSerializer(PerformanceReviewListSerializer):
    period_start = serializers.DateField(read_only=True)
    period_end = serializers.DateField(read_only=True)
    next_review_date = serializers.DateField(read_only=True)
    performance_score = serializers.IntegerField(read_only=True, allow_null=True)
    cpf_current_level = serializers.CharField(read_only=True)
    cpf_recommended_level = serializers.CharField(read_only=True)
    summary = serializers.CharField(read_only=True)
    employee_comments = serializers.CharField(read_only=True)
    reviewer_comments = serializers.CharField(read_only=True)
    reminder_offsets_days = serializers.JSONField(read_only=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    notes = PerformanceReviewNoteSerializer(many=True, read_only=True)
    action_points = PerformanceReviewActionPointSerializer(many=True, read_only=True)
    attachments = PerformanceReviewAttachmentSerializer(many=True, read_only=True)
    history_events = PerformanceReviewHistoryEventSerializer(many=True, read_only=True)

    class Meta(PerformanceReviewListSerializer.Meta):
        fields = PerformanceReviewListSerializer.Meta.fields + [
            "period_start",
            "period_end",
            "next_review_date",
            "performance_score",
            "cpf_current_level",
            "cpf_recommended_level",
            "summary",
            "employee_comments",
            "reviewer_comments",
            "reminder_offsets_days",
            "completed_at",
            "notes",
            "action_points",
            "attachments",
            "history_events",
        ]


class PerformanceReviewCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerformanceReview
        fields = [
            "employee",
            "reviewer",
            "review_type",
            "title",
            "period_start",
            "period_end",
            "scheduled_date",
            "next_review_date",
            "status",
            "outcome",
            "overall_rating",
            "performance_score",
            "cpf_score",
            "cpf_current_level",
            "cpf_recommended_level",
            "summary",
            "employee_comments",
            "reviewer_comments",
            "reminder_offsets_days",
            "completed_at",
        ]

    def validate(self, data):
        period_start = data.get("period_start")
        period_end = data.get("period_end")
        if period_start and period_end and period_start > period_end:
            raise serializers.ValidationError(
                {"period_end": "Period end must be after period start."}
            )

        employee = data.get("employee")
        reviewer = data.get("reviewer")
        if employee and reviewer and employee.pk == reviewer.pk:
            raise serializers.ValidationError(
                {"reviewer": "Reviewer cannot be the same as employee."}
            )

        reminder_offsets_days = data.get("reminder_offsets_days")
        if reminder_offsets_days is not None:
            if not isinstance(reminder_offsets_days, list):
                raise serializers.ValidationError(
                    {"reminder_offsets_days": "Expected a list of integer day offsets."}
                )
            normalized_offsets = []
            for offset in reminder_offsets_days:
                try:
                    offset_value = int(offset)
                except (TypeError, ValueError):
                    raise serializers.ValidationError(
                        {
                            "reminder_offsets_days": "All reminder offsets must be valid integers."
                        }
                    )
                if offset_value < 0:
                    raise serializers.ValidationError(
                        {
                            "reminder_offsets_days": "Reminder offsets cannot be negative."
                        }
                    )
                normalized_offsets.append(offset_value)
            data["reminder_offsets_days"] = normalized_offsets

        return data


# ──────────────────────────────────────────
# Onboarding / Offboarding Serializers
# ──────────────────────────────────────────


class TaskTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskTemplate
        fields = ["id", "title", "order", "role_responsible"]


class ChecklistTemplateSerializer(serializers.ModelSerializer):
    task_templates = TaskTemplateSerializer(many=True, required=False)

    class Meta:
        model = ChecklistTemplate
        fields = ["id", "name", "type", "task_templates"]

    def create(self, validated_data):
        task_templates_data = validated_data.pop("task_templates", [])
        template = ChecklistTemplate.objects.create(**validated_data)
        for task_data in task_templates_data:
            TaskTemplate.objects.create(checklist_template=template, **task_data)
        return template

    def update(self, instance, validated_data):
        task_templates_data = validated_data.pop("task_templates", None)
        instance.name = validated_data.get("name", instance.name)
        instance.type = validated_data.get("type", instance.type)
        instance.save()

        if task_templates_data is not None:
            instance.task_templates.all().delete()
            for task_data in task_templates_data:
                TaskTemplate.objects.create(checklist_template=instance, **task_data)

        return instance


class ChecklistTaskSerializer(serializers.ModelSerializer):
    assigned_to = UserProfileSerializer(read_only=True)
    task_template = TaskTemplateSerializer(read_only=True)

    class Meta:
        model = ChecklistTask
        fields = [
            "id",
            "checklist_instance",
            "task_template",
            "title",
            "status",
            "assigned_to",
            "due_date",
            "completed_at",
        ]
        read_only_fields = [
            "checklist_instance",
            "task_template",
            "assigned_to",
            "completed_at",
        ]
