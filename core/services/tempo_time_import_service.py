from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from time import sleep
from typing import Any

import requests
from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.enums import (
    ImportBatchSource,
    TimeEntryAuditEventType,
    TimeEntrySourceChangeFlag,
    TimeEntrySourceType,
)
from core.models import (
    JiraProjectMapping,
    Project,
    TempoAccountMapping,
    TempoConnection,
    TempoProjectMapping,
    TempoTeamMapping,
    TempoUserMapping,
    TimeEntry,
    TimeTask,
)
from core.services.time_import_batch_service import persist_external_import_batch
from core.services.time_tracking_service import (
    canonical_duplicate_fingerprint,
    find_duplicate,
    fingerprint_for_entry,
    log_time_entry_event,
    profile_for_user,
)


@dataclass(frozen=True)
class TempoImportFilters:
    date_from: date
    date_to: date
    employee_id: int | None = None
    tempo_team_id: str = ""
    tempo_account_id: str = ""
    tempo_account_key: str = ""
    tempo_project_id: str = ""
    project_id: int | None = None
    jira_issue_key: str = ""
    worklog_id: str = ""


def require_tempo_admin(user):
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return
    from core.services.time_tracking_service import has_time_tracking_permission

    if not has_time_tracking_permission(user, "approve_team_timesheets"):
        raise PermissionDenied(
            "You do not have permission to manage Tempo time imports."
        )


def _headers(connection: TempoConnection) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {connection.get_api_token()}",
    }


def _tempo_get(connection: TempoConnection, url: str, **kwargs):
    max_attempts = 3
    timeout = kwargs.pop("timeout", 30)
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(
                url,
                headers=_headers(connection),
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise ValidationError(f"Tempo request failed: {exc}") from exc
            sleep(0.1 * attempt)
            continue
        if response.status_code not in {429, 500, 502, 503, 504}:
            return response
        if attempt == max_attempts:
            retry_after = response.headers.get("Retry-After", "")
            raise ValidationError(
                f"Tempo rate-limit or service error: HTTP {response.status_code}."
                + (f" Retry after {retry_after} seconds." if retry_after else "")
            )
        retry_after = response.headers.get("Retry-After")
        delay = min(float(retry_after or 0.1 * attempt), 1.0)
        sleep(delay)
    raise ValidationError("Tempo request failed after retries.")


def test_tempo_connection(connection: TempoConnection) -> dict[str, Any]:
    if not connection.base_url or not connection.has_api_token:
        return {
            "status": "error",
            "message": "Tempo base URL and API token are required.",
            "metadata": {},
        }
    try:
        response = _tempo_get(
            connection,
            f"{connection.base_url.rstrip('/')}/worklogs",
            params={
                "from": date.today().isoformat(),
                "to": date.today().isoformat(),
                "limit": 1,
            },
            timeout=15,
        )
    except (requests.RequestException, ValidationError) as exc:
        return {"status": "error", "message": str(exc), "metadata": {}}
    if response.status_code >= 400:
        return {
            "status": "error",
            "message": f"Tempo returned HTTP {response.status_code}.",
            "metadata": {"status_code": response.status_code},
        }
    payload = response.json()
    return {
        "status": "success",
        "message": "Tempo connection OK.",
        "metadata": {"result_count": len(payload.get("results", []))},
    }


def fetch_tempo_worklogs(
    connection: TempoConnection,
    filters: TempoImportFilters,
) -> list[dict[str, Any]]:
    if not connection.enabled:
        raise ValidationError("Tempo connection is disabled.")
    if not connection.base_url or not connection.has_api_token:
        raise ValidationError("Tempo connection is not configured.")

    params: dict[str, Any] = {
        "from": filters.date_from.isoformat(),
        "to": filters.date_to.isoformat(),
        "limit": 1000,
    }
    if filters.tempo_account_id:
        params["accountId"] = filters.tempo_account_id
    if filters.tempo_team_id:
        params["teamId"] = filters.tempo_team_id
    if filters.tempo_project_id:
        params["projectId"] = filters.tempo_project_id
    if filters.jira_issue_key:
        params["issue"] = filters.jira_issue_key

    response = _tempo_get(
        connection,
        f"{connection.base_url.rstrip('/')}/worklogs",
        params=params,
        timeout=30,
    )
    if response.status_code >= 400:
        raise ValidationError(f"Tempo returned HTTP {response.status_code}.")
    return response.json().get("results", [])


def _payload_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


def _tempo_paginated_results(
    connection: TempoConnection,
    path: str,
    *,
    limit: int = 1000,
) -> tuple[list[dict[str, Any]], str | None]:
    base_url = connection.base_url.rstrip("/")
    collected: list[dict[str, Any]] = []
    offset = 0
    page_limit = min(limit, 50)

    while len(collected) < limit:
        response = _tempo_get(
            connection,
            f"{base_url}/{path.lstrip('/')}",
            params={"offset": offset, "limit": page_limit},
            timeout=30,
        )
        if response.status_code >= 400:
            return [], f"{path}: Tempo returned HTTP {response.status_code}."

        payload = response.json()
        page = _payload_results(payload)
        collected.extend(page)
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        next_url = metadata.get("next")
        if not next_url or not page:
            break
        offset += page_limit

    return collected[:limit], None


def _compact(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _suggest_project(*values: str) -> dict[str, Any] | None:
    lookup_values = [value for value in values if value]
    if not lookup_values:
        return None

    candidates = []
    compact_values = [_compact(value) for value in lookup_values]
    for project in Project.objects.all():
        compact_name = _compact(project.name)
        if not compact_name:
            continue
        score = 0
        reason = ""
        for raw_value, compact_value in zip(lookup_values, compact_values):
            if not compact_value:
                continue
            if compact_name == compact_value:
                score = max(score, 100)
                reason = f"exact name match: {raw_value}"
            elif len(compact_name) >= 3 and (
                compact_name in compact_value or compact_value in compact_name
            ):
                score = max(score, 80)
                reason = f"partial name match: {raw_value}"
        if score:
            candidates.append((score, project.name.lower(), project, reason))

    if not candidates:
        return None
    score, _, project, reason = sorted(
        candidates, key=lambda item: (-item[0], item[1])
    )[0]
    return {
        "id": project.id,
        "name": project.name,
        "confidence": score,
        "match_reason": reason,
    }


def _existing_project_payload(mapping) -> dict[str, Any] | None:
    if not mapping:
        return None
    return {
        "mapping_id": mapping.id,
        "project_id": mapping.project_id,
        "project_name": mapping.project.name,
    }


def _account_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    account_key = _id(raw.get("key") or raw.get("accountKey")).strip().upper()
    account_id = _id(raw.get("id") or raw.get("accountId") or account_key)
    if not account_id and not account_key:
        return None
    return {
        "tempo_account_id": account_id,
        "tempo_account_key": account_key,
        "tempo_account_name": raw.get("name") or raw.get("accountName") or "",
    }


def _project_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
    source = scope.get("source") if isinstance(scope.get("source"), dict) else {}
    project_id = _id(raw.get("id") or raw.get("projectId"))
    project_key = (
        raw.get("key")
        or raw.get("projectKey")
        or source.get("reference")
        or scope.get("id")
        or ""
    )
    project_key = _id(project_key).strip().upper()
    if not project_id and not project_key:
        return None
    return {
        "tempo_project_id": project_id or project_key,
        "tempo_project_key": project_key,
        "tempo_project_name": raw.get("name")
        or raw.get("projectName")
        or source.get("title")
        or "",
    }


def _team_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    team_id = _id(raw.get("id") or raw.get("teamId"))
    if not team_id:
        return None
    return {
        "tempo_team_id": team_id,
        "tempo_team_name": raw.get("name") or raw.get("teamName") or "",
    }


def discover_tempo_project_ids(
    connection: TempoConnection,
    *,
    date_from: date,
    date_to: date,
    limit: int = 1000,
) -> dict[str, Any]:
    if not connection.base_url or not connection.has_api_token:
        raise ValidationError("Tempo base URL and API token are required.")

    worklog_response = _tempo_get(
        connection,
        f"{connection.base_url.rstrip('/')}/worklogs",
        params={
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "limit": limit,
        },
        timeout=30,
    )
    if worklog_response.status_code >= 400:
        raise ValidationError(f"Tempo returned HTTP {worklog_response.status_code}.")

    rows = [
        normalize_tempo_worklog(raw)
        for raw in _payload_results(worklog_response.json())
    ]
    accounts: dict[str, dict[str, Any]] = {}
    projects: dict[str, dict[str, Any]] = {}
    teams: dict[str, dict[str, Any]] = {}
    discovery_errors = []

    account_results, account_error = _tempo_paginated_results(
        connection, "accounts", limit=limit
    )
    project_results, project_error = _tempo_paginated_results(
        connection, "projects", limit=limit
    )
    team_results, team_error = _tempo_paginated_results(
        connection, "teams", limit=limit
    )

    for error in [account_error, project_error, team_error]:
        if error:
            discovery_errors.append(error)

    for raw in account_results:
        item = _account_item(raw)
        if item:
            accounts.setdefault(item["tempo_account_id"], item)

    for raw in project_results:
        item = _project_item(raw)
        if item:
            projects.setdefault(item["tempo_project_id"], item)

    for raw in team_results:
        item = _team_item(raw)
        if item:
            teams.setdefault(item["tempo_team_id"], item)

    for row in rows:
        if row["tempo_account_id"]:
            accounts.setdefault(
                row["tempo_account_id"],
                {
                    "tempo_account_id": row["tempo_account_id"],
                    "tempo_account_key": row["tempo_account_key"],
                    "tempo_account_name": row["tempo_account_name"],
                },
            )
        if row["tempo_project_id"]:
            projects.setdefault(
                row["tempo_project_id"],
                {
                    "tempo_project_id": row["tempo_project_id"],
                    "tempo_project_key": row["tempo_project_key"],
                    "tempo_project_name": row["tempo_project_name"],
                },
            )
        if row["tempo_team_id"]:
            teams.setdefault(
                row["tempo_team_id"],
                {
                    "tempo_team_id": row["tempo_team_id"],
                    "tempo_team_name": row["tempo_team_name"],
                },
            )

    def account_payload(item):
        mapping = (
            TempoAccountMapping.objects.filter(
                tempo_account_id=item["tempo_account_id"], is_active=True
            )
            .select_related("project")
            .first()
        )
        if mapping is None and item["tempo_account_key"]:
            mapping = (
                TempoAccountMapping.objects.filter(
                    tempo_account_key=item["tempo_account_key"], is_active=True
                )
                .select_related("project")
                .first()
            )
        suggested = _suggest_project(
            item["tempo_account_key"], item["tempo_account_name"]
        )
        return {
            **item,
            "existing_mapping": _existing_project_payload(mapping),
            "suggested_project": suggested,
            "project_id": (
                mapping.project_id if mapping else (suggested or {}).get("id")
            ),
        }

    def project_payload(item):
        mapping = (
            TempoProjectMapping.objects.filter(
                tempo_project_id=item["tempo_project_id"], is_active=True
            )
            .select_related("project")
            .first()
        )
        if mapping is None and item["tempo_project_key"]:
            mapping = (
                TempoProjectMapping.objects.filter(
                    tempo_project_key=item["tempo_project_key"], is_active=True
                )
                .select_related("project")
                .first()
            )
        suggested = _suggest_project(
            item["tempo_project_key"], item["tempo_project_name"]
        )
        return {
            **item,
            "existing_mapping": _existing_project_payload(mapping),
            "suggested_project": suggested,
            "project_id": (
                mapping.project_id if mapping else (suggested or {}).get("id")
            ),
        }

    def team_payload(item):
        mapping = (
            TempoTeamMapping.objects.filter(
                tempo_team_id=item["tempo_team_id"], is_active=True
            )
            .select_related("project")
            .first()
        )
        suggested = _suggest_project(item["tempo_team_name"])
        return {
            **item,
            "existing_mapping": _existing_project_payload(mapping),
            "suggested_project": suggested,
            "project_id": (
                mapping.project_id if mapping else (suggested or {}).get("id")
            ),
        }

    return {
        "date_from": date_from,
        "date_to": date_to,
        "counts": {
            "worklogs": len(rows),
            "accounts": len(accounts),
            "projects": len(projects),
            "teams": len(teams),
        },
        "discovery_errors": discovery_errors,
        "accounts": [
            account_payload(item)
            for item in sorted(
                accounts.values(), key=lambda item: item["tempo_account_id"]
            )
        ],
        "projects": [
            project_payload(item)
            for item in sorted(
                projects.values(), key=lambda item: item["tempo_project_id"]
            )
        ],
        "teams": [
            team_payload(item)
            for item in sorted(teams.values(), key=lambda item: item["tempo_team_id"])
        ],
    }


def _nested(raw: dict[str, Any], *keys: str) -> Any:
    value: Any = raw
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _id(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _jira_project_key(issue_key: str) -> str:
    issue_key = (issue_key or "").strip().upper()
    if "-" not in issue_key:
        return ""
    return issue_key.split("-", 1)[0]


def _issue_key_from_text(value: str) -> str:
    match = re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", value or "", flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def _time_from_value(value: Any):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    raw = str(value).strip()
    if not raw:
        return None
    parsed_time = parse_time(raw)
    if parsed_time is not None:
        return parsed_time.replace(tzinfo=None)
    parsed_datetime = parse_datetime(raw)
    if parsed_datetime is not None:
        return parsed_datetime.time().replace(tzinfo=None)
    for input_format in ("%I:%M%p", "%I:%M %p", "%I:%M:%S%p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(raw.upper(), input_format).time()
        except ValueError:
            continue
    return None


def _end_time(start_time, *, seconds: int, hours: Decimal):
    if start_time is None:
        return None
    duration_seconds = seconds or int(hours * Decimal("3600"))
    return (
        datetime.combine(date.today(), start_time) + timedelta(seconds=duration_seconds)
    ).time()


def _hours(raw: dict[str, Any]) -> Decimal:
    seconds = raw.get("timeSpentSeconds")
    if seconds is None:
        seconds = raw.get("billableSeconds")
    if seconds is not None:
        return Decimal(int(seconds)) / Decimal("3600")
    raw_hours = raw.get("hours")
    return Decimal(str(raw_hours or "0"))


def normalize_tempo_worklog(raw: dict[str, Any]) -> dict[str, Any]:
    author = raw.get("author") or raw.get("worker") or {}
    account = raw.get("account") or {}
    project = raw.get("project") or {}
    team = raw.get("team") or {}
    issue = raw.get("issue") or {}
    work_date = parse_date(raw.get("startDate") or raw.get("date") or "")
    comment = raw.get("description") or raw.get("comment") or ""
    hours = _hours(raw)
    time_spent_seconds = int(raw.get("timeSpentSeconds") or 0)
    start_time = _time_from_value(
        raw.get("startTime")
        or raw.get("start_time")
        or raw.get("started")
        or raw.get("startDateTime")
    )
    end_time = _time_from_value(
        raw.get("endTime") or raw.get("end_time") or raw.get("endDateTime")
    ) or _end_time(start_time, seconds=time_spent_seconds, hours=hours)
    jira_issue_key = (
        (
            issue.get("key")
            or raw.get("issueKey")
            or _nested(raw, "jiraIssue", "key")
            or _issue_key_from_text(comment)
            or ""
        )
        .strip()
        .upper()
    )
    tempo_project_key = (
        (
            project.get("key")
            or raw.get("projectKey")
            or _jira_project_key(jira_issue_key)
        )
        .strip()
        .upper()
    )
    return {
        "worklog_id": _id(raw.get("tempoWorklogId") or raw.get("id")),
        "author_id": _id(
            author.get("accountId") or author.get("id") or raw.get("authorAccountId")
        ),
        "author_display_name": author.get("displayName") or author.get("name") or "",
        "tempo_account_id": _id(account.get("id") or raw.get("accountId")),
        "tempo_account_key": (account.get("key") or raw.get("accountKey") or "")
        .strip()
        .upper(),
        "tempo_account_name": account.get("name") or "",
        "tempo_project_id": _id(project.get("id") or raw.get("projectId")),
        "tempo_project_key": tempo_project_key,
        "tempo_project_name": project.get("name") or "",
        "tempo_team_id": _id(team.get("id") or raw.get("teamId")),
        "tempo_team_name": team.get("name") or "",
        "jira_issue_key": jira_issue_key,
        "work_date": work_date,
        "start_time": start_time,
        "end_time": end_time,
        "hours": hours,
        "comment": comment,
        "time_spent_seconds": time_spent_seconds,
        "updated": raw.get("updatedAt") or raw.get("updated") or "",
        "raw": raw,
    }


def _passes_filters(row: dict[str, Any], filters: TempoImportFilters) -> bool:
    if not row["worklog_id"]:
        return False
    if filters.worklog_id and row["worklog_id"] != filters.worklog_id:
        return False
    if filters.tempo_team_id and row["tempo_team_id"] != filters.tempo_team_id:
        return False
    if filters.tempo_account_id and row["tempo_account_id"] != filters.tempo_account_id:
        return False
    if (
        filters.tempo_account_key
        and row["tempo_account_key"] != filters.tempo_account_key
    ):
        return False
    if filters.tempo_project_id and row["tempo_project_id"] != filters.tempo_project_id:
        return False
    if filters.jira_issue_key and row["jira_issue_key"] != filters.jira_issue_key:
        return False
    if row["work_date"] is None:
        return True
    return filters.date_from <= row["work_date"] <= filters.date_to


def _resolve_project(row: dict[str, Any]):
    project_key = row["tempo_project_key"] or _jira_project_key(row["jira_issue_key"])
    mappings = [
        (
            TempoAccountMapping.objects.filter(
                tempo_account_id=row["tempo_account_id"], is_active=True
            )
            .select_related("project")
            .first()
            if row["tempo_account_id"]
            else None
        ),
        (
            TempoAccountMapping.objects.filter(
                tempo_account_key=row["tempo_account_key"], is_active=True
            )
            .select_related("project")
            .first()
            if row["tempo_account_key"]
            else None
        ),
        (
            TempoProjectMapping.objects.filter(
                tempo_project_id=row["tempo_project_id"], is_active=True
            )
            .select_related("project")
            .first()
            if row["tempo_project_id"]
            else None
        ),
        (
            TempoProjectMapping.objects.filter(
                tempo_project_key=row["tempo_project_key"], is_active=True
            )
            .select_related("project")
            .first()
            if row["tempo_project_key"]
            else None
        ),
        (
            TempoProjectMapping.objects.filter(
                tempo_project_id=project_key, is_active=True
            )
            .select_related("project")
            .first()
            if project_key
            else None
        ),
        (
            JiraProjectMapping.objects.filter(
                jira_project_key=project_key, is_active=True
            )
            .select_related("project")
            .first()
            if project_key
            else None
        ),
        (
            TempoTeamMapping.objects.filter(
                tempo_team_id=row["tempo_team_id"], is_active=True
            )
            .select_related("project")
            .first()
            if row["tempo_team_id"]
            else None
        ),
    ]
    for mapping in mappings:
        if mapping:
            return mapping.project
    task = (
        TimeTask.objects.filter(jira_project_key=project_key, is_active=True)
        .select_related("project")
        .first()
        if project_key
        else None
    )
    if task:
        return task.project
    return None


def preview_tempo_worklogs(
    *,
    filters: TempoImportFilters,
    raw_worklogs: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for raw in raw_worklogs:
        row = normalize_tempo_worklog(raw)
        if not _passes_filters(row, filters):
            continue
        messages = []
        user_mapping = (
            TempoUserMapping.objects.filter(
                tempo_user_id=row["author_id"], is_active=True
            )
            .select_related("employee__user")
            .first()
        )
        employee = user_mapping.employee if user_mapping else None
        project = _resolve_project(row)
        task = None
        if row["jira_issue_key"]:
            task = (
                TimeTask.objects.filter(
                    jira_issue_key=row["jira_issue_key"], is_active=True
                )
                .select_related("project")
                .first()
            )
        if task:
            project = task.project
        if filters.employee_id and (
            employee is None or employee.id != filters.employee_id
        ):
            continue
        if filters.project_id and (project is None or project.id != filters.project_id):
            continue
        if employee is None:
            messages.append(
                {
                    "code": "missing_user_mapping",
                    "message": "Tempo author is not mapped to a BloomHub employee.",
                }
            )
        if project is None:
            messages.append(
                {
                    "code": "missing_project_mapping",
                    "message": "Tempo account/project/team is not mapped to a BloomHub project.",
                }
            )
        if row["work_date"] is None:
            messages.append(
                {
                    "code": "invalid_date",
                    "message": "Tempo worklog date is missing or invalid.",
                }
            )
        if row["hours"] <= 0 or row["hours"] > 24:
            messages.append(
                {
                    "code": "invalid_hours",
                    "message": "Tempo worklog time spent must be between 0 and 24 hours.",
                }
            )

        existing = TimeEntry.objects.filter(
            source_type=TimeEntrySourceType.TEMPO,
            source_external_id=row["worklog_id"],
        ).first()
        duplicate = None
        fingerprint = ""
        if employee and project and row["work_date"] and row["hours"] > 0:
            fingerprint = canonical_duplicate_fingerprint(
                employee_id=employee.id,
                work_date=row["work_date"],
                project_id=project.id,
                task_id=task.id if task else None,
                jira_issue_key=row["jira_issue_key"],
                hours=row["hours"],
                notes=row["comment"],
            )
            duplicate = (
                TimeEntry.objects.filter(duplicate_fingerprint=fingerprint)
                .exclude(
                    source_type=TimeEntrySourceType.TEMPO,
                    source_external_id=row["worklog_id"],
                )
                .first()
            )
            if duplicate:
                messages.append(
                    {
                        "code": "duplicate",
                        "message": "Matching time entry already exists from another source.",
                    }
                )

        status = "valid"
        action = "create"
        if existing:
            action = "update" if _source_changed(existing, row) else "skip"
            status = "valid"
        if duplicate:
            action = "skip"
        blocking_messages = [
            message for message in messages if message["code"] != "duplicate"
        ]
        if blocking_messages and not existing:
            status = "error"

        rows.append(
            {
                "worklog_id": row["worklog_id"],
                "jira_issue_key": row["jira_issue_key"],
                "employee_id": employee.id if employee else None,
                "employee_name": (
                    (employee.full_name or employee.user.username) if employee else ""
                ),
                "project_id": project.id if project else None,
                "project_name": project.name if project else "",
                "task_id": task.id if task else None,
                "task_name": task.name if task else "",
                "work_date": row["work_date"].isoformat() if row["work_date"] else None,
                "start_time": (
                    row["start_time"].isoformat() if row["start_time"] else None
                ),
                "end_time": row["end_time"].isoformat() if row["end_time"] else None,
                "hours": str(row["hours"].quantize(Decimal("0.01"))),
                "comment": row["comment"],
                "status": status,
                "action": action,
                "duplicate_entry_id": duplicate.id if duplicate else None,
                "existing_entry_id": existing.id if existing else None,
                "validation_messages": messages,
                "source_metadata": _source_metadata(row),
                "duplicate_fingerprint": fingerprint,
            }
        )
    return {
        "source_type": TimeEntrySourceType.TEMPO,
        "date_from": filters.date_from.isoformat(),
        "date_to": filters.date_to.isoformat(),
        "row_count": len(rows),
        "valid_count": sum(1 for row in rows if row["status"] == "valid"),
        "error_count": sum(1 for row in rows if row["status"] == "error"),
        "rows": rows,
    }


def _source_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "tempo",
        "tempo_worklog_id": row["worklog_id"],
        "worklog_id": row["worklog_id"],
        "tempo_account_id": row["tempo_account_id"],
        "tempo_account_key": row["tempo_account_key"],
        "tempo_account_name": row["tempo_account_name"],
        "tempo_project_id": row["tempo_project_id"],
        "tempo_project_key": row["tempo_project_key"],
        "tempo_project_name": row["tempo_project_name"],
        "tempo_team_id": row["tempo_team_id"],
        "tempo_team_name": row["tempo_team_name"],
        "jira_issue_key": row["jira_issue_key"],
        "author_account_id": row["author_id"],
        "author_display_name": row["author_display_name"],
        "date": row["work_date"].isoformat() if row["work_date"] else None,
        "start_time": row["start_time"].isoformat() if row["start_time"] else None,
        "end_time": row["end_time"].isoformat() if row["end_time"] else None,
        "time_spent_seconds": row["time_spent_seconds"],
        "comment": row["comment"],
        "updated": row["updated"],
        "source_change_flag": TimeEntrySourceChangeFlag.NONE,
    }


def _source_changed(entry: TimeEntry, row: dict[str, Any]) -> bool:
    metadata = entry.source_metadata or {}
    return any(
        [
            metadata.get("updated") != row["updated"],
            metadata.get("time_spent_seconds") != row["time_spent_seconds"],
            metadata.get("comment", "") != row["comment"],
            metadata.get("date")
            != (row["work_date"].isoformat() if row["work_date"] else None),
            metadata.get("start_time")
            != (row["start_time"].isoformat() if row["start_time"] else None),
            metadata.get("end_time")
            != (row["end_time"].isoformat() if row["end_time"] else None),
        ]
    )


@transaction.atomic
def commit_tempo_worklogs(
    *,
    user,
    filters: TempoImportFilters,
    raw_worklogs: list[dict[str, Any]],
) -> dict[str, Any]:
    preview = preview_tempo_worklogs(filters=filters, raw_worklogs=raw_worklogs)
    actor = profile_for_user(user)
    counts = {"created": 0, "updated": 0, "skipped": 0, "error": 0}
    committed = []
    entry_ids_by_worklog = {}
    for row in preview["rows"]:
        if row["status"] != "valid" or row["action"] == "skip":
            counts["skipped" if row["status"] == "valid" else "error"] += 1
            continue
        work_date = parse_date(row["work_date"])
        hours = Decimal(row["hours"])
        start_time = parse_time(row["start_time"] or "") if row["start_time"] else None
        end_time = parse_time(row["end_time"] or "") if row["end_time"] else None
        existing = TimeEntry.objects.filter(
            source_type=TimeEntrySourceType.TEMPO,
            source_external_id=row["worklog_id"],
        ).first()
        metadata = dict(row["source_metadata"])
        if existing:
            metadata["source_change_flag"] = TimeEntrySourceChangeFlag.REVIEW_REQUIRED
            if existing.status == "approved":
                existing.source_metadata = {**existing.source_metadata, **metadata}
                existing.save(update_fields=["source_metadata", "updated_at"])
                log_time_entry_event(
                    existing,
                    TimeEntryAuditEventType.SOURCE_CHANGED,
                    actor,
                    "Tempo worklog changed after approval; review required.",
                    metadata,
                )
            else:
                existing.employee_id = row["employee_id"]
                existing.project_id = row["project_id"]
                existing.task_id = row["task_id"]
                existing.work_date = work_date
                existing.start_time = start_time
                existing.end_time = end_time
                existing.hours = hours
                existing.notes = row["comment"]
                existing.source_metadata = metadata
                existing.duplicate_fingerprint = fingerprint_for_entry(existing)
                existing.full_clean()
                existing.save()
                log_time_entry_event(existing, TimeEntryAuditEventType.CORRECTED, actor)
            counts["updated"] += 1
            committed.append(existing.id)
            entry_ids_by_worklog[row["worklog_id"]] = existing.id
            continue
        entry = TimeEntry(
            employee_id=row["employee_id"],
            project_id=row["project_id"],
            task_id=row["task_id"],
            work_date=work_date,
            start_time=start_time,
            end_time=end_time,
            hours=hours,
            notes=row["comment"],
            source_type=TimeEntrySourceType.TEMPO,
            source_external_id=row["worklog_id"],
            source_metadata=metadata,
        )
        entry.duplicate_fingerprint = fingerprint_for_entry(entry)
        duplicate = find_duplicate(entry)
        if duplicate:
            counts["skipped"] += 1
            continue
        entry.full_clean()
        entry.save()
        log_time_entry_event(entry, TimeEntryAuditEventType.IMPORTED, actor)
        counts["created"] += 1
        committed.append(entry.id)
        entry_ids_by_worklog[row["worklog_id"]] = entry.id
    batch = persist_external_import_batch(
        user=user,
        source_type=ImportBatchSource.TEMPO,
        filters=filters,
        preview=preview,
        counts=counts,
        entry_ids_by_worklog=entry_ids_by_worklog,
    )
    return {
        "source_type": TimeEntrySourceType.TEMPO,
        "counts": counts,
        "entry_ids": committed,
        "batch_id": batch.id,
        "preview": preview,
    }
