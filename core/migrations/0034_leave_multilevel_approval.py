"""
Migration: multi-level leave approval workflow
- Adds LEAD_APPROVED to LeaveRequest.status choices
- Adds lead_approver FK, lead_approved_date, lead_approval_comments fields
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_merge_20260505_1543"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leaverequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("lead_approved", "Lead Approved"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="leaverequest",
            name="lead_approver",
            field=models.ForeignKey(
                blank=True,
                help_text="Tech Lead who gave first-level approval",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="lead_approved_leaves",
                to="core.userprofile",
            ),
        ),
        migrations.AddField(
            model_name="leaverequest",
            name="lead_approved_date",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="leaverequest",
            name="lead_approval_comments",
            field=models.TextField(blank=True, help_text="Comments from Tech Lead"),
        ),
    ]
