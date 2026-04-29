# CLI and Config Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge woodglue's CLI and config with lythonic's lyth engine into a single config file and flat CLI that controls both the HTTP server and optional compose engine.

**Architecture:** `WoodglueConfig` replaces both the old woodglue config and the lyth engine config. It extends `StorageConfig`, supports `namespaces: dict[str, str | list[NsNodeConfig]]`, and adds an `engine.enabled` toggle. The CLI becomes flat (`wgl start/stop/run/fire/status`) with `WoodglueMain(Main)` as the root model.

**Tech Stack:** Python 3.11+, Pydantic, pydantic-yaml, Tornado, lythonic

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/woodglue/config.py` | Rewrite: `WoodglueConfig`, `WoodglueStorageConfig`, `EngineConfig`, `DocsConfig`, `UiConfig`, `load_config` |
| `src/woodglue/cli.py` | Rewrite: `WoodglueMain(Main)`, flat commands, `load_namespaces` handles 3 value types |
| `src/woodglue/apps/server.py` | Update `create_app` for new config shape |
| `tests/test_config.py` | Rewrite for new config models |
| `tests/test_rpc.py` | Update `WoodglueConfig` instantiation |
| `tests/test_client.py` | Update `WoodglueConfig` instantiation |
| `tests/test_llm_docs_handlers.py` | Update `WoodglueConfig` instantiation |
| `tests/test_integration.py` | Update `WoodglueConfig` instantiation |

---

### Task 1: Rewrite Config Models

**Files:**
- Modify: `src/woodglue/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Rewrite `src/woodglue/config.py`**

Replace the entire file:

```python
"""
YAML-backed server configuration.

The config file lives at `{data_dir}/woodglue.yaml` and is required to run
the server. It declares storage, namespaces, documentation, UI, and engine
settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lythonic.compose.engine import NsNodeConfig, StorageConfig
from pydantic import BaseModel
from pydantic_yaml import parse_yaml_file_as

CONFIG_FILENAME = "woodglue.yaml"


class WoodglueStorageConfig(StorageConfig):
    """Extends lythonic StorageConfig with woodglue-specific storage."""

    auth_db: Path | None = None


class DocsConfig(BaseModel):
    """Documentation generation settings."""

    enabled: bool = True
    openapi: bool = True


class UiConfig(BaseModel):
    """JavaScript documentation UI settings."""

    enabled: bool = True


class EngineConfig(BaseModel):
    """Lyth compose engine settings."""

    enabled: bool = False


class WoodglueConfig(BaseModel):
    """
    Root configuration loaded from `woodglue.yaml`.

    `namespaces` maps a prefix string to one of:
    - A GlobalRef string (e.g., `"woodglue.hello:ns"`)
    - A YAML file path ending in `.yaml`/`.yml`
    - An inline list of `NsNodeConfig` entries
    """

    storage: WoodglueStorageConfig = WoodglueStorageConfig()
    namespaces: dict[str, Any]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()
    engine: EngineConfig = EngineConfig()


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

Note: `namespaces` uses `dict[str, Any]` because Pydantic union discrimination of `str | list[NsNodeConfig]` from YAML is fragile. The `load_namespaces` function in `cli.py` handles the type dispatch at runtime.

- [ ] **Step 2: Rewrite `tests/test_config.py`**

Replace the entire file:

```python
"""Tests for woodglue.config YAML loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

from woodglue.config import DocsConfig, EngineConfig, UiConfig, WoodglueConfig, load_config


def test_load_minimal_config():
    """Load a YAML with only namespaces (required field)."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text("namespaces:\n  hello: 'woodglue.hello:ns'\n")
        cfg = load_config(Path(tmp))
        assert cfg.namespaces == {"hello": "woodglue.hello:ns"}
        assert cfg.docs == DocsConfig()
        assert cfg.ui == UiConfig()
        assert cfg.engine == EngineConfig()


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
            "engine:\n"
            "  enabled: true\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.namespaces == {
            "hello": "woodglue.hello:ns",
            "other": "some.module:ns",
        }
        assert cfg.docs.enabled is False
        assert cfg.docs.openapi is False
        assert cfg.ui.enabled is False
        assert cfg.engine.enabled is True


def test_load_config_missing_file():
    """Raise FileNotFoundError when woodglue.yaml is missing."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            load_config(Path(tmp))
            raise AssertionError("Expected FileNotFoundError")
        except FileNotFoundError:
            pass


def test_load_config_with_inline_namespace():
    """Load a config with inline NsNodeConfig list."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "namespaces:\n"
            "  api:\n"
            "    - nsref: hello\n"
            '      gref: "woodglue.hello:hello"\n'
            "      tags: ['api']\n"
        )
        cfg = load_config(Path(tmp))
        assert isinstance(cfg.namespaces["api"], list)
        assert len(cfg.namespaces["api"]) == 1


def test_load_config_with_storage():
    """Load a config with storage settings."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "storage:\n"
            "  cache_db: cache.db\n"
            "  auth_db: auth.db\n"
            "namespaces:\n"
            "  hello: 'woodglue.hello:ns'\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.storage.cache_db == Path("cache.db")
        assert cfg.storage.auth_db == Path("auth.db")
```

- [ ] **Step 3: Run tests**

Run: `make lint && make test`

Note: Other test files that import `WoodglueConfig` will fail because the constructor changed (now requires `namespaces: dict[str, Any]` but `storage` is new, `engine` is new). Those will be fixed in Task 3.

Expected: `test_config.py` tests pass. Other tests may fail — that's OK for now.

- [ ] **Step 4: Commit**

```bash
git add src/woodglue/config.py tests/test_config.py
git commit -m "feat: rewrite config models with storage, engine, inline namespaces"
```

---

### Task 2: Rewrite CLI with Flat Commands

**Files:**
- Modify: `src/woodglue/cli.py`

- [ ] **Step 1: Rewrite `src/woodglue/cli.py`**

Replace the entire file:

```python
"""
wgl — woodglue server CLI.

Commands:
    wgl start          Start the server (and optionally the engine)
    wgl stop           Stop a running instance
    wgl run <nsref>    Run a callable or DAG once
    wgl status         Show server status
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from lythonic import GlobalRef
from lythonic.compose.cli import ActionTree, Main, RunContext
from lythonic.compose.engine import NsNodeConfig
from lythonic.compose.namespace import Namespace
from pydantic import Field

from woodglue.config import WoodglueConfig, load_config


class WoodglueMain(Main):
    """wgl -- woodglue server CLI"""

    data: Path = Field(default=Path("./data"), description="data directory")
    port: int = Field(default=5321, description="port to listen on")
    host: str = Field(default="127.0.0.1", description="host to bind to")


main_at = ActionTree(WoodglueMain)


def _pid_file(data_dir: Path) -> Path:
    return data_dir / "wgl.pid"


def _resolve_storage(config: WoodglueConfig, data_dir: Path) -> None:
    """Resolve storage paths relative to data_dir, in place."""
    storage = config.storage
    if storage.cache_db is not None and not storage.cache_db.is_absolute():
        storage.cache_db = data_dir / storage.cache_db
    if storage.dags_db is not None and not storage.dags_db.is_absolute():
        storage.dags_db = data_dir / storage.dags_db
    if storage.triggers_db is not None and not storage.triggers_db.is_absolute():
        storage.triggers_db = data_dir / storage.triggers_db
    if storage.auth_db is not None and not storage.auth_db.is_absolute():
        storage.auth_db = data_dir / storage.auth_db
    if storage.log_file is not None and not storage.log_file.is_absolute():
        storage.log_file = data_dir / storage.log_file


def load_namespaces(
    ns_map: dict[str, Any], data_dir: Path
) -> dict[str, Namespace]:
    """
    Load all namespaces from config, keyed by prefix.

    Handles three value types:
    - String ending in `.yaml`/`.yml`: load as lythonic EngineConfig
    - Other string: GlobalRef to a Namespace instance
    - List of dicts/NsNodeConfig: inline namespace entries
    """
    from lythonic.compose.engine import EngineConfig as LythEngineConfig
    from pydantic_yaml import parse_yaml_file_as

    result: dict[str, Namespace] = {}
    for prefix, value in ns_map.items():
        if isinstance(value, str):
            if value.endswith(".yaml") or value.endswith(".yml"):
                config_path = data_dir / value
                engine_config = parse_yaml_file_as(LythEngineConfig, config_path)
                ns = Namespace()
                for entry in engine_config.namespace:
                    if entry.gref is not None:
                        ns.register(
                            str(entry.gref),
                            nsref=entry.nsref,
                            tags=entry.tags,
                            config=entry,
                        )
                result[prefix] = ns
            else:
                gref = GlobalRef(value)
                ns = gref.get_instance()
                assert isinstance(ns, Namespace), f"{value} is not a Namespace"
                result[prefix] = ns
        elif isinstance(value, list):
            ns = Namespace()
            for item in value:
                entry = NsNodeConfig.model_validate(item) if isinstance(item, dict) else item
                if entry.gref is not None:
                    ns.register(
                        str(entry.gref),
                        nsref=entry.nsref,
                        tags=entry.tags,
                        config=entry,
                    )
            result[prefix] = ns
    return result


def _get_config_and_dir(ctx: RunContext) -> tuple[WoodglueConfig, Path]:
    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    data_dir = root.data
    config = load_config(data_dir)
    _resolve_storage(config, data_dir)
    return config, data_dir


@main_at.actions.wrap
def start(ctx: RunContext) -> None:  # pyright: ignore[reportUnusedParameter]
    """Start the server (and optionally the engine)"""
    import tornado.ioloop

    from woodglue.apps.server import create_app

    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    data_dir = root.data
    data_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(data_dir)
    _resolve_storage(config, data_dir)
    namespaces = load_namespaces(config.namespaces, data_dir)

    app = create_app(namespaces=namespaces, config=config)
    app.listen(root.port, root.host)
    print(f"Woodglue listening on http://{root.host}:{root.port}")
    print(f"  RPC endpoint: http://{root.host}:{root.port}/rpc")
    if config.docs.enabled:
        print(f"  LLM docs:     http://{root.host}:{root.port}/docs/llms.txt")
    if config.ui.enabled:
        print(f"  UI:           http://{root.host}:{root.port}/ui/")

    # Write PID file
    pid_path = _pid_file(data_dir)
    pid_path.write_text(str(os.getpid()))

    try:
        if config.engine.enabled:
            import asyncio
            import signal

            from lythonic.compose.lyth import _setup_file_logging

            if config.storage.log_file:
                _setup_file_logging(config.storage.log_file)

            print("  Engine: enabled")

            async def _run_engine() -> None:
                from lythonic.compose.dag_provenance import DagProvenance
                from lythonic.compose.trigger import TriggerManager, TriggerStore

                triggers_db = config.storage.triggers_db or (data_dir / "triggers.db")
                dags_db = config.storage.dags_db or (data_dir / "dags.db")

                trigger_store = TriggerStore(triggers_db)
                provenance = DagProvenance(dags_db)
                # Merge all namespaces into one for the engine
                merged = Namespace()
                for ns in namespaces.values():
                    for _nsref, node in ns._nodes.items():  # pyright: ignore[reportPrivateUsage]
                        merged._nodes[node.nsref] = node  # pyright: ignore[reportPrivateUsage]

                manager = TriggerManager(
                    namespace=merged, store=trigger_store, provenance=provenance
                )

                for node in merged._all_leaves():  # pyright: ignore[reportPrivateUsage]
                    if hasattr(node, 'config') and node.config and node.config.triggers:
                        for tc in node.config.triggers:
                            manager.activate(tc.name)
                            print(f"  Activated trigger: {tc.name} ({tc.type})")

                manager.start()
                print("  Engine poll loop started.")

                shutdown = asyncio.Event()

                def _handle_signal() -> None:
                    print("\nShutting down...")
                    manager.stop()
                    tornado.ioloop.IOLoop.current().stop()
                    shutdown.set()

                loop = asyncio.get_running_loop()
                loop.add_signal_handler(signal.SIGTERM, _handle_signal)
                loop.add_signal_handler(signal.SIGINT, _handle_signal)

                await shutdown.wait()

            tornado.ioloop.IOLoop.current().run_sync(lambda: _run_engine())
        else:
            tornado.ioloop.IOLoop.current().start()
    finally:
        pid_path = _pid_file(data_dir)
        if pid_path.exists():
            pid_path.unlink()


@main_at.actions.wrap
def stop(ctx: RunContext) -> None:  # pyright: ignore[reportUnusedParameter]
    """Stop a running instance"""
    import signal as signal_mod

    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    pid_path = _pid_file(root.data)

    if not pid_path.exists():
        print("No running instance found (no PID file)")
        return

    pid = int(pid_path.read_text().strip())
    print(f"Sending SIGTERM to process {pid}")

    try:
        os.kill(pid, signal_mod.SIGTERM)
    except ProcessLookupError:
        print(f"Process {pid} not found, removing stale PID file")
        pid_path.unlink()


@main_at.actions.wrap
def run(ctx: RunContext, nsref: str) -> None:  # pyright: ignore[reportUnusedParameter]
    """Run a callable or DAG once"""
    import asyncio
    import json

    config, data_dir = _get_config_and_dir(ctx)
    namespaces = load_namespaces(config.namespaces, data_dir)

    # Find the node across all namespaces
    node = None
    for ns in namespaces.values():
        try:
            node = ns.get(nsref)
            break
        except KeyError:
            continue

    if node is None:
        print(f"'{nsref}' not found in any namespace")
        return

    async def _run() -> None:
        result = node()
        import inspect
        if inspect.isawaitable(result):
            result = await result
        print(json.dumps(result, indent=2, default=str))

    asyncio.run(_run())


@main_at.actions.wrap
def status(ctx: RunContext) -> None:  # pyright: ignore[reportUnusedParameter]
    """Show server status"""
    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    pid_path = _pid_file(root.data)

    if pid_path.exists():
        pid = pid_path.read_text().strip()
        print(f"Server running (pid={pid})")
    else:
        print("Server not running")


def main() -> None:
    if not main_at.run_args(sys.argv).success:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run lint**

Run: `make lint`
Expected: Clean (other tests may still fail)

- [ ] **Step 3: Commit**

```bash
git add src/woodglue/cli.py
git commit -m "feat: flat CLI with WoodglueMain, engine integration"
```

---

### Task 3: Update All Tests for New Config Shape

**Files:**
- Modify: `tests/test_rpc.py`
- Modify: `tests/test_client.py`
- Modify: `tests/test_llm_docs_handlers.py`
- Modify: `tests/test_integration.py`
- Modify: `src/woodglue/apps/server.py`

The `WoodglueConfig` constructor changed — `namespaces` is now required and `storage`/`engine` are new. Tests that pass `WoodglueConfig(namespaces={"test": "unused"})` still work since the shape is the same. But `create_app` calls that don't pass a config need a default that works.

- [ ] **Step 1: Update `create_app` default config**

In `src/woodglue/apps/server.py`, change the default:

```python
    if config is None:
        config = WoodglueConfig(namespaces={})
```

This still works — `namespaces: dict[str, Any]` accepts empty dict.

- [ ] **Step 2: Update `tests/test_config.py` — add inline namespace and load_namespaces tests**

Add to the end of `tests/test_config.py`:

```python
from woodglue.cli import load_namespaces


def test_load_namespaces_from_yaml():
    """Load a namespace from a YAML config file."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        ns_config_path = data_dir / "test_ns.yaml"
        ns_config_path.write_text(
            "namespace:\n"
            "  - nsref: hello\n"
            '    gref: "woodglue.hello:hello"\n'
            "    tags: ['api']\n"
            "  - nsref: pydantic_hello\n"
            '    gref: "woodglue.hello:pydantic_hello"\n'
            "    tags: ['api']\n"
        )
        namespaces = load_namespaces({"greet": "test_ns.yaml"}, data_dir)
        assert "greet" in namespaces
        ns = namespaces["greet"]
        node = ns.get("hello")
        assert node is not None
        assert "api" in node.tags


def test_load_namespaces_from_globalref():
    """Load a namespace from a GlobalRef string (existing behavior)."""
    namespaces = load_namespaces({"hello": "woodglue.hello:ns"}, Path("."))
    assert "hello" in namespaces


def test_load_namespaces_inline():
    """Load a namespace from inline NsNodeConfig list."""
    entries = [
        {"nsref": "hello", "gref": "woodglue.hello:hello", "tags": ["api"]},
        {"nsref": "pydantic_hello", "gref": "woodglue.hello:pydantic_hello", "tags": ["api"]},
    ]
    namespaces = load_namespaces({"inline": entries}, Path("."))
    assert "inline" in namespaces
    node = namespaces["inline"].get("hello")
    assert node is not None
    assert "api" in node.tags
```

- [ ] **Step 3: Run full test suite**

Run: `make lint && make test`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/woodglue/apps/server.py tests/test_config.py
git commit -m "feat: update tests and server for new config shape"
```

---

### Task 4: Final Verification

- [ ] **Step 1: Run full suite**

Run: `make lint && make test`
Expected: All tests pass, zero lint errors

- [ ] **Step 2: Verify CLI help works**

Run: `uv run wgl --help`
Expected: Shows WoodglueMain options and commands (start, stop, run, status)

- [ ] **Step 3: Commit any remaining fixes**

Only if needed.
