from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from rest_framework import serializers


class UserSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "avatar_url"]

    def get_avatar_url(self, obj: User) -> str | None:
        try:
            profile = obj.profile
        except Exception:
            return None
        if not getattr(profile, "avatar", None):
            return None
        try:
            return profile.avatar.url
        except Exception:
            return None


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    password_confirm = serializers.CharField(write_only=True, required=True)
    avatar = serializers.ImageField(required=False, allow_null=True)
    avatar_url = serializers.URLField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "avatar",
            "avatar_url",
        ]
        extra_kwargs = {
            "email": {"required": True},
        }

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

        from core.models import UserProfile

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
                import urllib.request

                with urllib.request.urlopen(avatar_url, timeout=10) as resp:
                    raw = resp.read()

                profile.avatar.save(
                    "avatar.png",
                    ContentFile(raw, name=f"avatar-{user.id}.png"),
                    save=True,
                )
        except Exception:
            # Keep registration functional; avatar can be generated later.
            pass

        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)


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
