"""Tests for woodglue.apps.engine_api."""

from __future__ import annotations

import tempfile
from pathlib import Path

from lythonic.compose.namespace import Namespace

from woodglue.apps.engine_api import build_engine_namespace
from woodglue.engine import EngineRegistry, create_engine
from woodglue.mount import MountContext


def _make_registry_with_ns(tmp_path: Path) -> EngineRegistry:
    mounts_dir = tmp_path / "mounts"
    ns = Namespace()
    mount = MountContext("demo", mounts_dir)
    engine = create_engine("demo", ns, mount)
    reg = EngineRegistry()
    reg.register(engine)
    return reg


def test_build_engine_namespace_has_methods() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = _make_registry_with_ns(Path(tmp))
        facade = build_engine_namespace(reg)

        expected = {
            "list_namespaces",
            "recent_runs",
            "active_runs",
            "inspect_run",
            "load_io",
            "child_runs",
            "list_triggers",
            "fire_trigger",
            "activate_trigger",
            "deactivate_trigger",
        }
        actual = set(facade._nodes.keys())  # pyright: ignore[reportPrivateUsage]
        assert expected == actual


def test_list_namespaces() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = _make_registry_with_ns(Path(tmp))
        facade = build_engine_namespace(reg)
        node = facade.get("list_namespaces")
        result = node()
        assert result == ["demo"]
