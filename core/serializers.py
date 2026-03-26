from django.contrib.auth.models import User
from rest_framework import serializers

from core.constants import (
    EMPLOYEE_PROFILE_FIELDS,
    EMPLOYEE_PROFILE_READ_ONLY_FIELDS,
    REGISTER_EXTRA_KWARGS,
    REGISTER_FIELDS,
)
from core.models import UserProfile
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


class EmployeeProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", required=False)
    last_name = serializers.CharField(source="user.last_name", required=False)
    email = serializers.EmailField(source="user.email", required=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    manager_name = serializers.CharField(source="manager.full_name", read_only=True)
    permissions_bitmap = serializers.SerializerMethodField()

    def get_permissions_bitmap(self, obj):
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
        return apply_profile_updates_and_save(profile, validated_data)

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
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
