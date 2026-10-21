"""Static UIs at `/ui/<name>/`: access by `view` Grant, SPA fallback, and public UIs."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
import tornado.httpclient
import tornado.testing
import tornado.web
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.apps.system_api import build_system_namespace
from woodglue.auth_store import AuthStore
from woodglue.config import NamespaceEntry, WoodglueConfig

ROLES = {"reports-viewer": ["view ui:reports"]}


def _write_ui(root: Path) -> None:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>reports</title>")
    (root / "assets" / "app.js").write_text("console.log('hi')")


class StaticUiTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    tokens: dict[str, str]  # pyright: ignore[reportUninitializedInstanceVariable]
    ui_default: str = "console"

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        for name in ("reports", "status", "plain"):
            _write_ui(tmp / name)
        self.store = AuthStore(tmp / "auth.db", resolve_roles(ROLES))
        self.tokens = {}
        for role in ("reports-viewer", None):
            user = self.store.create_service_user(role or "nobody")
            if role:
                self.store.assign_role(user.id, role)
            self.tokens[role or "nobody"] = self.store.create_token(user.id, "t").secret
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
                "ui": {"default": self.ui_default},
                "uis": {
                    "reports": {"path": str(tmp / "reports")},
                    "status": {"path": str(tmp / "status"), "public": True},
                    "plain": {"path": str(tmp / "plain"), "spa_fallback": False},
                },
            }
        )
        namespaces: dict[str, Any] = {}
        namespaces["system"] = (
            build_system_namespace(namespaces, None, self.store, uis=config.uis),
            NamespaceEntry(gref="builtin:system"),
        )
        return create_app(namespaces=namespaces, config=config, auth_store=self.store)

    def get(self, path: str, who: str | None = None) -> tornado.httpclient.HTTPResponse:
        headers = {"Authorization": f"Bearer {self.tokens[who]}"} if who else {}
        return self.fetch(path, headers=headers, follow_redirects=False, raise_error=False)


class TestStaticUis(StaticUiTestCase):
    def test_ui_needs_view_grant(self) -> None:
        assert self.get("/ui/reports/", "reports-viewer").code == 200
        assert self.get("/ui/reports/", "nobody").code == 403
        resp = self.get("/ui/reports/assets/app.js")
        assert resp.code == 302
        assert resp.headers["Location"] == "/auth/login?next=%2Fui%2Freports%2Fassets%2Fapp.js"

    def test_bare_name_redirects_to_trailing_slash(self) -> None:
        resp = self.get("/ui/reports")
        assert resp.code == 301 and resp.headers["Location"] == "/ui/reports/"

    def test_spa_fallback_serves_index_for_client_routes(self) -> None:
        resp = self.get("/ui/reports/runs/42", "reports-viewer")
        assert resp.code == 200 and b"<title>reports</title>" in resp.body
        assert self.get("/ui/reports/assets/missing.js", "reports-viewer").code == 404

    def test_without_fallback_unknown_paths_are_404(self) -> None:
        self.store.assign_role(self.store.find_user("reports-viewer").id, "admin")
        assert self.get("/ui/plain/runs/42", "reports-viewer").code == 404

    def test_public_ui_needs_no_login(self) -> None:
        assert self.get("/ui/status/").code == 200

    def test_unknown_ui_is_404(self) -> None:
        assert self.get("/ui/nope/", "reports-viewer").code == 404

    def test_list_uis_shows_only_viewable_uis(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "method": "system.list_uis", "id": 1})
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=body,
            headers={"Authorization": f"Bearer {self.tokens['reports-viewer']}"},
        )
        uis = json.loads(resp.body)["result"]
        assert [(u["name"], u["path"]) for u in uis] == [
            ("reports", "/ui/reports/"),
            ("status", "/ui/status/"),
        ]


class TestDefaultUi(StaticUiTestCase):
    ui_default: str = "reports"

    def test_root_lands_on_the_default_ui(self) -> None:
        resp = self.get("/")
        assert resp.headers["Location"] == "/ui/reports/"


def test_ui_names_are_validated() -> None:
    for bad in ({"console": {"path": "x"}}, {"Bad Name": {"path": "x"}}):
        with pytest.raises(ValueError):
            WoodglueConfig.model_validate({"namespaces": {}, "uis": bad})
    with pytest.raises(ValueError):
        WoodglueConfig.model_validate({"namespaces": {}, "ui": {"default": "nope"}})
    with pytest.raises(ValueError, match="Process-backed"):
        WoodglueConfig.model_validate(
            {"namespaces": {}, "uis": {"r": {"path": "x", "grants": ["call r"]}}}
        )
    with pytest.raises(ValueError, match="Unknown action"):
        WoodglueConfig.model_validate(
            {"namespaces": {}, "uis": {"r": {"command": ["srv"], "grants": ["fly r"]}}}
        )
