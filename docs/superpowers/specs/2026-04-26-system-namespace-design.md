# System Namespace Design

**Date:** 2026-04-26

## Summary

Replace the auto-mounted `engine` namespace with a unified `system` namespace
that provides both server introspection (mounted namespaces, methods, cache
config) and engine management (DAG runs, triggers). The system namespace is
always mounted with `expose_api=True`, making its methods visible in
`llms.txt`, `/docs/*.md`, OpenAPI, and the UI.

The UI becomes a pure consumer of `system.*` RPC calls instead of combining
`llms.txt` parsing with engine API calls.

## Motivation

- The `engine` namespace was conditionally mounted and had `expose_api`
  defaulting to `True` only by accident of the `NamespaceEntry` default. Its
  methods didn't appear in docs because the entry used
  `NamespaceEntry(gref="builtin:engine")` without explicit `expose_api=True`.
- There's no API to introspect what namespaces are mounted or what methods
  they expose. The UI had to parse `llms.txt` to discover methods.
- Caching configuration is invisible — no way to know which methods have
  caching enabled without reading the config file.

## Design

### System namespace scope

A single `system` prefix with three groups of methods:

**Namespace introspection:**
- `system.list_namespaces()` — all mounted namespaces with config summary
- `system.list_methods(namespace)` — methods in a namespace with summary metadata
- `system.describe_method(namespace, nsref)` — full method detail

**Engine management (moved from `engine.*`):**
- `system.recent_runs(namespace, limit, status)`
- `system.active_runs(namespace)`
- `system.inspect_run(namespace, run_id)`
- `system.load_io(namespace, run_id, node_labels)`
- `system.child_runs(namespace, parent_run_id)`

**Trigger management (moved from `engine.*`):**
- `system.list_triggers(namespace)`
- `system.fire_trigger(namespace, name, payload)`
- `system.activate_trigger(namespace, name)`
- `system.deactivate_trigger(namespace, name)`

### Pydantic models

Shared models used by both introspection methods and RPC serialization.
These live in `apps/system_api.py`.

```python
class ArgInfo(BaseModel):
    name: str
    type: str
    required: bool
    description: str | None = None
    default: str | None = None

class TriggerInfo(BaseModel):
    name: str
    schedule: str | None = None

class MethodInfo(BaseModel):
    """Method metadata. Summary fields are always populated.
    Detail fields (doc, args, return_type, cache_config, trigger_configs)
    are only populated by describe_method."""

    nsref: str
    tags: list[str]
    has_cache: bool
    has_triggers: bool
    doc_teaser: str | None = None

    # Detail fields
    doc: str | None = None
    args: list[ArgInfo] | None = None
    return_type: str | None = None
    cache_config: dict[str, Any] | None = None
    trigger_configs: list[TriggerInfo] | None = None

class NamespaceInfo(BaseModel):
    prefix: str
    expose_api: bool
    run_engine: bool
    method_count: int
    has_cache: bool
```

### Mounting behavior

The system namespace is **always** mounted when the server starts, regardless
of UI or docs configuration. It receives `expose_api=True` so its methods
appear in docs and are callable via RPC.

Build order in `cli.py`:
1. Load user-defined namespaces from config.
2. Build MountContexts and engines as today.
3. Build `system` namespace via
   `build_system_namespace(namespaces, registry)` where `registry` is
   `EngineRegistry | None`. The function captures both by closure reference.
   All methods are always registered (so they appear in docs). Engine/trigger
   methods raise `ValueError` at call time if `registry` is None or has no
   engine for the requested namespace.
4. Insert `("system", (system_ns, system_entry))` into the namespaces dict.
5. Pass to `create_app()`.

Since the introspection closures capture the dict reference, the system
namespace will see itself in `list_namespaces` output.

### File changes

| File | Change |
|------|--------|
| `apps/engine_api.py` | **Rename** to `apps/system_api.py`. Add introspection methods, Pydantic models. Accept `namespaces` dict in addition to `EngineRegistry`. |
| `cli.py` | Always mount `system` namespace. Remove engine-only conditional. Pass `namespaces` to builder. |
| `ui/src/main.js` | Replace `engine.*` calls with `system.*`. Use `system.list_namespaces()` as primary data source. |
| `tests/test_engine_api.py` | **Rename** to `tests/test_system_api.py`. Add introspection method tests. |

**Unchanged:** `engine.py`, `server.py`, `rpc.py`, `mount.py`, `llm_docs.py`,
`config.py`.

### Engine method guards

Engine/trigger methods check that the target namespace has `run_engine=True`
via the `EngineRegistry`. If the registry has no engine for the requested
namespace, they raise `ValueError` with a descriptive message — same behavior
as today.

### UI changes

The UI's `loadAllData()` switches from parsing `llms.txt` + calling
`engine.list_namespaces` to a single `system.list_namespaces()` call.
Namespace detail loads methods via `system.list_methods(prefix)`.
DAG/trigger calls change prefix from `engine.*` to `system.*`.
