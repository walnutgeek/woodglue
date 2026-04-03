"""
YAML-backed server configuration.

The config file lives at `{data_dir}/woodglue.yaml` and is required to run
the server. It declares namespaces, documentation, and UI settings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pydantic_yaml import parse_yaml_file_as

CONFIG_FILENAME = "woodglue.yaml"


class DocsConfig(BaseModel):
    """Documentation generation settings."""

    enabled: bool = True
    openapi: bool = True


class UiConfig(BaseModel):
    """JavaScript documentation UI settings."""

    enabled: bool = True


class WoodglueConfig(BaseModel):
    """
    Root configuration loaded from `woodglue.yaml`.

    `namespaces` maps a prefix string to a Python module path in
    `"module.path:attribute"` format (a lythonic `GlobalRef`).
    """

    namespaces: dict[str, str]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()


def load_config(data_dir: Path) -> WoodglueConfig:
    """
    Load `WoodglueConfig` from `{data_dir}/woodglue.yaml`.

    Raises `FileNotFoundError` if the file does not exist.
    """
    config_path = data_dir / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return parse_yaml_file_as(WoodglueConfig, config_path)
