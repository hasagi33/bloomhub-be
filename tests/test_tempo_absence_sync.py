from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import LeaveType, ProjectStatus, ProjectType, TimeEntrySourceType
from core.models import (
    JiraIssueMapping,
    JiraUserConnection,
    LeaveRequest,
    Project,
    TempoAbsenceSync,
    TempoAbsenceSyncSettings,
    TempoUserConnection,
    TimeEntry,
    TimeTask,
)
from core.services.tempo_absence_sync_service import sync_leave_request


class TempoAbsenceSyncTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", email="admin@example.com", password="pw", is_staff=True
        )
        self.employee = User.objects.create_user(
            username="employee", email="employee@example.com", password="pw"
        )
        self.employee.profile.full_name = "Employee One"
        self.employee.profile.save(update_fields=["full_name"])
        self.project = Project.objects.create(
            name="Absences",
            client="Internal",
            project_type=ProjectType.INTERNAL,
            status=ProjectStatus.ACTIVE,
        )
        self.task = TimeTask.objects.create(
            project=self.project,
            name="Leave",
            jira_issue_key="ABS-1",
            jira_project_key="ABS",
        )
        JiraIssueMapping.objects.create(
            jira_issue_key="ABS-1",
            jira_issue_id="10001",
            task=self.task,
            is_active=True,
        )
        self.leave = LeaveRequest.objects.create(
            employee=self.employee.profile,
            leave_type=LeaveType.VACATION,
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            reason="Vacation",
            status=LeaveRequest.Status.APPROVED,
            approver=self.admin.profile,
            approved_date=timezone.now(),
        )

    def test_settings_api_updates_admin_config(self):
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            reverse("core:time_tempo_absence_sync_settings"),
            {
                "enabled": True,
                "default_jira_issue_key": "abs-1",
                "leave_type_issue_keys": {"sick": "abs-2"},
                "daily_hours": "7.50",
                "default_start_time": "08:30:00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["default_jira_issue_key"], "ABS-1")
        self.assertEqual(response.data["leave_type_issue_keys"], {"sick": "ABS-2"})
        self.assertEqual(response.data["daily_hours"], "7.50")

    def test_tempo_sync_route_requires_user_connection_not_404(self):
        self.client.force_authenticate(self.employee)

        response = self.client.post(reverse("core:time_tempo_sync"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "tempo_reauth_required")

    @patch("core.views.commit_tempo_worklogs")
    @patch("core.views.fetch_tempo_worklogs_for_user")
    def test_tempo_sync_route_commits_user_tempo_worklogs(
        self, mock_fetch, mock_commit
    ):
        TempoUserConnection.objects.create(
            user=self.employee,
            tempo_account_id="acct-1",
            base_url="https://api.tempo.io/4",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        mock_fetch.return_value = [{"tempoWorklogId": "tw-1"}]
        mock_commit.return_value = {
            "counts": {"created": 1, "updated": 0, "skipped": 0, "error": 0},
            "batch_id": 123,
        }
        self.client.force_authenticate(self.employee)

        response = self.client.post(reverse("core:time_tempo_sync"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["batch_id"], 123)
        self.assertEqual(response.data["counts"]["created"], 1)
        mock_fetch.assert_called_once()
        mock_commit.assert_called_once()

    def test_missing_jira_connection_records_failed_sync(self):
        TempoAbsenceSyncSettings.objects.update_or_create(
            pk=1,
            defaults={
                "enabled": True,
                "default_jira_issue_key": "ABS-1",
                "daily_hours": "8.00",
            },
        )

        result = sync_leave_request(self.leave.id)

        self.assertEqual(result["failed"], 1)
        sync_row = TempoAbsenceSync.objects.get(leave_request=self.leave)
        self.assertEqual(sync_row.status, TempoAbsenceSync.Status.FAILED)
        self.assertEqual(sync_row.error_code, "jira_reauth_required")
        self.assertFalse(TimeEntry.objects.exists())

    @patch("core.services.tempo_absence_sync_service.requests.request")
    def test_sync_creates_local_entry_and_retries_without_duplicates(
        self, mock_request
    ):
        TempoAbsenceSyncSettings.objects.update_or_create(
            pk=1,
            defaults={
                "enabled": True,
                "default_jira_issue_key": "ABS-1",
                "daily_hours": "8.00",
            },
        )
        jira_connection = JiraUserConnection.objects.create(
            user=self.employee,
            jira_account_id="acct-1",
            cloud_id="cloud-1",
            site_url="https://example.atlassian.net",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        jira_connection.set_access_token("JIRA-TOKEN")
        jira_connection.save(update_fields=["access_token_encrypted", "updated_at"])
        tempo_connection = TempoUserConnection.objects.create(
            user=self.employee,
            tempo_account_id="acct-1",
            base_url="https://api.tempo.io/4",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        tempo_connection.set_access_token("TEMPO-TOKEN")
        tempo_connection.save(update_fields=["access_token_encrypted", "updated_at"])

        issue_response = Mock(status_code=200, headers={})
        issue_response.json.return_value = {"id": "10001", "key": "ABS-1"}
        tempo_response = Mock(status_code=200, headers={})
        tempo_response.json.return_value = {"tempoWorklogId": "tw-1"}
        mock_request.side_effect = [issue_response, tempo_response] * 2

        first = sync_leave_request(self.leave.id)
        second = sync_leave_request(self.leave.id)

        self.assertEqual(first["synced"], 1)
        self.assertEqual(second["synced"], 1)
        self.assertEqual(TempoAbsenceSync.objects.count(), 1)
        self.assertEqual(
            TimeEntry.objects.filter(
                source_type=TimeEntrySourceType.BLOOMHUB_LEAVE
            ).count(),
            1,
        )
        entry = TimeEntry.objects.get()
        self.assertEqual(entry.source_external_id, f"leave:{self.leave.id}:2026-06-08")
        self.assertEqual(entry.status, "approved")
        self.assertEqual(entry.source_metadata["tempo_worklog_id"], "tw-1")
        methods = [call.args[0] for call in mock_request.call_args_list]
        self.assertEqual(methods, ["get", "post", "get", "put"])

    @patch("core.services.tempo_absence_sync_service.requests.request")
    def test_sync_auto_creates_absence_task_mapping(self, mock_request):
        JiraIssueMapping.objects.filter(jira_issue_key="ABS-1").delete()
        TimeTask.objects.filter(jira_issue_key="ABS-1").delete()
        TempoAbsenceSyncSettings.objects.update_or_create(
            pk=1,
            defaults={
                "enabled": True,
                "default_jira_issue_key": "ABS-1",
                "daily_hours": "8.00",
            },
        )
        jira_connection = JiraUserConnection.objects.create(
            user=self.employee,
            jira_account_id="acct-1",
            cloud_id="cloud-1",
            site_url="https://example.atlassian.net",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        jira_connection.set_access_token("JIRA-TOKEN")
        jira_connection.save(update_fields=["access_token_encrypted", "updated_at"])
        tempo_connection = TempoUserConnection.objects.create(
            user=self.employee,
            tempo_account_id="acct-1",
            base_url="https://api.tempo.io/4",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        tempo_connection.set_access_token("TEMPO-TOKEN")
        tempo_connection.save(update_fields=["access_token_encrypted", "updated_at"])
        issue_response = Mock(status_code=200, headers={})
        issue_response.json.return_value = {"id": "10001", "key": "ABS-1"}
        tempo_response = Mock(status_code=200, headers={})
        tempo_response.json.return_value = {"tempoWorklogId": "tw-1"}
        mock_request.side_effect = [issue_response, tempo_response]

        result = sync_leave_request(self.leave.id)

        self.assertEqual(result["synced"], 1)
        mapping = JiraIssueMapping.objects.get(jira_issue_key="ABS-1")
        self.assertEqual(mapping.jira_issue_id, "10001")
        self.assertEqual(mapping.task.project.name, "BloomHub Absences")
        self.assertEqual(
            TimeEntry.objects.get().source_type, TimeEntrySourceType.BLOOMHUB_LEAVE
        )
