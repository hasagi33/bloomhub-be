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
    Permission,
    Role,
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

        # Grant Asset Management permissions to the test user so existing tests pass
        assign_perm, _ = Permission.objects.get_or_create(
            module_name="Asset Management", feature_action="assign_assets_to_employees"
        )
        return_perm, _ = Permission.objects.get_or_create(
            module_name="Asset Management", feature_action="process_asset_return"
        )
        view_all_perm, _ = Permission.objects.get_or_create(
            module_name="Asset Management", feature_action="view_all_assets"
        )

        self.profile.add_permission(assign_perm)
        self.profile.add_permission(return_perm)
        self.profile.add_permission(view_all_perm)
        self._asset_seq = 100

        # Get JWT token and set authentication
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def _auth_as(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def _permission(self, action):
        perm, _ = Permission.objects.get_or_create(
            module_name="Asset Management",
            feature_action=action,
        )
        return perm

    def _create_role_user(self, username, role_name, actions):
        role = Role.objects.create(name=role_name)
        for action in actions:
            role.permissions.add(self._permission(action))

        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="pass123",
        )
        profile = user.profile
        profile.role = role
        profile.save(update_fields=["role"])
        return user

    def _create_asset(self, prefix="LAPTOP"):
        self._asset_seq += 1
        return Asset.objects.create(
            asset_id=f"{prefix}{self._asset_seq}",
            name=f"{prefix} Device {self._asset_seq}",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

    def _pdf_role_actions(self):
        return {
            "Employee": {"view_own_assigned_assets"},
            "Manager": {"view_own_assigned_assets", "view_team_assets"},
            "HR": {
                "view_own_assigned_assets",
                "view_team_assets",
                "view_all_assets",
                "assign_assets_to_employees",
                "process_asset_return",
            },
            "HRManager": {
                "view_own_assigned_assets",
                "view_team_assets",
                "view_all_assets",
                "assign_assets_to_employees",
                "process_asset_return",
            },
            "Admin": {
                "view_own_assigned_assets",
                "view_team_assets",
                "view_all_assets",
                "assign_assets_to_employees",
                "process_asset_return",
            },
        }

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
            "notes": "First assignment",
        }

        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Second assignment should fail (asset not available)
        assignment_data["notes"] = "Second assignment - should fail"
        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_hr_user_forbidden(self):
        """Test that a user without permissions cannot assign or return assets"""
        normal_user = User.objects.create_user(
            username="normaluser",
            email="normal@example.com",
            password="password123",
        )
        refresh = RefreshToken.for_user(normal_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        asset = Asset.objects.create(
            asset_id="LAPTOP006",
            name="Restricted Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        assignment_data = {
            "asset": asset.id,
            "employee": normal_user.profile.id,
            "notes": "Trying to assign without permission",
        }

        # Attempt assignment without `assign_assets_to_employees`
        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt to return without `process_asset_return`
        response = self.client.post("/api/assignments/9999/return/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_assignment_tracking_accuracy(self):
        """Test that assigned_by, assigned_at, and returned_at are accurately tracked and cannot be spoofed."""
        asset = Asset.objects.create(
            asset_id="LAPTOP007",
            name="Tracking Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        # Another user trying to spoof assigned_by
        other_user = User.objects.create_user(
            username="otheruser",
            email="other@example.com",
            password="testpass123",
        )

        assignment_data = {
            "asset": asset.id,
            "employee": self.profile.id,
            "assigned_by": other_user.profile.id,  # Spoof attempt
            "notes": "Testing tracking dates and user",
        }

        # Test creating an assignment
        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verification: assigned_at is populated
        self.assertIsNotNone(response.data.get("assigned_at"))

        # Verification: assigned_by ignores the spoofed ID and uses the authenticated user's profile ID
        self.assertEqual(response.data.get("assigned_by"), self.profile.id)

        # Also check the is_active property
        self.assertTrue(response.data.get("is_active"))

        assignment_id = response.data["id"]

        # Test returning the asset
        return_data = {
            "return_condition": AssetCondition.FAIR,
            "notes": "Returned during test",
        }

        response = self.client.post(
            f"/api/assignments/{assignment_id}/return/", return_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verification: returned_at is now populated
        self.assertIsNotNone(response.data.get("returned_at"))

        # Verification: is_active is now False
        self.assertFalse(response.data.get("is_active"))

    def test_view_all_assets_scope(self):
        """HR user with view_all_assets sees all assets in the system."""
        # self.user already has view_all_assets from setUp
        Asset.objects.create(
            asset_id="SCOPE001",
            name="Asset A",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=10),
            status=AssetStatus.ACTIVE,
        )
        Asset.objects.create(
            asset_id="SCOPE002",
            name="Asset B",
            condition=AssetCondition.FAIR,
            purchase_date=date.today() - timedelta(days=20),
            status=AssetStatus.ACTIVE,
        )
        response = self.client.get("/api/assets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        asset_ids = [a["asset_id"] for a in response.data]
        self.assertIn("SCOPE001", asset_ids)
        self.assertIn("SCOPE002", asset_ids)

    def test_view_own_assigned_assets_scope(self):
        """Employee with only view_own_assigned_assets sees only their own active assignments."""
        from core.models import Permission

        # Create an employee user with only view_own_assigned_assets
        employee_user = User.objects.create_user(
            username="empscope", email="empscope@example.com", password="pass123"
        )
        employee_profile = employee_user.profile

        own_perm, _ = Permission.objects.get_or_create(
            module_name="Asset Management", feature_action="view_own_assigned_assets"
        )
        employee_profile.add_permission(own_perm)

        refresh = RefreshToken.for_user(employee_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        # Create two assets; assign one to employee, leave the other unassigned
        asset_mine = Asset.objects.create(
            asset_id="MINE001",
            name="My Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=5),
            status=AssetStatus.ACTIVE,
        )
        Asset.objects.create(
            asset_id="OTHER001",
            name="Other Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=5),
            status=AssetStatus.ACTIVE,
        )

        # HR user assigns asset_mine to the employee (use HR credentials temporarily)
        hr_refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {hr_refresh.access_token}")
        assign_response = self.client.post(
            "/api/assignments/",
            {
                "asset": asset_mine.id,
                "employee": employee_profile.id,
                "notes": "Scoping test",
            },
            format="json",
        )
        self.assertEqual(assign_response.status_code, status.HTTP_201_CREATED)

        # Switch back to the employee credentials
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = self.client.get("/api/assets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = [a["asset_id"] for a in response.data]
        self.assertIn("MINE001", returned_ids)
        self.assertNotIn("OTHER001", returned_ids)

    def test_no_permission_sees_empty_list(self):
        """A user with no view permissions on Asset Management gets an empty assets list."""
        no_perm_user = User.objects.create_user(
            username="nopermuser", email="noperm@example.com", password="pass123"
        )
        refresh = RefreshToken.for_user(no_perm_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = self.client.get("/api/assets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

        response = self.client.get("/api/assignments/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_pdf_role_matrix_assign_permissions(self):
        """PDF role matrix: only HR/HR Manager/Admin can assign assets."""
        role_actions = self._pdf_role_actions()
        can_assign = {
            "Employee": False,
            "Manager": False,
            "HR": True,
            "HRManager": True,
            "Admin": True,
        }

        for role_name, expected in can_assign.items():
            user = self._create_role_user(
                username=f"{role_name.lower()}_assign",
                role_name=f"{role_name}AssignRole",
                actions=role_actions[role_name],
            )
            asset = self._create_asset(prefix=f"ASSIGN{role_name[:2].upper()}")
            self._auth_as(user)

            response = self.client.post(
                "/api/assignments/",
                {
                    "asset": asset.id,
                    "employee": user.profile.id,
                    "notes": "pdf role check",
                },
                format="json",
            )

            expected_status = (
                status.HTTP_201_CREATED if expected else status.HTTP_403_FORBIDDEN
            )
            self.assertEqual(response.status_code, expected_status)

    def test_pdf_role_matrix_process_return_permissions(self):
        """PDF role matrix: only HR/HR Manager/Admin can process returns."""
        role_actions = self._pdf_role_actions()
        can_process_return = {
            "Employee": False,
            "Manager": False,
            "HR": True,
            "HRManager": True,
            "Admin": True,
        }

        for role_name, expected in can_process_return.items():
            actor = self._create_role_user(
                username=f"{role_name.lower()}_return",
                role_name=f"{role_name}ReturnRole",
                actions=role_actions[role_name],
            )
            employee = self._create_role_user(
                username=f"{role_name.lower()}_assignee",
                role_name=f"{role_name}AssigneeRole",
                actions=role_actions["Employee"],
            )
            asset = self._create_asset(prefix=f"RETURN{role_name[:2].upper()}")

            # Create the assignment with HR privileges from setUp user.
            self._auth_as(self.user)
            created = self.client.post(
                "/api/assignments/",
                {
                    "asset": asset.id,
                    "employee": employee.profile.id,
                    "notes": "seed return",
                },
                format="json",
            )
            self.assertEqual(created.status_code, status.HTTP_201_CREATED)

            self._auth_as(actor)
            response = self.client.post(
                f"/api/assignments/{created.data['id']}/return/",
                {"return_condition": AssetCondition.GOOD, "notes": "role return check"},
                format="json",
            )

            expected_status = (
                status.HTTP_200_OK if expected else status.HTTP_403_FORBIDDEN
            )
            self.assertEqual(response.status_code, expected_status)

    def test_pdf_role_matrix_view_scope_for_assets_and_assignments(self):
        """PDF role matrix: manager sees own+team, employee sees own only, HR sees all."""
        role_actions = self._pdf_role_actions()

        manager_user = self._create_role_user(
            username="manager_scope",
            role_name="ManagerScopeRole",
            actions=role_actions["Manager"],
        )
        employee_report = self._create_role_user(
            username="report_scope",
            role_name="EmployeeReportScopeRole",
            actions=role_actions["Employee"],
        )
        outsider = self._create_role_user(
            username="outsider_scope",
            role_name="OutsiderScopeRole",
            actions=role_actions["Employee"],
        )
        hr_user = self._create_role_user(
            username="hr_scope",
            role_name="HrScopeRole",
            actions=role_actions["HR"],
        )

        employee_report.profile.managers.set([manager_user.profile])

        asset_manager = self._create_asset(prefix="SCOPEM")
        asset_report = self._create_asset(prefix="SCOPER")
        asset_outsider = self._create_asset(prefix="SCOPEO")

        self._auth_as(self.user)
        for asset, employee_profile in [
            (asset_manager, manager_user.profile),
            (asset_report, employee_report.profile),
            (asset_outsider, outsider.profile),
        ]:
            created = self.client.post(
                "/api/assignments/",
                {
                    "asset": asset.id,
                    "employee": employee_profile.id,
                    "notes": "scope seed",
                },
                format="json",
            )
            self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        self._auth_as(manager_user)
        manager_assets_res = self.client.get("/api/assets/")
        self.assertEqual(manager_assets_res.status_code, status.HTTP_200_OK)
        manager_asset_ids = {a["asset_id"] for a in manager_assets_res.data}
        self.assertIn(asset_manager.asset_id, manager_asset_ids)
        self.assertIn(asset_report.asset_id, manager_asset_ids)
        self.assertNotIn(asset_outsider.asset_id, manager_asset_ids)

        manager_assignments_res = self.client.get("/api/assignments/")
        self.assertEqual(manager_assignments_res.status_code, status.HTTP_200_OK)
        manager_assignment_asset_ids = {
            a["asset_details"]["asset_id"] for a in manager_assignments_res.data
        }
        self.assertIn(asset_manager.asset_id, manager_assignment_asset_ids)
        self.assertIn(asset_report.asset_id, manager_assignment_asset_ids)
        self.assertNotIn(asset_outsider.asset_id, manager_assignment_asset_ids)

        self._auth_as(employee_report)
        report_assets_res = self.client.get("/api/assets/")
        self.assertEqual(report_assets_res.status_code, status.HTTP_200_OK)
        report_asset_ids = {a["asset_id"] for a in report_assets_res.data}
        self.assertEqual(report_asset_ids, {asset_report.asset_id})

        self._auth_as(hr_user)
        hr_assets_res = self.client.get("/api/assets/")
        self.assertEqual(hr_assets_res.status_code, status.HTTP_200_OK)
        hr_asset_ids = {a["asset_id"] for a in hr_assets_res.data}
        self.assertIn(asset_manager.asset_id, hr_asset_ids)
        self.assertIn(asset_report.asset_id, hr_asset_ids)
        self.assertIn(asset_outsider.asset_id, hr_asset_ids)

    def test_return_nonexistent_assignment_with_permission_returns_404(self):
        """Edge case: return endpoint should return 404 for missing assignment."""
        self._auth_as(self.user)
        response = self.client.post(
            "/api/assignments/999999/return/",
            {"return_condition": AssetCondition.GOOD, "notes": "not found"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_returning_already_returned_assignment_fails(self):
        """Edge case: processing return twice should fail on second attempt."""
        asset = self._create_asset(prefix="RETWICE")
        self._auth_as(self.user)
        created = self.client.post(
            "/api/assignments/",
            {"asset": asset.id, "employee": self.profile.id, "notes": "double return"},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        first_return = self.client.post(
            f"/api/assignments/{created.data['id']}/return/",
            {"return_condition": AssetCondition.GOOD, "notes": "first return"},
            format="json",
        )
        self.assertEqual(first_return.status_code, status.HTTP_200_OK)

        second_return = self.client.post(
            f"/api/assignments/{created.data['id']}/return/",
            {"return_condition": AssetCondition.FAIR, "notes": "second return"},
            format="json",
        )
        self.assertEqual(second_return.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assignment_fails_for_non_active_asset_status(self):
        """Edge case: non-active assets must not be assignable."""
        self._auth_as(self.user)
        asset = Asset.objects.create(
            asset_id="INACTIVE001",
            name="Inactive Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.RETURNED,
        )

        response = self.client.post(
            "/api/assignments/",
            {"asset": asset.id, "employee": self.profile.id, "notes": "should fail"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
