# Generated for BHB-000-cpf-levels

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0080_tempo_absence_sync"),
    ]

    operations = [
        migrations.AddField(
            model_name="cpflevel",
            name="display_name",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="cpflevel",
            name="career_level",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="employeeprofilechangehistory",
            name="field",
            field=models.CharField(
                choices=[
                    ("role", "Role"),
                    ("salary", "Salary"),
                    ("cpf_level", "CPF Level"),
                    ("department", "Department"),
                    ("manager_ids", "Manager IDs"),
                    ("employment_status", "Employment Status"),
                    ("career_level", "Career Level"),
                    ("start_date", "Start Date"),
                    ("employee_id", "Employee ID"),
                ],
                max_length=32,
            ),
        ),
    ]
