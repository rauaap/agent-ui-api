import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from agent_ui_api import Client, Image, Model, TurnRequest, request


DATA = b"\x89PNG\r\n\x1a\n\x00\xfforiginal bytes"
METADATA = {"id": "image-1", "mime_type": "image/png", "size": len(DATA),
            "width": 800, "height": 600}


class ImageHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.calls.append((self.command, self.path, dict(self.headers),
                                  self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(METADATA).encode())

    def do_GET(self):
        self.server.calls.append((self.command, self.path, dict(self.headers), None))
        if self.path.endswith("redirect"):
            self.send_response(302)
            self.send_header("Location", "/images/image-1")
        elif self.path.endswith("missing"):
            self.send_response(404)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
        self.end_headers()
        if self.path.endswith("missing"):
            self.wfile.write(b"unknown image")
        elif not self.path.endswith(("redirect", "empty")):
            self.wfile.write(DATA)


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ImageHandler)
        cls.server.calls = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = Client(f"http://127.0.0.1:{cls.server.server_port}", "secret", 2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_upload_download_original_bytes_and_auth(self):
        self.assertEqual(self.client.upload_image(DATA, "image/png"), METADATA)
        method, path, headers, body = self.server.calls[-1]
        self.assertEqual((method, path, body), ("POST", "/images", DATA))
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(self.client.download_image("image-1"), DATA)
        self.assertEqual(self.server.calls[-1][2]["Authorization"], "Bearer secret")
        self.assertEqual(self.client.download_image("empty"), b"")

    def test_download_encoded_identifier(self):
        self.client.download_image("a/b ?#%ü")
        self.assertEqual(self.server.calls[-1][1], "/images/a%2Fb%20%3F%23%25%C3%BC")

    def test_errors_and_redirects_not_retried(self):
        for image_id, status in (("missing", 404), ("redirect", 302)):
            before = len(self.server.calls)
            with self.assertRaises(HTTPError) as caught:
                self.client.download_image(image_id)
            with caught.exception as error:
                self.assertEqual(error.code, status)
                if status == 404:
                    self.assertEqual(error.read(), b"unknown image")
            self.assertEqual(len(self.server.calls) - before, 1)

    def test_binary_transport_uses_url_validation(self):
        for kwargs in ({"raw_body": DATA, "content_type": "image/png"},
                       {"binary_response": True}):
            with self.assertRaises(ValueError):
                request("file:///etc/passwd", "POST", "/images", token="t", **kwargs)
            with self.assertRaises(ValueError):
                request(self.client.base_url, "GET", "//evil/images", token="t", **kwargs)

    def test_binary_transport_network_errors_and_timeout(self):
        for operation in (lambda: self.client.upload_image(DATA, "image/png"),
                          lambda: self.client.download_image("image-1")):
            for error in (TimeoutError("timeout"), URLError("unreachable")):
                with patch("agent_ui_api.request.build_opener") as build:
                    build.return_value.open.side_effect = error
                    with self.assertRaises(type(error)):
                        operation()
                    build.return_value.open.assert_called_once()
                    self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 2)

    def test_raw_body_contract(self):
        for kwargs, error in (({"body": {}, "raw_body": DATA, "content_type": "image/png"}, ValueError),
                              ({"raw_body": "not bytes", "content_type": "image/png"}, TypeError),
                              ({"raw_body": DATA}, ValueError),
                              ({"content_type": "image/png"}, ValueError)):
            with self.assertRaises(error):
                request(self.client.base_url, "POST", "/images", token="t", **kwargs)
        self.client.upload_image(b"", "image/png")
        self.assertEqual(self.server.calls[-1][3], b"")

    def test_turn_images_order_and_prompt(self):
        cases = [((), {}, {"prompt": ""}),
                 (("",), {"images": ["b", "a", "b"]},
                  {"prompt": "", "images": ["b", "a", "b"]}),
                 (("hello",), {"images": []}, {"prompt": "hello", "images": []}),
                 (("hello",), {"images": None}, {"prompt": "hello"})]
        for args, kwargs, body in cases:
            with patch.object(self.client, "request", return_value={"message_id": 123}) as call:
                self.assertEqual(self.client.start_turn(7, *args, **kwargs), {"message_id": 123})
                call.assert_called_once_with("POST", "/sessions/7/turn", body=body)

    def test_shared_types(self):
        self.assertEqual(Image.__required_keys__, {"id", "mime_type", "size", "width", "height"})
        self.assertIn("input", Model.__required_keys__)
        self.assertEqual(TurnRequest.__optional_keys__, {"prompt", "images"})
        agents = [{"models": [{"input": ["text", "image"]}]}]
        with patch.object(self.client, "request", return_value=agents):
            self.assertIs(self.client.list_agents(), agents)


if __name__ == "__main__":
    unittest.main()
