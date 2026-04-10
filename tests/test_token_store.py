"""Tests for woodglue.token_store."""

from __future__ import annotations

import tempfile
from pathlib import Path

from woodglue.token_store import ensure_token, get_single_token, validate_token


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
