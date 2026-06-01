from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import JiraUserConnection, TempoOAuthState, TempoUserConnection
from core.services.credential_encryption import decrypt_secret
from core.services.tempo_oauth import (
    TempoReauthRequired,
    get_valid_access_token,
)


@override_settings(
    TEMPO_OAUTH_CLIENT_ID="tcid",
    TEMPO_OAUTH_CLIENT_SECRET="tcsec",
    TEMPO_OAUTH_REDIRECT_URI="https://example.test/oauth/tempo/callback",
    TEMPO_OAUTH_JIRA_URL="https://example.atlassian.net",
)
class TempoOAuthFlowTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.user = User.objects.create_user(
            username="t-alice", email="ta@example.com", password="pw"
        )
        self.client.force_authenticate(self.user)

    def test_authorize_returns_url_and_persists_state(self):
        response = self.client.get(reverse("core:time_tempo_oauth_authorize"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            response.data["authorize_url"].startswith(
                "https://api.tempo.io/oauth/authorize/redirect?"
            )
        )
        self.assertIn(
            "jira_url=https%3A%2F%2Fexample.atlassian.net",
            response.data["authorize_url"],
        )
        self.assertNotIn("scope=", response.data["authorize_url"])
        self.assertTrue(
            TempoOAuthState.objects.filter(
                user=self.user, state=response.data["state"]
            ).exists()
        )

    def test_authorize_uses_query_jira_url(self):
        response = self.client.get(
            reverse("core:time_tempo_oauth_authorize"),
            {"jira_url": "https://query-site.atlassian.net/"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "jira_url=https%3A%2F%2Fquery-site.atlassian.net",
            response.data["authorize_url"],
        )

    def test_authorize_uses_query_redirect_uri(self):
        response = self.client.get(
            reverse("core:time_tempo_oauth_authorize"),
            {"redirect_uri": "https://bloomhub-fe-dev.vercel.app/oauth/tempo/callback"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fbloomhub-fe-dev.vercel.app%2Foauth%2Ftempo%2Fcallback",
            response.data["authorize_url"],
        )

    @override_settings(TEMPO_OAUTH_JIRA_URL="")
    def test_authorize_uses_connected_jira_site_url(self):
        JiraUserConnection.objects.create(
            user=self.user,
            jira_account_id="jira-1",
            cloud_id="cloud-1",
            site_url="https://connected-site.atlassian.net/",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse("core:time_tempo_oauth_authorize"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "jira_url=https%3A%2F%2Fconnected-site.atlassian.net",
            response.data["authorize_url"],
        )

    @patch("core.services.tempo_oauth.requests.post")
    def test_callback_persists_connection_and_encrypts_tokens(self, mock_post):
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {
            "access_token": "TACCESS",
            "refresh_token": "TREFRESH",
            "expires_in": 3600,
            "scope": "read write",
        }
        mock_post.return_value = token_resp

        state_row = TempoOAuthState.objects.create(
            user=self.user, state="t-state-1", redirect_to=""
        )
        response = self.client.post(
            reverse("core:time_tempo_oauth_callback"),
            {
                "code": "code-1",
                "state": state_row.state,
                "redirect_uri": "https://bloomhub-fe-dev.vercel.app/oauth/tempo/callback",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            mock_post.call_args.kwargs["data"]["redirect_uri"],
            "https://bloomhub-fe-dev.vercel.app/oauth/tempo/callback",
        )
        connection = TempoUserConnection.objects.get(user=self.user)
        self.assertNotIn("TACCESS", connection.access_token_encrypted)
        self.assertEqual(decrypt_secret(connection.access_token_encrypted), "TACCESS")
        self.assertEqual(decrypt_secret(connection.refresh_token_encrypted), "TREFRESH")
        self.assertFalse(TempoOAuthState.objects.filter(pk=state_row.pk).exists())

    @patch("core.services.tempo_oauth.requests.post")
    def test_callback_rejects_foreign_state(self, mock_post):
        other = User.objects.create_user(
            username="t-bob", email="tb@e.com", password="x"
        )
        state_row = TempoOAuthState.objects.create(
            user=other, state="t-foreign", redirect_to=""
        )
        response = self.client.post(
            reverse("core:time_tempo_oauth_callback"),
            {"code": "c", "state": state_row.state},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_post.assert_not_called()

    def test_status_returns_disconnected_when_no_connection(self):
        response = self.client.get(reverse("core:time_tempo_oauth_status"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["connected"])

    def test_disconnect_deletes_connection(self):
        TempoUserConnection.objects.create(
            user=self.user,
            base_url="https://api.tempo.io/4",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        response = self.client.delete(reverse("core:time_tempo_oauth_disconnect"))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TempoUserConnection.objects.filter(user=self.user).exists())


@override_settings(
    TEMPO_OAUTH_CLIENT_ID="tcid",
    TEMPO_OAUTH_CLIENT_SECRET="tcsec",
    TEMPO_OAUTH_REDIRECT_URI="https://example.test/cb",
    TEMPO_OAUTH_JIRA_URL="https://example.atlassian.net",
)
class TempoOAuthRefreshTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="t-carol", email="tc@e.com", password="pw"
        )
        self.connection = TempoUserConnection.objects.create(
            user=self.user,
            base_url="https://api.tempo.io/4",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.connection.set_access_token("OLDA")
        self.connection.set_refresh_token("OLDR")
        self.connection.save()

    def test_get_valid_access_token_returns_cached_when_fresh(self):
        token, _ = get_valid_access_token(self.user)
        self.assertEqual(token, "OLDA")

    @patch("core.services.tempo_oauth.requests.post")
    def test_refresh_when_expired(self, mock_post):
        TempoUserConnection.objects.filter(pk=self.connection.pk).update(
            token_expires_at=timezone.now() - timedelta(minutes=1)
        )
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": "NEWA",
            "refresh_token": "NEWR",
            "expires_in": 3600,
        }
        mock_post.return_value = resp
        token, _ = get_valid_access_token(self.user)
        self.assertEqual(token, "NEWA")
        self.connection.refresh_from_db()
        self.assertEqual(
            decrypt_secret(self.connection.refresh_token_encrypted), "NEWR"
        )

    @patch("core.services.tempo_oauth.requests.post")
    def test_refresh_failure_clears_tokens(self, mock_post):
        TempoUserConnection.objects.filter(pk=self.connection.pk).update(
            token_expires_at=timezone.now() - timedelta(minutes=1)
        )
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {}
        mock_post.return_value = resp
        with self.assertRaises(TempoReauthRequired):
            get_valid_access_token(self.user)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.access_token_encrypted, "")
        self.assertEqual(self.connection.refresh_token_encrypted, "")
