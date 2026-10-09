# agent-ui-api

Thin, synchronous Python 3.11+ client for `agent-ui-server`, using `urllib` and
no runtime dependencies. Managed with `uv`.

## Usage

```python
from agent_ui_api import Client, request

# The caller supplies the URL and token; this package does not discover either.
client = Client(base_url, token=token)
agents = client.list_agents()
session = client.create_session("Review", "/path/to/project", agent="pi")
client.start_turn(session["id"], "Review the current changes.")

# The same transport is available independently.
sessions = request(base_url, "GET", "/sessions", token=token)
```

The project must already be registered before creating a session. Use
`create_project(path)` if necessary; this may create the directory on the server.

## Session abstractions

```python
from agent_ui_api import SessionMessageError

try:
    started = client.start_session(
        "Review", "/path/to/project", "Review the current changes.", agent="pi"
    )
except SessionMessageError as error:
    # Creation succeeded. The session remains; the original error is __cause__.
    session_id = error.session_id
    raise

page = client.read_session(started["session_id"], after=started["message_id"])
# Later: read again using page["next_cursor"].
message_id = client.message_session(started["session_id"], "Now review the tests.")
```

- `message_session(session_id, message)` returns the persisted input ID.
- `start_session(name, project_path, message, ...)` creates a session, then calls
  `message_session`; returns `session_id` and `message_id`. Projects must already
  exist. Creation failures propagate unchanged; messaging failures raise
  `SessionMessageError` carrying the created session ID and original cause.
- `read_session(session_id, after=None, limit=200)` returns one unchanged
  scrollback page. It does not wait for completion or combine output events.

No automatic retry or deletion occurs on partial success. A messaging timeout
can mean acceptance is unknown, not that the message was rejected. Further
messages remain subject to server busy/archive checks.

## Surface

- Agents and model catalogs: `list_agents`
- Images: `upload_image`, `download_image`
- Usage: `get_usage`
- Global sandbox paths: `get_sandbox_paths`, `update_sandbox_paths`
- Global sandbox TCP exceptions: `get_sandbox_network`, `update_sandbox_network`
- Shared assets: `list_shared_asset_roots`, `create_shared_asset_root`,
  `update_shared_asset_root`, `delete_shared_asset_root`
- Projects: `list_projects`, `create_project`, `update_project`, `delete_project`
- Sessions: `list_sessions`, `create_session`, `update_session`, `delete_session`,
  `detach_session_worktree`, `stop_session`, `start_turn`, `start_bash`,
  `get_scrollback`
- Worktrees: `list_worktrees`, `create_worktree`, `delete_worktree`
- Raw REST: `request` (standalone or on `Client`)

Wrappers return the server's JSON dictionaries/lists directly. Optional arguments
of `None` are omitted; `False` and empty lists are sent. Server defaults and
validation remain authoritative. Paths refer to the **server's** filesystem.

`start_turn` and `start_bash` return acceptance, not completion or output.
Their `message_id` identifies the persisted input and can seed a scrollback cursor:

```python
sent = client.start_turn(session["id"], "Review the current changes.")
cursor = sent["message_id"]
page = client.get_scrollback(session["id"], after=cursor, limit=200)
messages = page["messages"]
cursor = page["next_cursor"]  # Use as after on the next read.
```

`get_scrollback` returns one page of persisted events, ordered by ascending ID,
strictly after the cursor. Omit `after` to start from the beginning. The server
accepts limits of 1–1000 (default 200). `has_more` indicates another page is
available now; false does not mean the agent has finished. Empty pages preserve
the supplied cursor (or `None`). There is no automatic pagination or polling.

Live session events, WebSocket replay, file trees, approval responses, and
question answers remain outside this client's scope.
There is no separate agent-message endpoint: a prompt can be submitted to a
session using `start_turn`, subject to the server's busy/archive checks.

## Image attachments

```python
from pathlib import Path

image = client.upload_image(Path("screenshot.png").read_bytes(), "image/png")
sent = client.start_turn(session["id"], "Explain this screenshot", images=[image["id"]])
client.start_turn(session["id"], images=[image["id"]])  # Image-only input.
original_bytes = client.download_image(image["id"])
```

`upload_image(data: bytes, mime_type: str)` sends raw bytes to `POST /images`,
not JSON, base64, or multipart. It returns unchanged `Image` metadata:
`id`, `mime_type`, `size` (original bytes), `width`, and `height` (original pixels).
Supported MIME types are JPEG (`image/jpeg`), PNG, GIF, and WebP. The server
validates content and MIME agreement and enforces 10 MiB per upload (413 for
oversize, 415 for unsupported MIME, 400 for empty/invalid/mismatched content).
`download_image(id)` uses authenticated `GET /images/{id}` and returns original
bytes; unknown IDs return 404. Both use the same URL validation, timeout,
no-redirect/no-retry behavior and error propagation as JSON requests.

`start_turn(session_id, prompt="", images=None)` uses the existing HTTP turn
endpoint. It always sends `prompt`; `None` omits `images`, while `[]` sends an
empty list. `TurnRequest` describes the HTTP body. Meaningful text or images
are required. IDs are ordered and duplicates are preserved. Server acceptance
requires an image-capable session model, existing image IDs, at most 10 image
occurrences per message, and at most 20 MiB of image occurrences in the pending
turn batch (including repeated IDs and already queued inputs). Rejection does
not delete uploads; images are immutable and retained, even if unattached.

Accepted input, pending queue, shipment, scrollback and replay payloads contain
ordered `images` metadata objects, never image bytes/base64; text-only messages
omit the field. Sending uses ID strings, not metadata objects. WebSocket UIs
continue using their existing input operation with optional `images` IDs; this
client does not implement WebSocket transport or events.

## Model selection

```python
agents = client.list_agents()
pi = next(agent for agent in agents if agent["id"] == "pi")
if pi["models_error"] is None and pi["models"]:
    model = pi["models"][0]
    levels = model["reasoning_levels"]  # Harness vocabulary; may be empty.
    session = client.create_session(
        "Review",
        "/path/to/project",
        agent="pi",
        model=model["id"],
        reasoning_level=levels[-1] if levels else None,
    )
```

`list_agents` returns an ordered list of
`{"id", "name", "default", "models": [{"id", "name", "reasoning_levels", "input"}], "models_error"}`
(typed as `Agent` and `Model`). The server discovers catalogs once at startup;
there is no refresh. An agent whose discovery failed has empty `models` and a
`models_error` string; other agents are unaffected. A successful empty catalog
has `models_error` of `None`. Model IDs are opaque; show `name` and send `id`
unchanged. Each model has required `input: list[str]`; attachments are supported
when `"image" in model["input"]`. Catalogs contain no "default" entry. UI clients keep the server's
order, preselect the first entry, and always send an explicit ID; if discovery
failed they show the error and block session creation for that agent instead of
offering a fallback.

`create_session` and `start_session` accept `model`. `None` (the default) omits
it, so the server selects the first entry of the selected agent's catalog and
persists its ID. An ID not in that catalog raises `HTTPError` 400; discovery
failure returns 503. Omitted model also returns 503 if the catalog is empty.
New session objects include the selected `model` string; older sessions may
have `None`. The model cannot be changed after creation, so `update_session`
has no `model` argument.

## Reasoning levels

Each model's `reasoning_levels` is the harness's own vocabulary in the harness's
order (for example Pi: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`,
`max`). An empty list means the model has no reasoning choice. There is no
default-level field; send levels unchanged.

`create_session` and `start_session` accept `reasoning_level`. `None` omits it,
so the harness picks its own default level. A supplied level must be in the
selected model's `reasoning_levels`, including when `model` is omitted and the
first catalog entry is selected (`HTTPError` 400; 503 if model discovery is
unavailable). `update_session(session_id, reasoning_level=...)` changes it
from the next turn; the level must be in the session model's list (400
otherwise). `None` leaves it unchanged; a level cannot be cleared back to the
default.

Session objects include `reasoning_level` (string, or `None` for the harness
default; UIs show "Default"). After an update, the server broadcasts
`{"type": "reasoning_level", "reasoning_level": "..."}` on the session
WebSocket, which this client does not consume.

## Sandbox network exceptions

```python
settings = client.get_sandbox_network()
settings = client.update_sandbox_network([
    {"ip": "100.64.0.10", "port": 443},
    {"ip": "100.64.0.10", "port": 22},
])
# Use the server's returned list after saving.
allowlist = settings["sandbox_network_allowlist"]
client.update_sandbox_network([])  # Clear server-level exceptions only.

project = client.create_project("/server/project", sandbox_network_allowlist=[
    {"ip": "100.64.0.20", "port": 22},
])
project = client.update_project("/server/project", sandbox_network_allowlist=[])
# [] restores server inheritance; the returned list contains only project entries.
project_allowlist = project["sandbox_network_allowlist"]
```

`GET /sandbox-network` and `PATCH /sandbox-network` return
`{"sandbox_network_allowlist": [{"ip": "100.64.0.10", "port": 443}]}`
(`SandboxNetworkSettings`). PATCH sends a required replacement array
(`SandboxNetworkUpdate`); entries are typed as `SandboxNetworkDestination`.
Duplicates collapse in first-occurrence order on the server, not in the client.

Only exact unicast IPv4 literals and integer TCP ports 1–65535 are accepted:
no hostnames, CIDRs, IPv6, loopback, unspecified, reserved, multicast, or
`169.254.0.53` (the sandbox DNS proxy). Invalid IP/destination returns HTTP 400;
a missing/malformed array or invalid port returns 422. Invalid updates leave
settings unchanged; errors propagate as `HTTPError` without retries.

Each entry exposes only that TCP port, not other ports or UDP—even when Gitea
and agent-ui-server share an IP. `/sandbox-network` edits only the server scope
and uses normal server authentication. An empty server list means no server-level
exceptions; project exceptions may still apply.

`list_projects`, `create_project`, and `update_project` return typed `Project`
dictionaries (a list for `list_projects`). `ProjectCreate` and `ProjectUpdate`
describe request bodies. Project responses include `sandbox_network_allowlist`
for the project's own entries, never the effective union. Creation accepts an
optional list (omitted/`None` uses the server default `[]`); repeated creation
leaves existing settings unchanged. Update omission/`None` leaves the list
unchanged, a supplied list replaces it, and `[]` clears project entries back to
server inheritance. Validation and deduplication match the server scope; the
client sends entries unchanged and uses the returned list after saving.

Effective runtime exceptions are the deduplicated union of server and project
entries. Projects cannot remove inherited server exceptions. Worktree sessions
inherit their parent project's entries; there is no per-session list. Changes
apply to newly launched sandboxed turns for both agents; running turns keep
their rules. No WebSocket settings event is emitted.

## Shared assets

```python
root = client.create_shared_asset_root("notes", "/server/notes", project_id=42)
root = client.update_shared_asset_root("notes", new_asset_root="research")
root = client.update_shared_asset_root("research", project_id=None)  # Make global.
client.delete_shared_asset_root("research")  # Unregister; never delete files.
```

Responses are unchanged `SharedAssetRoot` dictionaries (`asset_root`, `path`,
`project_id`, `url`); list returns an array and delete returns `None` (204).
`SharedAssetRootCreate` and `SharedAssetRootUpdate` describe request bodies.
The server-relative `url` is preserved; clients open it against their configured
server address, not the local filesystem or UI origin.

Omitting `project_id` on create uses the server's global default. Omitting it on
update leaves the association unchanged; explicit `None` sends JSON null and
makes it global. IDs must be Python integers, not booleans, floats, or strings
(`TypeError` before sending); project existence is validated by the server.
Update name/path of `None` are omitted. Renaming sends `asset_root` and breaks
old links; route identifiers are encoded as single URL segments. Registration
never creates directories. Server errors (404, 409, 422) propagate unchanged.

## Transport behavior

- Sends `Authorization: Bearer <token>`; no token loading or persistence.
- Encodes JSON bodies and query parameters; an empty JSON response returns `None`.
  Raw transport options `raw_body`/`content_type` send bytes instead of JSON;
  `binary_response=True` returns bytes (including `b""`), without JSON decoding.
- Uses a 30-second socket timeout by default (configurable with `timeout`).
- Propagates `urllib.error.HTTPError` and `URLError`, timeout exceptions, and
  JSON decoding errors. `HTTPError.code`, `.headers`, and `.read()` retain the
  server's error response, including plain-text authentication failures.
- No retries, polling, or redirects (redirects raise `HTTPError`).
- No agent tools, approval orchestration, or sandbox bypass. Calls run with the
  caller's normal network permissions.

## Development

```sh
uv sync
uv run python -m unittest discover -s tests -v
uv build
```

Tests use a local HTTP server and mocks; no running agent server or real token
is needed.
