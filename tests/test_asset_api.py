import os
from datetime import date, timedelta

import django
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from core.models import (
    Asset,
    AssetCondition,
    AssetStatus,
)

django.setup()


class AssetManagementAPITest(APITestCase):
    """Test case for Asset Management API"""

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            first_name="Test",
            last_name="User",
        )
        self.profile = self.user.profile

        # Get JWT token and set authentication
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_asset_crud_operations(self):
        """Test Asset CRUD operations"""
        # Test creating an asset
        asset_data = {
            "asset_id": "LAPTOP001",
            "name": "Dell Laptop",
            "condition": AssetCondition.GOOD,
            "warranty_until": str(date.today() + timedelta(days=365)),
            "purchase_date": str(date.today() - timedelta(days=30)),
            "status": AssetStatus.ACTIVE,
            "serial_number": "DL123456789",
            "model": "Dell Inspiron 15",
            "manufacturer": "Dell",
            "purchase_price": "1200.00",
            "description": "Standard work laptop",
        }

        response = self.client.post("/api/assets/", asset_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        asset_id = response.data["id"]

        # Test getting asset list
        response = self.client.get("/api/assets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)

        # Test getting asset detail
        response = self.client.get(f"/api/assets/{asset_id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Test asset filtering
        response = self.client.get("/api/assets/?status=active")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Test updating asset
        update_data = {"name": "Updated Dell Laptop"}
        response = self.client.put(
            f"/api/assets/{asset_id}/", {**asset_data, **update_data}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        return asset_id

    def test_assignment_operations(self):
        """Test Assignment operations"""
        # First create an asset
        asset = Asset.objects.create(
            asset_id="LAPTOP002",
            name="Test Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        # Test creating an assignment
        assignment_data = {
            "asset": asset.id,
            "employee": self.profile.id,
            "assigned_by": self.profile.id,
            "notes": "Initial assignment for testing",
        }

        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        assignment_id = response.data["id"]

        # Test getting assignment list
        response = self.client.get("/api/assignments/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)

        # Test assignment filtering
        response = self.client.get("/api/assignments/?active=true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Test returning an asset
        return_data = {
            "return_condition": AssetCondition.FAIR,
            "notes": "Returned in good condition",
        }

        response = self.client.post(
            f"/api/assignments/{assignment_id}/return/", return_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        return assignment_id

    def test_replacement_log_operations(self):
        """Test ReplacementLog operations"""
        # First create an asset
        asset = Asset.objects.create(
            asset_id="LAPTOP003",
            name="Test Laptop for Replacement",
            condition=AssetCondition.DAMAGED,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.DAMAGED,
        )

        # Test creating a replacement log
        replacement_data = {
            "asset": asset.id,
            "reason": "Screen damage due to coffee spill",
            "replaced_by": self.profile.id,
            "cost": "150.00",
        }

        response = self.client.post(
            "/api/replacement-logs/", replacement_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        log_id = response.data["id"]

        # Test getting replacement log list
        response = self.client.get("/api/replacement-logs/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)

        # Test replacement log filtering
        response = self.client.get(f"/api/replacement-logs/?asset={asset.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        return log_id

    def test_user_profile_operations(self):
        """Test UserProfile operations"""
        # Test getting user profile list
        response = self.client.get("/api/user-profiles/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)

    def test_api_root(self):
        """Test API root endpoint"""
        response = self.client.get("/api/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_asset_availability_logic(self):
        """Test asset availability logic"""
        # Create an asset
        asset = Asset.objects.create(
            asset_id="LAPTOP004",
            name="Availability Test Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        # Asset should be available initially
        response = self.client.get(f"/api/assets/{asset.id}/")
        self.assertTrue(response.data["is_available"])

        # Assign the asset
        assignment_data = {
            "asset": asset.id,
            "employee": self.profile.id,
            "assigned_by": self.profile.id,
            "notes": "Testing availability",
        }

        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Asset should not be available after assignment
        response = self.client.get(f"/api/assets/{asset.id}/")
        self.assertFalse(response.data["is_available"])

    def test_assignment_validation(self):
        """Test assignment validation"""
        # Create an asset and assign it
        asset = Asset.objects.create(
            asset_id="LAPTOP005",
            name="Validation Test Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        # First assignment should succeed
        assignment_data = {
            "asset": asset.id,
            "employee": self.profile.id,
            "assigned_by": self.profile.id,
            "notes": "First assignment",
        }

        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Second assignment should fail (asset not available)
        assignment_data["notes"] = "Second assignment - should fail"
        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
