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

- Agents: `list_agents`
- Models: `list_models`
- Usage: `get_usage`
- Global sandbox paths: `get_sandbox_paths`, `update_sandbox_paths`
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

## Model selection

```python
catalog = client.list_models()  # ModelCatalog, keyed by agent ID
pi = catalog["pi"]
if pi["error"] is None and pi["models"]:
    session = client.create_session(
        "Review", "/path/to/project", agent="pi", model=pi["models"][0]["id"]
    )
```

`list_models` returns `{agent_id: {"models": [{"id", "name"}], "error"}}`
(typed as `ModelCatalog`, `HarnessModels`, and `Model`). The server discovers
catalogs once at startup; there is no refresh. A harness whose discovery failed
has empty `models` and an `error` string; other harnesses are unaffected.
Model IDs are opaque; show `name` and send `id` unchanged. Catalogs contain no
"default" entry. UI clients keep the server's order, preselect the first entry,
and always send an explicit ID; if discovery failed they show the error and
block session creation for that harness instead of offering a fallback.

`create_session` and `start_session` accept `model`. `None` (the default) omits
it, so the server uses the harness default; this remains for compatibility and
may become unsupported if the server makes `model` required. An ID not in the
selected agent's catalog raises `HTTPError` 400; if that harness's discovery failed, 503.
Session objects include `model` (string, or `None` for the default and for
sessions created before model selection). The model cannot be changed after
creation, so `update_session` has no `model` argument.

## Transport behavior

- Sends `Authorization: Bearer <token>`; no token loading or persistence.
- Encodes JSON bodies and query parameters; an empty response returns `None`.
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
