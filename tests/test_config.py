"""Tests for woodglue.config YAML loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

from woodglue.cli import load_namespaces
from woodglue.config import DocsConfig, UiConfig, load_config


def test_load_minimal_config():
    """Load a YAML with only namespaces (required field)."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text("namespaces:\n  hello: 'woodglue.hello:ns'\n")
        cfg = load_config(Path(tmp))
        assert cfg.namespaces == {"hello": "woodglue.hello:ns"}
        assert cfg.docs == DocsConfig()
        assert cfg.ui == UiConfig()


def test_load_full_config():
    """Load a YAML with all sections populated."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "woodglue.yaml"
        config_path.write_text(
            "namespaces:\n"
            "  hello: 'woodglue.hello:ns'\n"
            "  other: 'some.module:ns'\n"
            "docs:\n"
            "  enabled: false\n"
            "  openapi: false\n"
            "ui:\n"
            "  enabled: false\n"
        )
        cfg = load_config(Path(tmp))
        assert cfg.namespaces == {
            "hello": "woodglue.hello:ns",
            "other": "some.module:ns",
        }
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


def test_load_namespaces_from_yaml():
    """Load a namespace from a YAML config file."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        ns_config_path = data_dir / "test_ns.yaml"
        ns_config_path.write_text(
            "entries:\n"
            "  - nsref: hello\n"
            '    gref: "woodglue.hello:hello"\n'
            "    tags: ['api']\n"
            "  - nsref: pydantic_hello\n"
            '    gref: "woodglue.hello:pydantic_hello"\n'
            "    tags: ['api']\n"
        )
        namespaces = load_namespaces({"greet": "test_ns.yaml"}, data_dir)
        assert "greet" in namespaces
        ns = namespaces["greet"]
        node = ns.get("hello")
        assert node is not None
        assert "api" in node.tags


def test_load_namespaces_from_globalref():
    """Load a namespace from a GlobalRef string (existing behavior)."""
    namespaces = load_namespaces({"hello": "woodglue.hello:ns"}, Path("."))
    assert "hello" in namespaces
