"""
Seed leave management data for testing.

Creates:
- Leave policies for all 8 leave types
- Leave balances for existing employees
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def get_leave_models():
    from core.models import (
        LeavePolicy,
        UserProfile,
        ensure_default_leave_policies,
        initialize_leave_balances_for_profile,
    )

    return (
        LeavePolicy,
        UserProfile,
        ensure_default_leave_policies,
        initialize_leave_balances_for_profile,
    )


def create_leave_policies():
    """Create default leave policies for all leave types."""
    LeavePolicy, _, ensure_default_leave_policies, _ = get_leave_models()

    print("Creating leave policies...")
    before_count = LeavePolicy.objects.count()
    created_count = ensure_default_leave_policies()

    for policy in LeavePolicy.objects.all():
        status = "Created" if before_count == 0 else "Available"
        print(f"  - {status}: {policy.get_leave_type_display()}")

    print(f"\nCreated {created_count} new policies")
    return created_count


def create_leave_balances():
    """Create leave balances for all employees for current year."""
    LeavePolicy, UserProfile, _, initialize_leave_balances_for_profile = (
        get_leave_models()
    )

    print("\nCreating leave balances for employees...")

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

        employee_created_count = initialize_leave_balances_for_profile(employee)
        created_count += employee_created_count
        print(f"    Created {employee_created_count} balances")

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
