"""One-to-one wrappers for the server's REST routes."""

from dataclasses import dataclass, field
from typing import Any, TypedDict

from .request import request


class SandboxPath(TypedDict):
    path: str
    write: bool


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
    Optional fields set to None are omitted, leaving defaults to the server.
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
    ) -> dict[str, int]:
        """Create a session under an existing project, then send its first message.

        Creation errors propagate unchanged. If messaging fails, raise
        SessionMessageError with the created session ID and chained cause.
        No retries or cleanup are performed: a network error can leave message
        acceptance uncertain, so retrying automatically could duplicate it.
        """
        session = self.create_session(
            name, project_path, agent=agent, worktree_id=worktree_id, sandbox=sandbox
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
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read one page of persisted events; no waiting or output aggregation."""
        return self.get_scrollback(session_id, after=after, limit=limit)

    def list_agents(self) -> list[dict[str, Any]]:
        return self.request("GET", "/agents")

    def get_usage(self) -> dict[str, Any]:
        return self.request("GET", "/usage")

    def get_sandbox_paths(self) -> dict[str, Any]:
        return self.request("GET", "/sandbox-paths")

    def update_sandbox_paths(self, sandbox_paths: list[SandboxPath]) -> dict[str, Any]:
        return self.request(
            "PATCH", "/sandbox-paths", body={"sandbox_paths": sandbox_paths}
        )

    def list_projects(self) -> list[dict[str, Any]]:
        return self.request("GET", "/projects")

    def create_project(
        self,
        path: str,
        *,
        name: str | None = None,
        sandbox_paths: list[SandboxPath] | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/projects",
            body=_provided(path=path, name=name, sandbox_paths=sandbox_paths),
        )

    def update_project(
        self,
        path: str,
        *,
        archived: bool | None = None,
        sandbox_paths: list[SandboxPath] | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "PATCH",
            "/projects",
            body=_provided(path=path, archived=archived, sandbox_paths=sandbox_paths),
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
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/sessions",
            body=_provided(
                name=name,
                project_path=project_path,
                agent=agent,
                worktree_id=worktree_id,
                sandbox=sandbox,
            ),
        )

    def update_session(
        self,
        session_id: int,
        *,
        name: str | None = None,
        auto_approve_write: bool | None = None,
        auto_approve_command: bool | None = None,
        sandbox: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/sessions/{session_id}",
            body=_provided(
                name=name,
                auto_approve_write=auto_approve_write,
                auto_approve_command=auto_approve_command,
                sandbox=sandbox,
                archived=archived,
            ),
        )

    def detach_session_worktree(self, session_id: int) -> dict[str, Any]:
        return self.request("POST", f"/sessions/{session_id}/detach-worktree")

    def stop_session(self, session_id: int) -> dict[str, Any]:
        return self.request("POST", f"/sessions/{session_id}/stop")

    def delete_session(self, session_id: int) -> dict[str, Any]:
        return self.request("DELETE", f"/sessions/{session_id}")

    def get_scrollback(
        self,
        session_id: int,
        *,
        after: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read one page of persisted events, oldest first, exclusively after ID.

        Omit after to start from the beginning. Returns messages, next_cursor,
        and has_more unchanged. The server validates limit (1–1000).
        An empty page retains the supplied cursor; it does not imply completion.
        """
        return self.request(
            "GET",
            f"/sessions/{session_id}/scrollback",
            query=_provided(after=after, limit=limit),
        )

    def start_turn(self, session_id: int, prompt: str) -> dict[str, Any]:
        """Submit a prompt; acceptance does not mean the turn has completed."""
        return self.request(
            "POST", f"/sessions/{session_id}/turn", body={"prompt": prompt}
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
        return self.request("DELETE", f"/worktrees/{worktree_id}")
