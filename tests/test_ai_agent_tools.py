from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from core.ai.tools import (
    approve_leave_request,
    classify_confirmation_response,
    create_leave_request,
    get_employee_managers,
    get_employee_profile,
    list_document_templates,
    list_documents,
    list_leave_balances,
    list_top_paid_employees,
    mark_all_notifications_read,
)
from core.models import (
    CompensationPolicy,
    Document,
    DocumentTemplate,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    Notification,
    SalaryRecord,
)


class AIAgentToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ai-user",
            email="ai-user@example.com",
            password="password123",
            first_name="AI",
            last_name="User",
        )
        self.other = User.objects.create_user(
            username="other-user",
            email="other-user@example.com",
            password="password123",
            first_name="Other",
            last_name="User",
        )
        self.hr_user = User.objects.create_user(
            username="hr-user",
            email="hr@example.com",
            password="password123",
            first_name="HR",
            last_name="User",
            is_staff=True,
        )
        LeavePolicy.objects.update_or_create(
            leave_type="vacation",
            defaults={
                "allocated_days_per_year": 25,
                "carryover_days": 0,
                "requires_approval": True,
                "min_notice_in_days": 0,
            },
        )
        LeaveBalance.objects.update_or_create(
            employee=self.user.profile,
            leave_type="vacation",
            year=date.today().year,
            defaults={"allocated": 25, "used": 0, "carryover": 0},
        )

    def test_list_leave_balances_returns_own_balance(self):
        result = list_leave_balances(user=self.user)

        self.assertIn("vacation", {item["leave_type"] for item in result["balances"]})
        self.assertIn("Leave balances:", result["summary"])
        self.assertIn(
            "| Leave Type | Remaining Days | Used | Allocated |", result["summary"]
        )
        self.assertIn("Vacation", result["summary"])

    def test_create_leave_request_uses_existing_serializer_validation(self):
        result = create_leave_request(
            user=self.user,
            leave_type="vacation",
            start_date=(date.today() + timedelta(days=3)).isoformat(),
            end_date=(date.today() + timedelta(days=4)).isoformat(),
            reason="Family trip",
        )

        self.assertEqual(result["leave_request"]["reason"], "Family trip")
        self.assertEqual(result["summary"], "Created leave request.")

    def test_create_leave_request_normalizes_display_label_and_blank_reason(self):
        start = date.today() + timedelta(days=30)
        result = create_leave_request(
            user=self.user,
            leave_type="Vacation",
            start_date=start.strftime("%d.%m.%Y"),
            end_date=start.strftime("%d.%m.%Y"),
            reason="",
        )

        self.assertEqual(result["leave_request"]["leave_type"], "vacation")
        self.assertEqual(result["leave_request"]["start_date"], start.isoformat())
        self.assertEqual(result["leave_request"]["reason"], "Not specified")

    def test_superuser_can_approve_leave_as_lead_then_hr(self):
        self.hr_user.is_superuser = True
        self.hr_user.save(update_fields=["is_superuser"])
        request = LeaveRequest.objects.create(
            employee=self.user.profile,
            leave_type="vacation",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=30),
            reason="Family trip",
        )

        lead_result = approve_leave_request(
            user=self.hr_user,
            leave_request_id=request.id,
            comments="Lead approved.",
        )
        hr_result = approve_leave_request(
            user=self.hr_user,
            leave_request_id=request.id,
            comments="HR approved.",
            hr_final=True,
        )

        self.assertEqual(lead_result["leave_request"]["status"], "lead_approved")
        self.assertEqual(hr_result["leave_request"]["status"], "approved")

    def test_classify_confirmation_response_detects_positive_and_negative(self):
        positive = classify_confirmation_response(
            user=self.user,
            response="yes, create the request",
        )
        negative = classify_confirmation_response(
            user=self.user,
            response="no, cancel it",
        )

        self.assertEqual(positive["sentiment"], "positive")
        self.assertTrue(positive["is_positive"])
        self.assertEqual(negative["sentiment"], "negative")
        self.assertTrue(negative["is_negative"])

    def test_non_hr_cannot_read_other_employee_profile(self):
        with self.assertRaises(PermissionDenied):
            get_employee_profile(user=self.user, employee_id=self.other.profile.id)

    def test_mark_all_notifications_read_only_updates_current_user(self):
        Notification.objects.create(
            recipient=self.user.profile,
            title="Mine",
            message="Unread",
        )
        Notification.objects.create(
            recipient=self.other.profile,
            title="Other",
            message="Unread",
        )

        result = mark_all_notifications_read(user=self.user)

        self.assertEqual(result["updated"], 1)
        self.assertFalse(Notification.objects.get(recipient=self.other.profile).is_read)

    def test_list_top_paid_employees_is_hr_only_and_orders_by_salary(self):
        SalaryRecord.objects.create(
            user_profile=self.user.profile,
            amount="1000.00",
            effective_date=date.today(),
        )
        SalaryRecord.objects.create(
            user_profile=self.other.profile,
            amount="2500.00",
            effective_date=date.today(),
        )

        with self.assertRaises(PermissionDenied):
            list_top_paid_employees(user=self.user, limit=5)

        result = list_top_paid_employees(user=self.hr_user, limit=1)

        self.assertEqual(result["employees"][0]["email"], "other-user@example.com")
        self.assertEqual(result["employees"][0]["current_salary"], "2500")

    def test_list_top_paid_employees_uses_compensation_policy_without_salary_record(
        self,
    ):
        self.user.profile.cpf_level = "L1"
        self.user.profile.save(update_fields=["cpf_level"])
        self.other.profile.cpf_level = "L2"
        self.other.profile.save(update_fields=["cpf_level"])
        CompensationPolicy.objects.create(
            cpf_level="L1",
            net_monthly="1000.00",
            effective_date=date.today(),
        )
        CompensationPolicy.objects.create(
            cpf_level="L2",
            net_monthly="3000.00",
            effective_date=date.today(),
        )

        result = list_top_paid_employees(user=self.hr_user, limit=1)

        self.assertEqual(result["employees"][0]["email"], "other-user@example.com")
        self.assertEqual(result["employees"][0]["salary_source"], "compensation_policy")

    def test_get_employee_managers_returns_assigned_manager(self):
        self.other.profile.managers.add(self.user.profile)

        result = get_employee_managers(user=self.hr_user, query="Other")

        self.assertEqual(
            result["employees"][0]["managers"][0]["email"], "ai-user@example.com"
        )
        self.assertIn("Managers:", result["summary"])

    def test_list_documents_filters_by_name_field(self):
        Document.objects.create(
            uploaded_by=self.user.profile,
            category=Document.Category.POLICIES,
            file_key="documents/handbook.pdf",
            name="Employee Handbook",
            description="General policy",
            original_filename="handbook.pdf",
            file_size=123,
            mime_type="application/pdf",
        )

        result = list_documents(user=self.user, query="Handbook")

        self.assertEqual(result["documents"][0]["name"], "Employee Handbook")

    def test_list_documents_filters_expired_documents(self):
        Document.objects.create(
            uploaded_by=self.user.profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/expired.pdf",
            name="Expired Contract",
            description="Old contract",
            original_filename="expired.pdf",
            file_size=123,
            mime_type="application/pdf",
            expiry_date=date.today() - timedelta(days=1),
        )
        Document.objects.create(
            uploaded_by=self.user.profile,
            category=Document.Category.CONTRACTS,
            file_key="documents/future.pdf",
            name="Future Contract",
            description="Future contract",
            original_filename="future.pdf",
            file_size=123,
            mime_type="application/pdf",
            expiry_date=date.today().replace(year=date.today().year + 1),
        )

        result = list_documents(user=self.user, expired=True)

        names = {item["name"] for item in result["documents"]}
        self.assertIn("Expired Contract", names)
        self.assertNotIn("Future Contract", names)
        self.assertIn("Expired documents:", result["summary"])

    def test_list_document_templates_returns_visible_templates(self):
        DocumentTemplate.objects.create(
            name="Shared Offer Letter",
            description="Offer template",
            category="contract",
            content="Hello {{name}}",
            visibility="shared",
            status="published",
            created_by=self.other.profile,
        )
        DocumentTemplate.objects.create(
            name="My Private Template",
            description="Private",
            category="other",
            content="Private",
            visibility="private",
            status="draft",
            created_by=self.user.profile,
        )
        DocumentTemplate.objects.create(
            name="Hidden Private Template",
            description="Hidden",
            category="other",
            content="Hidden",
            visibility="private",
            status="draft",
            created_by=self.other.profile,
        )

        result = list_document_templates(user=self.user, limit=20)

        names = {item["name"] for item in result["document_templates"]}
        self.assertIn("Shared Offer Letter", names)
        self.assertIn("My Private Template", names)
        self.assertNotIn("Hidden Private Template", names)
        self.assertIn("Document templates:", result["summary"])
