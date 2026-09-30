import unittest
from unittest.mock import call, patch
from urllib.error import URLError

from agent_ui_api import Client, SessionMessageError


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.client = Client("http://server", "secret")

    def test_message_session(self):
        with patch.object(
            self.client,
            "start_turn",
            return_value={
                "status": "running",
                "message_id": 123,
            },
        ) as send:
            self.assertEqual(self.client.message_session(7, "hello"), 123)
            send.assert_called_once_with(7, "hello")

    def test_start_session_creates_before_messaging(self):
        with patch.object(
            self.client,
            "request",
            side_effect=[
                {"id": 7},
                {"status": "running", "message_id": 123},
            ],
        ) as transport:
            result = self.client.start_session(
                "Review",
                "/project",
                "hello",
                agent="pi",
                worktree_id=2,
                sandbox=False,
                model="openai-codex/gpt-5.5",
                reasoning_level="high",
            )
            self.assertEqual(result, {"session_id": 7, "message_id": 123})
            self.assertEqual(
                transport.call_args_list,
                [
                    call(
                        "POST",
                        "/sessions",
                        body={
                            "name": "Review",
                            "project_path": "/project",
                            "agent": "pi",
                            "worktree_id": 2,
                            "sandbox": False,
                            "model": "openai-codex/gpt-5.5",
                            "reasoning_level": "high",
                        },
                    ),
                    call("POST", "/sessions/7/turn", body={"prompt": "hello"}),
                ],
            )

    def test_start_session_uses_message_abstraction(self):
        with (
            patch.object(self.client, "create_session", return_value={"id": 7}),
            patch.object(self.client, "message_session", return_value=123) as message,
        ):
            self.assertEqual(
                self.client.start_session("Review", "/project", "hello"),
                {"session_id": 7, "message_id": 123},
            )
            message.assert_called_once_with(7, "hello")

    def test_creation_failure_does_not_message(self):
        error = URLError("creation failed")
        with (
            patch.object(self.client, "create_session", side_effect=error),
            patch.object(self.client, "message_session") as message,
        ):
            with self.assertRaises(URLError) as caught:
                self.client.start_session("Review", "/project", "hello")
            self.assertIs(caught.exception, error)
            message.assert_not_called()

    def test_message_failure_preserves_session_and_cause_without_retry(self):
        error = TimeoutError("acceptance unknown")
        with patch.object(
            self.client, "request", side_effect=[{"id": 7}, error]
        ) as transport:
            with self.assertRaises(SessionMessageError) as caught:
                self.client.start_session("Review", "/project", "hello")
            self.assertEqual(caught.exception.session_id, 7)
            self.assertIs(caught.exception.__cause__, error)
            self.assertEqual(transport.call_count, 2)

    def test_read_session_returns_page_unchanged(self):
        for after, limit in ((None, 200), (123, 10)):
            page = {"messages": [], "next_cursor": after, "has_more": False}
            with (
                self.subTest(after=after),
                patch.object(self.client, "get_scrollback", return_value=page) as read,
            ):
                self.assertIs(
                    self.client.read_session(7, after=after, limit=limit), page
                )
                read.assert_called_once_with(7, after=after, limit=limit)


if __name__ == "__main__":
    unittest.main()
