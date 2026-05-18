from django.db import migrations, models

TRAINING_MODULE = "Training"
PERMISSIONS = [
    "track_own_budget",
    "track_team_budget",
    "track_dept_budget",
    "configure_budget",
]
ROLE_GRANTS = {
    "configure_budget": ("HR", "HR-MGR", "ADMIN"),
    "track_own_budget": ("EMP", "MGR", "HR", "HR-MGR", "ADMIN"),
    "track_team_budget": ("MGR", "HR", "HR-MGR", "ADMIN"),
    "track_dept_budget": ("HR", "HR-MGR", "ADMIN"),
}


def seed(apps, schema_editor):
    Permission = apps.get_model("core", "Permission")
    Role = apps.get_model("core", "Role")

    perm_objs = {}
    for action in PERMISSIONS:
        perm = Permission.objects.filter(
            module_name=TRAINING_MODULE, feature_action=action
        ).first()
        if perm is None:
            max_bit = Permission.objects.aggregate(models.Max("bit_position"))[
                "bit_position__max"
            ]
            perm = Permission.objects.create(
                module_name=TRAINING_MODULE,
                feature_action=action,
                bit_position=(max_bit or 0) + 1,
            )
        perm_objs[action] = perm

    for action, role_names in ROLE_GRANTS.items():
        perm = perm_objs[action]
        for role in Role.objects.filter(name__in=role_names):
            role.permissions.add(perm)


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0046_training_budget_threshold_notification"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=noop_reverse),
    ]
