"""
Token storage for bearer authentication.

Manages random bearer tokens in a SQLite database. Tokens are generated
via `secrets.token_urlsafe(32)` and stored in a `tokens` table.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tokens (  token TEXT PRIMARY KEY,  created_at TEXT NOT NULL)"
    )


def ensure_token(db_path: Path) -> str | None:
    """
    If no tokens exist, generate one and return it.
    If tokens already exist, return `None`.
    """
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_table(conn)
        count = conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        if count > 0:
            return None
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC).isoformat()
        conn.execute("INSERT INTO tokens (token, created_at) VALUES (?, ?)", (token, now))
        conn.commit()
        return token


def get_single_token(db_path: Path) -> str | None:
    """Return the token if exactly one exists, otherwise `None`."""
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_table(conn)
        rows = conn.execute("SELECT token FROM tokens").fetchall()
        if len(rows) == 1:
            return rows[0][0]
        return None


def validate_token(db_path: Path, token: str) -> bool:
    """Return `True` if the token exists in the database."""
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_table(conn)
        row = conn.execute("SELECT 1 FROM tokens WHERE token = ?", (token,)).fetchone()
        return row is not None
