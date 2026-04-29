"""Tests for woodglue.config YAML loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from woodglue.cli import load_namespaces
from woodglue.config import DocsConfig, NamespaceEntry, UiConfig, load_config


def test_load_minimal_config():
    """Load a YAML with only namespaces (required field)."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text("namespaces:\n  hello:\n    gref: 'woodglue.hello:ns'\n")
        cfg = load_config(Path(tmp))
        assert cfg.namespaces["hello"].gref == "woodglue.hello:ns"
        assert cfg.docs == DocsConfig()
        assert cfg.ui == UiConfig()


def test_load_full_config():
    """Load a YAML with all sections populated."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "namespaces:\n"
            "  hello:\n"
            "    gref: 'woodglue.hello:ns'\n"
            "  other:\n"
            "    gref: 'some.module:ns'\n"
            "docs:\n"
            "  enabled: false\n"
            "  openapi: false\n"
            "ui:\n"
            "  enabled: false\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.namespaces["hello"].gref == "woodglue.hello:ns"
        assert cfg.namespaces["other"].gref == "some.module:ns"
        assert cfg.docs.enabled is False
        assert cfg.docs.openapi is False
        assert cfg.ui.enabled is False


def test_load_config_missing_file():
    """Raise FileNotFoundError when woodglue.yaml is missing."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            load_config(Path(tmp))
            raise AssertionError("Expected FileNotFoundError")
        except FileNotFoundError:
            pass


def test_load_config_with_inline_namespace():
    """Load a config with inline entries list."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "namespaces:\n"
            "  api:\n"
            "    entries:\n"
            "      - nsref: hello\n"
            '        gref: "woodglue.hello:hello"\n'
            "        tags: ['api']\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.namespaces["api"].entries is not None
        assert len(cfg.namespaces["api"].entries) == 1


def test_load_config_with_storage():
    """Load a config with storage settings."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "storage:\n"
            "  cache_db: cache.db\n"
            "  auth_db: auth.db\n"
            "namespaces:\n"
            "  hello:\n"
            "    gref: 'woodglue.hello:ns'\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.storage.cache_db == Path("cache.db")
        assert cfg.storage.auth_db == Path("auth.db")


def test_load_namespaces_from_yaml():
    """Load a namespace from a YAML config file."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        ns_config_path = data_dir / "test_ns.yaml"
        ns_config_path.write_text(
            "namespace:\n"
            "  - nsref: hello\n"
            '    gref: "woodglue.hello:hello"\n'
            "    tags: ['api']\n"
            "  - nsref: pydantic_hello\n"
            '    gref: "woodglue.hello:pydantic_hello"\n'
            "    tags: ['api']\n"
        )
        entry = NamespaceEntry(file="test_ns.yaml")
        namespaces = load_namespaces({"greet": entry}, data_dir)
        assert "greet" in namespaces
        ns, loaded_entry = namespaces["greet"]
        assert loaded_entry.expose_api is True
        assert loaded_entry.run_engine is False
        node = ns.get("hello")
        assert node is not None
        assert "api" in node.tags


def test_load_namespaces_from_gref():
    """Load a namespace from a GlobalRef string."""
    entry = NamespaceEntry(gref="woodglue.hello:ns")
    namespaces = load_namespaces({"hello": entry}, Path("."))
    assert "hello" in namespaces
    _ns, loaded_entry = namespaces["hello"]
    assert loaded_entry.gref == "woodglue.hello:ns"
    assert loaded_entry.expose_api is True


def test_load_namespaces_inline():
    """Load a namespace from inline entries list."""
    from lythonic import GlobalRef
    from lythonic.compose.namespace import NsNodeConfig

    entries = [
        NsNodeConfig(nsref="hello", gref=GlobalRef("woodglue.hello:hello"), tags=["api"]),
        NsNodeConfig(
            nsref="pydantic_hello",
            gref=GlobalRef("woodglue.hello:pydantic_hello"),
            tags=["api"],
        ),
    ]
    ns_entry = NamespaceEntry(entries=entries)
    namespaces = load_namespaces({"inline": ns_entry}, Path("."))
    assert "inline" in namespaces
    ns, loaded_entry = namespaces["inline"]
    assert loaded_entry.entries is not None
    node = ns.get("hello")
    assert node is not None
    assert "api" in node.tags


def test_namespace_entry_gref():
    """NamespaceEntry with gref."""
    entry = NamespaceEntry(gref="woodglue.hello:ns")
    assert entry.gref == "woodglue.hello:ns"
    assert entry.file is None
    assert entry.entries is None
    assert entry.expose_api is True
    assert entry.run_engine is False


def test_namespace_entry_file():
    """NamespaceEntry with file."""
    entry = NamespaceEntry(file="hello_ns.yaml")
    assert entry.file == "hello_ns.yaml"
    assert entry.gref is None
    assert entry.entries is None


def test_namespace_entry_with_flags():
    """NamespaceEntry with explicit flags."""
    entry = NamespaceEntry(file="internal_ns.yaml", expose_api=False, run_engine=True)
    assert entry.file == "internal_ns.yaml"
    assert entry.expose_api is False
    assert entry.run_engine is True


def test_namespace_entry_validation_none_set():
    """NamespaceEntry rejects when no source field is set."""
    with pytest.raises(ValueError, match="Exactly one"):
        NamespaceEntry()


def test_namespace_entry_validation_multiple_set():
    """NamespaceEntry rejects when multiple source fields are set."""
    with pytest.raises(ValueError, match="Exactly one"):
        NamespaceEntry(gref="foo:bar", file="foo.yaml")


def test_load_namespaces_with_flags():
    """Load a namespace with explicit flags."""
    entry = NamespaceEntry(gref="woodglue.hello:ns", expose_api=False, run_engine=True)
    namespaces = load_namespaces({"hello": entry}, Path("."))
    _ns, loaded_entry = namespaces["hello"]
    assert loaded_entry.expose_api is False
    assert loaded_entry.run_engine is True


def test_load_config_with_auth():
    """Load a config with auth settings."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "namespaces:\n  hello:\n    gref: 'woodglue.hello:ns'\nauth:\n  enabled: false\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.auth.enabled is False


def test_auth_enabled_by_default():
    """Auth is enabled by default."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text("namespaces:\n  hello:\n    gref: 'woodglue.hello:ns'\n")
        cfg = load_config(Path(tmp))
        assert cfg.auth.enabled is True
