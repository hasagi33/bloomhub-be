from __future__ import annotations

import logging

from django.db.models import Count
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.ai.graph import run_assistant_turn, runtime_status
from core.ai.json_utils import make_json_safe
from core.ai.message_display import display_chat_message_content
from core.ai.tools import registry
from core.choice_fields import patch_serializer_choice_fields
from core.models import AIChatMessage, AIChatSession

patch_serializer_choice_fields()

logger = logging.getLogger(__name__)


class AIChatMessageSerializer(serializers.ModelSerializer):
    content = serializers.SerializerMethodField()

    class Meta:
        model = AIChatMessage
        fields = ["id", "role", "content", "metadata", "created_at"]
        read_only_fields = fields

    def get_content(self, obj) -> str:
        return display_chat_message_content(
            role=obj.role,
            content=obj.content,
            metadata=obj.metadata,
        )


class AIChatSessionSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = AIChatSession
        fields = [
            "id",
            "title",
            "state",
            "pending_confirmation",
            "is_archived",
            "message_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_message_count(self, obj) -> int:
        return getattr(obj, "message_count", obj.messages.count())


class AIChatSessionDetailSerializer(AIChatSessionSerializer):
    messages = AIChatMessageSerializer(many=True, read_only=True)

    class Meta(AIChatSessionSerializer.Meta):
        fields = AIChatSessionSerializer.Meta.fields + ["messages"]


class AIChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(required=False, allow_blank=True, default="")
    session_id = serializers.IntegerField(required=False)
    tool_name = serializers.ChoiceField(
        choices=[],
        required=False,
        help_text="Optional explicit tool name for deterministic client-driven calls.",
    )
    arguments = serializers.DictField(required=False, default=dict)
    confirm = serializers.BooleanField(required=False, default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tool_name"].choices = [(name, name) for name in registry.names()]

    def validate(self, attrs):
        if (
            not attrs.get("confirm")
            and not attrs.get("message")
            and not attrs.get("tool_name")
        ):
            raise serializers.ValidationError(
                "Provide either a message, an explicit tool_name, or confirm=true."
            )
        return attrs


class AIChatView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AIChatRequestSerializer

    @extend_schema(
        tags=["AI"],
        request=AIChatRequestSerializer,
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def post(self, request):
        serializer = AIChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Touch User.last_login so the AI assistant's sensitive-action gate
        # can rely on it. JWT auth does not update this field on its own;
        # without this nudge, long-lived sessions permanently fail the
        # recent-auth check. Throttled by a per-request stale-check inside
        # update_last_login (it's a single UPDATE — cost is negligible).
        try:
            from django.contrib.auth.models import update_last_login
            from django.utils import timezone

            user = request.user
            last_login = getattr(user, "last_login", None)
            if (
                last_login is None
                or (timezone.now() - last_login).total_seconds() >= 60
            ):
                update_last_login(None, user)
        except Exception:
            pass

        session_id = data.get("session_id")
        if session_id:
            session = get_object_or_404(
                AIChatSession,
                pk=session_id,
                user=request.user,
                is_archived=False,
            )
        else:
            session = AIChatSession.objects.create(user=request.user)

        try:
            result = run_assistant_turn(
                user=request.user,
                session=session,
                message=data.get("message", ""),
                tool_name=data.get("tool_name"),
                arguments=data.get("arguments") or {},
                confirm=data.get("confirm", False),
            )
        except APIException:
            raise
        except Exception as exc:
            logger.exception(
                "[AI] api.chat_failed session=%s user=%s error=%s",
                session.id,
                request.user.id,
                exc,
            )
            fallback_message = (
                "I am not able to fulfill your request, try a different prompt."
            )
            result = {
                "session_id": session.id,
                "message": fallback_message,
                "tool_name": None,
                "module": "general",
                "result": {
                    "summary": fallback_message,
                    "blocked": True,
                    "reason": "ai_service_failed",
                },
                "entities": [],
                "entity_spans": [],
                "ui_action_type": "message",
                "ui_action": {"type": "message"},
                "requires_confirmation": False,
                "requires_input": False,
                "pending_confirmation": session.pending_confirmation,
            }
            AIChatMessage.objects.create(
                session=session,
                role=AIChatMessage.Role.ASSISTANT,
                content=fallback_message,
                metadata={
                    "module": "general",
                    "result": result["result"],
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        return Response(make_json_safe(result), status=status.HTTP_200_OK)


class AIChatSessionListView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AIChatSessionSerializer

    @extend_schema(
        tags=["AI"],
        responses={200: AIChatSessionSerializer(many=True)},
    )
    def get(self, request):
        qs = (
            AIChatSession.objects.filter(user=request.user, is_archived=False)
            .annotate(message_count=Count("messages"))
            .order_by("-updated_at")
        )
        return Response(AIChatSessionSerializer(qs, many=True).data)


class AIChatSessionDetailView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AIChatSessionDetailSerializer

    def get_object(self, request, pk):
        return get_object_or_404(
            AIChatSession.objects.prefetch_related("messages"),
            pk=pk,
            user=request.user,
            is_archived=False,
        )

    @extend_schema(tags=["AI"], responses={200: AIChatSessionDetailSerializer})
    def get(self, request, pk):
        return Response(
            AIChatSessionDetailSerializer(self.get_object(request, pk)).data
        )

    @extend_schema(tags=["AI"], responses={204: None})
    def delete(self, request, pk):
        session = self.get_object(request, pk)
        session.is_archived = True
        session.save(update_fields=["is_archived", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class AIChatCapabilitiesView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = None

    @extend_schema(
        tags=["AI"],
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def get(self, request):
        tools = registry.public_manifest()
        modules = sorted({tool["module"] for tool in tools})
        return Response(
            {
                "runtime": runtime_status(),
                "modules": modules,
                "tools": tools,
                "module_counts": registry.module_counts(),
            }
        )


class AIChatToolCoverageView(APIView):
    """Per-module tool coverage report.

    Lighter than `/capabilities/` (no full args schemas) — meant for
    dashboards / admin views that just want "how many tools per module".
    """

    permission_classes = [IsAuthenticated]
    serializer_class = None

    @extend_schema(
        tags=["AI"],
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT)},
    )
    def get(self, request):
        from core.ai.tools.coverage import gaps, module_counts, tool_coverage

        return Response(
            {
                "counts": module_counts(),
                "coverage": tool_coverage(),
                "gaps": gaps(),
            }
        )
