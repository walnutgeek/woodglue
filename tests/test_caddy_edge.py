"""The optional Caddy edge: `/auth/verify` for `forward_auth`, and `wgl caddy config`."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest
import tornado.httpclient
import tornado.testing
import tornado.web
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.auth_store import AuthStore
from woodglue.cli import main_at
from woodglue.config import WoodglueConfig

ROLES = {"reports-viewer": ["view ui:reports"]}


class TestVerify(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    sessions: dict[str, str]  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self._tmp.name) / "auth.db", resolve_roles(ROLES))
        self.sessions = {}
        for role in ("reports-viewer", None):
            user = self.store.create_service_user(role or "nobody")
            if role:
                self.store.assign_role(user.id, role)
            self.sessions[role or "nobody"] = self.store.create_session(user.id)
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        tmp = Path(self._tmp.name)
        config = WoodglueConfig.model_validate(
            {
                "namespaces": {},
                "auth": {"roles": ROLES},
                "storage": {"auth_db": str(tmp / "auth.db")},
                "uis": {
                    "reports": {"path": str(tmp)},
                    "status": {"path": str(tmp), "public": True},
                },
            }
        )
        return create_app(namespaces={}, config=config, auth_store=self.store)

    def verify(
        self, uri: str, who: str | None = None, query: str = ""
    ) -> tornado.httpclient.HTTPResponse:
        headers = {"X-Forwarded-Uri": uri, "X-Forwarded-Method": "GET"}
        if who:
            headers["Cookie"] = f"wgl_session={self.sessions[who]}"
        return self.fetch(
            f"/auth/verify{query}", headers=headers, follow_redirects=False, raise_error=False
        )

    def test_unauthenticated_redirects_to_login_for_the_original_page(self) -> None:
        resp = self.verify("/ui/reports/runs/42?x=1")
        assert resp.code == 302
        assert resp.headers["Location"] == "/auth/login?next=%2Fui%2Freports%2Fruns%2F42%3Fx%3D1"

    def test_view_grant_is_required(self) -> None:
        assert self.verify("/ui/reports/", "nobody").code == 403
        resp = self.verify("/ui/reports/app.js", "reports-viewer")
        assert resp.code == 200
        assert json.loads(resp.headers["X-Woodglue-User"])["name"] == "reports-viewer"
        assert resp.headers["X-Woodglue-Token"]

    def test_ui_can_be_named_explicitly(self) -> None:
        assert self.verify("/anything", "reports-viewer", "?ui=reports").code == 200
        assert self.verify("/anything", "reports-viewer", "?ui=grafana").code == 403

    def test_public_ui_passes_without_session(self) -> None:
        assert self.verify("/ui/status/index.html").code == 200

    def test_path_outside_any_ui_is_rejected(self) -> None:
        assert self.verify("/elsewhere", "reports-viewer").code == 400


def _wgl(data_dir: Path, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    result = main_at.run_args(["wgl", f"--data={data_dir}", *args])
    out = capsys.readouterr().out
    assert result.success, out
    return out


def test_caddy_config_routes_static_uis_through_caddy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "woodglue.yaml").write_text(
        dedent("""
            port: 5400
            namespaces: {}
            uis:
              reports:
                path: frontend/dist
              plain:
                path: plain
                spa_fallback: false
              dash:
                command: [srv, "{port}"]
            """)
    )
    out = _wgl(tmp_path, capsys, "caddy", "config", "wg.example.com")
    assert out.startswith("wg.example.com {")
    assert "request_header -X-Woodglue-User" in out
    reports = out[out.index("handle /ui/reports/*") :]
    reports = reports[: reports.index("\n\t}\n")]
    assert "forward_auth 127.0.0.1:5400" in reports
    assert "uri /auth/verify?ui=reports" in reports
    assert reports.index("forward_auth") < reports.index("uri strip_prefix /ui/reports")
    assert f"root * {tmp_path / 'frontend/dist'}" in reports
    assert "rewrite @client_route /index.html" in reports
    plain = out[out.index("handle /ui/plain/*") :]
    assert "client_route" not in plain[: plain.index("\n\t}\n")]
    # Process-backed UIs keep going through woodglue, which proxies them.
    assert "/ui/dash" not in out
    assert out.rstrip().endswith("reverse_proxy 127.0.0.1:5400\n\t}\n}".rstrip())
