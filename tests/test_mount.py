"""Tests for woodglue.mount."""

from __future__ import annotations

import tempfile
from pathlib import Path

from woodglue.mount import MountContext


def test_state_path_creates_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mounts_dir = Path(tmp) / "mounts"
        ctx = MountContext("pipeline", mounts_dir)
        path = ctx.state_path("dags.db")
        assert path.parent.is_dir()
        assert path == (mounts_dir / "pipeline" / "dags.db").resolve()


def test_state_path_returns_correct_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mounts_dir = Path(tmp) / "mounts"
        ctx = MountContext("etl", mounts_dir)
        assert ctx.state_path("triggers.db") == (mounts_dir / "etl" / "triggers.db").resolve()
        assert ctx.state_path("cache.db") == (mounts_dir / "etl" / "cache.db").resolve()


def test_dir_not_created_until_state_path_called() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        mounts_dir = Path(tmp) / "mounts"
        ctx = MountContext("lazy", mounts_dir)
        assert not ctx.state_dir.exists()
        ctx.state_path("something.db")
        assert ctx.state_dir.exists()
