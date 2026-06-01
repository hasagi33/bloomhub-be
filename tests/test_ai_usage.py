from unittest.mock import Mock

from django.test import SimpleTestCase

from core.ai.usage import extract_token_usage


class AIUsageTests(SimpleTestCase):
    def test_extract_token_usage_ignores_mock_response_objects(self):
        response = Mock()
        response.content = "ok"

        self.assertEqual(extract_token_usage(response), {})

    def test_extract_token_usage_reads_nested_usage_mapping(self):
        payload = {
            "response_metadata": {
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 7,
                }
            }
        }

        self.assertEqual(
            extract_token_usage(payload),
            {
                "input_tokens": 12,
                "output_tokens": 7,
                "prompt_tokens": 12,
                "completion_tokens": 7,
            },
        )
