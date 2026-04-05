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
    ChecklistTemplate,
    Project,
    ProjectAssignment,
    ReplacementLog,
    TaskTemplate,
    UserProfile,
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


class EmployeeProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    manager_names = serializers.SerializerMethodField()
    permissions_bitmap = serializers.SerializerMethodField()
    assigned_projects = ProjectAssignmentSerializer(
        source="project_assignments", many=True, required=False
    )

    def get_manager_names(self, obj) -> str:
        return ", ".join([m.full_name or m.user.username for m in obj.managers.all()])

    def get_permissions_bitmap(self, obj) -> str:
        return bin(obj.computed_permissions_bitmap)[2:]

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
        return instance

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        managers_data = validated_data.pop("managers", None)
        projects_data = validated_data.pop("project_assignments", None)

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

        if projects_data is not None:
            # Simple sync logic for project assignments
            # For a more robust solution, we'd match by ID, but for now:
            instance.project_assignments.all().delete()
            for project_item in projects_data:
                ProjectAssignment.objects.create(user_profile=instance, **project_item)

        return instance


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
