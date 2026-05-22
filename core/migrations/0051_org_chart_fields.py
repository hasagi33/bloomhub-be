from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0050_merge_20260519_1344"),
    ]

    operations = [
        migrations.AddField(
            model_name="department",
            name="color",
            field=models.CharField(default="#475569", max_length=7),
        ),
        migrations.AddField(
            model_name="department",
            name="color_soft",
            field=models.CharField(default="#f1f5f9", max_length=7),
        ),
        migrations.AddField(
            model_name="department",
            name="head_employee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="headed_departments",
                to="core.userprofile",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="primary_manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="primary_direct_reports",
                to="core.userprofile",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="department_fk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="members",
                to="core.department",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="is_remote",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="userprofile",
            name="employment_status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("on_leave", "On Leave"),
                    ("inactive", "Inactive"),
                ],
                default="active",
                max_length=10,
            ),
        ),
    ]
