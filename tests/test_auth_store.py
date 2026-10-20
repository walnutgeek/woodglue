"""Tests for `woodglue.auth_store.AuthStore` against a temporary `auth.db`."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from woodglue.access import Action, resolve_roles
from woodglue.auth_store import AuthStore, UnknownUser

ROLES = resolve_roles(
    {"operator": ["call reports", "call billing", "read *"], "viewer": ["read *"]}
)


def _store(tmp_path: Path) -> AuthStore:
    return AuthStore(tmp_path / "auth.db", ROLES)


def test_legacy_tokens_become_admin_tokens(tmp_path: Path) -> None:
    db = tmp_path / "auth.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE tokens (token TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO tokens VALUES ('old-secret', '2026-01-01T00:00:00+00:00')")
        conn.commit()
    store = _store(tmp_path)
    caller = store.resolve("old-secret")
    assert caller is not None
    assert caller.user_id == "admin"
    assert caller.can(Action.admin) and caller.can(Action.trigger, "anything")
    assert store.admin_tokens() == ["old-secret"]


def test_ensure_and_rotate_admin_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_admin_token()
    store.ensure_admin_token()
    [first] = store.admin_tokens()
    second = store.rotate_admin_token()
    assert store.admin_tokens() == [second]
    assert store.resolve(first) is None
    assert store.resolve(second) is not None


def test_token_grants_follow_role_assignments(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    issued = store.create_token(user.id, "deploy")
    assert store.resolve(issued.secret) is not None
    caller = store.resolve(issued.secret)
    assert caller is not None and not caller.can(Action.call, "reports")

    store.assign_role(user.id, "operator")
    caller = store.resolve(issued.secret)
    assert caller is not None and caller.can(Action.call, "billing")
    assert caller.roles == ("operator",)


def test_narrowed_token_never_exceeds_user_grants(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    store.assign_role(user.id, "operator")
    issued = store.create_token(user.id, "reports-only", grants=["call reports", "trigger *"])
    caller = store.resolve(issued.secret)
    assert caller is not None
    assert caller.can(Action.call, "reports")
    assert not caller.can(Action.call, "billing")
    # The token names `trigger`, but the User has no such Grant.
    assert not caller.can(Action.trigger, "reports")

    store.unassign_role(user.id, "operator")
    caller = store.resolve(issued.secret)
    assert caller is not None and not caller.can(Action.call, "reports")


def test_expired_token_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    issued = store.create_token(user.id, "old", expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert store.resolve(issued.secret) is None


def test_disabling_a_user_revokes_its_tokens(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    issued = store.create_token(user.id, "t")
    store.set_disabled(user.id, True)
    assert store.resolve(issued.secret) is None
    store.set_disabled(user.id, False)
    assert store.resolve(issued.secret) is None
    assert store.list_tokens(user.id) == []


def test_revoke_and_list_tokens(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    a = store.create_token(user.id, "a")
    b = store.create_token(user.id, "b", grants=["call reports"])
    assert [(t.name, t.grants) for t in store.list_tokens(user.id)] == [
        ("a", None),
        ("b", ["call reports"]),
    ]
    store.revoke_token(a.id)
    assert store.resolve(a.secret) is None
    assert store.resolve(b.secret) is not None


def test_users_are_found_by_id_name_or_email(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    assert store.find_user("ci").id == user.id
    assert store.find_user(user.id).name == "ci"
    with pytest.raises(UnknownUser):
        store.find_user("nobody")
    with pytest.raises(ValueError):
        store.assign_role(user.id, "no-such-role")
    with pytest.raises(ValueError):
        store.create_service_user("ci")


class _Clock:
    now: datetime

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def test_session_expires_after_idle_timeout(tmp_path: Path) -> None:
    clock = _Clock()
    store = AuthStore(tmp_path / "auth.db", ROLES, clock=clock)
    user = store.create_service_user("ci")
    secret = store.create_session(user.id)
    clock.advance(hours=11)
    assert store.resolve(secret) is not None
    # Activity slides the idle window.
    clock.advance(hours=11)
    assert store.resolve(secret) is not None
    clock.advance(hours=12, seconds=1)
    assert store.resolve(secret) is None


def test_session_expires_at_absolute_limit_despite_activity(tmp_path: Path) -> None:
    clock = _Clock()
    store = AuthStore(tmp_path / "auth.db", ROLES, clock=clock)
    user = store.create_service_user("ci")
    secret = store.create_session(user.id)
    # 15 * 11h = 165h, under the 7-day (168h) limit; one more step crosses it.
    for _ in range(15):
        clock.advance(hours=11)
        assert store.resolve(secret) is not None
    clock.advance(hours=11)
    assert store.resolve(secret) is None


def test_ended_or_disabled_sessions_stop_resolving(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    a, b = store.create_session(user.id), store.create_session(user.id)
    store.end_session(a)
    assert store.resolve(a) is None
    assert store.resolve(b) is not None
    store.set_disabled(user.id, True)
    assert store.resolve(b) is None


def test_sessions_are_not_listed_as_tokens(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    store.create_session(user.id)
    assert store.list_tokens(user.id) == []
