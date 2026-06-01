from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.ai.graph import (
    _strip_markdown_table_separator_rows,
    infer_tool,
    normalize_assistant_message,
)
from core.ai.tools import registry
from core.models import (
    AIChatMessage,
    AIChatSession,
    AIToolCallLog,
    Announcement,
    Asset,
    DocumentTemplate,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    Project,
    ProjectStage,
    ProjectStatus,
    ProjectType,
    SalaryRecord,
    TimeEntry,
)
from core.services.time_tracking_service import fingerprint_for_entry


class AIChatAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="chat-user",
            email="chat-user@example.com",
            password="password123",
            first_name="Chat",
            last_name="User",
        )
        cls.user.is_staff = True
        cls.user.save(update_fields=["is_staff"])
        LeaveBalance.objects.update_or_create(
            employee=cls.user.profile,
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

    def setUp(self):
        self.user = User.objects.get(pk=self.__class__.user.pk)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

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

    def test_plural_count_prompts_do_not_become_search_tail_filters(self):
        cases = {
            "how many employees do we have?": ("search_employees", "query"),
            "how many assets do we have?": ("list_assets", "query"),
            "how many documents do we have?": ("list_documents", "query"),
            "how many document templates do we have?": (
                "list_document_templates",
                "query",
            ),
        }

        for prompt, (tool_name, query_key) in cases.items():
            with self.subTest(prompt=prompt):
                selected_tool, args = infer_tool(prompt)
                self.assertEqual(selected_tool, tool_name)
                self.assertEqual(args[query_key], "")
                self.assertTrue(args["count_only"])

    def test_create_announcement_prompt_infers_mutating_tool(self):
        prompt = (
            'Create a new announcement of type General, title "Hello world", '
            'body "Lorem ipsum", and publish it now'
        )

        selected_tool, args = infer_tool(prompt)

        self.assertEqual(selected_tool, "create_announcement")
        self.assertEqual(args["title"], "Hello world")
        self.assertEqual(args["body"], "Lorem ipsum")
        self.assertEqual(args["type"], "General")
        self.assertIsNone(args["scheduled_at"])

    def test_create_asset_prompt_infers_mutating_tool(self):
        prompt = (
            "Create a new asset name Macatop, id MCTP-1, category laptop, "
            "condition Good, status active."
        )

        selected_tool, args = infer_tool(prompt)

        self.assertEqual(selected_tool, "create_asset")
        self.assertEqual(args["asset_id"], "MCTP-1")
        self.assertEqual(args["name"], "Macatop")
        self.assertEqual(args["category"], "laptops")
        self.assertEqual(args["condition"], "good")
        self.assertNotIn("status", args)

    def test_current_datetime_tool_returns_snapshot(self):
        fixed_now = datetime(2026, 6, 1, 10, 30, 5, tzinfo=UTC)

        with (
            timezone.override("UTC"),
            patch("core.ai.tools.timezone.now", return_value=fixed_now),
        ):
            result = registry.get("get_current_datetime").handler(user=self.user)

        self.assertEqual(result["date"], "2026-06-01")
        self.assertEqual(result["time"], "10:30:05")
        self.assertEqual(result["timezone"], "UTC")
        self.assertIn("2026-06-01 10:30:05", result["summary"])

    def test_asset_module_lists_assets_through_ai_chat(self):
        asset = Asset.objects.create(
            asset_id="AI-ASSET-001",
            name="MacBook Pro",
            category="laptops",
            purchase_date=date.today(),
        )

        response = self.client.post(
            "/api/ai/chat/",
            {
                "message": "List assets",
                "tool_name": "list_assets",
                "arguments": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "list_assets")
        self.assertEqual(body["module"], "assets")
        self.assertEqual(body["result"]["assets"][0]["id"], asset.id)
        self.assertEqual(body["result"]["assets"][0]["asset_id"], "AI-ASSET-001")
        self.assertIn("Loaded 1", body["result"]["summary"])

    def test_announcement_module_lists_announcements_through_ai_chat(self):
        announcement = Announcement.objects.create(
            title="AI Announcement",
            body="<p>Hello team</p>",
            author=self.user.profile,
            type="news",
        )

        response = self.client.post(
            "/api/ai/chat/",
            {
                "message": "List announcements",
                "tool_name": "list_announcements",
                "arguments": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "list_announcements")
        self.assertEqual(body["module"], "announcements")
        announcement_rows = body["result"]["announcements"]
        self.assertTrue(announcement_rows)
        self.assertIn(announcement.id, {row["id"] for row in announcement_rows})
        self.assertIn("AI Announcement", {row["title"] for row in announcement_rows})

    def test_time_tracking_module_lists_time_entries_through_ai_chat(self):
        project = Project.objects.create(
            name="AI Internal Project",
            project_type=ProjectType.INTERNAL,
            status=ProjectStatus.ACTIVE,
            stage=ProjectStage.INTAKE,
        )
        entry = TimeEntry(
            employee=self.user.profile,
            project=project,
            work_date=date.today(),
            hours=Decimal("3.50"),
            notes="AI test work",
        )
        entry.duplicate_fingerprint = fingerprint_for_entry(entry)
        entry.save()

        response = self.client.post(
            "/api/ai/chat/",
            {
                "message": "List my time entries",
                "tool_name": "list_time_entries",
                "arguments": {},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "list_time_entries")
        self.assertEqual(body["module"], "time_tracking")
        time_entries = body["result"]["time_entries"]
        self.assertTrue(time_entries)
        self.assertIn(entry.id, {row["id"] for row in time_entries})
        self.assertIn(project.name, {row["project_name"] for row in time_entries})
        self.assertIn("3.50", {row["hours"] for row in time_entries})

    def test_create_time_entry_prompt_routes_to_mutating_tool(self):
        project = Project.objects.create(
            name="Atlas",
            project_type=ProjectType.CLIENT,
            status=ProjectStatus.ACTIVE,
            stage=ProjectStage.INTAKE,
        )

        with patch("core.ai.graph.timezone.localdate", return_value=date(2026, 6, 1)):
            response = self.client.post(
                "/api/ai/chat/",
                {
                    "message": (
                        "Create a timelog entry for me on this date, from 9am "
                        "til 3pm, working on project Atlas, no task"
                    )
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "create_time_entry")
        self.assertEqual(body["module"], "time_tracking")
        self.assertTrue(body["requires_confirmation"])
        self.assertEqual(
            body["pending_confirmation"]["arguments"]["project_id"], project.id
        )
        self.assertEqual(
            body["pending_confirmation"]["arguments"]["work_date"], "2026-06-01"
        )
        self.assertEqual(body["pending_confirmation"]["arguments"]["hours"], "6.00")
        self.assertIsNone(body["pending_confirmation"]["arguments"]["task_id"])

    def test_create_time_entry_tool_uses_serializer_field_names(self):
        project = Project.objects.create(
            name="Atlas",
            project_type=ProjectType.CLIENT,
            status=ProjectStatus.ACTIVE,
            stage=ProjectStage.INTAKE,
        )

        result = registry.get("create_time_entry").handler(
            user=self.user,
            project_id=project.id,
            task_id=None,
            work_date="2026-06-01",
            hours="6.00",
            description="fixing chat routing",
        )

        time_entry = result["time_entry"]
        self.assertEqual(time_entry["project_id"], project.id)
        self.assertEqual(time_entry["employee_id"], self.user.profile.id)
        self.assertIsNone(time_entry["task_id"])
        self.assertEqual(time_entry["hours"], "6.00")
        self.assertEqual(time_entry["notes"], "fixing chat routing")
        self.assertEqual(result["summary"], "Created time entry.")

    def test_announcement_metadata_response_triggers_confirmation_flow(self):
        fake_llm = Mock()
        fake_llm.invoke.return_value.content = (
            '{"module":"announcements","prompt":"Create announcement."}'
        )
        fake_agent = Mock()
        metadata_response = (
            '{"name": "create_announcement", "description": "Create or '
            'schedule a rich-text announcement.", "module": "announcements", '
            '"mutating": true, "sensitive": true, '
            '"requires_confirmation": true, "ui_path": "", '
            '"required_permissions": ["Announcements: manage_announcements"], '
            '"workflow_topic": "", "can_run": true, "deny_reason": "", '
            '"summary": "You can run create_announcement."}'
        )
        pending = {
            "tool_name": "create_announcement",
            "module": "announcements",
            "mutating": True,
            "sensitive": True,
            "description": "Create or schedule a rich-text announcement.",
            "confirmation_label": "Run `create_announcement`",
            "confirmation_help": "",
            "arguments": {
                "title": "Hello world",
                "body": "Lorem ipsum",
                "type": "General",
                "scheduled_at": None,
                "send_email_notifications": False,
            },
            "proposed_arguments": {
                "title": "Hello world",
                "body": "Lorem ipsum",
                "type": "General",
                "scheduled_at": None,
                "send_email_notifications": False,
            },
            "args_schema": None,
            "examples": [],
            "created_at": "2026-06-01T09:00:00+00:00",
            "expires_at": "2026-06-01T09:10:00+00:00",
        }

        def fake_execute_tool(**kwargs):
            session = kwargs["session"]
            session.pending_confirmation = pending
            session.save(update_fields=["pending_confirmation", "updated_at"])
            return {
                "requires_confirmation": True,
                "pending_confirmation": pending,
                "summary": "Please confirm before I run `create_announcement`.",
            }

        with (
            patch("core.ai.graph.get_llm", return_value=fake_llm),
            patch("core.ai.graph.create_react_subagent", return_value=fake_agent),
            patch(
                "core.ai.graph.invoke_subagent",
                return_value=metadata_response,
            ),
            patch(
                "core.ai.graph.execute_tool", side_effect=fake_execute_tool
            ) as execute_tool,
        ):
            response = self.client.post(
                "/api/ai/chat/",
                {
                    "message": (
                        "Create a new announcement of type General, title "
                        '"Hello world", body "Lorem ipsum", and publish it now'
                    )
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "create_announcement")
        self.assertEqual(body["module"], "announcements")
        self.assertEqual(
            body["message"],
            "I need confirmation before running `create_announcement`. Reply with `confirm` to proceed.",
        )
        self.assertTrue(body["requires_confirmation"])
        self.assertEqual(
            body["pending_confirmation"]["tool_name"], "create_announcement"
        )
        execute_tool.assert_called_once()
        self.assertEqual(
            execute_tool.call_args.kwargs["tool_name"], "create_announcement"
        )
        self.assertEqual(
            execute_tool.call_args.kwargs["arguments"]["title"],
            "Hello world",
        )
        self.assertEqual(
            execute_tool.call_args.kwargs["arguments"]["body"],
            "Lorem ipsum",
        )

    def test_asset_metadata_response_triggers_slot_fill_flow(self):
        fake_llm = Mock()
        fake_llm.invoke.return_value.content = (
            '{"module":"assets","prompt":"Create asset."}'
        )
        fake_agent = Mock()
        metadata_response = (
            '{"name": "create_asset", "description": "Register a new asset in '
            'inventory. Requires Asset configure permission.", "module": '
            '"assets", "mutating": true, "sensitive": true, '
            '"requires_confirmation": true, "ui_path": "", '
            '"required_permissions": ["Asset Management: configure_asset_types"], '
            '"workflow_topic": "create_asset", "can_run": true, '
            '"deny_reason": "", "summary": "You can run create_asset."}'
        )

        with (
            patch("core.ai.graph.get_llm", return_value=fake_llm),
            patch("core.ai.graph.create_react_subagent", return_value=fake_agent),
            patch(
                "core.ai.graph.invoke_subagent",
                return_value=metadata_response,
            ),
        ):
            response = self.client.post(
                "/api/ai/chat/",
                {
                    "message": (
                        "Create a new asset name Macatop, id MCTP-1, "
                        "category laptop, condition Good, status active."
                    )
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "create_asset")
        self.assertEqual(body["module"], "assets")
        self.assertTrue(body["requires_input"])
        self.assertTrue(body["requires_confirmation"])
        self.assertIn("purchase_date", body["pending_confirmation"]["question"])
        self.assertIn(
            "purchase_date",
            {
                field["field"]
                for field in body["pending_confirmation"]["missing_fields"]
            },
        )
        self.assertEqual(body["ui_action_type"], "form")

    def test_orchestrated_json_response_is_rendered_as_plain_text(self):
        fake_llm = Mock()
        fake_llm.invoke.return_value.content = (
            '{"module":"announcements","prompt":"List recent announcements."}'
        )
        fake_agent = Mock()
        raw_json = (
            '{"announcements":[{"id":21,"title":"We are here on the behalf of '
            'the N.Y.O.B.","type":"general","author_id":1,"author_name":"Johnas Doe"},'
            '{"id":20,"title":"HUGE ANNOUNCEMENT","type":"news","author_id":1,'
            '"author_name":"Johnas Doe"}]}'
        )

        with (
            patch("core.ai.graph.get_llm", return_value=fake_llm),
            patch("core.ai.graph.create_react_subagent", return_value=fake_agent),
            patch("core.ai.graph.invoke_subagent", return_value=raw_json),
        ):
            response = self.client.post(
                "/api/ai/chat/",
                {"message": "List me recent announcements"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotEqual(body["message"][:1], "{")
        self.assertIn("Loaded 2 announcement", body["message"])
        self.assertIn("HUGE ANNOUNCEMENT", body["message"])

    def test_generic_json_payload_is_summarized(self):
        payload = (
            '{"balances":[{"leave_type":"vacation","remaining_days":15}],'
            '"total_count":1}'
        )

        result = normalize_assistant_message(payload)

        self.assertNotIn("{", result)
        self.assertIn("Loaded 1 balance", result)

    def test_explicit_raw_json_request_returns_json(self):
        fake_llm = Mock()
        fake_llm.invoke.return_value.content = (
            '{"module":"announcements","prompt":"List recent announcements."}'
        )
        fake_agent = Mock()

        with (
            patch("core.ai.graph.get_llm", return_value=fake_llm),
            patch("core.ai.graph.create_react_subagent", return_value=fake_agent),
            patch(
                "core.ai.graph.invoke_subagent", return_value="Loaded announcements."
            ),
        ):
            response = self.client.post(
                "/api/ai/chat/",
                {"message": "Show raw JSON for recent announcements"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["message"].lstrip().startswith("{"))
        self.assertIn('"summary"', body["message"])

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

    def test_who_am_i_returns_json_safe_current_user_context(self):
        SalaryRecord.objects.create(
            user_profile=self.user.profile,
            amount="1234.56",
            effective_date=date.today(),
        )

        response = self.client.post(
            "/api/ai/chat/",
            {"message": "Who am i?"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool_name"], "get_current_user_context")
        self.assertEqual(body["result"]["user"]["email"], "chat-user@example.com")
        self.assertEqual(body["result"]["profile"]["current_salary"], "1234.56")

    def test_employee_list_and_count_prompts_use_same_unfiltered_scope(self):
        User.objects.create_user(
            username="second-user",
            email="second@example.com",
            password="password123",
            first_name="Second",
            last_name="User",
        )

        list_response = self.client.post(
            "/api/ai/chat/",
            {"message": "Show me all employees"},
            format="json",
        )
        count_response = self.client.post(
            "/api/ai/chat/",
            {"message": "how many employees do we have?"},
            format="json",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(count_response.status_code, 200)
        list_body = list_response.json()
        count_body = count_response.json()
        self.assertEqual(list_body["tool_name"], "search_employees")
        self.assertEqual(count_body["tool_name"], "search_employees")
        self.assertEqual(list_body["result"]["total_count"], 2)
        self.assertEqual(count_body["result"]["total_count"], 2)
        self.assertEqual(
            count_body["result"]["summary"], "There are 2 employee profile(s)."
        )

    def test_unexpected_ai_error_returns_assistant_fallback_message(self):
        with patch("core.ai.api.run_assistant_turn", side_effect=TypeError("boom")):
            response = self.client.post(
                "/api/ai/chat/",
                {"message": "Who am i?"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["message"],
            "I am not able to fulfill your request, try a different prompt.",
        )
        self.assertEqual(body["result"]["reason"], "ai_service_failed")

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

    def test_orchestrated_turn_includes_pending_confirmation_context(self):
        session = AIChatSession.objects.create(user=self.user)
        now = datetime.now(UTC)
        pending = {
            "tool_name": "create_time_entry",
            "module": "time_tracking",
            "mutating": True,
            "sensitive": False,
            "confirmation_label": "Create time entry",
            "confirmation_help": "",
            "arguments": {
                "work_date": "2026-06-01",
                "start_time": "09:00",
                "end_time": "15:00",
                "project_id": 1,
                "task_id": None,
                "description": "",
            },
            "proposed_arguments": {
                "work_date": "2026-06-01",
                "start_time": "09:00",
                "end_time": "15:00",
                "project_id": 1,
                "task_id": None,
                "description": "",
            },
            "args_schema": None,
            "examples": [],
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
        session.pending_confirmation = pending
        session.save(update_fields=["pending_confirmation", "updated_at"])

        fake_llm = Mock()
        fake_llm.invoke.return_value.content = (
            '{"module":"time_tracking","prompt":"Continue with the current task."}'
        )
        fake_agent = Mock()

        with (
            patch("core.ai.graph.get_llm", return_value=fake_llm),
            patch("core.ai.graph.create_react_subagent", return_value=fake_agent),
            patch(
                "core.ai.graph.orchestrator_decision",
                return_value=(
                    {
                        "module": "time_tracking",
                        "prompt": "Continue with the current task.",
                    },
                    {},
                ),
            ),
            patch(
                "core.ai.graph.invoke_subagent",
                return_value=("Working on it.", {}),
            ) as invoke_subagent,
        ):
            response = self.client.post(
                "/api/ai/chat/",
                {
                    "session_id": session.id,
                    "message": "what about it?",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        invoke_subagent.assert_called_once()
        _, kwargs = invoke_subagent.call_args
        history = kwargs["history"]
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "system")
        self.assertIn("Open pending confirmation context", history[0]["content"])
        self.assertIn('"tool_name": "create_time_entry"', history[0]["content"])

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

    def test_session_detail_renders_blank_button_messages_with_labels(self):
        session = AIChatSession.objects.create(user=self.user, title="Buttons")
        AIChatMessage.objects.create(
            session=session,
            role=AIChatMessage.Role.USER,
            content="",
            metadata={"confirm": True},
        )
        AIChatMessage.objects.create(
            session=session,
            role=AIChatMessage.Role.USER,
            content="",
            metadata={"tool_name": "cancel_pending_action"},
        )

        detail_response = self.client.get(f"/api/ai/chat/sessions/{session.id}/")

        self.assertEqual(detail_response.status_code, 200)
        messages = detail_response.json()["messages"]
        self.assertEqual(messages[0]["content"], "Confirm")
        self.assertEqual(messages[1]["content"], "Cancel")

    def test_session_detail_renders_cancel_button_lowercase_as_cancel(self):
        session = AIChatSession.objects.create(user=self.user, title="Buttons")
        AIChatMessage.objects.create(
            session=session,
            role=AIChatMessage.Role.USER,
            content="cancel",
            metadata={"tool_name": "cancel_pending_action"},
        )

        detail_response = self.client.get(f"/api/ai/chat/sessions/{session.id}/")

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["messages"][0]["content"], "Cancel")

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
