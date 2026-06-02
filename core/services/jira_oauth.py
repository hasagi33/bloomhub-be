from __future__ import annotations

from datetime import timedelta
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from core.models import JiraUserConnection

ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
ATLASSIAN_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
ATLASSIAN_ME_URL = "https://api.atlassian.com/me"

REFRESH_LEEWAY_SECONDS = 60
DEFAULT_SCOPES = ("read:jira-work", "read:jira-user", "read:me", "offline_access")


class JiraReauthRequired(Exception):
    pass


def build_authorize_url(state: str) -> str:
    from urllib.parse import urlencode

    params = {
        "audience": "api.atlassian.com",
        "client_id": settings.JIRA_OAUTH_CLIENT_ID,
        "scope": " ".join(DEFAULT_SCOPES),
        "redirect_uri": settings.JIRA_OAUTH_REDIRECT_URI,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return f"{ATLASSIAN_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict[str, Any]:
    response = requests.post(
        ATLASSIAN_TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": settings.JIRA_OAUTH_CLIENT_ID,
            "client_secret": settings.JIRA_OAUTH_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.JIRA_OAUTH_REDIRECT_URI,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise JiraReauthRequired(
            f"Atlassian token exchange failed: HTTP {response.status_code}"
        )
    return response.json()


def fetch_accessible_resources(access_token: str) -> list[dict[str, Any]]:
    response = requests.get(
        ATLASSIAN_RESOURCES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise JiraReauthRequired(
            f"Failed to fetch Jira accessible resources: HTTP {response.status_code}"
        )
    return response.json()


def fetch_me(access_token: str) -> dict[str, Any]:
    response = requests.get(
        ATLASSIAN_ME_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise JiraReauthRequired(
            f"Failed to fetch Atlassian profile: HTTP {response.status_code}"
        )
    return response.json()


def _refresh_token(connection: JiraUserConnection) -> None:
    refresh_token = connection.get_refresh_token()
    if not refresh_token:
        raise JiraReauthRequired("No refresh token stored for user.")
    try:
        response = requests.post(
            ATLASSIAN_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": settings.JIRA_OAUTH_CLIENT_ID,
                "client_secret": settings.JIRA_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        connection.last_refresh_error = "network_error"
        connection.save(update_fields=["last_refresh_error", "updated_at"])
        raise JiraReauthRequired(f"Refresh request failed: {exc}") from exc

    if response.status_code >= 400:
        connection.access_token_encrypted = ""
        connection.refresh_token_encrypted = ""
        connection.last_refresh_error = f"http_{response.status_code}"
        connection.save(
            update_fields=[
                "access_token_encrypted",
                "refresh_token_encrypted",
                "last_refresh_error",
                "updated_at",
            ]
        )
        raise JiraReauthRequired(
            f"Atlassian refresh rejected: HTTP {response.status_code}"
        )

    payload = response.json()
    connection.set_access_token(payload["access_token"])
    if payload.get("refresh_token"):
        connection.set_refresh_token(payload["refresh_token"])
    connection.token_expires_at = timezone.now() + timedelta(
        seconds=int(payload.get("expires_in", 3600)) - REFRESH_LEEWAY_SECONDS
    )
    connection.last_refresh_at = timezone.now()
    connection.last_refresh_error = ""
    connection.save(
        update_fields=[
            "access_token_encrypted",
            "refresh_token_encrypted",
            "token_expires_at",
            "last_refresh_at",
            "last_refresh_error",
            "updated_at",
        ]
    )


def get_valid_access_token(user) -> tuple[str, JiraUserConnection]:
    connection = JiraUserConnection.objects.filter(user=user).first()
    if connection is None:
        raise JiraReauthRequired("User has no Jira connection.")
    if connection.token_expires_at <= timezone.now() + timedelta(
        seconds=REFRESH_LEEWAY_SECONDS
    ):
        _refresh_token(connection)
    token = connection.get_access_token()
    if not token:
        raise JiraReauthRequired("Access token missing after refresh.")
    return token, connection


def prune_expired_oauth_states(max_age_minutes: int = 10) -> int:
    from core.models import JiraOAuthState

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    deleted, _ = JiraOAuthState.objects.filter(created_at__lt=cutoff).delete()
    return deleted
