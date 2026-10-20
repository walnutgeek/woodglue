"""
System namespace: server introspection, engine management, and access control.

Builds a Namespace with introspection methods (list_namespaces, list_methods,
describe_method), engine/trigger facade methods, and User and token management,
all tagged `["api"]`. Always mounted as the `system` prefix with
`expose_api=True`.

`system` is not granted as a namespace. Each method checks the action it needs
(see `woodglue.access`) against the namespace it touches:

- Introspection shows only namespaces the Caller can see; others are reported
  as not found.
- `recent_runs`, `active_runs`, `inspect_run`, `load_io`, `child_runs`, and
  `list_triggers` need `read`.
- `fire_trigger`, `activate_trigger`, and `deactivate_trigger` need `trigger`.
- `list_uis` shows the UIs the Caller may open (`view` on `ui:<name>`, or
  public ones).
- `whoami` and a User's own tokens (`create_token`, `list_tokens`,
  `revoke_token`) need no Grant. A narrowed token cannot create tokens, so it
  cannot mint one broader than itself.
- User management (`list_users`, `create_service_user`, `disable_user`,
  `enable_user`, `assign_role`, `unassign_role`) and other Users' tokens need
  `admin`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from lythonic.compose.dag_provenance import DagRun
from lythonic.compose.dag_runner import DagRunResult
from lythonic.compose.namespace import Namespace, NamespaceNode
from pydantic import BaseModel

from woodglue.access import Action, Caller, check, current_caller
from woodglue.apps.llm_docs import API_TAG, walk_namespace
from woodglue.apps.rpc import INVALID_PARAMS, SYSTEM_PREFIX, RpcError
from woodglue.apps.ui_process import UiProcesses, UiState
from woodglue.apps.uis import CONSOLE, ui_path
from woodglue.auth_store import AuthStore, IssuedToken, TokenInfo, UnknownUser, User
from woodglue.config import NamespaceEntry, UiEntry
from woodglue.engine import EngineRegistry, NamespaceEngine


class ArgInfo(BaseModel):
    name: str
    type: str
    required: bool
    description: str | None = None
    default: str | None = None


class TriggerInfo(BaseModel):
    name: str
    schedule: str | None = None


class MethodInfo(BaseModel):
    """
    Method metadata. Summary fields are always populated.
    Detail fields (`doc`, `args`, `return_type`, `cache_config`,
    `trigger_configs`) are only populated by `describe_method`.
    """

    nsref: str
    tags: list[str]
    has_cache: bool
    has_triggers: bool
    doc_teaser: str | None = None

    doc: str | None = None
    args: list[ArgInfo] | None = None
    return_type: str | None = None
    cache_config: dict[str, Any] | None = None
    trigger_configs: list[TriggerInfo] | None = None


class WhoAmI(BaseModel):
    user_id: str | None
    name: str
    email: str | None = None
    roles: list[str]
    narrowed: bool
    """Whether the request's credential is limited to a subset of the User's Grants."""


class UiInfo(BaseModel):
    name: str
    path: str
    label: str
    public: bool
    state: UiState | None = None
    """For a supervised Process-backed UI, whether its process is up."""


class NamespaceInfo(BaseModel):
    prefix: str
    expose_api: bool
    run_engine: bool
    method_count: int
    has_cache: bool


def _docstring_teaser(doc: str | None) -> str:
    if not doc:
        return ""
    return doc.strip().split("\n")[0].strip()


def _node_has_cache(node: NamespaceNode) -> bool:
    return getattr(node.config, "type", None) == "cache"


def _node_has_triggers(node: NamespaceNode) -> bool:
    return bool(node.config.triggers)


def _get_engine(registry: EngineRegistry | None, namespace: str) -> NamespaceEngine:
    if registry is None:
        raise RpcError(
            INVALID_PARAMS, f"No engine for namespace '{namespace}': no engines configured"
        )
    try:
        return registry.get(namespace)
    except KeyError:
        available = ", ".join(registry.list_prefixes()) or "(none)"
        raise RpcError(
            INVALID_PARAMS, f"No engine for namespace '{namespace}'. Available: {available}"
        ) from None


def _can_see(namespace: str) -> bool:
    caller = current_caller.get()
    return namespace == SYSTEM_PREFIX or caller is None or caller.can_see(namespace)


def _require_user_caller() -> Caller:
    caller = current_caller.get()
    if caller is None or caller.user_id is None:
        raise RpcError(INVALID_PARAMS, "Tokens belong to a User; this request has none")
    return caller


def build_system_namespace(
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]],
    registry: EngineRegistry | None,
    auth_store: AuthStore | None = None,
    uis: Mapping[str, UiEntry] | None = None,
    console: bool = True,
    ui_processes: UiProcesses | None = None,
) -> Namespace:
    """
    Build the system Namespace. `auth_store` is `None` when auth is disabled;
    the token and User management methods then report that. `uis` and
    `console` describe the UIs `list_uis` reports.
    """
    ns = Namespace()
    tags = ["api"]

    def _store() -> AuthStore:
        if auth_store is None:
            raise RpcError(INVALID_PARAMS, "Auth is disabled on this server")
        return auth_store

    def _find_user(ref: str) -> User:
        try:
            return _store().find_user(ref)
        except UnknownUser as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    # -- Introspection methods --

    def list_namespaces() -> list[NamespaceInfo]:
        """List all mounted namespaces with config summary."""
        result: list[NamespaceInfo] = []
        for prefix in sorted(p for p in namespaces if _can_see(p)):
            ns_obj, entry = namespaces[prefix]
            nodes = walk_namespace(ns_obj)
            api_nodes = [(ref, node) for ref, node in nodes if API_TAG in node.tags]
            any_cache = any(_node_has_cache(node) for _, node in nodes)
            result.append(
                NamespaceInfo(
                    prefix=prefix,
                    expose_api=entry.expose_api,
                    run_engine=entry.run_engine,
                    method_count=len(api_nodes),
                    has_cache=any_cache,
                )
            )
        return result

    def list_methods(namespace: str) -> list[MethodInfo]:
        """List methods in a namespace with summary metadata."""
        if namespace not in namespaces or not _can_see(namespace):
            raise RpcError(INVALID_PARAMS, f"Namespace '{namespace}' not found")
        ns_obj, _entry = namespaces[namespace]
        result: list[MethodInfo] = []
        for nsref, node in walk_namespace(ns_obj):
            if API_TAG not in node.tags:
                continue
            result.append(
                MethodInfo(
                    nsref=nsref,
                    tags=sorted(node.tags),
                    has_cache=_node_has_cache(node),
                    has_triggers=_node_has_triggers(node),
                    doc_teaser=_docstring_teaser(node.method.doc) or None,
                )
            )
        return result

    def describe_method(namespace: str, nsref: str) -> MethodInfo:
        """Full method detail including args, return type, and config."""
        if namespace not in namespaces or not _can_see(namespace):
            raise RpcError(INVALID_PARAMS, f"Namespace '{namespace}' not found")
        ns_obj, _entry = namespaces[namespace]
        try:
            node = ns_obj.get(nsref)
        except KeyError:
            raise RpcError(
                INVALID_PARAMS, f"Method '{nsref}' not found in namespace '{namespace}'"
            ) from None

        method = node.method
        iface = method.interface
        args_info: list[ArgInfo] = [
            ArgInfo(
                name=p.name,
                type=p.type_str,
                required=not p.is_optional,
                description=p.description or None,
                default=str(p.default) if p.default is not None else None,
            )
            for p in iface.params
        ]
        return_type = iface.return_type

        cache_config: dict[str, Any] | None = None
        if _node_has_cache(node):
            # NsCacheConfig *is* the cache config (type="cache")
            cache_config = {
                k: v
                for k, v in node.config.model_dump().items()
                if k not in ("type", "nsref", "gref", "tags", "triggers")
            }

        trigger_configs: list[TriggerInfo] | None = None
        if _node_has_triggers(node):
            trigger_configs = []
            for tc in node.config.triggers:
                assert tc.name is not None, "trigger name must be set after registration"
                trigger_configs.append(TriggerInfo(name=tc.name, schedule=tc.schedule))

        return MethodInfo(
            nsref=nsref,
            tags=sorted(node.tags),
            has_cache=_node_has_cache(node),
            has_triggers=_node_has_triggers(node),
            doc_teaser=_docstring_teaser(method.doc) or None,
            doc=method.doc.strip() if method.doc else None,
            args=args_info,
            return_type=return_type,
            cache_config=cache_config,
            trigger_configs=trigger_configs,
        )

    # -- Engine methods --

    def recent_runs(namespace: str, limit: int = 20, status: str | None = None) -> list[DagRun]:
        """Recent DAG runs for a namespace."""
        check(Action.read, namespace)
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.get_recent_runs(limit, status)  # pyright: ignore[reportPrivateUsage]

    def active_runs(namespace: str) -> list[DagRun]:
        """Currently active DAG runs for a namespace."""
        check(Action.read, namespace)
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.get_active_runs()  # pyright: ignore[reportPrivateUsage]

    def inspect_run(namespace: str, run_id: str) -> DagRun | None:
        """Inspect a single DAG run by ID."""
        check(Action.read, namespace)
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.inspect_run(run_id)  # pyright: ignore[reportPrivateUsage]

    def load_io(namespace: str, run_id: str, node_labels: list[str] | None = None) -> DagRun | None:
        """Inspect a DAG run and load I/O payloads for its nodes."""
        check(Action.read, namespace)
        engine = _get_engine(registry, namespace)
        prov = engine.namespace._provenance  # pyright: ignore[reportPrivateUsage]
        dag_run = prov.inspect_run(run_id)
        if dag_run is None:
            return None
        labels: Sequence[str] | None = node_labels
        prov.load_io(dag_run, labels)
        return dag_run

    def child_runs(namespace: str, parent_run_id: str) -> list[DagRun]:
        """Get child runs spawned by a parent run."""
        check(Action.read, namespace)
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.get_child_runs(parent_run_id)  # pyright: ignore[reportPrivateUsage]

    def list_triggers(namespace: str) -> list[dict[str, Any]]:
        """List active poll triggers for a namespace."""
        check(Action.read, namespace)
        engine = _get_engine(registry, namespace)
        return engine.trigger_store.get_active_poll_triggers()

    async def fire_trigger(
        namespace: str, name: str, payload: dict[str, Any] | None = None
    ) -> DagRunResult:
        """Fire a trigger by name."""
        check(Action.trigger, namespace)
        engine = _get_engine(registry, namespace)
        with engine.mount.activate():
            return await engine.trigger_manager.fire(name, payload)

    def activate_trigger(namespace: str, name: str) -> dict[str, Any]:
        """Activate a trigger by name."""
        check(Action.trigger, namespace)
        engine = _get_engine(registry, namespace)
        with engine.mount.activate():
            engine.trigger_manager.activate(name)
        activation = engine.trigger_store.get_activation(name)
        return activation or {"name": name, "status": "activated"}

    def deactivate_trigger(namespace: str, name: str) -> dict[str, Any]:
        """Deactivate a trigger by name. It stays disabled across restarts until `activate_trigger`."""
        check(Action.trigger, namespace)
        engine = _get_engine(registry, namespace)
        with engine.mount.activate():
            engine.trigger_manager.deactivate(name)
        return {"name": name, "status": "deactivated"}

    # -- UIs --

    def list_uis() -> list[UiInfo]:
        """UIs this server hosts that you may open, the Console first."""
        caller = current_caller.get()
        result: list[UiInfo] = []
        if console:
            result.append(
                UiInfo(name=CONSOLE, path=ui_path(CONSOLE), label="Console", public=False)
            )
        for name in sorted(uis or {}):
            entry = (uis or {})[name]
            label = entry.label or name.replace("-", " ").replace("_", " ").title()
            state = ui_processes.state(name) if ui_processes else None
            result.append(
                UiInfo(name=name, path=ui_path(name), label=label, public=entry.public, state=state)
            )
        if caller is None:
            return result
        return [u for u in result if u.public or caller.can(Action.view, f"ui:{u.name}")]

    # -- Identity and tokens --

    def whoami() -> WhoAmI:
        """The Caller of this request: User, Roles, and whether its token is narrowed."""
        caller = current_caller.get()
        if caller is None:
            return WhoAmI(user_id=None, name="in-process", roles=[], narrowed=False)
        return WhoAmI(
            user_id=caller.user_id,
            name=caller.name,
            email=caller.email,
            roles=list(caller.roles),
            narrowed=len(caller.grant_sets) > 1,
        )

    def create_token(
        name: str, expires_in_days: int | None = None, grants: list[str] | None = None
    ) -> IssuedToken:
        """
        Create a Personal Access Token for yourself. The secret is returned only
        once. `grants` narrows it to a subset of your Grants.
        """
        caller = _require_user_caller()
        assert caller.user_id is not None
        if len(caller.grant_sets) > 1:
            raise RpcError(INVALID_PARAMS, "A narrowed token cannot create tokens")
        expires_at = (
            datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
        )
        try:
            return _store().create_token(caller.user_id, name, expires_at=expires_at, grants=grants)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    def list_tokens(user: str | None = None) -> list[TokenInfo]:
        """Your Personal Access Tokens, or another User's (needs `admin`). Secrets are never shown."""
        caller = _require_user_caller()
        user_id = caller.user_id
        if user is not None:
            target = _find_user(user)
            if target.id != caller.user_id:
                check(Action.admin)
            user_id = target.id
        return _store().list_tokens(user_id)

    def revoke_token(token_id: str) -> bool:
        """Revoke one of your Personal Access Tokens, or any token with `admin`."""
        caller = _require_user_caller()
        owner = _store().token_owner(token_id)
        if owner is None or (owner != caller.user_id and not caller.can(Action.admin)):
            raise RpcError(INVALID_PARAMS, f"No token {token_id!r}")
        return _store().revoke_token(token_id)

    # -- User management (admin) --

    def list_users() -> list[User]:
        """All Users with their Roles."""
        check(Action.admin)
        return _store().list_users()

    def create_service_user(name: str) -> User:
        """Create a Service User, for daemons and CI."""
        check(Action.admin)
        try:
            return _store().create_service_user(name)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    def disable_user(user: str) -> None:
        """Disable a User and revoke all of its credentials."""
        check(Action.admin)
        try:
            _store().set_disabled(_find_user(user).id, True)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    def enable_user(user: str) -> None:
        """Re-enable a disabled User. Its revoked credentials stay revoked."""
        check(Action.admin)
        _store().set_disabled(_find_user(user).id, False)

    def assign_role(user: str, role: str) -> None:
        """Give a User a Role."""
        check(Action.admin)
        try:
            _store().assign_role(_find_user(user).id, role)
        except ValueError as exc:
            raise RpcError(INVALID_PARAMS, str(exc)) from None

    def unassign_role(user: str, role: str) -> None:
        """Take a Role away from a User."""
        check(Action.admin)
        _store().unassign_role(_find_user(user).id, role)

    for fn, fn_nsref in [
        (list_uis, "list_uis"),
        (whoami, "whoami"),
        (create_token, "create_token"),
        (list_tokens, "list_tokens"),
        (revoke_token, "revoke_token"),
        (list_users, "list_users"),
        (create_service_user, "create_service_user"),
        (disable_user, "disable_user"),
        (enable_user, "enable_user"),
        (assign_role, "assign_role"),
        (unassign_role, "unassign_role"),
        (list_namespaces, "list_namespaces"),
        (list_methods, "list_methods"),
        (describe_method, "describe_method"),
        (recent_runs, "recent_runs"),
        (active_runs, "active_runs"),
        (inspect_run, "inspect_run"),
        (load_io, "load_io"),
        (child_runs, "child_runs"),
        (list_triggers, "list_triggers"),
        (fire_trigger, "fire_trigger"),
        (activate_trigger, "activate_trigger"),
        (deactivate_trigger, "deactivate_trigger"),
    ]:
        ns.register(fn, nsref=fn_nsref, tags=tags)

    return ns
