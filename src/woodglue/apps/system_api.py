"""
System namespace: server introspection and engine management.

Builds a Namespace with introspection methods (list_namespaces, list_methods,
describe_method) plus engine/trigger facade methods, all tagged `["api"]`.
Always mounted as the `system` prefix with `expose_api=True`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import Any

from lythonic.compose import Method
from lythonic.compose.dag_provenance import DagRun
from lythonic.compose.dag_runner import DagRunResult
from lythonic.compose.namespace import Namespace, NamespaceNode
from pydantic import BaseModel

from woodglue.apps.llm_docs import API_TAG, walk_namespace
from woodglue.config import NamespaceEntry
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


class NamespaceInfo(BaseModel):
    prefix: str
    expose_api: bool
    run_engine: bool
    method_count: int
    has_cache: bool


def _type_display(annotation: Any) -> str:
    """Human-readable name for a type annotation."""
    if annotation is None or annotation is inspect.Parameter.empty:
        return "str"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


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
        raise ValueError("No engines configured")
    try:
        return registry.get(namespace)
    except KeyError:
        available = ", ".join(registry.list_prefixes()) or "(none)"
        raise ValueError(f"No engine for namespace '{namespace}'. Available: {available}") from None


def _register_closure(ns: Namespace, fn: Callable[..., Any], nsref: str, tags: list[str]) -> None:
    """Register a closure/inner function that can't be resolved by GlobalRef."""
    method = Method(fn)
    node = NamespaceNode(method=method, nsref=nsref, namespace=ns, tags=tags)
    ns._nodes[nsref] = node  # pyright: ignore[reportPrivateUsage]


def build_system_namespace(
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]],
    registry: EngineRegistry | None,
) -> Namespace:
    """Build a Namespace with introspection and engine facade functions."""
    ns = Namespace()
    tags = ["api"]

    # -- Introspection methods --

    def list_namespaces() -> list[NamespaceInfo]:
        """List all mounted namespaces with config summary."""
        result: list[NamespaceInfo] = []
        for prefix in sorted(namespaces):
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
        if namespace not in namespaces:
            raise ValueError(f"Namespace '{namespace}' not found")
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
        if namespace not in namespaces:
            raise ValueError(f"Namespace '{namespace}' not found")
        ns_obj, _entry = namespaces[namespace]
        try:
            node = ns_obj.get(nsref)
        except KeyError:
            raise ValueError(f"Method '{nsref}' not found in namespace '{namespace}'") from None

        method = node.method
        args_info: list[ArgInfo] = []
        for arg in method.args:
            default_str: str | None = None
            if arg.default is not None and arg.default is not inspect.Parameter.empty:
                default_str = str(arg.default)
            args_info.append(
                ArgInfo(
                    name=arg.name,
                    type=_type_display(arg.annotation),
                    required=not arg.is_optional,
                    description=arg.description or None,
                    default=default_str,
                )
            )

        ret = method.return_annotation
        return_type: str | None = None
        if ret is not None and ret is not inspect.Parameter.empty:
            return_type = _type_display(ret)

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
            trigger_configs = [
                TriggerInfo(name=tc.name, schedule=tc.schedule) for tc in node.config.triggers
            ]

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
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.get_recent_runs(limit, status)  # pyright: ignore[reportPrivateUsage]

    def active_runs(namespace: str) -> list[DagRun]:
        """Currently active DAG runs for a namespace."""
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.get_active_runs()  # pyright: ignore[reportPrivateUsage]

    def inspect_run(namespace: str, run_id: str) -> DagRun | None:
        """Inspect a single DAG run by ID."""
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.inspect_run(run_id)  # pyright: ignore[reportPrivateUsage]

    def load_io(namespace: str, run_id: str, node_labels: list[str] | None = None) -> DagRun | None:
        """Inspect a DAG run and load I/O payloads for its nodes."""
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
        engine = _get_engine(registry, namespace)
        return engine.namespace._provenance.get_child_runs(parent_run_id)  # pyright: ignore[reportPrivateUsage]

    def list_triggers(namespace: str) -> list[dict[str, Any]]:
        """List active poll triggers for a namespace."""
        engine = _get_engine(registry, namespace)
        return engine.trigger_store.get_active_poll_triggers()

    async def fire_trigger(
        namespace: str, name: str, payload: dict[str, Any] | None = None
    ) -> DagRunResult:
        """Fire a trigger by name."""
        engine = _get_engine(registry, namespace)
        return await engine.trigger_manager.fire(name, payload)

    def activate_trigger(namespace: str, name: str) -> dict[str, Any]:
        """Activate a trigger by name."""
        engine = _get_engine(registry, namespace)
        engine.trigger_manager.activate(name)
        activation = engine.trigger_store.get_activation(name)
        return activation or {"name": name, "status": "activated"}

    def deactivate_trigger(namespace: str, name: str) -> dict[str, Any]:
        """Deactivate a trigger by name."""
        engine = _get_engine(registry, namespace)
        engine.trigger_manager.deactivate(name)
        return {"name": name, "status": "deactivated"}

    for fn, fn_nsref in [
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
        _register_closure(ns, fn, fn_nsref, tags)

    return ns
