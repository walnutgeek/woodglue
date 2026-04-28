# Implementation Plan: Lythonic Mount + LogConfig Integration

Based on: `docs/superpowers/specs/2026-04-28-mount-integration-design.md`

## Step 1: Update engine.py

**Remove** `DagProvenance` import and `provenance` field from `NamespaceEngine`.

**Change** `create_engine()`:
- Remove `DagProvenance(mount.state_path("dag.db"))` — mount() handles this
- Read `namespace._provenance` for TriggerManager's provenance arg
- Signature stays the same: `create_engine(prefix, namespace, mount)`

Before:
```python
from lythonic.compose.dag_provenance import DagProvenance

@dataclass
class NamespaceEngine:
    prefix: str
    namespace: Namespace
    provenance: DagProvenance
    trigger_store: TriggerStore
    trigger_manager: TriggerManager

def create_engine(prefix, namespace, mount):
    provenance = DagProvenance(mount.state_path("dag.db"))
    trigger_store = TriggerStore(mount.state_path("triggers.db"))
    trigger_manager = TriggerManager(namespace=namespace, store=trigger_store, provenance=provenance)
    return NamespaceEngine(prefix, namespace, provenance, trigger_store, trigger_manager)
```

After:
```python
@dataclass
class NamespaceEngine:
    prefix: str
    namespace: Namespace
    trigger_store: TriggerStore
    trigger_manager: TriggerManager

def create_engine(prefix, namespace, mount):
    trigger_store = TriggerStore(mount.state_path("triggers.db"))
    provenance = namespace._provenance  # set by mount()
    trigger_manager = TriggerManager(namespace=namespace, store=trigger_store, provenance=provenance)
    return NamespaceEngine(prefix, namespace, trigger_store, trigger_manager)
```

## Step 2: Update cli.py

### 2a: Replace `_setup_file_logging` with `LogConfig`

Remove the `_setup_file_logging()` function and `import logging`.

In `start()`, replace:
```python
log_file = config.storage.log_file or data_dir / "wgl.log"
_setup_file_logging(log_file)
```

With:
```python
from lythonic.compose.engine import LogConfig

log_config = LogConfig(
    log_file=config.storage.log_file or data_dir / "wgl.log",
    log_level=config.storage.log_level,
    loggers=config.storage.loggers,
)
log_config.setup_logging()
```

### 2b: Add mount calls before engine creation

In the engine loop in `start()`, mount each namespace before creating its
engine:

```python
from lythonic.compose.engine import StorageConfig as LythStorageConfig

for prefix, (ns, entry) in namespaces.items():
    if entry.run_engine:
        mount = mounts[prefix]
        storage = LythStorageConfig(
            cache_db=mount.state_path("cache.db"),
            dag_db=mount.state_path("dag.db"),
            trigger_db=mount.state_path("triggers.db"),
        )
        ns.mount(storage)
        engine = create_engine(prefix, ns, mount)
        activated = activate_triggers(engine)
        registry.register(engine)
```

### 2c: Simplify `load_namespaces` with `from_dict`

For `file`-based namespaces:
```python
import yaml

elif ns_entry.file is not None:
    config_path = data_dir / ns_entry.file
    raw = yaml.safe_load(config_path.read_text())
    ns = Namespace.from_dict(raw.get("namespace", []))
    result[prefix] = (ns, ns_entry)
```

For `entries`-based namespaces:
```python
elif ns_entry.entries is not None:
    entries = [e.model_dump(exclude_none=True) for e in ns_entry.entries]
    ns = Namespace.from_dict(entries)
    result[prefix] = (ns, ns_entry)
```

Remove the `LythEngineConfig` import and `parse_yaml_file_as` import (no
longer needed for namespace loading).

## Step 3: Update system_api.py

Replace all `engine.provenance.xxx()` calls with
`engine.namespace._provenance.xxx()`:

- `recent_runs`: `engine.provenance.get_recent_runs(...)` ->
  `engine.namespace._provenance.get_recent_runs(...)`
- `active_runs`: `engine.provenance.get_active_runs()`
- `inspect_run`: `engine.provenance.inspect_run(...)`
- `load_io`: `engine.provenance.load_io(...)`
- `child_runs`: `engine.provenance.get_child_runs(...)`

Add `# pyright: ignore[reportPrivateUsage]` where needed.

## Step 4: Run lint and tests

```bash
make lint
make test
```

Fix any issues.

## Step 5: Update test files if needed

Tests that create `NamespaceEngine` directly (in test_system_api.py or
similar) need to drop the `provenance` field. Tests that import
`DagProvenance` from engine.py need updating.
