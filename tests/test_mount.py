"""Tests for woodglue.mount."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock

from lythonic.compose.namespace import NamespaceFragment, nsnode

from woodglue.config import NamespaceEntry
from woodglue.mount import MountContext, current_mount


def test_state_path_creates_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mounts_dir = Path(tmp) / "mounts"
        ctx = MountContext("pipeline", Path(tmp))
        path = ctx.state_path("dags.db")
        assert path.parent.is_dir()
        assert path == (mounts_dir / "pipeline" / "dags.db").resolve()


def test_state_path_returns_correct_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mounts_dir = Path(tmp) / "mounts"
        ctx = MountContext("etl", Path(tmp))
        assert ctx.state_path("triggers.db") == (mounts_dir / "etl" / "triggers.db").resolve()
        assert ctx.state_path("cache.db") == (mounts_dir / "etl" / "cache.db").resolve()


def test_dir_not_created_until_state_path_called() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = MountContext("lazy", Path(tmp))
        assert not ctx.state_dir.exists()
        ctx.state_path("something.db")
        assert ctx.state_dir.exists()


class MountProbe(NamespaceFragment):
    """Records the `current_mount` it sees when built and when called."""

    seen: ClassVar[list[tuple[str, str | None]]] = []

    def __init__(self) -> None:
        MountProbe.seen.append(("init", _seen_prefix()))

    @nsnode(tags=["api"])
    def probe(self) -> str:
        MountProbe.seen.append(("call", _seen_prefix()))
        return "ok"


def _seen_prefix() -> str | None:
    mount = current_mount.get(None)
    return mount.prefix if mount else None


_PROBE_ENTRY: dict[str, Any] = {
    "type": "fragment",
    "gref": f"{__name__}:MountProbe",
    "nsref": "probe:",
    "configs": {"probe": {"triggers": [{"name": "probe_now", "schedule": "0 0 1 1 *"}]}},
}


def test_data_dir_is_absolute_and_contains_state_dir() -> None:
    ctx = MountContext("rel", Path("data"))
    assert ctx.data_dir.is_absolute()
    assert ctx.state_dir == ctx.data_dir / "mounts" / "rel"


def test_activate_sets_and_restores_current_mount() -> None:
    outer = MountContext("outer", Path("."))
    inner = MountContext("inner", Path("."))
    assert current_mount.get(None) is None
    with outer.activate():
        with inner.activate():
            assert current_mount.get() is inner
        assert current_mount.get() is outer
    assert current_mount.get(None) is None


def test_fragment_sees_mount_when_built() -> None:
    from woodglue.cli import load_namespaces

    MountProbe.seen.clear()
    with tempfile.TemporaryDirectory() as tmp:
        load_namespaces({"cointoss": NamespaceEntry(entries=[_PROBE_ENTRY])}, Path(tmp))
    assert MountProbe.seen == [("init", "cointoss")]


async def test_fire_trigger_runs_with_target_mount() -> None:
    from lythonic.compose.engine import StorageConfig as LythStorageConfig

    from woodglue.apps.system_api import build_system_namespace
    from woodglue.cli import load_namespaces
    from woodglue.engine import EngineRegistry, create_engine

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        namespaces = load_namespaces(
            {"cointoss": NamespaceEntry(entries=[_PROBE_ENTRY], run_engine=True)}, data_dir
        )
        ns, _entry = namespaces["cointoss"]
        mount = MountContext("cointoss", data_dir)
        storage = LythStorageConfig()
        storage.resolve_paths(mount.state_dir)
        storage.log_file = None  # no file logging in tests (Windows cleanup)
        ns.mount(storage)
        registry = EngineRegistry()
        registry.register(create_engine(mount, ns))
        system_ns = build_system_namespace(namespaces, registry)

        MountProbe.seen.clear()
        # The RPC handler runs system calls under the system mount.
        with MountContext("system", data_dir).activate():
            system_ns.get("activate_trigger")(namespace="cointoss", name="probe_now")
            await system_ns.get("fire_trigger")(namespace="cointoss", name="probe_now")
            assert current_mount.get().prefix == "system"
    assert MountProbe.seen == [("call", "cointoss")]


async def test_start_all_starts_poll_loops_under_mount() -> None:
    from lythonic.compose.namespace import Namespace

    from woodglue.engine import EngineRegistry, NamespaceEngine

    seen: list[str | None] = []
    manager = MagicMock()
    manager.start.side_effect = lambda: seen.append(_seen_prefix())
    registry = EngineRegistry()
    registry.register(
        NamespaceEngine(
            prefix="cointoss",
            namespace=Namespace(),
            trigger_store=MagicMock(),
            trigger_manager=manager,
            mount=MountContext("cointoss", Path(".")),
        )
    )
    await registry.start_all()
    assert seen == ["cointoss"]
    assert current_mount.get(None) is None
