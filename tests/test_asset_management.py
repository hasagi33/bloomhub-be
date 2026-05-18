import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    Asset,
    AssetCondition,
    AssetStatus,
    Assignment,
    ReplacementLog,
    ScheduledMaintenance,
)
from core.services.asset_qr import (
    build_asset_qr_image_path,
    ensure_asset_qr_code,
    generate_qr_png_bytes,
)


class AssetModelTestCase(TestCase):
    """Test cases for the Asset model"""

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

        self.asset_data = {
            "asset_id": "LAPTOP001",
            "name": "Dell Laptop",
            "condition": AssetCondition.GOOD,
            "warranty_until": date.today() + timedelta(days=365),
            "purchase_date": date.today() - timedelta(days=30),
            "status": AssetStatus.ACTIVE,
            "serial_number": "DL123456789",
            "model": "Dell Inspiron 15",
            "manufacturer": "Dell",
            "purchase_price": Decimal("1200.00"),
            "description": "Standard work laptop",
        }

    def test_asset_creation(self):
        """Test basic asset creation"""
        asset = Asset.objects.create(**self.asset_data)

        self.assertEqual(asset.asset_id, "LAPTOP001")
        self.assertEqual(asset.name, "Dell Laptop")
        self.assertEqual(asset.condition, AssetCondition.GOOD)
        self.assertEqual(asset.status, AssetStatus.ACTIVE)
        self.assertEqual(asset.purchase_price, Decimal("1200.00"))
        self.assertIsNotNone(asset.created_at)
        self.assertIsNotNone(asset.updated_at)

    def test_asset_str_representation(self):
        """Test asset string representation"""
        asset = Asset.objects.create(**self.asset_data)
        expected_str = f"{asset.asset_id} - {asset.name}"
        self.assertEqual(str(asset), expected_str)

    def test_asset_unique_asset_id_constraint(self):
        """Test unique constraint on asset_id"""
        Asset.objects.create(**self.asset_data)

        # Test duplicate asset_id
        with self.assertRaises(IntegrityError):
            duplicate_data = self.asset_data.copy()
            duplicate_data["serial_number"] = "DIFFERENT123"
            Asset.objects.create(**duplicate_data)

    def test_asset_unique_serial_number_constraint(self):
        """Test unique constraint on serial_number"""
        Asset.objects.create(**self.asset_data)

        # Test duplicate serial_number
        with self.assertRaises(IntegrityError):
            duplicate_data = self.asset_data.copy()
            duplicate_data["asset_id"] = "LAPTOP002"
            Asset.objects.create(**duplicate_data)

    def test_asset_is_under_warranty_property(self):
        """Test warranty status property"""
        # Asset under warranty
        asset = Asset.objects.create(**self.asset_data)
        self.assertTrue(asset.is_under_warranty)

        # Asset warranty expired
        asset.warranty_until = date.today() - timedelta(days=1)
        asset.save()
        self.assertFalse(asset.is_under_warranty)

        # Asset with no warranty date
        asset.warranty_until = None
        asset.save()
        self.assertFalse(asset.is_under_warranty)

    def test_asset_is_available_property(self):
        """Test asset availability property"""
        asset = Asset.objects.create(**self.asset_data)

        # Asset should be available when active and not assigned
        self.assertTrue(asset.is_available)

        # Asset should not be available when not active
        asset.status = AssetStatus.DAMAGED
        asset.save()
        self.assertFalse(asset.is_available)

    def test_asset_current_assignment_property(self):
        """Test current assignment property"""
        asset = Asset.objects.create(**self.asset_data)

        # No assignment initially
        self.assertIsNone(asset.current_assignment)

        # Create an assignment
        assignment = Assignment.objects.create(asset=asset, employee=self.profile)

        # Should return the current assignment
        self.assertEqual(asset.current_assignment, assignment)

        # Return the asset
        assignment.returned_at = timezone.now()
        assignment.save()

        # Should be None after return
        self.assertIsNone(asset.current_assignment)


@override_settings(FRONTEND_URL="https://app.example.com")
class AssetQRCodeTestCase(TestCase):
    """Test cases for stable Asset QR code generation"""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override_media_root = override_settings(MEDIA_ROOT=self.media_root)
        self.override_media_root.enable()

    def tearDown(self):
        self.override_media_root.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def create_asset(self, asset_id="QR001"):
        return Asset.objects.create(
            asset_id=asset_id,
            name="QR Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

    def test_asset_creation_generates_stable_qr_payload_and_image(self):
        asset = self.create_asset()

        self.assertEqual(
            asset.qr_code_payload, f"https://app.example.com/assets/{asset.pk}"
        )
        self.assertEqual(
            asset.qr_code_image.name,
            f"asset_qr_codes/{asset.pk}/Asset-QR_Laptop-{asset.pk}-QR.png",
        )
        self.assertTrue(default_storage.exists(asset.qr_code_image.name))
        with default_storage.open(asset.qr_code_image.name, "rb") as qr_file:
            self.assertEqual(qr_file.read(8), b"\x89PNG\r\n\x1a\n")

    def test_asset_qr_image_filename_uses_asset_name_and_id(self):
        asset = Asset.objects.create(
            asset_id="QR-NAME",
            name="MacBook Pro / 16",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        self.assertEqual(
            asset.qr_code_image.name,
            f"asset_qr_codes/{asset.pk}/Asset-MacBook_Pro__16-{asset.pk}-QR.png",
        )

    def test_two_assets_have_unique_payloads_and_image_paths(self):
        first = self.create_asset("QR001")
        second = self.create_asset("QR002")

        self.assertNotEqual(first.qr_code_payload, second.qr_code_payload)
        self.assertNotEqual(first.qr_code_image.name, second.qr_code_image.name)

    def test_asset_updates_do_not_change_qr_payload_or_image_path(self):
        asset = self.create_asset()
        payload = asset.qr_code_payload
        image_path = asset.qr_code_image.name

        asset.status = AssetStatus.DAMAGED
        asset.condition = AssetCondition.DAMAGED
        asset.warranty_until = date.today() + timedelta(days=90)
        asset.description = "Updated metadata"
        asset.save()
        asset.refresh_from_db()

        self.assertEqual(asset.qr_code_payload, payload)
        self.assertEqual(asset.qr_code_image.name, image_path)

    def test_missing_qr_image_can_be_regenerated_from_stable_payload(self):
        asset = self.create_asset()
        payload = asset.qr_code_payload
        image_path = asset.qr_code_image.name
        default_storage.delete(image_path)

        ensure_asset_qr_code(asset, regenerate_image=True)
        asset.refresh_from_db()

        self.assertEqual(asset.qr_code_payload, payload)
        self.assertEqual(asset.qr_code_image.name, image_path)
        self.assertTrue(default_storage.exists(image_path))

    def test_path_only_qr_payload_is_replaced_with_frontend_url(self):
        asset = self.create_asset()
        image_path = asset.qr_code_image.name
        Asset.objects.filter(pk=asset.pk).update(qr_code_payload=f"/asset/{asset.pk}")
        asset.refresh_from_db()

        ensure_asset_qr_code(asset)
        asset.refresh_from_db()

        self.assertEqual(
            asset.qr_code_payload, f"https://app.example.com/assets/{asset.pk}"
        )
        self.assertEqual(asset.qr_code_image.name, image_path)
        self.assertTrue(default_storage.exists(image_path))

    def test_existing_qr_image_is_regenerated_with_frontend_url_payload(self):
        asset = self.create_asset()
        image_path = asset.qr_code_image.name
        Asset.objects.filter(pk=asset.pk).update(qr_code_payload=f"/asset/{asset.pk}")
        asset.refresh_from_db()

        with patch(
            "core.services.asset_qr.generate_qr_png_bytes",
            wraps=generate_qr_png_bytes,
        ) as mock_generate:
            ensure_asset_qr_code(asset)

        asset.refresh_from_db()
        expected_payload = f"https://app.example.com/assets/{asset.pk}"

        mock_generate.assert_called_once_with(expected_payload)
        self.assertEqual(asset.qr_code_payload, expected_payload)
        self.assertEqual(asset.qr_code_image.name, image_path)
        self.assertTrue(default_storage.exists(image_path))

    @override_settings(FRONTEND_URL="http://localhost:3000")
    def test_existing_qr_payload_uses_local_frontend_prefix(self):
        asset = self.create_asset()
        Asset.objects.filter(pk=asset.pk).update(qr_code_payload=f"/asset/{asset.pk}")
        asset.refresh_from_db()

        ensure_asset_qr_code(asset)
        asset.refresh_from_db()

        self.assertEqual(
            asset.qr_code_payload, f"http://localhost:3000/assets/{asset.pk}"
        )

    @override_settings(FRONTEND_URL="https://bloomhub-fe-dev.vercel.app")
    def test_existing_qr_payload_uses_dev_frontend_prefix(self):
        asset = self.create_asset()
        Asset.objects.filter(pk=asset.pk).update(qr_code_payload=f"/asset/{asset.pk}")
        asset.refresh_from_db()

        ensure_asset_qr_code(asset)
        asset.refresh_from_db()

        self.assertEqual(
            asset.qr_code_payload,
            f"https://bloomhub-fe-dev.vercel.app/assets/{asset.pk}",
        )

    def test_existing_target_path_is_overwritten_without_suffix(self):
        asset = self.create_asset()
        image_path = asset.qr_code_image.name
        Asset.objects.filter(pk=asset.pk).update(qr_code_image="")
        asset.refresh_from_db()

        ensure_asset_qr_code(asset)
        asset.refresh_from_db()

        self.assertEqual(asset.qr_code_image.name, image_path)
        self.assertTrue(default_storage.exists(image_path))

    def test_existing_old_qr_image_path_is_replaced_with_named_path(self):
        asset = self.create_asset()
        old_image_path = f"asset_qr_codes/{asset.pk}/qr.png"
        expected_image_path = build_asset_qr_image_path(asset)
        Asset.objects.filter(pk=asset.pk).update(qr_code_image=old_image_path)
        asset.refresh_from_db()

        ensure_asset_qr_code(asset)
        asset.refresh_from_db()

        self.assertEqual(asset.qr_code_image.name, expected_image_path)
        self.assertTrue(default_storage.exists(expected_image_path))

    def test_failed_qr_image_replacement_keeps_old_image(self):
        asset = self.create_asset()
        old_image_path = "asset_qr_codes/stale/qr.png"
        with default_storage.open(asset.qr_code_image.name, "rb") as qr_file:
            default_storage.save(old_image_path, ContentFile(qr_file.read()))
        Asset.objects.filter(pk=asset.pk).update(qr_code_image=old_image_path)
        asset.refresh_from_db()

        with patch.object(asset.qr_code_image, "save", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                ensure_asset_qr_code(asset)

        self.assertTrue(default_storage.exists(old_image_path))

    def test_backfill_updates_existing_stale_qr_payload_and_image_path(self):
        asset = self.create_asset()
        Asset.objects.filter(pk=asset.pk).update(
            qr_code_payload=f"/asset/{asset.pk}",
            qr_code_image=f"asset_qr_codes/{asset.pk}/qr.png",
        )

        dry_run_output = StringIO()
        call_command("backfill_asset_qr_codes", "--dry-run", stdout=dry_run_output)
        self.assertIn("would update 1 asset QR codes", dry_run_output.getvalue())

        call_command("backfill_asset_qr_codes", stdout=StringIO())
        asset.refresh_from_db()

        self.assertEqual(
            asset.qr_code_payload, f"https://app.example.com/assets/{asset.pk}"
        )
        self.assertEqual(asset.qr_code_image.name, build_asset_qr_image_path(asset))


class AssignmentModelTestCase(TestCase):
    """Test cases for the Assignment model"""

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.profile = self.user.profile

        self.manager = User.objects.create_user(
            username="manager", email="manager@example.com", password="testpass123"
        )
        self.manager_profile = self.manager.profile

        self.asset = Asset.objects.create(
            asset_id="LAPTOP001",
            name="Dell Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

    def test_assignment_creation(self):
        """Test basic assignment creation"""
        assignment = Assignment.objects.create(
            asset=self.asset,
            employee=self.profile,
            assigned_by=self.manager_profile,
            notes="Initial assignment",
        )

        self.assertEqual(assignment.asset, self.asset)
        self.assertEqual(assignment.employee, self.profile)
        self.assertEqual(assignment.assigned_by, self.manager_profile)
        self.assertEqual(assignment.notes, "Initial assignment")
        self.assertIsNotNone(assignment.assigned_at)
        self.assertIsNone(assignment.returned_at)

    def test_assignment_str_representation(self):
        """Test assignment string representation"""
        assignment = Assignment.objects.create(asset=self.asset, employee=self.profile)

        expected_str = f"{self.asset.asset_id} → {self.profile.user.get_full_name() or self.profile.user.username} (Active)"
        self.assertEqual(str(assignment), expected_str)

    def test_assignment_is_active_property(self):
        """Test assignment active status property"""
        assignment = Assignment.objects.create(asset=self.asset, employee=self.profile)

        # Should be active initially
        self.assertTrue(assignment.is_active)

        # Should not be active after return
        assignment.returned_at = timezone.now()
        assignment.save()
        self.assertFalse(assignment.is_active)

    def test_assignment_duration_days_property(self):
        """Test assignment duration calculation"""
        assignment = Assignment.objects.create(asset=self.asset, employee=self.profile)

        # Duration should be 0 for same day
        self.assertEqual(assignment.duration_days, 0)

        # Test with returned assignment
        assignment.returned_at = timezone.now() + timedelta(days=5)
        assignment.save()
        self.assertEqual(assignment.duration_days, 5)

    def test_assignment_return_process(self):
        """Test asset return process"""
        assignment = Assignment.objects.create(asset=self.asset, employee=self.profile)

        # Asset should not be available when assigned
        self.assertFalse(self.asset.is_available)

        # Return the asset
        assignment.returned_at = timezone.now()
        assignment.return_condition = AssetCondition.FAIR
        assignment.save()

        # Asset should be available after return
        self.assertTrue(self.asset.is_available)
        self.assertEqual(assignment.return_condition, AssetCondition.FAIR)


class ReplacementLogModelTestCase(TestCase):
    """Test cases for the ReplacementLog model"""

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        self.profile = self.user.profile

        self.asset = Asset.objects.create(
            asset_id="LAPTOP001",
            name="Dell Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

        self.replacement_asset = Asset.objects.create(
            asset_id="LAPTOP002",
            name="Dell Laptop Replacement",
            condition=AssetCondition.EXCELLENT,
            purchase_date=date.today(),
            status=AssetStatus.ACTIVE,
        )

    def test_replacement_log_creation(self):
        """Test basic replacement log creation"""
        replacement_log = ReplacementLog.objects.create(
            asset=self.asset,
            reason="Screen damage due to coffee spill",
            date=date.today(),
            replaced_by=self.profile,
            replacement_asset=self.replacement_asset,
            cost=Decimal("150.00"),
        )

        self.assertEqual(replacement_log.asset, self.asset)
        self.assertEqual(replacement_log.reason, "Screen damage due to coffee spill")
        self.assertEqual(replacement_log.replaced_by, self.profile)
        self.assertEqual(replacement_log.replacement_asset, self.replacement_asset)
        self.assertEqual(replacement_log.cost, Decimal("150.00"))
        self.assertEqual(replacement_log.date, date.today())

    def test_replacement_log_can_store_asset_state_snapshots(self):
        """Test replacement log state snapshots"""
        replacement_log = ReplacementLog.objects.create(
            asset=self.asset,
            reason="Screen damage due to coffee spill",
            date=date.today(),
            asset_status_before=AssetStatus.DAMAGED,
            asset_status_after=AssetStatus.RETURNED,
            asset_condition_before=AssetCondition.DAMAGED,
            asset_condition_after=AssetCondition.POOR,
        )

        self.assertEqual(replacement_log.asset_status_before, AssetStatus.DAMAGED)
        self.assertEqual(replacement_log.asset_status_after, AssetStatus.RETURNED)
        self.assertEqual(replacement_log.asset_condition_before, AssetCondition.DAMAGED)
        self.assertEqual(replacement_log.asset_condition_after, AssetCondition.POOR)

    def test_replacement_log_str_representation(self):
        """Test replacement log string representation"""
        replacement_log = ReplacementLog.objects.create(
            asset=self.asset,
            reason="Screen damage due to coffee spill",
            date=date.today(),
        )

        expected_str = f"{self.asset.asset_id} replaced on {replacement_log.date} - Screen damage due to coffee spill"
        self.assertEqual(str(replacement_log), expected_str)

    def test_replacement_log_reason_truncation(self):
        """Test reason truncation in string representation"""
        long_reason = "This is a very long reason that should be truncated in the string representation to ensure it doesn't become too long"
        replacement_log = ReplacementLog.objects.create(
            asset=self.asset,
            reason=long_reason,
            date=date.today(),
        )

        str_repr = str(replacement_log)
        # Should contain truncated reason (first 50 characters)
        self.assertIn(long_reason[:50], str_repr)


class ScheduledMaintenanceModelTestCase(TestCase):
    """Test cases for the ScheduledMaintenance model"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="maintenanceuser",
            email="maintenance@example.com",
            password="testpass123",
        )
        self.profile = self.user.profile
        self.asset = Asset.objects.create(
            asset_id="MAINT001",
            name="Maintenance Laptop",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
        )

    def test_scheduled_maintenance_due_state_tracks_scheduled_dates_only(self):
        overdue = ScheduledMaintenance.objects.create(
            asset=self.asset,
            due_date=date.today() - timedelta(days=1),
            reason="Overdue inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.INSPECTION,
            created_by=self.profile,
        )
        due_today = ScheduledMaintenance.objects.create(
            asset=self.asset,
            due_date=date.today(),
            reason="Due today inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.PREVENTIVE,
            created_by=self.profile,
        )
        upcoming = ScheduledMaintenance.objects.create(
            asset=self.asset,
            due_date=date.today() + timedelta(days=1),
            reason="Upcoming inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.REPAIR,
            created_by=self.profile,
        )
        completed = ScheduledMaintenance.objects.create(
            asset=self.asset,
            due_date=date.today() - timedelta(days=1),
            reason="Completed inspection",
            maintenance_type=ScheduledMaintenance.MaintenanceType.WARRANTY,
            status=ScheduledMaintenance.Status.COMPLETED,
            created_by=self.profile,
        )

        self.assertEqual(overdue.due_state, "overdue")
        self.assertEqual(due_today.due_state, "due_today")
        self.assertEqual(upcoming.due_state, "upcoming")
        self.assertIsNone(completed.due_state)

    def test_scheduled_maintenance_str_representation(self):
        scheduled = ScheduledMaintenance.objects.create(
            asset=self.asset,
            due_date=date.today() + timedelta(days=7),
            reason="Warranty check",
            maintenance_type=ScheduledMaintenance.MaintenanceType.WARRANTY,
            created_by=self.profile,
        )

        self.assertEqual(
            str(scheduled),
            f"{self.asset.asset_id} maintenance due {scheduled.due_date}",
        )

    def test_scheduled_maintenance_estimated_cost_validation(self):
        with self.assertRaises(ValidationError):
            scheduled = ScheduledMaintenance(
                asset=self.asset,
                due_date=date.today(),
                reason="Invalid estimate",
                maintenance_type=ScheduledMaintenance.MaintenanceType.REPAIR,
                estimated_cost=Decimal("0.00"),
            )
            scheduled.full_clean()


class AssetManagementIntegrationTestCase(TestCase):
    """Integration tests for Asset Management models"""

    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username="employee", email="employee@example.com", password="testpass123"
        )
        self.profile = self.user.profile

        self.manager = User.objects.create_user(
            username="manager", email="manager@example.com", password="testpass123"
        )
        self.manager_profile = self.manager.profile

    def test_complete_asset_lifecycle(self):
        """Test complete asset lifecycle from creation to replacement"""
        # 1. Create asset
        asset = Asset.objects.create(
            asset_id="LAPTOP001",
            name="Dell Laptop",
            condition=AssetCondition.EXCELLENT,
            warranty_until=date.today() + timedelta(days=365),
            purchase_date=date.today() - timedelta(days=30),
            status=AssetStatus.ACTIVE,
            purchase_price=Decimal("1200.00"),
        )

        self.assertTrue(asset.is_available)
        self.assertTrue(asset.is_under_warranty)

        # 2. Assign asset to employee
        assignment = Assignment.objects.create(
            asset=asset,
            employee=self.profile,
            assigned_by=self.manager_profile,
            notes="Initial laptop assignment",
        )

        self.assertFalse(asset.is_available)
        self.assertEqual(asset.current_assignment, assignment)
        self.assertTrue(assignment.is_active)

        # 3. Asset gets damaged
        asset.condition = AssetCondition.DAMAGED
        asset.status = AssetStatus.DAMAGED
        asset.save()

        # 4. Return damaged asset
        assignment.returned_at = timezone.now()
        assignment.return_condition = AssetCondition.DAMAGED
        assignment.save()

        self.assertFalse(assignment.is_active)
        self.assertIsNone(asset.current_assignment)
        self.assertFalse(
            asset.is_available
        )  # Still not available because status is DAMAGED

        # 5. Create replacement asset
        replacement_asset = Asset.objects.create(
            asset_id="LAPTOP002",
            name="Dell Laptop Replacement",
            condition=AssetCondition.EXCELLENT,
            purchase_date=date.today(),
            status=AssetStatus.ACTIVE,
            purchase_price=Decimal("1300.00"),
        )

        # 6. Log the replacement
        ReplacementLog.objects.create(
            asset=asset,
            reason="Original laptop damaged beyond repair",
            date=date.today(),
            replaced_by=self.manager_profile,
            replacement_asset=replacement_asset,
            cost=Decimal("1300.00"),
        )

        # 7. Assign new asset
        Assignment.objects.create(
            asset=replacement_asset,
            employee=self.profile,
            assigned_by=self.manager_profile,
            notes="Replacement laptop assignment",
        )

        # Verify final state
        self.assertEqual(asset.assignments.count(), 1)
        self.assertEqual(asset.replacement_logs.count(), 1)
        self.assertEqual(replacement_asset.assignments.count(), 1)
        self.assertEqual(self.profile.asset_assignments.count(), 2)
        self.assertEqual(self.manager_profile.assignments_made.count(), 2)
        self.assertEqual(self.manager_profile.replacements_made.count(), 1)

    def test_multiple_assignments_history(self):
        """Test tracking multiple assignments for the same asset"""
        asset = Asset.objects.create(
            asset_id="SHARED001",
            name="Shared Equipment",
            condition=AssetCondition.GOOD,
            purchase_date=date.today() - timedelta(days=100),
            status=AssetStatus.ACTIVE,
        )

        # Create multiple users
        users = []
        for i in range(3):
            user = User.objects.create_user(
                username=f"user{i}",
                email=f"user{i}@example.com",
                password="testpass123",
            )
            users.append(user.profile)

        # Create assignment history
        assignments = []
        for i, user_profile in enumerate(users):
            assignment = Assignment.objects.create(
                asset=asset, employee=user_profile, assigned_by=self.manager_profile
            )
            assignments.append(assignment)

            # Return previous assignments (except the last one)
            if i < len(users) - 1:
                assignment.returned_at = timezone.now() + timedelta(days=i + 1)
                assignment.save()

        # Verify assignment history
        self.assertEqual(asset.assignments.count(), 3)
        self.assertEqual(asset.assignments.filter(returned_at__isnull=True).count(), 1)
        self.assertEqual(asset.current_assignment, assignments[-1])

        # Verify each user has one assignment
        for user_profile in users:
            self.assertEqual(user_profile.asset_assignments.count(), 1)

    def test_asset_validation_constraints(self):
        """Test model validation and constraints"""
        # Test minimum purchase price validation
        with self.assertRaises(ValidationError):
            asset = Asset(
                asset_id="INVALID001",
                name="Invalid Asset",
                purchase_date=date.today(),
                purchase_price=Decimal("0.00"),  # Should fail validation
            )
            asset.full_clean()

        # Test minimum replacement cost validation
        asset = Asset.objects.create(
            asset_id="VALID001", name="Valid Asset", purchase_date=date.today()
        )

        with self.assertRaises(ValidationError):
            replacement_log = ReplacementLog(
                asset=asset,
                reason="Test replacement",
                date=date.today(),
                cost=Decimal("0.00"),  # Should fail validation
            )
            replacement_log.full_clean()


if __name__ == "__main__":
    import unittest

    unittest.main()
