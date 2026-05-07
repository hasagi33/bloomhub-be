from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import LeaveRequest
from core.serializers import LeaveRequestListSerializer


class LeaveRequestSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="leave-user",
            email="leave-user@example.com",
            password="password123",
            first_name="Leave",
            last_name="Tester",
        )

    def test_leave_request_list_serializer_includes_reason(self):
        leave_request = LeaveRequest.objects.create(
            employee=self.user.profile,
            leave_type="vacation",
            start_date=date.today() + timedelta(days=7),
            end_date=date.today() + timedelta(days=9),
            reason="Family event",
        )

        data = LeaveRequestListSerializer(leave_request).data

        self.assertEqual(data["reason"], "Family event")
