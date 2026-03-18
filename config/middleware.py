"""
Log hostname seen by the app (for debugging tenant 404 behind reverse proxies).
Remove or disable once tenant resolution is working.
"""

import logging

logger = logging.getLogger(__name__)


class LogTenantHostMiddleware:
    """Log request.get_host() and proxy headers so we can see what django-tenants will use."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()
        meta_host = request.META.get("HTTP_HOST", "")
        x_forwarded_host = request.META.get("HTTP_X_FORWARDED_HOST", "")
        logger.warning(
            "tenant_host_debug path=%s get_host=%r HTTP_HOST=%r X-Forwarded-Host=%r",
            request.path,
            host,
            meta_host,
            x_forwarded_host,
        )
        return self.get_response(request)
