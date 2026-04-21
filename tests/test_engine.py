"""Tests for woodglue.engine."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from lythonic.compose.namespace import Namespace

from woodglue.engine import EngineRegistry, NamespaceEngine


def _make_engine(prefix: str) -> NamespaceEngine:
    return NamespaceEngine(
        prefix=prefix,
        namespace=Namespace(),
        provenance=MagicMock(),
        trigger_store=MagicMock(),
        trigger_manager=MagicMock(),
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
    from woodglue.engine import create_engine
    from woodglue.mount import MountContext

    with tempfile.TemporaryDirectory() as tmp:
        mounts_dir = Path(tmp) / "mounts"
        mount = MountContext("test_ns", mounts_dir)
        ns = Namespace()

        engine = create_engine("test_ns", ns, mount)
        assert engine.prefix == "test_ns"
        assert engine.namespace is ns
        # The state dir should have been created (DagProvenance and TriggerStore init)
        assert mount.state_dir.exists()
