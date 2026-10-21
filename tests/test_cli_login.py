"""
`wgl login` against a live server: the `/auth/cli` endpoints, and the CLI's
loopback flow driven by a fake browser that carries a Session cookie.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import tornado.httpclient
import tornado.testing
import tornado.web
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.apps.system_api import build_system_namespace
from woodglue.auth_store import AuthStore
from woodglue.cli_login import load_credentials, login, logout
from woodglue.client import WoodglueClient
from woodglue.config import NamespaceEntry, WoodglueConfig


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class CliLoginTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    session: str  # pyright: ignore[reportUninitializedInstanceVariable]
    user_id: str  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self._tmp.name) / "auth.db", resolve_roles({}))
        user = self.store.create_service_user("dev")
        self.store.assign_role(user.id, "admin")
        self.user_id = user.id
        self.session = self.store.create_session(user.id)
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        config = WoodglueConfig.model_validate(
            {"namespaces": {}, "storage": {"auth_db": str(self.store.db_path)}}
        )
        namespaces: dict[str, Any] = {}
        namespaces["system"] = (
            build_system_namespace(namespaces, None, self.store),
            NamespaceEntry(gref="builtin:system"),
        )
        return create_app(namespaces=namespaces, config=config, auth_store=self.store)

    def authorize(
        self, method: str = "GET", cookie: bool = True, **params: str
    ) -> tornado.httpclient.HTTPResponse:
        query = {
            "redirect_uri": "http://127.0.0.1:9/callback",
            "state": "s1",
            "code_challenge": _challenge("v" * 43),
            "name": "wgl on laptop",
        }
        query.update(params)
        headers = {"Cookie": f"wgl_session={self.session}"} if cookie else {}
        if method == "POST":
            return self.fetch(
                "/auth/cli",
                method="POST",
                body=urlencode(query),
                headers=headers,
                follow_redirects=False,
                raise_error=False,
            )
        return self.fetch(
            f"/auth/cli?{urlencode(query)}",
            headers=headers,
            follow_redirects=False,
            raise_error=False,
        )

    def exchange(self, code: str, verifier: str) -> tornado.httpclient.HTTPResponse:
        return self.fetch(
            "/auth/cli/token",
            method="POST",
            body=json.dumps({"code": code, "code_verifier": verifier}),
            headers={"Content-Type": "application/json"},
            raise_error=False,
        )


class TestCliEndpoints(CliLoginTestCase):
    def test_without_session_goes_through_login(self) -> None:
        resp = self.authorize(cookie=False)
        assert resp.code == 302
        assert resp.headers["Location"].startswith("/auth/login?next=%2Fauth%2Fcli%3F")

    def test_confirm_page_names_the_token(self) -> None:
        resp = self.authorize()
        assert resp.code == 200
        assert b"wgl on laptop" in resp.body and b'method="post"' in resp.body

    def test_only_loopback_redirects_are_allowed(self) -> None:
        for bad in ("https://evil.example/cb", "http://10.0.0.5:8000/cb", "http://127.0.0.1/cb"):
            assert self.authorize(redirect_uri=bad).code == 400, bad

    def test_approval_issues_a_one_time_code_for_the_verifier(self) -> None:
        resp = self.authorize("POST")
        assert resp.code == 302
        location = urlsplit(resp.headers["Location"])
        assert location.netloc == "127.0.0.1:9"
        params = {k: v[0] for k, v in parse_qs(location.query).items()}
        assert params["state"] == "s1" and "token" not in params

        resp = self.exchange(params["code"], "v" * 43)
        assert resp.code == 200
        issued = json.loads(resp.body)
        caller = self.store.resolve(issued["token"])
        assert caller is not None and caller.user_id == self.user_id
        assert [t.name for t in self.store.list_tokens(self.user_id)] == ["wgl on laptop"]
        assert self.exchange(params["code"], "v" * 43).code == 400

    def test_wrong_verifier_burns_the_code(self) -> None:
        location = urlsplit(self.authorize("POST").headers["Location"])
        code = parse_qs(location.query)["code"][0]
        assert self.exchange(code, "w" * 43).code == 400
        assert self.exchange(code, "v" * 43).code == 400

    def test_cross_site_approval_is_rejected(self) -> None:
        resp = self.fetch(
            "/auth/cli",
            method="POST",
            body=urlencode(
                {
                    "redirect_uri": "http://127.0.0.1:9/callback",
                    "state": "s",
                    "code_challenge": "c",
                    "name": "n",
                }
            ),
            headers={"Cookie": f"wgl_session={self.session}", "Origin": "https://evil.example"},
            follow_redirects=False,
            raise_error=False,
        )
        assert resp.code == 403


class TestCliFlow(CliLoginTestCase):
    def fake_browser(self, url: str) -> None:
        """Opens the authorize page with a Session, approves, and follows to the CLI."""
        query = parse_qs(urlsplit(url).query)
        form = urlencode({k: v[0] for k, v in query.items()}).encode()
        request = urllib.request.Request(
            self.get_url("/auth/cli"),
            data=form,
            headers={"Cookie": f"wgl_session={self.session}"},
        )
        # urllib follows the redirect to the CLI's loopback callback.
        with urllib.request.urlopen(request, timeout=10) as resp:
            assert resp.status == 200

    @tornado.testing.gen_test(timeout=30)
    async def test_login_stores_a_token_the_client_uses_and_logout_revokes_it(self) -> None:
        server = self.get_url("").rstrip("/")
        env = {"WOODGLUE_CONFIG_DIR": self._tmp.name}
        loop = asyncio.get_running_loop()
        with pytest.MonkeyPatch.context() as mp:
            for key, value in env.items():
                mp.setenv(key, value)
            stored = await loop.run_in_executor(
                None, lambda: login(server, "wgl test", open_browser=self.fake_browser)
            )
            assert load_credentials()[server].token == stored.token
            if sys.platform != "win32":  # Windows has no POSIX file modes
                assert (Path(self._tmp.name) / "credentials.json").stat().st_mode & 0o077 == 0

            me = await WoodglueClient(server).call("system.whoami")
            assert me["name"] == "dev"

            await loop.run_in_executor(None, lambda: logout(server))
            assert server not in load_credentials()
            assert self.store.resolve(stored.token) is None


class TestMacDebug(CliLoginTestCase):
    """Temporary: find where the loopback flow stalls on macOS CI."""

    @tornado.testing.gen_test(timeout=90)
    async def test_debug_loopback_steps(self) -> None:
        import socket
        import time

        from woodglue import cli_login

        trace: list[str] = []

        def step(name: str, fn: Any) -> Any:
            t = time.monotonic()
            try:
                out = fn()
                trace.append(f"{name}: ok {time.monotonic() - t:.2f}s {out!r}"[:200])
                return out
            except Exception as exc:
                trace.append(
                    f"{name}: {type(exc).__name__} {exc} {time.monotonic() - t:.2f}s"[:200]
                )
                return None

        def run() -> None:
            step("getproxies", urllib.request.getproxies)
            step("getfqdn", lambda: socket.getfqdn("127.0.0.1"))
            step(
                "GET tornado",
                lambda: urllib.request.urlopen(self.get_url("/auth/login"), timeout=5).status,
            )
            result = cli_login._Callback()  # pyright: ignore[reportPrivateUsage]
            listener = step("make listener", lambda: cli_login._listener(result))  # pyright: ignore[reportPrivateUsage]
            if listener is not None:
                import threading

                threading.Thread(target=listener.serve_forever, daemon=True).start()
                port = listener.server_address[1]
                step(
                    "GET listener",
                    lambda: (
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/cb?x=1", timeout=5).status
                    ),
                )
                step("shutdown", listener.shutdown)
            form = urlencode(
                {
                    "redirect_uri": "http://127.0.0.1:9/cb",
                    "state": "s",
                    "code_challenge": "c",
                    "name": "n",
                }
            ).encode()
            req = urllib.request.Request(
                self.get_url("/auth/cli"),
                data=form,
                headers={"Cookie": f"wgl_session={self.session}"},
            )
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
            step("POST cli (redirect to closed port)", lambda: opener.open(req, timeout=5).status)

        await asyncio.get_running_loop().run_in_executor(None, run)
        if sys.platform == "darwin":
            raise AssertionError("\n".join(trace))
