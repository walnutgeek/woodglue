# Config, Multi-Namespace, LLM Docs, and UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add YAML config, multi-namespace routing, BaseModel round-trip serialization, LLM-friendly documentation generation, and a JS documentation UI to woodglue.

**Architecture:** Server reads `{data}/woodglue.yaml` at startup to load multiple namespaces (mounted as dot-prefixed method names on `/rpc`). Documentation is generated as `llms.txt` + per-method markdown + OpenAPI, all served by Tornado. A Vite-built JS SPA at `/ui/` renders the markdown docs client-side.

**Tech Stack:** Python 3.11+, Pydantic, pydantic-yaml, Tornado, lythonic, Vite, marked.js

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/woodglue/config.py` | `WoodglueConfig`, `DocsConfig`, `UiConfig` Pydantic models + YAML loader |
| `src/woodglue/cli.py` | CLI `Server` model (remove `module_path`), `start` loads config |
| `src/woodglue/apps/rpc.py` | Multi-namespace dispatch, BaseModel input deserialization |
| `src/woodglue/apps/server.py` | App factory accepts `WoodglueConfig` + namespaces dict, conditional route mounting |
| `src/woodglue/apps/llm_docs.py` | New: generates `llms.txt`, per-method markdown, OpenAPI spec |
| `src/woodglue/apps/docs.py` | Removed after refactor into `llm_docs.py` |
| `src/woodglue/hello/__init__.py` | Add docstrings for doc generation testing |
| `src/woodglue/ui/` | Vite JS project: `package.json`, `vite.config.js`, `src/`, `dist/` |
| `tests/test_config.py` | Config loading tests |
| `tests/test_rpc.py` | Updated: multi-namespace + BaseModel round-trip tests |
| `tests/test_docs.py` | Updated: replaced by `test_llm_docs.py` |
| `tests/test_llm_docs.py` | New: llms.txt, markdown, OpenAPI generation tests |

---

### Task 1: Config Models and YAML Loader

**Files:**
- Create: `src/woodglue/config.py` (replace commented-out content)
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test for config loading**

Create `tests/test_config.py`:

```python
"""Tests for woodglue.config YAML loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

from woodglue.config import DocsConfig, UiConfig, WoodglueConfig, load_config


def test_load_minimal_config():
    """Load a YAML with only namespaces (required field)."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "namespaces:\n"
            "  hello: 'woodglue.hello:ns'\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.namespaces == {"hello": "woodglue.hello:ns"}
        assert cfg.docs == DocsConfig()
        assert cfg.ui == UiConfig()


def test_load_full_config():
    """Load a YAML with all sections populated."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "namespaces:\n"
            "  hello: 'woodglue.hello:ns'\n"
            "  other: 'some.module:ns'\n"
            "docs:\n"
            "  enabled: false\n"
            "  openapi: false\n"
            "ui:\n"
            "  enabled: false\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.namespaces == {
            "hello": "woodglue.hello:ns",
            "other": "some.module:ns",
        }
        assert cfg.docs.enabled is False
        assert cfg.docs.openapi is False
        assert cfg.ui.enabled is False


def test_load_config_missing_file():
    """Raise FileNotFoundError when woodglue.yaml is missing."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            load_config(Path(tmp))
            raise AssertionError("Expected FileNotFoundError")
        except FileNotFoundError:
            pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_config' from 'woodglue.config'`

- [ ] **Step 3: Implement config models and loader**

Replace the contents of `src/woodglue/config.py` with:

```python
"""
YAML-backed server configuration.

The config file lives at `{data_dir}/woodglue.yaml` and is required to run
the server. It declares namespaces, documentation, and UI settings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pydantic_yaml import parse_yaml_file_as

CONFIG_FILENAME = "woodglue.yaml"


class DocsConfig(BaseModel):
    """Documentation generation settings."""

    enabled: bool = True
    openapi: bool = True


class UiConfig(BaseModel):
    """JavaScript documentation UI settings."""

    enabled: bool = True


class WoodglueConfig(BaseModel):
    """
    Root configuration loaded from `woodglue.yaml`.

    `namespaces` maps a prefix string to a Python module path in
    `"module.path:attribute"` format (a lythonic `GlobalRef`).
    """

    namespaces: dict[str, str]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()


def load_config(data_dir: Path) -> WoodglueConfig:
    """
    Load `WoodglueConfig` from `{data_dir}/woodglue.yaml`.

    Raises `FileNotFoundError` if the file does not exist.
    """
    config_path = data_dir / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return parse_yaml_file_as(WoodglueConfig, config_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Run full lint and test suite**

Run: `make lint && make test`
Expected: No lint errors, all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/woodglue/config.py tests/test_config.py
git commit -m "feat: add WoodglueConfig models and YAML loader"
```

---

### Task 2: Multi-Namespace Routing in RPC Handler

**Files:**
- Modify: `src/woodglue/apps/rpc.py`
- Modify: `src/woodglue/apps/server.py`
- Modify: `tests/test_rpc.py`

- [ ] **Step 1: Write failing tests for multi-namespace dispatch**

Add to the top of `tests/test_rpc.py`, after existing imports:

```python
from woodglue.hello import HelloIn, HelloOut, pydantic_hello
```

Add a second namespace and new test class at the end of `tests/test_rpc.py`:

```python
def _make_multi_namespace() -> dict[str, Namespace]:
    ns1 = Namespace()
    ns1.register(sync_add, nsref="sync_add")
    ns1.register(async_greet, nsref="async_greet")

    ns2 = Namespace()
    ns2.register(pydantic_hello, nsref="pydantic_hello")

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

    def test_unknown_prefix_returns_not_found(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("bogus.sync_add", {"a": 1, "b": 2}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["error"]["code"] == -32601
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rpc.py::TestMultiNamespaceRpc -v`
Expected: FAIL — `create_app()` does not accept `namespaces` kwarg

- [ ] **Step 3: Update `create_app` to accept multi-namespace dict**

Replace `src/woodglue/apps/server.py` with (docs/UI routes added later in Task 6):

```python
"""Tornado application factory for the woodglue JSON-RPC server."""

from __future__ import annotations

import tornado.web
from lythonic.compose.namespace import Namespace

from woodglue.apps.rpc import JsonRpcHandler
from woodglue.config import WoodglueConfig


def create_app(
    namespaces: dict[str, Namespace],
    config: WoodglueConfig | None = None,
) -> tornado.web.Application:
    """
    Build a Tornado Application with JSON-RPC and optional docs/UI routes.

    `namespaces` maps prefix strings to loaded Namespace instances.
    """
    if config is None:
        config = WoodglueConfig(namespaces={})

    handlers: list[tuple[str, type[tornado.web.RequestHandler]]] = [
        (r"/rpc", JsonRpcHandler),
    ]

    return tornado.web.Application(
        handlers,
        namespaces=namespaces,
        config=config,
    )
```

- [ ] **Step 4: Update `JsonRpcHandler` for multi-namespace dispatch**

In `src/woodglue/apps/rpc.py`, replace the namespace lookup section (the block from `# 3. Look up method in namespace` through the `kwargs` building and method call) with multi-namespace aware logic.

Replace the entire `post` method body from `# 3. Look up method in namespace` onwards (lines 93-151):

```python
        # 3. Resolve namespace and method via dot prefix
        namespaces: dict[str, Namespace] = self.application.settings["namespaces"]

        dot_pos = method.find(".")
        if dot_pos < 0:
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return

        prefix = method[:dot_pos]
        method_name = method[dot_pos + 1 :]

        ns = namespaces.get(prefix)
        if ns is None:
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return

        try:
            node = ns.get(method_name)
        except KeyError:
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return

        # 4. Build kwargs from params
        kwargs: dict[str, Any] = {}
        method_args = node.method.args

        if params is not None:
            if isinstance(params, list):
                for arg_info, value in zip(method_args, params, strict=False):
                    kwargs[arg_info.name] = value
            elif isinstance(params, dict):
                kwargs = dict(params)
            else:
                self.write(
                    _error_response(
                        INVALID_PARAMS,
                        "params must be an array or object",
                        request_id,
                    )
                )
                return

        # 5. Validate required params
        for arg_info in method_args:
            if not arg_info.is_optional and arg_info.name not in kwargs:
                self.write(
                    _error_response(
                        INVALID_PARAMS,
                        f"Missing required parameter: {arg_info.name}",
                        request_id,
                    )
                )
                return

        # 6. Deserialize BaseModel params
        for arg_info in method_args:
            if (
                arg_info.name in kwargs
                and isinstance(arg_info.annotation, type)
                and issubclass(arg_info.annotation, BaseModel)
            ):
                kwargs[arg_info.name] = arg_info.annotation.model_validate(kwargs[arg_info.name])

        # 7. Call the method
        try:
            result = node(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception("Internal error calling %s", method)
            self.write(_error_response(INTERNAL_ERROR, "Internal error", request_id))
            return

        # 8. Return result
        self.write(
            {
                "jsonrpc": "2.0",
                "result": _serialize_result(result),
                "id": request_id,
            }
        )
```

Also add this import at the top of `rpc.py`:

```python
from lythonic.compose.namespace import Namespace
```

- [ ] **Step 5: Update old tests to use the new `create_app` signature**

The existing `TestJsonRpc` class in `tests/test_rpc.py` uses `create_app(namespace=ns)`. Update it to use the new multi-namespace dict signature. Change `get_app`:

```python
    @override
    def get_app(self):
        ns = _make_namespace()
        return create_app(namespaces={"test": ns})
```

The existing tests use method names like `"test:sync_add"`. These now need to be `"test.sync_add"` (dot notation). Update every `_rpc_body` call in `TestJsonRpc`:

- `"test:sync_add"` → `"test.sync_add"`
- `"test:async_greet"` → `"test.async_greet"`
- `"test:nonexistent"` → `"test.nonexistent"`

- [ ] **Step 6: Update `tests/test_docs.py` to use new `create_app` signature**

In `tests/test_docs.py`, change `get_app`:

```python
    @override
    def get_app(self):
        return create_app(namespaces={"api": _make_namespace()})
```

These tests will likely fail because `/docs` and `/docs/ui` routes are no longer mounted. That's expected — we'll replace these tests in Task 5. For now, mark them with `pytest.mark.skip`:

```python
import pytest

@pytest.mark.skip(reason="docs routes being refactored to llm_docs")
class TestDocs(tornado.testing.AsyncHTTPTestCase):
    ...
```

- [ ] **Step 7: Run all tests**

Run: `uv run pytest tests/test_rpc.py -v`
Expected: All tests in `TestJsonRpc` and `TestMultiNamespaceRpc` pass

Run: `make lint && make test`
Expected: Clean lint, all tests pass (TestDocs skipped)

- [ ] **Step 8: Commit**

```bash
git add src/woodglue/apps/rpc.py src/woodglue/apps/server.py tests/test_rpc.py tests/test_docs.py
git commit -m "feat: multi-namespace RPC dispatch with dot-prefixed method names"
```

---

### Task 3: BaseModel Round-Trip Serialization Test

**Files:**
- Modify: `tests/test_rpc.py`

The BaseModel deserialization was already added in Task 2 (step 6 of the `post` method). This task verifies it works end-to-end with a dedicated test.

- [ ] **Step 1: Verify the BaseModel round-trip test passes**

The test `test_call_method_in_second_namespace` in `TestMultiNamespaceRpc` (added in Task 2) already tests the full round-trip:
- Sends `{"input": {"name": "Alice", "age": 30}}` (dict)
- Handler deserializes to `HelloIn(name="Alice", age=30)`
- `pydantic_hello` returns `HelloOut(eman="ecilA", ega=-30)`
- `_serialize_result` serializes back to `{"eman": "ecilA", "ega": -30}`

Run: `uv run pytest tests/test_rpc.py::TestMultiNamespaceRpc::test_call_method_in_second_namespace -v`
Expected: PASS

- [ ] **Step 2: Add a test for nested BaseModel serialization in output**

Add to `tests/test_rpc.py`, after existing imports:

```python
from pydantic import BaseModel as PydanticBaseModel


class Inner(PydanticBaseModel):
    value: int


class Outer(PydanticBaseModel):
    inner: Inner
    label: str
```

Add a function that returns nested BaseModels:

```python
def nested_output(x: int) -> Outer:
    """Return nested BaseModel."""
    return Outer(inner=Inner(value=x), label=f"item-{x}")
```

Update `_make_multi_namespace` to register it in `ns2`:

```python
def _make_multi_namespace() -> dict[str, Namespace]:
    ns1 = Namespace()
    ns1.register(sync_add, nsref="sync_add")
    ns1.register(async_greet, nsref="async_greet")

    ns2 = Namespace()
    ns2.register(pydantic_hello, nsref="pydantic_hello")
    ns2.register(nested_output, nsref="nested_output")

    return {"test": ns1, "hello": ns2}
```

Add the test to `TestMultiNamespaceRpc`:

```python
    def test_nested_basemodel_output(self):
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=_rpc_body("hello.nested_output", {"x": 42}),
        )
        assert resp.code == 200
        data = json.loads(resp.body)
        assert data["result"] == {"inner": {"value": 42}, "label": "item-42"}
```

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/test_rpc.py::TestMultiNamespaceRpc::test_nested_basemodel_output -v`
Expected: PASS (recursive serialization already works)

- [ ] **Step 4: Commit**

```bash
git add tests/test_rpc.py
git commit -m "test: add BaseModel round-trip and nested output tests"
```

---

### Task 4: Update CLI to Load Config

**Files:**
- Modify: `src/woodglue/cli.py`

- [ ] **Step 1: Update `Server` model and `start` command**

Replace `src/woodglue/cli.py`:

```python
import sys
from pathlib import Path

from lythonic import GlobalRef
from lythonic.compose.cli import ActionTree, Main, RunContext
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel, Field

from woodglue.config import load_config

main_at = ActionTree(Main)


class Server(BaseModel):
    """Managing the server"""

    data: Path = Field(default=Path("./data"), description="directory to store all server data")
    port: int = Field(default=5321, description="port to listen on")
    host: str = Field(default="127.0.0.1", description="host to bind to")


server_at = main_at.actions.add(Server)


def load_namespaces(ns_map: dict[str, str]) -> dict[str, Namespace]:
    """Load all namespaces from config, keyed by prefix."""
    result: dict[str, Namespace] = {}
    for prefix, module_path in ns_map.items():
        gref = GlobalRef(module_path)
        ns = gref.get_instance()
        assert isinstance(ns, Namespace), f"{module_path} is not a Namespace"
        result[prefix] = ns
    return result


@server_at.actions.wrap
def start(ctx: RunContext):  # pyright: ignore[reportUnusedParameter]
    """Starts the server in the foreground"""
    import tornado.ioloop

    from woodglue.apps.server import create_app

    server_cfg = ctx.path.get("/server")
    assert isinstance(server_cfg, Server)

    config = load_config(server_cfg.data)
    namespaces = load_namespaces(config.namespaces)

    app = create_app(namespaces=namespaces, config=config)
    app.listen(server_cfg.port, server_cfg.host)
    print(f"Woodglue server listening on http://{server_cfg.host}:{server_cfg.port}")
    print(f"  RPC endpoint: http://{server_cfg.host}:{server_cfg.port}/rpc")
    if config.docs.enabled:
        print(f"  LLM docs:     http://{server_cfg.host}:{server_cfg.port}/docs/llms.txt")
    if config.ui.enabled:
        print(f"  UI:           http://{server_cfg.host}:{server_cfg.port}/ui/")
    tornado.ioloop.IOLoop.current().start()


@server_at.actions.wrap
def stop():
    """Stops the server"""
    print("stopping server")


def main():
    if not main_at.run_args(sys.argv).success:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run lint and tests**

Run: `make lint && make test`
Expected: Clean lint, all tests pass

- [ ] **Step 3: Commit**

```bash
git add src/woodglue/cli.py
git commit -m "feat: CLI loads config from data dir, removes module_path"
```

---

### Task 5: LLM Documentation Generation

**Files:**
- Create: `src/woodglue/apps/llm_docs.py`
- Create: `tests/test_llm_docs.py`
- Modify: `src/woodglue/hello/__init__.py` (add docstrings)
- Delete: `src/woodglue/apps/docs.py`
- Delete: `tests/test_docs.py`

- [ ] **Step 1: Add docstrings to hello module for testing**

Update `src/woodglue/hello/__init__.py` — add docstrings to functions:

```python
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel


def hello(name: str) -> int:
    """Returns the length of a name.

    A simple example method that counts characters.
    """
    return len(name)


class HelloIn(BaseModel):
    name: str
    age: int


class HelloOut(BaseModel):
    eman: str
    ega: int


def pydantic_hello(input: HelloIn) -> HelloOut:
    """Reverses name and negates age.

    Demonstrates BaseModel input/output round-trip.
    Given a HelloIn with name and age, returns HelloOut
    with the name reversed and age negated.
    """
    return HelloOut(eman=input.name[::-1], ega=-input.age)


ns = Namespace()
ns.register_all(hello, pydantic_hello)
```

- [ ] **Step 2: Write failing tests for llms.txt and markdown generation**

Create `tests/test_llm_docs.py`:

```python
"""Tests for woodglue.apps.llm_docs generation."""

from __future__ import annotations

from lythonic.compose.namespace import Namespace
from pydantic import BaseModel

from woodglue.apps.llm_docs import generate_llms_txt, generate_method_markdown, generate_openapi_spec


class ItemIn(BaseModel):
    name: str
    count: int


class ItemOut(BaseModel):
    label: str
    total: int


def create_item(input: ItemIn) -> ItemOut:
    """Create an item from input.

    This is a longer description that explains
    the full behavior of the method.
    """
    return ItemOut(label=input.name, total=input.count)


def simple_add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def _make_namespaces() -> dict[str, Namespace]:
    ns1 = Namespace()
    ns1.register(create_item, nsref="create_item")

    ns2 = Namespace()
    ns2.register(simple_add, nsref="simple_add")

    return {"items": ns1, "math": ns2}


def test_generate_llms_txt():
    namespaces = _make_namespaces()
    txt = generate_llms_txt(namespaces)
    assert "# Woodglue API" in txt
    assert "- items.create_item: Create an item from input." in txt
    assert "- math.simple_add: Add two numbers." in txt


def test_generate_llms_txt_no_docstring():
    """Methods without docstrings use the qualified name as teaser."""
    ns = Namespace()
    ns.register(lambda x: x, nsref="identity")
    txt = generate_llms_txt({"misc": ns})
    assert "- misc.identity:" in txt


def test_generate_method_markdown_with_basemodel():
    namespaces = _make_namespaces()
    md = generate_method_markdown("items", "create_item", namespaces["items"])
    assert "# items.create_item" in md
    assert "Create an item from input." in md
    assert "## Parameters" in md
    assert "| input | ItemIn |" in md
    assert "## Returns" in md
    assert "`ItemOut`" in md
    assert "## Referenced Models" in md
    assert "### ItemIn" in md
    assert "### ItemOut" in md
    assert "| name | str |" in md
    assert "| count | int |" in md
    assert "| label | str |" in md
    assert "| total | int |" in md


def test_generate_method_markdown_simple_types():
    namespaces = _make_namespaces()
    md = generate_method_markdown("math", "simple_add", namespaces["math"])
    assert "# math.simple_add" in md
    assert "## Parameters" in md
    assert "| a | int |" in md
    assert "| b | int |" in md
    assert "## Referenced Models" not in md


def test_generate_openapi_spec():
    namespaces = _make_namespaces()
    spec = generate_openapi_spec(namespaces)
    assert spec["openapi"] == "3.0.3"
    assert "/rpc/items.create_item" in spec["paths"] or "items.create_item" in str(spec)
    assert "/rpc/math.simple_add" in spec["paths"] or "math.simple_add" in str(spec)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_docs.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_llms_txt' from 'woodglue.apps.llm_docs'`

- [ ] **Step 4: Implement `llm_docs.py`**

Create `src/woodglue/apps/llm_docs.py`:

```python
"""
LLM-friendly documentation generation for woodglue namespaces.

Generates three artifact types:
- `llms.txt`: index of all methods with one-line teasers
- Per-method markdown: full docs with parameters, return types, referenced models
- OpenAPI 3.0.3 spec: standard API spec
"""

from __future__ import annotations

import inspect
from typing import Any

from lythonic.compose import Method
from lythonic.compose.namespace import Namespace, NamespaceNode
from pydantic import BaseModel


def walk_namespace(ns: Namespace) -> list[NamespaceNode]:
    """Yield every NamespaceNode in `ns`, recursively."""
    nodes: list[NamespaceNode] = []
    for _name, node in ns._leaves.items():  # pyright: ignore[reportPrivateUsage]
        nodes.append(node)
    for _branch_name, branch in ns._branches.items():  # pyright: ignore[reportPrivateUsage]
        nodes.extend(walk_namespace(branch))
    return nodes


def _docstring_teaser(doc: str | None) -> str:
    """Extract the first line of a docstring as a teaser."""
    if not doc:
        return ""
    return doc.strip().split("\n")[0].strip()


def _type_display(annotation: Any) -> str:
    """Human-readable name for a type annotation."""
    if annotation is None or annotation is inspect.Parameter.empty:
        return "str"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def _is_basemodel(annotation: Any) -> bool:
    """Check if annotation is a BaseModel subclass."""
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _collect_referenced_models(method: Method) -> list[type[BaseModel]]:
    """
    Collect all BaseModel types referenced by a method's args and return type,
    recursively expanding nested models.
    """
    seen: set[type[BaseModel]] = set()
    queue: list[type[BaseModel]] = []

    for arg in method.args:
        if _is_basemodel(arg.annotation) and arg.annotation not in seen:
            seen.add(arg.annotation)
            queue.append(arg.annotation)

    ret = method.return_annotation
    if _is_basemodel(ret) and ret not in seen:
        seen.add(ret)
        queue.append(ret)

    # Expand nested models
    i = 0
    while i < len(queue):
        model = queue[i]
        for field_info in model.model_fields.values():
            ann = field_info.annotation
            if _is_basemodel(ann) and ann not in seen:
                seen.add(ann)
                queue.append(ann)
        i += 1

    return queue


def _render_model_table(model: type[BaseModel]) -> str:
    """Render a BaseModel's fields as a markdown table."""
    lines = [
        f"### {model.__name__}",
        "",
        "| Field | Type | Required | Default | Description |",
        "|-------|------|----------|---------|-------------|",
    ]
    for name, field in model.model_fields.items():
        ftype = _type_display(field.annotation)
        required = "yes" if field.is_required() else "no"
        default = "-" if field.is_required() else repr(field.default)
        desc = field.description or "-"
        lines.append(f"| {name} | {ftype} | {required} | {default} | {desc} |")
    return "\n".join(lines)


# ---- llms.txt generation ----


def generate_llms_txt(namespaces: dict[str, Namespace]) -> str:
    """
    Generate an `llms.txt` index listing all methods across all namespaces.
    """
    lines = [
        "# Woodglue API",
        "",
        "> JSON-RPC 2.0 server",
        "",
        "## Methods",
        "",
    ]

    for prefix in sorted(namespaces):
        ns = namespaces[prefix]
        for node in walk_namespace(ns):
            teaser = _docstring_teaser(node.method.doc)
            qualified = f"{prefix}.{node.nsref}"
            if teaser:
                lines.append(f"- {qualified}: {teaser}")
            else:
                lines.append(f"- {qualified}:")
    lines.append("")
    return "\n".join(lines)


# ---- Per-method markdown generation ----


def generate_method_markdown(prefix: str, method_name: str, ns: Namespace) -> str:
    """
    Generate full markdown documentation for a single method.
    """
    node = ns.get(method_name)
    method = node.method
    qualified = f"{prefix}.{method_name}"

    doc = method.doc or ""
    teaser = _docstring_teaser(doc)
    full_doc = doc.strip() if doc else teaser

    lines = [
        f"# {qualified}",
        "",
        full_doc,
        "",
        "## Parameters",
        "",
        "| Name | Type | Required | Description |",
        "|------|------|----------|-------------|",
    ]

    for arg in method.args:
        atype = _type_display(arg.annotation)
        required = "no" if arg.is_optional else "yes"
        desc = arg.description or "-"
        lines.append(f"| {arg.name} | {atype} | {required} | {desc} |")

    # Return type
    ret = method.return_annotation
    if ret is not None and ret is not inspect.Parameter.empty:
        lines.extend(["", "## Returns", "", f"`{_type_display(ret)}`"])

    # Referenced models
    models = _collect_referenced_models(method)
    if models:
        lines.extend(["", "## Referenced Models", ""])
        for model in models:
            lines.append(_render_model_table(model))
            lines.append("")

    lines.append("")
    return "\n".join(lines)


# ---- OpenAPI generation ----


def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema fragment."""
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
        return annotation.model_json_schema()
    return {"type": "string"}


def _json_safe_default(value: Any) -> Any:
    """Return a JSON-serializable representation of a default value."""
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    return str(value)


def generate_openapi_spec(namespaces: dict[str, Namespace]) -> dict[str, Any]:
    """Build an OpenAPI 3.0.3 spec dict from multiple namespaces."""
    paths: dict[str, Any] = {}

    for prefix in sorted(namespaces):
        ns = namespaces[prefix]
        for node in walk_namespace(ns):
            method = node.method
            qualified = f"{prefix}.{node.nsref}"
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_docs.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Run full lint and test suite**

Run: `make lint && make test`
Expected: Clean lint, all tests pass

- [ ] **Step 7: Delete old docs module and tests**

```bash
git rm src/woodglue/apps/docs.py tests/test_docs.py
```

- [ ] **Step 8: Commit**

```bash
git add src/woodglue/apps/llm_docs.py src/woodglue/hello/__init__.py tests/test_llm_docs.py
git commit -m "feat: LLM-friendly docs generation (llms.txt, markdown, OpenAPI)"
```

---

### Task 6: Serve Documentation via Tornado

**Files:**
- Modify: `src/woodglue/apps/llm_docs.py` (add Tornado handlers)
- Modify: `src/woodglue/apps/server.py` (mount doc routes)
- Create: `tests/test_llm_docs_handlers.py`

- [ ] **Step 1: Write failing tests for doc HTTP handlers**

Create `tests/test_llm_docs_handlers.py`:

```python
"""Tests for LLM docs Tornado handlers."""

from __future__ import annotations

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
    ns.register(some_method, nsref="some_method")
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
        assert "demo.some_method:" in body

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_docs_handlers.py -v`
Expected: FAIL — routes not mounted, 404s

- [ ] **Step 3: Add Tornado handlers to `llm_docs.py`**

Add at the end of `src/woodglue/apps/llm_docs.py`:

```python
import tornado.web
from typing_extensions import override


class LlmsTxtHandler(tornado.web.RequestHandler):
    """GET /docs/llms.txt"""

    @override
    def get(self) -> None:
        namespaces: dict[str, Namespace] = self.application.settings["namespaces"]
        self.set_header("Content-Type", "text/plain; charset=utf-8")
        self.write(generate_llms_txt(namespaces))


class MethodDocHandler(tornado.web.RequestHandler):
    """GET /docs/methods/{prefix}.{method}.md"""

    @override
    def get(self, filename: str) -> None:
        namespaces: dict[str, Namespace] = self.application.settings["namespaces"]

        # filename is "prefix.method_name.md"
        if not filename.endswith(".md"):
            raise tornado.web.HTTPError(404)
        name = filename[:-3]  # strip .md

        dot_pos = name.find(".")
        if dot_pos < 0:
            raise tornado.web.HTTPError(404)

        prefix = name[:dot_pos]
        method_name = name[dot_pos + 1 :]

        ns = namespaces.get(prefix)
        if ns is None:
            raise tornado.web.HTTPError(404)

        try:
            md = generate_method_markdown(prefix, method_name, ns)
        except KeyError:
            raise tornado.web.HTTPError(404)

        self.set_header("Content-Type", "text/markdown; charset=utf-8")
        self.write(md)


class OpenApiHandler(tornado.web.RequestHandler):
    """GET /docs/openapi.json"""

    @override
    def get(self) -> None:
        namespaces: dict[str, Namespace] = self.application.settings["namespaces"]
        self.set_header("Content-Type", "application/json")
        self.write(generate_openapi_spec(namespaces))
```

Also move the `import tornado.web` and `from typing_extensions import override` to the top-level imports at the top of the file.

- [ ] **Step 4: Mount doc routes in `server.py`**

Update `src/woodglue/apps/server.py`:

```python
"""Tornado application factory for the woodglue JSON-RPC server."""

from __future__ import annotations

import tornado.web
from lythonic.compose.namespace import Namespace

from woodglue.apps.rpc import JsonRpcHandler
from woodglue.config import WoodglueConfig


def create_app(
    namespaces: dict[str, Namespace],
    config: WoodglueConfig | None = None,
) -> tornado.web.Application:
    """
    Build a Tornado Application with JSON-RPC and optional docs/UI routes.

    `namespaces` maps prefix strings to loaded Namespace instances.
    """
    if config is None:
        config = WoodglueConfig(namespaces={})

    handlers: list[tuple[str, type[tornado.web.RequestHandler], ...]] = [
        (r"/rpc", JsonRpcHandler),
    ]

    if config.docs.enabled:
        from woodglue.apps.llm_docs import LlmsTxtHandler, MethodDocHandler, OpenApiHandler

        handlers.append((r"/docs/llms\.txt", LlmsTxtHandler))
        handlers.append((r"/docs/methods/(.+)", MethodDocHandler))
        if config.docs.openapi:
            handlers.append((r"/docs/openapi\.json", OpenApiHandler))

    return tornado.web.Application(
        handlers,
        namespaces=namespaces,
        config=config,
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_llm_docs_handlers.py -v`
Expected: All 4 tests PASS

Run: `make lint && make test`
Expected: Clean lint, all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/woodglue/apps/llm_docs.py src/woodglue/apps/server.py tests/test_llm_docs_handlers.py
git commit -m "feat: serve llms.txt, method markdown, and OpenAPI via Tornado"
```

---

### Task 7: JS UI Scaffolding

**Files:**
- Create: `src/woodglue/ui/package.json`
- Create: `src/woodglue/ui/vite.config.js`
- Create: `src/woodglue/ui/index.html`
- Create: `src/woodglue/ui/src/main.js`
- Create: `src/woodglue/ui/src/style.css`
- Modify: `src/woodglue/apps/server.py` (serve static UI)
- Modify: `Makefile` (add `ui-build` target)

- [ ] **Step 1: Initialize Vite project**

```bash
cd src/woodglue/ui
npm init -y
npm install --save-dev vite
npm install marked
```

- [ ] **Step 2: Create `vite.config.js`**

Create `src/woodglue/ui/vite.config.js`:

```js
import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  base: "/ui/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
```

- [ ] **Step 3: Create `index.html`**

Create `src/woodglue/ui/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Woodglue API Docs</title>
  <link rel="stylesheet" href="/ui/src/style.css">
</head>
<body>
  <div id="app">
    <nav id="sidebar">
      <h1>Woodglue</h1>
      <div id="nav-tree"></div>
    </nav>
    <main id="content">
      <div id="doc-content">
        <p>Select a method from the sidebar.</p>
      </div>
    </main>
  </div>
  <script type="module" src="/ui/src/main.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create `style.css`**

Create `src/woodglue/ui/src/style.css`:

```css
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1a1a1a;
  background: #f5f5f5;
  line-height: 1.5;
}

#app {
  display: flex;
  min-height: 100vh;
}

#sidebar {
  width: 280px;
  background: #fff;
  border-right: 1px solid #ddd;
  padding: 1.5rem;
  overflow-y: auto;
  flex-shrink: 0;
}

#sidebar h1 {
  font-size: 1.2rem;
  margin-bottom: 1rem;
  color: #333;
}

#content {
  flex: 1;
  padding: 2rem;
  overflow-y: auto;
}

.ns-group { margin-bottom: 1rem; }
.ns-name {
  font-weight: 600;
  font-size: 0.9rem;
  color: #555;
  padding: 0.3rem 0;
  cursor: pointer;
}
.ns-name:hover { color: #111; }

.method-link {
  display: block;
  padding: 0.2rem 0 0.2rem 1rem;
  font-size: 0.85rem;
  color: #0066cc;
  text-decoration: none;
  cursor: pointer;
}
.method-link:hover { text-decoration: underline; }
.method-link.active { font-weight: 600; color: #003d7a; }

#doc-content {
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 6px;
  padding: 2rem;
  max-width: 900px;
}

#doc-content h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }
#doc-content h2 { font-size: 1.1rem; margin: 1.2rem 0 0.5rem; color: #333; }
#doc-content h3 { font-size: 1rem; margin: 1rem 0 0.4rem; color: #555; }

#doc-content table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
  margin: 0.5rem 0;
}
#doc-content th, #doc-content td {
  text-align: left;
  padding: 0.35rem 0.6rem;
  border-bottom: 1px solid #eee;
}
#doc-content th { background: #fafafa; font-weight: 600; }

#doc-content code {
  background: #f0f0f0;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  font-size: 0.85em;
}

#doc-content pre {
  background: #f5f5f5;
  padding: 1rem;
  border-radius: 4px;
  overflow-x: auto;
  margin: 0.5rem 0;
}
#doc-content pre code { background: none; padding: 0; }
```

- [ ] **Step 5: Create `main.js`**

Create `src/woodglue/ui/src/main.js`:

```js
import { marked } from "marked";

const navTree = document.getElementById("nav-tree");
const docContent = document.getElementById("doc-content");

async function loadIndex() {
  const resp = await fetch("/docs/llms.txt");
  const text = await resp.text();
  return parseLlmsTxt(text);
}

function parseLlmsTxt(text) {
  const namespaces = {};
  for (const line of text.split("\n")) {
    const match = line.match(/^- (\S+?):\s*(.*)/);
    if (!match) continue;
    const [, qualified, teaser] = match;
    const dot = qualified.indexOf(".");
    if (dot < 0) continue;
    const prefix = qualified.substring(0, dot);
    const method = qualified.substring(dot + 1);
    if (!namespaces[prefix]) namespaces[prefix] = [];
    namespaces[prefix].push({ method, teaser, qualified });
  }
  return namespaces;
}

function renderNav(namespaces) {
  navTree.innerHTML = "";
  for (const [prefix, methods] of Object.entries(namespaces).sort()) {
    const group = document.createElement("div");
    group.className = "ns-group";

    const title = document.createElement("div");
    title.className = "ns-name";
    title.textContent = prefix;
    group.appendChild(title);

    for (const { method, qualified } of methods) {
      const link = document.createElement("a");
      link.className = "method-link";
      link.textContent = method;
      link.dataset.qualified = qualified;
      link.addEventListener("click", () => loadMethod(qualified, link));
      group.appendChild(link);
    }

    navTree.appendChild(group);
  }
}

async function loadMethod(qualified, linkEl) {
  document.querySelectorAll(".method-link.active").forEach((el) => el.classList.remove("active"));
  if (linkEl) linkEl.classList.add("active");

  const resp = await fetch(`/docs/methods/${qualified}.md`);
  if (!resp.ok) {
    docContent.innerHTML = `<p>Failed to load documentation for ${qualified}.</p>`;
    return;
  }
  const md = await resp.text();
  docContent.innerHTML = marked.parse(md);
}

async function init() {
  try {
    const namespaces = await loadIndex();
    renderNav(namespaces);
  } catch (err) {
    navTree.innerHTML = `<p>Failed to load API index.</p>`;
    console.error(err);
  }
}

init();
```

- [ ] **Step 6: Build the UI**

```bash
cd src/woodglue/ui && npx vite build
```

Verify `src/woodglue/ui/dist/` contains `index.html` and asset files.

- [ ] **Step 7: Add `.gitignore` for UI node_modules**

Create `src/woodglue/ui/.gitignore`:

```
node_modules/
```

- [ ] **Step 8: Mount UI static files in `server.py`**

Update `src/woodglue/apps/server.py` to serve the UI dist directory. Add after the docs route block:

```python
    if config.ui.enabled:
        ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
        if ui_dist.is_dir():
            handlers.append(
                (r"/ui/(.*)", tornado.web.StaticFileHandler, {"path": str(ui_dist), "default_filename": "index.html"})
            )
```

Add `from pathlib import Path` to the imports in `server.py`.

- [ ] **Step 9: Add `ui-build` target to Makefile**

Add to `Makefile`:

```makefile
ui-build:
	cd src/woodglue/ui && npm install && npx vite build
```

- [ ] **Step 10: Run full lint and test suite**

Run: `make lint && make test`
Expected: Clean lint, all tests pass

- [ ] **Step 11: Commit**

```bash
git add src/woodglue/ui/ src/woodglue/apps/server.py Makefile
git commit -m "feat: add Vite-built JS documentation UI served at /ui/"
```

---

### Task 8: Create Test Data Directory

**Files:**
- Create: `data/woodglue.yaml` (example config for local dev)

- [ ] **Step 1: Create example config**

Create `data/woodglue.yaml`:

```yaml
namespaces:
  hello: "woodglue.hello:ns"

docs:
  enabled: true
  openapi: true

ui:
  enabled: true
```

- [ ] **Step 2: Verify server starts with the config**

```bash
uv run wgl server start
```

Expected output:
```
Woodglue server listening on http://127.0.0.1:5321
  RPC endpoint: http://127.0.0.1:5321/rpc
  LLM docs:     http://127.0.0.1:5321/docs/llms.txt
  UI:           http://127.0.0.1:5321/ui/
```

Stop with Ctrl+C after verifying.

- [ ] **Step 3: Commit**

```bash
git add data/woodglue.yaml
git commit -m "feat: add example config for local development"
```

---

### Task 9: Final Cleanup

**Files:**
- Verify all old references are removed
- Final lint + test pass

- [ ] **Step 1: Verify `docs.py` is deleted**

```bash
ls src/woodglue/apps/docs.py 2>/dev/null && echo "STILL EXISTS" || echo "OK - deleted"
```

Expected: `OK - deleted`

- [ ] **Step 2: Check for stale imports of old docs module**

Search for any remaining imports of `woodglue.apps.docs`:

```bash
uv run ruff check src/ tests/
```

Fix any import errors found.

- [ ] **Step 3: Run full suite**

Run: `make lint && make test`
Expected: Clean lint, all tests pass, no skipped tests

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "chore: final cleanup after config/docs/UI refactor"
```
