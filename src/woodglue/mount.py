"""Per-namespace mount state directories and context tracking."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path


class MountContext:
    """
    Per-namespace mount state, accessible via `current_mount` context var.

    Every mounted namespace gets a MountContext. The state directory
    (`data/mounts/{prefix}/`) is created lazily on first `state_path()` call.
    """

    prefix: str
    state_dir: Path
    _dir_created: bool

    def __init__(self, prefix: str, mounts_dir: Path) -> None:
        self.prefix = prefix
        self.state_dir = mounts_dir / prefix
        self._dir_created = False

    def state_path(self, filename: str) -> Path:
        """
        Resolve a file path within this mount's state dir.
        Creates the state dir lazily on first call.
        """
        if not self._dir_created:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._dir_created = True
        return self.state_dir / filename


current_mount: ContextVar[MountContext] = ContextVar("current_mount")
