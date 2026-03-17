import os

import django
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


class AuthTestCase(APITestCase):
    def test_register_user(self):
        """Test user registration"""
        data = {
            "username": "testuser",
            "email": "test@example.com",
            "password": "testpass123",
            "password_confirm": "testpass123",
            "first_name": "Test",
            "last_name": "User",
        }
        response = self.client.post("/api/auth/register/", data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response_data = response.json()
        self.assertIn("access", response_data)
        self.assertIn("refresh", response_data)

    def test_login_user(self):
        """Test user login"""
        # First create a user
        User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )

        data = {
            "email": "test@example.com",
            "password": "testpass123",
        }
        response = self.client.post("/api/auth/login/", data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertIn("access", response_data)
        self.assertIn("refresh", response_data)

    def test_login_invalid_credentials(self):
        """Test login with invalid credentials"""
        data = {
            "email": "nonexistent@example.com",
            "password": "wrongpass",
        }
        response = self.client.post("/api/auth/login/", data)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_refresh(self):
        """Test token refresh"""
        # First create and login a user
        User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        login_data = {
            "email": "test@example.com",
            "password": "testpass123",
        }
        login_response = self.client.post("/api/auth/login/", login_data)
        refresh_token = login_response.json()["refresh"]

        # Now refresh the token
        refresh_data = {"refresh": refresh_token}
        refresh_response = self.client.post("/api/auth/refresh/", refresh_data)
        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        refresh_response_data = refresh_response.json()
        self.assertIn("access", refresh_response_data)
        self.assertIn("user", refresh_response_data)


if __name__ == "__main__":
    import unittest

    unittest.main()
