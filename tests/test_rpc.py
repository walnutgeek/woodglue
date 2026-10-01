"""Tests for woodglue.apps.rpc.JsonRpcHandler."""

import json
import logging
from typing import Any

import tornado.testing
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel as PydanticBaseModel
from typing_extensions import override

from woodglue.apps.rpc import RpcError
from woodglue.apps.server import create_app
from woodglue.config import NamespaceEntry
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
    ns.register(sync_add, nsref="sync_add", tags=["api"])
    ns.register(async_greet, nsref="async_greet", tags=["api"])
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
        return create_app(namespaces={"test": (ns, NamespaceEntry(gref="test"))})

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


def _make_multi_namespace() -> dict[str, tuple[Namespace, NamespaceEntry]]:
    ns1 = Namespace()
    ns1.register(sync_add, nsref="sync_add", tags=["api"])
    ns1.register(async_greet, nsref="async_greet", tags=["api"])

    ns2 = Namespace()
    ns2.register(pydantic_hello, nsref="pydantic_hello", tags=["api"])
    ns2.register(nested_output, nsref="nested_output", tags=["api"])

    return {
        "test": (ns1, NamespaceEntry(gref="test")),
        "hello": (ns2, NamespaceEntry(gref="hello")),
    }


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
        result = data["result"]
        assert result["eman"] == "ecilA"
        assert result["ega"] == -30

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


class NotFoundError(RpcError):
    code: int = -32001


def raises_subclass(name: str) -> str:
    raise NotFoundError(f"No such name: {name}")


def raises_with_data(with_data: bool) -> str:
    raise RpcError(-32010, "x", data={"k": 1} if with_data else None)


def raises_secret() -> str:
    raise ValueError("secret")


class CodedError(Exception):
    """Not an `RpcError`, but has an int `code` like `urllib.error.HTTPError`."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code: int = 404


def raises_coded() -> str:
    raise CodedError("leaky internals")


def _make_error_namespace() -> Namespace:
    ns = Namespace()
    for fn in (raises_subclass, raises_with_data, raises_secret, raises_coded):
        ns.register(fn, nsref=fn.__name__, tags=["api"])
    return ns


class TestRpcErrors(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        ns = _make_error_namespace()
        return create_app(namespaces={"err": (ns, NamespaceEntry(gref="err"))})

    def _call(self, method: str, params: Any) -> dict[str, Any]:
        resp = self.fetch("/rpc", method="POST", body=_rpc_body(method, params))
        assert resp.code == 200
        return json.loads(resp.body)

    def test_rpc_error_subclass_with_class_code(self):
        with self.assertLogs("woodglue.apps.rpc", level="WARNING") as logs:
            data = self._call("err.raises_subclass", {"name": "bob"})
        assert data["error"] == {"code": -32001, "message": "No such name: bob"}
        assert data["id"] == 1
        assert all(r.exc_info is None for r in logs.records)
        assert all(r.levelno == logging.WARNING for r in logs.records)
        assert "err.raises_subclass" in logs.output[0]
        assert "-32001" in logs.output[0]

    def test_rpc_error_data_included_when_set(self):
        data = self._call("err.raises_with_data", {"with_data": True})
        assert data["error"] == {"code": -32010, "message": "x", "data": {"k": 1}}

    def test_rpc_error_data_absent_when_none(self):
        data = self._call("err.raises_with_data", {"with_data": False})
        assert data["error"] == {"code": -32010, "message": "x"}

    def test_other_exception_is_opaque_internal_error(self):
        with self.assertLogs("woodglue.apps.rpc", level="ERROR") as logs:
            resp = self.fetch("/rpc", method="POST", body=_rpc_body("err.raises_secret", {}))
        assert b"secret" not in resp.body
        assert json.loads(resp.body)["error"] == {"code": -32603, "message": "Internal error"}
        assert logs.records[0].exc_info is not None

    def test_exception_with_int_code_attr_is_internal_error(self):
        with self.assertLogs("woodglue.apps.rpc", level="ERROR"):
            resp = self.fetch("/rpc", method="POST", body=_rpc_body("err.raises_coded", {}))
        assert b"leaky" not in resp.body
        assert json.loads(resp.body)["error"] == {"code": -32603, "message": "Internal error"}
