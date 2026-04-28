# Integrate Lythonic Mount and LogConfig into Woodglue

## Problem

Woodglue duplicates persistence setup that lythonic's `Namespace.mount()` now
handles declaratively:

- `engine.py` manually creates `DagProvenance`, `TriggerStore`, `TriggerManager`
- `cli.py` has a hand-rolled `_setup_file_logging()` that copies lyth's format
- `load_namespaces()` manually loops over YAML entries instead of using
  `Namespace.from_dict()`

Lythonic v0.0.15+ introduced `Namespace.mount(storage)` for declarative
persistence activation (cache wrapping, DAG provenance) and extracted
`LogConfig` from `StorageConfig` with a `setup_logging()` method.

## Solution

Adopt lythonic's mount flow. Call `LogConfig.setup_logging()` once globally,
then `ns.mount(storage)` per namespace. Slim down `NamespaceEngine` and remove
duplicated code.

## Design

### 1. Global Logging via LogConfig

Replace woodglue's `_setup_file_logging()` with lythonic's `LogConfig`:

```python
from lythonic.compose.engine import LogConfig

log_config = LogConfig(
    log_file=data_dir / "wgl.log",
    log_level=config.storage.log_level,
    loggers=config.storage.loggers,
)
log_config.setup_logging()
```

Called once in `start()`, before any namespace loading or mounting. Single
global log file at `data/wgl.log` (or overridden via `storage.log_file`).

Remove `_setup_file_logging()` and `import logging` from `cli.py`.

### 2. Per-Namespace Mount

For each namespace with `run_engine=True`, build a `StorageConfig` from
`MountContext` paths (no `log_file`) and call `ns.mount()`:

```python
from lythonic.compose.engine import StorageConfig

storage = StorageConfig(
    cache_db=mount.state_path("cache.db"),
    dag_db=mount.state_path("dag.db"),
    trigger_db=mount.state_path("triggers.db"),
)
ns.mount(storage)
```

This replaces manual `DagProvenance` creation. Cache nodes get wrapped
automatically. DAG provenance is created and stored on the namespace as
`ns._provenance`.

### 3. Thinner NamespaceEngine

Remove `provenance` field from `NamespaceEngine`:

```python
@dataclass
class NamespaceEngine:
    prefix: str
    namespace: Namespace
    trigger_store: TriggerStore
    trigger_manager: TriggerManager
```

`create_engine()` changes to read provenance from the (already-mounted)
namespace:

```python
def create_engine(prefix: str, namespace: Namespace, mount: MountContext) -> NamespaceEngine:
    trigger_store = TriggerStore(mount.state_path("triggers.db"))
    trigger_manager = TriggerManager(
        namespace=namespace,
        store=trigger_store,
        provenance=namespace._provenance,
    )
    return NamespaceEngine(
        prefix=prefix,
        namespace=namespace,
        trigger_store=trigger_store,
        trigger_manager=trigger_manager,
    )
```

Namespace must be mounted before `create_engine()` is called.

### 4. system_api.py Updates

Methods that used `engine.provenance` switch to `engine.namespace._provenance`:

- `recent_runs` -> `engine.namespace._provenance.get_recent_runs(...)`
- `active_runs` -> `engine.namespace._provenance.get_active_runs()`
- `inspect_run` -> `engine.namespace._provenance.inspect_run(...)`
- `load_io` -> `engine.namespace._provenance.load_io(...)`
- `child_runs` -> `engine.namespace._provenance.get_child_runs(...)`

### 5. load_namespaces Uses from_dict

For `file`- and `entries`-based namespaces, replace the manual register loop
with `Namespace.from_dict()`:

```python
elif ns_entry.file is not None:
    config_path = data_dir / ns_entry.file
    raw = yaml.safe_load(config_path.read_text())
    ns = Namespace.from_dict(raw.get("namespace", []))
    result[prefix] = (ns, ns_entry)
elif ns_entry.entries is not None:
    entries = [e.model_dump(exclude_none=True) for e in ns_entry.entries]
    ns = Namespace.from_dict(entries)
    result[prefix] = (ns, ns_entry)
```

This uses lythonic's discriminated config deserialization (dispatches on
`type` field to create `NsCacheConfig` vs `NsNodeConfig`).

## Files to Modify

1. `src/woodglue/cli.py` — replace `_setup_file_logging` with `LogConfig`,
   add mount calls, simplify `load_namespaces` with `from_dict`
2. `src/woodglue/engine.py` — remove `provenance` from `NamespaceEngine`,
   remove `DagProvenance` import, read from namespace after mount
3. `src/woodglue/apps/system_api.py` — `engine.provenance` ->
   `engine.namespace._provenance`
4. `src/woodglue/config.py` — `WoodglueStorageConfig` stays as-is (extends
   `StorageConfig`, adds `auth_db`)

## Verification

```bash
make lint
make test
```
