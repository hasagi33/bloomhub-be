from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_seed_log_asset_replacement_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="replacementlog",
            name="asset_status_before",
            field=models.CharField(
                blank=True,
                choices=[
                    ("active", "Active"),
                    ("lost", "Lost"),
                    ("returned", "Returned"),
                    ("damaged", "Damaged"),
                ],
                help_text="Asset status before the replacement or maintenance event",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="replacementlog",
            name="asset_status_after",
            field=models.CharField(
                blank=True,
                choices=[
                    ("active", "Active"),
                    ("lost", "Lost"),
                    ("returned", "Returned"),
                    ("damaged", "Damaged"),
                ],
                help_text="Asset status after the replacement or maintenance event",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="replacementlog",
            name="asset_condition_before",
            field=models.CharField(
                blank=True,
                choices=[
                    ("excellent", "Excellent"),
                    ("good", "Good"),
                    ("fair", "Fair"),
                    ("poor", "Poor"),
                    ("damaged", "Damaged"),
                ],
                help_text="Asset condition before the replacement or maintenance event",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="replacementlog",
            name="asset_condition_after",
            field=models.CharField(
                blank=True,
                choices=[
                    ("excellent", "Excellent"),
                    ("good", "Good"),
                    ("fair", "Fair"),
                    ("poor", "Poor"),
                    ("damaged", "Damaged"),
                ],
                help_text="Asset condition after the replacement or maintenance event",
                max_length=20,
                null=True,
            ),
        ),
    ]
