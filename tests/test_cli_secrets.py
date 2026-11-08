"""
Which `wgl` commands need the Identity Provider secrets: only `wgl start`.
Run in-process through `main_at` with the referenced variables unset.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from textwrap import dedent

import pytest

from woodglue.access import resolve_roles
from woodglue.auth_store import AuthStore
from woodglue.cli import main_at


def _wgl(data_dir: Path, capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    result = main_at.run_args(["wgl", f"--data={data_dir}", *args])
    out = capsys.readouterr().out
    assert result.success, out
    return out.splitlines()


def _store(data_dir: Path) -> AuthStore:
    return AuthStore(
        data_dir / "auth.db", resolve_roles({"operator": ["call reports", "call billing"]})
    )


def _write_config_with_provider_secrets(data_dir: Path) -> None:
    (data_dir / "woodglue.yaml").write_text(
        dedent("""
            port: 5400
            auth:
              roles:
                operator: ["call reports", "call billing"]
              providers:
                - id: google
                  kind: oidc
                  issuer: https://accounts.google.com
                  client_id: ${WG_TEST_CLIENT_ID}
                  client_secret: ${WG_TEST_CLIENT_SECRET}
                  admit: {email_domains: [example.com]}
                  default_roles: [operator]
            namespaces:
              tools:
                entries:
                  - nsref: version
                    gref: "platform:python_version"
            """)
    )


def _unset_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WG_TEST_CLIENT_ID", raising=False)
    monkeypatch.delenv("WG_TEST_CLIENT_SECRET", raising=False)


def test_auth_db_commands_work_with_provider_secrets_unset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _unset_provider_secrets(monkeypatch)
    _write_config_with_provider_secrets(tmp_path)
    _wgl(tmp_path, capsys, "user", "add", "ci")
    _wgl(tmp_path, capsys, "user", "assign", "ci", "operator")
    listing = "\n".join(_wgl(tmp_path, capsys, "user", "list"))
    assert "ci" in listing and "operator" in listing
    [admin_token] = _wgl(tmp_path, capsys, "token")
    assert _store(tmp_path).resolve(admin_token) is not None


def test_run_works_with_provider_secrets_unset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _unset_provider_secrets(monkeypatch)
    _write_config_with_provider_secrets(tmp_path)
    assert _wgl(tmp_path, capsys, "run", "version") == [json.dumps(platform.python_version())]


def test_caddy_config_works_with_provider_secrets_unset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _unset_provider_secrets(monkeypatch)
    _write_config_with_provider_secrets(tmp_path)
    caddyfile = "\n".join(_wgl(tmp_path, capsys, "caddy", "config", "wg.example.com"))
    assert "wg.example.com" in caddyfile
    assert "127.0.0.1:5400" in caddyfile


def test_start_fails_naming_the_unset_provider_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WG_TEST_CLIENT_ID", raising=False)
    monkeypatch.setenv("WG_TEST_CLIENT_SECRET", "s3cret")
    _write_config_with_provider_secrets(tmp_path)
    result = main_at.run_args(["wgl", f"--data={tmp_path}", "start"])
    captured = capsys.readouterr()
    assert not result.success
    assert "WG_TEST_CLIENT_ID is not set" in captured.out + captured.err
    assert not (tmp_path / "auth.db").exists()
