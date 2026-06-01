"""
Custom S3/R2 storage that respects the configured SSL verify setting.

Used to pass the requested verify flag through to boto3 sessions.
"""

from django.conf import settings as django_settings
from storages.backends.s3boto3 import S3Boto3Storage


class R2Storage(S3Boto3Storage):
    """S3-compatible storage for Cloudflare R2 with optional SSL verify override."""

    def _get_verify(self):
        if getattr(django_settings, "AWS_S3_VERIFY", None) is False:
            return False
        return self.verify

    @property
    def connection(self):
        connection = getattr(self._connections, "connection", None)
        if connection is None:
            session = self._create_session()
            self._connections.connection = session.resource(
                "s3",
                region_name=self.region_name,
                use_ssl=self.use_ssl,
                endpoint_url=self.endpoint_url,
                config=self.client_config,
                verify=self._get_verify(),
            )
        return self._connections.connection

    @property
    def unsigned_connection(self):
        unsigned_connection = getattr(self._unsigned_connections, "connection", None)
        if unsigned_connection is None:
            import botocore
            from botocore.config import Config

            session = self._create_session()
            config = self.client_config.merge(
                Config(signature_version=botocore.UNSIGNED)
            )
            self._unsigned_connections.connection = session.resource(
                "s3",
                region_name=self.region_name,
                use_ssl=self.use_ssl,
                endpoint_url=self.endpoint_url,
                config=config,
                verify=self._get_verify(),
            )
        return self._unsigned_connections.connection
