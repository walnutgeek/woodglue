"""Browser Sessions over HTTP: login, the Session cookie, CSRF checks, and the Console gate."""

from __future__ import annotations

import json
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import tornado.httpclient
import tornado.testing
import tornado.web
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.auth_store import AuthStore
from woodglue.config import AuthConfig, NamespaceEntry, WoodglueConfig, WoodglueStorageConfig
from woodglue.hello import hello

ROLES = {"caller": ["call reports"], "console": ["call reports", "view ui:console"]}


class SessionTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    tokens: dict[str, str]  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self._tmp.name) / "auth.db", resolve_roles(ROLES))
        self.tokens = {}
        for role in ROLES:
            user = self.store.create_service_user(role)
            self.store.assign_role(user.id, role)
            self.tokens[role] = self.store.create_token(user.id, "t").secret
            self.tokens[f"{role}-narrow"] = self.store.create_token(
                user.id, "narrow", grants=["call reports"]
            ).secret
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        ns = Namespace()
        ns.register(hello, nsref="hello", tags=["api"])
        config = WoodglueConfig(
            namespaces={},
            auth=AuthConfig(enabled=True, roles=ROLES),
            storage=WoodglueStorageConfig(auth_db=self.store.db_path),
        )
        return create_app(
            namespaces={"reports": (ns, NamespaceEntry(gref="unused"))},
            config=config,
            auth_store=self.store,
        )

    @property
    def origin(self) -> str:
        return self.get_url("").rstrip("/")

    def login(self, token: str, next_url: str = "/") -> tornado.httpclient.HTTPResponse:
        return self.fetch(
            "/auth/login",
            method="POST",
            body=urlencode({"token": token, "next": next_url}),
            headers={"Origin": self.origin},
            follow_redirects=False,
            raise_error=False,
        )

    def session_cookie(self, who: str) -> str:
        resp = self.login(self.tokens[who])
        assert resp.code == 302, resp.body
        cookie = SimpleCookie(resp.headers["Set-Cookie"])
        return f"wgl_session={cookie['wgl_session'].value}"

    def rpc(self, cookie: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        body = json.dumps(
            {"jsonrpc": "2.0", "method": "reports.hello", "params": {"name": "abc"}, "id": 1}
        )
        all_headers = {"Content-Type": "application/json", "Origin": self.origin, "Cookie": cookie}
        all_headers.update(headers or {})
        return json.loads(self.fetch("/rpc", method="POST", body=body, headers=all_headers).body)

    def get(self, path: str, cookie: str | None = None) -> tornado.httpclient.HTTPResponse:
        headers = {"Cookie": cookie} if cookie else {}
        return self.fetch(path, headers=headers, follow_redirects=False, raise_error=False)


class TestLogin(SessionTestCase):
    def test_login_sets_an_httponly_session_cookie(self) -> None:
        resp = self.login(self.tokens["caller"], "/console/#runs")
        assert resp.code == 302
        assert resp.headers["Location"] == "/console/#runs"
        cookie = resp.headers["Set-Cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie

    def test_bad_token_is_rejected(self) -> None:
        resp = self.login("not-a-token")
        assert resp.code == 401
        assert "Set-Cookie" not in resp.headers

    def test_narrowed_token_cannot_start_a_session(self) -> None:
        resp = self.login(self.tokens["caller-narrow"])
        assert resp.code == 401
        assert b"narrowed" in resp.body

    def test_next_must_be_a_local_path(self) -> None:
        for bad in ("https://evil.example/", "//evil.example/x", "/\\evil.example"):
            assert self.login(self.tokens["caller"], bad).headers["Location"] == "/"

    def test_cross_site_login_post_is_rejected(self) -> None:
        resp = self.fetch(
            "/auth/login",
            method="POST",
            body=urlencode({"token": self.tokens["caller"]}),
            headers={"Origin": "https://evil.example"},
            follow_redirects=False,
            raise_error=False,
        )
        assert resp.code == 403

    def test_login_page_renders(self) -> None:
        resp = self.get("/auth/login?next=/console/")
        assert resp.code == 200
        assert b'name="token"' in resp.body


class TestSessionRpc(SessionTestCase):
    def test_session_cookie_authenticates_rpc(self) -> None:
        assert self.rpc(self.session_cookie("caller"))["result"] == 3

    def test_cross_origin_cookie_request_is_rejected(self) -> None:
        resp = self.rpc(self.session_cookie("caller"), {"Origin": "https://evil.example"})
        assert resp["error"]["code"] == -32003

    def test_non_json_cookie_request_is_rejected(self) -> None:
        resp = self.rpc(self.session_cookie("caller"), {"Content-Type": "text/plain"})
        assert resp["error"]["code"] == -32003

    def test_bearer_requests_skip_csrf_checks(self) -> None:
        body = json.dumps(
            {"jsonrpc": "2.0", "method": "reports.hello", "params": {"name": "ab"}, "id": 1}
        )
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=body,
            headers={
                "Authorization": f"Bearer {self.tokens['caller']}",
                "Origin": "https://elsewhere.example",
                "Content-Type": "text/plain",
            },
        )
        assert json.loads(resp.body)["result"] == 2

    def test_logout_ends_the_session(self) -> None:
        cookie = self.session_cookie("caller")
        resp = self.fetch(
            "/auth/logout",
            method="POST",
            body="",
            headers={"Cookie": cookie, "Origin": self.origin},
            follow_redirects=False,
            raise_error=False,
        )
        assert resp.code == 302
        assert SimpleCookie(resp.headers["Set-Cookie"])["wgl_session"].value == ""
        assert self.rpc(cookie)["error"]["code"] == -32000


class TestConsole(SessionTestCase):
    def test_root_redirects_to_console(self) -> None:
        for path in ("/", "/console"):
            resp = self.get(path)
            assert resp.code in (301, 302), path
            assert resp.headers["Location"] == "/console/", path

    def test_unauthenticated_console_redirects_to_login(self) -> None:
        resp = self.get("/console/")
        assert resp.code == 302
        assert resp.headers["Location"] == "/auth/login?next=%2Fconsole%2F"

    def test_console_needs_view_grant(self) -> None:
        assert self.get("/console/", self.session_cookie("caller")).code == 403
        resp = self.get("/console/", self.session_cookie("console"))
        assert resp.code == 200
        assert b"<html" in resp.body.lower()


class TestBehindTlsProxy(SessionTestCase):
    @override
    def get_app(self) -> tornado.web.Application:
        app = super().get_app()
        app.settings["config"].auth.public_url = "https://wg.example.com"
        return app

    def test_cookie_is_secure_when_the_public_url_is_https(self) -> None:
        resp = self.login(self.tokens["caller"])
        assert "secure" in resp.headers["Set-Cookie"].lower()
