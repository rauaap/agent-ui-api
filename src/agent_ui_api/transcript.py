"""Durable transcript payload shapes, not a WebSocket transport or validator.

ScrollbackEvent.payload remains open for all existing event kinds. These types
can describe known payloads without converting or repairing server responses.
Transient stream frames are documented in README.md and never appear in REST
scrollback. Draft shapes occur here because interruptions persist a full draft.
"""

from typing import Any, Literal, TypedDict


class _DraftIdentity(TypedDict):
    stream_id: str
    turn_id: str
    ordinal: int
    revision: int


class TextDraft(_DraftIdentity):
    kind: Literal["text"]
    text: str


class ToolDraft(_DraftIdentity):
    """Read-only provisional data; never a canonical action or permission."""

    kind: Literal["tool"]
    call_id: str
    name: str
    arguments_json: str
    arguments_preview: dict[str, Any]


StreamDraft = TextDraft | ToolDraft


class FinalStreamMetadata(TypedDict, total=False):
    """All three fields accompany streamed finals; historical rows may omit them."""

    stream_id: str
    turn_id: str
    ordinal: int


class OutputPayload(FinalStreamMetadata):
    text: str


class ToolUsePayload(FinalStreamMetadata):
    """Complete canonical action; generation completion does not imply execution."""

    call_id: str
    action: dict[str, Any]


class StreamInterruptedPayload(TypedDict):
    """Durable, non-executable draft; unlike transient frames, has a history row."""

    draft: StreamDraft
    reason: Literal["cancelled", "error", "retry", "incomplete"]


class QuestionPayload(TypedDict):
    """Current request: call_id joins the tool card; request_id identifies answers.

    Historical question rows may lack call_id. REST returns those unchanged;
    this definition describes newly generated requests, not a migration.
    """

    request_id: str
    call_id: str
    questions: list[dict[str, Any]]
