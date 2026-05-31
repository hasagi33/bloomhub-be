from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.ai.graph import _strip_markdown_table_separator_rows
from core.models import (
    AIChatMessage,
    AIChatSession,
    AIToolCallLog,
    DocumentTemplate,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    SalaryRecord,
)


class AIChatAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="chat-user",
            email="chat-user@example.com",
            password="password123",
            first_name="Chat",
            last_name="User",
        )
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        LeaveBalance.objects.update_or_create(
            employee=self.user.profile,
            leave_type="vacation",
            year=date.today().year,
            defaults={"allocated": 20, "used": 5, "carryover": 0},
        )
        LeavePolicy.objects.update_or_create(
            leave_type="vacation",
            defaults={
                "allocated_days_per_year": 20,
                "carryover_days": 0,
                "requires_approval": True,
                "min_notice_in_days": 0,
            },
        )

    def test_markdown_table_separator_rows_are_removed_for_custom_renderer(self):
        text = "\n".join(
            [
                "| # | Name |",
                "|:-:|:-----|",
                "| 1 | ugovor |",
            ]
        )

        result = _strip_markdown_table_separator_rows(text)

        self.assertNotIn("|:-:|:-----|", result)
        self.assertIn("| # | Name |", result)
        self.assertIn("| 1 | ugovor |", result)

    def test_chat_can_call_explicit_read_tool(self):
        response = self.client.post(
            "/api/ai/chat/",
            {
                "message": "Show my leave balance",
                "tool_name": "list_leave_balances",
                "arguments": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "list_leave_balances")
        self.assertIn(
            "vacation",
            {item["leave_type"] for item in body["result"]["balances"]},
        )
        self.assertEqual(AIChatSession.objects.count(), 1)
        self.assertEqual(AIChatMessage.objects.count(), 2)
        self.assertEqual(AIToolCallLog.objects.count(), 1)

    def test_document_template_tool_returns_clickable_entities(self):
        template = DocumentTemplate.objects.create(
            name="Shared Offer Letter",
            description="Offer template",
            category="contract",
            content="Hello {{name}}",
            visibility="shared",
            status="published",
            created_by=self.user.profile,
        )

        response = self.client.post(
            "/api/ai/chat/",
            {
                "message": "list document templates",
                "tool_name": "list_document_templates",
                "arguments": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["entities"],
            [
                {
                    "type": "document_template",
                    "id": template.id,
                    "name": "Shared Offer Letter",
                    "url": f"/documents/templates/{template.id}",
                }
            ],
        )
        self.assertEqual(body["entity_spans"][0]["type"], "document_template")

    def test_mutating_tool_requires_confirmation(self):
        response = self.client.post(
            "/api/ai/chat/",
            {"tool_name": "mark_all_notifications_read", "arguments": {}},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["requires_confirmation"])
        self.assertEqual(
            body["pending_confirmation"]["tool_name"],
            "mark_all_notifications_read",
        )
        self.assertEqual(body["ui_action_type"], "confirmation")
        self.assertEqual(body["ui_action"]["type"], "confirmation")
        self.assertEqual(
            AIToolCallLog.objects.first().status,
            AIToolCallLog.Status.PENDING_CONFIRMATION,
        )

    def test_create_leave_request_confirmation_has_form_ui_action(self):
        start = date.today() + timedelta(days=365)

        response = self.client.post(
            "/api/ai/chat/",
            {
                "tool_name": "create_leave_request",
                "arguments": {
                    "leave_type": "vacation",
                    "start_date": start.isoformat(),
                    "end_date": start.isoformat(),
                    "reason": "Family trip",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["requires_confirmation"])
        self.assertEqual(body["ui_action_type"], "form")
        self.assertEqual(body["ui_action"]["type"], "form")
        self.assertEqual(body["ui_action"]["tool_name"], "create_leave_request")

    def test_approve_leave_request_confirmation_has_approval_ui_action(self):
        leave_request = LeaveRequest.objects.create(
            employee=self.user.profile,
            leave_type="vacation",
            start_date=date.today() + timedelta(days=365),
            end_date=date.today() + timedelta(days=365),
            reason="Family trip",
        )

        response = self.client.post(
            "/api/ai/chat/",
            {
                "tool_name": "approve_leave_request",
                "arguments": {
                    "leave_request_id": leave_request.id,
                    "comments": "Looks good.",
                    "hr_final": False,
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["requires_confirmation"])
        self.assertEqual(body["ui_action_type"], "approval")
        self.assertEqual(body["ui_action"]["type"], "approval")
        self.assertEqual(body["ui_action"]["tool_name"], "approve_leave_request")

    def test_natural_language_yes_confirms_pending_even_with_stale_tool_hint(self):
        session = AIChatSession.objects.create(user=self.user)
        start = date.today() + timedelta(days=365)
        end = start

        first = self.client.post(
            "/api/ai/chat/",
            {
                "session_id": session.id,
                "tool_name": "create_leave_request",
                "arguments": {
                    "leave_type": "vacation",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "reason": "Family trip",
                },
            },
            format="json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["requires_confirmation"])

        second = self.client.post(
            "/api/ai/chat/",
            {
                "session_id": session.id,
                "message": "yes, create the request",
                "tool_name": "create_leave_request",
            },
            format="json",
        )

        self.assertEqual(second.status_code, 200)
        body = second.json()
        self.assertEqual(body["tool_name"], "create_leave_request")
        self.assertEqual(body["result"]["summary"], "Created leave request.")
        self.assertFalse(body["requires_confirmation"])
        self.assertEqual(body["pending_confirmation"], {})
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_session_list_detail_and_delete(self):
        session = AIChatSession.objects.create(user=self.user, title="Test")
        AIChatMessage.objects.create(
            session=session,
            role=AIChatMessage.Role.USER,
            content="Hello",
        )

        list_response = self.client.get("/api/ai/chat/sessions/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()[0]["message_count"], 1)

        detail_response = self.client.get(f"/api/ai/chat/sessions/{session.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["messages"][0]["content"], "Hello")

        delete_response = self.client.delete(f"/api/ai/chat/sessions/{session.id}/")
        self.assertEqual(delete_response.status_code, 204)
        session.refresh_from_db()
        self.assertTrue(session.is_archived)

    def test_salary_prompt_routes_to_compensation_tool(self):
        other = User.objects.create_user(
            username="salary-user",
            email="salary@example.com",
            password="password123",
            first_name="Salary",
            last_name="User",
        )
        SalaryRecord.objects.create(
            user_profile=other.profile,
            amount="3500.00",
            effective_date=date.today(),
        )

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Izlistaj mi 5 ljudi sa najvecom platom"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "list_top_paid_employees")
        self.assertEqual(body["module"], "mobility_compensation")
        self.assertEqual(body["result"]["employees"][0]["email"], "salary@example.com")

    def test_manager_prompt_routes_to_manager_tool(self):
        employee = User.objects.create_user(
            username="asmin",
            email="asmin@example.com",
            password="password123",
            first_name="Asmin",
            last_name="Basic",
        )
        employee.profile.full_name = "Asmin Basic"
        employee.profile.save(update_fields=["full_name"])
        employee.profile.managers.add(self.user.profile)

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Who is Asmin Basic's manager?"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "get_employee_managers")
        self.assertEqual(body["module"], "employees")
        self.assertEqual(
            body["result"]["employees"][0]["managers"][0]["email"],
            "chat-user@example.com",
        )

    def test_openrouter_path_uses_orchestrator_then_subagent(self):
        fake_llm = Mock()
        fake_llm.invoke.return_value.content = (
            '{"module":"mobility_compensation","prompt":"Find top paid employees."}'
        )
        fake_agent = Mock()

        with (
            patch("core.ai.graph.get_llm", return_value=fake_llm),
            patch(
                "core.ai.graph.create_react_subagent", return_value=fake_agent
            ) as create_agent,
            patch(
                "core.ai.graph.invoke_subagent",
                return_value="Top paid employees listed.",
            ) as invoke_agent,
        ):
            response = self.client.post(
                "/api/ai/chat/",
                {"message": "Izlistaj mi 5 ljudi sa najvecom platom"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["tool_name"])
        self.assertEqual(body["module"], "mobility_compensation")
        self.assertEqual(body["message"], "Top paid employees listed.")
        create_agent.assert_called_once()
        invoke_agent.assert_called_once()
        args, kwargs = invoke_agent.call_args
        self.assertEqual(args[0], fake_agent)
        # Original user message is now passed as the final prompt; the
        # orchestrator's rewrite is forwarded as a planner_hint.
        self.assertEqual(args[1], "Izlistaj mi 5 ljudi sa najvecom platom")
        self.assertIn("history", kwargs)
        self.assertEqual(kwargs.get("planner_hint"), "Find top paid employees.")
