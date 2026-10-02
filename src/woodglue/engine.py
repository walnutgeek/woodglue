"""
Per-namespace engine instances and registry.

`activate_triggers` runs on every `wgl start`. The node configs decide which triggers
exist; the stored activation status decides whether each one is on. A trigger paused
through the `deactivate_trigger` system API stays paused across restarts until it is
explicitly reactivated with `activate_trigger`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from lythonic.compose.namespace import Namespace
from lythonic.compose.trigger import TriggerManager, TriggerStore

from woodglue.mount import MountContext


@dataclass
class NamespaceEngine:
    """Engine instances for a single namespace."""

    prefix: str
    namespace: Namespace
    trigger_store: TriggerStore
    trigger_manager: TriggerManager
    mount: MountContext


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
        """
        Start all TriggerManagers. Each poll loop runs with its namespace's
        mount as `current_mount`.
        """
        for engine in self._engines.values():
            with engine.mount.activate():
                engine.trigger_manager.start()

    async def stop_all(self) -> None:
        """
        Stop all TriggerManagers and wait for their poll tasks to finish.

        `TriggerManager.stop()` only requests cancellation. Awaiting the tasks lets
        them unwind before a caller stops the event loop, which would otherwise
        leave them pending.
        """
        tasks: list[asyncio.Task[None]] = []
        for engine in self._engines.values():
            task = engine.trigger_manager._task  # pyright: ignore[reportPrivateUsage]
            engine.trigger_manager.stop()
            if task is not None:
                tasks.append(task)
        await asyncio.gather(*tasks, return_exceptions=True)


def create_engine(mount: MountContext, namespace: Namespace) -> NamespaceEngine:
    """Create engine instances for a namespace. Namespace must be mounted first."""
    storage = namespace._storage  # pyright: ignore[reportPrivateUsage]  # set by mount()
    assert storage.triggers_db is not None, "namespace must be mounted with triggers_db"
    trigger_store = TriggerStore(storage.triggers_db)
    provenance = namespace._provenance  # pyright: ignore[reportPrivateUsage]
    trigger_manager = TriggerManager(
        namespace=namespace, store=trigger_store, provenance=provenance
    )
    return NamespaceEngine(
        prefix=mount.prefix,
        namespace=namespace,
        trigger_store=trigger_store,
        trigger_manager=trigger_manager,
        mount=mount,
    )


@dataclass(frozen=True)
class TriggerActivationResult:
    """Outcome of `activate_triggers`: names activated and names left paused."""

    activated: list[str]
    left_disabled: list[str]


def activate_triggers(engine: NamespaceEngine) -> TriggerActivationResult:
    """
    Activate the triggers defined in namespace node configs, as done on every start.

    The config decides which triggers exist; the stored activation status decides
    whether each is on. New triggers and already active ones are activated (an active
    one keeps its `last_run_at`). A trigger stored as `disabled` is left untouched, so
    a pause made through the API lasts until the trigger is explicitly reactivated.
    """
    activated: list[str] = []
    left_disabled: list[str] = []
    for node in engine.namespace._nodes.values():  # pyright: ignore[reportPrivateUsage]
        if node.config and node.config.triggers:
            for tc in node.config.triggers:
                assert tc.name is not None, "trigger name must be set after registration"
                activation = engine.trigger_store.get_activation(tc.name)
                if activation is not None and activation["status"] == "disabled":
                    left_disabled.append(tc.name)
                    continue
                engine.trigger_manager.activate(tc.name)
                activated.append(tc.name)
    return TriggerActivationResult(activated=activated, left_disabled=left_disabled)
