from django.conf import settings
from django.db import migrations, models


ASSET_MODULE = "Asset Management"


def _bitmap_to_int(raw_value):
    if not raw_value:
        return 0
    try:
        return int(str(raw_value), 2)
    except ValueError:
        try:
            return int(str(raw_value))
        except ValueError:
            return 0


def _get_or_create_permission(Permission, module_name, feature_action):
    permission = Permission.objects.filter(
        module_name=module_name,
        feature_action=feature_action,
    ).first()
    if permission is not None:
        return permission

    max_bit = Permission.objects.aggregate(models.Max("bit_position"))[
        "bit_position__max"
    ]
    return Permission.objects.create(
        module_name=module_name,
        feature_action=feature_action,
        bit_position=(max_bit or 0) + 1,
    )


def _grant_permission_to_all_profiles(Permission, UserProfile, feature_action):
    permission = _get_or_create_permission(
        Permission,
        ASSET_MODULE,
        feature_action,
    )
    bit_mask = 1 << permission.bit_position

    for profile in UserProfile.objects.all().only("id", "permissions"):
        bitmap = _bitmap_to_int(profile.permissions)
        updated = bitmap | bit_mask
        if updated != bitmap:
            profile.permissions = bin(updated)[2:]
            profile.save(update_fields=["permissions"])


def seed_asset_permissions(apps, schema_editor):
    Permission = apps.get_model("core", "Permission")
    UserProfile = apps.get_model("core", "UserProfile")

    for feature_action in (
        "view_own_assets",
        "process_asset_return",
        "initiate_asset_return",
    ):
        _grant_permission_to_all_profiles(Permission, UserProfile, feature_action)


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):
    replaces = [
        ("core", "0014_asset_category"),
        ("core", "0015_assignment_snapshots_state_sync"),
        ("core", "0016_grant_default_view_own_assets_permission"),
        ("core", "0017_grant_default_process_asset_return_permission"),
        ("core", "0018_assignment_return_workflow_two_step"),
        ("core", "0019_add_approve_asset_return_permission"),
        ("core", "0020_assignment_return_checklist"),
    ]

    dependencies = [
        ("core", "0033_merge_20260505_1543"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="category",
            field=models.CharField(
                choices=[
                    ("laptops", "Laptops"),
                    ("phones", "Phones"),
                    ("monitors", "Monitors"),
                    ("headphones", "Headphones"),
                    ("cameras", "Cameras"),
                    ("vehicles", "Vehicles"),
                    ("furniture", "Furniture"),
                    ("other", "Other"),
                ],
                default="other",
                help_text="Asset category",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="asset_id_snapshot",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="assignment",
            name="asset_name_snapshot",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_rejection_reason",
            field=models.TextField(
                blank=True,
                help_text="Reason provided when return request is rejected",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_request_status",
            field=models.CharField(
                choices=[
                    ("none", "None"),
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                ],
                default="none",
                help_text="Two-step return workflow status",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_requested_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When return was requested",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_requested_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Who requested asset return",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="return_requests_made",
                to="core.userprofile",
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_reviewed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When return request was reviewed",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_reviewed_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Who reviewed return request",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="return_requests_reviewed",
                to="core.userprofile",
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_description",
            field=models.TextField(
                blank=True,
                help_text="Description entered when requesting a return",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="return_checklist",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Structured checklist submitted as part of the return request",
            ),
        ),
        migrations.RunPython(seed_asset_permissions, noop_reverse),
    ]
