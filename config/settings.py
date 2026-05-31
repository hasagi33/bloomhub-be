"""
Django settings for BloomHub project.

Uses DATABASE_URL (Neon or local Postgres). Set ENVIRONMENT to local, dev, or prod.
"""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

ENVIRONMENT = os.environ.get("ENVIRONMENT", "local")
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-dev-change-in-production")
DEBUG = os.environ.get("DEBUG", "true").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS", "localhost,127.0.0.1,.onrender.com"
).split(",")


def _get_database_url():
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if ENVIRONMENT == "dev":
        return os.environ.get("DEV_DATABASE_URL", "")
    if ENVIRONMENT == "prod":
        return os.environ.get("PROD_DATABASE_URL", "")
    # local, no DATABASE_URL: use SQLite so you don't need a local Postgres
    return ""


DATABASE_URL = _get_database_url()
USE_TENANTS = bool(
    DATABASE_URL and DATABASE_URL.strip() and "postgres" in DATABASE_URL.lower()
)

# core before staticfiles so core's custom runserver (localhost message) overrides staticfiles'
# 1. Base apps for the project (always included)
_BASE_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "core",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "storages",
]

if USE_TENANTS:
    _SHARED_APPS = ["django_tenants", "tenants"] + _BASE_APPS
    INSTALLED_APPS = list(dict.fromkeys(_SHARED_APPS))
    SHARED_APPS = tuple(INSTALLED_APPS)
    TENANT_APPS = ("core",)
    TENANT_MODEL = "tenants.Client"
    TENANT_DOMAIN_MODEL = "tenants.Domain"
else:
    INSTALLED_APPS = list(dict.fromkeys(_BASE_APPS))

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "BloomHub Backend API",
    "DESCRIPTION": "BloomHub internal HR platform API. Auth via JWT (login/register, then Bearer token).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "JWTAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "JWT access token from POST /api/auth/login/ or /api/auth/register/",
            }
        }
    },
    "ENUM_NAME_OVERRIDES": {
        "ConditionEnum": "core.models.AssetCondition",
        "StatusEnum": "core.models.AssetStatus",
        "CPFChangeSourceEnum": "core.enums.CPFChangeSource",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

MIDDLEWARE = (
    [
        "django_tenants.middleware.main.TenantMainMiddleware",
    ]
    if USE_TENANTS
    else []
) + [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(
        ","
    )
    if origin.strip()
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

if DATABASE_URL and DATABASE_URL.strip():
    _db = dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=(ENVIRONMENT == "prod"),
    )
    if USE_TENANTS:
        _db["ENGINE"] = "django_tenants.postgresql_backend"
    DATABASES = {"default": _db}
    if USE_TENANTS:
        DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)
else:
    # No URL (e.g. CI without secrets): use sqlite so ENGINE is always set
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = os.environ.get("STATIC_ROOT", "staticfiles")

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "bloomhub").strip()
USE_R2 = bool(
    R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME
)

if USE_R2:
    R2_ENDPOINT_URL = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL
    AWS_S3_REGION_NAME = "auto"
    # Keep avatar paths stable (e.g. .../avatar.png) by allowing overwrite.
    AWS_S3_FILE_OVERWRITE = True

    _r2_verify_env = os.environ.get("R2_VERIFY_SSL", "").lower()
    if _r2_verify_env in ("0", "false", "no"):
        AWS_S3_VERIFY = False
    elif _r2_verify_env in ("1", "true", "yes"):
        AWS_S3_VERIFY = True
    else:
        AWS_S3_VERIFY = not DEBUG
    STORAGES = {
        "default": {
            "BACKEND": "config.storage.R2Storage",
            "OPTIONS": {"verify": AWS_S3_VERIFY},
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# ── Resend email service ──────────────────────────────────────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "BloomHub <onboarding@resend.dev>"
)
# Public base URL used in email templates and QR payloads.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
if ENVIRONMENT == "local":
    FRONTEND_URL = "http://localhost:3000"
elif ENVIRONMENT == "dev":
    FRONTEND_URL = "https://bloomhub-fe-dev.vercel.app"
else:
    FRONTEND_URL = os.environ.get("FRONTEND_URL", SITE_URL).rstrip("/")
    if ENVIRONMENT == "prod" and not FRONTEND_URL:
        raise ImproperlyConfigured(
            "FRONTEND_URL or SITE_URL must be set when ENVIRONMENT=prod."
        )

# AI assistant / OpenRouter
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "deepseek/deepseek-v4-flash"
).strip()
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).strip()
AI_AGENT_MAX_TOOL_STEPS = int(os.environ.get("AI_AGENT_MAX_TOOL_STEPS", "8"))
AI_AGENT_REQUIRE_CONFIRMATION = os.environ.get(
    "AI_AGENT_REQUIRE_CONFIRMATION", "true"
).lower() in ("1", "true", "yes")
AI_AGENT_ENABLE_STREAMING = os.environ.get(
    "AI_AGENT_ENABLE_STREAMING", "false"
).lower() in ("1", "true", "yes")

OPENROUTER_TEMPERATURE = float(os.environ.get("OPENROUTER_TEMPERATURE", "0.1"))
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "2048"))
OPENROUTER_REQUEST_TIMEOUT = float(os.environ.get("OPENROUTER_REQUEST_TIMEOUT", "60"))
OPENROUTER_MAX_RETRIES = int(os.environ.get("OPENROUTER_MAX_RETRIES", "2"))

AI_AGENT_MAX_ITERATIONS = int(os.environ.get("AI_AGENT_MAX_ITERATIONS", "4"))
AI_AGENT_HISTORY_LIMIT = int(os.environ.get("AI_AGENT_HISTORY_LIMIT", "8"))
AI_AGENT_HISTORY_CHAR_BUDGET = int(
    os.environ.get("AI_AGENT_HISTORY_CHAR_BUDGET", "6000")
)
AI_AGENT_HISTORY_MSG_CHAR_CAP = int(
    os.environ.get("AI_AGENT_HISTORY_MSG_CHAR_CAP", "800")
)
AI_AGENT_SENSITIVE_RECENT_AUTH_SECONDS = int(
    os.environ.get("AI_AGENT_SENSITIVE_RECENT_AUTH_SECONDS", "3600")
)
AI_AGENT_PENDING_CONFIRMATION_TTL_SECONDS = int(
    os.environ.get("AI_AGENT_PENDING_CONFIRMATION_TTL_SECONDS", "600")
)
