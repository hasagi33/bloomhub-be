import os
from csv import DictReader
from datetime import date, timedelta
from io import StringIO

import django
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from core.models import (
    Asset,
    AssetCategory,
    AssetCondition,
    AssetStatus,
    Assignment,
    Permission,
    ReplacementLog,
    Role,
    ScheduledMaintenance,
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
        for action in [
            "view_all_assets",
            "assign_assets",
            "configure_asset_types",
            "initiate_asset_return",
            "process_asset_return",
            "export_inventory",
            "view_asset_history",
            "log_asset_lost",
            "log_asset_replacement",
            "update_asset_condition",
        ]:
            self.profile.add_permission(self._permission(action))
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
            "Employee": {"view_own_assets", "initiate_asset_return"},
            "Manager": {"view_own_assets", "view_team_assets", "initiate_asset_return"},
            "HR": {
                "view_own_assets",
                "view_team_assets",
                "view_all_assets",
                "assign_assets",
                "process_asset_return",
            },
            "HRManager": {
                "view_own_assets",
                "view_team_assets",
                "view_all_assets",
                "assign_assets",
                "process_asset_return",
            },
            "Admin": {
                "view_own_assets",
                "view_team_assets",
                "view_all_assets",
                "assign_assets",
                "process_asset_return",
            },
        }

    def test_asset_capabilities_endpoint_returns_canonical_contract(self):
        response = self.client.get("/api/assets/capabilities/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("assign_assets", response.data["permissions"])
        self.assertNotIn("assign_assets_to_employees", response.data["permissions"])
        self.assertEqual(response.data["scope"], "all")
        self.assertTrue(response.data["capabilities"]["can_assign_assets"])
        self.assertTrue(response.data["capabilities"]["can_create_assets"])
        self.assertTrue(response.data["capabilities"]["can_export_inventory"])

    def test_asset_response_includes_per_asset_capabilities(self):
        asset = self._create_asset(prefix="CAP")

        response = self.client.get(f"/api/assets/{asset.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("capabilities", response.data)
        self.assertTrue(response.data["capabilities"]["can_view"])
        self.assertTrue(response.data["capabilities"]["can_update"])
        self.assertTrue(response.data["capabilities"]["can_delete"])
        self.assertTrue(response.data["capabilities"]["can_assign"])

    def test_asset_crud_operations(self):
        """Test Asset CRUD operations"""
        # Test creating an asset
        asset_data = {
            "asset_id": "LAPTOP001",
            "name": "Dell Laptop",
            "category": AssetCategory.LAPTOPS,
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
        self.assertEqual(response.data["category"], AssetCategory.LAPTOPS)

        # Test asset filtering
        response = self.client.get("/api/assets/?status=active")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Test updating asset
        update_data = {"name": "Updated Dell Laptop"}
        response = self.client.put(
            f"/api/assets/{asset_id}/", {**asset_data, **update_data}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Asset.objects.filter(id=asset_id).exists())

    def test_asset_status_can_be_changed_via_patch(self):
        """Asset status should be updatable with partial update payloads."""
        asset = Asset.objects.create(
            asset_id="STATUS001",
            name="Status Update Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=10),
            status=AssetStatus.ACTIVE,
        )

        response = self.client.patch(
            f"/api/assets/{asset.id}/",
            {"status": AssetStatus.DAMAGED},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], AssetStatus.DAMAGED)

        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.DAMAGED)

    def test_asset_status_can_be_changed_to_maintenance(self):
        """Asset status should accept Maintenance as a valid enum value."""
        asset = Asset.objects.create(
            asset_id="STATUS003",
            name="Maintenance Status Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=10),
            status=AssetStatus.ACTIVE,
        )

        response = self.client.patch(
            f"/api/assets/{asset.id}/",
            {"status": AssetStatus.MAINTENANCE},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], AssetStatus.MAINTENANCE)

        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.MAINTENANCE)

    def test_asset_status_can_be_created_and_changed_to_retired(self):
        """Asset status should accept Retired for create and edit flows."""
        create_response = self.client.post(
            "/api/assets/",
            {
                "asset_id": "STATUS004",
                "name": "Retired Status Asset",
                "condition": AssetCondition.GOOD,
                "purchase_date": str(date.today() - timedelta(days=10)),
                "status": AssetStatus.RETIRED,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data["status"], AssetStatus.RETIRED)

        asset = Asset.objects.get(id=create_response.data["id"])
        patch_response = self.client.patch(
            f"/api/assets/{asset.id}/",
            {"status": AssetStatus.ACTIVE},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["status"], AssetStatus.ACTIVE)

        patch_response = self.client.patch(
            f"/api/assets/{asset.id}/",
            {"status": AssetStatus.RETIRED},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["status"], AssetStatus.RETIRED)

        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.RETIRED)

    def test_asset_status_patch_rejects_invalid_status(self):
        """Asset status update should reject invalid enum values."""
        asset = Asset.objects.create(
            asset_id="STATUS002",
            name="Invalid Status Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=10),
            status=AssetStatus.ACTIVE,
        )

        response = self.client.patch(
            f"/api/assets/{asset.id}/",
            {"status": "invalid_status"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", response.data)

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
        assignment = Assignment.objects.get(id=assignment_id)
        self.assertEqual(assignment.asset_id_snapshot, asset.asset_id)
        self.assertEqual(assignment.asset_name_snapshot, asset.name)

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
            f"/api/assignments/{assignment_id}/request-return/",
            {"notes": "Return requested"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.post(
            f"/api/assignments/{assignment_id}/return/", return_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Assignment.objects.filter(id=assignment_id).exists())

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
            "date": str(date.today() - timedelta(days=3)),
            "replaced_by": self.profile.id,
            "cost": "150.00",
        }

        response = self.client.post(
            "/api/replacement-logs/", replacement_data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        log_id = response.data["id"]
        self.assertEqual(response.data["date"], str(date.today() - timedelta(days=3)))
        self.assertEqual(response.data["replaced_by"], self.profile.id)
        self.assertEqual(response.data["asset_status_before"], AssetStatus.DAMAGED)
        self.assertEqual(
            response.data["asset_condition_before"], AssetCondition.DAMAGED
        )
        self.assertIsNone(response.data["asset_status_after"])
        self.assertIsNone(response.data["asset_condition_after"])

        # Test getting replacement log list
        response = self.client.get("/api/replacement-logs/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)

        # Test replacement log filtering
        response = self.client.get(f"/api/replacement-logs/?asset={asset.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(ReplacementLog.objects.filter(id=log_id).exists())

    def test_replacement_log_requires_manual_date(self):
        asset = self._create_asset(prefix="REPL")

        response = self.client.post(
            "/api/replacement-logs/",
            {
                "asset": asset.id,
                "reason": "Battery failed and laptop was replaced",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date", response.data)

    def test_replacement_log_accepts_asset_state_after_snapshots(self):
        asset = self._create_asset(prefix="REPL")
        asset.status = AssetStatus.DAMAGED
        asset.condition = AssetCondition.DAMAGED
        asset.save(update_fields=["status", "condition"])

        response = self.client.post(
            "/api/replacement-logs/",
            {
                "asset": asset.id,
                "reason": "Device was replaced after damage assessment",
                "date": str(date.today()),
                "asset_status_after": AssetStatus.RETIRED,
                "asset_condition_after": AssetCondition.POOR,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["asset_status_before"], AssetStatus.DAMAGED)
        self.assertEqual(response.data["asset_status_after"], AssetStatus.RETIRED)
        self.assertEqual(
            response.data["asset_condition_before"], AssetCondition.DAMAGED
        )
        self.assertEqual(response.data["asset_condition_after"], AssetCondition.POOR)

        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.DAMAGED)
        self.assertEqual(asset.condition, AssetCondition.DAMAGED)

    def test_replacement_log_uses_authenticated_user_as_replaced_by(self):
        asset = self._create_asset(prefix="REPL")
        spoofed_user = User.objects.create_user(
            username="spoofed",
            email="spoofed@example.com",
            password="pass123",
        )

        response = self.client.post(
            "/api/replacement-logs/",
            {
                "asset": asset.id,
                "reason": "Keyboard failed and device was replaced",
                "date": str(date.today()),
                "replaced_by": spoofed_user.profile.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["replaced_by"], self.profile.id)
        self.assertEqual(response.data["replaced_by_details"]["id"], self.profile.id)

        replacement_log = ReplacementLog.objects.get(id=response.data["id"])
        self.assertEqual(replacement_log.replaced_by, self.profile)

    def test_replacement_log_mutations_require_replacement_permission(self):
        asset = self._create_asset(prefix="REPL")
        replacement_log = ReplacementLog.objects.create(
            asset=asset,
            reason="Existing replacement record",
            date=date.today(),
            replaced_by=self.profile,
        )
        viewer = self._create_role_user(
            "replacement_viewer",
            "Replacement Viewer",
            ["view_all_assets", "view_asset_history"],
        )
        self._auth_as(viewer)

        list_response = self.client.get("/api/replacement-logs/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)

        detail_response = self.client.get(
            f"/api/replacement-logs/{replacement_log.id}/"
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)

        create_response = self.client.post(
            "/api/replacement-logs/",
            {
                "asset": asset.id,
                "reason": "Attempted replacement",
                "date": str(date.today()),
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

        update_response = self.client.put(
            f"/api/replacement-logs/{replacement_log.id}/",
            {
                "asset": asset.id,
                "reason": "Attempted update",
                "date": str(date.today()),
            },
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN)

        delete_response = self.client.delete(
            f"/api/replacement-logs/{replacement_log.id}/"
        )
        self.assertEqual(delete_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_log_asset_lost_does_not_allow_replacement_log_mutation(self):
        asset = self._create_asset(prefix="REPL")
        legacy_user = self._create_role_user(
            "legacy_asset_logger",
            "Legacy Asset Logger",
            ["view_asset_history", "log_asset_lost"],
        )
        self._auth_as(legacy_user)

        response = self.client.post(
            "/api/replacement-logs/",
            {
                "asset": asset.id,
                "reason": "Attempted replacement with legacy permission",
                "date": str(date.today()),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_replacement_log_patch_updates_fields_without_changing_replaced_by(self):
        asset = self._create_asset(prefix="REPL")
        replacement_log = ReplacementLog.objects.create(
            asset=asset,
            reason="Initial replacement reason",
            date=date.today() - timedelta(days=5),
            replaced_by=self.profile,
        )
        spoofed_user = User.objects.create_user(
            username="patch_spoofed",
            email="patch_spoofed@example.com",
            password="pass123",
        )

        response = self.client.patch(
            f"/api/replacement-logs/{replacement_log.id}/",
            {
                "reason": "Updated replacement reason",
                "date": str(date.today() - timedelta(days=2)),
                "replaced_by": spoofed_user.profile.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["reason"], "Updated replacement reason")
        self.assertEqual(response.data["date"], str(date.today() - timedelta(days=2)))
        self.assertEqual(response.data["replaced_by"], self.profile.id)

        replacement_log.refresh_from_db()
        self.assertEqual(replacement_log.replaced_by, self.profile)

    def test_replacement_log_patch_updates_asset_state_snapshots(self):
        asset = self._create_asset(prefix="REPL")
        replacement_log = ReplacementLog.objects.create(
            asset=asset,
            reason="Initial replacement reason",
            date=date.today(),
            replaced_by=self.profile,
            asset_status_before=AssetStatus.ACTIVE,
            asset_condition_before=AssetCondition.GOOD,
        )

        response = self.client.patch(
            f"/api/replacement-logs/{replacement_log.id}/",
            {
                "asset_status_after": AssetStatus.RETIRED,
                "asset_condition_after": AssetCondition.POOR,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["asset_status_before"], AssetStatus.ACTIVE)
        self.assertEqual(response.data["asset_status_after"], AssetStatus.RETIRED)
        self.assertEqual(response.data["asset_condition_before"], AssetCondition.GOOD)
        self.assertEqual(response.data["asset_condition_after"], AssetCondition.POOR)

        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.ACTIVE)
        self.assertEqual(asset.condition, AssetCondition.GOOD)

    def test_replacement_log_creation_has_no_inventory_side_effects(self):
        asset = self._create_asset(prefix="REPL")
        assignment = Assignment.objects.create(
            asset=asset,
            employee=self.profile,
            assigned_by=self.profile,
            notes="Original assigned device",
        )
        asset.condition = AssetCondition.DAMAGED
        asset.status = AssetStatus.DAMAGED
        asset.save(update_fields=["condition", "status"])
        replacement_asset = self._create_asset(prefix="NEW")

        response = self.client.post(
            "/api/replacement-logs/",
            {
                "asset": asset.id,
                "reason": "Original device damaged beyond repair",
                "date": str(date.today()),
                "replacement_asset": replacement_asset.id,
                "cost": "900.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        asset.refresh_from_db()
        assignment.refresh_from_db()
        replacement_asset.refresh_from_db()

        self.assertEqual(asset.status, AssetStatus.DAMAGED)
        self.assertEqual(asset.condition, AssetCondition.DAMAGED)
        self.assertIsNone(assignment.returned_at)
        self.assertTrue(assignment.is_active)
        self.assertEqual(asset.assignments.filter(returned_at__isnull=True).count(), 1)
        self.assertFalse(asset.is_available)
        self.assertEqual(replacement_asset.status, AssetStatus.ACTIVE)
        self.assertEqual(replacement_asset.condition, AssetCondition.GOOD)
        self.assertEqual(replacement_asset.assignments.count(), 0)
        self.assertTrue(replacement_asset.is_available)

    def test_replacement_log_filters_by_replaced_by_and_delete_removes_record(self):
        asset = self._create_asset(prefix="REPL")
        other_user = User.objects.create_user(
            username="other_replacer",
            email="other_replacer@example.com",
            password="pass123",
        )
        own_log = ReplacementLog.objects.create(
            asset=asset,
            reason="Logged by authenticated user's profile",
            date=date.today(),
            replaced_by=self.profile,
        )
        other_log = ReplacementLog.objects.create(
            asset=asset,
            reason="Logged by someone else",
            date=date.today() - timedelta(days=1),
            replaced_by=other_user.profile,
        )

        response = self.client.get(
            f"/api/replacement-logs/?replaced_by={self.profile.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in response.data], [own_log.id])
        self.assertNotEqual(own_log.id, other_log.id)

        delete_response = self.client.delete(f"/api/replacement-logs/{own_log.id}/")

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ReplacementLog.objects.filter(id=own_log.id).exists())
        self.assertTrue(ReplacementLog.objects.filter(id=other_log.id).exists())

    def test_scheduled_maintenance_create_and_due_state_filters(self):
        asset = self._create_asset(prefix="SCHED")
        overdue_asset = self._create_asset(prefix="SCHED")

        create_response = self.client.post(
            "/api/scheduled-maintenance/",
            {
                "asset": asset.id,
                "due_date": str(date.today() + timedelta(days=5)),
                "reason": "Quarterly device inspection",
                "maintenance_type": ScheduledMaintenance.MaintenanceType.INSPECTION,
                "owner": self.profile.id,
                "estimated_cost": "75.00",
                "vendor": "Local repair shop",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            create_response.data["status"], ScheduledMaintenance.Status.SCHEDULED
        )
        self.assertEqual(create_response.data["due_state"], "upcoming")
        self.assertEqual(create_response.data["created_by"], self.profile.id)
        self.assertEqual(create_response.data["owner"], self.profile.id)

        ScheduledMaintenance.objects.create(
            asset=overdue_asset,
            due_date=date.today() - timedelta(days=1),
            reason="Overdue battery check",
            maintenance_type=ScheduledMaintenance.MaintenanceType.PREVENTIVE,
            created_by=self.profile,
        )

        overdue_response = self.client.get(
            "/api/scheduled-maintenance/?due_state=overdue"
        )
        self.assertEqual(overdue_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(overdue_response.data), 1)
        self.assertEqual(overdue_response.data[0]["asset"], overdue_asset.id)

        asset_response = self.client.get(
            f"/api/scheduled-maintenance/?asset={asset.id}"
        )
        self.assertEqual(asset_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(asset_response.data), 1)
        self.assertEqual(asset_response.data[0]["id"], create_response.data["id"])

    def test_scheduled_maintenance_filters_status_owner_type_and_date_range(self):
        asset = self._create_asset(prefix="SCHED")
        owner_user = User.objects.create_user(
            username="maintenance_owner",
            email="maintenance_owner@example.com",
            password="pass123",
        )
        target = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() + timedelta(days=5),
            reason="Target warranty visit",
            maintenance_type=ScheduledMaintenance.MaintenanceType.WARRANTY,
            owner=owner_user.profile,
            created_by=self.profile,
        )
        ScheduledMaintenance.objects.create(
            asset=self._create_asset(prefix="SCHED"),
            due_date=date.today() + timedelta(days=8),
            reason="Different owner",
            maintenance_type=ScheduledMaintenance.MaintenanceType.WARRANTY,
            owner=self.profile,
            created_by=self.profile,
        )
        ScheduledMaintenance.objects.create(
            asset=self._create_asset(prefix="SCHED"),
            due_date=date.today() + timedelta(days=5),
            reason="Different type",
            maintenance_type=ScheduledMaintenance.MaintenanceType.REPAIR,
            owner=owner_user.profile,
            created_by=self.profile,
        )
        ScheduledMaintenance.objects.create(
            asset=self._create_asset(prefix="SCHED"),
            due_date=date.today() + timedelta(days=5),
            reason="Completed target-like item",
            maintenance_type=ScheduledMaintenance.MaintenanceType.WARRANTY,
            owner=owner_user.profile,
            status=ScheduledMaintenance.Status.COMPLETED,
            created_by=self.profile,
        )

        response = self.client.get(
            "/api/scheduled-maintenance/"
            f"?status={ScheduledMaintenance.Status.SCHEDULED}"
            f"&owner={owner_user.profile.id}"
            f"&maintenance_type={ScheduledMaintenance.MaintenanceType.WARRANTY}"
            f"&due_from={date.today() + timedelta(days=4)}"
            f"&due_to={date.today() + timedelta(days=6)}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in response.data], [target.id])

    def test_scheduled_maintenance_rejects_invalid_due_state_filter(self):
        response = self.client.get("/api/scheduled-maintenance/?due_state=soon")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("due_state", response.data)

    def test_scheduled_maintenance_requires_required_fields(self):
        asset = self._create_asset(prefix="SCHED")

        response = self.client.post(
            "/api/scheduled-maintenance/",
            {
                "asset": asset.id,
                "reason": "Missing due date and type",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("due_date", response.data)
        self.assertIn("maintenance_type", response.data)

    def test_scheduled_maintenance_permissions_match_maintenance_logging(self):
        asset = self._create_asset(prefix="SCHED")
        scheduled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today(),
            reason="Scheduled inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            created_by=self.profile,
        )
        viewer = self._create_role_user(
            "scheduled_viewer",
            "Scheduled Viewer",
            ["view_all_assets", "view_asset_history"],
        )
        self._auth_as(viewer)

        list_response = self.client.get("/api/scheduled-maintenance/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)

        detail_response = self.client.get(f"/api/scheduled-maintenance/{scheduled.id}/")
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)

        create_response = self.client.post(
            "/api/scheduled-maintenance/",
            {
                "asset": asset.id,
                "due_date": str(date.today()),
                "reason": "Attempted maintenance",
                "maintenance_type": ScheduledMaintenance.MaintenanceType.REPAIR,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_403_FORBIDDEN)

        complete_response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/complete/",
            {
                "date": str(date.today()),
                "reason": "Attempted completion",
            },
            format="json",
        )
        self.assertEqual(complete_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_can_view_scheduled_maintenance_for_own_active_asset(self):
        employee_user = User.objects.create_user(
            username="scheduled_owner",
            email="scheduled_owner@example.com",
            password="pass123",
        )
        history_perm = self._permission("view_asset_history")
        employee_user.profile.add_permission(history_perm)
        own_asset = self._create_asset(prefix="SCHEDOWN")
        other_asset = self._create_asset(prefix="SCHEDOTHER")
        Assignment.objects.create(
            asset=own_asset,
            employee=employee_user.profile,
            assigned_by=self.profile,
            notes="Active owner schedule visibility",
        )
        own_schedule = ScheduledMaintenance.objects.create(
            asset=own_asset,
            due_date=date.today() + timedelta(days=3),
            reason="Own assigned asset inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            created_by=self.profile,
        )
        other_schedule = ScheduledMaintenance.objects.create(
            asset=other_asset,
            due_date=date.today() + timedelta(days=3),
            reason="Other asset inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            created_by=self.profile,
        )
        own_log = ReplacementLog.objects.create(
            asset=own_asset,
            reason="Own asset historical maintenance",
            date=date.today(),
            replaced_by=self.profile,
        )
        ReplacementLog.objects.create(
            asset=other_asset,
            reason="Other asset historical maintenance",
            date=date.today(),
            replaced_by=self.profile,
        )

        self._auth_as(employee_user)
        list_response = self.client.get("/api/scheduled-maintenance/")

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item["id"] for item in list_response.data],
            [own_schedule.id],
        )

        own_detail = self.client.get(f"/api/scheduled-maintenance/{own_schedule.id}/")
        self.assertEqual(own_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(own_detail.data["id"], own_schedule.id)

        other_detail = self.client.get(
            f"/api/scheduled-maintenance/{other_schedule.id}/"
        )
        self.assertEqual(other_detail.status_code, status.HTTP_403_FORBIDDEN)

        logs_response = self.client.get("/api/replacement-logs/")
        self.assertEqual(logs_response.status_code, status.HTTP_403_FORBIDDEN)

        log_detail_response = self.client.get(f"/api/replacement-logs/{own_log.id}/")
        self.assertEqual(log_detail_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_returned_asset_scheduled_maintenance_hidden_from_owner(self):
        employee_user = User.objects.create_user(
            username="returned_scheduled_owner",
            email="returned_scheduled_owner@example.com",
            password="pass123",
        )
        asset = self._create_asset(prefix="SCHEDRET")
        Assignment.objects.create(
            asset=asset,
            employee=employee_user.profile,
            assigned_by=self.profile,
            returned_at=timezone.now(),
            notes="Returned owner schedule visibility",
        )
        scheduled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() + timedelta(days=3),
            reason="Returned asset inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            created_by=self.profile,
        )

        self._auth_as(employee_user)
        list_response = self.client.get("/api/scheduled-maintenance/")
        detail_response = self.client.get(f"/api/scheduled-maintenance/{scheduled.id}/")

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data, [])
        self.assertEqual(detail_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_completed_or_cancelled_maintenance_hidden_from_owner(self):
        employee_user = User.objects.create_user(
            username="finished_scheduled_owner",
            email="finished_scheduled_owner@example.com",
            password="pass123",
        )
        asset = self._create_asset(prefix="SCHEDFIN")
        Assignment.objects.create(
            asset=asset,
            employee=employee_user.profile,
            assigned_by=self.profile,
            notes="Finished owner schedule visibility",
        )
        completed = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() - timedelta(days=1),
            reason="Completed inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            status=ScheduledMaintenance.Status.COMPLETED,
            created_by=self.profile,
        )
        cancelled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() - timedelta(days=1),
            reason="Cancelled repair",
            maintenance_type=ScheduledMaintenance.MaintenanceType.REPAIR,
            status=ScheduledMaintenance.Status.CANCELLED,
            created_by=self.profile,
        )

        self._auth_as(employee_user)
        list_response = self.client.get("/api/scheduled-maintenance/")
        completed_detail = self.client.get(
            f"/api/scheduled-maintenance/{completed.id}/"
        )
        cancelled_detail = self.client.get(
            f"/api/scheduled-maintenance/{cancelled.id}/"
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data, [])
        self.assertEqual(completed_detail.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(cancelled_detail.status_code, status.HTTP_403_FORBIDDEN)

    def test_scheduled_maintenance_patch_updates_fields_and_ignores_read_only_fields(
        self,
    ):
        asset = self._create_asset(prefix="SCHED")
        owner_user = User.objects.create_user(
            username="patch_owner",
            email="patch_owner@example.com",
            password="pass123",
        )
        spoofed_creator = User.objects.create_user(
            username="spoofed_creator",
            email="spoofed_creator@example.com",
            password="pass123",
        )
        scheduled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() + timedelta(days=3),
            reason="Initial maintenance plan",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            created_by=self.profile,
        )

        response = self.client.patch(
            f"/api/scheduled-maintenance/{scheduled.id}/",
            {
                "due_date": str(date.today() + timedelta(days=10)),
                "reason": "Updated maintenance plan",
                "maintenance_type": ScheduledMaintenance.MaintenanceType.REPAIR,
                "owner": owner_user.profile.id,
                "estimated_cost": "95.00",
                "vendor": "Updated vendor",
                "status": ScheduledMaintenance.Status.COMPLETED,
                "cancelled_reason": "Client attempted to cancel",
                "created_by": spoofed_creator.profile.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["reason"], "Updated maintenance plan")
        self.assertEqual(response.data["owner"], owner_user.profile.id)
        self.assertEqual(response.data["vendor"], "Updated vendor")
        self.assertEqual(response.data["status"], ScheduledMaintenance.Status.SCHEDULED)
        self.assertEqual(response.data["cancelled_reason"], "")
        self.assertEqual(response.data["created_by"], self.profile.id)

        scheduled.refresh_from_db()
        self.assertEqual(scheduled.created_by, self.profile)
        self.assertEqual(scheduled.status, ScheduledMaintenance.Status.SCHEDULED)

    def test_scheduled_maintenance_completion_creates_historical_log(self):
        asset = self._create_asset(prefix="SCHED")
        asset.status = AssetStatus.DAMAGED
        asset.condition = AssetCondition.DAMAGED
        asset.save(update_fields=["status", "condition"])
        replacement_asset = self._create_asset(prefix="NEWSCHED")
        scheduled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() - timedelta(days=1),
            reason="Repair damaged laptop",
            maintenance_type=ScheduledMaintenance.MaintenanceType.REPAIR,
            owner=self.profile,
            estimated_cost="125.00",
            vendor="Local repair shop",
            created_by=self.profile,
        )

        response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/complete/",
            {
                "date": str(date.today()),
                "reason": "Repair completed and replacement issued",
                "cost": "150.00",
                "asset_status_after": AssetStatus.RETIRED,
                "asset_condition_after": AssetCondition.POOR,
                "replacement_asset": replacement_asset.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ScheduledMaintenance.Status.COMPLETED)
        self.assertIsNotNone(response.data["completed_log"])

        scheduled.refresh_from_db()
        replacement_log = scheduled.completed_log
        self.assertEqual(replacement_log.asset, asset)
        self.assertEqual(
            replacement_log.reason, "Repair completed and replacement issued"
        )
        self.assertEqual(replacement_log.replaced_by, self.profile)
        self.assertEqual(replacement_log.asset_status_before, AssetStatus.DAMAGED)
        self.assertEqual(replacement_log.asset_condition_before, AssetCondition.DAMAGED)
        self.assertEqual(replacement_log.asset_status_after, AssetStatus.RETIRED)
        self.assertEqual(replacement_log.asset_condition_after, AssetCondition.POOR)
        self.assertEqual(replacement_log.replacement_asset, replacement_asset)

        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.RETIRED)
        self.assertEqual(asset.condition, AssetCondition.POOR)

        second_response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/complete/",
            {
                "date": str(date.today()),
                "reason": "Second completion attempt",
            },
            format="json",
        )
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scheduled_maintenance_completion_requires_date_and_reason_then_defaults_snapshots(
        self,
    ):
        asset = self._create_asset(prefix="SCHED")
        asset.status = AssetStatus.DAMAGED
        asset.condition = AssetCondition.FAIR
        asset.save(update_fields=["status", "condition"])
        scheduled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today(),
            reason="Inspect damaged laptop",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            created_by=self.profile,
        )

        invalid_response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/complete/",
            {},
            format="json",
        )

        self.assertEqual(invalid_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date", invalid_response.data)
        self.assertIn("reason", invalid_response.data)

        response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/complete/",
            {
                "date": str(date.today()),
                "reason": "Inspection completed",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ScheduledMaintenance.Status.COMPLETED)
        self.assertIsNotNone(response.data["completed_log_details"])

        scheduled.refresh_from_db()
        replacement_log = scheduled.completed_log
        self.assertEqual(replacement_log.asset_status_before, AssetStatus.DAMAGED)
        self.assertEqual(replacement_log.asset_condition_before, AssetCondition.FAIR)
        self.assertIsNone(replacement_log.asset_status_after)
        self.assertIsNone(replacement_log.asset_condition_after)
        self.assertIsNone(replacement_log.replacement_asset)
        self.assertIsNone(replacement_log.cost)

        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.DAMAGED)
        self.assertEqual(asset.condition, AssetCondition.FAIR)

    def test_scheduled_maintenance_cancel_and_lock_edits(self):
        asset = self._create_asset(prefix="SCHED")
        scheduled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() + timedelta(days=3),
            reason="Warranty inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.WARRANTY,
            created_by=self.profile,
        )

        response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/cancel/",
            {"cancelled_reason": "Vendor unavailable"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ScheduledMaintenance.Status.CANCELLED)
        self.assertEqual(response.data["cancelled_reason"], "Vendor unavailable")

        patch_response = self.client.patch(
            f"/api/scheduled-maintenance/{scheduled.id}/",
            {"reason": "Changed after cancellation"},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_400_BAD_REQUEST)

        complete_response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/complete/",
            {
                "date": str(date.today()),
                "reason": "Attempted completion after cancellation",
            },
            format="json",
        )
        self.assertEqual(complete_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_scheduled_maintenance_cancel_completed_is_rejected(self):
        asset = self._create_asset(prefix="SCHED")
        replacement_log = ReplacementLog.objects.create(
            asset=asset,
            reason="Completed maintenance log",
            date=date.today(),
            replaced_by=self.profile,
        )
        scheduled = ScheduledMaintenance.objects.create(
            asset=asset,
            due_date=date.today() - timedelta(days=1),
            reason="Already completed maintenance",
            maintenance_type=ScheduledMaintenance.MaintenanceType.REPAIR,
            status=ScheduledMaintenance.Status.COMPLETED,
            completed_log=replacement_log,
            created_by=self.profile,
        )

        response = self.client.post(
            f"/api/scheduled-maintenance/{scheduled.id}/cancel/",
            {"cancelled_reason": "Cancel completed item"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, ScheduledMaintenance.Status.COMPLETED)
        self.assertEqual(scheduled.cancelled_reason, "")

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

    def test_asset_available_filter_uses_availability_rules(self):
        """Available filter should match active status and active assignments."""
        available_asset = self._create_asset(prefix="AVAILABLE")
        assigned_asset = self._create_asset(prefix="ASSIGNED")
        inactive_asset = self._create_asset(prefix="INACTIVE")
        inactive_asset.status = AssetStatus.DAMAGED
        inactive_asset.save(update_fields=["status"])

        Assignment.objects.create(
            asset=assigned_asset,
            employee=self.profile,
            notes="Currently assigned",
        )

        response = self.client.get("/api/assets/?available=true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in response.data},
            {available_asset.id},
        )

        response = self.client.get("/api/assets/?available=false")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in response.data},
            {assigned_asset.id, inactive_asset.id},
        )

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
        """Test that a user without assign permission cannot assign assets but can return with default access."""
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

        # Attempt assignment without `assign_assets`
        response = self.client.post("/api/assignments/", assignment_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Create an assignment as HR, then ensure the normal user can request return by default
        self._auth_as(self.user)
        assignee_asset = Asset.objects.create(
            asset_id="RETURNDEFAULT001",
            name="Return Default Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )
        created = self.client.post(
            "/api/assignments/",
            {
                "asset": assignee_asset.id,
                "employee": normal_user.profile.id,
                "notes": "default return permission seed",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        response = self.client.post(
            f"/api/assignments/{created.data['id']}/request-return/",
            {"notes": "request return by owner"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Normal user cannot approve the return request
        response = self.client.post(
            f"/api/assignments/{created.data['id']}/return/",
            {"return_condition": AssetCondition.GOOD, "notes": "returned by owner"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_new_user_gets_default_view_own_assets_permission(self):
        """Newly created users should automatically receive own-assets view permission."""
        user = User.objects.create_user(
            username="defaultpermuser",
            email="defaultperm@example.com",
            password="password123",
        )
        perm = Permission.objects.get(
            module_name="Asset Management",
            feature_action="view_own_assets",
        )

        self.assertTrue(user.profile.has_permission(perm))

    def test_new_user_gets_default_initiate_asset_return_permission(self):
        """Newly created users should automatically receive permission to initiate returns."""
        user = User.objects.create_user(
            username="defaultreturnuser",
            email="defaultreturn@example.com",
            password="password123",
        )
        perm = Permission.objects.get(
            module_name="Asset Management",
            feature_action="initiate_asset_return",
        )

        self.assertTrue(user.profile.has_permission(perm))

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
            f"/api/assignments/{assignment_id}/request-return/",
            {"notes": "Request return during test"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

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

    def test_view_own_assets_scope(self):
        """Employee with only view_own_assets sees only their own active assignments."""
        from core.models import Permission

        # Create an employee user with only view_own_assets
        employee_user = User.objects.create_user(
            username="empscope", email="empscope@example.com", password="pass123"
        )
        employee_profile = employee_user.profile

        own_perm, _ = Permission.objects.get_or_create(
            module_name="Asset Management", feature_action="view_own_assets"
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

    def test_own_assigned_assets_list_excludes_other_users_assets(self):
        """A user should only see assets actively assigned to their own profile."""
        employee_user = User.objects.create_user(
            username="assetscope", email="assetscope@example.com", password="pass123"
        )
        other_user = User.objects.create_user(
            username="assetscope_other",
            email="assetscope.other@example.com",
            password="pass123",
        )

        own_asset = Asset.objects.create(
            asset_id="OWNVIS001",
            name="Own Visible Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=5),
            status=AssetStatus.ACTIVE,
        )
        other_asset = Asset.objects.create(
            asset_id="OTHERVIS001",
            name="Other Visible Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=5),
            status=AssetStatus.ACTIVE,
        )

        self._auth_as(self.user)
        own_created = self.client.post(
            "/api/assignments/",
            {
                "asset": own_asset.id,
                "employee": employee_user.profile.id,
                "notes": "own asset visibility",
            },
            format="json",
        )
        self.assertEqual(own_created.status_code, status.HTTP_201_CREATED)

        other_created = self.client.post(
            "/api/assignments/",
            {
                "asset": other_asset.id,
                "employee": other_user.profile.id,
                "notes": "other asset visibility",
            },
            format="json",
        )
        self.assertEqual(other_created.status_code, status.HTTP_201_CREATED)

        self._auth_as(employee_user)
        response = self.client.get("/api/assets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        asset_ids = {asset["asset_id"] for asset in response.data}
        self.assertIn(own_asset.asset_id, asset_ids)
        self.assertNotIn(other_asset.asset_id, asset_ids)

    def test_returned_assigned_asset_is_hidden_from_own_asset_scope(self):
        """Once an asset is returned, it should no longer appear in the user's own asset list."""
        employee_user = User.objects.create_user(
            username="returnscope", email="returnscope@example.com", password="pass123"
        )

        asset = Asset.objects.create(
            asset_id="RETVIS001",
            name="Return Visibility Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=5),
            status=AssetStatus.ACTIVE,
        )

        self._auth_as(self.user)
        created = self.client.post(
            "/api/assignments/",
            {
                "asset": asset.id,
                "employee": employee_user.profile.id,
                "notes": "return visibility",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        self._auth_as(employee_user)
        before_return = self.client.get("/api/assets/")
        self.assertEqual(before_return.status_code, status.HTTP_200_OK)
        self.assertIn(asset.asset_id, {a["asset_id"] for a in before_return.data})

        self._auth_as(employee_user)
        requested = self.client.post(
            f"/api/assignments/{created.data['id']}/request-return/",
            {"notes": "request for return visibility"},
            format="json",
        )
        self.assertEqual(requested.status_code, status.HTTP_200_OK)

        self._auth_as(self.user)
        returned = self.client.post(
            f"/api/assignments/{created.data['id']}/return/",
            {"return_condition": AssetCondition.GOOD, "notes": "returned"},
            format="json",
        )
        self.assertEqual(returned.status_code, status.HTTP_200_OK)

        self._auth_as(employee_user)
        after_return = self.client.get("/api/assets/")
        self.assertEqual(after_return.status_code, status.HTTP_200_OK)
        self.assertNotIn(asset.asset_id, {a["asset_id"] for a in after_return.data})

    def test_own_assignment_list_only_shows_user_assignments(self):
        """Assignment list should only include assignments belonging to the authenticated user."""
        employee_user = User.objects.create_user(
            username="assignmentscope",
            email="assignmentscope@example.com",
            password="pass123",
        )
        other_user = User.objects.create_user(
            username="assignmentscope_other",
            email="assignmentscope.other@example.com",
            password="pass123",
        )

        own_asset = Asset.objects.create(
            asset_id="OWNASSIGN001",
            name="Own Assignment Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=10),
            status=AssetStatus.ACTIVE,
        )
        other_asset = Asset.objects.create(
            asset_id="OTHERASSIGN001",
            name="Other Assignment Asset",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=10),
            status=AssetStatus.ACTIVE,
        )

        self._auth_as(self.user)
        own_created = self.client.post(
            "/api/assignments/",
            {
                "asset": own_asset.id,
                "employee": employee_user.profile.id,
                "notes": "own assignment visibility",
            },
            format="json",
        )
        self.assertEqual(own_created.status_code, status.HTTP_201_CREATED)

        other_created = self.client.post(
            "/api/assignments/",
            {
                "asset": other_asset.id,
                "employee": other_user.profile.id,
                "notes": "other assignment visibility",
            },
            format="json",
        )
        self.assertEqual(other_created.status_code, status.HTTP_201_CREATED)

        self._auth_as(employee_user)
        response = self.client.get("/api/assignments/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assignment_asset_ids = {
            item["asset_details"]["asset_id"] for item in response.data
        }
        self.assertIn(own_asset.asset_id, assignment_asset_ids)
        self.assertNotIn(other_asset.asset_id, assignment_asset_ids)

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
        """PDF role matrix: all roles can initiate, but only HR/Admin can approve returns."""
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

            requester = employee
            self._auth_as(requester)
            requested = self.client.post(
                f"/api/assignments/{created.data['id']}/request-return/",
                {"notes": "request before approval"},
                format="json",
            )
            self.assertEqual(requested.status_code, status.HTTP_200_OK)

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

        requested = self.client.post(
            f"/api/assignments/{created.data['id']}/request-return/",
            {"notes": "first request"},
            format="json",
        )
        self.assertEqual(requested.status_code, status.HTTP_200_OK)

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

    def test_reject_pending_return_and_allow_re_request(self):
        """HR can reject a pending return request and the employee can request again."""
        employee_user = User.objects.create_user(
            username="rejectcycle",
            email="rejectcycle@example.com",
            password="pass123",
        )
        employee_profile = employee_user.profile
        employee_profile.add_permission(self._permission("initiate_asset_return"))

        asset = self._create_asset(prefix="REJECTFLOW")

        self._auth_as(self.user)
        created = self.client.post(
            "/api/assignments/",
            {
                "asset": asset.id,
                "employee": employee_profile.id,
                "notes": "seed reject flow",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        self._auth_as(employee_user)
        first_request = self.client.post(
            f"/api/assignments/{created.data['id']}/request-return/",
            {"notes": "first request"},
            format="json",
        )
        self.assertEqual(first_request.status_code, status.HTTP_200_OK)
        self.assertEqual(
            first_request.data["return_request_status"],
            Assignment.ReturnRequestStatus.PENDING,
        )

        self._auth_as(self.user)
        reject_response = self.client.post(
            f"/api/assignments/{created.data['id']}/reject-return/",
            {"rejection_reason": "Need additional asset check"},
            format="json",
        )
        self.assertEqual(reject_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            reject_response.data["return_request_status"],
            Assignment.ReturnRequestStatus.REJECTED,
        )
        self.assertEqual(
            reject_response.data["return_rejection_reason"],
            "Need additional asset check",
        )

        self._auth_as(employee_user)
        second_request = self.client.post(
            f"/api/assignments/{created.data['id']}/request-return/",
            {"notes": "second request"},
            format="json",
        )
        self.assertEqual(second_request.status_code, status.HTTP_200_OK)
        self.assertEqual(
            second_request.data["return_request_status"],
            Assignment.ReturnRequestStatus.PENDING,
        )

    def test_reject_return_requires_pending_request(self):
        """Reject endpoint must fail when assignment has no pending request."""
        asset = self._create_asset(prefix="REJNOPEND")
        self._auth_as(self.user)
        created = self.client.post(
            "/api/assignments/",
            {
                "asset": asset.id,
                "employee": self.profile.id,
                "notes": "reject without pending",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        reject_response = self.client.post(
            f"/api/assignments/{created.data['id']}/reject-return/",
            {"rejection_reason": "No pending request"},
            format="json",
        )
        self.assertEqual(reject_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_request_return_for_other_users_assignment_is_forbidden(self):
        """Non-admin users can only request return for their own active assignments."""
        owner = User.objects.create_user(
            username="owner_return",
            email="owner_return@example.com",
            password="pass123",
        )
        outsider = User.objects.create_user(
            username="outsider_return",
            email="outsider_return@example.com",
            password="pass123",
        )

        asset = self._create_asset(prefix="OWNRET")
        self._auth_as(self.user)
        created = self.client.post(
            "/api/assignments/",
            {
                "asset": asset.id,
                "employee": owner.profile.id,
                "notes": "ownership guard",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        self._auth_as(outsider)
        response = self.client.post(
            f"/api/assignments/{created.data['id']}/request-return/",
            {"notes": "attempt for someone else"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_return_request_queue_endpoint(self):
        """HR users can fetch pending return requests; non-approvers cannot."""
        employee_user = User.objects.create_user(
            username="queue_employee",
            email="queue_employee@example.com",
            password="pass123",
        )

        asset = self._create_asset(prefix="QUEUE")
        self._auth_as(self.user)
        created = self.client.post(
            "/api/assignments/",
            {
                "asset": asset.id,
                "employee": employee_user.profile.id,
                "notes": "queue seed",
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        self._auth_as(employee_user)
        requested = self.client.post(
            f"/api/assignments/{created.data['id']}/request-return/",
            {"notes": "needs hr review"},
            format="json",
        )
        self.assertEqual(requested.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(requested.data.get("return_requested"))
        self.assertEqual(
            requested.data["return_requested"]["status"],
            Assignment.ReturnRequestStatus.PENDING,
        )

        self._auth_as(self.user)
        queue_response = self.client.get("/api/return-requests/?status=pending")
        self.assertEqual(queue_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(queue_response.data), 1)
        self.assertEqual(queue_response.data[0]["assignment_id"], created.data["id"])

        self._auth_as(employee_user)
        forbidden = self.client.get("/api/return-requests/?status=pending")
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

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

    def test_asset_create_with_category(self):
        """Creating an asset persists the provided category value."""
        response = self.client.post(
            "/api/assets/",
            {
                "asset_id": "CAT001",
                "name": "Phone Device",
                "category": AssetCategory.PHONES,
                "condition": AssetCondition.GOOD,
                "purchase_date": str(date.today() - timedelta(days=7)),
                "status": AssetStatus.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["category"], AssetCategory.PHONES)

    def test_asset_create_defaults_category_to_other(self):
        """Creating an asset without category should use the default category."""
        response = self.client.post(
            "/api/assets/",
            {
                "asset_id": "CATDEF01",
                "name": "Default Category Asset",
                "condition": AssetCondition.GOOD,
                "purchase_date": str(date.today() - timedelta(days=7)),
                "status": AssetStatus.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["category"], AssetCategory.OTHER)

    def test_asset_list_filter_by_category(self):
        """Asset list endpoint supports filtering by category."""
        Asset.objects.create(
            asset_id="CATLAP01",
            name="Laptop Filter Asset",
            category=AssetCategory.LAPTOPS,
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=20),
            status=AssetStatus.ACTIVE,
        )
        Asset.objects.create(
            asset_id="CATPHN01",
            name="Phone Filter Asset",
            category=AssetCategory.PHONES,
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=20),
            status=AssetStatus.ACTIVE,
        )

        response = self.client.get(f"/api/assets/?category={AssetCategory.PHONES}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {asset["asset_id"] for asset in response.data}
        self.assertIn("CATPHN01", returned_ids)
        self.assertNotIn("CATLAP01", returned_ids)

    def test_asset_create_rejects_invalid_category(self):
        """Asset create should reject category values outside configured choices."""
        response = self.client.post(
            "/api/assets/",
            {
                "asset_id": "CATINV01",
                "name": "Invalid Category Asset",
                "category": "invalid_category",
                "condition": AssetCondition.GOOD,
                "purchase_date": str(date.today() - timedelta(days=7)),
                "status": AssetStatus.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("category", response.data)

    def test_asset_update_rejects_invalid_category(self):
        """Asset update should reject category values outside configured choices."""
        asset = Asset.objects.create(
            asset_id="CATUPD01",
            name="Update Category Asset",
            category=AssetCategory.LAPTOPS,
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        response = self.client.put(
            f"/api/assets/{asset.id}/",
            {
                "asset_id": asset.asset_id,
                "name": asset.name,
                "category": "not_a_real_category",
                "condition": asset.condition,
                "purchase_date": str(asset.purchase_date),
                "status": asset.status,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("category", response.data)

    def test_asset_export_requires_export_inventory_permission(self):
        """Users without export_inventory permission cannot export assets."""
        no_perm_user = User.objects.create_user(
            username="export_noperm",
            email="export_noperm@example.com",
            password="pass123",
        )
        self._auth_as(no_perm_user)

        response = self.client.post("/api/assets/export/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_asset_export_csv_with_filters_and_assignment_columns(self):
        """Export endpoint returns CSV rows filtered by payload and includes assignment data."""
        self._auth_as(self.user)
        phone_asset = Asset.objects.create(
            asset_id="EXP-PHN-001",
            name="Export Phone",
            category=AssetCategory.PHONES,
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=12),
            status=AssetStatus.ACTIVE,
        )
        Asset.objects.create(
            asset_id="EXP-LAP-001",
            name="Export Laptop",
            category=AssetCategory.LAPTOPS,
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=12),
            status=AssetStatus.ACTIVE,
        )

        Assignment.objects.create(
            asset=phone_asset,
            employee=self.profile,
            assigned_by=self.profile,
            notes="seed export assignment",
        )

        response = self.client.post(
            "/api/assets/export/",
            {
                "filters": {"category": AssetCategory.PHONES},
                "include_assignment": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("text/csv", response["Content-Type"])

        rows = list(DictReader(StringIO(response.content.decode("utf-8"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset_id"], "EXP-PHN-001")
        self.assertEqual(
            rows[0]["current_assignment_employee_id"], str(self.profile.id)
        )
        self.assertTrue(rows[0]["current_assignment_assigned_at"])

    def test_asset_export_csv_respects_selected_fields(self):
        """Export endpoint supports selecting a custom subset of exported fields."""
        self._auth_as(self.user)
        Asset.objects.create(
            asset_id="EXP-FIELDS-001",
            name="Field Export Asset",
            category=AssetCategory.OTHER,
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=3),
            status=AssetStatus.ACTIVE,
        )

        response = self.client.post(
            "/api/assets/export/",
            {
                "fields": ["asset_id", "name", "status"],
                "include_assignment": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        rows = list(DictReader(StringIO(response.content.decode("utf-8"))))
        self.assertEqual(set(rows[0].keys()), {"asset_id", "name", "status"})

    def test_return_checklist_and_description_are_persisted_and_accessible(self):
        """Test that return checklist and description are saved and accessible via API."""
        # Create an employee user
        employee_user = User.objects.create_user(
            username="employee_user",
            email="employee@example.com",
            password="pass123",
        )
        employee_profile = employee_user.profile
        employee_profile.add_permission(self._permission("initiate_asset_return"))

        # Create HR user with approve permission
        hr_user = User.objects.create_user(
            username="hr_user",
            email="hr@example.com",
            password="pass123",
            is_staff=True,
        )
        hr_profile = hr_user.profile
        hr_profile.add_permission(self._permission("process_asset_return"))

        # Create an asset and assign to employee
        asset = Asset.objects.create(
            asset_id="CHECKLIST-TEST-001",
            name="Laptop for Checklist Test",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        assignment = Assignment.objects.create(
            asset=asset,
            employee=employee_profile,
            assigned_by=self.profile,
            notes="Test assignment for checklist validation",
        )

        # Test data: create a structured checklist
        checklist_payload = [
            {"label": "Laptop returned", "completed": True},
            {"label": "Charger returned", "completed": True},
            {"label": "Badge returned", "completed": False},
        ]
        return_description = (
            "Employee packed the device and accessories in the return bag."
        )

        # Request return with checklist as the employee
        self._auth_as(employee_user)
        request_response = self.client.post(
            f"/api/assignments/{assignment.id}/request-return/",
            {
                "return_description": return_description,
                "return_checklist": checklist_payload,
                "notes": "Please process this return.",
            },
            format="json",
        )

        self.assertEqual(request_response.status_code, status.HTTP_200_OK)
        response_data = request_response.json()

        # Verify the checklist and description are returned in the response
        self.assertEqual(response_data["return_description"], return_description)
        self.assertEqual(response_data["return_checklist"], checklist_payload)

        # Verify that return_requested nested object contains the data
        self.assertIsNotNone(response_data["return_requested"])
        self.assertEqual(
            response_data["return_requested"]["return_description"], return_description
        )
        self.assertEqual(
            response_data["return_requested"]["return_checklist"], checklist_payload
        )

        # Verify data is saved in the database
        assignment.refresh_from_db()
        self.assertEqual(assignment.return_description, return_description)
        self.assertEqual(assignment.return_checklist, checklist_payload)

        # Verify HR can see the data
        self._auth_as(hr_user)
        hr_response = self.client.get(f"/api/assignments/{assignment.id}/")
        self.assertEqual(hr_response.status_code, status.HTTP_200_OK)
        hr_data = hr_response.json()
        self.assertEqual(hr_data["return_description"], return_description)
        self.assertEqual(hr_data["return_checklist"], checklist_payload)

        # Verify the data appears in the return request queue
        queue_response = self.client.get("/api/return-requests/")
        self.assertEqual(queue_response.status_code, status.HTTP_200_OK)
        queue_data = queue_response.json()
        self.assertGreater(len(queue_data), 0)

        # Find our assignment in the queue
        assignment_in_queue = None
        for item in queue_data:
            if item["assignment_id"] == assignment.id:
                assignment_in_queue = item
                break

        self.assertIsNotNone(assignment_in_queue)
        self.assertEqual(assignment_in_queue["return_description"], return_description)
        self.assertEqual(assignment_in_queue["return_checklist"], checklist_payload)

        # Verify unauthorized user cannot see the data
        outsider_user = User.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            password="pass123",
        )
        self._auth_as(outsider_user)
        outsider_response = self.client.get(f"/api/assignments/{assignment.id}/")

        # User should get 403 if they have no permission, or the data might be None
        if outsider_response.status_code == status.HTTP_200_OK:
            outsider_data = outsider_response.json()
            # If they can see the assignment, the sensitive data should be None/empty
            self.assertIsNone(outsider_data.get("return_description"))
            # return_checklist might be [] (empty list) or None
            actual_checklist = outsider_data.get("return_checklist")
            self.assertIn(actual_checklist, [None, []])
