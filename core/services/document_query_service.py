from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone

from core.models import Document


class DocumentFilterError(ValueError):
    """Raised when document list query parameters are invalid."""


DOCUMENT_LIST_ORDERINGS = {
    "updated_at",
    "-updated_at",
    "expiry_date",
    "-expiry_date",
    "category",
    "-category",
    "name",
    "-name",
}


def document_queryset(include_archived: bool = False):
    queryset = Document.objects.select_related(
        "uploaded_by__user",
        "employee__user",
    ).prefetch_related("signers", "versions")
    if not include_archived:
        queryset = queryset.filter(archived=False)
    return queryset


def get_document_for_api(pk):
    try:
        return document_queryset(include_archived=True).get(pk=pk)
    except Document.DoesNotExist:
        return None


def get_document_for_response(pk):
    return document_queryset(include_archived=True).get(pk=pk)


def apply_document_list_filters(queryset, params):
    category = params.get("category")
    if category:
        if category not in Document.Category.values:
            raise DocumentFilterError("Invalid category filter.")
        queryset = queryset.filter(category=category)

    signature_status = params.get("signature_status")
    if signature_status:
        if signature_status not in Document.SignatureStatus.values:
            raise DocumentFilterError("Invalid signature_status filter.")
        queryset = queryset.filter(signature_status=signature_status)

    expiry_filter = params.get("expiry_filter")
    if expiry_filter:
        today = timezone.localdate()
        if expiry_filter == "expiring_soon":
            queryset = queryset.filter(
                expiry_date__gte=today,
                expiry_date__lte=today + timedelta(days=30),
            )
        elif expiry_filter == "expired":
            queryset = queryset.filter(expiry_date__lt=today)
        else:
            raise DocumentFilterError("Invalid expiry_filter value.")

    search = params.get("search", "").strip()
    if search:
        queryset = queryset.filter(
            models.Q(name__icontains=search)
            | models.Q(description__icontains=search)
            | models.Q(tags__icontains=search)
        )

    ordering = params.get("ordering", "-updated_at")
    if ordering in DOCUMENT_LIST_ORDERINGS:
        queryset = queryset.order_by(ordering)

    return queryset
