from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import LeaveBalance, LeavePolicy


class LeaveAutoSetupTests(TestCase):
    def test_new_user_gets_current_year_leave_balances(self):
        user = User.objects.create_user(
            username="leave-user",
            email="leave-user@example.com",
            password="password123",
        )

        policy_count = LeavePolicy.objects.count()
        balance_count = LeaveBalance.objects.filter(
            employee=user.profile,
            year=timezone.now().year,
        ).count()

        self.assertGreater(policy_count, 0)
        self.assertEqual(balance_count, policy_count)
