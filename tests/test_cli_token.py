"""Tests for the `wgl token` action, run in-process through `main_at`."""

from __future__ import annotations

from pathlib import Path

import pytest

from woodglue.access import resolve_roles
from woodglue.auth_store import ADMIN_USER_ID, AuthStore
from woodglue.cli import main_at


def _store(data_dir: Path) -> AuthStore:
    return AuthStore(data_dir / "auth.db", resolve_roles({}))


def _write_config(data_dir: Path, auth_enabled: bool) -> None:
    (data_dir / "woodglue.yaml").write_text(
        f"auth:\n  enabled: {str(auth_enabled).lower()}\nnamespaces: {{}}\n"
    )


def _wgl_token(data_dir: Path, capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    """Run `wgl token` and return its stdout lines."""
    result = main_at.run_args(["wgl", f"--data={data_dir}", "token", *args])
    out = capsys.readouterr().out
    assert result.success, out
    return out.splitlines()


def test_token_creates_and_prints_token_before_first_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, auth_enabled=True)
    assert not (tmp_path / "auth.db").exists()
    lines = _wgl_token(tmp_path, capsys)
    assert len(lines) == 1
    assert _store(tmp_path).resolve(lines[0]) is not None
    assert _wgl_token(tmp_path, capsys) == lines


def test_token_prints_each_token_on_its_own_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, auth_enabled=True)
    store = _store(tmp_path)
    store.ensure_admin_token()
    [first] = store.admin_tokens()
    second = store.create_token(ADMIN_USER_ID, "another").secret
    assert _wgl_token(tmp_path, capsys) == [first, second]


def test_token_new_replaces_all_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(tmp_path, auth_enabled=True)
    [old] = _wgl_token(tmp_path, capsys)
    [new] = _wgl_token(tmp_path, capsys, "--new")
    assert new != old
    store = _store(tmp_path)
    assert store.resolve(old) is None
    assert store.resolve(new) is not None
    assert store.admin_tokens() == [new]


def test_token_with_auth_disabled_exits_nonzero_without_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, auth_enabled=False)
    for args in ([], ["--new"]):
        with pytest.raises(SystemExit) as exc_info:
            main_at.run_args(["wgl", f"--data={tmp_path}", "token", *args])
        assert exc_info.value.code not in (0, None)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "disabled" in str(exc_info.value.code)
    assert not (tmp_path / "auth.db").exists()
