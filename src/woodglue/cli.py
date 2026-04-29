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

from lythonic import GlobalRef
from lythonic.compose.cli import ActionTree, Main, RunContext
from lythonic.compose.namespace import Namespace
from pydantic import Field

from woodglue.config import NamespaceEntry, WoodglueConfig, load_config


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
    from lythonic.compose.engine import resolve_file

    storage = config.storage
    user_log_file = storage.log_file  # save before resolve_paths overwrites
    storage.resolve_paths(data_dir)
    # Override log_file default ("lyth.log" -> "wgl.log")
    storage.log_file = resolve_file(data_dir, user_log_file, "wgl.log")
    # Resolve auth_db (woodglue-specific)
    storage.auth_db = resolve_file(data_dir, storage.auth_db, "auth.db")


def load_namespaces(
    ns_map: dict[str, NamespaceEntry], data_dir: Path
) -> dict[str, tuple[Namespace, NamespaceEntry]]:
    """
    Load all namespaces from config, keyed by prefix.

    Each `NamespaceEntry` specifies exactly one of `gref`, `file`, or
    `entries`. Returns `(Namespace, NamespaceEntry)` tuples so callers
    can inspect per-namespace flags like `expose_api` and `run_engine`.
    """
    import yaml

    result: dict[str, tuple[Namespace, NamespaceEntry]] = {}
    for prefix, ns_entry in ns_map.items():
        if ns_entry.gref is not None:
            gref = GlobalRef(ns_entry.gref)
            ns = gref.get_instance()
            assert isinstance(ns, Namespace), f"{ns_entry.gref} is not a Namespace"
            result[prefix] = (ns, ns_entry)
        elif ns_entry.file is not None:
            config_path = data_dir / ns_entry.file
            raw = yaml.safe_load(config_path.read_text())
            ns = Namespace.from_dict(raw.get("namespace", []))
            result[prefix] = (ns, ns_entry)
        elif ns_entry.entries is not None:
            entries = [e.model_dump(exclude_none=True) for e in ns_entry.entries]
            ns = Namespace.from_dict(entries)
            result[prefix] = (ns, ns_entry)
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

    # File logging (same format as lyth)
    from lythonic.compose.engine import LogConfig

    LogConfig(
        log_file=config.storage.log_file,
        log_level=config.storage.log_level,
        loggers=config.storage.loggers,
    ).setup_logging()
    print(f"  Logging to {config.storage.log_file}")

    # CLI args override config values
    host = root.host if root.host != "127.0.0.1" else config.host
    port = root.port if root.port != 5321 else config.port

    # Auth token setup
    if config.auth.enabled:
        from woodglue.token_store import ensure_token, get_single_token

        assert config.storage.auth_db is not None
        ensure_token(config.storage.auth_db)
        single = get_single_token(config.storage.auth_db)
        if single:
            print(f"  Auth token: {single}")
        else:
            print("  Auth enabled (multiple tokens configured)")

    namespaces = load_namespaces(config.namespaces, data_dir)

    # Build MountContext for every namespace
    from woodglue.mount import MountContext

    mounts_dir = data_dir / "mounts"
    mounts: dict[str, MountContext] = {
        prefix: MountContext(prefix, mounts_dir) for prefix in namespaces
    }

    # Mount and build engines for namespaces with run_engine=True
    from lythonic.compose.engine import StorageConfig as LythStorageConfig

    from woodglue.engine import EngineRegistry, activate_triggers, create_engine

    registry = EngineRegistry()
    for prefix, (ns, entry) in namespaces.items():
        if entry.run_engine:
            mount = mounts[prefix]
            storage = LythStorageConfig()
            storage.resolve_paths(mount.state_dir)
            storage.log_file = None  # global logging already configured
            ns.mount(storage)
            engine = create_engine(prefix, ns)
            activated = activate_triggers(engine)
            registry.register(engine)
            if activated:
                print(f"  Triggers activated for '{prefix}': {', '.join(activated)}")

    # Always mount the system namespace (introspection + engine facade)
    from woodglue.apps.system_api import build_system_namespace

    system_ns = build_system_namespace(namespaces, registry if registry.has_engines() else None)
    system_entry = NamespaceEntry(gref="builtin:system", expose_api=True)
    namespaces["system"] = (system_ns, system_entry)
    mounts["system"] = MountContext("system", mounts_dir)

    app = create_app(namespaces=namespaces, config=config, engine_registry=registry, mounts=mounts)
    app.listen(port, host)
    print(f"Woodglue listening on http://{host}:{port}")
    print(f"  RPC endpoint: http://{host}:{port}/rpc")
    if config.docs.enabled:
        print(f"  LLM docs:     http://{host}:{port}/docs/llms.txt")
    if config.ui.enabled:
        print(f"  UI:           http://{host}:{port}/ui/")

    if registry.has_engines():
        print(f"  Engine: enabled ({', '.join(registry.list_prefixes())})")

    pid_path = _pid_file(data_dir)
    pid_path.write_text(str(os.getpid()))

    # Start trigger managers once the IOLoop is running
    if registry.has_engines():
        tornado.ioloop.IOLoop.current().add_callback(registry.start_all)

    try:
        tornado.ioloop.IOLoop.current().start()
    finally:
        if registry.has_engines():
            import asyncio

            loop = asyncio.get_event_loop()
            loop.run_until_complete(registry.stop_all())
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
    for ns, _entry in namespaces.values():
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
