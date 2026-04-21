"""
YAML-backed server configuration.

The config file lives at `{data_dir}/woodglue.yaml` and is required to run
the server. It declares storage, namespaces, documentation, UI, and engine
settings.
"""

from __future__ import annotations

from pathlib import Path

from lythonic.compose.engine import StorageConfig
from lythonic.compose.namespace import NsNodeConfig
from pydantic import BaseModel, model_validator
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


class NamespaceEntry(BaseModel):
    """
    Per-namespace configuration. Exactly one of `gref`, `file`, or `entries`
    must be set to specify how the namespace is instantiated.
    """

    gref: str | None = None
    file: str | None = None
    entries: list[NsNodeConfig] | None = None
    expose_api: bool = True
    run_engine: bool = False

    @model_validator(mode="after")
    def _exactly_one_source(self) -> NamespaceEntry:
        sources = [self.gref is not None, self.file is not None, self.entries is not None]
        if sum(sources) != 1:
            raise ValueError("Exactly one of gref, file, or entries must be set")
        return self


class AuthConfig(BaseModel):
    """Bearer token authentication settings."""

    enabled: bool = True


class WoodglueConfig(BaseModel):
    """
    Root configuration loaded from `woodglue.yaml`.

    `namespaces` maps a prefix string to a `NamespaceEntry` dict with exactly
    one of `gref`, `file`, or `entries`, plus optional `expose_api` and
    `run_engine` flags.
    """

    host: str = "127.0.0.1"
    port: int = 5321
    storage: WoodglueStorageConfig = WoodglueStorageConfig()
    namespaces: dict[str, NamespaceEntry]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()
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
