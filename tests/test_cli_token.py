"""Tests for the `wgl token` action, run in-process through `main_at`."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from woodglue.cli import main_at
from woodglue.token_store import ensure_token, list_tokens, validate_token


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
    assert validate_token(tmp_path / "auth.db", lines[0])
    assert _wgl_token(tmp_path, capsys) == lines


def test_token_prints_each_token_on_its_own_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, auth_enabled=True)
    db_path = tmp_path / "auth.db"
    first = ensure_token(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO tokens (token, created_at) VALUES (?, ?)",
            ("second-token", "9999-01-01T00:00:00+00:00"),
        )
        conn.commit()
    assert _wgl_token(tmp_path, capsys) == [first, "second-token"]


def test_token_new_replaces_all_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_config(tmp_path, auth_enabled=True)
    db_path = tmp_path / "auth.db"
    [old] = _wgl_token(tmp_path, capsys)
    [new] = _wgl_token(tmp_path, capsys, "--new")
    assert new != old
    assert not validate_token(db_path, old)
    assert validate_token(db_path, new)
    assert list_tokens(db_path) == [new]


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
