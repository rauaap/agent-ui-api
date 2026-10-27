"""Owned contract: typed durable payloads and unchanged HTTP history responses."""

import json
from pathlib import Path
import re
from typing import get_args, get_type_hints
import unittest
from unittest.mock import patch

from agent_ui_api import (
    Client,
    FinalStreamMetadata,
    OutputPayload,
    QuestionPayload,
    StreamDraft,
    StreamInterruptedPayload,
    TextDraft,
    ToolDraft,
    ToolUsePayload,
)


class TranscriptTypesTests(unittest.TestCase):
    def test_draft_shapes(self):
        identity = {"stream_id", "turn_id", "ordinal", "revision", "kind"}
        self.assertEqual(TextDraft.__required_keys__, identity | {"text"})
        self.assertEqual(
            ToolDraft.__required_keys__,
            identity | {"call_id", "name", "arguments_json", "arguments_preview"},
        )
        self.assertEqual(set(get_args(StreamDraft)), {TextDraft, ToolDraft})
        self.assertEqual(get_args(get_type_hints(TextDraft)["kind"]), ("text",))
        self.assertEqual(get_args(get_type_hints(ToolDraft)["kind"]), ("tool",))

    def test_final_metadata_is_optional_for_historical_payloads(self):
        metadata = {"stream_id", "turn_id", "ordinal"}
        self.assertEqual(FinalStreamMetadata.__required_keys__, set())
        self.assertEqual(FinalStreamMetadata.__optional_keys__, metadata)
        for model, required in (
            (OutputPayload, {"text"}),
            (ToolUsePayload, {"call_id", "action"}),
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(model.__required_keys__, required)
                self.assertEqual(model.__optional_keys__, metadata)
                self.assertNotIn("event_id", get_type_hints(model))

    def test_interruption_and_question_correlation(self):
        self.assertEqual(StreamInterruptedPayload.__required_keys__, {"draft", "reason"})
        self.assertEqual(
            set(get_args(get_type_hints(StreamInterruptedPayload)["reason"])),
            {"cancelled", "error", "retry", "incomplete"},
        )
        self.assertEqual(
            QuestionPayload.__required_keys__, {"request_id", "call_id", "questions"}
        )

    def test_readme_wire_examples_are_valid_json(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        for block in re.findall(r"```json\n(.*?)\n```", readme, re.DOTALL):
            for line in block.splitlines():
                with self.subTest(frame=line):
                    json.loads(line)


class TranscriptHistoryTests(unittest.TestCase):
    def test_durable_payloads_pass_through_forward_and_backward_unchanged(self):
        client = Client("http://server", "secret")
        identity = {"stream_id": "s1", "turn_id": "t1", "ordinal": 0}
        draft = {
            **identity, "revision": 2, "kind": "tool", "call_id": "c1",
            "name": "message_session", "arguments_json": '{"message":"unfinished',
            "arguments_preview": {"message": "unfinished"},
        }
        payloads = [
            ("output", {"text": "historical"}),
            ("output", {"text": "authoritative final", **identity}),
            ("tool_use", {
                "call_id": "c2", "action": {"kind": "other", "name": "unknown", "arguments": {}},
                "stream_id": "s2", "turn_id": "t1", "ordinal": 1,
            }),
            ("question", {"request_id": "old", "questions": []}),
            ("question", {"request_id": "q1", "call_id": "c2", "questions": []}),
            ("stream_interrupted", {"draft": draft, "reason": "cancelled"}),
        ]
        rows = [
            {"id": i, "session_id": 7, "ts": "2026-01-01T00:00:00Z", "type": kind, "payload": payload}
            for i, (kind, payload) in enumerate(payloads, start=10)
        ]
        for method in (client.get_scrollback, client.read_session):
            for cursor in ({"after": 9}, {"before": 20}):
                page = {
                    "messages": rows,
                    "next_cursor": 10 if "before" in cursor else 15,
                    "has_more": False,
                    "queued_messages": [],
                }
                original = json.dumps(page, sort_keys=True)
                with self.subTest(method=method.__name__, cursor=cursor), patch.object(
                    client, "request", return_value=page
                ) as transport:
                    result = method(7, **cursor)
                    self.assertIs(result, page)
                    self.assertIs(result["messages"][-1]["payload"]["draft"], draft)
                    self.assertEqual(json.dumps(result, sort_keys=True), original)
                    transport.assert_called_once_with(
                        "GET", "/sessions/7/scrollback", query={**cursor, "limit": 200}
                    )

    def test_submission_stays_one_complete_request(self):
        client = Client("http://server", "secret")
        message = "Complete message \\ Unicode: α\n" * 1000
        with patch.object(client, "request", return_value={"message_id": 42}) as send:
            self.assertEqual(client.message_session(7, message), 42)
            send.assert_called_once_with(
                "POST", "/sessions/7/turn", body={"prompt": message}
            )


if __name__ == "__main__":
    unittest.main()
