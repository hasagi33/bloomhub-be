from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

from core.models import TempoUserConnection

TEMPO_AUTHORIZE_URL = "https://api.tempo.io/oauth/authorize/redirect"
TEMPO_TOKEN_URL = "https://api.tempo.io/oauth/token/"
TEMPO_DEFAULT_API_BASE = "https://api.tempo.io/4"

REFRESH_LEEWAY_SECONDS = 60
DEFAULT_SCOPES: tuple[str, ...] = ()


class TempoReauthRequired(Exception):
    pass


def build_authorize_url(state: str, jira_url: str = "", redirect_uri: str = "") -> str:
    tempo_jira_url = (jira_url or settings.TEMPO_OAUTH_JIRA_URL).strip().rstrip("/")
    tempo_redirect_uri = (redirect_uri or settings.TEMPO_OAUTH_REDIRECT_URI).strip()
    params = {
        "client_id": settings.TEMPO_OAUTH_CLIENT_ID,
        "redirect_uri": tempo_redirect_uri,
        "state": state,
        "response_type": "code",
    }
    if tempo_jira_url:
        params["jira_url"] = tempo_jira_url
    return f"{TEMPO_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: str = "") -> dict[str, Any]:
    tempo_redirect_uri = (redirect_uri or settings.TEMPO_OAUTH_REDIRECT_URI).strip()
    try:
        response = requests.post(
            TEMPO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.TEMPO_OAUTH_CLIENT_ID,
                "client_secret": settings.TEMPO_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": tempo_redirect_uri,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TempoReauthRequired(f"Tempo token exchange failed: {exc}") from exc

    if response.status_code >= 400:
        raise TempoReauthRequired(
            f"Tempo token exchange failed: HTTP {response.status_code}"
        )
    return response.json()


def _refresh_token(connection: TempoUserConnection) -> None:
    refresh_token = connection.get_refresh_token()
    if not refresh_token:
        raise TempoReauthRequired("No refresh token stored for user.")
    try:
        response = requests.post(
            TEMPO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.TEMPO_OAUTH_CLIENT_ID,
                "client_secret": settings.TEMPO_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        connection.last_refresh_error = "network_error"
        connection.save(update_fields=["last_refresh_error", "updated_at"])
        raise TempoReauthRequired(f"Refresh request failed: {exc}") from exc

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
        raise TempoReauthRequired(
            f"Tempo refresh rejected: HTTP {response.status_code}"
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


def get_valid_access_token(user) -> tuple[str, TempoUserConnection]:
    connection = TempoUserConnection.objects.filter(user=user).first()
    if connection is None:
        raise TempoReauthRequired("User has no Tempo connection.")
    if connection.token_expires_at <= timezone.now() + timedelta(
        seconds=REFRESH_LEEWAY_SECONDS
    ):
        _refresh_token(connection)
    token = connection.get_access_token()
    if not token:
        raise TempoReauthRequired("Access token missing after refresh.")
    return token, connection


def prune_expired_oauth_states(max_age_minutes: int = 10) -> int:
    from core.models import TempoOAuthState

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    deleted, _ = TempoOAuthState.objects.filter(created_at__lt=cutoff).delete()
    return deleted
