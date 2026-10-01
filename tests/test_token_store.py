"""Tests for woodglue.token_store."""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from woodglue.token_store import (
    ensure_token,
    get_single_token,
    list_tokens,
    rotate_token,
    validate_token,
)


def test_ensure_token_creates_on_empty_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "auth.db"
        token = ensure_token(db_path)
        assert token is not None
        assert len(token) > 20


def test_ensure_token_returns_none_if_exists():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "auth.db"
        first = ensure_token(db_path)
        assert first is not None
        second = ensure_token(db_path)
        assert second is None


def test_get_single_token():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "auth.db"
        created = ensure_token(db_path)
        retrieved = get_single_token(db_path)
        assert retrieved == created


def test_get_single_token_returns_none_if_multiple():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "auth.db"
        ensure_token(db_path)
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "INSERT INTO tokens (token, created_at) VALUES (?, datetime('now'))",
                ("second-token",),
            )
            conn.commit()
        assert get_single_token(db_path) is None


def test_validate_token():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "auth.db"
        token = ensure_token(db_path)
        assert token is not None
        assert validate_token(db_path, token) is True
        assert validate_token(db_path, "bad-token") is False


def _add_later_token(db_path: Path, token: str) -> None:
    """Insert `token` with a `created_at` after any token created now."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO tokens (token, created_at) VALUES (?, ?)",
            (token, "9999-01-01T00:00:00+00:00"),
        )
        conn.commit()


def test_list_tokens_returns_all_in_creation_order(tmp_path: Path):
    db_path = tmp_path / "auth.db"
    assert list_tokens(db_path) == []
    first = ensure_token(db_path)
    _add_later_token(db_path, "second-token")
    assert list_tokens(db_path) == [first, "second-token"]


def test_rotate_token_replaces_all_tokens(tmp_path: Path):
    db_path = tmp_path / "auth.db"
    old = ensure_token(db_path)
    assert old is not None
    _add_later_token(db_path, "second-token")
    new = rotate_token(db_path)
    assert list_tokens(db_path) == [new]
    assert validate_token(db_path, new) is True
    assert validate_token(db_path, old) is False
    assert validate_token(db_path, "second-token") is False


def test_rotate_token_on_missing_db_creates_one(tmp_path: Path):
    db_path = tmp_path / "auth.db"
    new = rotate_token(db_path)
    assert list_tokens(db_path) == [new]
