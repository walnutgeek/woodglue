"""
Process-backed UIs: woodglue supervises a real child process and proxies HTTP
and websockets to it, injecting the viewing User's identity.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
import tornado.httpclient
import tornado.httpserver
import tornado.testing
import tornado.web
import tornado.websocket
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.apps.system_api import build_system_namespace
from woodglue.apps.ui_process import UiProcess, UiProcesses, UiState
from woodglue.auth_store import AuthStore
from woodglue.config import NamespaceEntry, WoodglueConfig

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process handling")

UPSTREAM_APP = str(Path(__file__).parent / "ui_upstream_app.py")
ROLES = {"echo-viewer": ["view ui:echo", "view ui:external"]}


class _ExternalInfo(tornado.web.RequestHandler):
    @override
    def get(self) -> None:
        self.write({"path": self.request.uri, "user": self.request.headers.get("X-Woodglue-User")})


class ProcessUiTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    tokens: dict[str, str]  # pyright: ignore[reportUninitializedInstanceVariable]
    processes: UiProcesses  # pyright: ignore[reportUninitializedInstanceVariable]
    _external: tornado.httpserver.HTTPServer  # pyright: ignore[reportUninitializedInstanceVariable]
    _external_port: int  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self._tmp.name) / "auth.db", resolve_roles(ROLES))
        self.tokens = {}
        for role in ("echo-viewer", None):
            user = self.store.create_service_user(role or "nobody")
            if role:
                self.store.assign_role(user.id, role)
            self.tokens[role or "nobody"] = self.store.create_token(user.id, "t").secret
        sock, self._external_port = tornado.testing.bind_unused_port()
        super().setUp()
        self._external = tornado.httpserver.HTTPServer(
            tornado.web.Application([(r"/ui/external/info", _ExternalInfo)])
        )
        self._external.add_sockets([sock])
        self.io_loop.run_sync(self.processes.start_all, timeout=30)
        self.io_loop.run_sync(self.process.wait_ready, timeout=30)

    @override
    def tearDown(self) -> None:
        self.io_loop.run_sync(self.processes.stop_all, timeout=30)
        self._external.stop()
        super().tearDown()
        self._tmp.cleanup()

    @property
    def process(self) -> UiProcess:
        return self.processes.get("echo")

    @override
    def get_app(self) -> tornado.web.Application:
        config = WoodglueConfig.model_validate(
            {
                "namespaces": {},
                "auth": {"roles": ROLES},
                "storage": {"auth_db": str(self.store.db_path)},
                "uis": {
                    "echo": {
                        "command": [sys.executable, UPSTREAM_APP, "{port}", "{base_path}"],
                        "ready_path": "/ui/echo/_health",
                        "env": {"EXTRA": "1"},
                    },
                    "external": {"upstream": f"http://127.0.0.1:{self._external_port}"},
                },
            }
        )
        woodglue_url = self.get_url("")
        self.processes = UiProcesses(config.uis, woodglue_url)
        namespaces: dict[str, Any] = {}
        namespaces["system"] = (
            build_system_namespace(
                namespaces, None, self.store, uis=config.uis, ui_processes=self.processes
            ),
            NamespaceEntry(gref="builtin:system"),
        )
        return create_app(
            namespaces=namespaces,
            config=config,
            auth_store=self.store,
            ui_processes=self.processes,
        )

    def session(self, who: str) -> str:
        resp = self.fetch(
            "/auth/login",
            method="POST",
            body=urlencode({"token": self.tokens[who]}),
            follow_redirects=False,
            raise_error=False,
        )
        for header in resp.headers.get_list("Set-Cookie"):
            morsel = SimpleCookie(header).get("wgl_session")
            if morsel is not None:
                return morsel.value
        raise AssertionError("no session cookie")

    def get(
        self, path: str, headers: dict[str, str] | None = None
    ) -> tornado.httpclient.HTTPResponse:
        return self.fetch(path, headers=headers or {}, follow_redirects=False, raise_error=False)

    def info(self, session: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
        headers = {"Cookie": f"wgl_session={session}; theme=dark"}
        headers.update(extra or {})
        resp = self.get("/ui/echo/info?x=1", headers)
        assert resp.code == 200, resp.body
        return json.loads(resp.body)


class TestProxyHttp(ProcessUiTestCase):
    def test_request_reaches_the_process_with_identity(self) -> None:
        got = self.info(self.session("echo-viewer"))
        assert got["path"] == "/ui/echo/info?x=1"
        user = json.loads(got["headers"]["X-Woodglue-User"])
        assert user["name"] == "echo-viewer" and user["roles"] == ["echo-viewer"]
        assert got["headers"]["X-Woodglue-Token"]
        assert got["env_woodglue_url"] == self.get_url("")

    def test_session_cookie_is_stripped_but_app_cookies_kept(self) -> None:
        got = self.info(self.session("echo-viewer"))
        assert got["cookies"] == {"theme": "dark"}

    def test_client_cannot_spoof_identity_headers(self) -> None:
        got = self.info(
            self.session("echo-viewer"),
            {"X-Woodglue-User": '{"name": "root"}', "X-Woodglue-Token": "x"},
        )
        assert json.loads(got["headers"]["X-Woodglue-User"])["name"] == "echo-viewer"
        assert got["headers"]["X-Woodglue-Token"] != "x"

    def test_delegated_token_calls_the_api_as_the_viewer(self) -> None:
        token = self.info(self.session("echo-viewer"))["headers"]["X-Woodglue-Token"]
        body = json.dumps({"jsonrpc": "2.0", "method": "system.whoami", "id": 1})
        resp = self.fetch(
            "/rpc", method="POST", body=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert json.loads(resp.body)["result"]["name"] == "echo-viewer"

    def test_delegated_token_cannot_mint_lasting_credentials(self) -> None:
        token = self.info(self.session("echo-viewer"))["headers"]["X-Woodglue-Token"]
        body = json.dumps(
            {"jsonrpc": "2.0", "method": "system.create_token", "params": {"name": "x"}, "id": 1}
        )
        resp = self.fetch(
            "/rpc", method="POST", body=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert json.loads(resp.body)["error"]["code"] == -32602
        resp = self.fetch(
            "/auth/login",
            method="POST",
            body=urlencode({"token": token}),
            follow_redirects=False,
            raise_error=False,
        )
        assert resp.code == 401
        cli = urlencode(
            {"redirect_uri": "http://127.0.0.1:9/cb", "state": "s", "code_challenge": "c"}
        )
        resp = self.fetch(
            f"/auth/cli?{cli}",
            method="POST",
            body=cli,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
            raise_error=False,
        )
        assert resp.code == 403

    def test_access_is_gated_by_view_grant(self) -> None:
        resp = self.get("/ui/echo/info")
        assert resp.code == 302 and resp.headers["Location"].startswith("/auth/login?next=")
        resp = self.get("/ui/echo/info", {"Cookie": f"wgl_session={self.session('nobody')}"})
        assert resp.code == 403

    def test_upstream_ui_is_proxied(self) -> None:
        resp = self.get(
            "/ui/external/info", {"Cookie": f"wgl_session={self.session('echo-viewer')}"}
        )
        got = json.loads(resp.body)
        assert got["path"] == "/ui/external/info"
        assert json.loads(got["user"])["name"] == "echo-viewer"

    def test_list_uis_reports_process_state(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "method": "system.list_uis", "id": 1})
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=body,
            headers={"Authorization": f"Bearer {self.tokens['echo-viewer']}"},
        )
        states = {u["name"]: u["state"] for u in json.loads(resp.body)["result"]}
        assert states == {"echo": "ready", "external": None}

    def test_crashed_process_is_restarted(self) -> None:
        session = self.session("echo-viewer")
        first_pid = self.process.pid
        self.get("/ui/echo/crash", {"Cookie": f"wgl_session={session}"})
        self.io_loop.run_sync(self.process.wait_restarted, timeout=30)
        assert self.process.state == UiState.ready
        assert self.process.pid != first_pid
        assert self.info(session)["path"] == "/ui/echo/info?x=1"


class TestProxyStreaming(ProcessUiTestCase):
    @tornado.testing.gen_test(timeout=20)
    async def test_response_chunks_pass_through_as_they_arrive(self) -> None:
        session = self.store.create_session(self.store.find_user("echo-viewer").id)
        cookie = {"Cookie": f"wgl_session={session}"}
        chunks: list[bytes] = []
        first = asyncio.Event()

        def on_chunk(chunk: bytes) -> None:
            chunks.append(chunk)
            first.set()

        streaming = self.http_client.fetch(
            self.get_url("/ui/echo/stream"), headers=cookie, streaming_callback=on_chunk
        )
        # A buffering proxy would hold `first` until the upstream finishes, which
        # needs the release below: this wait would then time out.
        await asyncio.wait_for(first.wait(), 10)
        assert b"".join(chunks) == b"first\n"
        await self.http_client.fetch(self.get_url("/ui/echo/release"), headers=cookie)
        resp = await streaming
        assert resp.code == 200
        assert b"".join(chunks) == b"first\nsecond\n"


class TestProxyWebsocket(ProcessUiTestCase):
    def direct_session(self, who: str) -> str:
        return self.store.create_session(self.store.find_user(who).id)

    async def _connect(self, session: str) -> tornado.websocket.WebSocketClientConnection:
        url = self.get_url("/ui/echo/ws").replace("http://", "ws://")
        request = tornado.httpclient.HTTPRequest(
            url,
            headers={
                "Cookie": f"wgl_session={session}; theme=dark",
                "Origin": self.get_url("").rstrip("/"),
            },
        )
        return await tornado.websocket.websocket_connect(
            request, subprotocols=["streamlit", "xsrf"]
        )

    @tornado.testing.gen_test(timeout=20)
    async def test_messages_flow_both_ways_with_identity(self) -> None:
        conn = await self._connect(self.direct_session("echo-viewer"))
        assert conn.selected_subprotocol == "streamlit"
        hello = json.loads(await conn.read_message() or "")
        assert json.loads(hello["headers"]["X-Woodglue-User"])["name"] == "echo-viewer"
        assert "wgl_session" not in hello["headers"].get("Cookie", "")
        await conn.write_message("ping")
        assert await conn.read_message() == "ping"
        conn.close()

    @tornado.testing.gen_test(timeout=20)
    async def test_logout_closes_the_websocket(self) -> None:
        session = self.direct_session("echo-viewer")
        conn = await self._connect(session)
        await conn.read_message()
        await self.http_client.fetch(
            self.get_url("/auth/logout"),
            method="POST",
            body="",
            headers={"Cookie": f"wgl_session={session}"},
            follow_redirects=False,
            raise_error=False,
        )
        assert await conn.read_message() is None
