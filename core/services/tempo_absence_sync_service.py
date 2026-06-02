from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from time import sleep
from typing import Any

import requests
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.enums import (
    ProjectStatus,
    ProjectType,
    TimeEntryAuditEventType,
    TimeEntrySourceType,
    TimeEntryStatus,
)
from core.models import (
    JiraIssueMapping,
    LeaveRequest,
    Project,
    TempoAbsenceSync,
    TempoAbsenceSyncSettings,
    TimeEntry,
    TimeTask,
)
from core.services.jira_oauth import JiraReauthRequired
from core.services.jira_oauth import get_valid_access_token as jira_get_valid_token
from core.services.tempo_oauth import TempoReauthRequired
from core.services.tempo_oauth import get_valid_access_token as tempo_get_valid_token
from core.services.time_tracking_service import (
    fingerprint_for_entry,
    log_time_entry_event,
)

RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class TempoAbsenceSyncError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def working_days(start_date: date, end_date: date) -> list[date]:
    days = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def sync_leave_request(leave_request_id: int) -> dict[str, int]:
    leave_request = (
        LeaveRequest.objects.select_related("employee__user")
        .filter(pk=leave_request_id)
        .first()
    )
    if leave_request is None:
        return {"synced": 0, "failed": 0, "deleted": 0, "skipped": 0}
    if leave_request.status == LeaveRequest.Status.APPROVED:
        return sync_approved_leave(leave_request)
    if leave_request.status in {
        LeaveRequest.Status.CANCELLED,
        LeaveRequest.Status.REJECTED,
    }:
        return delete_leave_syncs(leave_request)
    return {"synced": 0, "failed": 0, "deleted": 0, "skipped": 0}


def retry_failed_syncs(limit: int | None = None) -> dict[str, int]:
    queryset = TempoAbsenceSync.objects.filter(
        status=TempoAbsenceSync.Status.FAILED
    ).order_by("updated_at")
    if limit:
        queryset = queryset[:limit]
    leave_ids = list(queryset.values_list("leave_request_id", flat=True).distinct())
    totals = {"synced": 0, "failed": 0, "deleted": 0, "skipped": 0}
    for leave_id in leave_ids:
        result = sync_leave_request(leave_id)
        for key, value in result.items():
            totals[key] = totals.get(key, 0) + value
    return totals


def sync_approved_leave(leave_request: LeaveRequest) -> dict[str, int]:
    settings = TempoAbsenceSyncSettings.get_solo()
    desired_dates = set(working_days(leave_request.start_date, leave_request.end_date))
    existing = {
        row.work_date: row
        for row in TempoAbsenceSync.objects.filter(leave_request=leave_request)
    }
    totals = {"synced": 0, "failed": 0, "deleted": 0, "skipped": 0}

    for obsolete in sorted(set(existing) - desired_dates):
        if _delete_sync_row(existing[obsolete]):
            totals["deleted"] += 1
        else:
            totals["failed"] += 1

    for work_date in sorted(desired_dates):
        sync_row, _ = TempoAbsenceSync.objects.get_or_create(
            leave_request=leave_request,
            work_date=work_date,
            defaults={
                "employee": leave_request.employee,
                "leave_type": leave_request.leave_type,
                "status": TempoAbsenceSync.Status.PENDING,
            },
        )
        try:
            if not settings.enabled:
                _mark_skipped(sync_row, "absence_sync_disabled")
                totals["skipped"] += 1
                continue
            _sync_one_day(leave_request, sync_row, settings)
            totals["synced"] += 1
        except TempoAbsenceSyncError as exc:
            _mark_failed(sync_row, exc.code, str(exc))
            totals["failed"] += 1
        except Exception as exc:
            _mark_failed(sync_row, "unexpected_error", str(exc))
            totals["failed"] += 1
    return totals


def delete_leave_syncs(leave_request: LeaveRequest) -> dict[str, int]:
    totals = {"synced": 0, "failed": 0, "deleted": 0, "skipped": 0}
    for sync_row in TempoAbsenceSync.objects.filter(leave_request=leave_request):
        if _delete_sync_row(sync_row):
            totals["deleted"] += 1
        else:
            totals["failed"] += 1
    return totals


def _sync_one_day(
    leave_request: LeaveRequest,
    sync_row: TempoAbsenceSync,
    settings: TempoAbsenceSyncSettings,
) -> None:
    employee = leave_request.employee
    issue_key = settings.issue_key_for(leave_request.leave_type)
    if not issue_key:
        raise TempoAbsenceSyncError(
            "absence_issue_not_configured",
            "No Jira issue key configured for this leave type.",
        )

    sync_row.employee = employee
    sync_row.leave_type = leave_request.leave_type
    sync_row.jira_issue_key = issue_key
    sync_row.status = TempoAbsenceSync.Status.PENDING
    sync_row.save(
        update_fields=[
            "employee",
            "leave_type",
            "jira_issue_key",
            "status",
            "updated_at",
        ]
    )

    issue = _resolve_issue(employee.user, issue_key)
    mapping = _resolve_or_create_issue_mapping(issue_key, issue)

    time_entry = _upsert_time_entry(leave_request, sync_row, settings, mapping, issue)
    tempo_worklog = _upsert_tempo_worklog(
        leave_request, sync_row, settings, issue, time_entry
    )
    tempo_id = str(tempo_worklog.get("tempoWorklogId") or tempo_worklog.get("id") or "")
    if not tempo_id:
        raise TempoAbsenceSyncError(
            "tempo_worklog_id_missing", "Tempo response did not include a worklog id."
        )

    now = timezone.now()
    metadata = dict(time_entry.source_metadata or {})
    metadata.update(
        {
            "tempo_worklog_id": tempo_id,
            "jira_issue_key": issue_key,
            "jira_issue_id": issue["id"],
            "sync_status": TempoAbsenceSync.Status.SYNCED,
        }
    )
    time_entry.source_metadata = metadata
    time_entry.save(update_fields=["source_metadata", "updated_at"])

    sync_row.time_entry = time_entry
    sync_row.jira_issue_id = issue["id"]
    sync_row.tempo_worklog_id = tempo_id
    sync_row.status = TempoAbsenceSync.Status.SYNCED
    sync_row.error_code = ""
    sync_row.last_error = ""
    sync_row.last_synced_at = now
    sync_row.payload_snapshot = _payload_snapshot(
        leave_request, sync_row, settings, issue
    )
    sync_row.save()


def _resolve_issue(user, issue_key: str) -> dict[str, str]:
    try:
        token, connection = jira_get_valid_token(user)
    except JiraReauthRequired as exc:
        raise TempoAbsenceSyncError("jira_reauth_required", str(exc)) from exc

    url = (
        f"https://api.atlassian.com/ex/jira/{connection.cloud_id}"
        f"/rest/api/3/issue/{issue_key}"
    )
    response = _request_with_retries(
        "get",
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"fields": "id,key"},
    )
    if response.status_code == 401:
        raise TempoAbsenceSyncError("jira_reauth_required", "Jira OAuth rejected.")
    if response.status_code >= 400:
        raise TempoAbsenceSyncError(
            "jira_issue_lookup_failed",
            f"Jira issue lookup failed: HTTP {response.status_code}.",
        )
    payload = response.json()
    issue_id = str(payload.get("id") or "")
    if not issue_id:
        raise TempoAbsenceSyncError(
            "jira_issue_id_missing", "Jira issue response did not include id."
        )
    return {"id": issue_id, "key": str(payload.get("key") or issue_key).upper()}


def _resolve_or_create_issue_mapping(
    issue_key: str, issue: dict[str, str]
) -> JiraIssueMapping:
    mapping = (
        JiraIssueMapping.objects.select_related("task__project")
        .filter(jira_issue_key=issue_key, is_active=True)
        .first()
    )
    if mapping is not None:
        if not mapping.jira_issue_id and issue.get("id"):
            mapping.jira_issue_id = issue["id"]
            mapping.save(update_fields=["jira_issue_id", "updated_at"])
        return mapping

    project = Project.objects.filter(name="BloomHub Absences").first()
    if project is None:
        project = Project.objects.create(
            name="BloomHub Absences",
            client="",
            project_type=ProjectType.INTERNAL,
            status=ProjectStatus.ACTIVE,
            description="Auto-created project for BloomHub leave synced to Tempo.",
        )
    changed = False
    if project.project_type != ProjectType.INTERNAL:
        project.project_type = ProjectType.INTERNAL
        changed = True
    if project.status != ProjectStatus.ACTIVE:
        project.status = ProjectStatus.ACTIVE
        changed = True
    if changed:
        project.save(update_fields=["project_type", "status", "updated_at"])

    jira_project_key = issue_key.split("-", 1)[0] if "-" in issue_key else ""
    task, _ = TimeTask.objects.get_or_create(
        jira_issue_key=issue_key,
        defaults={
            "project": project,
            "name": f"BloomHub Leave ({issue_key})",
            "description": "Auto-created task for BloomHub leave synced to Tempo.",
            "jira_project_key": jira_project_key,
            "is_active": True,
        },
    )
    task_changed = False
    if task.project_id != project.id:
        task.project = project
        task_changed = True
    if not task.is_active:
        task.is_active = True
        task_changed = True
    if not task.jira_project_key and jira_project_key:
        task.jira_project_key = jira_project_key
        task_changed = True
    if task_changed:
        task.save(
            update_fields=["project", "is_active", "jira_project_key", "updated_at"]
        )

    mapping, _ = JiraIssueMapping.objects.update_or_create(
        jira_issue_key=issue_key,
        defaults={
            "jira_issue_id": issue.get("id", ""),
            "task": task,
            "is_active": True,
        },
    )
    return mapping


def _upsert_time_entry(
    leave_request: LeaveRequest,
    sync_row: TempoAbsenceSync,
    settings: TempoAbsenceSyncSettings,
    mapping: JiraIssueMapping,
    issue: dict[str, str],
) -> TimeEntry:
    external_id = f"leave:{leave_request.id}:{sync_row.work_date.isoformat()}"
    existing_entry = TimeEntry.objects.filter(
        source_type=TimeEntrySourceType.BLOOMHUB_LEAVE,
        source_external_id=external_id,
    ).first()
    preserved_tempo_id = sync_row.tempo_worklog_id or str(
        (existing_entry.source_metadata or {}).get("tempo_worklog_id", "")
        if existing_entry
        else ""
    )
    if preserved_tempo_id and not sync_row.tempo_worklog_id:
        sync_row.tempo_worklog_id = preserved_tempo_id
        sync_row.save(update_fields=["tempo_worklog_id", "updated_at"])
    metadata = {
        "leave_request_id": leave_request.id,
        "leave_type": leave_request.leave_type,
        "tempo_worklog_id": preserved_tempo_id,
        "jira_issue_key": issue["key"],
        "jira_issue_id": issue["id"],
        "sync_status": sync_row.status,
    }
    notes = (
        f"BloomHub leave: {leave_request.get_leave_type_display()} "
        f"({leave_request.start_date} to {leave_request.end_date})"
    )
    entry, created = TimeEntry.objects.get_or_create(
        source_type=TimeEntrySourceType.BLOOMHUB_LEAVE,
        source_external_id=external_id,
        defaults={
            "employee": leave_request.employee,
            "project": mapping.task.project,
            "task": mapping.task,
            "work_date": sync_row.work_date,
            "start_time": settings.default_start_time,
            "hours": settings.daily_hours,
            "notes": notes,
            "status": TimeEntryStatus.APPROVED,
            "approved_at": timezone.now(),
            "approved_by": leave_request.approver,
            "source_metadata": metadata,
            "duplicate_fingerprint": "",
        },
    )
    if not created:
        entry.employee = leave_request.employee
        entry.project = mapping.task.project
        entry.task = mapping.task
        entry.work_date = sync_row.work_date
        entry.start_time = settings.default_start_time
        entry.hours = settings.daily_hours
        entry.notes = notes
        entry.status = TimeEntryStatus.APPROVED
        entry.approved_at = entry.approved_at or timezone.now()
        entry.approved_by = entry.approved_by or leave_request.approver
        entry.source_metadata = {**(entry.source_metadata or {}), **metadata}
    entry.duplicate_fingerprint = fingerprint_for_entry(entry)
    entry.full_clean()
    entry.save()
    log_time_entry_event(
        entry,
        (
            TimeEntryAuditEventType.IMPORTED
            if created
            else TimeEntryAuditEventType.UPDATED
        ),
        leave_request.approver,
        "Synced BloomHub approved leave to local time entry.",
        metadata,
    )
    return entry


def _upsert_tempo_worklog(
    leave_request: LeaveRequest,
    sync_row: TempoAbsenceSync,
    settings: TempoAbsenceSyncSettings,
    issue: dict[str, str],
    time_entry: TimeEntry,
) -> dict[str, Any]:
    try:
        token, tempo_connection = tempo_get_valid_token(leave_request.employee.user)
    except TempoReauthRequired as exc:
        raise TempoAbsenceSyncError("tempo_reauth_required", str(exc)) from exc

    try:
        _jira_token, jira_connection = jira_get_valid_token(leave_request.employee.user)
    except JiraReauthRequired as exc:
        raise TempoAbsenceSyncError("jira_reauth_required", str(exc)) from exc

    author_account_id = (
        tempo_connection.tempo_account_id or jira_connection.jira_account_id
    )
    if not author_account_id:
        raise TempoAbsenceSyncError(
            "tempo_author_account_missing",
            "Tempo authorAccountId is not available for employee.",
        )

    payload = _tempo_payload(
        author_account_id=author_account_id,
        issue_id=issue["id"],
        work_date=sync_row.work_date,
        settings=settings,
        leave_request=leave_request,
        time_entry=time_entry,
    )
    base_url = (tempo_connection.base_url or "https://api.tempo.io/4").rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if sync_row.tempo_worklog_id:
        response = _request_with_retries(
            "put",
            f"{base_url}/worklogs/{sync_row.tempo_worklog_id}",
            headers=headers,
            json=payload,
        )
    else:
        response = _request_with_retries(
            "post", f"{base_url}/worklogs", headers=headers, json=payload
        )
    if response.status_code == 401:
        raise TempoAbsenceSyncError("tempo_reauth_required", "Tempo OAuth rejected.")
    if response.status_code >= 400:
        raise TempoAbsenceSyncError(
            "tempo_worklog_write_failed",
            f"Tempo worklog write failed: HTTP {response.status_code}.",
        )
    return response.json()


def _delete_sync_row(sync_row: TempoAbsenceSync) -> bool:
    try:
        if sync_row.tempo_worklog_id:
            _delete_tempo_worklog(sync_row)
        if sync_row.time_entry_id:
            sync_row.time_entry.delete()
        sync_row.status = TempoAbsenceSync.Status.DELETED
        sync_row.last_error = ""
        sync_row.error_code = ""
        sync_row.save(
            update_fields=["status", "last_error", "error_code", "updated_at"]
        )
        return True
    except TempoAbsenceSyncError as exc:
        _mark_failed(sync_row, exc.code, str(exc))
        return False


def _delete_tempo_worklog(sync_row: TempoAbsenceSync) -> None:
    try:
        token, tempo_connection = tempo_get_valid_token(sync_row.employee.user)
    except TempoReauthRequired as exc:
        raise TempoAbsenceSyncError("tempo_reauth_required", str(exc)) from exc

    base_url = (tempo_connection.base_url or "https://api.tempo.io/4").rstrip("/")
    response = _request_with_retries(
        "delete",
        f"{base_url}/worklogs/{sync_row.tempo_worklog_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if response.status_code == 404:
        return
    if response.status_code == 401:
        raise TempoAbsenceSyncError("tempo_reauth_required", "Tempo OAuth rejected.")
    if response.status_code >= 400:
        raise TempoAbsenceSyncError(
            "tempo_worklog_delete_failed",
            f"Tempo worklog delete failed: HTTP {response.status_code}.",
        )


def _tempo_payload(
    *,
    author_account_id: str,
    issue_id: str,
    work_date: date,
    settings: TempoAbsenceSyncSettings,
    leave_request: LeaveRequest,
    time_entry: TimeEntry,
) -> dict[str, Any]:
    return {
        "authorAccountId": author_account_id,
        "issueId": issue_id,
        "startDate": work_date.isoformat(),
        "startTime": settings.default_start_time.isoformat(),
        "timeSpentSeconds": int(Decimal(settings.daily_hours) * Decimal("3600")),
        "description": time_entry.notes,
        "remainingEstimateSeconds": 0,
    }


def _payload_snapshot(
    leave_request: LeaveRequest,
    sync_row: TempoAbsenceSync,
    settings: TempoAbsenceSyncSettings,
    issue: dict[str, str],
) -> dict[str, Any]:
    return {
        "leave_request_id": leave_request.id,
        "leave_type": leave_request.leave_type,
        "work_date": sync_row.work_date.isoformat(),
        "jira_issue_key": issue["key"],
        "jira_issue_id": issue["id"],
        "daily_hours": str(settings.daily_hours),
        "default_start_time": settings.default_start_time.isoformat(),
    }


def _request_with_retries(method: str, url: str, **kwargs):
    timeout = kwargs.pop("timeout", 30)
    for attempt in range(1, 4):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            if attempt == 3:
                raise ValidationError(
                    f"Tempo absence sync request failed: {exc}"
                ) from exc
            sleep(0.1 * attempt)
            continue
        if response.status_code not in RETRY_STATUS_CODES:
            return response
        if attempt == 3:
            return response
        retry_after = response.headers.get("Retry-After")
        sleep(min(float(retry_after or 0.1 * attempt), 1.0))
    raise ValidationError("Tempo absence sync request failed after retries.")


def _mark_failed(sync_row: TempoAbsenceSync, code: str, message: str) -> None:
    sync_row.status = TempoAbsenceSync.Status.FAILED
    sync_row.error_code = code
    sync_row.last_error = message
    sync_row.retry_count += 1
    sync_row.save(
        update_fields=[
            "status",
            "error_code",
            "last_error",
            "retry_count",
            "updated_at",
        ]
    )


def _mark_skipped(sync_row: TempoAbsenceSync, code: str) -> None:
    sync_row.status = TempoAbsenceSync.Status.SKIPPED
    sync_row.error_code = code
    sync_row.last_error = code
    sync_row.save(update_fields=["status", "error_code", "last_error", "updated_at"])


def enqueue_leave_sync_on_commit(leave_request_id: int) -> None:
    transaction.on_commit(lambda: sync_leave_request(leave_request_id))
