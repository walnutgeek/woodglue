"""Tests for woodglue.apps.docs (DocsHandler and DocsUiHandler)."""

import json

import tornado.testing
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.apps.server import create_app


def multiply(x: int, y: int) -> int:
    """Multiply two integers."""
    return x * y


async def echo(message: str) -> str:
    """Echo back the message."""
    return message


def _make_namespace() -> Namespace:
    ns = Namespace()
    ns.register(multiply, nsref="math:multiply")
    ns.register(echo, nsref="util:echo")
    return ns


class TestDocs(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        return create_app(namespace=_make_namespace())

    def test_docs_returns_valid_openapi_json(self):
        resp = self.fetch("/docs")
        assert resp.code == 200
        data = json.loads(resp.body)
        assert "openapi" in data
        assert data["openapi"] == "3.0.3"
        assert "paths" in data

    def test_docs_contains_registered_methods(self):
        resp = self.fetch("/docs")
        data = json.loads(resp.body)
        paths = data["paths"]
        assert "/rpc/math:multiply" in paths
        assert "/rpc/util:echo" in paths

    def test_docs_method_has_parameters(self):
        resp = self.fetch("/docs")
        data = json.loads(resp.body)
        multiply_op = data["paths"]["/rpc/math:multiply"]["post"]
        schema = multiply_op["requestBody"]["content"]["application/json"]["schema"]
        assert "x" in schema["properties"]
        assert "y" in schema["properties"]
        assert schema["properties"]["x"]["type"] == "integer"

    def test_docs_ui_returns_html(self):
        resp = self.fetch("/docs/ui")
        assert resp.code == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/html" in content_type

    def test_docs_ui_contains_method_names(self):
        resp = self.fetch("/docs/ui")
        body = resp.body.decode("utf-8")
        assert "math:multiply" in body
        assert "util:echo" in body
