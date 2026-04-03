# Config, Multi-Namespace, LLM Docs, and UI Scaffolding

## Overview

Woodglue needs four interconnected improvements: a YAML-based server config,
multi-namespace support, LLM-friendly API documentation, and a JavaScript
documentation UI. All four build against a shared config schema designed
upfront.

**Implementation order:** Config + Multi-Namespace → BaseModel Round-Trip →
LLM Docs Generation → JS UI Scaffolding.

---

## Sub-Project 1: YAML Config + Multi-Namespace

### Config File

The server's `data` directory (CLI `Server.data`, default `./data`) is the root
for all server state. The config file lives at `{data}/woodglue.yaml` and is
**required** to run the server.

```yaml
# ./data/woodglue.yaml
namespaces:
  hello: "woodglue.hello:ns"
  my_api: "myproject.api:ns"

docs:
  enabled: true
  openapi: true

ui:
  enabled: true
```

### Pydantic Config Models

```python
class DocsConfig(BaseModel):
    enabled: bool = True
    openapi: bool = True

class UiConfig(BaseModel):
    enabled: bool = True

class WoodglueConfig(BaseModel):
    namespaces: dict[str, str]  # prefix -> "module.path:instance", required
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()
```

The existing `Server` CLI model provides `host`, `port`, and `data`. These can
be overridden from the CLI while the YAML controls namespaces, docs, and UI.

### CLI Changes

- Remove `module_path` from `Server` (no backward compat).
- `start` command loads `{data}/woodglue.yaml`, fails if missing or if no
  namespaces are declared.
- CLI `host` and `port` override any future server-level config but the
  namespaces always come from the YAML.

### Multi-Namespace Routing

Single `/rpc` endpoint. Namespace prefix is prepended to method names using dot
notation, following JSON-RPC convention:

- Config: `hello: "woodglue.hello:ns"` with method `pydantic_hello`
- JSON-RPC method name: `hello.pydantic_hello`

At startup, iterate `config.namespaces`, load each `Namespace` via `GlobalRef`,
store in a `dict[str, Namespace]`.

The handler splits the method name on the first dot to resolve
`prefix.method_name` → namespace lookup → `ns.get(method_name)`.

If the method name has no dot prefix, return `METHOD_NOT_FOUND`.

---

## Sub-Project 2: BaseModel Round-Trip Serialization

### Deserialization (Input)

In `JsonRpcHandler.post()`, after building `kwargs` from params, check each
argument's type annotation. If it is a `BaseModel` subclass, deserialize the
raw dict into the declared type:

```python
for arg_info in method_args:
    if (arg_info.name in kwargs
        and isinstance(arg_info.annotation, type)
        and issubclass(arg_info.annotation, BaseModel)):
        kwargs[arg_info.name] = arg_info.annotation.model_validate(
            kwargs[arg_info.name]
        )
```

### Serialization (Output)

Already handled by `_serialize_result`:
- `BaseModel` → `model_dump(mode="json")`
- Lists and dicts are recursively serialized
- Primitives pass through, other types → `str()`

### Testing

Round-trip test using `pydantic_hello`:
- Send `{"name": "Alice", "age": 30}` as the `input` param
- Verify response result is `{"eman": "ecilA", "ega": -30}`

---

## Sub-Project 3: LLM-Friendly Documentation

### Generated Artifacts

All served by Tornado when `docs.enabled: true`:

| Endpoint | Content |
|----------|---------|
| `GET /docs/llms.txt` | Index of all methods across all namespaces |
| `GET /docs/methods/{prefix}.{method}.md` | Per-method markdown |
| `GET /docs/openapi.json` | OpenAPI 3.0.3 spec (if `docs.openapi: true`) |

### `llms.txt` Format

```
# Woodglue API

> JSON-RPC 2.0 server

## Methods

- hello.hello: Returns the length of a name
- hello.pydantic_hello: Reverses name and negates age
```

The teaser is the first line of the method's docstring. Full docstring content
goes in the per-method markdown.

### Per-Method Markdown Structure

```markdown
# hello.pydantic_hello

Reverses name and negates age.

Full docstring content here, including examples...

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| input | HelloIn | yes | - |

## Returns

`HelloOut`

## Referenced Models

### HelloIn

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| name | str | yes | - | - |
| age | int | yes | - | - |

### HelloOut

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| eman | str | yes | - | - |
| ega | int | yes | - | - |
```

All BaseModels encountered recursively (in parameters and return type) are
flattened into the "Referenced Models" section within the same document,
with internal anchors for cross-referencing.

### Docstring Convention

- First line: short summary (used as teaser in `llms.txt`)
- Remaining lines: full description, usage examples, notes
- This mirrors standard Python docstring conventions (PEP 257)

### Generation

Docs are generated at server startup by walking all loaded namespaces. A
`woodglue.apps.llm_docs` module handles generation. The existing
`walk_namespace` function from `docs.py` is reused.

The existing `docs.py` module (OpenAPI spec + inline HTML) is refactored:
- `generate_openapi_spec` moves to or is called from `llm_docs`
- The inline HTML docs (`DocsUiHandler`, `_HTML_TEMPLATE`) are removed,
  replaced by the JS UI

---

## Sub-Project 4: JS UI Scaffolding

### Architecture

- A JavaScript SPA served at `/ui/` when `ui.enabled: true`
- Built with Vite at development time
- Build output lives in `src/woodglue/ui/dist/`
- Tornado serves the built assets via `StaticFileHandler`
- The app fetches `/docs/llms.txt`, `/docs/methods/*.md`, and
  `/docs/openapi.json` from the same origin

### UI Structure

- **Left sidebar:** namespace list, expandable to show methods per namespace
- **Main content area:** rendered markdown for the selected method
- **Markdown rendering:** client-side via a library (e.g., `marked`) with
  syntax highlighting for code blocks

### Toggle

When `ui.enabled: false`, the `/ui/` routes are not mounted. The `/docs/*`
endpoints remain available regardless (controlled by `docs.enabled`).

### Scope Boundary

This spec covers the scaffolding: project setup, build pipeline, Tornado
integration, and basic navigation shell. Detailed UI design (component library,
styling, interactions) is a separate brainstorming cycle once the backend
(sub-projects 1-3) is in place.

---

## Files Affected

| File | Changes |
|------|---------|
| `src/woodglue/config.py` | New `WoodglueConfig`, `DocsConfig`, `UiConfig` models, YAML loader |
| `src/woodglue/cli.py` | Remove `module_path`, load config from `{data}/woodglue.yaml` |
| `src/woodglue/apps/rpc.py` | Multi-namespace dispatch, BaseModel deserialization |
| `src/woodglue/apps/server.py` | Accept config, mount routes conditionally |
| `src/woodglue/apps/docs.py` | Refactor into `llm_docs.py`, remove inline HTML |
| `src/woodglue/apps/llm_docs.py` | New: llms.txt, per-method markdown, OpenAPI generation |
| `src/woodglue/ui/` | New: Vite JS project for documentation UI |
| `tests/` | Round-trip BaseModel test, multi-namespace tests, docs generation tests |
