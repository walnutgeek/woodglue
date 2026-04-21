"""
Engine API facade namespace.

Builds a lythonic Namespace with thin wrappers around per-namespace
`DagProvenance` and `TriggerManager` methods, all tagged `["api"]`.
Auto-mounted as the `engine` prefix when any namespace has `run_engine=True`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from lythonic.compose import Method
from lythonic.compose.dag_provenance import DagRun
from lythonic.compose.dag_runner import DagRunResult
from lythonic.compose.namespace import Namespace, NamespaceNode

from woodglue.engine import EngineRegistry, NamespaceEngine


def _get_engine(registry: EngineRegistry, namespace: str) -> NamespaceEngine:
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


def build_engine_namespace(registry: EngineRegistry) -> Namespace:
    """Build a Namespace with engine facade functions for RPC exposure."""
    ns = Namespace()
    tags = ["api"]

    def list_namespaces() -> list[str]:
        """List prefixes of all engine-enabled namespaces."""
        return registry.list_prefixes()

    def recent_runs(namespace: str, limit: int = 20, status: str | None = None) -> list[DagRun]:
        """Recent DAG runs for a namespace."""
        engine = _get_engine(registry, namespace)
        return engine.provenance.get_recent_runs(limit, status)

    def active_runs(namespace: str) -> list[DagRun]:
        """Currently active DAG runs for a namespace."""
        engine = _get_engine(registry, namespace)
        return engine.provenance.get_active_runs()

    def inspect_run(namespace: str, run_id: str) -> DagRun | None:
        """Inspect a single DAG run by ID."""
        engine = _get_engine(registry, namespace)
        return engine.provenance.inspect_run(run_id)

    def load_io(namespace: str, run_id: str, node_labels: list[str] | None = None) -> DagRun | None:
        """Inspect a DAG run and load I/O payloads for its nodes."""
        engine = _get_engine(registry, namespace)
        dag_run = engine.provenance.inspect_run(run_id)
        if dag_run is None:
            return None
        labels: Sequence[str] | None = node_labels
        engine.provenance.load_io(dag_run, labels)
        return dag_run

    def child_runs(namespace: str, parent_run_id: str) -> list[DagRun]:
        """Get child runs spawned by a parent run."""
        engine = _get_engine(registry, namespace)
        return engine.provenance.get_child_runs(parent_run_id)

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

    for fn, nsref in [
        (list_namespaces, "list_namespaces"),
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
        _register_closure(ns, fn, nsref, tags)

    return ns
