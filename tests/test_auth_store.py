"""Tests for `woodglue.auth_store.AuthStore` against a temporary `auth.db`."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent

import pytest

from woodglue.access import Action, resolve_roles
from woodglue.auth_store import (
    ADMIN_USER_ID,
    AuthStore,
    UnknownUser,
    User,
    UserDisabled,
    UserPending,
    UserStatus,
)

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


# An `auth.db` at schema version 1, as written before User status existed.
_V1_SCHEMA = dedent("""
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        kind TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE role_assignments (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        source TEXT NOT NULL,
        PRIMARY KEY (user_id, role, source)
    );
    CREATE TABLE linked_identities (
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (provider, subject)
    );
    CREATE TABLE credentials (
        id TEXT PRIMARY KEY,
        secret TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        grants TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        last_used_at TEXT,
        parent_id TEXT REFERENCES credentials(id) ON DELETE CASCADE
    );
    INSERT INTO users VALUES ('admin', 'admin', NULL, 'service', 0, '2026-01-01T00:00:00+00:00');
    INSERT INTO users VALUES ('usr_ann', 'Ann', 'ann@x.com', 'person', 0, '2026-01-02T00:00:00+00:00');
    INSERT INTO users VALUES ('usr_eve', 'Eve', 'eve@x.com', 'person', 1, '2026-01-03T00:00:00+00:00');
    INSERT INTO users VALUES ('svc_ci', 'ci', NULL, 'service', 1, '2026-01-04T00:00:00+00:00');
    INSERT INTO role_assignments VALUES ('admin', 'admin', 'manual');
    INSERT INTO role_assignments VALUES ('usr_ann', 'viewer', 'provider:okta');
    INSERT INTO linked_identities VALUES ('okta', 'sub-ann', 'usr_ann', '2026-01-02T00:00:00+00:00');
    INSERT INTO credentials (id, secret, kind, user_id, name, created_at)
        VALUES ('tok_ann', 'ann-secret', 'token', 'usr_ann', 'laptop', '2026-01-02T00:00:00+00:00');
    PRAGMA user_version = 1;
    """)


def test_version_1_db_migrates_disabled_to_status(tmp_path: Path) -> None:
    with closing(sqlite3.connect(tmp_path / "auth.db")) as conn:
        conn.executescript(_V1_SCHEMA)
    store = _store(tmp_path)
    statuses = {u.id: u.status for u in store.list_users()}
    assert statuses == {
        "admin": UserStatus.active,
        "usr_ann": UserStatus.active,
        "usr_eve": UserStatus.disabled,
        "svc_ci": UserStatus.disabled,
    }
    caller = store.resolve("ann-secret")
    assert caller is not None and caller.can(Action.read, "anything")
    ann = store.sign_in_person(
        "okta", "sub-ann", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
    )
    assert ann.id == "usr_ann"
    with pytest.raises(UserDisabled):
        store.sign_in_person(
            "okta", "sub-eve", email="eve@x.com", email_verified=True, name="Eve", role_sources={}
        )
    # Reopening must not run the migration again.
    assert {u.id: u.status for u in _store(tmp_path).list_users()} == statuses


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


def test_person_sign_in_creates_then_reuses_a_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.sign_in_person(
        "okta",
        "sub-1",
        email="ann@x.com",
        email_verified=True,
        name="Ann",
        role_sources={"provider:okta": ["viewer"]},
    )
    again = store.sign_in_person(
        "okta",
        "sub-1",
        email="ann@x.com",
        email_verified=True,
        name="Ann B",
        role_sources={"provider:okta": ["viewer"]},
    )
    assert again.id == first.id
    assert again.name == "Ann B"
    assert again.roles == ["viewer"]
    assert store.find_user("ann@x.com").id == first.id


def test_provider_roles_resync_at_each_sign_in_but_manual_roles_stay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.sign_in_person(
        "okta",
        "sub-1",
        email="ann@x.com",
        email_verified=True,
        name="Ann",
        role_sources={"provider:okta": ["operator", "viewer"]},
    )
    store.assign_role(user.id, "operator")
    user = store.sign_in_person(
        "okta",
        "sub-1",
        email="ann@x.com",
        email_verified=True,
        name="Ann",
        role_sources={"provider:okta": []},
    )
    assert user.roles == ["operator"]


def test_verified_email_links_a_second_provider(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = store.sign_in_person(
        "okta", "sub-1", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
    )
    via_github = store.sign_in_person(
        "github", "42", email="ann@x.com", email_verified=True, name="ann", role_sources={}
    )
    assert via_github.id == ann.id
    impostor = store.sign_in_person(
        "other", "7", email="ann@x.com", email_verified=False, name="Ann?", role_sources={}
    )
    assert impostor.id != ann.id
    assert impostor.email is None


def test_disabled_person_cannot_sign_in(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = store.sign_in_person(
        "okta", "sub-1", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
    )
    store.set_disabled(ann.id, True)
    with pytest.raises(UserDisabled):
        store.sign_in_person(
            "okta", "sub-1", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
        )


def _rows_about(store: AuthStore, user_id: str) -> dict[str, int]:
    """Direct counts, since erasure means the rows are gone, not merely unreachable."""
    tables = {"users": "id", "role_assignments": "user_id"}
    tables |= {"linked_identities": "user_id", "credentials": "user_id"}
    with closing(sqlite3.connect(store.db_path)) as conn:
        return {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (user_id,)
            ).fetchone()[0]
            for table, column in tables.items()
        }


def test_deleting_a_user_erases_it_and_ends_its_credentials_at_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = store.sign_in_person(
        "okta", "sub-1", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
    )
    store.assign_role(ann.id, "operator")
    token = store.create_token(ann.id, "t").secret
    session = store.create_session(ann.id)
    delegated = store.delegate(session, "reports-ui")
    assert _rows_about(store, ann.id) == {
        "users": 1,
        "role_assignments": 1,
        "linked_identities": 1,
        "credentials": 3,
    }

    store.delete_user(ann.id)

    assert set(_rows_about(store, ann.id).values()) == {0}
    for secret in (token, session, delegated):
        assert store.resolve(secret) is None
    assert not store.session_is_live(session)
    with pytest.raises(UnknownUser):
        store.find_user(ann.id)
    assert store.linked_user("okta", "sub-1") is None


def test_deleting_one_user_leaves_others_alone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    gone, kept = store.create_service_user("gone"), store.create_service_user("kept")
    token = store.create_token(kept.id, "t").secret
    store.delete_user(gone.id)
    assert store.resolve(token) is not None


def test_built_in_admin_cannot_be_deleted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_admin_token()
    with pytest.raises(ValueError, match="admin"):
        store.delete_user(ADMIN_USER_ID)
    assert store.resolve(store.admin_tokens()[0]) is not None


def test_built_in_admin_keeps_the_admin_role(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_admin_token()
    store.assign_role(ADMIN_USER_ID, "viewer")
    with pytest.raises(ValueError, match="cannot lose the 'admin' Role"):
        store.unassign_role(ADMIN_USER_ID, "admin")
    store.unassign_role(ADMIN_USER_ID, "viewer")
    caller = store.resolve(store.admin_tokens()[0])
    assert caller is not None and caller.roles == ("admin",) and caller.can(Action.admin)


def test_deleting_an_unknown_user_raises(tmp_path: Path) -> None:
    with pytest.raises(UnknownUser):
        _store(tmp_path).delete_user("usr_nobody")


def test_deleted_person_signing_in_again_is_a_new_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = store.sign_in_person(
        "okta", "sub-1", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
    )
    store.assign_role(ann.id, "operator")
    store.delete_user(ann.id)
    again = store.sign_in_person(
        "okta", "sub-1", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
    )
    assert again.id != ann.id
    assert again.email == "ann@x.com"
    assert again.roles == []


def test_delegated_token_acts_as_the_session_user_and_dies_with_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    store.assign_role(user.id, "operator")
    session = store.create_session(user.id)
    delegated = store.delegate(session, "reports-ui")
    assert store.delegate(session, "reports-ui") == delegated
    caller = store.resolve(delegated)
    assert caller is not None and caller.user_id == user.id
    assert caller.can(Action.call, "billing")
    store.end_session(session)
    assert store.resolve(delegated) is None


def test_delegated_use_does_not_keep_the_session_alive(tmp_path: Path) -> None:
    clock = _Clock()
    store = AuthStore(tmp_path / "auth.db", ROLES, clock=clock)
    user = store.create_service_user("ci")
    session = store.create_session(user.id)
    delegated = store.delegate(session, "ui")
    # A UI polling the API on its own must not stand in for the person using it.
    for _ in range(2):
        clock.advance(hours=5)
        assert store.resolve(delegated) is not None
    clock.advance(hours=2, seconds=1)
    assert store.resolve(delegated) is None
    assert store.resolve(session) is None


def test_browser_requests_through_the_ui_keep_the_session_alive(tmp_path: Path) -> None:
    clock = _Clock()
    store = AuthStore(tmp_path / "auth.db", ROLES, clock=clock)
    user = store.create_service_user("ci")
    session = store.create_session(user.id)
    for _ in range(3):
        clock.advance(hours=11)
        store.delegate(session, "ui")
    assert store.resolve(session) is not None


def test_ui_grants_narrow_the_delegated_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    store.assign_role(user.id, "operator")
    session = store.create_session(user.id)
    caller = store.resolve(store.delegate(session, "reports-ui", grants=["call reports"]))
    assert caller is not None
    assert caller.can(Action.call, "reports")
    assert not caller.can(Action.call, "billing")
    assert caller.narrowed
    # A config change applies to the existing token on its next use.
    widened = store.resolve(store.delegate(session, "reports-ui", grants=None))
    assert widened is not None and widened.can(Action.call, "billing")


def test_only_sessions_can_be_delegated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    user = store.create_service_user("ci")
    token = store.create_token(user.id, "t").secret
    with pytest.raises(ValueError):
        store.delegate(token, "ui")
    with pytest.raises(ValueError):
        store.delegate("nonsense", "ui")


def _sign_in(
    store: AuthStore,
    provider: str = "okta",
    subject: str = "sub-1",
    *,
    roles: list[str] | None = None,
    admit_as: UserStatus = UserStatus.active,
    activate_pending: bool = False,
) -> User:
    return store.sign_in_person(
        provider,
        subject,
        email="ann@x.com",
        email_verified=True,
        name="Ann",
        role_sources={f"provider:{provider}": roles or []},
        admit_as=admit_as,
        activate_pending=activate_pending,
    )


def test_admit_as_pending_applies_only_when_a_sign_in_creates_the_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert _sign_in(store, subject="a").status == UserStatus.active
    store.delete_user(store.find_user("ann@x.com").id)

    ann = _sign_in(store, admit_as=UserStatus.pending)
    assert ann.status == UserStatus.pending
    # Signing in again or through a second provider keeps the status, whatever its `admit_as`.
    assert _sign_in(store, "github", "42", admit_as=UserStatus.active).status == UserStatus.pending
    store.approve_user(ann.id)
    assert _sign_in(store, admit_as=UserStatus.pending).status == UserStatus.active
    assert _sign_in(store, "gitlab", "9", admit_as=UserStatus.pending).id == ann.id
    assert store.find_user(ann.id).status == UserStatus.active


def test_each_new_pending_user_is_logged_without_email(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    with caplog.at_level(logging.INFO, logger="woodglue.auth_store"):
        ann = _sign_in(store, admit_as=UserStatus.pending)
        _sign_in(store, admit_as=UserStatus.pending)
        _sign_in(store, subject="other", admit_as=UserStatus.active)
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [f"New pending User {ann.id} via okta"]


def test_approve_makes_a_pending_user_active_with_manual_roles(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = _sign_in(store, roles=["viewer"], admit_as=UserStatus.pending)
    with pytest.raises(ValueError, match="Unknown Role"):
        store.approve_user(ann.id, ["nope"])
    assert store.find_user(ann.id).status == UserStatus.pending
    store.approve_user(ann.id, ["operator"])
    assert store.find_user(ann.id).status == UserStatus.active
    # The manual Role survives the next sign-in's resync.
    assert _sign_in(store, roles=[]).roles == ["operator"]


def test_approving_a_user_who_is_not_pending_fails(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = _sign_in(store, admit_as=UserStatus.pending)
    store.approve_user(ann.id)
    with pytest.raises(ValueError, match="is active, not pending"):
        store.approve_user(ann.id)
    store.set_disabled(ann.id, True)
    with pytest.raises(ValueError, match="is disabled, not pending"):
        store.approve_user(ann.id)
    with pytest.raises(UnknownUser):
        store.approve_user("usr_nobody")


def test_activate_pending_promotes_only_a_pending_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = _sign_in(store, admit_as=UserStatus.pending)
    assert _sign_in(store, activate_pending=True).status == UserStatus.active
    store.set_disabled(ann.id, True)
    with pytest.raises(UserDisabled):
        _sign_in(store, activate_pending=True)
    assert store.find_user(ann.id).status == UserStatus.disabled


def test_deny_is_disable_and_enable_never_yields_pending(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = _sign_in(store, admit_as=UserStatus.pending)
    with pytest.raises(ValueError, match="pending"):
        store.set_disabled(ann.id, False)
    assert store.find_user(ann.id).status == UserStatus.pending
    store.set_disabled(ann.id, True)
    assert store.find_user(ann.id).status == UserStatus.disabled
    store.set_disabled(ann.id, False)
    assert store.find_user(ann.id).status == UserStatus.active


def test_pending_user_has_no_grants_until_approved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = _sign_in(store, roles=["operator"], admit_as=UserStatus.pending)
    session = store.create_session(ann.id)
    delegated = store.delegate(session, "ui")
    for secret in (session, delegated):
        caller = store.resolve(secret)
        assert caller is not None and caller.user_id == ann.id
        assert not caller.can(Action.read, "reports")
    pending = store.resolve(session)
    assert pending is not None and not pending.may_issue_credentials

    store.approve_user(ann.id)
    for secret in (session, delegated):
        caller = store.resolve(secret)
        assert caller is not None and caller.can(Action.call, "reports")
    active = store.resolve(session)
    assert active is not None and active.may_issue_credentials


def test_pending_and_disabled_users_get_no_tokens(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ann = _sign_in(store, admit_as=UserStatus.pending)
    with pytest.raises(UserPending):
        store.create_token(ann.id, "laptop")
    ci = store.create_service_user("ci")
    store.set_disabled(ci.id, True)
    with pytest.raises(UserDisabled):
        store.create_token(ci.id, "deploy")
    # Re-enabling must not revive a token that should never have been issued.
    store.set_disabled(ci.id, False)
    assert store.list_tokens(ci.id) == []
    assert store.list_tokens(ann.id) == []
