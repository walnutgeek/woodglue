"""Tests for woodglue.apps.rpc.JsonRpcHandler."""

import json
from typing import Any

import tornado.testing
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel as PydanticBaseModel
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.hello import pydantic_hello


class Inner(PydanticBaseModel):
    value: int


class Outer(PydanticBaseModel):
    inner: Inner
    label: str


def sync_add(a: int, b: int) -> dict[str, int]:
    """Add two numbers and return a dict."""
    return {"sum": a + b}


async def async_greet(name: str) -> str:
    """Greet someone asynchronously."""
    return f"Hello, {name}!"


def nested_output(x: int) -> Outer:
    """Return nested BaseModel."""
    return Outer(inner=Inner(value=x), label=f"item-{x}")


def _make_namespace() -> Namespace:
    ns = Namespace()
    ns.register(sync_add, nsref="sync_add")
    ns.register(async_greet, nsref="async_greet")
    return ns


def _rpc_body(method: str, params: Any = None, request_id: int | None = 1) -> str:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if request_id is not None:
        body["id"] = request_id
    return json.dumps(body)


class TestJsonRpc(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        ns = _make_namespace()
        return create_app(namespaces={"test": ns})

    def test_sync_function_call(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.sync_add", {"a": 3, "b": 4}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["result"] == {"sum": 7}
        assert data["id"] == 1

    def test_async_function_call(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.async_greet", {"name": "World"}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["result"] == "Hello, World!"
        assert data["id"] == 1

    def test_method_not_found(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.nonexistent", {}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32601

    def test_parse_error_bad_json(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body="not json at all{{{",
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32700

    def test_invalid_request_missing_method(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 1}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32600

    def test_missing_required_param(self):
        # sync_add requires both a and b
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.sync_add", {"a": 1}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32602
        assert "b" in data["error"]["message"]

    def test_positional_params(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.sync_add", [10, 20]),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["result"] == {"sum": 30}

    def test_notification_no_id(self):
        body = json.dumps({"jsonrpc": "2.0", "method": "test.sync_add", "params": {"a": 1, "b": 2}})
        resp = self.fetch("/rpc", method="POST", body=body)
        assert resp.code == 200
        data = json.loads(resp.body)
        # Should still return a result; id will be None
        assert data["result"] == {"sum": 3}
        assert data["id"] is None


def _make_multi_namespace() -> dict[str, Namespace]:
    ns1 = Namespace()
    ns1.register(sync_add, nsref="sync_add")
    ns1.register(async_greet, nsref="async_greet")

    ns2 = Namespace()
    ns2.register(pydantic_hello, nsref="pydantic_hello")
    ns2.register(nested_output, nsref="nested_output")

    return {"test": ns1, "hello": ns2}


class TestMultiNamespaceRpc(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        namespaces = _make_multi_namespace()
        return create_app(namespaces=namespaces)

    def test_call_method_in_first_namespace(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("test.sync_add", {"a": 5, "b": 3}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["result"] == {"sum": 8}

    def test_call_method_in_second_namespace(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("hello.pydantic_hello", {"input": {"name": "Alice", "age": 30}}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["result"] == {"eman": "ecilA", "ega": -30}

    def test_method_without_prefix_returns_not_found(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("sync_add", {"a": 1, "b": 2}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32601

    def test_nested_basemodel_output(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("hello.nested_output", {"x": 42}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["result"] == {"inner": {"value": 42}, "label": "item-42"}

    def test_unknown_prefix_returns_not_found(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("bogus.sync_add", {"a": 1, "b": 2}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32601
