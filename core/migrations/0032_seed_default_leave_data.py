from django.db import migrations
from django.utils import timezone


DEFAULT_LEAVE_POLICIES = [
    {
        "leave_type": "vacation",
        "allocated_days_per_year": 25,
        "carryover_days": 5,
        "requires_approval": True,
        "requires_covering_employee": False,
        "min_notice_in_days": 7,
        "max_consecutive_days": 20,
    },
    {
        "leave_type": "sick",
        "allocated_days_per_year": 10,
        "carryover_days": 0,
        "requires_approval": False,
        "requires_covering_employee": False,
        "min_notice_in_days": 0,
        "max_consecutive_days": None,
    },
    {
        "leave_type": "wfh",
        "allocated_days_per_year": 52,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": False,
        "min_notice_in_days": 1,
        "max_consecutive_days": 5,
    },
    {
        "leave_type": "personal",
        "allocated_days_per_year": 3,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": False,
        "min_notice_in_days": 3,
        "max_consecutive_days": 3,
    },
    {
        "leave_type": "maternity",
        "allocated_days_per_year": 120,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": True,
        "min_notice_in_days": 30,
        "max_consecutive_days": None,
    },
    {
        "leave_type": "paternity",
        "allocated_days_per_year": 10,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": True,
        "min_notice_in_days": 7,
        "max_consecutive_days": None,
    },
    {
        "leave_type": "bereavement",
        "allocated_days_per_year": 5,
        "carryover_days": 0,
        "requires_approval": False,
        "requires_covering_employee": False,
        "min_notice_in_days": 0,
        "max_consecutive_days": 5,
    },
    {
        "leave_type": "unpaid",
        "allocated_days_per_year": 365,
        "carryover_days": 0,
        "requires_approval": True,
        "requires_covering_employee": True,
        "min_notice_in_days": 14,
        "max_consecutive_days": None,
    },
]


def seed_default_leave_data(apps, schema_editor):
    LeavePolicy = apps.get_model("core", "LeavePolicy")
    LeaveBalance = apps.get_model("core", "LeaveBalance")
    UserProfile = apps.get_model("core", "UserProfile")

    for policy_data in DEFAULT_LEAVE_POLICIES:
        LeavePolicy.objects.get_or_create(
            leave_type=policy_data["leave_type"],
            defaults=policy_data,
        )

    current_year = timezone.now().year
    for employee in UserProfile.objects.all():
        for policy in LeavePolicy.objects.all():
            LeaveBalance.objects.get_or_create(
                employee=employee,
                leave_type=policy.leave_type,
                year=current_year,
                defaults={
                    "allocated": policy.allocated_days_per_year,
                    "used": 0,
                    "carryover": 0,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0031_alter_documenttemplate_category_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_default_leave_data, migrations.RunPython.noop),
    ]
