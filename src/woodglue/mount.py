"""
Per-namespace mount state directories and context tracking.

Every mounted namespace gets a `MountContext`, readable through the
`current_mount` context var while woodglue runs that namespace's code:

- while the namespace is built by `load_namespaces` (so a fragment's
  `__init__` can read it),
- during JSON-RPC calls to its methods,
- during its scheduled trigger runs, and `system.fire_trigger`,
  `system.activate_trigger` and `system.deactivate_trigger` calls targeting it,
- during `wgl run`.

A `MountContext` exposes two directories:

- `data_dir`: the instance data dir (`wgl --data`), resolved to an absolute
  path. Shared by all namespaces; use it only for state that belongs to the
  instance as a whole.
- `state_dir`: `{data_dir}/mounts/{prefix}/`, private to the namespace.
  Prefer this (via `state_path()`) for a fragment's own files.

Outside woodglue (tests, a fragment's own CLI) no mount is set, so read it with
a fallback:

```
mount = current_mount.get(None)
data_dir = mount.data_dir if mount else default_data_dir()
```
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


class MountContext:
    """
    Per-namespace mount state, accessible via `current_mount` context var.

    The state directory (`{data_dir}/mounts/{prefix}/`) is created lazily on
    first `state_path()` call.
    """

    prefix: str
    data_dir: Path
    state_dir: Path
    _dir_created: bool

    def __init__(self, prefix: str, data_dir: Path) -> None:
        self.prefix = prefix
        self.data_dir = data_dir.resolve()
        self.state_dir = self.data_dir / "mounts" / prefix
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

    @contextmanager
    def activate(self) -> Iterator[MountContext]:
        """
        Set `current_mount` to this mount for the duration of the block.
        Tasks created inside the block keep it, since they copy the context.
        """
        token = current_mount.set(self)
        try:
            yield self
        finally:
            current_mount.reset(token)


current_mount: ContextVar[MountContext] = ContextVar("current_mount")
