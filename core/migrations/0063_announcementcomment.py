# Generated manually for announcement comments.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0062_announcement_notification_tracking"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnnouncementComment",
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
                ("body", models.TextField()),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "announcement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comments",
                        to="core.announcement",
                    ),
                ),
                (
                    "author",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="announcement_comments",
                        to="core.userprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Announcement Comment",
                "verbose_name_plural": "Announcement Comments",
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(
                        fields=["announcement", "created_at"],
                        name="core_announ_announc_6236f6_idx",
                    ),
                    models.Index(
                        fields=["author", "created_at"],
                        name="core_announ_author__7e5aae_idx",
                    ),
                ],
            },
        ),
    ]
