"""Tests for woodglue.engine."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from lythonic.compose.namespace import Namespace

from woodglue.engine import EngineRegistry, NamespaceEngine
from woodglue.mount import MountContext


def _make_engine(prefix: str) -> NamespaceEngine:
    return NamespaceEngine(
        prefix=prefix,
        namespace=Namespace(),
        trigger_store=MagicMock(),
        trigger_manager=MagicMock(),
        mount=MountContext(prefix, Path(".")),
    )


def test_engine_registry_register_and_get() -> None:
    reg = EngineRegistry()
    engine = _make_engine("pipeline")
    reg.register(engine)
    assert reg.get("pipeline") is engine


def test_engine_registry_list_prefixes() -> None:
    reg = EngineRegistry()
    reg.register(_make_engine("beta"))
    reg.register(_make_engine("alpha"))
    assert reg.list_prefixes() == ["alpha", "beta"]


def test_engine_registry_has_engines() -> None:
    reg = EngineRegistry()
    assert not reg.has_engines()
    reg.register(_make_engine("x"))
    assert reg.has_engines()


def test_engine_registry_get_missing_raises() -> None:
    reg = EngineRegistry()
    try:
        reg.get("nope")
        raise AssertionError("Expected KeyError")
    except KeyError:
        pass


def test_create_engine_wires_paths() -> None:
    from lythonic.compose.engine import StorageConfig as LythStorageConfig

    from woodglue.engine import create_engine

    with tempfile.TemporaryDirectory() as tmp:
        mount = MountContext("test_ns", Path(tmp))
        ns = Namespace()

        storage = LythStorageConfig()
        storage.resolve_paths(mount.state_dir)
        storage.log_file = None  # no file logging in tests (Windows cleanup)
        ns.mount(storage)

        engine = create_engine(mount, ns)
        assert engine.prefix == "test_ns"
        assert engine.namespace is ns
        # The state dir should have been created (mount + TriggerStore init)
        assert mount.state_dir.exists()


async def test_stop_all_waits_for_poll_tasks() -> None:
    from lythonic.compose.engine import StorageConfig as LythStorageConfig

    from woodglue.engine import create_engine

    with tempfile.TemporaryDirectory() as tmp:
        mount = MountContext("test_ns", Path(tmp))
        ns = Namespace()
        storage = LythStorageConfig()
        storage.resolve_paths(mount.state_dir)
        storage.log_file = None  # no file logging in tests (Windows cleanup)
        ns.mount(storage)

        registry = EngineRegistry()
        engine = create_engine(mount, ns)
        registry.register(engine)
        await registry.start_all()
        task = engine.trigger_manager._task  # pyright: ignore[reportPrivateUsage]
        assert task is not None and not task.done()

        await registry.stop_all()
        assert task.done()


def _make_trigger_engine(tmp: Path) -> NamespaceEngine:
    """Engine whose namespace has one node with a poll trigger named `tick`."""
    from lythonic.compose.engine import StorageConfig as LythStorageConfig
    from lythonic.compose.namespace import NsNodeConfig, TriggerConfig

    from woodglue.engine import create_engine

    def work() -> None:
        pass

    mount = MountContext("test_ns", tmp)
    ns = Namespace()
    ns.register(
        work,
        nsref="work",
        config=NsNodeConfig(
            nsref="work", triggers=[TriggerConfig(name="tick", schedule="0 0 * * *")]
        ),
    )
    storage = LythStorageConfig()
    storage.resolve_paths(mount.state_dir)
    storage.log_file = None  # no file logging in tests (Windows cleanup)
    ns.mount(storage)
    return create_engine(mount, ns)


def test_activate_triggers_keeps_paused_trigger_disabled_on_restart() -> None:
    from woodglue.engine import activate_triggers

    with tempfile.TemporaryDirectory() as tmp:
        engine = _make_trigger_engine(Path(tmp))
        with patch("time.time", return_value=1000.0):
            activate_triggers(engine)
        engine.trigger_manager.deactivate("tick")

        with patch("time.time", return_value=2000.0):
            result = activate_triggers(engine)

        assert result.activated == []
        assert result.left_disabled == ["tick"]
        activation = engine.trigger_store.get_activation("tick")
        assert activation is not None
        assert activation["status"] == "disabled"
        assert activation["last_run_at"] == 1000.0


def test_activate_triggers_activates_new_trigger() -> None:
    from woodglue.engine import activate_triggers

    with tempfile.TemporaryDirectory() as tmp:
        engine = _make_trigger_engine(Path(tmp))
        assert engine.trigger_store.get_activation("tick") is None

        result = activate_triggers(engine)

        assert result.activated == ["tick"]
        assert result.left_disabled == []
        activation = engine.trigger_store.get_activation("tick")
        assert activation is not None
        assert activation["status"] == "active"


def test_activate_triggers_keeps_active_trigger_last_run_at() -> None:
    from woodglue.engine import activate_triggers

    with tempfile.TemporaryDirectory() as tmp:
        engine = _make_trigger_engine(Path(tmp))
        with patch("time.time", return_value=1000.0):
            activate_triggers(engine)

        with patch("time.time", return_value=2000.0):
            result = activate_triggers(engine)

        assert result.activated == ["tick"]
        activation = engine.trigger_store.get_activation("tick")
        assert activation is not None
        assert activation["status"] == "active"
        assert activation["last_run_at"] == 1000.0


def test_activate_trigger_api_reactivates_trigger_left_disabled() -> None:
    from woodglue.apps.system_api import build_system_namespace
    from woodglue.engine import activate_triggers

    with tempfile.TemporaryDirectory() as tmp:
        engine = _make_trigger_engine(Path(tmp))
        registry = EngineRegistry()
        registry.register(engine)
        system_ns = build_system_namespace({}, registry)

        with patch("time.time", return_value=1000.0):
            activate_triggers(engine)
        system_ns.get("deactivate_trigger")(namespace="test_ns", name="tick")
        activate_triggers(engine)

        with patch("time.time", return_value=3000.0):
            activation = system_ns.get("activate_trigger")(namespace="test_ns", name="tick")

        assert activation["status"] == "active"
        assert activation["last_run_at"] == 3000.0
