"""Small synchronous JSON transport; urllib exceptions are left intact."""

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials or replay mutations at a different URL.
        return None


def request(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str,
    body: Any = None,
    query: Mapping[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    """Send one request and return decoded JSON (None for an empty body).

    ``path`` is an absolute API path, not a URL. Query values of None are
    omitted; sequences are encoded as repeated parameters. ``body=None``
    sends no body. HTTPError (including redirects), URLError, timeout, and
    JSON decoding errors propagate unchanged. No retries are performed.
    """
    base = urlsplit(base_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.netloc
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
    ):
        raise ValueError(
            "base_url must be an HTTP(S) URL without credentials, query or fragment"
        )
    endpoint = urlsplit(path)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("path must be an absolute API path without query or fragment")
    url = base_url.rstrip("/") + path
    if query:
        encoded = urlencode(
            {k: v for k, v in query.items() if v is not None}, doseq=True
        )
        if encoded:
            url += "?" + encoded
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method.upper())
    with build_opener(_NoRedirects()).open(req, timeout=timeout) as response:
        content = response.read()
    return json.loads(content) if content else None
