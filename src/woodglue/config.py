"""
YAML-backed server configuration.

The config file lives at `{data_dir}/woodglue.yaml` and is required to run
the server. It declares storage, namespaces, documentation, UI, and engine
settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lythonic.compose.engine import StorageConfig
from pydantic import BaseModel
from pydantic_yaml import parse_yaml_file_as

CONFIG_FILENAME = "woodglue.yaml"


class WoodglueStorageConfig(StorageConfig):
    """Extends lythonic StorageConfig with woodglue-specific storage."""

    auth_db: Path | None = None


class DocsConfig(BaseModel):
    """Documentation generation settings."""

    enabled: bool = True
    openapi: bool = True


class UiConfig(BaseModel):
    """JavaScript documentation UI settings."""

    enabled: bool = True


class EngineConfig(BaseModel):
    """Lyth compose engine settings."""

    enabled: bool = False


class AuthConfig(BaseModel):
    """Bearer token authentication settings."""

    enabled: bool = True


class WoodglueConfig(BaseModel):
    """
    Root configuration loaded from `woodglue.yaml`.

    `namespaces` maps a prefix string to one of:
    - A GlobalRef string (e.g., `"woodglue.hello:ns"`)
    - A YAML file path ending in `.yaml`/`.yml`
    - An inline list of `NsNodeConfig` entries
    """

    storage: WoodglueStorageConfig = WoodglueStorageConfig()
    namespaces: dict[str, Any]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()
    engine: EngineConfig = EngineConfig()
    auth: AuthConfig = AuthConfig()


def load_config(data_dir: Path) -> WoodglueConfig:
    """
    Load `WoodglueConfig` from `{data_dir}/woodglue.yaml`.

    Raises `FileNotFoundError` if the file does not exist.
    """
    config_path = data_dir / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return parse_yaml_file_as(WoodglueConfig, config_path)
