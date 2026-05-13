from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_replacementlog_asset_state_snapshots"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScheduledMaintenance",
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
                ("due_date", models.DateField(help_text="Date when maintenance is due")),
                ("reason", models.TextField(help_text="Reason maintenance is needed")),
                (
                    "maintenance_type",
                    models.CharField(
                        choices=[
                            ("preventive", "Preventive"),
                            ("repair", "Repair"),
                            ("inspection", "Inspection"),
                            ("warranty", "Warranty"),
                            ("replacement", "Replacement"),
                            ("other", "Other"),
                        ],
                        help_text="Type of scheduled maintenance",
                        max_length=20,
                    ),
                ),
                (
                    "estimated_cost",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Estimated cost of the scheduled maintenance",
                        max_digits=10,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.01"))
                        ],
                    ),
                ),
                (
                    "vendor",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Optional external vendor or service provider",
                        max_length=200,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("scheduled", "Scheduled"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="scheduled",
                        help_text="Scheduled maintenance lifecycle status",
                        max_length=20,
                    ),
                ),
                (
                    "cancelled_reason",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Optional reason the scheduled maintenance was cancelled",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "asset",
                    models.ForeignKey(
                        help_text="Asset that needs maintenance",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scheduled_maintenance",
                        to="core.asset",
                    ),
                ),
                (
                    "completed_log",
                    models.OneToOneField(
                        blank=True,
                        help_text=(
                            "Historical maintenance log created when this schedule "
                            "is completed"
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="scheduled_maintenance",
                        to="core.replacementlog",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="User who created the scheduled maintenance",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_scheduled_maintenance",
                        to="core.userprofile",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        help_text="Optional person responsible for the maintenance",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="owned_scheduled_maintenance",
                        to="core.userprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Scheduled Maintenance",
                "verbose_name_plural": "Scheduled Maintenance",
                "ordering": ["due_date", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="scheduledmaintenance",
            index=models.Index(
                fields=["status", "due_date"],
                name="core_schedu_status_956166_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="scheduledmaintenance",
            index=models.Index(
                fields=["asset", "status"],
                name="core_schedu_asset_i_3b398e_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="scheduledmaintenance",
            index=models.Index(
                fields=["owner", "status"],
                name="core_schedu_owner_i_3f6fc6_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="scheduledmaintenance",
            index=models.Index(
                fields=["maintenance_type", "due_date"],
                name="core_schedu_mainten_06c14d_idx",
            ),
        ),
    ]
