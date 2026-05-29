# Generated manually for announcement automation settings.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0064_discord_announcement_delivery"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnnouncementSettings",
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
                    "auto_employee_intro_on_registration",
                    models.BooleanField(default=True),
                ),
                (
                    "auto_employee_intro_on_employee_create",
                    models.BooleanField(default=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Announcement Settings",
                "verbose_name_plural": "Announcement Settings",
            },
        ),
    ]
