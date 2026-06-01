from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import JiraOAuthState, JiraUserConnection
from core.services.credential_encryption import decrypt_secret
from core.services.jira_oauth import (
    JiraReauthRequired,
    get_valid_access_token,
)


@override_settings(
    JIRA_OAUTH_CLIENT_ID="cid",
    JIRA_OAUTH_CLIENT_SECRET="csec",
    JIRA_OAUTH_REDIRECT_URI="https://example.test/oauth/jira/callback",
)
class JiraOAuthFlowTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw"
        )
        self.client.force_authenticate(self.user)

    def test_authorize_returns_url_and_persists_state(self):
        response = self.client.get(reverse("core:time_jira_oauth_authorize"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("authorize_url", response.data)
        self.assertIn("state", response.data)
        self.assertTrue(
            response.data["authorize_url"].startswith(
                "https://auth.atlassian.com/authorize?"
            )
        )
        self.assertTrue(
            JiraOAuthState.objects.filter(
                user=self.user, state=response.data["state"]
            ).exists()
        )

    def _mock_atlassian(self, mock_post, mock_get):
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {
            "access_token": "ACCESS123",
            "refresh_token": "REFRESH123",
            "expires_in": 3600,
            "scope": "read:jira-work read:jira-user read:me offline_access",
        }
        mock_post.return_value = token_resp

        resources_resp = MagicMock()
        resources_resp.status_code = 200
        resources_resp.json.return_value = [
            {"id": "cloud-1", "url": "https://acme.atlassian.net", "name": "ACME"}
        ]
        me_resp = MagicMock()
        me_resp.status_code = 200
        me_resp.json.return_value = {
            "account_id": "acc-1",
            "email": "alice@acme.com",
            "name": "Alice",
        }
        mock_get.side_effect = [resources_resp, me_resp]

    @patch("core.services.jira_oauth.requests.get")
    @patch("core.services.jira_oauth.requests.post")
    def test_callback_persists_connection_and_encrypts_tokens(
        self, mock_post, mock_get
    ):
        self._mock_atlassian(mock_post, mock_get)
        state_row = JiraOAuthState.objects.create(
            user=self.user, state="state-abc", redirect_to=""
        )
        response = self.client.post(
            reverse("core:time_jira_oauth_callback"),
            {"code": "code-xyz", "state": state_row.state},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        connection = JiraUserConnection.objects.get(user=self.user)
        self.assertEqual(connection.jira_account_id, "acc-1")
        self.assertEqual(connection.cloud_id, "cloud-1")
        self.assertEqual(connection.site_url, "https://acme.atlassian.net")
        # Tokens encrypted at rest
        self.assertNotIn("ACCESS123", connection.access_token_encrypted)
        self.assertNotIn("REFRESH123", connection.refresh_token_encrypted)
        self.assertEqual(decrypt_secret(connection.access_token_encrypted), "ACCESS123")
        self.assertEqual(
            decrypt_secret(connection.refresh_token_encrypted), "REFRESH123"
        )
        # State consumed
        self.assertFalse(JiraOAuthState.objects.filter(pk=state_row.pk).exists())

    @patch("core.services.jira_oauth.requests.get")
    @patch("core.services.jira_oauth.requests.post")
    def test_callback_rejects_foreign_state(self, mock_post, mock_get):
        other = User.objects.create_user(username="bob", email="b@e.com", password="x")
        state_row = JiraOAuthState.objects.create(
            user=other, state="state-foreign", redirect_to=""
        )
        response = self.client.post(
            reverse("core:time_jira_oauth_callback"),
            {"code": "c", "state": state_row.state},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # state for other user untouched, no token exchange attempted
        self.assertTrue(JiraOAuthState.objects.filter(pk=state_row.pk).exists())
        mock_post.assert_not_called()

    def test_callback_rejects_expired_state(self):
        state_row = JiraOAuthState.objects.create(
            user=self.user, state="state-old", redirect_to=""
        )
        JiraOAuthState.objects.filter(pk=state_row.pk).update(
            created_at=timezone.now() - timedelta(minutes=30)
        )
        response = self.client.post(
            reverse("core:time_jira_oauth_callback"),
            {"code": "c", "state": state_row.state},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Pruned
        self.assertFalse(JiraOAuthState.objects.filter(pk=state_row.pk).exists())

    def test_status_returns_disconnected_when_no_connection(self):
        response = self.client.get(reverse("core:time_jira_oauth_status"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["connected"])

    def test_disconnect_deletes_connection(self):
        JiraUserConnection.objects.create(
            user=self.user,
            jira_account_id="acc-1",
            cloud_id="cloud-1",
            site_url="https://acme.atlassian.net",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        response = self.client.delete(reverse("core:time_jira_oauth_disconnect"))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(JiraUserConnection.objects.filter(user=self.user).exists())


@override_settings(
    JIRA_OAUTH_CLIENT_ID="cid",
    JIRA_OAUTH_CLIENT_SECRET="csec",
    JIRA_OAUTH_REDIRECT_URI="https://example.test/cb",
)
class JiraOAuthRefreshTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="carol", email="c@e.com", password="pw"
        )
        self.connection = JiraUserConnection.objects.create(
            user=self.user,
            jira_account_id="acc-2",
            cloud_id="cloud-2",
            site_url="https://x.atlassian.net",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.connection.set_access_token("OLDACCESS")
        self.connection.set_refresh_token("OLDREFRESH")
        self.connection.save()

    def test_get_valid_access_token_returns_cached_when_fresh(self):
        token, conn = get_valid_access_token(self.user)
        self.assertEqual(token, "OLDACCESS")
        self.assertEqual(conn.pk, self.connection.pk)

    @patch("core.services.jira_oauth.requests.post")
    def test_get_valid_access_token_refreshes_when_expired(self, mock_post):
        # Expire token.
        JiraUserConnection.objects.filter(pk=self.connection.pk).update(
            token_expires_at=timezone.now() - timedelta(minutes=1)
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": "NEWACCESS",
            "refresh_token": "NEWREFRESH",
            "expires_in": 3600,
        }
        mock_post.return_value = resp
        token, _ = get_valid_access_token(self.user)
        self.assertEqual(token, "NEWACCESS")
        self.connection.refresh_from_db()
        self.assertEqual(
            decrypt_secret(self.connection.refresh_token_encrypted), "NEWREFRESH"
        )

    @patch("core.services.jira_oauth.requests.post")
    def test_refresh_failure_clears_tokens_and_raises_reauth(self, mock_post):
        JiraUserConnection.objects.filter(pk=self.connection.pk).update(
            token_expires_at=timezone.now() - timedelta(minutes=1)
        )
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"error": "invalid_grant"}
        mock_post.return_value = resp
        with self.assertRaises(JiraReauthRequired):
            get_valid_access_token(self.user)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.access_token_encrypted, "")
        self.assertEqual(self.connection.refresh_token_encrypted, "")
        self.assertEqual(self.connection.last_refresh_error, "http_400")


class JiraSyncViewTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        from core.models import UserProfile

        self.user = User.objects.create_user(
            username="syncer", email="s@e.com", password="pw"
        )
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.client.force_authenticate(self.user)

    def test_sync_requires_oauth_connection(self):
        response = self.client.post(reverse("core:time_jira_sync"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["code"], "jira_reauth_required")

    def test_sync_rejects_invalid_date_range(self):
        JiraUserConnection.objects.create(
            user=self.user,
            jira_account_id="acc-sync",
            cloud_id="cloud-x",
            site_url="https://x.atlassian.net",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        response = self.client.post(
            reverse("core:time_jira_sync"),
            {"date_from": "2026-06-10", "date_to": "2026-06-01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("core.views.fetch_jira_worklogs", return_value=[])
    def test_sync_updates_last_synced_at_on_success(self, _mock_fetch):
        connection = JiraUserConnection.objects.create(
            user=self.user,
            jira_account_id="acc-sync-2",
            cloud_id="cloud-x",
            site_url="https://x.atlassian.net",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        connection.set_access_token("AT")
        connection.save()
        response = self.client.post(
            reverse("core:time_jira_sync"),
            {"date_from": "2026-06-01", "date_to": "2026-06-05"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("counts", response.data)
        connection.refresh_from_db()
        self.assertIsNotNone(connection.last_synced_at)
        self.assertEqual(connection.last_sync_error, "")


class JiraTokenEncryptionRoundTripTests(APITestCase):
    def test_round_trip_encrypt(self):
        user = User.objects.create_user(username="d", email="d@e.com", password="pw")
        connection = JiraUserConnection.objects.create(
            user=user,
            jira_account_id="acc-3",
            cloud_id="c",
            site_url="https://y.atlassian.net",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        connection.set_access_token("plaintext-token")
        connection.save()
        self.assertNotEqual(connection.access_token_encrypted, "plaintext-token")
        self.assertEqual(connection.get_access_token(), "plaintext-token")
