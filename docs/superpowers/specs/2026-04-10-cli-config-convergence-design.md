# CLI and Config Convergence with Lyth Engine

## Overview

Merge woodglue's CLI and config with lythonic's lyth engine so that a
single config file and flat CLI controls both the HTTP server and the
optional compose engine (triggers, DAG scheduling).

---

## Unified Config

Single YAML file at `{data}/woodglue.yaml`.

### Config Models

```python
from lythonic.compose.engine import NsNodeConfig, StorageConfig


class WoodglueStorageConfig(StorageConfig):
    """Extends lythonic StorageConfig with woodglue-specific storage."""
    auth_db: Path | None = None


class DocsConfig(BaseModel):
    enabled: bool = True
    openapi: bool = True


class UiConfig(BaseModel):
    enabled: bool = True


class EngineConfig(BaseModel):
    enabled: bool = False


class WoodglueConfig(BaseModel):
    storage: WoodglueStorageConfig = WoodglueStorageConfig()
    namespaces: dict[str, str | list[NsNodeConfig]]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()
    engine: EngineConfig = EngineConfig()
```

### Example YAML

```yaml
storage:
  cache_db: cache.db
  dags_db: dags.db
  triggers_db: triggers.db
  auth_db: auth.db
  log_file: woodglue.log

namespaces:
  hello: "woodglue.hello:ns"
  api:
    - nsref: greet
      gref: "myapp:greet"
      tags: ["api"]
    - nsref: fetch
      gref: "myapp:fetch"
      tags: ["api"]
  pipeline: "pipeline_ns.yaml"

docs:
  enabled: true
  openapi: true

ui:
  enabled: true

engine:
  enabled: false
```

### Storage Path Resolution

Storage paths are resolved relative to `data_dir` at startup, following
the same pattern as lyth's `_resolve_config`:

- `None` → `{data_dir}/{default_name}.db`
- Relative path → `{data_dir}/{path}`
- Absolute path → used as-is

---

## Unified CLI

### Root Model

```python
from lythonic.compose.cli import Main

class WoodglueMain(Main):
    """wgl -- woodglue server CLI"""
    data: Path = Field(default=Path("./data"), description="data directory")
    port: int = Field(default=5321, description="port to listen on")
    host: str = Field(default="127.0.0.1", description="host to bind to")
```

### Commands

| Command | Description |
|---------|-------------|
| `wgl start` | Load config, boot Tornado (RPC + docs + UI), optionally start engine |
| `wgl stop` | Stop running instance via PID file |
| `wgl run <nsref>` | Run a single callable or DAG once |
| `wgl fire <trigger>` | Fire a trigger manually |
| `wgl status` | Show server and engine status |

The old `Server` subcommand model and `server_at` action tree are
removed. `WoodglueMain` is the root — commands are direct children.

---

## Namespace Loading

`load_namespaces` handles three value types from
`namespaces: dict[str, str | list[NsNodeConfig]]`:

| Value Type | Detection | Behavior |
|-----------|-----------|----------|
| String ending in `.yaml`/`.yml` | `value.endswith(...)` | Load file as lythonic `EngineConfig`, build namespace from `namespace` entries |
| Other string | Default | `GlobalRef(value).get_instance()` — must return a `Namespace` |
| `list[NsNodeConfig]` | `isinstance(value, list)` | Build namespace inline, registering each entry |

All three produce a `dict[str, Namespace]` keyed by prefix, passed to
`create_app` as before.

---

## Start Command Behavior

`wgl start`:

1. Load `WoodglueConfig` from `{data}/woodglue.yaml`
2. Resolve storage paths relative to `data_dir`
3. Load all namespaces
4. Build Tornado app (`create_app`)
5. Start Tornado listening on `host:port`
6. Write PID file
7. If `engine.enabled`:
   - Set up file logging to `storage.log_file`
   - Create `TriggerManager` with `TriggerStore` + `DagProvenance`
   - Activate triggers from node configs
   - Start poll loop
   - Handle SIGTERM/SIGINT for graceful shutdown of both engine and Tornado
8. If engine disabled: just `IOLoop.current().start()`
9. Clean up PID file on exit

---

## Files Affected

| File | Changes |
|------|---------|
| `src/woodglue/config.py` | Rewrite: `WoodglueConfig`, `WoodglueStorageConfig`, `EngineConfig`, `DocsConfig`, `UiConfig`. Remove `CONFIG_FILENAME` constant, `load_config` takes `data_dir` |
| `src/woodglue/cli.py` | Rewrite: `WoodglueMain(Main)`, flat commands (`start`, `stop`, `run`, `fire`, `status`), engine integration. Remove `Server` model |
| `src/woodglue/apps/server.py` | Minor: `create_app` accepts new `WoodglueConfig` shape |
| `tests/test_config.py` | Update for new config models and namespace loading |
| `tests/test_rpc.py` | Update `WoodglueConfig` instantiation |
| `tests/test_client.py` | Update `WoodglueConfig` instantiation |
| `tests/test_llm_docs_handlers.py` | Update `WoodglueConfig` instantiation |
| `tests/test_integration.py` | Update `WoodglueConfig` instantiation |
