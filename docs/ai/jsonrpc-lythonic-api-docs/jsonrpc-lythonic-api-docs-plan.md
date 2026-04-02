# JSON-RPC API with auto-discovery and OpenAPI docs — implementation plan

**Task:** docs/ai/jsonrpc-lythonic-api-docs/jsonrpc-lythonic-api-docs-task.md
**Complexity:** medium
**Mode:** sub-agents
**Parallel:** true

## Design decisions

### DD-1: Extend existing Server model for CLI and create_app

**Decision:** Add `port`, `host`, and `module_path` fields to the existing `Server(BaseModel)` in `cli.py:10-14`. `ActionTree` reads `Field(default=..., description=...)` for CLI help text. `create_app` accepts this `Server` instance. Note: `auth.py:122` defines a separate `ServerConfig` — avoid that name.
**Rationale:** `cli.py:10-14` already uses `Server(BaseModel)` with `ActionTree`, which extracts Field defaults and descriptions for CLI `--help`. Adding fields to this model surfaces them in CLI automatically.
**Alternative:** A separate config dict passed to `create_app` duplicates defaults between CLI arg parsing and app creation.

### DD-2: Synthetic per-method OpenAPI paths

**Decision:** Model each JSON-RPC method as `POST /rpc/{nsref}` in the OpenAPI spec. Each method gets its own expandable Swagger UI section with typed params.
**Rationale:** Swagger UI renders one section per path, letting users browse and test methods individually. A note in the spec description clarifies that the actual transport uses `POST /rpc` with a JSON-RPC 2.0 envelope.
**Alternative:** A single `POST /rpc` with `oneOf` schema matches the spec but collapses all methods under one Swagger UI path.

### DD-3: Support both positional and named params

**Decision:** Accept `params` as a list (positional, zipped with `Method.args` order) or a dict (named kwargs). The handler requires ~5 extra lines.
**Rationale:** Full JSON-RPC 2.0 compliance. `Method.args` provides ordered `ArgInfo` entries, making positional-to-named mapping straightforward.
**Alternative:** Named-only params simplify the handler but break spec compliance.

### DD-4: Use isawaitable for async dispatch

**Decision:** Call `node(*args, **kwargs)`, then check `inspect.isawaitable(result)` and `await` if true.
**Rationale:** Handles async functions, DAG wrappers (always async), and decorated callables regardless of how `NamespaceNode.__call__` delegates internally.
**Alternative:** `inspect.iscoroutinefunction(node.method.o)` misses decorated callables where `_decorated` is async but `method.o` is synchronous.

### DD-5: walk_namespace helper using private attrs

**Decision:** Write `walk_namespace(ns, prefix="")` in `docs.py` that recursively reads `ns._branches` and `ns._leaves`, yielding `NamespaceNode` objects (which carry `node.nsref`).
**Rationale:** Namespace exposes no public iteration method. The constraint allows private attr fallback when no public method exists.
**Alternative:** Adding `__iter__` to Namespace violates the "leave lythonic unmodified" constraint.

### DD-6: Local Python-to-JSON-Schema type mapping

**Decision:** Build a `python_type_to_schema(annotation)` function in `docs.py`. Map `int→integer`, `float→number`, `str→string`, `bool→boolean`, `BaseModel→object` with `model_json_schema()`, fallback `string`.
**Rationale:** `lythonic.types.KNOWN_TYPES` handles serialization (string/json/db conversion), not JSON Schema type descriptors.
**Alternative:** Using KNOWN_TYPES creates an impedance mismatch requiring an adapter layer.

## Tasks

### Task 1: Auto-discovery module

- **Files:** `src/woodglue/apps/discovery.py` (create)
- **Depends on:** none
- **Scope:** S
- **What:** Implement `auto_discover(module_path: str) -> Namespace`. Import the target module via `importlib.import_module`, find public callables via `inspect.getmembers(module, inspect.isfunction)`, filter out `_`-prefixed names, register each into a fresh `Namespace` via `ns.register(func)`.
- **How:**
  1. `importlib.import_module(module_path)` loads the target
  2. If the module is a package (`hasattr(module, '__path__')`), `pkgutil.walk_packages` recurses into submodules
  3. `inspect.getmembers(mod, inspect.isfunction)` yields members; filter with `not name.startswith('_')`
  4. `ns.register(func)` registers each function — GlobalRef auto-derives `nsref` from `module:name`
  5. Return the populated Namespace
- **Context:** `.venv/lib/python3.12/site-packages/lythonic/compose/namespace.py:180-216` (register method), `src/woodglue/workflow.py:1-6` (GlobalRef + Method import pattern)
- **Verify:** `uv run python -c "from woodglue.apps.discovery import auto_discover; ns = auto_discover('json'); print(ns._leaves)"` — prints discovered functions

### Task 2: JSON-RPC handler

- **Files:** `src/woodglue/apps/rpc.py` (create)
- **Depends on:** none
- **Scope:** M
- **What:** Implement `JsonRpcHandler(tornado.web.RequestHandler)` with `async def post(self)`. Parse the JSON-RPC 2.0 request body, dispatch to `Namespace.get(method)`, validate params against `Method.args`, handle async callables, return a JSON-RPC 2.0 response with standard error codes.
- **How:**
  1. Define error code constants: `-32700`, `-32600`, `-32601`, `-32602`, `-32603`
  2. `json.loads(self.request.body)` — catch `json.JSONDecodeError` → error `-32700`
  3. Validate required fields (`jsonrpc`, `method`) — missing fields → error `-32600`
  4. `ns = self.application.settings['namespace']`, `node = ns.get(method)` — `KeyError` → error `-32601`
  5. If `params` is a list: zip with `node.method.args` names to build a kwargs dict
  6. Validate required params via `ArgInfo.is_optional` — missing required param → error `-32602`
  7. `result = node(**kwargs)`, if `inspect.isawaitable(result)`: `result = await result`
  8. Serialize result: if `isinstance(result, BaseModel)` → `result.model_dump(mode="json")`, if primitive → pass through, otherwise `str(result)` as fallback
  9. Catch all exceptions → error `-32603`
  10. `self.write({"jsonrpc": "2.0", "result": result, "id": request_id})`
- **Context:** `.venv/lib/python3.12/site-packages/lythonic/compose/__init__.py:35-90` (ArgInfo — name, is_optional, annotation), `.venv/lib/python3.12/site-packages/lythonic/compose/namespace.py:264-270` (Namespace.get)
- **Verify:** `uv run python -c "from woodglue.apps.rpc import JsonRpcHandler; print('ok')"` — imports cleanly

### Task 3: OpenAPI spec and HTML docs

- **Files:** `src/woodglue/apps/docs.py` (create)
- **Depends on:** none
- **Scope:** M
- **What:** Implement `walk_namespace(ns)` helper, `generate_openapi_spec(ns) -> dict`, `DocsHandler` (GET `/docs` → OpenAPI JSON), and `DocsUiHandler` (GET `/docs/ui` → self-contained inline HTML docs page).
- **How:**
  1. `walk_namespace(ns, prefix="")`: recursively iterate `ns._branches` and `ns._leaves`, yield each `NamespaceNode`
  2. `python_type_to_schema(annotation)`: map Python types to JSON Schema types (DD-6)
  3. `generate_openapi_spec(ns)`: for each node from `walk_namespace`, create a path entry `POST /rpc/{nsref}` with request body schema from `Method.args` (ArgInfo fields) and response schema from `Method.return_annotation`
  4. `DocsHandler.get()`: `self.write(generate_openapi_spec(self.application.settings['namespace']))`
  5. `DocsUiHandler.get()`: generate a self-contained HTML page with inline CSS. Render a table per method listing name, docstring, params (name, type, default, description), and return type. No external JS or CDN dependencies — all content inline.
- **Context:** `.venv/lib/python3.12/site-packages/lythonic/compose/namespace.py:145-170` (Namespace._branches, _leaves), `.venv/lib/python3.12/site-packages/lythonic/compose/__init__.py:35-90` (ArgInfo fields, Method.doc, Method.return_annotation)
- **Verify:** `uv run python -c "from woodglue.apps.docs import generate_openapi_spec; print('ok')"` — imports cleanly

### Task 4: Server factory and CLI integration

- **Files:** `src/woodglue/apps/server.py` (create), `src/woodglue/cli.py` (edit lines 10-23)
- **Depends on:** Task 1, Task 2, Task 3
- **Scope:** S
- **What:** Create `create_app(config, namespace) -> tornado.web.Application`. Extend `Server(BaseModel)` in `cli.py` with `port`, `host`, `module_path` fields. Wire the `start` command to call `auto_discover`, `create_app`, and start the IOLoop.
- **How:**
  1. `server.py`: `create_app(config: Server, namespace: Namespace)` → `tornado.web.Application([(r"/rpc", JsonRpcHandler), (r"/docs", DocsHandler), (r"/docs/ui", DocsUiHandler)], namespace=namespace)`
  2. `cli.py`: add fields to `Server`:
     ```python
     port: int = Field(default=8888, description="port to listen on")
     host: str = Field(default="127.0.0.1", description="host to bind to")
     module_path: str = Field(default="", description="Python module path to auto-discover methods from")
     ```
  3. `start(ctx)`: `config = ctx.path.get('/server')`, call `auto_discover(config.module_path)`, `create_app(config, ns)`, `app.listen(config.port, config.host)`, `IOLoop.current().start()`
- **Context:** `src/woodglue/cli.py:1-38` (existing Server model, ActionTree, start function), `.venv/lib/python3.12/site-packages/lythonic/compose/cli.py` (RunContext.path)
- **Verify:** `uv run wgl server start --help` — shows `--port`, `--host`, `--module_path` with defaults and descriptions

### Task 5: Tests

- **Files:** `tests/test_discovery.py` (create), `tests/test_rpc.py` (create), `tests/test_docs.py` (create)
- **Depends on:** Task 1, Task 2, Task 3, Task 4
- **Scope:** M
- **What:** Write tests for all three modules using pytest + `tornado.testing.AsyncHTTPTestCase`.
- **How:**
  1. `test_discovery.py`: create a fixture module with 2 public functions + 1 private. Call `auto_discover`, verify `ns.get()` finds public functions and raises KeyError for private ones.
  2. `test_rpc.py`: subclass `AsyncHTTPTestCase`, `get_app()` returns an app with a test Namespace (register 2 test functions — one sync, one async). Test: valid call → result, unknown method → -32601, bad JSON → -32700, missing required param → -32602, async callable → correct result, positional params → correct mapping.
  3. `test_docs.py`: subclass `AsyncHTTPTestCase`. Test: GET /docs → valid OpenAPI JSON with paths for registered methods, GET /docs/ui → HTML 200 with swagger-ui reference.
- **Context:** `tests/test_auth.py` (existing test pattern), `pyproject.toml:173-189` (pytest config, asyncio_mode=auto)
- **Verify:** `uv run pytest tests/test_discovery.py tests/test_rpc.py tests/test_docs.py -v` — all pass

### Task 6: Validation

- **Files:** —
- **Depends on:** all
- **Scope:** S
- **What:** Run full validation: lint, type-check, all tests.
- **Context:** —
- **Verify:** `uv run ruff check src/ tests/ && uv run pytest tests/ src/ -v` — all pass, no regressions

## Execution

- **Mode:** sub-agents
- **Parallel:** true
- **Reasoning:** 6 tasks. Tasks 1-3 share no files and have no dependencies — parallelize them. Task 4 depends on all three. Single codebase, no cross-layer concerns.
- **Order:**
  ```
  Group 1 (parallel): Task 1, Task 2, Task 3
  ─── barrier ───
  Group 2 (sequential): Task 4
  ─── barrier ───
  Group 3 (sequential): Task 5
  ─── barrier ───
  Group 4 (sequential): Task 6
  ```

## Verification

- `uv run pytest tests/ src/` — all existing and new tests pass
- `curl -X POST http://localhost:8888/rpc -d '{"jsonrpc":"2.0","method":"<discovered_method>","params":{},"id":1}'` — returns `{"jsonrpc":"2.0","result":...,"id":1}`
- `curl -X POST http://localhost:8888/rpc -d '{"jsonrpc":"2.0","method":"nonexistent","params":{},"id":1}'` — returns error code `-32601`
- `curl -X POST http://localhost:8888/rpc -d 'not json'` — returns error code `-32700`
- `curl http://localhost:8888/docs` — returns valid OpenAPI 3.x JSON listing all discovered methods
- `curl http://localhost:8888/docs/ui` — returns self-contained HTML page listing all methods
- Call a method with a required param omitted — returns error `-32602`
- Call an async method — executes and returns the correct response
- `uv run wgl server start --help` — shows `--port`, `--host`, `--module_path` with defaults and descriptions

## Materials

- `src/woodglue/cli.py:19-23` — server start command, integration point
- `src/woodglue/auth.py` — Pydantic model patterns (Principal, Grant)
- `src/woodglue/workflow.py` — GlobalRef + Method usage pattern
- `.venv/lib/python3.12/site-packages/lythonic/compose/namespace.py` — Namespace, NamespaceNode
- `.venv/lib/python3.12/site-packages/lythonic/compose/__init__.py` — Method, ArgInfo
