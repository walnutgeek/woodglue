"""
wgl -- woodglue server CLI.

Commands:
    wgl start          Start the server (and optionally the engine)
    wgl stop           Stop a running instance
    wgl run <nsref>    Run a callable or DAG once
    wgl status         Show server status
    wgl token          Print the auth token(s), creating one if none exist
    wgl token --new    Replace all auth tokens with a single new one

`wgl start` never prints the token, since its output may be captured in logs
(e.g. the systemd journal). Use `wgl token` to read it. Rotation with `--new`
takes effect immediately: the server checks `auth.db` on every request.

`wgl start` activates configured triggers but leaves paused (disabled) ones
paused, listing them as `Trigger 'x' left disabled`.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from lythonic import GlobalRef
from lythonic.compose.cli import ActionTree, Main, RunContext
from lythonic.compose.namespace import Namespace
from pydantic import Field

from woodglue.config import CONFIG_FILENAME, NamespaceEntry, WoodglueConfig, load_config
from woodglue.mount import MountContext


class WoodglueMain(Main):
    """wgl -- woodglue server CLI"""

    data: Path = Field(default=Path("./data"), description="data directory")
    port: int = Field(default=5321, description="port to listen on")
    host: str = Field(default="127.0.0.1", description="host to bind to")


main_at = ActionTree(WoodglueMain)


def _pid_file(data_dir: Path) -> Path:
    return data_dir / "wgl.pid"


def _install_shutdown_handlers(
    loop: asyncio.AbstractEventLoop, on_signal: Callable[[str], None]
) -> None:
    """
    Route SIGTERM and SIGINT to `on_signal(signal_name)`, called on the loop's thread.

    Without this, SIGTERM (from `wgl stop` or systemd) kills the process without
    unwinding, so engines never stop and `wgl.pid` is left behind. A signal that
    is already ignored at startup (e.g. SIGINT for a `nohup` or background job)
    stays ignored, matching Python's own SIGINT behavior.
    """
    for sig in (signal.SIGTERM, signal.SIGINT):
        if signal.getsignal(sig) is signal.SIG_IGN:
            continue
        try:
            loop.add_signal_handler(sig, on_signal, sig.name)
        except NotImplementedError:
            # Windows event loops lack add_signal_handler; hop onto the loop explicitly.
            def _handler(signum: int, _frame: FrameType | None) -> None:
                loop.call_soon_threadsafe(on_signal, signal.Signals(signum).name)

            signal.signal(sig, _handler)


def _resolve_storage(config: WoodglueConfig, data_dir: Path) -> None:
    """Resolve storage paths relative to data_dir, in place."""
    from lythonic.compose.engine import resolve_file

    storage = config.storage
    user_log_file = storage.log_file  # save before resolve_paths overwrites
    storage.resolve_paths(data_dir)
    # Override log_file default ("lyth.log" -> "wgl.log"); an explicit null means no file log.
    if storage.log_file_explicit and user_log_file is None:
        storage.log_file = None
    else:
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

    Each namespace is built with its `MountContext` active, so fragment
    constructors can read `current_mount`.
    """
    result: dict[str, tuple[Namespace, NamespaceEntry]] = {}
    for prefix, ns_entry in ns_map.items():
        with MountContext(prefix, data_dir).activate():
            result[prefix] = (_build_namespace(ns_entry, data_dir), ns_entry)
    return result


def _build_namespace(ns_entry: NamespaceEntry, data_dir: Path) -> Namespace:
    import yaml

    if ns_entry.gref is not None:
        ns = GlobalRef(ns_entry.gref).get_instance()
        assert isinstance(ns, Namespace), f"{ns_entry.gref} is not a Namespace"
        return ns
    if ns_entry.file is not None:
        raw = yaml.safe_load((data_dir / ns_entry.file).read_text())
        return Namespace.from_dict(raw.get("namespace", []))
    assert ns_entry.entries is not None
    return Namespace.from_dict(ns_entry.entries)


@main_at.actions.wrap
def start(ctx: RunContext) -> None:  # pyright: ignore[reportUnusedParameter]
    """Start the server (and optionally the engine)"""
    import tornado.ioloop

    from woodglue.apps.server import create_app

    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    root.data.mkdir(parents=True, exist_ok=True)
    data_dir = root.data.resolve()

    config = load_config(data_dir)
    _resolve_storage(config, data_dir)

    from woodglue.log_setup import setup_logging

    print(f"  {setup_logging(config)}")

    # CLI args override config values
    host = root.host if root.host != "127.0.0.1" else config.host
    port = root.port if root.port != 5321 else config.port

    # Auth token setup. Never print the token: under systemd stdout goes to the journal.
    if config.auth.enabled:
        from woodglue.token_store import ensure_token

        assert config.storage.auth_db is not None
        ensure_token(config.storage.auth_db)
        print("  Auth enabled; run 'wgl token' to see the token")

    namespaces = load_namespaces(config.namespaces, data_dir)

    # Build MountContext for every namespace
    mounts: dict[str, MountContext] = {
        prefix: MountContext(prefix, data_dir) for prefix in namespaces
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
            engine = create_engine(mount, ns)
            with mount.activate():
                triggers = activate_triggers(engine)
            registry.register(engine)
            if triggers.activated:
                print(f"  Triggers activated for '{prefix}': {', '.join(triggers.activated)}")
            for name in triggers.left_disabled:
                print(f"  Trigger '{name}' left disabled in '{prefix}'")

    # Always mount the system namespace (introspection + engine facade)
    from woodglue.apps.system_api import build_system_namespace

    system_ns = build_system_namespace(namespaces, registry if registry.has_engines() else None)
    system_entry = NamespaceEntry(gref="builtin:system", expose_api=True)
    namespaces["system"] = (system_ns, system_entry)
    mounts["system"] = MountContext("system", data_dir)

    app = create_app(namespaces=namespaces, config=config, engine_registry=registry, mounts=mounts)
    server = app.listen(port, host)
    print(f"Woodglue listening on http://{host}:{port}")
    print(f"  RPC endpoint: http://{host}:{port}/rpc")
    if config.docs.enabled:
        print(f"  LLM docs:     http://{host}:{port}/docs/llms.txt")
    if config.ui.enabled:
        print(f"  UI:           http://{host}:{port}/ui/")

    if registry.has_engines():
        print(f"  Engine: enabled ({', '.join(registry.list_prefixes())})")

    ioloop = tornado.ioloop.IOLoop.current()

    # Shut down on the running loop so engine tasks are cancelled by live loop code,
    # rather than via run_until_complete after the loop has already stopped.
    async def _shutdown(signal_name: str) -> None:
        print(f"Received {signal_name}, shutting down")
        server.stop()
        await registry.stop_all()
        ioloop.stop()

    _install_shutdown_handlers(
        ioloop.asyncio_loop,  # pyright: ignore[reportAttributeAccessIssue]
        lambda signal_name: ioloop.add_callback(_shutdown, signal_name),
    )

    pid_path = _pid_file(data_dir)
    pid_path.write_text(str(os.getpid()))

    # Start trigger managers once the IOLoop is running
    if registry.has_engines():
        ioloop.add_callback(registry.start_all)

    try:
        ioloop.start()
    finally:
        pid_path.unlink(missing_ok=True)


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
    data_dir = root.data.resolve()
    config = load_config(data_dir)
    _resolve_storage(config, data_dir)
    namespaces = load_namespaces(config.namespaces, data_dir)

    node = None
    node_prefix = ""
    for prefix, (ns, _entry) in namespaces.items():
        try:
            node = ns.get(nsref)
            node_prefix = prefix
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

    # asyncio.run copies the current context, so the node sees the mount
    with MountContext(node_prefix, data_dir).activate():
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


@main_at.actions.wrap
def token(ctx: RunContext, new: bool = False) -> None:
    """Print the auth token(s); --new replaces all tokens with a new one"""
    from woodglue.token_store import ensure_token, list_tokens, rotate_token

    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    data_dir = root.data.resolve()
    config = load_config(data_dir)
    if not config.auth.enabled:
        sys.exit(f"Auth is disabled in {data_dir / CONFIG_FILENAME}; no token to show")
    _resolve_storage(config, data_dir)
    assert config.storage.auth_db is not None

    if new:
        print(rotate_token(config.storage.auth_db))
        return
    ensure_token(config.storage.auth_db)
    for t in list_tokens(config.storage.auth_db):
        print(t)


def main() -> None:
    if not main_at.run_args(sys.argv).success:
        sys.exit(1)


if __name__ == "__main__":
    main()
