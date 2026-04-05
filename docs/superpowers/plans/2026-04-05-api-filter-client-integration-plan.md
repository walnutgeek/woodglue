# API Filter, Client, and Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix api tag filtering across all four surfaces (RPC, llms.txt, markdown, OpenAPI), add YAML namespace config loading, embed `x-global-ref` in OpenAPI, build an async RPC client, and tie everything together with an integration test.

**Architecture:** `generate_llms_txt` and `generate_openapi_spec` are refactored to accept `method_index` (the api-filtered dict) instead of raw `namespaces`. A new `WoodglueClient` in `woodglue.client` uses Tornado's `AsyncHTTPClient` to make typed JSON-RPC calls, resolving return types from `x-global-ref` in the OpenAPI spec. Integration test uses YAML namespace configs to verify all surfaces agree on what's published.

**Tech Stack:** Python 3.11+, Pydantic, pydantic-yaml, Tornado, lythonic (NamespaceConfig)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/woodglue/apps/llm_docs.py` | Modify: `generate_llms_txt` and `generate_openapi_spec` take `method_index`; handlers use `method_index`; `_python_type_to_schema` adds `x-global-ref` |
| `src/woodglue/cli.py` | Modify: `load_namespaces` handles `.yaml`/`.yml` values via `NamespaceConfig` |
| `src/woodglue/client.py` | Create: `WoodglueClient`, `WoodglueRpcError` |
| `tests/test_llm_docs.py` | Modify: update calls to use `method_index` |
| `tests/test_llm_docs_handlers.py` | Minimal changes (handlers already use `method_index` for some) |
| `tests/test_integration.py` | Create: full integration test with YAML configs |

---

### Task 1: Fix `generate_llms_txt` to use `method_index`

**Files:**
- Modify: `src/woodglue/apps/llm_docs.py`
- Modify: `tests/test_llm_docs.py`

- [ ] **Step 1: Update test to pass `method_index` to `generate_llms_txt`**

In `tests/test_llm_docs.py`, change the two tests that call `generate_llms_txt`:

```python
def test_generate_llms_txt():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    txt = generate_llms_txt(index)
    assert "# Woodglue API" in txt
    assert (
        "- [items.create_item](/docs/methods/items.create_item.md): Create an item from input."
        in txt
    )
    assert "- [math.simple_add](/docs/methods/math.simple_add.md): Add two numbers." in txt


def test_generate_llms_txt_no_docstring():
    """Methods without docstrings use the qualified name as teaser."""
    ns = Namespace()
    ns.register(_identity, nsref="identity", tags=["api"])
    index = build_method_index({"misc": ns})
    txt = generate_llms_txt(index)
    assert "- [misc.identity](/docs/methods/misc.identity.md)" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_docs.py::test_generate_llms_txt -v`
Expected: FAIL — `generate_llms_txt` doesn't accept `method_index` type

- [ ] **Step 3: Change `generate_llms_txt` signature and body**

In `src/woodglue/apps/llm_docs.py`, replace `generate_llms_txt`:

```python
def generate_llms_txt(method_index: dict[str, dict[str, NamespaceNode]]) -> str:
    """
    Generate an `llms.txt` index listing all methods in the method index.
    """
    lines = [
        "# Woodglue API",
        "",
        "> JSON-RPC 2.0 server",
        "",
        "## Methods",
        "",
    ]

    for prefix in sorted(method_index):
        for leaf_name, node in sorted(method_index[prefix].items()):
            teaser = _docstring_teaser(node.method.doc)
            qualified = f"{prefix}.{leaf_name}"
            doc_link = f"/docs/methods/{qualified}.md"
            if teaser:
                lines.append(f"- [{qualified}]({doc_link}): {teaser}")
            else:
                lines.append(f"- [{qualified}]({doc_link})")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Update `LlmsTxtHandler` to use `method_index`**

In `src/woodglue/apps/llm_docs.py`, change `LlmsTxtHandler.get`:

```python
class LlmsTxtHandler(tornado.web.RequestHandler):
    """GET /docs/llms.txt"""

    @override
    def get(self) -> None:
        method_index: dict[str, dict[str, NamespaceNode]] = self.application.settings[
            "method_index"
        ]
        self.set_header("Content-Type", "text/plain; charset=utf-8")
        self.write(generate_llms_txt(method_index))
```

- [ ] **Step 5: Run tests**

Run: `make lint && make test`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/woodglue/apps/llm_docs.py tests/test_llm_docs.py
git commit -m "fix: generate_llms_txt uses method_index for api tag filtering"
```

---

### Task 2: Fix `generate_openapi_spec` to use `method_index` and add `x-global-ref`

**Files:**
- Modify: `src/woodglue/apps/llm_docs.py`
- Modify: `tests/test_llm_docs.py`

- [ ] **Step 1: Update test to pass `method_index` and check `x-global-ref`**

In `tests/test_llm_docs.py`, replace `test_generate_openapi_spec`:

```python
def test_generate_openapi_spec():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    spec = generate_openapi_spec(index)
    assert spec["openapi"] == "3.0.3"
    assert "/rpc/items.create_item" in spec["paths"]
    assert "/rpc/math.simple_add" in spec["paths"]


def test_openapi_x_global_ref():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    spec = generate_openapi_spec(index)
    # Check x-global-ref on request body schema (BaseModel param)
    create_op = spec["paths"]["/rpc/items.create_item"]["post"]
    req_schema = create_op["requestBody"]["content"]["application/json"]["schema"]
    input_prop = req_schema["properties"]["input"]
    assert "x-global-ref" in input_prop
    assert input_prop["x-global-ref"].endswith(":ItemIn")
    # Check x-global-ref on response schema (BaseModel return)
    resp_schema = create_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert "x-global-ref" in resp_schema
    assert resp_schema["x-global-ref"].endswith(":ItemOut")
    # Simple types should NOT have x-global-ref
    add_op = spec["paths"]["/rpc/math.simple_add"]["post"]
    add_resp = add_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert "x-global-ref" not in add_resp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_docs.py::test_generate_openapi_spec tests/test_llm_docs.py::test_openapi_x_global_ref -v`
Expected: FAIL

- [ ] **Step 3: Update `_python_type_to_schema` to add `x-global-ref`**

In `src/woodglue/apps/llm_docs.py`, replace `_python_type_to_schema`:

```python
def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema fragment.

    For BaseModel types, includes an `x-global-ref` vendor extension
    with the fully qualified module path for smart client deserialization.
    """
    if annotation is None or annotation is inspect.Parameter.empty:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if _is_basemodel(annotation):
        schema = annotation.model_json_schema()
        schema["x-global-ref"] = f"{annotation.__module__}:{annotation.__qualname__}"
        return schema
    return {"type": "string"}
```

- [ ] **Step 4: Change `generate_openapi_spec` signature and body**

In `src/woodglue/apps/llm_docs.py`, replace `generate_openapi_spec`:

```python
def generate_openapi_spec(
    method_index: dict[str, dict[str, NamespaceNode]],
) -> dict[str, Any]:
    """Build an OpenAPI 3.0.3 spec dict from the method index."""
    paths: dict[str, Any] = {}

    for prefix in sorted(method_index):
        for leaf_name, node in sorted(method_index[prefix].items()):
            method = node.method
            qualified = f"{prefix}.{leaf_name}"
            path = f"/rpc/{qualified}"

            properties: dict[str, Any] = {}
            required: list[str] = []
            for arg in method.args:
                prop = _python_type_to_schema(arg.annotation)
                if arg.description:
                    prop["description"] = arg.description
                if arg.default is not None and arg.default is not inspect.Parameter.empty:
                    prop["default"] = _json_safe_default(arg.default)
                properties[arg.name] = prop
                if not arg.is_optional:
                    required.append(arg.name)

            request_body_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }
            if required:
                request_body_schema["required"] = required

            ret = method.return_annotation
            if ret is None or ret is inspect.Parameter.empty:
                response_schema: dict[str, Any] = {"type": "object"}
            else:
                response_schema = _python_type_to_schema(ret)

            summary = _docstring_teaser(method.doc)
            operation: dict[str, Any] = {
                "summary": summary or qualified,
                "operationId": qualified,
                "requestBody": {
                    "required": bool(required),
                    "content": {
                        "application/json": {
                            "schema": request_body_schema,
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {
                            "application/json": {
                                "schema": response_schema,
                            }
                        },
                    }
                },
            }
            if method.doc:
                operation["description"] = method.doc.strip()

            paths[path] = {"post": operation}

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Woodglue JSON-RPC API",
            "version": "1.0.0",
        },
        "paths": paths,
    }
```

- [ ] **Step 5: Update `OpenApiHandler` to use `method_index`**

In `src/woodglue/apps/llm_docs.py`, find `OpenApiHandler` and change it:

```python
class OpenApiHandler(tornado.web.RequestHandler):
    """GET /docs/openapi.json"""

    @override
    def get(self) -> None:
        method_index: dict[str, dict[str, NamespaceNode]] = self.application.settings[
            "method_index"
        ]
        self.set_header("Content-Type", "application/json")
        self.write(generate_openapi_spec(method_index))
```

- [ ] **Step 6: Run tests**

Run: `make lint && make test`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/woodglue/apps/llm_docs.py tests/test_llm_docs.py
git commit -m "fix: generate_openapi_spec uses method_index and adds x-global-ref"
```

---

### Task 3: YAML Namespace Config Loading

**Files:**
- Modify: `src/woodglue/cli.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for YAML namespace loading**

Add to `tests/test_config.py`:

```python
from woodglue.cli import load_namespaces


def test_load_namespaces_from_yaml():
    """Load a namespace from a YAML config file."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        ns_config_path = data_dir / "test_ns.yaml"
        ns_config_path.write_text(
            "entries:\n"
            "  - nsref: hello\n"
            '    gref: "woodglue.hello:hello"\n'
            "    tags: ['api']\n"
            "  - nsref: pydantic_hello\n"
            '    gref: "woodglue.hello:pydantic_hello"\n'
            "    tags: ['api']\n"
        )
        namespaces = load_namespaces({"greet": "test_ns.yaml"}, data_dir)
        assert "greet" in namespaces
        ns = namespaces["greet"]
        # Verify the methods are registered
        node = ns.get("hello")
        assert node is not None
        assert "api" in node.tags


def test_load_namespaces_from_globalref():
    """Load a namespace from a GlobalRef string (existing behavior)."""
    namespaces = load_namespaces({"hello": "woodglue.hello:ns"}, Path("."))
    assert "hello" in namespaces
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_load_namespaces_from_yaml -v`
Expected: FAIL — `load_namespaces` doesn't accept `data_dir` parameter

- [ ] **Step 3: Update `load_namespaces` in `cli.py`**

Replace `load_namespaces` in `src/woodglue/cli.py`:

```python
def load_namespaces(ns_map: dict[str, str], data_dir: Path) -> dict[str, Namespace]:
    """
    Load all namespaces from config, keyed by prefix.

    Values ending with `.yaml` or `.yml` are treated as namespace config
    file paths relative to `data_dir`, loaded via
    `lythonic.compose.namespace_config.load_namespace`.
    Otherwise, values are treated as GlobalRef strings pointing to
    existing Namespace instances.
    """
    from lythonic.compose.namespace_config import NamespaceConfig, load_namespace
    from pydantic_yaml import parse_yaml_file_as

    result: dict[str, Namespace] = {}
    for prefix, value in ns_map.items():
        if value.endswith(".yaml") or value.endswith(".yml"):
            config_path = data_dir / value
            ns_config = parse_yaml_file_as(NamespaceConfig, config_path)
            result[prefix] = load_namespace(ns_config, data_dir)
        else:
            gref = GlobalRef(value)
            ns = gref.get_instance()
            assert isinstance(ns, Namespace), f"{value} is not a Namespace"
            result[prefix] = ns
    return result
```

- [ ] **Step 4: Update `start` command to pass `data_dir`**

In `src/woodglue/cli.py`, update the `start` function call:

```python
    namespaces = load_namespaces(config.namespaces, server_cfg.data)
```

- [ ] **Step 5: Run tests**

Run: `make lint && make test`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/woodglue/cli.py tests/test_config.py
git commit -m "feat: load namespaces from YAML config files"
```

---

### Task 4: Async RPC Client

**Files:**
- Create: `src/woodglue/client.py`
- Create: `tests/test_client.py`

- [ ] **Step 1: Write failing test for basic RPC call**

Create `tests/test_client.py`:

```python
"""Tests for woodglue.client.WoodglueClient."""

import json

import tornado.testing
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.client import WoodglueClient, WoodglueRpcError
from woodglue.config import WoodglueConfig
from woodglue.hello import HelloIn, HelloOut, hello, pydantic_hello


def _make_namespaces() -> dict[str, Namespace]:
    ns = Namespace()
    ns.register(hello, nsref="hello", tags=["api"])
    ns.register(pydantic_hello, nsref="pydantic_hello", tags=["api"])
    return {"test": ns}


class TestWoodglueClient(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        config = WoodglueConfig(namespaces={"test": "unused"})
        return create_app(namespaces=_make_namespaces(), config=config)

    async def test_call_simple_method(self):
        client = WoodglueClient(self.get_url(""))
        result = await client.call("test.hello", name="World")
        assert result == 5

    async def test_call_basemodel_method_raw(self):
        """Without spec loading, returns raw dict."""
        client = WoodglueClient(self.get_url(""))
        result = await client.call("test.pydantic_hello",
                                   input={"name": "Alice", "age": 30})
        assert result == {"eman": "ecilA", "ega": -30}

    async def test_call_with_return_type(self):
        """Explicit return_type deserializes the result."""
        client = WoodglueClient(self.get_url(""))
        result = await client.call("test.pydantic_hello",
                                   input={"name": "Alice", "age": 30},
                                   return_type=HelloOut)
        assert isinstance(result, HelloOut)
        assert result.eman == "ecilA"
        assert result.ega == -30

    async def test_call_with_spec_loading(self):
        """After load_spec, return types are auto-resolved."""
        client = WoodglueClient(self.get_url(""))
        await client.load_spec(strict=True)
        result = await client.call("test.pydantic_hello",
                                   input={"name": "Bob", "age": 25})
        assert isinstance(result, HelloOut)
        assert result.eman == "boB"

    async def test_call_method_not_found(self):
        """Calling a non-existent method raises WoodglueRpcError."""
        client = WoodglueClient(self.get_url(""))
        try:
            await client.call("test.nonexistent")
            raise AssertionError("Expected WoodglueRpcError")
        except WoodglueRpcError as e:
            assert e.code == -32601

    async def test_call_with_resolver(self):
        """Custom resolver overrides spec resolution."""
        client = WoodglueClient(self.get_url(""))
        # Load spec so _return_grefs is populated (resolver receives gref string)
        await client.load_spec(strict=True)

        def resolver(gref: str) -> type | None:
            if "HelloOut" in gref:
                return HelloOut
            return None

        # Clear auto-resolved types to prove resolver is used
        client._return_types.clear()
        result = await client.call("test.pydantic_hello",
                                   input={"name": "Eve", "age": 20},
                                   resolver=resolver)
        assert isinstance(result, HelloOut)
        assert result.eman == "evE"

    async def test_load_spec_strict_succeeds(self):
        """strict=True succeeds when all grefs are resolvable."""
        client = WoodglueClient(self.get_url(""))
        await client.load_spec(strict=True)
        assert "test.pydantic_hello" in client._return_types

    async def test_basemodel_input_serialized(self):
        """BaseModel kwargs are serialized before sending."""
        client = WoodglueClient(self.get_url(""))
        result = await client.call("test.pydantic_hello",
                                   input=HelloIn(name="Zoe", age=10),
                                   return_type=HelloOut)
        assert isinstance(result, HelloOut)
        assert result.eman == "eoZ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'WoodglueClient'`

- [ ] **Step 3: Implement `WoodglueClient`**

Create `src/woodglue/client.py`:

```python
"""
Async JSON-RPC 2.0 client for woodglue servers.

`WoodglueClient` makes typed RPC calls, optionally resolving return types
from `x-global-ref` in the OpenAPI spec. Uses Tornado's `AsyncHTTPClient`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from tornado.httpclient import AsyncHTTPClient, HTTPRequest


class WoodglueRpcError(Exception):
    """Raised when the server returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"JSON-RPC error {code}: {message}")


class WoodglueClient:
    """
    Async client for woodglue JSON-RPC servers.

    Optionally loads the OpenAPI spec to auto-resolve return types from
    `x-global-ref` vendor extensions.

    Resolution priority on `call()`:
    1. Explicit `return_type` parameter
    2. `resolver` callable
    3. Type from `load_spec()` if loaded
    4. Return raw dict/primitive
    """

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        self._http = AsyncHTTPClient()
        self._request_id = 0
        self._return_types: dict[str, type[BaseModel]] = {}

    async def load_spec(self, strict: bool = False) -> None:
        """
        Fetch `/docs/openapi.json` and resolve `x-global-ref` types.

        With `strict=True`, raises `ImportError` if any gref cannot be
        resolved. With `strict=False`, skips unresolvable grefs.
        """
        from lythonic import GlobalRef

        resp = await self._http.fetch(f"{self._base_url}/docs/openapi.json")
        spec = json.loads(resp.body)

        for path, path_item in spec.get("paths", {}).items():
            for _http_method, operation in path_item.items():
                op_id = operation.get("operationId")
                if not op_id:
                    continue

                resp_content = (
                    operation.get("responses", {})
                    .get("200", {})
                    .get("content", {})
                    .get("application/json", {})
                )
                schema = resp_content.get("schema", {})
                gref_str = schema.get("x-global-ref")
                if not gref_str:
                    continue

                try:
                    gref = GlobalRef(gref_str)
                    cls = gref.get_instance()
                    if isinstance(cls, type) and issubclass(cls, BaseModel):
                        self._return_types[op_id] = cls
                except Exception:
                    if strict:
                        raise ImportError(
                            f"Cannot resolve x-global-ref '{gref_str}' "
                            f"for method '{op_id}'"
                        )

    async def call(
        self,
        method: str,
        *,
        return_type: type[BaseModel] | None = None,
        resolver: Callable[[str], type[BaseModel] | None] | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Call a JSON-RPC method and return the deserialized result.

        `kwargs` are sent as the JSON-RPC `params` object. BaseModel
        values in kwargs are serialized via `model_dump(mode="json")`.
        """
        self._request_id += 1

        # Serialize BaseModel kwargs
        params: dict[str, Any] = {}
        for key, value in kwargs.items():
            if isinstance(value, BaseModel):
                params[key] = value.model_dump(mode="json")
            else:
                params[key] = value

        body = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._request_id,
        })

        req = HTTPRequest(
            f"{self._base_url}/rpc",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = await self._http.fetch(req)
        data = json.loads(resp.body)

        if "error" in data:
            err = data["error"]
            raise WoodglueRpcError(err["code"], err["message"])

        result = data.get("result")

        # Resolve the return type
        resolved_type = return_type
        if resolved_type is None and resolver is not None:
            gref_str = self._return_types.get(method)
            if gref_str is None:
                # Try to get gref from spec metadata if we have it
                pass
            resolved_type = resolver(method) if resolver else None
        if resolved_type is None:
            resolved_type = self._return_types.get(method)

        if resolved_type is not None and isinstance(result, dict):
            return resolved_type.model_validate(result)
        return result
```

Wait — the `resolver` callable in the spec takes a `global_ref: str` not a method name. Let me fix the logic. The resolver needs the `x-global-ref` string. Let me also store the gref strings separately.

Replace the `WoodglueClient` implementation:

```python
"""
Async JSON-RPC 2.0 client for woodglue servers.

`WoodglueClient` makes typed RPC calls, optionally resolving return types
from `x-global-ref` in the OpenAPI spec. Uses Tornado's `AsyncHTTPClient`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from tornado.httpclient import AsyncHTTPClient, HTTPRequest


class WoodglueRpcError(Exception):
    """Raised when the server returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"JSON-RPC error {code}: {message}")


class WoodglueClient:
    """
    Async client for woodglue JSON-RPC servers.

    Optionally loads the OpenAPI spec to auto-resolve return types from
    `x-global-ref` vendor extensions.

    Resolution priority on `call()`:
    1. Explicit `return_type` parameter
    2. `resolver` callable (receives the `x-global-ref` string)
    3. Type from `load_spec()` if loaded
    4. Return raw dict/primitive
    """

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")
        self._http = AsyncHTTPClient()
        self._request_id = 0
        self._return_types: dict[str, type[BaseModel]] = {}
        self._return_grefs: dict[str, str] = {}

    async def load_spec(self, strict: bool = False) -> None:
        """
        Fetch `/docs/openapi.json` and resolve `x-global-ref` types.

        With `strict=True`, raises `ImportError` if any gref cannot be
        resolved. With `strict=False`, skips unresolvable grefs.
        """
        from lythonic import GlobalRef

        resp = await self._http.fetch(f"{self._base_url}/docs/openapi.json")
        spec = json.loads(resp.body)

        for path, path_item in spec.get("paths", {}).items():
            for _http_method, operation in path_item.items():
                op_id = operation.get("operationId")
                if not op_id:
                    continue

                resp_content = (
                    operation.get("responses", {})
                    .get("200", {})
                    .get("content", {})
                    .get("application/json", {})
                )
                schema = resp_content.get("schema", {})
                gref_str = schema.get("x-global-ref")
                if not gref_str:
                    continue

                self._return_grefs[op_id] = gref_str

                try:
                    gref = GlobalRef(gref_str)
                    cls = gref.get_instance()
                    if isinstance(cls, type) and issubclass(cls, BaseModel):
                        self._return_types[op_id] = cls
                except Exception:
                    if strict:
                        raise ImportError(
                            f"Cannot resolve x-global-ref '{gref_str}' "
                            f"for method '{op_id}'"
                        )

    async def call(
        self,
        method: str,
        *,
        return_type: type[BaseModel] | None = None,
        resolver: Callable[[str], type[BaseModel] | None] | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Call a JSON-RPC method and return the deserialized result.

        `kwargs` are sent as the JSON-RPC `params` object. BaseModel
        values in kwargs are serialized via `model_dump(mode="json")`.
        """
        self._request_id += 1

        # Serialize BaseModel kwargs
        params: dict[str, Any] = {}
        for key, value in kwargs.items():
            if isinstance(value, BaseModel):
                params[key] = value.model_dump(mode="json")
            else:
                params[key] = value

        body = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._request_id,
        })

        req = HTTPRequest(
            f"{self._base_url}/rpc",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = await self._http.fetch(req)
        data = json.loads(resp.body)

        if "error" in data:
            err = data["error"]
            raise WoodglueRpcError(err["code"], err["message"])

        result = data.get("result")

        # Resolve the return type
        resolved_type = return_type
        if resolved_type is None and resolver is not None:
            gref_str = self._return_grefs.get(method)
            if gref_str:
                resolved_type = resolver(gref_str)
        if resolved_type is None:
            resolved_type = self._return_types.get(method)

        if resolved_type is not None and isinstance(result, dict):
            return resolved_type.model_validate(result)
        return result
```

- [ ] **Step 4: Run tests**

Run: `make lint && make test`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/woodglue/client.py tests/test_client.py
git commit -m "feat: add async WoodglueClient with typed RPC calls"
```

---

### Task 5: Integration Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_integration.py`:

```python
"""
Integration test: verifies all four surfaces (RPC, llms.txt, markdown, OpenAPI)
agree on api-tagged method publishing. Uses YAML namespace configs.
"""

import json
import tempfile
from pathlib import Path

import tornado.testing
from lythonic.compose.namespace_config import NamespaceConfig, load_namespace
from typing_extensions import override

from woodglue.apps.llm_docs import build_method_index
from woodglue.apps.server import create_app
from woodglue.client import WoodglueClient, WoodglueRpcError
from woodglue.config import WoodglueConfig
from woodglue.hello import HelloOut

PUB_NS_YAML = """\
entries:
  - nsref: hello
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: pydantic_hello
    gref: "woodglue.hello:pydantic_hello"
    tags: ["api"]
  - nsref: secret_hello
    gref: "woodglue.hello:hello"
"""

INTERNAL_NS_YAML = """\
entries:
  - nsref: greet
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: hidden_greet
    gref: "woodglue.hello:hello"
"""


def _load_ns_from_yaml(yaml_content: str, data_dir: Path):
    from pydantic_yaml import parse_yaml_raw_as

    config = parse_yaml_raw_as(NamespaceConfig, yaml_content)
    return load_namespace(config, data_dir)


class TestIntegration(tornado.testing.AsyncHTTPTestCase):
    @override
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._data_dir = Path(self._tmp.name)
        super().setUp()

    @override
    def tearDown(self):
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self):
        namespaces = {
            "pub": _load_ns_from_yaml(PUB_NS_YAML, self._data_dir),
            "internal": _load_ns_from_yaml(INTERNAL_NS_YAML, self._data_dir),
        }
        config = WoodglueConfig(
            namespaces={"pub": "pub_ns.yaml", "internal": "internal_ns.yaml"}
        )
        return create_app(namespaces=namespaces, config=config)

    # ---- Positive: api-tagged methods appear in all 4 surfaces ----

    def _rpc_call(self, method: str, params: dict | None = None):
        body: dict = {"jsonrpc": "2.0", "method": method, "id": 1}
        if params is not None:
            body["params"] = params
        resp = self.fetch("/rpc", method="POST", body=json.dumps(body))
        return json.loads(resp.body)

    def test_rpc_api_methods_callable(self):
        data = self._rpc_call("pub.hello", {"name": "test"})
        assert "result" in data
        assert data["result"] == 4

        data = self._rpc_call("pub.pydantic_hello",
                              {"input": {"name": "Alice", "age": 30}})
        assert data["result"] == {"eman": "ecilA", "ega": -30}

        data = self._rpc_call("internal.greet", {"name": "hi"})
        assert "result" in data

    def test_llms_txt_lists_api_methods(self):
        resp = self.fetch("/docs/llms.txt")
        body = resp.body.decode()
        assert "pub.hello" in body
        assert "pub.pydantic_hello" in body
        assert "internal.greet" in body

    def test_markdown_api_methods_accessible(self):
        for name in ["pub.hello", "pub.pydantic_hello", "internal.greet"]:
            resp = self.fetch(f"/docs/methods/{name}.md")
            assert resp.code == 200, f"{name} should return 200"

    def test_openapi_has_api_methods(self):
        resp = self.fetch("/docs/openapi.json")
        spec = json.loads(resp.body)
        paths = spec["paths"]
        assert "/rpc/pub.hello" in paths
        assert "/rpc/pub.pydantic_hello" in paths
        assert "/rpc/internal.greet" in paths

    def test_openapi_has_x_global_ref(self):
        resp = self.fetch("/docs/openapi.json")
        spec = json.loads(resp.body)
        op = spec["paths"]["/rpc/pub.pydantic_hello"]["post"]
        resp_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert "x-global-ref" in resp_schema
        assert "HelloOut" in resp_schema["x-global-ref"]

    # ---- Negative: non-api methods excluded from all 4 surfaces ----

    def test_rpc_non_api_methods_not_found(self):
        data = self._rpc_call("pub.secret_hello", {"name": "test"})
        assert data["error"]["code"] == -32601

        data = self._rpc_call("internal.hidden_greet", {"name": "test"})
        assert data["error"]["code"] == -32601

    def test_llms_txt_excludes_non_api(self):
        resp = self.fetch("/docs/llms.txt")
        body = resp.body.decode()
        assert "secret_hello" not in body
        assert "hidden_greet" not in body

    def test_markdown_non_api_returns_404(self):
        resp = self.fetch("/docs/methods/pub.secret_hello.md")
        assert resp.code == 404
        resp = self.fetch("/docs/methods/internal.hidden_greet.md")
        assert resp.code == 404

    def test_openapi_excludes_non_api(self):
        resp = self.fetch("/docs/openapi.json")
        spec = json.loads(resp.body)
        paths_str = json.dumps(spec["paths"])
        assert "secret_hello" not in paths_str
        assert "hidden_greet" not in paths_str

    # ---- Client ----

    async def test_client_load_spec_and_call(self):
        client = WoodglueClient(self.get_url(""))
        await client.load_spec(strict=True)
        result = await client.call("pub.pydantic_hello",
                                   input={"name": "Alice", "age": 30})
        assert isinstance(result, HelloOut)
        assert result.eman == "ecilA"

    async def test_client_non_api_raises_error(self):
        client = WoodglueClient(self.get_url(""))
        try:
            await client.call("pub.secret_hello", name="test")
            raise AssertionError("Expected WoodglueRpcError")
        except WoodglueRpcError as e:
            assert e.code == -32601
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_integration.py -v`
Expected: All pass

Run: `make lint && make test`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration test for api tag filtering across all surfaces"
```

---

### Task 6: Update example config to use YAML namespace

**Files:**
- Create: `data/hello_ns.yaml`
- Modify: `data/woodglue.yaml`

- [ ] **Step 1: Create YAML namespace config**

Create `data/hello_ns.yaml`:

```yaml
entries:
  - nsref: hello
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: pydantic_hello
    gref: "woodglue.hello:pydantic_hello"
    tags: ["api"]
```

- [ ] **Step 2: Update woodglue.yaml to use the config file**

Replace `data/woodglue.yaml`:

```yaml
namespaces:
  hello: "hello_ns.yaml"

docs:
  enabled: true
  openapi: true

ui:
  enabled: true
```

- [ ] **Step 3: Run full suite**

Run: `make lint && make test`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add data/hello_ns.yaml data/woodglue.yaml
git commit -m "feat: example config uses YAML namespace config"
```
