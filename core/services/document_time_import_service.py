from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_date, parse_time
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.enums import (
    ImportBatchStatus,
    ImportRowStatus,
    TimeEntryAuditEventType,
    TimeEntrySourceType,
)
from core.models import Project, TimeEntry, TimeImportBatch, TimeImportRow, TimeTask
from core.services.time_tracking_service import (
    canonical_duplicate_fingerprint,
    find_duplicate,
    fingerprint_for_entry,
    log_time_entry_event,
    profile_for_user,
)

REQUIRED_FIELDS = {"employee", "date", "project", "hours"}
FIELD_ALIASES = {
    "employee": {"employee", "name", "person", "worker", "consultant", "email"},
    "employee_id": {"employee id", "employee_id"},
    "date": {"date", "day", "work date", "work_date"},
    "start_time": {"start time", "start_time", "started"},
    "end_time": {"end time", "end_time"},
    "project": {"project", "client", "project/client", "account"},
    "project_id": {"project id", "project_id"},
    "task": {"task", "activity", "phase"},
    "task_id": {"task id", "task_id"},
    "jira_issue": {"jira", "jira issue", "issue", "issue key", "ticket"},
    "jira_issue_key": {"jira issue key", "jira_issue_key"},
    "hours": {"hours", "hrs", "time", "duration", "logged"},
    "notes": {"notes", "note", "comment", "comments", "description"},
}


def require_document_import_admin(user):
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return
    from core.services.time_tracking_service import has_time_tracking_permission

    if not has_time_tracking_permission(user, "approve_team_timesheets"):
        raise PermissionDenied(
            "You do not have permission to manage document time imports."
        )


def upload_document_import(*, user, uploaded_file) -> TimeImportBatch:
    require_document_import_admin(user)
    file_name = getattr(uploaded_file, "name", "") or "upload"
    rows, detected = parse_uploaded_file(uploaded_file, file_name)
    if not rows:
        raise ValidationError({"file": "No recognizable time table rows found."})
    batch = TimeImportBatch.objects.create(
        source_type=TimeEntrySourceType.DOCUMENT_IMPORT,
        file_name=file_name,
        source_file=uploaded_file,
        uploaded_by=profile_for_user(user),
        detected_columns=detected,
        column_mapping=detected.get("mapping", {}),
        status=(
            ImportBatchStatus.PREVIEWED
            if _mapping_complete(detected.get("mapping", {}))
            else ImportBatchStatus.NEEDS_MAPPING
        ),
    )
    for index, row in enumerate(rows):
        TimeImportRow.objects.create(
            batch=batch,
            sheet_name=row.get("sheet_name", ""),
            table_index=row.get("table_index"),
            row_number=row["row_number"],
            row_index=index,
            raw_data=row["data"],
            original_row_fingerprint=_row_fingerprint(row["data"]),
        )
    validate_batch_rows(batch)
    return batch


def parse_uploaded_file(uploaded_file, file_name: str):
    suffix = Path(file_name).suffix.lower()
    data = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    if not data:
        raise ValidationError({"file": "Uploaded file is empty."})
    if suffix == ".csv":
        return _parse_csv(data)
    if suffix == ".xlsx":
        return _parse_xlsx(data)
    if suffix == ".docx":
        return _parse_docx(data)
    raise ValidationError(
        {"file": "Unsupported file type. Allowed formats: DOCX, CSV, XLSX."}
    )


def _parse_csv(data: bytes):
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValidationError({"file": "CSV file has no header row."})
    rows = [
        {
            "sheet_name": "",
            "table_index": 0,
            "row_number": row_number,
            "data": {key: value for key, value in row.items()},
        }
        for row_number, row in enumerate(reader, start=2)
        if any((value or "").strip() for value in row.values())
    ]
    return rows, _detect_columns(reader.fieldnames)


def _parse_xlsx(data: bytes):
    try:
        import openpyxl
    except ImportError as exc:
        raise ValidationError({"file": "XLSX parsing requires openpyxl."}) from exc
    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = []
    first_headers: list[str] | None = None
    for sheet in workbook.worksheets:
        values = list(sheet.iter_rows(values_only=True))
        if not values:
            continue
        headers = [str(cell or "").strip() for cell in values[0]]
        if not any(headers):
            continue
        first_headers = first_headers or headers
        for row_number, row in enumerate(values[1:], start=2):
            raw = {headers[index]: row[index] for index in range(len(headers))}
            if any(value not in ("", None) for value in raw.values()):
                rows.append(
                    {
                        "sheet_name": sheet.title,
                        "table_index": 0,
                        "row_number": row_number,
                        "data": _stringify_raw(raw),
                    }
                )
    if first_headers is None:
        raise ValidationError({"file": "XLSX file has no recognizable table."})
    return rows, _detect_columns(first_headers)


def _parse_docx(data: bytes):
    try:
        from docx import Document as DocxDocument
    except ImportError as exc:
        raise ValidationError({"file": "DOCX parsing requires python-docx."}) from exc
    document = DocxDocument(io.BytesIO(data))
    rows = []
    first_headers: list[str] | None = None
    for table_index, table in enumerate(document.tables):
        if not table.rows:
            continue
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        if not any(headers):
            continue
        first_headers = first_headers or headers
        for row_number, table_row in enumerate(table.rows[1:], start=2):
            raw = {
                headers[index]: table_row.cells[index].text.strip()
                for index in range(len(headers))
            }
            if any(str(value).strip() for value in raw.values()):
                rows.append(
                    {
                        "sheet_name": "",
                        "table_index": table_index,
                        "row_number": row_number,
                        "data": raw,
                    }
                )
    if first_headers is None:
        raise ValidationError({"file": "DOCX file has no recognizable table."})
    return rows, _detect_columns(first_headers)


def _detect_columns(headers: list[str]):
    mapping: dict[str, str] = {}
    ambiguous = []
    normalized_headers = {header: _normalize_header(header) for header in headers}
    for field, aliases in FIELD_ALIASES.items():
        exact_matches = [
            header
            for header, normalized in normalized_headers.items()
            if normalized == field.replace("_", " ") or normalized in aliases
        ]
        if len(exact_matches) == 1:
            mapping[field] = exact_matches[0]
            continue
        matches = [
            header
            for header, normalized in normalized_headers.items()
            if any(alias in normalized for alias in aliases if len(alias) > 3)
        ]
        if len(matches) == 1:
            mapping[field] = matches[0]
        elif len(matches) > 1:
            ambiguous.append({"field": field, "candidates": matches})
    missing_required = sorted(
        field for field in REQUIRED_FIELDS if not _mapped_required_field(mapping, field)
    )
    return {
        "headers": headers,
        "mapping": mapping,
        "ambiguous": ambiguous,
        "missing_required": missing_required,
    }


def _mapping_complete(mapping: dict[str, str]) -> bool:
    return all(_mapped_required_field(mapping, field) for field in REQUIRED_FIELDS)


def _mapped_required_field(mapping: dict[str, str], field: str) -> bool:
    if field in mapping:
        return True
    if field == "employee":
        return bool(mapping.get("employee_id"))
    if field == "project":
        return bool(mapping.get("project_id"))
    return False


def _normalize_header(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _stringify_raw(row: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            result[key] = value.date().isoformat()
        elif value is None:
            result[key] = ""
        else:
            result[key] = str(value)
    return result


def _row_fingerprint(row: dict[str, Any]) -> str:
    payload = json.dumps(_stringify_raw(row), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def map_columns(
    *, user, batch: TimeImportBatch, mapping: dict[str, str]
) -> TimeImportBatch:
    require_document_import_admin(user)
    headers = batch.detected_columns.get("headers", [])
    invalid = [column for column in mapping.values() if column not in headers]
    if invalid:
        raise ValidationError(
            {"column_mapping": f"Unknown columns: {', '.join(invalid)}"}
        )
    batch.column_mapping = mapping
    batch.status = (
        ImportBatchStatus.PREVIEWED
        if _mapping_complete(mapping)
        else ImportBatchStatus.NEEDS_MAPPING
    )
    batch.save(update_fields=["column_mapping", "status", "updated_at"])
    validate_batch_rows(batch)
    return batch


def validate_batch_rows(batch: TimeImportBatch) -> TimeImportBatch:
    mapping = batch.column_mapping or {}
    for row in batch.rows.all():
        if row.status == ImportRowStatus.COMMITTED:
            continue
        parsed, messages = parse_import_row(row.raw_data, mapping)
        row.parsed_data = parsed
        row.validation_messages = messages
        if not messages:
            row.status = ImportRowStatus.VALID
        elif all(message["code"] == "duplicate" for message in messages):
            row.status = ImportRowStatus.SKIPPED
        else:
            row.status = ImportRowStatus.ERROR
        row.save(
            update_fields=["parsed_data", "validation_messages", "status", "updated_at"]
        )
    _refresh_batch_counts(batch)
    return batch


def parse_import_row(raw: dict[str, Any], mapping: dict[str, str]):
    messages = []
    parsed: dict[str, Any] = {}
    if not _mapping_complete(mapping):
        messages.append(
            {
                "code": "missing_column_mapping",
                "message": "Required column mapping is incomplete.",
            }
        )
        return parsed, messages
    employee_id_text = _raw(raw, mapping, "employee_id")
    employee_text = _raw(raw, mapping, "employee")
    date_text = _raw(raw, mapping, "date")
    start_time_text = _raw(raw, mapping, "start_time")
    end_time_text = _raw(raw, mapping, "end_time")
    project_id_text = _raw(raw, mapping, "project_id")
    project_text = _raw(raw, mapping, "project")
    task_id_text = _raw(raw, mapping, "task_id")
    task_text = _raw(raw, mapping, "task")
    jira_issue = (
        (_raw(raw, mapping, "jira_issue_key") or _raw(raw, mapping, "jira_issue"))
        .strip()
        .upper()
    )
    hours_text = _raw(raw, mapping, "hours")
    notes = _raw(raw, mapping, "notes")

    employee = _find_employee_by_id(employee_id_text) or _find_employee(employee_text)
    if employee is None:
        messages.append(
            {"code": "missing_user", "message": "Employee could not be matched."}
        )
    else:
        parsed["employee_id"] = employee.id
        parsed["employee_name"] = employee.full_name or employee.user.username

    work_date = _parse_date_value(date_text)
    if work_date is None:
        messages.append(
            {"code": "invalid_date", "message": "Date is missing or invalid."}
        )
    else:
        parsed["work_date"] = work_date.isoformat()

    start_time = _parse_time_value(start_time_text)
    if start_time_text and start_time is None:
        messages.append(
            {"code": "invalid_start_time", "message": "Start time is invalid."}
        )
    elif start_time is not None:
        parsed["start_time"] = start_time.isoformat()

    end_time = _parse_time_value(end_time_text)
    if end_time_text and end_time is None:
        messages.append({"code": "invalid_end_time", "message": "End time is invalid."})
    elif end_time is not None:
        parsed["end_time"] = end_time.isoformat()

    hours = _parse_hours(hours_text)
    if hours is None or hours <= 0 or hours > 24:
        messages.append(
            {"code": "invalid_hours", "message": "Hours must be between 0 and 24."}
        )
    else:
        parsed["hours"] = str(hours.quantize(Decimal("0.01")))

    project = _find_project_by_id(project_id_text) or _find_project(project_text)
    task = _find_task_by_id(task_id_text) or _find_task(task_text, jira_issue, project)
    if task and not project:
        project = task.project
    if project is None:
        messages.append(
            {
                "code": "missing_project",
                "message": "Project/client could not be matched.",
            }
        )
    else:
        parsed["project_id"] = project.id
        parsed["project_name"] = project.name
    if task:
        parsed["task_id"] = task.id
        parsed["task_name"] = task.name
        jira_issue = jira_issue or task.jira_issue_key
    parsed["jira_issue_key"] = jira_issue
    parsed["notes"] = notes

    if employee and project and work_date and hours and hours > 0:
        fingerprint = canonical_duplicate_fingerprint(
            employee_id=employee.id,
            work_date=work_date,
            project_id=project.id,
            task_id=task.id if task else None,
            jira_issue_key=jira_issue,
            hours=hours,
            notes=notes,
        )
        duplicate = TimeEntry.objects.filter(duplicate_fingerprint=fingerprint).first()
        if duplicate:
            parsed["duplicate_entry_id"] = duplicate.id
            messages.append(
                {"code": "duplicate", "message": "Matching time entry already exists."}
            )
        parsed["duplicate_fingerprint"] = fingerprint
    return parsed, messages


def _raw(raw: dict[str, Any], mapping: dict[str, str], field: str) -> str:
    column = mapping.get(field)
    if not column:
        return ""
    return str(raw.get(column, "") or "").strip()


def _find_employee(value: str):
    from core.models import UserProfile

    needle = (value or "").strip()
    if not needle:
        return None
    return (
        UserProfile.objects.select_related("user")
        .filter(
            models.Q(full_name__iexact=needle)
            | models.Q(email_address__iexact=needle)
            | models.Q(user__email__iexact=needle)
            | models.Q(user__username__iexact=needle)
        )
        .first()
    )


def _find_employee_by_id(value: str):
    from core.models import UserProfile

    try:
        employee_id = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return UserProfile.objects.select_related("user").filter(pk=employee_id).first()


def _find_project(value: str):
    needle = (value or "").strip()
    if not needle:
        return None
    return Project.objects.filter(
        models.Q(name__iexact=needle) | models.Q(client__iexact=needle)
    ).first()


def _find_project_by_id(value: str):
    try:
        project_id = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return Project.objects.filter(pk=project_id).first()


def _find_task_by_id(value: str):
    try:
        task_id = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return TimeTask.objects.filter(pk=task_id, is_active=True).first()


def _find_task(task_text: str, jira_issue: str, project: Project | None):
    if jira_issue:
        task = TimeTask.objects.filter(
            jira_issue_key=jira_issue, is_active=True
        ).first()
        if task:
            return task
    if not task_text:
        return None
    queryset = TimeTask.objects.filter(name__iexact=task_text, is_active=True)
    if project:
        queryset = queryset.filter(project=project)
    return queryset.select_related("project").first()


def _parse_date_value(value: str):
    parsed = parse_date(value)
    if parsed:
        return parsed
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _parse_time_value(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    parsed = parse_time(text)
    if parsed:
        return parsed.replace(tzinfo=None)
    for fmt in ("%I:%M%p", "%I:%M %p", "%I:%M:%S%p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(text.upper(), fmt).time()
        except (TypeError, ValueError):
            continue
    return None


def _parse_hours(value: str) -> Decimal | None:
    text = str(value or "").strip().lower().replace("h", "")
    if ":" in text:
        parts = text.split(":", 1)
        try:
            return Decimal(parts[0]) + (Decimal(parts[1]) / Decimal("60"))
        except (InvalidOperation, ValueError):
            return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _refresh_batch_counts(batch: TimeImportBatch):
    rows = list(TimeImportRow.objects.filter(batch=batch))
    batch.total_rows = len(rows)
    batch.valid_rows = sum(1 for row in rows if row.status == ImportRowStatus.VALID)
    batch.error_rows = sum(1 for row in rows if row.status == ImportRowStatus.ERROR)
    batch.skipped_rows = sum(1 for row in rows if row.status == ImportRowStatus.SKIPPED)
    batch.committed_rows = sum(
        1 for row in rows if row.status == ImportRowStatus.COMMITTED
    )
    if batch.error_rows and batch.valid_rows:
        batch.status = ImportBatchStatus.PREVIEWED
    elif batch.error_rows and not batch.valid_rows:
        batch.status = ImportBatchStatus.NEEDS_MAPPING
    elif batch.valid_rows:
        batch.status = ImportBatchStatus.PREVIEWED
    batch.save(
        update_fields=[
            "total_rows",
            "valid_rows",
            "error_rows",
            "skipped_rows",
            "committed_rows",
            "status",
            "updated_at",
        ]
    )


@transaction.atomic
def commit_document_import(*, user, batch: TimeImportBatch) -> TimeImportBatch:
    require_document_import_admin(user)
    validate_batch_rows(batch)
    actor = profile_for_user(user)
    for row in batch.rows.select_for_update().all():
        if row.status != ImportRowStatus.VALID:
            continue
        parsed = row.parsed_data
        if parsed.get("duplicate_entry_id"):
            _skip_duplicate_row(row, parsed["duplicate_entry_id"])
            continue
        entry = TimeEntry(
            employee_id=parsed["employee_id"],
            project_id=parsed["project_id"],
            task_id=parsed.get("task_id"),
            work_date=parse_date(parsed["work_date"]),
            start_time=parse_time(parsed.get("start_time", "")),
            end_time=parse_time(parsed.get("end_time", "")),
            hours=Decimal(parsed["hours"]),
            notes=parsed.get("notes", ""),
            source_type=TimeEntrySourceType.DOCUMENT_IMPORT,
            source_external_id=f"document:{batch.id}:{row.id}",
            source_metadata={
                "source": "document_import",
                "file_name": batch.file_name,
                "import_batch_id": batch.id,
                "table_index": row.table_index,
                "sheet_name": row.sheet_name,
                "row_number": row.row_number,
                "original_row_fingerprint": row.original_row_fingerprint,
                "jira_issue_key": parsed.get("jira_issue_key", ""),
                "start_time": parsed.get("start_time"),
                "end_time": parsed.get("end_time"),
            },
        )
        entry.duplicate_fingerprint = fingerprint_for_entry(entry)
        duplicate = find_duplicate(entry)
        if duplicate:
            _skip_duplicate_row(row, duplicate.id)
            continue
        try:
            with transaction.atomic():
                entry.full_clean()
                entry.save()
        except IntegrityError:
            duplicate = find_duplicate(entry)
            if duplicate:
                _skip_duplicate_row(row, duplicate.id)
            else:
                _error_row(row, "integrity_error", "Time entry could not be saved.")
            continue
        except DjangoValidationError as exc:
            _error_row(row, "validation_error", _validation_message(exc))
            continue
        row.committed_entry = entry
        row.status = ImportRowStatus.COMMITTED
        row.save(update_fields=["committed_entry", "status", "updated_at"])
        log_time_entry_event(entry, TimeEntryAuditEventType.IMPORTED, actor)
    _refresh_batch_counts(batch)
    if batch.committed_rows and (batch.error_rows or batch.skipped_rows):
        batch.status = ImportBatchStatus.PARTIALLY_COMMITTED
    elif batch.committed_rows or (batch.skipped_rows and not batch.error_rows):
        batch.status = ImportBatchStatus.COMMITTED
    elif batch.error_rows:
        batch.status = ImportBatchStatus.FAILED
    batch.save(update_fields=["status", "updated_at"])
    return batch


def _skip_duplicate_row(row: TimeImportRow, duplicate_id: int):
    messages = [
        message
        for message in row.validation_messages
        if message.get("code") != "duplicate"
    ]
    row.parsed_data = {**row.parsed_data, "duplicate_entry_id": duplicate_id}
    row.status = ImportRowStatus.SKIPPED
    row.validation_messages = [
        *messages,
        {"code": "duplicate", "message": "Matching time entry already exists."},
    ]
    row.save(
        update_fields=["parsed_data", "status", "validation_messages", "updated_at"]
    )


def _error_row(row: TimeImportRow, code: str, message: str):
    row.status = ImportRowStatus.ERROR
    row.validation_messages = [
        *row.validation_messages,
        {"code": code, "message": message},
    ]
    row.save(update_fields=["status", "validation_messages", "updated_at"])


def _validation_message(exc: DjangoValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return "; ".join(
            f"{field}: {', '.join(messages)}"
            for field, messages in exc.message_dict.items()
        )
    if hasattr(exc, "messages"):
        return "; ".join(exc.messages)
    return str(exc)


# Imported late in helpers to avoid exposing service callers to Django model module.
from django.db import models  # noqa: E402
