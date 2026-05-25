from django.db import migrations

# Per-role CPF (Career Progression Framework) ladders: role name -> (level
# name prefix, number of levels). Each role gets an ordered ladder, e.g.
# a manager gets M1..M4 with order 1..4.
CPF_LADDERS = {
    "employee": ("E", 5),
    "manager": ("M", 4),
    "hr_admin": ("HR", 3),
    "super_admin": ("SA", 2),
    "EMP": ("EMP", 5),
}
DEFAULT_LEVEL_COUNT = 5


def _ladder_for(role_name):
    if role_name in CPF_LADDERS:
        return CPF_LADDERS[role_name]
    return (role_name[:3].upper(), DEFAULT_LEVEL_COUNT)


def seed_cpf_levels(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    CPFLevel = apps.get_model("core", "CPFLevel")
    for role in Role.objects.all():
        prefix, count = _ladder_for(role.name)
        for rank in range(1, count + 1):
            CPFLevel.objects.get_or_create(
                name=f"{prefix}{rank}",
                defaults={"role": role, "order": rank},
            )


def unseed_cpf_levels(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    CPFLevel = apps.get_model("core", "CPFLevel")
    for role in Role.objects.all():
        prefix, count = _ladder_for(role.name)
        names = [f"{prefix}{rank}" for rank in range(1, count + 1)]
        CPFLevel.objects.filter(name__in=names, role=role).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0052_cpflevel_order"),
    ]

    operations = [
        migrations.RunPython(seed_cpf_levels, unseed_cpf_levels),
    ]
