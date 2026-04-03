"""Tests for woodglue.config YAML loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

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
