import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from agent_ui_api import Client, request


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls += 1
        path = urlsplit(self.path).path
        status, body = 200, b""
        if path == "/error":
            status, body = 401, b"Missing or invalid token"
        elif path == "/invalid":
            body = b"not json"
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/echo")
            self.end_headers()
            return
        elif path == "/empty":
            status = 204
        else:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.dumps(
                {
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "accept": self.headers.get("Accept"),
                    "body": json.loads(raw) if raw else None,
                }
            ).encode()
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PATCH = do_DELETE = do_GET


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.calls = 0
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def call(self, path, **kwargs):
        return request(self.url + "/", "GET", path, token="test-token", **kwargs)

    def test_headers_query_and_empty_get(self):
        result = self.call(
            "/echo", query={"path": "/a b/+&ü", "omit": None, "n": [1, 2]}
        )
        self.assertEqual(result["authorization"], "Bearer test-token")
        self.assertEqual(result["accept"], "application/json")
        self.assertIsNone(result["content_type"])
        self.assertIsNone(result["body"])
        self.assertEqual(
            parse_qs(urlsplit(result["path"]).query),
            {"path": ["/a b/+&ü"], "n": ["1", "2"]},
        )

    def test_json_bodies(self):
        for method in ("POST", "PATCH", "DELETE"):
            with self.subTest(method=method):
                result = request(
                    self.url,
                    method,
                    "/echo",
                    token="t",
                    body={"prompt": "héllo", "sandbox": False},
                )
                self.assertEqual(result["method"], method)
                self.assertEqual(result["content_type"], "application/json")
                self.assertEqual(result["body"], {"prompt": "héllo", "sandbox": False})
        self.assertEqual(self.call("/echo", body={})["body"], {})

    def test_empty_response(self):
        self.assertIsNone(self.call("/empty"))

    def test_http_error_preserves_plain_text_body(self):
        with self.assertRaises(HTTPError) as caught:
            self.call("/error")
        with caught.exception as error:
            self.assertEqual(error.code, 401)
            self.assertEqual(error.read(), b"Missing or invalid token")

    def test_invalid_json(self):
        with self.assertRaises(json.JSONDecodeError):
            self.call("/invalid")

    def test_redirect_not_followed(self):
        before = self.server.calls
        with self.assertRaises(HTTPError) as caught:
            self.call("/redirect")
        caught.exception.close()
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(self.server.calls - before, 1)

    def test_invalid_urls_rejected(self):
        for url in (
            "file:///etc/passwd",
            "http://user:pass@example.com",
            "http://example.com?x=1",
            "http://example.com/#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                request(url, "GET", "/agents", token="t")
        for path in (
            "https://example.com",
            "//example.com",
            "agents",
            "/agents?x=1",
            "/a#b",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call(path)

    def test_timeout_and_network_errors_propagate_without_retry(self):
        for error in (TimeoutError("timed out"), URLError("unreachable")):
            with patch("agent_ui_api.request.build_opener") as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(type(error)):
                    self.call("/echo", timeout=1.25)
                build.return_value.open.assert_called_once()
                self.assertEqual(
                    build.return_value.open.call_args.kwargs["timeout"], 1.25
                )


class ClientTests(unittest.TestCase):
    def test_all_routes(self):
        cases = [
            ("list_agents", (), {}, "GET", "/agents", None, None),
            ("get_usage", (), {}, "GET", "/usage", None, None),
            ("get_sandbox_paths", (), {}, "GET", "/sandbox-paths", None, None),
            (
                "update_sandbox_paths",
                ([],),
                {},
                "PATCH",
                "/sandbox-paths",
                {"sandbox_paths": []},
                None,
            ),
            ("list_projects", (), {}, "GET", "/projects", None, None),
            ("create_project", ("/p",), {}, "POST", "/projects", {"path": "/p"}, None),
            (
                "update_project",
                ("/p",),
                {"archived": False, "sandbox_paths": []},
                "PATCH",
                "/projects",
                {"path": "/p", "archived": False, "sandbox_paths": []},
                None,
            ),
            (
                "delete_project",
                ("/p",),
                {},
                "DELETE",
                "/projects",
                {"path": "/p"},
                None,
            ),
            ("list_sessions", (), {}, "GET", "/sessions", None, None),
            (
                "create_session",
                ("name", "/p"),
                {},
                "POST",
                "/sessions",
                {"name": "name", "project_path": "/p"},
                None,
            ),
            (
                "create_session",
                ("name", "/p"),
                {"agent": "pi", "worktree_id": 2, "sandbox": False},
                "POST",
                "/sessions",
                {
                    "name": "name",
                    "project_path": "/p",
                    "agent": "pi",
                    "worktree_id": 2,
                    "sandbox": False,
                },
                None,
            ),
            (
                "update_session",
                (1,),
                {
                    "name": "new",
                    "auto_approve_write": False,
                    "auto_approve_command": False,
                    "sandbox": True,
                    "archived": False,
                },
                "PATCH",
                "/sessions/1",
                {
                    "name": "new",
                    "auto_approve_write": False,
                    "auto_approve_command": False,
                    "sandbox": True,
                    "archived": False,
                },
                None,
            ),
            (
                "detach_session_worktree",
                (1,),
                {},
                "POST",
                "/sessions/1/detach-worktree",
                None,
                None,
            ),
            ("stop_session", (1,), {}, "POST", "/sessions/1/stop", None, None),
            ("delete_session", (1,), {}, "DELETE", "/sessions/1", None, None),
            (
                "start_turn",
                (1, "hello"),
                {},
                "POST",
                "/sessions/1/turn",
                {"prompt": "hello"},
                None,
            ),
            (
                "start_bash",
                (1, "pwd"),
                {},
                "POST",
                "/sessions/1/bash",
                {"command": "pwd"},
                None,
            ),
            ("list_worktrees", (), {}, "GET", "/worktrees", None, {}),
            (
                "list_worktrees",
                ("/p",),
                {},
                "GET",
                "/worktrees",
                None,
                {"project_path": "/p"},
            ),
            (
                "create_worktree",
                ("/p", "/w", "branch"),
                {},
                "POST",
                "/worktrees",
                {"project_path": "/p", "path": "/w", "branch": "branch"},
                None,
            ),
            ("delete_worktree", (2,), {}, "DELETE", "/worktrees/2", None, None),
        ]
        client = Client("http://server", "secret", timeout=5)
        for name, args, kwargs, method, path, body, query in cases:
            with (
                self.subTest(name=name),
                patch("agent_ui_api.client.request") as transport,
            ):
                self.assertIs(
                    getattr(client, name)(*args, **kwargs), transport.return_value
                )
                transport.assert_called_once_with(
                    "http://server",
                    method,
                    path,
                    token="secret",
                    body=body,
                    query=query,
                    timeout=5,
                )

    def test_get_scrollback(self):
        client = Client("http://server", "secret")
        for after, limit in ((None, 200), (0, 1), (123, 1000)):
            for messages in (
                [],
                [{"id": 124, "type": "output", "payload": {"text": "hi"}}],
            ):
                page = {
                    "messages": messages,
                    "next_cursor": messages[-1]["id"] if messages else after,
                    "has_more": bool(messages),
                }
                with (
                    self.subTest(after=after, messages=messages),
                    patch(
                        "agent_ui_api.client.request", return_value=page
                    ) as transport,
                ):
                    if after is None:
                        result = client.get_scrollback(7)
                    else:
                        result = client.get_scrollback(7, after=after, limit=limit)
                    self.assertIs(result, page)
                    query = {"limit": limit}
                    if after is not None:
                        query["after"] = after
                    transport.assert_called_once_with(
                        "http://server",
                        "GET",
                        "/sessions/7/scrollback",
                        token="secret",
                        body=None,
                        query=query,
                        timeout=30.0,
                    )

    def test_send_returns_message_id(self):
        client = Client("http://server", "secret")
        for method in (client.start_turn, client.start_bash):
            with patch(
                "agent_ui_api.client.request",
                return_value={
                    "status": "running",
                    "message_id": 123,
                },
            ):
                self.assertEqual(method(7, "hello")["message_id"], 123)

    def test_token_not_in_repr(self):
        self.assertNotIn("secret", repr(Client("http://server", "secret")))


if __name__ == "__main__":
    unittest.main()
