"""Tests for the `wgl user` actions, run in-process through `main_at`."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from woodglue.access import Action, resolve_roles
from woodglue.auth_store import AuthStore
from woodglue.cli import main_at

ROLES = {"operator": ["call reports", "call billing"]}


def _write_config(data_dir: Path) -> None:
    (data_dir / "woodglue.yaml").write_text(
        dedent("""
            auth:
              roles:
                operator: ["call reports", "call billing"]
            namespaces: {}
            """)
    )


def _wgl(data_dir: Path, capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    result = main_at.run_args(["wgl", f"--data={data_dir}", *args])
    out = capsys.readouterr().out
    assert result.success, out
    return out.splitlines()


def _store(data_dir: Path) -> AuthStore:
    return AuthStore(data_dir / "auth.db", resolve_roles(ROLES))


def test_service_user_with_role_and_narrowed_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path)
    _wgl(tmp_path, capsys, "user", "add", "ci")
    _wgl(tmp_path, capsys, "user", "assign", "ci", "operator")
    [secret] = _wgl(
        tmp_path, capsys, "user", "issue", "ci", "deploy", "--grants=call reports", "--days=30"
    )
    caller = _store(tmp_path).resolve(secret)
    assert caller is not None
    assert caller.can(Action.call, "reports")
    assert not caller.can(Action.call, "billing")

    listing = "\n".join(_wgl(tmp_path, capsys, "user", "list"))
    assert "ci" in listing and "operator" in listing and "admin" in listing
    [token_line] = _wgl(tmp_path, capsys, "user", "tokens", "ci")
    assert "deploy" in token_line and "call reports" in token_line


def test_revoke_and_disable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(tmp_path)
    _wgl(tmp_path, capsys, "user", "add", "ci")
    [first] = _wgl(tmp_path, capsys, "user", "issue", "ci", "a")
    [second] = _wgl(tmp_path, capsys, "user", "issue", "ci", "b")
    store = _store(tmp_path)
    [a_info, _] = store.list_tokens(store.find_user("ci").id)
    _wgl(tmp_path, capsys, "user", "revoke", a_info.id)
    assert store.resolve(first) is None
    assert store.resolve(second) is not None
    _wgl(tmp_path, capsys, "user", "disable", "ci")
    assert store.resolve(second) is None


def test_delete_removes_the_user_and_revokes_its_tokens_without_prompting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path)
    _wgl(tmp_path, capsys, "user", "add", "ci")
    [secret] = _wgl(tmp_path, capsys, "user", "issue", "ci", "a")
    _wgl(tmp_path, capsys, "user", "delete", "ci")
    store = _store(tmp_path)
    assert store.resolve(secret) is None
    assert "ci" not in {u.name for u in store.list_users()}


def test_delete_refuses_admin_and_unknown_users_with_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path)
    _wgl(tmp_path, capsys, "user", "list")
    for user in ("admin", "nobody"):
        with pytest.raises(SystemExit) as exc_info:
            main_at.run_args(["wgl", f"--data={tmp_path}", "user", "delete", user])
        assert user in str(exc_info.value.code)
    assert "admin" in {u.name for u in _store(tmp_path).list_users()}


def test_unknown_user_or_role_exits_with_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path)
    for args in (["assign", "nobody", "operator"], ["issue", "nobody", "t"]):
        with pytest.raises(SystemExit) as exc_info:
            main_at.run_args(["wgl", f"--data={tmp_path}", "user", *args])
        assert "nobody" in str(exc_info.value.code)
    _wgl(tmp_path, capsys, "user", "add", "ci")
    with pytest.raises(SystemExit) as exc_info:
        main_at.run_args(["wgl", f"--data={tmp_path}", "user", "assign", "ci", "wizard"])
    assert "wizard" in str(exc_info.value.code)
