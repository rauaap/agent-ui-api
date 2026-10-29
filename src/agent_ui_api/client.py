"""One-to-one wrappers for the server's REST routes."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, NotRequired, TypedDict
from urllib.parse import quote

from .request import request


class ServerSettings(TypedDict):
    """Server-owned preferences; template text is not expanded by the server."""

    worktree_path_template: str
    default_working_directory: str
    default_agent: str | None


class SharedAssetRoot(TypedDict):
    """Registered server directory; ``url`` is server-relative, not absolute."""

    asset_root: str
    path: str
    project_id: int | None
    url: str


class SharedAssetRootCreate(TypedDict):
    asset_root: str
    path: str
    project_id: NotRequired[int | None]


class SharedAssetRootUpdate(TypedDict, total=False):
    """Omitted fields stay unchanged; project_id=None makes a root global."""

    asset_root: str
    path: str
    project_id: int | None


class ScrollbackEvent(TypedDict):
    """Durable event, including stream_interrupted, never live stream drafts.

    Globally allocated IDs may have gaps across sessions. Known payload shapes
    are exported from transcript.py; payload stays open for other event kinds.
    """

    id: int
    session_id: int
    ts: str
    type: str
    payload: dict[str, Any]


class ScrollbackPage(TypedDict):
    """Chronological events and a directional cursor; queue is a current snapshot."""

    messages: list[ScrollbackEvent]
    next_cursor: int | None
    has_more: bool
    queued_messages: list[dict[str, Any]]


class _Unset(Enum):
    VALUE = 0


def _project_id_fields(project_id: int | None | _Unset) -> dict[str, Any]:
    if project_id is _Unset.VALUE:
        return {}
    if project_id is not None and type(project_id) is not int:
        raise TypeError("project_id must be an integer or None")
    return {"project_id": project_id}


class SandboxPath(TypedDict):
    path: str
    write: bool


class SandboxNetworkDestination(TypedDict):
    """An exact unicast IPv4 literal and TCP port (1–65535)."""

    ip: str
    port: int


class SandboxNetworkUpdate(TypedDict):
    """Required replacement list; an empty list clears all exceptions."""

    sandbox_network_allowlist: list[SandboxNetworkDestination]


class SandboxNetworkSettings(TypedDict):
    """Server-wide settings, with duplicates collapsed by the server."""

    sandbox_network_allowlist: list[SandboxNetworkDestination]


class Project(TypedDict):
    """Project settings; counts/activity include all sessions.

    Network entries are its own, not the effective union.
    """

    id: int
    path: str
    name: str
    is_git_repo: bool
    session_count: int
    last_active_at: str | None
    sandbox_paths: list[SandboxPath]
    sandbox_network_allowlist: list[SandboxNetworkDestination]


class ProjectCreate(TypedDict):
    """New project settings; repeated creation leaves existing settings unchanged."""

    path: str
    name: NotRequired[str]
    sandbox_paths: NotRequired[list[SandboxPath]]
    sandbox_network_allowlist: NotRequired[list[SandboxNetworkDestination]]


class ProjectUpdate(TypedDict):
    """Omitted settings stay unchanged; [] restores server network inheritance."""

    path: str
    sandbox_paths: NotRequired[list[SandboxPath]]
    sandbox_network_allowlist: NotRequired[list[SandboxNetworkDestination]]


class Image(TypedDict):
    """Immutable uploaded image metadata; never includes bytes or storage paths."""

    id: str
    mime_type: str
    size: int
    width: int
    height: int


class TurnRequest(TypedDict, total=False):
    """HTTP turn input. Meaningful prompt or at least one image ID is required."""

    prompt: str
    images: list[str]


class Model(TypedDict):
    """A selectable model. ``id`` is opaque; pass it back unchanged.

    ``reasoning_levels`` is the harness's own vocabulary, in its order; empty
    means the model offers no reasoning choice. ``input`` lists supported
    input types; image attachments require ``"image" in input``.
    """

    id: str
    name: str
    reasoning_levels: list[str]
    input: list[str]


class Agent(TypedDict):
    """An agent (harness) with its model catalog, discovered at server startup.

    On discovery failure, ``models`` is empty and ``models_error`` is set.
    """

    id: str
    name: str
    default: bool
    models: list[Model]
    models_error: str | None


class SessionMessageError(Exception):
    """A session was created, but sending its initial message failed.

    The original exception is available as __cause__. The session is retained.
    """

    def __init__(self, session_id: int):
        self.session_id = session_id
        super().__init__(
            f"Session {session_id} was created, but its initial message failed"
        )


def _provided(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


@dataclass
class Client:
    """Synchronous client. Credentials are supplied by the caller, never loaded.

    Responses are the server's decoded JSON, without domain-model conversion.
    Optional fields set to None are omitted, leaving defaults to the server,
    except shared-asset project_id: explicit None makes the root global.
    Settings updates also preserve explicit None for server validation.
    """

    base_url: str
    token: str = field(repr=False)
    timeout: float = 30.0

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        return request(
            self.base_url,
            method,
            path,
            token=self.token,
            body=body,
            query=query,
            timeout=self.timeout,
        )

    def message_session(self, session_id: int, message: str) -> int:
        """Submit a message and return its persisted input ID, not a response."""
        return self.start_turn(session_id, message)["message_id"]

    def start_session(
        self,
        name: str,
        project_path: str,
        message: str,
        *,
        agent: str | None = None,
        worktree_id: int | None = None,
        sandbox: bool | None = None,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> dict[str, int]:
        """Create a session under an existing project, then send its first message.

        Creation errors propagate unchanged. If messaging fails, raise
        SessionMessageError with the created session ID and chained cause.
        No retries or cleanup are performed: a network error can leave message
        acceptance uncertain, so retrying automatically could duplicate it.
        """
        session = self.create_session(
            name,
            project_path,
            agent=agent,
            worktree_id=worktree_id,
            sandbox=sandbox,
            model=model,
            reasoning_level=reasoning_level,
        )
        session_id = session["id"]
        try:
            message_id = self.message_session(session_id, message)
        except Exception as exc:
            raise SessionMessageError(session_id) from exc
        return {"session_id": session_id, "message_id": message_id}

    def read_session(
        self,
        session_id: int,
        *,
        after: int | None = None,
        before: int | None = None,
        limit: int = 200,
    ) -> ScrollbackPage:
        """Read a scrollback page in either direction; no waiting or aggregation.

        Includes durable stream_interrupted entries, never active drafts/deltas.
        See get_scrollback for cursor semantics and server validation.
        """
        return self.get_scrollback(session_id, after=after, before=before, limit=limit)

    def list_agents(self) -> list[Agent]:
        """Read agents and their model catalogs, discovered once at server startup."""
        return self.request("GET", "/agents")

    def get_usage(self) -> dict[str, Any]:
        return self.request("GET", "/usage")

    def get_settings(self) -> ServerSettings:
        """Read current server preferences without caching or local defaults."""
        return self.request("GET", "/settings")

    def update_settings(
        self,
        *,
        worktree_path_template: str | None | _Unset = _Unset.VALUE,
        default_working_directory: str | None | _Unset = _Unset.VALUE,
        default_agent: str | None | _Unset = _Unset.VALUE,
    ) -> ServerSettings:
        """Update supplied preferences and return the full server settings.

        Omitted fields stay unchanged. Explicit None is sent as null: it clears
        default_agent, but is rejected for the template and directory. Validation
        and trimming belong to the server; this client does not expand templates
        or apply preferences to creation requests.
        """
        body = {
            key: value
            for key, value in {
                "worktree_path_template": worktree_path_template,
                "default_working_directory": default_working_directory,
                "default_agent": default_agent,
            }.items()
            if value is not _Unset.VALUE
        }
        return self.request("PATCH", "/settings", body=body)

    def get_sandbox_paths(self) -> dict[str, Any]:
        return self.request("GET", "/sandbox-paths")

    def update_sandbox_paths(self, sandbox_paths: list[SandboxPath]) -> dict[str, Any]:
        return self.request(
            "PATCH", "/sandbox-paths", body={"sandbox_paths": sandbox_paths}
        )

    def get_sandbox_network(self) -> SandboxNetworkSettings:
        """Read server-wide TCP exceptions, not project/session settings."""
        return self.request("GET", "/sandbox-network")

    def update_sandbox_network(
        self, sandbox_network_allowlist: list[SandboxNetworkDestination]
    ) -> SandboxNetworkSettings:
        """Replace server exceptions; [] clears that scope. Return the server's list.

        Validation and deduplication belong to the server. Changes apply only
        to newly launched sandboxed turns; no WebSocket event is emitted.
        """
        body: SandboxNetworkUpdate = {
            "sandbox_network_allowlist": sandbox_network_allowlist
        }
        return self.request("PATCH", "/sandbox-network", body=body)

    def list_shared_asset_roots(self) -> list[SharedAssetRoot]:
        return self.request("GET", "/shared-asset-roots")

    def create_shared_asset_root(
        self,
        asset_root: str,
        path: str,
        *,
        project_id: int | None | _Unset = _Unset.VALUE,
    ) -> SharedAssetRoot:
        """Register a directory without creating it. Omitted project_id is global."""
        return self.request(
            "POST",
            "/shared-asset-roots",
            body={"asset_root": asset_root, "path": path, **_project_id_fields(project_id)},
        )

    def update_shared_asset_root(
        self,
        asset_root: str,
        *,
        new_asset_root: str | None = None,
        path: str | None = None,
        project_id: int | None | _Unset = _Unset.VALUE,
    ) -> SharedAssetRoot:
        """PATCH a root, optionally renaming it (which breaks old links).

        None omits name/path. Omitted project_id stays unchanged; explicit None
        makes it global. Integer IDs are sent without coercion (not bool/float).
        """
        return self.request(
            "PATCH",
            f"/shared-asset-roots/{quote(asset_root, safe='')}",
            body={
                **_provided(asset_root=new_asset_root, path=path),
                **_project_id_fields(project_id),
            },
        )

    def delete_shared_asset_root(self, asset_root: str) -> None:
        """Unregister a root, never deleting its files. Returns None (HTTP 204)."""
        return self.request(
            "DELETE", f"/shared-asset-roots/{quote(asset_root, safe='')}"
        )

    def get_directory_existence(self) -> dict[str, bool]:
        """Fresh server directory snapshot for project/worktree/effective session paths.

        Keys are absolute paths; an absent key is unknown, not False.
        """
        return self.request("GET", "/directory-existence")

    def list_projects(self) -> list[Project]:
        return self.request("GET", "/projects")

    def create_project(
        self,
        path: str,
        *,
        name: str | None = None,
        sandbox_paths: list[SandboxPath] | None = None,
        sandbox_network_allowlist: list[SandboxNetworkDestination] | None = None,
    ) -> Project:
        """Create a project; existing projects keep their settings.

        None omits the network list (server default []); supplied entries are
        project-only and validated/deduplicated by the server.
        """
        return self.request(
            "POST",
            "/projects",
            body=_provided(
                path=path,
                name=name,
                sandbox_paths=sandbox_paths,
                sandbox_network_allowlist=sandbox_network_allowlist,
            ),
        )

    def update_project(
        self,
        path: str,
        *,
        sandbox_paths: list[SandboxPath] | None = None,
        sandbox_network_allowlist: list[SandboxNetworkDestination] | None = None,
    ) -> Project:
        """Replace supplied project settings; None leaves them unchanged.

        [] clears project network entries, retaining inherited server entries.
        Responses contain only the project's own list, not the effective union.
        """
        return self.request(
            "PATCH",
            "/projects",
            body=_provided(
                path=path,
                sandbox_paths=sandbox_paths,
                sandbox_network_allowlist=sandbox_network_allowlist,
            ),
        )

    def delete_project(self, path: str) -> dict[str, Any]:
        return self.request("DELETE", "/projects", body={"path": path})

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.request("GET", "/sessions")

    def create_session(
        self,
        name: str,
        project_path: str,
        *,
        agent: str | None = None,
        worktree_id: int | None = None,
        sandbox: bool | None = None,
        model: str | None = None,
        reasoning_level: str | None = None,
    ) -> dict[str, Any]:
        """Create a session. ``model=None`` selects the first catalog entry;
        the server persists its ID. ``reasoning_level=None`` uses the harness's
        default reasoning level.

        Explicit model IDs must come from the selected agent's ``models`` in
        ``list_agents()``. A supplied reasoning level must be in the selected
        model's ``reasoning_levels``, even when ``model`` is omitted.
        """
        return self.request(
            "POST",
            "/sessions",
            body=_provided(
                name=name,
                project_path=project_path,
                agent=agent,
                worktree_id=worktree_id,
                sandbox=sandbox,
                model=model,
                reasoning_level=reasoning_level,
            ),
        )

    def update_session(
        self,
        session_id: int,
        *,
        name: str | None = None,
        auto_approve_write: bool | None = None,
        auto_approve_command: bool | None = None,
        auto_approve_inter_agent_communication: bool | None = None,
        sandbox: bool | None = None,
        reasoning_level: str | None = None,
    ) -> dict[str, Any]:
        """Update a session. A new ``reasoning_level`` applies from the next turn;
        it must be in the session model's ``reasoning_levels`` and cannot be
        cleared back to the harness default.
        """
        return self.request(
            "PATCH",
            f"/sessions/{session_id}",
            body=_provided(
                name=name,
                auto_approve_write=auto_approve_write,
                auto_approve_command=auto_approve_command,
                auto_approve_inter_agent_communication=(
                    auto_approve_inter_agent_communication
                ),
                sandbox=sandbox,
                reasoning_level=reasoning_level,
            ),
        )

    def stop_session(self, session_id: int) -> dict[str, Any]:
        return self.request("POST", f"/sessions/{session_id}/stop")

    def delete_session(self, session_id: int) -> dict[str, Any]:
        return self.request("DELETE", f"/sessions/{session_id}")

    def get_scrollback(
        self,
        session_id: int,
        *,
        after: int | None = None,
        before: int | None = None,
        limit: int = 200,
    ) -> ScrollbackPage:
        """Read persisted events in ascending ID order, with exclusive cursors.

        before selects the nearest older events; next_cursor is their oldest ID
        and has_more means more older events exist. Otherwise, after selects
        newer events (omit it to start at the beginning), with the newest ID as
        next_cursor. Empty pages retain the supplied cursor, or None.
        queued_messages is the current queue snapshot, not historical data.
        Final streaming metadata and stream_interrupted payloads pass through
        unchanged; transient stream frames/active drafts are never returned.
        The server validates nonnegative cursors, their mutual exclusion (422),
        and limit (1–1000, default 200). No pagination or polling is automatic.
        """
        return self.request(
            "GET",
            f"/sessions/{session_id}/scrollback",
            query=_provided(after=after, before=before, limit=limit),
        )

    def upload_image(self, data: bytes, mime_type: str) -> Image:
        """Upload original binary bytes; validation and limits belong to the server."""
        return request(
            self.base_url,
            "POST",
            "/images",
            token=self.token,
            raw_body=data,
            content_type=mime_type,
            timeout=self.timeout,
        )

    def download_image(self, image_id: str) -> bytes:
        """Retrieve original bytes with authentication, without JSON decoding."""
        return request(
            self.base_url,
            "GET",
            f"/images/{quote(image_id, safe='')}",
            token=self.token,
            binary_response=True,
            timeout=self.timeout,
        )

    def start_turn(
        self, session_id: int, prompt: str = "", *, images: list[str] | None = None
    ) -> dict[str, Any]:
        """Submit text and/or ordered image IDs; return acceptance, not completion.

        Omitted prompt sends an empty string. None omits images; [] is sent.
        Server validation requires meaningful text or at least one image.
        Repeated IDs remain repeated occurrences, in the supplied order.
        """
        return self.request(
            "POST",
            f"/sessions/{session_id}/turn",
            body={"prompt": prompt, **_provided(images=images)},
        )

    def start_bash(self, session_id: int, command: str) -> dict[str, Any]:
        """Submit a bash command; execution and output are asynchronous."""
        return self.request(
            "POST", f"/sessions/{session_id}/bash", body={"command": command}
        )

    def list_worktrees(self, project_path: str | None = None) -> list[dict[str, Any]]:
        return self.request(
            "GET", "/worktrees", query=_provided(project_path=project_path)
        )

    def create_worktree(
        self, project_path: str, path: str, branch: str
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/worktrees",
            body={"project_path": project_path, "path": path, "branch": branch},
        )

    def delete_worktree(self, worktree_id: int) -> dict[str, Any]:
        """Safely remove a worktree; preserve sessions and their working directories.

        The server clears attached worktree IDs. Busy-directory and Git safety
        failures propagate as HTTPError; removal is never forced or retried.
        """
        return self.request("DELETE", f"/worktrees/{worktree_id}")
