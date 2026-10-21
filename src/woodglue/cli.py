"""
wgl -- woodglue server CLI.

Commands:
    wgl start          Start the server (and optionally the engine)
    wgl stop           Stop a running instance
    wgl run <nsref>    Run a callable or DAG once
    wgl status         Show server status
    wgl token          Print the admin token(s), creating one if none exist
    wgl token --new    Replace all admin tokens with a single new one
    wgl user ...       Manage Users, Role assignments, and Personal Access Tokens:
        list                       Users with their kind, Roles, and state
        add <name>                 Create a Service User
        assign|unassign <user> <role>
        disable|enable <user>      Disabling also revokes the User's credentials
        issue <user> <name> [--days=N] [--grants="call reports, read *"]
                                   Create a Personal Access Token; prints the secret
        tokens <user>              List a User's tokens (never the secrets)
        revoke <token_id>
    wgl caddy config <site>   Print a Caddyfile serving this instance at <site>
    wgl login --server=URL    Get a Personal Access Token for a remote server via the browser
    wgl logout --server=URL   Revoke and forget that token

`wgl token` prints the tokens of the built-in `admin` Service User, which holds
every Grant. `wgl start` never prints a token, since its output may be captured
in logs (e.g. the systemd journal). Every change takes effect immediately: the
server checks `auth.db` on every request.

A `<user>` is a User id, email, or (unique) name.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

from lythonic import GlobalRef
from lythonic.compose.cli import ActionTree, Main, RunContext
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel, Field

from woodglue.config import CONFIG_FILENAME, NamespaceEntry, WoodglueConfig, load_config
from woodglue.mount import MountContext

if TYPE_CHECKING:
    from woodglue.auth_store import AuthStore


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
    """Resolve storage and UI paths relative to data_dir, in place."""
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
    for entry in config.uis.values():
        if entry.path is not None:
            entry.path = str(data_dir / entry.path)
        if entry.app is not None:
            entry.app = str(data_dir / entry.app)
        entry.cwd = str(data_dir / entry.cwd) if entry.cwd else str(data_dir)


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

    # Auth setup. Never print the token: under systemd stdout goes to the journal.
    auth_store = None
    if config.auth.enabled:
        from woodglue.auth_store import AuthStore

        assert config.storage.auth_db is not None
        auth_store = AuthStore(config.storage.auth_db, config.auth.resolved_roles())
        auth_store.ensure_admin_token()
        print("  Auth enabled; run 'wgl token' to see the admin token")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  WARNING: auth is disabled and the server listens on {host}; anyone who")
        print("  can reach it has full access")

    namespaces = load_namespaces(config.namespaces, data_dir)

    # Build MountContext for every namespace
    mounts: dict[str, MountContext] = {
        prefix: MountContext(prefix, data_dir) for prefix in namespaces
    }

    # Mount and build engines for namespaces with run_engine=True
    from lythonic.compose.engine import StorageConfig as LythStorageConfig

    from woodglue.engine import EngineRegistry, create_engine

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
                triggers = engine.trigger_manager.activate_configured()
            registry.register(engine)
            if triggers.activated:
                print(f"  Triggers activated for '{prefix}': {', '.join(triggers.activated)}")
            for name in triggers.left_disabled:
                print(f"  Trigger '{name}' left disabled in '{prefix}'")

    from woodglue.apps.ui_process import UiProcesses

    api_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    ui_processes = UiProcesses(config.uis, f"http://{api_host}:{port}")

    # Always mount the system namespace (introspection + engine facade)
    from woodglue.apps.system_api import build_system_namespace

    system_ns = build_system_namespace(
        namespaces,
        registry if registry.has_engines() else None,
        auth_store,
        uis=config.uis,
        console=config.ui.enabled,
        ui_processes=ui_processes,
    )
    system_entry = NamespaceEntry(gref="builtin:system", expose_api=True)
    namespaces["system"] = (system_ns, system_entry)
    mounts["system"] = MountContext("system", data_dir)

    app = create_app(
        namespaces=namespaces,
        config=config,
        engine_registry=registry,
        mounts=mounts,
        auth_store=auth_store,
        ui_processes=ui_processes,
    )
    server = app.listen(port, host)
    print(f"Woodglue listening on http://{host}:{port}")
    print(f"  RPC endpoint: http://{host}:{port}/rpc")
    if config.docs.enabled:
        print(f"  LLM docs:     http://{host}:{port}/docs/llms.txt")
    if config.ui.enabled:
        print(f"  Console:      http://{host}:{port}/console/")
    for name in config.uis:
        print(f"  UI:           http://{host}:{port}/ui/{name}/")

    if registry.has_engines():
        print(f"  Engine: enabled ({', '.join(registry.list_prefixes())})")

    ioloop = tornado.ioloop.IOLoop.current()

    # Shut down on the running loop so engine tasks are cancelled by live loop code,
    # rather than via run_until_complete after the loop has already stopped.
    async def _shutdown(signal_name: str) -> None:
        print(f"Received {signal_name}, shutting down")
        server.stop()
        await registry.stop_all()
        await ui_processes.stop_all()
        ioloop.stop()

    _install_shutdown_handlers(
        ioloop.asyncio_loop,  # pyright: ignore[reportAttributeAccessIssue]
        lambda signal_name: ioloop.add_callback(_shutdown, signal_name),
    )

    pid_path = _pid_file(data_dir)
    pid_path.write_text(str(os.getpid()))

    # Start trigger managers, then UI processes, once the IOLoop is running
    if registry.has_engines():
        ioloop.add_callback(registry.start_all)
    if ui_processes.has_processes():
        ioloop.add_callback(ui_processes.start_all)

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

    import getpass

    from lythonic.compose.dag_provenance import initiated_by

    # asyncio.run copies the current context, so the node sees the mount and initiator
    with MountContext(node_prefix, data_dir).activate(), initiated_by(f"cli:{getpass.getuser()}"):
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


def _auth_store(ctx: RunContext) -> AuthStore:
    """Open `auth.db` for a CLI action, exiting if auth is disabled."""
    from woodglue.auth_store import AuthStore

    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    data_dir = root.data.resolve()
    config = load_config(data_dir)
    if not config.auth.enabled:
        sys.exit(f"Auth is disabled in {data_dir / CONFIG_FILENAME}; there are no tokens or Users")
    _resolve_storage(config, data_dir)
    assert config.storage.auth_db is not None
    return AuthStore(config.storage.auth_db, config.auth.resolved_roles())


@main_at.actions.wrap
def token(ctx: RunContext, new: bool = False) -> None:
    """Print the admin token(s); --new replaces all of them with a new one"""
    store = _auth_store(ctx)
    if new:
        print(store.rotate_admin_token())
        return
    store.ensure_admin_token()
    for t in store.admin_tokens():
        print(t)


class UserGroup(BaseModel):
    """Manage Users, Role assignments, and Personal Access Tokens"""


user_at = ActionTree(UserGroup)
user_at.name = "user"
main_at.actions[user_at.name] = user_at


def _user_action(name: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Register a `wgl user <name>` action, turning lookup errors into a clean exit."""

    def register(fn: Callable[..., None]) -> Callable[..., None]:
        from functools import wraps

        from woodglue.auth_store import UnknownUser

        @wraps(fn)
        def run(*args: Any, **kwargs: Any) -> None:
            try:
                fn(*args, **kwargs)
            except (UnknownUser, ValueError) as exc:
                sys.exit(str(exc))

        run.__name__ = name
        user_at.actions.add(run)
        return fn

    return register


@_user_action("list")
def user_list(ctx: RunContext) -> None:
    """List Users with their kind, Roles, and state"""
    for u in _auth_store(ctx).list_users():
        state = " disabled" if u.disabled else ""
        email = f" <{u.email}>" if u.email else ""
        print(f"{u.id}\t{u.kind}\t{u.name}{email}\troles={','.join(u.roles)}{state}")


@_user_action("add")
def user_add(ctx: RunContext, name: str) -> None:
    """Create a Service User"""
    print(_auth_store(ctx).create_service_user(name).id)


@_user_action("assign")
def user_assign(ctx: RunContext, user: str, role: str) -> None:
    """Give a User a Role"""
    store = _auth_store(ctx)
    store.assign_role(store.find_user(user).id, role)


@_user_action("unassign")
def user_unassign(ctx: RunContext, user: str, role: str) -> None:
    """Take a Role away from a User"""
    store = _auth_store(ctx)
    store.unassign_role(store.find_user(user).id, role)


@_user_action("disable")
def user_disable(ctx: RunContext, user: str) -> None:
    """Disable a User and revoke all of its credentials"""
    store = _auth_store(ctx)
    store.set_disabled(store.find_user(user).id, True)


@_user_action("enable")
def user_enable(ctx: RunContext, user: str) -> None:
    """Re-enable a disabled User"""
    store = _auth_store(ctx)
    store.set_disabled(store.find_user(user).id, False)


@_user_action("issue")
def user_issue(ctx: RunContext, user: str, name: str, days: int = 0, grants: str = "") -> None:
    """Create a Personal Access Token and print its secret; --grants narrows it (comma-separated)"""
    from datetime import UTC, datetime, timedelta

    store = _auth_store(ctx)
    expires_at = datetime.now(UTC) + timedelta(days=days) if days else None
    grant_list = [g.strip() for g in grants.split(",") if g.strip()] or None
    issued = store.create_token(
        store.find_user(user).id, name, expires_at=expires_at, grants=grant_list
    )
    print(issued.secret)


@_user_action("tokens")
def user_tokens(ctx: RunContext, user: str) -> None:
    """List a User's Personal Access Tokens (never the secrets)"""
    store = _auth_store(ctx)
    for t in store.list_tokens(store.find_user(user).id):
        expires = t.expires_at.isoformat() if t.expires_at else "never"
        scope = ", ".join(t.grants) if t.grants is not None else "all of the User's Grants"
        print(f"{t.id}\t{t.name}\texpires={expires}\t{scope}")


@_user_action("revoke")
def user_revoke(ctx: RunContext, token_id: str) -> None:
    """Revoke a Personal Access Token"""
    if not _auth_store(ctx).revoke_token(token_id):
        sys.exit(f"No token {token_id!r}")


@main_at.actions.wrap
def login(ctx: RunContext, server: str = "", name: str = "") -> None:  # pyright: ignore[reportUnusedParameter]
    """Get a Personal Access Token for --server through the browser and store it"""
    import socket

    from woodglue.cli_login import credentials_path
    from woodglue.cli_login import login as do_login

    if not server:
        sys.exit("Give the server, e.g. wgl login --server=https://wg.example.com")
    try:
        do_login(server, name or f"wgl on {socket.gethostname()}")
    except RuntimeError as exc:
        sys.exit(str(exc))
    print(f"Signed in to {server}; token stored in {credentials_path()}")


@main_at.actions.wrap
def logout(ctx: RunContext, server: str = "") -> None:  # pyright: ignore[reportUnusedParameter]
    """Revoke and forget the token stored for --server"""
    from woodglue.cli_login import logout as do_logout

    if not server:
        sys.exit("Give the server, e.g. wgl logout --server=https://wg.example.com")
    print("Signed out" if do_logout(server) else f"No token stored for {server}")


class CaddyGroup(BaseModel):
    """Run woodglue behind Caddy"""


caddy_at = ActionTree(CaddyGroup)
caddy_at.name = "caddy"
main_at.actions[caddy_at.name] = caddy_at


@caddy_at.actions.wrap
def config(ctx: RunContext, site: str) -> None:
    """Print a Caddyfile serving this instance at <site>, e.g. wg.example.com"""
    from woodglue.caddy import caddyfile

    root: WoodglueMain = ctx.path.get("/")  # pyright: ignore[reportAssignmentType]
    data_dir = root.data.resolve()
    cfg = load_config(data_dir)
    _resolve_storage(cfg, data_dir)
    print(caddyfile(cfg, site), end="")


def main() -> None:
    if not main_at.run_args(sys.argv).success:
        sys.exit(1)


if __name__ == "__main__":
    main()
