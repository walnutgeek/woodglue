"""Tests for auth middleware on RPC and docs endpoints."""

import json
import tempfile
from pathlib import Path
from typing import Any

import tornado.testing
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.config import AuthConfig, WoodglueConfig, WoodglueStorageConfig
from woodglue.hello import hello, pydantic_hello
from woodglue.token_store import ensure_token


def _make_namespaces() -> dict[str, Namespace]:
    ns = Namespace()
    ns.register(hello, nsref="hello", tags=["api"])
    ns.register(pydantic_hello, nsref="pydantic_hello", tags=["api"])
    return {"test": ns}


def _rpc_body(method: str, params: dict[str, Any] | None = None) -> str:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params is not None:
        body["params"] = params
    return json.dumps(body)


class TestAuthEnabled(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    _db_path: Path  # pyright: ignore[reportUninitializedInstanceVariable]
    _token: str | None  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "auth.db"
        self._token = ensure_token(self._db_path)
        assert self._token is not None
        super().setUp()

    @override
    def tearDown(self):
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self):
        config = WoodglueConfig(
            namespaces={"test": "unused"},
            auth=AuthConfig(enabled=True),
            storage=WoodglueStorageConfig(auth_db=self._db_path),
        )
        return create_app(namespaces=_make_namespaces(), config=config)

    def test_rpc_without_token_returns_error(self):
        resp = self.fetch("/rpc", method="POST", body=_rpc_body("test.hello", {"name": "hi"}))
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32000

    def test_rpc_with_valid_token(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.hello", {"name": "hi"}),
            headers={"Authorization": f"Bearer {self._token}"},
        )
        data = json.loads(resp.body)
        assert "result" in data
        assert data["result"] == 2

    def test_rpc_with_bad_token(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.hello", {"name": "hi"}),
            headers={"Authorization": "Bearer bad-token"},
        )
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32000

    def test_docs_without_token_returns_401(self):
        resp = self.fetch("/docs/llms.txt")
        assert resp.code == 401

    def test_docs_with_header_token(self):
        resp = self.fetch(
            "/docs/llms.txt",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        assert resp.code == 200

    def test_docs_with_query_token(self):
        resp = self.fetch(f"/docs/llms.txt?token={self._token}")
        assert resp.code == 200

    def test_docs_with_bad_query_token(self):
        resp = self.fetch("/docs/llms.txt?token=bad-token")
        assert resp.code == 401


class TestAuthDisabled(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        config = WoodglueConfig(
            namespaces={"test": "unused"},
            auth=AuthConfig(enabled=False),
        )
        return create_app(namespaces=_make_namespaces(), config=config)

    def test_rpc_without_token_works(self):
        resp = self.fetch("/rpc", method="POST", body=_rpc_body("test.hello", {"name": "hi"}))
        data = json.loads(resp.body)
        assert "result" in data

    def test_docs_without_token_works(self):
        resp = self.fetch("/docs/llms.txt")
        assert resp.code == 200
