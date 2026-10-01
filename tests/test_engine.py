"""Tests for woodglue.engine."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

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
