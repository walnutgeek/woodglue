"""
Users, Role assignments, and credentials in `auth.db`.

`AuthStore` is the only reader and writer of `auth.db`. Credentials are opaque
random secrets looked up on every request (see `docs/adr/0002`), so revoking a
token, disabling a User, or changing a Role assignment takes effect immediately,
including on a running server.

Role definitions are not stored here: they come from `woodglue.yaml` (see
`woodglue.access`). A Role assignment naming a Role that is no longer defined
grants nothing.

The built-in `admin` Service User owns the tokens that `wgl token` prints. Tokens
from the pre-Users `tokens` table are migrated to it on first open, so existing
scripts keep working.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from woodglue.access import ADMIN_ROLE, Caller, Grant

logger = logging.getLogger(__name__)

ADMIN_USER_ID = "admin"
_SCHEMA_VERSION = 1


class UserKind(StrEnum):
    person = "person"
    service = "service"


class CredentialKind(StrEnum):
    token = "token"


class User(BaseModel):
    id: str
    name: str
    email: str | None = None
    kind: UserKind
    disabled: bool = False
    roles: list[str] = []
    created_at: datetime


class TokenInfo(BaseModel):
    """A Personal Access Token as listed; never includes the secret."""

    id: str
    user_id: str
    name: str
    grants: list[str] | None = None
    created_at: datetime
    expires_at: datetime | None = None


class IssuedToken(BaseModel):
    """A newly created Personal Access Token. The secret is shown only once."""

    id: str
    secret: str


class UnknownUser(LookupError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class AuthStore:
    """
    Access to `auth.db`. `roles` maps Role names to their Grants, as returned
    by `woodglue.access.resolve_roles`.
    """

    db_path: Path
    roles: dict[str, tuple[Grant, ...]]

    def __init__(self, db_path: Path, roles: dict[str, tuple[Grant, ...]]) -> None:
        self.db_path = db_path
        self.roles = roles
        with self._connect() as conn:
            self._migrate(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= _SCHEMA_VERSION:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE,
                kind TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS role_assignments (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                PRIMARY KEY (user_id, role)
            );
            CREATE TABLE IF NOT EXISTS credentials (
                id TEXT PRIMARY KEY,
                secret TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                grants TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );
            """
        )
        self._ensure_admin_user(conn)
        has_legacy = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tokens'"
        ).fetchone()
        if has_legacy:
            for row in conn.execute("SELECT token, created_at FROM tokens ORDER BY created_at"):
                conn.execute(
                    "INSERT INTO credentials (id, secret, kind, user_id, name, created_at) "
                    + "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _new_id("tok"),
                        row["token"],
                        CredentialKind.token,
                        ADMIN_USER_ID,
                        "wgl token",
                        row["created_at"],
                    ),
                )
            conn.execute("DROP TABLE tokens")
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def _ensure_admin_user(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, name, kind, created_at) VALUES (?, ?, ?, ?)",
            (ADMIN_USER_ID, ADMIN_USER_ID, UserKind.service, _now().isoformat()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO role_assignments (user_id, role) VALUES (?, ?)",
            (ADMIN_USER_ID, ADMIN_ROLE),
        )

    # Users

    def _user_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> User:
        roles = [
            r["role"]
            for r in conn.execute(
                "SELECT role FROM role_assignments WHERE user_id = ? ORDER BY role", (row["id"],)
            )
        ]
        return User(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            kind=UserKind(row["kind"]),
            disabled=bool(row["disabled"]),
            roles=roles,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at, id").fetchall()
            return [self._user_from_row(conn, r) for r in rows]

    def find_user(self, ref: str) -> User:
        """
        Look up a User by id, email, or name. A name matching several Users is
        ambiguous and raises `UnknownUser`.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE id = ? OR email = ?", (ref, ref)
            ).fetchall()
            if not rows:
                rows = conn.execute("SELECT * FROM users WHERE name = ?", (ref,)).fetchall()
            if len(rows) != 1:
                raise UnknownUser(f"No unique User matches {ref!r}")
            return self._user_from_row(conn, rows[0])

    def create_service_user(self, name: str) -> User:
        with self._connect() as conn:
            taken = conn.execute(
                "SELECT 1 FROM users WHERE name = ? OR id = ?", (name, name)
            ).fetchone()
            if taken:
                raise ValueError(f"A User named {name!r} already exists")
            user_id = _new_id("svc")
            conn.execute(
                "INSERT INTO users (id, name, kind, created_at) VALUES (?, ?, ?, ?)",
                (user_id, name, UserKind.service, _now().isoformat()),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._user_from_row(conn, row)

    def assign_role(self, user_id: str, role: str) -> None:
        if role not in self.roles:
            raise ValueError(f"Unknown Role {role!r}; defined: {', '.join(sorted(self.roles))}")
        with self._connect() as conn:
            self._require_user(conn, user_id)
            conn.execute(
                "INSERT OR IGNORE INTO role_assignments (user_id, role) VALUES (?, ?)",
                (user_id, role),
            )

    def unassign_role(self, user_id: str, role: str) -> None:
        with self._connect() as conn:
            self._require_user(conn, user_id)
            conn.execute(
                "DELETE FROM role_assignments WHERE user_id = ? AND role = ?", (user_id, role)
            )

    def set_disabled(self, user_id: str, disabled: bool) -> None:
        """Disable or re-enable a User. Disabling deletes all of its credentials."""
        if disabled and user_id == ADMIN_USER_ID:
            raise ValueError("The built-in admin User cannot be disabled")
        with self._connect() as conn:
            self._require_user(conn, user_id)
            conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
            if disabled:
                conn.execute("DELETE FROM credentials WHERE user_id = ?", (user_id,))

    def _require_user(self, conn: sqlite3.Connection, user_id: str) -> None:
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            raise UnknownUser(f"No User with id {user_id!r}")

    # Personal Access Tokens

    def create_token(
        self,
        user_id: str,
        name: str,
        *,
        expires_at: datetime | None = None,
        grants: list[str] | None = None,
    ) -> IssuedToken:
        """
        Issue a Personal Access Token. `grants`, if given, narrows the token to
        a subset of its User's Grants; they are validated but not checked
        against the User, since the effective set is computed per request.
        """
        if grants is not None:
            grants = [str(Grant.parse(g)) for g in grants]
        with self._connect() as conn:
            self._require_user(conn, user_id)
            return self._insert_token(conn, user_id, name, expires_at, grants)

    def _insert_token(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        name: str,
        expires_at: datetime | None = None,
        grants: list[str] | None = None,
    ) -> IssuedToken:
        issued = IssuedToken(id=_new_id("tok"), secret=secrets.token_urlsafe(32))
        conn.execute(
            "INSERT INTO credentials "
            + "(id, secret, kind, user_id, name, grants, created_at, expires_at) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                issued.id,
                issued.secret,
                CredentialKind.token,
                user_id,
                name,
                json.dumps(grants) if grants is not None else None,
                _now().isoformat(),
                expires_at.isoformat() if expires_at else None,
            ),
        )
        return issued

    def list_tokens(self, user_id: str | None = None) -> list[TokenInfo]:
        query = "SELECT * FROM credentials WHERE kind = ?"
        params: list[str] = [CredentialKind.token]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY created_at, id", params).fetchall()
        return [
            TokenInfo(
                id=r["id"],
                user_id=r["user_id"],
                name=r["name"],
                grants=json.loads(r["grants"]) if r["grants"] else None,
                created_at=datetime.fromisoformat(r["created_at"]),
                expires_at=_parse_time(r["expires_at"]),
            )
            for r in rows
        ]

    def token_owner(self, token_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM credentials WHERE id = ? AND kind = ?",
                (token_id, CredentialKind.token),
            ).fetchone()
        return row["user_id"] if row else None

    def revoke_token(self, token_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM credentials WHERE id = ? AND kind = ?",
                (token_id, CredentialKind.token),
            )
            return cur.rowcount > 0

    def ensure_admin_token(self) -> None:
        """Create a token for the built-in admin User if it has none."""
        with self._connect() as conn:
            if not self._admin_secrets(conn):
                self._insert_token(conn, ADMIN_USER_ID, "wgl token")

    def admin_tokens(self) -> list[str]:
        """Secrets of the admin User's tokens, oldest first."""
        with self._connect() as conn:
            return self._admin_secrets(conn)

    def _admin_secrets(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            "SELECT secret FROM credentials WHERE user_id = ? AND kind = ? "
            + "ORDER BY created_at, id",
            (ADMIN_USER_ID, CredentialKind.token),
        ).fetchall()
        return [r["secret"] for r in rows]

    def rotate_admin_token(self) -> str:
        """
        Replace all admin tokens with one new one, in one transaction, so a
        concurrent `resolve` never sees the admin without a token.
        """
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM credentials WHERE user_id = ? AND kind = ?",
                (ADMIN_USER_ID, CredentialKind.token),
            )
            return self._insert_token(conn, ADMIN_USER_ID, "wgl token").secret

    # Authentication

    def resolve(self, secret: str) -> Caller | None:
        """Return the Caller for a credential secret, or `None` if it is not valid now."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT c.id AS cid, c.grants, c.expires_at, u.* FROM credentials c "
                + "JOIN users u ON u.id = c.user_id WHERE c.secret = ?",
                (secret,),
            ).fetchone()
            if row is None or row["disabled"]:
                return None
            expires_at = _parse_time(row["expires_at"])
            if expires_at is not None and expires_at <= _now():
                return None
            user = self._user_from_row(conn, row)
        return self._caller(user, row["cid"], row["grants"])

    def _caller(self, user: User, credential_id: str, token_grants: str | None) -> Caller:
        user_grants: list[Grant] = []
        for role in user.roles:
            grants = self.roles.get(role)
            if grants is None:
                logger.warning("User %s has undefined Role %r; it grants nothing", user.id, role)
                continue
            user_grants.extend(grants)
        grant_sets = [tuple(user_grants)]
        if token_grants is not None:
            grant_sets.append(tuple(Grant.parse(g) for g in json.loads(token_grants)))
        return Caller(
            user_id=user.id,
            grant_sets=tuple(grant_sets),
            credential_id=credential_id,
            name=user.name,
            email=user.email,
            roles=tuple(user.roles),
        )
