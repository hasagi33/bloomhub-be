from django.db import migrations, models


ASSET_MODULE = "Asset Management"
REPLACEMENT_PERMISSION = "log_asset_replacement"
ROLE_NAMES = ("HR", "HR-MGR", "ADMIN")


def seed_log_asset_replacement_permission(apps, schema_editor):
    Permission = apps.get_model("core", "Permission")
    Role = apps.get_model("core", "Role")

    permission = Permission.objects.filter(
        module_name=ASSET_MODULE,
        feature_action=REPLACEMENT_PERMISSION,
    ).first()
    if permission is None:
        max_bit = Permission.objects.aggregate(models.Max("bit_position"))[
            "bit_position__max"
        ]
        permission = Permission.objects.create(
            module_name=ASSET_MODULE,
            feature_action=REPLACEMENT_PERMISSION,
            bit_position=(max_bit or 0) + 1,
        )

    for role in Role.objects.filter(name__in=ROLE_NAMES):
        role.permissions.add(permission)


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_alter_replacementlog_date"),
    ]

    operations = [
        migrations.RunPython(
            seed_log_asset_replacement_permission,
            reverse_code=noop_reverse,
        ),
    ]
