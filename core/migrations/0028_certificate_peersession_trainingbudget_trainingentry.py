import core.models
import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_documentcategory_document"),
    ]

    operations = [
        migrations.CreateModel(
            name="Certificate",
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
                    "title",
                    models.CharField(
                        help_text="Certificate title/name", max_length=255
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        help_text="Certificate file (PDF, image, etc.)",
                        upload_to=core.models.certificate_upload_to,
                    ),
                ),
                (
                    "issued_date",
                    models.DateField(help_text="Date when certificate was issued"),
                ),
                (
                    "expiration_date",
                    models.DateField(
                        blank=True,
                        help_text="Certificate expiration date (if applicable)",
                        null=True,
                    ),
                ),
                (
                    "issuer",
                    models.CharField(
                        blank=True,
                        help_text="Organization or body that issued the certificate",
                        max_length=255,
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "employee",
                    models.ForeignKey(
                        help_text="Employee who earned the certificate",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="certificates",
                        to="core.userprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Certificate",
                "verbose_name_plural": "Certificates",
                "ordering": ["-issued_date"],
                "indexes": [
                    models.Index(
                        fields=["employee", "-issued_date"],
                        name="core_certif_employe_c5b2e2_idx",
                    ),
                    models.Index(
                        fields=["expiration_date"],
                        name="core_certif_expirat_a1b910_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PeerSession",
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
                    "topic",
                    models.CharField(
                        help_text="Topic or skill shared in the session", max_length=255
                    ),
                ),
                (
                    "session_date",
                    models.DateField(help_text="Date when the peer session occurred"),
                ),
                (
                    "incentive_id",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Reference to associated incentive (FK when model exists)",
                        null=True,
                    ),
                ),
                (
                    "duration_minutes",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Duration of the session in minutes",
                        null=True,
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Additional details about the session",
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "employee",
                    models.ForeignKey(
                        help_text="Employee who participated in the peer session",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="peer_sessions",
                        to="core.userprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Peer Session",
                "verbose_name_plural": "Peer Sessions",
                "ordering": ["-session_date"],
                "indexes": [
                    models.Index(
                        fields=["employee", "-session_date"],
                        name="core_peerse_employe_b04452_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="TrainingBudget",
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
                    "fiscal_year",
                    models.PositiveIntegerField(
                        help_text="Fiscal year for which budget is allocated"
                    ),
                ),
                (
                    "allocated_budget",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Total budget allocated for training this fiscal year",
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.00"))
                        ],
                    ),
                ),
                (
                    "used_budget",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        help_text="Budget amount spent on training so far",
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.00"))
                        ],
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "employee",
                    models.ForeignKey(
                        help_text="Employee assigned the budget",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="training_budgets",
                        to="core.userprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Training Budget",
                "verbose_name_plural": "Training Budgets",
                "ordering": ["-fiscal_year", "employee"],
                "indexes": [
                    models.Index(
                        fields=["employee", "-fiscal_year"],
                        name="core_traini_employe_4bb5d4_idx",
                    )
                ],
                "unique_together": {("employee", "fiscal_year")},
            },
        ),
        migrations.CreateModel(
            name="TrainingEntry",
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
                    "course_title",
                    models.CharField(
                        help_text="Name or title of the training/course", max_length=255
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        help_text="Training provider or organization", max_length=255
                    ),
                ),
                (
                    "training_date",
                    models.DateField(help_text="Date when training occurred"),
                ),
                (
                    "completed_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When employee completed the training",
                        null=True,
                    ),
                ),
                (
                    "training_type",
                    models.CharField(
                        choices=[
                            ("course", "Course"),
                            ("conference", "Conference"),
                            ("workshop", "Workshop"),
                            ("webinar", "Webinar"),
                            ("certification", "Certification"),
                            ("other", "Other"),
                        ],
                        default="course",
                        help_text="Type of training activity",
                        max_length=20,
                    ),
                ),
                (
                    "cost",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Cost of training (for budget tracking)",
                        max_digits=10,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.00"))
                        ],
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Additional notes or description",
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "employee",
                    models.ForeignKey(
                        help_text="Employee who participated in training",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="training_entries",
                        to="core.userprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Training Entry",
                "verbose_name_plural": "Training Entries",
                "ordering": ["-training_date"],
                "indexes": [
                    models.Index(
                        fields=["employee", "-training_date"],
                        name="core_traini_employe_6ec7d4_idx",
                    ),
                    models.Index(
                        fields=["training_type"], name="core_traini_trainin_0bcd0d_idx"
                    ),
                ],
            },
        ),
    ]
