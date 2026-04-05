"""Tests for LLM docs Tornado handlers."""

import json

import tornado.testing
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.config import WoodglueConfig


class SomeInput(BaseModel):
    value: int


def some_method(input: SomeInput) -> int:
    """Do something with input.

    A longer explanation of what this does.
    """
    return input.value * 2


def _make_namespaces() -> dict[str, Namespace]:
    ns = Namespace()
    ns.register(some_method, nsref="some_method", tags=["api"])
    return {"demo": ns}


class TestLlmDocsHandlers(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        config = WoodglueConfig(namespaces={"demo": "unused"})
        return create_app(namespaces=_make_namespaces(), config=config)

    def test_llms_txt(self):
        resp = self.fetch("/docs/llms.txt")
        assert resp.code == 200
        body = resp.body.decode()
        assert "# Woodglue API" in body
        assert "[demo.some_method](/docs/methods/demo.some_method.md)" in body

    def test_method_markdown(self):
        resp = self.fetch("/docs/methods/demo.some_method.md")
        assert resp.code == 200
        body = resp.body.decode()
        assert "# demo.some_method" in body
        assert "## Parameters" in body
        assert "## Referenced Models" in body
        assert "### SomeInput" in body

    def test_method_markdown_not_found(self):
        resp = self.fetch("/docs/methods/demo.nonexistent.md")
        assert resp.code == 404

    def test_openapi_json(self):
        resp = self.fetch("/docs/openapi.json")
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["openapi"] == "3.0.3"
        assert "/rpc/demo.some_method" in data["paths"]
