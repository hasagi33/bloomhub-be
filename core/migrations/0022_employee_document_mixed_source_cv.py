from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_ensure_technology_tag_schema"),
    ]

    operations = [
        migrations.AlterField(
            model_name="employeedocument",
            name="file",
            field=models.FileField(
                blank=True, null=True, upload_to="employee_documents/%Y/%m/%d/"
            ),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="canva_design_id",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="external_url",
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="file_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="file_size",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="is_current",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="mime_type",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="provider",
            field=models.CharField(
                choices=[
                    ("internal", "Internal"),
                    ("canva", "Canva"),
                    ("other", "Other"),
                ],
                default="internal",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="employeedocument",
            name="source_type",
            field=models.CharField(
                choices=[("file", "File"), ("external_link", "External Link")],
                default="file",
                max_length=20,
            ),
        ),
    ]
