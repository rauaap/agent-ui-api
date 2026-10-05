import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from agent_ui_api import (
    Client,
    SharedAssetRoot,
    SharedAssetRootCreate,
    SharedAssetRootUpdate,
)


class SharedAssetTests(unittest.TestCase):
    def setUp(self):
        self.client = Client("https://server/prefix/", "secret", timeout=5)
        self.root = {
            "asset_root": "research",
            "path": "/server/research",
            "project_id": None,
            "url": "/shared-assets/research/",
        }

    def test_contract_types(self):
        self.assertEqual(
            SharedAssetRoot.__required_keys__, {"asset_root", "path", "project_id", "url"}
        )
        self.assertEqual(SharedAssetRootCreate.__required_keys__, {"asset_root", "path"})
        self.assertEqual(SharedAssetRootCreate.__optional_keys__, {"project_id"})
        self.assertEqual(SharedAssetRootUpdate.__required_keys__, set())
        self.assertEqual(
            SharedAssetRootUpdate.__optional_keys__, {"asset_root", "path", "project_id"}
        )

    def test_routes_payloads_and_unchanged_relative_urls(self):
        cases = [
            ("list_shared_asset_roots", (), {}, "GET", "/shared-asset-roots", None, [self.root]),
            ("create_shared_asset_root", ("notes", "/server/notes"), {}, "POST",
             "/shared-asset-roots", {"asset_root": "notes", "path": "/server/notes"}, self.root),
            ("update_shared_asset_root", ("notes",), {}, "PATCH",
             "/shared-asset-roots/notes", {}, self.root),
            ("update_shared_asset_root", ("notes",), {"new_asset_root": "research", "path": "/server/research", "project_id": 42}, "PATCH",
             "/shared-asset-roots/notes", {"asset_root": "research", "path": "/server/research", "project_id": 42}, self.root),
            ("update_shared_asset_root", ("notes",), {"new_asset_root": None, "path": None}, "PATCH",
             "/shared-asset-roots/notes", {}, self.root),
            ("update_shared_asset_root", ("notes",), {"new_asset_root": "", "path": ""}, "PATCH",
             "/shared-asset-roots/notes", {"asset_root": "", "path": ""}, self.root),
            ("delete_shared_asset_root", ("notes",), {}, "DELETE",
             "/shared-asset-roots/notes", None, None),
        ]
        for name, args, kwargs, method, path, body, response in cases:
            with self.subTest(name=name, kwargs=kwargs), patch(
                "agent_ui_api.client.request", return_value=response
            ) as transport:
                self.assertIs(getattr(self.client, name)(*args, **kwargs), response)
                transport.assert_called_once_with(
                    "https://server/prefix/", method, path,
                    token="secret", body=body, query=None, timeout=5,
                )

    def test_explicit_project_null_and_integer_are_preserved(self):
        for project_id in (None, 0, 42):
            for name, args in (
                ("create_shared_asset_root", ("notes", "/notes")),
                ("update_shared_asset_root", ("notes",)),
            ):
                with self.subTest(name=name, project_id=project_id), patch(
                    "agent_ui_api.client.request"
                ) as transport:
                    getattr(self.client, name)(*args, project_id=project_id)
                    body = transport.call_args.kwargs["body"]
                    self.assertIn("project_id", body)
                    self.assertIs(body["project_id"], project_id)

    def test_project_ids_are_not_coerced(self):
        for value in (True, False, 42.0, "42", [], {}):
            for name, args in (
                ("create_shared_asset_root", ("notes", "/notes")),
                ("update_shared_asset_root", ("notes",)),
            ):
                with self.subTest(value=value, name=name), patch(
                    "agent_ui_api.client.request"
                ) as transport:
                    with self.assertRaises(TypeError):
                        getattr(self.client, name)(*args, project_id=value)
                    transport.assert_not_called()

    def test_identifier_is_one_encoded_segment(self):
        for method, verb in ((self.client.update_shared_asset_root, "PATCH"),
                             (self.client.delete_shared_asset_root, "DELETE")):
            with patch("agent_ui_api.client.request") as transport:
                method("a/b ?#%ü")
                self.assertEqual(transport.call_args.args[1:3], (
                    verb, "/shared-asset-roots/a%2Fb%20%3F%23%25%C3%BC"
                ))

    def test_errors_propagate_without_retry(self):
        for status in (401, 404, 409, 422):
            error = HTTPError("https://server/shared-asset-roots", status, "invalid", {}, None)
            for method, args in (
                (self.client.list_shared_asset_roots, ()),
                (self.client.create_shared_asset_root, ("notes", "/notes")),
                (self.client.update_shared_asset_root, ("notes",)),
                (self.client.delete_shared_asset_root, ("notes",)),
            ):
                with self.subTest(status=status, method=method.__name__), patch(
                    "agent_ui_api.client.request", side_effect=error
                ) as transport:
                    with self.assertRaises(HTTPError) as caught:
                        method(*args)
                    self.assertIs(caught.exception, error)
                    transport.assert_called_once()


if __name__ == "__main__":
    unittest.main()
