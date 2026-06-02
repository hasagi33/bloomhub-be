from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from time import sleep
from typing import Any

import requests
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.enums import (
    ImportBatchSource,
    ProjectStatus,
    ProjectType,
    TimeEntryAuditEventType,
    TimeEntrySourceChangeFlag,
    TimeEntrySourceType,
)
from core.models import (
    JiraConnection,
    JiraIssueMapping,
    JiraProjectMapping,
    JiraUserConnection,
    JiraUserMapping,
    Project,
    TimeEntry,
    TimeTask,
    UserProfile,
)
from core.services.jira_oauth import (
    JiraReauthRequired,
    get_valid_access_token,
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
class JiraImportFilters:
    date_from: date
    date_to: date
    employee_id: int | None = None
    project_id: int | None = None
    jira_project_key: str = ""
    jira_issue_key: str = ""
    worklog_id: str = ""


@dataclass(frozen=True)
class JiraAssignedIssueImportOptions:
    employee_id: int
    max_results: int = 1000
    dry_run: bool = False


def require_jira_admin(user):
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return
    from core.services.time_tracking_service import has_time_tracking_permission

    if not has_time_tracking_permission(user, "approve_team_timesheets"):
        raise PermissionDenied(
            "You do not have permission to manage Jira time imports."
        )


@dataclass
class JiraApiContext:
    """Auth + base-URL bundle for Jira REST calls. Supports admin basic auth or per-user OAuth bearer."""

    base_url: str
    auth_email: str = ""
    api_token: str = ""
    bearer_token: str = ""

    @classmethod
    def from_admin_connection(cls, connection: JiraConnection) -> JiraApiContext:
        return cls(
            base_url=connection.base_url.rstrip("/"),
            auth_email=connection.auth_email,
            api_token=connection.get_api_token(),
        )

    @classmethod
    def from_user_oauth(
        cls, user_connection: JiraUserConnection, access_token: str
    ) -> JiraApiContext:
        return cls(
            base_url=f"https://api.atlassian.com/ex/jira/{user_connection.cloud_id}",
            bearer_token=access_token,
        )

    def request_kwargs(self) -> dict:
        if self.bearer_token:
            return {
                "headers": {
                    "Authorization": f"Bearer {self.bearer_token}",
                    "Accept": "application/json",
                },
                "auth": None,
            }
        return {
            "headers": {"Accept": "application/json"},
            "auth": (self.auth_email, self.api_token),
        }


def build_jira_api_context_for_employee(
    employee_id: int | None,
    *,
    fallback_connection: JiraConnection,
) -> JiraApiContext:
    """Prefer per-user OAuth token for the given employee, else fall back to admin global token."""
    if employee_id is not None:
        profile = (
            UserProfile.objects.filter(id=employee_id).select_related("user").first()
        )
        if profile is not None:
            try:
                token, user_conn = get_valid_access_token(profile.user)
                return JiraApiContext.from_user_oauth(user_conn, token)
            except JiraReauthRequired:
                # FE will surface a reconnect prompt via /status/; admin token covers the import meanwhile.
                pass
    return JiraApiContext.from_admin_connection(fallback_connection)


def _jira_get(context: JiraApiContext, url: str, **kwargs):
    max_attempts = 3
    timeout = kwargs.pop("timeout", 30)
    req_kwargs = context.request_kwargs()
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(
                url,
                auth=req_kwargs["auth"],
                headers=req_kwargs["headers"],
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise ValidationError(f"Jira request failed: {exc}") from exc
            sleep(0.1 * attempt)
            continue
        if response.status_code not in {429, 500, 502, 503, 504}:
            return response
        if attempt == max_attempts:
            retry_after = response.headers.get("Retry-After", "")
            raise ValidationError(
                f"Jira rate-limit or service error: HTTP {response.status_code}."
                + (f" Retry after {retry_after} seconds." if retry_after else "")
            )
        retry_after = response.headers.get("Retry-After")
        delay = min(float(retry_after or 0.1 * attempt), 1.0)
        sleep(delay)
    raise ValidationError("Jira request failed after retries.")


def test_jira_connection(connection: JiraConnection) -> dict[str, Any]:
    if (
        not connection.base_url
        or not connection.auth_email
        or not connection.has_api_token
    ):
        return {
            "status": "error",
            "message": "Jira base URL, auth email, and API token are required.",
            "metadata": {},
        }
    context = JiraApiContext.from_admin_connection(connection)
    try:
        response = _jira_get(
            context,
            f"{context.base_url}/rest/api/3/myself",
            timeout=15,
        )
    except (requests.RequestException, ValidationError) as exc:
        return {"status": "error", "message": str(exc), "metadata": {}}
    if response.status_code >= 400:
        return {
            "status": "error",
            "message": f"Jira returned HTTP {response.status_code}.",
            "metadata": {"status_code": response.status_code},
        }
    payload = response.json()
    return {
        "status": "success",
        "message": "Jira connection OK.",
        "metadata": {
            "account_id": payload.get("accountId", ""),
            "display_name": payload.get("displayName", ""),
        },
    }


def fetch_jira_worklogs(
    connection: JiraConnection,
    filters: JiraImportFilters,
) -> list[dict[str, Any]]:
    # Prefer per-user OAuth context when filters.employee_id has a JiraUserConnection;
    # fall back to the admin global token otherwise. Admin connection sanity checks
    # only apply when we actually need to fall back to it.
    context = build_jira_api_context_for_employee(
        filters.employee_id, fallback_connection=connection
    )
    using_admin_fallback = not context.bearer_token
    if using_admin_fallback:
        if not connection.enabled:
            raise ValidationError("Jira connection is disabled.")
        if (
            not connection.base_url
            or not connection.auth_email
            or not connection.has_api_token
        ):
            raise ValidationError("Jira connection is not configured.")

    jql = (
        f'worklogDate >= "{filters.date_from.isoformat()}" '
        f'AND worklogDate <= "{filters.date_to.isoformat()}"'
    )
    if filters.jira_project_key:
        jql += f' AND project = "{filters.jira_project_key}"'
    if filters.jira_issue_key:
        jql += f' AND issue = "{filters.jira_issue_key}"'

    since = int(
        datetime.combine(filters.date_from, time.min)
        .replace(tzinfo=timezone.get_current_timezone())
        .timestamp()
        * 1000
    )
    deleted_ids = _fetch_jira_worklog_change_ids(
        context,
        f"{context.base_url}/rest/api/3/worklog/deleted",
        since,
    )
    updated_ids = _fetch_jira_worklog_change_ids(
        context,
        f"{context.base_url}/rest/api/3/worklog/updated",
        since,
    )

    worklogs: list[dict[str, Any]] = []
    for issue in _fetch_jira_search_issues(context, jql):
        issue_key = issue.get("key", "")
        issue_id = issue.get("id", "")
        for worklog in _fetch_jira_issue_worklogs(context, issue_key):
            row = dict(worklog)
            row["issueKey"] = issue_key
            row["issueId"] = issue_id
            if str(row.get("id", "")) in updated_ids:
                row["updatedViaJiraWorklogAPI"] = True
            worklogs.append(row)
    existing_ids = {
        str(worklog.get("id") or worklog.get("worklogId") or "") for worklog in worklogs
    }
    missing_deleted_ids = deleted_ids - existing_ids
    if filters.worklog_id:
        missing_deleted_ids = {filters.worklog_id} & missing_deleted_ids
    worklogs.extend(_deleted_jira_worklog_payloads(missing_deleted_ids))
    return worklogs


def _deleted_jira_worklog_payloads(worklog_ids: set[str]) -> list[dict[str, Any]]:
    if not worklog_ids:
        return []
    entries = TimeEntry.objects.filter(
        source_type=TimeEntrySourceType.JIRA,
        source_external_id__in=worklog_ids,
    )
    payloads = []
    for entry in entries:
        metadata = entry.source_metadata or {}
        worklog_id = str(entry.source_external_id)
        payloads.append(
            {
                "id": worklog_id,
                "worklogId": worklog_id,
                "issueKey": metadata.get("jira_issue_key")
                or metadata.get("issue_key")
                or "",
                "issueId": metadata.get("issue_id") or "",
                "author_account_id": metadata.get("author_account_id") or "",
                "author_display_name": metadata.get("author_display_name") or "",
                "started": metadata.get("started") or "",
                "timeSpentSeconds": metadata.get("time_spent_seconds") or 0,
                "comment": metadata.get("comment") or "",
                "updated": metadata.get("updated") or "",
                "deleted": True,
            }
        )
    return payloads


def _fetch_jira_search_issues(
    context: JiraApiContext, jql: str
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    next_page_token = ""
    max_results = 100
    url = f"{context.base_url}/rest/api/3/search/jql"
    while True:
        params = {
            "jql": jql,
            "fields": ["key", "id", "summary", "project"],
            "maxResults": max_results,
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        response = _jira_get(
            context,
            url,
            params=params,
            timeout=30,
        )
        if response.status_code >= 400:
            raise ValidationError(f"Jira returned HTTP {response.status_code}.")
        payload = response.json()
        page = payload.get("issues", [])
        issues.extend(page)
        next_page_token = payload.get("nextPageToken") or ""
        if payload.get("isLast", True) or not page or not next_page_token:
            break
    return issues


def _jql_quote(value: str) -> str:
    return '"' + (value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def fetch_jira_assigned_issues(
    connection: JiraConnection,
    *,
    jira_account_id: str,
    max_results: int = 1000,
    employee_id: int | None = None,
) -> list[dict[str, Any]]:
    if not connection.enabled:
        raise ValidationError("Jira connection is disabled.")
    if (
        not connection.base_url
        or not connection.auth_email
        or not connection.has_api_token
    ):
        raise ValidationError("Jira connection is not configured.")

    context = build_jira_api_context_for_employee(
        employee_id, fallback_connection=connection
    )
    issues: list[dict[str, Any]] = []
    next_page_token = ""
    page_size = min(max(max_results, 1), 100)
    url = f"{context.base_url}/rest/api/3/search/jql"
    jql = f"assignee = {_jql_quote(jira_account_id)} ORDER BY updated DESC"
    while len(issues) < max_results:
        params = {
            "jql": jql,
            "fields": [
                "key",
                "id",
                "summary",
                "description",
                "project",
                "status",
                "assignee",
                "updated",
            ],
            "maxResults": min(page_size, max_results - len(issues)),
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        response = _jira_get(context, url, params=params, timeout=30)
        if response.status_code >= 400:
            raise ValidationError(f"Jira returned HTTP {response.status_code}.")
        payload = response.json()
        page = payload.get("issues", [])
        issues.extend(page)
        next_page_token = payload.get("nextPageToken") or ""
        if payload.get("isLast", True) or not page or not next_page_token:
            break
    return issues[:max_results]


def _jira_doc_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        chunks: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                text = node.get("text")
                if text:
                    chunks.append(str(text))
                for child in node.get("content") or []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return " ".join(chunks).strip()
    return ""


def _issue_import_payload(issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields") or {}
    project = fields.get("project") or {}
    issue_key = (issue.get("key") or "").strip().upper()
    project_key = (project.get("key") or _jira_project_key(issue_key)).strip().upper()
    return {
        "jira_issue_key": issue_key,
        "jira_issue_id": str(issue.get("id") or ""),
        "summary": (fields.get("summary") or issue_key).strip() or issue_key,
        "description": _jira_doc_text(fields.get("description")),
        "jira_project_key": project_key,
        "jira_project_name": (project.get("name") or project_key).strip()
        or project_key,
        "status_name": ((fields.get("status") or {}).get("name") or "").strip(),
        "updated": fields.get("updated") or "",
    }


def _assigned_issue_row_error(payload: dict[str, Any], code: str, message: str):
    return {
        "jira_issue_key": payload.get("jira_issue_key", ""),
        "jira_project_key": payload.get("jira_project_key", ""),
        "project_id": None,
        "task_id": None,
        "action": "error",
        "validation_messages": [{"code": code, "message": message}],
    }


def _task_name_for_issue(
    *, project: Project, summary: str, issue_key: str, current_task: TimeTask | None
) -> str:
    base_name = (summary or issue_key).strip()[:150] or issue_key
    conflict = TimeTask.objects.filter(project=project, name=base_name)
    if current_task is not None:
        conflict = conflict.exclude(pk=current_task.pk)
    if not conflict.exists():
        return base_name
    suffix = f" ({issue_key})"
    return f"{base_name[: 150 - len(suffix)]}{suffix}"


@transaction.atomic
def import_assigned_jira_issues(
    *,
    user,
    connection: JiraConnection,
    options: JiraAssignedIssueImportOptions,
    raw_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    require_jira_admin(user)
    mapping = (
        JiraUserMapping.objects.filter(employee_id=options.employee_id, is_active=True)
        .select_related("employee__user")
        .first()
    )
    if mapping is None:
        raise ValidationError(
            "Selected employee does not have an active Jira user mapping."
        )

    issues = (
        raw_issues
        if raw_issues is not None
        else fetch_jira_assigned_issues(
            connection,
            jira_account_id=mapping.jira_account_id,
            max_results=options.max_results,
            employee_id=options.employee_id,
        )
    )
    counts = {
        "created_projects": 0,
        "created_tasks": 0,
        "updated_tasks": 0,
        "created_issue_mappings": 0,
        "updated_issue_mappings": 0,
        "errors": 0,
    }
    rows = []

    for issue in issues:
        payload = _issue_import_payload(issue)
        issue_key = payload["jira_issue_key"]
        project_key = payload["jira_project_key"]
        if not issue_key:
            counts["errors"] += 1
            rows.append(
                _assigned_issue_row_error(
                    payload, "missing_issue_key", "Jira issue key is missing."
                )
            )
            continue
        if not project_key:
            counts["errors"] += 1
            rows.append(
                _assigned_issue_row_error(
                    payload, "missing_project_key", "Jira project key is missing."
                )
            )
            continue

        project_mapping = (
            JiraProjectMapping.objects.filter(jira_project_key=project_key)
            .select_related("project")
            .first()
        )
        project = (
            project_mapping.project
            if project_mapping and project_mapping.is_active
            else None
        )
        created_project = False
        if project is None:
            if options.dry_run:
                project_id = None
            else:
                project = Project(
                    name=payload["jira_project_name"] or project_key,
                    description=f"Imported from Jira project {project_key}.",
                    project_type=ProjectType.INTERNAL,
                    status=ProjectStatus.ACTIVE,
                )
                project.full_clean()
                project.save()
                if project_mapping:
                    project_mapping.jira_project_name = payload["jira_project_name"]
                    project_mapping.project = project
                    project_mapping.is_active = True
                    project_mapping.save()
                else:
                    JiraProjectMapping.objects.create(
                        jira_project_key=project_key,
                        jira_project_name=payload["jira_project_name"],
                        project=project,
                        is_active=True,
                    )
                project_id = project.id
            created_project = True
            counts["created_projects"] += 1
        else:
            project_id = project.id

        task = TimeTask.objects.filter(jira_issue_key=issue_key).first()
        task_created = task is None
        task_changed = False
        if options.dry_run:
            task_id = task.id if task else None
        elif task_created:
            task_name = _task_name_for_issue(
                project=project,
                summary=payload["summary"],
                issue_key=issue_key,
                current_task=None,
            )
            task = TimeTask(
                project=project,
                name=task_name,
                description=payload["description"],
                jira_issue_key=issue_key,
                jira_project_key=project_key,
                is_active=True,
            )
            task.full_clean()
            task.save()
            task_id = task.id
            counts["created_tasks"] += 1
        else:
            task_name = _task_name_for_issue(
                project=project,
                summary=payload["summary"],
                issue_key=issue_key,
                current_task=task,
            )
            updates = {
                "project": project,
                "name": task_name,
                "description": payload["description"],
                "jira_project_key": project_key,
                "is_active": True,
            }
            for field, value in updates.items():
                if getattr(task, field) != value:
                    setattr(task, field, value)
                    task_changed = True
            if task_changed:
                task.full_clean()
                task.save()
                counts["updated_tasks"] += 1
            task_id = task.id

        if task_created and options.dry_run:
            counts["created_tasks"] += 1
        elif not task_created and options.dry_run:
            counts["updated_tasks"] += 1

        issue_mapping = JiraIssueMapping.objects.filter(
            jira_issue_key=issue_key
        ).first()
        issue_mapping_created = issue_mapping is None
        if options.dry_run:
            if issue_mapping_created:
                counts["created_issue_mappings"] += 1
            else:
                counts["updated_issue_mappings"] += 1
        elif issue_mapping_created:
            JiraIssueMapping.objects.create(
                jira_issue_key=issue_key,
                jira_issue_id=payload["jira_issue_id"],
                task=task,
                is_active=True,
            )
            counts["created_issue_mappings"] += 1
        else:
            changed = False
            if issue_mapping.jira_issue_id != payload["jira_issue_id"]:
                issue_mapping.jira_issue_id = payload["jira_issue_id"]
                changed = True
            if issue_mapping.task_id != task.id:
                issue_mapping.task = task
                changed = True
            if not issue_mapping.is_active:
                issue_mapping.is_active = True
                changed = True
            if changed:
                issue_mapping.save()
                counts["updated_issue_mappings"] += 1

        action_parts = []
        if created_project:
            action_parts.append("create_project")
        action_parts.append("create_task" if task_created else "update_task")
        action_parts.append(
            "create_issue_mapping" if issue_mapping_created else "update_issue_mapping"
        )
        rows.append(
            {
                "jira_issue_key": issue_key,
                "jira_issue_id": payload["jira_issue_id"],
                "jira_project_key": project_key,
                "jira_project_name": payload["jira_project_name"],
                "project_id": project_id,
                "task_id": task_id,
                "task_name": task.name if task else payload["summary"][:150],
                "action": ",".join(action_parts),
                "validation_messages": [],
            }
        )

    return {
        "source_type": TimeEntrySourceType.JIRA,
        "employee_id": options.employee_id,
        "jira_account_id": mapping.jira_account_id,
        "dry_run": options.dry_run,
        "row_count": len(rows),
        "counts": counts,
        "rows": rows,
    }


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


def _suggest_employee(*values: str) -> dict[str, Any] | None:
    lookup_values = [value for value in values if value]
    if not lookup_values:
        return None

    candidates = []
    compact_values = [_compact(value) for value in lookup_values]
    for employee in UserProfile.objects.select_related("user").all():
        display_name = employee.full_name or employee.user.get_full_name()
        employee_values = [display_name, employee.email_address, employee.user.email]
        employee_compacts = [_compact(value or "") for value in employee_values]
        score = 0
        reason = ""
        for raw_value, compact_value in zip(lookup_values, compact_values):
            if not compact_value:
                continue
            for employee_value, employee_compact in zip(
                employee_values, employee_compacts
            ):
                if not employee_compact:
                    continue
                if employee_compact == compact_value:
                    score = max(score, 100)
                    reason = f"exact match: {raw_value}"
                elif len(employee_compact) >= 3 and (
                    employee_compact in compact_value
                    or compact_value in employee_compact
                ):
                    score = max(score, 80)
                    reason = f"partial match: {raw_value}"
        if score:
            candidates.append((score, (display_name or "").lower(), employee, reason))

    if not candidates:
        return None
    score, _, employee, reason = sorted(
        candidates, key=lambda item: (-item[0], item[1])
    )[0]
    return {
        "id": employee.id,
        "name": employee.full_name
        or employee.user.get_full_name()
        or employee.user.username,
        "confidence": score,
        "match_reason": reason,
    }


def _suggest_task(issue_key: str, issue_summary: str, project_id: int | None):
    query = TimeTask.objects.select_related("project").filter(is_active=True)
    if project_id:
        query = query.filter(project_id=project_id)
    issue_compact = _compact(issue_summary)
    candidates = []
    for task in query:
        score = 0
        reason = ""
        if task.jira_issue_key and task.jira_issue_key.upper() == issue_key:
            score = 100
            reason = f"exact Jira issue match: {issue_key}"
        elif issue_compact:
            task_compact = _compact(task.name)
            if task_compact == issue_compact:
                score = 90
                reason = f"exact task name match: {issue_summary}"
            elif len(task_compact) >= 3 and (
                task_compact in issue_compact or issue_compact in task_compact
            ):
                score = 70
                reason = f"partial task name match: {issue_summary}"
        if score:
            candidates.append((score, task.name.lower(), task, reason))
    if not candidates:
        return None
    score, _, task, reason = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    return {
        "id": task.id,
        "name": task.name,
        "project_id": task.project_id,
        "project_name": task.project.name,
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


def _existing_employee_payload(mapping) -> dict[str, Any] | None:
    if not mapping:
        return None
    employee = mapping.employee
    return {
        "mapping_id": mapping.id,
        "employee_id": employee.id,
        "employee_name": employee.full_name
        or employee.user.get_full_name()
        or employee.user.username,
    }


def _existing_task_payload(mapping) -> dict[str, Any] | None:
    if not mapping:
        return None
    task = mapping.task
    return {
        "mapping_id": mapping.id,
        "task_id": task.id,
        "task_name": task.name,
        "project_id": task.project_id,
        "project_name": task.project.name,
    }


def _jira_project_key(issue_key: str) -> str:
    issue_key = (issue_key or "").strip().upper()
    if "-" not in issue_key:
        return ""
    return issue_key.split("-", 1)[0]


def discover_jira_project_ids(
    connection: JiraConnection,
    *,
    date_from: date,
    date_to: date,
    limit: int = 1000,
) -> dict[str, Any]:
    if (
        not connection.base_url
        or not connection.auth_email
        or not connection.has_api_token
    ):
        raise ValidationError("Jira base URL, auth email, and API token are required.")

    context = JiraApiContext.from_admin_connection(connection)
    jql = (
        f'worklogDate >= "{date_from.isoformat()}" '
        f'AND worklogDate <= "{date_to.isoformat()}"'
    )
    issues_raw = _fetch_jira_search_issues(context, jql)
    issues: dict[str, dict[str, Any]] = {}
    projects: dict[str, dict[str, Any]] = {}
    users: dict[str, dict[str, Any]] = {}
    worklog_count = 0

    for raw_issue in issues_raw:
        issue_key = (raw_issue.get("key") or "").strip().upper()
        if not issue_key:
            continue
        fields = raw_issue.get("fields") or {}
        project = fields.get("project") or {}
        project_key = (project.get("key") or _jira_project_key(issue_key)).upper()
        project_name = project.get("name") or ""
        issue_item = {
            "jira_issue_key": issue_key,
            "jira_issue_id": str(raw_issue.get("id") or ""),
            "jira_issue_summary": fields.get("summary") or "",
            "jira_project_key": project_key,
            "jira_project_name": project_name,
        }
        issues.setdefault(issue_key, issue_item)
        if project_key:
            projects.setdefault(
                project_key,
                {
                    "jira_project_key": project_key,
                    "jira_project_name": project_name,
                },
            )

        for worklog in _fetch_jira_issue_worklogs(context, issue_key):
            if worklog_count >= limit:
                break
            row = normalize_jira_worklog({**worklog, "issueKey": issue_key})
            if not _passes_filters(
                row, JiraImportFilters(date_from=date_from, date_to=date_to)
            ):
                continue
            worklog_count += 1
            author = worklog.get("author") or {}
            account_id = row["author_account_id"]
            if account_id:
                users.setdefault(
                    account_id,
                    {
                        "jira_account_id": account_id,
                        "jira_display_name": row["author_display_name"],
                        "jira_email": author.get("emailAddress") or "",
                    },
                )
        if worklog_count >= limit:
            break

    def user_payload(item):
        mapping = (
            JiraUserMapping.objects.filter(
                jira_account_id=item["jira_account_id"], is_active=True
            )
            .select_related("employee__user")
            .first()
        )
        suggested = _suggest_employee(item["jira_display_name"], item["jira_email"])
        return {
            **item,
            "existing_mapping": _existing_employee_payload(mapping),
            "suggested_employee": suggested,
            "employee_id": (
                mapping.employee_id if mapping else (suggested or {}).get("id")
            ),
        }

    def project_payload(item):
        mapping = (
            JiraProjectMapping.objects.filter(
                jira_project_key=item["jira_project_key"], is_active=True
            )
            .select_related("project")
            .first()
        )
        suggested = _suggest_project(
            item["jira_project_key"], item["jira_project_name"]
        )
        return {
            **item,
            "existing_mapping": _existing_project_payload(mapping),
            "suggested_project": suggested,
            "project_id": (
                mapping.project_id if mapping else (suggested or {}).get("id")
            ),
        }

    def issue_payload(item):
        mapping = (
            JiraIssueMapping.objects.filter(
                jira_issue_key=item["jira_issue_key"], is_active=True
            )
            .select_related("task__project")
            .first()
        )
        project_mapping = (
            JiraProjectMapping.objects.filter(
                jira_project_key=item["jira_project_key"], is_active=True
            )
            .select_related("project")
            .first()
        )
        suggested_project = _suggest_project(
            item["jira_project_key"], item["jira_project_name"]
        )
        project_id = (
            project_mapping.project_id
            if project_mapping
            else (suggested_project or {}).get("id")
        )
        suggested_task = _suggest_task(
            item["jira_issue_key"], item["jira_issue_summary"], project_id
        )
        return {
            **item,
            "existing_mapping": _existing_task_payload(mapping),
            "suggested_task": suggested_task,
            "task_id": mapping.task_id if mapping else (suggested_task or {}).get("id"),
            "project_id": mapping.task.project_id if mapping else project_id,
        }

    return {
        "date_from": date_from,
        "date_to": date_to,
        "counts": {
            "worklogs": worklog_count,
            "users": len(users),
            "projects": len(projects),
            "issues": len(issues),
        },
        "users": [
            user_payload(item)
            for item in sorted(users.values(), key=lambda item: item["jira_account_id"])
        ],
        "projects": [
            project_payload(item)
            for item in sorted(
                projects.values(), key=lambda item: item["jira_project_key"]
            )
        ],
        "issues": [
            issue_payload(item)
            for item in sorted(issues.values(), key=lambda item: item["jira_issue_key"])
        ],
    }


def _fetch_jira_issue_worklogs(
    context: JiraApiContext, issue_key: str
) -> list[dict[str, Any]]:
    worklogs: list[dict[str, Any]] = []
    start_at = 0
    max_results = 100
    url = f"{context.base_url}/rest/api/3/issue/{issue_key}/worklog"
    while True:
        response = _jira_get(
            context,
            url,
            params={"maxResults": max_results, "startAt": start_at},
            timeout=30,
        )
        if response.status_code >= 400:
            raise ValidationError(
                f"Jira worklog fetch failed for {issue_key}: HTTP {response.status_code}."
            )
        payload = response.json()
        page = payload.get("worklogs", [])
        worklogs.extend(page)
        total = int(payload.get("total") or len(worklogs))
        if not page or len(worklogs) >= total:
            break
        start_at += len(page)
    return worklogs


def _fetch_jira_worklog_change_ids(
    context: JiraApiContext, url: str, since: int
) -> set[str]:
    response = _jira_get(context, url, params={"since": since}, timeout=30)
    if response.status_code >= 400:
        return set()
    values = response.json().get("values", [])
    return {str(item.get("worklogId") or item.get("id") or "") for item in values}


def _started_datetime(raw: str):
    started = parse_datetime(raw or "")
    if started is None:
        return None
    if timezone.is_naive(started):
        started = timezone.make_aware(started, timezone=timezone.get_current_timezone())
    return started


def _comment_text(comment: Any) -> str:
    if isinstance(comment, str):
        return comment
    if isinstance(comment, dict):
        chunks: list[str] = []
        for block in comment.get("content", []):
            for item in block.get("content", []):
                text = item.get("text")
                if text:
                    chunks.append(text)
        return " ".join(chunks)
    return ""


def normalize_jira_worklog(raw: dict[str, Any]) -> dict[str, Any]:
    author = raw.get("author") or {}
    issue_key = (raw.get("issueKey") or raw.get("issue_key") or "").strip().upper()
    started = _started_datetime(raw.get("started", ""))
    return {
        "worklog_id": str(raw.get("id") or raw.get("worklogId") or ""),
        "issue_key": issue_key,
        "issue_id": str(raw.get("issueId") or raw.get("issue_id") or ""),
        "author_account_id": author.get("accountId")
        or raw.get("author_account_id")
        or "",
        "author_display_name": author.get("displayName")
        or raw.get("author_display_name")
        or "",
        "started": raw.get("started", ""),
        "work_date": started.date() if started else None,
        "time_spent_seconds": int(raw.get("timeSpentSeconds") or 0),
        "hours": (Decimal(int(raw.get("timeSpentSeconds") or 0)) / Decimal("3600")),
        "comment": _comment_text(raw.get("comment")),
        "updated": raw.get("updated", ""),
        "deleted": bool(raw.get("deleted", False)),
        "raw": raw,
    }


def _passes_filters(row: dict[str, Any], filters: JiraImportFilters) -> bool:
    if not row["worklog_id"]:
        return False
    if filters.worklog_id and row["worklog_id"] != filters.worklog_id:
        return False
    if filters.jira_issue_key and row["issue_key"] != filters.jira_issue_key:
        return False
    if row["work_date"] is None:
        return True
    return filters.date_from <= row["work_date"] <= filters.date_to


def preview_jira_worklogs(
    *,
    filters: JiraImportFilters,
    raw_worklogs: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for raw in raw_worklogs:
        row = normalize_jira_worklog(raw)
        if not _passes_filters(row, filters):
            continue
        messages = []
        user_mapping = (
            JiraUserMapping.objects.filter(
                jira_account_id=row["author_account_id"], is_active=True
            )
            .select_related("employee__user")
            .first()
        )
        issue_mapping = (
            JiraIssueMapping.objects.filter(
                jira_issue_key=row["issue_key"], is_active=True
            )
            .select_related("task__project")
            .first()
        )
        project_mapping = (
            JiraProjectMapping.objects.filter(
                jira_project_key=row["issue_key"].split("-")[0], is_active=True
            )
            .select_related("project")
            .first()
        )

        employee = user_mapping.employee if user_mapping else None
        task = issue_mapping.task if issue_mapping else None
        project = (
            task.project
            if task
            else (project_mapping.project if project_mapping else None)
        )
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
                    "message": "Jira author is not mapped to a BloomHub employee.",
                }
            )
        if project is None:
            messages.append(
                {
                    "code": "missing_project_mapping",
                    "message": "Jira issue/project is not mapped to a BloomHub project.",
                }
            )
        if row["work_date"] is None:
            messages.append(
                {
                    "code": "invalid_started",
                    "message": "Jira worklog started datetime is missing or invalid.",
                }
            )
        if row["hours"] <= 0 or row["hours"] > 24:
            messages.append(
                {
                    "code": "invalid_hours",
                    "message": "Jira worklog time spent must be between 0 and 24 hours.",
                }
            )

        existing = TimeEntry.objects.filter(
            source_type=TimeEntrySourceType.JIRA,
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
                jira_issue_key=row["issue_key"],
                hours=row["hours"],
                notes=row["comment"],
            )
            duplicate = (
                TimeEntry.objects.filter(duplicate_fingerprint=fingerprint)
                .exclude(
                    source_type=TimeEntrySourceType.JIRA,
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
            action = (
                "update" if row["deleted"] or _source_changed(existing, row) else "skip"
            )
            status = "valid"
            if row["deleted"]:
                messages.append(
                    {
                        "code": "source_deleted",
                        "message": "Jira worklog was deleted; existing BloomHub entry needs review.",
                    }
                )
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
                "issue_key": row["issue_key"],
                "employee_id": employee.id if employee else None,
                "employee_name": (
                    (employee.full_name or employee.user.username) if employee else ""
                ),
                "project_id": project.id if project else None,
                "project_name": project.name if project else "",
                "task_id": task.id if task else None,
                "task_name": task.name if task else "",
                "work_date": row["work_date"].isoformat() if row["work_date"] else None,
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
        "source_type": TimeEntrySourceType.JIRA,
        "date_from": filters.date_from.isoformat(),
        "date_to": filters.date_to.isoformat(),
        "row_count": len(rows),
        "valid_count": sum(1 for row in rows if row["status"] == "valid"),
        "error_count": sum(1 for row in rows if row["status"] == "error"),
        "rows": rows,
    }


def _source_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "jira",
        "issue_key": row["issue_key"],
        "jira_issue_key": row["issue_key"],
        "issue_id": row["issue_id"],
        "worklog_id": row["worklog_id"],
        "author_account_id": row["author_account_id"],
        "author_display_name": row["author_display_name"],
        "started": row["started"],
        "time_spent_seconds": row["time_spent_seconds"],
        "comment": row["comment"],
        "updated": row["updated"],
        "source_change_flag": TimeEntrySourceChangeFlag.NONE,
        "deleted": row["deleted"],
    }


def _source_changed(entry: TimeEntry, row: dict[str, Any]) -> bool:
    metadata = entry.source_metadata or {}
    return any(
        [
            metadata.get("updated") != row["updated"],
            metadata.get("time_spent_seconds") != row["time_spent_seconds"],
            metadata.get("comment", "") != row["comment"],
            str(metadata.get("started", "")) != row["started"],
        ]
    )


@transaction.atomic
def commit_jira_worklogs(
    *,
    user,
    filters: JiraImportFilters,
    raw_worklogs: list[dict[str, Any]],
) -> dict[str, Any]:
    preview = preview_jira_worklogs(filters=filters, raw_worklogs=raw_worklogs)
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
        existing = TimeEntry.objects.filter(
            source_type=TimeEntrySourceType.JIRA,
            source_external_id=row["worklog_id"],
        ).first()
        metadata = dict(row["source_metadata"])
        if existing:
            metadata["source_change_flag"] = (
                TimeEntrySourceChangeFlag.DELETED
                if metadata.get("deleted")
                else TimeEntrySourceChangeFlag.REVIEW_REQUIRED
            )
            if existing.status == "approved":
                existing.source_metadata = {**existing.source_metadata, **metadata}
                existing.save(update_fields=["source_metadata", "updated_at"])
                log_time_entry_event(
                    existing,
                    TimeEntryAuditEventType.SOURCE_CHANGED,
                    actor,
                    "Jira worklog changed or was deleted after approval; review required.",
                    metadata,
                )
            else:
                existing.employee_id = row["employee_id"]
                existing.project_id = row["project_id"]
                existing.task_id = row["task_id"]
                existing.work_date = work_date
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
            hours=hours,
            notes=row["comment"],
            source_type=TimeEntrySourceType.JIRA,
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
        source_type=ImportBatchSource.JIRA,
        filters=filters,
        preview=preview,
        counts=counts,
        entry_ids_by_worklog=entry_ids_by_worklog,
    )
    return {
        "source_type": TimeEntrySourceType.JIRA,
        "counts": counts,
        "entry_ids": committed,
        "batch_id": batch.id,
        "preview": preview,
    }
