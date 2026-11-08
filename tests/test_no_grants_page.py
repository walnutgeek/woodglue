"""
The page a signed-in User with no Grants sees instead of a bare 403, for the
Console and a Static UI (Process-backed UIs are covered in `test_process_uis.py`).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import tornado.httpclient
import tornado.testing
import tornado.web
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.auth_store import AuthStore, UserStatus
from woodglue.config import WoodglueConfig

ROLES = {"caller": ["call reports"]}


class NoGrantsTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        (tmp / "reports").mkdir()
        (tmp / "reports" / "index.html").write_text("<!doctype html><title>reports</title>")
        self.store = AuthStore(tmp / "auth.db", resolve_roles(ROLES))
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
                "uis": {"reports": {"path": str(tmp / "reports")}},
            }
        )
        return create_app(namespaces={}, config=config, auth_store=self.store)

    @property
    def origin(self) -> str:
        return self.get_url("").rstrip("/")

    def person_session(
        self,
        name: str,
        email: str,
        assigned_roles: list[str] | None = None,
        *,
        admit_as: UserStatus = UserStatus.active,
        provider_roles: list[str] | None = None,
    ) -> str:
        user = self.store.sign_in_person(
            "google",
            f"sub-{name}",
            email=email,
            email_verified=True,
            name=name,
            role_sources={"google": provider_roles or []},
            admit_as=admit_as,
        )
        for role in assigned_roles or []:
            self.store.assign_role(user.id, role)
        return f"wgl_session={self.store.create_session(user.id)}"

    def get(self, path: str, cookie: str) -> tornado.httpclient.HTTPResponse:
        return self.fetch(
            path, headers={"Cookie": cookie}, follow_redirects=False, raise_error=False
        )


class TestNoGrantsPage(NoGrantsTestCase):
    def test_user_without_grants_gets_the_page_for_the_console_and_a_static_ui(self) -> None:
        cookie = self.person_session("Ada", "ada@example.com")
        for path in ("/console/", "/ui/reports/"):
            resp = self.get(path, cookie)
            assert resp.code == 403, path
            assert resp.headers["Content-Type"].startswith("text/html"), path
            body = resp.body.decode()
            assert "no access yet" in body and "ask an admin" in body, path
            assert "Ada" in body and "ada@example.com" in body, path
            assert 'action="/auth/logout"' in body, path

    def test_pending_user_gets_the_awaiting_approval_page(self) -> None:
        # Roles the provider syncs to a pending User grant nothing until approval.
        cookie = self.person_session(
            "Pat", "pat@example.com", admit_as=UserStatus.pending, provider_roles=["caller"]
        )
        for path in ("/console/", "/ui/reports/"):
            resp = self.get(path, cookie)
            assert resp.code == 403, path
            body = resp.body.decode()
            assert "Awaiting approval" in body and "no access yet" not in body, path
            assert "Pat" in body and 'action="/auth/logout"' in body, path

    def test_user_with_other_grants_gets_the_bare_403(self) -> None:
        cookie = self.person_session("Bob", "bob@example.com", ["caller"])
        for name, path in (("console", "/console/"), ("reports", "/ui/reports/")):
            resp = self.get(path, cookie)
            assert resp.code == 403, path
            assert resp.body == f"Forbidden: requires 'view' on 'ui:{name}'".encode()

    def test_bearer_token_without_grants_gets_the_bare_403(self) -> None:
        user = self.store.create_service_user("ci")
        token = self.store.create_token(user.id, "t").secret
        resp = self.fetch(
            "/console/", headers={"Authorization": f"Bearer {token}"}, raise_error=False
        )
        assert resp.code == 403
        assert resp.body == b"Forbidden: requires 'view' on 'ui:console'"

    def test_sign_out_on_the_page_ends_the_session(self) -> None:
        cookie = self.person_session("Ada", "ada@example.com")
        assert self.get("/console/", cookie).code == 403
        resp = self.fetch(
            "/auth/logout",
            method="POST",
            body="",
            headers={"Cookie": cookie, "Origin": self.origin},
            follow_redirects=False,
            raise_error=False,
        )
        assert resp.code == 302 and resp.headers["Location"] == "/auth/login"
        resp = self.get("/console/", cookie)
        assert resp.code == 302
        assert resp.headers["Location"].startswith("/auth/login")

    def test_page_escapes_name_and_email(self) -> None:
        cookie = self.person_session("<b>Eve</b>", '"eve"<x>@example.com')
        body = self.get("/console/", cookie).body.decode()
        assert "<b>Eve</b>" not in body and "&lt;b&gt;Eve&lt;/b&gt;" in body
        assert "<x>" not in body and "&quot;eve&quot;&lt;x&gt;@example.com" in body
