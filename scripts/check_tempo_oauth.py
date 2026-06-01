#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

import requests

TEMPO_AUTHORIZE_URL = "https://api.tempo.io/oauth/authorize/redirect"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def mask(value: str, visible: int = 6) -> str:
    if not value:
        return "<missing>"
    if len(value) <= visible:
        return "***"
    return f"***{value[-visible:]}"


def build_authorize_url(
    client_id: str, redirect_uri: str, jira_url: str, scopes: list[str]
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "jira_url": jira_url,
        "state": secrets.token_urlsafe(32),
        "response_type": "code",
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    return f"{TEMPO_AUTHORIZE_URL}?{urlencode(params)}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Tempo OAuth authorize endpoint using local env config."
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Env file to read before process env. Default: .env",
    )
    parser.add_argument("--client-id", default="")
    parser.add_argument("--redirect-uri", default="")
    parser.add_argument(
        "--jira-url",
        default="",
        help="Jira Cloud site URL, e.g. https://example.atlassian.net",
    )
    parser.add_argument(
        "--scope",
        action="append",
        dest="scopes",
        help="OAuth scope. Repeatable. Default: omit scope, per current Tempo docs.",
    )
    parser.add_argument(
        "--follow-redirects",
        action="store_true",
        help="Follow Tempo redirects instead of showing first response.",
    )
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))

    client_id = args.client_id or os.environ.get("TEMPO_OAUTH_CLIENT_ID", "").strip()
    redirect_uri = (
        args.redirect_uri or os.environ.get("TEMPO_OAUTH_REDIRECT_URI", "").strip()
    )
    jira_url = (
        args.jira_url or os.environ.get("TEMPO_OAUTH_JIRA_URL", "").strip()
    ).rstrip("/")
    scopes = args.scopes or []

    if not client_id:
        print("Missing TEMPO_OAUTH_CLIENT_ID.")
        return 2
    if not redirect_uri:
        print("Missing TEMPO_OAUTH_REDIRECT_URI.")
        return 2
    if not jira_url:
        print("Missing TEMPO_OAUTH_JIRA_URL or --jira-url.")
        print("Example: --jira-url https://your-site.atlassian.net")
        return 2

    authorize_url = build_authorize_url(client_id, redirect_uri, jira_url, scopes)
    print("Tempo OAuth config")
    print(f"  client_id: {mask(client_id)}")
    print(f"  redirect_uri: {redirect_uri}")
    print(f"  jira_url: {jira_url}")
    print(f"  scopes: {' '.join(scopes) if scopes else '<omitted>'}")
    print()
    print("Authorize URL")
    print(authorize_url)
    print()
    print("Equivalent curl")
    print(f"curl -i --max-redirs 0 '{authorize_url}'")
    print()

    response = requests.get(
        authorize_url,
        allow_redirects=args.follow_redirects,
        timeout=30,
        headers={"Accept": "text/html,application/xhtml+xml,application/json"},
    )

    print("HTTP result")
    print(f"  status: {response.status_code}")
    print(f"  final_url: {response.url}")
    if response.history:
        print("  redirects:")
        for item in response.history:
            print(f"    {item.status_code} -> {item.headers.get('Location', '')}")
    if response.headers.get("Location"):
        print(f"  location: {response.headers['Location']}")
    print(f"  server: {response.headers.get('Server', '<none>')}")
    print(f"  content_type: {response.headers.get('Content-Type', '<none>')}")

    body = response.text.replace("\r", "").strip()
    if body:
        print()
        print("Body snippet")
        print(body[:1200])
        if "organization-public.default.svc.cluster.local" in body:
            print()
            print(
                "Diagnosis: Tempo public endpoint returned a page whose internal URI is "
                "organization-public.default.svc.cluster.local. Backend redirect URL is not "
                "the only problem; Tempo is rejecting the authorize request."
            )

    return 0 if response.status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
