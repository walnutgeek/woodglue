"""Per-namespace engine instances and registry."""

from __future__ import annotations

from dataclasses import dataclass

from lythonic.compose.dag_provenance import DagProvenance
from lythonic.compose.namespace import Namespace
from lythonic.compose.trigger import TriggerManager, TriggerStore

from woodglue.mount import MountContext


@dataclass
class NamespaceEngine:
    """Engine instances for a single namespace."""

    prefix: str
    namespace: Namespace
    provenance: DagProvenance
    trigger_store: TriggerStore
    trigger_manager: TriggerManager


class EngineRegistry:
    """Manages per-namespace engine instances."""

    def __init__(self) -> None:
        self._engines: dict[str, NamespaceEngine] = {}

    def register(self, engine: NamespaceEngine) -> None:
        """Add an engine by prefix."""
        self._engines[engine.prefix] = engine

    def get(self, prefix: str) -> NamespaceEngine:
        """Lookup by prefix. Raises `KeyError` if not found."""
        return self._engines[prefix]

    def list_prefixes(self) -> list[str]:
        """Sorted list of registered prefixes."""
        return sorted(self._engines)

    def has_engines(self) -> bool:
        """True if any engines are registered."""
        return bool(self._engines)

    async def start_all(self) -> None:
        """Start all TriggerManagers."""
        for engine in self._engines.values():
            engine.trigger_manager.start()

    async def stop_all(self) -> None:
        """Stop all TriggerManagers."""
        for engine in self._engines.values():
            engine.trigger_manager.stop()


def create_engine(prefix: str, namespace: Namespace, mount: MountContext) -> NamespaceEngine:
    """Create engine instances for a namespace using its mount state dir."""
    provenance = DagProvenance(mount.state_path("dag.db"))
    trigger_store = TriggerStore(mount.state_path("triggers.db"))
    trigger_manager = TriggerManager(
        namespace=namespace, store=trigger_store, provenance=provenance
    )
    return NamespaceEngine(
        prefix=prefix,
        namespace=namespace,
        provenance=provenance,
        trigger_store=trigger_store,
        trigger_manager=trigger_manager,
    )


def activate_triggers(engine: NamespaceEngine) -> list[str]:
    """Activate all triggers defined in namespace node configs. Returns activated names."""
    activated: list[str] = []
    for node in engine.namespace._nodes.values():  # pyright: ignore[reportPrivateUsage]
        if node.config and node.config.triggers:
            for tc in node.config.triggers:
                engine.trigger_manager.activate(tc.name)
                activated.append(tc.name)
    return activated
