"""
Seed leave management data for testing.

Creates:
- Leave policies for all 8 leave types
- Leave balances for existing employees
"""

import os
from datetime import datetime

import django

from core.models import LeaveBalance, LeavePolicy, UserProfile

# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def create_leave_policies():
    """Create default leave policies for all leave types."""
    print("Creating leave policies...")

    policies = [
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
            "allocated_days_per_year": 52,  # ~1 day per week
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
            "allocated_days_per_year": 120,  # ~4 months
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
            "allocated_days_per_year": 365,  # Unlimited (capped at year)
            "carryover_days": 0,
            "requires_approval": True,
            "requires_covering_employee": True,
            "min_notice_in_days": 14,
            "max_consecutive_days": None,
        },
    ]

    created_count = 0
    for policy_data in policies:
        policy, created = LeavePolicy.objects.get_or_create(
            leave_type=policy_data["leave_type"],
            defaults=policy_data,
        )
        if created:
            created_count += 1
            print(f"  ✓ Created policy for {policy_data['leave_type']}")
        else:
            print(f"  - Policy for {policy_data['leave_type']} already exists")

    print(f"\nCreated {created_count} new policies")
    return created_count


def create_leave_balances():
    """Create leave balances for all employees for current year."""
    print("\nCreating leave balances for employees...")

    current_year = datetime.now().year
    employees = UserProfile.objects.all()
    policies = LeavePolicy.objects.all()

    if not employees.exists():
        print("  ! No employees found. Create users first.")
        return 0

    if not policies.exists():
        print("  ! No policies found. Run create_leave_policies first.")
        return 0

    created_count = 0
    for employee in employees:
        print(
            f"\n  Employee: {employee.user.get_full_name() or employee.user.username}"
        )

        for policy in policies:
            balance, created = LeaveBalance.objects.get_or_create(
                employee=employee,
                leave_type=policy.leave_type,
                year=current_year,
                defaults={
                    "allocated": policy.allocated_days_per_year,
                    "used": 0,
                    "carryover": 0,
                },
            )

            if created:
                created_count += 1
                print(
                    f"    ✓ {policy.get_leave_type_display()}: {policy.allocated_days_per_year} days"
                )
            else:
                print(f"    - {policy.get_leave_type_display()}: already exists")

    print(f"\nCreated {created_count} new balances")
    return created_count


def main():
    """Run the seed script."""
    print("=" * 60)
    print("LEAVE MANAGEMENT SEED DATA")
    print("=" * 60)
    print()

    # Step 1: Create policies
    policies_created = create_leave_policies()

    # Step 2: Create balances for all employees
    balances_created = create_leave_balances()

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Policies created: {policies_created}")
    print(f"Balances created: {balances_created}")
    print()
    print("✅ Seed data complete!")
    print()
    print("You can now:")
    print("  1. Start the backend: python manage.py runserver")
    print("  2. Log in via API: POST /api/auth/login/")
    print("  3. Test leave endpoints: GET /api/leave-balances/")
    print()


if __name__ == "__main__":
    main()
