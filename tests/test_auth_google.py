from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class GoogleExchangeTestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    @patch("core.serializers.verify_google_id_token")
    def test_google_exchange_new_user(self, mock_verify):
        mock_verify.return_value = {
            "email": "newuser@example.com",
            "given_name": "New",
            "family_name": "User",
        }

        url = reverse("core:google_exchange")
        response = self.client.post(url, {"id_token": "dummy_token"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertIn("user", response.data)
        self.assertEqual(response.data["user"]["email"], "newuser@example.com")

        user = User.objects.get(email="newuser@example.com")
        self.assertEqual(user.first_name, "New")
        self.assertEqual(user.last_name, "User")

    @patch("core.serializers.verify_google_id_token")
    def test_google_exchange_existing_user(self, mock_verify):
        User.objects.create_user(
            username="existinguser", email="existing@example.com", password="password"
        )

        mock_verify.return_value = {
            "email": "existing@example.com",
            "given_name": "Existing",
            "family_name": "User",
        }

        url = reverse("core:google_exchange")
        response = self.client.post(url, {"id_token": "dummy_token"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["email"], "existing@example.com")
        self.assertEqual(User.objects.count(), 1)  # No new user created
