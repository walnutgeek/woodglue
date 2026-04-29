# Mount State Directories and Engine API Facade

## Problem

With multiple namespaces potentially running engines (DAGs + triggers), each
namespace needs isolated state (databases, logs, cache). Currently, storage is
global. We also need an RPC API to inspect and manage per-namespace engine state.

## Part 1: Mount State Directories

### Layout

Every mounted namespace gets a `MountContext`. When namespace logic needs
persistent state, it uses a lazily-created directory under `data/mounts/`:

```
data/
  mounts/
    pipeline/         # state dir for prefix "pipeline"
      dags.db          # DagProvenance SQLite
      triggers.db     # TriggerStore SQLite
      cache.db        # optional: caching logic
      ...             # arbitrary files from namespace logic
    etl/
      dags.db
      triggers.db
```

Directories are created on first write via `state_path()`, not eagerly at startup.

### MountContext

A context variable tracks the current mount so that any code running within a
namespace can discover its state directory without explicit parameter passing.

```python
from contextvars import ContextVar

current_mount: ContextVar[MountContext]

class MountContext:
    """Per-namespace mount state, accessible via `current_mount` context var."""

    prefix: str
    state_dir: Path   # data/mounts/{prefix}

    def state_path(self, filename: str) -> Path:
        """
        Resolve a file path within this mount's state dir.
        Creates the state dir lazily on first call.
        """
```

New module: `src/woodglue/mount.py`.

### Who sets the context var

- **RPC dispatcher:** sets `current_mount` before invoking a namespace method
- **DAG runner:** sets `current_mount` before executing a DAG node
- **Trigger manager:** sets `current_mount` before firing a trigger
- **`wgl run`:** sets `current_mount` before running a callable

### Scope

All namespaces get a `MountContext` (not just `run_engine=True` ones). A
namespace that only exposes APIs but occasionally caches data can still use
`current_mount.get().state_path("cache.db")`. The state dir is only created
if `state_path()` is actually called.

### WoodglueStorageConfig changes

Remove `dags_db` and `triggers_db` from `WoodglueStorageConfig`. These are now
per-namespace, resolved via `mount.state_path("dags.db")` and
`mount.state_path("triggers.db")`. Keep `cache_db` (global app-level cache),
`auth_db`, and `log_file` as global.

## Part 2: EngineRegistry

For namespaces with `run_engine: true`, an `EngineRegistry` manages
per-namespace engine instances.

### Data model

```python
class NamespaceEngine:
    """Engine instances for a single namespace."""
    prefix: str
    namespace: Namespace
    provenance: DagProvenance
    trigger_store: TriggerStore
    trigger_manager: TriggerManager

class EngineRegistry:
    """Manages per-namespace engine instances."""

    _engines: dict[str, NamespaceEngine]

    def get(self, prefix: str) -> NamespaceEngine
    def list_prefixes(self) -> list[str]
    async def start_all(self) -> None   # start all TriggerManager poll loops
    async def stop_all(self) -> None    # stop all TriggerManager poll loops
```

New module: `src/woodglue/engine.py`.

### Startup flow

In `cli.py start()`:

1. Load config and namespaces (existing)
2. Build `MountContext` for each prefix
3. For each namespace with `run_engine=True`:
   a. Create `DagProvenance(mount.state_path("dags.db"))`
   b. Create `TriggerStore(mount.state_path("triggers.db"))`
   c. Create `TriggerManager(namespace, store, provenance)`
   d. Activate triggers from namespace node configs
   e. Register in `EngineRegistry`
4. Build engine facade namespace, add to app namespaces
5. `await registry.start_all()` to begin trigger poll loops
6. On shutdown: `await registry.stop_all()`

### EngineRegistry stored in app settings

The registry is stored in Tornado app settings so the facade handlers (and
potentially the RPC handler) can access it.

## Part 3: Engine API Facade Namespace

A built-in `engine` namespace is auto-mounted when any namespace has
`run_engine=True`. It wraps lythonic's existing Pydantic models and query
methods.

### Methods

All methods are tagged `["api"]` and take `namespace: str` as the first
parameter to identify which engine to query.

**Engine discovery:**

- `engine.list_namespaces() -> list[str]`
  Returns prefixes of all engine-enabled namespaces.

**Provenance queries (read-only):**

- `engine.recent_runs(namespace: str, limit: int = 20, status: str | None = None) -> list[DagRun]`
  Wraps `DagProvenance.get_recent_runs()`.

- `engine.active_runs(namespace: str) -> list[DagRun]`
  Wraps `DagProvenance.get_active_runs()`.

- `engine.inspect_run(namespace: str, run_id: str) -> DagRun | None`
  Wraps `DagProvenance.inspect_run()`.

- `engine.load_io(namespace: str, run_id: str, node_labels: list[str] | None = None) -> DagRun | None`
  Calls `inspect_run()` then `load_io()` to populate I/O payloads.

- `engine.child_runs(namespace: str, parent_run_id: str) -> list[DagRun]`
  Wraps `DagProvenance.get_child_runs()`.

**Trigger management:**

- `engine.list_triggers(namespace: str) -> list[dict]`
  Returns trigger activations from `TriggerStore`.

- `engine.fire_trigger(namespace: str, name: str, payload: dict | None = None) -> DagRunResult`
  Wraps `TriggerManager.fire()`.

- `engine.activate_trigger(namespace: str, name: str) -> dict`
  Wraps `TriggerManager.activate()`.

- `engine.deactivate_trigger(namespace: str, name: str) -> dict`
  Wraps `TriggerManager.deactivate()`.

### Implementation

New module: `src/woodglue/apps/engine_api.py`.

Each method is a thin wrapper:
1. Look up `NamespaceEngine` from registry by `namespace` parameter
2. Call the corresponding lythonic method
3. Return the Pydantic model directly (serialized by the RPC layer)

If `namespace` is not found in registry, raise a clear error.

### Auto-mounting

The `engine` namespace is only added to the app's namespace dict if at least
one namespace has `run_engine=True`. It is not configurable in `woodglue.yaml`
-- it is a built-in system namespace.

## File Summary

| File | Change |
|------|--------|
| `src/woodglue/mount.py` | New: `MountContext`, `current_mount` context var |
| `src/woodglue/engine.py` | New: `NamespaceEngine`, `EngineRegistry` |
| `src/woodglue/apps/engine_api.py` | New: facade namespace functions |
| `src/woodglue/config.py` | Remove `dags_db`, `triggers_db` from `WoodglueStorageConfig` |
| `src/woodglue/cli.py` | Build mounts, registry, facade at startup |
| `src/woodglue/apps/server.py` | Store registry in app settings |
| `src/woodglue/apps/rpc.py` | Set `current_mount` before dispatching |

## Test Plan

- `MountContext.state_path()` creates dir lazily, returns correct path
- `current_mount` context var is accessible within dispatched calls
- `EngineRegistry` creates/retrieves engines by prefix
- `EngineRegistry.start_all()` / `stop_all()` manages trigger managers
- Engine facade methods return correct Pydantic models
- `engine.list_namespaces()` reflects only `run_engine=True` prefixes
- Facade returns error for unknown namespace prefix
- Two namespaces with same structure but different prefixes have isolated state
- `WoodglueStorageConfig` no longer has `dags_db` / `triggers_db`
