"""
wgl -- woodglue server CLI.

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
from lythonic.compose.namespace import Namespace, NsNodeConfig
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
    if storage.dag_db is not None and not storage.dag_db.is_absolute():
        storage.dag_db = data_dir / storage.dag_db
    if storage.trigger_db is not None and not storage.trigger_db.is_absolute():
        storage.trigger_db = data_dir / storage.trigger_db
    if storage.auth_db is not None and not storage.auth_db.is_absolute():
        storage.auth_db = data_dir / storage.auth_db
    if storage.log_file is not None and not storage.log_file.is_absolute():
        storage.log_file = data_dir / storage.log_file


def load_namespaces(ns_map: dict[str, Any], data_dir: Path) -> dict[str, Namespace]:
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

    pid_path = _pid_file(data_dir)
    pid_path.write_text(str(os.getpid()))

    try:
        if config.engine.enabled:
            print("  Engine: enabled")
        tornado.ioloop.IOLoop.current().start()
    finally:
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
    import inspect
    import json

    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    config = load_config(root.data)
    data_dir = root.data
    _resolve_storage(config, data_dir)
    namespaces = load_namespaces(config.namespaces, data_dir)

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
