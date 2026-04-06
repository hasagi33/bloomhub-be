from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_merge_20260324_2027"),
    ]

    operations = [
        migrations.CreateModel(
            name="CPFLevel",
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
                ("name", models.CharField(max_length=100, unique=True)),
            ],
            options={
                "ordering": ["name"],
                "verbose_name": "CPF Level",
                "verbose_name_plural": "CPF Levels",
            },
        ),
    ]
