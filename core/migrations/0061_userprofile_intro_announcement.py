# Generated manually for employee introduction announcements.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0060_announcement_scheduled_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="intro_announcement",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="introduced_profiles",
                to="core.announcement",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="intro_announcement_published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
