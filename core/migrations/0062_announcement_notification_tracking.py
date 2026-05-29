# Generated manually for announcement notification tracking.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0061_userprofile_intro_announcement"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="notifications_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="announcement",
            name="notifications_sent_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="announcement",
            name="email_notifications_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="announcement",
            name="email_notifications_sent_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
