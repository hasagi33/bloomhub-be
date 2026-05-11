from django.db import migrations, models


def backfill_allowed_roles(apps, schema_editor):
    Document = apps.get_model("core", "Document")
    DocumentCategoryDefault = apps.get_model("core", "DocumentCategoryDefault")

    confidential_qs = Document.objects.filter(is_confidential=True)
    for doc in confidential_qs:
        if not doc.allowed_roles:
            doc.allowed_roles = ["hr", "admin"]
            doc.save(update_fields=["allowed_roles"])

    public_qs = Document.objects.filter(is_confidential=False)
    for doc in public_qs:
        if not doc.allowed_roles:
            doc.allowed_roles = ["employee"]
            doc.save(update_fields=["allowed_roles"])

    seeds = {
        "contracts": ["hr"],
        "compliance": ["hr"],
        "agreements": ["hr"],
        "policies": ["employee"],
        "onboarding": ["employee"],
        "training": ["employee"],
        "benefits": ["employee"],
        "other": ["employee"],
    }
    for category, roles in seeds.items():
        DocumentCategoryDefault.objects.update_or_create(
            category=category, defaults={"allowed_roles": roles}
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_merge_20260507_1304"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentCategoryDefault",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("contracts", "Contracts"),
                            ("policies", "Policies"),
                            ("agreements", "Agreements"),
                            ("compliance", "Compliance"),
                            ("onboarding", "Onboarding"),
                            ("training", "Training"),
                            ("benefits", "Benefits"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                        unique=True,
                    ),
                ),
                ("allowed_roles", models.JSONField(blank=True, default=list)),
            ],
            options={
                "verbose_name": "Document Category Default",
                "verbose_name_plural": "Document Category Defaults",
                "ordering": ["category"],
            },
        ),
        migrations.RunPython(backfill_allowed_roles, noop_reverse),
    ]
