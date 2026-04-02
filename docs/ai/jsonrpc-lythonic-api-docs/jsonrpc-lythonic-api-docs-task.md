# JSON-RPC API with auto-discovery and OpenAPI docs

**Slug:** jsonrpc-lythonic-api-docs
**Ticket:** —
**Complexity:** medium
**Type:** general

## Task

Build two Tornado routes: a JSON-RPC 2.0 endpoint that auto-discovers and dispatches methods from a lythonic `Namespace`, and a documentation route that serves OpenAPI 3.x spec plus human-readable HTML for those methods.

## Context

### Architecture

Request flow after implementation:

```
Client → POST /rpc → JsonRpcHandler → Namespace.get(method) → NamespaceNode.__call__(**params) → JSON-RPC response
Client → GET /docs → DocsHandler → walk Namespace tree → OpenAPI 3.x JSON
Client → GET /docs/ui → DocsHandler → render HTML from OpenAPI spec
```

`cli.py:19` runs the `server start` command, which creates a `tornado.web.Application` with routes and starts `tornado.httpserver.HTTPServer` on `tornado.ioloop.IOLoop`.

lythonic `Namespace` (`lythonic/compose/namespace.py`) stores methods in a tree: `_branches` (dict of child `Namespace`) and `_leaves` (dict of `NamespaceNode`). Each `NamespaceNode` wraps a `Method` with an `nsref` path like `"market.data:fetch_prices"`.

### Files to change

- `src/woodglue/cli.py:19-23` — wire Tornado app creation and IOLoop start into the `start` command
- `src/woodglue/apps/` (empty) — add new modules:
  - `rpc.py` — `JsonRpcHandler(tornado.web.RequestHandler)` for JSON-RPC 2.0
  - `docs.py` — `DocsHandler` for OpenAPI spec + HTML
  - `server.py` — `create_app(namespace)` factory, binds routes to handlers
  - `discovery.py` — auto-discover callables from a Python package, register into Namespace

### Patterns to reuse

**lythonic introspection** — `Method.args` returns `list[ArgInfo]` with `name`, `annotation`, `default`, `is_optional`, `description`. `Method.doc` returns the docstring. `Method.return_annotation` returns the return type. Use these for both JSON-RPC param validation and OpenAPI schema generation.

**Namespace registration** — `ns.register(callable, nsref="pkg:func_name")` wraps the callable in a `NamespaceNode`. `ns.get("pkg:func_name")` retrieves it. For auto-discovery: `importlib` + `inspect.getmembers()` → filter public callables → `ns.register()` each.

**Namespace tree walking** — `ns._branches` and `ns._leaves` are dicts. Recursively walk `_branches` to collect all `NamespaceNode` leaves with their `nsref` paths.

**Pydantic models in project** — `auth.py`, `service.py`, `workflow.py` all use `BaseModel` with `Field(description=...)`. Follow this pattern for JSON-RPC request/response models.

**Existing CLI pattern** — `cli.py` uses `ActionTree` from lythonic. `RunContext` provides `ctx.path` for config. The `start` function (line 19) receives `RunContext` — extract server config (port, host, namespace module path) from it.

### Tests

No existing tests cover Tornado or API functionality. `tests/test_auth.py`, `tests/test_git.py`, `tests/test_caddy.py` exist but are unrelated.

Test pattern: pytest with `asyncio_mode = "auto"`, `--doctest-modules`, `--cov=src`. Use `tornado.testing.AsyncHTTPTestCase` for handler tests.

## Requirements

1. **JSON-RPC handler** — accept `POST /rpc` with a JSON-RPC 2.0 body (`{"jsonrpc": "2.0", "method": "namespace:func", "params": {...}, "id": 1}`). Dispatch to the matching `NamespaceNode` via `Namespace.get(method)`. Return a JSON-RPC 2.0 response with `result` or `error`.
2. **Auto-discovery** — at server startup, scan a given Python module/package path, find all public callables (functions, excluding classes and `_`-prefixed names), and register each into a `Namespace` with an `nsref` derived from module + function name.
3. **Parameter validation** — validate `params` against `Method.args` before calling. Return JSON-RPC error code `-32602` (Invalid params) on type mismatch or missing required params.
4. **JSON-RPC error codes** — implement standard codes: `-32700` (Parse error), `-32600` (Invalid Request), `-32601` (Method not found), `-32602` (Invalid params), `-32603` (Internal error).
5. **OpenAPI spec route** — `GET /docs` returns OpenAPI 3.x JSON. Each discovered method maps to a POST operation under `/rpc`. Derive request body schema properties from `ArgInfo` params. Derive response schema from `Method.return_annotation`.
6. **Human-readable docs route** — `GET /docs/ui` serves a self-contained HTML page listing all methods with their params, types, defaults, descriptions, and return types. Inline everything — no external JS dependencies.
7. **Server factory** — `create_app(namespace: Namespace) -> tornado.web.Application` wires handlers to routes. The `start` command in `cli.py` creates the Namespace, runs auto-discovery, calls `create_app`, and starts the IOLoop.
8. **Async support** — if a `NamespaceNode`'s underlying callable is async (`inspect.iscoroutinefunction`), `await` it; otherwise call it synchronously.

## Constraints

- Consume only lythonic's public API (`Namespace`, `Method`, `ArgInfo`, `NamespaceNode`, `GlobalRef`) — leave lythonic package code unmodified.
- Ship without authentication. Defer `Grant`/`Principal` integration.
- Use only dependencies already in `pyproject.toml` — Tornado and Pydantic suffice.
- Prefer public iteration methods on `Namespace` over `_branches`/`_leaves`; fall back to private attrs only when no public method exists.
- Defer JSON-RPC batch requests (array of requests) to a future task.
- Limit changes to `src/woodglue/apps/` and `src/woodglue/cli.py` — leave existing tests and modules untouched.

## Verification

- `uv run pytest tests/ src/` — all existing and new tests pass
- `curl -X POST http://localhost:8888/rpc -d '{"jsonrpc":"2.0","method":"<discovered_method>","params":{},"id":1}'` — returns `{"jsonrpc":"2.0","result":...,"id":1}`
- `curl -X POST http://localhost:8888/rpc -d '{"jsonrpc":"2.0","method":"nonexistent","params":{},"id":1}'` — returns error with code `-32601`
- `curl -X POST http://localhost:8888/rpc -d 'not json'` — returns error with code `-32700`
- `curl http://localhost:8888/docs` — returns valid OpenAPI 3.x JSON with all discovered methods
- `curl http://localhost:8888/docs/ui` — returns self-contained HTML page listing all methods
- Call a method with a required param omitted — returns error `-32602`
- Call a discovered async method — executes correctly and returns a response

## Materials

- `src/woodglue/cli.py:19-23` — server start command, integration point
- `src/woodglue/auth.py` — Pydantic model patterns (Principal, Grant)
- `src/woodglue/workflow.py` — GlobalRef + Method usage pattern
- `.venv/lib/python3.12/site-packages/lythonic/compose/namespace.py` — Namespace, NamespaceNode
- `.venv/lib/python3.12/site-packages/lythonic/compose/__init__.py` — Method, ArgInfo
- `.venv/lib/python3.12/site-packages/lythonic/__init__.py` — GlobalRef
