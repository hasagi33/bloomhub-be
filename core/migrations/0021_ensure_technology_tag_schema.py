from django.db import migrations


def ensure_technology_tag_schema(apps, schema_editor):
    """
    Repair migration for environments where migration state says tech tags exist,
    but physical DB tables are missing.
    """
    UserProfile = apps.get_model("core", "UserProfile")
    TechnologyTag = apps.get_model("core", "TechnologyTag")
    through_model = UserProfile.tech_tags.through

    existing_tables = set(schema_editor.connection.introspection.table_names())

    if TechnologyTag._meta.db_table not in existing_tables:
        schema_editor.create_model(TechnologyTag)
        existing_tables.add(TechnologyTag._meta.db_table)

    if through_model._meta.db_table not in existing_tables:
        schema_editor.create_model(through_model)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_leavepolicy_leaveadjustment_leaverequest_and_more"),
    ]

    operations = [
        migrations.RunPython(
            ensure_technology_tag_schema, reverse_code=migrations.RunPython.noop
        )
    ]
