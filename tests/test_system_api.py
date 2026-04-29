"""Tests for woodglue.apps.system_api."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from lythonic.compose import Method
from lythonic.compose.namespace import Namespace, NamespaceNode

from woodglue.apps.system_api import (
    ArgInfo,
    MethodInfo,
    NamespaceInfo,
    TriggerInfo,
    build_system_namespace,
)
from woodglue.config import NamespaceEntry
from woodglue.engine import EngineRegistry, create_engine
from woodglue.mount import MountContext


def _make_registry_with_ns(tmp_path: Path) -> EngineRegistry:
    from lythonic.compose.engine import StorageConfig as LythStorageConfig

    mounts_dir = tmp_path / "mounts"
    ns = Namespace()
    mount = MountContext("demo", mounts_dir)
    storage = LythStorageConfig()
    storage.resolve_paths(mount.state_dir)
    ns.mount(storage)
    engine = create_engine("demo", ns)
    reg = EngineRegistry()
    reg.register(engine)
    return reg


def _make_api_namespace() -> Namespace:
    """Build a namespace with a couple of tagged api methods."""
    ns = Namespace()

    def greet(name: str, greeting: str = "Hello") -> str:
        """Say hello to someone."""
        return f"{greeting}, {name}!"

    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    method_g = Method(greet)
    node_g = NamespaceNode(method=method_g, nsref="greet", namespace=ns, tags=["api"])
    ns._nodes["greet"] = node_g  # pyright: ignore[reportPrivateUsage]

    method_a = Method(add)
    node_a = NamespaceNode(method=method_a, nsref="add", namespace=ns, tags=["api"])
    ns._nodes["add"] = node_a  # pyright: ignore[reportPrivateUsage]

    return ns


# -- Model tests --


def test_arg_info_model() -> None:
    a = ArgInfo(name="x", type="str", required=True)
    assert a.name == "x"
    assert a.default is None


def test_trigger_info_model() -> None:
    t = TriggerInfo(name="daily", schedule="0 0 * * *")
    assert t.name == "daily"
    assert t.schedule == "0 0 * * *"


def test_method_info_model() -> None:
    m = MethodInfo(nsref="greet", tags=["api"], has_cache=False, has_triggers=False)
    assert m.nsref == "greet"
    assert m.doc is None
    assert m.args is None


def test_namespace_info_model() -> None:
    n = NamespaceInfo(
        prefix="demo", expose_api=True, run_engine=False, method_count=3, has_cache=False
    )
    assert n.prefix == "demo"
    assert n.method_count == 3


# -- build_system_namespace tests --


def test_build_system_namespace_has_all_methods() -> None:
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {}
    system_ns = build_system_namespace(namespaces, None)

    expected = {
        "list_namespaces",
        "list_methods",
        "describe_method",
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
    actual = set(system_ns._nodes.keys())  # pyright: ignore[reportPrivateUsage]
    assert expected == actual


# -- Introspection tests --


def test_list_namespaces_returns_info() -> None:
    api_ns = _make_api_namespace()
    entry = NamespaceEntry(gref="test:api", expose_api=True)
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {
        "myns": (api_ns, entry),
    }
    system_ns = build_system_namespace(namespaces, None)

    # Add system itself to the dict (like cli.py does)
    system_entry = NamespaceEntry(gref="builtin:system", expose_api=True)
    namespaces["system"] = (system_ns, system_entry)

    node = system_ns.get("list_namespaces")
    result = node()

    assert isinstance(result, list)
    prefixes = {r.prefix for r in result}
    assert "myns" in prefixes
    assert "system" in prefixes

    myns_info = next(r for r in result if r.prefix == "myns")
    assert myns_info.expose_api is True
    assert myns_info.run_engine is False
    assert myns_info.method_count == 2


def test_list_methods_returns_method_info() -> None:
    api_ns = _make_api_namespace()
    entry = NamespaceEntry(gref="test:api", expose_api=True)
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {
        "myns": (api_ns, entry),
    }
    system_ns = build_system_namespace(namespaces, None)

    node = system_ns.get("list_methods")
    result = node(namespace="myns")

    assert isinstance(result, list)
    assert len(result) == 2
    nsrefs = {m.nsref for m in result}
    assert "greet" in nsrefs
    assert "add" in nsrefs

    greet_info = next(m for m in result if m.nsref == "greet")
    assert greet_info.doc_teaser == "Say hello to someone."
    assert greet_info.has_cache is False
    assert greet_info.has_triggers is False


def test_list_methods_unknown_namespace() -> None:
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {}
    system_ns = build_system_namespace(namespaces, None)
    node = system_ns.get("list_methods")

    with pytest.raises(ValueError, match="not found"):
        node(namespace="nonexistent")


def test_describe_method_returns_detail() -> None:
    api_ns = _make_api_namespace()
    entry = NamespaceEntry(gref="test:api", expose_api=True)
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {
        "myns": (api_ns, entry),
    }
    system_ns = build_system_namespace(namespaces, None)

    node = system_ns.get("describe_method")
    result = node(namespace="myns", nsref="greet")

    assert isinstance(result, MethodInfo)
    assert result.nsref == "greet"
    assert result.doc == "Say hello to someone."
    assert result.return_type == "str"
    assert result.args is not None
    assert len(result.args) == 2
    name_arg = result.args[0]
    assert name_arg.name == "name"
    assert name_arg.type == "str"
    assert name_arg.required is True
    greeting_arg = result.args[1]
    assert greeting_arg.name == "greeting"
    assert greeting_arg.required is False
    assert greeting_arg.default == "Hello"


def test_describe_method_unknown() -> None:
    api_ns = _make_api_namespace()
    entry = NamespaceEntry(gref="test:api", expose_api=True)
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {
        "myns": (api_ns, entry),
    }
    system_ns = build_system_namespace(namespaces, None)
    node = system_ns.get("describe_method")

    with pytest.raises(ValueError, match="not found"):
        node(namespace="myns", nsref="nonexistent")


# -- Engine method error tests --


def test_engine_methods_raise_without_registry() -> None:
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {}
    system_ns = build_system_namespace(namespaces, None)

    # Methods that only need namespace
    for method_name in ["recent_runs", "active_runs", "list_triggers"]:
        node = system_ns.get(method_name)
        with pytest.raises(ValueError, match="No engines configured"):
            node(namespace="anything")

    # inspect_run needs run_id too
    node = system_ns.get("inspect_run")
    with pytest.raises(ValueError, match="No engines configured"):
        node(namespace="anything", run_id="fake")


# -- Engine method tests with registry --


def test_engine_list_namespaces_in_system() -> None:
    """The system namespace includes engine-enabled namespace info."""
    with tempfile.TemporaryDirectory() as tmp:
        reg = _make_registry_with_ns(Path(tmp))
        demo_ns = Namespace()
        demo_entry = NamespaceEntry(gref="test:demo", run_engine=True)
        namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {
            "demo": (demo_ns, demo_entry),
        }
        system_ns = build_system_namespace(namespaces, reg)

        node = system_ns.get("list_namespaces")
        result = node()
        demo_info = next(r for r in result if r.prefix == "demo")
        assert demo_info.run_engine is True


def test_recent_runs_with_registry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = _make_registry_with_ns(Path(tmp))
        namespaces: dict[str, tuple[Namespace, NamespaceEntry]] = {}
        system_ns = build_system_namespace(namespaces, reg)

        node = system_ns.get("recent_runs")
        result = node(namespace="demo")
        assert isinstance(result, list)
