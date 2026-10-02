"""
YAML-backed server configuration.

The config file lives at `{data_dir}/woodglue.yaml` and is required to run
the server. It declares storage, namespaces, documentation, UI, and engine
settings.

Logging for `wgl start` is driven by `storage.log_file`, `storage.log_level`,
`storage.loggers`, `storage.log_max_bytes`, `storage.log_backup_count`, and
the optional top-level `logging:` section; see `woodglue.log_setup`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lythonic.compose.engine import StorageConfig
from pydantic import BaseModel, PrivateAttr, model_validator
from pydantic_yaml import parse_yaml_file_as
from typing_extensions import override

CONFIG_FILENAME = "woodglue.yaml"


class WoodglueStorageConfig(StorageConfig):
    """
    Extends lythonic StorageConfig with woodglue-specific storage and log rotation.

    `log_level` defaults to INFO here rather than lythonic's DEBUG.
    """

    auth_db: Path | None = None
    log_level: str = "INFO"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    _log_file_explicit: bool = PrivateAttr(default=False)

    @override
    def model_post_init(self, context: Any, /) -> None:
        super().model_post_init(context)
        # Snapshot now: path resolution later assigns `log_file`, which marks it as set.
        self._log_file_explicit = "log_file" in self.model_fields_set

    @property
    def log_file_explicit(self) -> bool:
        """
        Whether `log_file` was given in the config, including an explicit `null`
        (which disables the file log).
        """
        return self._log_file_explicit


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

    However it is defined, the namespace is built and called with its
    `woodglue.mount.MountContext` set as `current_mount`. A fragment needing the
    instance data dir reads `current_mount.get(None).data_dir` rather than
    taking an absolute path in its `init` config, so a copied `woodglue.yaml`
    still points at its own instance.
    """

    gref: str | None = None
    file: str | None = None
    entries: list[dict[str, Any]] | None = None
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

    `logging`, when present, is a `logging.config.dictConfig` dict that replaces
    woodglue's default logging setup (see `woodglue.log_setup`).
    """

    host: str = "127.0.0.1"
    port: int = 5321
    storage: WoodglueStorageConfig = WoodglueStorageConfig()
    namespaces: dict[str, NamespaceEntry]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()
    auth: AuthConfig = AuthConfig()
    logging: dict[str, Any] | None = None


def load_config(data_dir: Path) -> WoodglueConfig:
    """
    Load `WoodglueConfig` from `{data_dir}/woodglue.yaml`.

    Raises `FileNotFoundError` if the file does not exist.
    """
    config_path = data_dir / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return parse_yaml_file_as(WoodglueConfig, config_path)
