"""
Leave Analytics Export Service

Builds CSV and PDF exports for the leave analytics module. The view layer
is responsible for permission scoping (passing a pre-scoped queryset) and
HTTP response wrapping. This module is pure data-in/bytes-out.

Public entry point:

    export_leave_analytics(
        *,
        queryset,
        export_format,
        year,
        month=None,
        department=None,
        is_hr=True,
    ) -> ExportResult

Returns an `ExportResult(content: bytes, filename: str, content_type: str)`.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from django.db.models import QuerySet, Sum
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.enums import LeaveType
from core.models import LeaveMonthlyAggregate

EXPORT_FORMAT_CSV = "csv"
EXPORT_FORMAT_PDF = "pdf"
ALLOWED_EXPORT_FORMATS = frozenset({EXPORT_FORMAT_CSV, EXPORT_FORMAT_PDF})

CSV_CONTENT_TYPE = "text/csv"
PDF_CONTENT_TYPE = "application/pdf"

CSV_HEADERS = (
    "employee_id",
    "employee_name",
    "department",
    "year",
    "month",
    "leave_type",
    "approved_days",
    "pending_days",
    "rejected_days",
    "cancelled_days",
    "requests_count",
)


@dataclass(frozen=True)
class ExportResult:
    content: bytes
    filename: str
    content_type: str


def export_leave_analytics(
    *,
    queryset: QuerySet[LeaveMonthlyAggregate],
    export_format: str,
    year: int,
    month: int | None = None,
    department: str | None = None,
    is_hr: bool = True,
) -> ExportResult:
    """Build a CSV or PDF leave analytics export for the given scope."""
    if export_format not in ALLOWED_EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {export_format!r}")

    scoped_qs = _apply_scope(queryset, year=year, month=month, department=department)

    filename_stem = _filename_stem(year=year, month=month, department=department)

    if export_format == EXPORT_FORMAT_CSV:
        content = _build_csv(scoped_qs)
        return ExportResult(
            content=content,
            filename=f"{filename_stem}.csv",
            content_type=CSV_CONTENT_TYPE,
        )

    content = _build_pdf(
        scoped_qs,
        year=year,
        month=month,
        department=department,
        is_hr=is_hr,
    )
    return ExportResult(
        content=content,
        filename=f"{filename_stem}.pdf",
        content_type=PDF_CONTENT_TYPE,
    )


def _apply_scope(
    queryset: QuerySet[LeaveMonthlyAggregate],
    *,
    year: int,
    month: int | None,
    department: str | None,
) -> QuerySet[LeaveMonthlyAggregate]:
    qs = queryset.filter(year=year)
    if month is not None:
        qs = qs.filter(month=month)
    if department is not None:
        qs = qs.filter(employee__department=department)
    return qs.select_related("employee__user").order_by(
        "employee__user__first_name",
        "employee__user__last_name",
        "month",
        "leave_type",
    )


def _filename_stem(
    *,
    year: int,
    month: int | None,
    department: str | None,
) -> str:
    parts = ["leave-analytics", str(year)]
    if month is not None:
        parts.append(f"{month:02d}")
    if department:
        parts.append(_slugify(department))
    return "-".join(parts)


def _slugify(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")


def _employee_display(row: LeaveMonthlyAggregate) -> str:
    user = getattr(row.employee, "user", None)
    if user is not None:
        full = f"{user.first_name} {user.last_name}".strip()
        if full:
            return full
        if user.username:
            return user.username
    return f"Employee #{row.employee_id}"


def _build_csv(queryset: QuerySet[LeaveMonthlyAggregate]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)
    for row in queryset.iterator(chunk_size=500):
        writer.writerow(
            [
                row.employee_id,
                _employee_display(row),
                row.employee.department or "",
                row.year,
                row.month,
                row.leave_type,
                row.approved_days,
                row.pending_days,
                row.rejected_days,
                row.cancelled_days,
                row.requests_count,
            ]
        )
    # UTF-8 BOM keeps Excel happy with non-ASCII names.
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _build_pdf(
    queryset: QuerySet[LeaveMonthlyAggregate],
    *,
    year: int,
    month: int | None,
    department: str | None,
    is_hr: bool,
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Leave Analytics {year}",
    )
    styles = getSampleStyleSheet()
    story: list = []

    title_text = f"Leave Analytics Report — {year}"
    if month is not None:
        title_text += f" · {date(year, month, 1).strftime('%B')}"
    if department:
        title_text += f" · {department}"
    if not is_hr:
        title_text += " · Personal view"
    story.append(Paragraph(title_text, styles["Title"]))
    story.append(Spacer(1, 6 * mm))

    aggregates = list(queryset)

    story.extend(_build_summary_block(aggregates, styles))
    story.append(Spacer(1, 6 * mm))

    story.extend(_build_by_type_block(aggregates, styles))
    story.append(Spacer(1, 6 * mm))

    if month is None:
        story.extend(_build_monthly_block(aggregates, year, styles))
        story.append(Spacer(1, 6 * mm))

    story.extend(_build_employee_block(aggregates, styles))

    doc.build(story)
    return buffer.getvalue()


def _build_summary_block(
    aggregates: list[LeaveMonthlyAggregate],
    styles,
) -> list:
    approved = sum(row.approved_days for row in aggregates)
    pending = sum(row.pending_days for row in aggregates)
    headcount = len({row.employee_id for row in aggregates})
    requests = sum(row.requests_count for row in aggregates)

    data = [
        ["Approved days", "Pending days", "Employees", "Requests"],
        [approved, pending, headcount, requests],
    ]
    table = Table(data, hAlign="LEFT")
    table.setStyle(_summary_table_style())
    return [Paragraph("Summary", styles["Heading2"]), table]


def _build_by_type_block(
    aggregates: list[LeaveMonthlyAggregate],
    styles,
) -> list:
    totals = defaultdict(int)
    for row in aggregates:
        totals[row.leave_type] += row.approved_days

    type_labels = dict(LeaveType.choices)
    rows = [
        [type_labels.get(lt, lt), totals[lt]]
        for lt in LeaveType.values
        if totals[lt] > 0
    ]
    if not rows:
        return [
            Paragraph("By leave type", styles["Heading2"]),
            Paragraph("No approved leave in this period.", styles["BodyText"]),
        ]

    data = [["Leave type", "Approved days"], *rows]
    table = Table(data, hAlign="LEFT", colWidths=[60 * mm, 40 * mm])
    table.setStyle(_data_table_style())
    return [Paragraph("By leave type", styles["Heading2"]), table]


def _build_monthly_block(
    aggregates: list[LeaveMonthlyAggregate],
    year: int,
    styles,
) -> list:
    per_month = defaultdict(int)
    for row in aggregates:
        per_month[row.month] += row.approved_days

    data = [["Month", "Approved days"]]
    for m in range(1, 13):
        data.append([date(year, m, 1).strftime("%b"), per_month[m]])

    table = Table(data, hAlign="LEFT", colWidths=[40 * mm, 40 * mm])
    table.setStyle(_data_table_style())
    return [Paragraph("Monthly trend", styles["Heading2"]), table]


def _build_employee_block(
    aggregates: list[LeaveMonthlyAggregate],
    styles,
) -> list:
    per_employee: dict[int, dict] = {}
    for row in aggregates:
        bucket = per_employee.setdefault(
            row.employee_id,
            {
                "name": _employee_display(row),
                "department": row.employee.department or "",
                "approved": 0,
                "pending": 0,
            },
        )
        bucket["approved"] += row.approved_days
        bucket["pending"] += row.pending_days

    if not per_employee:
        return [
            Paragraph("Per employee", styles["Heading2"]),
            Paragraph("No employees in this scope.", styles["BodyText"]),
        ]

    ordered = sorted(per_employee.values(), key=lambda b: b["name"].lower())
    data = [["Employee", "Department", "Approved", "Pending"]]
    for bucket in ordered:
        data.append(
            [
                bucket["name"],
                bucket["department"],
                bucket["approved"],
                bucket["pending"],
            ]
        )

    table = Table(
        data,
        hAlign="LEFT",
        colWidths=[70 * mm, 60 * mm, 30 * mm, 30 * mm],
        repeatRows=1,
    )
    table.setStyle(_data_table_style())
    return [Paragraph("Per employee", styles["Heading2"]), table]


def _summary_table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 1), (-1, 1), 14),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
        ]
    )


def _data_table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (-1, 0), "LEFT"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def aggregate_totals(queryset: QuerySet[LeaveMonthlyAggregate]) -> dict[str, int]:
    """Convenience helper used by tests / callers wanting a quick sum."""
    summary = queryset.aggregate(
        approved=Sum("approved_days"),
        pending=Sum("pending_days"),
    )
    return {
        "approved": summary["approved"] or 0,
        "pending": summary["pending"] or 0,
    }


__all__ = [
    "ALLOWED_EXPORT_FORMATS",
    "CSV_CONTENT_TYPE",
    "EXPORT_FORMAT_CSV",
    "EXPORT_FORMAT_PDF",
    "ExportResult",
    "PDF_CONTENT_TYPE",
    "aggregate_totals",
    "export_leave_analytics",
]
