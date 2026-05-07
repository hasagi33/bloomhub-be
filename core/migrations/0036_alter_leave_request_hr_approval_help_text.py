import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0035_merge_asset_return_and_leave_approval"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leaverequest",
            name="approval_comments",
            field=models.TextField(blank=True, help_text="Comments from HR approver"),
        ),
        migrations.AlterField(
            model_name="leaverequest",
            name="approver",
            field=models.ForeignKey(
                blank=True,
                help_text="HR who gave final approval/rejection",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_leaves",
                to="core.userprofile",
            ),
        ),
    ]
