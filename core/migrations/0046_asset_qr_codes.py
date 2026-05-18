from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_merge_20260513_0000"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="qr_code_payload",
            field=models.URLField(
                blank=True,
                help_text="Stable frontend asset URL encoded in the asset QR code",
                max_length=500,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="asset",
            name="qr_code_image",
            field=models.FileField(
                blank=True,
                help_text="Persisted PNG QR image for the asset",
            ),
        ),
    ]
