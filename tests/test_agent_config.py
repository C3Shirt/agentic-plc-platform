import tempfile
import unittest
from pathlib import Path

from agentic_plc.agent.config import LLMConfig, load_dotenv_values
from agentic_plc.agent.planner import (
    completion_url,
    completion_url_candidates,
    parse_json_object,
)


class AgentConfigTests(unittest.TestCase):
    def test_loads_project_env_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OpenAIBaseURL=\"https://example.test/v1\"\n"
                "APIKey='dummy-key'\n"
                "OpenAIModel=test-model\n"
                "LLM_MAX_TOKENS=123\n"
                "LLM_CONTEXT_MAX_POINTS=7\n"
                "LLM_CONTEXT_MAX_EVENTS=4\n",
                encoding="utf-8",
            )

            config = LLMConfig.from_env(env_file, environ={})

            self.assertTrue(config.is_configured)
            self.assertEqual(config.base_url, "https://example.test/v1")
            self.assertEqual(config.api_key, "dummy-key")
            self.assertEqual(config.model, "test-model")
            self.assertEqual(config.max_tokens, 123)
            self.assertEqual(config.context_max_points, 7)
            self.assertEqual(config.context_max_events, 4)

    def test_explicit_empty_environ_does_not_read_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OpenAIBaseURL=https://example.test/v1\n", encoding="utf-8")

            config = LLMConfig.from_env(env_file, environ={})

            self.assertFalse(config.is_configured)
            self.assertEqual(config.base_url, "https://example.test/v1")
            self.assertIsNone(config.api_key)

    def test_load_dotenv_values_ignores_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("# comment\nexport APIKey=abc\n", encoding="utf-8")

            self.assertEqual(load_dotenv_values(env_file), {"APIKey": "abc"})

    def test_completion_url_preserves_explicit_endpoint(self) -> None:
        self.assertEqual(
            completion_url("https://example.test/v1/chat/completions"),
            "https://example.test/v1/chat/completions",
        )
        self.assertEqual(
            completion_url("https://example.test/v1"),
            "https://example.test/v1/chat/completions",
        )
        self.assertEqual(
            completion_url_candidates("https://example.test"),
            [
                "https://example.test/chat/completions",
                "https://example.test/v1/chat/completions",
            ],
        )

    def test_parse_json_object_accepts_markdown_fence(self) -> None:
        payload = parse_json_object('```json\n{"action": "none"}\n```')

        self.assertEqual(payload["action"], "none")


if __name__ == "__main__":
    unittest.main()
