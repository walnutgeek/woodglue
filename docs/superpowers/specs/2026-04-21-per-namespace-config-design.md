# Per-Namespace Configuration

## Problem

All namespaces currently expose their `api`-tagged methods via RPC/docs/OpenAPI, and
the engine (DAGs/triggers) is a single global toggle. We need per-namespace control over:

1. Whether API-tagged methods are exposed externally (default: yes)
2. Whether the engine runs DAGs/triggers for that namespace (default: no)

## Config Model

### `NamespaceEntry`

New Pydantic model in `config.py`:

```python
class NamespaceEntry(BaseModel):
    source: str | list[Any]   # GlobalRef, YAML path, or inline NsNodeConfig list
    expose_api: bool = True   # Include api-tagged methods in RPC/docs/OpenAPI
    run_engine: bool = False  # Activate DAGs and triggers for this namespace
```

### YAML Format

Plain strings and lists remain valid as shorthand (all defaults apply):

```yaml
namespaces:
  # Shorthand forms (expose_api=true, run_engine=false)
  hello: "woodglue.hello:ns"
  pipeline: "pipeline_ns.yaml"

  # Full form with explicit flags
  internal:
    source: "internal_ns.yaml"
    expose_api: false
    run_engine: true

  # Full form with inline entries
  workers:
    source:
      - gref: "myapp.workers:process"
        tags: ["api"]
    run_engine: true
```

### `WoodglueConfig` Changes

- `namespaces` field type stays `dict[str, Any]` (normalization happens at load time)
- Remove `EngineConfig` class and the `engine` field entirely

## Loading

`load_namespaces` in `cli.py` changes:

- **Return type:** `dict[str, tuple[Namespace, NamespaceEntry]]`
- **Normalization:** If value is `str` or `list`, wrap as `NamespaceEntry(source=value)`.
  If `dict`, validate as `NamespaceEntry`.
- **Namespace loading:** Uses `entry.source` with the existing three-form logic
  (YAML file, GlobalRef, inline list)

## API Filtering

`build_method_index` in `llm_docs.py`:

- Receives `dict[str, tuple[Namespace, NamespaceEntry]]`
- Skips namespaces where `entry.expose_api is False`
- Remaining logic unchanged (still filters by `api` tag within exposed namespaces)

`create_app` in `server.py`:

- Receives the richer dict
- Passes full namespaces dict (all, including non-exposed) for internal use
- Passes filtered method_index for RPC dispatch and docs

Methods in non-exposed namespaces remain registered in the `Namespace` objects and
are callable internally (e.g., from DAGs or other namespaces), just not exposed via
the external RPC/docs surface.

## Engine Activation

- Engine is considered enabled if `any(entry.run_engine for _, entry in namespaces.values())`
- On startup, only namespaces with `run_engine=True` have their DAGs/triggers activated
- The global `EngineConfig` / `engine.enabled` toggle is removed

## Cleanup

Remove from `config.py`:
- `EngineConfig` class
- `engine` field from `WoodglueConfig`

Remove from `cli.py`:
- `if config.engine.enabled:` block in `start()`

## Test Plan

- Config loading: verify all three shorthand forms normalize to `NamespaceEntry` correctly
- Config loading: verify full `NamespaceEntry` with explicit flags
- API filtering: namespace with `expose_api=False` has methods registered but not in
  method_index
- API filtering: namespace with `expose_api=True` (default) works as before
- Engine flag: namespace with `run_engine=True` is included in engine startup
- Engine flag: namespace with `run_engine=False` (default) is excluded
- Backward compat: existing `woodglue.yaml` with plain strings still works
