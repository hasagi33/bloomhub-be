from django.db import migrations


PALETTE = {
    "Engineering": ("#4f46e5", "#eef2ff"),
    "Design": ("#ea580c", "#fff7ed"),
    "Product": ("#7c3aed", "#f5f3ff"),
    "People": ("#16a34a", "#f0fdf4"),
    "Sales": ("#e11d48", "#fff1f2"),
    "Marketing": ("#d97706", "#fffbeb"),
    "Customer Success": ("#0891b2", "#ecfeff"),
    "Operations": ("#475569", "#f1f5f9"),
    "Finance": ("#059669", "#ecfdf5"),
    "Executive": ("#171717", "#f3f4f6"),
}


def seed_palette(apps, schema_editor):
    Department = apps.get_model("core", "Department")
    for dept in Department.objects.all():
        colors = PALETTE.get(dept.name)
        if colors:
            dept.color, dept.color_soft = colors
            dept.save(update_fields=["color", "color_soft"])


def backfill_department_fk(apps, schema_editor):
    UserProfile = apps.get_model("core", "UserProfile")
    Department = apps.get_model("core", "Department")
    by_name = {d.name: d for d in Department.objects.all()}
    for up in UserProfile.objects.exclude(department__isnull=True).exclude(
        department=""
    ):
        dept = by_name.get(up.department)
        if dept:
            up.department_fk_id = dept.id
            up.save(update_fields=["department_fk"])


def backfill_primary_manager(apps, schema_editor):
    UserProfile = apps.get_model("core", "UserProfile")
    for up in UserProfile.objects.all():
        mgr = up.managers.order_by("id").first()
        if mgr:
            up.primary_manager_id = mgr.id
            up.save(update_fields=["primary_manager"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0051_org_chart_fields"),
    ]

    operations = [
        migrations.RunPython(seed_palette, noop),
        migrations.RunPython(backfill_department_fk, noop),
        migrations.RunPython(backfill_primary_manager, noop),
    ]
